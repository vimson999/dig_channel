#!/usr/bin/env python3
import argparse
import logging
import signal
import sys
import time
from pathlib import Path

import schedule

BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app import create_app
from services.report_video_auto_service import load_auto_scan_config, auto_scan_preview
from tasks.report_video import scan_report_video_folder_task


PROJECT_ROOT = BACKEND_DIR.parents[1]
LOG_PATH = PROJECT_ROOT / "logs" / "report_video_auto_scheduler.log"
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("report_video_auto_scheduler")


class ReportVideoAutoScheduler:
    def __init__(self):
        self.app = create_app()
        self.running = False
        self.config_signature = None

    def _config_signature(self, config):
        return "|".join([
            str(config.get("enabled")),
            str(config.get("scan_dir")),
            str(config.get("schedule_time")),
            str(config.get("interval_minutes")),
            str(config.get("max_files_per_run")),
            ",".join(config.get("output_ratios") or []),
        ])

    def queue_scan(self, trigger="scheduler"):
        with self.app.app_context():
            config = load_auto_scan_config()
            if not config.get("enabled"):
                logger.info("自动扫描未启用，跳过本次触发")
                return
            preview = auto_scan_preview(config)
            if not preview.get("new_count"):
                logger.info("未发现待制作 PDF | dir=%s total=%s", config.get("scan_dir_abs"), preview.get("total_pdf_count"))
                return
            celery_task = scan_report_video_folder_task.apply_async(args=[None, False, trigger], queue="report_video")
            logger.info(
                "已投递研报自动扫描 | celery_id=%s dir=%s new=%s max=%s",
                celery_task.id,
                config.get("scan_dir_abs"),
                preview.get("new_count"),
                config.get("max_files_per_run"),
            )

    def refresh_schedule(self):
        with self.app.app_context():
            config = load_auto_scan_config()
        signature = self._config_signature(config)
        if signature == self.config_signature:
            return

        schedule.clear("report-video-auto-scan")
        self.config_signature = signature

        if config.get("interval_minutes"):
            minutes = max(1, int(config["interval_minutes"]))
            schedule.every(minutes).minutes.do(self.queue_scan).tag("report-video-auto-scan")
            logger.info("研报自动扫描计划已设置：每 %s 分钟执行一次", minutes)
        else:
            at_time = str(config.get("schedule_time") or "00:00")
            schedule.every().day.at(at_time).do(self.queue_scan).tag("report-video-auto-scan")
            logger.info("研报自动扫描计划已设置：每天 %s 执行", at_time)

    def start(self):
        self.running = True
        self.refresh_schedule()
        logger.info("研报自动扫描调度器启动")
        while self.running:
            self.refresh_schedule()
            schedule.run_pending()
            time.sleep(30)

    def stop(self):
        self.running = False
        schedule.clear("report-video-auto-scan")
        logger.info("研报自动扫描调度器停止")


def main():
    parser = argparse.ArgumentParser(description="研报视频自动扫描调度器")
    parser.add_argument("command", choices=["start", "once", "preview"], help="start: 启动调度；once: 立即投递一次；preview: 仅预览")
    args = parser.parse_args()

    service = ReportVideoAutoScheduler()

    def signal_handler(signum, frame):
        logger.info("收到信号 %s，准备停止", signum)
        service.stop()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    if args.command == "start":
        service.start()
    elif args.command == "once":
        service.queue_scan(trigger="manual_cli")
    elif args.command == "preview":
        with service.app.app_context():
            preview = auto_scan_preview(load_auto_scan_config())
        logger.info("扫描预览：%s", preview)


if __name__ == "__main__":
    main()
