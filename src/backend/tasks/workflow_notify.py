import logging
import math
import os
import random
import sys
import uuid
from datetime import datetime

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from app import create_app
from celery_config import celery_app


logger = logging.getLogger(__name__)


def _notification_retry_delay(retries):
    """Legacy-compatible delay helper; retries are now provider-level."""
    retry_index = max(0, int(retries or 0))
    backoff = min(30 * (2 ** retry_index), 300)
    return backoff + random.uniform(0, 30)


def _seconds_until(value):
    if not value:
        return 0
    try:
        target = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return 0
    return max(0, math.ceil((target - datetime.utcnow()).total_seconds()))


def _delivery_lease_seconds(settings):
    """Keep the provider lease alive for the full bounded SMTP operation."""

    settings = settings if isinstance(settings, dict) else {}
    try:
        timeout = max(1, int(settings.get("timeout") or 12))
    except (TypeError, ValueError):
        timeout = 12
    protocol_attempts = 2 if (
        settings.get("fallback_starttls")
        and int(settings.get("port") or 0) == 465
    ) else 1
    # Connect, authenticate, send, and close may each consume one socket
    # timeout.  Only an explicit protocol mismatch can consume the second
    # protocol budget.  Cap crash recovery at fifteen minutes.
    return min(900, max(45, (timeout * 4 * protocol_attempts) + 30))


def _schedule_provider_drain(provider, *, countdown=0):
    options = {
        "args": [str(provider or "email")],
        "queue": "workflow_notify",
    }
    if countdown:
        options["countdown"] = max(1, int(countdown))
    return send_workflow_notification.apply_async(**options)


def reconcile_workflow_notifications_once():
    """Compensate for broker dispatch loss without holding a DB session."""

    from services.workflow_notification_outbox import due_provider_drains

    candidates = due_provider_drains()
    items = []
    scheduled = 0
    for candidate in candidates:
        provider = str(candidate.get("provider") or "email")
        item = dict(candidate)
        try:
            result = _schedule_provider_drain(provider)
            item["scheduled"] = True
            item["celery_task_id"] = getattr(result, "id", None)
            scheduled += 1
        except Exception as exc:
            item["scheduled"] = False
            item["error"] = str(exc)
            logger.exception(
                "[WORKFLOW_NOTIFY_RECONCILE] broker dispatch failed | provider=%s",
                provider,
            )
        items.append(item)
    return {
        "scheduled": scheduled,
        "due_count": sum(int(item.get("due_count") or 0) for item in candidates),
        "items": items,
    }


@celery_app.task(bind=True, name="tasks.workflow_notify.send_workflow_notification")
def send_workflow_notification(self, delivery_ref="email"):
    """Drain one durable event while holding the provider's global send permit."""
    # Notification delivery only needs configured extensions and a DB context.
    # Avoid importing the whole mutable Web API graph inside every long-lived
    # worker task: a source edit between worker restarts can otherwise mix old
    # cached service modules with newly imported route modules.
    app = create_app(register_apis=False)
    with app.app_context():
        from services.workflow_notification_outbox import (
            acquire_delivery,
            complete_delivery,
            enqueue_delivery,
            fail_delivery,
            has_open_delivery,
        )
        from services.workflow_notification_service import workflow_notification_service

        provider = "email"
        if isinstance(delivery_ref, dict):
            legacy_payload = dict(delivery_ref)
            provider = str(legacy_payload.get("provider") or "email")
            event_type = str(legacy_payload.get("event_type") or "workflow")
            workflow = (
                legacy_payload.get("workflow")
                if isinstance(legacy_payload.get("workflow"), dict)
                else {}
            )
            enqueue_delivery(
                provider=provider,
                event_type=event_type,
                workflow=workflow,
                payload=legacy_payload,
            )
        else:
            provider = str(delivery_ref or "email")

        request_id = str(getattr(self.request, "id", None) or uuid.uuid4())
        owner = f"{request_id}:{uuid.uuid4().hex[:8]}"[:100]
        try:
            acquired = acquire_delivery(
                provider,
                owner,
                lease_seconds=_delivery_lease_seconds(
                    workflow_notification_service.email_settings()
                ),
            )
        except Exception as exc:
            logger.exception(
                "[WORKFLOW_NOTIFY_TASK] outbox lease failed | provider=%s error=%s",
                provider,
                exc,
            )
            try:
                _schedule_provider_drain(provider, countdown=60)
            except Exception:
                logger.exception(
                    "[WORKFLOW_NOTIFY_TASK] failed to schedule DB recovery drain | provider=%s",
                    provider,
                )
            return {
                "sent": False,
                "pending": True,
                "reason": "outbox_unavailable",
                "provider": provider,
            }

        if not acquired.get("acquired"):
            logger.info(
                "[WORKFLOW_NOTIFY_TASK] drain skipped | provider=%s reason=%s retry_at=%s",
                provider,
                acquired.get("reason"),
                acquired.get("retry_at"),
            )
            return {
                "sent": False,
                "pending": acquired.get("reason")
                in {"provider_busy", "circuit_open"},
                "reason": acquired.get("reason"),
                "retry_at": acquired.get("retry_at"),
                "provider": provider,
            }

        delivery = acquired["delivery"]
        payload = dict(delivery.get("payload") or {})
        payload["delivery_id"] = delivery["id"]
        payload["event_id"] = delivery["id"]
        try:
            result = workflow_notification_service.send(payload)
            if not result.get("sent"):
                reason = str(result.get("reason") or "send_failed")
                raise RuntimeError(reason)
        except Exception as exc:
            transient = workflow_notification_service._is_transient_email_error(exc)
            failed = fail_delivery(
                delivery["id"],
                owner,
                error=exc,
                transient=transient,
            )
            retry_at = (failed.get("provider") or {}).get("open_until")
            logger.exception(
                "[WORKFLOW_NOTIFY_TASK] durable send failed | delivery=%s provider=%s "
                "transient=%s status=%s retry_at=%s",
                delivery["id"],
                provider,
                transient,
                (failed.get("delivery") or {}).get("status"),
                retry_at,
            )
            if transient:
                try:
                    _schedule_provider_drain(
                        provider,
                        countdown=max(1, _seconds_until(retry_at)),
                    )
                except Exception:
                    logger.exception(
                        "[WORKFLOW_NOTIFY_TASK] recovery drain scheduling failed | "
                        "delivery=%s provider=%s",
                        delivery["id"],
                        provider,
                    )
            return {
                "sent": False,
                "pending": transient,
                "reason": "provider_transient" if transient else "dead_letter",
                "delivery_id": delivery["id"],
                "retry_at": retry_at,
            }

        completed = complete_delivery(delivery["id"], owner)
        logger.info(
            "[WORKFLOW_NOTIFY_TASK] durable delivery sent | delivery=%s event=%s provider=%s",
            delivery["id"],
            delivery.get("event_type"),
            provider,
        )
        if has_open_delivery(provider):
            try:
                _schedule_provider_drain(provider)
            except Exception:
                logger.exception(
                    "[WORKFLOW_NOTIFY_TASK] next drain scheduling failed | provider=%s",
                    provider,
                )
        return {
            **result,
            "delivery_id": delivery["id"],
            "delivery_status": (completed.get("delivery") or {}).get("status"),
        }
