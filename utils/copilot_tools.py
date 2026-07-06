"""
utils/copilot_tools.py
──────────────────────
Herramientas de CONSULTA para el copiloto analista (tool use de Anthropic).

El chat del copiloto puede ejecutar estas consultas de SOLO LECTURA sobre los
datos reales del usuario para responder preguntas como "¿qué pasa con mi
glucosa después del ejercicio?" o "¿qué me estuvo afectando?".

REGLAS DE DISEÑO (la línea regulatoria del producto):
- Todas las consultas son retrospectivas: describen lo que YA PASÓ.
- Nada calcula dosis, nada predice. Devuelven estadísticas descriptivas.
- Los guardarraíles del system prompt no cambian: el modelo explica y acompaña.

Estructura: helpers puros (testeables sin DB) + wrappers que cargan de la DB +
`COPILOT_TOOLS` (schemas Anthropic) + `run_tool()` (dispatcher a prueba de todo).
"""
from __future__ import annotations

from datetime import datetime, timedelta

from utils.copilot_memory import normalize_meal_name, reading_near, median

LOW, HIGH = 70, 180
_MAX_DAYS = 120

_BUCKETS = (("madrugada (0-6)", 0, 6), ("mañana (6-12)", 6, 12),
            ("tarde (12-18)", 12, 18), ("noche (18-24)", 18, 24))


# ─────────────────────── helpers puros (testeables) ───────────────────────

def in_hour_window(dt: datetime, h_from: int | None, h_to: int | None) -> bool:
    """¿dt cae en la franja [h_from, h_to)? Soporta cruce de medianoche (22→6)."""
    if h_from is None and h_to is None:
        return True
    h = dt.hour
    a = 0 if h_from is None else h_from
    b = 24 if h_to is None else h_to
    if a == b:
        return True
    if a < b:
        return a <= h < b
    return h >= a or h < b            # franja que cruza medianoche


def detect_hypo_events(times: list, values: list, threshold: float = LOW) -> list[dict]:
    """Rachas <threshold como EVENTOS: [{start, min_v, n_readings}]."""
    events = []
    cur = None
    for t, v in zip(times, values):
        if v < threshold:
            if cur is None:
                cur = {"start": t, "min_v": v, "n_readings": 1}
            else:
                cur["n_readings"] += 1
                cur["min_v"] = min(cur["min_v"], v)
        else:
            if cur is not None:
                events.append(cur)
                cur = None
    if cur is not None:
        events.append(cur)
    return events


def slice_stats(times: list, values: list, h_from=None, h_to=None,
                weekday: int | None = None) -> dict | None:
    """Estadísticas de una franja horaria/día de semana. weekday: 0=lunes."""
    pares = [(t, v) for t, v in zip(times, values)
             if in_hour_window(t, h_from, h_to)
             and (weekday is None or t.weekday() == weekday)]
    if len(pares) < 12:
        return None
    vals = [v for _, v in pares]
    n = len(vals)
    avg = sum(vals) / n
    var = sum((v - avg) ** 2 for v in vals) / n
    sd = var ** 0.5
    return {
        "lecturas": n,
        "tir_pct": round(100 * sum(1 for v in vals if LOW <= v <= HIGH) / n),
        "promedio": int(round(avg)),
        "cv_pct": round(100 * sd / avg, 1) if avg else None,
        "minimo": int(min(vals)),
        "maximo": int(max(vals)),
        "pct_bajo_70": round(100 * sum(1 for v in vals if v < LOW) / n, 1),
        "pct_sobre_180": round(100 * sum(1 for v in vals if v > HIGH) / n, 1),
        "hipo_eventos": len(detect_hypo_events([t for t, _ in pares], vals)),
    }


def delta_after(times: list, values: list, t0: datetime, hours: float) -> int | None:
    """Δ glucosa entre t0 y t0+hours (lecturas más cercanas, ±25 min)."""
    g0 = reading_near(times, values, t0)
    g1 = reading_near(times, values, t0 + timedelta(hours=hours))
    if g0 is None or g1 is None:
        return None
    return int(round(g1 - g0))


