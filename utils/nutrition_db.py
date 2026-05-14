"""
Base de datos nutricional interna (valores por 100g).
Fuente: USDA FoodData Central + tablas nutricionales LATINFOODS.

Formato: (carbs_total, fibra, proteina, grasa, kcal)  — todos por 100g
CH netos = carbs_total - fibra  ← esto es lo que impacta la glucemia
"""

import re

# Porción estándar para alimentos contados por unidad.
# Valor: (gramos_por_unidad, nombre_unidad)
PORCIONES_ESTANDAR = {
    "huevo":        (55,  "huevo"),
    "pan blanco":   (30,  "rebanada"),
    "pan integral": (30,  "rebanada"),
    "pan":          (30,  "rebanada"),
    "tortilla":     (30,  "tortilla"),
    "arepa":        (80,  "arepa"),
    "arepas":       (80,  "arepa"),
}

# Clave: texto en minúsculas (búsqueda por substring)
# Valor: (carbs_total_100g, fibra_100g, protein_100g, fat_100g, kcal_100g)
NUTRITION_DB = {
    # ── Legumbres (cocidas) ───────────────────────────────────────────────
    "garbanzo":        (27.4, 7.6,  8.9,  2.6, 164),
    "lenteja":         (20.1, 7.9,  9.0,  0.4, 116),
    "poroto":          (23.7, 6.4,  8.7,  0.5, 127),
    "frijol":          (23.7, 6.4,  8.7,  0.5, 127),
    "judía":           (23.7, 6.4,  8.7,  0.5, 127),
    "arveja":          (14.5, 5.1,  5.4,  0.4,  81),
    "chícharo":        (14.5, 5.1,  5.4,  0.4,  81),
    "soja":            (11.9, 6.0, 16.6,  6.4, 173),
    "haba":            (19.7, 5.4,  7.9,  0.7, 110),
    "edamame":         ( 8.9, 5.2, 11.9,  5.2, 121),

    # ── Cereales y granos ─────────────────────────────────────────────────
    "arroz blanco":    (28.6, 0.4,  2.7,  0.3, 130),
    "arroz integral":  (23.5, 1.8,  2.6,  0.9, 111),
    "arroz":           (28.6, 0.4,  2.7,  0.3, 130),
    "pasta":           (31.0, 1.8,  5.8,  0.9, 158),
    "fideos":          (31.0, 1.8,  5.8,  0.9, 158),
    "espagueti":       (31.0, 1.8,  5.8,  0.9, 158),
    "avena":           (66.3,10.6, 16.9,  6.9, 389),
    "pan blanco":      (49.4, 2.7,  8.9,  3.2, 265),
    "pan integral":    (43.1, 6.0,  8.0,  3.5, 247),
    "pan":             (49.4, 2.7,  8.9,  3.2, 265),
    "tortilla":        (45.9, 3.2,  6.2,  2.5, 218),
    "arepas":          (26.0, 1.0,  2.0,  1.0, 120),
    "arepa":           (26.0, 1.0,  2.0,  1.0, 120),
    "maíz":            (19.0, 2.7,  3.2,  1.2,  96),
    "choclo":          (19.0, 2.7,  3.2,  1.2,  96),
    "elote":           (19.0, 2.7,  3.2,  1.2,  96),
    "quinoa":          (22.0, 2.8,  4.4,  1.9, 120),
    "cuscús":          (23.2, 1.4,  3.8,  0.2, 112),
    "harina de trigo": (76.3, 2.7, 10.3,  1.0, 364),
    "galleta":         (63.0, 2.0,  7.0, 10.0, 420),
    "cereal":          (83.0, 3.0,  7.5,  3.5, 379),

    # ── Tubérculos ────────────────────────────────────────────────────────
    "papa":            (17.5, 2.2,  2.0,  0.1,  77),
    "patata":          (17.5, 2.2,  2.0,  0.1,  77),
    "batata":          (20.1, 3.0,  1.6,  0.1,  86),
    "camote":          (20.1, 3.0,  1.6,  0.1,  86),
    "yuca":            (38.1, 1.8,  1.4,  0.3, 160),
    "mandioca":        (38.1, 1.8,  1.4,  0.3, 160),
    "ñame":            (27.9, 4.1,  1.5,  0.2, 118),
    "taro":            (26.5, 4.1,  1.5,  0.2, 112),
    "remolacha":       ( 9.6, 2.8,  1.6,  0.2,  43),
    "betarraga":       ( 9.6, 2.8,  1.6,  0.2,  43),

    # ── Frutas ────────────────────────────────────────────────────────────
    "manzana":         (13.8, 2.4,  0.3,  0.2,  52),
    "pera":            (15.2, 3.1,  0.4,  0.1,  57),
    "naranja":         (11.8, 2.4,  0.9,  0.1,  47),
    "mandarina":       (13.3, 1.8,  0.8,  0.3,  53),
    "plátano":         (22.8, 2.6,  1.1,  0.3,  89),
    "banana":          (22.8, 2.6,  1.1,  0.3,  89),
    "uva":             (18.1, 0.9,  0.7,  0.2,  69),
    "sandía":          ( 7.6, 0.4,  0.6,  0.2,  30),
    "melón":           ( 8.2, 0.9,  0.8,  0.2,  34),
    "mango":           (15.0, 1.6,  0.8,  0.4,  60),
    "piña":            (13.1, 1.4,  0.5,  0.1,  50),
    "frutilla":        ( 7.7, 2.0,  0.7,  0.3,  32),
    "fresa":           ( 7.7, 2.0,  0.7,  0.3,  32),
    "kiwi":            (14.7, 3.0,  1.1,  0.5,  61),
    "durazno":         ( 9.5, 1.5,  0.9,  0.3,  39),
    "ciruela":         (11.4, 1.4,  0.7,  0.3,  46),
    "cereza":          (16.0, 2.1,  1.1,  0.2,  63),
    "palta":           ( 8.5, 6.7,  2.0, 14.7, 160),   # ← 1.8g CH netos!
    "aguacate":        ( 8.5, 6.7,  2.0, 14.7, 160),
    "papaya":          (10.8, 1.7,  0.5,  0.3,  43),
    "limón":           ( 9.3, 2.8,  1.1,  0.3,  29),

    # ── Lácteos ───────────────────────────────────────────────────────────
    "leche entera":    ( 4.7, 0.0,  3.2,  3.7,  61),
    "leche":           ( 4.7, 0.0,  3.2,  3.7,  61),
    "leche descremada":( 4.9, 0.0,  3.4,  0.2,  35),
    "yogur natural":   ( 4.7, 0.0,  3.5,  3.3,  61),
    "yogur":           ( 6.0, 0.0,  5.0,  0.2,  59),
    "queso":           ( 1.3, 0.0, 25.0, 33.0, 402),
    "queso fresco":    ( 3.4, 0.0, 18.0, 20.0, 264),
    "ricotta":         ( 3.0, 0.0, 11.3, 13.0, 174),
    "mantequilla":     ( 0.1, 0.0,  0.9, 81.0, 717),
    "manteca":         ( 0.1, 0.0,  0.9, 81.0, 717),
    "crema":           ( 3.4, 0.0,  2.8, 20.0, 206),

    # ── Carnes ────────────────────────────────────────────────────────────
    "pollo":           ( 0.0, 0.0, 27.0,  3.6, 165),
    "pechuga":         ( 0.0, 0.0, 31.0,  3.6, 165),
    "muslo":           ( 0.0, 0.0, 24.0,  8.0, 177),
    "pavo":            ( 0.0, 0.0, 29.0,  1.7, 135),
    "carne vacuna":    ( 0.0, 0.0, 26.0,  8.0, 179),
    "carne de res":    ( 0.0, 0.0, 26.0,  8.0, 179),
    "carne":           ( 0.0, 0.0, 26.0,  8.0, 179),
    "lomo":            ( 0.0, 0.0, 28.0,  4.0, 153),
    "cerdo":           ( 0.0, 0.0, 25.7, 14.0, 242),
    "chancho":         ( 0.0, 0.0, 25.7, 14.0, 242),
    "cordero":         ( 0.0, 0.0, 25.6, 16.5, 258),
    "atún":            ( 0.0, 0.0, 30.0,  0.5, 116),
    "salmón":          ( 0.0, 0.0, 25.4, 13.4, 208),
    "merluza":         ( 0.0, 0.0, 16.7,  0.8,  74),
    "sardina":         ( 0.0, 0.0, 24.6, 11.5, 208),
    "camarón":         ( 0.9, 0.0, 20.3,  1.7,  99),
    "huevo":           ( 1.1, 0.0, 12.6, 10.6, 155),

    # ── Verduras ─────────────────────────────────────────────────────────
    "lechuga":         ( 2.9, 1.3,  1.4,  0.2,  15),
    "espinaca":        ( 3.6, 2.2,  2.9,  0.4,  23),
    "brócoli":         ( 6.6, 2.6,  2.8,  0.4,  34),
    "coliflor":        ( 5.0, 2.0,  1.9,  0.3,  25),
    "tomate":          ( 3.9, 1.2,  0.9,  0.2,  18),
    "pepino":          ( 3.6, 0.5,  0.7,  0.1,  16),
    "zanahoria":       ( 9.6, 2.8,  0.9,  0.2,  41),
    "cebolla":         ( 9.3, 1.7,  1.1,  0.1,  40),
    "pimiento":        ( 6.0, 2.1,  1.0,  0.3,  31),
    "calabaza":        ( 7.0, 0.5,  1.0,  0.1,  34),
    "zapallo":         ( 7.0, 0.5,  1.0,  0.1,  34),
    "apio":            ( 3.0, 1.6,  0.7,  0.2,  16),
    "champiñón":       ( 3.3, 1.0,  3.1,  0.3,  22),
    "hongo":           ( 3.3, 1.0,  3.1,  0.3,  22),

    # ── Snacks / frutos secos ────────────────────────────────────────────
    "hummus":          (14.3, 6.0,  7.9,  9.6, 177),
    "maní":            (16.1, 8.5, 25.8, 49.2, 567),
    "cacahuete":       (16.1, 8.5, 25.8, 49.2, 567),
    "almendra":        ( 9.1,12.5, 21.2, 49.9, 579),
    "nuez":            (13.7, 6.7, 15.2, 65.2, 654),
    "semilla chía":    (42.1,34.4, 16.5, 30.7, 486),   # ← solo 7.7g CH netos!

    # ── Dulces / azúcar ──────────────────────────────────────────────────
    "azúcar":          (100.0, 0.0,  0.0,  0.0, 387),
    "miel":            (82.4,  0.2,  0.3,  0.0, 304),
    "chocolate":       (59.4,  3.4,  5.4, 30.0, 546),
    "helado":          (24.0,  0.0,  3.5,  7.0, 207),
    "mermelada":       (65.0,  0.6,  0.4,  0.1, 250),
}


