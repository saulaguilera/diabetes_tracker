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
    # NOTA: Q_G bajado de 4.0 → 2.0 tras bias_fix_v4 (8 combos, 14d replay).
    # v3 había sobredisperso (var_z=0.38, target [0.8,1.2]). Q_G=2.0 da
    # var_z=1.055 ✓, IC50=0.517 ✓, IC90=0.873 ✓, composite=0.501.
    # Tradeoff: mean_z en replay = -0.475 (vs -0.162 en producción v3).
    # Se monitorea producción 48h; si mean_z se mantiene < |0.25| → keeper.
    Q_G:       float = 2.0       # mg/dL²·min⁻¹  — process noise glucose (tuned v4)
    Q_IOB:     float = 0.001     # U²·min⁻¹
    Q_IOB_EFF: float = 0.001
    Q_COB1:    float = 0.1       # g²·min⁻¹
    Q_COB2:    float = 0.1
    Q_SI:      float = 2.5e-7    # = SIGMA_SI² with SIGMA_SI=0.0005

    # ── 2. Observation noise (CGM) ──
    # Recalibrado (2026-06-09, ×0.30 del valor previo 4.0/0.09) por whitening de
    # innovaciones sobre datos reales: el MARD de spec (~9%) mide sensor-vs-lab e
    # incluye sesgo de calibración LENTO; el ruido blanco relevante para el filtro
    # (sensor-vs-sensor) es ~⅓ de eso. Con el R previo el filtro era "lento"
    # (innovaciones autocorrelacionadas, ACF₁=0.60; sub-dispersas, std(z)=0.67).
    # Bajar R blanquea (ACF₁→0.15, std(z)→1.0) y mejora el pronóstico
    # (MAE +30: 11.1→8.8, +60: 17.8→15.4 en validación offline).
    R_CGM_BASE:   float = 1.2    # mg/dL — noise floor at low G
    R_CGM_MARD:   float = 0.027  # fracción multiplicativa (ruido blanco efectivo)

    # ── 3. Physiological dynamics ──
    # Insulin PK
    # NOTA: K_PI subido de 0.025 → 0.04 tras tuning bias_fix_v3 (hash d9006eda8a)
    # sobre 3 días reales. Mejora innovation +231% (autocorrelación residual).
    K_PI:  float = 0.040         # /min  plasma → interstitial (tuned v3)
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
    # NOTA Hito 7: EGP_BASAL subido 0.20 → 0.40 al modelar la basal Toujeo
    # explícitamente. El 0.20 anterior estaba compensando la basal no modelada
    # (Mejora composite +125% acumulado vs default original 0.55 → 0.20 venía
    # de aquí). Ahora que I_basal_eff aporta su acción explícita, EGP vuelve
    # hacia el rango fisiológico. 0.40 elegido por balance: en steady-state
    # con 10U/día Toujeo, I_basal_eff ≈ 0.37 U → insulin_effect ≈ 0.185
    # mg/dL/min. Para preservar dG/dt comparable al modelo previo en fasting,
    # EGP_BASAL_new ≈ 0.20 + 0.185 = 0.385 ≈ 0.40. No subir a 0.55 (textbook)
    # hasta validar con datos reales del nuevo modelo.
    EGP_BASAL:        float = 0.40      # mg/dL/min  (v5 — basal explícita)
    K_NIM_BASAL:      float = 0.0035    # /min
    RENAL_THRESHOLD:  float = 180.0
    K_RENAL:          float = 0.012

    # ── 3b. Basal insulin pharmacokinetics (Toujeo U-300) ──
    # Half-life del depot subcutáneo glargine U-300: ~20h literatura clínica.
    # K_DEPOT_BASAL = ln(2) / (20h × 60min) ≈ 0.000578 /min
    # Modelado como INPUT DETERMINÍSTICO (no estado del UKF): se computa con
    # kernel cerrado 3-exponencial (depot→plasma→intersticial) sobre el
    # historial de dosis. Reusa K_PI y K_IE del rapid (misma biología).
    # F_BIO_BASAL: bioavailability sistémica ~95% glargine U-300.
    K_DEPOT_BASAL: float = 0.000578     # /min — depot release rate
    F_BIO_BASAL:   float = 0.95         # fraction (0–1)

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
