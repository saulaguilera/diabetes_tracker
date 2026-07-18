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

# Intensidad de actividad: valor canónico baja/media/alta en todo el sistema
# (modelo, dashboard clásico, quicklog, datos históricos). Normalizamos cualquier
# variante que llegue del frontend (texto, mayúsculas, inglés) al código canónico.
_INTENSITY_CANON = {
    "baja": "baja", "ligera": "baja", "low": "baja", "light": "baja",
    "media": "media", "moderada": "media", "medium": "media", "moderate": "media",
    "alta": "alta", "intensa": "alta", "high": "alta", "intense": "alta",
}


def _norm_intensity(v):
    if not v:
        return None
    key = str(v).strip().lower()
    return _INTENSITY_CANON.get(key, key)


# idioma de respuesta del copiloto (según el setting ui_lang)
_LANG_NAME = {"es": "español latino neutro (como el de México): SIEMPRE tuteo (tú, tienes, puedes, mira); JAMÁS voseo rioplatense (nunca: vos, tenés, podés, mirá, registrá)", "en": "English", "pt": "português"}


def _ui_lang():
    try:
        from helpers import _get_setting as _gs
        return (_gs("ui_lang") or "es").strip().lower()
    except Exception:
        return "es"


def _copilot_lang():
    return _LANG_NAME.get(_ui_lang(), _LANG_NAME["es"])


def _glucose_unit_label():
    """Etiqueta de la unidad de glucosa elegida por el usuario (para los prompts)."""
    try:
        from helpers import _get_setting as _gs
        return "mmol/L" if (_gs("glucose_unit") or "mgdl") == "mmol" else "mg/dL"
    except Exception:
        return "mg/dL"


def _translate_patterns(patterns, lang):
    """Traduce titulo/detalle/sugerencia de los patrones al idioma de la UI.
    Los patrones los genera el backend en español; si el usuario eligió otro
    idioma, se traducen con un LLM y se cachea por hash del contenido. Con
    lang='es' o sin API key devuelve los patrones sin tocar."""
    if lang == "es" or not patterns:
        return patterns
    import os, json, re, hashlib
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return patterns
    from helpers import _get_setting, _set_setting
    payload = [{"t": p.get("titulo", ""), "d": p.get("detalle", ""),
                "s": p.get("sugerencia", "")} for p in patterns]
    key = "pat_i18n_" + hashlib.md5((lang + json.dumps(payload, ensure_ascii=False)).encode()).hexdigest()[:16]
    cached = _get_setting(key)
    if cached:
        try:
            tr = json.loads(cached)
        except Exception:
            tr = None
        if tr and len(tr) == len(patterns):
            return _apply_pattern_tr(patterns, tr)
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model="claude-haiku-4-5", max_tokens=900,
            system=(f"Traduce al {_LANG_NAME.get(lang, 'English')} los campos t/d/s de este "
                    "JSON (observaciones clínicas de glucosa). Mantén números, unidades "
                    "(mg/dL, g, %, U) y horas idénticos. Devuelve SOLO el JSON con la misma "
                    "estructura, sin texto extra."),
            messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
        )
        txt = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
        m = re.search(r"\[.*\]", txt, re.DOTALL)
        tr = json.loads(m.group(0)) if m else None
        if tr and len(tr) == len(patterns):
            _set_setting(key, json.dumps(tr, ensure_ascii=False))
            return _apply_pattern_tr(patterns, tr)
    except Exception:
        pass
    return patterns


def _apply_pattern_tr(patterns, tr):
    out = []
    for p, x in zip(patterns, tr):
        q = dict(p)
        q["titulo"] = x.get("t") or p.get("titulo", "")
        q["detalle"] = x.get("d") or p.get("detalle", "")
        q["sugerencia"] = x.get("s") or p.get("sugerencia", "")
        out.append(q)
    return out

# etiquetas de contexto: clave canónica → etiqueta visible
TAG_LABELS = {
    "estres": "😰 Estrés", "enfermo": "🤒 Enfermedad", "mal_sueno": "😴 Dormí mal",
    "viaje": "✈️ Viaje", "alcohol": "🍷 Alcohol", "otro": "📍 Contexto",
}


def _require_login():
    # user_id es obligatorio: sin él, el filtro de tenancy no aplica y un
    # request autenticado "a medias" vería datos de todos. Defensa doble.
    if not session.get("logged_in") or not session.get("user_id"):
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

    # datos viejos → dispara sync en background (la próxima carga los ve frescos)
    try:
        from blueprints.sync import maybe_kick_background_sync
        maybe_kick_background_sync()
    except Exception:
        pass

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
        hora_raw = _get_setting("basal_hora")
        try:
            hora = int(round(float(hora_raw))) if hora_raw not in (None, "") else None
        except (TypeError, ValueError):
            hora = None
        basal = {
            "last_units": last_basal.units if last_basal else None,
            "last_ago": _hace(last_basal.timestamp) if last_basal else None,
            "logged_today": bool(last_basal and last_basal.timestamp >= hoy0),
            "expected_units": float(expected) if expected not in (None, "") else None,
            "tipo": _get_setting("basal_tipo") or None,
            "hora": hora,   # hora habitual de aplicación (para el recordatorio)
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
                                "protein": m.protein_g or 0, "fat": m.fat_g or 0,
                                "components": [{"name": cp.name, "grams": cp.grams,
                                                "carbs": round(cp.carbs_g or 0)}
                                               for cp in (m.components or [])]}})
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
def _overnight_stats(now):
    """La noche/madrugada (hoy 00:00–08:00): TIR nocturno, mínima, hipos y alba.
    Es lo que más importa en un brief de la mañana ('¿cómo estuve de noche?')."""
    from models import GlucoseReading
    n0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    n1 = min(now, n0 + timedelta(hours=8))
    reads = (GlucoseReading.query
             .filter(GlucoseReading.timestamp >= n0, GlucoseReading.timestamp < n1,
                     GlucoseReading.is_artifact == False)  # noqa: E712
             .order_by(GlucoseReading.timestamp).all())
    if len(reads) < 6:
        return None
    vals = [r.value_mgdl for r in reads]
    n = len(vals)
    low_events, in_low = 0, False
    for v in vals:
        if v < LOW:
            if not in_low:
                low_events += 1
            in_low = True
        else:
            in_low = False
    dawn = None
    try:
        from utils.copilot_memory import reading_near
        times = [r.timestamp for r in reads]
        g3 = reading_near(times, vals, n0.replace(hour=3))
        g7 = reading_near(times, vals, n0.replace(hour=7))
        if g3 is not None and g7 is not None:
            dawn = int(round(g7 - g3))
    except Exception:
        pass
    return {
        "tir": round(100 * sum(1 for v in vals if LOW <= v <= HIGH) / n),
        "avg": int(round(sum(vals) / n)),
        "min": int(min(vals)),
        "low_events": low_events,
        "dawn_delta": dawn,
    }


def _today_stats():
    """Métricas de HOY (desde la medianoche local) calculadas, no predichas."""
    from models import GlucoseReading, Meal, InsulinDose, Activity, ContextTag
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
                        "protein": int(m.protein_g or 0),
                        "fat": int(m.fat_g or 0),
                        "delta_2h": int(round(g2 - g0)),
                    })
        except Exception:
            pass

    # ── basal de hoy ──────────────────────────────────────────────────────
    basal_hoy = next((d for d in doses if d.type == "basal"), None)

    # ── contexto marcado (desde anoche) + ejercicio de hoy ────────────────
    ctx_since = start - timedelta(hours=12)
    ctx_tags = [{"tag": t.tag, "time": t.timestamp.strftime("%H:%M"), "notes": t.notes or ""}
                for t in ContextTag.query.filter(ContextTag.timestamp >= ctx_since)
                .order_by(ContextTag.timestamp).all()]
    exercise = [{"type": a.activity_type or "ejercicio", "min": a.duration_min or 0,
                 "intensity": a.intensity or "", "time": a.timestamp.strftime("%H:%M")}
                for a in acts]

    # franja horaria del brief → el LLM enmarca distinto a la mañana vs la noche
    tod = "morning" if now.hour < 11 else "evening" if now.hour >= 20 else "day"

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
        "overnight": _overnight_stats(now),
        "context_tags": ctx_tags,
        "exercise": exercise,
        "time_of_day": tod,
    }


def _greeting(hour):
    lang = "es"
    try:
        from helpers import _get_setting as _gs
        lang = (_gs("ui_lang") or "es").strip().lower()
    except Exception:
        pass
    G = {
        "es": ("Buenos días", "Buenas tardes", "Buenas noches"),
        "en": ("Good morning", "Good afternoon", "Good evening"),
        "pt": ("Bom dia", "Boa tarde", "Boa noite"),
    }.get(lang, ("Buenos días", "Buenas tardes", "Buenas noches"))
    return G[0] if hour < 12 else G[1] if hour < 20 else G[2]


