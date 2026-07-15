"""
blueprints/admin_bp.py — observabilidad operativa.

    GET /healthz        → latido público para UptimeRobot: 200 si la BD
                          responde Y el scheduler corrió hace < 12 min;
                          503 si algo está caído. Sin datos personales.
    GET /admin/estado   → panel para el operador (solo usuario 1): último
                          sync por usuario, lecturas recientes, resultado
                          del último push de Drive, tokens registrados,
                          backup y latido del scheduler.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from flask import Blueprint, jsonify, render_template, session

from models import db, User, GlucoseReading

bp = Blueprint("admin", __name__)

_SCHED_FRESH_MIN = 12   # el sync corre cada 5 min; 12 = dos ciclos de gracia


def _sched_age_s():
    from helpers import _get_setting
    raw = _get_setting("sched_last_run")
    if not raw:
        return None
    try:
        return int((datetime.now() - datetime.fromisoformat(raw)).total_seconds())
    except Exception:
        return None


@bp.route("/healthz", endpoint="healthz")
def healthz():
    """Latido para el monitor externo. Público a propósito: no expone datos,
    solo si el servicio está vivo (BD + scheduler)."""
    db_ok = True
    try:
        db.session.execute(db.text("SELECT 1"))
    except Exception:
        db_ok = False
    age = _sched_age_s()
    sched_ok = age is not None and age < _SCHED_FRESH_MIN * 60
    ok = db_ok and sched_ok
    return jsonify({"ok": ok, "db": db_ok, "scheduler_age_s": age}), (200 if ok else 503)


@bp.route("/admin/estado", endpoint="admin_estado")
def admin_estado():
    """Panel del operador. Gateado al usuario 1 (dueño de la instancia)."""
    if session.get("user_id") != 1:
        return ("<div style='font-family:system-ui;background:#0B1324;color:#EAF2F8;"
                "min-height:100vh;display:grid;place-items:center;text-align:center;padding:24px'>"
                "<div><div style='font-size:34px;margin-bottom:12px'>🔒</div>"
                "<div style='font-size:17px;margin-bottom:6px'>Este panel es solo del operador</div>"
                "<div style='font-size:14px;opacity:.6'>Estás con la sesión de otra cuenta. "
                "<a href='/logout' style='color:#22D3EE'>Cierra sesión</a> y entra con tu "
                "usuario principal.</div></div></div>"), 403

    from helpers import set_user_context, reset_user_context, _get_setting

    ahora = datetime.now()
    usuarios = db.session.execute(
        db.select(User), execution_options={"all_users": True}).scalars().all()

    filas = []
    for u in usuarios:
        tok = set_user_context(u.id)
        try:
            ult = (GlucoseReading.query
                   .order_by(GlucoseReading.timestamp.desc()).first())
            n24 = (GlucoseReading.query
                   .filter(GlucoseReading.timestamp >= ahora - timedelta(hours=24))
                   .count())

            def _j(key):
                raw = _get_setting(key)
                try:
                    return json.loads(raw) if raw else None
                except Exception:
                    return None

            # conexión REAL del sensor: la columna de credenciales O el
            # fallback de entorno del usuario 1 (su config histórica vive en
            # Railway, no en la fila — el panel decía "no conectado" mintiendo)
            try:
                from blueprints.sync import _cgm_config_for_user
                _prov, _email, _ = _cgm_config_for_user(u)
                _conectado = bool(_email)
            except Exception:
                _prov = getattr(u, "cgm_provider", None) or "—"
                _conectado = bool(getattr(u, "libre_email_enc", None))
            filas.append({
                "usuario": u.username,
                "registrado": u.created_at.strftime("%d/%m/%Y") if u.created_at else "—",
                "provider": _prov or "—",
                "sensor": _conectado,
                "ultima_lectura": ult.timestamp.strftime("%d/%m %H:%M") if ult else None,
                "lectura_hace_min": int((ahora - ult.timestamp).total_seconds() // 60) if ult else None,
                "lecturas_24h": n24,
                "sync_last": _j("sync_last"),
                "drive_push": _j("drive_push_last"),
                "token_apns": bool(_get_setting("app_apns_token")),
                "token_fcm": bool(_get_setting("app_fcm_token")),
            })
        finally:
            reset_user_context(tok)

    from helpers import _get_setting as _gs
    globales = {
        "sched_last_run": _gs("sched_last_run"),
        "sched_age_s": _sched_age_s(),
        "backup_last": _gs("backup_last"),
        "generado": ahora.strftime("%d/%m/%Y %H:%M:%S"),
    }
    return render_template("admin_estado.html", filas=filas, g=globales)
