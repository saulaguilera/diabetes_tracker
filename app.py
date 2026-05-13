import os
import functools

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

from utils.libre_import import import_libre_csv
from utils.libre_linkup import sync_all as libre_sync_all
from utils.recommendations import generate_recommendations
from utils.pdf_charts import chart_pdf_tir, chart_pdf_circadiano, chart_pdf_timeline
from utils.pdf_report import generar_pdf
from utils.charts import (
    chart_glucose_timeline,
    chart_time_in_range,
    chart_glucose_by_hour,
    chart_meal_impact,
    chart_glucose_vs_carbs,
    chart_timeline_eventos,
    chart_activity_glucose_impact,
    chart_agp,
)

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
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    meal_id      INTEGER NOT NULL REFERENCES meals(id) ON DELETE CASCADE,
                    name         VARCHAR(200) NOT NULL,
                    food_item_id INTEGER REFERENCES food_items(id),
                    carbs_g      REAL DEFAULT 0,
                    protein_g    REAL DEFAULT 0,
                    fat_g        REAL DEFAULT 0,
                    calories     REAL DEFAULT 0,
                    grams        REAL
                )
            """))
            conn.commit()


# ── Configuración LibreLinkUp ─────────────────────────────────────────────────
_LIBRE_EMAIL    = os.environ.get("LIBRE_EMAIL", "")
_LIBRE_PASSWORD = os.environ.get("LIBRE_PASSWORD", "")
_SYNC_TOKEN     = os.environ.get("SYNC_TOKEN", "")   # token secreto para cron job


# ── Helpers de configuración de usuario ──────────────────────────────────────

def _get_setting(key, default=None):
    """Lee un valor de configuración personal."""
    s = UserSettings.query.filter_by(key=key).first()
    return s.value if s else default


def _set_setting(key, value):
    """Guarda o actualiza un valor de configuración personal."""
    from datetime import datetime as _dt
    s = UserSettings.query.filter_by(key=key).first()
    if s:
        s.value = str(value)
        s.updated_at = _dt.utcnow()
    else:
        db.session.add(UserSettings(key=key, value=str(value)))
    db.session.commit()


# ── Reglas de auto-categorización ────────────────────────────────────────────
CATEGORIAS_REGLAS = {
    "Lácteos":        ["yogur", "yoghurt", "yogurt", "leche", "queso", "crema",
                       "mantequilla", "kéfir", "jocoque", "requesón", "cottage"],
    "Carnes":         ["pollo", "pechuga", "muslo", "carne", "res", "cerdo", "pavo",
                       "pato", "costilla", "chuleta", "bistec", "milanesa", "salchicha",
                       "chorizo", "jamón", "tocino", "bacon", "cordero", "conejo"],
    "Pescados":       ["salmón", "atún", "tilapia", "merluza", "camarón", "pescado",
                       "mariscos", "pulpo", "calamar", "mejillón", "sardina", "trucha"],
    "Huevos":         ["huevo", "omelette", "omelet", "revuelto", "estrellado"],
    "Cereales":       ["arroz", "pan", "pasta", "avena", "cereal", "tortilla",
                       "galleta", "granola", "maíz", "trigo", "cebada", "quinoa",
                       "espagueti", "fideos", "lasaña", "pizza", "taco", "burrito",
                       "quesadilla", "sope", "tostada", "tamale", "tamal"],
    "Frutas":         ["manzana", "plátano", "banana", "naranja", "fresa", "uva",
                       "mango", "sandía", "melón", "durazno", "pera", "kiwi",
                       "piña", "papaya", "frambuesa", "arándano", "cereza", "ciruela",
                       "guayaba", "maracuyá", "fruta"],
    "Verduras":       ["ensalada", "brócoli", "espinaca", "zanahoria", "tomate",
                       "lechuga", "pepino", "calabaza", "calabacín", "chayote",
                       "nopales", "acelga", "col", "coliflor", "ejotes", "ejote",
                       "betabel", "pimiento", "cebolla", "ajo", "elote", "verdura"],
    "Legumbres":      ["frijol", "frijoles", "lenteja", "lentejas", "garbanzo",
                       "habas", "soya", "edamame", "chícharo", "alverjón"],
    "Alcohol":        ["cerveza", "vino", "whisky", "whiskey", "tequila", "mezcal",
                       "ron", "vodka", "alcohol", "copa", "trago", "gin", "brandy",
                       "cognac", "champagne", "sidra", "michelada", "cóctel", "cocktail",
                       "licor", "bourbon"],
    "Dulces/Postres": ["chocolate", "helado", "nieve", "pastel", "torta dulce",
                       "galleta", "dulce", "azúcar", "postre", "gelatina", "flan",
                       "cheesecake", "brownie", "donut", "dona", "churro", "cajeta",
                       "caramelo", "malvavisco", "mermelada", "miel", "pan dulce"],
    "Bebidas":        ["jugo", "refresco", "café", "té", "agua", "licuado",
                       "batido", "smoothie", "atole", "cocoa", "proteína", "shake",
                       "bebida", "limonada", "naranjada", "soda"],
    "Snacks/Botanas": ["nueces", "almendras", "cacahuate", "maní", "papas", "chips",
                       "palomitas", "pistache", "semillas", "amaranto", "barra",
                       "proteína barra", "trail mix", "totopos", "botana"],
    "Comida rápida":  ["hamburguesa", "hot dog", "nuggets", "alitas", "sandwich",
                       "subway", "kfc", "mcdonalds", "burger", "papas fritas", "nachos"],
    "Sopas/Caldos":   ["sopa", "caldo", "consomé", "pozole", "menudo", "birria",
                       "crema de", "bisque", "minestrone", "ramen"],
}

# Colores por categoría para las badges
CATEGORIA_COLORES = {
    "Lácteos":        "info",
    "Carnes":         "danger",
    "Pescados":       "primary",
    "Huevos":         "warning",
    "Cereales":       "warning",
    "Frutas":         "success",
    "Verduras":       "success",
    "Legumbres":      "secondary",
    "Alcohol":        "dark",
    "Dulces/Postres": "danger",
    "Bebidas":        "info",
    "Snacks/Botanas": "warning",
    "Comida rápida":  "danger",
    "Sopas/Caldos":   "secondary",
}


def _auto_categorizar(nombre: str) -> str | None:
    """Detecta la categoría de una comida por su nombre."""
    nombre_lower = nombre.lower()
    for categoria, palabras in CATEGORIAS_REGLAS.items():
        for palabra in palabras:
            if palabra in nombre_lower:
                return categoria
    return None


# ─── Helpers ──────────────────────────────────────────────────────────────────

def parse_datetime(date_str, time_str):
    """Combina strings de fecha y hora en un objeto datetime."""
    try:
        return datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
    except ValueError:
        return datetime.now(timezone.utc).replace(tzinfo=None)


def stats_resumen():
    """Estadísticas rápidas para el dashboard."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    hace_24h = now - timedelta(hours=24)
    hace_7d = now - timedelta(days=7)

    lecturas_24h = GlucoseReading.query.filter(
        GlucoseReading.timestamp >= hace_24h
    ).all()

    valores = [r.value_mgdl for r in lecturas_24h]
    if valores:
        promedio = round(sum(valores) / len(valores), 1)
        minimo = round(min(valores), 1)
        maximo = round(max(valores), 1)
        en_rango = round(
            len([v for v in valores if 70 <= v <= 180]) / len(valores) * 100, 1
        )
        hipo = round(len([v for v in valores if v < 70]) / len(valores) * 100, 1)
        hiper = round(len([v for v in valores if v > 180]) / len(valores) * 100, 1)
    else:
        promedio = minimo = maximo = en_rango = hipo = hiper = None

    ultima_lectura = (
        GlucoseReading.query.order_by(GlucoseReading.timestamp.desc()).first()
    )
    ultima_comida = Meal.query.order_by(Meal.timestamp.desc()).first()
    ultima_insulina = InsulinDose.query.order_by(InsulinDose.timestamp.desc()).first()

    comidas_7d = Meal.query.filter(Meal.timestamp >= hace_7d).count()

    return {
        "promedio_24h": promedio,
        "minimo_24h": minimo,
        "maximo_24h": maximo,
        "tir_24h": en_rango,
        "hipo_24h": hipo,
        "hiper_24h": hiper,
        "lecturas_24h": len(lecturas_24h),
        "ultima_lectura": ultima_lectura,
        "ultima_comida": ultima_comida,
        "ultima_insulina": ultima_insulina,
        "comidas_7d": comidas_7d,
    }


