"""Tests del push nativo de patrones («🧠 Orbit encontró algo»).

El escaneo (_check_new_patterns) existía pero solo lo disparaba el GET de
notificaciones — es decir, al ABRIR la app: el push llegaba cuando ya estabas
adentro. Ahora sync_all_users lo corre en segundo plano por usuario (ventana
9:00-21:30). Estos tests cubren la ventana y el disparo desde el cron.
"""
import os
import sys
import unittest
from datetime import datetime
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from blueprints.sync import _maybe_pattern_scan


class TestVentanaHoraria(unittest.TestCase):
    def _corre_a(self, hora, minuto=0):
        with mock.patch("blueprints.copilot_api._check_new_patterns") as m:
            _maybe_pattern_scan(now=datetime(2026, 7, 21, hora, minuto))
            return m.called

    def test_de_madrugada_no_molesta(self):
        for h in (0, 3, 6, 8):
            self.assertFalse(self._corre_a(h), f"corrió a las {h}h")

    def test_de_noche_tarde_no_molesta(self):
        self.assertFalse(self._corre_a(21, 45))
        self.assertFalse(self._corre_a(22, 0))
        self.assertFalse(self._corre_a(23, 30))

    def test_en_horario_diurno_corre(self):
        for h in (9, 12, 15, 18, 20):
            self.assertTrue(self._corre_a(h), f"no corrió a las {h}h")
        self.assertTrue(self._corre_a(21, 30))

    def test_error_del_escaneo_no_explota_hacia_el_cron(self):
        # sync_all_users lo llama con try/except; acá validamos que el
        # escaneo con excepción propaga (el except vive en el caller)
        with mock.patch("blueprints.copilot_api._check_new_patterns",
                        side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                _maybe_pattern_scan(now=datetime(2026, 7, 21, 12, 0))


class TestDisparoDesdeElCron(unittest.TestCase):
    """sync_all_users debe invocar el escaneo por usuario, bajo su contexto."""

    def test_sync_llama_al_escaneo_por_usuario(self):
        import flask
        from models import db, User
        from helpers import current_user_id

        app = flask.Flask(__name__)
        app.config.update(SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
                          SQLALCHEMY_TRACK_MODIFICATIONS=False, TESTING=True,
                          SECRET_KEY="test")
        db.init_app(app)
        with app.app_context():
            db.create_all()
            db.session.add(User(username="ana", password_hash="x",
                                display_name="ana"))
            db.session.commit()

            contextos = []

            def _spy():
                contextos.append(current_user_id())

            import blueprints.sync as sync_mod
            with mock.patch.object(sync_mod, "_cgm_config_for_user",
                                   return_value=("libre", "a@b.c", "pw")), \
                 mock.patch.object(sync_mod, "_sync_one_user",
                                   return_value={"insertadas": 0, "total": 0}), \
                 mock.patch.object(sync_mod, "_maybe_morning_brief"), \
                 mock.patch.object(sync_mod, "_maybe_pattern_scan",
                                   side_effect=_spy):
                sync_mod.sync_all_users()

            # el escaneo corrió para el usuario y BAJO su contexto de tenant
            self.assertEqual(contextos, [1])


if __name__ == "__main__":
    unittest.main()
