"""
utils/hypo_risk_engine.py
──────────────────────────
Hito 8 — Motor probabilístico de riesgo de hipoglucemia nocturna.

Ahora integrado con el Unified Confidence System (safety/confidence.py)
y la capa de narrativa humana (safety/narrative.py).

El motor:
  1. Computa confianza unificada antes de evaluar riesgo
  2. Aplica degradación elegante (silent/observe_only/conservative/full)
  3. Ajusta thresholds de alerta según hora del día y confianza
  4. Genera factores en lenguaje humano (no técnico)
  5. Suprime alertas si no tiene suficiente certeza

Thresholds circadianos
──────────────────────
  nocturno (22:00-06:00): p_hypo_70 > 0.30 → alerta
  diurno  (06:00-22:00): p_hypo_70 > 0.50 → alerta
  + boost si confianza baja: +0.15 adicional al threshold

Degradation flow
────────────────
  confidence.silent        → no alertar, devolver "datos insuficientes"
  confidence.observe_only  → no alertar, solo estado
  confidence.conservative  → threshold +0.15
  confidence.full          → threshold base circadiano
"""
from __future__ import annotations

import math
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# ── Horizontes de predicción (minutos) ───────────────────────────────────────
HORIZONS_MIN = (30, 60, 120, 240, 480)

# ── Umbrales clínicos ─────────────────────────────────────────────────────────
HYPO_THRESHOLD   = 70.0   # mg/dL  nivel 1 hipoglucemia (ADA 2024)
SEVERE_THRESHOLD = 55.0   # mg/dL  nivel 2 hipoglucemia
TROUGH_DEPTH_REF = 70.0   # mg/dL  referencia para normalizar la profundidad

# ── Pesos del score compuesto ─────────────────────────────────────────────────
W_P70     = 0.45
W_P55     = 0.25
W_DEPTH   = 0.15
W_ROC     = 0.10
W_OVERLAP = 0.05

# ── Umbrales de severidad ─────────────────────────────────────────────────────
SEV_LOW      = 0.15
SEV_MODERATE = 0.30
SEV_HIGH     = 0.50
# > SEV_HIGH → critical


@dataclass
class HypoRiskAssessment:
    """
    Resultado completo del análisis de riesgo de hipoglucemia.

    Campos nuevos (Fase 2/3):
      confidence_report  : ConfidenceReport unificado
      narrative          : HypoWarningText con lenguaje humano
      alert_suppressed   : True si el sistema decidió no alertar
      alert_threshold    : threshold efectivo usado (circadiano + confianza)
    """
    # ── Core probabilístico ───────────────────────────────────────────────────
    risk_score:             float           # 0-1 compuesto
    p_hypo_70:              float           # P(G<70) en algún punto del horizonte
    p_hypo_55:              float           # P(G<55) en algún punto del horizonte
    min_predicted_glucose:  float           # mg/dL proyectado más bajo (media)
    min_glucose_eta_min:    int             # minutos hasta el trough proyectado
    projected_trough_time:  Optional[datetime] = None
    risk_window_start:      Optional[datetime] = None
    risk_window_end:        Optional[datetime] = None

    # ── Factores contribuyentes (lenguaje técnico, para logs/debug) ───────────
    contributing_factors:   list[str] = field(default_factory=list)
    confidence:             float = 0.5         # 0-1 confianza en el modelo
    severity:               str   = "low"       # low/moderate/high/critical

    # ── Detalle por horizonte ─────────────────────────────────────────────────
    horizon_detail:         dict = field(default_factory=dict)

    # ── Metadata ─────────────────────────────────────────────────────────────
    assessed_at:            Optional[datetime] = None
    proposed_bolus:         float = 0.0
    ssm_available:          bool = False
    fallback_used:          bool = False

    # ── Confianza unificada + narrativa humana (Fase 2/3) ─────────────────────
    confidence_report:      Optional[object] = None   # ConfidenceReport
    narrative:              Optional[object] = None   # HypoWarningText
    alert_suppressed:       bool = False
    alert_threshold:        float = 0.30

    def to_dict(self) -> dict:
        base = {
            "risk_score":            round(self.risk_score, 3),
            "p_hypo_70":             round(self.p_hypo_70, 3),
            "p_hypo_55":             round(self.p_hypo_55, 3),
            "min_predicted_glucose": round(self.min_predicted_glucose, 1),
            "min_glucose_eta_min":   self.min_glucose_eta_min,
            "projected_trough_time": self.projected_trough_time.isoformat() if self.projected_trough_time else None,
            "risk_window_start":     self.risk_window_start.isoformat() if self.risk_window_start else None,
            "risk_window_end":       self.risk_window_end.isoformat() if self.risk_window_end else None,
            "contributing_factors":  self.contributing_factors,
            "confidence":            round(self.confidence, 2),
            "severity":              self.severity,
            "horizon_detail":        self.horizon_detail,
            "assessed_at":           self.assessed_at.isoformat() if self.assessed_at else None,
            "proposed_bolus":        round(self.proposed_bolus, 2),
            "ssm_available":         self.ssm_available,
            "fallback_used":         self.fallback_used,
            "alert_suppressed":      self.alert_suppressed,
            "alert_threshold":       round(self.alert_threshold, 2),
        }
        # Incluir ConfidenceReport si está disponible
        if self.confidence_report is not None:
            base["confidence_report"] = self.confidence_report.to_dict()
        # Incluir narrativa humana si está disponible
        if self.narrative is not None and not self.alert_suppressed:
            base["narrative"] = self.narrative.to_dict()
        return base


