#!/usr/bin/env python3
"""Run the video-management YouTube sync once per day."""

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
from services.optimized_video_sync_service import OptimizedVideoSyncService
from services.runtime_guard import release_db_session_quietly
from services.youtube_video_sync_schedule_service import load_schedule_config


PROJECT_ROOT = BACKEND_DIR.parents[1]
LOG_PATH = PROJECT_ROOT / "logs" / "youtube_video_sync_scheduler.log"
PID_PATH = PROJECT_ROOT / "runtime" / "youtube_video_sync_scheduler.pid"
SCHEDULE_TAG = "youtube-video-sync"
STOP_REQUESTED = False

LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler()],
)
LOGGER = logging.getLogger("youtube_video_sync_scheduler")


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
        LOGGER.exception("Failed to clean YouTube video sync scheduler pid file")


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
                "group_name": "YouTube 最近 3 天视频同步",
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
            },
        )
        LOGGER.info(
            "YouTube video sync notification: event=%s queued=%s reason=%s",
            event_type,
            bool(result.get("queued")),
            result.get("reason") or "-",
        )
        return result
    except Exception as exc:
        LOGGER.exception(
            "YouTube video sync notification enqueue failed: event=%s error=%s",
            event_type,
            exc,
        )
        return {"queued": False, "reason": "queue_failed", "error": str(exc)}


def _run_once(app, trigger):
    started_at = datetime.utcnow()
    attempt_id = f"youtube-video-sync-{started_at.strftime('%Y%m%dT%H%M%SZ')}"
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
                    "message": f"开始同步最近 {config['days_back']} 天的视频",
                    "days_back": config["days_back"],
                    "max_workers": config["max_workers"],
                },
                enabled=notify,
            )
            try:
                result = OptimizedVideoSyncService().sync_all_channels_videos_parallel(
                    days_back=config["days_back"],
                    user_id=None,
                    max_workers=config["max_workers"],
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
                LOGGER.exception("YouTube video sync failed: trigger=%s", trigger)
                return {"success": False, "message": str(exc)}

            data = result.get("data") or {}
            errors = int(data.get("errors") or 0)
            failures = [
                {
                    "channel_id": item.get("channel_id"),
                    "channel_name": item.get("channel_name"),
                    "message": str(item.get("message") or "同步失败")[:300],
                }
                for item in (data.get("details") or [])
                if item.get("status") == "error"
            ][:10]
            succeeded = bool(result.get("success")) and errors == 0
            status = "completed" if succeeded else (
                "partial" if result.get("success") else "failed"
            )
            summary = {
                "days_back": config["days_back"],
                "processed_channels": data.get("processed", 0),
                "new_videos": data.get("new_videos", 0),
                "updated_videos": data.get("updated_videos", 0),
                "errors": errors,
                "failures": failures,
                "requested_max_workers": config["max_workers"],
            }
            _enqueue_notification(
                "sync_completed" if succeeded else "sync_failed",
                attempt_id=attempt_id,
                trigger=trigger,
                status=status,
                summary=summary,
                error=None if succeeded else result.get("message"),
                enabled=notify,
            )
            LOGGER.info("YouTube video sync finished: %s", summary)
            if failures:
                LOGGER.warning("YouTube video sync failures: %s", failures)
            print(
                json.dumps(
                    {"success": result.get("success"), "message": result.get("message"), **summary},
                    ensure_ascii=False,
                ),
                flush=True,
            )
            return result
        finally:
            release_db_session_quietly()
            LOGGER.info(
                "YouTube video sync duration: %.2fs",
                (datetime.utcnow() - started_at).total_seconds(),
            )


def _run_scheduler():
    app = create_app()
    with app.app_context():
        config = load_schedule_config()
        release_db_session_quietly()
    if not config.get("enabled"):
        LOGGER.info("YouTube video sync scheduler disabled in system_configs")
        return 0

    job = schedule.every().day.at(config["daily_at"], config["timezone"]).do(
        _run_once,
        app,
        "scheduler",
    ).tag(SCHEDULE_TAG)
    _write_pid()
    LOGGER.info(
        "YouTube video sync scheduler started: daily_at=%s timezone=%s "
        "days_back=%s max_workers=%s notifications=%s next_run=%s",
        config["daily_at"],
        config["timezone"],
        config["days_back"],
        config["max_workers"],
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
