"""
pmm/ssm/parameters.py
──────────────────────
Vector completo de hiperparámetros estructurales del SSM v0.

Diseño
------
- Cada parámetro del modelo es tuneable vía esta clase.
- Defaults coinciden con state.py / dynamics.py (zero-change behavior).
- Hash determinístico (`fingerprint()`) para identificar configs en logs.
- Validación de rangos físicamente plausibles (`validate()`).
- Serialización JSON-friendly para persistir en DB y comparar configs.

Categorías (siguiendo el blueprint de tuning del usuario):

  1. Process noise        : Q_G, Q_SI, Q_KA, Q_IOB, Q_COB, Q_DRIFT
  2. Observation noise    : R_CGM_BASE, R_CGM_MARD
  3. Physiological dynamics: K_PI, K_IE, K_A_FAST/MED/SLOW, K_G,
                              LAMBDA_SI, SIGMA_SI, LAMBDA_KA
  4. Covariance regularization: PSD_EPSILON, SIGMA_FLOOR_G, INFLATION
  5. Initial uncertainty  : G_SIGMA_INIT, IOB_SIGMA_INIT, S_I_SIGMA_INIT

Uso
---
    # Backward compat: sin params → comportamiento actual
    run_filter(now=...)

    # Override puntual
    p = SSMParameters(Q_SI=0.001, LAMBDA_SI=1.0/(3*24*60))
    run_filter(now=..., params=p)
"""
from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass(frozen=True)
class SSMParameters:
    """
    Hiperparámetros estructurales del SSM v0.
    Inmutable (frozen) — cualquier "cambio" crea una instancia nueva.
    """

    # ── 1. Process noise diagonal (Q per state per minute) ──
    Q_G:       float = 4.0       # mg/dL²·min⁻¹  — process noise glucose
    Q_IOB:     float = 0.001     # U²·min⁻¹
    Q_IOB_EFF: float = 0.001
    Q_COB1:    float = 0.1       # g²·min⁻¹
    Q_COB2:    float = 0.1
    Q_SI:      float = 2.5e-7    # = SIGMA_SI² with SIGMA_SI=0.0005

    # ── 2. Observation noise (CGM) ──
    R_CGM_BASE:   float = 4.0    # mg/dL — noise floor at low G
    R_CGM_MARD:   float = 0.09   # multiplicative fraction (Libre 3 spec)

    # ── 3. Physiological dynamics ──
    # Insulin PK
    K_PI:  float = 0.025         # /min  plasma → interstitial
    K_IE:  float = 0.018         # /min  interstitial elimination
    K_ACT: float = 1.0/60.0      # /min  insulin action rate constant
    # Carb absorption
    K_A_FAST: float = 0.040
    K_A_MED:  float = 0.025
    K_A_SLOW: float = 0.015
    K_G:      float = 0.040
    # S_I OU process
    LAMBDA_SI:  float = 1.0/(5*24*60)   # mean-reversion ~5 días
    S_I_TARGET: float = 45.0            # target del prior populacional
    # Endogenous glucose
    EGP_BASAL:        float = 0.55      # mg/dL/min
    K_NIM_BASAL:      float = 0.0035    # /min
    RENAL_THRESHOLD:  float = 180.0
    K_RENAL:          float = 0.012

    # ── 4. Covariance regularization ──
    PSD_JITTER:    float = 1e-6    # Cholesky stabilization
    SIGMA_FLOOR_G: float = 1.0     # mg/dL minimum σ_G post-update
    INFLATION:     float = 1.0     # process-noise multiplier (>1 = inflar)

    # ── 5. Initial uncertainty (cold-start σ₀) ──
    G_SIGMA_INIT:    float = 8.0
    IOB_SIGMA_INIT:  float = 0.3
    COB_SIGMA_INIT:  float = 5.0
    S_I_SIGMA_INIT:  float = 12.0

    # ── 6. UKF tuning ──
    UKF_ALPHA: float = 1e-3
    UKF_BETA:  float = 2.0
    UKF_KAPPA: float = 0.0

    # ── 7. Filter operational ──
    LOOKBACK_HOURS:       int   = 6
    OUTLIER_GATE_SIGMA:   float = 5.0
    MAX_DT_MIN:           float = 10.0

    # ── Métodos utilitarios ──

    def fingerprint(self) -> str:
        """Hash md5 corto y determinístico para identificar la config."""
        canonical = json.dumps(asdict(self), sort_keys=True, default=float)
        return hashlib.md5(canonical.encode()).hexdigest()[:10]

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, default=float)

    @classmethod
    def from_dict(cls, d: dict) -> "SSMParameters":
        """Construye desde dict — ignora keys desconocidas para forward-compat."""
        valid = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**valid)

    def override(self, **kwargs) -> "SSMParameters":
        """Crea una nueva instancia con overrides puntuales."""
        d = asdict(self)
        d.update(kwargs)
        return SSMParameters(**d)

    def validate(self) -> list[str]:
        """
        Retorna lista de violaciones de rangos físicamente plausibles.
        Lista vacía = config válida.
        """
        problems = []

        if self.Q_G <= 0:                       problems.append("Q_G must be > 0")
        if self.Q_SI <= 0:                      problems.append("Q_SI must be > 0")
        if not (0.001 <= self.R_CGM_MARD <= 0.30):
            problems.append("R_CGM_MARD outside [0.001, 0.30]")
        if not (0.005 <= self.K_PI <= 0.10):    problems.append("K_PI outside [0.005, 0.10]")
        if not (0.005 <= self.K_IE <= 0.10):    problems.append("K_IE outside [0.005, 0.10]")
        if not (0.005 <= self.K_G  <= 0.10):    problems.append("K_G outside [0.005, 0.10]")
        if not (0 < self.LAMBDA_SI < 0.01):     problems.append("LAMBDA_SI outside (0, 0.01)")
        if self.UKF_ALPHA <= 0 or self.UKF_ALPHA > 1:
            problems.append("UKF_ALPHA must be in (0, 1]")
        if self.PSD_JITTER < 0:                 problems.append("PSD_JITTER must be >= 0")
        if self.INFLATION <= 0:                 problems.append("INFLATION must be > 0")
        if self.SIGMA_FLOOR_G < 0:              problems.append("SIGMA_FLOOR_G must be >= 0")

        return problems


def default_params() -> SSMParameters:
    """Returns the production-default SSMParameters (current behavior)."""
    return SSMParameters()


# ── Compatibility shim ──────────────────────────────────────────────────
# Cuando el filter recibe params=None, mapea a los constantes del módulo
# state.py / dynamics.py para mantener backward compat exacta.

def params_or_defaults(params: Optional[SSMParameters]) -> SSMParameters:
    return params if params is not None else default_params()
