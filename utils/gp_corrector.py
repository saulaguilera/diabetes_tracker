"""
utils/gp_corrector.py
──────────────────────
Gaussian Process sobre residuales del modelo físico de glucosa.

Problema que resuelve
---------------------
El modelo físico (IOB/COB/ROC/dawn) es bueno en promedio pero comete
errores sistemáticos que se autocorrelacionan en el tiempo: si subestimó
tu glucosa a las 14:00 y a las 14:15, probablemente también la subestime
a las 14:30. El GP aprende esa estructura de error y la usa para corregir
la predicción siguiente.

Diseño
------
  • Input X : minutos relativos a "ahora" → X_train ∈ [-480, 0], X_test ∈ {+30, +60}
  • Output y : error_30 (mg/dL) de predicciones resueltas
  • Kernel   : RBF (Squared Exponential) — errores correlacionados suavemente
                k(t1,t2) = σf² · exp(−½·(t1−t2)²/l²) + σn²·δ(t1,t2)
  • Hiperparámetros (θ = [log_l, log_σf, log_σn]):
       - l    = longitud de escala temporal (min) — cuánto duran las correlaciones
       - σf   = amplitud de señal — magnitud típica del error sistemático
       - σn   = ruido de observación — variabilidad del CGM + timing
  • Optimización : L-BFGS-B via scipy.optimize.minimize sobre log-verosimilitud marginal
  • Cache       : el GP se re-entrena cada _CACHE_TTL segundos para evitar latencia

Integración en el pipeline
--------------------------
    g_física  →  (+) corrección GP  →  g_final
    σ²_MC     →  (+) varianza GP   →  σ²_total  (suma en cuadratura)

Limitaciones conocidas
----------------------
  • RBF asume suavidad: puede subestimar la corrección en transiciones rápidas
    (comida reciente, ejercicio). Para estos casos la corrección se atenúa
    automáticamente (alta varianza GP → menor peso).
  • Necesita ≥ _MIN_TRAIN_POINTS datos resueltos para activarse.
  • La corrección se clampea a ±_MAX_CORRECTION_MGDL por seguridad.

Referencias
-----------
  - Rasmussen & Williams (2006) "Gaussian Processes for Machine Learning"
  - Pérez-Gandía et al. (2010) Diabetes Technol. Ther. — GP para glucosa
  - Frandes et al. (2017) Sci. Rep. — GP vs ARIMA en predicción CGM
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
from scipy.linalg import cholesky, solve_triangular
from scipy.optimize import minimize

logger = logging.getLogger("pmm.gp")

# ── Hiperparámetros iniciales (log-espacio) ───────────────────────────────────
_INIT_LOG_L   = np.log(20.0)    # longitud de escala inicial: 20 min
_INIT_LOG_SF  = np.log(12.0)    # amplitud de señal inicial: 12 mg/dL
_INIT_LOG_SN  = np.log(6.0)     # ruido inicial: 6 mg/dL

# Bounds para L-BFGS-B (log-espacio)
_BOUNDS = [
    (np.log(5.0),  np.log(120.0)),   # l ∈ [5, 120] min
    (np.log(3.0),  np.log(50.0)),    # σf ∈ [3, 50] mg/dL
    (np.log(2.0),  np.log(20.0)),    # σn ∈ [2, 20] mg/dL
]

# ── Parámetros operacionales ──────────────────────────────────────────────────
_MIN_TRAIN_POINTS = 10     # mínimo de puntos para activar el GP
_MAX_TRAIN_POINTS = 72     # máximo: ~6h a 5min de resolución
_LOOKBACK_HOURS   = 8      # ventana de entrenamiento hacia atrás
_MAX_CORRECTION   = 20.0   # clamp: el GP no corrige más de ±20 mg/dL
_MAX_VAR_RELIABLE = 300.0  # varianza máxima para considerar la corrección fiable
_JITTER           = 1e-5   # estabilidad numérica del Cholesky
_CACHE_TTL        = 300    # re-entrenar cada 5 minutos

# ── Cache en memoria (válido para el proceso actual) ──────────────────────────
_cache: dict = {
    "theta":      None,     # hiperparámetros optimizados [log_l, log_sf, log_sn]
    "X_train":    None,     # np.array (n,)
    "y_train":    None,     # np.array (n,)
    "trained_at": 0.0,      # time.monotonic() del último entrenamiento
    "n_points":   0,
}


# ── API pública ───────────────────────────────────────────────────────────────

def get_gp_correction(horizon_min: int) -> tuple[float, float]:
    """
    Retorna la corrección GP para el horizonte dado.

    Parameters
    ----------
    horizon_min : 30 o 60 — horizonte de predicción en minutos

    Returns
    -------
    (correction_mgdl, variance_mgdl2)
        correction : cuánto sumar a la predicción del modelo físico
        variance   : varianza posterior del GP (para propagar incertidumbre)

    Si el GP no está disponible (pocos datos, error numérico), retorna (0.0, 0.0).
    """
    try:
        _ensure_trained()
        if _cache["theta"] is None or _cache["n_points"] < _MIN_TRAIN_POINTS:
            return 0.0, 0.0

        X_test = np.array([float(horizon_min)])
        mu, var = _gp_predict(
            _cache["X_train"],
            _cache["y_train"],
            _cache["theta"],
            X_test,
        )

        correction = float(mu[0])
        variance   = float(max(0.0, var[0]))

        # Atenuar la corrección si la varianza es alta (GP no confiable)
        if variance > _MAX_VAR_RELIABLE:
            attenuation = _MAX_VAR_RELIABLE / variance   # 0→1
            correction  *= attenuation

        # Clamp de seguridad
        correction = float(np.clip(correction, -_MAX_CORRECTION, _MAX_CORRECTION))

        return correction, variance

    except Exception as exc:
        logger.debug(f"GP correction falló silenciosamente: {exc}")
        return 0.0, 0.0


def get_gp_status() -> dict:
    """
    Estado del GP para el endpoint diagnóstico /api/pmm/gp-status.
    """
    try:
        _ensure_trained()
        theta = _cache["theta"]
        n     = _cache["n_points"]

        if theta is None or n < _MIN_TRAIN_POINTS:
            return {
                "ok": True,
                "active": False,
                "reason": f"Datos insuficientes ({n}/{_MIN_TRAIN_POINTS} mínimo)",
                "n_train": n,
            }

        l  = float(np.exp(theta[0]))
        sf = float(np.exp(theta[1]))
        sn = float(np.exp(theta[2]))

        # Correcciones actuales para +30 y +60
        corr_30, var_30 = get_gp_correction(30)
        corr_60, var_60 = get_gp_correction(60)

        return {
            "ok":          True,
            "active":      True,
            "n_train":     n,
            "hyperparams": {
                "length_scale_min": round(l,  1),
                "signal_std_mgdl":  round(sf, 1),
                "noise_std_mgdl":   round(sn, 1),
            },
            "corrections": {
                "plus_30":  {
                    "correction": round(corr_30, 1),
                    "std":        round(float(np.sqrt(max(0, var_30))), 1),
                    "reliable":   var_30 <= _MAX_VAR_RELIABLE,
                },
                "plus_60":  {
                    "correction": round(corr_60, 1),
                    "std":        round(float(np.sqrt(max(0, var_60))), 1),
                    "reliable":   var_60 <= _MAX_VAR_RELIABLE,
                },
            },
            "interpretation": _interpret_gp(l, sf, sn, corr_30, corr_60),
            "trained_ago_s":   round(time.monotonic() - _cache["trained_at"]),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc), "active": False}


def invalidate_cache() -> None:
    """Fuerza re-entrenamiento en la próxima llamada."""
    _cache["trained_at"] = 0.0
    _cache["theta"] = None


# ── Entrenamiento ─────────────────────────────────────────────────────────────

def _ensure_trained() -> None:
    """Re-entrena el GP si el cache expiró."""
    now_mono = time.monotonic()
    if now_mono - _cache["trained_at"] < _CACHE_TTL and _cache["theta"] is not None:
        return   # cache válido

    X, y = _load_training_data()
    _cache["n_points"] = len(X)

    if len(X) < _MIN_TRAIN_POINTS:
        _cache["theta"]      = None
        _cache["trained_at"] = now_mono
        return

    theta_opt = _optimize_hyperparams(X, y)
    _cache["theta"]      = theta_opt
    _cache["X_train"]    = X
    _cache["y_train"]    = y
    _cache["trained_at"] = now_mono

    logger.info(
        f"GP re-entrenado: n={len(X)}, "
        f"l={np.exp(theta_opt[0]):.1f}min, "
        f"σf={np.exp(theta_opt[1]):.1f}, "
        f"σn={np.exp(theta_opt[2]):.1f} mg/dL"
    )


def _load_training_data() -> tuple[np.ndarray, np.ndarray]:
    """
    Carga los residuales de predicción resueltos de las últimas _LOOKBACK_HOURS horas.

    X : minutos relativos a now (negativos = pasado)
    y : error_30 observado (mg/dL)
    """
    try:
        from models import GlucosePrediction
        now    = datetime.now()
        cutoff = now - timedelta(hours=_LOOKBACK_HOURS)

        preds = (
            GlucosePrediction.query
            .filter(
                GlucosePrediction.resolved_30 == True,
                GlucosePrediction.error_30.isnot(None),
                GlucosePrediction.predicted_at >= cutoff,
            )
            .order_by(GlucosePrediction.predicted_at.desc())
            .limit(_MAX_TRAIN_POINTS)
            .all()
        )

        if not preds:
            return np.array([]), np.array([])

        X_list, y_list = [], []
        for p in preds:
            dt_min = (p.predicted_at - now).total_seconds() / 60.0
            X_list.append(dt_min)        # negativo: pasado
            y_list.append(p.error_30)    # error = real − predicho

        X = np.array(X_list, dtype=np.float64)
        y = np.array(y_list, dtype=np.float64)

        # Ordenar cronológicamente
        order = np.argsort(X)
        return X[order], y[order]

    except Exception as exc:
        logger.debug(f"GP data load error: {exc}")
        return np.array([]), np.array([])


def _optimize_hyperparams(X: np.ndarray, y: np.ndarray) -> np.ndarray:
    """
    Optimiza hiperparámetros del kernel via log-verosimilitud marginal.
    Múltiples restarts para evitar mínimos locales.
    """
    best_val   = np.inf
    best_theta = np.array([_INIT_LOG_L, _INIT_LOG_SF, _INIT_LOG_SN])

    # 3 restarts: punto inicial + 2 aleatorios con seed fijo
    rng = np.random.default_rng(42)
    inits = [
        best_theta.copy(),
        np.array([np.log(10.0), np.log(15.0), np.log(8.0)]),
        np.array([np.log(40.0), np.log(8.0),  np.log(4.0)]),
    ]

    for theta0 in inits:
        try:
            res = minimize(
                _neg_log_marginal_likelihood,
                theta0,
                args=(X, y),
                method="L-BFGS-B",
                bounds=_BOUNDS,
                options={"maxiter": 200, "ftol": 1e-8},
            )
            if res.success and res.fun < best_val:
                best_val   = res.fun
                best_theta = res.x
        except Exception:
            continue

    return best_theta


# ── Kernel y predicción GP ─────────────────────────────────────────────────────

def _rbf_kernel(
    X1: np.ndarray,
    X2: np.ndarray,
    log_l:  float,
    log_sf: float,
) -> np.ndarray:
    """K(X1, X2) = σf² · exp(−½·D²/l²)  — sin término de ruido."""
    l  = np.exp(log_l)
    sf = np.exp(log_sf)
    # Broadcasting seguro: (n,1) − (1,m) → (n,m)
    diff = X1[:, None] - X2[None, :]
    return sf ** 2 * np.exp(-0.5 * diff ** 2 / l ** 2)


def _neg_log_marginal_likelihood(
    theta: np.ndarray,
    X: np.ndarray,
    y: np.ndarray,
) -> float:
    """
    Negativo de la log-verosimilitud marginal GP — función objetivo para L-BFGS-B.

    log p(y|X,θ) = −½·yᵀK⁻¹y − ½·log|K| − n/2·log(2π)
    """
    log_l, log_sf, log_sn = theta
    sn = np.exp(log_sn)

    K = _rbf_kernel(X, X, log_l, log_sf)
    K += (sn ** 2 + _JITTER) * np.eye(len(X))

    try:
        L = cholesky(K, lower=True, check_finite=False)
    except Exception:
        return 1e10

    # Solve K·alpha = y via triangular systems (numericamente estable)
    alpha = solve_triangular(L, y,     lower=True,  check_finite=False)
    alpha = solve_triangular(L.T, alpha, lower=False, check_finite=False)

    lml = (
        -0.5 * float(y @ alpha)
        - float(np.sum(np.log(np.diag(L))))
        - 0.5 * len(y) * np.log(2 * np.pi)
    )
    return -lml


def _gp_predict(
    X_train: np.ndarray,
    y_train: np.ndarray,
    theta:   np.ndarray,
    X_test:  np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Posterior predictivo GP:
        μ*  = Ks · K⁻¹ · y
        σ²* = Kss − Ks · K⁻¹ · Ks ᵀ

    Usa solve_triangular (O(n²)) en lugar de invertir K (O(n³)).
    """
    log_l, log_sf, log_sn = theta
    sn = np.exp(log_sn)

    # Matriz de covarianza del training set (con ruido)
    K = _rbf_kernel(X_train, X_train, log_l, log_sf)
    K += (sn ** 2 + _JITTER) * np.eye(len(X_train))

    try:
        L = cholesky(K, lower=True, check_finite=False)
    except Exception:
        return np.zeros(len(X_test)), np.full(len(X_test), _MAX_VAR_RELIABLE + 1)

    # Cross-covarianza train × test (sin ruido — interpolamos la señal limpia)
    K_s = _rbf_kernel(X_test, X_train, log_l, log_sf)

    # α = K⁻¹ y  via Cholesky
    alpha = solve_triangular(L, y_train, lower=True,  check_finite=False)
    alpha = solve_triangular(L.T, alpha, lower=False, check_finite=False)

    # Media posterior
    mu = K_s @ alpha

    # Varianza posterior: diag(K**) − diag(Ks K⁻¹ Ksᵀ)
    v   = solve_triangular(L, K_s.T, lower=True, check_finite=False)
    K_ss = _rbf_kernel(X_test, X_test, log_l, log_sf)  # varianza prior en test
    var = np.diag(K_ss) - np.sum(v ** 2, axis=0)
    var = np.maximum(var, 0.0)   # evitar varianzas negativas por error numérico

    return mu, var


# ── Interpretación ────────────────────────────────────────────────────────────

def _interpret_gp(l: float, sf: float, sn: float, c30: float, c60: float) -> str:
    """Descripción legible del estado del GP."""
    parts = []

    if abs(c30) > 2 or abs(c60) > 2:
        direction = "sobreestimando" if c30 < -1 else "subestimando"
        parts.append(
            f"El modelo está {direction} tu glucosa sistemáticamente. "
            f"El GP corrige {c30:+.1f} mg/dL en +30min y {c60:+.1f} en +60min."
        )
    else:
        parts.append("El modelo físico está prediciendo bien — corrección GP mínima.")

    parts.append(
        f"Correlación de errores: {l:.0f} min · "
        f"Amplitud sistemática: ±{sf:.0f} mg/dL · "
        f"Ruido CGM estimado: ±{sn:.0f} mg/dL."
    )
    return " ".join(parts)
