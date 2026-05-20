"""
utils/foto_analisis.py
──────────────────────
Capa de visión: analiza una foto de comida con Claude y devuelve
macronutrientes estimados por ingrediente.

Modelo: claude-3-5-sonnet-20241022  (vision capable, mejor accuracy en comida)
Costo típico: ~$0.01–0.02 por foto (imagen ~1500 tok + respuesta ~400 tok)
"""

from __future__ import annotations
import base64
import json
import os

import anthropic

# ── Config ────────────────────────────────────────────────────────────────────
_MODELO        = "claude-3-5-sonnet-20241022"
_MAX_TOKENS    = 1400
_MAX_IMG_BYTES = 5 * 1024 * 1024   # 5 MB


# ── Prompts ───────────────────────────────────────────────────────────────────
_SYSTEM = """Eres un asistente nutricional especializado en Diabetes Tipo 1.
Analizas fotos de comida y estimás macronutrientes con máxima precisión,
especialmente carbohidratos, que son críticos para dosificar insulina correctamente.

REGLAS CLÍNICAS (obligatorias):
- Priorizá precisión en carbohidratos (CH) por encima de cualquier otro macro.
- Si hay incertidumbre en la porción, indicá un rango en el campo "nota" (ej. "30-45 g CH").
- Incluí salsas, aderezos, bebidas y guarniciones visibles como ingredientes separados.
- Si la imagen no muestra comida con claridad, devolvé lista vacía y explicá en "advertencia".
- NUNCA recomiendes dosis de insulina ni hagas menciones a bolo/corrección.
- Sobreestimar y subestimar CH son igualmente peligrosos; sé realista.
"""

_PROMPT = """Analizá esta foto de comida. Respondé ÚNICAMENTE con JSON válido, sin texto extra ni markdown.

Formato exacto requerido:
{
  "nombre_plato": "descripción breve del plato completo",
  "ingredientes": [
    {
      "nombre": "nombre del alimento",
      "gramos_estimados": 150,
      "carbs_g": 35.0,
      "protein_g": 12.0,
      "fat_g": 8.0,
      "calorias": 260,
      "confianza": "alta",
      "nota": ""
    }
  ],
  "advertencia": "",
  "confianza_general": "alta"
}

Valores de "confianza":
- "alta"  → alimento claramente identificable Y porción bien estimable
- "media" → alimento reconocible pero porción incierta (da rango en nota)
- "baja"  → difícil de identificar o estimar; explicá por qué en nota

Si el plato tiene múltiples componentes (arroz, proteína, ensalada, salsa),
listá cada uno como ingrediente separado.
"""


# ── Función principal ──────────────────────────────────────────────────────────
def analizar_foto(image_bytes: bytes, media_type: str = "image/jpeg") -> dict:
    """
    Analiza una foto de comida con Claude Vision.

    Parámetros
    ----------
    image_bytes : bytes  — contenido binario de la imagen
    media_type  : str   — MIME type (image/jpeg, image/png, image/webp)

    Retorna
    -------
    dict con keys:
        ok               bool
        nombre_plato     str
        ingredientes     list[dict]  — cada uno con nombre, macros, confianza, nota
        advertencia      str
        confianza_general str
        modelo           str
        tokens           dict {input, output}
        error            str | None
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return {"ok": False, "error": "ANTHROPIC_API_KEY no configurada"}

    if len(image_bytes) > _MAX_IMG_BYTES:
        mb = len(image_bytes) / (1024 * 1024)
        return {"ok": False, "error": f"Imagen demasiado grande ({mb:.1f} MB). Máximo 5 MB."}

    # Tipos soportados por la API
    if media_type not in ("image/jpeg", "image/png", "image/gif", "image/webp"):
        media_type = "image/jpeg"

    b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
    client = anthropic.Anthropic(api_key=api_key)

    raw = ""
    try:
        resp = client.messages.create(
            model=_MODELO,
            max_tokens=_MAX_TOKENS,
            system=_SYSTEM,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": _PROMPT,
                    },
                ],
            }],
        )

        raw = resp.content[0].text.strip()

        # Limpiar posible markdown que Claude a veces agrega
        if raw.startswith("```"):
            parts = raw.split("```")
            raw = parts[1] if len(parts) >= 2 else raw
            if raw.lower().startswith("json"):
                raw = raw[4:]
            raw = raw.strip()

        data = json.loads(raw)

        # Normalizar tipos numéricos en ingredientes
        for ing in data.get("ingredientes", []):
            for campo in ("carbs_g", "protein_g", "fat_g"):
                ing[campo] = round(float(ing.get(campo) or 0), 1)
            ing["calorias"]         = int(ing.get("calorias") or 0)
            ing["gramos_estimados"] = int(ing.get("gramos_estimados") or 0)
            ing.setdefault("confianza", "media")
            ing.setdefault("nota", "")

        return {
            "ok":               True,
            "nombre_plato":     data.get("nombre_plato", ""),
            "ingredientes":     data.get("ingredientes", []),
            "advertencia":      data.get("advertencia", ""),
            "confianza_general": data.get("confianza_general", "media"),
            "modelo":           _MODELO,
            "tokens": {
                "input":  resp.usage.input_tokens,
                "output": resp.usage.output_tokens,
            },
            "error": None,
        }

    except json.JSONDecodeError as exc:
        return {
            "ok":    False,
            "error": f"La IA devolvió una respuesta inesperada. Intentá de nuevo.",
            "_raw":  raw[:300],
        }
    except anthropic.APIError as exc:
        return {"ok": False, "error": f"Error API: {exc}"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
