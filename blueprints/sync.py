import math as _math
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

        # Actualizar filtro de Kalman con las nuevas lecturas (orden cronológico)
        try:
            from utils.kalman import update_with_reading as kalman_update
            nuevas_ord = GlucoseReading.query.filter(
                GlucoseReading.timestamp >= datetime.now() - timedelta(hours=2)
            ).order_by(GlucoseReading.timestamp).all()
            for r in nuevas_ord:
                kalman_update(r.value_mgdl, r.timestamp, save=False)
            # Guardar una sola vez al final (más eficiente)
            if nuevas_ord:
                kalman_update(nuevas_ord[-1].value_mgdl, nuevas_ord[-1].timestamp, save=True)
        except Exception:
            pass

        # Resolver predicciones pendientes con las nuevas lecturas
        try:
            from utils.prediction_feedback import resolve_predictions
            resolve_predictions(nuevas_ord if insertadas else [])
        except Exception:
            pass

        # Reajustar modelo AR si no se ha entrenado en las últimas 6h
        # (lazy: solo cuando llegan datos nuevos, máx 1 vez cada 6h)
        try:
            from utils.ar_model import maybe_fit_ar_model
            maybe_fit_ar_model()
        except Exception:
            pass

        # Re-estimar magnitud del fenómeno del alba (máx 1 vez cada 24h)
        # Requiere datos CGM nocturnos suficientes (≥ 45 días de historia)
        try:
            dawn_last = _get_setting("dawn_last_estimated")
            needs_dawn = True
            if dawn_last:
                hours_since = (datetime.now() - datetime.fromisoformat(dawn_last)).total_seconds() / 3600
                needs_dawn  = hours_since >= 24
            if needs_dawn:
                from utils.kinetics import estimate_dawn_magnitude
                result = estimate_dawn_magnitude(days=45)
                if result.get("ok"):
                    _set_setting("dawn_last_estimated", datetime.now().isoformat())
        except Exception:
            pass

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


@bp.route("/api/resumen-dia", endpoint="api_resumen_dia")
def api_resumen_dia():
    """
    Resumen del día de hoy (desde medianoche) para el dashboard.
    Incluye: TIR, glucemia promedio/min/max, insulina total, CH totales,
    número de correcciones, hipos y hipers.
    """
    from datetime import datetime, timedelta
    from models import GlucoseReading, InsulinDose, Meal

    now   = datetime.now()
    hoy   = now.replace(hour=0, minute=0, second=0, microsecond=0)
    ayer  = hoy - timedelta(days=1)

    # ── Glucemia de hoy ──────────────────────────────────────────────────────
    lecturas = GlucoseReading.query.filter(
        GlucoseReading.timestamp >= hoy
    ).all()
    vals = [r.value_mgdl for r in lecturas]

    tir = hipo_pct = hiper_pct = promedio = g_min = g_max = None
    n_hipos = n_hipers = 0
    if vals:
        n      = len(vals)
        tir    = round(len([v for v in vals if 70 <= v <= 180]) / n * 100, 1)
        hipo_pct  = round(len([v for v in vals if v < 70])  / n * 100, 1)
        hiper_pct = round(len([v for v in vals if v > 180]) / n * 100, 1)
        promedio  = round(sum(vals) / n, 0)
        g_min     = round(min(vals), 0)
        g_max     = round(max(vals), 0)
        # Eventos: contar rachas, no lecturas individuales
        en_hipo = en_hiper = False
        for v in vals:
            if v < 70:
                if not en_hipo: n_hipos += 1
                en_hipo = True; en_hiper = False
            elif v > 180:
                if not en_hiper: n_hipers += 1
                en_hiper = True; en_hipo = False
            else:
                en_hipo = en_hiper = False

    # ── Insulina de hoy ──────────────────────────────────────────────────────
    dosis_hoy = InsulinDose.query.filter(
        InsulinDose.timestamp >= hoy
    ).all()
    bolus_u   = round(sum(d.units for d in dosis_hoy if d.type == "bolus"), 1)
    basal_u   = round(sum(d.units for d in dosis_hoy if d.type == "basal"), 1)
    total_u   = round(bolus_u + basal_u, 1)
    n_correc  = sum(1 for d in dosis_hoy
                    if d.type == "bolus" and d.purpose == "correccion")
    n_bolus   = sum(1 for d in dosis_hoy if d.type == "bolus")

    # ── Comidas de hoy ───────────────────────────────────────────────────────
    comidas_hoy = Meal.query.filter(Meal.timestamp >= hoy).all()
    carbs_hoy   = round(sum(m.carbs_g or 0 for m in comidas_hoy), 0)
    n_comidas   = len(comidas_hoy)

    # ── TIR ayer (para comparar) ─────────────────────────────────────────────
    vals_ayer = [r.value_mgdl for r in GlucoseReading.query.filter(
        GlucoseReading.timestamp >= ayer,
        GlucoseReading.timestamp <  hoy,
    ).all()]
    tir_ayer = round(len([v for v in vals_ayer if 70 <= v <= 180]) / len(vals_ayer) * 100, 1) \
               if vals_ayer else None

    return jsonify({
        "ok": True,
        "fecha": hoy.strftime("%d/%m/%Y"),
        "lecturas_n": len(vals),
        # Glucemia
        "tir":       tir,
        "tir_ayer":  tir_ayer,
        "hipo_pct":  hipo_pct,
        "hiper_pct": hiper_pct,
        "promedio":  promedio,
        "g_min":     g_min,
        "g_max":     g_max,
        "n_hipos":   n_hipos,
        "n_hipers":  n_hipers,
        # Insulina
        "bolus_u":   bolus_u,
        "basal_u":   basal_u,
        "total_u":   total_u,
        "n_bolus":   n_bolus,
        "n_correc":  n_correc,
        # Comidas
        "carbs_g":   carbs_hoy,
        "n_comidas": n_comidas,
    })


@bp.route("/api/sync/libre/reset", endpoint="api_sync_libre_reset")
def api_sync_libre_reset():
    """Borra el caché de token para forzar un login fresco."""
    if not session.get("logged_in"):
        return jsonify({"error": "No autorizado"}), 401
    for key in ("libre_token", "libre_base_url", "libre_token_expiry",
                "libre_account_id", "libre_last_sync", "libre_rate_limited_at"):
        _set_setting(key, "")
    return jsonify({"ok": True, "mensaje": "Caché borrado. Apretá ↺ para hacer login fresco."})


