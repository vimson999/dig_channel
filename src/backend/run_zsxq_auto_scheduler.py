#!/usr/bin/env python3
"""Project-level scheduler for the ZSXQ sync test."""

import argparse
import json
import logging
import os
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

import schedule
from typing import Tuple

from flask import has_app_context


BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app import create_app
from models import db
from services.zsxq_system_sync_test import (
    COOKIE_ENV_NAME,
    COOKIE_OVERRIDE_ENV_NAME,
    config_status,
    queue_configured_jobs,
    _scheduler_interval_minutes,
)

DEFAULT_PROJECT_ROOT = BACKEND_DIR.parents[1]
PROJECT_ROOT = Path(os.environ.get("ZSXQ_PROJECT_ROOT", str(DEFAULT_PROJECT_ROOT))).resolve()
if not PROJECT_ROOT.exists():
    PROJECT_ROOT = DEFAULT_PROJECT_ROOT


def _resolve_log_path() -> Tuple[Path, Path]:
    candidates = [
        PROJECT_ROOT / "logs",
        Path.cwd() / "logs",
        DEFAULT_PROJECT_ROOT / "logs",
        Path("/tmp"),
    ]
    for logs_dir in candidates:
        try:
            logs_dir.mkdir(parents=True, exist_ok=True)
            return logs_dir / "zsxq_auto_scheduler.log", logs_dir
        except Exception:
            continue
    raise RuntimeError("ZSXQ_AUTO_SCHEDULER_LOG_PATH_UNAVAILABLE")


LOG_PATH = _resolve_log_path()[0]
PID_PATH = PROJECT_ROOT / "runtime" / "zsxq_auto_scheduler.pid"


def _env_truthy(name):
    return str(os.environ.get(name, "")).strip().lower() in {"1", "true", "yes", "on"}


def _stdout_is_redirected_to_log():
    return _env_truthy("SCHEDULER_STDOUT_REDIRECTED_TO_LOG")


logging_handlers = [logging.FileHandler(LOG_PATH)]
if not _stdout_is_redirected_to_log():
    logging_handlers.append(logging.StreamHandler())

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=logging_handlers,
)
logger = logging.getLogger("zsxq_auto_scheduler")


def _skip_startup_trigger():
    return _env_truthy("ZSXQ_AUTO_SCHEDULER_SKIP_STARTUP_TRIGGER")


def _write_runtime_pid_file(cookie_source=None):
    payload = {
        "pid": os.getpid(),
        "started_at": datetime.utcnow().isoformat() + "Z",
        "command": " ".join(sys.argv),
        "log_path": str(LOG_PATH),
        "log_file_descriptor_opened": True,
        "cookie_source": cookie_source or _cookie_source(),
    }
    try:
        PID_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = PID_PATH.with_suffix(".tmp")
        with tmp_path.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.chmod(tmp_path, 0o600)
        tmp_path.replace(PID_PATH)
    except Exception:
        logger.exception("[ZSXQ_AUTO_SYNC] pid file write failed | path=%s", PID_PATH)


def _remove_runtime_pid_file():
    try:
        with PID_PATH.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
        if int(payload.get("pid") or 0) != os.getpid():
            return
    except FileNotFoundError:
        return
    except Exception:
        logger.exception("[ZSXQ_AUTO_SYNC] pid file read failed before cleanup | path=%s", PID_PATH)
        return

    try:
        PID_PATH.unlink()
    except FileNotFoundError:
        return
    except Exception:
        logger.exception("[ZSXQ_AUTO_SYNC] pid file cleanup failed | path=%s", PID_PATH)


def _cookie_source():
    try:
        status = config_status()
        if status.get("cookie_db_configured"):
            return "db"
    except Exception:
        pass
    if (os.environ.get(COOKIE_OVERRIDE_ENV_NAME) or "").strip():
        return "page"
    if (os.environ.get(COOKIE_ENV_NAME) or "").strip():
        return "env"
    return "db"


