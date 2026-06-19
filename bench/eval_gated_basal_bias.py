"""
bench/eval_basal_bias.py
─────────────────────────
EXPERIMENTO r3 — evaluación OFFLINE de la corrección con COMPUERTA por glucosa.
Solo lectura. No toca producción. No despliega.

Método (estricto):
  1. Eval set = predicciones LIVE de r1 (ssm_v0_ukf6_basal_ex_r1), con sus
     timestamps/actuals/contexto reales. Split por TIEMPO: train (viejo) / test.
  2. TUNING: el offset se elige SOLO en train, sobre ventanas LIMPIAS
     (fasting / basal_only / low_COB / no_recent_meal), minimizando |sesgo +60|.
     No se optimiza sobre test ni sobre el global de test.
  3. VALIDACIÓN held-out: en test, por régimen, comparando
        r1 baseline (offset 0)  vs  r2 ON (offset elegido).
     (r2 OFF ≡ r1 por construcción — flag OFF da dG idéntico.)
  4. Gate de éxito de 9 condiciones → recomendación.

NO recalibra intervalos (σ se reporta pero no se toca). El offset solo mueve la
MEDIA; la covarianza del filtro no cambia.

Uso:  python3 -m bench.eval_basal_bias [--train-clean-max 120] [--test-max 150]
"""
import sys
sys.modules.setdefault("pytest", type(sys)("_stub"))

import os, json, math, argparse, statistics, bisect
from datetime import datetime

DB = "instance/diabetes.db"
OUT = os.path.join(os.path.dirname(__file__), "reports", "gated_basal_bias")
PROD = "ssm_v0_ukf6_basal_ex_r1"
OUTLIER = 100.0
CANDIDATES = [-0.15, -0.20, -0.25, -0.30, -0.35]  # gate atenúa → base un poco mayor
CLEAN = ["fasting", "basal_only", "low_COB", "no_recent_meal"]
REGS = ["GLOBAL", "fasting", "basal_only", "low_COB", "no_recent_meal",
        "no_recent_correction", "stable_glucose", "overnight",
        "post_meal_0_2h", "post_meal_2_5h", "exercise", "hypo_window", "high_glucose"]


def parse(s): return datetime.fromisoformat(s) if s else None


def hours_since(sorted_ts, t):
    i = bisect.bisect_right(sorted_ts, t)
    return float("inf") if i == 0 else (t - sorted_ts[i - 1]).total_seconds() / 3600.0


def regimes(t, iob, cob, roc, g_actual, meal_ts, corr_ts, act_ts):
    hm = hours_since(meal_ts, t); hc = hours_since(corr_ts, t); ha = hours_since(act_ts, t)
    out = {"GLOBAL"}
    if 0 <= t.hour < 6: out.add("overnight")
    if hm >= 5 and (cob is None or cob < 5): out.add("fasting")
    if hm >= 3: out.add("no_recent_meal")
    if hc >= 4: out.add("no_recent_correction")
    if iob is not None and iob < 0.3 and hm >= 4: out.add("basal_only")
    if cob is not None and cob < 5: out.add("low_COB")
    if roc is not None and abs(roc) <= 0.5: out.add("stable_glucose")
    if 0 <= hm <= 2: out.add("post_meal_0_2h")
    if 2 < hm <= 5: out.add("post_meal_2_5h")
    if ha <= 5: out.add("exercise")
    if g_actual is not None and g_actual < 80: out.add("hypo_window")
    if g_actual is not None and g_actual > 180: out.add("high_glucose")
    return out