@bp.route("/api/sync/status", endpoint="api_sync_status")
def api_sync_status():
    """
    Estado actual del sistema de sync — sin llamar a Abbott.
    Diagnóstico rápido para entender por qué no llegan lecturas.
    """
    if not session.get("logged_in"):
        return jsonify({"error": "No autorizado"}), 401

    from models import GlucoseReading

    now           = datetime.now()
    last_sync_str = _get_setting("libre_last_sync")
    rl_at_str     = _get_setting("libre_rate_limited_at")
    token         = _get_setting("libre_token")
    base_url      = _get_setting("libre_base_url")

    ultima_lectura = GlucoseReading.query.order_by(
        GlucoseReading.timestamp.desc()
    ).first()

    # Calcular minutos desde última sync y última lectura
    mins_desde_sync    = None
    mins_desde_lectura = None
    if last_sync_str:
        try:
            mins_desde_sync = round((now - datetime.fromisoformat(last_sync_str)).total_seconds() / 60, 1)
        except Exception:
            pass
    if ultima_lectura:
        mins_desde_lectura = round((now - ultima_lectura.timestamp).total_seconds() / 60, 1)

    # Estado del rate-limit
    rate_limit_activo = False
    rate_limit_wait   = 0
    if rl_at_str:
        try:
            rl_secs = (now - datetime.fromisoformat(rl_at_str)).total_seconds()
            if rl_secs < 600:   # 10 min
                rate_limit_activo = True
                rate_limit_wait   = int(600 - rl_secs)
        except Exception:
            pass

    return jsonify({
        "ahora":              now.strftime("%H:%M:%S"),
        "token_presente":     bool(token),
        "base_url":           base_url or "(no configurada)",
        "ultima_sync":        last_sync_str,
        "mins_desde_sync":    mins_desde_sync,
        "rate_limit_activo":  rate_limit_activo,
        "rate_limit_wait_s":  rate_limit_wait,
        "ultima_lectura_db":  ultima_lectura.timestamp.strftime("%H:%M %d/%m") if ultima_lectura else None,
        "ultima_lectura_val": ultima_lectura.value_mgdl if ultima_lectura else None,
        "mins_desde_lectura": mins_desde_lectura,
        "diagnostico": (
            "⚠️ Rate-limit Abbott activo"                 if rate_limit_activo else
            "⚠️ Sin token — necesita login"               if not token else
            f"⚠️ Sin lecturas hace {mins_desde_lectura:.0f} min — sync puede estar fallando"
                                                          if mins_desde_lectura and mins_desde_lectura > 20 else
            "✓ OK"
        ),
    })


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


@bp.route("/api/sync/libre/verbose", endpoint="api_sync_libre_verbose")
def api_sync_libre_verbose():
    """
    Diagnóstico completo del pipeline de sync — SIN insertar datos.
    Muestra exactamente qué devuelve Abbott y qué pasaría con cada lectura.
    Útil para diagnosticar "el sensor funciona pero no llegan lecturas a la app".
    """
    if not session.get("logged_in"):
        return jsonify({"error": "No autorizado"}), 401

    import requests as _req
    from utils.libre_linkup import (
        _decode_jwt_account_id, _hash_account_id,
        get_connections, get_readings,
    )

    token      = _get_setting("libre_token")
    base_url   = _get_setting("libre_base_url")
    account_id = _get_setting("libre_account_id") or ""

    if not token or not base_url:
        return jsonify({
            "estado": "sin_token",
            "mensaje": "No hay token cacheado. Apretá ↺ para hacer login.",
        })

    now = datetime.now()

    try:
        # Re-computar account_id desde el JWT (igual que sync_all)
        raw_id = _decode_jwt_account_id(token)
        if raw_id:
            account_id = _hash_account_id(raw_id)

        # ── 1. Connections ────────────────────────────────────────────────
        connections = get_connections(token, base_url, account_id)
        if not connections:
            return jsonify({
                "estado":      "sin_conexiones",
                "connections": [],
                "mensaje":     "Abbott devolvió 0 conexiones. El sensor no está vinculado en LibreLinkUp.",
            })

        patient    = connections[0]
        patient_id = patient.get("patientId") or patient.get("id")

        # ── 2. Lecturas raw del sensor ────────────────────────────────────
        readings = get_readings(token, base_url, patient_id, account_id)

        if not readings:
            return jsonify({
                "estado":      "sin_lecturas_abbott",
                "patient_id":  patient_id,
                "connections": len(connections),
                "mensaje":     "Abbott devolvió la lista de conexiones pero 0 lecturas en /graph. "
                               "Puede que el sensor no haya sido escaneado recientemente.",
            })

        # ── 3. Clasificar cada lectura: nueva vs dedup ────────────────────
        detalle = []
        nuevas  = 0
        dedup   = 0
        invalidas = 0

        # Última lectura en DB para contexto
        ultima_db = GlucoseReading.query.order_by(
            GlucoseReading.timestamp.desc()
        ).first()

        for r in readings:
            ts  = r["timestamp"]
            val = r["value_mgdl"]

            if not val or val < 20:
                invalidas += 1
                detalle.append({
                    "ts":     ts.strftime("%H:%M:%S %d/%m"),
                    "valor":  val,
                    "estado": "invalida",
                    "nota":   "valor < 20 mg/dL, descartada",
                })
                continue

            existe = GlucoseReading.query.filter(
                GlucoseReading.timestamp >= ts - timedelta(minutes=6),
                GlucoseReading.timestamp <= ts + timedelta(minutes=6),
            ).first()

            if existe:
                dedup += 1
                diff_min = round((ts - existe.timestamp).total_seconds() / 60, 1)
                detalle.append({
                    "ts":        ts.strftime("%H:%M:%S %d/%m"),
                    "valor":     val,
                    "estado":    "duplicada",
                    "db_ts":     existe.timestamp.strftime("%H:%M:%S %d/%m"),
                    "db_valor":  existe.value_mgdl,
                    "diff_min":  diff_min,
                })
            else:
                nuevas += 1
                detalle.append({
                    "ts":     ts.strftime("%H:%M:%S %d/%m"),
                    "valor":  val,
                    "estado": "nueva",
                    "trend":  r.get("trend", "?"),
                })

        # ── 4. Diagnóstico final ──────────────────────────────────────────
        if nuevas > 0:
            diagnostico = f"✓ {nuevas} lectura(s) nueva(s) — deberían insertarse al hacer sync real"
        elif dedup == len(readings):
            # Todas duplicadas — posible problema de timezone?
            # Comparar timestamp de Abbott vs DB
            primera_abbott = readings[0]["timestamp"]
            if ultima_db:
                diff_db_abbott = (ultima_db.timestamp - primera_abbott).total_seconds() / 60
                if abs(diff_db_abbott) > 60:
                    diagnostico = (f"⚠️ Todas duplicadas — posible desfase de timezone: "
                                   f"DB={ultima_db.timestamp.strftime('%H:%M')} "
                                   f"Abbott={primera_abbott.strftime('%H:%M')} "
                                   f"(diff={diff_db_abbott:.0f} min)")
                else:
                    diagnostico = ("⚠️ Todas las lecturas ya están en la DB — "
                                   "Abbott no ha enviado datos nuevos desde el último scan en LibreLink")
            else:
                diagnostico = "⚠️ Todas duplicadas y no hay lecturas en DB"
        else:
            diagnostico = f"Sin lecturas válidas nuevas ({invalidas} inválidas, {dedup} duplicadas)"

        return jsonify({
            "ok":              True,
            "timestamp":       now.strftime("%H:%M:%S"),
            "estado":          "ok",
            "patient_id":      patient_id,
            "connections":     len(connections),
            "abbott_total":    len(readings),
            "nuevas":          nuevas,
            "duplicadas":      dedup,
            "invalidas":       invalidas,
            "ultima_db_ts":    ultima_db.timestamp.strftime("%H:%M:%S %d/%m") if ultima_db else None,
            "ultima_db_val":   ultima_db.value_mgdl if ultima_db else None,
            "ultima_abbott_ts": readings[-1]["timestamp"].strftime("%H:%M:%S %d/%m") if readings else None,
            "ultima_abbott_val": readings[-1]["value_mgdl"] if readings else None,
            "diagnostico":     diagnostico,
            "detalle":         detalle,
        })

    except Exception as e:
        import traceback
        return jsonify({
            "ok":    False,
            "error": str(e),
            "trace": traceback.format_exc(),
        })


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

    # ── Cooldown: no llamar a Abbott más seguido de cada 4 min ──────────────
    # El botón manual (?force=1) siempre bypasea el cooldown local.
    # El rate-limit de Abbott (429) se respeta siempre.
    _COOLDOWN_MIN  = 4    # mínimo entre syncs automáticas
    _RATELIMIT_MIN = 10   # espera tras un 429 de Abbott (10 min)

    is_manual = request.args.get("force") == "1"
    now       = datetime.now()

    # Rate-limit de Abbott: se respeta incluso en sync manual
    rl_at_str = _get_setting("libre_rate_limited_at")
    if rl_at_str:
        try:
            rl_at         = datetime.fromisoformat(rl_at_str)
            secs_since_rl = (now - rl_at).total_seconds()
            if secs_since_rl < _RATELIMIT_MIN * 60:
                wait = int(_RATELIMIT_MIN * 60 - secs_since_rl)
                return jsonify({
                    "insertadas": 0, "total": 0,
                    "error": f"Abbott limitó las requests (429). Esperá {wait // 60}m {wait % 60}s más.",
                    "rate_limited": True, "wait_seconds": wait,
                })
        except (ValueError, TypeError):
            _set_setting("libre_rate_limited_at", "")   # timestamp corrupto → limpiar

    # Cooldown normal: solo para syncs automáticas (no manual)
    if not is_manual:
        last_sync_str = _get_setting("libre_last_sync")
        if last_sync_str:
            try:
                last_sync  = datetime.fromisoformat(last_sync_str)
                secs_since = (now - last_sync).total_seconds()
                if secs_since < _COOLDOWN_MIN * 60:
                    wait = int(_COOLDOWN_MIN * 60 - secs_since)
                    return jsonify({
                        "insertadas": 0, "total": 0,
                        "error": None,
                        "cooldown": True, "wait_seconds": wait,
                        "mensaje": f"Ya sincronizaste hace {int(secs_since)}s. Próxima sync en {wait}s.",
                    })
            except (ValueError, TypeError):
                pass

    resultado = _do_libre_sync(email, password)

    # Limpiar rate-limit si el sync fue exitoso
    if not resultado.get("error") or "429" not in (resultado.get("error") or ""):
        _set_setting("libre_rate_limited_at", "")

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



