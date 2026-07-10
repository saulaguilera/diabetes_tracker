"""
drive_mode/apns_push.py
───────────────────────
Push de updates de la ORBIT Drive Live Activity vía APNs (background).

Por qué existe: los updates locales (web → plugin) solo corren con la app en
primer plano. Para que la Live Activity se actualice con el teléfono bloqueado
o en CarPlay, el servidor tiene que empujar el nuevo estado por APNs cada vez
que llega glucosa nueva (hook en el sync de Libre).

FLAG: DRIVE_APNS_ENABLED (default OFF) → con el flag apagado TODO es no-op.

Env vars (Railway) — requieren cuenta de Apple Developer paga:
    DRIVE_APNS_ENABLED  "1" para activar (default "0")
    APNS_TEAM_ID        Team ID de la cuenta (Membership en developer.apple.com)
    APNS_KEY_ID         Key ID de la clave APNs (.p8)
    APNS_KEY_P8         contenido PEM de la clave .p8 (admite '\n' escapados
                        o el PEM entero en base64)
    APNS_TOPIC          default: com.saulaguilera.orbit2026.push-type.liveactivity
    APNS_ENV            "sandbox" (builds de Xcode, default) | "production"

El push token de la Live Activity lo registra la app nativa vía
POST /api/copilot/drive/push-token → settings key `drive_apns_token`.

Contenido: SOLO el ContentState glanceable (glucosa, flecha, estado, mensaje,
frescura). NADA de insulina/dosis/IOB/COB/predicción — mismo contrato que
`to_live_activity_payload`. Las claves espejan OrbitDriveActivityAttributes.
"""
from __future__ import annotations

import base64
import logging
import os
import time

log = logging.getLogger("drive.apns")

_DEFAULT_TOPIC = "com.saulaguilera.orbit2026.push-type.liveactivity"
_STALE_AFTER_S = 15 * 60          # igual que staleAfter del manager nativo
_JWT_TTL_S     = 45 * 60          # Apple pide refrescar el JWT cada 20–60 min

# Cache del JWT (evita firmar en cada push)
_jwt_cache: dict = {"token": None, "issued_at": 0.0}


def _enabled() -> bool:
    return os.environ.get("DRIVE_APNS_ENABLED", "0") == "1"


def _apns_host() -> str:
    env = os.environ.get("APNS_ENV", "sandbox").strip().lower()
    return ("https://api.push.apple.com" if env == "production"
            else "https://api.sandbox.push.apple.com")


def _topic() -> str:
    return os.environ.get("APNS_TOPIC", _DEFAULT_TOPIC)


# ─────────────────────────── clave y JWT ───────────────────────────

def _load_private_key() -> str | None:
    """Lee APNS_KEY_P8: PEM directo, con '\\n' escapados, o base64 del PEM."""
    raw = os.environ.get("APNS_KEY_P8", "").strip()
    if not raw:
        return None
    if "BEGIN PRIVATE KEY" in raw:
        return raw.replace("\\n", "\n")
    try:
        decoded = base64.b64decode(raw).decode("utf-8")
        if "BEGIN PRIVATE KEY" in decoded:
            return decoded
    except Exception:
        pass
    log.warning("APNS_KEY_P8 presente pero no parece un PEM válido")
    return None


def _get_jwt() -> str | None:
    """JWT ES256 para APNs, cacheado ~45 min."""
    now = time.time()
    if _jwt_cache["token"] and (now - _jwt_cache["issued_at"]) < _JWT_TTL_S:
        return _jwt_cache["token"]

    team_id = os.environ.get("APNS_TEAM_ID", "").strip()
    key_id  = os.environ.get("APNS_KEY_ID", "").strip()
    key_pem = _load_private_key()
    if not (team_id and key_id and key_pem):
        log.warning("APNs sin configurar: faltan APNS_TEAM_ID/APNS_KEY_ID/APNS_KEY_P8")
        return None

    import jwt as pyjwt   # lazy: solo se importa con el flag activo
    token = pyjwt.encode(
        {"iss": team_id, "iat": int(now)},
        key_pem,
        algorithm="ES256",
        headers={"kid": key_id},
    )
    _jwt_cache["token"] = token
    _jwt_cache["issued_at"] = now
    return token


