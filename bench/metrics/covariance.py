"""
bench/metrics/covariance.py
────────────────────────────
Monitoring longitudinal de salud de la matriz de covarianza P del UKF.

Lee de PredictionAudit las métricas pre-computadas (cov_trace, cov_condition,
cov_min_eig, psd_ok) y evalúa estabilidad temporal:

  - Covariance collapse: tr(P) cae bajo umbral → filter sobre-confiado
  - Covariance explosion: tr(P) sube descontroladamente → divergencia
  - Non-PSD: psd_ok = False → bug numérico (Cholesky falla)
  - Condition number: κ >> 1 → mal-condicionamiento, sensibilidad numérica
  - Mean reverting eigenvalues: rolling stability

Esto NO recalcula nada — solo agrega y reporta sobre lo que el sync ya
loguea. Es read-only sobre PredictionAudit.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Optional


# ─── Loader ──────────────────────────────────────────────────────────────

def load_audits_with_cov(days: int = 14,
                          model_version: str = "ssm_v0_ukf6") -> list:
    """Carga audits del SSM con datos de covarianza (skip si NULL)."""
    from models import PredictionAudit
    cutoff = datetime.now() - timedelta(days=days)
    return (PredictionAudit.query
            .filter(PredictionAudit.predicted_at >= cutoff,
                    PredictionAudit.model_version == model_version,
                    PredictionAudit.cov_trace.isnot(None))
            .order_by(PredictionAudit.predicted_at)
            .all())


# ─── Health diagnostics ──────────────────────────────────────────────────

def covariance_health(audits: list) -> dict:
    """
    Resumen de salud del filtro a lo largo de la ventana evaluada.

    Returns
    -------
    {
        "n":                    int,
        "trace_stats":          {mean, p50, p95, min, max},
        "condition_stats":      idem,
        "min_eig_stats":        idem,
        "n_non_psd":            int,
        "n_explosion":          int (trace > threshold),
        "n_collapse":           int (trace < threshold),
        "n_high_kappa":         int (condition > 1e6),
        "trace_trend":          "stable" | "growing" | "shrinking",
        "verdict":              "healthy" | "warnings" | "unhealthy"
    }
    """
    if not audits:
        return {"n": 0}

    traces      = [a.cov_trace     for a in audits if a.cov_trace     is not None]
    conditions  = [a.cov_condition for a in audits if a.cov_condition is not None]
    min_eigs    = [a.cov_min_eig   for a in audits if a.cov_min_eig   is not None]

    if not traces:
        return {"n": 0, "note": "audits sin covarianza"}

    # Outlier counts
    n_non_psd     = sum(1 for a in audits if a.psd_ok is False)
    TRACE_EXPLODE = 5000.0    # heurístico: tr(P) > 5000 con 6 states ~ σ medias > 28
    TRACE_COLLAPSE = 0.05     # collapse: tr(P) < 0.05 → todas las σ < 0.1
    KAPPA_HIGH     = 1e6

    n_explosion = sum(1 for t in traces if t > TRACE_EXPLODE)
    n_collapse  = sum(1 for t in traces if t < TRACE_COLLAPSE)
    n_high_kappa = sum(1 for k in conditions if k > KAPPA_HIGH)

    # Trend: comparar primer cuarto vs último cuarto
    q = len(traces) // 4
    trend = "stable"
    if q >= 3:
        first_mean = sum(traces[:q]) / q
        last_mean  = sum(traces[-q:]) / q
        ratio = last_mean / max(1e-9, first_mean)
        if ratio > 2.0:    trend = "growing"
        elif ratio < 0.5:  trend = "shrinking"

    # Verdict global
    pct_explosion = n_explosion / len(traces)
    pct_collapse  = n_collapse  / len(traces)
    pct_non_psd   = n_non_psd   / len(audits)
    if pct_non_psd > 0.01 or pct_explosion > 0.05 or pct_collapse > 0.05:
        verdict = "unhealthy"
    elif pct_explosion > 0 or pct_collapse > 0 or n_high_kappa > 0:
        verdict = "warnings"
    else:
        verdict = "healthy"

    return {
        "n":                len(audits),
        "trace_stats":      _stats(traces),
        "condition_stats":  _stats(conditions),
        "min_eig_stats":    _stats(min_eigs),
        "n_non_psd":        n_non_psd,
        "n_explosion":      n_explosion,
        "n_collapse":       n_collapse,
        "n_high_kappa":     n_high_kappa,
        "trace_trend":      trend,
        "verdict":          verdict,
        "thresholds": {
            "trace_explode":  TRACE_EXPLODE,
            "trace_collapse": TRACE_COLLAPSE,
            "kappa_high":     KAPPA_HIGH,
        },
    }


def _stats(values: list[float]) -> dict:
    if not values:
        return {"n": 0}
    v = sorted(values)
    n = len(v)
    def q(p):
        return v[max(0, min(n - 1, int(round(p * (n - 1)))))]
    return {
        "n":    n,
        "mean": round(sum(v) / n, 4),
        "p50":  round(q(0.50), 4),
        "p95":  round(q(0.95), 4),
        "min":  round(v[0],  4),
        "max":  round(v[-1], 4),
    }


# ─── Divergence detection ────────────────────────────────────────────────

def detect_divergence_events(audits: list) -> list[dict]:
    """
    Detecta eventos puntuales de divergencia del filtro.

    Criterios:
      - psd_ok = False (Cholesky falló en ese step)
      - cov_trace > 5×median (jump abrupto)
      - condition > 1e7 (mal condicionado severo)

    Devuelve lista de eventos con timestamp + reason.
    """
    events = []
    if not audits:
        return events

    traces = [a.cov_trace for a in audits if a.cov_trace is not None]
    if not traces:
        return events
    median_trace = sorted(traces)[len(traces) // 2]

    for a in audits:
        reasons = []
        if a.psd_ok is False:
            reasons.append("non_psd")
        if a.cov_trace and a.cov_trace > 5 * median_trace:
            reasons.append(f"trace_jump_{a.cov_trace:.1f}")
        if a.cov_condition and a.cov_condition > 1e7:
            reasons.append(f"kappa_{a.cov_condition:.0e}")
        if reasons:
            events.append({
                "ts":           a.predicted_at.isoformat(),
                "horizon_min":  a.horizon_min,
                "trace":        a.cov_trace,
                "condition":    a.cov_condition,
                "reasons":      reasons,
            })
    return events
