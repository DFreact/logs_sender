from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event as sql_event
from sqlalchemy import func, select
from test_identity import ORIGIN, create_user, headers, login
from test_sources import condition, configured, receipt

from app.api.routing_schemas import RoutingInput
from app.domain.rules.routing import plan
from app.persistence.database import Base
from app.persistence.models import (
    DEFAULT_TEAM_ID,
    Event,
    EventOccurrence,
    RoutingRule,
    RuleExecution,
    RuleVersion,
    WorkItem,
)
from app.services.routing import apply_routing
from app.workers.execution import execute
from app.workers.queue import clock


def action(kind="SET_FIELDS", **values):
    return {"kind": kind, "scope": "EVENT", **values}


def pure_rule(name, conditions, actions):
    return dict(
        id=str(uuid4()),
        version_id=str(uuid4()),
        version=1,
        name=name,
        conditions=conditions,
        actions=actions,
    )


def save_rule(factory, actor, *, conditions=None, actions=None, priority=10):
    body = RoutingInput(
        name="Обработка",
        priority=priority,
        conditions=conditions or condition(),
        actions=actions or [action(fields={"severity": "CRITICAL"})],
    )
    with factory.begin() as db:
        row = RoutingRule(team_id=DEFAULT_TEAM_ID, **body.model_dump(mode="json"))
        db.add(row)
        db.flush()
        db.add(
            RuleVersion(
                routing_rule_id=row.id,
                version=1,
                actor_id=actor,
                snapshot=body.model_dump(mode="json"),
            )
        )
        return row.id


def test_snapshot_matching_priority_union_suppression_and_no_input_mutation():
    data = {"severity": "WARNING", "sender": "backup", "tags": ["исходная"]}
    rules = [
        pure_rule(
            "Первое",
            condition("severity", "equals", "WARNING"),
            [action(fields={"severity": "CRITICAL", "tags": ["первая"]}), action("NOTIFY")],
        ),
        pure_rule(
            "Второе",
            condition("severity", "equals", "WARNING"),
            [
                action(fields={"severity": "INFO", "category": "Безопасность", "tags": ["вторая"]}),
                action("SUPPRESS"),
            ],
        ),
        pure_rule("Не совпало", condition("severity", "equals", "CRITICAL"), [action("ESCALATE")]),
    ]
    original = deepcopy((data, rules))
    result = plan(rules, data)
    assert result["data"]["severity"] == "CRITICAL"
    assert result["data"]["tags"] == ["вторая", "исходная", "первая"]
    assert result["data"]["category"] == "Безопасность"
    assert [d["outcome"] for d in result["decisions"]] == [
        "APPLIED",
        "SUPPRESSED",
        "APPLIED",
        "APPLIED",
    ]
    assert result["decisions"][2]["overridden"] == ["severity"]
    assert result["suppressed"] and (data, rules) == original
    repeated = plan(rules, data, repeated=True)
    assert all(d["outcome"] == "REPEAT" for d in repeated["decisions"])


def test_live_processing_is_atomic_idempotent_and_preserves_repeat_fields(background):
    factory, store, settings, actor = background
    configured(factory, actor)
    save_rule(
        factory,
        actor,
        actions=[
            action(fields={"severity": "CRITICAL", "tags": ["правило"]}),
            action("NOTIFY"),
            action("SUPPRESS"),
        ],
    )
    _, task = receipt(factory, store)
    execute(factory, store, settings, str(task))
    with factory() as db:
        row = db.scalar(select(Event))
        assert row.status == "SUPPRESSED" and row.severity == "CRITICAL"
        assert row.tags == ["правило", "проверка"]
        occurrence = db.scalar(select(EventOccurrence))
        assert occurrence.normalized_snapshot["severity"] == "WARNING"
        assert db.scalar(select(func.count()).select_from(RuleExecution)) == 3
    execute(factory, store, settings, str(task))
    _, repeat_task = receipt(factory, store)
    execute(factory, store, settings, str(repeat_task))
    with factory.begin() as db:
        row = db.scalar(select(Event))
        assert row.occurrence_count == 2 and row.severity == "CRITICAL"
        assert row.status == "SUPPRESSED"
        assert db.scalar(select(func.count()).select_from(RuleExecution)) == 6
        original = db.scalar(select(EventOccurrence).order_by(EventOccurrence.received_at))
        apply_routing(db, row, original, {"sender": "backup", "tags": []})
        assert db.scalar(select(func.count()).select_from(RuleExecution)) == 6


