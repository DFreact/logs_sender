from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from test_identity import ORIGIN, create_user, headers, login

from app.api.config_schemas import DEFAULT_FIELDS
from app.domain.rules.conditions import evaluate, validate
from app.persistence.models import (
    DEFAULT_TEAM_ID,
    DedupPolicy,
    Event,
    EventOccurrence,
    IdentificationRule,
    RawMessage,
    RuleVersion,
    Source,
    WorkItem,
)
from app.services.ingestion import receive
from app.workers.execution import execute
from app.workers.queue import clock, utc


def condition(field="sender", operator="contains", value="backup", **extra):
    return dict(field=field, operator=operator, value=value, **extra)


def configured(factory, actor_id, *, enabled=True):
    with factory.begin() as db:
        source = Source(team_id=DEFAULT_TEAM_ID, name="Резервное копирование")
        db.add(source)
        db.flush()
        rule = IdentificationRule(
            team_id=DEFAULT_TEAM_ID,
            source_id=source.id,
            name="По отправителю",
            priority=10,
            conditions=condition(),
            assignments={
                "severity": "WARNING",
                "category": "Инфраструктура",
                "event_type": "backup",
                "tags": ["проверка"],
            },
        )
        db.add(rule)
        db.flush()
        version = RuleVersion(
            rule_id=rule.id,
            version=1,
            actor_id=actor_id,
            snapshot={"name": rule.name, "conditions": rule.conditions},
        )
        policy = DedupPolicy(
            team_id=DEFAULT_TEAM_ID,
            scope=str(source.id),
            inherit=False,
            enabled=enabled,
            window_seconds=600,
            fields=DEFAULT_FIELDS,
            version=1,
        )
        db.add_all([version, policy])
        db.flush()
        return source.id, rule.id, policy.id


def receipt(
    factory, store, *, sender="backup@example.org", body="Disk 123 full", seconds=0, start=None
):
    raw_id = receive(
        factory,
        store,
        f"From: {sender}\r\nSubject: Disk\r\n\r\n{body}".encode(),
        "message/rfc822",
        kind="SMTP",
    )
    with factory.begin() as db:
        row = db.get(RawMessage, raw_id)
        row.received_at = (start or clock(db)) + timedelta(seconds=seconds)
        task_id = db.scalar(select(WorkItem.id).where(WorkItem.entity_id == raw_id))
    return raw_id, task_id


def test_conditions_groups_missing_case_lists_and_safe_regex():
    huge_number = 10**400
    numeric = validate(
        condition(field="metadata", selector="count", operator="gte", value=huge_number)
    )
    assert evaluate(numeric, {"metadata": {"count": huge_number + 1}}) is True
    assert evaluate(numeric, {"metadata": {"count": float("inf")}}) is None
    assert evaluate(numeric, {"metadata": {"count": True}}) is None
    assert evaluate(validate(condition()), {"sender": "BACKUP@example"}) is True
    assert evaluate(condition(case_sensitive=True), {"sender": "BACKUP"}) is False
    assert evaluate(condition(operator="not_contains"), {}) is None
    assert evaluate({"group": "NOT", "children": [condition()]}, {}) is None
    assert (
        evaluate({"group": "NOT", "children": [{"field": "sender", "operator": "exists"}]}, {})
        is True
    )
    assert (
        evaluate(
            condition(field="recipient", operator="not_equals", value="x"),
            {"recipient": ["y", "x"]},
        )
        is False
    )
    assert (
        evaluate(
            condition(field="header", selector="x-system"), {"header": [["X-System", "BACKUP"]]}
        )
        is True
    )
    assert (
        evaluate(
            condition(field="metadata", selector="count", operator="gt", value=3),
            {"metadata": {"count": 4}},
        )
        is True
    )
    assert (
        evaluate(
            condition(field="metadata", selector="count", operator="gt", value=3),
            {"metadata": {"count": "4"}},
        )
        is None
    )
    assert (
        evaluate(
            validate(condition(operator="regex", value="(a+)+$")), {"sender": "a" * 65000 + "!"}
        )
        is False
    )
    for tree in [
        condition(operator="regex", value="(?<=secret)x"),
        {"group": "AND", "children": []},
        {"group": "AND", "children": [condition()] * 101},
        condition(field="sql"),
    ]:
        with pytest.raises(ValueError):
            validate(tree)


