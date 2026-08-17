import logging
import os
import time
from datetime import datetime, timedelta
from urllib.parse import quote

from sqlalchemy.orm import load_only
from sqlalchemy.orm.attributes import flag_modified

from app import create_app
from celery_config import celery_app
from models import ReportStickFigureSceneAsset, db
from models.task import PublicationTask
from services.doubao_browser_tts import (
    FEATURE_NAME,
    DoubaoBrowserTTSCaptchaError,
    DoubaoBrowserTTSWorker,
    storage_path_for_job,
    validate_audio_duration_for_text,
)
from services.runtime_guard import end_db_transaction_before_external_io
from services.workflow_notification_service import workflow_notification_service


logger = logging.getLogger("DOUBAO_BROWSER_TTS_TASK")

DEFAULT_RETRY_LIMIT = 2
DEFAULT_RETRY_DELAY_SECONDS = 20
MAX_RETRY_LIMIT = 5
PERSISTED_PROGRESS_STEPS = {
    "clear_audio_cache",
    "submit_text",
    "wait_response",
    "capture_audio",
    "save_file",
}


class DoubaoBrowserTTSTaskCanceled(RuntimeError):
    pass


RETRYABLE_ERROR_FRAGMENTS = (
    "豆包音频时长异常",
    "疑似朗读未完成",
    "空音频",
    "等待音频捕获",
    "导出 WAV",
    "WAV 已保存",
    "音频捕获",
    "未捕获到 PCM 音频",
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
    PublicationTask.celery_task_id,
    PublicationTask.status,
    PublicationTask.current_step,
    PublicationTask.progress,
    PublicationTask.error_message,
    PublicationTask.created_at,
    PublicationTask.completed_at,
)
TASK_PAYLOAD_COLUMNS = (
    *TASK_UPDATE_COLUMNS,
    PublicationTask.params,
    PublicationTask.result,
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
    return (
        PublicationTask.query.options(load_only(*attrs))
        .filter(PublicationTask.id == str(task_id))
        .one_or_none()
    )


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
    request_id = str(
        getattr(getattr(celery_task, "request", None), "id", "") or ""
    )
    current_celery_id = str(getattr(task, "celery_task_id", "") or "")
    if request_id and current_celery_id and request_id != current_celery_id:
        logger.info(
            "Doubao browser TTS task received stale delivery; task=%s request=%s current=%s",
            task_id,
            request_id,
            current_celery_id,
        )
        return False
    if task.status == "completed":
        logger.info("Doubao browser TTS task already completed; skip duplicate: %s", task_id)
        return True
    if task.status in {"failed", "blocked_by_captcha", "canceled"}:
        return False

    job_id = task.id
    job_created_at = task.created_at
    payload = task.params.get("doubao_browser_tts") or {}
    text = (payload.get("text") or "").strip() or _text_from_reference(payload.get("text_source"))
    retry_limit = _retry_limit(task)
    retry_delay_seconds = _retry_delay_seconds(task)
    scene_asset_id = _scene_asset_id(task)
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
    _update_scene_asset_tts_state(
        scene_asset_id,
        task_id,
        status="processing",
        progress=3,
        error=None,
    )
    db.session.commit()
    end_db_transaction_before_external_io("doubao browser synthesis")
    progress_result = dict(result)

    def update_progress(event):
        nonlocal progress_result
        current_result = dict(progress_result)
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
        step = event.get("step") or "processing"
        progress = int(event.get("progress") or 0)
        progress_result = current_result
        if step not in PERSISTED_PROGRESS_STEPS:
            return
        last_error = None
        for attempt in range(1, 4):
            try:
                db.session.rollback()
                scene_updated = _update_scene_asset_tts_state(
                    scene_asset_id,
                    task_id,
                    status="processing",
                    progress=progress,
                    error=None,
                    require_active_task=True,
                )
                if not scene_updated:
                    updated = (
                        PublicationTask.query
                        .filter(
                            PublicationTask.id == str(task_id),
                            PublicationTask.status != "canceled",
                        )
                        .update(
                            {
                                PublicationTask.current_step: step,
                                PublicationTask.progress: progress,
                                PublicationTask.updated_at: datetime.utcnow(),
                            },
                            synchronize_session=False,
                        )
                    )
                    if not updated:
                        raise DoubaoBrowserTTSTaskCanceled("任务已取消")
                db.session.commit()
                progress_result = current_result
                return
            except DoubaoBrowserTTSTaskCanceled:
                db.session.rollback()
                raise
            except Exception as exc:
                last_error = exc
                db.session.rollback()
                db.session.remove()
                if attempt < 3:
                    time.sleep(attempt)
        raise last_error

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
        if fresh.status == "canceled":
            return False
        final_result = dict(progress_result)
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
            "page_url": audio.get("page_url") or final_result.get("page_url"),
            "completed_at": datetime.utcnow().isoformat() + "Z",
        })
        fresh.status = "completed"
        fresh.current_step = "done"
        fresh.progress = 100
        fresh.completed_at = datetime.utcnow()
        fresh.error_message = None
        fresh.result = final_result
        flag_modified(fresh, "result")
        _update_scene_asset_tts_state(
            scene_asset_id,
            task_id,
            status="completed",
            progress=100,
            error=None,
            audio_path=audio.get("wav_path"),
            audio_bytes=audio.get("bytes"),
            audio_seconds=audio.get("seconds"),
        )
        db.session.commit()
        logger.info("Doubao browser TTS task completed: %s", task_id)
        return True
    except DoubaoBrowserTTSTaskCanceled:
        db.session.rollback()
        logger.info("Doubao browser TTS task canceled while running: %s", task_id)
        return False
    except DoubaoBrowserTTSCaptchaError as exc:
        db.session.rollback()
        task = _load_task(task_id, *TASK_UPDATE_COLUMNS)
        if task:
            if task.status == "canceled":
                return False
            _mark_blocked_by_captcha(
                task,
                exc,
                result_snapshot=progress_result,
                scene_asset_id=scene_asset_id,
            )
        logger.warning("Doubao browser TTS task blocked by captcha: %s", task_id, exc_info=True)
        return False
    except Exception as exc:
        db.session.rollback()
        task = _load_task(task_id, *TASK_UPDATE_COLUMNS)
        if task:
            if task.status == "canceled":
                return False
            if _should_auto_retry(task, exc, celery_task, retry_limit=retry_limit):
                _mark_retrying(
                    task,
                    exc,
                    celery_task,
                    retry_limit=retry_limit,
                    retry_delay_seconds=retry_delay_seconds,
                    result_snapshot=progress_result,
                    scene_asset_id=scene_asset_id,
                )
                raise celery_task.retry(
                    exc=exc,
                    countdown=retry_delay_seconds,
                    max_retries=retry_limit,
                )
            _mark_failed(
                task,
                str(exc),
                result_snapshot=progress_result,
                scene_asset_id=scene_asset_id,
            )
        logger.error("Doubao browser TTS task failed: %s", exc, exc_info=True)
        return False


