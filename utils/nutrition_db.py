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
    "huevo":             (55,   "huevo"),
    "pan blanco":        (30,   "rebanada"),
    "pan integral":      (30,   "rebanada"),
    "pan":               (30,   "rebanada"),
    "tortilla":          (30,   "tortilla"),
    "arepa":             (80,   "arepa"),
    "arepas":            (80,   "arepa"),
    "empanada":          (90,   "empanada"),
    "alfajor":           (50,   "alfajor"),
    "barra cereal":      (40,   "barra"),
    "dátil":             (8,    "dátil"),
    "higo":              (50,   "higo"),
    "higo seco":         (15,   "higo"),
    "naranja":           (150,  "naranja"),
    "manzana":           (180,  "manzana"),
    "plátano":           (120,  "plátano"),
    "banana":            (120,  "banana"),
    "kiwi":              (80,   "kiwi"),
    "pizza":             (100,  "porción"),
    "hamburguesa":       (200,  "hamburguesa"),
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

    # ── Frutos secos (nueces, semillas) ──────────────────────────────────
    "maní":            (16.1, 8.5, 25.8, 49.2, 567),
    "cacahuete":       (16.1, 8.5, 25.8, 49.2, 567),
    "almendra":        ( 9.1,12.5, 21.2, 49.9, 579),
    "nuez":            (13.7, 6.7, 15.2, 65.2, 654),
    "nuez de brasil":  ( 3.9, 2.1, 14.3, 66.4, 659),
    "nuez de macadamia":(13.8, 8.6,  7.9, 75.8, 718),
    "pistacho":        (27.2,10.3, 20.6, 45.4, 560),
    "anacardo":        (30.2, 3.3, 18.2, 43.9, 553),
    "castañas de cajú":(30.2, 3.3, 18.2, 43.9, 553),
    "avellana":        (16.7, 9.7, 15.0, 60.8, 628),
    "pecana":          (13.9, 9.4,  9.2, 72.0, 691),
    "pino":            (19.0, 3.7, 13.7, 68.4, 673),
    "semilla girasol": (20.0, 8.6, 20.8, 51.5, 584),
    "semilla calabaza": (15.3, 6.5, 19.4, 19.4, 446),
    "semilla lino":    (28.9,27.3, 18.3, 42.2, 534),
    "semilla sésamo":  (23.5,11.8, 17.7, 49.7, 573),
    "semilla chía":    (42.1,34.4, 16.5, 30.7, 486),
    "coco rallado":    (15.2, 9.0,  3.3, 33.5, 354),
    "leche de coco":   ( 6.0, 0.0,  2.3, 24.0, 230),

    # ── Frutas secas / deshidratadas ──────────────────────────────────────
    "pasa":            (79.2, 3.7,  3.1,  0.5, 299),
    "uva pasa":        (79.2, 3.7,  3.1,  0.5, 299),
    "dátil":           (75.0, 8.0,  2.5,  0.4, 282),
    "higo seco":       (63.9, 9.8,  3.3,  0.9, 249),
    "damasco seco":    (62.6, 7.3,  3.4,  0.5, 241),
    "albaricoque seco":(62.6, 7.3,  3.4,  0.5, 241),
    "ciruela seca":    (63.9, 7.1,  2.2,  0.4, 240),
    "arándano seco":   (82.4, 5.3,  0.1,  1.4, 308),
    "mango seco":      (78.6, 2.4,  2.5,  1.2, 319),

    # ── Más frutas frescas ─────────────────────────────────────────────────
    "frambuesa":       (11.9, 6.5,  1.2,  0.7,  52),
    "arándano":        (14.5, 2.4,  0.7,  0.3,  57),
    "mora":            (13.8, 5.3,  1.4,  0.5,  43),
    "maracuyá":        (22.4, 10.4, 2.2,  0.7,  97),
    "granada":         (18.7, 4.0,  1.7,  1.2,  83),
    "higo":            (19.2, 2.9,  0.8,  0.3,  74),
    "caqui":           (18.6, 3.6,  0.7,  0.4,  70),
    "guayaba":         (14.3, 5.4,  2.6,  1.0,  68),
    "lúcuma":          (25.0, 3.0,  1.5,  0.5, 100),
    "chirimoya":       (25.0, 3.0,  1.6,  0.6, 101),
    "tamarindo":       (62.5, 5.1,  2.8,  0.6, 239),
    "coco fresco":     ( 9.0, 4.5,  3.3, 33.5, 354),

    # ── Más verduras ──────────────────────────────────────────────────────
    "espárrago":       ( 3.9, 2.1,  2.2,  0.1,  20),
    "alcachofa":       (10.5, 5.4,  3.3,  0.2,  53),
    "alcaucil":        (10.5, 5.4,  3.3,  0.2,  53),
    "chauchas":        ( 7.9, 3.4,  1.8,  0.1,  35),
    "poroto verde":    ( 7.9, 3.4,  1.8,  0.1,  35),
    "habichuela":      ( 7.9, 3.4,  1.8,  0.1,  35),
    "berenjenas":      ( 5.9, 3.0,  1.0,  0.2,  25),
    "berenjena":       ( 5.9, 3.0,  1.0,  0.2,  25),
    "zucchini":        ( 3.1, 1.0,  1.2,  0.3,  17),
    "zapallito":       ( 3.1, 1.0,  1.2,  0.3,  17),
    "choclo cocido":   (25.0, 2.8,  3.3,  1.4, 130),
    "puerro":          (14.2, 1.8,  1.5,  0.3,  61),
    "ajo":             (33.1, 2.1,  6.4,  0.5, 149),
    "jengibre":        (18.0, 2.0,  1.8,  0.8,  80),
    "repollo":         ( 5.8, 2.5,  1.3,  0.1,  25),
    "col":             ( 5.8, 2.5,  1.3,  0.1,  25),
    "repollo morado":  ( 7.4, 2.1,  1.4,  0.2,  31),
    "acelga":          ( 3.7, 1.6,  1.8,  0.2,  19),
    "berro":           ( 1.3, 0.5,  2.3,  0.1,  11),
    "rúcula":          ( 3.7, 1.6,  2.6,  0.7,  25),
    "maíz dulce":      (25.0, 2.8,  3.3,  1.4, 130),
    "chucrut":         ( 4.3, 2.9,  0.9,  0.1,  19),

    # ── Más carnes y proteínas ────────────────────────────────────────────
    "costilla":        ( 0.0, 0.0, 22.0, 23.0, 291),
    "bife":            ( 0.0, 0.0, 27.0,  9.0, 198),
    "asado":           ( 0.0, 0.0, 24.0, 18.0, 262),
    "chorizo":         ( 2.0, 0.0, 14.0, 30.0, 332),
    "salchicha":       ( 3.0, 0.0, 12.0, 29.0, 320),
    "jamón":           ( 1.5, 0.0, 17.0,  6.0, 130),
    "tocino":          ( 0.7, 0.0, 13.0, 42.0, 431),
    "panceta":         ( 0.7, 0.0, 13.0, 42.0, 431),
    "mortadela":       ( 3.0, 0.0, 13.0, 28.0, 312),
    "hígado":          ( 4.0, 0.0, 20.0,  4.7, 135),
    "calamar":         ( 3.1, 0.0, 18.0,  1.4,  92),
    "pulpo":           ( 2.2, 0.0, 14.9,  1.0,  82),
    "langostino":      ( 0.9, 0.0, 20.3,  1.7,  99),
    "trucha":          ( 0.0, 0.0, 20.8,  5.4, 141),
    "bacalao":         ( 0.0, 0.0, 17.8,  0.9,  82),
    "tilapia":         ( 0.0, 0.0, 20.1,  2.7, 111),
    "dorado":          ( 0.0, 0.0, 21.0,  3.5, 115),

    # ── Más lácteos ───────────────────────────────────────────────────────
    "leche de almendra":( 0.5, 0.3,  0.6,  1.0,  13),
    "leche de avena":  ( 6.6, 0.8,  0.9,  1.5,  46),
    "kéfir":           ( 4.5, 0.0,  3.5,  3.5,  61),
    "queso cottage":   ( 3.4, 0.0, 11.1,  4.3,  98),
    "queso mozzarella":( 2.2, 0.0, 22.2, 22.4, 299),
    "queso parmesano": ( 3.2, 0.0, 35.8, 25.8, 392),
    "crema agria":     ( 3.3, 0.0,  2.4, 20.0, 193),
    "yogur griego":    ( 3.9, 0.0, 10.2,  0.4,  59),

    # ── Aceites y grasas ──────────────────────────────────────────────────
    "aceite oliva":    ( 0.0, 0.0,  0.0,100.0, 884),
    "aceite vegetal":  ( 0.0, 0.0,  0.0,100.0, 884),
    "aceite coco":     ( 0.0, 0.0,  0.0,100.0, 862),
    "mayonesa":        ( 0.6, 0.0,  1.0, 74.9, 680),
    "margarina":       ( 0.7, 0.0,  0.2, 80.0, 717),

    # ── Bebidas ───────────────────────────────────────────────────────────
    "jugo naranja":    (10.4, 0.2,  0.7,  0.2,  45),
    "jugo manzana":    (11.7, 0.2,  0.1,  0.1,  46),
    "leche chocolatada":(10.4, 0.3,  3.4,  1.5,  68),
    "café":            ( 0.0, 0.0,  0.3,  0.0,   2),
    "té":              ( 0.2, 0.0,  0.0,  0.0,   1),
    "bebida":          (10.6, 0.0,  0.0,  0.0,  42),
    "gaseosa":         (10.6, 0.0,  0.0,  0.0,  42),

    # ── Preparaciones / comidas completas ─────────────────────────────────
    "pizza":           (33.0, 2.3, 11.0, 10.0, 266),
    "hamburguesa":     (24.0, 1.5, 13.0, 14.0, 274),
    "sándwich":        (27.0, 1.8, 10.0,  8.0, 220),
    "empanada":        (28.0, 1.5,  9.0, 14.0, 275),
    "sopa":            ( 5.0, 0.5,  2.5,  1.5,  40),
    "ensalada":        ( 4.0, 1.5,  1.5,  0.5,  25),
    "guiso":           (15.0, 2.0,  8.0,  5.0, 138),
    "arroz con pollo": (18.0, 0.5, 12.0,  4.0, 156),
    "puré de papa":    (14.0, 1.5,  2.0,  3.5, 100),
    "omelette":        ( 1.0, 0.0,  9.5,  8.0, 117),
    "tortilla española":(10.0, 0.5, 10.0, 11.0, 185),
    "panqueque":       (28.0, 0.9,  5.6,  5.3, 186),
    "waffle":          (33.0, 1.0,  7.4,  9.5, 244),

    # ── Dulces y postres ──────────────────────────────────────────────────
    "azúcar":          (100.0, 0.0,  0.0,  0.0, 387),
    "miel":            (82.4,  0.2,  0.3,  0.0, 304),
    "chocolate negro": (45.9,  10.9, 4.9, 43.1, 598),
    "chocolate leche": (59.4,  3.4,  7.7, 32.4, 546),
    "chocolate":       (59.4,  3.4,  5.4, 30.0, 546),
    "helado":          (24.0,  0.0,  3.5,  7.0, 207),
    "mermelada":       (65.0,  0.6,  0.4,  0.1, 250),
    "dulce de leche":  (55.0,  0.0,  6.0,  7.0, 313),
    "flan":            (20.0,  0.0,  4.5,  5.0, 145),
    "torta":           (55.0,  1.0,  5.0, 15.0, 371),
    "alfajor":         (65.0,  1.5,  5.5, 16.0, 417),
    "granola":         (60.0,  5.5,  8.0, 12.0, 380),
    "barra cereal":    (65.0,  3.0,  5.0,  8.0, 350),

    # ── Salsas y condimentos ──────────────────────────────────────────────
    "ketchup":         (26.1, 0.3,  1.3,  0.1, 112),
    "salsa de tomate": (11.0, 2.0,  2.0,  2.5,  70),
    "salsa soja":      ( 6.7, 0.8,  8.1,  0.0,  53),
    "mostaza":         ( 5.8, 1.6,  3.7,  3.3,  60),
    "vinagre":         ( 0.1, 0.0,  0.0,  0.0,   3),

    # ── Snacks salados ────────────────────────────────────────────────────
    "papa frita":      (53.0, 3.4,  6.6, 35.0, 536),
    "palomitas":       (74.0,14.5, 12.9,  4.5, 382),
    "nachos":          (60.0, 4.0,  7.0, 24.0, 484),
    "arroz inflado":   (82.0, 1.0,  7.0,  1.5, 372),
    "tostada":         (65.0, 3.0,  8.0,  5.0, 330),
    "cracker":         (65.0, 2.5,  9.0,  9.0, 380),
    "hummus":          (14.3, 6.0,  7.9,  9.6, 177),

    # ── Mezclas / combos típicos ──────────────────────────────────────────
    # (valores promedio ponderados de sus componentes habituales)
    "mix frutos secos":   (18.0,10.0, 15.0, 52.0, 580),
    "mezcla frutos secos":(18.0,10.0, 15.0, 52.0, 580),
    "frutos secos":       (18.0,10.0, 15.0, 52.0, 580),
    "trail mix":          (38.0, 6.5, 10.0, 35.0, 490),  # con frutas secas
    "mix nueces":         (14.0, 7.5, 15.0, 60.0, 620),
    "granola con leche":  (40.0, 3.5,  6.0,  5.5, 225),
    "avena con leche":    (20.0, 1.8,  5.0,  2.5, 120),
    "avena con fruta":    (28.0, 3.5,  5.0,  2.0, 140),
    "yogur con granola":  (35.0, 2.5,  8.0,  5.0, 215),
    "yogur con fruta":    (14.0, 1.0,  4.5,  1.5,  85),
    "ensalada de frutas": (14.0, 1.8,  0.8,  0.2,  58),
    "fruta mixta":        (13.0, 1.8,  0.7,  0.2,  54),
    "verduras mixtas":    ( 6.0, 2.5,  1.5,  0.2,  30),
    "ensalada verde":     ( 3.5, 1.5,  1.5,  0.2,  20),
    "ensalada cesar":     ( 6.0, 1.5,  5.0, 12.0, 150),
    "mix proteico":       ( 5.0, 1.0, 20.0,  5.0, 145),
    "batido proteico":    ( 8.0, 1.0, 25.0,  3.0, 160),
    "smoothie":           (14.0, 1.5,  2.0,  0.5,  68),
    "bowl de açaí":       (28.0, 4.5,  3.5,  6.0, 175),
}


