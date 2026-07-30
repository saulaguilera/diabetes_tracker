"""Zona horaria POR USUARIO (bug de viaje 2026-07-30): la app manda la zona
del teléfono (X-Orbit-TZ), el backend la guarda y desde ahí lecturas,
registros y ventanas se calculan en la hora real del usuario."""
import os
import sys
import unittest
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import flask
from models import db, User
from helpers import (set_user_context, reset_user_context, _set_setting,
                     _get_setting, ahora_usuario)


def _make_app():
    app = flask.Flask(__name__)
    app.config.update(SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
                      SQLALCHEMY_TRACK_MODIFICATIONS=False, TESTING=True,
                      SECRET_KEY="test")
    db.init_app(app)
    return app


class TestZonaPorUsuario(unittest.TestCase):
    def setUp(self):
        self.app = _make_app()
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        db.session.add(User(username="ana", password_hash="x", display_name="ana"))
        db.session.commit()
        self.tok = set_user_context(1)

    def tearDown(self):
        reset_user_context(self.tok)
        db.session.remove()
        self.ctx.pop()

    def test_ahora_usuario_usa_su_zona(self):
        _set_setting("tz", "America/Mexico_City")
        esperado = datetime.now(ZoneInfo("America/Mexico_City")).replace(tzinfo=None)
        self.assertLess(abs((ahora_usuario() - esperado).total_seconds()), 5)

    def test_sin_zona_cae_a_la_del_servidor(self):
        self.assertLess(abs((ahora_usuario() - datetime.now()).total_seconds()), 5)

    def test_lectura_libre_en_la_zona_del_usuario(self):
        _set_setting("tz", "America/Mexico_City")   # UTC-6 fijo (sin DST)
        from utils.libre_linkup import _parse_reading
        r = _parse_reading({"FactoryTimestamp": "7/29/2026 3:00:00 PM",
                            "Timestamp": "7/29/2026 9:00:00 AM",
                            "ValueInMgPerDl": 120, "TrendArrow": 4})
        # 15:00 UTC = 09:00 en CDMX — coincide con el reloj del usuario
        self.assertEqual(r["timestamp"], datetime(2026, 7, 29, 9, 0, 0))

    def test_registro_por_chat_en_la_zona_del_usuario(self):
        _set_setting("tz", "America/Mexico_City")
        from utils.copilot_tools import run_tool
        from models import Meal
        r = run_tool("registrar_comida", {"nombre": "taco", "carbs": 20})
        self.assertTrue(r.get("ok"), r)
        esperado = datetime.now(ZoneInfo("America/Mexico_City")).replace(tzinfo=None)
        self.assertLess(abs((Meal.query.one().timestamp - esperado).total_seconds()), 60)

    def test_captura_valida_y_rechaza_invalida(self):
        from blueprints.copilot_api import _capturar_tz
        with self.app.test_request_context(headers={"X-Orbit-TZ": "America/Mexico_City"}):
            _capturar_tz()
        self.assertEqual(_get_setting("tz"), "America/Mexico_City")
        with self.app.test_request_context(headers={"X-Orbit-TZ": "Zona/Falsa"}):
            with self.assertRaises(Exception):
                _capturar_tz()
        self.assertEqual(_get_setting("tz"), "America/Mexico_City")   # intacta

    def test_ventana_del_brief_sigue_la_zona_del_usuario(self):
        # 08:00 en Chile pero 06:00 en CDMX → para este usuario NO es la
        # ventana 7-10 todavía: _maybe_morning_brief debe decidir con SU hora
        _set_setting("tz", "America/Mexico_City")
        from blueprints.sync import _maybe_morning_brief
        # con now explícito el default no se usa; acá validamos el default:
        # ahora_usuario() adentro — solo comprobamos que no explota y respeta
        # la ventana según la hora del usuario en este instante
        hora_usuario = ahora_usuario().hour
        res = _maybe_morning_brief()
        if not (7 <= hora_usuario < 10):
            self.assertIsNone(res)


if __name__ == "__main__":
    unittest.main()
