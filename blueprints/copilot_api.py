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

    # ── basal — para que el contexto de hoy la tenga en cuenta ───────────
    # Muestra la última aplicada y si la de hoy ya está registrada (recordatorio
    # amable, no alarma). La pauta esperada sale de settings si está cargada.
    basal = None
    try:
        from helpers import _get_setting
        last_basal = (InsulinDose.query.filter(InsulinDose.type == "basal")
                      .order_by(InsulinDose.timestamp.desc()).first())
        hoy0 = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        expected = _get_setting("basal_dose_u")
        basal = {
            "last_units": last_basal.units if last_basal else None,
            "last_ago": _hace(last_basal.timestamp) if last_basal else None,
            "logged_today": bool(last_basal and last_basal.timestamp >= hoy0),
            "expected_units": float(expected) if expected not in (None, "") else None,
            "tipo": _get_setting("basal_tipo") or None,
        }
    except Exception:
        basal = None

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
        "context": {"iob": iob, "cob": cob, "trend": trend, "basal": basal},
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

    # ── comparación: TIR de ayer (día completo) ──────────────────────────
    ayer0 = start - timedelta(days=1)
    vals_ayer = [r.value_mgdl for r in GlucoseReading.query.filter(
        GlucoseReading.timestamp >= ayer0,
        GlucoseReading.timestamp < start).all()]
    tir_ayer = (round(100 * sum(1 for v in vals_ayer if LOW <= v <= HIGH) / len(vals_ayer))
                if len(vals_ayer) >= 24 else None)

    # ── respuesta 2h de las comidas de HOY (retrospectivo, ya ocurrió) ────
    meal_responses = []
    if reads:
        try:
            from utils.copilot_memory import reading_near
            times = [r.timestamp for r in reads]
            vals = [r.value_mgdl for r in reads]
            for m in meals:
                if (now - m.timestamp).total_seconds() < 2.2 * 3600:
                    continue   # aún no pasaron 2h → no hay respuesta que contar
                g0 = reading_near(times, vals, m.timestamp)
                g2 = reading_near(times, vals, m.timestamp + timedelta(hours=2))
                if g0 is not None and g2 is not None:
                    meal_responses.append({
                        "name": m.name or "comida",
                        "time": m.timestamp.strftime("%H:%M"),
                        "carbs": int(m.carbs_g or 0),
                        "delta_2h": int(round(g2 - g0)),
                    })
        except Exception:
            pass

    # ── basal de hoy ──────────────────────────────────────────────────────
    basal_hoy = next((d for d in doses if d.type == "basal"), None)

    return {
        **g,
        "carbs_total": int(round(sum(m.carbs_g or 0 for m in meals))),
        "meals_n": len(meals),
        "insulin_total": round(sum(d.units or 0 for d in doses), 1),
        "bolus_total": round(sum(d.units or 0 for d in doses if d.type == "bolus"), 1),
        "basal_total": round(sum(d.units or 0 for d in doses if d.type == "basal"), 1),
        "activity_n": len(acts),
        "activity_min": int(sum(a.duration_min or 0 for a in acts)),
        "tir_ayer": tir_ayer,
        "meal_responses": meal_responses,
        "basal_today": ({"units": basal_hoy.units,
                         "time": basal_hoy.timestamp.strftime("%H:%M")}
                        if basal_hoy else None),
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
    if s.get("tir_ayer") is not None:
        L.append(f"Ayer el tiempo en rango fue {s['tir_ayer']}%.")
    if s["meals_n"]:
        L.append(f"Comidas: {s['meals_n']} ({s['carbs_total']} g de carbohidratos en total).")
    for mr in (s.get("meal_responses") or [])[:4]:
        sign = "+" if mr["delta_2h"] >= 0 else ""
        L.append(f"Tras {mr['name']} ({mr['time']}, {mr['carbs']}g CH) la glucosa "
                 f"cambió {sign}{mr['delta_2h']} mg/dL a las 2h.")
    if s["insulin_total"]:
        L.append(f"Insulina: {s['insulin_total']} U en total "
                 f"({s['bolus_total']} rápida, {s['basal_total']} basal).")
    if s.get("basal_today"):
        L.append(f"Basal de hoy: {s['basal_today']['units']:g}U a las {s['basal_today']['time']}.")
    else:
        L.append("La basal de hoy todavía no está registrada.")
    if s["activity_min"]:
        L.append(f"Actividad: {s['activity_n']} sesión/es, {s['activity_min']} min.")

    # evolución 7/30 días (memoria) — para que el brief pueda poner el día en contexto
    try:
        from utils.copilot_memory import get_trends
        d7 = (get_trends() or {}).get("d7")
        if d7:
            L.append(f"Última semana: TIR {d7['tir']}%, promedio {d7['avg']} mg/dL.")
    except Exception:
        pass
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
- 3 a 4 frases, en segunda persona, tono humano, cálido y tranquilo. Sin listas ni emojis.
- No inventes datos que no estén abajo.

CÓMO ESCRIBIRLO (en este orden, todo en prosa):
1. Cómo viene el día en una mirada honesta y humana (no repitas todos los números:
   elegí lo que importa).
2. Algo positivo CONCRETO del día (una comida que salió bien, una recuperación,
   estabilidad nocturna, comparación favorable con ayer o con la semana).
3. Si hay algo notable (una hipo, una comida que subió mucho, la basal sin
   registrar), mencionalo con suavidad y SIN decir qué hacer — describir no es
   indicar. Si no hay nada notable, cerrá con calma.

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

    from models import db, Meal, MealComponent, InsulinDose, Activity

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
            # ingredientes (del desglose de la foto) → MealComponent, con ÍG
            # de la base si está. Enriquecen el historial y el research.
            for comp in (data.get("components") or [])[:12]:
                cname = (comp.get("name") or "").strip()[:200]
                if not cname:
                    continue
                gi = None
                try:
                    from utils.nutrition_db import get_gi
                    gi = get_gi(cname)
                except Exception:
                    pass
                def _cf(k):
                    try:
                        return max(0.0, float(comp.get(k) or 0))
                    except (TypeError, ValueError):
                        return 0.0
                row.components.append(MealComponent(
                    name=cname, grams=_cf("grams") or None,
                    carbs_g=_cf("carbs"), fiber_g=_cf("fiber"),
                    protein_g=_cf("protein"), fat_g=_cf("fat"),
                    calories=_cf("calories"), glycemic_index=gi,
                ))
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

        # medir el sesgo de la estimación por foto: si esta comida vino de una
        # estimación reciente, registrar (estimado vs lo que el usuario guardó)
        if cat == "comida":
            try:
                from utils.photo_estimate import log_saved_meal
                log_saved_meal(row.name, row.carbs_g)
            except Exception:
                pass

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
- Tenés MEMORIA: contexto de hoy, evolución de 7/30 días, la respuesta histórica
  a comidas repetidas y notas que la persona te pidió recordar. Usala con
  naturalidad ("la última vez que comiste pizza…"), siempre en pasado
  descriptivo, nunca como predicción.
- Si la persona te pide que recuerdes algo, la nota SE GUARDA AUTOMÁTICAMENTE:
  confirmalo con calidez ("Listo, lo voy a tener presente").
- Tenés CONSULTAS a los datos reales (ejercicio, hipos, franjas horarias,
  comidas, impacto de eventos, relación carbos-insulina). Cuando la pregunta
  lo amerite, usalas y respondé con los NÚMEROS que devuelven — nada de
  sensaciones vagas.
- CASO ESPECIAL relación carbos-insulina: podés contar qué relación usó la
  persona en el pasado y cómo terminó ("cuando cubriste ~1U:10g terminaste en
  rango el 75% de las veces"), pero JAMÁS la conviertas en dosis para una
  comida concreta ("para 60g serían 6U" está PROHIBIDO, aunque la cuenta sea
  trivial). Si piden la dosis, decliná y derivá al equipo médico, mostrando
  solo la historia.
  Si una consulta trae pocos datos, decilo con honestidad ("tengo pocas
  sesiones registradas para afirmarlo"). Los resultados describen el PASADO:
  contalos en pasado ("después de entrenar te bajó ~25"), jamás como promesa
  de lo que va a pasar. Para preguntas analíticas podés extenderte a 5-6
  frases; seguí sin listas salvo que ayuden de verdad.

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

    # ── basal (antes no se tenía en cuenta en el contexto) ────────────────
    try:
        from helpers import _get_setting
        last_basal = (InsulinDose.query.filter(InsulinDose.type == "basal")
                      .order_by(InsulinDose.timestamp.desc()).first())
        btipo = _get_setting("basal_tipo") or ""
        bdose = _get_setting("basal_dose_u") or ""
        seg = []
        if btipo or bdose:
            seg.append(f"pauta {btipo} {bdose}U/día".strip())
        if last_basal:
            seg.append(f"última aplicada {last_basal.units:g}U hace {_hace(last_basal.timestamp)}")
            hoy0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
            seg.append("hoy " + ("ya registrada" if last_basal.timestamp >= hoy0
                                 else "todavía sin registrar"))
        if seg:
            L.append("BASAL: " + ", ".join(seg) + ".")
    except Exception:
        pass

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

    # ── memoria del copiloto: evolución 7/30d, comidas aprendidas, notas ──
    try:
        from utils.copilot_memory import memory_context_lines
        L.extend(memory_context_lines())
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

    # ¿pidió recordar algo? → guardar la nota ANTES de armar el contexto,
    # así el modelo la ve ya guardada y la confirma.
    try:
        from utils.copilot_memory import extract_remember_request, add_note
        note = extract_remember_request(message)
        if note:
            add_note(note)
    except Exception:
        pass

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
        import json as _json
        import anthropic
        from utils.copilot_tools import COPILOT_TOOLS, run_tool

        client = anthropic.Anthropic(api_key=api_key)
        system = _CHAT_SYSTEM.format(context=_chat_context())
        # Sonnet para calidad analítica (las consultas requieren razonar sobre
        # números). Override por env si algún día hay que bajar costo.
        model = os.environ.get("COPILOT_CHAT_MODEL", "claude-sonnet-5")

        def _call():
            return client.messages.create(
                model=model, max_tokens=650, system=system,
                messages=msgs, tools=COPILOT_TOOLS,
            )

        resp = _call()
        used = []
        # Loop de tool use: el modelo decide qué consultar; cap 3 rondas.
        for _ in range(3):
            if resp.stop_reason != "tool_use":
                break
            msgs.append({"role": "assistant", "content": resp.content})
            results = []
            for b in resp.content:
                if getattr(b, "type", None) == "tool_use":
                    used.append(b.name)
                    out = run_tool(b.name, dict(b.input or {}))
                    results.append({"type": "tool_result", "tool_use_id": b.id,
                                    "content": _json.dumps(out, ensure_ascii=False)})
            msgs.append({"role": "user", "content": results})
            resp = _call()

        reply = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
        return jsonify({"ok": True, "reply": reply or "…",
                        "used_data": sorted(set(used))})
    except Exception as exc:
        return jsonify({"ok": False, "error": "No pude responder ahora. Intentá de nuevo."}), 502


# ── Estimación de macros desde foto (v2: componentes + grounding en la base) ──
# Estimación APROXIMADA para registrar; el usuario revisa/edita antes de guardar.
# No alimenta ninguna dosis (el producto no calcula bolo).
# El modelo identifica componentes y porciones (razonando); los macros se anclan
# en utils/nutrition_db cuando el alimento está en la base. Ver utils/photo_estimate.


@bp.route("/api/copilot/estimate", methods=["POST"], endpoint="copilot_estimate")
def copilot_estimate():
    """Recibe una foto (data URL base64) + pista opcional → macros por componente."""
    err = _require_login()
    if err:
        return err

    import os, re
    from utils.photo_estimate import (
        DEFAULT_VISION_MODEL, build_prompt, parse_response,
        ground_components, totals, remember_estimate,
    )

    data = request.get_json(silent=True) or {}
    image = data.get("image") or ""
    hint = (data.get("hint") or "").strip()
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
            model=os.environ.get("COPILOT_VISION_MODEL", DEFAULT_VISION_MODEL),
            max_tokens=900,   # deja lugar al razonamiento por pasos + JSON
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64}},
                    {"type": "text", "text": build_prompt(hint)},
                ],
            }],
        )
        txt = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
        parsed = parse_response(txt) or {}
        comps = ground_components(parsed.get("components") or [])
        tot = totals(comps)
        name = (parsed.get("name") or "").strip()[:200]
        remember_estimate(name, tot["carbs"], tot["fiber"])

        return jsonify({
            "ok": True,
            "name": name,
            "confidence": parsed.get("confidence") or "media",
            "carbs": tot["carbs"], "fiber": tot["fiber"],
            "protein": tot["protein"], "fat": tot["fat"], "calories": tot["calories"],
            # desglose completo: la UI lo muestra y lo manda a /log para que la
            # comida se guarde CON sus ingredientes (MealComponent)
            "breakdown": [{"name": c["name"], "grams": c["grams"],
                           "carbs": c["carbs"], "fiber": c["fiber"],
                           "protein": c["protein"], "fat": c["fat"],
                           "calories": c["calories"], "source": c["source"]}
                          for c in comps],
        })
    except Exception:
        return jsonify({"ok": False, "error": "No pude estimar la foto. Cargá los datos a mano."}), 502