@pytest.mark.anyio
async def test_source_rule_api_versions_conflicts_and_roles(setup):
    app, factory, password, _, _ = setup
    async with AsyncClient(transport=ASGITransport(app), base_url=ORIGIN) as client:
        assert (await client.get("/api/v1/sources")).status_code == 401
        auth = headers(await login(client, password))
        response = await client.post(
            "/api/v1/sources", headers=auth, json={"name": "Почтовый сервер"}
        )
        assert response.status_code == 201, response.text
        source = response.json()
        assert source["dedup"]["inherit"] is True
        assert (
            await client.post("/api/v1/sources", headers=auth, json={"name": source["name"]})
        ).status_code == 409
        body = {
            "name": "Почтовые ошибки",
            "source_id": source["id"],
            "conditions": condition(field="subject", value="ошибка"),
            "priority": 10,
        }
        response = await client.post("/api/v1/identification-rules", headers=auth, json=body)
        assert response.status_code == 201, response.text
        rule = response.json()
        identifier = rule.pop("id")
        rule["priority"] = 5
        response = await client.patch(
            f"/api/v1/identification-rules/{identifier}", headers=auth, json=rule
        )
        assert response.status_code == 200 and response.json()["version"] == 2
        assert (
            await client.patch(
                f"/api/v1/identification-rules/{identifier}", headers=auth, json=rule
            )
        ).status_code == 409
        history = (await client.get(f"/api/v1/identification-rules/{identifier}/versions")).json()
        assert history["total"] == 2
        assert [v["snapshot"]["priority"] for v in history["items"]] == [5, 10]
        assert all(v["author"] == "Администратор" for v in history["items"])
        audit = (await client.get("/api/v1/audit")).json()["items"]
        assert any(
            row["action"] == "SOURCE_CREATED" and row["target"] == source["name"] for row in audit
        )
        assert any(
            row["action"] == "IDENTIFICATION_RULE_UPDATED" and row["target"] == body["name"]
            for row in audit
        )
        assert (
            await client.post(
                "/api/v1/identification-rules",
                headers=auth,
                json={**body, "conditions": condition(operator="regex", value="(")},
            )
        ).status_code == 422
        assert (
            await client.post(
                "/api/v1/identification-rules",
                headers=auth,
                json={**body, "source_id": str(uuid4())},
            )
        ).status_code == 404
        policy = (await client.get("/api/v1/dedup-policy")).json()
        policy["enabled"] = True
        assert (
            await client.patch("/api/v1/dedup-policy", headers=auth, json=policy)
        ).status_code == 200
        assert (
            await client.patch("/api/v1/dedup-policy", headers=auth, json=policy)
        ).status_code == 409
        for role in ("OPERATOR", "VIEWER"):
            await login(client, password)
            user, pw = await create_user(
                client, headers(await login(client, password)), username=role.lower(), role=role
            )
            low = headers(await login(client, pw, user["username"]))
            assert (await client.get("/api/v1/sources")).status_code == 200
            for path, payload in [
                ("/api/v1/sources", {"name": "Запрещено"}),
                ("/api/v1/identification-rules", body),
            ]:
                assert (await client.post(path, headers=low, json=payload)).status_code == 403
            assert (await client.get("/api/v1/identification-rules")).status_code == 403
            assert (
                await client.patch("/api/v1/dedup-policy", headers=low, json=policy)
            ).status_code == 403


def test_fixed_window_late_receipts_policy_change_and_status(background):
    factory, store, settings, user_id = background
    source_id, _, policy_id = configured(factory, user_id)
    with factory() as db:
        start = clock(db)
    first, task = receipt(factory, store, start=start, seconds=20)
    execute(factory, store, settings, str(task))
    with factory.begin() as db:
        event = db.scalar(select(Event))
        event.status = "ACKNOWLEDGED"
    for seconds in (0, 599, 600):
        _, task = receipt(factory, store, start=start, seconds=seconds)
        execute(factory, store, settings, str(task))
        execute(factory, store, settings, str(task))
    with factory.begin() as db:
        events = db.scalars(select(Event).order_by(Event.first_seen_at)).all()
        assert len(events) == 2 and [e.occurrence_count for e in events] == [3, 1]
        assert events[0].status == "ACKNOWLEDGED"
        assert utc(events[0].first_seen_at) == start
        assert utc(events[0].last_seen_at) == start + timedelta(seconds=599)
        assert events[0].source_id == source_id and events[0].severity == "WARNING"
        assert db.scalar(select(func.count()).select_from(EventOccurrence)) == 4
        db.get(DedupPolicy, policy_id).version += 1
    _, task = receipt(factory, store, start=start, seconds=30)
    execute(factory, store, settings, str(task))
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Event)) == 3
        occurrence = db.scalar(
            select(EventOccurrence).where(EventOccurrence.raw_message_id == first)
        )
        assert (
            occurrence.rule_version_id and occurrence.normalized_snapshot["event_type"] == "backup"
        )


