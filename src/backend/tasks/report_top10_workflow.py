import logging
import os
import re
import time
from datetime import datetime
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from sqlalchemy import or_
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import load_only
from sqlalchemy.orm.attributes import flag_modified

from app import create_app
from celery_config import celery_app
from models import db
from models.task import PublicationTask
from services import gemini_browser as gemini_service
from services.report_audio_workflow import (
    DEFAULT_INITIAL_PROMPT,
    DEFAULT_REVIEW_PROMPT,
    normalize_for_tts,
)
from services.report_top10_workflow import (
    FEATURE_NAME,
    QUEUE_NAME,
    ReportTop10WorkflowError,
    assemble_final_script_for_tts,
    build_editor_input,
    cache_single_report_script,
    cached_single_report_for_pdf,
    default_editor_gemini_url,
    extract_editor_json,
    selected_report_count,
    valid_single_report_script,
    write_result_file,
)
from services.runtime_guard import (
    apply_async_after_db_release,
    guard_publication_task_start,
    revoke_celery_task_after_db_commit,
    retry_exception_types,
)
from tasks.gemini_browser import process_report_pdf_gemini_job


logger = logging.getLogger("REPORT_TOP10_WORKFLOW_TASK")

SINGLE_REPORT_FEATURE = "report_pdf_audio_workflow"
SCRIPT_MATERIAL_FEATURE = "report_script_material_workflow"
SINGLE_REPORT_FEATURES = (SCRIPT_MATERIAL_FEATURE, SINGLE_REPORT_FEATURE, "report_audio_workflow")
TERMINAL_STATUSES = {"completed", "failed", "canceled"}
GEMINI_RETRY_LIMIT_ENV_NAME = "REPORT_TOP10_GEMINI_RETRY_LIMIT"
DEFAULT_GEMINI_RETRY_LIMIT = 2

RETRYABLE_CHILD_ERROR_FRAGMENTS = (
    "超时",
    "子任务仍在执行",
    "timeout",
    "timed out",
    "filechooser",
    "文件上传失败",
    "上传完成超时",
    "上传被取消",
    "未找到 Gemini 文件上传入口",
    "未找到 Gemini 文件上传菜单项",
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
    PublicationTask.id,
    PublicationTask.celery_task_id,
    PublicationTask.status,
    PublicationTask.current_step,
    PublicationTask.progress,
    PublicationTask.params,
    PublicationTask.result,
    PublicationTask.error_message,
    PublicationTask.feature,
    PublicationTask.parent_task_id,
    PublicationTask.source_task_id,
    PublicationTask.created_at,
    PublicationTask.completed_at,
)
TASK_CHILD_STATUS_COLUMNS = (
    PublicationTask.id,
    PublicationTask.celery_task_id,
    PublicationTask.status,
    PublicationTask.current_step,
    PublicationTask.progress,
    PublicationTask.error_message,
    PublicationTask.completed_at,
)


@celery_app.task(
    bind=True,
    name="tasks.report_top10_workflow.process_job",
    store_errors_even_if_ignored=True,
    autoretry_for=(OperationalError,),
    retry_backoff=True,
    retry_jitter=True,
    retry_kwargs={"max_retries": 3},
)
def process_report_top10_workflow_job(self, task_id):
    app = create_app()
    with app.app_context():
        try:
            guard_publication_task_start(
                self,
                task_id,
                feature=FEATURE_NAME,
                queue=QUEUE_NAME,
            )
            return _process_job(task_id)
        except retry_exception_types():
            raise
        except Exception as exc:
            logger.error("Report Top10 workflow failed: %s", exc, exc_info=True)
            _reset_session_for_failure_marker()
            _mark_failed_safely(task_id, str(exc))
            return False
        finally:
            db.session.remove()


