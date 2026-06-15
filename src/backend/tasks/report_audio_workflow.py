import logging
import os
import time
from datetime import datetime

from sqlalchemy.orm.attributes import flag_modified

from app import create_app
from celery_config import celery_app
from models import db
from models.task import PublicationTask
from services import doubao_browser_tts as doubao_service
from services import gemini_browser as gemini_service
from services.report_audio_workflow import (
    DEFAULT_INITIAL_PROMPT,
    DEFAULT_REVIEW_PROMPT,
    FEATURE_NAME,
    audio_path_for_job,
    combine_wav_files,
    normalize_for_tts,
    split_tts_text,
    storage_path_for_job,
)
from services.workflow_notification_service import workflow_notification_service
from tasks.doubao_browser_tts import process_doubao_browser_tts_job
from tasks.gemini_browser import process_report_pdf_gemini_job


logger = logging.getLogger("REPORT_AUDIO_WORKFLOW_TASK")

TERMINAL_STATUSES = {"completed", "failed", "canceled"}


@celery_app.task(bind=True, name="tasks.report_audio_workflow.process_job")
def process_report_audio_workflow_job(self, task_id):
    app = create_app()
    with app.app_context():
        return _process_job(task_id)


def _process_job(task_id):
    task = PublicationTask.query.get(task_id)
    if not task:
        logger.error("Report audio workflow task not found: %s", task_id)
        return False
    if not isinstance(task.params, dict) or task.params.get("feature") != FEATURE_NAME:
        logger.error("Invalid report audio workflow task params: %s", task_id)
        return False
    if task.status == "canceled":
        return False

    payload = task.params.get("report_audio_workflow") or {}
    pdf = payload.get("pdf") or {}
    pdf_path = (payload.get("pdf_path") or pdf.get("path") or "").strip()
    if not pdf_path:
        _mark_failed(task, "PDF 文件不能为空")
        return False

    prompt = (payload.get("prompt") or DEFAULT_INITIAL_PROMPT).strip()
    review_prompt = (payload.get("review_prompt") or DEFAULT_REVIEW_PROMPT).strip()
    followups = [str(item).strip() for item in (payload.get("followups") or []) if str(item).strip()]
    if review_prompt and review_prompt not in followups:
        followups.append(review_prompt)

    result = dict(task.result or {})
    result.update({
        "started_at": datetime.utcnow().isoformat() + "Z",
        "logs": result.get("logs") or [],
        "children": result.get("children") or {},
    })
    task.status = "processing"
    task.current_step = "start"
    task.progress = 2
    task.result = result
    flag_modified(task, "result")
    db.session.commit()

    try:
        _queue_notification(task.id, "report_audio_started", "研报 PDF 到语音编排已启动")
        _emit(task.id, "submit_gemini", 8, "创建研报 PDF Gem 解析子任务")
        gemini_task = _create_gemini_task(task, pdf, pdf_path, prompt, followups, payload)
        _record_child(task.id, "gemini", gemini_task)
        _queue_notification(task.id, "report_audio_gemini_submitted", "研报 PDF Gem 解析子任务已入队")

        gemini_task = _wait_child(task.id, gemini_task.id, "gemini", start_progress=10, end_progress=52)
        if gemini_task.status != "completed":
            raise RuntimeError(f"Gemini 子任务失败: {gemini_task.error_message or (gemini_task.result or {}).get('error')}")
        _update_result(task.id, {"gemini": _task_public_summary(gemini_task)})
        _queue_notification(task.id, "report_audio_gemini_completed", "研报 PDF Gem 第一轮解析和自审追问已完成")

        gemini_result = gemini_task.result or {}
        raw_final = (
            gemini_result.get("final_response_text")
            or gemini_result.get("response_text")
            or ""
        )
        initial_text = gemini_result.get("initial_response_text") or ""
        if not raw_final:
            raise RuntimeError("Gemini 子任务没有返回 final_response_text")

        _emit(task.id, "normalize_result", 58, "使用结构化解析器整理 Gemini 结果")
        structured = normalize_for_tts(
            raw_final,
            initial_text=initial_text,
            metadata={
                "workflow_task_id": task.id,
                "gemini_task_id": gemini_task.id,
                "pdf_filename": pdf.get("filename"),
            },
        )
        chunks = split_tts_text(structured.get("final_script_for_tts") or "")
        if not chunks:
            raise RuntimeError("结构化结果没有可朗读正文")

        _update_result(task.id, {
            "gemini": _task_public_summary(gemini_task),
            "structured_result": structured,
            "tts_text": {
                "chars": len(structured.get("final_script_for_tts") or ""),
                "chunks": len(chunks),
            },
        })
        _queue_notification(
            task.id,
            "report_audio_normalized",
            f"结构化整理完成，可朗读正文 {len(structured.get('final_script_for_tts') or '')} 字，拆分 {len(chunks)} 段",
        )

        tts_tasks = []
        wav_paths = []
        doubao_submitted_notified = False
        for index, chunk in enumerate(chunks, start=1):
            start = 60 + int((index - 1) / max(1, len(chunks)) * 28)
            end = 60 + int(index / max(1, len(chunks)) * 28)
            _emit(task.id, f"submit_doubao_browser_{index}", start, f"创建豆包浏览器朗读子任务 {index}/{len(chunks)}")
            tts_task = _create_doubao_tts_task(task, chunk, index, len(chunks), payload)
            tts_tasks.append(tts_task)
            _record_child(task.id, f"doubao_browser_{index}", tts_task)
            if not doubao_submitted_notified:
                doubao_submitted_notified = True
                _queue_notification(task.id, "report_audio_doubao_submitted", f"豆包浏览器朗读已开始，共 {len(chunks)} 段")
            tts_task = _wait_child(task.id, tts_task.id, f"doubao_browser_{index}", start_progress=start, end_progress=end)
            if tts_task.status != "completed":
                raise RuntimeError(f"豆包浏览器朗读子任务失败: {tts_task.error_message or (tts_task.result or {}).get('error')}")
            wav_path = ((tts_task.result or {}).get("audio") or {}).get("wav_path")
            if wav_path:
                wav_paths.append(wav_path)
        _update_result(task.id, {"doubao_browser_tts": [_task_public_summary(item) for item in tts_tasks]})
        _queue_notification(task.id, "report_audio_doubao_completed", f"豆包浏览器朗读完成，共生成 {len(wav_paths)} 段 WAV")

        _emit(task.id, "combine_audio", 92, "合并豆包浏览器朗读音频")
        audio = combine_wav_files(wav_paths, audio_path_for_job(task.id, task.created_at))
        output_path = storage_path_for_job(task.id, task.created_at)
        final_payload = {
            "workflow_task_id": task.id,
            "gemini_task_id": gemini_task.id,
            "doubao_browser_task_ids": [item.id for item in tts_tasks],
            "structured_result": structured,
            "audio": audio,
            "gemini": gemini_result,
            "completed_at": datetime.utcnow().isoformat() + "Z",
        }
        with open(output_path, "w", encoding="utf-8") as file:
            import json

            json.dump(final_payload, file, ensure_ascii=False, indent=2)

        final_result = dict(PublicationTask.query.get(task.id).result or {})
        final_result.update({
            "gemini": _task_public_summary(gemini_task),
            "doubao_browser_tts": [_task_public_summary(item) for item in tts_tasks],
            "structured_result": structured,
            "audio": audio,
            "output_path": output_path,
            "completed_at": datetime.utcnow().isoformat() + "Z",
        })
        fresh = PublicationTask.query.get(task.id)
        fresh.status = "completed"
        fresh.current_step = "done"
        fresh.progress = 100
        fresh.completed_at = datetime.utcnow()
        fresh.error_message = None
        fresh.result = final_result
        flag_modified(fresh, "result")
        db.session.commit()
        _queue_notification(task.id, "report_audio_completed", "研报转语音编排完成，最终 WAV 和 JSON 已生成")
        logger.info("Report audio workflow completed: %s", task_id)
        return True
    except Exception as exc:
        db.session.rollback()
        task = PublicationTask.query.get(task_id)
        if task:
            _mark_failed(task, str(exc))
            _queue_notification(task.id, "report_audio_failed", str(exc))
        logger.error("Report audio workflow failed: %s", exc, exc_info=True)
        return False


