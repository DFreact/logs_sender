from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from test_identity import create_user, headers, login

from app.persistence.models import AuditEntry, IngestCredential, Team
from app.services.ingestion import issue_key
from app.workers.queue import clock

pytestmark = pytest.mark.anyio


async def test_key_issue_use_rotation_revoke_and_no_secret_disclosure(setup, background):
    app, factory, password, *_ = setup
    async with AsyncClient(transport=ASGITransport(app), base_url="https://test") as client:
        auth = headers(await login(client, password))
        response = await client.post(
            "/api/v1/connections/keys", headers=auth, json={"name": "Мониторинг"}
        )
        assert response.status_code == 201
        assert response.headers["cache-control"] == "no-store"
        original = response.json()
        token, identifier = original["token"], original["key"]["id"]
        with factory() as db:
            row = db.get(IngestCredential, UUID(identifier))
            assert token not in row.token_hash and row.token_hash != token
        for path in ["/api/v1/connections", "/api/v1/connections/keys", "/api/v1/audit"]:
            result = await client.get(path)
            assert token not in result.text and "token_hash" not in result.text
        request_headers = {"Authorization": "Bearer " + token, "Idempotency-Key": "test-one"}
        result = await client.post(
            "/api/v1/ingest", headers=request_headers, json={"subject": "Проверка"}
        )
        assert result.status_code == 202
        receipt = result.json()["receipt_id"]
        replacement = (
            await client.post(
                "/api/v1/connections/keys", headers=auth, json={"name": "Мониторинг — новый ключ"}
            )
        ).json()
        assert replacement["token"] != token
        assert (
            await client.post(
                "/api/v1/ingest",
                headers={"Authorization": "Bearer " + replacement["token"]},
                json={},
            )
        ).status_code == 202
        revoked = await client.post(f"/api/v1/connections/keys/{identifier}/revoke", headers=auth)
        assert revoked.json()["status"] == "REVOKED"
        assert token not in revoked.text
        assert (
            await client.post(f"/api/v1/connections/keys/{identifier}/revoke", headers=auth)
        ).status_code == 200
        assert (
            await client.post("/api/v1/ingest", headers=request_headers, json={})
        ).status_code == 401
        assert (
            await client.get(f"/api/v1/ingest/{receipt}", headers=request_headers)
        ).status_code == 401
        with factory() as db:
            assert (
                len(
                    db.scalars(
                        select(AuditEntry).where(
                            AuditEntry.entity_id == UUID(identifier),
                            AuditEntry.action == "INGEST_KEY_REVOKED",
                        )
                    ).all()
                )
                == 1
            )


async def test_management_requires_admin_csrf_and_team_scope(setup):
    app, factory, password, *_ = setup
    foreign = uuid4()
    with factory.begin() as db:
        db.add(Team(id=foreign, name="other"))
        db.flush()
        foreign_id, _ = issue_key(db, "Другой ключ", team_id=foreign)
    async with AsyncClient(transport=ASGITransport(app), base_url="https://test") as client:
        assert (await client.get("/api/v1/connections")).status_code == 401
        auth = headers(await login(client, password))
        assert (
            await client.post("/api/v1/connections/keys", json={"name": "Ключ"})
        ).status_code == 403
        assert (
            await client.post(f"/api/v1/connections/keys/{foreign_id}/revoke", headers=auth)
        ).status_code == 404
        assert str(foreign_id) not in (await client.get("/api/v1/connections/keys")).text
        for role in ("OPERATOR", "VIEWER"):
            _, member_password = await create_user(client, auth, username=role.lower(), role=role)
            async with AsyncClient(transport=ASGITransport(app), base_url="https://test") as member:
                member_auth = headers(await login(member, member_password, role.lower()))
                for path in ("/api/v1/connections", "/api/v1/connections/keys"):
                    assert (await member.get(path)).status_code == 403
                assert (
                    await member.post(
                        "/api/v1/connections/keys", headers=member_auth, json={"name": "Ключ"}
                    )
                ).status_code == 403
                assert (
                    await member.post(
                        f"/api/v1/connections/keys/{foreign_id}/revoke", headers=member_auth
                    )
                ).status_code == 403