# ── Motor principal ────────────────────────────────────────────────────────────

def assess_nocturnal_hypo_risk(
    current_glucose:      float,
    roc:                  float,       # mg/dL/min (negativo = bajando)
    proposed_bolus:       float = 0.0,
    current_iob:          float = 0.0,
    current_basal_effect: float = 0.0, # I_basal_eff en U activas
    carbs_on_board:       float = 0.0, # g CH pendientes
    timestamp:            Optional[datetime] = None,
    icr:                  float = 12.0,
    isf:                  float = 40.0,
    horizons_min:         tuple[int, ...] = HORIZONS_MIN,
    _filter_result=None,               # inyectado en tests
) -> HypoRiskAssessment:
    """
    Evalúa el riesgo de hipoglucemia nocturna incorporando un bolus propuesto.

    Parámetros
    ----------
    current_glucose      : glucemia actual (mg/dL)
    roc                  : tasa de cambio actual (mg/dL/min)
    proposed_bolus       : unidades de insulina rápida que el usuario piensa inyectar
    current_iob          : IOB activo de boluses anteriores (U)
    current_basal_effect : I_basal_eff actual de la basal lenta (U)
    carbs_on_board       : carbohidratos pendientes de absorción (g)
    timestamp            : ahora (default: datetime.utcnow())
    icr                  : ratio insulina:carbohidratos (g/U)
    isf                  : factor de sensibilidad (mg/dL/U)
    horizons_min         : horizontes de evaluación en minutos
    _filter_result       : inyectado externamente para tests (salta run_filter)

    Retorna
    -------
    HypoRiskAssessment con todo el perfil de riesgo.
    """
    now = timestamp or datetime.utcnow()

    # ── 1. Confianza unificada (Fase 2) ───────────────────────────────────────
    # Cargar ConfidenceReport antes de cualquier predicción para poder
    # degradar elegantemente si los datos son insuficientes.
    confidence_report = _compute_confidence_report(
        filter_result=_filter_result,
        now=now,
    )

    # ── 2. Threshold circadiano + ajuste por confianza ────────────────────────
    is_nocturnal = (now.hour >= 22 or now.hour < 6)
    alert_threshold_base = 0.30 if is_nocturnal else 0.50
    alert_threshold = alert_threshold_base + confidence_report.alert_threshold_boost()

    # Si el sistema debe silenciarse, devolver assessment vacío inmediatamente
    if confidence_report.suppress_alerts():
        from safety.narrative import render_hypo_warning
        suppressed_narrative = render_hypo_warning(
            _empty_assessment(now, proposed_bolus),
            confidence_report, now,
        )
        suppressed = HypoRiskAssessment(
            risk_score=0.0, p_hypo_70=0.0, p_hypo_55=0.0,
            min_predicted_glucose=current_glucose,
            min_glucose_eta_min=0,
            contributing_factors=[confidence_report.explanation],
            confidence=round(confidence_report.score, 2),
            severity="low",
            assessed_at=now,
            proposed_bolus=proposed_bolus,
            ssm_available=False,
            fallback_used=True,
            confidence_report=confidence_report,
            narrative=suppressed_narrative,
            alert_suppressed=True,
            alert_threshold=alert_threshold,
        )
        _log_audit(suppressed, current_glucose, roc, now)
        return suppressed

    # ── 3. Obtener posterior del SSM ──────────────────────────────────────────
    filter_result = _filter_result
    ssm_available = False
    fallback_used = False

    if filter_result is None:
        try:
            from pmm.ssm.filter import run_filter
            from pmm.core.parameter_store import get_isf_now, get_icr_now
            from pmm.engines.drift import get_drift_status

            hora = now.hour
            pmm_isf = get_isf_now(hora=hora)
            pmm_icr = get_icr_now(hora=hora)
            drift_st = get_drift_status()
            drift_factor = drift_st.get("drift_factor", 1.0)

            filter_result = run_filter(
                now=now,
                hours=6,
                isf_prior=pmm_isf.get("mu"),
                isf_sigma=pmm_isf.get("sigma"),
                drift_factor=drift_factor,
                icr_for_meals=pmm_icr.get("mu") or icr,
            )
            if filter_result and not filter_result.error and filter_result.n_steps > 0:
                ssm_available = True
        except Exception as exc:
            logger.warning("hypo_risk: SSM unavailable, using fallback — %s", exc)
            filter_result = None
    else:
        ssm_available = True

    # ── 4. Forward predict con bolus propuesto ────────────────────────────────
    horizon_detail: dict[int, dict] = {}

    if ssm_available and filter_result is not None:
        try:
            horizon_detail = _forward_with_bolus(
                filter_result=filter_result,
                proposed_bolus=proposed_bolus,
                carbs_on_board=carbs_on_board,
                icr=icr,
                horizons_min=horizons_min,
                now=now,
                basal_eff_override=current_basal_effect if current_basal_effect > 0 else None,
            )
        except Exception as exc:
            logger.warning("hypo_risk: forward_predict failed — %s", exc)
            ssm_available = False
            fallback_used = True

    if not ssm_available or not horizon_detail:
        fallback_used = True
        horizon_detail = _fallback_linear_profile(
            current_glucose=current_glucose, roc=roc,
            proposed_bolus=proposed_bolus, current_iob=current_iob,
            current_basal_effect=current_basal_effect,
            carbs_on_board=carbs_on_board,
            isf=isf, icr=icr, horizons_min=horizons_min, now=now,
        )

    # ── 5. Extraer métricas clave ─────────────────────────────────────────────
    p_hypo_70, p_hypo_55, g_min, eta_min = _extract_peak_risk(horizon_detail)

    # ── 6. Score compuesto ────────────────────────────────────────────────────
    depth_component   = min(1.0, max(0.0, (TROUGH_DEPTH_REF - g_min) / TROUGH_DEPTH_REF))
    roc_component     = min(1.0, max(0.0, -roc / 3.0))
    overlap_component = _nocturnal_overlap(now, eta_min)

    risk_score = (
        W_P70     * p_hypo_70 +
        W_P55     * p_hypo_55 +
        W_DEPTH   * depth_component +
        W_ROC     * roc_component +
        W_OVERLAP * overlap_component
    )
    risk_score = min(1.0, max(0.0, risk_score))

    # ── 7. Factores técnicos (para logs/audit) ────────────────────────────────
    factors = _explain_factors(
        p_hypo_70=p_hypo_70, p_hypo_55=p_hypo_55, g_min=g_min, roc=roc,
        proposed_bolus=proposed_bolus, current_iob=current_iob,
        current_basal_effect=current_basal_effect, carbs_on_board=carbs_on_board,
        eta_min=eta_min, overlap=overlap_component,
    )

    # ── 8. Severidad ──────────────────────────────────────────────────────────
    if risk_score >= SEV_HIGH:
        severity = "critical"
    elif risk_score >= SEV_MODERATE:
        severity = "high"
    elif risk_score >= SEV_LOW:
        severity = "moderate"
    else:
        severity = "low"

    # ── 9. Temporal ───────────────────────────────────────────────────────────
    trough_time       = now + timedelta(minutes=eta_min) if eta_min > 0 else None
    risk_window_start = now + timedelta(minutes=30)
    risk_window_end   = now + timedelta(minutes=max(480, eta_min + 60))

    # ── 10. Confianza legacy (backward compat) ────────────────────────────────
    confidence_scalar = confidence_report.score
    if ssm_available and filter_result:
        n_used = getattr(filter_result, "n_cgm_used", 0)
        ssm_boost = min(0.10, n_used / 80.0)   # pequeño boost por historia
        confidence_scalar = min(1.0, confidence_report.score + ssm_boost)

    # ── 11. Narrativa humana (Fase 3) ─────────────────────────────────────────
    # Construir assessment parcial para que render_hypo_warning() pueda
    # acceder a los campos necesarios (bolus, factors, etc.)
    _partial = _PartialAssessment(
        proposed_bolus=proposed_bolus,
        contributing_factors=factors,
        p_hypo_70=round(p_hypo_70, 3),
        p_hypo_55=round(p_hypo_55, 3),
        min_predicted_glucose=round(g_min, 1),
        projected_trough_time=trough_time,
        min_glucose_eta_min=eta_min,
        severity=severity,
        ssm_available=ssm_available,
    )
    try:
        from safety.narrative import render_hypo_warning
        narrative = render_hypo_warning(_partial, confidence_report, now)
    except Exception as exc:
        logger.debug("hypo_risk: narrative generation failed — %s", exc)
        narrative = None

    assessment = HypoRiskAssessment(
        risk_score=round(risk_score, 3),
        p_hypo_70=round(p_hypo_70, 3),
        p_hypo_55=round(p_hypo_55, 3),
        min_predicted_glucose=round(g_min, 1),
        min_glucose_eta_min=eta_min,
        projected_trough_time=trough_time,
        risk_window_start=risk_window_start,
        risk_window_end=risk_window_end,
        contributing_factors=factors,
        confidence=round(confidence_scalar, 2),
        severity=severity,
        horizon_detail=horizon_detail,
        assessed_at=now,
        proposed_bolus=proposed_bolus,
        ssm_available=ssm_available,
        fallback_used=fallback_used,
        confidence_report=confidence_report,
        narrative=narrative,
        alert_suppressed=False,
        alert_threshold=round(alert_threshold, 2),
    )

    # ── 12. Audit log ─────────────────────────────────────────────────────────
    _log_audit(assessment, current_glucose, roc, now)

    return assessment


