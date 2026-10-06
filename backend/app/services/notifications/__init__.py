"""Transactional preparation. No network IO and no replay of historical intents."""

import json
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import select

from app.api.errors import ApiFailure
from app.persistence.models import (
    Notification,
    NotificationChannel,
    Source,
    Template,
    TemplateVersion,
)
from app.services.templates import render
from app.workers.queue import clock, enqueue, utc

TEXT = json.loads((Path(__file__).parents[2] / "i18n/notifications.ru.json").read_text())


def event_context(db, event, data):
    source = db.get(Source, event.source_id) if event.source_id else None
    return {
        "subject": data.get("subject") or "",
        "body": data.get("body") or "",
        "sender": data.get("sender") or "",
        "source": source.name if source else TEXT["source_unknown"],
        "severity": TEXT["severity"].get(data.get("severity"), TEXT["severity_unset"]),
        "category": data.get("category") or "",
        "event_type": data.get("event_type") or "",
    }


def wake(db, row):
    row.work_id = enqueue(
        db,
        "DELIVER",
        row.id,
        f"notify:{row.id}:{row.generation}:{row.attempt_count}",
        team_id=row.team_id,
        due_at=row.due_at,
        max_attempts=1,
    )


def prepare(db, row, channel, template_id=None):
    """Never overwrite already prepared content, including manual retries."""
    if row.prepared is not None:
        return
    selected = template_id or channel.template_id
    template = db.get(Template, selected) if selected else None
    if not template or template.team_id != row.team_id or template.kind != row.kind:
        row.failure_code = "CHANNEL_NOT_CONFIGURED"
    else:
        version = db.scalar(
            select(TemplateVersion).where(
                TemplateVersion.template_id == template.id,
                TemplateVersion.version == template.version,
            )
        )
        if version:
            row.template_version_id = version.id
            try:
                row.prepared = render(version.snapshot, row.context)
                row.failure_code = None
                return
            except ApiFailure:
                row.failure_code = "TEMPLATE_INVALID"
        else:
            row.failure_code = "CHANNEL_NOT_CONFIGURED"
    row.status, row.finished_at, row.dead_lettered_at = "FAILED", clock(db), clock(db)


def create(
    db,
    channel,
    settings,
    context,
    *,
    event_id=None,
    execution_id=None,
    mode="IMMEDIATE",
    due_at=None,
    is_test=False,
    template_id=None,
):
    row = Notification(
        id=uuid4(),
        team_id=channel.team_id,
        event_id=event_id,
        execution_id=execution_id,
        channel_id=channel.id,
        channel_name=channel.name,
        channel_version=channel.version,
        kind=channel.kind,
        configuration=deepcopy(channel.configuration),
        context=context,
        mode=mode,
        status="PENDING",
        due_at=due_at or clock(db),
        generation=1,
        attempt_count=0,
        retry_delays=list(settings.delivery_retry_delays),
        jitter_percent=settings.delivery_jitter_percent,
        version=1,
        uncertain=False,
        is_test=is_test,
    )
    prepare(db, row, channel, template_id)
    db.add(row)
    db.flush()
    if row.status == "PENDING":
        wake(db, row)
    return row


def from_decision(db, settings, event, occurrence, decision, execution_id, data):
    action = decision["action"]
    if (
        decision["outcome"] != "PLANNED"
        or action["kind"] != "NOTIFY"
        or not action.get("target_id")
    ):
        return None
    channel = db.get(NotificationChannel, UUID(action["target_id"]))
    if not channel or channel.team_id != event.team_id:
        return None
    due = clock(db)
    if action["mode"] == "DELAYED":
        due = max(due, utc(occurrence.received_at) + timedelta(seconds=action["delay_seconds"]))
    elif action["mode"] == "SCHEDULED":
        due = max(due, datetime.fromisoformat(action["scheduled_at"]))
    return create(
        db,
        channel,
        settings,
        event_context(db, event, data),
        event_id=event.id,
        execution_id=execution_id,
        mode=action["mode"],
        due_at=due,
    )
