import logging
import os
import sys
from datetime import datetime, timedelta

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from celery_config import celery_app
from models import db
from services.runtime_guard import is_retryable_db_error, retry_exception_types


logger = logging.getLogger(__name__)

DEFAULT_DB_RETRIES = 5
DEFAULT_DB_RETRY_DELAY_SECONDS = 60
DB_RETRIES_ENV_NAME = "ZSXQ_SYNC_DB_RETRIES"
DB_RETRY_DELAY_ENV_NAME = "ZSXQ_SYNC_DB_RETRY_DELAY_SECONDS"
DEFAULT_UPSTREAM_RETRIES = 5
DEFAULT_UPSTREAM_RETRY_DELAY_SECONDS = 300
UPSTREAM_RETRIES_ENV_NAME = "ZSXQ_SYNC_UPSTREAM_RETRIES"
UPSTREAM_RETRY_DELAY_ENV_NAME = "ZSXQ_SYNC_UPSTREAM_RETRY_DELAY_SECONDS"


@celery_app.task(bind=True, name="tasks.zsxq_sync.run_zsxq_sync")
def run_zsxq_sync(self, run_id, payload):
    """Run one ZSXQ sync job in the dedicated zsxq_sync queue."""
    from app import create_app
    from services.zsxq_sync_service import zsxq_sync_service

    app = create_app()
    with app.app_context():
        terminal_delivery = _terminal_delivery_result(run_id, task_id=self.request.id)
        if terminal_delivery:
            return terminal_delivery

        workflow_run_id = payload.get("workflow_run_id")
        credential_id = payload.get("credential_id") or payload.get("platform_credential_id")
        logger.info("[ZSXQ_SYNC_TASK] start | run_id=%s", run_id)
        try:
            cookie = payload.get("cookie")
            if not cookie:
                from services.platform_credential_service import resolve_zsxq_cookie

                credential = resolve_zsxq_cookie(
                    account_id=credential_id,
                    purpose="zsxq.sync",
                    required=True,
                )
                cookie = credential.get("cookie")
                credential_id = credential.get("credential_id") or credential_id
            result = zsxq_sync_service.sync_tabs(
                run_id=run_id,
                cookie=cookie,
                group_id=payload.get("group_id"),
                group_name=payload.get("group_name"),
                tabs=payload.get("tabs"),
                count=payload.get("count", 20),
                max_pages=payload.get("max_pages", 1),
                stop_on_existing=payload.get("stop_on_existing", True),
                download_attachments=payload.get("download_attachments", False),
                download_pdf_only=payload.get("download_pdf_only", True),
                download_file_types=payload.get("download_file_types"),
                download_file_types_by_tab=payload.get("download_file_types_by_tab"),
                max_download_files=payload.get("max_download_files"),
                allow_attachment_downloads=payload.get("allow_attachment_downloads", False),
                mode=payload.get("mode", "manual"),
                sync_meta=payload.get("sync_meta"),
                manual_attachment_plan=payload.get("manual_attachment_plan"),
                attachment_download_rules=payload.get("attachment_download_rules"),
                auto_video_dispatch=payload.get("auto_video_dispatch"),
                auto_script_material_dispatch=payload.get("auto_script_material_dispatch"),
                workflow_run_id=workflow_run_id,
            )
            if not workflow_run_id and _auto_pdf_clean_enabled(payload):
                try:
                    pdf_clean_result = zsxq_sync_service.submit_pdf_clean_for_run(
                        run_id,
                        cleaner=payload.get("pdf_cleaner") or {},
                        trigger=payload.get("trigger") or "zsxq_sync",
                        auto_publish=payload.get("auto_publish_after_clean") or {},
                    )
                    result.setdefault("run", {}).setdefault("result", {}).setdefault("content_pipeline", {})[
                        "pdf_clean"
                    ] = pdf_clean_result
                    logger.info(
                        "[ZSXQ_SYNC_TASK] pdf clean submitted | run_id=%s queued=%s task_id=%s files=%s",
                        run_id,
                        bool(pdf_clean_result.get("queued")),
                        pdf_clean_result.get("task_id") or "-",
                        pdf_clean_result.get("files_count") or 0,
                    )
                except Exception as exc:
                    logger.exception("[ZSXQ_SYNC_TASK] pdf clean submit failed | run_id=%s error=%s", run_id, exc)
                    try:
                        zsxq_sync_service.set_run_pdf_clean_result(
                            run_id,
                            {
                                "queued": False,
                                "reason": "submit_failed",
                                "error": str(exc),
                            },
                        )
                    except Exception:
                        logger.exception("[ZSXQ_SYNC_TASK] failed to persist pdf clean submit error | run_id=%s", run_id)
            if not workflow_run_id and _auto_audio_clean_enabled(payload):
                try:
                    audio_clean_result = zsxq_sync_service.submit_audio_clean_for_run(
                        run_id,
                        cleaner=payload.get("audio_cleaner") or {},
                        trigger=payload.get("trigger") or "zsxq_sync",
                        auto_publish=payload.get("auto_publish_after_clean") or {},
                    )
                    result.setdefault("run", {}).setdefault("result", {}).setdefault("content_pipeline", {})[
                        "audio_clean"
                    ] = audio_clean_result
                    logger.info(
                        "[ZSXQ_SYNC_TASK] audio clean submitted | run_id=%s queued=%s task_id=%s files=%s",
                        run_id,
                        bool(audio_clean_result.get("queued")),
                        audio_clean_result.get("task_id") or "-",
                        audio_clean_result.get("files_count") or 0,
                    )
                except Exception as exc:
                    logger.exception("[ZSXQ_SYNC_TASK] audio clean submit failed | run_id=%s error=%s", run_id, exc)
                    try:
                        zsxq_sync_service.set_run_audio_clean_result(
                            run_id,
                            {
                                "queued": False,
                                "reason": "submit_failed",
                                "error": str(exc),
                            },
                        )
                    except Exception:
                        logger.exception("[ZSXQ_SYNC_TASK] failed to persist audio clean submit error | run_id=%s", run_id)
            _submit_report_asset_scan_after_sync(run_id, payload, result)
            if workflow_run_id:
                queued_id = _notify_workflow_sync_finished(workflow_run_id, run_id, success=True, result=result)
                if not queued_id:
                    _send_direct_sync_failure_email(
                        workflow_run_id=workflow_run_id,
                        run_id=run_id,
                        success=True,
                        error="同步完成，但工作流 finalizer 入队失败，可能无法进入清洗/发布阶段。",
                    )
            if credential_id:
                try:
                    from services.platform_credential_service import mark_credential_success

                    mark_credential_success(credential_id)
                except Exception:
                    logger.exception("[ZSXQ_SYNC_TASK] credential success mark failed | credential=%s", credential_id)
            logger.info("[ZSXQ_SYNC_TASK] complete | run_id=%s", run_id)
            return result
        except retry_exception_types():
            raise
        except Exception as exc:
            db.session.rollback()
            retryable_db_error = _is_retryable_db_error(exc)
            if retryable_db_error and self.request.retries < _db_retry_count():
                countdown = _db_retry_delay_seconds(self.request.retries)
                logger.warning(
                    "[ZSXQ_SYNC_TASK] retry transient db error | run_id=%s retry=%s/%s countdown=%ss error=%s",
                    run_id,
                    self.request.retries + 1,
                    _db_retry_count(),
                    countdown,
                    exc,
                )
                raise self.retry(exc=exc, countdown=countdown, max_retries=_db_retry_count())

            _maybe_retry_transient_upstream_error(self, run_id, exc)

            if credential_id:
                try:
                    from services.platform_credential_service import is_auth_error, mark_credential_error

                    mark_credential_error(credential_id, exc, expired=is_auth_error(exc))
                except Exception:
                    logger.exception("[ZSXQ_SYNC_TASK] credential error mark failed | credential=%s", credential_id)
            if workflow_run_id:
                queued_id = _notify_workflow_sync_finished(workflow_run_id, run_id, success=False, error=str(exc))
                if not queued_id:
                    _send_direct_sync_failure_email(
                        workflow_run_id=workflow_run_id,
                        run_id=run_id,
                        success=False,
                        error=str(exc),
                    )
            raise
        finally:
            db.session.remove()


