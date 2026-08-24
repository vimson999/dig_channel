"""Bounded automatic recovery for terminal report-audio orchestration failures.

The report-audio task is checkpointed after every expensive stage and its child
creation is idempotent.  A transient browser/network/DB failure can therefore
resume the same parent safely, while authentication, CAPTCHA, configuration,
and content-quality failures must remain explicit human decisions.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import load_only
from sqlalchemy.orm.attributes import flag_modified

from models import db
from models.task import PublicationTask
from services.runtime_guard import maybe_guarded_apply_async, release_db_session_quietly


logger = logging.getLogger("REPORT_AUDIO_RECOVERY")

FEATURES = ("report_audio_workflow", "report_pdf_audio_workflow")
QUEUE_NAME = "report_audio_workflow"
RESULT_KEY = "automatic_recovery"
DEFAULT_GRACE_SECONDS = 60
DEFAULT_MAX_ATTEMPTS = 2
DEFAULT_MAX_AGE_SECONDS = 24 * 60 * 60

RETRYABLE_FRAGMENTS = (
    "超时",
    "timeout",
    "timed out",
    "net::err_",
    "connection closed",
    "server closed the connection",
    "ssl connection",
    "数据库",
    "database",
    "target closed",
    "browser closed",
    "context or browser has been closed",
    "protocol error",
    "文件上传失败",
    "未找到 gemini 文件上传",
    "未找到 gemini 输入框",
    "提示词未成功提交",
    "等待 gemini 回复",
    "豆包音频时长异常",
    "等待豆包回复",
    "未捕获到 pcm 音频",
    "等待音频捕获",
    "profile 正在被其他 chrome 占用",
    "入队失败",
    "celery 子任务异常退出",
)

HUMAN_OR_PERMANENT_FRAGMENTS = (
    "验证码",
    "人机验证",
    "captcha",
    "登录态失效",
    "需要先在浏览器 profile 中登录",
    "pdf 文件不能为空",
    "任务不存在",
    "子任务不存在",
    "内容过短",
    "正文过短",
    "结构化结果没有可朗读正文",
    "未配置",
    "额度已耗尽",
)


def _int_env(name, default, minimum=0, maximum=100):
    try:
        value = int(os.getenv(name, default))
    except (TypeError, ValueError):
        value = int(default)
    return max(minimum, min(maximum, value))


def retryable_report_audio_failure(message):
    text = str(message or "").strip().lower()
    if not text or any(fragment in text for fragment in HUMAN_OR_PERMANENT_FRAGMENTS):
        return False
    return any(fragment in text for fragment in RETRYABLE_FRAGMENTS)


def _claim_failed_task(task_id, *, max_attempts, failure_not_before):
    task = (
        PublicationTask.query.options(load_only(
            PublicationTask.id,
            PublicationTask.feature,
            PublicationTask.status,
            PublicationTask.result,
            PublicationTask.error_message,
            PublicationTask.created_at,
            PublicationTask.updated_at,
            PublicationTask.completed_at,
        ))
        .filter(
            PublicationTask.id == str(task_id),
            PublicationTask.feature.in_(FEATURES),
            PublicationTask.status == "failed",
        )
        .with_for_update()
        .one_or_none()
    )
    if not task:
        db.session.rollback()
        return None, "state_changed"
    terminal_at = task.completed_at or task.updated_at or task.created_at
    if (
        not task.created_at
        or task.created_at < failure_not_before
        or not terminal_at
        or terminal_at < failure_not_before
    ):
        db.session.rollback()
        return None, "outside_recovery_window"
    result = dict(task.result or {})
    error = str(task.error_message or result.get("error") or "")
    if not retryable_report_audio_failure(error):
        db.session.rollback()
        return None, "not_retryable"
    recovery = dict(result.get(RESULT_KEY) or {})
    count = int(recovery.get("count") or 0)
    if count >= max_attempts:
        db.session.rollback()
        return None, "limit_reached"

    now = datetime.utcnow()
    history = list(recovery.get("history") or [])
    history.append({
        "attempt": count + 1,
        "claimed_at": now.isoformat() + "Z",
        "previous_error": error[:1000],
    })
    recovery.update({
        "count": count + 1,
        "max_attempts": max_attempts,
        "last_claimed_at": now.isoformat() + "Z",
        "previous_error": error[:1000],
        "history": history[-max_attempts:],
    })
    result[RESULT_KEY] = recovery
    result["error"] = None
    result.pop("error_type", None)
    result.pop("completed_at", None)
    task.status = "pending"
    task.current_step = "queued"
    task.error_message = None
    task.completed_at = None
    task.celery_task_id = None
    task.result = result
    task.updated_at = now
    flag_modified(task, "result")
    snapshot = {
        "task_id": str(task.id),
        "feature": str(task.feature),
        "attempt": count + 1,
    }
    db.session.commit()
    release_db_session_quietly()
    return snapshot, "claimed"


def repair_failed_report_audio_workflows_once(limit=10):
    """Resume recent retryable failures, bounded independently per parent."""

    grace_seconds = _int_env(
        "REPORT_AUDIO_FAILED_RECOVERY_GRACE_SECONDS",
        DEFAULT_GRACE_SECONDS,
        15,
        3600,
    )
    max_attempts = _int_env(
        "REPORT_AUDIO_FAILED_RECOVERY_MAX_ATTEMPTS",
        DEFAULT_MAX_ATTEMPTS,
        0,
        5,
    )
    if max_attempts <= 0:
        return {"enabled": False, "checked": 0, "requeued": 0, "items": []}
    max_age_seconds = _int_env(
        "REPORT_AUDIO_FAILED_RECOVERY_MAX_AGE_SECONDS",
        DEFAULT_MAX_AGE_SECONDS,
        300,
        7 * 24 * 60 * 60,
    )
    now = datetime.utcnow()
    stale_before = now - timedelta(seconds=grace_seconds)
    failure_not_before = now - timedelta(seconds=max_age_seconds)
    terminal_at = func.coalesce(
        PublicationTask.completed_at,
        PublicationTask.updated_at,
        PublicationTask.created_at,
    )
    rows = (
        db.session.query(PublicationTask.id)
        .filter(
            PublicationTask.feature.in_(FEATURES),
            PublicationTask.status == "failed",
            PublicationTask.updated_at <= stale_before,
            PublicationTask.created_at >= failure_not_before,
            terminal_at >= failure_not_before,
        )
        .order_by(PublicationTask.updated_at.asc())
        .limit(max(1, min(int(limit or 10), 50)))
        .all()
    )
    ids = [str(row.id) for row in rows]
    db.session.commit()
    release_db_session_quietly()
    items = []
    requeued = 0
    from tasks.report_audio_workflow import process_report_audio_workflow_job

    for task_id in ids:
        try:
            claimed, reason = _claim_failed_task(
                task_id,
                max_attempts=max_attempts,
                failure_not_before=failure_not_before,
            )
            if not claimed:
                items.append({"task_id": task_id, "requeued": False, "reason": reason})
                continue
            submit = maybe_guarded_apply_async(
                process_report_audio_workflow_job,
                args=[task_id],
                queue=QUEUE_NAME,
                feature=claimed["feature"],
                task_id=task_id,
                retry=False,
            )
            celery_id = str(submit["async_result"].id)
            task = PublicationTask.query.options(load_only(
                PublicationTask.id,
                PublicationTask.status,
                PublicationTask.result,
            )).filter(PublicationTask.id == task_id).one_or_none()
            if task and task.status in {"pending", "processing"}:
                result = dict(task.result or {})
                recovery = dict(result.get(RESULT_KEY) or {})
                recovery.update({
                    "last_celery_task_id": celery_id,
                    "last_enqueued_at": datetime.utcnow().isoformat() + "Z",
                    "deferred": bool(submit.get("deferred")),
                })
                result[RESULT_KEY] = recovery
                task.celery_task_id = celery_id
                task.result = result
                flag_modified(task, "result")
                db.session.commit()
            release_db_session_quietly()
            requeued += 1
            items.append({
                **claimed,
                "celery_task_id": celery_id,
                "requeued": True,
                "deferred": bool(submit.get("deferred")),
            })
        except Exception as exc:
            db.session.rollback()
            release_db_session_quietly()
            logger.exception("Failed report-audio automatic recovery task=%s", task_id)
            # The task remains pending after a successful claim. The generic
            # pending-message repair will safely publish it if this publish was
            # the operation that failed.
            items.append({
                "task_id": task_id,
                "requeued": False,
                "reason": "error",
                "error": str(exc),
            })
    return {
        "enabled": True,
        "checked": len(ids),
        "requeued": requeued,
        "items": items,
    }
