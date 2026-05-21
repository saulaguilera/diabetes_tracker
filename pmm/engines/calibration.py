"""
pmm/engines/calibration.py
───────────────────────────
Pipeline de recalibración del Personal Metabolic Model.

Punto de entrada: run_calibration()

Flujo:
  1. Cargar episodios de corrección de los últimos N días
  2. Filtrar los que ya fueron procesados (via PMMObservation)
  3. Evaluar calidad de cada nuevo episodio
  4. Actualizar parámetros Bayesianos (por bloque horario + global)
  5. Guardar historial en PMMObservation
  6. Repetir para episodios de comida (ICR)

Bootstrap:
  Si no hay parámetros aprendidos (primera corrida), procesa todos
  los episodios históricos en orden cronológico para inicializar el modelo.

Thread-safety:
  La función usa un lock de DB implícito a través de commits individuales.
  En entorno multi-worker (gunicorn) esto es suficiente para el MVP.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger("pmm.calibration")

# Ventana de búsqueda de nuevos episodios (para runs incrementales)
INCREMENTAL_WINDOW_HOURS = 4

# Días de historia para bootstrap inicial
BOOTSTRAP_DAYS = 180


# ── Función principal ──────────────────────────────────────────────────────────

def run_calibration(force_bootstrap: bool = False) -> dict:
    """
    Corre el pipeline completo de recalibración.

    Parámetros
    ----------
    force_bootstrap : si True, reprocesa TODA la historia (útil para debug
                      o tras cambios en el algoritmo de evaluación)

    Retorna dict con estadísticas del run:
        {
            isf_updates: int,
            icr_updates: int,
            isf_skipped: int,
            icr_skipped: int,
            bootstrap:   bool,
            duration_ms: int,
            errors:      list[str],
        }
    """
    t_start = datetime.utcnow()
    stats = {
        "isf_updates":        0,
        "icr_updates":        0,
        "absorption_updates": 0,
        "isf_skipped":        0,
        "icr_skipped":        0,
        "bootstrap":          False,
        "duration_ms":        0,
        "errors":             [],
    }

    try:
        from models import PMMParameter, PMMObservation
        from pmm.core.parameter_store import (
            load_parameter, save_parameter, get_isf_now,
        )
        from pmm.engines.observation import (
            load_correction_episodes, load_meal_episodes,
        )

        # ── Determinar si es bootstrap ─────────────────────────────────────────
        n_params = PMMParameter.query.count()
        n_obs    = PMMObservation.query.count()
        is_bootstrap = force_bootstrap or (n_params == 0 and n_obs == 0)
        stats["bootstrap"] = is_bootstrap

        # ── Reset si es bootstrap forzado ──────────────────────────────────────
        # Sin esto, re-ejecutar bootstrap procesaría las MISMAS observaciones
        # múltiples veces → n_obs inflado, σ colapsado bajo el floor,
        # parámetros corrompidos.
        if force_bootstrap and (n_params > 0 or n_obs > 0):
            from models import db
            PMMObservation.query.delete()
            PMMParameter.query.delete()
            # También resetear el CUSUM (los residuales previos asumían params viejos)
            try:
                from pmm.engines.drift import reset_cusum
                reset_cusum()
            except Exception:
                pass
            db.session.commit()
            logger.info(
                f"PMM bootstrap forzado: borrados {n_params} parámetros + "
                f"{n_obs} observaciones; CUSUM reseteado"
            )
            stats["reset_params"] = n_params
            stats["reset_obs"]    = n_obs

        days = BOOTSTRAP_DAYS if is_bootstrap else INCREMENTAL_WINDOW_HOURS / 24 * 2

        # ── ISF: episodios de corrección ───────────────────────────────────────
        logger.info(f"PMM calibration: {'bootstrap' if is_bootstrap else 'incremental'}, "
                    f"procesando {int(days)} días")

        correction_episodes = load_correction_episodes(days=int(days))

        # IDs ya procesados (no reprocesar)
        processed_ids = set()
        if not is_bootstrap:
            from models import db
            from sqlalchemy import text
            rows = db.session.execute(
                text("SELECT source_id FROM pmm_observations "
                     "WHERE param_name='ISF' AND source_id IS NOT NULL")
            ).fetchall()
            processed_ids = {r[0] for r in rows}

        for ep in correction_episodes:
            dose_id = ep.get("dose_id")

            # Skip si ya fue procesado
            if dose_id in processed_ids:
                continue

            _process_isf_episode(ep, stats)

        # ── ICR: episodios de comida ───────────────────────────────────────────
        # Usar ISF actual del PMM para descontar correcciones
        isf_state = load_parameter("ISF", context_block=-1)
        isf_mu = isf_state.mu if isf_state.n_obs >= 3 else 45.0

        meal_episodes = load_meal_episodes(days=int(days), isf_mu=isf_mu)

        processed_meal_ids = set()
        if not is_bootstrap:
            from models import db
            from sqlalchemy import text
            rows = db.session.execute(
                text("SELECT source_id FROM pmm_observations "
                     "WHERE param_name='ICR' AND source_id IS NOT NULL")
            ).fetchall()
            processed_meal_ids = {r[0] for r in rows}

        for ep in meal_episodes:
            dose_id = ep.get("dose_id")
            if dose_id in processed_meal_ids:
                continue
            _process_icr_episode(ep, stats)

        # ── Absorción: speed_factor por categoría ──────────────────────────────
        from pmm.engines.absorption import run_absorption_calibration
        abs_stats = run_absorption_calibration(force_bootstrap=is_bootstrap)
        stats["absorption_updates"] = abs_stats.get("updates", 0)
        if abs_stats.get("errors"):
            stats["errors"].extend(abs_stats["errors"])

    except Exception as exc:
        logger.exception("Error en PMM calibration")
        stats["errors"].append(str(exc))

    stats["duration_ms"] = int(
        (datetime.utcnow() - t_start).total_seconds() * 1000
    )
    logger.info(
        f"PMM calibration done: ISF +{stats['isf_updates']} skip={stats['isf_skipped']}, "
        f"ICR +{stats['icr_updates']} skip={stats['icr_skipped']}, "
        f"{stats['duration_ms']}ms"
    )
    return stats


# ── Helpers internos ───────────────────────────────────────────────────────────

def _process_isf_episode(ep: dict, stats: dict) -> None:
    """Procesa un episodio de corrección y actualiza el ISF."""
    from models import PMMObservation, db
    from pmm.core.parameter_store import load_parameter, save_parameter

    dose_id    = ep.get("dose_id")
    obs_at     = ep.get("observed_at")
    time_block = ep.get("time_block", -1)

    # Registrar el episodio (aunque no sea usable)
    obs_row = PMMObservation(
        param_name   = "ISF",
        source_type  = "correction_bolus",
        source_id    = dose_id,
        observed_at  = obs_at,
        time_block   = time_block,
        quality_score = ep.get("quality_score", 0.0),
        observed_value= ep.get("observed_value"),
        obs_sigma    = ep.get("obs_sigma"),
        used_in_update= False,
        skip_reason  = ep.get("skip_reason"),
    )

    if not ep.get("usable"):
        db.session.add(obs_row)
        db.session.commit()
        stats["isf_skipped"] += 1
        return

    isf_obs   = ep["observed_value"]
    obs_sigma = ep["obs_sigma"]

    # Actualizar bloque horario específico
    state_block  = load_parameter("ISF", context_block=time_block)
    obs_row.mu_before    = state_block.mu
    obs_row.sigma_before = state_block.sigma

    new_state_block = state_block.bayesian_update(isf_obs, obs_sigma, param_name="ISF")
    save_parameter("ISF", time_block, new_state_block)

    obs_row.mu_after    = new_state_block.mu
    obs_row.sigma_after = new_state_block.sigma

    # Actualizar global (todos los bloques contribuyen al global)
    state_global  = load_parameter("ISF", context_block=-1)
    new_state_global = state_global.bayesian_update(isf_obs, obs_sigma, param_name="ISF")
    save_parameter("ISF", -1, new_state_global)

    obs_row.used_in_update = True
    db.session.add(obs_row)
    db.session.commit()
    stats["isf_updates"] += 1


def _process_icr_episode(ep: dict, stats: dict) -> None:
    """Procesa un episodio de comida y actualiza el ICR."""
    from models import PMMObservation, db
    from pmm.core.parameter_store import load_parameter, save_parameter

    dose_id    = ep.get("dose_id")
    obs_at     = ep.get("observed_at")
    time_block = ep.get("time_block", -1)

    obs_row = PMMObservation(
        param_name    = "ICR",
        source_type   = "meal_bolus",
        source_id     = dose_id,
        observed_at   = obs_at,
        time_block    = time_block,
        quality_score  = ep.get("quality_score", 0.0),
        observed_value = ep.get("observed_value"),
        obs_sigma      = ep.get("obs_sigma"),
        used_in_update = False,
        skip_reason    = ep.get("skip_reason"),
    )

    if not ep.get("usable"):
        db.session.add(obs_row)
        db.session.commit()
        stats["icr_skipped"] += 1
        return

    icr_obs   = ep["observed_value"]
    obs_sigma = ep["obs_sigma"]

    state_block = load_parameter("ICR", context_block=time_block)
    obs_row.mu_before    = state_block.mu
    obs_row.sigma_before = state_block.sigma

    new_state_block = state_block.bayesian_update(icr_obs, obs_sigma, param_name="ICR")
    save_parameter("ICR", time_block, new_state_block)

    obs_row.mu_after    = new_state_block.mu
    obs_row.sigma_after = new_state_block.sigma

    state_global    = load_parameter("ICR", context_block=-1)
    new_state_global = state_global.bayesian_update(icr_obs, obs_sigma, param_name="ICR")
    save_parameter("ICR", -1, new_state_global)

    obs_row.used_in_update = True
    db.session.add(obs_row)
    db.session.commit()
    stats["icr_updates"] += 1


# ── Diagnóstico ────────────────────────────────────────────────────────────────

def get_calibration_summary() -> dict:
    """
    Resumen del estado de calibración del PMM.
    Útil para el endpoint /api/pmm/state.
    """
    from models import PMMParameter, PMMObservation
    from pmm.core.parameter_store import get_isf_now, get_icr_now, get_all_blocks

    isf_now = get_isf_now()
    icr_now = get_icr_now()

    n_isf_obs = PMMObservation.query.filter_by(
        param_name="ISF", used_in_update=True
    ).count()
    n_icr_obs = PMMObservation.query.filter_by(
        param_name="ICR", used_in_update=True
    ).count()

    last_obs = (
        PMMObservation.query
        .order_by(PMMObservation.created_at.desc())
        .first()
    )

    # Evolución de ISF (últimas 20 actualizaciones)
    isf_history = (
        PMMObservation.query
        .filter_by(param_name="ISF", used_in_update=True)
        .order_by(PMMObservation.observed_at)
        .limit(50)
        .all()
    )
    isf_curve = [
        {
            "t":      obs.observed_at.isoformat() if obs.observed_at else None,
            "mu":     obs.mu_after,
            "sigma":  obs.sigma_after,
            "val":    obs.observed_value,
            "q":      obs.quality_score,
            "block":  obs.time_block,
        }
        for obs in isf_history
        if obs.mu_after is not None
    ]

    return {
        "isf":          isf_now,
        "icr":          icr_now,
        "isf_blocks":   get_all_blocks("ISF"),
        "icr_blocks":   get_all_blocks("ICR"),
        "n_isf_obs":    n_isf_obs,
        "n_icr_obs":    n_icr_obs,
        "last_updated": last_obs.created_at.isoformat() if last_obs else None,
        "isf_learning_curve": isf_curve,
        "ready": isf_now.get("source") != "prior",
    }
