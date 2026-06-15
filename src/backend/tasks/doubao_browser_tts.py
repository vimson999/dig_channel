import logging
from datetime import datetime

from sqlalchemy.orm.attributes import flag_modified

from app import create_app
from celery_config import celery_app
from models import db
from models.task import PublicationTask
from services.doubao_browser_tts import (
    FEATURE_NAME,
    DoubaoBrowserTTSWorker,
    storage_path_for_job,
)


logger = logging.getLogger("DOUBAO_BROWSER_TTS_TASK")


@celery_app.task(bind=True, name="tasks.doubao_browser_tts.process_job")
def process_doubao_browser_tts_job(self, task_id):
    app = create_app()
    with app.app_context():
        return _process_job(task_id)


def _process_job(task_id):
    task = PublicationTask.query.get(task_id)
    if not task:
        logger.error("Doubao browser TTS task not found: %s", task_id)
        return False
    if not isinstance(task.params, dict) or task.params.get("feature") != FEATURE_NAME:
        logger.error("Invalid Doubao browser TTS task params: %s", task_id)
        return False
    if task.status == "canceled":
        return False

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
        worker = DoubaoBrowserTTSWorker()
        audio = worker.synthesize(
            job_id=task.id,
            text=text,
            output_path=output_path,
            progress_callback=update_progress,
        )

        final_result = dict(task.result or {})
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
        task.status = "completed"
        task.current_step = "done"
        task.progress = 100
        task.completed_at = datetime.utcnow()
        task.error_message = None
        task.result = final_result
        flag_modified(task, "result")
        db.session.commit()
        logger.info("Doubao browser TTS task completed: %s", task_id)
        return True
    except Exception as exc:
        db.session.rollback()
        task = PublicationTask.query.get(task_id)
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
