import logging
import os
import re
import time
from datetime import datetime
from types import SimpleNamespace

from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.orm import load_only
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
    pdf_first_page_image_path_for_job,
    render_pdf_first_page_image,
    split_tts_text,
    storage_path_for_job,
)
from services.publisher_queue_repair import maybe_requeue_stale_publisher_child
from services.runtime_guard import (
    guard_publication_task_start,
    is_retryable_db_error,
    release_db_session_quietly,
    retry_exception_types,
)
from services.workflow_notification_service import workflow_notification_service
from tasks.doubao_browser_tts import process_doubao_browser_tts_job
from tasks.gemini_browser import process_report_pdf_gemini_job
from tasks.publisher import run_publication_pipeline


logger = logging.getLogger("REPORT_AUDIO_WORKFLOW_TASK")

DEFAULT_DB_RETRIES = 5
DEFAULT_DB_RETRY_DELAY_SECONDS = 60
DB_RETRIES_ENV_NAME = "REPORT_AUDIO_WORKFLOW_DB_RETRIES"
DB_RETRY_DELAY_ENV_NAME = "REPORT_AUDIO_WORKFLOW_DB_RETRY_DELAY_SECONDS"
GEMINI_RETRY_LIMIT_ENV_NAME = "REPORT_AUDIO_GEMINI_RETRY_LIMIT"
DEFAULT_GEMINI_RETRY_LIMIT = 2
GENERIC_COVER_TITLE_NORMALIZED = {
    "pdf",
    "mp3",
    "音频",
    "视频",
    "外资",
    "外资研报",
    "研报",
    "调研",
    "调研纪要",
    "纪要",
    "研报播客",
    "研报视频",
    "研报视频编排",
}

RETRYABLE_CHILD_ERROR_FRAGMENTS = (
    "超时",
    "子任务仍在执行",
    "timeout",
    "timed out",
    "文件上传失败",
    "上传完成超时",
    "上传被取消",
    "提示词未成功提交",
    "未成功提交",
    "发送按钮",
    "等待 Gemini",
    "页面自动化超时",
    "Locator.click",
    "Target closed",
    "Browser closed",
    "Protocol error",
    "net::ERR",
)
NON_RETRYABLE_CHILD_ERROR_FRAGMENTS = (
    "父编排任务已取消",
    "子任务不存在",
    "任务不存在",
    "PDF 文件不能为空",
)


