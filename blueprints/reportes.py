from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, make_response
from datetime import datetime, timedelta
from collections import defaultdict
from models import db, GlucoseReading, Meal, InsulinDose, Activity
from helpers import (
    _precargar_glucosa, _precargar_bolus, _glucosa_impacto,
    _get_setting, _set_setting,
)
from utils.charts import (
    chart_glucose_timeline,
    chart_time_in_range,
    chart_glucose_by_hour,
    chart_meal_impact,
    chart_glucose_vs_carbs,
    chart_timeline_eventos,
    chart_activity_glucose_impact,
    chart_agp,
)
from utils.pdf_charts import chart_pdf_tir, chart_pdf_circadiano, chart_pdf_timeline
from utils.pdf_report import generar_pdf

bp = Blueprint("reportes", __name__)


def _tabla_impacto_comidas(days):
    """
    Calcula el impacto glucémico de cada comida registrada.
    Devuelve (filas_individuales, resumen_por_alimento).
    """
    desde = datetime.now() - timedelta(days=days)
    comidas = Meal.query.filter(Meal.timestamp >= desde).order_by(Meal.timestamp.desc()).all()

    # Precarga batch: 2 queries en lugar de 3N ──────────────────────────────
    all_readings = _precargar_glucosa(comidas, horas_post=2)
    all_bolus    = _precargar_bolus(comidas)

    filas = []
    for c in comidas:
        t0 = c.timestamp
        # Pre-comida en memoria
        pre_list  = [r for r in all_readings if t0 - timedelta(minutes=30) <= r.timestamp <= t0]
        post_list = [r for r in all_readings if t0 < r.timestamp <= t0 + timedelta(hours=2)]
        pre = pre_list[-1] if pre_list else None

        if not pre or not post_list:
            continue
        pico  = max(r.value_mgdl for r in post_list)
        delta = round(pico - pre.value_mgdl, 0)
        estado = "hiper" if pico > 180 else ("hipo" if pico < 70 else "rango")

        # Bolus en memoria
        dosis_cercanas = [
            d for d in all_bolus
            if t0 - timedelta(hours=1) <= d.timestamp <= t0 + timedelta(minutes=30)
        ]
        insulina = []
        for d in dosis_cercanas:
            diff_min = int((d.timestamp - c.timestamp).total_seconds() / 60)
            if diff_min < 0:
                timing = f"{abs(diff_min)} min antes"
            elif diff_min == 0:
                timing = "al comer"
            else:
                timing = f"{diff_min} min después"
            insulina.append({
                "units":  d.units,
                "type":   d.type,
                "timing": timing,
            })

        # Componentes de esta comida (para mostrar en la tabla de detalle)
        comp_list = [
            {"nombre": comp.name, "carbs": int(comp.carbs_g or 0)}
            for comp in c.components
        ]

        filas.append({
            "fecha":       c.timestamp.strftime("%d/%m %H:%M"),
            "nombre":      c.name,
            "carbs":       int(c.carbs_g or 0),
            "componentes": comp_list,   # ingredientes desglosados
            "pre":         int(pre.value_mgdl),
            "pico":        int(pico),
            "delta":       int(delta),
            "estado":      estado,
            "insulina":    insulina,
        })

    # ── Resumen por ingrediente (no por nombre de plato) ─────────────────────
    agrupado = defaultdict(list)
    for f in filas:
        if f["componentes"]:
            # Agrupa por ingrediente individual: cada uno hereda el impacto
            # glucémico del plato completo en el que apareció (delta / pico compartido)
            for comp in f["componentes"]:
                agrupado[comp["nombre"]].append({
                    "carbs": comp["carbs"],
                    "pico":  f["pico"],
                    "delta": f["delta"],
                })
        else:
            # Comida sin componentes (registro antiguo): grupo por nombre del plato
            agrupado[f["nombre"]].append({
                "carbs": f["carbs"],
                "pico":  f["pico"],
                "delta": f["delta"],
            })

    resumen = []
    for nombre, eventos in sorted(agrupado.items(), key=lambda x: -sum(e["delta"] for e in x[1]) / len(x[1])):
        n = len(eventos)
        avg_delta = round(sum(e["delta"] for e in eventos) / n)
        avg_pico  = round(sum(e["pico"]  for e in eventos) / n)
        avg_carbs = round(sum(e["carbs"] for e in eventos) / n)
        resumen.append({
            "nombre":    nombre,
            "n":         n,
            "avg_carbs": avg_carbs,
            "avg_pico":  avg_pico,
            "avg_delta": avg_delta,
        })

    return filas, resumen


