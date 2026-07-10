import base64
import json
import logging
import os
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, Iterable, Optional, Tuple

import redis
from sqlalchemy import or_
from sqlalchemy.orm import load_only
from sqlalchemy.orm.attributes import flag_modified

from celery_config import celery_app
from models import db
from models.task import PublicationTask
from services.runtime_guard import maybe_guarded_apply_async
from tasks.publisher import PLATFORM_UPLOAD_QUEUE, run_publication_pipeline


logger = logging.getLogger(__name__)

DEFAULT_ORPHAN_GRACE_SECONDS = 180
DEFAULT_ORPHAN_REQUEUE_LIMIT = 3
DEFAULT_INSPECT_TIMEOUT_SECONDS = 1.5
DEFAULT_QUEUE_SCAN_LIMIT = 1000
REPAIR_RESULT_KEY = "queue_repair"
CHILD_CLEANUP_RESULT_KEY = "orphan_child_cleanup"
PROCESSING_REPAIR_RESULT_KEY = "processing_orphan_repair"
ACTIVE_TASK_STATUSES = {"pending", "processing"}
TERMINAL_PARENT_STATUSES = {"completed", "failed", "blocked_by_captcha", "canceled"}
PUBLISHER_PARENT_FEATURES = {
    "mp3_video_workflow",
    "report_audio_workflow",
    "report_pdf_audio_workflow",
}
PUBLISHER_PROCESSING_REQUEUEABLE_STEPS = {
    "",
    "init",
    "prepare",
    "synthesis",
    "v_synthesis",
    "v_synthesis_2",
    "thumbnail",
}
PUBLISHER_PROCESSING_UPLOAD_STEPS = {
    "upload",
    "upload_bilibili",
    "upload_youtube",
}
PUBLISHER_PENDING_REPAIR_STEPS = {
    "",
    "init",
    "queued",
    "deferred_capacity",
    "deferred_backlog",
    "deferred_db_pressure",
}
REPAIRABLE_FEATURES = {
    "mp3_video_workflow": "mp3_video_workflow",
    "report_script_material_workflow": "report_script_material_workflow",
    "report_top10_workflow": "report_top10_workflow",
    "content_pdf_clean": "content_pipeline",
    "content_audio_clean": "content_pipeline",
    "zsxq_clean_output_publish": "zsxq_publish",
}
BUSINESS_PROCESSING_REPAIRABLE_FEATURES = {
    "mp3_video_workflow": {
        "queue": "mp3_video_workflow",
        "requeueable_steps": {
            "",
            "queued",
            "init",
            "prepare_audio",
            "gemini_upload_audio",
            "gemini_reused",
            "deepseek_plan",
            "deepseek_plan_reused",
            "generate_visual_assets",
            "generate_visual_assets_reused",
            "generate_video_keyframes",
            "video_keyframes_reused",
            "submit_landscape_video",
            "landscape_video_waiting",
            "landscape_video_wait_existing",
            "landscape_video_reused",
            "submit_vertical_video",
            "vertical_video_waiting",
            "vertical_video_wait_existing",
            "vertical_video_reused",
        },
        "requeueable_prefixes": ("landscape_video_", "vertical_video_"),
    },
}
REPAIRABLE_PENDING_STEPS = {
    "",
    "init",
    "queued",
    "deferred_capacity",
    "deferred_backlog",
    "deferred_db_pressure",
}
PUBLISHER_REPAIR_SCAN_COLUMNS = (
    PublicationTask.id,
    PublicationTask.celery_task_id,
    PublicationTask.status,
    PublicationTask.current_step,
    PublicationTask.progress,
    PublicationTask.params,
    PublicationTask.result,
    PublicationTask.error_message,
    PublicationTask.feature,
    PublicationTask.parent_task_id,
    PublicationTask.source_task_id,
    PublicationTask.created_at,
    PublicationTask.updated_at,
    PublicationTask.completed_at,
)
PUBLISHER_REPAIR_CANDIDATE_COLUMNS = tuple(
    column
    for column in PUBLISHER_REPAIR_SCAN_COLUMNS
    if column.key not in {"params", "result"}
)
PUBLISHER_PARENT_CLEANUP_SCAN_COLUMNS = (
    PublicationTask.id,
    PublicationTask.celery_task_id,
    PublicationTask.status,
    PublicationTask.current_step,
    PublicationTask.progress,
    PublicationTask.error_message,
    PublicationTask.feature,
    PublicationTask.params,
    PublicationTask.parent_task_id,
    PublicationTask.source_task_id,
    PublicationTask.created_at,
    PublicationTask.updated_at,
    PublicationTask.completed_at,
)
PUBLISHER_PARENT_CLEANUP_CANDIDATE_COLUMNS = tuple(
    column
    for column in PUBLISHER_PARENT_CLEANUP_SCAN_COLUMNS
    if column.key != "params"
)
PARENT_STATUS_COLUMNS = (
    PublicationTask.id,
    PublicationTask.feature,
    PublicationTask.status,
)


def _load_repair_task(task_id, *columns):
    return (
        PublicationTask.query.options(load_only(*(columns or PUBLISHER_REPAIR_SCAN_COLUMNS)))
        .filter(PublicationTask.id == str(task_id))
        .one_or_none()
    )


