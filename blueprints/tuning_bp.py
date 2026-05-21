"""
blueprints/tuning_bp.py
────────────────────────
API y página web del tuning framework.

Endpoints:
    GET  /tuning                           → página de visualización
    GET  /api/tuning/experiments           → lista de experiments + best score
    GET  /api/tuning/experiment/<name>     → detalle + frontier + 2D projection
    GET  /api/tuning/diagnostics?days=14   → deep innovation analysis
"""
from flask import Blueprint, jsonify, request, render_template, session

bp = Blueprint("tuning", __name__)


def _require_login():
    if not session.get("logged_in"):
        return jsonify({"ok": False, "error": "No autorizado"}), 401
    return None


@bp.route("/tuning", endpoint="tuning_page")
def tuning_page():
    if not session.get("logged_in"):
        from flask import redirect, url_for
        return redirect(url_for("login"))
    return render_template("tuning.html")


@bp.route("/api/tuning/experiments", endpoint="api_tuning_list")
def api_tuning_list():
    err = _require_login()
    if err: return err
    try:
        from models import TuningExperiment, db
        from sqlalchemy import func
        # Aggregate por name
        rows = (db.session.query(
            TuningExperiment.name,
            func.count(TuningExperiment.id).label("n_runs"),
            func.max(TuningExperiment.score_composite).label("best_score"),
            func.max(TuningExperiment.created_at).label("last_run"),
        ).group_by(TuningExperiment.name)
         .order_by(func.max(TuningExperiment.created_at).desc()).all())
        return jsonify({"ok": True, "experiments": [
            {"name": r.name, "n_runs": r.n_runs,
             "best_score": round(r.best_score or 0, 4),
             "last_run": r.last_run.isoformat() if r.last_run else None}
            for r in rows
        ]})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.route("/api/tuning/experiment/<name>", endpoint="api_tuning_experiment")
