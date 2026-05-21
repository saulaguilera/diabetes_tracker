"""
blueprints/bench_bp.py
───────────────────────
Endpoints para el backtest framework:

    GET  /bench                       → página de visualización
    GET  /api/bench/run?days=30       → JSON completo del reporte
    GET  /api/bench/verdict?days=30   → solo pass/warn/fail
"""
from flask import Blueprint, jsonify, request, render_template, session

bp = Blueprint("bench", __name__)


def _require_login():
    if not session.get("logged_in"):
        return jsonify({"ok": False, "error": "No autorizado"}), 401
    return None


@bp.route("/bench", endpoint="bench_page")
def bench_page():
    """Página de visualización del backtest."""
    if not session.get("logged_in"):
        from flask import redirect, url_for
        return redirect(url_for("login"))
    return render_template("bench.html")


@bp.route("/api/bench/run", endpoint="api_bench_run")
def api_bench_run():
    """
    Corre el backtest contra los datos en DB y devuelve el reporte JSON.

    Query params:
        days  : ventana en días (default 30, max 365)
        model : filtrar por model_version (opcional)
    """
    err = _require_login()
    if err:
        return err
    try:
        days  = min(int(request.args.get("days", 30)), 365)
        model = request.args.get("model") or None

        from bench.runner import run_backtest, verdict
        report          = run_backtest(days=days, model_version=model)
        report["verdict"] = verdict(report, model=model)
        return jsonify({"ok": True, "report": report})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.route("/api/bench/verdict", endpoint="api_bench_verdict")
def api_bench_verdict():
    """Solo veredicto pass/warn/fail (rápido)."""
    err = _require_login()
    if err:
        return err
    try:
        days  = min(int(request.args.get("days", 30)), 365)
        model = request.args.get("model") or None
        from bench.runner import run_backtest, verdict
        report = run_backtest(days=days, model_version=model)
        return jsonify({"ok": True, "verdict": verdict(report, model=model)})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
