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
LLM_MAX_TOKENS     = 220           # ~60-100 palabras (más conciso)
LLM_TEMPERATURE    = 0.5           # un poco más cálido / variado
CONFIDENCE_MIN_LLM = 0.5           # bajo eso, usamos fallback rule-based

# Nombre por defecto si no hay user_name en settings — el usuario puede
# cambiarlo con: _set_setting("user_name", "MiNombre") via shell.
DEFAULT_USER_NAME = "Saúl"

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
    """Construye system + user prompt — versión compacta y personalizada."""
    tone_desc = TONE_VARIANTS.get(tone, TONE_VARIANTS["supportive"])
    user_name = name or DEFAULT_USER_NAME

    system_prompt = f"""Sos un compañero cercano que le manda a {user_name} un resumen breve
de su día metabólico. NO sos un médico ni un dashboard frío — sos esa
amistad que se preocupa, sabe de diabetes tipo 1, y le manda 3 frases
cariñosas pero útiles cada mañana.

{tone_desc}

ESTRUCTURA OBLIGATORIA (3 frases cortas, en 1 párrafo único):

  Frase 1 — Saludo + resumen glicemia
    "Hola {user_name}, buenos días. [cómo estuvo tu glicemia en términos
    humanos: estable / con vaivenes / con un episodio bajo en la noche / etc]"

  Frase 2 — Un patrón observado (NO genérico)
    Mencionar UN solo patrón o dato destacable: "tu noche fue muy estable",
    "después de la cena tuviste una subida importante", "el ejercicio te
    ayudó", "hubo un episodio bajo a las X". Usar EL dato más relevante
    del JSON, no inventar.

  Frase 3 — Un consejo concreto y suave
    Una sugerencia accionable y amable. Ejemplos: "considerá pre-bolear
    un poco antes esta noche", "buen momento para repetir lo que hiciste
    hoy", "tal vez una caminata después del almuerzo te ayudaría",
    "andá tranquilo, vas bien". SIN dosis concretas, SIN "tenés que".

REGLAS INVIOLABLES:
- USÁ SOLO los datos del JSON. NO inventes números, eventos, ni patrones.
- Si un campo es null/None, NO lo menciones. Omitilo y elegí otro.
- 60–90 palabras TOTAL en el párrafo único. Mejor 70 que 90.
- Tono cariñoso, cercano, calmado. Como un mejor amigo que sabe de diabetes.
- NUNCA "peligroso/crítico/alerta", NUNCA dosis concretas, NUNCA diagnósticos
  ("resistencia", "fenómeno del alba", "lipodistrofia").
- Si hay hipos: mencionalas con calma, sin alarmar.
- NO usar bullets, NO títulos, NO emojis. Solo prosa fluida."""

    summary_json = summary_to_dict(s)
    user_prompt = f"""Datos de hoy ({s.day}) — usá SOLO esto:

```json
{_compact_summary_for_prompt(summary_json)}
```

Escribí el resumen de 3 frases para {user_name}. Recordá: 1 solo párrafo,
60-90 palabras, empezando con "Hola {user_name}, buenos días."."""

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

    # 3. Saludo obligatorio — acepta ambos formatos
    low = text.lower()
    if not (low.startswith("hola") or low.startswith("buenos días")):
        text = f"Hola, buenos días. {text}"

    return text


# ─── Fallbacks rule-based ───────────────────────────────────────────────

def _greeting(name: Optional[str]) -> str:
    """Saludo cálido: 'Hola Saúl, buenos días.'"""
    n = name or DEFAULT_USER_NAME
    return f"Hola {n}, buenos días."


def _fallback_insufficient_data(s: DailyMetabolicSummary,
                                  name: Optional[str] = None) -> str:
    """Cuando no hay data suficiente. Tono cariñoso, no técnico."""
    return (
        f"{_greeting(name)} Hoy no hubo suficientes datos del sensor para "
        f"darte un resumen detallado — quizás vale la pena chequear que el "
        f"CGM esté sincronizando bien. Mañana volvemos con todo."
    )