def _should_auto_retry(task, exc, celery_task, *, retry_limit=None):
    if not celery_task or not hasattr(celery_task, "retry"):
        return False
    limit = _retry_limit(task) if retry_limit is None else int(retry_limit)
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


def _scene_asset_id(task):
    params = task.params if task and isinstance(task.params, dict) else {}
    payload = params.get("doubao_browser_tts") if isinstance(params.get("doubao_browser_tts"), dict) else {}
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    return str(metadata.get("scene_asset_id") or "").strip() or None


def _update_scene_asset_tts_state(
    scene_asset_id,
    task_id,
    *,
    status,
    progress,
    error,
    audio_path=None,
    audio_bytes=None,
    audio_seconds=None,
    require_active_task=False,
):
    if not scene_asset_id:
        return 0
    values = {
        ReportStickFigureSceneAsset.tts_status: status,
        ReportStickFigureSceneAsset.tts_progress: int(progress or 0),
        ReportStickFigureSceneAsset.tts_error: error,
        ReportStickFigureSceneAsset.updated_at: datetime.utcnow(),
    }
    if status == "completed":
        values.update({
            ReportStickFigureSceneAsset.audio_path: audio_path,
            ReportStickFigureSceneAsset.audio_bytes: audio_bytes,
            ReportStickFigureSceneAsset.audio_seconds: audio_seconds,
        })
    query = ReportStickFigureSceneAsset.query.filter(
        ReportStickFigureSceneAsset.id == str(scene_asset_id),
        ReportStickFigureSceneAsset.tts_task_id == str(task_id),
    )
    if require_active_task:
        active_task_exists = db.session.query(PublicationTask.id).filter(
            PublicationTask.id == str(task_id),
            PublicationTask.status != "canceled",
        ).exists()
        query = query.filter(active_task_exists)
    return query.update(values, synchronize_session=False)


