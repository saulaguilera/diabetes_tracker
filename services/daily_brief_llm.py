"""
services/daily_brief_llm.py
────────────────────────────
Daily Metabolic Brief — Capa 2: capa narrativa Claude.

Convierte un DailyMetabolicSummary estructurado (de daily_brief.py) en
una narrativa corta en español. Claude SOLO transforma — no calcula
métricas, no infiere estados, no toma decisiones clínicas.

Si Claude no está disponible o confidence baja → fallback determinístico
rule-based usando los mismos datos del summary.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Optional

from services.daily_brief import DailyMetabolicSummary, summary_to_dict

logger = logging.getLogger("daily_brief.llm")


# ── Configuración ──────────────────────────────────────────────────────
LLM_MODEL          = "claude-sonnet-4-20250514"
LLM_MAX_TOKENS     = 320           # ~80-140 palabras
LLM_TEMPERATURE    = 0.4           # baja — narrativas similares en datos similares
CONFIDENCE_MIN_LLM = 0.5           # bajo eso, usamos fallback rule-based

TONE_VARIANTS = {
    "supportive": (
        "Tono empático, cercano, calmado. Hablás como un compañero que entiende "
        "diabetes, no como un médico ni como un técnico. Frases cortas. "
        "Vos / tu / te (segunda persona)."
    ),
    "neutral": (
        "Tono informativo y directo, sin emojis ni exclamaciones. "
        "Hechos primero, observaciones después. Segunda persona vos / tu."
    ),
    "clinical_light": (
        "Tono profesional pero amigable. Usá términos clínicos básicos "
        "(TIR, variabilidad, glucemia media) sin tecnicismos extremos. "
        "Segunda persona vos / tu."
    ),
}


@dataclass
class BriefResult:
    """Resultado de generar un brief."""
    narrative:    str
    llm_used:     bool                      # True si Claude generó, False si fallback
    llm_model:    Optional[str] = None
    tokens_in:    Optional[int] = None
    tokens_out:   Optional[int] = None
    latency_ms:   Optional[int] = None
    prompt:       Optional[str] = None
    error:        Optional[str] = None


# ─── Generación principal ───────────────────────────────────────────────

def generate_brief(
    summary:  DailyMetabolicSummary,
    tone:     str = "supportive",
    name:     Optional[str] = None,
) -> BriefResult:
    """
    Genera la narrativa diaria a partir del summary estructurado.

    Decisión LLM vs fallback:
      - has_sufficient_data == False → fallback "no hubo suficientes datos"
      - confidence < 0.5             → fallback con métricas básicas
      - ANTHROPIC_API_KEY no set     → fallback rule-based
      - LLM falla                    → fallback rule-based
      - Else                         → Claude
    """
    # ── Safety gates ──
    if not summary.has_sufficient_data:
        return BriefResult(
            narrative=_fallback_insufficient_data(summary, name=name),
            llm_used=False,
        )
    if summary.confidence < CONFIDENCE_MIN_LLM:
        return BriefResult(
            narrative=_fallback_partial_data(summary, name=name),
            llm_used=False,
        )

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return BriefResult(
            narrative=_fallback_rule_based(summary, name=name),
            llm_used=False,
            error="ANTHROPIC_API_KEY not set",
        )

    # ── LLM path ──
    try:
        system_prompt, user_prompt = _build_prompts(summary, tone=tone, name=name)
        t0 = time.time()
        from anthropic import Anthropic
        client = Anthropic(api_key=api_key)
        resp = client.messages.create(
            model=LLM_MODEL,
            max_tokens=LLM_MAX_TOKENS,
            temperature=LLM_TEMPERATURE,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        latency_ms = int((time.time() - t0) * 1000)

        # Extraer texto del response
        text = ""
        if hasattr(resp, "content") and resp.content:
            text = "".join(getattr(b, "text", "") for b in resp.content).strip()
        if not text:
            raise RuntimeError("empty response from Claude")

        # Validación post-hoc de la respuesta
        text = _post_validate(text, summary)

        return BriefResult(
            narrative=text,
            llm_used=True,
            llm_model=LLM_MODEL,
            tokens_in=getattr(resp.usage, "input_tokens", None) if hasattr(resp, "usage") else None,
            tokens_out=getattr(resp.usage, "output_tokens", None) if hasattr(resp, "usage") else None,
            latency_ms=latency_ms,
            prompt=f"SYSTEM:\n{system_prompt}\n\nUSER:\n{user_prompt}",
        )

    except Exception as exc:
        logger.exception("Claude generation failed, falling back")
        return BriefResult(
            narrative=_fallback_rule_based(summary, name=name),
            llm_used=False,
            error=str(exc),
        )


# ─── Prompts ────────────────────────────────────────────────────────────

def _build_prompts(s: DailyMetabolicSummary,
                    tone: str,
                    name: Optional[str]) -> tuple[str, str]:
    """Construye system + user prompt."""
    tone_desc = TONE_VARIANTS.get(tone, TONE_VARIANTS["supportive"])

    system_prompt = f"""Sos el redactor del "Daily Metabolic Brief" — un resumen diario corto
