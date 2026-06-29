import json
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path

import redis
from flask import has_app_context
from sqlalchemy import text

from celery_config import celery_app
from models.zsxq_sync import ZsxqSyncRun
from services.zsxq_service import zsxq_service
from services.zsxq_sync_service import (
    TRANSIENT_UPSTREAM_CODES,
    ZsxqTransientUpstreamError,
    zsxq_sync_service,
)


logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
CONFIG_PATH = PROJECT_ROOT / "runtime" / "zsxq_scheduled_sync_test.json"
DEFAULT_COUNT = 12
DEFAULT_MAX_DOWNLOAD_FILES = 40
DEFAULT_MAX_ROUNDS = 0
DEFAULT_FOREIGN_REPORT_PDF_DOWNLOAD_THRESHOLD = 30
DEFAULT_RESEARCH_MEMO_MP3_DOWNLOAD_THRESHOLD = 15
DEFAULT_AUTO_VIDEO_PDF_THRESHOLD = 35
DEFAULT_AUTO_VIDEO_MP3_THRESHOLD = 25
DEFAULT_AUTO_SCRIPT_MATERIAL_PDF_THRESHOLD = 40
TOPIC_PAGE_SIZE_HINT = 20
COOKIE_ENV_NAME = "ZSXQ_SYNC_TEST_COOKIE"
COOKIE_OVERRIDE_ENV_NAME = "ZSXQ_SYNC_TEST_COOKIE_OVERRIDE"
TRANSIENT_VALIDATE_RETRIES_ENV_NAME = "ZSXQ_SYSTEM_SYNC_TRANSIENT_RETRIES"
TRANSIENT_VALIDATE_RETRY_DELAY_ENV_NAME = "ZSXQ_SYSTEM_SYNC_TRANSIENT_RETRY_DELAY_SECONDS"
DEFAULT_TRANSIENT_VALIDATE_RETRIES = 5
DEFAULT_TRANSIENT_VALIDATE_RETRY_DELAY_SECONDS = 60
ENV_INTERVAL_MINUTES = 25
GROUP_ID_PLACEHOLDER_RE = re.compile(r"^group[-_]?test", re.IGNORECASE)
SYSTEM_SYNC_GROUP_ID = "28888112822211"
SYSTEM_SYNC_GROUP_NAME = "前沿信息收录"


def config_exists():
    return CONFIG_PATH.exists()


def config_status():
    if not CONFIG_PATH.exists():
        return {
            "exists": False,
            "path": str(CONFIG_PATH),
            "cookie_env_name": COOKIE_ENV_NAME,
            "cookie_env_configured": _runtime_cookie_configured(),
            "cookie_db_configured": _runtime_db_cookie_configured(),
            "cookie_source_mode": _runtime_cookie_source(),
        }

    config = load_config()
    return _snapshot_config(config)


def _snapshot_config(config):
    max_rounds = _normalize_max_rounds(config.get("max_rounds"), default=DEFAULT_MAX_ROUNDS, none_means_continuous=True)
    rounds_completed = _bounded_int(config.get("rounds_completed"), default=0, lower=0, upper=1_000_000)
    group_id = str(config.get("group_id") or "").strip()
    group_validation_error = _validate_group_id_local(group_id)
    if not group_validation_error and group_id and not _is_supported_system_sync_group(group_id):
        group_validation_error = _unsupported_system_sync_group_message(group_id)
    enabled = bool(config.get("enabled", True))
    if group_validation_error and (
        _is_placeholder_group_id(group_id) or not _is_supported_system_sync_group(group_id)
    ):
        enabled = False
    return {
        "exists": True,
        "path": str(CONFIG_PATH),
        "updated_at": config.get("updated_at"),
        "enabled": enabled,
        "credential_id": config.get("credential_id") or None,
        "cookie_env_name": COOKIE_ENV_NAME,
        "cookie_env_configured": _runtime_cookie_configured(),
        "cookie_db_configured": _runtime_db_cookie_configured(),
        "cookie_source_mode": _runtime_cookie_source(),
        "interval_minutes": _scheduler_interval_minutes(config.get("interval_minutes")),
        "group_id": group_id,
        "group_validation_error": group_validation_error,
        "group_name": config.get("group_name"),
        "count": config.get("count"),
        "max_pages": config.get("max_pages"),
        "download_attachments": bool(config.get("download_attachments", False)),
        "allow_attachment_downloads": bool(config.get("allow_attachment_downloads", False)),
        "max_download_files": config.get("max_download_files"),
        "skip_existing_downloads": bool(config.get("skip_existing_downloads", True)),
        "attachment_download_rules": _normalize_attachment_download_rules(
            config.get("attachment_download_rules"),
            config.get("jobs") or [],
        ),
        "auto_video_dispatch": _normalize_auto_video_dispatch(config.get("auto_video_dispatch")),
        "auto_script_material_dispatch": _normalize_auto_script_material_dispatch(
            config.get("auto_script_material_dispatch")
        ),
        "auto_pdf_clean": bool(config.get("auto_pdf_clean", False)),
        "auto_audio_clean": bool(config.get("auto_audio_clean", False)),
        "auto_publish_after_clean": _normalize_auto_publish_after_clean(
            config.get("auto_publish_after_clean"),
            previous={"enabled": False},
        ),
        "max_rounds": max_rounds,
        "continuous": max_rounds is None,
        "rounds_completed": rounds_completed,
        "testing_complete": False if max_rounds is None else rounds_completed >= max_rounds,
        "last_batch_id": config.get("last_batch_id"),
        "last_trigger_at": config.get("last_trigger_at"),
        "last_queued_run_id": config.get("last_queued_run_id"),
        "last_workflow_run_id": config.get("last_workflow_run_id"),
        "last_skip_at": config.get("last_skip_at"),
        "last_skip_reason": config.get("last_skip_reason"),
        "last_skip_error": config.get("last_skip_error"),
        "last_transient_retry_count": config.get("last_transient_retry_count"),
        "jobs": [
            {
                "key": job.get("key"),
                "tab_label": (job.get("tab") or {}).get("label"),
                "file_type": job.get("file_type"),
                "mode_code": job.get("mode_code"),
            }
            for job in config.get("jobs", [])
        ],
    }


