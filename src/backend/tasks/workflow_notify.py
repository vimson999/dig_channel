import logging
import os
import random
import sys

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from celery_config import celery_app


logger = logging.getLogger(__name__)


def _notification_retry_delay(retries):
    """Return one provider-level retry delay with exponential backoff + jitter."""
    retry_index = max(0, int(retries or 0))
    backoff = min(30 * (2 ** retry_index), 300)
    return backoff + random.uniform(0, 30)


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
        countdown = _notification_retry_delay(getattr(self.request, "retries", 0))
        logger.warning(
            "[WORKFLOW_NOTIFY_TASK] scheduling provider retry | retry=%s/3 countdown=%.1fs",
            int(getattr(self.request, "retries", 0) or 0) + 1,
            countdown,
        )
        raise self.retry(exc=exc, countdown=countdown, max_retries=3)
