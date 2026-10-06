"""Event transitions and escalation serialize with delivery's shared team lock."""

from sqlalchemy import select

from app.persistence.models import EscalationRun, EscalationStep, Notification, WorkItem


def stop_reason(db, row):
    from app.persistence.models import Event

    event = db.get(Event, row.event_id) if row.event_id else None
    if event and (
        event.status == "SUPPRESSED" or (row.escalation_run_id and event.status != "NEW")
    ):
        return "EVENT_" + event.status
    return None


def cancel_waiting(db, row, reason, now):
    row.status, row.finished_at, row.failure_code = "CANCELLED", now, reason
    row.version += 1
    if row.work_id:
        task = db.get(WorkItem, row.work_id)
        if task and task.state == "PENDING":
            task.state, task.completed_at = "SUCCEEDED", now


def stop_for_event(db, event, now):
    runs = db.scalars(
        select(EscalationRun).where(EscalationRun.event_id == event.id).with_for_update()
    ).all()
    for run in runs:
        if run.state != "STOPPED":
            run.state, run.stop_reason, run.stopped_at = "STOPPED", event.status, now
        for step in db.scalars(
            select(EscalationStep)
            .where(EscalationStep.run_id == run.id, EscalationStep.state == "WAITING")
            .with_for_update()
        ):
            step.state = "CANCELLED"
    query = select(Notification).where(
        Notification.event_id == event.id,
        Notification.status.in_(("PENDING", "RETRYING", "FAILED")),
    )
    if event.status != "SUPPRESSED":
        query = query.where(Notification.escalation_run_id.is_not(None))
    for row in db.scalars(query.order_by(Notification.id).with_for_update()):
        cancel_waiting(db, row, "EVENT_" + event.status, now)
