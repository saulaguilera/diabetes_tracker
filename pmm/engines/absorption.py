"""
pmm/engines/absorption.py
──────────────────────────
Aprendizaje adaptivo de la velocidad de absorción de carbohidratos.

Problema
--------
El modelo 2-compartimentos usa constantes poblacionales (k_a) para la
velocidad de vaciado gástrico. En la práctica, cada persona absorbe
los alimentos a velocidades distintas de la media:

    k_a_personal = k_a_default × speed_factor

Con speed_factor > 1 el alimento se absorbe más rápido que el modelo
predice (glucosa sube antes); speed_factor < 1, más lento (glucosa
sube más tarde o de forma más gradual).

Cómo se aprende speed_factor
----------------------------
De episodios limpios comida + bolo:

  1. Observar G_obs a t=60min post-comida
  2. Predecir G_pred(60) con el modelo usando k_a_default y PMM ISF/ICR
  3. Computar la fracción de absorción "observada" vs "modelada"

     true_abs_60  = (G_obs_60 − G0 + ΔIOB_60 × ISF) / (carbs × ISF/ICR)
     model_abs_60 = 1 − COB_frac(60, k_a_default)

     speed_factor_obs = true_abs_60 / model_abs_60

  4. Si speed_factor_obs > 1 → el usuario absorbió más de lo esperado
     en los primeros 60min → k_a más alto del que asume el modelo

  5. Actualizar prior Bayesiano por bucket de velocidad (FAST/MED/SLOW)

Buckets de velocidad (mapeo desde categoría de comida)
-----------------------------------------------------
  FAST  → Dulces/Postres, Bebidas, Frutas           (GI > 70 por defecto)
  SLOW  → Legumbres, Verduras                       (GI < 55)
  MED   → Cereales, Snacks, Carnes, Lácteos y resto (GI 55-70)

Prior (antes de observaciones personales)
-----------------------------------------
  speed_factor ~ N(1.0, σ=0.25)
  Significa: creemos que el modelo poblacional es razonablemente correcto
  pero con ±25% de incertidumbre intrínseca.

Sigma mínimo: 0.08 (no colapsar a certeza absoluta — hay variabilidad real).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger("pmm.absorption")

# ── Parámetros del prior ──────────────────────────────────────────────────────
_PRIOR_MU    = 1.0    # speed_factor esperado sin observaciones
_PRIOR_SIGMA = 0.25   # incertidumbre del prior (~±25%)
_SIGMA_FLOOR = 0.08   # sigma mínimo (evita colapso a certeza absoluta)
_MIN_OBS     = 3      # mínimo de obs para confiar en el posterior
_OBS_WINDOW_MIN = 55  # ventana post-comida para observar (±5 min de 60min)
_MAX_SPEED_FACTOR = 3.0   # cap superior (evitar valores fisiopatológicos)
_MIN_SPEED_FACTOR = 0.2   # cap inferior

# ── Mapeo categoría → bucket ──────────────────────────────────────────────────
# 'FAST'  → vaciado gástrico rápido (high GI)
# 'MED'   → por defecto
# 'SLOW'  → vaciado gástrico lento (low GI, alto en fibra/grasa)
_CATEGORY_TO_BUCKET: dict[str, str] = {
    "Dulces/Postres": "FAST",
    "Bebidas":        "FAST",
    "Frutas":         "FAST",
    "Cereales":       "MED",
    "Comida rápida":  "MED",
    "Snacks/Botanas": "MED",
    "Sopas/Caldos":   "MED",
    "Lácteos":        "MED",
    "Carnes":         "MED",
    "Pescados":       "MED",
    "Huevos":         "MED",
    "Alcohol":        "MED",
    "Verduras":       "SLOW",
    "Legumbres":      "SLOW",
}
_BUCKET_DEFAULT = "MED"

# Nombres de param_name en PMMParameter
_PARAM_NAME = {
    "FAST": "KASPEED_FAST",
    "MED":  "KASPEED_MED",
    "SLOW": "KASPEED_SLOW",
}

# k_a base del modelo (min⁻¹) por bucket — espeja utils/kinetics.py
_K_A_DEFAULT = {
    "FAST": 0.040,
    "MED":  0.025,
    "SLOW": 0.015,
}


# ── API pública ───────────────────────────────────────────────────────────────

def get_speed_factor(categoria: Optional[str]) -> float:
    """
    Devuelve el speed_factor PMM para la categoría dada.

    Si hay suficientes observaciones personales (≥3), devuelve el μ posterior.
    Si no, devuelve 1.0 (sin modificar el modelo poblacional).
    """
    bucket = _CATEGORY_TO_BUCKET.get(categoria or "", _BUCKET_DEFAULT)
    param_name = _PARAM_NAME[bucket]
    try:
        from pmm.core.parameter_store import load_parameter
        state = load_parameter(param_name, context_block=-1)
        if state.n_obs >= _MIN_OBS:
            return round(max(_MIN_SPEED_FACTOR, min(_MAX_SPEED_FACTOR, state.mu)), 3)
    except Exception:
        pass
    return 1.0


def get_all_speed_factors() -> dict:
    """
    Devuelve el estado de los speed factors para todos los buckets.
    Útil para el endpoint /api/pmm/absorption.
    """
    result = {}
    for bucket, param_name in _PARAM_NAME.items():
        try:
            from pmm.core.parameter_store import load_parameter
            state = load_parameter(param_name, context_block=-1)
            result[bucket] = {
                "mu":          round(state.mu,    3),
                "sigma":       round(state.sigma, 3),
                "n_obs":       state.n_obs,
                "ci_95_lo":    round(state.mu - 1.96 * state.sigma, 3),
                "ci_95_hi":    round(state.mu + 1.96 * state.sigma, 3),
                "confidence":  state.confidence,
                "source":      "learned" if state.n_obs >= _MIN_OBS else "prior",
                "k_a_default": _K_A_DEFAULT[bucket],
                "k_a_personal": round(_K_A_DEFAULT[bucket] * state.mu, 4),
                "interpretation": _interpret_speed(state.mu, state.n_obs),
            }
        except Exception as exc:
            result[bucket] = {
                "mu": 1.0, "sigma": _PRIOR_SIGMA, "n_obs": 0,
                "source": "prior", "error": str(exc),
            }
    return result


def load_absorption_episodes(days: int = 180) -> list[dict]:
    """
    Carga episodios de comida con potencial de aprender speed_factor.

    Criterios:
      - Comida con carbs > 10g
      - Bolo asociado en ±30min
      - Lectura de glucosa disponible a t0 y a t=55-65min
      - Sin corrección en la ventana 0-90min

    Retorna lista de dicts con toda la info necesaria para evaluate_absorption_episode().
    """
    from models import GlucoseReading, InsulinDose, Meal
    from sqlalchemy import or_

    cutoff = datetime.now() - timedelta(days=days)

    meals = (
        Meal.query
        .filter(
            Meal.timestamp >= cutoff,
            Meal.carbs_g >= 10.0,
        )
        .order_by(Meal.timestamp)
        .all()
    )

    all_boluses = InsulinDose.query.filter(
        InsulinDose.type == "bolus",
        InsulinDose.timestamp >= cutoff,
    ).all()

    all_readings = GlucoseReading.query.filter(
        GlucoseReading.timestamp >= cutoff,
    ).order_by(GlucoseReading.timestamp).all()

    episodes = []
    for meal in meals:
        # Bolo de comida en ±30min
        bolus = _find_meal_bolus(meal, all_boluses)
        if not bolus:
            continue
        # Glucosa al inicio (t0 = meal time ±15min)
        g0_read = _nearest_reading_list(meal.timestamp, all_readings, window_min=15)
        if not g0_read:
            continue
        # Glucosa a t=60min (55-65min)
        t60 = meal.timestamp + timedelta(minutes=60)
        g60_read = _nearest_reading_list(t60, all_readings, window_min=8)
        if not g60_read:
            continue
        # Sin corrección en la ventana
        has_correction = any(
            b for b in all_boluses
            if b.purpose == "correccion"
            and meal.timestamp <= b.timestamp <= meal.timestamp + timedelta(minutes=90)
            and b.id != bolus.id
        )
        if has_correction:
            continue

        episodes.append({
            "meal_id":    meal.id,
            "meal_time":  meal.timestamp,
            "categoria":  meal.categoria,
            "carbs":      meal.carbs_g,
            "fat":        meal.fat_g or 0.0,
            "bolus_id":   bolus.id,
            "bolus_units": bolus.units,
            "g0":         g0_read.value_mgdl,
            "g60":        g60_read.value_mgdl,
            "t0":         g0_read.timestamp,
            "t60":        g60_read.timestamp,
        })

    return episodes


def evaluate_absorption_episode(
    ep: dict,
    isf_mu: float,
    icr_mu: float,
) -> dict:
    """
    A partir de un episodio de comida, estima el speed_factor observado.

    Retorna
    -------
    {
        usable        : bool
        speed_factor  : float | None   — speed_factor observado
        obs_sigma     : float | None   — ruido estimado
        quality_score : float
        bucket        : str
        skip_reason   : str | None
    }
    """
    from utils.kinetics import _cob_fraction_2comp, current_iob, _DEFAULT_PEAK_MIN, _DEFAULT_DIA_MIN
    from helpers import _get_setting

    result = {
        "usable": False,
        "speed_factor": None,
        "obs_sigma": None,
        "quality_score": 0.0,
        "bucket": _CATEGORY_TO_BUCKET.get(ep.get("categoria") or "", _BUCKET_DEFAULT),
        "skip_reason": None,
    }

    carbs = ep.get("carbs", 0.0)
    g0    = ep.get("g0", 0.0)
    g60   = ep.get("g60", 0.0)
    fat   = ep.get("fat", 0.0)

    # ── Validaciones básicas ──────────────────────────────────────────────
    if not icr_mu or icr_mu <= 0:
        result["skip_reason"] = "ICR no disponible"
        return result

    if not isf_mu or isf_mu <= 0:
        result["skip_reason"] = "ISF no disponible"
        return result

    max_g_rise = carbs * isf_mu / icr_mu
    if max_g_rise < 10:
        result["skip_reason"] = "Comida demasiado pequeña para observar absorción"
        return result

    # ── ΔIOB en los primeros 60min ────────────────────────────────────────
    try:
        from models import InsulinDose
        t0 = ep["t0"]
        t60_ts = ep["t60"]

        dia_raw = _get_setting("dia_min")
        dia_min = int(float(dia_raw)) if dia_raw else _DEFAULT_DIA_MIN

        boluses_window = InsulinDose.query.filter(
            InsulinDose.type == "bolus",
            InsulinDose.timestamp >= t0 - timedelta(hours=dia_min / 60 + 1),
            InsulinDose.timestamp <= t60_ts,
        ).all()

        iob_t0  = current_iob(boluses_window, at_time=t0,
                              peak_min=_DEFAULT_PEAK_MIN, dia_min=dia_min)
        iob_t60 = current_iob(boluses_window, at_time=t60_ts,
                              peak_min=_DEFAULT_PEAK_MIN, dia_min=dia_min)
        d_iob = iob_t0 - iob_t60   # insulina consumida en 60min (U, > 0)
    except Exception:
        d_iob = 0.0

    # ── Fracción observada de absorción ──────────────────────────────────
    # G_obs_60 = G0 + carbs_absorbed * ISF/ICR - d_iob * ISF
    # → carbs_absorbed = (G_obs_60 - G0 + d_iob * ISF) / (ISF/ICR)
    g_rise = g60 - g0
    insulin_effect_60 = d_iob * isf_mu

    numerator = g_rise + insulin_effect_60    # carbos netos que subieron glucosa
    carbs_absorbed_obs = numerator / (isf_mu / icr_mu)
    true_abs_frac = carbs_absorbed_obs / carbs

    # ── Fracción modelada de absorción (k_a_default para este bucket) ────
    bucket  = result["bucket"]
    k_a_def = _K_A_DEFAULT[bucket]
    # La grasa lentifica el vaciado gástrico — ajuste simplificado
    fat_factor = 1.0 / (1.0 + fat * 0.015)
    k_a_eff    = k_a_def * fat_factor

    from utils.kinetics import _K_GUT
    t_elapsed = (ep["t60"] - ep["meal_time"]).total_seconds() / 60
    model_abs_frac = 1.0 - _cob_fraction_2comp(t_elapsed, k_a_eff, _K_GUT)

    if model_abs_frac < 0.05:
        result["skip_reason"] = "Fracción modelada insuficiente para estimar ratio"
        return result

    # ── Speed factor observado ────────────────────────────────────────────
    speed_factor_obs = true_abs_frac / model_abs_frac

    if not (0.1 <= speed_factor_obs <= 4.0):
        result["skip_reason"] = (
            f"Speed factor fuera de rango: {speed_factor_obs:.2f} "
            f"(true_abs={true_abs_frac:.2f}, model_abs={model_abs_frac:.2f})"
        )
        return result

    # ── Calidad del episodio ──────────────────────────────────────────────
    # 1. ¿Sube la glucosa? (mínimo de señal)
    q_signal = 1.0 if g_rise > 0.15 * max_g_rise else 0.0
    # 2. ¿La fracción observada es razonable? (0.1 - 1.5)
    q_range = 1.0 if 0.1 <= true_abs_frac <= 1.5 else 0.5
    # 3. ¿El intervalo de tiempo es bueno? (55-65 min exactos)
    dt_actual = abs(t_elapsed - 60)
    q_timing = max(0.0, 1.0 - dt_actual / 10.0)
    # 4. ¿La comida tiene categoría conocida? (reduce incertidumbre del bucket)
    q_category = 0.8 if ep.get("categoria") in _CATEGORY_TO_BUCKET else 0.4

    quality = 0.30 * q_signal + 0.25 * q_range + 0.25 * q_timing + 0.20 * q_category

    if quality < 0.35:
        result["skip_reason"] = f"Calidad insuficiente: {quality:.2f}"
        return result

    # obs_sigma: inversamente proporcional a la calidad
    # calidad=1.0 → obs_sigma=0.15, calidad=0.35 → obs_sigma=0.40
    obs_sigma = 0.15 + (1.0 - quality) * 0.35

    result.update({
        "usable":       True,
        "speed_factor": round(speed_factor_obs, 3),
        "obs_sigma":    round(obs_sigma, 3),
        "quality_score": round(quality, 3),
    })
    return result


# ── Calibración ───────────────────────────────────────────────────────────────

def run_absorption_calibration(force_bootstrap: bool = False) -> dict:
    """
    Corre el pipeline de aprendizaje de speed_factor.

    Integrado en run_calibration() de calibration.py.
    """
    from models import PMMObservation, db
    from pmm.core.parameter_store import load_parameter, save_parameter

    stats = {"updates": 0, "skipped": 0, "errors": []}

    try:
        # Obtener ISF y ICR actuales del PMM
        isf_state = load_parameter("ISF", context_block=-1)
        icr_state = load_parameter("ICR", context_block=-1)
        isf_mu = isf_state.mu if isf_state.n_obs >= 3 else 45.0
        icr_mu = icr_state.mu if icr_state.n_obs >= 3 else 12.0

        # Episodios candidatos
        days = 180 if force_bootstrap else 7
        episodes = load_absorption_episodes(days=days)

        # IDs ya procesados
        processed = set()
        if not force_bootstrap:
            from models import db
            from sqlalchemy import text
            rows = db.session.execute(
                text("SELECT source_id FROM pmm_observations "
                     "WHERE param_name LIKE 'KASPEED_%' AND source_id IS NOT NULL")
            ).fetchall()
            processed = {r[0] for r in rows}

        for ep in episodes:
            if ep["meal_id"] in processed:
                continue

            ev = evaluate_absorption_episode(ep, isf_mu, icr_mu)
            param_name = _PARAM_NAME[ev["bucket"]]

            obs_row = PMMObservation(
                param_name    = param_name,
                source_type   = "meal_absorption",
                source_id     = ep["meal_id"],
                observed_at   = ep["meal_time"],
                time_block    = -1,
                quality_score  = ev["quality_score"],
                observed_value = ev.get("speed_factor"),
                obs_sigma      = ev.get("obs_sigma"),
                used_in_update = False,
                skip_reason    = ev.get("skip_reason"),
            )

            if not ev["usable"]:
                db.session.add(obs_row)
                db.session.commit()
                stats["skipped"] += 1
                continue

            # Bayesian update del speed_factor para el bucket
            state = load_parameter(param_name, context_block=-1)
            obs_row.mu_before    = state.mu
            obs_row.sigma_before = state.sigma

            new_state = state.bayesian_update(
                ev["speed_factor"], ev["obs_sigma"],
                param_name=param_name,
            )
            # Clampear sigma_floor a _SIGMA_FLOOR
            from pmm.core.parameter_store import ParameterState
            new_state = ParameterState(
                mu=new_state.mu,
                sigma=max(new_state.sigma, _SIGMA_FLOOR),
                n_obs=new_state.n_obs,
            )
            save_parameter(param_name, -1, new_state)

            obs_row.mu_after      = new_state.mu
            obs_row.sigma_after   = new_state.sigma
            obs_row.used_in_update = True
            db.session.add(obs_row)
            db.session.commit()
            stats["updates"] += 1

    except Exception as exc:
        logger.exception("Error en absorption calibration")
        stats["errors"].append(str(exc))

    logger.info(
        f"PMM absorption calibration: +{stats['updates']} updates, "
        f"{stats['skipped']} skipped"
    )
    return stats


# ── Utilidades ────────────────────────────────────────────────────────────────

def _find_meal_bolus(meal, all_boluses):
    """Bolo de comida más cercano en ±30min del meal timestamp."""
    from sqlalchemy import or_
    window = timedelta(minutes=30)
    candidates = [
        b for b in all_boluses
        if abs((b.timestamp - meal.timestamp).total_seconds()) <= window.total_seconds()
        and b.purpose in ("comida", "mixto", None)
        and b.units and b.units > 0
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda b: abs((b.timestamp - meal.timestamp).total_seconds()))


def _nearest_reading_list(t: datetime, readings: list, window_min: int = 15):
    """Lectura más cercana a `t` en ±window_min de una lista ya cargada."""
    window_s = window_min * 60
    candidates = [
        r for r in readings
        if abs((r.timestamp - t).total_seconds()) <= window_s
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda r: abs((r.timestamp - t).total_seconds()))


def _interpret_speed(mu: float, n_obs: int) -> str:
    """Interpretación textual del speed_factor."""
    if n_obs < _MIN_OBS:
        return "Sin datos suficientes — usando valores poblacionales"
    pct = round((mu - 1.0) * 100)
    if abs(pct) < 8:
        return "Velocidad de absorción similar a la media poblacional"
    direction = "más rápido" if pct > 0 else "más lento"
    return f"Absorbés este tipo de alimento {abs(pct)}% {direction} que el modelo estándar"
