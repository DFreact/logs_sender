"""Daily firewall summary through the real SMTP worker and a local TLS receiver."""

import json
import smtplib
import time
from email.message import EmailMessage

BASE = """
from datetime import timedelta
from sqlalchemy import select, func
from app.settings import Settings
from app.persistence.database import build_engine, build_session_factory
from app.persistence.models import *
from app.services.digests import advance, snapshot
from app.services.digests.schedule import next_boundary
from app.workers.queue import clock, utc

settings = Settings()
engine = build_engine(settings)
factory = build_session_factory(engine)
"""


def verify(run, smtp_port):
    run(
        "exec",
        "-T",
        "api",
        "python",
        "-",
        input_text=BASE
        + """
from app.api.digest_schemas import DigestInput
from app.api.config_schemas import DEFAULT_FIELDS

with factory.begin() as db:
    actor = db.scalar(select(User).where(User.username == "admin"))
    source = Source(team_id=DEFAULT_TEAM_ID, name="Межсетевой экран")
    db.add(source)
    db.flush()
    condition = {
        "field": "subject",
        "operator": "contains",
        "value": "digest-check",
        "case_sensitive": False,
    }
    rule = IdentificationRule(
        team_id=DEFAULT_TEAM_ID,
        source_id=source.id,
        name="События межсетевого экрана",
        priority=0,
        enabled=True,
        version=1,
        conditions=condition,
        assignments={
            "severity": "WARNING",
            "category": "Безопасность",
            "event_type": "Проверка",
            "tags": [],
        },
    )
    db.add(rule)
    db.flush()
    db.add(
        RuleVersion(
            rule_id=rule.id,
            version=1,
            actor_id=actor.id,
            snapshot={"name": rule.name, "conditions": condition},
        )
    )
    db.add(
        DedupPolicy(
            team_id=DEFAULT_TEAM_ID,
            scope=str(source.id),
            inherit=False,
            enabled=True,
            window_seconds=86400,
            fields=DEFAULT_FIELDS,
            version=1,
        )
    )
    template = Template(
        team_id=DEFAULT_TEAM_ID,
        name="Почтовая сводка",
        kind="SMTP",
        version=1,
        subject="{{ event.subject }}",
        body="{{ event.body }}",
        html="",
    )
    db.add(template)
    db.flush()
    db.add(
        TemplateVersion(
            template_id=template.id,
            version=1,
            actor_id=actor.id,
            snapshot={
                "kind": "SMTP",
                "subject": template.subject,
                "body": template.body,
                "html": "",
            },
        )
    )
    channel = NotificationChannel(
        team_id=DEFAULT_TEAM_ID,
        name="Локальная почта сводок",
        kind="SMTP",
        enabled=True,
        template_id=template.id,
        configuration={
            "kind": "SMTP",
            "host": "api.telegram.org",
            "port": 465,
            "tls": "TLS",
            "username": "",
            "sender": "hub@example.org",
            "recipients": ["security@example.org"],
        },
    )
    db.add(channel)
    db.flush()
    config = DigestInput(
        name="Ежедневная сводка межсетевого экрана",
        channel_id=channel.id,
        template_id=template.id,
        selection="RULE",
        source_ids=[source.id],
    ).model_dump(mode="json")
    now = clock(db)
    definition = DigestDefinition(
        team_id=DEFAULT_TEAM_ID,
        name=config.pop("name"),
        description=config.pop("description"),
        enabled=config.pop("enabled"),
        version=config.pop("version"),
        configuration=config,
        created_at=now,
        window_start=now,
        next_end=next_boundary(now, config),
        window_snapshot={},
    )
    definition.window_snapshot = snapshot(definition)
    db.add(definition)
    db.flush()
    db.add(
        DigestVersion(
            definition_id=definition.id,
            version=1,
            actor_id=actor.id,
            snapshot=snapshot(definition),
        )
    )
    actions = [
        {"kind": "DIGEST", "scope": "OCCURRENCE", "target_id": str(definition.id)}
    ]
    routing = RoutingRule(
        team_id=DEFAULT_TEAM_ID,
        name="Включить межсетевой экран в сводку",
        description="",
        priority=0,
        enabled=True,
        version=1,
        conditions=condition,
        actions=actions,
    )
    db.add(routing)
    db.flush()
    db.add(
        RuleVersion(
            routing_rule_id=routing.id,
            version=1,
            actor_id=actor.id,
            snapshot={
                "name": routing.name,
                "description": "",
                "priority": 0,
                "enabled": True,
                "version": 1,
                "conditions": condition,
                "actions": actions,
            },
        )
    )
engine.dispose()
""",
    )
    for _ in range(3):
        message = EmailMessage()
        message["From"], message["To"] = "firewall@example.org", "events@localhost"
        message["Subject"] = "digest-check Отклонено подключение"
        message.set_content("Сообщение межсетевого экрана")
        with smtplib.SMTP("127.0.0.1", smtp_port, timeout=5) as client:
            client.send_message(message)
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        ready = json.loads(
            run(
                "exec",
                "-T",
                "api",
                "python",
                "-",
                input_text=BASE
                + """
with factory() as db:
    print(
        db.scalar(
            select(func.count())
            .select_from(DigestReceipt)
            .join(Event, Event.id == DigestReceipt.event_id)
            .where(Event.subject.like("digest-check%"))
        )
    )
engine.dispose()
""",
            )
        )
        if ready == 3:
            break
        time.sleep(0.5)
    else:
        raise AssertionError("DIGEST_NORMALIZATION_TIMEOUT")
    run("restart", "scheduler")
    run("up", "-d", "--no-build", "--no-deps", "--wait", "scheduler")
    run(
        "exec",
        "-T",
        "api",
        "python",
        "-",
        input_text=BASE
        + """
with factory() as db:
    definition = db.scalar(select(DigestDefinition))
    end, identifier = utc(definition.next_end), definition.id
    assert end.hour == 5 and end.minute == 0
for _ in range(3):
    advance(factory, settings, now=end)
with factory() as db:
    result = db.scalar(select(DigestRun).where(DigestRun.definition_id == identifier))
    assert result.summary["occurrences"] == 3 and result.summary["events"] == 1
    assert result.state == "READY"
    assert (
        db.scalar(
            select(func.count())
            .select_from(Notification)
            .where(Notification.mode == "DIGEST")
        )
        == 1
    )
engine.dispose()
""",
    )
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        status = json.loads(
            run(
                "exec",
                "-T",
                "api",
                "python",
                "-",
                input_text=BASE
                + """
import json

with factory() as db:
    print(
        json.dumps(
            db.scalar(select(Notification.status).where(Notification.mode == "DIGEST"))
        )
    )
engine.dispose()
""",
            )
        )
        if status == "SENT":
            break
        time.sleep(0.5)
    else:
        raise AssertionError("DIGEST_DELIVERY_TIMEOUT")
    run(
        "exec",
        "-T",
        "api",
        "python",
        "-",
        input_text=BASE
        + """
import ssl, urllib.request, json

context = ssl.create_default_context(cafile="/run/fixtures/cert.pem")
with urllib.request.urlopen(
    "https://api.telegram.org/messages", context=context, timeout=5
) as response:
    messages = json.load(response)["messages"]
mail = [
    m
    for m in messages
    if m.get("smtp") and m.get("subject") == "Ежедневная сводка межсетевого экрана"
]
assert (
    len(mail) == 1
    and "Поступлений: 3" in mail[0]["text"]
    and "Отдельных событий: 1" in mail[0]["text"]
)
engine.dispose()
""",
    )
    prepare_pending_fixture(run, smtp_port)
    print(
        "Межсетевой экран → сводка в 08:00 → одно письмо: 3 поступления, 1 событие; "
        "рестарт и повторы не создали дубль.",
        flush=True,
    )