def _is_retryable_db_error(exc):
    return is_retryable_db_error(exc)


def _terminal_delivery_result(run_id, task_id=None):
    from models.zsxq_sync import ZsxqSyncRun

    persisted_run = db.session.get(ZsxqSyncRun, run_id)
    if not persisted_run or persisted_run.status not in {"completed", "failed"}:
        return None
    logger.warning(
        "[ZSXQ_SYNC_TASK] duplicate terminal delivery ignored | run_id=%s status=%s task_id=%s",
        run_id,
        persisted_run.status,
        task_id,
    )
    return {
        "skipped": True,
        "reason": "duplicate_terminal_delivery",
        "run_id": run_id,
        "status": persisted_run.status,
    }


def _is_retryable_upstream_error(exc):
    try:
        from services.zsxq_sync_service import ZsxqTransientUpstreamError

        if isinstance(exc, ZsxqTransientUpstreamError):
            return True
    except Exception:
        pass

    try:
        from requests import RequestException

        if isinstance(exc, RequestException):
            return True
    except Exception:
        pass

    message = str(exc or "").lower()
    return "code=1059" in message or "code 1059" in message


def _maybe_retry_transient_upstream_error(celery_task, run_id, exc):
    if not _is_retryable_upstream_error(exc):
        return False
    retry_count = _upstream_retry_count()
    retries = _request_retries(celery_task)
    if retry_count <= 0 or retries >= retry_count:
        return False

    countdown = _upstream_retry_delay_seconds(retries)
    logger.warning(
        "[ZSXQ_SYNC_TASK] retry transient upstream error | run_id=%s retry=%s/%s countdown=%ss error=%s",
        run_id,
        retries + 1,
        retry_count,
        countdown,
        exc,
    )
    _mark_sync_run_upstream_retrying(
        run_id,
        exc,
        celery_task,
        countdown=countdown,
        retry_count=retry_count,
    )
    raise celery_task.retry(exc=exc, countdown=countdown, max_retries=retry_count)