def stats(e30, e60, z60, ordered60):
    if not e60: return None
    n = len(e60)
    def acf1(s):
        if len(s) < 30: return None
        m = sum(s) / len(s); zc = [x - m for x in s]; v = sum(x * x for x in zc)
        return sum(zc[i] * zc[i + 1] for i in range(len(s) - 1)) / v if v > 0 else None
    def ljung(s, K=10):
        if len(s) < 40: return None
        m = sum(s) / len(s); zc = [x - m for x in s]; v = sum(x * x for x in zc)
        if v <= 0: return None
        N = len(s)
        acf = [sum(zc[i] * zc[i + k] for i in range(N - k)) / v for k in range(1, K + 1)]
        return N * (N + 2) * sum(acf[k - 1] ** 2 / (N - k) for k in range(1, K + 1))
    a60 = [abs(x) for x in e60]
    return dict(
        n=n,
        mae30=(sum(abs(x) for x in e30) / len(e30)) if e30 else None,
        mae60=sum(a60) / n,
        bias30=(sum(e30) / len(e30)) if e30 else None,
        bias60=sum(e60) / n,
        rmse60=math.sqrt(sum(x * x for x in e60) / n),
        p90abs60=sorted(a60)[min(n - 1, int(0.9 * n))],
        within20_60=100 * sum(1 for x in a60 if x <= 20) / n,
        stdz60=(math.sqrt(sum(z * z for z in z60) / len(z60)) if z60 else None),
        ic90_60=(100 * sum(1 for z in z60 if abs(z) <= 1.645) / len(z60)) if z60 else None,
        acf1_60=acf1(ordered60),
        ljung_60=ljung(ordered60),
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--train-clean-max", type=int, default=120)
    ap.add_argument("--test-max", type=int, default=150)
    ap.add_argument("--test-frac", type=float, default=0.30)
    args = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)

    import sqlite3
    con = sqlite3.connect(DB); con.row_factory = sqlite3.Row; cur = con.cursor()
    meal_ts = sorted(parse(r[0]) for r in cur.execute("select timestamp from meals"))
    corr_ts = sorted(parse(r[0]) for r in cur.execute(
        "select timestamp from insulin_doses where type='bolus' and (purpose='correccion' or purpose='corrección')"))
    act_ts = sorted(parse(r[0]) for r in cur.execute("select timestamp from activities"))

    rows = [dict(r) for r in cur.execute(
        "select * from glucose_predictions where model_version=? and resolved_60=1 and g_real_60 is not null "
        "order by predicted_at", (PROD,))]
    for r in rows:
        r["pa"] = parse(r["predicted_at"])
        r["regs"] = regimes(r["pa"], r["iob"], r["cob"], r["roc"], r["g_actual"], meal_ts, corr_ts, act_ts)
    split = int(len(rows) * (1 - args.test_frac))
    train, test = rows[:split], rows[split:]
    print(f"r1 live resueltas: {len(rows)}  |  TRAIN {len(train)} ({train[0]['pa']:%m-%d}→{train[-1]['pa']:%m-%d})  "
          f"TEST {len(test)} ({test[0]['pa']:%m-%d}→{test[-1]['pa']:%m-%d}) [held-out]")

    from app import app
    with app.app_context():
        from pmm.ssm.filter import run_filter, forward_predict
        from pmm.ssm.parameters import params_or_defaults
        from pmm.core.parameter_store import get_isf_now, get_icr_now
        from pmm.engines.drift import get_drift_status
        import pmm.ssm.basal_bias as BB
        try: from utils.kinetics import dawn_roc_mgdl_min
        except Exception: dawn_roc_mgdl_min = lambda at_time=None: 0.0
        P = params_or_defaults(None)

        def predict(t):
            hora = t.hour; isf = get_isf_now(hora=hora); icr = get_icr_now(hora=hora)
            drift = get_drift_status().get("drift_factor", 1.0); icr_m = icr.get("mu") or 12.0
            try: dawn = float(dawn_roc_mgdl_min(at_time=t) or 0.0)
            except Exception: dawn = 0.0
            res = run_filter(now=t, isf_prior=isf.get("mu"), isf_sigma=isf.get("sigma"),
                             drift_factor=drift, icr_for_meals=icr_m, params=P)
            if res.error or res.n_cgm_used < 3: return None
            return forward_predict(res, horizons_min=(30, 60), drift_factor=drift,
                                   icr_for_meals=icr_m, dawn_rate=dawn, params=P)

        def set_offset(off):
            BB.BASAL_NET_BIAS_ENABLED = (off != 0.0)
            BB.BASAL_NET_OFFSET = off

        def eval_rows(rs, off):
            set_offset(off)
            res = []
            for r in rs:
                try: pr = predict(r["pa"])
                except Exception: pr = None
                if not pr: continue
                e30 = (r["g_real_30"] - pr[30].g_pred) if r["g_real_30"] is not None else None
                e60 = r["g_real_60"] - pr[60].g_pred
                if abs(e60) > OUTLIER: e60 = None
                if e30 is not None and abs(e30) > OUTLIER: e30 = None
                if e60 is None: continue
                res.append(dict(pa=r["pa"], regs=r["regs"], e30=e30, e60=e60, s60=pr[60].sigma))
            set_offset(0.0)
            return res

        def by_regime(evrows):
            out = {}
            for reg in REGS:
                sel = [x for x in evrows if reg in x["regs"]]
                if not sel: out[reg] = None; continue
                sel.sort(key=lambda x: x["pa"])
                e30 = [x["e30"] for x in sel if x["e30"] is not None]
                e60 = [x["e60"] for x in sel]
                z60 = [x["e60"] / x["s60"] for x in sel if x["s60"]]
                out[reg] = stats(e30, e60, z60, e60)
            return out

        # ── 1) TUNING en TRAIN, ventanas limpias ──
        train_clean = [r for r in train if any(c in r["regs"] for c in CLEAN)]
        train_clean = train_clean[::max(1, len(train_clean) // args.train_clean_max)]
        print(f"\n── TUNING (solo train, ventanas limpias {CLEAN}, n={len(train_clean)}) ──")
        print(f"{'offset':>7s} {'sesgo+60':>9s} {'|sesgo|':>8s} {'MAE+60':>7s}")
        tune = []
        for off in [0.0] + CANDIDATES:
            ev = eval_rows(train_clean, off)
            e60 = [x["e60"] for x in ev]
            b = sum(e60) / len(e60); mae = sum(abs(x) for x in e60) / len(e60)
            tune.append((off, b, mae))
            print(f"{off:>7.2f} {b:>+9.1f} {abs(b):>8.1f} {mae:>7.1f}")
        # elegir min |sesgo| entre los candidatos (excluye 0), MAE como desempate
        cand = [t for t in tune if t[0] != 0.0]
        best = min(cand, key=lambda t: (abs(t[1]), t[2]))
        chosen = best[0]
        print(f"→ offset elegido en train: {chosen:+.2f} mg/dL/min (|sesgo| train-clean {abs(best[1]):.1f})")

        # ── 2) VALIDACIÓN held-out en TEST ──
        test_s = test[::max(1, len(test) // args.test_max)]
        print(f"\n── TEST held-out (n={len(test_s)}): baseline (r1) vs r2 ON ({chosen:+.2f}) ──")
        base_ev = eval_rows(test_s, 0.0)
        on_ev = eval_rows(test_s, chosen)
        base_bd = by_regime(base_ev)
        on_bd = by_regime(on_ev)

    # ── Gate de éxito ──
    def g(bd, reg, k):
        m = bd.get(reg); return m[k] if m and m.get(k) is not None else None
    gb, go = base_bd, on_bd
    gate = {}
    gate["1_global_bias_to_0"] = (abs(g(go,"GLOBAL","bias60")) <= abs(g(gb,"GLOBAL","bias60")) - 2.0)
    gate["2_fasting_bias_improves"] = (abs(g(go,"fasting","bias60")) < abs(g(gb,"fasting","bias60"))) if g(gb,"fasting","bias60") is not None else None
    gate["3_stable_bias_improves"] = (abs(g(go,"stable_glucose","bias60")) < abs(g(gb,"stable_glucose","bias60"))) if g(gb,"stable_glucose","bias60") is not None else None
    gate["4_overnight_not_worse"] = (abs(g(go,"overnight","bias60")) <= abs(g(gb,"overnight","bias60")) + 1.0) if g(gb,"overnight","bias60") is not None else None
    def pm_ok(reg):
        b, o = g(gb,reg,"mae60"), g(go,reg,"mae60")
        return (o <= b + 2.0) if (b is not None and o is not None) else None
    gate["5_postmeal_no_regress"] = all(x for x in [pm_ok("post_meal_0_2h"), pm_ok("post_meal_2_5h")] if x is not None) or None
    def hypo_safe():
        b, o = g(gb,"hypo_window","mae60"), g(go,"hypo_window","mae60")
        bb, ob = g(gb,"hypo_window","bias60"), g(go,"hypo_window","bias60")
        if b is None: return None
        return (o <= b + 3.0) and (ob is None or ob <= 5.0)   # no fuerte under-pred nueva
    gate["6_hypo_not_unsafe"] = hypo_safe()
    gate["7_mae_improves_or_neutral"] = (g(go,"GLOBAL","mae60") <= g(gb,"GLOBAL","mae60") + 0.5)
    gate["8_no_new_positive_bias"] = all((g(go,r,"bias60") is None) or (g(go,r,"bias60") <= 3.0) for r in REGS)
    gate["9_holds_heldout"] = gate["1_global_bias_to_0"]   # medido en test = held-out

    passed = sum(1 for v in gate.values() if v is True)
    relevant = sum(1 for v in gate.values() if v is not None)
    if gate["1_global_bias_to_0"] and gate["7_mae_improves_or_neutral"] and gate["8_no_new_positive_bias"] and (gate["6_hypo_not_unsafe"] is not False):
        rec = "CANDIDATE for later deploy (pending review + separate interval-calibration experiment)"
    elif gate["1_global_bias_to_0"]:
        rec = "KEEP OFFLINE — bias improves but some secondary gate failed; needs refinement"
    else:
        rec = "REJECT — no material bias improvement held-out"

    # ── Salidas ──
    report = {"generated": datetime.now().isoformat(), "model": PROD, "experiment": "r3-glucose-gated-basal-bias",
              "chosen_offset_mgdl_min": chosen, "tuning_train_clean": [{"offset": o, "bias60": b, "mae60": m} for o, b, m in tune],
              "gate": {k: v for k, v in gate.items()}, "recommendation": rec,
              "test": {"baseline_r1": base_bd, "r2_on": on_bd}}
    with open(os.path.join(OUT, "basal_bias_report.json"), "w") as f:
        json.dump(report, f, indent=2, default=lambda o: None)
    with open(os.path.join(OUT, "basal_bias_report.csv"), "w") as f:
        f.write("arm,regime,n,mae30,mae60,bias30,bias60,rmse60,p90abs60,within20_60,stdz60,ic90_60,acf1_60,ljung60\n")
        def wr(arm, bd):
            for reg in REGS:
                m = bd.get(reg)
                if not m: continue
                def v(k): x = m.get(k); return f"{x:.2f}" if isinstance(x, float) else ""
                f.write(f"{arm},{reg},{m['n']},{v('mae30')},{v('mae60')},{v('bias30')},{v('bias60')},{v('rmse60')},{v('p90abs60')},{v('within20_60')},{v('stdz60')},{v('ic90_60')},{v('acf1_60')},{v('ljung_60')}\n")
        wr("baseline_r1", base_bd); wr("r2_on", on_bd)

    def table(bd):
        L = ["| régimen | n | MAE60 | sesgo60 | RMSE60 | p90|e| | ±20 | std(z) | IC90 | ACF₁ |",
             "|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|"]
        for reg in REGS:
            m = bd.get(reg)
            if not m: continue
            def v(k, s=""): x = m.get(k); return f"{x:.1f}{s}" if isinstance(x, float) else "—"
            L.append(f"| {reg} | {m['n']} | {v('mae60')} | {m['bias60']:+.1f} | {v('rmse60')} | {v('p90abs60')} | "
                     f"{v('within20_60','%')} | {v('stdz60')} | {v('ic90_60','%')} | {v('acf1_60')} |")
        return "\n".join(L)

    md = [f"# Experimento r3 — corrección basal neta CON COMPUERTA por glucosa (OFFLINE, {PROD})",
          f"\n_Solo lectura. No producción, no merge, no deploy. Convención: error = g_real − g_pred; "
          f"negativo = sobre-predicción. Offset elegido **{chosen:+.2f} mg/dL/min** (tuneado solo en train, "
          f"ventanas {CLEAN})._\n",
          "## Tuning (solo train, ventanas limpias)\n",
          "| offset | sesgo+60 | MAE+60 |", "|--:|--:|--:|"]
    for o, b, m in tune:
        md.append(f"| {o:+.2f} | {b:+.1f} | {m:.1f} |")
    md += [f"\n## Gate de éxito (test held-out)\n"]
    for k, v in gate.items():
        md.append(f"- {'✅' if v is True else ('❌' if v is False else '—')} {k}")
    md += [f"\n**Recomendación: {rec}**\n",
           "## TEST held-out — baseline r1 (offset 0)\n", table(base_bd),
           f"\n## TEST held-out — r2 ON (offset {chosen:+.2f})\n", table(on_bd)]
    with open(os.path.join(OUT, "basal_bias_report.md"), "w") as f:
        f.write("\n".join(md))

    # ── Consola ──
    print(f"\n{'régimen':16s} {'baseline sesgo60':>16s} {'r2 sesgo60':>11s} {'Δ|sesgo|':>9s} {'base MAE60':>10s} {'r2 MAE60':>9s}")
    for reg in REGS:
        b, o = base_bd.get(reg), on_bd.get(reg)
        if not b or not o: continue
        d = abs(b["bias60"]) - abs(o["bias60"])
        print(f"{reg:16s} {b['bias60']:>+16.1f} {o['bias60']:>+11.1f} {d:>+9.1f} {b['mae60']:>10.1f} {o['mae60']:>9.1f}")
    print(f"\nGATE: {passed}/{relevant} en verde")
    print(f"RECOMENDACIÓN: {rec}")
    print(f"Reportes → {OUT}/ (md, json, csv)")


if __name__ == "__main__":
    main()
