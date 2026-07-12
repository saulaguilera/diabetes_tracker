"""
Tests de utils/glucose_curve.py — segmentación de la serie CGM en tramos.
Sin Flask, sin BD: funciones puras sobre [(datetime, valor)].
"""
import os
import sys
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from utils.glucose_curve import segmentos, pivotes, huecos, duracion_txt

T0 = datetime(2026, 7, 12, 7, 0)


def _serie(valores, paso_min=5, t0=T0):
    """Serie sintética: un valor cada `paso_min` minutos."""
    return [(t0 + timedelta(minutes=i * paso_min), float(v))
            for i, v in enumerate(valores)]


class TestSegmentos(unittest.TestCase):

    def test_subida_meseta_bajada(self):
        # 90 → 180 (subida), meseta, 180 → 95 (bajada) = 2 tramos
        vals = [90, 100, 115, 135, 160, 180] + [180] * 6 + [165, 140, 118, 100, 95]
        segs = segmentos(_serie(vals))
        self.assertEqual([s["tipo"] for s in segs], ["subida", "bajada"])
        # colas planas recortadas: el arranque puede perder el primer pasito
        self.assertLessEqual(int(segs[0]["v0"]), 115)
        self.assertEqual(int(segs[0]["v1"]), 180)
        self.assertLessEqual(int(segs[1]["v1"]), 105)
        # la meseta queda FUERA de ambos tramos: la subida termina en el
        # primer 180 y la bajada arranca en el último
        self.assertGreater(segs[1]["t0"], segs[0]["t1"])

    def test_ruido_no_corta_tramos(self):
        # subida de 90→190 con ruido de ±8 (menor al umbral 25) = UN tramo
        base = [90 + i * 5 for i in range(21)]
        vals = [v + (8 if i % 2 else -8) for i, v in enumerate(base)]
        segs = segmentos(_serie(vals))
        self.assertEqual(len(segs), 1)
        self.assertEqual(segs[0]["tipo"], "subida")

    def test_plano_sin_tramos(self):
        vals = [105 + (3 if i % 2 else -3) for i in range(40)]
        self.assertEqual(segmentos(_serie(vals)), [])

    def test_hueco_de_sensor_parte_la_serie(self):
        # 1h de datos, hueco de 3h, 1h de datos: el salto 100→200 dentro del
        # hueco NO debe narrarse como subida vista
        a = _serie([100] * 12)                                   # 07:00-07:55
        b = _serie([200] * 12, t0=T0 + timedelta(hours=4))       # 11:00-11:55
        segs = segmentos(a + b)
        self.assertEqual(segs, [])
        gs = huecos(a + b)
        self.assertEqual(len(gs), 1)
        self.assertEqual(gs[0][0], a[-1][0])

    def test_deriva_lenta(self):
        # +45 mg/dL a lo largo de 4 horas (~0.19/min) → deriva, no subida
        vals = [100 + i * 0.94 for i in range(48)]
        segs = segmentos(_serie(vals))
        self.assertEqual(len(segs), 1)
        self.assertEqual(segs[0]["tipo"], "deriva")

    def test_vacia_y_corta(self):
        self.assertEqual(segmentos([]), [])
        self.assertEqual(segmentos(_serie([100])), [])

    def test_noche_plana_no_se_fusiona_con_la_subida(self):
        # 6h plano ~100 (ruido ±4) y subida 98→196 en 50 min: el tramo debe
        # empezar cerca del arranque real de la subida, no a medianoche
        import math
        plano = [100 + 4 * math.sin(i / 3) for i in range(72)]   # 6 h
        subida = [98 + (196 - 98) * i / 10 for i in range(11)]   # 50 min
        segs = segmentos(_serie(plano + subida))
        self.assertEqual(len(segs), 1)
        self.assertEqual(segs[0]["tipo"], "subida")
        # arranca dentro de los últimos 40 min del plano o ya en la subida
        inicio_real = T0 + timedelta(minutes=72 * 5)
        self.assertGreaterEqual(segs[0]["t0"], inicio_real - timedelta(minutes=40))
        # y su duración narrada es la de la subida, no 7 horas
        self.assertLess(segs[0]["minutos"], 100)


class TestHelpers(unittest.TestCase):

    def test_pivotes_v(self):
        # V perfecta: 3 pivotes (inicio, valle, fin)
        vals = [180, 150, 120, 90, 120, 150, 180]
        pvs = pivotes(_serie(vals))
        self.assertEqual(len(pvs), 3)
        self.assertEqual(pvs[1][1], 90)

    def test_duracion_txt(self):
        self.assertEqual(duracion_txt(95), "95 min")
        self.assertEqual(duracion_txt(150), "2 h 30 min")
        self.assertEqual(duracion_txt(120), "2 h")


if __name__ == "__main__":
    unittest.main()
