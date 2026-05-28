"""
blueprints/health_bp.py
────────────────────────
Endpoints de Model Health & Validation Readiness.

    GET  /api/model-health           → snapshot completo (status + cobertura + audits)
    GET  /api/model-health/ready     → ¿podemos correr el bench con confianza?
    GET  /api/model-health/coverage  → sólo cobertura (rápido)

NO devuelve dashboard. Sólo JSON limpio para inspección técnica.
"""
from flask import Blueprint, jsonify, request, session

bp = Blueprint("health", __name__)


def _require_login():
    if not session.get("logged_in"):
        return jsonify({"ok": False, "error": "No autorizado"}), 401
    return None


def _json_safe(obj):
    """Reemplaza NaN/Infinity por None — mismo helper que bench_bp."""
    import math
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    return obj


@bp.route("/api/model-health", endpoint="api_model_health")
def api_model_health():
    """Snapshot completo. `days` (default 7, max 30)."""
    err = _require_login()
    if err: return err
    try:
        from services.model_health import get_model_health
        days = min(int(request.args.get("days", 7)), 30)
        return jsonify(_json_safe({"ok": True, "health": get_model_health(days=days)}))
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.route("/api/model-health/ready", endpoint="api_model_health_ready")
def api_model_health_ready():
    """Readiness gate. Devuelve `ready: true/false` + razones."""
    err = _require_login()
    if err: return err
    try:
        from services.model_health import is_ready_for_bench
        days = min(int(request.args.get("days", 7)), 30)
        return jsonify(_json_safe({"ok": True, "readiness": is_ready_for_bench(days=days)}))
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.route("/api/model-health/coverage", endpoint="api_model_health_coverage")
def api_model_health_coverage():
    """Solo el bloque de cobertura — más liviano para polling."""
    err = _require_login()
    if err: return err
    try:
        from services.model_health import _coverage_check
        days = min(int(request.args.get("days", 7)), 30)
        return jsonify(_json_safe({"ok": True, "coverage": _coverage_check(days=days)}))
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
