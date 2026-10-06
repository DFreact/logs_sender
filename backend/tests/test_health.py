from datetime import timedelta
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from test_identity import ORIGIN, create_user, headers, login

from app.api import health as health_api
from app.observability.health import heartbeat, infrastructure, snapshot
from app.persistence.models import ComponentHeartbeat
from app.workers.queue import clock, enqueue

pytestmark = pytest.mark.anyio


def healthy(_settings):
    return [dict(component=value, status="HEALTHY", code="OK") for value in ("BROKER", "STORAGE")]


async def test_health_rbac_and_public_readiness_do_not_reveal_details(setup, monkeypatch):
    app, factory, password, _, settings = setup
    monkeypatch.setattr(health_api, "infrastructure", healthy)
    async with AsyncClient(transport=ASGITransport(app), base_url=ORIGIN) as client:
        assert (await client.get("/api/v1/health")).status_code == 401
        unknown = await client.get("/health/ready")
        assert unknown.status_code == 503 and unknown.json() == {"status": "DEGRADED"}
        for component in ("WORKER", "DISPATCHER", "SCHEDULER", "SMTP", "INGESTION", "DELIVERY"):
            heartbeat(factory, component, uuid4())
        ready = await client.get("/health/ready")
        assert ready.status_code == 200 and ready.json() == {"status": "HEALTHY"}
        await login(client, password)
        response = await client.get("/api/v1/health")
        assert response.status_code == 200 and response.json()["status"] == "HEALTHY"
        assert len(response.json()["components"]) == 9
        for role in ("VIEWER", "OPERATOR"):
            await login(client, password)
            user, user_password = await create_user(
                client, headers(await login(client, password)), username=role.lower(), role=role
            )
            await login(client, user_password, user["username"])
            assert (await client.get("/api/v1/health")).status_code == 403


async def test_stale_component_and_backlog_are_visible(setup):
    _, factory, _, _, settings = setup
    for component in ("WORKER", "DISPATCHER", "SCHEDULER", "SMTP", "INGESTION", "DELIVERY"):
        heartbeat(factory, component, uuid4())
    with factory.begin() as db:
        worker = db.scalar(
            select(ComponentHeartbeat).where(ComponentHeartbeat.component == "WORKER")
        )
        worker.checked_at = clock(db) - timedelta(seconds=settings.heartbeat_stale_seconds + 1)
        enqueue(db, "TEST", uuid4(), "overdue", due_at=clock(db) - timedelta(seconds=120))
    with factory() as db:
        result = snapshot(db, settings, probe=healthy)
        assert result["status"] == "DEGRADED" and result["oldest_due_seconds"] >= 120
        assert result["queue"]["PENDING"] == 1
        worker = next(row for row in result["components"] if row["component"] == "WORKER")
        assert worker["status"] == "UNAVAILABLE" and worker["code"] == "STALE"


async def test_probe_failures_have_only_safe_codes(background, monkeypatch):
    _, _, settings, _ = background
    from app.observability import health

    def fail(_settings):
        raise RuntimeError("redis://user:secret-token@internal-server")

    monkeypatch.setattr(health, "redis_client", fail)
    result = infrastructure(settings)
    assert result == [
        dict(component="BROKER", status="UNAVAILABLE", code="UNREACHABLE"),
        dict(component="STORAGE", status="HEALTHY", code="OK"),
    ]
    settings.storage_min_free_bytes = 10**30
    assert infrastructure(settings)[1]["code"] == "LOW_SPACE"
