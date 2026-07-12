from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from datetime import datetime
from models import db, FoodItem
from helpers import parse_datetime

bp = Blueprint("alimentos", __name__)


@bp.route("/alimentos", endpoint="alimentos")
def alimentos():
    categoria = request.args.get("categoria", "")
    q = request.args.get("q", "").strip()
    query = FoodItem.query
    if categoria:
        query = query.filter_by(category=categoria)
    if q:
        query = query.filter(FoodItem.name.ilike(f"%{q}%"))
    items = query.order_by(FoodItem.times_used.desc(), FoodItem.name).all()
    categorias = db.session.query(FoodItem.category).distinct().filter(
        FoodItem.category.isnot(None), FoodItem.category != ""
    ).all()
    categorias = [c[0] for c in categorias]
    return render_template("alimentos.html", items=items, categorias=categorias,
                           categoria=categoria, q=q)


@bp.route("/alimentos/nuevo", methods=["GET", "POST"], endpoint="alimento_nuevo")
def alimento_nuevo():
    if request.method == "POST":
        nombre = request.form.get("nombre", "").strip()
        if not nombre:
            flash("El nombre del alimento es obligatorio.", "danger")
            return redirect(url_for("alimento_nuevo"))

        item = FoodItem(
            name=nombre,
            serving_desc=request.form.get("serving_desc", "").strip(),
            serving_g=request.form.get("serving_g", type=float) or None,
            carbs_per_serving=request.form.get("carbs_per_serving", 0, type=float),
            fat_per_serving=request.form.get("fat_per_serving", 0, type=float),
            protein_per_serving=request.form.get("protein_per_serving", 0, type=float),
            calories_per_serving=request.form.get("calories_per_serving", 0, type=float),
            category=request.form.get("category", "").strip(),
            notes=request.form.get("notes", "").strip(),
        )
        db.session.add(item)
        db.session.commit()
        flash(f'Alimento "{nombre}" guardado ({item.carbs_per_serving}g CH por porción).', "success")

        # Si vino desde el formulario de comida, regresa ahí
        next_url = request.form.get("next")
        if next_url == "comida":
            return redirect(url_for("comida_nueva"))
        return redirect(url_for("alimentos"))

    ahora = datetime.now()
    return render_template("alimento_form.html",
                           next=request.args.get("next", ""),
                           fecha=ahora.strftime("%Y-%m-%d"),
                           hora=ahora.strftime("%H:%M"))


@bp.route("/alimentos/<int:id>/editar", methods=["GET", "POST"], endpoint="alimento_editar")
def alimento_editar(id):
    item = FoodItem.query.get_or_404(id)
    if request.method == "POST":
        item.name = request.form.get("nombre", item.name).strip()
        item.serving_desc = request.form.get("serving_desc", "").strip()
        item.serving_g = request.form.get("serving_g", type=float) or None
        item.carbs_per_serving = request.form.get("carbs_per_serving", 0, type=float)
        item.fat_per_serving = request.form.get("fat_per_serving", 0, type=float)
        item.protein_per_serving = request.form.get("protein_per_serving", 0, type=float)
        item.calories_per_serving = request.form.get("calories_per_serving", 0, type=float)
        item.category = request.form.get("category", "").strip()
        item.notes = request.form.get("notes", "").strip()
        db.session.commit()
        flash(f'Alimento "{item.name}" actualizado.', "success")
        return redirect(url_for("alimentos"))
    return render_template("alimento_form.html", item=item, next="")


@bp.route("/alimentos/<int:id>/eliminar", methods=["POST"], endpoint="alimento_eliminar")
def alimento_eliminar(id):
    item = FoodItem.query.get_or_404(id)
    db.session.delete(item)
    db.session.commit()
    flash("Alimento eliminado.", "info")
    return redirect(url_for("alimentos"))


@bp.route("/api/alimentos/buscar", endpoint="api_alimentos_buscar")
def api_alimentos_buscar():
    q = request.args.get("q", "").strip()
    if len(q) < 1:
        return jsonify([])
    items = (
        FoodItem.query
        .filter(FoodItem.name.ilike(f"%{q}%"))
        .order_by(FoodItem.times_used.desc())
        .limit(10).all()
    )
    return jsonify([{
        "id":         i.id,
        "name":       i.name,
        "serving_desc": i.serving_desc or "",
        "serving_g":  i.serving_g or 100,   # gramos de la porción de referencia
        "carbs":      i.carbs_per_serving,
        "fat":        i.fat_per_serving,
        "protein":    i.protein_per_serving,
        "calories":   i.calories_per_serving,
        "fiber":      i.fiber_per_serving or 0,
        "gi":         i.glycemic_index,
    } for i in items])


