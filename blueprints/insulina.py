from flask import Blueprint, render_template, request, redirect, url_for, flash
from datetime import datetime, timedelta
from models import db, InsulinDose
from helpers import parse_datetime

bp = Blueprint("insulina", __name__)


@bp.route("/insulina", endpoint="insulina")
def insulina():
    page = request.args.get("page", 1, type=int)
    dias = request.args.get("dias", 7, type=int)
    desde = datetime.now() - timedelta(days=dias)
    dosis_list = (
        InsulinDose.query.filter(InsulinDose.timestamp >= desde)
        .order_by(InsulinDose.timestamp.desc())
        .paginate(page=page, per_page=30, error_out=False)
    )
    return render_template("insulina.html", dosis=dosis_list, dias=dias)


@bp.route("/insulina/nueva", methods=["GET", "POST"], endpoint="insulina_nueva")
def insulina_nueva():
    if request.method == "POST":
        fecha = request.form.get("fecha")
        hora = request.form.get("hora")
        tipo = request.form.get("tipo", "bolus")
        unidades = request.form.get("units", type=float)
        marca = request.form.get("brand", "").strip()
        notas = request.form.get("notas", "")

        if not unidades or unidades <= 0:
            flash("Las unidades deben ser mayores a 0.", "danger")
            return redirect(url_for("insulina_nueva"))

        dosis = InsulinDose(
            timestamp=parse_datetime(fecha, hora),
            type=tipo,
            units=unidades,
            brand=marca,
            notes=notas,
        )
        db.session.add(dosis)
        db.session.commit()
        flash(f"Dosis de {unidades}U de insulina {tipo} registrada.", "success")
        return redirect(url_for("insulina"))

    ahora = datetime.now()
    return render_template(
        "insulina_form.html",
        fecha=ahora.strftime("%Y-%m-%d"),
        hora=ahora.strftime("%H:%M"),
    )


@bp.route("/insulina/<int:id>/eliminar", methods=["POST"], endpoint="insulina_eliminar")
def insulina_eliminar(id):
    dosis = InsulinDose.query.get_or_404(id)
    db.session.delete(dosis)
    db.session.commit()
    flash("Dosis eliminada.", "info")
    return redirect(url_for("insulina"))


@bp.route("/insulina/<int:id>/editar", methods=["GET", "POST"], endpoint="insulina_editar")
def insulina_editar(id):
    dosis = InsulinDose.query.get_or_404(id)
    if request.method == "POST":
        dosis.timestamp = parse_datetime(request.form["fecha"], request.form["hora"])
        dosis.type      = request.form.get("tipo", dosis.type)
        dosis.units     = request.form.get("units", type=float)
        dosis.brand     = request.form.get("brand", "").strip()
        dosis.notes     = request.form.get("notas", "")
        db.session.commit()
        flash("Dosis actualizada.", "success")
        return redirect(url_for("insulina"))
    return render_template("insulina_form.html",
                           editar=True, item=dosis,
                           fecha=dosis.timestamp.strftime("%Y-%m-%d"),
                           hora=dosis.timestamp.strftime("%H:%M"))
