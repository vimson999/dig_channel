import logging
import os
import sys

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from celery_config import celery_app


logger = logging.getLogger(__name__)


@celery_app.task(bind=True, name="tasks.zsxq_sync.run_zsxq_sync")
def run_zsxq_sync(self, run_id, payload):
    """Run one ZSXQ sync job in the dedicated zsxq_sync queue."""
    from app import create_app
    from services.zsxq_sync_service import zsxq_sync_service

    app = create_app()
    with app.app_context():
        workflow_run_id = payload.get("workflow_run_id")
        logger.info("[ZSXQ_SYNC_TASK] start | run_id=%s", run_id)
        try:
            result = zsxq_sync_service.sync_tabs(
                run_id=run_id,
                cookie=payload.get("cookie"),
                group_id=payload.get("group_id"),
                group_name=payload.get("group_name"),
                tabs=payload.get("tabs"),
                count=payload.get("count", 20),
                max_pages=payload.get("max_pages", 1),
                stop_on_existing=payload.get("stop_on_existing", True),
                download_attachments=payload.get("download_attachments", True),
                download_pdf_only=payload.get("download_pdf_only", True),
                download_file_types=payload.get("download_file_types"),
                download_file_types_by_tab=payload.get("download_file_types_by_tab"),
                max_download_files=payload.get("max_download_files"),
                mode=payload.get("mode", "manual"),
                sync_meta=payload.get("sync_meta"),
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
            if workflow_run_id:
                _notify_workflow_sync_finished(workflow_run_id, run_id, success=True, result=result)
            logger.info("[ZSXQ_SYNC_TASK] complete | run_id=%s", run_id)
            return result
        except Exception as exc:
            if workflow_run_id:
                _notify_workflow_sync_finished(workflow_run_id, run_id, success=False, error=str(exc))
            raise


def _auto_pdf_clean_enabled(payload):
    return _truthy(payload.get("auto_pdf_clean", False))


def _auto_audio_clean_enabled(payload):
    return _truthy(payload.get("auto_audio_clean", False))


def _truthy(value):
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _notify_workflow_sync_finished(workflow_run_id, run_id, *, success, result=None, error=None):
    try:
        from services.zsxq_workflow_service import zsxq_workflow_service
        from tasks.zsxq_workflow import advance_zsxq_workflow

        zsxq_workflow_service.record_sync_finished(
            workflow_run_id,
            run_id=run_id,
            success=success,
            result=result,
            error=error,
        )
        advance_zsxq_workflow.apply_async(
            args=[workflow_run_id, "sync_finished"],
            queue="zsxq_workflow",
            retry=False,
        )
    except Exception:
        logger.exception("[ZSXQ_SYNC_TASK] workflow notify failed | workflow=%s run_id=%s", workflow_run_id, run_id)
