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

# ── CSRF Protection ───────────────────────────────────────────────────────────
from flask_wtf.csrf import CSRFProtect, generate_csrf
csrf = CSRFProtect(app)

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
    """Protege todas las rutas excepto login, logout, static y APIs con token válido."""
    exempt = {"login", "logout", "static"}
    if request.endpoint in exempt or session.get("logged_in"):
        return
    # Permitir llamadas de cron/API autenticadas con SYNC_TOKEN
    token_param = request.args.get("token", "")
    if _SYNC_TOKEN and token_param == _SYNC_TOKEN:
        return
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
            if "ml" not in mc_cols:
                conn.execute(text("ALTER TABLE meal_components ADD COLUMN ml REAL"))
                conn.commit()
            if "density_g_ml" not in mc_cols:
                conn.execute(text("ALTER TABLE meal_components ADD COLUMN density_g_ml REAL"))
                conn.commit()
        # Migración: fiber_per_serving y glycemic_index en food_items
        fi_cols = [c["name"] for c in inspector.get_columns("food_items")]
        if "fiber_per_serving" not in fi_cols:
            conn.execute(text("ALTER TABLE food_items ADD COLUMN fiber_per_serving REAL DEFAULT 0"))
            conn.commit()
        if "glycemic_index" not in fi_cols:
            conn.execute(text("ALTER TABLE food_items ADD COLUMN glycemic_index INTEGER"))
            conn.commit()
        # Migración: purpose y pre_meal_min en insulin_doses
        id_cols = [c["name"] for c in inspector.get_columns("insulin_doses")]
        if "purpose" not in id_cols:
            conn.execute(text("ALTER TABLE insulin_doses ADD COLUMN purpose VARCHAR(20)"))
            conn.commit()
        if "pre_meal_min" not in id_cols:
            conn.execute(text("ALTER TABLE insulin_doses ADD COLUMN pre_meal_min INTEGER"))
        # Migración: exercise_type en activities
        act_cols = [c["name"] for c in inspector.get_columns("activities")]
        if "exercise_type" not in act_cols:
            conn.execute(text("ALTER TABLE activities ADD COLUMN exercise_type VARCHAR(20)"))
            conn.commit()
        # Migración: columnas de calidad de lectura en glucose_readings
        gr_cols = [c["name"] for c in inspector.get_columns("glucose_readings")]
        for col_name, ddl in [
            ("is_artifact",         "ALTER TABLE glucose_readings ADD COLUMN is_artifact BOOLEAN DEFAULT 0"),
            ("artifact_reason",     "ALTER TABLE glucose_readings ADD COLUMN artifact_reason VARCHAR(40)"),
            ("original_value_mgdl", "ALTER TABLE glucose_readings ADD COLUMN original_value_mgdl REAL"),
            ("corrected_at",        "ALTER TABLE glucose_readings ADD COLUMN corrected_at DATETIME"),
        ]:
            if col_name not in gr_cols:
                conn.execute(text(ddl))
                conn.commit()
        # Migración: tabla glucose_predictions (feedback del modelo)
        # db.create_all() ya la crea si no existe — esto es solo un guard extra
        if "glucose_predictions" not in existing_tables:
            db.create_all()   # dialect-aware: funciona en SQLite y PostgreSQL
        else:
            # Migración incremental: columnas para calibración y versionado
            gp_cols = [c["name"] for c in inspector.get_columns("glucose_predictions")]
            if "sigma_30" not in gp_cols:
                conn.execute(text("ALTER TABLE glucose_predictions ADD COLUMN sigma_30 REAL"))
                conn.commit()
            if "sigma_60" not in gp_cols:
                conn.execute(text("ALTER TABLE glucose_predictions ADD COLUMN sigma_60 REAL"))
                conn.commit()
            if "model_version" not in gp_cols:
                conn.execute(text("ALTER TABLE glucose_predictions ADD COLUMN model_version VARCHAR(40)"))
                conn.commit()

        # ── PMM: Personal Metabolic Model ─────────────────────────────────────
        # db.create_all() crea las tablas nuevas automáticamente.
        # Las guardamos aquí por si el create_all inicial fue antes de agregar los modelos.
        if "pmm_parameters" not in existing_tables:
            db.create_all()
        if "pmm_observations" not in existing_tables:
            db.create_all()
        if "pmm_drift_state" not in existing_tables:
            db.create_all()

        # Tablas de validación científica (logging + diagnostics)
        if "prediction_audit" not in existing_tables:
            db.create_all()
        if "ssm_innovations" not in existing_tables:
            db.create_all()
        if "tuning_experiments" not in existing_tables:
            db.create_all()
        # Hito 8: audit trail de alertas de hipoglucemia nocturna
        if "hypo_risk_audit" not in existing_tables:
            db.create_all()
        else:
            # Migración incremental: campos de outcome tracking + alert fatigue
            hra_cols = [c["name"] for c in inspector.get_columns("hypo_risk_audit")]
            for col_name, ddl in [
                ("projected_trough_time", "ALTER TABLE hypo_risk_audit ADD COLUMN projected_trough_time DATETIME"),
                ("alert_triggered",       "ALTER TABLE hypo_risk_audit ADD COLUMN alert_triggered BOOLEAN DEFAULT 0"),
                ("resolved_confidence",   "ALTER TABLE hypo_risk_audit ADD COLUMN resolved_confidence REAL"),
                ("resolved_at",           "ALTER TABLE hypo_risk_audit ADD COLUMN resolved_at DATETIME"),
                ("outcome_class",         "ALTER TABLE hypo_risk_audit ADD COLUMN outcome_class VARCHAR(2)"),
                ("true_positive",         "ALTER TABLE hypo_risk_audit ADD COLUMN true_positive BOOLEAN"),
                ("false_positive",        "ALTER TABLE hypo_risk_audit ADD COLUMN false_positive BOOLEAN"),
                ("false_negative",        "ALTER TABLE hypo_risk_audit ADD COLUMN false_negative BOOLEAN"),
                ("true_negative",         "ALTER TABLE hypo_risk_audit ADD COLUMN true_negative BOOLEAN"),
                ("actual_nadir",          "ALTER TABLE hypo_risk_audit ADD COLUMN actual_nadir REAL"),
                ("actual_hypo_time",      "ALTER TABLE hypo_risk_audit ADD COLUMN actual_hypo_time DATETIME"),
                ("prediction_error",      "ALTER TABLE hypo_risk_audit ADD COLUMN prediction_error REAL"),
                ("warning_lead_time_min", "ALTER TABLE hypo_risk_audit ADD COLUMN warning_lead_time_min INTEGER"),
                ("alert_fatigue_ignored", "ALTER TABLE hypo_risk_audit ADD COLUMN alert_fatigue_ignored BOOLEAN DEFAULT 0"),
                ("dismissed_at",          "ALTER TABLE hypo_risk_audit ADD COLUMN dismissed_at DATETIME"),
            ]:
                if col_name not in hra_cols:
                    conn.execute(text(ddl))
                    conn.commit()

        if "daily_briefs" not in existing_tables:
            db.create_all()
        else:
            # Migración incremental: extensiones para lineage / reproducibility / gates
            te_cols = [c["name"] for c in inspector.get_columns("tuning_experiments")]
            for col_sql in [
                ("data_checksum",   "ALTER TABLE tuning_experiments ADD COLUMN data_checksum VARCHAR(40)"),
                ("random_seed",     "ALTER TABLE tuning_experiments ADD COLUMN random_seed INTEGER"),
                ("replay_checksum", "ALTER TABLE tuning_experiments ADD COLUMN replay_checksum VARCHAR(40)"),
                ("parent_name",     "ALTER TABLE tuning_experiments ADD COLUMN parent_name VARCHAR(120)"),
                ("diagnoses_json",  "ALTER TABLE tuning_experiments ADD COLUMN diagnoses_json TEXT"),
                ("gates_passed",    "ALTER TABLE tuning_experiments ADD COLUMN gates_passed INTEGER"),
                ("gates_json",      "ALTER TABLE tuning_experiments ADD COLUMN gates_json TEXT"),
            ]:
                col_name, ddl = col_sql
                if col_name not in te_cols:
                    conn.execute(text(ddl))
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

            def _weekly_report_job():
                with app.app_context():
                    try:
                        from utils.email_notifier import send_weekly_accuracy_report
                        send_weekly_accuracy_report()
                    except Exception:
                        pass

            def _pmm_calibration_job():
                with app.app_context():
                    try:
                        from pmm.engines.calibration import run_calibration
                        run_calibration()
                    except Exception:
                        pass

            def _pmm_drift_job():
                with app.app_context():
                    try:
                        from pmm.engines.drift import update_cusum
                        update_cusum()
                    except Exception:
                        pass

            def _daily_brief_job():
                """Genera el Daily Metabolic Brief al amanecer (06:30 local)."""
                with app.app_context():
                    try:
                        from blueprints.daily_brief_bp import _generate_and_persist
                        _generate_and_persist(force=False, tone="supportive")
                    except Exception as e:
                        import logging
                        logging.getLogger("daily_brief.scheduler").warning(
                            f"daily brief scheduler falló: {e}")

            scheduler = BackgroundScheduler(timezone=_tz)  # usa TZ del entorno (America/Santiago)
            scheduler.add_job(_sync_job, "interval", minutes=5, id="libre_sync")
            # PMM recalibración: cada hora
            scheduler.add_job(
                _pmm_calibration_job,
                "interval",
                hours=1,
                id="pmm_calibration",
            )
            # PMM drift CUSUM: cada 15 minutos (sensible a cambios metabólicos agudos)
            scheduler.add_job(
                _pmm_drift_job,
                "interval",
                minutes=15,
                id="pmm_drift",
            )
            # Reporte semanal: cada lunes a las 9:00am (hora local del servidor)
            scheduler.add_job(
                _weekly_report_job,
                "cron",
                day_of_week="mon",
                hour=9,
                minute=0,
                id="weekly_report",
            )
            # Daily Metabolic Brief: cada día a las 06:30 hora local
            scheduler.add_job(
                _daily_brief_job,
                "cron",
                hour=6,
                minute=30,
                id="daily_brief",
            )

            # ── Hito 8: chequeos nocturnos de riesgo de hipoglucemia ────────
            # Se ejecutan a las 22:00, 00:00 y 02:00. Si el riesgo es alto
            # (p_hypo_70 > 0.30), envía notificación push / email.
            def _hypo_risk_check_job():
                with app.app_context():
                    try:
                        from utils.hypo_risk_engine import (
                            assess_nocturnal_hypo_risk, should_alert,
                            format_alert_message,
                        )
                        from utils.kinetics import get_kinetics_snapshot
                        from pmm.ssm.basal_input import load_basal_doses, compute_basal_eff
                        from models import GlucoseReading
                        from datetime import datetime, timedelta

                        now = datetime.utcnow()

                        # Lectura CGM más reciente (últimos 15 min)
                        cutoff = now - timedelta(minutes=15)
                        last_cgm = (
                            GlucoseReading.query
                            .filter(GlucoseReading.timestamp >= cutoff)
                            .order_by(GlucoseReading.timestamp.desc())
                            .first()
                        )
                        if not last_cgm:
                            return   # sin CGM reciente — skip silencioso

                        snap = get_kinetics_snapshot(hours_lookback=4)
                        iob   = snap.get("iob_bolus", 0.0)
                        cob   = snap.get("cob", 0.0)
                        roc   = snap.get("roc") or 0.0

                        basal_doses = load_basal_doses(now)
                        basal_eff   = compute_basal_eff(now, basal_doses)

                        risk = assess_nocturnal_hypo_risk(
                            current_glucose      = float(last_cgm.value),
                            roc                  = roc,
                            proposed_bolus       = 0.0,   # sin bolus propuesto
                            current_iob          = iob,
                            current_basal_effect = basal_eff,
                            carbs_on_board       = cob,
                            timestamp            = now,
                        )

                        if should_alert(risk):
                            msg = format_alert_message(risk, compact=True)
                            import logging
                            logging.getLogger("hypo_risk.scheduler").warning(
                                f"ALERTA NOCTURNA: {msg} | score={risk.risk_score:.2f}"
                            )
                            # Intentar email si está disponible
                            try:
                                from utils.email_notifier import send_hypo_alert
                                send_hypo_alert(risk, format_alert_message(risk))
                            except Exception:
                                pass   # email es opcional
                    except Exception as _e:
                        import logging
                        logging.getLogger("hypo_risk.scheduler").debug(
                            f"hypo_risk_check_job falló silenciosamente: {_e}"
                        )

            for _h, _m, _jid in [(22, 0, "hypo_risk_2200"),
                                  (0,  0, "hypo_risk_0000"),
                                  (2,  0, "hypo_risk_0200")]:
                scheduler.add_job(
                    _hypo_risk_check_job,
                    "cron",
                    hour=_h,
                    minute=_m,
                    id=_jid,
                )

            scheduler.start()
            # Sync inicial al arrancar
            _sync_job()
            # Bootstrap PMM al arrancar (procesa historial completo si es primera vez)
            _pmm_calibration_job()

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
from blueprints.pmm_bp       import bp as pmm_bp
from blueprints.bench_bp     import bp as bench_bp
from blueprints.tuning_bp    import bp as tuning_bp
from blueprints.daily_brief_bp import bp as daily_brief_bp
from blueprints.health_bp     import bp as health_bp