# ─────────────────────── payload / content-state ───────────────────────

def build_content_state(payload: dict) -> dict:
    """
    Payload del adapter (`to_live_activity_payload`) → ContentState de ActivityKit.
    Las claves DEBEN espejar OrbitDriveActivityAttributes.ContentState (Codable).
    """
    value = payload.get("value")
    if not isinstance(value, (int, float)):
        value = None      # "--" u otro no-numérico → nil (no engañar)
    return {
        "glucoseValueMgdl":   int(value) if value is not None else None,
        "trendArrow":         payload.get("trend_arrow", "—"),
        "status":             payload.get("status", "disconnected"),
        "statusLevel":        payload.get("level", "unavailable"),
        "safetyMessage":      payload.get("message", "Sensor disconnected"),
        "minutesSinceUpdate": payload.get("minutes_since_update"),
        "sensorName":         payload.get("sensor"),
        "sensorConnected":    bool(payload.get("connected", False)),
        "staleData":          bool(payload.get("stale", True)),
        "updatedText":        payload.get("updated_text", "No data"),
    }


# ─────────────────────────── envío ───────────────────────────

def _get_registered_token() -> str:
    from helpers import _get_setting
    return (_get_setting("drive_apns_token") or "").strip()


def _clear_registered_token() -> None:
    from helpers import _set_setting
    _set_setting("drive_apns_token", "")


def push_drive_update() -> dict:
    """
    Empuja el estado actual de Drive Mode a la Live Activity vía APNs.
    Pensada para llamarse desde el sync de Libre cuando entran lecturas nuevas.
    Nunca levanta excepciones al caller. Devuelve un dict con el resultado.
    """
    if not _enabled():
        return {"ok": False, "reason": "disabled"}

    token = _get_registered_token()
    if not token:
        return {"ok": False, "reason": "no_token"}

    try:
        from drive_mode import build_drive_mode_state, to_live_activity_payload
        state   = build_drive_mode_state()
        payload = to_live_activity_payload(state)
        content = build_content_state(payload)
        return _send(token, content)
    except Exception as exc:
        log.warning("APNs push falló: %s", exc)
        return {"ok": False, "reason": f"error: {exc}"}


def _send(device_token: str, content_state: dict) -> dict:
    jwt_token = _get_jwt()
    if not jwt_token:
        return {"ok": False, "reason": "no_jwt"}

    now = int(time.time())
    body = {
        "aps": {
            "timestamp":     now,
            "event":         "update",
            "content-state": content_state,
            "stale-date":    now + _STALE_AFTER_S,
        }
    }
    headers = {
        "authorization":   f"bearer {jwt_token}",
        "apns-topic":      _topic(),
        "apns-push-type":  "liveactivity",
        "apns-priority":   "10",
        "apns-expiration": "0",
    }

    import httpx   # lazy: HTTP/2 (APNs es HTTP/2-only)
    url = f"{_apns_host()}/3/device/{device_token}"
    with httpx.Client(http2=True, timeout=10) as client:
        r = client.post(url, json=body, headers=headers)

    if r.status_code == 200:
        log.info("APNs push OK (%s %s)", content_state.get("glucoseValueMgdl"),
                 content_state.get("status"))
        return {"ok": True}

    # Token muerto (actividad terminada / build distinta) → limpiar registro
    reason = ""
    try:
        reason = r.json().get("reason", "")
    except Exception:
        pass
    if r.status_code in (400, 410) and reason in ("BadDeviceToken", "Unregistered",
                                                  "ExpiredToken"):
        _clear_registered_token()
        log.info("APNs token inválido (%s) — registro limpiado", reason)
        return {"ok": False, "reason": f"token_cleared: {reason}"}

    log.warning("APNs push HTTP %s: %s", r.status_code, r.text[:200])
    return {"ok": False, "reason": f"http_{r.status_code}: {reason}"}