def prepare_pending_fixture(run, smtp_port):
    # A failed normalization remains visibly pending; only the disposable test task is stopped.
    run("stop", "ingestion")
    try:
        message = EmailMessage()
        message["From"], message["To"] = "firewall@example.org", "events@localhost"
        message["Subject"] = "digest-pending Контроль ожидания"
        message.set_content("Проверка решения администратора")
        with smtplib.SMTP("127.0.0.1", smtp_port, timeout=5) as client:
            client.send_message(message)
        run(
            "exec",
            "-T",
            "api",
            "python",
            "-",
            input_text=BASE
            + """
from copy import deepcopy

with factory.begin() as db:
    raw = db.scalar(
        select(RawMessage)
        .where(RawMessage.state == "PENDING")
        .order_by(RawMessage.received_at.desc())
    )
    task = db.scalar(
        select(WorkItem).where(
            WorkItem.kind == "NORMALIZE", WorkItem.entity_id == raw.id
        )
    )
    task.state, task.failure_code = "FAILED", "TASK_FAILED"
    original = db.scalar(select(DigestDefinition))
    now = clock(db)
    config = deepcopy(original.configuration)
    config["selection"], config["source_ids"], config["send_empty"] = "FILTER", [], True
    definition = DigestDefinition(
        team_id=DEFAULT_TEAM_ID,
        name="Сводка с задержкой обработки",
        description="",
        enabled=True,
        version=1,
        configuration=config,
        created_at=utc(raw.received_at) - timedelta(seconds=1),
        window_start=now,
        next_end=next_boundary(now, config),
        window_snapshot={},
    )
    definition.window_snapshot = snapshot(definition)
    db.add(definition)
    db.flush()
    actor = db.scalar(select(User).where(User.username == "admin"))
    db.add(
        DigestVersion(
            definition_id=definition.id,
            version=1,
            actor_id=actor.id,
            snapshot=snapshot(definition),
        )
    )
    db.add(
        DigestRun(
            team_id=DEFAULT_TEAM_ID,
            definition_id=definition.id,
            window_start=definition.created_at,
            window_end=now,
            snapshot=snapshot(definition),
            state="ATTENTION",
            sealed_at=now,
            review_at=now - timedelta(minutes=1),
            pending_count=1,
        )
    )
engine.dispose()
""",
        )
    finally:
        run("start", "ingestion")
        run("up", "-d", "--no-build", "--no-deps", "--wait", "ingestion")
