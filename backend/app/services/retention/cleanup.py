"""Short, restartable transactions. Files are removed by durable BLOB_DELETE tasks.

Every SELECT/DELETE of history is bounded. Active deliveries, normalization and
unsealed/preparing digest windows hold their inputs. Retiring events stop accepting
new deduplicated occurrences, then drain dependent rows over several batches.
"""

from datetime import timedelta

from sqlalchemy import and_, delete, exists, select, tuple_

from app.persistence.models import (
    Attachment,
    AuditEntry,
    AuthSession,
    BlobRecord,
    DedupKey,
    DeliveryAttempt,
    DigestDefinition,
    DigestItem,
    DigestReceipt,
    DigestRun,
    EscalationRun,
    EscalationStep,
    Event,
    EventAction,
    EventOccurrence,
    HealthSample,
    LoginLimit,
    Notification,
    NotificationOperation,
    RawMessage,
    RetentionPolicy,
    RetentionProgress,
    RuleExecution,
    Team,
    WorkItem,
)
from app.services.retention import DEFAULTS, progress, recalculate
from app.storage.service import request_delete
from app.workers.queue import clock, utc

ACTIVE_NOTIFICATIONS = ("PENDING", "PROCESSING", "RETRYING")
ACTIVE_DIGESTS = ("WAITING", "ATTENTION", "BUILDING")
TERMINAL = ("SENT", "FAILED", "CANCELLED")


def prune(db, model, conditions, limit):
    keys = list(model.__table__.primary_key.columns)
    order = {
        AuditEntry: AuditEntry.timestamp,
        WorkItem: WorkItem.completed_at,
        BlobRecord: BlobRecord.deleted_at,
        HealthSample: HealthSample.measured_at,
        AuthSession: AuthSession.expires_at,
        LoginLimit: LoginLimit.window_start,
    }.get(model)
    if order is None and model.__tablename__.endswith("versions"):
        order = model.created_at
    rows = db.execute(
        select(*keys).where(*conditions).order_by(*keys).limit(limit).with_for_update()
    ).all()
    if rows:
        db.execute(
            delete(model)
            .where(tuple_(*keys).in_([tuple(row) for row in rows]))
            .execution_options(synchronize_session=False)
        )
    return len(rows)


def has(db, model, *conditions):
    return (
        db.scalar(select(model.__table__.primary_key.columns[0]).where(*conditions).limit(1))
        is not None
    )


def active_work(db, team, kind, identifier):
    return has(
        db,
        WorkItem,
        WorkItem.team_id == team,
        WorkItem.kind == kind,
        WorkItem.entity_id == identifier,
        WorkItem.state.in_(["PENDING", "RUNNING"]),
    )


def digest_hold(db, event):
    # Conservative overlap keeps input even when a digest filter currently excludes it.
    if has(
        db,
        DigestDefinition,
        DigestDefinition.team_id == event.team_id,
        DigestDefinition.enabled.is_(True),
        DigestDefinition.window_start <= event.last_seen_at,
    ):
        return True
    return has(
        db,
        DigestRun,
        DigestRun.team_id == event.team_id,
        DigestRun.state.in_(ACTIVE_DIGESTS),
        DigestRun.window_start <= event.last_seen_at,
        DigestRun.window_end > event.first_seen_at,
    )


def held(db, event):
    return (
        has(db, DigestItem, DigestItem.event_id == event.id)
        or digest_hold(db, event)
        or has(
            db,
            Notification,
            Notification.event_id == event.id,
            Notification.status.in_(ACTIVE_NOTIFICATIONS),
        )
        or has(
            db, EscalationRun, EscalationRun.event_id == event.id, EscalationRun.state == "ACTIVE"
        )
    )


def cursor_rows(db, team, kind, model, conditions, now, limit):
    state = progress(db, team, kind)
    where = [*conditions]
    date = {
        RawMessage: RawMessage.expires_at,
        Event: Event.expires_at,
        Notification: Notification.finished_at,
        DigestRun: DigestRun.window_end,
    }[model]
    if state.cursor and state.cursor_at:
        where.append(tuple_(date, model.id) > tuple_(state.cursor_at, state.cursor))
    rows = db.scalars(
        select(model).where(*where).order_by(date, model.id).limit(limit + 1).with_for_update()
    ).all()
    more = len(rows) > limit
    rows = rows[:limit]
    state.cursor = rows[-1].id if more else None
    state.cursor_at = utc(getattr(rows[-1], date.key)) if state.cursor else None
    state.checked_at = now
    return state, rows


