"""
bench/replay.py
────────────────
Replay engine: carga predicciones resueltas históricas y arma el dataset
listo para evaluación.

Distinguimos dos modos:

  1. **historical_replay**  — usa GlucosePrediction ya persistidas en la DB.
     Rápido. Mide al modelo "tal como corrió en vivo" en su momento.
     No permite testear cambios al modelo sin re-correrlo.

  2. **shadow_replay**     — re-ejecuta el modelo actual sobre el histórico
     crudo (CGM + meals + doses), generando predicciones nuevas.
     Lento pero necesario para validar cambios sin esperar 30 días de
     datos en vivo.

El MVP implementa (1). (2) lo agregamos cuando tengamos el SSM en shadow
mode y queramos comparar contra el modelo actual.

Output: lista de `PredictionRecord` con todo lo necesario para que las
funciones de métricas no toquen la DB.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Iterable, Optional


@dataclass
class PredictionRecord:
    """
    Snapshot inmutable de una predicción + su resolución.
    Las funciones de métricas operan sobre listas de esto, sin tocar DB.
    """
    predicted_at:  datetime
    horizon_min:   int           # 30 o 60
    g_actual:      float         # glucemia al momento de predecir
    g_pred:        float         # predicción del modelo
    g_real:        float         # glucemia real observada en t+horizon
    sigma:         Optional[float] = None   # σ predictivo (mg/dL)
    # Contexto en el momento de la predicción (para slicing posterior)
    iob:           Optional[float] = None
    cob:           Optional[float] = None
    roc:           Optional[float] = None
    isf_used:      Optional[float] = None
    icr_used:      Optional[float] = None
    ex_factor:     Optional[float] = None
    model_version: Optional[str]   = None

    @property
    def error(self) -> float:
        """Error con signo: real − pred. Positivo = modelo subestimó."""
        return self.g_real - self.g_pred

    @property
    def abs_error(self) -> float:
        return abs(self.error)

    @property
    def relative_error_pct(self) -> float:
        """|err| / g_real × 100. Base para MARD."""
        if self.g_real == 0:
            return 0.0
        return abs(self.error) / self.g_real * 100

    @property
    def hour_of_day(self) -> int:
        return self.predicted_at.hour

    @property
    def context_tag(self) -> str:
        """Tag de contexto principal — útil para slicing."""
        if (self.cob or 0) > 5:        return "post_meal"
        if (self.iob or 0) > 1:        return "iob_active"
        if self.roc is not None and abs(self.roc) > 1.5:
            return "rapid_change"
        if 5 <= self.hour_of_day < 8:  return "dawn"
        return "stable"


# ── Loader desde DB ────────────────────────────────────────────────────────

def load_resolved(
    days:           int = 30,
    horizon_min:    Optional[int] = None,
    model_version:  Optional[str] = None,
    min_g_real:     float = 30.0,
    max_g_real:     float = 500.0,
) -> list[PredictionRecord]:
    """
    Carga predicciones resueltas de los últimos `days` días.

    Filtros:
        horizon_min   : 30, 60 o None (ambos)
        model_version : restringir a una versión específica
        min/max_g_real: descartar lecturas absurdas (artefactos del sensor)

    Returns
    -------
    Lista ordenada cronológicamente por predicted_at.
    Cada predicción resuelta a +30 y +60 produce DOS records (uno por horizon).
    """
    from models import GlucosePrediction

    cutoff = datetime.now() - timedelta(days=days)

    q = (
        GlucosePrediction.query
        .filter(GlucosePrediction.predicted_at >= cutoff)
        .order_by(GlucosePrediction.predicted_at)
    )
    if model_version:
        q = q.filter(GlucosePrediction.model_version == model_version)

    records: list[PredictionRecord] = []

    for p in q.all():
        # Resolución +30
        if (horizon_min in (None, 30) and
            p.resolved_30 and p.g_real_30 is not None and
            p.g_pred_30 is not None and
            min_g_real <= p.g_real_30 <= max_g_real):
            records.append(PredictionRecord(
                predicted_at  = p.predicted_at,
                horizon_min   = 30,
                g_actual      = p.g_actual or 0.0,
                g_pred        = p.g_pred_30,
                g_real        = p.g_real_30,
                sigma         = p.sigma_30,
                iob           = p.iob,
                cob           = p.cob,
                roc           = p.roc,
                isf_used      = p.isf_used,
                icr_used      = p.icr_used,
                ex_factor     = p.ex_factor,
                model_version = p.model_version,
            ))

        # Resolución +60
        if (horizon_min in (None, 60) and
            p.resolved_60 and p.g_real_60 is not None and
            p.g_pred_60 is not None and
            min_g_real <= p.g_real_60 <= max_g_real):
            records.append(PredictionRecord(
                predicted_at  = p.predicted_at,
                horizon_min   = 60,
                g_actual      = p.g_actual or 0.0,
                g_pred        = p.g_pred_60,
                g_real        = p.g_real_60,
                sigma         = p.sigma_60,
                iob           = p.iob,
                cob           = p.cob,
                roc           = p.roc,
                isf_used      = p.isf_used,
                icr_used      = p.icr_used,
                ex_factor     = p.ex_factor,
                model_version = p.model_version,
            ))

    return records


# ── Helpers de slicing ─────────────────────────────────────────────────────

def slice_by_horizon(records: Iterable[PredictionRecord], horizon: int) -> list[PredictionRecord]:
    return [r for r in records if r.horizon_min == horizon]


def slice_by_context(records: Iterable[PredictionRecord], tag: str) -> list[PredictionRecord]:
    return [r for r in records if r.context_tag == tag]


def slice_by_glucose_range(
    records: Iterable[PredictionRecord], lo: float, hi: float
) -> list[PredictionRecord]:
    """Filtrar por rango de la glucosa REAL — útil para hypo-zone analysis."""
    return [r for r in records if lo <= r.g_real <= hi]


def slice_by_model_version(records: Iterable[PredictionRecord], version: str) -> list[PredictionRecord]:
    return [r for r in records if r.model_version == version]


def available_model_versions(records: Iterable[PredictionRecord]) -> list[str]:
    return sorted({r.model_version or "unknown" for r in records})
