"""
blueprints/copilot_api.py
─────────────────────────
API JSON del producto Orbit Copilot (frontend React).

Solo **estado presente** y datos retrospectivos — SIN predicciones (la
separación regulatoria del producto). Solo lectura: no toca el SSM ni escribe
nada. Reusa los cálculos que ya existen (get_kinetics_snapshot).

    GET /api/copilot/home → glucosa actual, IOB, COB, tendencia, TIR 24h,
                            actividad reciente.
"""
from datetime import datetime, timedelta
from flask import Blueprint, jsonify, session, request

bp = Blueprint("copilot_api", __name__)

LOW, HIGH = 70, 180


def _require_login():
    if not session.get("logged_in"):
        return jsonify({"ok": False, "error": "No autorizado"}), 401
    return None


def _hace(ts):
    """'ahora' / '12m' / '3h' / '2d' — hora local (datetime.now)."""
    if not ts:
        return ""
    mins = int((datetime.now() - ts).total_seconds() / 60)
    if mins < 1:
        return "ahora"
    if mins < 60:
        return f"{mins}m"
    horas = mins // 60
    if horas < 24:
        return f"{horas}h"
    return f"{horas // 24}d"


@bp.route("/api/copilot/home", endpoint="copilot_home")
def copilot_home():
    """Datos de la pantalla Hoy. Estado presente, sin predicciones."""
    err = _require_login()
    if err:
        return err

    from models import GlucoseReading, Meal, InsulinDose, Activity
    from utils.kinetics import get_kinetics_snapshot

    # Resiliencia: si el snapshot de cinética falla, la pantalla igual carga
    # (glucosa, TIR y actividad no dependen de él). No predice nada.
    try:
        snap = get_kinetics_snapshot(hours_lookback=6) or {}
    except Exception:
        snap = {}
    iob = round(snap.get("iob_bolus") or 0.0, 1)
    cob = int(round(snap.get("cob") or 0))
    roc = snap.get("roc") or 0.0
    arrow = snap.get("arrow") or "→"

    # ── glucosa actual (última lectura) ───────────────────────────────────
    last = GlucoseReading.query.order_by(GlucoseReading.timestamp.desc()).first()
    glucose = None
    if last:
        v = int(round(last.value_mgdl))
        status = "hipo" if v < LOW else "hiper" if v > HIGH else "rango"
        glucose = {
            "value": v,
            "status": status,
            "arrow": arrow,
            "source": last.source,
            "at": last.timestamp.isoformat(),
            "age_min": int((datetime.now() - last.timestamp).total_seconds() / 60),
        }

    trend = "Subiendo" if roc > 1 else "Bajando" if roc < -1 else "Estable"

    # ── tiempo en rango — últimas 24h ─────────────────────────────────────
    since = datetime.now() - timedelta(hours=24)
    reads = (GlucoseReading.query
             .filter(GlucoseReading.timestamp >= since)
             .all())
    tir = None
    series = []
    if reads:
        in_range = sum(1 for r in reads if LOW <= r.value_mgdl <= HIGH)
        tir = round(100 * in_range / len(reads))
        # serie ordenada para la onda de 24h (solo lecturas, sin predicción)
        series = [{"t": r.timestamp.isoformat(), "v": round(r.value_mgdl, 1)}
                  for r in sorted(reads, key=lambda r: r.timestamp)]

    # ── actividad reciente (comida / insulina / ejercicio) ────────────────
    # Incluye id + data para poder editar/borrar tocando el item (misma hoja
    # que el Historial).
    events = []
    for m in Meal.query.order_by(Meal.timestamp.desc()).limit(4).all():
        events.append({"cat": "comida", "id": m.id, "title": m.name or "Comida",
                       "badge": f"{int(m.carbs_g)}g" if m.carbs_g else "", "ts": m.timestamp,
                       "data": {"name": m.name or "", "carbs": m.carbs_g or 0,
                                "protein": m.protein_g or 0, "fat": m.fat_g or 0}})
    for d in InsulinDose.query.order_by(InsulinDose.timestamp.desc()).limit(4).all():
        label = {"bolus": "Rápida", "basal": "Basal"}.get(d.type, (d.type or "").capitalize())
        events.append({"cat": "insulina", "id": d.id, "title": f"Insulina {label}".strip(),
                       "badge": f"{d.units:g}U", "ts": d.timestamp,
                       "data": {"units": d.units, "type": d.type, "label": label}})
    for a in Activity.query.order_by(Activity.timestamp.desc()).limit(4).all():
        events.append({"cat": "ejercicio", "id": a.id, "title": a.activity_type or "Ejercicio",
                       "badge": f"{a.duration_min}m" if a.duration_min else "", "ts": a.timestamp,
                       "data": {"activity_type": a.activity_type or "", "duration_min": a.duration_min or 0,
                                "intensity": a.intensity or ""}})
    events.sort(key=lambda e: e["ts"], reverse=True)
    recent = [{"cat": e["cat"], "id": e["id"], "title": e["title"], "badge": e["badge"],
               "ago": _hace(e["ts"]), "data": e["data"],
               "date": e["ts"].strftime("%Y-%m-%d"), "time": e["ts"].strftime("%H:%M")}
              for e in events[:4]]

    return jsonify({
        "ok": True,
        "glucose": glucose,
        "context": {"iob": iob, "cob": cob, "trend": trend},
        "tir_today": tir,
        "series": series,
        "recent": recent,
        "updated_at": datetime.now().isoformat(),
    })


