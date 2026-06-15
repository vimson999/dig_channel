#!/usr/bin/env python3
"""Scheduler for content retention cleanup."""

import argparse
import json
import logging
import os
import signal
import sys
import time
from pathlib import Path

import schedule


BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app import create_app
from services.content_pipeline.retention_cleanup_service import (
    ContentPipelineRetentionCleanupService,
    ContentRetentionPolicy,
)
from services.content_pipeline.retention_scheduler_service import (
    DRY_RUN_OVERRIDE_ENV,
    LOG_PATH,
    load_config,
    next_run_iso,
    update_runtime_status,
    utc_now_iso,
)


LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("content_retention_cleanup_scheduler")


class ContentRetentionCleanupScheduler:
    def __init__(self):
        self.app = create_app()
        self.running = False
        self.interval_hours = self._interval_hours()
        self._scheduled_interval = None

    def cleanup_once(self, trigger="scheduler", dry_run_override=None):
        started_at = time.monotonic()
        config = load_config()
        if not config.get("enabled", True):
            result = {
                "queued": False,
                "skipped": True,
                "reason": "disabled",
                "message": "历史文件清理计划已禁用。",
                "config": config,
            }
            logger.info("[RETENTION_CLEANUP] skipped disabled | trigger=%s", trigger)
            return result

        dry_run = config.get("dry_run", True) if dry_run_override is None else bool(dry_run_override)
        policy = ContentRetentionPolicy.from_payload(config.get("policy") or {})
        logger.info(
            "[RETENTION_CLEANUP] trigger_start | trigger=%s dry_run=%s interval_hours=%s policy=%s",
            trigger,
            dry_run,
            config.get("interval_hours"),
            policy.to_dict(),
        )
        try:
            with self.app.app_context():
                service = ContentPipelineRetentionCleanupService()
                cleanup_result = service.run(policy=policy, dry_run=dry_run)

            summary = {
                "trigger": trigger,
                "dry_run": bool(dry_run),
                "candidate_count": cleanup_result.get("candidate_count", 0),
                "total_bytes": cleanup_result.get("total_bytes", 0),
                "total_human": cleanup_result.get("total_human", "0 B"),
                "deleted_count": cleanup_result.get("deleted_count", 0),
                "deleted_bytes": cleanup_result.get("deleted_bytes", 0),
                "deleted_human": cleanup_result.get("deleted_human", "0 B"),
                "error_count": len(cleanup_result.get("errors") or []),
                "completed_at": utc_now_iso(),
                "elapsed_seconds": round(time.monotonic() - started_at, 2),
            }
            update_runtime_status(
                {
                    "last_run_at": summary["completed_at"],
                    "last_result": summary,
                    "next_run_at": next_run_iso(config.get("interval_hours") or self.interval_hours),
                }
            )
            logger.info(
                "[RETENTION_CLEANUP] trigger_done | trigger=%s dry_run=%s candidates=%s total=%s deleted=%s deleted_size=%s errors=%s elapsed=%.2fs",
                trigger,
                dry_run,
                summary["candidate_count"],
                summary["total_human"],
                summary["deleted_count"],
                summary["deleted_human"],
                summary["error_count"],
                summary["elapsed_seconds"],
            )
            return {
                "queued": False,
                "skipped": False,
                "success": summary["error_count"] == 0,
                "message": "历史文件清理预览完成。" if dry_run else "历史文件清理执行完成。",
                "summary": summary,
                "result": cleanup_result,
                "config": load_config(),
            }
        except Exception as exc:
            summary = {
                "trigger": trigger,
                "dry_run": bool(dry_run),
                "error": str(exc),
                "error_count": 1,
                "completed_at": utc_now_iso(),
                "elapsed_seconds": round(time.monotonic() - started_at, 2),
            }
            update_runtime_status({"last_run_at": summary["completed_at"], "last_result": summary})
            logger.exception("[RETENTION_CLEANUP] trigger_failed | trigger=%s error=%s", trigger, exc)
            return {
                "queued": False,
                "skipped": True,
                "success": False,
                "reason": "error",
                "message": str(exc),
                "summary": summary,
                "config": load_config(),
            }

    def refresh_schedule(self):
        schedule.clear("content-retention-cleanup")
        self.interval_hours = self._interval_hours()
        job = schedule.every(self.interval_hours).hours.do(self.cleanup_once).tag("content-retention-cleanup")
        self._scheduled_interval = self.interval_hours
        update_runtime_status({"next_run_at": next_run_iso(self.interval_hours)})
        logger.info(
            "历史文件清理计划已设置：每 %s 小时执行一次 | next_run=%s",
            self.interval_hours,
            getattr(job, "next_run", None),
        )

    def start(self):
        config = load_config()
        self.running = True
        self.refresh_schedule()
        logger.info("历史文件清理调度器启动")
        if config.get("run_on_start"):
            self.cleanup_once(trigger="scheduler_start")

        while self.running:
            current_interval = self._interval_hours()
            if current_interval != self._scheduled_interval:
                self.refresh_schedule()
            due_count = sum(1 for job in schedule.get_jobs("content-retention-cleanup") if job.should_run)
            schedule.run_pending()
            if due_count:
                update_runtime_status({"next_run_at": next_run_iso(self.interval_hours)})
            time.sleep(30)

    def stop(self):
        self.running = False
        schedule.clear("content-retention-cleanup")
        update_runtime_status({"next_run_at": None})
        logger.info("历史文件清理调度器停止")

    def _interval_hours(self):
        config = load_config()
        return int(config.get("interval_hours") or 24)


def _dry_run_override_from_env():
    value = os.getenv(DRY_RUN_OVERRIDE_ENV)
    if value is None:
        return None
    return str(value).strip().lower() not in {"0", "false", "no", "off"}


def main():
    parser = argparse.ArgumentParser(description="历史文件保留期清理调度器")
    parser.add_argument("command", choices=["start", "once", "status"])
    args = parser.parse_args()

    service = ContentRetentionCleanupScheduler()

    def signal_handler(signum, frame):
        logger.info("收到信号 %s，准备停止", signum)
        service.stop()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    if args.command == "start":
        service.start()
    elif args.command == "once":
        result = service.cleanup_once(trigger="manual_cli", dry_run_override=_dry_run_override_from_env())
        print(json.dumps(result, ensure_ascii=False))
    elif args.command == "status":
        print(json.dumps(load_config(), ensure_ascii=False))


if __name__ == "__main__":
    main()
