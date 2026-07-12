"""
utils/copilot_memory.py
───────────────────────
Memoria del copiloto de Orbit — 100% retrospectiva y descriptiva.

Tres piezas, todas de SOLO LECTURA sobre datos ya registrados:

1. MEMORIA DE COMIDAS: para comidas repetidas (mismo nombre normalizado),
   cuánto subió la glucosa típicamente a las 2h en el pasado. Es estadística
   histórica ("subió"), NO predicción ("va a subir") — la separación
   regulatoria del producto se mantiene.

2. TENDENCIAS 7/30 días: TIR, promedio e hipos por semana, para que el
   copiloto pueda hablar de la evolución y no solo del día.

3. NOTAS PERSISTENTES: cosas que el usuario le pidió recordar al copiloto
   ("recuerda que la pizza me sube de noche"). Viven en settings como JSON.

Los cálculos pesados se cachean en settings con TTL (la memoria de comidas
recorre 60 días de lecturas). Nada aquí escribe en Meal/GlucoseReading.
"""
from __future__ import annotations

import json
import unicodedata
from bisect import bisect_left
from datetime import datetime, timedelta

_MEAL_MEMORY_KEY = "copilot_meal_memory"      # cache JSON {computed_at, items}
_MEAL_MEMORY_TTL_H = 24
_TRENDS_KEY = "copilot_trends_cache"          # cache JSON {computed_at, data}
_TRENDS_TTL_H = 6
_NOTES_KEY = "copilot_user_notes"             # JSON [{ts, text}]
_MAX_NOTES = 20

LOW, HIGH = 70, 180


# ─────────────────────── helpers puros (testeables) ───────────────────────

def normalize_meal_name(name: str) -> str:
    """'Pizza Muzzarella ' → 'pizza muzzarella' (sin tildes, minúsculas)."""
    s = (name or "").strip().lower()
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return " ".join(s.split())


def reading_near(times: list, values: list, target: datetime,
                 tolerance_min: int = 25) -> float | None:
    """Lectura más cercana a `target` (±tolerance). times ordenado asc."""
    if not times:
        return None
    i = bisect_left(times, target)
    best = None
    best_diff = None
    for j in (i - 1, i):
        if 0 <= j < len(times):
            diff = abs((times[j] - target).total_seconds()) / 60.0
            if diff <= tolerance_min and (best_diff is None or diff < best_diff):
                best, best_diff = values[j], diff
    return best


def median(xs: list) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    n = len(s)
    mid = n // 2
    return float(s[mid]) if n % 2 else (s[mid - 1] + s[mid]) / 2.0


# ─────────────────────── 1. memoria de comidas ───────────────────────

def _compute_meal_memory(days: int = 60, min_occurrences: int = 3,
                         max_items: int = 8) -> list[dict]:
    """[{name, n, delta_2h}] — comidas repetidas y su Δglucosa 2h mediana."""
    from models import Meal, GlucoseReading

    since = datetime.now() - timedelta(days=days)
    meals = (Meal.query.filter(Meal.timestamp >= since, Meal.name != "")
             .order_by(Meal.timestamp).all())
    if not meals:
        return []

    reads = (GlucoseReading.query
             .filter(GlucoseReading.timestamp >= since,
                     GlucoseReading.is_artifact == False)  # noqa: E712
             .order_by(GlucoseReading.timestamp).all())
    times = [r.timestamp for r in reads]
    values = [r.value_mgdl for r in reads]

    groups: dict[str, dict] = {}
    for m in meals:
        key = normalize_meal_name(m.name)
        if not key or len(key) < 3:
            continue
        g = groups.setdefault(key, {"display": m.name.strip(), "deltas": [],
                                    "carbs": [], "n": 0})
        g["n"] += 1
        if m.carbs_g:
            g["carbs"].append(m.carbs_g)
        g0 = reading_near(times, values, m.timestamp)
        g2 = reading_near(times, values, m.timestamp + timedelta(hours=2))
        if g0 is not None and g2 is not None:
            g["deltas"].append(g2 - g0)

    items = []
    for key, g in groups.items():
        if g["n"] < min_occurrences or len(g["deltas"]) < 2:
            continue
        items.append({
            "name":      g["display"],
            "n":         g["n"],
            "delta_2h":  round(median(g["deltas"])),
            "carbs_med": round(median(g["carbs"])) if g["carbs"] else None,
        })
    items.sort(key=lambda x: -x["n"])
    return items[:max_items]


def get_meal_memory() -> list[dict]:
    """Memoria de comidas, cacheada en settings (TTL 24h). Nunca levanta."""
    from helpers import _get_setting, _set_setting
    try:
        raw = _get_setting(_MEAL_MEMORY_KEY)
        if raw:
            cached = json.loads(raw)
            age_h = (datetime.now()
                     - datetime.fromisoformat(cached["computed_at"])).total_seconds() / 3600
            if age_h < _MEAL_MEMORY_TTL_H:
                return cached["items"]
    except Exception:
        pass
    try:
        items = _compute_meal_memory()
        _set_setting(_MEAL_MEMORY_KEY, json.dumps(
            {"computed_at": datetime.now().isoformat(), "items": items}))
        return items
    except Exception:
        return []