# ── Brief diario — resumen retrospectivo del día (SIN predicción) ─────────────
def _today_stats():
    """Métricas de HOY (desde la medianoche local) calculadas, no predichas."""
    from models import GlucoseReading, Meal, InsulinDose, Activity
    now = datetime.now()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    reads = (GlucoseReading.query
             .filter(GlucoseReading.timestamp >= start)
             .order_by(GlucoseReading.timestamp).all())
    g = {"readings_n": len(reads), "tir": None, "avg": None,
         "low_pct": 0, "high_pct": 0, "min": None, "max": None}
    if reads:
        vals = [r.value_mgdl for r in reads]
        g["avg"] = int(round(sum(vals) / len(vals)))
        g["tir"] = round(100 * sum(1 for v in vals if LOW <= v <= HIGH) / len(vals))
        g["low_pct"] = round(100 * sum(1 for v in vals if v < LOW) / len(vals))
        g["high_pct"] = round(100 * sum(1 for v in vals if v > HIGH) / len(vals))
        lo = min(reads, key=lambda r: r.value_mgdl)
        hi = max(reads, key=lambda r: r.value_mgdl)
        g["min"] = {"v": int(round(lo.value_mgdl)), "time": lo.timestamp.strftime("%H:%M")}
        g["max"] = {"v": int(round(hi.value_mgdl)), "time": hi.timestamp.strftime("%H:%M")}

    meals = Meal.query.filter(Meal.timestamp >= start).all()
    doses = InsulinDose.query.filter(InsulinDose.timestamp >= start).all()
    acts = Activity.query.filter(Activity.timestamp >= start).all()

    return {
        **g,
        "carbs_total": int(round(sum(m.carbs_g or 0 for m in meals))),
        "meals_n": len(meals),
        "insulin_total": round(sum(d.units or 0 for d in doses), 1),
        "bolus_total": round(sum(d.units or 0 for d in doses if d.type == "bolus"), 1),
        "basal_total": round(sum(d.units or 0 for d in doses if d.type == "basal"), 1),
        "activity_n": len(acts),
        "activity_min": int(sum(a.duration_min or 0 for a in acts)),
    }


def _greeting(hour):
    if hour < 12:
        return "Buenos días"
    if hour < 20:
        return "Buenas tardes"
    return "Buenas noches"


