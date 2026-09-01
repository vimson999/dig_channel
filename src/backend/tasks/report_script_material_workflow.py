import json
import logging
import os
import re
import time
from datetime import datetime
from types import SimpleNamespace
from typing import Any, Dict, Optional

from sqlalchemy import inspect as sqlalchemy_inspect
from sqlalchemy import or_, text
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
    review_prompt_for_pdf_evidence,
    split_tts_text,
    structured_result_quality_issue,
)
from services.report_pdf_evidence import build_and_store_evidence
from services.report_script_material_workflow import FEATURE_NAME, QUEUE_NAME, write_result_file
from services.runtime_guard import (
    apply_async_after_db_release,
    guard_publication_task_start,
    maybe_guarded_apply_async,
    release_db_session_quietly,
    revoke_celery_task_after_db_commit,
    retry_exception_types,
)
from tasks.gemini_browser import process_report_pdf_gemini_job


logger = logging.getLogger("REPORT_SCRIPT_MATERIAL_WORKFLOW_TASK")

TERMINAL_STATUSES = {"completed", "failed", "blocked_by_captcha", "canceled"}
CELERY_FAILURE_STATES = {"FAILURE", "REVOKED"}
CURRENT_STEP_MAX_LENGTH = 80
GEMINI_RETRY_LIMIT_ENV_NAME = "REPORT_SCRIPT_MATERIAL_GEMINI_RETRY_LIMIT"
DEFAULT_GEMINI_RETRY_LIMIT = 2
DEFAULT_CHILD_TIMEOUT_SECONDS = 1800
DEFAULT_PARENT_RETRY_DELAY_SECONDS = 300
DEFAULT_PARENT_MAX_DEFERS = 144
DEFAULT_NORMALIZER_TIMEOUT_SECONDS = 90
DEFAULT_NORMALIZER_RETRY_LIMIT = 2
DEFAULT_NORMALIZER_RETRY_DELAY_SECONDS = 15
MATERIAL_REUSE_FEATURES = (
    FEATURE_NAME,
    "report_pdf_audio_workflow",
    "report_audio_workflow",
)
AUTO_SCRIPT_MATERIAL_SOURCE = "zsxq_auto_script_material_dispatch"
AUTO_SCRIPT_MATERIAL_AUDIO_SOURCE = "zsxq_auto_script_material_audio_handoff"
REPORT_AUDIO_WORKFLOW_FEATURE = "report_pdf_audio_workflow"

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
    PublicationTask.source_task_id,
    PublicationTask.report_audio_pdf_attachment_id,
)
TASK_CHILD_COLUMNS = (
    *TASK_STATE_COLUMNS,
    PublicationTask.params,
    PublicationTask.feature,
    PublicationTask.parent_task_id,
    PublicationTask.source_task_id,
)
TASK_CHILD_POLL_COLUMNS = (
    PublicationTask.id,
    PublicationTask.celery_task_id,
    PublicationTask.status,
    PublicationTask.current_step,
    PublicationTask.progress,
    PublicationTask.error_message,
    PublicationTask.created_at,
    PublicationTask.updated_at,
    PublicationTask.completed_at,
    PublicationTask.feature,
    PublicationTask.parent_task_id,
    PublicationTask.source_task_id,
)


class ChildTaskStillRunning(RuntimeError):
    def __init__(self, label: str, child_id: str, status: str = "", step: str = ""):
        self.label = label
        self.child_id = child_id
        self.status = status
        self.step = step
        super().__init__(f"子任务仍在执行: {label} {child_id} ({status or '-'} / {step or '-'})")


@celery_app.task(
    bind=True,
    name="tasks.report_script_material_workflow.process_job",
    store_errors_even_if_ignored=True,
    autoretry_for=(OperationalError,),
    retry_backoff=True,
    retry_jitter=True,
    retry_kwargs={"max_retries": 3},
)
def process_report_script_material_workflow_job(self, task_id):
    app = create_app()
    with app.app_context():
        try:
            guard_publication_task_start(self, task_id, feature=FEATURE_NAME, queue=QUEUE_NAME)
            return _process_job(task_id)
        except ChildTaskStillRunning as exc:
            _defer_parent_retry(task_id, exc)
            return True
        except retry_exception_types():
            raise
        except Exception as exc:
            logger.error("Report script material workflow failed: %s", exc, exc_info=True)
            _mark_failed_safely(task_id, str(exc))
            return False
        finally:
            db.session.remove()


