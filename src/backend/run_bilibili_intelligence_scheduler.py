#!/usr/bin/env python3
"""Run the hourly Bilibili intelligence sync and notify its result."""

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
from services.bilibili_intelligence_scheduler_service import (
    build_sync_report,
    load_schedule_config,
)
from services.bilibili_intelligence_service import sync_all_competitors
from services.runtime_guard import release_db_session_quietly
from services.workflow_notification_service import workflow_notification_service


PROJECT_ROOT = BACKEND_DIR.parents[1]
LOG_PATH = PROJECT_ROOT / "logs" / "bilibili_intelligence_scheduler.log"
PID_PATH = PROJECT_ROOT / "runtime" / "bilibili_intelligence_scheduler.pid"
LOCK_PATH = PROJECT_ROOT / "runtime" / "bilibili_intelligence_scheduler.lock"
SCHEDULE_TAG = "bilibili-intelligence-sync"
STOP_REQUESTED = False

LOGGER = logging.getLogger("bilibili_intelligence_scheduler")


def _configure_logging():
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler()],
    )


def _acquire_singleton_lock():
    """Hold an OS lock for the lifetime of one scheduler process."""
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    handle = LOCK_PATH.open("a", encoding="utf-8")
    try:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None
    except Exception:
        handle.close()
        raise
    return handle


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
        LOGGER.exception("Failed to clean Bilibili intelligence scheduler pid file")


def _notification_payload(report):
    status = report.get("status") or "failed"
    failures = report.get("failed") or []
    failure_text = "；".join(
        f"{item.get('competitor_name') or item.get('competitor_id') or '未知监控源'}: {item.get('error') or '未知错误'}"
        for item in failures
    )
    return {
        "id": report.get("run_id"),
        "batch_id": report.get("run_id"),
        "group_name": "B站内容情报台",
        "group_label": "计划任务",
        "trigger": report.get("trigger") or "scheduler",
        "status": status,
        "current_stage": "done",
        "event_message": (
            f"本轮耗时 {report.get('duration_seconds', 0)} 秒，"
            f"监控源成功 {report.get('succeeded_count', 0)} 个，"
            f"失败 {report.get('failed_count', 0)} 个。"
        ),
        "error_message": failure_text or None,
        "stage_results": {
            "sync": {
                "status": status,
                "summary": {"bilibili_intelligence": report},
                "error": failure_text or None,
            }
        },
    }


def _enqueue_notification(event_type, *, report, enabled=True):
    if not enabled:
        return {"queued": False, "reason": "schedule_disabled"}
    try:
        result = workflow_notification_service.enqueue_workflow_event(
            event_type,
            _notification_payload(report),
        )
        LOGGER.info(
            "Bilibili intelligence notification: event=%s queued=%s reason=%s",
            event_type,
            bool(result.get("queued")),
            result.get("reason") or "-",
        )
        return result
    except Exception as exc:
        LOGGER.exception(
            "Bilibili intelligence notification enqueue failed: event=%s error=%s",
            event_type,
            exc,
        )
        return {"queued": False, "reason": "queue_failed", "error": str(exc)}


def _run_once(app, trigger):
    started_at = datetime.utcnow()
    with app.app_context():
        config = load_schedule_config()
        try:
            sync_result = sync_all_competitors(page_size=config["page_size"])
            completed_at = datetime.utcnow()
            report = build_sync_report(
                sync_result,
                started_at=started_at,
                completed_at=completed_at,
                trigger=trigger,
            )
        except Exception as exc:
            completed_at = datetime.utcnow()
            report = build_sync_report(
                {
                    "synced": 0,
                    "failed": 1,
                    "results": [],
                    "errors": [
                        {
                            "competitor_name": "全部监控源",
                            "error": str(exc)[:1000],
                        }
                    ],
                },
                started_at=started_at,
                completed_at=completed_at,
                trigger=trigger,
            )
            LOGGER.exception("Bilibili intelligence sync failed: trigger=%s", trigger)

        event_type = "sync_completed" if report["status"] == "completed" else "sync_failed"
        _enqueue_notification(
            event_type,
            report=report,
            enabled=config.get("notify_milestones", True),
        )
        LOGGER.info(
            "Bilibili intelligence sync finished: status=%s total=%s succeeded=%s failed=%s "
            "fetched=%s created=%s updated=%s duration=%.3fs",
            report["status"],
            report["total_competitors"],
            report["succeeded_count"],
            report["failed_count"],
            report["fetched"],
            report["created"],
            report["updated"],
            report["duration_seconds"],
        )
        print(json.dumps(report, ensure_ascii=False), flush=True)
        return report


def _run_scheduler():
    app = create_app()
    with app.app_context():
        config = load_schedule_config()
        release_db_session_quietly()
    if not config.get("enabled"):
        LOGGER.info("Bilibili intelligence scheduler disabled in system_configs")
        return 0

    interval_minutes = config["interval_minutes"]
    schedule.every(interval_minutes).minutes.do(
        _run_once,
        app,
        "scheduler",
    ).tag(SCHEDULE_TAG)
    _write_pid()
    LOGGER.info(
        "Bilibili intelligence scheduler started: interval_minutes=%s timezone=%s notifications=%s",
        interval_minutes,
        config["timezone"],
        bool(config.get("notify_milestones", True)),
    )
    while not STOP_REQUESTED:
        schedule.run_pending()
        time.sleep(5)
    return 0


def main():
    _configure_logging()
    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)
    mode = str(sys.argv[1] if len(sys.argv) > 1 else "start").strip().lower()
    if mode == "once":
        _run_once(create_app(), "scheduler_once")
        return 0
    lock_handle = _acquire_singleton_lock()
    if lock_handle is None:
        LOGGER.error("Bilibili intelligence scheduler already running; refusing duplicate instance")
        return 2
    try:
        return _run_scheduler()
    finally:
        schedule.clear(SCHEDULE_TAG)
        _remove_pid()
        lock_handle.close()


if __name__ == "__main__":
    raise SystemExit(main())
