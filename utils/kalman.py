"""
utils/kalman.py — Filtro de Kalman para estimación de glucemia y ROC.

Estado del filtro
-----------------
El filtro rastrea un vector de dos variables:
    x = [G, v]
    G : glucemia estimada (mg/dL)
    v : velocidad de cambio — ROC (mg/dL/min)

Cada 5 minutos, cuando llega una nueva lectura CGM:
  1. PREDECIR  — propagar el estado usando el modelo de velocidad constante
  2. CORREGIR  — combinar predicción con la medición real via ganancia K

Parámetros clave
----------------
Q_G = 4.0   — ruido del proceso en G (mg²/dL²/min)
              → La glucosa puede cambiar ~2 mg/dL/min "espontáneamente" (contra-regulación,
                hepatic glucose output, etc.) — incertidumbre que crece con el tiempo.
Q_V = 0.01  — ruido del proceso en v (mg²/dL²/min³)
              → El ROC cambia lentamente entre lecturas.
R   = 225.0 — ruido de la medición (mg²/dL²)
              → Abbott Libre 3 MARD ≈ 9%, std empírico ≈ 15 mg/dL → R = 15² = 225.

Implementación sin numpy
------------------------
Las matrices 2×2 se representan como listas de listas y las operaciones
se implementan explícitamente — sin dependencias externas.

Persistencia del estado
-----------------------
El estado [G, v, P, last_t] se guarda en UserSettings (clave 'kalman_state')
como JSON para sobrevivir reinicios del servidor entre lecturas CGM.

Referencias
-----------
Reifman & Stenger (2007) Diabetes Technol Ther — Kalman para CGM
Palerm et al. (2009) — estimación continua de glucosa
Welch & Bishop (2006) — "An Introduction to the Kalman Filter"
"""
from __future__ import annotations

import json
import math
from datetime import datetime
from typing import Optional

# ── Parámetros del filtro ─────────────────────────────────────────────────────
_Q_G  = 4.0     # mg²/dL²/min  — varianza del cambio de glucemia por minuto
_Q_V  = 0.01    # mg²/dL²/min³ — varianza del cambio de ROC por minuto
_R    = 225.0   # mg²/dL²      — varianza del ruido del sensor Libre (~15 mg/dL std)

# Covarianza inicial — alta incertidumbre al arrancar
_P0_G = 400.0   # (std ≈ 20 mg/dL para la glucemia inicial)
_P0_V = 1.0     # (std ≈ 1 mg/dL/min para el ROC inicial)

# Clave en UserSettings para persistir el estado
_KEY = "kalman_state"

# Si el filtro no recibió lecturas en más de X minutos, se reinicia
_MAX_GAP_MIN = 360   # 6 horas


# ── Helpers de matrices 2×2 ───────────────────────────────────────────────────

def _m_add(A: list, B: list) -> list:
    """Suma de dos matrices 2×2."""
    return [
        [A[0][0]+B[0][0], A[0][1]+B[0][1]],
        [A[1][0]+B[1][0], A[1][1]+B[1][1]],
    ]


def _m_scale(A: list, s: float) -> list:
    """Escala una matriz 2×2 por un escalar."""
    return [[A[i][j]*s for j in range(2)] for i in range(2)]


# ── Ciclo del filtro ──────────────────────────────────────────────────────────

def _new_state(G: float, v: float = 0.0) -> dict:
    """Crea un estado inicial con incertidumbre alta (arranque frío)."""
    return {
        "G":      G,
        "v":      v,
        "P":      [[_P0_G, 0.0], [0.0, _P0_V]],
        "last_t": None,
    }


def _predict(state: dict, dt: float) -> dict:
    """
    PASO 1 — Predicción: propaga el estado dt minutos hacia adelante.

    Modelo de velocidad constante:
        G_pred = G + v × dt
        v_pred = v

    Propagación de incertidumbre:
        F = [[1, dt], [0, 1]]
        P_pred = F × P × Fᵀ + Q × dt

    La incertidumbre P crece con el tiempo porque el modelo no es perfecto.
    """
    G, v = state["G"], state["v"]
    P    = state["P"]

    G_pred = G + v * dt
    v_pred = v

    # F @ P (con F = [[1,dt],[0,1]])
    fp00 = P[0][0] + dt * P[1][0]
    fp01 = P[0][1] + dt * P[1][1]
    fp10 = P[1][0]
    fp11 = P[1][1]

    # (F @ P) @ Fᵀ (con Fᵀ = [[1,0],[dt,1]])
    fPFt = [
        [fp00 + dt * fp01,  fp01],
        [fp10 + dt * fp11,  fp11],
    ]

    # + Q × dt  (proceso escalado por el intervalo de tiempo)
    Q_dt = [[_Q_G * dt, 0.0], [0.0, _Q_V * dt]]
    P_pred = _m_add(fPFt, Q_dt)

    return {"G": G_pred, "v": v_pred, "P": P_pred, "last_t": state.get("last_t")}