def _process_job(task_id: str) -> bool:
    task = _load_task(task_id, *TASK_PAYLOAD_COLUMNS)
    if not task:
        logger.error("Report Top10 workflow task not found: %s", task_id)
        return False
    if not isinstance(task.params, dict) or task.params.get("feature") != FEATURE_NAME:
        logger.error("Invalid Report Top10 workflow params: %s", task_id)
        return False
    if task.status == "canceled":
        return False

    payload = task.params.get("report_top10_workflow") or {}
    pdfs = payload.get("pdfs") if isinstance(payload.get("pdfs"), list) else []
    report_count = selected_report_count(payload.get("selected_count") or len(pdfs))
    if len(pdfs) != report_count:
        _mark_failed(task, f"需要选择 {report_count} 份 PDF，当前 {len(pdfs)} 份")
        return False

    result = dict(task.result or {})
    started_at = datetime.utcnow().isoformat() + "Z"
    result.update({
        "started_at": result.get("started_at") or started_at,
        "error": None,
        "logs": result.get("logs") or [],
        "children": result.get("children") or {},
        "source_reports": result.get("source_reports") or [],
    })
    task.status = "processing"
    task.current_step = "prepare_single_reports"
    task.progress = max(int(task.progress or 0), 2)
    task.error_message = None
    task.completed_at = None
    task.result = result
    flag_modified(task, "result")
    db.session.commit()

    parent_ref = SimpleNamespace(
        id=task.id,
        created_at=task.created_at,
        params={"user_id": task.user_id or task.params.get("user_id")},
    )

    _emit(task.id, "prepare_single_reports", 3, f"开始准备 {report_count} 篇单篇研报文案")
    source_reports = _existing_source_reports(task.id)
    for index, pdf in enumerate(pdfs, start=1):
        _raise_if_canceled(task.id)
        existing = _source_report_for_pdf(source_reports, pdf)
        if existing:
            _emit(task.id, f"single_report_{index:02d}_reused", _single_progress(index, report_count), f"复用第 {index} 篇单篇文案")
            continue

        reusable = None
        if _truthy(payload.get("reuse_existing_single_reports"), True):
            reusable = _reusable_single_report_for_pdf(pdf)
        if reusable:
            source_reports.append(reusable)
            _update_result(task.id, {"source_reports": source_reports})
            _emit(task.id, f"single_report_{index:02d}_reused_history", _single_progress(index, report_count), f"复用历史单篇解析：{_pdf_title(pdf)}")
            continue

        report = _run_single_report_gemini(
            parent_ref,
            pdf=pdf,
            index=index,
            payload=payload,
            start_progress=_single_progress(index - 1, report_count),
            end_progress=_single_progress(index, report_count),
        )
        source_reports.append(report)
        _update_result(task.id, {"source_reports": source_reports})
        _emit(task.id, f"single_report_{index:02d}_completed", _single_progress(index, report_count), f"第 {index} 篇单篇文案已完成")

    source_reports = _order_source_reports(source_reports, pdfs)
    editor_input = build_editor_input(source_reports, payload)
    _update_result(task.id, {
        "source_reports": source_reports,
        "editor_input_chars": len(editor_input),
        "editor_input": editor_input,
    })
    _emit(task.id, "editor_prompt_ready", 72, f"总编辑输入素材已组装，约 {len(editor_input)} 字")

    editor_task = _run_editor_gemini_with_retries(parent_ref, editor_input, payload)

    if editor_task.status != "completed":
        raise RuntimeError(f"总编辑 Gem 子任务失败: {_child_error_message(editor_task)}")

    editor_result_raw = editor_task.result or {}
    editor_response_text = (
        editor_result_raw.get("final_response_text")
        or editor_result_raw.get("response_text")
        or ""
    )
    editor_json = extract_editor_json(editor_response_text)
    final_script_for_tts = assemble_final_script_for_tts(editor_json, source_reports)
    if final_script_for_tts:
        raw_final_script = str(editor_json.get("final_script_for_tts") or "").strip()
        if raw_final_script and raw_final_script != final_script_for_tts:
            editor_json["final_script_for_tts_raw"] = raw_final_script
        editor_json["final_script_for_tts"] = final_script_for_tts
        editor_json["final_script_assembled_by_backend"] = True
    output_path = write_result_file(
        job_id=task.id,
        created_at=task.created_at,
        editor_input=editor_input,
        editor_response_text=editor_response_text,
        editor_result=editor_json,
        source_reports=source_reports,
    )

    final = _load_task(task.id, *TASK_STATE_COLUMNS)
    final_result = dict(final.result or {})
    final_result.update({
        "source_reports": source_reports,
        "editor_gemini": _task_public_summary(editor_task),
        "editor_response_text": editor_response_text,
        "editor_result": editor_json,
        "final_script_for_tts": final_script_for_tts,
        "video_segments": editor_json.get("video_segments"),
        "output_path": output_path,
        "completed_at": datetime.utcnow().isoformat() + "Z",
    })
    final.status = "completed"
    final.current_step = "done"
    final.progress = 100
    final.completed_at = datetime.utcnow()
    final.error_message = None
    final.result = final_result
    flag_modified(final, "result")
    db.session.commit()
    logger.info("Report Top10 workflow completed: %s", task.id)
    return True


