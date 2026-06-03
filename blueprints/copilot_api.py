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
    events = []
    for m in Meal.query.order_by(Meal.timestamp.desc()).limit(4).all():
        events.append({"cat": "comida", "title": m.name or "Comida",
                       "badge": f"{int(m.carbs_g)}g" if m.carbs_g else "",
                       "ts": m.timestamp})
    for d in InsulinDose.query.order_by(InsulinDose.timestamp.desc()).limit(4).all():
        label = {"bolus": "Rápida", "basal": "Basal"}.get(d.type, (d.type or "").capitalize())
        events.append({"cat": "insulina", "title": f"Insulina {label}".strip(),
                       "badge": f"{d.units:g}U", "ts": d.timestamp})
    for a in Activity.query.order_by(Activity.timestamp.desc()).limit(4).all():
        events.append({"cat": "ejercicio", "title": a.activity_type or "Ejercicio",
                       "badge": f"{a.duration_min}m" if a.duration_min else "",
                       "ts": a.timestamp})
    events.sort(key=lambda e: e["ts"], reverse=True)
    recent = [{"cat": e["cat"], "title": e["title"], "badge": e["badge"],
               "ago": _hace(e["ts"])} for e in events[:4]]

    return jsonify({
        "ok": True,
        "glucose": glucose,
        "context": {"iob": iob, "cob": cob, "trend": trend},
        "tir_today": tir,
        "series": series,
        "recent": recent,
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

    return jsonify({
        "ok": True,
        "resumen": {
            "avg": resumen.get("avg"),
            "cv": resumen.get("cv"),
            "tir": resumen.get("tir"),
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
    """Contexto compacto y real para el copiloto (solo lectura)."""
    from models import GlucoseReading
    try:
        from utils.kinetics import get_kinetics_snapshot
        snap = get_kinetics_snapshot(hours_lookback=6) or {}
    except Exception:
        snap = {}
    last = GlucoseReading.query.order_by(GlucoseReading.timestamp.desc()).first()
    since = datetime.now() - timedelta(hours=24)
    reads = GlucoseReading.query.filter(GlucoseReading.timestamp >= since).all()
    tir = round(100 * sum(1 for r in reads if LOW <= r.value_mgdl <= HIGH) / len(reads)) if reads else None
    parts = []
    if last:
        parts.append(f"Glucosa actual: {int(round(last.value_mgdl))} mg/dL "
                     f"(hace {_hace(last.timestamp)}).")
    iob = round(snap.get("iob_bolus") or 0.0, 1)
    cob = int(round(snap.get("cob") or 0))
    if iob:
        parts.append(f"Insulina activa: {iob} U.")
    if cob:
        parts.append(f"Carbohidratos activos: {cob} g.")
    roc = snap.get("roc") or 0.0
    parts.append("Tendencia: " + ("subiendo" if roc > 1 else "bajando" if roc < -1 else "estable") + ".")
    if tir is not None:
        parts.append(f"Tiempo en rango (24h): {tir}%.")
    return " ".join(parts) if parts else "Sin datos recientes disponibles."


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
    "macronutrientes. Respondé SOLO con un JSON válido, sin texto extra, con esta "
    "forma exacta:\n"
    '{"name": "nombre corto del plato", "carbs": <g carbohidratos>, '
    '"protein": <g proteína>, "fat": <g grasa>, "calories": <kcal>}\n'
    "Los valores numéricos son enteros aproximados. Si no se distingue comida, "
    'devolvé {"name": "", "carbs": 0, "protein": 0, "fat": 0, "calories": 0}.'
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
            "carbs": _i("carbs"), "protein": _i("protein"), "fat": _i("fat"), "calories": _i("calories"),
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
