"""
utils/monte_carlo.py — Propagación de incertidumbre por simulación de Monte Carlo.

Problema que resuelve
---------------------
El modelo físico de predicción (G = G₀ + ROC×Δt − ΔIOB×ISF + ΔCOB×ISF/ICR) usa
valores únicos ("puntuales") para cada parámetro.  En realidad todos tienen
incertidumbre propia: el ISF varía con el estrés, el ciclo hormonal y el sueño;
la absorción intestinal depende de la composición real del plato; el sensor CGM
tiene ruido.  Al asumir una Normal centrada en el estimado puntual se subestima la
asimetría real del riesgo (el riesgo de hipo no es simétrico al de hiper cuando
hay IOB alto).

Solución
--------
Ejecutar N simulaciones del mismo modelo físico, cada una con parámetros
muestreados de sus distribuciones de incertidumbre.  La distribución resultante
de G_pred es empírica: captura asimetría, colas pesadas y correlaciones no
lineales (ISF aparece en dos términos del modelo simultáneamente).

Sin dependencias externas — solo random y statistics de la stdlib de Python.

Referencias
-----------
- Haidar (2015) Annals of Biomed. Eng. — variabilidad ISF ±18%
- Patek et al. (2012) J. Diabetes Sci. Technol. — variabilidad ICR ±12%
- Schiavon et al. (2018) — variabilidad PK bolo ±15%
- Dalla Man et al. (2007) — variabilidad absorción intestinal ±25%
- Abbott Freestyle Libre 3 — MARD sensor ~9%, ruido ROC ≈0.28 mg/dL/min
"""
from __future__ import annotations

import random
from typing import Optional

# ── Número de simulaciones ────────────────────────────────────────────────────
# 3 000 muestras × 2 horizontes ≈ 30–60 ms en CPython (sin numpy).
# Con 1 000 el error de Monte Carlo sobre p_hipo es ±1 pp.
# Con 3 000 es ±0.5 pp — suficiente para tomar decisiones clínicas.
_N_DEFAULT = 3_000

# ── Sigmas por defecto para cada fuente de incertidumbre ────────────────────
_SIGMA_ISF_PCT  = 0.18   # ±18% del ISF base    (Haidar 2015)
_SIGMA_ICR_PCT  = 0.12   # ±12% del ICR base    (Patek 2012)
_SIGMA_ROC      = 0.28   # mg/dL/min — ruido sensor CGM (Libre 3 spec)
_SIGMA_DIOB_PCT = 0.15   # ±15% del ΔIOB        (Schiavon 2018)
_SIGMA_DCOB_PCT = 0.25   # ±25% del ΔCOB        (Dalla Man 2007)

# Umbrales clínicos por defecto
_HIPO_THRESH  = 70.0
_HIPER_THRESH = 180.0


