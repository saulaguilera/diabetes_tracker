from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from werkzeug.security import check_password_hash
import os

from helpers import stats_resumen, _detectar_patrones, _get_setting
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
            next_url = request.args.get("next") or url_for("dashboard")
            return redirect(next_url)
        else:
            error = "Usuario o contraseña incorrectos."

    return render_template("login.html", error=error)


@bp.route("/logout", endpoint="logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


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

    return render_template("dashboard.html", stats=stats, chart=chart, alertas=alertas,
                           libre_configured=libre_configured, ultima_sync=ultima_sync)