def _mark_retrying(
    task,
    exc,
    celery_task,
    *,
    retry_limit=None,
    retry_delay_seconds=None,
    result_snapshot=None,
    scene_asset_id=None,
):
    now_dt = datetime.utcnow()
    now = now_dt.isoformat() + "Z"
    retries = int(getattr(getattr(celery_task, "request", None), "retries", 0) or 0)
    retry_number = retries + 1
    limit = _retry_limit(task) if retry_limit is None else int(retry_limit)
    delay = _retry_delay_seconds(task) if retry_delay_seconds is None else int(retry_delay_seconds)
    message = str(exc)
    result = dict(result_snapshot if isinstance(result_snapshot, dict) else (task.result or {}))
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
    _update_scene_asset_tts_state(
        scene_asset_id,
        task.id,
        status="queued",
        progress=task.progress or 0,
        error=None,
    )
    db.session.commit()


def _mark_failed(task, message, *, result_snapshot=None, scene_asset_id=None):
    result = dict(result_snapshot if isinstance(result_snapshot, dict) else (task.result or {}))
    result["error"] = message
    result["completed_at"] = datetime.utcnow().isoformat() + "Z"
    task.status = "failed"
    task.current_step = "failed"
    task.progress = task.progress or 0
    task.error_message = message
    task.completed_at = datetime.utcnow()
    task.result = result
    flag_modified(task, "result")
    _update_scene_asset_tts_state(
        scene_asset_id,
        task.id,
        status="failed",
        progress=task.progress or 0,
        error=message,
    )
    db.session.commit()


def _mark_blocked_by_captcha(task, exc, *, result_snapshot=None, scene_asset_id=None):
    now = datetime.utcnow()
    message = str(exc) or "豆包页面触发人机验证，浏览器自动化已被阻塞"
    captcha = {
        "blocked": True,
        "reason": "captcha",
        "message": message,
        "page_url": getattr(exc, "page_url", "") or "",
        "screenshot_path": getattr(exc, "screenshot", "") or "",
        "profile": getattr(exc, "profile", "") or "",
        "worker_id": getattr(exc, "worker_id", "") or "",
        "requires_human_action": True,
        "blocked_at": now.isoformat() + "Z",
    }
    result = dict(result_snapshot if isinstance(result_snapshot, dict) else (task.result or {}))
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
    _update_scene_asset_tts_state(
        scene_asset_id,
        task.id,
        status="blocked",
        progress=task.progress or 0,
        error=message,
    )
    db.session.commit()
    _send_captcha_blocked_email(task.id)