def _run_single_report_gemini(parent, *, pdf: Dict[str, Any], index: int, payload: Dict[str, Any], start_progress: int, end_progress: int) -> Dict[str, Any]:
    label = f"single_gemini_{index:02d}"
    retry_limit = _retry_limit(GEMINI_RETRY_LIMIT_ENV_NAME, DEFAULT_GEMINI_RETRY_LIMIT)
    max_attempts = retry_limit + 1
    last_error = None
    last_child = None

    child = _existing_single_gemini_child(parent.id, pdf)
    if child and child.status == "completed":
        _record_child(parent.id, label, child)
        _emit(parent.id, f"single_report_{index:02d}_gemini_reused", start_progress, f"复用第 {index} 篇 Gemini 子任务")
        return _single_report_from_gemini_child(parent, pdf=pdf, index=index, child=child)

    next_attempt = 1
    if child and child.status not in TERMINAL_STATUSES:
        active_attempt = _child_attempt(child, default=1, max_attempts=max_attempts)
        _record_child(parent.id, label, child)
        _record_child_attempt(parent.id, label, child, active_attempt, max_attempts, "running")
        _emit(parent.id, f"single_report_{index:02d}_wait_existing", start_progress, f"等待第 {index} 篇 Gemini 子任务")
        try:
            child = _wait_child(parent.id, child.id, label, start_progress=start_progress, end_progress=end_progress)
        except Exception as exc:
            last_error = str(exc)
            latest = _load_task(child.id, *TASK_CHILD_COLUMNS) or child
            if latest and latest.status not in TERMINAL_STATUSES:
                _revoke_child_task(latest)
                _mark_child_failed(latest, last_error)
                latest = _load_task(latest.id, *TASK_CHILD_COLUMNS) or latest
            child = latest
        if child and child.status == "completed":
            _record_child_attempt(parent.id, label, child, active_attempt, max_attempts, "completed")
            return _single_report_from_gemini_child(parent, pdf=pdf, index=index, child=child)
        last_child = child
        last_error = last_error or _child_error_message(child)
        _record_child_attempt(parent.id, label, child, active_attempt, max_attempts, "failed", error=last_error)
        if active_attempt < max_attempts and _is_retryable_child_error(label, last_error):
            _emit(
                parent.id,
                f"single_report_{index:02d}_gemini_retry",
                max(start_progress, 5),
                f"第 {index} 篇 Gemini 子任务失败，准备自动重试 {active_attempt}/{retry_limit}: {_compact_error(last_error)}",
            )
            next_attempt = active_attempt + 1
        else:
            if last_child:
                _record_child(parent.id, label, last_child)
            raise RuntimeError(f"第 {index} 篇单篇 Gemini 子任务失败: {last_error or '未知错误'}")

    for attempt in range(next_attempt, max_attempts + 1):
        retry_suffix = "" if attempt == 1 else f"（自动重试 {attempt - 1}/{retry_limit}）"
        child = _create_single_gemini_task(parent, pdf, index, payload, attempt=attempt, max_attempts=max_attempts)
        _record_child(parent.id, label, child)
        _record_child_attempt(parent.id, label, child, attempt, max_attempts, "running")
        _emit(parent.id, f"single_report_{index:02d}_submitted", start_progress, f"提交第 {index} 篇 PDF 到单篇 Gem{retry_suffix}")
        try:
            child = _wait_child(parent.id, child.id, label, start_progress=start_progress, end_progress=end_progress)
        except Exception as exc:
            last_error = str(exc)
            latest = _load_task(child.id, *TASK_CHILD_COLUMNS) or child
            if latest and latest.status not in TERMINAL_STATUSES:
                _revoke_child_task(latest)
                _mark_child_failed(latest, last_error)
                latest = _load_task(latest.id, *TASK_CHILD_COLUMNS) or latest
            child = latest
            last_child = child
            _record_child_attempt(parent.id, label, child, attempt, max_attempts, "failed", error=last_error)
        else:
            last_child = child
            _record_child(parent.id, label, child)
            if child.status == "completed":
                _record_child_attempt(parent.id, label, child, attempt, max_attempts, "completed")
                return _single_report_from_gemini_child(parent, pdf=pdf, index=index, child=child)
            last_error = _child_error_message(child)
            _record_child_attempt(parent.id, label, child, attempt, max_attempts, "failed", error=last_error)

        if attempt < max_attempts and _is_retryable_child_error(label, last_error):
            _emit(
                parent.id,
                f"single_report_{index:02d}_gemini_retry",
                max(start_progress, 5),
                f"第 {index} 篇 Gemini 子任务失败，准备自动重试 {attempt}/{retry_limit}: {_compact_error(last_error)}",
            )
            continue
        break

    if last_child:
        _record_child(parent.id, label, last_child)
    raise RuntimeError(f"第 {index} 篇单篇 Gemini 子任务失败: {last_error or '未知错误'}")


