"""
safety/confidence.py
─────────────────────
Sistema de Confianza Clínica Unificado.

Reemplaza la fragmentación actual de scores de confianza:
  - composite_confidence()    en audit_logger
  - hypo_risk.confidence      en hypo_risk_engine
  - _compute_confidence()     en daily_brief
  - ParameterState.confidence en parameter_store

Un único contrato: compute_confidence(...) → ConfidenceReport.

Modos de degradación
────────────────────
  full          → capacidades completas, alertas normales
  conservative  → thresholds más exigentes, lenguaje cauteloso
  observe_only  → solo observación, sin sugerencias de acción
  silent        → sensor malo o datos insuficientes, no emitir nada

Señales y pesos
───────────────
  sharpness          0.25 — σ_G del SSM posterior
  observability      0.25 — densidad y frescura de CGM
  sensor_health      0.20 — artefactos recientes
  innovation_quality 0.20 — bias y calibración del filtro
  model_freshness    0.10 — tiempo desde último update válido

Diseño
──────
- Todos los parámetros son optativos: la función carga desde DB si
  no se pasan, y tolera cualquier fallo silenciosamente.
- Para tests: pasar todos los valores explícitamente (sin DB).
- Para producción: llamar sin argumentos — carga todo automáticamente.
"""
from __future__ import annotations

import math
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger("safety.confidence")

# ── Umbrales de modo de operación ────────────────────────────────────────────
THRESHOLD_FULL         = 0.75
THRESHOLD_CONSERVATIVE = 0.50
THRESHOLD_OBSERVE_ONLY = 0.35
# < THRESHOLD_OBSERVE_ONLY → silent

# ── Pesos de componentes ──────────────────────────────────────────────────────
W_SHARPNESS   = 0.25
W_OBSERV      = 0.25
W_SENSOR      = 0.20
W_INNOVATION  = 0.20
W_FRESHNESS   = 0.10

# ── Referencias de normalización ─────────────────────────────────────────────
SIGMA_G_GOOD    = 10.0   # mg/dL — σ_G "bueno" (referencia para sharpness)
SIGMA_G_BAD     = 40.0   # mg/dL — σ_G "malo" (confidence → 0)
READINGS_FULL   = 12     # CGM readings en 6h = saturación (5-min interval = 72, pero Libre ~15min → 24)
MAX_GAP_OK      = 30     # min — gap entre lecturas sin penalización
MAX_GAP_BAD     = 120    # min — gap que lleva confidence a 0
STALE_OK_MIN    = 15     # min — sin penalización si última lectura < 15min
STALE_BAD_MIN   = 90     # min — confidence → 0 si hace > 90min sin lectura
ARTIFACTS_OK    = 0      # 0 artefactos → sin penalización
ARTIFACTS_BAD   = 4      # 4+ artefactos en 24h → confidence sensor → 0
MEAN_Z_OK       = 0.5    # |mean_z| < 0.5 → sin penalización
MEAN_Z_BAD      = 2.0    # |mean_z| > 2.0 → innovation quality → 0
VAR_Z_TARGET    = 1.0    # var_z ideal = 1.0 (calibración perfecta)
VAR_Z_MARGIN    = 0.5    # |var_z - 1.0| < 0.5 → sin penalización
VAR_Z_BAD       = 2.0    # |var_z - 1.0| > 2.0 → quality → 0
FRESHNESS_OK_H  = 2.0    # horas — modelo "fresco"
FRESHNESS_BAD_H = 12.0   # horas — modelo "vencido"


@dataclass
class ConfidenceReport:
    """
    Reporte completo de confianza del sistema.

    El campo `degradation_mode` determina el comportamiento de todos
    los módulos que usan este reporte:

      full          → operación normal
      conservative  → más cauteloso, thresholds más altos para alertas
      observe_only  → no recomendar acciones, solo mostrar estado
      silent        → no emitir alertas (datos malos o insuficientes)
    """
    score:              float           # 0-1 compuesto
    level:              str             # "high" | "medium" | "low" | "minimal"
    limiting_factor:    str             # componente que más baja el score
    should_recommend:   bool            # False si score < THRESHOLD_OBSERVE_ONLY
    degradation_mode:   str             # full | conservative | observe_only | silent
    explanation:        str             # frase humana breve
    components:         dict = field(default_factory=dict)
    # {sharpness, observability, sensor_health, innovation_quality, model_freshness}
    computed_at:        Optional[datetime] = None

    # ── Helpers de uso rápido ─────────────────────────────────────────────────

    def alert_threshold_boost(self) -> float:
        """
        Cuánto sumar al threshold de alerta base según confianza.
        Score < 0.50 → +0.15. Score < 0.35 → +inf (suprime alertas).
        """
        if self.score < THRESHOLD_OBSERVE_ONLY:
            return float("inf")   # supprime
        if self.score < THRESHOLD_CONSERVATIVE:
            return 0.15
        return 0.0

    def suppress_alerts(self) -> bool:
        """True si el sistema debe silenciar todas las alertas."""
        return self.degradation_mode in ("silent", "observe_only")

    def to_dict(self) -> dict:
        return {
            "score":            round(self.score, 3),
            "level":            self.level,
            "limiting_factor":  self.limiting_factor,
            "should_recommend": self.should_recommend,
            "degradation_mode": self.degradation_mode,
            "explanation":      self.explanation,
            "components":       {k: round(v, 3) for k, v in self.components.items()},
            "computed_at":      self.computed_at.isoformat() if self.computed_at else None,
        }


