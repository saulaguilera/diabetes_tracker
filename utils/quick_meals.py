"""
utils/quick_meals.py
────────────────────
"Mis comidas": agrupa el historial de comidas para re-registrar en 1 tap.

Puro (testeable sin DB): recibe filas ya cargadas y devuelve los candidatos
ordenados por uso. La consistencia de nombres que esto fomenta alimenta la
memoria de comidas del copiloto (mismo normalizador).
"""
from __future__ import annotations

from datetime import datetime

from utils.copilot_memory import normalize_meal_name, median


def group_quick_meals(rows: list[dict], max_items: int = 8,
                      min_count: int = 2) -> list[dict]:
    """
    rows: [{name, carbs, protein, fat, ts}] (ts datetime, más viejo o nuevo da igual).
    Devuelve top comidas por frecuencia (mediana de macros), y garantiza que la
    comida MÁS RECIENTE esté incluida aunque sea única (repetir la última cena
    es el caso más común).
    """
    groups: dict[str, dict] = {}
    for r in rows:
        key = normalize_meal_name(r.get("name") or "")
        if len(key) < 3:
            continue
        g = groups.setdefault(key, {"names": [], "carbs": [], "protein": [],
                                    "fat": [], "last": None})
        g["names"].append((r["ts"], (r.get("name") or "").strip()))
        g["carbs"].append(r.get("carbs") or 0)
        g["protein"].append(r.get("protein") or 0)
        g["fat"].append(r.get("fat") or 0)
        if g["last"] is None or r["ts"] > g["last"]:
            g["last"] = r["ts"]

    def _item(key: str, g: dict) -> dict:
        display = max(g["names"])[1]          # nombre más reciente tal cual se escribió
        return {
            "name":    display,
            "n":       len(g["names"]),
            "carbs":   int(round(median(g["carbs"]) or 0)),
            "protein": int(round(median(g["protein"]) or 0)),
            "fat":     int(round(median(g["fat"]) or 0)),
            "last":    g["last"].isoformat() if isinstance(g["last"], datetime) else str(g["last"]),
        }

    frecuentes = sorted(
        (_item(k, g) for k, g in groups.items() if len(g["names"]) >= min_count),
        key=lambda x: (-x["n"], x["last"]), reverse=False)
    frecuentes = sorted(frecuentes, key=lambda x: -x["n"])[:max_items]

    # garantizar la más reciente de TODAS (aunque n=1)
    if groups:
        reciente_key = max(groups, key=lambda k: groups[k]["last"])
        reciente = _item(reciente_key, groups[reciente_key])
        if all(normalize_meal_name(f["name"]) != reciente_key for f in frecuentes):
            frecuentes = [reciente] + frecuentes[:max_items - 1]
    return frecuentes
