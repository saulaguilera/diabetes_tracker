"""
bench/tuning/pareto.py
───────────────────────
Pareto frontier analysis para selección de configs en multi-objective tuning.

Problema
--------
Un grid search produce N configs con K métricas (MAE, ECE, recall, σ̄, etc.).
Ranking por composite scalar (promotion_score) pierde info: dos configs
con score similar pueden ser muy distintas (una "honesta pero ancha", otra
"sharp pero borderline calibrada").

El Pareto frontier filtra el ranking:
  Config A domina B si A es ≥ B en todos los objetivos y > en al menos uno.
  El frontier = configs no dominadas.

Workflow
--------
  1. Define objectives (qué minimizar / maximizar)
  2. dominance_filter() → configs no dominadas
  3. best_balanced() → del frontier, la más cerca de "ideal point" (0/1)

Diseño minimalista — sin scipy / no numerical optimization. Para
algoritmos de Pareto en grandes escalas (>10⁴ configs) se necesita NSGA-II
o similar — fuera del alcance MVP.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


# ── Configuración de objectives ─────────────────────────────────────────

@dataclass(frozen=True)
class Objective:
    """
    Una dimensión del espacio multi-objective.

      name      : key para extraer del metrics dict
      maximize  : True = mayor = mejor (recall); False = menor = mejor (MAE)
      target    : valor "ideal" del objetivo (para best_balanced)
      tolerance : umbral mínimo aceptable (configs peores se filtran)
    """
    name:       str
    maximize:   bool
    target:     float
    tolerance:  float


# Pre-definidos típicos del SSM
STANDARD_OBJECTIVES = [
    Objective("mae_30",          maximize=False, target= 0.0,  tolerance= 25.0),
    Objective("ece",             maximize=False, target= 0.0,  tolerance=  0.15),
    Objective("ic90_gap",        maximize=False, target= 0.0,  tolerance=  0.15),
    Objective("var_z_gap",       maximize=False, target= 0.0,  tolerance=  0.5),
    Objective("hypo_recall_30",  maximize=True,  target= 1.0,  tolerance=  0.50),
    Objective("false_alarms_per_day", maximize=False, target=0.0, tolerance=3.0),
]


def add_derived_metrics(metrics: dict) -> dict:
    """
    Calcula derivadas (gaps respecto a targets) que algunos objectives usan.
    """
    m = dict(metrics)
    if m.get("ic50_coverage") is not None:
        m["ic50_gap"] = abs(m["ic50_coverage"] - 0.50)
    if m.get("ic90_coverage") is not None:
        m["ic90_gap"] = abs(m["ic90_coverage"] - 0.90)
    if m.get("var_z") is not None:
        m["var_z_gap"] = abs(m["var_z"] - 1.0)
    if m.get("mean_z") is not None:
        m["abs_mean_z"] = abs(m["mean_z"])
    return m


# ── Dominance ──────────────────────────────────────────────────────────

def _dominates(a: dict, b: dict, objectives: list[Objective]) -> bool:
    """A domina B si A es ≥ B en TODOS los objectives y > en al menos UNO."""
    at_least_one_strict = False
    for o in objectives:
        va, vb = a.get(o.name), b.get(o.name)
        if va is None or vb is None:
            return False  # data faltante = no comparable
        if o.maximize:
            if va < vb: return False
            if va > vb: at_least_one_strict = True
        else:
            if va > vb: return False
            if va < vb: at_least_one_strict = True
    return at_least_one_strict


def dominance_filter(configs: Iterable[dict],
                     objectives: list[Objective] = None) -> list[dict]:
    """
    Devuelve solo los configs no dominados (el Pareto frontier).

    `configs` es lista de dicts con un campo "metrics" y opcionalmente
    otros campos identificadores (param_hash, name, etc.).
    """
    if objectives is None:
        objectives = STANDARD_OBJECTIVES
    configs = list(configs)
    frontier = []
    for i, c in enumerate(configs):
        ci = add_derived_metrics(c.get("metrics", {}))
        dominated = False
        for j, other in enumerate(configs):
            if i == j: continue
            cj = add_derived_metrics(other.get("metrics", {}))
            if _dominates(cj, ci, objectives):
                dominated = True
                break
        if not dominated:
            frontier.append(c)
    return frontier


# ── Filtro de aceptabilidad ────────────────────────────────────────────

def filter_acceptable(configs: Iterable[dict],
                      objectives: list[Objective] = None) -> list[dict]:
    """
    Descarta configs que violan tolerancias mínimas en cualquier objective.
    """
    if objectives is None:
        objectives = STANDARD_OBJECTIVES
    out = []
    for c in configs:
        m = add_derived_metrics(c.get("metrics", {}))
        accept = True
        for o in objectives:
            v = m.get(o.name)
            if v is None: continue   # missing = neutral
            if o.maximize:
                if v < o.tolerance:  accept = False; break
            else:
                if v > o.tolerance:  accept = False; break
        if accept:
            out.append(c)
    return out


# ── Best balanced (closest to ideal point) ─────────────────────────────

def _normalize(value: float, vmin: float, vmax: float) -> float:
    if vmax == vmin:
        return 0.5
    return (value - vmin) / (vmax - vmin)


def best_balanced(configs: list[dict],
                  objectives: list[Objective] = None) -> dict:
    """
    Del frontier, elige la config más cerca del "ideal point" en el
    espacio multi-objective normalizado.

    Distancia: euclídea sobre objectives normalizados a [0, 1] con la
    dirección "0 = ideal" (invertida si maximize).
    """
    if not configs:
        return {}
    if objectives is None:
        objectives = STANDARD_OBJECTIVES

    # Min/max por objective para normalizar
    enriched = [{"_orig": c, "_metrics": add_derived_metrics(c.get("metrics", {}))}
                for c in configs]
    ranges = {}
    for o in objectives:
        vals = [c["_metrics"].get(o.name) for c in enriched
                if c["_metrics"].get(o.name) is not None]
        if not vals: continue
        ranges[o.name] = (min(vals), max(vals))

    best, best_dist = None, float("inf")
    for c in enriched:
        d2 = 0.0
        valid = True
        for o in objectives:
            v = c["_metrics"].get(o.name)
            if v is None:
                valid = False; break
            if o.name not in ranges: continue
            vmin, vmax = ranges[o.name]
            n = _normalize(v, vmin, vmax)
            # Ideal: 0 si minimize, 1 si maximize
            ideal = 1.0 if o.maximize else 0.0
            d2 += (n - ideal) ** 2
        if not valid: continue
        if d2 < best_dist:
            best_dist = d2
            best = c["_orig"]
    return best or {}


# ── Output for visualization ───────────────────────────────────────────

def pareto_2d_projection(configs: list[dict],
                          x_name: str, y_name: str,
                          x_maximize: bool = False, y_maximize: bool = False) -> dict:
    """
    Proyección a 2D para plottear (calibration vs sharpness, etc.).

    Returns
    -------
    {
        "all":      [{x, y, frontier: bool, label}, ...],
        "frontier": [{x, y, label}, ...] ordenada por x
    }
    """
    obj_x = Objective(x_name, x_maximize, 0.0, float("inf"))
    obj_y = Objective(y_name, y_maximize, 0.0, float("inf"))
    objs = [obj_x, obj_y]

    points = []
    for c in configs:
        m = add_derived_metrics(c.get("metrics", {}))
        x, y = m.get(x_name), m.get(y_name)
        if x is None or y is None: continue
        points.append({"x": x, "y": y, "label": c.get("name") or c.get("param_hash"),
                       "config": c})

    # Marcar frontier
    front_set = set()
    for i, pi in enumerate(points):
        ci_m = {"metrics": {x_name: pi["x"], y_name: pi["y"]}}
        dominated = False
        for j, pj in enumerate(points):
            if i == j: continue
            cj_m = {"metrics": {x_name: pj["x"], y_name: pj["y"]}}
            if _dominates(add_derived_metrics(cj_m["metrics"]),
                          add_derived_metrics(ci_m["metrics"]), objs):
                dominated = True; break
        if not dominated:
            front_set.add(i)

    for i, p in enumerate(points):
        p["frontier"] = (i in front_set)

    frontier = sorted([p for p in points if p["frontier"]], key=lambda p: p["x"])
    return {"all": points, "frontier": frontier}
