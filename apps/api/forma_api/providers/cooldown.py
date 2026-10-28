"""Honor provider Retry-After without retrying before the specified instant."""

from datetime import datetime, timedelta, timezone
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


def with_cooldown(error, raw, *, now=None):
    value = raw.get("retry_after")
    if error.category == "quota" and not value:
        # OpenRouter's free-model allowance is daily. With no Retry-After,
        # wait until the next UTC day plus a short propagation margin. A
        # provider-supplied deadline always takes precedence.
        current = now or datetime.now(timezone.utc)
        midnight = (current + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        error.retry_after_seconds = math.ceil((midnight - current).total_seconds()) + 60
    else:
        error.retry_after_seconds = retry_after_seconds(value, now=now)
    return error
