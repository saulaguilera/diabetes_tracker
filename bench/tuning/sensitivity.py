"""
bench/tuning/sensitivity.py
────────────────────────────
Sensitivity analysis sobre experiments persistidos.

Tres niveles:

  1. **Local sensitivity**: ∂score/∂param via finite differences sobre
     pares de runs que difieren en UN solo parámetro.
  2. **Sobol-light (variance-based)**: cuánto de la varianza del score se
     explica al variar cada parámetro, marginalizando sobre los otros.
  3. **Response surfaces**: heatmap data para pares (param_x, param_y)
     con metric_z.

Importante
----------
Estos métodos NO requieren correr nuevos replays. Trabajan SOBRE los
experiments YA persistidos en `tuning_experiments`. Esto significa que
la calidad depende del muestreo previo:
  - Con 4 combos solo se pueden estimar trends, no significancia
  - Con 30+ combos los rankings empiezan a ser fiables
  - Sobol completo necesita ~100+ combos en diseño estructurado (Saltelli)
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from typing import Optional


# ── Loader ─────────────────────────────────────────────────────────────

def _load_runs(experiment_name: str) -> list[dict]:
    """Carga rows persistidos de TuningExperiment como dicts."""
    from models import TuningExperiment
    rows = (TuningExperiment.query
            .filter_by(name=experiment_name)
            .filter(TuningExperiment.score_composite.isnot(None))
            .all())
    out = []
    for r in rows:
        try:
            params = json.loads(r.params_json) if r.params_json else {}
        except Exception:
            params = {}
        out.append({
            "param_hash":      r.param_hash,
            "params":          params,
            "score_composite": r.score_composite,
            "score_calibration": r.score_calibration,
            "score_innovation":  r.score_innovation,
            "score_stability":   r.score_stability,
            "score_accuracy":    r.score_accuracy,
            "score_clinical":    r.score_clinical,
            "metrics":         (json.loads(r.metrics_json) if r.metrics_json else {}),
        })
    return out


# ── Identifica qué params se barrieron ─────────────────────────────────

def _swept_params(runs: list[dict]) -> dict[str, list]:
    """Detecta params con más de un valor único — son los que se barrieron."""
    if not runs:
        return {}
    values_per_param: dict[str, set] = defaultdict(set)
    for r in runs:
        for k, v in (r.get("params") or {}).items():
            try:
                values_per_param[k].add(round(float(v), 12))
            except (TypeError, ValueError):
                continue
    return {k: sorted(v) for k, v in values_per_param.items() if len(v) > 1}


# ── Local sensitivity (finite differences) ─────────────────────────────

def local_sensitivity(experiment_name: str,
                       score_key: str = "score_composite") -> dict:
    """
    Para cada parámetro barrido, calcula:
      Δscore / Δparam (normalizada) sobre pares de runs adyacentes en el grid.

    Returns
    -------
    {
        "n_runs":  int,
        "score":   str (qué se midió),
        "params":  {
            param_name: {
                "mean_abs_d_score":   float,
                "max_abs_d_score":    float,
                "n_pairs":            int,
                "direction":          "positive" | "negative" | "mixed",
            },
            ...
        }
    }
    """
    runs = _load_runs(experiment_name)
    if not runs:
        return {"n_runs": 0}
    swept = _swept_params(runs)
    if not swept:
        return {"n_runs": len(runs), "note": "ningún parámetro se barrió"}

    out: dict[str, dict] = {}
    for param, values in swept.items():
        # Find pairs that differ ONLY in this param
        pairs = []
        for i, a in enumerate(runs):
            for b in runs[i+1:]:
                p_a = a.get("params", {})
                p_b = b.get("params", {})
                if p_a.get(param) == p_b.get(param):
                    continue
                # Verificar que todo lo demás sea idéntico
                other_keys = set(swept.keys()) - {param}
                if all(p_a.get(k) == p_b.get(k) for k in other_keys):
                    s_a, s_b = a.get(score_key), b.get(score_key)
                    if s_a is None or s_b is None:
                        continue
                    try:
                        d_p = float(p_b[param]) - float(p_a[param])
                    except (TypeError, ValueError):
                        continue
                    if d_p == 0: continue
                    d_s = s_b - s_a
                    pairs.append((d_p, d_s))

        if not pairs:
            out[param] = {"n_pairs": 0, "note": "no pairs found"}
            continue

        # Normalize by range del parámetro para comparabilidad
        rng = max(values) - min(values)
        scaled = [abs(d_s) for _, d_s in pairs]
        d_signs = [d_s for _, d_s in pairs]
        n_pos = sum(1 for d in d_signs if d > 0)
        n_neg = sum(1 for d in d_signs if d < 0)
        direction = ("positive" if n_pos > 0.7 * len(pairs) else
                     "negative" if n_neg > 0.7 * len(pairs) else "mixed")

        out[param] = {
            "n_pairs":          len(pairs),
            "mean_abs_d_score": round(sum(scaled) / len(scaled), 5),
            "max_abs_d_score":  round(max(scaled), 5),
            "param_range":      round(rng, 6),
            "direction":        direction,
        }
    return {
        "n_runs": len(runs),
        "score":  score_key,
        "params": out,
    }


# ── Sobol-light (variance contribution) ────────────────────────────────

def parameter_importance(experiment_name: str,
                          score_key: str = "score_composite") -> dict:
    """
    Estimación variance-based de la importancia de cada parámetro:
        S_i ≈ Var_p[E[score | p_i = v]] / Var[score]

    Implementación simplificada: para cada parámetro barrido, agrupa runs
    por valor del param, calcula la media del score por grupo, y mide
    la varianza entre esas medias normalizada por la varianza total.

    Esto es similar al "first-order Sobol index" sin diseño Saltelli estricto.
    No es métricamente exacto pero es robusto y útil para ranking.
    """
    runs = _load_runs(experiment_name)
    if not runs:
        return {"n_runs": 0}
    swept = _swept_params(runs)
    scores = [r.get(score_key) for r in runs if r.get(score_key) is not None]
    if len(scores) < 2:
        return {"n_runs": len(runs), "note": "muestra insuficiente"}

    total_mean = sum(scores) / len(scores)
    total_var  = sum((s - total_mean) ** 2 for s in scores) / max(1, len(scores) - 1)

    importance: dict[str, dict] = {}
    for param in swept:
        groups: dict[float, list] = defaultdict(list)
        for r in runs:
            v = r.get("params", {}).get(param)
            s = r.get(score_key)
            if v is None or s is None: continue
            try:
                groups[round(float(v), 12)].append(s)
            except (TypeError, ValueError):
                continue
        if len(groups) < 2:
            importance[param] = {"S_i": None, "n_groups": len(groups)}
            continue

        group_means = [sum(g) / len(g) for g in groups.values()]
        gm_mean = sum(group_means) / len(group_means)
        between_var = sum((gm - gm_mean) ** 2 for gm in group_means) / max(1, len(group_means) - 1)
        S_i = between_var / max(1e-12, total_var)
        importance[param] = {
            "S_i":          round(min(1.0, S_i), 4),
            "n_groups":     len(groups),
            "best_value":   max(groups.items(), key=lambda kv: sum(kv[1]) / len(kv[1]))[0],
            "worst_value":  min(groups.items(), key=lambda kv: sum(kv[1]) / len(kv[1]))[0],
            "best_mean":    round(max(sum(g) / len(g) for g in groups.values()), 4),
            "worst_mean":   round(min(sum(g) / len(g) for g in groups.values()), 4),
        }

    # Ranking
    ranked = sorted(importance.items(),
                    key=lambda kv: -(kv[1].get("S_i") or 0))
    return {
        "n_runs":   len(runs),
        "score":    score_key,
        "total_var": round(total_var, 6),
        "importance": dict(ranked),
        "ranking":  [k for k, _ in ranked],
    }


# ── Response surface 2D ────────────────────────────────────────────────

def response_surface(experiment_name: str,
                      param_x: str, param_y: str,
                      metric_key: str = "score_composite") -> dict:
    """
    Genera datos heatmap para (param_x, param_y) → metric.
    Si hay múltiples runs en el mismo (x, y), promedia.
    """
    runs = _load_runs(experiment_name)
    cells: dict[tuple, list] = defaultdict(list)
    for r in runs:
        x = r.get("params", {}).get(param_x)
        y = r.get("params", {}).get(param_y)
        v = r.get(metric_key)
        if x is None or y is None or v is None: continue
        try:
            cells[(round(float(x), 12), round(float(y), 12))].append(v)
        except (TypeError, ValueError):
            continue
    grid = [
        {"x": x, "y": y, "value": round(sum(vs) / len(vs), 4), "n": len(vs)}
        for (x, y), vs in sorted(cells.items())
    ]
    if grid:
        vmin = min(c["value"] for c in grid)
        vmax = max(c["value"] for c in grid)
    else:
        vmin = vmax = None
    return {
        "param_x":  param_x,
        "param_y":  param_y,
        "metric":   metric_key,
        "cells":    grid,
        "vmin":     vmin,
        "vmax":     vmax,
        "n_cells":  len(grid),
    }


# ── Suggested dimensionality reduction ─────────────────────────────────

def suggest_dimensionality_reduction(experiment_name: str,
                                      threshold: float = 0.05) -> dict:
    """
    Identifica parámetros "inútiles" — los que tienen S_i < threshold.
    Útil para diseñar el siguiente grid con menos dimensiones.
    """
    imp = parameter_importance(experiment_name)
    if "importance" not in imp:
        return imp
    dominant = []
    irrelevant = []
    for p, info in imp["importance"].items():
        S = info.get("S_i") or 0
        if S >= threshold:
            dominant.append((p, S))
        else:
            irrelevant.append((p, S))
    return {
        "threshold":  threshold,
        "n_runs":     imp.get("n_runs"),
        "dominant":   sorted(dominant, key=lambda x: -x[1]),
        "irrelevant": sorted(irrelevant, key=lambda x: -x[1]),
        "next_sweep_should_fix": [p for p, _ in irrelevant],
        "next_sweep_should_explore": [p for p, _ in dominant],
    }
