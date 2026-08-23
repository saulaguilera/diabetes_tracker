"""Tests de las herramientas de REGISTRO del copiloto (chat → escribir en las
mismas tablas que /log) y de la línea SALUD DE LOS DATOS del contexto."""
import os
import sys
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import flask
from models import db, User, Meal, InsulinDose, Activity, GlucoseReading
from helpers import set_user_context, reset_user_context
from utils.copilot_tools import run_tool


def _make_app():
    app = flask.Flask(__name__)
    app.config.update(SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
                      SQLALCHEMY_TRACK_MODIFICATIONS=False, TESTING=True,
                      SECRET_KEY="test")
    db.init_app(app)
    return app


class TestRegistroDesdeElChat(unittest.TestCase):
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

    def test_registrar_comida_crea_meal_con_tenant(self):
        r = run_tool("registrar_comida", {"nombre": "pan con palta", "carbs": 25,
                                          "fiber": 3, "hace_minutos": 30})
        self.assertTrue(r.get("ok"), r)
        m = Meal.query.one()
        self.assertEqual(m.user_id, 1)
        self.assertEqual(m.carbs_g, 25)
        self.assertEqual(m.fiber_g, 3)
        # timestamp desplazado ~30 min hacia atrás
        self.assertLess(abs((datetime.now() - timedelta(minutes=30)
                             - m.timestamp).total_seconds()), 90)

    def test_comida_sin_carbos_pide_el_dato(self):
        r = run_tool("registrar_comida", {"nombre": "algo", "carbs": 0})
        self.assertIn("error", r)
        self.assertEqual(Meal.query.count(), 0)

    def test_registrar_insulina_valida_y_clampa(self):
        r = run_tool("registrar_insulina", {"unidades": 4.5, "tipo": "bolus"})
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(InsulinDose.query.one().units, 4.5)
        # unidades absurdas se clampan al tope (60), no explotan
        r2 = run_tool("registrar_insulina", {"unidades": 900})
        self.assertTrue(r2.get("ok"))
        self.assertEqual(r2["registrado"]["unidades"], 60)
        # tipo inválido cae a bolus
        r3 = run_tool("registrar_insulina", {"unidades": 2, "tipo": "mega"})
        self.assertEqual(r3["registrado"]["tipo"], "insulina bolus")

    def test_registrar_ejercicio(self):
        r = run_tool("registrar_ejercicio", {"actividad": "Bici",
                                             "duracion_min": 45,
                                             "intensidad": "alta"})
        self.assertTrue(r.get("ok"), r)
        a = Activity.query.one()
        self.assertEqual((a.activity_type, a.duration_min, a.intensity),
                         ("Bici", 45, "alta"))

    def test_hace_minutos_se_clampa_a_24h(self):
        r = run_tool("registrar_comida", {"nombre": "x", "carbs": 10,
                                          "hace_minutos": 99999})
        self.assertTrue(r.get("ok"))
        m = Meal.query.one()
        self.assertLess(abs((datetime.now() - timedelta(minutes=1440)
                             - m.timestamp).total_seconds()), 90)


class TestSaludDeDatos(unittest.TestCase):
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

    def test_contexto_avisa_dato_atrasado(self):
        db.session.add(GlucoseReading(timestamp=datetime.now() - timedelta(hours=3),
                                      value_mgdl=120, source="test"))
        db.session.commit()
        from blueprints.copilot_api import _chat_context
        ctx = _chat_context()
        self.assertIn("SALUD DE LOS DATOS", ctx)
        self.assertIn("ATRASADO", ctx)

    def test_contexto_fresco_sin_alarma(self):
        db.session.add(GlucoseReading(timestamp=datetime.now() - timedelta(minutes=4),
                                      value_mgdl=110, source="test"))
        db.session.commit()
        from blueprints.copilot_api import _chat_context
        ctx = _chat_context()
        self.assertIn("SALUD DE LOS DATOS", ctx)
        self.assertNotIn("ATRASADO", ctx)


if __name__ == "__main__":
    unittest.main()


class TestMemoriaEntreConversaciones(unittest.TestCase):
    """recordar/olvidar: la memoria de largo plazo es del usuario."""

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

    def test_recordar_guarda_y_aparece_en_el_contexto(self):
        r = run_tool("recordar", {"nota": "Cambió su basal a 22U por indicación médica"})
        self.assertTrue(r.get("ok"), r)
        from utils.copilot_memory import get_notes, memory_context_lines
        self.assertTrue(any("22U" in n["text"] for n in get_notes()))
        self.assertTrue(any("22U" in l for l in memory_context_lines()))

    def test_olvidar_borra_y_dice_QUE_borro(self):
        run_tool("recordar", {"nota": "Está estresado por exámenes finales"})
        run_tool("recordar", {"nota": "Cambió su basal a 22U"})
        # sin tildes también matchea («examenes» → «exámenes»)
        r = run_tool("olvidar", {"texto": "examenes"})
        self.assertEqual(len(r.get("borradas")), 1)
        self.assertIn("exámenes", r["borradas"][0])   # confirma el texto exacto
        from utils.copilot_memory import get_notes
        textos = [n["text"] for n in get_notes()]
        self.assertTrue(any("22U" in t for t in textos))

    def test_olvidar_todo_vacia_la_memoria(self):
        run_tool("recordar", {"nota": "Nota uno importante"})
        run_tool("recordar", {"nota": "Nota dos importante"})
        r = run_tool("olvidar", {"texto": "*"})
        self.assertTrue(r.get("memoria_vacia"))
        self.assertEqual(len(r.get("borradas")), 2)
        from utils.copilot_memory import get_notes
        self.assertEqual(get_notes(), [])

    def test_cap_lleno_reporta_la_expulsada(self):
        for i in range(20):
            run_tool("recordar", {"nota": f"Nota de relleno número {i:02d}"})
        r = run_tool("recordar", {"nota": "La nota veintiuno que desplaza"})
        self.assertTrue(r.get("ok"))
        self.assertIn("00", r.get("expulsada_por_espacio", ""))   # salió la más vieja
        self.assertIn("aviso", r)                                  # y el modelo debe avisar

    def test_contexto_trae_TODAS_las_notas(self):
        for i in range(12):
            run_tool("recordar", {"nota": f"Nota persistente número {i:02d}"})
        from utils.copilot_memory import memory_context_lines
        linea = next(l for l in memory_context_lines() if "NOTAS" in l)
        for i in range(12):   # las 12, no solo las últimas 8
            self.assertIn(f"{i:02d}", linea)

    def test_nota_vacia_no_se_guarda(self):
        r = run_tool("recordar", {"nota": "  "})
        self.assertIn("error", r)

    def test_olvidar_sin_coincidencias_no_explota(self):
        r = run_tool("olvidar", {"texto": "unicornios"})
        self.assertEqual(r.get("borradas"), [])
