"""Sin contexto de usuario NO se escribe nada; los fallbacks de zona usan la
TZ del producto (la libc del contenedor devuelve UTC)."""
import os
import sys
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import flask
from models import db, User
from helpers import set_user_context, reset_user_context


def _make_app():
    app = flask.Flask(__name__)
    app.config.update(SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
                      SQLALCHEMY_TRACK_MODIFICATIONS=False, TESTING=True,
                      SECRET_KEY="test")
    db.init_app(app)
    return app


class TestSyncContexto(unittest.TestCase):
    def setUp(self):
        self.app = _make_app()
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        db.session.add(User(username="ana", password_hash="x", display_name="ana"))
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        self.ctx.pop()

    def test_do_libre_sync_sin_contexto_no_inserta(self):
        from blueprints.sync import _do_libre_sync
        r = _do_libre_sync("e@x.com", "pw")
        self.assertIn("sin contexto", r["error"])
        self.assertEqual(r["insertadas"], 0)

    def test_parse_reading_sin_tz_usuario_usa_tz_producto(self):
        os.environ["TZ"] = "America/Santiago"
        from utils.libre_linkup import _parse_reading
        # sin contexto de usuario → tz_usuario None → tz del producto (no UTC)
        ts = _parse_reading({"FactoryTimestamp": "9/4/2026 3:45:48 AM",
                             "ValueInMgPerDl": 108})["timestamp"]
        esperado = datetime(2026, 9, 4, 3, 45, 48, tzinfo=timezone.utc)
        from zoneinfo import ZoneInfo
        self.assertEqual(ts, esperado.astimezone(ZoneInfo("America/Santiago")).replace(tzinfo=None))

    def test_ahora_usuario_sin_tz_no_es_utc_crudo(self):
        os.environ["TZ"] = "America/Santiago"
        from helpers import ahora_usuario
        from zoneinfo import ZoneInfo
        a = ahora_usuario()
        b = datetime.now(ZoneInfo("America/Santiago")).replace(tzinfo=None)
        self.assertLess(abs((a - b).total_seconds()), 5)


if __name__ == "__main__":
    unittest.main()