def maybe_requeue_stale_publisher_child(
    child: PublicationTask,
    *,
    queue: str = "render",
    log: Optional[logging.Logger] = None,
) -> Tuple[PublicationTask, bool]:
    """Requeue a stale publisher child if DB says queued but Celery cannot see it.

    This is intentionally conservative: it only touches old pending/queued
    publisher tasks, and it requires both worker inspect and queue scan to
    succeed before deciding the message is orphaned.
    """
    active_logger = log or logger
    if not _truthy_env("PUBLISHER_CHILD_QUEUE_REPAIR_ENABLED", True):
        return child, False
    if not child or getattr(child, "feature", None) != "publisher":
        return child, False
    if (child.status or "").lower() != "pending":
        return child, False
    if (child.current_step or "queued").lower() not in PUBLISHER_PENDING_REPAIR_STEPS:
        return child, False

    grace_seconds = _int_env("PUBLISHER_CHILD_ORPHAN_GRACE_SECONDS", DEFAULT_ORPHAN_GRACE_SECONDS, 30)
    reference_time = child.updated_at or child.created_at
    if _age_seconds(reference_time) < grace_seconds:
        return child, False

    result = dict(child.result or {})
    repair_state = dict(result.get(REPAIR_RESULT_KEY) or {})
    requeue_limit = _int_env("PUBLISHER_CHILD_ORPHAN_REQUEUE_LIMIT", DEFAULT_ORPHAN_REQUEUE_LIMIT, 1)
    requeue_count = int(repair_state.get("count") or 0)
    if requeue_count >= requeue_limit:
        active_logger.warning(
            "Publisher child queue repair skipped after limit: child=%s count=%s limit=%s",
            child.id,
            requeue_count,
            requeue_limit,
        )
        return child, False

    requeue_target, target_queue = _publisher_child_repair_target(child, default_queue=queue)
    previous_celery_id = child.celery_task_id
    if previous_celery_id:
        worker_visible, worker_checked = _message_visible_in_workers(previous_celery_id, child_id=child.id)
        if not worker_checked or worker_visible:
            return child, False
        queue_visible, queue_checked = _message_visible_in_queue(previous_celery_id, child_id=child.id, queue=target_queue)
        if not queue_checked:
            return child, False
    else:
        queue_visible = False

    reason = (
        "pending_message_not_active_but_queue_visible"
        if queue_visible
        else "pending_queued_not_visible_in_celery"
    )

    if not queue_visible and previous_celery_id:
        logger.info(
            "Publisher pending task message is absent from workers and queue: child=%s celery=%s",
            child.id,
            previous_celery_id,
        )
    elif queue_visible:
        logger.warning(
            "Publisher pending task message is queued/unacked but not active; requeueing with a fresh celery id: child=%s celery=%s",
            child.id,
            previous_celery_id,
        )

    if previous_celery_id and worker_visible:
        return child, False

    try:
        async_result = requeue_target.apply_async(args=[child.id], queue=target_queue, retry=False)
    except Exception as exc:
        active_logger.warning(
            "Publisher child queue repair publish failed: child=%s previous_celery_id=%s error=%s",
            child.id,
            previous_celery_id,
            exc,
        )
        return child, False
    now = datetime.utcnow().isoformat() + "Z"

    repair_state.update({
        "count": requeue_count + 1,
        "last_requeued_at": now,
        "previous_celery_id": previous_celery_id,
        "current_celery_id": async_result.id,
        "queue": target_queue,
        "reason": reason,
        "previous_message_visible_in_queue": bool(queue_visible),
    })
    history = list(repair_state.get("history") or [])
    history.append({
        "at": now,
        "previous_celery_id": previous_celery_id,
        "new_celery_id": async_result.id,
    })
    repair_state["history"] = history[-10:]

    result[REPAIR_RESULT_KEY] = repair_state
    result.pop("error", None)
    result.pop("error_type", None)
    result.pop("completed_at", None)

    child.celery_task_id = async_result.id
    child.status = "pending"
    child.current_step = "queued"
    child.progress = 0
    child.error_message = None
    child.completed_at = None
    child.result = result
    flag_modified(child, "result")
    db.session.commit()

    active_logger.warning(
        "Requeued orphan publisher child: child=%s previous_celery_id=%s new_celery_id=%s",
        child.id,
        previous_celery_id,
        async_result.id,
    )
    return child, True


def repair_stale_publisher_pending_tasks_once(
    *,
    limit: Optional[int] = None,
    log: Optional[logging.Logger] = None,
) -> Dict[str, Any]:
    """Scan and requeue stale pending publisher tasks when their message vanished."""
    active_logger = log or logger
    if not _truthy_env("PUBLISHER_CHILD_QUEUE_REPAIR_ENABLED", True):
        return {"enabled": False, "checked": 0, "requeued": 0, "items": []}

    scan_limit = limit or _int_env("PUBLISHER_PENDING_QUEUE_REPAIR_SCAN_LIMIT", 50, 1)
    rows = (
        db.session.query(*PUBLISHER_REPAIR_CANDIDATE_COLUMNS)
        .filter(PublicationTask.feature == "publisher")
        .filter(PublicationTask.status == "pending")
        .filter(PublicationTask.current_step.in_(tuple(PUBLISHER_PENDING_REPAIR_STEPS)))
        .order_by(PublicationTask.updated_at.asc(), PublicationTask.created_at.asc())
        .limit(scan_limit)
        .all()
    )

    items = []
    requeued = 0
    for row in rows:
        previous_celery_id = row.celery_task_id
        previous_step = row.current_step
        task = _load_repair_task(row.id, *PUBLISHER_REPAIR_SCAN_COLUMNS)
        if not task:
            continue
        try:
            updated, did_requeue = maybe_requeue_stale_publisher_child(task, log=active_logger)
        except Exception as exc:
            db.session.rollback()
            active_logger.exception("Publisher pending queue repair failed: task=%s", getattr(row, "id", "-"))
            items.append({
                "task_id": getattr(row, "id", None),
                "requeued": False,
                "error": str(exc),
            })
            continue
        if not did_requeue:
            continue
        requeued += 1
        _target, target_queue = _publisher_child_repair_target(updated, default_queue="render")
        items.append({
            "task_id": updated.id,
            "queue": target_queue,
            "previous_celery_id": previous_celery_id,
            "new_celery_id": updated.celery_task_id,
            "previous_step": previous_step,
            "requeued": True,
        })

    return {
        "enabled": True,
        "checked": len(rows),
        "requeued": requeued,
        "items": items,
    }


