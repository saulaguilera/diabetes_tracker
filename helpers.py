"""
helpers.py — Funciones compartidas entre blueprints.
No importa nada de app.py.
"""
from datetime import datetime, timedelta
from models import db, GlucoseReading, Meal, MealComponent, InsulinDose, Activity, UserSettings


# ── Reglas de auto-categorización ────────────────────────────────────────────
CATEGORIAS_REGLAS = {
    "Lácteos":        ["yogur", "yoghurt", "yogurt", "leche", "queso", "crema",
                       "mantequilla", "kéfir", "jocoque", "requesón", "cottage"],
    "Carnes":         ["pollo", "pechuga", "muslo", "carne", "res", "cerdo", "pavo",
                       "pato", "costilla", "chuleta", "bistec", "milanesa", "salchicha",
                       "chorizo", "jamón", "tocino", "bacon", "cordero", "conejo"],
    "Pescados":       ["salmón", "atún", "tilapia", "merluza", "camarón", "pescado",
                       "mariscos", "pulpo", "calamar", "mejillón", "sardina", "trucha"],
    "Huevos":         ["huevo", "omelette", "omelet", "revuelto", "estrellado"],
    "Cereales":       ["arroz", "pan", "pasta", "avena", "cereal", "tortilla",
                       "galleta", "granola", "maíz", "trigo", "cebada", "quinoa",
                       "espagueti", "fideos", "lasaña", "pizza", "taco", "burrito",
                       "quesadilla", "sope", "tostada", "tamale", "tamal"],
    "Frutas":         ["manzana", "plátano", "banana", "naranja", "fresa", "uva",
                       "mango", "sandía", "melón", "durazno", "pera", "kiwi",
                       "piña", "papaya", "frambuesa", "arándano", "cereza", "ciruela",
                       "guayaba", "maracuyá", "fruta"],
    "Verduras":       ["ensalada", "brócoli", "espinaca", "zanahoria", "tomate",
                       "lechuga", "pepino", "calabaza", "calabacín", "chayote",
                       "nopales", "acelga", "col", "coliflor", "ejotes", "ejote",
                       "betabel", "pimiento", "cebolla", "ajo", "elote", "verdura"],
    "Legumbres":      ["frijol", "frijoles", "lenteja", "lentejas", "garbanzo",
                       "habas", "soya", "edamame", "chícharo", "alverjón"],
    "Alcohol":        ["cerveza", "vino", "whisky", "whiskey", "tequila", "mezcal",
                       "ron", "vodka", "alcohol", "copa", "trago", "gin", "brandy",
                       "cognac", "champagne", "sidra", "michelada", "cóctel", "cocktail",
                       "licor", "bourbon"],
    "Dulces/Postres": ["chocolate", "helado", "nieve", "pastel", "torta dulce",
                       "galleta", "dulce", "azúcar", "postre", "gelatina", "flan",
                       "cheesecake", "brownie", "donut", "dona", "churro", "cajeta",
                       "caramelo", "malvavisco", "mermelada", "miel", "pan dulce"],
    "Bebidas":        ["jugo", "refresco", "café", "té", "agua", "licuado",
                       "batido", "smoothie", "atole", "cocoa", "proteína", "shake",
                       "bebida", "limonada", "naranjada", "soda"],
    "Snacks/Botanas": ["nueces", "almendras", "cacahuate", "maní", "papas", "chips",
                       "palomitas", "pistache", "semillas", "amaranto", "barra",
                       "proteína barra", "trail mix", "totopos", "botana"],
    "Comida rápida":  ["hamburguesa", "hot dog", "nuggets", "alitas", "sandwich",
                       "subway", "kfc", "mcdonalds", "burger", "papas fritas", "nachos"],
    "Sopas/Caldos":   ["sopa", "caldo", "consomé", "pozole", "menudo", "birria",
                       "crema de", "bisque", "minestrone", "ramen"],
}

