"""
Modelo Auto-Regresivo directo (AR-D) para predicción de glucosa CGM.

Aprende coeficientes lineales (OLS) usando los últimos n valores de glucosa
para predecir a +30 y +60 minutos. Complementa el modelo físico (IOB/COB/ROC)
capturando patrones individuales: rebounds postprandiales, momentum de la curva,
oscilaciones del sensor y patrones circadianos residuales.

Método: AR directo multi-horizonte (Pérez-Gandía et al. 2010)
  X[i] = [G(t), G(t-15), G(t-30), ..., G(t-75min), 1]   (6 lags + intercept)
  y_h[i] = G(t + h×15min)                                 (target)
  φ_h = lstsq(X, y_h)                                     (OLS)

Predicción en tiempo real:
  G_AR(t+h) = φ_h · [G_actual, G_-15, G_-30, ..., G_-75, 1]

Blending con Monte Carlo (módulo externo):
  σ_AR y σ_MC determinan el peso de cada modelo vía ponderación inversa-varianza.
"""

from __future__ import annotations

import json
import numpy as np
from datetime import datetime, timedelta

# ── Hiperparámetros del modelo ────────────────────────────────────────────────
_AR_N         = 6      # número de lags (6 × 15 min = 90 min de historia)
_STEP_MIN     = 15     # resolución de grilla para entrenamiento (min)
_HORIZONS     = {30: 2, 60: 4}   # horizonte_min → pasos de grilla
_AR_DAYS      = 21     # días de datos para entrenar
_MIN_SAMPLES  = 60     # pares (X,y) mínimos para confiar en el fit
_MAX_AGE_MIN  = 35     # lectura más antigua válida para predecir (min)
_FIT_COOLDOWN_H = 6   # reajustar como máximo cada N horas
_SETTINGS_KEY = "ar_model_params"


# ── Persistencia ──────────────────────────────────────────────────────────────

def _get_setting(k):
    from helpers import _get_setting as _gs
    return _gs(k)


def _set_setting(k, v):
    from helpers import _set_setting as _ss
    _ss(k, v)


def _load_params() -> dict | None:
    raw = _get_setting(_SETTINGS_KEY)
    if not raw:
        return None
    try:
        p = json.loads(raw)
        if "phi_30" in p and "phi_60" in p:
            return p
    except Exception:
        pass
    return None


def _save_params(p: dict):
    _set_setting(_SETTINGS_KEY, json.dumps(p))


# ── Grilla temporal ───────────────────────────────────────────────────────────

def _build_grid(readings: list) -> tuple[datetime | None, list]:
    """
    Proyecta lecturas CGM en una grilla uniforme de _STEP_MIN minutos.

    Cada slot de la grilla toma el valor de la lectura más cercana dentro de
    ±(STEP_MIN/2) minutos. Slots sin lectura cercana quedan como None.

    Returns:
        (t0, grid) — t0 es el timestamp del primer slot; grid[i] es el valor
        en t0 + i*STEP_MIN o None si hay brecha.
    """
    if not readings:
        return None, []

    t0   = readings[0].timestamp
    tend = readings[-1].timestamp
    n_steps = int((tend - t0).total_seconds() / 60 / _STEP_MIN) + 2

    grid = [None] * n_steps

    for r in readings:
        dt_min = (r.timestamp - t0).total_seconds() / 60
        idx = round(dt_min / _STEP_MIN)
        if 0 <= idx < n_steps and grid[idx] is None:
            grid[idx] = float(r.value_mgdl)

    return t0, grid


def _build_xy(grid: list, n: int, h_steps: int) -> tuple:
    """
    Construye matrices X e y para OLS directo.

    X[i] = [G(t), G(t-Δ), ..., G(t-(n-1)Δ), 1.0]   ← n lags + intercept
    y[i] = G(t + h_steps × Δ)                         ← target

    Omite filas con cualquier None en la ventana de lags o en el target.
    """
    X_rows, y_vals = [], []

    for i in range(n - 1, len(grid) - h_steps):
        window = grid[i - n + 1 : i + 1]   # n valores, desde el más antiguo
        target = grid[i + h_steps]

        if target is None or None in window:
            continue

        # Features: más reciente primero, luego intercept
        row = list(reversed(window)) + [1.0]
        X_rows.append(row)
        y_vals.append(target)

    if len(X_rows) < _MIN_SAMPLES:
        return None, None

    return np.array(X_rows, dtype=float), np.array(y_vals, dtype=float)