def cleanup_publisher_children_of_terminal_parents_once(
    *,
    limit: Optional[int] = None,
    log: Optional[logging.Logger] = None,
) -> Dict[str, Any]:
    """Cancel active publisher children whose parent workflow already ended.

    A publisher child is meaningful only while its workflow parent is still
    alive. If the parent is canceled/failed/completed and the child remains
    pending or processing, the monitor will otherwise report a phantom
    "business in progress" task forever.
    """
    active_logger = log or logger
    if not _truthy_env("PUBLISHER_CHILD_PARENT_CLEANUP_ENABLED", True):
        return {"enabled": False, "checked": 0, "cleaned": 0, "items": []}

    scan_limit = limit or _int_env("PUBLISHER_CHILD_PARENT_CLEANUP_SCAN_LIMIT", 50, 1)
    rows = (
        db.session.query(*PUBLISHER_PARENT_CLEANUP_CANDIDATE_COLUMNS)
        .filter(PublicationTask.feature == "publisher")
        .filter(PublicationTask.status.in_(tuple(ACTIVE_TASK_STATUSES)))
        .filter(or_(PublicationTask.parent_task_id.isnot(None), PublicationTask.source_task_id.isnot(None)))
        .order_by(PublicationTask.updated_at.asc(), PublicationTask.created_at.asc())
        .limit(scan_limit)
        .all()
    )

    items = []
    cleaned = 0
    for row in rows:
        parent_id = _publisher_child_parent_id(row)
        if not parent_id:
            continue
        parent = (
            PublicationTask.query
            .options(load_only(*PARENT_STATUS_COLUMNS))
            .filter(PublicationTask.id == parent_id)
            .first()
        )
        if not parent:
            continue
        parent_feature = str(parent.feature or "")
        if parent_feature not in PUBLISHER_PARENT_FEATURES:
            continue
        parent_status = str(parent.status or "").lower()
        if parent_status not in TERMINAL_PARENT_STATUSES:
            continue
        child = _load_repair_task(row.id, *PUBLISHER_PARENT_CLEANUP_SCAN_COLUMNS)
        if not child:
            continue
        if parent_status == "completed" and _publisher_child_is_post_completion_upload(child):
            continue

        try:
            _cancel_orphan_publisher_child(child, parent=parent)
        except Exception as exc:
            db.session.rollback()
            active_logger.exception(
                "Publisher child parent cleanup failed: child=%s parent=%s",
                getattr(child, "id", "-"),
                parent_id,
            )
            items.append({
                "task_id": getattr(child, "id", None),
                "parent_task_id": parent_id,
                "parent_status": parent_status,
                "cleaned": False,
                "error": str(exc),
            })
            continue

        cleaned += 1
        items.append({
            "task_id": child.id,
            "parent_task_id": parent.id,
            "parent_status": parent_status,
            "cleaned": True,
        })
    return {
        "enabled": True,
        "checked": len(rows),
        "cleaned": cleaned,
        "items": items,
    }


def maybe_repair_stale_publisher_processing_task(
    task: PublicationTask,
    *,
    queue: str = "render",
    log: Optional[logging.Logger] = None,
) -> Tuple[PublicationTask, bool, str]:
    """Repair a publisher row stuck in processing when Celery no longer sees it."""
    active_logger = log or logger
    if not _truthy_env("PUBLISHER_PROCESSING_ORPHAN_REPAIR_ENABLED", True):
        return task, False, "disabled"
    if not task or getattr(task, "feature", None) != "publisher":
        return task, False, "not_publisher"
    if (task.status or "").lower() != "processing":
        return task, False, "not_processing"

    step = str(task.current_step or "").lower()
    grace_seconds = _publisher_processing_orphan_grace_seconds(step)
    reference_time = task.updated_at or task.created_at
    if _age_seconds(reference_time) < grace_seconds:
        return task, False, "within_grace"

    requeue_target, target_queue = _publisher_child_repair_target(task, default_queue=queue)
    worker_visible, worker_checked = _message_visible_in_workers(task.celery_task_id, child_id=task.id)
    if not worker_checked:
        return task, False, "visibility_unknown"
    if worker_visible:
        return task, False, "visible_in_worker"

    if step in PUBLISHER_PROCESSING_UPLOAD_STEPS:
        _mark_processing_publisher_orphan_failed(task, queue=target_queue, reason="upload_step_not_requeued")
        active_logger.warning(
            "Closed orphan publisher upload task without auto-requeue: task=%s step=%s",
            task.id,
            step,
        )
        return task, True, "closed"

    queue_visible, queue_checked = _message_visible_in_queue(task.celery_task_id, child_id=task.id, queue=target_queue)
    if not queue_checked:
        return task, False, "visibility_unknown"

    if step not in PUBLISHER_PROCESSING_REQUEUEABLE_STEPS:
        _mark_processing_publisher_orphan_failed(task, queue=target_queue, reason="unknown_processing_step")
        active_logger.warning("Closed orphan publisher task with unknown step: task=%s step=%s", task.id, step)
        return task, True, "closed"

    if queue_visible:
        _demote_processing_publisher_to_pending(
            task,
            queue=target_queue,
            reason="processing_not_active_but_message_visible",
        )
        active_logger.warning(
            "Demoted orphan publisher processing task to pending: task=%s celery_id=%s step=%s",
            task.id,
            task.celery_task_id,
            step,
        )
        return task, True, "demoted"

    result = dict(task.result or {})
    repair_state = dict(result.get(PROCESSING_REPAIR_RESULT_KEY) or {})
    requeue_limit = _int_env(
        "PUBLISHER_PROCESSING_ORPHAN_REQUEUE_LIMIT",
        DEFAULT_ORPHAN_REQUEUE_LIMIT,
        1,
    )
    requeue_count = int(repair_state.get("count") or 0)
    if requeue_count >= requeue_limit:
        _mark_processing_publisher_orphan_failed(task, queue=queue, reason="requeue_limit_reached")
        active_logger.warning(
            "Closed orphan publisher task after processing repair limit: task=%s count=%s limit=%s",
            task.id,
            requeue_count,
            requeue_limit,
        )
        return task, True, "closed"

    previous_celery_id = task.celery_task_id
    previous_status = task.status
    previous_step = task.current_step
    try:
        async_result = requeue_target.apply_async(args=[task.id], queue=target_queue, retry=False)
    except Exception as exc:
        active_logger.warning(
            "Publisher processing orphan requeue failed: task=%s previous_celery_id=%s error=%s",
            task.id,
            previous_celery_id,
            exc,
        )
        return task, False, "publish_failed"

    now = datetime.utcnow().isoformat() + "Z"
    repair_state.update({
        "count": requeue_count + 1,
        "last_requeued_at": now,
        "previous_celery_id": previous_celery_id,
        "current_celery_id": async_result.id,
        "previous_status": previous_status,
        "previous_step": previous_step,
        "queue": target_queue,
        "reason": "processing_not_visible_in_celery",
    })
    history = list(repair_state.get("history") or [])
    history.append({
        "at": now,
        "previous_celery_id": previous_celery_id,
        "new_celery_id": async_result.id,
        "previous_step": previous_step,
    })
    repair_state["history"] = history[-10:]

    result[PROCESSING_REPAIR_RESULT_KEY] = repair_state
    result.pop("error", None)
    result.pop("error_type", None)
    result.pop("completed_at", None)

    task.celery_task_id = async_result.id
    task.status = "pending"
    task.current_step = "queued"
    task.progress = 0
    task.error_message = None
    task.completed_at = None
    task.result = result
    flag_modified(task, "result")
    db.session.commit()

    active_logger.warning(
        "Requeued orphan publisher processing task: task=%s previous_celery_id=%s new_celery_id=%s step=%s",
        task.id,
        previous_celery_id,
        async_result.id,
        previous_step,
    )
    return task, True, "requeued"


