from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from datetime import datetime, timedelta
from models import db, GlucoseReading, Meal, InsulinDose, MealComponent
from helpers import (
    parse_datetime, _auto_categorizar, _save_meal_components,
    _get_setting, _set_setting,
)
from utils.recommendations import generate_recommendations

bp = Blueprint("herramientas", __name__)


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
        # Ignorar si hay comida cercana (es bolus de comida, no corrección)
        comida = Meal.query.filter(
            Meal.timestamp >= d.timestamp - timedelta(minutes=30),
            Meal.timestamp <= d.timestamp + timedelta(minutes=30),
        ).first()
        if comida:
            continue

        # Glucosa justo antes de la dosis
        pre = (
            GlucoseReading.query
            .filter(
                GlucoseReading.timestamp >= d.timestamp - timedelta(minutes=30),
                GlucoseReading.timestamp <= d.timestamp + timedelta(minutes=15),
            )
            .order_by(GlucoseReading.timestamp.desc())
            .first()
        )

        # Nadir en las 3h siguientes
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
            continue   # la glucosa no bajó — no sirve como muestra

        isf = (pre.value_mgdl - nadir) / d.units
        if 10 <= isf <= 200:   # rango fisiológico razonable
            muestras.append(round(isf, 1))

    if len(muestras) >= 2:
        return round(sum(muestras) / len(muestras), 1), len(muestras)
    return None, len(muestras)


