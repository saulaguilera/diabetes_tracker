from flask import Blueprint, render_template, request
from datetime import datetime, timedelta
from models import db, GlucoseReading, Meal, InsulinDose
from helpers import _precargar_glucosa, _precargar_bolus, _glucosa_impacto, _calcular_isf_personal

bp = Blueprint("patrones", __name__)


def _analisis_ratio_insulina(days=30):
    """
    Para cada comida con bolus cercano y glucosa pre/post:
    calcula ratio CH:Insulina, delta glucémico, IOB al momento y outcome.

    Incluye delta_neto = ΔGlucosa_real + (IOB_al_comer × ISF_personal)
    que aísla el efecto real de la comida de la insulina ya activa.
    """
    desde = datetime.now() - timedelta(days=days)
    comidas = Meal.query.filter(
        Meal.timestamp >= desde,
        Meal.carbs_g > 5,
    ).all()

    # Precarga batch ─────────────────────────────────────────────────────────
    all_readings = _precargar_glucosa(comidas, horas_post=3)
    all_bolus    = _precargar_bolus(comidas)

    # ISF personal para calcular delta neto
    isf_personal, _ = _calcular_isf_personal(days=90)

    # Todos los bolus en el período para calcular IOB al momento de la comida
    bolus_todos = InsulinDose.query.filter(
        InsulinDose.type == "bolus",
        InsulinDose.timestamp >= desde - timedelta(hours=6),
    ).all()

    # Kinetics helpers (importación diferida)
    try:
        from utils.kinetics import current_iob as _calc_iob
        _kinetics_ok = True
    except ImportError:
        _kinetics_ok = False

    datos = []
    for c in comidas:
        t0 = c.timestamp
        pre_list  = [r for r in all_readings if t0 - timedelta(minutes=30) <= r.timestamp <= t0]
        post_list = [r for r in all_readings
                     if t0 + timedelta(minutes=60) < r.timestamp <= t0 + timedelta(hours=3)]
        bolus     = [d for d in all_bolus
                     if t0 - timedelta(hours=1) <= d.timestamp <= t0 + timedelta(minutes=30)]
        pre = pre_list[-1] if pre_list else None

        if not pre or not post_list or not bolus:
            continue

        total_bolus = sum(d.units for d in bolus)
        if total_bolus == 0:
            continue

        # Glucosa más cercana a las 2h post-comida
        post = min(post_list, key=lambda r: abs((r.timestamp - c.timestamp).total_seconds() - 7200))
        delta   = round(post.value_mgdl - pre.value_mgdl, 0)
        ratio   = round(c.carbs_g / total_bolus, 1)
        hora    = c.timestamp.hour

        if post.value_mgdl < 70:    outcome = "hipo"
        elif post.value_mgdl > 180: outcome = "hiper"
        else:                        outcome = "rango"

        # IOB al momento de la comida (de bolus ANTERIORES al meal, no incluidos en la comida)
        bolus_previos = [b for b in bolus_todos
                         if b.timestamp < t0 - timedelta(minutes=30)]
        iob_al_comer = 0.0
        if _kinetics_ok and bolus_previos:
            iob_al_comer = _calc_iob(bolus_previos, at_time=t0)

        # Delta neto: aísla el efecto de la comida de la insulina ya activa
        # ΔGlucosa_neto = ΔGlucosa_real + (IOB_al_comer × ISF)
        # Un IOB alto "presiona" la glucosa a la baja; el delta neto muestra
        # cuánto subió la glucosa si no hubiera habido insulina residual.
        delta_neto = None
        if isf_personal and iob_al_comer > 0.1:
            delta_neto = round(delta + iob_al_comer * isf_personal, 0)

        datos.append({
            "name":        c.name,
            "carbs":       round(c.carbs_g, 1),
            "bolus":       round(total_bolus, 1),
            "ratio":       ratio,
            "pre":         int(pre.value_mgdl),
            "post":        int(post.value_mgdl),
            "delta":       delta,
            "delta_neto":  delta_neto,
            "iob_al_comer": round(iob_al_comer, 2),
            "outcome":     outcome,
            "hora":        hora,
        })
    return datos


