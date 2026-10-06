import asyncio
import json
import os
import secrets
from datetime import timedelta
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy import event, select
from sqlalchemy.exc import SQLAlchemyError

from app.api.schemas import UserCreate
from app.domain.enums import UserRole
from app.main import create_app
from app.persistence.models import (
    DEFAULT_TEAM_ID,
    AuditEntry,
    AuthSession,
    Membership,
    Team,
    User,
    utcnow,
)
from app.security.audit import safe_snapshot
from app.security.bootstrap import create_first_admin
from app.security.passwords import verify_password
from app.security.permissions import Permission, permitted
from app.security.tokens import digest
from app.settings import Settings

pytestmark = pytest.mark.anyio
ORIGIN = "https://test"


@pytest.fixture
async def client(setup):
    async with AsyncClient(transport=ASGITransport(setup[0]), base_url=ORIGIN) as client:
        yield client


async def login(client, password, username="admin"):
    csrf = (await client.get("/api/v1/auth/csrf")).json()["csrf_token"]
    return await client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
        headers={"Origin": ORIGIN, "X-CSRF-Token": csrf},
    )


def headers(response):
    return {"Origin": ORIGIN, "X-CSRF-Token": response.json()["csrf_token"]}


async def create_user(client, auth_headers, username="reader", role="VIEWER"):
    password = secrets.token_urlsafe(24)
    response = await client.post(
        "/api/v1/users",
        headers=auth_headers,
        json={
            "username": username,
            "display_name": username,
            "password": password,
            "role": role,
        },
    )
    assert response.status_code == 201, response.json()["code"] if response.is_error else ""
    return response.json(), password


def update_body(user, **overrides):
    return {
        "display_name": user["display_name"],
        "active": user["active"],
        "role": user["role"],
        "version": user["version"],
        **overrides,
    }


async def test_login_cookie_session_rotation_logout_and_hash_storage(setup, client):
    response = await login(client, setup[2])
    assert response.status_code == 200
    cookie = response.headers.get_list("set-cookie")[0]
    assert all(flag in cookie for flag in ["HttpOnly", "Secure", "SameSite=strict", "Path=/"])
    old = client.cookies.get(setup[4].session_cookie)
    assert response.json()["user"]["role"] == "ADMINISTRATOR"
    assert "MANAGE_USERS" in response.json()["permissions"]
    with setup[1]() as db:
        user = db.get(User, setup[3])
        assert user.password_hash.startswith("$argon2id$")
        assert verify_password(user.password_hash, setup[2])
        assert db.get(AuthSession, digest(old)) is not None
        assert old not in str(db.execute(select(AuthSession.token_hash)).all())
    rotated = await login(client, setup[2])
    assert client.cookies.get(setup[4].session_cookie) != old
    with setup[1]() as db:
        assert db.get(AuthSession, digest(old)).revoked_at is not None
    assert (await client.get("/api/v1/auth/me")).status_code == 200
    assert (await client.post("/api/v1/auth/logout", headers=headers(rotated))).status_code == 204
    assert (await client.get("/api/v1/auth/me")).status_code == 401


async def test_login_csrf_origin_and_missing_token(client, setup):
    body = {"username": "admin", "password": setup[2]}
    assert (await client.post("/api/v1/auth/login", json=body)).status_code == 403
    csrf = (await client.get("/api/v1/auth/csrf")).json()["csrf_token"]
    response = await client.post(
        "/api/v1/auth/login",
        json=body,
        headers={"Origin": "https://evil.invalid", "X-CSRF-Token": csrf},
    )
    assert response.json()["code"] == "CSRF_FAILED"
    assert (await client.get("/api/v1/auth/me")).status_code == 401


async def test_failures_do_not_enumerate_users(client, setup):
    known = await login(client, "incorrect-password")
    unknown = await login(client, "incorrect-password", "not-present")
    assert known.status_code == unknown.status_code == 401
    assert known.json()["code"] == unknown.json()["code"] == "INVALID_CREDENTIALS"
    assert not known.json()["field_errors"] and not unknown.json()["field_errors"]
    auth = headers(await login(client, setup[2]))
    user, password = await create_user(client, auth)
    await client.patch(
        f"/api/v1/users/{user['id']}", headers=auth, json=update_body(user, active=False)
    )
    disabled = await login(client, password, "reader")
    assert disabled.status_code == 401 and disabled.json()["code"] == "INVALID_CREDENTIALS"


@pytest.mark.parametrize("role", ["VIEWER", "OPERATOR"])
async def test_rbac_is_enforced_for_direct_requests(role, client, setup):
    auth = headers(await login(client, setup[2]))
    user, password = await create_user(client, auth, role=role)
    actor = await login(client, password, "reader")
    for path in ["/api/v1/users", "/api/v1/audit"]:
        assert (await client.get(path)).status_code == 403
    response = await client.post(
        "/api/v1/users",
        headers=headers(actor),
        json={
            "username": "intruder",
            "display_name": "intruder",
            "password": secrets.token_urlsafe(24),
        },
    )
    assert response.status_code == 403
    assert (
        await client.patch(
            f"/api/v1/users/{user['id']}",
            headers=headers(actor),
            json=update_body(user, role="ADMINISTRATOR"),
        )
    ).status_code == 403
    assert (await client.get("/api/v1/auth/me")).status_code == 200


