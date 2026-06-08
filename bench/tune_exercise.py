"""
bench/tune_exercise.py
───────────────────────
Grid-search de las constantes del efecto de ejercicio del SSM, sobre la DB
local, usando el mismo A/B controlado que bench/ab_exercise.py.

Para cada combinación (EX_DROP_RATE_BASE × EX_SENS_SCALE) recorre el pipeline
completo (run_filter + forward_predict) en las ventanas 0-Nh post-ejercicio y
mide sesgo / MAE a +60 contra el real. Busca acercar el sesgo a 0 sin inflar
el MAE. El baseline OFF (sin ejercicio) se computa una sola vez como referencia.

Uso:  python3 -m bench.tune_exercise [--band-hours 3] [--max 90]
"""
import sys
sys.modules.setdefault("pytest", type(sys)("_pytest_stub"))

import math
import argparse


def _stats(errs):
    if not errs:
        return None
    n = len(errs)
    return (n,
            sum(abs(e) for e in errs) / n,                    # MAE
            math.sqrt(sum(e * e for e in errs) / n),          # RMSE
            sum(errs) / n,                                    # sesgo
            100 * sum(1 for e in errs if abs(e) <= 20) / n)   # ±20


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--band-hours", type=float, default=3.0)
    ap.add_argument("--max", type=int, default=120)
    args = ap.parse_args()

    DROP_GRID = [0.6, 1.2, 2.0]
    SCALE_GRID = [1.0, 2.0, 3.0, 4.0]

    from app import app
    with app.app_context():
        from models import GlucosePrediction, Activity
        import pmm.ssm.filter as F
        import pmm.ssm.exercise_input as EX
        from pmm.ssm.filter import run_filter, forward_predict
        from pmm.core.parameter_store import get_isf_now, get_icr_now
        from pmm.engines.drift import get_drift_status
        try:
            from utils.kinetics import dawn_roc_mgdl_min
        except Exception:
            dawn_roc_mgdl_min = lambda at_time=None: 0.0

        acts = [a.timestamp for a in Activity.query.order_by(Activity.timestamp).all()]

        def ex_relevant(t):
            return any(0 <= (t - a).total_seconds() / 3600.0 <= args.band_hours for a in acts)

        rows = (GlucosePrediction.query
                .filter(GlucosePrediction.resolved_60 == 1)
                .order_by(GlucosePrediction.predicted_at).all())
        rel = [r for r in rows if ex_relevant(r.predicted_at) and r.g_real_60 is not None]
        if len(rel) > args.max:
            stride = math.ceil(len(rel) / args.max)
            rel = rel[::stride]
        print(f"Ventanas 0-{args.band_hours:g}h post-ejercicio evaluadas: {len(rel)}\n")

        orig_load = F.load_activities

        def run(t, exercise):
            hora = t.hour
            isf = get_isf_now(hora=hora); icr = get_icr_now(hora=hora)
            drift = get_drift_status().get("drift_factor", 1.0)
            icr_m = icr.get("mu") or 12.0
            try: dawn = float(dawn_roc_mgdl_min(at_time=t) or 0.0)
            except Exception: dawn = 0.0
            F.load_activities = (orig_load if exercise else (lambda *a, **k: []))
            res = run_filter(now=t, isf_prior=isf.get("mu"), isf_sigma=isf.get("sigma"),
                             drift_factor=drift, icr_for_meals=icr_m)
            if res.error or res.n_cgm_used < 3:
                F.load_activities = orig_load; return None
            pr = forward_predict(res, horizons_min=(60,), drift_factor=drift,
                                 icr_for_meals=icr_m, dawn_rate=dawn,
                                 activities=(None if exercise else []))
            F.load_activities = orig_load
            return pr[60].g_pred

        # Baseline OFF (sin ejercicio) — invariante a las constantes
        off = []
        for r in rel:
            try:
                g = run(r.predicted_at, exercise=False)
            except Exception:
                g = None
            if g is not None:
                off.append((r, r.g_real_60 - g))
        s = _stats([e for _, e in off])
        print(f"BASELINE OFF (sin ejercicio) +60:  MAE {s[1]:.1f}  RMSE {s[2]:.1f}  sesgo {s[3]:+.1f}  ±20 {s[4]:.0f}%\n")

        print(f"{'drop':>5s} {'scale':>5s} | {'MAE':>6s} {'RMSE':>6s} {'sesgo':>6s} {'±20':>5s}   ΔMAE  Δ|sesgo|")
        print("-" * 64)
        results = []
        base_drop, base_scale = EX.EX_DROP_RATE_BASE, EX.EX_SENS_SCALE
        for drop in DROP_GRID:
            for scale in SCALE_GRID:
                EX.EX_DROP_RATE_BASE = drop
                EX.EX_SENS_SCALE = scale
                errs = []
                for r in rel:
                    try:
                        g = run(r.predicted_at, exercise=True)
                    except Exception:
                        g = None
                    if g is not None:
                        errs.append(r.g_real_60 - g)
                st = _stats(errs)
                d_mae = s[1] - st[1]                 # >0 = mejora MAE
                d_absbias = abs(s[3]) - abs(st[3])   # >0 = sesgo más cerca de 0
                results.append((drop, scale, st, d_mae, d_absbias))
                print(f"{drop:>5.1f} {scale:>5.1f} | {st[1]:>6.1f} {st[2]:>6.1f} {st[3]:>+6.1f} {st[4]:>4.0f}%  {d_mae:>+5.1f}   {d_absbias:>+5.1f}")
        EX.EX_DROP_RATE_BASE, EX.EX_SENS_SCALE = base_drop, base_scale

        # Mejor por |sesgo| más bajo, desempatando por MAE
        best = min(results, key=lambda x: (abs(x[2][3]), x[2][1]))
        print(f"\n→ Mejor por |sesgo|:  drop={best[0]}  scale={best[1]}  "
              f"(sesgo {best[2][3]:+.1f}, MAE {best[2][1]:.1f}, ±20 {best[2][4]:.0f}%)")
        best_mae = max(results, key=lambda x: x[3])
        print(f"→ Mejor por MAE:      drop={best_mae[0]}  scale={best_mae[1]}  "
              f"(MAE {best_mae[2][1]:.1f}, sesgo {best_mae[2][3]:+.1f}, ΔMAE {best_mae[3]:+.1f})")


if __name__ == "__main__":
    main()
