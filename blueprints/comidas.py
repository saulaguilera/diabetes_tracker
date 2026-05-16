from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from datetime import datetime, timedelta
from collections import defaultdict
from models import db, Meal, MealComponent
from helpers import (
    parse_datetime, _auto_categorizar, _save_meal_components,
    _precargar_glucosa, _precargar_bolus, _glucosa_impacto,
    CATEGORIAS_REGLAS, CATEGORIA_COLORES,
)

bp = Blueprint("comidas", __name__)


@bp.route("/comidas/grupos", endpoint="comidas_grupos")
def comidas_grupos():
    dias  = request.args.get("dias", 30, type=int)
    tab   = request.args.get("tab", "categorias")   # categorias | ingredientes
    desde = datetime.now() - timedelta(days=dias)
    comidas = Meal.query.filter(Meal.timestamp >= desde).order_by(Meal.timestamp.desc()).all()

    # ── Pre-calcular impacto glucémico (1 query batch, sin N+1) ──────────
    all_readings = _precargar_glucosa(comidas, horas_post=2)
    impacto_por_meal = {}
    for c in comidas:
        imp = _glucosa_impacto(c, readings=all_readings)
        if imp:
            impacto_por_meal[c.id] = imp

    # ══ TAB 1: Por categoría (comportamiento original) ════════════════════
    grupos_cat: dict = defaultdict(list)
    sin_cat = []
    for c in comidas:
        if c.categoria:
            grupos_cat[c.categoria].append(c)
        else:
            sin_cat.append(c)

    resumen_grupos = []
    for cat in sorted(grupos_cat.keys()):
        items = grupos_cat[cat]
        carbs_vals = [c.carbs_g or 0 for c in items]
        avg_carbs  = round(sum(carbs_vals) / len(carbs_vals), 1) if carbs_vals else 0
        deltas = [impacto_por_meal[c.id]["delta"] for c in items if c.id in impacto_por_meal]
        resumen_grupos.append({
            "categoria":     cat,
            "color":         CATEGORIA_COLORES.get(cat, "secondary"),
            "n":             len(items),
            "avg_carbs":     avg_carbs,
            "avg_delta":     round(sum(deltas) / len(deltas), 0) if deltas else None,
            "n_con_glucosa": len(deltas),
            "ultimas":       items[:8],
        })

    # ══ TAB 2: Por ingrediente (nuevo - usa MealComponent) ════════════════
    ingrediente_data: dict = defaultdict(lambda: {
        "ocurrencias": [], "carbs_list": [], "platos": []
    })

    for c in comidas:
        imp = impacto_por_meal.get(c.id)
        if c.components:
            for comp in c.components:
                nombre = comp.name.strip()
                if not nombre:
                    continue
                ingrediente_data[nombre]["carbs_list"].append(comp.carbs_g or 0)
                if imp:
                    ingrediente_data[nombre]["ocurrencias"].append(imp["delta"])
                ingrediente_data[nombre]["platos"].append({
                    "plato":    c.name,
                    "fecha":    c.timestamp.strftime("%d/%m"),
                    "carbs":    int(comp.carbs_g or 0),
                    "delta":    imp["delta"] if imp else None,
                })

    # Construir lista ordenada por CH promedio (mayor impacto al frente)
    resumen_ingredientes = []
    for nombre, data in ingrediente_data.items():
        n = len(data["carbs_list"])
        avg_carbs = round(sum(data["carbs_list"]) / n, 1) if n else 0
        ocur = data["ocurrencias"]
        avg_delta = round(sum(ocur) / len(ocur), 0) if ocur else None
        resumen_ingredientes.append({
            "nombre":        nombre,
            "n":             n,
            "avg_carbs":     avg_carbs,
            "avg_delta":     avg_delta,
            "n_con_glucosa": len(ocur),
            "platos":        data["platos"][:6],
        })

    # Ordenar: primero por CH promedio (los que más carbos aportan)
    resumen_ingredientes.sort(key=lambda x: (-x["avg_carbs"], -x["n"]))

    # Ingredientes sin datos de glucosa = menos útiles, los movemos al final
    resumen_ingredientes.sort(key=lambda x: (x["avg_delta"] is None, -x["avg_carbs"]))

    n_sin_cat         = len(sin_cat)
    n_total           = len(comidas)
    n_con_componentes = sum(1 for c in comidas if c.components)
    n_sin_componentes = n_total - n_con_componentes
    # Cobertura global (all-time, no solo el período)
    total_global       = Meal.query.count()
    con_comp_global    = Meal.query.filter(Meal.components.any()).count()

    return render_template("comidas_grupos.html",
        grupos=resumen_grupos,
        ingredientes=resumen_ingredientes,
        active_tab=tab,
        categorias=sorted(CATEGORIAS_REGLAS.keys()),
        colores=CATEGORIA_COLORES,
        n_sin_cat=n_sin_cat,
        n_total=n_total,
        n_con_componentes=n_con_componentes,
        n_sin_componentes=n_sin_componentes,
        cobertura_global=round(con_comp_global / total_global * 100) if total_global else 0,
        total_global=total_global,
        con_comp_global=con_comp_global,
        dias=dias,
    )