# ── Filtro Jinja para color de categoría ──────────────────────────────────────
@app.template_filter("categoria_color")
def categoria_color_filter(cat):
    return CATEGORIA_COLORES.get(cat, "secondary")


# ─── Detección de patrones peligrosos ─────────────────────────────────────────

def _detectar_patrones(days=30):
    """Analiza los últimos N días y devuelve lista de alertas de patrones."""
    from collections import defaultdict, Counter

    alertas = []
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    desde = now - timedelta(days=days)

    lecturas = (GlucoseReading.query
                .filter(GlucoseReading.timestamp >= desde)
                .order_by(GlucoseReading.timestamp).all())

    if not lecturas:
        return []

    # ── 1. Hipoglucemias nocturnas recurrentes (00:00–06:00) ─────────────────
    hipos_noct = [r for r in lecturas if r.value_mgdl < 70 and 0 <= r.timestamp.hour < 6]
    noches_con_hipo = {r.timestamp.date() for r in hipos_noct}
    if len(noches_con_hipo) >= 2:
        horas = sorted({r.timestamp.hour for r in hipos_noct})
        franja = f"{min(horas):02d}:00–0{max(horas)+1}:00" if max(horas) < 6 else "madrugada"
        alertas.append({
            "nivel":   "danger",
            "icono":   "bi-moon-stars-fill",
            "titulo":  "Hipoglucemias nocturnas recurrentes",
            "detalle": (f"{len(noches_con_hipo)} noches con glucosa < 70 mg/dL "
                        f"entre las 00:00 y las 06:00 en los últimos {days} días."),
            "accion":  "Revisá tu dosis de insulina basal nocturna con tu endocrinólogo.",
        })

    # ── 2. Fenómeno del alba (sube sola entre 3am y 9am) ─────────────────────
    alba_count = 0
    fechas_alba = set()
    lecturas_por_hora = defaultdict(list)
    for r in lecturas:
        lecturas_por_hora[(r.timestamp.date(), r.timestamp.hour)].append(r)

    for r in lecturas:
        if r.timestamp.hour not in (3, 4, 5):
            continue
        fecha = r.timestamp.date()
        if fecha in fechas_alba:
            continue
        # Buscar lectura 3–6h después (hora 7–9am)
        vals_mañana = []
        for h in (7, 8, 9):
            vals_mañana += [x.value_mgdl for x in lecturas_por_hora.get((fecha, h), [])]
        if vals_mañana and max(vals_mañana) - r.value_mgdl > 40:
            alba_count += 1
            fechas_alba.add(fecha)

    if alba_count >= 3:
        alertas.append({
            "nivel":   "warning",
            "icono":   "bi-sunrise-fill",
            "titulo":  "Posible fenómeno del alba",
            "detalle": (f"En {alba_count} días se detectó un alza de glucosa > 40 mg/dL "
                        f"entre las 3am y las 9am sin ingesta previa."),
            "accion":  "Consultá con tu médico el ajuste de basal nocturna o el horario de Tresiba/Lantus.",
        })

    # ── 3. Hipoglucemias post-ejercicio ──────────────────────────────────────
    actividades = Activity.query.filter(Activity.timestamp >= desde).all()
    hipos_post_ej = []
    for act in actividades:
        ventana = act.timestamp + timedelta(hours=6)
        hipo = next((r for r in lecturas
                     if act.timestamp < r.timestamp <= ventana
                     and r.value_mgdl < 70), None)
        if hipo:
            hipos_post_ej.append((act, hipo))

    if len(hipos_post_ej) >= 2:
        alertas.append({
            "nivel":   "warning",
            "icono":   "bi-bicycle",
            "titulo":  "Hipoglucemias recurrentes post-ejercicio",
            "detalle": (f"{len(hipos_post_ej)} veces con glucosa < 70 mg/dL "
                        f"en las 6 horas posteriores a actividad física."),
            "accion":  "Considerá reducir el bolo previo al ejercicio o consumir CH extra antes de entrenar.",
        })

    # ── 4. Hipos recurrentes en la misma franja horaria (diurna) ─────────────
    hipos_diurnas = [r for r in lecturas if r.value_mgdl < 70 and 6 <= r.timestamp.hour < 24]
    conteo_hora = Counter(r.timestamp.hour for r in hipos_diurnas)
    for hora, cnt in conteo_hora.items():
        if cnt >= 3:
            alertas.append({
                "nivel":   "danger",
                "icono":   "bi-clock-history",
                "titulo":  f"Hipos frecuentes a las {hora:02d}:00 hs",
                "detalle": (f"{cnt} hipoglucemias registradas entre las {hora:02d}:00 "
                            f"y las {hora:02d}:59 en los últimos {days} días."),
                "accion":  "Revisá si coincide con el pico de acción de tu insulina o con el horario de comidas.",
            })

    # ── 5. Hiperglucemias frecuentes post-comida (pico > 250 mg/dL) ──────────
    comidas_periodo = Meal.query.filter(Meal.timestamp >= desde).all()
    hipers_post = 0
    for c in comidas_periodo:
        pico = next((r for r in lecturas
                     if c.timestamp < r.timestamp <= c.timestamp + timedelta(hours=2)
                     and r.value_mgdl > 250), None)
        if pico:
            hipers_post += 1

    if hipers_post >= 3:
        alertas.append({
            "nivel":   "warning",
            "icono":   "bi-graph-up-arrow",
            "titulo":  "Hiperglucemias frecuentes post-comida",
            "detalle": (f"{hipers_post} comidas con pico > 250 mg/dL en las 2 horas siguientes "
                        f"en los últimos {days} días."),
            "accion":  "Revisá el timing del bolo pre-comida y tu ratio insulina:carbohidratos.",
        })

    return alertas


# ─── Sync LibreLinkUp ────────────────────────────────────────────────────────

def _do_libre_sync(email: str, password: str) -> dict:
    """
    Descarga lecturas de LibreLinkUp e inserta las nuevas en la base de datos.
    Retorna {"insertadas": int, "total": int, "error": str|None, "ultima": datetime|None}
    """
    resultado = libre_sync_all(email, password,
                               get_setting_fn=_get_setting,
                               set_setting_fn=_set_setting)
    if resultado["error"]:
        return {"insertadas": 0, "total": 0,
                "error": resultado["error"], "ultima": None}

    readings  = resultado["readings"]
    insertadas = 0
    ultima_ts  = None

    for r in readings:
        if not r["value_mgdl"] or r["value_mgdl"] < 20:
            continue
        # Verificar si ya existe (ventana ±2 min para evitar duplicados)
        ts = r["timestamp"]
        existe = GlucoseReading.query.filter(
            GlucoseReading.timestamp >= ts - timedelta(minutes=2),
            GlucoseReading.timestamp <= ts + timedelta(minutes=2),
        ).first()
        if not existe:
            db.session.add(GlucoseReading(
                timestamp=ts,
                value_mgdl=r["value_mgdl"],
                source="cgm_libre",
                notes=r.get("trend", ""),
            ))
            insertadas += 1
            if ultima_ts is None or ts > ultima_ts:
                ultima_ts = ts

    if insertadas:
        db.session.commit()

    # Guardar timestamp de última sync exitosa
    _set_setting("libre_last_sync", datetime.now().isoformat())
    _set_setting("libre_last_sync_ok", "1")

    return {
        "insertadas": insertadas,
        "total":      len(readings),
        "error":      None,
        "ultima":     ultima_ts,
    }


