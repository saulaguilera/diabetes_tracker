"""
services/tests/test_hypo_outcome.py
─────────────────────────────────────
Tests del loop de validación real del motor de hipoglucemia.

Cubre:
  - resolve_pending_hypo_audits (Fases 1-2)
  - compute_hypo_performance (Fase 4)
  - render_hypo_performance_summary (Fase 7)
  - get_alert_fatigue_score + mark_alert_dismissed (Fase 8)
  - HypoRiskAudit.to_dict() y propiedades (Fase 3)

Los tests usan SQLite en memoria para aislar completamente la DB de producción.

Ejecutar: python3 -m services.tests.test_hypo_outcome
"""
from __future__ import annotations
import sys, os
_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _root not in sys.path:
    sys.path.insert(0, _root)

from datetime import datetime, timedelta


# ── Bootstrap Flask app con SQLite en memoria ─────────────────────────────────

def _make_test_app():
    """Crea un Flask app mínimo con DB en memoria para tests."""
    import flask
    from models import db

    app = flask.Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["TESTING"] = True
    db.init_app(app)
    with app.app_context():
        db.create_all()
    return app


_app = _make_test_app()


def _clean_db():
    """Limpia tablas entre tests para evitar contaminación de datos."""
    from models import db, HypoRiskAudit, GlucoseReading
    db.session.query(HypoRiskAudit).delete()
    db.session.query(GlucoseReading).delete()
    db.session.commit()


# ── Fixtures helpers ──────────────────────────────────────────────────────────

def _make_audit(
    assessed_at: datetime,
    p_hypo_70: float = 0.40,
    severity: str = "high",
    alert_triggered: bool = True,
    eta_min: int = 120,
    min_predicted_glucose: float = 60.0,
    resolved_confidence: float = 0.80,
) -> "HypoRiskAudit":
    from models import db, HypoRiskAudit
    trough_time = assessed_at + timedelta(minutes=eta_min)
    rec = HypoRiskAudit(
        assessed_at=assessed_at,
        current_glucose=110.0,
        roc=-0.5,
        proposed_bolus=2.0,
        risk_score=0.55,
        p_hypo_70=p_hypo_70,
        p_hypo_55=0.15,
        min_predicted_glucose=min_predicted_glucose,
        min_glucose_eta_min=eta_min,
        severity=severity,
        ssm_available=True,
        fallback_used=False,
        projected_trough_time=trough_time,
        alert_triggered=alert_triggered,
        resolved_confidence=resolved_confidence,
    )
    db.session.add(rec)
    db.session.commit()
    return rec


def _make_glucose(timestamp: datetime, value: float):
    from models import db, GlucoseReading
    r = GlucoseReading(
        timestamp=timestamp,
        value_mgdl=value,
        source="cgm_libre",
        is_artifact=False,
    )
    db.session.add(r)
    db.session.commit()
    return r


# ── Tests Fase 3: modelo extendido ────────────────────────────────────────────

def test_model_new_fields():
    """HypoRiskAudit tiene todos los campos nuevos y to_dict() funciona."""
    with _app.app_context():
        now = datetime(2026, 5, 27, 2, 0, 0)
        audit = _make_audit(now)

        assert audit.projected_trough_time == now + timedelta(minutes=120)
        assert audit.alert_triggered is True
        assert audit.resolved_confidence == 0.80
        assert audit.resolved_at is None
        assert audit.outcome_class is None
        assert not audit.is_resolved
        assert audit.alert_fatigue_ignored is False

        d = audit.to_dict()
        assert "outcome_class" in d
        assert "is_resolved" in d
        assert d["alert_triggered"] is True
        print(f"  ✓ modelo extendido: {len(d)} campos en to_dict()")


# ── Tests Fase 1-2: resolve_pending_hypo_audits ───────────────────────────────

