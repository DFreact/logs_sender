import json
import subprocess
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import event as sql_event
from sqlalchemy import func, select
from test_identity import create_user, headers, login

from app.adapters.output import Adapter, PreparedNotification, network
from app.api.errors import ApiFailure
from app.persistence.database import Base
from app.persistence.models import AuditEntry, NotificationChannel, TemplateVersion
from app.security.channel_secrets import protect, reveal
from app.services.templates import render


def template(kind="SMTP", **kw):
    return {
        "name": "Шаблон письма",
        "kind": kind,
        "subject": "{{ event.subject }}",
        "body": "{{ event.body }}",
        "html": "",
        "version": 1,
        **kw,
    }


def channel(template_id=None, **kw):
    return {
        "name": "Почтовый канал",
        "version": 1,
        "secret_version": 0,
        "enabled": True,
        "template_id": template_id,
        "configuration": {
            "kind": "SMTP",
            "host": "192.0.2.10",
            "port": 587,
            "tls": "STARTTLS",
            "username": "",
            "sender": "hub@example.org",
            "recipients": ["ops@example.org"],
        },
        **kw,
    }


def test_encryption_is_random_authenticated_and_scoped(setup):
    settings = setup[-1]
    team, identifier = uuid4(), uuid4()
    first = protect(settings, team, identifier, "VERY_PRIVATE_TOKEN")
    second = protect(settings, team, identifier, "VERY_PRIVATE_TOKEN")
    assert first != second and "VERY_PRIVATE_TOKEN" not in first
    assert reveal(settings, team, identifier, first) == "VERY_PRIVATE_TOKEN"
    for wrong_team, wrong_id, value in [
        (uuid4(), identifier, first),
        (team, uuid4(), first),
        (team, identifier, first[:-4] + "AAAA"),
    ]:
        with pytest.raises(ApiFailure):
            reveal(settings, wrong_team, wrong_id, value)
    settings.channel_secret = SecretStr("different-key" * 4)
    with pytest.raises(ApiFailure):
        reveal(settings, team, identifier, first)
    settings.channel_secret = None
    with pytest.raises(ApiFailure):
        protect(settings, team, identifier, "secret")


@pytest.mark.parametrize(
    "source",
    [
        "{{ event.__class__ }}",
        "{{ event.body.upper() }}",
        "{{ cycler.__init__ }}",
        "{% for x in range(1000000) %}x{% endfor %}",
        "{{ 'x' * 100000000 }}",
        "{% include '/etc/passwd' %}",
        "{{ event.body | safe }}",
        "{{ event.unknown }}",
        "{{ event.body | attr('__class__') }}",
        "{% set x = 1 %}",
        "{{ event['body'] }}",
        "{{ missing }}",
        "{{ event.body",
        "x" * 32769,
    ],
)
def test_template_rejects_unsafe_syntax(source):
    with pytest.raises(ApiFailure) as exc:
        render(template(body=source), {"body": "text"})
    assert exc.value.code == "TEMPLATE_INVALID"


def test_template_escaping_json_crlf_and_output_bounds():
    result = render(
        template(
            html=(
                '<p>{{ event.body }}</p><img src="https://example.org">'
                '<script>evil()</script><a href="javascript:evil()">link</a>'
            )
        ),
        {"subject": "Тема", "body": "<b>Текст</b>"},
    )
    assert result["subject"] == "Тема" and result["body"] == "<b>Текст</b>"
    assert result["html"] == "<p>&lt;b&gt;Текст&lt;/b&gt;</p>link"
    value = '"},"admin":true,"message":"'
    assert json.loads(render(template("WEBHOOK"), {"body": value})["payload"]) == {
        "subject": "",
        "message": value,
    }
    assert (
        render(template(body="{{ event.body | upper | trim }}"), {"body": " текст "})["body"]
        == "ТЕКСТ"
    )
    for t, data in [
        (template(), {"subject": "Тема\r\nBcc: private@example.org"}),
        (template(body="{{ event.body }}" * 20), {"body": "x" * 10000}),
        (template("TELEGRAM"), {"body": "😀" * 2049}),
    ]:
        with pytest.raises(ApiFailure):
            render(t, data)