@pytest.mark.anyio
async def test_simulation_uses_live_rules_and_dedup_without_any_write(setup, background):
    app, factory, password, actor, _ = setup
    _, store, settings, _ = background
    source_id, _, _ = configured(factory, actor)
    save_rule(factory, actor, actions=[action(fields={"severity": "CRITICAL"}), action("NOTIFY")])
    async with AsyncClient(transport=ASGITransport(app), base_url=ORIGIN) as client:
        auth = headers(await login(client, password))
        sample = dict(sender="backup@example.org", subject="Disk", body="Disk 123 full")
        before = (await client.post("/api/v1/rules/simulate", headers=auth, json=sample)).json()
        assert before["result"]["severity"] == "CRITICAL"
        assert before["identification"]["version"] == 1
        assert before["deduplication"]["status"] == "NEW"
        assert before["decisions"][1]["outcome"] == "PLANNED"
        assert before["retention"]["status"] == "DISABLED"
        _, task = receipt(factory, store)
        execute(factory, store, settings, str(task))
        with factory() as db:
            counts = {
                name: db.scalar(select(func.count()).select_from(table))
                for name, table in Base.metadata.tables.items()
            }
        statements = []

        def capture(connection, cursor, statement, parameters, context, executemany):
            statements.append(statement.lstrip().upper())

        sql_event.listen(factory.kw["bind"], "before_cursor_execute", capture)
        try:
            response = await client.post("/api/v1/rules/simulate", headers=auth, json=sample)
            assert response.status_code == 200, response.text
        finally:
            sql_event.remove(factory.kw["bind"], "before_cursor_execute", capture)
        result = response.json()
        assert result["deduplication"]["status"] == "REPEAT"
        assert result["result"]["severity"] == "CRITICAL"
        assert all(row["outcome"] == "REPEAT" for row in result["decisions"])
        assert not any(s.startswith(("INSERT", "UPDATE", "DELETE")) for s in statements)
        with factory() as db:
            assert counts == {
                name: db.scalar(select(func.count()).select_from(table))
                for name, table in Base.metadata.tables.items()
            }
        manual = await client.post(
            "/api/v1/rules/simulate", headers=auth, json={**sample, "source_id": str(source_id)}
        )
        assert manual.json()["manual_source"] and manual.json()["identification"] is None
        history = (
            await client.get(f"/api/v1/events/{result['deduplication']['event_id']}/processing")
        ).json()
        assert history["total"] == 2


