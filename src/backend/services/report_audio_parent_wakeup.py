"""Event-first continuation for report-audio parents waiting on Publisher.

The report workflow keeps a delayed Celery delivery as a recovery fallback.
When Publisher reaches a terminal state, this service replaces that delivery
with an immediate, preassigned broker ID.  The report task entry point rejects
the superseded delayed delivery, while the compare-and-set token makes repeated
Publisher terminal events harmless.
"""

import logging
from datetime import datetime
from uuid import uuid4

from sqlalchemy.orm import load_only

from models import db
from models.task import PublicationTask
from services.runtime_guard import maybe_guarded_apply_async


logger = logging.getLogger(__name__)

REPORT_AUDIO_FEATURES = ("report_pdf_audio_workflow", "report_audio_workflow")
ACTIVE_PARENT_STATUSES = ("pending", "processing")
PUBLISHER_TERMINAL_STATUSES = ("completed", "failed")
PUBLISHER_WAIT_LABEL = "publisher_video"
WORKFLOW_QUEUE = "report_audio_workflow"
WAKE_STEP = "pub_resume_queued"
WAKE_TOKEN_PREFIX = "publisher-terminal:"
# Admission can persist one of these steps before its broker publish fails.
# They are still owned by this claim, unlike a worker that has advanced it.
RECOVERABLE_CLAIM_STEPS = (
    WAKE_STEP, "deferred_db_pressure", "deferred_backlog", "deferred_capacity",
)

CHILD_COLUMNS = (
    PublicationTask.id,
    PublicationTask.feature,
    PublicationTask.status,
    PublicationTask.current_step,
)
PARENT_COLUMNS = (
    PublicationTask.id,
    PublicationTask.celery_task_id,
    PublicationTask.feature,
    PublicationTask.status,
    PublicationTask.current_step,
    PublicationTask.updated_at,
    PublicationTask.error_message,
    PublicationTask.completed_at,
    PublicationTask.waiting_child_label,
    PublicationTask.waiting_child_id,
    PublicationTask.waiting_child_status,
    PublicationTask.waiting_child_step,
    PublicationTask.waiting_child_updated_at,
)


def wake_report_audio_parent_for_child(child_task_id: str) -> dict:
    """Immediately queue the active report parent waiting on a terminal child."""

    child_id = str(child_task_id or "").strip()
    if not child_id:
        return _result(False, "missing_child_id")

    child = (
        PublicationTask.query.options(load_only(*CHILD_COLUMNS))
        .filter(PublicationTask.id == child_id)
        .one_or_none()
    )
    if not child:
        return _result(False, "child_not_found")
    if str(child.feature or "") != "publisher":
        return _result(False, "child_not_publisher")

    child_status = str(child.status or "")
    child_step = str(child.current_step or "")
    if child_status not in PUBLISHER_TERMINAL_STATUSES:
        return _result(False, "child_not_terminal")

    parent = (
        db.session.query(*PARENT_COLUMNS)
        .filter(PublicationTask.feature.in_(REPORT_AUDIO_FEATURES))
        .filter(PublicationTask.status.in_(ACTIVE_PARENT_STATUSES))
        .filter(PublicationTask.waiting_child_label == PUBLISHER_WAIT_LABEL)
        .filter(PublicationTask.waiting_child_id == child_id)
        .order_by(PublicationTask.updated_at.desc())
        .first()
    )
    if not parent:
        return _result(False, "no_waiting_parent")

    parent_id = str(parent.id)
    expected_token = str(parent.waiting_child_updated_at or "")
    if (
        str(parent.current_step or "") == WAKE_STEP
        or expected_token.startswith(WAKE_TOKEN_PREFIX)
    ):
        return _result(False, "already_woken", parent_task_id=parent_id)
    if not expected_token:
        return _result(False, "missing_wait_token", parent_task_id=parent_id)

    # Candidate discovery and duplicate events must not materialize audit JSON.
    # Only the selected, eligible parent needs the exact rollback snapshot.
    payload = (
        db.session.query(PublicationTask.result)
        .filter(PublicationTask.id == parent_id)
        .filter(PublicationTask.updated_at == parent.updated_at)
        .with_for_update()
        .one_or_none()
    )
    if payload is None:
        return _result(False, "parent_changed", parent_task_id=parent_id)

    old_state = {
        "celery_task_id": parent.celery_task_id,
        "result": dict(payload.result or {}),
        "status": parent.status,
        "current_step": parent.current_step,
        "error_message": parent.error_message,
        "completed_at": parent.completed_at,
        "waiting_child_status": parent.waiting_child_status,
        "waiting_child_step": parent.waiting_child_step,
        "waiting_child_updated_at": parent.waiting_child_updated_at,
    }
    broker_task_id = str(uuid4())
    wake_token = f"{WAKE_TOKEN_PREFIX}{broker_task_id}"
    claimed_result = dict(old_state["result"])
    claimed_wait = dict(claimed_result.get("waiting_child") or {})
    claimed_wait.update(
        {
            "label": PUBLISHER_WAIT_LABEL,
            "child_id": child_id,
            "status": child_status,
            "step": child_step,
            "updated_at": wake_token,
        }
    )
    claimed_result["waiting_child"] = claimed_wait
    parent_feature = str(parent.feature or REPORT_AUDIO_FEATURES[0])
    now = datetime.utcnow()
    claimed = (
        PublicationTask.query
        .filter(PublicationTask.id == parent_id)
        .filter(PublicationTask.feature.in_(REPORT_AUDIO_FEATURES))
        .filter(PublicationTask.status.in_(ACTIVE_PARENT_STATUSES))
        .filter(PublicationTask.waiting_child_label == PUBLISHER_WAIT_LABEL)
        .filter(PublicationTask.waiting_child_id == child_id)
        .filter(PublicationTask.waiting_child_updated_at == expected_token)
        .filter(PublicationTask.celery_task_id == old_state["celery_task_id"])
        .update(
            {
                PublicationTask.celery_task_id: broker_task_id,
                PublicationTask.current_step: WAKE_STEP,
                PublicationTask.result: claimed_result,
                PublicationTask.waiting_child_status: child_status,
                PublicationTask.waiting_child_step: child_step,
                PublicationTask.waiting_child_updated_at: wake_token,
                PublicationTask.updated_at: now,
            },
            synchronize_session=False,
        )
    )
    db.session.commit()
    if not claimed:
        return _result(False, "already_woken", parent_task_id=parent_id)

    try:
        from tasks.report_audio_workflow import process_report_audio_workflow_job

        queued = maybe_guarded_apply_async(
            process_report_audio_workflow_job,
            args=[parent_id],
            queue=WORKFLOW_QUEUE,
            feature=parent_feature,
            task_id=parent_id,
            broker_task_id=broker_task_id,
            retry=False,
        )
        async_result = queued["async_result"]
        actual_task_id = str(getattr(async_result, "id", "") or "")
        if actual_task_id != broker_task_id:
            raise RuntimeError(
                "Report-audio wake-up broker ID mismatch: "
                f"expected={broker_task_id} actual={actual_task_id}"
            )
    except Exception:
        db.session.rollback()
        _restore_delayed_delivery(
            parent_id=parent_id,
            child_id=child_id,
            broker_task_id=broker_task_id,
            wake_token=wake_token,
            old_state=old_state,
        )
        raise

    logger.info(
        "Publisher terminal event woke report-audio parent: child=%s parent=%s delivery=%s status=%s",
        child_id,
        parent_id,
        broker_task_id,
        child_status,
    )
    return _result(
        True,
        "queued",
        parent_task_id=parent_id,
        celery_task_id=broker_task_id,
        deferred=bool(queued.get("deferred")),
    )