# ── Helper: confidence report desde FilterResult ─────────────────────────────

def _compute_confidence_report(filter_result, now: datetime):
    """
    Construye el ConfidenceReport pasando las señales del SSM cuando están
    disponibles, y dejando que el sistema cargue el resto desde DB.
    """
    from safety.confidence import compute_confidence

    kwargs: dict = {}

    if filter_result is not None and not getattr(filter_result, "error", True):
        import numpy as np
        P = getattr(filter_result, "P", None)
        if P is not None:
            try:
                from pmm.ssm.state import state_index
                idx_g = state_index("G")
                kwargs["sigma_g"]   = float(math.sqrt(max(0.0, float(P[idx_g, idx_g]))))
                kwargs["cov_trace"] = float(np.trace(P))
            except Exception:
                pass
        kwargs["n_cgm_used"] = getattr(filter_result, "n_cgm_used", None)

    return compute_confidence(now=now, **kwargs)


@dataclass
class _PartialAssessment:
    """
    Subset de HypoRiskAssessment suficiente para render_hypo_warning().
    Evita circularidad de importaciones.
    """
    proposed_bolus:        float
    contributing_factors:  list
    p_hypo_70:             float
    p_hypo_55:             float
    min_predicted_glucose: float
    projected_trough_time: Optional[datetime]
    min_glucose_eta_min:   int
    severity:              str
    ssm_available:         bool