def save_config(payload, cookie):
    group_id = str(payload.get("group_id") or "").strip()
    if not group_id:
        raise ValueError("group_id 不能为空")
    _validate_supported_system_sync_group(group_id)

    previous = load_config(required=False) or {}
    credential_id = (
        str(payload.get("credential_id") or payload.get("platform_credential_id") or "").strip()
        or previous.get("credential_id")
        or None
    )
    cookie_info = runtime_cookie_info(account_id=credential_id, required=False)
    if cookie_info.get("cookie"):
        cookie = cookie_info["cookie"]
        credential_id = cookie_info.get("credential_id") or credential_id
    elif cookie:
        cookie = zsxq_service.normalize_cookie(cookie)
    else:
        env_cookie = _strip_wrapped_quotes((os.environ.get(COOKIE_ENV_NAME) or "").strip())
        if env_cookie:
            try:
                cookie = zsxq_service.normalize_cookie(env_cookie)
            except Exception:
                cookie = None

    _validate_group_id(group_id, cookie=cookie)
    jobs = _normalize_jobs(payload.get("jobs"))
    if not jobs:
        raise ValueError("系统计划测试至少需要一个同步目标")

    now = datetime.utcnow().isoformat() + "Z"
    max_download_files = _bounded_int(
        payload.get("max_download_files"),
        default=DEFAULT_MAX_DOWNLOAD_FILES,
        lower=0,
        upper=40,
    )
    attachment_download_rules = _normalize_attachment_download_rules(
        payload.get("attachment_download_rules")
        if "attachment_download_rules" in payload
        else previous.get("attachment_download_rules"),
        jobs,
    )
    download_attachments = bool(payload.get("download_attachments", False))
    allow_attachment_downloads = bool(payload.get("allow_attachment_downloads", False))
    if attachment_download_rules.get("enabled") and max_download_files > 0:
        download_attachments = True
        allow_attachment_downloads = True
    count = _bounded_int(payload.get("count"), default=DEFAULT_COUNT, lower=1, upper=50)
    min_pages_for_count = _topic_pages_for_count(count)
    max_pages = _bounded_int(
        payload.get("max_pages"),
        default=min_pages_for_count,
        lower=min_pages_for_count,
        upper=5,
    )
    config = {
        "enabled": True,
        "created_at": previous.get("created_at") or now,
        "updated_at": now,
        "credential_id": credential_id,
        "cookie_env_name": COOKIE_ENV_NAME,
        "group_id": group_id,
        "group_name": payload.get("group_name") or "",
        "interval_minutes": _normalize_interval_minutes(
            payload.get("interval_minutes"),
            default=_scheduler_interval_minutes(),
        ),
        "count": count,
        "max_pages": max_pages,
        "stop_on_existing": bool(payload.get("stop_on_existing", True)),
        "download_attachments": download_attachments,
        "allow_attachment_downloads": allow_attachment_downloads,
        "max_download_files": max_download_files,
        "skip_existing_downloads": True,
        "attachment_download_rules": attachment_download_rules,
        "auto_video_dispatch": _normalize_auto_video_dispatch(
            payload.get("auto_video_dispatch")
            if "auto_video_dispatch" in payload
            else previous.get("auto_video_dispatch"),
            previous=previous.get("auto_video_dispatch"),
        ),
        "auto_script_material_dispatch": _normalize_auto_script_material_dispatch(
            payload.get("auto_script_material_dispatch")
            if "auto_script_material_dispatch" in payload
            else previous.get("auto_script_material_dispatch"),
            previous=previous.get("auto_script_material_dispatch"),
        ),
        "auto_pdf_clean": bool(payload.get("auto_pdf_clean", previous.get("auto_pdf_clean", False))),
        "pdf_cleaner": payload.get("pdf_cleaner") if isinstance(payload.get("pdf_cleaner"), dict) else previous.get("pdf_cleaner", {}),
        "auto_audio_clean": bool(payload.get("auto_audio_clean", previous.get("auto_audio_clean", False))),
        "audio_cleaner": payload.get("audio_cleaner") if isinstance(payload.get("audio_cleaner"), dict) else previous.get("audio_cleaner", {}),
        "auto_publish_after_clean": _normalize_auto_publish_after_clean(
            payload.get("auto_publish_after_clean")
            if "auto_publish_after_clean" in payload
            else previous.get("auto_publish_after_clean"),
            previous=previous.get("auto_publish_after_clean") or {"enabled": False},
        ),
        "max_rounds": _normalize_max_rounds(
            payload["max_rounds"] if "max_rounds" in payload else DEFAULT_MAX_ROUNDS,
            default=DEFAULT_MAX_ROUNDS,
            none_means_continuous=True,
        ),
        "rounds_completed": 0,
        "last_batch_id": None,
        "last_trigger_at": None,
        "last_queued_run_id": None,
        "last_workflow_run_id": None,
        "jobs": jobs,
    }

    _write_config(config)
    return config_status()