def _restore_delayed_delivery(*, parent_id, child_id, broker_task_id, wake_token, old_state):
    """Restore the delayed fallback only if this wake-up still owns the claim."""

    owned_claim = (
        PublicationTask.query
        .filter(PublicationTask.id == parent_id)
        .filter(PublicationTask.feature.in_(REPORT_AUDIO_FEATURES))
        .filter(PublicationTask.status.in_(ACTIVE_PARENT_STATUSES))
        .filter(PublicationTask.current_step.in_(RECOVERABLE_CLAIM_STEPS))
        .filter(PublicationTask.waiting_child_label == PUBLISHER_WAIT_LABEL)
        .filter(PublicationTask.waiting_child_id == child_id)
        .filter(PublicationTask.celery_task_id == broker_task_id)
        .filter(PublicationTask.waiting_child_updated_at == wake_token)
    )
    # Broker I/O happens outside the transaction. Another writer may append
    # audit data while it is in flight, so never restore the old whole payload.
    latest = owned_claim.with_entities(PublicationTask.result).with_for_update().one_or_none()
    restored = 0
    if latest is not None:
        restored_result = dict(latest.result or {})
        restored_wait = dict(restored_result.get("waiting_child") or {})
        old_wait = dict(old_state["result"].get("waiting_child") or {})
        for key in ("label", "child_id", "status", "step", "updated_at"):
            if key in old_wait:
                restored_wait[key] = old_wait[key]
            else:
                restored_wait.pop(key, None)
        if restored_wait or "waiting_child" in old_state["result"]:
            restored_result["waiting_child"] = restored_wait
        else:
            restored_result.pop("waiting_child", None)
        restored = owned_claim.update(
            {
                PublicationTask.celery_task_id: old_state["celery_task_id"],
                PublicationTask.current_step: old_state["current_step"],
                PublicationTask.result: restored_result,
                PublicationTask.waiting_child_status: old_state["waiting_child_status"],
                PublicationTask.waiting_child_step: old_state["waiting_child_step"],
                PublicationTask.waiting_child_updated_at: old_state["waiting_child_updated_at"],
                PublicationTask.updated_at: datetime.utcnow(),
            },
            synchronize_session=False,
        )
    db.session.commit()
    if not restored:
        logger.warning(
            "Report-audio wake-up publish failed after claim ownership changed: parent=%s child=%s",
            parent_id,
            child_id,
        )


def _result(woken, reason, **extra):
    return {"woken": bool(woken), "reason": str(reason), **extra}
