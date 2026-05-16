from flask import Blueprint, render_template, request, redirect, url_for, flash
from datetime import datetime, timedelta
from models import db, Activity
from helpers import parse_datetime, ACTIVIDADES_COMUNES

bp = Blueprint("actividad", __name__)


@bp.route("/actividad", endpoint="actividad")
def actividad():
    page = request.args.get("page", 1, type=int)
    dias = request.args.get("dias", 7, type=int)
    desde = datetime.now() - timedelta(days=dias)
    actividades = (
        Activity.query.filter(Activity.timestamp >= desde)
        .order_by(Activity.timestamp.desc())
        .paginate(page=page, per_page=30, error_out=False)
    )
    tipos = [a.activity_type for a in actividades.items]
    actividad_frecuente = max(set(tipos), key=tipos.count) if tipos else None
    return render_template("actividad.html", actividades=actividades,
                           dias=dias, actividad_frecuente=actividad_frecuente)


@bp.route("/actividad/nueva", methods=["GET", "POST"], endpoint="actividad_nueva")
def actividad_nueva():
    if request.method == "POST":
        fecha = request.form.get("fecha")
        hora  = request.form.get("hora")
        tipo  = request.form.get("activity_type", "").strip()
        duracion = request.form.get("duration_min", type=int)
        intensidad = request.form.get("intensity", "media")
        notas = request.form.get("notas", "").strip()

        if not tipo:
            flash("El tipo de actividad es obligatorio.", "danger")
            return redirect(url_for("actividad_nueva"))

        ex_type = request.form.get("exercise_type") or None
        act = Activity(
            timestamp=parse_datetime(fecha, hora),
            activity_type=tipo,
            duration_min=duracion,
            intensity=intensidad,
            exercise_type=ex_type,
            notes=notas,
        )
        db.session.add(act)
        db.session.commit()
        flash(f"{tipo} de {duracion} min registrada.", "success")
        return redirect(url_for("actividad"))

    ahora = datetime.now()
    return render_template(
        "actividad_form.html",
        fecha=ahora.strftime("%Y-%m-%d"),
        hora=ahora.strftime("%H:%M"),
        actividades_comunes=ACTIVIDADES_COMUNES,
    )


@bp.route("/actividad/<int:id>/eliminar", methods=["POST"], endpoint="actividad_eliminar")
def actividad_eliminar(id):
    act = Activity.query.get_or_404(id)
    db.session.delete(act)
    db.session.commit()
    flash("Actividad eliminada.", "info")
    return redirect(url_for("actividad"))


@bp.route("/actividad/<int:id>/editar", methods=["GET", "POST"], endpoint="actividad_editar")
def actividad_editar(id):
    act = Activity.query.get_or_404(id)
    if request.method == "POST":
        act.timestamp     = parse_datetime(request.form["fecha"], request.form["hora"])
        act.activity_type = request.form.get("activity_type", act.activity_type).strip()
        act.duration_min  = request.form.get("duration_min", type=int)
        act.intensity     = request.form.get("intensity", act.intensity)
        act.exercise_type = request.form.get("exercise_type") or None
        act.notes         = request.form.get("notas", "")
        db.session.commit()
        flash("Actividad actualizada.", "success")
        return redirect(url_for("actividad"))
    return render_template("actividad_form.html",
                           editar=True, item=act,
                           fecha=act.timestamp.strftime("%Y-%m-%d"),
                           hora=act.timestamp.strftime("%H:%M"),
                           actividades_comunes=ACTIVIDADES_COMUNES)