# ── Motor principal ───────────────────────────────────────────────────────────

def compute_confidence(
    now: Optional[datetime] = None,
    *,
    # ── 1. SSM sharpness ─────────────────────────────────────────────────────
    sigma_g: Optional[float] = None,          # σ_G del estado posterior
    cov_trace: Optional[float] = None,        # traza de P (fallback si no hay σ_G)
    n_cgm_used: Optional[int] = None,         # updates del filtro en el run
    # ── 2. Observability ─────────────────────────────────────────────────────
    n_readings_6h: Optional[int] = None,      # lecturas CGM en las últimas 6h
    max_gap_min: Optional[int] = None,        # mayor gap entre lecturas (min)
    minutes_since_last_cgm: Optional[int] = None,  # min desde última lectura
    # ── 3. Sensor health ─────────────────────────────────────────────────────
    artifacts_24h: Optional[int] = None,      # artefactos en últimas 24h
    # ── 4. Innovation quality ────────────────────────────────────────────────
    recent_mean_z: Optional[float] = None,    # media de innovation_z reciente
    recent_var_z: Optional[float] = None,     # varianza de innovation_z reciente
    # ── 5. Model freshness ───────────────────────────────────────────────────
    minutes_since_last_update: Optional[int] = None,  # min desde último CGM procesado
    minutes_since_pmm_update: Optional[int] = None,   # min desde última calibración PMM
) -> ConfidenceReport:
    """
    Calcula el ConfidenceReport unificado.

    Cuando no se pasan argumentos, carga automáticamente desde DB.
    Todos los fallos de DB se tratan silenciosamente (degradación graceful).
    """
    now = now or datetime.utcnow()

    # ── Auto-cargar señales desde DB si no se pasaron ─────────────────────────
    if _needs_db_load(sigma_g, n_readings_6h, max_gap_min, minutes_since_last_cgm,
                      artifacts_24h, recent_mean_z, recent_var_z,
                      minutes_since_last_update):
        loaded = _load_signals_from_db(now)
        if sigma_g            is None: sigma_g                = loaded.get("sigma_g")
        if n_readings_6h      is None: n_readings_6h          = loaded.get("n_readings_6h")
        if max_gap_min        is None: max_gap_min             = loaded.get("max_gap_min")
        if minutes_since_last_cgm is None:
            minutes_since_last_cgm = loaded.get("minutes_since_last_cgm")
        if artifacts_24h      is None: artifacts_24h           = loaded.get("artifacts_24h")
        if recent_mean_z      is None: recent_mean_z           = loaded.get("recent_mean_z")
        if recent_var_z       is None: recent_var_z            = loaded.get("recent_var_z")
        if minutes_since_last_update is None:
            minutes_since_last_update = loaded.get("minutes_since_last_update")

    # ── Calcular sub-scores ───────────────────────────────────────────────────
    s_sharpness   = _score_sharpness(sigma_g, cov_trace, n_cgm_used)
    s_observ      = _score_observability(n_readings_6h, max_gap_min, minutes_since_last_cgm)
    s_sensor      = _score_sensor_health(artifacts_24h)
    s_innovation  = _score_innovation_quality(recent_mean_z, recent_var_z)
    s_freshness   = _score_model_freshness(minutes_since_last_update, minutes_since_pmm_update)

    components = {
        "sharpness":          s_sharpness,
        "observability":      s_observ,
        "sensor_health":      s_sensor,
        "innovation_quality": s_innovation,
        "model_freshness":    s_freshness,
    }

    # ── Score compuesto ───────────────────────────────────────────────────────
    score = (
        W_SHARPNESS  * s_sharpness  +
        W_OBSERV     * s_observ     +
        W_SENSOR     * s_sensor     +
        W_INNOVATION * s_innovation +
        W_FRESHNESS  * s_freshness
    )
    score = round(max(0.0, min(1.0, score)), 3)

    # ── Factor limitante ──────────────────────────────────────────────────────
    limiting_factor = min(components, key=components.get)

    # ── Nivel y modo ──────────────────────────────────────────────────────────
    if score >= THRESHOLD_FULL:
        level = "high"
        mode  = "full"
    elif score >= THRESHOLD_CONSERVATIVE:
        level = "medium"
        mode  = "conservative"
    elif score >= THRESHOLD_OBSERVE_ONLY:
        level = "low"
        mode  = "observe_only"
    else:
        level = "minimal"
        mode  = "silent"

    should_recommend = (mode not in ("observe_only", "silent"))

    # ── Explicación humana ────────────────────────────────────────────────────
    explanation = _build_explanation(
        mode, limiting_factor, components,
        minutes_since_last_cgm, artifacts_24h, sigma_g,
    )

    return ConfidenceReport(
        score=score,
        level=level,
        limiting_factor=limiting_factor,
        should_recommend=should_recommend,
        degradation_mode=mode,
        explanation=explanation,
        components=components,
        computed_at=now,
    )