def _captcha_human_intervention_context(task, captcha):
    params = task.params if isinstance(task.params, dict) else {}
    payload = (
        params.get("doubao_browser_tts")
        if isinstance(params.get("doubao_browser_tts"), dict)
        else {}
    )
    metadata = (
        payload.get("metadata")
        if isinstance(payload.get("metadata"), dict)
        else {}
    )
    parent_task_id = str(
        metadata.get("parent_task_id") or params.get("parent_task_id") or ""
    ).strip()
    base_url = str(
        os.getenv("REPORT_AUDIO_WORKFLOW_FRONTEND_BASE_URL")
        or os.getenv("REPORT_WORKFLOW_FRONTEND_BASE_URL")
        or os.getenv("FRONTEND_BASE_URL")
        or "http://localhost:5001"
    ).strip().rstrip("/")
    workflow_url = (
        f"{base_url}/report-audio-workflow-detail.html?job_id={quote(parent_task_id)}"
        if parent_task_id
        else ""
    )
    return {
        "parent_task_id": parent_task_id,
        "workflow_url": workflow_url,
        "profile": str(
            captcha.get("profile")
            or payload.get("user_data_dir")
            or os.getenv("DOUBAO_BROWSER_TTS_USER_DATA_DIR")
            or ""
        ),
        "worker_id": str(
            captcha.get("worker_id")
            or metadata.get("worker_id")
            or os.getenv("DOUBAO_BROWSER_TTS_WORKER_ID")
            or "default"
        ),
        "resolution_steps": [
            "使用通知中指定的浏览器 Profile 打开豆包页面",
            "完成验证码或安全验证，并确认页面可以正常发起朗读",
            "返回工作流详情页，点击“从失败处续跑”",
        ],
    }


def _send_captcha_blocked_email(task_id):
    task = _load_task(task_id, *TASK_PAYLOAD_COLUMNS)
    if not task:
        return False
    result = dict(task.result or {})
    notifications = dict(result.get("notifications") or {})
    event_key = "doubao_captcha_blocked"
    captcha = dict(result.get("captcha_blocked") or {}) if isinstance(result.get("captcha_blocked"), dict) else {}
    params = task.params if isinstance(task.params, dict) else {}
    payload = params.get("doubao_browser_tts") if isinstance(params.get("doubao_browser_tts"), dict) else {}
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    context = _captcha_human_intervention_context(task, captcha)
    captcha.update(context)
    result["captcha_blocked"] = captcha
    if notifications.get(event_key, {}).get("sent"):
        task.result = result
        flag_modified(task, "result")
        db.session.commit()
        return True

    notification = {
        "event_type": event_key,
        "sent_at": datetime.utcnow().isoformat() + "Z",
    }
    try:
        send_result = workflow_notification_service.send_browser_human_intervention(
            provider="豆包",
            reason="verification",
            message=captcha.get("message") or task.error_message or "豆包页面触发人机验证",
            job_id=str(task.id),
            page_url=captcha.get("page_url") or "",
            screenshot_path=captcha.get("screenshot_path") or "",
            profile=captcha.get("profile") or "",
            worker_id=captcha.get("worker_id") or "",
            details={
                "父编排任务": captcha.get("parent_task_id") or "-",
                "工作流详情": captcha.get("workflow_url") or "-",
                "分段": f"{metadata.get('chunk_index') or '-'}/{metadata.get('chunk_total') or '-'}",
                "尝试": metadata.get("chunk_attempt") or "-",
                "文本长度": f"{len(str(payload.get('text') or ''))} 字",
            },
            resolution_steps=captcha.get("resolution_steps") or [],
        )
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


def _text_from_reference(reference):
    if not isinstance(reference, dict) or reference.get("kind") != "report_visual_plan_segment":
        return ""
    segment_id = str(reference.get("segment_id") or "").strip()
    if not segment_id:
        return ""
    from models.report_visual_plan import ReportVisualPlanSegment

    row = (
        db.session.query(ReportVisualPlanSegment.script_text)
        .filter(ReportVisualPlanSegment.id == segment_id)
        .first()
    )
    return str(row[0] or "").strip() if row else ""


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
