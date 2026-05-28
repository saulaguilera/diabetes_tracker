"""
services/hypo_outcome_tracker.py
─────────────────────────────────
Fases 1-2: Resolución automática de HypoRiskAudit comparando predicción vs realidad.

Ciclo de vida de un audit:
  assessed_at  → motor genera predicción + trough_time + alert_triggered
  [tiempo pasa, lecturas CGM llegan via LibreLinkUp sync]
  resolve_pending_hypo_audits() → busca lecturas en ventana, clasifica outcome

Definición de outcomes
─────────────────────
  TP (True Positive):  alert_triggered=True  + hubo lectura < 70 en ventana
  FP (False Positive): alert_triggered=True  + NO hubo lectura < 70 en ventana
  FN (False Negative): alert_triggered=False + hubo lectura < 70 en ventana
  TN (True Negative):  alert_triggered=False + NO hubo lectura < 70 en ventana

Ventana de observación: projected_trough_time ± 90 min.
Si no hay projected_trough_time, se usa assessed_at + eta_min, y si tampoco
hay eta_min se usa assessed_at + 240 min como ventana por defecto.

Ejecutar: llamado automáticamente por _do_libre_sync() en blueprints/sync.py
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# ── Constantes ─────────────────────────────────────────────────────────────────

OBSERVATION_WINDOW_MIN    = 90     # ± minutos alrededor del trough proyectado
HYPO_THRESHOLD_MGDL       = 70.0   # umbral clínico nivel 1
DEFAULT_ETA_MIN           = 240    # si no hay eta_min, asumir 4h
MAX_LOOKBACK_HOURS        = 12     # solo resolver audits de las últimas N horas
MIN_READINGS_FOR_RESOLVE  = 1      # mínimo 1 lectura en la ventana para resolver


# ── Función principal ─────────────────────────────────────────────────────────

def resolve_pending_hypo_audits() -> dict:
    """
    Busca todos los HypoRiskAudit sin resolver cuyo projected_trough_time
    ya pasó, y los resuelve comparando con lecturas CGM reales.

    Retorna:
        {
          "resolved": int,          # cuántos audits se resolvieron
          "skipped":  int,          # cuántos no tenían suficientes datos CGM
          "errors":   int,          # cuántos fallaron (silenciados)
          "outcomes": {"TP": n, "FP": n, "FN": n, "TN": n}
        }
    """
    from models import db, HypoRiskAudit, GlucoseReading

    now = datetime.now()
    cutoff_old = now - timedelta(hours=MAX_LOOKBACK_HOURS)

    # Audits sin resolver, con trough ya pasado, dentro de la ventana de 12h
    pending = (
        HypoRiskAudit.query
        .filter(
            HypoRiskAudit.resolved_at == None,                    # noqa: E711
            HypoRiskAudit.assessed_at >= cutoff_old,
        )
        .order_by(HypoRiskAudit.assessed_at)
        .all()
    )

    resolved = skipped = errors = 0
    outcome_counts: dict[str, int] = {"TP": 0, "FP": 0, "FN": 0, "TN": 0}

    for audit in pending:
        try:
            trough_time = _get_trough_time(audit)

            # Solo resolver si el trough ya pasó (o si pasó más de 60 min del assess)
            earliest_resolve = trough_time + timedelta(minutes=15)
            if now < earliest_resolve:
                skipped += 1
                continue

            # Ventana de observación
            win_start = trough_time - timedelta(minutes=OBSERVATION_WINDOW_MIN)
            win_end   = trough_time + timedelta(minutes=OBSERVATION_WINDOW_MIN)

            # Lecturas CGM reales en la ventana (excluir artefactos)
            readings = (
                GlucoseReading.query
                .filter(
                    GlucoseReading.timestamp >= win_start,
                    GlucoseReading.timestamp <= win_end,
                    GlucoseReading.is_artifact == False,          # noqa: E712
                )
                .order_by(GlucoseReading.timestamp)
                .all()
            )

            if len(readings) < MIN_READINGS_FOR_RESOLVE:
                skipped += 1
                continue

            # Nadir real y primera hipo
            values        = [r.value_mgdl for r in readings]
            actual_nadir  = min(values)
            had_hypo      = actual_nadir < HYPO_THRESHOLD_MGDL

            actual_hypo_time = None
            if had_hypo:
                for r in readings:
                    if r.value_mgdl < HYPO_THRESHOLD_MGDL:
                        actual_hypo_time = r.timestamp
                        break

            # Lead time: minutos entre el momento de la alerta y la primera hipo real
            lead_time_min = None
            if had_hypo and actual_hypo_time and audit.alert_triggered:
                delta = (actual_hypo_time - audit.assessed_at).total_seconds() / 60.0
                lead_time_min = max(0, int(delta))

            # Prediction error (signed): positivo = el modelo predijo más bajo que real
            prediction_error = None
            if audit.min_predicted_glucose is not None:
                prediction_error = round(audit.min_predicted_glucose - actual_nadir, 1)

            # Clasificar outcome
            alert = bool(audit.alert_triggered)
            if alert and had_hypo:
                outcome = "TP"
            elif alert and not had_hypo:
                outcome = "FP"
            elif not alert and had_hypo:
                outcome = "FN"
            else:
                outcome = "TN"

            # Persistir
            audit.resolved_at     = now
            audit.outcome_class   = outcome
            audit.true_positive   = (outcome == "TP")
            audit.false_positive  = (outcome == "FP")
            audit.false_negative  = (outcome == "FN")
            audit.true_negative   = (outcome == "TN")
            audit.actual_nadir    = round(actual_nadir, 1)
            audit.actual_hypo_time      = actual_hypo_time
            audit.prediction_error      = prediction_error
            audit.warning_lead_time_min = lead_time_min

            db.session.add(audit)
            resolved += 1
            outcome_counts[outcome] += 1

            logger.info(
                "hypo_outcome: resolved audit #%d → %s  "
                "(nadir=%.0f alert=%s lead=%s min)",
                audit.id, outcome, actual_nadir,
                alert, lead_time_min,
            )

        except Exception as exc:
            logger.warning(
                "hypo_outcome: failed to resolve audit #%s — %s",
                getattr(audit, "id", "?"), exc,
            )
            errors += 1

    if resolved:
        try:
            db.session.commit()
        except Exception as exc:
            logger.error("hypo_outcome: commit failed — %s", exc)
            db.session.rollback()
            return {"resolved": 0, "skipped": skipped, "errors": errors + resolved,
                    "outcomes": {"TP": 0, "FP": 0, "FN": 0, "TN": 0}}

    return {
        "resolved": resolved,
        "skipped":  skipped,
        "errors":   errors,
        "outcomes": outcome_counts,
    }


# ── Alert fatigue ──────────────────────────────────────────────────────────────

def mark_alert_dismissed(audit_id: int) -> bool:
    """
    Marca un audit como ignorado por el usuario (alert fatigue tracking).
    Llamado desde el endpoint /api/hypo-risk/dismiss.
    Retorna True si se encontró y actualizó, False si no existe.
    """
    from models import db, HypoRiskAudit

    audit = HypoRiskAudit.query.get(audit_id)
    if audit is None:
        return False

    audit.alert_fatigue_ignored = True
    audit.dismissed_at = datetime.now()
    try:
        db.session.commit()
        return True
    except Exception as exc:
        logger.warning("hypo_outcome: dismiss commit failed — %s", exc)
        db.session.rollback()
        return False


def get_alert_fatigue_score(days: int = 14) -> dict:
    """
    Calcula un score de fatiga de alertas sobre los últimos N días.

    Retorna:
        {
          "fatigue_score": float 0-1,  # fracción de alertas ignoradas sin hipo
          "total_alerts":  int,
          "ignored":       int,
          "ignored_fp":    int,        # ignoradas Y eran falsos positivos
          "days":          int,
        }
    """
    from models import HypoRiskAudit

    cutoff = datetime.now() - timedelta(days=days)
    alerts = (
        HypoRiskAudit.query
        .filter(
            HypoRiskAudit.assessed_at >= cutoff,
            HypoRiskAudit.alert_triggered == True,               # noqa: E712
        )
        .all()
    )

    total   = len(alerts)
    ignored = sum(1 for a in alerts if a.alert_fatigue_ignored)
    # Ignoradas y clasificadas como FP (lo peor para fatiga)
    ignored_fp = sum(1 for a in alerts if a.alert_fatigue_ignored and a.false_positive)

    fatigue_score = round(ignored / total, 3) if total > 0 else 0.0

    return {
        "fatigue_score": fatigue_score,
        "total_alerts":  total,
        "ignored":       ignored,
        "ignored_fp":    ignored_fp,
        "days":          days,
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_trough_time(audit) -> datetime:
    """
    Devuelve el projected_trough_time del audit.
    Fallback: assessed_at + eta_min, o assessed_at + DEFAULT_ETA_MIN.
    """
    if audit.projected_trough_time is not None:
        return audit.projected_trough_time

    eta = audit.min_glucose_eta_min or DEFAULT_ETA_MIN
    return audit.assessed_at + timedelta(minutes=eta)
