import logging
import os
import sys
from datetime import datetime

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
                final_message=f"工作流启动阶段异常: {exc}",
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
                final_message=f"工作流推进阶段异常: {exc}",
            )


@celery_app.task(bind=True, name="tasks.zsxq_workflow.finalize_zsxq_sync_stage")
def finalize_zsxq_sync_stage(self, workflow_run_id, run_id, *, success, result=None, error=None):
    """Persist sync completion and advance the workflow with DB retry protection."""
    from app import create_app
    from services.zsxq_workflow_service import zsxq_workflow_service

    app = create_app()
    with app.app_context():
        logger.info(
            "[ZSXQ_WORKFLOW_TASK] finalize sync | workflow=%s run_id=%s success=%s",
            workflow_run_id,
            run_id,
            success,
        )
        try:
            zsxq_workflow_service.record_sync_finished(
                workflow_run_id,
                run_id=run_id,
                success=success,
                result=result,
                error=error,
            )
            return zsxq_workflow_service.advance_workflow(workflow_run_id, reason="sync_finished")
        except Exception as exc:
            _handle_task_failure(
                self,
                exc,
                workflow_run_id=workflow_run_id,
                stage="sync",
                final_message=f"同步阶段完成后推进工作流异常: {exc}",
            )


def _handle_task_failure(task, exc, *, workflow_run_id, stage, final_message):
    from models import db

    db.session.rollback()
    retryable_db_error = _is_retryable_db_error(exc)
    if retryable_db_error and task.request.retries < _db_retry_count():
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

    marked_failed = False
    mark_error = None
    try:
        from services.zsxq_workflow_service import zsxq_workflow_service

        result = zsxq_workflow_service.mark_failed_by_id(
            workflow_run_id,
            final_message,
            stage=stage,
        )
        marked_failed = bool(result)
        if not marked_failed:
            mark_error = "工作流记录不存在或未能标记失败"
    except Exception as mark_exc:
        mark_error = str(mark_exc)
        logger.exception("[ZSXQ_WORKFLOW_TASK] failed to mark workflow failed | workflow=%s", workflow_run_id)

    _send_direct_workflow_failure_email(
        workflow_run_id=workflow_run_id,
        stage=stage,
        final_message=final_message,
        error=exc,
        retryable_db_error=retryable_db_error,
        marked_failed=marked_failed,
        mark_error=mark_error,
    )
    raise exc


def _send_direct_workflow_failure_email(
    *,
    workflow_run_id,
    stage,
    final_message,
    error,
    retryable_db_error=False,
    marked_failed=False,
    mark_error=None,
):
    try:
        from services.workflow_notification_service import workflow_notification_service

        subject = f"[知识星球工作流] 编排任务异常 · {stage or '-'} · {str(workflow_run_id)[:8]}"
        lines = [
            "编排任务异常退出，已触发兜底通知。",
            "",
            f"工作流 ID：{workflow_run_id}",
            f"阶段：{stage or '-'}",
            f"失败已写入数据库：{'是' if marked_failed else '否'}",
            f"是否数据库连接类异常：{'是' if retryable_db_error else '否'}",
            f"失败信息：{final_message}",
            f"原始异常：{error}",
        ]
        if mark_error:
            lines.append(f"落库异常：{mark_error}")
        lines.extend([
            "",
            f"发送时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        ])
        workflow_notification_service.send_email_message(subject, "\n".join(lines))
    except Exception:
        logger.exception("[ZSXQ_WORKFLOW_TASK] direct failure email failed | workflow=%s", workflow_run_id)


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
