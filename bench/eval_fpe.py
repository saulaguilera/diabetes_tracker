"""
bench/eval_fpe.py
──────────────────
Evaluación OFFLINE del componente FPE (grasa/proteína) — rama r2_fpe.
NO toca producción. Compara r1 (FPE OFF) vs r2_fpe (FPE ON) sobre la DB local.

Hace:
  1. Tunea FPE_GAIN SOLO en train (por MAE +60 en ventana post-meal 2-5h).
  2. Same-period replay (train) r1 vs r2_fpe.
  3. Held-out validation (test) r1 vs r2_fpe.
  4. Desglose por régimen (post-meal 0-2h / 2-5h, cenas, alta grasa/proteína,
     overnight, fasting, hypo windows, stable).
  5. Whiteness de innovaciones OFF vs ON.
  6. Safety (ventanas de hipo: que ON no enmascare bajas).
  7. Reporte markdown + JSON + CSV en bench/reports/fpe/.

Uso:  python3 -m bench.eval_fpe [--max 140] [--test-frac 0.25]
"""
import sys
sys.modules.setdefault("pytest", type(sys)("_stub"))

import os, json, math, argparse
from datetime import timedelta

LOW, HIGH = 70, 180
OUT_DIR = os.path.join(os.path.dirname(__file__), "reports", "fpe")