@app.route("/api/ultima-lectura")
def api_ultima_lectura():
    """Mini-widget sidebar: última lectura de glucosa."""
    r = GlucoseReading.query.order_by(GlucoseReading.timestamp.desc()).first()
    if not r:
        return jsonify({"value": None})
    delta = datetime.now() - r.timestamp
    mins  = int(delta.total_seconds() / 60)
    if mins < 1:
        time_ago = "ahora"
    elif mins < 60:
        time_ago = f"hace {mins}min"
    else:
        time_ago = f"hace {mins // 60}h"
    return jsonify({
        "value":    int(r.value_mgdl),
        "trend":    r.notes if r.notes in ["↑↑","↑","↗","→","↘","↓","↓↓"] else "→",
        "time_ago": time_ago,
    })


@app.route("/api/sync/libre/reset")
def api_sync_libre_reset():
    """Borra el caché de token para forzar un login fresco."""
    if not session.get("logged_in"):
        return jsonify({"error": "No autorizado"}), 401
    for key in ("libre_token", "libre_base_url", "libre_token_expiry",
                "libre_account_id", "libre_last_sync"):
        _set_setting(key, "")
    return jsonify({"ok": True, "mensaje": "Caché borrado. Apretá ↺ para hacer login fresco."})


@app.route("/api/sync/libre/debug")
def api_sync_libre_debug():
    """
    Diagnóstico usando el token cacheado — NO hace login nuevo para evitar rate limiting.
    """
    if not session.get("logged_in"):
        return jsonify({"error": "No autorizado"}), 401

    import requests as _req

    token      = _get_setting("libre_token")
    base_url   = _get_setting("libre_base_url")
    account_id = _get_setting("libre_account_id") or ""
    expiry     = _get_setting("libre_token_expiry")

    if not token or not base_url:
        return jsonify({
            "estado": "sin_token",
            "mensaje": "No hay token cacheado. Apretá ↺ en el dashboard para hacer el primer login (esperá 15 min si tuviste errores 430).",
        })

    try:
        import hashlib as _hl
        from utils.libre_linkup import _decode_jwt_account_id, _hash_account_id
        raw_id = _decode_jwt_account_id(token)
        hashed = _hash_account_id(raw_id)

        # Abbott espera SHA-256(user_id) como Account-Id
        effective_account_id = hashed or account_id

        get_headers = {
            "product":        "llu.android",
            "version":        "4.16.0",
            "Accept":         "application/json",
            "User-Agent":     "LibreLinkUp/4.16.0 (Android)",
            "Authorization":  f"Bearer {token}",
            "Account-Id":     effective_account_id,
        }
        r = _req.get(f"{base_url}/llu/connections",
                     headers=get_headers, timeout=15)

        # Si funcionó con el hash, guardarlo para sync_all
        if r.status_code == 200 and hashed:
            _set_setting("libre_account_id", hashed)

        return jsonify({
            "base_url":           base_url,
            "raw_user_id":        raw_id[:8] + "..." if raw_id else "(vacío)",
            "account_id_hashed":  hashed[:16] + "..." if hashed else "(vacío)",
            "token_expiry":       expiry,
            "connections_status": r.status_code,
            "connections_resp":   r.json() if r.status_code == 200 else r.text[:400],
        })
    except Exception as e:
        return jsonify({"error": str(e)})


@app.route("/api/sync/libre")
def api_sync_libre():
    """
    Endpoint de sincronización con LibreLinkUp.
    Puede ser llamado:
      - Desde el dashboard (autenticado con sesión)
      - Desde un cron job de Railway (con ?token=SYNC_TOKEN)
    """
    # Autenticación: sesión web O token de cron job
    token_param = request.args.get("token", "")
    if not session.get("logged_in"):
        if not _SYNC_TOKEN or token_param != _SYNC_TOKEN:
            return jsonify({"error": "No autorizado"}), 401

    email    = _LIBRE_EMAIL
    password = _LIBRE_PASSWORD

    if not email or not password:
        return jsonify({
            "error": "Configurá LIBRE_EMAIL y LIBRE_PASSWORD en las variables de entorno de Railway."
        }), 400

    resultado = _do_libre_sync(email, password)
    return jsonify(resultado)


@app.route("/sync/libre")
def sync_libre_manual():
    """Vista de sincronización manual con feedback visual."""
    email    = _LIBRE_EMAIL
    password = _LIBRE_PASSWORD

    if not email or not password:
        flash("Configurá LIBRE_EMAIL y LIBRE_PASSWORD en Railway para usar la sync automática.", "warning")
        return redirect(url_for("importar"))

    resultado = _do_libre_sync(email, password)

    if resultado["error"]:
        flash(f"Error en sync: {resultado['error']}", "danger")
    else:
        msg = f"✓ Libre sync: {resultado['insertadas']} lecturas nuevas"
        if resultado["total"] > 0 and resultado["insertadas"] == 0:
            msg += " (todas ya estaban registradas)"
        flash(msg, "success")

    return redirect(url_for("dashboard"))


# ── Scheduler automático (cada 5 min si hay credenciales configuradas) ────────
def _iniciar_scheduler():
    """Inicia APScheduler para sync automática si hay credenciales disponibles."""
    if not _LIBRE_EMAIL or not _LIBRE_PASSWORD:
        return  # Sin credenciales, no hay nada que hacer

    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.events import EVENT_JOB_ERROR
        import threading, fcntl, tempfile

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

            scheduler = BackgroundScheduler(timezone="America/Argentina/Buenos_Aires")
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


# Arrancar scheduler cuando la app esté lista (no en modo test)
import sys
if "pytest" not in sys.modules and not app.config.get("TESTING"):
    with app.app_context():
        _iniciar_scheduler()


# ─── Autenticación ────────────────────────────────────────────────────────────

@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("logged_in"):
        return redirect(url_for("dashboard"))

    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        if not _APP_PASSWORD:
            # Sin contraseña configurada: mostrar aviso en lugar de dejar entrar
            error = "El servidor no tiene contraseña configurada. Definí APP_PASSWORD."
        elif (username == _APP_USER and
              check_password_hash(_PASS_HASH, password)):
            session.permanent = True
            session["logged_in"] = True
            session["username"] = username
            next_url = request.args.get("next") or url_for("dashboard")
            return redirect(next_url)
        else:
            error = "Usuario o contraseña incorrectos."

    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ─── Dashboard ────────────────────────────────────────────────────────────────

@app.route("/")
def dashboard():
    stats   = stats_resumen()
    chart   = chart_glucose_timeline(hours=24)
    alertas = _detectar_patrones(days=30)

    # Estado de sync Libre
    libre_configured = bool(_LIBRE_EMAIL and _LIBRE_PASSWORD)
    ultima_sync_raw  = _get_setting("libre_last_sync")
    ultima_sync      = None
    if ultima_sync_raw:
        try:
            ts = datetime.fromisoformat(ultima_sync_raw)
            delta = datetime.now() - ts
            mins  = int(delta.total_seconds() / 60)
            if mins < 1:
                ultima_sync = "ahora"
            elif mins < 60:
                ultima_sync = f"hace {mins}min"
            else:
                horas = mins // 60
                ultima_sync = f"hace {horas}h"
        except Exception:
            pass

    return render_template("dashboard.html", stats=stats, chart=chart, alertas=alertas,
                           libre_configured=libre_configured, ultima_sync=ultima_sync)


# ─── Glucemia ─────────────────────────────────────────────────────────────────

@app.route("/glucemia")
def glucemia():
    page = request.args.get("page", 1, type=int)
    dias = request.args.get("dias", 7, type=int)
    desde = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=dias)
    lecturas = (
        GlucoseReading.query.filter(GlucoseReading.timestamp >= desde)
        .order_by(GlucoseReading.timestamp.desc())
        .paginate(page=page, per_page=50, error_out=False)
    )
    chart = chart_glucose_timeline(hours=dias * 24)
    return render_template("glucemia.html", lecturas=lecturas, chart=chart, dias=dias)