def _create_gemini_task(parent, pdf, pdf_path, prompt, followups, payload):
    child = PublicationTask(
        status="pending",
        current_step="queued",
        progress=0,
        params={
            "feature": gemini_service.FEATURE_NAME,
            "user_id": parent.params.get("user_id"),
            "parent_feature": FEATURE_NAME,
            "parent_task_id": parent.id,
            "report_pdf_gemini": {
                "prompt": prompt,
                "followups": followups,
                "url": (payload.get("gemini_url") or payload.get("url") or "").strip(),
                "metadata": {
                    "source": FEATURE_NAME,
                    "parent_task_id": parent.id,
                },
                "pdf": pdf,
                "pdf_path": pdf_path,
            },
        },
        result={
            "metadata": {
                "source": FEATURE_NAME,
                "parent_task_id": parent.id,
            },
            "response_text": None,
            "turns": [],
            "pdf": pdf,
            "logs": [],
        },
    )
    db.session.add(child)
    db.session.commit()
    async_result = process_report_pdf_gemini_job.apply_async(args=[child.id], queue=gemini_service.QUEUE_NAME, retry=False)
    child.celery_task_id = async_result.id
    db.session.commit()
    return child


def _create_doubao_tts_task(parent, text, index, total, payload):
    child = PublicationTask(
        status="pending",
        current_step="queued",
        progress=0,
        params={
            "feature": doubao_service.FEATURE_NAME,
            "user_id": parent.params.get("user_id"),
            "parent_feature": FEATURE_NAME,
            "parent_task_id": parent.id,
            "doubao_browser_tts": {
                "text": text,
                "metadata": {
                    "source": FEATURE_NAME,
                    "parent_task_id": parent.id,
                    "chunk_index": index,
                    "chunk_total": total,
                    **(payload.get("metadata") or {}),
                },
            },
        },
        result={
            "metadata": {
                "source": FEATURE_NAME,
                "parent_task_id": parent.id,
                "chunk_index": index,
                "chunk_total": total,
            },
            "audio": None,
            "logs": [],
        },
    )
    db.session.add(child)
    db.session.commit()
    async_result = process_doubao_browser_tts_job.apply_async(args=[child.id], queue=doubao_service.QUEUE_NAME, retry=False)
    child.celery_task_id = async_result.id
    db.session.commit()
    return child


