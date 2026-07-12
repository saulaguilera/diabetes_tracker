"""
drive_mode/fcm_push.py — notificaciones push a Android vía FCM (HTTP v1).

Espejo de apns_push.push_alert pero para dispositivos Android. La app
registra su token FCM vía POST /api/copilot/push-token {platform:"android"}
→ settings key `app_fcm_token` (per-usuario, igual que el de APNs).

Credenciales: env FCM_SERVICE_ACCOUNT_JSON = service account de Firebase
(el JSON crudo, o en base64 para evitar problemas de saltos de línea —
misma lección que APNS_KEY_P8). Sin la variable todo es no-op.

Sin dependencias nuevas: el JWT RS256 se firma con PyJWT + cryptography
(ya presentes por APNs y crypto_box) y el HTTP con httpx.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import time

log = logging.getLogger(__name__)

_TOKEN_KEY = "app_fcm_token"
_OAUTH_URL = "https://oauth2.googleapis.com/token"
_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"

# access token de OAuth cacheado (~1h de vida; renovamos con margen)
_oauth_cache: dict = {"token": None, "expires_at": 0.0}


def _service_account() -> dict | None:
    """Parsea FCM_SERVICE_ACCOUNT_JSON (JSON crudo o base64). None si falta."""
    raw = (os.environ.get("FCM_SERVICE_ACCOUNT_JSON") or "").strip()
    if not raw:
        return None
    if not raw.startswith("{"):
        try:
            raw = base64.b64decode(raw).decode("utf-8")
        except Exception:
            log.warning("FCM_SERVICE_ACCOUNT_JSON no es JSON ni base64 válido")
            return None
    try:
        data = json.loads(raw)
    except Exception:
        log.warning("FCM_SERVICE_ACCOUNT_JSON no parsea como JSON")
        return None
    if not (data.get("client_email") and data.get("private_key")
            and data.get("project_id")):
        log.warning("FCM service account incompleto (faltan campos)")
        return None
    return data


def _get_access_token(sa: dict) -> str | None:
    """JWT RS256 firmado con la clave del service account → access token."""
    now = time.time()
    if _oauth_cache["token"] and now < _oauth_cache["expires_at"] - 60:
        return _oauth_cache["token"]
    try:
        import jwt
        import httpx
        assertion = jwt.encode(
            {
                "iss": sa["client_email"],
                "scope": _SCOPE,
                "aud": _OAUTH_URL,
                "iat": int(now),
                "exp": int(now) + 3600,
            },
            sa["private_key"],
            algorithm="RS256",
        )
        r = httpx.post(_OAUTH_URL, data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": assertion,
        }, timeout=10)
        if r.status_code != 200:
            log.warning("FCM OAuth HTTP %s: %s", r.status_code, r.text[:200])
            return None
        payload = r.json()
        _oauth_cache["token"] = payload["access_token"]
        _oauth_cache["expires_at"] = now + int(payload.get("expires_in", 3600))
        return _oauth_cache["token"]
    except Exception as exc:
        log.warning("FCM OAuth falló: %s", exc)
        return None


def push_alert_fcm(title: str, body: str) -> dict:
    """Notificación normal al teléfono Android del usuario actual.
    Nunca levanta excepciones al caller. Devuelve dict con el resultado."""
    sa = _service_account()
    if not sa:
        return {"ok": False, "reason": "disabled"}

    from helpers import _get_setting, _set_setting
    token = (_get_setting(_TOKEN_KEY) or "").strip()
    if not token:
        return {"ok": False, "reason": "no_token"}

    access = _get_access_token(sa)
    if not access:
        return {"ok": False, "reason": "no_oauth"}

    message = {
        "message": {
            "token": token,
            "notification": {"title": title, "body": body},
            "android": {
                "priority": "HIGH",
                "notification": {
                    "channel_id": "orbit_alerts",
                    "sound": "default",
                },
            },
        }
    }
    url = f"https://fcm.googleapis.com/v1/projects/{sa['project_id']}/messages:send"
    try:
        import httpx
        r = httpx.post(url, json=message, timeout=10,
                       headers={"authorization": f"Bearer {access}"})
    except Exception as exc:
        log.warning("FCM push falló: %s", exc)
        return {"ok": False, "reason": f"error: {exc}"}

    if r.status_code == 200:
        log.info("FCM push OK (%s)", title)
        return {"ok": True}

    # Token muerto (app desinstalada / token rotado) → limpiar registro
    status = ""
    try:
        err = r.json().get("error", {})
        status = err.get("status", "")
        for d in err.get("details", []):
            if d.get("errorCode"):
                status = d["errorCode"]
    except Exception:
        pass
    if r.status_code in (400, 404) and status in ("UNREGISTERED", "NOT_FOUND",
                                                  "INVALID_ARGUMENT"):
        _set_setting(_TOKEN_KEY, "")
        log.info("FCM token inválido (%s) — registro limpiado", status)
        return {"ok": False, "reason": f"token_cleared: {status}"}

    log.warning("FCM push HTTP %s: %s", r.status_code, r.text[:200])
    return {"ok": False, "reason": f"http_{r.status_code}: {status}"}