def _mark_sync_run_upstream_retrying(run_id, exc, celery_task, *, countdown, retry_count):
    if not run_id:
        return
    try:
        from models.zsxq_sync import ZsxqSyncRun

        run = ZsxqSyncRun.query.filter(ZsxqSyncRun.id == str(run_id)).one_or_none()
        if not run:
            return
        now = datetime.utcnow()
        now_text = now.isoformat() + "Z"
        next_retry_at = (now + timedelta(seconds=countdown)).isoformat() + "Z"
        retries = _request_retries(celery_task)
        retry_number = retries + 1
        result = dict(run.result or {})
        retry_state = dict(result.get("retry_state") or {})
        retry_state["upstream"] = {
            "status": "scheduled",
            "retry_number": retry_number,
            "retry_limit": retry_count,
            "retry_delay_seconds": countdown,
            "next_retry_at": next_retry_at,
            "last_error": str(exc),
            "updated_at": now_text,
        }
        result.update({
            "queued": True,
            "running": False,
            "last_error": str(exc),
            "retry_state": retry_state,
        })
        run.status = "queued"
        run.completed_at = None
        run.error_message = None
        run.result = result
        db.session.add(run)
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception("[ZSXQ_SYNC_TASK] failed to mark upstream retry | run_id=%s", run_id)


def _request_retries(celery_task):
    return int(getattr(getattr(celery_task, "request", None), "retries", 0) or 0)


def _db_retry_count():
    return max(0, _int_env(DB_RETRIES_ENV_NAME, DEFAULT_DB_RETRIES))