def pair_meals_boluses(meals: list, boluses: list, max_gap_min: int = 30) -> list:
    """
    Aparea cada comida con SU bolo (el más cercano dentro de ±max_gap_min);
    cada bolo se usa una sola vez. meals=[(ts, carbs)], boluses=[(ts, units)].
    Devuelve [(meal_ts, carbs, units)].
    """
    libres = sorted(boluses)
    pares = []
    for m_ts, carbs in sorted(meals):
        mejor, mejor_dif = None, None
        for i, (b_ts, _u) in enumerate(libres):
            dif = abs((b_ts - m_ts).total_seconds()) / 60.0
            if dif <= max_gap_min and (mejor_dif is None or dif < mejor_dif):
                mejor, mejor_dif = i, dif
        if mejor is not None:
            b_ts, units = libres.pop(mejor)
            pares.append((m_ts, carbs, units))
    return pares


def ratio_bucket(g_per_u: float) -> str:
    """Bucket de la relación usada (gramos cubiertos por 1U)."""
    if g_per_u < 8:
        return "menos de 8 g/U"
    if g_per_u < 12:
        return "8-12 g/U"
    if g_per_u < 16:
        return "12-16 g/U"
    return "16 g/U o más"


# ─────────────────────── carga de datos ───────────────────────

def _load_readings(days: int):
    from models import GlucoseReading
    since = datetime.now() - timedelta(days=min(days, _MAX_DAYS))
    reads = (GlucoseReading.query
             .filter(GlucoseReading.timestamp >= since,
                     GlucoseReading.is_artifact == False)  # noqa: E712
             .order_by(GlucoseReading.timestamp).all())
    return [r.timestamp for r in reads], [r.value_mgdl for r in reads]


# ─────────────────────── las consultas ───────────────────────

def respuesta_al_ejercicio(days: int = 90) -> dict:
    """Qué pasó con la glucosa después de entrenar (0-2h y la noche siguiente)."""
    from models import Activity
    days = min(int(days or 90), _MAX_DAYS)
    since = datetime.now() - timedelta(days=days)
    acts = (Activity.query.filter(Activity.timestamp >= since)
            .order_by(Activity.timestamp).all())
    if not acts:
        return {"sesiones": 0, "nota": f"Sin ejercicio registrado en {days} días."}

    times, values = _load_readings(days)
    por_intensidad: dict[str, list] = {}
    nocturnas = {"sesiones_vespertinas": 0, "noches_con_hipo": 0}
    detalle = []
    for a in acts:
        dur = a.duration_min or 30
        fin = a.timestamp + timedelta(minutes=dur)
        d2 = delta_after(times, values, a.timestamp, 2)
        d6 = delta_after(times, values, a.timestamp, 6)
        key = (a.intensity or "sin intensidad").lower()
        if d2 is not None:
            por_intensidad.setdefault(key, []).append(d2)
        # sesión vespertina → ¿la madrugada siguiente tuvo hipo?
        if a.timestamp.hour >= 16:
            nocturnas["sesiones_vespertinas"] += 1
            noche0 = (a.timestamp + timedelta(days=1)).replace(hour=0, minute=0)
            noche1 = noche0 + timedelta(hours=6)
            vals_noche = [v for t, v in zip(times, values) if noche0 <= t <= noche1]
            if vals_noche and min(vals_noche) < LOW:
                nocturnas["noches_con_hipo"] += 1
        if len(detalle) < 12:
            detalle.append({"fecha": a.timestamp.strftime("%d/%m %H:%M"),
                            "tipo": a.activity_type or "ejercicio",
                            "min": dur, "intensidad": a.intensity or "-",
                            "delta_2h": d2, "delta_6h": d6})
    return {
        "sesiones": len(acts),
        "ventana_dias": days,
        "delta_2h_mediana_por_intensidad": {
            k: {"n": len(v), "delta_2h_mediana": int(median(v))}
            for k, v in por_intensidad.items()},
        "despues_de_entrenar_de_tarde_noche": nocturnas,
        "ultimas_sesiones": detalle,
        "nota": "Deltas en mg/dL, observados en el pasado (no predicción).",
    }


