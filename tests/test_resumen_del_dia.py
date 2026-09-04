"""Tests de resumen_del_dia (gráfica del día en el chat): la serie viaja SOLO
por el canal lateral _frontend → flask.g, jamás al contexto del modelo."""
import json
import os
import sys
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import flask
from flask import g
from models import db, User, Meal, GlucoseReading
from helpers import set_user_context, reset_user_context, ahora_usuario
from utils.copilot_tools import run_tool


def _make_app():
    app = flask.Flask(__name__)
    app.config.update(SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
                      SQLALCHEMY_TRACK_MODIFICATIONS=False, TESTING=True,
                      SECRET_KEY="test")
    db.init_app(app)
    return app


class TestResumenDelDia(unittest.TestCase):
    def setUp(self):
        self.app = _make_app()
        self.ctx = self.app.test_request_context()   # request context: g vive
        self.ctx.push()
        db.create_all()
        db.session.add(User(username="ana", password_hash="x", display_name="ana"))
        db.session.commit()
        self.tok = set_user_context(1)

    def tearDown(self):
        reset_user_context(self.tok)
        db.session.remove()
        self.ctx.pop()

    def _sembrar_dia(self, dia, cada_min=5, base=110):
        """Lecturas de un día completo (00:10 → 23:5x) con una excursión."""
        t = datetime(dia.year, dia.month, dia.day, 0, 10)
        fin = t + timedelta(hours=23, minutes=40)
        i = 0
        while t <= fin:
            v = base + (90 if 14 <= t.hour < 15 else 0) - (55 if t.hour == 4 else 0)
            db.session.add(GlucoseReading(timestamp=t, value_mgdl=v, source="test"))
            t += timedelta(minutes=cada_min)
            i += 1
        db.session.commit()
        return i

    def test_serie_solo_por_el_canal_lateral(self):
        hoy = ahora_usuario().date()
        self._sembrar_dia(hoy)
        out = run_tool("resumen_del_dia", {})
        # lo que ve el modelo: resumen sin serie ni clave reservada
        crudo = json.dumps(out, ensure_ascii=False)
        self.assertNotIn("_frontend", crudo)
        self.assertNotIn('"series"', crudo)
        self.assertEqual(out["fecha"], hoy.isoformat())
        self.assertIn("tir_pct", out)
        self.assertIn("eventos", out)
        self.assertIn("nárrala", out.get("nota", ""))
        # el canal lateral: payload completo en g
        fe = (getattr(g, "copilot_fe", None) or [None])[-1]
        self.assertIsNotNone(fe)
        self.assertEqual(fe["kind"], "grafica_dia")
        self.assertGreaterEqual(len(fe["series"]), 2)
        self.assertLessEqual(len(fe["series"]), 150)   # decimada
        # la excursión sobrevive la decimación (mín y máx globales presentes)
        vals = [p["v"] for p in fe["series"]]
        self.assertEqual(max(vals), out["maximo"]["v"])
        self.assertEqual(min(vals), out["minimo"]["v"])

    def test_dia_sin_datos_no_emite_grafica(self):
        out = run_tool("resumen_del_dia", {"fecha": "2020-01-15"})
        self.assertIn("no habrá gráfica", out.get("nota", ""))
        self.assertIsNone(getattr(g, "copilot_fe", None))

    def test_fecha_invalida_y_futura(self):
        self.assertIn("error", run_tool("resumen_del_dia", {"fecha": "mañana"}))
        hoy = ahora_usuario().date()
        self._sembrar_dia(hoy)
        futuro = (hoy + timedelta(days=3)).isoformat()
        out = run_tool("resumen_del_dia", {"fecha": futuro})
        self.assertEqual(out["fecha"], hoy.isoformat())   # clamp a hoy
        self.assertIn("nota_fecha", out)

    def test_eventos_del_dia_en_resumen_y_markers(self):
        hoy = ahora_usuario().date()
        self._sembrar_dia(hoy)
        db.session.add(Meal(name="marraqueta", carbs_g=40,
                            timestamp=datetime(hoy.year, hoy.month, hoy.day, 8, 30)))
        db.session.commit()
        out = run_tool("resumen_del_dia", {})
        self.assertTrue(any(e["cat"] == "comida" and "marraqueta" in e["title"]
                            for e in out["eventos"]))
        fe = (getattr(g, "copilot_fe", None) or [None])[-1]
        self.assertTrue(any(m["cat"] == "comida" for m in fe["markers"]))

    def test_multiples_llamadas_gana_la_ultima(self):
        hoy = ahora_usuario().date()
        self._sembrar_dia(hoy)
        ayer = hoy - timedelta(days=1)
        self._sembrar_dia(ayer, base=140)
        run_tool("resumen_del_dia", {"fecha": ayer.isoformat()})
        run_tool("resumen_del_dia", {})
        ultima = (getattr(g, "copilot_fe", None) or [None])[-1]
        self.assertEqual(ultima["fecha"], hoy.isoformat())

    def test_sin_request_context_no_rompe(self):
        hoy = ahora_usuario().date()
        self._sembrar_dia(hoy)
        # fuera del request context (cron/tests): la gráfica se descarta sin error
        self.ctx.pop()
        plain = self.app.app_context()
        plain.push()
        try:
            out = run_tool("resumen_del_dia", {})
            self.assertIn("tir_pct", out)
            self.assertNotIn("_frontend", json.dumps(out))
        finally:
            plain.pop()
            self.ctx.push()   # tearDown lo saca


if __name__ == "__main__":
    unittest.main()