def _brief_context(s):
    """Texto compacto de los datos de hoy para alimentar la narrativa."""
    L = []
    try:
        from helpers import _get_setting as _gs
        nombre = (_gs("user_name") or "").strip()
        if nombre:
            L.append(f"La persona se llama {nombre} (puedes usar su nombre con calidez).")
    except Exception:
        pass
    tod = s.get("time_of_day", "day")
    L.append({"morning": "MOMENTO: es la mañana — arranca por cómo estuvo la noche/madrugada "
                          "y pon el foco ahí; el día recién empieza.",
              "evening": "MOMENTO: es la noche — haz un cierre del día completo.",
              "day":     "MOMENTO: es de día — mira cómo viene la jornada hasta ahora."}[tod])

    # ── la noche/madrugada (lo primero que importa a la mañana) ──────────
    ov = s.get("overnight")
    if ov:
        seg = f"NOCHE/MADRUGADA (00–08h): tiempo en rango {ov['tir']}%, promedio {ov['avg']} mg/dL, mínima {ov['min']}"
        if ov["low_events"]:
            seg += f", {ov['low_events']} episodio(s) de hipoglucemia nocturna"
        if ov.get("dawn_delta") is not None and ov["dawn_delta"] >= 20:
            seg += f", subida del alba de +{ov['dawn_delta']} mg/dL entre las 3 y las 7"
        L.append(seg + ".")

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
    # contexto marcado (estrés/enfermedad/mal sueño) — para conectar el porqué
    for ct in (s.get("context_tags") or []):
        L.append(f"CONTEXTO marcado: {ct['tag']} ({ct['time']})"
                 + (f" — {ct['notes']}" if ct["notes"] else "") + ".")
    for ex in (s.get("exercise") or []):
        L.append(f"Ejercicio: {ex['type']} {ex['min']}min "
                 + (f"({ex['intensity']}) " if ex["intensity"] else "") + f"a las {ex['time']}.")
    if s["meals_n"]:
        L.append(f"Comidas: {s['meals_n']} ({s['carbs_total']} g de carbohidratos en total).")
    for mr in (s.get("meal_responses") or [])[:4]:
        sign = "+" if mr["delta_2h"] >= 0 else ""
        macros = f"{mr['carbs']}g CH"
        if mr.get("protein"):
            macros += f", {mr['protein']}g proteína"
        if mr.get("fat"):
            macros += f", {mr['fat']}g grasa"
        L.append(f"Tras {mr['name']} ({mr['time']}, {macros}) la glucosa "
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
    txt = f"Hoy llevas {s['tir']}% del tiempo en rango, con un promedio de {s['avg']} mg/dL."
    if s["low_pct"] >= 4:
        txt += f" Hubo momentos por debajo de 70 (tu mínima fue {s['min']['v']})."
    elif s["high_pct"] >= 25:
        txt += f" Pasaste un rato por encima de 180 (tu máxima fue {s['max']['v']})."
    if s["meals_n"]:
        txt += f" Registraste {s['meals_n']} comida(s), {s['carbs_total']} g de carbohidratos."
    return txt


# ── Perfil de vida: adapta la voz del copiloto (deportista/cuidador/estándar).
# El texto se inyecta en el bloque ESTÁTICO cacheado del prompt: hay una
# variante de cache por (perfil × idioma × unidad), compartida entre mensajes
# y usuarios del mismo combo — el costo marginal sigue siendo ~10%.
_PERFILES_VIDA = {
    "deportista": (
        "PERFIL DE VIDA — DEPORTISTA: la persona entrena en serio y su prioridad "
        "es entender la glucosa alrededor del ejercicio. Dale peso extra a: el "
        "combustible antes/durante/después de las sesiones, la diferencia "
        "aeróbico (tiende a bajar) vs fuerza/intensidad (puede subir por "
        "adrenalina), las hipos tardías hasta 6+ horas post-entreno y nocturnas "
        "tras días de carga, y comparar días de entrenamiento vs descanso. Usa "
        "su vocabulario (sesión, carga, series, fondo) con naturalidad. Cuando "
        "un movimiento de la curva coincida con ejercicio registrado, conéctalo "
        "proactivamente."),
    "cuidador": (
        "PERFIL DE VIDA — CUIDADOR: quien te escribe NO es quien vive con "
        "diabetes: cuida a alguien que la vive (su hijo/a, un familiar). Háblale "
        "al cuidador: los datos son de la persona que cuida — di «su glucosa», "
        "«la noche que tuvo», nunca «tu glucosa». Dale peso extra a: la "
        "seguridad nocturna, las franjas donde no está presente (colegio, "
        "trabajo) y qué patrones conviene comentar con el equipo médico o con "
        "otros cuidadores. Tono: baja la culpa siempre — cuidar es difícil, los "
        "números no son calificaciones; celebra lo que salió bien del cuidado."),
    "estandar": "",
}


def _perfil_vida() -> str:
    try:
        from helpers import _get_setting as _gs
        p = (_gs("perfil_vida") or "estandar").strip().lower()
        return p if p in _PERFILES_VIDA else "estandar"
    except Exception:
        return "estandar"


def _perfil_block() -> str:
    return _PERFILES_VIDA.get(_perfil_vida(), "")


_BRIEF_SYSTEM = """Eres el copiloto de Orbit: escribes el brief diario de una persona
con diabetes tipo 1. Eres como un buen educador en diabetes / nutricionista amigo:
cálido, humano, claro, y con criterio — no un robot que enumera métricas.
{PERFIL}

TU MIRADA (úsala para dar el PORQUÉ, no para indicar):
Razonas desde la nutrición y la endocrinología. Si algo del día tiene una
explicación fisiológica linda de contar, cuéntala en simple: "la subida del alba
(esas hormonas que te despiertan) te llevó la glucosa de X a Y de madrugada";
"como marcaste que dormiste mal, tiene sentido que la noche viniera más
variable — el mal sueño sube el cortisol y baja la sensibilidad a la insulina".
Conecta los datos con el mecanismo, siempre en pasado y descriptivo.

REGLAS INVIOLABLES:
- Solo DESCRIBÍS y ACOMPAÑÁS lo que muestran los datos. NUNCA recomiendas dosis,
  correcciones, qué comer o hacer, ni indicaciones médicas. NUNCA predigas.
- Escribe SIEMPRE en {IDIOMA}, en segunda persona, cálido y tranquilo.
- UNIDAD: la persona usa {UNIDAD} para la glucosa. Expresa TODOS los valores de
  glucosa en {UNIDAD}. Los datos de abajo vienen en mg/dL; si la unidad es
  mmol/L, convierte (mmol/L = mg/dL ÷ 18) y muestra 1 decimal.
- Prosa, sin listas ni bullets. 3 a 5 frases. Usa 1-2 emojis suaves que sumen
  calma (🌙 💙 ✅ ☀️) — eres un acompañante que tranquiliza, no un informe.
- No inventes nada que no esté en los datos.

CÓMO ESCRIBIRLO (adáptalo al MOMENTO del día que dice el contexto):
- A la MAÑANA: abre por cómo estuvo la NOCHE/madrugada (si dormiste protegido,
  si hubo alguna baja, la subida del alba), y cierra con un aliento para el día
  que arranca.
- De DÍA/NOCHE: haz el balance de la jornada.
En cualquier caso: (1) una mirada honesta y humana de lo esencial —elige lo que
importa, no recites todo—; (2) algo POSITIVO concreto (una comida que salió
bien, una recuperación, una noche cuidada, mejor que ayer/la semana); (3) si hay
algo para notar (una hipo, una comida que trepó, la basal sin registrar,
contexto como estrés/enfermedad), nómbralo con suavidad y con su PORQUÉ, sin
decir qué hacer. Si no hay nada notable, cierra con calma y calidez.

DATOS:
{context}"""


@bp.route("/api/copilot/drive", endpoint="copilot_drive")
def copilot_drive():
    """ORBIT Drive Mode — estado de seguridad glanceable para conducir.
    Solo glucosa actual + tendencia + frescura. SIN predicción, SIN dosis.
    Devuelve el payload del adapter (mismo contrato para web y superficies nativas)."""
    err = _require_login()
    if err:
        return err
    # manejando, la frescura importa doble: si el dato está viejo, dispara sync
    try:
        from blueprints.sync import maybe_kick_background_sync
        maybe_kick_background_sync()
    except Exception:
        pass
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
    from helpers import _set_setting, _get_setting
    # GARANTÍA ANTI-DUPLICADOS DEL SERVIDOR: si llega un token nuevo y había
    # otro registrado, la actividad vieja se termina REMOTAMENTE vía APNs
    # (event: end). Así, aunque la dedup del teléfono falle (carrera del
    # ciclo de vida, build viejo instalado), el duplicado muere en segundos
    # — es lo que se veía en CarPlay como contenido doble en la tarjeta.
    viejo = (_get_setting("drive_apns_token") or "").strip()
    if token and viejo and viejo != token:
        try:
            from drive_mode.apns_push import push_drive_end
            r_end = push_drive_end(viejo)
            import logging as _log
            _log.getLogger("drive.apns").info("fin remoto de actividad anterior: %s", r_end)
        except Exception:
            pass
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

    # narrativa con el LLM (mismos guardarraíles); si falla, queda el fallback.
    # Sonnet para calidad narrativa (override por env). Corre si hay lecturas de
    # hoy O de la noche (a la mañana temprano el foco es la madrugada).
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if api_key and (s["readings_n"] or s.get("overnight")):
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=api_key)
            # prompt caching: instrucciones (estables por idioma+unidad) en un
            # bloque cacheado — las mañanas generan el brief de todos los
            # usuarios en secuencia y comparten esta lectura barata.
            _base, _sep, _ = _BRIEF_SYSTEM.partition("DATOS:")
            resp = client.messages.create(
                model=os.environ.get("COPILOT_BRIEF_MODEL", "claude-sonnet-5"),
                # tope con aire: el razonamiento interno de Sonnet 5 consume del
                # mismo tope; 450 podía recortar la narrativa a mitad de frase
                max_tokens=1500,
                system=[
                    {"type": "text",
                     "text": _base.format(IDIOMA=_copilot_lang(), UNIDAD=_glucose_unit_label(),
                                          PERFIL=_perfil_block()),
                     "cache_control": {"type": "ephemeral"}},
                    {"type": "text", "text": "DATOS:\n" + ctx},
                ],
                messages=[{"role": "user", "content": "Escribe mi brief."}],
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

    from models import db, Meal, MealComponent, InsulinDose, Activity, ContextTag

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
                intensity=_norm_intensity(data.get("intensity")),
                notes=(data.get("notes") or None),
            )
        elif cat == "contexto":
            tag = (data.get("tag") or "").strip().lower()[:40]
            if not tag:
                return jsonify({"ok": False, "error": "Falta la etiqueta"}), 400
            row = ContextTag(timestamp=now, tag=tag,
                             notes=(data.get("notes") or "").strip()[:300] or None)
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
    patrones = _translate_patterns(a.get("patrones") or [], _ui_lang())

    # patrones → formato liviano (observaciones; el médico decide acciones)
    out_patterns = [{
        "tipo": p.get("tipo"),
        "nivel": p.get("nivel", "info"),
        "titulo": p.get("titulo", ""),
        "detalle": p.get("detalle", ""),
        "sugerencia": p.get("sugerencia", ""),
        "frecuencia": p.get("frecuencia"),
    } for p in patrones]

    # TIR por día — últimos 7 días (iniciales localizadas)
    DOW = {"es": "LMMJVSD", "en": "MTWTFSS", "pt": "STQQSSD"}.get(_ui_lang(), "LMMJVSD")
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