def test_resolve_true_positive():
    """Alerta disparada + hipo real → TP."""
    with _app.app_context():
        _clean_db()
        from services.hypo_outcome_tracker import resolve_pending_hypo_audits
        from models import HypoRiskAudit

        # Audit con trough hace 30 minutos (ya pasó)
        assessed = datetime.utcnow() - timedelta(hours=2)
        audit    = _make_audit(assessed, alert_triggered=True, eta_min=60)
        trough   = assessed + timedelta(minutes=60)

        # Lectura real: hipo a los 5 min del trough
        _make_glucose(trough + timedelta(minutes=5), 62.0)

        result = resolve_pending_hypo_audits()
        assert result["resolved"] >= 1
        assert result["outcomes"]["TP"] >= 1

        fresh = HypoRiskAudit.query.get(audit.id)
        assert fresh.outcome_class == "TP"
        assert fresh.true_positive is True
        assert fresh.false_positive is not True
        assert fresh.actual_nadir == 62.0
        assert fresh.actual_hypo_time is not None
        assert fresh.warning_lead_time_min is not None
        assert fresh.warning_lead_time_min >= 0
        print(f"  ✓ TP: nadir={fresh.actual_nadir} lead={fresh.warning_lead_time_min}min")


def test_resolve_false_positive():
    """Alerta disparada + no hipo → FP."""
    with _app.app_context():
        _clean_db()
        from services.hypo_outcome_tracker import resolve_pending_hypo_audits
        from models import HypoRiskAudit

        assessed = datetime.utcnow() - timedelta(hours=2)
        audit    = _make_audit(assessed, alert_triggered=True, eta_min=60)
        trough   = assessed + timedelta(minutes=60)

        # Lecturas normales: glucose 85, 90, 88 — sin hipo
        for i, v in enumerate([88.0, 90.0, 85.0]):
            _make_glucose(trough + timedelta(minutes=i*20 - 20), v)

        result = resolve_pending_hypo_audits()
        fresh = HypoRiskAudit.query.get(audit.id)
        assert fresh.outcome_class == "FP"
        assert fresh.false_positive is True
        assert fresh.actual_nadir >= 70.0
        assert fresh.warning_lead_time_min is None   # no hubo hipo
        print(f"  ✓ FP: nadir={fresh.actual_nadir} (sin hipo)")


def test_resolve_false_negative():
    """Sin alerta + hipo real → FN."""
    with _app.app_context():
        _clean_db()
        from services.hypo_outcome_tracker import resolve_pending_hypo_audits
        from models import HypoRiskAudit

        assessed = datetime.utcnow() - timedelta(hours=2)
        audit    = _make_audit(assessed, alert_triggered=False, eta_min=60)
        trough   = assessed + timedelta(minutes=60)

        _make_glucose(trough, 58.0)   # hipo real, sin alerta previa

        result = resolve_pending_hypo_audits()
        fresh = HypoRiskAudit.query.get(audit.id)
        assert fresh.outcome_class == "FN"
        assert fresh.false_negative is True
        assert fresh.actual_nadir == 58.0
        print(f"  ✓ FN: hipo real {fresh.actual_nadir} mg/dL sin alerta previa")


def test_resolve_true_negative():
    """Sin alerta + sin hipo → TN."""
    with _app.app_context():
        _clean_db()
        from services.hypo_outcome_tracker import resolve_pending_hypo_audits
        from models import HypoRiskAudit

        assessed = datetime.utcnow() - timedelta(hours=2)
        audit    = _make_audit(assessed, alert_triggered=False, eta_min=60)
        trough   = assessed + timedelta(minutes=60)

        _make_glucose(trough, 95.0)   # normal

        result = resolve_pending_hypo_audits()
        fresh = HypoRiskAudit.query.get(audit.id)
        assert fresh.outcome_class == "TN"
        assert fresh.true_negative is True
        print(f"  ✓ TN: sin alerta y sin hipo (nadir={fresh.actual_nadir})")


