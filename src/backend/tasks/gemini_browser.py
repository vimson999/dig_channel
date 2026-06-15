import logging
from datetime import datetime

from sqlalchemy.orm.attributes import flag_modified

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


def _run_with_app_context(task_id):
    app = create_app()
    with app.app_context():
        return _process_job(task_id)


@celery_app.task(bind=True, name="tasks.report_pdf_gemini.process_job")
def process_report_pdf_gemini_job(self, task_id):
    return _run_with_app_context(task_id)


@celery_app.task(bind=True, name="tasks.gemini_browser.process_job")
def process_gemini_browser_job(self, task_id):
    return _run_with_app_context(task_id)


def _process_job(task_id):
    task = PublicationTask.query.get(task_id)
    if not task:
        logger.error("Report PDF Gemini task not found: %s", task_id)
        return False
    if not isinstance(task.params, dict) or task.params.get("feature") not in {FEATURE_NAME, LEGACY_FEATURE_NAME}:
        logger.error("Invalid report PDF Gemini task params: %s", task_id)
        return False
    if task.status == "canceled":
        return False

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

    def update_progress(event):
        fresh = PublicationTask.query.get(task_id)
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
        output_path = storage_path_for_job(task.id, task.created_at)
        worker = GeminiBrowserWorker()
        gemini_result = worker.generate(
            job_id=task.id,
            prompt=prompt,
            output_path=output_path,
            pdf_path=pdf_path,
            target_url=target_url,
            followup_prompts=followups,
            progress_callback=update_progress,
        )

        final_result = dict(task.result or {})
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
        task.status = "completed"
        task.current_step = "done"
        task.progress = 100
        task.completed_at = datetime.utcnow()
        task.error_message = None
        task.result = final_result
        flag_modified(task, "result")
        db.session.commit()
        logger.info("Report PDF Gemini task completed: %s", task_id)
        return True
    except Exception as exc:
        db.session.rollback()
        task = PublicationTask.query.get(task_id)
        if task:
            _mark_failed(task, str(exc))
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