TASK_STATE_COLUMNS = (
    PublicationTask.id,
    PublicationTask.celery_task_id,
    PublicationTask.status,
    PublicationTask.current_step,
    PublicationTask.progress,
    PublicationTask.result,
    PublicationTask.error_message,
    PublicationTask.created_at,
    PublicationTask.updated_at,
    PublicationTask.completed_at,
)
TASK_PAYLOAD_COLUMNS = (
    *TASK_STATE_COLUMNS,
    PublicationTask.params,
    PublicationTask.user_id,
    PublicationTask.feature,
)
TASK_CHILD_COLUMNS = (
    *TASK_STATE_COLUMNS,
    PublicationTask.params,
    PublicationTask.feature,
    PublicationTask.parent_task_id,
    PublicationTask.source_task_id,
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

TERMINAL_STATUSES = {"completed", "failed", "blocked_by_captcha", "canceled"}
CELERY_FAILURE_STATES = {"FAILURE", "REVOKED"}
CURRENT_STEP_MAX_LENGTH = 20
WORKFLOW_QUEUE_NAME = "report_audio_workflow"
LEADING_COVER_PUNCTUATION = "，,、。.;；:：!！?？)]）】》> "
TRAILING_COVER_PUNCTUATION = "，,、。.;；:：!！?？([（【《< "
MAX_COVER_TITLE_LINE_CHARS = 18
MAX_COVER_SUBTITLE_CHARS = 34


class ChildTaskStillRunning(RuntimeError):
    def __init__(self, label, child_id, status="", step=""):
        self.label = label
        self.child_id = child_id
        self.status = status
        self.step = step
        super().__init__(f"子任务仍在执行: {label} {child_id} ({status or '-'} / {step or '-'})")

BANK_ALIASES = [
    ("Morgan Stanley", "大摩"),
    ("摩根士丹利", "大摩"),
    ("大摩", "大摩"),
    ("J.P. Morgan", "摩根大通"),
    ("JP Morgan", "摩根大通"),
    ("JPMorgan", "摩根大通"),
    ("摩根大通", "摩根大通"),
    ("小摩", "摩根大通"),
    ("Goldman Sachs", "高盛"),
    ("高盛", "高盛"),
    ("Bernstein", "伯恩斯坦"),
    ("伯恩斯坦", "伯恩斯坦"),
    ("UBS", "瑞银"),
    ("瑞银", "瑞银"),
    ("Citi", "花旗"),
    ("Citigroup", "花旗"),
    ("花旗", "花旗"),
    ("BofA", "美银"),
    ("Bank of America", "美银"),
    ("美国银行", "美银"),
    ("Nomura", "野村"),
    ("野村", "野村"),
]

STEP_CODE_ALIASES = {
    "submit_gemini": "gemini_submit",
    "gemini_prepare_browser": "gemini_prepare",
    "gemini_open_page": "gemini_open",
    "gemini_upload_pdf": "gemini_upload",
    "gemini_submit_prompt": "gemini_prompt",
    "gemini_wait_response": "gemini_wait",
    "gemini_initial_response": "gemini_initial",
    "gemini_save_result": "gemini_save",
    "render_pdf_first_page": "pdf_preview",
    "submit_publisher_video": "publisher_submit",
    "publisher_video_done": "publisher_done",
    "skip_publisher_video": "publisher_skip",
}

DOUBAO_STEP_ALIASES = {
    "queued": "queued",
    "prepare_browser": "prepare",
    "clear_audio_cache": "clear",
    "submit_text": "submit",
    "wait_response": "wait",
    "start_reading": "reading",
    "capture_audio": "capture",
    "download_wav": "download",
    "save_file": "save",
    "done": "done",
    "failed": "failed",
    "canceled": "canceled",
}

PUBLISHER_STEP_ALIASES = {
    "queued": "queued",
    "init": "init",
    "prepare": "prepare",
    "synthesis": "synthesis",
    "thumbnail": "thumbnail",
    "upload": "upload",
    "upload_youtube": "youtube",
    "done": "done",
    "failed": "failed",
    "canceled": "canceled",
}


@celery_app.task(bind=True, name="tasks.report_audio_workflow.process_job")
def process_report_audio_workflow_job(self, task_id):
    try:
        app = create_app()
        with app.app_context():
            guard_publication_task_start(
                self,
                task_id,
                feature=FEATURE_NAME,
                queue=WORKFLOW_QUEUE_NAME,
            )
            return _process_job(task_id)
    except retry_exception_types():
        raise
    except Exception as exc:
        return _handle_report_audio_task_failure(self, task_id, exc)


def _process_job(task_id):
    task = _load_task(task_id, *TASK_PAYLOAD_COLUMNS)
    if not task:
        logger.error("Report audio workflow task not found: %s", task_id)
        return False
    if not isinstance(task.params, dict) or task.params.get("feature") != FEATURE_NAME:
        logger.error("Invalid report audio workflow task params: %s", task_id)
        return False
    if task.status == "canceled":
        return False

    workflow_task_id = task.id
    workflow_created_at = task.created_at
    workflow_user_id = task.user_id or (task.params or {}).get("user_id")
    parent_ref = SimpleNamespace(
        id=workflow_task_id,
        created_at=workflow_created_at,
        params={"user_id": workflow_user_id},
    )

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
    resume_waiting_child = isinstance(result.get("waiting_child"), dict)
    started_at = datetime.utcnow().isoformat() + "Z"
    if not resume_waiting_child:
        result.pop("resumed_at", None)
    result.update({
        "started_at": (result.get("started_at") or started_at) if resume_waiting_child else started_at,
        "error": None,
        "logs": result.get("logs") or [],
        "children": result.get("children") or {},
    })
    if resume_waiting_child:
        result["resumed_at"] = started_at
    task.status = "processing"
    task.current_step = task.current_step if resume_waiting_child and task.current_step else "start"
    task.progress = max(int(task.progress or 0), 2) if resume_waiting_child else 2
    task.error_message = None
    task.completed_at = None
    task.result = result
    flag_modified(task, "result")
    db.session.commit()

    try:
        _queue_notification(workflow_task_id, "report_audio_started", "研报 PDF 到语音编排已启动")
        structured = _valid_structured_result(result)
        gemini_task = _completed_gemini_task(workflow_task_id, result)
        active_gemini_task = None if gemini_task else _active_gemini_task(workflow_task_id, result)
        if structured:
            _emit(workflow_task_id, "reuse_structured_result", 58, "复用已有 Gemini 结构化结果")
            gemini_result = gemini_task.result if gemini_task else {}
            if gemini_task:
                _record_child(workflow_task_id, "gemini", gemini_task)
                _update_result(workflow_task_id, {"gemini": _task_public_summary(gemini_task)})
        else:
            if gemini_task:
                _emit(workflow_task_id, "reuse_gemini_result", 52, "复用已完成的 Gemini 子任务")
                _record_child(workflow_task_id, "gemini", gemini_task)
                _update_result(workflow_task_id, {"gemini": _task_public_summary(gemini_task)})
            elif active_gemini_task:
                _emit(workflow_task_id, "gemini_wait_existing", 10, "继续等待已提交的研报 PDF Gem 解析子任务")
                _record_child(workflow_task_id, "gemini", active_gemini_task)
                gemini_task = _wait_child(workflow_task_id, active_gemini_task.id, "gemini", start_progress=10, end_progress=52)
                if gemini_task.status != "completed":
                    raise RuntimeError(f"Gemini 子任务失败: {_child_error_message(gemini_task)}")
                _update_result(workflow_task_id, {"gemini": _task_public_summary(gemini_task)})
                _queue_notification(workflow_task_id, "report_audio_gemini_completed", "研报 PDF Gem 第一轮解析和自审追问已完成")
            else:
                _queue_notification(workflow_task_id, "report_audio_gemini_submitted", "研报 PDF Gem 解析子任务已入队")
                gemini_task = _run_gemini_task_with_retries(
                    parent_ref,
                    pdf=pdf,
                    pdf_path=pdf_path,
                    prompt=prompt,
                    followups=followups,
                    payload=payload,
                    start_progress=10,
                    end_progress=52,
                )
                _update_result(workflow_task_id, {"gemini": _task_public_summary(gemini_task)})
                _queue_notification(workflow_task_id, "report_audio_gemini_completed", "研报 PDF Gem 第一轮解析和自审追问已完成")

            gemini_result = gemini_task.result or {}
            raw_final = (
                gemini_result.get("final_response_text")
                or gemini_result.get("response_text")
                or ""
            )
            initial_text = gemini_result.get("initial_response_text") or ""
            if not raw_final:
                raise RuntimeError("Gemini 子任务没有返回 final_response_text")
            final_chars = len(_plain_text(raw_final))
            min_final_chars = _min_gemini_final_chars()
            if final_chars < min_final_chars:
                raise RuntimeError(
                    f"Gemini 子任务返回内容过短: {final_chars} 字，低于 {min_final_chars} 字，疑似未抓到有效研报解析结果"
                )

            _emit(workflow_task_id, "normalize_result", 58, "使用结构化解析器整理 Gemini 结果")
            structured = normalize_for_tts(
                raw_final,
                initial_text=initial_text,
                metadata={
                    "workflow_task_id": workflow_task_id,
                    "gemini_task_id": gemini_task.id,
                    "pdf_filename": pdf.get("filename"),
                },
            )
        chunks = split_tts_text(structured.get("final_script_for_tts") or "")
        if not chunks:
            raise RuntimeError("结构化结果没有可朗读正文")
        tts_chars = len(_plain_text(structured.get("final_script_for_tts") or ""))
        min_final_chars = _min_gemini_final_chars()
        if tts_chars < min_final_chars:
            raise RuntimeError(
                f"结构化可朗读正文过短: {tts_chars} 字，低于 {min_final_chars} 字，停止进入豆包浏览器朗读"
            )

        _update_result(workflow_task_id, {
            "gemini": _task_public_summary(gemini_task),
            "structured_result": structured,
            "tts_text": {
                "chars": len(structured.get("final_script_for_tts") or ""),
                "chunks": len(chunks),
            },
        })
        _queue_notification(
            workflow_task_id,
            "report_audio_normalized",
            f"结构化整理完成，可朗读正文 {len(structured.get('final_script_for_tts') or '')} 字，拆分 {len(chunks)} 段",
        )

        tts_tasks = []
        wav_paths = []
        doubao_submitted_notified = False
        doubao_retry_limit = max(0, min(_int_env("REPORT_AUDIO_DOUBAO_RETRY_LIMIT", 2), 5))
        for index, chunk in enumerate(chunks, start=1):
            start = 60 + int((index - 1) / max(1, len(chunks)) * 28)
            end = 60 + int(index / max(1, len(chunks)) * 28)
            reusable_tts_task = _completed_tts_task_for_chunk(workflow_task_id, index, chunk)
            if reusable_tts_task:
                wav_path = _task_audio_wav_path(reusable_tts_task)
                tts_tasks.append(reusable_tts_task)
                wav_paths.append(wav_path)
                _record_child(workflow_task_id, f"doubao_browser_{index}", reusable_tts_task)
                _emit(
                    workflow_task_id,
                    f"doubao_browser_{index}_reuse",
                    end,
                    f"复用豆包浏览器朗读子任务 {index}/{len(chunks)}",
                )
                continue
            active_tts_task = _active_tts_task_for_chunk(workflow_task_id, index, chunk)
            if active_tts_task:
                tts_tasks.append(active_tts_task)
                _record_child(workflow_task_id, f"doubao_browser_{index}", active_tts_task)
                _emit(
                    workflow_task_id,
                    f"doubao_browser_{index}_wait_existing",
                    start,
                    f"继续等待已提交的豆包浏览器朗读子任务 {index}/{len(chunks)}",
                )
                tts_task = _wait_child(workflow_task_id, active_tts_task.id, f"doubao_browser_{index}", start_progress=start, end_progress=end)
                if tts_task.status == "completed":
                    wav_path = _task_audio_wav_path(tts_task)
                    if wav_path:
                        wav_paths.append(wav_path)
                        continue
                    raise RuntimeError("豆包浏览器朗读子任务完成但没有返回 WAV 路径")
                if _is_doubao_captcha_blocked(tts_task):
                    raise RuntimeError(f"豆包浏览器朗读被人机验证阻塞: {_child_error_message(tts_task)}")
                raise RuntimeError(f"豆包浏览器朗读子任务失败: {_child_error_message(tts_task)}")
            last_error = None
            for attempt in range(1, doubao_retry_limit + 2):
                retry_suffix = "" if attempt == 1 else f"（重试 {attempt - 1}/{doubao_retry_limit}）"
                _emit(
                    workflow_task_id,
                    f"submit_doubao_browser_{index}",
                    start,
                    f"创建豆包浏览器朗读子任务 {index}/{len(chunks)}{retry_suffix}",
                )
                tts_task = _create_doubao_tts_task(parent_ref, chunk, index, len(chunks), payload, attempt=attempt)
                tts_tasks.append(tts_task)
                _record_child(workflow_task_id, f"doubao_browser_{index}", tts_task)
                if not doubao_submitted_notified:
                    doubao_submitted_notified = True
                    _queue_notification(workflow_task_id, "report_audio_doubao_submitted", f"豆包浏览器朗读已开始，共 {len(chunks)} 段")
                tts_task = _wait_child(workflow_task_id, tts_task.id, f"doubao_browser_{index}", start_progress=start, end_progress=end)
                if tts_task.status == "completed":
                    wav_path = ((tts_task.result or {}).get("audio") or {}).get("wav_path")
                    if wav_path:
                        wav_paths.append(wav_path)
                        break
                    last_error = "豆包浏览器朗读子任务完成但没有返回 WAV 路径"
                else:
                    last_error = tts_task.error_message or (tts_task.result or {}).get("error") or "未知错误"
                    if _is_doubao_captcha_blocked(tts_task):
                        raise RuntimeError(f"豆包浏览器朗读被人机验证阻塞: {last_error}")
                if attempt <= doubao_retry_limit:
                    _emit(
                        workflow_task_id,
                        f"doubao_browser_{index}_retry",
                        end,
                        f"豆包浏览器朗读子任务 {index}/{len(chunks)} 失败，准备重试：{last_error}",
                    )
                    continue
                raise RuntimeError(f"豆包浏览器朗读子任务失败: {last_error}")
        _update_result(workflow_task_id, {"doubao_browser_tts": [_task_public_summary(item) for item in tts_tasks]})
        _queue_notification(workflow_task_id, "report_audio_doubao_completed", f"豆包浏览器朗读完成，共生成 {len(wav_paths)} 段 WAV")

        _emit(workflow_task_id, "combine_audio", 92, "合并豆包浏览器朗读音频")
        audio = combine_wav_files(wav_paths, audio_path_for_job(workflow_task_id, workflow_created_at))
        audio = doubao_service.validate_audio_duration_for_text(
            audio,
            structured.get("final_script_for_tts") or "",
        )
        _update_result(workflow_task_id, {"audio": audio})
        publisher_task = None
        video_result = None
        if _video_stage_enabled(payload):
            _emit(workflow_task_id, "render_pdf_first_page", 94, "生成 PDF 首屏截图")
            pdf_screenshot = render_pdf_first_page_image(
                pdf_path,
                pdf_first_page_image_path_for_job(workflow_task_id, workflow_created_at),
            )
            _update_result(workflow_task_id, {"pdf_first_page_image": pdf_screenshot})
            current_result = _fresh_result(workflow_task_id)
            publisher_task = _existing_child_from_result(current_result, "publisher_video")
            if publisher_task and publisher_task.status not in {"failed", "canceled"}:
                _emit(workflow_task_id, "publisher_video_wait_existing", 95, "继续等待已提交的视频合成子任务")
                _record_child(workflow_task_id, "publisher_video", publisher_task)
            else:
                _emit(workflow_task_id, "submit_publisher_video", 95, "提交视频合成子任务")
                publisher_task = _create_publisher_video_task(
                    parent_ref,
                    audio=audio,
                    structured=structured,
                    pdf=pdf,
                    pdf_screenshot=pdf_screenshot,
                    payload=payload,
                )
                _record_child(workflow_task_id, "publisher_video", publisher_task)
            publisher_task = _wait_child(workflow_task_id, publisher_task.id, "publisher_video", start_progress=95, end_progress=99)
            if publisher_task.status != "completed":
                raise RuntimeError(f"视频合成子任务失败: {publisher_task.error_message or (publisher_task.result or {}).get('error')}")
            video_result = _publisher_result_summary(publisher_task, pdf_screenshot)
            _update_result(workflow_task_id, {
                "publisher_video": _task_public_summary(publisher_task),
                "video": video_result,
            })
            _emit(workflow_task_id, "publisher_video_done", 99, "视频合成完成")
        else:
            video_result = {"enabled": False, "status": "skipped", "message": "未勾选生成视频"}
            _update_result(workflow_task_id, {"video": video_result})
            _emit(workflow_task_id, "skip_publisher_video", 94, "跳过视频合成")

        output_path = storage_path_for_job(workflow_task_id, workflow_created_at)
        final_payload = {
            "workflow_task_id": workflow_task_id,
            "gemini_task_id": gemini_task.id if gemini_task else None,
            "doubao_browser_task_ids": [item.id for item in tts_tasks],
            "publisher_task_id": publisher_task.id if publisher_task else None,
            "structured_result": structured,
            "audio": audio,
            "video": video_result,
            "gemini": gemini_result,
            "completed_at": datetime.utcnow().isoformat() + "Z",
        }
        with open(output_path, "w", encoding="utf-8") as file:
            import json

            json.dump(final_payload, file, ensure_ascii=False, indent=2)

        current = _load_task(workflow_task_id, *TASK_STATE_COLUMNS)
        final_result = dict((current.result if current else {}) or {})
        final_result.update({
            "gemini": _task_public_summary(gemini_task),
            "doubao_browser_tts": [_task_public_summary(item) for item in tts_tasks],
            "publisher_video": _task_public_summary(publisher_task) if publisher_task else None,
            "structured_result": structured,
            "audio": audio,
            "video": video_result,
            "output_path": output_path,
            "completed_at": datetime.utcnow().isoformat() + "Z",
        })
        fresh = _load_task(workflow_task_id, *TASK_STATE_COLUMNS)
        if not fresh:
            raise RuntimeError("编排任务不存在")
        fresh.status = "completed"
        fresh.current_step = "done"
        fresh.progress = 100
        fresh.completed_at = datetime.utcnow()
        fresh.error_message = None
        fresh.result = final_result
        flag_modified(fresh, "result")
        db.session.commit()
        _queue_notification(workflow_task_id, "report_audio_completed", "研报转语音编排完成，最终 WAV 和 JSON 已生成")
        logger.info("Report audio workflow completed: %s", task_id)
        return True
    except ChildTaskStillRunning as exc:
        db.session.rollback()
        _defer_parent_retry(task_id, exc)
        logger.info("Report audio workflow deferred: %s", exc)
        return True
    except Exception as exc:
        db.session.rollback()
        task = _load_task(task_id, *TASK_STATE_COLUMNS)
        notification_queued = False
        if task:
            _mark_failed(task, str(exc))
            notification_queued = _queue_notification(task_id, "report_audio_failed", str(exc))
        if not notification_queued:
            _send_direct_report_audio_failure_email(
                task_id=task_id,
                error=exc,
                persisted=bool(task),
                notification_queued=notification_queued,
            )
        logger.error("Report audio workflow failed: %s", exc, exc_info=True)
        return False


def _run_gemini_task_with_retries(parent, *, pdf, pdf_path, prompt, followups, payload, start_progress, end_progress):
    retry_limit = _retry_limit(GEMINI_RETRY_LIMIT_ENV_NAME, DEFAULT_GEMINI_RETRY_LIMIT)
    max_attempts = retry_limit + 1
    last_error = None
    last_task = None

    for attempt in range(1, max_attempts + 1):
        retry_suffix = "" if attempt == 1 else f"（自动重试 {attempt - 1}/{retry_limit}）"
        _emit(parent.id, "submit_gemini", 8, f"创建研报 PDF Gem 解析子任务{retry_suffix}")
        gemini_task = _create_gemini_task(
            parent,
            pdf,
            pdf_path,
            prompt,
            followups,
            payload,
            attempt=attempt,
            max_attempts=max_attempts,
        )
        last_task = gemini_task
        _record_child(parent.id, "gemini", gemini_task)
        _record_child_attempt(parent.id, "gemini", gemini_task, attempt, max_attempts, "running")

        try:
            gemini_task = _wait_child(
                parent.id,
                gemini_task.id,
                "gemini",
                start_progress=start_progress,
                end_progress=end_progress,
            )
        except ChildTaskStillRunning:
            _record_child(parent.id, "gemini", _load_task(gemini_task.id, *TASK_CHILD_COLUMNS) or gemini_task)
            raise
        except Exception as exc:
            last_error = str(exc)
            latest = _load_task(gemini_task.id, *TASK_CHILD_COLUMNS) or gemini_task
            if latest and latest.status not in TERMINAL_STATUSES:
                _revoke_child_task(latest)
                _mark_child_failed(latest, last_error)
                latest = _load_task(latest.id, *TASK_CHILD_COLUMNS) or latest
            last_task = latest
            _record_child_attempt(parent.id, "gemini", latest, attempt, max_attempts, "failed", error=last_error)
        else:
            last_task = gemini_task
            _record_child(parent.id, "gemini", gemini_task)
            if gemini_task.status == "completed":
                _record_child_attempt(parent.id, "gemini", gemini_task, attempt, max_attempts, "completed")
                return gemini_task
            last_error = _child_error_message(gemini_task)
            _record_child_attempt(parent.id, "gemini", gemini_task, attempt, max_attempts, "failed", error=last_error)

        if attempt < max_attempts and _is_retryable_child_error("gemini", last_error):
            _emit(
                parent.id,
                "gemini_retry",
                max(start_progress, 10),
                f"Gemini 子任务失败，准备自动重试 {attempt}/{retry_limit}: {_compact_error(last_error)}",
            )
            continue
        break

    if last_task:
        _record_child(parent.id, "gemini", last_task)
    raise RuntimeError(f"Gemini 子任务失败: {last_error or '未知错误'}")


def _create_gemini_task(parent, pdf, pdf_path, prompt, followups, payload, *, attempt=1, max_attempts=1):
    retry_index = max(0, int(attempt or 1) - 1)
    metadata = {
        "source": FEATURE_NAME,
        "parent_task_id": parent.id,
        "attempt": int(attempt or 1),
        "max_attempts": int(max_attempts or 1),
        "retry_index": retry_index,
    }
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
                "metadata": metadata,
                "pdf": pdf,
                "pdf_path": pdf_path,
            },
        },
        result={
            "metadata": metadata,
            "response_text": None,
            "turns": [],
            "pdf": pdf,
            "logs": [],
        },
    )
    db.session.add(child)
    db.session.flush()
    child_id = child.id
    db.session.commit()
    async_result = process_report_pdf_gemini_job.apply_async(args=[child_id], queue=gemini_service.QUEUE_NAME, retry=False)
    child = _load_task(child_id, *TASK_CHILD_COLUMNS)
    child.celery_task_id = async_result.id
    db.session.commit()
    return _load_task(child_id, *TASK_CHILD_COLUMNS)


