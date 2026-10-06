"""Persisted deadlines and browser fixtures in the disposable local stack."""

import json
import smtplib
import time
from email.message import EmailMessage


def verify_restart(run):
    query = """
import json
from sqlalchemy import select
from app.settings import Settings
from app.persistence.database import build_engine, build_session_factory
from app.persistence.models import EscalationRun, EscalationStep
engine = build_engine(Settings())
with build_session_factory(engine)() as db:
    rows = db.execute(
        select(EscalationStep.id, EscalationStep.due_at, EscalationStep.state)
        .join(EscalationRun, EscalationStep.run_id == EscalationRun.id)
        .where(EscalationRun.state == "ACTIVE")
        .order_by(EscalationStep.id)
    ).all()
    assert rows and any(row.state == "WAITING" for row in rows)
    print(json.dumps([[str(value) for value in row] for row in rows]))
engine.dispose()
"""
    before = run("exec", "-T", "api", "python", "-", input_text=query)
    run("restart", "scheduler")
    run("up", "-d", "--no-build", "--no-deps", "--wait", "scheduler")
    after = run("exec", "-T", "api", "python", "-", input_text=query)
    assert json.loads(before) == json.loads(after)
    print("Перезапуск планировщика между шагами сохранил сроки и состояния.", flush=True)


