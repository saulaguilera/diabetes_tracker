"""
bench/tuning/deep_diagnostics.py
─────────────────────────────────
Análisis profundo de innovations + covariance para diagnóstico de fallas
fisiológicas del modelo.

Niveles:

  1. Distributional (QQ, skew, kurt, heavy-tail detection)
  2. Temporal (ACF extendida, spectral density, rolling variance)
  3. Regime segmentation (fasting / post-meal / exercise / overnight)
  4. Covariance trajectory (eigenvalue evolution)

El propósito: identificar EN QUÉ régimen fisiológico el modelo falla.
Ejemplo: si var_z = 1.2 globalmente pero 2.5 en post-meal → el modelo
de carb absorption es sub-dimensionado → ajustar Q_COB / k_a / k_g.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Optional


# ── Distributional ─────────────────────────────────────────────────────

def moments(values: list[float]) -> dict:
    """Sample moments: mean, var, skew (Fisher), excess kurtosis."""
    n = len(values)
    if n < 4:
        return {"n": n}
    m = sum(values) / n
    centered = [v - m for v in values]
    m2 = sum(c ** 2 for c in centered) / n
    m3 = sum(c ** 3 for c in centered) / n
    m4 = sum(c ** 4 for c in centered) / n
    if m2 <= 0:
        return {"n": n, "mean": m, "var": 0, "std": 0, "skew": 0, "kurt": 0}
    std = math.sqrt(m2)
    skew = m3 / (std ** 3)
    kurt = m4 / (m2 ** 2) - 3.0   # Fisher excess kurtosis
    return {
        "n":    n,
        "mean": round(m,    4),
        "var":  round(m2,   4),
        "std":  round(std,  4),
        "skew": round(skew, 4),
        "kurt": round(kurt, 4),
    }


def jarque_bera(values: list[float]) -> dict:
    """
    Jarque-Bera test de normalidad.
    JB = n/6 × (S² + (K - 3)²/4)  ~ χ²(2) bajo H0=normal.
    P-value via aproximación exponencial (χ² con df=2).
    """
    mo = moments(values)
    if mo.get("n", 0) < 8:
        return {"n": mo.get("n", 0), "note": "muestra insuficiente"}
    n = mo["n"]
    S = mo["skew"]
    K_excess = mo["kurt"]   # ya es excess (K-3)
    JB = n / 6.0 * (S ** 2 + (K_excess ** 2) / 4.0)
    # χ²(2) CDF en forma cerrada: P(X<x) = 1 - exp(-x/2)
    p_value = math.exp(-JB / 2.0)
    return {
        "n":         n,
        "JB":        round(JB, 3),
        "p_value":   round(p_value, 4),
        "normal":    bool(p_value > 0.05),
        "skew":      S,
        "kurt":      K_excess,
    }


def heavy_tail_check(values: list[float], threshold: float = 3.0) -> dict:
    """
    Cuenta cuantas innovations exceden ±threshold·σ.
    Para z-scores normalizados (std≈1), esperamos ~0.27% sobre ±3σ.
    """
    if not values:
        return {"n": 0}
    n = len(values)
    n_out = sum(1 for v in values if abs(v) > threshold)
    expected = 0.0027 * n  # P(|Z| > 3) ≈ 0.27% para normal
    return {
        "n":          n,
        "threshold":  threshold,
        "n_outliers": n_out,
        "rate":       round(n_out / n, 4),
        "expected":   round(expected, 1),
        "ratio":      round(n_out / max(1e-6, expected), 2),
        "heavy_tailed": n_out > 3 * expected and n_out > 5,
    }


def qq_plot_data(values: list[float], n_quantiles: int = 50) -> dict:
    """
    Datos para QQ plot vs Normal estándar. Frontend plotea (theoretical, sample).
    """
    if len(values) < 10:
        return {"n": len(values), "theoretical": [], "sample": []}
    sorted_v = sorted(values)
    n = len(sorted_v)
    # Sample quantiles + theoretical (normal)
    theoretical = []
    sample = []
    for k in range(1, n_quantiles + 1):
        p = k / (n_quantiles + 1)
        # Inverse CDF normal estándar via approximation (Beasley-Springer-Moro)
        z_theo = _inv_normal_cdf(p)
        # Sample quantile
        idx = max(0, min(n - 1, int(round(p * (n - 1)))))
        theoretical.append(round(z_theo, 4))
        sample.append(round(sorted_v[idx], 4))
    return {"n": n, "theoretical": theoretical, "sample": sample}


def _inv_normal_cdf(p: float) -> float:
    """Inversa de la CDF normal estándar. Aproximación rápida (Acklam 2003)."""
    if p <= 0 or p >= 1:
        return 0.0
    a = [-3.969683028665376e+01,  2.209460984245205e+02,
         -2.759285104469687e+02,  1.383577518672690e+02,
         -3.066479806614716e+01,  2.506628277459239e+00]
    b = [-5.447609879822406e+01,  1.615858368580409e+02,
         -1.556989798598866e+02,  6.680131188771972e+01,
         -1.328068155288572e+01]
    p_low = 0.02425; p_high = 1.0 - p_low
    if p < p_low:
        q = math.sqrt(-2 * math.log(p))
        return (((((a[0]*q + a[1])*q + a[2])*q + a[3])*q + a[4])*q + a[5]) / \
               (((((b[0]*q + b[1])*q + b[2])*q + b[3])*q + b[4])*q + 1)
    if p > p_high:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((a[0]*q + a[1])*q + a[2])*q + a[3])*q + a[4])*q + a[5]) / \
                (((((b[0]*q + b[1])*q + b[2])*q + b[3])*q + b[4])*q + 1)
    q = p - 0.5
    r = q * q
    return q * (((((a[0]*r + a[1])*r + a[2])*r + a[3])*r + a[4])*r + a[5]) / \
              (((((b[0]*r + b[1])*r + b[2])*r + b[3])*r + b[4])*r + 1)


# ── Temporal ────────────────────────────────────────────────────────────

def rolling_variance(values: list[float], window: int = 30) -> list[float]:
    """
    Varianza en ventana móvil de tamaño `window`.
    Útil para detectar si σ_z crece o decrece con el tiempo.
    """
    if len(values) < window:
        return []
    out = []
    for i in range(window, len(values) + 1):
        slice_ = values[i - window:i]
        m = sum(slice_) / window
        var = sum((v - m) ** 2 for v in slice_) / (window - 1)
        out.append(round(var, 4))
    return out


def spectral_density(values: list[float], max_freq: int = 50) -> dict:
    """
    Periodograma simple (no Welch — sin scipy).
    Detecta periodicidades en innovations: si veo pico a freq=1/24h,
    significa que hay un ciclo diario no modelado (e.g., dawn no capturado).

    f_k = k / N, k = 0..N/2
    """
    n = len(values)
    if n < 16:
        return {"freqs": [], "power": []}
    # Demean para evitar peak en DC
    m = sum(values) / n
    x = [v - m for v in values]

    K = min(max_freq, n // 2)
    freqs, power = [], []
    for k in range(1, K + 1):
        omega = 2 * math.pi * k / n
        real = sum(x[t] * math.cos(omega * t) for t in range(n))
        imag = sum(x[t] * math.sin(omega * t) for t in range(n))
        p = (real ** 2 + imag ** 2) / n
        freqs.append(round(k / n, 5))
        power.append(round(p, 4))
    return {"freqs": freqs, "power": power, "n_samples": n}


# ── Regime segmentation ─────────────────────────────────────────────────

def segment_by_regime(innovations: list[dict],
                      cutoff_hours: int = 14) -> dict:
    """
    Segmenta la lista de innovations en regímenes fisiológicos basados en
    el contexto de cada timestamp (cargado de DB).

    Regímenes:
      - fasting    : sin meals en ±4h
      - post_meal  : meal en últimas 4h
      - exercise   : activity recent (±2h)
      - overnight  : hour ∈ [0, 6] sin meal/exercise
      - other      : resto

    Necesita consultar Meal/Activity en bench. Carga en bulk por ventana.
    """
    if not innovations:
        return {}
    from models import Meal, Activity
    t_min = min(i["ts"] for i in innovations)
    t_max = max(i["ts"] for i in innovations)
    meals = (Meal.query
             .filter(Meal.timestamp >= t_min - timedelta(hours=4),
                     Meal.timestamp <= t_max).all())
    acts  = (Activity.query
             .filter(Activity.timestamp >= t_min - timedelta(hours=2),
                     Activity.timestamp <= t_max).all())

    def _regime(ts: datetime) -> str:
        for a in acts:
            if 0 <= (ts - a.timestamp).total_seconds() / 60 <= 120:
                return "exercise"
        for m in meals:
            dt = (ts - m.timestamp).total_seconds() / 60.0
            if 0 <= dt <= 240:
                return "post_meal"
        if 0 <= ts.hour < 6:
            return "overnight"
        return "fasting"

    by_regime: dict[str, list] = {"fasting": [], "post_meal": [],
                                  "exercise": [], "overnight": []}
    for inn in innovations:
        r = _regime(inn["ts"])
        by_regime.setdefault(r, []).append(inn["innovation_z"])

    # Stats por régimen
    summary = {}
    for regime, zs in by_regime.items():
        if len(zs) < 5:
            summary[regime] = {"n": len(zs), "note": "muestra insuficiente"}
            continue
        mo = moments(zs)
        summary[regime] = {
            "n":      mo["n"],
            "mean":   mo["mean"],
            "var":    mo["var"],
            "skew":   mo["skew"],
            "kurt":   mo["kurt"],
            "verdict": _regime_verdict(mo),
        }
    return summary


def _regime_verdict(mo: dict) -> str:
    if abs(mo["mean"]) > 0.5:
        d = "subestima" if mo["mean"] > 0 else "sobreestima"
        return f"BIAS — el modelo {d} en este régimen"
    if mo["var"] > 1.5:
        return "SUB-DISPERSADO — σ predictivo demasiado chico en este régimen"
    if mo["var"] < 0.5:
        return "SOBRE-DISPERSADO — σ predictivo demasiado grande"
    return "OK — innovations consistentes con N(0,1)"


# ── Resumen agregado ────────────────────────────────────────────────────

def deep_innovation_analysis(innovations: list[dict]) -> dict:
    """
    Análisis profundo end-to-end de la secuencia de innovations.
    """
    if not innovations:
        return {"n": 0}
    z_vals = [i["innovation_z"] for i in innovations
              if i.get("innovation_z") is not None]
    if len(z_vals) < 10:
        return {"n": len(z_vals), "note": "muestra insuficiente"}

    result = {
        "n":              len(z_vals),
        "moments":        moments(z_vals),
        "jarque_bera":    jarque_bera(z_vals),
        "heavy_tails":    heavy_tail_check(z_vals),
        "qq_plot":        qq_plot_data(z_vals, n_quantiles=40),
        "rolling_var":    rolling_variance(z_vals, window=min(30, len(z_vals)//4)),
        "spectral":       spectral_density(z_vals, max_freq=40),
    }
    # Regime segmentation requiere acceso a DB
    try:
        result["by_regime"] = segment_by_regime(innovations)
    except Exception as exc:
        result["by_regime"] = {"error": str(exc)}
    return result