def _wait_child(parent_id, child_id, label, *, start_progress, end_progress):
    timeout = _int_env("REPORT_AUDIO_CHILD_TIMEOUT_SECONDS", 1800)
    poll_seconds = max(1, _int_env("REPORT_AUDIO_CHILD_POLL_SECONDS", 3))
    deadline = time.monotonic() + timeout
    last_step = None
    while time.monotonic() < deadline:
        parent = PublicationTask.query.get(parent_id)
        if not parent or parent.status == "canceled":
            raise RuntimeError("父编排任务已取消")
        child = PublicationTask.query.get(child_id)
        if not child:
            raise RuntimeError(f"子任务不存在: {child_id}")
        result = child.result or {}
        if child.current_step != last_step:
            last_step = child.current_step
            child_progress = int(child.progress or 0)
            mapped = start_progress + int((end_progress - start_progress) * child_progress / 100)
            _emit(parent_id, f"{label}_{child.current_step}", mapped, f"{label}: {child.current_step}")
        if child.status in TERMINAL_STATUSES:
            return child
        time.sleep(poll_seconds)
        db.session.expire_all()
    raise RuntimeError(f"等待子任务超时: {label} {child_id}")


def _record_child(parent_id, label, child):
    fresh = PublicationTask.query.get(parent_id)
    result = dict(fresh.result or {})
    children = dict(result.get("children") or {})
    children[label] = _task_public_summary(child)
    result["children"] = children
    fresh.result = result
    flag_modified(fresh, "result")
    db.session.commit()


def _update_result(parent_id, values):
    fresh = PublicationTask.query.get(parent_id)
    result = dict(fresh.result or {})
    result.update(values)
    fresh.result = result
    flag_modified(fresh, "result")
    db.session.commit()