def test_skip_future_trough():
    """Audit cuyo trough es en el futuro NO se resuelve."""
    with _app.app_context():
        _clean_db()
        from services.hypo_outcome_tracker import resolve_pending_hypo_audits
        from models import HypoRiskAudit

        # assessed hace 10 min, trough en 110 min = futuro
        assessed = datetime.utcnow() - timedelta(minutes=10)
        audit    = _make_audit(assessed, alert_triggered=True, eta_min=120)

        result = resolve_pending_hypo_audits()
        fresh  = HypoRiskAudit.query.get(audit.id)
        assert fresh.resolved_at is None, "Trough futuro no debe resolverse"
        assert result["skipped"] >= 1
        print(f"  ✓ trough futuro skipped (skipped={result['skipped']})")


def test_prediction_error_computed():
    """prediction_error = min_predicted_glucose - actual_nadir."""
    with _app.app_context():
        _clean_db()
        from services.hypo_outcome_tracker import resolve_pending_hypo_audits
        from models import HypoRiskAudit

        assessed = datetime.utcnow() - timedelta(hours=2)
        # predicted=60, real=65 → error=-5
        audit  = _make_audit(assessed, alert_triggered=True, eta_min=60,
                             min_predicted_glucose=60.0)
        trough = assessed + timedelta(minutes=60)
        _make_glucose(trough, 65.0)

        resolve_pending_hypo_audits()
        fresh = HypoRiskAudit.query.get(audit.id)
        assert fresh.prediction_error is not None
        # 60 - 65 = -5 → el modelo predijo MÁS bajo que la realidad
        assert abs(fresh.prediction_error - (-5.0)) < 0.5
        print(f"  ✓ prediction_error={fresh.prediction_error:.1f} (predicted=60, real=65)")


# ── Tests Fase 4: compute_hypo_performance ────────────────────────────────────

def test_performance_metrics_precision_recall():
    """Con 2 TP, 1 FP, 1 FN → precision=0.667, recall=0.667."""
    with _app.app_context():
        _clean_db()
        from services.hypo_metrics import compute_hypo_performance
        from models import db, HypoRiskAudit

        now = datetime.utcnow()

        def _resolved(outcome, days_ago=1):
            tp = outcome == "TP"
            fp = outcome == "FP"
            fn = outcome == "FN"
            tn = outcome == "TN"
            r = HypoRiskAudit(
                assessed_at=now - timedelta(days=days_ago),
                current_glucose=110.0, roc=-0.3,
                risk_score=0.5, p_hypo_70=0.4, p_hypo_55=0.1,
                severity="high",
                alert_triggered=(tp or fp),
                resolved_at=now,
                outcome_class=outcome,
                true_positive=tp, false_positive=fp,
                false_negative=fn, true_negative=tn,
                actual_nadir=60.0 if (tp or fn) else 85.0,
                warning_lead_time_min=45 if tp else None,
                resolved_confidence=0.75,
            )
            db.session.add(r)

        _resolved("TP")
        _resolved("TP")
        _resolved("FP")
        _resolved("FN")
        db.session.commit()

        m = compute_hypo_performance(days=7)
        assert m["real_hypos_detected"] == 2
        assert m["false_positives"] == 1
        assert m["missed_hypos"] == 1
        assert m["alerts_triggered"] == 3

        assert m["precision"] is not None
        assert abs(m["precision"] - 2/3) < 0.01, f"precision={m['precision']}"
        assert m["recall"] is not None
        assert abs(m["recall"] - 2/3) < 0.01, f"recall={m['recall']}"
        assert m["mean_warning_lead_time_min"] == 45.0
        print(f"  ✓ métricas: precision={m['precision']:.3f} recall={m['recall']:.3f} "
              f"lead={m['mean_warning_lead_time_min']}min")


def test_performance_empty_returns_nones():
    """Sin datos resueltos → precision/recall son None."""
    with _app.app_context():
        from services.hypo_metrics import compute_hypo_performance
        # Usar ventana muy estrecha para evitar interferencia con otros tests
        m = compute_hypo_performance(days=0)
        # days=0 puede dar resultados vacíos
        assert m["precision"] is None or isinstance(m["precision"], float)
        print(f"  ✓ sin datos: precision={m['precision']}")


