"""
utils/tests/test_cob_contract.py
─────────────────────────────────
Guard de contrato para `current_cob_detailed`.

Contexto: el SSM (`pmm/ssm/filter.py::_warm_start_cob`) y todo el sistema
(`get_kinetics_snapshot`) consumen el COB vía la clave **`carbs_cob`**.
Hubo un bug en el que el warm-start leía `detail.get("cob")` —una clave que
NO existe— y por lo tanto inicializaba el COB del SSM SIEMPRE en 0, dejando al
modelo ciego a los carbohidratos al arrancar (sesgo en predicciones post-comida).

Este test falla si:
  1. `current_cob_detailed` deja de exponer `carbs_cob` / `total_cob`.
  2. Una comida reciente con carbos NO produce `carbs_cob > 0`
     (es decir, los carbohidratos dejan de entrar al cálculo).

No usa DB: arma un `Meal` falso con los atributos que la función lee por getattr.
"""
import unittest
from datetime import datetime, timedelta


class _FakeMeal:
    """Mínimo viable: solo los atributos que current_cob_detailed lee."""
    def __init__(self, timestamp, carbs_g=0, fat_g=0, protein_g=0,
                 name="test", categoria=None):
        self.timestamp = timestamp
        self.carbs_g = carbs_g
        self.fat_g = fat_g
        self.protein_g = protein_g
        self.name = name
        self.categoria = categoria


class TestCobContract(unittest.TestCase):

    def setUp(self):
        from utils.kinetics import current_cob_detailed
        self.current_cob_detailed = current_cob_detailed
        self.now = datetime.now()

    def test_expone_claves_esperadas(self):
        """El consumidor depende de carbs_cob y total_cob."""
        d = self.current_cob_detailed([], at_time=self.now)
        self.assertIn("carbs_cob", d)
        self.assertIn("total_cob", d)

    def test_no_existe_clave_cob_pelada(self):
        """Regresión: 'cob' NO es una clave válida — usar carbs_cob."""
        d = self.current_cob_detailed([], at_time=self.now)
        self.assertNotIn(
            "cob", d,
            "Si agregás la clave 'cob', revisá _warm_start_cob: el bug original "
            "fue leer una clave inexistente y caer al default 0.",
        )

    def test_comida_reciente_produce_carbs_cob(self):
        """Una comida de 60g hace 30min DEBE dejar carbs_cob > 0."""
        meal = _FakeMeal(self.now - timedelta(minutes=30), carbs_g=60)
        d = self.current_cob_detailed([meal], at_time=self.now)
        self.assertGreater(
            d["carbs_cob"], 0.0,
            "carbs_cob == 0 con una comida reciente → el modelo quedaría ciego "
            "a los carbohidratos (mismo síntoma que el bug del warm-start).",
        )

    def test_warm_start_ssm_usa_carbs_no_cero(self):
        """
        El warm-start del SSM debe leer la clave correcta. Reproducimos su
        lógica de extracción sobre el dict real (sin tocar la DB).
        """
        meal = _FakeMeal(self.now - timedelta(minutes=30), carbs_g=60)
        detail = self.current_cob_detailed([meal], at_time=self.now)
        # Misma extracción que pmm/ssm/filter.py::_warm_start_cob
        cob_total = detail.get("carbs_cob", 0.0) if isinstance(detail, dict) else 0.0
        c1, c2 = float(cob_total * 0.55), float(cob_total * 0.45)
        self.assertGreater(c1 + c2, 0.0,
                           "El warm-start del SSM quedaría en 0 con carbos activos.")


if __name__ == "__main__":
    unittest.main()
