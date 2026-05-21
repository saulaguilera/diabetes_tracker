"""
bench/metrics/rolling.py
─────────────────────────
Métricas longitudinales para detectar drift de performance / calibración.

Genera series temporales (por hora, día o ventana móvil) de:
  - MAE, RMSE, MARD
  - IC50 / IC90 coverage
  - Mean / Var de innovation_z
  - Sharpness (σ̄)
  - p_hypo Brier

Esto es el termómetro de "longitudinal stability" — si una métrica
empeora con el tiempo, hay drift del modelo o del usuario.

Output formato: lista de buckets temporales con métricas, listo para
plotear o calcular trend tests (Mann-Kendall, etc.).
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Optional


# ─── Bucketing temporal ──────────────────────────────────────────────────

def _bucket_key(ts: datetime, granularity: str) -> str:
    if granularity == "hour":
        return ts.strftime("%Y-%m-%d %H:00")
    if granularity == "day":
        return ts.strftime("%Y-%m-%d")
    raise ValueError(f"granularity inválida: {granularity}")


def _bucket_audits(audits: list, granularity: str = "day") -> dict[str, list]:
    """Agrupa audits por bucket temporal."""
    buckets: dict[str, list] = {}
    for a in audits:
        k = _bucket_key(a.predicted_at, granularity)
        buckets.setdefault(k, []).append(a)
    return buckets


# ─── Rolling MAE / RMSE / coverage / calibration ─────────────────────────

def rolling_metrics(
    audits:       list,
    granularity:  str = "day",
    min_bucket_n: int = 10,
) -> list[dict]:
    """
    Para cada bucket temporal, calcula métricas críticas.

    Returns
    -------
    Lista de dicts ordenados cronológicamente:
      [{
          "bucket": "2026-05-20",
          "n": int,
          "mae": float,
          "rmse": float,
          "bias": float,
          "ic50_coverage": float,
          "ic90_coverage": float,
          "mean_z": float,
          "var_z": float,
          "mean_sigma": float,
      }, ...]
    """
    if not audits:
        return []
    buckets = _bucket_audits(audits, granularity)
    rows = []
    for key in sorted(buckets.keys()):
        items = buckets[key]
        if len(items) < min_bucket_n:
            continue
        errs   = [a.realized_glucose - a.mu for a in items if a.realized_glucose is not None]
        abs_e  = [abs(e) for e in errs]
        sq     = [e * e for e in errs]
        n_50   = sum(1 for a in items if a.inside_ic50)
        n_90   = sum(1 for a in items if a.inside_ic90)
        sigmas = [a.sigma for a in items if a.sigma is not None]
        zs     = [a.innovation_z for a in items if a.innovation_z is not None]
        n = len(items)
        rows.append({
            "bucket":        key,
            "n":             n,
            "mae":           round(sum(abs_e) / max(1, len(abs_e)), 2) if abs_e else None,
            "rmse":          round(math.sqrt(sum(sq) / len(sq)), 2)    if sq    else None,
            "bias":          round(sum(errs) / len(errs), 2)           if errs  else None,
            "ic50_coverage": round(n_50 / n, 3),
            "ic90_coverage": round(n_90 / n, 3),
            "mean_z":        round(sum(zs) / len(zs), 3)               if zs    else None,
            "var_z":         round(_var(zs), 3)                         if len(zs) > 1 else None,
            "mean_sigma":    round(sum(sigmas) / len(sigmas), 2)        if sigmas else None,
        })
    return rows


def _var(xs: list[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = sum(xs) / len(xs)
    return sum((x - m) ** 2 for x in xs) / (len(xs) - 1)


# ─── Trend test simple (Mann-Kendall liviano) ───────────────────────────

def mann_kendall(values: list[float]) -> dict:
    """
    Test no-paramétrico de tendencia. Tau de Kendall + p-valor aproximado.

    Devuelve: {tau, p_value, trend ∈ {increasing, decreasing, none}}
    """
    n = len(values)
    if n < 8:
        return {"n": n, "note": "muestra insuficiente"}
    S = 0
    for i in range(n - 1):
        for j in range(i + 1, n):
            d = values[j] - values[i]
            S += (1 if d > 0 else -1 if d < 0 else 0)
    # Varianza bajo H0 (sin ties)
    var_S = n * (n - 1) * (2 * n + 5) / 18.0
    if var_S <= 0:
        return {"n": n, "tau": 0, "p_value": 1, "trend": "none"}
    z = (S - 1) / math.sqrt(var_S) if S > 0 else (S + 1) / math.sqrt(var_S) if S < 0 else 0
    p_value = 2 * (1 - 0.5 * (1 + math.erf(abs(z) / math.sqrt(2))))
    tau = S / (0.5 * n * (n - 1))
    trend = "none"
    if p_value < 0.05:
        trend = "increasing" if S > 0 else "decreasing"
    return {
        "n":       n,
        "tau":     round(tau, 3),
        "z":       round(z, 3),
        "p_value": round(p_value, 4),
        "trend":   trend,
    }


# ─── Drift summary ──────────────────────────────────────────────────────

def longitudinal_drift(audits: list, granularity: str = "day") -> dict:
    """
    Detecta drift de performance / calibración a lo largo de la ventana.

    Para cada métrica clave (MAE, ECE-like, mean_z), aplica Mann-Kendall.
    Reporta si hay degradación monotónica.
    """
    rolling = rolling_metrics(audits, granularity=granularity, min_bucket_n=5)
    if len(rolling) < 4:
        return {"n_buckets": len(rolling), "note": "histórico insuficiente"}

    series = {
        "mae":           [r["mae"]           for r in rolling if r["mae"]           is not None],
        "ic50_coverage": [r["ic50_coverage"] for r in rolling],
        "ic90_coverage": [r["ic90_coverage"] for r in rolling],
        "mean_z":        [r["mean_z"]        for r in rolling if r["mean_z"]        is not None],
        "var_z":         [r["var_z"]         for r in rolling if r["var_z"]         is not None],
        "mean_sigma":    [r["mean_sigma"]    for r in rolling if r["mean_sigma"]    is not None],
    }
    trends = {name: mann_kendall(vals) for name, vals in series.items()}

    # Alarmas
    alerts = []
    if trends["mae"].get("trend") == "increasing":
        alerts.append("MAE creciendo: performance del modelo degradando")
    if trends["ic90_coverage"].get("trend") == "decreasing":
        alerts.append("IC90 coverage cayendo: filter cada vez más sobre-confiado")
    if trends["var_z"].get("trend") == "increasing":
        alerts.append("var_z creciendo: σ predictivo cada vez más sub-dimensionado")

    return {
        "granularity":  granularity,
        "n_buckets":    len(rolling),
        "rolling":      rolling,
        "trends":       trends,
        "alerts":       alerts,
    }
