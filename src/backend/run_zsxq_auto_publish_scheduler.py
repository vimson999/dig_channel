#!/usr/bin/env python3
"""Local scheduler for ZSXQ auto publishing."""

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
from services.zsxq_auto_publish_service import (
    COOKIE_ENV_NAME,
    config_status,
    run_configured_once,
)

DEFAULT_PROJECT_ROOT = BACKEND_DIR.parents[2]
PROJECT_ROOT = Path(os.environ.get("ZSXQ_PROJECT_ROOT", str(DEFAULT_PROJECT_ROOT))).resolve()
LOG_PATH = PROJECT_ROOT / "logs" / "zsxq_auto_publish.log"
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("zsxq_auto_publish")


class ZsxqAutoPublishScheduler:
    def __init__(self):
        self.app = create_app()
        self.running = False
        self.interval_minutes = self._interval_minutes()
        self._scheduled_interval = None

    def _interval_minutes(self):
        config = config_status()
        return int(config.get("interval_minutes") or 30)

    def run_once(self, trigger="scheduler"):
        started_at = time.monotonic()
        with self.app.app_context():
            status = config_status()
            if not status.get("exists"):
                return {
                    "queued": False,
                    "skipped": True,
                    "reason": "missing_config",
                    "message": "未找到自动发帖配置，请先保存策略。",
                }
            if not status.get("cookie_pool_ready") and not status.get("cookie_env_configured") and not os.environ.get("ZSXQ_AUTO_PUBLISH_COOKIE_OVERRIDE"):
                return {
                    "queued": False,
                    "skipped": True,
                    "reason": "missing_cookie_env",
                    "message": f"未配置环境变量 {COOKIE_ENV_NAME}。",
                }
            result = run_configured_once(trigger=trigger)
            logger.info(
                "[ZSXQ_AUTO_PUBLISH] trigger=%s status=%s queued=%s elapsed=%.2fs message=%s",
                trigger,
                result.get("status") or result.get("reason"),
                result.get("queued"),
                time.monotonic() - started_at,
                result.get("message"),
            )
            return result

    def refresh_schedule(self):
        schedule.clear("zsxq-auto-publish")
        self.interval_minutes = self._interval_minutes()
        schedule.every(self.interval_minutes).minutes.do(self.run_once).tag("zsxq-auto-publish")
        logger.info("ZSXQ 自动发帖调度已设置：每 %s 分钟执行一次", self.interval_minutes)

    def start(self):
        self.running = True
        self.refresh_schedule()
        self._scheduled_interval = self.interval_minutes
        logger.info("ZSXQ 自动发帖调度器启动")
        while self.running:
            self.interval_minutes = self._interval_minutes()
            if self._scheduled_interval != self.interval_minutes:
                self.refresh_schedule()
                self._scheduled_interval = self.interval_minutes
            schedule.run_pending()
            time.sleep(15)

    def stop(self):
        self.running = False
        schedule.clear("zsxq-auto-publish")
        logger.info("ZSXQ 自动发帖调度器停止")


def main():
    parser = argparse.ArgumentParser(description="ZSXQ 自动发帖调度器")
    parser.add_argument("command", choices=["start", "once", "status"])
    args = parser.parse_args()

    service = ZsxqAutoPublishScheduler()

    def signal_handler(signum, frame):
        logger.info("收到信号 %s，准备停止自动发帖调度器", signum)
        service.stop()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    if args.command == "start":
        service.start()
    elif args.command == "once":
        result = service.run_once(trigger="manual_cli")
        print(json.dumps(result, ensure_ascii=False))
    elif args.command == "status":
        with service.app.app_context():
            print(json.dumps(config_status(), ensure_ascii=False))


if __name__ == "__main__":
    main()
