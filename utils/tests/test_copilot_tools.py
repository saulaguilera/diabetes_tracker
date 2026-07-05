"""
Tests de utils/copilot_tools.py — helpers puros (sin DB ni Flask ni API).
"""
import os
import sys
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from utils.copilot_tools import (
    in_hour_window, detect_hypo_events, slice_stats, delta_after, run_tool,
)


def _series(start, mins_step, vals):
    times = [start + timedelta(minutes=mins_step * i) for i in range(len(vals))]
    return times, [float(v) for v in vals]


class TestInHourWindow(unittest.TestCase):
    def test_simple_range(self):
        dt = datetime(2026, 7, 1, 14, 0)
        self.assertTrue(in_hour_window(dt, 12, 18))
        self.assertFalse(in_hour_window(dt, 18, 24))

    def test_midnight_wrap(self):
        # franja 22→6 (noches): 23h y 3h adentro; 12h afuera
        self.assertTrue(in_hour_window(datetime(2026, 7, 1, 23, 0), 22, 6))
        self.assertTrue(in_hour_window(datetime(2026, 7, 1, 3, 0), 22, 6))
        self.assertFalse(in_hour_window(datetime(2026, 7, 1, 12, 0), 22, 6))

    def test_no_filter(self):
        self.assertTrue(in_hour_window(datetime(2026, 7, 1, 5, 0), None, None))


class TestDetectHypoEvents(unittest.TestCase):
    def test_single_event_as_streak(self):
        # una racha de 3 lecturas <70 = UN evento, no tres
        t, v = _series(datetime(2026, 7, 1, 3, 0), 5, [90, 68, 62, 65, 95, 100])
        ev = detect_hypo_events(t, v)
        self.assertEqual(len(ev), 1)
        self.assertEqual(ev[0]["min_v"], 62)
        self.assertEqual(ev[0]["n_readings"], 3)

    def test_two_separate_events(self):
        t, v = _series(datetime(2026, 7, 1, 3, 0), 5, [65, 90, 90, 66, 68, 90])
        self.assertEqual(len(detect_hypo_events(t, v)), 2)

    def test_no_events(self):
        t, v = _series(datetime(2026, 7, 1, 3, 0), 5, [90, 110, 120])
        self.assertEqual(detect_hypo_events(t, v), [])

    def test_event_at_end_not_lost(self):
        t, v = _series(datetime(2026, 7, 1, 3, 0), 5, [90, 90, 60])
        self.assertEqual(len(detect_hypo_events(t, v)), 1)


class TestSliceStats(unittest.TestCase):
    def setUp(self):
        # 2 días de lecturas cada 30 min, todas 100 salvo las de madrugada a 60
        base = datetime(2026, 6, 29, 0, 0)   # lunes
        self.times, self.values = [], []
        for i in range(96):
            t = base + timedelta(minutes=30 * i)
            self.times.append(t)
            self.values.append(60.0 if t.hour < 3 else 100.0)

    def test_full(self):
        s = slice_stats(self.times, self.values)
        self.assertEqual(s["lecturas"], 96)
        self.assertLess(s["tir_pct"], 100)

    def test_hour_filter_catches_lows(self):
        s = slice_stats(self.times, self.values, 0, 3)
        self.assertEqual(s["promedio"], 60)
        self.assertEqual(s["tir_pct"], 0)

    def test_weekday_filter(self):
        s_lunes = slice_stats(self.times, self.values, weekday=0)
        self.assertIsNotNone(s_lunes)
        s_viernes = slice_stats(self.times, self.values, weekday=4)
        self.assertIsNone(s_viernes)   # no hay viernes en la serie

    def test_too_few_readings(self):
        self.assertIsNone(slice_stats(self.times[:5], self.values[:5]))


class TestDeltaAfter(unittest.TestCase):
    def test_delta(self):
        t, v = _series(datetime(2026, 7, 1, 12, 0), 5, list(range(100, 148, 2)))
        d = delta_after(t, v, datetime(2026, 7, 1, 12, 0), 1)   # +2 cada 5 min → +24/h
        self.assertEqual(d, 24)

    def test_missing_baseline(self):
        t, v = _series(datetime(2026, 7, 1, 12, 0), 5, [100, 102])
        self.assertIsNone(delta_after(t, v, datetime(2026, 7, 1, 9, 0), 2))


class TestRunToolSafety(unittest.TestCase):
    def test_unknown_tool(self):
        r = run_tool("hackear_nasa", {})
        self.assertIn("error", r)

    def test_tool_error_never_raises(self):
        # sin app context de Flask, la query interna falla → dict de error, no excepción
        r = run_tool("hipos_recientes", {"days": 30})
        self.assertIsInstance(r, dict)


if __name__ == "__main__":
    unittest.main()
