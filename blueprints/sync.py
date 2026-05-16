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



@bp.route("/api/kinetics", endpoint="api_kinetics")
def api_kinetics():
    """
    Devuelve un snapshot de IOB / COB / ROC actual en JSON.
    Útil para actualización periódica en el navegador sin recargar la página.
    """
    try:
        from utils.kinetics import get_kinetics_snapshot
        snap = get_kinetics_snapshot(hours_lookback=6)
        return jsonify({
            "ok":    True,
            "iob":   snap["iob"],
            "cob":   snap["cob"],
            "roc":   snap["roc"],
            "arrow": snap["arrow"],
            "last_glucose": snap["last_glucose"],
            "context":      snap["context"],
            "dia_min":      snap["dia_min"],
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


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
        )
        from utils.kinetics import (
            get_kinetics_snapshot, exercise_sensitivity_factor,
            _classify_exercise, current_cob_detailed,
        )

        now   = datetime.now()
        hora  = now.hour

        # ── 1. IOB: desglose por bolus ──────────────────────────────────────
        from utils.kinetics import _iob_fraction, _DEFAULT_PEAK_MIN, _DEFAULT_DIA_MIN
        saved_dia   = _get_setting("dia_min")
        dia_min     = int(saved_dia) if saved_dia else _DEFAULT_DIA_MIN
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
            frac    = _iob_fraction(elapsed, peak_min, dia_min)
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

        # ── 2. COB: desglose por comida ──────────────────────────────────────
        fat_cutoff  = now - timedelta(hours=8)
        meals_raw   = Meal.query.filter(Meal.timestamp >= fat_cutoff).all()
        cob_data    = current_cob_detailed(meals_raw, at_time=now)

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
        icr_personal, n_icr = _calcular_icr_personal()
        icr_guardado_raw    = _get_setting("icr")
        icr_guardado        = float(icr_guardado_raw) if icr_guardado_raw else None
        icr_efectivo        = icr_guardado or icr_personal

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
                "total_U":     round(iob_total, 3),
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
                "efectivo":          icr_efectivo,
                "calculado":         icr_personal,
                "n_comidas":         n_icr,
                "guardado_usuario":  icr_guardado,
                "fuente":            "guardado" if icr_guardado else "calculado",
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