def _calcular_isf_personal(days=60):
    """
    Estima el Factor de Sensibilidad a la Insulina (ISF) personal.
    Busca bolus sin comida cercana (correcciones puras) y mide la caída de glucosa.
    Retorna (isf_promedio, n_muestras).
    """
    desde = datetime.now() - timedelta(days=days)
    bolus_list = InsulinDose.query.filter(
        InsulinDose.type == "bolus",
        InsulinDose.timestamp >= desde,
    ).all()

    muestras = []
    for d in bolus_list:
        comida = Meal.query.filter(
            Meal.timestamp >= d.timestamp - timedelta(minutes=30),
            Meal.timestamp <= d.timestamp + timedelta(minutes=30),
        ).first()
        if comida:
            continue

        pre = (
            GlucoseReading.query
            .filter(
                GlucoseReading.timestamp >= d.timestamp - timedelta(minutes=30),
                GlucoseReading.timestamp <= d.timestamp + timedelta(minutes=15),
            )
            .order_by(GlucoseReading.timestamp.desc())
            .first()
        )

        posts = (
            GlucoseReading.query
            .filter(
                GlucoseReading.timestamp > d.timestamp + timedelta(minutes=30),
                GlucoseReading.timestamp <= d.timestamp + timedelta(hours=3),
            )
            .all()
        )

        if not pre or not posts or d.units <= 0:
            continue

        nadir = min(r.value_mgdl for r in posts)
        if nadir >= pre.value_mgdl:
            continue

        isf = (pre.value_mgdl - nadir) / d.units
        if 10 <= isf <= 200:
            muestras.append(round(isf, 1))

    if len(muestras) >= 2:
        return round(sum(muestras) / len(muestras), 1), len(muestras)
    return None, len(muestras)


def _calcular_icr_personal(days=90):
    """
    Estima el ratio Insulina:Carbohidratos (ICR) personal.
    Retorna (icr_promedio, n_muestras).
    """
    desde = datetime.now() - timedelta(days=days)
    isf_personal, _ = _calcular_isf_personal(days=days)

    comidas = Meal.query.filter(
        Meal.timestamp >= desde,
        Meal.carbs_g > 0,
    ).all()

    muestras = []
    for c in comidas:
        if not c.carbs_g or c.carbs_g < 5:
            continue

        pre = (GlucoseReading.query
               .filter(
                   GlucoseReading.timestamp >= c.timestamp - timedelta(minutes=30),
                   GlucoseReading.timestamp <= c.timestamp + timedelta(minutes=5),
               )
               .order_by(GlucoseReading.timestamp.desc()).first())
        if not pre:
            continue

        bolus = (InsulinDose.query
                 .filter(
                     InsulinDose.type == "bolus",
                     InsulinDose.timestamp >= c.timestamp - timedelta(minutes=15),
                     InsulinDose.timestamp <= c.timestamp + timedelta(minutes=30),
                 )
                 .order_by(InsulinDose.timestamp).first())
        if not bolus or bolus.units <= 0:
            continue

        objetivo = float(_get_setting("objetivo", 100))
        isf = isf_personal or 40
        correccion = max(0, (pre.value_mgdl - objetivo) / isf)
        bolo_comida = bolus.units - correccion

        if bolo_comida <= 0.2:
            continue

        icr = c.carbs_g / bolo_comida
        if 3 <= icr <= 30:
            muestras.append(round(icr, 1))

    if len(muestras) >= 3:
        return round(sum(muestras) / len(muestras), 1), len(muestras)
    return None, len(muestras)


