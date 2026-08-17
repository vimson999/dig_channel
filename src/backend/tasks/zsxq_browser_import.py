"""Celery task for importing Chrome sidebar Knowledge Planet manifests."""

from __future__ import annotations

import logging

from celery_config import celery_app
from services.zsxq_browser_import_service import zsxq_browser_import_service
from services.zsxq_workflow_service import zsxq_workflow_service


logger = logging.getLogger(__name__)


@celery_app.task(bind=True, name="tasks.zsxq_browser_import.run_browser_import")
def run_browser_import(
    self,
    manifest_paths,
    *,
    dispatch_clean=False,
    cleaner=None,
    auto_publish=None,
    submission_id=None,
):
    """Import sidebar manifests and optionally hand them to existing cleaners."""
    from app import create_app

    app = create_app()
    with app.app_context():
        if submission_id:
            request_id = getattr(getattr(self, "request", None), "id", None)
            claim = zsxq_browser_import_service.claim_submission(
                submission_id, request_id
            )
            if not claim["claimed"]:
                return {
                    "submission_id": submission_id,
                    "status": claim["status"],
                    "replayed": True,
                    "result": claim.get("result"),
                }

        try:
            result = zsxq_browser_import_service.import_manifests(
                manifest_paths,
                dispatch_clean=bool(dispatch_clean),
            )
            if dispatch_clean:
                handoffs = {}
                for manifest in result.get("manifests") or []:
                    run_id = manifest.get("run_id")
                    kind = manifest.get("kind")
                    if not run_id:
                        continue
                    try:
                        handoffs[
                            run_id
                        ] = zsxq_workflow_service.start_browser_import_workflow(
                            run_id=run_id,
                            kind=kind,
                            manifest=manifest,
                            cleaner=cleaner or {},
                            auto_publish=auto_publish or {},
                            trigger="zsxq_browser_import",
                        )
                    except Exception as exc:
                        logger.exception(
                            "[ZSXQ_BROWSER_IMPORT] clean handoff failed | run_id=%s",
                            run_id,
                        )
                        handoffs[run_id] = {
                            "queued": False,
                            "reason": "submit_failed",
                            "error": str(exc),
                        }
                result["clean_handoffs"] = handoffs
            if submission_id:
                zsxq_browser_import_service.complete_submission(submission_id, result)
            return result
        except Exception as exc:
            if submission_id:
                try:
                    zsxq_browser_import_service.fail_submission(submission_id, str(exc))
                except Exception:
                    logger.exception(
                        "[ZSXQ_BROWSER_IMPORT] failed to persist receipt failure | submission_id=%s",
                        submission_id,
                    )
            raise


__all__ = ["run_browser_import"]
