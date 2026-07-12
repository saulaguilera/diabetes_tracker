"""
utils/db_backup.py — backup automático de la DB a un bucket S3 de Railway.

Por qué: la SQLite vive en UN volumen; con multi-usuario eso son datos de
salud de terceros sin copia. Esto sube un snapshot cifrado a object storage
(otro dominio de falla) una vez al día, con retención de 30 copias.

Pipeline: sqlite3 backup API (snapshot consistente aunque la DB esté en uso)
→ gzip → Fernet (clave derivada de SECRET_KEY, utils/crypto_box) → S3.

Trigger: el cron de sync llama a maybe_backup() en cada corrida; solo ejecuta
si pasaron ≥24h del último backup (setting global "backup_last").

Restore (manual, documentado aquí):
    1. Descargar el .db.gz.enc más nuevo del bucket (aws s3 cp / consola).
    2. python -c "from utils.crypto_box import decrypt_bytes; import sys,gzip;
       open('restored.db','wb').write(gzip.decompress(decrypt_bytes(open(sys.argv[1],'rb').read())))" backup.db.gz.enc
       (con SECRET_KEY del entorno de producción)
    3. Reemplazar diabetes.db en el volumen y redeployar.
"""
from __future__ import annotations

import gzip
import io
import logging
import os
import sqlite3
import tempfile
from datetime import datetime

log = logging.getLogger("db.backup")

_KEEP = 30                    # copias a retener
_MIN_INTERVAL_H = 24          # como mucho un backup por día


def _s3():
    """Cliente S3 (bucket de Railway). None si no está configurado."""
    endpoint = os.environ.get("BACKUP_S3_ENDPOINT", "")
    key = os.environ.get("BACKUP_S3_KEY_ID", "")
    secret = os.environ.get("BACKUP_S3_SECRET", "")
    if not (endpoint and key and secret):
        return None
    import boto3
    from botocore.config import Config
    style = os.environ.get("BACKUP_S3_URL_STYLE", "path")
    return boto3.client(
        "s3", endpoint_url=endpoint,
        region_name=os.environ.get("BACKUP_S3_REGION", "auto"),
        aws_access_key_id=key, aws_secret_access_key=secret,
        config=Config(s3={"addressing_style": "virtual" if style == "virtual" else "path"}),
    )


def _bucket():
    return os.environ.get("BACKUP_S3_BUCKET", "orbit-backups")


def _db_path():
    from models import db
    p = db.engine.url.database
    return p


def run_backup() -> dict:
    """Snapshot → gzip → cifrado → upload → prune. Nunca levanta al caller."""
    try:
        s3 = _s3()
        if s3 is None:
            return {"ok": False, "reason": "sin_configurar"}

        src_path = _db_path()
        if not src_path or not os.path.exists(src_path):
            return {"ok": False, "reason": f"db_no_encontrada: {src_path}"}

        # snapshot consistente con la API de backup de sqlite (no bloquea)
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            src = sqlite3.connect(src_path)
            dst = sqlite3.connect(tmp.name)
            with dst:
                src.backup(dst)
            src.close(); dst.close()
            raw = open(tmp.name, "rb").read()

        comp = gzip.compress(raw, compresslevel=6)
        from utils.crypto_box import encrypt_bytes
        enc = encrypt_bytes(comp)

        key = f"orbit-{datetime.now().strftime('%Y%m%d-%H%M%S')}.db.gz.enc"
        s3.upload_fileobj(io.BytesIO(enc), _bucket(), key)
        log.info("backup subido: %s (%.1f MB → %.1f MB cifrado)",
                 key, len(raw) / 1e6, len(enc) / 1e6)

        # retención: conservar las _KEEP más nuevas
        borradas = 0
        try:
            objs = s3.list_objects_v2(Bucket=_bucket()).get("Contents", [])
            nuestras = sorted((o["Key"] for o in objs
                               if o["Key"].startswith("orbit-") and o["Key"].endswith(".db.gz.enc")))
            for k in nuestras[:-_KEEP]:
                s3.delete_object(Bucket=_bucket(), Key=k)
                borradas += 1
        except Exception as exc:
            log.warning("prune de backups falló: %s", exc)

        return {"ok": True, "key": key, "size_mb": round(len(enc) / 1e6, 2),
                "pruned": borradas}
    except Exception as exc:
        log.warning("backup falló: %s", exc)
        return {"ok": False, "reason": str(exc)[:200]}


def maybe_backup() -> dict | None:
    """Corre el backup si pasaron ≥24h del último. Pensada para el cron de sync."""
    from helpers import _get_setting, _set_setting
    last = _get_setting("backup_last")
    if last:
        try:
            if (datetime.now() - datetime.fromisoformat(last)).total_seconds() \
                    < _MIN_INTERVAL_H * 3600:
                return None
        except Exception:
            pass
    _set_setting("backup_last", datetime.now().isoformat())   # antes de correr: sin martilleo si falla
    res = run_backup()
    if res.get("ok"):
        _set_setting("backup_last_ok", datetime.now().isoformat())
        _set_setting("backup_last_key", res.get("key") or "")
    return res