def _empty_assessment(now: datetime, proposed_bolus: float) -> "_PartialAssessment":
    return _PartialAssessment(
        proposed_bolus=proposed_bolus, contributing_factors=[],
        p_hypo_70=0.0, p_hypo_55=0.0, min_predicted_glucose=0.0,
        projected_trough_time=None, min_glucose_eta_min=0,
        severity="low", ssm_available=False,
    )


# ── Forward predict con bolus ──────────────────────────────────────────────────

def _forward_with_bolus(
    filter_result,
    proposed_bolus: float,
    carbs_on_board: float,
    icr: float,
    horizons_min: tuple[int, ...],
    now: datetime,
    basal_eff_override: Optional[float] = None,
) -> dict[int, dict]:
    """
    Ejecuta forward_predict desde el posterior del SSM después de aplicar
    el bolus propuesto como impulso instantáneo.
    """
    import numpy as np
    from pmm.ssm.filter import forward_predict
    from pmm.ssm.state import state_index

    # Clonar posterior y aplicar bolus como impulso (igual que dynamics.step)
    x_with_bolus = filter_result.x.copy()
    P_with_bolus = filter_result.P.copy()

    if proposed_bolus > 0:
        x_with_bolus[state_index("IOB")] += proposed_bolus

    # Clonar FilterResult con bolus aplicado
    import dataclasses
    result_with_bolus = dataclasses.replace(
        filter_result,
        x=x_with_bolus,
        P=P_with_bolus,
    )

    preds = forward_predict(
        result=result_with_bolus,
        horizons_min=horizons_min,
        icr_for_meals=icr,
        i_basal_eff_override=basal_eff_override,
    )

    horizon_detail: dict[int, dict] = {}
    for h, pred in preds.items():
        horizon_detail[h] = {
            "g_pred":     round(pred.g_pred, 1),
            "sigma":      round(pred.sigma, 2),
            "p70":        round(pred.p_hypo, 3),
            "p55":        round(_p_below_thresh(pred.g_pred, pred.sigma, 55.0), 3),
            "horizon_ts": (now + timedelta(minutes=h)).isoformat(),
        }

    return horizon_detail