# ── Notificaciones in-app («🧠 Orbit encontró algo») ──────────────────────────
# Cuando el detector encuentra un patrón NUEVO (tipo no notificado en los
# últimos 30 días), se crea una notificación para la campanita del header.
# El escaneo corre como mucho cada 6h (lo dispara el GET de notificaciones).

_NOTIF_TITLES = {"es": "🧠 Orbit encontró algo",
                 "en": "🧠 Orbit found something",
                 "pt": "🧠 Orbit encontrou algo"}


def _check_new_patterns():
    """Escaneo throttled: detecta patrones nuevos y crea notificaciones."""
    import json as _json
    from models import db, CopilotNotification
    from helpers import _get_setting, _set_setting
    now = datetime.now()

    last = _get_setting("notif_scan_last")
    if last:
        try:
            if now - datetime.fromisoformat(last) < timedelta(hours=6):
                return
        except Exception:
            pass
    _set_setting("notif_scan_last", now.isoformat())

    from utils.patrones_detector import analizar_patrones
    # misma ventana que la pestaña Patrones: la notificación siempre apunta
    # a algo que la persona puede ver ahí
    pats = (analizar_patrones(days=14) or {}).get("patrones") or []
    if not pats:
        return

    try:
        seen = _json.loads(_get_setting("notif_patterns_seen") or "{}")
    except Exception:
        seen = {}

    nuevos = []
    for p in pats:
        tipo = p.get("tipo") or ""
        prev = seen.get(tipo)
        if prev:
            try:
                if now - datetime.fromisoformat(prev) < timedelta(days=30):
                    continue   # ya notificado hace poco
            except Exception:
                pass
        nuevos.append(p)

    if not nuevos:
        return

    lang = _ui_lang()
    titulo_notif = _NOTIF_TITLES.get(lang, _NOTIF_TITLES["es"])
    traducidos = _translate_patterns(nuevos, lang)
    cuerpos = []
    for p in traducidos:
        # cuerpo estilo insight: la estadística + el porqué (sin repetir el
        # título del patrón, que ya dice lo mismo que la primera frase)
        frases = [s.strip() for s in (p.get("detalle") or "").split(". ") if s.strip()]
        cuerpo = (frases[0].rstrip(".") + "." if frases else p.get("titulo", ""))
        if len(frases) > 1 and len(cuerpo) + len(frases[1]) <= 230:
            cuerpo += " " + frases[1].rstrip(".") + "."
        cuerpos.append(cuerpo)
        db.session.add(CopilotNotification(
            created_at=now, kind="pattern",
            title=titulo_notif, body=cuerpo,
        ))
    for p in nuevos:
        seen[p.get("tipo") or ""] = now.isoformat()
    _set_setting("notif_patterns_seen", _json.dumps(seen))
    db.session.commit()

    # push real al teléfono (si APNs está configurado y hay token registrado);
    # UN solo push por escaneo para no ametrallar
    try:
        from drive_mode.notify import push_alert
        if len(cuerpos) == 1:
            push_alert(titulo_notif, cuerpos[0])
        elif cuerpos:
            push_alert(titulo_notif, {
                "es": f"Encontré {len(cuerpos)} patrones nuevos en tus datos — toca la campanita para verlos 🔔",
                "en": f"Found {len(cuerpos)} new patterns in your data — tap the bell to see them 🔔",
            }.get(lang, f"Encontré {len(cuerpos)} patrones nuevos en tus datos 🔔"))
    except Exception:
        pass   # el push nunca debe romper el escaneo


@bp.route("/api/copilot/notifications", endpoint="copilot_notifications")
def copilot_notifications():
    """Lista de notificaciones + conteo de no leídas (dispara el escaneo)."""
    err = _require_login()
    if err:
        return err
    from models import CopilotNotification
    try:
        _check_new_patterns()
    except Exception:
        pass   # el escaneo nunca debe romper la campanita

    rows = (CopilotNotification.query
            .order_by(CopilotNotification.created_at.desc()).limit(30).all())
    return jsonify({
        "ok": True,
        "unread": sum(1 for r in rows if not r.read_at),
        "notifications": [{
            "id": r.id, "kind": r.kind, "title": r.title, "body": r.body or "",
            "time": r.created_at.isoformat(), "read": bool(r.read_at),
        } for r in rows],
    })


@bp.route("/api/copilot/notifications/read", methods=["POST"],
          endpoint="copilot_notifications_read")
def copilot_notifications_read():
    """Marca todas como leídas (al abrir la campanita)."""
    err = _require_login()
    if err:
        return err
    from models import db, CopilotNotification
    now = datetime.now()
    (CopilotNotification.query
     .filter(CopilotNotification.read_at.is_(None))
     .update({CopilotNotification.read_at: now}))
    db.session.commit()
    return jsonify({"ok": True})


@bp.route("/api/copilot/push-token", methods=["POST"], endpoint="copilot_push_token")
def copilot_push_token():
    """La app nativa registra el token push del DISPOSITIVO (notificaciones
    normales; distinto del token de la Live Activity). platform decide el
    canal: iOS → APNs (default, retrocompatible), Android → FCM."""
    err = _require_login()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    token = (data.get("token") or "").strip()
    if not token or len(token) > 300:
        return jsonify({"ok": False, "error": "Token inválido"}), 400
    platform = (data.get("platform") or "ios").strip().lower()
    key = "app_fcm_token" if platform in ("android", "fcm") else "app_apns_token"
    from helpers import _set_setting
    _set_setting(key, token)
    return jsonify({"ok": True})


@bp.route("/api/copilot/notifications/test-push", methods=["POST"],
          endpoint="copilot_test_push")
def copilot_test_push():
    """Push de prueba para verificar la tubería de punta a punta."""
    err = _require_login()
    if err:
        return err
    from drive_mode.notify import push_alert
    lang = _ui_lang()
    res = push_alert(_NOTIF_TITLES.get(lang, _NOTIF_TITLES["es"]), {
        "es": "Notificación de prueba — el push de la campanita funciona ✅",
        "en": "Test notification — the bell push works ✅",
    }.get(lang, "Notificación de prueba ✅"))
    return jsonify({"ok": bool(res.get("ok")), "result": res})


@bp.route("/api/copilot/libre", methods=["GET", "PUT", "DELETE"],
          endpoint="copilot_libre")
