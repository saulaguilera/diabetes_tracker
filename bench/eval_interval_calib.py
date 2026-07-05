"""
bench/eval_interval_calib.py
─────────────────────────────
EXPERIMENTO r4 — calibración de intervalos +60 (OFFLINE, solo lectura).

El multiplicador es post-hoc sobre σ: NO cambia la media/MAE/sesgo (g_pred intacto),
solo el ancho del intervalo. Por eso la evaluación es ANALÍTICA y EXACTA sobre los σ
ya guardados de las predicciones LIVE de producción:

    z = error / σ        →     con multiplicador m:   z_cal = z / m
    cobertura_cal(nivel) = fracción |z| ≤ nivel·m

Es exactamente equivalente a lo que produciría el mecanismo implementado
(pmm/ssm/interval_calib.py) si se desplegara. No requiere replay.

Método:
  - dataset = predicciones LIVE de ssm_v0_ukf6_basal_ex_r2_gated_bias.
  - split por tiempo: train / test (held-out).
  - tune m (+60) SOLO en train → m = RMS(z_train) (lleva std(z)→1).
  - validar en test, por régimen: IC50/80/90/95, std(z), mean(z), ancho, MAE, sesgo.

Uso:  python3 -m bench.eval_interval_calib [--test-frac 0.30]
"""
import os, json, math, argparse, sqlite3, bisect
from datetime import datetime

DB = "instance/diabetes.db"
OUT = os.path.join(os.path.dirname(__file__), "reports", "interval_calib")
PROD = "ssm_v0_ukf6_basal_ex_r2_gated_bias"
OUTLIER = 100.0
# niveles de intervalo (z crítico bilateral) → cobertura ideal
LEVELS = {"IC50": (0.674, 50), "IC80": (1.282, 80), "IC90": (1.645, 90), "IC95": (1.960, 95)}
REGS = ["GLOBAL", "fasting", "basal_only", "stable_glucose", "overnight",
        "post_meal_0_2h", "post_meal_2_5h", "hypo_window", "high_glucose"]


def parse(s): return datetime.fromisoformat(s) if s else None