del estado glucémico para una persona con diabetes tipo 1.

{tone_desc}

REGLAS DE GROUNDEDNESS — son inviolables:
- USÁ SOLO los datos del JSON estructurado que recibís en el mensaje.
- NO inventes números, eventos, comidas, bolos, ni patrones que no estén ahí.
- NO calcules nada. No hagas matemática. Los números ya están calculados.
- Si un campo es null/None/falta, NO lo menciones. No digas "no tenés datos
  de X" — simplemente omitilo.

REGLAS DE TONO Y SEGURIDAD:
- 2 párrafos máximo. Entre 80 y 140 palabras TOTAL.
- NUNCA des recomendaciones de dosis concretas (ej. "subí 1U la basal").
- NUNCA digas "tenés que" — usá "podrías", "tal vez", "considerá".
- NUNCA uses lenguaje alarmista ("peligroso", "crítico", "alerta") incluso
  con hipos. Usá: "tuviste un episodio bajo a las X" o "tu glucosa cruzó
  bajo 70 en la madrugada".
- NUNCA hagas diagnósticos. NO digas "tenés resistencia" / "fenómeno del alba" /
  "lipodistrofia" / etc. Usá descripciones de patrones: "glucemias matutinas
  más altas de lo habitual".
- Si hay hipos: mencionalas con calma. Sugerí revisar con el médico, no des
  pauta tú mismo.
- Empezá SIEMPRE con "Buenos días{', ' + name if name else ''}." como saludo.

ESTRUCTURA DESEADA (suave, no rígida):
  Párrafo 1: cómo fue el día/noche en términos generales (overnight + TIR).
  Párrafo 2: observación destacable o patrón + nota positiva o suave."""

    summary_json = summary_to_dict(s)
    user_prompt = f"""Generá el Daily Brief para el día {s.day}.

Datos estructurados (USÁ SOLO ESTO, no inventes nada):

```json
{_compact_summary_for_prompt(summary_json)}
```

