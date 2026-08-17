"""Tests de los datos que alimentan la UI nueva: marcadores de eventos para
la onda de Hoy y puntuación T1D de comidas en el historial."""
import os
import sys
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import flask
from models import db, User, GlucoseReading, Meal, InsulinDose


def _make_app():
    app = flask.Flask(__name__, template_folder=os.path.join(
        os.path.dirname(__file__), "..", "templates"))
    app.config.update(SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
                      SQLALCHEMY_TRACK_MODIFICATIONS=False, TESTING=True,
                      SECRET_KEY="test")
    db.init_app(app)
    from blueprints.copilot_api import bp
    app.register_blueprint(bp)
    return app


class TestDatosDeUI(unittest.TestCase):
    def setUp(self):
        self.app = _make_app()
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        db.session.add(User(username="ana", password_hash="x", display_name="ana"))
        db.session.commit()
        now = datetime.now()
        for i in range(6):
            db.session.add(GlucoseReading(
                timestamp=now - timedelta(minutes=10 * i),
                value_mgdl=110 + i, source="test", user_id=1))
        db.session.add(Meal(timestamp=now - timedelta(minutes=35),
                            name="Cazuela", carbs_g=30, fiber_g=4,
                            health_score=8, user_id=1))
        db.session.add(InsulinDose(timestamp=now - timedelta(minutes=30),
                                   type="bolus", units=4, user_id=1))
        db.session.commit()
        self.client = self.app.test_client()
        with self.client.session_transaction() as s:
            s["logged_in"] = True
            s["user_id"] = 1
            s["username"] = "ana"

    def tearDown(self):
        db.session.remove()
        self.ctx.pop()

    def test_home_trae_marcadores_para_la_onda(self):
        r = self.client.get("/api/copilot/home").get_json()
        self.assertTrue(r["ok"])
        cats = sorted(m["cat"] for m in r.get("markers", []))
        self.assertEqual(cats, ["comida", "insulina"])
        for m in r["markers"]:
            self.assertIn("t", m)   # ISO para que la onda lo ancle en el tiempo

    def test_eventos_post_serie_no_desplazan_a_los_renderizables(self):
        """Hallazgo de la revisión: con sensor offline, los eventos posteriores
        a la última lectura consumían el tope de 14 y podían dejar la curva
        sin iconos. Ahora el tope se aplica DENTRO del rango de la serie y
        solo se dejan pasar 2 posteriores (anclados al borde por la UI)."""
        now = datetime.now()
        # 13 comidas DESPUÉS de la última lectura (sensor "offline")
        for i in range(13):
            db.session.add(Meal(timestamp=now + timedelta(minutes=1 + i),
                                name=f"post{i}", carbs_g=10, user_id=1))
        db.session.commit()
        r = self.client.get("/api/copilot/home").get_json()
        ts_serie_fin = r["series"][-1]["t"]
        dentro = [m for m in r["markers"] if m["t"] <= ts_serie_fin]
        fuera = [m for m in r["markers"] if m["t"] > ts_serie_fin]
        # los 2 eventos originales (dentro de la serie) siguen presentes
        self.assertGreaterEqual(len(dentro), 2)
        self.assertLessEqual(len(fuera), 2)

    def test_historial_trae_score_y_fibra_de_la_comida(self):
        r = self.client.get("/api/copilot/history").get_json()
        comida = next(e for e in r["events"] if e["cat"] == "comida")
        self.assertEqual(comida["data"]["score"], 8)
        self.assertEqual(comida["data"]["fiber"], 4)


if __name__ == "__main__":
    unittest.main()


class TestContadoresDeUso(unittest.TestCase):
    """Métricas de uso: contadores diarios por usuario — números, no contenido."""

    def setUp(self):
        self.app = _make_app()
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        db.session.add(User(username="ana", password_hash="x", display_name="ana"))
        db.session.commit()
        self.client = self.app.test_client()
        with self.client.session_transaction() as s:
            s["logged_in"] = True
            s["user_id"] = 1
            s["username"] = "ana"

    def tearDown(self):
        db.session.remove()
        self.ctx.pop()

    def test_abrir_app_y_registrar_cuentan(self):
        from helpers import set_user_context, reset_user_context, uso_7d
        self.client.get("/api/copilot/home")
        self.client.get("/api/copilot/home")
        self.client.post("/api/copilot/log", json={"cat": "comida",
                                                   "name": "pan", "carbs": 20})
        tok = set_user_context(1)
        try:
            u = uso_7d()
        finally:
            reset_user_context(tok)
        self.assertEqual(u.get("app"), 2)
        self.assertEqual(u.get("log"), 1)

    def test_contador_roto_no_rompe_el_request(self):
        from unittest import mock
        with mock.patch("helpers._set_setting", side_effect=Exception("boom")):
            r = self.client.get("/api/copilot/home")
        self.assertEqual(r.status_code, 200)   # el conteo jamás tumba el endpoint
