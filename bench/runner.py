"""
bench/runner.py
────────────────
Orquestador: carga records, corre todas las métricas, devuelve reporte
consolidado.

Diseño
------
- Función `run_backtest()` es la API pública. Devuelve un dict que es la
  fuente de verdad — todos los renders (CLI, HTML, JSON, API) consumen lo
  mismo.
- Sin side effects fuera de logging.
- Sin acceso directo a la DB excepto vía `replay.load_resolved()`.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from bench.replay import (
    load_resolved, available_model_versions,
    slice_by_horizon, slice_by_model_version,
)
from bench.metrics.accuracy    import (
    accuracy_summary, accuracy_by_horizon,
    accuracy_by_context, accuracy_by_glucose_range,
)
from bench.metrics.calibration import calibration_summary
from bench.metrics.clinical    import clinical_summary


def run_backtest(
    days:          int = 30,
    model_version: Optional[str] = None,
) -> dict:
    """
    Corre el backtest completo y devuelve dict-reporte.

    Returns
    -------
    {
        "meta": {window_days, run_at, n_records, models_compared},
        "by_model": {
            "<model_version>": {
                "accuracy": {...},
                "calibration": {...},
                "clinical": {...},
            },
            ...
        }
    }
    """
    records = load_resolved(days=days)

    versions = ([model_version]
                if model_version
                else (available_model_versions(records) or ["unknown"]))

    by_model: dict[str, dict] = {}
    for v in versions:
        recs_v = slice_by_model_version(records, v) if v != "unknown" else records
        if not recs_v:
            by_model[v] = {"note": "sin datos para esta versión"}
            continue

        by_model[v] = {
            "n_records":             len(recs_v),
            "accuracy":              accuracy_summary(recs_v),
            "accuracy_by_horizon":   accuracy_by_horizon(recs_v),
            "accuracy_by_context":   accuracy_by_context(recs_v),
            "accuracy_by_glucose":   accuracy_by_glucose_range(recs_v),
            "calibration_+30min":    calibration_summary(slice_by_horizon(recs_v, 30)),
            "calibration_+60min":    calibration_summary(slice_by_horizon(recs_v, 60)),
            "clinical":              clinical_summary(recs_v),
        }

    return {
        "meta": {
            "window_days":      days,
            "run_at":           datetime.now().isoformat(timespec="seconds"),
            "n_records_total":  len(records),
            "models_compared":  versions,
            "horizons":         [30, 60],
        },
        "by_model": by_model,
    }


# ── Verdict (pasa/no pasa thresholds objetivo) ──────────────────────────

# Thresholds objetivo del documento de arquitectura.
_TARGETS = {
    "mae_30":             15.0,
    "mae_60":             25.0,
    "ece":                 0.05,
    "hypo_recall_30":      0.85,
    "false_alarms_per_day": 1.0,
}


def verdict(report: dict, model: Optional[str] = None) -> dict:
    """
    Compara el reporte contra los thresholds del blueprint y devuelve
    pass/warn/fail por métrica.
    """
    if model is None:
        models = list(report["by_model"].keys())
        model  = models[0] if models else None
    if not model or model not in report["by_model"]:
        return {"ok": False, "error": "no hay modelo para evaluar"}

    m = report["by_model"][model]
    if "accuracy" not in m:
        return {"ok": False, "error": "modelo sin datos suficientes"}

    def _check(value, target, lower_is_better=True):
        if value is None: return ("missing", None)
        if lower_is_better:
            if value <= target:           return ("pass", value)
            if value <= target * 1.5:     return ("warn", value)
            return ("fail", value)
        else:
            if value >= target:           return ("pass", value)
            if value >= target * 0.7:     return ("warn", value)
            return ("fail", value)

    mae_30 = m["accuracy_by_horizon"].get("+30min", {}).get("mae")
    mae_60 = m["accuracy_by_horizon"].get("+60min", {}).get("mae")
    ece_30 = m["calibration_+30min"].get("ece")
    hypo_r = m["clinical"].get("hypo_+30min", {}).get("recall")
    fa_day = m["clinical"].get("hypo_+30min", {}).get("false_alarm_rate_per_day")

    checks = {
        "mae_30":               _check(mae_30, _TARGETS["mae_30"]),
        "mae_60":               _check(mae_60, _TARGETS["mae_60"]),
        "ece":                  _check(ece_30, _TARGETS["ece"]),
        "hypo_recall_30":       _check(hypo_r, _TARGETS["hypo_recall_30"], lower_is_better=False),
        "false_alarms_per_day": _check(fa_day, _TARGETS["false_alarms_per_day"]),
    }

    statuses = [s for s, _ in checks.values() if s != "missing"]
    overall = (
        "fail" if "fail" in statuses else
        "warn" if "warn" in statuses else
        "pass" if statuses else
        "no_data"
    )
    return {
        "model":   model,
        "overall": overall,
        "targets": _TARGETS,
        "checks":  {k: {"status": s, "value": v} for k, (s, v) in checks.items()},
    }
