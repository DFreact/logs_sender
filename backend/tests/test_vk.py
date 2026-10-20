"""VK connector checks with local mocks only; no real token or Internet delivery."""

import json
from unittest.mock import Mock
from urllib.parse import parse_qs

import pytest
from pydantic import ValidationError

from app.adapters.output import Adapter, BeforeSend, PreparedNotification, network
from app.api.channel_schemas import VKConfiguration
from app.api.errors import ApiFailure
from app.settings import Settings


def settings():
    return Settings(
        vk_allowed=True, outbound_hosts=["api.vk.com"], outbound_networks=["8.8.8.8/32"]
    )


def adapter(monkeypatch):
    monkeypatch.setattr(network, "resolve", lambda *_: ["8.8.8.8"])
    return Adapter(
        settings(),
        {"kind": "VK", "group_id": "123", "peer_id": "2000000001"},
        lambda: "test-secret&+only",
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("group_id", "0"),
        ("group_id", "-1"),
        ("peer_id", "0"),
        ("peer_id", "9223372036854775808"),
        ("peer_id", "1&access_token=x"),
    ],
)
def test_invalid_configuration(field, value):
    config = {"kind": "VK", "group_id": "1", "peer_id": "2", field: value}
    with pytest.raises(ValidationError):
        VKConfiguration(**config)


def test_form_transport_and_stable_retry_identifier(monkeypatch):
    instance = adapter(monkeypatch)
    captured = []
    replies = [
        {"response": {"permissions": [{"name": "messages", "setting": 1}]}},
        {"response": 1},
        {"response": 1},
        {"response": 2},
    ]

    class Connection:
        def __init__(self, host, port, timeout):
            assert (host, port, timeout) == ("api.vk.com", 443, 3)

        def request(self, method, path, body, headers):
            assert method == "POST"
            assert "test-secret" not in path and "?" not in path
            assert "Authorization" not in headers
            assert headers["Content-Type"] == "application/x-www-form-urlencoded"
            captured.append((path, parse_qs(body.decode())))

        def getresponse(self):
            return Mock(
                status=200,
                read=lambda limit: json.dumps(replies.pop(0)).encode(),
                getheader=lambda _: None,
            )

        def close(self):
            pass

    monkeypatch.setattr("app.adapters.output.http.client.HTTPSConnection", Connection)
    monkeypatch.setattr(instance, "tls_socket", lambda: object())
    assert instance.test_connection() == "COMMUNITY_VERIFIED"
    for key in ["notification-1", "notification-1", "notification-2"]:
        assert (
            instance.send(PreparedNotification("", "Тест & +", idempotency_key=key)).outcome
            == "ACCEPTED"
        )
    assert captured[0][0] == "/method/groups.getTokenPermissions"
    assert captured[1][0] == "/method/messages.send"
    data = captured[1][1]
    assert data["access_token"] == ["test-secret&+only"]
    assert data["message"] == ["Тест & +"]
    assert data["group_id"] == ["123"] and data["peer_id"] == ["2000000001"]
    assert data["v"] == ["5.199"] and data["dont_parse_links"] == ["1"]
    assert data["disable_mentions"] == ["1"]
    assert data["random_id"] == captured[2][1]["random_id"]
    assert data["random_id"] != captured[3][1]["random_id"]
    assert 0 < int(data["random_id"][0]) < 2**31


@pytest.mark.parametrize(
    "code,outcome,reason",
    [
        (6, "TRANSIENT", "RATE_LIMITED"),
        (9, "TRANSIENT", "RATE_LIMITED"),
        (29, "TRANSIENT", "RATE_LIMITED"),
        (10, "TRANSIENT", "REMOTE_UNAVAILABLE"),
        (5, "PERMANENT", "AUTHENTICATION_FAILED"),
        (27, "PERMANENT", "AUTHENTICATION_FAILED"),
        (901, "PERMANENT", "RECIPIENT_REJECTED"),
        (902, "PERMANENT", "RECIPIENT_REJECTED"),
        (15, "PERMANENT", "REMOTE_REJECTED"),
        (14, "PERMANENT", "REMOTE_REJECTED"),
    ],
)
def test_api_errors_even_with_http_200(monkeypatch, code, outcome, reason):
    instance = adapter(monkeypatch)
    monkeypatch.setattr(
        instance,
        "vk_http",
        lambda *args: (
            200,
            json.dumps(
                {"error": {"error_code": code, "error_msg": "private remote detail"}}
            ).encode(),
            None,
        ),
    )
    result = instance.send(PreparedNotification("", "Тест", idempotency_key="1"))
    assert (result.outcome, result.reason) == (outcome, reason)
    assert "private" not in repr(result)


