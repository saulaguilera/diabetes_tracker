"""
Motor de recomendaciones personalizadas basado en patrones de datos reales.

Cada recomendación tiene:
  - category:  glucemia | comidas | insulina | ejercicio | habitos
  - priority:  urgente | importante | informativo
  - title:     frase corta
  - body:      explicación con datos reales
  - action:    qué hacer concreto
  - data:      dict con los números que sustentan la recomendación
"""

from datetime import datetime, timedelta
from collections import defaultdict


def _now():
    return datetime.now()  # hora local del servidor (TZ=America/Santiago)


def _readings(days=30):
    from models import GlucoseReading
    desde = _now() - timedelta(days=days)
    return GlucoseReading.query.filter(
        GlucoseReading.timestamp >= desde
    ).order_by(GlucoseReading.timestamp.asc()).all()


def _meals(days=30):
    from models import Meal
    desde = _now() - timedelta(days=days)
    return Meal.query.filter(
        Meal.timestamp >= desde
    ).order_by(Meal.timestamp.asc()).all()


def _load_bolus(days=30):
    """Carga todos los bolus del período en UNA sola query."""
    from models import InsulinDose
    desde = _now() - timedelta(days=days)
    return InsulinDose.query.filter(
        InsulinDose.type == "bolus",
        InsulinDose.timestamp >= desde,
    ).order_by(InsulinDose.timestamp.asc()).all()


# ─── Analizadores individuales ────────────────────────────────────────────────

def _rec_tiempo_en_rango(readings) -> dict | None:
    if len(readings) < 48:
        return None
    vals = [r.value_mgdl for r in readings]
    n = len(vals)
    tir   = round(len([v for v in vals if 70 <= v <= 180]) / n * 100, 1)
    hipo  = round(len([v for v in vals if v < 70])  / n * 100, 1)
    hiper = round(len([v for v in vals if v > 180]) / n * 100, 1)

    if tir >= 70 and hipo < 4:
        return {
            "category": "glucemia",
            "priority": "informativo",
            "icon": "bi-check-circle-fill",
            "color": "success",
            "title": f"Buen tiempo en rango: {tir}%",
            "body": (
                f"En los últimos 30 días estuviste en rango (70–180 mg/dL) el {tir}% del tiempo. "
                f"La meta recomendada es ≥70%. ¡Vas bien!"
            ),
            "action": "Mantén los hábitos actuales y sigue registrando para detectar cualquier cambio.",
            "data": {"tir": tir, "hipo": hipo, "hiper": hiper, "lecturas": n},
        }
    if hipo >= 4:
        return {
            "category": "glucemia",
            "priority": "urgente",
            "icon": "bi-exclamation-triangle-fill",
            "color": "danger",
            "title": f"Hipoglucemia frecuente: {hipo}% del tiempo",
            "body": (
                f"Estuviste por debajo de 70 mg/dL el {hipo}% del tiempo "
                f"({round(hipo * 24 * 0.3, 0):.0f} min/día en promedio). "
                f"La meta es estar menos del 4% del tiempo en hipoglucemia."
            ),
            "action": (
                "Habla con tu endocrinólogo sobre ajustar la dosis de insulina basal "
                "o tu relación insulina:carbohidrato. No hagas ajustes sin supervisión médica."
            ),
            "data": {"tir": tir, "hipo": hipo, "hiper": hiper, "lecturas": n},
        }
    if tir < 50:
        return {
            "category": "glucemia",
            "priority": "urgente",
            "icon": "bi-exclamation-triangle-fill",
            "color": "danger",
            "title": f"Tiempo en rango muy bajo: {tir}%",
            "body": (
                f"Solo el {tir}% del tiempo estás en rango. "
                f"El {hiper}% del tiempo estás por encima de 180 mg/dL. "
                f"La hiperglucemia sostenida aumenta el riesgo de complicaciones."
            ),
            "action": "Consulta a tu médico pronto. Lleva estos datos a tu próxima cita.",
            "data": {"tir": tir, "hipo": hipo, "hiper": hiper},
        }
    if tir < 70:
        return {
            "category": "glucemia",
            "priority": "importante",
            "icon": "bi-arrow-up-circle-fill",
            "color": "warning",
            "title": f"Tiempo en rango mejorable: {tir}%",
            "body": (
                f"Estás en rango el {tir}% del tiempo. La meta es ≥70%. "
                f"El {hiper}% del tiempo estás por encima de 180 mg/dL."
            ),
            "action": (
                "Revisa los patrones de comidas y horarios en los reportes para identificar "
                "qué momentos del día te sacan más del rango."
            ),
            "data": {"tir": tir, "hipo": hipo, "hiper": hiper},
        }
    return None