# ── Sub-scores individuales ───────────────────────────────────────────────────

def _score_sharpness(
    sigma_g: Optional[float],
    cov_trace: Optional[float],
    n_cgm_used: Optional[int],
) -> float:
    """
    Qué tan preciso es el estado del SSM.
    Alta confianza cuando σ_G es pequeña y hay suficientes updates.
    """
    if sigma_g is None and cov_trace is None:
        # Sin info del SSM: confianza media (no penalizar si no usamos SSM)
        return 0.55

    # Normalizar σ_G a [0,1] con función sigmoide
    if sigma_g is not None:
        # score = 1 cuando σ=0, → 0 cuando σ=SIGMA_G_BAD
        sigma_norm = max(0.0, min(sigma_g, SIGMA_G_BAD))
        sigma_score = 1.0 - (sigma_norm / SIGMA_G_BAD) ** 1.5
    else:
        # Usar traza como proxy (traza = suma de varianzas de todos los estados)
        # σ_G² típicamente ~ trace/6, pero escala conservadoramente
        sigma_proxy = math.sqrt(max(0.0, cov_trace) / 6.0) if cov_trace else SIGMA_G_BAD
        sigma_score = max(0.0, 1.0 - sigma_proxy / SIGMA_G_BAD)

    # Bonus por historial de updates (satura rápido)
    history_factor = 1.0
    if n_cgm_used is not None:
        history_factor = min(1.0, n_cgm_used / 8.0)   # 8 updates = factor completo

    return round(sigma_score * 0.7 + history_factor * 0.3, 3)


def _score_observability(
    n_readings: Optional[int],
    max_gap: Optional[int],
    minutes_since_last: Optional[int],
) -> float:
    """
    Calidad de la observación: cuántos datos y qué tan frescos.
    """
    # Sin info: confianza media-baja
    if n_readings is None and max_gap is None and minutes_since_last is None:
        return 0.50

    score = 1.0

    # Penalización por pocas lecturas
    if n_readings is not None:
        reading_score = min(1.0, n_readings / READINGS_FULL)
        score *= (0.5 + 0.5 * reading_score)   # [0.5, 1.0]

    # Penalización por gaps grandes
    if max_gap is not None:
        if max_gap <= MAX_GAP_OK:
            gap_factor = 1.0
        elif max_gap >= MAX_GAP_BAD:
            gap_factor = 0.0
        else:
            gap_factor = 1.0 - (max_gap - MAX_GAP_OK) / (MAX_GAP_BAD - MAX_GAP_OK)
        score *= (0.4 + 0.6 * gap_factor)

    # Penalización por lectura vieja
    if minutes_since_last is not None:
        if minutes_since_last <= STALE_OK_MIN:
            freshness_factor = 1.0
        elif minutes_since_last >= STALE_BAD_MIN:
            freshness_factor = 0.0
        else:
            freshness_factor = 1.0 - (minutes_since_last - STALE_OK_MIN) / (STALE_BAD_MIN - STALE_OK_MIN)
        score *= (0.3 + 0.7 * freshness_factor)

    return round(max(0.0, min(1.0, score)), 3)