async def test_bounded_pages_expiry_and_explicit_published_address(setup):
    app, factory, password, _, settings = setup
    settings.smtp_public_host = "mail-ingest.corp.example"
    settings.smtp_public_port = 2526
    settings.smtp_host = "0.0.0.0"
    with factory.begin() as db:
        for i in range(27):
            identifier, _ = issue_key(db, f"Источник {i}")
        db.get(IngestCredential, identifier).expires_at = clock(db) - timedelta(days=1)
    async with AsyncClient(transport=ASGITransport(app), base_url="https://test") as client:
        await login(client, password)
        response = (
            await client.get("/api/v1/connections", headers={"X-Forwarded-Host": "evil.example"})
        ).json()
        assert response["smtp"] == {
            "host": "mail-ingest.corp.example",
            "port": 2526,
            "recipients": ["events@localhost"],
            "tls_required": True,
        }
        assert response["rest_path"] == "/api/v1/ingest"
        first = (await client.get("/api/v1/connections/keys")).json()
        last = (await client.get("/api/v1/connections/keys?offset=25")).json()
        assert len(first["items"]) == 25 and first["has_more"]
        assert len(last["items"]) == 2 and not last["has_more"]
        assert len({r["id"] for r in first["items"] + last["items"]}) == 27
        assert "EXPIRED" in [r["status"] for r in first["items"] + last["items"]]


@pytest.mark.parametrize("kind", ["SMTP", "MAX", "TELEGRAM", "WEBHOOK"])
async def test_all_channel_kinds_rotate_preserve_and_clear_credentials(setup, monkeypatch, kind):
    from test_channels import channel

    from app.adapters.output import Adapter
    from app.persistence.models import NotificationChannel
    from app.security.channel_secrets import reveal

    app, factory, password, _, settings = setup
    settings.max_allowed = settings.telegram_allowed = True
    # Configuration validation has separate network-boundary tests; never resolve
    # or contact an Internet host while checking the secret lifecycle.
    monkeypatch.setattr(Adapter, "validate_configuration", lambda self: None)
    async with AsyncClient(transport=ASGITransport(app), base_url="https://test") as client:
        auth = headers(await login(client, password))
        if kind == "TELEGRAM":
            await client.patch(
                "/api/v1/output-adapters/TELEGRAM",
                headers=auth,
                json={"enabled": True, "visible": True, "version": 1},
            )
        config = (
            channel()["configuration"]
            if kind == "SMTP"
            else {"kind": kind, "url": "https://192.0.2.10/receive"}
            if kind == "WEBHOOK"
            else {"kind": kind, "chat_id": "123"}
        )
        old, new = "123:" + "a" * 24, "456:" + "b" * 24
        body = channel(configuration=config, secret=old)
        response = await client.post("/api/v1/channels", headers=auth, json=body)
        assert response.status_code == 201
        identifier = response.json()["id"]
        body.update(secret=new, secret_version=1)
        rotated = await client.patch("/api/v1/channels/" + identifier, headers=auth, json=body)
        assert rotated.status_code == 200 and rotated.json()["secret_version"] == 2
        assert (
            await client.patch("/api/v1/channels/" + identifier, headers=auth, json=body)
        ).status_code == 409
        with factory() as db:
            row = db.get(NotificationChannel, UUID(identifier))
            assert reveal(settings, row.team_id, row.id, row.secret_ciphertext) == new
        body.pop("secret")
        body["secret_version"] = 2
        kept = await client.patch("/api/v1/channels/" + identifier, headers=auth, json=body)
        assert kept.json()["secret_version"] == 2 and kept.json()["secret_configured"]
        body["clear_secret"] = True
        cleared = await client.patch("/api/v1/channels/" + identifier, headers=auth, json=body)
        assert cleared.json()["secret_version"] == 3 and not cleared.json()["secret_configured"]
        for path in ["/api/v1/channels", "/api/v1/audit"]:
            result = await client.get(path)
            assert old not in result.text and new not in result.text


async def test_key_limit_requires_revocation_and_does_not_create_or_audit_a_key(setup):
    from app.persistence.models import DEFAULT_TEAM_ID

    app, factory, password, *_ = setup
    with factory.begin() as db:
        now = clock(db)
        rows = [
            IngestCredential(
                team_id=DEFAULT_TEAM_ID,
                name=f"Ключ {i}",
                token_hash=f"{i:064x}",
                expires_at=now + timedelta(days=1),
            )
            for i in range(1000)
        ]
        db.add_all(rows)
        db.flush()
        identifier = rows[0].id
    async with AsyncClient(transport=ASGITransport(app), base_url="https://test") as client:
        auth = headers(await login(client, password))
        result = await client.post("/api/v1/connections/keys", headers=auth, json={"name": "Новый"})
        assert result.status_code == 422 and result.json()["code"] == "INGEST_KEY_LIMIT"
        with factory() as db:
            assert not db.scalars(
                select(AuditEntry).where(AuditEntry.action == "INGEST_KEY_CREATED")
            ).all()
        await client.post(f"/api/v1/connections/keys/{identifier}/revoke", headers=auth)
        result = await client.post("/api/v1/connections/keys", headers=auth, json={"name": "Новый"})
        assert result.status_code == 201
