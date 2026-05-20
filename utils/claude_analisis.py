"""
utils/claude_analisis.py — Capa 3: Análisis narrativo con Claude API.

Toma los datos del usuario (serie glucémica, patrones detectados, métricas)
y genera un análisis personalizado en lenguaje natural usando Claude.

Función pública:
    generar_analisis(days=2) → dict con:
        analisis   : str   — texto en Markdown
        modelo     : str   — modelo usado
        tokens     : dict  — input/output tokens
        generado_en: str   — timestamp ISO
        error      : str|None
"""

from __future__ import annotations

import os
from datetime import datetime

import anthropic

from utils.patrones_detector import analizar_patrones


# ── Configuración ────────────────────────────────────────────────────────────
_MODELO        = "claude-opus-4-5"   # mejor razonamiento para análisis médico
_MAX_TOKENS    = 1024
_SERIE_PUNTOS  = 120   # máximo de puntos de glucosa enviados (~30h a 15-min bins)


def _resumir_serie(serie: list[dict]) -> str:
    """
    Convierte la serie glucémica en texto compacto para el prompt.
    Formato: HH:MM G mg/dL [comida: X] [insulina: Y]
    Solo incluye los últimos _SERIE_PUNTOS puntos.
    """
    ultimos = serie[-_SERIE_PUNTOS:]
    lineas = []
    for p in ultimos:
        ts = p["ts"][11:16]   # solo HH:MM
        linea = f"{ts} {p['g']} mg/dL"
        if p.get("comida"):
            linea += f" | 🍽 {p['comida']}"
        if p.get("insulina"):
            linea += f" | 💉 {p['insulina']}"
        lineas.append(linea)
    return "\n".join(lineas)


def _formatear_patrones(patrones: list[dict]) -> str:
    if not patrones:
        return "No se detectaron patrones de riesgo recurrentes en el período."
    lineas = []
    for p in patrones:
        lineas.append(f"• **{p['titulo']}** ({p['nivel']})")
        lineas.append(f"  {p['detalle']}")
    return "\n".join(lineas)


def _construir_prompt(datos: dict) -> tuple[str, str]:
    """
    Devuelve (system_prompt, user_message).
    """
    resumen = datos["resumen"]
    patrones = datos["patrones"]
    serie = datos["serie_glucose"]
    days  = datos["days"]

    # ── System prompt ────────────────────────────────────────────────────────
    system = (
        "Sos un asistente especializado en análisis de datos de glucosa para personas "
        "con diabetes tipo 1. Tu objetivo es ayudar al usuario a entender sus patrones "
        "glucémicos con explicaciones claras, empáticas y basadas en los datos reales.\n\n"
        "Reglas importantes:\n"
        "- Respondé siempre en español, en segunda persona (vos/te).\n"
        "- Usá lenguaje claro, sin jerga médica innecesaria.\n"
        "- Basate SOLO en los datos proporcionados; no inventes información.\n"
        "- Nunca des instrucciones directas de cambio de dosis; sugerí consultar "
        "al médico o endocrinólogo.\n"
        "- Si los datos son insuficientes, indicalo honestamente.\n"
        "- Usá markdown (negritas, listas, encabezados ##) para estructurar la respuesta.\n"
        "- Sé conciso: máximo 400 palabras."
    )

    # ── Métricas principales ──────────────────────────────────────────────────
    metricas = "Sin datos suficientes."
    if resumen["n_lecturas"] > 0:
        partes = [f"- **Lecturas analizadas:** {resumen['n_lecturas']} (últimos {days} días)"]
        if resumen["avg"]:
            partes.append(f"- **Promedio glucosa:** {resumen['avg']} mg/dL")
        if resumen["sd"]:
            partes.append(f"- **Desviación estándar:** {resumen['sd']} mg/dL")
        if resumen["cv"]:
            partes.append(f"- **Coeficiente de variación (CV%):** {resumen['cv']}%  "
                          f"{'⚠️ elevado' if resumen['cv'] > 36 else '✓ dentro del objetivo'}")
        if resumen["tir"] is not None:
            partes.append(f"- **Tiempo en rango (70–180):** {resumen['tir']}%  "
                          f"{'✓ meta alcanzada' if resumen['tir'] >= 70 else '⚠️ por debajo del objetivo (≥70%)'}")
        if resumen["hipo_pct"] is not None:
            partes.append(f"- **Tiempo en hipoglucemia (<70):** {resumen['hipo_pct']}%")
        if resumen["hiper_pct"] is not None:
            partes.append(f"- **Tiempo en hiperglucemia (>180):** {resumen['hiper_pct']}%")
        metricas = "\n".join(partes)

    # ── Serie glucémica ───────────────────────────────────────────────────────
    serie_txt = _resumir_serie(serie) if serie else "Sin datos de CGM disponibles."

    # ── Mensaje de usuario ────────────────────────────────────────────────────
    user = f"""Analizá mis datos de glucosa de los últimos {days} días y decime qué ves.

## Métricas generales
{metricas}

## Patrones detectados automáticamente
{_formatear_patrones(patrones)}

## Serie glucémica reciente (HH:MM · glucosa · eventos)
```
{serie_txt}
```

Por favor:
1. Explicame en pocas palabras qué está pasando con mi glucosa.
2. Señalá los patrones más importantes que ves en la serie y en las métricas.
3. Dame 2–3 sugerencias concretas que pueda llevar a mi próxima consulta médica.
"""

    return system, user