@app.route("/glucemia/nueva", methods=["GET", "POST"])
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
        flash(f"Glucemia de {valor} mg/dL registrada correctamente.", "success")
        return redirect(url_for("glucemia"))

    ahora = datetime.now()
    return render_template(
        "glucemia_form.html",
        fecha=ahora.strftime("%Y-%m-%d"),
        hora=ahora.strftime("%H:%M"),
    )


@app.route("/glucemia/<int:id>/eliminar", methods=["POST"])
def glucemia_eliminar(id):
    lectura = GlucoseReading.query.get_or_404(id)
    db.session.delete(lectura)
    db.session.commit()
    flash("Lectura eliminada.", "info")
    return redirect(url_for("glucemia"))


# ─── Comidas ──────────────────────────────────────────────────────────────────

@app.route("/comidas/grupos")
def comidas_grupos():
    dias = request.args.get("dias", 30, type=int)
    desde = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=dias)
    comidas = Meal.query.filter(Meal.timestamp >= desde).order_by(Meal.timestamp.desc()).all()

    # Agrupar por categoría
    from collections import defaultdict
    grupos: dict = defaultdict(list)
    sin_cat = []
    for c in comidas:
        cat = c.categoria or ""
        if cat:
            grupos[cat].append(c)
        else:
            sin_cat.append(c)

    # Para cada grupo calcular stats + impacto glucémico
    resumen_grupos = []
    for cat in sorted(grupos.keys()):
        items = grupos[cat]
        carbs_vals = [c.carbs_g or 0 for c in items]
        avg_carbs = round(sum(carbs_vals) / len(carbs_vals), 1) if carbs_vals else 0

        # Impacto glucémico promedio
        deltas = []
        for c in items:
            pre = (GlucoseReading.query
                   .filter(GlucoseReading.timestamp >= c.timestamp - timedelta(minutes=30),
                           GlucoseReading.timestamp <= c.timestamp)
                   .order_by(GlucoseReading.timestamp.desc()).first())
            posts = (GlucoseReading.query
                     .filter(GlucoseReading.timestamp > c.timestamp,
                             GlucoseReading.timestamp <= c.timestamp + timedelta(hours=2))
                     .all())
            if pre and posts:
                pico = max(r.value_mgdl for r in posts)
                deltas.append(pico - pre.value_mgdl)

        resumen_grupos.append({
            "categoria":    cat,
            "color":        CATEGORIA_COLORES.get(cat, "secondary"),
            "n":            len(items),
            "avg_carbs":    avg_carbs,
            "avg_delta":    round(sum(deltas) / len(deltas), 0) if deltas else None,
            "n_con_glucosa":len(deltas),
            "ultimas":      items[:8],   # muestra las últimas 8
        })

    # Contar sin categoría
    n_sin_cat = len(sin_cat)
    n_total   = len(comidas)

    return render_template("comidas_grupos.html",
        grupos=resumen_grupos,
        categorias=sorted(CATEGORIAS_REGLAS.keys()),
        colores=CATEGORIA_COLORES,
        n_sin_cat=n_sin_cat,
        n_total=n_total,
        dias=dias,
    )


@app.route("/api/comidas/auto-categorizar", methods=["POST"])
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


@app.route("/api/comidas/<int:id>/categoria", methods=["POST"])
def api_set_categoria(id):
    """Cambia la categoría de una comida individual."""
    comida = Meal.query.get_or_404(id)
    comida.categoria = request.json.get("categoria", "").strip() or None
    db.session.commit()
    return jsonify({"ok": True, "categoria": comida.categoria})


@app.route("/comidas")
def comidas():
    page = request.args.get("page", 1, type=int)
    dias = request.args.get("dias", 7, type=int)
    desde = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=dias)
    comidas_list = (
        Meal.query.filter(Meal.timestamp >= desde)
        .order_by(Meal.timestamp.desc())
        .paginate(page=page, per_page=30, error_out=False)
    )
    return render_template("comidas.html", comidas=comidas_list, dias=dias,
                           categorias=sorted(CATEGORIAS_REGLAS.keys()),
                           colores=CATEGORIA_COLORES)


def _save_meal_components(comida, form):
    """Parsea y guarda los componentes de una comida desde el form."""
    comp_names    = form.getlist("comp_name[]")
    comp_carbs    = form.getlist("comp_carbs[]")
    comp_proteins = form.getlist("comp_protein[]")
    comp_fats     = form.getlist("comp_fat[]")
    comp_cals     = form.getlist("comp_calories[]")
    comp_grams    = form.getlist("comp_grams[]")
    comp_food_ids = form.getlist("comp_food_id[]")

    # Limpiar componentes vacíos
    componentes = []
    for i, name in enumerate(comp_names):
        name = name.strip()
        if not name:
            continue
        def _f(lst, idx, default=0.0):
            try: return float(lst[idx]) if lst[idx].strip() else default
            except (IndexError, ValueError): return default
        componentes.append(MealComponent(
            name         = name,
            food_item_id = comp_food_ids[i].strip() or None if i < len(comp_food_ids) else None,
            carbs_g      = _f(comp_carbs, i),
            protein_g    = _f(comp_proteins, i),
            fat_g        = _f(comp_fats, i),
            calories     = _f(comp_cals, i),
            grams        = _f(comp_grams, i) or None,
        ))

    # Actualizar totales en la comida padre
    if componentes:
        comida.carbs_g   = round(sum(c.carbs_g   for c in componentes), 1)
        comida.protein_g = round(sum(c.protein_g for c in componentes), 1)
        comida.fat_g     = round(sum(c.fat_g     for c in componentes), 1)
        comida.calories  = round(sum(c.calories  for c in componentes), 1)
    return componentes


@app.route("/comidas/nueva", methods=["GET", "POST"])
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


@app.route("/comidas/<int:id>/eliminar", methods=["POST"])
def comida_eliminar(id):
    comida = Meal.query.get_or_404(id)
    db.session.delete(comida)
    db.session.commit()
    flash("Comida eliminada.", "info")
    return redirect(url_for("comidas"))


# ─── Insulina ─────────────────────────────────────────────────────────────────

@app.route("/insulina")
def insulina():
    page = request.args.get("page", 1, type=int)
    dias = request.args.get("dias", 7, type=int)
    desde = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=dias)
    dosis_list = (
        InsulinDose.query.filter(InsulinDose.timestamp >= desde)
        .order_by(InsulinDose.timestamp.desc())
        .paginate(page=page, per_page=30, error_out=False)
    )
    return render_template("insulina.html", dosis=dosis_list, dias=dias)


@app.route("/insulina/nueva", methods=["GET", "POST"])
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


@app.route("/insulina/<int:id>/eliminar", methods=["POST"])
def insulina_eliminar(id):
    dosis = InsulinDose.query.get_or_404(id)
    db.session.delete(dosis)
    db.session.commit()
    flash("Dosis eliminada.", "info")
    return redirect(url_for("insulina"))


# ─── Actividad física ────────────────────────────────────────────────────────

ACTIVIDADES_COMUNES = [
    "Caminata", "Correr", "Ciclismo", "Natación", "Pesas / Gym",
    "Fútbol", "Básquetbol", "Yoga", "Pilates", "Baile",
    "Escaleras", "Ejercicio en casa", "Otro",
]

@app.route("/actividad")
def actividad():
    page = request.args.get("page", 1, type=int)
    dias = request.args.get("dias", 7, type=int)
    desde = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=dias)
    actividades = (
        Activity.query.filter(Activity.timestamp >= desde)
        .order_by(Activity.timestamp.desc())
        .paginate(page=page, per_page=30, error_out=False)
    )
    tipos = [a.activity_type for a in actividades.items]
    actividad_frecuente = max(set(tipos), key=tipos.count) if tipos else None
    return render_template("actividad.html", actividades=actividades,
                           dias=dias, actividad_frecuente=actividad_frecuente)


