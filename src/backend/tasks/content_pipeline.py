import logging
import os
import sys
from datetime import datetime
from pathlib import Path

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
from services.runtime_guard import (
    guard_publication_task_start,
    is_retryable_db_error,
    retry_exception_types,
)


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
    return PublicationTask.query.options(load_only(*attrs)).filter(PublicationTask.id == str(task_id)).one_or_none()


def _update_task_fields(task_id, **values):
    """Persist task state without selecting the JSON-bearing row first."""
    task_key = str(task_id)
    if isinstance(values.get("result"), dict):
        values.update(_clean_result_projection_values(values["result"]))
    update_values = {
        getattr(PublicationTask, key): value
        for key, value in values.items()
    }
    update_values[PublicationTask.updated_at] = datetime.utcnow()
    try:
        updated = (
            PublicationTask.query
            .filter(PublicationTask.id == task_key)
            .update(update_values, synchronize_session=False)
        )
        if updated != 1:
            raise RuntimeError(f"Content pipeline task not found during state update: {task_key}")
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    finally:
        db.session.remove()


def _clean_result_projection_values(result):
    result = result if isinstance(result, dict) else {}
    cleaned_files = result.get("cleaned_files") if isinstance(result.get("cleaned_files"), list) else []
    files = result.get("files") if isinstance(result.get("files"), list) else []

    def count(value, fallback=0):
        try:
            return max(0, int(float(value if value is not None else fallback)))
        except (TypeError, ValueError):
            return max(0, int(fallback or 0))

    output_dir = str(result.get("local_output_dir") or "").strip() or None
    return {
        "content_clean_output_dir": output_dir[:600] if output_dir else None,
        "content_clean_files_count": count(
            result.get("files_count"),
            len(cleaned_files or files),
        ),
        "content_clean_success_count": count(
            result.get("success_count"),
            len(cleaned_files),
        ),
        "content_clean_failed_count": count(result.get("failed_count")),
        "content_clean_message": str(result.get("message") or "").strip() or None,
    }


def _update_task_progress(task_id, *, current_step, progress):
    _update_task_fields(
        task_id,
        current_step=str(current_step or "running"),
        progress=max(0, min(100, int(progress or 0))),
    )


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
    return _run_pdf_clean_task_impl_guarded(task_id, celery_task=None)