def hipos_recientes(days: int = 30) -> dict:
    """Hipos como eventos + qué se registró en las 4h previas a cada una."""
    from models import Meal, InsulinDose, Activity
    days = min(int(days or 30), _MAX_DAYS)
    times, values = _load_readings(days)
    events = detect_hypo_events(times, values)
    if not events:
        return {"hipo_eventos": 0, "ventana_dias": days}

    since = datetime.now() - timedelta(days=days)
    meals = Meal.query.filter(Meal.timestamp >= since).all()
    doses = InsulinDose.query.filter(InsulinDose.timestamp >= since).all()
    acts = Activity.query.filter(Activity.timestamp >= since).all()

    buckets = {label: 0 for label, _, _ in _BUCKETS}
    detalle = []
    for ev in events:
        for label, a, b in _BUCKETS:
            if a <= ev["start"].hour < b:
                buckets[label] += 1
        if len(detalle) >= 12:
            continue
        w0 = ev["start"] - timedelta(hours=4)
        previo = []
        for m in meals:
            if w0 <= m.timestamp < ev["start"]:
                previo.append(f"comida '{m.name or 'comida'}' ({int(m.carbs_g or 0)}g CH) "
                              f"{int((ev['start'] - m.timestamp).total_seconds() // 60)}min antes")
        for d in doses:
            if w0 <= d.timestamp < ev["start"]:
                tipo = "bolo" if d.type == "bolus" else "basal"
                previo.append(f"{tipo} {d.units:g}U "
                              f"{int((ev['start'] - d.timestamp).total_seconds() // 60)}min antes")
        for a2 in acts:
            if w0 <= a2.timestamp < ev["start"]:
                previo.append(f"ejercicio {a2.activity_type or ''} {a2.duration_min or 0}min "
                              f"{int((ev['start'] - a2.timestamp).total_seconds() // 60)}min antes")
        detalle.append({
            "fecha": ev["start"].strftime("%d/%m %H:%M"),
            "minimo": int(ev["min_v"]),
            "duracion_aprox_min": ev["n_readings"] * 5,
            "que_hubo_antes_4h": previo or ["nada registrado"],
        })
    detalle.reverse()   # más recientes primero
    return {"hipo_eventos": len(events), "ventana_dias": days,
            "por_franja_horaria": buckets, "detalle": detalle}


def estadisticas_periodo(days: int = 14, hora_desde=None, hora_hasta=None,
                         dia_semana=None) -> dict:
    """TIR/promedio/CV/hipos de una franja horaria y/o día de semana."""
    days = min(int(days or 14), _MAX_DAYS)
    times, values = _load_readings(days)
    hf = int(hora_desde) if hora_desde is not None else None
    hh = int(hora_hasta) if hora_hasta is not None else None
    ds = int(dia_semana) if dia_semana is not None else None
    stats = slice_stats(times, values, hf, hh, ds)
    if not stats:
        return {"nota": "Muy pocas lecturas en esa franja para un número confiable."}
    out = {"ventana_dias": days, **stats}
    if hf is not None or hh is not None:
        out["franja_horaria"] = f"{hf if hf is not None else 0}–{hh if hh is not None else 24}h"
    if ds is not None:
        out["dia_semana"] = ["lunes", "martes", "miércoles", "jueves",
                             "viernes", "sábado", "domingo"][ds]
    return out