def _process_job(task_id: str) -> bool:
    task = _load_task(task_id, *TASK_PAYLOAD_COLUMNS)
    if not task:
        logger.error("Report script material workflow task not found: %s", task_id)
        return False
    if not isinstance(task.params, dict) or task.params.get("feature") != FEATURE_NAME:
        logger.error("Invalid report script material workflow task params: %s", task_id)
        return False
    if task.status == "canceled":
        return False
    if task.status == "completed" and _valid_structured_result(task.result):
        return True

    payload = _workflow_payload(task)
    pdf = payload.get("pdf") if isinstance(payload.get("pdf"), dict) else {}
    pdf_path = str(payload.get("pdf_path") or pdf.get("path") or "").strip()
    if not pdf_path:
        _mark_failed(task, "PDF 文件不能为空")
        return False

    prompt = str(payload.get("prompt") or DEFAULT_INITIAL_PROMPT).strip()
    pdf_evidence_enabled = (
        _truthy(payload.get("pdf_evidence_enabled"), True)
        if "pdf_evidence_enabled" in payload
        else True
    )
    review_prompt = review_prompt_for_pdf_evidence(
        payload.get("review_prompt") or DEFAULT_REVIEW_PROMPT,
        enabled=pdf_evidence_enabled,
    )
    followups = [str(item).strip() for item in (payload.get("followups") or []) if str(item).strip()]
    if review_prompt and review_prompt not in followups:
        followups.append(review_prompt)

    workflow_task_id = task.id
    parent_ref = SimpleNamespace(
        id=workflow_task_id,
        created_at=task.created_at,
        params={"user_id": task.user_id or task.params.get("user_id")},
    )
    started_at = datetime.utcnow().isoformat() + "Z"
    result = dict(task.result or {})
    result.update({
        "started_at": result.get("started_at") or started_at,
        "error": None,
        "logs": result.get("logs") or [],
        "children": result.get("children") or {},
        "pdf": result.get("pdf") or pdf,
    })
    task.status = "processing"
    task.current_step = "prepare_material"
    task.progress = max(int(task.progress or 0), 2)
    task.error_message = None
    task.completed_at = None
    task.result = result
    flag_modified(task, "result")
    db.session.commit()

    structured = _valid_structured_result(result)
    source = "existing_structured_result"
    source_task_id = workflow_task_id
    gemini_task = None
    gemini_result = None
    raw_final = ""

    if structured:
        _emit(workflow_task_id, "reuse_structured_result", 35, "复用当前任务已有结构化文案")
    else:
        reusable = _reusable_material_for_pdf(pdf, exclude_task_id=workflow_task_id)
        if reusable:
            structured = reusable["structured_result"]
            source = "reused_completed_material"
            source_task_id = reusable["source_task_id"]
            _emit(workflow_task_id, "reuse_completed_material", 45, "复用同 PDF 已完成基础文案物料")
        else:
            gemini_task = _task_snapshot(_completed_gemini_task(workflow_task_id, result))
            active_gemini_task = None if gemini_task else _task_snapshot(_active_gemini_task(workflow_task_id, result))
            if gemini_task:
                _record_child(workflow_task_id, "gemini", gemini_task)
                _emit(workflow_task_id, "reuse_gemini_result", 52, "复用已完成 Gemini 子任务")
            elif active_gemini_task:
                _record_child(workflow_task_id, "gemini", active_gemini_task)
                _emit(workflow_task_id, "gemini_wait_existing", 10, "继续等待已提交的 Gemini 子任务")
                active_gemini_task_id = active_gemini_task.id
                gemini_task = _wait_child(workflow_task_id, active_gemini_task_id, "gemini", start_progress=10, end_progress=52)
                if gemini_task.status != "completed":
                    error = _child_error_message(gemini_task)
                    if not _is_retryable_child_error(error):
                        raise RuntimeError(f"Gemini 子任务失败: {error}")
                    _emit(workflow_task_id, "gemini_retry", 12, f"Gemini 子任务失败，准备重试: {_compact_error(error)}")
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
            else:
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

            if gemini_task:
                if gemini_task.status != "completed":
                    raise RuntimeError(f"Gemini 子任务失败: {_child_error_message(gemini_task)}")
                gemini_result = gemini_task.result or {}
                raw_final = gemini_result.get("final_response_text") or gemini_result.get("response_text") or ""
                initial_text = gemini_result.get("initial_response_text") or ""
                if not raw_final:
                    raise RuntimeError("Gemini 子任务没有返回 final_response_text")
                final_chars = len(_plain_text(raw_final))
                min_final_chars = _min_final_chars()
                if final_chars < min_final_chars:
                    raise RuntimeError(
                        f"Gemini 子任务返回内容过短: {final_chars} 字，低于 {min_final_chars} 字"
                    )
                normalizer_timeout = _normalizer_timeout_seconds()
                logger.info(
                    "Normalizing report script material: task=%s gemini_task=%s timeout=%ss",
                    workflow_task_id,
                    gemini_task.id,
                    normalizer_timeout,
                )
                structured = _normalize_for_tts_with_retries(
                    workflow_task_id,
                    raw_final,
                    initial_text=initial_text,
                    metadata={
                        "workflow_task_id": workflow_task_id,
                        "gemini_task_id": gemini_task.id,
                        "pdf_filename": pdf.get("filename"),
                    },
                    normalizer_timeout=normalizer_timeout,
                )
                logger.info(
                    "Report script material normalized: task=%s provider=%s chars=%s",
                    workflow_task_id,
                    structured.get("provider"),
                    len(_plain_text(structured.get("final_script_for_tts") or "")),
                )
                source = "gemini"
                source_task_id = gemini_task.id
                _update_result(workflow_task_id, {"gemini": _task_public_summary(gemini_task)})

    if not structured:
        raise RuntimeError("未能生成基础文案物料")

    gemini_evidence_task_id = str(getattr(gemini_task, "id", "") or "") or None
    _complete_material_task(
        workflow_task_id,
        structured=structured,
        pdf=pdf,
        gemini_task=gemini_task,
        gemini_result=gemini_result,
        source=source,
        source_task_id=source_task_id,
    )
    if raw_final and pdf_evidence_enabled:
        try:
            release_db_session_quietly()
            evidence_output = build_and_store_evidence(
                source_task_id=workflow_task_id,
                pdf_path=pdf_path,
                final_script=structured.get("final_script_for_tts") or "",
                raw_gemini_text=raw_final,
                pdf_attachment_id=pdf.get("attachment_id"),
                pdf_filename=pdf.get("filename"),
                gemini_task_id=gemini_evidence_task_id,
            )
            _update_result(workflow_task_id, {
                "pdf_evidence": {
                    "status": evidence_output.status,
                    "output_id": evidence_output.id,
                    "claim_count": int(evidence_output.claim_count or 0),
                    "verified_claim_count": int(evidence_output.verified_claim_count or 0),
                },
            })
        except Exception as exc:
            db.session.rollback()
            logger.warning("PDF evidence shadow build failed task=%s error=%s", workflow_task_id, exc)
            try:
                _update_result(workflow_task_id, {
                    "pdf_evidence": {
                        "status": "failed",
                        "error": str(exc)[:1000],
                    },
                })
            except Exception:
                db.session.rollback()
                logger.exception("Failed to persist PDF evidence failure task=%s", workflow_task_id)
    elif not pdf_evidence_enabled:
        _update_result(workflow_task_id, {
            "pdf_evidence": {
                "status": "disabled",
                "enabled": False,
            },
        })
    _dispatch_auto_storyboard(workflow_task_id)
    logger.info("Report script material workflow completed: %s", workflow_task_id)
    return True


