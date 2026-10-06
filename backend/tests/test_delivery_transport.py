"""Classify failures without returning untrusted remote diagnostics."""

import json
import smtplib
import subprocess
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.adapters.output import Adapter, BeforeSend, PreparedNotification
from app.adapters.output.retry import retry_after
from app.adapters.output.send import send_isolated


@pytest.mark.parametrize(
    "value, expected",
    [
        ("120", 120),
        ("9999999999", 3600),
        ("-1", None),
        ("bad", None),
        (None, None),
        ("x" * 129, None),
        ("1.5", None),
        ("Tue, 29 Sep 2026 00:02:00 GMT", 120),
        ("Mon, 28 Sep 2026 00:00:00 GMT", 0),
    ],
)
def test_retry_after_is_bounded(value, expected):
    assert retry_after(value, now=datetime(2026, 9, 29, tzinfo=UTC)) == expected


@pytest.mark.parametrize(
    "status, body, outcome, reason, delay",
    [
        (429, b'{"parameters":{"retry_after":9000}}', "TRANSIENT", "RATE_LIMITED", 3600),
        (503, b"private remote traceback", "TRANSIENT", "REMOTE_UNAVAILABLE", 10),
        (403, b"secret remote token", "PERMANENT", "AUTHENTICATION_FAILED", 10),
        (400, b"bad request", "PERMANENT", "REMOTE_REJECTED", 10),
        (200, b'{"ok":false}', "UNKNOWN", "UNKNOWN_RESULT", None),
        (200, b'{"ok":true}', "ACCEPTED", None, None),
    ],
)
def test_http_result_allowlist(setup, monkeypatch, status, body, outcome, reason, delay):
    adapter = Adapter(setup[-1], {"kind": "TELEGRAM", "chat_id": "-123"})
    monkeypatch.setattr(adapter, "validate_configuration", lambda: None)
    http = Mock(return_value=(status, body, 10))
    monkeypatch.setattr(adapter, "http", http)
    result = adapter.send(PreparedNotification("", "Текст", idempotency_key="stable-key"))
    assert (result.outcome, result.reason, result.retry_after) == (outcome, reason, delay)
    assert http.call_args.args[-1] == "stable-key"
    assert "secret" not in repr(result) and "traceback" not in repr(result)


@pytest.mark.parametrize(
    "failure, outcome, reason",
    [
        (smtplib.SMTPDataError(451, b"secret"), "TRANSIENT", "REMOTE_REJECTED"),
        (smtplib.SMTPDataError(550, b"secret"), "PERMANENT", "REMOTE_REJECTED"),
        (smtplib.SMTPAuthenticationError(535, b"secret"), "PERMANENT", "AUTHENTICATION_FAILED"),
        (
            smtplib.SMTPRecipientsRefused({"x@y.org": (450, b"secret")}),
            "TRANSIENT",
            "RECIPIENT_REJECTED",
        ),
        (
            smtplib.SMTPRecipientsRefused({"x@y.org": (550, b"secret")}),
            "PERMANENT",
            "RECIPIENT_REJECTED",
        ),
        (TimeoutError("secret"), "UNKNOWN", "UNKNOWN_RESULT"),
    ],
)
def test_smtp_failures(setup, monkeypatch, failure, outcome, reason):
    adapter = Adapter(
        setup[-1], {"kind": "SMTP", "sender": "hub@example.org", "recipients": ["ops@example.org"]}
    )
    monkeypatch.setattr(adapter, "validate_configuration", lambda: None)
    client = Mock()
    client.send_message.side_effect = failure
    monkeypatch.setattr(adapter, "smtp", lambda: client)
    result = adapter.send(PreparedNotification("Тема", "Текст"))
    assert (result.outcome, result.reason) == (outcome, reason)
    assert "secret" not in repr(result)
    client.close.assert_called_once()
    client.send_message.side_effect = None
    client.send_message.return_value = {"x@y.org": (450, b"secret")}
    assert adapter.send(PreparedNotification("Тема", "Текст")).outcome == "UNKNOWN"


def test_failure_before_http_send_is_retryable(setup, monkeypatch):
    adapter = Adapter(setup[-1], {"kind": "TELEGRAM", "chat_id": "-123"})
    monkeypatch.setattr(adapter, "validate_configuration", lambda: None)
    monkeypatch.setattr(adapter, "http", Mock(side_effect=BeforeSend()))
    assert adapter.send(PreparedNotification("", "Текст")).outcome == "TRANSIENT"


@pytest.mark.parametrize(
    "output",
    [
        b"[]",
        b"null",
        b'{"outcome":"unexpected"}',
        b'{"outcome":"TRANSIENT","reason":"secret"}',
        b'{"outcome":"TRANSIENT","retry_after":true}',
        b'{"outcome":"TRANSIENT","retry_after":3601}',
        b"x" * 2049,
    ],
)
def test_isolated_sender_rejects_untrusted_result(setup, monkeypatch, output):
    dispatch = SimpleNamespace(
        configuration={}, prepared={}, idempotency_key="key", secret="private"
    )
    monkeypatch.setattr(
        subprocess, "run", Mock(return_value=SimpleNamespace(returncode=0, stdout=output))
    )
    result = send_isolated(setup[-1], dispatch)
    assert result.outcome == "UNKNOWN" and result.reason == "UNKNOWN_RESULT"


def test_isolated_sender_timeout_and_environment(setup, monkeypatch):
    dispatch = SimpleNamespace(
        configuration={}, prepared={}, idempotency_key="key", secret="private"
    )
    monkeypatch.setenv("HUB_AUTH_SECRET", "must-not-reach-child")
    run = Mock(side_effect=subprocess.TimeoutExpired("sender", 15))
    monkeypatch.setattr(subprocess, "run", run)
    assert send_isolated(setup[-1], dispatch).outcome == "UNKNOWN"
    kwargs = run.call_args.kwargs
    assert "HUB_AUTH_SECRET" not in kwargs["env"]
    assert kwargs["timeout"] < setup[-1].delivery_lease_seconds
    assert json.loads(kwargs["input"])["secret"] == "private"
    assert kwargs["stderr"] == subprocess.DEVNULL
