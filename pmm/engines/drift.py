"""
pmm/engines/drift.py
─────────────────────
CUSUM (Cumulative Sum Control Chart) para detección de drift fisiológico.

Detecta cuándo el metabolismo del usuario cambió de forma sostenida:
  - CUSUM_pos > h  →  resistencia aumentada (enfermedad, estrés, ciclo hormonal)
  - CUSUM_neg < -h →  sensibilidad aumentada (post-ejercicio, pérdida de peso)

El residual que alimenta el CUSUM es:
    residual_t = G_obs(t) - G_pred(t)

tomado de la tabla glucose_predictions (predicciones resueltas).

Algoritmo CUSUM (Page 1954, variante two-sided):
    CUSUM_pos(t) = max(0, CUSUM_pos(t-1) + residual_t - k)
    CUSUM_neg(t) = min(0, CUSUM_neg(t-1) + residual_t + k)

donde:
    k = σ_ref / 2    (allowance — detecta shifts > 1σ en expectation)
    h = 5 × σ_ref    (threshold — alarma cuando el shift acumulado es "real")

σ_ref se estima de los residuales históricos del usuario.
Se actualiza adaptativamente con una EWMA de la varianza.
"""
from __future__ import annotations

import math
import logging
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger("pmm.drift")

# ── Parámetros CUSUM ──────────────────────────────────────────────────────────
_CUSUM_K_FACTOR  = 0.5    # k = σ_ref × K_FACTOR
_CUSUM_H_FACTOR  = 5.0    # h = σ_ref × H_FACTOR
_SIGMA_EWMA_ALPHA = 0.05  # suavizado exponencial para σ_ref (ventana ≈ 20 obs)
_DEFAULT_SIGMA   = 20.0   # σ inicial asumido si no hay datos
_MIN_RESIDUALS   = 20     # mínimo de residuales para activar el detector

# Mensaje narrativo del estado de drift
_NARRATIVA = {
    "resistance": (
        "Tu glucosa responde menos a la insulina de lo habitual. "
        "Causas posibles: estrés, enfermedad, ciclo hormonal, falta de sueño."
    ),
    "sensitivity": (
        "Tu glucosa responde más a la insulina de lo habitual. "
        "Causas posibles: actividad física reciente, pérdida de peso, ayuno."
    ),
    "normal": (
        "Tu metabolismo está dentro del rango habitual. "
        "No se detectaron cambios sostenidos."
    ),
}


# ── Modelo DB ─────────────────────────────────────────────────────────────────

def _get_or_create_state() -> "PMMDriftState":
    """Carga o inicializa el estado CUSUM desde la DB."""
    from models import PMMDriftState, db
    state = PMMDriftState.query.first()
    if not state:
        state = PMMDriftState(
            cusum_pos    = 0.0,
            cusum_neg    = 0.0,
            sigma_ref    = _DEFAULT_SIGMA,
            drift_active = False,
            drift_dir    = None,
            drift_factor = 1.0,
        )
        db.session.add(state)
        db.session.commit()
    return state


def _save_state(state) -> None:
    from models import db
    state.updated_at = datetime.utcnow()
    db.session.commit()


# ── Funciones principales ──────────────────────────────────────────────────────

def update_cusum(residuals: Optional[list[float]] = None) -> dict:
    """
    Actualiza el estado CUSUM con los nuevos residuales disponibles.

    Si `residuals` es None, los carga automáticamente desde glucose_predictions
    (solo los resueltos que aún no fueron procesados).

    Retorna el estado actual del detector de drift.
    """
    from models import PMMDriftState, db

    state = _get_or_create_state()

    if residuals is None:
        residuals = _load_new_residuals(state)

    if not residuals:
        return _state_to_dict(state)

    k = state.sigma_ref * _CUSUM_K_FACTOR
    h = state.sigma_ref * _CUSUM_H_FACTOR

    was_active = state.drift_active
    prev_dir   = state.drift_dir

    for r in residuals:
        # ── Actualizar σ_ref con EWMA (varianza rolling) ──────────────────
        state.sigma_ref = math.sqrt(
            (1 - _SIGMA_EWMA_ALPHA) * state.sigma_ref ** 2 +
            _SIGMA_EWMA_ALPHA       * r ** 2
        )
        k = state.sigma_ref * _CUSUM_K_FACTOR
        h = state.sigma_ref * _CUSUM_H_FACTOR

        # ── Update CUSUM ───────────────────────────────────────────────────
        state.cusum_pos = max(0.0, state.cusum_pos + r - k)
        state.cusum_neg = min(0.0, state.cusum_neg + r + k)

    # ── Evaluar alarma ─────────────────────────────────────────────────────
    if state.cusum_pos > h:
        state.drift_active = True
        state.drift_dir    = "resistance"
        # Factor de drift: cuánto supera el umbral (normalizado)
        state.drift_factor = round(1.0 + (state.cusum_pos - h) / (h * 2), 3)
        if not was_active or prev_dir != "resistance":
            state.drift_since = datetime.utcnow()
            logger.warning(
                f"PMM DRIFT: resistencia detectada | CUSUM_pos={state.cusum_pos:.1f} "
                f"h={h:.1f} σ={state.sigma_ref:.1f}"
            )
    elif state.cusum_neg < -h:
        state.drift_active = True
        state.drift_dir    = "sensitivity"
        state.drift_factor = round(1.0 - (-state.cusum_neg - h) / (h * 2), 3)
        state.drift_factor = max(0.5, state.drift_factor)
        if not was_active or prev_dir != "sensitivity":
            state.drift_since = datetime.utcnow()
            logger.warning(
                f"PMM DRIFT: sensibilidad detectada | CUSUM_neg={state.cusum_neg:.1f} "
                f"h={h:.1f} σ={state.sigma_ref:.1f}"
            )
    else:
        # Sin alarma — reset si estaba activo
        if was_active:
            logger.info("PMM DRIFT: resuelto — volviendo a baseline")
        state.drift_active = False
        state.drift_dir    = None
        state.drift_factor = 1.0
        state.drift_since  = None

    _save_state(state)
    return _state_to_dict(state)


