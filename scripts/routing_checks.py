"""Exercise the real SMTP process and worker only on the disposable test stack."""

import json
import smtplib
import time

PRELUDE = """
import json
from sqlalchemy import select
from app.settings import Settings
from app.persistence.database import build_engine, build_session_factory
from app.persistence.models import (
    DEFAULT_TEAM_ID, User, Source, IdentificationRule, RuleVersion, DedupPolicy,
    RoutingRule, Event, RuleExecution,
)
from app.api.routing_schemas import RoutingInput
engine = build_engine(Settings())
factory = build_session_factory(engine)
"""


def verify(run, smtp_port):
    def python(code):
        return run("exec", "-T", "api", "python", "-", input_text=PRELUDE + code)

    python("""
with factory.begin() as db:
    actor = db.scalar(select(User).where(User.username == "admin"))
    source = Source(team_id=DEFAULT_TEAM_ID, name="Проверка обработки SMTP")
    db.add(source)
    db.flush()
    conditions = {"field": "sender", "operator": "equals", "value": "routing-check@example.org"}
    identify = IdentificationRule(team_id=DEFAULT_TEAM_ID, source_id=source.id,
        name="Проверка SMTP", priority=0, conditions=conditions,
        assignments={"severity": None, "category": "", "event_type": "", "tags": []})
    db.add(identify)
    db.flush()
    db.add(RuleVersion(rule_id=identify.id, version=1, actor_id=actor.id,
        snapshot={"name": identify.name, "conditions": conditions,
                  "source_id": str(source.id), "priority": 0, "enabled": True,
                  "version": 1, "assignments": identify.assignments}))
    db.add(DedupPolicy(team_id=DEFAULT_TEAM_ID, scope=str(source.id), inherit=False,
        enabled=True, fields=["sender", "subject", "body"], window_seconds=600))
    config = RoutingInput(name="Обработка SMTP", conditions=conditions, actions=[
        {"kind": "SET_FIELDS", "fields": {"severity": "CRITICAL"}},
        {"kind": "NOTIFY"}, {"kind": "SUPPRESS"},
    ])
    rule = RoutingRule(team_id=DEFAULT_TEAM_ID, **config.model_dump(mode="json"))
    db.add(rule)
    db.flush()
    db.add(RuleVersion(routing_rule_id=rule.id, version=1, actor_id=actor.id,
        snapshot=config.model_dump(mode="json")))
""")
    for _ in range(2):
        with smtplib.SMTP("127.0.0.1", smtp_port, timeout=20) as client:
            assert (
                client.sendmail(
                    "routing-check@example.org",
                    ["events@localhost"],
                    b"From: routing-check@example.org\r\n"
                    b"Subject: Routing check\r\n\r\nDisk full\r\n",
                )
                == {}
            )
    for _ in range(50):
        result = json.loads(
            python("""
with factory() as db:
    rows = db.scalars(select(Event).where(Event.sender == "routing-check@example.org")).all()
    executions = db.scalars(select(RuleExecution).join(Event)
        .where(Event.sender == "routing-check@example.org")).all()
    print(json.dumps({"events": len(rows), "count": sum(row.occurrence_count for row in rows),
        "status": rows[0].status if rows else None,
        "severity": rows[0].severity if rows else None,
        "outcomes": sorted(row.result["outcome"] for row in executions)}))
""")
        )
        if result["count"] == 2:
            break
        time.sleep(1)
    assert result == {
        "events": 1,
        "count": 2,
        "status": "SUPPRESSED",
        "severity": "CRITICAL",
        "outcomes": sorted(
            ["APPLIED", "APPLIED", "SUPPRESSED", "REPEAT", "REPEAT", "REPEAT"]
        ),
    }
    print(
        "SMTP → определение источника → повторы → правила обработки: пройдено.",
        flush=True,
    )
