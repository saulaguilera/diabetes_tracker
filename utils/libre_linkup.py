"""
Cliente para la API no oficial de LibreLinkUp (Abbott FreeStyle Libre).

La API es reverse-engineered por la comunidad DIY de diabetes (Nightscout, xDrip+, etc.).
Funciona autenticándose con las mismas credenciales de LibreView/LibreLink.

Con Libre 2: los datos se sincronizan cada vez que el usuario escanea el sensor
con la app LibreLink. Esta función devuelve esos datos desde los servidores de Abbott.

Referencia: https://github.com/timoschlueter/nightscout-librelink-up
"""

import requests
from datetime import datetime, timezone, timedelta

# URL base — Abbott detecta la región automáticamente en el login
_BASE_URL = "https://api.libreview.io"

# Headers para POST (login)
_HEADERS_POST = {
    "product":      "llu.android",
    "version":      "4.16.0",
    "Content-Type": "application/json",
    "Accept":       "application/json",
    "User-Agent":   "LibreLinkUp/4.16.0 (Android)",
}

# Headers para GET (connections, graph) — sin Content-Type ni connection
_HEADERS_GET = {
    "product":    "llu.android",
    "version":    "4.16.0",
    "Accept":     "application/json",
    "User-Agent": "LibreLinkUp/4.16.0 (Android)",
}


class LibreLinkUpError(Exception):
    pass


def login(email: str, password: str) -> tuple[str, str, str]:
    """
    Autentica con LibreLinkUp.
    Retorna (token, base_url, account_id).
    """
    base_url = _BASE_URL
    payload  = {"email": email, "password": password}

    # Primer intento — puede redirigir a servidor regional (ej. api-la, api-eu)
    for _ in range(2):
        resp = requests.post(
            f"{base_url}/llu/auth/login",
            json=payload, headers=_HEADERS_POST, timeout=15
        )
        resp.raise_for_status()
        data = resp.json()

        # Abbott devuelve redirect=true cuando hay que usar un servidor regional
        if data.get("data", {}).get("redirect"):
            region   = data["data"]["region"]
            base_url = f"https://api-{region}.libreview.io"
            continue

        if data.get("status") != 0:
            raise LibreLinkUpError(
                f"Login fallido (status {data.get('status')}). "
                "Verificá tu email y contraseña de LibreView."
            )

        # authTicket puede venir con distintas capitalizaciones según la región
        inner = data.get("data", {})
        ticket = (inner.get("authTicket")
                  or inner.get("authticket")
                  or inner.get("AuthTicket")
                  or {})

        token = ticket.get("token") or ticket.get("Token")
        if not token:
            if inner.get("redirect"):
                raise LibreLinkUpError(
                    "Abbott requiere que aceptes los Términos de Servicio. "
                    "Abrí la app LibreLink en tu celular, iniciá sesión y aceptá los términos."
                )
            raise LibreLinkUpError(
                f"No se pudo obtener el token. Respuesta: {list(inner.keys())}"
            )

        # Extraer account_id del usuario — requerido como header en requests posteriores
        user       = inner.get("user") or {}
        account_id = user.get("id") or user.get("accountId") or ""

        return token, base_url, account_id

    raise LibreLinkUpError("No se pudo resolver el servidor regional de Abbott.")


def _get_headers(token: str, account_id: str) -> dict:
    """Headers GET con Authorization y Account-Id."""
    return {**_HEADERS_GET,
            "Authorization": f"Bearer {token}",
            "Account-Id":    account_id}


