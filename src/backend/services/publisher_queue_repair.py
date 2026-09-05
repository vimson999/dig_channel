import base64
import json
import logging
import os
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any, Callable, Dict, Iterable, Optional, Tuple

import redis
from sqlalchemy import and_, or_
from sqlalchemy.orm import load_only
from sqlalchemy.orm.attributes import flag_modified

from celery_config import celery_app
from models import db
from models.bilibili_publish import PublicationTaskBilibiliUpload
from models.task import PublicationTask
from services.platform_publish_dispatch import task_for_unique_feature
from services.runtime_guard import (
    apply_async_after_db_release,
    maybe_guarded_apply_async,
    revoke_celery_task_after_db_commit,
)
from tasks.publisher import PLATFORM_UPLOAD_QUEUE, run_publication_pipeline


logger = logging.getLogger(__name__)

DEFAULT_ORPHAN_GRACE_SECONDS = 180
PENDING_REPAIR_GRACE_SECONDS_BY_FEATURE = {
    # Browser image jobs are submitted in bursts and may remain reserved behind
    # another long browser generation after disappearing from the broker queue.
    "report_stick_figure_image": 1800,
}
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
    "report_script_material_workflow",
    "report_top10_workflow",
}
TERMINAL_PARENT_CLEANUP_CHILD_FEATURES = {
    "publisher",
    "doubao_browser_tts",
    "gemini_browser",
    "report_pdf_gemini",
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
    "report_audio_workflow": "report_audio_workflow",
    "report_pdf_audio_workflow": "report_audio_workflow",
    "report_script_material_workflow": "report_script_material_workflow",
    "report_pdf_gemini": "report_pdf_gemini",
    "gemini_browser": "report_pdf_gemini",
    "report_visual_plan": "report_visual_plan",
    "report_top10_workflow": "report_top10_workflow",
    "content_pdf_clean": "content_pipeline",
    "content_audio_clean": "content_pipeline",
    "zsxq_clean_output_publish": "zsxq_publish",
    "douyin_upload_test": "platform_upload",
    "wechat_channels_upload_test": "platform_upload",
    "doubao_browser_tts": "doubao_browser_tts",
    "report_stick_figure_image": "report_pdf_gemini",
    "report_stick_figure_audio_alignment": "render",
    "report_stick_figure_video_render": "render",
}
BUSINESS_PROCESSING_REPAIRABLE_FEATURES = {
    "report_audio_workflow": {
        "queue": "report_audio_workflow",
        "requeueable_steps": {"", "queued", "init"},
        "requeueable_prefixes": (
            "gemini_",
            "review_",
            "normalize_",
            "doubao_",
            "combine_",
            "render_pdf_",
            "generate_video_",
            "publisher_",
            "pub_",
        ),
    },
    "report_pdf_audio_workflow": {
        "queue": "report_audio_workflow",
        "requeueable_steps": {"", "queued", "init"},
        "requeueable_prefixes": (
            "gemini_",
            "review_",
            "normalize_",
            "doubao_",
            "combine_",
            "render_pdf_",
            "generate_video_",
            "publisher_",
            "pub_",
        ),
    },
    "report_pdf_gemini": {
        "queue": "report_pdf_gemini",
        "requeueable_steps": {
            "", "queued", "prepare_browser", "open_page", "switch_model",
            "upload_pdf", "submit_prompt", "wait_response", "initial_response",
            "save_result",
        },
        "requeueable_prefixes": (
            "followup_",
            "gemini_",
            "response_contract_repair_",
        ),
    },
    "gemini_browser": {
        "queue": "report_pdf_gemini",
        "requeueable_steps": {
            "", "queued", "prepare_browser", "open_page", "switch_model",
            "upload_pdf", "submit_prompt", "wait_response", "initial_response",
            "save_result",
        },
        "requeueable_prefixes": (
            "followup_",
            "gemini_",
            "response_contract_repair_",
        ),
    },
    "report_visual_plan": {
        "queue": "report_visual_plan",
        "requeueable_steps": {
            "", "queued", "auto_retry", "gemini_storyboard", "prepare_browser",
            "open_page", "submit_prompt", "wait_response", "normalize_storyboard",
        },
        "requeueable_prefixes": ("followup_", "gemini_"),
    },
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
    "content_pdf_clean": {
        "queue": "content_pipeline",
        "requeueable_steps": {
            "",
            "queued",
            "init",
            "prepare",
            "local_cleaning",
        },
        "requeueable_prefixes": (),
    },
    "content_audio_clean": {
        "queue": "content_pipeline",
        "requeueable_steps": {
            "",
            "queued",
            "init",
            "prepare",
            "cleaning",
            "audio_cleaning",
        },
        "requeueable_prefixes": (),
    },
    "douyin_upload_test": {
        "queue": "platform_upload",
        "requeueable_steps": {
            "",
            "queued",
            "resolving_credential",
            "opening_creator",
            "uploading_video",
            "filling_copy",
        },
        "requeueable_prefixes": (),
    },
    "wechat_channels_upload_test": {
        "queue": "platform_upload",
        "requeueable_steps": {
            "",
            "queued",
            "resolving_credential",
            "opening_creator",
            "uploading_video",
            "filling_copy",
        },
        "requeueable_prefixes": (),
    },
    "doubao_browser_tts": {
        "queue": "doubao_browser_tts",
        "requeueable_steps": {
            "",
            "queued",
            "prepare_browser",
            "wait_browser_profile",
            "clear_audio_cache",
            "submit_text",
            "wait_response",
            "start_reading",
            "capture_audio",
            "download_wav",
            "save_file",
        },
        "requeueable_prefixes": (),
    },
    "report_stick_figure_image": {
        "queue": "report_pdf_gemini",
        "requeueable_steps": {
            "",
            "queued",
            "prepare_browser",
            "wait_browser_profile",
            "open_page",
            "select_image_mode",
            "submit_image_prompt",
            "wait_generated_image",
            "generated_image_saved",
        },
        "requeueable_prefixes": (),
    },
    "report_stick_figure_audio_alignment": {
        "queue": "render",
        "requeueable_steps": {
            "", "queued", "transcribe_whole_audio", "align_timeline",
            "save_alignment",
        },
        "requeueable_prefixes": ("align_", "transcribe_"),
    },
    "report_stick_figure_video_render": {
        "queue": "render",
        "requeueable_steps": {
            "", "queued", "prepare_timeline", "render_video", "save_video",
        },
        "requeueable_prefixes": ("prepare_", "render_", "ffmpeg_", "mux_"),
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
REPAIRABLE_PENDING_STEPS_BY_FEATURE = {
    # Celery retries briefly persist this state before the ETA message runs.
    # A worker restart can drop that message, so the watchdog must be able to
    # recover it after the normal visibility and grace-period checks.
    "doubao_browser_tts": {"auto_retry"},
    # Celery retries for browser-profile contention are persisted as pending
    # auto_retry rows.  If a worker restarts before the ETA message is
    # delivered, the watchdog must requeue the storyboard instead of leaving
    # its parent waiting forever.
    "report_visual_plan": {"auto_retry"},
}


def _pending_step_repairable(feature: Optional[str], step: Optional[str]) -> bool:
    normalized_step = str(step or "").lower()
    if normalized_step in REPAIRABLE_PENDING_STEPS:
        return True
    return normalized_step in REPAIRABLE_PENDING_STEPS_BY_FEATURE.get(
        str(feature or ""),
        set(),
    )


PUBLISHER_REPAIR_SCAN_COLUMNS = (
    PublicationTask.id,
    PublicationTask.celery_task_id,
    PublicationTask.status,
    PublicationTask.current_step,
    PublicationTask.progress,
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
    PublicationTask.result,
    PublicationTask.parent_task_id,
    PublicationTask.source_task_id,
    PublicationTask.created_at,
    PublicationTask.updated_at,
    PublicationTask.completed_at,
)
PUBLISHER_PARENT_CLEANUP_CANDIDATE_COLUMNS = tuple(
    column
    for column in PUBLISHER_PARENT_CLEANUP_SCAN_COLUMNS
    if column.key not in {"params", "result"}
)
WORKFLOW_CHILD_CLEANUP_SCAN_COLUMNS = (
    *PUBLISHER_PARENT_CLEANUP_CANDIDATE_COLUMNS,
    PublicationTask.result,
)


def _load_repair_task(task_id, *columns):
    return (
        PublicationTask.query.options(load_only(*(columns or PUBLISHER_REPAIR_SCAN_COLUMNS)))
        .filter(PublicationTask.id == str(task_id))
        .one_or_none()
    )


def _detached_repair_task_snapshot(task):
    """Copy a repair candidate before Celery/Redis network inspection."""
    return SimpleNamespace(
        id=str(task.id),
        celery_task_id=task.celery_task_id,
        status=task.status,
        current_step=task.current_step,
        progress=task.progress,
        params=dict(task.__dict__.get("params") or {}),
        result=dict(task.__dict__.get("result") or {}),
        error_message=task.error_message,
        feature=task.feature,
        parent_task_id=task.parent_task_id,
        source_task_id=task.source_task_id,
        created_at=task.created_at,
        updated_at=task.updated_at,
        completed_at=task.completed_at,
    )


def _release_repair_db_session():
    """Do not keep an idle transaction open during Celery/Redis I/O."""
    try:
        db.session.rollback()
    finally:
        db.session.remove()


def _load_matching_processing_publisher_task(snapshot):
    """Reload a processing publisher row after external visibility checks."""
    current = _load_repair_task(snapshot.id, *PUBLISHER_REPAIR_SCAN_COLUMNS)
    if (
        current is None
        or current.status != snapshot.status
        or current.celery_task_id != snapshot.celery_task_id
        or current.current_step != snapshot.current_step
    ):
        _release_repair_db_session()
        return None
    return current


def _revoke_published_repair_message(async_result, *, task_id, log):
    celery_id = getattr(async_result, "id", None)
    if not celery_id:
        return
    revoke_celery_task_after_db_commit(
        celery_id,
        operation=f"publisher superseded repair revoke task={task_id}",
        business_task_id=str(task_id),
        log=log,
    )


def _release_then_revoke_published_repair_message(
    async_result,
    *,
    task_id,
    log,
):
    """Never hold a DB checkout while waiting for Celery control I/O."""
    _release_repair_db_session()
    _revoke_published_repair_message(
        async_result,
        task_id=task_id,
        log=log,
    )


def maybe_requeue_stale_publisher_child(
    child: PublicationTask,
    *,
    queue: str = "render",
    log: Optional[logging.Logger] = None,
    upload_projection: Optional[PublicationTaskBilibiliUpload] = None,
    repair_outcome: Optional[Dict[str, str]] = None,
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

    if upload_projection is None:
        upload_projection = PublicationTaskBilibiliUpload.query.filter_by(
            task_id=child.id
        ).one_or_none()
    snapshot = _detached_repair_task_snapshot(child)
    upload_snapshot = (
        SimpleNamespace(
            task_id=str(upload_projection.task_id),
            queue_repair_count=int(upload_projection.queue_repair_count or 0),
        )
        if upload_projection is not None
        else None
    )
    result = None if upload_snapshot is not None else dict(snapshot.result or {})
    repair_state = (
        {"count": upload_snapshot.queue_repair_count}
        if upload_snapshot is not None
        else dict(result.get(REPAIR_RESULT_KEY) or {})
    )
    requeue_limit = _int_env("PUBLISHER_CHILD_ORPHAN_REQUEUE_LIMIT", DEFAULT_ORPHAN_REQUEUE_LIMIT, 1)
    requeue_count = int(repair_state.get("count") or 0)

    requeue_target, target_queue = _publisher_child_repair_target(
        snapshot,
        default_queue=queue,
        upload_projection=upload_snapshot,
    )
    previous_celery_id = snapshot.celery_task_id
    _release_repair_db_session()
    if previous_celery_id:
        worker_visible, worker_checked = _message_visible_in_workers(
            previous_celery_id,
            child_id=snapshot.id,
        )
        if not worker_checked or worker_visible:
            return snapshot, False
        queue_visible, queue_checked = _message_visible_in_queue(
            previous_celery_id,
            child_id=snapshot.id,
            queue=target_queue,
        )
        if not queue_checked:
            return snapshot, False
    else:
        worker_visible = False
        queue_visible = False

    if requeue_count >= requeue_limit:
        # A missing Celery message at the repair limit is a terminal recovery
        # condition.  Leaving the child pending makes its parent wait forever
        # and turns every watchdog tick into the same warning.  Reload the
        # narrow task state after broker inspection so a concurrent worker or
        # cancellation wins over this close operation.
        current = _load_repair_task(snapshot.id, *PUBLISHER_REPAIR_SCAN_COLUMNS)
        if (
            not current
            or current.status != "pending"
            or current.celery_task_id != snapshot.celery_task_id
            or current.current_step != snapshot.current_step
        ):
            return snapshot, False
        closed = _mark_business_task_queue_repair_limit_failed(
            current,
            queue=target_queue,
            feature="publisher",
            requeue_count=requeue_count,
            requeue_limit=requeue_limit,
        )
        updated = _load_repair_task(snapshot.id, *PUBLISHER_REPAIR_SCAN_COLUMNS)
        updated_snapshot = (
            _detached_repair_task_snapshot(updated)
            if updated is not None
            else snapshot
        )
        if not closed:
            return updated_snapshot, False
        if repair_outcome is not None:
            repair_outcome["action"] = "closed"
        active_logger.warning(
            "Closed orphan publisher child after queue repair limit: child=%s count=%s limit=%s",
            snapshot.id,
            requeue_count,
            requeue_limit,
        )
        return updated_snapshot, False

    reason = (
        "pending_message_not_active_but_queue_visible"
        if queue_visible
        else "pending_queued_not_visible_in_celery"
    )

    if not queue_visible and previous_celery_id:
        logger.info(
            "Publisher pending task message is absent from workers and queue: child=%s celery=%s",
            snapshot.id,
            previous_celery_id,
        )
    elif queue_visible:
        logger.warning(
            "Publisher pending task message is queued/unacked but not active; requeueing with a fresh celery id: child=%s celery=%s",
            snapshot.id,
            previous_celery_id,
        )

    if previous_celery_id and worker_visible:
        return snapshot, False

    try:
        async_result = apply_async_after_db_release(
            requeue_target,
            args=[snapshot.id],
            queue=target_queue,
            retry=False,
        )
    except Exception as exc:
        active_logger.warning(
            "Publisher child queue repair publish failed: child=%s previous_celery_id=%s error=%s",
            snapshot.id,
            previous_celery_id,
            exc,
        )
        return snapshot, False
    now_value = datetime.utcnow()
    now = now_value.isoformat() + "Z"

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

    if upload_snapshot is None:
        result[REPAIR_RESULT_KEY] = repair_state
        result.pop("error", None)
        result.pop("error_type", None)
        result.pop("completed_at", None)

    compare_and_swap = PublicationTask.query.filter(
        PublicationTask.id == snapshot.id,
        PublicationTask.status == "pending",
        PublicationTask.celery_task_id == previous_celery_id,
        PublicationTask.current_step == snapshot.current_step,
    )
    if snapshot.updated_at is not None:
        compare_and_swap = compare_and_swap.filter(
            PublicationTask.updated_at == snapshot.updated_at,
        )
    task_values = {
        PublicationTask.celery_task_id: async_result.id,
        PublicationTask.status: "pending",
        PublicationTask.current_step: "queued",
        PublicationTask.progress: 0,
        PublicationTask.error_message: None,
        PublicationTask.completed_at: None,
        PublicationTask.updated_at: now_value,
    }
    if result is not None:
        task_values[PublicationTask.result] = result
    task_updated = compare_and_swap.update(
        task_values,
        synchronize_session=False,
    )
    projection_updated = 1
    if upload_snapshot is not None:
        projection_updated = (
            PublicationTaskBilibiliUpload.query
            .filter(
                PublicationTaskBilibiliUpload.task_id == snapshot.id,
                PublicationTaskBilibiliUpload.queue_repair_count
                == upload_snapshot.queue_repair_count,
            )
            .update({
                PublicationTaskBilibiliUpload.queue_repair_count: requeue_count + 1,
                PublicationTaskBilibiliUpload.queue_repair_last_at: now_value,
                PublicationTaskBilibiliUpload.queue_repair_previous_celery_id:
                    previous_celery_id,
                PublicationTaskBilibiliUpload.queue_repair_current_celery_id:
                    async_result.id,
                PublicationTaskBilibiliUpload.queue_repair_queue: target_queue,
                PublicationTaskBilibiliUpload.queue_repair_reason: reason,
                PublicationTaskBilibiliUpload.queue_repair_previous_message_visible:
                    bool(queue_visible),
                PublicationTaskBilibiliUpload.updated_at: now_value,
            }, synchronize_session=False)
        )
    if not task_updated or not projection_updated:
        _release_then_revoke_published_repair_message(
            async_result,
            task_id=snapshot.id,
            log=active_logger,
        )
        return snapshot, False
    db.session.commit()

    snapshot_values = dict(snapshot.__dict__)
    snapshot_values.update({
        "celery_task_id": str(async_result.id),
        "status": "pending",
        "current_step": "queued",
        "progress": 0,
        "error_message": None,
        "updated_at": now_value,
        "completed_at": None,
        "result": dict(result or {}),
    })
    updated_snapshot = SimpleNamespace(**snapshot_values)
    active_logger.warning(
        "Requeued orphan publisher child: child=%s previous_celery_id=%s new_celery_id=%s",
        snapshot.id,
        previous_celery_id,
        async_result.id,
    )
    return updated_snapshot, True


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
    grace_seconds = _int_env(
        "PUBLISHER_CHILD_ORPHAN_GRACE_SECONDS",
        DEFAULT_ORPHAN_GRACE_SECONDS,
        30,
    )
    stale_before = datetime.utcnow() - timedelta(seconds=grace_seconds)
    rows = (
        db.session.query(*PUBLISHER_REPAIR_CANDIDATE_COLUMNS)
        .filter(PublicationTask.feature == "publisher")
        .filter(PublicationTask.status == "pending")
        .filter(PublicationTask.current_step.in_(tuple(PUBLISHER_PENDING_REPAIR_STEPS)))
        .filter(or_(
            PublicationTask.updated_at <= stale_before,
            and_(PublicationTask.updated_at.is_(None), PublicationTask.created_at <= stale_before),
        ))
        .order_by(PublicationTask.updated_at.asc(), PublicationTask.created_at.asc())
        .limit(scan_limit)
        .all()
    )

    items = []
    requeued = 0
    closed = 0
    for row in rows:
        previous_celery_id = row.celery_task_id
        previous_step = row.current_step
        upload_projection = PublicationTaskBilibiliUpload.query.filter_by(
            task_id=row.id
        ).one_or_none()
        repair_columns = (
            PUBLISHER_REPAIR_CANDIDATE_COLUMNS
            if upload_projection is not None
            else PUBLISHER_REPAIR_SCAN_COLUMNS
        )
        task = _load_repair_task(row.id, *repair_columns)
        if not task:
            continue
        try:
            repair_outcome = {}
            updated, did_requeue = maybe_requeue_stale_publisher_child(
                task,
                log=active_logger,
                upload_projection=upload_projection,
                repair_outcome=repair_outcome,
            )
        except Exception as exc:
            db.session.rollback()
            active_logger.exception("Publisher pending queue repair failed: task=%s", getattr(row, "id", "-"))
            items.append({
                "task_id": getattr(row, "id", None),
                "requeued": False,
                "error": str(exc),
            })
            continue
        if repair_outcome.get("action") == "closed":
            closed += 1
            _target, target_queue = _publisher_child_repair_target(
                updated,
                default_queue="render",
                upload_projection=upload_projection,
            )
            items.append({
                "task_id": updated.id,
                "queue": target_queue,
                "previous_celery_id": previous_celery_id,
                "previous_step": previous_step,
                "action": "closed",
                "requeued": False,
            })
            continue
        if not did_requeue:
            continue
        requeued += 1
        _target, target_queue = _publisher_child_repair_target(
            updated,
            default_queue="render",
            upload_projection=upload_projection,
        )
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
        "closed": closed,
        "items": items,
    }


def cleanup_publisher_children_of_terminal_parents_once(
    *,
    limit: Optional[int] = None,
    log: Optional[logging.Logger] = None,
) -> Dict[str, Any]:
    """Cancel active workflow children whose parent workflow already ended.

    Child synthesis, browser, and publisher work is meaningful only while its
    workflow parent is alive. Post-completion upload-only publisher jobs are
    the sole exception.
    """
    active_logger = log or logger
    if not _truthy_env("PUBLISHER_CHILD_PARENT_CLEANUP_ENABLED", True):
        return {"enabled": False, "checked": 0, "cleaned": 0, "items": []}

    scan_limit = limit or _int_env("PUBLISHER_CHILD_PARENT_CLEANUP_SCAN_LIMIT", 50, 1)
    terminal_parent_ids = (
        db.session.query(PublicationTask.id)
        .filter(PublicationTask.feature.in_(tuple(PUBLISHER_PARENT_FEATURES)))
        .filter(PublicationTask.status.in_(tuple(TERMINAL_PARENT_STATUSES)))
    )
    rows = (
        db.session.query(*PUBLISHER_PARENT_CLEANUP_CANDIDATE_COLUMNS)
        .filter(PublicationTask.feature.in_(tuple(TERMINAL_PARENT_CLEANUP_CHILD_FEATURES)))
        .filter(PublicationTask.status.in_(tuple(ACTIVE_TASK_STATUSES)))
        .filter(or_(PublicationTask.parent_task_id.isnot(None), PublicationTask.source_task_id.isnot(None)))
        .filter(or_(
            PublicationTask.parent_task_id.in_(terminal_parent_ids),
            and_(
                PublicationTask.parent_task_id.is_(None),
                PublicationTask.source_task_id.in_(terminal_parent_ids),
            ),
        ))
        .order_by(PublicationTask.updated_at.asc(), PublicationTask.created_at.asc())
        .limit(scan_limit)
        .all()
    )
    parent_ids = {
        str(row.parent_task_id or row.source_task_id)
        for row in rows
        if row.parent_task_id or row.source_task_id
    }
    parent_rows = (
        db.session.query(PublicationTask.id, PublicationTask.feature, PublicationTask.status)
        .filter(PublicationTask.id.in_(parent_ids))
        .all()
        if parent_ids
        else []
    )
    parents = {
        str(row.id): SimpleNamespace(id=str(row.id), feature=row.feature, status=row.status)
        for row in parent_rows
    }

    items = []
    cleaned = 0
    for row in rows:
        parent_id = str(row.parent_task_id or row.source_task_id or "")
        parent = parents.get(parent_id)
        if not parent:
            continue
        parent_status = str(parent.status or "").lower()
        child = _load_repair_task(row.id, *WORKFLOW_CHILD_CLEANUP_SCAN_COLUMNS)
        if not child:
            continue
        if parent_status == "completed" and child.feature == "publisher":
            publisher_child = _load_repair_task(row.id, *PUBLISHER_PARENT_CLEANUP_SCAN_COLUMNS)
            if publisher_child and _publisher_child_is_post_completion_upload(publisher_child):
                continue
            child = publisher_child or child

        try:
            updated_child = _cancel_orphan_workflow_child(child, parent=parent)
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

        if updated_child is None:
            items.append({
                "task_id": getattr(child, "id", None),
                "parent_task_id": parent_id,
                "parent_status": parent_status,
                "cleaned": False,
                "reason": "state_changed",
            })
            continue

        cleaned += 1
        items.append({
            "task_id": updated_child.id,
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

    snapshot = _detached_repair_task_snapshot(task)
    step = str(snapshot.current_step or "").lower()
    grace_seconds = _publisher_processing_orphan_grace_seconds(step)
    reference_time = snapshot.updated_at or snapshot.created_at
    if _age_seconds(reference_time) < grace_seconds:
        return snapshot, False, "within_grace"

    requeue_target, target_queue = _publisher_child_repair_target(
        snapshot,
        default_queue=queue,
    )
    _release_repair_db_session()
    worker_visible, worker_checked = _message_visible_in_workers(
        snapshot.celery_task_id,
        child_id=snapshot.id,
    )
    if not worker_checked:
        return snapshot, False, "visibility_unknown"
    if worker_visible:
        return snapshot, False, "visible_in_worker"

    if step in PUBLISHER_PROCESSING_UPLOAD_STEPS:
        _revoke_celery_task(snapshot)
        current = _load_matching_processing_publisher_task(snapshot)
        if current is None:
            return snapshot, False, "state_changed"
        _mark_processing_publisher_orphan_failed(
            current,
            queue=target_queue,
            reason="upload_step_not_requeued",
            revoke=False,
        )
        updated_snapshot = _detached_repair_task_snapshot(current)
        db.session.remove()
        active_logger.warning(
            "Closed orphan publisher upload task without auto-requeue: task=%s step=%s",
            snapshot.id,
            step,
        )
        return updated_snapshot, True, "closed"

    queue_visible, queue_checked = _message_visible_in_queue(
        snapshot.celery_task_id,
        child_id=snapshot.id,
        queue=target_queue,
    )
    if not queue_checked:
        return snapshot, False, "visibility_unknown"

    current = _load_matching_processing_publisher_task(snapshot)
    if current is None:
        return snapshot, False, "state_changed"

    if step not in PUBLISHER_PROCESSING_REQUEUEABLE_STEPS:
        _release_repair_db_session()
        _revoke_celery_task(snapshot)
        current = _load_matching_processing_publisher_task(snapshot)
        if current is None:
            return snapshot, False, "state_changed"
        _mark_processing_publisher_orphan_failed(
            current,
            queue=target_queue,
            reason="unknown_processing_step",
            revoke=False,
        )
        updated_snapshot = _detached_repair_task_snapshot(current)
        db.session.remove()
        active_logger.warning(
            "Closed orphan publisher task with unknown step: task=%s step=%s",
            snapshot.id,
            step,
        )
        return updated_snapshot, True, "closed"

    if queue_visible:
        _demote_processing_publisher_to_pending(
            current,
            queue=target_queue,
            reason="processing_not_active_but_message_visible",
        )
        updated_snapshot = _detached_repair_task_snapshot(current)
        db.session.remove()
        active_logger.warning(
            "Demoted orphan publisher processing task to pending: task=%s celery_id=%s step=%s",
            snapshot.id,
            snapshot.celery_task_id,
            step,
        )
        return updated_snapshot, True, "demoted"

    result = dict(current.result or snapshot.result or {})
    repair_state = dict(result.get(PROCESSING_REPAIR_RESULT_KEY) or {})
    requeue_limit = _int_env(
        "PUBLISHER_PROCESSING_ORPHAN_REQUEUE_LIMIT",
        DEFAULT_ORPHAN_REQUEUE_LIMIT,
        1,
    )
    requeue_count = int(repair_state.get("count") or 0)
    if requeue_count >= requeue_limit:
        _release_repair_db_session()
        _revoke_celery_task(snapshot)
        current = _load_matching_processing_publisher_task(snapshot)
        if current is None:
            return snapshot, False, "state_changed"
        _mark_processing_publisher_orphan_failed(
            current,
            queue=target_queue,
            reason="requeue_limit_reached",
            revoke=False,
        )
        updated_snapshot = _detached_repair_task_snapshot(current)
        db.session.remove()
        active_logger.warning(
            "Closed orphan publisher task after processing repair limit: task=%s count=%s limit=%s",
            snapshot.id,
            requeue_count,
            requeue_limit,
        )
        return updated_snapshot, True, "closed"

    previous_celery_id = snapshot.celery_task_id
    previous_status = snapshot.status
    previous_step = snapshot.current_step
    _release_repair_db_session()
    try:
        async_result = apply_async_after_db_release(
            requeue_target,
            args=[snapshot.id],
            queue=target_queue,
            retry=False,
        )
    except Exception as exc:
        active_logger.warning(
            "Publisher processing orphan requeue failed: task=%s previous_celery_id=%s error=%s",
            snapshot.id,
            previous_celery_id,
            exc,
        )
        return snapshot, False, "publish_failed"

    current = _load_matching_processing_publisher_task(snapshot)
    if current is None:
        _release_then_revoke_published_repair_message(
            async_result,
            task_id=snapshot.id,
            log=active_logger,
        )
        return snapshot, False, "state_changed"

    latest_result = dict(current.result or {})
    latest_result[PROCESSING_REPAIR_RESULT_KEY] = repair_state
    latest_result.pop("error", None)
    latest_result.pop("error_type", None)
    latest_result.pop("completed_at", None)
    result = latest_result

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

    current.celery_task_id = async_result.id
    current.status = "pending"
    current.current_step = "queued"
    current.progress = 0
    current.error_message = None
    current.completed_at = None
    current.result = result
    flag_modified(current, "result")
    updated_snapshot = _detached_repair_task_snapshot(current)
    db.session.commit()
    db.session.remove()

    active_logger.warning(
        "Requeued orphan publisher processing task: task=%s previous_celery_id=%s new_celery_id=%s step=%s",
        snapshot.id,
        previous_celery_id,
        async_result.id,
        previous_step,
    )
    return updated_snapshot, True, "requeued"


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
    message_visibility: Optional[Tuple[bool, bool]] = None,
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

    snapshot = _detached_repair_task_snapshot(task)
    feature = str(getattr(snapshot, "feature", "") or "")
    queue = REPAIRABLE_FEATURES.get(feature)
    if not queue:
        return snapshot, False
    if (snapshot.status or "").lower() != "pending":
        return snapshot, False
    if not _pending_step_repairable(feature, snapshot.current_step):
        return snapshot, False

    grace_seconds = _pending_repair_grace_seconds(feature)
    reference_time = snapshot.updated_at or snapshot.created_at
    if _age_seconds(reference_time) < grace_seconds:
        return snapshot, False

    result = dict(snapshot.result or {})
    repair_state = dict(result.get(REPAIR_RESULT_KEY) or {})
    requeue_limit = _int_env(
        "BUSINESS_TASK_QUEUE_REPAIR_LIMIT",
        DEFAULT_ORPHAN_REQUEUE_LIMIT,
        1,
    )
    requeue_count = int(repair_state.get("count") or 0)

    # Worker inspection can take several seconds. Keeping the candidate SELECT
    # transaction open here used to hit idle_in_transaction_session_timeout;
    # the following admission query then reused a server-closed connection.
    _release_repair_db_session()
    if message_visibility is None:
        visible, checked = _celery_message_visible(
            snapshot.celery_task_id,
            child_id=snapshot.id,
            queue=queue,
        )
    else:
        visible, checked = message_visibility
    if not checked or visible:
        return snapshot, False

    if requeue_count >= requeue_limit:
        current = _load_repair_task(snapshot.id, *PUBLISHER_REPAIR_SCAN_COLUMNS)
        if (
            not current
            or current.status != "pending"
            or current.celery_task_id != snapshot.celery_task_id
        ):
            return snapshot, False
        closed = _mark_business_task_queue_repair_limit_failed(
            current,
            queue=queue,
            feature=feature,
            requeue_count=requeue_count,
            requeue_limit=requeue_limit,
        )
        updated = _load_repair_task(snapshot.id, *PUBLISHER_REPAIR_SCAN_COLUMNS)
        if not closed or updated is None:
            return (
                _detached_repair_task_snapshot(updated)
                if updated is not None
                else snapshot
            ), False
        active_logger.warning(
            "Closed orphan business task after queue repair limit: task=%s feature=%s count=%s limit=%s",
            snapshot.id,
            feature,
            requeue_count,
            requeue_limit,
        )
        return _detached_repair_task_snapshot(updated), False

    celery_callable = _repair_target_for_feature(feature)
    if celery_callable is None:
        return snapshot, False

    previous_celery_id = snapshot.celery_task_id
    try:
        submit_result = maybe_guarded_apply_async(
            celery_callable,
            args=[snapshot.id],
            queue=queue,
            feature=feature,
            task=snapshot,
            retry=False,
        )
        async_result = submit_result["async_result"]
    except Exception as exc:
        _release_repair_db_session()
        active_logger.warning(
            "Business task queue repair publish failed: task=%s feature=%s previous_celery_id=%s error=%s",
            snapshot.id,
            feature,
            previous_celery_id,
            exc,
        )
        return snapshot, False

    now_value = datetime.utcnow()
    now = now_value.isoformat() + "Z"
    latest_result = dict(snapshot.result or {})
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

    current_step = snapshot.current_step
    progress = snapshot.progress
    error_message = snapshot.error_message
    if not submit_result.get("deferred"):
        current_step = "queued"
        progress = 0
        error_message = None
    compare_and_swap = PublicationTask.query.filter(
        PublicationTask.id == snapshot.id,
        PublicationTask.status == "pending",
        PublicationTask.celery_task_id == previous_celery_id,
    )
    if snapshot.updated_at is not None:
        compare_and_swap = compare_and_swap.filter(
            PublicationTask.updated_at == snapshot.updated_at,
        )
    updated = compare_and_swap.update({
        PublicationTask.celery_task_id: async_result.id,
        PublicationTask.status: "pending",
        PublicationTask.current_step: current_step,
        PublicationTask.progress: progress,
        PublicationTask.error_message: error_message,
        PublicationTask.completed_at: None,
        PublicationTask.result: latest_result,
        PublicationTask.updated_at: now_value,
    }, synchronize_session=False)
    if not updated:
        _release_then_revoke_published_repair_message(
            async_result,
            task_id=snapshot.id,
            log=active_logger,
        )
        return snapshot, False
    db.session.commit()
    db.session.remove()

    snapshot_values = dict(snapshot.__dict__)
    snapshot_values.update({
        "celery_task_id": async_result.id,
        "status": "pending",
        "current_step": current_step,
        "progress": progress,
        "error_message": error_message,
        "completed_at": None,
        "updated_at": now_value,
        "result": latest_result,
    })
    updated_snapshot = SimpleNamespace(**snapshot_values)

    active_logger.warning(
        "Requeued orphan business task: task=%s feature=%s previous_celery_id=%s new_celery_id=%s deferred=%s",
        snapshot.id,
        feature,
        previous_celery_id,
        async_result.id,
        bool(submit_result.get("deferred")),
    )
    return updated_snapshot, True


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
        if not _pending_step_repairable(row.feature, row.current_step):
            continue
        grace_seconds = _pending_repair_grace_seconds(row.feature)
        reference_time = row.updated_at or row.created_at
        if _age_seconds(reference_time) < grace_seconds:
            continue

        # Most pending rows still have a visible broker message. Check that
        # using the lightweight candidate projection before loading params/result.
        _release_repair_db_session()
        visible, checked = _celery_message_visible(
            row.celery_task_id,
            child_id=row.id,
            queue=REPAIRABLE_FEATURES.get(row.feature),
        )
        if not checked or visible:
            continue
        task = _load_repair_task(row.id, *PUBLISHER_REPAIR_SCAN_COLUMNS)
        if not task:
            continue
        try:
            updated, did_requeue = maybe_requeue_stale_publication_task(
                task,
                log=active_logger,
                message_visibility=(False, True),
            )
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

    snapshot = _detached_repair_task_snapshot(task)
    feature = str(getattr(snapshot, "feature", "") or "")
    config = BUSINESS_PROCESSING_REPAIRABLE_FEATURES.get(feature)
    if not config:
        return snapshot, False, "unsupported_feature"
    if (snapshot.status or "").lower() != "processing":
        return snapshot, False, "not_processing"

    grace_seconds = _int_env("BUSINESS_PROCESSING_ORPHAN_GRACE_SECONDS", 600, 120)
    reference_time = snapshot.updated_at or snapshot.created_at
    if _age_seconds(reference_time) < grace_seconds:
        return snapshot, False, "within_grace"

    queue = str(config.get("queue") or REPAIRABLE_FEATURES.get(feature) or feature)
    step = str(snapshot.current_step or "").lower()
    requeueable_steps = set(config.get("requeueable_steps") or ())
    requeueable_prefixes = tuple(str(item) for item in (config.get("requeueable_prefixes") or ()) if item)
    if step not in requeueable_steps and not step.startswith(requeueable_prefixes):
        return snapshot, False, "step_not_requeueable"

    result = dict(snapshot.result or {})
    repair_state = dict(result.get(PROCESSING_REPAIR_RESULT_KEY) or {})
    requeue_limit = _int_env(
        "BUSINESS_PROCESSING_ORPHAN_REQUEUE_LIMIT",
        DEFAULT_ORPHAN_REQUEUE_LIMIT,
        1,
    )
    requeue_count = int(repair_state.get("count") or 0)

    _release_repair_db_session()
    worker_visible, worker_checked = _message_visible_in_workers(
        snapshot.celery_task_id,
        child_id=snapshot.id,
    )
    if not worker_checked:
        return snapshot, False, "visibility_unknown"
    if worker_visible:
        return snapshot, False, "visible_in_worker"
    queue_visible, queue_checked = _message_visible_in_queue(
        snapshot.celery_task_id,
        child_id=snapshot.id,
        queue=queue,
    )
    if not queue_checked:
        return snapshot, False, "visibility_unknown"

    current = _load_repair_task(snapshot.id, *PUBLISHER_REPAIR_SCAN_COLUMNS)
    if (
        not current
        or current.status != "processing"
        or current.celery_task_id != snapshot.celery_task_id
        or current.current_step != snapshot.current_step
    ):
        _release_repair_db_session()
        return snapshot, False, "state_changed"

    if queue_visible:
        _demote_business_processing_to_pending(
            current,
            queue=queue,
            feature=feature,
            reason="processing_not_active_but_message_visible",
        )
        updated_snapshot = _detached_repair_task_snapshot(current)
        db.session.remove()
        active_logger.warning(
            "Demoted orphan business processing task to pending: task=%s feature=%s celery=%s step=%s",
            snapshot.id,
            feature,
            snapshot.celery_task_id,
            snapshot.current_step,
        )
        return updated_snapshot, True, "demoted"

    if requeue_count >= requeue_limit:
        _mark_business_processing_repair_limit_failed(
            current,
            queue=queue,
            feature=feature,
            requeue_count=requeue_count,
            requeue_limit=requeue_limit,
        )
        active_logger.warning(
            "Closed orphan business processing task after repair limit: task=%s feature=%s count=%s limit=%s",
            snapshot.id,
            feature,
            requeue_count,
            requeue_limit,
        )
        updated_snapshot = _detached_repair_task_snapshot(current)
        db.session.remove()
        return updated_snapshot, True, "closed"

    celery_callable = _repair_target_for_feature(feature)
    if celery_callable is None:
        _release_repair_db_session()
        return snapshot, False, "missing_repair_target"

    previous_celery_id = snapshot.celery_task_id
    previous_status = snapshot.status
    previous_step = snapshot.current_step
    _release_repair_db_session()
    try:
        submit_result = maybe_guarded_apply_async(
            celery_callable,
            args=[snapshot.id],
            queue=queue,
            feature=feature,
            task=snapshot,
            retry=False,
        )
        async_result = submit_result["async_result"]
    except Exception as exc:
        _release_repair_db_session()
        active_logger.warning(
            "Business processing orphan requeue failed: task=%s feature=%s previous_celery_id=%s error=%s",
            snapshot.id,
            feature,
            previous_celery_id,
            exc,
        )
        return snapshot, False, "publish_failed"

    current = _load_repair_task(snapshot.id, *PUBLISHER_REPAIR_SCAN_COLUMNS)
    if (
        not current
        or current.status != "processing"
        or current.celery_task_id != previous_celery_id
        or current.current_step != previous_step
    ):
        _release_then_revoke_published_repair_message(
            async_result,
            task_id=snapshot.id,
            log=active_logger,
        )
        return snapshot, False, "state_changed"

    now = datetime.utcnow().isoformat() + "Z"
    latest_result = dict(current.result or result)
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

    current.celery_task_id = async_result.id
    current.status = "pending"
    if not submit_result.get("deferred"):
        current.current_step = "queued"
        current.progress = 0
        current.error_message = None
    current.completed_at = None
    current.result = latest_result
    flag_modified(current, "result")
    updated_snapshot = _detached_repair_task_snapshot(current)
    db.session.commit()
    db.session.remove()

    active_logger.warning(
        "Requeued orphan business processing task: task=%s feature=%s previous_celery_id=%s new_celery_id=%s deferred=%s step=%s",
        snapshot.id,
        feature,
        previous_celery_id,
        async_result.id,
        bool(submit_result.get("deferred")),
        previous_step,
    )
    return updated_snapshot, True, "requeued"


def repair_stale_business_processing_tasks_once(
    *,
    limit: Optional[int] = None,
    excluded_features: Optional[Iterable[str]] = None,
    log: Optional[logging.Logger] = None,
) -> Dict[str, Any]:
    """Scan and requeue stale processing business task rows once."""
    active_logger = log or logger
    if not _truthy_env("BUSINESS_PROCESSING_ORPHAN_REPAIR_ENABLED", True):
        return {"enabled": False, "checked": 0, "requeued": 0, "demoted": 0, "closed": 0, "items": []}

    excluded = {str(item) for item in (excluded_features or []) if item}
    features = [item for item in BUSINESS_PROCESSING_REPAIRABLE_FEATURES.keys() if item not in excluded]
    if not features:
        return {"enabled": True, "checked": 0, "requeued": 0, "demoted": 0, "closed": 0, "items": [], "skipped": "all_features_paused"}

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
    demoted = 0
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
        elif action == "demoted":
            demoted += 1
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
        "demoted": demoted,
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


def _demote_business_processing_to_pending(
    task: PublicationTask,
    *,
    queue: str,
    feature: str,
    reason: str,
) -> None:
    """Restore DB state when the original Celery message still exists."""
    now = datetime.utcnow().isoformat() + "Z"
    previous_result = dict(task.result or {})
    repair_state = dict(previous_result.get(PROCESSING_REPAIR_RESULT_KEY) or {})
    history = list(repair_state.get("history") or [])
    history.append({
        "at": now,
        "feature": feature,
        "queue": queue,
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
        "feature": feature,
        "reason": reason,
        "history": history[-10:],
    })
    previous_result[PROCESSING_REPAIR_RESULT_KEY] = repair_state
    previous_result["queued"] = True
    previous_result["deferred"] = False

    task.status = "pending"
    task.current_step = "queued"
    task.progress = 0
    task.error_message = None
    task.completed_at = None
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
) -> bool:
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

    compare_and_swap = PublicationTask.query.filter(
        PublicationTask.id == task.id,
        PublicationTask.feature == feature,
        PublicationTask.status == task.status,
        PublicationTask.celery_task_id == task.celery_task_id,
        PublicationTask.current_step == task.current_step,
        PublicationTask.updated_at == task.updated_at,
    )
    updated = compare_and_swap.update({
        PublicationTask.status: "failed",
        PublicationTask.current_step: "failed",
        PublicationTask.progress: task.progress or 0,
        PublicationTask.error_message: "任务入队修复已达到上限，Celery 中也找不到对应消息，已自动标记失败。",
        PublicationTask.completed_at: now,
        PublicationTask.result: previous_result,
        PublicationTask.updated_at: now,
    }, synchronize_session=False)
    if updated != 1:
        db.session.rollback()
        return False
    db.session.commit()
    return True


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


def _publisher_child_repair_target(
    child: PublicationTask,
    *,
    default_queue: str,
    upload_projection: Optional[PublicationTaskBilibiliUpload] = None,
) -> Tuple[Callable[..., Any], str]:
    if upload_projection is not None:
        from tasks.publisher import run_bilibili_upload_only

        return run_bilibili_upload_only, PLATFORM_UPLOAD_QUEUE
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


def _cancel_orphan_workflow_child(child: PublicationTask, *, parent: PublicationTask):
    child_snapshot = _detached_repair_task_snapshot(child)
    parent_snapshot = SimpleNamespace(
        id=str(parent.id),
        feature=parent.feature,
        status=parent.status,
    )
    _release_repair_db_session()
    _revoke_celery_task(child_snapshot)
    current = _load_repair_task(
        child_snapshot.id,
        *WORKFLOW_CHILD_CLEANUP_SCAN_COLUMNS,
    )
    if (
        current is None
        or current.status != child_snapshot.status
        or current.current_step != child_snapshot.current_step
        or current.celery_task_id != child_snapshot.celery_task_id
    ):
        _release_repair_db_session()
        return None
    now = datetime.utcnow()
    previous_result = dict(current.result or {})
    cleanup_state = {
        "reason": "parent_workflow_terminal",
        "parent_task_id": parent_snapshot.id,
        "parent_feature": parent_snapshot.feature,
        "parent_status": parent_snapshot.status,
        "previous_status": child_snapshot.status,
        "previous_step": child_snapshot.current_step,
        "previous_celery_id": child_snapshot.celery_task_id,
        "cleaned_at": now.isoformat() + "Z",
    }
    message = f"父编排任务已终止({parent_snapshot.status})，遗留子任务自动收口。"
    current.result = {
        **previous_result,
        CHILD_CLEANUP_RESULT_KEY: cleanup_state,
        "error": message,
    }
    current.status = "canceled"
    current.current_step = "canceled"
    current.error_message = message
    current.completed_at = now
    flag_modified(current, "result")
    updated_snapshot = _detached_repair_task_snapshot(current)
    db.session.commit()
    db.session.remove()
    return updated_snapshot


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


def _mark_processing_publisher_orphan_failed(
    task: PublicationTask,
    *,
    queue: str,
    reason: str,
    revoke: bool = True,
) -> None:
    revoke_snapshot = SimpleNamespace(
        id=str(task.id),
        celery_task_id=str(task.celery_task_id or ""),
    )
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
    if revoke:
        _revoke_celery_task(revoke_snapshot)


def _revoke_celery_task(task: PublicationTask) -> None:
    task_id = str(getattr(task, "id", None) or "-")
    celery_task_id = str(getattr(task, "celery_task_id", None) or "").strip()
    if not celery_task_id:
        return
    revoke_celery_task_after_db_commit(
        celery_task_id,
        operation=f"publisher child cleanup revoke task={task_id}",
        business_task_id=task_id,
        log=logger,
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


def _age_seconds(
    value: Optional[datetime],
    *,
    now_utc: Optional[datetime] = None,
    now_local: Optional[datetime] = None,
) -> float:
    if not value:
        return 0
    now_utc = (now_utc or datetime.utcnow()).replace(tzinfo=None)
    candidate = value.replace(tzinfo=None)
    # PostgreSQL NOW() is returned as a naive value in the database session's
    # local timezone, while older ORM paths wrote naive UTC values.  A local
    # timestamp therefore appears several hours in the future to utcnow() and
    # used to keep orphan repairs inside their grace period forever.  Only
    # normalize timestamps that are clearly future-dated, preserving genuine
    # naive-UTC rows.
    if candidate > now_utc + timedelta(minutes=5):
        local_now = (now_local or datetime.now()).replace(tzinfo=None)
        local_offset = local_now - now_utc
        if abs(local_offset.total_seconds()) > 300:
            candidate -= local_offset
    return max(0.0, (now_utc - candidate).total_seconds())


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


def _pending_repair_grace_seconds(feature: Optional[str]) -> int:
    global_grace = _int_env(
        "BUSINESS_TASK_QUEUE_REPAIR_GRACE_SECONDS",
        DEFAULT_ORPHAN_GRACE_SECONDS,
        30,
    )
    feature_default = PENDING_REPAIR_GRACE_SECONDS_BY_FEATURE.get(str(feature or ""))
    if feature_default is None:
        return global_grace
    return _int_env(
        "BUSINESS_TASK_QUEUE_REPAIR_STICK_FIGURE_IMAGE_GRACE_SECONDS",
        max(global_grace, feature_default),
        30,
    )


def _repair_target_for_feature(feature: str) -> Optional[Callable[..., Any]]:
    if feature == "publisher":
        return run_publication_pipeline
    if feature == "mp3_video_workflow":
        from tasks.mp3_video_workflow import process_mp3_video_workflow_job

        return process_mp3_video_workflow_job
    if feature in {"report_audio_workflow", "report_pdf_audio_workflow"}:
        from tasks.report_audio_workflow import process_report_audio_workflow_job

        return process_report_audio_workflow_job
    if feature in {"content_pdf_clean", "content_audio_clean"}:
        from tasks.content_pipeline import run_audio_clean_task, run_pdf_clean_task

        return run_pdf_clean_task if feature == "content_pdf_clean" else run_audio_clean_task
    if feature == "report_top10_workflow":
        from tasks.report_top10_workflow import process_report_top10_workflow_job

        return process_report_top10_workflow_job
    if feature == "report_script_material_workflow":
        from tasks.report_script_material_workflow import process_report_script_material_workflow_job

        return process_report_script_material_workflow_job
    if feature == "report_visual_plan":
        from tasks.report_visual_plan import process_report_visual_plan_job

        return process_report_visual_plan_job
    if feature in {"report_pdf_gemini", "gemini_browser"}:
        from tasks.gemini_browser import process_report_pdf_gemini_job

        return process_report_pdf_gemini_job
    if feature == "doubao_browser_tts":
        from tasks.doubao_browser_tts import process_doubao_browser_tts_job

        return process_doubao_browser_tts_job
    if feature == "report_stick_figure_image":
        from tasks.report_stick_figure_assets import generate_scene_image

        return generate_scene_image
    if feature == "report_stick_figure_audio_alignment":
        from tasks.report_stick_figure_video import align_stick_figure_whole_audio

        return align_stick_figure_whole_audio
    if feature == "report_stick_figure_video_render":
        from tasks.report_stick_figure_video import render_stick_figure_video

        return render_stick_figure_video
    return task_for_unique_feature(feature)