def _p_below_thresh(g_pred: float, sigma: float, thresh: float) -> float:
    """P(G < thresh) bajo N(g_pred, sigma²)."""
    if sigma <= 0:
        return 1.0 if g_pred < thresh else 0.0
    z = (thresh - g_pred) / sigma
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


# ── Fallback lineal ───────────────────────────────────────────────────────────

def _fallback_linear_profile(
    current_glucose: float,
    roc: float,
    proposed_bolus: float,
    current_iob: float,
    current_basal_effect: float,
    carbs_on_board: float,
    isf: float,
    icr: float,
    horizons_min: tuple[int, ...],
    now: datetime,
) -> dict[int, dict]:
    """
    Perfil lineal simple cuando el SSM no está disponible.

    Modelo:
        G(t) = G0 + roc*t - delta_insulin(t) + delta_carbs(t)
        delta_insulin(t) = (IOB_total * isf) * (1 - exp(-t/120))
        delta_carbs(t)   = COB / icr * isf * (1 - exp(-t/90))

    La incertidumbre crece con sqrt(t).
    """
    iob_total = current_iob + proposed_bolus  # después de inyectar
    sigma_base = 15.0   # mg/dL incertidumbre basal

    detail: dict[int, dict] = {}
    for h in horizons_min:
        t = float(h)
        # Efecto insulínico (biexponencial simplificado)
        insulin_drop = (iob_total * isf) * (1.0 - math.exp(-t / 120.0))
        # Efecto carbohidratos
        cob_rise = (carbs_on_board / max(1.0, icr)) * isf * (1.0 - math.exp(-t / 90.0))
        # Efecto basal: compensa la producción hepática (pequeño)
        basal_drop = current_basal_effect * isf * (1.0 - math.exp(-t / 240.0)) * 0.3

        g_pred = current_glucose + roc * t - insulin_drop + cob_rise - basal_drop
        g_pred = max(30.0, g_pred)

        # Sigma crece con sqrt(t): incertidumbre acumulada
        sigma = sigma_base * math.sqrt(t / 60.0 + 0.5)

        detail[h] = {
            "g_pred":     round(g_pred, 1),
            "sigma":      round(sigma, 2),
            "p70":        round(_p_below_thresh(g_pred, sigma, 70.0), 3),
            "p55":        round(_p_below_thresh(g_pred, sigma, 55.0), 3),
            "horizon_ts": (now + timedelta(minutes=h)).isoformat(),
        }

    return detail


