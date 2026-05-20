"""
pmm/engines/anomaly.py
───────────────────────
Puntuación compuesta de anomalía metabólica (0-100).

Combina tres señales complementarias con horizontes temporales distintos:

  1. CUSUM drift (sostenido, días-semanas)          peso 0.40
     Shift metabólico acumulativo detectado por el detector de drift.

  2. Residual puntual (episodio, últimas horas)     peso 0.35
     Error de predicción reciente (error_30 de GlucosePrediction).
     Captura episodios aislados no explicados por el modelo.

  3. Mahalanobis sobre estado actual (puntual)      peso 0.25
     Distancia del estado [G, ROC, IOB] al centro histórico del usuario.
     Detecta combinaciones inusuales aunque cada variable parezca normal
     por separado (ej: glucosa normal pero ROC muy rápido + IOB alto).

Niveles de alarma:
  0-30   → normal    — sin acción requerida
  30-55  → watch     — atención ligera
  55-75  → alert     — revisar estado
  75-100 → critical  — acción inmediata recomendada

Mahalanobis con covarianza diagonal:
    d² = z_G² + z_ROC² + z_IOB²

Para df=3, la función de supervivencia chi-cuadrado tiene forma cerrada:
    P(χ²(3) > d²) = exp(-d²/2) × (1 + d²/2)

Lo que permite convertir d² en p-valor sin scipy ni numpy.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta


# ── Pesos de cada componente ──────────────────────────────────────────────────
_W_CUSUM    = 0.40
_W_RESIDUAL = 0.35
_W_MAHAL    = 0.25

# ── Umbrales de nivel (mayor → menor) ────────────────────────────────────────
_LEVELS = [
    (75, "critical"),
    (55, "alert"),
    (30, "watch"),
    ( 0, "normal"),
]

# ── Ventana de residuales recientes ──────────────────────────────────────────
_RESIDUAL_WINDOW_H  = 3.0    # horas hacia atrás
_RESIDUAL_MAX_PREDS = 12     # máximo de predicciones a evaluar

# ── Mínimo de historia para componente Mahalanobis ───────────────────────────
_MIN_HIST_READINGS = 30


def compute_anomaly_score() -> dict:
    """
    Calcula el score compuesto de anomalía metabólica para el momento actual.

    Retorna
    -------
    {
        ok            : bool
        score         : int    — 0-100
        level         : str    — 'normal' | 'watch' | 'alert' | 'critical'
        components    : {
            drift_cusum : float  — componente drift CUSUM (0-100)
            residual    : float  — componente residual (0-100)
            mahalanobis : float  — componente Mahalanobis (0-100)
        }
        mahal_detail  : {        — desglose del estado actual vs. histórico
            g_now    : float, g_mu : float, g_sigma : float, z_g : float
            roc_now  : float, roc_mu: float, roc_sigma: float, z_roc: float
            iob_now  : float, iob_mu: float, iob_sigma: float, z_iob: float
            d_sq     : float, p_val: float
        }
        reasons       : [str]   — causas detectadas en lenguaje natural
        suggestions   : [str]   — sugerencias accionables
        narrativa     : str     — descripción en español
        computed_at   : str     — ISO timestamp
        error         : str | None
    }
    """
    result = {
        "ok":          False,
        "score":       0,
        "level":       "normal",
        "components":  {},
        "mahal_detail": {},
        "reasons":     [],
        "suggestions": [],
        "narrativa":   None,
        "computed_at": datetime.now().isoformat(),
        "error":       None,
    }

    try:
        reasons:     list[str] = []
        suggestions: list[str] = []

        # ── Componente 1: CUSUM drift ─────────────────────────────────────
        from pmm.engines.drift import get_drift_status
        drift         = get_drift_status()
        drift_intens  = float(drift.get("intensity", 0.0))
        cusum_score   = min(100.0, drift_intens * 100.0)

        if drift.get("drift_active"):
            drift_dir = drift.get("drift_dir")
            drift_h   = drift.get("drift_hours") or 0
            if drift_dir == "resistance":
                reasons.append(
                    f"Resistencia a la insulina sostenida ({drift_h:.0f}h, "
                    f"intensidad {drift_intens:.0%})"
                )
                suggestions.append(
                    "Revisa con tu médico si necesitas ajustar la pauta basal o los ratios."
                )
            elif drift_dir == "sensitivity":
                reasons.append(
                    f"Hipersensibilidad a la insulina sostenida ({drift_h:.0f}h, "
                    f"intensidad {drift_intens:.0%})"
                )
                suggestions.append(
                    "Mayor riesgo de hipoglucemia — ten carbohidratos de rescate a mano."
                )

        # ── Componente 2: Residual de predicción ──────────────────────────
        residual_score = _residual_component(reasons, suggestions)

        # ── Componente 3: Mahalanobis ─────────────────────────────────────
        mahal_score, mahal_detail = _mahalanobis_component(reasons, suggestions)

        # ── Score compuesto ───────────────────────────────────────────────
        composite = (
            _W_CUSUM    * cusum_score    +
            _W_RESIDUAL * residual_score +
            _W_MAHAL    * mahal_score
        )
        score = round(min(100.0, max(0.0, composite)))

        # ── Nivel ─────────────────────────────────────────────────────────
        level = "normal"
        for threshold, lvl in _LEVELS:
            if score >= threshold:
                level = lvl
                break

        result.update({
            "ok":          True,
            "score":       score,
            "level":       level,
            "components":  {
                "drift_cusum":  round(cusum_score,  1),
                "residual":     round(residual_score, 1),
                "mahalanobis":  round(mahal_score,  1),
            },
            "mahal_detail":  mahal_detail,
            "reasons":       reasons,
            "suggestions":   suggestions,
            "narrativa":     _build_narrative(score, level, reasons, drift),
        })

    except Exception as exc:
        result["error"] = str(exc)

    return result


# ── Componentes internos ───────────────────────────────────────────────────────

def _residual_component(reasons: list, suggestions: list) -> float:
    """
    Score 0-100 basado en los residuales de predicción de las últimas horas.
    Un error de 2×σ_ref = 100 puntos.
    """
    try:
        from models import GlucosePrediction, PMMDriftState

        cutoff = datetime.now() - timedelta(hours=_RESIDUAL_WINDOW_H)
        preds = (
            GlucosePrediction.query
            .filter(
                GlucosePrediction.resolved_30 == True,
                GlucosePrediction.error_30.isnot(None),
                GlucosePrediction.predicted_at >= cutoff,
            )
            .order_by(GlucosePrediction.predicted_at.desc())
            .limit(_RESIDUAL_MAX_PREDS)
            .all()
        )

        if not preds:
            return 0.0

        state     = PMMDriftState.query.first()
        sigma_ref = (state.sigma_ref if state and state.sigma_ref else 20.0)

        errors      = [abs(p.error_30) for p in preds]
        max_err     = max(errors)
        avg_err     = sum(errors) / len(errors)
        # 60% peor caso + 40% promedio — sensible a spikes pero sin ignorarlos
        combined    = 0.6 * max_err + 0.4 * avg_err
        score       = min(100.0, (combined / (2.0 * sigma_ref)) * 100.0)

        if score > 30:
            reasons.append(
                f"Error de predicción elevado: {avg_err:.0f} mg/dL en las últimas "
                f"{_RESIDUAL_WINDOW_H:.0f}h (σ_ref={sigma_ref:.0f} mg/dL)"
            )
        if score > 55:
            suggestions.append(
                "El modelo no está explicando bien tu glucosa — posible estrés, "
                "enfermedad, sensor defectuoso o datos de comida incorrectos."
            )

        return score

    except Exception:
        return 0.0


def _mahalanobis_component(
    reasons: list,
    suggestions: list,
) -> tuple[float, dict]:
    """
    Distancia de Mahalanobis sobre [G_actual, ROC_actual, IOB_actual].

    Covarianza diagonal (asume independencia de las features) → df=3.
    Para df=3: P(χ²(3)>d²) = exp(-d²/2)×(1+d²/2)   [forma cerrada exacta]

    Convierte p-valor a score: p=1→0, p=0.001→100.
    """
    detail: dict = {}

    try:
        from models import GlucoseReading, InsulinDose
        from utils.kinetics import current_iob, _DEFAULT_PEAK_MIN, _DEFAULT_DIA_MIN
        from helpers import _get_setting

        now          = datetime.now()
        cutoff_hist  = now - timedelta(days=30)

        # ── Glucosa actual y ROC ──────────────────────────────────────────
        recent = (
            GlucoseReading.query
            .filter(GlucoseReading.timestamp >= now - timedelta(minutes=30))
            .order_by(GlucoseReading.timestamp.desc())
            .limit(4)
            .all()
        )
        if not recent:
            return 0.0, detail

        g_now = recent[0].value_mgdl

        # ROC: pendiente de la regresión lineal simple sobre las últimas lecturas
        roc_now = 0.0
        if len(recent) >= 2:
            # Usar las dos lecturas más separadas para reducir ruido sensor
            r_new = recent[0]
            r_old = recent[-1]
            dt_min = (r_new.timestamp - r_old.timestamp).total_seconds() / 60
            if dt_min > 1:
                roc_now = (r_new.value_mgdl - r_old.value_mgdl) / dt_min

        # ── IOB actual ────────────────────────────────────────────────────
        dia_raw = _get_setting("dia_min")
        dia_min = int(float(dia_raw)) if dia_raw else _DEFAULT_DIA_MIN
        boluses = InsulinDose.query.filter(
            InsulinDose.type == "bolus",
            InsulinDose.timestamp >= now - timedelta(hours=dia_min / 60 + 1),
        ).all()
        iob_now = current_iob(
            boluses, at_time=now,
            peak_min=_DEFAULT_PEAK_MIN, dia_min=dia_min,
        )

        # ── Estadísticas históricas ───────────────────────────────────────
        hist = (
            GlucoseReading.query
            .filter(GlucoseReading.timestamp >= cutoff_hist)
            .order_by(GlucoseReading.timestamp)
            .all()
        )
        if len(hist) < _MIN_HIST_READINGS:
            return 0.0, detail

        g_vals = [r.value_mgdl for r in hist]
        g_mu, g_sigma = _mean_std(g_vals)

        # ROC histórico — solo pares con spacing CGM normal (4-16 min)
        roc_vals = []
        for i in range(1, min(len(hist), 300)):
            dt = (hist[i].timestamp - hist[i-1].timestamp).total_seconds() / 60
            if 4.0 <= dt <= 16.0:
                roc_vals.append(
                    (hist[i].value_mgdl - hist[i-1].value_mgdl) / dt
                )
        roc_mu, roc_sigma = _mean_std(roc_vals) if roc_vals else (0.0, 0.5)

        # IOB histórico: distribución de boluses como proxy de IOB típico
        # (la distribución del IOB puntual es difícil de estimar sin simular;
        #  usar la distribución de dosis captura el rango habitual de IOB)
        hist_boluses = InsulinDose.query.filter(
            InsulinDose.type == "bolus",
            InsulinDose.timestamp >= cutoff_hist,
        ).all()
        iob_vals = [b.units for b in hist_boluses if b.units and b.units > 0]
        iob_mu, iob_sigma = _mean_std(iob_vals) if iob_vals else (2.0, 1.5)

        # ── Z-scores y distancia de Mahalanobis ──────────────────────────
        z_g   = (g_now  - g_mu)   / max(g_sigma,   5.0)
        z_roc = (roc_now - roc_mu) / max(roc_sigma, 0.1)
        z_iob = (iob_now - iob_mu) / max(iob_sigma, 0.3)

        d_sq = z_g**2 + z_roc**2 + z_iob**2

        # P(χ²(3) > d²) = e^(-d²/2) × (1 + d²/2)  — exacto para df=3
        p_val = math.exp(-d_sq / 2.0) * (1.0 + d_sq / 2.0)
        p_val = max(1e-6, min(1.0, p_val))

        # Score: p=1→0, p=0.001→~100
        # Escala: -log10(p) / 3 * 100  (p=0.001 → -log10=3 → 100 pts)
        score = min(100.0, -math.log10(p_val) / 3.0 * 100.0)

        # ── Guardar detalle para debugging ────────────────────────────────
        detail = {
            "g_now":     round(g_now,   1),
            "g_mu":      round(g_mu,    1),
            "g_sigma":   round(g_sigma, 1),
            "z_g":       round(z_g,     2),
            "roc_now":   round(roc_now,  3),
            "roc_mu":    round(roc_mu,   3),
            "roc_sigma": round(roc_sigma,3),
            "z_roc":     round(z_roc,   2),
            "iob_now":   round(iob_now,  2),
            "iob_mu":    round(iob_mu,   2),
            "iob_sigma": round(iob_sigma,2),
            "z_iob":     round(z_iob,   2),
            "d_sq":      round(d_sq,    2),
            "p_val":     round(p_val,   4),
        }

        if score > 50:
            reasons.append(
                f"Estado metabólico inusual: G={g_now:.0f} mg/dL, "
                f"ROC={roc_now:+.2f} mg/dL/min, IOB={iob_now:.2f}U "
                f"(distancia Mahalanobis d²={d_sq:.1f})"
            )
        if score > 70:
            suggestions.append(
                "La combinación actual de glucosa + tendencia + insulina activa "
                "es atípica para tu historial — revisa tu estado y el sensor."
            )

        return score, detail

    except Exception:
        return 0.0, detail


# ── Utilidades ────────────────────────────────────────────────────────────────

def _mean_std(vals: list) -> tuple[float, float]:
    """Media y desviación estándar de una lista de floats."""
    n  = len(vals)
    mu = sum(vals) / n
    sigma = (sum((v - mu) ** 2 for v in vals) / max(n - 1, 1)) ** 0.5
    return mu, max(sigma, 1e-6)


def _build_narrative(
    score: int,
    level: str,
    reasons: list,
    drift: dict,
) -> str:
    """Narrativa legible en español del estado de anomalía."""
    if level == "normal":
        return (
            "Tu metabolismo está dentro de los parámetros habituales. "
            "No se detectaron patrones anómalos."
        )

    prefixes = {
        "watch":    "Leve desviación del patrón habitual detectada.",
        "alert":    "Se detectó un comportamiento inusual en tu metabolismo.",
        "critical": "⚠️ Anomalía metabólica significativa detectada.",
    }
    parts = [prefixes.get(level, "")]

    if reasons:
        # Máximo 2 razones para no saturar
        parts.append("Factores: " + "; ".join(reasons[:2]) + ".")

    if level in ("alert", "critical"):
        parts.append(
            "Si el patrón persiste o te sientes mal, "
            "consulta a tu médico o revisa tu pauta de insulina."
        )

    return " ".join(parts)
