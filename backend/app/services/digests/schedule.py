"""Calendar boundaries, including gaps and overlaps in local civil time."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from app.workers.queue import utc


def boundary(day, minute, zone):
    local = datetime.combine(day, datetime.min.time()) + timedelta(minutes=minute)
    tz = ZoneInfo(zone)
    # A civil day can disappear on date-line changes. Find the first valid minute.
    for offset in range(1441):
        candidate = local + timedelta(minutes=offset)
        value = candidate.replace(tzinfo=tz, fold=0).astimezone(UTC)
        if value.astimezone(tz).replace(tzinfo=None) == candidate:
            return value
    raise ValueError("INVALID_CIVIL_TIME")


def next_boundary(after, configuration):
    after = utc(after)
    day = after.astimezone(ZoneInfo(configuration["time_zone"])).date()
    for offset in range(4):
        value = boundary(
            day + timedelta(days=offset), configuration["minute_of_day"], configuration["time_zone"]
        )
        if value > after:
            return value
    raise ValueError("INVALID_DIGEST_SCHEDULE")


def window_start(previous_end, end, created_at, config):
    return (
        max(utc(created_at), utc(end) - timedelta(hours=24))
        if config["period"] == "LAST_24_HOURS"
        else utc(previous_end)
    )
