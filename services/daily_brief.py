"""
services/daily_brief.py
────────────────────────
Daily Metabolic Brief — Capa 1: pipeline determinístico.

Genera un `DailyMetabolicSummary` estructurado a partir de CGM + meals +
insulin + activities. NO usa LLM. NO usa el SSM. Solo agregación y
clasificación rule-based sobre datos ya disponibles.

El output de este módulo es el INPUT del LLM en daily_brief_llm.py.
Si este módulo falla o devuelve confidence baja, el LLM se omite y se
sirve un fallback estático.

Filosofía
---------
Toda decisión "estado metabólico" se toma acá con reglas explícitas
auditables. Claude solo traduce a lenguaje natural — nunca calcula ni
infiere estados nuevos.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, asdict, field
from datetime import datetime, date, time, timedelta
from typing import Optional


# ── Umbrales clínicos (ADA Standards 2024) ──────────────────────────────
HYPO_THRESHOLD       = 70.0
HYPO_SEVERE          = 54.0
TARGET_LOW           = 70.0
TARGET_HIGH          = 180.0
HYPER_SEVERE         = 250.0

# Ventana overnight (typicamente sueño)
OVERNIGHT_START_HOUR = 0     # 00:00
OVERNIGHT_END_HOUR   = 6     # 06:00

# Confidence thresholds
MIN_READINGS_FOR_TIR    = 36   # 3h × 12 lecturas/h
EXPECTED_READINGS_24H   = 288  # 24h × 12 lecturas/h (CGM cada 5min)
MAX_ACCEPTABLE_GAP_MIN  = 60   # gap > 60min penaliza confidence


# ─── Dataclass del summary ──────────────────────────────────────────────

@dataclass
class DailyMetabolicSummary:
    """Resumen estructurado — INPUT del LLM, no output del LLM."""
    # Metadata
    day:                 str                # ISO date
    generated_at:        str                # ISO datetime
    window_start:        str                # ISO datetime — inicio de la ventana 24h
    window_end:          str                # ISO datetime
    n_readings:          int
    expected_readings:   int                # ~288 si CGM perfecto

    # Confidence
    confidence:          float              # 0-1 — agregado de todas las dimensiones
    data_completeness:   float              # n_readings / expected
    max_gap_minutes:     int                # mayor gap entre lecturas consecutivas
    unified_confidence:  Optional[dict]        # capa de confianza unificada (Fase 2); None si no disponible

    # Overnight (00:00-06:00)
    overnight_n:                 int
    overnight_mean_glucose:      Optional[float]
    overnight_min_glucose:       Optional[float]
    overnight_max_glucose:       Optional[float]
    overnight_variability_cv:    Optional[float]   # % CV
    overnight_hypos:             int               # n eventos < 70
    overnight_stability:         str               # 'stable'|'mildly_variable'|'unstable'|'no_data'

    # 24h overall
    avg_glucose_24h:     Optional[float]
    sd_24h:              Optional[float]
    cv_24h:              Optional[float]
    tir_24h:             Optional[float]            # % 70-180
    tbr_24h:             Optional[float]            # % <70
    tar_24h:             Optional[float]            # % >180

    # Eventos
    hypo_events_24h:     int                        # n eventos hipo (cruces)
    hyper_events_24h:    int
    n_meals:             int
    total_carbs_24h:     float
    n_boluses:           int
    n_basal_doses:       int
    total_bolus_units:   float
    n_activities:        int
    total_exercise_min:  int

    # Patrones (rule-based)
    dominant_pattern:    Optional[str]              # ver _detect_dominant_pattern
    trend_today:         str                        # 'stable'|'rising'|'falling'|'volatile'
    exercise_impact:     Optional[str]              # 'positive'|'negative'|'neutral'|None
    post_meal_pattern:   Optional[str]              # 'controlled'|'high_spikes'|None

    # Highlight para narrativa
    notable_observation: Optional[str]              # frase corta accionable

    # Para safety layer
    has_sufficient_data: bool


# ─── Loader ─────────────────────────────────────────────────────────────

def _load_window_data(now: datetime, hours_back: int = 24) -> dict:
    """Carga CGM + meals + insulin + activities en la ventana."""
    from models import GlucoseReading, Meal, InsulinDose, Activity

    end   = now
    start = now - timedelta(hours=hours_back)
    overnight_start = datetime.combine(now.date(), time(OVERNIGHT_START_HOUR, 0))
    overnight_end   = datetime.combine(now.date(), time(OVERNIGHT_END_HOUR,   0))

    # Si todavía no pasó el "overnight end" de hoy, usar el de ayer
    if now < overnight_end:
        overnight_start -= timedelta(days=1)
        overnight_end   -= timedelta(days=1)

    cgm = (GlucoseReading.query
           .filter(GlucoseReading.timestamp >= start,
                   GlucoseReading.timestamp <= end,
                   GlucoseReading.is_artifact == False)   # excluir artefactos
           .order_by(GlucoseReading.timestamp).all())

    meals = (Meal.query
             .filter(Meal.timestamp >= start, Meal.timestamp <= end)
             .order_by(Meal.timestamp).all())

    boluses = (InsulinDose.query
               .filter(InsulinDose.timestamp >= start,
                       InsulinDose.timestamp <= end)
               .order_by(InsulinDose.timestamp).all())

    activities = (Activity.query
                  .filter(Activity.timestamp >= start, Activity.timestamp <= end)
                  .order_by(Activity.timestamp).all())

    cgm_overnight = [r for r in cgm
                     if overnight_start <= r.timestamp <= overnight_end]

    return {
        "cgm":             cgm,
        "cgm_overnight":   cgm_overnight,
        "meals":           meals,
        "boluses":         boluses,
        "activities":      activities,
        "start":           start,
        "end":             end,
        "overnight_start": overnight_start,
        "overnight_end":   overnight_end,
    }


# ─── Métricas ───────────────────────────────────────────────────────────

def _glucose_stats(readings: list) -> dict:
    """Stats básicas sobre una lista de GlucoseReading."""
    if not readings:
        return {"n": 0, "mean": None, "sd": None, "cv": None,
                "min": None, "max": None}
    vals = [float(r.value_mgdl) for r in readings]
    m = statistics.mean(vals)
    s = statistics.stdev(vals) if len(vals) > 1 else 0.0
    return {
        "n":     len(vals),
        "mean":  round(m, 1),
        "sd":    round(s, 1),
        "cv":    round(s / m * 100, 1) if m > 0 else None,
        "min":   round(min(vals), 1),
        "max":   round(max(vals), 1),
    }


def _tir_breakdown(readings: list) -> dict:
    """% en rango, debajo, arriba."""
    if not readings:
        return {"tir": None, "tbr": None, "tar": None}
    n = len(readings)
    in_range = sum(1 for r in readings if TARGET_LOW <= r.value_mgdl <= TARGET_HIGH)
    below    = sum(1 for r in readings if r.value_mgdl < TARGET_LOW)
    above    = sum(1 for r in readings if r.value_mgdl > TARGET_HIGH)
    return {
        "tir": round(in_range / n * 100, 1),
        "tbr": round(below    / n * 100, 1),
        "tar": round(above    / n * 100, 1),
    }


def _count_events(readings: list, threshold: float, direction: str) -> int:
    """
    Cuenta cruces (transiciones) — un evento es una entrada nueva al rango,
    NO el número de lecturas dentro del rango. Filtra eventos efímeros
    (< 10 min = 2 lecturas consecutivas mínimo).
    """
    if not readings or len(readings) < 2:
        return 0
    events = 0
    in_event = False
    streak = 0
    for r in readings:
        is_in = (r.value_mgdl < threshold) if direction == "below" else (r.value_mgdl > threshold)
        if is_in:
            streak += 1
            if not in_event and streak >= 2:
                events += 1
                in_event = True
        else:
            in_event = False
            streak = 0
    return events


def _max_gap(readings: list) -> int:
    """Mayor gap entre lecturas consecutivas (minutos)."""
    if len(readings) < 2:
        return 0
    gaps = []
    for i in range(1, len(readings)):
        dt = (readings[i].timestamp - readings[i-1].timestamp).total_seconds() / 60.0
        gaps.append(dt)
    return int(max(gaps))


# ─── Pattern extraction (rule-based) ────────────────────────────────────

def _classify_overnight_stability(stats: dict, hypos: int) -> str:
    """Clasificación overnight con reglas explícitas."""
    if stats["n"] < 12:                  # menos de 1h de data
        return "no_data"
    cv = stats.get("cv") or 0
    if hypos >= 2 or cv > 45:
        return "unstable"
    if cv > 30 or hypos == 1:
        return "mildly_variable"
    return "stable"


def _classify_trend_today(readings: list) -> str:
    """
    Clasifica la tendencia del día comparando el primer tercio vs último tercio.
    """
    if len(readings) < 24:                # menos de 2h de data → no clasificar
        return "stable"
    n = len(readings)
    first_third = readings[:n // 3]
    last_third  = readings[-n // 3:]

    m_first = statistics.mean([r.value_mgdl for r in first_third])
    m_last  = statistics.mean([r.value_mgdl for r in last_third])
    delta = m_last - m_first

    # Volatilidad: SD global vs media
    cv_global = (statistics.stdev([r.value_mgdl for r in readings])
                 / statistics.mean([r.value_mgdl for r in readings]) * 100)
    if cv_global > 40:
        return "volatile"

    if delta > 25:    return "slightly_rising"
    if delta < -25:   return "slightly_falling"
    return "stable"


def _detect_dominant_pattern(data: dict, stats_24h: dict, tir: dict) -> Optional[str]:
    """
    Detecta el patrón dominante del día. Reglas:

      - "stable_day"           : tir > 75, cv < 30, sin hipos/hipers severos
      - "post_dinner_rise"     : pico > 200 entre 20:00 y 23:59
      - "recurrent_morning_high": G > 160 entre 06-10 (fenómeno del alba)
      - "late_hypo"            : hipo entre 01:00 y 05:00
      - "exercise_improvement" : ejercicio en el día Y tir > 70 post-ejercicio
      - "high_variability"     : cv > 40
      - None                   : nada destaca
    """
    cgm = data["cgm"]
    if not cgm:
        return None

    cv = stats_24h.get("cv") or 0

    # ── stable_day ──
    if (tir.get("tir") or 0) > 75 and cv < 30 and (tir.get("tbr") or 0) < 2:
        return "stable_day"

    # ── late_hypo ──
    for r in cgm:
        if r.value_mgdl < HYPO_THRESHOLD and 1 <= r.timestamp.hour <= 5:
            return "late_hypo"

    # ── post_dinner_rise ──
    evening = [r for r in cgm if 20 <= r.timestamp.hour <= 23]
    if evening and max(r.value_mgdl for r in evening) > 220:
        return "post_dinner_rise"

    # ── recurrent_morning_high ──
    morning = [r for r in cgm if 6 <= r.timestamp.hour <= 10]
    if morning:
        m_morning = statistics.mean([r.value_mgdl for r in morning])
        if m_morning > 165:
            return "recurrent_morning_high"

    # ── exercise_improvement ──
    if data["activities"]:
        after_exercise = []
        for a in data["activities"]:
            end_act = a.timestamp + timedelta(minutes=(a.duration_min or 30))
            after = [r for r in cgm
                     if end_act < r.timestamp < end_act + timedelta(hours=2)]
            after_exercise.extend(after)
        if after_exercise:
            ae_in_range = sum(1 for r in after_exercise
                              if TARGET_LOW <= r.value_mgdl <= TARGET_HIGH)
            if ae_in_range / len(after_exercise) > 0.75:
                return "exercise_improvement"

    # ── high_variability ──
    if cv > 42:
        return "high_variability"

    return None


def _classify_exercise_impact(data: dict) -> Optional[str]:
    """positive / negative / neutral / None."""
    if not data["activities"]:
        return None
    cgm = data["cgm"]
    pre_post = []
    for a in data["activities"]:
        end_act = a.timestamp + timedelta(minutes=(a.duration_min or 30))
        # Glucemia 30 min antes
        pre = [r for r in cgm
               if a.timestamp - timedelta(minutes=30) <= r.timestamp <= a.timestamp]
        # Glucemia 30-90 min después
        post = [r for r in cgm
                if end_act + timedelta(minutes=30) <= r.timestamp <= end_act + timedelta(minutes=90)]
        if pre and post:
            pre_post.append((statistics.mean([r.value_mgdl for r in pre]),
                             statistics.mean([r.value_mgdl for r in post])))
    if not pre_post:
        return "neutral"
    deltas = [p[1] - p[0] for p in pre_post]
    avg_delta = statistics.mean(deltas)
    if avg_delta < -15:   return "positive"     # baja glucosa, bueno
    if avg_delta > 30:    return "negative"     # subió mucho, raro
    return "neutral"


def _classify_post_meal_pattern(data: dict) -> Optional[str]:
    """
    Mira las 2h después de cada comida con carbs > 15g y mira el pico.
    'controlled' si todos los picos < 200, 'high_spikes' si >50% > 220.
    """
    cgm   = data["cgm"]
    meals = [m for m in data["meals"] if (m.carbs_g or 0) > 15]
    if not meals or not cgm:
        return None
    spikes = []
    for m in meals:
        post = [r for r in cgm
                if m.timestamp <= r.timestamp <= m.timestamp + timedelta(hours=2)]
        if post:
            spikes.append(max(r.value_mgdl for r in post))
    if not spikes:
        return None
    high = sum(1 for s in spikes if s > 220)
    if high / len(spikes) > 0.5:
        return "high_spikes"
    if max(spikes) < 200:
        return "controlled"
    return None


def _notable_observation(summary: dict) -> Optional[str]:
    """
    Frase corta (~10-15 palabras) con el dato más importante del día
    para que el LLM lo destaque. Prioriza eventos clínicamente relevantes.
    """
    # Priority order
    if summary["overnight_hypos"] >= 1:
        return f"Tuviste {summary['overnight_hypos']} episodio(s) de hipoglucemia nocturna."
    if summary["hypo_events_24h"] >= 2:
        return f"Detectamos {summary['hypo_events_24h']} eventos de hipoglucemia hoy."
    if summary["tir_24h"] is not None and summary["tir_24h"] > 75:
        return f"Tu time-in-range fue {summary['tir_24h']:.0f}% — un día muy estable."
    if summary["dominant_pattern"] == "post_dinner_rise":
        return "La cena disparó una subida significativa en las horas siguientes."
    if summary["dominant_pattern"] == "recurrent_morning_high":
        return "Glucemias matutinas más altas de lo habitual — posible fenómeno del alba."
    if summary["dominant_pattern"] == "high_variability":
        return "La variabilidad del día fue alta — más vaivenes que un día típico."
    if summary["dominant_pattern"] == "exercise_improvement":
        return "El ejercicio mejoró notablemente el control glucémico hoy."
    if summary["tir_24h"] is not None and summary["tir_24h"] < 50:
        return f"Tu time-in-range bajó a {summary['tir_24h']:.0f}% — revisar bolos o basal con el médico."
    return None


# ─── Confidence ─────────────────────────────────────────────────────────

def _compute_confidence(n_readings: int, max_gap: int) -> tuple[float, float]:
    """Devuelve (confidence_total, data_completeness)."""
    completeness = min(1.0, n_readings / EXPECTED_READINGS_24H)
    # Penalty por gaps grandes
    gap_penalty = 1.0
    if max_gap > MAX_ACCEPTABLE_GAP_MIN:
        gap_penalty = max(0.3, 1.0 - (max_gap - MAX_ACCEPTABLE_GAP_MIN) / 240)
    # Multiplicar — bajo en cualquiera baja todo
    confidence = round(completeness * gap_penalty, 3)
    return confidence, round(completeness, 3)


# ─── Entry point ────────────────────────────────────────────────────────

def compute_daily_summary(now: Optional[datetime] = None) -> DailyMetabolicSummary:
    """
    Genera el DailyMetabolicSummary del día.

    Toda la lógica es determinística. Si no hay data suficiente, retorna
    summary con has_sufficient_data=False y confidence bajo.
    """
    if now is None:
        now = datetime.now()

    data = _load_window_data(now, hours_back=24)

    n   = len(data["cgm"])
    max_gap = _max_gap(data["cgm"])
    conf, completeness = _compute_confidence(n, max_gap)

    # Unified confidence (Fase 2 — enriquece el brief sin romper la lógica existente)
    _unified_conf = None
    try:
        from safety.confidence import compute_confidence
        _ucr = compute_confidence(
            now=now,
            n_readings_6h=min(n, 24),   # proxy: 6h ~ 1/4 de las 24h
            max_gap_min=max_gap,
        )
        _unified_conf = _ucr.to_dict()
    except Exception:
        pass

    stats_24h = _glucose_stats(data["cgm"])
    stats_ov  = _glucose_stats(data["cgm_overnight"])
    tir       = _tir_breakdown(data["cgm"])
    n_hypo_ov = sum(1 for r in data["cgm_overnight"] if r.value_mgdl < HYPO_THRESHOLD)
    hypo_evt  = _count_events(data["cgm"], HYPO_THRESHOLD,  "below")
    hyper_evt = _count_events(data["cgm"], TARGET_HIGH,     "above")

    # Patrones
    pattern   = _detect_dominant_pattern(data, stats_24h, tir)
    trend     = _classify_trend_today(data["cgm"])
    ex_impact = _classify_exercise_impact(data)
    pmp       = _classify_post_meal_pattern(data)
    ov_stab   = _classify_overnight_stability(stats_ov, n_hypo_ov)

    # Aggregations
    total_carbs   = sum(m.carbs_g or 0 for m in data["meals"])
    n_bolus       = sum(1 for d in data["boluses"] if d.type == "bolus")
    n_basal       = sum(1 for d in data["boluses"] if d.type == "basal")
    total_bolus_U = sum(d.units for d in data["boluses"] if d.type == "bolus")
    total_act_min = sum((a.duration_min or 0) for a in data["activities"])

    summary_dict = {
        "day":                 now.date().isoformat(),
        "generated_at":        now.isoformat(timespec="seconds"),
        "window_start":        data["start"].isoformat(timespec="seconds"),
        "window_end":          data["end"].isoformat(timespec="seconds"),
        "n_readings":          n,
        "expected_readings":   EXPECTED_READINGS_24H,
        "confidence":          conf,
        "data_completeness":   completeness,
        "max_gap_minutes":     max_gap,
        "unified_confidence":  _unified_conf,   # unified safety layer (Fase 2)
        "overnight_n":               stats_ov["n"],
        "overnight_mean_glucose":    stats_ov["mean"],
        "overnight_min_glucose":     stats_ov["min"],
        "overnight_max_glucose":     stats_ov["max"],
        "overnight_variability_cv":  stats_ov["cv"],
        "overnight_hypos":           n_hypo_ov,
        "overnight_stability":       ov_stab,
        "avg_glucose_24h":     stats_24h["mean"],
        "sd_24h":              stats_24h["sd"],
        "cv_24h":              stats_24h["cv"],
        "tir_24h":             tir["tir"],
        "tbr_24h":             tir["tbr"],
        "tar_24h":             tir["tar"],
        "hypo_events_24h":     hypo_evt,
        "hyper_events_24h":    hyper_evt,
        "n_meals":             len(data["meals"]),
        "total_carbs_24h":     round(total_carbs, 1),
        "n_boluses":           n_bolus,
        "n_basal_doses":       n_basal,
        "total_bolus_units":   round(total_bolus_U, 1),
        "n_activities":        len(data["activities"]),
        "total_exercise_min":  int(total_act_min),
        "dominant_pattern":    pattern,
        "trend_today":         trend,
        "exercise_impact":     ex_impact,
        "post_meal_pattern":   pmp,
        "notable_observation": None,                     # se llena abajo
        "has_sufficient_data": (n >= MIN_READINGS_FOR_TIR and conf >= 0.4),
    }
    summary_dict["notable_observation"] = _notable_observation(summary_dict)

    return DailyMetabolicSummary(**summary_dict)


def summary_to_dict(s: DailyMetabolicSummary) -> dict:
    return asdict(s)
