"""Shared Celery execution lifecycle for browser-backed platform publishing."""

import logging
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, Tuple, Type

from models import db
from models.task import PublicationTask
from services.platform_credential_service import mark_credential_error, mark_credential_success
from services.runtime_guard import guard_publication_task_start


LOGGER = logging.getLogger(__name__)


class _NoSubmissionPendingError(Exception):
    """Sentinel used by publishers without a post-submit pending state."""


@dataclass(frozen=True)
class BrowserPublishTaskSpec:
    feature_name: str
    queue_name: str
    platform_label: str
    resolve_credential: Callable[..., Dict[str, Any]]
    publish: Callable[..., Dict[str, Any]]
    authentication_error: Type[Exception]
    verification_error: Type[Exception]
    browser_error: Type[Exception]
    guard_start: Callable[..., Any] = guard_publication_task_start
    mark_success: Callable[..., Any] = mark_credential_success
    mark_error: Callable[..., Any] = mark_credential_error
    extra_command_keys: Tuple[str, ...] = ()
    execution_guard: Callable[..., Any] = lambda **_kwargs: nullcontext()
    include_credential_context: bool = False
    submission_pending_error: Type[Exception] = _NoSubmissionPendingError


def _update_task(spec, job_id, **values):
    try:
        db.session.rollback()
        columns = {}
        for key, value in values.items():
            column = getattr(PublicationTask, key, None)
            if column is None:
                raise ValueError(f"未知任务字段: {key}")
            columns[column] = value
        updated = PublicationTask.query.filter(PublicationTask.id == str(job_id)).update(columns, synchronize_session=False)
        if not updated:
            raise ValueError(f"{spec.platform_label}上传任务不存在: {job_id}")
        db.session.commit()
    finally:
        db.session.remove()


def _finish_task(spec, job_id, *, status, step, progress, result=None, error=None):
    values = {
        "status": status, "current_step": step, "progress": progress,
        "error_message": error, "completed_at": datetime.utcnow(),
    }
    if result is not None:
        values["result"] = result
    _update_task(spec, job_id, **values)


def _mark_credential_safely(action, *args, **kwargs):
    try:
        action(*args, **kwargs)
    except Exception:
        LOGGER.exception("Browser publisher credential state update failed")
        db.session.remove()


def run_browser_publish_job(celery_task, job_id, spec: BrowserPublishTaskSpec):
    from app import create_app

    app = create_app(register_apis=False)
    with app.app_context():
        task = PublicationTask.query.filter(PublicationTask.id == str(job_id)).first()
        if not task:
            return
        request_id = str(getattr(getattr(celery_task, "request", None), "id", "") or "")
        if request_id and task.celery_task_id and request_id != task.celery_task_id:
            LOGGER.info("Skipping stale %s message %s for job %s", spec.platform_label, request_id, job_id)
            db.session.remove(); return
        if task.status in {"completed", "failed", "canceled"}:
            db.session.remove(); return
        spec.guard_start(celery_task, job_id, feature=spec.feature_name, queue=spec.queue_name)
        command = dict(dict(task.params or {}).get(spec.feature_name) or {})
        db.session.remove()
        credential_id = str(command.get("credential_id") or "")
        result = {
            "original_filename": command.get("original_filename"),
            "submit_requested": bool(command.get("submit")),
            "visibility": command.get("visibility") or "private",
            **{key: command.get(key) for key in spec.extra_command_keys},
        }

        def progress(step, percent, _message):
            _update_task(spec, job_id, status="processing", current_step=step, progress=int(percent), error_message=None)

        try:
            _update_task(spec, job_id, status="processing", current_step="resolving_credential", progress=5, error_message=None)
            auth = spec.resolve_credential(account_id=credential_id, required=True)
            db.session.remove()
            kwargs = {
                "cookies": auth["cookies"], "video_path": command.get("video_path"),
                "title": command.get("title"), "description": command.get("description") or "",
                "tags": command.get("tags") or [], "visibility": command.get("visibility") or "private",
                "submit": bool(command.get("submit")), "progress": progress,
                **{key: command.get(key) for key in spec.extra_command_keys},
            }
            if spec.include_credential_context:
                kwargs["credential_id"] = auth.get("credential_id")
            with spec.execution_guard(
                credential_id=auth.get("credential_id"),
                command=command,
                progress=progress,
            ):
                publish_result = spec.publish(**kwargs)
            result.update(publish_result)
            result["credential_id"] = auth.get("credential_id")
            result["credential_name"] = auth.get("credential_name")
            _mark_credential_safely(spec.mark_success, auth.get("credential_id"))
            finish_step = str(publish_result.get("publish_status") or "").strip()
            if finish_step not in {"visible", "submitted", "prepared"}:
                finish_step = "submitted" if publish_result.get("submitted") else "prepared"
            _finish_task(
                spec, job_id, status="completed",
                step=finish_step,
                progress=100, result=result,
            )
            return result
        except spec.submission_pending_error as exc:
            expired = bool(getattr(exc, "credential_expired", False))
            _mark_credential_safely(spec.mark_error, credential_id, exc, expired=expired)
            result.update({
                "publish_status": "submitted_pending_verification",
                "submitted": True,
                "confirmed": False,
                "message": str(exc),
            })
            _finish_task(
                spec,
                job_id,
                status="completed",
                step="submitted_pending_verification",
                progress=100,
                result=result,
            )
            return result
        except spec.authentication_error as exc:
            _mark_credential_safely(spec.mark_error, credential_id, exc, expired=True)
            result.update({"publish_status": "auth_required", "message": str(exc)})
            _finish_task(spec, job_id, status="failed", step="auth_required", progress=10, result=result, error=str(exc))
        except spec.verification_error as exc:
            _mark_credential_safely(spec.mark_error, credential_id, exc, expired=False)
            result.update({"publish_status": "verification_required", "message": str(exc)})
            _finish_task(spec, job_id, status="failed", step="manual_verification_required", progress=90, result=result, error=str(exc))
        except spec.browser_error as exc:
            _mark_credential_safely(spec.mark_error, credential_id, exc, expired=False)
            result.update({"publish_status": "failed", "message": str(exc)})
            _finish_task(spec, job_id, status="failed", step="failed", progress=10, result=result, error=str(exc))
        except Exception as exc:
            LOGGER.exception("%s upload job %s failed", spec.platform_label, job_id)
            result.update({"publish_status": "failed", "message": str(exc)})
            _finish_task(spec, job_id, status="failed", step="failed", progress=10, result=result, error=str(exc))
        return result