def api_tuning_experiment(name):
    err = _require_login()
    if err: return err
    try:
        from bench.tuning.grid_search import (
            load_experiment_results, experiment_summary,
        )
        from bench.tuning.pareto import (
            dominance_filter, best_balanced, filter_acceptable,
            pareto_2d_projection,
        )
        results = load_experiment_results(name)
        adapted = []
        for r in results:
            flat = (r.get("metrics") or {}).get("flat", {})
            adapted.append({
                "name":       r["param_hash"],
                "param_hash": r["param_hash"],
                "metrics":    flat,
                "scores":     r.get("scores", {}),
                "params":     r.get("params", {}),
                "verdict":    r.get("verdict"),
                "n_records":  r.get("n_records"),
            })
        frontier  = dominance_filter(adapted)
        best      = best_balanced(frontier)

        # 2D projections útiles
        x_param = request.args.get("x")
        y_param = request.args.get("y")
        proj_2d = None
        if x_param and y_param:
            proj_2d = pareto_2d_projection(
                adapted, x_param, y_param,
                x_maximize=(request.args.get("x_max") == "1"),
                y_maximize=(request.args.get("y_max") == "1"),
            )

        return jsonify({
            "ok":           True,
            "summary":      experiment_summary(name),
            "results":      adapted,
            "frontier":     frontier,
            "best_balanced": best,
            "projection_2d": proj_2d,
        })
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.route("/api/tuning/gates", endpoint="api_tuning_gates")
def api_tuning_gates():
    """Promotion gates evaluation + rolling history."""
    err = _require_login()
    if err: return err
    try:
        days    = min(int(request.args.get("days", 7)), 90)
        rolling = min(int(request.args.get("rolling", 14)), 90)
        model   = request.args.get("model", "ssm_v0_ukf6")
        from bench.tuning.promotion_gates import (
            compute_gate_metrics, evaluate_gates,
            gates_rolling_history, stability_summary,
        )
        metrics = compute_gate_metrics(days=days, model_version=model)
        current = evaluate_gates(metrics)
        history = gates_rolling_history(days=rolling, window_days=days,
                                         model_version=model)
        stab    = stability_summary(history)
        return jsonify({
            "ok":          True,
            "current":     current,
            "metrics":     metrics,
            "history":     history,
            "stability":   stab,
            "days":        days,
            "rolling":     rolling,
        })
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.route("/api/tuning/attribute/<name>", endpoint="api_tuning_attribute")
def api_tuning_attribute(name):
    """Failure attribution + suggested sweep para best run de experiment."""
    err = _require_login()
    if err: return err
    try:
        from bench.tuning.grid_search import load_experiment_results
        from bench.tuning.attribution import diagnose, suggested_next_sweep
        rows = load_experiment_results(name)
        if not rows:
            return jsonify({"ok": False, "error": "experiment not found"}), 404
        best = rows[0]
        flat = (best.get("metrics") or {}).get("flat", {})
        diagnoses = diagnose(flat)
        sweep = suggested_next_sweep(diagnoses)
        return jsonify({
            "ok":               True,
            "experiment":       name,
            "best_hash":        best["param_hash"],
            "best_composite":   best["scores"].get("composite"),
            "diagnoses":        diagnoses,
            "suggested_sweep":  sweep,
        })
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.route("/api/tuning/sensitivity/<name>", endpoint="api_tuning_sensitivity")
def api_tuning_sensitivity(name):
    """Sensitivity analysis."""
    err = _require_login()
    if err: return err
    try:
        from bench.tuning.sensitivity import (
            local_sensitivity, parameter_importance,
            suggest_dimensionality_reduction, response_surface,
        )
        local      = local_sensitivity(name)
        importance = parameter_importance(name)
        reduction  = suggest_dimensionality_reduction(name)
        # Response surface si pidió dos params
        rs = None
        x = request.args.get("x"); y = request.args.get("y")
        if x and y:
            rs = response_surface(name, x, y)
        return jsonify({
            "ok":                True,
            "local":             local,
            "importance":        importance,
            "reduction":         reduction,
            "response_surface":  rs,
        })
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.route("/api/tuning/regimes", endpoint="api_tuning_regimes")
def api_tuning_regimes():
    """Regime-specific failure analysis."""
    err = _require_login()
    if err: return err
    try:
        days    = min(int(request.args.get("days", 14)), 90)
        horizon = int(request.args.get("horizon", 30))
        model   = request.args.get("model", "ssm_v0_ukf6")
        from bench.tuning.regimes import evaluate_regimes, regime_specific_diagnoses
        ev = evaluate_regimes(days=days, model_version=model, horizon_min=horizon)
        diags = regime_specific_diagnoses(ev)
        return jsonify({"ok": True, "regimes": ev, "diagnoses": diags})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.route("/api/tuning/lineage", endpoint="api_tuning_lineage")
def api_tuning_lineage():
    """Lineage graph + recent experiments."""
    err = _require_login()
    if err: return err
    try:
        from bench.tuning.lineage import (
            build_lineage, lineage_path, impact_analysis, latest_branch_summary,
        )
        root = request.args.get("root")
        compare = request.args.get("compare")
        if compare and ":" in compare:
            parent, child = compare.split(":", 1)
            return jsonify({"ok": True, "compare": impact_analysis(parent, child)})
        if root:
            return jsonify({"ok": True, "tree": build_lineage(root)})
        return jsonify({"ok": True, "latest": latest_branch_summary()})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.route("/api/tuning/protocols", endpoint="api_tuning_protocols")
def api_tuning_protocols():
    """Lista los baseline protocols disponibles."""
    err = _require_login()
    if err: return err
    try:
        from bench.tuning.protocol import list_protocols
        return jsonify({"ok": True, "protocols": list_protocols()})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.route("/api/tuning/diagnostics", endpoint="api_tuning_diagnostics")
def api_tuning_diagnostics():
    err = _require_login()
    if err: return err
    try:
        days  = min(int(request.args.get("days", 14)), 90)
        model = request.args.get("model", "ssm_v0_ukf6")
        from bench.metrics.innovations    import load_innovations
        from bench.tuning.deep_diagnostics import deep_innovation_analysis
        inns = load_innovations(days=days, model_version=model)
        analysis = deep_innovation_analysis(inns)
        return jsonify({"ok": True, "analysis": analysis,
                        "n_innovations_loaded": len(inns),
                        "days": days, "model_version": model})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
