import logging
import os
import sys

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from celery_config import celery_app


logger = logging.getLogger(__name__)


@celery_app.task(bind=True, name="tasks.workflow_notify.send_workflow_notification")
def send_workflow_notification(self, payload):
    """Send a workflow notification through the configured provider."""
    try:
        from services.workflow_notification_service import workflow_notification_service

        result = workflow_notification_service.send(payload or {})
        if result.get("sent"):
            return result
        reason = result.get("reason") or "send_failed"
        if reason in {"missing_config", "disabled"} or str(reason).startswith("unsupported_provider"):
            return result
        raise RuntimeError(f"workflow notification send failed: {result}")
    except Exception as exc:
        logger.exception("[WORKFLOW_NOTIFY_TASK] send failed: %s", exc)
        raise self.retry(exc=exc, countdown=30, max_retries=3)
