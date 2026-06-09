"""
bench/holdout_eval.py
──────────────────────
Validación HELD-OUT (out-of-sample) del SSM sobre la DB local.

Idea: partir el tiempo en TRAIN (período viejo) y TEST (período nuevo que el
modelo/tuneo nunca vio). Cualquier parámetro se elige SOLO en train; el número
honesto de generalización sale de medir en test. Si una mejora vista en train
NO se sostiene en test → era overfitting.

Demostración por defecto (parámetro R, el ruido de observación):
  1. Barre R en TRAIN y elige el mejor (por MAE +60 de train).
  2. Evalúa en TEST: R viejo (4.0/0.09) vs R re-derivado en train.
  3. Reporta train y test lado a lado → la brecha mide overfitting.

Es además el harness REUTILIZABLE: cualquier cambio futuro (p.ej. grasa/proteína)
se valida llamando evaluate() sobre el MISMO test set, sin tocarlo al tunear.

Uso:  python3 -m bench.holdout_eval [--test-frac 0.25] [--max 110]
"""
import sys
sys.modules.setdefault("pytest", type(sys)("_stub"))

import math
import argparse
from dataclasses import replace


def _agg(errs_sig, horizon_with_sigma=False):
    """errs_sig: lista de error (o (error, sigma)). Devuelve dict de métricas."""
    if not errs_sig:
        return None
    if horizon_with_sigma:
        errs = [e for e, s in errs_sig]
        zs = [e / s for e, s in errs_sig if s and s > 0]
    else:
        errs = errs_sig
        zs = []
    n = len(errs)
    mae = sum(abs(e) for e in errs) / n
    rmse = math.sqrt(sum(e * e for e in errs) / n)
    bias = sum(errs) / n
    w20 = 100 * sum(1 for e in errs if abs(e) <= 20) / n
    stdz = math.sqrt(sum(z * z for z in zs) / len(zs)) if zs else None
    return dict(n=n, mae=mae, rmse=rmse, bias=bias, w20=w20, stdz=stdz)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test-frac", type=float, default=0.25, help="fracción final del tiempo reservada a TEST")
    ap.add_argument("--max", type=int, default=110, help="máx timestamps por subset (subsamplea)")
    args = ap.parse_args()

    from app import app
    with app.app_context():
        from models import GlucosePrediction
        from pmm.ssm.filter import run_filter, forward_predict
        from pmm.ssm.parameters import params_or_defaults
        from pmm.core.parameter_store import get_isf_now, get_icr_now
        from pmm.engines.drift import get_drift_status
        try:
            from utils.kinetics import dawn_roc_mgdl_min
        except Exception:
            dawn_roc_mgdl_min = lambda at_time=None: 0.0

        base = params_or_defaults(None)

        rows = (GlucosePrediction.query
                .filter(GlucosePrediction.resolved_60 == 1, GlucosePrediction.g_real_60 != None)
                .order_by(GlucosePrediction.predicted_at).all())
        if len(rows) < 80:
            print(f"Pocas predicciones resueltas ({len(rows)})."); return

        split = int(len(rows) * (1 - args.test_frac))
        train, test = rows[:split], rows[split:]
        split_ts = test[0].predicted_at
        print(f"Total resueltas: {len(rows)}")
        print(f"TRAIN: {len(train)}  ({train[0].predicted_at:%m-%d} → {train[-1].predicted_at:%m-%d})")
        print(f"TEST : {len(test)}  ({test[0].predicted_at:%m-%d %H:%M} → {test[-1].predicted_at:%m-%d %H:%M})  [held-out]\n")

        def sub(rs):
            return rs[::max(1, len(rs) // args.max)]
        train_s, test_s = sub(train), sub(test)

        def fc(t, p):
            hora = t.hour
            isf = get_isf_now(hora=hora); icr = get_icr_now(hora=hora)
            drift = get_drift_status().get("drift_factor", 1.0)
            icr_m = icr.get("mu") or 12.0
            try: dawn = float(dawn_roc_mgdl_min(at_time=t) or 0.0)
            except Exception: dawn = 0.0
            res = run_filter(now=t, isf_prior=isf.get("mu"), isf_sigma=isf.get("sigma"),
                             drift_factor=drift, icr_for_meals=icr_m, params=p)
            if res.error or res.n_cgm_used < 3:
                return None
            return forward_predict(res, horizons_min=(30, 60), drift_factor=drift,
                                   icr_for_meals=icr_m, dawn_rate=dawn, params=p)

        def evaluate(rs, p):
            e30, e60 = [], []
            for r in rs:
                try: pr = fc(r.predicted_at, p)
                except Exception: pr = None
                if not pr: continue
                if r.g_real_30 is not None:
                    e30.append(r.g_real_30 - pr[30].g_pred)
                if r.g_real_60 is not None:
                    e60.append((r.g_real_60 - pr[60].g_pred, pr[60].sigma))
            return _agg(e30), _agg(e60, horizon_with_sigma=True)

        # R por escala relativa al valor VIEJO (4.0 / 0.09)
        OLD = replace(base, R_CGM_BASE=4.0, R_CGM_MARD=0.09)
        def R(scale):
            return replace(base, R_CGM_BASE=4.0 * scale, R_CGM_MARD=max(0.002, 0.09 * scale))

        # 1) Elegir R SOLO en train
        print("── Paso 1: barrido de R en TRAIN (elige por MAE +60) ──")
        best_scale, best_mae = None, 1e9
        for scale in (1.0, 0.5, 0.3, 0.2):
            _, m60 = evaluate(train_s, R(scale))
            tag = ""
            if m60 and m60["mae"] < best_mae:
                best_mae, best_scale = m60["mae"], scale; tag = " ←"
            print(f"   R×{scale:<4}  train MAE+60 = {m60['mae']:.1f}{tag}")
        print(f"   → R elegido en train: ×{best_scale}\n")

        # 2) Evaluar en TEST (held-out): R viejo vs R re-derivado en train
        print("── Paso 2: evaluación HELD-OUT (test que el tuneo no vio) ──")
        print(f"{'config':22s} {'set':6s} {'h':>3s} {'n':>4s} {'MAE':>6s} {'RMSE':>6s} {'sesgo':>6s} {'±20':>5s} {'std(z)':>6s}")
        for name, p in (("R viejo (×1.0)", OLD), (f"R re-derivado (×{best_scale})", R(best_scale))):
            for setname, rs in (("train", train_s), ("test", test_s)):
                m30, m60 = evaluate(rs, p)
                for h, m in ((30, m30), (60, m60)):
                    if not m: continue
                    sz = f"{m['stdz']:.2f}" if m.get("stdz") else "—"
                    print(f"{name:22s} {setname:6s} {h:>3d} {m['n']:>4d} {m['mae']:>6.1f} {m['rmse']:>6.1f} {m['bias']:>+6.1f} {m['w20']:>4.0f}% {sz:>6s}")
            print()


if __name__ == "__main__":
    main()