def _rec_patron_circadiano(readings) -> dict | None:
    if len(readings) < 72:
        return None

    por_hora = defaultdict(list)
    for r in readings:
        por_hora[r.timestamp.hour].append(r.value_mgdl)

    promedios = {h: sum(v) / len(v) for h, v in por_hora.items() if len(v) >= 3}
    if not promedios:
        return None

    hora_max = max(promedios, key=promedios.get)
    val_max  = round(promedios[hora_max], 0)
    hora_min = min(promedios, key=promedios.get)
    val_min  = round(promedios[hora_min], 0)

    if val_max > 200:
        return {
            "category": "glucemia",
            "priority": "importante",
            "icon": "bi-clock-fill",
            "color": "warning",
            "title": f"Glucemia alta recurrente a las {hora_max:02d}:00 h",
            "body": (
                f"Tu promedio de glucemia a las {hora_max:02d}:00 h es de {val_max:.0f} mg/dL, "
                f"el más alto del día. Esto puede indicar el fenómeno del alba, "
                f"una comida previa con muchos carbohidratos, o una dosis de basal insuficiente en ese horario."
            ),
            "action": (
                f"Registra qué comes o haces antes de las {hora_max:02d}:00 h. "
                f"Si el patrón persiste, coméntalo con tu endocrinólogo para revisar la insulina basal."
            ),
            "data": {"hora_pico": hora_max, "promedio_pico": val_max,
                     "hora_min": hora_min, "promedio_min": val_min},
        }

    variabilidades = {}
    for h, vals in por_hora.items():
        if len(vals) >= 5:
            promedio = sum(vals) / len(vals)
            desv = (sum((v - promedio) ** 2 for v in vals) / len(vals)) ** 0.5
            variabilidades[h] = round(desv, 1)

    if variabilidades:
        hora_var = max(variabilidades, key=variabilidades.get)
        val_var  = variabilidades[hora_var]
        if val_var > 50:
            return {
                "category": "glucemia",
                "priority": "informativo",
                "icon": "bi-activity",
                "color": "info",
                "title": f"Alta variabilidad glucémica a las {hora_var:02d}:00 h",
                "body": (
                    f"A las {hora_var:02d}:00 h tus lecturas varían mucho "
                    f"(desviación estándar de {val_var:.0f} mg/dL). "
                    f"Esto sugiere que en ese horario hay factores que afectan tu glucemia de forma inconsistente."
                ),
                "action": "Revisa si en ese horario comes cosas diferentes cada día o si hay actividad física variable.",
                "data": {"hora": hora_var, "desviacion": val_var},
            }
    return None


def _rec_hipoglucemia_nocturna(readings) -> dict | None:
    if len(readings) < 48:
        return None

    hipos_nocturnas = [
        r for r in readings
        if r.value_mgdl < 70 and 0 <= r.timestamp.hour < 6
    ]
    total_noches = max(
        (readings[-1].timestamp - readings[0].timestamp).days, 1
    )
    if len(hipos_nocturnas) >= 3:
        return {
            "category": "glucemia",
            "priority": "urgente",
            "icon": "bi-moon-stars-fill",
            "color": "danger",
            "title": f"Hipoglucemia nocturna recurrente ({len(hipos_nocturnas)} episodios)",
            "body": (
                f"Tienes {len(hipos_nocturnas)} lecturas por debajo de 70 mg/dL entre la medianoche y las 6:00 am "
                f"en los últimos {total_noches} días. "
                f"La hipoglucemia nocturna es especialmente peligrosa porque puede pasar desapercibida."
            ),
            "action": (
                "Habla con tu médico sobre ajustar la insulina basal nocturna. "
                "Considera un snack antes de dormir si tu glucemia es menor a 120 mg/dL al acostarte."
            ),
            "data": {"episodios": len(hipos_nocturnas), "dias": total_noches},
        }
    return None