@bp.route("/api/kinetics", endpoint="api_kinetics")
def api_kinetics():
    """
    Devuelve un snapshot de IOB / COB / ROC actual en JSON.
    Útil para actualización periódica en el navegador sin recargar la página.
    """
    try:
        from utils.kinetics import get_kinetics_snapshot, _BASAL_DIA_MIN, _BASAL_DIA_DEFAULT
        from models import InsulinDose
        from helpers import _get_setting

        snap = get_kinetics_snapshot(hours_lookback=6)

        # Debug: replicamos exactamente lo que current_basal_iob() computa
        tipo = (_get_setting("basal_tipo") or "glargina").lower().strip()
        dia  = _BASAL_DIA_MIN.get(tipo, _BASAL_DIA_DEFAULT)
        # Usar datetime.now() igual que current_basal_iob — NO utcnow()
        now_local = datetime.now()
        now_utc   = datetime.utcnow()
        cutoff = now_local - timedelta(minutes=dia)
        dosis_db = (InsulinDose.query
            .filter(InsulinDose.type == "basal",
                    InsulinDose.timestamp >= cutoff,
                    InsulinDose.timestamp <= now_local)
            .order_by(InsulinDose.timestamp.desc())
            .all())
        dosis_info = []
        iob_recalc = 0.0
        for d in dosis_db:
            elapsed_min = (now_local - d.timestamp).total_seconds() / 60.0
            frac = max(0.0, 1.0 - elapsed_min / dia)
            contrib = round(d.units * frac, 3)
            iob_recalc += contrib
            dosis_info.append({
                "ts":         d.timestamp.isoformat(),
                "units":      d.units,
                "elapsed_h":  round(elapsed_min / 60, 3),
                "frac":       round(frac, 4),
                "iob_contrib": contrib,
            })

        from utils.kinetics import biexp_vs_bilinear, _DEFAULT_PEAK_MIN
        modelo_comparacion = biexp_vs_bilinear(
            peak_min=_DEFAULT_PEAK_MIN,
            dia_min=snap["dia_min"],
        )

        return jsonify({
            "ok":        True,
            "iob":       snap["iob"],
            "iob_basal": snap["iob_basal"],
            "iob_bolus": snap["iob_bolus"],
            "cob":       snap["cob"],
            "roc":       snap["roc"],
            "arrow":     snap["arrow"],
            "last_glucose": snap["last_glucose"],
            "context":      snap["context"],
            "dia_min":      snap["dia_min"],
            "modelo_iob":   "biexponencial",
            "modelo_comparacion": modelo_comparacion,
            "basal_debug": {
                "tipo":             tipo,
                "dia_min":          dia,
                "dia_h":            round(dia / 60, 1),
                "now_local":        now_local.isoformat(),
                "now_utc":          now_utc.isoformat(),
                "tz_offset_h":      round((now_local - now_utc).total_seconds() / 3600, 2),
                "ventana_desde":    cutoff.isoformat(),
                "dosis_encontradas": dosis_info,
                "iob_recalc":       round(iob_recalc, 2),
            },
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.route("/api/backfill/insulin-labels", methods=["POST"], endpoint="api_backfill_insulin_labels")
def api_backfill_insulin_labels():
    """
    Etiqueta automáticamente boluses históricos sin purpose usando correlación
    temporal con comidas:

    - purpose='comida'    si hay una comida en los 90 min siguientes al bolus
                          pre_meal_min = minutos entre bolus y comida
    - purpose='correccion' si no hay ninguna comida en ±90 min
    - purpose='mixto'     si hay comida simultánea (±15 min) Y la glucemia
                          previa era >180 mg/dL (corrección + cobertura)
    - Boluses ya etiquetados: se saltan (no se sobreescriben)
    """
    from models import InsulinDose, Meal, GlucoseReading

    # Cargar todo en memoria para evitar N+1
    boluses_sin_label = InsulinDose.query.filter(
        InsulinDose.type == "bolus",
        InsulinDose.purpose.is_(None),
    ).all()

    if not boluses_sin_label:
        return jsonify({"ok": True, "procesados": 0, "mensaje": "No hay boluses sin etiquetar."})

    # Rango: desde el más antiguo hasta ahora
    ts_min = min(b.timestamp for b in boluses_sin_label)
    meals  = Meal.query.filter(Meal.timestamp >= ts_min - timedelta(hours=2)).all()

    # Lecturas de glucosa para detectar hiperglucemia previa al bolus
    readings = GlucoseReading.query.filter(
        GlucoseReading.timestamp >= ts_min - timedelta(hours=1)
    ).order_by(GlucoseReading.timestamp).all()

    stats = {"comida": 0, "correccion": 0, "mixto": 0, "sin_clasificar": 0}

    for bolus in boluses_sin_label:
        bt = bolus.timestamp

        # Buscar comida más cercana DESPUÉS del bolus (ventana 0–90 min)
        comidas_post = [
            m for m in meals
            if 0 <= (m.timestamp - bt).total_seconds() / 60 <= 90
        ]
        # Buscar comida simultánea (bolus dado ≤15 min DESPUÉS de comer)
        comidas_simul = [
            m for m in meals
            if -15 <= (m.timestamp - bt).total_seconds() / 60 <= 15
        ]
        # Glucemia previa al bolus (última lectura en los 30 min anteriores)
        pre_readings = [r for r in readings if bt - timedelta(minutes=30) <= r.timestamp <= bt]
        glucemia_pre = pre_readings[-1].value_mgdl if pre_readings else None

        if comidas_post or comidas_simul:
            # Comida más cercana (puede ser simultánea o pre-bolo)
            todas_cercanas = comidas_post + comidas_simul
            comida_ref     = min(todas_cercanas, key=lambda m: abs((m.timestamp - bt).total_seconds()))
            diff_min       = (comida_ref.timestamp - bt).total_seconds() / 60

            # Si hay hiperglucemia previa Y hay comida → mixto (corrección + cobertura)
            if glucemia_pre and glucemia_pre > 180 and abs(diff_min) <= 30:
                bolus.purpose      = "mixto"
                bolus.pre_meal_min = max(0, round(diff_min))
                stats["mixto"] += 1
            else:
                bolus.purpose      = "comida"
                bolus.pre_meal_min = max(0, round(diff_min))
                stats["comida"] += 1
        else:
            # Sin comida en ±90 min → corrección pura
            bolus.purpose      = "correccion"
            bolus.pre_meal_min = None
            stats["correccion"] += 1

    db.session.commit()

    total = sum(stats.values())
    return jsonify({
        "ok":        True,
        "procesados": total,
        "etiquetas":  stats,
        "mensaje": (
            f"{total} boluses etiquetados: "
            f"{stats['comida']} comida, "
            f"{stats['correccion']} corrección, "
            f"{stats['mixto']} mixto."
        ),
    })


@bp.route("/api/predict/glucose", endpoint="api_predict_glucose")
def api_predict_glucose():
    """
    Predice la glucemia a +30 y +60 minutos usando:
      1. Glucemia actual + tendencia (ROC × Δt)
      2. IOB residual × ISF  (cuánto baja la insulina activa)
      3. COB residual / absorción (cuánto sube la comida restante)
      4. Factor de ejercicio (ajuste de sensibilidad)

    Modelo lineal de primer orden (suficiente para ventanas cortas):
      G(t+Δt) = G_actual
                + ROC × Δt                          (tendencia CGM)
                - ΔIOB(Δt) × ISF_efectivo            (efecto insulina)
                + ΔCOB(Δt) × (ISF / ICR)             (efecto comida)

    donde ΔIOB y ΔCOB son los cambios proyectados en la ventana Δt.
    """
    try:
        from models import InsulinDose, Meal, GlucoseReading, Activity
        from helpers import (
            _get_setting, _calcular_isf_personal, _calcular_icr_personal,
            _calcular_isf_circadiano, _isf_para_hora,
            _calcular_icr_circadiano, _icr_para_hora,
        )
        from utils.kinetics import (
            get_kinetics_snapshot, exercise_sensitivity_factor,
            current_iob, current_cob, current_basal_iob,
            dawn_roc_mgdl_min, _basal_inyeccion_reciente,
            _DEFAULT_PEAK_MIN, _DEFAULT_DIA_MIN,
        )

        now  = datetime.now()
        hora = now.hour

        # ── Parámetros del modelo ─────────────────────────────────────────
        saved_dia = _get_setting("dia_min")
        dia_min   = int(float(saved_dia)) if saved_dia else _DEFAULT_DIA_MIN
        peak_min  = _DEFAULT_PEAK_MIN

        isf_personal, n_isf = _calcular_isf_personal()
        icr_personal, n_icr = _calcular_icr_personal()
        isf_guardado = float(_get_setting("isf_manual")) if _get_setting("isf_manual") else None
        icr_guardado = float(_get_setting("icr"))        if _get_setting("icr")        else None

        # ISF circadiano por hora actual
        isf_circ = _calcular_isf_circadiano(days=90)
        isf_bloque, bloque_label, _ = _isf_para_hora(hora, isf_circ, isf_personal)
        isf_base = isf_guardado or isf_bloque or isf_personal

        # ICR circadiano por hora actual (prioridad: guardado > circadiano > global)
        icr_circ = _calcular_icr_circadiano(days=90)
        icr_bloque, icr_bloque_label, fuente_icr = _icr_para_hora(hora, icr_circ, icr_personal)
        icr = icr_guardado or icr_bloque or icr_personal

        # Factor de ejercicio
        act_cutoff = now - timedelta(hours=24)
        activities = Activity.query.filter(Activity.timestamp >= act_cutoff).all()
        ex_factor  = exercise_sensitivity_factor(activities, at_time=now)
        isf_ef     = round((isf_base or 0) * ex_factor, 1) if isf_base else None

        # ── Datos actuales ────────────────────────────────────────────────
        snap = get_kinetics_snapshot(hours_lookback=6, dia_min=dia_min, peak_min=peak_min)
        g_raw          = snap["last_glucose"]
        roc_regression = snap["roc"]        # mg/dL/min — regresión ponderada
        iob_now        = snap["iob"]         # total bolus + basal (para mostrar)
        iob_bolus_now  = snap["iob_bolus"]   # solo bolus (para predicción de trayectoria)
        iob_basal_now  = snap["iob_basal"]   # solo basal (para desglose informativo)
        cob_now        = snap["cob"]

        if g_raw is None:
            return jsonify({"ok": False, "error": "Sin lecturas de glucosa recientes"})

        # ── Filtro de Kalman — glucosa y ROC estimados con menor ruido ────
        from utils.kalman import get_current_estimate as kalman_estimate
        kalman     = kalman_estimate(propagate=True)
        sigma_g0   = 0.0   # incertidumbre del punto de partida para MC

        if kalman and kalman.get("sigma_G", 99) < 20:
            # Kalman confiable (σ < 20 mg/dL = filtro convergido)
            g_actual  = round(kalman["G"], 1)
            sigma_g0  = kalman["sigma_G"]           # para MC: incertidumbre del estado inicial
            # Blend ROC: 70 % Kalman + 30 % regresión (si disponible)
            roc_k = kalman["v"]
            if roc_regression is not None:
                roc = round(0.7 * roc_k + 0.3 * roc_regression, 3)
            else:
                roc = round(roc_k, 3)
            kalman_active = True
        else:
            # Kalman no inicializado o divergido — usar valores crudos
            g_actual      = g_raw
            roc           = roc_regression
            kalman_active = False
        if isf_ef is None:
            return jsonify({"ok": False, "error": "Sin ISF configurado — ingresalo en Configuración"})

        # ── Fenómeno del alba ─────────────────────────────────────────────────
        # Cortisol + GH secretan glucosa hepática entre las 3–8am.
        # Se suma al ROC efectivo como componente independiente.
        dawn_roc    = dawn_roc_mgdl_min(at_time=now)
        dawn_active = dawn_roc > 0.05   # activo si > umbral mínimo

        # ── Proyección IOB e COB en t+30 y t+60 ──────────────────────────
        cutoff_iob = now - timedelta(minutes=dia_min)
        boluses    = InsulinDose.query.filter(
            InsulinDose.type == "bolus",
            InsulinDose.timestamp >= cutoff_iob,
        ).all()
        fat_cutoff = now - timedelta(hours=8)
        meals_ext  = Meal.query.filter(Meal.timestamp >= fat_cutoff).all()

        # ── Imports internos para esta función ───────────────────────────
        from utils.prediction_feedback import save_prediction, get_adaptive_bias, get_model_accuracy
        from utils.monte_carlo import run_monte_carlo
        from utils.ar_model import get_ar_prediction

        # Bias adaptivo: desplazamiento sistemático observado en predicciones pasadas
        bias    = get_adaptive_bias()
        bias_30 = bias["bias_30"] if bias["confiable"] else 0.0
        bias_60 = bias["bias_60"] if bias["confiable"] else 0.0
        bias_map = {30: bias_30, 60: bias_60}

        # ── Constante de amortiguación del ROC ───────────────────────────
        # El ritmo actual (ROC) no persiste de forma lineal — decaimiento
        # exponencial con τ=30 min (Sparacino 2007):
        #   roc_eff_min = τ × (1 − e^(−Δt/τ))
        # τ=30, Δt=60 → 25.9 min efectivos  (vs. 60 lineal = 2.3× exagerado)
        _TAU_ROC = 30.0

        predictions = {}
        for delta_min in (30, 60):
            t_fut    = now + timedelta(minutes=delta_min)
            iob_fut  = current_iob(boluses,   at_time=t_fut, peak_min=peak_min, dia_min=dia_min)
            cob_fut  = current_cob(meals_ext, at_time=t_fut)
            # ΔIOB para predicción: bolus siempre + basal solo si es reciente.
            #
            # La basal en estado estable (> 4h) ya está capturada en el ROC del
            # CGM → sumarla sería doble conteo. Pero si la inyección fue hace
            # < 4h, el sensor aún no registró todo el efecto → hay que incluirla.
            iob_basal_fut = current_basal_iob(at_time=t_fut)
            basal_es_reciente = _basal_inyeccion_reciente(now, umbral_h=4)
            d_iob_basal = (iob_basal_now - iob_basal_fut) if basal_es_reciente else 0.0
            d_iob = (iob_bolus_now - iob_fut) + d_iob_basal
            d_cob    = cob_now - cob_fut   # carbos absorbidos en Δt  (> 0 → sube glucosa)

            # ROC con decaimiento exponencial + supresión por COB activo
            # (evita doble conteo: comida ya está en ROC Y en carb_effect)
            roc_eff_min     = _TAU_ROC * (1.0 - _math.exp(-delta_min / _TAU_ROC))
            cob_suppression = max(0.15, 1.0 - (cob_now / 35.0))
            roc_effect      = (roc or 0) * roc_eff_min * cob_suppression
            insulin_effect  = d_iob * isf_ef
            carb_effect     = (d_cob * isf_ef / icr) if icr else 0.0
            # Fenómeno del alba: efecto independiente del ROC del CGM
            # (secreción hepática de glucosa, no suprimida por COB)
            dawn_effect_total = dawn_roc * roc_eff_min

            # Estimación puntual (referencia para tooltip y feedback)
            g_pred_pt = g_actual + roc_effect - insulin_effect + carb_effect + dawn_effect_total

            # ── Monte Carlo — propaga incertidumbre de todos los parámetros ──
            # El bias se aplica desplazando g_actual: toda la distribución se
            # corre el mismo offset sin afectar la forma (incertidumbre).
            bias_val = bias_map[delta_min]
            mc = run_monte_carlo(
                g_actual        = g_actual + bias_val + dawn_effect_total,
                roc             = roc,
                roc_eff_min     = roc_eff_min,
                cob_suppression = cob_suppression,
                d_iob           = d_iob,
                d_cob           = d_cob,
                isf_base        = isf_ef,
                icr             = icr,
                n               = 3_000,
                sigma_g0        = sigma_g0,   # incertidumbre Kalman del punto de partida
            )

            # ── Blending AR + MC — ponderación por varianza inversa ─────────
            # El modelo AR captura momentum/patrones individuales; el MC
            # captura causalidad (insulina, carbos). Combinados son complementarios.
            # Pesos: w_AR = (1/σ²_AR) / (1/σ²_AR + 1/σ²_MC), cap 40%.
            ar        = get_ar_prediction(horizon_min=delta_min)
            ar_active = False
            ar_weight = 0.0
            g_final   = mc["g_pred_median"]   # default: solo MC

            if ar and ar.get("ok") and ar["sigma"] > 0 and mc["sigma"] > 0:
                sigma_ar = ar["sigma"]
                sigma_mc = mc["sigma"]
                # Ponderación inversa-varianza (estadísticamente óptima si errores independientes)
                inv_var_ar = 1.0 / sigma_ar ** 2
                inv_var_mc = 1.0 / sigma_mc ** 2
                w_ar_raw   = inv_var_ar / (inv_var_ar + inv_var_mc)
                # Cap: AR no supera el 40% (el modelo físico siempre domina)
                ar_weight  = round(min(w_ar_raw, 0.40), 3)
                g_blended  = (1.0 - ar_weight) * mc["g_pred_median"] + ar_weight * ar["g_pred"]
                g_final    = round(g_blended)
                ar_active  = True

            # ── Explicación legible de la predicción ──────────────────────
            from utils.explicabilidad import explicar_prediccion
            explicacion = explicar_prediccion(
                g_actual    = g_actual,
                g_pred      = g_final,
                componentes = {
                    "roc_effect":    round(roc_effect, 1),
                    "insulin_effect": round(insulin_effect, 1),
                    "carb_effect":   round(carb_effect, 1),
                    "dawn_effect":   round(dawn_effect_total, 1),
                },
                iob       = iob_now,
                cob       = cob_now,
                roc       = roc,
                ex_factor = ex_factor,
                isf       = isf_ef,
                icr       = icr,
                delta_min = delta_min,
            )

            predictions[f"+{delta_min}min"] = {
                # Valor central: mediana MC (o blend MC+AR si AR disponible)
                "glucemia_pred":  g_final,
                "glucemia_mean":  mc["g_pred_mean"],
                "glucemia_pt":    round(g_pred_pt + bias_val),   # estimado puntual (debug)
                "estado":         mc["estado"],
                "delta_min":      delta_min,
                "bias_aplicado":  bias_val,
                # Incertidumbre empírica (no asume Normal)
                "sigma":          mc["sigma"],
                "skewness":       mc["skewness"],
                "p_hipo":         mc["p_hipo"],
                "p_rango":        mc["p_rango"],
                "p_hiper":        mc["p_hiper"],
                "ci_50":          mc["ci_50"],    # [p25, p75]
                "ci_68":          mc["ci_68"],    # [p16, p84]
                "ci_90":          mc["ci_90"],    # [p5,  p95]
                "p5":             mc["p5"],
                "p95":            mc["p95"],
                "n_sim":          mc["n_sim"],
                # Componentes del modelo (para tooltip diagnóstico)
                "componentes": {
                    "roc_effect":      round(roc_effect,        1),
                    "insulin_effect":  round(-insulin_effect,   1),
                    "carb_effect":     round(carb_effect,       1),
                    "dawn_effect":     round(dawn_effect_total, 1),
                    "basal_reciente":  basal_es_reciente,
                    "d_iob_basal":     round(d_iob_basal,       3),
                    "roc_eff_min":     round(roc_eff_min,       1),
                    "cob_suppression": round(cob_suppression,   2),
                },
                # Contribución del modelo AR
                "ar": {
                    "active":    ar_active,
                    "g_pred":    ar["g_pred"]   if ar_active else None,
                    "sigma":     ar["sigma"]    if ar_active else None,
                    "weight":    ar_weight      if ar_active else 0,
                    "mc_weight": round(1 - ar_weight, 3) if ar_active else 1.0,
                    "age_min":   ar.get("last_age_min") if ar_active else None,
                },
                "iob_fut":     round(iob_fut, 2),
                "cob_fut":     round(cob_fut, 1),
                "explicacion": explicacion,
            }

        # ── Guardar predicción en BD para feedback posterior ──────────────
        try:
            save_prediction(
                predicted_at = now,
                g_actual     = g_actual,
                g_pred_30    = predictions["+30min"]["glucemia_pred"],
                g_pred_60    = predictions["+60min"]["glucemia_pred"],
                iob          = iob_now,
                cob          = cob_now,
                roc          = roc,
                isf_used     = isf_ef,
                icr_used     = icr,
                ex_factor    = ex_factor,
            )
        except Exception as _e:
            import logging
            logging.getLogger(__name__).warning(f"save_prediction falló: {_e}")
            # nunca romper la predicción por fallo de persistencia

        # ── Accuracy del modelo (últimas N predicciones resueltas) ────────
        accuracy = get_model_accuracy(n=20)

        # Confianza: baja si sin ROC, sin ISF personal, o con pocos datos
        confianza_baja = []
        if roc is None:                 confianza_baja.append("sin tendencia CGM")
        if n_isf < 5:                   confianza_baja.append(f"ISF con solo {n_isf} muestras")
        if icr is None:                 confianza_baja.append("sin ICR")
        if not activities:              confianza_baja.append("ejercicio desconocido")

        return jsonify({
            "ok":             True,
            "g_actual":       g_actual,
            "g_raw":          g_raw,
            "roc":            roc,
            "arrow":          snap["arrow"],
            "iob_now":        iob_now,
            "iob_basal":      iob_basal_now,
            "cob_now":        cob_now,
            "isf_ef":         isf_ef,
            "icr":            icr,
            "ex_factor":      ex_factor,
            "predictions":    predictions,
            "confianza_baja": confianza_baja,
            "bias":           bias,
            "accuracy":       accuracy,
            "dawn": {
                "active":       dawn_active,
                "roc_mgdl_min": round(dawn_roc, 4),
            },
            "kalman": {
                "active":   kalman_active,
                "G":        round(kalman["G"], 1)      if kalman_active else None,
                "sigma_G":  kalman.get("sigma_G")      if kalman_active else None,
                "v":        round(kalman["v"], 3)      if kalman_active else None,
                "sigma_v":  kalman.get("sigma_v")      if kalman_active else None,
                "gain":     kalman.get("kalman_gain")  if kalman_active else None,
                "dt_min":   kalman.get("dt_since_update") if kalman_active else None,
            },
            "timestamp":      now.strftime("%H:%M:%S"),
        })

    except Exception as e:
        import traceback
        return jsonify({"ok": False, "error": str(e), "trace": traceback.format_exc()}), 500


@bp.route("/api/ar/status", endpoint="api_ar_status")
def api_ar_status():
    """Estado y métricas del modelo AR (sin reentrenar)."""
    if not session.get("logged_in"):
        return jsonify({"error": "No autorizado"}), 401
    try:
        from utils.ar_model import get_ar_status
        return jsonify({"ok": True, **get_ar_status()})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.route("/api/ar/fit", methods=["POST"], endpoint="api_ar_fit")
def api_ar_fit():
    """
    Fuerza el reentrenamiento del modelo AR con todos los datos disponibles.
    Útil tras importar un CSV de datos históricos o para entrenamiento inicial.
    """
    if not session.get("logged_in"):
        return jsonify({"error": "No autorizado"}), 401
    try:
        from utils.ar_model import fit_ar_model
        result = fit_ar_model()
        return jsonify(result)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.route("/api/model/accuracy", endpoint="api_model_accuracy")
def api_model_accuracy():
    """Métricas de accuracy del modelo — para mostrar en Calibración."""
    try:
        from utils.prediction_feedback import get_model_accuracy, get_adaptive_bias
        return jsonify({
            "ok":       True,
            "accuracy": get_model_accuracy(n=500),
            "bias":     get_adaptive_bias(),
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.route("/api/feedback/stats", endpoint="api_feedback_stats")
def api_feedback_stats():
    """Estado del feedback loop: cuántas predicciones hay, resueltas y pendientes."""
    try:
        from models import GlucosePrediction
        from utils.prediction_feedback import get_model_accuracy, get_adaptive_bias

        total      = GlucosePrediction.query.count()
        res_30     = GlucosePrediction.query.filter_by(resolved_30=True).count()
        res_60     = GlucosePrediction.query.filter_by(resolved_60=True).count()
        pendientes = GlucosePrediction.query.filter(
            db.or_(GlucosePrediction.resolved_30 == False,
                   GlucosePrediction.resolved_60 == False)
        ).count()
        ultima = GlucosePrediction.query.order_by(
            GlucosePrediction.predicted_at.desc()
        ).first()

        accuracy = get_model_accuracy(n=500) if res_30 >= 5 else None

        return jsonify({
            "ok": True,
            "tabla_existe": True,
            "total_predicciones": total,
            "resueltas_30min": res_30,
            "resueltas_60min": res_60,
            "pendientes": pendientes,
            "ultima_prediccion": ultima.predicted_at.isoformat() if ultima else None,
            "loop_activo": total > 0,
            "accuracy": accuracy,
            # Clarke Error Grid viene dentro de accuracy como ceg_30 / ceg_60
            "clarke_30": accuracy.get("ceg_30") if accuracy else None,
            "clarke_60": accuracy.get("ceg_60") if accuracy else None,
            "bias":      get_adaptive_bias(),
        })
    except Exception as e:
        import traceback
        return jsonify({"ok": False, "error": str(e),
                        "tabla_existe": False,
                        "trace": traceback.format_exc()}), 500


@bp.route("/api/recalibration", endpoint="api_recalibration")
def api_recalibration():
    """
    Sugerencias de recalibración automática de ISF e ICR.
    Compara los valores calculados desde datos reales vs los configurados
    y genera recomendaciones accionables.
    """
    try:
        from utils.prediction_feedback import get_recalibration_suggestions
        result = get_recalibration_suggestions()
        return jsonify({"ok": True, **result})
    except Exception as e:
        import traceback
        return jsonify({"ok": False, "error": str(e),
                        "trace": traceback.format_exc()}), 500


@bp.route("/api/diagnostico", endpoint="api_diagnostico")
def api_diagnostico():
    """
    Panel de diagnóstico completo: expone todas las variables del modelo
    con sus valores actuales, fuentes y contribución al cálculo final.
    """
    try:
        from datetime import datetime, timedelta
        from models import InsulinDose, Meal, GlucoseReading, Activity
        from helpers import (
            _get_setting, _calcular_isf_personal, _calcular_icr_personal,
            _calcular_isf_circadiano, _isf_para_hora,
            _calcular_icr_circadiano, _icr_para_hora,
        )
        from utils.kinetics import (
            get_kinetics_snapshot, exercise_sensitivity_factor,
            _classify_exercise, current_cob_detailed,
        )

        now   = datetime.now()
        hora  = now.hour

        # ── 1. IOB: desglose por bolus (solo bolus se deduce de corrección) ──
        from utils.kinetics import (
            _biexp_iob_fraction, _DEFAULT_PEAK_MIN, _DEFAULT_DIA_MIN, current_basal_iob
        )
        saved_dia   = _get_setting("dia_min")
        dia_min     = int(float(saved_dia)) if saved_dia else _DEFAULT_DIA_MIN
        peak_min    = _DEFAULT_PEAK_MIN
        cutoff_iob  = now - timedelta(minutes=dia_min)
        boluses_raw = InsulinDose.query.filter(
            InsulinDose.type == "bolus",
            InsulinDose.timestamp >= cutoff_iob,
        ).order_by(InsulinDose.timestamp.desc()).all()

        iob_detalle = []
        iob_total   = 0.0
        for b in boluses_raw:
            elapsed = (now - b.timestamp).total_seconds() / 60
            frac    = _biexp_iob_fraction(elapsed, peak_min, dia_min)
            contrib = round(b.units * frac, 3)
            iob_total += contrib
            iob_detalle.append({
                "timestamp":   b.timestamp.strftime("%H:%M"),
                "units":       b.units,
                "purpose":     b.purpose or "sin_etiqueta",
                "elapsed_min": round(elapsed),
                "frac_activa": round(frac, 3),
                "contribucion_U": contrib,
            })

        # IOB basal (informativo — no se resta de la corrección)
        iob_basal_U = current_basal_iob(at_time=now)

        # ── 2. COB: desglose por comida ──────────────────────────────────────
        fat_cutoff  = now - timedelta(hours=8)
        meals_raw   = Meal.query.filter(Meal.timestamp >= fat_cutoff).all()
        snap_roc    = get_kinetics_snapshot(hours_lookback=1).get("roc")
        cob_data    = current_cob_detailed(meals_raw, at_time=now, roc=snap_roc)

        cob_detalle = []
        for m in cob_data["meals_detail"]:
            cob_detalle.append({
                "nombre":      m["name"],
                "hace_horas":  m["elapsed_h"],
                "carbs_cob":   m["carbs_cob"],
                "fat_cob":     m["fat_cob"],
                "prot_cob":    m["prot_cob"],
            })

        # ── 3. ISF: cadena de prioridad ──────────────────────────────────────
        isf_personal, n_isf  = _calcular_isf_personal()
        isf_circ             = _calcular_isf_circadiano(days=90)
        isf_guardado_raw     = _get_setting("isf_manual")
        isf_guardado         = float(isf_guardado_raw) if isf_guardado_raw else None
        isf_bloque, bloque_label, fuente_circ = _isf_para_hora(hora, isf_circ, isf_personal)

        # Cadena de prioridad
        isf_cadena = [
            {"fuente": "manual_sesion",  "valor": None,         "activa": False,
             "descripcion": "ISF ingresado manualmente en calculadora (parámetro ?isf=)"},
            {"fuente": "guardado",       "valor": isf_guardado, "activa": isf_guardado is not None,
             "descripcion": "ISF guardado en Configuración por el usuario"},
            {"fuente": "circadiano",     "valor": isf_bloque,   "activa": fuente_circ == "circadiano",
             "descripcion": f"ISF del bloque {bloque_label} (promedio de correcciones en ese horario)"},
            {"fuente": "global",         "valor": isf_personal, "activa": True,
             "descripcion": f"ISF global promedio de {n_isf} correcciones de los últimos 90d"},
        ]
        isf_efectivo_base = isf_guardado or isf_bloque or isf_personal

        # Bloques circadianos
        circ_bloques = []
        for blk, data in isf_circ.items():
            circ_bloques.append({
                "bloque_h":   blk,
                "label":      data["label"],
                "isf":        data["isf"],
                "n":          data["n"],
                "es_actual":  blk == (hora // 4) * 4,
            })

        # ── 4. ICR ───────────────────────────────────────────────────────────
        icr_personal, n_icr  = _calcular_icr_personal()
        icr_guardado_raw     = _get_setting("icr")
        icr_guardado         = float(icr_guardado_raw) if icr_guardado_raw else None
        icr_circ_diag        = _calcular_icr_circadiano(days=90)
        icr_bloque_d, icr_bloque_label_d, fuente_icr_d = _icr_para_hora(
            hora, icr_circ_diag, icr_personal
        )
        icr_efectivo = icr_guardado or icr_bloque_d or icr_personal

        # Tabla circadiana de ICR por bloque
        icr_circ_bloques = []
        for blk, data in sorted(icr_circ_diag.items()):
            icr_circ_bloques.append({
                "bloque_h":  blk,
                "label":     data["label"],
                "icr":       data["icr"],
                "n":         data["n"],
                "es_actual": blk == (hora // 4) * 4,
            })

        # ── 5. Ejercicio ─────────────────────────────────────────────────────
        act_cutoff  = now - timedelta(hours=24)
        activities  = Activity.query.filter(Activity.timestamp >= act_cutoff).all()

        ej_detalle  = []
        for act in activities:
            elapsed_h = (now - act.timestamp).total_seconds() / 3600
            ex_type   = _classify_exercise(act.activity_type, act.exercise_type)
            # Factor individual
            f_ind = exercise_sensitivity_factor([act], at_time=now)
            ej_detalle.append({
                "nombre":       act.activity_type,
                "tipo_guardado": act.exercise_type or "no especificado",
                "tipo_inferido": ex_type,
                "intensidad":   act.intensity or "media",
                "duracion_min": act.duration_min,
                "hace_horas":   round(elapsed_h, 1),
                "factor_individual": f_ind,
                "delta_pct":    round((f_ind - 1) * 100, 1),
            })

        ex_factor_total = exercise_sensitivity_factor(activities, at_time=now)
        isf_con_ejercicio = round((isf_efectivo_base or 0) * ex_factor_total, 1) if isf_efectivo_base else None

        # ── 6. DIA ───────────────────────────────────────────────────────────
        dia_fuente = "guardado" if saved_dia else "default_NovoRapid"

        # ── 7. Última glucemia + ROC ─────────────────────────────────────────
        from utils.kinetics import glucose_roc, roc_arrow
        cgm_roc     = GlucoseReading.query.filter(
            GlucoseReading.timestamp >= now - timedelta(minutes=30)
        ).order_by(GlucoseReading.timestamp).all()
        ultima_g    = GlucoseReading.query.order_by(
            GlucoseReading.timestamp.desc()
        ).first()
        roc_val     = glucose_roc(cgm_roc, window_min=20)

        # ── Resumen: qué se usaría AHORA en la calculadora ───────────────────
        objetivo = float(_get_setting("objetivo", "100"))

        return jsonify({
            "ok": True,
            "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
            "hora_actual": hora,

            "iob": {
                "bolus_U":     round(iob_total, 3),
                "basal_U":     iob_basal_U,
                "total_U":     round(iob_total + iob_basal_U, 3),
                "nota":        "bolus_U es el que se deduce de la corrección. basal_U es informativo (cubre producción hepática, no exceso de glucosa).",
                "dia_min":     dia_min,
                "peak_min":    peak_min,
                "dia_fuente":  dia_fuente,
                "boluses_activos": len([b for b in iob_detalle if b["contribucion_U"] > 0]),
                "detalle":     iob_detalle,
            },

            "cob": {
                "carbs_cob":   cob_data["carbs_cob"],
                "fat_cob":     cob_data["fat_cob"],
                "prot_cob":    cob_data["prot_cob"],
                "fp_cob":      cob_data["fp_cob"],
                "total_cob":   cob_data["total_cob"],
                "has_extended": cob_data["has_extended"],
                "comidas_activas": len(cob_detalle),
                "detalle":     cob_detalle,
            },

            "isf": {
                "efectivo_base":     isf_efectivo_base,
                "con_ejercicio":     isf_con_ejercicio,
                "global_calculado":  isf_personal,
                "n_correcciones":    n_isf,
                "guardado_usuario":  isf_guardado,
                "objetivo_mg_dl":    objetivo,
                "cadena_prioridad":  isf_cadena,
                "circadiano_bloques": circ_bloques,
                "bloque_activo":     bloque_label,
            },

            "icr": {
                "efectivo":           icr_efectivo,
                "calculado":          icr_personal,
                "n_comidas":          n_icr,
                "guardado_usuario":   icr_guardado,
                "fuente":             "guardado" if icr_guardado else fuente_icr_d,
                "circadiano_bloques": icr_circ_bloques,
                "bloque_activo":      icr_bloque_label_d,
            },

            "ejercicio": {
                "factor_total":      ex_factor_total,
                "delta_pct":         round((ex_factor_total - 1) * 100, 1),
                "actividades_24h":   len(ej_detalle),
                "detalle":           ej_detalle,
            },

            "glucemia": {
                "ultima_mg_dl":  ultima_g.value_mgdl if ultima_g else None,
                "ultima_ts":     ultima_g.timestamp.strftime("%H:%M") if ultima_g else None,
                "roc_mgdl_min":  roc_val,
                "arrow":         roc_arrow(roc_val),
                "lecturas_roc":  len(cgm_roc),
            },

            "variables_faltantes": _check_missing_variables(
                isf_personal, n_isf, icr_personal, n_icr,
                iob_detalle, cob_detalle, ej_detalle
            ),
        })

    except Exception as e:
        import traceback
        return jsonify({"ok": False, "error": str(e), "trace": traceback.format_exc()}), 500


def _check_missing_variables(isf, n_isf, icr, n_icr, boluses, meals, activities):
    """Detecta qué variables no tienen datos suficientes."""
    alertas = []
    if not isf:
        alertas.append({"variable": "ISF", "nivel": "critico",
                        "mensaje": "Sin correcciones registradas — el ISF no se puede calcular"})
    elif n_isf < 5:
        alertas.append({"variable": "ISF", "nivel": "advertencia",
                        "mensaje": f"Solo {n_isf} correcciones — estimación poco confiable (mínimo recomendado: 10)"})

    if not icr:
        alertas.append({"variable": "ICR", "nivel": "critico",
                        "mensaje": "Sin comidas + bolus correlacionados — el ICR no se puede calcular"})
    elif n_icr < 5:
        alertas.append({"variable": "ICR", "nivel": "advertencia",
                        "mensaje": f"Solo {n_icr} comidas con bolus — estimación poco confiable"})

    labeled_boluses = sum(1 for b in boluses if b["purpose"] != "sin_etiqueta")
    if boluses and labeled_boluses == 0:
        alertas.append({"variable": "Propósito_bolus", "nivel": "advertencia",
                        "mensaje": "Ningún bolus está etiquetado (comida/corrección/mixto) — el modelo usa inferencias menos precisas"})

    if not activities:
        alertas.append({"variable": "Ejercicio", "nivel": "info",
                        "mensaje": "Sin actividad en las últimas 24h — factor de ejercicio = 1.0 (sin ajuste)"})

    ex_sin_tipo = sum(1 for a in activities if a["tipo_guardado"] == "no especificado")
    if ex_sin_tipo > 0:
        alertas.append({"variable": "Tipo_ejercicio", "nivel": "advertencia",
                        "mensaje": f"{ex_sin_tipo} actividad(es) sin tipo metabólico — se usa inferencia por nombre"})

    return alertas


@bp.route("/api/weekly-accuracy-report", methods=["POST"], endpoint="api_weekly_accuracy_report")
def api_weekly_accuracy_report():
    """
    Dispara manualmente el reporte semanal de precisión por email.
    Útil para testear la configuración SMTP sin esperar el lunes.

    Autenticación: sesión web O ?token=SYNC_TOKEN
    """
    token_param = request.args.get("token", "")
    if not session.get("logged_in"):
        if not _SYNC_TOKEN or token_param != _SYNC_TOKEN:
            return jsonify({"error": "No autorizado"}), 401

    try:
        from utils.email_notifier import send_weekly_accuracy_report
        result = send_weekly_accuracy_report()
        return jsonify(result)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.route("/api/kinetics/dia", endpoint="api_dia_estimate")
def api_dia_estimate():
    """
    Estima la Duración de Acción de la Insulina (DIA) del usuario
    analizando eventos de corrección pura (sin comida cercana).
    """
    try:
        from utils.kinetics import estimate_dia_from_data
        result = estimate_dia_from_data(days=90)
        return jsonify({"ok": True, **result})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── Capa 2: Detección de patrones fisiológicos ────────────────────────────────
@bp.route("/api/patrones/analisis", endpoint="api_patrones_analisis")
def api_patrones_analisis():
    """
    Detecta patrones fisiológicos recurrentes (Capa 2 IA):
    Somogyi, fenómeno del alba, hipo post-ejercicio, rebote grasa/proteína,
    variabilidad excesiva, hipers pre-comida.

    Query param: days (int, default 30)

    Retorna: { ok, patrones[], resumen{}, serie_glucose[], generado_en }
    La serie_glucose está disponible para Capa 3 (Claude API).
    """
    days = request.args.get("days", 30, type=int)
    days = max(7, min(days, 90))   # clamp 7–90 días
    try:
        from utils.patrones_detector import analizar_patrones
        resultado = analizar_patrones(days=days)
        return jsonify({"ok": True, **resultado})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