def agg(errs_sig):
    """errs_sig: lista de (error, sigma|None). Métricas."""
    errs = [e for e, _ in errs_sig]
    zs = [e / s for e, s in errs_sig if s and s > 0]
    if not errs:
        return None
    n = len(errs)
    return dict(n=n,
                mae=sum(abs(e) for e in errs) / n,
                rmse=math.sqrt(sum(e * e for e in errs) / n),
                bias=sum(errs) / n,
                w20=100 * sum(1 for e in errs if abs(e) <= 20) / n,
                stdz=(math.sqrt(sum(z * z for z in zs) / len(zs)) if zs else None))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=140)
    ap.add_argument("--test-frac", type=float, default=0.25)
    args = ap.parse_args()
    os.makedirs(OUT_DIR, exist_ok=True)

    from app import app
    with app.app_context():
        from models import GlucosePrediction, Meal
        import pmm.ssm.filter as F
        import pmm.ssm.fpe_input as FPE
        from pmm.ssm.filter import run_filter, forward_predict
        from pmm.ssm.parameters import params_or_defaults
        from pmm.core.parameter_store import get_isf_now, get_icr_now
        from pmm.engines.drift import get_drift_status
        try: from utils.kinetics import dawn_roc_mgdl_min
        except Exception: dawn_roc_mgdl_min = lambda at_time=None: 0.0

        P = params_or_defaults(None)  # = r1 (R recalibrado, ejercicio); FPE vía flag

        # ── meals con grasa/proteína (para regímenes + relevancia) ──
        meals = [(m.timestamp, m.fat_g or 0.0, m.protein_g or 0.0)
                 for m in Meal.query.filter((Meal.fat_g > 0) | (Meal.protein_g > 0)).order_by(Meal.timestamp).all()]

        def recent_meals(t, h):
            return [(ts, f, p) for ts, f, p in meals if 0 <= (t - ts).total_seconds() / 3600.0 <= h]

        def regimes(t, g):
            out = set()
            r5 = recent_meals(t, 5)
            for ts, f, p in r5:
                age = (t - ts).total_seconds() / 3600.0
                if age <= 2: out.add("post_meal_0_2h")
                else: out.add("post_meal_2_5h")
                if ts.hour >= 19 or ts.hour < 1: out.add("cena")
                if FPE.fpe_load_g(f, p) >= 12: out.add("alta_grasa_proteina")
            if not r5: out.add("fasting")
            if 0 <= t.hour < 6: out.add("overnight")
            if g is not None and g < 80: out.add("hypo_window")
            if not r5 and g is not None and 80 <= g <= HIGH: out.add("stable")
            out.add("GLOBAL")
            return out

        def fpe_relevant(t):
            return len(recent_meals(t, FPE.FPE_LOOKBACK_HOURS)) > 0

        rows = (GlucosePrediction.query
                .filter(GlucosePrediction.resolved_60 == 1, GlucosePrediction.g_real_60 != None)
                .order_by(GlucosePrediction.predicted_at).all())
        split = int(len(rows) * (1 - args.test_frac))
        train, test = rows[:split], rows[split:]
        print(f"Resueltas {len(rows)}  |  TRAIN {len(train)} ({train[0].predicted_at:%m-%d}→{train[-1].predicted_at:%m-%d})  TEST {len(test)} ({test[0].predicted_at:%m-%d}→{test[-1].predicted_at:%m-%d}) held-out")

        def fc(t):
            hora = t.hour
            isf = get_isf_now(hora=hora); icr = get_icr_now(hora=hora)
            drift = get_drift_status().get("drift_factor", 1.0); icr_m = icr.get("mu") or 12.0
            try: dawn = float(dawn_roc_mgdl_min(at_time=t) or 0.0)
            except Exception: dawn = 0.0
            res = run_filter(now=t, isf_prior=isf.get("mu"), isf_sigma=isf.get("sigma"),
                             drift_factor=drift, icr_for_meals=icr_m, params=P)
            if res.error or res.n_cgm_used < 3: return None
            return forward_predict(res, horizons_min=(30, 60), drift_factor=drift,
                                   icr_for_meals=icr_m, dawn_rate=dawn, params=P)

        # ── 1) Tuneo de FPE_GAIN en TRAIN (post-meal 2-5h, por MAE+60) ──
        train_rel = [r for r in train if fpe_relevant(r.predicted_at)
                     and "post_meal_2_5h" in regimes(r.predicted_at, r.g_actual)]
        train_rel = train_rel[::max(1, len(train_rel) // 90)]
        print(f"\nTuneo de FPE_GAIN en {len(train_rel)} ventanas post-meal 2-5h de train:")
        FPE.FPE_ENABLED = True
        maes = {}
        for gain in (0.0, 1.0, 1.5, 2.5, 4.0):
            FPE.FPE_GAIN = gain
            es = []
            for r in train_rel:
                try: pr = fc(r.predicted_at)
                except Exception: pr = None
                if pr: es.append((r.g_real_60 - pr[60].g_pred, None))
            m = agg(es); maes[gain] = m["mae"] if m else 1e9
            print(f"  GAIN={gain:<4} MAE+60(2-5h)={m['mae']:.1f} sesgo={m['bias']:+.1f}")
        opt_gain = min(maes, key=maes.get)                        # óptimo global (puede ser 0)
        best_pos = min((g for g in maes if g > 0), key=maes.get)  # mejor POSITIVO
        fpe_helps = opt_gain > 0
        print(f"  → óptimo global: GAIN={opt_gain}  ({'FPE ayuda' if fpe_helps else 'FPE NO aporta — el óptimo es apagarlo'})")
        print(f"  → brazo ON del reporte usa el mejor positivo (GAIN={best_pos}) para mostrar el efecto\n")
        best_gain = best_pos
        FPE.FPE_GAIN = best_gain

        # ── 2-4) Recolectar OFF/ON por timestamp y desglosar ──
        def collect(rs):
            data = []
            relevant = [r for r in rs if fpe_relevant(r.predicted_at)]
            relevant = relevant[::max(1, len(relevant) // args.max)]
            # baseline no-relevante (OFF=ON) para GLOBAL: muestra chica
            nonrel = [r for r in rs if not fpe_relevant(r.predicted_at)]
            nonrel = nonrel[::max(1, len(nonrel) // (args.max // 2 or 1))]
            for r in relevant:
                FPE.FPE_ENABLED = False
                off = fc(r.predicted_at)
                FPE.FPE_ENABLED = True
                on = fc(r.predicted_at)
                if not off or not on: continue
                data.append((r, regimes(r.predicted_at, r.g_actual), off, on))
            for r in nonrel:
                FPE.FPE_ENABLED = False
                off = fc(r.predicted_at)
                if not off: continue
                data.append((r, regimes(r.predicted_at, r.g_actual), off, off))  # ON=OFF
            return data

        REGS = ["GLOBAL", "post_meal_0_2h", "post_meal_2_5h", "cena", "alta_grasa_proteina",
                "overnight", "fasting", "hypo_window", "stable"]

        def breakdown(data):
            res = {}
            for reg in REGS:
                off60 = [(r.g_real_60 - off[60].g_pred, off[60].sigma) for r, rg, off, on in data if reg in rg]
                on60 = [(r.g_real_60 - on[60].g_pred, on[60].sigma) for r, rg, off, on in data if reg in rg]
                res[reg] = {"off": agg(off60), "on": agg(on60)}
            return res

        train_bd = breakdown(collect(train))
        test_bd = breakdown(collect(test))

        # ── 5) Whiteness OFF vs ON (innovaciones, una corrida larga) ──
        from models import GlucoseReading
        now = GlucoseReading.query.order_by(GlucoseReading.timestamp.desc()).first().timestamp
        def whiteness():
            FPE.FPE_GAIN = best_gain
            res = {}
            for arm, on in (("off", False), ("on", True)):
                FPE.FPE_ENABLED = on
                r = run_filter(now=now, hours=240, params=P)
                innov = [e for e in (r.innovations or []) if not e.get("rejected") and e.get("sigma_pred")]
                z = [(e["y_obs"] - e["y_pred"]) / e["sigma_pred"] for e in innov]
                if len(z) < 30: res[arm] = None; continue
                m = sum(z) / len(z); zc = [x - m for x in z]; var = sum(x * x for x in zc)
                acf1 = sum(zc[i] * zc[i + 1] for i in range(len(zc) - 1)) / var
                K = 15; n = len(z)
                acfs = [sum(zc[i] * zc[i + k] for i in range(n - k)) / var for k in range(1, K + 1)]
                lb = n * (n + 2) * sum(acfs[k - 1] ** 2 / (n - k) for k in range(1, K + 1))
                res[arm] = dict(n=n, mean=m, std=math.sqrt(var / n), acf1=acf1, ljung_box=lb)
            return res
        white = whiteness()
        FPE.FPE_ENABLED = False  # dejar el módulo apagado al salir

        # ── Reportes ──
        report = dict(model="ssm_v0_ukf6_basal_ex_r2_fpe", baseline="ssm_v0_ukf6_basal_ex_r1",
                      fpe_gain=best_gain, fp_prot=FPE.FP_PROT_GLUCOSE, fp_fat=FPE.FP_FAT_GLUCOSE,
                      fpe_k=FPE.FPE_K, train=train_bd, test=test_bd, whiteness=white)
        with open(os.path.join(OUT_DIR, "fpe_report.json"), "w") as f:
            json.dump(report, f, indent=2, default=lambda o: None)
        # CSV
        with open(os.path.join(OUT_DIR, "fpe_report.csv"), "w") as f:
            f.write("set,regime,arm,n,mae,rmse,bias,w20,stdz\n")
            for setname, bd in (("train", train_bd), ("test", test_bd)):
                for reg, d in bd.items():
                    for arm in ("off", "on"):
                        m = d[arm]
                        if m: f.write(f"{setname},{reg},{arm},{m['n']},{m['mae']:.2f},{m['rmse']:.2f},{m['bias']:.2f},{m['w20']:.1f},{m['stdz'] or ''}\n")

        def fmt_row(reg, d):
            o, n = d["off"], d["on"]
            if not o or not n: return None
            dm = o["mae"] - n["mae"]
            arrow = "✅" if dm > 0.3 else ("⚠️" if dm < -0.3 else "≈")
            return f"| {reg} | {o['n']} | {o['mae']:.1f} → {n['mae']:.1f} | {o['bias']:+.1f} → {n['bias']:+.1f} | {o['w20']:.0f}→{n['w20']:.0f}% | {arrow} |"

        lines = [f"# FPE (grasa/proteína) — r2_fpe vs r1 (OFFLINE, no desplegado)",
                 f"\n**Óptimo de FPE_GAIN en train = `{opt_gain}`** "
                 f"{'(FPE ayuda)' if fpe_helps else '→ el óptimo es APAGAR el FPE; el brazo ON usa el mejor positivo (`'+str(best_pos)+'`) solo para ilustrar la degradación'}."
                 f"  ·  prot×{FPE.FP_PROT_GLUCOSE} fat×{FPE.FP_FAT_GLUCOSE}, pico ~{1/FPE.FPE_K:.0f}min\n"]
        for title, bd in (("Same-period (TRAIN)", train_bd), ("HELD-OUT (TEST)", test_bd)):
            lines.append(f"\n## {title} — MAE/sesgo/±20 a +60 (OFF→ON)\n")
            lines.append("| régimen | n | MAE +60 | sesgo +60 | ±20 | |")
            lines.append("|---|---|---|---|---|---|")
            for reg in REGS:
                row = fmt_row(reg, bd[reg])
                if row: lines.append(row)
        if white.get("off") and white.get("on"):
            lines.append(f"\n## Whiteness (innovaciones)\n")
            lines.append(f"- OFF: std {white['off']['std']:.2f}, ACF₁ {white['off']['acf1']:.2f}, Ljung-Box {white['off']['ljung_box']:.0f}")
            lines.append(f"- ON : std {white['on']['std']:.2f}, ACF₁ {white['on']['acf1']:.2f}, Ljung-Box {white['on']['ljung_box']:.0f}")

        # ── Criterio de éxito ──
        def mae_delta(bd, reg):
            d = bd.get(reg, {})
            if d.get("off") and d.get("on"): return d["off"]["mae"] - d["on"]["mae"]
            return None
        crit = {
            "mejora post-meal 2-5h (test)": (mae_delta(test_bd, "post_meal_2_5h") or 0) > 0.3,
            "no degradación global (test)": (mae_delta(test_bd, "GLOBAL") or 0) > -0.5,
            "no degradación overnight (test)": (mae_delta(test_bd, "overnight") or 0) > -0.5,
            "whiteness igual o mejor": (white.get("on") and white.get("off") and white["on"]["ljung_box"] <= white["off"]["ljung_box"] * 1.05),
            "safety hypo no empeora (test)": (mae_delta(test_bd, "hypo_window") if test_bd.get("hypo_window", {}).get("on") else 0) is None or (mae_delta(test_bd, "hypo_window") or 0) > -1.0,
        }
        lines.append("\n## Criterio de éxito\n")
        for k, v in crit.items():
            lines.append(f"- {'✅' if v else '❌'} {k}")
        verdict = "PROMOVER (tras 5-7d de _ex_r1 live)" if all(crit.values()) else "NO promover — revisar"
        lines.append(f"\n**Veredicto:** {verdict}\n")

        md = "\n".join(lines)
        with open(os.path.join(OUT_DIR, "fpe_report.md"), "w") as f:
            f.write(md)
        print("\n" + md)
        print(f"\nReportes en {OUT_DIR}/ (md, json, csv)")


if __name__ == "__main__":
    main()
