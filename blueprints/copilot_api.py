"""
blueprints/copilot_api.py
─────────────────────────
API JSON del producto Orbit Copilot (frontend React).

Solo **estado presente** y datos retrospectivos — SIN predicciones (la
separación regulatoria del producto). Solo lectura: no toca el SSM ni escribe
nada. Reusa los cálculos que ya existen (get_kinetics_snapshot).

    GET /api/copilot/home → glucosa actual, IOB, COB, tendencia, TIR 24h,
                            actividad reciente.
"""
from datetime import datetime, timedelta
from flask import Blueprint, jsonify, session, request

bp = Blueprint("copilot_api", __name__)

LOW, HIGH = 70, 180


def _require_login():
    if not session.get("logged_in"):
        return jsonify({"ok": False, "error": "No autorizado"}), 401
    return None


def _hace(ts):
    """'ahora' / '12m' / '3h' / '2d' — hora local (datetime.now)."""
    if not ts:
        return ""
    mins = int((datetime.now() - ts).total_seconds() / 60)
    if mins < 1:
        return "ahora"
    if mins < 60:
        return f"{mins}m"
    horas = mins // 60
    if horas < 24:
        return f"{horas}h"
    return f"{horas // 24}d"


@bp.route("/api/copilot/home", endpoint="copilot_home")
def copilot_home():
    """Datos de la pantalla Hoy. Estado presente, sin predicciones."""
    err = _require_login()
    if err:
        return err

    from models import GlucoseReading, Meal, InsulinDose, Activity
    from utils.kinetics import get_kinetics_snapshot

    # Resiliencia: si el snapshot de cinética falla, la pantalla igual carga
    # (glucosa, TIR y actividad no dependen de él). No predice nada.
    try:
        snap = get_kinetics_snapshot(hours_lookback=6) or {}
    except Exception:
        snap = {}
    iob = round(snap.get("iob_bolus") or 0.0, 1)
    cob = int(round(snap.get("cob") or 0))
    roc = snap.get("roc") or 0.0
    arrow = snap.get("arrow") or "→"

    # ── glucosa actual (última lectura) ───────────────────────────────────
    last = GlucoseReading.query.order_by(GlucoseReading.timestamp.desc()).first()
    glucose = None
    if last:
        v = int(round(last.value_mgdl))
        status = "hipo" if v < LOW else "hiper" if v > HIGH else "rango"
        glucose = {
            "value": v,
            "status": status,
            "arrow": arrow,
            "source": last.source,
            "at": last.timestamp.isoformat(),
            "age_min": int((datetime.now() - last.timestamp).total_seconds() / 60),
        }

    trend = "Subiendo" if roc > 1 else "Bajando" if roc < -1 else "Estable"

    # ── tiempo en rango — últimas 24h ─────────────────────────────────────
    since = datetime.now() - timedelta(hours=24)
    reads = (GlucoseReading.query
             .filter(GlucoseReading.timestamp >= since)
             .all())
    tir = None
    series = []
    if reads:
        in_range = sum(1 for r in reads if LOW <= r.value_mgdl <= HIGH)
        tir = round(100 * in_range / len(reads))
        # serie ordenada para la onda de 24h (solo lecturas, sin predicción)
        series = [{"t": r.timestamp.isoformat(), "v": round(r.value_mgdl, 1)}
                  for r in sorted(reads, key=lambda r: r.timestamp)]

    # ── actividad reciente (comida / insulina / ejercicio) ────────────────
    events = []
    for m in Meal.query.order_by(Meal.timestamp.desc()).limit(4).all():
        events.append({"cat": "comida", "title": m.name or "Comida",
                       "badge": f"{int(m.carbs_g)}g" if m.carbs_g else "",
                       "ts": m.timestamp})
    for d in InsulinDose.query.order_by(InsulinDose.timestamp.desc()).limit(4).all():
        label = {"bolus": "Rápida", "basal": "Basal"}.get(d.type, (d.type or "").capitalize())
        events.append({"cat": "insulina", "title": f"Insulina {label}".strip(),
                       "badge": f"{d.units:g}U", "ts": d.timestamp})
    for a in Activity.query.order_by(Activity.timestamp.desc()).limit(4).all():
        events.append({"cat": "ejercicio", "title": a.activity_type or "Ejercicio",
                       "badge": f"{a.duration_min}m" if a.duration_min else "",
                       "ts": a.timestamp})
    events.sort(key=lambda e: e["ts"], reverse=True)
    recent = [{"cat": e["cat"], "title": e["title"], "badge": e["badge"],
               "ago": _hace(e["ts"])} for e in events[:4]]

    return jsonify({
        "ok": True,
        "glucose": glucose,
        "context": {"iob": iob, "cob": cob, "trend": trend},
        "tir_today": tir,
        "series": series,
        "recent": recent,
        "updated_at": datetime.now().isoformat(),
    })


@bp.route("/api/copilot/log", methods=["POST"], endpoint="copilot_log")
def copilot_log():
    """Registrar comida / insulina / ejercicio. Escribe a las mismas tablas que
    los formularios actuales (una sola fuente de verdad). Hora local. Estos datos
    alimentan también el research (el SSM los lee como contexto)."""
    err = _require_login()
    if err:
        return err

    from models import db, Meal, InsulinDose, Activity

    data = request.get_json(silent=True) or {}
    cat = data.get("cat")
    now = datetime.now()

    def _f(key, default=0.0):
        try:
            return float(data.get(key) or default)
        except (TypeError, ValueError):
            return default

    try:
        if cat == "comida":
            row = Meal(
                timestamp=now,
                name=(data.get("name") or "Comida").strip()[:200],
                carbs_g=_f("carbs"), fat_g=_f("fat"), protein_g=_f("protein"),
                calories=_f("calories"), notes=(data.get("notes") or None),
            )
        elif cat == "insulina":
            units = _f("units")
            if units <= 0:
                return jsonify({"ok": False, "error": "Unidades inválidas"}), 400
            tipo = data.get("type") if data.get("type") in ("bolus", "basal") else "bolus"
            row = InsulinDose(
                timestamp=now, type=tipo, units=units,
                purpose=(data.get("purpose") or None), notes=(data.get("notes") or None),
            )
        elif cat == "ejercicio":
            row = Activity(
                timestamp=now,
                activity_type=(data.get("activity_type") or "Ejercicio").strip()[:100],
                duration_min=int(_f("duration_min")),
                intensity=(data.get("intensity") or None),
                notes=(data.get("notes") or None),
            )
        else:
            return jsonify({"ok": False, "error": "Categoría no soportada"}), 400

        db.session.add(row)
        db.session.commit()
        return jsonify({"ok": True, "id": row.id})
    except Exception as exc:
        db.session.rollback()
        return jsonify({"ok": False, "error": str(exc)}), 400