def respuesta_a_comida(nombre: str, days: int = 90) -> dict:
    """Cómo respondió la glucosa a una comida (búsqueda por nombre)."""
    from models import Meal
    days = min(int(days or 90), _MAX_DAYS)
    q = normalize_meal_name(nombre or "")
    if len(q) < 2:
        return {"nota": "Nombre demasiado corto para buscar."}
    since = datetime.now() - timedelta(days=days)
    meals = [m for m in Meal.query.filter(Meal.timestamp >= since).all()
             if q in normalize_meal_name(m.name or "")]
    if not meals:
        return {"coincidencias": 0, "nota": f"Sin comidas que coincidan con '{nombre}' en {days} días."}

    times, values = _load_readings(days)
    instancias, d1s, d2s, d3s = [], [], [], []
    for m in sorted(meals, key=lambda x: x.timestamp, reverse=True):
        d1 = delta_after(times, values, m.timestamp, 1)
        d2 = delta_after(times, values, m.timestamp, 2)
        d3 = delta_after(times, values, m.timestamp, 3)
        if d1 is not None: d1s.append(d1)
        if d2 is not None: d2s.append(d2)
        if d3 is not None: d3s.append(d3)
        if len(instancias) < 15:
            instancias.append({"fecha": m.timestamp.strftime("%d/%m %H:%M"),
                               "nombre": m.name, "carbs_g": int(m.carbs_g or 0),
                               "delta_1h": d1, "delta_2h": d2, "delta_3h": d3})
    return {
        "coincidencias": len(meals), "ventana_dias": days,
        "delta_mediana": {"1h": int(median(d1s)) if d1s else None,
                          "2h": int(median(d2s)) if d2s else None,
                          "3h": int(median(d3s)) if d3s else None},
        "instancias": instancias,
        "nota": "Deltas en mg/dL respecto del momento de comer (observado, no predicción).",
    }


def impacto_de_eventos(tipo: str, days: int = 60, hora_desde=None, hora_hasta=None) -> dict:
    """Δ2h mediano tras comidas/bolos/ejercicio, opcionalmente por franja horaria
    (p.ej. comidas después de las 22 → ¿cenar tarde me afecta?)."""
    from models import Meal, InsulinDose, Activity
    days = min(int(days or 60), _MAX_DAYS)
    since = datetime.now() - timedelta(days=days)
    hf = int(hora_desde) if hora_desde is not None else None
    hh = int(hora_hasta) if hora_hasta is not None else None

    if tipo == "comida":
        rows = [(m.timestamp, int(m.carbs_g or 0)) for m in
                Meal.query.filter(Meal.timestamp >= since).all()]
    elif tipo == "bolo":
        rows = [(d.timestamp, d.units) for d in
                InsulinDose.query.filter(InsulinDose.timestamp >= since,
                                         InsulinDose.type == "bolus").all()]
    elif tipo == "ejercicio":
        rows = [(a.timestamp, a.duration_min or 0) for a in
                Activity.query.filter(Activity.timestamp >= since).all()]
    else:
        return {"nota": "tipo debe ser comida | bolo | ejercicio"}

    rows = [(t, x) for t, x in rows if in_hour_window(t, hf, hh)]
    if not rows:
        return {"eventos": 0, "nota": "Sin eventos de ese tipo en esa franja."}

    times, values = _load_readings(days)
    deltas = [d for d in (delta_after(times, values, t, 2) for t, _ in rows)
              if d is not None]
    out = {"tipo": tipo, "eventos": len(rows), "ventana_dias": days,
           "delta_2h_mediana": int(median(deltas)) if deltas else None,
           "con_lecturas_apareadas": len(deltas)}
    if hf is not None or hh is not None:
        out["franja_horaria"] = f"{hf if hf is not None else 0}–{hh if hh is not None else 24}h"
    if tipo == "comida":
        out["carbs_mediana_g"] = int(median([x for _, x in rows]))
    return out


