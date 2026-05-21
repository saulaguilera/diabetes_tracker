"""
bench/metrics/clinical.py
──────────────────────────
Métricas clínicamente relevantes — más importantes que MAE para producto.

Hypoglycemia detection
----------------------
Una predicción "buena" no es la que tiene MAE bajo: es la que **detecta hipos
antes que ocurran** con anticipación suficiente para actuar (≥15min).

Métricas:
  - hypo_recall:     de las hipos REALES, ¿cuántas se anticiparon?
  - false_alarm_rate: alertas que NO terminaron en hipo, por día
  - anticipation_time: minutos entre alerta y hipo real
  - severity_capture: para hipos severas (<54), ¿se detectaron mejor?

Time-in-range delta
-------------------
TIR alteracion: complejo de medir sin contrafactual. MVP: solo reportar
TIR observado en el periodo evaluado.

Hyperglycemia
-------------
Simétrico al hypo pero menos urgente clínicamente (efecto a horas, no minutos).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable

from bench.replay import PredictionRecord


# ── Umbrales clínicos estándar ──────────────────────────────────────────
HYPO_THRESHOLD       = 70.0    # mg/dL — hipoglucemia leve
HYPO_SEVERE          = 54.0    # mg/dL — hipoglucemia severa (ADA L2)
HYPER_THRESHOLD      = 180.0   # mg/dL — hiperglucemia
HYPER_SEVERE         = 250.0
HYPO_ALERT_PROB      = 0.30    # p_hipo para considerar "alerta emitida"

# Ventana para considerar una alerta válida (anticipation)
ALERT_WINDOW_MIN     = 30      # alerta a t puede contar para hipo en t..t+30min


# ── Hypo detection metrics ──────────────────────────────────────────────

def _p_hypo_predicted(r: PredictionRecord) -> float:
    """
    Estima P(G_pred < 70) bajo N(g_pred, sigma²).
    Sin sigma, devuelve 1.0 si g_pred < 70, else 0.0 (degeneración a threshold).
    """
    if r.sigma is None or r.sigma <= 0:
        return 1.0 if r.g_pred < HYPO_THRESHOLD else 0.0
    import math
    z = (HYPO_THRESHOLD - r.g_pred) / r.sigma
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def hypo_metrics(records: list[PredictionRecord], horizon_min: int = 30) -> dict:
    """
    Calcula recall, false alarm rate y anticipación para detección de hipo
    a un horizonte específico.

    Estrategia
    ----------
    - Cada record es una predicción (predicted_at, g_pred, g_real_at_t+horizon).
    - "Hipo real" = g_real < 70 en t+horizon.
    - "Alerta emitida" = P(g_pred < 70) >= HYPO_ALERT_PROB.

    Returns
    -------
    {
        "n_predictions":     int,
        "n_real_hypos":      int,
        "n_alerts":          int,
        "n_true_positives":  int,
        "n_false_alarms":    int,
        "n_missed":          int,
        "recall":            float,    # TP / (TP + FN)
        "precision":         float,    # TP / (TP + FP)
        "false_alarm_rate_per_day": float,
        "severity_recall":   float,    # recall sobre hipos <54
    }
    """
    horizon_records = [r for r in records if r.horizon_min == horizon_min]
    if not horizon_records:
        return _empty_hypo_metrics()

    n_real_hypos    = 0
    n_alerts        = 0
    n_true_pos      = 0
    n_severe_real   = 0
    n_severe_caught = 0

    for r in horizon_records:
        is_real_hypo   = r.g_real < HYPO_THRESHOLD
        is_severe_hypo = r.g_real < HYPO_SEVERE
        is_alert       = _p_hypo_predicted(r) >= HYPO_ALERT_PROB

        if is_real_hypo:   n_real_hypos += 1
        if is_severe_hypo: n_severe_real += 1
        if is_alert:       n_alerts += 1
        if is_real_hypo and is_alert: n_true_pos += 1
        if is_severe_hypo and is_alert: n_severe_caught += 1

    n_false_alarms = n_alerts - n_true_pos
    n_missed       = n_real_hypos - n_true_pos

    # Frecuencia de false alarms por día
    if horizon_records:
        span_days = max(
            1.0,
            (horizon_records[-1].predicted_at -
             horizon_records[0].predicted_at).total_seconds() / 86400.0
        )
    else:
        span_days = 1.0

    return {
        "n_predictions":          len(horizon_records),
        "n_real_hypos":           n_real_hypos,
        "n_alerts":               n_alerts,
        "n_true_positives":       n_true_pos,
        "n_false_alarms":         n_false_alarms,
        "n_missed":               n_missed,
        "recall":                 round(n_true_pos / n_real_hypos, 3) if n_real_hypos else None,
        "precision":              round(n_true_pos / n_alerts,     3) if n_alerts     else None,
        "false_alarm_rate_per_day": round(n_false_alarms / span_days, 2),
        "n_severe_real":          n_severe_real,
        "severity_recall":        round(n_severe_caught / n_severe_real, 3) if n_severe_real else None,
        "span_days":              round(span_days, 1),
        "alert_threshold_p_hypo": HYPO_ALERT_PROB,
        "horizon_min":            horizon_min,
    }


def _empty_hypo_metrics() -> dict:
    return {
        "n_predictions": 0, "n_real_hypos": 0, "n_alerts": 0,
        "n_true_positives": 0, "n_false_alarms": 0, "n_missed": 0,
        "recall": None, "precision": None,
        "false_alarm_rate_per_day": None,
        "severity_recall": None,
    }


# ── Hyper detection (simétrico) ──────────────────────────────────────────

def hyper_metrics(records: list[PredictionRecord], horizon_min: int = 30) -> dict:
    """Análogo a hypo_metrics pero para hiperglucemia."""
    horizon_records = [r for r in records if r.horizon_min == horizon_min]
    if not horizon_records:
        return _empty_hypo_metrics()

    n_real, n_alerts, n_tp = 0, 0, 0
    for r in horizon_records:
        real  = r.g_real > HYPER_THRESHOLD
        alert = r.g_pred > HYPER_THRESHOLD     # simple threshold (no requiere σ)
        if real:  n_real   += 1
        if alert: n_alerts += 1
        if real and alert: n_tp += 1

    return {
        "n_predictions":      len(horizon_records),
        "n_real_hypers":      n_real,
        "n_alerts":           n_alerts,
        "n_true_positives":   n_tp,
        "recall":             round(n_tp / n_real, 3)   if n_real   else None,
        "precision":          round(n_tp / n_alerts, 3) if n_alerts else None,
        "horizon_min":        horizon_min,
    }


# ── TIR observado en el periodo ──────────────────────────────────────────

def observed_tir(records: list[PredictionRecord]) -> dict:
    """
    Time-in-range sobre las glucemias reales del periodo evaluado.
    Útil para contexto: si TIR es 90%, las hipos son raras y MAE alto duele menos.
    """
    if not records:
        return {"n": 0}
    n = len(records)
    n_hypo  = sum(1 for r in records if r.g_real < HYPO_THRESHOLD)
    n_low   = sum(1 for r in records if HYPO_THRESHOLD <= r.g_real < 70)  # redundante
    n_tir   = sum(1 for r in records if HYPO_THRESHOLD <= r.g_real <= HYPER_THRESHOLD)
    n_hyper = sum(1 for r in records if HYPER_THRESHOLD < r.g_real)
    n_severe_hyper = sum(1 for r in records if r.g_real > HYPER_SEVERE)
    n_severe_hypo  = sum(1 for r in records if r.g_real < HYPO_SEVERE)
    return {
        "n":                 n,
        "tir_pct":           round(n_tir   / n * 100, 1),
        "hypo_pct":          round(n_hypo  / n * 100, 1),
        "hyper_pct":         round(n_hyper / n * 100, 1),
        "severe_hypo_pct":   round(n_severe_hypo  / n * 100, 2),
        "severe_hyper_pct":  round(n_severe_hyper / n * 100, 2),
    }


# ── Resumen clínico ──────────────────────────────────────────────────────

def clinical_summary(records: list[PredictionRecord]) -> dict:
    return {
        "tir":              observed_tir(records),
        "hypo_+30min":      hypo_metrics(records, horizon_min=30),
        "hypo_+60min":      hypo_metrics(records, horizon_min=60),
        "hyper_+30min":     hyper_metrics(records, horizon_min=30),
        "hyper_+60min":     hyper_metrics(records, horizon_min=60),
    }
