"""Configuration and process controls for the hourly Bilibili intelligence sync."""

import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from models import SystemConfig, db


PROJECT_ROOT = Path(__file__).resolve().parents[3]
RUNTIME_DIR = PROJECT_ROOT / "runtime"
PID_PATH = RUNTIME_DIR / "bilibili_intelligence_scheduler.pid"
LOG_PATH = PROJECT_ROOT / "logs" / "bilibili_intelligence_scheduler.log"
RUNNER_PATH = PROJECT_ROOT / "src" / "backend" / "run_bilibili_intelligence_scheduler.py"

CONFIG_KEY = "bilibili_intelligence_sync.schedule"
CONFIG_TYPE = "bilibili_intelligence_sync"
CONFIG_SCOPE = "global"
DEFAULT_SCHEDULE = {
    "enabled": True,
    "interval_minutes": 60,
    "timezone": "Asia/Shanghai",
    "page_size": 30,
    "notify_milestones": True,
}


def _as_bool(value, fallback):
    if value is None:
        return bool(fallback)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _bounded_int(value, default, lower, upper):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(lower, min(upper, parsed))


def load_schedule_config():
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
            payload = {}

    timezone_name = str(
        payload.get("timezone") or DEFAULT_SCHEDULE["timezone"]
    ).strip()
    try:
        ZoneInfo(timezone_name)
    except Exception:
        timezone_name = DEFAULT_SCHEDULE["timezone"]

    return {
        "enabled": _as_bool(
            payload.get("enabled"),
            DEFAULT_SCHEDULE["enabled"],
        ) and (row is None or int(row.status or 0) == 1),
        "interval_minutes": _bounded_int(
            payload.get("interval_minutes"),
            DEFAULT_SCHEDULE["interval_minutes"],
            1,
            1440,
        ),
        "timezone": timezone_name,
        "page_size": _bounded_int(
            payload.get("page_size"),
            DEFAULT_SCHEDULE["page_size"],
            1,
            50,
        ),
        "notify_milestones": _as_bool(
            payload.get("notify_milestones"),
            DEFAULT_SCHEDULE["notify_milestones"],
        ),
    }


def _iso(value):
    if not value:
        return None
    return value.isoformat() + ("Z" if value.tzinfo is None else "")


def _source_result(item, *, failed=False):
    item = item if isinstance(item, dict) else {}
    keys = (
        "competitor_id",
        "competitor_name",
        "uid",
        "fetched",
        "created",
        "created_titles",
        "updated",
        "video_count",
        "latest_published_at",
        "error",
    )
    result = {key: item.get(key) for key in keys if item.get(key) is not None}
    if failed and "error" not in result:
        result["error"] = "未知同步错误"
    return result


def build_sync_report(sync_result, *, started_at, completed_at, trigger):
    """Normalize one sync result for logs and email notifications."""
    sync_result = sync_result if isinstance(sync_result, dict) else {}
    succeeded = [_source_result(item) for item in sync_result.get("results") or []]
    failed = [_source_result(item, failed=True) for item in sync_result.get("errors") or []]
    duration = max(0.0, (completed_at - started_at).total_seconds())
    status = "completed" if not failed else ("partial" if succeeded else "failed")

    return {
        "run_id": f"bilibili-intelligence-{started_at.strftime('%Y%m%dT%H%M%SZ')}",
        "trigger": trigger,
        "status": status,
        "started_at": _iso(started_at),
        "completed_at": _iso(completed_at),
        "duration_seconds": round(duration, 3),
        "total_competitors": len(succeeded) + len(failed),
        "succeeded_count": len(succeeded),
        "failed_count": len(failed),
        "fetched": sum(int(item.get("fetched") or 0) for item in succeeded),
        "created": sum(int(item.get("created") or 0) for item in succeeded),
        "updated": sum(int(item.get("updated") or 0) for item in succeeded),
        "succeeded": succeeded,
        "failed": failed,
    }


def _read_pid():
    try:
        return json.loads(PID_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _is_running(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, TypeError, ValueError):
        return False


def scheduler_status():
    payload = _read_pid()
    pid = payload.get("pid")
    running = bool(pid and _is_running(pid))
    if pid and not running:
        PID_PATH.unlink(missing_ok=True)
    return {
        "running": running,
        "pid": int(pid) if running else None,
        "started_at": payload.get("started_at") if running else None,
        "log_path": str(LOG_PATH),
        "message": "运行中" if running else "未运行",
    }


def start_scheduler():
    current = scheduler_status()
    if current["running"]:
        return {"started": False, "running": True, "scheduler": current}

    env = dict(os.environ)
    env["DB_PROCESS_ROLE"] = "scheduler"
    env["BACKEND_PROCESS_BOOTSTRAP_ENABLED"] = "0"
    process = subprocess.Popen(
        [sys.executable, str(RUNNER_PATH), "start"],
        cwd=str(PROJECT_ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )
    deadline = time.time() + 8
    status = scheduler_status()
    while time.time() < deadline:
        if process.poll() is not None or not _is_running(process.pid):
            break
        status = scheduler_status()
        if status.get("running") and status.get("pid") == process.pid:
            break
        time.sleep(0.2)
    running = bool(
        process.poll() is None
        and status.get("running")
        and status.get("pid") == process.pid
    )
    return {
        "started": running,
        "running": running,
        "scheduler": status if running else scheduler_status(),
    }


def stop_scheduler():
    current = scheduler_status()
    pid = current.get("pid")
    if not pid:
        return {"stopped": True, "running": False, "scheduler": current}
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    deadline = time.time() + 10
    while time.time() < deadline and _is_running(pid):
        time.sleep(0.2)
    running = _is_running(pid)
    if not running:
        PID_PATH.unlink(missing_ok=True)
    return {
        "stopped": not running,
        "running": running,
        "scheduler": scheduler_status(),
    }


def restart_scheduler():
    stopped = stop_scheduler()
    if stopped.get("running"):
        return {"restarted": False, "running": True, "stop": stopped}
    started = start_scheduler()
    return {
        "restarted": bool(started.get("running")),
        "running": bool(started.get("running")),
        "stop": stopped,
        "start": started,
        "scheduler": started.get("scheduler"),
    }


__all__ = [
    "CONFIG_KEY",
    "CONFIG_SCOPE",
    "CONFIG_TYPE",
    "DEFAULT_SCHEDULE",
    "build_sync_report",
    "load_schedule_config",
    "restart_scheduler",
    "scheduler_status",
    "start_scheduler",
    "stop_scheduler",
]
