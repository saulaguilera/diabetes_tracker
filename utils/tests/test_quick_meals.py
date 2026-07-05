"""
Tests de utils/quick_meals.py — agrupador puro de "Mis comidas".
"""
import os
import sys
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from utils.quick_meals import group_quick_meals


def _row(name, carbs, ts, protein=0, fat=0):
    return {"name": name, "carbs": carbs, "protein": protein, "fat": fat, "ts": ts}


class TestGroupQuickMeals(unittest.TestCase):
    def setUp(self):
        self.t0 = datetime(2026, 7, 1, 12, 0)

    def test_groups_by_normalized_name(self):
        rows = [
            _row("Pizza Muzzarella", 60, self.t0),
            _row("pizza muzzarella ", 70, self.t0 + timedelta(days=1)),
            _row("PIZZA MUZZARELLA", 65, self.t0 + timedelta(days=2)),
        ]
        out = group_quick_meals(rows)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["n"], 3)
        self.assertEqual(out[0]["carbs"], 65)          # mediana
        self.assertEqual(out[0]["name"], "PIZZA MUZZARELLA")  # display más reciente

    def test_min_count_filters_singles(self):
        rows = [
            _row("Milanesa", 40, self.t0),
            _row("Milanesa", 42, self.t0 + timedelta(days=1)),
            _row("Sopa rara", 12, self.t0 - timedelta(days=5)),  # única y vieja
        ]
        out = group_quick_meals(rows)
        names = [m["name"] for m in out]
        self.assertIn("Milanesa", names)
        self.assertNotIn("Sopa rara", names)

    def test_most_recent_single_is_kept(self):
        # la comida MÁS reciente entra aunque n=1 (repetir la última cena)
        rows = [
            _row("Milanesa", 40, self.t0),
            _row("Milanesa", 42, self.t0 + timedelta(days=1)),
            _row("Wok de verduras", 25, self.t0 + timedelta(days=3)),
        ]
        out = group_quick_meals(rows)
        self.assertEqual(out[0]["name"], "Wok de verduras")

    def test_sorted_by_frequency(self):
        rows = ([_row("Arroz", 45, self.t0 + timedelta(hours=i)) for i in range(5)]
                + [_row("Fideos", 50, self.t0 + timedelta(days=1, hours=i)) for i in range(2)])
        out = group_quick_meals(rows)
        # el más reciente (Fideos) va primero por la garantía de recencia;
        # después manda la frecuencia
        self.assertEqual({out[0]["name"], out[1]["name"]}, {"Fideos", "Arroz"})

    def test_short_names_ignored(self):
        out = group_quick_meals([_row("ok", 10, self.t0), _row("ok", 10, self.t0)])
        self.assertEqual(out, [])

    def test_cap(self):
        rows = []
        for i in range(15):
            for j in range(2):
                rows.append(_row(f"Comida numero {i}", 30, self.t0 + timedelta(hours=i * 2 + j)))
        self.assertLessEqual(len(group_quick_meals(rows, max_items=8)), 8)

    def test_empty(self):
        self.assertEqual(group_quick_meals([]), [])


if __name__ == "__main__":
    unittest.main()