def _calcular_icr_personal(days=90):
    """
    Estima el ratio Insulina:Carbohidratos (ICR) personal.
    Busca pares comida+bolus donde la glucemia pre-comida estaba en rango
    para aislar el componente de cobertura de carbohidratos.
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

        # Glucemia justo antes de la comida (ventana -30min a +5min)
        pre = (GlucoseReading.query
               .filter(
                   GlucoseReading.timestamp >= c.timestamp - timedelta(minutes=30),
                   GlucoseReading.timestamp <= c.timestamp + timedelta(minutes=5),
               )
               .order_by(GlucoseReading.timestamp.desc()).first())
        if not pre:
            continue

        # Bolus en ventana -15min a +30min de la comida
        bolus = (InsulinDose.query
                 .filter(
                     InsulinDose.type == "bolus",
                     InsulinDose.timestamp >= c.timestamp - timedelta(minutes=15),
                     InsulinDose.timestamp <= c.timestamp + timedelta(minutes=30),
                 )
                 .order_by(InsulinDose.timestamp).first())
        if not bolus or bolus.units <= 0:
            continue

        # Descontar el componente de corrección del bolus total
        objetivo = float(_get_setting("objetivo", 100))
        isf = isf_personal or 40  # fallback razonable
        correccion = max(0, (pre.value_mgdl - objetivo) / isf)
        bolo_comida = bolus.units - correccion

        if bolo_comida <= 0.2:
            continue

        icr = c.carbs_g / bolo_comida
        if 3 <= icr <= 30:   # rango fisiológico razonable (3–30g CH/U)
            muestras.append(round(icr, 1))

    if len(muestras) >= 3:
        return round(sum(muestras) / len(muestras), 1), len(muestras)
    return None, len(muestras)


@bp.route("/calculadora", endpoint="calculadora")
def calculadora():
    isf_personal, n_isf   = _calcular_isf_personal()
    icr_personal, n_icr   = _calcular_icr_personal()
    ultima = GlucoseReading.query.order_by(GlucoseReading.timestamp.desc()).first()

    # Configuración guardada por el usuario
    icr_guardado      = _get_setting("icr")
    isf_manual_guard  = _get_setting("isf_manual")
    objetivo_guardado = _get_setting("objetivo", "100")

    return render_template("calculadora.html",
        isf_personal=isf_personal,
        n_isf=n_isf,
        icr_personal=icr_personal,
        n_icr=n_icr,
        icr_guardado=icr_guardado,
        isf_manual_guardado=isf_manual_guard,
        objetivo_guardado=objetivo_guardado,
        ultima=ultima,
    )


@bp.route("/api/calculadora/correccion", endpoint="api_calculadora_correccion")
def api_calculadora_correccion():
    glucemia   = request.args.get("glucemia",  type=float)
    objetivo   = request.args.get("objetivo",  type=float)
    isf_manual = request.args.get("isf",       type=float)
    carbs      = request.args.get("carbs",     type=float)
    icr_manual = request.args.get("icr",       type=float)

    # Usar configuración guardada como fallback
    if objetivo is None:
        objetivo = float(_get_setting("objetivo", 100))

    isf_personal, n_isf = _calcular_isf_personal()
    icr_personal, n_icr = _calcular_icr_personal()

    isf = isf_manual or (float(_get_setting("isf_manual")) if _get_setting("isf_manual") else None) or isf_personal
    icr = icr_manual or (float(_get_setting("icr")) if _get_setting("icr") else None) or icr_personal

    if not glucemia or glucemia <= 0:
        return jsonify({"error": "Glucemia inválida"})
    if not isf:
        return jsonify({"error": "Sin ISF — ingresalo manualmente en Configuración"})

    # ── Componente de corrección ──────────────────────────────────────────────
    if glucemia > objetivo:
        correccion_exacta = (glucemia - objetivo) / isf
    else:
        correccion_exacta = 0.0   # glucemia baja: no corregir

    # ── Componente de comida ──────────────────────────────────────────────────
    bolo_comida_exacto = 0.0
    if carbs and carbs > 0:
        if not icr:
            return jsonify({"error": "Sin I:CH — ingresalo en Configuración"})
        bolo_comida_exacto = carbs / icr

    # ── Total ─────────────────────────────────────────────────────────────────
    total_exacto    = correccion_exacta + bolo_comida_exacto
    total_redondeado = round(total_exacto * 2) / 2  # redondear a 0.5U

    resultado_esperado = round(glucemia - correccion_exacta * isf, 0)

    return jsonify({
        # Totales
        "total_exacto":     round(total_exacto, 2),
        "total_sugerido":   total_redondeado,
        # Componentes
        "correccion_exacta":     round(correccion_exacta, 2),
        "correccion_redondeada": round(round(correccion_exacta * 2) / 2, 1),
        "bolo_comida_exacto":    round(bolo_comida_exacto, 2),
        # Parámetros usados
        "glucemia":    glucemia,
        "objetivo":    objetivo,
        "isf":         isf,
        "icr":         icr,
        "carbs":       carbs or 0,
        "n_isf":       n_isf,
        "n_icr":       n_icr,
        "fuente_isf":  "manual" if isf_manual else ("guardado" if _get_setting("isf_manual") else "calculado"),
        "fuente_icr":  "manual" if icr_manual else ("guardado" if _get_setting("icr") else "calculado"),
        "resultado_esperado": resultado_esperado,
    })


@bp.route("/quicklog", methods=["GET", "POST"], endpoint="quicklog")
def quicklog():
    if request.method == "POST":
        fecha = request.form.get("fecha")
        hora  = request.form.get("hora")
        ts    = parse_datetime(fecha, hora)
        guardados = []

        # Glucemia
        if request.form.get("reg_glucemia"):
            valor = request.form.get("glucemia_valor", type=float)
            if valor and valor > 0:
                db.session.add(GlucoseReading(
                    timestamp=ts,
                    value_mgdl=valor,
                    source="manual",
                    notes=request.form.get("glucemia_notas", ""),
                ))
                guardados.append(f"Glucemia {int(valor)} mg/dL")

        # Comida
        if request.form.get("reg_comida"):
            nombre = request.form.get("comida_nombre", "").strip()
            if nombre:
                comida = Meal(
                    timestamp=ts,
                    name=nombre,
                    carbs_g=0, fat_g=0, protein_g=0, calories=0,
                    notes="",
                    categoria=_auto_categorizar(nombre),
                )
                db.session.add(comida)
                db.session.flush()

                componentes = _save_meal_components(comida, request.form)
                if componentes:
                    for c in componentes:
                        c.meal_id = comida.id
                        db.session.add(c)
                    total_ch = int(comida.carbs_g)
                    detalle  = f"{len(componentes)} ingredientes, {total_ch}g CH"
                else:
                    # fallback sin componentes
                    comida.carbs_g   = request.form.get("comida_carbs",   0, type=float)
                    comida.fat_g     = request.form.get("comida_fat",     0, type=float)
                    comida.protein_g = request.form.get("comida_protein", 0, type=float)
                    comida.calories  = request.form.get("comida_cal",     0, type=float)
                    detalle = f"{int(comida.carbs_g)}g CH"

                guardados.append(f"Comida {nombre} ({detalle})")

        # Insulina bolus
        if request.form.get("reg_insulina"):
            units = request.form.get("insulina_units", type=float)
            if units and units > 0:
                db.session.add(InsulinDose(
                    timestamp=ts,
                    type="bolus",
                    units=units,
                    brand=request.form.get("insulina_brand", "").strip(),
                    notes="",
                ))
                guardados.append(f"Insulina {units}U bolus")

        if guardados:
            db.session.commit()
            flash("✓ Registrado: " + " · ".join(guardados), "success")
        else:
            flash("No se seleccionó ningún dato para registrar.", "warning")

        return redirect(url_for("dashboard"))

    ahora = datetime.now()
    ultima_glucemia = GlucoseReading.query.order_by(GlucoseReading.timestamp.desc()).first()
    return render_template(
        "quicklog.html",
        fecha=ahora.strftime("%Y-%m-%d"),
        hora=ahora.strftime("%H:%M"),
        ultima_glucemia=ultima_glucemia,
    )


@bp.route("/recomendaciones", endpoint="recomendaciones")
def recomendaciones():
    dias = request.args.get("dias", 30, type=int)
    recs = generate_recommendations(days=dias)
    return render_template("recomendaciones.html", recs=recs, dias=dias)
