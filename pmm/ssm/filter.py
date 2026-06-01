"""
pmm/ssm/filter.py
──────────────────
API de alto nivel para correr el SSM contra los datos del usuario.

Diseño shadow-mode (SSM v0)
---------------------------
No persistimos el estado entre llamadas. En cada predicción:

  1. Reconstruimos el estado leyendo las últimas N horas de eventos
     (CGM, boluses, comidas, actividad).
  2. Inicializamos el UKF con un warm start desde la primera lectura.
  3. Avanzamos el filtro evento por evento hasta "now":
       - Para cada CGM: predict (Δt) + update
       - Para cada bolus/meal: aplicar como impulse en el step siguiente
  4. Hacemos forward sampling para +30 / +60 min usando los pesos del UKF.

Performance: ~50 CGM reads × ~1ms cada uno = ~50ms por predicción.
Aceptable para el endpoint actual que ya tarda 100-200ms.

Cuando agreguemos persistencia (SSM v1), reemplazamos esto por:
  - Leer último snapshot de twin_state
  - Avanzar solo los eventos nuevos desde ese snapshot
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

import numpy as np

from pmm.ssm.state       import (
    DIM_X, STATE_NAMES, state_index, STATE_BOUNDS,
    PROCESS_NOISE_DIAG, initial_state, S_I_DEFAULT,
    CGM_MARD_PCT, CGM_NOISE_FLOOR,
)
from pmm.ssm.dynamics    import step, StepInputs, k_a_for_meal_category
from pmm.ssm.basal_input import load_basal_doses, compute_basal_eff
from pmm.ssm.observation import h_cgm, R_cgm, gating_outlier
from pmm.ssm.ukf         import UKF

logger = logging.getLogger("pmm.ssm")


# ── Configuración ─────────────────────────────────────────────────────────

LOOKBACK_HOURS = 6           # cuánto histórico cargar para reconstruir estado
WARMUP_MIN     = 30          # primeros N min se usan para que el filter converja
MAX_DT_MIN     = 10.0        # Δt máximo entre updates (sino interpolamos)
OUTLIER_GATE_SIGMA = 5.0


# ── Eventos unificados ───────────────────────────────────────────────────

@dataclass(order=True)
class Event:
    """Evento ordenable por timestamp. payload depende de kind."""
    ts: datetime
    kind: str            # 'cgm' | 'bolus' | 'meal' | 'activity_end'
    payload: dict


def _load_events(now: datetime, hours: int = LOOKBACK_HOURS) -> list[Event]:
    """Lee eventos de las últimas `hours` horas y los unifica en una línea temporal."""
    from models import GlucoseReading, InsulinDose, Meal

    cutoff = now - timedelta(hours=hours)
    evts: list[Event] = []

    # CGM readings
    for r in GlucoseReading.query.filter(
        GlucoseReading.timestamp >= cutoff,
        GlucoseReading.timestamp <= now,
        GlucoseReading.is_artifact == False,   # excluir artefactos del filter
    ).order_by(GlucoseReading.timestamp).all():
        evts.append(Event(ts=r.timestamp, kind="cgm",
                          payload={"g": float(r.value_mgdl)}))

    # Boluses
    for d in InsulinDose.query.filter(
        InsulinDose.type == "bolus",
        InsulinDose.timestamp >= cutoff,
        InsulinDose.timestamp <= now,
    ).order_by(InsulinDose.timestamp).all():
        evts.append(Event(ts=d.timestamp, kind="bolus",
                          payload={"units": float(d.units or 0)}))

    # Meals
    for m in Meal.query.filter(
        Meal.timestamp >= cutoff,
        Meal.timestamp <= now,
        Meal.carbs_g > 0,
    ).order_by(Meal.timestamp).all():
        evts.append(Event(ts=m.timestamp, kind="meal",
                          payload={
                              "carbs": float(m.carbs_g),
                              "categoria": m.categoria,
                          }))

    evts.sort(key=lambda e: e.ts)
    return evts


# ── Process noise matrix ─────────────────────────────────────────────────

def _Q_for_dt(dt_min: float, params=None) -> np.ndarray:
    """
    Process noise Q escalado con Δt (Wiener increments).
    Diagonal — sin correlaciones (priors no informados).

    Cuando params se pasa, override exhaustivo de los Q_ por estado +
    INFLATION multiplier.
    """
    if params is not None:
        inflation = params.INFLATION
        diag = [
            params.Q_G       * max(0.1, dt_min) * inflation,
            params.Q_IOB     * max(0.1, dt_min) * inflation,
            params.Q_IOB_EFF * max(0.1, dt_min) * inflation,
            params.Q_COB1    * max(0.1, dt_min) * inflation,
            params.Q_COB2    * max(0.1, dt_min) * inflation,
            params.Q_SI      * max(0.1, dt_min) * inflation,
        ]
    else:
        diag = [PROCESS_NOISE_DIAG[n] * max(0.1, dt_min) for n in STATE_NAMES]
    return np.diag(diag)


def _R_cgm(g_estimated: float, params=None) -> np.ndarray:
    """R override-aware del CGM (multiplicativo MARD + floor)."""
    if params is not None:
        base = params.R_CGM_BASE
        mard = params.R_CGM_MARD
    else:
        base = CGM_NOISE_FLOOR
        mard = CGM_MARD_PCT
    sigma = max(base, mard * abs(g_estimated))
    return np.array([[sigma ** 2]])


# ── Filter run ───────────────────────────────────────────────────────────

@dataclass
class FilterResult:
    """Posterior + diagnósticos del run."""
    x:            np.ndarray            # mean (6,)
    P:            np.ndarray            # cov (6, 6)
    last_ts:      datetime
    n_cgm_used:   int = 0
    n_outliers:   int = 0
    log_evidence: float = 0.0
    n_steps:      int = 0
    error:        Optional[str] = None

    # ── Innovation log (granular, una entrada por update CGM) ──
    # Cada entrada: {ts, y_obs, y_pred, sigma_pred, g_state, p_g_g,
    #                rejected, log_likelihood}
    innovations: list = field(default_factory=list)

    # Última innovation (para audit rápido)
    last_innov:    Optional[float] = None
    last_innov_z:  Optional[float] = None


def run_filter(
    now:           datetime,
    hours:         Optional[int] = None,
    isf_prior:     Optional[float] = None,    # μ del PMM como prior
    isf_sigma:     Optional[float] = None,    # σ del PMM
    drift_factor:  float = 1.0,
    icr_for_meals: float = 12.0,
    pmm_speed_factors: Optional[dict] = None,
    params = None,    # SSMParameters — None usa defaults (backward compat)
) -> FilterResult:
    """
    Reconstruye el posterior del SSM al tiempo `now` procesando todos
    los eventos del lookback window.

    Returns
    -------
    FilterResult con (x, P, last_ts, log_evidence, contadores).
    """
    # Resolver params override
    from pmm.ssm.parameters import params_or_defaults
    p = params_or_defaults(params)
    if hours is None:
        hours = p.LOOKBACK_HOURS

    events = _load_events(now, hours=hours)
    cgm_events = [e for e in events if e.kind == "cgm"]

    # Hito 7: cargar historial de dosis basales (Toujeo) para computar
    # I_basal_eff(t) determinísticamente en cada step. Lookback amplio
    # (~5 days) cubre el efecto residual de cualquier dosis pasada.
    basal_doses = load_basal_doses(now)

    if not cgm_events:
        return FilterResult(
            x=np.zeros(DIM_X), P=np.eye(DIM_X),
            last_ts=now, error="sin lecturas CGM en lookback",
        )

    # ── Inicialización ───
    first_cgm = cgm_events[0]
    mu0, P0 = initial_state(
        g_init   = first_cgm.payload["g"],
        s_i_init = isf_prior,
        s_i_sigma= isf_sigma if isf_sigma and isf_sigma > 0 else p.S_I_SIGMA_INIT,
        g_sigma  = p.G_SIGMA_INIT,
        iob_sigma= p.IOB_SIGMA_INIT,
        cob_sigma= p.COB_SIGMA_INIT,
    )
    mu0 = np.array(mu0)
    P0  = np.array(P0)

    # Seed COB del histórico previo al lookback (warm start)
    cob1_warm, cob2_warm = _warm_start_cob(first_cgm.ts, pmm_speed_factors)
    mu0[state_index("COB1")] = cob1_warm
    mu0[state_index("COB2")] = cob2_warm

    iob_warm, iob_eff_warm = _warm_start_iob(first_cgm.ts)
    mu0[state_index("IOB")]     = iob_warm
    mu0[state_index("IOB_eff")] = iob_eff_warm

    ukf = UKF(dim_x=DIM_X, dim_z=1,
              alpha=p.UKF_ALPHA, beta=p.UKF_BETA, kappa=p.UKF_KAPPA)
    ukf.x = mu0
    ukf.P = P0

    # ── Filter loop ───
    t_prev      = first_cgm.ts
    n_cgm_used  = 0
    n_outliers  = 0
    log_evidence = 0.0
    n_steps     = 0
    s_i_target  = isf_prior if isf_prior else S_I_DEFAULT
    innovations: list[dict] = []      # log granular de cada update CGM
    last_innov   = None
    last_innov_z = None

    # Procesar eventos en orden (excluyendo el CGM inicial que ya inicializó)
    for evt in events:
        if evt.ts <= t_prev and not (evt.ts == first_cgm.ts and evt is first_cgm):
            continue   # ya consumido en init

        dt_min = (evt.ts - t_prev).total_seconds() / 60.0
        if dt_min < 0:
            continue
        if dt_min > MAX_DT_MIN * 4:
            # Gap grande — interpolar con un step largo pero inflar Q
            dt_min = min(dt_min, 60.0)

        # Inputs durante el step (no incluyen impulses todavía).
        # i_basal_eff computado al INICIO del step (t_prev) — cuasi-constante
        # durante el step (depot half-life ~20h ≫ dt típico ≤ 5min).
        i_basal_eff = compute_basal_eff(
            t_prev, basal_doses,
            K_depot=p.K_DEPOT_BASAL, K_pi=p.K_PI, K_ie=p.K_IE,
            F_bio=p.F_BIO_BASAL,
        )
        u = StepInputs(
            bolus_U=0.0, meal_g=0.0,
            k_a=k_a_for_meal_category(None,
                pmm_speed=(pmm_speed_factors or {}).get("MED", 1.0)),
            icr=icr_for_meals,
            s_i_target=s_i_target,
            i_basal_eff=i_basal_eff,
            drift_factor=drift_factor,
        )

        # Si el evento es un impulse, lo aplicamos en el step
        if evt.kind == "bolus":
            u.bolus_U = evt.payload["units"]
        elif evt.kind == "meal":
            u.meal_g = evt.payload["carbs"]
            u.k_a = k_a_for_meal_category(
                evt.payload.get("categoria"),
                pmm_speed=_bucket_speed(evt.payload.get("categoria"), pmm_speed_factors),
            )

        if dt_min > 0.01:
            ukf.predict(fx=lambda x: step(x, u, dt_min, params=p),
                        Q=_Q_for_dt(dt_min, params=p))
            n_steps += 1

        # Si el evento es CGM, update
        if evt.kind == "cgm":
            y = evt.payload["g"]
            # Predecir observación para detectar outlier
            g_pred  = ukf.x[state_index("G")]
            p_g_g   = float(max(0.0, ukf.P[state_index("G"), state_index("G")]))
            sig_g   = float(np.sqrt(p_g_g))
            R_obs   = _R_cgm(g_pred, params=p)
            sig_pred= float(np.sqrt(sig_g**2 + R_obs[0, 0]))
            innov   = float(y - g_pred)
            rejected = gating_outlier(y, g_pred, sig_pred, p.OUTLIER_GATE_SIGMA)

            inn_entry = {
                "ts":             evt.ts,
                "y_obs":          float(y),
                "y_pred":         float(g_pred),
                "sigma_pred":     sig_pred,
                "g_state":        float(g_pred),
                "p_g_g":          p_g_g,
                "rejected":       rejected,
                "log_likelihood": None,   # se llena si no es rechazado
            }

            if rejected:
                n_outliers += 1
                logger.debug(f"SSM: outlier CGM rechazado y={y} pred={g_pred:.0f}±{sig_pred:.0f}")
            else:
                ll = ukf.update(z=np.array([y]), hx=h_cgm, R=R_obs)
                log_evidence += ll
                n_cgm_used += 1
                inn_entry["log_likelihood"] = float(ll)
                # Tracking del último innovation (post-update sigma para audit)
                last_innov   = innov
                last_innov_z = innov / sig_pred if sig_pred > 0 else None

            innovations.append(inn_entry)

        # Aplicar bounds suaves al posterior (evitar derivas absurdas)
        _apply_bounds(ukf)

        t_prev = evt.ts

    # Avanzar el filter hasta `now` si quedó atrás
    dt_final = (now - t_prev).total_seconds() / 60.0
    if dt_final > 0.1:
        i_basal_eff_final = compute_basal_eff(
            t_prev, basal_doses,
            K_depot=p.K_DEPOT_BASAL, K_pi=p.K_PI, K_ie=p.K_IE,
            F_bio=p.F_BIO_BASAL,
        )
        u_final = StepInputs(
            bolus_U=0.0, meal_g=0.0, k_a=k_a_for_meal_category(None),
            icr=icr_for_meals, s_i_target=s_i_target,
            i_basal_eff=i_basal_eff_final,
            drift_factor=drift_factor,
        )
        dt_cap = min(dt_final, 60.0)
        ukf.predict(fx=lambda x: step(x, u_final, dt_cap, params=p),
                    Q=_Q_for_dt(dt_cap, params=p))
        n_steps += 1

    return FilterResult(
        x=ukf.x.copy(), P=ukf.P.copy(),
        last_ts=now,
        n_cgm_used=n_cgm_used,
        n_outliers=n_outliers,
        log_evidence=log_evidence,
        n_steps=n_steps,
        innovations=innovations,
        last_innov=last_innov,
        last_innov_z=last_innov_z,
    )


def _apply_bounds(ukf: UKF) -> None:
    """Clip suave del estado posterior a rangos fisiológicos."""
    for i, name in enumerate(STATE_NAMES):
        lo, hi = STATE_BOUNDS[name]
        ukf.x[i] = max(lo, min(hi, ukf.x[i]))


def _bucket_speed(categoria: Optional[str], pmm_speed: Optional[dict]) -> float:
    if not pmm_speed:
        return 1.0
    from pmm.engines.absorption import _CATEGORY_TO_BUCKET, _BUCKET_DEFAULT
    bucket = _CATEGORY_TO_BUCKET.get(categoria or "", _BUCKET_DEFAULT)
    return pmm_speed.get(bucket, 1.0)


def _warm_start_iob(t: datetime) -> tuple[float, float]:
    """IOB inicial desde el modelo biexponencial existente."""
    try:
        from utils.kinetics import current_iob, _DEFAULT_PEAK_MIN, _DEFAULT_DIA_MIN
        from models import InsulinDose
        boluses = InsulinDose.query.filter(
            InsulinDose.type == "bolus",
            InsulinDose.timestamp >= t - timedelta(minutes=_DEFAULT_DIA_MIN),
            InsulinDose.timestamp <= t,
        ).all()
        iob = current_iob(boluses, at_time=t,
                          peak_min=_DEFAULT_PEAK_MIN, dia_min=_DEFAULT_DIA_MIN)
        # Para iob_eff aproximamos: en steady-state, IOB_eff ≈ IOB × K_PI/K_IE
        from pmm.ssm.state import K_PI, K_IE
        iob_eff = iob * (K_PI / (K_PI + K_IE))
        return float(iob), float(iob_eff)
    except Exception:
        return 0.0, 0.0


def _warm_start_cob(t: datetime, pmm_speed: Optional[dict]) -> tuple[float, float]:
    """COB inicial desde las comidas recientes."""
    try:
        from utils.kinetics import current_cob_detailed
        from models import Meal
        meals = Meal.query.filter(
            Meal.timestamp >= t - timedelta(hours=4),
            Meal.timestamp <= t,
        ).all()
        detail = current_cob_detailed(meals, at_time=t,
                                       pmm_speed_factors=pmm_speed)
        # current_cob_detailed() NO devuelve la clave "cob"; expone carbs_cob /
        # total_cob / fat_cob / etc. El resto del sistema (get_kinetics_snapshot)
        # define "cob" := carbs_cob, así que usamos esa misma clave para mantener
        # la semántica de carbohidratos en el warm-start del SSM. Antes leía "cob"
        # (inexistente) → warm-start SIEMPRE 0 → modelo ciego a carbos al iniciar.
        cob_total = detail.get("carbs_cob", 0.0) if isinstance(detail, dict) else 0.0
        # Distribución 60/40 entre los dos compartimentos (heurística inicial)
        return float(cob_total * 0.55), float(cob_total * 0.45)
    except Exception:
        return 0.0, 0.0


# ── Predicción forward ───────────────────────────────────────────────────

@dataclass
class SSMPrediction:
    """Predicción del SSM para un horizonte específico."""
    horizon_min: int
    g_pred:      float
    sigma:       float
    p_hypo:      float   # P(G < 70) bajo gaussiana posterior
    p_hyper:     float   # P(G > 180)
    g_state_final: np.ndarray    # estado completo en t+horizon (para debug)


def forward_predict(
    result: FilterResult,
    horizons_min: tuple[int, ...] = (30, 60),
    drift_factor: float = 1.0,
    icr_for_meals: float = 12.0,
    exercise_drop_rate: float = 0.0,
    dawn_rate: float = 0.0,
    ex_sensitivity_mult: float = 1.0,
    params = None,
    i_basal_eff_override: Optional[float] = None,
) -> dict[int, SSMPrediction]:
    """
    Forward-sample el SSM desde el posterior actual hasta cada horizonte.

    Implementación: re-creamos un UKF temporal con el posterior, y aplicamos
    predict() sucesivos con steps de 5 min. La σ a cada horizon es
    sqrt(P[G,G] + R(G)) — incertidumbre de estado + ruido CGM.
    """
    if result.error or result.n_steps == 0:
        # No hay posterior útil — devolver placeholder
        return {h: SSMPrediction(
            horizon_min=h,
            g_pred=result.x[state_index("G")] if result.x is not None else float("nan"),
            sigma=999.0,
            p_hypo=0.0, p_hyper=0.0,
            g_state_final=result.x.copy() if result.x is not None else np.zeros(DIM_X),
        ) for h in horizons_min}

    import math
    from pmm.ssm.parameters import params_or_defaults
    p = params_or_defaults(params)

    ukf = UKF(dim_x=DIM_X, dim_z=1,
              alpha=p.UKF_ALPHA, beta=p.UKF_BETA, kappa=p.UKF_KAPPA)
    ukf.x = result.x.copy()
    ukf.P = result.P.copy()

    # Hito 7: cargar dosis basales para forward — la basal sigue actuando
    # durante el horizonte (peak ~12h post-dosis), su efecto NO es desprecible
    # en horizontes de 30-60 min.
    # i_basal_eff_override: valor constante inyectado externamente (útil en tests
    # o cuando load_basal_doses no tiene contexto de DB). None → cargar desde DB.
    basal_doses = load_basal_doses(result.last_ts)

    # Inputs del horizonte (sin nuevos boluses/meals — predicción "as is")
    u_template = StepInputs(
        bolus_U=0.0, meal_g=0.0,
        k_a=k_a_for_meal_category(None),
        icr=icr_for_meals,
        s_i_target=result.x[state_index("S_I")],   # mantener
        dawn_rate=dawn_rate,
        exercise_drop_rate=exercise_drop_rate,
        ex_sensitivity_mult=ex_sensitivity_mult,
        drift_factor=drift_factor,
    )

    STEP_MIN = 5.0
    preds: dict[int, SSMPrediction] = {}
    t_sim   = 0.0
    targets = sorted(horizons_min)

    while targets:
        dt = min(STEP_MIN, targets[0] - t_sim)
        if dt <= 0:
            h_now = targets.pop(0)
            preds[h_now] = _snapshot_prediction(ukf, h_now)
            continue
        # Recomputar I_basal_eff en cada step (varía suavemente con t_sim)
        if i_basal_eff_override is not None:
            i_basal = i_basal_eff_override
        else:
            t_step = result.last_ts + timedelta(minutes=t_sim)
            i_basal = compute_basal_eff(
                t_step, basal_doses,
                K_depot=p.K_DEPOT_BASAL, K_pi=p.K_PI, K_ie=p.K_IE,
                F_bio=p.F_BIO_BASAL,
            )
        u = StepInputs(**{**u_template.__dict__, "i_basal_eff": i_basal})
        ukf.predict(fx=lambda x: step(x, u, dt, params=p), Q=_Q_for_dt(dt, params=p))
        t_sim += dt
        if abs(t_sim - targets[0]) < 1e-6:
            h_now = targets.pop(0)
            preds[h_now] = _snapshot_prediction(ukf, h_now, params=p)

    return preds


def _snapshot_prediction(ukf: UKF, h: int, params=None) -> SSMPrediction:
    import math
    g_pred = float(ukf.x[state_index("G")])
    var_g  = float(ukf.P[state_index("G"), state_index("G")])
    sig_g  = max(1.0, math.sqrt(max(0.0, var_g)))
    # σ_total observable incluye el ruido CGM (lo que el bench va a comparar)
    R = _R_cgm(g_pred, params=params)[0, 0]
    sig_total = math.sqrt(sig_g**2 + R)

    # P(G<70) y P(G>180) bajo N(g_pred, sig_total²)
    def _p_below(thresh: float) -> float:
        from math import erf, sqrt
        z = (thresh - g_pred) / max(1.0, sig_total)
        return 0.5 * (1 + erf(z / sqrt(2)))

    return SSMPrediction(
        horizon_min=h,
        g_pred=round(g_pred, 1),
        sigma=round(sig_total, 2),
        p_hypo=round(_p_below(70.0), 3),
        p_hyper=round(1 - _p_below(180.0), 3),
        g_state_final=ukf.x.copy(),
    )