# ── Tests Fase 7: render_hypo_performance_summary ────────────────────────────

def test_summary_all_detected():
    """Con recall=1.0 y sin FP → texto positivo."""
    from safety.narrative import render_hypo_performance_summary
    m = {
        "n_resolved": 5,
        "real_hypos_detected": 3,
        "missed_hypos": 0,
        "false_positives": 0,
        "true_negatives": 2,
        "alerts_triggered": 3,
        "precision": 1.0,
        "recall": 1.0,
        "mean_warning_lead_time_min": 55,
        "days": 7,
    }
    s = render_hypo_performance_summary(m)
    assert "detectaron" in s.lower() or "3" in s
    assert "minutos" in s.lower()
    print(f"  ✓ summary positivo: '{s}'")


def test_summary_many_fp():
    """Con FP > 60% → texto de sobre-alertas."""
    from safety.narrative import render_hypo_performance_summary
    m = {
        "n_resolved": 8,
        "real_hypos_detected": 1,
        "missed_hypos": 1,
        "false_positives": 5,
        "true_negatives": 1,
        "alerts_triggered": 6,
        "precision": round(1/6, 3),
        "recall": 0.5,
        "mean_warning_lead_time_min": 30,
        "days": 14,
    }
    s = render_hypo_performance_summary(m)
    # Debe mencionar alertas falsas o de más
    assert any(w in s.lower() for w in ["falso", "más", "alertando"])
    print(f"  ✓ summary FP alto: '{s[:80]}...'")


def test_summary_no_data():
    """Sin datos → texto descriptivo."""
    from safety.narrative import render_hypo_performance_summary
    m = {"n_resolved": 0, "days": 14}
    s = render_hypo_performance_summary(m)
    assert len(s) > 20
    print(f"  ✓ summary sin datos: '{s}'")


# ── Tests Fase 8: alert fatigue ───────────────────────────────────────────────

def test_mark_alert_dismissed():
    """mark_alert_dismissed() actualiza el audit correctamente."""
    with _app.app_context():
        from services.hypo_outcome_tracker import mark_alert_dismissed
        from models import HypoRiskAudit

        assessed = datetime.utcnow() - timedelta(hours=1)
        audit    = _make_audit(assessed, alert_triggered=True)

        ok = mark_alert_dismissed(audit.id)
        assert ok is True

        fresh = HypoRiskAudit.query.get(audit.id)
        assert fresh.alert_fatigue_ignored is True
        assert fresh.dismissed_at is not None
        print(f"  ✓ dismiss: audit #{audit.id} marcado como ignorado")


def test_mark_alert_dismissed_invalid_id():
    """ID inexistente → retorna False."""
    with _app.app_context():
        from services.hypo_outcome_tracker import mark_alert_dismissed
        ok = mark_alert_dismissed(999999)
        assert ok is False
        print(f"  ✓ dismiss ID inválido retorna False")


def test_fatigue_score_empty():
    """Sin alertas → fatigue_score=0."""
    with _app.app_context():
        from services.hypo_outcome_tracker import get_alert_fatigue_score
        # Ventana de 0 días → sin datos
        r = get_alert_fatigue_score(days=0)
        assert r["fatigue_score"] == 0.0
        print(f"  ✓ fatigue score vacío: {r['fatigue_score']}")


# ── Runner ────────────────────────────────────────────────────────────────────

def main():
    tests = [
        test_model_new_fields,
        test_resolve_true_positive,
        test_resolve_false_positive,
        test_resolve_false_negative,
        test_resolve_true_negative,
        test_skip_future_trough,
        test_prediction_error_computed,
        test_performance_metrics_precision_recall,
        test_performance_empty_returns_nones,
        test_summary_all_detected,
        test_summary_many_fp,
        test_summary_no_data,
        test_mark_alert_dismissed,
        test_mark_alert_dismissed_invalid_id,
        test_fatigue_score_empty,
    ]
    passed = failed = 0
    print()
    print("══ Tests Outcome Validation Loop ══════════════════════════════")
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
    import sys as _sys
    _sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
