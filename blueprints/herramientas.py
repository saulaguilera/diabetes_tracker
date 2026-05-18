from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from datetime import datetime, timedelta
from models import db, GlucoseReading, Meal, InsulinDose, MealComponent, MealPreset
from helpers import (
    parse_datetime, _auto_categorizar, _save_meal_components,
    _get_setting, _set_setting,
    _calcular_isf_personal, _calcular_icr_personal,
    _calcular_isf_circadiano, _isf_para_hora,
    _calcular_icr_circadiano, _icr_para_hora,
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

    # ICR circadiano
    icr_circ = _calcular_icr_circadiano(days=90)

    # ISF e ICR recomendados para la hora actual
    hora_actual = datetime.now().hour
    isf_ahora, bloque_label, fuente_isf_ahora = _isf_para_hora(
        hora_actual, isf_circ, isf_personal
    )
    icr_ahora, icr_bloque_label, fuente_icr_ahora = _icr_para_hora(
        hora_actual, icr_circ, icr_personal
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
        icr_circ=icr_circ,
        icr_ahora=icr_ahora,
        icr_bloque_label=icr_bloque_label,
        fuente_icr_ahora=fuente_icr_ahora,
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

    # ICR: manual > guardado > circadiano para la hora actual > global calculado
    icr_guardado_raw = _get_setting("icr")
    icr_guardado_val = float(icr_guardado_raw) if icr_guardado_raw else None
    icr_circ         = _calcular_icr_circadiano(days=90)
    icr_bloque, icr_bloque_label, fuente_icr_circ = _icr_para_hora(hora, icr_circ, icr_personal)

    icr = icr_manual or icr_guardado_val or icr_bloque or icr_personal
    icr_fuente = (
        "manual"      if icr_manual        else
        "guardado"    if icr_guardado_val  else
        "circadiano"  if (icr_bloque and fuente_icr_circ == "circadiano") else
        "calculado"
    )

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

    # ── IOB + ROC (ambos del mismo snapshot) ─────────────────────────────────
    # Solo se deduce IOB de bolus (insulina rápida activa).
    # El IOB basal (Toujeo, Lantus, etc.) NO se resta porque cubre la producción
    # hepática basal, no el exceso de glucosa que estamos corrigiendo.
    iob_actual = 0.0
    roc_mgdl_min = None   # mg/dL/min — None si no hay lecturas CGM recientes
    try:
        from utils.kinetics import get_kinetics_snapshot
        snap       = get_kinetics_snapshot(hours_lookback=6)
        iob_actual = snap.get("iob_bolus", 0.0)  # solo bolus, nunca basal
        roc_mgdl_min = snap.get("roc")            # puede ser None
    except Exception:
        pass

    # ── Ajuste por tendencia (ROC) ────────────────────────────────────────────
    # La corrección se calcula sobre la glucemia PROYECTADA a +30 min,
    # no sobre la glucemia actual. Si está bajando a 2 mg/dL/min, en 30 min
    # estará 60 mg/dL más baja — dar una corrección completa causaría hipo.
    #
    # Horizonte conservador: 30 min (tiempo mínimo de inicio de acción del bolo).
    # Clip: ±60 mg/dL máximo de ajuste (evita proyecciones absurdas con lecturas ruidosas).
    ROC_HORIZON_MIN = 30
    ROC_MAX_ADJ     = 60   # mg/dL

    roc_adj      = 0.0     # ajuste aplicado en mg/dL
    roc_arrow    = "→"
    roc_label    = None
    roc_alerta   = None    # aviso visible al usuario

    if roc_mgdl_min is not None:
        roc_adj_raw = roc_mgdl_min * ROC_HORIZON_MIN
        roc_adj     = max(-ROC_MAX_ADJ, min(ROC_MAX_ADJ, roc_adj_raw))

        # Flecha de tendencia (mismos umbrales que el dashboard)
        if   roc_mgdl_min >  2.0: roc_arrow = "↑↑"
        elif roc_mgdl_min >  1.0: roc_arrow = "↑"
        elif roc_mgdl_min >  0.5: roc_arrow = "↗"
        elif roc_mgdl_min < -2.0: roc_arrow = "↓↓"
        elif roc_mgdl_min < -1.0: roc_arrow = "↓"
        elif roc_mgdl_min < -0.5: roc_arrow = "↘"
        else:                     roc_arrow = "→"

        roc_label = f"{roc_arrow} {roc_mgdl_min:+.1f} mg/dL/min"

        # Alertas por tendencia pronunciada
        if roc_mgdl_min < -2.0:
            roc_alerta = "caida_rapida"   # bajando >2 mg/dL/min — considerar omitir corrección
        elif roc_mgdl_min < -1.0:
            roc_alerta = "bajando"        # bajando — corrección reducida automáticamente
        elif roc_mgdl_min > 2.0:
            roc_alerta = "subida_rapida"  # subiendo >2 mg/dL/min

    # Glucemia proyectada a 30 min (para corrección)
    glucemia_proyectada = max(40.0, min(400.0, glucemia + roc_adj))

    # Recalcular corrección sobre la glucemia proyectada
    if glucemia_proyectada > objetivo:
        correccion_exacta = (glucemia_proyectada - objetivo) / isf
    else:
        correccion_exacta = 0.0

    iob_deduccion = min(iob_actual, correccion_exacta + bolo_comida_exacto)

    # ── Total ─────────────────────────────────────────────────────────────────
    total_exacto      = correccion_exacta + bolo_comida_exacto
    total_neto_exacto = max(0.0, total_exacto - iob_deduccion)
    total_redondeado  = round(total_neto_exacto * 2) / 2  # redondear a 0.5U

    resultado_esperado = round(glucemia_proyectada - correccion_exacta * isf, 0)

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
        "fuente_icr":      icr_fuente,
        "icr_bloque":      icr_bloque,
        "icr_global":      icr_personal,
        "icr_bloque_label": icr_bloque_label,
        "isf_bloque":      isf_bloque,
        "isf_global":      isf_personal,
        "bloque_label":    bloque_label,
        "hora":            hora,
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
        # ROC — tendencia de glucemia
        "roc_mgdl_min":        round(roc_mgdl_min, 2) if roc_mgdl_min is not None else None,
        "roc_adj_mgdl":        round(roc_adj, 1),
        "roc_arrow":           roc_arrow,
        "roc_label":           roc_label or "",
        "roc_alerta":          roc_alerta or "",
        "glucemia_proyectada": glucemia_proyectada,
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

        # Actividad física
        if request.form.get("reg_actividad"):
            tipo      = request.form.get("actividad_tipo", "").strip()
            duracion  = request.form.get("actividad_duracion", type=int)
            intensidad = request.form.get("actividad_intensidad", "media")
            if tipo and duracion and duracion > 0:
                from models import Activity
                db.session.add(Activity(
                    timestamp=ts,
                    activity_type=tipo,
                    duration_min=duracion,
                    intensity=intensidad,
                    exercise_type=None,
                    notes="",
                ))
                guardados.append(f"Actividad {tipo} {duracion}min ({intensidad})")

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


@bp.route("/configuracion", endpoint="configuracion")
def configuracion():
    """Página central de configuración del modelo y parámetros personales."""
    from utils.kinetics import _DEFAULT_DIA_MIN, _DEFAULT_PEAK_MIN
    from utils.kinetics import estimate_dia_from_data

    isf_personal, n_isf = _calcular_isf_personal()
    icr_personal, n_icr = _calcular_icr_personal()
    isf_circ            = _calcular_isf_circadiano(days=90)

    # Valores guardados manualmente
    isf_manual   = _get_setting("isf_manual")
    icr_manual   = _get_setting("icr")
    objetivo     = _get_setting("objetivo") or "100"
    dia_min_raw  = _get_setting("dia_min")
    basal_tipo   = _get_setting("basal_tipo") or "glargina"

    # Última inyección basal registrada (para mostrar en la card)
    from models import InsulinDose
    from datetime import timedelta
    ultima_basal = (
        InsulinDose.query
        .filter(InsulinDose.type == "basal")
        .order_by(InsulinDose.timestamp.desc())
        .first()
    )

    dia_min_guardado = int(float(dia_min_raw)) if dia_min_raw else None
    dia_min_default  = _DEFAULT_DIA_MIN

    # Estimación DIA desde datos reales
    try:
        dia_estimado = estimate_dia_from_data(days=90)
    except Exception:
        dia_estimado = None

    # Bloques circadianos de ISF (para tabla informativa)
    circ_bloques = []
    for blk, data in sorted(isf_circ.items()):
        circ_bloques.append({
            "label": data["label"],
            "isf":   data["isf"],
            "n":     data["n"],
        })

    return render_template(
        "configuracion.html",
        # ISF
        isf_personal=isf_personal,
        n_isf=n_isf,
        isf_manual=isf_manual,
        circ_bloques=circ_bloques,
        # ICR
        icr_personal=icr_personal,
        n_icr=n_icr,
        icr_manual=icr_manual,
        # Objetivo
        objetivo=objetivo,
        # DIA
        dia_min_guardado=dia_min_guardado,
        dia_min_default=dia_min_default,
        dia_estimado=dia_estimado,
        # Basal
        basal_tipo=basal_tipo,
        ultima_basal=ultima_basal,
    )


@bp.route("/recomendaciones", endpoint="recomendaciones")
def recomendaciones():
    dias = request.args.get("dias", 30, type=int)
    recs = generate_recommendations(days=dias)
    return render_template("recomendaciones.html", recs=recs, dias=dias)


# ── Meal Presets (Comidas favoritas) ────────────────────────────────────────

@bp.route("/api/meal-presets", endpoint="api_meal_presets_list")
def api_meal_presets_list():
    """Devuelve todos los presets ordenados por frecuencia de uso."""
    presets = MealPreset.query.order_by(
        MealPreset.use_count.desc(), MealPreset.created_at.desc()
    ).all()
    return jsonify({
        "ok": True,
        "presets": [
            {
                "id":        p.id,
                "name":      p.name,
                "carbs_g":   round(p.carbs_g or 0, 1),
                "fat_g":     round(p.fat_g or 0, 1),
                "protein_g": round(p.protein_g or 0, 1),
                "calories":  round(p.calories or 0, 1),
                "use_count": p.use_count or 0,
                "components": p.components_list(),
            }
            for p in presets
        ],
    })


@bp.route("/api/meal-presets", methods=["POST"], endpoint="api_meal_presets_create")
def api_meal_presets_create():
    """Guarda un nuevo preset a partir de la lista de ingredientes del QuickLog."""
    data = request.get_json(silent=True) or {}
    name       = (data.get("name") or "").strip()
    components = data.get("components") or []

    if not name:
        return jsonify({"ok": False, "error": "Nombre requerido"}), 400
    if not components:
        return jsonify({"ok": False, "error": "Al menos un ingrediente requerido"}), 400

    import json

    # Totals
    carbs_g    = sum(float(c.get("carbs_g") or 0)    for c in components)
    fat_g      = sum(float(c.get("fat_g") or 0)      for c in components)
    protein_g  = sum(float(c.get("protein_g") or 0)  for c in components)
    calories   = sum(float(c.get("calories") or 0)   for c in components)

    preset = MealPreset(
        name       = name,
        components = json.dumps(components, ensure_ascii=False),
        carbs_g    = round(carbs_g, 2),
        fat_g      = round(fat_g, 2),
        protein_g  = round(protein_g, 2),
        calories   = round(calories, 2),
    )
    db.session.add(preset)
    db.session.commit()

    return jsonify({"ok": True, "id": preset.id, "name": preset.name})


@bp.route("/api/meal-presets/<int:preset_id>", methods=["DELETE"],
          endpoint="api_meal_presets_delete")
def api_meal_presets_delete(preset_id):
    """Elimina un preset."""
    preset = MealPreset.query.get_or_404(preset_id)
    db.session.delete(preset)
    db.session.commit()
    return jsonify({"ok": True})


@bp.route("/api/meal-presets/<int:preset_id>/use", methods=["POST"],
          endpoint="api_meal_presets_use")
def api_meal_presets_use(preset_id):
    """Registra un uso del preset (incrementa contador y actualiza timestamp)."""
    preset = MealPreset.query.get_or_404(preset_id)
    preset.use_count    = (preset.use_count or 0) + 1
    preset.last_used_at = datetime.utcnow()
    db.session.commit()
    return jsonify({"ok": True, "use_count": preset.use_count})
