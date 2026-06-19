import logging
import os
import sys
from datetime import datetime

from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.orm import load_only
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

DEFAULT_DB_RETRIES = 5
DEFAULT_DB_RETRY_DELAY_SECONDS = 60
DB_RETRIES_ENV_NAME = "CONTENT_PIPELINE_DB_RETRIES"
DB_RETRY_DELAY_ENV_NAME = "CONTENT_PIPELINE_DB_RETRY_DELAY_SECONDS"

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
    PublicationTask.user_id,
    PublicationTask.created_at,
)


def _load_task(task_id, *columns):
    attrs = columns or TASK_PAYLOAD_COLUMNS
    return PublicationTask.query.options(load_only(*attrs)).get(task_id)


def _create_content_pipeline_app():
    """Create an app context for worker tasks without depending on API blueprints."""
    from app import create_app

    try:
        return create_app()
    except Exception:
        logger.exception("Full Flask app initialization failed; falling back to DB-only task app.")

    from flask import Flask
    from config import Config

    app = Flask(__name__)
    app.config.from_object(Config)
    db.init_app(app)
    return app


@celery_app.task(bind=True, name="tasks.content_pipeline.run_pdf_clean_task")
def run_pdf_clean_task(self, task_id):
    return _run_content_pipeline_task_with_guard(
        self,
        task_id,
        "PDF 清洗",
        _run_pdf_clean_task_impl,
    )


def _run_pdf_clean_task_impl(task_id):
    app = _create_content_pipeline_app()
    with app.app_context():
        task = _load_task(task_id, *TASK_PAYLOAD_COLUMNS)
        if not task:
            logger.error("PDF clean task not found: %s", task_id)
            return False

        params = task.params or {}
        pipeline = params.get("content_pipeline") or {}
        workflow_run_id = _workflow_run_id_from_pipeline(pipeline)
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
                engine=cleaner_config.get("engine") or os.getenv("PDF_CLEANER_ENGINE") or "local",
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
                fresh = _load_task(task_id, *TASK_UPDATE_COLUMNS)
                if not fresh:
                    raise RuntimeError(f"PDF clean task not found during progress update: {task_id}")
                job = event.get("job") or {}
                fresh.current_step = event.get("phase") or "running"
                fresh.progress = int(event.get("progress") or fresh.progress or 0)
                result = dict(fresh.result or {})
                result["cleaner_job_id"] = event.get("job_id") or result.get("cleaner_job_id")
                result["cleaner_job"] = job or result.get("cleaner_job")
                result["message"] = job.get("message") or result.get("message")
                fresh.result = result
                flag_modified(fresh, "result")
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

            task = _load_task(task_id, *TASK_UPDATE_COLUMNS)
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
            _notify_workflow_run(workflow_run_id, task_id)
            return True
        except Exception as exc:
            db.session.rollback()
            task = _load_task(task_id, *TASK_UPDATE_COLUMNS)
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
                _notify_workflow_run(workflow_run_id, task_id)
            logger.error("PDF clean task failed: %s", exc, exc_info=True)
            return False


@celery_app.task(bind=True, name="tasks.content_pipeline.run_audio_clean_task")
def run_audio_clean_task(self, task_id):
    return _run_content_pipeline_task_with_guard(
        self,
        task_id,
        "音频清洗",
        _run_audio_clean_task_impl,
    )


def _run_audio_clean_task_impl(task_id):
    app = _create_content_pipeline_app()
    with app.app_context():
        task = _load_task(task_id, *TASK_PAYLOAD_COLUMNS)
        if not task:
            logger.error("Audio clean task not found: %s", task_id)
            return False

        params = task.params or {}
        pipeline = params.get("content_pipeline") or {}
        workflow_run_id = _workflow_run_id_from_pipeline(pipeline)
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
                fresh = _load_task(task_id, *TASK_UPDATE_COLUMNS)
                if not fresh:
                    raise RuntimeError(f"Audio clean task not found during progress update: {task_id}")
                fresh.current_step = event.get("phase") or "running"
                fresh.progress = int(event.get("progress") or fresh.progress or 0)
                result = dict(fresh.result or {})
                result["message"] = event.get("message") or result.get("message")
                fresh.result = result
                flag_modified(fresh, "result")
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

            task = _load_task(task_id, *TASK_UPDATE_COLUMNS)
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
            _notify_workflow_run(workflow_run_id, task_id)
            return True
        except Exception as exc:
            db.session.rollback()
            task = _load_task(task_id, *TASK_UPDATE_COLUMNS)
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
                _notify_workflow_run(workflow_run_id, task_id)
            logger.error("Audio clean task failed: %s", exc, exc_info=True)
            return False