def copilot_libre():
    """Conexión del sensor: credenciales de LibreLinkUp del usuario.
    GET → estado; PUT {email, password} → valida contra LibreLinkUp y guarda
    cifrado; DELETE → desconecta. La contraseña jamás se devuelve."""
    err = _require_login()
    if err:
        return err
    from models import db, User
    from utils.crypto_box import encrypt, decrypt
    import os as _os
    u = db.session.get(User, session["user_id"])
    if not u:
        return jsonify({"ok": False, "error": "Usuario no encontrado"}), 404

    def _masked():
        email = decrypt(u.libre_email_enc or "")
        if not email and u.id == 1 and _os.environ.get("LIBRE_EMAIL"):
            email = _os.environ["LIBRE_EMAIL"]   # fallback histórico del usuario 1
        if not email:
            return None
        name, _, dom = email.partition("@")
        return (name[:2] + "•••@" + dom) if dom else email[:2] + "•••"

    if request.method == "GET":
        return jsonify({"ok": True, "connected": bool(_masked()), "email": _masked(),
                        "provider": (u.cgm_provider or "libre")})

    if request.method == "DELETE":
        u.libre_email_enc = None
        u.libre_password_enc = None
        db.session.commit()
        # limpiar el caché de token de Libre de este usuario
        for k in ("libre_token", "libre_base_url", "libre_token_expiry",
                  "libre_account_id", "libre_last_sync", "libre_rate_limited_at",
                  "dexcom_base", "dexcom_session"):
            try:
                from helpers import _set_setting
                _set_setting(k, "")
            except Exception:
                pass
        return jsonify({"ok": True, "connected": False})

    data = request.get_json(silent=True) or {}
    provider = (data.get("provider") or "libre").strip().lower()
    email = (data.get("email") or "").strip()      # libre/dexcom: usuario · nightscout: URL
    password = data.get("password") or ""          # nightscout: token (opcional)
    from utils.cgm_connectors import PROVIDERS, validate as cgm_validate
    if provider not in PROVIDERS:
        return jsonify({"ok": False, "error": "Sensor no soportado"}), 400
    if provider == "libre" and ("@" not in email or not password):
        return jsonify({"ok": False, "error": "Email o contraseña inválidos"}), 400
    if provider == "dexcom" and (not email or not password):
        return jsonify({"ok": False, "error": "Usuario o contraseña inválidos"}), 400
    if provider == "nightscout" and "." not in email:
        return jsonify({"ok": False, "error": "URL inválida"}), 400
    # validar contra el proveedor ANTES de guardar (un intento real)
    err_v = cgm_validate(provider, email, password)
    if err_v:
        return jsonify({"ok": False,
                        "error": f"El proveedor rechazó la conexión: {err_v}"}), 400
    u.cgm_provider = provider
    u.libre_email_enc = encrypt(email)
    u.libre_password_enc = encrypt(password)
    db.session.commit()
    # invalidar caché de token (el próximo sync loguea fresco con esta cuenta)
    from helpers import _set_setting
    for k in ("libre_token", "libre_base_url", "libre_token_expiry", "libre_account_id",
              "dexcom_base", "dexcom_session"):
        _set_setting(k, "")
    return jsonify({"ok": True, "connected": True, "email": _masked(),
                    "provider": provider})


@bp.route("/api/copilot/profile", endpoint="copilot_profile")
def copilot_profile():
    """Pantalla Perfil — datos del usuario, sensor y terapia (solo lectura).
    La edición fina sigue en la app/herramientas; aquí se muestra el estado."""
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

    # Valores OBSERVADOS (PMM bayesiano): el modelo de research está calibrado
    # con los datos del usuario #1 y sus tablas NO son multi-tenant — mostrarlo
    # a otro usuario sería filtrar la fisiología de otra persona. Gate duro.
    observed = _observed_params() if session.get("user_id") == 1 else {}

    return jsonify({
        "ok": True,
        "name": _get_setting("user_name") or None,
        "perfil_vida": _perfil_vida(),
        # onboarding: usuarios nuevos completan nombre/objetivo/basal + sensor
        "onboarded": bool(_get_setting("onboarding_done")) or session.get("user_id") == 1,
        "sensor": {
            "last_reading": int(round(last.value_mgdl)) if last else None,
            "last_reading_ago": _hace(last.timestamp) if last else None,
            "source": last.source if last else None,
            "last_sync_ago": sync_ago,
            # último error de sync (p. ej. LibreLinkUp sin sensores vinculados)
            # para que el usuario se auto-diagnostique desde su Perfil
            "sync_error": _sync_error(),
        },
        "config": {
            "isf": _num("isf_manual"),
            "icr": _num("icr"),
            "objetivo": _num("objetivo"),
            "basal_dose": _num("basal_dose_u"),
            "basal_hora": _get_setting("basal_hora"),
            "basal_tipo": _get_setting("basal_tipo"),
            "glucose_unit": _get_setting("glucose_unit") or "mgdl",
        },
        # Solo referencia descriptiva — jamás se auto-aplica a la terapia.
        "observed": observed,
    })


def _sync_error():
    """Error del último intento de sync del usuario actual, si lo hubo."""
    try:
        import json as _j
        from helpers import _get_setting as _gs_sync
        raw = _gs_sync("sync_last")
        if raw:
            return (_j.loads(raw).get("error") or None)
    except Exception:
        pass
    return None


def _observed_params():
    """ISF/ICR aprendidos por el PMM (si tienen datos reales). Nunca levanta."""
    out = {}
    try:
        from pmm.core.parameter_store import get_isf_now, get_icr_now
        isf = get_isf_now()
        icr = get_icr_now()
        if isf.get("source") != "prior" and isf.get("n_obs", 0) >= 3:
            out["isf"] = {"mu": round(isf["mu"], 1), "n": isf["n_obs"]}
        if icr.get("source") != "prior" and icr.get("n_obs", 0) >= 3:
            out["icr"] = {"mu": round(icr["mu"], 1), "n": icr["n_obs"]}
    except Exception:
        pass
    return out