def _clasificar_comida(ch, prot, grasa):
    """
    Clasifica una comida según su perfil de macronutrientes.

    Umbrales basados en evidencia clínica T1D:
    - Smart et al. (2013) Diabetes Care: la proteína >40g Y grasa >35g elevan
      significativamente la glucosa de forma independiente y aditiva.
    - Wolpert et al. (2013) Diabetes Care: 50g de grasa causan hiperglucemia
      sostenida 3-5h post-comida ("efecto pizza").

    Simple          → <20g grasa Y <30g proteína (efecto glucémico predecible, solo CH)
    Mixta           → 20-35g grasa O 30-50g proteína (efecto moderado grasa/proteína)
    Alta grasa/prot → >35g grasa O >50g proteína (pico retardado, bolus extendido recomendado)
    """
    if grasa > 35 or prot > 50:
        return "Alta grasa/proteína"
    elif grasa >= 20 or prot >= 30:
        return "Mixta"
    else:
        return "Simple"


def _iauc_trapezoidal(baseline, readings_timed):
    """
    Área incremental bajo la curva (iAUC) por el método trapezoidal.

    readings_timed: lista de (minutos_desde_comida, value_mgdl) ordenada por tiempo.
    baseline: glucosa pre-comida (mg/dL).

    Retorna el área neta (mg/dL × hora) sobre la línea de base — positivo = exceso
    sobre baseline (hiperglucemia), negativo = sobrebolus/hipoglucemia.

    Ref: Brouns et al. (2005) Am J Clin Nutr; adaptado para análisis T1D.
    """
    if len(readings_timed) < 2:
        return None
    iauc = 0.0
    for i in range(len(readings_timed) - 1):
        t0, v0 = readings_timed[i]
        t1, v1 = readings_timed[i + 1]
        dt_h = (t1 - t0) / 60.0       # minutos → horas
        d0 = v0 - baseline
        d1 = v1 - baseline
        iauc += (d0 + d1) / 2.0 * dt_h
    return round(iauc, 1)


def _impacto_v2(meal, horas=3, readings=None):
    """
    Métricas postprandiales basadas en iAUC para una comida.

    Requiere ≥3 lecturas de glucosa en la ventana postprandial.
    La glucosa pre-comida sirve de referencia (baseline).

    - Si se pasa `readings` (lista pre-cargada con _precargar_glucosa),
      opera en memoria: 0 queries adicionales.

    Retorna:
    - pre          : glucosa pre-comida (mg/dL)
    - pico         : glucosa máxima en la ventana (mg/dL)
    - iauc         : área neta sobre baseline (mg/dL·h)
    - tiempo_pico  : minutos hasta el pico
    - tir_post     : % lecturas en rango 70-180 en la ventana
    - n_readings   : nº lecturas postprandiales
    - curva        : [(min, Δ_sobre_baseline), ...] para promedios inter-comidas
    """
    t0       = meal.timestamp
    t_pre    = t0 - timedelta(minutes=30)
    t_pre_ok = t0 + timedelta(minutes=5)   # pequeño margen para lectura simultánea
    t_post   = t0 + timedelta(hours=horas)

    if readings is not None:
        pre_list  = [r for r in readings if t_pre <= r.timestamp <= t_pre_ok]
        pre       = pre_list[-1] if pre_list else None
        posts     = sorted([r for r in readings if t0 < r.timestamp <= t_post],
                           key=lambda r: r.timestamp)
    else:
        pre = (GlucoseReading.query
               .filter(GlucoseReading.timestamp >= t_pre,
                       GlucoseReading.timestamp <= t_pre_ok)
               .order_by(GlucoseReading.timestamp.desc()).first())
        posts = (GlucoseReading.query
                 .filter(GlucoseReading.timestamp > t0,
                         GlucoseReading.timestamp <= t_post)
                 .order_by(GlucoseReading.timestamp).all())

    if not pre or len(posts) < 3:
        return None

    baseline = pre.value_mgdl
    readings_timed = [
        ((r.timestamp - meal.timestamp).total_seconds() / 60, r.value_mgdl)
        for r in posts
    ]
    iauc = _iauc_trapezoidal(baseline, readings_timed)

    pico_reading = max(posts, key=lambda r: r.value_mgdl)
    pico_val     = pico_reading.value_mgdl
    tiempo_pico  = round((pico_reading.timestamp - meal.timestamp).total_seconds() / 60)

    en_rango = sum(1 for r in posts if 70 <= r.value_mgdl <= 180)
    tir_post = round(100 * en_rango / len(posts))

    curva = [(round(min_t), round(val - baseline, 1)) for min_t, val in readings_timed]

    return {
        "pre":         int(baseline),
        "pico":        int(pico_val),
        "iauc":        iauc,
        "tiempo_pico": tiempo_pico,
        "tir_post":    tir_post,
        "n_readings":  len(posts),
        "curva":       curva,
    }