def _run_content_pipeline_task_with_guard(celery_task, task_id, task_label, runner):
    try:
        return runner(task_id)
    except Exception as exc:
        if _is_retryable_db_error(exc) and celery_task.request.retries < _db_retry_count():
            countdown = _db_retry_delay_seconds(celery_task.request.retries)
            logger.warning(
                "[CONTENT_PIPELINE] retry transient db error | task=%s label=%s retry=%s/%s countdown=%ss error=%s",
                task_id,
                task_label,
                celery_task.request.retries + 1,
                _db_retry_count(),
                countdown,
                exc,
            )
            raise celery_task.retry(exc=exc, countdown=countdown, max_retries=_db_retry_count())

        details = _mark_clean_task_failed_safely(task_id, exc)
        _send_direct_clean_failure_email(
            task_id=task_id,
            task_label=task_label,
            error=exc,
            workflow_run_id=details.get("workflow_run_id"),
            persisted=details.get("persisted"),
            workflow_notified=details.get("workflow_notified"),
            persist_error=details.get("persist_error"),
        )
        logger.error(
            "[CONTENT_PIPELINE] task failed outside normal handler | task=%s label=%s persisted=%s workflow_notified=%s error=%s",
            task_id,
            task_label,
            details.get("persisted"),
            details.get("workflow_notified"),
            exc,
            exc_info=True,
        )
        return False


def _mark_clean_task_failed_safely(task_id, exc):
    details = {
        "persisted": False,
        "workflow_notified": False,
        "workflow_run_id": None,
        "persist_error": None,
    }
    try:
        app = _create_content_pipeline_app()
        with app.app_context():
            db.session.rollback()
            task = _load_task(task_id, *TASK_UPDATE_COLUMNS, PublicationTask.params)
            if not task:
                details["persist_error"] = f"任务不存在: {task_id}"
                return details

            pipeline = (task.params or {}).get("content_pipeline") or {}
            workflow_run_id = _workflow_run_id_from_pipeline(pipeline)
            details["workflow_run_id"] = workflow_run_id

            result = dict(task.result or {})
            result["error"] = str(exc)
            result["failed_outside_handler"] = True
            task.status = "failed"
            task.current_step = "failed"
            task.progress = task.progress or 0
            task.error_message = str(exc)
            task.completed_at = datetime.utcnow()
            task.result = result
            flag_modified(task, "result")
            db.session.commit()
            details["persisted"] = True
            details["workflow_notified"] = _notify_workflow_run(workflow_run_id, task_id)
    except Exception as persist_exc:
        details["persist_error"] = str(persist_exc)
        logger.exception("[CONTENT_PIPELINE] failed to persist task failure | task=%s", task_id)
    return details


def _send_direct_clean_failure_email(
    *,
    task_id,
    task_label,
    error,
    workflow_run_id=None,
    persisted=False,
    workflow_notified=False,
    persist_error=None,
):
    try:
        from services.workflow_notification_service import workflow_notification_service

        subject = f"[知识星球工作流] 清洗任务异常 · {task_label} · {str(task_id)[:8]}"
        lines = [
            "清洗任务异常退出，已触发兜底通知。",
            "",
            f"任务 ID：{task_id}",
            f"任务类型：{task_label}",
            f"工作流 ID：{workflow_run_id or '-'}",
            f"失败已写入数据库：{'是' if persisted else '否'}",
            f"已通知工作流收尾：{'是' if workflow_notified else '否'}",
            f"异常：{error}",
        ]
        if persist_error:
            lines.append(f"落库/收尾异常：{persist_error}")
        lines.extend([
            "",
            f"发送时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        ])
        workflow_notification_service.send_email_message(subject, "\n".join(lines))
    except Exception:
        logger.exception("[CONTENT_PIPELINE] direct failure email failed | task=%s", task_id)


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
        _notify_workflow_run(metadata.get("workflow_run_id"), getattr(task, "id", "-"))
    except Exception:
        logger.exception("[CONTENT_PIPELINE] workflow notify failed | task=%s", getattr(task, "id", "-"))


def _workflow_run_id_from_pipeline(pipeline):
    metadata = pipeline.get("metadata") if isinstance(pipeline, dict) and isinstance(pipeline.get("metadata"), dict) else {}
    return metadata.get("workflow_run_id")


def _notify_workflow_run(workflow_run_id, task_id):
    try:
        if not workflow_run_id:
            return False

        from tasks.zsxq_workflow import advance_zsxq_workflow

        advance_zsxq_workflow.apply_async(
            args=[workflow_run_id, "clean_task_finished"],
            queue="zsxq_workflow",
            retry=False,
        )
        return True
    except Exception:
        logger.exception("[CONTENT_PIPELINE] workflow notify failed | task=%s", task_id)
        return False


def _is_retryable_db_error(exc):
    if isinstance(exc, OperationalError):
        return True
    if isinstance(exc, DBAPIError) and getattr(exc, "connection_invalidated", False):
        return True

    text = str(exc).lower()
    retryable_fragments = (
        "timeout expired",
        "connection to server",
        "server closed the connection unexpectedly",
        "could not connect to server",
        "connection refused",
        "terminating connection",
        "connection not open",
    )
    return any(fragment in text for fragment in retryable_fragments)


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


def _bounded_int(value, *, default, lower, upper):
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(lower, min(upper, number))
