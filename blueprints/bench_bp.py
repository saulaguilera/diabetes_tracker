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

# Orden de preferencia para elegir qué modelo mostrar en el verdict
# cuando el usuario no pidió uno específico.
_MODEL_PREFERENCE = [
    "ssm_v0_ukf6_basal",
    "ssm_v0_ukf6",
]


def _preferred_model(report: dict) -> str | None:
    """
    Devuelve el modelo preferido para el verdict.
    Prioriza el SSM activo sobre modelos anteriores.
    Si no hay ninguno de los preferidos, devuelve el primero disponible.
    """
    available = list(report.get("by_model", {}).keys())
    for pref in _MODEL_PREFERENCE:
        if pref in available:
            return pref
    return available[0] if available else None


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
        # Default: modelo SSM activo. Pasar "all" para ver todos los modelos.
        model_param = request.args.get("model") or None
        model = model_param if model_param != "all" else None

        from bench.runner import run_backtest, verdict
        report          = run_backtest(days=days, model_version=model)
        # Para el verdict usar el SSM activo si no se pidió un modelo específico
        verdict_model = model or _preferred_model(report)
        report["verdict"] = verdict(report, model=verdict_model)
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
        model_param = request.args.get("model") or None
        model = model_param if model_param != "all" else None
        from bench.runner import run_backtest, verdict
        report = run_backtest(days=days, model_version=model)
        verdict_model = model or _preferred_model(report)
        return jsonify({"ok": True, "verdict": verdict(report, model=verdict_model)})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
