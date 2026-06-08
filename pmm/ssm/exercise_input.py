"""
pmm/ssm/exercise_input.py
──────────────────────────
Modelo determinístico del efecto del ejercicio como input al SSM.

Por qué determinístico (igual que la basal)
────────────────────────────────────────────
Cada actividad tiene timestamp, duración e intensidad conocidos. No hay
incertidumbre estructural que justifique un estado propio en el UKF →
lo tratamos como input determinístico calculado en cada step, igual que
`basal_input.compute_basal_eff()`. Mantiene el filtro en 6 estados.

Dos efectos fisiológicos — y NO reinventamos la rueda
──────────────────────────────────────────────────────
1. SENSIBILIDAD a la insulina (durante y, sobre todo, HORAS después — el
   "efecto cola" que causa hipos tardías). Ya existe un modelo clínico
   validado: `utils.kinetics.exercise_sensitivity_factor` (curva con pico
   a las 4-12h post, maneja aeróbico/anaeróbico/mixto, clamp [0.70, 1.50]).
   Lo REUTILIZAMOS tal cual → un solo modelo de sensibilidad en toda la app.
   En la dinámica entra como `ex_sensitivity_mult` (multiplica insulin_effect).

2. BAJA DIRECTA de glucosa, insulino-INDEPENDIENTE, durante la actividad
   (el músculo consume glucosa aunque no haya insulina a bordo). Esto un
   multiplicador de sensibilidad NO lo captura (si IOB≈0, multiplicar por
   1.4 sigue dando ~0). Es la pieza que de verdad le faltaba al SSM. Entra
   en la dinámica como `exercise_drop_rate` (mg/dL/min restados de dG).

Ambos términos (`exercise_drop_rate`, `ex_sensitivity_mult`) ya estaban
cableados en `dynamics._flow` pero llegaban en 0.0 / 1.0. Este módulo los llena.

Parámetros de la baja directa (conservadores)
──────────────────────────────────────────────
Anclados a literatura (Riddell 2017 consensus, T1D exercise). Conservadores
para no sobre-predecir hipos. Constantes centralizadas → fáciles de tunear.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional


# ── Escalas de la BAJA DIRECTA (insulino-independiente) ─────────────────────
# Intensidad reportada (baja/media/alta) → factor de escala.
INTENSITY_FACTOR = {"baja": 0.5, "media": 1.0, "alta": 1.6}
INTENSITY_DEFAULT = 1.0      # intensity None/desconocida → asumir "media"

# Tipo → cuánto de baja DIRECTA aporta. Aeróbico baja fuerte; anaeróbico casi
# nada de forma directa (su efecto es más vía sensibilidad/hormonas).
EX_TYPE_DROP_MULT = {"aerobico": 1.0, "mixto": 0.7, "anaerobico": 0.2}
EX_TYPE_DROP_DEFAULT = 0.7

# Baja directa base a intensidad "media" aeróbica, durante la actividad (mg/dL/min).
# Se mantiene conservadora (0.6): subirla mejora el sesgo a +60 pero SOBRE-corrige
# el horizonte +30 (MAE peor), porque la respuesta al ejercicio es muy variable.
EX_DROP_RATE_BASE: float = 0.6
EX_DROP_RATE_CAP:  float = 2.5         # tope (suma de actividades simultáneas)

# Escala de la COLA de sensibilidad post-ejercicio — SOLO dentro del SSM.
# sens_mult = 1 + EX_SENS_SCALE × (factor_clínico − 1). Con 1.0 es idéntico al
# modelo clínico; >1 amplifica el efecto de sensibilidad. No toca las
# recomendaciones (que llaman a exercise_sensitivity_factor directamente).
# Calibrado a 2.0 (2026-06-08, bench/tune_exercise.py): mejora Pareto en las
# ventanas post-ejercicio del usuario a +60 (MAE 19.9→19.4, sesgo −10.4→−8.2,
# ±20 61→63%) sin regresión en +30. Margen para subir con datos en vivo.
EX_SENS_SCALE: float = 2.0

DURATION_DEFAULT_MIN: int = 30          # si duration_min es None


# ── Estructura de actividad ────────────────────────────────────────────────

@dataclass(frozen=True)
class ExerciseEvent:
    """
    Actividad puntual. Inmutable (viene de DB). Campo `timestamp` (no `ts`)
    para ser directamente compatible con `exercise_sensitivity_factor`.
    """
    timestamp:     datetime
    duration_min:  float
    intensity:     Optional[str] = None       # baja | media | alta
    exercise_type: Optional[str] = None        # aerobico | anaerobico | mixto
    activity_type: Optional[str] = None        # nombre libre (caminar, fuerza…)


# ── Inferencia de tipo cuando no fue especificado (solo para la baja directa) ─
_AEROBIC_HINTS   = ("camin", "corr", "trot", "bici", "cicl", "nad", "cardio", "elip", "remo", "baile", "fútbol", "futbol", "tenis")
_ANAEROBIC_HINTS = ("fuerza", "pesa", "gym", "gimnas", "muscul", "sprint", "crossfit", "hiit")


def infer_exercise_type(exercise_type: Optional[str], activity_type: Optional[str]) -> str:
    """Resuelve el tipo: usa el explícito; si falta, lo infiere del nombre."""
    if exercise_type in ("aerobico", "anaerobico", "mixto"):
        return exercise_type
    name = (activity_type or "").lower()
    if any(h in name for h in _ANAEROBIC_HINTS):
        return "anaerobico"
    if any(h in name for h in _AEROBIC_HINTS):
        return "aerobico"
    return "mixto"


# ── Efecto del ejercicio en tiempo t ───────────────────────────────────────

def compute_exercise_effect(t: datetime, activities: list) -> tuple[float, float]:
    """
    Efecto agregado de TODAS las actividades en el instante `t`.

    Returns
    -------
    (drop_rate, sens_mult)
        drop_rate : mg/dL/min de baja directa (insulino-independiente),
                    solo DURANTE la ventana [inicio, inicio+duración].
        sens_mult : multiplicador del efecto insulínico (modelo clínico
                    reutilizado; incluye la cola post-ejercicio de 4-12h).
    """
    if not activities:
        return 0.0, 1.0

    # 1) Baja directa — solo durante la actividad.
    drop = 0.0
    for a in activities:
        dur = float(a.duration_min) if a.duration_min else DURATION_DEFAULT_MIN
        start = a.timestamp
        end = start + timedelta(minutes=dur)
        if start <= t <= end:
            intensity_f = INTENSITY_FACTOR.get((a.intensity or "").lower(), INTENSITY_DEFAULT)
            etype = infer_exercise_type(a.exercise_type, a.activity_type)
            type_drop_f = EX_TYPE_DROP_MULT.get(etype, EX_TYPE_DROP_DEFAULT)
            drop += EX_DROP_RATE_BASE * intensity_f * type_drop_f
    drop = min(drop, EX_DROP_RATE_CAP)

    # 2) Sensibilidad — reutilizar el modelo clínico validado de la app,
    #    con una escala opcional (EX_SENS_SCALE) que solo afecta al SSM.
    try:
        from utils.kinetics import exercise_sensitivity_factor
        raw = float(exercise_sensitivity_factor(activities, at_time=t))
        sens_mult = 1.0 + EX_SENS_SCALE * (raw - 1.0)
    except Exception:
        sens_mult = 1.0

    return drop, sens_mult


# ── DB loader ──────────────────────────────────────────────────────────────

def load_activities(now: datetime, lookback_hours: int = 24) -> list:
    """
    Lee actividades desde la tabla `activities` para el lookback window.
    Lookback amplio (24h) para captar la cola post-ejercicio de sesiones
    que empezaron antes de la ventana del filtro.

    Si fallara (sin contexto Flask), devuelve [] silenciosamente — el filtro
    corre sin ejercicio (backward compat).
    """
    try:
        from models import Activity
        cutoff = now - timedelta(hours=lookback_hours)
        rows = (Activity.query
                .filter(Activity.timestamp >= cutoff,
                        Activity.timestamp <= now)
                .order_by(Activity.timestamp)
                .all())
        return [ExerciseEvent(
            timestamp=r.timestamp,
            duration_min=r.duration_min or DURATION_DEFAULT_MIN,
            intensity=r.intensity,
            exercise_type=r.exercise_type,
            activity_type=r.activity_type,
        ) for r in rows]
    except Exception:
        return []


# ── Diagnóstico / introspección ────────────────────────────────────────────

def exercise_effect_trace(
    activities: list,
    t_start:    datetime,
    t_end:      datetime,
    step_min:   int = 15,
) -> list:
    """Traza temporal (t, drop_rate, sens_mult) para visualización/validación."""
    out = []
    t = t_start
    while t <= t_end:
        drop, mult = compute_exercise_effect(t, activities)
        out.append((t, drop, mult))
        t += timedelta(minutes=step_min)
    return out
