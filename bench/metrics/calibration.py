"""
bench/metrics/calibration.py
─────────────────────────────
Calibración de incertidumbre: ¿la σ predicha refleja el error real?

Por qué esto importa más que MAE
---------------------------------
Un modelo con MAE 10 mg/dL que dice σ=5 te miente con confianza —
sus intervalos de confianza son ficticios, lo que invalida toda
inferencia downstream (P_hipo, dose range, safety gating).

Un modelo con MAE 15 mg/dL y σ=15 es honesto: sabés exactamente cuánto
confiar. Esto es lo que necesitamos para producto clínico.

Métricas implementadas
----------------------
- **Reliability diagram**: para cada bin de σ_pred, ¿el error real cae
  en el rango esperado? Idealmente la curva está sobre la diagonal.
- **ECE** (Expected Calibration Error): área entre la curva y la diagonal.
  Target < 0.05 para producto.
- **CRPS** (Continuous Ranked Probability Score): métrica scalar gold-standard
  para distribuciones predictivas. Combina sharpness + calibration.
  Penaliza ambos overconfident y underconfident.
- **Sharpness**: σ_pred promedio. Modelos sobreconfidados tienen sharpness
  alta pero ECE pobre. El trade-off es la frontera de Pareto.
- **PIT** (Probability Integral Transform): si la distribución predictiva
  es correcta, los PIT values deben ser uniformes en [0,1]. Test K-S.
"""
from __future__ import annotations

import math
from typing import Iterable

from bench.replay import PredictionRecord


# ── Funciones núcleo ──────────────────────────────────────────────────────

def _normal_cdf(x: float, mu: float = 0.0, sigma: float = 1.0) -> float:
    """CDF normal estándar (sin scipy)."""
    if sigma <= 0:
        return 1.0 if x >= mu else 0.0
    z = (x - mu) / (sigma * math.sqrt(2))
    return 0.5 * (1 + math.erf(z))


def _normal_pdf(x: float, mu: float = 0.0, sigma: float = 1.0) -> float:
    if sigma <= 0:
        return 0.0
    z = (x - mu) / sigma
    return math.exp(-0.5 * z ** 2) / (sigma * math.sqrt(2 * math.pi))


def _crps_gaussian(g_real: float, mu: float, sigma: float) -> float:
    """
    CRPS para distribución predictiva N(mu, sigma²).
    Forma cerrada (Gneiting & Raftery 2007):

        CRPS = σ × [ z(2Φ(z)-1) + 2φ(z) - 1/√π ]
        donde z = (y - μ) / σ
    """
    if sigma <= 0:
        return abs(g_real - mu)
    z = (g_real - mu) / sigma
    phi = _normal_pdf(z)            # density at z (standard normal)
    Phi = _normal_cdf(z)            # cumulative
    return sigma * (z * (2 * Phi - 1) + 2 * phi - 1 / math.sqrt(math.pi))


# ── Reliability diagram ───────────────────────────────────────────────────

def reliability_diagram(records: list[PredictionRecord], n_bins: int = 10) -> dict:
    """
    Para cada quantile predicho (10%, 20%, ..., 90%), mide qué fracción de
    observaciones cayeron por debajo. En un modelo bien calibrado,
    observed_freq ≈ predicted_quantile.

    Returns
    -------
    {
        "quantiles":      [0.1, 0.2, ..., 0.9],   # niveles predichos
        "observed_freqs": [f1, f2, ..., f9],      # fracciones reales
        "n_used":         int,                    # registros con σ válido
    }
    """
    usable = [r for r in records if r.sigma and r.sigma > 0]
    if not usable:
        return {"quantiles": [], "observed_freqs": [], "n_used": 0}

    # Quantile levels: 10%, 20%, ..., 90% (evitar 0 y 1)
    levels = [i / n_bins for i in range(1, n_bins)]
    observed = []
    for level in levels:
        # Para cada predicción, calculamos qué g_real está bajo el quantile `level`
        # del posterior N(g_pred, sigma²). Equivalente a chequear si
        # g_real <= norm.ppf(level, g_pred, sigma).
        # Más directo: la fracción real que estaba bajo ese quantile predicho.
        n_under = sum(
            1 for r in usable
            if _normal_cdf(r.g_real, r.g_pred, r.sigma) <= level
        )
        observed.append(n_under / len(usable))

    return {
        "quantiles":      levels,
        "observed_freqs": observed,
        "n_used":         len(usable),
    }


