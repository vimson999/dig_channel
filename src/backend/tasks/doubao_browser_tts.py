import logging
import os
from datetime import datetime, timedelta

from sqlalchemy.orm import load_only
from sqlalchemy.orm.attributes import flag_modified

from app import create_app
from celery_config import celery_app
from models import db
from models.task import PublicationTask
from services.doubao_browser_tts import (
    FEATURE_NAME,
    DoubaoBrowserTTSCaptchaError,
    DoubaoBrowserTTSWorker,
    storage_path_for_job,
    validate_audio_duration_for_text,
)
from services.workflow_notification_service import workflow_notification_service


logger = logging.getLogger("DOUBAO_BROWSER_TTS_TASK")

DEFAULT_RETRY_LIMIT = 2
DEFAULT_RETRY_DELAY_SECONDS = 20
MAX_RETRY_LIMIT = 5

RETRYABLE_ERROR_FRAGMENTS = (
    "豆包音频时长异常",
    "疑似朗读未完成",
    "空音频",
    "等待音频捕获",
    "导出 WAV",
    "WAV 已保存",
    "音频捕获",
    "页面自动化超时",
    "Timeout",
    "timed out",
    "Target closed",
    "Browser closed",
    "Protocol error",
    "net::ERR",
)

NON_RETRYABLE_ERROR_FRAGMENTS = (
    "文本不能为空",
    "任务已取消",
    "任务不存在",
    "人机验证",
    "captcha",
)


TASK_UPDATE_COLUMNS = (
    PublicationTask.id,
    PublicationTask.status,
    PublicationTask.current_step,
    PublicationTask.progress,
    PublicationTask.result,
    PublicationTask.error_message,
    PublicationTask.created_at,
    PublicationTask.completed_at,
)
TASK_PAYLOAD_COLUMNS = (
    *TASK_UPDATE_COLUMNS,
    PublicationTask.params,
    PublicationTask.feature,
)


def _load_task(task_id, *columns):
    seen = set()
    attrs = []
    for column in (PublicationTask.id, *columns):
        key = getattr(column, "key", str(column))
        if key in seen:
            continue
        seen.add(key)
        attrs.append(column)
    return PublicationTask.query.options(load_only(*attrs)).get(task_id)


@celery_app.task(
    bind=True,
    name="tasks.doubao_browser_tts.process_job",
    store_errors_even_if_ignored=True,
)
def process_doubao_browser_tts_job(self, task_id):
    app = create_app()
    with app.app_context():
        return _process_job(task_id, celery_task=self)


def _process_job(task_id, celery_task=None):
    task = _load_task(task_id, *TASK_PAYLOAD_COLUMNS)
    if not task:
        logger.error("Doubao browser TTS task not found: %s", task_id)
        return False
    if not isinstance(task.params, dict) or task.params.get("feature") != FEATURE_NAME:
        logger.error("Invalid Doubao browser TTS task params: %s", task_id)
        return False
    if task.status == "canceled":
        return False

    job_id = task.id
    job_created_at = task.created_at
    payload = task.params.get("doubao_browser_tts") or {}
    text = (payload.get("text") or "").strip()
    if not text:
        _mark_failed(task, "文本不能为空")
        return False

    result = dict(task.result or {})
    result["started_at"] = datetime.utcnow().isoformat() + "Z"
    result["logs"] = result.get("logs") or []
    task.status = "processing"
    task.current_step = "prepare_browser"
    task.progress = 3
    task.result = result
    flag_modified(task, "result")
    db.session.commit()

    def update_progress(event):
        fresh = _load_task(task_id, *TASK_UPDATE_COLUMNS)
        if not fresh or fresh.status == "canceled":
            raise RuntimeError("任务已取消")
        current_result = dict(fresh.result or {})
        logs = list(current_result.get("logs") or [])
        logs.append({
            "step": event.get("step"),
            "message": event.get("message"),
            "progress": event.get("progress"),
            "updated_at": event.get("updated_at"),
        })
        current_result["logs"] = logs[-80:]
        if event.get("result"):
            current_result.update(event["result"])
        fresh.current_step = event.get("step") or fresh.current_step
        fresh.progress = int(event.get("progress") or fresh.progress or 0)
        fresh.result = current_result
        flag_modified(fresh, "result")
        db.session.commit()

    try:
        output_path = storage_path_for_job(job_id, job_created_at)
        worker = DoubaoBrowserTTSWorker()
        audio = worker.synthesize(
            job_id=job_id,
            text=text,
            output_path=output_path,
            progress_callback=update_progress,
        )
        audio = validate_audio_duration_for_text(audio, text)

        fresh = _load_task(task_id, *TASK_UPDATE_COLUMNS)
        if not fresh:
            raise RuntimeError("任务不存在")
        final_result = dict(fresh.result or {})
        metadata = dict(final_result.get("metadata") or {})
        metadata.update({
            "worker_id": os.getenv("DOUBAO_BROWSER_TTS_WORKER_ID", "default"),
            "user_data_dir": audio.get("user_data_dir"),
            "profile_directory": audio.get("profile_directory"),
        })
        final_result.update({
            "metadata": {key: value for key, value in metadata.items() if value not in (None, "")},
            "audio": {
                "wav_path": audio.get("wav_path"),
                "chunks": audio.get("chunks"),
                "seconds": audio.get("seconds"),
                "sample_rate": audio.get("sample_rate"),
                "bytes": audio.get("bytes"),
            },
            "page_url": audio.get("page_url"),
            "completed_at": datetime.utcnow().isoformat() + "Z",
        })
        fresh.status = "completed"
        fresh.current_step = "done"
        fresh.progress = 100
        fresh.completed_at = datetime.utcnow()
        fresh.error_message = None
        fresh.result = final_result
        flag_modified(fresh, "result")
        db.session.commit()
        logger.info("Doubao browser TTS task completed: %s", task_id)
        return True
    except DoubaoBrowserTTSCaptchaError as exc:
        db.session.rollback()
        task = _load_task(task_id, *TASK_UPDATE_COLUMNS)
        if task:
            _mark_blocked_by_captcha(task, exc)
        logger.warning("Doubao browser TTS task blocked by captcha: %s", task_id, exc_info=True)
        return False
    except Exception as exc:
        db.session.rollback()
        task = _load_task(task_id, *TASK_UPDATE_COLUMNS)
        if task:
            if _should_auto_retry(task, exc, celery_task):
                _mark_retrying(task, exc, celery_task)
                raise celery_task.retry(
                    exc=exc,
                    countdown=_retry_delay_seconds(task),
                    max_retries=_retry_limit(task),
                )
            _mark_failed(task, str(exc))
        logger.error("Doubao browser TTS task failed: %s", exc, exc_info=True)
        return False


