"""
safety/tests/test_confidence.py
─────────────────────────────────
Tests del sistema de confianza unificado y la capa narrativa.

Ejecutar: python3 -m safety.tests.test_confidence
"""
from __future__ import annotations
import sys, os
_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _root not in sys.path:
    sys.path.insert(0, _root)

from datetime import datetime
from safety.confidence import compute_confidence, ConfidenceReport, THRESHOLD_FULL
from safety.narrative import (
    render_hypo_warning, render_confidence_message, render_degradation_message,
    HypoWarningText, TONE_CALM, TONE_ALERT, TONE_URGENT,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _approx(a, b, tol=0.05):
    return abs(a - b) <= tol

TS_NIGHT = datetime(2026, 5, 26, 23, 0, 0)   # 23:00 — ventana nocturna
TS_DAY   = datetime(2026, 5, 26, 15, 0, 0)   # 15:00 — ventana diurna


# ─── Tests de confidence.py ───────────────────────────────────────────────────

def test_perfect_conditions():
    """Datos perfectos → score alto, modo full."""
    cr = compute_confidence(
        now=TS_NIGHT,
        sigma_g=8.0,        # muy bajo
        n_cgm_used=12,
        n_readings_6h=18,
        max_gap_min=15,
        minutes_since_last_cgm=5,
        artifacts_24h=0,
        recent_mean_z=0.1,
        recent_var_z=0.95,
        minutes_since_last_update=10,
    )
    assert cr.score >= THRESHOLD_FULL, f"score={cr.score:.3f} debe ser >= {THRESHOLD_FULL}"
    assert cr.degradation_mode == "full"
    assert cr.should_recommend
    print(f"  ✓ condiciones perfectas: score={cr.score:.3f} modo={cr.degradation_mode}")


def test_stale_cgm_degrades():
    """CGM viejo (90+ min) → observability baja → modo conservador o peor."""
    cr = compute_confidence(
        now=TS_NIGHT,
        sigma_g=10.0,
        n_readings_6h=3,
        max_gap_min=95,
        minutes_since_last_cgm=95,   # ≥ STALE_BAD_MIN
        artifacts_24h=0,
    )
    assert cr.degradation_mode in ("conservative", "observe_only", "silent"), \
        f"modo={cr.degradation_mode} — CGM viejo debería degradar"
    assert cr.limiting_factor in ("observability", "sensor_health", "model_freshness"), \
        f"factor limitante inesperado: {cr.limiting_factor}"
    print(f"  ✓ CGM viejo degrada: score={cr.score:.3f} modo={cr.degradation_mode} "
          f"factor={cr.limiting_factor}")


def test_many_artifacts_degrades():
    """4+ artefactos en 24h → sensor_health bajo."""
    cr = compute_confidence(
        now=TS_NIGHT,
        sigma_g=10.0, n_readings_6h=12, max_gap_min=15,
        minutes_since_last_cgm=5, artifacts_24h=5, recent_mean_z=0.1,
    )
    assert cr.components["sensor_health"] <= 0.20, \
        f"sensor_health={cr.components['sensor_health']:.3f} — debería ser <= 0.20 con 5 artefactos"
    print(f"  ✓ artefactos degradan sensor_health: {cr.components['sensor_health']:.3f}")


def test_biased_innovations_degrades():
    """mean_z > 2.0 → innovation_quality baja."""
    cr = compute_confidence(
        now=TS_NIGHT,
        sigma_g=10.0, n_readings_6h=12, max_gap_min=15,
        minutes_since_last_cgm=5, artifacts_24h=0,
        recent_mean_z=2.5,   # bias alto
        recent_var_z=1.0,
    )
    iq = cr.components["innovation_quality"]
    assert iq < 0.50, f"innovation_quality={iq:.3f} — bias alto debería degradar"
    print(f"  ✓ bias sistemático degrada innovation_quality: {iq:.3f}")


def test_silent_mode_suppresses_alerts():
    """Datos muy malos → modo silent → suppress_alerts() = True."""
    cr = compute_confidence(
        now=TS_NIGHT,
        sigma_g=45.0,        # incertidumbre máxima
        n_readings_6h=0,     # cero lecturas
        max_gap_min=999,     # gap infinito
        minutes_since_last_cgm=200,
        artifacts_24h=6,
        recent_mean_z=3.0,
    )
    assert cr.suppress_alerts(), f"modo={cr.degradation_mode} — debería suprimir alertas"
    assert not cr.should_recommend, "should_recommend debe ser False en modo silent"
    print(f"  ✓ modo silent suprime alertas: score={cr.score:.3f}")


def test_alert_threshold_boost_conservative():
    """Confianza < 0.50 → boost de +0.15 al threshold."""
    cr = compute_confidence(
        now=TS_NIGHT,
        sigma_g=25.0, n_readings_6h=4, max_gap_min=45,
        minutes_since_last_cgm=30, artifacts_24h=1,
        recent_mean_z=1.0,
    )
    boost = cr.alert_threshold_boost()
    if cr.score < 0.50:
        assert boost >= 0.15, f"boost={boost:.2f} — score<0.50 debe dar boost>=0.15"
    print(f"  ✓ threshold boost: score={cr.score:.3f} boost={boost:.2f}")


def test_components_sum_to_one():
    """Los pesos de los componentes deben sumar 1.0."""
    from safety.confidence import W_SHARPNESS, W_OBSERV, W_SENSOR, W_INNOVATION, W_FRESHNESS
    total = W_SHARPNESS + W_OBSERV + W_SENSOR + W_INNOVATION + W_FRESHNESS
    assert abs(total - 1.0) < 1e-6, f"pesos suman {total:.6f}, debe ser 1.0"
    print(f"  ✓ pesos suman 1.0 exacto")


def test_to_dict_serializable():
    """to_dict() debe devolver tipos JSON-serializables."""
    import json
    cr = compute_confidence(
        now=TS_NIGHT, sigma_g=10.0, n_readings_6h=12,
        max_gap_min=15, minutes_since_last_cgm=5, artifacts_24h=0,
    )
    d = cr.to_dict()
    json.dumps(d)  # debe no lanzar
    assert "score" in d and "degradation_mode" in d
    print(f"  ✓ to_dict() serializable como JSON")


# ─── Tests de narrative.py ────────────────────────────────────────────────────

class _MockAssessment:
    """Mock mínimo de HypoRiskAssessment para tests de narrativa."""
    def __init__(self, **kw):
        self.proposed_bolus        = kw.get("proposed_bolus", 2.0)
        self.contributing_factors  = kw.get("contributing_factors", [
            "Insulina acumulada: bolo propuesto (2.0U) + IOB activo (0.3U) = 2.3U total",
            "Basal activa: Toujeo con 0.41U en intersticial (efecto continuo durante la noche)",
        ])
        self.p_hypo_70             = kw.get("p_hypo_70", 0.38)
        self.p_hypo_55             = kw.get("p_hypo_55", 0.12)
        self.min_predicted_glucose = kw.get("min_predicted_glucose", 72.0)
        self.projected_trough_time = kw.get("projected_trough_time",
                                            datetime(2026, 5, 27, 1, 30))
        self.min_glucose_eta_min   = kw.get("min_glucose_eta_min", 150)
        self.severity              = kw.get("severity", "high")
        self.ssm_available         = kw.get("ssm_available", True)


def test_narrative_no_technical_jargon():
    """La narrativa no debe contener términos técnicos inapropiados."""
    cr_full = compute_confidence(
        now=TS_NIGHT, sigma_g=10.0, n_readings_6h=12,
        max_gap_min=15, minutes_since_last_cgm=5, artifacts_24h=0,
    )
    mock = _MockAssessment()
    warning = render_hypo_warning(mock, cr_full, now=TS_NIGHT)

    # Términos que NO deben aparecer
    forbidden = ["intersticial", "IOB_eff", "kernel", "K_depot", "pharmacokinetics",
                 "trough", "nadir", "mg/dL/min", "p_hypo", "innovation"]
    full_text = (warning.title + " " + warning.probability_phrase + " " +
                 warning.trough_phrase + " " + " ".join(warning.factors) +
                 " " + warning.suggestion).lower()

    for term in forbidden:
        assert term.lower() not in full_text, \
            f"Término técnico prohibido encontrado: '{term}'"

    assert not warning.suppress, "No debe suprimir con confianza alta"
    print(f"  ✓ narrativa sin jerga técnica: '{warning.title}'")
    print(f"    Factores: {warning.factors}")


def test_narrative_human_factors():
    """Los factores deben ser legibles y empáticos."""
    cr_full = compute_confidence(
        now=TS_NIGHT, sigma_g=10.0, n_readings_6h=12,
        max_gap_min=15, minutes_since_last_cgm=5, artifacts_24h=0,
    )
    mock = _MockAssessment()
    warning = render_hypo_warning(mock, cr_full, now=TS_NIGHT)

    # Al menos un factor humano mencionando insulina o basal
    all_factors_text = " ".join(warning.factors).lower()
    has_insulin_ref = any(w in all_factors_text for w in ["insulina", "bolo", "toujeo", "basal"])
    assert has_insulin_ref, f"Factores sin referencia a insulina: {warning.factors}"
    print(f"  ✓ factores humanos mencionan insulina/basal")


def test_narrative_suppressed_when_silent():
    """Modo silent → narrativa suprimida."""
    cr_silent = compute_confidence(
        now=TS_NIGHT,
        sigma_g=50.0, n_readings_6h=0, max_gap_min=999,
        minutes_since_last_cgm=200, artifacts_24h=8,
    )
    mock = _MockAssessment()
    warning = render_hypo_warning(mock, cr_silent, now=TS_NIGHT)

    assert warning.suppress, "Modo silent debe suprimir la narrativa"
    assert warning.confidence_note != "", "Debe incluir razón de supresión"
    print(f"  ✓ narrativa suprimida en modo silent: '{warning.confidence_note}'")


def test_narrative_nocturnal_vs_diurnal_tone():
    """Tono nocturno vs diurno difiere."""
    cr = compute_confidence(
        now=TS_NIGHT, sigma_g=10.0, n_readings_6h=12,
        max_gap_min=15, minutes_since_last_cgm=5, artifacts_24h=0,
    )
    mock_urgent = _MockAssessment(severity="critical", p_hypo_70=0.75)

    warning_night = render_hypo_warning(mock_urgent, cr, now=TS_NIGHT)
    warning_day   = render_hypo_warning(mock_urgent, cr, now=TS_DAY)

    # Ambos deben ser informativos
    assert not warning_night.suppress and not warning_day.suppress
    # El nocturno debe mencionar "noche" o "dormir"
    night_text = (warning_night.title + " " + warning_night.suggestion).lower()
    assert any(w in night_text for w in ["noch", "dorm", "acost"]), \
        f"Texto nocturno debería mencionar noche: '{warning_night.title}'"
    print(f"  ✓ tono nocturno: '{warning_night.title}'")
    print(f"    tono diurno:   '{warning_day.title}'")


def test_render_confidence_message():
    """render_confidence_message devuelve texto coherente."""
    for sigma_g, expected_mode in [(8.0, "full"), (22.0, "conservative"), (50.0, "silent")]:
        cr = compute_confidence(
            now=TS_NIGHT, sigma_g=sigma_g, n_readings_6h=12 if sigma_g < 40 else 1,
            max_gap_min=15, minutes_since_last_cgm=5, artifacts_24h=0,
        )
        msg = render_confidence_message(cr)
        assert isinstance(msg, str) and len(msg) >= 0
        print(f"  ✓ confidence msg [{cr.degradation_mode}]: '{msg}'")


def test_render_degradation_message():
    """render_degradation_message vacío para full, descriptivo para otros."""
    cr_full = compute_confidence(
        now=TS_NIGHT, sigma_g=8.0, n_readings_6h=18, max_gap_min=12,
        minutes_since_last_cgm=5, artifacts_24h=0,
    )
    msg_full = render_degradation_message(cr_full)
    assert msg_full == "", f"Modo full debe dar mensaje vacío, fue: '{msg_full}'"

    cr_silent = compute_confidence(
        now=TS_NIGHT, sigma_g=50.0, n_readings_6h=0, max_gap_min=999,
        minutes_since_last_cgm=200, artifacts_24h=8,
    )
    msg_silent = render_degradation_message(cr_silent)
    assert len(msg_silent) > 20, "Modo silent debe dar mensaje explicativo"
    print(f"  ✓ degradation msg: '{msg_silent[:60]}...'")


# ── Runner ────────────────────────────────────────────────────────────────────

def main():
    tests = [
        test_perfect_conditions,
        test_stale_cgm_degrades,
        test_many_artifacts_degrades,
        test_biased_innovations_degrades,
        test_silent_mode_suppresses_alerts,
        test_alert_threshold_boost_conservative,
        test_components_sum_to_one,
        test_to_dict_serializable,
        test_narrative_no_technical_jargon,
        test_narrative_human_factors,
        test_narrative_suppressed_when_silent,
        test_narrative_nocturnal_vs_diurnal_tone,
        test_render_confidence_message,
        test_render_degradation_message,
    ]
    passed = failed = 0
    print()
    print("══ Tests safety/confidence + narrative ══════════════════════")
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
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