def _single_report_from_gemini_child(parent, *, pdf: Dict[str, Any], index: int, child: PublicationTask) -> Dict[str, Any]:
    if child.status != "completed":
        raise RuntimeError(f"第 {index} 篇单篇 Gemini 子任务失败: {_child_error_message(child)}")
    raw_result = child.result or {}
    raw_final = raw_result.get("final_response_text") or raw_result.get("response_text") or ""
    if not raw_final:
        raise RuntimeError(f"第 {index} 篇单篇 Gemini 子任务没有返回正文")
    structured = normalize_for_tts(
        raw_final,
        initial_text=raw_result.get("initial_response_text") or "",
        metadata={
            "workflow_task_id": parent.id,
            "gemini_task_id": child.id,
            "pdf_filename": pdf.get("filename"),
            "top10_index": index,
        },
    )
    script = valid_single_report_script(structured)
    if not script:
        raise RuntimeError(f"第 {index} 篇单篇文案不可用")
    report = {
        "source": "single_gemini",
        "source_task_id": child.id,
        "gemini_task_id": child.id,
        "pdf": pdf,
        "pdf_asset_id": pdf.get("attachment_id"),
        "filename": pdf.get("filename"),
        "structured_result": structured,
        "single_report_script": script,
        "raw_text_chars": len(raw_final),
    }
    cache_single_report_script(
        pdf=pdf,
        structured_result=structured,
        source_feature=gemini_service.FEATURE_NAME,
        source_task_id=child.id,
        user_id=parent.params.get("user_id"),
        metadata={"source": FEATURE_NAME, "report_index": index},
    )
    return report


def _run_editor_gemini_with_retries(parent, editor_input: str, payload: Dict[str, Any]) -> PublicationTask:
    label = "editor_gemini"
    retry_limit = _retry_limit(GEMINI_RETRY_LIMIT_ENV_NAME, DEFAULT_GEMINI_RETRY_LIMIT)
    max_attempts = retry_limit + 1
    last_error = None
    last_child = None

    editor_task = _completed_editor_task(parent.id)
    if editor_task:
        _record_child(parent.id, label, editor_task)
        _emit(parent.id, "editor_gemini_reused", 92, "复用已完成的总编辑 Gem 子任务")
        return editor_task

    next_attempt = 1
    active_editor = _active_editor_task(parent.id)
    if active_editor:
        active_attempt = _child_attempt(active_editor, default=1, max_attempts=max_attempts)
        _record_child(parent.id, label, active_editor)
        _record_child_attempt(parent.id, label, active_editor, active_attempt, max_attempts, "running")
        _emit(parent.id, "editor_gemini_wait_existing", 74, "继续等待总编辑 Gem 子任务")
        try:
            editor_task = _wait_child(parent.id, active_editor.id, label, start_progress=74, end_progress=92)
        except Exception as exc:
            last_error = str(exc)
            latest = _load_task(active_editor.id, *TASK_CHILD_COLUMNS) or active_editor
            if latest and latest.status not in TERMINAL_STATUSES:
                _revoke_child_task(latest)
                _mark_child_failed(latest, last_error)
                latest = _load_task(latest.id, *TASK_CHILD_COLUMNS) or latest
            editor_task = latest
        if editor_task and editor_task.status == "completed":
            _record_child_attempt(parent.id, label, editor_task, active_attempt, max_attempts, "completed")
            return editor_task
        last_child = editor_task
        last_error = last_error or _child_error_message(editor_task)
        _record_child_attempt(parent.id, label, editor_task, active_attempt, max_attempts, "failed", error=last_error)
        if active_attempt < max_attempts and _is_retryable_child_error(label, last_error):
            _emit(
                parent.id,
                "editor_gemini_retry",
                74,
                f"总编辑 Gem 子任务失败，准备自动重试 {active_attempt}/{retry_limit}: {_compact_error(last_error)}",
            )
            next_attempt = active_attempt + 1
        else:
            if last_child:
                _record_child(parent.id, label, last_child)
            raise RuntimeError(f"总编辑 Gem 子任务失败: {last_error or '未知错误'}")

    for attempt in range(next_attempt, max_attempts + 1):
        retry_suffix = "" if attempt == 1 else f"（自动重试 {attempt - 1}/{retry_limit}）"
        editor_task = _create_editor_gemini_task(parent, editor_input, payload, attempt=attempt, max_attempts=max_attempts)
        _record_child(parent.id, label, editor_task)
        _record_child_attempt(parent.id, label, editor_task, attempt, max_attempts, "running")
        _emit(parent.id, "editor_gemini_submitted", 74, f"总编辑 Gem 子任务已提交{retry_suffix}")
        try:
            editor_task = _wait_child(parent.id, editor_task.id, label, start_progress=74, end_progress=92)
        except Exception as exc:
            last_error = str(exc)
            latest = _load_task(editor_task.id, *TASK_CHILD_COLUMNS) or editor_task
            if latest and latest.status not in TERMINAL_STATUSES:
                _revoke_child_task(latest)
                _mark_child_failed(latest, last_error)
                latest = _load_task(latest.id, *TASK_CHILD_COLUMNS) or latest
            editor_task = latest
            last_child = editor_task
            _record_child_attempt(parent.id, label, editor_task, attempt, max_attempts, "failed", error=last_error)
        else:
            last_child = editor_task
            _record_child(parent.id, label, editor_task)
            if editor_task.status == "completed":
                _record_child_attempt(parent.id, label, editor_task, attempt, max_attempts, "completed")
                return editor_task
            last_error = _child_error_message(editor_task)
            _record_child_attempt(parent.id, label, editor_task, attempt, max_attempts, "failed", error=last_error)

        if attempt < max_attempts and _is_retryable_child_error(label, last_error):
            _emit(
                parent.id,
                "editor_gemini_retry",
                74,
                f"总编辑 Gem 子任务失败，准备自动重试 {attempt}/{retry_limit}: {_compact_error(last_error)}",
            )
            continue
        break

    if last_child:
        _record_child(parent.id, label, last_child)
    raise RuntimeError(f"总编辑 Gem 子任务失败: {last_error or '未知错误'}")