def _should_auto_retry(task, exc, celery_task):
    if not celery_task or not hasattr(celery_task, "retry"):
        return False
    limit = _retry_limit(task)
    if limit <= 0:
        return False
    retries = int(getattr(getattr(celery_task, "request", None), "retries", 0) or 0)
    if retries >= limit:
        return False
    return _is_retryable_error(exc)


def _is_retryable_error(exc):
    message = str(exc or "")
    if not message:
        return False
    lowered = message.lower()
    for fragment in NON_RETRYABLE_ERROR_FRAGMENTS:
        if fragment.lower() in lowered:
            return False
    return any(fragment.lower() in lowered for fragment in RETRYABLE_ERROR_FRAGMENTS)


def _retry_limit(task):
    payload = ((task.params or {}).get("doubao_browser_tts") or {}) if task else {}
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    raw = payload.get("retry_limit")
    if raw is None:
        raw = metadata.get("retry_limit")
    if raw is None:
        raw = os.getenv("DOUBAO_BROWSER_TTS_RETRY_LIMIT", DEFAULT_RETRY_LIMIT)
    try:
        return max(0, min(int(raw), MAX_RETRY_LIMIT))
    except (TypeError, ValueError):
        return DEFAULT_RETRY_LIMIT


def _retry_delay_seconds(task):
    payload = ((task.params or {}).get("doubao_browser_tts") or {}) if task else {}
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    raw = payload.get("retry_delay_seconds")
    if raw is None:
        raw = metadata.get("retry_delay_seconds")
    if raw is None:
        raw = os.getenv("DOUBAO_BROWSER_TTS_RETRY_DELAY_SECONDS", DEFAULT_RETRY_DELAY_SECONDS)
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return DEFAULT_RETRY_DELAY_SECONDS


def _mark_retrying(task, exc, celery_task):
    now_dt = datetime.utcnow()
    now = now_dt.isoformat() + "Z"
    retries = int(getattr(getattr(celery_task, "request", None), "retries", 0) or 0)
    retry_number = retries + 1
    limit = _retry_limit(task)
    delay = _retry_delay_seconds(task)
    message = str(exc)
    result = dict(task.result or {})
    logs = list(result.get("logs") or [])
    logs.append({
        "step": "auto_retry",
        "message": f"自动重试 {retry_number}/{limit}: {message}",
        "progress": task.progress or 0,
        "updated_at": now,
    })
    result.update({
        "last_error": message,
        "retry_state": {
            "retry_number": retry_number,
            "retry_limit": limit,
            "retry_delay_seconds": delay,
            "next_retry_at": (now_dt + timedelta(seconds=delay)).isoformat() + "Z",
            "updated_at": now,
        },
        "logs": logs[-80:],
    })
    task.status = "pending"
    task.current_step = "auto_retry"
    task.error_message = None
    task.completed_at = None
    task.result = result
    flag_modified(task, "result")
    db.session.commit()