async def test_csrf_for_mutations_and_session_binding(client, setup):
    auth = headers(await login(client, setup[2]))
    assert (await client.post("/api/v1/auth/logout")).status_code == 403
    assert (
        await client.post("/api/v1/auth/logout", headers={**auth, "X-CSRF-Token": "0" * 64})
    ).status_code == 403
    assert (
        await client.post("/api/v1/auth/logout", headers={**auth, "Origin": "https://evil.invalid"})
    ).status_code == 403
    await login(client, setup[2])
    assert (await client.post("/api/v1/auth/logout", headers=auth)).status_code == 403


async def test_expired_sessions_are_rejected(client, setup):
    await login(client, setup[2])
    with setup[1]() as db:
        session = db.get(AuthSession, digest(client.cookies.get(setup[4].session_cookie)))
        session.expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    assert (await client.get("/api/v1/auth/me")).status_code == 401


@pytest.mark.parametrize("change", [{"role": "OPERATOR"}, {"active": False}])
async def test_last_administrator_is_protected(change, client, setup):
    result = await login(client, setup[2])
    user = result.json()["user"]
    response = await client.patch(
        f"/api/v1/users/{user['id']}", headers=headers(result), json=update_body(user, **change)
    )
    assert response.status_code == 409 and response.json()["code"] == "LAST_ADMINISTRATOR"
    assert (await client.get("/api/v1/auth/me")).status_code == 200


@pytest.mark.parametrize(
    "change", [{"active": False}, {"role": "OPERATOR"}, {"password": secrets.token_urlsafe(24)}]
)
async def test_sensitive_changes_revoke_sessions(change, client, setup):
    auth = headers(await login(client, setup[2]))
    user, password = await create_user(client, auth)
    async with AsyncClient(transport=ASGITransport(setup[0]), base_url=ORIGIN) as other:
        await login(other, password, "reader")
        response = await client.patch(
            f"/api/v1/users/{user['id']}", headers=auth, json=update_body(user, **change)
        )
        assert response.status_code == 200
        assert (await other.get("/api/v1/auth/me")).status_code == 401


async def test_password_change_requires_current_password_and_revokes(client, setup):
    auth = headers(await login(client, setup[2]))
    new = secrets.token_urlsafe(24)
    failed = await client.post(
        "/api/v1/auth/password",
        headers=auth,
        json={"current_password": "wrong", "new_password": new},
    )
    assert failed.json()["code"] == "CURRENT_PASSWORD_INVALID"
    assert (
        await client.post(
            "/api/v1/auth/password",
            headers=auth,
            json={"current_password": setup[2], "new_password": new},
        )
    ).status_code == 204
    assert (await client.get("/api/v1/auth/me")).status_code == 401
    assert (await login(client, setup[2])).status_code == 401
    assert (await login(client, new)).status_code == 200


async def test_safe_validation_duplicate_names_and_version_conflict(client, setup):
    auth = headers(await login(client, setup[2]))
    user, password = await create_user(client, auth)
    bad = await client.post("/api/v1/users", headers=auth, json={"password": "secret"})
    assert bad.status_code == 422 and "secret" not in bad.text
    assert {f["field"] for f in bad.json()["field_errors"]} == {
        "username",
        "display_name",
        "password",
    }
    duplicate = await client.post(
        "/api/v1/users",
        headers=auth,
        json={
            "username": " READER ",
            "display_name": "reader",
            "password": password,
        },
    )
    assert duplicate.status_code == 409 and duplicate.json()["code"] == "USERNAME_TAKEN"
    assert (
        await client.patch(
            f"/api/v1/users/{user['id']}",
            headers=auth,
            json=update_body(user, display_name="Новое имя"),
        )
    ).status_code == 200
    response = await client.patch(
        f"/api/v1/users/{user['id']}", headers=auth, json=update_body(user)
    )
    assert response.status_code == 409 and response.json()["code"] == "VERSION_CONFLICT"


