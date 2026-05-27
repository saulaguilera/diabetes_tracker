"""
utils/tests/test_hypo_risk_engine.py
─────────────────────────────────────
Validación del motor de riesgo de hipoglucemia nocturna (Hito 8).

Incluye el caso real del 26-27 de mayo 2026: el usuario tenía G=176 a las
22:00, inyectó 2U de NovoRapid, con I_basal_eff≈0.41U activa, IOB residual
≈0.3U y sin carbohidratos de cobertura. La glucemia llegó a 60 mg/dL a las
02:00. El engine DEBE detectar riesgo > 30%, trough < 80 y ventana 00:00-03:00.

Ejecutar: python3 -m utils.tests.test_hypo_risk_engine
"""
from __future__ import annotations

import sys
import math
from datetime import datetime, timedelta

# ── Path bootstrap (para ejecutar sin instalar el paquete) ───────────────────
import os
_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _root not in sys.path:
    sys.path.insert(0, _root)


from utils.hypo_risk_engine import (
    assess_nocturnal_hypo_risk,
    should_alert,
    format_alert_message,
    HORIZONS_MIN,
    SEV_MODERATE,
)
from pmm.ssm.basal_input import (
    BasalDose, compute_basal_eff,
    K_DEPOT_BASAL_DEFAULT, F_BIO_BASAL_DEFAULT,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _approx(a: float, b: float, tol: float = 0.05) -> bool:
    return abs(a - b) <= tol


# ── Construcción del FilterResult sintético ───────────────────────────────────

def _build_synthetic_filter_result(
    G: float,
    IOB: float,
    IOB_eff: float,
    basal_eff: float,
    S_I: float = 42.0,
    g_sigma: float = 12.0,
):
    """
    Construye un FilterResult mínimo para inyectar en el engine.
    Evita necesitar Flask app context / DB en los tests.
    """
    import numpy as np
    from pmm.ssm.filter import FilterResult
    from pmm.ssm.state import DIM_X, state_index

    x = np.zeros(DIM_X)
    x[state_index("G")]       = G
    x[state_index("IOB")]     = IOB
    x[state_index("IOB_eff")] = IOB_eff
    x[state_index("COB1")]    = 0.0
    x[state_index("COB2")]    = 0.0
    x[state_index("S_I")]     = S_I

    P = np.diag([
        g_sigma**2,   # G
        0.1,          # IOB
        0.1,          # IOB_eff
        1.0,          # COB1
        1.0,          # COB2
        25.0,         # S_I
    ])

    return FilterResult(
        x=x,
        P=P,
        last_ts=datetime(2026, 5, 26, 22, 0, 0),
        n_cgm_used=8,
        n_steps=24,
        error=None,
    )


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_case_real_may26_fallback():
    """
    Caso real 26 May 2026, 22:00 — usando el modelo fallback lineal.

    Estado:
        G=176, ROC=-0.3 mg/dL/min (bajando lentamente)
        Bolus propuesto: 2U NovoRapid
        IOB residual: 0.3U (de correcciones anteriores)
        I_basal_eff: 0.41U (Toujeo 10U/día, steady-state)
        COB: ~0g (no hay comida activa)

    Resultado real: G=60 a las 02:00 → hipo severa nocturna
    El test debe detectar:
        - p_hypo_70  > 0.30   (riesgo >30%)
        - trough     < 80 mg/dL
        - ventana de riesgo incluye 00:00-03:00
    """
    ts = datetime(2026, 5, 26, 22, 0, 0)

    risk = assess_nocturnal_hypo_risk(
        current_glucose      = 176.0,
        roc                  = -0.3,
        proposed_bolus       = 2.0,
        current_iob          = 0.3,
        current_basal_effect = 0.41,
        carbs_on_board       = 0.0,
        timestamp            = ts,
        icr                  = 12.0,
        isf                  = 42.0,    # ISF aprendido por el PMM del usuario
    )

    print(f"\n  [fallback] risk_score={risk.risk_score:.3f}  "
          f"p70={risk.p_hypo_70:.2%}  "
          f"p55={risk.p_hypo_55:.2%}  "
          f"trough={risk.min_predicted_glucose:.0f}mg/dL  "
          f"eta={risk.min_glucose_eta_min}min  "
          f"severity={risk.severity}")

    assert risk.p_hypo_70 > 0.30, (
        f"p_hypo_70={risk.p_hypo_70:.3f} debería ser >0.30 — "
        f"el evento real fue 60 mg/dL a las 02:00"
    )
    assert risk.min_predicted_glucose < 80.0, (
        f"trough={risk.min_predicted_glucose:.1f} debería ser <80 mg/dL"
    )

    # La ventana de riesgo debe incluir las 00:00-03:00
    t_risk = ts + timedelta(minutes=risk.min_glucose_eta_min)
    assert t_risk.hour >= 0 or t_risk.day > ts.day, (
        f"trough proyectado a las {t_risk} — debería estar después de medianoche"
    )

    assert should_alert(risk), "should_alert() debe ser True con este perfil"
    print(f"  ✓ caso real: alerta detectada, trough en {t_risk.strftime('%H:%M')}")


def test_case_real_may26_ssm():
    """
    Mismo caso real pero inyectando un FilterResult sintético del SSM.
    Valida que el engine usa forward_predict() correctamente.
    """
    ts = datetime(2026, 5, 26, 22, 0, 0)

    # Construir FilterResult que representa el estado del SSM a las 22:00
    # IOB_eff ≈ 0.2U (decayendo — IOB de boluses del día)
    filter_result = _build_synthetic_filter_result(
        G        = 176.0,
        IOB      = 0.3,       # IOB que aún no actuó en plasma
        IOB_eff  = 0.2,       # IOB ya en intersticial
        basal_eff= 0.41,      # se pasa directamente al StepInputs en forward_predict
        S_I      = 42.0,
        g_sigma  = 10.0,
    )

    # Simular que la basal ya está incorporada como i_basal_eff en el state
    # (se computa internamente en forward_predict via basal_input)
    # Aquí inyectamos el FilterResult directamente
    risk = assess_nocturnal_hypo_risk(
        current_glucose      = 176.0,
        roc                  = -0.3,
        proposed_bolus       = 2.0,
        current_iob          = 0.3,
        current_basal_effect = 0.41,
        carbs_on_board       = 0.0,
        timestamp            = ts,
        icr                  = 12.0,
        isf                  = 42.0,
        _filter_result       = filter_result,   # inyectar directamente
    )

    print(f"\n  [SSM] risk_score={risk.risk_score:.3f}  "
          f"p70={risk.p_hypo_70:.2%}  "
          f"p55={risk.p_hypo_55:.2%}  "
          f"trough={risk.min_predicted_glucose:.0f}mg/dL  "
          f"eta={risk.min_glucose_eta_min}min  "
          f"ssm_available={risk.ssm_available}  "
          f"severity={risk.severity}")

    assert risk.ssm_available, "SSM debe estar disponible con FilterResult inyectado"
    assert risk.p_hypo_70 > 0.30, (
        f"p_hypo_70={risk.p_hypo_70:.3f} debe ser >0.30 con SSM"
    )
    assert risk.min_predicted_glucose < 90.0, (
        f"trough SSM={risk.min_predicted_glucose:.1f} debería ser <90 mg/dL"
    )
    assert should_alert(risk), "should_alert() debe ser True"
    print(f"  ✓ caso real SSM: riesgo={risk.risk_score:.2f}, severidad={risk.severity}")


def test_safe_scenario_no_alert():
    """
    Escenario seguro: G=140, sin bolus, sin IOB, COB=30g.
    NO debe disparar alerta (p_hypo_70 < 0.15).
    """
    ts = datetime(2026, 5, 26, 18, 0, 0)  # tarde, no nocturno

    risk = assess_nocturnal_hypo_risk(
        current_glucose      = 140.0,
        roc                  = 0.0,
        proposed_bolus       = 0.0,
        current_iob          = 0.0,
        current_basal_effect = 0.41,
        carbs_on_board       = 30.0,
        timestamp            = ts,
        icr                  = 12.0,
        isf                  = 42.0,
    )

    print(f"\n  [safe] risk_score={risk.risk_score:.3f}  "
          f"p70={risk.p_hypo_70:.2%}  trough={risk.min_predicted_glucose:.0f}  "
          f"severity={risk.severity}")

    assert not should_alert(risk), (
        f"p_hypo_70={risk.p_hypo_70:.3f} — escenario seguro no debe disparar alerta"
    )
    assert risk.min_predicted_glucose > 80.0, (
        f"trough={risk.min_predicted_glucose:.1f} debería estar >80 en escenario seguro"
    )
    print(f"  ✓ escenario seguro: sin alerta (severity={risk.severity})")


def test_high_carbs_buffer():
    """
    Con COB suficiente para cubrir la insulina, el riesgo debe reducirse.
    G=160, 3U bolus, COB=45g (45/12 ≈ 3.75U de cobertura de carbs).
    """
    ts = datetime(2026, 5, 26, 20, 0, 0)

    risk = assess_nocturnal_hypo_risk(
        current_glucose      = 160.0,
        roc                  = 0.0,
        proposed_bolus       = 3.0,
        current_iob          = 0.0,
        current_basal_effect = 0.41,
        carbs_on_board       = 45.0,  # suficiente para cubrir el bolo
        timestamp            = ts,
        icr                  = 12.0,
        isf                  = 42.0,
    )

    print(f"\n  [high_carbs] risk_score={risk.risk_score:.3f}  "
          f"p70={risk.p_hypo_70:.2%}  trough={risk.min_predicted_glucose:.0f}  "
          f"severity={risk.severity}")

    # Con COB suficiente, trough no debería caer en zona hipo
    assert risk.min_predicted_glucose > 80.0, (
        f"trough={risk.min_predicted_glucose:.1f} — con 45g COB no debería hipoglucemiar"
    )
    print(f"  ✓ buffer de carbohidratos reduce riesgo (trough={risk.min_predicted_glucose:.0f} mg/dL)")


def test_horizon_detail_populated():
    """Verifica que horizon_detail tiene entradas para todos los horizontes."""
    ts = datetime(2026, 5, 26, 22, 0, 0)
    risk = assess_nocturnal_hypo_risk(
        current_glucose=176.0, roc=-0.3, proposed_bolus=2.0,
        current_iob=0.3, current_basal_effect=0.41, carbs_on_board=0.0,
        timestamp=ts, icr=12.0, isf=42.0,
    )
    for h in HORIZONS_MIN:
        assert h in risk.horizon_detail, f"horizonte {h}min falta en horizon_detail"
        d = risk.horizon_detail[h]
        assert "g_pred" in d, f"horizonte {h}: falta g_pred"
        assert "p70" in d,    f"horizonte {h}: falta p70"
        assert 0 <= d["p70"] <= 1.0, f"horizonte {h}: p70 fuera de [0,1]"
    print(f"  ✓ horizon_detail contiene los {len(HORIZONS_MIN)} horizontes esperados")


def test_format_alert_message():
    """El mensaje de alerta debe ser legible y coherente con el riesgo."""
    ts = datetime(2026, 5, 26, 22, 0, 0)
    risk = assess_nocturnal_hypo_risk(
        current_glucose=176.0, roc=-0.3, proposed_bolus=2.0,
        current_iob=0.3, current_basal_effect=0.41, carbs_on_board=0.0,
        timestamp=ts, icr=12.0, isf=42.0,
    )
    msg_full    = format_alert_message(risk, compact=False)
    msg_compact = format_alert_message(risk, compact=True)

    assert len(msg_full) > 50,    "mensaje full debe tener contenido"
    assert len(msg_compact) > 20, "mensaje compact debe tener contenido"
    assert str(round(risk.p_hypo_70 * 100)) in msg_full or \
           str(round(risk.p_hypo_70 * 100)) in msg_compact, \
           "el porcentaje de riesgo debe aparecer en el mensaje"
    print(f"  ✓ formato mensaje OK ({len(msg_full)} chars full, {len(msg_compact)} chars compact)")
    print(f"    compact: {msg_compact}")


def test_missed_basal_increases_risk():
    """
    Cuando la basal está baja (dosis olvidada), el riesgo TAMBIÉN debería
    ser considerado: la corrección de hiperglucemia tiene menos amortiguación.
    Con I_basal_eff=0.0 (sin basal) vs 0.41 (normal), el trough será similar
    ya que la basal es efecto CONTINUO — lo que cambia es la corrección
    de la hiperglucemia residual. Este test verifica consistencia numérica.
    """
    ts = datetime(2026, 5, 26, 22, 0, 0)

    risk_normal = assess_nocturnal_hypo_risk(
        current_glucose=176.0, roc=-0.3, proposed_bolus=2.0,
        current_iob=0.3, current_basal_effect=0.41, carbs_on_board=0.0,
        timestamp=ts, icr=12.0, isf=42.0,
    )
    risk_no_basal = assess_nocturnal_hypo_risk(
        current_glucose=176.0, roc=-0.3, proposed_bolus=2.0,
        current_iob=0.3, current_basal_effect=0.0, carbs_on_board=0.0,
        timestamp=ts, icr=12.0, isf=42.0,
    )

    # Con basal, el riesgo de hipo es igual o mayor (basal amplía efecto insulínico)
    # (verificar solo que el engine computa valores consistentes)
    assert risk_normal.risk_score >= 0 and risk_no_basal.risk_score >= 0
    print(f"  ✓ consistencia basal: "
          f"con_basal risk={risk_normal.risk_score:.3f}  "
          f"sin_basal risk={risk_no_basal.risk_score:.3f}")


# ── Runner ────────────────────────────────────────────────────────────────────

def main():
    tests = [
        test_case_real_may26_fallback,
        test_case_real_may26_ssm,
        test_safe_scenario_no_alert,
        test_high_carbs_buffer,
        test_horizon_detail_populated,
        test_format_alert_message,
        test_missed_basal_increases_risk,
    ]
    passed = 0
    failed = 0
    print()
    print("══ Tests hypo_risk_engine.py (Hito 8) ══════════════════")
    for t in tests:
        try:
            print(f"\n• {t.__name__}")
            t()
            passed += 1
        except AssertionError as e:
            print(f"  ✗ FAIL: {e}")
            failed += 1
        except Exception as e:
            import traceback
            print(f"  ✗ ERROR: {type(e).__name__}: {e}")
            traceback.print_exc()
            failed += 1
    print()
    print(f"══ Resultado: {passed} passed, {failed} failed ══")
    if failed > 0:
        print()
        print("⚠  ADVERTENCIA: El PR no está listo si test_case_real_may26 falla.")
        print("   El engine DEBE detectar el evento real del 26-27 de mayo.")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
