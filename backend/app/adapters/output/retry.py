import re
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime


def retry_after(value, *, now=None):
    if not isinstance(value, str) or len(value) > 128:
        return None
    if re.fullmatch(r"[0-9]{1,10}", value):
        return min(3600, int(value))
    try:
        timestamp = parsedate_to_datetime(value)
        if timestamp.tzinfo is None:
            return None
        seconds = int((timestamp - (now or datetime.now(UTC))).total_seconds())
        return min(3600, max(0, seconds))
    except (ValueError, TypeError, OverflowError):
        return None