def _analisis_postprandial(days=60):
    """
    Análisis postprandial científico por tipo de comida.

    Clasifica cada comida según Smart et al. (2013) y Wolpert et al. (2013)
    y calcula iAUC trapezoidal en ventana de 3h post-comida.

    Retorna:
    - datos           : lista de dicts individuales (un item por comida)
    - clusters        : estadísticas por tipo (Simple / Mixta / Alta grasa+prot)
    - curvas_promedio : curva glucémica media por tipo (bins de 15 min)
    """
    from utils.nutrition_db import get_gi, gl_from_gi
    desde   = datetime.now() - timedelta(days=days)
    comidas = Meal.query.filter(Meal.timestamp >= desde, Meal.carbs_g > 0).all()

    # Precarga batch: 1 query para todas las lecturas (3h ventana post-comida)
    all_readings = _precargar_glucosa(comidas, horas_post=3)

    datos = []
    for c in comidas:
        imp = _impacto_v2(c, readings=all_readings)
        if not imp:
            continue
        if not (60 <= imp["pre"] <= 260):   # descartar hipoglucemia/crisis previas
            continue

        ch    = round(c.carbs_g   or 0, 1)
        prot  = round(c.protein_g or 0, 1)
        grasa = round(c.fat_g     or 0, 1)
        tipo  = _clasificar_comida(ch, prot, grasa)

        # ── Fibra y Carga Glucémica (GL) desde componentes ────────────────
        fiber_total = 0.0
        gl_total    = None
        if c.components:
            fiber_total = round(sum(comp.fiber_g or 0 for comp in c.components), 1)
            # GL = Σ (ÍG_i × CH_i / 100) para componentes con ÍG conocido
            gl_parts = []
            for comp in c.components:
                gi_comp = comp.glycemic_index
                if gi_comp is None:
                    gi_comp = get_gi(comp.name)   # fallback a base interna
                gl_part = gl_from_gi(gi_comp, comp.carbs_g or 0)
                if gl_part is not None:
                    gl_parts.append(gl_part)
            if gl_parts:
                gl_total = round(sum(gl_parts), 1)

        datos.append({
            "name":        c.name,
            "timestamp":   c.timestamp.strftime("%Y-%m-%d %H:%M"),
            "ch":          ch,
            "protein":     prot,
            "fat":         grasa,
            "fiber":       fiber_total,
            "gl":          gl_total,
            "pre":         imp["pre"],
            "pico":        imp["pico"],
            "iauc":        imp["iauc"],
            "tiempo_pico": imp["tiempo_pico"],
            "tir_post":    imp["tir_post"],
            "tipo":        tipo,
            "curva":       imp["curva"],
            "alto_riesgo": (grasa > 35 or prot > 50),
        })

    if not datos:
        return {"datos": [], "clusters": {}, "curvas_promedio": {}}

    # ── Estadísticas por cluster ───────────────────────────────────────────
    tipos = ["Simple", "Mixta", "Alta grasa/proteína"]
    clusters = {}
    for tipo in tipos:
        items = [d for d in datos if d["tipo"] == tipo]
        if not items:
            clusters[tipo] = None
            continue
        n     = len(items)
        iaucs = [d["iauc"] for d in items]
        avg_iauc = round(sum(iaucs) / n, 1)
        if n >= 2:
            mean_i   = avg_iauc
            variance = sum((x - mean_i) ** 2 for x in iaucs) / (n - 1)
            std      = variance ** 0.5
            cv = round(100 * std / abs(mean_i), 0) if mean_i != 0 else None
        else:
            cv = None
        # GL promedio (solo comidas con GL calculable)
        items_con_gl = [d for d in items if d["gl"] is not None]
        avg_gl = round(sum(d["gl"] for d in items_con_gl) / len(items_con_gl), 1) if items_con_gl else None

        clusters[tipo] = {
            "n":               n,
            "avg_iauc":        avg_iauc,
            "cv":              cv,
            "avg_tir":         round(sum(d["tir_post"]    for d in items) / n, 0),
            "avg_pico":        round(sum(d["pico"]        for d in items) / n, 0),
            "avg_tiempo_pico": round(sum(d["tiempo_pico"] for d in items) / n, 0),
            "avg_ch":          round(sum(d["ch"]          for d in items) / n, 1),
            "avg_fat":         round(sum(d["fat"]         for d in items) / n, 1),
            "avg_protein":     round(sum(d["protein"]     for d in items) / n, 1),
            "avg_fiber":       round(sum(d["fiber"]       for d in items) / n, 1),
            "avg_gl":          avg_gl,
            "n_con_gl":        len(items_con_gl),
            "n_alto_riesgo":   sum(1 for d in items if d["alto_riesgo"]),
        }

    # ── Curvas promedio por tipo (bins cada 15 min, ±7.5 min tolerancia) ──
    bins_min = list(range(0, 181, 15))
    curvas_promedio = {}
    for tipo in tipos:
        items = [d for d in datos if d["tipo"] == tipo]
        if not items:
            curvas_promedio[tipo] = None
            continue
        curva_tipo = []
        for b in bins_min:
            valores = [
                delta
                for d in items
                for (min_t, delta) in d["curva"]
                if abs(min_t - b) <= 7.5
            ]
            curva_tipo.append({
                "min":       b,
                "avg_delta": round(sum(valores) / len(valores), 1) if valores else None,
                "n":         len(valores),
            })
        curvas_promedio[tipo] = curva_tipo

    return {
        "datos":           datos,
        "clusters":        clusters,
        "curvas_promedio": curvas_promedio,
    }