def _brief_context(s):
    """Texto compacto de los datos de hoy para alimentar la narrativa."""
    L = []
    if s["readings_n"]:
        L.append(f"Tiempo en rango hoy: {s['tir']}% ({s['readings_n']} lecturas).")
        L.append(f"Glucosa promedio: {s['avg']} mg/dL. Mínima {s['min']['v']} a las "
                 f"{s['min']['time']}, máxima {s['max']['v']} a las {s['max']['time']}.")
        if s["low_pct"]:
            L.append(f"{s['low_pct']}% del tiempo por debajo de 70.")
        if s["high_pct"]:
            L.append(f"{s['high_pct']}% del tiempo por encima de 180.")
    else:
        L.append("Todavía no hay lecturas de glucosa hoy.")
    if s["meals_n"]:
        L.append(f"Comidas: {s['meals_n']} ({s['carbs_total']} g de carbohidratos en total).")
    if s["insulin_total"]:
        L.append(f"Insulina: {s['insulin_total']} U en total.")
    if s["activity_min"]:
        L.append(f"Actividad: {s['activity_n']} sesión/es, {s['activity_min']} min.")
    return " ".join(L)


def _brief_fallback(s):
    """Narrativa determinista si el LLM no está disponible."""
    if not s["readings_n"]:
        return ("Todavía no hay lecturas de glucosa registradas hoy. "
                "Cuando sincronices tu sensor, te muestro cómo viene tu día.")
    txt = f"Hoy llevás {s['tir']}% del tiempo en rango, con un promedio de {s['avg']} mg/dL."
    if s["low_pct"] >= 4:
        txt += f" Hubo momentos por debajo de 70 (tu mínima fue {s['min']['v']})."
    elif s["high_pct"] >= 25:
        txt += f" Pasaste un rato por encima de 180 (tu máxima fue {s['max']['v']})."
    if s["meals_n"]:
        txt += f" Registraste {s['meals_n']} comida(s), {s['carbs_total']} g de carbohidratos."
    return txt


_BRIEF_SYSTEM = """Sos el copiloto de Orbit. Escribí el RESUMEN DEL DÍA de una persona con diabetes tipo 1.

REGLAS ESTRICTAS E INVIOLABLES:
- Solo DESCRIBÍS y ACOMPAÑÁS con lo que muestran los datos de hoy.
- NUNCA recomiendes dosis, correcciones, qué comer o hacer, ni des indicaciones médicas.
- NUNCA predigas la glucosa futura ni afirmes qué va a pasar.
- 2 a 3 frases, en segunda persona, tono humano, cálido y tranquilo. Sin listas ni emojis.
- No inventes datos que no estén abajo.

DATOS DE HOY:
{context}"""


@bp.route("/api/copilot/drive", endpoint="copilot_drive")
def copilot_drive():
    """ORBIT Drive Mode — estado de seguridad glanceable para conducir.
    Solo glucosa actual + tendencia + frescura. SIN predicción, SIN dosis.
    Devuelve el payload del adapter (mismo contrato para web y superficies nativas)."""
    err = _require_login()
    if err:
        return err
    from drive_mode import build_drive_mode_state, to_live_activity_payload
    state = build_drive_mode_state()
    return jsonify({"ok": True, "drive": to_live_activity_payload(state), "state": state.to_dict()})


@bp.route("/api/copilot/drive/push-token", methods=["POST"], endpoint="copilot_drive_push_token")
def copilot_drive_push_token():
    """Registra el push token de ActivityKit para updates de la Live Activity
    vía APNs en background. Token vacío → des-registro. Solo se usa con
    DRIVE_APNS_ENABLED=1 (ver drive_mode/apns_push.py)."""
    err = _require_login()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    token = (data.get("token") or "").strip().lower()
    if token and (len(token) > 200 or any(c not in "0123456789abcdef" for c in token)):
        return jsonify({"ok": False, "error": "Token inválido"}), 400
    from helpers import _set_setting
    _set_setting("drive_apns_token", token)
    _set_setting("drive_apns_token_updated_at", datetime.now().isoformat())
    return jsonify({"ok": True, "registered": bool(token)})


@bp.route("/api/copilot/brief", endpoint="copilot_brief")
def copilot_brief():
    """Brief diario: métricas calculadas de hoy + narrativa que solo explica."""
    err = _require_login()
    if err:
        return err

    import os
    s = _today_stats()
    ctx = _brief_context(s)
    narrative = _brief_fallback(s)

    # narrativa con el LLM (mismos guardarraíles); si falla, queda el fallback
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if api_key and s["readings_n"]:
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=api_key)
            resp = client.messages.create(
                model="claude-haiku-4-5",
                max_tokens=200,
                system=_BRIEF_SYSTEM.format(context=ctx),
                messages=[{"role": "user", "content": "Escribí mi resumen del día."}],
            )
            txt = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
            if txt:
                narrative = txt
        except Exception:
            pass

    return jsonify({
        "ok": True,
        "greeting": _greeting(datetime.now().hour),
        "date": datetime.now().strftime("%Y-%m-%d"),
        "narrative": narrative,
        "stats": s,
        "updated_at": datetime.now().isoformat(),
    })