def raw_files(db, team, now, limit):
    state, rows = cursor_rows(
        db,
        team,
        "RAW_FILES",
        RawMessage,
        [
            RawMessage.team_id == team,
            RawMessage.expires_at <= now,
            RawMessage.purged_at.is_(None),
            RawMessage.state.in_(["NORMALIZED", "LIMITED"]),
        ],
        now,
        min(limit, 20),
    )
    count = 0
    for raw in rows:
        if active_work(db, team, "NORMALIZE", raw.id):
            state.held += 1
            continue
        # MIME parsing already bounds attachments; honour the same global batch budget.
        files = db.scalars(
            select(Attachment)
            .where(Attachment.raw_message_id == raw.id)
            .order_by(Attachment.id)
            .limit(max(0, limit - count))
        ).all()
        for file in files:
            blob = db.scalar(
                select(BlobRecord).where(BlobRecord.id == file.blob_id).with_for_update()
            )
            db.delete(file)
            db.flush()
            request_delete(db, blob)
            count += 1
        if count >= limit:
            break
        if raw.blob_id:
            blob = db.scalar(
                select(BlobRecord).where(BlobRecord.id == raw.blob_id).with_for_update()
            )
            raw.blob_id = None
            db.flush()
            request_delete(db, blob)
        raw.purged_at, raw.headers, raw.envelope_sender, raw.recipients = now, [], "", []
        count += 1
    state.processed += count
    return count


def retire_events(db, team, now, limit):
    state, rows = cursor_rows(
        db,
        team,
        "EVENTS",
        Event,
        [Event.team_id == team, Event.expires_at <= now],
        now,
        1,
    )
    count = 0
    for event in rows:
        if held(db, event):
            state.held += 1
            continue
        if has(
            db,
            EventOccurrence,
            EventOccurrence.event_id == event.id,
            EventOccurrence.raw_message_id.in_(
                select(RawMessage.id).where(RawMessage.purged_at.is_(None))
            ),
        ):
            state.held += 1
            continue
        event.retiring = True
        db.flush()
        # Historical delivery context is self-contained and survives event removal.
        notifications = db.scalars(
            select(Notification)
            .where(Notification.event_id == event.id)
            .order_by(Notification.id)
            .limit(limit)
            .with_for_update()
        ).all()
        if notifications:
            for row in notifications:
                row.event_id = row.execution_id = None
            count += len(notifications)
            break
        run_ids = select(EscalationRun.id).where(EscalationRun.event_id == event.id)
        linked = db.scalars(
            select(Notification)
            .where(Notification.escalation_run_id.in_(run_ids))
            .order_by(Notification.id)
            .limit(limit)
            .with_for_update()
        ).all()
        if linked:
            for row in linked:
                row.escalation_run_id = None
            count += len(linked)
            break
        for model, conditions in [
            (EscalationStep, [EscalationStep.run_id.in_(run_ids)]),
            (EscalationRun, [EscalationRun.event_id == event.id]),
            (EventAction, [EventAction.event_id == event.id]),
            (RuleExecution, [RuleExecution.event_id == event.id]),
            (DigestReceipt, [DigestReceipt.event_id == event.id]),
        ]:
            removed = prune(db, model, conditions, limit)
            if removed:
                count += removed
                break
        if count:
            break
        occurrences = db.execute(
            select(EventOccurrence, RawMessage)
            .join(RawMessage, RawMessage.id == EventOccurrence.raw_message_id)
            .where(EventOccurrence.event_id == event.id)
            .order_by(EventOccurrence.id)
            .limit(limit)
        ).all()
        for occurrence, raw in occurrences:
            if not raw.purged_at or active_work(db, team, "NORMALIZE", raw.id):
                state.held += 1
                continue
            db.delete(occurrence)
            db.flush()
            db.delete(raw)
            count += 1
        if not occurrences:
            db.delete(event)
            count += 1
    state.processed += count
    return count


def notifications(db, team, now, days, limit):
    state, rows = cursor_rows(
        db,
        team,
        "NOTIFICATIONS",
        Notification,
        [
            Notification.team_id == team,
            Notification.status.in_(TERMINAL),
            Notification.finished_at <= now - timedelta(days=days),
        ],
        now,
        1,
    )
    count = 0
    for row in rows:
        if active_work(db, team, "DELIVER", row.id):
            state.held += 1
            continue
        if row.escalation_run_id:
            run = db.get(EscalationRun, row.escalation_run_id)
            if run and run.state == "ACTIVE":
                state.held += 1
                continue
        for model in [EscalationStep, DigestRun]:
            linked = db.scalars(
                select(model).where(model.notification_id == row.id).limit(1).with_for_update()
            ).all()
            for item in linked:
                item.notification_id = None
        count += prune(db, DeliveryAttempt, [DeliveryAttempt.notification_id == row.id], limit)
        if not count:
            count += prune(
                db, NotificationOperation, [NotificationOperation.notification_id == row.id], limit
            )
        if not count:
            db.delete(row)
            count = 1
    state.processed += count
    return count


def digest_history(db, team, now, days, limit):
    state, rows = cursor_rows(
        db,
        team,
        "DIGEST_HISTORY",
        DigestRun,
        [
            DigestRun.team_id == team,
            ~DigestRun.state.in_(ACTIVE_DIGESTS),
            DigestRun.window_end <= now - timedelta(days=days),
        ],
        now,
        1,
    )
    count = 0
    for row in rows:
        if row.notification_id:
            note = db.get(Notification, row.notification_id)
            if note and note.status in ACTIVE_NOTIFICATIONS:
                state.held += 1
                continue
        count += prune(db, DigestItem, [DigestItem.run_id == row.id], limit)
        if not has(db, DigestItem, DigestItem.run_id == row.id):
            db.delete(row)
            count += 1
    state.processed += count
    return count


