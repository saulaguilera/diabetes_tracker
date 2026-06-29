"""
ORBIT Drive Mode — vista de seguridad glanceable de glucosa para conducir.

Safety-first, mínima, no distractiva. NO es Clinic, NO es asistente de dosis,
NO usa el modelo de predicción. Solo glucosa actual + tendencia + frescura.

API pública:
    build_drive_mode_state(now=None) -> DriveModeState
    to_live_activity_payload(state)  -> dict   (contrato para superficies nativas)
"""
from drive_mode.state import (
    DriveModeState, TrendDirection, Status, StatusLevel,
)
from drive_mode.status_logic import classify_status, classify_trend
from drive_mode.builder import build_drive_mode_state
from drive_mode.live_activity_adapter import to_live_activity_payload

__all__ = [
    "DriveModeState", "TrendDirection", "Status", "StatusLevel",
    "classify_status", "classify_trend",
    "build_drive_mode_state", "to_live_activity_payload",
]