@bp.route("/api/copilot/meals/quick", endpoint="copilot_meals_quick")
def copilot_meals_quick():
    """'Mis comidas': las más repetidas (90 días) + la más reciente, con macros
    medianos — para re-registrar en un tap desde el Registro."""
    err = _require_login()
    if err:
        return err
    from models import Meal
    from utils.quick_meals import group_quick_meals
    since = datetime.now() - timedelta(days=90)
    rows = [{"name": m.name, "carbs": m.carbs_g, "protein": m.protein_g,
             "fat": m.fat_g, "ts": m.timestamp}
            for m in Meal.query.filter(Meal.timestamp >= since).all()]
    return jsonify({"ok": True, "meals": group_quick_meals(rows)})


@bp.route("/api/copilot/food/search", endpoint="copilot_food_search")
def copilot_food_search():
    """Autocompletar desde la base nutricional interna (sin APIs externas).
    Entiende cantidades: '2 tostadas', '200ml leche', '150g arroz'.
    Devuelve CH NETOS (fibra ya descontada) — mismo criterio que la foto."""
    err = _require_login()
    if err:
        return err
    from utils.nutrition_db import NUTRITION_DB, estimar

    q = (request.args.get("q") or "").strip().lower()
    if len(q) < 3:
        return jsonify({"ok": True, "results": []})

    results, seen = [], set()

    def _add(nombre, est):
        if not est or est["key"] in seen:
            return
        seen.add(est["key"])
        results.append({
            "label":   nombre,
            "carbs":   int(round(est["carbs_g"])),        # netos
            "fiber":   int(round(est["fibra_g"])),
            "protein": int(round(est["protein_g"])),
            "fat":     int(round(est["fat_g"])),
            "grams":   est.get("grams"),
            "nota":    est.get("nota") or "",
        })

    # 1. el texto completo, SOLO si trae cantidad ("200ml leche", "2 tostadas")
    #    o es un nombre exacto — evita matches raros con palabras a medias
    if any(ch.isdigit() for ch in q) or q in NUTRITION_DB:
        _add(q, estimar(q))
    # 2. claves de la DB que matchean (prefijo primero, luego substring)
    keys = sorted(NUTRITION_DB.keys())
    matches = [k for k in keys if k.startswith(q)] + \
              [k for k in keys if q in k and not k.startswith(q)]
    for k in matches:
        if len(results) >= 5:
            break
        _add(k, estimar(k))
    return jsonify({"ok": True, "results": results[:5]})


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
    """Editar una comida del historial (nombre + macros + fecha/hora).
    La fecha/hora es editable porque muchas veces se registra tarde — y el
    horario real importa para entender la respuesta glucémica."""
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

        # fecha/hora: "date" (YYYY-MM-DD) y/o "time" (HH:MM) — hora local
        if data.get("date") or data.get("time"):
            try:
                base = m.timestamp
                d = (datetime.strptime(data["date"], "%Y-%m-%d").date()
                     if data.get("date") else base.date())
                t = (datetime.strptime(data["time"], "%H:%M").time()
                     if data.get("time") else base.time())
                nuevo = datetime.combine(d, t)
                now = datetime.now()
                if nuevo > now + timedelta(minutes=5):
                    return jsonify({"ok": False, "error": "La hora no puede ser futura"}), 400
                if nuevo < now - timedelta(days=365):
                    return jsonify({"ok": False, "error": "Fecha demasiado antigua"}), 400
                m.timestamp = nuevo
            except ValueError:
                return jsonify({"ok": False, "error": "Fecha u hora inválida"}), 400

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