@app.route("/actividad/nueva", methods=["GET", "POST"])
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

        act = Activity(
            timestamp=parse_datetime(fecha, hora),
            activity_type=tipo,
            duration_min=duracion,
            intensity=intensidad,
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


@app.route("/actividad/<int:id>/eliminar", methods=["POST"])
def actividad_eliminar(id):
    act = Activity.query.get_or_404(id)
    db.session.delete(act)
    db.session.commit()
    flash("Actividad eliminada.", "info")
    return redirect(url_for("actividad"))


# ─── Alimentos frecuentes ────────────────────────────────────────────────────

@app.route("/alimentos")
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


@app.route("/alimentos/nuevo", methods=["GET", "POST"])
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


@app.route("/alimentos/<int:id>/editar", methods=["GET", "POST"])
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


@app.route("/alimentos/<int:id>/eliminar", methods=["POST"])
def alimento_eliminar(id):
    item = FoodItem.query.get_or_404(id)
    db.session.delete(item)
    db.session.commit()
    flash("Alimento eliminado.", "info")
    return redirect(url_for("alimentos"))


@app.route("/api/alimentos/buscar")
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
        "id": i.id,
        "name": i.name,
        "serving_desc": i.serving_desc or "",
        "carbs": i.carbs_per_serving,
        "fat": i.fat_per_serving,
        "protein": i.protein_per_serving,
        "calories": i.calories_per_serving,
    } for i in items])


@app.route("/api/alimentos/<int:id>/usar", methods=["POST"])
def api_alimento_usar(id):
    item = FoodItem.query.get_or_404(id)
    item.times_used += 1
    db.session.commit()
    return jsonify({"ok": True})


# ─── Importar Freestyle Libre ─────────────────────────────────────────────────

@app.route("/backup/exportar-db")
def backup_exportar_db():
    """Descarga el archivo SQLite completo."""
    import shutil
    import tempfile
    from flask import send_file
    from sqlalchemy import engine as _sa_engine

    # Obtener la ruta real del archivo desde el engine de SQLAlchemy
    db_path = db.engine.url.database

    # En algunos entornos la ruta es relativa — resolverla desde instance/
    if not os.path.isabs(db_path):
        db_path = os.path.join(app.instance_path, db_path)

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


@app.route("/backup/exportar")
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


@app.route("/backup/importar", methods=["GET", "POST"])
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


@app.route("/importar", methods=["GET", "POST"])
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


# ─── Reportes ─────────────────────────────────────────────────────────────────

def _tabla_impacto_comidas(days):
    """
    Calcula el impacto glucémico de cada comida registrada.
    Devuelve (filas_individuales, resumen_por_alimento).
    """
    desde = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
    comidas = Meal.query.filter(Meal.timestamp >= desde).order_by(Meal.timestamp.desc()).all()

    filas = []
    for c in comidas:
        pre = (
            GlucoseReading.query
            .filter(
                GlucoseReading.timestamp >= c.timestamp - timedelta(minutes=30),
                GlucoseReading.timestamp <= c.timestamp,
            )
            .order_by(GlucoseReading.timestamp.desc())
            .first()
        )
        posts = (
            GlucoseReading.query
            .filter(
                GlucoseReading.timestamp > c.timestamp,
                GlucoseReading.timestamp <= c.timestamp + timedelta(hours=2),
            )
            .all()
        )
        if not pre or not posts:
            continue
        pico = max(r.value_mgdl for r in posts)
        delta = round(pico - pre.value_mgdl, 0)
        if pico > 180:
            estado = "hiper"
        elif pico < 70:
            estado = "hipo"
        else:
            estado = "rango"

        # Buscar dosis de insulina rápida (bolus) en ventana -60min a +30min de la comida
        dosis_cercanas = (
            InsulinDose.query
            .filter(
                InsulinDose.type == "bolus",
                InsulinDose.timestamp >= c.timestamp - timedelta(hours=1),
                InsulinDose.timestamp <= c.timestamp + timedelta(minutes=30),
            )
            .order_by(InsulinDose.timestamp)
            .all()
        )
        insulina = []
        for d in dosis_cercanas:
            diff_min = int((d.timestamp - c.timestamp).total_seconds() / 60)
            if diff_min < 0:
                timing = f"{abs(diff_min)} min antes"
            elif diff_min == 0:
                timing = "al comer"
            else:
                timing = f"{diff_min} min después"
            insulina.append({
                "units":  d.units,
                "type":   d.type,
                "timing": timing,
            })

        # Componentes de esta comida (para mostrar en la tabla de detalle)
        comp_list = [
            {"nombre": comp.name, "carbs": int(comp.carbs_g or 0)}
            for comp in c.components
        ]

        filas.append({
            "fecha":       c.timestamp.strftime("%d/%m %H:%M"),
            "nombre":      c.name,
            "carbs":       int(c.carbs_g or 0),
            "componentes": comp_list,   # ingredientes desglosados
            "pre":         int(pre.value_mgdl),
            "pico":        int(pico),
            "delta":       int(delta),
            "estado":      estado,
            "insulina":    insulina,
        })

    # ── Resumen por ingrediente (no por nombre de plato) ─────────────────────
    from collections import defaultdict
    agrupado = defaultdict(list)
    for f in filas:
        if f["componentes"]:
            # Agrupa por ingrediente individual: cada uno hereda el impacto
            # glucémico del plato completo (delta / pico compartido)
            for comp in f["componentes"]:
                agrupado[comp["nombre"]].append({
                    "carbs": comp["carbs"],
                    "pico":  f["pico"],
                    "delta": f["delta"],
                })
        else:
            # Comida sin componentes (registro antiguo): grupo por nombre del plato
            agrupado[f["nombre"]].append({
                "carbs": f["carbs"],
                "pico":  f["pico"],
                "delta": f["delta"],
            })

    resumen = []
    for nombre, eventos in sorted(agrupado.items(), key=lambda x: -sum(e["delta"] for e in x[1]) / len(x[1])):
        n = len(eventos)
        avg_delta = round(sum(e["delta"] for e in eventos) / n)
        avg_pico  = round(sum(e["pico"]  for e in eventos) / n)
        avg_carbs = round(sum(e["carbs"] for e in eventos) / n)
        resumen.append({
            "nombre":    nombre,
            "n":         n,
            "avg_carbs": avg_carbs,
            "avg_pico":  avg_pico,
            "avg_delta": avg_delta,
        })

    return filas, resumen


@app.route("/agp")
def agp():
    """Ambulatory Glucose Profile."""
    from collections import defaultdict
    import statistics

    days = request.args.get("dias", 14, type=int)
    now  = datetime.now(timezone.utc).replace(tzinfo=None)
    desde = now - timedelta(days=days)

    lecturas = (GlucoseReading.query
                .filter(GlucoseReading.timestamp >= desde)
                .order_by(GlucoseReading.timestamp).all())

    valores = [r.value_mgdl for r in lecturas]
    n_dias  = len({r.timestamp.date() for r in lecturas})

    # ── Estadísticas resumen ──────────────────────────────────────────────────
    if valores:
        media = round(sum(valores) / len(valores), 1)
        gmi   = round(3.31 + 0.02392 * media, 1)
        std   = round(statistics.stdev(valores), 1) if len(valores) > 1 else 0
        cv    = round(std / media * 100, 1) if media else 0

        n = len(valores)
        tir_muy_bajo = round(len([v for v in valores if v < 54])  / n * 100, 1)
        tir_bajo     = round(len([v for v in valores if 54 <= v < 70])  / n * 100, 1)
        tir_rango    = round(len([v for v in valores if 70 <= v <= 180]) / n * 100, 1)
        tir_alto     = round(len([v for v in valores if 180 < v <= 250]) / n * 100, 1)
        tir_muy_alto = round(len([v for v in valores if v > 250]) / n * 100, 1)
    else:
        media = gmi = std = cv = None
        tir_muy_bajo = tir_bajo = tir_rango = tir_alto = tir_muy_alto = None

    stats_agp = dict(
        media=media, gmi=gmi, std=std, cv=cv, n_lecturas=len(valores),
        n_dias=n_dias,
        tir_muy_bajo=tir_muy_bajo, tir_bajo=tir_bajo, tir_rango=tir_rango,
        tir_alto=tir_alto, tir_muy_alto=tir_muy_alto,
    )

    chart = chart_agp(days=days)
    return render_template("agp.html", chart=chart, stats=stats_agp, dias=days)


