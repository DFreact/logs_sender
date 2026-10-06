import hashlib
import hmac
import ipaddress
import re
import secrets
import time

from fastapi import Request

from app.api.errors import ApiFailure, ErrorCode
from app.settings import Settings

TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")


def new_token() -> str:
    return secrets.token_urlsafe(32)


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def signed(settings: Settings, value: str) -> str:
    return hmac.new(settings.signing_key(), value.encode(), hashlib.sha256).hexdigest()


def origin_guard(request: Request, settings: Settings):
    if request.headers.get("origin") not in settings.allowed_origins:
        raise ApiFailure(403, ErrorCode.CSRF_FAILED)
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise ApiFailure(403, ErrorCode.CSRF_FAILED)


def preauth_pair(settings: Settings) -> tuple[str, str]:
    cookie = f"{new_token()}.{int(time.time())}"
    return cookie, signed(settings, "preauth:" + cookie)


def check_preauth(request: Request, settings: Settings):
    origin_guard(request, settings)
    cookie = request.cookies.get(settings.preauth_cookie, "")
    match = re.fullmatch(r"[A-Za-z0-9_-]{43}\.(\d{10})", cookie)
    age = time.time() - int(match[1]) if match else -1
    expected = signed(settings, "preauth:" + cookie)
    supplied = request.headers.get("x-csrf-token", "")
    if (
        not 0 <= age <= 600
        or not re.fullmatch(r"[a-f0-9]{64}", supplied)
        or not hmac.compare_digest(expected, supplied)
    ):
        raise ApiFailure(403, ErrorCode.CSRF_FAILED)


def csrf_for_session(settings: Settings, token: str) -> str:
    return signed(settings, "session:" + token)


def client_ip(request: Request, settings: Settings) -> str:
    try:
        address = ipaddress.ip_address(request.client.host if request.client else "127.0.0.1")
        if any(address in ipaddress.ip_network(net) for net in settings.trusted_proxy_networks):
            address = ipaddress.ip_address(request.headers.get("x-real-ip", str(address)))
        return str(address)
    except ValueError:
        return "0.0.0.0"
