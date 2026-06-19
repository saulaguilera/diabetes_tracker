"""
pmm/ssm/basal_bias.py
──────────────────────
EXPERIMENTO r2 — corrección del SESGO GLOBAL del término basal neto (offline).

Contexto: r1 (`ssm_v0_ukf6_basal_ex_r1`) mejoró precisión pero mantiene un sesgo
de SOBRE-predicción confirmado en live, presente en TODAS las ventanas limpias
(fasting −9.6, basal_only −8.8, stable −9.1, overnight −13.2, GLOBAL −10.3 a +60).
Como aparece en ventanas sin comida ni bolo reciente, apunta a la PRODUCCIÓN
ENDÓGENA NETA del SSM (EGP − efecto basal − captación), no a la comida ni a R.

Mecanismo (opción C — un único parámetro interpretable):
    dG  +=  BASAL_NET_OFFSET            [mg/dL/min]
Un offset constante a la tasa de glucosa = corrección de la producción neta.
NEGATIVO baja la predicción (corrige la sobre-predicción). Es horizonte-
proporcional por construcción (offset×Δt), lo que calza con que el sesgo crece
con el horizonte (~−5 a +30, ~−10 a +60).

Garantía de seguridad del experimento:
    Con BASAL_NET_BIAS_ENABLED=False → net_basal_offset()=0.0 → dG SIN cambios →
    el modelo es BYTE-IDÉNTICO a r1. El flag arranca OFF.

NO toca: R_CGM, Q/σ/calibración de intervalos, hypo engine, thresholds, FPE.
NO se despliega. Tuneado solo en train; validado held-out por régimen.
"""
from __future__ import annotations

# Flag maestro — OFF por defecto. Con OFF el modelo es idéntico a r1.
BASAL_NET_BIAS_ENABLED: bool = False

# Único parámetro tunable: offset constante a dG (mg/dL/min).
# Negativo = baja la predicción (corrige sobre-predicción). 0.0 = sin efecto.
BASAL_NET_OFFSET: float = 0.0


def net_basal_offset() -> float:
    """Offset a aplicar a dG. 0.0 si el experimento está apagado (= r1)."""
    return BASAL_NET_OFFSET if BASAL_NET_BIAS_ENABLED else 0.0
