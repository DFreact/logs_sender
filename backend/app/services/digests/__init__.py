"""Durable windows and bounded composition; no network IO or process-local timers."""

import json
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import String, cast, delete, func, select, tuple_

from app.persistence.models import (
    DigestDefinition,
    DigestItem,
    DigestReceipt,
    DigestRun,
    Event,
    NotificationChannel,
    RawMessage,
    Source,
    Team,
)
from app.services.digests.schedule import next_boundary, window_start
from app.services.notifications import TEXT as NOTIFICATION_TEXT
from app.services.notifications import create
from app.workers.queue import clock, utc

TEXT = json.loads((Path(__file__).parents[2] / "i18n/digests.ru.json").read_text())
BATCH = 200


def snapshot(definition):
    return {
        "name": definition.name,
        "description": definition.description,
        "enabled": definition.enabled,
        "version": definition.version,
        **deepcopy(definition.configuration),
    }


def capture(db, event, occurrence, result):
    data = result["data"] if result else occurrence.normalized_snapshot
    source = db.get(Source, event.source_id) if event.source_id else None
    db.add(
        DigestReceipt(
            occurrence_id=occurrence.id,
            team_id=event.team_id,
            event_id=event.id,
            received_at=occurrence.received_at,
            processed_at=clock(db),
            snapshot={
                "source_id": str(event.source_id) if event.source_id else "",
                "source": source.name if source else NOTIFICATION_TEXT["source_unknown"],
                "severity": data.get("severity") or "",
                "subject": data.get("subject") or "",
                "suppressed": event.status == "SUPPRESSED",
                "targets": sorted(
                    {
                        d["action"]["target_id"]
                        for d in (result or {}).get("decisions", [])
                        if d["action"]["kind"] == "DIGEST"
                        and d["outcome"] == "PLANNED"
                        and d["action"].get("target_id")
                    }
                ),
            },
        )
    )


def receipts(run, *, late=False):
    query = select(DigestReceipt).where(
        DigestReceipt.team_id == run.team_id,
        DigestReceipt.received_at >= run.window_start,
        DigestReceipt.received_at < run.window_end,
        DigestReceipt.snapshot["suppressed"].as_boolean().is_(False),
        DigestReceipt.event_id.in_(select(Event.id).where(Event.status != "SUPPRESSED")),
    )
    config = run.snapshot
    if config["source_ids"]:
        query = query.where(
            DigestReceipt.snapshot["source_id"].as_string().in_(config["source_ids"])
        )
    if config["severities"]:
        query = query.where(
            DigestReceipt.snapshot["severity"].as_string().in_(config["severities"])
        )
    if config["selection"] == "RULE":
        # UUIDs have a fixed canonical length; serialized JSON cannot contain a partial UUID match.
        query = query.where(
            cast(DigestReceipt.snapshot["targets"], String).contains(str(run.definition_id))
        )
    if run.cutoff_at:
        query = query.where(
            DigestReceipt.processed_at > run.cutoff_at
            if late
            else DigestReceipt.processed_at <= run.cutoff_at
        )
    return query


def pending(db, run):
    return db.scalar(
        select(func.count())
        .select_from(RawMessage)
        .where(
            RawMessage.team_id == run.team_id,
            RawMessage.received_at >= run.window_start,
            RawMessage.received_at < run.window_end,
            RawMessage.state == "PENDING",
        )
    )


def aggregate(db, run_id):
    total, events, first, last = db.execute(
        select(
            func.count(),
            func.count(func.distinct(DigestItem.event_id)),
            func.min(DigestItem.received_at),
            func.max(DigestItem.received_at),
        ).where(DigestItem.run_id == run_id)
    ).one()

    def groups(column, limit):
        return [
            {"label": label, "count": count}
            for label, count in db.execute(
                select(column, func.count())
                .where(DigestItem.run_id == run_id)
                .group_by(column)
                .order_by(func.count().desc(), column)
                .limit(limit)
            )
        ]

    frequent = [
        {"event_id": str(event), "subject": subject, "count": count}
        for event, subject, count in db.execute(
            select(DigestItem.event_id, func.min(DigestItem.subject), func.count())
            .where(DigestItem.run_id == run_id)
            .group_by(DigestItem.event_id)
            .order_by(func.count().desc(), DigestItem.event_id)
            .limit(20)
        )
    ]
    return {
        "occurrences": total,
        "events": events,
        "first": utc(first).isoformat() if first else None,
        "last": utc(last).isoformat() if last else None,
        "sources": groups(DigestItem.source, 20),
        "severities": groups(DigestItem.severity, 6),
        "frequent": frequent,
    }


