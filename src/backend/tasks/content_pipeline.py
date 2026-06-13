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
from services.content_pipeline.audio_cleaner_adapter import (
    AudioCleanOptions,
    AudioCleanerAdapter,
)
from services.content_pipeline.auto_publish_handoff import submit_publish_clean_output_task


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
                engine=cleaner_config.get("engine") or os.getenv("PDF_CLEANER_ENGINE", "external"),
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

            result = _enqueue_auto_publish_task(
                task=task,
                file_type="pdf",
                result=result,
                options=pipeline.get("auto_publish") or {},
                pipeline=pipeline,
            )

            task.status = "completed"
            task.current_step = "done"
            task.progress = 100
            task.completed_at = datetime.utcnow()
            task.result = result
            flag_modified(task, "result")
            db.session.commit()
            logger.info("PDF clean task completed: %s files=%s", task_id, len(cleaned_files))
            _notify_workflow_from_pipeline_task(task)
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
                _notify_workflow_from_pipeline_task(task)
            logger.error("PDF clean task failed: %s", exc, exc_info=True)
            return False


@celery_app.task(bind=True, name="tasks.content_pipeline.run_audio_clean_task")
def run_audio_clean_task(self, task_id):
    from app import create_app

    app = create_app()
    with app.app_context():
        task = PublicationTask.query.get(task_id)
        if not task:
            logger.error("Audio clean task not found: %s", task_id)
            return False

        params = task.params or {}
        pipeline = params.get("content_pipeline") or {}
        audio_clean = pipeline.get("audio_clean") or {}
        cleaner_config = audio_clean.get("cleaner") or {}

        try:
            task.status = "processing"
            task.current_step = "prepare"
            task.progress = 3
            db.session.commit()

            options = AudioCleanOptions(
                head_trim_seconds=float(cleaner_config.get("head_trim_seconds") or 11),
                tail_trim_seconds=float(cleaner_config.get("tail_trim_seconds") or 90),
                gap_audio_path=cleaner_config.get("gap_audio_path") or "",
                bitrate=cleaner_config.get("bitrate") or "128k",
                sample_rate=int(cleaner_config.get("sample_rate") or 44100),
                channels=int(cleaner_config.get("channels") or 2),
                fast_copy=str(cleaner_config.get("fast_copy", True)).lower() not in {"0", "false", "no", "off"},
            )
            adapter = AudioCleanerAdapter(output_root=cleaner_config.get("output_root"))

            files = audio_clean.get("files") or []
            source_root = audio_clean.get("source_root") or None

            def update_progress(event):
                task.current_step = event.get("phase") or "running"
                task.progress = int(event.get("progress") or task.progress or 0)
                result = dict(task.result or {})
                result["message"] = event.get("message") or result.get("message")
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
                raise RuntimeError(f"音频清洗存在失败文件: {failed_count} 个")
            if not cleaned_files:
                raise RuntimeError("音频清洗完成但没有拿到输出文件")

            result = _enqueue_auto_publish_task(
                task=task,
                file_type="audio",
                result=result,
                options=pipeline.get("auto_publish") or {},
                pipeline=pipeline,
            )

            task.status = "completed"
            task.current_step = "done"
            task.progress = 100
            task.completed_at = datetime.utcnow()
            task.result = result
            flag_modified(task, "result")
            db.session.commit()
            logger.info("Audio clean task completed: %s files=%s", task_id, len(cleaned_files))
            _notify_workflow_from_pipeline_task(task)
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
                _notify_workflow_from_pipeline_task(task)
            logger.error("Audio clean task failed: %s", exc, exc_info=True)
            return False


def _enqueue_auto_publish_task(*, task, file_type, result, options, pipeline):
    if not isinstance(options, dict) or not options.get("enabled"):
        return result

    try:
        task.current_step = "enqueue_publish"
        task.progress = max(int(task.progress or 0), 92)
        interim = dict(task.result or {})
        interim["message"] = "清洗完成，正在提交独立发布任务。"
        task.result = interim
        flag_modified(task, "result")
        db.session.commit()

        publish_task = submit_publish_clean_output_task(
            title=f"知识星球清洗产物发布 - {task.id[:8]}",
            file_type=file_type,
            output_dir=result.get("local_output_dir"),
            options=options,
            source_task_id=task.id,
            trigger=options.get("trigger") or "content_pipeline_clean_complete",
            metadata={
                "content_pipeline_task_id": task.id,
                "content_pipeline_title": (pipeline or {}).get("title") or "",
                "source_metadata": (pipeline or {}).get("metadata") or {},
            },
        )
        publish_result = {
            "enabled": True,
            "queued": True,
            "task_id": publish_task.get("id"),
            "celery_task_id": publish_task.get("celery_task_id"),
            "status": publish_task.get("status"),
            "message": "独立发布任务已提交。",
        }
    except Exception as exc:
        logger.exception("Clean output publish enqueue failed: task=%s type=%s", task.id, file_type)
        publish_result = {
            "enabled": True,
            "queued": False,
            "status": "failed",
            "reason": "exception",
            "message": str(exc),
        }

    merged = dict(result or {})
    merged["auto_publish"] = publish_result
    return merged


def _notify_workflow_from_pipeline_task(task):
    try:
        pipeline = (task.params or {}).get("content_pipeline") or {}
        metadata = pipeline.get("metadata") if isinstance(pipeline.get("metadata"), dict) else {}
        workflow_run_id = metadata.get("workflow_run_id")
        if not workflow_run_id:
            return

        from tasks.zsxq_workflow import advance_zsxq_workflow

        advance_zsxq_workflow.apply_async(
            args=[workflow_run_id, "clean_task_finished"],
            queue="zsxq_workflow",
            retry=False,
        )
    except Exception:
        logger.exception("[CONTENT_PIPELINE] workflow notify failed | task=%s", getattr(task, "id", "-"))