def ece(records: list[PredictionRecord], n_bins: int = 10) -> float:
    """
    Expected Calibration Error: distancia L1 promedio entre la curva de
    reliability y la diagonal perfecta.

    Target: < 0.05 (excelente), < 0.10 (aceptable), > 0.15 (mal calibrado).
    """
    diag = reliability_diagram(records, n_bins)
    if diag["n_used"] == 0:
        return None
    diffs = [
        abs(obs - pred)
        for obs, pred in zip(diag["observed_freqs"], diag["quantiles"])
    ]
    return sum(diffs) / len(diffs)


# ── CRPS ──────────────────────────────────────────────────────────────────

def crps(records: list[PredictionRecord]) -> float:
    """
    Mean CRPS. Unidad: mg/dL. Menor = mejor.
    Para referencia: predictor que devuelve mean histórico ~ CRPS = stdev/√π × 2.
    """
    usable = [r for r in records if r.sigma and r.sigma > 0]
    if not usable:
        return None
    vals = [_crps_gaussian(r.g_real, r.g_pred, r.sigma) for r in usable]
    return sum(vals) / len(vals)


# ── Sharpness ─────────────────────────────────────────────────────────────

def sharpness(records: list[PredictionRecord]) -> float:
    """
    σ predictivo promedio. Menor = más confianza.
    Solo es útil junto con calibration: σ pequeño + ECE alta = overconfident.
    """
    sigmas = [r.sigma for r in records if r.sigma and r.sigma > 0]
    if not sigmas:
        return None
    return sum(sigmas) / len(sigmas)


# ── PIT histogram (test de uniformidad) ───────────────────────────────────

def pit_histogram(records: list[PredictionRecord], n_bins: int = 20) -> dict:
    """
    Si las predicciones N(μ, σ²) son correctas, los PIT values
    PIT_i = Φ((y_i - μ_i)/σ_i) deben ser ~ Uniform(0, 1).

    - Histograma en U: modelo subdispersado (σ muy chico) — error real
      cae en colas más de lo esperado
    - Histograma en ∩: modelo sobredispersado (σ muy grande) — error real
      cae más al centro de lo esperado
    - Histograma uniforme: bien calibrado
    """
    usable = [r for r in records if r.sigma and r.sigma > 0]
    if not usable:
        return {"bins": [], "counts": [], "uniform_freq": 0, "n": 0}

    pits = [_normal_cdf(r.g_real, r.g_pred, r.sigma) for r in usable]
    bin_edges = [i / n_bins for i in range(n_bins + 1)]
    counts = [0] * n_bins
    for p in pits:
        idx = min(int(p * n_bins), n_bins - 1)
        counts[idx] += 1

    return {
        "bins":         bin_edges,
        "counts":       counts,
        "uniform_freq": len(pits) / n_bins,    # baseline esperado
        "n":            len(pits),
    }


# ── Resumen agregado ──────────────────────────────────────────────────────

def calibration_summary(records: list[PredictionRecord]) -> dict:
    """Resumen completo para reportes."""
    usable = [r for r in records if r.sigma and r.sigma > 0]
    if not usable:
        return {
            "n_with_sigma": 0,
            "note": "ningún record tiene σ predictivo — calibración no medible. "
                    "Esto debería resolverse en ~7 días a medida que se acumulen "
                    "predicciones del nuevo schema con sigma_30/sigma_60.",
        }
    def _r(v, ndigits):
        return round(v, ndigits) if v is not None else None
    return {
        "n_with_sigma": len(usable),
        "n_total":      len(records),
        "crps":         _r(crps(usable), 2),
        "ece":          _r(ece(usable),  4),
        "sharpness":    _r(sharpness(usable), 2),
        "reliability":  reliability_diagram(usable),
        "pit":          pit_histogram(usable),
    }
