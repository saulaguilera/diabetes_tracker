"""
bench/metrics/innovations.py
─────────────────────────────
Innovation diagnostics — la prueba científica de que el filtro está
correctamente especificado.

Teoría
------
Bajo un modelo perfectamente especificado, las innovations normalizadas
(z-scores) deben formar una secuencia ~ Normal(0, 1) i.i.d.:

  1. Mean ≈ 0          → no bias estructural
  2. Var  ≈ 1          → σ predictivo correctamente calibrado
                          (Var < 1 → sobre-confiado; Var > 1 → sub-confiado)
  3. Autocorr ≈ 0      → no info dejada en los residuales (whiteness)
  4. Normalidad        → modelo gaussiano apropiado

Si CUALQUIERA de estas falla, el filtro está mal especificado:
  - Mean off → bias en EGP o input event ignorado
  - Var off  → R o Q mal calibrado, o forma del modelo incorrecta
  - Autocorr → estado oculto faltante (illness, stress, sleep)
  - Cola pesada → noise no-gaussiano, missing states

Tests implementados
-------------------
  - Sample mean/var de innovation_z
  - ACF lag 1..k
  - Ljung-Box test (chi² approximation)
  - Sign test (¿innovations positivas y negativas balanceadas?)
  - Runs test (¿hay clusters de signo?)

Todo sin scipy.stats (usamos solo math) para mantener el módulo simple
y rápido. La aproximación chi² del Ljung-Box es estándar.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Optional


# ─── Loader ──────────────────────────────────────────────────────────────

def load_innovations(
    days:           int = 14,
    model_version:  Optional[str] = "ssm_v0_ukf6",
    exclude_rejected: bool = True,
) -> list[dict]:
    """
    Carga SSMInnovation rows como lista de dicts ordenados por timestamp.
    """
    from models import SSMInnovation
    cutoff = datetime.now() - timedelta(days=days)
    q = (SSMInnovation.query
         .filter(SSMInnovation.ts >= cutoff))
    if model_version:
        q = q.filter(SSMInnovation.model_version == model_version)
    if exclude_rejected:
        q = q.filter(SSMInnovation.rejected == False)
    rows = q.order_by(SSMInnovation.ts).all()
    return [{
        "ts":             r.ts,
        "y_obs":          r.y_obs,
        "y_pred":         r.y_pred,
        "innovation":     r.innovation,
        "sigma_pred":     r.sigma_pred,
        "innovation_z":   r.innovation_z,
        "log_likelihood": r.log_likelihood,
    } for r in rows]


# ─── ACF (autocorrelation function) ─────────────────────────────────────

def acf(values: list[float], max_lag: int = 20) -> list[float]:
    """
    Autocorrelation function unbiased estimate hasta max_lag.
    Implementación numericamente estable sin numpy (~O(n × k)).
    """
    n = len(values)
    if n < 2:
        return []
    mean = sum(values) / n
    var  = sum((v - mean) ** 2 for v in values) / n
    if var == 0:
        return [0.0] * (max_lag + 1)
    acfs = []
    for k in range(max_lag + 1):
        if k >= n:
            acfs.append(0.0)
            continue
        cov_k = sum((values[t] - mean) * (values[t + k] - mean) for t in range(n - k)) / n
        acfs.append(cov_k / var)
    return acfs


# ─── Ljung-Box test ─────────────────────────────────────────────────────

def ljung_box(values: list[float], lags: int = 10) -> dict:
    """
    Ljung-Box Q-test para autocorrelación residual hasta `lags`.

    H0: las primeras `lags` autocorrelaciones son cero (whiteness)
    Q = n(n+2) Σ ρ_k² / (n-k),  k=1..lags
    Bajo H0, Q ~ χ²(lags). Rechazamos H0 si p_value < α (típico 0.05).

    Aproximamos p_value usando la CDF chi² serie (Wilson-Hilferty 1931).
    """
    n = len(values)
    if n < lags + 5:
        return {"n": n, "lags": lags, "Q": None, "p_value": None,
                "white_noise": None, "note": "muestra insuficiente"}

    rho = acf(values, max_lag=lags)[1:]   # excluir lag 0
    Q = n * (n + 2) * sum((r ** 2) / (n - k - 1) for k, r in enumerate(rho))

    # Approx p-value via Wilson-Hilferty: (Q/lags)^(1/3) ~ N(1 - 2/(9k), 2/(9k))
    k = lags
    z = ((Q / k) ** (1.0 / 3.0) - (1.0 - 2.0 / (9.0 * k))) / math.sqrt(2.0 / (9.0 * k))
    p_value = 1.0 - _normal_cdf(z)

    return {
        "n":          n,
        "lags":       lags,
        "Q":          round(Q, 3),
        "p_value":    round(p_value, 4),
        "white_noise": bool(p_value > 0.05),
        "acf":        [round(r, 3) for r in rho],
    }


def _normal_cdf(z: float) -> float:
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


# ─── Sign test + Runs test ──────────────────────────────────────────────

def sign_balance(values: list[float]) -> dict:
    """
    Test de balance de signos: bajo H0 (cero bias) ~50% positivos.
    Usa aproximación normal de la binomial: z = (n_pos − n/2) / √(n/4).
    """
    n = len(values)
    if n < 10:
        return {"n": n, "note": "muestra insuficiente"}
    n_pos = sum(1 for v in values if v > 0)
    n_neg = sum(1 for v in values if v < 0)
    p_pos = n_pos / n
    z = (n_pos - n / 2) / math.sqrt(n / 4)
    p_value = 2 * (1 - _normal_cdf(abs(z)))
    return {
        "n":         n,
        "n_pos":     n_pos,
        "n_neg":     n_neg,
        "p_pos":     round(p_pos, 3),
        "z_score":   round(z, 3),
        "p_value":   round(p_value, 4),
        "balanced":  bool(p_value > 0.05),
    }


def runs_test(values: list[float]) -> dict:
    """
    Wald-Wolfowitz runs test: ¿hay clusters de signo (=trend) en los residuales?
    """
    n = len(values)
    if n < 20:
        return {"n": n, "note": "muestra insuficiente"}

    signs = [1 if v > 0 else 0 for v in values if v != 0]
    n = len(signs)
    if n < 20:
        return {"n": n, "note": "muestra insuficiente (todos cero?)"}
    n1 = sum(signs); n0 = n - n1
    if n1 == 0 or n0 == 0:
        return {"n": n, "note": "todos del mismo signo"}

    # Contar runs (cambios + 1)
    runs = 1
    for i in range(1, n):
        if signs[i] != signs[i - 1]:
            runs += 1

    # Bajo H0: E[R] = 2n1n0/n + 1, Var = 2n1n0(2n1n0 - n) / (n²(n-1))
    expected = 2.0 * n1 * n0 / n + 1.0
    var      = (2.0 * n1 * n0 * (2.0 * n1 * n0 - n)) / (n ** 2 * (n - 1))
    z = (runs - expected) / math.sqrt(var) if var > 0 else 0.0
    p_value = 2 * (1 - _normal_cdf(abs(z)))
    return {
        "n":          n,
        "runs":       runs,
        "expected":   round(expected, 1),
        "z_score":    round(z, 3),
        "p_value":    round(p_value, 4),
        "random":     bool(p_value > 0.05),
    }


# ─── Resumen de salud del filter ────────────────────────────────────────

def innovation_diagnostics(innovations: list[dict],
                           lags_lb: int = 10) -> dict:
    """
    Suite completa de diagnostics sobre la secuencia de innovation_z.

    Returns
    -------
    {
        n, mean_z, var_z, mean_innov, ...
        ljung_box: {Q, p_value, white_noise, acf},
        sign_test: {p_pos, z_score, balanced},
        runs_test: {runs, z_score, random},
        verdict:  "white" | "biased" | "autocorrelated" | "miscalibrated" | "no_data"
    }
    """
    if not innovations:
        return {"n": 0, "verdict": "no_data"}

    z_values = [i["innovation_z"] for i in innovations
                if i.get("innovation_z") is not None]
    raw      = [i["innovation"]   for i in innovations
                if i.get("innovation") is not None]
    if len(z_values) < 5:
        return {"n": len(z_values), "verdict": "no_data"}

    n = len(z_values)
    mean_z = sum(z_values) / n
    var_z  = sum((z - mean_z) ** 2 for z in z_values) / max(1, n - 1)
    mean_innov = sum(raw) / n
    var_innov  = sum((v - mean_innov) ** 2 for v in raw) / max(1, n - 1)

    # Min/Max para detectar covariance collapse/explosion
    abs_z_max = max(abs(z) for z in z_values)
    n_outliers_3sig = sum(1 for z in z_values if abs(z) > 3)

    lb     = ljung_box(z_values, lags=lags_lb)
    signs  = sign_balance(z_values)
    runs   = runs_test(z_values)

    # Verdict heurístico
    if lb.get("white_noise") is False:
        verdict = "autocorrelated"
    elif signs.get("balanced") is False:
        verdict = "biased"
    elif var_z < 0.5 or var_z > 2.0:
        verdict = "miscalibrated"
    elif n_outliers_3sig / n > 0.05:
        verdict = "heavy_tails"
    else:
        verdict = "white"

    return {
        "n":                 n,
        "mean_innovation":   round(mean_innov, 2),
        "var_innovation":    round(var_innov, 2),
        "mean_z":            round(mean_z, 3),
        "var_z":             round(var_z, 3),
        "abs_z_max":         round(abs_z_max, 2),
        "n_outliers_3sigma": n_outliers_3sig,
        "ljung_box":         lb,
        "sign_test":         signs,
        "runs_test":         runs,
        "verdict":           verdict,
        "note":              _interpret_verdict(verdict, mean_z, var_z),
    }


def _interpret_verdict(verdict: str, mean_z: float, var_z: float) -> str:
    if verdict == "white":
        return "Innovations ~ N(0,1) i.i.d. — filtro bien especificado."
    if verdict == "biased":
        d = "subestima sistemáticamente" if mean_z > 0 else "sobreestima sistemáticamente"
        return f"Bias estructural — el modelo {d} (mean_z={mean_z:+.2f})."
    if verdict == "autocorrelated":
        return "Autocorrelación residual significativa — falta estado oculto."
    if verdict == "miscalibrated":
        if var_z < 0.5:
            return f"Sub-dispersado (var_z={var_z:.2f}) — σ predictivo demasiado grande, R inflado."
        return f"Sobre-confiado (var_z={var_z:.2f}) — σ predictivo demasiado chico, Q/R subestimados."
    if verdict == "heavy_tails":
        return "Colas pesadas — outliers >3σ frecuentes, considerar noise no-gaussiano."
    return ""