@app.route("/reportes")
def reportes():
    dias = request.args.get("dias", 30, type=int)
    charts = {
        "por_hora":       chart_glucose_by_hour(days=dias),
        "carbs":          chart_glucose_vs_carbs(days=dias),
        "timeline_full":  chart_timeline_eventos(hours=dias * 24),
        "actividad":      chart_activity_glucose_impact(days=dias),
    }
    tabla_filas, tabla_resumen = _tabla_impacto_comidas(dias)
    return render_template("reportes.html", dias=dias, charts=charts,
                           tabla_filas=tabla_filas, tabla_resumen=tabla_resumen)


@app.route("/api/charts/timeline")
def api_chart_timeline():
    horas = request.args.get("horas", 168, type=int)
    return jsonify(chart_glucose_timeline(hours=horas))


@app.route("/api/charts/tir")
def api_chart_tir():
    dias = request.args.get("dias", 30, type=int)
    return jsonify(chart_time_in_range(days=dias))


@app.route("/api/charts/por_hora")
def api_chart_por_hora():
    dias = request.args.get("dias", 30, type=int)
    return jsonify(chart_glucose_by_hour(days=dias))


@app.route("/api/charts/impacto_comidas")
def api_chart_impacto():
    dias = request.args.get("dias", 30, type=int)
    return jsonify(chart_meal_impact(days=dias))


@app.route("/api/charts/carbs_vs_glucosa")
def api_chart_carbs_glucosa():
    dias = request.args.get("dias", 90, type=int)
    return jsonify(chart_glucose_vs_carbs(days=dias))


# ─── Reporte semanal ──────────────────────────────────────────────────────────

@app.route("/reporte-semanal")
def reporte_semanal():
    import statistics as _stats

    dias = request.args.get("dias", 14, type=int)
    desde = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=dias)
    hasta = datetime.now(timezone.utc).replace(tzinfo=None)

    lecturas = GlucoseReading.query.filter(GlucoseReading.timestamp >= desde).order_by(GlucoseReading.timestamp).all()
    valores = [r.value_mgdl for r in lecturas]

    stats = {}
    if valores:
        n = len(valores)
        mean = round(sum(valores) / n, 1)
        sd   = round(_stats.stdev(valores), 1) if n > 1 else 0
        cv   = round(sd / mean * 100, 1) if mean > 0 else 0
        tir   = round(len([v for v in valores if 70 <= v <= 180]) / n * 100, 1)
        tbr   = round(len([v for v in valores if v < 70])  / n * 100, 1)
        tbr54 = round(len([v for v in valores if v < 54])  / n * 100, 1)
        tar   = round(len([v for v in valores if v > 180]) / n * 100, 1)
        tar250= round(len([v for v in valores if v > 250]) / n * 100, 1)
        gmi   = round(3.31 + 0.02392 * mean, 1)   # estimado de HbA1c
        stats = {
            "mean": mean, "sd": sd, "cv": cv,
            "min": int(min(valores)), "max": int(max(valores)),
            "tir": tir, "tbr": tbr, "tbr54": tbr54,
            "tar": tar, "tar250": tar250,
            "gmi": gmi, "n": n,
        }

    # Episodios de hipoglucemia (lecturas individuales < 70)
    hipos = sorted([r for r in lecturas if r.value_mgdl < 70],
                   key=lambda r: r.timestamp, reverse=True)

    # Distribución por franja horaria
    franjas_def = [
        ("Madrugada", "00–06 h", range(0, 6)),
        ("Mañana",    "06–12 h", range(6, 12)),
        ("Tarde",     "12–18 h", range(12, 18)),
        ("Noche",     "18–24 h", range(18, 24)),
    ]
    franjas = []
    for nombre, label, rng in franjas_def:
        vals = [r.value_mgdl for r in lecturas if r.timestamp.hour in rng]
        if vals:
            tir_f = round(len([v for v in vals if 70 <= v <= 180]) / len(vals) * 100)
            franjas.append({
                "nombre": nombre, "label": label,
                "avg": int(sum(vals)/len(vals)),
                "min": int(min(vals)), "max": int(max(vals)),
                "tir": tir_f, "n": len(vals),
            })
        else:
            franjas.append({"nombre": nombre, "label": label, "sin_datos": True})

    # Insulina
    dosis_all = InsulinDose.query.filter(InsulinDose.timestamp >= desde).all()
    bolus_total = round(sum(d.units for d in dosis_all if d.type == "bolus"), 1)
    basal_total = round(sum(d.units for d in dosis_all if d.type == "basal"), 1)

    # Comidas
    comidas_all = Meal.query.filter(Meal.timestamp >= desde).all()
    carbs_total  = sum(c.carbs_g or 0 for c in comidas_all)

    # Actividad
    acts_all = Activity.query.filter(Activity.timestamp >= desde).all()
    min_total = sum(a.duration_min or 0 for a in acts_all)

    # Charts (sólo TIR y circadiano para el reporte)
    charts = {
        "tir":      chart_time_in_range(days=dias),
        "por_hora": chart_glucose_by_hour(days=dias),
        "timeline": chart_glucose_timeline(hours=dias * 24),
    }

    tabla_comidas, resumen_comidas = _tabla_impacto_comidas(dias)
    ctx = dict(
        dias=dias, desde=desde, hasta=hasta,
        stats=stats, hipos=hipos[:15], franjas=franjas,
        bolus_total=bolus_total, basal_total=basal_total,
        bolus_diario=round(bolus_total / dias, 1),
        basal_diario=round(basal_total / dias, 1),
        n_comidas=len(comidas_all),
        carbs_diario=round(carbs_total / dias, 0) if dias else 0,
        n_actividades=len(acts_all),
        min_actividad=min_total,
        charts=charts,
        tabla_comidas=tabla_comidas,
        resumen_comidas=resumen_comidas,
    )
    return render_template("reporte_semanal.html", **ctx)


