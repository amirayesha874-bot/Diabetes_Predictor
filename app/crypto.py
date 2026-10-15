"""Encryption of sensitive health data at rest (Fernet / AES-128-CBC + HMAC)."""
import base64
import hashlib
import json

from cryptography.fernet import Fernet
from flask import current_app


def _fernet():
    digest = hashlib.sha256(current_app.config["DATA_KEY"].encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_json(obj) -> str:
    return _fernet().encrypt(json.dumps(obj).encode()).decode()


def decrypt_json(token):
    if not token:
        return {}
    return json.loads(_fernet().decrypt(token.encode()).decode())