def repair_stale_publisher_processing_tasks_once(
    *,
    limit: Optional[int] = None,
    log: Optional[logging.Logger] = None,
) -> Dict[str, Any]:
    """Scan and repair publisher tasks stuck in processing but absent from Celery."""
    active_logger = log or logger
    if not _truthy_env("PUBLISHER_PROCESSING_ORPHAN_REPAIR_ENABLED", True):
        return {"enabled": False, "checked": 0, "requeued": 0, "demoted": 0, "closed": 0, "items": []}

    scan_limit = limit or _int_env("PUBLISHER_PROCESSING_ORPHAN_SCAN_LIMIT", 50, 1)
    scan_grace_seconds = min(
        _int_env("PUBLISHER_PROCESSING_ORPHAN_GRACE_SECONDS", 600, 120),
        _int_env("PUBLISHER_UPLOAD_PROCESSING_ORPHAN_GRACE_SECONDS", 90, 30),
    )
    cutoff = datetime.utcnow() - timedelta(seconds=scan_grace_seconds)
    rows = (
        db.session.query(*PUBLISHER_REPAIR_CANDIDATE_COLUMNS)
        .filter(PublicationTask.feature == "publisher")
        .filter(PublicationTask.status == "processing")
        .filter(or_(PublicationTask.updated_at == None, PublicationTask.updated_at <= cutoff))  # noqa: E711
        .order_by(PublicationTask.updated_at.asc(), PublicationTask.created_at.asc())
        .limit(scan_limit)
        .all()
    )

    items = []
    requeued = 0
    demoted = 0
    closed = 0
    for row in rows:
        previous_celery_id = row.celery_task_id
        previous_step = row.current_step
        task = _load_repair_task(row.id, *PUBLISHER_REPAIR_SCAN_COLUMNS)
        if not task:
            continue
        try:
            updated, did_repair, action = maybe_repair_stale_publisher_processing_task(task, log=active_logger)
        except Exception as exc:
            db.session.rollback()
            active_logger.exception(
                "Publisher processing orphan repair failed: task=%s",
                getattr(row, "id", "-"),
            )
            items.append({
                "task_id": getattr(row, "id", None),
                "repaired": False,
                "action": "error",
                "error": str(exc),
            })
            continue
        if not did_repair:
            continue
        if action == "requeued":
            requeued += 1
        elif action == "demoted":
            demoted += 1
        elif action == "closed":
            closed += 1
        _target, target_queue = _publisher_child_repair_target(updated, default_queue="render")
        items.append({
            "task_id": updated.id,
            "queue": target_queue,
            "previous_celery_id": previous_celery_id,
            "new_celery_id": updated.celery_task_id,
            "previous_step": previous_step,
            "action": action,
            "repaired": True,
        })

    return {
        "enabled": True,
        "checked": len(rows),
        "requeued": requeued,
        "demoted": demoted,
        "closed": closed,
        "items": items,
    }


def maybe_requeue_stale_publication_task(
    task: PublicationTask,
    *,
    log: Optional[logging.Logger] = None,
) -> Tuple[PublicationTask, bool]:
    """Requeue a stale pending business task when its Celery message vanished.

    This is deliberately narrower than a generic retry: it only touches pending
    tasks whose previous Celery id is absent from worker snapshots and Redis.
    The same PublicationTask id is requeued, so downstream idempotency and
    workflow references stay intact.
    """
    active_logger = log or logger
    if not _truthy_env("BUSINESS_TASK_QUEUE_REPAIR_ENABLED", True):
        return task, False
    if not task:
        return task, False

    feature = str(getattr(task, "feature", "") or "")
    queue = REPAIRABLE_FEATURES.get(feature)
    if not queue:
        return task, False
    if (task.status or "").lower() != "pending":
        return task, False
    if (task.current_step or "").lower() not in REPAIRABLE_PENDING_STEPS:
        return task, False

    grace_seconds = _int_env(
        "BUSINESS_TASK_QUEUE_REPAIR_GRACE_SECONDS",
        DEFAULT_ORPHAN_GRACE_SECONDS,
        30,
    )
    reference_time = task.updated_at or task.created_at
    if _age_seconds(reference_time) < grace_seconds:
        return task, False

    result = dict(task.result or {})
    repair_state = dict(result.get(REPAIR_RESULT_KEY) or {})
    requeue_limit = _int_env(
        "BUSINESS_TASK_QUEUE_REPAIR_LIMIT",
        DEFAULT_ORPHAN_REQUEUE_LIMIT,
        1,
    )
    requeue_count = int(repair_state.get("count") or 0)

    visible, checked = _celery_message_visible(task.celery_task_id, child_id=task.id, queue=queue)
    if not checked or visible:
        return task, False

    if requeue_count >= requeue_limit:
        _mark_business_task_queue_repair_limit_failed(
            task,
            queue=queue,
            feature=feature,
            requeue_count=requeue_count,
            requeue_limit=requeue_limit,
        )
        active_logger.warning(
            "Closed orphan business task after queue repair limit: task=%s feature=%s count=%s limit=%s",
            task.id,
            feature,
            requeue_count,
            requeue_limit,
        )
        return task, False

    celery_callable = _repair_target_for_feature(feature)
    if celery_callable is None:
        return task, False

    previous_celery_id = task.celery_task_id
    try:
        submit_result = maybe_guarded_apply_async(
            celery_callable,
            args=[task.id],
            queue=queue,
            feature=feature,
            task=task,
            retry=False,
        )
        async_result = submit_result["async_result"]
    except Exception as exc:
        active_logger.warning(
            "Business task queue repair publish failed: task=%s feature=%s previous_celery_id=%s error=%s",
            task.id,
            feature,
            previous_celery_id,
            exc,
        )
        return task, False

    now = datetime.utcnow().isoformat() + "Z"
    latest_result = dict(task.result or {})
    repair_state = dict(latest_result.get(REPAIR_RESULT_KEY) or repair_state)
    repair_state.update({
        "count": requeue_count + 1,
        "last_requeued_at": now,
        "previous_celery_id": previous_celery_id,
        "current_celery_id": async_result.id,
        "queue": queue,
        "feature": feature,
        "reason": "pending_not_visible_in_celery",
        "admission": submit_result.get("admission"),
        "deferred": bool(submit_result.get("deferred")),
        "countdown": submit_result.get("countdown"),
    })
    history = list(repair_state.get("history") or [])
    history.append({
        "at": now,
        "feature": feature,
        "queue": queue,
        "previous_celery_id": previous_celery_id,
        "new_celery_id": async_result.id,
        "deferred": bool(submit_result.get("deferred")),
    })
    repair_state["history"] = history[-10:]

    latest_result[REPAIR_RESULT_KEY] = repair_state
    latest_result["celery_task_id"] = async_result.id
    latest_result["queued"] = not bool(submit_result.get("deferred"))
    latest_result["deferred"] = bool(submit_result.get("deferred"))
    if not submit_result.get("deferred"):
        latest_result.pop("error", None)
        latest_result.pop("error_type", None)
        latest_result.pop("completed_at", None)

    task.celery_task_id = async_result.id
    task.status = "pending"
    if not submit_result.get("deferred"):
        task.current_step = "queued"
        task.progress = 0
        task.error_message = None
    task.completed_at = None
    task.result = latest_result
    flag_modified(task, "result")
    db.session.commit()

    active_logger.warning(
        "Requeued orphan business task: task=%s feature=%s previous_celery_id=%s new_celery_id=%s deferred=%s",
        task.id,
        feature,
        previous_celery_id,
        async_result.id,
        bool(submit_result.get("deferred")),
    )
    return task, True