def _analisis_ingredientes(days=60):
    """
    Impacto glucémico por ingrediente con atribución proporcional a CH.

    Principio: si una comida subió 60 mg/dL y el arroz aporta el 80% de los
    carbohidratos totales de esa comida, al arroz se le atribuyen 48 mg/dL
    y al pollo (0g CH) se le atribuyen 0 mg/dL.

    Calidad estadística:
    - Solo se incluyen comidas cuyo Δ glucémico es razonable (pre entre 60–250 mg/dL)
    - Solo ingredientes con ≥ 3 observaciones (con CH) tienen suficiente confianza
    - delta_por_10g se acota a ±80 mg/dL (límite fisiológico razonable)
    - Ingredientes con < 3 obs se muestran con flag "pocos datos"
    """
    desde = datetime.now() - timedelta(days=days)
    comidas = Meal.query.filter(Meal.timestamp >= desde).all()

    from collections import defaultdict
    por_ingrediente = defaultdict(list)

    for c in comidas:
        if not c.components:
            continue
        imp = _glucosa_impacto(c)
        if imp is None:
            continue

        # Descartar comidas con glucosa pre fuera de rango razonable
        if not (60 <= imp["pre"] <= 250):
            continue

        # Carbohidratos totales de la comida (suma de componentes)
        total_ch = sum(comp.carbs_g or 0 for comp in c.components)
        if total_ch < 3:          # comida sin carbohidratos relevantes → skip
            continue

        for comp in c.components:
            nombre = comp.name.strip().lower()
            if not nombre:
                continue
            ch = comp.carbs_g or 0

            # Fracción de CH que aporta este ingrediente al total de la comida
            fraccion = ch / total_ch if total_ch > 0 else 0
            # Delta atribuido proporcionalmente
            delta_atrib = round(imp["delta"] * fraccion, 1)

            por_ingrediente[nombre].append({
                "delta_atrib": delta_atrib,
                "carbs":       ch,
                "fraccion":    fraccion,
            })

    resultado = []
    for nombre, items in por_ingrediente.items():
        n          = len(items)
        avg_carbs  = round(sum(i["carbs"]       for i in items) / n, 1)
        avg_delta  = round(sum(i["delta_atrib"] for i in items) / n, 0)

        # Δ por 10g CH: normaliza por porción → comparable entre alimentos
        items_con_ch = [i for i in items if i["carbs"] > 0]
        if items_con_ch:
            raw = sum(i["delta_atrib"] / i["carbs"] * 10 for i in items_con_ch) / len(items_con_ch)
            # Acotar a rango fisiológico razonable (±80 mg/dL por 10g CH)
            delta_por_10g = round(max(-80, min(80, raw)), 0)
        else:
            delta_por_10g = 0

        # Confianza: necesitamos al menos 3 obs con CH para una conclusión útil
        confianza = "ok" if (n >= 3 and avg_carbs > 1) else ("baja" if avg_carbs > 1 else "sinCH")

        resultado.append({
            "nombre":        nombre.title(),
            "avg_delta":     avg_delta,
            "delta_por_10g": delta_por_10g,
            "avg_carbs":     avg_carbs,
            "n":             n,
            "tiene_ch":      avg_carbs > 1,
            "confianza":     confianza,
        })

    # Ordenar: con CH primero (por delta_por_10g desc), sin CH al final
    con_ch = sorted([r for r in resultado if r["tiene_ch"]],
                    key=lambda x: (x["confianza"] == "baja", -x["delta_por_10g"]))
    sin_ch = sorted([r for r in resultado if not r["tiene_ch"]],
                    key=lambda x: x["nombre"])
    return con_ch + sin_ch