for _bp in [auth_bp, glucemia_bp, insulina_bp, actividad_bp, alimentos_bp,
            backup_bp, sync_bp, comidas_bp, herramientas_bp, reportes_bp,
            patrones_bp, pmm_bp, bench_bp, tuning_bp, daily_brief_bp,
            health_bp]:
    app.register_blueprint(_bp)

# PMM blueprint exento de CSRF (API JSON)
csrf.exempt(pmm_bp)
csrf.exempt(bench_bp)
csrf.exempt(tuning_bp)
csrf.exempt(daily_brief_bp)
csrf.exempt(health_bp)

# sync blueprint: exento de CSRF (cron externo + APIs JSON)
csrf.exempt(sync_bp)

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
    ("sync.api_sync_libre_verbose", "api_sync_libre_verbose"),
    ("sync.api_sync_status",        "api_sync_status"),
    ("sync.api_ar_status",          "api_ar_status"),
    ("sync.api_ar_fit",             "api_ar_fit"),
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
    ("herramientas.configuracion",          "configuracion"),
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
    ("sync.api_kinetics",           "api_kinetics"),
    ("sync.api_dia_estimate",       "api_dia_estimate"),
    ("sync.api_diagnostico",        "api_diagnostico"),
    ("sync.api_backfill_insulin_labels", "api_backfill_insulin_labels"),
    ("sync.api_predict_glucose",    "api_predict_glucose"),
    ("sync.api_model_accuracy",           "api_model_accuracy"),
    ("sync.api_weekly_accuracy_report",   "api_weekly_accuracy_report"),
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