@bp.route("/api/copilot/log", methods=["POST"], endpoint="copilot_log")
def copilot_log():
    """Registrar comida / insulina / ejercicio. Escribe a las mismas tablas que
    los formularios actuales (una sola fuente de verdad). Hora local. Estos datos
    alimentan también el research (el SSM los lee como contexto)."""
    err = _require_login()
    if err:
        return err

    from models import db, Meal, InsulinDose, Activity

    data = request.get_json(silent=True) or {}
    cat = data.get("cat")
    now = datetime.now()

    def _f(key, default=0.0):
        try:
            return float(data.get(key) or default)
        except (TypeError, ValueError):
            return default

    try:
        if cat == "comida":
            row = Meal(
                timestamp=now,
                name=(data.get("name") or "Comida").strip()[:200],
                carbs_g=_f("carbs"), fat_g=_f("fat"), protein_g=_f("protein"),
                calories=_f("calories"), notes=(data.get("notes") or None),
            )
        elif cat == "insulina":
            units = _f("units")
            if units <= 0:
                return jsonify({"ok": False, "error": "Unidades inválidas"}), 400
            tipo = data.get("type") if data.get("type") in ("bolus", "basal") else "bolus"
            row = InsulinDose(
                timestamp=now, type=tipo, units=units,
                purpose=(data.get("purpose") or None), notes=(data.get("notes") or None),
            )
        elif cat == "ejercicio":
            row = Activity(
                timestamp=now,
                activity_type=(data.get("activity_type") or "Ejercicio").strip()[:100],
                duration_min=int(_f("duration_min")),
                intensity=(data.get("intensity") or None),
                notes=(data.get("notes") or None),
            )
        else:
            return jsonify({"ok": False, "error": "Categoría no soportada"}), 400

        db.session.add(row)
        db.session.commit()
        return jsonify({"ok": True, "id": row.id})
    except Exception as exc:
        db.session.rollback()
        return jsonify({"ok": False, "error": str(exc)}), 400


@bp.route("/api/copilot/patterns", endpoint="copilot_patterns")
def copilot_patterns():
    """Pantalla Patrones — retrospectivo: TIR semanal, resumen y patrones
    detectados (reusa analizar_patrones). Solo lectura, sin predicción."""
    err = _require_login()
    if err:
        return err

    from models import GlucoseReading
    from utils.patrones_detector import analizar_patrones

    try:
        a = analizar_patrones(days=14) or {}
    except Exception:
        a = {}
    resumen = a.get("resumen") or {}
    patrones = a.get("patrones") or []

    # patrones → formato liviano (observaciones; el médico decide acciones)
    out_patterns = [{
        "tipo": p.get("tipo"),
        "nivel": p.get("nivel", "info"),
        "titulo": p.get("titulo", ""),
        "detalle": p.get("detalle", ""),
        "sugerencia": p.get("sugerencia", ""),
        "frecuencia": p.get("frecuencia"),
    } for p in patrones]

    # TIR por día — últimos 7 días
    DOW = "LMMJVSD"  # lunes..domingo (weekday(): 0=lunes)
    weekly = {"labels": [], "values": []}
    now = datetime.now()
    for i in range(6, -1, -1):
        d0 = (now - timedelta(days=i)).replace(hour=0, minute=0, second=0, microsecond=0)
        d1 = d0 + timedelta(days=1)
        reads = (GlucoseReading.query
                 .filter(GlucoseReading.timestamp >= d0, GlucoseReading.timestamp < d1)
                 .all())
        tir = round(100 * sum(1 for r in reads if LOW <= r.value_mgdl <= HIGH) / len(reads)) if reads else None
        weekly["labels"].append(DOW[d0.weekday()])
        weekly["values"].append(tir)

    # GMI estimada (Glucose Management Indicator ≈ HbA1c estimada).
    # Fórmula estándar (Bergenstal 2018): GMI% = 3.31 + 0.02392 × glucosa media (mg/dL)
    avg = resumen.get("avg")
    gmi = round(3.31 + 0.02392 * avg, 1) if avg else None

    return jsonify({
        "ok": True,
        "resumen": {
            "avg": resumen.get("avg"),
            "cv": resumen.get("cv"),
            "tir": resumen.get("tir"),
            "gmi": gmi,
            "hipo_pct": resumen.get("hipo_pct"),
            "hiper_pct": resumen.get("hiper_pct"),
            "n": resumen.get("n_lecturas"),
            "days": 14,
        },
        "weekly": weekly,
        "patterns": out_patterns,
        "updated_at": datetime.now().isoformat(),
    })