def _notify_scheduler_trigger_failure(*, attempt_id, trigger, reason, message=None, error=None, config=None, stopped=False):
    try:
        from services.workflow_notification_service import workflow_notification_service

        config = config if isinstance(config, dict) else {}
        group_name = config.get("group_name") or config.get("group_id") or "-"
        detail = error or message or reason or "-"
        lines = [
            "事件：系统同步触发失败",
            f"星球：{group_name}",
            "状态：失败 / 当前阶段：scheduler",
            f"触发方式：{trigger or '-'}",
            f"触发批次：{attempt_id or '-'}",
            f"原因：{reason or '-'}",
            f"错误：{detail}",
            "结果：本轮未入队，未创建 workflow，未执行下载、清洗、发布。",
        ]
        if config.get("interval_minutes"):
            lines.append(f"计划间隔：{config.get('interval_minutes')} 分钟")
        if config.get("path"):
            lines.append(f"配置文件：{config.get('path')}")
        if stopped:
            lines.append("处理：调度器已停止，请修复配置后重启。")
        else:
            lines.append("处理：调度器仍在运行，下一轮会继续尝试。")
        result = workflow_notification_service.send_email_message(
            "[知识星球工作流] 系统同步触发失败",
            "\n".join(lines),
        )
        logger.info(
            "[ZSXQ_AUTO_SYNC] trigger_failure_notification | attempt_id=%s reason=%s sent=%s result=%s",
            attempt_id or "-",
            reason or "-",
            bool(isinstance(result, dict) and result.get("sent")),
            result,
        )
    except Exception:
        logger.exception(
            "[ZSXQ_AUTO_SYNC] trigger failure notification failed | attempt_id=%s reason=%s",
            attempt_id or "-",
            reason or "-",
        )


def _reset_db_session(reason=None):
    if not has_app_context():
        return
    try:
        db.session.rollback()
    except Exception:
        logger.exception(
            "[ZSXQ_AUTO_SYNC] db session rollback failed | reason=%s",
            reason or "-",
        )
    try:
        db.session.remove()
    except Exception:
        logger.exception(
            "[ZSXQ_AUTO_SYNC] db session remove failed | reason=%s",
            reason or "-",
        )