def _update(state: dict, z: float) -> dict:
    """
    PASO 2 — Corrección: actualiza el estado con la medición z (mg/dL).

    H = [1, 0]  (solo medimos G directamente)

    Innovación:
        y = z - G_pred

    Covarianza de la innovación:
        S = P[0][0] + R

    Ganancia de Kalman:
        K = [P[0][0]/S, P[1][0]/S]

        K[0] ≈ 1 si P[0][0] >> R  → confiamos en el sensor
        K[0] ≈ 0 si P[0][0] << R  → confiamos en el modelo

    Corrección del estado:
        G_new = G_pred + K[0] × y
        v_new = v_pred + K[1] × y  ← el ROC también se corrige

    Actualización de covarianza:
        P_new = (I − K⊗H) × P_pred
    """
    G, v = state["G"], state["v"]
    P    = state["P"]

    innov = z - G                       # innovación: qué tan lejos estaba la predicción
    S     = P[0][0] + _R               # covarianza de la innovación
    k0    = P[0][0] / S                # ganancia Kalman para G
    k1    = P[1][0] / S                # ganancia Kalman para v

    G_new = G + k0 * innov
    v_new = v + k1 * innov

    # P_new = (I - K@H) @ P  con K@H = [[k0,0],[k1,0]]
    P_new = [
        [(1 - k0) * P[0][0],          (1 - k0) * P[0][1]],
        [P[1][0] - k1 * P[0][0],      P[1][1] - k1 * P[0][1]],
    ]

    return {"G": G_new, "v": v_new, "P": P_new, "last_t": state.get("last_t")}


# ── API pública ───────────────────────────────────────────────────────────────

def load_state() -> Optional[dict]:
    """Carga el estado persistido desde UserSettings."""
    from helpers import _get_setting
    raw = _get_setting(_KEY)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


def save_state(state: dict) -> None:
    """Persiste el estado en UserSettings."""
    from helpers import _set_setting
    # Solo guardar los campos esenciales (sin sigma calculados)
    to_save = {k: state[k] for k in ("G", "v", "P", "last_t") if k in state}
    _set_setting(_KEY, json.dumps(to_save))


def _annotate(state: dict) -> dict:
    """Añade sigma_G y sigma_v calculados desde P."""
    state["sigma_G"] = round(math.sqrt(max(0.0, state["P"][0][0])), 1)
    state["sigma_v"] = round(math.sqrt(max(0.0, state["P"][1][1])), 4)
    return state


def update_with_reading(
    glucose_mgdl: float,
    timestamp:    datetime,
    save:         bool = True,
) -> dict:
    """
    Ciclo completo: carga estado → predice hasta timestamp → corrige con z → guarda.

    Llamar cada vez que llega una nueva lectura CGM (sync Libre o entrada manual).

    Parameters
    ----------
    glucose_mgdl : valor del sensor (mg/dL)
    timestamp    : momento de la lectura
    save         : si True, persiste el estado actualizado

    Returns
    -------
    Estado actualizado con sigma_G, sigma_v y kalman_gain.
    """
    state   = load_state()
    now_iso = timestamp.isoformat()

    if state is None or state.get("last_t") is None:
        # Primer arranque: inicializar con la lectura actual
        state = _new_state(G=glucose_mgdl)
        state = _update(state, glucose_mgdl)   # primera corrección
        state["last_t"] = now_iso
    else:
        last_t = datetime.fromisoformat(state["last_t"])
        dt     = (timestamp - last_t).total_seconds() / 60.0

        if dt <= 0.5:
            # Lectura casi simultánea o duplicada — solo actualizar, sin predecir
            state = _update(state, glucose_mgdl)
        elif dt > _MAX_GAP_MIN:
            # Gap mayor a 6 h → reiniciar (datos perdidos, estado obsoleto)
            state = _new_state(G=glucose_mgdl)
            state = _update(state, glucose_mgdl)
        else:
            # Ciclo normal: predecir → corregir
            state = _predict(state, dt)
            state = _update(state, glucose_mgdl)

        state["last_t"] = now_iso

    # Calcular ganancia de Kalman actual (para diagnóstico)
    S = state["P"][0][0] + _R
    state["kalman_gain"] = round(state["P"][0][0] / S, 3) if S > 0 else 0.0

    _annotate(state)

    if save:
        save_state(state)

    return state


def get_current_estimate(propagate: bool = True) -> Optional[dict]:
    """
    Retorna la estimación actual del filtro.

    Si propagate=True, propaga el estado hasta 'ahora' sin corrección
    (la incertidumbre P crece — refleja que el filtro está "prediciendo").

    Returns None si el filtro no está inicializado o el estado es demasiado viejo.
    """
    state = load_state()
    if state is None or state.get("last_t") is None:
        return None

    last_t = datetime.fromisoformat(state["last_t"])
    dt     = (datetime.now() - last_t).total_seconds() / 60.0

    if dt > _MAX_GAP_MIN:
        return None   # estado demasiado viejo — no usar

    if propagate and dt > 0.5:
        state = _predict(state, dt)

    state["dt_since_update"] = round(dt, 1)
    S = state["P"][0][0] + _R
    state["kalman_gain"] = round(state["P"][0][0] / S, 3) if S > 0 else 0.0
    _annotate(state)

    return state


def get_filter_info() -> dict:
    """
    Información diagnóstica del filtro para el panel de Calibración.
    """
    state = load_state()
    if state is None:
        return {"initialized": False}

    last_t = state.get("last_t")
    if last_t:
        dt = (datetime.now() - datetime.fromisoformat(last_t)).total_seconds() / 60.0
        fresco = dt < _MAX_GAP_MIN
    else:
        dt     = None
        fresco = False

    S = state["P"][0][0] + _R
    K = state["P"][0][0] / S if S > 0 else 0.0

    return {
        "initialized":   True,
        "fresco":        fresco,
        "G_est":         round(state.get("G", 0), 1),
        "v_est":         round(state.get("v", 0), 3),
        "sigma_G":       round(math.sqrt(max(0, state["P"][0][0])), 1),
        "sigma_v":       round(math.sqrt(max(0, state["P"][1][1])), 4),
        "kalman_gain":   round(K, 3),
        "last_t":        last_t,
        "dt_min":        round(dt, 1) if dt is not None else None,
        "params":        {"Q_G": _Q_G, "Q_V": _Q_V, "R": _R},
    }
