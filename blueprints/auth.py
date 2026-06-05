from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from werkzeug.security import check_password_hash
import os

from helpers import stats_resumen, _detectar_patrones, _get_setting, _dashboard_insights
from utils.charts import chart_glucose_timeline

bp = Blueprint("auth", __name__)

_APP_USER     = os.environ.get("APP_USERNAME", "admin")
_APP_PASSWORD = os.environ.get("APP_PASSWORD", "")

# Hash is generated lazily to avoid import-time side effects
_PASS_HASH = None

def _get_pass_hash():
    global _PASS_HASH
    if _PASS_HASH is None and _APP_PASSWORD:
        from werkzeug.security import generate_password_hash
        _PASS_HASH = generate_password_hash(_APP_PASSWORD)
    return _PASS_HASH

_LIBRE_EMAIL    = os.environ.get("LIBRE_EMAIL", "")
_LIBRE_PASSWORD = os.environ.get("LIBRE_PASSWORD", "")


@bp.route("/login", methods=["GET", "POST"], endpoint="login")
def login():
    if session.get("logged_in"):
        return redirect(url_for("dashboard"))

    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        if not _APP_PASSWORD:
            error = "El servidor no tiene contraseña configurada. Definí APP_PASSWORD."
        elif (username == _APP_USER and
              check_password_hash(_get_pass_hash(), password)):
            session.permanent = True
            session["logged_in"] = True
            session["username"] = username
            # next puede venir por query (?next=) o por el campo oculto del form.
            # Solo se aceptan rutas internas (que empiezan con "/") por seguridad.
            next_url = request.form.get("next") or request.args.get("next") or ""
            if not next_url.startswith("/"):
                next_url = url_for("dashboard")
            return redirect(next_url)
        else:
            error = "Usuario o contraseña incorrectos."

    return render_template("login.html", error=error)


@bp.route("/logout", endpoint="logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


def _hace(ts):
    """Devuelve 'hace Xmin', 'hace Xh', 'ayer HH:MM', etc. para un timestamp."""
    if not ts:
        return None
    from datetime import datetime
    delta = datetime.now() - ts
    mins  = int(delta.total_seconds() / 60)
    if mins < 1:
        return "ahora"
    if mins < 60:
        return f"hace {mins}min"
    if mins < 1440:
        return f"hace {mins // 60}h"
    return f"hace {mins // 1440}d"


@bp.route("/", endpoint="dashboard")
def dashboard():
    from datetime import datetime
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

    insights = _dashboard_insights()

    # ── PMM: señales del modelo metabólico personal ───────────────────────
    pmm_data = {}
    try:
        from datetime import datetime as _dt
        from pmm.engines.drift import get_drift_status
        from pmm.engines.anomaly import compute_anomaly_score
        from pmm.core.parameter_store import get_isf_now, get_icr_now
        hora = _dt.now().hour
        pmm_data = {
            "drift":   get_drift_status(),
            "anomaly": compute_anomaly_score(),
            "isf":     get_isf_now(hora=hora),
            "icr":     get_icr_now(hora=hora),
        }
    except Exception:
        pass

    # Feed de actividad reciente — lista ordenada de últimos eventos
    feed = []
    ul   = stats.get("ultima_lectura")
    uc   = stats.get("ultima_comida")
    ui   = stats.get("ultima_insulina")

    if ul:
        v = int(ul.value_mgdl)
        estado = "hipo" if v < 70 else "hiper" if v > 180 else "rango"
        feed.append({
            "tipo": "glucosa", "icono": "bi-droplet-fill", "color": "primary",
            "valor": f"{v} mg/dL", "estado": estado,
            "meta": ul.source,
            "hace": _hace(ul.timestamp), "ts": ul.timestamp,
        })
    if uc:
        feed.append({
            "tipo": "comida", "icono": "bi-egg-fried", "color": "success",
            "valor": uc.name, "estado": "rango",
            "meta": f"{int(uc.carbs_g)}g CH" if uc.carbs_g else "",
            "hace": _hace(uc.timestamp), "ts": uc.timestamp,
        })
    if ui:
        tipo_label = {"bolus": "Bolus", "basal": "Basal"}.get(ui.type, ui.type.capitalize())
        feed.append({
            "tipo": "insulina", "icono": "bi-capsule-pill", "color": "warning",
            "valor": f"{ui.units}U {tipo_label}",
            "estado": "rango",
            "meta": ui.brand or ui.purpose or "",
            "hace": _hace(ui.timestamp), "ts": ui.timestamp,
        })

    # Ordenar por timestamp desc
    feed.sort(key=lambda x: x["ts"], reverse=True)

    return render_template("dashboard.html", stats=stats, chart=chart, alertas=alertas,
                           libre_configured=libre_configured, ultima_sync=ultima_sync,
                           insights=insights, feed=feed, pmm=pmm_data)
