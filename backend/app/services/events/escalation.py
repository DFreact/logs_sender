"""Absolute persisted deadlines; policy edits never rewrite existing runs."""

from copy import deepcopy
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import select

from app.persistence.models import (
    EscalationPolicy,
    EscalationRun,
    EscalationStep,
    Event,
    NotificationChannel,
    Team,
)
from app.services.events import stop_for_event
from app.services.notifications import create, event_context
from app.workers.queue import clock, utc


def materialize(db, run, settings, now):
    steps = db.scalars(
        select(EscalationStep)
        .where(EscalationStep.run_id == run.id)
        .order_by(EscalationStep.ordinal)
        .with_for_update()
    ).all()
    for step in steps:
        if step.state != "WAITING" or utc(step.due_at) > now:
            continue
        channel = db.get(NotificationChannel, step.channel_id)
        row = create(db, channel, settings, run.context, event_id=run.event_id, due_at=step.due_at)
        row.escalation_run_id = run.id
        step.notification_id, step.state = row.id, "QUEUED"
    if all(step.state != "WAITING" for step in steps):
        run.state = "COMPLETED"


def from_decision(db, settings, event, decision, execution_id, data, *, now=None):
    action = decision["action"]
    if (
        decision["outcome"] != "PLANNED"
        or action["kind"] != "ESCALATE"
        or not action.get("target_id")
        or event.status != "NEW"
    ):
        return None
    policy = db.get(EscalationPolicy, UUID(action["target_id"]))
    if not policy or policy.team_id != event.team_id or not policy.enabled:
        return None
    now = now or clock(db)
    run = EscalationRun(
        id=uuid4(),
        team_id=event.team_id,
        event_id=event.id,
        execution_id=execution_id,
        policy_id=policy.id,
        snapshot={"name": policy.name, "version": policy.version, "steps": deepcopy(policy.steps)},
        context=event_context(db, event, data),
        state="ACTIVE",
        created_at=now,
    )
    db.add(run)
    db.flush()
    for index, item in enumerate(policy.steps):
        channel = db.get(NotificationChannel, UUID(item["channel_id"]))
        db.add(
            EscalationStep(
                run_id=run.id,
                ordinal=index + 1,
                channel_id=channel.id,
                channel_name=channel.name,
                due_at=now + timedelta(seconds=item["delay_seconds"]),
                state="WAITING",
            )
        )
    db.flush()
    materialize(db, run, settings, now)
    return run


def advance(factory, settings, *, now=None):
    # Discovery takes no locks. Each event uses a short independent transaction.
    with factory() as db:
        due = now or clock(db)
        candidates = db.execute(
            select(EscalationRun.id, EscalationRun.team_id, EscalationRun.event_id)
            .join(EscalationStep, EscalationStep.run_id == EscalationRun.id)
            .where(
                EscalationRun.state == "ACTIVE",
                EscalationStep.state == "WAITING",
                EscalationStep.due_at <= due,
            )
            .order_by(EscalationStep.due_at, EscalationRun.id)
            .limit(settings.work_batch_size)
        ).all()
    processed = 0
    for run_id, team_id, event_id in dict.fromkeys(candidates):
        with factory.begin() as db:
            db.execute(
                select(Team).where(Team.id == team_id).with_for_update(read=True)
            ).scalar_one()
            event = db.scalar(
                select(Event).where(Event.id == event_id).with_for_update(skip_locked=True)
            )
            if event is None:
                continue
            run = db.scalar(
                select(EscalationRun).where(EscalationRun.id == run_id).with_for_update()
            )
            if run.state != "ACTIVE":
                continue
            if event.status != "NEW":
                stop_for_event(db, event, now or clock(db))
            else:
                materialize(db, run, settings, now or clock(db))
            processed += 1
    return processed
