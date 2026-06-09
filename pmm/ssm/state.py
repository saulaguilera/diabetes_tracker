"""
pmm/ssm/state.py
─────────────────
Definición del vector de estado del SSM v0 + constantes fisiológicas.

State vector (6 estados)
------------------------
Trabajamos en "operational units" — los mismos órdenes de magnitud que el
modelo IOB/COB biexponencial existente. Esto permite cross-validar contra
el predictor actual sin reconciliar unidades.

    x = [
        G,        # plasma glucose (mg/dL)
        IOB,      # plasma insulin pool (U)  — analog to Q_p / V_p
        IOB_eff,  # active interstitial insulin (U) — analog to Q_i
        COB1,     # gut compartment 1 (g carbs)
        COB2,     # gut compartment 2 (g carbs en plasma-equivalent)
        S_I,      # personal insulin sensitivity (mg/dL per U·active)
    ]

Inputs (u_t — eventos conocidos):
    bolus(t): U inyectadas en t (impulse)
    meal(t):  g de carbohidratos en t (impulse)
    dawn(t):  mg/dL/min adicionales por fenómeno del alba
    exercise(t):  multiplicador de sensibilidad >1 o <1

Observation:
    y_CGM = G + ε(MARD-dependent, multiplicative)

Diseño deliberadamente MVP
--------------------------
Lo que NO incluimos en v0 (estimación determinística / fixed populacional):
  - ICR: usamos el del PMM clásico para convertir d/dt(COB2) a mg/dL
  - k_a: por bucket de comida (constante, mismo que kinetics.py)
  - V_p, k_pi, k_ie: poblacionales (calibrados a la curva biexponencial actual)
  - Stress, illness, dawn estructural: agregaremos en SSM v1 con wearables

Compatibilidad con kinetics.py
-------------------------------
Las constantes están alineadas con `utils/kinetics.py` para que el SSM y
el modelo biexponencial conviertan la misma data en estimaciones similares
(salvo el aprendizaje Bayesiano de S_I que hace el SSM y el biexp no).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


# ── Constantes fisiológicas (poblacionales) ──────────────────────────────
# Valores derivados de Hovorka 2004 + UVA/Padova + calibración empírica
# contra `utils/kinetics.py` (NovoRapid, DIA típico 4h, peak 60min).

# Insulin pharmacokinetics — biexponencial calibrada
K_PI: float       = 0.025      # /min : plasma → interstitial (t_half ≈ 28 min)
K_IE: float       = 0.018      # /min : interstitial elimination
V_P_NOMINAL: float = 0.12      # L/kg — informativo (no usado directamente
                                #  porque trabajamos en U, no concentraciones)

# Carb absorption (matchea bucket MED de kinetics.py)
K_A_FAST: float   = 0.040      # /min : vaciado gástrico rápido
K_A_MED: float    = 0.025      # /min : default
K_A_SLOW: float   = 0.015      # /min : lento (legumbres, alto grasa)
K_G: float        = 0.040      # /min : intestino → plasma

# Hepatic glucose output (steady-state)
EGP_BASAL: float  = 0.55       # mg/dL/min — Bergman 1981

# Non-insulin-mediated peripheral uptake (Renal threshold ~180)
K_NIM_BASAL: float = 0.0035    # /min — baseline uptake rate at G=100
RENAL_THRESHOLD: float = 180.0
K_RENAL: float    = 0.012      # /min : extra clearance G > threshold

# Insulin sensitivity dynamics (OU process for S_I)
LAMBDA_SI: float  = 1.0 / (5 * 24 * 60)   # mean-reversion ~5 días
SIGMA_SI: float   = 0.0005     # per √min — random walk component
S_I_DEFAULT: float = 45.0      # mg/dL per U·active (≈ ISF típico)
S_I_MIN: float    = 10.0
S_I_MAX: float    = 150.0

# Process noise (Q diagonal) — qué tanto puede cambiar cada estado por min
# Valores ajustados conservadoramente; se refinan via EM en SSM v1.
PROCESS_NOISE_DIAG = {
    "G":       4.0,     # mg/dL — equivalent to ±2 mg/dL noise per min
    "IOB":     0.001,   # U
    "IOB_eff": 0.001,
    "COB1":    0.1,     # g
    "COB2":    0.1,
    "S_I":     SIGMA_SI ** 2,
}

# Observation noise (R) — CGM-specific
# Recalibrado por whitening de innovaciones (2026-06-09): el ruido BLANCO que ve
# el filtro (sensor-vs-sensor) es ~⅓ del MARD de spec (~9%, que es sensor-vs-lab
# e incluye sesgo de calibración lento). Ver pmm/ssm/parameters.py para el detalle.
CGM_MARD_PCT: float    = 0.027
CGM_NOISE_FLOOR: float = 1.2     # mg/dL — floor de ruido a bajas glucemias


# ── Estado y dimensión ───────────────────────────────────────────────────

STATE_NAMES = ("G", "IOB", "IOB_eff", "COB1", "COB2", "S_I")
DIM_X = len(STATE_NAMES)      # 6


def state_index(name: str) -> int:
    return STATE_NAMES.index(name)


@dataclass
class SSMState:
    """
    Snapshot tipado del estado posterior del SSM.
    μ y Σ son listas planas (numpy-friendly) para serialización JSON.
    """
    mu:    list[float]              # (6,)
    cov:   list[list[float]]        # (6, 6)
    t:     float                    # epoch seconds
    log_evidence: float = 0.0       # acumulado — usado para anomaly score

    def get(self, name: str) -> float:
        return self.mu[state_index(name)]

    def get_sigma(self, name: str) -> float:
        i = state_index(name)
        return float(self.cov[i][i]) ** 0.5

    def to_dict(self) -> dict:
        return {
            "mu":           {n: round(self.mu[i], 4) for i, n in enumerate(STATE_NAMES)},
            "sigma":        {n: round(self.get_sigma(n), 4) for n in STATE_NAMES},
            "t":            self.t,
            "log_evidence": round(self.log_evidence, 3),
        }


# ── Inicialización ────────────────────────────────────────────────────────

def initial_state(
    g_init:        float,
    iob_init:      float = 0.0,
    iob_eff_init:  float = 0.0,
    cob1_init:     float = 0.0,
    cob2_init:     float = 0.0,
    s_i_init:      Optional[float] = None,
    s_i_sigma:     float = 12.0,      # initial uncertainty on S_I
    g_sigma:       float = 8.0,
    iob_sigma:     float = 0.3,
    cob_sigma:     float = 5.0,
) -> tuple[list[float], list[list[float]]]:
    """
    Construye (μ₀, Σ₀) del filtro inicial.

    Tipicamente llamado al cold-start: G de la última lectura CGM,
    IOB del biexponencial actual (warm start), COB de la suma de comidas
    recientes. S_I del PMM si disponible, sino default poblacional.
    """
    s_i = S_I_DEFAULT if s_i_init is None else max(S_I_MIN, min(S_I_MAX, s_i_init))

    mu = [g_init, iob_init, iob_eff_init, cob1_init, cob2_init, s_i]

    # Σ diagonal (no asumimos correlaciones a priori — el filter las descubrirá)
    sigmas2 = [
        g_sigma   ** 2,
        iob_sigma ** 2,
        iob_sigma ** 2,
        cob_sigma ** 2,
        cob_sigma ** 2,
        s_i_sigma ** 2,
    ]
    cov = [[sigmas2[i] if i == j else 0.0 for j in range(DIM_X)] for i in range(DIM_X)]

    return mu, cov


# ── Bounds (para guardrails post-filter, NO durante el step) ─────────────
# Los aplica el wrapper en filter.py después de cada update, no el UKF mismo
# (clip dentro del filter rompe la consistencia gaussiana del posterior).

STATE_BOUNDS = {
    "G":       (10.0,  600.0),
    "IOB":     (0.0,    50.0),
    "IOB_eff": (0.0,    50.0),
    "COB1":    (0.0,   500.0),
    "COB2":    (0.0,   500.0),
    "S_I":     (S_I_MIN, S_I_MAX),
}