def _sensibilidad_horaria(days=60):
    """
    Promedio de Δ glucémico por hora del día (6 bloques de 4h).
    """
    desde = datetime.now() - timedelta(days=days)
    comidas = Meal.query.filter(Meal.timestamp >= desde, Meal.carbs_g > 5).all()

    from collections import defaultdict
    all_readings = _precargar_glucosa(comidas, horas_post=2)
    por_bloque: dict = defaultdict(list)

    for c in comidas:
        imp = _glucosa_impacto(c, readings=all_readings)
        if imp is None:
            continue
        bloque = c.timestamp.hour // 4   # 0–3h, 4–7h, 8–11h, 12–15h, 16–19h, 20–23h
        por_bloque[bloque].append(imp["delta"])

    labels = ["00–04h","04–08h","08–12h","12–16h","16–20h","20–24h"]
    return [
        {
            "label":     labels[b],
            "avg_delta": round(sum(por_bloque[b])/len(por_bloque[b]), 0) if por_bloque[b] else None,
            "n":         len(por_bloque[b]),
        }
        for b in range(6)
    ]


@bp.route("/patrones", endpoint="patrones")
def patrones():
    days = request.args.get("dias", 30, type=int)

    ratio_datos   = _analisis_ratio_insulina(days=days)
    postprandial  = _analisis_postprandial(days=max(days, 60))
    horario       = _sensibilidad_horaria(days=max(days, 60))

    # ── Resumen ratio ─────────────────────────────────────────────────────
    ratio_resumen = {}
    if ratio_datos:
        from collections import Counter, defaultdict as _dd
        ratios = [d["ratio"] for d in ratio_datos]
        outcomes = [d["outcome"] for d in ratio_datos]
        n_total  = len(ratio_datos)
        ratio_resumen = {
            "n":           n_total,
            "mediana":     round(sorted(ratios)[n_total // 2], 1),
            "pct_rango":   round(100 * outcomes.count("rango")  / n_total),
            "pct_hiper":   round(100 * outcomes.count("hiper")  / n_total),
            "pct_hipo":    round(100 * outcomes.count("hipo")   / n_total),
            "avg_delta":   round(sum(d["delta"] for d in ratio_datos) / n_total, 0),
        }
        # Ratio con mayor % en rango (bucket de 2g)
        buckets: dict = _dd(list)
        for d in ratio_datos:
            b = round(d["ratio"] / 2) * 2   # bucket de 2 en 2
            buckets[b].append(d["outcome"])
        mejor_ratio = max(
            buckets.items(),
            key=lambda kv: kv[1].count("rango") / len(kv[1]) if kv[1] else 0,
        )
        ratio_resumen["mejor_ratio"]  = mejor_ratio[0]
        ratio_resumen["mejor_pct"]    = round(100 * mejor_ratio[1].count("rango") / len(mejor_ratio[1]))

    return render_template("patrones.html",
                           ratio_datos=ratio_datos,
                           ratio_resumen=ratio_resumen,
                           postprandial=postprandial,
                           horario=horario,
                           dias=days)
