"""
utils/audit_logger.py
──────────────────────
Persistencia de auditoría científica de predicciones e innovations.

API pública
-----------
  log_prediction_audit(...)   — una row por (predicted_at, horizon, model)
  log_filter_innovations(...) — N rows por filter run (una por update CGM)
  resolve_audits(readings)    — llena realized_glucose + inside_ic50/90
                                cuando llegan lecturas CGM nuevas

Diseño
------
Append-only. Toda escritura es idempotente (dedup por unique tuple).
Tolerante a fallos: nunca rompe el hot path — todas las funciones
swallow exceptions silentemente con log.debug.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta
from typing import Iterable, Optional

logger = logging.getLogger("audit")

# Tolerancia para considerar una lectura como "resolución" de un horizon
_RESOLVE_TOLERANCE_MIN = 7.0    # ± 7 min de la t target

# Cuantiles Z para IC50/IC90 (gauss)
_Z_50 = 0.6745   # P(|Z|<0.6745) = 0.50
_Z_90 = 1.6449   # P(|Z|<1.6449) = 0.90


# ─── Compute confidence ──────────────────────────────────────────────────

def composite_confidence(
    mu:             float,
    sigma:          float,
    n_filter_updates: Optional[int] = None,
    log_evidence:   Optional[float] = None,
    cov_condition:  Optional[float] = None,
) -> float:
    """
    Composite confidence ∈ [0, 1]. Más alto = más confiable.

    Heurística MVP — refinable con datos de validación:
      - sharpness:   σ pequeño respecto a 30 mg/dL → alta
      - history:     más updates del filtro → más alta (saturando)
      - evidence:    log_evidence promedio cerca de óptimo → alta
      - cond_number: κ(P) muy alto → degradar (filter mal condicionado)

    Esta C la usa el Clinical Safety Layer (próximo hito) para gating.
    Por ahora solo loguea — no influye en decisiones todavía.
    """
    # Sharpness term: sigmoide en sigma/30
    sigma_term = 1.0 / (1.0 + (sigma / 25.0) ** 2)

    # History term: saturación con n updates
    history_term = 1.0
    if n_filter_updates is not None:
        history_term = min(1.0, n_filter_updates / 30.0)   # 30 updates = full

    # Evidence term: si log_evidence/n_updates está cerca del óptimo gaussiano
    # (~-3 para σ=10), bien. Si muy negativo, mal.
    evidence_term = 1.0
    if log_evidence is not None and n_filter_updates and n_filter_updates > 0:
        avg = log_evidence / n_filter_updates
        # Óptimo ~ -log(σ√2π) ≈ -3.2 para σ=10. Aceptable hasta -6.
        if avg < -6:
            evidence_term = max(0.2, math.exp(0.5 * (avg + 6)))
        elif avg < -10:
            evidence_term = 0.1

    # Condition number: si κ > 1e8, filter mal condicionado
    cond_term = 1.0
    if cov_condition is not None and cov_condition > 0:
        if cov_condition > 1e8:
            cond_term = 0.3
        elif cov_condition > 1e6:
            cond_term = 0.7

    return round(max(0.0, min(1.0,
        sigma_term * 0.5 + history_term * 0.2 +
        evidence_term * 0.2 + cond_term * 0.1)), 3)


# ─── Predict audit logging ───────────────────────────────────────────────

def log_prediction_audit(
    predicted_at:     datetime,
    horizon_min:      int,
    model_version:    str,
    mu:               float,
    sigma:            Optional[float] = None,
    p_hypo:           Optional[float] = None,
    p_hyper:          Optional[float] = None,
    # SSM-only
    cov_trace:        Optional[float] = None,
    cov_condition:    Optional[float] = None,
    cov_min_eig:      Optional[float] = None,
    cov_max_eig:      Optional[float] = None,
    psd_ok:           Optional[bool]  = None,
    log_evidence:     Optional[float] = None,
    last_innov:       Optional[float] = None,
    last_innov_z:     Optional[float] = None,
    n_filter_updates: Optional[int]   = None,
) -> Optional[int]:
    """
    Persiste una row de PredictionAudit. Dedup por (predicted_at±5min, horizon, model).
    Devuelve el id de la row creada (o None si fue dedup o falló).
    """
    try:
        from models import db, PredictionAudit

        # Dedup ligero (8 min, mismo modelo+horizon)
        cutoff = predicted_at - timedelta(minutes=8)
        existe = (PredictionAudit.query
                  .filter(PredictionAudit.model_version == model_version,
                          PredictionAudit.horizon_min   == horizon_min,
                          PredictionAudit.predicted_at  >= cutoff,
                          PredictionAudit.predicted_at  <= predicted_at)
                  .first())
        if existe:
            return existe.id

        # IC50 / IC90 computados desde σ (gaussiano)
        sigma_v = max(0.0, sigma or 0.0)
        ic50_lo = round(mu - _Z_50 * sigma_v, 2) if sigma else None
        ic50_hi = round(mu + _Z_50 * sigma_v, 2) if sigma else None
        ic90_lo = round(mu - _Z_90 * sigma_v, 2) if sigma else None
        ic90_hi = round(mu + _Z_90 * sigma_v, 2) if sigma else None

        confidence = composite_confidence(
            mu=mu, sigma=sigma_v,
            n_filter_updates=n_filter_updates,
            log_evidence=log_evidence,
            cov_condition=cov_condition,
        )

        row = PredictionAudit(
            predicted_at  = predicted_at,
            horizon_min   = horizon_min,
            model_version = model_version,
            mu            = round(mu, 2),
            sigma         = round(sigma_v, 2) if sigma else None,
            ic50_low      = ic50_lo,
            ic50_high     = ic50_hi,
            ic90_low      = ic90_lo,
            ic90_high     = ic90_hi,
            p_hypo        = round(p_hypo,  4) if p_hypo  is not None else None,
            p_hyper       = round(p_hyper, 4) if p_hyper is not None else None,
            confidence    = confidence,
            cov_trace     = round(cov_trace,     6) if cov_trace     is not None else None,
            cov_condition = round(cov_condition, 4) if cov_condition is not None else None,
            cov_min_eig   = round(cov_min_eig,   6) if cov_min_eig   is not None else None,
            cov_max_eig   = round(cov_max_eig,   6) if cov_max_eig   is not None else None,
            psd_ok        = psd_ok,
            log_evidence  = round(log_evidence, 3) if log_evidence is not None else None,
            last_innov    = round(last_innov,   3) if last_innov   is not None else None,
            last_innov_z  = round(last_innov_z, 3) if last_innov_z is not None else None,
            n_filter_updates = n_filter_updates,
            resolved      = False,
        )
        db.session.add(row)
        db.session.commit()
        return row.id

    except Exception as exc:
        logger.debug(f"log_prediction_audit falló: {exc}")
        try:
            from models import db
            db.session.rollback()
        except Exception:
            pass
        return None


# ─── Filter innovations logging ──────────────────────────────────────────

def log_filter_innovations(
    model_version: str,
    run_at:        datetime,
    innovations:   list[dict],
) -> int:
    """
    Persiste innovations del UKF.

    `innovations` = lista de dicts con: ts, y_obs, y_pred, sigma_pred,
    g_state, p_g_g, rejected, log_likelihood.
    """
    if not innovations:
        return 0
    try:
        from models import db, SSMInnovation
        n_inserted = 0
        for inn in innovations:
            innov = inn["y_obs"] - inn["y_pred"]
            sig   = max(1e-6, inn["sigma_pred"])
            row = SSMInnovation(
                ts             = inn["ts"],
                run_at         = run_at,
                model_version  = model_version,
                y_obs          = round(inn["y_obs"], 2),
                y_pred         = round(inn["y_pred"], 2),
                innovation     = round(innov, 3),
                sigma_pred     = round(sig, 3),
                innovation_z   = round(innov / sig, 3),
                g_state        = round(inn.get("g_state",  inn["y_pred"]), 2),
                p_g_g          = round(inn.get("p_g_g", 0.0), 4),
                rejected       = bool(inn.get("rejected", False)),
                log_likelihood = (round(inn["log_likelihood"], 3)
                                  if inn.get("log_likelihood") is not None else None),
            )
            db.session.add(row)
            n_inserted += 1
        db.session.commit()
        return n_inserted
    except Exception as exc:
        logger.debug(f"log_filter_innovations falló: {exc}")
        try:
            from models import db
            db.session.rollback()
        except Exception:
            pass
        return 0


# ─── Resolución post-hoc cuando llegan lecturas reales ───────────────────

def resolve_audits(readings: Iterable) -> int:
    """
    Para cada reading nueva, busca audits pendientes a t-horizon ± tolerance
    y los marca como resueltos con realized_glucose, innovation, IC inside.

    Devuelve número de rows actualizadas.
    """
    try:
        from models import db, PredictionAudit
        readings = list(readings)
        if not readings:
            return 0

        # Cargar TODOS los audits pendientes (típicamente < 50)
        pendientes = (PredictionAudit.query
                      .filter(PredictionAudit.resolved == False)
                      .all())
        if not pendientes:
            return 0

        n_resolved = 0
        for audit in pendientes:
            t_target = audit.predicted_at + timedelta(minutes=audit.horizon_min)
            best = None
            best_dt = float("inf")
            for r in readings:
                dt = abs((r.timestamp - t_target).total_seconds() / 60.0)
                if dt < best_dt and dt <= _RESOLVE_TOLERANCE_MIN:
                    best, best_dt = r, dt
            if best is None:
                continue

            realized = float(best.value_mgdl)
            innov    = realized - audit.mu
            innov_z  = innov / audit.sigma if audit.sigma and audit.sigma > 0 else None

            audit.realized_glucose = round(realized, 1)
            audit.realized_at      = best.timestamp
            audit.innovation       = round(innov, 2)
            audit.innovation_z     = round(innov_z, 3) if innov_z is not None else None
            if audit.ic50_low is not None and audit.ic50_high is not None:
                audit.inside_ic50 = bool(audit.ic50_low <= realized <= audit.ic50_high)
            if audit.ic90_low is not None and audit.ic90_high is not None:
                audit.inside_ic90 = bool(audit.ic90_low <= realized <= audit.ic90_high)
            audit.resolved = True
            n_resolved += 1

        if n_resolved:
            db.session.commit()
        return n_resolved
    except Exception as exc:
        logger.debug(f"resolve_audits falló: {exc}")
        try:
            from models import db
            db.session.rollback()
        except Exception:
            pass
        return 0


# ─── Covariance health helpers ───────────────────────────────────────────

def covariance_diagnostics(P) -> dict:
    """
    Métricas de salud para la matriz de covarianza posterior P.

    Returns
    -------
    {
      "trace": float,
      "min_eig": float,
      "max_eig": float,
      "condition": float,     # κ = max_eig / min_eig
      "psd_ok": bool,         # todos los eigenvalues > 0
    }
    """
    try:
        import numpy as np
        P_sym = 0.5 * (P + P.T)
        eigs  = np.linalg.eigvalsh(P_sym)   # devuelve real para matriz simétrica
        e_min = float(eigs.min())
        e_max = float(eigs.max())
        return {
            "trace":     float(np.trace(P_sym)),
            "min_eig":   e_min,
            "max_eig":   e_max,
            "condition": float(e_max / e_min) if e_min > 0 else float("inf"),
            "psd_ok":    bool(e_min > -1e-8),     # tolerancia numérica
        }
    except Exception:
        return {"trace": None, "min_eig": None, "max_eig": None,
                "condition": None, "psd_ok": None}
