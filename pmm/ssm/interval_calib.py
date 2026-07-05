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

# Flag maestro — OFF por defecto. Con OFF el modelo es idéntico a producción.
INTERVAL_CALIB_ENABLED: bool = False

# Multiplicadores de σ por horizonte (1.0 = sin cambio). Tunables.
# El foco del experimento es +60 (donde hay subcobertura). +30 se deja en 1.0.
# SIGMA_MULT_60=1.64 es el valor CANDIDATO (tuneado solo en train = RMS(z_train),
# validado held-out: IC90 81→92%, std(z) 1.59→0.97). Queda listo pero el flag
# arranca OFF (no desplegado). Ver bench/reports/interval_calib/.
SIGMA_MULT_30: float = 1.0
SIGMA_MULT_60: float = 1.64


def sigma_mult(horizon_min: int) -> float:
    """Multiplicador de σ para el horizonte. 1.0 si el experimento está OFF."""
    if not INTERVAL_CALIB_ENABLED:
        return 1.0
    return SIGMA_MULT_30 if horizon_min <= 45 else SIGMA_MULT_60