def _parse_cantidad(s: str):
    """
    Convierte un string numérico a float, soportando:
      "250"   → 250.0
      "0.5"   → 0.5
      "1/2"   → 0.5
      "3/4"   → 0.75
      "1 1/2" → 1.5
      "2,5"   → 2.5
    Devuelve None si no reconoce el formato.
    """
    s = s.strip().replace(',', '.')

    # Número entero + fracción: "1 1/2"
    m = re.match(r'^(\d+)\s+(\d+)/(\d+)$', s)
    if m:
        return int(m.group(1)) + int(m.group(2)) / int(m.group(3))

    # Fracción sola: "1/2", "3/4"
    m = re.match(r'^(\d+)/(\d+)$', s)
    if m:
        denom = int(m.group(2))
        return int(m.group(1)) / denom if denom else None

    # Decimal o entero: "250", "0.5"
    m = re.match(r'^\d+(?:\.\d+)?$', s)
    if m:
        return float(s)

    return None


def _parsear_nombre(nombre: str):
    """
    Extrae cantidad y nombre limpio de un string libre.
    Soporta:
      "250 g Carne"   → (250, True,  "carne")
      "Carne 250 g"   → (250, True,  "carne")
      "4 huevos"      → (4,   False, "huevos")
      "1/2 cebolla"   → (0.5, False, "cebolla")
      "1/2 taza arroz"→ (0.5, False, "arroz")   [taza = unidad ignorada]
      "3/4 de palta"  → (0.75,False, "palta")
      "1 1/2 banana"  → (1.5, False, "banana")
    Devuelve: (cantidad_o_None, es_gramos: bool, nombre_limpio: str)
    """
    s = nombre.strip()

    # Patrón numérico: entero, decimal, fracción o entero+fracción
    NUM_PAT  = r'(\d+(?:[.,]\d+)?|\d+\s+\d+/\d+|\d+/\d+)'
    UNIG_PAT = r'(gr?(?:amos?)?)'   # g / gr / gramos

    # ── "CANTIDAD [g] texto" al inicio ─────────────────────────────────
    m = re.match(r'^' + NUM_PAT + r'\s*' + UNIG_PAT + r'?\s+(.+)$', s, re.IGNORECASE)
    if m:
        cantidad      = _parse_cantidad(m.group(1))
        unidad_str    = (m.group(2) or '').strip()
        nombre_limpio = m.group(3).strip()
        # Quitar palabras de unidad/medida que no son el alimento
        nombre_limpio = re.sub(
            r'\b(taza|cucharada|cucharadita|porcion|porción|trozo|pedazo|'
            r'rodaja|rebanada|feta|sobre|paquete|lata|vaso|copa|de)\b',
            '', nombre_limpio, flags=re.IGNORECASE
        ).strip()
        return cantidad, bool(unidad_str), nombre_limpio.lower()

    # ── "texto CANTIDAD [g]" al final ──────────────────────────────────
    m = re.match(r'^(.+?)\s+' + NUM_PAT + r'\s*' + UNIG_PAT + r'?\s*$', s, re.IGNORECASE)
    if m:
        nombre_limpio = m.group(1).strip()
        cantidad      = _parse_cantidad(m.group(2))
        unidad_str    = (m.group(3) or '').strip()
        return cantidad, bool(unidad_str), nombre_limpio.lower()

    return None, False, s.lower()