def test_template_timeout_and_concurrency_return_safe_error(monkeypatch):
    import app.services.templates as service

    def timeout(*args, **kw):
        assert kw["timeout"] == 3 and not any(k.startswith("HUB_") for k in kw["env"])
        raise subprocess.TimeoutExpired("hidden", 3)

    monkeypatch.setattr(service.subprocess, "run", timeout)
    with pytest.raises(ApiFailure):
        render(template())
    assert service._slots.acquire(False) and service._slots.acquire(False)
    try:
        with pytest.raises(ApiFailure):
            render(template())
    finally:
        service._slots.release()
        service._slots.release()


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "::1",
        "169.254.169.254",
        "fe80::1",
        "0.0.0.0",
        "::",
        "224.0.0.1",
        "::ffff:127.0.0.1",
        "64:ff9b::a00:1",
        "2002:0a00:0001::",
        "2001::1",
        "10.0.0.1",
    ],
)
def test_ssrf_blocks_special_and_unlisted_addresses(address):
    with pytest.raises(ApiFailure):
        network.check_address(address, ["192.0.2.0/24"])


@pytest.mark.parametrize(
    "url",
    [
        "http://192.0.2.10/",
        "https://user:password@192.0.2.10/",
        "https://192.0.2.10/#x",
        "https://192.0.2.10/?token=secret",
        "https://192.0.2.10\\@example.org",
        "https://[fe80::1%eth0]/",
        "https://192.0.2.10/\r\nx",
    ],
)
def test_endpoint_rejects_unsafe_urls(url):
    with pytest.raises(ApiFailure):
        network.endpoint(url)


def test_all_dns_answers_rechecked_and_pinned(setup, monkeypatch):
    settings = setup[-1]
    settings.outbound_hosts = ["mail.example.org"]
    calls = []
    monkeypatch.setattr(network, "resolve", lambda *_: ["192.0.2.10", "127.0.0.1"])
    with pytest.raises(ApiFailure):
        network.validate_destination(settings, "mail.example.org", 587)
    monkeypatch.setattr(network, "resolve", lambda *_: ["192.0.2.10"])

    class Socket:
        def settimeout(self, t):
            pass

        def connect(self, target):
            calls.append(target)

        def getpeername(self):
            return ("192.0.2.10", 587)

        def close(self):
            calls.append("closed")

    monkeypatch.setattr(network.socket, "socket", lambda *_: Socket())
    network.connect(settings, "mail.example.org", 587)
    assert calls == [("192.0.2.10", 587)]
    monkeypatch.setattr(network, "resolve", lambda *_: ["169.254.169.254"])
    with pytest.raises(ApiFailure):
        network.connect(settings, "mail.example.org", 587)
    assert calls == [("192.0.2.10", 587)]
    monkeypatch.setattr(network, "resolve", lambda *_: ["192.0.2.10"])
    monkeypatch.setattr(Socket, "getpeername", lambda _: ("192.0.2.11", 587))
    with pytest.raises(ApiFailure):
        network.connect(settings, "mail.example.org", 587)
    assert calls[-1] == "closed"
    assert network.check_address("fd00::10", ["fd00::/64"]) == "fd00::10"
    assert network.endpoint("https://[fd00::10]:443/hook") == ("fd00::10", 443, "/hook")