def _emit(parent_id, step, progress, message):
    fresh = PublicationTask.query.get(parent_id)
    if not fresh:
        return
    result = dict(fresh.result or {})
    logs = list(result.get("logs") or [])
    logs.append({
        "step": step,
        "message": message,
        "progress": progress,
        "updated_at": datetime.utcnow().isoformat() + "Z",
    })
    result["logs"] = logs[-120:]
    fresh.current_step = step
    fresh.progress = max(0, min(100, int(progress or fresh.progress or 0)))
    fresh.result = result
    flag_modified(fresh, "result")
    db.session.commit()


def _queue_notification(task_id, event_type, message=None):
    try:
        fresh = PublicationTask.query.get(task_id)
        if not fresh:
            return
        result = dict(fresh.result or {})
        notifications = dict(result.get("notifications") or {})
        if notifications.get(event_type, {}).get("queued"):
            return

        payload = _notification_payload(fresh, event_type, message)
        queued = workflow_notification_service.enqueue_workflow_event(event_type, payload)
        if not queued.get("queued"):
            return

        notifications[event_type] = {
            "queued": True,
            "event_type": event_type,
            "celery_task_id": queued.get("celery_task_id"),
            "queued_at": datetime.utcnow().isoformat() + "Z",
        }
        result["notifications"] = notifications
        fresh.result = result
        flag_modified(fresh, "result")
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception("[REPORT_AUDIO_WORKFLOW] notification queue failed | task=%s event=%s", task_id, event_type)


def _notification_payload(task, event_type, message=None):
    result = task.result or {}
    params = task.params or {}
    workflow = params.get("report_audio_workflow") or {}
    metadata = workflow.get("metadata") if isinstance(workflow.get("metadata"), dict) else {}
    pdf = workflow.get("pdf") or result.get("pdf") or {}
    audio = result.get("audio") or {}
    structured = result.get("structured_result") or {}
    tts_text = result.get("tts_text") or {}

    return {
        "id": task.id,
        "batch_id": task.id,
        "group_id": FEATURE_NAME,
        "group_name": "研报转语音编排",
        "group_label": "编排",
        "subject_prefix": "[研报转语音编排]",
        "trigger": metadata.get("source") or "manual",
        "status": _notification_status(task.status),
        "current_stage": task.current_step,
        "event_type": event_type,
        "event_message": message,
        "error_message": task.error_message or result.get("error"),
        "created_at": task.created_at.isoformat() + "Z" if task.created_at else None,
        "started_at": result.get("started_at"),
        "completed_at": task.completed_at.isoformat() + "Z" if task.completed_at else result.get("completed_at"),
        "stage_results": {
            "stages": _notification_stages(task, result, pdf, structured, tts_text, audio),
            "outputs": _notification_outputs(result, pdf, structured, tts_text, audio),
        },
    }


def _notification_stages(task, result, pdf, structured, tts_text, audio):
    children = result.get("children") if isinstance(result.get("children"), dict) else {}
    gemini = result.get("gemini") or children.get("gemini")
    doubao_items = result.get("doubao_browser_tts") if isinstance(result.get("doubao_browser_tts"), list) else []
    if not doubao_items:
        doubao_items = [value for key, value in children.items() if str(key).startswith("doubao_browser_")]

    return [
        {
            "name": "PDF 上传",
            "status": "completed" if pdf else "pending",
            "message": _pdf_message(pdf),
        },
        {
            "name": "研报 PDF Gem 解析/自审",
            "status": _child_stage_status(gemini),
            "message": _child_message(gemini),
        },
        {
            "name": "DeepSeek 结构化整理",
            "status": "completed" if structured else ("running" if task.current_step == "normalize_result" else "pending"),
            "message": _structured_message(structured, tts_text),
        },
        {
            "name": "豆包浏览器朗读捕获",
            "status": _children_stage_status(doubao_items),
            "message": _doubao_message(doubao_items, tts_text),
        },
        {
            "name": "WAV 合并",
            "status": "completed" if audio else ("running" if task.current_step == "combine_audio" else "pending"),
            "message": _audio_message(audio),
        },
    ]


def _notification_outputs(result, pdf, structured, tts_text, audio):
    outputs = {
        "PDF": _pdf_message(pdf),
        "标题": (structured or {}).get("title"),
        "可朗读正文": f"{tts_text.get('chars')} 字 / {tts_text.get('chunks')} 段" if tts_text else None,
        "音频": _audio_message(audio),
        "结果 JSON": result.get("output_path"),
    }
    return {key: value for key, value in outputs.items() if value}