@bp.route("/api/copilot/profile", endpoint="copilot_profile")
def copilot_profile():
    """Pantalla Perfil — datos del usuario, sensor y terapia (solo lectura).
    La edición fina sigue en la app/herramientas; acá se muestra el estado."""
    err = _require_login()
    if err:
        return err

    from models import GlucoseReading
    from helpers import _get_setting

    last = GlucoseReading.query.order_by(GlucoseReading.timestamp.desc()).first()
    sync_raw = _get_setting("libre_last_sync")
    sync_ago = None
    if sync_raw:
        try:
            sync_ago = _hace(datetime.fromisoformat(sync_raw))
        except Exception:
            sync_ago = None

    def _num(k):
        v = _get_setting(k)
        try:
            return float(v) if v not in (None, "") else None
        except (TypeError, ValueError):
            return None

    return jsonify({
        "ok": True,
        "name": _get_setting("user_name") or None,
        "sensor": {
            "last_reading": int(round(last.value_mgdl)) if last else None,
            "last_reading_ago": _hace(last.timestamp) if last else None,
            "source": last.source if last else None,
            "last_sync_ago": sync_ago,
        },
        "config": {
            "isf": _num("isf_manual"),
            "icr": _num("icr"),
            "objetivo": _num("objetivo"),
            "basal_dose": _num("basal_dose_u"),
            "basal_hora": _get_setting("basal_hora"),
            "basal_tipo": _get_setting("basal_tipo"),
        },
    })


# ── Copiloto (chat) — SOLO explica y acompaña. NUNCA recomienda ni predice. ────
_CHAT_SYSTEM = """Sos el copiloto de Orbit, una app para una persona con diabetes tipo 1.
Tu ÚNICO rol es EXPLICAR los datos de la persona y ACOMPAÑARLA con calidez y claridad.

REGLAS ESTRICTAS E INVIOLABLES:
- NUNCA recomiendes dosis de insulina, correcciones, ni cuánto comer o hacer.
- NUNCA des indicaciones médicas ni de tratamiento.
- NUNCA predigas la glucosa futura ni afirmes qué va a pasar.
- Si te piden una dosis, una corrección o "qué hago", decliná con amabilidad y sugerí
  consultarlo con su equipo médico. No es tu rol decidir.
- Solo explicás lo que muestran sus datos (presente y pasado) y acompañás.
- Respondé SIEMPRE en español, en segunda persona, cálido y breve (2 a 4 frases).
- No inventes datos que no estén en el contexto.

CONTEXTO ACTUAL DE LA PERSONA:
{context}"""