def _dispatch_auto_storyboard(material_task_id: str) -> None:
    material = _load_task(
        material_task_id,
        PublicationTask.id,
        PublicationTask.params,
        PublicationTask.result,
        PublicationTask.user_id,
        PublicationTask.task_title,
        PublicationTask.feature,
        PublicationTask.status,
        PublicationTask.report_audio_pdf_attachment_id,
        PublicationTask.report_audio_pdf_filename,
        PublicationTask.report_audio_pdf_bytes,
        PublicationTask.report_audio_pdf_topic_title,
    )
    if not material or material.status != "completed":
        return
    payload = _workflow_payload(material)
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    metadata_source = str(metadata.get("source") or "")
    if metadata_source == AUTO_SCRIPT_MATERIAL_SOURCE:
        visual_scheme_keys = metadata.get("visual_scheme_keys")
        if not isinstance(visual_scheme_keys, list) or not visual_scheme_keys:
            return
        try:
            from tasks.report_audio_workflow import _dispatch_requested_visual_workflows

            dispatch = _dispatch_requested_visual_workflows(
                material_task_id,
                {"visual_scheme_keys": visual_scheme_keys},
            )
            _update_result(material_task_id, {"visual_workflows": dispatch})
            logger.info(
                "Auto visual workflows dispatched: material=%s schemes=%s",
                material_task_id,
                visual_scheme_keys,
            )
        except Exception as exc:
            db.session.rollback()
            _update_result(material_task_id, {
                "visual_workflows": {
                    "requested": visual_scheme_keys,
                    "items": [],
                    "status": "failed",
                    "error": str(exc),
                },
            })
            logger.error(
                "Auto visual workflows dispatch failed: material=%s schemes=%s error=%s",
                material_task_id,
                visual_scheme_keys,
                exc,
                exc_info=True,
            )
        try:
            audio_dispatch = _dispatch_auto_audio_for_material(material)
            _update_result(material_task_id, {"auto_audio_workflow": audio_dispatch})
            logger.info(
                "Auto audio workflow handoff: material=%s result=%s",
                material_task_id,
                audio_dispatch,
            )
        except Exception as exc:
            db.session.rollback()
            _update_result(material_task_id, {
                "auto_audio_workflow": {
                    "status": "failed",
                    "error": str(exc)[:1000],
                },
            })
            logger.error(
                "Auto audio workflow handoff failed: material=%s error=%s",
                material_task_id,
                exc,
                exc_info=True,
            )
        return
    if metadata_source != "report-stick-figure-video-workflow":
        return
    workflow_task_id = str(metadata.get("workflow_job_id") or "").strip()
    if not workflow_task_id:
        return
    try:
        from tasks.report_visual_plan import enqueue_report_visual_plan_for_material

        dispatch = enqueue_report_visual_plan_for_material(
            material_task_id,
            user_id=str(material.user_id or ""),
            parent_feature="report_stick_figure_video_workflow",
            parent_task_id=workflow_task_id,
            visual_scheme_key=str(metadata.get("visual_scheme_key") or "stick_figure"),
        )
        storyboard_id = str(dispatch.get("job_id") or "")
        storyboard_status = str(dispatch.get("status") or "pending")
        parent_status = "completed" if storyboard_status == "completed" else "pending"
        now = datetime.utcnow()
        PublicationTask.query.filter(
            PublicationTask.id == workflow_task_id,
            PublicationTask.feature == "report_stick_figure_video_workflow",
            PublicationTask.status != "canceled",
        ).update({
            PublicationTask.status: "processing",
            PublicationTask.current_step: "storyboard_done" if parent_status == "completed" else "storyboard_queued",
            PublicationTask.progress: 60 if parent_status == "completed" else 35,
            PublicationTask.waiting_child_id: storyboard_id,
            PublicationTask.waiting_child_label: "标题与正文分镜",
            PublicationTask.waiting_child_status: parent_status,
            PublicationTask.waiting_child_step: "done" if parent_status == "completed" else "queued",
            PublicationTask.waiting_child_updated_at: now.isoformat() + "Z",
            PublicationTask.error_message: None,
            PublicationTask.updated_at: now,
        }, synchronize_session=False)
        db.session.commit()
        logger.info(
            "Auto storyboard dispatched: workflow=%s material=%s storyboard=%s status=%s",
            workflow_task_id,
            material_task_id,
            storyboard_id,
            storyboard_status,
        )
    except Exception as exc:
        db.session.rollback()
        now = datetime.utcnow()
        PublicationTask.query.filter(
            PublicationTask.id == workflow_task_id,
            PublicationTask.feature == "report_stick_figure_video_workflow",
            PublicationTask.status != "canceled",
        ).update({
            PublicationTask.status: "failed",
            PublicationTask.current_step: "storyboard_submit_failed",
            PublicationTask.error_message: str(exc)[:4000],
            PublicationTask.completed_at: now,
            PublicationTask.updated_at: now,
        }, synchronize_session=False)
        db.session.commit()
        logger.error(
            "Auto storyboard dispatch failed: workflow=%s material=%s error=%s",
            workflow_task_id,
            material_task_id,
            exc,
            exc_info=True,
        )


def _dispatch_auto_audio_for_material(material: PublicationTask) -> Dict[str, Any]:
    """Continue an automatic PDF material job into reusable-script audio/video.

    The material branch used to stop after initializing visual assets.  Its
    images then had no whole WAV to align against unless a separately enabled
    auto-video dispatch happened to create one.  This handoff is deliberately
    material-owned and idempotent: it reuses the completed script, never runs
    Gemini again, and does not replace an existing automatic audio workflow.
    """
    material_id = str(getattr(material, "id", "") or "").strip()
    if not material_id:
        return {"status": "skipped", "reason": "missing_material_id"}
    if str(getattr(material, "status", "") or "") != "completed":
        return {"status": "skipped", "reason": "material_not_completed"}

    existing = (
        PublicationTask.query.options(load_only(
            PublicationTask.id,
            PublicationTask.status,
            PublicationTask.current_step,
            PublicationTask.celery_task_id,
        ))
        .filter(
            PublicationTask.feature.in_(MATERIAL_REUSE_FEATURES[1:]),
            PublicationTask.parent_feature == FEATURE_NAME,
            PublicationTask.parent_task_id == material_id,
            PublicationTask.source_task_id == material_id,
        )
        .order_by(PublicationTask.created_at.desc())
        .first()
    )
    if existing:
        return {
            "status": "existing",
            "task_id": str(existing.id),
            "task_status": str(existing.status or ""),
            "current_step": str(existing.current_step or ""),
        }

    payload = _workflow_payload(material)
    pdf = payload.get("pdf") if isinstance(payload.get("pdf"), dict) else {}
    pdf_path = str(pdf.get("path") or payload.get("pdf_path") or "").strip()
    attachment_id = str(
        pdf.get("attachment_id")
        or getattr(material, "report_audio_pdf_attachment_id", "")
        or ""
    ).strip()
    if not pdf_path or not attachment_id:
        return {"status": "skipped", "reason": "missing_pdf_identity"}

    from services.report_audio_workflow import QUEUE_NAME as AUDIO_QUEUE_NAME
    from tasks.report_audio_workflow import process_report_audio_workflow_job

    handoff_metadata = {
        "source": AUTO_SCRIPT_MATERIAL_AUDIO_SOURCE,
        "material_task_id": material_id,
        "attachment_id": attachment_id,
        "queued_at": datetime.utcnow().isoformat() + "Z",
    }
    video_options = {
        "enabled": True,
        "vertical_enabled": True,
        "vertical_video_scheme": "scheme3",
        "vertical_video_schemes": ["scheme3"],
        "render_mode": "full_video",
        "bilibili_enabled": False,
        "sub_burn_enabled": True,
        "source": AUTO_SCRIPT_MATERIAL_AUDIO_SOURCE,
    }
    task = PublicationTask(
        status="pending",
        current_step="queued",
        progress=0,
        params={
            "feature": REPORT_AUDIO_WORKFLOW_FEATURE,
            "user_id": str(getattr(material, "user_id", "") or "system"),
            "parent_feature": FEATURE_NAME,
            "parent_task_id": material_id,
            "source_feature": FEATURE_NAME,
            "source_task_id": material_id,
            "report_audio_workflow": {
                "metadata": handoff_metadata,
                "followups": [],
                "gemini_url": "",
                "pdf_evidence_enabled": False,
                "tts_provider": "doubao_browser",
                "video": video_options,
                "pdf": dict(pdf),
                "pdf_path": pdf_path,
            },
        },
        result={
            "metadata": handoff_metadata,
            "pdf": dict(pdf),
            "children": {},
            "structured_result": None,
            "audio": None,
            "video": None,
            "logs": [],
            "script_material": {
                "status": "pending",
                "mode": "reusable_script_expected",
                "source_task_id": material_id,
            },
        },
    )
    task.task_title = getattr(material, "task_title", None) or getattr(
        material, "report_audio_pdf_topic_title", None
    ) or getattr(material, "report_audio_pdf_filename", None) or "研报自动语音视频"
    task.parent_feature = FEATURE_NAME
    task.parent_task_id = material_id
    task.source_feature = FEATURE_NAME
    task.source_task_id = material_id
    task.report_audio_pdf_attachment_id = attachment_id
    task.report_audio_pdf_filename = pdf.get("filename") or getattr(material, "report_audio_pdf_filename", None)
    task.report_audio_pdf_bytes = str(pdf.get("bytes") or getattr(material, "report_audio_pdf_bytes", "") or "")
    task.report_audio_pdf_topic_title = pdf.get("topic_title") or getattr(material, "report_audio_pdf_topic_title", None)
    db.session.add(task)
    db.session.flush()
    task_id = str(task.id)
    db.session.commit()

    try:
        submission = maybe_guarded_apply_async(
            process_report_audio_workflow_job,
            args=[task_id],
            queue=AUDIO_QUEUE_NAME,
            feature=REPORT_AUDIO_WORKFLOW_FEATURE,
            task=task,
            retry=False,
        )
        task.celery_task_id = submission["async_result"].id
        db.session.commit()
    except Exception as exc:
        task.status = "failed"
        task.current_step = "enqueue_failed"
        task.error_message = f"自动语音任务入队失败: {exc}"[:4000]
        task.completed_at = datetime.utcnow()
        db.session.commit()
        return {"status": "failed", "task_id": task_id, "error": task.error_message}

    return {
        "status": "deferred" if submission.get("deferred") else "queued",
        "task_id": task_id,
        "celery_task_id": task.celery_task_id,
        "admission": submission.get("admission"),
        "vertical_video_schemes": ["scheme3"],
    }