# ── Copiloto (chat) — SOLO explica y acompaña. NUNCA recomienda ni predice. ────
_CHAT_SYSTEM = """Eres el copiloto de Orbit, una app para una persona con diabetes tipo 1.
Tu ÚNICO rol es EXPLICAR los datos de la persona y ACOMPAÑARLA con calidez y claridad.

TU FORMACIÓN: razonas con DOS miradas expertas y complementarias, y cuando
ayuda a entender, ofreces las dos:
- NUTRICIÓN: índice glucémico y carga glucémica, fibra, cómo la grasa y la
  proteína retrasan y prolongan la absorción (el "efecto pizza"), alcohol e
  hipoglucemias tardías, tamaño de porción.
- ENDOCRINOLOGÍA / diabetología: fenómeno del alba, cortisol y estrés,
  ejercicio aeróbico (baja) vs fuerza (puede subir), sensibilidad a la
  insulina, resistencia transitoria por enfermedad, hormonas de
  contrarregulación.
CÓMO usar esa formación: para EXPLICAR EL PORQUÉ conectando SUS datos con el
mecanismo ("desde lo nutricional, la grasa de la pizza retrasó el vaciado
gástrico; desde lo hormonal, además cenaste tarde y el cortisol nocturno pudo
sumar — por eso la subida llegó recién 3 horas después").
SIMPLICIDAD ante todo: lenguaje llano, sin jerga innecesaria; si usas un
término técnico, explícalo en la misma frase con palabras simples ("el vaciado
gástrico, o sea qué tan rápido la comida sale del estómago"). Mejor una
explicación clara que suene humana que una clase magistral.

SÉ ÚTIL Y EDUCATIVO (no te cierres en seco):
Puedes y DEBES responder preguntas GENERALES de nutrición, ejercicio y fisiología
con generosidad, como lo haría un buen nutricionista/educador en diabetes amigo.
Ejemplos de lo que SÍ respondes a fondo:
- "¿la manzana sirve como energía antes del gym?" → Sí, explica qué aporta en
  general (una manzana ~20-25g de carbohidratos, algo de fibra que suaviza la
  subida, agua), para qué suele servir comer algo antes de entrenar, y la
  diferencia aeróbico (tiende a bajar la glucosa) vs fuerza (puede subirla).
- combinaciones generales ("carbohidrato + algo de proteína suele dar energía
  más sostenida"), índice glucémico, por qué la fibra ayuda, etc.
Cuando tengas SUS datos relevantes, súmalos ("además, la última vez la manzana
te subió ~36 a la hora, y tus entrenamientos de fuerza te bajaron ~18 a las 2h").
Responde con calidez y ejemplos concretos, pero con ECONOMÍA: educar no es
alargar — es elegir lo que de verdad le sirve saber y decirlo claro.

DÓNDE SÍ PARÁS (esto es de su equipo médico, no tuyo):
- Dosis de insulina o correcciones con NÚMEROS concretos, aunque la cuenta sea trivial.
- Reglas clínicas personalizadas por umbral de glucosa ("si estás en 180 haz X",
  "corrige ahora", "no comas carbohidratos", protocolos de cetonas).
- Cambios de terapia (ISF/ICR/basal) o un "deberías" de tratamiento.
- Predecir la glucosa futura o afirmar qué VA a pasar.
Cuando la pregunta caiga aquí, NO cortes en seco: responde todo lo GENERAL y
educativo que sí puedes + sus datos, y SOLO la decisión personalizada (la
cantidad exacta para ti hoy, la dosis, el umbral) derívala con calidez a su
equipo. La diferencia: "una manzana es una buena fuente de energía antes de
entrenar" (SÍ, general) vs. "vos come una manzana ahora" o "si estás en 180 no
comas" (NO, es indicación personalizada). Nunca un "no puedo" pelado.

SI EL CONTEXTO DICE "USUARIO NUEVO" (sin datos todavía):
Cambia el sombrero: eres el anfitrión, no el analista. En tu primera respuesta:
saluda por su nombre si lo tienes, explica EN SIMPLE cómo funciona Orbit
(1. conecta tu sensor en Perfil para que la glucosa entre sola — o registrala
a mano; 2. registra comidas, insulina y ejercicio en Registro — a la comida
puedes sacarle una foto y estimo los carbohidratos; 3. con unos días de datos
te armo el brief diario, encuentro patrones y te aviso con la campanita), y
cierra ofreciendo ayuda con calidez ("si te trabas con algo de la app,
pregúntame"). NO uses las consultas a datos (no hay nada que consultar), no
inventes datos, y si pregunta algo general de diabetes/nutrición responde
normalmente. Sugiere el primer paso concreto según el contexto: si el sensor
NO está conectado, ese es el paso 1; si ya está, que registre su primera
comida.

{PERFIL}

REGLAS DE ESTILO:
- ARQUITECTURA DE LA RESPUESTA — sigue SIEMPRE este orden, sin excepción:
  (1) LA RESPUESTA: tu primera frase contesta directamente lo que se
      preguntó o da el hallazgo clave. Sin preámbulos ("¡buena pregunta!",
      "veamos", "analicemos") ni contexto antes de la respuesta.
  (2) EL PORQUÉ, EN ORDEN: el razonamiento que la sostiene, como UN hilo.
      Si narras hechos → orden cronológico estricto (jamás saltes de la
      tarde a la mañana y de vuelta). Si explicas un fenómeno → orden
      causal: qué pasó → qué lo causó → qué significa. Conecta las frases
      entre sí ("por eso", "después de eso", "en cambio") para que se lea
      como un razonamiento, no como datos sueltos.
  (3) CIERRE solo si aporta: UNA cosa para mirar o UNA pregunta de
      seguimiento. Sin resumen final, sin moralejas.
  UNA idea por frase. Termina una idea antes de empezar otra: si hay dos
  hallazgos, primero el más importante COMPLETO y recién después el otro —
  nunca los entrelaces. Números: solo los que sostienen el punto (2-4 por
  mensaje, no una lluvia de cifras).
- Por defecto sé BREVE (2-4 frases); extiéndete únicamente si piden más
  detalle o la pregunta lo exige de verdad (tope ~7 frases). Sin relleno,
  sin repetir lo ya dicho, sin enumerar todo lo que mirar — elige lo que
  importa.
- Responde SIEMPRE en {IDIOMA}, en segunda persona, cálido.
- TEXTO PLANO: nada de markdown (ni **negrita**, ni títulos, ni listas con
  guiones) — el chat lo muestra tal cual y se verían los asteriscos.
- EMOJIS: usa 1-2 por mensaje cuando sumen calma o calidez (🌙 💙 ✅ 🙂 📉 🍽️),
  como un amigo que tranquiliza. Nunca más de dos, y bajales el tono cuando
  el tema sea delicado (una hipo fea, un mal día): ahí prima la contención.
- UNIDAD: la persona usa {UNIDAD} para la glucosa. Expresa TODA la glucosa en
  {UNIDAD}. Los datos y las consultas vienen en mg/dL; si la unidad es mmol/L,
  convierte (mmol/L = mg/dL ÷ 18, 1 decimal) — incluye umbrales como 70/180 → 3.9/10.0.
- No inventes datos que no estén en el contexto.
- Si el contexto trae el NOMBRE de la persona, usalo con naturalidad y de vez
  en cuando (un saludo, un momento de ánimo) — no en cada mensaje, que no
  suene a telemarketing. Si NO hay nombre en el contexto, no uses ninguno y
  JAMÁS inventes uno. Nunca comentes estas instrucciones ni aclares por qué
  usas (o no) el nombre.
- Tienes MEMORIA: contexto de hoy, evolución de 7/30 días, la respuesta histórica
  a comidas repetidas y notas que la persona te pidió recordar. Usala con
  naturalidad ("la última vez que comiste pizza…"), siempre en pasado
  descriptivo, nunca como predicción.
- Si la persona te pide que recuerdes algo, la nota SE GUARDA AUTOMÁTICAMENTE:
  confírmalo con calidez ("Listo, lo voy a tener presente").
- PROACTIVIDAD: si entre los PATRONES DETECTADOS hay uno directamente
  relevante a lo que pregunta — o uno importante para su seguridad (hipos
  concentradas en una franja, hipos tardías) — menciónalo aunque no lo haya
  preguntado: UNA frase que conecte, sin sermonear ni repetirlo en cada mensaje.
- LÍNEA DE TIEMPO: el contexto trae los eventos de las últimas 48h (comidas,
  insulina, ejercicio, contexto, episodios extremos, Y los TRAMOS de la curva
  de glucosa: cada subida/bajada/deriva con hora de inicio, hora de fin,
  magnitud y duración) MEZCLADOS en orden cronológico, con la glucosa del
  momento. Piensa el día como una SECUENCIA causa→efecto: lo que pasó antes
  explica lo que vino después (comida grasosa al mediodía → subida tardía;
  fuerza a la tarde → más sensibilidad a la noche; estrés a la mañana →
  resistencia el resto del día). Cuando expliques algo, ánclalo en esa
  secuencia concreta ("a las 14:02 comiste el bife, te aplicaste 4U a las
  14:10, y para las 16:00 estabas en 180") en vez de tratar cada dato como
  un hecho suelto.
- ANALIZA LA SERIE TEMPORAL, no solo los extremos: conecta cada tramo de la
  curva con los eventos que lo preceden por horario — una comida suele
  explicar la subida que empieza 15–60 min después; un bolo, la bajada
  1–2 h después; el ejercicio aeróbico, una bajada durante o después (hasta
  6 h); una subida de madrugada sin eventos apunta a alba o rebote. Fíjate
  también en la VELOCIDAD (+80 en 50 min es distinto que +80 en 4 h) y en lo
  que NO se movió (una noche plana en rango vale la pena celebrarla). Si un
  movimiento no tiene ningún evento que lo explique, dilo con honestidad y
  pregunta qué pasó a esa hora ("¿comiste algo cerca de las 17:30?").
- Tienes CONSULTAS a los datos reales (ejercicio, hipos, franjas horarias,
  comidas, impacto de eventos, relación carbos-insulina, impacto de contexto
  como estrés/enfermedad/mal sueño). Cuando la pregunta lo amerite, úsalas y
  responde con los NÚMEROS que devuelven — nada de sensaciones vagas. Lo ideal:
  el NÚMERO (de la consulta) + el PORQUÉ (de tu formación). Ej: "los días que
  marcaste estrés tu promedio fue 12 mg/dL más alto — tiene sentido, el
  cortisol que libera el estrés reduce la sensibilidad a la insulina".
- CASO ESPECIAL relación carbos-insulina: puedes contar qué relación usó la
  persona en el pasado y cómo terminó ("cuando cubriste ~1U:10g terminaste en
  rango el 75% de las veces"), pero JAMÁS la conviertas en dosis para una
  comida concreta ("para 60g serían 6U" está PROHIBIDO, aunque la cuenta sea
  trivial). Si piden la dosis, declina y deriva al equipo médico, mostrando
  solo la historia.
  Si una consulta trae pocos datos, dilo con honestidad ("tengo pocas
  sesiones registradas para afirmarlo"). Los resultados describen el PASADO:
  cuéntalos en pasado ("después de entrenar te bajó ~25"), jamás como promesa
  de lo que va a pasar. Para preguntas analíticas: el NÚMERO clave + el
  porqué, en 3-5 frases; sin listas salvo que ayuden de verdad.

GUÍA DE USO DE ORBIT (misma info que Perfil → Centro de ayuda). Cuando
pregunten cómo usar la app o conectar un sensor, responde con ESTOS pasos
(en el idioma de la persona) — jamás inventes menús o pantallas que no
están aquí. Si algo no aparece en esta guía, dilo y sugiere escribir a
sauvlogs@gmail.com.
- Conectar FreeStyle Libre: Orbit se conecta vía LibreLinkUp (la app de
  seguidores), NO con la app LibreLink principal. Pasos: (1) en LibreLink:
  Menú → Compartir → Aplicaciones conectadas → LibreLinkUp → invitar un
  correo (puede ser uno tuyo distinto); (2) descargar la app LibreLinkUp,
  entrar con ese correo y aceptar la invitación; (3) en Orbit (Perfil → Tu
  sensor → Conectar, o durante el registro) elegir FreeStyle Libre y poner
  el email y contraseña de ESA cuenta LibreLinkUp.
- Conectar Dexcom (G6/G7): en la app de Dexcom activar Compartir (Share)
  con al menos un seguidor activo; en Orbit elegir Dexcom y usar el usuario
  y contraseña de la cuenta Dexcom PRINCIPAL (no la del seguidor).
- Conectar Nightscout: en Orbit elegir Nightscout, poner la URL del sitio
  (sin https:// está bien) y el token de acceso solo si el sitio es privado
  (Admin tools → Subjects). Trae también los bolos de la bomba (Loop,
  AndroidAPS, Omnipod DIY) automáticamente.
- Registrar: pestaña Registro → comida, insulina, ejercicio o contexto
  (estrés, enfermedad, dormir mal…). A las comidas se les puede sacar foto
  y Orbit estima los carbohidratos (ajustables). Todo editable.
- Patrones y avisos: Orbit busca patrones solo y avisa con la campanita 🔔;
  la pestaña Patrones tiene el detalle. El brief matutino llega como
  notificación si están habilitadas (en iPhone: Ajustes → Orbit).
- Orbit Drive: botón arriba a la derecha; muestra la glucosa en la pantalla
  de bloqueo/Dynamic Island. iOS lo apaga a las ~8 horas: reabrir Drive lo
  reactiva.
- Perfil: editar objetivo/basal/ISF/ICR (botón Editar), cambiar idioma y
  unidad (mg/dL ↔ mmol/L), descargar reporte PDF para la consulta médica,
  desconectar/reconectar el sensor, Centro de ayuda, cerrar sesión.
- Problemas frecuentes: si no llegan lecturas → revisar credenciales, que
  el seguidor (LibreLinkUp) o Share (Dexcom) siga activo y que el teléfono
  del sensor tenga internet; Orbit sincroniza cada ~5 min. Credenciales
  equivocadas → Perfil → Sensor → Desconectar y volver a conectar.

CONTEXTO ACTUAL DE LA PERSONA:
{context}"""


