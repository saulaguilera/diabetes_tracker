"""
pmm/engines/observation.py
───────────────────────────
Motor de detección y evaluación de episodios de aprendizaje.

Un "episodio limpio" es un evento del que podemos inferir un parámetro
fisiológico con razonable confianza:

  - Episodio de CORRECCIÓN → aprende ISF
      (bolo de corrección sin comida cercana + respuesta glucémica observada)

  - Episodio de COMIDA → aprende ICR
      (bolo pre-prandial + comida registrada + respuesta observada)

Cada episodio recibe un quality_score ∈ [0, 1] que determina cuánto
peso tiene en el update Bayesiano. Solo episodios con quality >= MIN_QUALITY
se usan para aprender.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

# ── Umbrales ────────────────────────────────────────────────────────────────────
MIN_QUALITY        = 0.35   # calidad mínima para usar un episodio
CORRECTION_WINDOW  = 3.0    # horas post-corrección para buscar nadir
MEAL_WINDOW        = 3.0    # horas post-comida para evaluar respuesta
MEAL_CONFOUND_WIN  = 90     # minutos: si hay comida en ±90min → corrección contaminada
PRE_READ_WINDOW    = (30, 15)  # ventana para lectura pre (minutos antes, minutos después del bolo)
MAX_ISF_OBS        = 150.0  # mg/dL·U — límite superior (filtra artefactos)
MIN_ISF_OBS        = 10.0   # mg/dL·U — límite inferior
MIN_GLUCOSE_DROP   = 8.0    # mg/dL — caída mínima para que el episodio sea válido
MAX_ICR_OBS        = 30.0   # g CH / U
MIN_ICR_OBS        = 3.0    # g CH / U


def _readings_in_window(all_readings, t_start, t_end):
    """Filtra lecturas en una ventana temporal."""
    return [r for r in all_readings if t_start <= r.timestamp <= t_end]


def _roc_mgdl_per_min(readings, before: datetime, window_min: int = 20) -> Optional[float]:
    """
    Calcula la tasa de cambio (mg/dL/min) en los `window_min` minutos
    previos a `before` usando las lecturas disponibles.
    Retorna None si no hay suficientes datos.
    """
    t_start = before - timedelta(minutes=window_min)
    local = sorted(
        [r for r in readings if t_start <= r.timestamp <= before],
        key=lambda r: r.timestamp
    )
    if len(local) < 2:
        return None
    dt = (local[-1].timestamp - local[0].timestamp).total_seconds() / 60
    if dt < 5:
        return None
    return (local[-1].value_mgdl - local[0].value_mgdl) / dt


# ── Evaluación de episodios de corrección ──────────────────────────────────────

def evaluate_correction_episode(
    dose,
    all_readings: list,
    all_meal_times: list,
) -> dict:
    """
    Evalúa un bolo como episodio de aprendizaje de ISF.

    Parámetros
    ----------
    dose           : InsulinDose (type='bolus')
    all_readings   : lista de GlucoseReading del período relevante
    all_meal_times : lista de datetime de comidas del período

    Retorna dict con:
        usable         : bool — si supera MIN_QUALITY
        quality_score  : float 0-1
        observed_value : float | None — ISF_obs = ΔG / U
        obs_sigma      : float — ruido estimado
        pre_glucose    : float | None
        nadir          : float | None
        n_post_readings: int
        skip_reason    : str | None
        quality_factors: dict — desglose de factores
    """
    result = {
        "usable":          False,
        "quality_score":   0.0,
        "observed_value":  None,
        "obs_sigma":       None,
        "pre_glucose":     None,
        "nadir":           None,
        "n_post_readings": 0,
        "skip_reason":     None,
        "quality_factors": {},
    }

    # ── Lectura pre-corrección ─────────────────────────────────────────────────
    t0 = dose.timestamp
    pre_reads = _readings_in_window(
        all_readings,
        t0 - timedelta(minutes=PRE_READ_WINDOW[0]),
        t0 + timedelta(minutes=PRE_READ_WINDOW[1]),
    )
    if not pre_reads:
        result["skip_reason"] = "sin_lectura_pre"
        return result

    pre_read = max(pre_reads, key=lambda r: r.timestamp)
    pre_glucose = pre_read.value_mgdl
    result["pre_glucose"] = pre_glucose

    # ── Lecturas post-corrección ───────────────────────────────────────────────
    post_reads = _readings_in_window(
        all_readings,
        t0 + timedelta(minutes=30),
        t0 + timedelta(hours=CORRECTION_WINDOW),
    )
    result["n_post_readings"] = len(post_reads)

    if not post_reads:
        result["skip_reason"] = "sin_lecturas_post"
        return result

    nadir = min(r.value_mgdl for r in post_reads)
    result["nadir"] = nadir

    # ── Caída mínima necesaria ─────────────────────────────────────────────────
    delta_g = pre_glucose - nadir
    if delta_g < MIN_GLUCOSE_DROP:
        result["skip_reason"] = f"caida_insuficiente_{delta_g:.0f}mg"
        return result

    if dose.units <= 0:
        result["skip_reason"] = "units_cero"
        return result

    # ── Calcular ISF_obs ───────────────────────────────────────────────────────
    isf_obs = delta_g / dose.units
    if not (MIN_ISF_OBS <= isf_obs <= MAX_ISF_OBS):
        result["skip_reason"] = f"isf_fuera_rango_{isf_obs:.0f}"
        return result

    result["observed_value"] = round(isf_obs, 2)

    # ── Quality scoring ────────────────────────────────────────────────────────
    factors = {}

    # F1: etiquetado explícitamente como corrección (0.25)
    f1 = 0.25 if dose.purpose == "correccion" else 0.0
    factors["labeled_correction"] = f1

    # F2: sin comidas en ventana ±90min (0.20)
    meal_confound = any(
        abs((mt - t0).total_seconds()) < MEAL_CONFOUND_WIN * 60
        for mt in all_meal_times
    )
    f2 = 0.0 if meal_confound else 0.20
    factors["no_meal_confound"] = f2

    # F3: glucosa estable pre-corrección (0.20)
    roc = _roc_mgdl_per_min(all_readings, t0)
    if roc is not None and abs(roc) < 0.5:
        f3 = 0.20
    elif roc is not None:
        f3 = 0.10 if abs(roc) < 1.0 else 0.0
    else:
        f3 = 0.05  # sin datos suficientes para RoC
    factors["stable_pre_glucose"] = f3

    # F4: suficientes lecturas post (0.20)
    if len(post_reads) >= 5:
        f4 = 0.20
    elif len(post_reads) >= 3:
        f4 = 0.13
    elif len(post_reads) >= 2:
        f4 = 0.07
    else:
        f4 = 0.0
    factors["post_reading_coverage"] = f4

    # F5: caída limpia (nadir definido, no zigzag) (0.15)
    # Proxy: la glucosa al final del período está más cerca del nadir que del pre
    post_sorted = sorted(post_reads, key=lambda r: r.timestamp)
    last_g = post_sorted[-1].value_mgdl if post_sorted else pre_glucose
    drop_ratio = (pre_glucose - last_g) / max(delta_g, 1)
    f5 = 0.15 if drop_ratio > 0.5 else 0.07 if drop_ratio > 0.2 else 0.0
    factors["clean_response"] = f5

    quality = min(1.0, f1 + f2 + f3 + f4 + f5)
    result["quality_score"]   = round(quality, 3)
    result["quality_factors"] = factors

    if quality < MIN_QUALITY:
        result["skip_reason"] = f"calidad_baja_{quality:.2f}"
        return result

    # Calcular obs_sigma: alta calidad → menor ruido → más peso en el update
    from pmm.core.parameter_store import _obs_sigma_for_quality
    result["obs_sigma"] = round(_obs_sigma_for_quality("ISF", quality), 2)
    result["usable"]    = True
    return result


# ── Evaluación de episodios de comida ──────────────────────────────────────────

def evaluate_meal_episode(
    meal,
    dose,
    all_readings: list,
    isf_mu: float,
    target_glucose: float = 100.0,
) -> dict:
    """
    Evalúa un par (comida, bolo) como episodio de aprendizaje de ICR.

    La corrección por glucemia inicial se hace con el ISF actual del PMM:
        bolo_comida = total_units - (G_pre - target) / ISF

    Parámetros
    ----------
    meal          : Meal
    dose          : InsulinDose (bolo asociado a la comida)
    all_readings  : GlucoseReading del período
    isf_mu        : ISF μ actual del PMM (para descontar corrección)
    target_glucose: objetivo glucémico del usuario

    Retorna dict con los mismos campos que evaluate_correction_episode.
    """
    result = {
        "usable":          False,
        "quality_score":   0.0,
        "observed_value":  None,
        "obs_sigma":       None,
        "pre_glucose":     None,
        "nadir":           None,
        "n_post_readings": 0,
        "skip_reason":     None,
        "quality_factors": {},
    }

    if not meal.carbs_g or meal.carbs_g < 5:
        result["skip_reason"] = "carbs_insuficientes"
        return result

    if dose.units <= 0:
        result["skip_reason"] = "units_cero"
        return result

    # Bolo de comida pura (excluir correcciones etiquetadas explícitamente)
    if dose.purpose == "correccion":
        result["skip_reason"] = "bolo_correccion"
        return result

    t0 = meal.timestamp

    # Lectura pre-comida
    pre_reads = _readings_in_window(
        all_readings,
        t0 - timedelta(minutes=30),
        t0 + timedelta(minutes=5),
    )
    if not pre_reads:
        result["skip_reason"] = "sin_lectura_pre"
        return result

    pre_read = max(pre_reads, key=lambda r: r.timestamp)
    pre_glucose = pre_read.value_mgdl
    result["pre_glucose"] = pre_glucose

    # Descontar corrección del bolo total
    correccion = max(0.0, (pre_glucose - target_glucose) / isf_mu) if isf_mu > 0 else 0.0
    bolo_comida = dose.units - correccion

    if bolo_comida < 0.2:
        result["skip_reason"] = f"bolo_comida_muy_bajo_{bolo_comida:.2f}"
        return result

    # ICR_obs
    icr_obs = meal.carbs_g / bolo_comida
    if not (MIN_ICR_OBS <= icr_obs <= MAX_ICR_OBS):
        result["skip_reason"] = f"icr_fuera_rango_{icr_obs:.1f}"
        return result

    result["observed_value"] = round(icr_obs, 2)

    # Lecturas post para quality scoring
    post_reads = _readings_in_window(
        all_readings,
        t0 + timedelta(minutes=60),
        t0 + timedelta(hours=MEAL_WINDOW),
    )
    result["n_post_readings"] = len(post_reads)

    # ── Quality scoring ────────────────────────────────────────────────────────
    factors = {}

    # F1: bolo etiquetado como comida (0.20)
    f1 = 0.20 if dose.purpose in ("comida", "mixto") else 0.05
    factors["labeled_meal"] = f1

    # F2: glucosa pre-comida estable (no en corrección activa) (0.20)
    roc = _roc_mgdl_per_min(all_readings, t0)
    if roc is not None and abs(roc) < 0.5:
        f2 = 0.20
    elif roc is not None:
        f2 = 0.10 if abs(roc) < 1.0 else 0.0
    else:
        f2 = 0.05
    factors["stable_pre_glucose"] = f2

    # F3: carbohidratos > 15g (estimación más confiable) (0.20)
    if meal.carbs_g >= 30:
        f3 = 0.20
    elif meal.carbs_g >= 15:
        f3 = 0.12
    else:
        f3 = 0.06
    factors["sufficient_carbs"] = f3

    # F4: lecturas post disponibles (0.20)
    if len(post_reads) >= 4:
        f4 = 0.20
    elif len(post_reads) >= 2:
        f4 = 0.10
    else:
        f4 = 0.0
    factors["post_reading_coverage"] = f4

    # F5: pre-glucose cerca de target (corrección de ISF pequeña → menos ruido) (0.20)
    dist_target = abs(pre_glucose - target_glucose)
    if dist_target < 20:
        f5 = 0.20
    elif dist_target < 40:
        f5 = 0.12
    elif dist_target < 70:
        f5 = 0.06
    else:
        f5 = 0.0
    factors["glucose_near_target"] = f5

    quality = min(1.0, f1 + f2 + f3 + f4 + f5)
    result["quality_score"]   = round(quality, 3)
    result["quality_factors"] = factors

    if quality < MIN_QUALITY:
        result["skip_reason"] = f"calidad_baja_{quality:.2f}"
        return result

    from pmm.core.parameter_store import _obs_sigma_for_quality
    result["obs_sigma"] = round(_obs_sigma_for_quality("ICR", quality), 2)
    result["usable"]    = True
    return result


# ── Batch loader de episodios históricos ───────────────────────────────────────

def load_correction_episodes(days: int = 180) -> list[dict]:
    """
    Carga y evalúa todos los episodios de corrección de los últimos `days` días.
    Hace carga en batch (3 queries) para no saturar la DB.

    Retorna lista de dicts, ordenada por observed_at ASC (para update secuencial).
    """
    from datetime import datetime
    from models import InsulinDose, GlucoseReading, Meal

    since = datetime.utcnow() - timedelta(days=days)

    bolus_list = (
        InsulinDose.query
        .filter(
            InsulinDose.type == "bolus",
            InsulinDose.timestamp >= since,
        )
        .order_by(InsulinDose.timestamp)
        .all()
    )

    # Cargar glucemias en bloque (ventana amplia para cubrir todos los post)
    all_readings = (
        GlucoseReading.query
        .filter(GlucoseReading.timestamp >= since - timedelta(minutes=30))
        .order_by(GlucoseReading.timestamp)
        .all()
    )

    # Cargar timestamps de comidas para detectar confounders
    meal_times = [
        m.timestamp for m in
        Meal.query
        .filter(Meal.timestamp >= since - timedelta(hours=2))
        .all()
    ]

    episodes = []
    for dose in bolus_list:
        ep = evaluate_correction_episode(dose, all_readings, meal_times)
        ep["dose_id"]     = dose.id
        ep["observed_at"] = dose.timestamp
        ep["time_block"]  = (dose.timestamp.hour // 4) * 4
        episodes.append(ep)

    # Ordenar por timestamp para update secuencial (el orden importa en Bayes)
    episodes.sort(key=lambda e: e["observed_at"])
    return episodes


def load_meal_episodes(days: int = 180, isf_mu: float = 45.0) -> list[dict]:
    """
    Carga y evalúa todos los episodios de comida de los últimos `days` días.
    """
    from datetime import datetime
    from models import InsulinDose, GlucoseReading, Meal
    from helpers import _get_setting

    since = datetime.utcnow() - timedelta(days=days)
    target = float(_get_setting("objetivo", 100))

    meals = (
        Meal.query
        .filter(Meal.timestamp >= since, Meal.carbs_g > 0)
        .order_by(Meal.timestamp)
        .all()
    )

    all_readings = (
        GlucoseReading.query
        .filter(GlucoseReading.timestamp >= since - timedelta(minutes=30))
        .order_by(GlucoseReading.timestamp)
        .all()
    )

    episodes = []
    for meal in meals:
        t0 = meal.timestamp
        # Buscar bolus asociado (ventana ±30-45min)
        # NULL purpose también es válido (bolus sin etiquetar)
        from sqlalchemy import or_
        dose = (
            InsulinDose.query
            .filter(
                InsulinDose.type == "bolus",
                InsulinDose.timestamp >= t0 - timedelta(minutes=30),
                InsulinDose.timestamp <= t0 + timedelta(minutes=45),
                or_(
                    InsulinDose.purpose.in_(["comida", "mixto"]),
                    InsulinDose.purpose.is_(None),
                ),
            )
            .order_by(InsulinDose.timestamp)
            .first()
        )
        if not dose:
            continue

        ep = evaluate_meal_episode(meal, dose, all_readings, isf_mu, target)
        ep["dose_id"]     = dose.id
        ep["meal_id"]     = meal.id
        ep["observed_at"] = meal.timestamp
        ep["time_block"]  = (meal.timestamp.hour // 4) * 4
        episodes.append(ep)

    episodes.sort(key=lambda e: e["observed_at"])
    return episodes
