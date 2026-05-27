"""
utils/hypo_risk_engine.py
──────────────────────────
Hito 8 — Motor probabilístico de riesgo de hipoglucemia nocturna.

Convierte el conocimiento del SSM en una alerta preventiva clara,
calmada y accionable. No reemplaza al médico: da contexto cuantitativo
para que el usuario tome decisiones informadas.

Diseño
──────
- Usa SSM forward_predict() con horizontes 30/60/120/240/480 min.
- Construye un perfil de glucemia proyectada a partir del estado del UKF.
- Calcula P(G<70) y P(G<55) por horizonte sumando distribuciones normales.
- Score compuesto: 0.45·p70 + 0.25·p55 + 0.15·depth + 0.10·roc + 0.05·overlap
- Severidades: low (<0.15), moderate (0.15-0.30), high (0.30-0.50), critical (>0.50)
- Logging en HypoRiskAudit (audit trail para revisión clínica post-evento).

Integración
───────────
    from utils.hypo_risk_engine import assess_nocturnal_hypo_risk

    risk = assess_nocturnal_hypo_risk(
        current_glucose=176,
        roc=-0.5,
        proposed_bolus=2.0,
        current_iob=0.3,
        current_basal_effect=0.41,
        carbs_on_board=0.0,
    )
    if risk.p_hypo_70 > 0.30:
        # mostrar modal preventivo
        ...
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

    # ── Factores contribuyentes ────────────────────────────────────────────────
    contributing_factors:   list[str] = field(default_factory=list)
    confidence:             float = 0.5         # 0-1 confianza en el modelo
    severity:               str   = "low"       # low/moderate/high/critical

    # ── Detalle por horizonte ─────────────────────────────────────────────────
    horizon_detail:         dict = field(default_factory=dict)
    # {30: {g_pred, sigma, p70, p55}, 60: ..., ...}

    # ── Metadata ─────────────────────────────────────────────────────────────
    assessed_at:            Optional[datetime] = None
    proposed_bolus:         float = 0.0
    ssm_available:          bool = False
    fallback_used:          bool = False

    def to_dict(self) -> dict:
        return {
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
        }


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

    # ── 1. Obtener posterior del SSM ──────────────────────────────────────────
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

    # ── 2. Forward predict con bolus propuesto ────────────────────────────────
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
        # Fallback: modelo lineal simple con incertidumbre creciente
        fallback_used = True
        horizon_detail = _fallback_linear_profile(
            current_glucose=current_glucose,
            roc=roc,
            proposed_bolus=proposed_bolus,
            current_iob=current_iob,
            current_basal_effect=current_basal_effect,
            carbs_on_board=carbs_on_board,
            isf=isf,
            icr=icr,
            horizons_min=horizons_min,
            now=now,
        )

    # ── 3. Extraer métricas clave ─────────────────────────────────────────────
    p_hypo_70, p_hypo_55, g_min, eta_min = _extract_peak_risk(horizon_detail)

    # ── 4. Score compuesto ────────────────────────────────────────────────────
    # Componente de profundidad: cuánto baja el trough por debajo de 70 mg/dL
    depth_component = max(0.0, (TROUGH_DEPTH_REF - g_min) / TROUGH_DEPTH_REF)
    depth_component = min(1.0, depth_component)

    # Componente ROC: tendencia bajista activa es señal de riesgo adicional
    roc_component = max(0.0, -roc / 3.0)   # -3 mg/dL/min → 1.0
    roc_component = min(1.0, roc_component)

    # Overlap con ventana nocturna (22:00 - 06:00) si aplica
    overlap_component = _nocturnal_overlap(now, eta_min)

    risk_score = (
        W_P70     * p_hypo_70 +
        W_P55     * p_hypo_55 +
        W_DEPTH   * depth_component +
        W_ROC     * roc_component +
        W_OVERLAP * overlap_component
    )
    risk_score = min(1.0, max(0.0, risk_score))

    # ── 5. Factores contribuyentes (texto legible) ────────────────────────────
    factors = _explain_factors(
        p_hypo_70=p_hypo_70,
        p_hypo_55=p_hypo_55,
        g_min=g_min,
        roc=roc,
        proposed_bolus=proposed_bolus,
        current_iob=current_iob,
        current_basal_effect=current_basal_effect,
        carbs_on_board=carbs_on_board,
        eta_min=eta_min,
        overlap=overlap_component,
    )

    # ── 6. Severidad ──────────────────────────────────────────────────────────
    if risk_score >= SEV_HIGH:
        severity = "critical"
    elif risk_score >= SEV_MODERATE:
        severity = "high"
    elif risk_score >= SEV_LOW:
        severity = "moderate"
    else:
        severity = "low"

    # ── 7. Ventana temporal de riesgo ─────────────────────────────────────────
    trough_time = now + timedelta(minutes=eta_min) if eta_min > 0 else None
    risk_window_start = now + timedelta(minutes=30)
    risk_window_end   = now + timedelta(minutes=max(480, eta_min + 60))

    # ── 8. Confianza (baja con fallback, sube con más observaciones SSM) ──────
    confidence = 0.75 if ssm_available else 0.45
    if ssm_available and filter_result:
        n_used = getattr(filter_result, "n_cgm_used", 0)
        if n_used >= 6:
            confidence = 0.85
        elif n_used >= 3:
            confidence = 0.75
        else:
            confidence = 0.60

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
        confidence=round(confidence, 2),
        severity=severity,
        horizon_detail=horizon_detail,
        assessed_at=now,
        proposed_bolus=proposed_bolus,
        ssm_available=ssm_available,
        fallback_used=fallback_used,
    )

    # ── 9. Audit log ──────────────────────────────────────────────────────────
    _log_audit(assessment, current_glucose, roc, now)

    return assessment


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
    """True si el assessment justifica mostrar una alerta al usuario."""
    return assessment.p_hypo_70 > 0.30 or assessment.severity in ("high", "critical")


def format_alert_message(assessment: HypoRiskAssessment, *, compact: bool = False) -> str:
    """
    Genera el mensaje de alerta en tono calmado y accionable.
    compact=True → una sola línea para notificaciones push.
    """
    p_pct = round(assessment.p_hypo_70 * 100)
    g_min = round(assessment.min_predicted_glucose)
    eta   = assessment.min_glucose_eta_min

    if compact:
        return (
            f"Riesgo de baja glucemia: {p_pct}% de probabilidad de llegar a "
            f"<70 mg/dL en ~{eta} min (mín proyectado: {g_min} mg/dL)"
        )

    sev_label = {
        "low":      "Riesgo bajo",
        "moderate": "Riesgo moderado",
        "high":     "Riesgo elevado",
        "critical": "Riesgo crítico",
    }.get(assessment.severity, "Riesgo")

    lines = [
        f"**{sev_label} de hipoglucemia nocturna**",
        "",
        f"El modelo proyecta una probabilidad del **{p_pct}%** de que la glucemia "
        f"llegue a menos de 70 mg/dL en las próximas horas.",
        f"Mínimo proyectado: **{g_min} mg/dL** hacia las "
        f"{assessment.projected_trough_time.strftime('%H:%M') if assessment.projected_trough_time else '?'}",
        "",
    ]

    if assessment.contributing_factors:
        lines.append("Factores que contribuyen:")
        for f in assessment.contributing_factors[:4]:
            lines.append(f"  • {f}")
        lines.append("")

    if assessment.severity == "critical":
        lines.append(
            "Considerá reducir el bolo, agregar una colación antes de dormir "
            "o revisar la dosis de basal con tu médico."
        )
    elif assessment.severity == "high":
        lines.append(
            "Considerá tomar una colación pequeña (10-15g de carbohidratos) "
            "o reducir el bolo propuesto."
        )
    else:
        lines.append(
            "Mantené una colación disponible y seteá una alarma de CGM "
            "en 70 mg/dL por si acaso."
        )

    return "\n".join(lines)