def _workflow_payload(task: PublicationTask) -> Dict[str, Any]:
    params = task.params if isinstance(task.params, dict) else {}
    payload = params.get(FEATURE_NAME)
    if isinstance(payload, dict):
        return payload
    legacy = params.get("report_audio_workflow")
    return legacy if isinstance(legacy, dict) else {}


def _run_gemini_task_with_retries(parent, *, pdf, pdf_path, prompt, followups, payload, start_progress, end_progress):
    retry_limit = _retry_limit()
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
        gemini_task = _task_snapshot(gemini_task)
        gemini_task_id = gemini_task.id if gemini_task else None
        last_task = gemini_task
        _record_child(parent.id, "gemini", gemini_task)
        _record_child_attempt(parent.id, "gemini", gemini_task, attempt, max_attempts, "running")

        try:
            if not gemini_task_id:
                raise RuntimeError("Gemini 子任务创建失败")
            gemini_task = _wait_child(parent.id, gemini_task_id, "gemini", start_progress=start_progress, end_progress=end_progress)
        except ChildTaskStillRunning:
            if gemini_task_id:
                _record_child(parent.id, "gemini", _load_task_snapshot(gemini_task_id) or gemini_task)
            raise
        except Exception as exc:
            last_error = str(exc)
            latest = _load_task_snapshot(gemini_task_id) if gemini_task_id else gemini_task
            if latest and latest.status not in TERMINAL_STATUSES:
                _revoke_child_task(latest)
                _mark_child_failed(latest, last_error)
                latest = _load_task_snapshot(latest.id) or latest
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

        if attempt < max_attempts and _is_retryable_child_error(last_error):
            _emit(parent.id, "gemini_retry", max(start_progress, 10), f"Gemini 子任务失败，准备自动重试 {attempt}/{retry_limit}: {_compact_error(last_error)}")
            continue
        break

    if last_task:
        _record_child(parent.id, "gemini", last_task)
    raise RuntimeError(f"Gemini 子任务失败: {last_error or '未知错误'}")


