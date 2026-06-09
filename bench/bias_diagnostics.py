"""
bench/bias_diagnostics.py
──────────────────────────
Diagnóstico OFFLINE del sesgo de pronóstico (solo lectura). NO toca producción,
NO cambia parámetros, NO despliega.

Objetivo: localizar el origen del sesgo negativo persistente (el modelo
sobre-predice glucosa: error = g_real − g_pred < 0) desglosando por ventanas
fisiológicas LIMPIAS.

Dos fuentes SEPARADAS, agrupadas por model_version (sin mezclar):
  A. LIVE   — predicciones que el modelo guardó en tiempo real (glucose_predictions).
  B. REPLAY — recomputadas ahora con los parámetros de PRODUCCIÓN (= r1) sobre los
              mismos timestamps históricos (da el comportamiento de r1 con N alto;
              es replay cold-start, no el filtro continuo).

Convención de signo: error = g_real − g_pred. NEGATIVO = real por debajo de la
predicción = SOBRE-predicción.

Salidas: consola + bench/reports/bias/{bias_report.md,bias_report.json,bias_report.csv}

Uso:  python3 -m bench.bias_diagnostics [--replay-max 260] [--no-replay]
"""
import sys
sys.modules.setdefault("pytest", type(sys)("_stub"))

import os, json, math, argparse, sqlite3, statistics
from datetime import datetime, timedelta

DB = "instance/diabetes.db"
OUT = os.path.join(os.path.dirname(__file__), "reports", "bias")
PROD_VERSION = "ssm_v0_ukf6_basal_ex_r1"
OUTLIER_ERR = 100.0   # |error| mayor → artefacto (sensor/gap), se excluye

REGS = ["GLOBAL", "fasting", "basal_only", "overnight", "low_IOB", "low_COB",
        "no_recent_meal", "no_recent_correction", "stable_glucose",
        "post_meal_0_2h", "post_meal_2_5h", "exercise"]


def parse(s): return datetime.fromisoformat(s) if s else None


def hours_since(sorted_ts, t):
    """Horas desde el evento más reciente ≤ t (inf si no hay)."""
    import bisect
    i = bisect.bisect_right(sorted_ts, t)
    if i == 0:
        return float("inf")
    return (t - sorted_ts[i - 1]).total_seconds() / 3600.0


def regimes(t, iob, cob, roc, meal_ts, corr_ts, act_ts):
    hm = hours_since(meal_ts, t)
    hc = hours_since(corr_ts, t)
    ha = hours_since(act_ts, t)
    out = {"GLOBAL"}
    if 0 <= t.hour < 6: out.add("overnight")
    if hm >= 5: out.add("fasting")
    if hm >= 3: out.add("no_recent_meal")
    if hc >= 4: out.add("no_recent_correction")
    if iob is not None and iob < 0.5: out.add("low_IOB")
    if iob is not None and iob < 0.3 and hm >= 4: out.add("basal_only")
    if cob is not None and cob < 5: out.add("low_COB")
    if roc is not None and abs(roc) <= 0.5: out.add("stable_glucose")
    if 0 <= hm <= 2: out.add("post_meal_0_2h")
    if 2 < hm <= 5: out.add("post_meal_2_5h")
    if ha <= 5: out.add("exercise")
    return out


def acf1(series):
    n = len(series)
    if n < 30: return None
    m = sum(series) / n
    zc = [x - m for x in series]
    var = sum(x * x for x in zc)
    if var <= 0: return None
    return sum(zc[i] * zc[i + 1] for i in range(n - 1)) / var


def metrics(items):
    """items: lista de dict(e30,e60,s30,s60,pa). Devuelve métricas robustas."""
    e30 = [it["e30"] for it in items if it["e30"] is not None]
    e60 = [it["e60"] for it in items if it["e60"] is not None]
    if not e60: return None
    z60 = [it["e60"] / it["s60"] for it in items if it["e60"] is not None and it.get("s60")]
    a60 = [abs(e) for e in e60]
    ordered = [it["e60"] for it in sorted(items, key=lambda x: x["pa"]) if it["e60"] is not None]
    cov90 = (100 * sum(1 for it in items if it["e60"] is not None and it.get("s60")
                       and abs(it["e60"]) <= 1.645 * it["s60"]) / len(z60)) if z60 else None
    return dict(
        n=len(e60),
        mae30=(sum(abs(e) for e in e30) / len(e30)) if e30 else None,
        mae60=sum(a60) / len(a60),
        bias30=(sum(e30) / len(e30)) if e30 else None,
        bias60=sum(e60) / len(e60),
        median60=statistics.median(e60),
        p90_abs60=sorted(a60)[min(len(a60) - 1, int(0.9 * len(a60)))],
        stdz60=(math.sqrt(sum(z * z for z in z60) / len(z60)) if z60 else None),
        acf1_60=acf1(ordered),
        cov90_60=cov90,
    )