def _chat_context():
    """Contexto real y con HISTORIAL para el copiloto (solo lectura): estado
    actual + glucosa 24h + comidas/insulina/actividad recientes + patrones."""
    from models import GlucoseReading, Meal, InsulinDose, Activity
    now = datetime.now()
    L = []

    # ── quién es (nombre del perfil) ──────────────────────────────────────
    try:
        from helpers import _get_setting as _gs
        nombre = (_gs("user_name") or "").strip()
        if nombre:
            L.append(f"NOMBRE: la persona se llama {nombre}.")
    except Exception:
        pass

    # ── ¿usuario nuevo sin datos? → el copiloto guía en vez de analizar ───
    try:
        n_reads = GlucoseReading.query.count()
        n_meals = Meal.query.count()
        if n_reads == 0 and n_meals == 0:
            from flask import session as _sess
            from models import User as _User
            _u = db.session.get(_User, _sess.get("user_id")) if _sess.get("user_id") else None
            sensor_ok = bool(_u and _u.libre_email_enc)
            L.append(
                "USUARIO NUEVO: todavía NO hay ningún dato registrado (ni glucosa "
                "ni comidas). Sensor CGM: "
                + ("conectado — la glucosa va a empezar a entrar sola en minutos."
                   if sensor_ok else
                   "NO conectado (se conecta en Perfil → Tu sensor).")
            )
            return "\n".join(L)   # sin datos, el resto del contexto no aporta
    except Exception:
        pass

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

    # ── glucosa: lecturas 48h (alimentan resumen 24h + línea de tiempo) ───
    since48 = now - timedelta(hours=48)
    reads48 = (GlucoseReading.query.filter(GlucoseReading.timestamp >= since48)
               .order_by(GlucoseReading.timestamp).all())
    reads = [r for r in reads48 if r.timestamp >= now - timedelta(hours=24)]
    if reads:
        vals = [r.value_mgdl for r in reads]
        tir = round(100 * sum(1 for v in vals if LOW <= v <= HIGH) / len(vals))
        lo_r = min(reads, key=lambda r: r.value_mgdl)
        hi_r = max(reads, key=lambda r: r.value_mgdl)
        L.append(f"GLUCOSA 24h: tiempo en rango {tir}%, mínimo {int(lo_r.value_mgdl)} "
                 f"a las {lo_r.timestamp.strftime('%H:%M')}, máximo {int(hi_r.value_mgdl)} "
                 f"a las {hi_r.timestamp.strftime('%H:%M')}.")

    # ── LÍNEA DE TIEMPO unificada (48h) ───────────────────────────────────
    # Comidas, insulina, ejercicio, contexto y excursiones de glucosa MEZCLADOS
    # en orden cronológico, con la glucosa del momento al lado de cada evento.
    # Así el copiloto lee el día como secuencia causa→efecto, no como listas
    # sueltas por categoría.
    def _g_at(ts):
        """Lectura más cercana a ±12 min del evento (para anclarlo en la curva)."""
        best = None
        for r in reads48:
            d = abs((r.timestamp - ts).total_seconds())
            if d <= 720 and (best is None or d < best[0]):
                best = (d, r.value_mgdl)
        return f" · glucosa {int(best[1])}" if best else ""

    def _macros(m):
        # carbos + proteína + grasa (las tres se registran; el copiloto necesita
        # las tres para razonar el 'efecto pizza' y la subida tardía por proteína)
        parts = [f"{int(m.carbs_g or 0)}g CH"]
        if (m.protein_g or 0) > 0:
            parts.append(f"{int(m.protein_g)}g proteína")
        if (m.fat_g or 0) > 0:
            parts.append(f"{int(m.fat_g)}g grasa")
        return ", ".join(parts)

    eventos = []   # (timestamp, texto)
    meals = Meal.query.filter(Meal.timestamp >= since48).order_by(Meal.timestamp).all()[-10:]
    for m in meals:
        eventos.append((m.timestamp,
                        f"comida: {m.name or 'comida'} ({_macros(m)}){_g_at(m.timestamp)}"))
    doses = InsulinDose.query.filter(InsulinDose.timestamp >= since48).order_by(InsulinDose.timestamp).all()[-10:]
    for d in doses:
        tipo = {"bolus": "rápida", "basal": "basal"}.get(d.type, d.type or "")
        eventos.append((d.timestamp, f"insulina: {d.units:g}U {tipo}{_g_at(d.timestamp)}"))
    acts = Activity.query.filter(Activity.timestamp >= since48).order_by(Activity.timestamp).all()[-6:]
    for a in acts:
        inten = f", intensidad {a.intensity}" if a.intensity else ""
        eventos.append((a.timestamp,
                        f"ejercicio: {a.activity_type or 'ejercicio'} "
                        f"{a.duration_min or 0}min{inten}{_g_at(a.timestamp)}"))
    from models import ContextTag
    tags = ContextTag.query.filter(ContextTag.timestamp >= since48).order_by(ContextTag.timestamp).all()[-8:]
    for t in tags:
        nota = f" ({t.notes[:60]})" if t.notes else ""
        eventos.append((t.timestamp, f"contexto: {t.tag}{nota}"))

    # excursiones de glucosa como eventos propios (episodios colapsados)
    def _episodios(pred):
        eps, cur = [], []
        for r in reads48:
            if pred(r.value_mgdl):
                cur.append(r)
            elif cur:
                eps.append(cur); cur = []
        if cur:
            eps.append(cur)
        return eps
    for ep in _episodios(lambda v: v < LOW)[-6:]:
        nadir = min(r.value_mgdl for r in ep)
        eventos.append((ep[0].timestamp,
                        f"glucosa BAJA: mínimo {int(nadir)} "
                        f"({ep[0].timestamp.strftime('%H:%M')}–{ep[-1].timestamp.strftime('%H:%M')})"))
    for ep in _episodios(lambda v: v > 250)[-4:]:
        pico = max(r.value_mgdl for r in ep)
        eventos.append((ep[0].timestamp,
                        f"glucosa muy alta: pico {int(pico)} "
                        f"({ep[0].timestamp.strftime('%H:%M')}–{ep[-1].timestamp.strftime('%H:%M')})"))

    # tramos de la CURVA (subidas/bajadas con hora, magnitud y duración) como
    # eventos propios: el copiloto ve la forma de la serie temporal completa,
    # no solo los extremos, y puede conectar cada movimiento con lo que lo
    # precede (comida → subida, bolo → bajada, madrugada sin eventos → alba).
    try:
        from utils.glucose_curve import segmentos, huecos, duracion_txt
        pts = [(r.timestamp, r.value_mgdl) for r in reads48]
        for s in segmentos(pts)[-18:]:
            signo = "+" if s["delta"] > 0 else "−"
            etiq = {"subida": "SUBIDA", "bajada": "bajada",
                    "deriva": "deriva lenta"}[s["tipo"]]
            eventos.append((s["t0"],
                            f"curva: {etiq} {int(s['v0'])}→{int(s['v1'])} "
                            f"({signo}{abs(int(s['delta']))} en {duracion_txt(s['minutos'])}, "
                            f"termina {s['t1'].strftime('%H:%M')})"))
        for g0, g1 in huecos(pts)[-4:]:
            eventos.append((g0, f"sensor sin datos {g0.strftime('%H:%M')}–{g1.strftime('%H:%M')}"))
    except Exception:
        pass

    if eventos:
        eventos.sort(key=lambda e: e[0])
        tl, dia_prev = ["LÍNEA DE TIEMPO (48h, en orden cronológico):"], None
        for ts, txt in eventos:
            if ts.date() != dia_prev:
                dia_prev = ts.date()
                delta = (now.date() - dia_prev).days
                etiq = {0: "hoy", 1: "ayer", 2: "anteayer"}.get(delta, "")
                tl.append(f"[{etiq} {dia_prev.strftime('%d/%m')}]".replace("[ ", "["))
            tl.append(f"  {ts.strftime('%H:%M')} {txt}")
        L.append("\n".join(tl))
    else:
        L.append("LÍNEA DE TIEMPO (48h): sin eventos registrados.")

    # ── etiquetas de contexto (7 días): estrés/enfermedad/sueño/viaje ─────
    try:
        from models import ContextTag
        tags = (ContextTag.query.filter(ContextTag.timestamp >= now - timedelta(days=7))
                .order_by(ContextTag.timestamp.desc()).limit(10).all())
        if tags:
            L.append("CONTEXTO ETIQUETADO (7 días): " + "; ".join(
                f"{t.timestamp.strftime('%d/%m %H:%M')} {t.tag}"
                + (f" ({t.notes[:60]})" if t.notes else "") for t in tags))
    except Exception:
        pass

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
                f"{p.get('titulo')}: {p.get('detalle', '')[:160]}" for p in pats[:6]))
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
        # ── Prompt caching ────────────────────────────────────────────────
        # El system se parte en 2 bloques: las INSTRUCCIONES (grandes y
        # estables — solo varían por idioma+unidad, así que se comparten
        # entre usuarios y entre mensajes) llevan cache_control; el CONTEXTO
        # (datos del usuario, cambia con cada lectura) queda fuera del cache.
        # Con esto, cada mensaje/ronda relee las instrucciones a ~10% del
        # costo en vez de pagarlas completas.
        _base, _sep, _ = _CHAT_SYSTEM.partition("CONTEXTO ACTUAL DE LA PERSONA:")
        system = [
            {"type": "text",
             "text": _base.format(IDIOMA=_copilot_lang(), UNIDAD=_glucose_unit_label(),
                                  PERFIL=_perfil_block()),
             "cache_control": {"type": "ephemeral"}},
            {"type": "text",
             "text": "CONTEXTO ACTUAL DE LA PERSONA:\n" + _chat_context()},
        ]
        # Sonnet para calidad analítica (las consultas requieren razonar sobre
        # números). Override por env si algún día hay que bajar costo.
        model = os.environ.get("COPILOT_CHAT_MODEL", "claude-sonnet-5")

        def _con_marca_de_cache(mensajes):
            """Copia msgs marcando el último bloque del último mensaje con
            cache_control → en las rondas de tools, todo el prefijo (tools +
            system + historia previa) se relee del cache. Solo se marca si el
            contenido es nuestro (str o dicts); los bloques del SDK se dejan."""
            if not mensajes:
                return mensajes
            out = list(mensajes)
            ult = dict(out[-1])
            c = ult.get("content")
            if isinstance(c, str):
                ult["content"] = [{"type": "text", "text": c,
                                   "cache_control": {"type": "ephemeral"}}]
            elif isinstance(c, list) and c and isinstance(c[-1], dict):
                nuevos = list(c)
                nuevos[-1] = {**nuevos[-1], "cache_control": {"type": "ephemeral"}}
                ult["content"] = nuevos
            else:
                return mensajes
            out[-1] = ult
            return out

        def _call(force_text=False):
            # max_tokens es un TOPE (no un gasto): en Sonnet 5 el razonamiento
            # interno automático consume del mismo tope que el texto visible,
            # así que 900 recortaba respuestas a mitad de frase (o se comía TODO
            # el tope pensando y la respuesta salía vacía → el "…"). 4000 da aire.
            kw = {"tool_choice": {"type": "none"}} if force_text else {}
            return client.messages.create(
                model=model, max_tokens=4000, system=system,
                messages=_con_marca_de_cache(msgs), tools=COPILOT_TOOLS, **kw,
            )

        def _text(r):
            return "".join(b.text for b in r.content
                           if getattr(b, "type", None) == "text").strip()

        def _run_pending_tools(r):
            msgs.append({"role": "assistant", "content": r.content})
            results = []
            for b in r.content:
                if getattr(b, "type", None) == "tool_use":
                    used.append(b.name)
                    out = run_tool(b.name, dict(b.input or {}))
                    results.append({"type": "tool_result", "tool_use_id": b.id,
                                    "content": _json.dumps(out, ensure_ascii=False)})
            msgs.append({"role": "user", "content": results})

        resp = _call()
        used = []
        # Loop de tool use: el modelo decide qué consultar; cap 3 rondas.
        for _ in range(3):
            if resp.stop_reason != "tool_use":
                break
            _run_pending_tools(resp)
            resp = _call()
        # Si agotó las rondas y AÚN quiere consultar más, respondemos sus
        # consultas pendientes y lo obligamos a escribir con lo que ya tiene
        # (sin esto, la respuesta salía sin texto → el usuario veía "…").
        if resp.stop_reason == "tool_use":
            _run_pending_tools(resp)
            resp = _call(force_text=True)

        reply = _text(resp)

        # Red de seguridad 1: si aun así topó el límite, pedir UNA continuación
        # para que a la persona nunca le llegue un mensaje cortado a mitad de frase.
        if resp.stop_reason == "max_tokens" and reply:
            try:
                msgs.append({"role": "assistant", "content": reply})
                msgs.append({"role": "user",
                             "content": "Tu respuesta quedó cortada. Continúa EXACTAMENTE "
                                        "donde quedaste, sin repetir nada de lo ya dicho."})
                extra = _text(_call())
                if extra:
                    reply = (reply.rstrip() + " " + extra).strip()
            except Exception:
                pass   # mejor entregar lo que hay que fallar todo el mensaje

        # Red de seguridad 2: respuesta vacía por el motivo que sea → un reintento
        # directo. Nunca devolver "…" (la app lo mostraba como mensaje).
        if not reply:
            try:
                msgs.append({"role": "user",
                             "content": "Responde ahora en 2-3 frases, con lo más importante."})
                reply = _text(_call(force_text=True))
            except Exception:
                pass
        if not reply:
            reply = ("Me quedé sin respuesta ahí — prueba preguntarmelo de nuevo, "
                     "quizás en dos preguntas más cortas.")

        return jsonify({"ok": True, "reply": reply,
                        "used_data": sorted(set(used))})
    except Exception as exc:
        return jsonify({"ok": False, "error": "No pude responder ahora. Intenta de nuevo."}), 502


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
            max_tokens=2500,  # razonamiento por pasos + JSON; en Sonnet 5 el
                              # pensamiento interno consume del mismo tope — con
                              # 900 un JSON largo podía salir truncado (parse roto)
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
        return jsonify({"ok": False, "error": "No pude estimar la foto. Carga los datos a mano."}), 502


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

    from models import Meal, InsulinDose, Activity, ContextTag
    days = min(int(request.args.get("days", 14)), 90)
    since = datetime.now() - timedelta(days=days)
    events = []

    for t in ContextTag.query.filter(ContextTag.timestamp >= since).order_by(ContextTag.timestamp.desc()).limit(100).all():
        events.append({"cat": "contexto", "id": t.id, "title": TAG_LABELS.get(t.tag, t.tag),
                       "badge": "", "ts": t.timestamp,
                       "data": {"tag": t.tag, "notes": t.notes or ""}})

    for m in Meal.query.filter(Meal.timestamp >= since).order_by(Meal.timestamp.desc()).limit(150).all():
        events.append({"cat": "comida", "id": m.id, "title": m.name or "Comida",
                       "badge": f"{int(m.carbs_g)}g" if m.carbs_g else "", "ts": m.timestamp,
                       "data": {"name": m.name or "", "carbs": m.carbs_g or 0,
                                "protein": m.protein_g or 0, "fat": m.fat_g or 0,
                                "calories": m.calories or 0, "notes": m.notes or "",
                                "components": [{"name": cp.name, "grams": cp.grams,
                                                "carbs": round(cp.carbs_g or 0)}
                                               for cp in (m.components or [])]}})
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


