"""
bench/tuning/lineage.py
────────────────────────
Lineage graph entre experiments + comparative impact analysis.

Cada ExperimentSpec puede declarar parent (otra ExperimentSpec previa).
Esto construye un árbol que captura LA EVOLUCIÓN del tuning:

    baseline_v1
     ├── q_refine_v1    (-15% MAE, -0.04 ECE → ✓ adoptar)
     │    └── q_refine_v2 (-3% MAE, +0.02 ECE → trade-off)
     ├── inflation_test (+0.03 IC90 coverage → ✓)
     └── r_noise_test   (no significant change)

Funcionalidades
---------------
  - build_lineage(root_name) → árbol con métricas comparativas
  - lineage_path(name) → ancestros + self
  - impact_analysis(parent_name, child_name) → delta de scores y verdict
  - latest_branch_summary() → último frente de mejora
"""
from __future__ import annotations

import json
from typing import Optional


def _load_best_run(experiment_name: str) -> Optional[dict]:
    """Carga el mejor run (max composite) de un experiment."""
    from models import TuningExperiment
    row = (TuningExperiment.query
           .filter_by(name=experiment_name)
           .order_by(TuningExperiment.score_composite.desc())
           .first())
    if not row:
        return None
    return {
        "name":              row.name,
        "param_hash":        row.param_hash,
        "params":            json.loads(row.params_json) if row.params_json else {},
        "score_composite":   row.score_composite,
        "score_calibration": row.score_calibration,
        "score_innovation":  row.score_innovation,
        "score_clinical":    row.score_clinical,
        "score_stability":   row.score_stability,
        "score_accuracy":    row.score_accuracy,
        "parent_name":       row.parent_name,
        "gates_passed":      row.gates_passed,
        "created_at":        row.created_at.isoformat() if row.created_at else None,
    }


def _children_of(parent_name: str) -> list[str]:
    """Devuelve nombres de experimentos que declaran `parent_name`."""
    from models import db, TuningExperiment
    rows = (db.session.query(TuningExperiment.name)
            .filter_by(parent_name=parent_name)
            .distinct().all())
    return [r[0] for r in rows]


def build_lineage(root_name: str, max_depth: int = 5) -> dict:
    """
    Construye recursivamente el árbol descendente desde un root.

    Returns
    -------
    {
        "name":             str,
        "best":             {scores...},
        "delta_vs_parent":  {scores diffs} | None,
        "children":         [recursive tree...]
    }
    """
    def _build(name: str, parent_run: Optional[dict], depth: int) -> dict:
        node_run = _load_best_run(name)
        if node_run is None:
            return {"name": name, "missing": True}
        delta = None
        if parent_run:
            delta = {
                k.replace("score_", "Δ"): round((node_run.get(k) or 0) - (parent_run.get(k) or 0), 4)
                for k in ["score_composite","score_calibration","score_innovation",
                          "score_clinical","score_stability","score_accuracy"]
            }
        node = {
            "name":            name,
            "param_hash":      node_run["param_hash"],
            "best":            {k: node_run.get(k) for k in
                                ["score_composite","score_calibration","score_innovation",
                                 "score_clinical","score_stability","score_accuracy",
                                 "gates_passed"]},
            "delta_vs_parent": delta,
            "verdict":         _impact_verdict(delta),
            "children":        [],
        }
        if depth < max_depth:
            for child_name in _children_of(name):
                node["children"].append(_build(child_name, node_run, depth + 1))
        return node

    return _build(root_name, None, 0)


def _impact_verdict(delta: Optional[dict]) -> str:
    if not delta:
        return "root"
    # Acepta ambos formatos: "Δcomposite" (tree build) o "score_composite"
    # (impact_analysis usa key crudo del DB). El bug histórico era que
    # impact_analysis pasaba "score_composite" pero esta función solo
    # buscaba "Δcomposite" → siempre devolvía "no_change" aunque Δ>0.05.
    dc = (delta.get("Δcomposite")
          or delta.get("score_composite")
          or 0)
    if dc > 0.05:    return "improvement"
    if dc > 0.01:    return "marginal_gain"
    if dc < -0.05:   return "regression"
    if dc < -0.01:   return "minor_regression"
    return "no_change"


def lineage_path(experiment_name: str) -> list[dict]:
    """Camino ascendente: root → ... → experiment_name."""
    path = []
    current = _load_best_run(experiment_name)
    while current:
        path.append({
            "name":             current["name"],
            "param_hash":       current["param_hash"],
            "score_composite":  current["score_composite"],
            "gates_passed":     current.get("gates_passed"),
        })
        parent_name = current.get("parent_name")
        if not parent_name:
            break
        current = _load_best_run(parent_name)
    return list(reversed(path))


def impact_analysis(parent_name: str, child_name: str) -> dict:
    """Delta detallado entre dos experiments adjacentes."""
    p = _load_best_run(parent_name)
    c = _load_best_run(child_name)
    if not p or not c:
        return {"ok": False, "error": "experiment not found"}

    keys = ["score_composite","score_calibration","score_innovation",
            "score_clinical","score_stability","score_accuracy"]
    deltas = {k: round((c.get(k) or 0) - (p.get(k) or 0), 4) for k in keys}

    # Param overrides que cambiaron
    p_params = p.get("params", {})
    c_params = c.get("params", {})
    changes = {}
    for k in set(p_params) | set(c_params):
        if p_params.get(k) != c_params.get(k):
            changes[k] = {"from": p_params.get(k), "to": c_params.get(k)}

    return {
        "ok":             True,
        "parent":         {"name": parent_name, "scores": {k: p.get(k) for k in keys}},
        "child":          {"name": child_name, "scores": {k: c.get(k) for k in keys}},
        "deltas":         deltas,
        "param_changes":  changes,
        "verdict":        _impact_verdict(deltas),
        "summary":        _summarize_impact(deltas, changes),
    }


def _summarize_impact(deltas: dict, changes: dict) -> str:
    parts = []
    _dc = deltas.get("Δcomposite") or deltas.get("score_composite") or 0
    if _dc > 0.02:
        parts.append(f"composite +{_dc:.3f}")
    elif _dc < -0.02:
        parts.append(f"composite {_dc:.3f}")
    for k in ["Δcalibration", "Δinnovation", "Δclinical", "Δstability", "Δaccuracy"]:
        v = deltas.get(k, 0) or 0
        if abs(v) > 0.05:
            parts.append(f"{k} {v:+.2f}")
    if not parts:
        return "sin cambio significativo"
    return " · ".join(parts) + (f"  ({len(changes)} param overrides)" if changes else "")


def latest_branch_summary() -> list[dict]:
    """Lista los N experiments más recientes con su impacto."""
    from models import db, TuningExperiment
    from sqlalchemy import func
    rows = (db.session.query(
        TuningExperiment.name,
        func.max(TuningExperiment.score_composite).label("best"),
        func.max(TuningExperiment.created_at).label("when"),
        func.max(TuningExperiment.parent_name).label("parent"),
    ).group_by(TuningExperiment.name)
     .order_by(func.max(TuningExperiment.created_at).desc()).limit(20).all())

    out = []
    for r in rows:
        delta = None
        if r.parent:
            impact = impact_analysis(r.parent, r.name)
            if impact.get("ok"):
                _deltas = impact.get("deltas") or {}
                delta = _deltas.get("Δcomposite") or _deltas.get("score_composite")
        out.append({
            "name":         r.name,
            "best_score":   round(r.best or 0, 4),
            "parent":       r.parent,
            "Δcomposite":   round(delta, 4) if delta is not None else None,
            "when":         r.when.isoformat() if r.when else None,
        })
    return out
