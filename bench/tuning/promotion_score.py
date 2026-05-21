"""
bench/tuning/promotion_score.py
────────────────────────────────
Composite promotion score: condensa 5 dimensiones de evaluación en un
único scalar ∈ [0, 1] usado para ranking de configs en grid search.

Filosofía
---------
NO optimizar solo MAE. Un modelo con MAE bajo pero σ ficticio o
inestable es PEOR que uno con MAE mediano y bien calibrado. Por eso
penalizamos cada dimensión independientemente y combinamos con pesos:

    score = 0.30·calibration + 0.25·innovation + 0.20·clinical
          + 0.15·stability   + 0.10·accuracy

Cada sub-score ∈ [0, 1] (más alto = mejor). Diseñados de forma que:
  - 1.0 = excelente (cumple thresholds estrictos)
  - 0.5 = aceptable
  - 0.0 = inaceptable

Esto permite filtrar configs sub-aceptables (score < 0.5) antes de
considerar trade-offs Pareto.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional


# ── Pesos (suman 1) ─────────────────────────────────────────────────────
W_CALIBRATION = 0.30
W_INNOVATION  = 0.25
W_CLINICAL    = 0.20
W_STABILITY   = 0.15
W_ACCURACY    = 0.10


# ── Sub-scores ─────────────────────────────────────────────────────────

def calibration_score(metrics: dict) -> float:
    """
    Mide qué tan bien calibrados son los intervalos de confianza.

    Targets:
      IC50 coverage ∈ [0.45, 0.55]
      IC90 coverage ∈ [0.85, 0.95]
      MCE < 0.05 excelente, < 0.10 aceptable

    Combina los tres con exp(-|gap|/τ).
    """
    ic50 = metrics.get("ic50_coverage")
    ic90 = metrics.get("ic90_coverage")
    mce  = metrics.get("mce")
    if ic50 is None or ic90 is None or mce is None:
        return 0.0
    s_50 = math.exp(-abs(ic50 - 0.50) / 0.10)
    s_90 = math.exp(-abs(ic90 - 0.90) / 0.10)
    s_mc = math.exp(-mce / 0.10)
    # Geometric mean — penaliza si alguno está MUY mal
    return round((s_50 * s_90 * s_mc) ** (1.0 / 3.0), 4)


def innovation_score(metrics: dict) -> float:
    """
    Mide whiteness de los residuales del filtro.

    Targets:
      |mean_z| < 0.2     (sin bias estructural)
      var_z ∈ [0.8, 1.2] (σ predictivo correctamente calibrado)
      Ljung-Box p > 0.05 (white noise no rechazada)
    """
    mz   = metrics.get("mean_z")
    vz   = metrics.get("var_z")
    lb_p = metrics.get("lb_pvalue")
    if mz is None or vz is None:
        return 0.0
    s_mean = math.exp(-abs(mz) / 0.5)
    s_var  = math.exp(-abs(vz - 1.0) / 0.5)
    # Ljung-Box: indicator suave (0.5 si rechaza, 1.0 si pasa)
    s_lb = 1.0
    if lb_p is not None:
        s_lb = 1.0 if lb_p > 0.05 else 0.5 + 5.0 * lb_p   # rampa hasta 0.5
    return round(s_mean * s_var * s_lb, 4)


def clinical_score(metrics: dict) -> float:
    """
    Mide utilidad clínica: detectar hipos con bajo FA rate.

    Targets:
      hypo_recall > 0.85 a +30min
      false_alarms/día < 1.0
    """
    recall = metrics.get("hypo_recall_30")
    fa_day = metrics.get("false_alarms_per_day")
    if recall is None:
        return 0.5  # neutral si no hay hipos para evaluar
    s_recall = min(1.0, recall / 0.85)
    s_fa     = 1.0
    if fa_day is not None:
        s_fa = math.exp(-fa_day / 2.0)
    return round(s_recall * s_fa, 4)


def stability_score(metrics: dict) -> float:
    """
    Mide salud numérica del filtro.

    Targets:
      0 eventos non-PSD
      < 5% explosion / collapse
      κ(P) razonable
    """
    n_total       = metrics.get("n_audits") or 1
    n_non_psd     = metrics.get("n_non_psd") or 0
    n_explosion   = metrics.get("n_explosion") or 0
    n_collapse    = metrics.get("n_collapse") or 0
    n_high_kappa  = metrics.get("n_high_kappa") or 0

    s_psd = 1.0 if n_non_psd == 0 else max(0.0, 1.0 - 10 * n_non_psd / n_total)
    s_explosion = max(0.0, 1.0 - 5 * n_explosion / n_total)
    s_collapse  = max(0.0, 1.0 - 5 * n_collapse  / n_total)
    s_kappa     = max(0.5, 1.0 - n_high_kappa / n_total)
    return round(s_psd * s_explosion * s_collapse * s_kappa, 4)


def accuracy_score(metrics: dict) -> float:
    """
    Mide error absoluto. Diseñado para NO dominar el composite.
    MAE_30 < 15 mg/dL → 1.0 ; MAE_30 = 30 → 0.5 ; MAE_30 > 40 → ≈ 0.
    """
    mae_30 = metrics.get("mae_30")
    if mae_30 is None:
        return 0.0
    return round(math.exp(-(max(0, mae_30 - 10)) / 15.0), 4)


# ── Composite ───────────────────────────────────────────────────────────

@dataclass
class PromotionScoreBreakdown:
    """Descomposición del composite — para auditoría y ranking."""
    calibration: float
    innovation:  float
    clinical:    float
    stability:   float
    accuracy:    float
    composite:   float

    def to_dict(self) -> dict:
        return self.__dict__


def compute_promotion_score(metrics: dict) -> PromotionScoreBreakdown:
    """
    Aplica los 5 sub-scores y combina con pesos del módulo.

    `metrics` es un dict plano con todas las keys que cada sub-score lee.
    Construido por `flatten_metrics()` desde el output del runner.
    """
    cal = calibration_score(metrics)
    inn = innovation_score(metrics)
    cli = clinical_score(metrics)
    sta = stability_score(metrics)
    acc = accuracy_score(metrics)
    composite = (W_CALIBRATION * cal + W_INNOVATION * inn +
                 W_CLINICAL    * cli + W_STABILITY  * sta +
                 W_ACCURACY    * acc)
    return PromotionScoreBreakdown(
        calibration=cal, innovation=inn, clinical=cli,
        stability=sta, accuracy=acc, composite=round(composite, 4),
    )


# ── Helper: collapse del reporte del runner a metrics flat ──────────────

def flatten_metrics_for_score(
    bench_report:    dict,
    model_version:   str,
    horizon_min:     int = 30,
) -> dict:
    """
    Toma el output completo de `bench.runner.run_backtest` y extrae el
    subset de números necesarios para los sub-scores de un modelo+horizon.

    Resilient a keys faltantes — devuelve dict con None si no hay datos.
    """
    flat = {}
    m = (bench_report or {}).get("by_model", {}).get(model_version, {})
    val = (bench_report or {}).get("validation", {}).get(model_version, {})

    # Accuracy by horizon
    acc_h = m.get("accuracy_by_horizon", {}).get(f"+{horizon_min}min", {})
    flat["mae_30"] = acc_h.get("mae") if horizon_min == 30 else None
    if horizon_min == 60:
        flat["mae_60"] = acc_h.get("mae")

    # Coverage
    cov = val.get(f"coverage_{horizon_min}", {}).get("interval_coverage", {})
    flat["ic50_coverage"] = cov.get("ic50_coverage")
    flat["ic90_coverage"] = cov.get("ic90_coverage")
    mce_d = val.get(f"coverage_{horizon_min}", {}).get("maximum_calibration_error", {})
    flat["mce"] = mce_d.get("mce")
    flat["ece"] = mce_d.get("ece")

    # Innovation (no es por horizon — global del filter)
    inn = val.get("innovation_diagnostics", {})
    flat["mean_z"]    = inn.get("mean_z")
    flat["var_z"]     = inn.get("var_z")
    flat["lb_pvalue"] = (inn.get("ljung_box") or {}).get("p_value")

    # Clinical (del bench primary)
    clin = m.get("clinical", {})
    h_key = f"hypo_+{horizon_min}min"
    flat["hypo_recall_30"]       = clin.get(h_key, {}).get("recall")
    flat["false_alarms_per_day"] = clin.get(h_key, {}).get("false_alarm_rate_per_day")

    # Stability
    cov_h = val.get("covariance_health", {})
    flat["n_audits"]     = cov_h.get("n") or val.get("n_audits")
    flat["n_non_psd"]    = cov_h.get("n_non_psd")
    flat["n_explosion"]  = cov_h.get("n_explosion")
    flat["n_collapse"]   = cov_h.get("n_collapse")
    flat["n_high_kappa"] = cov_h.get("n_high_kappa")

    return flat