def _valid_structured_result(result):
    structured = result.get("structured_result") if isinstance(result, dict) else None
    if not isinstance(structured, dict):
        return None
    script = str(structured.get("final_script_for_tts") or "").strip()
    if not script:
        return None
    if len(_plain_text(script)) < _min_gemini_final_chars():
        return None
    return structured


def _completed_gemini_task(parent_id, result):
    candidate_ids = []
    gemini = result.get("gemini") if isinstance(result, dict) else None
    children = result.get("children") if isinstance(result, dict) else None
    if isinstance(gemini, dict) and gemini.get("job_id"):
        candidate_ids.append(str(gemini["job_id"]))
    if isinstance(children, dict):
        child = children.get("gemini")
        if isinstance(child, dict) and child.get("job_id"):
            candidate_ids.append(str(child["job_id"]))
    for candidate_id in dict.fromkeys(candidate_ids):
        task = _load_task(candidate_id, *TASK_CHILD_COLUMNS)
        if task and task.status == "completed" and _valid_gemini_task_result(task.result):
            return task
    candidates = PublicationTask.query.filter(
        PublicationTask.feature == gemini_service.FEATURE_NAME,
        PublicationTask.parent_task_id == parent_id,
        PublicationTask.status == "completed",
    ).options(load_only(*TASK_CHILD_COLUMNS)).order_by(PublicationTask.created_at.desc()).limit(10).all()
    for task in candidates:
        if _valid_gemini_task_result(task.result):
            return task
    return None


