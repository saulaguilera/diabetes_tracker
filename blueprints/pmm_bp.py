"""
blueprints/pmm_bp.py
─────────────────────
API endpoints del Personal Metabolic Model.

GET  /api/pmm/state            → estado completo: ISF, ICR, bloques, curva
GET  /api/pmm/isf/now          → ISF dinámico para ahora con IC95
GET  /api/pmm/icr/now          → ICR dinámico para ahora con IC95
GET  /api/pmm/observations     → episodios identificados (últimos 50)
POST /api/pmm/recalibrate      → trigger manual de recalibración
GET  /api/pmm/learning-curve   → evolución histórica de parámetros
"""
from flask import Blueprint, jsonify, request, session

bp = Blueprint("pmm", __name__)


def _require_login():
    if not session.get("logged_in"):
        return jsonify({"ok": False, "error": "No autorizado"}), 401
    return None


@bp.route("/api/pmm/state", endpoint="api_pmm_state")
def api_pmm_state():
    """Estado completo del PMM: parámetros aprendidos + confianza."""
    err = _require_login()
    if err:
        return err
    try:
        from pmm.engines.calibration import get_calibration_summary
        data = get_calibration_summary()
        return jsonify({"ok": True, **data})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.route("/api/pmm/isf/now", endpoint="api_pmm_isf_now")
def api_pmm_isf_now():
    """
    ISF dinámico para la hora actual (o la hora especificada via ?hora=N).

    Retorna:
        mu          : estimación puntual
        sigma       : incertidumbre (std)
        ci_95_lo/hi : intervalo de confianza 95%
        confidence  : 0-1
        source      : 'circadiano' | 'global' | 'prior'
        block_label : ej. '08–12h'
        n_obs       : observaciones que lo construyeron
    """
    err = _require_login()
    if err:
        return err
    try:
        hora = request.args.get("hora", None, type=int)
        from pmm.core.parameter_store import get_isf_now
        data = get_isf_now(hora=hora)
        return jsonify({"ok": True, "isf": data})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.route("/api/pmm/icr/now", endpoint="api_pmm_icr_now")
def api_pmm_icr_now():
    """ICR dinámico para la hora actual."""
    err = _require_login()
    if err:
        return err
    try:
        hora = request.args.get("hora", None, type=int)
        from pmm.core.parameter_store import get_icr_now
        data = get_icr_now(hora=hora)
        return jsonify({"ok": True, "icr": data})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.route("/api/pmm/observations", endpoint="api_pmm_observations")
def api_pmm_observations():
    """
    Episodios de aprendizaje identificados (últimos 100).
    Incluye tanto los usados como los descartados (con skip_reason).
    """
    err = _require_login()
    if err:
        return err
    try:
        from models import PMMObservation

        param = request.args.get("param", None)  # ISF | ICR | None (todos)
        q = PMMObservation.query.order_by(PMMObservation.observed_at.desc())
        if param:
            q = q.filter_by(param_name=param.upper())
        obs = q.limit(100).all()

        rows = [
            {
                "id":            o.id,
                "param":         o.param_name,
                "source_type":   o.source_type,
                "observed_at":   o.observed_at.isoformat() if o.observed_at else None,
                "time_block":    o.time_block,
                "quality":       o.quality_score,
                "value":         o.observed_value,
                "obs_sigma":     o.obs_sigma,
                "used":          o.used_in_update,
                "skip_reason":   o.skip_reason,
                "mu_before":     o.mu_before,
                "sigma_before":  o.sigma_before,
                "mu_after":      o.mu_after,
                "sigma_after":   o.sigma_after,
            }
            for o in obs
        ]
        return jsonify({"ok": True, "observations": rows, "total": len(rows)})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.route("/api/pmm/recalibrate", methods=["POST"], endpoint="api_pmm_recalibrate")
def api_pmm_recalibrate():
    """
    Trigger manual de recalibración.
    force=true reprocesa todo el historial (bootstrap).
    """
    err = _require_login()
    if err:
        return err
    try:
        force = request.json.get("force", False) if request.is_json else False
        from pmm.engines.calibration import run_calibration
        stats = run_calibration(force_bootstrap=bool(force))
        return jsonify({"ok": True, "stats": stats})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.route("/api/pmm/learning-curve", endpoint="api_pmm_learning_curve")
def api_pmm_learning_curve():
    """
    Evolución temporal de los parámetros aprendidos.
    Muestra cómo μ y σ cambiaron con cada nueva observación.
    """
    err = _require_login()
    if err:
        return err
    try:
        from models import PMMObservation

        param = request.args.get("param", "ISF").upper()
        obs = (
            PMMObservation.query
            .filter_by(param_name=param, used_in_update=True)
            .order_by(PMMObservation.observed_at)
            .all()
        )

        curve = [
            {
                "t":       o.observed_at.isoformat() if o.observed_at else None,
                "mu":      o.mu_after,
                "sigma":   o.sigma_after,
                "ci_lo":   round(o.mu_after - 1.96 * o.sigma_after, 1) if o.mu_after and o.sigma_after else None,
                "ci_hi":   round(o.mu_after + 1.96 * o.sigma_after, 1) if o.mu_after and o.sigma_after else None,
                "obs_val": o.observed_value,
                "quality": o.quality_score,
                "block":   o.time_block,
            }
            for o in obs
        ]
        return jsonify({"ok": True, "param": param, "curve": curve, "n": len(curve)})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
