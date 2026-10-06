"""Signed pagination positions, scoped to a team and an unchanged filter set."""

import base64
import hashlib
import hmac
import json
import time
from datetime import datetime
from uuid import UUID

from app.api.errors import ApiFailure, ErrorCode
from app.security.tokens import signed


def scope_for(team, filters):
    return hashlib.sha256(
        json.dumps([str(team), filters], sort_keys=True, default=str).encode()
    ).hexdigest()


def encode_cursor(settings, scope, before, position, issued=None):
    data = [
        scope,
        before.isoformat(),
        position[0].isoformat(),
        str(position[1]),
        issued or int(time.time()),
    ]
    payload = base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")
    return payload + "." + signed(settings, "events:" + payload)


def decode_cursor(settings, token, scope):
    try:
        payload, signature = token.split(".")
        if not hmac.compare_digest(signature, signed(settings, "events:" + payload)):
            raise ValueError()
        data = json.loads(
            base64.b64decode(payload + "=" * (-len(payload) % 4), altchars=b"-_", validate=True)
        )
        bound, before, at, identifier, issued = data
        before, at, identifier = (
            datetime.fromisoformat(before),
            datetime.fromisoformat(at),
            UUID(identifier),
        )
        if (
            bound != scope
            or not before.tzinfo
            or not at.tzinfo
            or not 0 <= time.time() - issued <= 3600
        ):
            raise ValueError()
        return before, (at, identifier), issued
    except (ValueError, TypeError, OverflowError):
        raise ApiFailure(422, ErrorCode.CURSOR_INVALID) from None