def _create_single_gemini_task(parent, pdf: Dict[str, Any], index: int, payload: Dict[str, Any], *, attempt: int = 1, max_attempts: int = 1) -> PublicationTask:
    prompt = str(payload.get("single_report_prompt") or DEFAULT_INITIAL_PROMPT).strip()
    review_prompt = str(payload.get("single_report_review_prompt") or DEFAULT_REVIEW_PROMPT).strip()
    followups = [str(item).strip() for item in (payload.get("single_report_followups") or []) if str(item).strip()]
    if review_prompt and review_prompt not in followups:
        followups.append(review_prompt)
    retry_index = max(0, int(attempt or 1) - 1)
    metadata = {
        "source": FEATURE_NAME,
        "role": "single_report_parser",
        "parent_task_id": parent.id,
        "report_index": index,
        "pdf_asset_id": pdf.get("attachment_id"),
        "title": _pdf_title(pdf),
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
            "source_feature": pdf.get("source") or "pdf",
            "source_task_id": pdf.get("attachment_id"),
            "report_pdf_gemini": {
                "prompt": prompt,
                "followups": followups,
                "url": str(payload.get("single_gemini_url") or payload.get("gemini_url") or "").strip(),
                "metadata": metadata,
                "pdf": pdf,
                "pdf_path": pdf.get("path"),
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
    async_result = apply_async_after_db_release(
        process_report_pdf_gemini_job,
        args=[child_id],
        queue=gemini_service.QUEUE_NAME,
        retry=False,
    )
    child = _load_task(child_id, *TASK_CHILD_COLUMNS)
    child.celery_task_id = async_result.id
    db.session.commit()
    return _load_task(child_id, *TASK_CHILD_COLUMNS)


def _create_editor_gemini_task(parent, editor_input: str, payload: Dict[str, Any], *, attempt: int = 1, max_attempts: int = 1) -> PublicationTask:
    retry_index = max(0, int(attempt or 1) - 1)
    metadata = {
        "source": FEATURE_NAME,
        "role": "top10_editor",
        "parent_task_id": parent.id,
        "title": str(payload.get("program_name") or "今日研报严选"),
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
                "prompt": editor_input,
                "followups": [],
                "url": str(payload.get("editor_gemini_url") or default_editor_gemini_url()).strip(),
                "metadata": metadata,
                "pdf": None,
                "pdf_path": None,
            },
        },
        result={
            "metadata": metadata,
            "response_text": None,
            "turns": [],
            "pdf": None,
            "logs": [],
        },
    )
    db.session.add(child)
    db.session.flush()
    child_id = child.id
    db.session.commit()
    async_result = apply_async_after_db_release(
        process_report_pdf_gemini_job,
        args=[child_id],
        queue=gemini_service.QUEUE_NAME,
        retry=False,
    )
    child = _load_task(child_id, *TASK_CHILD_COLUMNS)
    child.celery_task_id = async_result.id
    db.session.commit()
    return _load_task(child_id, *TASK_CHILD_COLUMNS)


