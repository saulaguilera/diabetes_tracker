"""
Tests de aislamiento multi-usuario (LA garantía de privacidad del producto).

Verifica el enforcement automático de models.py:
  - SELECT: cada usuario ve SOLO sus filas (filtro do_orm_execute).
  - INSERT: user_id se asigna solo desde el contexto (before_flush).
  - Settings: _get/_set_setting namespacean por usuario.
  - Sin contexto: sin filtro (comportamiento de scripts/arranque, documentado).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import flask
from models import db, GlucoseReading, Meal, CopilotNotification, UserSettings
from helpers import set_user_context, reset_user_context, _get_setting, _set_setting


def _make_test_app():
    app = flask.Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["TESTING"] = True
    db.init_app(app)
    with app.app_context():
        db.create_all()
    return app


_app = _make_test_app()


class TestTenantIsolation(unittest.TestCase):
    def setUp(self):
        self.ctx = _app.app_context()
        self.ctx.push()
        db.create_all()
        # datos de dos usuarios, cada uno insertado bajo su contexto
        t = set_user_context(1)
        db.session.add(GlucoseReading(value_mgdl=100, source="manual"))
        db.session.add(Meal(name="pizza de u1", carbs_g=60))
        db.session.add(CopilotNotification(title="notif u1"))
        db.session.commit()
        reset_user_context(t)
        t = set_user_context(2)
        db.session.add(GlucoseReading(value_mgdl=200, source="manual"))
        db.session.add(Meal(name="ensalada de u2", carbs_g=10))
        db.session.commit()
        reset_user_context(t)

    def tearDown(self):
        db.session.rollback()
        db.drop_all()
        self.ctx.pop()

    def test_select_solo_ve_lo_propio(self):
        t = set_user_context(1)
        try:
            vals = [r.value_mgdl for r in GlucoseReading.query.all()]
            self.assertEqual(vals, [100])
            meals = [m.name for m in Meal.query.all()]
            self.assertEqual(meals, ["pizza de u1"])
        finally:
            reset_user_context(t)
        t = set_user_context(2)
        try:
            vals = [r.value_mgdl for r in GlucoseReading.query.all()]
            self.assertEqual(vals, [200])
            self.assertEqual(CopilotNotification.query.count(), 0)  # la notif es de u1
        finally:
            reset_user_context(t)

    def test_insert_asigna_user_id_del_contexto(self):
        t = set_user_context(2)
        try:
            db.session.add(Meal(name="postre", carbs_g=30))
            db.session.commit()
            row = Meal.query.filter_by(name="postre").first()
            self.assertIsNotNone(row)
            self.assertEqual(row.user_id, 2)
        finally:
            reset_user_context(t)

    def test_filtros_explicitos_siguen_scoped(self):
        # un filter_by cualquiera NO puede saltar el tenant filter
        t = set_user_context(1)
        try:
            self.assertIsNone(Meal.query.filter_by(name="ensalada de u2").first())
        finally:
            reset_user_context(t)

    def test_sin_contexto_sin_filtro(self):
        # comportamiento documentado para scripts/arranque
        self.assertEqual(GlucoseReading.query.count(), 2)

    def test_settings_por_usuario(self):
        t = set_user_context(1)
        try:
            _set_setting("ui_lang", "es")
        finally:
            reset_user_context(t)
        t = set_user_context(2)
        try:
            _set_setting("ui_lang", "en")
            self.assertEqual(_get_setting("ui_lang"), "en")
        finally:
            reset_user_context(t)
        t = set_user_context(1)
        try:
            self.assertEqual(_get_setting("ui_lang"), "es")
        finally:
            reset_user_context(t)
        # las claves quedaron namespaceadas de verdad
        keys = sorted(s.key for s in UserSettings.query.all())
        self.assertEqual(keys, ["u1::ui_lang", "u2::ui_lang"])

    def test_settings_globales_compartidas(self):
        t = set_user_context(1)
        try:
            _set_setting("pat_i18n_abc123", "cache compartido")
        finally:
            reset_user_context(t)
        t = set_user_context(2)
        try:
            self.assertEqual(_get_setting("pat_i18n_abc123"), "cache compartido")
        finally:
            reset_user_context(t)


if __name__ == "__main__":
    unittest.main()