@bp.route("/agp", endpoint="agp")
def agp():
    """Ambulatory Glucose Profile."""
    import statistics

    days = request.args.get("dias", 14, type=int)
    now  = datetime.now()
    desde = now - timedelta(days=days)

    lecturas = (GlucoseReading.query
                .filter(GlucoseReading.timestamp >= desde)
                .order_by(GlucoseReading.timestamp).all())

    valores = [r.value_mgdl for r in lecturas]
    n_dias  = len({r.timestamp.date() for r in lecturas})

    # ── Estadísticas resumen ──────────────────────────────────────────────────
    if valores:
        media = round(sum(valores) / len(valores), 1)
        gmi   = round(3.31 + 0.02392 * media, 1)
        std   = round(statistics.stdev(valores), 1) if len(valores) > 1 else 0
        cv    = round(std / media * 100, 1) if media else 0

        n = len(valores)
        tir_muy_bajo = round(len([v for v in valores if v < 54])  / n * 100, 1)
        tir_bajo     = round(len([v for v in valores if 54 <= v < 70])  / n * 100, 1)
        tir_rango    = round(len([v for v in valores if 70 <= v <= 180]) / n * 100, 1)
        tir_alto     = round(len([v for v in valores if 180 < v <= 250]) / n * 100, 1)
        tir_muy_alto = round(len([v for v in valores if v > 250]) / n * 100, 1)
    else:
        media = gmi = std = cv = None
        tir_muy_bajo = tir_bajo = tir_rango = tir_alto = tir_muy_alto = None

    stats_agp = dict(
        media=media, gmi=gmi, std=std, cv=cv, n_lecturas=len(valores),
        n_dias=n_dias,
        tir_muy_bajo=tir_muy_bajo, tir_bajo=tir_bajo, tir_rango=tir_rango,
        tir_alto=tir_alto, tir_muy_alto=tir_muy_alto,
    )

    chart = chart_agp(days=days)
    return render_template("agp.html", chart=chart, stats=stats_agp, dias=days)


@bp.route("/reportes", endpoint="reportes")
def reportes():
    dias = request.args.get("dias", 30, type=int)
    charts = {
        "por_hora":       chart_glucose_by_hour(days=dias),
        "carbs":          chart_glucose_vs_carbs(days=dias),
        "timeline_full":  chart_timeline_eventos(hours=dias * 24),
        "actividad":      chart_activity_glucose_impact(days=dias),
    }
    tabla_filas, tabla_resumen = _tabla_impacto_comidas(dias)
    return render_template("reportes.html", dias=dias, charts=charts,
                           tabla_filas=tabla_filas, tabla_resumen=tabla_resumen)


@bp.route("/api/charts/timeline", endpoint="api_chart_timeline")
def api_chart_timeline():
    horas = request.args.get("horas", 168, type=int)
    return jsonify(chart_glucose_timeline(hours=horas))


@bp.route("/api/charts/tir", endpoint="api_chart_tir")
def api_chart_tir():
    dias = request.args.get("dias", 30, type=int)
    return jsonify(chart_time_in_range(days=dias))


@bp.route("/api/charts/por_hora", endpoint="api_chart_por_hora")
def api_chart_por_hora():
    dias = request.args.get("dias", 30, type=int)
    return jsonify(chart_glucose_by_hour(days=dias))


@bp.route("/api/charts/impacto_comidas", endpoint="api_chart_impacto")
def api_chart_impacto():
    dias = request.args.get("dias", 30, type=int)
    return jsonify(chart_meal_impact(days=dias))


@bp.route("/api/charts/carbs_vs_glucosa", endpoint="api_chart_carbs_glucosa")
def api_chart_carbs_glucosa():
    dias = request.args.get("dias", 90, type=int)
    return jsonify(chart_glucose_vs_carbs(days=dias))


