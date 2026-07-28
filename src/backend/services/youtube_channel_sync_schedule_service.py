"""Database-backed configuration for the daily YouTube channel snapshot sync."""

import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from models import SystemConfig, db


LOGGER = logging.getLogger("youtube_channel_sync_schedule")

CONFIG_KEY = "youtube_channel_sync.schedule"
CONFIG_TYPE = "youtube_channel_sync"
CONFIG_SCOPE = "global"
DEFAULT_SCHEDULE = {
    "enabled": True,
    "daily_at": "00:03",
    "timezone": "Asia/Shanghai",
    "notify_milestones": True,
    "use_parallel": True,
    "max_workers": 5,
    "active_only": True,
}


def _as_bool(value, fallback):
    if value is None:
        return bool(fallback)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def load_schedule_config():
    """Load and validate the global channel-sync schedule."""
    row = (
        db.session.query(SystemConfig.value, SystemConfig.status)
        .filter(
            SystemConfig.key == CONFIG_KEY,
            SystemConfig.scope == CONFIG_SCOPE,
        )
        .one_or_none()
    )
    payload = {}
    if row and row.value:
        try:
            payload = json.loads(row.value)
        except (TypeError, ValueError):
            LOGGER.warning("Invalid YouTube channel sync schedule JSON; using defaults")

    daily_at = str(payload.get("daily_at") or DEFAULT_SCHEDULE["daily_at"]).strip()
    try:
        datetime.strptime(daily_at, "%H:%M")
    except (TypeError, ValueError):
        LOGGER.warning(
            "Invalid YouTube channel sync time %r; using %s",
            daily_at,
            DEFAULT_SCHEDULE["daily_at"],
        )
        daily_at = DEFAULT_SCHEDULE["daily_at"]

    timezone_name = str(
        payload.get("timezone") or DEFAULT_SCHEDULE["timezone"]
    ).strip()
    try:
        ZoneInfo(timezone_name)
    except Exception:
        LOGGER.warning(
            "Invalid YouTube channel sync timezone %r; using %s",
            timezone_name,
            DEFAULT_SCHEDULE["timezone"],
        )
        timezone_name = DEFAULT_SCHEDULE["timezone"]

    try:
        max_workers = int(payload.get("max_workers", DEFAULT_SCHEDULE["max_workers"]))
    except (TypeError, ValueError):
        max_workers = DEFAULT_SCHEDULE["max_workers"]
    max_workers = max(1, min(max_workers, 10))

    return {
        "enabled": _as_bool(
            payload.get("enabled"),
            DEFAULT_SCHEDULE["enabled"],
        )
        and (row is None or int(row.status or 0) == 1),
        "daily_at": daily_at,
        "timezone": timezone_name,
        "notify_milestones": _as_bool(
            payload.get("notify_milestones"),
            DEFAULT_SCHEDULE["notify_milestones"],
        ),
        "use_parallel": _as_bool(
            payload.get("use_parallel"),
            DEFAULT_SCHEDULE["use_parallel"],
        ),
        "max_workers": max_workers,
        "active_only": _as_bool(
            payload.get("active_only"),
            DEFAULT_SCHEDULE["active_only"],
        ),
    }


__all__ = [
    "CONFIG_KEY",
    "CONFIG_SCOPE",
    "CONFIG_TYPE",
    "DEFAULT_SCHEDULE",
    "load_schedule_config",
]
