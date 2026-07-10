"""
Tests de drive_mode/apns_push.py — sin red, sin Flask, sin clave real.

Cubre:
  1. Flag OFF (default) → push_drive_update es no-op total.
  2. build_content_state espeja OrbitDriveActivityAttributes.ContentState.
  3. Datos no confiables nunca producen un número engañoso.
  4. El content-state NUNCA contiene campos prohibidos (insulina/IOB/COB/predicción).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from drive_mode.apns_push import build_content_state, push_drive_update, _load_private_key

# Claves exactas del ContentState de Swift (Codable) — el contrato con ActivityKit.
SWIFT_CONTENT_STATE_KEYS = {
    "glucoseValueMgdl", "trendArrow", "status", "statusLevel", "safetyMessage",
    "minutesSinceUpdate", "sensorName", "sensorConnected", "staleData", "updatedText",
}

FORBIDDEN_SUBSTRINGS = ("insulin", "dose", "bolus", "iob", "cob", "predict", "forecast")


def _payload_stable():
    return {
        "value": 112, "unit": "mg/dL", "trend_arrow": "→", "trend": "flat",
        "status": "stable", "level": "normal", "tint": "positive",
        "message": "Stable", "minutes_since_update": 2,
        "updated_text": "Updated 2 min ago", "sensor": "Libre 3",
        "connected": True, "stale": False, "brand": "ORBIT",
    }


def _payload_disconnected():
    return {
        "value": "--", "unit": "mg/dL", "trend_arrow": "—", "trend": "unknown",
        "status": "disconnected", "level": "unavailable", "tint": "muted",
        "message": "Sensor disconnected", "minutes_since_update": None,
        "updated_text": "No data", "sensor": "Libre 3",
        "connected": False, "stale": True, "brand": "ORBIT",
    }


class TestFlagOff(unittest.TestCase):
    def test_disabled_by_default(self):
        os.environ.pop("DRIVE_APNS_ENABLED", None)
        r = push_drive_update()
        self.assertFalse(r["ok"])
        self.assertEqual(r["reason"], "disabled")

    def test_disabled_explicit_zero(self):
        os.environ["DRIVE_APNS_ENABLED"] = "0"
        try:
            r = push_drive_update()
            self.assertEqual(r["reason"], "disabled")
        finally:
            os.environ.pop("DRIVE_APNS_ENABLED", None)


class TestContentState(unittest.TestCase):
    def test_keys_match_swift_contract(self):
        cs = build_content_state(_payload_stable())
        self.assertEqual(set(cs.keys()), SWIFT_CONTENT_STATE_KEYS)

    def test_stable_mapping(self):
        cs = build_content_state(_payload_stable())
        self.assertEqual(cs["glucoseValueMgdl"], 112)
        self.assertEqual(cs["trendArrow"], "→")
        self.assertEqual(cs["status"], "stable")
        self.assertEqual(cs["statusLevel"], "normal")
        self.assertEqual(cs["safetyMessage"], "Stable")
        self.assertTrue(cs["sensorConnected"])
        self.assertFalse(cs["staleData"])

    def test_disconnected_value_is_none_not_string(self):
        # "--" jamás debe viajar como número; el widget muestra "--" con nil.
        cs = build_content_state(_payload_disconnected())
        self.assertIsNone(cs["glucoseValueMgdl"])
        self.assertEqual(cs["statusLevel"], "unavailable")
        self.assertTrue(cs["staleData"])
        self.assertFalse(cs["sensorConnected"])

    def test_missing_fields_fail_safe(self):
        # Payload vacío → estado "no confiable", nunca "stable".
        cs = build_content_state({})
        self.assertIsNone(cs["glucoseValueMgdl"])
        self.assertEqual(cs["status"], "disconnected")
        self.assertEqual(cs["statusLevel"], "unavailable")
        self.assertTrue(cs["staleData"])

    def test_no_forbidden_fields(self):
        for payload in (_payload_stable(), _payload_disconnected()):
            cs = build_content_state(payload)
            for key in cs:
                for bad in FORBIDDEN_SUBSTRINGS:
                    self.assertNotIn(bad, key.lower(),
                                     f"Campo prohibido en content-state: {key}")


class TestKeyLoading(unittest.TestCase):
    def test_no_key_returns_none(self):
        os.environ.pop("APNS_KEY_P8", None)
        self.assertIsNone(_load_private_key())

    def test_pem_with_escaped_newlines(self):
        os.environ["APNS_KEY_P8"] = (
            "-----BEGIN PRIVATE KEY-----\\nMIGT...fake...\\n-----END PRIVATE KEY-----"
        )
        try:
            pem = _load_private_key()
            self.assertIn("-----BEGIN PRIVATE KEY-----\n", pem)
        finally:
            os.environ.pop("APNS_KEY_P8", None)

    def test_base64_pem(self):
        import base64
        raw = "-----BEGIN PRIVATE KEY-----\nMIGT...fake...\n-----END PRIVATE KEY-----"
        os.environ["APNS_KEY_P8"] = base64.b64encode(raw.encode()).decode()
        try:
            self.assertEqual(_load_private_key(), raw)
        finally:
            os.environ.pop("APNS_KEY_P8", None)

    def test_garbage_returns_none(self):
        os.environ["APNS_KEY_P8"] = "esto-no-es-una-clave"
        try:
            self.assertIsNone(_load_private_key())
        finally:
            os.environ.pop("APNS_KEY_P8", None)




class TestPushAlert(unittest.TestCase):
    """push_alert (notificaciones normales) — mismos candados que el drive push."""

    def test_disabled_by_default(self):
        os.environ.pop("DRIVE_APNS_ENABLED", None)
        from drive_mode.apns_push import push_alert
        r = push_alert("t", "b")
        self.assertFalse(r["ok"])
        self.assertEqual(r["reason"], "disabled")

    def test_bundle_topic_strips_liveactivity_suffix(self):
        from drive_mode.apns_push import _bundle_topic
        os.environ.pop("APNS_TOPIC", None)
        self.assertEqual(_bundle_topic(), "com.saulaguilera.orbit")
        os.environ["APNS_TOPIC"] = "com.x.y.push-type.liveactivity"
        try:
            self.assertEqual(_bundle_topic(), "com.x.y")
        finally:
            os.environ.pop("APNS_TOPIC", None)

    def test_enabled_without_token_is_noop(self):
        # flag ON pero sin token registrado → no intenta red (no_token)
        os.environ["DRIVE_APNS_ENABLED"] = "1"
        try:
            import unittest.mock as mock
            with mock.patch("helpers._get_setting", return_value=""):
                from drive_mode.apns_push import push_alert
                r = push_alert("t", "b")
            self.assertFalse(r["ok"])
            self.assertEqual(r["reason"], "no_token")
        finally:
            os.environ.pop("DRIVE_APNS_ENABLED", None)


if __name__ == "__main__":
    unittest.main()