def test_unknown_disabled_source_rule_priority_and_disabled_dedup(background):
    factory, store, settings, user_id = background
    source_id, rule_id, policy_id = configured(factory, user_id, enabled=False)
    with factory.begin() as db:
        other = Source(team_id=DEFAULT_TEAM_ID, name="Приоритетный источник")
        db.add(other)
        db.flush()
        rule = IdentificationRule(
            team_id=DEFAULT_TEAM_ID,
            source_id=other.id,
            name="Первое",
            priority=1,
            conditions=condition(),
            assignments={},
        )
        db.add(rule)
        db.flush()
        db.add(
            RuleVersion(rule_id=rule.id, version=1, actor_id=user_id, snapshot={"name": "Первое"})
        )
        other_id = other.id
    for _ in range(2):
        _, task = receipt(factory, store)
        execute(factory, store, settings, str(task))
    with factory.begin() as db:
        events = db.scalars(select(Event)).all()
        assert len(events) == 2 and all(e.source_id == other_id for e in events)
        db.get(Source, other_id).enabled = False
        db.get(IdentificationRule, rule_id).enabled = False
    _, task = receipt(factory, store)
    execute(factory, store, settings, str(task))
    with factory() as db:
        assert (
            db.scalar(select(func.count()).select_from(Event).where(Event.source_id.is_(None))) == 1
        )


def test_twenty_concurrent_normalizations_make_one_event(background):
    factory, store, settings, user_id = background
    if factory.kw["bind"].dialect.name != "postgresql":
        pytest.skip("PostgreSQL concurrency")
    configured(factory, user_id)
    with factory() as db:
        start = clock(db)
    items = [receipt(factory, store, start=start, seconds=index) for index in range(20)]
    with ThreadPoolExecutor(max_workers=20) as pool:
        list(
            pool.map(lambda item: execute(factory, store, settings, str(item[1])), reversed(items))
        )
    with factory() as db:
        event = db.scalar(select(Event))
        assert event.occurrence_count == 20
        assert db.scalar(select(func.count()).select_from(Event)) == 1
        assert db.scalar(select(func.count()).select_from(EventOccurrence)) == 20
        assert (
            db.scalar(
                select(func.count()).select_from(WorkItem).where(WorkItem.state == "SUCCEEDED")
            )
            == 20
        )


def test_unknown_streams_inheritance_and_policy_versions(background):
    from app.security.tokens import digest
    from app.services.ingestion import issue_key

    factory, store, settings, user_id = background
    with factory.begin() as db:
        global_policy = DedupPolicy(
            team_id=DEFAULT_TEAM_ID,
            scope="GLOBAL",
            enabled=True,
            inherit=False,
            window_seconds=600,
            fields=DEFAULT_FIELDS,
            version=1,
        )
        db.add(global_policy)
        _, one = issue_key(db, "Один")
        _, two = issue_key(db, "Два")
    for token in (one, one, two):
        raw_id = receive(
            factory, store, b"body", "text/plain", kind="REST", token_hash=digest(token)
        )
        with factory() as db:
            task = db.scalar(select(WorkItem.id).where(WorkItem.entity_id == raw_id))
        execute(factory, store, settings, str(task))
    with factory() as db:
        assert sorted(db.scalars(select(Event.occurrence_count)).all()) == [1, 2]
    source_id, _, policy_id = configured(factory, user_id)
    with factory.begin() as db:
        db.get(DedupPolicy, policy_id).inherit = True
    for _ in range(2):
        _, task = receipt(factory, store)
        execute(factory, store, settings, str(task))
    with factory.begin() as db:
        assert db.scalar(select(Event.occurrence_count).where(Event.source_id == source_id)) == 2
        policy = db.get(DedupPolicy, policy_id)
        policy.inherit = False
        policy.version += 1
    _, task = receipt(factory, store)
    execute(factory, store, settings, str(task))
    with factory.begin() as db:
        policy = db.get(DedupPolicy, policy_id)
        policy.inherit = True
        policy.version += 1
    _, task = receipt(factory, store)
    execute(factory, store, settings, str(task))
    with factory() as db:
        assert sorted(
            db.scalars(select(Event.occurrence_count).where(Event.source_id == source_id)).all()
        ) == [1, 1, 2]


@pytest.mark.anyio
async def test_receipt_history_has_pages_and_rule_version(setup, background):
    app, factory, password, user_id, _ = setup
    _, store, settings, _ = background
    configured(factory, user_id)
    for _ in range(26):
        _, task = receipt(factory, store)
        execute(factory, store, settings, str(task))
    async with AsyncClient(transport=ASGITransport(app), base_url=ORIGIN) as client:
        await login(client, password)
        page = (await client.get("/api/v1/events")).json()
        assert len(page["items"]) == 1 and page["items"][0]["occurrence_count"] == 26
        event = page["items"][0]
        assert event["source_name"] == "Резервное копирование"
        detail = (await client.get(f"/api/v1/events/{event['id']}")).json()
        assert len(detail["occurrences"]) == 25
        assert all(item["rule_version"] == 1 for item in detail["occurrences"])
        last = (await client.get(f"/api/v1/events/{event['id']}?occurrence_offset=25")).json()
        assert len(last["occurrences"]) == 1
        assert last["occurrences"][0]["snapshot"]["body"] == "Disk 123 full"