def relacion_carbos_insulina(days: int = 90) -> dict:
    """
    DESCRIBE cómo resultaron las coberturas de carbohidratos del pasado,
    agrupadas por la relación usada (g de CH por unidad). NO es un cálculo de
    dosis: es el espejo retrospectivo de lo que el usuario hizo y cómo terminó.
    """
    from models import Meal, InsulinDose
    days = min(int(days or 90), _MAX_DAYS)
    since = datetime.now() - timedelta(days=days)

    meals_rows = [(m.timestamp, float(m.carbs_g))
                  for m in Meal.query.filter(Meal.timestamp >= since).all()
                  if (m.carbs_g or 0) >= 15]          # coberturas con sustancia
    bolus_rows = [(d.timestamp, float(d.units))
                  for d in InsulinDose.query.filter(
                      InsulinDose.timestamp >= since,
                      InsulinDose.type == "bolus").all() if (d.units or 0) > 0]
    pares = pair_meals_boluses(meals_rows, bolus_rows)
    if not pares:
        return {"eventos": 0,
                "nota": f"Sin comidas ≥15g CH apareadas con un bolo (±30 min) en {days} días."}

    # Excluir eventos "contaminados": otro bolo u otra comida grande dentro de
    # las 2h siguientes → el resultado a las 3h no sería atribuible al par.
    all_bolus_ts = [b for b, _ in bolus_rows]
    all_meal_ts = [(m, c) for m, c in meals_rows]
    limpios, descartados = [], 0
    for m_ts, carbs, units in pares:
        fin = m_ts + timedelta(hours=2)
        otro_bolo = any(m_ts < b <= fin and abs((b - m_ts).total_seconds()) > 1800
                        for b in all_bolus_ts)
        otra_comida = any(m_ts < t2 <= fin and c2 >= 10 for t2, c2 in all_meal_ts
                          if t2 != m_ts)
        if otro_bolo or otra_comida:
            descartados += 1
        else:
            limpios.append((m_ts, carbs, units))
    if not limpios:
        return {"eventos": 0, "descartados_por_solapamiento": descartados,
                "nota": "Todos los eventos tenían otra comida/bolo encima (no atribuibles)."}

    times, values = _load_readings(days)
    buckets: dict[str, dict] = {}
    ratios = []
    for m_ts, carbs, units in limpios:
        r = carbs / units
        ratios.append(r)
        b = buckets.setdefault(ratio_bucket(r), {"n": 0, "en_rango_3h": 0,
                                                 "hipo_4h": 0, "deltas_3h": []})
        b["n"] += 1
        g3 = reading_near(times, values, m_ts + timedelta(hours=3))
        if g3 is not None and LOW <= g3 <= HIGH:
            b["en_rango_3h"] += 1
        vals_4h = [v for t, v in zip(times, values)
                   if m_ts < t <= m_ts + timedelta(hours=4)]
        if vals_4h and min(vals_4h) < LOW:
            b["hipo_4h"] += 1
        d3 = delta_after(times, values, m_ts, 3)
        if d3 is not None:
            b["deltas_3h"].append(d3)

    resumen = {}
    for label, b in sorted(buckets.items()):
        resumen[label] = {
            "eventos": b["n"],
            "pct_en_rango_a_las_3h": round(100 * b["en_rango_3h"] / b["n"]),
            "pct_con_hipo_en_4h": round(100 * b["hipo_4h"] / b["n"]),
            "delta_3h_mediana": int(median(b["deltas_3h"])) if b["deltas_3h"] else None,
        }
    return {
        "eventos_analizados": len(limpios),
        "descartados_por_solapamiento": descartados,
        "ventana_dias": days,
        "relacion_mediana_usada_g_por_U": round(median(ratios), 1),
        "resultados_por_relacion_usada": resumen,
        "nota": ("Espejo RETROSPECTIVO: describe qué relación usaste y cómo terminó "
                 "(en rango a las 3h / hipos en 4h). NO es una recomendación de dosis. "
                 "Confundidores no controlados: grasa/proteína de la comida, ejercicio, "
                 "basal, glucosa de partida."),
    }