def get_connections(token: str, base_url: str, account_id: str = "") -> list:
    """Devuelve la lista de conexiones (pacientes vinculados en LibreLinkUp)."""
    resp = requests.get(f"{base_url}/llu/connections",
                        headers=_get_headers(token, account_id), timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") != 0:
        raise LibreLinkUpError(f"Error obteniendo conexiones: {data}")
    return data.get("data") or []


def get_readings(token: str, base_url: str, patient_id: str, account_id: str = "") -> list[dict]:
    """
    Devuelve lista de lecturas de glucemia del paciente.
    Cada lectura: {"timestamp": datetime, "value_mgdl": float, "trend": str}
    """
    url  = f"{base_url}/llu/connections/{patient_id}/graph"
    resp = requests.get(url, headers=_get_headers(token, account_id), timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") != 0:
        raise LibreLinkUpError(f"Error obteniendo datos: {data}")

    graph_data = data.get("data", {})

    # Combinar lectura actual + historial de la gráfica
    readings = []

    # Lectura actual
    current = graph_data.get("connection", {}).get("glucoseMeasurement")
    if current:
        readings.append(_parse_reading(current))

    # Historial (últimas ~8h desde el último scan)
    for r in graph_data.get("graphData", []):
        readings.append(_parse_reading(r))

    # Eliminar duplicados por timestamp y ordenar
    seen = set()
    unique = []
    for r in readings:
        key = r["timestamp"].replace(microsecond=0)
        if key not in seen:
            seen.add(key)
            unique.append(r)

    return sorted(unique, key=lambda x: x["timestamp"])


def _parse_reading(raw: dict) -> dict:
    """Convierte una lectura raw de la API al formato interno."""
    # La API devuelve el timestamp como string en varios formatos posibles
    ts_raw = raw.get("Timestamp") or raw.get("timestamp") or ""

    # Formato Abbott: "1/15/2026 10:30:00 AM" o ISO
    try:
        ts = datetime.strptime(ts_raw, "%m/%d/%Y %I:%M:%S %p")
    except ValueError:
        try:
            ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
            ts = ts.replace(tzinfo=None)
        except ValueError:
            ts = datetime.now(timezone.utc).replace(tzinfo=None)

    value = float(raw.get("ValueInMgPerDl") or raw.get("value") or 0)

    # Tendencia: 0=no data,1=↑↑,2=↑,3=↗,4=→,5=↘,6=↓,7=↓↓
    trend_map = {1:"↑↑", 2:"↑", 3:"↗", 4:"→", 5:"↘", 6:"↓", 7:"↓↓"}
    trend_raw = raw.get("TrendArrow") or raw.get("trendArrow") or 0
    trend = trend_map.get(int(trend_raw), "→")

    return {"timestamp": ts, "value_mgdl": value, "trend": trend}


def get_cached_token(get_setting_fn, set_setting_fn):
    """
    Devuelve (token, base_url) desde caché si aún es válido.
    Retorna (None, None) si el caché expiró o no existe.
    """
    token    = get_setting_fn("libre_token")
    base_url = get_setting_fn("libre_base_url")
    expiry   = get_setting_fn("libre_token_expiry")

    if not token or not base_url or not expiry:
        return None, None

    try:
        expiry_dt = datetime.fromisoformat(expiry)
        # Renovar 1 día antes de que expire
        if datetime.now() < expiry_dt - timedelta(days=1):
            return token, base_url
    except Exception:
        pass

    return None, None


def save_token_cache(token, base_url, expires_ms, set_setting_fn):
    """Guarda el token en caché con su fecha de expiración."""
    try:
        # expires es timestamp en milisegundos desde epoch
        expiry_dt = datetime.fromtimestamp(expires_ms / 1000)
        set_setting_fn("libre_token",        token)
        set_setting_fn("libre_base_url",     base_url)
        set_setting_fn("libre_token_expiry", expiry_dt.isoformat())
    except Exception:
        pass


def sync_all(email: str, password: str, get_setting_fn=None, set_setting_fn=None) -> dict:
    """
    Función principal: autentica y descarga todas las lecturas disponibles.
    Retorna {"readings": [...], "patient_id": str, "error": None|str}
    """
    try:
        # Intentar usar token cacheado primero (evita rate limiting de Abbott)
        token, base_url = None, None
        if get_setting_fn and set_setting_fn:
            token, base_url = get_cached_token(get_setting_fn, set_setting_fn)

        account_id = ""
        if get_setting_fn:
            account_id = get_setting_fn("libre_account_id") or ""

        # Hacer login si: no hay token, o no hay account_id (caché incompleto)
        if not token or not account_id:
            token, base_url, account_id = login(email, password)
            if set_setting_fn:
                expires_ms = int((datetime.now() + timedelta(days=170)).timestamp() * 1000)
                save_token_cache(token, base_url, expires_ms, set_setting_fn)
                set_setting_fn("libre_account_id", account_id)

        connections = get_connections(token, base_url, account_id)

        if not connections:
            # Si el token cacheado devolvió lista vacía, forzar re-login
            if get_setting_fn and set_setting_fn:
                set_setting_fn("libre_token", "")
            return {"readings": [], "error": "No hay sensores vinculados en LibreLinkUp."}

        # Usar el primer paciente (en uso personal, sos vos mismo)
        patient = connections[0]
        patient_id = patient.get("patientId") or patient.get("id")

        readings = get_readings(token, base_url, patient_id, account_id)
        return {"readings": readings, "patient_id": patient_id, "error": None}

    except LibreLinkUpError as e:
        # Si falló con token cacheado, limpiar caché para forzar re-login la próxima vez
        if set_setting_fn:
            set_setting_fn("libre_token", "")
        return {"readings": [], "error": str(e)}
    except requests.RequestException as e:
        err = str(e)
        # 401/403 con token cacheado → limpiar y reintentar sin caché
        if ("401" in err or "403" in err or "430" in err) and get_setting_fn:
            cached = get_setting_fn("libre_token")
            if cached and set_setting_fn:
                set_setting_fn("libre_token", "")
                return {"readings": [], "error": "Token expirado, limpiado. Intentá de nuevo en 1 minuto."}
        return {"readings": [], "error": f"Error de red: {e}"}
    except Exception as e:
        return {"readings": [], "error": f"Error inesperado: {e}"}
