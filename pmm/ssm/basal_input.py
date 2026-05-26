"""
pmm/ssm/basal_input.py
───────────────────────
Modelo determinístico de insulina basal (Toujeo U-300) como input al SSM.

Por qué determinístico (y no un estado más del UKF)
───────────────────────────────────────────────────
La basal se conoce con precisión: cada dosis tiene timestamp y unidades
exactas. Su farmacocinética (Toujeo U-300, glargine U-300) está bien
caracterizada en la literatura clínica. No hay incertidumbre estructural
que justifique covarianza propagada → tratarla como input determinístico
mantiene el UKF en 6 estados, evita regenerar 13 sigma points y aporta
estabilidad numérica.

Modelo farmacocinético (3 compartimentos)
─────────────────────────────────────────
    Toujeo dose ─→ [depot subQ] ─→ [plasma] ─→ [intersticial] ─→ acción
                       K_D           K_PI         K_IE

    - K_D  = ln(2) / 20h ≈ 0.000578 /min   (NUEVO — depot release lento)
    - K_PI = 0.040 /min  (mismo que rapid — biología idéntica)
    - K_IE = 0.018 /min  (mismo que rapid — biología idéntica)

Esto da un kernel tri-exponencial con peak ~ 10-12h post-dosis y cola
suave hasta ~36-48h. La acción intersticial efectiva en cualquier
tiempo t se obtiene por convolución del kernel con el historial de
dosis registradas:

    I_basal_eff(t) = Σ_d  dose_d.units × F_bio × kernel(t - dose_d.ts)

Donde el kernel para una dosis unitaria de 1U es la solución cerrada
del sistema EDO de la cascada (3 exponenciales distintas).

Acoplamiento con el SSM
───────────────────────
En la ecuación de glucosa, la acción insulínica antes era:
    insulin_effect = S_I × IOB_eff × k_act
Ahora:
    insulin_effect = S_I × (IOB_eff + I_basal_eff) × k_act

Una sola línea cambia. El resto del filtro intacto.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

# ── Constantes farmacocinéticas Toujeo U-300 ───────────────────────────────
# Half-life del depot subcutáneo de glargine U-300: ~19-22h en estudios PK.
# K_DEPOT_BASAL = ln(2) / (20h × 60min/h) ≈ 0.000578 /min
K_DEPOT_BASAL_DEFAULT: float = math.log(2.0) / (20.0 * 60.0)

# Bioavailability sistémica de glargine U-300: ~95% según literatura.
F_BIO_BASAL_DEFAULT: float = 0.95

# Cuántas horas atrás cargar dosis basales para computar el kernel.
# 5 half-lives del depot ≈ 100h cubre >97% del efecto residual de
# cualquier dosis pasada. 120h da margen de seguridad.
BASAL_LOOKBACK_HOURS_DEFAULT: int = 120


# ── Estructura de dosis ────────────────────────────────────────────────────

@dataclass(frozen=True)
class BasalDose:
    """Dosis basal puntual. Inmutable porque vienen de DB."""
    ts:    datetime
    units: float


# ── Kernel cerrado (3 exponenciales) ───────────────────────────────────────

def basal_kernel(
    tau_min:        float,
    K_depot:        float = K_DEPOT_BASAL_DEFAULT,
    K_pi:           float = 0.040,
    K_ie:           float = 0.018,
    F_bio:          float = F_BIO_BASAL_DEFAULT,
) -> float:
    """
    Insulina basal efectiva intersticial en tiempo τ post-dosis unitaria.

    Solución cerrada del sistema:
        ddepot/dt   = -K_depot · depot           depot(0+) = 1
        dplasma/dt  = K_depot · depot - K_pi · plasma   plasma(0) = 0
        deff/dt     = K_pi · plasma  - K_ie · eff        eff(0) = 0

    Por descomposición en fracciones parciales:
        eff(τ) = F_bio · [a·exp(-K_depot·τ) + b·exp(-K_pi·τ) + c·exp(-K_ie·τ)]

    Devuelve eff(τ) en U-activas-intersticiales para 1U dosado.
    """
    if tau_min < 0:
        return 0.0

    # Coeficientes de la descomposición. Requiere las 3 K's distintas
    # (lo cual se cumple para Toujeo: K_depot ≪ K_ie < K_pi).
    Kp, Ki, Kd = K_pi, K_ie, K_depot
    denom_a = (Kp - Kd) * (Ki - Kd)
    denom_b = (Kd - Kp) * (Ki - Kp)
    denom_c = (Kd - Ki) * (Kp - Ki)

    # Guard contra K's degeneradas (cae a 0 — debería disparar warning aguas arriba)
    if min(abs(denom_a), abs(denom_b), abs(denom_c)) < 1e-12:
        return 0.0

    factor = Kd * Kp  # numerador común
    a = factor / denom_a
    b = factor / denom_b
    c = factor / denom_c

    val = (a * math.exp(-Kd * tau_min)
         + b * math.exp(-Kp * tau_min)
         + c * math.exp(-Ki * tau_min))

    return F_bio * val


def compute_basal_eff(
    t:           datetime,
    doses:       list[BasalDose],
    K_depot:     float = K_DEPOT_BASAL_DEFAULT,
    K_pi:        float = 0.040,
    K_ie:        float = 0.018,
    F_bio:       float = F_BIO_BASAL_DEFAULT,
) -> float:
    """
    Insulina basal efectiva acumulada en `t` por todas las dosis previas.

    Suma del kernel × units para cada dosis con ts ≤ t.
    Dosis futuras se ignoran (kernel devuelve 0).
    """
    if not doses:
        return 0.0

    total = 0.0
    for d in doses:
        if d.ts > t:
            continue
        tau_min = (t - d.ts).total_seconds() / 60.0
        if tau_min > 4320:    # > 72h: contribución < 0.5% del peak — saltar
            continue
        total += d.units * basal_kernel(tau_min, K_depot, K_pi, K_ie, F_bio)

    return total


# ── DB loader ──────────────────────────────────────────────────────────────

def load_basal_doses(
    now:            datetime,
    lookback_hours: int = BASAL_LOOKBACK_HOURS_DEFAULT,
) -> list[BasalDose]:
    """
    Lee dosis basales desde insulin_doses para el lookback window.
    Filtra `type='basal'` (incluye Toujeo + cualquier otra basal registrada).

    Si por alguna razón fallara (modelo sin import, sin contexto Flask),
    devuelve lista vacía silenciosamente — el filtro corre sin basal,
    backward compat.
    """
    try:
        from models import InsulinDose
        cutoff = now - timedelta(hours=lookback_hours)
        rows = (InsulinDose.query
                .filter(InsulinDose.type == "basal",
                        InsulinDose.timestamp >= cutoff,
                        InsulinDose.timestamp <= now,
                        InsulinDose.units != None,
                        InsulinDose.units > 0)
                .order_by(InsulinDose.timestamp)
                .all())
        return [BasalDose(ts=r.timestamp, units=float(r.units)) for r in rows]
    except Exception:
        return []


# ── Diagnóstico / introspección ────────────────────────────────────────────

def basal_eff_trace(
    doses:    list[BasalDose],
    t_start:  datetime,
    t_end:    datetime,
    step_min: int = 30,
    K_depot:  float = K_DEPOT_BASAL_DEFAULT,
    K_pi:     float = 0.040,
    K_ie:     float = 0.018,
    F_bio:    float = F_BIO_BASAL_DEFAULT,
) -> list[tuple[datetime, float]]:
    """
    Genera una traza temporal (t, I_basal_eff(t)) para visualización
    y debugging. Útil para verificar que el modelo es consistente con
    la fisiología esperada antes de promover.
    """
    out = []
    t = t_start
    while t <= t_end:
        out.append((t, compute_basal_eff(t, doses, K_depot, K_pi, K_ie, F_bio)))
        t += timedelta(minutes=step_min)
    return out
