"""
safety/narrative.py
────────────────────
Capa de narrativa humana para T1D.

Traduce variables técnicas del modelo a lenguaje claro, calmado y empático.
Diseñado para una persona con T1D que ya lleva años gestionando su condición:
inteligente, cansada del lenguaje médico, que quiere entender — no ser alarmada.

Principios
──────────
1. Sin jerga técnica: no "IOB residual", no "trough", no "intersticial"
2. Con contexto temporal: "del bolus que te pusiste antes de cenar", no "bolus anterior"
3. Calma antes que precisión: un "puede bajar" es más útil que "p_hypo=0.38"
4. Tres tonos: calm (informativo), alert (atención), urgent (acción ahora)
5. La sugerencia es opcional, no un mandato
6. Nunca usa LLM — todo determinístico

API pública
───────────
  render_hypo_warning(assessment, confidence, now=None) → HypoWarningText
  render_confidence_message(confidence)                 → str
  render_degradation_message(confidence)                → str
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from utils.hypo_risk_engine import HypoRiskAssessment
    from safety.confidence import ConfidenceReport


# ── Constantes de tono ────────────────────────────────────────────────────────
TONE_CALM   = "calm"
TONE_ALERT  = "alert"
TONE_URGENT = "urgent"


@dataclass
class HypoWarningText:
    """Texto estructurado para la alerta de riesgo de hipoglucemia."""
    tone:               str           # calm | alert | urgent
    title:              str
    probability_phrase: str
    trough_phrase:      str
    factors:            list[str]     # máx 3 frases, cada una ≤ 12 palabras
    suggestion:         str
    confidence_note:    str
    badge_text:         str           # texto corto para el badge (≤ 6 palabras)
    suppress:           bool = False  # True si no debe mostrarse

    def to_dict(self) -> dict:
        return {
            "tone":               self.tone,
            "title":              self.title,
            "probability_phrase": self.probability_phrase,
            "trough_phrase":      self.trough_phrase,
            "factors":            self.factors,
            "suggestion":         self.suggestion,
            "confidence_note":    self.confidence_note,
            "badge_text":         self.badge_text,
            "suppress":           self.suppress,
        }


# ── Render principal ──────────────────────────────────────────────────────────

def render_hypo_warning(
    assessment,           # HypoRiskAssessment
    confidence,           # ConfidenceReport
    now: Optional[datetime] = None,
) -> HypoWarningText:
    """
    Genera el texto completo de la alerta de riesgo de hipoglucemia en tono
    humano y empático para un usuario T1D.
    """
    now = now or datetime.utcnow()

    # ── Si el sistema debe silenciarse ────────────────────────────────────────
    if confidence.suppress_alerts():
        reason = _suppression_reason(confidence)
        return HypoWarningText(
            tone=TONE_CALM,
            title="",
            probability_phrase="",
            trough_phrase="",
            factors=[],
            suggestion="",
            confidence_note=reason,
            badge_text="",
            suppress=True,
        )

    # ── Tono según severidad ──────────────────────────────────────────────────
    tone = _choose_tone(assessment.severity, assessment.p_hypo_70)

    # ── Título ────────────────────────────────────────────────────────────────
    title = _build_title(tone, now)

    # ── Frase de probabilidad ─────────────────────────────────────────────────
    probability_phrase = _build_probability_phrase(
        assessment.p_hypo_70, assessment.p_hypo_55, confidence
    )

    # ── Frase del trough ──────────────────────────────────────────────────────
    trough_phrase = _build_trough_phrase(
        assessment.min_predicted_glucose,
        assessment.projected_trough_time,
        assessment.min_glucose_eta_min,
        now,
    )

    # ── Factores contribuyentes en lenguaje humano ────────────────────────────
    factors = _build_human_factors(assessment, now)

    # ── Sugerencia ────────────────────────────────────────────────────────────
    suggestion = _build_suggestion(assessment.severity, tone, now)

    # ── Nota de confianza ─────────────────────────────────────────────────────
    confidence_note = _build_confidence_note(confidence, assessment.ssm_available)

    # ── Badge ─────────────────────────────────────────────────────────────────
    badge_text = _build_badge(assessment.p_hypo_70, assessment.severity)

    return HypoWarningText(
        tone=tone,
        title=title,
        probability_phrase=probability_phrase,
        trough_phrase=trough_phrase,
        factors=factors,
        suggestion=suggestion,
        confidence_note=confidence_note,
        badge_text=badge_text,
        suppress=False,
    )


def render_confidence_message(confidence) -> str:
    """
    Frase breve sobre el estado de confianza del sistema.
    Para mostrar debajo de una predicción o en la barra de estado.
    """
    mode = confidence.degradation_mode
    score = confidence.score
    factor = confidence.limiting_factor

    factor_labels = {
        "sharpness":          "el modelo necesita más lecturas para estabilizarse",
        "observability":      "hay un gap de datos recientes",
        "sensor_health":      "el sensor mostró lecturas inusuales",
        "innovation_quality": "el modelo tiene un leve sesgo sistemático",
        "model_freshness":    "el modelo no se actualizó recientemente",
    }

    if mode == "full":
        return f"Modelo con buena confianza ({round(score * 100):.0f}%)"
    elif mode == "conservative":
        detail = factor_labels.get(factor, "")
        base = f"Confianza moderada ({round(score * 100):.0f}%)"
        return f"{base} — {detail}." if detail else base
    elif mode == "observe_only":
        detail = factor_labels.get(factor, "")
        return f"Confianza baja — {detail}. Mostrando estado sin recomendaciones."
    else:
        return f"Datos insuficientes — {confidence.explanation}"


def render_degradation_message(confidence) -> str:
    """
    Mensaje de degradación para mostrar al usuario cuando el sistema
    opera en modo conservador o inferior.
    """
    mode = confidence.degradation_mode

    if mode == "full":
        return ""   # nada que decir

    messages = {
        "conservative": (
            "El modelo opera con datos parciales. "
            "Las estimaciones son orientativas — prestá atención a cómo te sentís."
        ),
        "observe_only": (
            "Hay información insuficiente para hacer una estimación confiable ahora. "
            "Seguí tus síntomas y medí si tenés dudas."
        ),
        "silent": (
            "Sin datos suficientes del sensor para analizar el riesgo. "
            "Verificá el sensor y hacé una medición manual si tenés síntomas."
        ),
    }

    return messages.get(mode, "")


# ── Builders internos ─────────────────────────────────────────────────────────

def _choose_tone(severity: str, p_hypo_70: float) -> str:
    if severity == "critical" or p_hypo_70 >= 0.70:
        return TONE_URGENT
    elif severity in ("high",) or p_hypo_70 >= 0.40:
        return TONE_ALERT
    else:
        return TONE_CALM


def _build_title(tone: str, now: datetime) -> str:
    hour = now.hour
    is_night = hour >= 22 or hour < 6

    if tone == TONE_URGENT:
        return "Riesgo elevado de bajada esta noche" if is_night else "Riesgo elevado de bajada"
    elif tone == TONE_ALERT:
        return "Posible bajada nocturna" if is_night else "Posible bajada de glucemia"
    else:
        return "Algo a tener en cuenta esta noche" if is_night else "Un detalle sobre tu glucemia"


def _build_probability_phrase(
    p70: float,
    p55: float,
    confidence,
) -> str:
    p_pct = round(p70 * 100)
    mode = confidence.degradation_mode

    qualifier = {
        "full":         "",
        "conservative": " (estimación con datos parciales)",
        "observe_only": " (estimación orientativa)",
        "silent":       "",
    }.get(mode, "")

    if p_pct >= 70:
        base = f"El modelo ve una probabilidad alta ({p_pct}%) de que la glucemia baje por debajo de 70"
    elif p_pct >= 40:
        base = f"Hay una probabilidad moderada ({p_pct}%) de que la glucemia llegue por debajo de 70"
    else:
        base = f"El modelo estima un {p_pct}% de probabilidad de bajada por debajo de 70"

    if p55 >= 0.20:
        p55_pct = round(p55 * 100)
        base += f", con {p55_pct}% de probabilidad de llegar por debajo de 55"

    return base + qualifier + "."


def _build_trough_phrase(
    g_min: float,
    trough_time: Optional[datetime],
    eta_min: int,
    now: datetime,
) -> str:
    g_str = f"~{round(g_min)}" if g_min > 0 else "muy baja"

    if trough_time:
        hour = trough_time.hour
        minute = trough_time.minute
        t_str = f"{hour:02d}:{minute:02d}"

        # Descriptor del momento del día
        if trough_time.date() > now.date():
            day_str = "mañana"
        else:
            day_str = "esta noche" if hour >= 22 or hour < 6 else "después"

        return f"El mínimo estimado es {g_str} mg/dL, alrededor de las {t_str} ({day_str})."
    elif eta_min > 0:
        if eta_min >= 60:
            h = eta_min // 60
            m = eta_min % 60
            t_str = f"{h}h{f' {m}m' if m else ''}"
        else:
            t_str = f"{eta_min} min"
        return f"El mínimo estimado es {g_str} mg/dL, en aproximadamente {t_str}."
    else:
        return f"El modelo estima un mínimo de {g_str} mg/dL."


def _build_human_factors(assessment, now: datetime) -> list[str]:
    """
    Convierte los factores técnicos del assessment en frases humanas.
    Máximo 3 factores, ordenados por relevancia.
    """
    factors = []
    hour_now = now.hour

    # ── Factor 1: insulina activa ─────────────────────────────────────────────
    bolus    = getattr(assessment, "proposed_bolus", 0.0) or 0.0
    # Extraer IOB de contributing_factors (heurística sobre el texto técnico)
    raw_factors = getattr(assessment, "contributing_factors", []) or []
    iob_est  = _extract_iob_from_factors(raw_factors)
    basal    = _extract_basal_from_factors(raw_factors)

    if bolus > 0 and iob_est > 0:
        total_ins = bolus + iob_est
        factors.append(
            f"Hay {total_ins:.1f}U de insulina activa entre el bolo y la que quedaba antes"
        )
    elif bolus > 0:
        factors.append(f"El bolo de {bolus:.1f}U estará actuando durante las próximas horas")
    elif iob_est > 0.3:
        factors.append(f"Todavía hay insulina del bolo anterior actuando ({iob_est:.1f}U)")

    # ── Factor 2: basal ───────────────────────────────────────────────────────
    if basal > 0.2:
        # Determinar descriptor temporal de la basal
        basal_time_desc = _basal_time_descriptor(hour_now)
        factors.append(f"Tu Toujeo {basal_time_desc} sigue haciendo efecto a esta hora")

    # ── Factor 3: tendencia bajista / sin carbohidratos ───────────────────────
    has_cob_factor  = any("carb" in f.lower() or "cobertura" in f.lower() for f in raw_factors)
    has_roc_factor  = any("bajand" in f.lower() or "tendencia" in f.lower() for f in raw_factors)

    if has_roc_factor:
        factors.append("La glucemia ya viene bajando en este momento")
    elif has_cob_factor and bolus > 0:
        factors.append("No hay carbohidratos activos que amortigüen la insulina")

    return factors[:3]


def _build_suggestion(severity: str, tone: str, now: datetime) -> str:
    hour = now.hour
    is_nocturnal = hour >= 22 or hour < 6

    if tone == TONE_URGENT:
        if is_nocturnal:
            return (
                "Considerá tomar algo pequeño antes de acostarte "
                "(10–15g de carbohidratos) y activar la alarma del sensor en 70 mg/dL."
            )
        return (
            "Tomá algo pequeño (10–15g de carbohidratos) y mantené el sensor visible."
        )
    elif tone == TONE_ALERT:
        if is_nocturnal:
            return (
                "Tené una colación disponible cerca y activá la alarma del CGM. "
                "No es urgente, pero vale la pena estar preparado."
            )
        return "Tené algo a mano por si la glucemia baja más de lo esperado."
    else:
        if is_nocturnal:
            return (
                "Activar la alarma del sensor en 70 mg/dL es una buena precaución esta noche."
            )
        return "Nada urgente — solo tenerlo en cuenta."


def _build_confidence_note(confidence, ssm_available: bool) -> str:
    mode = confidence.degradation_mode
    score_pct = round(confidence.score * 100)
    source = "modelo SSM" if ssm_available else "estimación simplificada"

    if mode == "full":
        return f"Basado en {source} · confianza {score_pct}%"
    elif mode == "conservative":
        return f"Basado en {source} con datos parciales · confianza {score_pct}%"
    else:
        return f"Estimación con datos limitados · confianza {score_pct}%"


def _build_badge(p70: float, severity: str) -> str:
    p_pct = round(p70 * 100)
    if severity == "critical":
        return f"{p_pct}% riesgo · crítico"
    elif severity == "high":
        return f"{p_pct}% · riesgo alto"
    elif severity == "moderate":
        return f"{p_pct}% · riesgo moderado"
    else:
        return f"{p_pct}% · atención"


def _suppression_reason(confidence) -> str:
    mode = confidence.degradation_mode
    if mode == "silent":
        return confidence.explanation or "Datos insuficientes para evaluar el riesgo."
    elif mode == "observe_only":
        return "El sistema puede observar pero no tiene suficiente confianza para alertar."
    return ""


# ── Helpers de extracción ─────────────────────────────────────────────────────

def _extract_iob_from_factors(factors: list[str]) -> float:
    """
    Extrae el IOB estimado del texto de factores técnicos.
    Busca patrones como "IOB activo (0.3U)" o "0.3U".
    """
    import re
    for f in factors:
        # Buscar "IOB ... (X.XU)"
        m = re.search(r'IOB\s+(?:activo|residual)?\s*\(?(\d+\.?\d*)U?\)?', f, re.IGNORECASE)
        if m:
            return float(m.group(1))
    return 0.0


def _extract_basal_from_factors(factors: list[str]) -> float:
    """
    Extrae el efecto basal estimado del texto de factores técnicos.
    """
    import re
    for f in factors:
        m = re.search(r'(?:basal|Toujeo).*?(\d+\.?\d*)U', f, re.IGNORECASE)
        if m:
            return float(m.group(1))
    return 0.0


def _basal_time_descriptor(hour_now: int) -> str:
    """
    Describe en lenguaje natural cuándo fue la inyección de basal basándose
    en la hora actual. Toujeo se suele inyectar una vez por día.
    """
    # Heurística simple basada en la hora: la mayoría inyectan por la mañana
    if 6 <= hour_now < 14:
        return "de esta mañana"
    elif 14 <= hour_now < 20:
        return "de la mañana"
    else:
        return "de hoy"
