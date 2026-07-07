#!/usr/bin/env python3
"""Scheduler for content retention cleanup."""

import argparse
import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
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
    acquire_scheduler_process_lock,
    local_naive_datetime,
    load_config,
    next_run_iso,
    release_file_lock,
    scheduled_next_run_datetime,
    update_runtime_status,
    utc_datetime_iso,
    utc_now_iso,
)
from services.workflow_notification_service import workflow_notification_service


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
NOTIFY_ENV_NAME = "CONTENT_RETENTION_EMAIL_NOTIFY"


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
        self._send_notification(
            "started",
            trigger=trigger,
            dry_run=dry_run,
            policy=policy.to_dict(),
            config=config,
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
            self._send_notification(
                "completed" if summary["error_count"] == 0 else "completed_with_errors",
                trigger=trigger,
                dry_run=dry_run,
                policy=policy.to_dict(),
                summary=summary,
                cleanup_result=cleanup_result,
                config=load_config(),
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
            self._send_notification(
                "failed",
                trigger=trigger,
                dry_run=dry_run,
                policy=policy.to_dict(),
                summary=summary,
                error=exc,
                config=load_config(),
            )
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
        config = load_config()
        self.interval_hours = self._interval_hours()
        job = schedule.every(self.interval_hours).hours.do(self.cleanup_once).tag("content-retention-cleanup")
        target_next_run = scheduled_next_run_datetime(config, interval_hours=self.interval_hours)
        job.next_run = local_naive_datetime(target_next_run)
        self._scheduled_interval = self.interval_hours
        update_runtime_status({"next_run_at": utc_datetime_iso(target_next_run)})
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
            self.refresh_schedule()

        while self.running:
            current_interval = self._interval_hours()
            if current_interval != self._scheduled_interval:
                self.refresh_schedule()
            due_count = sum(1 for job in schedule.get_jobs("content-retention-cleanup") if job.should_run)
            schedule.run_pending()
            if due_count:
                update_runtime_status({"next_run_at": _scheduled_job_next_run_iso() or next_run_iso(self.interval_hours)})
            time.sleep(30)

    def stop(self):
        self.running = False
        schedule.clear("content-retention-cleanup")
        logger.info("历史文件清理调度器停止")

    def _interval_hours(self):
        config = load_config()
        return int(config.get("interval_hours") or 24)

    def _send_notification(self, event, **context):
        if not _email_notify_enabled():
            return {"sent": False, "reason": "disabled"}
        try:
            subject, body = _build_retention_email(event, **context)
            workflow_notification_service.send_email_message(subject, body)
            logger.info("[RETENTION_CLEANUP] email sent | event=%s", event)
            return {"sent": True}
        except Exception as exc:
            logger.exception("[RETENTION_CLEANUP] email failed | event=%s error=%s", event, exc)
            return {"sent": False, "error": str(exc)}


def _scheduled_job_next_run_iso():
    jobs = schedule.get_jobs("content-retention-cleanup")
    if not jobs:
        return None
    next_run = getattr(jobs[0], "next_run", None)
    if not isinstance(next_run, datetime):
        return None
    if next_run.tzinfo is None:
        local_tz = datetime.now().astimezone().tzinfo
        next_run = next_run.replace(tzinfo=local_tz)
    return utc_datetime_iso(next_run.astimezone(timezone.utc))


def _dry_run_override_from_env():
    value = os.getenv(DRY_RUN_OVERRIDE_ENV)
    if value is None:
        return None
    return str(value).strip().lower() not in {"0", "false", "no", "off"}


def _email_notify_enabled():
    value = os.getenv(NOTIFY_ENV_NAME)
    if value is None:
        return True
    return str(value).strip().lower() not in {"0", "false", "no", "off"}


def _build_retention_email(event, *, trigger, dry_run, policy=None, summary=None, cleanup_result=None, error=None, config=None):
    status_text = {
        "started": "开始",
        "completed": "完成",
        "completed_with_errors": "完成但有错误",
        "failed": "异常",
    }.get(event, event)
    mode_text = "预览" if dry_run else "真实清理"
    subject = f"[历史文件清理] {status_text} · {mode_text}"
    lines = [
        f"事件：历史文件清理{status_text}",
        f"模式：{mode_text}",
        f"触发：{trigger}",
    ]

    if summary:
        lines.extend(
            [
                f"候选：{summary.get('candidate_count', 0)}",
                f"候选体积：{summary.get('total_human', '0 B')}",
                f"已删除：{summary.get('deleted_count', 0)}",
                f"已释放：{summary.get('deleted_human', '0 B')}",
                f"错误数：{summary.get('error_count', 0)}",
                f"耗时：{summary.get('elapsed_seconds', 0)} 秒",
                f"完成时间：{summary.get('completed_at') or '-'}",
            ]
        )
    else:
        lines.append("状态：任务已开始，正在扫描候选文件。")

    if error:
        lines.append(f"错误：{error}")

    cleanup_errors = (cleanup_result or {}).get("errors") if isinstance(cleanup_result, dict) else None
    if cleanup_errors:
        lines.append("")
        lines.append("错误明细（最多 5 条）：")
        for item in cleanup_errors[:5]:
            lines.append(f"- {item.get('path') or '-'} | {item.get('error') or '-'}")

    policy = policy if isinstance(policy, dict) else {}
    if policy:
        lines.append("")
        lines.append("保留策略：")
        lines.append(f"- 同步原始附件：{policy.get('sync_raw_days')} 天")
        lines.append(f"- 清洗批次：{policy.get('content_pipeline_batch_days')} 天")
        lines.append(f"- 手动发布临时目录：{policy.get('manual_publish_days')} 天")
        lines.append(f"- pytest 中转批次：{policy.get('pytest_batch_days')} 天")
        lines.append(f"- 未收尾任务保护：{policy.get('unfinished_reference_protect_days')} 天")
        lines.append(f"- 最大候选：{policy.get('max_candidates')}")

    config = config if isinstance(config, dict) else {}
    if config:
        lines.append("")
        lines.append(f"下次计划：{config.get('next_run_at') or '-'}")
        lines.append(f"日志：{config.get('log_path') or LOG_PATH}")

    return subject, "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="历史文件保留期清理调度器")
    parser.add_argument("command", choices=["start", "once", "status"])
    args = parser.parse_args()

    process_lock = None
    if args.command == "start":
        process_lock = acquire_scheduler_process_lock()
        if not process_lock:
            logger.warning("已有历史文件清理调度器进程运行，退出本次启动")
            return

    service = ContentRetentionCleanupScheduler()

    def signal_handler(signum, frame):
        logger.info("收到信号 %s，准备停止", signum)
        service.stop()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        if args.command == "start":
            service.start()
        elif args.command == "once":
            result = service.cleanup_once(trigger="manual_cli", dry_run_override=_dry_run_override_from_env())
            print(json.dumps(result, ensure_ascii=False))
        elif args.command == "status":
            print(json.dumps(load_config(), ensure_ascii=False))
    finally:
        release_file_lock(process_lock)


if __name__ == "__main__":
    main()