def run_monte_carlo(
    g_actual:        float,
    roc:             Optional[float],
    roc_eff_min:     float,
    cob_suppression: float,
    d_iob:           float,
    d_cob:           float,
    isf_base:        float,
    icr:             Optional[float],
    n:               int   = _N_DEFAULT,
    hipo_thresh:     float = _HIPO_THRESH,
    hiper_thresh:    float = _HIPER_THRESH,
    seed:            Optional[int] = None,
    sigma_g0:        float = 0.0,   # incertidumbre del punto de partida (Kalman σ_G)
) -> dict:
    """
    Ejecuta N simulaciones del modelo de predicción muestreando de las
    distribuciones de incertidumbre de cada parámetro.

    Parámetros clave
    ----------------
    g_actual        : glucemia actual (mg/dL) — ya con bias adaptivo aplicado
    roc             : ritmo de cambio (mg/dL/min); None → muestrea alrededor de 0
    roc_eff_min     : minutos efectivos del ROC (con decaimiento exponencial)
    cob_suppression : supresión del ROC por COB activo [0.15, 1.0]
    d_iob           : ΔIOB en el horizonte (U) — insulina que se "consume"
    d_cob           : ΔCOB en el horizonte (g) — carbos que se absorben
    isf_base        : ISF efectivo del usuario (mg/dL/U), ya con factor ejercicio
    icr             : ICR (g/U); None → carb_effect = 0

    Returns
    -------
    {
      g_pred_median  : int    — mediana de la distribución (estimado central)
      g_pred_mean    : int    — media de la distribución
      p5, p10, p25,           — percentiles inferiores
      p50,                    — mediana
      p75, p90, p95           — percentiles superiores
      p_hipo         : int    — % simulaciones < hipo_thresh
      p_rango        : int    — % simulaciones en rango
      p_hiper        : int    — % simulaciones > hiper_thresh
      sigma          : float  — desviación estándar empírica
      ci_50          : [p25, p75]   — rango del 50 % más probable
      ci_68          : [p16, p84]   — intervalo al 68 %
      ci_90          : [p5,  p95]   — intervalo al 90 %
      estado         : str    — estado dominante ("hipo" | "rango" | "hiper")
      n_sim          : int    — simulaciones ejecutadas
      skewness       : float  — asimetría de la distribución
    }
    """
    if seed is not None:
        random.seed(seed)

    # Valor central y sigma del ROC
    roc_mu    = roc if roc is not None else 0.0
    roc_sigma = _SIGMA_ROC if roc is not None else _SIGMA_ROC * 0.5

    sims: list[float] = []

    for _ in range(n):
        # ── Muestreo de parámetros inciertos ──────────────────────────────
        # Punto de partida: si Kalman está activo, la glucosa inicial también es incierta
        # sigma_g0 = σ_G del filtro de Kalman (≈ 4–10 mg/dL cuando convergido)
        g0_i = random.gauss(g_actual, sigma_g0) if sigma_g0 > 0.5 else g_actual

        # ISF: Normal(ISF_base, ISF_base×18%), mínimo 10 mg/dL/U
        isf_i = max(10.0, random.gauss(isf_base, isf_base * _SIGMA_ISF_PCT))

        # ICR: Normal(ICR_base, ICR_base×12%), mínimo 1 g/U
        icr_i = max(1.0, random.gauss(icr, icr * _SIGMA_ICR_PCT)) if icr else None

        # ROC: Normal(ROC_medido, σ_sensor)
        roc_i = random.gauss(roc_mu, roc_sigma)

        # ΔIOB: Normal(ΔIOB_central, ΔIOB×15% + 0.01 U mínimo)
        # Variabilidad farmacocinética del bolo (timing de absorción, volumen distribución)
        diob_i = max(0.0, random.gauss(d_iob, abs(d_iob) * _SIGMA_DIOB_PCT + 0.01))

        # ΔCOB: Normal(ΔCOB_central, ΔCOB×25% + 0.5 g mínimo)
        # Variabilidad de vaciado gástrico e índice glucémico real
        # No se clampea: absorción más rápida (dcob_i > d_cob) o más lenta son posibles
        dcob_i = random.gauss(d_cob, abs(d_cob) * _SIGMA_DCOB_PCT + 0.5)

        # ── Mismo modelo físico que api_predict_glucose ────────────────────
        roc_effect     = roc_i * roc_eff_min * cob_suppression
        insulin_effect = diob_i * isf_i
        carb_effect    = (dcob_i * isf_i / icr_i) if icr_i else 0.0

        g_i = g0_i + roc_effect - insulin_effect + carb_effect
        sims.append(g_i)

    # ── Estadísticas de la distribución empírica ──────────────────────────
    sims.sort()
    n_sims = len(sims)

    def _pct(p: float) -> int:
        """Percentil p (0–100) de la distribución simulada."""
        idx = min(int(p / 100.0 * (n_sims - 1)), n_sims - 1)
        return round(sims[idx])

    p5  = _pct(5);   p10 = _pct(10);  p16 = _pct(16)
    p25 = _pct(25);  p50 = _pct(50)
    p75 = _pct(75);  p84 = _pct(84)
    p90 = _pct(90);  p95 = _pct(95)

    # Probabilidades clínicas — conteo directo (sin asunción de forma)
    n_hipo  = sum(1 for g in sims if g < hipo_thresh)
    n_hiper = sum(1 for g in sims if g > hiper_thresh)
    n_rango = n_sims - n_hipo - n_hiper

    p_hipo  = round(100 * n_hipo  / n_sims)
    p_hiper = round(100 * n_hiper / n_sims)
    p_rango = max(0, 100 - p_hipo - p_hiper)

    # Media y varianza
    mean_val = sum(sims) / n_sims
    variance = sum((g - mean_val) ** 2 for g in sims) / max(n_sims - 1, 1)
    sigma    = round(variance ** 0.5, 1)

    # Asimetría de Fisher-Pearson (skewness) — indica si el riesgo es asimétrico
    if sigma > 0:
        skew = round(
            sum((g - mean_val) ** 3 for g in sims) / n_sims / (sigma ** 3), 2
        )
    else:
        skew = 0.0

    # Estado dominante
    estado = max(
        [("hipo", p_hipo), ("rango", p_rango), ("hiper", p_hiper)],
        key=lambda x: x[1],
    )[0]

    return {
        "g_pred_median": p50,
        "g_pred_mean":   round(mean_val),
        "p5":   p5,  "p10": p10, "p25": p25,
        "p50":  p50,
        "p75":  p75, "p90": p90, "p95": p95,
        "p_hipo":   p_hipo,
        "p_rango":  p_rango,
        "p_hiper":  p_hiper,
        "sigma":    sigma,
        "ci_50":    [p25, p75],
        "ci_68":    [p16, p84],
        "ci_90":    [p5,  p95],
        "estado":   estado,
        "n_sim":    n_sims,
        "skewness": skew,
    }


def get_uncertainty_sigmas(isf_base: float, icr: Optional[float]) -> dict:
    """
    Devuelve los sigmas de incertidumbre que se aplicarán en la simulación,
    para mostrarlos en el panel diagnóstico.

    Útil para que el usuario entienda de dónde viene la incertidumbre.
    """
    return {
        "sigma_isf_pct":   round(_SIGMA_ISF_PCT * 100),
        "sigma_isf_abs":   round(isf_base * _SIGMA_ISF_PCT, 1) if isf_base else None,
        "sigma_icr_pct":   round(_SIGMA_ICR_PCT * 100),
        "sigma_icr_abs":   round(icr * _SIGMA_ICR_PCT, 2) if icr else None,
        "sigma_roc":       _SIGMA_ROC,
        "sigma_diob_pct":  round(_SIGMA_DIOB_PCT * 100),
        "sigma_dcob_pct":  round(_SIGMA_DCOB_PCT * 100),
        "n_simulations":   _N_DEFAULT,
    }