def _parsear_nombre(nombre: str):
    """
    Extrae gramos/unidades y nombre limpio de un string libre.
    Soporta: "250 g Carne", "Carne 250 g", "250g pollo", "4 huevos", etc.
    Devuelve: (cantidad_o_None, es_gramos: bool, nombre_limpio: str)
    """
    s = nombre.strip()
    NUM  = r'(\d+(?:[.,]\d+)?)'
    UNIG = r'\s*(gr?(?:amos?)?)'   # g / gr / gram / gramos (opcional)

    # "NNN [g] texto" al inicio
    m = re.match(r'^' + NUM + UNIG + r'?\s+(.+)$', s, re.IGNORECASE)
    if m:
        cantidad      = float(m.group(1).replace(',', '.'))
        unidad_str    = (m.group(2) or '').strip()
        nombre_limpio = m.group(3).strip()
        return cantidad, bool(unidad_str), nombre_limpio.lower()

    # "texto NNN [g]" al final
    m = re.match(r'^(.+?)\s+' + NUM + UNIG + r'?\s*$', s, re.IGNORECASE)
    if m:
        nombre_limpio = m.group(1).strip()
        cantidad      = float(m.group(2).replace(',', '.'))
        unidad_str    = (m.group(3) or '').strip()
        return cantidad, bool(unidad_str), nombre_limpio.lower()

    return None, False, s.lower()