def load_config(required=True):
    if not CONFIG_PATH.exists():
        if required:
            raise FileNotFoundError(f"系统计划测试配置不存在: {CONFIG_PATH}")
        return None

    with CONFIG_PATH.open("r", encoding="utf-8") as fh:
        config = json.load(fh)
    if not isinstance(config, dict):
        raise ValueError("系统计划测试配置格式不正确")
    return config


def _write_config(config):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = CONFIG_PATH.with_suffix(".json.tmp")
    with tmp_path.open("w", encoding="utf-8") as fh:
        json.dump(config, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.chmod(tmp_path, 0o600)
    tmp_path.replace(CONFIG_PATH)
    os.chmod(CONFIG_PATH, 0o600)


def queue_configured_jobs(trigger="scheduler", attempt_id=None):
    config = load_config()
    if not config.get("enabled", True):
        logger.info("[ZSXQ_SYSTEM_SYNC_TEST] skipped disabled config")
        return {"queued": [], "skipped": True, "reason": "disabled"}

    group_id = str(config.get("group_id") or "").strip()
    if not group_id:
        raise ValueError("系统计划测试配置缺少 group_id")
    if not _is_supported_system_sync_group(group_id):
        skipped_at = datetime.utcnow().isoformat() + "Z"
        error = _unsupported_system_sync_group_message(group_id)
        config["enabled"] = False
        config["last_trigger_at"] = skipped_at
        config["last_skip_at"] = skipped_at
        config["last_skip_reason"] = "unsupported_group"
        config["last_skip_error"] = error
        config["last_failed_batch_id"] = attempt_id
        config["updated_at"] = skipped_at
        _write_config(config)
        logger.warning(
            "[ZSXQ_SYSTEM_SYNC_TEST] skipped unsupported group | attempt_id=%s group_id=%s",
            attempt_id or "-",
            group_id,
        )
        return {
            "queued": [],
            "skipped": True,
            "reason": "unsupported_group",
            "error": error,
            "config": _snapshot_config(config),
        }

    jobs = _normalize_jobs(config.get("jobs"))
    if not jobs:
        raise ValueError("系统计划测试配置缺少 jobs")

    max_rounds = _normalize_max_rounds(config.get("max_rounds"), default=DEFAULT_MAX_ROUNDS, none_means_continuous=True)
    rounds_completed = _bounded_int(config.get("rounds_completed"), default=0, lower=0, upper=1_000_000)
    if max_rounds is not None and rounds_completed >= max_rounds:
        logger.info(
            "[ZSXQ_SYSTEM_SYNC_TEST] skipped max rounds reached | rounds_completed=%s max_rounds=%s",
            rounds_completed,
            max_rounds,
        )
        return {
            "queued": [],
            "skipped": True,
            "reason": "max_rounds_reached",
            "config": _snapshot_config(config),
        }

    pending_work = _pending_system_sync_work(group_id)
    if pending_work.get("pending"):
        logger.info(
            "[ZSXQ_SYSTEM_SYNC_TEST] skipped previous work pending | group_id=%s queue_length=%s run_id=%s status=%s",
            group_id,
            pending_work.get("queue_length"),
            pending_work.get("run_id") or "-",
            pending_work.get("run_status") or "-",
        )
        return {
            "queued": [],
            "skipped": True,
            "reason": "previous_run_pending",
            "pending": pending_work,
            "config": _snapshot_config(config),
        }

    validation_retry_count = _transient_validation_retry_count()
    validation_retry_delay = _transient_validation_retry_delay_seconds()
    try:
        cookie_info = runtime_cookie_info(account_id=config.get("credential_id"), required=True)
        cookie = cookie_info["cookie"]
        credential_id = cookie_info.get("credential_id")
        _validate_group_id_with_retry(
            group_id,
            cookie=cookie,
            max_retries=validation_retry_count,
            delay_seconds=validation_retry_delay,
        )
    except ZsxqTransientUpstreamError as exc:
        snapshot = _record_transient_validation_skip(
            config,
            attempt_id=attempt_id,
            group_id=group_id,
            error=exc,
            retry_count=validation_retry_count,
        )
        _notify_transient_validation_skip(
            attempt_id=attempt_id,
            group_id=group_id,
            group_name=config.get("group_name"),
            error=str(exc),
            retry_count=validation_retry_count,
            retry_delay_seconds=validation_retry_delay,
        )
        return {
            "queued": [],
            "skipped": True,
            "reason": "transient_upstream_error",
            "error": str(exc),
            "retry_count": validation_retry_count,
            "retry_delay_seconds": validation_retry_delay,
            "config": snapshot,
        }
    except ValueError as exc:
        disabled = False
        if _is_placeholder_group_id(group_id) or _is_auth_validation_error(exc):
            config["enabled"] = False
            disabled = True
            if locals().get("credential_id"):
                try:
                    from services.platform_credential_service import mark_credential_error

                    mark_credential_error(credential_id, exc, expired=True)
                except Exception:
                    logger.exception(
                        "[ZSXQ_SYSTEM_SYNC_TEST] credential error mark failed | credential=%s",
                        credential_id,
                    )
        snapshot = _record_invalid_group_skip(
            config,
            attempt_id=attempt_id,
            group_id=group_id,
            error=exc,
            disabled=disabled,
        )
        _notify_invalid_group_skip(
            attempt_id=attempt_id,
            trigger=trigger,
            group_id=group_id,
            group_name=config.get("group_name"),
            error=str(exc),
            config=snapshot,
            disabled=disabled,
        )
        return {
            "queued": [],
            "skipped": True,
            "reason": "invalid_group",
            "error": str(exc),
            "config": snapshot,
        }

    batch_round = rounds_completed + 1
    queued_at = datetime.utcnow().isoformat() + "Z"
    batch_id = attempt_id or f"{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}"
    max_download_files = _bounded_int(
        config.get("max_download_files"),
        default=DEFAULT_MAX_DOWNLOAD_FILES,
        lower=0,
        upper=40,
    )
    queued = []
    workflow = None
    queue_error = None
    try:
        from services.zsxq_workflow_service import zsxq_workflow_service

        workflow = zsxq_workflow_service.create_system_workflow(
            config=config,
            trigger=trigger,
            batch_id=batch_id,
            batch_round=batch_round,
            max_rounds=max_rounds,
            max_download_files=max_download_files,
            jobs=jobs,
        )
        queued_workflow = zsxq_workflow_service.enqueue_workflow_task(
            workflow["id"],
            credential_id=credential_id,
        )
        queued = [{
            "batch_id": batch_id,
            "round": batch_round,
            "max_rounds": max_rounds,
            "workflow_run_id": workflow["id"],
            "celery_task_id": queued_workflow.get("celery_task_id"),
            "mode": (workflow.get("config_snapshot") or {}).get("sync", {}).get("mode"),
            "jobs": ((workflow.get("config_snapshot") or {}).get("system_sync") or {}).get("jobs") or [],
            "max_download_files": max_download_files,
        }]
    except Exception as exc:
        queue_error = str(exc)
        if isinstance(workflow, dict) and workflow.get("id"):
            try:
                from services.zsxq_workflow_service import zsxq_workflow_service
                zsxq_workflow_service.mark_failed_by_id(
                    workflow["id"],
                    f"工作流编排任务入队失败: {queue_error}",
                    stage="workflow",
                )
            except Exception:
                logger.exception("[ZSXQ_SYSTEM_SYNC_TEST] failed to mark workflow failed | workflow=%s", workflow.get("id"))
        logger.exception(
            "[ZSXQ_SYSTEM_SYNC_TEST] workflow queue failed | batch_id=%s round=%s workflow_id=%s error=%s",
            batch_id,
            batch_round,
            workflow["id"] if isinstance(workflow, dict) and workflow.get("id") else "-",
            queue_error,
        )
        _notify_queue_failure(
            batch_id=batch_id,
            batch_round=batch_round,
            trigger=trigger,
            group_id=group_id,
            group_name=config.get("group_name"),
            error=queue_error,
        )

    logger.info(
        "[ZSXQ_SYSTEM_SYNC_TEST] queued workflow batch | batch_id=%s round=%s/%s workflow_id=%s queue_error=%s jobs=%s trigger=%s max_download_files=%s",
        batch_id,
        batch_round,
        max_rounds if max_rounds is not None else "continuous",
        workflow.get("id") if isinstance(workflow, dict) and workflow.get("id") else "-",
        bool(queue_error),
        queued[0]["jobs"] if queued else [],
        trigger,
        max_download_files,
    )

    config["max_rounds"] = max_rounds
    config["max_download_files"] = max_download_files
    config["last_queued_run_id"] = None
    if queue_error:
        config["last_queue_failed_at"] = queued_at
        config["last_queue_error"] = queue_error
        config["last_failed_batch_id"] = batch_id
    else:
        config["rounds_completed"] = batch_round
        config["last_batch_id"] = batch_id
        config["last_trigger_at"] = queued_at
        config.pop("last_queue_failed_at", None)
        config.pop("last_queue_error", None)
        config.pop("last_failed_batch_id", None)
    if isinstance(workflow, dict) and workflow.get("id"):
        config["last_workflow_run_id"] = workflow["id"]
    config["updated_at"] = queued_at
    _write_config(config)

    return {
        "queued": queued,
        "skipped": False,
        "batch_id": batch_id,
        "round": batch_round,
        "max_rounds": max_rounds,
        "testing_complete": False if queue_error or max_rounds is None else batch_round >= max_rounds,
        "reason": "queue_failed" if queue_error else None,
        "error": queue_error,
        "config": _snapshot_config(config),
    }


def _record_transient_validation_skip(config, *, attempt_id, group_id, error, retry_count):
    skipped_at = datetime.utcnow().isoformat() + "Z"
    config["last_trigger_at"] = skipped_at
    config["last_skip_at"] = skipped_at
    config["last_skip_reason"] = "transient_upstream_error"
    config["last_skip_error"] = str(error)
    config["last_transient_retry_count"] = retry_count
    config["last_failed_batch_id"] = attempt_id
    config["updated_at"] = skipped_at
    _write_config(config)
    logger.warning(
        "[ZSXQ_SYSTEM_SYNC_TEST] skipped transient upstream error after retries | "
        "attempt_id=%s group_id=%s retries=%s error=%s",
        attempt_id or "-",
        group_id,
        retry_count,
        error,
    )
    return _snapshot_config(config)


def _record_invalid_group_skip(config, *, attempt_id, group_id, error, disabled=False):
    skipped_at = datetime.utcnow().isoformat() + "Z"
    config["last_trigger_at"] = skipped_at
    config["last_skip_at"] = skipped_at
    config["last_skip_reason"] = "invalid_group"
    config["last_skip_error"] = str(error)
    config["last_failed_batch_id"] = attempt_id
    config["updated_at"] = skipped_at
    if disabled:
        config["enabled"] = False
    _write_config(config)
    logger.error(
        "[ZSXQ_SYSTEM_SYNC_TEST] skipped invalid group during preflight | "
        "attempt_id=%s group_id=%s disabled=%s error=%s",
        attempt_id or "-",
        group_id,
        bool(disabled),
        error,
    )
    return _snapshot_config(config)


def _notify_transient_validation_skip(
    *,
    attempt_id,
    group_id,
    group_name,
    error,
    retry_count,
    retry_delay_seconds,
):
    try:
        from services.workflow_notification_service import workflow_notification_service

        workflow_notification_service.send_email_message(
            "[知识星球工作流] 系统同步本轮跳过",
            "\n".join([
                "事件：系统同步触发前校验遇到知识星球临时错误，本轮已跳过",
                f"星球：{group_name or group_id or '-'}",
                f"触发：{attempt_id or '-'}",
                f"重试：{retry_count} 次，每次间隔 {retry_delay_seconds:g} 秒",
                f"错误：{error}",
                "处理：调度器保持运行，下一轮会继续尝试。",
            ]),
        )
    except Exception:
        logger.exception("[ZSXQ_SYSTEM_SYNC_TEST] transient skip notification failed | attempt_id=%s", attempt_id)


def _notify_invalid_group_skip(
    *,
    attempt_id,
    trigger,
    group_id,
    group_name,
    error,
    config,
    disabled=False,
):
    try:
        from services.workflow_notification_service import workflow_notification_service

        workflow_notification_service.send_email_message(
            "[知识星球工作流] 系统同步触发失败",
            "\n".join(_invalid_group_skip_email_lines(
                attempt_id=attempt_id,
                trigger=trigger,
                group_id=group_id,
                group_name=group_name,
                error=error,
                config=config,
                disabled=disabled,
            )),
        )
    except Exception:
        logger.exception("[ZSXQ_SYSTEM_SYNC_TEST] invalid group notification failed | attempt_id=%s", attempt_id)


def _invalid_group_skip_email_lines(*, attempt_id, trigger, group_id, group_name, error, config, disabled=False):
    config = config if isinstance(config, dict) else {}
    rounds_completed = _bounded_int(config.get("rounds_completed"), default=0, lower=0, upper=1_000_000)
    next_round = rounds_completed + 1
    max_rounds = config.get("max_rounds")
    round_text = f"第 {next_round} 轮"
    if max_rounds:
        round_text = f"{round_text} / 共 {max_rounds} 轮"
    elif config.get("continuous"):
        round_text = f"{round_text} / 持续运行"

    plan_parts = [
        f"间隔 {config.get('interval_minutes') or '-'} 分钟",
        f"每目标 Tab {config.get('count') or '-'} 个 topic",
        f"每目标最多 {config.get('max_pages') or '-'} 页",
        f"最大下载 {config.get('max_download_files') if config.get('max_download_files') is not None else '-'} 个附件",
    ]
    target_tabs = [
        str(item.get("tab_label") or item.get("key") or "").strip()
        for item in config.get("jobs") or []
        if isinstance(item, dict)
    ]
    target_tabs = [item for item in target_tabs if item]
    if target_tabs:
        plan_parts.append(f"目标 Tab：{'、'.join(target_tabs)}")

    lines = [
        "事件：系统同步触发失败",
        f"星球：{group_name or config.get('group_name') or group_id or config.get('group_id') or '-'}",
        "状态：失败 / 当前阶段：preflight",
        f"触发方式：{trigger or '-'}",
        f"触发批次：{attempt_id or '-'}",
        f"轮次：{round_text}",
        f"计划：{'；'.join(plan_parts)}。",
        "结果：本轮未入队，未创建 workflow，未执行下载、清洗、发布。",
        f"错误：{error}",
    ]
    if disabled:
        lines.append("处理：调度器已停止，请更新 Cookie 或星球配置后重启。")
        if _is_placeholder_group_id(group_id or config.get("group_id")):
            lines.append("备注：检测到占位 group_id，配置已自动停用。")
        else:
            lines.append("备注：检测到认证失效，配置已自动停用，避免持续失败。")
    else:
        lines.append("处理：本轮已跳过，调度器下一轮会继续预检。")
    return lines


def _notify_queue_failure(*, batch_id, batch_round, trigger, group_id, group_name, error):
    try:
        from services.workflow_notification_service import workflow_notification_service

        workflow_notification_service.send_email_message(
            "[知识星球工作流] 系统同步入队失败",
            "\n".join([
                "事件：系统同步入队失败",
                f"星球：{group_name or group_id or '-'}",
                f"批次：{batch_id}",
                f"轮次：{batch_round}",
                f"触发：{trigger or '-'}",
                f"错误：{error}",
            ]),
        )
    except Exception:
        logger.exception("[ZSXQ_SYSTEM_SYNC_TEST] queue failure notification failed | batch_id=%s", batch_id)


def _pending_system_sync_work(group_id):
    queue_length = None
    queue_error = None
    pending_workflow = None
    stale_workflows = []
    try:
        client = redis.Redis.from_url(
            celery_app.conf.broker_url,
            socket_connect_timeout=2,
            socket_timeout=2,
            retry_on_timeout=False,
        )
        queue_length = int(client.llen("zsxq_sync"))
    except Exception as exc:
        queue_error = str(exc)

    try:
        from services.zsxq_workflow_service import zsxq_workflow_service
        stale_workflows = zsxq_workflow_service.recover_stale_initial_workflows(group_id=group_id)
        pending_workflow = zsxq_workflow_service.pending_workflow(group_id=group_id, recover_stale=False)
    except Exception:
        pending_workflow = None

    pending_run = (
        ZsxqSyncRun.query
        .filter(
            ZsxqSyncRun.group_id == str(group_id),
            text("zsxq_sync_runs.mode LIKE 'sys%'"),
            ZsxqSyncRun.status.in_(("queued", "running")),
        )
        .order_by(ZsxqSyncRun.created_at.desc())
        .first()
    )

    return {
        "pending": bool(pending_workflow) or bool(pending_run) or bool(queue_length and queue_length > 0),
        "workflow_run_id": pending_workflow.get("id") if isinstance(pending_workflow, dict) else None,
        "workflow_status": pending_workflow.get("status") if isinstance(pending_workflow, dict) else None,
        "workflow_stage": pending_workflow.get("current_stage") if isinstance(pending_workflow, dict) else None,
        "stale_workflow_count": len(stale_workflows),
        "stale_workflow_ids": [item.get("id") for item in stale_workflows if isinstance(item, dict)],
        "queue_length": queue_length,
        "queue_error": queue_error,
        "run_id": pending_run.id if pending_run else None,
        "run_status": pending_run.status if pending_run else None,
        "run_updated_at": pending_run.updated_at.isoformat() if pending_run and pending_run.updated_at else None,
    }


def runtime_cookie(account_id=None):
    return runtime_cookie_info(account_id=account_id, required=True)["cookie"]


def runtime_cookie_info(account_id=None, required=True):
    try:
        from services.platform_credential_service import resolve_zsxq_cookie

        info = resolve_zsxq_cookie(account_id=account_id, purpose="zsxq.sync", required=required)
        if info.get("cookie"):
            return info
    except Exception:
        if required:
            raise

    override = _strip_wrapped_quotes((os.environ.get(COOKIE_OVERRIDE_ENV_NAME) or "").strip())
    if override:
        return {
            "cookie": zsxq_service.normalize_cookie(override),
            "credential_id": None,
            "credential_name": "页面 Cookie",
            "credential_source": "page",
        }

    cookie = _strip_wrapped_quotes((os.environ.get(COOKIE_ENV_NAME) or "").strip())
    if not cookie:
        if required:
            raise ValueError("未找到可用的知识星球同步凭据，请先在平台凭据管理里保存 zsxq.sync Cookie")
        return {}
    return {
        "cookie": zsxq_service.normalize_cookie(cookie),
        "credential_id": None,
        "credential_name": "环境变量 Cookie",
        "credential_source": "env",
    }


def _runtime_cookie_configured():
    return bool(
        _runtime_db_cookie_configured()
        or
        _strip_wrapped_quotes(os.environ.get(COOKIE_OVERRIDE_ENV_NAME) or "")
        or _strip_wrapped_quotes(os.environ.get(COOKIE_ENV_NAME) or "")
    )


def _runtime_db_cookie_configured():
    if not has_app_context():
        return False
    try:
        from services.platform_credential_service import has_active_credential

        return bool(has_active_credential(platform="zsxq", purpose="zsxq.sync"))
    except Exception:
        return False


def _runtime_cookie_source():
    if _runtime_db_cookie_configured():
        return "db"
    if _strip_wrapped_quotes(os.environ.get(COOKIE_OVERRIDE_ENV_NAME) or ""):
        return "page"
    if _strip_wrapped_quotes(os.environ.get(COOKIE_ENV_NAME) or ""):
        return "env"
    return None


def _strip_wrapped_quotes(value):
    if not isinstance(value, str):
        return value

    value = value.strip()
    if len(value) >= 2 and ((value[0] == value[-1]) and value[0] in {"'", '"'}):
        value = value[1:-1].strip()
    return value


def _validate_group_id(group_id, cookie=None):
    group_id = str(group_id or "").strip()
    if not group_id:
        raise ValueError("group_id 不能为空")

    if GROUP_ID_PLACEHOLDER_RE.match(group_id):
        raise ValueError(f"检测到占位 group_id：{group_id}。请在页面重新选择实际的星球。")

    if not cookie:
        return

    try:
        result = zsxq_service.get_menus(cookie, group_id)
    except Exception as exc:
        raise ValueError(f"读取星球菜单失败（group_id={group_id}）：{exc}") from exc

    if not isinstance(result, dict):
        raise ValueError(f"群组配置返回格式异常（group_id={group_id}）。")

    if result.get("succeeded") is False:
        code = str(result.get("code") or "UNKNOWN")
        detail = (
            (result.get("resp_data") or {}).get("message")
            or result.get("message")
            or result.get("error")
            or result
        )
        if code in TRANSIENT_UPSTREAM_CODES:
            raise ZsxqTransientUpstreamError(code, detail, payload=result)
        raise ValueError(f"知识星球返回失败 code={code}：{detail}（group_id={group_id}）")


def _validate_group_id_with_retry(group_id, cookie=None, *, max_retries=None, delay_seconds=None):
    retries = _normalize_int(
        max_retries,
        default=_transient_validation_retry_count(),
        lower=0,
        upper=10,
    )
    delay = _normalize_float(
        delay_seconds,
        default=_transient_validation_retry_delay_seconds(),
        lower=0,
        upper=300,
    )
    attempts = retries + 1
    for attempt in range(1, attempts + 1):
        try:
            return _validate_group_id(group_id, cookie=cookie)
        except ZsxqTransientUpstreamError as exc:
            if attempt >= attempts:
                logger.warning(
                    "[ZSXQ_SYSTEM_SYNC_TEST] transient group validation retry exhausted | "
                    "group_id=%s attempts=%s error=%s",
                    group_id,
                    attempts,
                    exc,
                )
                raise

            logger.warning(
                "[ZSXQ_SYSTEM_SYNC_TEST] transient group validation failure | "
                "group_id=%s attempt=%s/%s retry_in=%.1fs error=%s",
                group_id,
                attempt,
                attempts,
                delay,
                exc,
            )
            if delay > 0:
                time.sleep(delay)

    raise RuntimeError("ZSXQ_SYSTEM_SYNC_VALIDATE_RETRY_LOOP_UNREACHABLE")


def _transient_validation_retry_count():
    return _normalize_int(
        os.environ.get(TRANSIENT_VALIDATE_RETRIES_ENV_NAME),
        default=DEFAULT_TRANSIENT_VALIDATE_RETRIES,
        lower=0,
        upper=10,
    )


def _transient_validation_retry_delay_seconds():
    return _normalize_float(
        os.environ.get(TRANSIENT_VALIDATE_RETRY_DELAY_ENV_NAME),
        default=DEFAULT_TRANSIENT_VALIDATE_RETRY_DELAY_SECONDS,
        lower=0,
        upper=300,
    )


def transient_validation_retry_timeout_seconds():
    return int((_transient_validation_retry_count() * _transient_validation_retry_delay_seconds()) + 90)


def _is_placeholder_group_id(group_id):
    return bool(GROUP_ID_PLACEHOLDER_RE.match(str(group_id or "").strip()))


def _is_supported_system_sync_group(group_id):
    return str(group_id or "").strip() == SYSTEM_SYNC_GROUP_ID


def _unsupported_system_sync_group_message(group_id):
    return (
        f"系统计划同步仅支持{SYSTEM_SYNC_GROUP_NAME}（group_id={SYSTEM_SYNC_GROUP_ID}），"
        f"当前 group_id={str(group_id or '').strip() or '-'}。"
        "生财有术、洪灝的宏观策略请使用普通 Tab 同步或人工计划。"
    )


def _validate_supported_system_sync_group(group_id):
    if not _is_supported_system_sync_group(group_id):
        raise ValueError(_unsupported_system_sync_group_message(group_id))


def _is_auth_validation_error(error):
    message = str(error or "").lower()
    return (
        "401" in message
        or "unauthorized" in message
        or "未授权" in message
        or "登录" in message
    )


def _validate_group_id_local(group_id):
    try:
        _validate_group_id(str(group_id or "").strip())
        return None
    except ValueError as exc:
        return str(exc)


def _normalize_jobs(jobs):
    normalized = []
    for item in jobs or []:
        if not isinstance(item, dict):
            continue
        tab = item.get("tab")
        if not isinstance(tab, dict) or not tab.get("id") or not tab.get("label"):
            continue
        file_type = str(item.get("file_type") or "").strip().lower()
        if file_type not in {"pdf", "mp3", "all"}:
            continue
        mode_code = str(item.get("mode_code") or file_type[:2] or "x").strip().lower()
        mode_code = "".join(ch for ch in mode_code if ch.isalnum())[:4] or file_type[:2]
        normalized.append({
            "key": str(item.get("key") or f"{tab['id']}:{file_type}")[:80],
            "mode_code": mode_code,
            "file_type": file_type,
            "tab": {
                "id": str(tab.get("id")),
                "label": str(tab.get("label")),
                "scope": str(tab.get("scope") or "all"),
                **({"hashtagId": str(tab.get("hashtagId"))} if tab.get("hashtagId") else {}),
            },
        })
    return normalized


def _normalize_attachment_download_rules(value, jobs):
    jobs = _normalize_jobs(jobs)
    raw_rules = []
    enabled = True
    if isinstance(value, dict):
        enabled = bool(value.get("enabled", True))
        raw_rules = value.get("rules") if isinstance(value.get("rules"), list) else []

    rules = []
    if raw_rules:
        job_tabs = {
            str((job.get("tab") or {}).get("id") or ""): job
            for job in jobs
            if (job.get("tab") or {}).get("id")
        }
        for raw_rule in raw_rules:
            if not isinstance(raw_rule, dict):
                continue
            file_type = str(raw_rule.get("file_type") or "").strip().lower().lstrip(".")
            if file_type not in {"pdf", "mp3", "all"}:
                continue
            tab_id = str(raw_rule.get("tab_id") or "").strip()
            if tab_id and job_tabs and tab_id not in job_tabs:
                continue
            threshold = _normalize_int(
                raw_rule.get("min_download_count", raw_rule.get("threshold")),
                default=0,
                lower=0,
                upper=1_000_000,
            )
            tab = (job_tabs.get(tab_id) or {}).get("tab") or {}
            rules.append({
                "key": str(raw_rule.get("key") or f"{tab_id or '*'}:{file_type}")[:80],
                "tab_id": tab_id,
                "tab_label": str(raw_rule.get("tab_label") or tab.get("label") or ""),
                "file_type": file_type,
                "min_download_count": threshold,
            })

    if not rules:
        for job in jobs:
            file_type = str(job.get("file_type") or "").strip().lower()
            key = str(job.get("key") or "")
            tab = job.get("tab") or {}
            tab_label = str(tab.get("label") or "")
            threshold = None
            if file_type == "pdf" and ("foreign" in key or "外资" in tab_label):
                threshold = DEFAULT_FOREIGN_REPORT_PDF_DOWNLOAD_THRESHOLD
            elif file_type == "mp3" and ("memo" in key or "调研" in tab_label):
                threshold = DEFAULT_RESEARCH_MEMO_MP3_DOWNLOAD_THRESHOLD
            if threshold is None:
                continue
            rules.append({
                "key": str(job.get("key") or f"{tab.get('id')}:{file_type}")[:80],
                "tab_id": str(tab.get("id") or ""),
                "tab_label": tab_label,
                "file_type": file_type,
                "min_download_count": threshold,
            })

    return {
        "enabled": bool(enabled and rules),
        "rules": rules,
    }


def _normalize_auto_video_dispatch(value, previous=None):
    previous = previous if isinstance(previous, dict) else {}
    value = value if isinstance(value, dict) else {}
    enabled = bool(value.get("enabled", previous.get("enabled", False)))
    pdf_threshold = _bounded_int(
        value.get("pdf_min_download_count", previous.get("pdf_min_download_count")),
        default=DEFAULT_AUTO_VIDEO_PDF_THRESHOLD,
        lower=0,
        upper=1_000_000,
    )
    mp3_threshold = _bounded_int(
        value.get("mp3_min_download_count", previous.get("mp3_min_download_count")),
        default=DEFAULT_AUTO_VIDEO_MP3_THRESHOLD,
        lower=0,
        upper=1_000_000,
    )
    return {
        "enabled": enabled,
        "pdf_min_download_count": pdf_threshold,
        "mp3_min_download_count": mp3_threshold,
        "wait_for_completion": False,
    }


def _normalize_auto_script_material_dispatch(value, previous=None):
    previous = previous if isinstance(previous, dict) else {}
    value = value if isinstance(value, dict) else {}
    enabled = bool(value.get("enabled", previous.get("enabled", False)))
    pdf_threshold = _bounded_int(
        value.get("pdf_min_download_count", previous.get("pdf_min_download_count")),
        default=DEFAULT_AUTO_SCRIPT_MATERIAL_PDF_THRESHOLD,
        lower=0,
        upper=1_000_000,
    )
    return {
        "enabled": enabled,
        "pdf_min_download_count": pdf_threshold,
        "wait_for_completion": False,
    }


def _normalize_auto_publish_after_clean(value, previous=None):
    previous = previous if isinstance(previous, dict) else {}
    if isinstance(value, dict):
        enabled = bool(value.get("enabled", previous.get("enabled", False)))
        max_rounds = _bounded_int(value.get("max_rounds"), default=previous.get("max_rounds", 20), lower=1, upper=100)
        trigger = str(value.get("trigger") or previous.get("trigger") or "zsxq_system_sync_clean_complete").strip()
        rule = value.get("rule") if isinstance(value.get("rule"), dict) else previous.get("rule", {})
    elif value is None:
        enabled = bool(previous.get("enabled", False))
        max_rounds = _bounded_int(previous.get("max_rounds"), default=20, lower=1, upper=100)
        trigger = str(previous.get("trigger") or "zsxq_system_sync_clean_complete").strip()
        rule = previous.get("rule", {})
    else:
        enabled = bool(value)
        max_rounds = _bounded_int(previous.get("max_rounds"), default=20, lower=1, upper=100)
        trigger = str(previous.get("trigger") or "zsxq_system_sync_clean_complete").strip()
        rule = previous.get("rule", {})

    return {
        "enabled": enabled,
        "max_rounds": max_rounds,
        "trigger": trigger or "zsxq_system_sync_clean_complete",
        "rule": rule if isinstance(rule, dict) else {},
    }


def _bounded_int(value, default, lower, upper):
    return _normalize_int(value, default=default, lower=lower, upper=upper)


def _topic_pages_for_count(count):
    try:
        count = int(count)
    except (TypeError, ValueError):
        count = DEFAULT_COUNT
    count = min(max(count, 1), 50)
    return max(1, (count + TOPIC_PAGE_SIZE_HINT - 1) // TOPIC_PAGE_SIZE_HINT)


def _normalize_int(value, default, lower, upper):
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return min(max(number, lower), upper)


def _normalize_float(value, default, lower, upper):
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return min(max(number, lower), upper)


def _normalize_max_rounds(value, default, none_means_continuous=False):
    if value is None:
        return None if none_means_continuous else default
    if value in ("", 0, "0", False):
        return None if value in (0, "0", False) else default
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    if number <= 0:
        return None
    return min(number, 100)


def _normalize_interval_minutes(value, default=ENV_INTERVAL_MINUTES):
    if value is None or str(value).strip() == "":
        return default

    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    if number <= 0:
        return default
    return min(max(number, 1), 24 * 60)


def _scheduler_interval_minutes(interval_override=None):
    if interval_override is not None:
        return _normalize_interval_minutes(interval_override, default=ENV_INTERVAL_MINUTES)

    try:
        value = int(os.environ.get("ZSXQ_SYNC_TEST_INTERVAL_MINUTES", str(ENV_INTERVAL_MINUTES)))
    except ValueError:
        value = ENV_INTERVAL_MINUTES
    return min(max(value, 1), 24 * 60)