def verify(run, smtp_port):
    run(
        "exec",
        "-T",
        "api",
        "python",
        "-",
        input_text="""
from app.settings import Settings
from app.persistence.database import build_engine, build_session_factory
from app.persistence.models import (
    DEFAULT_TEAM_ID,
    User,
    Source,
    IdentificationRule,
    RuleVersion,
    RoutingRule,
    Template,
    TemplateVersion,
    NotificationChannel,
    EscalationPolicy,
    EscalationPolicyVersion,
)

settings = Settings()
engine = build_engine(settings)
with build_session_factory(engine).begin() as db:
    actor = db.query(User).filter_by(username="admin").one()
    source = db.query(Source).filter_by(name="Kaspersky").one()
    condition = {
        "field": "subject",
        "operator": "contains",
        "value": "escalation-check",
        "case_sensitive": False,
    }
    assignments = {
        "severity": "CRITICAL",
        "category": "Безопасность",
        "event_type": "Проверка",
        "tags": [],
    }
    identification = IdentificationRule(
        team_id=DEFAULT_TEAM_ID,
        source_id=source.id,
        name="Проверка эскалации",
        enabled=True,
        priority=0,
        version=1,
        conditions=condition,
        assignments=assignments,
    )
    db.add(identification)
    db.flush()
    db.add(
        RuleVersion(
            rule_id=identification.id,
            version=1,
            actor_id=actor.id,
            snapshot={
                "name": identification.name,
                "source_id": str(source.id),
                "enabled": True,
                "priority": 0,
                "version": 1,
                "conditions": condition,
                "assignments": assignments,
            },
        )
    )
    template = Template(
        team_id=DEFAULT_TEAM_ID,
        name="Эскалация",
        kind="WEBHOOK",
        version=1,
        subject="{{ event.subject }}",
        body="{{ event.source }} — {{ event.severity }}: {{ event.body }}",
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
                "kind": "WEBHOOK",
                "subject": template.subject,
                "body": template.body,
                "html": "",
            },
        )
    )
    channel = NotificationChannel(
        team_id=DEFAULT_TEAM_ID,
        name="Локальная проверка эскалации",
        kind="WEBHOOK",
        template_id=template.id,
        enabled=True,
        configuration={"kind": "WEBHOOK", "url": "https://api.telegram.org/escalation"},
    )
    db.add(channel)
    db.flush()
    steps = [{"channel_id": str(channel.id), "delay_seconds": n} for n in [0, 600, 1800]]
    policy = EscalationPolicy(
        team_id=DEFAULT_TEAM_ID,
        name="Критическое событие",
        description="Локальная проверка",
        enabled=True,
        version=1,
        steps=steps,
    )
    db.add(policy)
    db.flush()
    db.add(
        EscalationPolicyVersion(
            policy_id=policy.id,
            version=1,
            actor_id=actor.id,
            snapshot={
                "name": policy.name,
                "description": policy.description,
                "enabled": True,
                "version": 1,
                "steps": steps,
            },
        )
    )
    actions = [{"kind": "ESCALATE", "scope": "EVENT", "target_id": str(policy.id)}]
    routing = RoutingRule(
        team_id=DEFAULT_TEAM_ID,
        name="Критическая эскалация",
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

    mail(smtp_port, "расписание")
    base = """
import json
from datetime import timedelta
from sqlalchemy import select, func
from app.settings import Settings
from app.persistence.database import build_engine, build_session_factory
from app.persistence.models import Event, EscalationRun, EscalationStep, Notification
from app.services.events.escalation import advance
from app.workers.queue import utc

settings = Settings()
engine = build_engine(settings)
factory = build_session_factory(engine)
"""
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        count = json.loads(
            run(
                "exec",
                "-T",
                "api",
                "python",
                "-",
                input_text=base
                + """
with factory() as db:
    print(
        db.scalar(
            select(func.count())
            .select_from(Notification)
            .where(Notification.escalation_run_id.is_not(None), Notification.status == "SENT")
        )
    )
engine.dispose()
""",
            )
        )
        if count == 1:
            break
        time.sleep(0.5)
    else:
        raise AssertionError("ESCALATION_FIRST_DELIVERY_TIMEOUT")
    verify_restart(run)
    run(
        "exec",
        "-T",
        "api",
        "python",
        "-",
        input_text=base
        + """
with factory() as db:
    run = db.scalar(
        select(EscalationRun)
        .join(Event, Event.id == EscalationRun.event_id)
        .where(Event.subject == "escalation-check расписание")
    )
    identifier, start = run.id, utc(run.created_at)
for seconds in [599, 600, 1799, 1800, 1800]:
    advance(factory, settings, now=start + timedelta(seconds=seconds))
    with factory() as db:
        total = db.scalar(
            select(func.count())
            .select_from(Notification)
            .where(Notification.escalation_run_id == identifier)
        )
        assert total == (1 if seconds < 600 else 2 if seconds < 1800 else 3)
with factory() as db:
    steps = db.scalars(
        select(EscalationStep)
        .where(EscalationStep.run_id == identifier)
        .order_by(EscalationStep.ordinal)
    ).all()
    assert [int((utc(s.due_at) - start).total_seconds()) for s in steps] == [0, 600, 1800]
    assert db.get(EscalationRun, identifier).state == "COMPLETED"
engine.dispose()
""",
    )
    print(
        "Эскалация: первый шаг доставлен локально; сроки 10 и 30 минут "
        "и повтор планировщика проверены управляемыми часами.",
        flush=True,
    )


def mail(smtp_port, suffix):
    message = EmailMessage()
    message["From"], message["To"] = "security@example.org", "events@localhost"
    message["Subject"] = "escalation-check " + suffix
    message.set_content("Критическое событие: " + suffix)
    with smtplib.SMTP("127.0.0.1", smtp_port, timeout=5) as client:
        client.send_message(message)


def prepare_browser(run, smtp_port):
    # Create timed browser data AFTER server tests: a slow suite may exceed 10 min.
    # Never rewrite real deadlines or weaken assertions about unstarted steps.
    for suffix in ["для подтверждения", "для подавления"]:
        mail(smtp_port, suffix)
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        count = run(
            "exec",
            "-T",
            "api",
            "python",
            "-",
            input_text="""
from sqlalchemy import select, func
from app.settings import Settings
from app.persistence.database import build_engine, build_session_factory
from app.persistence.models import Event, EscalationRun, EscalationStep
engine = build_engine(Settings())
with build_session_factory(engine)() as db:
    print(db.scalar(select(func.count()).select_from(EscalationStep)
          .join(EscalationRun, EscalationStep.run_id == EscalationRun.id)
          .join(Event, Event.id == EscalationRun.event_id)
          .where(Event.subject.in_(["escalation-check для подтверждения",
                                    "escalation-check для подавления"]),
                 EscalationRun.state == "ACTIVE", EscalationStep.ordinal > 1,
                 EscalationStep.state == "WAITING")))
engine.dispose()
""",
        )
        if int(count.strip()) == 4:
            print(
                "События эскалации подготовлены непосредственно перед браузерными проверками.",
                flush=True,
            )
            return
        time.sleep(0.5)
    raise AssertionError("BROWSER_ESCALATION_FIXTURE_TIMEOUT")