# Colores por categoría para las badges
CATEGORIA_COLORES = {
    "Lácteos":        "info",
    "Carnes":         "danger",
    "Pescados":       "primary",
    "Huevos":         "warning",
    "Cereales":       "warning",
    "Frutas":         "success",
    "Verduras":       "success",
    "Legumbres":      "secondary",
    "Alcohol":        "dark",
    "Dulces/Postres": "danger",
    "Bebidas":        "info",
    "Snacks/Botanas": "warning",
    "Comida rápida":  "danger",
    "Sopas/Caldos":   "secondary",
}

ACTIVIDADES_COMUNES = [
    "Caminata", "Correr", "Ciclismo", "Natación", "Pesas / Gym",
    "Fútbol", "Básquetbol", "Yoga", "Pilates", "Baile",
    "Escaleras", "Ejercicio en casa", "Otro",
]


def _get_setting(key, default=None):
    """Lee un valor de configuración personal."""
    s = UserSettings.query.filter_by(key=key).first()
    return s.value if s else default


def _set_setting(key, value):
    """Guarda o actualiza un valor de configuración personal."""
    from datetime import datetime as _dt
    s = UserSettings.query.filter_by(key=key).first()
    if s:
        s.value = str(value)
        s.updated_at = _dt.utcnow()
    else:
        db.session.add(UserSettings(key=key, value=str(value)))
    db.session.commit()


def _auto_categorizar(nombre: str):
    """Detecta la categoría de una comida por su nombre."""
    nombre_lower = nombre.lower()
    for categoria, palabras in CATEGORIAS_REGLAS.items():
        for palabra in palabras:
            if palabra in nombre_lower:
                return categoria
    return None


def parse_datetime(date_str, time_str):
    """Combina strings de fecha y hora en un objeto datetime en hora local del usuario."""
    try:
        return datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
    except ValueError:
        return datetime.now()


def stats_resumen():
    """Estadísticas rápidas para el dashboard."""
    now = datetime.now()
    hace_24h = now - timedelta(hours=24)
    hace_7d = now - timedelta(days=7)

    lecturas_24h = GlucoseReading.query.filter(
        GlucoseReading.timestamp >= hace_24h
    ).all()

    valores = [r.value_mgdl for r in lecturas_24h]
    if valores:
        promedio = round(sum(valores) / len(valores), 1)
        minimo = round(min(valores), 1)
        maximo = round(max(valores), 1)
        en_rango = round(
            len([v for v in valores if 70 <= v <= 180]) / len(valores) * 100, 1
        )
        hipo = round(len([v for v in valores if v < 70]) / len(valores) * 100, 1)
        hiper = round(len([v for v in valores if v > 180]) / len(valores) * 100, 1)
    else:
        promedio = minimo = maximo = en_rango = hipo = hiper = None

    ultima_lectura = (
        GlucoseReading.query.order_by(GlucoseReading.timestamp.desc()).first()
    )
    ultima_comida = Meal.query.order_by(Meal.timestamp.desc()).first()
    ultima_insulina = InsulinDose.query.order_by(InsulinDose.timestamp.desc()).first()

    comidas_7d = Meal.query.filter(Meal.timestamp >= hace_7d).count()

    return {
        "promedio_24h": promedio,
        "minimo_24h": minimo,
        "maximo_24h": maximo,
        "tir_24h": en_rango,
        "hipo_24h": hipo,
        "hiper_24h": hiper,
        "lecturas_24h": len(lecturas_24h),
        "ultima_lectura": ultima_lectura,
        "ultima_comida": ultima_comida,
        "ultima_insulina": ultima_insulina,
        "comidas_7d": comidas_7d,
    }