def _score_sensor_health(artifacts_24h: Optional[int]) -> float:
    """
    Calidad del sensor basada en artefactos detectados.
    0 artefactos → 1.0. 4+ artefactos → 0.2 (nunca llega a 0 por artefactos solos).
    """
    if artifacts_24h is None:
        return 0.80   # sin info → confianza media-alta

    if artifacts_24h <= ARTIFACTS_OK:
        return 1.0
    if artifacts_24h >= ARTIFACTS_BAD:
        return 0.20

    # Degradación lineal
    return round(1.0 - 0.8 * (artifacts_24h / ARTIFACTS_BAD), 3)


def _score_innovation_quality(
    mean_z: Optional[float],
    var_z: Optional[float],
) -> float:
    """
    Calidad de las innovations del filtro.
    mean_z cercano a 0 → sin bias. var_z cercano a 1 → bien calibrado.
    """
    if mean_z is None and var_z is None:
        return 0.70   # sin info → confianza media

    score = 1.0

    # Penalización por bias (mean_z ≠ 0)
    if mean_z is not None:
        abs_mean = abs(mean_z)
        if abs_mean <= MEAN_Z_OK:
            bias_factor = 1.0
        elif abs_mean >= MEAN_Z_BAD:
            bias_factor = 0.0
        else:
            bias_factor = 1.0 - (abs_mean - MEAN_Z_OK) / (MEAN_Z_BAD - MEAN_Z_OK)
        score *= (0.3 + 0.7 * bias_factor)

    # Penalización por mala calibración (var_z ≠ 1)
    if var_z is not None:
        deviation = abs(var_z - VAR_Z_TARGET)
        if deviation <= VAR_Z_MARGIN:
            calib_factor = 1.0
        elif deviation >= VAR_Z_BAD:
            calib_factor = 0.0
        else:
            calib_factor = 1.0 - (deviation - VAR_Z_MARGIN) / (VAR_Z_BAD - VAR_Z_MARGIN)
        score *= (0.4 + 0.6 * calib_factor)

    return round(max(0.0, min(1.0, score)), 3)


def _score_model_freshness(
    minutes_since_last_update: Optional[int],
    minutes_since_pmm_update: Optional[int],
) -> float:
    """
    Cuán fresco está el modelo.
    Degrada lentamente — el SSM tiene memoria de 6h, el PMM de días.
    """
    if minutes_since_last_update is None and minutes_since_pmm_update is None:
        return 0.75   # sin info → confianza media-alta

    score = 1.0

    # Frescura del último update del filtro
    if minutes_since_last_update is not None:
        ok_min  = FRESHNESS_OK_H  * 60
        bad_min = FRESHNESS_BAD_H * 60
        if minutes_since_last_update <= ok_min:
            update_factor = 1.0
        elif minutes_since_last_update >= bad_min:
            update_factor = 0.10
        else:
            update_factor = 1.0 - 0.9 * (
                (minutes_since_last_update - ok_min) / (bad_min - ok_min)
            )
        score *= (0.2 + 0.8 * update_factor)

    # Frescura del PMM (degrada más lento — semanas)
    if minutes_since_pmm_update is not None:
        pmm_ok_min  = 24 * 60    # 1 día
        pmm_bad_min = 7 * 24 * 60  # 7 días
        if minutes_since_pmm_update <= pmm_ok_min:
            pmm_factor = 1.0
        elif minutes_since_pmm_update >= pmm_bad_min:
            pmm_factor = 0.30
        else:
            pmm_factor = 1.0 - 0.7 * (
                (minutes_since_pmm_update - pmm_ok_min) / (pmm_bad_min - pmm_ok_min)
            )
        score *= (0.5 + 0.5 * pmm_factor)

    return round(max(0.0, min(1.0, score)), 3)


# ── Carga automática desde DB ─────────────────────────────────────────────────

def _needs_db_load(*values) -> bool:
    """True si al menos una señal clave no está disponible."""
    return any(v is None for v in values)


