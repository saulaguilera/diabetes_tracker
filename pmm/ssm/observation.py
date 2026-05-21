"""
pmm/ssm/observation.py
───────────────────────
Modelo de observación h(x): qué se mide y con qué ruido.

Por ahora solo CGM (Libre 3):
    y_CGM = G + ε,    ε ~ N(0, R(G))

donde R(G) refleja el MARD multiplicativo del sensor:
    σ(G) = max(noise_floor, MARD × G)
    R(G) = σ²

Cuando agreguemos wearables (HRV, HR, etc.) en SSM v1, este módulo crece
con observaciones adicionales que dependen de estados latentes (stress,
illness).
"""
from __future__ import annotations

import numpy as np

from pmm.ssm.state import (
    DIM_X, state_index, CGM_MARD_PCT, CGM_NOISE_FLOOR,
)


def h_cgm(x: np.ndarray) -> np.ndarray:
    """h(x) → glucemia observable."""
    return np.array([x[state_index("G")]])


def R_cgm(g_estimated: float) -> np.ndarray:
    """
    Matriz de covarianza del ruido CGM.
    σ multiplicativo + floor — captura mejor MARD a glucemias altas.
    """
    sigma = max(CGM_NOISE_FLOOR, CGM_MARD_PCT * abs(g_estimated))
    return np.array([[sigma ** 2]])


def gating_outlier(y_obs: float, y_pred: float, sigma_pred: float,
                   threshold_sigma: float = 5.0) -> bool:
    """
    True si la observación es un outlier extremo y debe rechazarse.

    Protege contra:
      - sensor compression artifacts (drops a 40 mg/dL espurios)
      - jumps absurdos por scan failure
      - lecturas corruptas en el sync

    Threshold 5σ es deliberadamente alto — preferimos absorber el outlier
    como incertidumbre del modelo (vía R inflado) que ignorarlo, salvo
    cuando es claramente patológico.
    """
    if sigma_pred <= 0:
        return False
    z = abs(y_obs - y_pred) / sigma_pred
    return z > threshold_sigma
