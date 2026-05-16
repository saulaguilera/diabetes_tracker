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