def repair_stale_publication_tasks_once(
    *,
    limit: Optional[int] = None,
    excluded_features: Optional[Iterable[str]] = None,
    log: Optional[logging.Logger] = None,
) -> Dict[str, Any]:
    """Scan and requeue stale pending PublicationTask rows once."""
    active_logger = log or logger
    if not _truthy_env("BUSINESS_TASK_QUEUE_REPAIR_ENABLED", True):
        return {"enabled": False, "checked": 0, "requeued": 0, "items": []}

    scan_limit = limit or _int_env("BUSINESS_TASK_QUEUE_REPAIR_SCAN_LIMIT", 50, 1)
    excluded = {str(item) for item in (excluded_features or []) if item}
    features = [item for item in REPAIRABLE_FEATURES.keys() if item not in excluded]
    if not features:
        return {"enabled": True, "checked": 0, "requeued": 0, "items": [], "skipped": "all_features_paused"}

    rows = (
        db.session.query(*PUBLISHER_REPAIR_CANDIDATE_COLUMNS)
        .filter(PublicationTask.feature.in_(tuple(features)))
        .filter(PublicationTask.status == "pending")
        .order_by(PublicationTask.updated_at.asc(), PublicationTask.created_at.asc())
        .limit(scan_limit)
        .all()
    )
    items = []
    requeued = 0
    for row in rows:
        previous_celery_id = row.celery_task_id
        task = _load_repair_task(row.id, *PUBLISHER_REPAIR_SCAN_COLUMNS)
        if not task:
            continue
        try:
            updated, did_requeue = maybe_requeue_stale_publication_task(task, log=active_logger)
        except Exception as exc:
            db.session.rollback()
            active_logger.exception("Business task queue repair failed: task=%s", getattr(row, "id", "-"))
            items.append({
                "task_id": getattr(row, "id", None),
                "feature": getattr(row, "feature", None),
                "requeued": False,
                "error": str(exc),
            })
            continue
        if did_requeue:
            requeued += 1
            items.append({
                "task_id": updated.id,
                "feature": updated.feature,
                "queue": REPAIRABLE_FEATURES.get(updated.feature),
                "previous_celery_id": previous_celery_id,
                "new_celery_id": updated.celery_task_id,
                "requeued": True,
            })
    return {
        "enabled": True,
        "checked": len(rows),
        "requeued": requeued,
        "items": items,
    }


def maybe_repair_stale_business_processing_task(
    task: PublicationTask,
    *,
    log: Optional[logging.Logger] = None,
) -> Tuple[PublicationTask, bool, str]:
    """Requeue a processing business task when Celery no longer owns it."""
    active_logger = log or logger
    if not _truthy_env("BUSINESS_PROCESSING_ORPHAN_REPAIR_ENABLED", True):
        return task, False, "disabled"
    if not task:
        return task, False, "missing_task"

    feature = str(getattr(task, "feature", "") or "")
    config = BUSINESS_PROCESSING_REPAIRABLE_FEATURES.get(feature)
    if not config:
        return task, False, "unsupported_feature"
    if (task.status or "").lower() != "processing":
        return task, False, "not_processing"

    grace_seconds = _int_env("BUSINESS_PROCESSING_ORPHAN_GRACE_SECONDS", 600, 120)
    reference_time = task.updated_at or task.created_at
    if _age_seconds(reference_time) < grace_seconds:
        return task, False, "within_grace"

    queue = str(config.get("queue") or REPAIRABLE_FEATURES.get(feature) or feature)
    worker_visible, worker_checked = _message_visible_in_workers(task.celery_task_id, child_id=task.id)
    if not worker_checked:
        return task, False, "visibility_unknown"
    if worker_visible:
        return task, False, "visible_in_worker"
    queue_visible, queue_checked = _message_visible_in_queue(task.celery_task_id, child_id=task.id, queue=queue)
    if not queue_checked:
        return task, False, "visibility_unknown"

    step = str(task.current_step or "").lower()
    requeueable_steps = set(config.get("requeueable_steps") or ())
    requeueable_prefixes = tuple(str(item) for item in (config.get("requeueable_prefixes") or ()) if item)
    if step not in requeueable_steps and not step.startswith(requeueable_prefixes):
        return task, False, "step_not_requeueable"

    result = dict(task.result or {})
    repair_state = dict(result.get(PROCESSING_REPAIR_RESULT_KEY) or {})
    requeue_limit = _int_env(
        "BUSINESS_PROCESSING_ORPHAN_REQUEUE_LIMIT",
        DEFAULT_ORPHAN_REQUEUE_LIMIT,
        1,
    )
    requeue_count = int(repair_state.get("count") or 0)
    if requeue_count >= requeue_limit:
        _mark_business_processing_repair_limit_failed(
            task,
            queue=queue,
            feature=feature,
            requeue_count=requeue_count,
            requeue_limit=requeue_limit,
        )
        active_logger.warning(
            "Closed orphan business processing task after repair limit: task=%s feature=%s count=%s limit=%s",
            task.id,
            feature,
            requeue_count,
            requeue_limit,
        )
        return task, True, "closed"

    celery_callable = _repair_target_for_feature(feature)
    if celery_callable is None:
        return task, False, "missing_repair_target"

    previous_celery_id = task.celery_task_id
    previous_status = task.status
    previous_step = task.current_step
    try:
        submit_result = maybe_guarded_apply_async(
            celery_callable,
            args=[task.id],
            queue=queue,
            feature=feature,
            task=task,
            retry=False,
        )
        async_result = submit_result["async_result"]
    except Exception as exc:
        active_logger.warning(
            "Business processing orphan requeue failed: task=%s feature=%s previous_celery_id=%s error=%s",
            task.id,
            feature,
            previous_celery_id,
            exc,
        )
        return task, False, "publish_failed"

    now = datetime.utcnow().isoformat() + "Z"
    latest_result = dict(task.result or result)
    repair_state = dict(latest_result.get(PROCESSING_REPAIR_RESULT_KEY) or repair_state)
    repair_state.update({
        "count": requeue_count + 1,
        "last_requeued_at": now,
        "previous_celery_id": previous_celery_id,
        "current_celery_id": async_result.id,
        "previous_status": previous_status,
        "previous_step": previous_step,
        "queue": queue,
        "feature": feature,
        "reason": "processing_not_visible_in_worker",
        "previous_message_visible_in_queue": bool(queue_visible),
        "admission": submit_result.get("admission"),
        "deferred": bool(submit_result.get("deferred")),
        "countdown": submit_result.get("countdown"),
    })
    history = list(repair_state.get("history") or [])
    history.append({
        "at": now,
        "feature": feature,
        "queue": queue,
        "previous_celery_id": previous_celery_id,
        "new_celery_id": async_result.id,
        "previous_step": previous_step,
        "deferred": bool(submit_result.get("deferred")),
    })
    repair_state["history"] = history[-10:]

    latest_result[PROCESSING_REPAIR_RESULT_KEY] = repair_state
    latest_result["celery_task_id"] = async_result.id
    latest_result["queued"] = not bool(submit_result.get("deferred"))
    latest_result["deferred"] = bool(submit_result.get("deferred"))
    if not submit_result.get("deferred"):
        latest_result.pop("error", None)
        latest_result.pop("error_type", None)
        latest_result.pop("completed_at", None)

    task.celery_task_id = async_result.id
    task.status = "pending"
    if not submit_result.get("deferred"):
        task.current_step = "queued"
        task.progress = 0
        task.error_message = None
    task.completed_at = None
    task.result = latest_result
    flag_modified(task, "result")
    db.session.commit()

    active_logger.warning(
        "Requeued orphan business processing task: task=%s feature=%s previous_celery_id=%s new_celery_id=%s deferred=%s step=%s",
        task.id,
        feature,
        previous_celery_id,
        async_result.id,
        bool(submit_result.get("deferred")),
        previous_step,
    )
    return task, True, "requeued"