def _chat_context():
    """Contexto real y con HISTORIAL para el copiloto (solo lectura): estado
    actual + glucosa 24h + comidas/insulina/actividad recientes + patrones."""
    from models import GlucoseReading, Meal, InsulinDose, Activity
    now = datetime.now()
    L = []

    # ── estado actual ─────────────────────────────────────────────────────
    try:
        from utils.kinetics import get_kinetics_snapshot
        snap = get_kinetics_snapshot(hours_lookback=6) or {}
    except Exception:
        snap = {}
    last = GlucoseReading.query.order_by(GlucoseReading.timestamp.desc()).first()
    cur = []
    if last:
        cur.append(f"glucosa {int(round(last.value_mgdl))} mg/dL (hace {_hace(last.timestamp)})")
    iob = round(snap.get("iob_bolus") or 0.0, 1)
    cob = int(round(snap.get("cob") or 0))
    if iob: cur.append(f"insulina activa {iob} U")
    if cob: cur.append(f"carbos activos {cob} g")
    roc = snap.get("roc") or 0.0
    cur.append("tendencia " + ("subiendo" if roc > 1 else "bajando" if roc < -1 else "estable"))
    L.append("ESTADO ACTUAL: " + ", ".join(cur) + ".")

    # ── glucosa últimas 24h ───────────────────────────────────────────────
    since = now - timedelta(hours=24)
    reads = GlucoseReading.query.filter(GlucoseReading.timestamp >= since).order_by(GlucoseReading.timestamp).all()
    if reads:
        vals = [r.value_mgdl for r in reads]
        tir = round(100 * sum(1 for v in vals if LOW <= v <= HIGH) / len(vals))
        lo_r = min(reads, key=lambda r: r.value_mgdl)
        hi_r = max(reads, key=lambda r: r.value_mgdl)
        L.append(f"GLUCOSA 24h: tiempo en rango {tir}%, mínimo {int(lo_r.value_mgdl)} "
                 f"a las {lo_r.timestamp.strftime('%H:%M')}, máximo {int(hi_r.value_mgdl)} "
                 f"a las {hi_r.timestamp.strftime('%H:%M')}.")

    # ── eventos recientes (48h) ───────────────────────────────────────────
    since48 = now - timedelta(hours=48)

    def _fmt(rows, fn):
        return "; ".join(fn(x) for x in rows) if rows else "ninguno"

    meals = Meal.query.filter(Meal.timestamp >= since48).order_by(Meal.timestamp.desc()).limit(8).all()
    L.append("COMIDAS (48h): " + _fmt(meals, lambda m:
             f"{m.timestamp.strftime('%d/%m %H:%M')} {m.name or 'comida'} "
             f"({int(m.carbs_g or 0)}g CH" + (f", {int(m.fat_g)}g grasa" if (m.fat_g or 0) > 0 else "") + ")"))
    doses = InsulinDose.query.filter(InsulinDose.timestamp >= since48).order_by(InsulinDose.timestamp.desc()).limit(8).all()
    L.append("INSULINA (48h): " + _fmt(doses, lambda d:
             f"{d.timestamp.strftime('%d/%m %H:%M')} {d.units:g}U "
             f"{ {'bolus':'rápida','basal':'basal'}.get(d.type, d.type or '') }"))
    acts = Activity.query.filter(Activity.timestamp >= since48).order_by(Activity.timestamp.desc()).limit(5).all()
    if acts:
        L.append("ACTIVIDAD (48h): " + _fmt(acts, lambda a:
                 f"{a.timestamp.strftime('%d/%m %H:%M')} {a.activity_type or 'ejercicio'} "
                 f"{a.duration_min or 0}min"))

    # ── patrones + resumen 14 días ────────────────────────────────────────
    try:
        from utils.patrones_detector import analizar_patrones
        a = analizar_patrones(days=14) or {}
        res = a.get("resumen") or {}
        if res.get("avg"):
            gmi = round(3.31 + 0.02392 * res["avg"], 1)
            L.append(f"RESUMEN 14 días: promedio {res['avg']} mg/dL, GMI estimada {gmi}%, "
                     f"tiempo en rango {res.get('tir')}%, variabilidad CV {res.get('cv')}%.")
        pats = a.get("patrones") or []
        if pats:
            L.append("PATRONES DETECTADOS: " + " | ".join(
                f"{p.get('titulo')}: {p.get('detalle', '')[:160]}" for p in pats[:3]))
    except Exception:
        pass

    return "\n".join(L) if L else "Sin datos recientes disponibles."


@bp.route("/api/copilot/chat", methods=["POST"], endpoint="copilot_chat")
def copilot_chat():
    err = _require_login()
    if err:
        return err

    import os
    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()
    if not message:
        return jsonify({"ok": False, "error": "Mensaje vacío"}), 400

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return jsonify({"ok": True, "reply": "El copiloto no está disponible ahora mismo."})

    # historial (multi-turno), acotado
    history = data.get("history") or []
    msgs = []
    for m in history[-8:]:
        role = m.get("role")
        content = (m.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            msgs.append({"role": role, "content": content})
    msgs.append({"role": "user", "content": message})

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=320,
            system=_CHAT_SYSTEM.format(context=_chat_context()),
            messages=msgs,
        )
        reply = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
        return jsonify({"ok": True, "reply": reply or "…"})
    except Exception as exc:
        return jsonify({"ok": False, "error": "No pude responder ahora. Intentá de nuevo."}), 502


