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
from flask import Blueprint, jsonify, request, session, render_template

bp = Blueprint("pmm", __name__)


def _require_login():
    if not session.get("logged_in"):
        return jsonify({"ok": False, "error": "No autorizado"}), 401
    return None


@bp.route("/pmm", endpoint="pmm_page")
def pmm_page():
    """Página del Personal Metabolic Model — visualización del aprendizaje."""
    if not session.get("logged_in"):
        from flask import redirect, url_for
        return redirect(url_for("login"))
    return render_template("pmm.html")


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


@bp.route("/api/pmm/absorption", endpoint="api_pmm_absorption")
def api_pmm_absorption():
    """
    Estado del aprendizaje de velocidad de absorción por categoría de comida.

    Retorna para cada bucket (FAST / MED / SLOW):
        mu            : speed_factor estimado (1.0 = igual al modelo poblacional)
        sigma         : incertidumbre
        ci_95_lo/hi   : intervalo de confianza
        n_obs         : observaciones usadas
        confidence    : 0-1
        source        : 'learned' | 'prior'
        k_a_default   : constante de vaciado gástrico poblacional (min⁻¹)
        k_a_personal  : k_a_default × speed_factor (el que usa el modelo)
        interpretation: texto explicativo
    """
    err = _require_login()
    if err:
        return err
    try:
        from pmm.engines.absorption import get_all_speed_factors
        data = get_all_speed_factors()
        return jsonify({"ok": True, "absorption": data})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.route("/api/pmm/anomaly", endpoint="api_pmm_anomaly")
def api_pmm_anomaly():
    """
    Score compuesto de anomalía metabólica (0-100).

    Combina tres señales con horizontes temporales distintos:
      - drift_cusum  : shift sostenido (días/semanas) — peso 40%
      - residual     : error de predicción reciente (horas) — peso 35%
      - mahalanobis  : estado actual vs historial (puntual) — peso 25%

    Retorna:
        score         : 0-100
        level         : 'normal' | 'watch' | 'alert' | 'critical'
        components    : desglose de cada señal
        mahal_detail  : z-scores e info de la distancia de Mahalanobis
        reasons       : causas detectadas
        suggestions   : acciones recomendadas
        narrativa     : descripción en español
    """
    err = _require_login()
    if err:
        return err
    try:
        from pmm.engines.anomaly import compute_anomaly_score
        data = compute_anomaly_score()
        return jsonify(data)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.route("/api/pmm/drift", endpoint="api_pmm_drift")
def api_pmm_drift():
    """
    Estado actual del detector CUSUM de drift metabólico.

    Retorna:
        drift_active  : bool — hay drift detectado
        drift_dir     : 'resistance' | 'sensitivity' | null
        drift_factor  : float — factor corrector para ISF (1.0 = sin drift)
        drift_since   : ISO timestamp del inicio del drift actual
        drift_hours   : horas de duración del drift
        intensity     : 0-1 — qué tan lejos está el CUSUM del umbral
        cusum_pos     : valor acumulador positivo
        cusum_neg     : valor acumulador negativo
        sigma_ref     : σ adaptivo del residual (mg/dL)
        threshold_h   : umbral de alarma (= 5 × σ_ref)
        narrativa     : explicación legible en español
    """
    err = _require_login()
    if err:
        return err
    try:
        from pmm.engines.drift import get_drift_status
        data = get_drift_status()
        return jsonify(data)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.route("/api/pmm/drift/reset", methods=["POST"], endpoint="api_pmm_drift_reset")
def api_pmm_drift_reset():
    """
    Reset manual del CUSUM de drift.
    Usar después de un cambio de pauta de insulina, medicación nueva, etc.
    """
    err = _require_login()
    if err:
        return err
    try:
        from pmm.engines.drift import reset_cusum
        reset_cusum()
        return jsonify({"ok": True, "message": "CUSUM reseteado correctamente"})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.route("/api/pmm/explain", endpoint="api_pmm_explain")
def api_pmm_explain():
    """
    Descomposición causal del cambio de glucosa en las últimas N horas.

    Query params:
        hours : float — ventana de análisis (default 3.0, max 12)
        t_start : ISO timestamp — inicio de ventana (alternativa a hours)
        t_end   : ISO timestamp — fin de ventana (alternativa a hours)

    Retorna:
        delta_total    : ΔG observado (mg/dL)
        g_start / g_end: glucosas al inicio y al final
        attributions   : { iob, cob, fpe, ejercicio, basal, residual } en mg/dL
        attribution_pct: mismos valores como % de |delta_total|
        dominant_factor: factor con mayor impacto absoluto
        anomaly_flag   : True si |residual| > 2σ histórico
        residual_sigma : σ histórico del residual
        narrativa      : descripción en español
    """
    err = _require_login()
    if err:
        return err
    try:
        from datetime import datetime as dt
        from pmm.engines.explainability import decompose_glucose_delta, explain_last_hours

        t_start_str = request.args.get("t_start")
        t_end_str   = request.args.get("t_end")

        if t_start_str and t_end_str:
            t_start = dt.fromisoformat(t_start_str)
            t_end   = dt.fromisoformat(t_end_str)
            data    = decompose_glucose_delta(t_start, t_end)
        else:
            hours = min(float(request.args.get("hours", 3.0)), 12.0)
            data  = explain_last_hours(hours=hours)

        return jsonify(data)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.route("/api/pmm/hypo-risk", endpoint="api_pmm_hypo_risk")
def api_pmm_hypo_risk():
    """
    Predicción de riesgo de hipoglucemia en horizonte corto (15–30 min).

    Evalúa P(G < 70) en horizontes [15, 20, 30] min con Monte Carlo y
    determina:
      level       : 'normal' | 'watch' | 'alert' | 'critical'
      horizon_min : horizonte del peor caso
      g_pred      : predicción central
      sigma       : incertidumbre
      p_hipo      : probabilidad de hipoglucemia
      action      : recomendación de acción
      narrativa   : descripción legible

    Query params:
        force=1 : invalida el cache de 60s
    """
    err = _require_login()
    if err:
        return err
    try:
        from utils.hypo_predictor import compute_hypo_risk
        force = request.args.get("force", "0") in ("1", "true", "yes")
        return jsonify(compute_hypo_risk(force=force))
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc), "active": False}), 500


@bp.route("/api/pmm/gp-status", endpoint="api_pmm_gp_status")
def api_pmm_gp_status():
    """
    Estado del Gaussian Process corrector de predicción.

    Retorna:
        active          : bool — GP activo con suficientes datos
        n_train         : int  — puntos de entrenamiento
        hyperparams     : { length_scale_min, signal_std_mgdl, noise_std_mgdl }
        corrections     : { plus_30: { correction, std, reliable }, plus_60: ... }
        interpretation  : descripción en español
        trained_ago_s   : segundos desde el último entrenamiento
    """
    err = _require_login()
    if err:
        return err
    try:
        from utils.gp_corrector import get_gp_status
        return jsonify(get_gp_status())
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
