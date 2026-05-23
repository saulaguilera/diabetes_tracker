"""
utils/sensor_quality.py
────────────────────────
Detección automática de artefactos del CGM.

El sensor Libre (y CGMs en general) ocasionalmente reporta valores
spurious por:
  - Compresión local del sensor (dormir sobre el brazo)
  - Scan failure con interpolación incorrecta
  - Hidratación local (sudor, agua, lociones)
  - Sensor cerca del final de vida útil

La app oficial del Libre suele **corregir retroactivamente** esos puntos
a los pocos minutos. Pero las apps de terceros (como esta) que consumen
la API ya recibieron el punto malo. Sin detección, ese punto:
  - Aparece como "hipo" en el chart aunque no haya ocurrido
  - Dispara alertas hipo falsas
  - Contamina las métricas (TIR, hypo recall, calibration)
  - Confunde al SSM/PMM que aprenden de él

Estrategias de detección
------------------------
1. **Drop-spike-recover** (este módulo): patrón temporal claramente
   no fisiológico. Si entre lecturas A, B, C separadas <10min cada una:
   - A → B baja > 40 mg/dL
   - B → C sube > 30 mg/dL
   - |A − C| < 25 mg/dL  (la "base" es similar antes y después)
   - dt total < 20 min
   → B es casi seguro un artefacto, no hipo real.

2. **Re-sync correction** (en blueprints/sync.py): si Libre cambia el
   valor de una lectura existente, lo actualizamos en lugar de ignorar.

3. **Manual user flag** (TODO): endpoint para que el usuario marque
   manualmente lecturas como inválidas.

Las lecturas marcadas con `is_artifact=True` se excluyen automáticamente
de hypo_predictor, daily_brief, bench y PMM updates via WHERE clause.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger("sensor.quality")


# ── Umbrales del detector drop-spike-recover ───────────────────────────
# Calibrados empíricamente: capturan el 90% de artefactos clásicos
# sin marcar hipoglucemias reales (que típicamente tienen perfil distinto
# — bajan más gradualmente o se sostienen).
DROP_MIN_MGDL    = 40    # mg/dL — drop mínimo A→B para considerar artefacto
RECOVERY_MIN     = 30    # mg/dL — recovery mínimo B→C
BASE_DIFF_MAX    = 25    # mg/dL — diferencia máxima entre A y C
WINDOW_TOTAL_MIN = 20    # min — duración máxima del patrón completo
MAX_STEP_MIN     = 10    # min — separación máxima entre lecturas consecutivas

# Floor de seguridad: nunca marcar lecturas > 100 mg/dL como artefacto
# (un drop a 75 desde 130 con recovery a 132 podría ser real, no artefacto)
SAFETY_FLOOR_MGDL = 80   # solo marcamos como artefacto si B < 80


# ── Detector principal ────────────────────────────────────────────────

def flag_drop_spike_artifacts(
    window_hours: int = 4,
    dry_run: bool = False,
) -> dict:
    """
    Escanea lecturas recientes y marca como artifact las que coincidan con
    el patrón drop-spike-recover.

    Args:
        window_hours: ventana de lecturas a analizar (default 4h)
        dry_run: si True, no persiste cambios, solo retorna candidatos

    Returns:
        dict con:
          - candidates: lista de {id, ts, value, prev, next, reason}
          - flagged:    int — cuántas se marcaron (0 si dry_run)
          - n_scanned:  int — cuántas lecturas se evaluaron
    """
    try:
        from models import db, GlucoseReading

        cutoff = datetime.now() - timedelta(hours=window_hours)
        readings = (GlucoseReading.query
                    .filter(GlucoseReading.timestamp >= cutoff)
                    .filter(GlucoseReading.is_artifact == False)  # no re-marcar
                    .order_by(GlucoseReading.timestamp)
                    .all())

        if len(readings) < 3:
            return {"candidates": [], "flagged": 0, "n_scanned": len(readings)}

        candidates = []
        for i in range(1, len(readings) - 1):
            prev = readings[i - 1]
            curr = readings[i]
            nxt  = readings[i + 1]

            # Solo evaluar si la lectura es lo suficientemente baja
            if curr.value_mgdl >= SAFETY_FLOOR_MGDL:
                continue

            drop      = prev.value_mgdl - curr.value_mgdl
            recovery  = nxt.value_mgdl  - curr.value_mgdl
            base_diff = abs(prev.value_mgdl - nxt.value_mgdl)

            dt_drop = (curr.timestamp - prev.timestamp).total_seconds() / 60.0
            dt_rec  = (nxt.timestamp  - curr.timestamp).total_seconds() / 60.0
            dt_tot  = (nxt.timestamp  - prev.timestamp).total_seconds() / 60.0

            # Pattern check (todas las condiciones deben cumplirse)
            is_artifact = (
                drop      >= DROP_MIN_MGDL    and
                recovery  >= RECOVERY_MIN     and
                base_diff <= BASE_DIFF_MAX    and
                dt_drop   <= MAX_STEP_MIN     and
                dt_rec    <= MAX_STEP_MIN     and
                dt_tot    <= WINDOW_TOTAL_MIN
            )

            if is_artifact:
                candidates.append({
                    "id":        curr.id,
                    "ts":        curr.timestamp.isoformat(),
                    "value":     curr.value_mgdl,
                    "prev":      prev.value_mgdl,
                    "next":      nxt.value_mgdl,
                    "drop":      round(drop, 1),
                    "recovery":  round(recovery, 1),
                    "reason":    "drop_spike_recover",
                })

        # Marcar si no es dry_run
        flagged = 0
        if not dry_run and candidates:
            from sqlalchemy import update
            for c in candidates:
                row = GlucoseReading.query.get(c["id"])
                if row and not row.is_artifact:
                    row.is_artifact     = True
                    row.artifact_reason = c["reason"]
                    flagged += 1
            if flagged:
                db.session.commit()
                logger.info(f"sensor_quality: marcadas {flagged} lecturas como artefacto "
                            f"({[c['value'] for c in candidates[:5]]})")

        return {
            "candidates": candidates,
            "flagged":    flagged,
            "n_scanned":  len(readings),
            "dry_run":    dry_run,
        }

    except Exception as exc:
        logger.exception("flag_drop_spike_artifacts falló")
        return {"candidates": [], "flagged": 0, "error": str(exc)}


def mark_as_artifact(
    reading_id: int,
    reason: str = "manual",
) -> dict:
    """
    Marca manualmente una lectura como artefacto (endpoint del usuario).
    """
    try:
        from models import db, GlucoseReading
        row = GlucoseReading.query.get(reading_id)
        if not row:
            return {"ok": False, "error": "reading not found"}
        row.is_artifact     = True
        row.artifact_reason = reason
        db.session.commit()
        return {"ok": True, "id": reading_id}
    except Exception as exc:
        from models import db
        db.session.rollback()
        return {"ok": False, "error": str(exc)}


def unmark_as_artifact(reading_id: int) -> dict:
    """Revierte el flag de artefacto — útil si el usuario lo confirma como real."""
    try:
        from models import db, GlucoseReading
        row = GlucoseReading.query.get(reading_id)
        if not row:
            return {"ok": False, "error": "reading not found"}
        row.is_artifact     = False
        row.artifact_reason = None
        db.session.commit()
        return {"ok": True, "id": reading_id}
    except Exception as exc:
        from models import db
        db.session.rollback()
        return {"ok": False, "error": str(exc)}


def list_recent_artifacts(days: int = 7) -> list[dict]:
    """Lista artefactos detectados recientemente (para UI de revisión)."""
    try:
        from models import GlucoseReading
        cutoff = datetime.now() - timedelta(days=days)
        rows = (GlucoseReading.query
                .filter(GlucoseReading.timestamp >= cutoff)
                .filter(GlucoseReading.is_artifact == True)
                .order_by(GlucoseReading.timestamp.desc())
                .limit(100).all())
        return [{
            "id":         r.id,
            "ts":         r.timestamp.isoformat(),
            "value":      r.value_mgdl,
            "reason":     r.artifact_reason,
            "original":   r.original_value_mgdl,
        } for r in rows]
    except Exception:
        return []
