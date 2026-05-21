"""
bench/tuning/promotion_gates.py
────────────────────────────────
Los 8 promotion gates del SSM — definición formal + tracking longitudinal.

Gates
-----
  1. IC50 coverage ∈ [0.45, 0.55]   (calibración a la mediana)
  2. IC90 coverage ∈ [0.85, 0.95]   (calibración a las colas)
  3. |mean(innovation_z)| < 0.2      (sin bias estructural)
  4. var(innovation_z) ∈ [0.8, 1.2]  (σ correctamente dimensionado)
  5. Ljung-Box p > 0.05              (innovations white noise)
  6. 0 eventos non-PSD               (filter numéricamente sano)
  7. Mann-Kendall MAE trend = none   (no degrada performance)
  8. Mann-Kendall IC90 trend = none  (no degrada calibración)

`evaluate_gates(audits, innovations)` evalúa los 8 sobre una ventana
agregada (típico: rolling 7d). Retorna pass/fail por gate + composite
score "promotion_readiness" ∈ [0, 1].

`gates_history(audits, days, granularity='day')` evalúa por bucket
temporal para detectar estabilidad temporal del cumplimiento.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Optional


# ── Gate definition ─────────────────────────────────────────────────────

@dataclass
class GateDefinition:
    name:        str
    description: str
    metric_key:  str
    target_lo:   Optional[float] = None
    target_hi:   Optional[float] = None
    equality:    Optional[float] = None      # gate pasa si metric == equality
    inverse:     bool = False                # True si metric debe estar FUERA del range


@dataclass
class GateResult:
    name:        str
    passed:      bool
    value:       Optional[float]
    target:      str           # human-readable target
    note:        str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name, "passed": self.passed,
            "value": self.value, "target": self.target, "note": self.note,
        }


# ── The 8 gates ─────────────────────────────────────────────────────────

GATES = [
    GateDefinition("ic50_in_range",       "IC50 coverage ∈ [0.45, 0.55]",
                   "ic50_coverage",       target_lo=0.45, target_hi=0.55),
    GateDefinition("ic90_in_range",       "IC90 coverage ∈ [0.85, 0.95]",
                   "ic90_coverage",       target_lo=0.85, target_hi=0.95),
    GateDefinition("mean_z_unbiased",     "|mean_z| < 0.2",
                   "abs_mean_z",          target_hi=0.2),
    GateDefinition("var_z_calibrated",    "var_z ∈ [0.8, 1.2]",
                   "var_z",               target_lo=0.8, target_hi=1.2),
    GateDefinition("innovations_white",   "Ljung-Box p > 0.05",
                   "lb_pvalue",           target_lo=0.05),
    GateDefinition("filter_psd",          "0 eventos non-PSD",
                   "n_non_psd",           equality=0),
    GateDefinition("mae_trend_stable",    "Mann-Kendall MAE trend = none",
                   "mae_trend_is_none",   equality=1),
    GateDefinition("ic90_trend_stable",   "Mann-Kendall IC90 trend = none",
                   "ic90_trend_is_none",  equality=1),
]


# ── Evaluator ──────────────────────────────────────────────────────────

def _gate_passes(gate: GateDefinition, value) -> bool:
    if value is None:
        return False
    if gate.equality is not None:
        return value == gate.equality
    lo_ok = gate.target_lo is None or value >= gate.target_lo
    hi_ok = gate.target_hi is None or value <= gate.target_hi
    inside = lo_ok and hi_ok
    return (not inside) if gate.inverse else inside


def _target_str(gate: GateDefinition) -> str:
    if gate.equality is not None:
        return f"= {gate.equality}"
    if gate.target_lo is not None and gate.target_hi is not None:
        return f"∈ [{gate.target_lo}, {gate.target_hi}]"
    if gate.target_lo is not None:
        return f"≥ {gate.target_lo}"
    if gate.target_hi is not None:
        return f"≤ {gate.target_hi}"
    return "—"


def evaluate_gates(metrics: dict) -> dict:
    """
    Evalúa los 8 gates sobre un dict de métricas agregadas.

    `metrics` debe tener al menos:
        ic50_coverage, ic90_coverage, abs_mean_z, var_z, lb_pvalue,
        n_non_psd, mae_trend_is_none, ic90_trend_is_none

    Returns
    -------
    {
        "gates":              [GateResult dicts...] (longitud 8),
        "n_passed":           int (0-8),
        "promotion_readiness": float (n_passed / 8),
        "verdict":            "ready" | "near_ready" | "not_ready",
        "blockers":           [list of failed gate names],
    }
    """
    results = []
    n_passed = 0
    for gate in GATES:
        value = metrics.get(gate.metric_key)
        passed = _gate_passes(gate, value)
        if passed: n_passed += 1
        results.append(GateResult(
            name=gate.name,
            passed=passed,
            value=value if isinstance(value, (int, float)) else None,
            target=_target_str(gate),
            note=gate.description,
        ).to_dict())

    readiness = n_passed / len(GATES)
    if readiness >= 7/8:
        verdict = "ready"
    elif readiness >= 5/8:
        verdict = "near_ready"
    else:
        verdict = "not_ready"

    blockers = [r["name"] for r in results if not r["passed"]]
    return {
        "gates":              results,
        "n_passed":           n_passed,
        "n_total":            len(GATES),
        "promotion_readiness": round(readiness, 3),
        "verdict":            verdict,
        "blockers":           blockers,
    }


# ── Compute gate metrics from audits + innovations ──────────────────────

def compute_gate_metrics(
    days:           int = 7,
    model_version:  str = "ssm_v0_ukf6",
) -> dict:
    """
    Computa las 8 métricas necesarias para los gates desde la DB.

    Combina:
      - PredictionAudit (IC50/IC90 + innovation_z + non_psd events)
      - SSMInnovation (secuencia para Ljung-Box)
      - Rolling diaria (para Mann-Kendall trends)
    """
    from bench.metrics.coverage    import load_resolved_audits, interval_coverage
    from bench.metrics.innovations import load_innovations, ljung_box, _normal_cdf
    from bench.metrics.covariance  import load_audits_with_cov, covariance_health
    from bench.metrics.rolling     import longitudinal_drift

    # IC50 / IC90 coverage (de los audits)
    audits_30 = load_resolved_audits(days=days, model_version=model_version,
                                      horizon_min=30)
    cov_30 = interval_coverage(audits_30)
    ic50 = cov_30.get("ic50_coverage")
    ic90 = cov_30.get("ic90_coverage")

    # mean_z + var_z (sobre innovations granulares)
    innovs = load_innovations(days=days, model_version=model_version)
    z_vals = [i["innovation_z"] for i in innovs if i.get("innovation_z") is not None]
    if z_vals:
        n = len(z_vals)
        m = sum(z_vals) / n
        v = sum((z - m) ** 2 for z in z_vals) / max(1, n - 1)
        lb = ljung_box(z_vals, lags=min(10, n // 4))
    else:
        m = v = None
        lb = {"p_value": None}

    # Covariance health
    cov_audits = load_audits_with_cov(days=days, model_version=model_version)
    h = covariance_health(cov_audits)
    n_non_psd = h.get("n_non_psd")

    # Mann-Kendall trends sobre audits
    all_audits = load_resolved_audits(days=days, model_version=model_version)
    drift = longitudinal_drift(all_audits, granularity="day")
    trends = drift.get("trends") or {}

    return {
        "ic50_coverage":         ic50,
        "ic90_coverage":         ic90,
        "abs_mean_z":            abs(m) if m is not None else None,
        "mean_z":                m,
        "var_z":                 v,
        "lb_pvalue":             lb.get("p_value"),
        "n_non_psd":             n_non_psd if n_non_psd is not None else 0,
        "mae_trend_is_none":     1 if trends.get("mae", {}).get("trend") == "none" else 0,
        "ic90_trend_is_none":    1 if trends.get("ic90_coverage", {}).get("trend") == "none" else 0,
        # extras útiles para diagnóstico (no gates)
        "n_innovations":         len(innovs),
        "n_audits":              len(all_audits),
        "n_audits_with_cov":     len(cov_audits),
    }


# ── Rolling history ────────────────────────────────────────────────────

def gates_rolling_history(
    days:           int = 14,
    window_days:    int = 7,
    model_version:  str = "ssm_v0_ukf6",
) -> list[dict]:
    """
    Para cada día en la ventana, evalúa los gates sobre una rolling window
    de `window_days` previos. Permite ver si el cumplimiento es estable o
    volátil.

    Returns
    -------
    [
      {"day": "2026-05-15", "n_passed": 5, "readiness": 0.625,
       "gates_pass": ["ic50_in_range","ic90_in_range",...], ...},
      ...
    ]
    """
    from bench.metrics.coverage    import load_resolved_audits
    from bench.metrics.innovations import load_innovations, ljung_box
    from bench.metrics.covariance  import load_audits_with_cov, covariance_health
    from bench.metrics.rolling     import longitudinal_drift

    rows = []
    now = datetime.now()
    for d in range(days, 0, -1):
        end_d   = now - timedelta(days=d - 1)
        start_d = end_d - timedelta(days=window_days)

        # Re-implementamos compute_gate_metrics con ventana custom
        # (evita N queries idénticas — todos usan el mismo filter time-frame)
        from models import PredictionAudit, SSMInnovation
        audits_30 = (PredictionAudit.query
                     .filter(PredictionAudit.predicted_at >= start_d,
                             PredictionAudit.predicted_at <= end_d,
                             PredictionAudit.resolved == True,
                             PredictionAudit.horizon_min == 30,
                             PredictionAudit.model_version == model_version).all())
        if len(audits_30) < 5:
            rows.append({
                "day":      end_d.strftime("%Y-%m-%d"),
                "n_passed": None,
                "readiness": None,
                "note":     f"datos insuficientes (n={len(audits_30)})",
            })
            continue

        n_in_50 = sum(1 for a in audits_30 if a.inside_ic50)
        n_in_90 = sum(1 for a in audits_30 if a.inside_ic90)
        ic50 = n_in_50 / len(audits_30)
        ic90 = n_in_90 / len(audits_30)

        innovs = (SSMInnovation.query
                  .filter(SSMInnovation.ts >= start_d,
                          SSMInnovation.ts <= end_d,
                          SSMInnovation.model_version == model_version,
                          SSMInnovation.rejected == False).all())
        zs = [i.innovation_z for i in innovs if i.innovation_z is not None]
        if zs and len(zs) > 8:
            mz = sum(zs) / len(zs)
            vz = sum((z - mz) ** 2 for z in zs) / (len(zs) - 1)
            lb_p = ljung_box(zs, lags=min(10, len(zs) // 4)).get("p_value")
        else:
            mz = vz = lb_p = None

        # PSD events
        cov_a = [a for a in audits_30 if a.cov_trace is not None]
        n_non_psd = sum(1 for a in cov_a if a.psd_ok is False)

        # Trends sobre el window (necesitamos rolling bucketing interno)
        drift = longitudinal_drift(audits_30, granularity="day") or {}
        trends = drift.get("trends") or {}

        metrics = {
            "ic50_coverage":      ic50,
            "ic90_coverage":      ic90,
            "abs_mean_z":         abs(mz) if mz is not None else None,
            "var_z":              vz,
            "lb_pvalue":          lb_p,
            "n_non_psd":          n_non_psd,
            "mae_trend_is_none":  1 if trends.get("mae", {}).get("trend") == "none" else 0,
            "ic90_trend_is_none": 1 if trends.get("ic90_coverage", {}).get("trend") == "none" else 0,
        }
        ev = evaluate_gates(metrics)
        rows.append({
            "day":         end_d.strftime("%Y-%m-%d"),
            "n_passed":    ev["n_passed"],
            "n_total":     ev["n_total"],
            "readiness":   ev["promotion_readiness"],
            "verdict":     ev["verdict"],
            "blockers":    ev["blockers"],
            "gates_pass":  [g["name"] for g in ev["gates"] if g["passed"]],
            "n_audits":    len(audits_30),
        })
    return rows


# ── Stability summary ──────────────────────────────────────────────────

def stability_summary(history: list[dict]) -> dict:
    """
    Sobre el rolling history calcula:
      - max streak consecutiva días con readiness == 1.0
      - volatilidad (std de readiness)
      - cuántos días con verdict = "ready"
      - cuál gate falló más veces
    """
    if not history:
        return {"n_days": 0}

    valid = [h for h in history if h.get("readiness") is not None]
    if not valid:
        return {"n_days": 0, "note": "sin días con datos suficientes"}

    readiness_vals = [h["readiness"] for h in valid]
    mean_r = sum(readiness_vals) / len(readiness_vals)
    var_r  = sum((r - mean_r) ** 2 for r in readiness_vals) / max(1, len(readiness_vals) - 1)

    # Max consecutive 100%
    max_streak = streak = 0
    for h in valid:
        if h["readiness"] >= 0.999:
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            streak = 0

    # Counter de blockers
    blocker_counts: dict[str, int] = {}
    for h in valid:
        for b in (h.get("blockers") or []):
            blocker_counts[b] = blocker_counts.get(b, 0) + 1

    n_ready = sum(1 for h in valid if h.get("verdict") == "ready")

    return {
        "n_days":            len(valid),
        "mean_readiness":    round(mean_r, 3),
        "stdev_readiness":   round(math.sqrt(var_r), 3),
        "max_streak_ready":  max_streak,
        "n_ready":           n_ready,
        "blocker_frequency": blocker_counts,
        "most_blocking":     (sorted(blocker_counts.items(), key=lambda x: -x[1])[0]
                              if blocker_counts else None),
    }
