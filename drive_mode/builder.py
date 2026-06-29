"""
drive_mode/builder.py
──────────────────────
Construye un DriveModeState desde el pipeline de glucosa EXISTENTE de ORBIT.

SOLO usa: última lectura CGM, serie reciente (para la tasa de tendencia),
frescura y sensor. NO importa el SSM/predicción ni nada de Clinic.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from drive_mode.state import DriveModeState, TrendDirection
from drive_mode.status_logic import classify_trend, classify_status

# Ventana para estimar la tasa de tendencia (mg/dL/min) desde la serie reciente.
_RATE_WINDOW_MIN = 20


def _sensor_name() -> str:
    """Nombre del sensor para mostrar. Configurable; default genérico."""
    try:
        from helpers import _get_setting
        return (_get_setting("sensor_name") or "CGM").strip() or "CGM"
    except Exception:
        return "CGM"


def build_drive_mode_state(now: Optional[datetime] = None) -> DriveModeState:
    """
    Estado de Drive Mode al instante `now`. Determinista; sin predicción.

    Resiliente: si no hay datos o falla la DB, devuelve un estado
    'disconnected' seguro (nunca afirma que la glucosa está bien).
    """
    now = now or datetime.now()
    sensor = _sensor_name()

    def _disconnected(msg_age=None):
        from drive_mode.status_logic import classify_status as _cs
        c = _cs(None, TrendDirection.UNKNOWN, msg_age, sensor_connected=False)
        return DriveModeState(
            glucose_value_mgdl=None, trend_direction=TrendDirection.UNKNOWN,
            trend_rate=None, status=c["status"], status_level=c["status_level"],
            last_update_at=None, minutes_since_update=msg_age,
            sensor_name=sensor, sensor_connected=False, stale_data=True,
            safety_message=c["safety_message"],
        )

    try:
        from models import GlucoseReading
        last = (GlucoseReading.query
                .filter((GlucoseReading.is_artifact == False) | (GlucoseReading.is_artifact.is_(None)))
                .order_by(GlucoseReading.timestamp.desc()).first())
    except Exception:
        return _disconnected()

    if not last:
        return _disconnected()

    age_min = int((now - last.timestamp).total_seconds() / 60)
    glucose = float(last.value_mgdl)

    # ── tasa de tendencia desde la serie reciente (mg/dL/min) ──
    rate = None
    try:
        from models import GlucoseReading
        since = now - timedelta(minutes=_RATE_WINDOW_MIN)
        recent = (GlucoseReading.query
                  .filter(GlucoseReading.timestamp >= since,
                          (GlucoseReading.is_artifact == False) | (GlucoseReading.is_artifact.is_(None)))
                  .order_by(GlucoseReading.timestamp).all())
        if len(recent) >= 2:
            dg = recent[-1].value_mgdl - recent[0].value_mgdl
            dt = (recent[-1].timestamp - recent[0].timestamp).total_seconds() / 60.0
            if dt > 0:
                rate = round(dg / dt, 2)
    except Exception:
        rate = None

    trend = classify_trend(rate)
    from drive_mode.status_logic import DISCONNECT_MIN, STALE_MIN
    connected = age_min <= DISCONNECT_MIN
    c = classify_status(glucose, trend, age_min, sensor_connected=connected)

    return DriveModeState(
        glucose_value_mgdl=int(round(glucose)),
        trend_direction=trend,
        trend_rate=rate,
        status=c["status"],
        status_level=c["status_level"],
        last_update_at=last.timestamp.isoformat(),
        minutes_since_update=age_min,
        sensor_name=sensor,
        sensor_connected=connected,
        stale_data=age_min > STALE_MIN,
        safety_message=c["safety_message"],
    )
