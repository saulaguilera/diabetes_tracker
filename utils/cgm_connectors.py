"""
utils/cgm_connectors.py — capa de conectores CGM (multi-marca).

Un solo contrato para todas las fuentes; el sync no sabe de marcas:
    fetch(provider, cred1, cred2, get_setting, set_setting) → {
        "readings":   [{"timestamp": datetime local, "value_mgdl": float, "trend": str}],
        "treatments": [{"timestamp": datetime, "units": float, "kind": "bolus"}],  # opcional (bombas)
        "error":      None | str,
    }

Proveedores:
    libre      — LibreLinkUp (cred1=email, cred2=password) → utils/libre_linkup
    dexcom     — Dexcom Share, la vía "seguidores" (cred1=usuario Dexcom, cred2=password).
                 Tiempo real; requiere Share activado con ≥1 seguidor.
    nightscout — sitio Nightscout del usuario (cred1=URL, cred2=token opcional).
                 Trae glucosa Y tratamientos (bolos de bombas: Loop/AndroidAPS/Omnipod DIY).

HealthKit (Dexcom comercial, Omnipod 5, Tandem) es la fase NATIVA: el teléfono
lee Salud y postea al backend — no vive aquí.
"""
from __future__ import annotations

import logging
from datetime import datetime

log = logging.getLogger("cgm.connectors")

PROVIDERS = ("libre", "dexcom", "nightscout")

# Dexcom Share (protocolo de la app de seguidores; el mismo que usan
# Sugarmate/Nightscout como fuente). App ID público del cliente oficial.
_DEXCOM_APP_ID = "d89443d2-327c-4a6f-89e5-496bbb0317db"
_DEXCOM_HOSTS = ("https://shareous1.dexcom.com",   # fuera de EE.UU. primero (LATAM)
                 "https://share2.dexcom.com")
_DEXCOM_TREND = {
    "DoubleUp": "↑↑", "SingleUp": "↑", "FortyFiveUp": "↗",
    "Flat": "→", "FortyFiveDown": "↘", "SingleDown": "↓", "DoubleDown": "↓↓",
    # variantes numéricas del API viejo
    "1": "↑↑", "2": "↑", "3": "↗", "4": "→", "5": "↘", "6": "↓", "7": "↓↓",
}
_NS_TREND = {
    "DoubleUp": "↑↑", "SingleUp": "↑", "FortyFiveUp": "↗",
    "Flat": "→", "FortyFiveDown": "↘", "SingleDown": "↓", "DoubleDown": "↓↓",
}


def fetch(provider, cred1, cred2, get_setting_fn=None, set_setting_fn=None) -> dict:
    """Punto de entrada del sync. Nunca levanta al caller."""
    try:
        if provider == "dexcom":
            return _fetch_dexcom(cred1, cred2, get_setting_fn, set_setting_fn)
        if provider == "nightscout":
            return _fetch_nightscout(cred1, cred2)
        # default: libre (compatibilidad con todo lo existente)
        from utils.libre_linkup import sync_all
        return sync_all(cred1, cred2, get_setting_fn=get_setting_fn,
                        set_setting_fn=set_setting_fn)
    except Exception as exc:
        log.warning("conector %s falló: %s", provider, exc)
        return {"readings": [], "error": f"{provider}: {str(exc)[:150]}"}


def validate(provider, cred1, cred2) -> str | None:
    """Prueba las credenciales al conectar. Devuelve None si OK, o el error."""
    try:
        if provider == "dexcom":
            _dexcom_session(cred1, cred2)
            return None
        if provider == "nightscout":
            r = _ns_get(cred1, cred2, "entries/sgv.json", {"count": 1})
            if not isinstance(r, list):
                return "El sitio respondió algo inesperado (¿URL correcta?)"
            return None
        from utils.libre_linkup import login as libre_login, get_connections
        token, base_url, account_id = libre_login(cred1, cred2)
        # login válido NO basta: sin invitación de seguidor aceptada no hay
        # paciente del cual leer — la trampa clásica que deja al usuario
        # "conectado" pero sin lecturas para siempre (caso marcolamp).
        if not get_connections(token, base_url, account_id):
            return ("Tu cuenta LibreLinkUp no tiene sensores vinculados. "
                    "Primero invita a este correo desde LibreLink (Compartir → "
                    "LibreLinkUp) y acepta la invitación en la app LibreLinkUp — "
                    "los pasos están en el Centro de ayuda.")
        return None
    except Exception as exc:
        return str(exc)[:150]


# ── Dexcom Share ──────────────────────────────────────────────────────────────