def _combine_datetime(row, data):
    """Aplica date/time del payload al timestamp de row (hora local).
    Devuelve (nuevo_datetime, error_str|None). Ninguno de los dos = None si
    no hay cambios de fecha/hora en el payload."""
    if not (data.get("date") or data.get("time")):
        return None, None
    try:
        base = row.timestamp
        d = (datetime.strptime(data["date"], "%Y-%m-%d").date()
             if data.get("date") else base.date())
        tm = (datetime.strptime(data["time"], "%H:%M").time()
              if data.get("time") else base.time())
        nuevo = datetime.combine(d, tm)
    except ValueError:
        return None, "Fecha u hora inválida"
    now = datetime.now()
    if nuevo > now + timedelta(minutes=5):
        return None, "La hora no puede ser futura"
    if nuevo < now - timedelta(days=365):
        return None, "Fecha demasiado antigua"
    return nuevo, None


@bp.route("/api/copilot/entry/<cat>/<int:entry_id>", methods=["PUT"], endpoint="copilot_entry_edit")
def copilot_entry_edit(cat, entry_id):
    """Editar fecha/hora de un registro (insulina / ejercicio / contexto).
    El horario real importa: mucha gente registra tarde, y el momento correcto
    hace que el análisis (ejercicio→glucosa, etc.) sea fiel."""
    err = _require_login()
    if err:
        return err
    from models import db, InsulinDose, Activity, ContextTag
    model = {"insulina": InsulinDose, "ejercicio": Activity, "contexto": ContextTag}.get(cat)
    if not model:
        return jsonify({"ok": False, "error": "Categoría inválida"}), 400
    row = model.query.get(entry_id)
    if not row:
        return jsonify({"ok": False, "error": "No encontrado"}), 404
    data = request.get_json(silent=True) or {}
    nuevo, e = _combine_datetime(row, data)
    if e:
        return jsonify({"ok": False, "error": e}), 400
    try:
        if nuevo is not None:
            row.timestamp = nuevo
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
    from models import db, Meal, InsulinDose, Activity, ContextTag
    model = {"comida": Meal, "insulina": InsulinDose, "ejercicio": Activity,
             "contexto": ContextTag}.get(cat)
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