def _load_signals_from_db(now: datetime) -> dict:
    """
    Carga todas las señales necesarias desde la base de datos.
    Tolera cualquier fallo — devuelve dict vacío si no hay app context.
    """
    result: dict = {}

    # ── Observability: lecturas CGM recientes ─────────────────────────────────
    try:
        from models import GlucoseReading
        cutoff_6h = now - timedelta(hours=6)
        readings = (
            GlucoseReading.query
            .filter(
                GlucoseReading.timestamp >= cutoff_6h,
                GlucoseReading.timestamp <= now,
                GlucoseReading.is_artifact == False,
            )
            .order_by(GlucoseReading.timestamp)
            .all()
        )
        result["n_readings_6h"] = len(readings)

        if readings:
            # Tiempo desde la última lectura
            last_reading = readings[-1]
            mins_since = (now - last_reading.timestamp).total_seconds() / 60.0
            result["minutes_since_last_update"] = int(mins_since)
            result["minutes_since_last_cgm"]    = int(mins_since)

            # Gap máximo entre lecturas consecutivas
            if len(readings) >= 2:
                gaps = [
                    (readings[i].timestamp - readings[i-1].timestamp).total_seconds() / 60.0
                    for i in range(1, len(readings))
                ]
                result["max_gap_min"] = int(max(gaps))
            else:
                result["max_gap_min"] = 360   # solo una lectura → gap "infinito"
        else:
            result["n_readings_6h"]          = 0
            result["minutes_since_last_cgm"] = 999
            result["max_gap_min"]            = 999
    except Exception as exc:
        logger.debug("_load_signals_from_db: CGM query failed — %s", exc)

    # ── Sensor health: artefactos en 24h ──────────────────────────────────────
    try:
        from models import GlucoseReading
        cutoff_24h = now - timedelta(hours=24)
        n_artifacts = (
            GlucoseReading.query
            .filter(
                GlucoseReading.timestamp >= cutoff_24h,
                GlucoseReading.is_artifact == True,
            )
            .count()
        )
        result["artifacts_24h"] = n_artifacts
    except Exception as exc:
        logger.debug("_load_signals_from_db: artifacts query failed — %s", exc)

    # ── Innovation quality: últimas 2h de SSM innovations ────────────────────
    try:
        from models import SSMInnovation
        import statistics
        cutoff_innov = now - timedelta(hours=2)
        recent_innovs = (
            SSMInnovation.query
            .filter(
                SSMInnovation.run_at >= cutoff_innov,
                SSMInnovation.rejected == False,
            )
            .order_by(SSMInnovation.run_at.desc())
            .limit(40)
            .all()
        )
        if len(recent_innovs) >= 4:
            z_vals = [r.innovation_z for r in recent_innovs if r.innovation_z is not None]
            if len(z_vals) >= 4:
                result["recent_mean_z"] = statistics.mean(z_vals)
                result["recent_var_z"]  = statistics.variance(z_vals)
    except Exception as exc:
        logger.debug("_load_signals_from_db: innovations query failed — %s", exc)

    # ── Model freshness: última calibración PMM ───────────────────────────────
    try:
        from models import PMMParameter
        last_pmm = (
            PMMParameter.query
            .order_by(PMMParameter.last_updated.desc())
            .first()
        )
        if last_pmm and last_pmm.last_updated:
            result["minutes_since_pmm_update"] = int(
                (now - last_pmm.last_updated).total_seconds() / 60.0
            )
    except Exception as exc:
        logger.debug("_load_signals_from_db: PMM freshness query failed — %s", exc)

    return result


# ── Explicación humana ────────────────────────────────────────────────────────

_LIMITING_FACTOR_LABELS = {
    "sharpness":          "incertidumbre alta del modelo",
    "observability":      "datos recientes insuficientes",
    "sensor_health":      "lecturas del sensor inconsistentes",
    "innovation_quality": "el modelo muestra desvío sistemático",
    "model_freshness":    "modelo desactualizado",
}

_MODE_INTROS = {
    "full":          "El sistema opera con plena confianza.",
    "conservative":  "El sistema opera en modo conservador.",
    "observe_only":  "El sistema puede observar pero no recomendar acciones.",
    "silent":        "Confianza insuficiente para emitir alertas.",
}


def _build_explanation(
    mode: str,
    limiting_factor: str,
    components: dict,
    minutes_since_last_cgm: Optional[int],
    artifacts_24h: Optional[int],
    sigma_g: Optional[float],
) -> str:
    """Genera explicación concisa en lenguaje humano."""
    intro = _MODE_INTROS.get(mode, "")

    if mode == "full":
        return intro

    # Descripción específica del factor limitante
    detail = _LIMITING_FACTOR_LABELS.get(limiting_factor, limiting_factor)

    extras = []
    if minutes_since_last_cgm is not None and minutes_since_last_cgm > STALE_OK_MIN:
        h = minutes_since_last_cgm // 60
        m = minutes_since_last_cgm % 60
        t_str = f"{h}h {m}m" if h > 0 else f"{m} min"
        extras.append(f"última lectura hace {t_str}")

    if artifacts_24h is not None and artifacts_24h >= 2:
        extras.append(f"{artifacts_24h} lecturas anómalas en las últimas 24h")

    if sigma_g is not None and sigma_g > SIGMA_G_GOOD * 2:
        extras.append(f"incertidumbre del modelo elevada (±{sigma_g:.0f} mg/dL)")

    if extras:
        return f"{intro} Razón: {detail} ({', '.join(extras)})."
    else:
        return f"{intro} Razón: {detail}."
