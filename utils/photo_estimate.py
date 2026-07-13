"""
utils/photo_estimate.py
───────────────────────
Estimación de macros desde foto, v2 — identifica POR COMPONENTES y ancla los
macros en la base nutricional interna.

Por qué v2: la v1 (un solo prompt → JSON directo) se equivocaba de alimento y
SOBREESTIMABA carbohidratos. La v2 divide el problema:

  1. El modelo de visión hace lo que hace bien: IDENTIFICAR componentes y
     estimar PORCIONES (gramos) con referencias visuales, razonando paso a
     paso, con instrucción explícita anti-sobreestimación.
  2. Los MACROS de cada componente salen de `utils/nutrition_db` (valores
     curados por 100g × gramos estimados) cuando el alimento está en la base
     ("grounding"). Solo si no está, se usan los números del modelo.

Además se registra estimado→guardado (settings JSON) para medir el sesgo real
con datos y calibrar a futuro.
"""
from __future__ import annotations

import json
import re
from datetime import datetime

# Modelo de visión: Sonnet (la identificación de alimentos y porciones mejora
# mucho vs Haiku). Override por env si hay que bajar costo.
DEFAULT_VISION_MODEL = "claude-sonnet-5"

PROMPT = """Eres un nutricionista experto estimando una comida desde una foto.

Trabaja PASO A PASO antes de responder (piensa primero, responde al final):
1. Identifica CADA componente comestible visible. Sé específico ("arroz blanco
   cocido", "milanesa de pollo empanizada"), no genérico ("cereal", "carne").
2. Estima el PESO en gramos de cada componente TAL COMO SE VE (cocido), usando
   referencias visuales: plato estándar ~26 cm, cubiertos, tamaño de la mano.
3. IMPORTANTE — sesgo conocido: las estimaciones desde foto tienden a
   SOBREESTIMAR los carbohidratos en un 20-30%. Sé conservador: entre dos
   porciones posibles, elige la MENOR. Verduras de hoja, carnes, quesos y
   huevos casi no aportan carbohidratos. No inventes guarniciones que no se
   ven.
4. Calcula los macros de cada componente (tabla por 100 g × peso estimado).
   "carbs" son carbohidratos TOTALES (la fibra va incluida y además aparte).
{hint_block}
Responde SOLO con este JSON, sin texto extra:
{{"name": "nombre corto del plato",
 "confidence": "alta" | "media" | "baja",
 "components": [{{"name": "componente", "grams": <int>, "carbs": <int>,
                 "fiber": <int>, "protein": <int>, "fat": <int>,
                 "calories": <int>}}]}}
Si no se distingue comida: {{"name": "", "confidence": "baja", "components": []}}"""


def build_prompt(hint: str = "") -> str:
    hint_block = ""
    if hint and hint.strip():
        hint_block = (f"\nPISTA DEL USUARIO (priorizala para identificar el plato): "
                      f"«{hint.strip()[:120]}»\n")
    return PROMPT.format(hint_block=hint_block)


def parse_response(text: str) -> dict | None:
    """Extrae el JSON de la respuesta del modelo. None si no hay JSON."""
    m = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def _i(v) -> int:
    try:
        return max(0, int(round(float(v or 0))))
    except (TypeError, ValueError):
        return 0


def ground_components(components: list) -> list[dict]:
    """
    Ancla cada componente en la base nutricional: si `buscar_nutricion` lo
    encuentra, los macros salen de la DB (por 100g × gramos estimados por el
    modelo). Si no, quedan los del modelo. Marca la fuente ("base" | "ia").
    """
    from utils.nutrition_db import estimar

    out = []
    for c in components or []:
        name = (c.get("name") or "").strip()[:120]
        grams = _i(c.get("grams"))
        item = {
            "name": name, "grams": grams,
            "carbs": _i(c.get("carbs")), "fiber": _i(c.get("fiber")),
            "protein": _i(c.get("protein")), "fat": _i(c.get("fat")),
            "calories": _i(c.get("calories")), "source": "ia",
        }
        # nombres muy cortos fuzzy-matchean con cualquier cosa en la base → no anclar
        if len(name) >= 3 and grams > 0:
            try:
                est = estimar(name, grams_usuario=grams)
            except Exception:
                est = None
            if est:
                item.update({
                    "carbs":    _i(est.get("carbs_total")),   # totales (fibra incluida)
                    "fiber":    _i(est.get("fibra_g")),
                    "protein":  _i(est.get("protein_g")),
                    "fat":      _i(est.get("fat_g")),
                    "calories": _i(est.get("calories")),
                    "source":   "base",
                })
        out.append(item)
    return out


def totals(components: list[dict]) -> dict:
    return {
        "carbs":    sum(c["carbs"] for c in components),
        "fiber":    sum(c["fiber"] for c in components),
        "protein":  sum(c["protein"] for c in components),
        "fat":      sum(c["fat"] for c in components),
        "calories": sum(c["calories"] for c in components),
    }


# ─────────────── log estimado→guardado (para medir el sesgo real) ───────────────

_LAST_KEY = "last_photo_estimate"
_LOG_KEY = "photo_estimate_log"
_MAX_LOG = 100


def remember_estimate(name: str, carbs_total: int, fiber: int) -> None:
    """Guarda la última estimación (para compararla con lo que el usuario salve)."""
    from helpers import _set_setting
    try:
        _set_setting(_LAST_KEY, json.dumps({
            "ts": datetime.now().isoformat(), "name": name,
            "carbs_total": carbs_total, "fiber": fiber,
        }, ensure_ascii=False))
    except Exception:
        pass


def log_saved_meal(name: str, carbs_saved: float) -> None:
    """
    Si hubo una estimación por foto hace <30 min, registra el par
    (estimado, guardado). Con el tiempo esto mide el sesgo REAL del sistema.
    """
    from helpers import _get_setting, _set_setting
    try:
        raw = _get_setting(_LAST_KEY)
        if not raw:
            return
        last = json.loads(raw)
        age_min = (datetime.now() - datetime.fromisoformat(last["ts"])).total_seconds() / 60
        if age_min > 30:
            return
        log = json.loads(_get_setting(_LOG_KEY) or "[]")
        log.append({
            "ts": last["ts"], "name": name or last.get("name") or "",
            "est_carbs_net": max(0, last["carbs_total"] - last.get("fiber", 0)),
            "saved_carbs": round(float(carbs_saved or 0), 1),
        })
        _set_setting(_LOG_KEY, json.dumps(log[-_MAX_LOG:], ensure_ascii=False))
        _set_setting(_LAST_KEY, "")   # consumida
    except Exception:
        pass