def _dexcom_session(username, password):
    """Login de dos pasos; prueba OUS y US. Devuelve (base_url, session_id)."""
    import httpx
    last = None
    for base in _DEXCOM_HOSTS:
        try:
            with httpx.Client(timeout=15) as c:
                r = c.post(f"{base}/ShareWebServices/Services/General/AuthenticatePublisherAccount",
                           json={"accountName": username, "password": password,
                                 "applicationId": _DEXCOM_APP_ID})
                if r.status_code != 200:
                    last = f"auth {r.status_code}: {r.text[:80]}"
                    continue
                account_id = r.json()
                r = c.post(f"{base}/ShareWebServices/Services/General/LoginPublisherAccountById",
                           json={"accountId": account_id, "password": password,
                                 "applicationId": _DEXCOM_APP_ID})
                if r.status_code != 200:
                    last = f"login {r.status_code}: {r.text[:80]}"
                    continue
                return base, r.json()
        except Exception as exc:
            last = str(exc)[:80]
    raise RuntimeError(f"Dexcom rechazó las credenciales ({last})")


def _fetch_dexcom(username, password, get_setting_fn=None, set_setting_fn=None) -> dict:
    import httpx
    # sesión cacheada por usuario (settings del contexto) para no re-loguear
    base = session_id = None
    if get_setting_fn:
        base = get_setting_fn("dexcom_base") or None
        session_id = get_setting_fn("dexcom_session") or None

    def _leer(b, s):
        with httpx.Client(timeout=15) as c:
            return c.post(f"{b}/ShareWebServices/Services/Publisher/ReadPublisherLatestGlucoseValues",
                          params={"sessionId": s, "minutes": 1440, "maxCount": 288})

    r = _leer(base, session_id) if (base and session_id) else None
    if r is None or r.status_code != 200:
        base, session_id = _dexcom_session(username, password)
        if set_setting_fn:
            set_setting_fn("dexcom_base", base)
            set_setting_fn("dexcom_session", session_id)
        r = _leer(base, session_id)
        if r.status_code != 200:
            return {"readings": [], "error": f"Dexcom lectura {r.status_code}"}

    readings = []
    for item in r.json():
        # WT = "/Date(1719000000000)/" (epoch ms, UTC) → hora local naive
        try:
            ms = int("".join(ch for ch in str(item.get("WT", "")) if ch.isdigit()))
            ts = datetime.fromtimestamp(ms / 1000)
        except Exception:
            continue
        val = float(item.get("Value") or 0)
        if val <= 0:
            continue
        trend = _DEXCOM_TREND.get(str(item.get("Trend", "")), "→")
        readings.append({"timestamp": ts, "value_mgdl": val, "trend": trend})
    readings.sort(key=lambda x: x["timestamp"])
    return {"readings": readings, "error": None}


# ── Nightscout ────────────────────────────────────────────────────────────────

def _ns_get(url, token, path, params):
    import httpx
    base = (url or "").strip().rstrip("/")
    if not base.startswith("http"):
        base = "https://" + base
    p = dict(params)
    if token:
        p["token"] = token
    with httpx.Client(timeout=15, follow_redirects=True) as c:
        r = c.get(f"{base}/api/v1/{path}", params=p)
        r.raise_for_status()
        return r.json()


def _fetch_nightscout(url, token) -> dict:
    entries = _ns_get(url, token, "entries/sgv.json", {"count": 288})
    readings = []
    for e in entries:
        val = float(e.get("sgv") or 0)
        ms = e.get("date")
        if val <= 0 or not ms:
            continue
        readings.append({
            "timestamp": datetime.fromtimestamp(ms / 1000),
            "value_mgdl": val,
            "trend": _NS_TREND.get(e.get("direction", ""), "→"),
        })
    readings.sort(key=lambda x: x["timestamp"])

    # tratamientos (bolos de bomba / registros de loop) — lo que hace valioso
    # Nightscout: la insulina entra sola
    treatments = []
    try:
        for t in _ns_get(url, token, "treatments.json", {"count": 100}):
            units = t.get("insulin")
            created = t.get("created_at") or t.get("timestamp")
            if not units or not created:
                continue
            try:
                ts = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
                ts = ts.astimezone().replace(tzinfo=None)   # → hora local naive
            except Exception:
                continue
            treatments.append({"timestamp": ts, "units": float(units), "kind": "bolus"})
    except Exception as exc:
        log.info("nightscout treatments no disponibles: %s", exc)

    return {"readings": readings, "treatments": treatments, "error": None}