def _child_stage_status(child):
    if not isinstance(child, dict) or not child:
        return "pending"
    status = str(child.get("status") or "").lower()
    if status == "completed":
        return "completed"
    if status in {"failed", "canceled"}:
        return status
    if status in {"pending", "queued"}:
        return "queued"
    return "running"


def _children_stage_status(children):
    if not children:
        return "pending"
    statuses = [_child_stage_status(child) for child in children if isinstance(child, dict)]
    if not statuses:
        return "pending"
    if any(status == "failed" for status in statuses):
        return "failed"
    if any(status == "canceled" for status in statuses):
        return "canceled"
    if all(status == "completed" for status in statuses):
        return "completed"
    if any(status in {"running", "queued"} for status in statuses):
        return "running"
    return statuses[-1]


def _notification_status(status):
    mapping = {
        "pending": "queued",
        "processing": "running",
        "completed": "completed",
        "failed": "failed",
        "canceled": "canceled",
    }
    return mapping.get(status or "", status or "-")


def _pdf_message(pdf):
    if not isinstance(pdf, dict) or not pdf:
        return None
    filename = pdf.get("filename") or "PDF"
    size = _format_bytes(pdf.get("bytes"))
    return f"{filename} / {size}" if size else filename


def _child_message(child):
    if not isinstance(child, dict) or not child:
        return None
    parts = []
    if child.get("job_id"):
        parts.append(f"任务 {child.get('job_id')}")
    if child.get("progress") is not None:
        parts.append(f"{child.get('progress')}%")
    if child.get("error"):
        parts.append(f"错误：{child.get('error')}")
    return " / ".join(parts) if parts else None


def _structured_message(structured, tts_text):
    if not isinstance(structured, dict) or not structured:
        return None
    provider = structured.get("provider") or "-"
    chars = tts_text.get("chars") if isinstance(tts_text, dict) else None
    chunks = tts_text.get("chunks") if isinstance(tts_text, dict) else None
    suffix = f" / {chars} 字 / {chunks} 段" if chars is not None and chunks is not None else ""
    return f"{provider}{suffix}"


def _doubao_message(children, tts_text):
    total = tts_text.get("chunks") if isinstance(tts_text, dict) else None
    if children:
        completed = sum(1 for child in children if _child_stage_status(child) == "completed")
        failed = sum(1 for child in children if _child_stage_status(child) == "failed")
        return f"子任务 {len(children)} 个，完成 {completed}，失败 {failed}"
    if total:
        return f"等待 {total} 段朗读"
    return None


def _audio_message(audio):
    if not isinstance(audio, dict) or not audio:
        return None
    seconds = audio.get("seconds")
    chunks = audio.get("chunks")
    size = _format_bytes(audio.get("bytes"))
    parts = []
    if seconds is not None:
        parts.append(f"{round(float(seconds), 1)} 秒")
    if chunks is not None:
        parts.append(f"{chunks} 段")
    if size:
        parts.append(size)
    return " / ".join(parts) if parts else None


def _format_bytes(value):
    try:
        bytes_value = int(value or 0)
    except (TypeError, ValueError):
        return None
    if bytes_value <= 0:
        return None
    if bytes_value < 1024:
        return f"{bytes_value} B"
    if bytes_value < 1024 * 1024:
        return f"{bytes_value / 1024:.1f} KB"
    return f"{bytes_value / 1024 / 1024:.1f} MB"


def _task_public_summary(task):
    if not task:
        return None
    return {
        "job_id": task.id,
        "celery_id": task.celery_task_id,
        "status": task.status,
        "step": task.current_step,
        "progress": task.progress or 0,
        "error": task.error_message,
        "created_at": task.created_at.isoformat() + "Z" if task.created_at else None,
        "completed_at": task.completed_at.isoformat() + "Z" if task.completed_at else None,
    }


def _mark_failed(task, message):
    result = dict(task.result or {})
    result["error"] = message
    result["completed_at"] = datetime.utcnow().isoformat() + "Z"
    task.status = "failed"
    task.current_step = "failed"
    task.error_message = message
    task.completed_at = datetime.utcnow()
    task.result = result
    flag_modified(task, "result")
    db.session.commit()


def _int_env(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default