def repair_stale_business_processing_tasks_once(
    *,
    limit: Optional[int] = None,
    excluded_features: Optional[Iterable[str]] = None,
    log: Optional[logging.Logger] = None,
) -> Dict[str, Any]:
    """Scan and requeue stale processing business task rows once."""
    active_logger = log or logger
    if not _truthy_env("BUSINESS_PROCESSING_ORPHAN_REPAIR_ENABLED", True):
        return {"enabled": False, "checked": 0, "requeued": 0, "closed": 0, "items": []}

    excluded = {str(item) for item in (excluded_features or []) if item}
    features = [item for item in BUSINESS_PROCESSING_REPAIRABLE_FEATURES.keys() if item not in excluded]
    if not features:
        return {"enabled": True, "checked": 0, "requeued": 0, "closed": 0, "items": [], "skipped": "all_features_paused"}

    scan_limit = limit or _int_env("BUSINESS_PROCESSING_ORPHAN_SCAN_LIMIT", 50, 1)
    grace_seconds = _int_env("BUSINESS_PROCESSING_ORPHAN_GRACE_SECONDS", 600, 120)
    rows = (
        db.session.query(*PUBLISHER_REPAIR_CANDIDATE_COLUMNS)
        .filter(PublicationTask.feature.in_(tuple(features)))
        .filter(PublicationTask.status == "processing")
        .order_by(PublicationTask.updated_at.asc(), PublicationTask.created_at.asc())
        .limit(scan_limit)
        .all()
    )

    items = []
    requeued = 0
    closed = 0
    for row in rows:
        if _age_seconds(row.updated_at or row.created_at) < grace_seconds:
            continue
        previous_celery_id = row.celery_task_id
        previous_step = row.current_step
        task = _load_repair_task(row.id, *PUBLISHER_REPAIR_SCAN_COLUMNS)
        if not task:
            continue
        try:
            updated, did_repair, action = maybe_repair_stale_business_processing_task(task, log=active_logger)
        except Exception as exc:
            db.session.rollback()
            active_logger.exception("Business processing orphan repair failed: task=%s", getattr(row, "id", "-"))
            items.append({
                "task_id": getattr(row, "id", None),
                "feature": getattr(row, "feature", None),
                "repaired": False,
                "action": "error",
                "error": str(exc),
            })
            continue
        if not did_repair:
            continue
        if action == "requeued":
            requeued += 1
        elif action == "closed":
            closed += 1
        items.append({
            "task_id": updated.id,
            "feature": updated.feature,
            "queue": REPAIRABLE_FEATURES.get(updated.feature),
            "previous_celery_id": previous_celery_id,
            "new_celery_id": updated.celery_task_id,
            "previous_step": previous_step,
            "action": action,
            "repaired": True,
        })

    return {
        "enabled": True,
        "checked": len(rows),
        "requeued": requeued,
        "closed": closed,
        "items": items,
    }


def _mark_business_processing_repair_limit_failed(
    task: PublicationTask,
    *,
    queue: str,
    feature: str,
    requeue_count: int,
    requeue_limit: int,
) -> None:
    now = datetime.utcnow()
    previous_result = dict(task.result or {})
    repair_state = dict(previous_result.get(PROCESSING_REPAIR_RESULT_KEY) or {})
    repair_state.update({
        "count": requeue_count,
        "limit": requeue_limit,
        "queue": queue,
        "feature": feature,
        "reason": "processing_repair_limit_reached",
        "closed_at": now.isoformat() + "Z",
        "previous_status": task.status,
        "previous_step": task.current_step,
        "previous_celery_id": task.celery_task_id,
    })
    previous_result[PROCESSING_REPAIR_RESULT_KEY] = repair_state
    previous_result["error"] = "processing_repair_limit_reached"
    previous_result["error_type"] = "processing_repair_limit_reached"
    previous_result["completed_at"] = now.isoformat() + "Z"

    task.status = "failed"
    task.current_step = "failed"
    task.progress = task.progress or 0
    task.error_message = "任务执行占位修复已达到上限，Celery 中也找不到对应执行体，已自动标记失败。"
    task.completed_at = now
    task.result = previous_result
    flag_modified(task, "result")
    db.session.commit()