@bp.route("/api/estimar-macros", endpoint="api_estimar_macros")
def api_estimar_macros():
    """Estima proteínas, grasas y calorías dado un nombre de ingrediente y gramos de CH.
    Acepta también `ml` para alimentos líquidos (leche, jugo, refrescos, etc.)."""
    from utils.nutrition_db import estimar, buscar_nutricion
    nombre = request.args.get("nombre", "").strip()
    carbs  = request.args.get("carbs",  0, type=float)
    grams  = request.args.get("grams",  0, type=float)
    ml     = request.args.get("ml",     0, type=float)
    if not nombre:
        return jsonify({"error": "Falta el nombre"}), 400

    # 1. Base nutricional interna (80+ alimentos comunes, siempre disponible)
    from utils.nutrition_db import get_gi, gl_from_gi
    estimado = estimar(nombre, carbs_usuario=carbs, grams_usuario=grams, ml_usuario=ml)
    if estimado:
        gi = get_gi(nombre)
        return jsonify({
            "protein_g":      estimado["protein_g"],
            "fat_g":          estimado["fat_g"],
            "calories":       estimado["calories"],
            "carbs_g":        estimado["carbs_g"],       # CH netos
            "carbs_total":    estimado["carbs_total"],
            "fibra_g":        estimado["fibra_g"],
            "alta_fibra":     estimado["alta_fibra"],
            "glycemic_index": gi,
            "grams":          estimado.get("grams"),     # gramos finales (incluye conv. mL→g)
            "ml":             estimado.get("ml"),        # mL detectados (si aplica)
            "density":        estimado.get("density"),   # densidad usada (g/mL)
            "source":         nombre,
            "origin":         "interno",
            "nota":           estimado["nota"],
        })

    # 2. Base de alimentos del usuario (FoodItem)
    items = (FoodItem.query
             .filter(FoodItem.name.ilike(f"%{nombre}%"))
             .order_by(FoodItem.times_used.desc())
             .limit(5).all())
    for item in items:
        if item.carbs_per_serving and item.carbs_per_serving > 0 and carbs > 0:
            factor = carbs / item.carbs_per_serving
            return jsonify({
                "protein_g": round((item.protein_per_serving or 0) * factor, 1),
                "fat_g":     round((item.fat_per_serving     or 0) * factor, 1),
                "calories":  round((item.calories_per_serving or 0) * factor, 1),
                "source":    item.name,
                "origin":    "mis_alimentos",
            })

    # 3. Fallback: Open Food Facts
    try:
        import requests as _req
        resp = _req.get(
            "https://world.openfoodfacts.org/cgi/search.pl",
            params={
                "search_terms": nombre, "search_simple": 1,
                "action": "process", "json": 1,
                "fields": "product_name,nutriments", "page_size": 5,
            },
            timeout=5,
        )
        if resp.status_code == 200:
            data = resp.json()
            for product in data.get("products", []):
                n = product.get("nutriments", {})
                carbs_100 = n.get("carbohydrates_100g") or n.get("carbohydrates")
                if carbs_100 and float(carbs_100) > 0 and carbs > 0:
                    factor = carbs / float(carbs_100)
                    return jsonify({
                        "protein_g": round(float(n.get("proteins_100g",  0) or 0) * factor, 1),
                        "fat_g":     round(float(n.get("fat_100g",       0) or 0) * factor, 1),
                        "calories":  round(float(n.get("energy-kcal_100g", 0) or 0) * factor, 1),
                        "source":    product.get("product_name", nombre),
                        "origin":    "openfoodfacts",
                    })
    except Exception:
        pass

    return jsonify({"error": f"No encontré datos para «{nombre}». Intenta con otro nombre."}), 404


@bp.route("/api/alimentos/<int:id>/usar", methods=["POST"], endpoint="api_alimento_usar")
def api_alimento_usar(id):
    item = FoodItem.query.get_or_404(id)
    item.times_used += 1
    db.session.commit()
    return jsonify({"ok": True})