def _active_gemini_task(parent_id, result):
    candidate_ids = []
    children = result.get("children") if isinstance(result, dict) and isinstance(result.get("children"), dict) else {}
    gemini = children.get("gemini") if isinstance(children.get("gemini"), dict) else {}
    if gemini.get("job_id"):
        candidate_ids.append(str(gemini["job_id"]))
    for candidate_id in dict.fromkeys(candidate_ids):
        task = _load_task(candidate_id, *TASK_CHILD_COLUMNS)
        if task and task.status not in TERMINAL_STATUSES and task.feature == gemini_service.FEATURE_NAME:
            return task
    candidates = PublicationTask.query.filter(
        PublicationTask.feature == gemini_service.FEATURE_NAME,
        PublicationTask.parent_task_id == parent_id,
        PublicationTask.status.notin_(list(TERMINAL_STATUSES)),
    ).options(load_only(*TASK_CHILD_COLUMNS)).order_by(PublicationTask.created_at.desc()).limit(10).all()
    return candidates[0] if candidates else None


def _valid_gemini_task_result(result):
    if not isinstance(result, dict):
        return False
    raw_text = result.get("final_response_text") or result.get("response_text") or ""
    return len(_plain_text(raw_text)) >= _min_gemini_final_chars()


def _fresh_result(parent_id):
    fresh = _load_task(parent_id, *TASK_STATE_COLUMNS)
    return dict(fresh.result or {}) if fresh and isinstance(fresh.result, dict) else {}


def _existing_child_from_result(result, label):
    child_id = _child_id_from_result(result, label)
    if not child_id:
        return None
    return _load_task(child_id, *TASK_CHILD_COLUMNS)


def _child_id_from_result(result, label):
    children = result.get("children") if isinstance(result.get("children"), dict) else {}
    child_summary = children.get(label) if isinstance(children.get(label), dict) else {}
    direct_summary = result.get(label) if isinstance(result.get(label), dict) else {}
    child_id = child_summary.get("job_id") or direct_summary.get("job_id")
    return str(child_id).strip() if child_id else None


def _completed_tts_task_for_chunk(parent_id, chunk_index, expected_text):
    candidates = PublicationTask.query.filter(
        PublicationTask.feature == doubao_service.FEATURE_NAME,
        PublicationTask.parent_task_id == parent_id,
        PublicationTask.status == "completed",
    ).options(load_only(*TASK_CHILD_COLUMNS)).order_by(PublicationTask.created_at.asc()).all()
    for candidate in candidates:
        if _task_chunk_index(candidate) != chunk_index:
            continue
        if _plain_text(_task_tts_text(candidate)) != _plain_text(expected_text):
            continue
        wav_path = _task_audio_wav_path(candidate)
        if wav_path and os.path.exists(wav_path):
            return candidate
    return None


def _active_tts_task_for_chunk(parent_id, chunk_index, expected_text):
    candidates = PublicationTask.query.filter(
        PublicationTask.feature == doubao_service.FEATURE_NAME,
        PublicationTask.parent_task_id == parent_id,
        PublicationTask.status.notin_(list(TERMINAL_STATUSES)),
    ).options(load_only(*TASK_CHILD_COLUMNS)).order_by(PublicationTask.created_at.desc()).limit(50).all()
    for candidate in candidates:
        if _task_chunk_index(candidate) != chunk_index:
            continue
        if _plain_text(_task_tts_text(candidate)) != _plain_text(expected_text):
            continue
        return candidate
    return None


def _task_chunk_index(task):
    result = task.result or {}
    metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
    if not metadata:
        params = task.params or {}
        doubao_payload = params.get("doubao_browser_tts") if isinstance(params.get("doubao_browser_tts"), dict) else {}
        metadata = doubao_payload.get("metadata") if isinstance(doubao_payload.get("metadata"), dict) else {}
    try:
        return int(metadata.get("chunk_index") or 0)
    except (TypeError, ValueError):
        return 0


def _task_audio_wav_path(task):
    result = task.result or {}
    audio = result.get("audio") if isinstance(result.get("audio"), dict) else {}
    return audio.get("wav_path") or result.get("wav_path")


def _task_tts_text(task):
    params = task.params or {}
    doubao_payload = params.get("doubao_browser_tts") if isinstance(params.get("doubao_browser_tts"), dict) else {}
    return str(doubao_payload.get("text") or "")


def _is_doubao_captcha_blocked(task):
    if not task:
        return False
    result = task.result if isinstance(task.result, dict) else {}
    return (
        task.status == "blocked_by_captcha"
        or result.get("error_type") == "blocked_by_captcha"
        or result.get("blocked_reason") == "captcha"
    )


def _create_doubao_tts_task(parent, text, index, total, payload, *, attempt=1):
    retry_index = max(0, int(attempt or 1) - 1)
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
                    "chunk_attempt": int(attempt or 1),
                    "retry_index": retry_index,
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
                "chunk_attempt": int(attempt or 1),
                "retry_index": retry_index,
            },
            "audio": None,
            "logs": [],
        },
    )
    db.session.add(child)
    db.session.flush()
    child_id = child.id
    db.session.commit()
    async_result = process_doubao_browser_tts_job.apply_async(args=[child_id], queue=doubao_service.QUEUE_NAME, retry=False)
    child = _load_task(child_id, *TASK_CHILD_COLUMNS)
    child.celery_task_id = async_result.id
    db.session.commit()
    return _load_task(child_id, *TASK_CHILD_COLUMNS)


def _create_publisher_video_task(parent, *, audio, structured, pdf, pdf_screenshot, payload):
    existing = _find_existing_publisher_video_task(parent.id, pdf_screenshot)
    if existing:
        return existing

    video_options = payload.get("video") if isinstance(payload.get("video"), dict) else {}
    main_title = _publisher_main_title(structured, video_options)
    sub_title = _publisher_sub_title(structured, video_options)
    date_text = (
        str(video_options.get("date_text") or "").strip()
        or _publisher_date_text(parent.created_at)
    )
    tag_text = str(video_options.get("tag_text") or "").strip() or "研报播客"
    audio_path = audio.get("wav_path") if isinstance(audio, dict) else None
    screenshot_path = pdf_screenshot.get("path") if isinstance(pdf_screenshot, dict) else None

    params = {
        "feature": "publisher",
        "source_feature": FEATURE_NAME,
        "source_task_id": parent.id,
        "audio_segments": [
            {
                "path": audio_path,
                "subtitle": _publisher_script_subtitles(structured, audio),
                "start": 0,
                "end_offset": 0,
                "speed": 1.0,
            }
        ],
        "video": {
            "enabled": True,
            "sub_burn_enabled": _publisher_sub_burn_enabled(video_options),
            "asr_model": str(video_options.get("asr_model") or "base"),
            "video_path": "",
            "audio_path": audio_path,
            "image_path": screenshot_path,
            "audio_start": 0,
            "audio_end_offset": 0,
            "transform": _publisher_video_transform(video_options),
            "cta": _publisher_cta(video_options),
        },
        "vertical_video": {
            "enabled": _truthy(video_options.get("vertical_enabled", False)),
            "asr_model": str(video_options.get("asr_model") or "base"),
            "image_path": screenshot_path,
            "subtitles": [],
        },
        "vertical_video_2": {"enabled": False, "image_path": ""},
        "vertical_thumbnail": {
            "enabled": _truthy(video_options.get("vertical_enabled", False)),
            "main_title": main_title,
            "sub_title": sub_title,
            "date_text": date_text,
            "screenshot_path": screenshot_path,
        },
        "thumbnail": {
            "main_title": main_title,
            "sub_title": sub_title,
            "tag_text": tag_text,
            "date_text": date_text,
            "bg_index": int(video_options.get("bg_index") or 0),
            "layout": "left",
            "mode": "screenshot",
            "custom_bg_path": screenshot_path,
            "overlay_opacity": float(video_options.get("overlay_opacity") or 0.5),
            "transform": video_options.get("cover_transform") or {"x": 0, "y": 0, "scale": 1},
        },
        "bilibili": {
            "enabled": _truthy(video_options.get("bilibili_enabled", False)),
            "title": _single_line(main_title),
            "desc": _publisher_description(structured, pdf),
            "tid": int(video_options.get("bilibili_tid") or 207),
            "tags": _publisher_tags(video_options),
            "copyright": 1,
            "no_reprint": 1,
            "neutral_mark": "个人观点，仅供参考",
            "charging_pay": _publisher_bilibili_charging_pay(video_options),
            "delay_time": _publisher_bilibili_delay_time(video_options),
            "season_id": _publisher_bilibili_season_id(video_options),
        },
        "youtube": {"enabled": False},
        "metadata": {
            "source": FEATURE_NAME,
            "parent_task_id": parent.id,
            "pdf": pdf,
        },
    }

    child = PublicationTask(
        status="pending",
        current_step="queued",
        progress=0,
        params=params,
        result={
            "metadata": params["metadata"],
            "source_task_id": parent.id,
            "pdf_first_page_image": pdf_screenshot,
            "video_path": None,
            "cover_path": None,
        },
    )
    db.session.add(child)
    db.session.flush()
    child_id = child.id
    db.session.commit()
    async_result = run_publication_pipeline.apply_async(args=[child_id], queue="render", retry=False)
    child = _load_task(child_id, *TASK_CHILD_COLUMNS)
    child.celery_task_id = async_result.id
    db.session.commit()
    return _load_task(child_id, *TASK_CHILD_COLUMNS)