def impacto_de_contexto(tag: str | None = None, days: int = 90) -> dict:
    """
    Compara la glucosa de los DÍAS con una etiqueta de contexto (estrés,
    enfermedad, mal sueño…) contra los días sin ella. Retrospectivo.
    """
    from models import ContextTag
    days = min(int(days or 90), _MAX_DAYS)
    since = datetime.now() - timedelta(days=days)
    q = ContextTag.query.filter(ContextTag.timestamp >= since)
    if tag:
        q = q.filter(ContextTag.tag == tag.strip().lower())
    tags = q.all()
    if not tags:
        return {"eventos": 0,
                "nota": f"Sin contexto '{tag or 'ninguno'}' registrado en {days} días."}

    times, values = _load_readings(days)
    if not times:
        return {"eventos": len(tags), "nota": "Sin lecturas para comparar."}

    # días marcados (por tipo de etiqueta)
    por_tag: dict[str, set] = {}
    for t in tags:
        por_tag.setdefault(t.tag, set()).add(t.timestamp.date())

    def _stats_de_dias(dias_set):
        vals = [v for tt, v in zip(times, values) if tt.date() in dias_set]
        if len(vals) < 12:
            return None
        n = len(vals)
        return {"lecturas": n, "promedio": int(round(sum(vals) / n)),
                "tir_pct": round(100 * sum(1 for v in vals if LOW <= v <= HIGH) / n),
                "pct_bajo_70": round(100 * sum(1 for v in vals if v < LOW) / n, 1),
                "pct_sobre_180": round(100 * sum(1 for v in vals if v > HIGH) / n, 1)}

    todos_marcados = set().union(*por_tag.values())
    baseline = _stats_de_dias(set(t.date() for t in times) - todos_marcados)

    out = {"ventana_dias": days, "dias_normales": baseline, "por_contexto": {}}
    for tg, dias in por_tag.items():
        st = _stats_de_dias(dias)
        if st:
            st["dias_marcados"] = len(dias)
            out["por_contexto"][tg] = st
    out["nota"] = ("Compara días CON cada contexto vs días normales. Observacional: "
                   "el contexto se marcó a mano y puede coincidir con otras cosas.")
    return out


# ─────────────────────── schemas Anthropic + dispatcher ───────────────────────