@bp.route("/reporte-semanal", endpoint="reporte_semanal")
def reporte_semanal():
    import statistics as _stats

    dias = request.args.get("dias", 14, type=int)
    desde = datetime.now() - timedelta(days=dias)
    hasta = datetime.now()

    lecturas = GlucoseReading.query.filter(GlucoseReading.timestamp >= desde).order_by(GlucoseReading.timestamp).all()
    valores = [r.value_mgdl for r in lecturas]

    stats = {}
    if valores:
        n = len(valores)
        mean = round(sum(valores) / n, 1)
        sd   = round(_stats.stdev(valores), 1) if n > 1 else 0
        cv   = round(sd / mean * 100, 1) if mean > 0 else 0
        tir   = round(len([v for v in valores if 70 <= v <= 180]) / n * 100, 1)
        tbr   = round(len([v for v in valores if v < 70])  / n * 100, 1)
        tbr54 = round(len([v for v in valores if v < 54])  / n * 100, 1)
        tar   = round(len([v for v in valores if v > 180]) / n * 100, 1)
        tar250= round(len([v for v in valores if v > 250]) / n * 100, 1)
        gmi   = round(3.31 + 0.02392 * mean, 1)   # estimado de HbA1c
        stats = {
            "mean": mean, "sd": sd, "cv": cv,
            "min": int(min(valores)), "max": int(max(valores)),
            "tir": tir, "tbr": tbr, "tbr54": tbr54,
            "tar": tar, "tar250": tar250,
            "gmi": gmi, "n": n,
        }

    # Episodios de hipoglucemia (lecturas individuales < 70)
    hipos = sorted([r for r in lecturas if r.value_mgdl < 70],
                   key=lambda r: r.timestamp, reverse=True)

    # Distribución por franja horaria
    franjas_def = [
        ("Madrugada", "00–06 h", range(0, 6)),
        ("Mañana",    "06–12 h", range(6, 12)),
        ("Tarde",     "12–18 h", range(12, 18)),
        ("Noche",     "18–24 h", range(18, 24)),
    ]
    franjas = []
    for nombre, label, rng in franjas_def:
        vals = [r.value_mgdl for r in lecturas if r.timestamp.hour in rng]
        if vals:
            tir_f = round(len([v for v in vals if 70 <= v <= 180]) / len(vals) * 100)
            franjas.append({
                "nombre": nombre, "label": label,
                "avg": int(sum(vals)/len(vals)),
                "min": int(min(vals)), "max": int(max(vals)),
                "tir": tir_f, "n": len(vals),
            })
        else:
            franjas.append({"nombre": nombre, "label": label, "sin_datos": True})

    # Insulina
    dosis_all = InsulinDose.query.filter(InsulinDose.timestamp >= desde).all()
    bolus_total = round(sum(d.units for d in dosis_all if d.type == "bolus"), 1)
    basal_total = round(sum(d.units for d in dosis_all if d.type == "basal"), 1)

    # Comidas
    comidas_all = Meal.query.filter(Meal.timestamp >= desde).all()
    carbs_total  = sum(c.carbs_g or 0 for c in comidas_all)

    # Actividad
    acts_all = Activity.query.filter(Activity.timestamp >= desde).all()
    min_total = sum(a.duration_min or 0 for a in acts_all)

    # Charts (sólo TIR y circadiano para el reporte)
    charts = {
        "tir":      chart_time_in_range(days=dias),
        "por_hora": chart_glucose_by_hour(days=dias),
        "timeline": chart_glucose_timeline(hours=dias * 24),
    }

    tabla_comidas, resumen_comidas = _tabla_impacto_comidas(dias)
    ctx = dict(
        dias=dias, desde=desde, hasta=hasta,
        stats=stats, hipos=hipos[:15], franjas=franjas,
        bolus_total=bolus_total, basal_total=basal_total,
        bolus_diario=round(bolus_total / dias, 1),
        basal_diario=round(basal_total / dias, 1),
        n_comidas=len(comidas_all),
        carbs_diario=round(carbs_total / dias, 0) if dias else 0,
        n_actividades=len(acts_all),
        min_actividad=min_total,
        charts=charts,
        tabla_comidas=tabla_comidas,
        resumen_comidas=resumen_comidas,
    )
    return render_template("reporte_semanal.html", **ctx)