def _find_existing_publisher_video_task(parent_id, pdf_screenshot):
    candidates = (
        PublicationTask.query
        .filter(PublicationTask.feature == "publisher")
        .filter(PublicationTask.source_task_id == parent_id)
        .options(load_only(*TASK_CHILD_COLUMNS))
        .order_by(PublicationTask.created_at.desc())
        .limit(30)
        .all()
    )
    if not candidates:
        return None

    for task in candidates:
        if task.status == "completed" and _publisher_video_output_ready(task, pdf_screenshot):
            _cancel_duplicate_publisher_tasks(candidates, keep_id=task.id)
            return task

    active = [task for task in candidates if task.status not in TERMINAL_STATUSES]
    if active:
        keep = active[0]
        _cancel_duplicate_publisher_tasks(active[1:], keep_id=keep.id)
        return keep
    return None


def _publisher_video_output_ready(task, pdf_screenshot):
    summary = _publisher_result_summary(task, pdf_screenshot)
    path = summary.get("video_path") or summary.get("landscape_video_path") or summary.get("vertical_video_path")
    return bool(path and os.path.exists(path))


def _cancel_duplicate_publisher_tasks(tasks, *, keep_id):
    changed = False
    for task in tasks:
        if not task or task.id == keep_id or task.status in TERMINAL_STATUSES:
            continue
        _revoke_child_task(task)
        result = dict(task.result or {})
        result["error"] = f"重复视频子任务已取消，保留 {keep_id}"
        task.status = "canceled"
        task.current_step = "canceled"
        task.error_message = result["error"]
        task.completed_at = datetime.utcnow()
        task.result = result
        flag_modified(task, "result")
        changed = True
    if changed:
        db.session.commit()


def _wait_child(parent_id, child_id, label, *, start_progress, end_progress):
    timeout = _int_env("REPORT_AUDIO_CHILD_TIMEOUT_SECONDS", 1800)
    poll_seconds = max(1, _int_env("REPORT_AUDIO_CHILD_POLL_SECONDS", 3))
    deadline = time.monotonic() + timeout
    last_step = None
    while time.monotonic() < deadline:
        parent = _load_task(parent_id, PublicationTask.status)
        if not parent or parent.status == "canceled":
            raise RuntimeError("父编排任务已取消")
        child = _load_task(child_id, *TASK_CHILD_COLUMNS)
        if not child:
            raise RuntimeError(f"子任务不存在: {child_id}")
        if child.current_step != last_step:
            last_step = child.current_step
            child_progress = int(child.progress or 0)
            mapped = start_progress + int((end_progress - start_progress) * child_progress / 100)
            _emit(parent_id, f"{label}_{child.current_step}", mapped, f"{label}: {child.current_step}")
            _record_child(parent_id, label, child)
        if child.status in TERMINAL_STATUSES:
            _clear_waiting_child(parent_id, label)
            return child
        celery_failure = _celery_child_failure_message(child)
        if celery_failure:
            _mark_child_failed(child, celery_failure)
            db.session.expire_all()
            _clear_waiting_child(parent_id, label)
            return _load_task(child_id, *TASK_CHILD_COLUMNS)
        if label == "publisher_video":
            child, requeued = maybe_requeue_stale_publisher_child(child, log=logger)
            if requeued:
                _emit(parent_id, "publisher_video_requeued", start_progress, "重新入队视频合成子任务")
                _record_child(parent_id, label, child)
                last_step = None
        release_db_session_quietly()
        time.sleep(poll_seconds)
    child = _load_task(child_id, *TASK_CHILD_COLUMNS)
    if child and child.status not in TERMINAL_STATUSES:
        _record_child(parent_id, label, child)
        raise ChildTaskStillRunning(label, child.id, child.status or "", child.current_step or "")
    raise RuntimeError(f"等待子任务超时: {label} {child_id}")


def _defer_parent_retry(parent_id, exc):
    delay = max(30, _int_env("REPORT_AUDIO_PARENT_RETRY_DELAY_SECONDS", 300))
    max_defers = max(1, _int_env("REPORT_AUDIO_PARENT_MAX_DEFERS", 144))
    fresh = _load_task(parent_id, *TASK_STATE_COLUMNS)
    if not fresh or fresh.status == "canceled":
        return

    result = dict(fresh.result or {})
    deferred_waits = dict(result.get("deferred_waits") or {})
    state = dict(deferred_waits.get(exc.label) or {})
    count = int(state.get("defer_count") or state.get("count") or 0) + 1
    if count > max_defers:
        raise RuntimeError(f"等待子任务超过最大续跑次数: {exc.label} {exc.child_id}")

    waiting_child = {
        "label": exc.label,
        "child_id": exc.child_id,
        "status": exc.status,
        "step": exc.step,
        "retry_delay_seconds": delay,
        "defer_count": count,
        "max_defers": max_defers,
        "updated_at": datetime.utcnow().isoformat() + "Z",
    }
    deferred_waits[exc.label] = waiting_child
    result["waiting_child"] = waiting_child
    result["deferred_waits"] = deferred_waits
    fresh.status = "processing"
    fresh.current_step = _current_step_code(f"{exc.label}_waiting")
    fresh.progress = max(int(fresh.progress or 0), 95 if exc.label == "publisher_video" else int(fresh.progress or 0))
    fresh.error_message = None
    fresh.result = result
    flag_modified(fresh, "result")
    db.session.commit()

    celery_app.send_task(
        "tasks.report_audio_workflow.process_job",
        args=[parent_id],
        queue=WORKFLOW_QUEUE_NAME,
        countdown=delay,
    )


def _clear_waiting_child(parent_id, label):
    fresh = _load_task(parent_id, *TASK_STATE_COLUMNS)
    if not fresh or not isinstance(fresh.result, dict):
        return
    result = dict(fresh.result or {})
    waiting_child = result.get("waiting_child")
    if not isinstance(waiting_child, dict) or waiting_child.get("label") != label:
        return
    result.pop("waiting_child", None)
    fresh.result = result
    flag_modified(fresh, "result")
    db.session.commit()


def _celery_child_failure_message(child):
    celery_task_id = getattr(child, "celery_task_id", None)
    if not celery_task_id:
        return None
    try:
        async_result = celery_app.AsyncResult(celery_task_id)
        state = async_result.state
    except Exception as exc:
        logger.warning("读取 Celery 子任务状态失败: child=%s error=%s", child.id, exc)
        return None
    if state not in CELERY_FAILURE_STATES:
        return None
    detail = ""
    try:
        raw = async_result.result
        detail = str(raw) if raw else ""
    except Exception as exc:
        detail = str(exc)
    if detail:
        return f"Celery 子任务异常退出: {state}: {detail}"
    return f"Celery 子任务异常退出: {state}"


def _mark_child_failed(child, message):
    result = dict(child.result or {})
    result["error"] = message
    result["completed_at"] = datetime.utcnow().isoformat() + "Z"
    child.status = "failed"
    child.current_step = "failed"
    child.error_message = message
    child.completed_at = datetime.utcnow()
    child.result = result
    flag_modified(child, "result")
    db.session.commit()