# ── Estimación de macros desde foto (Claude visión) ──────────────────────────
# Estimación APROXIMADA para registrar; el usuario revisa/edita antes de guardar.
# No alimenta ninguna dosis (el producto no calcula bolo).
_ESTIMATE_PROMPT = (
    "Sos un asistente nutricional. Mirá la foto de la comida y ESTIMÁ sus "
    "macronutrientes. 'carbs' son los carbohidratos TOTALES y 'fiber' la fibra "
    "que contienen (la fibra es parte de los carbohidratos totales). Respondé SOLO "
    "con un JSON válido, sin texto extra, con esta forma exacta:\n"
    '{"name": "nombre corto del plato", "carbs": <g carbohidratos totales>, '
    '"fiber": <g fibra>, "protein": <g proteína>, "fat": <g grasa>, "calories": <kcal>}\n'
    "Los valores numéricos son enteros aproximados. Si no se distingue comida, "
    'devolvé {"name": "", "carbs": 0, "fiber": 0, "protein": 0, "fat": 0, "calories": 0}.'
)


@bp.route("/api/copilot/estimate", methods=["POST"], endpoint="copilot_estimate")
def copilot_estimate():
    """Recibe una foto (data URL base64) y devuelve macros estimados."""
    err = _require_login()
    if err:
        return err

    import os, json, re
    data = request.get_json(silent=True) or {}
    image = data.get("image") or ""
    # data URL → media_type + base64
    m = re.match(r"data:(image/[a-zA-Z0-9.+-]+);base64,(.*)$", image, re.DOTALL)
    if not m:
        return jsonify({"ok": False, "error": "Imagen inválida"}), 400
    media_type, b64 = m.group(1), m.group(2)

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return jsonify({"ok": False, "error": "Estimación no disponible ahora."}), 503

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=200,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64}},
                    {"type": "text", "text": _ESTIMATE_PROMPT},
                ],
            }],
        )
        txt = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
        mjson = re.search(r"\{.*\}", txt, re.DOTALL)
        parsed = json.loads(mjson.group(0)) if mjson else {}

        def _i(k):
            try:
                return max(0, int(round(float(parsed.get(k) or 0))))
            except (TypeError, ValueError):
                return 0

        return jsonify({
            "ok": True,
            "name": (parsed.get("name") or "").strip()[:200],
            "carbs": _i("carbs"), "fiber": _i("fiber"), "protein": _i("protein"), "fat": _i("fat"), "calories": _i("calories"),
        })
    except Exception:
        return jsonify({"ok": False, "error": "No pude estimar la foto. Cargá los datos a mano."}), 502