def _db_retry_delay_seconds(retry_index):
    base = max(1, _int_env(DB_RETRY_DELAY_ENV_NAME, DEFAULT_DB_RETRY_DELAY_SECONDS))
    return min(base * max(int(retry_index or 0) + 1, 1), 600)


def _upstream_retry_count():
    return max(0, min(_int_env(UPSTREAM_RETRIES_ENV_NAME, DEFAULT_UPSTREAM_RETRIES), 20))


def _upstream_retry_delay_seconds(retry_index):
    base = max(1, _int_env(UPSTREAM_RETRY_DELAY_ENV_NAME, DEFAULT_UPSTREAM_RETRY_DELAY_SECONDS))
    return min(base * max(int(retry_index or 0) + 1, 1), 1800)


def _int_env(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return int(default)


def _auto_pdf_clean_enabled(payload):
    return _truthy(payload.get("auto_pdf_clean", False))


def _auto_audio_clean_enabled(payload):
    return _truthy(payload.get("auto_audio_clean", False))


def _truthy(value):
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _submit_report_asset_scan_after_sync(run_id, payload, result):
    try:
        from services.report_video_asset_service import (
            build_sync_run_asset_scan_payload,
            should_auto_scan_after_sync,
        )
        from tasks.report_video import scan_zsxq_report_assets_task

        if not should_auto_scan_after_sync(payload, result):
            return
        scan_payload = build_sync_run_asset_scan_payload(run_id, payload)
        celery_task = scan_zsxq_report_assets_task.apply_async(
            args=[scan_payload, False],
            queue="report_video",
            retry=False,
        )
        result.setdefault("run", {}).setdefault("result", {}).setdefault("report_asset_scan", {})[
            "celery_task_id"
        ] = celery_task.id
        logger.info(
            "[ZSXQ_SYNC_TASK] report asset scan queued | run_id=%s celery_id=%s",
            run_id,
            celery_task.id,
        )
    except Exception as exc:
        logger.warning("[ZSXQ_SYNC_TASK] report asset scan submit skipped | run_id=%s error=%s", run_id, exc)


def _notify_workflow_sync_finished(workflow_run_id, run_id, *, success, result=None, error=None):
    from tasks.zsxq_workflow import finalize_zsxq_sync_stage

    try:
        async_result = finalize_zsxq_sync_stage.apply_async(
            args=[workflow_run_id, run_id],
            kwargs={
                "success": success,
                "result": result,
                "error": error,
            },
            queue="zsxq_workflow",
            retry=False,
        )
        logger.info(
            "[ZSXQ_SYNC_TASK] workflow finalizer queued | workflow=%s run_id=%s celery_id=%s success=%s",
            workflow_run_id,
            run_id,
            async_result.id,
            success,
        )
        return async_result.id
    except Exception as exc:
        logger.exception(
            "[ZSXQ_SYNC_TASK] workflow finalizer queue failed | workflow=%s run_id=%s success=%s error=%s",
            workflow_run_id,
            run_id,
            success,
            exc,
        )
        return None


def _send_direct_sync_failure_email(*, workflow_run_id, run_id, success, error):
    try:
        from datetime import datetime
        from services.workflow_notification_service import workflow_notification_service

        subject = f"[知识星球工作流] 同步收尾异常 · {str(workflow_run_id)[:8]}"
        lines = [
            "知识星球同步任务未能通知工作流收尾，已触发兜底通知。",
            "",
            f"工作流 ID：{workflow_run_id}",
            f"同步 Run ID：{run_id}",
            f"同步是否已成功：{'是' if success else '否'}",
            f"异常：{error}",
            "",
            f"发送时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        ]
        workflow_notification_service.send_email_message(subject, "\n".join(lines))
    except Exception:
        logger.exception("[ZSXQ_SYNC_TASK] direct sync failure email failed | workflow=%s run_id=%s", workflow_run_id, run_id)