def _celery_message_visible(celery_id: Optional[str], *, child_id: str, queue: str) -> Tuple[bool, bool]:
    if not celery_id:
        return False, True

    worker_visible, worker_checked = _message_visible_in_workers(celery_id, child_id=child_id)
    queue_visible, queue_checked = _message_visible_in_queue(celery_id, child_id=child_id, queue=queue)
    checked = worker_checked and queue_checked
    return worker_visible or queue_visible, checked


def _publisher_processing_orphan_grace_seconds(step: str) -> int:
    normalized = str(step or "").strip().lower()
    if normalized in PUBLISHER_PROCESSING_UPLOAD_STEPS:
        return _int_env("PUBLISHER_UPLOAD_PROCESSING_ORPHAN_GRACE_SECONDS", 90, 30)
    return _int_env("PUBLISHER_PROCESSING_ORPHAN_GRACE_SECONDS", 600, 120)


def _mark_business_task_queue_repair_limit_failed(
    task: PublicationTask,
    *,
    queue: str,
    feature: str,
    requeue_count: int,
    requeue_limit: int,
) -> None:
    now = datetime.utcnow()
    previous_result = dict(task.result or {})
    repair_state = dict(previous_result.get(REPAIR_RESULT_KEY) or {})
    repair_state.update({
        "count": requeue_count,
        "limit": requeue_limit,
        "queue": queue,
        "feature": feature,
        "reason": "queue_repair_limit_reached",
        "closed_at": now.isoformat() + "Z",
        "previous_status": task.status,
        "previous_step": task.current_step,
        "previous_celery_id": task.celery_task_id,
    })
    previous_result[REPAIR_RESULT_KEY] = repair_state
    previous_result["error"] = "queue_repair_limit_reached"
    previous_result["error_type"] = "queue_repair_limit_reached"
    previous_result["completed_at"] = now.isoformat() + "Z"

    task.status = "failed"
    task.current_step = "failed"
    task.progress = task.progress or 0
    task.error_message = "任务入队修复已达到上限，Celery 中也找不到对应消息，已自动标记失败。"
    task.completed_at = now
    task.result = previous_result
    flag_modified(task, "result")
    db.session.commit()


def _publisher_child_parent_id(child: PublicationTask) -> Optional[str]:
    parent_id = child.parent_task_id or child.source_task_id
    if parent_id:
        return str(parent_id)
    params = child.params if isinstance(child.params, dict) else {}
    metadata = params.get("metadata") if isinstance(params.get("metadata"), dict) else {}
    return metadata.get("parent_task_id") or metadata.get("source_task_id")


def _publisher_child_is_post_completion_upload(child: PublicationTask) -> bool:
    params = child.params if isinstance(child.params, dict) else {}
    metadata = params.get("metadata") if isinstance(params.get("metadata"), dict) else {}
    source = str(metadata.get("source") or "").strip()
    if source in {
        "mp3_video_workflow:bilibili_upload",
        "report_audio_workflow_vertical_backfill",
    }:
        return True

    video = params.get("video") if isinstance(params.get("video"), dict) else {}
    bilibili = params.get("bilibili") if isinstance(params.get("bilibili"), dict) else {}
    youtube = params.get("youtube") if isinstance(params.get("youtube"), dict) else {}
    return (
        str(video.get("media_type") or "") == "existing_video"
        and video.get("enabled") is False
        and (bool(bilibili.get("enabled")) or bool(youtube.get("enabled")))
    )


def _publisher_child_repair_target(child: PublicationTask, *, default_queue: str) -> Tuple[Callable[..., Any], str]:
    if not _publisher_child_is_post_completion_upload(child):
        return run_publication_pipeline, default_queue

    params = child.params if isinstance(child.params, dict) else {}
    bilibili = params.get("bilibili") if isinstance(params.get("bilibili"), dict) else {}
    youtube = params.get("youtube") if isinstance(params.get("youtube"), dict) else {}
    if bool(bilibili.get("enabled")):
        from tasks.publisher import run_bilibili_upload_only

        return run_bilibili_upload_only, PLATFORM_UPLOAD_QUEUE
    if bool(youtube.get("enabled")):
        from tasks.publisher import run_youtube_upload_only

        return run_youtube_upload_only, PLATFORM_UPLOAD_QUEUE
    return run_publication_pipeline, default_queue


def _cancel_orphan_publisher_child(child: PublicationTask, *, parent: PublicationTask) -> None:
    _revoke_celery_task(child)
    now = datetime.utcnow()
    previous_result = dict(child.result or {})
    cleanup_state = {
        "reason": "parent_workflow_terminal",
        "parent_task_id": parent.id,
        "parent_feature": parent.feature,
        "parent_status": parent.status,
        "previous_status": child.status,
        "previous_step": child.current_step,
        "previous_celery_id": child.celery_task_id,
        "cleaned_at": now.isoformat() + "Z",
    }
    message = f"父编排任务已终止({parent.status})，视频子任务自动收口。"
    child.result = {
        **previous_result,
        CHILD_CLEANUP_RESULT_KEY: cleanup_state,
        "error": message,
    }
    child.status = "canceled"
    child.current_step = "canceled"
    child.error_message = message
    child.completed_at = now
    flag_modified(child, "result")
    db.session.commit()


def _demote_processing_publisher_to_pending(task: PublicationTask, *, queue: str, reason: str) -> None:
    now = datetime.utcnow().isoformat() + "Z"
    previous_result = dict(task.result or {})
    repair_state = dict(previous_result.get(PROCESSING_REPAIR_RESULT_KEY) or {})
    history = list(repair_state.get("history") or [])
    history.append({
        "at": now,
        "previous_celery_id": task.celery_task_id,
        "previous_step": task.current_step,
        "action": "demoted_to_pending",
    })
    repair_state.update({
        "last_demoted_at": now,
        "previous_celery_id": task.celery_task_id,
        "previous_status": task.status,
        "previous_step": task.current_step,
        "queue": queue,
        "reason": reason,
        "history": history[-10:],
    })
    task.result = {
        **previous_result,
        PROCESSING_REPAIR_RESULT_KEY: repair_state,
    }
    task.status = "pending"
    task.current_step = "queued"
    task.progress = 0
    task.error_message = None
    task.completed_at = None
    flag_modified(task, "result")
    db.session.commit()