@app.route("/reporte-semanal/pdf")
def reporte_semanal_pdf():
    import statistics as _stats
    from flask import make_response

    dias = request.args.get("dias", 14, type=int)
    desde = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=dias)
    hasta = datetime.now(timezone.utc).replace(tzinfo=None)

    lecturas = GlucoseReading.query.filter(
        GlucoseReading.timestamp >= desde
    ).order_by(GlucoseReading.timestamp).all()
    valores = [r.value_mgdl for r in lecturas]

    stats = {}
    if valores:
        n = len(valores)
        mean  = round(sum(valores) / n, 1)
        sd    = round(_stats.stdev(valores), 1) if n > 1 else 0
        cv    = round(sd / mean * 100, 1) if mean > 0 else 0
        tir   = round(len([v for v in valores if 70 <= v <= 180]) / n * 100, 1)
        tbr   = round(len([v for v in valores if v < 70])  / n * 100, 1)
        tbr54 = round(len([v for v in valores if v < 54])  / n * 100, 1)
        tar   = round(len([v for v in valores if v > 180]) / n * 100, 1)
        tar250= round(len([v for v in valores if v > 250]) / n * 100, 1)
        gmi   = round(3.31 + 0.02392 * mean, 1)
        stats = {
            "mean": mean, "sd": sd, "cv": cv,
            "min": int(min(valores)), "max": int(max(valores)),
            "tir": tir, "tbr": tbr, "tbr54": tbr54,
            "tar": tar, "tar250": tar250,
            "gmi": gmi, "n": n,
        }

    hipos = sorted([r for r in lecturas if r.value_mgdl < 70],
                   key=lambda r: r.timestamp, reverse=True)

    franjas_def = [
        ("Madrugada", "00–06 h", range(0, 6)),
        ("Mañana",    "06–12 h", range(6, 12)),
        ("Tarde",     "12–18 h", range(12, 18)),
        ("Noche",     "18–24 h", range(18, 24)),
    ]
    franjas = []
    for nombre, label, rng in franjas_def:
        vals = [r.value_mgdl for r in lecturas if r.timestamp.hour in rng]
        if vals:
            tir_f = round(len([v for v in vals if 70 <= v <= 180]) / len(vals) * 100)
            franjas.append({
                "nombre": nombre, "label": label,
                "avg": int(sum(vals)/len(vals)),
                "min": int(min(vals)), "max": int(max(vals)),
                "tir": tir_f, "n": len(vals),
            })
        else:
            franjas.append({"nombre": nombre, "label": label, "sin_datos": True})

    dosis_all   = InsulinDose.query.filter(InsulinDose.timestamp >= desde).all()
    comidas_all = Meal.query.filter(Meal.timestamp >= desde).all()
    acts_all    = Activity.query.filter(Activity.timestamp >= desde).all()
    bolus_total = round(sum(d.units for d in dosis_all if d.type == "bolus"), 1)
    basal_total = round(sum(d.units for d in dosis_all if d.type == "basal"), 1)
    carbs_total = sum(c.carbs_g or 0 for c in comidas_all)
    min_total   = sum(a.duration_min or 0 for a in acts_all)

    tabla_comidas, resumen_comidas = _tabla_impacto_comidas(dias)

    # Gráficas estáticas con matplotlib
    img_tir      = chart_pdf_tir(valores)
    img_circ     = chart_pdf_circadiano(lecturas)
    img_timeline = chart_pdf_timeline(lecturas)

    html = render_template("reporte_pdf.html",
        dias=dias, desde=desde, hasta=hasta,
        stats=stats, hipos=hipos[:20], franjas=franjas,
        bolus_total=bolus_total, basal_total=basal_total,
        bolus_diario=round(bolus_total / dias, 1),
        basal_diario=round(basal_total / dias, 1),
        n_comidas=len(comidas_all),
        carbs_diario=round(carbs_total / dias, 0) if dias else 0,
        n_actividades=len(acts_all),
        min_actividad=min_total,
        tabla_comidas=tabla_comidas,
        resumen_comidas=resumen_comidas,
        img_tir=img_tir,
        img_circ=img_circ,
        img_timeline=img_timeline,
    )

    ctx = dict(
        dias=dias, desde=desde, hasta=hasta,
        stats=stats, hipos=hipos[:20], franjas=franjas,
        bolus_total=bolus_total, basal_total=basal_total,
        bolus_diario=round(bolus_total / dias, 1),
        basal_diario=round(basal_total / dias, 1),
        n_comidas=len(comidas_all),
        carbs_diario=round(carbs_total / dias, 0) if dias else 0,
        n_actividades=len(acts_all),
        min_actividad=min_total,
        tabla_comidas=tabla_comidas,
        resumen_comidas=resumen_comidas,
        img_tir=img_tir,
        img_circ=img_circ,
        img_timeline=img_timeline,
    )

    pdf_bytes = generar_pdf(ctx)
    nombre_archivo = f"reporte_diabetes_{hasta.strftime('%Y%m%d')}.pdf"
    response = make_response(pdf_bytes)
    response.headers["Content-Type"] = "application/pdf"
    response.headers["Content-Disposition"] = f'attachment; filename="{nombre_archivo}"'
    return response


# ─── Calculadora de corrección ────────────────────────────────────────────────

def _calcular_isf_personal(days=60):
    """
    Estima el Factor de Sensibilidad a la Insulina (ISF) personal.
    Busca bolus sin comida cercana (correcciones puras) y mide la caída de glucosa.
    Retorna (isf_promedio, n_muestras).
    """
    desde = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
    bolus_list = InsulinDose.query.filter(
        InsulinDose.type == "bolus",
        InsulinDose.timestamp >= desde,
    ).all()

    muestras = []
    for d in bolus_list:
        # Ignorar si hay comida cercana (es bolus de comida, no corrección)
        comida = Meal.query.filter(
            Meal.timestamp >= d.timestamp - timedelta(minutes=30),
            Meal.timestamp <= d.timestamp + timedelta(minutes=30),
        ).first()
        if comida:
            continue

        # Glucosa justo antes de la dosis
        pre = (
            GlucoseReading.query
            .filter(
                GlucoseReading.timestamp >= d.timestamp - timedelta(minutes=30),
                GlucoseReading.timestamp <= d.timestamp + timedelta(minutes=15),
            )
            .order_by(GlucoseReading.timestamp.desc())
            .first()
        )

        # Nadir en las 3h siguientes
        posts = (
            GlucoseReading.query
            .filter(
                GlucoseReading.timestamp > d.timestamp + timedelta(minutes=30),
                GlucoseReading.timestamp <= d.timestamp + timedelta(hours=3),
            )
            .all()
        )

        if not pre or not posts or d.units <= 0:
            continue

        nadir = min(r.value_mgdl for r in posts)
        if nadir >= pre.value_mgdl:
            continue   # la glucosa no bajó — no sirve como muestra

        isf = (pre.value_mgdl - nadir) / d.units
        if 10 <= isf <= 200:   # rango fisiológico razonable
            muestras.append(round(isf, 1))

    if len(muestras) >= 2:
        return round(sum(muestras) / len(muestras), 1), len(muestras)
    return None, len(muestras)


def _calcular_icr_personal(days=90):
    """
    Estima el ratio Insulina:Carbohidratos (ICR) personal.
    Busca pares comida+bolus donde la glucemia pre-comida estaba en rango
    para aislar el componente de cobertura de carbohidratos.
    Retorna (icr_promedio, n_muestras).
    """
    desde = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
    isf_personal, _ = _calcular_isf_personal(days=days)

    comidas = Meal.query.filter(
        Meal.timestamp >= desde,
        Meal.carbs_g > 0,
    ).all()

    muestras = []
    for c in comidas:
        if not c.carbs_g or c.carbs_g < 5:
            continue

        # Glucemia justo antes de la comida (ventana -30min a +5min)
        pre = (GlucoseReading.query
               .filter(
                   GlucoseReading.timestamp >= c.timestamp - timedelta(minutes=30),
                   GlucoseReading.timestamp <= c.timestamp + timedelta(minutes=5),
               )
               .order_by(GlucoseReading.timestamp.desc()).first())
        if not pre:
            continue

        # Bolus en ventana -15min a +30min de la comida
        bolus = (InsulinDose.query
                 .filter(
                     InsulinDose.type == "bolus",
                     InsulinDose.timestamp >= c.timestamp - timedelta(minutes=15),
                     InsulinDose.timestamp <= c.timestamp + timedelta(minutes=30),
                 )
                 .order_by(InsulinDose.timestamp).first())
        if not bolus or bolus.units <= 0:
            continue

        # Descontar el componente de corrección del bolus total
        objetivo = float(_get_setting("objetivo", 100))
        isf = isf_personal or 40  # fallback razonable
        correccion = max(0, (pre.value_mgdl - objetivo) / isf)
        bolo_comida = bolus.units - correccion

        if bolo_comida <= 0.2:
            continue

        icr = c.carbs_g / bolo_comida
        if 3 <= icr <= 30:   # rango fisiológico razonable (3–30g CH/U)
            muestras.append(round(icr, 1))

    if len(muestras) >= 3:
        return round(sum(muestras) / len(muestras), 1), len(muestras)
    return None, len(muestras)


@app.route("/api/settings/save", methods=["POST"])
def api_settings_save():
    """Guarda configuración personal (ICR, ISF manual, objetivo)."""
    data = request.get_json() or {}
    guardados = []
    for key in ("icr", "isf_manual", "objetivo"):
        val = data.get(key)
        if val is not None and val != "":
            try:
                fval = float(val)
                if fval > 0:
                    _set_setting(key, fval)
                    guardados.append(key)
            except (ValueError, TypeError):
                pass
    return jsonify({"ok": True, "guardados": guardados})


