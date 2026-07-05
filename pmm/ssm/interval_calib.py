"""
pmm/ssm/interval_calib.py
──────────────────────────
EXPERIMENTO r4 — calibración de los INTERVALOS de incertidumbre (offline).

Contexto: el modelo de producción `ssm_v0_ukf6_basal_ex_r2_gated_bias` ya tiene
la MEDIA corregida (sesgo +60 ~ −2 live), pero sus intervalos a +60 son
SOBRECONFIADOS (std(z) ≈ 1.63, cobertura IC90 ≈ 80% vs 90% ideal): las bandas
son demasiado angostas.

Mecanismo (opción A — lo más simple): un multiplicador post-hoc de σ específico
por horizonte. Escala SOLO la σ reportada (y por ende p_hypo/p_hyper); NO toca
la media g_pred, ni el filtro, ni la covarianza interna. Por construcción:

    σ_reportada(h) = σ_modelo(h) × sigma_mult(h)

  → media / MAE / sesgo: SIN CAMBIO (es imposible que cambien: g_pred intacto).
  → solo cambia el ANCHO del intervalo y la calibración (std(z), IC90).

Garantía: con INTERVAL_CALIB_ENABLED=False → sigma_mult()=1.0 → σ sin cambios →
modelo BYTE-IDÉNTICO al de producción. El flag arranca OFF.

NO toca: media/gated bias, R_CGM, dinámica EGP/basal, hypo engine, thresholds,
Copilot/Clinic. NO se despliega.

Tuning: SOLO la magnitud (SIGMA_MULT_60) en train; validado held-out.
"""
from __future__ import annotations

# Flag maestro — ON desde ssm_v0_ukf6_basal_ex_r3_cal60 (promoción 5/7/2026).
# Con OFF el modelo vuelve byte-idéntico a r2_gated_bias (rollback de 1 línea).
INTERVAL_CALIB_ENABLED: bool = True

# Multiplicadores de σ por horizonte (1.0 = sin cambio). Tunables.
# El foco del experimento es +60 (donde hay subcobertura). +30 se deja en 1.0.
# SIGMA_MULT_60=1.64 tuneado SOLO en train (RMS(z_train)); validado dos veces:
#   held-out 25-27/6: IC90 81→92%, std(z) 1.59→0.97 (8/8 gates)
#   semana virgen 28/6–5/7 (n=946): IC90 74→88%, hypo_window 35→82%,
#   std(z) 1.85→1.13, recall de hipos sin regresión. Ver bench/reports/.
SIGMA_MULT_30: float = 1.0
SIGMA_MULT_60: float = 1.64


def sigma_mult(horizon_min: int) -> float:
    """Multiplicador de σ para el horizonte. 1.0 si el experimento está OFF."""
    if not INTERVAL_CALIB_ENABLED:
        return 1.0
    return SIGMA_MULT_30 if horizon_min <= 45 else SIGMA_MULT_60
