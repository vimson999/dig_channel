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
from services.content_pipeline.auto_publish_handoff import publish_clean_output
from services.runtime_guard import (
    guard_publication_task_start,
    is_retryable_db_error,
    retry_exception_types,
)


logger = logging.getLogger(__name__)

DEFAULT_DB_RETRIES = 5
DEFAULT_DB_RETRY_DELAY_SECONDS = 60
DB_RETRIES_ENV_NAME = "ZSXQ_PUBLISH_DB_RETRIES"
DB_RETRY_DELAY_ENV_NAME = "ZSXQ_PUBLISH_DB_RETRY_DELAY_SECONDS"

TASK_UPDATE_COLUMNS = (
    PublicationTask.id,
    PublicationTask.status,
    PublicationTask.current_step,
    PublicationTask.progress,
    PublicationTask.params,
    PublicationTask.result,
    PublicationTask.error_message,
    PublicationTask.completed_at,
)


def _load_task(task_id, *columns):
    attrs = columns or TASK_UPDATE_COLUMNS
    return PublicationTask.query.options(load_only(*attrs)).filter(PublicationTask.id == str(task_id)).one_or_none()


def _create_zsxq_publish_app():
    """Create an app context for publish tasks without depending on API blueprints."""
    from app import create_app

    try:
        return create_app()
    except Exception:
        logger.exception("Full Flask app initialization failed; falling back to DB-only publish app.")

    from flask import Flask
    from config import Config

    app = Flask(__name__)
    app.config.from_object(Config)
    db.init_app(app)
    return app


@celery_app.task(bind=True, name="tasks.zsxq_publish.run_publish_clean_output_task")
def run_publish_clean_output_task(self, task_id):
    """Publish cleaned content-pipeline output in the dedicated zsxq_publish queue."""
    return _run_publish_task_with_guard(self, task_id)


def _run_publish_task_with_guard(celery_task, task_id):
    try:
        return _run_publish_clean_output_task_impl(task_id, celery_task=celery_task)
    except retry_exception_types():
        raise
    except Exception as exc:
        if _is_retryable_db_error(exc) and celery_task.request.retries < _db_retry_count():
            countdown = _db_retry_delay_seconds(celery_task.request.retries)
            logger.warning(
                "[ZSXQ_PUBLISH_TASK] retry transient db error | task=%s retry=%s/%s countdown=%ss error=%s",
                task_id,
                celery_task.request.retries + 1,
                _db_retry_count(),
                countdown,
                exc,
            )
            raise celery_task.retry(exc=exc, countdown=countdown, max_retries=_db_retry_count())

        details = _mark_publish_task_failed_safely(task_id, exc)
        if not details.get("workflow_notified"):
            _send_direct_publish_failure_email(
                task_id=task_id,
                error=exc,
                workflow_run_id=details.get("workflow_run_id"),
                persisted=details.get("persisted"),
                workflow_notified=details.get("workflow_notified"),
                persist_error=details.get("persist_error"),
            )
        logger.error(
            "[ZSXQ_PUBLISH_TASK] task failed outside normal handler | task=%s persisted=%s workflow_notified=%s error=%s",
            task_id,
            details.get("persisted"),
            details.get("workflow_notified"),
            exc,
            exc_info=True,
        )
        return False


