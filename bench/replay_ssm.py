"""
bench/replay_ssm.py
────────────────────
Replay determinístico del SSM sobre histórico.

Para qué sirve
--------------
El bench/replay.py actual evalúa lo que el modelo predijo "en vivo" en su
momento — útil pero limitado:
  - No podemos testear cambios al modelo sin esperar 7 días de nueva data
  - No podemos hacer A/B testing de versiones
  - No podemos hacer regression testing en CI

Este replay engine:
  1. Toma una ventana histórica (e.g. últimos 30 días)
  2. Identifica cada "punto de decisión" (cuando hubo CGM nuevo)
  3. Re-corre el SSM EN CADA PUNTO con la config actual o una variante
  4. Compara la predicción re-generada contra el ground truth real
  5. Devuelve métricas estilo bench/

Determinístico
--------------
Misma entrada + misma config → mismo output. Permite:
  - Comparar versiones del SSM (cambiar Q, K_PI, R_cgm, etc.)
  - Comparar SSM vs MC vs GP sobre el mismo dataset histórico
  - Regression testing: cualquier cambio que empeore métricas → bloquear

Uso
---
    python -m bench.replay_ssm --days 14
    python -m bench.replay_ssm --days 14 --decision-every-min 30
    python -m bench.replay_ssm --days 30 --config '{"sigma_q_g": 10}'
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from bench.replay import PredictionRecord


@dataclass
class ReplayConfig:
    """Configuración versionable del SSM para replay."""
    isf_prior:        float = 45.0
    isf_sigma:        float = 12.0
    drift_factor:     float = 1.0
    icr_for_meals:    float = 12.0
    lookback_hours:   int   = 6
    horizons_min:     tuple = (30, 60)

    def fingerprint(self) -> str:
        """Hash determinístico para identificar config en logs."""
        import hashlib
        return hashlib.md5(json.dumps(self.__dict__, sort_keys=True,
                                       default=str).encode()).hexdigest()[:8]


# ─── Replay sobre histórico ─────────────────────────────────────────────

def replay_window(
    days:                int = 14,
    decision_every_min:  int = 15,
    config:              Optional[ReplayConfig] = None,
    model_label:         str = "ssm_replay",
) -> list[PredictionRecord]:
    """
    Re-corre el SSM en cada punto de decisión y construye PredictionRecords
    comparables con bench.metrics.*.

    Parameters
    ----------
    days : ventana histórica
    decision_every_min : cada cuántos minutos hacer una "predicción"
    config : ReplayConfig (sino default)
    model_label : tag para identificar la corrida (no se persiste a DB)
    """
    if config is None:
        config = ReplayConfig()

    from models import GlucoseReading
    from pmm.ssm.filter import run_filter, forward_predict

    now    = datetime.now()
    cutoff = now - timedelta(days=days)

    # Determinar todos los puntos de decisión: cada `decision_every_min`
    # desde cutoff hasta `now - max_horizon` (para poder evaluar).
    max_horizon = max(config.horizons_min)
    end = now - timedelta(minutes=max_horizon)

    decision_times = []
    t = cutoff
    while t <= end:
        decision_times.append(t)
        t += timedelta(minutes=decision_every_min)

    # Pre-cargar ground truth completo (CGM) para resolución rápida
    cgm = (GlucoseReading.query
           .filter(GlucoseReading.timestamp >= cutoff,
                   GlucoseReading.timestamp <= now)
           .order_by(GlucoseReading.timestamp)
           .all())

    records: list[PredictionRecord] = []
    for dt in decision_times:
        # Re-correr SSM AS-IF AT dt
        try:
            result = run_filter(
                now           = dt,
                hours         = config.lookback_hours,
                isf_prior     = config.isf_prior,
                isf_sigma     = config.isf_sigma,
                drift_factor  = config.drift_factor,
                icr_for_meals = config.icr_for_meals,
            )
            if result.error or result.n_cgm_used < 3:
                continue
            preds = forward_predict(
                result, horizons_min=config.horizons_min,
                drift_factor=config.drift_factor,
                icr_for_meals=config.icr_for_meals,
            )
        except Exception:
            continue

        # Glucose actual al momento de predecir (la más cercana en ±5min)
        g_actual = _nearest_cgm(cgm, dt, tol_min=10)
        if g_actual is None:
            continue

        for h in config.horizons_min:
            g_real = _nearest_cgm(cgm, dt + timedelta(minutes=h), tol_min=7)
            if g_real is None:
                continue
            p = preds[h]
            records.append(PredictionRecord(
                predicted_at  = dt,
                horizon_min   = h,
                g_actual      = float(g_actual),
                g_pred        = float(p.g_pred),
                g_real        = float(g_real),
                sigma         = float(p.sigma),
                model_version = model_label,
            ))

    return records


def _nearest_cgm(cgm_list, t: datetime, tol_min: float = 5):
    """Lectura CGM más cercana a t dentro de ±tol_min."""
    best, best_dt = None, float("inf")
    for r in cgm_list:
        dt = abs((r.timestamp - t).total_seconds() / 60.0)
        if dt < best_dt and dt <= tol_min:
            best, best_dt = r, dt
    return best.value_mgdl if best else None


# ─── A/B benchmark de dos configs ───────────────────────────────────────

def compare_configs(
    days: int = 14,
    config_a: Optional[ReplayConfig] = None,
    config_b: Optional[ReplayConfig] = None,
    decision_every_min: int = 15,
) -> dict:
    """
    Corre el replay con dos configs y compara métricas head-to-head.

    Returns
    -------
    {
        "config_a": {fingerprint, accuracy, calibration, n_records},
        "config_b": idem,
        "diff": {mae_30_pct_change, ece_pct_change, ...},
        "winner": "a" | "b" | "tie",
    }
    """
    from bench.metrics.accuracy    import accuracy_summary, accuracy_by_horizon
    from bench.metrics.calibration import calibration_summary

    a = config_a or ReplayConfig()
    b = config_b or ReplayConfig()
    recs_a = replay_window(days=days, decision_every_min=decision_every_min,
                            config=a, model_label="config_a")
    recs_b = replay_window(days=days, decision_every_min=decision_every_min,
                            config=b, model_label="config_b")

    sum_a = {
        "fingerprint": a.fingerprint(), "n": len(recs_a),
        "accuracy":    accuracy_summary(recs_a),
        "by_horizon":  accuracy_by_horizon(recs_a),
        "calibration": calibration_summary(recs_a),
    }
    sum_b = {
        "fingerprint": b.fingerprint(), "n": len(recs_b),
        "accuracy":    accuracy_summary(recs_b),
        "by_horizon":  accuracy_by_horizon(recs_b),
        "calibration": calibration_summary(recs_b),
    }

    # Diff de MAE y ECE (las dos métricas clave)
    mae_a = sum_a["accuracy"].get("mae"); mae_b = sum_b["accuracy"].get("mae")
    ece_a = sum_a["calibration"].get("ece"); ece_b = sum_b["calibration"].get("ece")

    diff = {
        "mae_a": mae_a, "mae_b": mae_b,
        "ece_a": ece_a, "ece_b": ece_b,
    }
    if mae_a and mae_b:
        diff["mae_pct_change"] = round((mae_b - mae_a) / mae_a * 100, 2)
    if ece_a and ece_b:
        diff["ece_pct_change"] = round((ece_b - ece_a) / ece_a * 100, 2)

    # Winner heurístico: ambos en favor de uno → declarar
    if mae_a and mae_b and ece_a and ece_b:
        a_better_mae = mae_a < mae_b
        a_better_ece = ece_a < ece_b
        if a_better_mae and a_better_ece:  winner = "a"
        elif (not a_better_mae) and (not a_better_ece): winner = "b"
        else: winner = "tie"
    else:
        winner = "no_data"

    return {"config_a": sum_a, "config_b": sum_b, "diff": diff, "winner": winner}


# ─── CLI ────────────────────────────────────────────────────────────────

def _cli():
    import argparse, sys, json as _json
    ap = argparse.ArgumentParser(description="SSM replay engine — deterministic backtest")
    ap.add_argument("--days",  type=int, default=14)
    ap.add_argument("--decision-every-min", type=int, default=15)
    ap.add_argument("--config", type=str, default=None,
                    help="JSON con overrides de ReplayConfig")
    ap.add_argument("--json",  action="store_true")
    args = ap.parse_args()

    cfg = ReplayConfig()
    if args.config:
        for k, v in _json.loads(args.config).items():
            if hasattr(cfg, k):
                setattr(cfg, k, v)

    from app import app
    with app.app_context():
        records = replay_window(days=args.days,
                                 decision_every_min=args.decision_every_min,
                                 config=cfg)
        from bench.metrics.accuracy    import accuracy_summary, accuracy_by_horizon
        from bench.metrics.calibration import calibration_summary

        report = {
            "config":      cfg.__dict__,
            "fingerprint": cfg.fingerprint(),
            "n_records":   len(records),
            "accuracy":    accuracy_summary(records),
            "by_horizon":  accuracy_by_horizon(records),
            "calibration": calibration_summary(records),
        }

        if args.json:
            print(_json.dumps(report, indent=2, default=str))
        else:
            print(f"\n{'═' * 60}")
            print(f"SSM replay — config {cfg.fingerprint()}, "
                  f"{args.days}d window, decision every {args.decision_every_min}min")
            print(f"{'═' * 60}")
            print(f"  n_records:   {report['n_records']}")
            print(f"  MAE global:  {report['accuracy'].get('mae')}")
            print(f"  RMSE global: {report['accuracy'].get('rmse')}")
            for h, hd in report['by_horizon'].items():
                print(f"    {h}: MAE {hd.get('mae')}  RMSE {hd.get('rmse')}  n={hd['n']}")
            cal = report['calibration']
            if cal.get("n_with_sigma", 0):
                print(f"  ECE:         {cal.get('ece')}")
                print(f"  CRPS:        {cal.get('crps')}")
                print(f"  Sharpness:   {cal.get('sharpness')}")


if __name__ == "__main__":
    _cli()