# ── Entrenamiento ─────────────────────────────────────────────────────────────

def fit_ar_model(n: int = _AR_N, days: int = _AR_DAYS) -> dict:
    """
    Ajusta el modelo AR(n) directo con los últimos `days` días de datos CGM.

    Usa OLS (numpy.linalg.lstsq) para cada horizonte (30 y 60 min).
    Guarda los coeficientes en UserSettings bajo 'ar_model_params'.

    Returns dict con stats del ajuste: ok, sigma_30/60, rmse_30/60, n_samples.
    """
    from models import GlucoseReading

    cutoff = datetime.now() - timedelta(days=days)
    readings = (
        GlucoseReading.query
        .filter(GlucoseReading.timestamp >= cutoff,
                GlucoseReading.value_mgdl > 20)
        .order_by(GlucoseReading.timestamp)
        .all()
    )

    if not readings:
        return {"ok": False, "error": "Sin datos CGM para entrenar el modelo AR"}

    _, grid = _build_grid(readings)

    fit_results = {}
    for h_min, h_steps in _HORIZONS.items():
        X, y = _build_xy(grid, n, h_steps)
        if X is None:
            fit_results[h_min] = {
                "ok": False,
                "error": f"Menos de {_MIN_SAMPLES} muestras válidas para +{h_min}min"
            }
            continue

        # OLS: φ = argmin ‖Xφ − y‖²
        phi, _, _, _ = np.linalg.lstsq(X, y, rcond=None)

        y_hat = X @ phi
        resid = y - y_hat
        sigma = float(np.std(resid))
        rmse  = float(np.sqrt(np.mean(resid ** 2)))
        mae   = float(np.mean(np.abs(resid)))

        fit_results[h_min] = {
            "ok":        True,
            "phi":       phi.tolist(),
            "n_samples": len(y),
            "sigma":     round(sigma, 2),
            "rmse":      round(rmse, 2),
            "mae":       round(mae, 2),
        }

    if not any(r.get("ok") for r in fit_results.values()):
        return {"ok": False, "error": "No se pudo ajustar ningún horizonte"}

    def _get(h, key):
        return fit_results.get(h, {}).get(key)

    params = {
        "fitted_at":    datetime.now().isoformat(),
        "n_lags":       n,
        "step_min":     _STEP_MIN,
        "days_used":    days,
        "total_grid":   len(grid),
        # Horizonte 30 min
        "phi_30":       _get(30, "phi"),
        "sigma_30":     _get(30, "sigma"),
        "rmse_30":      _get(30, "rmse"),
        "mae_30":       _get(30, "mae"),
        "n_samples_30": _get(30, "n_samples"),
        # Horizonte 60 min
        "phi_60":       _get(60, "phi"),
        "sigma_60":     _get(60, "sigma"),
        "rmse_60":      _get(60, "rmse"),
        "mae_60":       _get(60, "mae"),
        "n_samples_60": _get(60, "n_samples"),
    }
    _save_params(params)

    return {
        "ok":           True,
        "fitted_at":    params["fitted_at"],
        "n_lags":       n,
        "n_samples_30": _get(30, "n_samples") or 0,
        "n_samples_60": _get(60, "n_samples") or 0,
        "sigma_30":     _get(30, "sigma"),
        "rmse_30":      _get(30, "rmse"),
        "sigma_60":     _get(60, "sigma"),
        "rmse_60":      _get(60, "rmse"),
        "days_used":    days,
    }


def maybe_fit_ar_model() -> dict | None:
    """
    Ajusta el modelo AR solo si han pasado más de _FIT_COOLDOWN_H horas
    desde el último entrenamiento. Pensado para llamarse en cada sync sin
    saturar el sistema.
    """
    params = _load_params()
    if params:
        try:
            fitted_at = datetime.fromisoformat(params["fitted_at"])
            if (datetime.now() - fitted_at).total_seconds() < _FIT_COOLDOWN_H * 3600:
                return None   # todavía está fresco
        except Exception:
            pass

    return fit_ar_model()


