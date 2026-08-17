"""Durable keyframe-confirmation continuation for report PDF videos.

The report-audio parent is already terminal when a user approves keyframes.
Historically the approval endpoint tried to create the final publisher child
immediately and silently dropped scheme 3 while its visual assets were still
running.  This module stores the complete intent in a small child task and
reconciles it whenever any prerequisite changes (plus from the watchdog).
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import load_only
from sqlalchemy.orm.attributes import flag_modified

from models import ReportStickFigureAudioTrack, ReportStickFigureSceneAsset, db
from models.task import PublicationTask
from services.report_audio_completion_contract import (
    evaluate_report_video_completion,
    probe_report_media,
)


logger = logging.getLogger("REPORT_AUDIO_FINALIZATION")

FEATURE_NAME = "report_audio_finalization"
REPORT_AUDIO_FEATURE = "report_pdf_audio_workflow"
VISUAL_WORKFLOW_FEATURE = "report_stick_figure_video_workflow"
PUBLISHER_FEATURE = "publisher"
INTENT_KEY = "report_audio_finalization"
ACTIVE_STATUSES = {"pending", "processing"}
TERMINAL_STATUSES = {"completed", "failed", "canceled"}
MAX_VISUAL_RETRIES = 3
MAX_WHITEBOARD_RETRIES = 3
MAX_PUBLISHER_RETRIES = 2
RETRY_DELAY_SECONDS = 60
COMPLETION_CONTRACT_KEY = "completion_contract_version"
COMPLETION_CONTRACT_VERSION = 2
COMPLETION_MEDIA_PROBE = probe_report_media


def _normalize_schemes(value):
    raw = value if isinstance(value, (list, tuple, set)) else [value]
    result = []
    for item in raw:
        scheme = str(item or "").strip().lower()
        if scheme in {"scheme1", "scheme2", "scheme3"} and scheme not in result:
            result.append(scheme)
    return result


def _intent_options(*, include_landscape, schemes, visual_scheme_key, selection_source):
    return {
        "schema_version": "20260808_keyframe_finalization_v1",
        "include_landscape": bool(include_landscape),
        "vertical_video_schemes": _normalize_schemes(schemes),
        "visual_scheme_key": str(visual_scheme_key or "stick_figure").strip().lower() or "stick_figure",
        "selection_source": str(selection_source or "keyframe-confirmation").strip() or "keyframe-confirmation",
        "preview_seconds": 0,
    }


def _intent_signature(options):
    canonical = {
        "include_landscape": bool(options.get("include_landscape")),
        "vertical_video_schemes": _normalize_schemes(options.get("vertical_video_schemes")),
        "visual_scheme_key": str(options.get("visual_scheme_key") or "stick_figure"),
        "preview_seconds": 0,
    }
    return hashlib.sha256(
        json.dumps(canonical, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _task_options(task):
    params = task.params if isinstance(task.params, dict) else {}
    options = params.get(INTENT_KEY) if isinstance(params.get(INTENT_KEY), dict) else {}
    return dict(options)


def _task_result(task):
    return dict(task.result) if isinstance(task.result, dict) else {}


def _scope_lock(audio_task_id):
    """Serialize dispatch for one audio parent without requiring a new table."""

    try:
        bind = db.session.get_bind()
        if bind is not None and bind.dialect.name == "postgresql":
            db.session.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                {"key": f"report-audio-finalization:{audio_task_id}"},
            )
    except Exception:
        db.session.rollback()
        raise


def _next_retry_due(result):
    value = str(result.get("next_retry_at") or "").strip()
    if not value:
        return True
    try:
        parsed = datetime.fromisoformat(value.rstrip("Z"))
    except ValueError:
        return True
    return parsed <= datetime.utcnow()


def _retry_at():
    return (datetime.utcnow() + timedelta(seconds=RETRY_DELAY_SECONDS)).isoformat() + "Z"


def _update_finalization(
    task_id,
    *,
    status=None,
    step=None,
    progress=None,
    error_marker=False,
    error=None,
    result_patch=None,
    completed=False,
):
    task = PublicationTask.query.filter(
        PublicationTask.id == str(task_id),
        PublicationTask.feature == FEATURE_NAME,
    ).one_or_none()
    if not task:
        return None
    if status is not None:
        task.status = status
    if step is not None:
        task.current_step = step
    if progress is not None:
        task.progress = max(0, min(100, int(progress)))
    if error_marker:
        task.error_message = str(error or "")[:4000] or None
    if result_patch:
        result = _task_result(task)
        result.update(result_patch)
        result["updated_at"] = datetime.utcnow().isoformat() + "Z"
        task.result = result
        flag_modified(task, "result")
    task.updated_at = datetime.utcnow()
    if completed:
        task.completed_at = datetime.utcnow()
    elif status in ACTIVE_STATUSES:
        task.completed_at = None
    db.session.commit()
    return task


def _audio_path(task):
    if not task:
        return ""
    projected = str(getattr(task, "report_audio_wav_path", None) or "").strip()
    if projected:
        return projected
    result = _task_result(task)
    audio = result.get("audio") if isinstance(result.get("audio"), dict) else {}
    return str(audio.get("wav_path") or "").strip()


def _output_completion_contract(child, *, include_landscape, schemes, audio_path=""):
    if not child or child.status != "completed":
        return {
            "schema_version": "report_video_completion_v1",
            "ready": False,
            "outputs": {},
            "missing": ["publisher_child"],
        }
    result = _task_result(child)
    landscape = str(child.task_video_path or result.get("video_path") or "").strip()
    vertical_paths = result.get("vertical_video_paths") if isinstance(result.get("vertical_video_paths"), dict) else {}
    vertical_paths = dict(vertical_paths)
    for scheme in _normalize_schemes(schemes):
        path = str(vertical_paths.get(scheme) or "").strip()
        if not path and len(schemes) == 1:
            path = str(result.get("vertical_video_path") or child.publisher_vertical_video_path or "").strip()
        if path:
            vertical_paths[scheme] = path
    return evaluate_report_video_completion(
        include_landscape=bool(include_landscape),
        vertical_schemes=_normalize_schemes(schemes),
        landscape_path=landscape,
        vertical_paths=vertical_paths,
        audio_path=str(audio_path or ""),
        probe=COMPLETION_MEDIA_PROBE,
    )


def _output_paths_ready(child, *, include_landscape, schemes, audio_path=""):
    return _output_completion_contract(
        child,
        include_landscape=include_landscape,
        schemes=schemes,
        audio_path=audio_path,
    )["ready"]


def _completion_contract_ready(finalization, audio_task_id, options):
    """Verify every artifact promised by the confirmation intent exists."""

    result = _task_result(finalization)
    schemes = _normalize_schemes(options.get("vertical_video_schemes"))
    if "scheme3" in schemes:
        whiteboard_path = str(result.get("whiteboard_video_path") or "").strip()
        if not whiteboard_path or not Path(whiteboard_path).is_file():
            return False
    audio_task = PublicationTask.query.options(load_only(
        PublicationTask.id,
        PublicationTask.result,
        PublicationTask.report_audio_wav_path,
    )).filter(PublicationTask.id == str(audio_task_id)).one_or_none()
    child = _matching_publisher_child(finalization, audio_task_id, options)
    return _output_paths_ready(
        child,
        include_landscape=bool(options.get("include_landscape")),
        schemes=schemes,
        audio_path=_audio_path(audio_task),
    )


def _matching_publisher_child(finalization, audio_task_id, options):
    result = _task_result(finalization)
    explicit_id = str(result.get("publisher_task_id") or "").strip()
    if explicit_id:
        child = PublicationTask.query.filter(
            PublicationTask.id == explicit_id,
            PublicationTask.feature == PUBLISHER_FEATURE,
        ).one_or_none()
        if child:
            return child

    candidates = (
        PublicationTask.query
        .filter(
            PublicationTask.feature == PUBLISHER_FEATURE,
            PublicationTask.source_task_id == str(audio_task_id),
            PublicationTask.publisher_preview_seconds == 0,
        )
        .order_by(PublicationTask.created_at.desc())
        .limit(30)
        .all()
    )
    requested = _normalize_schemes(options.get("vertical_video_schemes"))
    for child in candidates:
        params = child.params if isinstance(child.params, dict) else {}
        metadata = params.get("metadata") if isinstance(params.get("metadata"), dict) else {}
        if str(metadata.get("finalization_task_id") or "") == str(finalization.id):
            return child
    # Adopt a pre-existing exact combined output.  This makes confirmation on
    # historical successful jobs idempotent instead of producing a duplicate.
    if bool(options.get("include_landscape")) and requested == ["scheme3"]:
        for child in candidates:
            if (
                child.publisher_landscape_enabled is True
                and child.publisher_vertical_enabled is True
                and str(child.publisher_vertical_scheme or "") == "scheme3"
            ):
                return child
    return None


def _visual_candidates(audio_task, visual_scheme_key):
    # Lazy import avoids the API/task module cycle during Celery bootstrap.
    from api.report_audio_workflow import _linked_visual_video_payloads

    visible_user = type("VisibleUser", (), {"id": audio_task.user_id})()
    requested = str(visual_scheme_key or "stick_figure").strip().lower()
    return [
        item for item in _linked_visual_video_payloads(visible_user, audio_task)
        if str(((item.get("visual_scheme") or {}).get("key")) or "").strip().lower() == requested
    ]


def _select_visual_candidate(candidates, preferred_id=""):
    preferred_id = str(preferred_id or "").strip()
    if preferred_id:
        preferred = next(
            (item for item in candidates if str(item.get("workflow_job_id") or "") == preferred_id),
            None,
        )
        if preferred and str(preferred.get("status") or "").lower() not in {"failed", "canceled"}:
            return preferred
    ready = next((item for item in candidates if item.get("scheme3_ready") is True), None)
    if ready:
        return ready
    active = next(
        (item for item in candidates if str(item.get("status") or "").lower() in {"queued", "running", "ready", "pending", "processing"}),
        None,
    )
    return active or (candidates[0] if candidates else None)


def _dispatch_visual(audio_task, options):
    from tasks.report_audio_workflow import _dispatch_requested_visual_workflows

    dispatch = _dispatch_requested_visual_workflows(
        str(audio_task.id),
        {"visual_scheme_keys": [options.get("visual_scheme_key") or "stick_figure"]},
    )
    items = dispatch.get("items") if isinstance(dispatch, dict) else []
    return (items or [{}])[0]


def _retry_failed_visual_assets(workflow_id):
    storyboard_id = (
        db.session.query(ReportStickFigureSceneAsset.storyboard_task_id)
        .filter(ReportStickFigureSceneAsset.workflow_task_id == str(workflow_id))
        .order_by(ReportStickFigureSceneAsset.sequence.asc())
        .limit(1)
        .scalar()
    )
    db.session.commit()
    if not storyboard_id:
        return None
    from tasks.report_visual_plan import _dispatch_auto_scene_images

    return _dispatch_auto_scene_images(str(storyboard_id), str(workflow_id))


def _visual_inputs(audio_task, workflow_id):
    from api.report_audio_workflow import (
        _linked_visual_scene_timeline,
        _linked_visual_video_path,
    )

    visible_user = type("VisibleUser", (), {"id": audio_task.user_id})()
    audio_result = _task_result(audio_task)
    audio = audio_result.get("audio") if isinstance(audio_result.get("audio"), dict) else {}
    try:
        seconds = float(audio.get("seconds") or audio_task.report_audio_seconds or 0)
    except (TypeError, ValueError):
        seconds = 0
    return {
        "scene_timeline": _linked_visual_scene_timeline(
            visible_user,
            audio_task,
            str(workflow_id),
            audio_seconds=seconds,
        ),
        "video_path": _linked_visual_video_path(visible_user, audio_task, str(workflow_id)),
    }


def _latest_visual_child(workflow_id, feature):
    return (
        PublicationTask.query.options(load_only(
            PublicationTask.id,
            PublicationTask.status,
            PublicationTask.current_step,
            PublicationTask.error_message,
            PublicationTask.task_video_path,
            PublicationTask.created_at,
            PublicationTask.updated_at,
        ))
        .filter(
            PublicationTask.feature == str(feature),
            PublicationTask.parent_task_id == str(workflow_id),
        )
        .order_by(PublicationTask.created_at.desc())
        .first()
    )


def _whiteboard_video_state(workflow_id, visual_inputs):
    """Describe the independently rendered whiteboard MP4 prerequisite."""

    path = str((visual_inputs or {}).get("video_path") or "").strip()
    if path and Path(path).is_file():
        return {"ready": True, "video_path": path, "status": "completed"}

    render = _latest_visual_child(workflow_id, "report_stick_figure_video_render")
    alignment = _latest_visual_child(workflow_id, "report_stick_figure_audio_alignment")
    active = next(
        (
            child for child in (render, alignment)
            if child and str(child.status or "") in ACTIVE_STATUSES
        ),
        None,
    )
    failed = next(
        (
            child for child in (render, alignment)
            if child and str(child.status or "") in {"failed", "canceled"}
        ),
        None,
    )
    return {
        "ready": False,
        "status": str(getattr(active or failed, "status", "") or "waiting"),
        "step": str(getattr(active or failed, "current_step", "") or ""),
        "active_task_id": str(getattr(active, "id", "") or "") or None,
        "failed_task_id": str(getattr(failed, "id", "") or "") or None,
        "error": str(getattr(failed, "error_message", "") or "") or None,
    }


def _resume_whiteboard_video(workflow_id, state):
    """Idempotently restore alignment/render without regenerating completed images."""

    if (state or {}).get("ready") or (state or {}).get("active_task_id"):
        return {"enqueued": False, "reason": "ready_or_active"}

    track = (
        ReportStickFigureAudioTrack.query.options(load_only(
            ReportStickFigureAudioTrack.id,
            ReportStickFigureAudioTrack.status,
            ReportStickFigureAudioTrack.audio_path,
            ReportStickFigureAudioTrack.manifest_path,
        ))
        .filter(ReportStickFigureAudioTrack.workflow_task_id == str(workflow_id))
        .one_or_none()
    )
    track_ready = bool(
        track
        and str(track.status or "") == "completed"
        and track.audio_path
        and Path(str(track.audio_path)).is_file()
        and track.manifest_path
        and Path(str(track.manifest_path)).is_file()
    )
    if not track_ready:
        dispatch = _retry_failed_visual_assets(workflow_id)
        return {
            "enqueued": bool(dispatch),
            "reason": "alignment_reconciled",
            "dispatch": dispatch,
        }

    # A failed render leaves the parent terminal even though all durable inputs
    # remain reusable. Restore only the ready marker; the enqueue helper claims
    # it atomically and refuses duplicate active/completed render children.
    now = datetime.utcnow()
    PublicationTask.query.filter(
        PublicationTask.id == str(workflow_id),
        PublicationTask.feature == VISUAL_WORKFLOW_FEATURE,
        PublicationTask.status != "canceled",
    ).update({
        PublicationTask.status: "processing",
        PublicationTask.current_step: "whole_audio_aligned",
        PublicationTask.progress: 82,
        PublicationTask.error_message: None,
        PublicationTask.completed_at: None,
        PublicationTask.updated_at: now,
    }, synchronize_session=False)
    db.session.commit()
    from tasks.report_stick_figure_video import maybe_auto_enqueue_stick_figure_video

    return maybe_auto_enqueue_stick_figure_video(str(workflow_id))


def _whiteboard_retry_state(finalization_state, whiteboard_state):
    attempts = int(finalization_state.get("whiteboard_attempts") or 0)
    failed_ids = [
        str(item) for item in (finalization_state.get("whiteboard_failed_task_ids") or [])
        if str(item or "").strip()
    ]
    failed_id = str((whiteboard_state or {}).get("failed_task_id") or "").strip()
    if failed_id and failed_id not in failed_ids:
        failed_ids.append(failed_id)
        attempts += 1
    return attempts, failed_ids[-MAX_WHITEBOARD_RETRIES:]


def create_or_resume_finalization(
    audio_task_id,
    *,
    include_landscape=True,
    schemes=("scheme3",),
    visual_scheme_key="stick_figure",
    selection_source="keyframe-confirmation",
    force=False,
):
    options = _intent_options(
        include_landscape=include_landscape,
        schemes=schemes,
        visual_scheme_key=visual_scheme_key,
        selection_source=selection_source,
    )
    if not options["include_landscape"] and not options["vertical_video_schemes"]:
        raise ValueError("请至少选择一个成片版本")
    if options["vertical_video_schemes"] != ["scheme3"] or not options["include_landscape"]:
        raise ValueError("关键帧确认链路当前固定生成横屏 + 方案三竖屏")

    audio_task_id = str(audio_task_id or "").strip()
    _scope_lock(audio_task_id)
    audio_task = PublicationTask.query.filter(
        PublicationTask.id == audio_task_id,
        PublicationTask.feature == REPORT_AUDIO_FEATURE,
    ).with_for_update().one_or_none()
    if not audio_task:
        raise ValueError("研报转语音任务不存在")
    signature = _intent_signature(options)
    candidates = (
        PublicationTask.query
        .filter(
            PublicationTask.feature == FEATURE_NAME,
            PublicationTask.source_task_id == audio_task_id,
        )
        .order_by(PublicationTask.created_at.desc())
        .limit(20)
        .all()
    )
    existing = next(
        (item for item in candidates if _task_result(item).get("intent_signature") == signature),
        None,
    )
    if existing:
        if existing.status in {"failed", "canceled"} and force:
            result = _task_result(existing)
            result.update({
                "manual_resume_count": int(result.get("manual_resume_count") or 0) + 1,
                "next_retry_at": None,
                "visual_attempts": 0,
                "publisher_attempts": 0,
            })
            existing.status = "processing"
            existing.current_step = "confirmed"
            existing.progress = max(5, int(existing.progress or 0))
            existing.error_message = None
            existing.completed_at = None
            existing.result = result
            flag_modified(existing, "result")
            db.session.commit()
        else:
            db.session.commit()
        _reconcile_after_confirmation(existing.id)
        return PublicationTask.query.filter(PublicationTask.id == existing.id).one()

    finalization = PublicationTask(
        status="processing",
        current_step="confirmed",
        progress=5,
        feature=FEATURE_NAME,
        user_id=str(audio_task.user_id or ""),
        parent_task_id=audio_task_id,
        parent_feature=REPORT_AUDIO_FEATURE,
        source_task_id=audio_task_id,
        source_feature=REPORT_AUDIO_FEATURE,
        params={
            "feature": FEATURE_NAME,
            "user_id": str(audio_task.user_id or ""),
            "parent_task_id": audio_task_id,
            "parent_feature": REPORT_AUDIO_FEATURE,
            "source_task_id": audio_task_id,
            "source_feature": REPORT_AUDIO_FEATURE,
            INTENT_KEY: options,
        },
        result={
            "intent_signature": signature,
            "confirmed_at": datetime.utcnow().isoformat() + "Z",
            "visual_attempts": 0,
            "publisher_attempts": 0,
        },
    )
    finalization.task_title = f"{audio_task.task_title or '研报'} · 白板 + 横屏 + 方案三"
    db.session.add(finalization)
    db.session.commit()
    finalization_id = str(finalization.id)
    _reconcile_after_confirmation(finalization_id)
    return PublicationTask.query.filter(PublicationTask.id == finalization_id).one()


def _reconcile_after_confirmation(finalization_task_id):
    """Keep a durable accepted intent even when immediate dispatch is unavailable."""

    try:
        return reconcile_finalization(finalization_task_id)
    except Exception as exc:
        db.session.rollback()
        logger.exception(
            "Immediate finalization reconcile failed; watchdog will retry task=%s",
            finalization_task_id,
        )
        _update_finalization(
            finalization_task_id,
            status="processing",
            step="reconcile_retry_wait",
            progress=10,
            error_marker=True,
            error=None,
            result_patch={
                "last_reconcile_error": str(exc),
                "next_retry_at": _retry_at(),
                "retryable": True,
                "message": "全链路已确认，当前投递暂不可用，系统将自动重试",
            },
        )
        return {"reconciled": False, "reason": "retry_scheduled", "error": str(exc)}


def reconcile_finalization(finalization_task_id):
    finalization_task_id = str(finalization_task_id or "").strip()
    finalization = PublicationTask.query.filter(
        PublicationTask.id == finalization_task_id,
        PublicationTask.feature == FEATURE_NAME,
    ).one_or_none()
    if not finalization:
        return {"reconciled": False, "reason": "missing_finalization"}
    if finalization.status == "canceled":
        return {"reconciled": False, "reason": "terminal", "task_id": finalization_task_id}

    audio_task_id = str(finalization.source_task_id or finalization.parent_task_id or "").strip()
    _scope_lock(audio_task_id)
    # Reload after the lock so concurrent image/publisher callbacks cannot both
    # enqueue the same downstream task.
    finalization = PublicationTask.query.filter(
        PublicationTask.id == finalization_task_id,
        PublicationTask.feature == FEATURE_NAME,
    ).one()
    options = _task_options(finalization)
    state = _task_result(finalization)
    if finalization.status == "completed":
        if _completion_contract_ready(finalization, audio_task_id, options):
            if int(state.get(COMPLETION_CONTRACT_KEY) or 0) < COMPLETION_CONTRACT_VERSION:
                _update_finalization(
                    finalization_task_id,
                    result_patch={COMPLETION_CONTRACT_KEY: COMPLETION_CONTRACT_VERSION},
                )
            return {
                "reconciled": False,
                "reason": "terminal",
                "task_id": finalization_task_id,
            }

        # Old coordinators could be marked completed as soon as the combined
        # landscape/vertical publisher finished, even if the standalone
        # whiteboard MP4 was still absent. Reopen those rows once and let the
        # normal idempotent state machine adopt existing assets and finish only
        # after all three promised files pass the filesystem checks above.
        reopened_at = datetime.utcnow().isoformat() + "Z"
        _update_finalization(
            finalization_task_id,
            status="processing",
            step="completion_contract_reconcile",
            progress=min(95, max(55, int(finalization.progress or 0))),
            error_marker=True,
            error=None,
            result_patch={
                "legacy_completion_reopened_at": (
                    state.get("legacy_completion_reopened_at") or reopened_at
                ),
                "legacy_completion_previous_completed_at": (
                    finalization.completed_at.isoformat() + "Z"
                    if finalization.completed_at else None
                ),
                "retryable": True,
                "message": "旧完成记录缺少白板成片，系统正在自动补齐，不重做已完成产物",
            },
        )
        finalization = PublicationTask.query.filter(
            PublicationTask.id == finalization_task_id,
            PublicationTask.feature == FEATURE_NAME,
        ).one()
        options = _task_options(finalization)
        state = _task_result(finalization)
    audio_task = PublicationTask.query.filter(
        PublicationTask.id == audio_task_id,
        PublicationTask.feature == REPORT_AUDIO_FEATURE,
    ).one_or_none()
    if not audio_task:
        _update_finalization(
            finalization_task_id,
            status="failed",
            step="audio_missing",
            progress=5,
            error_marker=True,
            error="研报转语音任务不存在",
            result_patch={"retryable": False},
            completed=True,
        )
        return {"reconciled": True, "reason": "audio_missing"}

    audio_path = str(audio_task.report_audio_wav_path or "").strip()
    if (
        audio_task.status != "completed"
        or int(audio_task.final_script_chars or 0) <= 0
        or not audio_path
        or not Path(audio_path).is_file()
    ):
        _update_finalization(
            finalization_task_id,
            status="processing",
            step="waiting_audio",
            progress=10,
            error_marker=True,
            error=None,
            result_patch={"message": "等待完整文案和 WAV 就绪"},
        )
        return {"reconciled": True, "reason": "waiting_audio"}

    schemes = _normalize_schemes(options.get("vertical_video_schemes"))
    visual_workflow_id = str(state.get("visual_workflow_id") or "").strip()
    visual_inputs = {"scene_timeline": None, "video_path": None}
    whiteboard_state = {"ready": True, "status": "not_required"}
    if "scheme3" in schemes:
        candidates = _visual_candidates(audio_task, options.get("visual_scheme_key"))
        visual = _select_visual_candidate(candidates, visual_workflow_id)
        if not visual:
            if not _next_retry_due(state):
                return {"reconciled": False, "reason": "visual_retry_delayed"}
            dispatch = _dispatch_visual(audio_task, options)
            visual_attempts = int(state.get("visual_attempts") or 0) + 1
            visual_workflow_id = str(dispatch.get("workflow_job_id") or "").strip()
            if str(dispatch.get("status") or "").lower() == "failed":
                error = str(dispatch.get("error") or "白板分镜投递失败")
                terminal = visual_attempts >= MAX_VISUAL_RETRIES
                _update_finalization(
                    finalization_task_id,
                    status="failed" if terminal else "processing",
                    step="visual_failed" if terminal else "visual_retry_wait",
                    progress=20,
                    error_marker=True,
                    error=error if terminal else None,
                    result_patch={
                        "visual_attempts": visual_attempts,
                        "visual_workflow_id": visual_workflow_id or None,
                        "last_visual_error": error,
                        "next_retry_at": None if terminal else _retry_at(),
                        "retryable": True,
                        "message": "白板分镜暂时失败，系统将自动重试" if not terminal else "白板分镜连续失败，可点击安全续跑",
                    },
                    completed=terminal,
                )
                return {"reconciled": True, "reason": "visual_dispatch_failed", "terminal": terminal}
            _update_finalization(
                finalization_task_id,
                status="processing",
                step="visual_queued",
                progress=20,
                error_marker=True,
                error=None,
                result_patch={
                    "visual_attempts": visual_attempts,
                    "visual_workflow_id": visual_workflow_id or None,
                    "next_retry_at": None,
                    "message": "白板任务已创建，正在生成分镜与图片",
                },
            )
            return {"reconciled": True, "reason": "visual_dispatched", "workflow_id": visual_workflow_id}

        visual_workflow_id = str(visual.get("workflow_job_id") or "").strip()
        visual_status = str(visual.get("status") or "").lower()
        visual_step = str(visual.get("step") or "").lower()
        if visual_status in {"failed", "canceled"}:
            if not _next_retry_due(state):
                return {"reconciled": False, "reason": "visual_retry_delayed"}
            visual_attempts = int(state.get("visual_attempts") or 0) + 1
            if visual_attempts >= MAX_VISUAL_RETRIES:
                error = str(visual.get("error") or "白板任务连续失败")
                _update_finalization(
                    finalization_task_id,
                    status="failed",
                    step="visual_failed",
                    progress=25,
                    error_marker=True,
                    error=error,
                    result_patch={
                        "visual_attempts": visual_attempts,
                        "visual_workflow_id": visual_workflow_id,
                        "last_visual_error": error,
                        "next_retry_at": None,
                        "retryable": True,
                        "message": "白板任务连续失败，可点击安全续跑",
                    },
                    completed=True,
                )
                return {"reconciled": True, "reason": "visual_failed", "terminal": True}
            retried = None
            if visual_step in {"images_failed", "image_submit_failed"}:
                try:
                    retried = _retry_failed_visual_assets(visual_workflow_id)
                except Exception as exc:
                    db.session.rollback()
                    logger.exception("Failed to retry visual assets workflow=%s", visual_workflow_id)
                    retried = {"error": str(exc)}
            if retried is None:
                dispatch = _dispatch_visual(audio_task, options)
                visual_workflow_id = str(dispatch.get("workflow_job_id") or visual_workflow_id)
                retried = dispatch
            error = str(visual.get("error") or retried.get("error") or "白板任务失败")
            retry_started = (
                bool(str(retried.get("workflow_job_id") or "").strip())
                and str(retried.get("status") or "").lower() != "failed"
            )
            _update_finalization(
                finalization_task_id,
                status="processing",
                step="visual_queued" if retry_started else "visual_retry_wait",
                progress=25,
                error_marker=True,
                error=None,
                result_patch={
                    "visual_attempts": visual_attempts,
                    "visual_workflow_id": visual_workflow_id,
                    "last_visual_error": error,
                    "next_retry_at": None if retry_started else _retry_at(),
                    "retryable": True,
                    "message": (
                        "旧白板任务失败；已新建安全续跑任务，正在生成分镜与图片"
                        if retry_started
                        else "白板任务暂时失败，系统将在稍后自动重试"
                    ),
                },
            )
            return {"reconciled": True, "reason": "visual_retried", "terminal": False}

        if visual.get("scheme3_ready") is not True:
            _update_finalization(
                finalization_task_id,
                status="processing",
                step="waiting_visual",
                progress=max(25, min(55, int(visual.get("progress") or 0) // 2)),
                error_marker=True,
                error=None,
                result_patch={
                    "visual_workflow_id": visual_workflow_id,
                    "message": "白板分镜与图片生成中；完成后自动合成横屏 + 方案三",
                },
            )
            return {"reconciled": True, "reason": "waiting_visual", "workflow_id": visual_workflow_id}

        visual_inputs = _visual_inputs(audio_task, visual_workflow_id)
        if not visual_inputs.get("scene_timeline") and not visual_inputs.get("video_path"):
            _update_finalization(
                finalization_task_id,
                status="processing",
                step="waiting_visual",
                progress=55,
                result_patch={
                    "visual_workflow_id": visual_workflow_id,
                    "message": "白板图片已完成，正在同步方案三时间轴",
                },
            )
            return {"reconciled": True, "reason": "waiting_visual_inputs"}

        # Scheme 3 can render directly from completed scene images, so the
        # combined publisher may run in parallel with the standalone whiteboard
        # MP4.  Completion, however, must require both artifacts.  Otherwise a
        # fast publisher can produce a false green state while the whiteboard
        # render is missing or failed.
        whiteboard_state = _whiteboard_video_state(
            visual_workflow_id,
            visual_inputs,
        )
        whiteboard_attempts, failed_ids = _whiteboard_retry_state(
            state,
            whiteboard_state,
        )
        if (
            not whiteboard_state.get("ready")
            and whiteboard_state.get("failed_task_id")
            and whiteboard_attempts >= MAX_WHITEBOARD_RETRIES
        ):
            error = str(
                whiteboard_state.get("error")
                or "白板横屏成片连续失败"
            )
            _update_finalization(
                finalization_task_id,
                status="failed",
                step="whiteboard_video_failed",
                progress=58,
                error_marker=True,
                error=error,
                result_patch={
                    "visual_workflow_id": visual_workflow_id,
                    "whiteboard_attempts": whiteboard_attempts,
                    "whiteboard_failed_task_ids": failed_ids,
                    "last_whiteboard_error": error,
                    "next_retry_at": None,
                    "retryable": True,
                    "message": "白板横屏连续失败，可点击安全续跑；文案、音频和图片不会重做",
                },
                completed=True,
            )
            return {
                "reconciled": True,
                "reason": "whiteboard_video_failed",
                "terminal": True,
            }

        resume_result = None
        if (
            not whiteboard_state.get("ready")
            and not whiteboard_state.get("active_task_id")
            and _next_retry_due(state)
        ):
            try:
                resume_result = _resume_whiteboard_video(
                    visual_workflow_id,
                    whiteboard_state,
                )
                visual_inputs = _visual_inputs(audio_task, visual_workflow_id)
                whiteboard_state = _whiteboard_video_state(
                    visual_workflow_id,
                    visual_inputs,
                )
            except Exception as exc:
                db.session.rollback()
                whiteboard_attempts += 1
                error = str(exc)
                terminal = whiteboard_attempts >= MAX_WHITEBOARD_RETRIES
                _update_finalization(
                    finalization_task_id,
                    status="failed" if terminal else "processing",
                    step=(
                        "whiteboard_video_failed"
                        if terminal else "whiteboard_video_retry_wait"
                    ),
                    progress=58,
                    error_marker=True,
                    error=error if terminal else None,
                    result_patch={
                        "visual_workflow_id": visual_workflow_id,
                        "whiteboard_attempts": whiteboard_attempts,
                        "whiteboard_failed_task_ids": failed_ids,
                        "last_whiteboard_error": error,
                        "next_retry_at": None if terminal else _retry_at(),
                        "retryable": True,
                        "message": (
                            "白板横屏续跑暂不可用，系统将自动重试"
                            if not terminal
                            else "白板横屏连续失败，可点击安全续跑；文案、音频和图片不会重做"
                        ),
                    },
                    completed=terminal,
                )
                if terminal:
                    return {
                        "reconciled": True,
                        "reason": "whiteboard_video_failed",
                        "terminal": True,
                    }
        _update_finalization(
            finalization_task_id,
            status="processing",
            step=(
                "whiteboard_video_ready"
                if whiteboard_state.get("ready")
                else "waiting_whiteboard_video"
            ),
            progress=60 if whiteboard_state.get("ready") else 58,
            error_marker=True,
            error=None,
            result_patch={
                "visual_workflow_id": visual_workflow_id,
                "whiteboard_attempts": whiteboard_attempts,
                "whiteboard_failed_task_ids": failed_ids,
                "whiteboard_video_path": whiteboard_state.get("video_path"),
                "whiteboard_resume": resume_result,
                "message": (
                    "白板横屏已完成，正在合成横屏 + 方案三"
                    if whiteboard_state.get("ready")
                    else "白板横屏正在对齐/合成；横屏 + 方案三可并行生成"
                ),
            },
        )

    # Release any ORM snapshots expired by the visual dispatch commits, then
    # find/adopt the exact publisher child under the same advisory lock.
    db.session.expire_all()
    finalization = PublicationTask.query.filter(PublicationTask.id == finalization_task_id).one()
    options = _task_options(finalization)
    state = _task_result(finalization)
    audio_task = PublicationTask.query.filter(PublicationTask.id == audio_task_id).one()
    child = _matching_publisher_child(finalization, audio_task_id, options)
    if child and child.status in ACTIVE_STATUSES:
        _update_finalization(
            finalization_task_id,
            status="processing",
            step="final_video_queued",
            progress=max(65, min(95, int(child.progress or 0))),
            error_marker=True,
            error=None,
            result_patch={
                "visual_workflow_id": visual_workflow_id or None,
                "publisher_task_id": str(child.id),
                "message": (
                    "白板横屏已完成，横屏 + 方案三正在合成"
                    if whiteboard_state.get("ready")
                    else "白板横屏与横屏 + 方案三正在并行合成"
                ),
            },
        )
        return {"reconciled": True, "reason": "publisher_active", "publisher_task_id": str(child.id)}
    publisher_completion = _output_completion_contract(
        child,
        include_landscape=bool(options.get("include_landscape")),
        schemes=schemes,
        audio_path=_audio_path(audio_task),
    )
    if child and publisher_completion["ready"]:
        if not whiteboard_state.get("ready"):
            _update_finalization(
                finalization_task_id,
                status="processing",
                step="waiting_whiteboard_video",
                progress=95,
                error_marker=True,
                error=None,
                result_patch={
                    "visual_workflow_id": visual_workflow_id or None,
                    "publisher_task_id": str(child.id),
                    "message": "横屏 + 方案三已完成；正在等待白板横屏成片",
                },
            )
            return {
                "reconciled": True,
                "reason": "waiting_whiteboard_video",
                "publisher_task_id": str(child.id),
            }
        child_result = _task_result(child)
        _update_finalization(
            finalization_task_id,
            status="completed",
            step="done",
            progress=100,
            error_marker=True,
            error=None,
            result_patch={
                "visual_workflow_id": visual_workflow_id or None,
                "publisher_task_id": str(child.id),
                "whiteboard_video_path": whiteboard_state.get("video_path"),
                "landscape_video_path": child_result.get("video_path") or child.task_video_path,
                "vertical_video_path": child_result.get("vertical_video_path") or child.publisher_vertical_video_path,
                "completion_contract": publisher_completion,
                COMPLETION_CONTRACT_KEY: COMPLETION_CONTRACT_VERSION,
                "message": "白板、横屏和方案三竖屏均已完成",
                "retryable": False,
            },
            completed=True,
        )
        return {"reconciled": True, "reason": "completed", "publisher_task_id": str(child.id)}
    if child and child.status == "completed":
        _update_finalization(
            finalization_task_id,
            status="failed",
            step="completion_contract_failed",
            progress=max(95, int(child.progress or 0)),
            error_marker=True,
            error="成片未通过完整性验收",
            result_patch={
                "publisher_task_id": str(child.id),
                "completion_contract": publisher_completion,
                "retryable": True,
                "message": "成片文件不完整或质量检查失败；已保留现有文案、音频和视觉资产",
            },
            completed=True,
        )
        return {
            "reconciled": True,
            "reason": "completion_contract_failed",
            "publisher_task_id": str(child.id),
            "missing": list(publisher_completion.get("missing") or []),
        }
    if child and child.status in {"failed", "canceled"}:
        publisher_attempts = int(state.get("publisher_attempts") or 0)
        if publisher_attempts >= MAX_PUBLISHER_RETRIES or not _next_retry_due(state):
            terminal = publisher_attempts >= MAX_PUBLISHER_RETRIES
            if terminal:
                _update_finalization(
                    finalization_task_id,
                    status="failed",
                    step="final_video_failed",
                    progress=max(65, int(child.progress or 0)),
                    error_marker=True,
                    error=child.error_message or "横屏 + 方案三合成失败",
                    result_patch={
                        "publisher_task_id": str(child.id),
                        "retryable": True,
                        "message": "成片连续失败，可点击安全续跑；白板、文案和音频不会重做",
                    },
                    completed=True,
                )
            return {"reconciled": False, "reason": "publisher_retry_delayed" if not terminal else "publisher_failed"}

    from api.report_audio_workflow import _create_vertical_video_backfill_task

    publisher_attempts = int(state.get("publisher_attempts") or 0) + 1
    try:
        child = _create_vertical_video_backfill_task(
            audio_task,
            scheme="scheme3",
            schemes=schemes,
            preview_seconds=0,
            include_landscape=bool(options.get("include_landscape")),
            visual_workflow_id=visual_workflow_id or None,
            visual_video_path=visual_inputs.get("video_path"),
            visual_scene_timeline=visual_inputs.get("scene_timeline"),
            finalization_task_id=finalization_task_id,
        )
    except Exception as exc:
        db.session.rollback()
        terminal = publisher_attempts >= MAX_PUBLISHER_RETRIES
        _update_finalization(
            finalization_task_id,
            status="failed" if terminal else "processing",
            step="final_video_failed" if terminal else "final_video_retry_wait",
            progress=60,
            error_marker=True,
            error=str(exc) if terminal else None,
            result_patch={
                "publisher_attempts": publisher_attempts,
                "last_publisher_error": str(exc),
                "next_retry_at": None if terminal else _retry_at(),
                "retryable": True,
                "message": "成片投递暂时失败，系统将自动重试" if not terminal else "成片连续失败，可点击安全续跑",
            },
            completed=terminal,
        )
        return {"reconciled": True, "reason": "publisher_submit_failed", "terminal": terminal}

    _update_finalization(
        finalization_task_id,
        status="processing",
        step="final_video_queued",
        progress=65,
        error_marker=True,
        error=None,
        result_patch={
            "visual_workflow_id": visual_workflow_id or None,
            "publisher_task_id": str(child.id),
            "publisher_attempts": publisher_attempts,
            "next_retry_at": None,
            "message": "白板已就绪，横屏 + 方案三已自动投递",
        },
    )
    return {"reconciled": True, "reason": "publisher_dispatched", "publisher_task_id": str(child.id)}


def finalization_summary(task):
    if not task:
        return None
    options = _task_options(task)
    result = _task_result(task)
    return {
        "job_id": str(task.id),
        "status": str(task.status or "pending"),
        "step": str(task.current_step or "confirmed"),
        "progress": int(task.progress or 0),
        "error": task.error_message,
        "include_landscape": bool(options.get("include_landscape")),
        "vertical_video_schemes": _normalize_schemes(options.get("vertical_video_schemes")),
        "visual_scheme_key": str(options.get("visual_scheme_key") or "stick_figure"),
        "visual_workflow_id": result.get("visual_workflow_id"),
        "whiteboard_video_path": result.get("whiteboard_video_path"),
        "whiteboard_attempts": int(result.get("whiteboard_attempts") or 0),
        "publisher_task_id": result.get("publisher_task_id"),
        "completion_contract": (
            result.get("completion_contract")
            if isinstance(result.get("completion_contract"), dict)
            else None
        ),
        "message": result.get("message"),
        "retryable": bool(result.get("retryable")),
        "created_at": task.created_at.isoformat() + "Z" if task.created_at else None,
        "updated_at": task.updated_at.isoformat() + "Z" if task.updated_at else None,
        "completed_at": task.completed_at.isoformat() + "Z" if task.completed_at else None,
    }


def latest_finalization_for_audio(audio_task_id):
    return (
        PublicationTask.query
        .options(load_only(
            PublicationTask.id,
            PublicationTask.status,
            PublicationTask.current_step,
            PublicationTask.progress,
            PublicationTask.params,
            PublicationTask.result,
            PublicationTask.error_message,
            PublicationTask.created_at,
            PublicationTask.updated_at,
            PublicationTask.completed_at,
        ))
        .filter(
            PublicationTask.feature == FEATURE_NAME,
            PublicationTask.source_task_id == str(audio_task_id),
        )
        .order_by(PublicationTask.created_at.desc())
        .first()
    )


def finalization_summary_for_audio(audio_task_id):
    task_id = str(audio_task_id or "").strip()
    if not task_id:
        return None
    return finalization_summaries_for_audio_ids([task_id]).get(task_id)


def _projected_schemes(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    return _normalize_schemes(value)


def _projected_bool(value):
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _summary_timestamp(value):
    return value.isoformat() + "Z" if value else None


def finalization_summaries_for_audio_ids(audio_task_ids):
    """Load latest finalization summaries for one bounded API page.

    The projection intentionally extracts only response fields from JSON. It
    preserves the stored audit payload while avoiding both an N+1 query and a
    multi-row transfer of complete ``params``/``result`` documents.
    """

    ids = list(dict.fromkeys(
        str(value).strip() for value in (audio_task_ids or []) if str(value).strip()
    ))[:100]
    if not ids:
        return {}

    options = PublicationTask.params[INTENT_KEY]
    result = PublicationTask.result
    rows = (
        db.session.query(
            PublicationTask.source_task_id.label("audio_task_id"),
            PublicationTask.id.label("job_id"),
            PublicationTask.status.label("status"),
            PublicationTask.current_step.label("current_step"),
            PublicationTask.progress.label("progress"),
            PublicationTask.error_message.label("error_message"),
            PublicationTask.created_at.label("created_at"),
            PublicationTask.updated_at.label("updated_at"),
            PublicationTask.completed_at.label("completed_at"),
            options["include_landscape"].as_boolean().label("include_landscape"),
            options["vertical_video_schemes"].label("vertical_video_schemes"),
            options["visual_scheme_key"].as_string().label("visual_scheme_key"),
            result["visual_workflow_id"].as_string().label("visual_workflow_id"),
            result["whiteboard_video_path"].as_string().label("whiteboard_video_path"),
            result["whiteboard_attempts"].as_integer().label("whiteboard_attempts"),
            result["publisher_task_id"].as_string().label("publisher_task_id"),
            result["message"].as_string().label("message"),
            result["retryable"].as_boolean().label("retryable"),
        )
        .filter(
            PublicationTask.feature == FEATURE_NAME,
            PublicationTask.source_task_id.in_(ids),
        )
        .order_by(
            PublicationTask.source_task_id.asc(),
            PublicationTask.created_at.desc(),
        )
        .all()
    )
    summaries = {}
    for row in rows:
        audio_task_id = str(row.audio_task_id or "")
        if not audio_task_id or audio_task_id in summaries:
            continue
        summaries[audio_task_id] = {
            "job_id": str(row.job_id),
            "status": str(row.status or "pending"),
            "step": str(row.current_step or "confirmed"),
            "progress": int(row.progress or 0),
            "error": row.error_message,
            "include_landscape": _projected_bool(row.include_landscape),
            "vertical_video_schemes": _projected_schemes(row.vertical_video_schemes),
            "visual_scheme_key": str(row.visual_scheme_key or "stick_figure"),
            "visual_workflow_id": row.visual_workflow_id,
            "whiteboard_video_path": row.whiteboard_video_path,
            "whiteboard_attempts": int(row.whiteboard_attempts or 0),
            "publisher_task_id": row.publisher_task_id,
            "message": row.message,
            "retryable": _projected_bool(row.retryable),
            "created_at": _summary_timestamp(row.created_at),
            "updated_at": _summary_timestamp(row.updated_at),
            "completed_at": _summary_timestamp(row.completed_at),
        }
    return summaries


def reconcile_finalizations_for_audio(audio_task_id):
    ids = [
        str(row.id) for row in (
            PublicationTask.query.options(load_only(PublicationTask.id))
            .filter(
                PublicationTask.feature == FEATURE_NAME,
                PublicationTask.source_task_id == str(audio_task_id),
                PublicationTask.status.in_(tuple(ACTIVE_STATUSES)),
            )
            .order_by(PublicationTask.created_at.asc())
            .all()
        )
    ]
    db.session.commit()
    return [reconcile_finalization(task_id) for task_id in ids]


def reconcile_finalizations_for_visual_workflow(workflow_id):
    parent = (
        PublicationTask.query.options(load_only(
            PublicationTask.source_task_id,
            PublicationTask.source_feature,
        ))
        .filter(
            PublicationTask.id == str(workflow_id),
            PublicationTask.feature == VISUAL_WORKFLOW_FEATURE,
        )
        .one_or_none()
    )
    if not parent or str(parent.source_feature or "") != REPORT_AUDIO_FEATURE:
        return []
    audio_task_id = str(parent.source_task_id or "").strip()
    db.session.commit()
    return reconcile_finalizations_for_audio(audio_task_id) if audio_task_id else []


def reconcile_finalization_for_publisher(publisher_task_id):
    child = PublicationTask.query.filter(
        PublicationTask.id == str(publisher_task_id),
        PublicationTask.feature == PUBLISHER_FEATURE,
    ).one_or_none()
    if not child:
        return []
    params = child.params if isinstance(child.params, dict) else {}
    metadata = params.get("metadata") if isinstance(params.get("metadata"), dict) else {}
    finalization_id = str(metadata.get("finalization_task_id") or "").strip()
    if not finalization_id:
        return []
    db.session.commit()
    return [reconcile_finalization(finalization_id)]


def repair_pending_finalizations_once(limit=20):
    stale_before = datetime.utcnow() - timedelta(seconds=15)
    rows = (
        PublicationTask.query.options(load_only(PublicationTask.id))
        .filter(
            PublicationTask.feature == FEATURE_NAME,
            PublicationTask.status.in_(tuple(ACTIVE_STATUSES)),
            PublicationTask.updated_at <= stale_before,
        )
        .order_by(PublicationTask.updated_at.asc())
        .limit(max(1, min(int(limit or 20), 100)))
        .all()
    )
    ids = [str(row.id) for row in rows]
    db.session.commit()
    items = []
    for task_id in ids:
        try:
            items.append({"task_id": task_id, **reconcile_finalization(task_id)})
        except Exception as exc:
            db.session.rollback()
            logger.exception("Finalization watchdog reconcile failed task=%s", task_id)
            items.append({"task_id": task_id, "reconciled": False, "reason": "error", "error": str(exc)})
    return {
        "checked": len(ids),
        "reconciled": sum(bool(item.get("reconciled")) for item in items),
        "items": items,
    }
