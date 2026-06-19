"""
pmm/ssm/basal_bias.py
──────────────────────
EXPERIMENTO r3 — corrección del sesgo basal neto CON COMPUERTA POR GLUCOSA (offline).

Evolución de r2: r2 probó un offset CONSTANTE a dG y confirmó que el sesgo global
de sobre-predicción vive en el término basal neto — pero un offset constante
SOBRE-corrige en glucosa baja (donde el modelo ya estaba sin sesgo, por contra-
regulación). El sesgo es DEPENDIENTE de la glucosa.

r3: la corrección se ATENÚA suavemente cerca de hipoglucemia mediante una compuerta
sigmoide sobre la glucosa actual:

    offset_efectivo(G) = BASAL_NET_OFFSET × gate(G)
    gate(G) = sigmoid((G − GATE_THRESHOLD) / GATE_SOFTNESS)

  → glucosa normal/alta:  gate ≈ 1  → corrección activa (conserva la ganancia global)
  → cerca de baja:        gate → 0  → corrección se apaga (no daña las hipos)
  → glucosa baja:         gate ≈ 0  → no toca

La compuerta se evalúa sobre el G que evoluciona en la dinámica, así que la
corrección se auto-atenúa si la trayectoria predicha cae hacia lo bajo.

Garantía de seguridad del experimento:
    Con BASAL_NET_BIAS_ENABLED=False → net_basal_offset()=0.0 → el término en dG se
    saltea por completo → modelo BYTE-IDÉNTICO a r1. El flag arranca OFF.

Tuning: SOLO la magnitud base (BASAL_NET_OFFSET) en train; la compuerta
(THRESHOLD/SOFTNESS) es estructural (seguridad), elegida por fisiología, no tuneada.

NO toca: R_CGM, Q/σ/calibración de intervalos, hypo engine, thresholds, FPE.
NO se despliega.
"""
from __future__ import annotations

import math

# Flag maestro — OFF por defecto. Con OFF el modelo es idéntico a r1.
BASAL_NET_BIAS_ENABLED: bool = False

# Único parámetro tunable: magnitud base del offset a dG (mg/dL/min).
# Negativo = baja la predicción (corrige sobre-predicción). 0.0 = sin efecto.
BASAL_NET_OFFSET: float = 0.0

# Compuerta de seguridad por glucosa (estructural, NO tuneada):
# gate≈0 por debajo de ~75, ≈0.5 en 88, ≈1 por encima de ~105.
GATE_THRESHOLD: float = 88.0    # mg/dL — centro de la transición
GATE_SOFTNESS:  float = 6.0     # mg/dL — suavidad (chico = más abrupto)


def net_basal_offset() -> float:
    """Magnitud base del offset. 0.0 si el experimento está apagado (= r1)."""
    return BASAL_NET_OFFSET if BASAL_NET_BIAS_ENABLED else 0.0


def gate(g: float) -> float:
    """Compuerta sigmoide ∈ (0,1): apaga la corrección cerca de hipoglucemia."""
    z = (g - GATE_THRESHOLD) / GATE_SOFTNESS
    # clamp para estabilidad numérica del exp
    if z < -40: return 0.0
    if z > 40:  return 1.0
    return 1.0 / (1.0 + math.exp(-z))