def _create_gemini_task(parent, pdf, pdf_path, prompt, followups, payload, *, attempt=1, max_attempts=1):
    retry_index = max(0, int(attempt or 1) - 1)
    metadata = {
        "source": FEATURE_NAME,
        "role": "script_material_parser",
        "parent_task_id": parent.id,
        "pdf_asset_id": pdf.get("attachment_id"),
        "title": pdf.get("topic_title") or pdf.get("filename"),
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
            "source_feature": pdf.get("source") or FEATURE_NAME,
            "source_task_id": pdf.get("attachment_id") or parent.id,
            "report_pdf_gemini": {
                "prompt": prompt,
                "followups": followups,
                "url": str(payload.get("gemini_url") or payload.get("url") or "").strip(),
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
    child_snapshot = _task_snapshot(child)
    db.session.commit()
    async_result = apply_async_after_db_release(
        process_report_pdf_gemini_job,
        args=[child_id],
        queue=gemini_service.QUEUE_NAME,
        retry=False,
    )
    _persist_child_celery_task_id(child_id, async_result.id)
    child_snapshot.celery_task_id = async_result.id
    return child_snapshot


def _reusable_material_for_pdf(pdf: Dict[str, Any], *, exclude_task_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    asset_id = str(pdf.get("attachment_id") or "").strip()
    if not asset_id:
        return None
    query = (
        PublicationTask.query.options(load_only(
            PublicationTask.id,
            PublicationTask.feature,
            PublicationTask.status,
            PublicationTask.final_script_chars,
            PublicationTask.source_task_id,
            PublicationTask.report_audio_pdf_attachment_id,
            PublicationTask.created_at,
            PublicationTask.completed_at,
        ))
        .filter(PublicationTask.feature.in_(MATERIAL_REUSE_FEATURES))
        .filter(PublicationTask.status == "completed")
        .filter(PublicationTask.final_script_chars > 0)
        .filter(or_(
            PublicationTask.source_task_id == asset_id,
            PublicationTask.report_audio_pdf_attachment_id == asset_id,
        ))
    )
    if exclude_task_id:
        query = query.filter(PublicationTask.id != exclude_task_id)
    candidates = query.order_by(PublicationTask.completed_at.desc(), PublicationTask.created_at.desc()).limit(20).all()
    details = _load_task_results(candidate.id for candidate in candidates)
    for candidate in candidates:
        detail = details.get(candidate.id)
        structured = _valid_structured_result(detail.result if detail else None)
        if structured:
            return {
                "source_task_id": candidate.id,
                "source_feature": candidate.feature,
                "structured_result": structured,
            }
    return None


def _completed_gemini_task(parent_id: str, result: Dict[str, Any]) -> Optional[PublicationTask]:
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
    candidate = (
        db.session.query(PublicationTask.id)
        .filter(PublicationTask.feature == gemini_service.FEATURE_NAME)
        .filter(PublicationTask.parent_task_id == parent_id)
        .filter(PublicationTask.status == "completed")
        .filter(PublicationTask.gemini_response_chars >= _min_gemini_final_chars())
        .order_by(PublicationTask.created_at.desc())
        .first()
    )
    return _load_task(candidate.id, *TASK_CHILD_COLUMNS) if candidate else None


def _active_gemini_task(parent_id: str, result: Dict[str, Any]) -> Optional[PublicationTask]:
    candidate_ids = []
    children = result.get("children") if isinstance(result, dict) and isinstance(result.get("children"), dict) else {}
    gemini = children.get("gemini") if isinstance(children.get("gemini"), dict) else {}
    if gemini.get("job_id"):
        candidate_ids.append(str(gemini["job_id"]))
    for candidate_id in dict.fromkeys(candidate_ids):
        task = _load_task(candidate_id, *TASK_CHILD_COLUMNS)
        if task and task.status not in TERMINAL_STATUSES and task.feature == gemini_service.FEATURE_NAME:
            return task
    return (
        PublicationTask.query.options(load_only(*TASK_CHILD_POLL_COLUMNS))
        .filter(PublicationTask.feature == gemini_service.FEATURE_NAME)
        .filter(PublicationTask.parent_task_id == parent_id)
        .filter(PublicationTask.status.notin_(list(TERMINAL_STATUSES)))
        .order_by(PublicationTask.created_at.desc())
        .first()
    )


def _wait_child(parent_id: str, child_id: str, label: str, *, start_progress: int, end_progress: int):
    timeout = _child_inline_wait_seconds()
    poll_seconds = max(1, _int_env("REPORT_SCRIPT_MATERIAL_CHILD_POLL_SECONDS", 3))
    parent_poll_seconds = max(3, _int_env("REPORT_SCRIPT_MATERIAL_PARENT_POLL_SECONDS", 15))
    deadline = time.monotonic() + timeout
    next_parent_check = 0.0
    last_step = None
    while True:
        now = time.monotonic()
        if now >= next_parent_check:
            parent = _load_task(parent_id, PublicationTask.status)
            if not parent or parent.status == "canceled":
                raise RuntimeError("父编排任务已取消")
            next_parent_check = now + parent_poll_seconds
        child = _task_snapshot(_load_task(child_id, *TASK_CHILD_POLL_COLUMNS), include_payload=False)
        if not child:
            raise RuntimeError(f"子任务不存在: {child_id}")
        if child.current_step != last_step:
            last_step = child.current_step
            child_progress = int(child.progress or 0)
            mapped = start_progress + int((end_progress - start_progress) * child_progress / 100)
            _emit(parent_id, f"{label}_{child.current_step}", mapped, f"{label}: {child.current_step}")
            _record_child(parent_id, label, child)
        if child.status in TERMINAL_STATUSES:
            completed_child = _load_task_snapshot(child_id) or child
            _record_child(parent_id, label, completed_child)
            _clear_waiting_child(parent_id, label)
            return completed_child
        celery_failure = _celery_child_failure_message(child)
        if celery_failure:
            _mark_child_failed(child, celery_failure)
            db.session.expire_all()
            _clear_waiting_child(parent_id, label)
            failed_child = _load_task_snapshot(child_id) or child
            _record_child(parent_id, label, failed_child)
            return failed_child
        if time.monotonic() >= deadline:
            waiting_child = _load_task_snapshot(child_id) or child
            _record_child(parent_id, label, waiting_child)
            raise ChildTaskStillRunning(label, waiting_child.id, waiting_child.status or "", waiting_child.current_step or "")
        release_db_session_quietly()
        sleep_seconds = min(poll_seconds, max(0.2, deadline - time.monotonic()))
        time.sleep(sleep_seconds)


def _complete_material_task(
    task_id: str,
    *,
    structured: Dict[str, Any],
    pdf: Dict[str, Any],
    gemini_task: Optional[PublicationTask],
    gemini_result: Optional[Dict[str, Any]],
    source: str,
    source_task_id: Optional[str],
) -> None:
    chunks = split_tts_text(structured.get("final_script_for_tts") or "")
    if not chunks:
        raise RuntimeError("结构化结果没有可朗读正文")
    tts_chars = len(_plain_text(structured.get("final_script_for_tts") or ""))
    min_final_chars = _min_final_chars()
    if tts_chars < min_final_chars:
        raise RuntimeError(f"结构化可朗读正文过短: {tts_chars} 字，低于 {min_final_chars} 字")

    completed_at = datetime.utcnow()
    completed_at_text = completed_at.isoformat() + "Z"
    script_material = {
        "status": "completed",
        "mode": FEATURE_NAME,
        "source": source,
        "source_task_id": source_task_id,
        "pdf_attachment_id": pdf.get("attachment_id"),
        "pdf_filename": pdf.get("filename"),
        "chars": tts_chars,
        "chunks": len(chunks),
        "completed_at": completed_at_text,
    }
    video_result = {
        "enabled": False,
        "status": "skipped",
        "message": FEATURE_NAME,
    }
    task = _load_task(task_id, *TASK_STATE_COLUMNS)
    if not task:
        raise RuntimeError("基础文案物料任务不存在")
    result = dict(task.result or {})
    tts_text = {
        "chars": len(structured.get("final_script_for_tts") or ""),
        "chunks": len(chunks),
    }
    output_path = write_result_file(
        job_id=task.id,
        created_at=task.created_at,
        structured_result=structured,
        script_material=script_material,
        pdf=pdf,
        gemini=gemini_result,
        children=result.get("children") if isinstance(result.get("children"), dict) else {},
        logs=result.get("logs") if isinstance(result.get("logs"), list) else [],
        tts_text=tts_text,
        error=result.get("error"),
    )
    result.update({
        "gemini": _task_public_summary(gemini_task) if gemini_task else result.get("gemini"),
        "structured_result": structured,
        "tts_text": tts_text,
        "script_material": script_material,
        "audio": None,
        "video": video_result,
        "output_path": output_path,
        "completed_at": completed_at_text,
    })
    task.status = "completed"
    task.current_step = "done"
    task.progress = 100
    task.completed_at = completed_at
    task.error_message = None
    task.result = result
    flag_modified(task, "result")
    db.session.commit()


def _valid_structured_result(result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    structured = result.get("structured_result") if isinstance(result, dict) else None
    if not isinstance(structured, dict):
        return None
    script = str(structured.get("final_script_for_tts") or "").strip()
    if not script:
        return None
    quality_issue = structured_result_quality_issue(structured)
    if quality_issue:
        logger.warning("Skip cached script material with quality issue: %s", quality_issue)
        return None
    if len(_plain_text(script)) < _min_final_chars():
        return None
    return structured


def _valid_gemini_task_result(result: Dict[str, Any]) -> bool:
    if not isinstance(result, dict):
        return False
    raw_text = result.get("final_response_text") or result.get("response_text") or ""
    return len(_plain_text(raw_text)) >= _min_final_chars()


def _record_child(parent_id: str, label: str, child) -> None:
    if not child:
        return
    summary = _task_public_summary(child)
    _record_child_summary(parent_id, label, summary)


def _record_child_summary(parent_id: str, label: str, summary: Optional[Dict[str, Any]]) -> None:
    if not summary:
        return
    if _is_postgresql_session():
        db.session.execute(
            text("""
                UPDATE public.publication_tasks
                SET result = jsonb_set(
                        jsonb_set(
                            COALESCE(result, '{}'::jsonb),
                            '{children}',
                            COALESCE(result->'children', '{}'::jsonb),
                            true
                        ),
                        ARRAY['children', CAST(:label AS text)],
                        COALESCE(result #> ARRAY['children', CAST(:label AS text)], '{}'::jsonb)
                            || CAST(:child AS jsonb),
                        true
                    ),
                    updated_at = NOW()
                WHERE id = :task_id AND status IS DISTINCT FROM 'canceled'
            """),
            {
                "task_id": str(parent_id),
                "label": str(label),
                "child": json.dumps(summary, ensure_ascii=False),
            },
        )
        db.session.commit()
        return
    parent = _load_task(parent_id, *TASK_STATE_COLUMNS)
    if not parent:
        return
    result = dict(parent.result or {})
    children = dict(result.get("children") or {})
    previous = children.get(label) if isinstance(children.get(label), dict) else {}
    children[label] = _merge_child_summary(previous, summary)
    result["children"] = children
    parent.result = result
    flag_modified(parent, "result")
    db.session.commit()


def _record_child_attempt(parent_id: str, label: str, child, attempt: int, max_attempts: int, status: str, *, error: Optional[str] = None) -> None:
    summary = _task_public_summary(child) if child else {}
    updated_at = datetime.utcnow().isoformat() + "Z"
    summary.update({
        "attempt": int(attempt or 1),
        "max_attempts": int(max_attempts or 1),
        "attempt_status": status,
        "error": error or summary.get("error"),
        "updated_at": updated_at,
    })
    if _is_postgresql_session():
        db.session.execute(
            text("""
                UPDATE public.publication_tasks
                SET result = jsonb_set(
                        jsonb_set(
                            COALESCE(result, '{}'::jsonb),
                            '{child_attempts}',
                            CASE
                                WHEN jsonb_typeof(result->'child_attempts') = 'object'
                                THEN result->'child_attempts'
                                ELSE '{}'::jsonb
                            END,
                            true
                        ),
                        ARRAY['child_attempts', CAST(:label AS text)],
                        (
                            SELECT COALESCE(
                                jsonb_agg(retained.item ORDER BY retained.ordinality),
                                '[]'::jsonb
                            ) || CAST(:entry AS jsonb)
                            FROM (
                                SELECT existing.item, existing.ordinality
                                FROM jsonb_array_elements(
                                    CASE
                                        WHEN jsonb_typeof(
                                            result #> ARRAY['child_attempts', CAST(:label AS text)]
                                        ) = 'array'
                                        THEN result #> ARRAY['child_attempts', CAST(:label AS text)]
                                        ELSE '[]'::jsonb
                                    END
                                ) WITH ORDINALITY AS existing(item, ordinality)
                                WHERE existing.item->>'attempt' IS DISTINCT FROM :attempt_text
                                ORDER BY existing.ordinality DESC
                                LIMIT :retain_count
                            ) AS retained
                        ),
                        true
                    ),
                    updated_at = NOW()
                WHERE id = :task_id AND status IS DISTINCT FROM 'canceled'
            """),
            {
                "task_id": str(parent_id),
                "label": str(label),
                "attempt_text": str(int(attempt or 1)),
                "retain_count": max(0, int(max_attempts or 1) - 1),
                "entry": json.dumps([summary], ensure_ascii=False),
            },
        )
        db.session.commit()
        return
    parent = _load_task(parent_id, *TASK_STATE_COLUMNS)
    if not parent:
        return
    result = dict(parent.result or {})
    attempts = dict(result.get("child_attempts") or {})
    items = list(attempts.get(label) or [])
    items = [item for item in items if int(item.get("attempt") or 0) != int(attempt or 1)]
    items.append(summary)
    attempts[label] = items[-int(max_attempts or 1):]
    result["child_attempts"] = attempts
    parent.result = result
    flag_modified(parent, "result")
    db.session.commit()


def _task_public_summary(task) -> Optional[Dict[str, Any]]:
    if not task:
        return None
    task = _task_snapshot(task)
    return {
        "job_id": task.id,
        "celery_id": task.celery_task_id,
        "status": task.status,
        "step": task.current_step,
        "progress": task.progress or 0,
        "feature": task.feature,
        "error": task.error_message,
        "metadata": _child_metadata(task),
        "created_at": task.created_at.isoformat() + "Z" if task.created_at else None,
        "completed_at": task.completed_at.isoformat() + "Z" if task.completed_at else None,
    }


def _merge_child_summary(previous: Dict[str, Any], summary: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(previous or {})
    merged.update(summary or {})
    if previous.get("metadata") and not merged.get("metadata"):
        merged["metadata"] = previous.get("metadata")
    return merged


def _child_metadata(child) -> Dict[str, Any]:
    result = child.result if isinstance(child.result, dict) else {}
    metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else None
    if metadata is not None:
        return metadata
    params = child.params if isinstance(child.params, dict) else {}
    payload = params.get("report_pdf_gemini") if isinstance(params.get("report_pdf_gemini"), dict) else {}
    return payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}


def _is_postgresql_session() -> bool:
    try:
        return db.session.get_bind().dialect.name == "postgresql"
    except Exception:
        return False


def _update_result(parent_id: str, values: Dict[str, Any]) -> None:
    if not isinstance(values, dict) or not values:
        return
    if _is_postgresql_session():
        db.session.execute(
            text("""
                UPDATE public.publication_tasks
                SET result = COALESCE(result, '{}'::jsonb) || CAST(:patch AS jsonb),
                    updated_at = NOW()
                WHERE id = :task_id AND status IS DISTINCT FROM 'canceled'
            """),
            {
                "task_id": str(parent_id),
                "patch": json.dumps(values, ensure_ascii=False),
            },
        )
        db.session.commit()
        return
    task = _load_task(parent_id, *TASK_STATE_COLUMNS)
    if not task:
        return
    result = dict(task.result or {})
    result.update(values)
    task.result = result
    flag_modified(task, "result")
    db.session.commit()


def _normalize_for_tts_with_retries(
    parent_id: str,
    raw_text: str,
    *,
    initial_text: str,
    metadata: Dict[str, Any],
    normalizer_timeout: int,
) -> Dict[str, Any]:
    max_attempts = _normalizer_retry_limit() + 1
    retry_delay = _normalizer_retry_delay_seconds()
    last_error = ""

    for attempt in range(1, max_attempts + 1):
        if attempt > 1:
            _emit(
                parent_id,
                "normalize_retry",
                58,
                f"结构化器第 {attempt}/{max_attempts} 次重试，等待 {retry_delay}s 后继续",
            )
            time.sleep(retry_delay)
        else:
            _emit(parent_id, "normalize_result", 58, "使用结构化解析器整理 Gemini 结果")

        _record_normalizer_attempt(parent_id, attempt, max_attempts, "running")
        release_db_session_quietly()
        try:
            structured = normalize_for_tts(
                raw_text,
                initial_text=initial_text,
                metadata=metadata,
                normalizer_timeout=normalizer_timeout,
            )
        except Exception as exc:
            last_error = str(exc)
            _record_normalizer_attempt(parent_id, attempt, max_attempts, "failed", error=last_error)
            if attempt < max_attempts and _is_retryable_normalizer_error(last_error):
                logger.warning(
                    "Report script material normalizer retrying: task=%s attempt=%s/%s error=%s",
                    parent_id,
                    attempt,
                    max_attempts,
                    last_error,
                )
                continue
            raise

        _record_normalizer_attempt(parent_id, attempt, max_attempts, "completed")
        return structured

    raise RuntimeError(f"结构化器不可用或返回不可用结果：{last_error}")


def _record_normalizer_attempt(
    parent_id: str,
    attempt: int,
    max_attempts: int,
    status: str,
    *,
    error: Optional[str] = None,
) -> None:
    updated_at = datetime.utcnow().isoformat() + "Z"
    entry = {
        "attempt": int(attempt or 1),
        "max_attempts": int(max_attempts or 1),
        "status": status,
        "error": error,
        "updated_at": updated_at,
    }
    if _is_postgresql_session():
        db.session.execute(
            text("""
                UPDATE public.publication_tasks
                SET result = jsonb_set(
                        COALESCE(result, '{}'::jsonb),
                        '{normalizer_attempts}',
                        (
                            SELECT COALESCE(
                                jsonb_agg(retained.item ORDER BY retained.ordinality),
                                '[]'::jsonb
                            ) || CAST(:entry AS jsonb)
                            FROM (
                                SELECT existing.item, existing.ordinality
                                FROM jsonb_array_elements(
                                    CASE
                                        WHEN jsonb_typeof(result->'normalizer_attempts') = 'array'
                                        THEN result->'normalizer_attempts'
                                        ELSE '[]'::jsonb
                                    END
                                ) WITH ORDINALITY AS existing(item, ordinality)
                                WHERE existing.item->>'attempt' IS DISTINCT FROM :attempt_text
                                ORDER BY existing.ordinality DESC
                                LIMIT :retain_count
                            ) AS retained
                        ),
                        true
                    ),
                    updated_at = NOW()
                WHERE id = :task_id AND status IS DISTINCT FROM 'canceled'
            """),
            {
                "task_id": str(parent_id),
                "attempt_text": str(int(attempt or 1)),
                "retain_count": max(0, int(max_attempts or 1) - 1),
                "entry": json.dumps([entry], ensure_ascii=False),
            },
        )
        db.session.commit()
        return
    parent = _load_task(parent_id, *TASK_STATE_COLUMNS)
    if not parent:
        return
    result = dict(parent.result or {})
    attempts = list(result.get("normalizer_attempts") or [])
    attempts = [item for item in attempts if int(item.get("attempt") or 0) != int(attempt or 1)]
    attempts.append(entry)
    result["normalizer_attempts"] = attempts[-int(max_attempts or 1):]
    parent.result = result
    flag_modified(parent, "result")
    db.session.commit()


def _emit(parent_id: str, step: str, progress: int, message: str) -> None:
    log_entry = {
        "step": step,
        "message": message,
        "progress": progress,
        "updated_at": datetime.utcnow().isoformat() + "Z",
    }
    step_code = _current_step_code(step)
    progress_value = max(0, min(100, int(progress or 0)))
    if _is_postgresql_session():
        db.session.execute(
            text("""
                UPDATE public.publication_tasks
                SET current_step = :step,
                    progress = CASE WHEN :progress > 0 THEN :progress ELSE progress END,
                    result = jsonb_set(
                        COALESCE(result, '{}'::jsonb),
                        '{logs}',
                        CASE
                            WHEN jsonb_array_length(COALESCE(result->'logs', '[]'::jsonb)) >= 120
                            THEN (COALESCE(result->'logs', '[]'::jsonb) #- '{0}') || CAST(:entry AS jsonb)
                            ELSE COALESCE(result->'logs', '[]'::jsonb) || CAST(:entry AS jsonb)
                        END,
                        true
                    ),
                    updated_at = NOW()
                WHERE id = :task_id AND status IS DISTINCT FROM 'canceled'
            """),
            {
                "task_id": str(parent_id),
                "step": step_code,
                "progress": progress_value,
                "entry": json.dumps([log_entry], ensure_ascii=False),
            },
        )
        db.session.commit()
        return
    task = _load_task(parent_id, *TASK_STATE_COLUMNS)
    if not task:
        return
    result = dict(task.result or {})
    logs = list(result.get("logs") or [])
    logs.append(log_entry)
    result["logs"] = logs[-120:]
    task.current_step = step_code
    task.progress = max(0, min(100, int(progress or task.progress or 0)))
    task.result = result
    flag_modified(task, "result")
    db.session.commit()


def _clear_waiting_child(parent_id: str, label: str) -> None:
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


def _defer_parent_retry(parent_id: str, exc: ChildTaskStillRunning) -> None:
    delay = _parent_retry_delay_seconds()
    max_defers = _parent_max_defers()
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
    fresh.error_message = None
    fresh.result = result
    flag_modified(fresh, "result")
    db.session.commit()

    celery_app.send_task(
        "tasks.report_script_material_workflow.process_job",
        args=[parent_id],
        queue=QUEUE_NAME,
        countdown=delay,
    )


def _mark_failed_safely(task_id: str, message: str) -> None:
    try:
        db.session.rollback()
    except Exception:
        logger.exception("Failed to rollback script material workflow session before failure marker")
        try:
            db.session.remove()
        except Exception:
            logger.exception("Failed to remove script material workflow session before failure marker")
    try:
        task = _load_task(task_id, *TASK_STATE_COLUMNS)
        if task and task.status not in {"completed", "canceled"}:
            _mark_failed(task, message)
    except Exception:
        logger.exception("Failed to mark script material workflow failed: %s", task_id)
        try:
            db.session.rollback()
        except Exception:
            pass


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
        operation=f"report script material child revoke child={child_id}",
        business_task_id=child_id,
        log=logger,
    )


def _celery_child_failure_message(child: PublicationTask) -> Optional[str]:
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
    try:
        detail = str(async_result.result) if async_result.result else ""
    except Exception as exc:
        detail = str(exc)
    return f"Celery 子任务状态 {state}: {detail}".strip()


def _load_task(task_id: str, *columns) -> Optional[PublicationTask]:
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
        .filter(PublicationTask.id == task_id)
        .one_or_none()
    )


def _persist_child_celery_task_id(child_id: str, celery_task_id: str) -> None:
    (
        PublicationTask.query
        .filter(PublicationTask.id == child_id)
        .update(
            {
                "celery_task_id": celery_task_id,
                "updated_at": datetime.utcnow(),
            },
            synchronize_session=False,
        )
    )
    db.session.commit()


def _load_task_results(task_ids) -> Dict[str, PublicationTask]:
    ids = [str(task_id) for task_id in dict.fromkeys(task_ids or []) if task_id]
    if not ids:
        return {}
    rows = (
        PublicationTask.query.options(load_only(PublicationTask.id, PublicationTask.result))
        .filter(PublicationTask.id.in_(ids))
        .all()
    )
    return {row.id: row for row in rows}


def _load_task_snapshot(task_id: str, *, include_payload: bool = True):
    columns = TASK_CHILD_COLUMNS if include_payload else TASK_CHILD_POLL_COLUMNS
    return _task_snapshot(_load_task(task_id, *columns), include_payload=include_payload)


def _task_snapshot(task, *, include_payload: bool = True):
    if not task:
        return None
    if isinstance(task, SimpleNamespace):
        return task
    result = _loaded_task_attr(task, "result", {}) if include_payload else {}
    params = _loaded_task_attr(task, "params", {}) if include_payload else {}
    return SimpleNamespace(
        id=_loaded_task_attr(task, "id"),
        celery_task_id=_loaded_task_attr(task, "celery_task_id"),
        status=_loaded_task_attr(task, "status"),
        current_step=_loaded_task_attr(task, "current_step"),
        progress=_loaded_task_attr(task, "progress"),
        result=result if isinstance(result, dict) else {},
        params=params if isinstance(params, dict) else {},
        feature=_loaded_task_attr(task, "feature"),
        parent_task_id=_loaded_task_attr(task, "parent_task_id"),
        source_task_id=_loaded_task_attr(task, "source_task_id"),
        error_message=_loaded_task_attr(task, "error_message"),
        created_at=_loaded_task_attr(task, "created_at"),
        updated_at=_loaded_task_attr(task, "updated_at"),
        completed_at=_loaded_task_attr(task, "completed_at"),
    )


def _loaded_task_attr(task, name: str, default=None):
    try:
        state = sqlalchemy_inspect(task)
        if name in state.unloaded:
            return default
    except Exception:
        pass
    try:
        return getattr(task, name, default)
    except Exception:
        return default


def _child_error_message(child: Optional[PublicationTask]) -> str:
    if not child:
        return "子任务不存在"
    result = child.result if isinstance(child.result, dict) else {}
    return child.error_message or result.get("error") or child.current_step or child.status or "子任务失败"


def _is_retryable_child_error(message: str) -> bool:
    text = str(message or "")
    if not text:
        return False
    lower_text = text.lower()
    if any(fragment.lower() in lower_text for fragment in NON_RETRYABLE_CHILD_ERROR_FRAGMENTS):
        return False
    return any(fragment.lower() in lower_text for fragment in RETRYABLE_CHILD_ERROR_FRAGMENTS)


def _current_step_code(step: str) -> str:
    raw = re.sub(r"\s+", "_", str(step or "running")).strip("_") or "running"
    if len(raw) <= CURRENT_STEP_MAX_LENGTH:
        return raw
    return raw[:CURRENT_STEP_MAX_LENGTH]


def _retry_limit() -> int:
    return max(0, min(_int_env(GEMINI_RETRY_LIMIT_ENV_NAME, DEFAULT_GEMINI_RETRY_LIMIT), 5))


def _child_inline_wait_seconds() -> int:
    return max(1, _int_env("REPORT_SCRIPT_MATERIAL_CHILD_TIMEOUT_SECONDS", DEFAULT_CHILD_TIMEOUT_SECONDS))


def _parent_retry_delay_seconds() -> int:
    return max(30, _int_env("REPORT_SCRIPT_MATERIAL_PARENT_RETRY_DELAY_SECONDS", DEFAULT_PARENT_RETRY_DELAY_SECONDS))


def _parent_max_defers() -> int:
    return max(1, _int_env("REPORT_SCRIPT_MATERIAL_PARENT_MAX_DEFERS", DEFAULT_PARENT_MAX_DEFERS))


def _normalizer_timeout_seconds() -> int:
    return max(1, min(_int_env(
        "REPORT_SCRIPT_MATERIAL_NORMALIZER_TIMEOUT_SECONDS",
        DEFAULT_NORMALIZER_TIMEOUT_SECONDS,
    ), 120))


def _normalizer_retry_limit() -> int:
    return max(0, min(_int_env(
        "REPORT_SCRIPT_MATERIAL_NORMALIZER_RETRY_LIMIT",
        DEFAULT_NORMALIZER_RETRY_LIMIT,
    ), 5))


def _normalizer_retry_delay_seconds() -> int:
    return max(1, min(_int_env(
        "REPORT_SCRIPT_MATERIAL_NORMALIZER_RETRY_DELAY_SECONDS",
        DEFAULT_NORMALIZER_RETRY_DELAY_SECONDS,
    ), 300))


def _is_retryable_normalizer_error(message: str) -> bool:
    text = str(message or "").lower()
    retryable_fragments = (
        "timeout",
        "timed out",
        "read timed out",
        "connect timeout",
        "connection aborted",
        "connection reset",
        "connection pool",
        "remote end closed",
        "temporarily unavailable",
        "too many requests",
        "rate limit",
        "429",
        "500",
        "502",
        "503",
        "504",
    )
    return any(fragment in text for fragment in retryable_fragments)


def _plain_text(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or ""))


def _min_gemini_final_chars() -> int:
    """Keep Gemini cache eligibility aligned with report-audio workflow."""
    return max(0, _int_env(
        "REPORT_AUDIO_GEMINI_MIN_FINAL_CHARS",
        _int_env("REPORT_PDF_GEMINI_MIN_RESPONSE_CHARS", 120),
    ))


def _min_final_chars() -> int:
    return max(0, _int_env(
        "REPORT_SCRIPT_MATERIAL_MIN_FINAL_CHARS",
        _int_env("REPORT_AUDIO_GEMINI_MIN_FINAL_CHARS", _int_env("REPORT_PDF_GEMINI_MIN_RESPONSE_CHARS", 120)),
    ))


def _truthy(value: Any, default: bool = False) -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _compact_error(message: str, limit: int = 180) -> str:
    text = re.sub(r"\s+", " ", str(message or "")).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."