def _record_child(parent_id, label, child):
    fresh = _load_task(parent_id, *TASK_STATE_COLUMNS)
    if not fresh:
        return
    result = dict(fresh.result or {})
    children = dict(result.get("children") or {})
    children[label] = _task_public_summary(child)
    result["children"] = children
    fresh.result = result
    flag_modified(fresh, "result")
    db.session.commit()


def _record_child_attempt(parent_id, label, child, attempt, max_attempts, status, *, error=None):
    fresh = _load_task(parent_id, *TASK_STATE_COLUMNS)
    if not fresh:
        return
    result = dict(fresh.result or {})
    attempts = dict(result.get("child_attempts") or {})
    items = list(attempts.get(label) or [])
    summary = _task_public_summary(child) or {}
    updated_at = datetime.utcnow().isoformat() + "Z"
    summary.update({
        "attempt": int(attempt or 1),
        "max_attempts": int(max_attempts or 1),
        "attempt_status": status,
        "error": error or summary.get("error"),
        "updated_at": updated_at,
    })
    items = [item for item in items if int(item.get("attempt") or 0) != int(attempt or 1)]
    items.append(summary)
    attempts[label] = items[-int(max_attempts or 1):]
    result["child_attempts"] = attempts
    retry_state = dict(result.get("retry_state") or {})
    retry_state[label] = {
        "attempt": int(attempt or 1),
        "max_attempts": int(max_attempts or 1),
        "status": status,
        "error": error or summary.get("error"),
        "updated_at": updated_at,
    }
    result["retry_state"] = retry_state
    fresh.result = result
    flag_modified(fresh, "result")
    db.session.commit()


def _update_result(parent_id, values):
    fresh = _load_task(parent_id, *TASK_STATE_COLUMNS)
    if not fresh:
        return
    result = dict(fresh.result or {})
    result.update(values)
    fresh.result = result
    flag_modified(fresh, "result")
    db.session.commit()


def _child_error_message(child):
    if not child:
        return "子任务不存在"
    result = child.result if isinstance(child.result, dict) else {}
    return child.error_message or result.get("error") or f"子任务状态异常: {child.status or '-'} / {child.current_step or '-'}"


def _is_retryable_child_error(label, message):
    text = str(message or "")
    if not text:
        return False
    lower_text = text.lower()
    if any(fragment.lower() in lower_text for fragment in NON_RETRYABLE_CHILD_ERROR_FRAGMENTS):
        return False
    return any(fragment.lower() in lower_text for fragment in RETRYABLE_CHILD_ERROR_FRAGMENTS)


def _retry_limit(env_name, default):
    return max(0, min(_int_env(env_name, default), 5))


def _revoke_child_task(child):
    celery_task_id = getattr(child, "celery_task_id", None)
    if not celery_task_id:
        return
    try:
        celery_app.control.revoke(celery_task_id, terminate=False)
    except Exception as exc:
        logger.warning("撤销子任务失败: child=%s celery=%s error=%s", child.id, celery_task_id, exc)


def _compact_error(message, limit=180):
    text = re.sub(r"\s+", " ", str(message or "")).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _emit(parent_id, step, progress, message):
    fresh = _load_task(parent_id, *TASK_STATE_COLUMNS)
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
    fresh.current_step = _current_step_code(step)
    fresh.progress = max(0, min(100, int(progress or fresh.progress or 0)))
    fresh.result = result
    flag_modified(fresh, "result")
    db.session.commit()


def _current_step_code(step):
    raw = str(step or "running")
    alias = STEP_CODE_ALIASES.get(raw)
    if alias:
        return alias[:CURRENT_STEP_MAX_LENGTH]
    if len(raw) <= CURRENT_STEP_MAX_LENGTH:
        return raw

    if raw.startswith("submit_doubao_browser_"):
        index = raw.rsplit("_", 1)[-1]
        return f"doubao_{index}_submit"[:CURRENT_STEP_MAX_LENGTH]

    if raw.startswith("doubao_browser_"):
        parts = raw.split("_", 3)
        if len(parts) == 4:
            index = parts[2]
            child_step = parts[3]
            child_code = DOUBAO_STEP_ALIASES.get(child_step) or _safe_step_segment(child_step, 8)
            return f"doubao_{index}_{child_code}"[:CURRENT_STEP_MAX_LENGTH]

    if raw.startswith("publisher_video_"):
        child_step = raw[len("publisher_video_"):]
        child_code = PUBLISHER_STEP_ALIASES.get(child_step) or _safe_step_segment(child_step, 10)
        return f"pub_{child_code}"[:CURRENT_STEP_MAX_LENGTH]

    if raw.startswith("gemini_followup_"):
        parts = raw.split("_")
        if len(parts) >= 4:
            followup_index = parts[2]
            action = _safe_step_segment("_".join(parts[3:]), 8)
            return f"gem_fup{followup_index}_{action}"[:CURRENT_STEP_MAX_LENGTH]

    if raw.startswith("gemini_"):
        return f"gem_{_safe_step_segment(raw[len('gemini_'):], 16)}"[:CURRENT_STEP_MAX_LENGTH]

    return _safe_step_segment(raw, CURRENT_STEP_MAX_LENGTH)


def _safe_step_segment(value, max_length):
    text = str(value or "running")
    safe = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in text)
    safe = safe.strip("_") or "running"
    return safe[:max_length]


def _landscape_video_enabled(payload):
    video_options = payload.get("video") if isinstance(payload.get("video"), dict) else {}
    return _truthy(video_options.get("enabled", False))


def _vertical_video_enabled(payload):
    video_options = payload.get("video") if isinstance(payload.get("video"), dict) else {}
    return _truthy(video_options.get("vertical_enabled", False))


def _video_stage_enabled(payload):
    return _landscape_video_enabled(payload) or _vertical_video_enabled(payload)


def _is_generic_cover_title_override(value):
    text = _single_line(value).strip()
    if not text:
        return False
    compact = re.sub(r"[\s#＃:：|｜/_\\-]+", "", text).lower()
    if compact in GENERIC_COVER_TITLE_NORMALIZED:
        return True
    if text.startswith(("#", "＃")) and len(compact) <= 6 and not re.search(r"\d", compact):
        return True
    return False


def _publisher_main_title(structured, video_options):
    override = str(video_options.get("title") or video_options.get("main_title") or "").strip()
    if override and not _is_generic_cover_title_override(override):
        return _format_cover_title(override)
    cover_title = str((structured or {}).get("cover_title") or "").strip()
    if cover_title:
        return _format_cover_title(cover_title)
    title = str((structured or {}).get("title") or "").strip()
    if title:
        return _format_cover_title(title)
    titles = (structured or {}).get("titles") if isinstance((structured or {}).get("titles"), dict) else {}
    for key in ("short", "standard"):
        values = titles.get(key)
        if isinstance(values, list) and values:
            return _format_cover_title(str(values[0]))
    if override:
        return _format_cover_title(override)
    return "研报核心观点解读"


def _publisher_sub_title(structured, video_options):
    override = str(video_options.get("subtitle") or video_options.get("sub_title") or "").strip()
    if override:
        return _format_cover_subtitle(override)
    cover_subtitle = str((structured or {}).get("cover_subtitle") or "").strip()
    if cover_subtitle:
        return _format_cover_subtitle(cover_subtitle)
    summary = str((structured or {}).get("summary") or "").strip()
    if summary:
        return _format_cover_subtitle(summary)
    notes = (structured or {}).get("risk_notes")
    if isinstance(notes, list) and notes:
        return _format_cover_subtitle(str(notes[0]))
    return "AI 自审整理后的研报音频版"


def _publisher_date_text(created_at):
    created_at = created_at or datetime.utcnow()
    return created_at.strftime("%b %Y") + " | 研报播客"


def _publisher_description(structured, pdf):
    title = str((structured or {}).get("title") or "").strip()
    summary = str((structured or {}).get("summary") or "").strip()
    filename = (pdf or {}).get("filename") or ""
    parts = [part for part in [title, summary, f"来源 PDF：{filename}" if filename else ""] if part]
    return "\n\n".join(parts)[:1800] if parts else "由研报转语音编排自动生成。"


def _publisher_tags(video_options):
    raw = video_options.get("bilibili_tags") or video_options.get("tags")
    if isinstance(raw, list):
        values = [str(item).strip() for item in raw if str(item).strip()]
    elif isinstance(raw, str) and raw.strip():
        values = [item.strip() for item in raw.replace("，", ",").split(",") if item.strip()]
    else:
        values = ["财经", "研报", "投资"]
    return values[:10]