@bp.route("/api/copilot/history", endpoint="copilot_history")
def copilot_history():
    """Historial de eventos (comida / insulina / ejercicio), ordenado desc."""
    err = _require_login()
    if err:
        return err

    from models import Meal, InsulinDose, Activity
    days = min(int(request.args.get("days", 14)), 90)
    since = datetime.now() - timedelta(days=days)
    events = []

    for m in Meal.query.filter(Meal.timestamp >= since).order_by(Meal.timestamp.desc()).limit(150).all():
        events.append({"cat": "comida", "id": m.id, "title": m.name or "Comida",
                       "badge": f"{int(m.carbs_g)}g" if m.carbs_g else "", "ts": m.timestamp,
                       "data": {"name": m.name or "", "carbs": m.carbs_g or 0,
                                "protein": m.protein_g or 0, "fat": m.fat_g or 0,
                                "calories": m.calories or 0, "notes": m.notes or ""}})
    for d in InsulinDose.query.filter(InsulinDose.timestamp >= since).order_by(InsulinDose.timestamp.desc()).limit(150).all():
        label = {"bolus": "Rápida", "basal": "Basal"}.get(d.type, (d.type or "").capitalize())
        events.append({"cat": "insulina", "id": d.id, "title": f"Insulina {label}".strip(),
                       "badge": f"{d.units:g}U", "ts": d.timestamp,
                       "data": {"units": d.units, "type": d.type, "label": label,
                                "purpose": d.purpose or "", "notes": d.notes or ""}})
    for a in Activity.query.filter(Activity.timestamp >= since).order_by(Activity.timestamp.desc()).limit(150).all():
        events.append({"cat": "ejercicio", "id": a.id, "title": a.activity_type or "Ejercicio",
                       "badge": f"{a.duration_min}m" if a.duration_min else "", "ts": a.timestamp,
                       "data": {"activity_type": a.activity_type or "", "duration_min": a.duration_min or 0,
                                "intensity": a.intensity or "", "notes": a.notes or ""}})

    events.sort(key=lambda e: e["ts"], reverse=True)
    out = [{
        "cat": e["cat"], "id": e["id"], "title": e["title"], "badge": e["badge"],
        "ts": e["ts"].isoformat(),
        "date": e["ts"].strftime("%Y-%m-%d"),
        "time": e["ts"].strftime("%H:%M"),
        "data": e["data"],
    } for e in events[:150]]

    return jsonify({"ok": True, "events": out, "days": days})


@bp.route("/api/copilot/meal/<int:meal_id>", methods=["PUT"], endpoint="copilot_meal_edit")
def copilot_meal_edit(meal_id):
    """Editar una comida del historial (nombre + macros)."""
    err = _require_login()
    if err:
        return err
    from models import db, Meal
    m = Meal.query.get(meal_id)
    if not m:
        return jsonify({"ok": False, "error": "No encontrada"}), 404
    data = request.get_json(silent=True) or {}

    def _f(k, default):
        try:
            return float(data[k]) if k in data and data[k] not in (None, "") else default
        except (TypeError, ValueError):
            return default

    try:
        if "name" in data:
            m.name = (data.get("name") or "Comida").strip()[:200]
        m.carbs_g = _f("carbs", m.carbs_g)
        m.protein_g = _f("protein", m.protein_g)
        m.fat_g = _f("fat", m.fat_g)
        db.session.commit()
        return jsonify({"ok": True})
    except Exception:
        db.session.rollback()
        return jsonify({"ok": False, "error": "No se pudo guardar"}), 400


@bp.route("/api/copilot/entry/<cat>/<int:entry_id>", methods=["DELETE"], endpoint="copilot_entry_delete")
def copilot_entry_delete(cat, entry_id):
    """Eliminar un registro del historial (comida / insulina / ejercicio)."""
    err = _require_login()
    if err:
        return err
    from models import db, Meal, InsulinDose, Activity
    model = {"comida": Meal, "insulina": InsulinDose, "ejercicio": Activity}.get(cat)
    if not model:
        return jsonify({"ok": False, "error": "Categoría inválida"}), 400
    row = model.query.get(entry_id)
    if not row:
        return jsonify({"ok": False, "error": "No encontrado"}), 404
    try:
        db.session.delete(row)
        db.session.commit()
        return jsonify({"ok": True})
    except Exception:
        db.session.rollback()
        return jsonify({"ok": False, "error": "No se pudo eliminar"}), 400


@bp.route("/api/copilot/profile", methods=["PUT"], endpoint="copilot_profile_edit")
def copilot_profile_edit():
    """Editar perfil/terapia (nombre, objetivo, ISF, ICR). Escribe a settings."""
    err = _require_login()
    if err:
        return err
    from helpers import _set_setting
    data = request.get_json(silent=True) or {}

    def _numstr(k):
        v = data.get(k)
        if v in (None, ""):
            return None
        try:
            return str(float(v))
        except (TypeError, ValueError):
            return None

    try:
        if "name" in data:
            _set_setting("user_name", (data.get("name") or "").strip()[:60])
        for key, setting in (("objetivo", "objetivo"), ("isf", "isf_manual"), ("icr", "icr")):
            if key in data:
                val = _numstr(key)
                if val is not None:
                    _set_setting(setting, val)
        return jsonify({"ok": True})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
