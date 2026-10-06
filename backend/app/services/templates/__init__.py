"""The only template entry point; the renderer never receives application secrets."""

import json
import os
import subprocess
import sys
from pathlib import Path
from threading import BoundedSemaphore

from app.api.errors import ApiFailure, ErrorCode

_slots = BoundedSemaphore(2)
FIELDS = ("subject", "body", "sender", "source", "severity", "category", "event_type")


def render(template, event=None):
    event = event or {}
    context = {key: event.get(key, "") for key in FIELDS}
    if any(not isinstance(v, str) or len(v.encode()) > 262144 for v in context.values()):
        raise ApiFailure(422, ErrorCode.TEMPLATE_INVALID)
    data = {
        "template": {k: template.get(k, "") for k in ("kind", "subject", "body", "html")},
        "event": context,
    }
    raw = json.dumps(data).encode()
    if len(raw) > 1024 * 1024 or not _slots.acquire(blocking=False):
        raise ApiFailure(422, ErrorCode.TEMPLATE_INVALID)
    try:
        result = subprocess.run(
            [sys.executable, "-I", str(Path(__file__).with_name("renderer.py"))],
            input=raw,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=3,
            cwd="/tmp",
            env={"PATH": os.defpath, "LANG": "C.UTF-8"},
            check=False,
        )
        if result.returncode or len(result.stdout) > 400000:
            raise ValueError()
        return json.loads(result.stdout)
    except (ValueError, OSError, subprocess.TimeoutExpired):
        raise ApiFailure(422, ErrorCode.TEMPLATE_INVALID) from None
    finally:
        _slots.release()