@app.route("/calculadora")
def calculadora():
    isf_personal, n_isf   = _calcular_isf_personal()
    icr_personal, n_icr   = _calcular_icr_personal()
    ultima = GlucoseReading.query.order_by(GlucoseReading.timestamp.desc()).first()

    # Configuración guardada por el usuario
    icr_guardado      = _get_setting("icr")
    isf_manual_guard  = _get_setting("isf_manual")
    objetivo_guardado = _get_setting("objetivo", "100")

    return render_template("calculadora.html",
        isf_personal=isf_personal,
        n_isf=n_isf,
        icr_personal=icr_personal,
        n_icr=n_icr,
        icr_guardado=icr_guardado,
        isf_manual_guardado=isf_manual_guard,
        objetivo_guardado=objetivo_guardado,
        ultima=ultima,
    )


@app.route("/api/calculadora/correccion")
def api_calculadora_correccion():
    glucemia   = request.args.get("glucemia",  type=float)
    objetivo   = request.args.get("objetivo",  type=float)
    isf_manual = request.args.get("isf",       type=float)
    carbs      = request.args.get("carbs",     type=float)
    icr_manual = request.args.get("icr",       type=float)

    # Usar configuración guardada como fallback
    if objetivo is None:
        objetivo = float(_get_setting("objetivo", 100))

    isf_personal, n_isf = _calcular_isf_personal()
    icr_personal, n_icr = _calcular_icr_personal()

    isf = isf_manual or (float(_get_setting("isf_manual")) if _get_setting("isf_manual") else None) or isf_personal
    icr = icr_manual or (float(_get_setting("icr")) if _get_setting("icr") else None) or icr_personal

    if not glucemia or glucemia <= 0:
        return jsonify({"error": "Glucemia inválida"})
    if not isf:
        return jsonify({"error": "Sin ISF — ingresalo manualmente en Configuración"})

    # ── Componente de corrección ──────────────────────────────────────────────
    if glucemia > objetivo:
        correccion_exacta = (glucemia - objetivo) / isf
    else:
        correccion_exacta = 0.0   # glucemia baja: no corregir

    # ── Componente de comida ──────────────────────────────────────────────────
    bolo_comida_exacto = 0.0
    if carbs and carbs > 0:
        if not icr:
            return jsonify({"error": "Sin I:CH — ingresalo en Configuración"})
        bolo_comida_exacto = carbs / icr

    # ── Total ─────────────────────────────────────────────────────────────────
    total_exacto    = correccion_exacta + bolo_comida_exacto
    total_redondeado = round(total_exacto * 2) / 2  # redondear a 0.5U

    resultado_esperado = round(glucemia - correccion_exacta * isf, 0)

    return jsonify({
        # Totales
        "total_exacto":     round(total_exacto, 2),
        "total_sugerido":   total_redondeado,
        # Componentes
        "correccion_exacta":     round(correccion_exacta, 2),
        "correccion_redondeada": round(round(correccion_exacta * 2) / 2, 1),
        "bolo_comida_exacto":    round(bolo_comida_exacto, 2),
        # Parámetros usados
        "glucemia":    glucemia,
        "objetivo":    objetivo,
        "isf":         isf,
        "icr":         icr,
        "carbs":       carbs or 0,
        "n_isf":       n_isf,
        "n_icr":       n_icr,
        "fuente_isf":  "manual" if isf_manual else ("guardado" if _get_setting("isf_manual") else "calculado"),
        "fuente_icr":  "manual" if icr_manual else ("guardado" if _get_setting("icr") else "calculado"),
        "resultado_esperado": resultado_esperado,
    })


# ─── Quick Log ───────────────────────────────────────────────────────────────

@app.route("/quicklog", methods=["GET", "POST"])
def quicklog():
    if request.method == "POST":
        fecha = request.form.get("fecha")
        hora  = request.form.get("hora")
        ts    = parse_datetime(fecha, hora)
        guardados = []

        # Glucemia
        if request.form.get("reg_glucemia"):
            valor = request.form.get("glucemia_valor", type=float)
            if valor and valor > 0:
                db.session.add(GlucoseReading(
                    timestamp=ts,
                    value_mgdl=valor,
                    source="manual",
                    notes=request.form.get("glucemia_notas", ""),
                ))
                guardados.append(f"Glucemia {int(valor)} mg/dL")

        # Comida
        if request.form.get("reg_comida"):
            nombre = request.form.get("comida_nombre", "").strip()
            if nombre:
                carbs = request.form.get("comida_carbs", 0, type=float)
                comida = Meal(
                    timestamp=ts,
                    name=nombre,
                    carbs_g=carbs,
                    fat_g=request.form.get("comida_fat", 0, type=float),
                    protein_g=request.form.get("comida_protein", 0, type=float),
                    calories=request.form.get("comida_cal", 0, type=float),
                    notes="",
                    categoria=_auto_categorizar(nombre),
                )
                db.session.add(comida)
                food_id = request.form.get("food_item_id", type=int)
                if food_id:
                    fi = FoodItem.query.get(food_id)
                    if fi:
                        fi.times_used += 1
                guardados.append(f"Comida {nombre} ({int(carbs)}g CH)")

        # Insulina bolus
        if request.form.get("reg_insulina"):
            units = request.form.get("insulina_units", type=float)
            if units and units > 0:
                db.session.add(InsulinDose(
                    timestamp=ts,
                    type="bolus",
                    units=units,
                    brand=request.form.get("insulina_brand", "").strip(),
                    notes="",
                ))
                guardados.append(f"Insulina {units}U bolus")

        if guardados:
            db.session.commit()
            flash("✓ Registrado: " + " · ".join(guardados), "success")
        else:
            flash("No se seleccionó ningún dato para registrar.", "warning")

        return redirect(url_for("dashboard"))

    ahora = datetime.now()
    ultima_glucemia = GlucoseReading.query.order_by(GlucoseReading.timestamp.desc()).first()
    return render_template(
        "quicklog.html",
        fecha=ahora.strftime("%Y-%m-%d"),
        hora=ahora.strftime("%H:%M"),
        ultima_glucemia=ultima_glucemia,
    )


# ─── Recomendaciones ──────────────────────────────────────────────────────────

@app.route("/recomendaciones")
def recomendaciones():
    dias = request.args.get("dias", 30, type=int)
    recs = generate_recommendations(days=dias)
    return render_template("recomendaciones.html", recs=recs, dias=dias)


# ─── Edición de registros ─────────────────────────────────────────────────────

@app.route("/glucemia/<int:id>/editar", methods=["GET", "POST"])
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


@app.route("/comidas/<int:id>/editar", methods=["GET", "POST"])
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
        return redirect(url_for("comidas"))

    return render_template("comida_form.html",
                           editar=True, item=comida,
                           fecha=comida.timestamp.strftime("%Y-%m-%d"),
                           hora=comida.timestamp.strftime("%H:%M"))


@app.route("/insulina/<int:id>/editar", methods=["GET", "POST"])
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


@app.route("/actividad/<int:id>/editar", methods=["GET", "POST"])
def actividad_editar(id):
    act = Activity.query.get_or_404(id)
    if request.method == "POST":
        act.timestamp     = parse_datetime(request.form["fecha"], request.form["hora"])
        act.activity_type = request.form.get("activity_type", act.activity_type).strip()
        act.duration_min  = request.form.get("duration_min", type=int)
        act.intensity     = request.form.get("intensity", act.intensity)
        act.notes         = request.form.get("notas", "")
        db.session.commit()
        flash("Actividad actualizada.", "success")
        return redirect(url_for("actividad"))
    return render_template("actividad_form.html",
                           editar=True, item=act,
                           fecha=act.timestamp.strftime("%Y-%m-%d"),
                           hora=act.timestamp.strftime("%H:%M"),
                           actividades_comunes=ACTIVIDADES_COMUNES)


if __name__ == "__main__":
    import socket
    hostname = socket.gethostname()
    local_ip = socket.gethostbyname(hostname)
    print(f"\n  App corriendo en:")
    print(f"  → Computadora : http://localhost:5050")
    print(f"  → Celular/Red  : http://{local_ip}:5050")
    print(f"  (ambos dispositivos deben estar en la misma red WiFi)\n")
    app.run(debug=True, host="0.0.0.0", port=5050)
