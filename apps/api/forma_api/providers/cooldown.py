"""Honor provider Retry-After without retrying before the specified instant."""

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import math


def retry_after_seconds(value, *, now=None, default=60):
    if value is None:
        return default
    try:
        seconds = float(value)
        if math.isfinite(seconds):
            return max(1, math.ceil(seconds))
    except (TypeError, ValueError):
        pass
    try:
        when = parsedate_to_datetime(str(value))
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return max(1, math.ceil((when - (now or datetime.now(timezone.utc))).total_seconds()))
    except (TypeError, ValueError, OverflowError):
        return default


def with_cooldown(error, raw):
    error.retry_after_seconds = retry_after_seconds(raw.get("retry_after"))
    return error