def _mark_processing_publisher_orphan_failed(task: PublicationTask, *, queue: str, reason: str) -> None:
    _revoke_celery_task(task)
    now = datetime.utcnow()
    previous_result = dict(task.result or {})
    repair_state = {
        "reason": reason,
        "previous_status": task.status,
        "previous_step": task.current_step,
        "previous_celery_id": task.celery_task_id,
        "queue": queue,
        "closed_at": now.isoformat() + "Z",
    }
    message = (
        "publisher 任务在 processing 状态下已不在 Celery worker/队列中，"
        "系统已自动收口以释放并发槽位；如需继续请手动重试。"
    )
    task.result = {
        **previous_result,
        PROCESSING_REPAIR_RESULT_KEY: repair_state,
        "error": message,
    }
    task.status = "failed"
    task.current_step = "orphaned_processing"
    task.error_message = message
    task.completed_at = now
    flag_modified(task, "result")
    db.session.commit()


def _revoke_celery_task(task: PublicationTask) -> None:
    celery_task_id = getattr(task, "celery_task_id", None)
    if not celery_task_id:
        return
    try:
        celery_app.control.revoke(celery_task_id, terminate=False)
    except Exception as exc:
        logger.warning(
            "Publisher child cleanup revoke failed: task=%s celery=%s error=%s",
            getattr(task, "id", "-"),
            celery_task_id,
            exc,
        )


def _message_visible_in_workers(celery_id: str, *, child_id: str) -> Tuple[bool, bool]:
    timeout = float(os.environ.get("PUBLISHER_CHILD_ORPHAN_INSPECT_TIMEOUT", DEFAULT_INSPECT_TIMEOUT_SECONDS))
    try:
        inspector = celery_app.control.inspect(timeout=timeout)
        snapshots = [
            inspector.active(),
            inspector.reserved(),
            inspector.scheduled(),
        ]
    except Exception as exc:
        logger.warning("Publisher child queue repair inspect failed: child=%s error=%s", child_id, exc)
        return False, False

    if any(snapshot is None for snapshot in snapshots):
        return False, False

    for snapshot in snapshots:
        if _task_id_in_worker_snapshot(snapshot or {}, celery_id):
            return True, True
    return False, True


def _message_visible_in_queue(celery_id: str, *, child_id: str, queue: str) -> Tuple[bool, bool]:
    try:
        client = redis.Redis.from_url(
            celery_app.conf.broker_url,
            socket_connect_timeout=2,
            socket_timeout=3,
            retry_on_timeout=False,
        )
        limit = _int_env("PUBLISHER_CHILD_ORPHAN_QUEUE_SCAN_LIMIT", DEFAULT_QUEUE_SCAN_LIMIT, 20)
        for key in _redis_queue_keys(queue):
            for raw in client.lrange(key, 0, limit - 1):
                if _raw_message_matches(raw, celery_id=celery_id, child_id=child_id):
                    return True, True
        for raw in _iter_unacked_messages(client, limit):
            if _raw_message_matches(raw, celery_id=celery_id, child_id=child_id) or _raw_contains(raw, celery_id, child_id):
                return True, True
    except Exception as exc:
        logger.warning("Publisher child queue repair queue scan failed: child=%s error=%s", child_id, exc)
        return False, False
    return False, True


def _task_id_in_worker_snapshot(snapshot: Dict[str, Any], celery_id: str) -> bool:
    for tasks in snapshot.values():
        if not isinstance(tasks, list):
            continue
        for task in tasks:
            if isinstance(task, dict) and str(task.get("id") or "") == celery_id:
                return True
    return False


def _redis_queue_keys(queue: str) -> Iterable[bytes]:
    encoded = str(queue).encode("utf-8")
    yield encoded
    separator = b"\x06\x16"
    for priority in (b"0", b"3", b"6", b"9"):
        yield encoded + separator + priority


def _raw_message_matches(raw: Any, *, celery_id: str, child_id: str) -> bool:
    try:
        text = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
        envelope = json.loads(text)
    except Exception:
        return False

    headers = envelope.get("headers") if isinstance(envelope, dict) else None
    if isinstance(headers, dict) and str(headers.get("id") or "") == celery_id:
        return True

    body = envelope.get("body") if isinstance(envelope, dict) else None
    if not isinstance(body, str):
        return False
    try:
        decoded = base64.b64decode(body)
        payload = json.loads(decoded.decode("utf-8"))
    except Exception:
        return False
    return child_id in _flatten_strings(payload)


def _iter_unacked_messages(client: redis.Redis, limit: int) -> Iterable[Any]:
    scanned = 0
    for _key, value in client.hscan_iter("unacked", count=min(max(limit, 20), 200)):
        yield value
        scanned += 1
        if scanned >= limit:
            return


def _raw_contains(raw: Any, *needles: str) -> bool:
    if isinstance(raw, bytes):
        return any(needle.encode("utf-8") in raw for needle in needles if needle)
    text = str(raw)
    return any(needle in text for needle in needles if needle)


def _flatten_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _flatten_strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _flatten_strings(item)


def _age_seconds(value: Optional[datetime]) -> float:
    if not value:
        return 0
    return max(0.0, (datetime.utcnow() - value.replace(tzinfo=None)).total_seconds())


def _truthy_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _int_env(name: str, default: int, minimum: int) -> int:
    try:
        value = int(os.environ.get(name, default))
    except (TypeError, ValueError):
        value = default
    return max(value, minimum)


def _repair_target_for_feature(feature: str) -> Optional[Callable[..., Any]]:
    if feature == "publisher":
        return run_publication_pipeline
    if feature == "mp3_video_workflow":
        from tasks.mp3_video_workflow import process_mp3_video_workflow_job

        return process_mp3_video_workflow_job
    if feature in {"content_pdf_clean", "content_audio_clean"}:
        from tasks.content_pipeline import run_audio_clean_task, run_pdf_clean_task

        return run_pdf_clean_task if feature == "content_pdf_clean" else run_audio_clean_task
    if feature == "report_top10_workflow":
        from tasks.report_top10_workflow import process_report_top10_workflow_job

        return process_report_top10_workflow_job
    if feature == "report_script_material_workflow":
        from tasks.report_script_material_workflow import process_report_script_material_workflow_job

        return process_report_script_material_workflow_job
    if feature == "zsxq_clean_output_publish":
        from tasks.zsxq_publish import run_publish_clean_output_task

        return run_publish_clean_output_task
    return None
