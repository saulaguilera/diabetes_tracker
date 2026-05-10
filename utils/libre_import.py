"""
Importador de archivos CSV exportados desde FreeStyle LibreLink / FreeStyle Libre.

El CSV del Libre tiene estas columnas relevantes:
  - Device Timestamp (columna 2)
  - Record Type: 0=CGM histórico, 1=escáner CGM, 2=glucómetro manual, 6=cetona
  - Historic Glucose mg/dL (columna 4) — para tipo 0
  - Scan Glucose mg/dL (columna 5)   — para tipo 1
  - Strip Glucose mg/dL (columna 13) — para tipo 2

Las primeras filas contienen metadatos del dispositivo; el CSV real
empieza con una fila de encabezados que incluye "Device Timestamp".
"""

import csv
import io
from datetime import datetime


# Formatos de fecha que exporta el Libre en distintas regiones
_DATE_FORMATS = [
    "%m/%d/%Y %I:%M %p",   # 01/25/2024 08:30 AM  (EE.UU.)
    "%d/%m/%Y %H:%M",      # 25/01/2024 08:30     (América Latina)
    "%Y-%m-%d %H:%M",      # 2024-01-25 08:30
    "%m-%d-%Y %I:%M %p",
    "%d-%m-%Y %H:%M",
]


def _parse_timestamp(raw: str) -> datetime | None:
    raw = raw.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def _parse_float(raw: str) -> float | None:
    raw = raw.strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def import_libre_csv(file_obj, db, GlucoseReading, CGMImport) -> dict:
    """
    Lee el archivo CSV del Freestyle Libre y persiste los registros de glucosa.

    Retorna un dict con: total, insertados, duplicados.
    """
    content = file_obj.read()
    # El Libre exporta en UTF-8 con BOM o en latin-1
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            text = content.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError("No se pudo decodificar el archivo CSV.")

    lines = text.splitlines()

    # Encontrar la fila de encabezados (contiene "Device Timestamp")
    header_idx = None
    for i, line in enumerate(lines):
        if "Device Timestamp" in line or "Marca temporal del dispositivo" in line:
            header_idx = i
            break

    if header_idx is None:
        raise ValueError(
            "No se encontró encabezado válido del Freestyle Libre en el archivo."
        )

    reader = csv.DictReader(lines[header_idx:])

    # Normalizar nombres de columnas (el Libre puede exportar en inglés o español)
    COL_TIMESTAMP = None
    COL_RECORD_TYPE = None
    COL_HISTORIC = None
    COL_SCAN = None
    COL_STRIP = None

    # Se detectan en la primera pasada de headers
    KNOWN_TIMESTAMP = ["Device Timestamp", "Marca temporal del dispositivo"]
    KNOWN_RECORD_TYPE = ["Record Type", "Tipo de registro"]
    KNOWN_HISTORIC = ["Historic Glucose mg/dL", "Glucosa histórica mg/dL",
                      "Historic Glucose mmol/L", "Glucosa histórica mmol/L"]
    KNOWN_SCAN = ["Scan Glucose mg/dL", "Escanear glucosa mg/dL",
                  "Scan Glucose mmol/L", "Escanear glucosa mmol/L"]
    KNOWN_STRIP = ["Strip Glucose mg/dL", "Glucosa en tira mg/dL",
                   "Strip Glucose mmol/L", "Glucosa en tira mmol/L"]

    rows = list(reader)
    if not rows:
        raise ValueError("El archivo no contiene datos.")

    fieldnames = rows[0].keys() if rows else []

    def find_col(candidates):
        for c in candidates:
            if c in fieldnames:
                return c
        return None

    COL_TIMESTAMP = find_col(KNOWN_TIMESTAMP)
    COL_RECORD_TYPE = find_col(KNOWN_RECORD_TYPE)
    COL_HISTORIC = find_col(KNOWN_HISTORIC)
    COL_SCAN = find_col(KNOWN_SCAN)
    COL_STRIP = find_col(KNOWN_STRIP)

    if not COL_TIMESTAMP:
        raise ValueError("No se encontró la columna de timestamp en el CSV.")

    # Detectar si los valores están en mmol/L para convertir a mg/dL
    use_mmol = COL_HISTORIC and "mmol" in COL_HISTORIC.lower()

    total = 0
    insertados = 0
    duplicados = 0
    timestamps_vistos = set()
    fecha_min = None
    fecha_max = None

    for row in rows:
        raw_ts = row.get(COL_TIMESTAMP, "").strip()
        if not raw_ts:
            continue

        ts = _parse_timestamp(raw_ts)
        if ts is None:
            continue

        record_type_raw = row.get(COL_RECORD_TYPE, "").strip()
        try:
            record_type = int(record_type_raw)
        except (ValueError, TypeError):
            continue

        # Determinar valor según tipo
        value = None
        source = "cgm_historic"

        if record_type == 0 and COL_HISTORIC:
            value = _parse_float(row.get(COL_HISTORIC, ""))
            source = "cgm_historic"
        elif record_type == 1 and COL_SCAN:
            value = _parse_float(row.get(COL_SCAN, ""))
            source = "cgm_scan"
        elif record_type == 2 and COL_STRIP:
            value = _parse_float(row.get(COL_STRIP, ""))
            source = "cgm_strip"

        if value is None or value <= 0:
            continue

        # Convertir mmol/L → mg/dL
        if use_mmol:
            value = round(value * 18.0182, 1)

        total += 1

        # Evitar duplicados exactos (mismo timestamp + valor)
        key = (ts, round(value, 1), source)
        if key in timestamps_vistos:
            duplicados += 1
            continue
        timestamps_vistos.add(key)

        # Verificar en BD
        existe = GlucoseReading.query.filter_by(
            timestamp=ts, value_mgdl=round(value, 1), source=source
        ).first()
        if existe:
            duplicados += 1
            continue

        lectura = GlucoseReading(
            timestamp=ts,
            value_mgdl=round(value, 1),
            source=source,
        )
        db.session.add(lectura)
        insertados += 1

        if fecha_min is None or ts < fecha_min:
            fecha_min = ts
        if fecha_max is None or ts > fecha_max:
            fecha_max = ts

    db.session.flush()

    # Registrar la importación
    registro = CGMImport(
        filename=getattr(file_obj, "filename", "upload.csv"),
        records_count=insertados,
        date_from=fecha_min,
        date_to=fecha_max,
    )
    db.session.add(registro)
    db.session.commit()

    return {"total": total, "insertados": insertados, "duplicados": duplicados}
