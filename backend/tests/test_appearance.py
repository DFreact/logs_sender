import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from test_identity import ORIGIN, create_user, headers, login

from app.persistence.models import AuditEntry

pytestmark = pytest.mark.anyio
PATH = "/api/v1/settings/appearance"


async def test_shared_appearance_persistence_conflict_audit_and_validation(setup):
    app, factory, password, _, _ = setup
    async with AsyncClient(transport=ASGITransport(app=app), base_url=ORIGIN) as client:
        initial = (await client.get(PATH)).json()
        assert initial == {"palette": "blue", "mode": "light", "version": 1}
        auth = headers(await login(client, password))
        changed = {"palette": "teal", "mode": "dark", "version": 1}
        assert (await client.patch(PATH, json=changed)).status_code == 403
        response = await client.patch(PATH, headers=auth, json=changed)
        assert response.status_code == 200
        assert response.json() == {**changed, "version": 2}
        stale = await client.patch(PATH, headers=auth, json={**initial, "palette": "wine"})
        assert stale.status_code == 409 and stale.json()["code"] == "VERSION_CONFLICT"
        for invalid in (
            {"palette": "url(https://example.org)"},
            {"mode": "AUTO"},
            {"version": True},
            {"secret": "hidden"},
        ):
            assert (
                await client.patch(PATH, headers=auth, json={**changed, "version": 2, **invalid})
            ).status_code == 422
        assert (await client.patch(PATH, headers=auth, json={**changed, "version": 2})).json()[
            "version"
        ] == 2
    async with AsyncClient(transport=ASGITransport(app=app), base_url=ORIGIN) as anonymous:
        assert (await anonymous.get(PATH)).json() == {**changed, "version": 2}
    with factory() as db:
        rows = db.scalars(select(AuditEntry).where(AuditEntry.action == "APPEARANCE_UPDATED")).all()
        assert len(rows) == 1
        assert rows[0].before == {"appearance_palette": "blue", "appearance_mode": "light"}
        assert rows[0].after == {"appearance_palette": "teal", "appearance_mode": "dark"}


@pytest.mark.parametrize("role", ["VIEWER", "OPERATOR"])
async def test_only_administrator_may_change_shared_appearance(setup, role):
    async with AsyncClient(transport=ASGITransport(app=setup[0]), base_url=ORIGIN) as client:
        admin = headers(await login(client, setup[2]))
        user, password = await create_user(client, admin, username="appearance-user", role=role)
        actor = headers(await login(client, password, user["username"]))
        before = (await client.get(PATH)).json()
        assert (
            await client.patch(PATH, headers=actor, json={**before, "palette": "wine"})
        ).status_code == 403
        assert (await client.get(PATH)).json() == before


def test_populated_appearance_migration_preserves_users(setup):
    from alembic import command
    from alembic.config import Config

    from app.persistence.models import DEFAULT_TEAM_ID, Membership, Team, User

    factory = setup[1]
    if factory.kw["bind"].dialect.name != "postgresql":
        pytest.skip("PostgreSQL populated migration")
    with factory() as db:
        original = db.get(User, setup[3])
        identity = (original.username, original.password_hash)
    config = Config("alembic.ini")
    with factory.kw["bind"].begin() as connection:
        config.attributes["connection"] = connection
        command.stamp(config, "0012")
        command.downgrade(config, "0010a")
        command.upgrade(config, "0012")
        command.check(config)
    with factory() as db:
        team = db.get(Team, DEFAULT_TEAM_ID)
        user = db.get(User, setup[3])
        assert (team.appearance_palette, team.appearance_mode, team.appearance_version) == (
            "blue",
            "light",
            1,
        )
        assert (user.username, user.password_hash) == identity
        assert db.get(Membership, (user.id, team.id)).role == "ADMINISTRATOR"