def get_drift_status() -> dict:
    """
    Retorna el estado actual del detector sin actualizar.
    Usado por los endpoints de API.
    """
    try:
        state = _get_or_create_state()
        return _state_to_dict(state)
    except Exception as exc:
        return {"ok": False, "error": str(exc), "drift_active": False}


def reset_cusum() -> None:
    """
    Reset manual del CUSUM (ej. después de un cambio de insulina o pauta).
    """
    from models import db
    state = _get_or_create_state()
    state.cusum_pos    = 0.0
    state.cusum_neg    = 0.0
    state.drift_active = False
    state.drift_dir    = None
    state.drift_factor = 1.0
    state.drift_since  = None
    _save_state(state)
    logger.info("PMM DRIFT: CUSUM reseteado manualmente")


# ── Helpers internos ───────────────────────────────────────────────────────────

def _load_new_residuals(state) -> list[float]:
    """
    Carga los residuales de predicción resueltos que aún no fueron
    incorporados al CUSUM.

    Usa error_30 (predicción a 30min) como señal principal: es más
    sensible a cambios agudos que error_60 y tiene menor varianza total.
    """
    from models import GlucosePrediction

    # Cargar predicciones resueltas desde la última actualización del CUSUM
    cutoff = state.updated_at or (datetime.utcnow() - timedelta(days=30))

    preds = (
        GlucosePrediction.query
        .filter(
            GlucosePrediction.resolved_30 == True,
            GlucosePrediction.error_30.isnot(None),
            GlucosePrediction.created_at > cutoff,
        )
        .order_by(GlucosePrediction.predicted_at)
        .limit(500)
        .all()
    )

    residuals = [p.error_30 for p in preds if p.error_30 is not None]
    return residuals


def _state_to_dict(state) -> dict:
    from datetime import datetime as dt

    k = state.sigma_ref * _CUSUM_K_FACTOR
    h = state.sigma_ref * _CUSUM_H_FACTOR

    # Intensidad del drift: cuánto excede el umbral (0-1)
    if state.drift_dir == "resistance":
        intensity = min(1.0, max(0.0, (state.cusum_pos - h) / h))
    elif state.drift_dir == "sensitivity":
        intensity = min(1.0, max(0.0, (-state.cusum_neg - h) / h))
    else:
        intensity = 0.0

    # Duración del drift
    drift_hours = None
    if state.drift_active and state.drift_since:
        drift_hours = round(
            (dt.utcnow() - state.drift_since).total_seconds() / 3600, 1
        )

    return {
        "ok":            True,
        "drift_active":  state.drift_active,
        "drift_dir":     state.drift_dir,
        "drift_factor":  state.drift_factor,
        "drift_since":   state.drift_since.isoformat() if state.drift_since else None,
        "drift_hours":   drift_hours,
        "intensity":     round(intensity, 3),
        "cusum_pos":     round(state.cusum_pos, 2),
        "cusum_neg":     round(state.cusum_neg, 2),
        "sigma_ref":     round(state.sigma_ref, 2),
        "threshold_h":   round(h, 2),
        "narrativa":     _NARRATIVA.get(state.drift_dir or "normal"),
        "updated_at":    state.updated_at.isoformat() if state.updated_at else None,
    }
