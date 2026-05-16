from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app
from datetime import datetime
from models import db, GlucoseReading, Meal, InsulinDose, Activity, FoodItem, CGMImport
from utils.libre_import import import_libre_csv
import os

bp = Blueprint("backup", __name__)


@bp.route("/backup/exportar-db", endpoint="backup_exportar_db")
def backup_exportar_db():
    """Descarga el archivo SQLite completo."""
    import shutil
    import tempfile
    from flask import send_file

    # Obtener la ruta real del archivo desde el engine de SQLAlchemy
    db_path = db.engine.url.database

    # En algunos entornos la ruta es relativa — resolverla desde instance/
    if not os.path.isabs(db_path):
        db_path = os.path.join(current_app.instance_path, db_path)

    if not os.path.exists(db_path):
        flash("No se encontró el archivo de base de datos.", "danger")
        return redirect(url_for("backup_importar"))

    # Copiar a temporal para no bloquear el archivo en uso
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    shutil.copy2(db_path, tmp.name)
    tmp.close()

    filename = f"diabetes_{datetime.now().strftime('%Y%m%d_%H%M')}.db"
    return send_file(
        tmp.name,
        mimetype="application/x-sqlite3",
        as_attachment=True,
        download_name=filename,
    )


@bp.route("/backup/exportar", endpoint="backup_exportar")
def backup_exportar():
    """Exporta toda la base de datos como JSON para backup."""
    from flask import Response
    import json as _json

    def serial(obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        raise TypeError(f"Type {type(obj)} not serializable")

    data = {
        "version": 1,
        "exportado_en": datetime.now().isoformat(),
        "glucemia": [
            {"id": r.id, "timestamp": r.timestamp.isoformat(),
             "value_mgdl": r.value_mgdl, "source": r.source, "notes": r.notes}
            for r in GlucoseReading.query.all()
        ],
        "comidas": [
            {"id": r.id, "timestamp": r.timestamp.isoformat(),
             "name": r.name, "carbs_g": r.carbs_g, "fat_g": r.fat_g,
             "protein_g": r.protein_g, "calories": r.calories,
             "notes": r.notes, "categoria": r.categoria,
             "components": [
                 {"name": c.name, "food_item_id": c.food_item_id,
                  "carbs_g": c.carbs_g, "protein_g": c.protein_g,
                  "fat_g": c.fat_g, "calories": c.calories, "grams": c.grams}
                 for c in r.components
             ]}
            for r in Meal.query.all()
        ],
        "insulina": [
            {"id": r.id, "timestamp": r.timestamp.isoformat(),
             "type": r.type, "units": r.units, "brand": r.brand, "notes": r.notes}
            for r in InsulinDose.query.all()
        ],
        "actividad": [
            {"id": r.id, "timestamp": r.timestamp.isoformat(),
             "activity_type": r.activity_type, "duration_min": r.duration_min,
             "intensity": r.intensity, "notes": r.notes}
            for r in Activity.query.all()
        ],
        "alimentos": [
            {"id": r.id, "name": r.name, "serving_desc": r.serving_desc,
             "serving_g": r.serving_g, "carbs_per_serving": r.carbs_per_serving,
             "fat_per_serving": r.fat_per_serving, "protein_per_serving": r.protein_per_serving,
             "calories_per_serving": r.calories_per_serving, "category": r.category,
             "notes": r.notes, "times_used": r.times_used}
            for r in FoodItem.query.all()
        ],
    }

    payload = _json.dumps(data, ensure_ascii=False, indent=2, default=serial)
    filename = f"diabetes_backup_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
    return Response(
        payload,
        mimetype="application/json",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@bp.route("/backup/importar", methods=["GET", "POST"], endpoint="backup_importar")
def backup_importar():
    """Restaura datos desde un archivo JSON de backup."""
    import json as _json

    if request.method == "POST":
        archivo = request.files.get("archivo")
        if not archivo or not archivo.filename.endswith(".json"):
            flash("Seleccioná un archivo .json de backup válido.", "danger")
            return redirect(request.url)

        try:
            data = _json.load(archivo)
            if data.get("version") != 1:
                flash("Formato de backup no reconocido.", "danger")
                return redirect(request.url)

            importados = {"glucemia": 0, "comidas": 0, "insulina": 0, "actividad": 0, "alimentos": 0}

            for r in data.get("glucemia", []):
                if not GlucoseReading.query.get(r["id"]):
                    db.session.add(GlucoseReading(
                        id=r["id"],
                        timestamp=datetime.fromisoformat(r["timestamp"]),
                        value_mgdl=r["value_mgdl"], source=r.get("source","manual"),
                        notes=r.get("notes")
                    ))
                    importados["glucemia"] += 1

            for r in data.get("comidas", []):
                if not Meal.query.get(r["id"]):
                    db.session.add(Meal(
                        id=r["id"],
                        timestamp=datetime.fromisoformat(r["timestamp"]),
                        name=r["name"], carbs_g=r.get("carbs_g",0),
                        fat_g=r.get("fat_g",0), protein_g=r.get("protein_g",0),
                        calories=r.get("calories",0), notes=r.get("notes"),
                        categoria=r.get("categoria")
                    ))
                    importados["comidas"] += 1

            for r in data.get("insulina", []):
                if not InsulinDose.query.get(r["id"]):
                    db.session.add(InsulinDose(
                        id=r["id"],
                        timestamp=datetime.fromisoformat(r["timestamp"]),
                        type=r["type"], units=r["units"],
                        brand=r.get("brand"), notes=r.get("notes")
                    ))
                    importados["insulina"] += 1

            for r in data.get("actividad", []):
                if not Activity.query.get(r["id"]):
                    db.session.add(Activity(
                        id=r["id"],
                        timestamp=datetime.fromisoformat(r["timestamp"]),
                        activity_type=r["activity_type"],
                        duration_min=r.get("duration_min"),
                        intensity=r.get("intensity"), notes=r.get("notes")
                    ))
                    importados["actividad"] += 1

            for r in data.get("alimentos", []):
                if not FoodItem.query.get(r["id"]):
                    db.session.add(FoodItem(
                        id=r["id"], name=r["name"],
                        serving_desc=r.get("serving_desc"), serving_g=r.get("serving_g"),
                        carbs_per_serving=r.get("carbs_per_serving",0),
                        fat_per_serving=r.get("fat_per_serving",0),
                        protein_per_serving=r.get("protein_per_serving",0),
                        calories_per_serving=r.get("calories_per_serving",0),
                        category=r.get("category"), notes=r.get("notes"),
                        times_used=r.get("times_used",0)
                    ))
                    importados["alimentos"] += 1

            db.session.commit()
            total = sum(importados.values())
            flash(f"✓ {total} registros importados: "
                  f"{importados['glucemia']} glucemias, {importados['comidas']} comidas, "
                  f"{importados['insulina']} insulinas, {importados['actividad']} actividades, "
                  f"{importados['alimentos']} alimentos.", "success")
            return redirect(url_for("dashboard"))

        except Exception as e:
            db.session.rollback()
            flash(f"Error al procesar el backup: {e}", "danger")
            return redirect(request.url)

    # GET — mostrar estadísticas actuales
    stats = {
        "glucemia": GlucoseReading.query.count(),
        "comidas":  Meal.query.count(),
        "insulina": InsulinDose.query.count(),
        "actividad": Activity.query.count(),
        "alimentos": FoodItem.query.count(),
    }
    imports = CGMImport.query.order_by(CGMImport.imported_at.desc()).all()
    active_tab = request.args.get("tab", "exportar")
    return render_template("backup.html", stats=stats, imports=imports, active_tab=active_tab)


@bp.route("/importar", methods=["GET", "POST"], endpoint="importar")
def importar():
    imports = CGMImport.query.order_by(CGMImport.imported_at.desc()).all()

    if request.method == "POST":
        if "archivo" not in request.files:
            flash("No se seleccionó ningún archivo.", "danger")
            return redirect(url_for("importar"))

        archivo = request.files["archivo"]
        if archivo.filename == "":
            flash("No se seleccionó ningún archivo.", "danger")
            return redirect(url_for("importar"))

        if not archivo.filename.lower().endswith(".csv"):
            flash("Solo se aceptan archivos CSV del Freestyle Libre.", "danger")
            return redirect(url_for("importar"))

        try:
            resultado = import_libre_csv(archivo, db, GlucoseReading, CGMImport)
            flash(
                f"Importación exitosa: {resultado['insertados']} nuevos registros "
                f"(de {resultado['total']} en el archivo, {resultado['duplicados']} ya existían).",
                "success",
            )
        except Exception as e:
            flash(f"Error al importar: {str(e)}", "danger")

        return redirect(url_for("importar"))

    return render_template("importar.html", imports=imports)