@pytest.mark.anyio
async def test_channel_crud_secrets_versions_adapters_and_binding(setup):
    app, factory, password, *_ = setup
    async with AsyncClient(transport=ASGITransport(app), base_url="https://test") as client:
        auth = headers(await login(client, password))
        ts = await client.post("/api/v1/templates", headers=auth, json=template())
        assert ts.status_code == 201, ts.text
        body = channel(ts.json()["id"], secret="VERY_PRIVATE_TOKEN")
        response = await client.post("/api/v1/channels", headers=auth, json=body)
        assert response.status_code == 201, response.text
        row = response.json()
        assert row["secret_configured"] and row["available"] and row["secret_version"] == 1
        assert "VERY_PRIVATE" not in response.text and "ciphertext" not in response.text
        cid = row["id"]
        body["secret_version"] = 1
        body["secret"] = "ROTATED_PRIVATE_TOKEN"
        response = await client.patch("/api/v1/channels/" + cid, headers=auth, json=body)
        assert response.status_code == 200
        assert response.json()["version"] == 1 and response.json()["secret_version"] == 2
        assert (
            await client.patch("/api/v1/channels/" + cid, headers=auth, json=body)
        ).status_code == 409
        rule = {
            "name": "Отправка",
            "conditions": {"field": "sender", "operator": "exists"},
            "actions": [{"kind": "NOTIFY", "target_id": cid}],
        }
        response = await client.post("/api/v1/routing-rules", headers=auth, json=rule)
        assert response.status_code == 201, response.text
        rule_id = response.json()["id"]
        hidden = await client.patch(
            "/api/v1/output-adapters/SMTP",
            headers=auth,
            json={"version": 1, "enabled": True, "visible": False},
        )
        assert hidden.status_code == 200 and hidden.json()["affected_rules"] == ["Отправка"]
        assert not (await client.get("/api/v1/channels")).json()["items"][0]["available"]
        assert (
            await client.patch(
                "/api/v1/routing-rules/" + rule_id, headers=auth, json={**rule, "version": 1}
            )
        ).status_code == 200
        assert (
            await client.post("/api/v1/routing-rules", headers=auth, json=rule)
        ).status_code == 422
        assert (
            await client.post("/api/v1/channels", headers=auth, json=channel())
        ).status_code == 422
        assert (
            await client.patch(
                "/api/v1/output-adapters/TELEGRAM",
                headers=auth,
                json={"version": 1, "enabled": True, "visible": True},
            )
        ).status_code == 422
        text = (await client.get("/api/v1/audit")).text
        assert "PRIVATE_TOKEN" not in text and "Почтовый канал" in text
    with factory() as db:
        saved = db.get(NotificationChannel, UUID(cid))
        assert "PRIVATE_TOKEN" not in saved.secret_ciphertext
        assert (
            reveal(setup[-1], saved.team_id, saved.id, saved.secret_ciphertext)
            == "ROTATED_PRIVATE_TOKEN"
        )
        assert all(
            "PRIVATE_TOKEN" not in json.dumps(a.after) for a in db.scalars(select(AuditEntry))
        )


@pytest.mark.anyio
async def test_template_history_preview_is_read_only_and_permissions(setup, monkeypatch):
    app, factory, password, *_ = setup
    monkeypatch.setattr(Adapter, "send", lambda *_: pytest.fail("unexpected send"))
    async with AsyncClient(transport=ASGITransport(app), base_url="https://test") as client:
        auth = headers(await login(client, password))
        response = await client.post("/api/v1/templates", headers=auth, json=template())
        tid = response.json()["id"]
        assert (
            await client.patch(
                "/api/v1/templates/" + tid, headers=auth, json=template(body="Изменено")
            )
        ).status_code == 200
        history = (await client.get("/api/v1/templates/" + tid + "/versions")).json()
        assert (
            history["total"] == 2 and history["items"][1]["snapshot"]["body"] == "{{ event.body }}"
        )
        assert (
            await client.patch("/api/v1/templates/" + tid, headers=auth, json=template())
        ).status_code == 409
        with factory() as db:
            before = {
                t.name: db.scalar(select(func.count()).select_from(t))
                for t in Base.metadata.sorted_tables
            }
        statements = []

        def observe(_, __, statement, ___, ____, _____):
            statements.append(statement.upper())

        sql_event.listen(factory.kw["bind"], "before_cursor_execute", observe)
        try:
            response = await client.post(
                "/api/v1/templates/preview",
                headers=auth,
                json={"template": template(), "event": {"subject": "Проверка", "body": "Текст"}},
            )
        finally:
            sql_event.remove(factory.kw["bind"], "before_cursor_execute", observe)
        assert response.status_code == 200 and response.json()["body"] == "Текст"
        assert not any(s.lstrip().startswith(("INSERT", "UPDATE", "DELETE")) for s in statements)
        with factory() as db:
            assert before == {
                t.name: db.scalar(select(func.count()).select_from(t))
                for t in Base.metadata.sorted_tables
            }
            assert db.scalar(select(func.count()).select_from(TemplateVersion)) == 2
        assert (
            await client.post("/api/v1/templates/preview", json={"template": template()})
        ).status_code == 403
        _, pw = await create_user(client, auth)
        await login(client, pw, "reader")
        for path in ("channels", "templates", "output-adapters"):
            assert (await client.get("/api/v1/" + path)).status_code == 403


