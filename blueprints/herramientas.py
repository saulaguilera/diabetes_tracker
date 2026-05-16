from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from datetime import datetime, timedelta
from models import db, GlucoseReading, Meal, InsulinDose, MealComponent
from helpers import (
    parse_datetime, _auto_categorizar, _save_meal_components,
    _get_setting, _set_setting,
    _calcular_isf_personal, _calcular_icr_personal,
    _calcular_isf_circadiano, _isf_para_hora,
)
from utils.recommendations import generate_recommendations

bp = Blueprint("herramientas", __name__)


@bp.route("/calculadora", endpoint="calculadora")
def calculadora():
    isf_personal, n_isf   = _calcular_isf_personal()
    icr_personal, n_icr   = _calcular_icr_personal()
    ultima = GlucoseReading.query.order_by(GlucoseReading.timestamp.desc()).first()

    # Configuración guardada por el usuario
    icr_guardado       = _get_setting("icr")
    isf_manual_guard   = _get_setting("isf_manual")
    objetivo_guardado  = _get_setting("objetivo", "100")
    basal_dose_guard   = _get_setting("basal_dose_u")
    basal_hora_guard   = _get_setting("basal_hora") or "22"
    basal_tipo_guard   = _get_setting("basal_tipo") or "glargina"

    # ISF circadiano
    isf_circ = _calcular_isf_circadiano(days=90)

    # ISF recomendado para la hora actual
    hora_actual = datetime.now().hour
    isf_ahora, bloque_label, fuente_isf_ahora = _isf_para_hora(
        hora_actual, isf_circ, isf_personal
    )

    # IOB actual para descontar de la dosis sugerida
    kinetics = {}
    try:
        from utils.kinetics import get_kinetics_snapshot
        kinetics = get_kinetics_snapshot(hours_lookback=6)
    except Exception:
        pass

    return render_template("calculadora.html",
        isf_personal=isf_personal,
        n_isf=n_isf,
        icr_personal=icr_personal,
        n_icr=n_icr,
        icr_guardado=icr_guardado,
        isf_manual_guardado=isf_manual_guard,
        objetivo_guardado=objetivo_guardado,
        basal_dose_guard=basal_dose_guard,
        basal_hora_guard=basal_hora_guard,
        basal_tipo_guard=basal_tipo_guard,
        ultima=ultima,
        kinetics=kinetics,
        isf_circ=isf_circ,
        isf_ahora=isf_ahora,
        bloque_label=bloque_label,
        fuente_isf_ahora=fuente_isf_ahora,
        hora_actual=hora_actual,
    )