@pytest.mark.anyio
async def test_routing_api_history_validation_version_conflicts_and_permissions(setup):
    app, _, password, _, _ = setup
    body = dict(name="Правило", conditions=condition(), actions=[action("SUPPRESS")])
    async with AsyncClient(transport=ASGITransport(app), base_url=ORIGIN) as client:
        assert (await client.post("/api/v1/rules/simulate", json={})).status_code == 401
        auth = headers(await login(client, password))
        assert (await client.post("/api/v1/rules/validate", headers=auth, json=body)).json() == {
            "valid": True
        }
        created = await client.post("/api/v1/routing-rules", headers=auth, json=body)
        assert created.status_code == 201, created.text
        row = created.json()
        path = f"/api/v1/routing-rules/{row['id']}"
        update = {**body, "description": "Изменено", "priority": 5, "version": 1}
        assert (await client.patch(path, headers=auth, json=update)).json()["version"] == 2
        assert (await client.patch(path, headers=auth, json=update)).status_code == 409
        history = (await client.get(path + "/versions")).json()
        assert history["total"] == 2
        assert [r["snapshot"]["priority"] for r in history["items"]] == [5, 100]
        assert all(r["author"] == "Администратор" for r in history["items"])
        invalid = [
            {**body, "conditions": condition("source", "equals", str(uuid4()))},
            {**body, "conditions": condition("severity", "contains", "ERROR")},
            {**body, "actions": [action("NOTIFY", target_id=str(uuid4()))]},
            {**body, "actions": [action("NOTIFY", mode="DELAYED")]},
            {
                **body,
                "actions": [action("NOTIFY", mode="SCHEDULED", scheduled_at="2026-10-01T00:00:00")],
            },
            {**body, "actions": [action(fields={})]},
        ]
        for bad in invalid:
            response = await client.post("/api/v1/routing-rules", headers=auth, json=bad)
            assert response.status_code in (404, 422), response.text
        for role in ("OPERATOR", "VIEWER"):
            user, user_password = await create_user(client, auth, username=role.lower(), role=role)
            async with AsyncClient(transport=ASGITransport(app), base_url=ORIGIN) as limited:
                token = headers(await login(limited, user_password, user["username"]))
                assert (await limited.get("/api/v1/routing-rules")).status_code == 403
                assert (
                    await limited.post("/api/v1/rules/simulate", headers=token, json={})
                ).status_code == 403


def test_occurrence_actions_apply_to_repeats_and_do_not_change_matching_snapshot():
    rules = [
        pure_rule("Повторы", condition(), [action(fields={"tags": ["новая"]}, scope="OCCURRENCE")])
    ]
    result = plan(
        rules,
        {"sender": "backup"},
        repeated=True,
        current={"sender": "backup", "tags": ["ранее"], "severity": "ERROR"},
    )
    assert result["data"]["tags"] == ["новая", "ранее"]
    assert result["data"]["severity"] == "ERROR"
    assert result["decisions"][0]["outcome"] == "APPLIED"


def test_failed_routing_rolls_back_event_occurrence_and_decisions(background, monkeypatch):
    from app.services import normalization

    factory, store, settings, actor = background
    save_rule(factory, actor)
    original = normalization.apply_routing

    def fail_after_decisions(*args):
        original(*args)
        raise RuntimeError("INJECTED_FAILURE")

    _, task = receipt(factory, store)
    monkeypatch.setattr(normalization, "apply_routing", fail_after_decisions)
    execute(factory, store, settings, str(task))
    with factory.begin() as db:
        for model in (Event, EventOccurrence, RuleExecution):
            assert db.scalar(select(func.count()).select_from(model)) == 0
        work = db.get(WorkItem, task)
        assert work.state == "PENDING"
        work.due_at = clock(db)
    monkeypatch.setattr(normalization, "apply_routing", original)
    execute(factory, store, settings, str(task))
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Event)) == 1
        assert db.scalar(select(func.count()).select_from(RuleExecution)) == 1
        assert db.get(WorkItem, task).state == "SUCCEEDED"


def test_postgres_concurrent_routing_decisions_once_per_occurrence(background):
    factory, store, settings, actor = background
    if factory.kw["bind"].dialect.name != "postgresql":
        pytest.skip("PostgreSQL concurrency")
    configured(factory, actor)
    save_rule(
        factory,
        actor,
        actions=[action(fields={"severity": "CRITICAL"}), action("NOTIFY", scope="OCCURRENCE")],
    )
    items = [receipt(factory, store) for _ in range(20)]
    with ThreadPoolExecutor(max_workers=20) as pool:
        list(pool.map(lambda item: execute(factory, store, settings, str(item[1])), items))
    with factory() as db:
        row = db.scalars(select(Event)).one()
        assert row.occurrence_count == 20 and row.severity == "CRITICAL"
        decisions = db.scalars(select(RuleExecution)).all()
        assert len(decisions) == 40
        assert sum(d.result["outcome"] == "APPLIED" for d in decisions) == 1
        assert sum(d.result["outcome"] == "PLANNED" for d in decisions) == 20
        assert sum(d.result["outcome"] == "REPEAT" for d in decisions) == 19