def _publisher_bilibili_charging_pay(video_options):
    enabled = _truthy(video_options.get("bilibili_charging_pay", False))
    preview_end = video_options.get("bilibili_charging_preview_end")
    try:
        preview_end = int(preview_end or 5)
    except (TypeError, ValueError):
        preview_end = 5

    return {
        "enabled": enabled,
        "upower_mode": 0,
        "preview": {
            "need_preview": 1,
            "start_time": 0,
            "end_time": preview_end,
        },
        "qa": {"qa_enabled": 0, "qa_id": None},
        "member_first": {"enabled": 0, "exp_time": 0},
    }


def _publisher_bilibili_delay_time(video_options):
    raw = (
        video_options.get("bilibili_delay_time")
        or video_options.get("bilibili_dtime")
        or video_options.get("delay_time")
    )
    if not raw:
        return None
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return None


def _publisher_bilibili_season_id(video_options):
    raw = video_options.get("bilibili_season_id") or video_options.get("season_id")
    if not raw:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _publisher_cta(video_options):
    if not _truthy(video_options.get("cta_enabled", True)):
        return None
    return {
        "text": str(video_options.get("cta_text") or "简介查看研报资料入口\n每日更新100+外资研报与机构调研"),
        "position": str(video_options.get("cta_position") or "bottom_right"),
        "style": str(video_options.get("cta_style") or "police_yellow"),
    }


def _publisher_video_transform(video_options):
    transform = video_options.get("video_transform") if isinstance(video_options, dict) else None
    if not isinstance(transform, dict):
        return None
    try:
        scale = float(transform.get("scale", 1) or 1)
        offset_x = float(transform.get("x", 0) or 0)
        offset_y = float(transform.get("y", 0) or 0)
    except (TypeError, ValueError):
        return transform
    if abs(scale - 1.0) < 0.0001 and abs(offset_x) < 0.0001 and abs(offset_y) < 0.0001:
        return None
    return transform


def _publisher_sub_burn_enabled(video_options):
    if not isinstance(video_options, dict):
        return True
    return _truthy(video_options.get("sub_burn_enabled"), True)


def _publisher_script_subtitles(structured, audio):
    script = str((structured or {}).get("final_script_for_tts") or "").strip()
    if not script:
        return ""
    try:
        duration = float((audio or {}).get("seconds") or 0)
    except (TypeError, ValueError):
        duration = 0
    if duration <= 0:
        return ""

    chunks = _split_subtitle_script(script)
    if not chunks:
        return ""
    weights = [max(1, len(re.sub(r"\s+", "", chunk))) for chunk in chunks]
    total_weight = sum(weights) or len(chunks)
    cursor = 0.0
    subtitles = []
    for index, (chunk, weight) in enumerate(zip(chunks, weights)):
        if index == len(chunks) - 1:
            end = duration
        else:
            end = min(duration, cursor + duration * weight / total_weight)
        if end <= cursor:
            end = min(duration, cursor + 0.8)
        subtitles.append({
            "start": round(cursor, 3),
            "end": round(end, 3),
            "text": chunk,
        })
        cursor = end
        if cursor >= duration:
            break
    return subtitles


def _split_subtitle_script(script, max_chars=18):
    text = _single_line(script)
    if not text:
        return []
    parts = [part for part in re.split(r"([。！？!?；;，,、])", text) if part]
    units = []
    index = 0
    while index < len(parts):
        unit = parts[index]
        if index + 1 < len(parts) and re.fullmatch(r"[。！？!?；;，,、]", parts[index + 1]):
            unit += parts[index + 1]
            index += 1
        units.append(unit)
        index += 1

    chunks = []
    current = ""
    for unit in units:
        unit = unit.strip()
        if not unit:
            continue
        while len(unit) > max_chars:
            prefix = unit[:max_chars]
            if current:
                chunks.append(current)
                current = ""
            chunks.append(prefix)
            unit = unit[max_chars:]
        if not current:
            current = unit
        elif len(current) + len(unit) <= max_chars:
            current += unit
        else:
            chunks.append(current)
            current = unit
    if current:
        chunks.append(current)
    return chunks


def _publisher_result_summary(publisher_task, pdf_screenshot):
    result = publisher_task.result or {}
    params = publisher_task.params or {}
    landscape_options = params.get("video") if isinstance(params.get("video"), dict) else {}
    vertical_options = params.get("vertical_video") if isinstance(params.get("vertical_video"), dict) else {}
    video_path = result.get("video_path") or result.get("vertical_video_path")
    return {
        "enabled": True,
        "status": publisher_task.status,
        "publisher_task_id": publisher_task.id,
        "celery_id": publisher_task.celery_task_id,
        "landscape_enabled": _truthy(landscape_options.get("enabled", False)),
        "vertical_enabled": _truthy(vertical_options.get("enabled", False)),
        "video_path": video_path,
        "landscape_video_path": result.get("video_path"),
        "vertical_video_path": result.get("vertical_video_path"),
        "cover_path": result.get("cover_path"),
        "pdf_first_page_image": pdf_screenshot,
        "bilibili_result": result.get("bilibili_result"),
        "youtube_result": result.get("youtube_result"),
    }


def _format_cover_title(value):
    raw_lines = [
        _clean_cover_line(line)
        for line in str(value or "").replace("\r", "\n").split("\n")
    ]
    raw_lines = [line for line in raw_lines if line]
    if len(raw_lines) >= 2:
        first = _clip_cover_line(raw_lines[0], MAX_COVER_TITLE_LINE_CHARS)
        second = _clip_cover_line(raw_lines[1], MAX_COVER_TITLE_LINE_CHARS)
        return "\n".join(line for line in [first, second] if line) or "研报核心观点解读"

    text = _clean_cover_line(_single_line(value))[:120]
    if not text:
        return "研报核心观点解读"

    bank, subject = _extract_bank_subject(text)
    if bank:
        first_budget = max(6, MAX_COVER_TITLE_LINE_CHARS - len(bank) - 1)
        first_subject, rest = _take_cover_piece(subject, first_budget)
        first = _clean_cover_line(f"{bank}:{first_subject}" if first_subject else bank)
        second = _clip_cover_line(rest, MAX_COVER_TITLE_LINE_CHARS)
        return "\n".join(line for line in [first, second] if line)

    first, rest = _take_cover_piece(text, MAX_COVER_TITLE_LINE_CHARS)
    second = _clip_cover_line(rest, MAX_COVER_TITLE_LINE_CHARS)
    return "\n".join(line for line in [first, second] if line) or "研报核心观点解读"


def _format_cover_subtitle(value):
    text = _clean_cover_line(_single_line(value))
    text = re.sub(r"^(本报告|本文|本期|本研报)(解读|认为|指出)?", "", text).strip()
    return _clip_cover_line(text, MAX_COVER_SUBTITLE_CHARS) or "AI 自审整理后的研报音频版"


def _extract_bank_subject(text):
    normalized = _clean_cover_line(text)
    for alias, label in BANK_ALIASES:
        pattern = re.compile(re.escape(alias), flags=re.I)
        match = pattern.search(normalized)
        if not match:
            continue
        subject = (normalized[:match.start()] + normalized[match.end():]).strip()
        subject = _clean_cover_line(subject)
        subject = re.sub(r"^(研报|报告|证券|研究|全球|亚洲|中国)?[：:，,、\\-\\s]+", "", subject)
        return label, subject

    for separator in ("：", ":"):
        if separator in normalized:
            left, right = normalized.split(separator, 1)
            left = _clean_cover_line(left)
            right = _clean_cover_line(right)
            if 1 <= len(left) <= 6 and right:
                return left, right
    return None, normalized


def _take_cover_piece(text, max_chars):
    text = _clean_cover_line(text)
    if len(text) <= max_chars:
        return text, ""
    split_at = -1
    for index, char in enumerate(text[:max_chars + 1]):
        if index >= 4 and char in "，,、；;|/ ":
            split_at = index
    if split_at > 0:
        return _clean_cover_line(text[:split_at]), _clean_cover_line(text[split_at + 1:])
    return _clean_cover_line(text[:max_chars]), _clean_cover_line(text[max_chars:])


def _clip_cover_line(text, max_chars):
    text = _clean_cover_line(text)
    if len(text) <= max_chars:
        return text
    piece, _rest = _take_cover_piece(text, max_chars)
    return piece or _clean_cover_line(text[:max_chars])


def _clean_cover_line(value):
    text = _single_line(value)
    text = re.sub(r"(行业)?(深度)?研报(解读|拆解)?$", "", text)
    text = re.sub(r"(报告|专题)(解读|拆解)$", "", text)
    text = re.sub(r"\s*[-—_]+\s*", "-", text)
    text = text.strip()
    text = text.lstrip(LEADING_COVER_PUNCTUATION)
    text = text.rstrip(TRAILING_COVER_PUNCTUATION)
    return text.strip()


def _single_line(value):
    return " ".join(str(value or "").replace("\n", " ").split())