# ── Extracción de métricas peak ───────────────────────────────────────────────

def _extract_peak_risk(
    horizon_detail: dict[int, dict],
) -> tuple[float, float, float, int]:
    """
    Extrae: P(G<70) máxima, P(G<55) máxima, G mínimo proyectado, eta (min).
    """
    if not horizon_detail:
        return 0.0, 0.0, 100.0, 0

    p70_max  = 0.0
    p55_max  = 0.0
    g_min    = float("inf")
    eta_min  = 0

    for h, d in sorted(horizon_detail.items()):
        p70 = d.get("p70", 0.0)
        p55 = d.get("p55", 0.0)
        g   = d.get("g_pred", 120.0)

        if p70 > p70_max:
            p70_max = p70
        if p55 > p55_max:
            p55_max = p55
        if g < g_min:
            g_min   = g
            eta_min = h

    return p70_max, p55_max, g_min, eta_min


# ── Overlap nocturno ──────────────────────────────────────────────────────────

def _nocturnal_overlap(now: datetime, eta_min: int) -> float:
    """
    Fracción del horizonte de riesgo (ahora → ahora+eta) que cae en
    la ventana nocturna 22:00-06:00. Rango [0, 1].
    """
    if eta_min <= 0:
        return 0.0

    NOCTURNAL_START = 22
    NOCTURNAL_END   = 6   # 06:00

    trough_time = now + timedelta(minutes=eta_min)
    overlap_min = 0.0
    step = timedelta(minutes=15)
    t = now
    while t <= trough_time:
        h = t.hour
        in_night = (h >= NOCTURNAL_START) or (h < NOCTURNAL_END)
        if in_night:
            overlap_min += 15
        t += step

    return min(1.0, overlap_min / max(1.0, eta_min))


# ── Factores explicativos ──────────────────────────────────────────────────────

def _explain_factors(
    p_hypo_70: float,
    p_hypo_55: float,
    g_min: float,
    roc: float,
    proposed_bolus: float,
    current_iob: float,
    current_basal_effect: float,
    carbs_on_board: float,
    eta_min: int,
    overlap: float,
) -> list[str]:
    """Genera lista de factores contribuyentes en lenguaje humano (ES)."""
    factors = []

    if proposed_bolus > 0 and current_iob > 0.5:
        total_ins = proposed_bolus + current_iob
        factors.append(
            f"Insulina acumulada: bolo propuesto ({proposed_bolus:.1f}U) + "
            f"IOB activo ({current_iob:.1f}U) = {total_ins:.1f}U total"
        )
    elif proposed_bolus > 1.5:
        factors.append(f"Bolo propuesto: {proposed_bolus:.1f}U de insulina rápida")

    if current_basal_effect > 0.3:
        factors.append(
            f"Basal activa: Toujeo con {current_basal_effect:.2f}U en intersticial "
            f"(efecto continuo durante la noche)"
        )

    if roc < -1.0:
        factors.append(
            f"Tendencia descendente: {roc:+.1f} mg/dL/min "
            f"— glucemia bajando activamente"
        )
    elif roc < -0.5:
        factors.append(f"Tendencia leve a la baja: {roc:+.1f} mg/dL/min")

    if carbs_on_board < 10.0 and proposed_bolus > 0:
        factors.append(
            "Pocos carbohidratos activos para amortiguar el bolo propuesto"
        )

    if g_min < HYPO_THRESHOLD and p_hypo_70 > 0.20:
        factors.append(
            f"Trough proyectado: {g_min:.0f} mg/dL a los {eta_min} min"
        )
    elif g_min < 90:
        factors.append(
            f"Mínimo proyectado: {g_min:.0f} mg/dL a los {eta_min} min "
            f"(zona de precaución)"
        )

    if overlap > 0.6:
        factors.append(
            "El período de mayor riesgo coincide con el sueño nocturno "
            "(sin supervisión)"
        )

    if p_hypo_55 > 0.15:
        factors.append(
            f"Riesgo de hipoglucemia severa (<55 mg/dL): {p_hypo_55*100:.0f}%"
        )

    return factors


# ── Audit log ─────────────────────────────────────────────────────────────────