class ZsxqAutoScheduler:
    def __init__(self):
        self.app = create_app()
        self.running = False
        self.interval_minutes = self._interval_minutes()
        self._scheduled_interval = None

    def _interval_minutes(self):
        status = config_status()
        configured_interval = status.get("interval_minutes")
        return _scheduler_interval_minutes(configured_interval)

    def queue_once(self, trigger="scheduler"):
        attempt_id = datetime.now().strftime("%Y%m%d-%H%M%S")
        started_at = time.monotonic()
        status = None
        logger.info(
            "[ZSXQ_AUTO_SYNC] trigger_start | attempt_id=%s trigger=%s interval_minutes=%s",
            attempt_id,
            trigger,
            self.interval_minutes,
        )
        try:
            with self.app.app_context():
                _reset_db_session("trigger_start")
                status = config_status()
                if not status.get("exists"):
                    logger.info(
                        "[ZSXQ_AUTO_SYNC] trigger_skip | attempt_id=%s reason=missing_config path=%s elapsed=%.2fs",
                        attempt_id,
                        status.get("path"),
                        time.monotonic() - started_at,
                    )
                    _notify_scheduler_trigger_failure(
                        attempt_id=attempt_id,
                        trigger=trigger,
                        reason="missing_config",
                        message="未找到系统计划配置，请先保存配置。",
                        config=status,
                    )
                    return {
                        "queued": False,
                        "skipped": True,
                        "reason": "missing_config",
                        "message": "未找到系统计划配置，请先保存配置。",
                        "config": status,
                    }
                if not status.get("cookie_env_configured"):
                    logger.info(
                        "[ZSXQ_AUTO_SYNC] trigger_skip | attempt_id=%s reason=missing_credential env=%s elapsed=%.2fs",
                        attempt_id,
                        status.get("cookie_env_name"),
                        time.monotonic() - started_at,
                    )
                    _notify_scheduler_trigger_failure(
                        attempt_id=attempt_id,
                        trigger=trigger,
                        reason="missing_credential",
                        message="未找到可用的知识星球同步凭据，请先在平台凭据管理里保存 zsxq.sync Cookie。",
                        config=status,
                    )
                    return {
                        "queued": False,
                        "skipped": True,
                        "reason": "missing_credential",
                        "message": "未找到可用的知识星球同步凭据，请先在平台凭据管理里保存 zsxq.sync Cookie。",
                        "config": status,
                    }

                result = queue_configured_jobs(trigger=trigger, attempt_id=attempt_id)
                if result.get("skipped"):
                    logger.info(
                        "[ZSXQ_AUTO_SYNC] trigger_skip | attempt_id=%s reason=%s error=%s elapsed=%.2fs",
                        attempt_id,
                        result.get("reason") or "unknown",
                        result.get("error") or "-",
                        time.monotonic() - started_at,
                    )
                    if result.get("reason") in {"max_rounds_reached", "invalid_group", "missing_config"}:
                        logger.info(
                            "[ZSXQ_AUTO_SYNC] scheduler stopped by trigger_reason=%s | attempt_id=%s",
                            result.get("reason") or "unknown",
                            attempt_id,
                        )
                        self.stop()
                    return {
                        "queued": False,
                        "skipped": True,
                        "reason": result.get("reason") or "skip",
                        "message": self._skip_message(result.get("reason"), attempt_id),
                        "config": result.get("config") or status,
                    }

                queued = result.get("queued") or []
                if result.get("reason") == "queue_failed" and result.get("error"):
                    error_text = str(result.get("error"))[:200]
                    if result.get("testing_complete") or result.get("reason") == "max_rounds_reached":
                        logger.info(
                            "[ZSXQ_AUTO_SYNC] max rounds reached after queue failure, stopping scheduler | attempt_id=%s round=%s/%s",
                            attempt_id,
                            result.get("round") or "-",
                            result.get("max_rounds") or "-",
                        )
                        self.stop()
                    return {
                        "queued": False,
                        "skipped": False,
                        "round": result.get("round"),
                        "max_rounds": result.get("max_rounds"),
                        "batch_id": result.get("batch_id"),
                        "testing_complete": bool(result.get("testing_complete")),
                        "reason": "queue_failed",
                        "error": result.get("error"),
                        "message": f"第 {result.get('round', '-')} 轮入队失败：{error_text}",
                        "config": result.get("config") or status,
                        "queued_runs": queued,
                    }
                logger.info(
                    "[ZSXQ_AUTO_SYNC] trigger_queued | attempt_id=%s batch_id=%s round=%s/%s queued_count=%s jobs=%s elapsed=%.2fs",
                    attempt_id,
                    result.get("batch_id") or "-",
                    result.get("round") or "-",
                    result.get("max_rounds") or "-",
                    len(queued),
                    queued,
                    time.monotonic() - started_at,
                )
                if result.get("testing_complete") or result.get("reason") == "max_rounds_reached":
                    logger.info("[ZSXQ_AUTO_SYNC] max rounds reached, stopping scheduler | attempt_id=%s", attempt_id)
                    self.stop()
                return {
                    "queued": True,
                    "skipped": False,
                    "round": result.get("round"),
                    "max_rounds": result.get("max_rounds"),
                    "batch_id": result.get("batch_id"),
                    "testing_complete": bool(result.get("testing_complete")),
                    "reason": result.get("reason"),
                    "config": result.get("config") or status,
                    "queued_runs": queued,
                    "message": f"已提交第 {result.get('round', '-')} 轮，batch={result.get('batch_id', '-')}",
                }
        except Exception as exc:
            try:
                with self.app.app_context():
                    _reset_db_session("trigger_failed")
            except Exception:
                logger.exception(
                    "[ZSXQ_AUTO_SYNC] db session reset failed after trigger error | attempt_id=%s",
                    attempt_id,
                )
            logger.exception(
                "[ZSXQ_AUTO_SYNC] trigger_failed | attempt_id=%s trigger=%s elapsed=%.2fs error=%s",
                attempt_id,
                trigger,
                time.monotonic() - started_at,
                exc,
            )
            _notify_scheduler_trigger_failure(
                attempt_id=attempt_id,
                trigger=trigger,
                reason="error",
                error=str(exc),
                config=locals().get("status"),
            )
            return {
                "queued": False,
                "skipped": True,
                "reason": "error",
                "error": str(exc),
                "message": str(exc),
                "config": locals().get("status"),
            }

    @staticmethod
    def _skip_message(reason, attempt_id):
        if reason == "max_rounds_reached":
            return f"触发尝试 {attempt_id}：已到达最大轮次，不再继续。"
        if reason == "missing_config":
            return "未找到系统计划配置，请先保存配置。"
        if reason in {"missing_cookie_env", "missing_credential"}:
            return "缺失知识星球同步平台凭据。"
        if reason == "invalid_group":
            return "星球配置无效（占位/已下线/权限不足），请重新保存有效 group_id 后再启动。"
        if reason == "transient_upstream_error":
            return "知识星球接口临时错误，本轮已重试后跳过；调度器会继续等待下一轮。"
        if reason == "disabled":
            return "当前配置已停用。"
        if reason == "previous_run_pending":
            return "上一轮系统同步仍在排队或执行中，本轮已跳过，避免重复入队。"
        return f"触发尝试 {attempt_id}：已跳过（{reason}）。"

    def refresh_schedule(self):
        schedule.clear("zsxq-auto-sync-test")
        self.interval_minutes = self._interval_minutes()
        job = schedule.every(self.interval_minutes).minutes.do(self.queue_once).tag("zsxq-auto-sync-test")
        logger.info(
            "ZSXQ 自动同步计划已设置：每 %s 分钟执行一次 | next_run=%s",
            self.interval_minutes,
            self._format_next_run(job.next_run),
        )

    def start(self):
        self.running = True
        self.refresh_schedule()
        self._scheduled_interval = self.interval_minutes
        logger.info("ZSXQ 自动同步调度器启动")
        if _skip_startup_trigger():
            logger.info(
                "[ZSXQ_AUTO_SYNC] startup_trigger | queued=False skipped=True "
                "reason=maintenance_reload"
            )
        else:
            try:
                immediate_result = self.queue_once(trigger="scheduler_start")
                logger.info(
                    "[ZSXQ_AUTO_SYNC] startup_trigger | queued=%s skipped=%s reason=%s round=%s/%s",
                    bool(immediate_result.get("queued")),
                    bool(immediate_result.get("skipped")),
                    immediate_result.get("reason") or "queued",
                    immediate_result.get("round") or "-",
                    immediate_result.get("max_rounds") or "-",
                )
            except Exception as exc:
                logger.exception("[ZSXQ_AUTO_SYNC] startup_trigger_failed | error=%s", exc)

        while self.running:
            self.interval_minutes = self._interval_minutes()
            if self._scheduled_interval != self.interval_minutes:
                self.refresh_schedule()
                self._scheduled_interval = self.interval_minutes
            due_count = sum(1 for job in schedule.get_jobs("zsxq-auto-sync-test") if job.should_run)
            schedule.run_pending()
            if due_count:
                logger.info(
                    "[ZSXQ_AUTO_SYNC] scheduler_waiting | next_run=%s",
                    self._format_next_run(self._next_run()),
                )
            time.sleep(30)

    def stop(self):
        self.running = False
        schedule.clear("zsxq-auto-sync-test")
        logger.info("ZSXQ 自动同步调度器停止")

    def _next_run(self):
        next_runs = [
            job.next_run
            for job in schedule.get_jobs("zsxq-auto-sync-test")
            if job.next_run is not None
        ]
        return min(next_runs) if next_runs else None

    def _format_next_run(self, value):
        if not value:
            return "-"
        return value.isoformat(sep=" ", timespec="seconds")


def main():
    parser = argparse.ArgumentParser(description="ZSXQ 自动同步测试调度器")
    parser.add_argument("command", choices=["start", "once", "status"], help="start: 启动调度；once: 立即投递一次；status: 查看配置")
    args = parser.parse_args()

    service = ZsxqAutoScheduler()

    def signal_handler(signum, frame):
        logger.info("收到信号 %s，准备停止", signum)
        service.stop()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    if args.command == "start":
        with service.app.app_context():
            cookie_source = _cookie_source()
        _write_runtime_pid_file(cookie_source=cookie_source)
        try:
            service.start()
        finally:
            _remove_runtime_pid_file()
    elif args.command == "once":
        result = service.queue_once(trigger="manual_cli")
        print(json.dumps(result, ensure_ascii=False))
    elif args.command == "status":
        with service.app.app_context():
            logger.info("ZSXQ 自动同步配置：%s", config_status())


if __name__ == "__main__":
    main()