def _rec_comidas_problematicas(meals, all_readings) -> dict | None:
    """
    Detecta comidas que disparan la glucemia más de lo esperado.
    Usa all_readings pre-cargado — sin queries por comida.
    """
    if len(meals) < 5:
        return None

    resultados = []
    for comida in meals:
        t0 = comida.timestamp
        # Pre: lectura más reciente hasta 30 min antes de comer
        pre_list = [r for r in all_readings
                    if t0 - timedelta(minutes=30) <= r.timestamp <= t0]
        # Post: lecturas en las 2 h siguientes
        post_list = [r for r in all_readings
                     if t0 < r.timestamp <= t0 + timedelta(hours=2)]

        if not pre_list or not post_list:
            continue

        pre  = pre_list[-1].value_mgdl   # la más cercana antes
        pico = max(r.value_mgdl for r in post_list)
        delta = pico - pre
        resultados.append({
            "nombre": comida.name,
            "carbs":  comida.carbs_g,
            "pre":    pre,
            "pico":   pico,
            "delta":  delta,
        })

    if len(resultados) < 3:
        return None

    por_comida = defaultdict(list)
    for r in resultados:
        por_comida[r["nombre"]].append(r["delta"])

    promedios_delta = {
        nombre: round(sum(deltas) / len(deltas), 0)
        for nombre, deltas in por_comida.items()
        if len(deltas) >= 2
    }

    if not promedios_delta:
        peor = max(resultados, key=lambda x: x["delta"])
        if peor["delta"] > 100:
            return {
                "category": "comidas",
                "priority": "importante",
                "icon": "bi-egg-fried",
                "color": "warning",
                "title": f"'{peor['nombre']}' te disparó +{peor['delta']:.0f} mg/dL",
                "body": (
                    f"Al comer '{peor['nombre']}' ({peor['carbs']}g CH) "
                    f"tu glucemia subió de {peor['pre']:.0f} a {peor['pico']:.0f} mg/dL "
                    f"(+{peor['delta']:.0f} mg/dL) en 2 horas."
                ),
                "action": "Registra más veces esta comida para confirmar el patrón.",
                "data": peor,
            }
        return None

    peor_nombre = max(promedios_delta, key=promedios_delta.get)
    peor_delta  = promedios_delta[peor_nombre]

    if peor_delta > 80:
        veces = len(por_comida[peor_nombre])
        return {
            "category": "comidas",
            "priority": "importante",
            "icon": "bi-egg-fried",
            "color": "warning",
            "title": f"'{peor_nombre}' sube tu glucemia +{peor_delta:.0f} mg/dL en promedio",
            "body": (
                f"En {veces} registros, comer '{peor_nombre}' te sube la glucemia "
                f"un promedio de {peor_delta:.0f} mg/dL en las 2 horas siguientes. "
                f"Un alza de más de 80 mg/dL post-comida suele indicar que necesitas "
                f"más insulina de bolo o reducir la porción."
            ),
            "action": (
                f"Prueba reducir la porción de '{peor_nombre}' o adelantar la dosis de bolo "
                f"10–15 min antes de comer. Consulta con tu médico antes de cambiar dosis."
            ),
            "data": {"comida": peor_nombre, "delta_promedio": peor_delta, "veces": veces},
        }
    return None


def _rec_ratio_insulina_carbs(meals, all_readings, all_bolus) -> dict | None:
    """
    Estima el ratio I:C basado en comidas donde la glucemia se mantuvo en rango.
    Usa all_readings y all_bolus pre-cargados — sin queries por comida.
    """
    if len(meals) < 10:
        return None

    puntos_bien = []
    for comida in meals:
        if comida.carbs_g < 10:
            continue

        t0 = comida.timestamp

        # Bolus prandial: ventana -30 / +15 min
        bolus_cercanos = [b for b in all_bolus
                          if t0 - timedelta(minutes=30) <= b.timestamp <= t0 + timedelta(minutes=15)]
        if not bolus_cercanos:
            continue
        bolo = bolus_cercanos[0]  # el primero (más cercano antes de comer)

        # Glucemia 90–150 min post-comida
        post = [r for r in all_readings
                if t0 + timedelta(minutes=90) <= r.timestamp <= t0 + timedelta(minutes=150)]
        if not post:
            continue

        promedio_post = sum(r.value_mgdl for r in post) / len(post)
        if 80 <= promedio_post <= 160:
            ratio = round(comida.carbs_g / bolo.units, 1)
            puntos_bien.append(ratio)

    if len(puntos_bien) < 3:
        return None

    ratio_estimado = round(sum(puntos_bien) / len(puntos_bien), 1)

    return {
        "category": "insulina",
        "priority": "informativo",
        "icon": "bi-calculator-fill",
        "color": "info",
        "title": f"Ratio estimado: 1U por cada {ratio_estimado}g de carbohidratos",
        "body": (
            f"Basado en {len(puntos_bien)} comidas donde tu glucemia se mantuvo en rango "
            f"(80–160 mg/dL) 2 horas después, tu ratio insulina:carbohidrato parece ser "
            f"aproximadamente 1U por cada {ratio_estimado}g de CH."
        ),
        "action": (
            "Este es solo un estimado calculado por la app — no ajustes dosis sin "
            "confirmarlo con tu endocrinólogo. Puede variar por hora del día y actividad física."
        ),
        "data": {"ratio": ratio_estimado, "muestras": len(puntos_bien)},
    }


