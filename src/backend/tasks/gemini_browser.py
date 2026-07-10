import logging
from datetime import datetime

from sqlalchemy.orm import load_only
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.exc import OperationalError

from app import create_app
from celery_config import celery_app
from models import db
from models.task import PublicationTask
from services.gemini_browser import (
    FEATURE_NAME,
    LEGACY_FEATURE_NAME,
    GeminiBrowserWorker,
    storage_path_for_job,
)


logger = logging.getLogger("REPORT_PDF_GEMINI_TASK")

TASK_UPDATE_COLUMNS = (
    PublicationTask.id,
    PublicationTask.status,
    PublicationTask.current_step,
    PublicationTask.progress,
    PublicationTask.result,
    PublicationTask.error_message,
    PublicationTask.completed_at,
)

TASK_PAYLOAD_COLUMNS = (
    *TASK_UPDATE_COLUMNS,
    PublicationTask.params,
    PublicationTask.feature,
    PublicationTask.created_at,
)


def _load_task(task_id, *columns):
    attrs = columns or TASK_PAYLOAD_COLUMNS
    return (
        PublicationTask.query.options(load_only(*attrs))
        .filter(PublicationTask.id == str(task_id))
        .first()
    )


def _update_active_task(task_id, values):
    updated = (
        PublicationTask.query
        .filter(PublicationTask.id == str(task_id))
        .filter(PublicationTask.status != "canceled")
        .update(values, synchronize_session=False)
    )
    db.session.commit()
    return bool(updated)


def _run_with_app_context(task_id):
    app = create_app()
    with app.app_context():
        return _process_job(task_id)


@celery_app.task(
    bind=True,
    name="tasks.report_pdf_gemini.process_job",
    store_errors_even_if_ignored=True,
    autoretry_for=(OperationalError,),
    retry_backoff=True,
    retry_jitter=True,
    retry_kwargs={"max_retries": 3},
)
def process_report_pdf_gemini_job(self, task_id):
    return _run_with_app_context(task_id)


@celery_app.task(
    bind=True,
    name="tasks.gemini_browser.process_job",
    store_errors_even_if_ignored=True,
    autoretry_for=(OperationalError,),
    retry_backoff=True,
    retry_jitter=True,
    retry_kwargs={"max_retries": 3},
)
def process_gemini_browser_job(self, task_id):
    return _run_with_app_context(task_id)


def _process_job(task_id):
    task = _load_task(task_id, *TASK_PAYLOAD_COLUMNS)
    if not task:
        logger.error("Report PDF Gemini task not found: %s", task_id)
        return False
    if not isinstance(task.params, dict) or (task.feature or task.params.get("feature")) not in {FEATURE_NAME, LEGACY_FEATURE_NAME}:
        logger.error("Invalid report PDF Gemini task params: %s", task_id)
        return False
    if task.status == "canceled":
        return False

    job_id = task.id
    job_created_at = task.created_at or datetime.utcnow()
    payload = task.params.get("report_pdf_gemini") or task.params.get("gemini_browser") or {}
    prompt = (payload.get("prompt") or "").strip()
    followups = [str(item).strip() for item in (payload.get("followups") or []) if str(item).strip()]
    target_url = (payload.get("url") or "").strip() or None
    pdf_path = (payload.get("pdf_path") or "").strip() or None
    if not prompt and not pdf_path:
        _mark_failed(task, "Prompt 和 PDF 不能同时为空")
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
    db.session.expunge_all()
    progress_result = result

    def update_progress(event):
        nonlocal progress_result
        current_result = dict(progress_result)
        logs = list(progress_result.get("logs") or [])
        logs.append({
            "step": event.get("step"),
            "message": event.get("message"),
            "progress": event.get("progress"),
            "updated_at": event.get("updated_at"),
        })
        current_result["logs"] = logs[-80:]
        if event.get("result"):
            current_result.update(event["result"])
        progress_result = current_result
        if not _update_active_task(job_id, {
            PublicationTask.current_step: event.get("step") or "processing",
            PublicationTask.progress: int(event.get("progress") or 0),
            PublicationTask.result: current_result,
        }):
            raise RuntimeError("任务已取消或不存在")

    try:
        output_path = storage_path_for_job(job_id, job_created_at)
        worker = GeminiBrowserWorker()
        gemini_result = worker.generate(
            job_id=job_id,
            prompt=prompt,
            output_path=output_path,
            pdf_path=pdf_path,
            target_url=target_url,
            followup_prompts=followups,
            progress_callback=update_progress,
        )

        final_result = dict(progress_result)
        final_result.update({
            "response_text": gemini_result.get("response_text"),
            "final_response_text": gemini_result.get("final_response_text") or gemini_result.get("response_text"),
            "initial_response_text": gemini_result.get("initial_response_text"),
            "turns": gemini_result.get("turns") or [],
            "followup_prompts": gemini_result.get("followup_prompts") or followups,
            "pdf": gemini_result.get("pdf"),
            "page_url": gemini_result.get("page_url"),
            "target_url": gemini_result.get("target_url"),
            "output_path": gemini_result.get("output_path"),
            "completed_at": datetime.utcnow().isoformat() + "Z",
        })
        final_response = final_result.get("final_response_text") or final_result.get("response_text") or ""
        if not _update_active_task(job_id, {
            PublicationTask.status: "completed",
            PublicationTask.current_step: "done",
            PublicationTask.progress: 100,
            PublicationTask.completed_at: datetime.utcnow(),
            PublicationTask.error_message: None,
            PublicationTask.result: final_result,
            PublicationTask.gemini_response_chars: len("".join(str(final_response).split())),
        }):
            logger.warning("Report PDF Gemini task was canceled before final update: %s", job_id)
            return False
        logger.info("Report PDF Gemini task completed: %s", job_id)
        return True
    except Exception as exc:
        db.session.rollback()
        failure_result = dict(progress_result)
        failure_result["error"] = str(exc)
        failure_result["completed_at"] = datetime.utcnow().isoformat() + "Z"
        _update_active_task(job_id, {
            PublicationTask.status: "failed",
            PublicationTask.current_step: "failed",
            PublicationTask.error_message: str(exc),
            PublicationTask.completed_at: datetime.utcnow(),
            PublicationTask.result: failure_result,
            PublicationTask.gemini_response_chars: 0,
        })
        logger.error("Report PDF Gemini task failed: %s", exc, exc_info=True)
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