@bp.route("/api/copilot/report.pdf", endpoint="copilot_report_pdf")
def copilot_report_pdf():
    """Reporte PDF para el equipo médico — 100% DESCRIPTIVO (datos, no
    recomendaciones). Reusa los mismos análisis del copiloto analista."""
    err = _require_login()
    if err:
        return err

    import io
    from flask import send_file
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                    TableStyle, Image)
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from utils.copilot_tools import (estadisticas_periodo, hipos_recientes,
                                     relacion_carbos_insulina)
    from utils.agp import agp_chart_png, tir_bar_png, agp_summary
    from helpers import _get_setting

    days = min(int(request.args.get("days", 30)), 90)
    hoy = datetime.now()

    glob = estadisticas_periodo(days=days)
    noches = estadisticas_periodo(days=days, hora_desde=22, hora_hasta=6)
    dias_franja = estadisticas_periodo(days=days, hora_desde=6, hora_hasta=22)
    hipos = hipos_recientes(days=days)
    cobert = relacion_carbos_insulina(days=days)

    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=styles["Title"], fontSize=17, spaceAfter=2)
    sub = ParagraphStyle("sub", parent=styles["Normal"], fontSize=9.5,
                         textColor=colors.HexColor("#666677"), spaceAfter=10)
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], fontSize=12, spaceBefore=12,
                        spaceAfter=4, textColor=colors.HexColor("#23233A"))
    small = ParagraphStyle("small", parent=styles["Normal"], fontSize=8.5,
                           textColor=colors.HexColor("#888899"), spaceBefore=14)

    def tabla(rows, widths=None):
        t = Table(rows, colWidths=widths)
        t.setStyle(TableStyle([
            ("FONTSIZE", (0, 0), (-1, -1), 9.5),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#44445A")),
            ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.HexColor("#CCCCDD")),
            ("LINEBELOW", (0, 1), (-1, -1), 0.25, colors.HexColor("#E8E8F0")),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        return t

    nombre = _get_setting("user_name") or ""
    story = [
        Paragraph("Orbit — Reporte para el equipo médico", h1),
        Paragraph(f"{nombre + ' · ' if nombre else ''}Últimos {days} días · "
                  f"generado el {hoy.strftime('%d/%m/%Y %H:%M')} · "
                  f"FreeStyle Libre vía LibreLinkUp", sub),
    ]

    # ── AGP (Ambulatory Glucose Profile) — el formato estándar clínico ──
    agp_days = min(days, 14)   # consenso: el AGP se lee mejor a 14 días
    agp_m = agp_summary(agp_days)
    agp_png = agp_chart_png(agp_days)
    tir_png = tir_bar_png(agp_days)
    if agp_png and agp_m.get("n"):
        story.append(Paragraph(f"AGP — Perfil Ambulatorio de Glucosa ({agp_days} días)", h2))
        story.append(Paragraph(
            f"{agp_m['dias_con_datos']} días con datos · sensor activo {agp_m['sensor_activo_pct']}% · "
            f"glucosa promedio <b>{agp_m['promedio']} mg/dL</b> · GMI <b>{agp_m['gmi']}%</b> · "
            f"CV <b>{agp_m['cv']}%</b>", styles["Normal"]))
        story.append(Spacer(1, 4))
        story.append(Image(io.BytesIO(agp_png), width=170 * mm, height=67 * mm))
        if tir_png:
            story.append(Spacer(1, 2))
            story.append(Paragraph("Tiempo en rangos", h2))
            story.append(Image(io.BytesIO(tir_png), width=170 * mm, height=26 * mm))

    story.append(Paragraph("Glucosa por franjas", h2))
    if "tir_pct" in glob:
        filas = [["", "TIR", "Promedio", "CV", "<70", ">180", "Hipos (eventos)"]]
        for etiqueta, st in [("Global", glob), ("Día (6-22h)", dias_franja),
                             ("Noche (22-6h)", noches)]:
            if st and "tir_pct" in st:
                filas.append([etiqueta, f"{st['tir_pct']}%", f"{st['promedio']} mg/dL",
                              f"{st['cv_pct']}%", f"{st['pct_bajo_70']}%",
                              f"{st['pct_sobre_180']}%", str(st["hipo_eventos"])])
        story.append(tabla(filas, [70, 45, 70, 45, 40, 40, 80]))
    else:
        story.append(Paragraph("Sin lecturas suficientes en el período.", styles["Normal"]))

    if hipos.get("hipo_eventos"):
        story.append(Paragraph("Hipoglucemias por franja horaria", h2))
        b = hipos.get("por_franja_horaria") or {}
        story.append(tabla([list(b.keys()), [str(v) for v in b.values()]]))

    if cobert.get("eventos_analizados"):
        story.append(Paragraph("Coberturas de carbohidratos — resultados observados", h2))
        filas = [["Relación usada", "Eventos", "En rango a 3h", "Con hipo en 4h", "Δ3h mediana"]]
        for label, r in (cobert.get("resultados_por_relacion_usada") or {}).items():
            filas.append([label, str(r["eventos"]), f"{r['pct_en_rango_a_las_3h']}%",
                          f"{r['pct_con_hipo_en_4h']}%",
                          f"{r['delta_3h_mediana']:+d} mg/dL" if r["delta_3h_mediana"] is not None else "—"])
        story.append(tabla(filas, [110, 55, 85, 85, 80]))
        story.append(Paragraph(
            f"Relación mediana usada: 1U : {cobert['relacion_mediana_usada_g_por_U']}g. "
            "Describe lo que el paciente HIZO y cómo resultó — no es una recomendación de dosis.",
            small))

    story.append(Paragraph(
        "Este reporte es descriptivo y generado automáticamente por Orbit a partir de los "
        "registros del paciente (CGM, comidas, insulina, actividad). No contiene "
        "recomendaciones de tratamiento. Confundidores no controlados: composición de "
        "comidas, ejercicio, estrés, calidad del registro manual.", small))

    buf = io.BytesIO()
    SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                      topMargin=16 * mm, bottomMargin=16 * mm).build(story)
    buf.seek(0)
    return send_file(buf, mimetype="application/pdf", as_attachment=True,
                     download_name=f"orbit_reporte_{hoy.strftime('%Y%m%d')}.pdf")


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
        for key, setting in (("objetivo", "objetivo"), ("isf", "isf_manual"), ("icr", "icr"),
                             ("basal_dose", "basal_dose_u")):
            if key in data:
                val = _numstr(key)
                if val is not None:
                    _set_setting(setting, val)
        # modo automático: vaciar el override manual → la app usa lo aprendido
        # (PMM/circadiano). El valor observado se muestra como referencia.
        if data.get("isf_auto"):
            _set_setting("isf_manual", "")
        if data.get("icr_auto"):
            _set_setting("icr", "")
        # basal: tipo (texto) y hora habitual (0-23) — alimentan el modelo,
        # el contexto del copiloto y el recordatorio de Hoy
        if "basal_tipo" in data:
            _set_setting("basal_tipo", (data.get("basal_tipo") or "").strip().lower()[:40])
        if "basal_hora" in data:
            try:
                h = int(float(data["basal_hora"]))
                if 0 <= h <= 23:
                    _set_setting("basal_hora", str(h))
            except (TypeError, ValueError):
                pass
        # idioma de la UI → el copiloto responde en el mismo idioma
        if "ui_lang" in data:
            lang = (data.get("ui_lang") or "").strip().lower()[:5]
            if lang in ("es", "en", "pt"):
                _set_setting("ui_lang", lang)
        # unidad de glucosa (los datos se guardan en mg/dL; esto es display) →
        # el copiloto expresa la glucosa en la misma unidad
        if "glucose_unit" in data:
            u = (data.get("glucose_unit") or "").strip().lower()
            if u in ("mgdl", "mmol"):
                _set_setting("glucose_unit", u)
        # perfil de vida: adapta la voz del copiloto (deportista/cuidador/estándar)
        if "perfil_vida" in data:
            p = (data.get("perfil_vida") or "").strip().lower()
            if p in ("deportista", "cuidador", "estandar"):
                _set_setting("perfil_vida", p)
        # onboarding completado (lo marca la pantalla de bienvenida)
        if data.get("onboarded"):
            _set_setting("onboarding_done", "1")
        return jsonify({"ok": True})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