def hs(ts, t):
    i = bisect.bisect_right(ts, t)
    return 1e9 if i == 0 else (t - ts[i - 1]).total_seconds() / 3600.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test-frac", type=float, default=0.30)
    args = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)

    con = sqlite3.connect(DB); con.row_factory = sqlite3.Row; cur = con.cursor()
    meal_ts = sorted(parse(r[0]) for r in cur.execute("select timestamp from meals"))
    act_ts = sorted(parse(r[0]) for r in cur.execute("select timestamp from activities"))

    def regimes(t, iob, cob, roc, g):
        hm = hs(meal_ts, t); out = {"GLOBAL"}
        if 0 <= t.hour < 6: out.add("overnight")
        if hm >= 5 and (cob is None or cob < 5): out.add("fasting")
        if iob is not None and iob < 0.3 and hm >= 4: out.add("basal_only")
        if roc is not None and abs(roc) <= 0.5: out.add("stable_glucose")
        if 0 <= hm <= 2: out.add("post_meal_0_2h")
        if 2 < hm <= 5: out.add("post_meal_2_5h")
        if g is not None and g < 80: out.add("hypo_window")
        if g is not None and g > 180: out.add("high_glucose")
        return out

    rows = []
    for r in cur.execute("select * from glucose_predictions where model_version=? and resolved_60=1 "
                         "and error_60 is not null and sigma_60>0 order by predicted_at", (PROD,)):
        if abs(r["error_60"]) > OUTLIER: continue
        t = parse(r["predicted_at"])
        rows.append(dict(t=t, e=r["error_60"], s=r["sigma_60"], z=r["error_60"] / r["sigma_60"],
                         regs=regimes(t, r["iob"], r["cob"], r["roc"], r["g_actual"])))
    n = len(rows)
    split = int(n * (1 - args.test_frac))
    train, test = rows[:split], rows[split:]
    print(f"{PROD} live +60: {n} resueltas  |  TRAIN {len(train)} ({train[0]['t']:%m-%d}→{train[-1]['t']:%m-%d})  "
          f"TEST {len(test)} ({test[0]['t']:%m-%d}→{test[-1]['t']:%m-%d}) [held-out]")

    # ── Tune m SOLO en train (RMS(z) → std(z)→1) ──
    zt = [x["z"] for x in train]
    m = math.sqrt(sum(z * z for z in zt) / len(zt))
    print(f"\n── Tuning (solo train): m(+60) = RMS(z_train) = {m:.2f} ──")

    def cov(zs, crit): return 100 * sum(1 for z in zs if abs(z) <= crit) / len(zs)
    def metrics(items, mult):
        zs = [x["z"] for x in items]; e = [x["e"] for x in items]; s = [x["s"] for x in items]
        if not zs: return None
        out = dict(n=len(zs),
                   stdz=math.sqrt(sum((z / mult) ** 2 for z in zs) / len(zs)),
                   meanz=sum(z / mult for z in zs) / len(zs),
                   mae60=sum(abs(x) for x in e) / len(e),
                   bias60=sum(e) / len(e),
                   width90=sum(2 * 1.645 * si * mult for si in s) / len(s))
        for name, (crit, _ideal) in LEVELS.items():
            out[name] = cov(zs, crit * mult)   # |z| ≤ crit·m  ≡  |z/m| ≤ crit
        return out

    def by_regime(items, mult):
        return {rg: metrics([x for x in items if rg in x["regs"]], mult) for rg in REGS}

    base = by_regime(test, 1.0)
    calib = by_regime(test, m)

    # ── Gate ──
    def G(d, rg, k): x = d.get(rg); return x[k] if x and x.get(k) is not None else None
    gate = {}
    gate["1_IC90_toward_90"] = abs(G(calib, "GLOBAL", "IC90") - 90) < abs(G(base, "GLOBAL", "IC90") - 90) - 3
    gate["2_stdz_toward_1"] = abs(G(calib, "GLOBAL", "stdz") - 1) < abs(G(base, "GLOBAL", "stdz") - 1)
    gate["3_mean_unchanged"] = True   # por construcción (post-hoc σ; g_pred intacto)
    gate["4_mae_unchanged"] = True    # por construcción
    gate["5_bias_unchanged"] = True   # por construcción
    hb, hc = G(base, "hypo_window", "IC90"), G(calib, "hypo_window", "IC90")
    gate["6_hypo_cov_not_worse"] = (abs(hc - 90) <= abs(hb - 90) + 3) if (hb is not None and hc is not None) else None
    gate["7_intervals_not_absurd"] = (m <= 2.5)
    gate["8_holds_heldout"] = gate["1_IC90_toward_90"]   # medido en test

    passed = sum(1 for v in gate.values() if v is True)
    relevant = sum(1 for v in gate.values() if v is not None)
    if gate["1_IC90_toward_90"] and gate["2_stdz_toward_1"] and gate["7_intervals_not_absurd"] and (gate["6_hypo_cov_not_worse"] is not False):
        rec = "CANDIDATE for later deploy (post-hoc +60 sigma multiplier; mean untouched)"
    elif gate["1_IC90_toward_90"]:
        rec = "KEEP OFFLINE — IC90 improves but a secondary gate failed"
    else:
        rec = "REJECT — IC90 does not improve held-out"

    # ── Salidas ──
    report = {"generated": datetime.now().isoformat(), "model": PROD, "experiment": "r4-interval-calibration-h60",
              "sigma_mult_60": round(m, 3), "method": "post-hoc sigma multiplier (+60), m=RMS(z_train)",
              "gate": gate, "recommendation": rec,
              "test": {"baseline": base, "calibrated": calib}}
    with open(os.path.join(OUT, "interval_calib_report.json"), "w") as f:
        json.dump(report, f, indent=2, default=lambda o: None)
    with open(os.path.join(OUT, "interval_calib_report.csv"), "w") as f:
        f.write("arm,regime,n,stdz,meanz,IC50,IC80,IC90,IC95,mae60,bias60,width90\n")
        for arm, bd in (("baseline", base), ("calibrated", calib)):
            for rg in REGS:
                x = bd.get(rg)
                if not x: continue
                def v(k): y = x.get(k); return f"{y:.2f}" if isinstance(y, float) else ""
                f.write(f"{arm},{rg},{x['n']},{v('stdz')},{v('meanz')},{v('IC50')},{v('IC80')},{v('IC90')},{v('IC95')},{v('mae60')},{v('bias60')},{v('width90')}\n")

    def table(bd):
        L = ["| régimen | n | std(z) | IC50 | IC80 | IC90 | IC95 | ancho90 |",
             "|---|--:|--:|--:|--:|--:|--:|--:|"]
        for rg in REGS:
            x = bd.get(rg)
            if not x: continue
            def v(k, s=""): y = x.get(k); return f"{y:.0f}{s}" if isinstance(y, float) else "—"
            L.append(f"| {rg} | {x['n']} | {x['stdz']:.2f} | {v('IC50','%')} | {v('IC80','%')} | "
                     f"{v('IC90','%')} | {v('IC95','%')} | {v('width90')} |")
        return "\n".join(L)

    md = [f"# Experimento r4 — calibración de intervalos +60 (OFFLINE, {PROD})",
          f"\n_Post-hoc σ multiplier. NO cambia media/MAE/sesgo (g_pred intacto). "
          f"Ideal: IC90≈90%, std(z)≈1. m(+60) tuneado solo en train = **{m:.2f}**._\n",
          "## Gate de éxito (test held-out)\n"]
    for k, v in gate.items():
        md.append(f"- {'✅' if v is True else ('❌' if v is False else '—')} {k}")
    md += [f"\n**Recomendación: {rec}**\n",
           "## TEST held-out — baseline (σ actual)\n", table(base),
           f"\n## TEST held-out — calibrado (σ ×{m:.2f} a +60)\n", table(calib)]
    with open(os.path.join(OUT, "interval_calib_report.md"), "w") as f:
        f.write("\n".join(md))

    # ── Consola ──
    print(f"\n{'régimen':16s} | {'IC90 base→cal':^16s} | {'std(z) base→cal':^17s} | {'ancho90 base→cal':^18s}")
    for rg in REGS:
        b, c = base.get(rg), calib.get(rg)
        if not b or not c: continue
        print(f"{rg:16s} | {b['IC90']:>6.0f}% → {c['IC90']:>5.0f}% | {b['stdz']:>6.2f} → {c['stdz']:>6.2f}  | "
              f"{b['width90']:>6.0f} → {c['width90']:>6.0f}")
    print(f"\nGATE: {passed}/{relevant} en verde")
    print(f"RECOMENDACIÓN: {rec}")
    print(f"Reportes → {OUT}/ (md, json, csv)")


if __name__ == "__main__":
    main()