@pytest.mark.anyio
async def test_probe_has_safe_errors_and_disabled_adapter_blocks(setup, monkeypatch):
    app, factory, password, *_ = setup
    async with AsyncClient(transport=ASGITransport(app), base_url="https://test") as client:
        auth = headers(await login(client, password))
        row = (await client.post("/api/v1/channels", headers=auth, json=channel())).json()

        def failure(*_):
            raise ApiFailure(422, "MAIL_CONNECTION_FAILED")

        monkeypatch.setattr("app.adapters.output.probe.probe", failure)
        response = await client.post(
            "/api/v1/channels/" + row["id"] + "/test-connection", headers=auth
        )
        assert response.status_code == 422 and response.json()["code"] == "MAIL_CONNECTION_FAILED"
        assert (await client.get("/api/v1/channels")).json()["items"][0][
            "health_status"
        ] == "UNAVAILABLE"
        await client.patch(
            "/api/v1/output-adapters/SMTP",
            headers=auth,
            json={"enabled": False, "visible": True, "version": 1},
        )
        assert (
            await client.post("/api/v1/channels/" + row["id"] + "/test-connection", headers=auth)
        ).json()["code"] == "CHANNEL_UNAVAILABLE"


def test_common_contract_classifies_outcomes_without_following_redirects(setup, monkeypatch):
    adapter = Adapter(setup[-1], {"kind": "WEBHOOK", "url": "https://192.0.2.10/hook"})
    prepared = PreparedNotification("Тема", "Текст", payload='{"subject":"Тема","message":"Текст"}')
    for status, expected in [
        (200, "ACCEPTED"),
        (302, "PERMANENT"),
        (429, "TRANSIENT"),
        (500, "TRANSIENT"),
        (401, "PERMANENT"),
    ]:
        monkeypatch.setattr(adapter, "http", lambda *_, status=status: (status, b"{}", None))
        assert adapter.send(prepared).outcome == expected

    def timeout(*_):
        raise TimeoutError("SECRET_TOKEN")

    monkeypatch.setattr(adapter, "http", timeout)
    assert adapter.send(prepared).outcome == "UNKNOWN"
    assert adapter.send(PreparedNotification("A\r\nB", "text")).outcome == "PERMANENT"


@pytest.mark.anyio
async def test_secret_clear_cross_type_and_invalid_input_are_safe(setup):
    app, _, password, *_ = setup
    async with AsyncClient(transport=ASGITransport(app), base_url="https://test") as client:
        auth = headers(await login(client, password))
        body = channel(secret="PRIVATE_TOKEN")
        response = await client.post("/api/v1/channels", headers=auth, json=body)
        identifier = response.json()["id"]
        body.pop("secret")
        body.update(secret_version=1, clear_secret=True)
        response = await client.patch("/api/v1/channels/" + identifier, headers=auth, json=body)
        assert response.status_code == 200 and not response.json()["secret_configured"]
        assert response.json()["secret_version"] == 2
        wrong = (
            await client.post("/api/v1/templates", headers=auth, json=template("WEBHOOK"))
        ).json()
        body.update(secret_version=2, clear_secret=False, template_id=wrong["id"])
        assert (
            await client.patch("/api/v1/channels/" + identifier, headers=auth, json=body)
        ).status_code == 422
        for values in [
            channel(secret="PRIVATE_TOKEN\r\nINJECTED"),
            channel(secret="PRIVATE_TOKEN", clear_secret=True),
            {**channel(), "password": "PRIVATE_TOKEN"},
        ]:
            response = await client.post("/api/v1/channels", headers=auth, json=values)
            assert response.status_code == 422 and "PRIVATE_TOKEN" not in response.text
        response = await client.patch(
            "/api/v1/channels/" + str(uuid4()), headers=auth, json=channel()
        )
        assert response.status_code == 404
