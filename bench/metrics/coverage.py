"""
bench/metrics/coverage.py
──────────────────────────
Coverage validation + Brier score + Maximum Calibration Error.

Coverage
--------
La pregunta fundamental: cuando el modelo dice "el 90% de probabilidad
es que esté entre X y Y", ¿el 90% de las veces realmente está ahí?

Si IC90 coverage = 87% → ligeramente sub-confiados (intervals un poco
estrechos). Si = 78% → muy sub-confiados (intervals muy estrechos,
underestimación de incertidumbre). Si = 95% → sobre-confiados (intervals
muy anchos, sobreestimación).

Brier Score
-----------
Para probabilities de eventos binarios (hypo, hyper).
BS = E[(p_pred − I_real)²]
Menor = mejor. BS=0 ideal, BS=0.25 baseline tonto, BS=1 worst case.

MCE (Maximum Calibration Error)
-------------------------------
Peor gap entre predicted quantile y observed frequency. Captura el
"worst case" mientras ECE captura el promedio.

Estas métricas se computan SOBRE PredictionAudit, no GlucosePrediction,
porque audit tiene IC50/IC90 explícitos y inside_ic50/inside_ic90 pre-
computados al resolver.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Iterable, Optional


# ─── Loader desde audit ──────────────────────────────────────────────────

def load_resolved_audits(
    days:          int = 30,
    model_version: Optional[str] = None,
    horizon_min:   Optional[int] = None,
):
    """Carga PredictionAudit resueltos para análisis."""
    from models import PredictionAudit
    cutoff = datetime.now() - timedelta(days=days)
    q = (PredictionAudit.query
         .filter(PredictionAudit.predicted_at >= cutoff,
                 PredictionAudit.resolved == True))
    if model_version:
        q = q.filter(PredictionAudit.model_version == model_version)
    if horizon_min:
        q = q.filter(PredictionAudit.horizon_min == horizon_min)
    return q.order_by(PredictionAudit.predicted_at).all()


# ─── Coverage IC50/IC90 ──────────────────────────────────────────────────

def interval_coverage(audits: list) -> dict:
    """
    % de realized glucose que cayó dentro de cada intervalo predicho.

    Returns
    -------
    {
        "n":                int,
        "ic50_coverage":    float,    # target 0.50
        "ic90_coverage":    float,    # target 0.90
        "ic50_deviation":   float,    # |observed - 0.50|
        "ic90_deviation":   float,
        "ic50_status":      "good" | "narrow" | "wide",
        "ic90_status":      "good" | "narrow" | "wide",
    }
    """
    if not audits:
        return {"n": 0}

    n = len(audits)
    n_50 = sum(1 for a in audits if a.inside_ic50)
    n_90 = sum(1 for a in audits if a.inside_ic90)
    c50  = n_50 / n
    c90  = n_90 / n

    def _status(observed: float, target: float, tol: float = 0.05) -> str:
        diff = observed - target
        if abs(diff) <= tol:    return "good"
        return "narrow" if diff < 0 else "wide"

    return {
        "n":              n,
        "ic50_coverage":  round(c50, 3),
        "ic90_coverage":  round(c90, 3),
        "ic50_deviation": round(abs(c50 - 0.50), 3),
        "ic90_deviation": round(abs(c90 - 0.90), 3),
        "ic50_status":    _status(c50, 0.50),
        "ic90_status":    _status(c90, 0.90),
        "ic50_target":    0.50,
        "ic90_target":    0.90,
    }


# ─── Brier score para p_hypo / p_hyper ───────────────────────────────────

def brier_score(audits: list, kind: str = "hypo", threshold: float = 70.0) -> dict:
    """
    Brier score para la probabilidad predicha de un evento binario.

    kind = "hypo"  → evento: realized < 70  vs predicted p_hypo
    kind = "hyper" → evento: realized > 180 vs predicted p_hyper

    Brier decomposition (Murphy 1973):
        BS = REL − RES + UNC
        REL (reliability) — qué tan calibrado (menor = mejor, ideal 0)
        RES (resolution)  — qué tan informativo (mayor = mejor)
        UNC (uncertainty) — entropía del evento (constant para data fija)

    Reportamos BS + un baseline (predictor constante con frecuencia media).
    """
    if not audits:
        return {"n": 0}

    valid = []
    for a in audits:
        if kind == "hypo":
            p = a.p_hypo
            actual = 1 if (a.realized_glucose is not None and a.realized_glucose < threshold) else 0
        else:
            p = a.p_hyper
            actual = 1 if (a.realized_glucose is not None and a.realized_glucose > threshold) else 0
        if p is None:
            continue
        valid.append((float(p), int(actual)))

    if not valid:
        return {"n": 0}

    bs = sum((p - y) ** 2 for p, y in valid) / len(valid)
    base_rate = sum(y for _, y in valid) / len(valid)
    bs_baseline = base_rate * (1 - base_rate)   # constant predictor BS

    # Brier skill score: 1 - BS/BS_baseline. Positivo = mejor que constante.
    if bs_baseline > 0:
        skill = 1.0 - bs / bs_baseline
    else:
        skill = 0.0 if bs == 0 else float("-inf")

    return {
        "n":            len(valid),
        "kind":         kind,
        "threshold":    threshold,
        "brier_score":  round(bs, 4),
        "base_rate":    round(base_rate, 4),
        "bs_baseline":  round(bs_baseline, 4),
        "skill_score":  round(skill, 3),
    }


# ─── Maximum Calibration Error ──────────────────────────────────────────

def maximum_calibration_error(audits: list, n_bins: int = 10) -> dict:
    """
    MCE: peor diferencia entre confidence predicho (quantile) y frecuencia
    observada de "inside ic" en bins de probabilidad.

    Para esto necesitamos múltiples niveles de IC — usamos μ ± k·σ con
    k ∈ {0.25, 0.5, 0.75, ..., 2.0} → 8 niveles que cubren coverage
    de ~20% a ~95%.

    Esto es independiente de los IC50/IC90 ya guardados: re-computamos
    sobre cada audit con su (mu, sigma, realized).
    """
    valid = [a for a in audits if a.sigma and a.sigma > 0 and a.realized_glucose is not None]
    if not valid:
        return {"n": 0}

    # Niveles z y sus quantiles esperados (P(|Z|<k))
    ks = [0.25, 0.5, 0.75, 1.0, 1.282, 1.645, 1.96, 2.326]
    def _phi(z): return 0.5 * (1 + math.erf(z / math.sqrt(2)))
    expected = [round(2 * _phi(k) - 1, 4) for k in ks]   # P(|Z|<k)

    observed = []
    for k in ks:
        n_in = sum(1 for a in valid
                   if abs(a.realized_glucose - a.mu) <= k * a.sigma)
        observed.append(n_in / len(valid))

    diffs = [abs(o - e) for o, e in zip(observed, expected)]
    mce   = max(diffs) if diffs else 0.0
    ece_v = sum(diffs) / len(diffs) if diffs else 0.0

    return {
        "n":           len(valid),
        "z_levels":    ks,
        "expected":    expected,
        "observed":    [round(o, 4) for o in observed],
        "gaps":        [round(d, 4) for d in diffs],
        "ece":         round(ece_v, 4),
        "mce":         round(mce, 4),
    }


# ─── Sharpness vs Calibration (Pareto trade-off) ─────────────────────────

def sharpness_calibration_tradeoff(audits: list) -> dict:
    """
    Reporta σ promedio + MCE para visualizar el trade-off.

    Modelo ideal: σ chico (sharpness alta) + MCE bajo (bien calibrado).
    Si σ grande y MCE bajo: honesto pero poco útil (intervalos anchos).
    Si σ chico y MCE alto:  miente con confianza.
    """
    sigmas = [a.sigma for a in audits if a.sigma is not None]
    if not sigmas:
        return {"n": 0}
    mean_sigma = sum(sigmas) / len(sigmas)
    mce_data   = maximum_calibration_error(audits)
    return {
        "n":              len(sigmas),
        "mean_sigma":     round(mean_sigma, 2),
        "median_sigma":   round(sorted(sigmas)[len(sigmas) // 2], 2),
        "mce":            mce_data.get("mce"),
        "ece":            mce_data.get("ece"),
        "interpretation": _interpret_tradeoff(mean_sigma, mce_data.get("mce")),
    }


def _interpret_tradeoff(mean_sigma: float, mce: Optional[float]) -> str:
    if mce is None:
        return "datos insuficientes"
    if mce < 0.05 and mean_sigma < 20:
        return "óptimo — bien calibrado y útilmente sharp"
    if mce < 0.05:
        return "honesto pero intervalos anchos"
    if mean_sigma < 15:
        return "overconfident — sharp pero mal calibrado"
    if mce > 0.15:
        return "mal calibrado en general — revisar dynamics/observation model"
    return "aceptable"


# ─── Resumen ────────────────────────────────────────────────────────────

def coverage_summary(audits: list) -> dict:
    return {
        "interval_coverage":   interval_coverage(audits),
        "brier_hypo":          brier_score(audits, kind="hypo",  threshold=70.0),
        "brier_hyper":         brier_score(audits, kind="hyper", threshold=180.0),
        "maximum_calibration_error": maximum_calibration_error(audits),
        "sharpness_tradeoff":  sharpness_calibration_tradeoff(audits),
    }
