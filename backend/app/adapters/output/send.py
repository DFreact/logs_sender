"""Terminate the isolated sender before its persisted lease can expire."""

import json
import os
import subprocess
import sys
from pathlib import Path

from app.adapters.output import DeliveryResult
from app.domain.enums import DeliveryReason


def send_isolated(settings, dispatch):
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
        "configuration": dispatch.configuration,
        "prepared": {**dispatch.prepared, "idempotency_key": dispatch.idempotency_key},
        "secret": dispatch.secret,
    }
    try:
        result = subprocess.run(
            [sys.executable, "-I", str(Path(__file__).with_name("send_worker.py"))],
            input=json.dumps(data, default=str).encode(),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=settings.delivery_timeout_seconds,
            check=False,
            cwd="/tmp",
            env={"PATH": os.defpath, "LANG": "C.UTF-8"},
        )
        if result.returncode or len(result.stdout) > 2048:
            raise ValueError()
        value = json.loads(result.stdout)
        if not isinstance(value, dict) or value.get("outcome") not in (
            "ACCEPTED",
            "TRANSIENT",
            "PERMANENT",
            "UNKNOWN",
        ):
            raise ValueError()
        reason = value.get("reason")
        if reason is not None and reason not in DeliveryReason:
            raise ValueError()
        retry = value.get("retry_after")
        if retry is not None and (type(retry) is not int or not 0 <= retry <= 3600):
            raise ValueError()
        return DeliveryResult(value["outcome"], reason, retry)
    except (ValueError, TypeError, OSError, subprocess.SubprocessError):
        return DeliveryResult("UNKNOWN", "UNKNOWN_RESULT")
