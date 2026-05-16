import os
import functools

# Forzar zona horaria de Chile para que datetime.now() sea consistente
# con los timestamps guardados en la DB (hora local del usuario).
# TZ=America/Santiago puede sobreescribirse con variable de entorno en Railway.
_tz = os.environ.get("TZ", "America/Santiago")
os.environ["TZ"] = _tz
try:
    import time as _time_mod
    _time_mod.tzset()   # Aplica en Linux/Mac; no-op en Windows pero no falla
except AttributeError:
    pass

# Cargar .env si existe (desarrollo local)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session
from datetime import datetime, timedelta, timezone
from werkzeug.security import generate_password_hash, check_password_hash
from models import db, GlucoseReading, Meal, MealComponent, InsulinDose, Activity, CGMImport, FoodItem, UserSettings

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "diabetes-tracker-secret-2024")
app.config["SQLALCHEMY_DATABASE_URI"] = (
    os.environ.get("DATABASE_URL") or
    "sqlite:///" + os.path.join(os.environ.get("DATA_DIR", ""), "diabetes.db")
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16MB max upload

db.init_app(app)

# ── Credenciales desde variables de entorno ───────────────────────────────────
_APP_USER     = os.environ.get("APP_USERNAME", "admin")
_APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
_PASS_HASH    = generate_password_hash(_APP_PASSWORD) if _APP_PASSWORD else None


def login_required(f):
    """Decorador que redirige a /login si el usuario no está autenticado."""
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login", next=request.path))
        return f(*args, **kwargs)
    return decorated


def _protect_all():
    """Protege todas las rutas excepto login, logout y static."""
    exempt = {"login", "logout", "static"}
    if request.endpoint not in exempt and not session.get("logged_in"):
        return redirect(url_for("login", next=request.path))

app.before_request(_protect_all)


# ── Manejadores de error ──────────────────────────────────────────────────────

@app.errorhandler(404)
def page_not_found(e):
    return render_template("404.html"), 404


@app.errorhandler(500)
def internal_error(e):
    db.session.rollback()   # evita transacciones colgadas
    return render_template("500.html"), 500


with app.app_context():
    db.create_all()
    from sqlalchemy import text, inspect
    inspector = inspect(db.engine)
    with db.engine.connect() as conn:
        # Migración: columna categoria en meals
        cols = [c["name"] for c in inspector.get_columns("meals")]
        if "categoria" not in cols:
            conn.execute(text("ALTER TABLE meals ADD COLUMN categoria VARCHAR(50)"))
            conn.commit()
        # Migración: tabla meal_components (multi-ingrediente)
        existing_tables = inspector.get_table_names()
        if "meal_components" not in existing_tables:
            conn.execute(text("""
                CREATE TABLE meal_components (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    meal_id         INTEGER NOT NULL REFERENCES meals(id) ON DELETE CASCADE,
                    name            VARCHAR(200) NOT NULL,
                    food_item_id    INTEGER REFERENCES food_items(id),
                    carbs_g         REAL DEFAULT 0,
                    protein_g       REAL DEFAULT 0,
                    fat_g           REAL DEFAULT 0,
                    calories        REAL DEFAULT 0,
                    fiber_g         REAL DEFAULT 0,
                    glycemic_index  INTEGER,
                    grams           REAL
                )
            """))
            conn.commit()
        else:
            # Migración: agregar fiber_g y glycemic_index si no existen
            mc_cols = [c["name"] for c in inspector.get_columns("meal_components")]
            if "fiber_g" not in mc_cols:
                conn.execute(text("ALTER TABLE meal_components ADD COLUMN fiber_g REAL DEFAULT 0"))
                conn.commit()
            if "glycemic_index" not in mc_cols:
                conn.execute(text("ALTER TABLE meal_components ADD COLUMN glycemic_index INTEGER"))
                conn.commit()
        # Migración: fiber_per_serving y glycemic_index en food_items
        fi_cols = [c["name"] for c in inspector.get_columns("food_items")]
        if "fiber_per_serving" not in fi_cols:
            conn.execute(text("ALTER TABLE food_items ADD COLUMN fiber_per_serving REAL DEFAULT 0"))
            conn.commit()
        if "glycemic_index" not in fi_cols:
            conn.execute(text("ALTER TABLE food_items ADD COLUMN glycemic_index INTEGER"))
            conn.commit()


# ── Configuración LibreLinkUp ─────────────────────────────────────────────────
_LIBRE_EMAIL    = os.environ.get("LIBRE_EMAIL", "")
_LIBRE_PASSWORD = os.environ.get("LIBRE_PASSWORD", "")
_SYNC_TOKEN     = os.environ.get("SYNC_TOKEN", "")   # token secreto para cron job


# ── Filtro Jinja para timestamps en hora local del usuario ─────────────────────
# Uso: {{ obj.timestamp | local_ts }}          → "14/05 11:10"
#      {{ obj.timestamp | local_ts('hm') }}    → "11:10"
#      {{ obj.timestamp | local_ts('full') }}  → "14/05/2026 11:10"
#      {{ obj.timestamp | local_ts('dm') }}    → "14/05"
# El servidor devuelve el tiempo en UTC (naive). El filtro lo envuelve en un
# <time> con data-utc="ISO" y el JS en base.html lo convierte a hora local.
from markupsafe import Markup

@app.template_filter("categoria_color")
def categoria_color_filter(cat):
    """Mapea una categoría de comida a un color Bootstrap para los badges."""
    from helpers import CATEGORIA_COLORES
    return CATEGORIA_COLORES.get(cat, "secondary")


@app.template_filter("local_ts")
def local_ts_filter(dt, fmt="dmhm"):
    if dt is None:
        return ""
    iso = dt.strftime("%Y-%m-%dT%H:%M:%S")
    # Texto de fallback (UTC, se reemplaza por JS)
    if fmt == "hm":
        fallback = dt.strftime("%H:%M")
    elif fmt == "dm":
        fallback = dt.strftime("%d/%m")
    elif fmt == "full":
        fallback = dt.strftime("%d/%m/%Y %H:%M")
    else:  # dmhm por defecto
        fallback = dt.strftime("%d/%m %H:%M")
    return Markup(f'<time class="local-ts" data-utc="{iso}" data-fmt="{fmt}">{fallback}</time>')


# ── Context processor: endpoint sin prefijo de blueprint ─────────────────────
# Los blueprints registran endpoints como 'glucemia.glucemia', 'auth.dashboard',
# etc. Este processor inyecta `ep` con el nombre simple (sin prefijo) para que
# los checks de nav activo en base.html funcionen sin cambios adicionales.
@app.context_processor
def _inject_nav_endpoint():
    ep = request.endpoint or ''
    simple = ep.split('.', 1)[-1] if '.' in ep else ep
    return {'ep': simple}


# ── Scheduler automático (cada 5 min si hay credenciales configuradas) ────────
def _iniciar_scheduler():
    """Inicia APScheduler para sync automática si hay credenciales disponibles."""
    if not _LIBRE_EMAIL or not _LIBRE_PASSWORD:
        return  # Sin credenciales, no hay nada que hacer

    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.events import EVENT_JOB_ERROR
        import threading, fcntl, tempfile
        from blueprints.sync import _do_libre_sync

        # Lock de archivo para evitar que múltiples workers de gunicorn
        # ejecuten el scheduler al mismo tiempo
        lock_file = os.path.join(tempfile.gettempdir(), "dt_scheduler.lock")

        def _scheduler_worker():
            try:
                lock_fd = open(lock_file, "w")
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except (IOError, OSError):
                return  # Otro worker ya tiene el lock

            def _sync_job():
                with app.app_context():
                    try:
                        _do_libre_sync(_LIBRE_EMAIL, _LIBRE_PASSWORD)
                    except Exception:
                        pass

            scheduler = BackgroundScheduler(timezone=_tz)  # usa TZ del entorno (America/Santiago)
            scheduler.add_job(_sync_job, "interval", minutes=5, id="libre_sync")
            scheduler.start()
            # Sync inicial al arrancar
            _sync_job()

        t = threading.Thread(target=_scheduler_worker, daemon=True)
        t.start()
    except ImportError:
        pass  # APScheduler no instalado, skip silencioso
    except Exception:
        pass


# ── Registrar blueprints ───────────────────────────────────────────────────────
from blueprints.auth         import bp as auth_bp
from blueprints.glucemia     import bp as glucemia_bp
from blueprints.insulina     import bp as insulina_bp
from blueprints.actividad    import bp as actividad_bp
from blueprints.alimentos    import bp as alimentos_bp
from blueprints.backup       import bp as backup_bp
from blueprints.sync         import bp as sync_bp
from blueprints.comidas      import bp as comidas_bp
from blueprints.herramientas import bp as herramientas_bp
from blueprints.reportes     import bp as reportes_bp
from blueprints.patrones     import bp as patrones_bp

for _bp in [auth_bp, glucemia_bp, insulina_bp, actividad_bp, alimentos_bp,
            backup_bp, sync_bp, comidas_bp, herramientas_bp, reportes_bp, patrones_bp]:
    app.register_blueprint(_bp)

# ── Alias de endpoints para compatibilidad con templates ──────────────────────
# Los blueprints registran sus funciones como "blueprintname.endpointname".
# Para que url_for('login'), url_for('dashboard'), etc. sigan funcionando sin
# cambiar ningún template, creamos alias directos en app.view_functions.
_ENDPOINT_ALIASES = [
    ("auth.login",                  "login"),
    ("auth.logout",                 "logout"),
    ("auth.dashboard",              "dashboard"),
    ("glucemia.glucemia",           "glucemia"),
    ("glucemia.glucemia_nueva",     "glucemia_nueva"),
    ("glucemia.glucemia_eliminar",  "glucemia_eliminar"),
    ("glucemia.glucemia_editar",    "glucemia_editar"),
    ("insulina.insulina",           "insulina"),
    ("insulina.insulina_nueva",     "insulina_nueva"),
    ("insulina.insulina_eliminar",  "insulina_eliminar"),
    ("insulina.insulina_editar",    "insulina_editar"),
    ("actividad.actividad",         "actividad"),
    ("actividad.actividad_nueva",   "actividad_nueva"),
    ("actividad.actividad_eliminar","actividad_eliminar"),
    ("actividad.actividad_editar",  "actividad_editar"),
    ("alimentos.alimentos",         "alimentos"),
    ("alimentos.alimento_nuevo",    "alimento_nuevo"),
    ("alimentos.alimento_editar",   "alimento_editar"),
    ("alimentos.alimento_eliminar", "alimento_eliminar"),
    ("alimentos.api_alimentos_buscar", "api_alimentos_buscar"),
    ("alimentos.api_estimar_macros",   "api_estimar_macros"),
    ("alimentos.api_alimento_usar",    "api_alimento_usar"),
    ("backup.backup_exportar_db",   "backup_exportar_db"),
    ("backup.backup_exportar",      "backup_exportar"),
    ("backup.backup_importar",      "backup_importar"),
    ("backup.importar",             "importar"),
    ("sync.api_ultima_lectura",     "api_ultima_lectura"),
    ("sync.api_sync_libre_reset",   "api_sync_libre_reset"),
    ("sync.api_backfill_fiber_gi",  "api_backfill_fiber_gi"),
    ("sync.api_sync_libre_debug",   "api_sync_libre_debug"),
    ("sync.api_sync_libre",         "api_sync_libre"),
    ("sync.sync_libre_manual",      "sync_libre_manual"),
    ("comidas.comidas_grupos",      "comidas_grupos"),
    ("comidas.api_auto_categorizar","api_auto_categorizar"),
    ("comidas.api_set_categoria",   "api_set_categoria"),
    ("comidas.comidas",             "comidas"),
    ("comidas.comida_nueva",        "comida_nueva"),
    ("comidas.comida_eliminar",     "comida_eliminar"),
    ("comidas.comida_editar",       "comida_editar"),
    ("herramientas.calculadora",            "calculadora"),
    ("herramientas.api_calculadora_correccion", "api_calculadora_correccion"),
    ("herramientas.quicklog",               "quicklog"),
    ("herramientas.recomendaciones",        "recomendaciones"),
    ("reportes.agp",                "agp"),
    ("reportes.reportes",           "reportes"),
    ("reportes.api_chart_timeline", "api_chart_timeline"),
    ("reportes.api_chart_tir",      "api_chart_tir"),
    ("reportes.api_chart_por_hora", "api_chart_por_hora"),
    ("reportes.api_chart_impacto",  "api_chart_impacto"),
    ("reportes.api_chart_carbs_glucosa", "api_chart_carbs_glucosa"),
    ("reportes.reporte_semanal",    "reporte_semanal"),
    ("reportes.reporte_semanal_pdf","reporte_semanal_pdf"),
    ("reportes.api_settings_save",  "api_settings_save"),
    ("patrones.patrones",           "patrones"),
]

for _bp_endpoint, _flat_name in _ENDPOINT_ALIASES:
    if _bp_endpoint in app.view_functions:
        app.view_functions[_flat_name] = app.view_functions[_bp_endpoint]
        # Also register the flat endpoint name in the URL map so url_for() works
        for _rule in app.url_map.iter_rules(_bp_endpoint):
            try:
                app.add_url_rule(
                    _rule.rule,
                    endpoint=_flat_name,
                    view_func=app.view_functions[_flat_name],
                    methods=_rule.methods,
                )
            except (AssertionError, ValueError):
                pass  # rule already registered


# Arrancar scheduler cuando la app esté lista (no en modo test)
import sys
if "pytest" not in sys.modules and not app.config.get("TESTING"):
    with app.app_context():
        _iniciar_scheduler()


if __name__ == "__main__":
    import socket
    hostname = socket.gethostname()
    local_ip = socket.gethostbyname(hostname)
    print(f"\n  App corriendo en:")
    print(f"  → Computadora : http://localhost:5050")
    print(f"  → Celular/Red  : http://{local_ip}:5050")
    print(f"  (ambos dispositivos deben estar en la misma red WiFi)\n")
    app.run(debug=True, host="0.0.0.0", port=5050)
