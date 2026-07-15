from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


SHANGHAI_TIMEZONE = ZoneInfo("Asia/Shanghai")


def submission_date_utc_range(value):
    """Return naive UTC bounds for one Asia/Shanghai calendar date."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        local_day = datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        return None
    local_start = local_day.replace(tzinfo=SHANGHAI_TIMEZONE)
    local_end = local_start + timedelta(days=1)
    return (
        local_start.astimezone(timezone.utc).replace(tzinfo=None),
        local_end.astimezone(timezone.utc).replace(tzinfo=None),
    )