def _mark_failed(task, message):
    result = dict(task.result or {})
    result["error"] = message
    result["completed_at"] = datetime.utcnow().isoformat() + "Z"
    task.status = "failed"
    task.current_step = "failed"
    task.progress = task.progress or 0
    task.error_message = message
    task.completed_at = datetime.utcnow()
    task.result = result
    flag_modified(task, "result")
    db.session.commit()


def _mark_blocked_by_captcha(task, exc):
    now = datetime.utcnow()
    message = str(exc) or "豆包页面触发人机验证，浏览器自动化已被阻塞"
    captcha = {
        "blocked": True,
        "reason": "captcha",
        "message": message,
        "page_url": getattr(exc, "page_url", "") or "",
        "screenshot_path": getattr(exc, "screenshot", "") or "",
        "requires_human_action": True,
        "blocked_at": now.isoformat() + "Z",
    }
    result = dict(task.result or {})
    logs = list(result.get("logs") or [])
    logs.append({
        "step": "blocked_by_captcha",
        "message": message,
        "progress": task.progress or 0,
        "updated_at": captcha["blocked_at"],
    })
    result.update({
        "error": message,
        "error_type": "blocked_by_captcha",
        "blocked_reason": "captcha",
        "captcha_blocked": captcha,
        "logs": logs[-80:],
        "completed_at": captcha["blocked_at"],
    })
    task.status = "blocked_by_captcha"
    task.current_step = "blocked_by_captcha"
    task.progress = task.progress or 0
    task.error_message = message
    task.completed_at = now
    task.result = result
    flag_modified(task, "result")
    db.session.commit()
    _send_captcha_blocked_email(task.id)


def _send_captcha_blocked_email(task_id):
    task = _load_task(task_id, *TASK_PAYLOAD_COLUMNS)
    if not task:
        return False
    result = dict(task.result or {})
    notifications = dict(result.get("notifications") or {})
    event_key = "doubao_captcha_blocked"
    if notifications.get(event_key, {}).get("sent"):
        return True

    captcha = result.get("captcha_blocked") if isinstance(result.get("captcha_blocked"), dict) else {}
    subject = f"[豆包浏览器朗读] 人机验证阻塞 · {str(task.id)[:8]}"
    body = _captcha_blocked_email_body(task, captcha)
    notification = {
        "event_type": event_key,
        "sent_at": datetime.utcnow().isoformat() + "Z",
    }
    try:
        send_result = workflow_notification_service.send_email_message(subject, body)
        notification.update(send_result if isinstance(send_result, dict) else {"sent": False, "result": send_result})
        logger.info("[DOUBAO_BROWSER_TTS] captcha blocked email result | task=%s result=%s", task.id, send_result)
    except Exception as exc:
        notification.update({"sent": False, "reason": "send_failed", "error": str(exc)})
        logger.exception("[DOUBAO_BROWSER_TTS] captcha blocked email failed | task=%s", task.id)

    notifications[event_key] = notification
    result["notifications"] = notifications
    task.result = result
    flag_modified(task, "result")
    db.session.commit()
    return bool(notification.get("sent"))


def _captcha_blocked_email_body(task, captcha):
    params = task.params if isinstance(task.params, dict) else {}
    payload = params.get("doubao_browser_tts") if isinstance(params.get("doubao_browser_tts"), dict) else {}
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    text = str(payload.get("text") or "")
    parent_task_id = metadata.get("parent_task_id") or params.get("parent_task_id") or "-"
    chunk_index = metadata.get("chunk_index") or "-"
    chunk_total = metadata.get("chunk_total") or "-"
    chunk_attempt = metadata.get("chunk_attempt") or "-"
    lines = [
        "事件：豆包浏览器朗读被人机验证阻塞",
        f"任务 ID：{task.id}",
        f"父编排任务：{parent_task_id}",
        f"分段：{chunk_index}/{chunk_total}",
        f"尝试：{chunk_attempt}",
        f"状态：blocked_by_captcha / 当前阶段：{task.current_step or '-'}",
        f"页面：{captcha.get('page_url') or '-'}",
        f"截图：{captcha.get('screenshot_path') or '-'}",
        f"文本长度：{len(text)} 字",
        f"原因：{captcha.get('message') or task.error_message or '-'}",
        "",
        "处理方式：请进入对应 Chrome/豆包浏览器窗口，手动完成验证，然后重试任务。",
        "系统处理：已停止当前任务的自动重试，避免持续触发第三方风控。",
        "",
        f"发送时间：{datetime.utcnow().isoformat()}Z",
    ]
    return "\n".join(lines)