def _log_audit(
    assessment: HypoRiskAssessment,
    current_glucose: float,
    roc: float,
    now: datetime,
) -> None:
    """
    Persiste el assessment en hypo_risk_audit para revisión clínica.
    Silencia errores — el log nunca debe bloquear al usuario.
    """
    try:
        from models import db, HypoRiskAudit
        import json

        record = HypoRiskAudit(
            assessed_at=now,
            current_glucose=current_glucose,
            roc=roc,
            proposed_bolus=assessment.proposed_bolus,
            risk_score=assessment.risk_score,
            p_hypo_70=assessment.p_hypo_70,
            p_hypo_55=assessment.p_hypo_55,
            min_predicted_glucose=assessment.min_predicted_glucose,
            min_glucose_eta_min=assessment.min_glucose_eta_min,
            severity=assessment.severity,
            ssm_available=assessment.ssm_available,
            fallback_used=assessment.fallback_used,
            contributing_factors_json=json.dumps(
                assessment.contributing_factors, ensure_ascii=False
            ),
        )
        db.session.add(record)
        db.session.commit()
    except Exception:
        pass  # silenciar — audit no debe bloquear flujo principal


# ── Helpers para el scheduler y para el modal UI ──────────────────────────────

def should_alert(assessment: HypoRiskAssessment) -> bool:
    """
    True si el assessment justifica mostrar una alerta al usuario.
    Usa el threshold contextual (circadiano + confianza) calculado
    durante el assessment, en lugar de un valor fijo.
    """
    if assessment.alert_suppressed:
        return False
    threshold = getattr(assessment, "alert_threshold", 0.30)
    return assessment.p_hypo_70 > threshold or assessment.severity in ("high", "critical")


def format_alert_message(assessment: HypoRiskAssessment, *, compact: bool = False) -> str:
    """
    Genera el mensaje de alerta usando la narrativa humana cuando está disponible,
    o el formato técnico como fallback.
    compact=True → una sola línea para notificaciones push.
    """
    # ── Usar narrativa humana si está disponible ──────────────────────────────
    narrative = getattr(assessment, "narrative", None)

    if compact:
        if narrative and not getattr(narrative, "suppress", False):
            prob = narrative.probability_phrase
            trough = narrative.trough_phrase
            # Truncar para notificación push
            return f"{narrative.title}. {prob} {trough}"[:200]
        # Fallback compacto técnico
        p_pct = round(assessment.p_hypo_70 * 100)
        g_min = round(assessment.min_predicted_glucose)
        eta   = assessment.min_glucose_eta_min
        return (
            f"Riesgo de baja glucemia: {p_pct}% de probabilidad de llegar a "
            f"<70 mg/dL en ~{eta} min (mín proyectado: {g_min} mg/dL)"
        )

    # ── Full message con narrativa humana ─────────────────────────────────────
    if narrative and not getattr(narrative, "suppress", False):
        lines = [f"**{narrative.title}**", ""]
        if narrative.probability_phrase:
            lines.append(narrative.probability_phrase)
        if narrative.trough_phrase:
            lines.append(narrative.trough_phrase)
        lines.append("")
        if narrative.factors:
            lines.append("Por qué:")
            for f in narrative.factors:
                lines.append(f"  • {f}")
            lines.append("")
        if narrative.suggestion:
            lines.append(narrative.suggestion)
        if narrative.confidence_note:
            lines.append(f"\n_{narrative.confidence_note}_")
        return "\n".join(lines)

    # ── Fallback técnico (sin narrativa) ──────────────────────────────────────
    p_pct = round(assessment.p_hypo_70 * 100)
    g_min = round(assessment.min_predicted_glucose)
    sev_label = {
        "low": "Riesgo bajo", "moderate": "Riesgo moderado",
        "high": "Riesgo elevado", "critical": "Riesgo crítico",
    }.get(assessment.severity, "Riesgo")

    lines = [f"**{sev_label} de hipoglucemia nocturna**", "",
             f"Probabilidad estimada: {p_pct}% de llegar a <70 mg/dL.",
             f"Mínimo proyectado: {g_min} mg/dL.", ""]

    if assessment.contributing_factors:
        for f in assessment.contributing_factors[:3]:
            lines.append(f"  • {f}")

    return "\n".join(lines)
