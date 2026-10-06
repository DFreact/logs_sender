from datetime import timedelta

from sqlalchemy import delete, select

from app.persistence.models import DEFAULT_TEAM_ID, ComponentHeartbeat, Team, WorkItem
from app.services.notifications.delivery import recover
from app.workers.queue import clock, enqueue, recover_expired


def schedule_once(factory, settings):
    from app.services.events.escalation import advance

    advance(factory, settings)
    from app.services.digests import advance as advance_digests

    advance_digests(factory, settings)
    with factory.begin() as db:
        db.scalar(select(Team).where(Team.id == DEFAULT_TEAM_ID).with_for_update())
        now = clock(db)
        recovered = recover_expired(db, settings.work_batch_size, now=now)
        recovered += recover(db, settings.work_batch_size, now=now)
        slot = int(now.timestamp()) // settings.maintenance_seconds
        enqueue(db, "STORAGE_COLLECT", DEFAULT_TEAM_ID, f"storage-collect:{slot}", due_at=now)
        active = db.scalar(
            select(WorkItem.id)
            .where(
                WorkItem.team_id == DEFAULT_TEAM_ID,
                WorkItem.kind == "RETENTION",
                WorkItem.entity_id == DEFAULT_TEAM_ID,
                WorkItem.state.in_(["PENDING", "RUNNING"]),
            )
            .limit(1)
        )
        if not active:
            enqueue(db, "RETENTION", DEFAULT_TEAM_ID, f"retention:{slot}", due_at=now)
        # Only transient liveness rows, not audit/business history, are pruned here.
        db.execute(
            delete(ComponentHeartbeat).where(
                ComponentHeartbeat.checked_at < now - timedelta(days=1),
            )
        )
        return recovered
