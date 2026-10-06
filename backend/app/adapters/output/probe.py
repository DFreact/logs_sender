"""Bound the entire probe, including slow DNS/TLS/SMTP and response streams."""

import json
import os
import subprocess
import sys
from pathlib import Path
from threading import BoundedSemaphore

from app.api.errors import ApiFailure, ErrorCode

_slots = BoundedSemaphore(2)


def probe(settings, configuration, secret):
    if not _slots.acquire(blocking=False):
        raise ApiFailure(429, ErrorCode.RATE_LIMITED)
    try:
        data = {
            "settings": {
                k: getattr(settings, k)
                for k in (
                    "outbound_ca_file",
                    "outbound_hosts",
                    "outbound_networks",
                    "outbound_ports",
                    "telegram_allowed",
                    "max_allowed",
                )
            },
            "configuration": configuration,
            "secret": secret,
        }
        result = subprocess.run(
            [sys.executable, "-I", str(Path(__file__).with_name("probe_worker.py"))],
            input=json.dumps(data, default=str).encode(),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
            cwd="/tmp",
            env={"PATH": os.defpath, "LANG": "C.UTF-8"},
        )
        output = json.loads(result.stdout)
        if result.returncode or output.get("result") not in (
            "CONNECTED",
            "TLS_ONLY",
            "BOT_VERIFIED",
        ):
            code = output.get("code")
            raise ApiFailure(
                422,
                ErrorCode(code)
                if code in (ErrorCode.OUTBOUND_BLOCKED, ErrorCode.MAIL_CONNECTION_FAILED)
                else ErrorCode.CHANNEL_CONNECTION_FAILED,
            )
        return output["result"]
    except (ValueError, OSError, subprocess.SubprocessError):
        raise ApiFailure(422, ErrorCode.CHANNEL_CONNECTION_FAILED) from None
    finally:
        _slots.release()