def generar_analisis(days: int = 2) -> dict:
    """
    Genera un análisis narrativo con Claude API.

    Args:
        days: Días de histórico a analizar (default 2 = últimas 48h para serie,
              pero usa 30 días para detección de patrones).

    Returns:
        dict con keys: analisis, modelo, tokens, generado_en, error
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return {
            "analisis":    None,
            "modelo":      None,
            "tokens":      None,
            "generado_en": datetime.now().isoformat(),
            "error":       "ANTHROPIC_API_KEY no configurada.",
        }

    try:
        # Obtener datos: patrones y serie de los últimos 30 días,
        # pero la serie compacta se limita a los últimos _SERIE_PUNTOS puntos (~30h)
        datos = analizar_patrones(days=30)
        # Para la serie, re-generamos solo con los últimos `days` días si se pide menos
        if days < 30:
            from utils.patrones_detector import analizar_patrones as _ap
            datos_cortos = _ap(days=days)
            datos["serie_glucose"] = datos_cortos["serie_glucose"]
            datos["days"] = days

        system_prompt, user_message = _construir_prompt(datos)

        cliente = anthropic.Anthropic(api_key=api_key)
        respuesta = cliente.messages.create(
            model=_MODELO,
            max_tokens=_MAX_TOKENS,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )

        texto = respuesta.content[0].text if respuesta.content else ""
        tokens = {
            "input":  respuesta.usage.input_tokens,
            "output": respuesta.usage.output_tokens,
        }

        return {
            "analisis":    texto,
            "modelo":      respuesta.model,
            "tokens":      tokens,
            "generado_en": datetime.now().isoformat(),
            "error":       None,
        }

    except anthropic.AuthenticationError:
        return {
            "analisis":    None,
            "modelo":      None,
            "tokens":      None,
            "generado_en": datetime.now().isoformat(),
            "error":       "API key inválida. Verificá ANTHROPIC_API_KEY.",
        }
    except anthropic.RateLimitError:
        return {
            "analisis":    None,
            "modelo":      None,
            "tokens":      None,
            "generado_en": datetime.now().isoformat(),
            "error":       "Límite de rate alcanzado. Intentá en unos minutos.",
        }
    except Exception as e:
        return {
            "analisis":    None,
            "modelo":      None,
            "tokens":      None,
            "generado_en": datetime.now().isoformat(),
            "error":       str(e),
        }
