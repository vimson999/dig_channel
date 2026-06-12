import logging
import os
import sys
from datetime import datetime

from sqlalchemy.orm.attributes import flag_modified

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from celery_config import celery_app
from models import db
from models.task import PublicationTask
from services.content_pipeline.pdf_cleaner_adapter import (
    PdfCleanOptions,
    PdfCleanerAdapter,
)


logger = logging.getLogger(__name__)


@celery_app.task(bind=True, name="tasks.content_pipeline.run_pdf_clean_task")
def run_pdf_clean_task(self, task_id):
    from app import create_app

    app = create_app()
    with app.app_context():
        task = PublicationTask.query.get(task_id)
        if not task:
            logger.error("PDF clean task not found: %s", task_id)
            return False

        params = task.params or {}
        pipeline = params.get("content_pipeline") or {}
        pdf_clean = pipeline.get("pdf_clean") or {}
        cleaner_config = pdf_clean.get("cleaner") or {}

        try:
            task.status = "processing"
            task.current_step = "prepare"
            task.progress = 3
            db.session.commit()

            options = PdfCleanOptions(
                threshold=int(cleaner_config.get("threshold") or 245),
                do_ocr=bool(cleaner_config.get("do_ocr", False)),
                overwrite=bool(cleaner_config.get("overwrite", False)),
                prefix=cleaner_config.get("prefix") or "",
                poll_interval=float(cleaner_config.get("poll_interval") or os.getenv("PDF_CLEANER_POLL_INTERVAL", 1.5)),
                timeout_seconds=int(cleaner_config.get("timeout_seconds") or os.getenv("PDF_CLEANER_TIMEOUT_SECONDS", 3600)),
            )
            adapter = PdfCleanerAdapter(
                base_url=cleaner_config.get("base_url"),
                cleaner_project_dir=cleaner_config.get("cleaner_project_dir"),
                cleaner_batch_root=cleaner_config.get("cleaner_batch_root"),
                output_root=cleaner_config.get("output_root"),
            )

            files = pdf_clean.get("files") or []
            source_root = pdf_clean.get("source_root") or None

            def update_progress(event):
                job = event.get("job") or {}
                task.current_step = event.get("phase") or "running"
                task.progress = int(event.get("progress") or task.progress or 0)
                result = dict(task.result or {})
                result["cleaner_job_id"] = event.get("job_id") or result.get("cleaner_job_id")
                result["cleaner_job"] = job or result.get("cleaner_job")
                result["message"] = job.get("message") or result.get("message")
                task.result = result
                flag_modified(task, "result")
                db.session.commit()

            result = adapter.run_batch(
                batch_id=task.id,
                files=files,
                source_root=source_root,
                options=options,
                progress_callback=update_progress,
                reset_workspace=True,
            )

            failed_count = int(result.get("failed_count") or 0)
            cleaned_files = result.get("cleaned_files") or []
            if failed_count > 0:
                raise RuntimeError(f"PDF 清洗存在失败文件: {failed_count} 个")
            if not cleaned_files:
                raise RuntimeError("PDF 清洗完成但没有拿到输出文件")

            task.status = "completed"
            task.current_step = "done"
            task.progress = 100
            task.completed_at = datetime.utcnow()
            task.result = result
            flag_modified(task, "result")
            db.session.commit()
            logger.info("PDF clean task completed: %s files=%s", task_id, len(cleaned_files))
            return True
        except Exception as exc:
            db.session.rollback()
            task = PublicationTask.query.get(task_id)
            if task:
                result = dict(task.result or {})
                result["error"] = str(exc)
                task.status = "failed"
                task.current_step = "failed"
                task.progress = task.progress or 0
                task.error_message = str(exc)
                task.completed_at = datetime.utcnow()
                task.result = result
                flag_modified(task, "result")
                db.session.commit()
            logger.error("PDF clean task failed: %s", exc, exc_info=True)
            return False

