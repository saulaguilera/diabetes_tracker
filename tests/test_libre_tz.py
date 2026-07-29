"""Regresión del bug de viaje (2026-07-29): LibreLinkUp entrega "Timestamp"
en la hora local del TELÉFONO. Al viajar (+2h de desfase) todas las lecturas
quedaban 2h atrás en el server → la app decía "sensor offline hace 121 min"
a cada rato y el brief matutino salía degradado. El fix: preferir
FactoryTimestamp (UTC del sensor) convertido a la hora local del servidor.
"""
import os
import time
import unittest
from datetime import datetime, timezone

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from utils.libre_linkup import _parse_reading


def _fijar_tz(tz):
    os.environ["TZ"] = tz
    time.tzset()


class TestTimestampsDeViaje(unittest.TestCase):
    def setUp(self):
        self._tz_original = os.environ.get("TZ", "America/Santiago")
        _fijar_tz("America/Santiago")   # TZ del producto (como en Railway)

    def tearDown(self):
        _fijar_tz(self._tz_original)

    def test_prefiere_factory_utc_sobre_hora_del_telefono(self):
        # 15:00 UTC = 11:00 en Chile (invierno, UTC-4). El teléfono viajando
        # en CDMX (UTC-6) reporta Timestamp 09:00 — debe ser IGNORADO.
        r = _parse_reading({
            "FactoryTimestamp": "7/29/2026 3:00:00 PM",
            "Timestamp": "7/29/2026 9:00:00 AM",
            "ValueInMgPerDl": 120, "TrendArrow": 4,
        })
        self.assertEqual(r["timestamp"], datetime(2026, 7, 29, 11, 0, 0))

    def test_mismo_factory_distinto_telefono_da_misma_hora(self):
        # La hora guardada no puede depender de dónde esté el teléfono
        base = {"FactoryTimestamp": "7/29/2026 3:00:00 PM",
                "ValueInMgPerDl": 100, "TrendArrow": 4}
        en_chile = _parse_reading({**base, "Timestamp": "7/29/2026 11:00:00 AM"})
        de_viaje = _parse_reading({**base, "Timestamp": "7/29/2026 9:00:00 AM"})
        self.assertEqual(en_chile["timestamp"], de_viaje["timestamp"])

    def test_sin_factory_cae_al_timestamp_local(self):
        r = _parse_reading({"Timestamp": "7/29/2026 11:05:00 AM",
                            "ValueInMgPerDl": 95, "TrendArrow": 4})
        self.assertEqual(r["timestamp"], datetime(2026, 7, 29, 11, 5, 0))

    def test_factory_invalido_no_rompe(self):
        r = _parse_reading({"FactoryTimestamp": "no-es-fecha",
                            "Timestamp": "7/29/2026 11:05:00 AM",
                            "ValueInMgPerDl": 95, "TrendArrow": 4})
        self.assertEqual(r["timestamp"], datetime(2026, 7, 29, 11, 5, 0))


if __name__ == "__main__":
    unittest.main()
