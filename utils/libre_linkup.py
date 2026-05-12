"""
Cliente para la API no oficial de LibreLinkUp (Abbott FreeStyle Libre).

La API es reverse-engineered por la comunidad DIY de diabetes (Nightscout, xDrip+, etc.).
Funciona autenticándose con las mismas credenciales de LibreView/LibreLink.

Con Libre 2: los datos se sincronizan cada vez que el usuario escanea el sensor
con la app LibreLink. Esta función devuelve esos datos desde los servidores de Abbott.

Referencia: https://github.com/timoschlueter/nightscout-librelink-up
"""

import requests
from datetime import datetime, timezone

# URL base — Abbott detecta la región automáticamente en el login
_BASE_URL = "https://api.libreview.io"

_HEADERS = {
    "product":         "llu.android",
    "version":         "4.7",
    "Accept-Encoding": "gzip",
    "Content-Type":    "application/json",
    "User-Agent":      "Mozilla/5.0 (compatible; DiabetesTracker)",
}


class LibreLinkUpError(Exception):
    pass


def login(email: str, password: str) -> tuple[str, str]:
    """
    Autentica con LibreLinkUp.
    Retorna (token, base_url) — la URL puede cambiar por redirección regional.
    """
    base_url = _BASE_URL
    payload  = {"email": email, "password": password}

    # Primer intento — puede redirigir a servidor regional (ej. api-la, api-eu)
    for _ in range(2):
        resp = requests.post(
            f"{base_url}/llu/auth/login",
            json=payload, headers=_HEADERS, timeout=15
        )
        resp.raise_for_status()
        data = resp.json()

        # Abbott devuelve status=2 cuando hay que usar un servidor regional
        if data.get("status") == 2 and data.get("data", {}).get("redirect"):
            region   = data["data"]["region"]
            base_url = f"https://api-{region}.libreview.io"
            continue

        if data.get("status") != 0:
            raise LibreLinkUpError(
                f"Login fallido (status {data.get('status')}). "
                "Verificá tu email y contraseña de LibreView."
            )

        token = data["data"]["authTicket"]["token"]
        return token, base_url

    raise LibreLinkUpError("No se pudo resolver el servidor regional de Abbott.")


def get_connections(token: str, base_url: str) -> list:
    """Devuelve la lista de conexiones (pacientes vinculados en LibreLinkUp)."""
    headers = {**_HEADERS, "Authorization": f"Bearer {token}"}
    resp    = requests.get(f"{base_url}/llu/connections", headers=headers, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") != 0:
        raise LibreLinkUpError(f"Error obteniendo conexiones: {data}")
    return data.get("data") or []


def get_readings(token: str, base_url: str, patient_id: str) -> list[dict]:
    """
    Devuelve lista de lecturas de glucemia del paciente.
    Cada lectura: {"timestamp": datetime, "value_mgdl": float, "trend": str}
    """
    headers = {**_HEADERS, "Authorization": f"Bearer {token}"}
    url     = f"{base_url}/llu/connections/{patient_id}/graph"
    resp    = requests.get(url, headers=headers, timeout=15)
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


def sync_all(email: str, password: str) -> dict:
    """
    Función principal: autentica y descarga todas las lecturas disponibles.
    Retorna {"readings": [...], "patient_id": str, "error": None|str}
    """
    try:
        token, base_url = login(email, password)
        connections     = get_connections(token, base_url)

        if not connections:
            return {"readings": [], "error": "No hay sensores vinculados en LibreLinkUp."}

        # Usar el primer paciente (en uso personal, sos vos mismo)
        patient = connections[0]
        patient_id = patient.get("patientId") or patient.get("id")

        readings = get_readings(token, base_url, patient_id)
        return {"readings": readings, "patient_id": patient_id, "error": None}

    except LibreLinkUpError as e:
        return {"readings": [], "error": str(e)}
    except requests.RequestException as e:
        return {"readings": [], "error": f"Error de red: {e}"}
    except Exception as e:
        return {"readings": [], "error": f"Error inesperado: {e}"}
