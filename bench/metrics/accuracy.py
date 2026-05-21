"""
bench/metrics/accuracy.py
──────────────────────────
Métricas de exactitud puntual: MAE, RMSE, MARD, bias.

Todas las funciones son puras y operan sobre listas de PredictionRecord.
Devuelven dicts JSON-serializables.
"""
from __future__ import annotations

import math
from typing import Iterable

from bench.replay import PredictionRecord


def _stats(values: list[float]) -> dict:
    """Stats básicos para reportar p25/p50/p75/p95."""
    if not values:
        return {"n": 0}
    v = sorted(values)
    n = len(v)
    def q(p):
        idx = max(0, min(n - 1, int(round(p * (n - 1)))))
        return v[idx]
    return {
        "n":      n,
        "mean":   round(sum(v) / n, 2),
        "p25":    round(q(0.25), 2),
        "p50":    round(q(0.50), 2),
        "p75":    round(q(0.75), 2),
        "p95":    round(q(0.95), 2),
        "max":    round(v[-1], 2),
    }


def mae(records: Iterable[PredictionRecord]) -> float:
    """Mean Absolute Error (mg/dL)."""
    errors = [r.abs_error for r in records]
    if not errors:
        return float("nan")
    return sum(errors) / len(errors)


def rmse(records: Iterable[PredictionRecord]) -> float:
    """Root Mean Squared Error (mg/dL). Penaliza más los outliers."""
    sq = [r.abs_error ** 2 for r in records]
    if not sq:
        return float("nan")
    return math.sqrt(sum(sq) / len(sq))


def mard(records: Iterable[PredictionRecord]) -> float:
    """
    Mean Absolute Relative Difference (%).
    Métrica estándar de la industria CGM (Abbott Libre = ~9% MARD).
    """
    rel = [r.relative_error_pct for r in records]
    if not rel:
        return float("nan")
    return sum(rel) / len(rel)


def bias(records: Iterable[PredictionRecord]) -> float:
    """
    Error medio con signo (mg/dL).
    Positivo → modelo subestima sistemáticamente (real > predicho).
    Negativo → modelo sobreestima.
    """
    errors = [r.error for r in records]
    if not errors:
        return float("nan")
    return sum(errors) / len(errors)


def accuracy_summary(records: list[PredictionRecord]) -> dict:
    """
    Resumen completo para un slice de predicciones.
    Devuelve un dict listo para serializar a JSON/HTML.
    """
    if not records:
        return {"n": 0, "mae": None, "rmse": None, "mard": None, "bias": None}

    abs_errs = [r.abs_error for r in records]
    rel_errs = [r.relative_error_pct for r in records]
    signed   = [r.error for r in records]

    return {
        "n":              len(records),
        "mae":            round(mae(records),  2),
        "rmse":           round(rmse(records), 2),
        "mard":           round(mard(records), 2),
        "bias":           round(bias(records), 2),
        "abs_error_dist": _stats(abs_errs),
        "rel_error_dist": _stats(rel_errs),
        "signed_error_dist": _stats(signed),
    }


def accuracy_by_horizon(records: list[PredictionRecord]) -> dict:
    """Desglose MAE/RMSE/MARD por horizonte (30 y 60 min)."""
    from bench.replay import slice_by_horizon
    return {
        f"+{h}min": accuracy_summary(slice_by_horizon(records, h))
        for h in (30, 60)
    }


def accuracy_by_context(records: list[PredictionRecord]) -> dict:
    """Desglose por contexto (post_meal, iob_active, dawn, etc.)."""
    from bench.replay import slice_by_context
    tags = sorted({r.context_tag for r in records})
    return {
        tag: accuracy_summary(slice_by_context(records, tag))
        for tag in tags
    }


def accuracy_by_glucose_range(records: list[PredictionRecord]) -> dict:
    """
    Desglose por rango glucémico real.
    Crítico para detectar si el modelo es preciso en TIR pero impreciso
    en hipo/hiper (donde más importa).
    """
    from bench.replay import slice_by_glucose_range
    return {
        "hypo_<70":        accuracy_summary(slice_by_glucose_range(records,   0,  70)),
        "low_70_100":      accuracy_summary(slice_by_glucose_range(records,  70, 100)),
        "tir_100_180":     accuracy_summary(slice_by_glucose_range(records, 100, 180)),
        "high_180_250":    accuracy_summary(slice_by_glucose_range(records, 180, 250)),
        "hyper_>=250":     accuracy_summary(slice_by_glucose_range(records, 250, 500)),
    }
