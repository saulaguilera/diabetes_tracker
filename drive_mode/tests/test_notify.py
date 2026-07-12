"""
Tests de drive_mode/notify.py y fcm_push.py — sin red, sin Flask, sin credenciales.

Cubre:
  1. FCM sin FCM_SERVICE_ACCOUNT_JSON → no-op total ({"ok": False, "disabled"}).
  2. El service account acepta JSON crudo y base64; rechaza basura e incompletos.
  3. El despachador (notify.push_alert) combina APNs + FCM: ok si al menos
     uno llegó, y jamás propaga excepciones de un backend al caller.
"""
import base64
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from drive_mode import fcm_push, notify


SA = {"client_email": "svc@test.iam.gserviceaccount.com",
      "private_key": "-----BEGIN PRIVATE KEY-----\nx\n-----END PRIVATE KEY-----\n",
      "project_id": "orbit-test"}


class TestFcmDisabled(unittest.TestCase):
    def test_sin_env_es_noop(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("FCM_SERVICE_ACCOUNT_JSON", None)
            r = fcm_push.push_alert_fcm("t", "b")
        self.assertFalse(r["ok"])
        self.assertEqual(r["reason"], "disabled")


class TestServiceAccountParsing(unittest.TestCase):
    def test_json_crudo(self):
        with mock.patch.dict(os.environ,
                             {"FCM_SERVICE_ACCOUNT_JSON": json.dumps(SA)}):
            self.assertEqual(fcm_push._service_account()["project_id"], "orbit-test")

    def test_base64(self):
        b64 = base64.b64encode(json.dumps(SA).encode()).decode()
        with mock.patch.dict(os.environ, {"FCM_SERVICE_ACCOUNT_JSON": b64}):
            self.assertEqual(fcm_push._service_account()["client_email"],
                             SA["client_email"])

    def test_basura(self):
        with mock.patch.dict(os.environ, {"FCM_SERVICE_ACCOUNT_JSON": "ni json ni base64 ~~"}):
            self.assertIsNone(fcm_push._service_account())

    def test_incompleto(self):
        sa = {"client_email": "x@y.z"}   # sin private_key ni project_id
        with mock.patch.dict(os.environ, {"FCM_SERVICE_ACCOUNT_JSON": json.dumps(sa)}):
            self.assertIsNone(fcm_push._service_account())


class TestNotifyDispatch(unittest.TestCase):
    def _run(self, apns, fcm):
        with mock.patch("drive_mode.apns_push.push_alert", return_value=apns), \
             mock.patch("drive_mode.fcm_push.push_alert_fcm", return_value=fcm):
            return notify.push_alert("t", "b")

    def test_ok_si_solo_apns(self):
        r = self._run({"ok": True}, {"ok": False, "reason": "no_token"})
        self.assertTrue(r["ok"])
        self.assertTrue(r["apns"]["ok"])
        self.assertFalse(r["fcm"]["ok"])

    def test_ok_si_solo_fcm(self):
        r = self._run({"ok": False, "reason": "no_token"}, {"ok": True})
        self.assertTrue(r["ok"])

    def test_ambos_fallan(self):
        r = self._run({"ok": False, "reason": "disabled"},
                      {"ok": False, "reason": "disabled"})
        self.assertFalse(r["ok"])

    def test_excepcion_de_un_backend_no_rompe(self):
        with mock.patch("drive_mode.apns_push.push_alert",
                        side_effect=RuntimeError("boom")), \
             mock.patch("drive_mode.fcm_push.push_alert_fcm",
                        return_value={"ok": True}):
            r = notify.push_alert("t", "b")
        self.assertTrue(r["ok"])
        self.assertIn("error", r["apns"]["reason"])


if __name__ == "__main__":
    unittest.main()
