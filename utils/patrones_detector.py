"""
utils/patrones_detector.py — Capa 2: Detección de patrones fisiológicos.

Analiza las series de tiempo de glucosa, insulina, comidas y ejercicio
para identificar patrones clínicos recurrentes que el usuario podría
no percibir con simples números.

Patrones detectados:
    1. Efecto Somogyi        — hipo nocturna + rebote matutino
    2. Fenómeno del alba     — sube sola entre 03:00 y 08:00
    3. Hipo post-ejercicio   — hipo en las 6h siguientes a actividad
    4. Rebote grasa/proteína — segunda elevación 3-5h post-comida rica
    5. Variabilidad excesiva — CV% > 36% (ATTD 2019 consenso)
    6. Hipers pre-comida     — glucosa alta ANTES de comer (sin corrección)
    7. Patrón postprandial   — pico consistente > esperado para tipo de comida
    8. Franja de hipos       — ≥40% de los episodios de hipo en un mismo bloque horario
    9. Hipo tardía comida rica — bolo + comida baja en CH / alta proteína-grasa → hipo 2-5h
   10. Impacto de contexto   — días etiquetados (estrés/enfermedad/…) vs. resto
   11. Día de la semana      — un día puntual corre ≥15 mg/dL distinto al resto
   12. Basal sin registrar   — hueco de registro que limita el análisis nocturno

Función pública:
    analizar_patrones(days=30) → dict con:
        patrones       : list[dict]  — patrones detectados
        resumen        : dict        — métricas agregadas
        serie_glucose  : list[dict]  — serie temporal compacta (para Capa 3)
        generado_en    : str ISO
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from statistics import mean, stdev
from typing import Optional

from models import db, GlucoseReading, Meal, InsulinDose


def _ahora_local():
    """now() en la zona del usuario del contexto (fallback: server)."""
    try:
        from helpers import ahora_usuario
        return ahora_usuario()
    except Exception:
        from datetime import datetime as _d
        return _d.now()


# Intentar importar Activity (puede no existir en todas las instancias)
try:
    from models import Activity
    _ACTIVITY_OK = True
except ImportError:
    _ACTIVITY_OK = False

# ContextTag (etiquetas estrés/enfermedad/…) — igual: import defensivo
try:
    from models import ContextTag
    _CONTEXT_OK = True
except ImportError:
    _CONTEXT_OK = False


# ── Umbrales clínicos ────────────────────────────────────────────────────────
_HIPO          = 70    # mg/dL
_HIPER         = 180   # mg/dL
_CV_UMBRAL     = 36.0  # % — ATTD 2019 consenso
_REBOTE_MIN    = 30    # mg/dL de diferencia tardío-temprano para llamarlo rebote
_SOMOGYI_HORA_HIPO_INI  = 1
_SOMOGYI_HORA_HIPO_FIN  = 5   # [01:00–05:00)
_SOMOGYI_HORA_HIPER_INI = 7
_SOMOGYI_HORA_HIPER_FIN = 11  # [07:00–11:00)


def _lecturas_en_rango(lecturas, t_ini, t_fin):
    return [r for r in lecturas if t_ini <= r.timestamp < t_fin]


def _detectar_somogyi(lecturas, days) -> Optional[dict]:
    """
    Efecto Somogyi: hipo nocturna (01:00–05:00) seguida de hiper matutina
    (07:00–11:00) en la misma noche. Se requieren ≥2 episodios.
    """
    episodios = []
    fechas_vistas = set()

    for r in lecturas:
        h = r.timestamp.hour
        if r.value_mgdl >= _HIPO or h not in range(_SOMOGYI_HORA_HIPO_INI, _SOMOGYI_HORA_HIPO_FIN):
            continue
        fecha = r.timestamp.date()
        if fecha in fechas_vistas:
            continue
        # Buscar hiper hasta 9h después
        for r2 in lecturas:
            if r2.timestamp <= r.timestamp:
                continue
            if r2.timestamp > r.timestamp + timedelta(hours=9):
                break
            if r2.timestamp.hour in range(_SOMOGYI_HORA_HIPER_INI, _SOMOGYI_HORA_HIPER_FIN) \
                    and r2.value_mgdl > 200:
                episodios.append({
                    "fecha":      str(fecha),
                    "hipo_hora":  r.timestamp.strftime("%H:%M"),
                    "hipo_val":   round(r.value_mgdl),
                    "hiper_hora": r2.timestamp.strftime("%H:%M"),
                    "hiper_val":  round(r2.value_mgdl),
                })
                fechas_vistas.add(fecha)
                break

    if len(episodios) < 2:
        return None

    return {
        "tipo":      "somogyi",
        "icono":     "bi-arrow-down-up",
        "nivel":     "danger",
        "titulo":    "Efecto Somogyi detectado",
        "frecuencia": len(episodios),
        "detalle":   (
            f"En {len(episodios)} ocasiones hubo hipoglucemia nocturna "
            f"(01–05hs) seguida de hiperglucemia matutina (07–11hs). "
            f"El organismo libera glucagón, adrenalina y cortisol en respuesta "
            f"a la hipo, lo que dispara la glucosa en la madrugada/mañana."
        ),
        "sugerencia": (
            "Mide tu glucosa entre 02:00 y 03:00 durante 3–5 noches consecutivas. "
            "Si confirmas hipoglucemia nocturna, la insulina basal puede ser "
            "demasiado alta. Ajusta con tu médico antes de cambiar la dosis."
        ),
        "episodios": episodios[-5:],  # últimos 5 para no saturar JSON
    }


def _detectar_fenomeno_alba(lecturas, days) -> Optional[dict]:
    """
    Fenómeno del alba: glucosa sube > 40 mg/dL entre 03:00 y 09:00
    sin comida ni insulina bolus en esa ventana.
    """
    desde = _ahora_local() - timedelta(days=days)
    bolus_periodo = InsulinDose.query.filter(
        InsulinDose.type == "bolus",
        InsulinDose.timestamp >= desde,
    ).all()
    comidas_periodo = Meal.query.filter(
        Meal.timestamp >= desde,
    ).all()

    por_fecha_hora = defaultdict(list)
    for r in lecturas:
        por_fecha_hora[r.timestamp.date()].append(r)

    dias_alba = []
    fechas_vistas = set()

    for r in lecturas:
        if r.timestamp.hour not in range(2, 6):
            continue
        fecha = r.timestamp.date()
        if fecha in fechas_vistas:
            continue

        val_3am = r.value_mgdl
        # Lecturas entre 07:00 y 10:00 del mismo día
        vals_manana = [
            x.value_mgdl for x in por_fecha_hora[fecha]
            if x.timestamp.hour in range(7, 11) and x.timestamp > r.timestamp
        ]
        if not vals_manana:
            continue
        val_max_manana = max(vals_manana)

        if val_max_manana - val_3am < 40:
            continue

        # Verificar que no hubo bolus ni comida en la ventana 03:00–09:00
        t_ini = datetime.combine(fecha, datetime.min.time()) + timedelta(hours=3)
        t_fin = t_ini + timedelta(hours=6)
        hay_bolus = any(t_ini <= b.timestamp < t_fin for b in bolus_periodo)
        hay_comida = any(t_ini <= c.timestamp < t_fin for c in comidas_periodo)
        if hay_bolus or hay_comida:
            continue

        dias_alba.append({
            "fecha":     str(fecha),
            "val_3am":   round(val_3am),
            "val_pico":  round(val_max_manana),
            "delta":     round(val_max_manana - val_3am),
        })
        fechas_vistas.add(fecha)

    if len(dias_alba) < 3:
        return None

    avg_delta = round(mean(d["delta"] for d in dias_alba))
    return {
        "tipo":      "alba",
        "icono":     "bi-sunrise-fill",
        "nivel":     "warning",
        "titulo":    "Fenómeno del alba recurrente",
        "frecuencia": len(dias_alba),
        "detalle":   (
            f"En {len(dias_alba)} días la glucosa subió en promedio "
            f"{avg_delta} mg/dL entre las 03:00 y las 09:00 sin comida "
            f"ni bolo de insulina. La secreción natural de cortisol y hormona "
            f"de crecimiento al amanecer contrarresta la insulina basal."
        ),
        "sugerencia": (
            "Habla con tu médico para considerar ajustar la hora o la dosis de "
            "insulina basal (Tresiba, Lantus, Levemir). Algunos pacientes también "
            "benefician de un micro-bolo de corrección al despertar."
        ),
        "episodios": dias_alba[-5:],
    }


def _detectar_hipo_post_ejercicio(lecturas, days) -> Optional[dict]:
    """
    Hipoglucemia dentro de las 6h posteriores a una sesión de ejercicio.
    """
    if not _ACTIVITY_OK:
        return None

    desde = _ahora_local() - timedelta(days=days)
    actividades = Activity.query.filter(Activity.timestamp >= desde).all()
    if not actividades:
        return None

    episodios = []
    for act in actividades:
        ventana_fin = act.timestamp + timedelta(hours=6)
        hipo = next(
            (r for r in lecturas
             if act.timestamp < r.timestamp <= ventana_fin and r.value_mgdl < _HIPO),
            None
        )
        if hipo:
            delay_min = round((hipo.timestamp - act.timestamp).total_seconds() / 60)
            episodios.append({
                "fecha":     act.timestamp.strftime("%Y-%m-%d"),
                "actividad": getattr(act, "name", "Ejercicio"),
                "delay_min": delay_min,
                "hipo_val":  round(hipo.value_mgdl),
            })

    if len(episodios) < 2:
        return None

    avg_delay = round(mean(e["delay_min"] for e in episodios))
    return {
        "tipo":      "hipo_ejercicio",
        "icono":     "bi-bicycle",
        "nivel":     "warning",
        "titulo":    "Hipoglucemias recurrentes post-ejercicio",
        "frecuencia": len(episodios),
        "detalle":   (
            f"En {len(episodios)} sesiones de ejercicio hubo hipoglucemia "
            f"en las 6 horas siguientes (promedio: {avg_delay} min después). "
            f"El ejercicio aeróbico aumenta la sensibilidad a la insulina "
            f"hasta 24–48h post-sesión."
        ),
        "sugerencia": (
            f"Considera reducir el bolo de la comida previa al ejercicio en "
            f"un 20–30%, o consumir 15–20g de carbohidratos sin insulina antes "
            f"de entrenar. Monitorea la glucosa antes, durante y 2h después."
        ),
        "episodios": episodios[-5:],
    }


def _detectar_rebote_grasa_proteina(lecturas, meals_periodo) -> Optional[dict]:
    """
    Rebote tardío grasa/proteína: en comidas con >35g grasa o >50g proteína,
    la glucosa a las 3–5h es > 30 mg/dL más alta que el pico a 1–2h.
    """
    comidas_ricas = [
        c for c in meals_periodo
        if (c.fat_g or 0) > 35 or (c.protein_g or 0) > 50
    ]
    if not comidas_ricas:
        return None

    episodios = []
    for c in comidas_ricas:
        t0 = c.timestamp
        early = [r for r in lecturas
                 if t0 + timedelta(hours=1) <= r.timestamp <= t0 + timedelta(hours=2)]
        late  = [r for r in lecturas
                 if t0 + timedelta(hours=3) <= r.timestamp <= t0 + timedelta(hours=5)]
        if not early or not late:
            continue
        g_early = max(r.value_mgdl for r in early)
        g_late  = max(r.value_mgdl for r in late)
        if g_late > g_early + _REBOTE_MIN and g_late > 160:
            episodios.append({
                "fecha":   c.timestamp.strftime("%Y-%m-%d %H:%M"),
                "comida":  c.name,
                "fat_g":   round(c.fat_g or 0, 1),
                "prot_g":  round(c.protein_g or 0, 1),
                "g_early": round(g_early),
                "g_late":  round(g_late),
                "rebote":  round(g_late - g_early),
            })

    if len(episodios) < 2:
        return None

    avg_rebote = round(mean(e["rebote"] for e in episodios))
    return {
        "tipo":      "rebote_grasa_prot",
        "icono":     "bi-clock-history",
        "nivel":     "warning",
        "titulo":    "Rebote tardío de grasa y proteína",
        "frecuencia": len(episodios),
        "detalle":   (
            f"En {len(episodios)} comidas ricas en grasa (>35g) o proteína (>50g) "
            f"la glucosa volvió a subir {avg_rebote} mg/dL en promedio a las 3–5h "
            f"post-comida (efecto pizza). La gluconeogénesis de grasa/proteína "
            f"eleva la glucosa horas después de que el bolo original ya bajó."
        ),
        "sugerencia": (
            "Consulta con tu médico la estrategia de bolo dual o extendido "
            "para este tipo de comidas: por ejemplo 60% del bolo al empezar "
            "y 40% extendido 2–3 horas. Alternativamente puedes usar un "
            "bolo de corrección a las 2h si tu glucosa sigue en rango."
        ),
        "episodios": episodios[-5:],
    }


def _detectar_variabilidad_alta(lecturas) -> Optional[dict]:
    """
    Variabilidad glucémica excesiva: CV% > 36% (consenso ATTD 2019).
    CV = (SD / media) × 100.
    """
    vals = [r.value_mgdl for r in lecturas]
    if len(vals) < 30:
        return None
    avg = mean(vals)
    if avg == 0:
        return None
    sd  = stdev(vals)
    cv  = round(sd / avg * 100, 1)

    if cv <= _CV_UMBRAL:
        return None

    return {
        "tipo":      "variabilidad",
        "icono":     "bi-activity",
        "nivel":     "warning",
        "titulo":    f"Variabilidad glucémica elevada (CV {cv}%)",
        "frecuencia": None,
        "detalle":   (
            f"Tu coeficiente de variación glucémica es {cv}% "
            f"(meta: <36% según consenso ATTD 2019). "
            f"Alta variabilidad implica más tiempo en hipoglucemia e "
            f"hiperglucemia aunque el promedio parezca aceptable."
        ),
        "sugerencia": (
            "Revisa la consistencia de horarios de comidas, dosis e "
            "intervalos de inyección. La variabilidad suele mejorar "
            "estandarizando rutinas y ajustando el timing del bolo."
        ),
        "cv": cv,
        "sd": round(sd, 1),
        "avg": round(avg, 1),
    }


def _detectar_hipers_pre_comida(lecturas, meals_periodo) -> Optional[dict]:
    """
    Patrón de comer con glucosa alta sin corrección previa:
    glucosa pre-comida > 200 mg/dL sin bolus de corrección en los 30–60 min previos.
    """
    desde_bolus = min((c.timestamp for c in meals_periodo), default=None)
    if not desde_bolus:
        return None
    desde_bolus -= timedelta(hours=2)
    bolus_todos = InsulinDose.query.filter(
        InsulinDose.type == "bolus",
        InsulinDose.timestamp >= desde_bolus,
    ).all()

    episodios = []
    for c in meals_periodo:
        t0  = c.timestamp
        pre = next(
            (r for r in reversed(lecturas)
             if t0 - timedelta(minutes=30) <= r.timestamp <= t0
             and r.value_mgdl > 200),
            None,
        )
        if not pre:
            continue
        # ¿Hubo algún bolus de corrección 30–60 min ANTES (no el bolo de comida)?
        hay_corr = any(
            t0 - timedelta(hours=1) <= b.timestamp < t0 - timedelta(minutes=5)
            for b in bolus_todos
        )
        if not hay_corr:
            episodios.append({
                "fecha":   t0.strftime("%Y-%m-%d %H:%M"),
                "comida":  c.name,
                "pre_val": round(pre.value_mgdl),
            })

    if len(episodios) < 3:
        return None

    avg_pre = round(mean(e["pre_val"] for e in episodios))
    return {
        "tipo":      "hiper_pre_comida",
        "icono":     "bi-exclamation-triangle-fill",
        "nivel":     "warning",
        "titulo":    "Comidas frecuentes con hiperglucemia sin corrección",
        "frecuencia": len(episodios),
        "detalle":   (
            f"En {len(episodios)} comidas la glucosa previa superaba 200 mg/dL "
            f"(promedio {avg_pre} mg/dL) sin haber aplicado un bolo de corrección "
            f"previo. Empezar a comer en hiperglucemia amplifica la respuesta "
            f"postprandial y dificulta el control."
        ),
        "sugerencia": (
            "Cuando tu glucosa esté > 200 mg/dL antes de comer, considera "
            "aplicar un bolo de corrección 15–20 min antes de comenzar. "
            "Usa la calculadora para estimar la corrección más tu bolo de comida."
        ),
        "episodios": episodios[-5:],
    }


def _episodios_hipo(lecturas) -> list[dict]:
    """Colapsa lecturas <70 consecutivas (huecos <30 min) en episodios."""
    eps, cur = [], []
    for r in lecturas:
        if r.value_mgdl < _HIPO:
            if cur and (r.timestamp - cur[-1].timestamp) > timedelta(minutes=30):
                eps.append(cur)
                cur = []
            cur.append(r)
        elif cur:
            eps.append(cur)
            cur = []
    if cur:
        eps.append(cur)
    return [{"inicio": ep[0].timestamp, "fin": ep[-1].timestamp,
             "nadir": round(min(r.value_mgdl for r in ep))} for ep in eps]


def _detectar_franja_hipos(lecturas) -> Optional[dict]:
    """
    Franja horaria problemática: una VENTANA DESLIZANTE de 6h (con vuelta a
    medianoche) concentra ≥40% de los episodios de hipo, con ≥5 episodios.
    La ventana deslizante evita que un corte fijo (p.ej. a las 06:00) parta
    en dos una concentración real.
    """
    eps = _episodios_hipo(lecturas)
    if len(eps) < 5:
        return None

    horas = [e["inicio"].hour for e in eps]
    mejor = max(range(24), key=lambda s: sum(1 for h in horas if (h - s) % 24 < 6))
    lista = [e for e in eps if (e["inicio"].hour - mejor) % 24 < 6]
    if len(lista) < 5 or len(lista) / len(eps) < 0.40:
        return None

    fin = (mejor + 6) % 24
    ventana = f"{mejor:02d}:00–{fin:02d}:00"
    pct = round(100 * len(lista) / len(eps))
    # ¿la ventana cae mayormente en horas de sueño (00–08)?
    horas_sueno = sum(1 for k in range(6) if (mejor + k) % 24 < 8)
    es_nocturna = horas_sueno >= 3
    mecanismo = (
        "En esas horas estás durmiendo: la basal queda 'sola' frente a una "
        "glucosa que baja, no hay ingesta que compense y los síntomas no se "
        "sienten — por eso es la franja que más vale la pena vigilar."
        if es_nocturna else
        "Una concentración así sugiere que algo sistemático de esa franja "
        "(dosis, comida previa, actividad) está empujando la glucosa hacia abajo."
    )
    return {
        "tipo":      "franja_hipos",
        "icono":     "bi-moon-stars-fill" if es_nocturna else "bi-clock-fill",
        "nivel":     "danger",
        "titulo":    f"Tus hipoglucemias se concentran entre las {ventana}",
        "frecuencia": len(lista),
        "detalle":   (
            f"El {pct}% de tus episodios de hipoglucemia ({len(lista)} de {len(eps)}) "
            f"empezaron entre las {ventana}. {mecanismo}"
        ),
        "sugerencia": (
            "Llévale este dato a tu equipo médico: la concentración horaria es "
            "la pista clave para revisar qué la está causando (basal, cena, "
            "ejercicio del día)."
        ),
        "episodios": [{"fecha": e["inicio"].strftime("%Y-%m-%d %H:%M"),
                       "nadir": e["nadir"]} for e in lista[-5:]],
    }


def _detectar_hipo_tardia_comida_rica(lecturas, meals_periodo) -> Optional[dict]:
    """
    Hipo tardía tras comida baja en carbos y rica en proteína/grasa: aporta
    poca glucosa mientras la insulina activa (basal o bolo) sigue trabajando —
    la baja llega 2–7h después, muchas veces ya de noche. El bolo cercano se
    REPORTA si existe, pero no se exige (suele no quedar registrado junto a
    la comida). ≥3 episodios.
    """
    candidatas = [c for c in meals_periodo
                  if (c.carbs_g or 0) < 20 and ((c.protein_g or 0) + (c.fat_g or 0)) >= 25]
    if len(candidatas) < 3:
        return None

    desde = min(c.timestamp for c in candidatas) - timedelta(hours=2)
    bolus = InsulinDose.query.filter(
        InsulinDose.type == "bolus", InsulinDose.timestamp >= desde).all()
    eps = _episodios_hipo(lecturas)

    episodios = []
    for c in candidatas:
        hipo = next((e for e in eps
                     if c.timestamp + timedelta(hours=2) <= e["inicio"]
                     <= c.timestamp + timedelta(hours=7)), None)
        if not hipo:
            continue
        con_bolo = any(abs((b.timestamp - c.timestamp).total_seconds()) <= 90 * 60
                       for b in bolus)
        episodios.append({
            "fecha":  c.timestamp.strftime("%Y-%m-%d %H:%M"),
            "comida": c.name,
            "carbs":  round(c.carbs_g or 0),
            "prot_grasa": round((c.protein_g or 0) + (c.fat_g or 0)),
            "con_bolo": con_bolo,
            "nadir":  hipo["nadir"],
            "hora_hipo": hipo["inicio"].strftime("%H:%M"),
        })

    if len(episodios) < 3:
        return None

    n_bolo = sum(1 for e in episodios if e["con_bolo"])
    extra_bolo = (f" En {n_bolo} de ellas además había un bolo cerca de la comida."
                  if n_bolo else "")
    return {
        "tipo":      "hipo_tardia_comida_rica",
        "icono":     "bi-egg-fried",
        "nivel":     "danger",
        "titulo":    "Hipos tardías tras comidas con pocos carbohidratos",
        "frecuencia": len(episodios),
        "detalle":   (
            f"En {len(episodios)} ocasiones, después de una comida baja en "
            f"carbohidratos (<20g) pero rica en proteína/grasa, tuviste una hipo "
            f"2–7 horas más tarde.{extra_bolo} Ese tipo de comida aporta poca "
            f"glucosa mientras la insulina que sigue activa continúa trabajando: "
            f"la baja llega horas después, muchas veces ya durmiendo."
        ),
        "sugerencia": (
            "Cuéntale este patrón a tu equipo médico: cómo cubrir comidas altas "
            "en proteína y bajas en carbohidrato es un ajuste clásico (y muy "
            "personal) de la terapia."
        ),
        "episodios": episodios[-5:],
    }


def _detectar_impacto_contexto(lecturas) -> Optional[dict]:
    """
    Días etiquetados (estrés/enfermedad/mal sueño…) vs. días sin etiqueta:
    diferencia de promedio ≥10 mg/dL con ≥3 días etiquetados.
    """
    if not _CONTEXT_OK or not lecturas:
        return None
    desde = lecturas[0].timestamp
    tags = ContextTag.query.filter(ContextTag.timestamp >= desde).all()
    if not tags:
        return None

    por_dia = defaultdict(list)
    for r in lecturas:
        por_dia[r.timestamp.date()].append(r.value_mgdl)
    medias_dia = {d: mean(v) for d, v in por_dia.items() if len(v) >= 24}

    etiquetas = defaultdict(set)
    for t in tags:
        etiquetas[t.tag].add(t.timestamp.date())

    mejores = None
    for tag, dias in etiquetas.items():
        con = [m for d, m in medias_dia.items() if d in dias]
        sin = [m for d, m in medias_dia.items() if d not in dias]
        if len(con) < 3 or len(sin) < 3:
            continue
        delta = round(mean(con) - mean(sin))
        if abs(delta) >= 10 and (mejores is None or abs(delta) > abs(mejores[1])):
            mejores = (tag, delta, len(con))

    if not mejores:
        return None
    tag, delta, n = mejores
    labels = {"estres": "estrés", "enfermo": "enfermedad", "mal_sueno": "mal sueño",
              "viaje": "viaje", "alcohol": "alcohol"}
    nombre = labels.get(tag, tag)
    direccion = "más alto" if delta > 0 else "más bajo"
    mecanismo = ("El cortisol y la adrenalina reducen la sensibilidad a la insulina."
                 if delta > 0 else
                 "Puede reflejar menos ingesta, más movimiento o el efecto del alcohol.")
    return {
        "tipo":      "impacto_contexto",
        "icono":     "bi-tags-fill",
        "nivel":     "warning",
        "titulo":    f"Los días con {nombre} tu glucosa corre {direccion}",
        "frecuencia": n,
        "detalle":   (
            f"En los {n} días que marcaste «{nombre}», tu promedio fue "
            f"{abs(delta)} mg/dL {direccion} que en los días sin esa etiqueta. "
            f"{mecanismo}"
        ),
        "sugerencia": (
            "Sigue etiquetando esos días: cuantos más datos, más clara la señal. "
            "Es información valiosa para conversar el manejo de esos días con tu equipo."
        ),
    }


def _detectar_dia_semana(lecturas) -> Optional[dict]:
    """
    Efecto día-de-semana: un día puntual con promedio ≥15 mg/dL distinto al
    resto (≥3 instancias de ese día con datos suficientes).
    """
    por_dia = defaultdict(list)
    for r in lecturas:
        por_dia[r.timestamp.date()].append(r.value_mgdl)
    medias_dia = {d: mean(v) for d, v in por_dia.items() if len(v) >= 24}
    if len(medias_dia) < 10:
        return None

    nombres = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
    mejor = None
    for wd in range(7):
        del_dia = [m for d, m in medias_dia.items() if d.weekday() == wd]
        resto   = [m for d, m in medias_dia.items() if d.weekday() != wd]
        if len(del_dia) < 3 or len(resto) < 5:
            continue
        delta = round(mean(del_dia) - mean(resto))
        if abs(delta) >= 15 and (mejor is None or abs(delta) > abs(mejor[1])):
            mejor = (wd, delta, len(del_dia))

    if not mejor:
        return None
    wd, delta, n = mejor
    direccion = "más alto" if delta > 0 else "más bajo"
    plural = "los " + nombres[wd] if wd != 6 else "los domingos"
    return {
        "tipo":      "dia_semana",
        "icono":     "bi-calendar-week-fill",
        "nivel":     "info",
        "titulo":    f"Patrón de {nombres[wd]}: promedio {direccion}",
        "frecuencia": n,
        "detalle":   (
            f"En {plural} ({n} analizados) tu promedio fue {abs(delta)} mg/dL "
            f"{direccion} que el resto de la semana. Los cambios de rutina "
            f"(horarios, comidas, actividad, salidas) suelen estar detrás de "
            f"este tipo de patrón semanal."
        ),
        "sugerencia": (
            "Piensa qué haces distinto ese día (horarios, comidas, movimiento) — "
            "identificarlo es el primer paso para decidir, con tu equipo, si vale "
            "la pena ajustar algo."
        ),
    }


def _detectar_basal_sin_registrar(lecturas, days) -> Optional[dict]:
    """
    Hueco de registro: la persona usa basal pero en ≥40% de los días del
    período no quedó registrada. No es un patrón fisiológico sino de datos,
    pero limita todo el análisis nocturno — mejor decirlo con honestidad.
    """
    desde = _ahora_local() - timedelta(days=days)
    basales = InsulinDose.query.filter(
        InsulinDose.type == "basal", InsulinDose.timestamp >= desde).all()
    if not basales:
        return None   # no usa basal registrada — nada que decir

    dias_con_datos = {r.timestamp.date() for r in lecturas}
    if len(dias_con_datos) < 7:
        return None
    dias_con_basal = {b.timestamp.date() for b in basales}
    faltantes = len(dias_con_datos - dias_con_basal)
    pct = round(100 * faltantes / len(dias_con_datos))
    if pct < 40:
        return None

    return {
        "tipo":      "basal_sin_registrar",
        "icono":     "bi-journal-x",
        "nivel":     "info",
        "titulo":    "La basal quedó sin registrar muchos días",
        "frecuencia": faltantes,
        "detalle":   (
            f"En {faltantes} de {len(dias_con_datos)} días con datos ({pct}%) no "
            f"quedó registrada la insulina basal. Sin ese dato pierdo precisión "
            f"para analizar tus noches y madrugadas — justo donde más pasa."
        ),
        "sugerencia": (
            "El recordatorio de basal de la app ayuda; registrarla lleva dos toques "
            "y hace mucho más útil todo el análisis nocturno."
        ),
    }


def _serie_compacta(lecturas, meals_periodo, days) -> list[dict]:
    """
    Serie temporal de glucosa de los últimos `days` días en formato compacto,
    anotada con eventos (comidas, insulina) para Capa 3 (Claude API).
    Solo exporta un punto cada 15 min para reducir tamaño.
    """
    if not lecturas:
        return []

    # Agrupar lecturas en bins de 15 min
    bins: dict[datetime, list] = defaultdict(list)
    for r in lecturas:
        # Redondear a múltiplos de 15 min
        ts = r.timestamp.replace(second=0, microsecond=0)
        minutos = (ts.minute // 15) * 15
        bin_ts = ts.replace(minute=minutos)
        bins[bin_ts].append(r.value_mgdl)

    serie = []
    for ts in sorted(bins):
        vals = bins[ts]
        serie.append({
            "ts":  ts.strftime("%Y-%m-%dT%H:%M"),
            "g":   round(mean(vals)),
            "n":   len(vals),
        })

    # Anotar con comidas e insulina
    desde = _ahora_local() - timedelta(days=days)
    insulinas = InsulinDose.query.filter(
        InsulinDose.timestamp >= desde
    ).all()

    for punto in serie:
        ts_p = datetime.strptime(punto["ts"], "%Y-%m-%dT%H:%M")
        t_ini = ts_p - timedelta(minutes=7)
        t_fin = ts_p + timedelta(minutes=8)
        comida_bin = next(
            (c for c in meals_periodo if t_ini <= c.timestamp < t_fin), None
        )
        # Formato explícito del tipo para que la IA no confunda basal con bolus
        ins_bin = []
        for i in insulinas:
            if t_ini <= i.timestamp < t_fin:
                tipo_label = ("RÁPIDA" if i.type == "bolus" else
                              "BASAL"  if i.type == "basal" else
                              i.type.upper())
                purpose_label = ""
                if i.type == "bolus" and getattr(i, "purpose", None):
                    purpose_label = f"/{i.purpose}"
                ins_bin.append(f"{i.units}U {tipo_label}{purpose_label}")
        if comida_bin:
            punto["comida"] = f"{comida_bin.name} {round(comida_bin.carbs_g or 0)}gCH"
        if ins_bin:
            punto["insulina"] = "; ".join(ins_bin)

    return serie


def analizar_patrones(days: int = 30) -> dict:
    """
    Punto de entrada principal de Capa 2.

    Retorna:
        patrones       — lista de patrones detectados (puede estar vacía)
        resumen        — métricas clave (avg, SD, CV, TIR, n_lecturas)
        serie_glucose  — serie compacta para Capa 3 (Claude API)
        generado_en    — timestamp ISO
    """
    desde = _ahora_local() - timedelta(days=days)

    lecturas = (
        GlucoseReading.query
        .filter(GlucoseReading.timestamp >= desde)
        .order_by(GlucoseReading.timestamp)
        .all()
    )
    meals_periodo = Meal.query.filter(Meal.timestamp >= desde).all()

    patrones_detectados = []

    # ── Correr detectores ────────────────────────────────────────────────────
    detectors = [
        lambda: _detectar_somogyi(lecturas, days),
        lambda: _detectar_fenomeno_alba(lecturas, days),
        lambda: _detectar_hipo_post_ejercicio(lecturas, days),
        lambda: _detectar_rebote_grasa_proteina(lecturas, meals_periodo),
        lambda: _detectar_variabilidad_alta(lecturas),
        lambda: _detectar_hipers_pre_comida(lecturas, meals_periodo),
        lambda: _detectar_franja_hipos(lecturas),
        lambda: _detectar_hipo_tardia_comida_rica(lecturas, meals_periodo),
        lambda: _detectar_impacto_contexto(lecturas),
        lambda: _detectar_dia_semana(lecturas),
        lambda: _detectar_basal_sin_registrar(lecturas, days),
    ]
    for fn in detectors:
        try:
            resultado = fn()
            if resultado:
                patrones_detectados.append(resultado)
        except Exception:
            pass  # nunca romper el dashboard por un detector

    # ── Métricas de resumen ──────────────────────────────────────────────────
    vals = [r.value_mgdl for r in lecturas]
    resumen = {
        "n_lecturas": len(vals),
        "avg":        round(mean(vals), 1)   if vals else None,
        "sd":         round(stdev(vals), 1)  if len(vals) > 1 else None,
        "cv":         None,
        "tir":        None,
        "hipo_pct":   None,
        "hiper_pct":  None,
    }
    if resumen["avg"] and resumen["sd"]:
        resumen["cv"] = round(resumen["sd"] / resumen["avg"] * 100, 1)
    if vals:
        resumen["tir"]       = round(100 * sum(1 for v in vals if 70 <= v <= 180) / len(vals))
        resumen["hipo_pct"]  = round(100 * sum(1 for v in vals if v < 70) / len(vals), 1)
        resumen["hiper_pct"] = round(100 * sum(1 for v in vals if v > 180) / len(vals), 1)

    # ── Serie compacta para Capa 3 ───────────────────────────────────────────
    serie = _serie_compacta(lecturas, meals_periodo, days)

    return {
        "patrones":      patrones_detectados,
        "resumen":       resumen,
        "serie_glucose": serie,
        "generado_en":   _ahora_local().strftime("%Y-%m-%dT%H:%M:%S"),
        "days":          days,
    }