# Palabras a ignorar en la búsqueda (conectores, preposiciones, artículos)
_STOP_WORDS = {
    "de", "del", "la", "el", "los", "las", "con", "sin", "y", "o",
    "en", "a", "al", "un", "una", "mix", "mezcla", "combo", "tipo",
    "estilo", "casero", "casera", "light", "diet", "natural",
}

def _palabras_clave(texto: str) -> list:
    """Devuelve palabras significativas (≥3 letras, sin stop words)."""
    return [w for w in texto.lower().split()
            if len(w) >= 3 and w not in _STOP_WORDS]


def buscar_nutricion(nombre: str):
    """
    Busca el alimento en la DB con múltiples estrategias.
    Devuelve (key, carbs_total, fibra, protein, fat, kcal) o None.
    """
    _, _, nombre_limpio = _parsear_nombre(nombre)
    variantes = list(dict.fromkeys([nombre_limpio, nombre.lower().strip()]))

    # Claves ordenadas de más larga a más corta → match específico primero
    keys_sorted = sorted(NUTRITION_DB.keys(), key=len, reverse=True)

    for buscar in variantes:
        buscar = buscar.strip()
        if not buscar:
            continue

        # 1. Coincidencia exacta
        if buscar in NUTRITION_DB:
            return (buscar,) + NUTRITION_DB[buscar]

        # 2. La clave está contenida en el texto buscado (más larga primero)
        for key in keys_sorted:
            if key in buscar:
                return (key,) + NUTRITION_DB[key]

        # 3. El texto buscado está contenido en la clave
        for key in keys_sorted:
            if buscar in key:
                return (key,) + NUTRITION_DB[key]

    # 4. Palabras clave: todas las palabras significativas del nombre
    #    deben aparecer en la clave (en cualquier orden)
    palabras = _palabras_clave(nombre_limpio)
    if palabras:
        for key, vals in NUTRITION_DB.items():
            if all(p in key for p in palabras):
                return (key,) + vals

    # 5. Al menos una palabra significativa coincide con parte de la clave
    #    (ordenado por cantidad de palabras que coinciden → mejor match primero)
    if palabras:
        candidatos = []
        for key, vals in NUTRITION_DB.items():
            score = sum(1 for p in palabras if p in key)
            if score > 0:
                candidatos.append((score, key, vals))
        if candidatos:
            candidatos.sort(reverse=True)
            _, key, vals = candidatos[0]
            return (key,) + vals

    # 6. Cualquier palabra de la clave aparece en el nombre
    for key, vals in NUTRITION_DB.items():
        for kword in _palabras_clave(key):
            if kword in nombre_limpio:
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
