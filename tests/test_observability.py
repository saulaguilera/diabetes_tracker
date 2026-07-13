"""
Tests de observabilidad: sync multi-usuario con contexto + /healthz.

El primero es EL test de regresión del bug del 2026-07-12: el APScheduler
llamaba al sync sin set_user_context y el push de la Live Activity salía
por 'no_token' en silencio. sync_all_users DEBE correr cada usuario bajo
su propio contexto de tenant y dejar los marcadores de salud.
"""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import flask
from models import db, User
from helpers import (set_user_context, reset_user_context,
                     _get_setting, current_user_id)


def _make_app():
    app = flask.Flask(__name__, template_folder=os.path.join(
        os.path.dirname(__file__), "..", "templates"))
    app.config.update(SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
                      SQLALCHEMY_TRACK_MODIFICATIONS=False, TESTING=True,
                      SECRET_KEY="test")
    db.init_app(app)
    from blueprints.admin_bp import bp as admin_bp
    app.register_blueprint(admin_bp)
    with app.app_context():
        db.create_all()
    return app


_app = _make_app()


class TestSyncAllUsersContext(unittest.TestCase):
    """sync_all_users corre cada usuario bajo SU contexto (regresión)."""

    def setUp(self):
        self.ctx = _app.app_context()
        self.ctx.push()
        db.create_all()
        db.session.add(User(username="ana", password_hash="x"))
        db.session.add(User(username="beto", password_hash="x"))
        db.session.commit()

    def tearDown(self):
        db.session.rollback()
        db.drop_all()
        self.ctx.pop()

    def test_contexto_por_usuario_y_marcadores(self):
        import blueprints.sync as sync_mod
        contextos = []

        def _fake_sync(email, password, is_manual, provider="libre"):
            contextos.append(current_user_id())
            return {"insertadas": 2, "total": 2, "error": None}

        with mock.patch.object(sync_mod, "_sync_one_user", side_effect=_fake_sync), \
             mock.patch.object(sync_mod, "_cgm_config_for_user",
                               return_value=("libre", "a@b.c", "pw")), \
             mock.patch.object(sync_mod, "_maybe_morning_brief"):
            r = sync_mod.sync_all_users(False)

        ids = [u.id for u in db.session.execute(
            db.select(User), execution_options={"all_users": True}).scalars()]
        # cada usuario se sincronizó bajo SU contexto — no None, no siempre 1
        self.assertEqual(contextos, ids)
        self.assertEqual(r["insertadas"], 4)

        # latido global del scheduler (visible sin contexto y con contexto)
        self.assertIsNotNone(_get_setting("sched_last_run"))
        tok = set_user_context(ids[0])
        try:
            self.assertIsNotNone(_get_setting("sched_last_run"))
            bit = json.loads(_get_setting("sync_last"))
            self.assertTrue(bit["ok"])
            self.assertEqual(bit["insertadas"], 2)
        finally:
            reset_user_context(tok)


class TestHealthz(unittest.TestCase):

    def setUp(self):
        self.ctx = _app.app_context()
        self.ctx.push()
        db.create_all()
        self.client = _app.test_client()

    def tearDown(self):
        db.session.rollback()
        db.drop_all()
        self.ctx.pop()

    def test_sin_latido_es_503(self):
        r = self.client.get("/healthz")
        self.assertEqual(r.status_code, 503)
        self.assertFalse(r.get_json()["ok"])

    def test_con_latido_fresco_es_200(self):
        from datetime import datetime
        from helpers import _set_setting
        _set_setting("sched_last_run", datetime.now().isoformat(timespec="seconds"))
        db.session.commit()
        r = self.client.get("/healthz")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()["ok"])

    def test_latido_viejo_es_503(self):
        from datetime import datetime, timedelta
        from helpers import _set_setting
        _set_setting("sched_last_run",
                     (datetime.now() - timedelta(minutes=30)).isoformat(timespec="seconds"))
        db.session.commit()
        self.assertEqual(self.client.get("/healthz").status_code, 503)

    def test_panel_gateado_al_operador(self):
        with self.client.session_transaction() as s:
            s["logged_in"] = True
            s["user_id"] = 2
        self.assertEqual(self.client.get("/admin/estado").status_code, 403)


if __name__ == "__main__":
    unittest.main()
