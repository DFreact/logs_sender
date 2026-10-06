from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event as sql_event
from test_identity import ORIGIN, login

from app.api.cursors import decode_cursor, encode_cursor, scope_for
from app.api.errors import ApiFailure
from app.persistence.models import DEFAULT_TEAM_ID, Event, Source, Team, utcnow


def seed(factory, count=63):
    now = utcnow() - timedelta(minutes=1)
    with factory.begin() as db:
        source = Source(team_id=DEFAULT_TEAM_ID, name="Большой источник")
        db.add(source)
        db.flush()
        for i in range(count):
            db.add(
                Event(
                    id=UUID(int=i + 1000),
                    team_id=DEFAULT_TEAM_ID,
                    source_id=source.id,
                    input_adapter="REST",
                    subject=f"Событие {i}",
                    body="x" * 65536,
                    received_at=now,
                    first_seen_at=now,
                    last_seen_at=now,
                )
            )
    return now


@pytest.mark.anyio
async def test_feed_ties_insertions_filters_and_bounded_queries(setup):
    app, factory, password, _, _ = setup
    now = seed(factory)
    statements = []
    engine = factory.kw["bind"]

    def capture(_, __, statement, *args):
        statements.append(statement)

    async with AsyncClient(transport=ASGITransport(app=app), base_url=ORIGIN) as client:
        await login(client, password)
        sql_event.listen(engine, "before_cursor_execute", capture)
        try:
            first = (await client.get("/api/v1/events?limit=25")).json()
        finally:
            sql_event.remove(engine, "before_cursor_execute", capture)
        assert len(first["items"]) == 25 and first["next_cursor"] and "total" not in first
        selects = [s for s in statements if "FROM events" in s]
        assert len(selects) == 1
        assert "count(" not in selects[0].lower() and "events.body" not in selects[0]
        assert all(item["source_name"] == "Большой источник" for item in first["items"])
        with factory.begin() as db:
            new = utcnow()
            db.add(
                Event(
                    team_id=DEFAULT_TEAM_ID,
                    input_adapter="REST",
                    subject="После открытия",
                    received_at=new,
                    first_seen_at=new,
                    last_seen_at=new,
                )
            )
            other = Team(id=uuid4(), name="Другая команда")
            db.add(other)
            db.flush()
            db.add(
                Event(
                    team_id=other.id,
                    input_adapter="REST",
                    subject="Чужое",
                    received_at=now,
                    first_seen_at=now,
                    last_seen_at=now,
                )
            )
        seen = [v["id"] for v in first["items"]]
        cursor = first["next_cursor"]
        while cursor:
            page = (await client.get("/api/v1/events", params={"cursor": cursor})).json()
            seen += [v["id"] for v in page["items"]]
            cursor = page["next_cursor"]
        assert len(seen) == len(set(seen)) == 63
        assert (
            await client.get(
                "/api/v1/events", params={"cursor": first["next_cursor"], "status": "RESOLVED"}
            )
        ).json()["code"] == "CURSOR_INVALID"
        assert (await client.get("/api/v1/events?offset=10000")).status_code == 422
        assert (await client.get("/api/v1/events?q=После")).json()["items"][0][
            "subject"
        ] == "После открытия"


def test_cursor_is_signed_scoped_and_expires(setup):
    settings = setup[-1]
    now, identifier = utcnow(), uuid4()
    scope = scope_for(DEFAULT_TEAM_ID, ["filter"])
    token = encode_cursor(settings, scope, now, (now, identifier))
    assert decode_cursor(settings, token, scope)[1] == (now, identifier)
    for value, bound in [
        (token + "x", scope),
        (token, "other"),
        (encode_cursor(settings, scope, now, (now, identifier), 1), scope),
        ("bad", scope),
    ]:
        with pytest.raises(ApiFailure):
            decode_cursor(settings, value, bound)


def test_shared_adapter_lock_does_not_serialize_receipts(background):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event as Signal

    from app.services.ingestion import adapter

    factory, _, _, _ = background
    if factory.kw["bind"].dialect.name != "postgresql":
        pytest.skip("PostgreSQL locking semantics")
    with factory.begin() as db:
        adapter(db, "SMTP", DEFAULT_TEAM_ID)
    ready = Signal()

    def another():
        with factory.begin() as db:
            adapter(db, "SMTP", DEFAULT_TEAM_ID)
            ready.set()

    with ThreadPoolExecutor(1) as pool:
        with factory.begin() as db:
            adapter(db, "SMTP", DEFAULT_TEAM_ID)
            future = pool.submit(another)
            assert ready.wait(3), "independent receipt blocked on shared adapter"
        future.result(timeout=3)


@pytest.mark.anyio
async def test_slow_search_returns_safe_recovery(setup, monkeypatch):
    from sqlalchemy import func, select

    from app.api import events

    app, factory, password, _, settings = setup
    if factory.kw["bind"].dialect.name != "postgresql":
        pytest.skip("PostgreSQL statement timeout")
    settings.event_query_timeout_ms = 100
    monkeypatch.setattr(events, "event_page_query", lambda *_: select(func.pg_sleep(0.3)))
    async with AsyncClient(transport=ASGITransport(app=app), base_url=ORIGIN) as client:
        await login(client, password)
        response = await client.get("/api/v1/events")
        assert response.status_code == 422
        assert response.json()["code"] == "QUERY_TOO_BROAD"
        assert "pg_sleep" not in response.text