def _detectar_patrones(days=30):
    """Analiza los últimos N días y devuelve lista de alertas de patrones."""
    from collections import defaultdict, Counter

    alertas = []
    now = datetime.now()
    desde = now - timedelta(days=days)

    lecturas = (GlucoseReading.query
                .filter(GlucoseReading.timestamp >= desde)
                .order_by(GlucoseReading.timestamp).all())

    if not lecturas:
        return []

    # ── 1. Hipoglucemias nocturnas recurrentes (00:00–06:00) ─────────────────
    hipos_noct = [r for r in lecturas if r.value_mgdl < 70 and 0 <= r.timestamp.hour < 6]
    noches_con_hipo = {r.timestamp.date() for r in hipos_noct}
    if len(noches_con_hipo) >= 2:
        horas = sorted({r.timestamp.hour for r in hipos_noct})
        franja = f"{min(horas):02d}:00–0{max(horas)+1}:00" if max(horas) < 6 else "madrugada"
        alertas.append({
            "nivel":   "danger",
            "icono":   "bi-moon-stars-fill",
            "titulo":  "Hipoglucemias nocturnas recurrentes",
            "detalle": (f"{len(noches_con_hipo)} noches con glucosa < 70 mg/dL "
                        f"entre las 00:00 y las 06:00 en los últimos {days} días."),
            "accion":  "Revisá tu dosis de insulina basal nocturna con tu endocrinólogo.",
        })

    # ── 2. Fenómeno del alba (sube sola entre 3am y 9am) ─────────────────────
    alba_count = 0
    fechas_alba = set()
    lecturas_por_hora = defaultdict(list)
    for r in lecturas:
        lecturas_por_hora[(r.timestamp.date(), r.timestamp.hour)].append(r)

    for r in lecturas:
        if r.timestamp.hour not in (3, 4, 5):
            continue
        fecha = r.timestamp.date()
        if fecha in fechas_alba:
            continue
        # Buscar lectura 3–6h después (hora 7–9am)
        vals_mañana = []
        for h in (7, 8, 9):
            vals_mañana += [x.value_mgdl for x in lecturas_por_hora.get((fecha, h), [])]
        if vals_mañana and max(vals_mañana) - r.value_mgdl > 40:
            alba_count += 1
            fechas_alba.add(fecha)

    if alba_count >= 3:
        alertas.append({
            "nivel":   "warning",
            "icono":   "bi-sunrise-fill",
            "titulo":  "Posible fenómeno del alba",
            "detalle": (f"En {alba_count} días se detectó un alza de glucosa > 40 mg/dL "
                        f"entre las 3am y las 9am sin ingesta previa."),
            "accion":  "Consultá con tu médico el ajuste de basal nocturna o el horario de Tresiba/Lantus.",
        })

    # ── 3. Hipoglucemias post-ejercicio ──────────────────────────────────────
    actividades = Activity.query.filter(Activity.timestamp >= desde).all()
    hipos_post_ej = []
    for act in actividades:
        ventana = act.timestamp + timedelta(hours=6)
        hipo = next((r for r in lecturas
                     if act.timestamp < r.timestamp <= ventana
                     and r.value_mgdl < 70), None)
        if hipo:
            hipos_post_ej.append((act, hipo))

    if len(hipos_post_ej) >= 2:
        alertas.append({
            "nivel":   "warning",
            "icono":   "bi-bicycle",
            "titulo":  "Hipoglucemias recurrentes post-ejercicio",
            "detalle": (f"{len(hipos_post_ej)} veces con glucosa < 70 mg/dL "
                        f"en las 6 horas posteriores a actividad física."),
            "accion":  "Considerá reducir el bolo previo al ejercicio o consumir CH extra antes de entrenar.",
        })

    # ── 4. Hipos recurrentes en la misma franja horaria (diurna) ─────────────
    hipos_diurnas = [r for r in lecturas if r.value_mgdl < 70 and 6 <= r.timestamp.hour < 24]
    conteo_hora = Counter(r.timestamp.hour for r in hipos_diurnas)
    for hora, cnt in conteo_hora.items():
        if cnt >= 3:
            alertas.append({
                "nivel":   "danger",
                "icono":   "bi-clock-history",
                "titulo":  f"Hipos frecuentes a las {hora:02d}:00 hs",
                "detalle": (f"{cnt} hipoglucemias registradas entre las {hora:02d}:00 "
                            f"y las {hora:02d}:59 en los últimos {days} días."),
                "accion":  "Revisá si coincide con el pico de acción de tu insulina o con el horario de comidas.",
            })

    # ── 5. Hiperglucemias frecuentes post-comida (pico > 250 mg/dL) ──────────
    comidas_periodo = Meal.query.filter(Meal.timestamp >= desde).all()
    hipers_post = 0
    for c in comidas_periodo:
        pico = next((r for r in lecturas
                     if c.timestamp < r.timestamp <= c.timestamp + timedelta(hours=2)
                     and r.value_mgdl > 250), None)
        if pico:
            hipers_post += 1

    if hipers_post >= 3:
        alertas.append({
            "nivel":   "warning",
            "icono":   "bi-graph-up-arrow",
            "titulo":  "Hiperglucemias frecuentes post-comida",
            "detalle": (f"{hipers_post} comidas con pico > 250 mg/dL en las 2 horas siguientes "
                        f"en los últimos {days} días."),
            "accion":  "Revisá el timing del bolo pre-comida y tu ratio insulina:carbohidratos.",
        })

    return alertas


