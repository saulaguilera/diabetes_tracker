"""
services/background_predictor.py
─────────────────────────────────
Genera una predicción del SSM a 30/60 min en cada CGM sync, sin requerir
interacción del usuario. Persiste el resultado en `glucose_prediction` con
`model_version = "ssm_v0_ukf6_basal"` para que el bench tenga cobertura
continua (no sólo cuando el usuario abre /calcular).

Diseño
------
- NO toca el SSM ni sus ecuaciones (cumple freeze científico).
- Reutiliza `run_filter` + `forward_predict` exactamente igual que la
  ruta `/api/predict-glucose` y el `hypo_risk_engine`.
- Throttle natural: `save_prediction` ya deduplica si hay otra del mismo
  modelo dentro de 8 min.
- Sin side effects fuera de DB writes + logging.
- Cualquier excepción se traga: jamás puede romper un sync de CGM.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger("background_predictor")


def run_and_save_ssm_prediction(now: Optional[datetime] = None) -> dict:
    """
    Corre el SSM con los datos actuales y persiste la predicción 30/60min.

    Returns
    -------
    {
        "ok":          bool,
        "saved":       bool,            # False si dedup la descartó o falló
        "reason":      str | None,      # motivo si saved=False
        "g_pred_30":   float | None,
        "g_pred_60":   float | None,
        "sigma_30":    float | None,
        "sigma_60":    float | None,
    }
    """
    out = {"ok": False, "saved": False, "reason": None,
           "g_pred_30": None, "g_pred_60": None,
           "sigma_30": None, "sigma_60": None}
    try:
        now = now or datetime.now()
        from models import db, GlucoseReading, GlucosePrediction

        # 1. Última lectura CGM (no artefacto) para g_actual
        last_cgm = (db.session.query(GlucoseReading)
                    .filter((GlucoseReading.is_artifact == False) |
                            (GlucoseReading.is_artifact.is_(None)))
                    .order_by(GlucoseReading.timestamp.desc())
                    .first())
        if not last_cgm:
            out["reason"] = "no_cgm"
            return out
        # No predecir si la última lectura tiene > 20 min (gap de sensor)
        gap_min = (now - last_cgm.timestamp).total_seconds() / 60.0
        if gap_min > 20:
            out["reason"] = f"cgm_stale_{gap_min:.0f}min"
            return out
        g_actual = float(last_cgm.value_mgdl)

        # 2. Throttle: si ya hay una predicción ssm_v0_ukf6_basal en los
        #    últimos 4.5 min, no corremos (save_prediction usaría 8min pero
        #    queremos evitar también el costo de run_filter).
        from datetime import timedelta
        recent = (db.session.query(GlucosePrediction)
                  .filter(GlucosePrediction.model_version == "ssm_v0_ukf6_basal")
                  .filter(GlucosePrediction.predicted_at >= now - timedelta(minutes=4, seconds=30))
                  .first())
        if recent:
            out["reason"] = "throttled_recent_prediction"
            return out

        # 3. Contexto IOB / COB sólo como metadata (no afecta al SSM, que ya
        #    integra ambos internamente). Cualquier fallo aquí es no-crítico.
        iob_now = 0.0
        cob_now = 0.0
        try:
            from models import InsulinDose, Meal
            cutoff_iob = now - timedelta(hours=8)
            bolus_list = (db.session.query(InsulinDose)
                          .filter(InsulinDose.type == "bolus")
                          .filter(InsulinDose.timestamp >= cutoff_iob)
                          .filter(InsulinDose.timestamp <= now)
                          .all())
            cutoff_cob = now - timedelta(hours=6)
            meal_list = (db.session.query(Meal)
                         .filter(Meal.timestamp >= cutoff_cob)
                         .filter(Meal.timestamp <= now)
                         .all())
            from utils.kinetics import current_iob, current_cob
            iob_now = float(current_iob(bolus_list, at_time=now) or 0.0)
            cob_now = float(current_cob(meal_list,  at_time=now) or 0.0)
        except Exception as exc:
            logger.debug("iob/cob unavailable: %s", exc)

        # 4. ROC ~ pendiente últimos 15min
        roc = None
        try:
            recent_cgm = (db.session.query(GlucoseReading)
                          .filter(GlucoseReading.timestamp >= now - timedelta(minutes=15))
                          .filter((GlucoseReading.is_artifact == False) |
                                  (GlucoseReading.is_artifact.is_(None)))
                          .order_by(GlucoseReading.timestamp.asc())
                          .all())
            if len(recent_cgm) >= 2:
                dg = recent_cgm[-1].value_mgdl - recent_cgm[0].value_mgdl
                dt_min = (recent_cgm[-1].timestamp - recent_cgm[0].timestamp).total_seconds() / 60.0
                if dt_min > 0:
                    roc = round(dg / dt_min, 3)
        except Exception:
            pass

        # 5. Parámetros del SSM (mismos que api_predict_glucose / hypo_risk_engine)
        from pmm.ssm.filter import run_filter, forward_predict
        from pmm.core.parameter_store import get_isf_now, get_icr_now
        from pmm.engines.drift import get_drift_status

        hora       = now.hour
        pmm_isf    = get_isf_now(hora=hora)
        pmm_icr    = get_icr_now(hora=hora)
        drift_st   = get_drift_status()
        drift_factor = drift_st.get("drift_factor", 1.0)
        icr_for_meals = pmm_icr.get("mu") or 12.0
        isf_prior  = pmm_isf.get("mu")
        isf_sigma  = pmm_isf.get("sigma")

        # 6. Corrida del SSM
        ssm_result = run_filter(
            now=now,
            isf_prior=isf_prior,
            isf_sigma=isf_sigma,
            drift_factor=drift_factor,
            icr_for_meals=icr_for_meals,
        )
        if ssm_result.error is not None:
            out["reason"] = f"ssm_error:{ssm_result.error}"
            return out
        if ssm_result.n_cgm_used < 3:
            out["reason"] = f"ssm_insufficient_cgm:{ssm_result.n_cgm_used}"
            return out

        # 7. Forward predict a 30 y 60 min
        try:
            from utils.kinetics import dawn_roc_mgdl_min
            dawn_rate = float(dawn_roc_mgdl_min(at_time=now) or 0.0)
        except Exception:
            dawn_rate = 0.0

        ssm_preds = forward_predict(
            ssm_result,
            horizons_min=(30, 60),
            drift_factor=drift_factor,
            icr_for_meals=icr_for_meals,
            exercise_drop_rate=0.0,
            dawn_rate=dawn_rate,
            ex_sensitivity_mult=1.0,
        )

        # 8. Persistir GlucosePrediction
        from utils.prediction_feedback import save_prediction
        save_prediction(
            predicted_at  = now,
            g_actual      = g_actual,
            g_pred_30     = float(ssm_preds[30].g_pred),
            g_pred_60     = float(ssm_preds[60].g_pred),
            sigma_30      = float(ssm_preds[30].sigma),
            sigma_60      = float(ssm_preds[60].sigma),
            model_version = "ssm_v0_ukf6_basal",
            iob           = iob_now,
            cob           = cob_now,
            roc           = roc,
            isf_used      = isf_prior,
            icr_used      = icr_for_meals,
            ex_factor     = 1.0,
        )

        # 9. Audit científico (PredictionAudit + SSMInnovation)
        #    NOTA: usar la firma exacta de log_prediction_audit (cov_trace,
        #    cov_condition, etc.) — no `state_post` / `P_post` / `cov_diag`,
        #    que no son parámetros válidos y disparaban TypeError silenciado.
        try:
            from utils.audit_logger import (
                log_prediction_audit, log_filter_innovations,
                covariance_diagnostics,
            )
            cov_diag = covariance_diagnostics(ssm_result.P)
            for h_min in (30, 60):
                pp = ssm_preds[h_min]
                log_prediction_audit(
                    predicted_at     = now,
                    horizon_min      = h_min,
                    model_version    = "ssm_v0_ukf6_basal",
                    mu               = float(pp.g_pred),
                    sigma            = float(pp.sigma),
                    p_hypo           = float(pp.p_hypo),
                    p_hyper          = float(pp.p_hyper),
                    cov_trace        = cov_diag.get("trace"),
                    cov_condition    = cov_diag.get("condition"),
                    cov_min_eig      = cov_diag.get("min_eig"),
                    cov_max_eig      = cov_diag.get("max_eig"),
                    psd_ok           = cov_diag.get("psd_ok"),
                    log_evidence     = float(ssm_result.log_evidence),
                    last_innov       = ssm_result.last_innov,
                    last_innov_z     = ssm_result.last_innov_z,
                    n_filter_updates = int(ssm_result.n_cgm_used),
                )
            if ssm_result.innovations:
                log_filter_innovations(
                    model_version = "ssm_v0_ukf6_basal",
                    run_at        = now,
                    innovations   = ssm_result.innovations,
                )
        except Exception as exc:
            logger.debug("audit logging falló: %s", exc)

        out.update({
            "ok":         True,
            "saved":      True,
            "g_pred_30":  round(float(ssm_preds[30].g_pred), 1),
            "g_pred_60":  round(float(ssm_preds[60].g_pred), 1),
            "sigma_30":   round(float(ssm_preds[30].sigma),  2),
            "sigma_60":   round(float(ssm_preds[60].sigma),  2),
        })
        return out

    except Exception as exc:
        logger.warning("background_predictor falló: %s", exc, exc_info=False)
        out["reason"] = f"exception:{type(exc).__name__}"
        return out
