import logging
import os
import sys

from sqlalchemy.exc import DBAPIError, OperationalError

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from celery_config import celery_app


logger = logging.getLogger(__name__)

DEFAULT_DB_RETRIES = 5
DEFAULT_DB_RETRY_DELAY_SECONDS = 60
DB_RETRIES_ENV_NAME = "ZSXQ_WORKFLOW_DB_RETRIES"
DB_RETRY_DELAY_ENV_NAME = "ZSXQ_WORKFLOW_DB_RETRY_DELAY_SECONDS"


@celery_app.task(bind=True, name="tasks.zsxq_workflow.run_zsxq_workflow")
def run_zsxq_workflow(self, workflow_run_id, runtime=None):
    """Start a configured ZSXQ workflow and submit its first stage."""
    from app import create_app
    from services.zsxq_workflow_service import zsxq_workflow_service

    app = create_app()
    with app.app_context():
        logger.info("[ZSXQ_WORKFLOW_TASK] start | workflow=%s", workflow_run_id)
        try:
            return zsxq_workflow_service.start_workflow(workflow_run_id, runtime=runtime or {})
        except Exception as exc:
            _handle_task_failure(
                self,
                exc,
                workflow_run_id=workflow_run_id,
                stage="workflow",
                final_message=f"工作流启动阶段数据库异常重试耗尽: {exc}",
            )


@celery_app.task(bind=True, name="tasks.zsxq_workflow.advance_zsxq_workflow")
def advance_zsxq_workflow(self, workflow_run_id, reason=None):
    """Advance a workflow after one stage has reached a terminal state."""
    from app import create_app
    from services.zsxq_workflow_service import zsxq_workflow_service

    app = create_app()
    with app.app_context():
        logger.info("[ZSXQ_WORKFLOW_TASK] advance | workflow=%s reason=%s", workflow_run_id, reason or "-")
        try:
            return zsxq_workflow_service.advance_workflow(workflow_run_id, reason=reason)
        except Exception as exc:
            _handle_task_failure(
                self,
                exc,
                workflow_run_id=workflow_run_id,
                stage="workflow",
                final_message=f"工作流推进阶段数据库异常重试耗尽: {exc}",
            )


def _handle_task_failure(task, exc, *, workflow_run_id, stage, final_message):
    from models import db

    db.session.rollback()
    if _is_retryable_db_error(exc) and task.request.retries < _db_retry_count():
        countdown = _db_retry_delay_seconds(task.request.retries)
        logger.warning(
            "[ZSXQ_WORKFLOW_TASK] retry transient db error | workflow=%s retry=%s/%s countdown=%ss error=%s",
            workflow_run_id,
            task.request.retries + 1,
            _db_retry_count(),
            countdown,
            exc,
        )
        raise task.retry(exc=exc, countdown=countdown, max_retries=_db_retry_count())

    if _is_retryable_db_error(exc):
        try:
            from services.zsxq_workflow_service import zsxq_workflow_service

            zsxq_workflow_service.mark_failed_by_id(
                workflow_run_id,
                final_message,
                stage=stage,
            )
        except Exception:
            logger.exception("[ZSXQ_WORKFLOW_TASK] failed to mark workflow failed | workflow=%s", workflow_run_id)
    raise exc


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