def _precargar_glucosa(comidas, horas_post=5):
    """
    Carga TODAS las lecturas de glucosa relevantes para una lista de comidas
    en UNA sola query — en lugar de 2 queries por comida.

    Retorna lista ordenada por timestamp (apta para búsqueda en memoria).
    """
    if not comidas:
        return []
    ts_min = min(c.timestamp for c in comidas) - timedelta(minutes=30)
    ts_max = max(c.timestamp for c in comidas) + timedelta(hours=horas_post)
    return (GlucoseReading.query
            .filter(GlucoseReading.timestamp >= ts_min,
                    GlucoseReading.timestamp <= ts_max)
            .order_by(GlucoseReading.timestamp)
            .all())


def _precargar_bolus(comidas):
    """
    Carga TODOS los bolus relevantes para una lista de comidas en UNA sola query.
    Retorna lista ordenada por timestamp.
    """
    if not comidas:
        return []
    ts_min = min(c.timestamp for c in comidas) - timedelta(hours=1)
    ts_max = max(c.timestamp for c in comidas) + timedelta(minutes=30)
    return (InsulinDose.query
            .filter(InsulinDose.type == "bolus",
                    InsulinDose.timestamp >= ts_min,
                    InsulinDose.timestamp <= ts_max)
            .order_by(InsulinDose.timestamp)
            .all())


def _glucosa_impacto(meal, readings=None):
    """
    Pre/pico/delta glucémico de una comida.

    - Si se pasa `readings` (lista pre-cargada con _precargar_glucosa),
      opera completamente en memoria: 0 queries adicionales.
    - Sin `readings`: hace 2 queries a la DB (modo legacy, evitar en bucles).
    """
    t0     = meal.timestamp
    t_pre  = t0 - timedelta(minutes=30)
    t_post = t0 + timedelta(hours=2)

    if readings is not None:
        pre_list  = [r for r in readings if t_pre  <= r.timestamp <= t0]
        post_list = [r for r in readings if t0     <  r.timestamp <= t_post]
        pre = pre_list[-1] if pre_list else None   # más reciente antes de comer
    else:
        pre = (GlucoseReading.query
               .filter(GlucoseReading.timestamp >= t_pre,
                       GlucoseReading.timestamp <= t0)
               .order_by(GlucoseReading.timestamp.desc()).first())
        post_list = (GlucoseReading.query
                     .filter(GlucoseReading.timestamp > t0,
                             GlucoseReading.timestamp <= t_post)
                     .all())

    if not pre or not post_list:
        return None
    pico = max(r.value_mgdl for r in post_list)
    return {"pre": int(pre.value_mgdl), "pico": int(pico), "delta": round(pico - pre.value_mgdl, 0)}


