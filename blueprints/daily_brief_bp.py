"""
blueprints/daily_brief_bp.py
─────────────────────────────
Endpoints del Daily Metabolic Brief.

GET  /api/daily-brief                → último brief del día actual (cache)
POST /api/daily-brief/refresh        → fuerza regeneración (rate-limited)
GET  /api/daily-brief/history?n=7    → últimos N briefs

Caching strategy:
  - El scheduler genera el brief al amanecer (06:30 hora local)
  - GET sirve el brief más reciente del día actual desde DB
  - Si no hay brief del día → genera on-demand (lazy)
  - POST /refresh fuerza generación nueva — útil si el usuario quiere ver
    el brief actualizado con más data acumulada
"""
from datetime import date, datetime, timedelta
import json
import logging
from flask import Blueprint, jsonify, request, session

logger = logging.getLogger("daily_brief.bp")

bp = Blueprint("daily_brief", __name__)


def _require_login():
    if not session.get("logged_in"):
        return jsonify({"ok": False, "error": "No autorizado"}), 401
    return None


# ─── Helpers ───────────────────────────────────────────────────────────

def _generate_and_persist(force: bool = False, tone: str = "supportive") -> dict:
    """Genera el brief del día actual, lo persiste y devuelve dict serializable."""
    from models import db, DailyBrief
    from services.daily_brief     import compute_daily_summary, summary_to_dict
    from services.daily_brief_llm import generate_brief

    today = date.today()
    # Si ya hay brief de hoy y NO se fuerza, devolverlo
    if not force:
        existing = (DailyBrief.query
                    .filter_by(day=today)
                    .order_by(DailyBrief.generated_at.desc())
                    .first())
        if existing:
            return _row_to_dict(existing)

    # Generar nuevo
    summary = compute_daily_summary()
    summary_dict = summary_to_dict(summary)

    # Nombre opcional (de settings, si existe)
    name = None
    try:
        from helpers import _get_setting
        name = _get_setting("user_name") or None
    except Exception:
        pass

    result = generate_brief(summary, tone=tone, name=name)

    # Persistir
    try:
        row = DailyBrief(
            day            = today,
            generated_at   = datetime.utcnow(),
            summary_json   = json.dumps(summary_dict, default=str),
            narrative      = result.narrative,
            confidence     = float(summary.confidence),
            tone           = tone,
            llm_used       = bool(result.llm_used),
            llm_model      = result.llm_model,
            llm_tokens_in  = result.tokens_in,
            llm_tokens_out = result.tokens_out,
            llm_latency_ms = result.latency_ms,
            llm_prompt     = result.prompt,
            error          = result.error,
        )
        db.session.add(row)
        db.session.commit()
        return _row_to_dict(row)
    except Exception as exc:
        db.session.rollback()
        logger.exception("falló persistir DailyBrief")
        # Aún así devolver el brief al usuario
        return {
            "day":          today.isoformat(),
            "generated_at": datetime.utcnow().isoformat(),
            "narrative":    result.narrative,
            "confidence":   summary.confidence,
            "summary":      summary_dict,
            "llm_used":     result.llm_used,
            "from_cache":   False,
            "persist_error": str(exc),
        }


def _row_to_dict(row) -> dict:
    try:
        summary = json.loads(row.summary_json) if row.summary_json else {}
    except Exception:
        summary = {}
    return {
        "day":          row.day.isoformat() if row.day else None,
        "generated_at": row.generated_at.isoformat() if row.generated_at else None,
        "narrative":    row.narrative,
        "confidence":   row.confidence,
        "tone":         row.tone,
        "summary":      summary,
        "llm_used":     row.llm_used,
        "llm_model":    row.llm_model,
        "llm_latency_ms": row.llm_latency_ms,
        "from_cache":   True,
    }


# ─── Endpoints ─────────────────────────────────────────────────────────

@bp.route("/api/daily-brief", endpoint="api_daily_brief_get")
def api_daily_brief_get():
    """
    Devuelve el brief del día. Si no existe, lo genera (lazy).
    Query params:
      tone: 'supportive' | 'neutral' | 'clinical_light' (default supportive)
      force: '1' fuerza regeneración (ignora cache)
    """
    err = _require_login()
    if err: return err
    try:
        tone  = request.args.get("tone", "supportive")
        force = request.args.get("force", "0") in ("1", "true", "yes")
        data  = _generate_and_persist(force=force, tone=tone)
        return jsonify({"ok": True, **data})
    except Exception as exc:
        logger.exception("daily-brief GET falló")
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.route("/api/daily-brief/refresh", methods=["POST"],
           endpoint="api_daily_brief_refresh")
def api_daily_brief_refresh():
    """Fuerza regeneración del brief del día (rate-limited a 1 vez cada 10min)."""
    err = _require_login()
    if err: return err
    try:
        from models import DailyBrief
        # Rate limit simple: si el último brief es de hace < 10min, no regenerar
        last = (DailyBrief.query
                .filter_by(day=date.today())
                .order_by(DailyBrief.generated_at.desc())
                .first())
        if last:
            age_min = (datetime.utcnow() - last.generated_at).total_seconds() / 60
            if age_min < 10:
                return jsonify({
                    "ok": False,
                    "error": f"último brief generado hace {age_min:.0f}min — esperá "
                             "10min antes de refrescar de nuevo",
                    "next_available_in_min": round(10 - age_min, 1),
                }), 429

        tone = (request.json or {}).get("tone", "supportive") if request.is_json else "supportive"
        data = _generate_and_persist(force=True, tone=tone)
        return jsonify({"ok": True, **data})
    except Exception as exc:
        logger.exception("daily-brief refresh falló")
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.route("/api/daily-brief/history", endpoint="api_daily_brief_history")
def api_daily_brief_history():
    """Últimos N briefs (default 7)."""
    err = _require_login()
    if err: return err
    try:
        from models import DailyBrief
        n = min(int(request.args.get("n", 7)), 30)
        # Para historial mostramos un brief por día (el más reciente)
        rows = (DailyBrief.query
                .order_by(DailyBrief.day.desc(), DailyBrief.generated_at.desc())
                .limit(n * 3)
                .all())
        # Dedup por día
        seen = set()
        history = []
        for r in rows:
            if r.day in seen: continue
            seen.add(r.day)
            history.append({
                "day":          r.day.isoformat(),
                "generated_at": r.generated_at.isoformat() if r.generated_at else None,
                "narrative":    r.narrative,
                "confidence":   r.confidence,
                "llm_used":     r.llm_used,
            })
            if len(history) >= n: break
        return jsonify({"ok": True, "history": history})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
