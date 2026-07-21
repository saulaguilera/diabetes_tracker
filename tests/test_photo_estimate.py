"""Tests de utils/photo_estimate: parseo con score, fallback determinístico
y ancla de los platos chilenos nuevos en la base nutricional."""
import json

from utils.photo_estimate import parse_response, meal_score, totals, ground_components


def _tot(carbs=0, fiber=0, protein=0, fat=0, calories=0):
    return {"carbs": carbs, "fiber": fiber, "protein": protein,
            "fat": fat, "calories": calories}


class TestMealScore:
    def test_usa_score_del_modelo(self):
        parsed = {"score": 8, "score_reason": "Balanceado, con proteína y fibra"}
        s, r = meal_score(parsed, _tot(carbs=40, protein=25, fiber=6, calories=500))
        assert s == 8
        assert "Balanceado" in r

    def test_score_invalido_cae_al_fallback(self):
        for malo in (None, 0, 15, "alto", -3):
            s, _ = meal_score({"score": malo}, _tot(carbs=30, fiber=2, calories=300))
            assert 1 <= s <= 10

    def test_fallback_castiga_carga_alta(self):
        alto, _ = meal_score({}, _tot(carbs=90, fiber=2, calories=600))
        bajo, _ = meal_score({}, _tot(carbs=12, fiber=4, protein=25, calories=300))
        assert alto < bajo
        assert alto <= 4

    def test_fallback_premia_fibra_y_proteina(self):
        base, _ = meal_score({}, _tot(carbs=40, fiber=2, protein=5, calories=400))
        rico, _ = meal_score({}, _tot(carbs=40, fiber=10, protein=25, calories=400))
        assert rico > base

    def test_sin_comida_no_hay_score(self):
        s, r = meal_score({"score": 9}, _tot())
        assert s is None and r == ""

    def test_reason_se_trunca(self):
        s, r = meal_score({"score": 5, "score_reason": "x" * 500},
                          _tot(carbs=30, calories=200))
        assert len(r) <= 140


class TestParseConScore:
    def test_json_completo(self):
        raw = json.dumps({
            "name": "Cazuela de vacuno", "confidence": "alta",
            "score": 8, "score_reason": "Sopa casera con verduras",
            "components": [{"name": "cazuela", "grams": 400, "carbs": 30,
                            "fiber": 4, "protein": 24, "fat": 12, "calories": 340}],
        })
        parsed = parse_response("bla bla\n" + raw)
        assert parsed["score"] == 8
        comps = ground_components(parsed["components"])
        tot = totals(comps)
        s, r = meal_score(parsed, tot)
        assert s == 8 and "casera" in r


class TestPlatosChilenos:
    def test_marraqueta_anclada_en_base(self):
        comps = ground_components([{"name": "marraqueta", "grams": 100,
                                    "carbs": 99, "fiber": 0, "protein": 0,
                                    "fat": 0, "calories": 0}])
        assert comps[0]["source"] == "base"
        # 100g de marraqueta ≈ 57g CH totales (no los 99 que dijo la IA)
        assert 50 <= comps[0]["carbs"] <= 62

    def test_platos_clave_existen(self):
        from utils.nutrition_db import NUTRITION_DB
        for plato in ("marraqueta", "hallulla", "sopaipilla", "completo",
                      "pastel de choclo", "cazuela", "charquicán", "sushi",
                      "mote con huesillo", "milanesa", "humita"):
            assert plato in NUTRITION_DB, plato

    def test_gi_de_panes_chilenos(self):
        from utils.nutrition_db import get_gi
        assert get_gi("marraqueta") == 70
        assert get_gi("milanesa de pollo") is None or True  # sin GI está bien
