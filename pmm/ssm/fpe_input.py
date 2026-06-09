"""
pmm/ssm/fpe_input.py
─────────────────────
⚠️ EXPERIMENTO RECHAZADO (branch experiment/r2-fpe-rejected, 2026-06-09).
   La validación held-out dio óptimo FPE_GAIN=0: prender el FPE EMPEORA
   post-meal 2-5h, high-fat/protein y global (el modelo ya sobre-predice
   post-comida). NO promover ni desplegar. Ver bench/reports/fpe/README.md.
   Se preserva apagable (FPE_ENABLED=False) como registro reproducible.

Fat/Protein Effect (FPE) — input determinístico DESACOPLADO y APAGABLE.

Hipótesis (r2_fpe)
──────────────────
r1 ya corrigió el exceso de R_CGM. Los errores residuales post-comida,
sobre todo 2-5h después de comidas altas en grasa/proteína, podrían deberse
a una APARICIÓN RETARDADA de glucosa NO modelada:
  - proteína → gluconeogénesis (en T1D ~½ de la proteína aparece como glucosa
    a lo largo de 3-5h),
  - grasa → enlentece el vaciado gástrico → subida tardía y prolongada
    ("efecto pizza").

Modelo (reservorio lento, mass-conserving)
──────────────────────────────────────────
Cada comida deposita una carga de "glucosa-equivalente":
    load = FP_PROT_GLUCOSE·protein_g + FP_FAT_GLUCOSE·fat_g     [g]
que aparece en sangre con un kernel gamma(2) LENTO (pico ~1/k):
    φ(τ) = k² · τ · e^(−kτ)      (∫φ dτ = 1)
La tasa de aparición se suma a dG en la dinámica:
    fpe_rise_rate(t) = Σ_meals load · φ(t − t_meal) · FPE_GAIN   [mg/dL/min]

Usa SOLO grasa y proteína (los carbos siguen en COB) → no hay doble conteo.
La glucosa que aporta queda sujeta a la insulina activa (insulin_effect se
resta globalmente en dG).

Seguridad / aislamiento
───────────────────────
- FPE_ENABLED = False por defecto → con el flag apagado, fpe_rise_rate = 0 y
  el modelo es BYTE-IDÉNTICO a r1. No toca producción.
- Es un input determinístico (como basal/ejercicio): el UKF sigue en 6 estados.
- FPE_GAIN es la perilla principal (se tunea SOLO en train con el harness).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional


# ── FEATURE FLAG ────────────────────────────────────────────────────────────
# OFF por defecto. El harness lo prende para el brazo experimental.
FPE_ENABLED: bool = False


# ── Constantes (tunables; defaults conservadores, ancladas a fisiología) ─────
FP_PROT_GLUCOSE: float = 0.5    # g glucosa por g proteína (gluconeogénesis T1D)
FP_FAT_GLUCOSE:  float = 0.10   # g glucosa-equiv por g grasa (subida tardía)

FPE_K:    float = 1.0 / 150.0   # /min — kernel gamma(2), pico ~150 min (2.5h)
FPE_GAIN: float = 1.5           # mg/dL por g-equiv (excursión total = load·GAIN)

FPE_LOOKBACK_HOURS: int = 8     # el kernel tiene ~99% de su masa antes de ~7h
FPE_LOAD_CAP_G:     float = 120 # tope de carga por comida (anti-outlier)


@dataclass(frozen=True)
class FpeMeal:
    """Comida con su grasa/proteína. Inmutable (viene de DB)."""
    timestamp: datetime
    fat_g:     float
    protein_g: float


def fpe_load_g(fat_g: float, protein_g: float) -> float:
    """Carga de glucosa-equivalente (g) de una comida por su grasa+proteína."""
    load = FP_PROT_GLUCOSE * max(0.0, protein_g or 0.0) + FP_FAT_GLUCOSE * max(0.0, fat_g or 0.0)
    return min(load, FPE_LOAD_CAP_G)


def compute_fpe_effect(t: datetime, meals: list) -> float:
    """
    Tasa de aparición de glucosa (mg/dL/min) por el efecto grasa/proteína de
    TODAS las comidas previas a `t`. Devuelve 0.0 si el flag está apagado o
    no hay comidas relevantes.
    """
    if not FPE_ENABLED or not meals:
        return 0.0
    k = FPE_K
    rate = 0.0
    for m in meals:
        tau = (t - m.timestamp).total_seconds() / 60.0
        if tau <= 0 or tau > FPE_LOOKBACK_HOURS * 60:
            continue
        load = fpe_load_g(m.fat_g, m.protein_g)
        if load <= 0:
            continue
        phi = (k * k) * tau * math.exp(-k * tau)     # gamma(2), 1/min
        rate += load * phi * FPE_GAIN
    return rate


def load_fpe_meals(now: datetime, lookback_hours: int = FPE_LOOKBACK_HOURS) -> list:
    """
    Lee comidas con grasa/proteína para el lookback. [] silencioso si falla
    (sin contexto Flask) → el filtro corre sin FPE (backward compat).
    """
    try:
        from models import Meal
        cutoff = now - timedelta(hours=lookback_hours)
        rows = (Meal.query
                .filter(Meal.timestamp >= cutoff, Meal.timestamp <= now)
                .filter((Meal.fat_g > 0) | (Meal.protein_g > 0))
                .order_by(Meal.timestamp)
                .all())
        return [FpeMeal(timestamp=r.timestamp, fat_g=r.fat_g or 0.0, protein_g=r.protein_g or 0.0)
                for r in rows]
    except Exception:
        return []


def fpe_effect_trace(meals: list, t_start: datetime, t_end: datetime, step_min: int = 15) -> list:
    """Traza (t, fpe_rise_rate) para visualización/validación."""
    out = []
    t = t_start
    while t <= t_end:
        out.append((t, compute_fpe_effect(t, meals)))
        t += timedelta(minutes=step_min)
    return out