@pytest.mark.parametrize(
    "status,body,outcome",
    [
        (429, b"{}", "TRANSIENT"),
        (503, b"{}", "TRANSIENT"),
        (401, b"{}", "PERMANENT"),
        (302, b"{}", "PERMANENT"),
        (200, b"{}", "UNKNOWN"),
        (200, b"invalid", "UNKNOWN"),
        (200, b'{"response":true}', "UNKNOWN"),
        (200, b"[]", "UNKNOWN"),
        (204, b"", "UNKNOWN"),
        (200, b'{"response":0}', "ACCEPTED"),
    ],
)
def test_transport_results(monkeypatch, status, body, outcome):
    instance = adapter(monkeypatch)
    monkeypatch.setattr(instance, "vk_http", lambda *args: (status, body, 25))
    result = instance.send(PreparedNotification("", "Тест", idempotency_key="1"))
    assert result.outcome == outcome
    if status == 429:
        assert result.retry_after == 25


@pytest.mark.parametrize("error,outcome", [(BeforeSend(), "TRANSIENT"), (OSError(), "UNKNOWN")])
def test_transport_ambiguity(monkeypatch, error, outcome):
    instance = adapter(monkeypatch)
    monkeypatch.setattr(instance, "vk_http", Mock(side_effect=error))
    assert instance.send(PreparedNotification("", "Тест", idempotency_key="1")).outcome == outcome


@pytest.mark.parametrize(
    "permissions", [[], [{"name": "messages", "setting": 0}], [{"name": "photos", "setting": 1}]]
)
def test_probe_requires_messages_permission(monkeypatch, permissions):
    instance = adapter(monkeypatch)
    monkeypatch.setattr(
        instance,
        "vk_http",
        lambda *args: (200, json.dumps({"response": {"permissions": permissions}}).encode(), None),
    )
    with pytest.raises(ApiFailure):
        instance.test_connection()


def test_network_switch_and_host_boundary(monkeypatch):
    instance = adapter(monkeypatch)
    config = instance.settings
    config.outbound_hosts += ["example.org"]
    config.outbound_networks = ["0.0.0.0/0"]
    assert network.validate_destination(config, "api.vk.com", 443) == ["8.8.8.8"]
    with pytest.raises(ApiFailure):
        network.validate_destination(config, "example.org", 443)
    config.vk_allowed = False
    with pytest.raises(ApiFailure):
        instance.destination()
    with pytest.raises(ApiFailure):
        network.validate_destination(config, "api.vk.com", 443)


@pytest.mark.parametrize("body,key", [("", "1"), ("x" * 9001, "1"), ("😀" * 4501, "1"), ("x", "")])
def test_payload_limits_before_send(monkeypatch, body, key):
    instance = adapter(monkeypatch)
    send = Mock()
    monkeypatch.setattr(instance, "vk_http", send)
    assert (
        instance.send(PreparedNotification("", body, idempotency_key=key)).reason
        == "TEMPLATE_INVALID"
    )
    send.assert_not_called()


@pytest.mark.anyio
async def test_channel_token_lifecycle(setup, monkeypatch):
    from uuid import UUID

    from httpx import ASGITransport, AsyncClient
    from test_channels import channel, template
    from test_identity import headers, login

    from app.persistence.models import NotificationChannel
    from app.security.channel_secrets import reveal

    app, factory, password, _, config = setup
    monkeypatch.setattr(network, "resolve", lambda *_: ["8.8.8.8"])
    async with AsyncClient(transport=ASGITransport(app), base_url="https://test") as client:
        auth = headers(await login(client, password))
        body = channel(
            configuration={"kind": "VK", "group_id": "123", "peer_id": "2000000001"},
            secret="test-token-one",
        )
        assert (await client.post("/api/v1/channels", headers=auth, json=body)).status_code == 422
        config.vk_allowed = True
        config.outbound_hosts = ["api.vk.com"]
        config.outbound_networks = ["8.8.8.8/32"]
        response = await client.post("/api/v1/templates", headers=auth, json=template("VK"))
        assert response.status_code == 201, response.text
        body["template_id"] = response.json()["id"]
        response = await client.post("/api/v1/channels", headers=auth, json=body)
        assert response.status_code == 201, response.text
        row = response.json()
        assert row["available"] and row["secret_configured"]
        path = "/api/v1/channels/" + row["id"]
        for secret in [None, "test-token-two", None]:
            body.update(version=row["version"], secret_version=row["secret_version"])
            body.pop("secret", None)
            if secret:
                body["secret"] = secret
            response = await client.patch(path, headers=auth, json=body)
            assert response.status_code == 200, response.text
            row = response.json()
            assert "test-token" not in response.text
            with factory() as db:
                saved = db.get(NotificationChannel, UUID(row["id"]))
                expected = "test-token-one" if row["secret_version"] == 1 else "test-token-two"
                assert "test-token" not in saved.secret_ciphertext
                assert reveal(config, saved.team_id, saved.id, saved.secret_ciphertext) == expected
        assert "test-token" not in (await client.get("/api/v1/audit")).text
        config.vk_allowed = False
        assert not (await client.get("/api/v1/channels")).json()["items"][0]["available"]
        body.update(version=row["version"], secret_version=row["secret_version"], clear_secret=True)
        response = await client.patch(path, headers=auth, json=body)
        assert response.status_code == 200
        assert not response.json()["secret_configured"]