def _save_meal_components(comida, form):
    """Parsea y guarda los componentes de una comida desde el form."""
    comp_names    = form.getlist("comp_name[]")
    comp_carbs    = form.getlist("comp_carbs[]")
    comp_proteins = form.getlist("comp_protein[]")
    comp_fats     = form.getlist("comp_fat[]")
    comp_cals     = form.getlist("comp_calories[]")
    comp_fibers   = form.getlist("comp_fiber[]")
    comp_gis      = form.getlist("comp_gi[]")
    comp_grams    = form.getlist("comp_grams[]")
    comp_food_ids = form.getlist("comp_food_id[]")

    componentes = []
    for i, name in enumerate(comp_names):
        name = name.strip()
        if not name:
            continue
        def _f(lst, idx, default=0.0):
            try: return float(lst[idx]) if lst[idx].strip() else default
            except (IndexError, ValueError): return default
        def _i(lst, idx):
            try: return int(float(lst[idx])) if lst[idx].strip() else None
            except (IndexError, ValueError): return None

        componentes.append(MealComponent(
            name           = name,
            food_item_id   = comp_food_ids[i].strip() or None if i < len(comp_food_ids) else None,
            carbs_g        = _f(comp_carbs, i),
            protein_g      = _f(comp_proteins, i),
            fat_g          = _f(comp_fats, i),
            calories       = _f(comp_cals, i),
            fiber_g        = _f(comp_fibers, i),
            glycemic_index = _i(comp_gis, i),
            grams          = _f(comp_grams, i) or None,
        ))

    # Actualizar totales en la comida padre
    if componentes:
        comida.carbs_g   = round(sum(c.carbs_g   for c in componentes), 1)
        comida.protein_g = round(sum(c.protein_g for c in componentes), 1)
        comida.fat_g     = round(sum(c.fat_g     for c in componentes), 1)
        comida.calories  = round(sum(c.calories  for c in componentes), 1)
    return componentes


# ─── Cálculos personales de sensibilidad ─────────────────────────────────────

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

        objetivo   = float(_get_setting("objetivo", 100))
        isf        = isf_personal or 40
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


# ─── Insights para dashboard ──────────────────────────────────────────────────

def _dashboard_insights():
    """
    Calcula los tres bloques de insight del dashboard:
      - tir7: TIR de los últimos 7 días + delta vs semana anterior
      - top_rec: recomendación más urgente (excluyendo "pocos datos")
      - isf / icr: valores personales calculados
    Todas las queries están agrupadas aquí para minimizar round-trips.
    """
    now = datetime.now()

    # ── TIR 7d y tendencia ───────────────────────────────────────────────────
    hace_7d  = now - timedelta(days=7)
    hace_14d = now - timedelta(days=14)

    r7   = GlucoseReading.query.filter(GlucoseReading.timestamp >= hace_7d).all()
    rprev = GlucoseReading.query.filter(
        GlucoseReading.timestamp >= hace_14d,
        GlucoseReading.timestamp <  hace_7d,
    ).all()

    def _tir(readings):
        if not readings:
            return None
        vals = [r.value_mgdl for r in readings]
        return round(len([v for v in vals if 70 <= v <= 180]) / len(vals) * 100, 1)

    tir7   = _tir(r7)
    tir7_prev = _tir(rprev)
    tir7_delta = round(tir7 - tir7_prev, 1) if (tir7 is not None and tir7_prev is not None) else None

    if tir7 is None:
        tir7_color = 'secondary'
    elif tir7 >= 70:
        tir7_color = 'success'
    elif tir7 >= 50:
        tir7_color = 'warning'
    else:
        tir7_color = 'danger'

    # ── Recomendación más urgente ────────────────────────────────────────────
    top_rec = None
    try:
        from utils.recommendations import generate_recommendations
        recs = generate_recommendations(days=30)
        # Excluir el placeholder de "pocos datos"
        recs_reales = [r for r in recs if r.get('category') != 'habitos'
                       or 'suficientes datos' not in r.get('title', '')]
        if recs_reales:
            top_rec = recs_reales[0]   # ya viene ordenado por prioridad
    except Exception:
        pass

    # ── ISF / ICR personal ───────────────────────────────────────────────────
    isf, n_isf = _calcular_isf_personal(days=90)
    icr, n_icr = _calcular_icr_personal(days=90)

    # ── IOB / COB / ROC (cinética activa) ────────────────────────────────────
    kinetics = {}
    try:
        from utils.kinetics import get_kinetics_snapshot
        kinetics = get_kinetics_snapshot(hours_lookback=6)
    except Exception:
        pass

    return {
        'tir7':        tir7,
        'tir7_delta':  tir7_delta,
        'tir7_color':  tir7_color,
        'top_rec':     top_rec,
        'isf':         isf,
        'isf_n':       n_isf,
        'icr':         icr,
        'icr_n':       n_icr,
        'kinetics':    kinetics,
    }
