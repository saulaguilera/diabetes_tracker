"""El 429 de Cloudflare en /graph no puede dejar a un usuario a oscuras:
la lectura actual del payload de /connections lo mantiene vivo."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import requests
from utils import libre_linkup as llu


class TestGraphFallback(unittest.TestCase):
    def _sync(self, graph_exc, patient):
        with mock.patch.object(llu, "get_cached_token", return_value=("tok", "https://api.x")), \
             mock.patch.object(llu, "get_connections", return_value=[patient]), \
             mock.patch.object(llu, "get_readings", side_effect=graph_exc):
            return llu.sync_all("e@x.com", "pw",
                                get_setting_fn=lambda k: {"libre_account_id": "acc"}.get(k, ""),
                                set_setting_fn=lambda k, v: None)

    def test_graph_429_rescata_lectura_actual_de_connections(self):
        patient = {"patientId": "p1", "glucoseMeasurement": {
            "FactoryTimestamp": "9/4/2026 2:14:07 AM",
            "ValueInMgPerDl": 211, "TrendArrow": 3}}
        r = self._sync(requests.RequestException("429 Client Error"), patient)
        self.assertIsNone(r["error"], r)
        self.assertEqual(len(r["readings"]), 1)
        self.assertEqual(r["readings"][0]["value_mgdl"], 211)

    def test_graph_429_sin_lectura_actual_sigue_reportando_429(self):
        r = self._sync(requests.RequestException("429 Client Error"),
                       {"patientId": "p1"})
        self.assertIn("429", r["error"] or "")

    def test_error_no_429_no_se_traga(self):
        patient = {"patientId": "p1", "glucoseMeasurement": {
            "FactoryTimestamp": "9/4/2026 2:14:07 AM", "ValueInMgPerDl": 100}}
        r = self._sync(requests.RequestException("500 Server Error"), patient)
        self.assertIn("500", r["error"] or "")


if __name__ == "__main__":
    unittest.main()