@bp.route("/api/comidas/auto-categorizar", methods=["POST"], endpoint="api_auto_categorizar")
def api_auto_categorizar():
    """Auto-categoriza todas las comidas sin categoría."""
    sin_cat = Meal.query.filter(
        (Meal.categoria == None) | (Meal.categoria == "")
    ).all()
    asignadas = 0
    for c in sin_cat:
        cat = _auto_categorizar(c.name)
        if cat:
            c.categoria = cat
            asignadas += 1
    db.session.commit()
    return jsonify({"total": len(sin_cat), "asignadas": asignadas,
                    "sin_match": len(sin_cat) - asignadas})


@bp.route("/api/comidas/<int:id>/categoria", methods=["POST"], endpoint="api_set_categoria")
def api_set_categoria(id):
    """Cambia la categoría de una comida individual."""
    comida = Meal.query.get_or_404(id)
    comida.categoria = request.json.get("categoria", "").strip() or None
    db.session.commit()
    return jsonify({"ok": True, "categoria": comida.categoria})


@bp.route("/comidas", endpoint="comidas")
def comidas():
    page = request.args.get("page", 1, type=int)
    dias = request.args.get("dias", 7, type=int)
    solo_incompletas = request.args.get("incompletas", "0") == "1"
    desde = datetime.now() - timedelta(days=dias)

    if solo_incompletas:
        # Modo backfill: mostrar TODAS las comidas sin ingredientes (sin filtro de fecha)
        q = Meal.query.filter(~Meal.components.any())
    else:
        q = Meal.query.filter(Meal.timestamp >= desde)
    comidas_list = q.order_by(Meal.timestamp.desc()).paginate(page=page, per_page=30, error_out=False)

    # Contador global de comidas sin ingredientes (para el banner)
    n_sin_comp = Meal.query.filter(~Meal.components.any()).count()

    return render_template("comidas.html", comidas=comidas_list, dias=dias,
                           solo_incompletas=solo_incompletas,
                           n_sin_comp=n_sin_comp,
                           categorias=sorted(CATEGORIAS_REGLAS.keys()),
                           colores=CATEGORIA_COLORES)


@bp.route("/comidas/nueva", methods=["GET", "POST"], endpoint="comida_nueva")
def comida_nueva():
    if request.method == "POST":
        fecha  = request.form.get("fecha")
        hora   = request.form.get("hora")
        nombre = request.form.get("nombre", "").strip()
        notas  = request.form.get("notas", "")

        if not nombre:
            flash("El nombre de la comida es obligatorio.", "danger")
            return redirect(url_for("comida_nueva"))

        # Totales manuales (fallback si no hay componentes)
        carbs    = request.form.get("carbs_g",   0, type=float)
        fat      = request.form.get("fat_g",     0, type=float)
        protein  = request.form.get("protein_g", 0, type=float)
        calorias = request.form.get("calories",  0, type=float)

        comida = Meal(
            timestamp=parse_datetime(fecha, hora),
            name=nombre,
            carbs_g=carbs, fat_g=fat, protein_g=protein, calories=calorias,
            notes=notas,
        )
        db.session.add(comida)
        db.session.flush()   # obtener comida.id

        componentes = _save_meal_components(comida, request.form)
        for c in componentes:
            c.meal_id = comida.id
            db.session.add(c)

        db.session.commit()
        flash(f'Comida "{nombre}" registrada — {int(comida.carbs_g)}g CH'
              + (f' en {len(componentes)} ingredientes.' if componentes else '.'), "success")
        return redirect(url_for("comidas"))

    ahora = datetime.now()
    return render_template(
        "comida_form.html",
        fecha=ahora.strftime("%Y-%m-%d"),
        hora=ahora.strftime("%H:%M"),
    )


@bp.route("/comidas/<int:id>/eliminar", methods=["POST"], endpoint="comida_eliminar")
def comida_eliminar(id):
    comida = Meal.query.get_or_404(id)
    db.session.delete(comida)
    db.session.commit()
    flash("Comida eliminada.", "info")
    return redirect(url_for("comidas"))


@bp.route("/comidas/<int:id>/editar", methods=["GET", "POST"], endpoint="comida_editar")
def comida_editar(id):
    comida = Meal.query.get_or_404(id)
    if request.method == "POST":
        comida.timestamp = parse_datetime(request.form["fecha"], request.form["hora"])
        comida.name      = request.form.get("nombre", comida.name).strip()
        comida.notes     = request.form.get("notas", "")

        # Borrar componentes anteriores y recrear
        MealComponent.query.filter_by(meal_id=comida.id).delete()

        componentes = _save_meal_components(comida, request.form)
        if not componentes:
            # Sin componentes: tomar totales manuales
            comida.carbs_g   = request.form.get("carbs_g",   0, type=float)
            comida.fat_g     = request.form.get("fat_g",     0, type=float)
            comida.protein_g = request.form.get("protein_g", 0, type=float)
            comida.calories  = request.form.get("calories",  0, type=float)
        else:
            for c in componentes:
                c.meal_id = comida.id
                db.session.add(c)

        db.session.commit()
        flash(f'Comida "{comida.name}" actualizada.', "success")
        next_page = request.form.get("next", "")
        if next_page == "incompletas":
            return redirect(url_for("comidas", incompletas="1"))
        return redirect(url_for("comidas"))

    return render_template("comida_form.html",
                           editar=True, item=comida,
                           fecha=comida.timestamp.strftime("%Y-%m-%d"),
                           hora=comida.timestamp.strftime("%H:%M"))
