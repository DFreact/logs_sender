"""Authenticated encryption; database rows never contain plaintext credentials."""

import base64
import hashlib
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.api.errors import ApiFailure, ErrorCode


def cipher(settings):
    value = (
        settings.channel_secret_file.read_text().strip()
        if settings.channel_secret_file
        else settings.channel_secret.get_secret_value()
        if settings.channel_secret
        else ""
    )
    if len(value) < 32:
        raise ApiFailure(503, ErrorCode.CHANNEL_SECRET_UNAVAILABLE)
    return AESGCM(hashlib.sha256(value.encode()).digest())


def protect(settings, team_id, channel_id, secret):
    nonce = os.urandom(12)
    aad = f"eventhub:channel:v1:{team_id}:{channel_id}".encode()
    return (
        "v1:"
        + base64.b64encode(nonce + cipher(settings).encrypt(nonce, secret.encode(), aad)).decode()
    )


def reveal(settings, team_id, channel_id, value):
    if not value:
        return ""
    try:
        if not value.startswith("v1:"):
            raise ValueError()
        raw = base64.b64decode(value[3:], validate=True)
        aad = f"eventhub:channel:v1:{team_id}:{channel_id}".encode()
        return cipher(settings).decrypt(raw[:12], raw[12:], aad).decode()
    except Exception:
        raise ApiFailure(503, ErrorCode.CHANNEL_SECRET_UNAVAILABLE) from None
