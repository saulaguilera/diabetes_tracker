"""
pmm/ssm/dynamics.py
────────────────────
Modelo de transición f(x, u, Δt) → x'.

Dinámica continua (ODE):

    dG/dt    = - S_I × IOB_eff × k_act
               + COB2 × k_carb_to_glu(ICR)
               + EGP_basal
               − non_insulin_uptake(G)
               + dawn_input
               − exercise_effect

    dIOB/dt     = − K_PI × IOB + bolus_input(t)
    dIOB_eff/dt = + K_PI × IOB − K_IE × IOB_eff

    dCOB1/dt    = − k_a × COB1 + meal_input(t)
    dCOB2/dt    = + k_a × COB1 − K_G  × COB2

    dS_I/dt     = − λ_S × (S_I − μ_S_target)

Integramos con Runge-Kutta 4 (RK4) en sub-steps de 1 min para mantener
estabilidad cuando Δt > 1 min. Los inputs (bolus, meal) son impulses
aplicados al inicio del step (se incorporan instantáneamente al pool).

Parámetro k_act
---------------
Convierte la masa de insulin_eff (en U·activas) a una "tasa de acción"
(efecto en mg/dL por min por U). El SSM aprende S_I × k_act como un
único factor efectivo, así que fijamos k_act = 1/DIA_min para que el
peak de acción matchee al biexponencial.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from pmm.ssm.state import (
    DIM_X, STATE_NAMES, state_index,
    K_PI, K_IE, K_G,
    EGP_BASAL, K_NIM_BASAL, K_RENAL, RENAL_THRESHOLD,
    LAMBDA_SI, S_I_DEFAULT,
    K_A_FAST, K_A_MED, K_A_SLOW,
)


# k_act poblacional: convierte IOB_eff (U) en tasa de efecto.
# Calibrado para que en steady state, IOB_eff × S_I × k_act matchee
# la tasa de caída de glucemia del modelo biexponencial NovoRapid
# (peak ~ 1.5 mg/dL/min en t=60 min con 1U y S_I=45).
K_ACT_DEFAULT = 1.0 / 60.0    # 1 / (peak min)


@dataclass
class StepInputs:
    """
    Inputs conocidos durante un step de duración Δt.
    Todos son tasas/cantidades aplicadas durante la ventana [t, t+Δt].
    """
    bolus_U:       float = 0.0     # U inyectadas en este step (impulse)
    meal_g:        float = 0.0     # g carbohidratos en este step (impulse)
    k_a:           float = K_A_MED # carb absorption rate aplicable a COB1→COB2
    icr:           float = 12.0    # g CH per U (para convertir d/dt COB2 → glucose)
    s_i_target:    float = S_I_DEFAULT  # objetivo de mean-reversion (circadiano)
    dawn_rate:     float = 0.0     # mg/dL/min adicionales por dawn
    exercise_drop_rate: float = 0.0  # mg/dL/min de drop por ejercicio
    ex_sensitivity_mult: float = 1.0  # multiplicador del efecto insulínico

    # Para predicción forward: nuestro multiplicador del SSM mismo
    drift_factor:  float = 1.0     # acoplado al CUSUM (resistance/sensitivity)


def _flow(x: np.ndarray, u: StepInputs, params=None) -> np.ndarray:
    """
    Derivadas continuas dx/dt para el estado x dado los inputs u.
    NO incluye los impulses (bolus, meal) — esos se aplican como salto
    instantáneo antes de la integración.

    x: (6,) array — [G, IOB, IOB_eff, COB1, COB2, S_I]
    params: SSMParameters opcional. None → usa constantes del módulo.
    """
    # Resolver constantes (params override sino defaults del módulo)
    if params is not None:
        kpi   = params.K_PI
        kie   = params.K_IE
        kact  = params.K_ACT
        kg    = params.K_G
        egp   = params.EGP_BASAL
        knim  = params.K_NIM_BASAL
        renal_t = params.RENAL_THRESHOLD
        krenal  = params.K_RENAL
        lam_si  = params.LAMBDA_SI
    else:
        kpi, kie, kact = K_PI, K_IE, K_ACT_DEFAULT
        kg, egp, knim  = K_G, EGP_BASAL, K_NIM_BASAL
        renal_t, krenal = RENAL_THRESHOLD, K_RENAL
        lam_si = LAMBDA_SI

    G, IOB, IOB_eff, COB1, COB2, S_I = x

    # ── Insulin pharmacokinetics ──
    dIOB     = -kpi * IOB
    dIOB_eff =  kpi * IOB - kie * IOB_eff

    # ── Carb pharmacokinetics ──
    dCOB1    = -u.k_a * COB1
    dCOB2    =  u.k_a * COB1 - kg * COB2

    # ── Glucose dynamics ──
    insulin_effect = S_I * IOB_eff * kact * u.ex_sensitivity_mult / max(0.1, u.drift_factor)
    carb_effect    = (kg * COB2) * (S_I / max(1.0, u.icr))

    nim_uptake = knim * max(0.0, G - 80.0)
    if G > renal_t:
        nim_uptake += krenal * (G - renal_t)

    dG = (egp + u.dawn_rate + carb_effect
          - insulin_effect - nim_uptake - u.exercise_drop_rate)

    # ── S_I OU mean-reversion ──
    dSI = -lam_si * (S_I - u.s_i_target)

    return np.array([dG, dIOB, dIOB_eff, dCOB1, dCOB2, dSI])


def _rk4_step(x: np.ndarray, u: StepInputs, dt_min: float, params=None) -> np.ndarray:
    """Un paso RK4 de duración dt_min minutos."""
    k1 = _flow(x,                     u, params)
    k2 = _flow(x + 0.5 * dt_min * k1, u, params)
    k3 = _flow(x + 0.5 * dt_min * k2, u, params)
    k4 = _flow(x +       dt_min * k3, u, params)
    return x + (dt_min / 6.0) * (k1 + 2 * k2 + 2 * k3 + k4)


def step(
    x: np.ndarray,
    u: StepInputs,
    dt_min: float,
    substep_min: float = 1.0,
    params=None,
) -> np.ndarray:
    """
    Avanza el estado x por dt_min minutos integrando con RK4 en subpasos.

    Los inputs impulsivos (bolus, meal) se aplican como salto instantáneo
    al COMIENZO del step total — supone que dt_min es lo suficientemente
    corto para que esa aproximación sea razonable (típicamente Δt ≤ 5min).
    """
    x = x.copy()

    # Aplicar impulses (entran instantáneamente al pool correspondiente)
    if u.bolus_U > 0:
        x[state_index("IOB")]  += u.bolus_U
    if u.meal_g > 0:
        x[state_index("COB1")] += u.meal_g

    # Sub-stepping RK4 para estabilidad
    n_sub = max(1, int(np.ceil(dt_min / substep_min)))
    h = dt_min / n_sub

    # Inputs durante los substeps NO incluyen los impulses (ya aplicados)
    u_cont = StepInputs(
        bolus_U=0.0, meal_g=0.0,
        k_a=u.k_a, icr=u.icr,
        s_i_target=u.s_i_target,
        dawn_rate=u.dawn_rate,
        exercise_drop_rate=u.exercise_drop_rate,
        ex_sensitivity_mult=u.ex_sensitivity_mult,
        drift_factor=u.drift_factor,
    )

    for _ in range(n_sub):
        x = _rk4_step(x, u_cont, h, params=params)

    return x


# ── Helpers para construir inputs desde el modelo de eventos ─────────────

def k_a_for_meal_category(categoria: Optional[str], pmm_speed: float = 1.0) -> float:
    """
    Mapea categoría de comida (o None) a k_a aplicable.
    pmm_speed = speed_factor aprendido del PMM (default 1.0).
    """
    from pmm.engines.absorption import _CATEGORY_TO_BUCKET, _BUCKET_DEFAULT
    bucket = _CATEGORY_TO_BUCKET.get(categoria or "", _BUCKET_DEFAULT)
    base = {"FAST": K_A_FAST, "MED": K_A_MED, "SLOW": K_A_SLOW}[bucket]
    return base * max(0.2, min(3.0, pmm_speed))