COPILOT_TOOLS = [
    {
        "name": "respuesta_al_ejercicio",
        "description": ("Qué pasó con la glucosa DESPUÉS de las sesiones de ejercicio "
                        "registradas: delta 2h y 6h por intensidad, y si hubo hipos "
                        "nocturnas tras entrenar de tarde/noche. Retrospectivo."),
        "input_schema": {"type": "object", "properties": {
            "days": {"type": "integer", "description": "Ventana en días (default 90, máx 120)"}}},
    },
    {
        "name": "hipos_recientes",
        "description": ("Eventos de hipoglucemia (<70) recientes: cuándo, mínimo, "
                        "duración, franja horaria y qué se registró en las 4h previas "
                        "(comidas, insulina, ejercicio). Para '¿qué me estuvo afectando?'"),
        "input_schema": {"type": "object", "properties": {
            "days": {"type": "integer", "description": "Ventana en días (default 30)"}}},
    },
    {
        "name": "estadisticas_periodo",
        "description": ("TIR, promedio, CV, mín/máx e hipos de un período, con filtro "
                        "opcional por franja horaria (soporta cruce de medianoche, "
                        "p.ej. 22→6 = noches) y/o día de semana (0=lunes … 6=domingo)."),
        "input_schema": {"type": "object", "properties": {
            "days": {"type": "integer", "description": "Ventana en días (default 14)"},
            "hora_desde": {"type": "integer", "description": "Hora inicio 0-23 (opcional)"},
            "hora_hasta": {"type": "integer", "description": "Hora fin 0-24 (opcional)"},
            "dia_semana": {"type": "integer", "description": "0=lunes … 6=domingo (opcional)"}}},
    },
    {
        "name": "respuesta_a_comida",
        "description": ("Cómo respondió la glucosa a una comida específica en el pasado "
                        "(búsqueda por nombre): delta 1h/2h/3h por instancia y medianas."),
        "input_schema": {"type": "object", "properties": {
            "nombre": {"type": "string", "description": "Nombre o parte del nombre de la comida"},
            "days": {"type": "integer", "description": "Ventana en días (default 90)"}},
            "required": ["nombre"]},
    },
    {
        "name": "relacion_carbos_insulina",
        "description": ("Espejo RETROSPECTIVO de las coberturas de carbohidratos: qué "
                        "relación g-de-CH-por-unidad usó la persona en el pasado y cómo "
                        "terminó cada grupo (% en rango a las 3h, % con hipo en 4h). "
                        "USO PERMITIDO: describir resultados pasados. PROHIBIDO: "
                        "convertirlo en dosis ('para Xg ponete YU') — si piden una "
                        "dosis se declina igual que siempre y se deriva al equipo "
                        "médico; solo se puede mostrar la historia."),
        "input_schema": {"type": "object", "properties": {
            "days": {"type": "integer", "description": "Ventana en días (default 90)"}}},
    },
    {
        "name": "impacto_de_contexto",
        "description": ("Compara la glucosa de los días CON una etiqueta de contexto "
                        "(estres | enfermo | mal_sueno | viaje | alcohol) contra los "
                        "días normales: TIR, promedio, % bajo/alto. Para '¿cómo me "
                        "afecta el estrés / dormir mal / estar enfermo?'. Si no se pasa "
                        "tag, compara todos los contextos registrados."),
        "input_schema": {"type": "object", "properties": {
            "tag": {"type": "string", "description": "estres|enfermo|mal_sueno|viaje|alcohol (opcional)"},
            "days": {"type": "integer", "description": "Ventana en días (default 90)"}}},
    },
    {
        "name": "impacto_de_eventos",
        "description": ("Delta 2h mediano tras un tipo de evento (comida | bolo | "
                        "ejercicio), con franja horaria opcional. Sirve para preguntas "
                        "como '¿cenar tarde me afecta?' (tipo=comida, hora_desde=22)."),
        "input_schema": {"type": "object", "properties": {
            "tipo": {"type": "string", "enum": ["comida", "bolo", "ejercicio"]},
            "days": {"type": "integer", "description": "Ventana en días (default 60)"},
            "hora_desde": {"type": "integer"}, "hora_hasta": {"type": "integer"}},
            "required": ["tipo"]},
    },
]

_DISPATCH = {
    "respuesta_al_ejercicio": lambda a: respuesta_al_ejercicio(a.get("days", 90)),
    "hipos_recientes":        lambda a: hipos_recientes(a.get("days", 30)),
    "estadisticas_periodo":   lambda a: estadisticas_periodo(
        a.get("days", 14), a.get("hora_desde"), a.get("hora_hasta"), a.get("dia_semana")),
    "respuesta_a_comida":     lambda a: respuesta_a_comida(a.get("nombre", ""), a.get("days", 90)),
    "relacion_carbos_insulina": lambda a: relacion_carbos_insulina(a.get("days", 90)),
    "impacto_de_contexto":    lambda a: impacto_de_contexto(a.get("tag"), a.get("days", 90)),
    "impacto_de_eventos":     lambda a: impacto_de_eventos(
        a.get("tipo", ""), a.get("days", 60), a.get("hora_desde"), a.get("hora_hasta")),
}


def run_tool(name: str, args: dict) -> dict:
    """Ejecuta una consulta. Nunca levanta: los errores vuelven como dict."""
    fn = _DISPATCH.get(name)
    if not fn:
        return {"error": f"consulta desconocida: {name}"}
    try:
        return fn(args or {})
    except Exception as exc:
        return {"error": f"la consulta falló: {exc}"}
