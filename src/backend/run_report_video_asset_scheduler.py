#!/usr/bin/env python3
"""Scheduler for ZSXQ report PDF asset backfill scans."""

import argparse
import json
import logging
import os
import signal
import sys
import time
from datetime import timezone
from pathlib import Path

import schedule


BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app import create_app
from services.report_video_asset_scheduler_service import (
    LOG_PATH,
    build_daily_backfill_payload,
    load_config,
    next_interval_run_iso,
    update_runtime_status,
    utc_now_iso,
)
from services.report_video_asset_service import preview_zsxq_report_assets
from tasks.report_video import scan_zsxq_report_assets_task


LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("report_video_asset_scheduler")


class ReportVideoAssetScheduler:
    def __init__(self):
        self.app = create_app()
        self.running = False
        self._schedule_signature = None

    def queue_scan(self, trigger="scheduler", target_date=None):
        started_at = time.monotonic()
        config = load_config()
        if not config.get("enabled", True):
            result = {
                "queued": False,
                "skipped": True,
                "reason": "disabled",
                "message": "研报资产补漏计划已禁用。",
                "config": config,
            }
            logger.info("[REPORT_ASSET_BACKFILL] skipped disabled | trigger=%s", trigger)
            return result

        payload = build_daily_backfill_payload(config, trigger=trigger, target_date=target_date)
        logger.info(
            "[REPORT_ASSET_BACKFILL] trigger_start | trigger=%s target_date=%s max_files=%s parse_pdfs=%s",
            trigger,
            payload.get("target_date"),
            (payload.get("config") or {}).get("max_files"),
            payload.get("parse_pdfs"),
        )
        try:
            with self.app.app_context():
                preview = preview_zsxq_report_assets(payload)
                if not preview.get("new_count"):
                    summary = self._summary(trigger, payload, preview, queued=False, celery_id=None, started_at=started_at)
                    update_runtime_status({
                        "last_run_at": summary["completed_at"],
                        "last_result": summary,
                        "next_run_at": self._next_run_iso(config),
                    })
                    logger.info(
                        "[REPORT_ASSET_BACKFILL] no_new_assets | trigger=%s target_date=%s candidates=%s existing=%s",
                        trigger,
                        payload.get("target_date"),
                        preview.get("candidate_count"),
                        preview.get("existing_count"),
                    )
                    return {
                        "queued": False,
                        "skipped": False,
                        "success": True,
                        "message": "未发现新的研报 PDF 资产。",
                        "summary": summary,
                        "preview": preview,
                        "config": load_config(),
                    }

                celery_task = scan_zsxq_report_assets_task.apply_async(args=[payload, False], queue="report_video")

            summary = self._summary(trigger, payload, preview, queued=True, celery_id=celery_task.id, started_at=started_at)
            update_runtime_status({
                "last_run_at": summary["completed_at"],
                "last_result": summary,
                "next_run_at": self._next_run_iso(config),
            })
            logger.info(
                "[REPORT_ASSET_BACKFILL] queued | trigger=%s target_date=%s celery_id=%s candidates=%s new=%s existing=%s",
                trigger,
                payload.get("target_date"),
                celery_task.id,
                preview.get("candidate_count"),
                preview.get("new_count"),
                preview.get("existing_count"),
            )
            return {
                "queued": True,
                "skipped": False,
                "success": True,
                "message": "研报资产补漏扫描已入队。",
                "summary": summary,
                "preview": preview,
                "celery_id": celery_task.id,
                "config": load_config(),
            }
        except Exception as exc:
            summary = {
                "trigger": trigger,
                "target_date": payload.get("target_date"),
                "queued": False,
                "error": str(exc),
                "error_count": 1,
                "completed_at": utc_now_iso(),
                "elapsed_seconds": round(time.monotonic() - started_at, 2),
            }
            update_runtime_status({"last_run_at": summary["completed_at"], "last_result": summary})
            logger.exception("[REPORT_ASSET_BACKFILL] failed | trigger=%s error=%s", trigger, exc)
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
        config = load_config()
        signature = self._config_signature(config)
        if signature == self._schedule_signature:
            return

        schedule.clear("report-video-asset-backfill")
        self._schedule_signature = signature
        if config.get("interval_minutes"):
            minutes = max(1, int(config["interval_minutes"]))
            job = schedule.every(minutes).minutes.do(self.queue_scan).tag("report-video-asset-backfill")
            update_runtime_status({"next_run_at": next_interval_run_iso(minutes)})
            logger.info("研报资产补漏计划已设置：每 %s 分钟执行一次 | next_run=%s", minutes, getattr(job, "next_run", None))
        else:
            at_time = str(config.get("schedule_time") or "02:30")
            job = schedule.every().day.at(at_time).do(self.queue_scan).tag("report-video-asset-backfill")
            update_runtime_status({"next_run_at": _job_next_run_iso(job)})
            logger.info("研报资产补漏计划已设置：每天 %s 执行 | next_run=%s", at_time, getattr(job, "next_run", None))

    def start(self):
        config = load_config()
        self.running = True
        self.refresh_schedule()
        logger.info("研报资产补漏调度器启动")
        if config.get("run_on_start"):
            target_date = os.getenv("REPORT_ASSET_BACKFILL_TARGET_DATE") or None
            self.queue_scan(trigger="scheduler_start", target_date=target_date)

        while self.running:
            self.refresh_schedule()
            due_count = sum(1 for job in schedule.get_jobs("report-video-asset-backfill") if job.should_run)
            schedule.run_pending()
            if due_count:
                self.refresh_schedule()
            time.sleep(30)

    def stop(self):
        self.running = False
        schedule.clear("report-video-asset-backfill")
        update_runtime_status({"next_run_at": None})
        logger.info("研报资产补漏调度器停止")

    def _config_signature(self, config):
        nested = config.get("config") or {}
        return "|".join([
            str(config.get("enabled")),
            str(config.get("schedule_time")),
            str(config.get("interval_minutes")),
            str(config.get("run_on_start")),
            str(config.get("parse_pdfs")),
            str(config.get("target_date_offset_days")),
            str(nested.get("max_files")),
            str(nested.get("tab_label_keyword")),
        ])

    def _summary(self, trigger, payload, preview, *, queued, celery_id, started_at):
        return {
            "trigger": trigger,
            "source": payload.get("source"),
            "target_date": payload.get("target_date"),
            "parse_pdfs": payload.get("parse_pdfs"),
            "queued": bool(queued),
            "celery_id": celery_id,
            "candidate_count": preview.get("candidate_count", 0),
            "new_count": preview.get("new_count", 0),
            "existing_count": preview.get("existing_count", 0),
            "selected_count": preview.get("selected_count", 0),
            "completed_at": utc_now_iso(),
            "elapsed_seconds": round(time.monotonic() - started_at, 2),
        }

    def _next_run_iso(self, config):
        if config.get("interval_minutes"):
            return next_interval_run_iso(config["interval_minutes"])
        jobs = schedule.get_jobs("report-video-asset-backfill")
        return _job_next_run_iso(jobs[0]) if jobs else None


def _job_next_run_iso(job):
    next_run = getattr(job, "next_run", None)
    if not next_run:
        return None
    return next_run.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


def main():
    parser = argparse.ArgumentParser(description="知识星球研报资产补漏调度器")
    parser.add_argument("command", choices=["start", "once", "status"])
    args = parser.parse_args()

    service = ReportVideoAssetScheduler()

    def signal_handler(signum, frame):
        logger.info("收到信号 %s，准备停止", signum)
        service.stop()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    if args.command == "start":
        service.start()
    elif args.command == "once":
        target_date = os.getenv("REPORT_ASSET_BACKFILL_TARGET_DATE") or None
        result = service.queue_scan(trigger="manual_cli", target_date=target_date)
        print(json.dumps(result, ensure_ascii=False))
    elif args.command == "status":
        print(json.dumps(load_config(), ensure_ascii=False))


if __name__ == "__main__":
    main()