def breakdown(rows, meal_ts, corr_ts, act_ts):
    buckets = {r: [] for r in REGS}
    excl = 0
    for r in rows:
        e60 = r["error_60"] if r["resolved_60"] else None
        e30 = r["error_30"] if r["resolved_30"] else None
        if e60 is not None and abs(e60) > OUTLIER_ERR:
            excl += 1; e60 = None
        if e30 is not None and abs(e30) > OUTLIER_ERR:
            e30 = None
        if e60 is None and e30 is None:
            continue
        item = dict(e30=e30, e60=e60, s30=r["sigma_30"], s60=r["sigma_60"], pa=r["pa"])
        for reg in regimes(r["pa"], r["iob"], r["cob"], r["roc"], meal_ts, corr_ts, act_ts):
            buckets[reg].append(item)
    return {reg: metrics(items) for reg, items in buckets.items()}, excl


def load_events(cur):
    meal_ts = sorted(parse(r[0]) for r in cur.execute("select timestamp from meals"))
    corr_ts = sorted(parse(r[0]) for r in cur.execute(
        "select timestamp from insulin_doses where type='bolus' and (purpose='correccion' or purpose='corrección')"))
    act_ts = sorted(parse(r[0]) for r in cur.execute("select timestamp from activities"))
    return meal_ts, corr_ts, act_ts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--replay-max", type=int, default=260)
    ap.add_argument("--no-replay", action="store_true")
    args = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)

    con = sqlite3.connect(DB); con.row_factory = sqlite3.Row; cur = con.cursor()
    meal_ts, corr_ts, act_ts = load_events(cur)

    report = {"generated": datetime.now().isoformat(), "db": DB,
              "outlier_threshold_mgdl": OUTLIER_ERR, "live": {}, "replay": None}

    # ── A. LIVE por versión ──
    versions = [r[0] for r in cur.execute(
        "select model_version from glucose_predictions group by model_version order by count(*) desc")]
    live_bd = {}
    for ver in versions:
        if ver is None: continue
        rows = []
        for r in cur.execute("select * from glucose_predictions where model_version=? order by predicted_at", (ver,)):
            d = dict(r); d["pa"] = parse(d["predicted_at"]); rows.append(d)
        bd, excl = breakdown(rows, meal_ts, corr_ts, act_ts)
        live_bd[ver] = {"n_total": len(rows), "excluded_outliers": excl, "regimes": bd}
    report["live"] = live_bd

    # ── B. REPLAY con params de producción (= r1) ──
    replay_bd = None
    if not args.no_replay:
        from app import app
        with app.app_context():
            from models import GlucosePrediction
            from pmm.ssm.filter import run_filter, forward_predict
            from pmm.ssm.parameters import params_or_defaults
            from pmm.core.parameter_store import get_isf_now, get_icr_now
            from pmm.engines.drift import get_drift_status
            try: from utils.kinetics import dawn_roc_mgdl_min
            except Exception: dawn_roc_mgdl_min = lambda at_time=None: 0.0
            from pmm.ssm.version import MODEL_VERSION
            P = params_or_defaults(None)
            print(f"REPLAY con params de producción → MODEL_VERSION={MODEL_VERSION}, R_BASE={P.R_CGM_BASE}, R_MARD={P.R_CGM_MARD}")

            src = (GlucosePrediction.query
                   .filter(GlucosePrediction.resolved_60 == 1, GlucosePrediction.g_real_60 != None)
                   .order_by(GlucosePrediction.predicted_at).all())
            src = src[::max(1, len(src) // args.replay_max)]
            rows = []
            for s in src:
                t = s.predicted_at; hora = t.hour
                isf = get_isf_now(hora=hora); icr = get_icr_now(hora=hora)
                drift = get_drift_status().get("drift_factor", 1.0); icr_m = icr.get("mu") or 12.0
                try: dawn = float(dawn_roc_mgdl_min(at_time=t) or 0.0)
                except Exception: dawn = 0.0
                try:
                    res = run_filter(now=t, isf_prior=isf.get("mu"), isf_sigma=isf.get("sigma"),
                                     drift_factor=drift, icr_for_meals=icr_m, params=P)
                    if res.error or res.n_cgm_used < 3: continue
                    pr = forward_predict(res, horizons_min=(30, 60), drift_factor=drift,
                                         icr_for_meals=icr_m, dawn_rate=dawn, params=P)
                except Exception:
                    continue
                e30 = (s.g_real_30 - pr[30].g_pred) if s.g_real_30 is not None else None
                e60 = s.g_real_60 - pr[60].g_pred
                rows.append(dict(pa=t, iob=s.iob, cob=s.cob, roc=s.roc,
                                 error_30=e30, error_60=e60, resolved_30=1 if e30 is not None else 0,
                                 resolved_60=1, sigma_30=pr[30].sigma, sigma_60=pr[60].sigma))
            bd, excl = breakdown(rows, meal_ts, corr_ts, act_ts)
            replay_bd = {"model_version": PROD_VERSION, "n": len(rows), "excluded_outliers": excl,
                         "params": {"R_CGM_BASE": P.R_CGM_BASE, "R_CGM_MARD": P.R_CGM_MARD}, "regimes": bd}
    report["replay"] = replay_bd

    # ── Salidas ──
    with open(os.path.join(OUT, "bias_report.json"), "w") as f:
        json.dump(report, f, indent=2, default=lambda o: None)
    with open(os.path.join(OUT, "bias_report.csv"), "w") as f:
        f.write("source,model_version,regime,n,mae30,mae60,bias30,bias60,median60,p90abs60,stdz60,acf1_60,cov90_60\n")
        def wr(source, ver, bd):
            for reg in REGS:
                m = bd.get(reg)
                if not m: continue
                def g(k): v = m.get(k); return f"{v:.2f}" if isinstance(v, float) else ""
                f.write(f"{source},{ver},{reg},{m['n']},{g('mae30')},{g('mae60')},{g('bias30')},{g('bias60')},{g('median60')},{g('p90_abs60')},{g('stdz60')},{g('acf1_60')},{g('cov90_60')}\n")
        for ver, d in live_bd.items(): wr("live", ver, d["regimes"])
        if replay_bd: wr("replay", PROD_VERSION, replay_bd["regimes"])

    # ── Markdown + consola ──
    def table(bd):
        L = ["| régimen | n | MAE30 | MAE60 | sesgo30 | sesgo60 | mediana60 | p90|e|60 | std(z) | ACF₁ | cob90 |",
             "|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|"]
        for reg in REGS:
            m = bd.get(reg)
            if not m: continue
            def f(k, suf=""):
                v = m.get(k); return f"{v:.1f}{suf}" if isinstance(v, float) else "—"
            L.append(f"| {reg} | {m['n']} | {f('mae30')} | {f('mae60')} | {f('bias30','+'if False else '')} | "
                     f"{m['bias60']:+.1f} | {m['median60']:+.1f} | {f('p90_abs60')} | {f('stdz60')} | {f('acf1_60')} | {f('cov90_60','%')} |")
        return "\n".join(L)

    md = [f"# Diagnóstico de sesgo — `{PROD_VERSION}` (OFFLINE, solo lectura)",
          f"\n_Convención: error = g_real − g_pred. **Negativo = sobre-predicción.** "
          f"Outliers |error|>{OUTLIER_ERR:.0f} excluidos (artefactos)._\n"]

    md.append("## B. REPLAY — comportamiento de r1 (params de producción), N alto sobre histórico\n")
    if replay_bd:
        md.append(f"_Replay cold-start sobre {replay_bd['n']} timestamps; R_BASE={replay_bd['params']['R_CGM_BASE']}, "
                  f"R_MARD={replay_bd['params']['R_CGM_MARD']}. (No es el filtro continuo live.)_\n")
        md.append(table(replay_bd["regimes"]))
    else:
        md.append("_(replay desactivado)_")

    md.append("\n## A. LIVE — predicciones reales en tiempo real, por versión (sin mezclar)\n")
    md.append(f"> ⚠️ `{PROD_VERSION}` aún no tiene datos LIVE en este export. La versión live más reciente es "
              f"`ssm_v0_ukf6_basal_ex`. Se listan las de N útil para mostrar **persistencia del sesgo entre versiones**.\n")
    for ver, d in live_bd.items():
        if d["regimes"].get("GLOBAL") is None or d["regimes"]["GLOBAL"]["n"] < 25:
            continue
        md.append(f"\n### `{ver}`  (n={d['n_total']}, outliers excl={d['excluded_outliers']})\n")
        md.append(table(d["regimes"]))

    md_text = "\n".join(md)
    with open(os.path.join(OUT, "bias_report.md"), "w") as f:
        f.write(md_text)

    # Consola: resumen compacto del replay (r1) + interpretación
    print("\n" + "=" * 70)
    print(f"DIAGNÓSTICO DE SESGO — {PROD_VERSION} (replay) — sesgo60 por régimen")
    print("=" * 70)
    if replay_bd:
        for reg in REGS:
            m = replay_bd["regimes"].get(reg)
            if m:
                print(f"  {reg:22s} n={m['n']:4d}  sesgo60={m['bias60']:+6.1f}  MAE60={m['mae60']:5.1f}  "
                      f"std(z)={m['stdz60']:.2f}" if m['stdz60'] else f"  {reg:22s} n={m['n']:4d}  sesgo60={m['bias60']:+6.1f}")
    print(f"\nReportes → {OUT}/ (md, json, csv)")


if __name__ == "__main__":
    main()