@bp.route("/api/calculadora/correccion", endpoint="api_calculadora_correccion")
def api_calculadora_correccion():
    glucemia   = request.args.get("glucemia",  type=float)
    objetivo   = request.args.get("objetivo",  type=float)
    isf_manual = request.args.get("isf",       type=float)
    carbs      = request.args.get("carbs",     type=float)
    fat        = request.args.get("fat",       type=float, default=0)
    protein    = request.args.get("protein",   type=float, default=0)
    icr_manual = request.args.get("icr",       type=float)
    hora       = request.args.get("hora",      type=int)   # 0-23, default = ahora

    # Usar configuración guardada como fallback
    if objetivo is None:
        objetivo = float(_get_setting("objetivo", 100))
    if hora is None:
        hora = datetime.now().hour

    isf_personal, n_isf = _calcular_isf_personal()
    icr_personal, n_icr = _calcular_icr_personal()

    # ISF: manual > guardado > circadiano para la hora actual > global calculado
    isf_guardado  = float(_get_setting("isf_manual")) if _get_setting("isf_manual") else None
    isf_circ      = _calcular_isf_circadiano(days=90)
    isf_bloque, bloque_label, fuente_circ = _isf_para_hora(hora, isf_circ, isf_personal)

    isf_base = isf_manual or isf_guardado or isf_bloque or isf_personal
    isf_fuente = (
        "manual"      if isf_manual  else
        "guardado"    if isf_guardado else
        "circadiano"  if (isf_bloque and fuente_circ == "circadiano") else
        "calculado"
    )

    icr = icr_manual or (float(_get_setting("icr")) if _get_setting("icr") else None) or icr_personal

    if not glucemia or glucemia <= 0:
        return jsonify({"error": "Glucemia inválida"})
    if not isf_base:
        return jsonify({"error": "Sin ISF — ingresalo manualmente en Configuración"})

    # ── Exercise sensitivity factor ───────────────────────────────────────────
    exercise_factor = 1.0
    exercise_label  = None
    try:
        from models import Activity
        from utils.kinetics import exercise_sensitivity_factor
        act_cutoff  = datetime.now() - timedelta(hours=24)
        activities  = Activity.query.filter(Activity.timestamp >= act_cutoff).all()
        exercise_factor = exercise_sensitivity_factor(activities)
        if exercise_factor >= 1.10:
            exercise_label = f"+{round((exercise_factor - 1) * 100):.0f}% sensibilidad (ejercicio)"
        elif exercise_factor <= 0.92:
            exercise_label = f"−{round((1 - exercise_factor) * 100):.0f}% sensibilidad (ejercicio agudo)"
    except Exception:
        pass

    # Apply exercise factor to ISF (more sensitive → higher effective ISF → smaller dose)
    isf = round((isf_base or 0) * exercise_factor, 1)

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

    # ── Fat + protein split-bolus recommendation ─────────────────────────────
    split_rec = None
    if (fat or 0) > 0 or (protein or 0) > 0:
        try:
            from utils.kinetics import extended_bolus_recommendation
            split_rec = extended_bolus_recommendation(fat or 0, protein or 0, icr)
        except Exception:
            pass

    # ── IOB deduction ────────────────────────────────────────────────────────
    iob_actual = 0.0
    try:
        from utils.kinetics import get_kinetics_snapshot
        snap = get_kinetics_snapshot(hours_lookback=6)
        iob_actual = snap.get("iob", 0.0)
    except Exception:
        pass
    iob_deduccion = min(iob_actual, correccion_exacta + bolo_comida_exacto)  # no restar más de lo que se daría

    # ── Total ─────────────────────────────────────────────────────────────────
    total_exacto      = correccion_exacta + bolo_comida_exacto
    total_neto_exacto = max(0.0, total_exacto - iob_deduccion)
    total_redondeado  = round(total_neto_exacto * 2) / 2  # redondear a 0.5U

    resultado_esperado = round(glucemia - correccion_exacta * isf, 0)

    return jsonify({
        # Totales
        "total_exacto":     round(total_exacto, 2),
        "total_sugerido":   total_redondeado,
        # IOB
        "iob_actual":       round(iob_actual, 2),
        "iob_deduccion":    round(iob_deduccion, 2),
        "total_neto_exacto": round(total_neto_exacto, 2),
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
        "n_isf":        n_isf,
        "n_icr":        n_icr,
        "fuente_isf":   isf_fuente,
        "fuente_icr":   "manual" if icr_manual else ("guardado" if _get_setting("icr") else "calculado"),
        "isf_bloque":   isf_bloque,
        "isf_global":   isf_personal,
        "bloque_label": bloque_label,
        "hora":         hora,
        "resultado_esperado": resultado_esperado,
        # Fat+protein split bolus
        "fat":              fat or 0,
        "protein":          protein or 0,
        "fp_glucose_equiv": split_rec["fp_glucose_equiv"] if split_rec else 0,
        "deferred_units":   split_rec["deferred_units"]   if split_rec else 0,
        "deferred_at":      split_rec["deferred_at"]      if split_rec else "",
        "fp_trigger":       split_rec["trigger"]          if split_rec else "",
        # Exercise sensitivity
        "exercise_factor":  round(exercise_factor, 3),
        "exercise_label":   exercise_label or "",
        "isf_base":         isf_base,
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
            # Auto-generar nombre desde ingredientes si no se proporcionó
            if not nombre:
                comp_names = [n.strip() for n in request.form.getlist("comp_name[]") if n.strip()]
                nombre = ", ".join(comp_names[:3]) or "Comida"
                if len(comp_names) > 3:
                    nombre += f" y {len(comp_names)-3} más"
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
                purpose   = request.form.get("purpose", "comida")
                pre_meal  = request.form.get("pre_meal_min", type=int) if purpose in ("comida", "mixto") else None
                db.session.add(InsulinDose(
                    timestamp=ts,
                    type="bolus",
                    units=units,
                    brand=request.form.get("insulina_brand", "").strip(),
                    notes="",
                    purpose=purpose,
                    pre_meal_min=pre_meal,
                ))
                label = {"comida": "comida", "correccion": "corrección", "mixto": "mixto"}.get(purpose, "")
                guardados.append(f"Insulina {units}U {label}")

        if guardados:
            db.session.commit()
            flash("✓ Registrado: " + " · ".join(guardados), "success")
        else:
            flash("No se seleccionó ningún dato para registrar.", "warning")

        return redirect(url_for("dashboard"))

    ahora = datetime.now()
    ultima_glucemia = GlucoseReading.query.order_by(GlucoseReading.timestamp.desc()).first()

    # IOB / COB snapshot para contexto al registrar
    kinetics = {}
    try:
        from utils.kinetics import get_kinetics_snapshot
        kinetics = get_kinetics_snapshot(hours_lookback=6)
    except Exception:
        pass

    return render_template(
        "quicklog.html",
        fecha=ahora.strftime("%Y-%m-%d"),
        hora=ahora.strftime("%H:%M"),
        ultima_glucemia=ultima_glucemia,
        kinetics=kinetics,
    )


@bp.route("/recomendaciones", endpoint="recomendaciones")
def recomendaciones():
    dias = request.args.get("dias", 30, type=int)
    recs = generate_recommendations(days=dias)
    return render_template("recomendaciones.html", recs=recs, dias=dias)
