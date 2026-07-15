"""
services/model_health.py
─────────────────────────
Capa de Model Health & Validation Readiness.

Objetivo: NO mejorar el modelo. Sólo determinar — con datos duros — si el
sistema está generando evidencia limpia y suficiente para poder evaluarlo
después de 7-10 días.

API pública
-----------
- `get_model_health(days=7)`        → dict con status, cobertura, conteos,
                                      blocking_issues, warnings.
- `is_ready_for_bench(days=7)`      → dict con `ready: bool` + razones.
- `log_health_warnings()`           → revisa el estado y emite warnings al
                                      logger 'model_health'. No bloquea.

Principios
----------
- Sólo lectura: jamás muta DB.
- TZ-aware: usa `datetime.now()` (TZ local del servidor, configurada como
  America/Santiago en app.py) — coincide con la TZ de las timestamps en DB.
- Resiliente: cualquier excepción se contiene; nunca rompe un sync ni una
  ruta. Los problemas se reportan como warnings, no como tracebacks.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger("model_health")


# ── Constantes ──────────────────────────────────────────────────────────────

# Frecuencia ideal del CGM Libre (~ cada 5 min → 288 puntos/día).
# El background predictor tira una predicción por sync, throttleada a 4.5 min.
EXPECTED_PREDICTIONS_PER_DAY     = 288
COVERAGE_RATIO_HEALTHY           = 0.75   # ≥75% del esperado → healthy
COVERAGE_RATIO_WARNING           = 0.50   # 50-75% → warning
# < 50% → critical

# Si la última predicción fue hace más que esto, el background predictor
# probablemente no está corriendo.
MAX_AGE_LAST_PREDICTION_MIN      = 20

# Backlog admisible de audits sin resolver
MAX_UNRESOLVED_AUDITS_HEALTHY    = 30
MAX_UNRESOLVED_AUDITS_WARNING    = 100

# Backlog admisible de hypo audits sin outcome (más de 12h)
MAX_UNRESOLVED_HYPO_AUDITS_AGE_H = 12

from pmm.ssm.version import MODEL_VERSION
ACTIVE_MODEL_VERSION             = MODEL_VERSION   # versión activa monitoreada


# ── Helpers ─────────────────────────────────────────────────────────────────

def _safe_count(query) -> int:
    try:
        return int(query.count())
    except Exception as exc:
        logger.debug("count failed: %s", exc)
        return 0


def _safe_first(query):
    try:
        return query.first()
    except Exception as exc:
        logger.debug("first() failed: %s", exc)
        return None


# ── Sub-checks ──────────────────────────────────────────────────────────────

def _coverage_check(days: int) -> dict:
    """
    Cobertura de predicciones del modelo activo en la ventana.
    """
    from models import db, GlucosePrediction

    now    = datetime.now()
    cutoff = now - timedelta(days=days)

    base_q = (db.session.query(GlucosePrediction)
              .filter(GlucosePrediction.model_version == ACTIVE_MODEL_VERSION)
              .filter(GlucosePrediction.predicted_at >= cutoff))

    n_window = _safe_count(base_q)

    n_24h = _safe_count(
        db.session.query(GlucosePrediction)
        .filter(GlucosePrediction.model_version == ACTIVE_MODEL_VERSION)
        .filter(GlucosePrediction.predicted_at >= now - timedelta(hours=24))
    )

    expected_24h    = EXPECTED_PREDICTIONS_PER_DAY
    expected_window = EXPECTED_PREDICTIONS_PER_DAY * days
    coverage_ratio  = round(n_window / expected_window, 3) if expected_window else 0.0

    # Última predicción
    last_pred = _safe_first(
        db.session.query(GlucosePrediction)
        .filter(GlucosePrediction.model_version == ACTIVE_MODEL_VERSION)
        .order_by(GlucosePrediction.predicted_at.desc())
    )
    last_pred_at  = last_pred.predicted_at if last_pred else None
    age_last_min  = ((now - last_pred_at).total_seconds() / 60.0
                     if last_pred_at else None)

    # Cobertura por hora del día (0..23) — usa últimos 7 días
    hour_counts = [0] * 24
    sample = (db.session.query(GlucosePrediction)
              .filter(GlucosePrediction.model_version == ACTIVE_MODEL_VERSION)
              .filter(GlucosePrediction.predicted_at >= now - timedelta(days=min(7, days)))
              .all())
    for p in sample:
        try:
            hour_counts[p.predicted_at.hour] += 1
        except Exception:
            pass

    # missing_hours debe detectar GAPS RELATIVOS (huecos en la distribución),
    # no horas con menos del ideal teórico. Si la cobertura global es 0.5,
    # cada hora individual tiene ~50% del ideal — eso NO es un gap, es la
    # cobertura uniforme del sistema acumulando. Un gap real es cuando una
    # hora específica tiene mucho menos que el promedio observado.
    expected_per_hour_ideal = (EXPECTED_PREDICTIONS_PER_DAY / 24) * min(7, days)
    n_hours_with_data = sum(1 for c in hour_counts if c > 0)
    observed_avg_per_hour = (sum(hour_counts) / n_hours_with_data
                             if n_hours_with_data else 0)
    # Una hora es "missing" si tiene <40% del promedio observado de las otras
    # horas — eso sí es un gap real, no warm-up uniforme.
    missing_hours = (
        [h for h, c in enumerate(hour_counts)
         if c < observed_avg_per_hour * 0.4]
        if observed_avg_per_hour > 0 else list(range(24))
    )

    return {
        "predictions_in_window":   n_window,
        "predictions_24h":         n_24h,
        "expected_24h":            expected_24h,
        "expected_in_window":      expected_window,
        "coverage_ratio":          coverage_ratio,
        "last_prediction_at":      last_pred_at.isoformat() if last_pred_at else None,
        "age_last_pred_min":       round(age_last_min, 1) if age_last_min is not None else None,
        "hourly_counts":           hour_counts,
        "missing_hours":           missing_hours,
        "expected_per_hour_ideal":  round(expected_per_hour_ideal, 1),
        "observed_avg_per_hour":    round(observed_avg_per_hour, 1),
    }


def _audit_check(days: int) -> dict:
    """
    Conteo y backlog de PredictionAudit / SSMInnovation / HypoRiskAudit.
    """
    from models import db, PredictionAudit, SSMInnovation, HypoRiskAudit

    now    = datetime.now()
    cutoff = now - timedelta(days=days)

    n_pred_audit = _safe_count(
        db.session.query(PredictionAudit)
        .filter(PredictionAudit.model_version == ACTIVE_MODEL_VERSION)
        .filter(PredictionAudit.predicted_at >= cutoff)
    )
    n_ssm_innov  = _safe_count(
        db.session.query(SSMInnovation)
        .filter(SSMInnovation.model_version == ACTIVE_MODEL_VERSION)
        .filter(SSMInnovation.ts >= cutoff)
    )
    # Última fila por tabla (para detectar pipeline detenido)
    last_pred_audit = _safe_first(
        db.session.query(PredictionAudit)
        .filter(PredictionAudit.model_version == ACTIVE_MODEL_VERSION)
        .order_by(PredictionAudit.predicted_at.desc())
    )
    last_innov = _safe_first(
        db.session.query(SSMInnovation)
        .filter(SSMInnovation.model_version == ACTIVE_MODEL_VERSION)
        .order_by(SSMInnovation.ts.desc())
    )

    # Backlog de PredictionAudit sin resolver (más viejas que 2h → ya no
    # se pueden resolver con CGMs nuevos, son backlog real)
    backlog_cutoff = now - timedelta(hours=2)
    n_unresolved_old = _safe_count(
        db.session.query(PredictionAudit)
        .filter(PredictionAudit.model_version == ACTIVE_MODEL_VERSION)
        .filter(PredictionAudit.resolved == False)               # noqa: E712
        .filter(PredictionAudit.predicted_at < backlog_cutoff)
        .filter(PredictionAudit.predicted_at >= cutoff)
    )

    # HypoRiskAudit
    n_hypo_total = _safe_count(
        db.session.query(HypoRiskAudit)
        .filter(HypoRiskAudit.assessed_at >= cutoff)
    )
    n_hypo_unresolved = _safe_count(
        db.session.query(HypoRiskAudit)
        .filter(HypoRiskAudit.assessed_at >= cutoff)
        .filter(HypoRiskAudit.resolved_at == None)              # noqa: E711
    )
    # Hypo audits que ya deberían haberse resuelto (>12h sin outcome)
    hypo_stale_cutoff = now - timedelta(hours=MAX_UNRESOLVED_HYPO_AUDITS_AGE_H)
    n_hypo_stale = _safe_count(
        db.session.query(HypoRiskAudit)
        .filter(HypoRiskAudit.assessed_at >= cutoff)
        .filter(HypoRiskAudit.assessed_at < hypo_stale_cutoff)
        .filter(HypoRiskAudit.resolved_at == None)              # noqa: E711
    )
    n_hypo_resolved = n_hypo_total - n_hypo_unresolved

    return {
        "prediction_audits":             n_pred_audit,
        "ssm_innovations":               n_ssm_innov,
        "last_prediction_audit_at":      last_pred_audit.predicted_at.isoformat()
                                          if last_pred_audit else None,
        "last_innovation_at":            last_innov.ts.isoformat()
                                          if last_innov and getattr(last_innov, "ts", None) else None,
        "prediction_audits_unresolved_old": n_unresolved_old,
        "hypo_audits_total":             n_hypo_total,
        "hypo_audits_resolved":          n_hypo_resolved,
        "hypo_audits_unresolved":        n_hypo_unresolved,
        "hypo_audits_stale_12h":         n_hypo_stale,
    }


def _timezone_check() -> dict:
    """
    Verifica consistencia entre la TZ del servidor y la TZ de los datos.

    Chequeos:
    - TZ env var configurada
    - datetime.now() vs datetime.utcnow() — diferencia esperada según TZ
    - Última GlucoseReading.timestamp coherente con datetime.now() (gap < 30min
      durante operación normal)
    """
    import os
    from datetime import datetime as _dt

    tz_env  = os.environ.get("TZ", "(unset)")
    now_l   = _dt.now()
    now_utc = _dt.utcnow()
    delta_h = round((now_utc - now_l).total_seconds() / 3600, 1)

    issues: list[str] = []

    # Si no hay TZ configurada, datetime.now() y utcnow() son iguales — fine
    # pero el sistema asume hora local. Reportar pero no flag crítico.
    if tz_env == "(unset)":
        issues.append("variable de entorno TZ no configurada — sistema asume UTC")

    # CGM recent
    try:
        from models import db, GlucoseReading
        last_cgm = (db.session.query(GlucoseReading)
                    .order_by(GlucoseReading.timestamp.desc())
                    .first())
        cgm_gap_min = ((now_l - last_cgm.timestamp).total_seconds() / 60.0
                       if last_cgm else None)
        cgm_ts      = last_cgm.timestamp.isoformat() if last_cgm else None
    except Exception:
        cgm_gap_min = None
        cgm_ts      = None

    # Si la última CGM es MUY vieja en hora local pero el servidor cree
    # que es ahora, podría haber un desfase de TZ a la inversa.
    # (Sólo reportar como info, no crítico — puede ser que el sensor está down.)
    if cgm_gap_min is not None and cgm_gap_min > 60:
        issues.append(f"última CGM hace {cgm_gap_min:.0f}min — sensor podría estar offline")

    return {
        "tz_env":              tz_env,
        "datetime_now_local":  now_l.isoformat(timespec="seconds"),
        "datetime_now_utc":    now_utc.isoformat(timespec="seconds"),
        "utc_local_delta_h":   delta_h,
        "last_cgm_at":         cgm_ts,
        "last_cgm_age_min":    round(cgm_gap_min, 1) if cgm_gap_min is not None else None,
        "issues":              issues,
    }


# ── API pública ─────────────────────────────────────────────────────────────

def get_model_health(days: int = 7) -> dict:
    """
    Resumen completo del estado de salud del modelo y su pipeline.

    Status semáforo:
      "healthy"  : todo OK, listo para validación
      "warning"  : funcional pero con degradación
      "critical" : pipeline roto, no podemos confiar en métricas todavía
    """
    coverage = _coverage_check(days)
    audits   = _audit_check(days)
    tz_info  = _timezone_check()

    blocking_issues: list[str] = []
    warnings:        list[str] = []

    # 1. Background predictor corriendo?
    #    OJO con el diagnóstico: el predictor corre SOLO cuando entran
    #    lecturas nuevas. Si el sensor lleva un rato sin datos (cambio de
    #    sensor, teléfono lejos), la predicción vieja es CONSECUENCIA del
    #    hueco de datos, no un pipeline caído — eso es un warning suave,
    #    no un blocking que pagee (falsa alarma del 2026-07-14: 65 min de
    #    hueco de sensor reportados como "predictor caído").
    age = coverage.get("age_last_pred_min")
    edad_lectura = None
    try:
        from models import GlucoseReading
        _ult = (GlucoseReading.query
                .order_by(GlucoseReading.timestamp.desc()).first())
        if _ult:
            edad_lectura = (datetime.now() - _ult.timestamp).total_seconds() / 60
    except Exception:
        pass
    hay_datos_frescos = edad_lectura is not None and edad_lectura <= 20

    if age is None:
        blocking_issues.append(
            "no hay predicciones del modelo activo en la DB todavía"
        )
    elif age > MAX_AGE_LAST_PREDICTION_MIN * 3:
        if hay_datos_frescos:
            # datos fluyendo Y sin predicciones → pipeline roto de verdad
            blocking_issues.append(
                f"última predicción hace {age:.0f}min con lecturas frescas — "
                f"background predictor probablemente caído"
            )
        else:
            warnings.append(
                f"última predicción hace {age:.0f}min por hueco de sensor "
                f"(última lectura hace {edad_lectura:.0f}min) — se retoma solo "
                f"cuando vuelvan los datos" if edad_lectura is not None else
                f"última predicción hace {age:.0f}min — sin lecturas para predecir"
            )
    elif age > MAX_AGE_LAST_PREDICTION_MIN:
        warnings.append(
            f"última predicción hace {age:.0f}min — esperado <{MAX_AGE_LAST_PREDICTION_MIN}min"
        )

    # 2. Cobertura — pero ojo: ratio bajo NO es lo mismo que pipeline roto.
    #    Sólo es blocking si el predictor también está caído (age > umbral).
    #    Si el predictor corre normal y la cobertura es baja, es warm-up:
    #    se va a llenar con el tiempo. No es para alarmar.
    ratio = coverage.get("coverage_ratio", 0)
    predictor_alive = (age is not None and age <= MAX_AGE_LAST_PREDICTION_MIN * 3)
    if ratio < COVERAGE_RATIO_WARNING and not predictor_alive and hay_datos_frescos:
        # Bajo coverage Y predictor caído CON datos fluyendo → roto de verdad
        blocking_issues.append(
            f"coverage_ratio={ratio:.2f} y predictor inactivo — pipeline roto"
        )
    elif ratio < COVERAGE_RATIO_HEALTHY:
        warnings.append(
            f"coverage_ratio={ratio:.2f} — todavía acumulando evidencia "
            f"(esperar más días)"
        )

    # 3. Audits del SSM acompañando las predicciones?
    n_preds  = coverage.get("predictions_in_window", 0)
    n_audits = audits.get("prediction_audits", 0)
    if n_preds > 50 and n_audits < n_preds * 0.5:
        # Cada predicción genera 2 audits (h=30, h=60) → esperar ~2× n_preds.
        warnings.append(
            f"PredictionAudit ({n_audits}) muy por debajo de "
            f"GlucosePrediction ({n_preds}) — pipeline de audit puede estar fallando"
        )

    n_innov = audits.get("ssm_innovations", 0)
    if n_preds > 50 and n_innov == 0:
        blocking_issues.append(
            "SSMInnovation vacío con predicciones presentes — log_filter_innovations "
            "no se está ejecutando"
        )

    # 4. Backlog de audits
    n_backlog = audits.get("prediction_audits_unresolved_old", 0)
    if n_backlog > MAX_UNRESOLVED_AUDITS_WARNING:
        warnings.append(
            f"{n_backlog} PredictionAudit pendientes de resolver — verificar "
            f"resolve_audits"
        )

    n_hypo_stale = audits.get("hypo_audits_stale_12h", 0)
    if n_hypo_stale > 5:
        warnings.append(
            f"{n_hypo_stale} HypoRiskAudit sin outcome después de 12h — "
            f"verificar resolve_pending_hypo_audits"
        )

    # 5. Cobertura nocturna
    missing = coverage.get("missing_hours", [])
    nocturnal_missing = [h for h in missing if h >= 22 or h < 7]
    if nocturnal_missing:
        warnings.append(
            f"cobertura baja en horas nocturnas {nocturnal_missing} — "
            f"hipos nocturnas pueden no tener predicción"
        )

    # 6. TZ
    for issue in tz_info.get("issues", []):
        warnings.append(f"tz: {issue}")

    # Status final
    if blocking_issues:
        status = "critical"
    elif warnings:
        status = "warning"
    else:
        status = "healthy"

    return {
        "status":            status,
        "model_version":     ACTIVE_MODEL_VERSION,
        "computed_at":       datetime.now().isoformat(timespec="seconds"),
        "window_days":       days,
        "coverage":          coverage,
        "audits":            audits,
        "timezone":          tz_info,
        "blocking_issues":   blocking_issues,
        "warnings":          warnings,
    }


# ── Readiness gates ─────────────────────────────────────────────────────────

# Umbrales mínimos para correr el bench con seriedad.
READINESS_THRESHOLDS = {
    "min_coverage_ratio":           0.75,
    "min_predictions_24h":          200,
    "min_prediction_audits":        300,
    "min_ssm_innovations":          200,
    "max_age_last_prediction_min":  MAX_AGE_LAST_PREDICTION_MIN,
    "max_nocturnal_missing_hours":  2,    # de las 8 horas 22:00-06:00
    "max_blocking_issues":          0,
}


def is_ready_for_bench(days: int = 7) -> dict:
    """
    ¿Está el sistema listo para correr el bench con confianza?

    Returns
    -------
    {
      "ready":   bool,
      "checks":  {nombre: {passed, value, threshold}},
      "missing": [str],     # qué falta, en lenguaje claro
      "health":  {...},     # snapshot completo para debugging
    }
    """
    health = get_model_health(days=days)
    cov    = health["coverage"]
    aud    = health["audits"]

    checks: dict[str, dict] = {}
    missing: list[str] = []

    def _check(name: str, value, threshold, passed: bool, hint: str):
        checks[name] = {"passed": passed, "value": value, "threshold": threshold}
        if not passed:
            missing.append(hint)

    # 1. Coverage ratio
    cr = cov.get("coverage_ratio", 0)
    _check(
        "coverage_ratio", cr, READINESS_THRESHOLDS["min_coverage_ratio"],
        cr >= READINESS_THRESHOLDS["min_coverage_ratio"],
        f"cobertura {cr:.2f} — esperá hasta tener ≥0.75 "
        f"(~{int(READINESS_THRESHOLDS['min_coverage_ratio']*100)}% de los slots)"
    )

    # 2. Predictions 24h
    n24 = cov.get("predictions_24h", 0)
    _check(
        "predictions_24h", n24, READINESS_THRESHOLDS["min_predictions_24h"],
        n24 >= READINESS_THRESHOLDS["min_predictions_24h"],
        f"sólo {n24} predicciones en 24h — esperado ≥200 "
        f"(verificar que el background predictor corra cada sync)"
    )

    # 3. PredictionAudit suficientes
    npa = aud.get("prediction_audits", 0)
    _check(
        "prediction_audits", npa, READINESS_THRESHOLDS["min_prediction_audits"],
        npa >= READINESS_THRESHOLDS["min_prediction_audits"],
        f"sólo {npa} PredictionAudit en {days}d — esperado ≥300"
    )

    # 4. SSMInnovation suficientes
    nsi = aud.get("ssm_innovations", 0)
    _check(
        "ssm_innovations", nsi, READINESS_THRESHOLDS["min_ssm_innovations"],
        nsi >= READINESS_THRESHOLDS["min_ssm_innovations"],
        f"sólo {nsi} SSMInnovation en {days}d — esperado ≥200"
    )

    # 5. Background predictor reciente
    age = cov.get("age_last_pred_min")
    age_ok = age is not None and age <= READINESS_THRESHOLDS["max_age_last_prediction_min"]
    _check(
        "background_predictor_recent", age, READINESS_THRESHOLDS["max_age_last_prediction_min"],
        age_ok,
        (f"última predicción hace {age:.0f}min — esperado ≤{MAX_AGE_LAST_PREDICTION_MIN}min "
         f"(background predictor podría no estar corriendo)")
        if age is not None else
        "no hay predicciones todavía"
    )

    # 6. Cobertura nocturna
    nocturnal_missing = [h for h in cov.get("missing_hours", [])
                         if h >= 22 or h < 7]
    n_nocturnal_miss = len(nocturnal_missing)
    _check(
        "nocturnal_coverage", n_nocturnal_miss, READINESS_THRESHOLDS["max_nocturnal_missing_hours"],
        n_nocturnal_miss <= READINESS_THRESHOLDS["max_nocturnal_missing_hours"],
        f"cobertura nocturna insuficiente — faltan horas {nocturnal_missing} "
        f"(las hipos nocturnas no podrán evaluarse)"
    )

    # 7. Sin blocking issues
    nbi = len(health.get("blocking_issues", []))
    _check(
        "no_blocking_issues", nbi, READINESS_THRESHOLDS["max_blocking_issues"],
        nbi == 0,
        f"{nbi} blocking issues activos: " + "; ".join(health.get("blocking_issues", []))
    )

    ready = all(c["passed"] for c in checks.values())

    return {
        "ready":     ready,
        "checks":    checks,
        "missing":   missing,
        "health":    health,
    }


# ── Guardrails ──────────────────────────────────────────────────────────────

_ultimo_warn = {"fp": None, "at": 0.0}
_WARN_CADA_S = 12 * 3600   # el mismo warning repetido: 1 vez cada 12 h


def log_health_warnings() -> None:
    """
    Revisa el estado y emite warnings al logger 'model_health'.
    Pensado para ser llamado al final de cada sync. NO bloquea: cualquier
    excepción se traga silenciosamente.
    Los blocking se loguean SIEMPRE; los warnings repetidos se silencian
    12 h para no inundar los logs de Railway (p. ej. coverage_ratio en
    warm-up repetía la misma línea cada 5 minutos).
    """
    try:
        health = get_model_health(days=1)   # sólo últimas 24h, barato
    except Exception as exc:
        logger.debug("health check failed: %s", exc)
        return

    status = health["status"]
    if status == "healthy":
        return  # silencio en estado normal

    for issue in health.get("blocking_issues", []):
        logger.error("blocking: %s", issue)

    import time as _time
    ws = health.get("warnings", [])
    fp = "|".join(ws)
    ahora = _time.time()
    if fp == _ultimo_warn["fp"] and (ahora - _ultimo_warn["at"]) < _WARN_CADA_S:
        return
    _ultimo_warn["fp"], _ultimo_warn["at"] = fp, ahora
    for w in ws:
        logger.warning("warn: %s", w)