# ── Predicción en tiempo real ─────────────────────────────────────────────────

def get_ar_prediction(horizon_min: int) -> dict | None:
    """
    Predice glucosa a +horizon_min usando el modelo AR entrenado y las
    últimas lecturas de la DB.

    Returns:
        {ok, g_pred, sigma, n_lags, last_age_min, span_min}  o  None si
        el modelo no está entrenado o los datos son insuficientes/obsoletos.
    """
    params = _load_params()
    if not params:
        return None

    phi_key   = f"phi_{horizon_min}"
    sigma_key = f"sigma_{horizon_min}"
    phi_list  = params.get(phi_key)
    sigma     = params.get(sigma_key)

    if not phi_list or sigma is None:
        return None

    phi    = np.array(phi_list, dtype=float)
    n_lags = params.get("n_lags", _AR_N)

    # ── Obtener las últimas n_lags lecturas de la DB ──────────────────────
    from models import GlucoseReading

    cutoff = datetime.now() - timedelta(minutes=n_lags * _STEP_MIN * 4)
    recent_readings = (
        GlucoseReading.query
        .filter(GlucoseReading.timestamp >= cutoff,
                GlucoseReading.value_mgdl > 20)
        .order_by(GlucoseReading.timestamp.desc())
        .limit(n_lags * 3)
        .all()
    )
    recent_readings = list(reversed(recent_readings))   # orden cronológico

    if not recent_readings:
        return None

    # La lectura más reciente no puede ser muy antigua
    last_age_min = (datetime.now() - recent_readings[-1].timestamp).total_seconds() / 60
    if last_age_min > _MAX_AGE_MIN:
        return None

    # Necesitamos al menos n_lags lecturas
    if len(recent_readings) < n_lags:
        return None

    # Tomar las últimas n_lags lecturas
    used = recent_readings[-n_lags:]

    # Verificar que la ventana no sea demasiado amplia (datos muy dispersos)
    # Los coeficientes se entrenaron asumiendo ~15 min entre muestras
    span_min = (used[-1].timestamp - used[0].timestamp).total_seconds() / 60
    max_span = (n_lags - 1) * _STEP_MIN * 4   # hasta 4× la resolución de entrenamiento
    if span_min > max_span:
        return None

    # Features: [G(t), G(t-1), ..., G(t-(n-1)), 1]  más reciente primero
    values   = [r.value_mgdl for r in reversed(used)]   # [más_nuevo, ..., más_viejo]
    features = np.array(values + [1.0], dtype=float)

    g_pred = float(phi @ features)
    # Clip a rango fisiológico
    g_pred = max(40.0, min(400.0, g_pred))

    return {
        "ok":          True,
        "g_pred":      round(g_pred, 1),
        "sigma":       sigma,
        "n_lags":      n_lags,
        "last_age_min": round(last_age_min, 1),
        "span_min":    round(span_min, 0),
    }


def get_ar_status() -> dict:
    """Devuelve el estado actual del modelo AR (para diagnóstico)."""
    params = _load_params()
    if not params:
        return {"entrenado": False, "mensaje": "Modelo no entrenado aún"}

    fitted_at = params.get("fitted_at", "?")
    try:
        age_h = (datetime.now() - datetime.fromisoformat(fitted_at)).total_seconds() / 3600
        age_str = f"hace {age_h:.1f}h"
    except Exception:
        age_str = "?"

    return {
        "entrenado":    True,
        "fitted_at":    fitted_at,
        "age":          age_str,
        "n_lags":       params.get("n_lags"),
        "step_min":     params.get("step_min"),
        "days_used":    params.get("days_used"),
        "n_samples_30": params.get("n_samples_30"),
        "n_samples_60": params.get("n_samples_60"),
        "sigma_30":     params.get("sigma_30"),
        "rmse_30":      params.get("rmse_30"),
        "sigma_60":     params.get("sigma_60"),
        "rmse_60":      params.get("rmse_60"),
    }
