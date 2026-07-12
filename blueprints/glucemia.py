from flask import Blueprint, render_template, request, redirect, url_for, flash
from datetime import datetime, timedelta
from models import db, GlucoseReading
from helpers import parse_datetime
from utils.charts import chart_glucose_timeline

bp = Blueprint("glucemia", __name__)


@bp.route("/glucemia", endpoint="glucemia")
def glucemia():
    page = request.args.get("page", 1, type=int)
    dias = request.args.get("dias", 7, type=int)
    desde = datetime.now() - timedelta(days=dias)
    lecturas = (
        GlucoseReading.query.filter(GlucoseReading.timestamp >= desde)
        .order_by(GlucoseReading.timestamp.desc())
        .paginate(page=page, per_page=50, error_out=False)
    )
    chart = chart_glucose_timeline(hours=dias * 24)
    return render_template("glucemia.html", lecturas=lecturas, chart=chart, dias=dias)


@bp.route("/glucemia/nueva", methods=["GET", "POST"], endpoint="glucemia_nueva")
def glucemia_nueva():
    if request.method == "POST":
        fecha = request.form.get("fecha")
        hora = request.form.get("hora")
        valor = request.form.get("valor", type=float)
        fuente = request.form.get("fuente", "manual")
        notas = request.form.get("notas", "")

        if not valor or valor <= 0:
            flash("El valor de glucemia debe ser mayor a 0.", "danger")
            return redirect(url_for("glucemia_nueva"))

        lectura = GlucoseReading(
            timestamp=parse_datetime(fecha, hora),
            value_mgdl=valor,
            source=fuente,
            notes=notas,
        )
        db.session.add(lectura)
        db.session.commit()
        # Actualizar filtro de Kalman con la nueva lectura manual
        try:
            from utils.kalman import update_with_reading as kalman_update
            kalman_update(lectura.value_mgdl, lectura.timestamp)
        except Exception:
            pass
        # Resolver predicciones pendientes con esta nueva lectura
        try:
            from utils.prediction_feedback import resolve_predictions
            resolve_predictions([lectura])
        except Exception:
            pass
        flash(f"Glucemia de {valor} mg/dL registrada correctamente.", "success")
        return redirect(url_for("glucemia"))

    ahora = datetime.now()
    return render_template(
        "glucemia_form.html",
        fecha=ahora.strftime("%Y-%m-%d"),
        hora=ahora.strftime("%H:%M"),
    )


@bp.route("/glucemia/marcar-artefacto-hora", methods=["GET", "POST"],
          endpoint="glucemia_marcar_artefacto_hora")
def glucemia_marcar_artefacto_hora():
    """
    Marca como artefacto la lectura CGM más cercana a un timestamp dado.
    Útil cuando LibreLinkUp no devuelve la corrección y necesitas invalidar
    una lectura específica desde el browser.

    Query params (GET) o JSON (POST):
        hora:    "HH:MM" — hora de hoy (default si no se pasa fecha)
        fecha:   "YYYY-MM-DD" — opcional, default = hoy
        reason:  texto opcional (default 'manual')

    Ejemplos de URL (basta con abrir en el browser, autenticado):
        /glucemia/marcar-artefacto-hora?hora=15:59
        /glucemia/marcar-artefacto-hora?hora=03:15&fecha=2026-05-22
        /glucemia/marcar-artefacto-hora?hora=15:59&reason=hipo-falsa-libre
    """
    from flask import jsonify, request as _req
    from utils.sensor_quality import mark_as_artifact

    # Aceptar query params o JSON body
    data = _req.get_json(silent=True) or {}
    hora   = (_req.args.get("hora")   or data.get("hora") or "").strip()
    fecha  = (_req.args.get("fecha")  or data.get("fecha") or "").strip()
    reason = (_req.args.get("reason") or data.get("reason") or "manual").strip()

    if not hora or ":" not in hora:
        return jsonify({"ok": False,
                        "error": "Falta param 'hora' en formato HH:MM. "
                                 "Ej: /glucemia/marcar-artefacto-hora?hora=15:59"}), 400

    try:
        hh, mm = [int(x) for x in hora.split(":")]
    except ValueError:
        return jsonify({"ok": False, "error": f"hora inválida: {hora}"}), 400

    # Resolver fecha (default hoy)
    if fecha:
        try:
            base_date = datetime.strptime(fecha, "%Y-%m-%d").date()
        except ValueError:
            return jsonify({"ok": False, "error": f"fecha inválida: {fecha}"}), 400
    else:
        base_date = datetime.now().date()

    target = datetime.combine(base_date, datetime.min.time()).replace(
        hour=hh, minute=mm)

    # Buscar lectura más cercana (±10 min)
    row = (GlucoseReading.query
           .filter(GlucoseReading.timestamp >= target - timedelta(minutes=10),
                   GlucoseReading.timestamp <= target + timedelta(minutes=10))
           .order_by(GlucoseReading.timestamp).all())
    if not row:
        return jsonify({"ok": False,
                        "error": f"No se encontró lectura cerca de {target}"})

    # Tomar la más cercana exacta
    closest = min(row, key=lambda r: abs((r.timestamp - target).total_seconds()))
    result = mark_as_artifact(closest.id, reason=reason)
    return jsonify({
        "ok":           result.get("ok", False),
        "id":           closest.id,
        "timestamp":    closest.timestamp.isoformat(),
        "value_mgdl":   closest.value_mgdl,
        "is_artifact":  True,
        "reason":       reason,
        "mensaje":      f"✓ Lectura {closest.timestamp.strftime('%d/%m %H:%M')} "
                        f"({closest.value_mgdl} mg/dL) marcada como artefacto. "
                        f"Recarga el dashboard para ver el chart limpio.",
    })


