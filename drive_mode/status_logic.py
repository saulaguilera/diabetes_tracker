"""
drive_mode/status_logic.py
───────────────────────────
Lógica DETERMINISTA del estado de Drive Mode. Función pura, testeable, sin DB.

Principios:
- Solo usa: glucosa actual + tendencia + frescura + conexión. NUNCA predicción.
- Safety-first: a menor glucosa, mensaje más urgente (monótono).
- Mensajes cortos, NO prescriptivos (nunca "comé X" ni "inyectá").
- Si los datos no son confiables (sin sensor / viejos) → no se afirma seguridad.

Umbrales (documentados, simples a propósito):
    urgent_low : glucosa < 70
    low/attn   : 70–85          → "Check when safe"
    attention  : 85–100 cayendo rápido → "Check when safe"
    stable     : 85–180 (tendencia normal)
    high       : > 180
    urgent_high: > 250 (mensajería NO alarmante)
    stale      : última lectura > 15 min
    disconnected: sin sensor / lectura > 45 min

Tendencia (mg/dL/min): |r|<1 plano · 1–2 lento · >2 rápido.
"""
from __future__ import annotations

from typing import Optional

from drive_mode.state import TrendDirection, Status, StatusLevel

# ── Umbrales ──────────────────────────────────────────────────────────────
URGENT_LOW   = 70
LOW          = 85
LOW_WATCH    = 100     # cayendo rápido por debajo de esto = atención
HIGH         = 180
URGENT_HIGH  = 250
STALE_MIN    = 15
DISCONNECT_MIN = 45

TREND_FLAT_MAX = 1.0   # |rate| < 1.0 → plano
TREND_FAST_MIN = 2.0   # |rate| ≥ 2.0 → rápido

# ── Mensajes de seguridad (cortos, no prescriptivos) ──────────────────────
MESSAGES = {
    Status.STABLE:       "Stable",
    Status.ATTENTION:    "Check when safe",
    Status.LOW:          "Check when safe",
    Status.HIGH:         "Glucose high",
    Status.URGENT_LOW:   "Low glucose — stop when safe",
    Status.URGENT_HIGH:  "Glucose high",
    Status.STALE:        "Data stale",
    Status.DISCONNECTED: "Sensor disconnected",
}

_LEVEL = {
    Status.STABLE:       StatusLevel.NORMAL,
    Status.ATTENTION:    StatusLevel.CAUTION,
    Status.LOW:          StatusLevel.CAUTION,
    Status.HIGH:         StatusLevel.CAUTION,
    Status.URGENT_LOW:   StatusLevel.URGENT,
    Status.URGENT_HIGH:  StatusLevel.URGENT,
    Status.STALE:        StatusLevel.UNAVAILABLE,
    Status.DISCONNECTED: StatusLevel.UNAVAILABLE,
}


def classify_trend(rate: Optional[float]) -> str:
    """rate en mg/dL/min → TrendDirection. None → unknown."""
    if rate is None:
        return TrendDirection.UNKNOWN
    if abs(rate) < TREND_FLAT_MAX:
        return TrendDirection.FLAT
    fast = abs(rate) >= TREND_FAST_MIN
    if rate > 0:
        return TrendDirection.RISING_FAST if fast else TrendDirection.RISING_SLOWLY
    return TrendDirection.FALLING_FAST if fast else TrendDirection.FALLING_SLOWLY


def classify_status(
    glucose: Optional[float],
    trend_direction: str,
    minutes_since_update: Optional[int],
    sensor_connected: bool,
) -> dict:
    """
    Devuelve {status, status_level, safety_message} de forma determinista.

    Orden de prioridad (seguridad primero): conexión/frescura → bajo → alto → estable.
    Si los datos no son confiables, NO se afirma 'estable'.
    """
    # 1) Datos no confiables — nunca afirmar seguridad sobre ellos.
    if not sensor_connected or glucose is None:
        s = Status.DISCONNECTED
    elif minutes_since_update is not None and minutes_since_update > DISCONNECT_MIN:
        s = Status.DISCONNECTED
    elif minutes_since_update is not None and minutes_since_update > STALE_MIN:
        s = Status.STALE
    # 2) Bajo (lo más crítico al conducir).
    elif glucose < URGENT_LOW:
        s = Status.URGENT_LOW
    elif glucose < LOW:
        s = Status.LOW
    elif glucose < LOW_WATCH and trend_direction == TrendDirection.FALLING_FAST:
        s = Status.ATTENTION
    # 3) Alto.
    elif glucose > URGENT_HIGH:
        s = Status.URGENT_HIGH
    elif glucose > HIGH:
        s = Status.HIGH
    # 4) Estable.
    else:
        s = Status.STABLE

    return {
        "status": s,
        "status_level": _LEVEL[s],
        "safety_message": MESSAGES[s],
    }