def other_history(db, team, now, config, limit):
    attempts = select(Notification.id).where(Notification.team_id == team)
    count = prune(
        db,
        DeliveryAttempt,
        [
            DeliveryAttempt.notification_id.in_(attempts),
            DeliveryAttempt.status != "PROCESSING",
            DeliveryAttempt.finished_at <= now - timedelta(days=config["attempt_days"]),
        ],
        limit,
    )
    count += prune(
        db,
        AuditEntry,
        [
            AuditEntry.team_id == team,
            AuditEntry.timestamp <= now - timedelta(days=config["audit_days"]),
        ],
        limit,
    )
    count += prune(
        db,
        WorkItem,
        [
            WorkItem.team_id == team,
            WorkItem.state.in_(["SUCCEEDED", "FAILED"]),
            WorkItem.completed_at <= now - timedelta(days=config["task_days"]),
            ~exists(select(Notification.id).where(Notification.work_id == WorkItem.id)),
            ~and_(
                WorkItem.kind == "NORMALIZE",
                exists(select(RawMessage.id).where(RawMessage.id == WorkItem.entity_id)),
            ),
            ~and_(
                WorkItem.kind == "BLOB_DELETE",
                exists(select(BlobRecord.id).where(BlobRecord.id == WorkItem.entity_id)),
            ),
        ],
        limit,
    )
    count += prune(
        db,
        BlobRecord,
        [
            BlobRecord.team_id == team,
            BlobRecord.state == "DELETED",
            BlobRecord.deleted_at <= now - timedelta(days=config["task_days"]),
            ~exists(
                select(WorkItem.id).where(
                    WorkItem.team_id == team,
                    WorkItem.kind == "BLOB_DELETE",
                    WorkItem.entity_id == BlobRecord.id,
                    WorkItem.state.in_(["PENDING", "RUNNING"]),
                )
            ),
        ],
        limit,
    )
    count += prune(
        db,
        DedupKey,
        [
            DedupKey.team_id == team,
            ~exists(select(Event.id).where(Event.dedup_key_id == DedupKey.id)),
        ],
        limit,
    )
    count += prune(
        db,
        AuthSession,
        [AuthSession.team_id == team, AuthSession.expires_at <= now - timedelta(days=1)],
        limit,
    )
    count += prune(
        db,
        HealthSample,
        [
            HealthSample.team_id == team,
            HealthSample.measured_at <= now - timedelta(days=config["health_days"]),
        ],
        limit,
    )
    count += prune(
        db, LoginLimit, [LoginLimit.window_start < int(now.timestamp()) // 60 - 1440], limit
    )
    from app.services.retention.versions import prune_versions

    count += prune_versions(db, team, now, config["history_days"], limit)
    return count


def run_batch(factory, settings, team, *, now=None):
    """One bounded pass; independent transactions release locks between work kinds."""
    limit = min(settings.work_batch_size, 200)
    total = 0
    for operation in ["RECALCULATE", "RAW", "EVENT", "NOTIFICATION", "DIGEST", "HISTORY"]:
        with factory.begin() as db:
            if db.bind.dialect.name == "postgresql":
                from sqlalchemy import text

                db.execute(text("SET LOCAL lock_timeout = '1s'"))
                db.execute(text("SET LOCAL statement_timeout = '3s'"))
            db.scalar(select(Team).where(Team.id == team).with_for_update(read=True))
            policy = db.scalar(
                select(RetentionPolicy)
                .where(RetentionPolicy.team_id == team, RetentionPolicy.scope == "GLOBAL")
                .with_for_update(skip_locked=True)
            )
            if not policy:
                continue
            at = now or clock(db)
            config = {**DEFAULTS, **policy.configuration}
            if operation == "RECALCULATE":
                total += recalculate(db, team, policy.revision, at, limit)
            elif policy.enabled and all(
                (state := db.get(RetentionProgress, (team, kind))) is not None
                and state.revision == policy.revision
                and state.complete
                for kind in ("RECALCULATE_EVENTS", "RECALCULATE_RAW")
            ):
                if operation == "RAW":
                    total += raw_files(db, team, at, limit)
                elif operation == "EVENT":
                    total += retire_events(db, team, at, limit)
                elif operation == "NOTIFICATION":
                    total += notifications(db, team, at, config["notification_days"], limit)
                elif operation == "DIGEST":
                    total += digest_history(db, team, at, config["history_days"], limit)
                else:
                    total += other_history(db, team, at, config, limit)
                kind = {
                    "RAW": "RAW_FILES",
                    "EVENT": "EVENTS",
                    "NOTIFICATION": "NOTIFICATIONS",
                    "DIGEST": "DIGEST_HISTORY",
                }.get(operation)
                state = db.get(RetentionProgress, (team, kind)) if kind else None
                # Continue scanning past held records, but stop after a full pass.
                total += int(state is not None and state.cursor is not None)
    return total