def _truthy(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled", "y"}


def _queue_notification(task_id, event_type, message=None):
    try:
        fresh = _load_task(task_id, *TASK_PAYLOAD_COLUMNS)
        if not fresh:
            return False
        result = dict(fresh.result or {})
        notifications = dict(result.get("notifications") or {})
        if notifications.get(event_type, {}).get("queued"):
            return True

        payload = _notification_payload(fresh, event_type, message)
        queued = workflow_notification_service.enqueue_workflow_event(event_type, payload)
        if not queued.get("queued"):
            return False

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
        return True
    except Exception:
        db.session.rollback()
        logger.exception("[REPORT_AUDIO_WORKFLOW] notification queue failed | task=%s event=%s", task_id, event_type)
        return False


def _notification_payload(task, event_type, message=None):
    result = task.result or {}
    params = task.params or {}
    workflow = params.get("report_audio_workflow") or {}
    metadata = workflow.get("metadata") if isinstance(workflow.get("metadata"), dict) else {}
    pdf = workflow.get("pdf") or result.get("pdf") or {}
    audio = result.get("audio") or {}
    video = result.get("video") or {}
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
            "stages": _notification_stages(task, result, pdf, structured, tts_text, audio, video),
            "outputs": _notification_outputs(result, pdf, structured, tts_text, audio, video),
        },
    }


def _notification_stages(task, result, pdf, structured, tts_text, audio, video):
    children = result.get("children") if isinstance(result.get("children"), dict) else {}
    gemini = result.get("gemini") or children.get("gemini")
    doubao_items = result.get("doubao_browser_tts") if isinstance(result.get("doubao_browser_tts"), list) else []
    if not doubao_items:
        doubao_items = [value for key, value in children.items() if str(key).startswith("doubao_browser_")]
    publisher = result.get("publisher_video") or children.get("publisher_video")

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
        {
            "name": "横屏视频合成",
            "status": _video_stage_status(video, publisher),
            "message": _video_message(video, publisher),
        },
    ]


def _notification_outputs(result, pdf, structured, tts_text, audio, video):
    outputs = {
        "PDF": _pdf_message(pdf),
        "标题": (structured or {}).get("title"),
        "可朗读正文": f"{tts_text.get('chars')} 字 / {tts_text.get('chunks')} 段" if tts_text else None,
        "音频": _audio_message(audio),
        "视频": _video_output_message(video),
        "封面": (video or {}).get("cover_path") if isinstance(video, dict) else None,
        "PDF 首屏图": ((video or {}).get("pdf_first_page_image") or {}).get("path") if isinstance(video, dict) else None,
        "结果 JSON": result.get("output_path"),
    }
    return {key: value for key, value in outputs.items() if value}


def _child_stage_status(child):
    if not isinstance(child, dict) or not child:
        return "pending"
    status = str(child.get("status") or "").lower()
    if status in {"blocked", "blocked_by_captcha"}:
        return "blocked"
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
    if any(status == "blocked" for status in statuses):
        return "blocked"
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
        "blocked_by_captcha": "blocked",
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
        blocked = sum(1 for child in children if _child_stage_status(child) == "blocked")
        failed = sum(1 for child in children if _child_stage_status(child) == "failed")
        parts = [f"子任务 {len(children)} 个", f"完成 {completed}"]
        if blocked:
            parts.append(f"阻塞 {blocked}")
        if failed:
            parts.append(f"失败 {failed}")
        return "，".join(parts)
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


def _video_stage_status(video, publisher):
    if isinstance(video, dict) and video.get("enabled") is False:
        return "skipped"
    if isinstance(video, dict) and video.get("video_path"):
        return "completed"
    return _child_stage_status(publisher)


def _video_message(video, publisher):
    if isinstance(video, dict) and video.get("enabled") is False:
        return video.get("message") or "未勾选生成横屏视频"
    if isinstance(video, dict) and video.get("video_path"):
        return f"任务 {video.get('publisher_task_id')} / MP4 已生成"
    return _child_message(publisher)


def _video_output_message(video):
    if not isinstance(video, dict) or not video:
        return None
    if video.get("enabled") is False:
        return video.get("message")
    path = video.get("video_path")
    if not path:
        return None
    try:
        size = _format_bytes(os.path.getsize(path))
    except OSError:
        size = None
    return f"{path} / {size}" if size else path


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
    result = task.result if isinstance(task.result, dict) else {}
    return {
        "job_id": task.id,
        "celery_id": task.celery_task_id,
        "status": task.status,
        "step": task.current_step,
        "progress": task.progress or 0,
        "error": task.error_message,
        "error_type": result.get("error_type"),
        "blocked_reason": result.get("blocked_reason"),
        "captcha_blocked": result.get("captcha_blocked"),
        "metadata": result.get("metadata") or {},
        "audio": result.get("audio"),
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


def _handle_report_audio_task_failure(celery_task, task_id, exc):
    if _is_retryable_db_error(exc) and celery_task.request.retries < _db_retry_count():
        countdown = _db_retry_delay_seconds(celery_task.request.retries)
        logger.warning(
            "[REPORT_AUDIO_WORKFLOW] retry transient db error | task=%s retry=%s/%s countdown=%ss error=%s",
            task_id,
            celery_task.request.retries + 1,
            _db_retry_count(),
            countdown,
            exc,
        )
        raise celery_task.retry(exc=exc, countdown=countdown, max_retries=_db_retry_count())

    details = _mark_report_audio_failed_safely(task_id, str(exc))
    if not details.get("notification_queued"):
        _send_direct_report_audio_failure_email(
            task_id=task_id,
            error=exc,
            persisted=details.get("persisted"),
            notification_queued=details.get("notification_queued"),
            persist_error=details.get("persist_error"),
        )
    logger.error(
        "[REPORT_AUDIO_WORKFLOW] task failed outside normal handler | task=%s persisted=%s notification_queued=%s error=%s",
        task_id,
        details.get("persisted"),
        details.get("notification_queued"),
        exc,
        exc_info=bool(getattr(exc, "__traceback__", None)),
    )
    return False


def _mark_report_audio_failed_safely(task_id, message):
    details = {
        "persisted": False,
        "notification_queued": False,
        "persist_error": None,
    }
    try:
        app = create_app()
        with app.app_context():
            db.session.rollback()
            task = _load_task(task_id, *TASK_STATE_COLUMNS)
            if not task:
                details["persist_error"] = f"任务不存在: {task_id}"
                return details
            _mark_failed(task, message)
            details["persisted"] = True
            details["notification_queued"] = _queue_notification(task_id, "report_audio_failed", message)
    except Exception as persist_exc:
        details["persist_error"] = str(persist_exc)
        try:
            db.session.rollback()
        except Exception:
            pass
        logger.exception("[REPORT_AUDIO_WORKFLOW] failed to persist failure state | task=%s", task_id)
    return details


def _send_direct_report_audio_failure_email(
    *,
    task_id,
    error,
    persisted=False,
    notification_queued=False,
    persist_error=None,
):
    try:
        subject = f"[研报转语音] 编排任务异常 · {str(task_id)[:8]}"
        lines = [
            "研报转语音编排任务异常退出，已触发兜底通知。",
            "",
            f"任务 ID：{task_id}",
            f"失败已写入数据库：{'是' if persisted else '否'}",
            f"失败通知已入队：{'是' if notification_queued else '否'}",
            f"异常：{error}",
        ]
        if persist_error:
            lines.append(f"落库/通知异常：{persist_error}")
        lines.extend([
            "",
            f"发送时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        ])
        workflow_notification_service.send_email_message(subject, "\n".join(lines))
    except Exception:
        logger.exception("[REPORT_AUDIO_WORKFLOW] direct failure email failed | task=%s", task_id)


def _is_retryable_db_error(exc):
    return is_retryable_db_error(exc)


def _db_retry_count():
    return _bounded_int(os.environ.get(DB_RETRIES_ENV_NAME), default=DEFAULT_DB_RETRIES, lower=0, upper=20)


def _db_retry_delay_seconds(retry_index):
    base = _bounded_int(
        os.environ.get(DB_RETRY_DELAY_ENV_NAME),
        default=DEFAULT_DB_RETRY_DELAY_SECONDS,
        lower=1,
        upper=600,
    )
    return min(base * max(int(retry_index or 0) + 1, 1), 600)


def _plain_text(value):
    return re.sub(r"\s+", "", str(value or ""))


def _min_gemini_final_chars():
    return max(0, _int_env(
        "REPORT_AUDIO_GEMINI_MIN_FINAL_CHARS",
        _int_env("REPORT_PDF_GEMINI_MIN_RESPONSE_CHARS", 120),
    ))


def _bounded_int(value, *, default, lower, upper):
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(lower, min(upper, number))


def _int_env(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default
