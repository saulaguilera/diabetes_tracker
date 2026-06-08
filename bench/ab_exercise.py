"""
bench/ab_exercise.py
─────────────────────
A/B OFFLINE del efecto del ejercicio en el SSM, sobre la DB local.

Para cada predicción YA RESUELTA cuyo timestamp cae en una ventana relevante
de ejercicio (≤24h tras una actividad), recorre el pipeline COMPLETO
(run_filter + forward_predict) dos veces — ejercicio OFF vs ON — y compara
ambas contra el valor real (g_real_30 / g_real_60 ya guardados).

Es un A/B controlado: mismos timestamps, mismos datos, solo se togglea el
ejercicio. Fuera de las ventanas de ejercicio ON==OFF por construcción, así
que no aportan señal y se omiten.

Uso:  python3 -m bench.ab_exercise [--max N] [--all]
"""
import sys
# Evitar que app.py arranque el scheduler/sync de fondo al importar.
sys.modules.setdefault("pytest", type(sys)("_pytest_stub"))

import math
import argparse
from datetime import timedelta


def _stats(errs):
    if not errs:
        return None
    n = len(errs)
    mae = sum(abs(e) for e in errs) / n
    rmse = math.sqrt(sum(e * e for e in errs) / n)
    bias = sum(errs) / n
    within20 = 100 * sum(1 for e in errs if abs(e) <= 20) / n
    return n, mae, rmse, bias, within20


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=250, help="máx de timestamps a evaluar (subsamplea)")
    ap.add_argument("--all", action="store_true", help="evaluar todos (ignora --max)")
    ap.add_argument("--band-hours", type=float, default=24.0, help="ventana post-actividad a considerar (h)")
    args = ap.parse_args()

    from app import app
    with app.app_context():
        from models import GlucosePrediction, Activity, GlucoseReading
        import pmm.ssm.filter as F
        from pmm.ssm.filter import run_filter, forward_predict
        from pmm.core.parameter_store import get_isf_now, get_icr_now
        from pmm.engines.drift import get_drift_status
        try:
            from utils.kinetics import dawn_roc_mgdl_min
        except Exception:
            dawn_roc_mgdl_min = lambda at_time=None: 0.0

        nread = GlucoseReading.query.count()
        last = GlucoseReading.query.order_by(GlucoseReading.timestamp.desc()).first()
        print(f"DB: {nread} lecturas, última {last.timestamp if last else '—'}")

        acts = [a.timestamp for a in Activity.query.order_by(Activity.timestamp).all()]
        print(f"Actividades: {len(acts)}")

        def ex_relevant(t):
            return any(0 <= (t - a).total_seconds() / 3600.0 <= args.band_hours for a in acts)

        rows = (GlucosePrediction.query
                .filter(GlucosePrediction.resolved_30 == 1)
                .order_by(GlucosePrediction.predicted_at).all())
        rel = [r for r in rows if ex_relevant(r.predicted_at)]
        print(f"Predicciones resueltas: {len(rows)}   relevantes a ejercicio (≤24h post): {len(rel)}")

        if not args.all and len(rel) > args.max:
            stride = math.ceil(len(rel) / args.max)
            rel = rel[::stride]
            print(f"Subsampleo con stride {stride} → {len(rel)} timestamps")

        orig_load = F.load_activities

        def predict(t, exercise):
            hora = t.hour
            isf = get_isf_now(hora=hora)
            icr = get_icr_now(hora=hora)
            drift = get_drift_status().get("drift_factor", 1.0)
            icr_m = icr.get("mu") or 12.0
            try:
                dawn = float(dawn_roc_mgdl_min(at_time=t) or 0.0)
            except Exception:
                dawn = 0.0
            # Toggle ejercicio en run_filter vía el loader; en forward vía arg.
            F.load_activities = (orig_load if exercise else (lambda *a, **k: []))
            res = run_filter(now=t, isf_prior=isf.get("mu"), isf_sigma=isf.get("sigma"),
                             drift_factor=drift, icr_for_meals=icr_m)
            if res.error or res.n_cgm_used < 3:
                F.load_activities = orig_load
                return None
            pr = forward_predict(res, horizons_min=(30, 60), drift_factor=drift,
                                 icr_for_meals=icr_m, dawn_rate=dawn,
                                 activities=(None if exercise else []))
            F.load_activities = orig_load
            return pr

        off30, on30, off60, on60 = [], [], [], []
        live30 = []  # error de la predicción guardada en vivo (referencia/sanity)
        done = 0
        for r in rel:
            try:
                p_off = predict(r.predicted_at, exercise=False)
                p_on = predict(r.predicted_at, exercise=True)
            except Exception:
                continue
            if not p_off or not p_on:
                continue
            if r.g_real_30 is not None:
                off30.append(r.g_real_30 - p_off[30].g_pred)
                on30.append(r.g_real_30 - p_on[30].g_pred)
                if r.g_pred_30 is not None:
                    live30.append(r.g_real_30 - r.g_pred_30)
            if r.g_real_60 is not None and r.resolved_60:
                off60.append(r.g_real_60 - p_off[60].g_pred)
                on60.append(r.g_real_60 - p_on[60].g_pred)
            done += 1
            if done % 50 == 0:
                print(f"  ...{done}/{len(rel)}")

        print(f"\nEvaluadas: {done} ventanas relevantes a ejercicio\n")
        print(f"{'':28s} {'n':>4s} {'MAE':>6s} {'RMSE':>6s} {'sesgo':>6s} {'±20':>5s}")
        for label, errs in (("+30 SIN ejercicio (OFF)", off30),
                            ("+30 CON ejercicio (ON)", on30),
                            ("+60 SIN ejercicio (OFF)", off60),
                            ("+60 CON ejercicio (ON)", on60)):
            s = _stats(errs)
            if s:
                n, mae, rmse, bias, w = s
                print(f"{label:28s} {n:>4d} {mae:>6.1f} {rmse:>6.1f} {bias:>+6.1f} {w:>4.0f}%")

        def improvement(off, on, h):
            so, sn = _stats(off), _stats(on)
            if so and sn:
                d_mae = so[1] - sn[1]
                d_rmse = so[2] - sn[2]
                print(f"  +{h}min: ΔMAE {d_mae:+.1f}  ({so[1]:.1f}→{sn[1]:.1f}),  "
                      f"ΔRMSE {d_rmse:+.1f}  ({so[2]:.1f}→{sn[2]:.1f})   "
                      f"{'MEJORA' if d_mae > 0 else 'empeora' if d_mae < 0 else 'igual'}")

        print("\n── Mejora del ejercicio (OFF → ON) en ventanas post-ejercicio ──")
        improvement(off30, on30, 30)
        improvement(off60, on60, 60)

        sl = _stats(live30)
        if sl:
            print(f"\n(sanity) error de la predicción en vivo +30: MAE {sl[1]:.1f}  "
                  f"vs OFF recomputado {_stats(off30)[1]:.1f} — deberían parecerse)")


if __name__ == "__main__":
    main()