def _wait_child(parent_id: str, child_id: str, label: str, *, start_progress: int, end_progress: int) -> PublicationTask:
    started = time.monotonic()
    timeout = _child_timeout_seconds(label)
    interval = _poll_interval_seconds()
    last_progress = -1
    while True:
        _raise_if_canceled(parent_id)
        child = _load_task_status(child_id)
        if not child:
            raise RuntimeError(f"子任务不存在: {child_id}")
        if child.status in TERMINAL_STATUSES:
            final_child = _load_task(child_id, *TASK_CHILD_COLUMNS) or child
            _record_child(parent_id, label, final_child)
            refreshed_child = _load_task(child_id, *TASK_CHILD_COLUMNS)
            if not refreshed_child:
                raise RuntimeError(f"终态子任务重新加载失败: {child_id}")
            return refreshed_child
        if time.monotonic() - started > timeout:
            raise RuntimeError(f"等待子任务超时: {child_id}")
        child_progress = int(child.progress or 0)
        mapped = start_progress + int((end_progress - start_progress) * child_progress / 100)
        mapped = max(start_progress, min(end_progress - 1, mapped))
        if mapped != last_progress:
            _set_parent_progress(parent_id, f"{label}_waiting", mapped)
            last_progress = mapped
        time.sleep(interval)