def buscar_nutricion(nombre: str):
    """
    Busca el alimento en la DB.
    Devuelve (key, carbs_total, fibra, protein, fat, kcal) o None.
    """
    _, _, nombre_limpio = _parsear_nombre(nombre)

    for buscar in [nombre_limpio, nombre.lower().strip()]:
        buscar = buscar.strip()
        if not buscar:
            continue
        if buscar in NUTRITION_DB:
            return (buscar,) + NUTRITION_DB[buscar]
        for key, vals in NUTRITION_DB.items():
            if key in buscar:
                return (key,) + vals
        for word in buscar.split():
            if len(word) < 3:
                continue
            for key, vals in NUTRITION_DB.items():
                if word in key:
                    return (key,) + vals

    return None


def estimar(nombre: str, carbs_usuario: float = 0, grams_usuario: float = 0):
    """
    Estima macros dado un nombre de alimento.
    Siempre devuelve CH netos (= CH totales - fibra) para uso en bolo.

    Prioridad de escalado:
    1. Gramos en el nombre ("250g carne")
    2. grams_usuario
    3. CH del usuario → escalar por ratio (alimentos con carbohidratos)
    4. Unidades en el nombre ("4 huevos") × porción estándar
    5. Fallback 100g
    """
    found = buscar_nutricion(nombre)
    if found is None:
        return None

    db_key, carbs_total_100, fibra_100, prot_100, fat_100, kcal_100 = found
    net_carbs_100 = max(carbs_total_100 - fibra_100, 0)
    cantidad_nombre, es_gramos, _ = _parsear_nombre(nombre)

    alta_fibra = fibra_100 >= 2.0  # Marcador para avisar al usuario

    # ── Determinar total_g ────────────────────────────────────────────────
    total_g = 0
    base    = "carbs"
    nota    = ""

    if cantidad_nombre and es_gramos:
        total_g = cantidad_nombre
        base    = "gramos"
        nota    = f"{total_g:.0f}g de {db_key}"

    elif grams_usuario > 0:
        total_g = grams_usuario
        base    = "gramos"
        nota    = f"{total_g:.0f}g de {db_key}"

    elif carbs_total_100 >= 2 and carbs_usuario > 0:
        # Escalar por CH netos si hay fibra significativa,
        # sino por CH totales (igual resultado si fibra ≈ 0)
        ref = net_carbs_100 if net_carbs_100 > 0 else carbs_total_100
        factor = carbs_usuario / ref
        total_g = factor * 100
        base    = "carbs"
        nota    = ""

    elif cantidad_nombre and not es_gramos:
        porcion = PORCIONES_ESTANDAR.get(db_key)
        if porcion:
            total_g = cantidad_nombre * porcion[0]
            nota    = f"{cantidad_nombre:.0f} {porcion[1]}(s) = {total_g:.0f}g"
        else:
            total_g = cantidad_nombre * 100
            nota    = f"{cantidad_nombre:.0f} porciones ≈ {total_g:.0f}g"
        base = "gramos"

    else:
        porcion = PORCIONES_ESTANDAR.get(db_key)
        if porcion:
            total_g = porcion[0]
            nota    = f"1 {porcion[1]} = {total_g}g"
        else:
            total_g = 100
            nota    = "100g"
        base = "gramos"

    factor = total_g / 100.0
    net_carbs = round(net_carbs_100 * factor, 1)
    carbs_total = round(carbs_total_100 * factor, 1)
    fibra = round(fibra_100 * factor, 1)

    return {
        "protein_g":   round(prot_100 * factor, 1),
        "fat_g":       round(fat_100  * factor, 1),
        "calories":    round(kcal_100 * factor, 1),
        "carbs_g":     net_carbs,        # CH netos → lo que va al campo CH
        "carbs_total": carbs_total,
        "fibra_g":     fibra,
        "alta_fibra":  alta_fibra,
        "base":        base,
        "grams":       total_g,
        "key":         db_key,
        "nota":        nota,
    }


# Compatibilidad
def estimar_desde_carbs(nombre: str, carbs_usuario: float):
    return estimar(nombre, carbs_usuario=carbs_usuario)
