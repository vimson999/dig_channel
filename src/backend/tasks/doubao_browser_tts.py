import logging
from datetime import datetime

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
        return _process_job(task_id)


def _process_job(task_id):
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
        final_result.update({
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
            _mark_failed(task, str(exc))
        logger.error("Doubao browser TTS task failed: %s", exc, exc_info=True)
        return False


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
