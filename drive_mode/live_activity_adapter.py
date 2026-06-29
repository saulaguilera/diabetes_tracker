"""
drive_mode/live_activity_adapter.py
────────────────────────────────────
Adaptador de presentación: mapea DriveModeState → un payload PLANO y estable
pensado para superficies tipo Live Activity / widget / CarPlay.

Por qué existe: la capa nativa (ActivityKit en Swift, futura) NO debe contener
lógica — solo renderiza. Este adaptador define el CONTRATO que esa capa consume.
La misma forma sirve para: Lock Screen Live Activity, Dynamic Island, widget y
(si Apple lo permite) una superficie glanceable en CarPlay.

El endpoint GET /api/copilot/drive devuelve exactamente este payload.
"""
from __future__ import annotations

from drive_mode.state import DriveModeState, StatusLevel

# Tokens de color por nivel (la capa nativa los mapea a sus colores del sistema).
# normal → azul/verde · caution → ámbar · urgent → rojo · unavailable → gris.
_TINT = {
    StatusLevel.NORMAL:      "positive",
    StatusLevel.CAUTION:     "warning",
    StatusLevel.URGENT:      "critical",
    StatusLevel.UNAVAILABLE: "muted",
}


def to_live_activity_payload(state: DriveModeState) -> dict:
    """
    Payload mínimo y glanceable. Solo lo esencial y seguro para conducir.
    Deliberadamente SIN: insulina, dosis, predicción, gráficos, comidas.
    """
    g = state.glucose_value_mgdl
    return {
        # línea primaria (número grande + flecha)
        "value":        g if g is not None else "--",
        "unit":         "mg/dL",
        "trend_arrow":  state.trend_arrow,
        "trend":        state.trend_direction,
        # estado / seguridad
        "status":       state.status,
        "level":        state.status_level,
        "tint":         _TINT.get(state.status_level, "muted"),
        "message":      state.safety_message,
        # frescura / fuente
        "minutes_since_update": state.minutes_since_update,
        "updated_text": _updated_text(state),
        "sensor":       state.sensor_name,
        "connected":    state.sensor_connected,
        "stale":        state.stale_data,
        # marca
        "brand":        "ORBIT",
    }


def _updated_text(state: DriveModeState) -> str:
    m = state.minutes_since_update
    if not state.sensor_connected or m is None:
        return "No data"
    if m <= 0:
        return "Updated now"
    if m == 1:
        return "Updated 1 min ago"
    return f"Updated {m} min ago"
