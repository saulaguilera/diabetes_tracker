"""
Tests de utils/agp.py — cómputo puro (bandas, percentiles, métricas).
"""
import os
import sys
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from utils.agp import band_pcts, agp_percentiles, agp_metrics


class TestBandPcts(unittest.TestCase):
    def test_standard_bands(self):
        vals = [50, 60, 100, 150, 200, 300]   # una por banda + extra en rango
        bands = dict((label, pct) for label, pct, _ in band_pcts(vals))
        self.assertAlmostEqual(bands["muy bajo (<54)"], 16.7, delta=0.1)
        self.assertAlmostEqual(bands["bajo (54-69)"], 16.7, delta=0.1)
        self.assertAlmostEqual(bands["en rango (70-180)"], 33.3, delta=0.1)
        self.assertAlmostEqual(sum(p for _, p, _ in band_pcts(vals)), 100.0, delta=0.3)

    def test_empty(self):
        self.assertTrue(all(p == 0 for _, p, _ in band_pcts([])))

    def test_boundaries(self):
        # 70 es EN RANGO, 181 es ALTO, 54 es BAJO (bordes del consenso)
        bands = {label: pct for label, pct, _ in band_pcts([70, 181, 54])}
        self.assertAlmostEqual(bands["en rango (70-180)"], 33.3, delta=0.1)
        self.assertAlmostEqual(bands["alto (181-250)"], 33.3, delta=0.1)
        self.assertAlmostEqual(bands["bajo (54-69)"], 33.3, delta=0.1)


class TestAgpPercentiles(unittest.TestCase):
    def _series(self, days=14):
        # 14 días de lecturas cada 10 min: seno diario 100±30 (pico 15:00)
        import math
        base = datetime(2026, 6, 20, 0, 0)
        times, values = [], []
        for d in range(days):
            for m in range(0, 24 * 60, 10):
                t = base + timedelta(days=d, minutes=m)
                times.append(t)
                values.append(100 + 30 * math.sin(2 * math.pi * (m / 60 - 9) / 24))
        return times, values

    def test_shapes_and_ordering(self):
        p = agp_percentiles(*self._series())
        self.assertIsNotNone(p)
        self.assertEqual(len(p[50]), 48)
        # percentiles ordenados en todos los bins
        for i in range(48):
            self.assertLessEqual(p[5][i], p[25][i] + 1e-6)
            self.assertLessEqual(p[25][i], p[50][i] + 1e-6)
            self.assertLessEqual(p[50][i], p[75][i] + 1e-6)
            self.assertLessEqual(p[75][i], p[95][i] + 1e-6)

    def test_median_follows_daily_pattern(self):
        p = agp_percentiles(*self._series())
        # el seno pica a las 15h y toca fondo a las 3h
        hora_max = float(p["hours"][int(p[50].argmax())])
        hora_min = float(p["hours"][int(p[50].argmin())])
        self.assertAlmostEqual(hora_max, 15, delta=1.5)
        self.assertAlmostEqual(hora_min, 3, delta=1.5)

    def test_insufficient_data(self):
        t0 = datetime(2026, 7, 1, 12, 0)
        self.assertIsNone(agp_percentiles([t0], [100.0]))


class TestAgpMetrics(unittest.TestCase):
    def test_metrics(self):
        t0 = datetime(2026, 7, 1)
        times = [t0 + timedelta(minutes=5 * i) for i in range(288 * 2)]  # 2 días completos
        values = [100.0] * len(times)
        m = agp_metrics(times, values, days=2)
        self.assertEqual(m["promedio"], 100)
        self.assertEqual(m["dias_con_datos"], 2)   # 48h exactas = 2 fechas
        self.assertAlmostEqual(m["gmi"], 5.7, delta=0.1)
        self.assertEqual(m["cv"], 0.0)
        self.assertGreaterEqual(m["sensor_activo_pct"], 99.0)

    def test_empty(self):
        self.assertEqual(agp_metrics([], [], 14), {"n": 0})


if __name__ == "__main__":
    unittest.main()