# ─────────────────────── 2. tendencias 7 / 30 días ───────────────────────

def _window_stats(days: int) -> dict | None:
    from models import GlucoseReading
    since = datetime.now() - timedelta(days=days)
    reads = (GlucoseReading.query
             .filter(GlucoseReading.timestamp >= since,
                     GlucoseReading.is_artifact == False)  # noqa: E712
             .order_by(GlucoseReading.timestamp).all())
    if len(reads) < 24:
        return None
    vals = [r.value_mgdl for r in reads]
    n = len(vals)
    # hipos como EVENTOS (rachas), no lecturas sueltas
    hypo_events = 0
    in_hypo = False
    for v in vals:
        if v < LOW:
            if not in_hypo:
                hypo_events += 1
            in_hypo = True
        else:
            in_hypo = False
    return {
        "days":  days,
        "tir":   round(100 * sum(1 for v in vals if LOW <= v <= HIGH) / n),
        "avg":   int(round(sum(vals) / n)),
        "hypos_per_week": round(hypo_events / days * 7, 1),
    }


def get_trends() -> dict:
    """{'d7': {...}, 'd30': {...}} cacheado 6h. Nunca levanta."""
    from helpers import _get_setting, _set_setting
    try:
        raw = _get_setting(_TRENDS_KEY)
        if raw:
            cached = json.loads(raw)
            age_h = (datetime.now()
                     - datetime.fromisoformat(cached["computed_at"])).total_seconds() / 3600
            if age_h < _TRENDS_TTL_H:
                return cached["data"]
    except Exception:
        pass
    try:
        data = {"d7": _window_stats(7), "d30": _window_stats(30)}
        _set_setting(_TRENDS_KEY, json.dumps(
            {"computed_at": datetime.now().isoformat(), "data": data}))
        return data
    except Exception:
        return {"d7": None, "d30": None}


# ─────────────────────── 3. notas persistentes ───────────────────────

def get_notes() -> list[dict]:
    from helpers import _get_setting
    try:
        return json.loads(_get_setting(_NOTES_KEY) or "[]")
    except Exception:
        return []


def add_note(text: str) -> bool:
    """Guarda una nota corta (dedup, cap). Devuelve True si la guardó."""
    from helpers import _set_setting
    text = " ".join((text or "").split())[:240]
    if len(text) < 4:
        return False
    notes = get_notes()
    if any(n["text"].lower() == text.lower() for n in notes):
        return True   # ya está — idempotente
    notes.append({"ts": datetime.now().strftime("%Y-%m-%d"), "text": text})
    _set_setting(_NOTES_KEY, json.dumps(notes[-_MAX_NOTES:], ensure_ascii=False))
    return True


def extract_remember_request(message: str) -> str | None:
    """
    Si el mensaje le pide al copiloto recordar algo, devuelve el contenido.
    'recuerda que la pizza me sube de noche' → 'la pizza me sube de noche'.
    """
    import re
    m = re.search(
        r"(?:record[aá]|recuerda|acordate|acu[eé]rdate)\s*(?:de\s+)?(?:que\s+)?(.{4,})",
        (message or "").strip(), re.IGNORECASE)
    return m.group(1).strip().rstrip(".!") if m else None


# ─────────────────────── texto para el contexto del LLM ───────────────────────

def memory_context_lines() -> list[str]:
    """Bloques de memoria listos para el system prompt del chat/brief."""
    L = []
    trends = get_trends()
    d7, d30 = trends.get("d7"), trends.get("d30")
    if d7 and d30:
        L.append(f"EVOLUCIÓN: últimos 7 días TIR {d7['tir']}% (promedio {d7['avg']} mg/dL, "
                 f"{d7['hypos_per_week']} hipos/semana) · últimos 30 días TIR {d30['tir']}% "
                 f"(promedio {d30['avg']} mg/dL, {d30['hypos_per_week']} hipos/semana).")
    elif d30:
        L.append(f"EVOLUCIÓN 30 días: TIR {d30['tir']}%, promedio {d30['avg']} mg/dL, "
                 f"{d30['hypos_per_week']} hipos/semana.")

    meals = get_meal_memory()
    if meals:
        parts = []
        for m in meals:
            seg = f"{m['name']} ({m['n']} veces"
            if m.get("carbs_med"):
                seg += f", ~{m['carbs_med']}g CH"
            sign = "+" if m["delta_2h"] >= 0 else ""
            seg += f"): históricamente {sign}{m['delta_2h']} mg/dL a las 2h"
            parts.append(seg)
        L.append("MEMORIA DE COMIDAS (respuesta observada en el pasado, no predicción): "
                 + "; ".join(parts) + ".")

    notes = get_notes()
    if notes:
        L.append("NOTAS QUE EL USUARIO PIDIÓ RECORDAR: "
                 + " | ".join(f"[{n['ts']}] {n['text']}" for n in notes[-8:]))
    return L
