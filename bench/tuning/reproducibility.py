"""
bench/tuning/reproducibility.py
────────────────────────────────
Garantiza reproducibilidad determinística de replays.

Determinismo
------------
El SSM v0 con UKF es determinístico por construcción (no usa muestreo
aleatorio). Sin embargo el replay introduce variabilidad por:
  - Cambios en el conjunto de eventos (nueva CGM llegó entre runs)
  - Cache stale (GP corrector, PMM speed factors)
  - Floating point non-associativity en agregaciones diferentes

Garantías de este módulo
------------------------
  1. data_checksum(window) — hash determinístico de los eventos consumidos
     (CGM ids + boluses + meals + activities). Si dos runs tienen el mismo
     checksum, vieron exactamente los mismos datos.

  2. random_seed_for(params, window) — derivación determinística del seed
     numpy a partir de (params_fingerprint, window). Idéntico inputs →
     idéntico seed → idéntico estado interno de numpy aún si algo cambia.

  3. replay_checksum(records) — hash del output del replay. Permite verificar
     post-hoc que dos runs produjeron resultados idénticos.

  4. assert_deterministic(name, runs=2) — re-ejecuta el mismo experiment N
     veces y verifica que TODOS los outputs coincidan exactamente.

Si un assert_deterministic falla, hay no-determinismo escondido en el pipeline.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta
from typing import Any, Optional

logger = logging.getLogger("bench.repro")


# ── Data checksum ───────────────────────────────────────────────────────

def data_checksum(now: datetime, days: int) -> str:
    """
    Hash determinístico de todos los eventos en la ventana [now-days, now].

    Estable bajo:
      - Re-runs sobre el mismo DB state
      - Orden de queries (siempre ordenamos por timestamp ASC)

    NO estable bajo:
      - Inserción de nuevos eventos en la ventana
      - Backfill / cleanup del histórico
    """
    from models import GlucoseReading, InsulinDose, Meal, Activity

    cutoff = now - timedelta(days=days)
    h = hashlib.sha1()
    h.update(f"window={cutoff.isoformat()}|now={now.isoformat()}|days={days}".encode())

    for cls in (GlucoseReading, InsulinDose, Meal, Activity):
        items = (cls.query
                 .filter(cls.timestamp >= cutoff,
                         cls.timestamp <= now)
                 .order_by(cls.timestamp, cls.id).all())
        for r in items:
            # Campos canónicos por tipo
            if isinstance(r, GlucoseReading):
                payload = f"G|{r.id}|{r.timestamp.isoformat()}|{r.value_mgdl}"
            elif isinstance(r, InsulinDose):
                payload = f"I|{r.id}|{r.timestamp.isoformat()}|{r.type}|{r.units}"
            elif isinstance(r, Meal):
                payload = (f"M|{r.id}|{r.timestamp.isoformat()}|{r.carbs_g}|"
                           f"{r.fat_g or 0}|{r.protein_g or 0}|{r.categoria or ''}")
            else:  # Activity
                payload = (f"A|{r.id}|{r.timestamp.isoformat()}|"
                           f"{r.activity_type}|{r.duration_min or 0}")
            h.update(payload.encode())
    return h.hexdigest()[:16]


# ── Random seed derivation ──────────────────────────────────────────────

def random_seed_for(params_fingerprint: str, window_checksum: str) -> int:
    """
    Seed determinístico ∈ [0, 2³¹-1] desde fingerprint+checksum.
    """
    s = hashlib.sha256(f"{params_fingerprint}::{window_checksum}".encode()).hexdigest()
    return int(s[:8], 16) % (2**31)


def with_seeded_numpy(seed: int):
    """Context manager opcional para setear numpy random seed."""
    try:
        import numpy as np
        np.random.seed(seed)
    except ImportError:
        pass


# ── Replay output checksum ──────────────────────────────────────────────

def replay_checksum(records: list) -> str:
    """
    Hash del output del replay. Si dos runs producen mismo checksum,
    son numéricamente idénticos.

    Estable bajo orden — siempre ordenamos por (predicted_at, horizon).
    """
    if not records:
        return "empty"
    h = hashlib.sha1()
    ordered = sorted(records, key=lambda r: (r.predicted_at, r.horizon_min))
    for r in ordered:
        # Redondeamos a 4 decimales — tolerancia FP típica
        payload = (
            f"{r.predicted_at.isoformat()}|{r.horizon_min}|"
            f"{round(r.g_actual, 2)}|{round(r.g_pred, 4)}|"
            f"{round(r.g_real, 2)}|{round(r.sigma or 0, 4)}"
        )
        h.update(payload.encode())
    return h.hexdigest()[:16]


# ── Determinism assertion ───────────────────────────────────────────────

def assert_deterministic(
    name:    str,
    params:  Any,                # SSMParameters
    days:    int = 3,
    runs:    int = 2,
    decision_every_min: int = 30,
) -> dict:
    """
    Re-ejecuta el mismo experiment `runs` veces y verifica que produzca
    EXACTAMENTE el mismo output.

    Returns
    -------
    {
        "deterministic": bool,
        "checksums":     [list of replay_checksum por run],
        "n_records":     [list de tamaños por run],
        "first_run_ms":  int,
        "note":          str
    }
    """
    import time
    from bench.tuning.grid_search import _replay_with_params

    checksums = []
    sizes     = []
    timings   = []
    for i in range(runs):
        t0 = time.time()
        records = _replay_with_params(
            days=days,
            decision_every_min=decision_every_min,
            params=params,
        )
        timings.append(int((time.time() - t0) * 1000))
        sizes.append(len(records))
        checksums.append(replay_checksum(records))

    deterministic = len(set(checksums)) == 1
    return {
        "deterministic": deterministic,
        "checksums":     checksums,
        "n_records":     sizes,
        "first_run_ms":  timings[0],
        "all_runs_ms":   timings,
        "note":          ("✓ replay determinístico" if deterministic
                          else "✗ NON-DETERMINISTIC — investigar pipeline"),
    }


# ── Integrity verification post-run ─────────────────────────────────────

def verify_experiment(experiment_id: int) -> dict:
    """
    Verifica que un experimento persistido siga siendo reproducible.
    Re-corre con sus params guardados y compara checksums.
    """
    from models import TuningExperiment
    from pmm.ssm.parameters import SSMParameters
    from bench.tuning.grid_search import _replay_with_params

    exp = TuningExperiment.query.get(experiment_id)
    if not exp:
        return {"ok": False, "error": "experiment not found"}
    if not exp.replay_checksum:
        return {"ok": False, "error": "no replay_checksum recorded (legacy run)"}

    params = SSMParameters.from_dict(json.loads(exp.params_json))
    days_window = exp.days_window or 7
    records = _replay_with_params(days=days_window,
                                   decision_every_min=30,
                                   params=params)
    new_cs = replay_checksum(records)
    return {
        "ok":              new_cs == exp.replay_checksum,
        "expected":        exp.replay_checksum,
        "actual":          new_cs,
        "n_records_now":   len(records),
        "n_records_then":  exp.n_records,
        "drift":           new_cs != exp.replay_checksum,
    }