def _fallback_partial_data(s: DailyMetabolicSummary,
                            name: Optional[str] = None) -> str:
    """Confidence intermedio — versión compacta."""
    parts = [_greeting(name)]
    if s.tir_24h is not None and s.avg_glucose_24h:
        parts.append(f"Glucemia promedio {s.avg_glucose_24h:.0f} mg/dL, "
                     f"con {s.tir_24h:.0f}% del tiempo en rango.")
    if s.hypo_events_24h > 0:
        if s.hypo_events_24h == 1:
            parts.append("Hubo un episodio bajo 70.")
        else:
            parts.append(f"Hubo {s.hypo_events_24h} episodios bajo 70.")
    parts.append("Los datos vinieron parciales hoy — revisá que el sensor "
                 "esté bien anclado para no perder lecturas.")
    return " ".join(parts)


def _fallback_rule_based(s: DailyMetabolicSummary,
                          name: Optional[str] = None) -> str:
    """
    Narrativa compacta sin LLM — 3 frases:
      1) Saludo + resumen glicemia
      2) Patrón destacable
      3) Consejo suave personalizado al patrón
    Total ~60-90 palabras. Mismas restricciones de tono que el LLM.
    """
    # ── Frase 1: saludo + resumen glicemia ──
    ov_map = {
        "stable":           "Tu noche estuvo muy tranquila",
        "mildly_variable":  "Tu noche tuvo algunos vaivenes pero sin sobresaltos",
        "unstable":         "Tu noche estuvo movida",
        "no_data":          "No tengo datos completos de la noche",
    }
    f1_parts = [_greeting(name), ov_map.get(s.overnight_stability, "Tu noche transcurrió") + "."]
    if s.tir_24h is not None:
        if s.tir_24h >= 75:
            f1_parts.append(f"El día tuvo un buen control ({s.tir_24h:.0f}% en rango).")
        elif s.tir_24h >= 60:
            f1_parts.append(f"El día estuvo aceptable ({s.tir_24h:.0f}% en rango).")
        else:
            f1_parts.append(f"El día estuvo más complicado ({s.tir_24h:.0f}% en rango).")
    f1 = " ".join(f1_parts)

    # ── Frase 2: un patrón ──
    f2 = ""
    if s.overnight_hypos > 0:
        if s.overnight_hypos == 1:
            f2 = "Tuviste un episodio bajo 70 mg/dL durante la noche."
        else:
            f2 = f"Tuviste {s.overnight_hypos} episodios bajo 70 mg/dL durante la noche."
    elif s.dominant_pattern == "post_dinner_rise":
        f2 = "La cena marcó una subida importante en las horas siguientes."
    elif s.dominant_pattern == "recurrent_morning_high":
        f2 = "Tus glucemias matutinas estuvieron más altas de lo habitual."
    elif s.dominant_pattern == "late_hypo":
        f2 = "Hubo un episodio bajo en la madrugada — algo a tener en mente."
    elif s.dominant_pattern == "exercise_improvement":
        f2 = "El ejercicio te ayudó a mantener mejor control."
    elif s.dominant_pattern == "high_variability":
        f2 = "El día tuvo bastante variabilidad — más vaivenes que un día típico."
    elif s.dominant_pattern == "stable_day":
        f2 = "Día parejo, sin grandes picos ni caídas."
    elif s.exercise_impact == "positive":
        f2 = "El ejercicio del día te ayudó con el control."
    elif s.notable_observation:
        f2 = s.notable_observation
    else:
        f2 = "Sin patrones particulares para destacar hoy."

    # ── Frase 3: consejo suave ──
    if s.overnight_hypos > 0 or s.dominant_pattern == "late_hypo":
        f3 = "Quizás valga la pena revisar la basal con tu médico."
    elif s.dominant_pattern == "post_dinner_rise":
        f3 = "Tal vez pre-bolear un poco antes esta noche te ayude."
    elif s.dominant_pattern == "recurrent_morning_high":
        f3 = "Algo a comentar en tu próxima consulta médica."
    elif s.dominant_pattern == "high_variability":
        f3 = "Andá tranquilo, los días así pasan — mañana es otro día."
    elif s.dominant_pattern in ("stable_day", "exercise_improvement"):
        f3 = "Buen momento para repetir lo que estás haciendo."
    elif s.tir_24h is not None and s.tir_24h >= 70:
        f3 = "Seguís yendo bien, así que sin cambios necesarios."
    else:
        f3 = "Día a día, vamos viendo."

    return f"{f1} {f2} {f3}"
