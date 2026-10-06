"""MAX transport and Internet boundary; never use a real bot token or remote host."""

import json
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from app.adapters.output import Adapter, PreparedNotification, network
from app.api.channel_schemas import MaxConfiguration
from app.api.errors import ApiFailure
from app.settings import Settings


def settings(**kwargs):
    return Settings(
        max_allowed=True,
        outbound_hosts=["platform-api2.max.ru"],
        outbound_networks=["8.8.8.8/32"],
        **kwargs,
    )


@pytest.mark.parametrize(
    "chat_id", ["0", "9223372036854775808", "-9223372036854775809", "1&token=x"]
)
def test_invalid_chat_ids(chat_id):
    with pytest.raises(ValidationError):
        MaxConfiguration(kind="MAX", chat_id=chat_id)


def test_max_uses_authorization_header_get_probe_and_no_link_preview(monkeypatch):
    captured = []
    replies = [(200, {"is_bot": True}), (200, {"message": {"body": {"mid": "test-id"}}})]

    class Connection:
        sock = None

        def __init__(self, host, port, timeout):
            assert (host, port) == ("platform-api2.max.ru", 443)

        def request(self, method, path, body, headers):
            captured.append((method, path, body, headers))

        def getresponse(self):
            status, data = replies.pop(0)
            return Mock(
                status=status,
                read=lambda limit: json.dumps(data).encode(),
                getheader=lambda _: None,
            )

        def close(self):
            pass

    monkeypatch.setattr("app.adapters.output.http.client.HTTPSConnection", Connection)
    monkeypatch.setattr(Adapter, "tls_socket", lambda _: object())
    monkeypatch.setattr(network, "resolve", lambda *_: ["8.8.8.8"])
    adapter = Adapter(settings(), {"kind": "MAX", "chat_id": "-100"}, lambda: "local-test-token")
    assert adapter.test_connection() == "BOT_VERIFIED"
    assert adapter.send(PreparedNotification("ignored", "Проверка")).outcome == "ACCEPTED"
    assert captured[0][:3] == ("GET", "/me", None)
    method, path, body, headers = captured[1]
    assert method == "POST" and path == "/messages?chat_id=-100&disable_link_preview=true"
    assert headers["Authorization"] == "local-test-token"
    assert "local-test-token" not in path
    assert json.loads(body) == {"text": "Проверка", "notify": True}


@pytest.mark.parametrize(
    "status,body,outcome,reason",
    [
        (429, "{}", "TRANSIENT", "RATE_LIMITED"),
        (503, "{}", "TRANSIENT", "REMOTE_UNAVAILABLE"),
        (401, "{}", "PERMANENT", "AUTHENTICATION_FAILED"),
        (302, "{}", "PERMANENT", "REMOTE_REJECTED"),
        (200, "{}", "UNKNOWN", "UNKNOWN_RESULT"),
        (200, "invalid", "UNKNOWN", "UNKNOWN_RESULT"),
    ],
)
def test_max_classifies_failures_without_returning_remote_body(
    monkeypatch, status, body, outcome, reason
):
    adapter = Adapter(settings(), {"kind": "MAX", "chat_id": "1"}, lambda: "not-logged")
    monkeypatch.setattr(network, "resolve", lambda *_: ["8.8.8.8"])
    monkeypatch.setattr(adapter, "http", lambda *args: (status, body.encode(), 25))
    result = adapter.send(PreparedNotification("", "test"))
    assert (result.outcome, result.reason) == (outcome, reason)
    if status == 429:
        assert result.retry_after == 25


def test_public_allowlist_cannot_enable_arbitrary_internet(monkeypatch):
    configured = settings()
    configured.outbound_hosts = ["example.org", "platform-api2.max.ru"]
    configured.outbound_networks = ["0.0.0.0/0"]
    monkeypatch.setattr(network, "resolve", lambda *_: ["8.8.8.8"])
    with pytest.raises(ApiFailure):
        network.validate_destination(configured, "example.org", 443)
    assert network.validate_destination(configured, "platform-api2.max.ru", 443) == ["8.8.8.8"]
    configured.max_allowed = False
    with pytest.raises(ApiFailure):
        network.validate_destination(configured, "platform-api2.max.ru", 443)


def test_max_payload_limit_blocks_before_send(monkeypatch):
    adapter = Adapter(settings(), {"kind": "MAX", "chat_id": "1"})
    monkeypatch.setattr(network, "resolve", lambda *_: ["8.8.8.8"])
    request = Mock()
    monkeypatch.setattr(adapter, "http", request)
    assert adapter.send(PreparedNotification("", "x" * 4001)).reason == "TEMPLATE_INVALID"
    request.assert_not_called()


@pytest.mark.anyio
async def test_max_api_persists_encrypted_secret_and_obeys_server_switch(setup, monkeypatch):
    from uuid import UUID

    from httpx import ASGITransport, AsyncClient
    from test_channels import channel, template
    from test_identity import headers, login

    from app.persistence.models import NotificationChannel
    from app.security.channel_secrets import reveal

    app, factory, password, _, config = setup
    async with AsyncClient(transport=ASGITransport(app), base_url="https://test") as client:
        auth = headers(await login(client, password))
        body = channel(configuration={"kind": "MAX", "chat_id": "-123"}, secret="MAX_PRIVATE_TOKEN")
        assert (await client.post("/api/v1/channels", headers=auth, json=body)).status_code == 422
        config.max_allowed = True
        config.outbound_hosts = ["platform-api2.max.ru"]
        config.outbound_networks = ["8.8.8.8/32"]
        monkeypatch.setattr(network, "resolve", lambda *_: ["8.8.8.8"])
        response = await client.post("/api/v1/templates", headers=auth, json=template("MAX"))
        assert response.status_code == 201, response.text
        body["template_id"] = response.json()["id"]
        response = await client.post("/api/v1/channels", headers=auth, json=body)
        assert response.status_code == 201, response.text
        row = response.json()
        assert row["configured"] and row["secret_configured"] and row["available"]
        assert "MAX_PRIVATE_TOKEN" not in response.text
        with factory() as db:
            saved = db.get(NotificationChannel, UUID(row["id"]))
            assert "MAX_PRIVATE_TOKEN" not in saved.secret_ciphertext
            assert (
                reveal(config, saved.team_id, saved.id, saved.secret_ciphertext)
                == "MAX_PRIVATE_TOKEN"
            )
        assert "MAX_PRIVATE_TOKEN" not in (await client.get("/api/v1/audit")).text
        config.max_allowed = False
        assert not (await client.get("/api/v1/channels")).json()["items"][0]["available"]
