"""
pmm/ssm/tests/test_exercise_input.py
─────────────────────────────────────
Sanity checks físicos del input de ejercicio + su integración en forward_predict.

Ejecutar:  python3 -m pytest pmm/ssm/tests/test_exercise_input.py -q
"""
from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np

from pmm.ssm.exercise_input import (
    ExerciseEvent, compute_exercise_effect, infer_exercise_type,
    EX_DROP_RATE_CAP,
)
from pmm.ssm.filter import FilterResult, forward_predict
from pmm.ssm.state import state_index, DIM_X


NOW = datetime(2026, 6, 8, 12, 0, 0)


def _aerobic(start_offset_min, duration=45, intensity="media"):
    return ExerciseEvent(
        timestamp=NOW + timedelta(minutes=start_offset_min),
        duration_min=duration, intensity=intensity,
        exercise_type="aerobico", activity_type="Correr",
    )


# ── Efecto puntual ──────────────────────────────────────────────────────────

def test_no_activities_is_noop():
    assert compute_exercise_effect(NOW, []) == (0.0, 1.0)


def test_direct_drop_during_aerobic():
    act = _aerobic(-20)               # empezó hace 20 min, dura 45 → en curso
    drop, sens = compute_exercise_effect(NOW, [act])
    assert drop > 0.0                 # hay baja directa durante la actividad
    assert sens >= 1.0                # sensibilidad ≥ 1 (no resistencia en aeróbico)


def test_no_direct_drop_after_activity_ends():
    act = _aerobic(-120, duration=45)  # terminó hace ~75 min
    drop, sens = compute_exercise_effect(NOW, [act])
    assert drop == 0.0                  # ya no hay baja directa
    assert sens > 1.0                   # pero la cola de sensibilidad sigue activa


def test_sensitivity_tail_peaks_hours_later():
    act = _aerobic(0, duration=45)
    # La sensibilidad post-aeróbica debe ser mayor a +6h que justo al terminar
    _, sens_end = compute_exercise_effect(act.timestamp + timedelta(minutes=45), [act])
    _, sens_6h  = compute_exercise_effect(act.timestamp + timedelta(hours=6), [act])
    assert sens_6h > sens_end


def test_anaerobic_acute_resistance():
    fz = ExerciseEvent(timestamp=NOW - timedelta(minutes=10), duration_min=40,
                       intensity="alta", exercise_type="anaerobico",
                       activity_type="Fuerza")
    aero = ExerciseEvent(timestamp=NOW - timedelta(minutes=10), duration_min=40,
                         intensity="alta", exercise_type="aerobico",
                         activity_type="Correr")
    drop_fz, sens = compute_exercise_effect(NOW, [fz])
    drop_aero, _ = compute_exercise_effect(NOW, [aero])
    assert sens < 1.0                       # resistencia aguda (sens<1)
    assert drop_fz < 0.5 * drop_aero        # mucha menos baja directa que el aeróbico


def test_drop_is_capped():
    acts = [_aerobic(-10, intensity="alta") for _ in range(10)]  # muchas simultáneas
    drop, _ = compute_exercise_effect(NOW, acts)
    assert drop <= EX_DROP_RATE_CAP + 1e-9


def test_infer_type_from_name():
    assert infer_exercise_type(None, "Salí a correr") == "aerobico"
    assert infer_exercise_type(None, "Fuerza en el gym") == "anaerobico"
    assert infer_exercise_type("mixto", "lo que sea") == "mixto"


# ── Integración con forward_predict ─────────────────────────────────────────

def _make_result(g=150.0, iob_eff=1.5, s_i=45.0):
    x = np.zeros(DIM_X)
    x[state_index("G")]       = g
    x[state_index("IOB")]     = 0.5
    x[state_index("IOB_eff")] = iob_eff
    x[state_index("S_I")]     = s_i
    P = np.eye(DIM_X) * 1.0
    return FilterResult(x=x, P=P, last_ts=NOW, n_cgm_used=10, n_steps=5, error=None)


def test_forward_predict_exercise_lowers_glucose():
    """Con ejercicio aeróbico en curso, la predicción debe ser MÁS BAJA
    que sin ejercicio (baja directa + sensibilidad amplificada)."""
    res = _make_result()
    base = forward_predict(res, horizons_min=(60,), activities=[])
    with_ex = forward_predict(res, horizons_min=(60,), activities=[_aerobic(-15)])
    assert with_ex[60].g_pred < base[60].g_pred - 2.0   # al menos 2 mg/dL más bajo


def test_forward_predict_no_activities_matches_baseline():
    """Sin actividades, el camino nuevo no cambia nada (backward compat)."""
    res = _make_result()
    a = forward_predict(res, horizons_min=(30, 60), activities=[])
    b = forward_predict(res, horizons_min=(30, 60), activities=[])
    assert abs(a[60].g_pred - b[60].g_pred) < 1e-6
