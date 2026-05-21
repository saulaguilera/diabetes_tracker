"""
pmm/engines/explainability.py
──────────────────────────────
Descomposición causal del delta de glucosa.

Para cada ventana de tiempo [t_start, t_end], calcula cuánto aportó
cada componente fisiológico al cambio total de glucosa:

    ΔG_total = ΔG_IOB + ΔG_COB + ΔG_FPE + ΔG_ejercicio + ΔG_basal + ΔG_residual

Esta descomposición es la base del sistema de inferencia narrativa:
"Tu glucosa subió 35 mg/dL principalmente por la absorción de carbohidratos (+48)
 parcialmente contrarrestada por la insulina activa (-18)."

El residual (ΔG_residual) es la señal de anomalía:
    |residual| > 2σ_histórico → comportamiento no explicado por el modelo

Limitaciones conocidas:
    - El modelo es de primer orden (lineal) → impreciso en rangos extremos
    - FPE es una aproximación (el modelo real es no lineal)
    - El "residual" captura tanto anomalías como errores del modelo
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Optional


# ── Umbral de anomalía ────────────────────────────────────────────────────────
_RESIDUAL_SIGMA_THRESHOLD = 2.0   # |residual| > 2σ → flag de anomalía


def decompose_glucose_delta(
    t_start: datetime,
    t_end:   datetime,
) -> dict:
    """
    Descompone el cambio de glucosa en [t_start, t_end] en componentes causales.

    Retorna
    -------
    {
        ok            : bool
        delta_total   : float  — ΔG total observado (mg/dL)
        g_start       : float  — glucosa al inicio
        g_end         : float  — glucosa al final
        attributions  : {
            iob       : float  — efecto insulina activa (negativo = baja glucosa)
            cob       : float  — efecto carbohidratos (positivo = sube glucosa)
            fpe       : float  — efecto grasa/proteína tardío
            ejercicio : float  — efecto sensibilidad por ejercicio
            basal     : float  — efecto basal / fenómeno del alba
            residual  : float  — no explicado por el modelo
        }
        attribution_pct : { mismos keys, valores como % del |delta_total| }
        anomaly_flag  : bool   — True si |residual| > 2σ
        residual_sigma: float  — σ histórico del residual
        narrativa     : str    — descripción legible en español
        dominant_factor: str   — factor con mayor impacto absoluto
        error         : str | None
    }
    """
    result = {
        "ok":              False,
        "delta_total":     None,
        "g_start":         None,
        "g_end":           None,
        "attributions":    {},
        "attribution_pct": {},
        "anomaly_flag":    False,
        "residual_sigma":  None,
        "narrativa":       None,
        "dominant_factor": None,
        "error":           None,
    }

    try:
        from models import GlucoseReading, InsulinDose, Meal, Activity
        from helpers import _get_setting
        from utils.kinetics import (
            current_iob, current_cob, fat_protein_glucose_equiv,
            exercise_sensitivity_factor, dawn_roc_mgdl_min,
            _DEFAULT_PEAK_MIN, _DEFAULT_DIA_MIN,
        )

        isf_mu = _get_isf_for_decompose(t_start.hour)
        icr_mu = _get_icr_for_decompose(t_start.hour)
        if not isf_mu:
            result["error"] = "Sin ISF disponible"
            return result

        # Drift CUSUM: aplica al ISF para descomposición causal consistente
        # con la predicción y la calculadora de bolo.
        try:
            from pmm.engines.drift import get_drift_status
            drift_factor = get_drift_status().get("drift_factor", 1.0)
            if drift_factor and drift_factor != 1.0:
                isf_mu = isf_mu / max(0.1, drift_factor)
        except Exception:
            pass

        saved_dia = _get_setting("dia_min")
        dia_min = int(float(saved_dia)) if saved_dia else _DEFAULT_DIA_MIN

        # ── Glucosas al inicio y al final ─────────────────────────────────
        read_start = _nearest_reading(t_start, window_min=15)
        read_end   = _nearest_reading(t_end,   window_min=15)

        if not read_start or not read_end:
            result["error"] = "Sin lecturas de glucosa en la ventana"
            return result

        g_start = read_start.value_mgdl
        g_end   = read_end.value_mgdl
        delta_total = g_end - g_start

        result["g_start"]    = round(g_start, 1)
        result["g_end"]      = round(g_end, 1)
        result["delta_total"] = round(delta_total, 1)

        # ── Cargar eventos en la ventana ───────────────────────────────────
        dt_min = (t_end - t_start).total_seconds() / 60
        cutoff_wide = t_start - timedelta(hours=8)   # FPE necesita 8h atrás

        boluses = InsulinDose.query.filter(
            InsulinDose.type == "bolus",
            InsulinDose.timestamp >= t_start - timedelta(hours=dia_min / 60 + 1),
        ).all()

        meals_ext = Meal.query.filter(
            Meal.timestamp >= cutoff_wide,
        ).all()

        activities = Activity.query.filter(
            Activity.timestamp >= t_start - timedelta(hours=24),
        ).all()

        # ── IOB: efecto de insulina activa ────────────────────────────────
        iob_start = current_iob(boluses, at_time=t_start,
                                peak_min=_DEFAULT_PEAK_MIN, dia_min=dia_min)
        iob_end   = current_iob(boluses, at_time=t_end,
                                peak_min=_DEFAULT_PEAK_MIN, dia_min=dia_min)
        d_iob = iob_start - iob_end   # insulina consumida en la ventana (U)
        attr_iob = -d_iob * isf_mu    # negativo = baja glucosa

        # ── COB: efecto de carbohidratos activos ──────────────────────────
        cob_start = current_cob(meals_ext, at_time=t_start)
        cob_end   = current_cob(meals_ext, at_time=t_end)
        d_cob = cob_end - cob_start   # carbos netos absorbidos: positivo = sube
        attr_cob = (d_cob * isf_mu / icr_mu) if icr_mu else 0.0

        # ── FPE: grasa/proteína tardía ────────────────────────────────────
        # Acumulado en la ventana (aproximación: promedio)
        fpe_start = _compute_fpe(meals_ext, t_start) * isf_mu
        fpe_end   = _compute_fpe(meals_ext, t_end)   * isf_mu
        attr_fpe  = (fpe_end + fpe_start) / 2 * (dt_min / 60)
        attr_fpe  = max(0.0, attr_fpe)

        # ── Ejercicio: efecto de sensibilidad ─────────────────────────────
        ex_start = exercise_sensitivity_factor(activities, at_time=t_start)
        ex_end   = exercise_sensitivity_factor(activities, at_time=t_end)
        ex_delta = ex_start - ex_end   # cambio en factor durante la ventana
        # Si sensibilidad aumentó → glucosa bajó más de lo esperado
        attr_ejercicio = -ex_delta * iob_start * isf_mu * 0.5

        # ── Basal / alba ──────────────────────────────────────────────────
        dawn_roc   = dawn_roc_mgdl_min(at_time=t_start)
        attr_basal = dawn_roc * dt_min if dawn_roc > 0 else 0.0

        # ── Residual (anomalía) ───────────────────────────────────────────
        attr_sum     = attr_iob + attr_cob + attr_fpe + attr_ejercicio + attr_basal
        attr_residual = delta_total - attr_sum

        # ── Sigma histórico del residual ──────────────────────────────────
        residual_sigma = _get_residual_sigma()

        # ── Anomaly flag ──────────────────────────────────────────────────
        anomaly_flag = (
            residual_sigma > 0 and
            abs(attr_residual) > _RESIDUAL_SIGMA_THRESHOLD * residual_sigma
        )

        # ── Atribuciones ──────────────────────────────────────────────────
        attributions = {
            "iob":       round(attr_iob, 1),
            "cob":       round(attr_cob, 1),
            "fpe":       round(attr_fpe, 1),
            "ejercicio": round(attr_ejercicio, 1),
            "basal":     round(attr_basal, 1),
            "residual":  round(attr_residual, 1),
        }

        # Porcentajes sobre |delta_total| (evita div/0)
        scale = max(abs(delta_total), 5.0)
        attribution_pct = {
            k: round(v / scale * 100, 1)
            for k, v in attributions.items()
        }

        # Factor dominante
        dominant = max(attributions, key=lambda k: abs(attributions[k]))

        # ── Narrativa ─────────────────────────────────────────────────────
        narrativa = _build_narrative(
            delta_total, attributions, anomaly_flag, residual_sigma
        )

        result.update({
            "ok":              True,
            "attributions":    attributions,
            "attribution_pct": attribution_pct,
            "anomaly_flag":    anomaly_flag,
            "residual_sigma":  round(residual_sigma, 1) if residual_sigma else None,
            "narrativa":       narrativa,
            "dominant_factor": dominant,
        })

    except Exception as exc:
        result["error"] = str(exc)

    return result


def explain_last_hours(hours: float = 3.0) -> dict:
    """
    Descompone el cambio de glucosa de las últimas N horas.
    Acceso directo sin especificar timestamps.
    """
    t_end   = datetime.now()
    t_start = t_end - timedelta(hours=hours)
    return decompose_glucose_delta(t_start, t_end)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_isf_for_decompose(hora: int) -> Optional[float]:
    """ISF del PMM o fallback al cálculo clásico."""
    try:
        from pmm.core.parameter_store import get_isf_now
        isf = get_isf_now(hora=hora)
        if isf.get("mu") and isf["mu"] > 0:
            return isf["mu"]
    except Exception:
        pass
    try:
        from helpers import _calcular_isf_personal
        isf_val, _ = _calcular_isf_personal()
        return isf_val
    except Exception:
        return None


def _get_icr_for_decompose(hora: int) -> Optional[float]:
    """ICR del PMM o fallback al cálculo clásico."""
    try:
        from pmm.core.parameter_store import get_icr_now
        icr = get_icr_now(hora=hora)
        if icr.get("mu") and icr["mu"] > 0:
            return icr["mu"]
    except Exception:
        pass
    try:
        from helpers import _calcular_icr_personal
        icr_val, _ = _calcular_icr_personal()
        return icr_val
    except Exception:
        return None


def _nearest_reading(t: datetime, window_min: int = 15):
    """Lectura más cercana a `t` en una ventana ±window_min."""
    from models import GlucoseReading
    reads = (
        GlucoseReading.query
        .filter(
            GlucoseReading.timestamp >= t - timedelta(minutes=window_min),
            GlucoseReading.timestamp <= t + timedelta(minutes=window_min),
        )
        .order_by(GlucoseReading.timestamp)
        .all()
    )
    if not reads:
        return None
    return min(reads, key=lambda r: abs((r.timestamp - t).total_seconds()))


def _compute_fpe(meals, at_time: datetime) -> float:
    """
    Estima la tasa de glucosa equivalente de grasa/proteína (mg/dL/h)
    en `at_time`. Solo comidas con fat+protein relevante.
    """
    try:
        from utils.kinetics import fat_protein_glucose_equiv
        total_fpe_rate = 0.0
        for meal in meals:
            if not (meal.fat_g or meal.protein_g):
                continue
            fpe_g = fat_protein_glucose_equiv(
                meal.fat_g or 0.0, meal.protein_g or 0.0
            )
            if fpe_g < 5:
                continue
            elapsed_h = (at_time - meal.timestamp).total_seconds() / 3600
            if elapsed_h < 1 or elapsed_h > 8:
                continue
            # Modelo trapezoidal: pico a 2-3h, declina hasta 7-8h
            peak_h = 2.5
            if elapsed_h <= peak_h:
                rate = fpe_g / peak_h * (elapsed_h / peak_h)
            else:
                rate = fpe_g / peak_h * max(0, 1 - (elapsed_h - peak_h) / 5.5)
            total_fpe_rate += rate
        return total_fpe_rate
    except Exception:
        return 0.0


def _get_residual_sigma() -> float:
    """
    Sigma histórico del residual de predicción.
    Tomado del estado CUSUM si disponible.
    """
    try:
        from models import PMMDriftState
        state = PMMDriftState.query.first()
        if state and state.sigma_ref:
            return state.sigma_ref
    except Exception:
        pass
    return _DEFAULT_SIGMA_RESIDUAL


_DEFAULT_SIGMA_RESIDUAL = 20.0


def _build_narrative(
    delta: float,
    attrs: dict,
    anomaly: bool,
    sigma: float,
) -> str:
    """Genera narrativa legible en español a partir de las atribuciones."""
    lines = []

    direction = "subió" if delta > 0 else "bajó"
    lines.append(
        f"Tu glucosa {direction} {abs(delta):.0f} mg/dL."
    )

    # Factores con impacto significativo (>5 mg/dL o >10% del delta)
    relevant = {
        k: v for k, v in attrs.items()
        if k != "residual" and abs(v) > 5
    }

    if relevant:
        parts = []
        labels = {
            "iob":       "insulina activa",
            "cob":       "carbohidratos",
            "fpe":       "grasa/proteína tardía",
            "ejercicio": "efecto del ejercicio",
            "basal":     "secreción basal / fenómeno del alba",
        }
        # Ordenar por impacto absoluto descendente
        for k, v in sorted(relevant.items(), key=lambda x: -abs(x[1])):
            sign = "+" if v > 0 else ""
            parts.append(f"{labels.get(k, k)} ({sign}{v:.0f} mg/dL)")
        lines.append("Factores principales: " + ", ".join(parts) + ".")

    residual = attrs.get("residual", 0)
    if anomaly and sigma > 0:
        z = abs(residual) / sigma
        lines.append(
            f"⚠️ Comportamiento anormal: {abs(residual):.0f} mg/dL no explicados "
            f"por el modelo ({z:.1f}σ). Posibles causas: estrés, enfermedad, "
            f"error de registro o sensor defectuoso."
        )
    elif abs(residual) > 10:
        lines.append(
            f"Componente no explicada: {residual:+.0f} mg/dL "
            f"(dentro del rango esperable)."
        )

    return " ".join(lines)
