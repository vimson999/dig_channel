#!/usr/bin/env python3
"""Periodically refresh the Bilibili charging dashboard snapshots."""

import json
import logging
import os
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

import schedule


BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app import create_app
from services.bilibili_charging_dashboard_service import (
    collect_charging_snapshots,
    load_schedule_config,
)
from services.runtime_guard import release_db_session_quietly


PROJECT_ROOT = BACKEND_DIR.parents[1]
LOG_PATH = PROJECT_ROOT / "logs" / "bilibili_charging_scheduler.log"
PID_PATH = PROJECT_ROOT / "runtime" / "bilibili_charging_scheduler.pid"
SCHEDULE_TAG = "bilibili-charging-dashboard"
STOP_REQUESTED = False

LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler()],
)
LOGGER = logging.getLogger("bilibili_charging_scheduler")


def _signal_handler(_signum, _frame):
    global STOP_REQUESTED
    STOP_REQUESTED = True


def _write_pid():
    PID_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = PID_PATH.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "started_at": datetime.utcnow().isoformat() + "Z",
                "command": " ".join(sys.argv),
                "log_path": str(LOG_PATH),
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    os.chmod(temporary, 0o600)
    temporary.replace(PID_PATH)


def _remove_pid():
    try:
        payload = json.loads(PID_PATH.read_text(encoding="utf-8"))
        if int(payload.get("pid") or 0) == os.getpid():
            PID_PATH.unlink(missing_ok=True)
    except FileNotFoundError:
        return
    except Exception:
        LOGGER.exception("Failed to clean Bilibili charging scheduler pid file")


def _run_once(app, trigger):
    started_at = datetime.utcnow()
    attempt_id = f"bilibili-charging-{started_at.strftime('%Y%m%dT%H%M%SZ')}"
    with app.app_context():
        try:
            config = load_schedule_config()
            _enqueue_notification(
                "workflow_started",
                attempt_id=attempt_id,
                trigger=trigger,
                status="running",
                summary={"message": "开始采集 B站 UP 主充电人数"},
                enabled=config.get("notify_milestones", True),
            )
            try:
                result = collect_charging_snapshots(trigger=trigger)
            except Exception as exc:
                release_db_session_quietly()
                _enqueue_notification(
                    "sync_failed",
                    attempt_id=attempt_id,
                    trigger=trigger,
                    status="failed",
                    error=exc,
                    enabled=config.get("notify_milestones", True),
                )
                LOGGER.exception("Charging snapshot failed: trigger=%s", trigger)
                return {
                    "trigger": trigger,
                    "synced": 0,
                    "failed": 1,
                    "errors": [{"error": str(exc)[:1000]}],
                }

            event_type = "sync_failed" if result.get("failed") else "sync_completed"
            status = "partial" if result.get("failed") else "completed"
            _enqueue_notification(
                event_type,
                attempt_id=attempt_id,
                trigger=trigger,
                status=status,
                summary={
                    "snapshot_date": result.get("snapshot_date"),
                    "synced": result.get("synced"),
                    "failed": result.get("failed"),
                    "accounts": [
                        {
                            "name": item.get("name"),
                            "charger_count": item.get("charger_count"),
                        }
                        for item in result.get("results") or []
                    ],
                },
                error="；".join(
                    f"{item.get('name')}: {item.get('error')}"
                    for item in result.get("errors") or []
                ) or None,
                enabled=config.get("notify_milestones", True),
            )
            LOGGER.info(
                "Charging snapshot finished: synced=%s failed=%s date=%s",
                result.get("synced"),
                result.get("failed"),
                result.get("snapshot_date"),
            )
            print(json.dumps(result, ensure_ascii=False), flush=True)
            return result
        finally:
            release_db_session_quietly()
            LOGGER.info(
                "Charging snapshot duration: %.2fs",
                (datetime.utcnow() - started_at).total_seconds(),
            )


def _enqueue_notification(
    event_type,
    *,
    attempt_id,
    trigger,
    status,
    summary=None,
    error=None,
    enabled=True,
):
    if not enabled:
        return {"queued": False, "reason": "schedule_disabled"}
    try:
        from services.workflow_notification_service import workflow_notification_service

        result = workflow_notification_service.enqueue_workflow_event(
            event_type,
            {
                "id": attempt_id,
                "batch_id": attempt_id,
                "group_name": "B站充电人数看板",
                "trigger": trigger,
                "status": status,
                "current_stage": "sync" if status not in {"completed"} else "done",
                "error_message": str(error or "")[:1000] or None,
                "stage_results": {
                    "sync": {
                        "status": status,
                        "summary": summary or {},
                    },
                },
            },
        )
        LOGGER.info(
            "Charging notification: event=%s queued=%s reason=%s",
            event_type,
            bool(result.get("queued")),
            result.get("reason") or "-",
        )
        return result
    except Exception as exc:
        LOGGER.exception(
            "Charging notification enqueue failed: event=%s error=%s",
            event_type,
            exc,
        )
        return {"queued": False, "reason": "queue_failed", "error": str(exc)}


def _run_scheduler():
    app = create_app()
    with app.app_context():
        config = load_schedule_config()
        release_db_session_quietly()
    if not config.get("enabled"):
        LOGGER.info("Bilibili charging scheduler disabled in system_configs")
        return 0

    daily_at = config["daily_at"]
    timezone_name = config["timezone"]
    schedule.every().day.at(daily_at, timezone_name).do(
        _run_once,
        app,
        "scheduler",
    ).tag(SCHEDULE_TAG)
    _write_pid()
    LOGGER.info(
        "Bilibili charging scheduler started: daily_at=%s timezone=%s notifications=%s",
        daily_at,
        timezone_name,
        bool(config.get("notify_milestones", True)),
    )
    while not STOP_REQUESTED:
        schedule.run_pending()
        time.sleep(5)
    return 0


def main():
    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)
    mode = str(sys.argv[1] if len(sys.argv) > 1 else "start").strip().lower()
    if mode == "once":
        app = create_app()
        _run_once(app, "scheduler_once")
        return 0
    try:
        return _run_scheduler()
    finally:
        schedule.clear(SCHEDULE_TAG)
        _remove_pid()


if __name__ == "__main__":
    raise SystemExit(main())