async def test_audit_is_safe_filtered_and_transactional(client, setup):
    auth = headers(await login(client, setup[2]))
    user, password = await create_user(client, auth)
    assert password not in json.dumps((await client.get("/api/v1/users")).json())
    response = await client.get("/api/v1/audit?action=USER_CREATED&limit=1")
    assert response.json()["total"] == 1
    entry = response.json()["items"][0]
    assert entry["action"] == "USER_CREATED"
    assert "password" not in json.dumps(entry)
    assert set(entry["after"]) == {"username", "display_name", "role", "active"}
    assert safe_snapshot({"password": password, "token": "secret", "active": True}) == {
        "active": True
    }
    assert (await client.get("/api/v1/audit?limit=1000")).status_code == 422
    with setup[1]() as db:
        before_count = len(db.scalars(select(User)).all())

    def fail_audit(_mapper, _connection, _target):
        raise SQLAlchemyError("password=database-secret")

    event.listen(AuditEntry, "before_insert", fail_audit)
    try:
        result = await client.post(
            "/api/v1/users",
            headers=auth,
            json={
                "username": "rollback",
                "display_name": "rollback",
                "password": secrets.token_urlsafe(24),
            },
        )
        assert result.status_code == 503 and "database-secret" not in result.text
    finally:
        event.remove(AuditEntry, "before_insert", fail_audit)
    with setup[1]() as db:
        assert len(db.scalars(select(User)).all()) == before_count


async def test_persistent_login_limit_and_untrusted_forwarded_ip(client, setup):
    setup[4].login_account_limit = 2
    setup[4].login_ip_limit = 3
    for _ in range(2):
        assert (await login(client, "wrong-password")).status_code == 401
    assert (await login(client, "wrong-password")).status_code == 429
    restarted = create_app(setup[4], setup[1])
    async with AsyncClient(
        transport=ASGITransport(restarted),
        base_url=ORIGIN,
        headers={"X-Real-IP": "192.0.2.5", "X-Forwarded-For": "192.0.2.5"},
    ) as other:
        assert (await login(other, "wrong-password", "different")).status_code == 429


async def test_role_matrix_bootstrap_and_secure_configuration(setup):
    assert permitted(UserRole.OPERATOR, Permission.ACKNOWLEDGE_EVENTS)
    assert not permitted(UserRole.VIEWER, Permission.ACKNOWLEDGE_EVENTS)
    assert not permitted("UNKNOWN", Permission.MANAGE_USERS)
    assert not permitted(UserRole.OPERATOR, Permission.MANAGE_CONFIGURATION)
    with setup[1]() as db, pytest.raises(RuntimeError, match="BOOTSTRAP_ALREADY_COMPLETED"):
        create_first_admin(
            db, UserCreate(username="another", display_name="another", password=setup[2])
        )
    with pytest.raises(ValidationError):
        Settings(allowed_origins=["http://example.com"], allow_insecure_local_http=True)
    with pytest.raises(ValidationError):
        Settings(allowed_origins=["http://localhost"])


async def test_membership_team_filter(client, setup):
    auth = headers(await login(client, setup[2]))
    user, password = await create_user(client, auth)
    from uuid import UUID

    with setup[1]() as db:
        team = Team(id=uuid4(), name="other")
        db.add(team)
        db.flush()
        member = db.get(Membership, (UUID(user["id"]), DEFAULT_TEAM_ID))
        member.team_id = team.id
        db.commit()
    assert (await login(client, password, "reader")).status_code == 401
    assert (
        await client.patch(f"/api/v1/users/{user['id']}", headers=auth, json=update_body(user))
    ).status_code == 404


@pytest.mark.skipif(not os.environ.get("HUB_TEST_POSTGRES_PORT"), reason="PostgreSQL concurrency")
@pytest.mark.parametrize("disable_other", [False, True])
async def test_concurrent_admin_changes_preserve_authorization(setup, client, disable_other):
    first = await login(client, setup[2])
    second_user, password = await create_user(client, headers(first), "second", "ADMINISTRATOR")
    first_user = first.json()["user"]
    async with AsyncClient(transport=ASGITransport(setup[0]), base_url=ORIGIN) as other:
        second = await login(other, password, "second")
        targets = [second_user, first_user] if disable_other else [first_user, second_user]
        change = {"active": False} if disable_other else {"role": "VIEWER"}
        responses = await asyncio.gather(
            *[
                connection.patch(
                    f"/api/v1/users/{target['id']}",
                    headers=headers(auth),
                    json=update_body(target, **change),
                )
                for connection, auth, target in zip(
                    [client, other], [first, second], targets, strict=True
                )
            ]
        )
        assert sorted(r.status_code for r in responses) == (
            [200, 401] if disable_other else [200, 409]
        )
    with setup[1]() as db:
        admins = db.scalars(
            select(User)
            .join(Membership)
            .where(User.active.is_(True), Membership.role == "ADMINISTRATOR")
        ).all()
        assert len(admins) == 1


@pytest.mark.skipif(not os.environ.get("HUB_TEST_POSTGRES_PORT"), reason="PostgreSQL concurrency")
async def test_concurrent_login_reservations_cannot_exceed_limit(setup):
    setup[4].login_account_limit = 5

    async def attempt():
        async with AsyncClient(transport=ASGITransport(setup[0]), base_url=ORIGIN) as connection:
            return (await login(connection, "incorrect-password")).status_code

    results = await asyncio.gather(*(attempt() for _ in range(20)))
    assert results.count(401) == 5
    assert results.count(429) == 15
