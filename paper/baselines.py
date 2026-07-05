"""
paper/baselines.py
──────────────────
Baselines para el paper: compara el SSM contra predictores triviales SOBRE LOS
MISMOS puntos (mismos predicted_at, mismos realized), desde los audits resueltos.

Baselines:
  naive (zero-order hold): G(t+h) = G(t)  — el punto de referencia mínimo que
      todo predictor debe superar. Se computa retrospectivamente con la lectura
      CGM más cercana a predicted_at (±10 min).
  (el AR y MC/GP tienen sus propios buckets de audits — se reportan aparte
   porque cubren ventanas temporales distintas)

Uso:  python3 paper/baselines.py [dias]
"""
from __future__ import annotations

import sys
from bisect import bisect_left
from datetime import datetime, timedelta
from statistics import mean, stdev

sys.path.insert(0, ".")

VERSIONS = [
    "ssm_v0_ukf6_basal_ex_r1",
    "ssm_v0_ukf6_basal_ex_r2_gated_bias",
    "ssm_v0_ukf6_basal_ex_r3_cal60",
    "ssm_v0_ukf6_basal_ex_r4_cal3060",
]


def _nearest(times, values, t, tol_min=10):
    i = bisect_left(times, t)
    best, bd = None, None
    for j in (i - 1, i):
        if 0 <= j < len(times):
            d = abs((times[j] - t).total_seconds()) / 60
            if d <= tol_min and (bd is None or d < bd):
                best, bd = values[j], d
    return best


def run(days: int = 60):
    from app import app
    from models import GlucoseReading, PredictionAudit

    with app.app_context():
        since = datetime.now() - timedelta(days=days)
        reads = (GlucoseReading.query
                 .filter(GlucoseReading.timestamp >= since,
                         GlucoseReading.is_artifact == False)  # noqa: E712
                 .order_by(GlucoseReading.timestamp).all())
        times = [r.timestamp for r in reads]
        values = [r.value_mgdl for r in reads]

        print(f"{'versión':38s} {'h':>4s} {'n':>5s} {'MAE ssm':>8s} {'MAE naive':>9s} "
              f"{'mejora':>7s} {'bias ssm':>9s}")
        for ver in VERSIONS:
            for h in (30, 60):
                rows = (PredictionAudit.query
                        .filter(PredictionAudit.model_version == ver,
                                PredictionAudit.resolved == True,  # noqa: E712
                                PredictionAudit.horizon_min == h,
                                PredictionAudit.predicted_at >= since).all())
                pares = []
                for r in rows:
                    g0 = _nearest(times, values, r.predicted_at)
                    if g0 is not None and r.innovation is not None:
                        pares.append((abs(r.innovation),               # |err| ssm
                                      abs(r.realized_glucose - g0),    # |err| naive
                                      r.innovation))
                if len(pares) < 30:
                    continue
                mae_s = mean(p[0] for p in pares)
                mae_n = mean(p[1] for p in pares)
                print(f"{ver:38s} {h:>4d} {len(pares):>5d} {mae_s:>8.1f} {mae_n:>9.1f} "
                      f"{100*(mae_n-mae_s)/mae_n:>6.0f}% {mean(p[2] for p in pares):>+9.1f}")


if __name__ == "__main__":
    run(int(sys.argv[1]) if len(sys.argv) > 1 else 60)