@bp.route("/glucemia/<int:id>/artefacto", methods=["POST"],
          endpoint="glucemia_marcar_artefacto")
def glucemia_marcar_artefacto(id):
    """
    Marca/desmarca una lectura como artefacto del sensor.
    Las lecturas marcadas se EXCLUYEN de: hypo predictor, daily brief,
    SSM filter, kinetics snapshot (ROC y last_glucose).
    """
    from flask import jsonify
    from utils.sensor_quality import mark_as_artifact, unmark_as_artifact
    row = GlucoseReading.query.get_or_404(id)

    data = request.get_json(silent=True) or {}
    action = data.get("action")
    reason = data.get("reason", "manual")
    if action is None:
        action = "unmark" if row.is_artifact else "mark"

    if action == "mark":
        result = mark_as_artifact(id, reason=reason)
    elif action == "unmark":
        result = unmark_as_artifact(id)
    else:
        return jsonify({"ok": False, "error": "action must be 'mark' or 'unmark'"}), 400
    return jsonify(result)


@bp.route("/api/sensor-quality/scan", methods=["POST"],
          endpoint="api_sensor_quality_scan")
def api_sensor_quality_scan():
    """Corre el detector drop-spike-recover en una ventana custom."""
    from flask import jsonify
    from utils.sensor_quality import flag_drop_spike_artifacts
    data = request.get_json(silent=True) or {}
    hours   = int(data.get("hours", 24))
    dry_run = bool(data.get("dry_run", False))
    result  = flag_drop_spike_artifacts(window_hours=hours, dry_run=dry_run)
    return jsonify({"ok": True, **result})


@bp.route("/api/sensor-quality/artifacts", endpoint="api_sensor_quality_list")
def api_sensor_quality_list():
    """Lista artefactos marcados en los últimos N días (default 7)."""
    from flask import jsonify
    from utils.sensor_quality import list_recent_artifacts
    days = int(request.args.get("days", 7))
    return jsonify({"ok": True, "artifacts": list_recent_artifacts(days=days)})


@bp.route("/glucemia/<int:id>/eliminar", methods=["POST"], endpoint="glucemia_eliminar")
def glucemia_eliminar(id):
    lectura = GlucoseReading.query.get_or_404(id)
    db.session.delete(lectura)
    db.session.commit()
    flash("Lectura eliminada.", "info")
    return redirect(url_for("glucemia"))


@bp.route("/glucemia/<int:id>/editar", methods=["GET", "POST"], endpoint="glucemia_editar")
def glucemia_editar(id):
    lectura = GlucoseReading.query.get_or_404(id)
    if request.method == "POST":
        lectura.timestamp  = parse_datetime(request.form["fecha"], request.form["hora"])
        lectura.value_mgdl = request.form.get("valor", type=float)
        lectura.source     = request.form.get("fuente", lectura.source)
        lectura.notes      = request.form.get("notas", "")
        db.session.commit()
        flash("Lectura actualizada.", "success")
        return redirect(url_for("glucemia"))
    return render_template("glucemia_form.html",
                           editar=True, item=lectura,
                           fecha=lectura.timestamp.strftime("%Y-%m-%d"),
                           hora=lectura.timestamp.strftime("%H:%M"))