def _reusable_single_report_for_pdf(pdf: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    cached = cached_single_report_for_pdf(pdf)
    if cached:
        return cached

    asset_id = str(pdf.get("attachment_id") or "").strip()
    if not asset_id:
        return None
    task = (
        PublicationTask.query.options(load_only(
            PublicationTask.id,
            PublicationTask.feature,
            PublicationTask.status,
            PublicationTask.final_script_chars,
            PublicationTask.source_task_id,
            PublicationTask.report_audio_pdf_attachment_id,
            PublicationTask.report_audio_cover_title,
            PublicationTask.report_audio_structured_title,
            PublicationTask.created_at,
            PublicationTask.completed_at,
        ))
        .filter(
            PublicationTask.feature.in_(SINGLE_REPORT_FEATURES),
            PublicationTask.status == "completed",
            PublicationTask.final_script_chars > 0,
            or_(
                PublicationTask.source_task_id == asset_id,
                PublicationTask.report_audio_pdf_attachment_id == asset_id,
            ),
        )
        .order_by(PublicationTask.completed_at.desc(), PublicationTask.created_at.desc())
        .first()
    )
    if not task:
        return None
    detail = _load_task(task.id, PublicationTask.result)
    result = detail.result if detail and isinstance(detail.result, dict) else {}
    structured = result.get("structured_result") if isinstance(result.get("structured_result"), dict) else {}
    script = valid_single_report_script(structured)
    if not script:
        return None
    payload = {
        "source": f"history_{task.feature}",
        "source_task_id": task.id,
        "pdf": pdf,
        "pdf_asset_id": asset_id,
        "filename": pdf.get("filename"),
        "structured_result": structured,
        "single_report_script": script,
    }
    cache_single_report_script(
        pdf=pdf,
        structured_result=structured,
        source_feature=task.feature,
        source_task_id=task.id,
        metadata={"source": f"history_{task.feature}"},
    )
    return payload


def _existing_single_gemini_child(parent_id: str, pdf: Dict[str, Any]) -> Optional[PublicationTask]:
    asset_id = str(pdf.get("attachment_id") or "").strip()
    if not asset_id:
        return None
    children = (
        PublicationTask.query.options(load_only(*TASK_CHILD_COLUMNS))
        .filter(
            PublicationTask.feature == gemini_service.FEATURE_NAME,
            PublicationTask.parent_task_id == parent_id,
            PublicationTask.source_task_id == asset_id,
        )
        .order_by(PublicationTask.created_at.desc())
        .limit(5)
        .all()
    )
    for child in children:
        metadata = _child_metadata(child)
        if metadata.get("role") == "single_report_parser":
            return child
    return None


def _completed_editor_task(parent_id: str) -> Optional[PublicationTask]:
    for child in _editor_children(parent_id):
        if child.status == "completed":
            return child
    return None


def _active_editor_task(parent_id: str) -> Optional[PublicationTask]:
    for child in _editor_children(parent_id):
        if child.status not in TERMINAL_STATUSES:
            return child
    return None


def _editor_children(parent_id: str) -> List[PublicationTask]:
    children = (
        PublicationTask.query.options(load_only(*TASK_CHILD_COLUMNS))
        .filter(
            PublicationTask.feature == gemini_service.FEATURE_NAME,
            PublicationTask.parent_task_id == parent_id,
        )
        .order_by(PublicationTask.created_at.desc())
        .limit(20)
        .all()
    )
    return [child for child in children if _child_metadata(child).get("role") == "top10_editor"]


def _existing_source_reports(parent_id: str) -> List[Dict[str, Any]]:
    task = _load_task(parent_id, *TASK_STATE_COLUMNS)
    result = task.result if task and isinstance(task.result, dict) else {}
    reports = result.get("source_reports") if isinstance(result.get("source_reports"), list) else []
    return [item for item in reports if isinstance(item, dict) and item.get("single_report_script")]


def _source_report_for_pdf(source_reports: List[Dict[str, Any]], pdf: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    asset_id = str(pdf.get("attachment_id") or "").strip()
    for report in source_reports:
        if asset_id and str(report.get("pdf_asset_id") or (report.get("pdf") or {}).get("attachment_id") or "").strip() == asset_id:
            return report
    return None


def _order_source_reports(source_reports: List[Dict[str, Any]], pdfs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    ordered = []
    for pdf in pdfs:
        report = _source_report_for_pdf(source_reports, pdf)
        if report:
            ordered.append(report)
    return ordered


def _child_metadata(child: PublicationTask) -> Dict[str, Any]:
    result = child.result if isinstance(child.result, dict) else {}
    metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else None
    if metadata is not None:
        return metadata
    params = child.params if isinstance(child.params, dict) else {}
    payload = params.get("report_pdf_gemini") if isinstance(params.get("report_pdf_gemini"), dict) else {}
    return payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}


def _record_child(parent_id: str, label: str, child: PublicationTask) -> None:
    parent = _load_task(parent_id, *TASK_STATE_COLUMNS)
    if not parent:
        return
    result = dict(parent.result or {})
    children = dict(result.get("children") or {})
    children[label] = _task_public_summary(child)
    result["children"] = children
    parent.result = result
    flag_modified(parent, "result")
    db.session.commit()


def _record_child_attempt(parent_id: str, label: str, child: PublicationTask, attempt: int, max_attempts: int, status: str, *, error: Optional[str] = None) -> None:
    parent = _load_task(parent_id, *TASK_STATE_COLUMNS)
    if not parent:
        return
    result = dict(parent.result or {})
    attempts = dict(result.get("child_attempts") or {})
    items = list(attempts.get(label) or [])
    summary = _task_public_summary(child) if child else {}
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
    parent.result = result
    flag_modified(parent, "result")
    db.session.commit()


def _task_public_summary(task: PublicationTask) -> Dict[str, Any]:
    return {
        "job_id": task.id,
        "celery_id": task.celery_task_id,
        "status": task.status,
        "step": task.current_step,
        "progress": task.progress or 0,
        "feature": task.feature,
        "error": task.error_message,
        "created_at": task.created_at.isoformat() + "Z" if task.created_at else None,
        "completed_at": task.completed_at.isoformat() + "Z" if task.completed_at else None,
        "metadata": _child_metadata(task),
    }


def _update_result(parent_id: str, values: Dict[str, Any]) -> None:
    task = _load_task(parent_id, *TASK_STATE_COLUMNS)
    if not task:
        return
    result = dict(task.result or {})
    result.update(values)
    task.result = result
    flag_modified(task, "result")
    db.session.commit()


def _emit(parent_id: str, step: str, progress: int, message: str) -> None:
    task = _load_task(parent_id, *TASK_STATE_COLUMNS)
    if not task:
        return
    result = dict(task.result or {})
    logs = list(result.get("logs") or [])
    logs.append({
        "step": step,
        "message": message,
        "progress": progress,
        "updated_at": datetime.utcnow().isoformat() + "Z",
    })
    result["logs"] = logs[-160:]
    task.current_step = step
    task.progress = max(0, min(100, int(progress or task.progress or 0)))
    task.result = result
    flag_modified(task, "result")
    db.session.commit()


def _set_parent_progress(parent_id: str, step: str, progress: int) -> None:
    clamped = max(0, min(99, int(progress or 0)))
    updated = (
        PublicationTask.query
        .filter(PublicationTask.id == parent_id)
        .update({
            PublicationTask.current_step: step,
            PublicationTask.progress: clamped,
        }, synchronize_session=False)
    )
    if not updated:
        db.session.rollback()
        return
    db.session.commit()


def _mark_failed_safely(task_id: str, message: str) -> None:
    _reset_session_for_failure_marker()
    try:
        task = _load_task(task_id, *TASK_STATE_COLUMNS)
        if task and task.status not in {"completed", "canceled"}:
            _mark_failed(task, message)
    except Exception:
        logger.exception("Failed to mark Report Top10 workflow failed: %s", task_id)
        _reset_session_for_failure_marker()


def _reset_session_for_failure_marker() -> None:
    try:
        db.session.rollback()
    except Exception:
        logger.exception("Failed to rollback Report Top10 workflow session before failure marker")
        try:
            db.session.remove()
        except Exception:
            logger.exception("Failed to remove Report Top10 workflow session before failure marker")


def _mark_failed(task: PublicationTask, message: str) -> None:
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


def _mark_child_failed(child: PublicationTask, message: str) -> None:
    fresh = _load_task(child.id, *TASK_STATE_COLUMNS)
    if not fresh or fresh.status in TERMINAL_STATUSES:
        return
    result = dict(fresh.result or {})
    result["error"] = message
    result["completed_at"] = datetime.utcnow().isoformat() + "Z"
    fresh.status = "failed"
    fresh.current_step = "failed"
    fresh.progress = max(int(fresh.progress or 0), 1)
    fresh.error_message = message
    fresh.completed_at = datetime.utcnow()
    fresh.result = result
    flag_modified(fresh, "result")
    db.session.commit()


def _revoke_child_task(child: PublicationTask) -> None:
    child_id = str(getattr(child, "id", None) or "-")
    celery_task_id = str(getattr(child, "celery_task_id", None) or "").strip()
    if not celery_task_id:
        return
    revoke_celery_task_after_db_commit(
        celery_task_id,
        operation=f"report top10 child revoke child={child_id}",
        business_task_id=child_id,
        log=logger,
    )


def _raise_if_canceled(parent_id: str) -> None:
    row = (
        PublicationTask.query
        .with_entities(PublicationTask.status)
        .filter(PublicationTask.id == parent_id)
        .first()
    )
    if row and row.status == "canceled":
        raise RuntimeError("父编排任务已取消")


def _load_task(task_id: str, *columns) -> Optional[PublicationTask]:
    attrs = columns or TASK_PAYLOAD_COLUMNS
    return PublicationTask.query.options(load_only(*attrs)).filter(PublicationTask.id == str(task_id)).one_or_none()


def _load_task_status(task_id: str):
    return (
        PublicationTask.query
        .with_entities(*TASK_CHILD_STATUS_COLUMNS)
        .filter(PublicationTask.id == task_id)
        .first()
    )


def _single_progress(index: int, total: int = 10) -> int:
    total = max(1, int(total or 1))
    index = max(0, min(total, int(index or 0)))
    return 5 + int(index / total * 65)


def _pdf_title(pdf: Dict[str, Any]) -> str:
    return str(pdf.get("topic_title") or pdf.get("filename") or pdf.get("attachment_id") or "PDF").strip()


def _child_error_message(child: PublicationTask) -> str:
    result = child.result if isinstance(child.result, dict) else {}
    return child.error_message or result.get("error") or child.current_step or child.status or "子任务失败"


def _child_attempt(child: PublicationTask, *, default: int, max_attempts: int) -> int:
    metadata = _child_metadata(child)
    try:
        attempt = int(metadata.get("attempt") or default)
    except (TypeError, ValueError):
        attempt = default
    return max(1, min(int(max_attempts or 1), attempt))


def _is_retryable_child_error(label: str, message: str) -> bool:
    text = str(message or "")
    if not text:
        return False
    lower_text = text.lower()
    if any(fragment.lower() in lower_text for fragment in NON_RETRYABLE_CHILD_ERROR_FRAGMENTS):
        return False
    return any(fragment.lower() in lower_text for fragment in RETRYABLE_CHILD_ERROR_FRAGMENTS)


def _retry_limit(env_name: str, default: int) -> int:
    return max(0, min(_int_env(env_name, default), 5))


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _compact_error(message: str, limit: int = 180) -> str:
    text = re.sub(r"\s+", " ", str(message or "")).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _child_timeout_seconds(label: str) -> int:
    env_name = "REPORT_TOP10_EDITOR_TIMEOUT" if label == "editor_gemini" else "REPORT_TOP10_SINGLE_REPORT_TIMEOUT"
    default = 1800 if label == "editor_gemini" else 1500
    try:
        return max(60, int(os.getenv(env_name, os.getenv("REPORT_TOP10_CHILD_TIMEOUT", str(default)))))
    except (TypeError, ValueError):
        return default


def _poll_interval_seconds() -> float:
    try:
        return max(1.0, float(os.getenv("REPORT_TOP10_CHILD_POLL_INTERVAL", "5")))
    except (TypeError, ValueError):
        return 5.0


def _truthy(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled", "y"}