def _run_publish_clean_output_task_impl(task_id, celery_task=None):
    app = _create_zsxq_publish_app()
    with app.app_context():
        task = _load_task(task_id, *TASK_UPDATE_COLUMNS)
        if not task:
            logger.error("ZSXQ publish task not found: %s", task_id)
            return False
        if celery_task is not None:
            guard_publication_task_start(
                celery_task,
                task_id,
                feature="zsxq_clean_output_publish",
                queue="zsxq_publish",
            )

        params = task.params or {}
        publish_params = params.get("zsxq_publish") or {}

        try:
            task.status = "processing"
            task.current_step = "publish"
            task.progress = 5
            result = dict(task.result or {})
            result["message"] = "正在发布清洗产物到知识星球。"
            task.result = result
            flag_modified(task, "result")
            db.session.commit()

            publish_result = publish_clean_output(
                file_type=publish_params.get("file_type"),
                output_dir=publish_params.get("output_dir"),
                options=publish_params.get("options") or {},
                requested_by=f"zsxq_publish:{task.id}",
            )

            status = str(publish_result.get("status") or "").lower()
            failed = status == "failed" or bool(publish_result.get("reason") == "exception")
            task.status = "failed" if failed else "completed"
            task.current_step = "failed" if failed else "done"
            task.progress = 100
            task.completed_at = datetime.utcnow()
            task.error_message = publish_result.get("message") if failed else None
            task.result = {
                **(task.result or {}),
                **publish_result,
                "publish_result": publish_result,
            }
            flag_modified(task, "result")
            db.session.commit()
            _update_attachment_publish_status(task)

            if failed:
                logger.error("ZSXQ publish task failed: %s message=%s", task_id, task.error_message)
                workflow_notified = _notify_workflow_from_publish_task(task)
                if not workflow_notified:
                    _send_direct_publish_failure_email(
                        task_id=task_id,
                        error=task.error_message or "发布失败",
                        workflow_run_id=_workflow_run_id_from_publish_task(task),
                        persisted=True,
                        workflow_notified=workflow_notified,
                    )
                return False

            logger.info("ZSXQ publish task completed: %s status=%s", task_id, status or "completed")
            _notify_workflow_from_publish_task(task)
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
                _update_attachment_publish_status(task)
                workflow_notified = _notify_workflow_from_publish_task(task)
                if not workflow_notified:
                    _send_direct_publish_failure_email(
                        task_id=task_id,
                        error=exc,
                        workflow_run_id=_workflow_run_id_from_publish_task(task),
                        persisted=True,
                        workflow_notified=workflow_notified,
                    )
            logger.error("ZSXQ publish task failed: %s", exc, exc_info=True)
            return False


def _update_attachment_publish_status(task):
    try:
        from services.zsxq_sync_service import zsxq_sync_service

        result = zsxq_sync_service.update_attachment_publish_status_from_publish_task(task)
        logger.info(
            "[ZSXQ_PUBLISH_TASK] attachment publish status updated | task=%s matched=%s updated=%s reason=%s",
            getattr(task, "id", "-"),
            result.get("matched"),
            result.get("updated"),
            result.get("reason") or "",
        )
    except Exception:
        logger.exception("[ZSXQ_PUBLISH_TASK] attachment publish status update failed | task=%s", getattr(task, "id", "-"))


def _notify_workflow_from_publish_task(task):
    try:
        workflow_run_id = _workflow_run_id_from_publish_task(task)
        if not workflow_run_id:
            return False

        from tasks.zsxq_workflow import advance_zsxq_workflow

        advance_zsxq_workflow.apply_async(
            args=[workflow_run_id, "publish_task_finished"],
            queue="zsxq_workflow",
            retry=False,
        )
        return True
    except Exception:
        logger.exception("[ZSXQ_PUBLISH_TASK] workflow notify failed | task=%s", getattr(task, "id", "-"))
        return False


def _workflow_run_id_from_publish_task(task):
    publish_params = (task.params or {}).get("zsxq_publish") or {}
    metadata = publish_params.get("metadata") if isinstance(publish_params.get("metadata"), dict) else {}
    return metadata.get("workflow_run_id")


def _mark_publish_task_failed_safely(task_id, exc):
    details = {
        "persisted": False,
        "workflow_notified": False,
        "workflow_run_id": None,
        "persist_error": None,
    }
    try:
        app = _create_zsxq_publish_app()
        with app.app_context():
            db.session.rollback()
            task = _load_task(task_id, *TASK_UPDATE_COLUMNS)
            if not task:
                details["persist_error"] = f"任务不存在: {task_id}"
                return details

            details["workflow_run_id"] = _workflow_run_id_from_publish_task(task)
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
            _update_attachment_publish_status(task)
            details["workflow_notified"] = _notify_workflow_from_publish_task(task)
    except Exception as persist_exc:
        details["persist_error"] = str(persist_exc)
        try:
            db.session.rollback()
        except Exception:
            pass
        logger.exception("[ZSXQ_PUBLISH_TASK] failed to persist task failure | task=%s", task_id)
    return details


def _send_direct_publish_failure_email(
    *,
    task_id,
    error,
    workflow_run_id=None,
    persisted=False,
    workflow_notified=False,
    persist_error=None,
):
    try:
        from services.workflow_notification_service import workflow_notification_service

        subject = f"[知识星球工作流] 发布任务异常 · {str(task_id)[:8]}"
        lines = [
            "知识星球发布任务异常退出，已触发兜底通知。",
            "",
            f"任务 ID：{task_id}",
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
        logger.exception("[ZSXQ_PUBLISH_TASK] direct failure email failed | task=%s", task_id)


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
