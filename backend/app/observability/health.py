from datetime import timedelta
from uuid import UUID

from sqlalchemy import select

from app.domain.enums import HealthReason, HealthStatus
from app.observability.metrics import counters
from app.persistence.models import DEFAULT_TEAM_ID, ComponentHeartbeat, WorkItem
from app.storage.files import BlobStore
from app.workers.queue import clock, insert_for, utc
from app.workers.transport import redis_client


def heartbeat(factory, component: str, instance_id: UUID, status="HEALTHY", code="OK"):
    with factory.begin() as db:
        statement = insert_for(db, ComponentHeartbeat).values(
            component=component,
            instance_id=instance_id,
            checked_at=clock(db),
            status=status,
            code=code,
        )
        db.execute(
            statement.on_conflict_do_update(
                index_elements=[ComponentHeartbeat.component, ComponentHeartbeat.instance_id],
                set_={"checked_at": statement.excluded.checked_at, "status": status, "code": code},
            )
        )


def infrastructure(settings):
    result = []
    client = None
    try:
        client = redis_client(settings)
        client.ping()
        result.append({"component": "BROKER", "status": "HEALTHY", "code": "OK"})
    except Exception:
        result.append({"component": "BROKER", "status": "UNAVAILABLE", "code": "UNREACHABLE"})
    finally:
        if client:
            client.close()
    try:
        code = BlobStore(
            settings.storage_root, settings.blob_max_bytes, settings.storage_min_free_bytes
        ).probe()
        result.append(
            {
                "component": "STORAGE",
                "status": "HEALTHY" if code == "OK" else "DEGRADED",
                "code": code,
            }
        )
    except Exception:
        result.append({"component": "STORAGE", "status": "UNAVAILABLE", "code": "UNREACHABLE"})
    return result


def snapshot(db, settings, probe=None):
    now = clock(db)
    components = [{"component": "DATABASE", "status": "HEALTHY", "code": "OK"}]
    components.extend((probe or infrastructure)(settings))
    for component in ("DISPATCHER", "SCHEDULER", "WORKER", "SMTP", "INGESTION", "DELIVERY"):
        rows = db.scalars(
            select(ComponentHeartbeat).where(ComponentHeartbeat.component == component)
        ).all()
        fresh = [
            row
            for row in rows
            if utc(row.checked_at) >= now - timedelta(seconds=settings.heartbeat_stale_seconds)
        ]
        row = max(
            fresh or rows,
            key=lambda item: (item.status == "HEALTHY", utc(item.checked_at)),
            default=None,
        )
        status = row.status if fresh else "UNAVAILABLE" if row else "UNKNOWN"
        code = row.code if fresh else "STALE" if row else "NOT_OBSERVED"
        components.append(
            {
                "component": component,
                "status": status if status in HealthStatus else "UNKNOWN",
                "code": code if code in HealthReason else "NOT_OBSERVED",
                "last_seen_at": utc(row.checked_at) if row else None,
            }
        )
    counts = {state: 0 for state in ("PENDING", "RUNNING", "SUCCEEDED", "FAILED")}
    totals = counters(db)
    counts.update({state: totals.get(f"task_{state}", 0) for state in counts})
    earliest = db.scalar(
        select(WorkItem.due_at)
        .where(
            WorkItem.state == "PENDING",
            WorkItem.due_at <= now,
            WorkItem.team_id == DEFAULT_TEAM_ID,
        )
        .order_by(WorkItem.due_at)
        .limit(1)
    )
    delay = max(0, int((now - utc(earliest)).total_seconds())) if earliest else 0
    status = "HEALTHY" if all(row["status"] == "HEALTHY" for row in components) else "DEGRADED"
    if counts["FAILED"] or delay > settings.lease_seconds:
        status = "DEGRADED"
    return {
        "status": status,
        "checked_at": now,
        "components": components,
        "queue": counts,
        "oldest_due_seconds": delay,
    }