def _rec_carbohidratos_altos(meals) -> dict | None:
    if len(meals) < 5:
        return None

    carbs_vals = [m.carbs_g for m in meals if m.carbs_g > 0]
    if not carbs_vals:
        return None

    promedio_carbs = round(sum(carbs_vals) / len(carbs_vals), 0)
    comidas_altas  = [m for m in meals if m.carbs_g > 60]
    pct_altas      = round(len(comidas_altas) / len(meals) * 100, 0)

    if promedio_carbs > 55 and pct_altas > 30:
        return {
            "category": "comidas",
            "priority": "informativo",
            "icon": "bi-bar-chart-fill",
            "color": "info",
            "title": f"Promedio de {promedio_carbs:.0f}g de CH por comida",
            "body": (
                f"El {pct_altas:.0f}% de tus comidas registradas tienen más de 60g de carbohidratos. "
                f"Porciones grandes de carbohidratos son más difíciles de cubrir con precisión "
                f"con insulina de bolo y generan mayor variabilidad glucémica."
            ),
            "action": (
                "Considera dividir porciones grandes en dos comidas más pequeñas "
                "o explorar alternativas con menor índice glucémico."
            ),
            "data": {"promedio_carbs": promedio_carbs, "pct_altas": pct_altas},
        }
    return None


def _rec_pocos_datos() -> dict:
    return {
        "category": "habitos",
        "priority": "informativo",
        "icon": "bi-database-fill-add",
        "color": "secondary",
        "title": "Necesitas más datos para recomendaciones precisas",
        "body": (
            "Aún no hay suficientes datos para generar análisis personalizados. "
            "Para obtener recomendaciones útiles necesitas al menos 7 días de lecturas "
            "de glucemia y 10 comidas registradas."
        ),
        "action": (
            "Importa el CSV del Freestyle Libre para cargar tus lecturas históricas, "
            "y empieza a registrar tus comidas diariamente."
        ),
        "data": {},
    }


def _rec_registros_inconsistentes(meals, days=14) -> dict | None:
    if not meals:
        return None
    dias_con_registro = len({m.timestamp.date() for m in meals})
    if dias_con_registro < days * 0.5:
        pct = round(dias_con_registro / days * 100, 0)
        return {
            "category": "habitos",
            "priority": "importante",
            "icon": "bi-journal-x",
            "color": "warning",
            "title": f"Solo registraste comidas el {pct:.0f}% de los días",
            "body": (
                f"En los últimos {days} días, solo tienes comidas registradas en "
                f"{dias_con_registro} de ellos. Los patrones y recomendaciones son más precisos "
                f"cuando el registro es consistente."
            ),
            "action": (
                "Intenta registrar al menos las comidas principales cada día. "
                "Guarda tus alimentos frecuentes para que sea más rápido."
            ),
            "data": {"dias_registrados": dias_con_registro, "dias_total": days},
        }
    return None


# ─── Función principal ────────────────────────────────────────────────────────

def generate_recommendations(days: int = 30) -> list[dict]:
    """
    Genera lista de recomendaciones ordenadas por prioridad.
    Todas las queries de DB se hacen aquí en batch — los analizadores
    individuales trabajan solo con listas en memoria.
    """
    readings  = _readings(days)
    meals     = _meals(days)
    all_bolus = _load_bolus(days)   # una sola query extra para los ratios

    PRIORITY_ORDER = {"urgente": 0, "importante": 1, "informativo": 2}

    if len(readings) < 24 and len(meals) < 5:
        return [_rec_pocos_datos()]

    recs = []

    analizadores = [
        _rec_tiempo_en_rango(readings),
        _rec_hipoglucemia_nocturna(readings),
        _rec_patron_circadiano(readings),
        _rec_comidas_problematicas(meals, readings),          # in-memory
        _rec_ratio_insulina_carbs(meals, readings, all_bolus), # in-memory
        _rec_carbohidratos_altos(meals),
        _rec_registros_inconsistentes(meals, days=min(days, 14)),
    ]

    for rec in analizadores:
        if rec is not None:
            recs.append(rec)

    if not recs:
        recs.append(_rec_pocos_datos())

    recs.sort(key=lambda r: PRIORITY_ORDER.get(r["priority"], 99))
    return recs
