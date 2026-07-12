"""
drive_mode/notify.py — despachador de notificaciones push.

Un solo push_alert(title, body) que llega a donde esté el usuario:
APNs (iPhone) y/o FCM (Android), según qué tokens tenga registrados.
Cada backend es no-op si no está configurado o no hay token, así que
llamar a ambos siempre es seguro. ok=True si al menos uno llegó.
"""

from __future__ import annotations


def push_alert(title: str, body: str) -> dict:
    """Nunca levanta excepciones al caller (igual que los backends)."""
    try:
        from drive_mode.apns_push import push_alert as _apns
        apns = _apns(title, body)
    except Exception as exc:            # cinturón: jamás romper al caller
        apns = {"ok": False, "reason": f"error: {exc}"}
    try:
        from drive_mode.fcm_push import push_alert_fcm as _fcm
        fcm = _fcm(title, body)
    except Exception as exc:
        fcm = {"ok": False, "reason": f"error: {exc}"}
    return {"ok": bool(apns.get("ok") or fcm.get("ok")), "apns": apns, "fcm": fcm}
