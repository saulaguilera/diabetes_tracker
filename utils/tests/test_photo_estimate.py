"""
Tests de utils/photo_estimate.py — parsing, grounding y totales (sin API).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from utils.photo_estimate import (
    build_prompt, parse_response, ground_components, totals,
)


class TestBuildPrompt(unittest.TestCase):
    def test_no_hint(self):
        p = build_prompt("")
        self.assertIn("PASO A PASO", p)
        self.assertNotIn("PISTA DEL USUARIO", p)
        self.assertIn('{"name"', p)          # el JSON de ejemplo quedó bien formateado

    def test_with_hint(self):
        p = build_prompt("pastel de papa")
        self.assertIn("PISTA DEL USUARIO", p)
        self.assertIn("pastel de papa", p)

    def test_hint_truncated(self):
        p = build_prompt("x" * 500)
        self.assertNotIn("x" * 200, p)


class TestParseResponse(unittest.TestCase):
    def test_json_with_reasoning_prefix(self):
        # el modelo razona antes del JSON → igual se extrae
        txt = ('Veo arroz y pollo. El plato mide unos 26cm...\n'
               '{"name": "Arroz con pollo", "confidence": "alta", '
               '"components": [{"name": "arroz blanco", "grams": 150, "carbs": 42, '
               '"fiber": 1, "protein": 4, "fat": 0, "calories": 195}]}')
        r = parse_response(txt)
        self.assertEqual(r["name"], "Arroz con pollo")
        self.assertEqual(len(r["components"]), 1)

    def test_no_json(self):
        self.assertIsNone(parse_response("no puedo ver la imagen"))
        self.assertIsNone(parse_response(""))


class TestGroundComponents(unittest.TestCase):
    def test_known_food_uses_db(self):
        # "garbanzo" está en NUTRITION_DB (27.4g CH/100g) → 200g ≈ 55g CH,
        # aunque la IA haya dicho 90 (sobreestimación típica)
        comps = ground_components([{"name": "garbanzo", "grams": 200, "carbs": 90,
                                    "fiber": 2, "protein": 5, "fat": 1, "calories": 400}])
        self.assertEqual(comps[0]["source"], "base")
        self.assertAlmostEqual(comps[0]["carbs"], 55, delta=2)

    def test_unknown_food_keeps_ai(self):
        comps = ground_components([{"name": "xyzzy plato inventado 9000", "grams": 100,
                                    "carbs": 33, "fiber": 1, "protein": 2, "fat": 3,
                                    "calories": 150}])
        self.assertEqual(comps[0]["source"], "ia")
        self.assertEqual(comps[0]["carbs"], 33)

    def test_zero_grams_not_grounded(self):
        comps = ground_components([{"name": "garbanzo", "grams": 0, "carbs": 10}])
        self.assertEqual(comps[0]["source"], "ia")

    def test_empty(self):
        self.assertEqual(ground_components([]), [])
        self.assertEqual(ground_components(None), [])


class TestTotals(unittest.TestCase):
    def test_sums(self):
        # nombres de 1 letra: NO se anclan a la base (guard) → quedan los de la IA
        comps = ground_components([
            {"name": "a", "grams": 10, "carbs": 10, "fiber": 1, "protein": 2, "fat": 3, "calories": 50},
            {"name": "b", "grams": 10, "carbs": 5, "fiber": 0, "protein": 1, "fat": 1, "calories": 30},
        ])
        self.assertTrue(all(c["source"] == "ia" for c in comps))
        t = totals(comps)
        self.assertEqual(t["carbs"], 15)
        self.assertEqual(t["calories"], 80)

    def test_short_names_not_grounded(self):
        comps = ground_components([{"name": "aj", "grams": 100, "carbs": 7}])
        self.assertEqual(comps[0]["source"], "ia")


if __name__ == "__main__":
    unittest.main()
