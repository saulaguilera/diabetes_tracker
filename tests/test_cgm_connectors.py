"""Conectores CGM: mapeo al contrato interno, sin tocar la red."""
import os, sys, unittest
from unittest import mock
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from utils import cgm_connectors as cc


class TestNightscout(unittest.TestCase):
    def test_mapea_entries_y_treatments(self):
        entries = [
            {"sgv": 120, "date": 1760000000000, "direction": "Flat"},
            {"sgv": 85,  "date": 1760000300000, "direction": "FortyFiveDown"},
            {"sgv": 0,   "date": 1760000600000, "direction": "Flat"},   # inválida
        ]
        treatments = [
            {"insulin": 2.5, "created_at": "2026-07-12T12:00:00Z"},
            {"carbs": 30, "created_at": "2026-07-12T12:05:00Z"},        # sin insulina
        ]
        def fake_get(url, token, path, params):
            return entries if path.startswith("entries") else treatments
        with mock.patch.object(cc, "_ns_get", fake_get):
            r = cc._fetch_nightscout("https://demo.ns.com", "tok")
        self.assertIsNone(r["error"])
        self.assertEqual(len(r["readings"]), 2)          # la inválida se descarta
        self.assertEqual(r["readings"][0]["value_mgdl"], 120)
        self.assertEqual(r["readings"][1]["trend"], "↘")
        self.assertEqual(len(r["treatments"]), 1)        # solo la que tiene insulina
        self.assertEqual(r["treatments"][0]["units"], 2.5)
        self.assertEqual(r["treatments"][0]["kind"], "bolus")

    def test_orden_cronologico(self):
        entries = [
            {"sgv": 100, "date": 1760000600000, "direction": "Flat"},
            {"sgv": 90,  "date": 1760000000000, "direction": "Flat"},
        ]
        with mock.patch.object(cc, "_ns_get", lambda *a: entries if a[2].startswith("entries") else []):
            r = cc._fetch_nightscout("x.com", "")
        self.assertLess(r["readings"][0]["timestamp"], r["readings"][1]["timestamp"])


class TestDispatcher(unittest.TestCase):
    def test_error_no_explota(self):
        # proveedor válido con red rota → error prolijo, jamás excepción
        with mock.patch.object(cc, "_fetch_nightscout", side_effect=RuntimeError("boom")):
            r = cc.fetch("nightscout", "x.com", "")
        self.assertEqual(r["readings"], [])
        self.assertIn("boom", r["error"])

    def test_providers(self):
        self.assertEqual(set(cc.PROVIDERS), {"libre", "dexcom", "nightscout"})


if __name__ == "__main__":
    unittest.main()