def content(run):
    from zoneinfo import ZoneInfo

    def date(value):
        return (
            utc(value).astimezone(ZoneInfo(run.snapshot["time_zone"])).strftime("%d.%m.%Y %H:%M %z")
        )

    summary = run.summary
    lines = [
        TEXT["period"].format(start=date(run.window_start), end=date(run.window_end)),
        TEXT["counts"].format(**summary),
    ]
    if run.partial:
        lines.append(TEXT["partial"].format(count=run.pending_count))
    if summary["first"]:
        from datetime import datetime

        lines.append(
            TEXT["range"].format(
                first=date(datetime.fromisoformat(summary["first"])),
                last=date(datetime.fromisoformat(summary["last"])),
            )
        )
    for field in ("sources", "severities"):
        lines.append(TEXT[field])
        for item in summary[field]:
            label = (
                item["label"]
                if field == "sources"
                else NOTIFICATION_TEXT["severity"].get(
                    item["label"], NOTIFICATION_TEXT["severity_unset"]
                )
            )
            lines.append(TEXT["group"].format(label=label, count=item["count"]))
    lines.append(TEXT["frequent"])
    for item in summary["frequent"]:
        lines.append(
            TEXT["group"].format(label=item["subject"] or TEXT["no_subject"], count=item["count"])
        )
    return {"subject": run.snapshot["name"], "body": "\n".join(lines)}


def build_batch(db, run, settings, now):
    if run.state in ("WAITING", "ATTENTION"):
        run.pending_count = pending(db, run)
        if run.pending_count and not run.partial:
            run.state = "ATTENTION" if now >= utc(run.review_at) else "WAITING"
            return
        run.cutoff_at = now
        run.state = "BUILDING"
    query = receipts(run)
    if run.cursor:
        previous = db.get(DigestReceipt, run.cursor)
        query = query.where(
            tuple_(DigestReceipt.received_at, DigestReceipt.occurrence_id)
            > (previous.received_at, previous.occurrence_id)
        )
    rows = db.scalars(
        query.order_by(DigestReceipt.received_at, DigestReceipt.occurrence_id).limit(BATCH)
    ).all()
    for row in rows:
        db.add(
            DigestItem(
                run_id=run.id,
                occurrence_id=row.occurrence_id,
                event_id=row.event_id,
                received_at=row.received_at,
                subject=row.snapshot["subject"],
                source=row.snapshot["source"],
                severity=row.snapshot["severity"],
            )
        )
        run.cursor = row.occurrence_id
    if len(rows) == BATCH:
        return
    db.flush()
    # Suppression before freezing the payload applies even between composition batches.
    db.execute(
        delete(DigestItem).where(
            DigestItem.run_id == run.id,
            DigestItem.event_id.in_(select(Event.id).where(Event.status == "SUPPRESSED")),
        )
    )
    run.summary = aggregate(db, run.id)
    if not run.summary["occurrences"] and not run.snapshot["send_empty"]:
        run.state = "EMPTY"
    else:
        channel = db.get(NotificationChannel, UUID(run.snapshot["channel_id"]))
        row = create(
            db,
            channel,
            settings,
            content(run),
            mode="DIGEST",
            template_id=UUID(run.snapshot["template_id"]),
        )
        run.notification_id, run.state = row.id, "READY"


def advance(factory, settings, *, now=None):
    with factory() as db:
        due = now or clock(db)
        definitions = db.execute(
            select(DigestDefinition.id, DigestDefinition.team_id)
            .where(DigestDefinition.next_end <= due)
            .order_by(DigestDefinition.next_end)
            .limit(10)
        ).all()
    for identifier, team in definitions:
        with factory.begin() as db:
            db.execute(select(Team).where(Team.id == team).with_for_update()).scalar_one()
            row = db.scalar(
                select(DigestDefinition).where(DigestDefinition.id == identifier).with_for_update()
            )
            instant = now or clock(db)
            for _ in range(8):
                if utc(row.next_end) > instant:
                    break
                config = row.window_snapshot
                end = utc(row.next_end)
                start = window_start(row.window_start, end, row.created_at, config)
                db.add(
                    DigestRun(
                        id=uuid4(),
                        team_id=team,
                        definition_id=row.id,
                        window_start=start,
                        window_end=end,
                        snapshot=deepcopy(config),
                        state="WAITING" if config["enabled"] else "SKIPPED",
                        sealed_at=instant,
                        review_at=end + timedelta(seconds=config["wait_seconds"]),
                    )
                )
                row.window_start = end
                row.window_snapshot = snapshot(row)
                row.next_end = next_boundary(end, row.configuration)
    with factory() as db:
        candidates = db.execute(
            select(DigestRun.id, DigestRun.team_id)
            .where(DigestRun.state.in_(("WAITING", "ATTENTION", "BUILDING")))
            .order_by(DigestRun.checked_at, DigestRun.window_end, DigestRun.id)
            .limit(settings.work_batch_size)
        ).all()
    for identifier, team in candidates:
        with factory.begin() as db:
            # Same order as authenticated mutations. Excludes in-flight ingestion at cutoff.
            db.execute(select(Team).where(Team.id == team).with_for_update()).scalar_one()
            run = db.scalar(select(DigestRun).where(DigestRun.id == identifier).with_for_update())
            if run.state in ("WAITING", "ATTENTION", "BUILDING"):
                run.checked_at = now or clock(db)
                build_batch(db, run, settings, run.checked_at)
