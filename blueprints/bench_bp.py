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
    "ssm_v0_ukf6_basal_ex_r1",
    "ssm_v0_ukf6_basal_ex",
    "ssm_v0_ukf6_basal",
    "ssm_v0_ukf6",
]


def _json_safe(obj):
    """
    Reemplaza NaN/Infinity/-Infinity por None recursivamente.
    JSON estricto (browser) rechaza esos valores; Python los serializa
    como texto literal y rompe el parser del cliente.
    """
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
        return jsonify(_json_safe({"ok": True, "report": report}))
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.route("/api/bench/hypo_post_mortem", endpoint="api_bench_hypo_post_mortem")
def api_bench_hypo_post_mortem():
    """
    Para cada hipoglucemia real (CGM < 70) en la ventana de días, busca
    qué predijo el modelo ~30 min antes y reporta:
        - si había predicción activa
        - qué valor predijo (g_pred)
        - cuánto era la incertidumbre (sigma)
        - si la regla `p_hypo >= 0.30` habría disparado alerta
        - error en mg/dL

    Sirve para diagnosticar por qué HYPO_RECALL = 0 sin tocar el SSM ni
    el tuning: nos dice si las hipos "se le escapan" al modelo por
    sigma grande, predicción inexistente, o respuesta lenta.
    """
    err = _require_login()
    if err: return err
    try:
        import math
        from datetime import datetime, timedelta
        from models import db, GlucoseReading, GlucosePrediction

        days     = min(int(request.args.get("days", 30)), 365)
        horizon  = int(request.args.get("horizon", 30))   # 30 o 60
        model    = request.args.get("model", "ssm_v0_ukf6_basal")
        tol_min  = int(request.args.get("tol_min", 8))    # ± minutos de tolerancia

        # TZ local (CGM Libre y predicciones se guardan en hora local del servidor)
        cutoff = datetime.now() - timedelta(days=days)
        # Hipos reales (CGM < 70) en la ventana, excluyendo artefactos
        hypos = (db.session.query(GlucoseReading)
                 .filter(GlucoseReading.timestamp >= cutoff)
                 .filter(GlucoseReading.value_mgdl < 70)
                 .filter((GlucoseReading.is_artifact == False) | (GlucoseReading.is_artifact.is_(None)))
                 .order_by(GlucoseReading.timestamp.asc())
                 .all())

        gpred_col  = f"g_pred_{horizon}"
        sigma_col  = f"sigma_{horizon}"

        def p_hypo(gp, sigma):
            if not sigma or sigma <= 0:
                return 1.0 if gp < 70 else 0.0
            z = (70 - gp) / sigma
            return 0.5 * (1 + math.erf(z / math.sqrt(2)))

        events = []
        n_with_pred       = 0
        n_alert_triggered = 0
        n_no_prediction   = 0
        n_pred_above_70   = 0
        n_sigma_blocks    = 0   # pred<70 estricto pero sigma evita p_hypo>=0.30

        for h_event in hypos:
            target_pred_time = h_event.timestamp - timedelta(minutes=horizon)
            lo = target_pred_time - timedelta(minutes=tol_min)
            hi = target_pred_time + timedelta(minutes=tol_min)
            # Predicción más cercana a target_pred_time dentro de tolerancia
            cand = (db.session.query(GlucosePrediction)
                    .filter(GlucosePrediction.model_version == model)
                    .filter(GlucosePrediction.predicted_at >= lo)
                    .filter(GlucosePrediction.predicted_at <= hi)
                    .order_by(GlucosePrediction.predicted_at.desc())
                    .first())

            row = {
                "hypo_at":     h_event.timestamp.isoformat(),
                "real_mgdl":   round(h_event.value_mgdl, 1),
                "target_pred_at": target_pred_time.isoformat(),
            }
            if cand is None:
                n_no_prediction += 1
                row["status"] = "no_prediction"
                row["why"]    = f"sin predicción {model} en ±{tol_min}min de target"
            else:
                gp     = getattr(cand, gpred_col)
                sigma  = getattr(cand, sigma_col) or 0
                if gp is None:
                    n_no_prediction += 1
                    row["status"] = "no_prediction"
                    row["why"]    = f"predicción existe pero {gpred_col} es None"
                else:
                    ph = p_hypo(gp, sigma)
                    alert = ph >= 0.30
                    n_with_pred += 1
                    if alert: n_alert_triggered += 1
                    if gp >= 70: n_pred_above_70 += 1
                    if gp < 70 and not alert: n_sigma_blocks += 1
                    row.update({
                        "status":      "alert" if alert else "missed",
                        "pred_mgdl":   round(gp, 1),
                        "sigma":       round(sigma, 2),
                        "p_hypo":      round(ph, 3),
                        "error":       round(h_event.value_mgdl - gp, 1),
                        "lead_min":    round((h_event.timestamp - cand.predicted_at).total_seconds() / 60, 1),
                    })
            events.append(row)

        n_total = len(hypos)
        return jsonify({
            "ok":            True,
            "model":         model,
            "horizon_min":   horizon,
            "days":          days,
            "tol_min":       tol_min,
            "summary": {
                "n_real_hypos":          n_total,
                "n_with_prediction":     n_with_pred,
                "n_no_prediction":       n_no_prediction,
                "n_alert_triggered":     n_alert_triggered,
                "n_pred_above_70":       n_pred_above_70,
                "n_sigma_too_wide":      n_sigma_blocks,
                "recall_observed":       round(n_alert_triggered / n_total, 3) if n_total else None,
            },
            "interpretation": _interpret_post_mortem(
                n_total, n_no_prediction, n_pred_above_70, n_sigma_blocks, n_alert_triggered),
            "events":        events,
        })
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


def _interpret_post_mortem(n_total, n_no_pred, n_above, n_sigma, n_alert) -> str:
    if n_total == 0:
        return "No hubo hipoglucemias reales en la ventana — nada que evaluar."
    parts = []
    if n_no_pred / n_total >= 0.5:
        parts.append(f"{n_no_pred}/{n_total} hipos no tenían predicción activa "
                     f"30min antes → problema de COBERTURA, no del modelo.")
    if n_above / n_total >= 0.5:
        parts.append(f"{n_above}/{n_total} veces el modelo predijo glucosa ≥70 cuando "
                     f"luego hubo hipo → el SSM no anticipa caídas rápidas (problema de DINÁMICA).")
    if n_sigma / n_total >= 0.3:
        parts.append(f"{n_sigma}/{n_total} veces el modelo SÍ predijo <70 pero la "
                     f"incertidumbre era tan grande que p_hypo no superó 0.30 → "
                     f"sigma demasiado ancho.")
    if n_alert > 0:
        parts.append(f"{n_alert}/{n_total} hipos fueron correctamente anticipadas.")
    if not parts:
        parts.append("Patrón mixto, ver eventos individuales.")
    return " ".join(parts)


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
        return jsonify(_json_safe({"ok": True, "verdict": verdict(report, model=verdict_model)}))
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
