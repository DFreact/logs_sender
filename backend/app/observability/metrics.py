"""Fixed-size counter reads and bounded minute samples, independent of archive size."""

import shutil
from datetime import timedelta

from sqlalchemy import func, select

from app.observability.resources import cpu_percent, read_resources
from app.persistence.models import DEFAULT_TEAM_ID, HealthSample, MetricCounter
from app.workers.queue import clock, insert_for, utc

TOTALS = ("accepted_total", "normalized_total", "sent_total")


def counters(db, team=DEFAULT_TEAM_ID):
    rows = db.execute(
        select(MetricCounter.kind, func.sum(MetricCounter.value))
        .where(MetricCounter.team_id == team)
        .group_by(MetricCounter.kind)
    )
    # PostgreSQL SUM(bigint) returns numeric; JSON counters must be integers.
    return {kind: int(value) for kind, value in rows}


def sample(factory, settings, team=DEFAULT_TEAM_ID, *, now=None):
    # Filesystem inspection outside a database transaction. Failure is unknown, not zero.
    disk = None
    try:
        usage = shutil.disk_usage(settings.storage_root)
        disk = {"total": usage.total, "used": usage.used, "free": usage.free}
    except OSError:
        pass
    resources = read_resources()
    with factory.begin() as db:
        at = now or clock(db)
        slot = int(at.timestamp()) // 60
        db.execute(
            insert_for(db, HealthSample)
            .values(
                team_id=team,
                slot=slot,
                measured_at=at,
                metrics={"counters": counters(db, team), "disk": disk, "resources": resources},
            )
            .on_conflict_do_nothing(index_elements=[HealthSample.team_id, HealthSample.slot])
        )


def current(db, team=DEFAULT_TEAM_ID):
    now = clock(db)
    rows = db.scalars(
        select(HealthSample)
        .where(HealthSample.team_id == team)
        .order_by(HealthSample.slot.desc())
        .limit(2)
    ).all()
    latest = rows[0] if rows else None
    fresh = latest is not None and utc(latest.measured_at) >= now - timedelta(minutes=3)
    rates = {key: None for key in TOTALS}
    cpu = None
    if fresh and len(rows) == 2:
        seconds = (utc(latest.measured_at) - utc(rows[1].measured_at)).total_seconds()
        if 0 < seconds <= 180:
            cpu = cpu_percent(
                latest.metrics.get("resources", {}).get("cpu"),
                rows[1].metrics.get("resources", {}).get("cpu"),
            )
            for key in TOTALS:
                delta = latest.metrics["counters"].get(key, 0) - rows[1].metrics["counters"].get(
                    key, 0
                )
                rates[key] = delta / seconds if delta >= 0 else None
    disk = latest.metrics.get("disk") if fresh else None
    forecast = None
    if disk:
        old = db.scalar(
            select(HealthSample)
            .where(
                HealthSample.team_id == team,
                HealthSample.slot <= latest.slot - 60,
                HealthSample.slot >= latest.slot - 1440,
            )
            .order_by(HealthSample.slot.desc())
            .limit(1)
        )
        if old and old.metrics.get("disk") and old.metrics["disk"]["total"] == disk["total"]:
            growth = disk["used"] - old.metrics["disk"]["used"]
            if growth > 0:
                seconds = (utc(latest.measured_at) - utc(old.measured_at)).total_seconds()
                forecast = disk["free"] * seconds / growth
    return {
        "cpu_percent": cpu,
        "memory": latest.metrics.get("resources", {}).get("memory") if fresh else None,
        "counters": counters(db, team),
        "rates": rates,
        "disk": disk,
        "disk_remaining_seconds": forecast,
        "sampled_at": utc(latest.measured_at) if latest else None,
        "fresh": fresh,
    }
