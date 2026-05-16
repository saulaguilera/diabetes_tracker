import os
from datetime import datetime, timedelta
from flask import Blueprint, jsonify, request, redirect, url_for, flash, session
from models import db, GlucoseReading, MealComponent
from helpers import _get_setting, _set_setting
from utils.libre_linkup import sync_all as libre_sync_all

bp = Blueprint("sync", __name__)
# Este blueprint está exento de CSRF: recibe llamadas de cron externos
# (autenticados por SYNC_TOKEN) y APIs JSON del navegador.

_LIBRE_EMAIL    = os.environ.get("LIBRE_EMAIL", "")
_LIBRE_PASSWORD = os.environ.get("LIBRE_PASSWORD", "")
_SYNC_TOKEN     = os.environ.get("SYNC_TOKEN", "")


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
        # Verificar si ya existe (ventana ±6 min para evitar duplicados,
        # incluye desfases por cambios de timezone anteriores)
        ts = r["timestamp"]
        existe = GlucoseReading.query.filter(
            GlucoseReading.timestamp >= ts - timedelta(minutes=6),
            GlucoseReading.timestamp <= ts + timedelta(minutes=6),
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


@bp.route("/api/ultima-lectura", endpoint="api_ultima_lectura")
def api_ultima_lectura():
    """Mini-widget sidebar: última lectura de glucosa."""
    r = GlucoseReading.query.order_by(GlucoseReading.timestamp.desc()).first()
    if not r:
        return jsonify({"value": None})
    # Devolver el timestamp en ISO para que el cliente calcule el time_ago
    # en su propia zona horaria (evita desfase UTC vs hora local del servidor)
    return jsonify({
        "value":     int(r.value_mgdl),
        "trend":     r.notes if r.notes in ["↑↑","↑","↗","→","↘","↓","↓↓"] else "→",
        "timestamp": r.timestamp.isoformat(),
    })


@bp.route("/api/sync/libre/reset", endpoint="api_sync_libre_reset")
def api_sync_libre_reset():
    """Borra el caché de token para forzar un login fresco."""
    if not session.get("logged_in"):
        return jsonify({"error": "No autorizado"}), 401
    for key in ("libre_token", "libre_base_url", "libre_token_expiry",
                "libre_account_id", "libre_last_sync"):
        _set_setting(key, "")
    return jsonify({"ok": True, "mensaje": "Caché borrado. Apretá ↺ para hacer login fresco."})


@bp.route("/api/backfill-fiber-gi", methods=["POST"], endpoint="api_backfill_fiber_gi")
def api_backfill_fiber_gi():
    """
    Backfill one-shot: asigna fibra e ÍG a todos los componentes de comida
    ya guardados que tienen estos campos vacíos.

    - fiber_g: se obtiene de la base nutricional interna (nutrition_db)
    - glycemic_index: se obtiene de GI_DB (nutrition_db)

    Idempotente: solo actualiza componentes donde falta al menos uno de los
    dos valores. No sobreescribe datos ingresados manualmente.
    """
    from utils.nutrition_db import get_gi, estimar

    # Componentes candidatos: sin ÍG O con fibra en 0
    candidatos = MealComponent.query.filter(
        db.or_(
            MealComponent.glycemic_index == None,
            MealComponent.fiber_g == 0,
        )
    ).all()

    gi_updated    = 0
    fiber_updated = 0

    for comp in candidatos:
        nombre = comp.name.strip()
        if not nombre:
            continue

        # ── Índice Glucémico ─────────────────────────────────────────────
        if comp.glycemic_index is None:
            gi = get_gi(nombre)
            if gi is not None:
                comp.glycemic_index = gi
                gi_updated += 1

        # ── Fibra ────────────────────────────────────────────────────────
        if (comp.fiber_g or 0) == 0:
            estimado = estimar(nombre, carbs_usuario=comp.carbs_g or 0)
            if estimado and estimado.get("fibra_g", 0) > 0:
                comp.fiber_g = estimado["fibra_g"]
                fiber_updated += 1

    if gi_updated or fiber_updated:
        db.session.commit()

    return jsonify({
        "candidatos":    len(candidatos),
        "gi_updated":    gi_updated,
        "fiber_updated": fiber_updated,
        "ok": True,
    })


@bp.route("/api/sync/libre/debug", endpoint="api_sync_libre_debug")
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


@bp.route("/api/sync/libre", endpoint="api_sync_libre")
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


@bp.route("/sync/libre", endpoint="sync_libre_manual")
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
