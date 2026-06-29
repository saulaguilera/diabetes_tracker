"""
drive_mode/state.py
────────────────────
DriveModeState — el objeto de datos simplificado que consume ORBIT Drive Mode.

Es la FUENTE ÚNICA DE VERDAD del modo conducción: lo computa el servidor una vez
y lo consumen TODAS las superficies (UI web, futura Live Activity / Dynamic Island,
futuro widget CarPlay). Mantenerlo plano y serializable es deliberado para que la
capa nativa (ActivityKit) pueda mapearlo sin lógica adicional.

Drive Mode NO depende de ORBIT Clinic ni del modelo de predicción experimental.
Solo usa: glucosa actual, tendencia, frescura del sensor y conexión.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Optional


# ── Vocabularios cerrados (strings estables para el contrato nativo) ──────────
class TrendDirection:
    FLAT          = "flat"
    RISING_SLOWLY = "rising_slowly"
    RISING_FAST   = "rising_fast"
    FALLING_SLOWLY = "falling_slowly"
    FALLING_FAST  = "falling_fast"
    UNKNOWN       = "unknown"

    ARROW = {
        "flat": "→", "rising_slowly": "↗", "rising_fast": "↑",
        "falling_slowly": "↘", "falling_fast": "↓", "unknown": "—",
    }


class Status:
    STABLE       = "stable"
    ATTENTION    = "attention"
    LOW          = "low"
    HIGH         = "high"
    URGENT_LOW   = "urgent_low"
    URGENT_HIGH  = "urgent_high"
    DISCONNECTED = "disconnected"
    STALE        = "stale"


class StatusLevel:
    NORMAL      = "normal"        # azul/verde
    CAUTION     = "caution"       # ámbar
    URGENT      = "urgent"        # rojo
    UNAVAILABLE = "unavailable"   # gris (datos no confiables)


@dataclass
class DriveModeState:
    """Estado glanceable para conducción. Plano y serializable a propósito."""
    glucose_value_mgdl:   Optional[int]
    trend_direction:      str            # TrendDirection.*
    trend_rate:           Optional[float]  # mg/dL/min (None si desconocido)
    status:               str            # Status.*
    status_level:         str            # StatusLevel.*
    last_update_at:       Optional[str]  # ISO8601
    minutes_since_update: Optional[int]
    sensor_name:          str
    sensor_connected:     bool
    stale_data:           bool
    safety_message:       str
    trend_arrow:          str = "—"      # glifo derivado de trend_direction

    def __post_init__(self):
        # El glifo siempre se deriva de la dirección (fuente única de verdad).
        self.trend_arrow = TrendDirection.ARROW.get(self.trend_direction, "—")

    def to_dict(self) -> dict:
        return asdict(self)
