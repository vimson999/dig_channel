#!/usr/bin/env python3
"""Run the homepage YouTube channel snapshot sync once per day."""

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
from services.optimized_channel_sync_service import OptimizedChannelSyncService
from services.runtime_guard import release_db_session_quietly
from services.youtube_channel_sync_schedule_service import load_schedule_config


PROJECT_ROOT = BACKEND_DIR.parents[1]
LOG_PATH = PROJECT_ROOT / "logs" / "youtube_channel_sync_scheduler.log"
PID_PATH = PROJECT_ROOT / "runtime" / "youtube_channel_sync_scheduler.pid"
SCHEDULE_TAG = "youtube-channel-sync"
STOP_REQUESTED = False

LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler()],
)
LOGGER = logging.getLogger("youtube_channel_sync_scheduler")


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
        LOGGER.exception("Failed to clean YouTube channel sync scheduler pid file")


def _notification_payload(attempt_id, trigger, status, summary=None, error=None):
    return {
        "id": attempt_id,
        "batch_id": attempt_id,
        "group_name": "YouTube 频道数据同步",
        "trigger": trigger,
        "status": status,
        "current_stage": "sync" if status != "completed" else "done",
        "error_message": str(error or "")[:1000] or None,
        "stage_results": {
            "sync": {
                "status": status,
                "summary": summary or {},
            },
        },
    }


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
            _notification_payload(
                attempt_id,
                trigger,
                status,
                summary=summary,
                error=error,
            ),
        )
        LOGGER.info(
            "YouTube sync notification: event=%s queued=%s reason=%s",
            event_type,
            bool(result.get("queued")),
            result.get("reason") or "-",
        )
        return result
    except Exception as exc:
        LOGGER.exception(
            "YouTube sync notification enqueue failed: event=%s error=%s",
            event_type,
            exc,
        )
        return {"queued": False, "reason": "queue_failed", "error": str(exc)}


def _run_once(app, trigger):
    started_at = datetime.utcnow()
    attempt_id = f"youtube-channel-sync-{started_at.strftime('%Y%m%dT%H%M%SZ')}"
    with app.app_context():
        try:
            config = load_schedule_config()
            release_db_session_quietly()
            notify = config.get("notify_milestones", True)
            _enqueue_notification(
                "workflow_started",
                attempt_id=attempt_id,
                trigger=trigger,
                status="running",
                summary={
                    "message": "开始同步首页全部启用 YouTube 频道",
                    "max_workers": config["max_workers"],
                },
                enabled=notify,
            )
            try:
                service = OptimizedChannelSyncService()
                result = service.sync_all_channels_parallel(
                    target_date=None,
                    max_workers=config["max_workers"],
                    active_only=config["active_only"],
                )
            except Exception as exc:
                release_db_session_quietly()
                _enqueue_notification(
                    "sync_failed",
                    attempt_id=attempt_id,
                    trigger=trigger,
                    status="failed",
                    error=exc,
                    enabled=notify,
                )
                LOGGER.exception("YouTube channel sync failed: trigger=%s", trigger)
                return {"success": False, "message": str(exc)}

            data = result.get("data") or {}
            failed_count = int(data.get("failed_count") or 0)
            failures = [
                {
                    "channel_id": item.get("channel_id"),
                    "channel_name": item.get("channel_name"),
                    "message": str(item.get("message") or "同步失败")[:300],
                }
                for item in (data.get("results") or [])
                if not item.get("success")
            ][:10]
            succeeded = bool(result.get("success")) and failed_count == 0
            status = "completed" if succeeded else (
                "partial" if result.get("success") else "failed"
            )
            _enqueue_notification(
                "sync_completed" if succeeded else "sync_failed",
                attempt_id=attempt_id,
                trigger=trigger,
                status=status,
                summary={
                    "target_date": datetime.now().date().isoformat(),
                    "total_channels": data.get("total_channels", 0),
                    "success_count": data.get("success_count", 0),
                    "failed_count": failed_count,
                    "failures": failures,
                    "max_workers": data.get("max_workers", config["max_workers"]),
                    "duration_seconds": data.get("duration_seconds"),
                },
                error=None if succeeded else result.get("message"),
                enabled=notify,
            )
            LOGGER.info(
                "YouTube channel sync finished: total=%s success=%s failed=%s duration=%s",
                data.get("total_channels", 0),
                data.get("success_count", 0),
                failed_count,
                data.get("duration_seconds"),
            )
            if failures:
                LOGGER.warning("YouTube channel sync failures: %s", failures)
            print(json.dumps(result, ensure_ascii=False), flush=True)
            return result
        finally:
            release_db_session_quietly()
            LOGGER.info(
                "YouTube channel sync duration: %.2fs",
                (datetime.utcnow() - started_at).total_seconds(),
            )


def _run_scheduler():
    app = create_app()
    with app.app_context():
        config = load_schedule_config()
        release_db_session_quietly()
    if not config.get("enabled"):
        LOGGER.info("YouTube channel sync scheduler disabled in system_configs")
        return 0

    job = schedule.every().day.at(config["daily_at"], config["timezone"]).do(
        _run_once,
        app,
        "scheduler",
    ).tag(SCHEDULE_TAG)
    _write_pid()
    LOGGER.info(
        "YouTube channel sync scheduler started: daily_at=%s timezone=%s "
        "max_workers=%s active_only=%s notifications=%s next_run=%s",
        config["daily_at"],
        config["timezone"],
        config["max_workers"],
        config["active_only"],
        config["notify_milestones"],
        job.next_run.isoformat(),
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
        return 0 if _run_once(create_app(), "scheduler_once").get("success") else 1
    try:
        return _run_scheduler()
    finally:
        schedule.clear(SCHEDULE_TAG)
        _remove_pid()


if __name__ == "__main__":
    raise SystemExit(main())
