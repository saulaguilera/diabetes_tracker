"""
services/hypo_metrics.py
─────────────────────────
Fase 4: Métricas de performance del motor de riesgo de hipoglucemia.

Agrega los HypoRiskAudit resueltos para calcular:
  - Precision, Recall (Sensibilidad), FPR, FNR
  - Lead time promedio (minutos de anticipación)
  - Conteos: alertas disparadas, hipos reales detectadas, perdidas

Nota sobre la semántica:
  "Predicado positivo" = el sistema alertó (alert_triggered=True)
  "Positivo real"      = hubo hipo real (true_positive o false_negative)
  precision = TP / (TP + FP)    ← ¿cuándo alerta, acierta?
  recall    = TP / (TP + FN)    ← ¿de las hipos reales, cuántas detectó?
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)


# ── Función principal ─────────────────────────────────────────────────────────

def compute_hypo_performance(days: int = 14) -> dict:
    """
    Calcula métricas de performance sobre los últimos `days` días
    usando únicamente audits RESUELTOS.

    Retorna:
        {
          "days":                       int,
          "n_resolved":                 int,   # audits resueltos en la ventana
          "n_unresolved":               int,   # audits aún pendientes
          "alerts_triggered":           int,   # total alertas disparadas
          "real_hypos_detected":        int,   # TP count
          "missed_hypos":               int,   # FN count
          "false_positives":            int,   # FP count
          "true_negatives":             int,   # TN count
          "precision":                  float | None,
          "recall":                     float | None,
          "false_positive_rate":        float | None,  # FP / (FP + TN)
          "false_negative_rate":        float | None,  # FN / (FN + TP)
          "mean_warning_lead_time_min": float | None,  # solo TPs con lead_time
          "mean_confidence":            float | None,
          "computed_at":                str,
        }
    """
    from models import HypoRiskAudit

    now    = datetime.now()
    cutoff = now - timedelta(days=days)

    all_in_window = (
        HypoRiskAudit.query
        .filter(HypoRiskAudit.assessed_at >= cutoff)
        .all()
    )

    resolved   = [a for a in all_in_window if a.resolved_at is not None]
    unresolved = [a for a in all_in_window if a.resolved_at is None]

    # Conteos básicos
    tp = sum(1 for a in resolved if a.true_positive)
    fp = sum(1 for a in resolved if a.false_positive)
    fn = sum(1 for a in resolved if a.false_negative)
    tn = sum(1 for a in resolved if a.true_negative)

    alerts_triggered = tp + fp   # alertas que se mostraron (resueltas)
    real_hypos       = tp + fn   # hipos reales en la ventana

    # Precision: TP / (TP + FP)
    precision: Optional[float] = None
    if (tp + fp) > 0:
        precision = round(tp / (tp + fp), 3)

    # Recall (sensibilidad): TP / (TP + FN)
    recall: Optional[float] = None
    if (tp + fn) > 0:
        recall = round(tp / (tp + fn), 3)

    # False positive rate: FP / (FP + TN)
    fpr: Optional[float] = None
    if (fp + tn) > 0:
        fpr = round(fp / (fp + tn), 3)

    # False negative rate: FN / (FN + TP)
    fnr: Optional[float] = None
    if (fn + tp) > 0:
        fnr = round(fn / (fn + tp), 3)

    # Lead time: promedio de minutos de anticipación (solo TPs)
    lead_times = [
        a.warning_lead_time_min for a in resolved
        if a.true_positive and a.warning_lead_time_min is not None
    ]
    mean_lead_time: Optional[float] = None
    if lead_times:
        mean_lead_time = round(sum(lead_times) / len(lead_times), 1)

    # Confianza promedio al momento del assessment
    confs = [a.resolved_confidence for a in resolved if a.resolved_confidence is not None]
    mean_confidence: Optional[float] = None
    if confs:
        mean_confidence = round(sum(confs) / len(confs), 3)

    return {
        "days":                       days,
        "n_resolved":                 len(resolved),
        "n_unresolved":               len(unresolved),
        "alerts_triggered":           alerts_triggered,
        "real_hypos_detected":        tp,
        "missed_hypos":               fn,
        "false_positives":            fp,
        "true_negatives":             tn,
        "precision":                  precision,
        "recall":                     recall,
        "false_positive_rate":        fpr,
        "false_negative_rate":        fnr,
        "mean_warning_lead_time_min": mean_lead_time,
        "mean_confidence":            mean_confidence,
        "computed_at":                now.isoformat(),
    }


def get_performance_trend(weeks: int = 4) -> list[dict]:
    """
    Devuelve métricas semana a semana para ver evolución.
    Retorna lista de dicts, una entrada por semana (más reciente primero).
    """
    results = []
    now = datetime.now()

    for week_idx in range(weeks):
        week_end   = now - timedelta(weeks=week_idx)
        week_start = week_end - timedelta(weeks=1)

        from models import HypoRiskAudit
        resolved = (
            HypoRiskAudit.query
            .filter(
                HypoRiskAudit.assessed_at >= week_start,
                HypoRiskAudit.assessed_at < week_end,
                HypoRiskAudit.resolved_at != None,            # noqa: E711
            )
            .all()
        )

        tp = sum(1 for a in resolved if a.true_positive)
        fp = sum(1 for a in resolved if a.false_positive)
        fn = sum(1 for a in resolved if a.false_negative)
        tn = sum(1 for a in resolved if a.true_negative)

        precision = round(tp / (tp + fp), 3) if (tp + fp) > 0 else None
        recall    = round(tp / (tp + fn), 3) if (tp + fn) > 0 else None

        results.append({
            "week_start":  week_start.date().isoformat(),
            "week_end":    week_end.date().isoformat(),
            "n":           len(resolved),
            "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision":   precision,
            "recall":      recall,
        })

    return results