Recordá: 2 párrafos, 80-140 palabras, empieza con "Buenos días{', ' + name if name else ''}."."""

    return system_prompt, user_prompt


def _compact_summary_for_prompt(d: dict) -> str:
    """Filtra campos None/null para no contaminar el prompt."""
    import json
    cleaned = {k: v for k, v in d.items()
               if v is not None and v != "" and v != []}
    return json.dumps(cleaned, indent=2, ensure_ascii=False, default=str)


# ─── Validación post-hoc ────────────────────────────────────────────────

# Palabras prohibidas/alarmistas
_FORBIDDEN_WORDS = [
    "peligros",     # peligroso, peligrosamente
    "crítico", "critico", "alerta roja", "emergencia",
    "resistencia a la insulina",      # diagnóstico
    "lipodistrofia", "lipohipertrofia",
    "fenómeno del alba",              # diagnóstico
    "subí",  "bajá", "aumentá", "reducí",   # imperativos médicos sobre dosis
    "unidades de basal", "unidades de bolo",
]


def _post_validate(text: str, s: DailyMetabolicSummary) -> str:
    """
    Validación de la salida de Claude:
    - Si contiene palabra prohibida → fallback completo (más seguro)
    - Si excede 200 palabras → truncar suavemente al final del párrafo
    - Si no empieza con "Buenos días" → prepend
    """
    text = text.strip()

    # 1. Palabras prohibidas → degradar a fallback
    text_low = text.lower()
    for w in _FORBIDDEN_WORDS:
        if w in text_low:
            logger.warning(f"Claude usó palabra prohibida '{w}', fallback")
            return _fallback_rule_based(s)

    # 2. Longitud
    words = text.split()
    if len(words) > 200:
        # Truncar al final del primer punto que esté pasada la marca 140
        snippet = " ".join(words[:140])
        end = max(snippet.rfind("."), snippet.rfind("!"), snippet.rfind("?"))
        if end > 0:
            text = snippet[:end + 1]
        else:
            text = snippet + "."

    # 3. Saludo obligatorio
    if not text.lower().startswith("buenos días"):
        text = "Buenos días. " + text

    return text


# ─── Fallbacks rule-based ───────────────────────────────────────────────

def _greeting(name: Optional[str]) -> str:
    return f"Buenos días{', ' + name if name else ''}."


def _fallback_insufficient_data(s: DailyMetabolicSummary,
                                  name: Optional[str] = None) -> str:
    """Cuando no hay data suficiente para sacar conclusiones."""
    return (
        f"{_greeting(name)} No hubo suficientes datos continuos del CGM "
        f"para generar un resumen detallado hoy ({s.n_readings} lecturas "
        f"sobre las {s.expected_readings} esperadas). "
        f"Verificá que el sensor esté sincronizando correctamente. "
        f"Cuando los datos se acumulen, volveremos a tener un brief completo mañana."
    )


def _fallback_partial_data(s: DailyMetabolicSummary,
                            name: Optional[str] = None) -> str:
    """Confidence intermedio — datos básicos sin interpretación."""
    parts = [_greeting(name)]
    if s.avg_glucose_24h:
        parts.append(f"Tu glucemia media de las últimas 24h fue {s.avg_glucose_24h:.0f} mg/dL.")
    if s.tir_24h is not None:
        parts.append(f"Tiempo en rango: {s.tir_24h:.0f}%.")
    if s.hypo_events_24h > 0:
        parts.append(f"Hubo {s.hypo_events_24h} episodio(s) bajo 70 mg/dL.")
    parts.append(f"La data fue parcial (gap máximo {s.max_gap_minutes}min); "
                 "para un resumen más completo, asegurate de que el sensor "
                 "se sincronice bien durante todo el día.")
    return " ".join(parts)


def _fallback_rule_based(s: DailyMetabolicSummary,
                          name: Optional[str] = None) -> str:
    """
    Narrativa completa sin LLM. Templates por overnight_stability + dominant_pattern.
    Mismas restricciones de tono.
    """
    parts = [_greeting(name)]

    # ── Párrafo 1: overnight + TIR ──
    ov_map = {
        "stable":           "La noche fue estable, con baja variabilidad",
        "mildly_variable":  "La noche tuvo algunos vaivenes pero sin sobresaltos",
        "unstable":         "La noche estuvo más movida de lo habitual",
        "no_data":          "No hay datos suficientes de la noche",
    }
    p1 = ov_map.get(s.overnight_stability, "")
    if s.overnight_mean_glucose and s.overnight_stability != "no_data":
        p1 += f" (media {s.overnight_mean_glucose:.0f} mg/dL"
        if s.overnight_variability_cv:
            p1 += f", CV {s.overnight_variability_cv:.0f}%"
        p1 += ")"
    if s.overnight_hypos > 0:
        p1 += f". Tuviste {s.overnight_hypos} episodio(s) bajo 70 mg/dL durante la noche"
    p1 += ". "

    if s.tir_24h is not None:
        if s.tir_24h >= 75:
            p1 += f"Tu tiempo en rango fue {s.tir_24h:.0f}% — un día sólido."
        elif s.tir_24h >= 60:
            p1 += f"Tu tiempo en rango fue {s.tir_24h:.0f}%."
        else:
            p1 += f"Tu tiempo en rango fue {s.tir_24h:.0f}% — más bajo que un día típico."

    parts.append(p1)

    # ── Párrafo 2: observación destacable ──
    p2_pieces = []
    if s.notable_observation:
        p2_pieces.append(s.notable_observation)

    if s.exercise_impact == "positive":
        p2_pieces.append("El ejercicio te ayudó a mantener glucemias más controladas.")
    elif s.dominant_pattern == "post_dinner_rise":
        p2_pieces.append("La cena marcó una subida importante — quizás valga revisar el timing del bolo.")
    elif s.dominant_pattern == "stable_day":
        p2_pieces.append("En general fue un día con buen control. Seguir así.")

    if p2_pieces:
        parts.append(" ".join(p2_pieces))
    else:
        parts.append("Sin patrones destacables hoy.")

    return " ".join(parts)