def _run_pdf_clean_task_impl_guarded(task_id, celery_task=None):
    app = _create_content_pipeline_app()
    with app.app_context():
        task = _load_task(task_id, *TASK_PAYLOAD_COLUMNS)
        if not task:
            logger.error("PDF clean task not found: %s", task_id)
            return False
        task_key = str(task.id)
        if celery_task is not None:
            guard_publication_task_start(
                celery_task,
                task_key,
                feature="content_pdf_clean",
                queue="content_pipeline",
            )

        params = task.params or {}
        progress_result = dict(task.result or {})
        progress_state = {
            "current_step": "prepare",
            "progress": 3,
            "checkpointed_job_id": None,
        }
        pipeline = params.get("content_pipeline") or {}
        workflow_run_id = _workflow_run_id_from_pipeline(pipeline)
        pdf_clean = pipeline.get("pdf_clean") or {}
        cleaner_config = pdf_clean.get("cleaner") or {}

        try:
            _update_task_fields(
                task_key,
                status="processing",
                current_step="prepare",
                progress=3,
                error_message=None,
                completed_at=None,
            )

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
                job = event.get("job") or {}
                job_id = event.get("job_id") or progress_result.get("cleaner_job_id")
                progress_result["cleaner_job_id"] = job_id
                progress_result["cleaner_job"] = job or progress_result.get("cleaner_job")
                progress_result["message"] = job.get("message") or progress_result.get("message")
                current_step = event.get("phase") or "running"
                progress = int(event.get("progress") or progress_state["progress"])
                if (
                    current_step == progress_state["current_step"]
                    and progress == progress_state["progress"]
                ):
                    return
                progress_state.update({"current_step": current_step, "progress": progress})
                if job_id and job_id != progress_state["checkpointed_job_id"]:
                    _update_task_fields(
                        task_key,
                        current_step=current_step,
                        progress=progress,
                        result=dict(progress_result),
                    )
                    progress_state["checkpointed_job_id"] = job_id
                else:
                    _update_task_progress(
                        task_key,
                        current_step=current_step,
                        progress=progress,
                    )

            result = adapter.run_batch(
                batch_id=task_key,
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

            merged_result = dict(progress_result)
            merged_result.update(result)
            source_refs = _clean_source_refs(pipeline, merged_result)
            result = _enqueue_auto_publish_task(
                task_id=task_key,
                file_type="pdf",
                result=merged_result,
                options=pipeline.get("auto_publish") or {},
                pipeline=pipeline,
                source_refs=source_refs,
            )

            _update_task_fields(
                task_key,
                status="completed",
                current_step="done",
                progress=100,
                completed_at=datetime.utcnow(),
                error_message=None,
                result=result,
            )
            logger.info("PDF clean task completed: %s files=%s", task_key, len(cleaned_files))
            _notify_workflow_run(
                workflow_run_id,
                task_key,
                event_payload=_clean_stage_event(task_key, source_refs),
            )
            return True
        except Exception as exc:
            db.session.rollback()
            if _is_retryable_db_error(exc):
                db.session.remove()
                raise
            failure_result = dict(progress_result)
            failure_result["error"] = str(exc)
            _update_task_fields(
                task_key,
                status="failed",
                current_step="failed",
                progress=progress_state["progress"],
                error_message=str(exc),
                completed_at=datetime.utcnow(),
                result=failure_result,
            )
            _notify_workflow_run(workflow_run_id, task_key)
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
    return _run_audio_clean_task_impl_guarded(task_id, celery_task=None)


def _run_audio_clean_task_impl_guarded(task_id, celery_task=None):
    app = _create_content_pipeline_app()
    with app.app_context():
        task = _load_task(task_id, *TASK_PAYLOAD_COLUMNS)
        if not task:
            logger.error("Audio clean task not found: %s", task_id)
            return False
        task_key = str(task.id)
        if celery_task is not None:
            guard_publication_task_start(
                celery_task,
                task_key,
                feature="content_audio_clean",
                queue="content_pipeline",
            )

        params = task.params or {}
        progress_result = dict(task.result or {})
        progress_state = {"current_step": "prepare", "progress": 3}
        pipeline = params.get("content_pipeline") or {}
        workflow_run_id = _workflow_run_id_from_pipeline(pipeline)
        audio_clean = pipeline.get("audio_clean") or {}
        cleaner_config = audio_clean.get("cleaner") or {}

        try:
            _update_task_fields(
                task_key,
                status="processing",
                current_step="prepare",
                progress=3,
                error_message=None,
                completed_at=None,
            )

            options = AudioCleanOptions(
                head_trim_seconds=float(cleaner_config.get("head_trim_seconds") or 11),
                tail_trim_seconds=float(cleaner_config.get("tail_trim_seconds") or 90),
                gap_audio_path=cleaner_config.get("gap_audio_path") or "",
                bitrate=cleaner_config.get("bitrate") or "128k",
                sample_rate=int(cleaner_config.get("sample_rate") or 44100),
                channels=int(cleaner_config.get("channels") or 2),
                fast_copy=str(cleaner_config.get("fast_copy", True)).lower() not in {"0", "false", "no", "off"},
                max_output_size_mb=float(cleaner_config.get("max_output_size_mb") or 49),
            )
            adapter = AudioCleanerAdapter(output_root=cleaner_config.get("output_root"))

            files = audio_clean.get("files") or []
            source_root = audio_clean.get("source_root") or None

            def update_progress(event):
                progress_result["message"] = event.get("message") or progress_result.get("message")
                current_step = event.get("phase") or "running"
                progress = int(event.get("progress") or progress_state["progress"])
                if (
                    current_step == progress_state["current_step"]
                    and progress == progress_state["progress"]
                ):
                    return
                progress_state.update({"current_step": current_step, "progress": progress})
                _update_task_progress(
                    task_key,
                    current_step=current_step,
                    progress=progress,
                )

            result = adapter.run_batch(
                batch_id=task_key,
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

            merged_result = dict(progress_result)
            merged_result.update(result)
            source_refs = _clean_source_refs(pipeline, merged_result)
            result = _enqueue_auto_publish_task(
                task_id=task_key,
                file_type="audio",
                result=merged_result,
                options=pipeline.get("auto_publish") or {},
                pipeline=pipeline,
                source_refs=source_refs,
            )

            _update_task_fields(
                task_key,
                status="completed",
                current_step="done",
                progress=100,
                completed_at=datetime.utcnow(),
                error_message=None,
                result=result,
            )
            logger.info("Audio clean task completed: %s files=%s", task_key, len(cleaned_files))
            _notify_workflow_run(
                workflow_run_id,
                task_key,
                event_payload=_clean_stage_event(task_key, source_refs),
            )
            return True
        except Exception as exc:
            db.session.rollback()
            if _is_retryable_db_error(exc):
                db.session.remove()
                raise
            failure_result = dict(progress_result)
            failure_result["error"] = str(exc)
            _update_task_fields(
                task_key,
                status="failed",
                current_step="failed",
                progress=progress_state["progress"],
                error_message=str(exc),
                completed_at=datetime.utcnow(),
                result=failure_result,
            )
            _notify_workflow_run(workflow_run_id, task_key)
            logger.error("Audio clean task failed: %s", exc, exc_info=True)
            return False


def _run_content_pipeline_task_with_guard(celery_task, task_id, task_label, runner):
    try:
        guarded_runner = {
            _run_pdf_clean_task_impl: _run_pdf_clean_task_impl_guarded,
            _run_audio_clean_task_impl: _run_audio_clean_task_impl_guarded,
        }.get(runner)
        if guarded_runner is not None:
            return guarded_runner(task_id, celery_task=celery_task)
        return runner(task_id)
    except retry_exception_types():
        raise
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


def _enqueue_auto_publish_task(*, task_id, file_type, result, options, pipeline, source_refs=None):
    if not isinstance(options, dict) or not options.get("enabled"):
        return result

    try:
        task_key = str(task_id)
        _update_task_progress(
            task_key,
            current_step="enqueue_publish",
            progress=92,
        )

        publish_task = submit_publish_clean_output_task(
            title=f"知识星球清洗产物发布 - {task_key[:8]}",
            file_type=file_type,
            output_dir=result.get("local_output_dir"),
            options=options,
            source_task_id=task_key,
            trigger=options.get("trigger") or "content_pipeline_clean_complete",
            metadata={
                "content_pipeline_task_id": task_key,
                "content_pipeline_title": (pipeline or {}).get("title") or "",
                "source_metadata": (pipeline or {}).get("metadata") or {},
                "source_refs": list(source_refs or []),
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
        logger.exception("Clean output publish enqueue failed: task=%s type=%s", task_id, file_type)
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


def _notify_workflow_run(workflow_run_id, task_id, event_payload=None):
    try:
        if not workflow_run_id:
            return False

        from tasks.zsxq_workflow import advance_zsxq_workflow

        advance_zsxq_workflow.apply_async(
            args=[workflow_run_id, "clean_task_finished", event_payload],
            queue="zsxq_workflow",
            retry=False,
        )
        return True
    except Exception:
        logger.exception("[CONTENT_PIPELINE] workflow notify failed | task=%s", task_id)
        return False


def _clean_stage_event(task_id, source_refs):
    return {
        "stage": "clean",
        "task_id": str(task_id),
        "source_refs": list(source_refs or []),
    }


def _clean_source_refs(pipeline, result):
    """Build a bounded source-to-clean-output snapshot while both are in memory."""
    pipeline = pipeline if isinstance(pipeline, dict) else {}
    result = result if isinstance(result, dict) else {}
    metadata = pipeline.get("metadata") if isinstance(pipeline.get("metadata"), dict) else {}
    source_files = metadata.get("files") if isinstance(metadata.get("files"), list) else []
    clean_config = pipeline.get("pdf_clean") or pipeline.get("audio_clean") or {}
    source_root = _normalized_path(clean_config.get("source_root"))
    default_group_id = str(metadata.get("group_id") or "").strip()

    output_paths = {}
    for item in (result.get("cleaned_files") or result.get("files") or [])[:100]:
        if not isinstance(item, dict):
            continue
        source_path = _normalized_path(item.get("source_path") or item.get("source"), source_root)
        output_path = _normalized_path(
            item.get("cleaner_output_path") or item.get("output_path") or item.get("path")
        )
        if source_path and output_path:
            output_paths.setdefault(source_path, []).append(output_path)

    refs = []
    for item in source_files[:100]:
        if not isinstance(item, dict):
            continue
        source_path = _normalized_path(item.get("path") or item.get("local_path"), source_root)
        name = str(item.get("name") or "").strip()
        if not name and source_path:
            name = Path(source_path).name
        refs.append({
            "attachment_id": str(item.get("attachment_id") or "").strip()[:36],
            "group_id": str(item.get("group_id") or default_group_id or "").strip()[:32],
            "tab_id": str(item.get("tab_id") or "").strip()[:128],
            "topic_id": str(item.get("topic_id") or "").strip()[:64],
            "source_path": source_path,
            "name": name[:500],
            "output_paths": output_paths.get(source_path, []) if source_path else [],
        })
    return refs


def _normalized_path(value, source_root=None):
    if not value or str(value).startswith("browser-download:"):
        return None
    try:
        path = Path(str(value)).expanduser()
        if not path.is_absolute() and source_root:
            path = Path(source_root) / path
        return str(path.resolve())[:600]
    except (OSError, TypeError, ValueError):
        return None


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


def _bounded_int(value, *, default, lower, upper):
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(lower, min(upper, number))