@bp.route("/reporte-semanal/pdf", endpoint="reporte_semanal_pdf")
def reporte_semanal_pdf():
    import statistics as _stats

    dias = request.args.get("dias", 14, type=int)
    desde = datetime.now() - timedelta(days=dias)
    hasta = datetime.now()

    lecturas = GlucoseReading.query.filter(
        GlucoseReading.timestamp >= desde
    ).order_by(GlucoseReading.timestamp).all()
    valores = [r.value_mgdl for r in lecturas]

    stats = {}
    if valores:
        n = len(valores)
        mean  = round(sum(valores) / n, 1)
        sd    = round(_stats.stdev(valores), 1) if n > 1 else 0
        cv    = round(sd / mean * 100, 1) if mean > 0 else 0
        tir   = round(len([v for v in valores if 70 <= v <= 180]) / n * 100, 1)
        tbr   = round(len([v for v in valores if v < 70])  / n * 100, 1)
        tbr54 = round(len([v for v in valores if v < 54])  / n * 100, 1)
        tar   = round(len([v for v in valores if v > 180]) / n * 100, 1)
        tar250= round(len([v for v in valores if v > 250]) / n * 100, 1)
        gmi   = round(3.31 + 0.02392 * mean, 1)
        stats = {
            "mean": mean, "sd": sd, "cv": cv,
            "min": int(min(valores)), "max": int(max(valores)),
            "tir": tir, "tbr": tbr, "tbr54": tbr54,
            "tar": tar, "tar250": tar250,
            "gmi": gmi, "n": n,
        }

    hipos = sorted([r for r in lecturas if r.value_mgdl < 70],
                   key=lambda r: r.timestamp, reverse=True)

    franjas_def = [
        ("Madrugada", "00–06 h", range(0, 6)),
        ("Mañana",    "06–12 h", range(6, 12)),
        ("Tarde",     "12–18 h", range(12, 18)),
        ("Noche",     "18–24 h", range(18, 24)),
    ]
    franjas = []
    for nombre, label, rng in franjas_def:
        vals = [r.value_mgdl for r in lecturas if r.timestamp.hour in rng]
        if vals:
            tir_f = round(len([v for v in vals if 70 <= v <= 180]) / len(vals) * 100)
            franjas.append({
                "nombre": nombre, "label": label,
                "avg": int(sum(vals)/len(vals)),
                "min": int(min(vals)), "max": int(max(vals)),
                "tir": tir_f, "n": len(vals),
            })
        else:
            franjas.append({"nombre": nombre, "label": label, "sin_datos": True})

    dosis_all   = InsulinDose.query.filter(InsulinDose.timestamp >= desde).all()
    comidas_all = Meal.query.filter(Meal.timestamp >= desde).all()
    acts_all    = Activity.query.filter(Activity.timestamp >= desde).all()
    bolus_total = round(sum(d.units for d in dosis_all if d.type == "bolus"), 1)
    basal_total = round(sum(d.units for d in dosis_all if d.type == "basal"), 1)
    carbs_total = sum(c.carbs_g or 0 for c in comidas_all)
    min_total   = sum(a.duration_min or 0 for a in acts_all)

    tabla_comidas, resumen_comidas = _tabla_impacto_comidas(dias)

    # Gráficas estáticas con matplotlib
    img_tir      = chart_pdf_tir(valores)
    img_circ     = chart_pdf_circadiano(lecturas)
    img_timeline = chart_pdf_timeline(lecturas)

    html = render_template("reporte_pdf.html",
        dias=dias, desde=desde, hasta=hasta,
        stats=stats, hipos=hipos[:20], franjas=franjas,
        bolus_total=bolus_total, basal_total=basal_total,
        bolus_diario=round(bolus_total / dias, 1),
        basal_diario=round(basal_total / dias, 1),
        n_comidas=len(comidas_all),
        carbs_diario=round(carbs_total / dias, 0) if dias else 0,
        n_actividades=len(acts_all),
        min_actividad=min_total,
        tabla_comidas=tabla_comidas,
        resumen_comidas=resumen_comidas,
        img_tir=img_tir,
        img_circ=img_circ,
        img_timeline=img_timeline,
    )

    ctx = dict(
        dias=dias, desde=desde, hasta=hasta,
        stats=stats, hipos=hipos[:20], franjas=franjas,
        bolus_total=bolus_total, basal_total=basal_total,
        bolus_diario=round(bolus_total / dias, 1),
        basal_diario=round(basal_total / dias, 1),
        n_comidas=len(comidas_all),
        carbs_diario=round(carbs_total / dias, 0) if dias else 0,
        n_actividades=len(acts_all),
        min_actividad=min_total,
        tabla_comidas=tabla_comidas,
        resumen_comidas=resumen_comidas,
        img_tir=img_tir,
        img_circ=img_circ,
        img_timeline=img_timeline,
    )

    pdf_bytes = generar_pdf(ctx)
    nombre_archivo = f"reporte_diabetes_{hasta.strftime('%Y%m%d')}.pdf"
    response = make_response(pdf_bytes)
    response.headers["Content-Type"] = "application/pdf"
    response.headers["Content-Disposition"] = f'attachment; filename="{nombre_archivo}"'
    return response


@bp.route("/api/settings/save", methods=["POST"], endpoint="api_settings_save")
def api_settings_save():
    """Guarda configuración personal (ICR, ISF manual, objetivo)."""
    data = request.get_json() or {}
    guardados = []
    for key in ("icr", "isf_manual", "objetivo"):
        val = data.get(key)
        if val is not None and val != "":
            try:
                fval = float(val)
                if fval > 0:
                    _set_setting(key, fval)
                    guardados.append(key)
            except (ValueError, TypeError):
                pass
    return jsonify({"ok": True, "guardados": guardados})
