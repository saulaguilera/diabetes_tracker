"""
Tests de utils/copilot_memory.py — solo los helpers puros (sin DB ni Flask).
"""
import os
import sys
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from utils.copilot_memory import (
    normalize_meal_name, reading_near, median, extract_remember_request,
)


class TestNormalizeMealName(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(normalize_meal_name("  Pizza Muzzarella "), "pizza muzzarella")

    def test_accents(self):
        self.assertEqual(normalize_meal_name("Puré de papá"), "pure de papa")

    def test_collapses_spaces(self):
        self.assertEqual(normalize_meal_name("arroz   con  pollo"), "arroz con pollo")

    def test_empty(self):
        self.assertEqual(normalize_meal_name(None), "")
        self.assertEqual(normalize_meal_name("   "), "")


class TestReadingNear(unittest.TestCase):
    def setUp(self):
        base = datetime(2026, 7, 1, 12, 0)
        self.times = [base + timedelta(minutes=5 * i) for i in range(24)]  # 12:00–13:55
        self.values = [100.0 + i for i in range(24)]

    def test_exact_match(self):
        t = datetime(2026, 7, 1, 12, 30)
        self.assertEqual(reading_near(self.times, self.values, t), 106.0)

    def test_nearest_between(self):
        t = datetime(2026, 7, 1, 12, 32)   # más cerca de 12:30 que de 12:35
        self.assertEqual(reading_near(self.times, self.values, t), 106.0)

    def test_outside_tolerance(self):
        t = datetime(2026, 7, 1, 15, 0)    # >25 min de la última lectura
        self.assertIsNone(reading_near(self.times, self.values, t))

    def test_empty(self):
        self.assertIsNone(reading_near([], [], datetime.now()))


class TestMedian(unittest.TestCase):
    def test_odd(self):
        self.assertEqual(median([3, 1, 2]), 2)

    def test_even(self):
        self.assertEqual(median([1, 2, 3, 4]), 2.5)

    def test_empty(self):
        self.assertIsNone(median([]))


class TestExtractRememberRequest(unittest.TestCase):
    def test_recorda_que(self):
        self.assertEqual(
            extract_remember_request("recordá que la pizza me sube de noche"),
            "la pizza me sube de noche")

    def test_recuerda(self):
        self.assertEqual(
            extract_remember_request("Recuerda que los lunes entreno fútbol."),
            "los lunes entreno fútbol")

    def test_acordate_de(self):
        self.assertEqual(
            extract_remember_request("acordate de que ceno tarde los viernes"),
            "ceno tarde los viernes")

    def test_no_match(self):
        self.assertIsNone(extract_remember_request("¿cómo vengo hoy?"))
        self.assertIsNone(extract_remember_request(""))


if __name__ == "__main__":
    unittest.main()
