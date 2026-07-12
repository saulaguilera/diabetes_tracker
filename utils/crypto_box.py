"""
utils/crypto_box.py — cifrado simétrico para credenciales por usuario
(LibreLinkUp email/password en la tabla users).

Fernet con clave derivada de SECRET_KEY (SHA-256 → urlsafe base64). Si algún
día rota SECRET_KEY, los valores no descifran → el usuario re-conecta su
sensor (falla segura, nunca texto plano).
"""
from __future__ import annotations

import base64
import hashlib
import os


def _fernet():
    from cryptography.fernet import Fernet
    secret = os.environ.get("SECRET_KEY", "diabetes-tracker-secret-2024")
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
    return Fernet(key)


def encrypt(text: str) -> str:
    if not text:
        return ""
    return _fernet().encrypt(text.encode()).decode()


def decrypt(token: str) -> str:
    """Devuelve '' si no hay valor o no descifra (clave rotada / corrupto)."""
    if not token:
        return ""
    try:
        return _fernet().decrypt(token.encode()).decode()
    except Exception:
        return ""


def encrypt_bytes(data: bytes) -> bytes:
    return _fernet().encrypt(data)


def decrypt_bytes(token: bytes) -> bytes:
    return _fernet().decrypt(token)
