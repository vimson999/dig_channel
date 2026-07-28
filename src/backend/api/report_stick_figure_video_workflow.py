import hashlib
import json
import os
import threading
import time
import wave
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from flask import Blueprint, jsonify, request, send_file
from sqlalchemy import or_
from sqlalchemy.orm import load_only

from api.report_script_material_workflow import (
    _bounded_int,
    _create_task_for_asset,
    _create_task_for_pdf,
    _escape_like,
    _normalize_search_query,
    _latest_material_task_for_asset,
    _parse_followups,
    _request_payload,
    _save_pdf_upload,
    _validate_pdf_upload,
    _visible_user_ids,
)
from models import (
    ReportStickFigureAudioTrack,
    ReportStickFigureSceneAsset,
    ReportVisualPlanOutput,
    ReportVisualPlanSegment,
    db,
)
from models.task import PublicationTask
from services import gemini_browser
from services.report_script_material_workflow import (
    DEFAULT_INITIAL_PROMPT,
    DEFAULT_REVIEW_PROMPT,
    FEATURE_NAME as MATERIAL_FEATURE,
    status_payload as material_status_payload,
)
from services.report_audio_workflow import FEATURE_NAME as REPORT_AUDIO_FEATURE
from services.report_visual_plan_service import (
    FEATURE_NAME as STORYBOARD_FEATURE,
    provider_status as storyboard_status_payload,
)
from services.doubao_browser_tts import FEATURE_NAME as DOUBAO_TTS_FEATURE, QUEUE_NAME as DOUBAO_TTS_QUEUE, status_payload as doubao_status_payload
from services.report_stick_figure_assets import (
    FEATURE_NAME as IMAGE_FEATURE,
    QUEUE_NAME as IMAGE_QUEUE,
    scene_image_prompt_hash,
)
from services.report_visual_scheme import (
    DEFAULT_VISUAL_SCHEME_KEY,
    visual_scheme,
    visual_scheme_payloads,
)
from services.report_stick_figure_audio_alignment import (
    FEATURE_NAME as AUDIO_ALIGNMENT_FEATURE,
    QUEUE_NAME as AUDIO_ALIGNMENT_QUEUE,
    find_reusable_audio_candidate,
)
from services.report_stick_figure_video import (
    FEATURE_NAME as VIDEO_FEATURE,
    QUEUE_NAME as VIDEO_QUEUE,
    normalize_render_options,
    subtitle_output_path,
    timeline_manifest_path,
    video_output_path,
    workflow_output_root,
)
from services.runtime_guard import maybe_guarded_apply_async
from tasks.doubao_browser_tts import process_doubao_browser_tts_job
from tasks.report_stick_figure_assets import generate_scene_image
from tasks.report_stick_figure_video import align_stick_figure_whole_audio, render_stick_figure_video
from tasks.report_visual_plan import (
    _dispatch_auto_scene_images,
    _sync_parent_image_dispatch_failure,
    enqueue_report_visual_plan_for_material,
)
from utils.auth_utils import require_login
from utils.date_filters import submission_date_utc_range


FEATURE_NAME = "report_stick_figure_video_workflow"
report_stick_figure_video_workflow_bp = Blueprint(
    "report_stick_figure_video_workflow",
    __name__,
    url_prefix="/api/report-stick-figure-video-workflows",
)

PARENT_COLUMNS = (
    PublicationTask.id,
    PublicationTask.status,
    PublicationTask.current_step,
    PublicationTask.progress,
    PublicationTask.error_message,
    PublicationTask.feature,
    PublicationTask.user_id,
    PublicationTask.source_task_id,
    PublicationTask.source_feature,
    PublicationTask.task_title,
    PublicationTask.report_audio_pdf_attachment_id,
    PublicationTask.report_audio_pdf_filename,
    PublicationTask.report_audio_pdf_bytes,
    PublicationTask.report_audio_pdf_topic_title,
    PublicationTask.final_script_chars,
    PublicationTask.task_video_path,
    PublicationTask.report_visual_scheme_key,
    PublicationTask.waiting_child_id,
    PublicationTask.waiting_child_label,
    PublicationTask.waiting_child_status,
    PublicationTask.waiting_child_step,
    PublicationTask.created_at,
    PublicationTask.updated_at,
    PublicationTask.completed_at,
)
CHILD_COLUMNS = (
    PublicationTask.id,
    PublicationTask.status,
    PublicationTask.current_step,
    PublicationTask.progress,
    PublicationTask.error_message,
    PublicationTask.feature,
    PublicationTask.user_id,
    PublicationTask.source_task_id,
    PublicationTask.task_title,
    PublicationTask.report_audio_pdf_attachment_id,
    PublicationTask.report_audio_pdf_filename,
    PublicationTask.report_audio_pdf_bytes,
    PublicationTask.report_audio_pdf_topic_title,
    PublicationTask.final_script_chars,
    PublicationTask.created_at,
    PublicationTask.updated_at,
    PublicationTask.completed_at,
)
VIDEO_TASK_COLUMNS = (
    PublicationTask.id,
    PublicationTask.status,
    PublicationTask.current_step,
    PublicationTask.progress,
    PublicationTask.error_message,
    PublicationTask.task_video_path,
    PublicationTask.parent_task_id,
    PublicationTask.created_at,
    PublicationTask.completed_at,
)
SCENE_PARENT_COLUMNS = (
    PublicationTask.id,
    PublicationTask.user_id,
    PublicationTask.source_task_id,
    PublicationTask.task_title,
    PublicationTask.task_video_path,
    PublicationTask.report_audio_pdf_attachment_id,
    PublicationTask.report_visual_scheme_key,
)
SCENE_ASSET_COLUMNS = (
    ReportStickFigureSceneAsset.id,
    ReportStickFigureSceneAsset.workflow_task_id,
    ReportStickFigureSceneAsset.storyboard_task_id,
    ReportStickFigureSceneAsset.segment_id,
    ReportStickFigureSceneAsset.sequence,
    ReportStickFigureSceneAsset.script_text_hash,
    ReportStickFigureSceneAsset.visual_prompt_hash,
    ReportStickFigureSceneAsset.image_task_id,
    ReportStickFigureSceneAsset.image_status,
    ReportStickFigureSceneAsset.image_progress,
    ReportStickFigureSceneAsset.image_attempt,
    ReportStickFigureSceneAsset.image_path,
    ReportStickFigureSceneAsset.image_bytes,
    ReportStickFigureSceneAsset.image_width,
    ReportStickFigureSceneAsset.image_height,
    ReportStickFigureSceneAsset.image_error,
    ReportStickFigureSceneAsset.tts_task_id,
    ReportStickFigureSceneAsset.tts_status,
    ReportStickFigureSceneAsset.tts_progress,
    ReportStickFigureSceneAsset.tts_attempt,
    ReportStickFigureSceneAsset.audio_path,
    ReportStickFigureSceneAsset.audio_bytes,
    ReportStickFigureSceneAsset.audio_seconds,
    ReportStickFigureSceneAsset.tts_error,
    ReportStickFigureSceneAsset.timeline_start_seconds,
    ReportStickFigureSceneAsset.timeline_end_seconds,
    ReportStickFigureSceneAsset.timeline_confidence,
    ReportStickFigureSceneAsset.timeline_source,
    ReportStickFigureSceneAsset.updated_at,
)
SCENE_ASSET_STATUS_COLUMNS = (
    ReportStickFigureSceneAsset.id,
    ReportStickFigureSceneAsset.workflow_task_id,
    ReportStickFigureSceneAsset.storyboard_task_id,
    ReportStickFigureSceneAsset.segment_id,
    ReportStickFigureSceneAsset.sequence,
    ReportStickFigureSceneAsset.image_status,
    ReportStickFigureSceneAsset.image_progress,
    ReportStickFigureSceneAsset.image_attempt,
    ReportStickFigureSceneAsset.image_bytes,
    ReportStickFigureSceneAsset.image_width,
    ReportStickFigureSceneAsset.image_height,
    ReportStickFigureSceneAsset.image_error,
    ReportStickFigureSceneAsset.tts_task_id,
    ReportStickFigureSceneAsset.tts_status,
    ReportStickFigureSceneAsset.tts_progress,
    ReportStickFigureSceneAsset.tts_attempt,
    ReportStickFigureSceneAsset.audio_path,
    ReportStickFigureSceneAsset.audio_bytes,
    ReportStickFigureSceneAsset.audio_seconds,
    ReportStickFigureSceneAsset.tts_error,
    ReportStickFigureSceneAsset.timeline_start_seconds,
    ReportStickFigureSceneAsset.timeline_end_seconds,
    ReportStickFigureSceneAsset.timeline_confidence,
    ReportStickFigureSceneAsset.timeline_source,
)
SCENE_MEDIA_ASSET_COLUMNS = (
    ReportStickFigureSceneAsset.id,
    ReportStickFigureSceneAsset.workflow_task_id,
    ReportStickFigureSceneAsset.storyboard_task_id,
    ReportStickFigureSceneAsset.segment_id,
    ReportStickFigureSceneAsset.sequence,
    ReportStickFigureSceneAsset.script_text_hash,
    ReportStickFigureSceneAsset.image_status,
    ReportStickFigureSceneAsset.image_attempt,
    ReportStickFigureSceneAsset.image_path,
    ReportStickFigureSceneAsset.tts_status,
    ReportStickFigureSceneAsset.tts_attempt,
    ReportStickFigureSceneAsset.audio_path,
    ReportStickFigureSceneAsset.timeline_start_seconds,
    ReportStickFigureSceneAsset.timeline_end_seconds,
    ReportStickFigureSceneAsset.timeline_confidence,
    ReportStickFigureSceneAsset.timeline_source,
)
SCENE_PARENT_ACCESS_CACHE_TTL_SECONDS = max(
    0.0,
    float(os.getenv("STICK_FIGURE_PARENT_ACCESS_CACHE_TTL", "30")),
)
SCENE_PARENT_ACCESS_CACHE_MAX_ENTRIES = 256
_scene_parent_access_cache = {}
_scene_parent_access_cache_lock = threading.RLock()
SCENE_STRUCTURE_CACHE_TTL_SECONDS = max(
    0.0,
    float(os.getenv("STICK_FIGURE_STRUCTURE_CACHE_TTL", "30")),
)
SCENE_STRUCTURE_CACHE_MAX_ENTRIES = 256
SCENE_STRUCTURE_REDIS_TTL_SECONDS = max(
    0,
    int(os.getenv("STICK_FIGURE_STRUCTURE_REDIS_TTL", "86400")),
)
_scene_structure_cache = {}
_scene_segment_cache = {}
_scene_structure_cache_lock = threading.RLock()
SCENE_MEDIA_CACHE_TTL_SECONDS = max(
    0.0,
    float(os.getenv("STICK_FIGURE_MEDIA_CACHE_TTL", "30")),
)
SCENE_MEDIA_CACHE_MAX_ENTRIES = 2048
_scene_media_asset_cache = {}
_scene_media_workflow_cache = {}
_scene_media_cache_lock = threading.RLock()
TERMINAL_WORKFLOW_CACHE_TTL_SECONDS = 300
TERMINAL_WORKFLOW_REDIS_TTL_SECONDS = 86400
TERMINAL_WORKFLOW_CACHE_MAX_ENTRIES = 512
_terminal_workflow_cache = {}
_terminal_workflow_cache_lock = threading.RLock()
ACTIVE_WORKFLOW_CACHE_TTL_SECONDS = max(
    0.0,
    float(os.getenv("STICK_FIGURE_ACTIVE_WORKFLOW_CACHE_TTL", "20")),
)
ACTIVE_WORKFLOW_CACHE_MAX_ENTRIES = 512
_active_workflow_cache = {}
_active_workflow_cache_lock = threading.RLock()
LEGACY_SCENE_BUNDLE_CACHE_TTL_SECONDS = 300
LEGACY_SCENE_BUNDLE_CACHE_MAX_ENTRIES = 128
_legacy_scene_bundle_cache = {}
_legacy_scene_bundle_cache_lock = threading.RLock()
SCENE_STATUS_ACTIVE_CACHE_TTL_SECONDS = max(
    0.0,
    float(os.getenv("STICK_FIGURE_STATUS_ACTIVE_CACHE_TTL", "20")),
)
SCENE_STATUS_IDLE_CACHE_TTL_SECONDS = max(
    SCENE_STATUS_ACTIVE_CACHE_TTL_SECONDS,
    float(os.getenv("STICK_FIGURE_STATUS_IDLE_CACHE_TTL", "300")),
)
SCENE_STATUS_CACHE_MAX_ENTRIES = 256
_scene_status_bundle_cache = {}
_scene_status_bundle_cache_lock = threading.RLock()
_scene_status_fill_locks = tuple(threading.Lock() for _ in range(32))


def _terminal_workflow_cache_key(current_user, job_id):
    return (str(getattr(current_user, "id", "") or ""), str(job_id))


def _terminal_workflow_redis_key(current_user, job_id):
    user_id, job_id = _terminal_workflow_cache_key(current_user, job_id)
    digest = hashlib.sha256(f"{user_id}:{job_id}".encode("utf-8")).hexdigest()
    return f"shioa:stick_figure:terminal:v3:{digest}"


def _persistent_terminal_workflow_get(current_user, job_id):
    client = _scene_structure_redis_client()
    if client is None:
        return None
    try:
        raw = client.get(_terminal_workflow_redis_key(current_user, job_id))
        payload = json.loads(raw) if raw else None
        return payload if isinstance(payload, dict) else None
    except Exception:
        return None


def _persistent_terminal_workflow_set(current_user, job_id, payload):
    client = _scene_structure_redis_client()
    if client is None:
        return
    try:
        client.setex(
            _terminal_workflow_redis_key(current_user, job_id),
            TERMINAL_WORKFLOW_REDIS_TTL_SECONDS,
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        )
    except Exception:
        return


def _cached_terminal_workflow(current_user, job_id):
    key = _terminal_workflow_cache_key(current_user, job_id)
    now = time.time()
    with _terminal_workflow_cache_lock:
        cached = _terminal_workflow_cache.get(key)
        if cached and float(cached.get("expires_at") or 0) > now:
            return deepcopy(cached["payload"])
        _terminal_workflow_cache.pop(key, None)
    payload = _persistent_terminal_workflow_get(current_user, job_id)
    if payload is not None:
        with _terminal_workflow_cache_lock:
            _terminal_workflow_cache[key] = {
                "expires_at": now + TERMINAL_WORKFLOW_CACHE_TTL_SECONDS,
                "payload": deepcopy(payload),
            }
        return deepcopy(payload)
    return None


def _cache_terminal_workflow(current_user, job_id, payload):
    key = _terminal_workflow_cache_key(current_user, job_id)
    with _terminal_workflow_cache_lock:
        if len(_terminal_workflow_cache) >= TERMINAL_WORKFLOW_CACHE_MAX_ENTRIES:
            _terminal_workflow_cache.clear()
        _terminal_workflow_cache[key] = {
            "expires_at": time.time() + TERMINAL_WORKFLOW_CACHE_TTL_SECONDS,
            "payload": deepcopy(payload),
        }
    _persistent_terminal_workflow_set(current_user, job_id, payload)


def _cached_active_workflow(current_user, job_id):
    key = _terminal_workflow_cache_key(current_user, job_id)
    now = time.monotonic()
    with _active_workflow_cache_lock:
        cached = _active_workflow_cache.get(key)
        if not cached or cached["expires_at"] <= now:
            _active_workflow_cache.pop(key, None)
            return None
        return deepcopy(cached["payload"])


def _cache_active_workflow(current_user, job_id, payload):
    if ACTIVE_WORKFLOW_CACHE_TTL_SECONDS <= 0:
        return
    key = _terminal_workflow_cache_key(current_user, job_id)
    with _active_workflow_cache_lock:
        if len(_active_workflow_cache) >= ACTIVE_WORKFLOW_CACHE_MAX_ENTRIES:
            _active_workflow_cache.clear()
        _active_workflow_cache[key] = {
            "expires_at": time.monotonic() + ACTIVE_WORKFLOW_CACHE_TTL_SECONDS,
            "payload": deepcopy(payload),
        }


def _clear_active_workflow_cache(current_user=None, job_id=None):
    key = _terminal_workflow_cache_key(current_user, job_id) if current_user is not None and job_id else None
    with _active_workflow_cache_lock:
        if key is None:
            _active_workflow_cache.clear()
        else:
            _active_workflow_cache.pop(key, None)


def _clear_terminal_workflow_cache(current_user=None, job_id=None):
    key = _terminal_workflow_cache_key(current_user, job_id) if current_user is not None and job_id else None
    with _terminal_workflow_cache_lock:
        if key is None:
            _terminal_workflow_cache.clear()
        else:
            _terminal_workflow_cache.pop(key, None)
    if key is not None:
        client = _scene_structure_redis_client()
        if client is not None:
            try:
                client.delete(_terminal_workflow_redis_key(current_user, job_id))
            except Exception:
                pass
    _clear_legacy_scene_bundle_cache(job_id)
    _clear_scene_status_bundle_cache(job_id)
    _clear_active_workflow_cache(current_user, job_id)


@report_stick_figure_video_workflow_bp.route("/status", methods=["GET"])
@require_login
def service_status(_current_user):
    return jsonify({
        "success": True,
        "data": {
            "feature": FEATURE_NAME,
            "material": material_status_payload(),
            "storyboard": storyboard_status_payload(),
            "visual_schemes": visual_scheme_payloads(),
            "gemini": gemini_browser.status_payload(),
            "doubao_tts": doubao_status_payload(),
        },
    })


@report_stick_figure_video_workflow_bp.route("/jobs", methods=["POST"])
@require_login
def create_job(current_user):
    data = _request_payload()
    try:
        scheme = visual_scheme(data.get("visual_scheme_key"))
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    pdf_file = request.files.get("pdf") or request.files.get("file")
    asset_id = str(data.get("pdf_asset_id") or data.get("asset_id") or "").strip()
    raw_asset_ids = data.get("pdf_asset_ids") or []
    if not asset_id and isinstance(raw_asset_ids, list) and raw_asset_ids:
        asset_id = str(raw_asset_ids[0] or "").strip()
    if not pdf_file and not asset_id:
        return jsonify({"success": False, "message": "请上传 PDF 或选择一份已同步 PDF"}), 400
    if pdf_file:
        error = _validate_pdf_upload(pdf_file)
        if error:
            return jsonify({"success": False, "message": error}), 400

    user_id = str(getattr(current_user, "id", "") or "")
    parent = PublicationTask(
        # 父任务只记录编排关系，不占用 worker；真实执行状态由子任务合成。
        status="pending",
        current_step="material_queued",
        progress=1,
        params={
            "feature": FEATURE_NAME,
            "user_id": user_id,
            "visual_scheme_key": scheme.key,
            FEATURE_NAME: {
                "schema_version": "20260722_visual_scheme_v1",
                "visual_scheme_key": scheme.key,
            },
        },
        result={"schema_version": "20260722_visual_scheme_v1"},
    )
    parent.task_title = f"研报 PDF 转{scheme.name}视频"
    parent.report_visual_scheme_key = scheme.key
    db.session.add(parent)
    db.session.commit()

    prompt = str(data.get("prompt") or DEFAULT_INITIAL_PROMPT).strip()
    review_prompt = str(data.get("review_prompt") or DEFAULT_REVIEW_PROMPT).strip()
    followups = _parse_followups(data)
    gemini_url = str(data.get("gemini_url") or data.get("url") or "").strip()
    metadata = {
        "source": "report-stick-figure-video-workflow",
        "workflow_job_id": parent.id,
        "visual_scheme_key": scheme.key,
    }
    reused_material = False
    reusable_material = None
    if asset_id and not pdf_file:
        reusable_material = _latest_material_task_for_asset(
            asset_id,
            current_user=current_user,
            include_result=False,
        )
        if not reusable_material or reusable_material.status != "completed" or int(reusable_material.final_script_chars or 0) <= 0:
            reusable_material = None
    try:
        if reusable_material:
            child = {"job_id": reusable_material.id, "status": "completed", "reused": True}
            reused_material = True
        elif pdf_file:
            child = _create_task_for_pdf(
                _save_pdf_upload(pdf_file),
                user_id=user_id,
                prompt=prompt,
                review_prompt=review_prompt,
                followups=followups,
                gemini_url=gemini_url,
                metadata=metadata,
            )
        else:
            child = _create_task_for_asset(
                asset_id,
                user_id=user_id,
                prompt=prompt,
                review_prompt=review_prompt,
                followups=followups,
                gemini_url=gemini_url,
                metadata=metadata,
            )
    except Exception as exc:
        db.session.rollback()
        parent = db.session.get(PublicationTask, parent.id)
        if parent:
            parent.status = "failed"
            parent.current_step = "material_submit_failed"
            parent.error_message = str(exc)[:4000]
            parent.completed_at = datetime.utcnow()
            db.session.commit()
        return jsonify({"success": False, "message": str(exc)}), 400

    material = db.session.get(PublicationTask, child["job_id"])
    parent_title = material.task_title or material.report_audio_pdf_topic_title or material.report_audio_pdf_filename or f"研报 PDF 转{scheme.name}视频"
    PublicationTask.query.filter(PublicationTask.id == parent.id).update({
        PublicationTask.source_task_id: material.id,
        PublicationTask.source_feature: MATERIAL_FEATURE,
        PublicationTask.waiting_child_id: material.id,
        PublicationTask.waiting_child_label: "基础文案",
        PublicationTask.waiting_child_status: material.status,
        PublicationTask.task_title: parent_title,
        PublicationTask.report_audio_pdf_attachment_id: material.report_audio_pdf_attachment_id,
        PublicationTask.report_audio_pdf_filename: material.report_audio_pdf_filename,
        PublicationTask.report_audio_pdf_bytes: material.report_audio_pdf_bytes,
        PublicationTask.report_audio_pdf_topic_title: material.report_audio_pdf_topic_title,
    }, synchronize_session=False)
    db.session.commit()
    parent = db.session.get(PublicationTask, parent.id)
    storyboard_dispatch = None
    if material.status == "completed" and int(material.final_script_chars or 0) > 0:
        try:
            storyboard_dispatch = enqueue_report_visual_plan_for_material(
                material.id,
                user_id=user_id,
                parent_feature=FEATURE_NAME,
                parent_task_id=parent.id,
                visual_scheme_key=scheme.key,
            )
            storyboard_status = str(storyboard_dispatch.get("status") or "pending")
            storyboard_done = storyboard_status == "completed"
            now = datetime.utcnow()
            PublicationTask.query.filter(PublicationTask.id == parent.id).update({
                PublicationTask.status: "processing",
                PublicationTask.current_step: "storyboard_done" if storyboard_done else "storyboard_queued",
                PublicationTask.progress: 60 if storyboard_done else 35,
                PublicationTask.waiting_child_id: storyboard_dispatch.get("job_id"),
                PublicationTask.waiting_child_label: "标题与正文分镜",
                PublicationTask.waiting_child_status: "completed" if storyboard_done else "pending",
                PublicationTask.waiting_child_step: "done" if storyboard_done else "queued",
                PublicationTask.waiting_child_updated_at: now.isoformat() + "Z",
                PublicationTask.error_message: None,
                PublicationTask.updated_at: now,
            }, synchronize_session=False)
            db.session.commit()
            parent = db.session.get(PublicationTask, parent.id)
            if storyboard_done:
                _auto_dispatch_completed_storyboard_images(
                    storyboard_dispatch.get("job_id"),
                    parent.id,
                )
        except Exception as exc:
            PublicationTask.query.filter(PublicationTask.id == parent.id).update({
                PublicationTask.status: "failed",
                PublicationTask.current_step: "storyboard_submit_failed",
                PublicationTask.error_message: str(exc)[:4000],
                PublicationTask.completed_at: datetime.utcnow(),
            }, synchronize_session=False)
            db.session.commit()
            return jsonify({"success": False, "message": f"基础文案已复用，但自动投递分镜失败：{exc}"}), 502
    return jsonify({
        "success": True,
        "job_id": parent.id,
        "workflow_job_id": parent.id,
        "material_job_id": material.id,
        "status": "storyboard_queued" if storyboard_dispatch else "material_queued",
        "reused_material": reused_material,
        "storyboard_job_id": storyboard_dispatch.get("job_id") if storyboard_dispatch else None,
        "data": _workflow_payload(parent, material, _latest_storyboard(material.id, scheme.key)),
    })


@report_stick_figure_video_workflow_bp.route(
    "/jobs/from-report-audio/<audio_job_id>",
    methods=["POST"],
)
@require_login
def create_job_from_report_audio(current_user, audio_job_id):
    """Start the visual workflow while reusing a report-audio script and WAV."""

    audio_task = (
        PublicationTask.query.options(load_only(
            PublicationTask.id,
            PublicationTask.feature,
            PublicationTask.status,
            PublicationTask.user_id,
            PublicationTask.task_title,
            PublicationTask.final_script_chars,
            PublicationTask.report_audio_wav_path,
            PublicationTask.report_audio_pdf_attachment_id,
            PublicationTask.report_audio_pdf_filename,
            PublicationTask.report_audio_pdf_bytes,
            PublicationTask.report_audio_pdf_topic_title,
            PublicationTask.created_at,
            PublicationTask.updated_at,
            PublicationTask.completed_at,
            PublicationTask.current_step,
            PublicationTask.progress,
            PublicationTask.error_message,
        ))
        .filter(
            PublicationTask.id == str(audio_job_id),
            PublicationTask.feature == REPORT_AUDIO_FEATURE,
            PublicationTask.user_id.in_(_visible_user_ids(current_user)),
        )
        .one_or_none()
    )
    if not audio_task:
        return jsonify({"success": False, "message": "研报转语音任务不存在"}), 404
    if audio_task.status != "completed" or int(audio_task.final_script_chars or 0) <= 0:
        return jsonify({"success": False, "message": "研报转语音任务尚未生成完整文案"}), 409
    audio_path = Path(str(audio_task.report_audio_wav_path or ""))
    if not audio_path.is_file() or audio_path.stat().st_size <= 0:
        return jsonify({"success": False, "message": "研报转语音任务的完整 WAV 不存在"}), 409
    # This endpoint commits the new orchestration row before dispatching the
    # storyboard.  Snapshot the narrow source projection first so SQLAlchemy's
    # expire-on-commit cannot trigger a post-commit full-row JSON reload.
    audio_task = SimpleNamespace(
        id=str(audio_task.id),
        feature=str(audio_task.feature or REPORT_AUDIO_FEATURE),
        status=str(audio_task.status),
        user_id=str(audio_task.user_id or ""),
        task_title=audio_task.task_title,
        final_script_chars=int(audio_task.final_script_chars or 0),
        report_audio_wav_path=str(audio_path.resolve()),
        report_audio_pdf_attachment_id=audio_task.report_audio_pdf_attachment_id,
        report_audio_pdf_filename=audio_task.report_audio_pdf_filename,
        report_audio_pdf_bytes=audio_task.report_audio_pdf_bytes,
        report_audio_pdf_topic_title=audio_task.report_audio_pdf_topic_title,
        current_step=audio_task.current_step,
        progress=int(audio_task.progress or 0),
        error_message=audio_task.error_message,
    )

    data = request.get_json(silent=True) or {}
    try:
        scheme = visual_scheme(data.get("visual_scheme_key"))
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    force_new = _truthy(data.get("force_new"), False)
    existing = None
    if not force_new:
        existing = (
            PublicationTask.query.options(load_only(*PARENT_COLUMNS))
            .filter(
                PublicationTask.feature == FEATURE_NAME,
                PublicationTask.source_feature == REPORT_AUDIO_FEATURE,
                PublicationTask.source_task_id == str(audio_task.id),
                PublicationTask.report_visual_scheme_key == scheme.key,
                PublicationTask.user_id.in_(_visible_user_ids(current_user)),
            )
            .order_by(PublicationTask.created_at.desc())
            .first()
        )

    parent = existing
    if not parent:
        user_id = str(getattr(current_user, "id", "") or "")
        parent = PublicationTask(
            status="processing",
            current_step="storyboard_queued",
            progress=35,
            params={
                "feature": FEATURE_NAME,
                "user_id": user_id,
                "source_task_id": str(audio_task.id),
                "source_feature": REPORT_AUDIO_FEATURE,
                "visual_scheme_key": scheme.key,
                FEATURE_NAME: {
                    "schema_version": "20260722_audio_reuse_v1",
                    "input_mode": "report_audio_reuse",
                    "visual_scheme_key": scheme.key,
                    "pdf": {
                        "attachment_id": audio_task.report_audio_pdf_attachment_id,
                        "filename": audio_task.report_audio_pdf_filename,
                        "bytes": audio_task.report_audio_pdf_bytes,
                        "topic_title": audio_task.report_audio_pdf_topic_title,
                    },
                },
            },
            result={"schema_version": "20260722_audio_reuse_v1"},
        )
        parent.task_title = audio_task.task_title or audio_task.report_audio_pdf_topic_title or audio_task.report_audio_pdf_filename or f"研报 PDF 转{scheme.name}视频"
        parent.report_visual_scheme_key = scheme.key
        parent.source_task_id = str(audio_task.id)
        parent.source_feature = REPORT_AUDIO_FEATURE
        parent.report_audio_pdf_attachment_id = audio_task.report_audio_pdf_attachment_id
        parent.report_audio_pdf_filename = audio_task.report_audio_pdf_filename
        parent.report_audio_pdf_bytes = audio_task.report_audio_pdf_bytes
        parent.report_audio_pdf_topic_title = audio_task.report_audio_pdf_topic_title
        db.session.add(parent)
        db.session.commit()

    existing_storyboard = _latest_storyboard(audio_task.id, scheme.key) if existing else None
    if existing_storyboard and existing_storyboard.status in {"pending", "processing", "completed"}:
        if existing_storyboard.status == "completed":
            _auto_dispatch_completed_storyboard_images(existing_storyboard.id, parent.id)
        return jsonify({
            "success": True,
            "message": f"已打开现有{scheme.name}方案",
            "job_id": parent.id,
            "workflow_job_id": parent.id,
            "report_audio_job_id": audio_task.id,
            "reused_audio": True,
            "reused_workflow": True,
            "storyboard_job_id": existing_storyboard.id,
            "data": _workflow_payload(parent, audio_task, existing_storyboard),
        })

    try:
        storyboard_dispatch = enqueue_report_visual_plan_for_material(
            audio_task.id,
            user_id=str(getattr(current_user, "id", "") or ""),
            parent_feature=FEATURE_NAME,
            parent_task_id=parent.id,
            visual_scheme_key=scheme.key,
        )
        storyboard_status = str(storyboard_dispatch.get("status") or "pending")
        storyboard_done = storyboard_status == "completed"
        now = datetime.utcnow()
        PublicationTask.query.filter(PublicationTask.id == parent.id).update({
            PublicationTask.status: "processing",
            PublicationTask.current_step: "storyboard_done" if storyboard_done else "storyboard_queued",
            PublicationTask.progress: 60 if storyboard_done else 35,
            PublicationTask.waiting_child_id: storyboard_dispatch.get("job_id"),
            PublicationTask.waiting_child_label: "标题与正文分镜",
            PublicationTask.waiting_child_status: "completed" if storyboard_done else "pending",
            PublicationTask.waiting_child_step: "done" if storyboard_done else "queued",
            PublicationTask.waiting_child_updated_at: now.isoformat() + "Z",
            PublicationTask.error_message: None,
            PublicationTask.completed_at: None,
            PublicationTask.updated_at: now,
        }, synchronize_session=False)
        db.session.commit()
        if storyboard_done:
            _auto_dispatch_completed_storyboard_images(
                storyboard_dispatch.get("job_id"),
                parent.id,
            )
    except Exception as exc:
        PublicationTask.query.filter(PublicationTask.id == parent.id).update({
            PublicationTask.status: "failed",
            PublicationTask.current_step: "storyboard_submit_failed",
            PublicationTask.error_message: str(exc)[:4000],
            PublicationTask.completed_at: datetime.utcnow(),
        }, synchronize_session=False)
        db.session.commit()
        return jsonify({"success": False, "message": f"{scheme.name}分镜投递失败：{exc}"}), 502

    parent = (
        PublicationTask.query.options(load_only(*PARENT_COLUMNS))
        .filter(PublicationTask.id == parent.id)
        .one()
    )
    storyboard = _latest_storyboard(audio_task.id, scheme.key)
    return jsonify({
        "success": True,
        "message": f"已复用完整文案和 WAV，{scheme.name}分镜已就绪" if storyboard_status == "completed" else f"已复用完整文案和 WAV，{scheme.name}分镜已投递",
        "job_id": parent.id,
        "workflow_job_id": parent.id,
        "report_audio_job_id": audio_task.id,
        "reused_audio": True,
        "reused_workflow": existing is not None,
        "storyboard_job_id": storyboard_dispatch.get("job_id"),
        "data": _workflow_payload(parent, audio_task, storyboard),
    }), 200 if existing else 201


def _auto_dispatch_completed_storyboard_images(storyboard_job_id, workflow_job_id=None):
    storyboard_job_id = str(storyboard_job_id or "").strip()
    if not storyboard_job_id:
        raise ValueError("自动投递图片缺少分镜任务 ID")
    try:
        return _dispatch_auto_scene_images(storyboard_job_id, workflow_job_id)
    except Exception as exc:
        _sync_parent_image_dispatch_failure(storyboard_job_id, str(exc), workflow_job_id)
        raise


@report_stick_figure_video_workflow_bp.route("/jobs", methods=["GET"])
@require_login
def list_jobs(current_user):
    page = _bounded_int(request.args.get("page"), 1, 1, 100000)
    per_page = _bounded_int(request.args.get("per_page"), 10, 1, 100)
    search = _normalize_search_query(request.args.get("q") or request.args.get("search"))
    status = str(request.args.get("status") or "").strip()
    submitted_on = str(request.args.get("submitted_on") or "").strip()
    submitted_range = submission_date_utc_range(submitted_on)

    query = PublicationTask.query.options(load_only(*PARENT_COLUMNS)).filter(
        PublicationTask.feature == FEATURE_NAME,
        PublicationTask.user_id.in_(_visible_user_ids(current_user)),
    )
    if submitted_range:
        start, end = submitted_range
        query = query.filter(PublicationTask.created_at >= start, PublicationTask.created_at < end)
    if search:
        pattern = f"%{_escape_like(search)}%"
        query = query.filter(or_(
            PublicationTask.id.ilike(pattern, escape="\\"),
            PublicationTask.task_title.ilike(pattern, escape="\\"),
            PublicationTask.report_audio_pdf_filename.ilike(pattern, escape="\\"),
            PublicationTask.report_audio_pdf_topic_title.ilike(pattern, escape="\\"),
        ))
    parents = query.order_by(PublicationTask.created_at.desc()).all()
    materials = _materials_by_id([item.source_task_id for item in parents if item.source_task_id])
    storyboards = _storyboards_by_workflow(parents)
    legacy_storyboards = _storyboards_by_source(list(materials))
    board_for = lambda parent: storyboards.get(parent.id) or legacy_storyboards.get((
        str(parent.source_task_id or ""),
        str(parent.report_visual_scheme_key or DEFAULT_VISUAL_SCHEME_KEY),
    ))
    selected_storyboards = [board_for(parent) for parent in parents if board_for(parent)]
    segment_counts = _segment_counts_by_task([item.id for item in selected_storyboards])
    board_by_workflow = {
        parent.id: getattr(board_for(parent), "id", None)
        for parent in parents
    }
    asset_summaries = _asset_summaries(board_by_workflow)
    payloads = [
        _workflow_payload(
            parent,
            materials.get(parent.source_task_id),
            board_for(parent),
            segment_count=segment_counts.get(getattr(board_for(parent), "id", None), 0),
            asset_summary=asset_summaries.get(parent.id),
        )
        for parent in parents
    ]
    if status:
        payloads = [item for item in payloads if item["status"] == status]
    total = len(payloads)
    start_index = (page - 1) * per_page
    items = payloads[start_index:start_index + per_page]
    pages = max(1, (total + per_page - 1) // per_page)
    return jsonify({
        "success": True,
        "data": {
            "items": items,
            "pagination": {
                "page": page,
                "per_page": per_page,
                "total": total,
                "pages": pages,
                "q": search,
                "submitted_on": submitted_on if submitted_range else "",
            },
        },
        "items": items,
    })


@report_stick_figure_video_workflow_bp.route("/jobs/<job_id>", methods=["GET"])
@require_login
def get_job(current_user, job_id):
    cached = _cached_active_workflow(current_user, job_id)
    if cached is not None:
        return jsonify(cached)
    cached = _cached_terminal_workflow(current_user, job_id)
    if cached is not None:
        return jsonify(cached)
    parent = (
        PublicationTask.query.options(load_only(*PARENT_COLUMNS))
        .filter(PublicationTask.id == str(job_id), PublicationTask.feature == FEATURE_NAME)
        .filter(PublicationTask.user_id.in_(_visible_user_ids(current_user)))
        .first()
    )
    if not parent:
        return jsonify({"success": False, "message": "视觉视频编排任务不存在"}), 404
    material = _materials_by_id([parent.source_task_id]).get(parent.source_task_id)
    storyboard = _storyboard_for_workflow(parent)
    summaries = _asset_summaries({parent.id: getattr(storyboard, "id", None)})
    payload = _workflow_payload(parent, material, storyboard, asset_summary=summaries.get(parent.id))
    response_payload = {"success": True, "job_id": parent.id, "status": payload["status"], "data": payload}
    if payload["status"] == "succeeded":
        _cache_terminal_workflow(current_user, parent.id, response_payload)
    else:
        _cache_active_workflow(current_user, parent.id, response_payload)
    return jsonify(response_payload)


@report_stick_figure_video_workflow_bp.route("/jobs/<job_id>/scene-assets", methods=["GET"])
@require_login
def get_scene_assets(current_user, job_id):
    parent = _owned_parent(current_user, job_id)
    if not parent:
        return jsonify({"success": False, "message": "视觉视频编排任务不存在"}), 404
    parent_snapshot = _scene_parent_snapshot(parent)
    if request.args.get("view") == "status":
        storyboard_id = str(request.args.get("storyboard_job_id") or "").strip()
        if not storyboard_id:
            return jsonify({"success": False, "message": "状态轮询缺少 storyboard_job_id"}), 400
        payload = _scene_asset_status_payload(parent_snapshot, storyboard_id)
        if payload is None:
            return jsonify({"success": False, "message": "分镜素材尚未初始化", "code": "scene_assets_not_ready"}), 409
        # Scene status bundles are intentionally cached, but video rendering is
        # completed by another worker process and cannot invalidate this web
        # process's in-memory cache. Always overlay the narrow durable render
        # status so a completed video never remains displayed as queued.
        payload["video"] = _video_payload(parent_snapshot)
        return jsonify({"success": True, "data": payload})
    cached_payload = _cached_legacy_scene_bundle(parent_snapshot)
    if cached_payload is not None:
        cached_payload["video"] = _video_payload(parent_snapshot)
        return jsonify({"success": True, "data": cached_payload})
    try:
        storyboard, assets = _ensure_scene_assets(parent)
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc), "code": "storyboard_not_ready"}), 409
    storyboard_id = storyboard.id
    assets = _sync_tts_assets(assets)
    payload = _scene_asset_bundle(parent_snapshot, storyboard, assets)
    _cache_legacy_scene_bundle(payload)
    return jsonify({"success": True, "data": payload})


@report_stick_figure_video_workflow_bp.route("/jobs/<job_id>/scene-assets/images", methods=["POST"])
@require_login
def submit_scene_images(current_user, job_id):
    parent = _owned_parent(current_user, job_id)
    if not parent:
        return jsonify({"success": False, "message": "视觉视频编排任务不存在"}), 404
    _clear_terminal_workflow_cache(current_user, job_id)
    parent_snapshot = _scene_parent_snapshot(parent)
    try:
        storyboard, assets = _ensure_scene_assets(parent, force_refresh=True)
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc), "code": "storyboard_not_ready"}), 409
    data = request.get_json(silent=True) or {}
    selected = _selected_sequences(data)
    force = _truthy(data.get("force"), False)
    retry_failed = _truthy(data.get("retry_failed"), False)
    storyboard_id = storyboard.id
    assets = [_scene_asset_enqueue_snapshot(asset) for asset in assets]
    jobs, skipped, errors = [], [], []
    for asset in assets:
        if selected is not None and asset.sequence not in selected:
            continue
        if retry_failed and asset.image_status not in {"failed", "blocked"}:
            skipped.append(asset.sequence)
            continue
        if not force and asset.image_status in {"queued", "processing", "completed"}:
            skipped.append(asset.sequence)
            continue
        try:
            jobs.append(_enqueue_image_asset(parent_snapshot, asset))
        except Exception as exc:
            db.session.rollback()
            errors.append({"sequence": asset.sequence, "message": str(exc)})
    return jsonify({
        "success": bool(jobs) or not errors,
        "message": f"已投递 {len(jobs)} 个分镜图片任务",
        "data": {"jobs": jobs, "skipped_sequences": skipped, "errors": errors, "storyboard_job_id": storyboard_id},
    }), (200 if jobs or not errors else 502)


@report_stick_figure_video_workflow_bp.route("/jobs/<job_id>/scene-assets/tts", methods=["POST"])
@require_login
def submit_scene_tts(current_user, job_id):
    parent = _owned_parent(current_user, job_id)
    if not parent:
        return jsonify({"success": False, "message": "视觉视频编排任务不存在"}), 404
    _clear_terminal_workflow_cache(current_user, job_id)
    parent_snapshot = _scene_parent_snapshot(parent)
    try:
        storyboard, assets = _ensure_scene_assets(parent, force_refresh=True)
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc), "code": "storyboard_not_ready"}), 409
    data = request.get_json(silent=True) or {}
    requested_mode = str(data.get("audio_mode") or "auto").strip().lower()
    if requested_mode not in {"auto", "whole_audio", "scene_tts"}:
        return jsonify({"success": False, "message": "audio_mode 只能是 auto、whole_audio 或 scene_tts"}), 400
    if requested_mode != "scene_tts":
        try:
            track = _ensure_whole_audio_track(parent_snapshot, storyboard)
        except ValueError as exc:
            if requested_mode == "whole_audio":
                return jsonify({"success": False, "message": str(exc), "code": "whole_audio_unavailable"}), 409
            track = None
        if track:
            try:
                alignment_task = _enqueue_whole_audio_alignment(
                    parent_snapshot,
                    storyboard,
                    track,
                    force=_truthy(data.get("force"), False),
                )
            except Exception as exc:
                db.session.rollback()
                return jsonify({"success": False, "message": str(exc)}), 502
            return jsonify({
                "success": True,
                "message": "已复用研报转语音的整篇 WAV，并投递分镜时间轴对齐",
                "data": {
                    "audio_mode": "whole_audio",
                    "audio_track_id": track.id,
                    "job_id": alignment_task.id if alignment_task else track.alignment_task_id,
                    "status": track.status,
                    "storyboard_job_id": storyboard.id,
                },
            })
    storyboard_id = storyboard.id
    assets = [
        asset
        for asset in _sync_tts_assets(assets)
        if int(asset.sequence) >= 1
    ]
    segments = _scene_segments_by_id(
        storyboard_id,
        [asset.segment_id for asset in assets],
    )
    scene_count = len(assets)
    assets = [
        _scene_asset_enqueue_snapshot(
            asset,
            segment=segments.get(str(asset.segment_id)),
            scene_count=scene_count,
        )
        for asset in assets
    ]
    selected = _selected_sequences(data)
    force = _truthy(data.get("force"), False)
    retry_failed = _truthy(data.get("retry_failed"), False)
    eligible, skipped, errors = [], [], []
    for asset in assets:
        if selected is not None and asset.sequence not in selected:
            continue
        if retry_failed and asset.tts_status not in {"failed", "blocked"}:
            skipped.append(asset.sequence)
            continue
        if not force and asset.tts_status in {"queued", "processing", "completed"}:
            skipped.append(asset.sequence)
            continue
        eligible.append(asset)
    jobs = []
    if eligible:
        try:
            jobs, enqueue_errors = _enqueue_tts_assets(parent_snapshot, eligible)
            errors.extend(enqueue_errors)
        except Exception as exc:
            db.session.rollback()
            errors.extend({"sequence": asset.sequence, "message": str(exc)} for asset in eligible)
    return jsonify({
        "success": bool(jobs) or not errors,
        "message": f"已投递 {len(jobs)} 个分镜语音任务",
        "data": {"jobs": jobs, "skipped_sequences": skipped, "errors": errors, "storyboard_job_id": storyboard_id},
    }), (200 if jobs or not errors else 502)


@report_stick_figure_video_workflow_bp.route("/jobs/<job_id>/scene-assets/<asset_id>/image/retry", methods=["POST"])
@require_login
def retry_scene_image(current_user, job_id, asset_id):
    parent = _owned_parent(current_user, job_id)
    asset = _owned_scene_asset(parent, asset_id, fresh=True) if parent else None
    if not asset:
        return jsonify({"success": False, "message": "分镜资产不存在"}), 404
    _clear_terminal_workflow_cache(current_user, job_id)
    job = _enqueue_image_asset(
        _scene_parent_snapshot(parent),
        _scene_asset_enqueue_snapshot(asset),
    )
    return jsonify({"success": True, "message": "图片任务已重新投递", "data": job})


@report_stick_figure_video_workflow_bp.route("/jobs/<job_id>/scene-assets/<asset_id>/tts/retry", methods=["POST"])
@require_login
def retry_scene_tts(current_user, job_id, asset_id):
    parent = _owned_parent(current_user, job_id)
    asset = _owned_scene_asset(parent, asset_id, fresh=True) if parent else None
    if not asset:
        return jsonify({"success": False, "message": "分镜资产不存在"}), 404
    _clear_terminal_workflow_cache(current_user, job_id)
    job = _enqueue_tts_asset(
        _scene_parent_snapshot(parent),
        _scene_asset_tts_snapshot(asset),
    )
    return jsonify({"success": True, "message": "语音任务已重新投递", "data": job})


@report_stick_figure_video_workflow_bp.route("/jobs/<job_id>/scene-assets/<asset_id>/image", methods=["GET"])
@require_login
def scene_image(current_user, job_id, asset_id):
    parent = _owned_parent(current_user, job_id)
    asset = _owned_scene_asset(parent, asset_id) if parent else None
    path = Path(str(asset.image_path or "")).resolve() if asset and asset.image_path else None
    root = (Path(__file__).resolve().parents[3] / "data" / "processed" / FEATURE_NAME / str(job_id)).resolve()
    if not path or root not in path.parents or not path.is_file():
        return jsonify({"success": False, "message": "分镜图片不存在"}), 404
    return send_file(path, mimetype="image/png", as_attachment=False, download_name=path.name)


@report_stick_figure_video_workflow_bp.route("/jobs/<job_id>/scene-assets/<asset_id>/audio", methods=["GET"])
@require_login
def scene_audio(current_user, job_id, asset_id):
    parent = _owned_parent(current_user, job_id)
    asset = _owned_scene_asset(parent, asset_id) if parent else None
    path = Path(str(asset.audio_path or "")).resolve() if asset and asset.audio_path else None
    if not path or not path.is_file():
        return jsonify({"success": False, "message": "分镜语音不存在"}), 404
    return send_file(path, mimetype="audio/wav", as_attachment=False, download_name=_scene_audio_filename(asset.sequence))


@report_stick_figure_video_workflow_bp.route("/jobs/<job_id>/audio-preview", methods=["GET"])
@require_login
def whole_audio_preview(current_user, job_id):
    parent = _owned_parent(current_user, job_id)
    if not parent:
        return jsonify({"success": False, "message": "视觉视频编排任务不存在"}), 404
    storyboard = _storyboard_for_workflow(parent)
    track = _whole_audio_track(parent.id, getattr(storyboard, "id", None))
    audio_path = str(getattr(track, "audio_path", "") or "").strip()
    if not audio_path:
        candidate = find_reusable_audio_candidate(
            attachment_id=str(parent.report_audio_pdf_attachment_id or ""),
            material_task_id=str(parent.source_task_id or ""),
            visible_user_ids=_visible_user_ids(current_user),
        )
        audio_path = str(getattr(candidate, "audio_path", "") or "").strip()
    path = Path(audio_path).resolve() if audio_path else None
    if not path or not path.is_file():
        return jsonify({"success": False, "message": "整篇试听音频不存在"}), 404
    return send_file(
        path,
        mimetype="audio/wav",
        as_attachment=False,
        conditional=True,
        download_name=f"{job_id}-preview.wav",
    )


@report_stick_figure_video_workflow_bp.route("/jobs/<job_id>/video", methods=["POST"])
@require_login
def submit_video(current_user, job_id):
    parent = _owned_parent(current_user, job_id)
    if not parent:
        return jsonify({"success": False, "message": "视觉视频编排任务不存在"}), 404
    _clear_terminal_workflow_cache(current_user, job_id)
    parent_snapshot = _scene_parent_snapshot(parent)
    try:
        storyboard, assets = _ensure_scene_assets(parent, force_refresh=True)
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc), "code": "storyboard_not_ready"}), 409
    storyboard_id = storyboard.id
    assets = _sync_tts_assets(assets)
    render_assets = [asset for asset in assets if int(asset.sequence) >= 1]
    if not render_assets:
        return jsonify({"success": False, "message": "没有可合成的正文分镜资产（正文必须从 001 开始）"}), 409
    data = request.get_json(silent=True) or {}
    requested_audio_mode = str(data.get("audio_mode") or "auto").strip().lower()
    if requested_audio_mode not in {"auto", "whole_audio", "scene_tts"}:
        return jsonify({"success": False, "message": "audio_mode 只能是 auto、whole_audio 或 scene_tts"}), 400
    audio_track = _whole_audio_track(parent_snapshot.id, storyboard_id)
    whole_audio_ready = _whole_audio_track_ready(audio_track, len(render_assets))
    audio_mode = (
        "whole_audio"
        if requested_audio_mode == "whole_audio" or (requested_audio_mode == "auto" and whole_audio_ready)
        else "scene_tts"
    )
    if audio_mode == "whole_audio" and not whole_audio_ready:
        return jsonify({
            "success": False,
            "message": "整篇音频时间轴尚未完成，请先点击“复用整篇语音并对齐”",
            "code": "whole_audio_not_aligned",
        }), 409
    for asset in render_assets:
        if asset.image_status != "completed" or not asset.image_path or not Path(asset.image_path).is_file():
            return jsonify({"success": False, "message": f"第 {asset.sequence} 段图片尚未完成"}), 409
        if (
            audio_mode == "scene_tts"
            and (asset.tts_status != "completed" or not asset.audio_path or not Path(asset.audio_path).is_file())
        ):
            return jsonify({"success": False, "message": f"第 {asset.sequence} 段语音尚未完成"}), 409

    force = _truthy(data.get("force"), False)
    try:
        render_options = normalize_render_options(data.get("render_options"))
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    latest = _latest_video_task(parent_snapshot.id)
    if latest and latest.status in {"pending", "processing"}:
        if not _video_request_matches(latest, audio_mode, render_options):
            return jsonify({
                "success": False,
                "message": "已有另一种音频或渲染配置的合成任务正在执行，请等待完成后再重做",
                "code": "video_render_conflict",
            }), 409
        return jsonify({
            "success": True,
            "message": "视频合成任务已在执行",
            "data": _video_payload(parent_snapshot, latest, include_result=False),
        })
    if (
        latest
        and latest.status == "completed"
        and latest.task_video_path
        and Path(latest.task_video_path).is_file()
        and not force
        and _video_request_matches(latest, audio_mode, render_options)
    ):
        return jsonify({
            "success": True,
            "message": "视频已经生成；如需重做请使用 force=true",
            "data": _video_payload(parent_snapshot, latest),
        })

    task = PublicationTask(
        status="pending",
        current_step="queued",
        progress=0,
        params={
            "feature": VIDEO_FEATURE,
            "user_id": parent_snapshot.user_id,
            "parent_feature": FEATURE_NAME,
            "parent_task_id": parent_snapshot.id,
            "source_feature": STORYBOARD_FEATURE,
            "source_task_id": storyboard_id,
            "workflow_task_id": parent_snapshot.id,
            "audio_mode": audio_mode,
            "audio_track_id": audio_track.id if audio_mode == "whole_audio" else None,
            "render_options": render_options,
        },
        result={},
    )
    task.task_title = f"{parent_snapshot.task_title or '研报'} · {visual_scheme(parent_snapshot.report_visual_scheme_key, require_configured=False).name}视频"
    db.session.add(task)
    db.session.flush()
    task_id = task.id
    PublicationTask.query.filter(PublicationTask.id == parent_snapshot.id).update({
        PublicationTask.status: "processing",
        PublicationTask.current_step: "video_queued",
        PublicationTask.progress: 90,
        PublicationTask.waiting_child_id: task_id,
        PublicationTask.waiting_child_label: "视频合成",
        PublicationTask.waiting_child_status: "pending",
        PublicationTask.waiting_child_step: "queued",
        PublicationTask.waiting_child_updated_at: datetime.utcnow().isoformat() + "Z",
        PublicationTask.error_message: None,
        PublicationTask.completed_at: None,
    }, synchronize_session=False)
    db.session.commit()
    try:
        submit_result = maybe_guarded_apply_async(
            render_stick_figure_video,
            args=[task_id],
            queue=VIDEO_QUEUE,
            feature=VIDEO_FEATURE,
            task_id=task_id,
            retry=False,
        )
        celery_id = submit_result["async_result"].id
    except Exception as exc:
        PublicationTask.query.filter(PublicationTask.id == task_id).update({
            PublicationTask.status: "failed",
            PublicationTask.current_step: "enqueue_failed",
            PublicationTask.error_message: str(exc)[:4000],
            PublicationTask.completed_at: datetime.utcnow(),
        }, synchronize_session=False)
        PublicationTask.query.filter(PublicationTask.id == parent_snapshot.id).update({
            PublicationTask.status: "failed",
            PublicationTask.current_step: "video_enqueue_failed",
            PublicationTask.waiting_child_status: "failed",
            PublicationTask.waiting_child_step: "enqueue_failed",
            PublicationTask.error_message: str(exc)[:4000],
        }, synchronize_session=False)
        db.session.commit()
        return jsonify({"success": False, "message": str(exc)}), 502
    PublicationTask.query.filter(PublicationTask.id == task_id).update({
        PublicationTask.celery_task_id: celery_id,
    }, synchronize_session=False)
    db.session.commit()
    task = _video_task_by_id(task_id)
    return jsonify({
        "success": True,
        "message": (
            f"已按 {render_options['speed']:g} 倍速投递 {len(render_assets)} 段正文音画合成任务"
            f"（{'复用整篇 WAV' if audio_mode == 'whole_audio' else '逐镜头 TTS'}）"
            f"（字幕{'已开启' if render_options['subtitles_enabled'] else '未开启'}，000 标题镜头不参与）"
        ),
        "data": _video_payload(parent_snapshot, task, include_result=False),
    })


@report_stick_figure_video_workflow_bp.route("/jobs/<job_id>/video", methods=["GET"])
@require_login
def get_video(current_user, job_id):
    parent = _owned_parent(current_user, job_id)
    if not parent:
        return jsonify({"success": False, "message": "视觉视频编排任务不存在"}), 404
    return jsonify({"success": True, "data": _video_payload(parent, _latest_video_task(parent.id))})


@report_stick_figure_video_workflow_bp.route("/jobs/<job_id>/video/file", methods=["GET"])
@require_login
def video_file(current_user, job_id):
    parent = _owned_parent(current_user, job_id)
    raw_path = (parent.task_video_path if parent else None) or video_output_path(job_id)
    path = Path(str(raw_path or "")).resolve() if raw_path else None
    root = workflow_output_root(job_id).resolve()
    if not path or root not in path.parents or not path.is_file():
        return jsonify({"success": False, "message": "合成视频不存在"}), 404
    return send_file(path, mimetype="video/mp4", as_attachment=False, download_name=path.name)


@report_stick_figure_video_workflow_bp.route("/jobs/<job_id>/video/subtitles/file", methods=["GET"])
@require_login
def video_subtitle_file(current_user, job_id):
    parent = _owned_parent(current_user, job_id)
    raw_path = subtitle_output_path(job_id) if parent else None
    path = Path(str(raw_path or "")).resolve() if raw_path else None
    root = workflow_output_root(job_id).resolve()
    if not path or root not in path.parents or not path.is_file():
        return jsonify({"success": False, "message": "字幕文件不存在"}), 404
    return send_file(path, mimetype="text/x-ssa", as_attachment=True, download_name="subtitles.ass")


def _owned_parent(current_user, job_id):
    visible_user_ids = tuple(sorted(str(value) for value in _visible_user_ids(current_user)))
    cache_key = (str(job_id), visible_user_ids)
    now = time.monotonic()
    with _scene_parent_access_cache_lock:
        cached = _scene_parent_access_cache.get(cache_key)
        if cached and cached["expires_at"] > now:
            return cached["parent"]

    parent = (
        PublicationTask.query.options(load_only(*SCENE_PARENT_COLUMNS))
        .filter(PublicationTask.id == str(job_id), PublicationTask.feature == FEATURE_NAME)
        .filter(PublicationTask.user_id.in_(visible_user_ids))
        .one_or_none()
    )
    if not parent or SCENE_PARENT_ACCESS_CACHE_TTL_SECONDS <= 0:
        return _scene_parent_snapshot(parent) if parent else None

    snapshot = _scene_parent_snapshot(parent)
    with _scene_parent_access_cache_lock:
        expired = [
            key
            for key, value in _scene_parent_access_cache.items()
            if value["expires_at"] <= now
        ]
        for key in expired:
            _scene_parent_access_cache.pop(key, None)
        if len(_scene_parent_access_cache) >= SCENE_PARENT_ACCESS_CACHE_MAX_ENTRIES:
            oldest_key = min(
                _scene_parent_access_cache,
                key=lambda key: _scene_parent_access_cache[key]["expires_at"],
            )
            _scene_parent_access_cache.pop(oldest_key, None)
        _scene_parent_access_cache[cache_key] = {
            "expires_at": now + SCENE_PARENT_ACCESS_CACHE_TTL_SECONDS,
            "parent": snapshot,
        }
    return snapshot


def _scene_parent_snapshot(parent):
    if parent is None:
        return None
    return SimpleNamespace(
        id=str(parent.id),
        user_id=str(parent.user_id or ""),
        source_task_id=parent.source_task_id,
        task_title=parent.task_title,
        task_video_path=parent.task_video_path,
        report_audio_pdf_attachment_id=parent.report_audio_pdf_attachment_id,
        report_visual_scheme_key=getattr(parent, "report_visual_scheme_key", None) or DEFAULT_VISUAL_SCHEME_KEY,
    )


def _scene_asset_enqueue_snapshot(asset, *, segment=None, scene_count=None):
    return SimpleNamespace(
        id=str(asset.id),
        storyboard_task_id=str(asset.storyboard_task_id),
        segment_id=str(asset.segment_id),
        sequence=int(asset.sequence),
        image_status=asset.image_status,
        image_attempt=int(asset.image_attempt or 0),
        tts_status=asset.tts_status,
        tts_attempt=int(asset.tts_attempt or 0),
        script_text_hash=asset.script_text_hash,
        script_text=(segment.script_text if segment else None),
        scene_count=scene_count,
    )


def _scene_asset_tts_snapshot(asset):
    segment = _scene_segments_by_id(
        asset.storyboard_task_id,
        [asset.segment_id],
    ).get(str(asset.segment_id))
    scene_count = ReportStickFigureSceneAsset.query.filter_by(
        workflow_task_id=str(asset.workflow_task_id),
        storyboard_task_id=str(asset.storyboard_task_id),
    ).count()
    return _scene_asset_enqueue_snapshot(
        asset,
        segment=segment,
        scene_count=scene_count,
    )


def _completed_storyboard(source_task_id, visual_scheme_key=DEFAULT_VISUAL_SCHEME_KEY):
    scheme_key = str(visual_scheme_key or DEFAULT_VISUAL_SCHEME_KEY)
    return (
        PublicationTask.query.options(load_only(
            PublicationTask.id,
            PublicationTask.status,
            PublicationTask.source_task_id,
            PublicationTask.created_at,
            PublicationTask.report_visual_scheme_key,
        ))
        .join(ReportVisualPlanOutput, ReportVisualPlanOutput.task_id == PublicationTask.id)
        .filter(
            PublicationTask.feature == STORYBOARD_FEATURE,
            PublicationTask.source_task_id == str(source_task_id),
            PublicationTask.status == "completed",
        )
        .filter(or_(
            PublicationTask.report_visual_scheme_key == scheme_key,
            *([PublicationTask.report_visual_scheme_key.is_(None)] if scheme_key == DEFAULT_VISUAL_SCHEME_KEY else []),
        ))
        .order_by(PublicationTask.created_at.desc())
        .first()
    )


def _ensure_scene_assets(parent, *, force_refresh=False):
    parent_id = str(parent.id)
    source_task_id = str(parent.source_task_id or "")
    cache_key = (parent_id, source_task_id)
    cached_storyboard_id = None if force_refresh else _scene_structure_cache_get(cache_key)
    if cached_storyboard_id:
        return (
            SimpleNamespace(id=cached_storyboard_id),
            _scene_assets(parent_id, cached_storyboard_id),
        )

    scheme_key = str(parent.report_visual_scheme_key or DEFAULT_VISUAL_SCHEME_KEY)
    storyboard = _completed_storyboard(source_task_id, scheme_key)
    if not storyboard:
        raise ValueError("请先完成标题与正文分镜")
    storyboard_id = str(storyboard.id)
    segments = (
        ReportVisualPlanSegment.query.options(load_only(
            ReportVisualPlanSegment.id,
            ReportVisualPlanSegment.sequence,
            ReportVisualPlanSegment.scene_title,
            ReportVisualPlanSegment.script_text,
            ReportVisualPlanSegment.visual_prompt,
        ))
        .filter(ReportVisualPlanSegment.task_id == storyboard_id)
        .order_by(ReportVisualPlanSegment.sequence.asc())
        .all()
    )
    if not segments or int(segments[0].sequence) != 0:
        raise ValueError("当前是旧版分镜，缺少标题镜头提示词；请先点击“重新生成含标题分镜”")
    scheme_output = (
        ReportVisualPlanOutput.query.options(load_only(
            ReportVisualPlanOutput.visual_scheme_key,
            ReportVisualPlanOutput.visual_scheme_name,
            ReportVisualPlanOutput.image_style_profile,
            ReportVisualPlanOutput.image_gemini_url,
        ))
        .filter(ReportVisualPlanOutput.task_id == storyboard_id)
        .one_or_none()
    )
    if not scheme_output or str(scheme_output.visual_scheme_key) != scheme_key:
        raise ValueError("分镜视觉方案与当前编排任务不一致，请重新生成分镜")
    if visual_scheme(scheme_key, require_configured=False).image_profile_version != str(scheme_output.image_style_profile or ""):
        raise ValueError("视觉方案版本已经升级，请重新生成分镜后再初始化图片")
    existing = {
        row.sequence: row
        for row in _scene_assets(parent_id, storyboard_id)
    }
    changed = False
    for segment in segments:
        script_hash = _text_hash(segment.script_text)
        legacy_prompt_hash = _text_hash(segment.visual_prompt)
        prompt_hash = scene_image_prompt_hash(segment.visual_prompt, segment.script_text, scheme_key)
        asset = existing.get(segment.sequence)
        if asset:
            if asset.segment_id != segment.id or asset.script_text_hash != script_hash:
                raise ValueError(f"第 {segment.sequence} 段分镜内容已变化，请重新生成分镜后再初始化素材")
            if asset.visual_prompt_hash == legacy_prompt_hash:
                asset.visual_prompt_hash = prompt_hash
                asset.image_task_id = None
                asset.image_status = "failed"
                asset.image_progress = 0
                asset.image_error = f"生图风格契约已升级为{scheme_output.visual_scheme_name}方案，请续跑此图片"
                asset.updated_at = datetime.utcnow()
                changed = True
            elif (
                asset.visual_prompt_hash != prompt_hash
                and _completed_image_asset_can_adopt_prompt_hash(asset)
            ):
                # Visual schemes introduced a scheme key into the prompt hash.
                # Assets rendered before that rollout can therefore have a
                # different hash even though their immutable segment, script,
                # style profile and finished image are unchanged.  Adopt the
                # current hash without throwing away an already verified file.
                asset.visual_prompt_hash = prompt_hash
                asset.image_error = None
                asset.updated_at = datetime.utcnow()
                changed = True
            elif asset.visual_prompt_hash != prompt_hash:
                raise ValueError(f"第 {segment.sequence} 段分镜提示词已变化，请重新生成分镜后再初始化素材")
            continue
        asset = ReportStickFigureSceneAsset(
            workflow_task_id=parent_id,
            storyboard_task_id=storyboard_id,
            segment_id=segment.id,
            sequence=segment.sequence,
            script_text_hash=script_hash,
            visual_prompt_hash=prompt_hash,
        )
        db.session.add(asset)
        existing[segment.sequence] = asset
        changed = True
    if changed:
        db.session.commit()
        assets = _scene_assets(parent_id, storyboard_id)
    else:
        assets = [existing[index] for index in sorted(existing)]
    _scene_structure_cache_set(cache_key, storyboard_id, segments)
    return SimpleNamespace(id=storyboard_id), assets


def _completed_image_asset_can_adopt_prompt_hash(asset) -> bool:
    """Recognize a completed pre-scheme-hash asset without weakening retries.

    Segment identity, narration hash and style-profile equality are validated
    by the caller before this check.  Pending/failed rows never qualify, so a
    changed prompt cannot silently enter a new image-generation attempt.
    """
    image_path = str(getattr(asset, "image_path", None) or "").strip()
    if (
        str(getattr(asset, "image_status", "") or "") != "completed"
        or not str(getattr(asset, "image_task_id", "") or "").strip()
        or not image_path
        or int(getattr(asset, "image_bytes", 0) or 0) <= 0
        or int(getattr(asset, "image_width", 0) or 0) <= 0
        or int(getattr(asset, "image_height", 0) or 0) <= 0
    ):
        return False
    path = Path(image_path)
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def _scene_structure_cache_get(cache_key):
    if SCENE_STRUCTURE_CACHE_TTL_SECONDS <= 0:
        return None
    now = time.monotonic()
    with _scene_structure_cache_lock:
        cached = _scene_structure_cache.get(cache_key)
        if not cached or cached["expires_at"] <= now:
            _scene_structure_cache.pop(cache_key, None)
        else:
            return cached["storyboard_id"]

    payload = _persistent_scene_structure_get(cache_key)
    if not payload:
        return None
    segments = [SimpleNamespace(**segment) for segment in payload["segments"]]
    _scene_structure_cache_set(
        cache_key,
        payload["storyboard_id"],
        segments,
        persist=False,
    )
    return payload["storyboard_id"]


def _scene_structure_cache_set(cache_key, storyboard_id, segments, *, persist=True):
    if SCENE_STRUCTURE_CACHE_TTL_SECONDS <= 0:
        return
    now = time.monotonic()
    segment_snapshots = {
        str(segment.id): SimpleNamespace(
            id=str(segment.id),
            scene_title=segment.scene_title,
            script_text=segment.script_text,
            visual_prompt=segment.visual_prompt,
        )
        for segment in segments
    }
    with _scene_structure_cache_lock:
        for cache in (_scene_structure_cache, _scene_segment_cache):
            expired = [key for key, value in cache.items() if value["expires_at"] <= now]
            for key in expired:
                cache.pop(key, None)
            if len(cache) >= SCENE_STRUCTURE_CACHE_MAX_ENTRIES:
                oldest_key = min(cache, key=lambda key: cache[key]["expires_at"])
                cache.pop(oldest_key, None)
        expires_at = now + SCENE_STRUCTURE_CACHE_TTL_SECONDS
        _scene_structure_cache[cache_key] = {
            "expires_at": expires_at,
            "storyboard_id": str(storyboard_id),
        }
        _scene_segment_cache[str(storyboard_id)] = {
            "expires_at": expires_at,
            "segments": segment_snapshots,
        }
    if persist and cache_key[0] != "segments":
        _persistent_scene_structure_set(cache_key, storyboard_id, segment_snapshots)


def _scene_structure_redis_key(cache_key):
    source_task_id = str(cache_key[1] if len(cache_key) > 1 else "")
    digest = hashlib.sha256(source_task_id.encode("utf-8")).hexdigest()
    return f"shioa:stick_figure:structure:v1:{digest}"


def _scene_structure_redis_client():
    try:
        from utils.redis_client import redis_client

        return redis_client
    except Exception:
        return None


def _persistent_scene_structure_get(cache_key):
    if SCENE_STRUCTURE_REDIS_TTL_SECONDS <= 0:
        return None
    client = _scene_structure_redis_client()
    if client is None:
        return None
    try:
        raw = client.get(_scene_structure_redis_key(cache_key))
        payload = json.loads(raw) if raw else None
    except Exception:
        return None
    if not isinstance(payload, dict) or not payload.get("storyboard_id"):
        return None
    segments = payload.get("segments")
    if not isinstance(segments, list) or not all(isinstance(item, dict) for item in segments):
        return None
    return payload


def _persistent_scene_structure_set(cache_key, storyboard_id, segment_snapshots):
    if SCENE_STRUCTURE_REDIS_TTL_SECONDS <= 0:
        return
    client = _scene_structure_redis_client()
    if client is None:
        return
    payload = {
        "storyboard_id": str(storyboard_id),
        "segments": [
            {
                "id": segment.id,
                "scene_title": segment.scene_title,
                "script_text": segment.script_text,
                "visual_prompt": segment.visual_prompt,
            }
            for segment in segment_snapshots.values()
        ],
    }
    try:
        client.setex(
            _scene_structure_redis_key(cache_key),
            SCENE_STRUCTURE_REDIS_TTL_SECONDS,
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        )
    except Exception:
        return


def _scene_segments_by_id(storyboard_id, segment_ids):
    expected = {str(segment_id) for segment_id in segment_ids if segment_id}
    now = time.monotonic()
    with _scene_structure_cache_lock:
        cached = _scene_segment_cache.get(str(storyboard_id))
        if cached and cached["expires_at"] > now and expected.issubset(cached["segments"]):
            return {segment_id: cached["segments"][segment_id] for segment_id in expected}

    segments = (
        ReportVisualPlanSegment.query.options(load_only(
            ReportVisualPlanSegment.id,
            ReportVisualPlanSegment.scene_title,
            ReportVisualPlanSegment.script_text,
            ReportVisualPlanSegment.visual_prompt,
        ))
        .filter(
            ReportVisualPlanSegment.task_id == str(storyboard_id),
            ReportVisualPlanSegment.id.in_(expected),
        )
        .all()
    )
    _scene_structure_cache_set(
        ("segments", str(storyboard_id)),
        str(storyboard_id),
        segments,
    )
    return {str(segment.id): _scene_segment_cache[str(storyboard_id)]["segments"][str(segment.id)] for segment in segments}


def _scene_assets(workflow_id, storyboard_id=None):
    query = ReportStickFigureSceneAsset.query.options(load_only(*SCENE_ASSET_COLUMNS)).filter(
        ReportStickFigureSceneAsset.workflow_task_id == str(workflow_id)
    )
    if storyboard_id:
        query = query.filter(ReportStickFigureSceneAsset.storyboard_task_id == str(storyboard_id))
    return query.order_by(ReportStickFigureSceneAsset.sequence.asc()).all()


def _scene_asset_status_rows(workflow_id, storyboard_id):
    return (
        ReportStickFigureSceneAsset.query.options(load_only(*SCENE_ASSET_STATUS_COLUMNS))
        .filter(
            ReportStickFigureSceneAsset.workflow_task_id == str(workflow_id),
            ReportStickFigureSceneAsset.storyboard_task_id == str(storyboard_id),
        )
        .order_by(ReportStickFigureSceneAsset.sequence.asc())
        .all()
    )


def _scene_status_cache_key(workflow_id, storyboard_id):
    return str(workflow_id), str(storyboard_id)


def _cached_scene_status_bundle(workflow_id, storyboard_id):
    key = _scene_status_cache_key(workflow_id, storyboard_id)
    now = time.monotonic()
    with _scene_status_bundle_cache_lock:
        cached = _scene_status_bundle_cache.get(key)
        if not cached or cached["expires_at"] <= now:
            _scene_status_bundle_cache.pop(key, None)
            return None
        return deepcopy(cached["payload"])


def _cache_scene_status_bundle(payload):
    workflow_id = str(payload.get("workflow_job_id") or "")
    storyboard_id = str(payload.get("storyboard_job_id") or "")
    if not workflow_id or not storyboard_id:
        return
    summary = payload.get("summary") or {}
    has_active_work = bool(summary.get("images_active") or summary.get("tts_active"))
    ttl = (
        SCENE_STATUS_ACTIVE_CACHE_TTL_SECONDS
        if has_active_work
        else SCENE_STATUS_IDLE_CACHE_TTL_SECONDS
    )
    if ttl <= 0:
        return
    key = _scene_status_cache_key(workflow_id, storyboard_id)
    with _scene_status_bundle_cache_lock:
        if len(_scene_status_bundle_cache) >= SCENE_STATUS_CACHE_MAX_ENTRIES:
            _scene_status_bundle_cache.clear()
        _scene_status_bundle_cache[key] = {
            "expires_at": time.monotonic() + ttl,
            "payload": deepcopy(payload),
        }


def _clear_scene_status_bundle_cache(workflow_id=None):
    with _scene_status_bundle_cache_lock:
        if workflow_id is None:
            _scene_status_bundle_cache.clear()
            return
        workflow_id = str(workflow_id)
        stale_keys = [key for key in _scene_status_bundle_cache if key[0] == workflow_id]
        for key in stale_keys:
            _scene_status_bundle_cache.pop(key, None)


def _scene_asset_status_payload(parent, storyboard_id):
    cached = _cached_scene_status_bundle(parent.id, storyboard_id)
    if cached is not None:
        return cached
    key = _scene_status_cache_key(parent.id, storyboard_id)
    fill_lock = _scene_status_fill_locks[hash(key) % len(_scene_status_fill_locks)]
    with fill_lock:
        cached = _cached_scene_status_bundle(parent.id, storyboard_id)
        if cached is not None:
            return cached
        assets = _scene_asset_status_rows(parent.id, storyboard_id)
        if not assets:
            return None
        payload = _scene_asset_status_bundle(
            parent,
            SimpleNamespace(id=str(storyboard_id)),
            assets,
        )
        _cache_scene_status_bundle(payload)
        return payload


def _latest_video_task(workflow_id):
    if not workflow_id:
        return None
    return (
        PublicationTask.query.options(load_only(*VIDEO_TASK_COLUMNS))
        .filter(
            PublicationTask.feature == VIDEO_FEATURE,
            PublicationTask.parent_task_id == str(workflow_id),
        )
        .order_by(PublicationTask.created_at.desc())
        .first()
    )


def _video_request_matches(task, audio_mode, render_options):
    """Check durable inputs before reusing an active or completed render."""

    if not task:
        return False
    payload = (
        _video_manifest(task.parent_task_id)
        if task.status == "completed"
        else _video_task_params(task.id)
    )
    if not isinstance(payload, dict):
        return False
    existing_mode = str(payload.get("audio_mode") or "scene_tts")
    try:
        existing_options = normalize_render_options(payload.get("render_options"))
        requested_options = normalize_render_options(render_options)
    except ValueError:
        return False
    return existing_mode == str(audio_mode) and existing_options == requested_options


def _video_task_by_id(task_id):
    if not task_id:
        return None
    return (
        PublicationTask.query.options(load_only(*VIDEO_TASK_COLUMNS))
        .filter(PublicationTask.id == str(task_id))
        .one_or_none()
    )


def _video_task_result(task_id):
    if not task_id:
        return {}
    result = (
        db.session.query(PublicationTask.result)
        .filter(PublicationTask.id == str(task_id))
        .scalar()
    )
    return result if isinstance(result, dict) else {}


def _video_task_params(task_id):
    if not task_id:
        return {}
    params = (
        db.session.query(PublicationTask.params)
        .filter(PublicationTask.id == str(task_id))
        .scalar()
    )
    return params if isinstance(params, dict) else {}


def _video_manifest(workflow_id):
    if not workflow_id:
        return {}
    path = Path(timeline_manifest_path(str(workflow_id)))
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _video_payload(parent, task=None, *, include_result=True):
    task = task or _latest_video_task(parent.id)
    raw_path = (task.task_video_path if task else None) or parent.task_video_path
    path = Path(str(raw_path or "")) if raw_path else None
    status = {
        "pending": "queued",
        "processing": "processing",
        "completed": "completed",
        "failed": "failed",
        "canceled": "failed",
    }.get(task.status if task else "", task.status if task else "pending")
    manifest = _video_manifest(parent.id) if include_result and status == "completed" else {}
    # Pre-manifest historical tasks retain a cold compatibility fallback. Normal
    # completed requests use the bounded local manifest and never fetch result JSON.
    result = (
        manifest
        or (_video_task_result(task.id) if task and include_result and status == "completed" else {})
    )
    task_params = (
        _video_task_params(task.id)
        if task and include_result and status not in {"completed", "failed"}
        else {}
    )
    try:
        render_options = normalize_render_options(
            result.get("render_options") or task_params.get("render_options")
        )
    except ValueError:
        render_options = normalize_render_options()
    if status == "completed" and (not path or not path.is_file()):
        status = "failed"
    subtitle_path = Path(subtitle_output_path(parent.id))
    return {
        "job_id": task.id if task else None,
        "status": status,
        "progress": int(task.progress or 0) if task else 0,
        "step": task.current_step if task else "pending",
        "error": task.error_message if task else None,
        "bytes": path.stat().st_size if path and path.is_file() else result.get("bytes"),
        "scene_count": result.get("scene_count"),
        "duration_seconds": result.get("duration_seconds"),
        "width": result.get("width"),
        "height": result.get("height"),
        "fps": result.get("fps"),
        "render_profile": result.get("render_profile") or result.get("schema_version"),
        "audio_mode": result.get("audio_mode") or task_params.get("audio_mode") or "scene_tts",
        "render_options": render_options,
        "subtitle_entry_count": result.get("subtitle_entry_count"),
        "subtitle_url": (
            f"/api/report-stick-figure-video-workflows/jobs/{parent.id}/video/subtitles/file"
            if status == "completed" and subtitle_path.is_file()
            else None
        ),
        "url": f"/api/report-stick-figure-video-workflows/jobs/{parent.id}/video/file" if status == "completed" else None,
    }


def _scene_media_asset_snapshot(asset):
    return SimpleNamespace(
        id=str(asset.id),
        workflow_task_id=str(asset.workflow_task_id),
        storyboard_task_id=str(asset.storyboard_task_id),
        segment_id=str(asset.segment_id),
        sequence=int(asset.sequence),
        script_text_hash=asset.script_text_hash,
        image_status=asset.image_status,
        image_attempt=int(asset.image_attempt or 0),
        image_path=asset.image_path,
        tts_status=asset.tts_status,
        tts_attempt=int(asset.tts_attempt or 0),
        audio_path=asset.audio_path,
        timeline_start_seconds=asset.timeline_start_seconds,
        timeline_end_seconds=asset.timeline_end_seconds,
        timeline_confidence=asset.timeline_confidence,
        timeline_source=asset.timeline_source,
    )


def _prune_scene_media_cache(now):
    expired_assets = [
        key
        for key, value in _scene_media_asset_cache.items()
        if value["expires_at"] <= now
    ]
    for key in expired_assets:
        _scene_media_asset_cache.pop(key, None)
    expired_workflows = [
        key
        for key, expires_at in _scene_media_workflow_cache.items()
        if expires_at <= now
    ]
    for key in expired_workflows:
        _scene_media_workflow_cache.pop(key, None)


def _cache_scene_media_assets(assets):
    if SCENE_MEDIA_CACHE_TTL_SECONDS <= 0 or not assets:
        return
    snapshots = [_scene_media_asset_snapshot(asset) for asset in assets]
    workflow_id = snapshots[0].workflow_task_id
    now = time.monotonic()
    expires_at = now + SCENE_MEDIA_CACHE_TTL_SECONDS
    with _scene_media_cache_lock:
        _prune_scene_media_cache(now)
        if len(_scene_media_asset_cache) + len(snapshots) > SCENE_MEDIA_CACHE_MAX_ENTRIES:
            _scene_media_asset_cache.clear()
            _scene_media_workflow_cache.clear()
        stale_keys = [key for key in _scene_media_asset_cache if key[0] == workflow_id]
        for key in stale_keys:
            _scene_media_asset_cache.pop(key, None)
        for snapshot in snapshots:
            _scene_media_asset_cache[(workflow_id, snapshot.id)] = {
                "expires_at": expires_at,
                "asset": snapshot,
            }
        _scene_media_workflow_cache[workflow_id] = expires_at


def _invalidate_scene_media_cache(workflow_id, asset_id=None):
    workflow_id = str(workflow_id)
    _clear_scene_status_bundle_cache(workflow_id)
    with _scene_media_cache_lock:
        _scene_media_workflow_cache.pop(workflow_id, None)
        if asset_id is None:
            stale_keys = [key for key in _scene_media_asset_cache if key[0] == workflow_id]
            for key in stale_keys:
                _scene_media_asset_cache.pop(key, None)
        else:
            _scene_media_asset_cache.pop((workflow_id, str(asset_id)), None)


def _owned_scene_asset(parent, asset_id, *, fresh=False):
    if not parent:
        return None
    workflow_id = str(parent.id)
    asset_id = str(asset_id)
    if fresh or SCENE_MEDIA_CACHE_TTL_SECONDS <= 0:
        asset = (
            ReportStickFigureSceneAsset.query.options(load_only(*SCENE_MEDIA_ASSET_COLUMNS))
            .filter(
                ReportStickFigureSceneAsset.id == asset_id,
                ReportStickFigureSceneAsset.workflow_task_id == workflow_id,
            )
            .one_or_none()
        )
        return _scene_media_asset_snapshot(asset) if asset else None

    now = time.monotonic()
    with _scene_media_cache_lock:
        _prune_scene_media_cache(now)
        cached = _scene_media_asset_cache.get((workflow_id, asset_id))
        if cached:
            return cached["asset"]
        if _scene_media_workflow_cache.get(workflow_id, 0) > now:
            return None

        # Hold the lock across the one bulk read so concurrent media requests
        # cannot amplify into one database query per scene.
        assets = (
            ReportStickFigureSceneAsset.query.options(load_only(*SCENE_MEDIA_ASSET_COLUMNS))
            .filter(ReportStickFigureSceneAsset.workflow_task_id == workflow_id)
            .order_by(ReportStickFigureSceneAsset.sequence.asc())
            .all()
        )
        _cache_scene_media_assets(assets)
        cached = _scene_media_asset_cache.get((workflow_id, asset_id))
        return cached["asset"] if cached else None


def _enqueue_image_asset(parent, asset):
    parent_id = str(parent.id)
    parent_user_id = str(parent.user_id or "")
    asset_id = str(asset.id)
    segment_id = str(asset.segment_id)
    sequence = int(asset.sequence)
    image_attempt = int(asset.image_attempt or 0)
    broker_task_id = str(uuid4())
    task = PublicationTask(
        celery_task_id=broker_task_id,
        status="pending",
        current_step="queued",
        progress=0,
        params={
            "feature": IMAGE_FEATURE,
            "user_id": parent_user_id,
            "parent_feature": FEATURE_NAME,
            "parent_task_id": parent_id,
            "source_feature": "report_visual_plan_segment",
            "source_task_id": segment_id,
            "scene_asset_id": asset_id,
        },
        result={"scene_asset_id": asset_id},
    )
    db.session.add(task)
    db.session.flush()
    task_id = task.id
    ReportStickFigureSceneAsset.query.filter(ReportStickFigureSceneAsset.id == asset_id).update({
        ReportStickFigureSceneAsset.image_task_id: task_id,
        ReportStickFigureSceneAsset.image_status: "queued",
        ReportStickFigureSceneAsset.image_progress: 0,
        ReportStickFigureSceneAsset.image_attempt: image_attempt + 1,
        ReportStickFigureSceneAsset.image_error: None,
        ReportStickFigureSceneAsset.updated_at: datetime.utcnow(),
    }, synchronize_session=False)
    db.session.commit()
    try:
        submit_result = maybe_guarded_apply_async(
            generate_scene_image,
            args=[task_id],
            queue=IMAGE_QUEUE,
            feature=IMAGE_FEATURE,
            task_id=task_id,
            broker_task_id=broker_task_id,
            retry=False,
        )
        celery_id = submit_result["async_result"].id
    except Exception as exc:
        PublicationTask.query.filter(PublicationTask.id == task_id).update({
            PublicationTask.status: "failed",
            PublicationTask.current_step: "enqueue_failed",
            PublicationTask.error_message: str(exc)[:4000],
            PublicationTask.completed_at: datetime.utcnow(),
        }, synchronize_session=False)
        ReportStickFigureSceneAsset.query.filter(ReportStickFigureSceneAsset.id == asset_id).update({
            ReportStickFigureSceneAsset.image_status: "failed",
            ReportStickFigureSceneAsset.image_error: str(exc)[:4000],
            ReportStickFigureSceneAsset.updated_at: datetime.utcnow(),
        }, synchronize_session=False)
        db.session.commit()
        raise
    if str(celery_id) != broker_task_id:
        PublicationTask.query.filter(PublicationTask.id == task_id).update({
            PublicationTask.celery_task_id: str(celery_id),
        }, synchronize_session=False)
        db.session.commit()
    _invalidate_scene_media_cache(parent_id, asset_id)
    return {"scene_asset_id": asset_id, "sequence": sequence, "job_id": task_id, "status": "queued"}


def _prepare_tts_asset(parent, asset):
    parent_id = str(parent.id)
    parent_user_id = str(parent.user_id or "")
    asset_id = str(asset.id)
    storyboard_id = str(asset.storyboard_task_id)
    segment_id = str(asset.segment_id)
    sequence = int(asset.sequence)
    script_text_hash = asset.script_text_hash
    text = str(asset.script_text or "").strip()
    if not text:
        raise ValueError(f"第 {sequence} 段没有可朗读文本")
    attempt = int(asset.tts_attempt or 0) + 1
    task_id = str(uuid4())
    broker_task_id = str(uuid4())
    metadata = {
        "source": FEATURE_NAME,
        "workflow_job_id": parent_id,
        "scene_asset_id": asset_id,
        "segment_id": segment_id,
        "sequence": sequence,
        "chunk_index": sequence,
        "chunk_total": int(asset.scene_count or 0),
        "chunk_attempt": attempt,
        "recommended_file_name": _scene_audio_filename(sequence),
        "text_hash": script_text_hash,
    }
    task = PublicationTask(
        id=task_id,
        celery_task_id=broker_task_id,
        status="pending",
        current_step="queued",
        progress=0,
        params={
            "feature": DOUBAO_TTS_FEATURE,
            "user_id": parent_user_id,
            "parent_feature": FEATURE_NAME,
            "parent_task_id": parent_id,
            "source_feature": "report_visual_plan_segment",
            "source_task_id": segment_id,
            "doubao_browser_tts": {
                "text_source": {"kind": "report_visual_plan_segment", "segment_id": segment_id},
                "metadata": metadata,
            },
        },
        result={"metadata": metadata, "audio": None, "logs": []},
    )
    db.session.add(task)
    return {
        "task_id": task_id,
        "broker_task_id": broker_task_id,
        "parent_id": parent_id,
        "asset_id": asset_id,
        "sequence": sequence,
        "attempt": attempt,
    }


def _enqueue_tts_assets(parent, assets):
    prepared = []
    errors = []
    for asset in assets:
        try:
            prepared.append(_prepare_tts_asset(parent, asset))
        except Exception as exc:
            errors.append({"sequence": int(asset.sequence), "message": str(exc)})
    if not prepared:
        return [], errors

    now = datetime.utcnow()
    db.session.flush()
    db.session.bulk_update_mappings(
        ReportStickFigureSceneAsset,
        [
            {
                "id": item["asset_id"],
                "tts_task_id": item["task_id"],
                "tts_status": "queued",
                "tts_progress": 0,
                "tts_attempt": item["attempt"],
                "tts_error": None,
                "updated_at": now,
            }
            for item in prepared
        ],
    )
    db.session.commit()

    jobs = []
    failed = []
    celery_id_updates = []
    for item in prepared:
        try:
            submit_result = maybe_guarded_apply_async(
                process_doubao_browser_tts_job,
                args=[item["task_id"]],
                queue=os.getenv("DOUBAO_BROWSER_TTS_QUEUE", DOUBAO_TTS_QUEUE),
                feature=DOUBAO_TTS_FEATURE,
                task_id=item["task_id"],
                broker_task_id=item["broker_task_id"],
                retry=False,
            )
            celery_id = str(submit_result["async_result"].id)
            if celery_id != item["broker_task_id"]:
                celery_id_updates.append({"id": item["task_id"], "celery_task_id": celery_id})
            jobs.append({
                "scene_asset_id": item["asset_id"],
                "sequence": item["sequence"],
                "job_id": item["task_id"],
                "status": "queued",
            })
        except Exception as exc:
            failed.append((item, str(exc)[:4000]))
            errors.append({"sequence": item["sequence"], "message": str(exc)})

    if celery_id_updates or failed:
        failed_at = datetime.utcnow()
        task_updates = list(celery_id_updates)
        task_updates.extend({
            "id": item["task_id"],
            "status": "failed",
            "current_step": "enqueue_failed",
            "error_message": message,
            "completed_at": failed_at,
            "updated_at": failed_at,
        } for item, message in failed)
        if task_updates:
            db.session.bulk_update_mappings(PublicationTask, task_updates)
        if failed:
            db.session.bulk_update_mappings(
                ReportStickFigureSceneAsset,
                [{
                    "id": item["asset_id"],
                    "tts_status": "failed",
                    "tts_error": message,
                    "updated_at": failed_at,
                } for item, message in failed],
            )
        db.session.commit()

    _invalidate_scene_media_cache(prepared[0]["parent_id"])
    return jobs, errors


def _enqueue_tts_asset(parent, asset):
    jobs, errors = _enqueue_tts_assets(parent, [asset])
    if errors:
        raise RuntimeError(errors[0]["message"])
    if not jobs:
        raise RuntimeError("分镜语音任务未成功投递")
    return jobs[0]


def _sync_tts_assets(assets, *, asset_loader=_scene_assets):
    task_ids = [asset.tts_task_id for asset in assets if asset.tts_task_id]
    if not task_ids:
        return assets
    tasks = (
        PublicationTask.query.options(load_only(
            PublicationTask.id,
            PublicationTask.status,
            PublicationTask.current_step,
            PublicationTask.progress,
            PublicationTask.error_message,
            PublicationTask.doubao_tts_wav_path,
        ))
        .filter(PublicationTask.id.in_(task_ids), PublicationTask.feature == DOUBAO_TTS_FEATURE)
        .all()
    )
    by_id = {task.id: task for task in tasks}
    changed = False
    for asset in assets:
        task = by_id.get(asset.tts_task_id)
        if not task:
            continue
        status = {
            "pending": "queued",
            "processing": "processing",
            "completed": "completed",
            "failed": "failed",
            "blocked_by_captcha": "blocked",
            "canceled": "failed",
        }.get(task.status, task.status)
        values = {
            "tts_status": status,
            "tts_progress": int(task.progress or 0),
            "tts_error": task.error_message,
        }
        path = str(task.doubao_tts_wav_path or "").strip()
        if task.status == "completed" and path and Path(path).is_file():
            values.update({
                "tts_status": "completed",
                "tts_progress": 100,
                "audio_path": path,
                "audio_bytes": Path(path).stat().st_size,
                "audio_seconds": _wav_seconds(path),
                "tts_error": None,
            })
        for key, value in values.items():
            if getattr(asset, key) != value:
                setattr(asset, key, value)
                changed = True
    if changed:
        workflow_id = str(assets[0].workflow_task_id)
        storyboard_id = str(assets[0].storyboard_task_id)
        db.session.commit()
        return asset_loader(workflow_id, storyboard_id)
    return assets


def _whole_audio_track(workflow_id, storyboard_id=None):
    query = ReportStickFigureAudioTrack.query.options(load_only(
        ReportStickFigureAudioTrack.id,
        ReportStickFigureAudioTrack.workflow_task_id,
        ReportStickFigureAudioTrack.storyboard_task_id,
        ReportStickFigureAudioTrack.source_task_id,
        ReportStickFigureAudioTrack.alignment_task_id,
        ReportStickFigureAudioTrack.mode,
        ReportStickFigureAudioTrack.status,
        ReportStickFigureAudioTrack.audio_path,
        ReportStickFigureAudioTrack.audio_bytes,
        ReportStickFigureAudioTrack.audio_seconds,
        ReportStickFigureAudioTrack.asr_cache_key,
        ReportStickFigureAudioTrack.asr_model,
        ReportStickFigureAudioTrack.aligned_scene_count,
        ReportStickFigureAudioTrack.average_confidence,
        ReportStickFigureAudioTrack.minimum_confidence,
        ReportStickFigureAudioTrack.manifest_path,
        ReportStickFigureAudioTrack.error,
        ReportStickFigureAudioTrack.created_at,
        ReportStickFigureAudioTrack.updated_at,
    )).filter(ReportStickFigureAudioTrack.workflow_task_id == str(workflow_id))
    if storyboard_id:
        query = query.filter(ReportStickFigureAudioTrack.storyboard_task_id == str(storyboard_id))
    return _audio_track_snapshot(query.one_or_none())


def _audio_track_snapshot(track):
    if not track:
        return None
    return SimpleNamespace(
        id=str(track.id),
        workflow_task_id=str(track.workflow_task_id),
        storyboard_task_id=str(track.storyboard_task_id),
        source_task_id=str(track.source_task_id),
        alignment_task_id=str(track.alignment_task_id or ""),
        mode=str(track.mode or "whole_audio"),
        status=str(track.status or "available"),
        audio_path=str(track.audio_path or ""),
        audio_bytes=int(track.audio_bytes or 0),
        audio_seconds=float(track.audio_seconds or 0),
        asr_cache_key=str(track.asr_cache_key or ""),
        asr_model=str(track.asr_model or ""),
        aligned_scene_count=int(track.aligned_scene_count or 0),
        average_confidence=float(track.average_confidence or 0),
        minimum_confidence=float(track.minimum_confidence or 0),
        manifest_path=str(track.manifest_path or ""),
        error=str(track.error or ""),
        created_at=track.created_at,
        updated_at=track.updated_at,
    )


def _ensure_whole_audio_track(parent, storyboard):
    candidate = find_reusable_audio_candidate(
        attachment_id=str(parent.report_audio_pdf_attachment_id or ""),
        material_task_id=str(parent.source_task_id or ""),
        visible_user_ids=_visible_user_ids(parent.user_id),
    )
    if not candidate:
        raise ValueError("没有找到同一研报已完成且文件有效的整篇 WAV")
    current = _whole_audio_track(parent.id)
    same_binding = bool(
        current
        and current.storyboard_task_id == str(storyboard.id)
        and current.source_task_id == candidate.task_id
        and current.audio_path == candidate.audio_path
        and current.audio_bytes == candidate.audio_bytes
        and abs(float(current.audio_seconds or 0) - float(candidate.audio_seconds or 0)) < 0.01
    )
    if same_binding:
        return current
    now = datetime.utcnow()
    if current:
        ReportStickFigureAudioTrack.query.filter(ReportStickFigureAudioTrack.id == current.id).update({
            ReportStickFigureAudioTrack.storyboard_task_id: str(storyboard.id),
            ReportStickFigureAudioTrack.source_task_id: candidate.task_id,
            ReportStickFigureAudioTrack.alignment_task_id: None,
            ReportStickFigureAudioTrack.mode: "whole_audio",
            ReportStickFigureAudioTrack.status: "available",
            ReportStickFigureAudioTrack.audio_path: candidate.audio_path,
            ReportStickFigureAudioTrack.audio_bytes: candidate.audio_bytes,
            ReportStickFigureAudioTrack.audio_seconds: candidate.audio_seconds,
            ReportStickFigureAudioTrack.source_script_hash: None,
            ReportStickFigureAudioTrack.storyboard_script_hash: None,
            ReportStickFigureAudioTrack.asr_cache_key: None,
            ReportStickFigureAudioTrack.asr_model: None,
            ReportStickFigureAudioTrack.aligned_scene_count: 0,
            ReportStickFigureAudioTrack.average_confidence: None,
            ReportStickFigureAudioTrack.minimum_confidence: None,
            ReportStickFigureAudioTrack.manifest_path: None,
            ReportStickFigureAudioTrack.error: None,
            ReportStickFigureAudioTrack.updated_at: now,
        }, synchronize_session=False)
        track_id = current.id
    else:
        row = ReportStickFigureAudioTrack(
            workflow_task_id=str(parent.id),
            storyboard_task_id=str(storyboard.id),
            source_task_id=candidate.task_id,
            mode="whole_audio",
            status="available",
            audio_path=candidate.audio_path,
            audio_bytes=candidate.audio_bytes,
            audio_seconds=candidate.audio_seconds,
        )
        db.session.add(row)
        db.session.flush()
        track_id = str(row.id)
    ReportStickFigureSceneAsset.query.filter(
        ReportStickFigureSceneAsset.workflow_task_id == str(parent.id),
    ).update({
        ReportStickFigureSceneAsset.timeline_start_seconds: None,
        ReportStickFigureSceneAsset.timeline_end_seconds: None,
        ReportStickFigureSceneAsset.timeline_confidence: None,
        ReportStickFigureSceneAsset.timeline_source: None,
        ReportStickFigureSceneAsset.updated_at: now,
    }, synchronize_session=False)
    db.session.commit()
    return _whole_audio_track(parent.id, storyboard.id)


def _enqueue_whole_audio_alignment(parent, storyboard, track, *, force=False):
    if track.status == "completed" and _whole_audio_track_ready(track) and not force:
        return SimpleNamespace(id=track.alignment_task_id, status="completed")
    if track.alignment_task_id and not force:
        existing = (
            PublicationTask.query.options(load_only(
                PublicationTask.id,
                PublicationTask.status,
            ))
            .filter(
                PublicationTask.id == track.alignment_task_id,
                PublicationTask.feature == AUDIO_ALIGNMENT_FEATURE,
            )
            .one_or_none()
        )
        if existing and existing.status in {"pending", "processing"}:
            return SimpleNamespace(id=str(existing.id), status=str(existing.status))
    task = PublicationTask(
        status="pending",
        current_step="queued",
        progress=0,
        params={
            "feature": AUDIO_ALIGNMENT_FEATURE,
            "user_id": str(parent.user_id or ""),
            "parent_feature": FEATURE_NAME,
            "parent_task_id": str(parent.id),
            "source_feature": REPORT_AUDIO_FEATURE,
            "source_task_id": str(track.source_task_id),
            "workflow_task_id": str(parent.id),
            "storyboard_task_id": str(storyboard.id),
            "audio_track_id": str(track.id),
            "asr_model": "medium",
        },
        result={},
    )
    task.task_title = f"{parent.task_title or '研报'} · 整篇音频时间轴对齐"
    db.session.add(task)
    db.session.flush()
    task_id = str(task.id)
    now = datetime.utcnow()
    ReportStickFigureAudioTrack.query.filter(ReportStickFigureAudioTrack.id == track.id).update({
        ReportStickFigureAudioTrack.alignment_task_id: task_id,
        ReportStickFigureAudioTrack.status: "pending",
        ReportStickFigureAudioTrack.error: None,
        ReportStickFigureAudioTrack.updated_at: now,
    }, synchronize_session=False)
    ReportStickFigureSceneAsset.query.filter(
        ReportStickFigureSceneAsset.workflow_task_id == str(parent.id),
        ReportStickFigureSceneAsset.storyboard_task_id == str(storyboard.id),
        ReportStickFigureSceneAsset.sequence >= 1,
    ).update({
        ReportStickFigureSceneAsset.timeline_start_seconds: None,
        ReportStickFigureSceneAsset.timeline_end_seconds: None,
        ReportStickFigureSceneAsset.timeline_confidence: None,
        ReportStickFigureSceneAsset.timeline_source: None,
        ReportStickFigureSceneAsset.updated_at: now,
    }, synchronize_session=False)
    PublicationTask.query.filter(PublicationTask.id == str(parent.id)).update({
        PublicationTask.status: "processing",
        PublicationTask.current_step: "whole_audio_alignment_queued",
        PublicationTask.progress: 70,
        PublicationTask.waiting_child_id: task_id,
        PublicationTask.waiting_child_label: "整篇音频时间轴对齐",
        PublicationTask.waiting_child_status: "pending",
        PublicationTask.waiting_child_step: "queued",
        PublicationTask.waiting_child_updated_at: now.isoformat() + "Z",
        PublicationTask.error_message: None,
        PublicationTask.completed_at: None,
        PublicationTask.updated_at: now,
    }, synchronize_session=False)
    db.session.commit()
    try:
        submit_result = maybe_guarded_apply_async(
            align_stick_figure_whole_audio,
            args=[task_id],
            queue=AUDIO_ALIGNMENT_QUEUE,
            feature=AUDIO_ALIGNMENT_FEATURE,
            task_id=task_id,
            retry=False,
        )
        celery_id = submit_result["async_result"].id
    except Exception as exc:
        failed_at = datetime.utcnow()
        PublicationTask.query.filter(PublicationTask.id == task_id).update({
            PublicationTask.status: "failed",
            PublicationTask.current_step: "enqueue_failed",
            PublicationTask.error_message: str(exc)[:4000],
            PublicationTask.completed_at: failed_at,
            PublicationTask.updated_at: failed_at,
        }, synchronize_session=False)
        ReportStickFigureAudioTrack.query.filter(ReportStickFigureAudioTrack.id == track.id).update({
            ReportStickFigureAudioTrack.status: "failed",
            ReportStickFigureAudioTrack.error: str(exc)[:4000],
            ReportStickFigureAudioTrack.updated_at: failed_at,
        }, synchronize_session=False)
        db.session.commit()
        raise
    PublicationTask.query.filter(PublicationTask.id == task_id).update({
        PublicationTask.celery_task_id: celery_id,
    }, synchronize_session=False)
    db.session.commit()
    _invalidate_scene_media_cache(parent.id)
    return SimpleNamespace(id=task_id, status="pending")


def _whole_audio_track_ready(track, expected_scene_count=0):
    if not track or track.status != "completed":
        return False
    if not track.audio_path or not Path(track.audio_path).is_file():
        return False
    if not track.manifest_path or not Path(track.manifest_path).is_file():
        return False
    return not expected_scene_count or int(track.aligned_scene_count or 0) == int(expected_scene_count)


def _audio_strategy_payload(parent, storyboard, body_scene_count):
    track = _whole_audio_track(parent.id, storyboard.id)
    candidate = find_reusable_audio_candidate(
        attachment_id=str(parent.report_audio_pdf_attachment_id or ""),
        material_task_id=str(parent.source_task_id or ""),
        visible_user_ids=_visible_user_ids(parent.user_id),
    )
    ready = _whole_audio_track_ready(track, body_scene_count)
    status = track.status if track else ("available" if candidate else "unavailable")
    return {
        "recommended_mode": "whole_audio" if candidate else "scene_tts",
        "available": bool(candidate),
        "ready": ready,
        "status": status,
        "track_id": track.id if track else None,
        "alignment_job_id": track.alignment_task_id if track else None,
        "source_task_id": track.source_task_id if track else (candidate.task_id if candidate else None),
        "audio_seconds": track.audio_seconds if track else (candidate.audio_seconds if candidate else None),
        "audio_bytes": track.audio_bytes if track else (candidate.audio_bytes if candidate else None),
        "aligned_scene_count": track.aligned_scene_count if track else 0,
        "average_confidence": track.average_confidence if track else None,
        "minimum_confidence": track.minimum_confidence if track else None,
        "asr_model": track.asr_model if track else None,
        "error": track.error if track else None,
        "preview_url": (
            f"/api/report-stick-figure-video-workflows/jobs/{parent.id}/audio-preview"
            if track or candidate
            else None
        ),
    }


def _scene_asset_bundle(parent, storyboard, assets):
    _cache_scene_media_assets(assets)
    segment_ids = [asset.segment_id for asset in assets]
    by_id = _scene_segments_by_id(storyboard.id, segment_ids)
    items = []
    for asset in assets:
        segment = by_id.get(str(asset.segment_id))
        item = _scene_asset_state_item(parent, asset)
        item.update({
            "sequence": asset.sequence,
            "scene_title": segment.scene_title if segment else None,
            "script_text": segment.script_text if segment else "",
            "visual_prompt": segment.visual_prompt if segment else "",
        })
        items.append(item)
    body_items = [item for item in items if int(item["sequence"]) >= 1]
    audio_strategy = _audio_strategy_payload(parent, storyboard, len(body_items))
    scheme_output = (
        ReportVisualPlanOutput.query.options(load_only(
            ReportVisualPlanOutput.visual_scheme_key,
            ReportVisualPlanOutput.visual_scheme_name,
            ReportVisualPlanOutput.image_style_profile,
        ))
        .filter(ReportVisualPlanOutput.task_id == str(storyboard.id))
        .one()
    )
    scheme = visual_scheme(scheme_output.visual_scheme_key, require_configured=False)
    return {
        "workflow_job_id": parent.id,
        "storyboard_job_id": storyboard.id,
        "video": _video_payload(parent),
        "audio_strategy": audio_strategy,
        "image_style": {
            "scheme_key": scheme_output.visual_scheme_key,
            "scheme_name": scheme_output.visual_scheme_name,
            "profile": scheme_output.image_style_profile,
            "description": scheme.description,
        },
        "summary": _scene_asset_summary(items, audio_strategy),
        "items": items,
    }


def _scene_asset_status_bundle(parent, storyboard, assets):
    items = [_scene_asset_state_item(parent, asset) for asset in assets]
    body_items = [item for item in items if int(item["sequence"]) >= 1]
    audio_strategy = _audio_strategy_payload(parent, storyboard, len(body_items))
    return {
        "workflow_job_id": parent.id,
        "storyboard_job_id": storyboard.id,
        "video": _video_payload(parent),
        "audio_strategy": audio_strategy,
        "summary": _scene_asset_summary(items, audio_strategy),
        "items": items,
    }


def _cache_legacy_scene_bundle(payload):
    workflow_id = str(payload.get("workflow_job_id") or "")
    storyboard_id = str(payload.get("storyboard_job_id") or "")
    if not workflow_id or not storyboard_id:
        return
    with _legacy_scene_bundle_cache_lock:
        if len(_legacy_scene_bundle_cache) >= LEGACY_SCENE_BUNDLE_CACHE_MAX_ENTRIES:
            _legacy_scene_bundle_cache.clear()
        _legacy_scene_bundle_cache[workflow_id] = {
            "storyboard_id": storyboard_id,
            "expires_at": time.monotonic() + LEGACY_SCENE_BUNDLE_CACHE_TTL_SECONDS,
            "payload": deepcopy(payload),
        }
    _cache_scene_status_bundle(_scene_status_payload_from_full_bundle(payload))


def _scene_status_payload_from_full_bundle(payload):
    return {
        "workflow_job_id": payload.get("workflow_job_id"),
        "storyboard_job_id": payload.get("storyboard_job_id"),
        "video": deepcopy(payload.get("video")),
        "audio_strategy": deepcopy(payload.get("audio_strategy")),
        "summary": deepcopy(payload.get("summary") or {}),
        "items": [
            {
                "id": item.get("id"),
                "sequence": item.get("sequence"),
                "image": deepcopy(item.get("image") or {}),
                "tts": deepcopy(item.get("tts") or {}),
                "timeline": deepcopy(item.get("timeline") or {}),
            }
            for item in (payload.get("items") or [])
        ],
    }


def _clear_legacy_scene_bundle_cache(workflow_id=None):
    with _legacy_scene_bundle_cache_lock:
        if workflow_id:
            _legacy_scene_bundle_cache.pop(str(workflow_id), None)
        else:
            _legacy_scene_bundle_cache.clear()


def _cached_legacy_scene_bundle(parent):
    workflow_id = str(parent.id)
    now = time.monotonic()
    with _legacy_scene_bundle_cache_lock:
        cached = _legacy_scene_bundle_cache.get(workflow_id)
        if not cached or cached["expires_at"] <= now:
            _legacy_scene_bundle_cache.pop(workflow_id, None)
            return None
        cached = deepcopy(cached)

    storyboard = _completed_storyboard(parent.source_task_id)
    if not storyboard or str(storyboard.id) != cached["storyboard_id"]:
        _clear_legacy_scene_bundle_cache(workflow_id)
        return None
    status_payload = _scene_asset_status_payload(parent, storyboard.id)
    if status_payload is None:
        _clear_legacy_scene_bundle_cache(workflow_id)
        return None
    merged = _merge_legacy_scene_bundle(cached["payload"], status_payload)
    if merged is None:
        _clear_legacy_scene_bundle_cache(workflow_id)
        return None
    _cache_legacy_scene_bundle(merged)
    return merged


def _merge_legacy_scene_bundle(full_payload, status_payload):
    full_items = full_payload.get("items") or []
    status_items = status_payload.get("items") or []
    state_by_id = {str(item.get("id")): item for item in status_items}
    if len(full_items) != len(status_items) or any(str(item.get("id")) not in state_by_id for item in full_items):
        return None
    items = []
    for item in full_items:
        state = state_by_id[str(item.get("id"))]
        items.append({
            **item,
            **state,
            "image": {**(item.get("image") or {}), **(state.get("image") or {})},
            "tts": {**(item.get("tts") or {}), **(state.get("tts") or {})},
            "timeline": {**(item.get("timeline") or {}), **(state.get("timeline") or {})},
        })
    return {**full_payload, **status_payload, "items": items}


def _scene_asset_state_item(parent, asset):
    return {
        "id": asset.id,
        "sequence": asset.sequence,
        "image": {
            "status": asset.image_status,
            "progress": asset.image_progress,
            "attempt": asset.image_attempt,
            "source": (
                "storyboard_gemini"
                if str(getattr(asset, "image_task_id", None) or "").startswith("storyboard-gemini:")
                else "image_queue"
            ),
            "bytes": asset.image_bytes,
            "width": asset.image_width,
            "height": asset.image_height,
            "error": asset.image_error,
            "url": f"/api/report-stick-figure-video-workflows/jobs/{parent.id}/scene-assets/{asset.id}/image" if asset.image_status == "completed" else None,
        },
        "tts": {
            "status": asset.tts_status,
            "progress": asset.tts_progress,
            "attempt": asset.tts_attempt,
            "bytes": asset.audio_bytes,
            "seconds": asset.audio_seconds,
            "error": asset.tts_error,
            "url": f"/api/report-stick-figure-video-workflows/jobs/{parent.id}/scene-assets/{asset.id}/audio" if asset.tts_status == "completed" else None,
        },
        "timeline": {
            "source": asset.timeline_source,
            "start_seconds": asset.timeline_start_seconds,
            "end_seconds": asset.timeline_end_seconds,
            "confidence": asset.timeline_confidence,
        },
    }


def _scene_asset_summary(items, audio_strategy):
    body_items = [item for item in items if int(item["sequence"]) >= 1]
    scene_tts_ready = bool(body_items) and all(item["tts"]["status"] == "completed" for item in body_items)
    audio_ready = bool(audio_strategy["ready"] or scene_tts_ready)
    return {
        "total": len(items),
        "images_completed": sum(1 for item in items if item["image"]["status"] == "completed"),
        "images_failed": sum(1 for item in items if item["image"]["status"] in {"failed", "blocked"}),
        "images_active": sum(1 for item in items if item["image"]["status"] in {"queued", "processing"}),
        "tts_completed": sum(1 for item in body_items if item["tts"]["status"] == "completed"),
        "tts_failed": sum(1 for item in body_items if item["tts"]["status"] in {"failed", "blocked"}),
        "tts_active": sum(1 for item in body_items if item["tts"]["status"] in {"queued", "processing"}),
        "body_total": len(body_items),
        "scene_tts_ready": scene_tts_ready,
        "whole_audio_available": audio_strategy["available"],
        "whole_audio_ready": audio_strategy["ready"],
        "audio_ready": audio_ready,
        "audio_mode": "whole_audio" if audio_strategy["ready"] else ("scene_tts" if scene_tts_ready else None),
        "aligned_scenes": audio_strategy["aligned_scene_count"],
    }


def _selected_sequences(data):
    raw = data.get("sequences")
    if raw is None:
        return None
    if not isinstance(raw, list):
        raw = [raw]
    values = set()
    for value in raw:
        try:
            values.add(int(value))
        except (TypeError, ValueError):
            continue
    return values


def _scene_audio_filename(sequence):
    return "000_title.wav" if int(sequence) == 0 else f"{int(sequence):03d}.wav"


def _text_hash(value):
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _wav_seconds(path):
    try:
        with wave.open(str(path), "rb") as audio:
            rate = audio.getframerate()
            return round(audio.getnframes() / rate, 3) if rate else 0
    except (OSError, wave.Error):
        return 0


def _truthy(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled", "y"}


def _materials_by_id(ids):
    values = [str(value) for value in ids if value]
    if not values:
        return {}
    rows = PublicationTask.query.options(load_only(*CHILD_COLUMNS)).filter(PublicationTask.id.in_(values)).all()
    return {row.id: row for row in rows}


def _latest_storyboard(source_task_id, visual_scheme_key=DEFAULT_VISUAL_SCHEME_KEY):
    if not source_task_id:
        return None
    scheme_key = str(visual_scheme_key or DEFAULT_VISUAL_SCHEME_KEY)
    rows = (
        PublicationTask.query.options(load_only(
            PublicationTask.id,
            PublicationTask.status,
            PublicationTask.current_step,
            PublicationTask.progress,
            PublicationTask.error_message,
            PublicationTask.source_task_id,
            PublicationTask.created_at,
            PublicationTask.completed_at,
            PublicationTask.report_visual_scheme_key,
        ))
        .filter(PublicationTask.feature == STORYBOARD_FEATURE, PublicationTask.source_task_id == source_task_id)
        .filter(or_(
            PublicationTask.report_visual_scheme_key == scheme_key,
            *([PublicationTask.report_visual_scheme_key.is_(None)] if scheme_key == DEFAULT_VISUAL_SCHEME_KEY else []),
        ))
        .order_by(PublicationTask.created_at.desc())
        .all()
    )
    return _preferred_storyboard(rows)


def _preferred_storyboard(rows):
    """Keep the last usable storyboard when a regeneration attempt fails.

    Rows are expected in newest-first order.  An active/new successful attempt
    still wins immediately, but a failed or canceled retry must not hide an
    older completed plan (and all media assets attached to that plan).
    """
    rows = list(rows or [])
    if not rows:
        return None
    latest = rows[0]
    if getattr(latest, "status", None) not in {"failed", "canceled"}:
        return latest
    return next(
        (row for row in rows if getattr(row, "status", None) == "completed"),
        latest,
    )


def _storyboard_for_workflow(parent):
    if not parent:
        return None
    storyboards = (
        PublicationTask.query.options(load_only(
            PublicationTask.id,
            PublicationTask.status,
            PublicationTask.current_step,
            PublicationTask.progress,
            PublicationTask.error_message,
            PublicationTask.source_task_id,
            PublicationTask.parent_task_id,
            PublicationTask.created_at,
            PublicationTask.completed_at,
            PublicationTask.report_visual_scheme_key,
        ))
        .filter(
            PublicationTask.feature == STORYBOARD_FEATURE,
            PublicationTask.parent_task_id == str(parent.id),
        )
        .order_by(PublicationTask.created_at.desc())
        .all()
    )
    storyboard = _preferred_storyboard(storyboards)
    if storyboard and storyboard.status not in {"failed", "canceled"}:
        return storyboard
    source_storyboard = _latest_storyboard(parent.source_task_id, parent.report_visual_scheme_key)
    if source_storyboard and source_storyboard.status == "completed":
        return source_storyboard
    return storyboard or source_storyboard


def _storyboards_by_workflow(parents):
    workflow_ids = [str(parent.id) for parent in parents if getattr(parent, "id", None)]
    if not workflow_ids:
        return {}
    rows = (
        PublicationTask.query.options(load_only(
            PublicationTask.id,
            PublicationTask.status,
            PublicationTask.current_step,
            PublicationTask.progress,
            PublicationTask.error_message,
            PublicationTask.source_task_id,
            PublicationTask.parent_task_id,
            PublicationTask.created_at,
            PublicationTask.completed_at,
            PublicationTask.report_visual_scheme_key,
        ))
        .filter(
            PublicationTask.feature == STORYBOARD_FEATURE,
            PublicationTask.parent_task_id.in_(workflow_ids),
        )
        .order_by(PublicationTask.created_at.desc())
        .all()
    )
    grouped = {}
    for row in rows:
        grouped.setdefault(str(row.parent_task_id), []).append(row)
    return {
        workflow_id: _preferred_storyboard(storyboards)
        for workflow_id, storyboards in grouped.items()
    }


def _storyboards_by_source(source_ids):
    values = [str(value) for value in source_ids if value]
    if not values:
        return {}
    rows = (
        PublicationTask.query.options(load_only(
            PublicationTask.id,
            PublicationTask.status,
            PublicationTask.current_step,
            PublicationTask.progress,
            PublicationTask.error_message,
            PublicationTask.source_task_id,
            PublicationTask.created_at,
            PublicationTask.completed_at,
            PublicationTask.report_visual_scheme_key,
        ))
        .filter(PublicationTask.feature == STORYBOARD_FEATURE, PublicationTask.source_task_id.in_(values))
        .order_by(PublicationTask.created_at.desc())
        .all()
    )
    grouped = {}
    for row in rows:
        key = (
            str(row.source_task_id or ""),
            str(row.report_visual_scheme_key or DEFAULT_VISUAL_SCHEME_KEY),
        )
        grouped.setdefault(key, []).append(row)
    return {
        key: _preferred_storyboard(storyboards)
        for key, storyboards in grouped.items()
    }


def _segment_counts_by_task(task_ids):
    values = [str(value) for value in task_ids if value]
    if not values:
        return {}
    rows = (
        db.session.query(ReportVisualPlanOutput.task_id, ReportVisualPlanOutput.segment_count)
        .filter(ReportVisualPlanOutput.task_id.in_(values))
        .all()
    )
    return {row.task_id: int(row.segment_count or 0) for row in rows}


def _asset_summaries(board_by_workflow):
    workflow_ids = [workflow_id for workflow_id, board_id in board_by_workflow.items() if board_id]
    if not workflow_ids:
        return {}
    rows = (
        ReportStickFigureSceneAsset.query.options(load_only(
            ReportStickFigureSceneAsset.workflow_task_id,
            ReportStickFigureSceneAsset.storyboard_task_id,
            ReportStickFigureSceneAsset.sequence,
            ReportStickFigureSceneAsset.image_status,
            ReportStickFigureSceneAsset.image_progress,
            ReportStickFigureSceneAsset.tts_status,
            ReportStickFigureSceneAsset.tts_progress,
        ))
        .filter(ReportStickFigureSceneAsset.workflow_task_id.in_(workflow_ids))
        .all()
    )
    result = {}
    for row in rows:
        if board_by_workflow.get(row.workflow_task_id) != row.storyboard_task_id:
            continue
        summary = result.setdefault(row.workflow_task_id, {
            "total": 0,
            "body_total": 0,
            "images_completed": 0,
            "images_failed": 0,
            "images_active": 0,
            "image_progress_sum": 0,
            "tts_completed": 0,
            "tts_failed": 0,
            "tts_active": 0,
            "tts_progress_sum": 0,
        })
        summary["total"] += 1
        is_body = int(row.sequence or 0) >= 1
        summary["body_total"] += int(is_body)
        summary["images_completed"] += int(row.image_status == "completed")
        summary["images_failed"] += int(row.image_status in {"failed", "blocked"})
        summary["images_active"] += int(row.image_status in {"queued", "processing"})
        summary["image_progress_sum"] += int(row.image_progress or 0)
        summary["tts_completed"] += int(is_body and row.tts_status == "completed")
        summary["tts_failed"] += int(is_body and row.tts_status in {"failed", "blocked"})
        summary["tts_active"] += int(is_body and row.tts_status in {"queued", "processing"})
        summary["tts_progress_sum"] += int(row.tts_progress or 0) if is_body else 0

    tracks = (
        ReportStickFigureAudioTrack.query.options(load_only(
            ReportStickFigureAudioTrack.workflow_task_id,
            ReportStickFigureAudioTrack.storyboard_task_id,
            ReportStickFigureAudioTrack.status,
            ReportStickFigureAudioTrack.aligned_scene_count,
        ))
        .filter(ReportStickFigureAudioTrack.workflow_task_id.in_(workflow_ids))
        .all()
    )
    for track in tracks:
        if board_by_workflow.get(track.workflow_task_id) != track.storyboard_task_id:
            continue
        summary = result.get(track.workflow_task_id)
        if not summary:
            continue
        summary["audio_alignment_status"] = track.status
        summary["whole_audio_ready"] = (
            track.status == "completed"
            and int(track.aligned_scene_count or 0) >= int(summary.get("body_total") or 0)
        )
    return result


def _workflow_payload(parent, material, storyboard, *, segment_count=None, asset_summary=None):
    scheme = visual_scheme(getattr(parent, "report_visual_scheme_key", None), require_configured=False)
    material_status = _status_label(material.status) if material else "queued"
    board_status = _status_label(storyboard.status) if storyboard else None
    if segment_count is None:
        segment_count = 0
    if storyboard and not segment_count:
        output = db.session.get(ReportVisualPlanOutput, storyboard.id)
        segment_count = int(output.segment_count or 0) if output else 0

    if not material:
        status, progress, stage = "queued", 1, "等待基础文案任务"
    elif material_status == "failed":
        status, progress, stage = "failed", max(1, int(material.progress or 0) * 35 // 100), "基础文案失败"
    elif material_status in {"queued", "running"}:
        status = material_status
        progress = max(1, int(material.progress or 0) * 35 // 100)
        stage = f"基础文案 · {material.current_step or material_status}"
    elif not storyboard:
        status, progress, stage = "running", 35, "基础文案完成 · 等待生成分镜"
    elif board_status == "failed":
        status, progress, stage = "failed", 35 + int(storyboard.progress or 0) * 25 // 100, "分镜失败"
    elif board_status in {"queued", "running"}:
        status = board_status
        progress = 35 + int(storyboard.progress or 0) * 25 // 100
        stage = f"分镜 · {storyboard.current_step or board_status}"
    else:
        status, progress, stage = "running", 60, f"分镜完成 · {segment_count} 镜头 · 等待生成图片"

    assets = asset_summary or {}
    total = int(assets.get("total") or 0)
    if board_status == "succeeded" and total:
        body_total = int(assets.get("body_total") or max(0, total - 1))
        image_done = int(assets.get("images_completed") or 0)
        image_failed = int(assets.get("images_failed") or 0)
        tts_done = int(assets.get("tts_completed") or 0)
        tts_failed = int(assets.get("tts_failed") or 0)
        image_ratio = float(assets.get("image_progress_sum") or 0) / max(1, total * 100)
        tts_ratio = float(assets.get("tts_progress_sum") or 0) / max(1, body_total * 100)
        whole_audio_ready = bool(assets.get("whole_audio_ready"))
        audio_ready = whole_audio_ready or (body_total > 0 and tts_done >= body_total)
        if image_failed:
            status, progress, stage = "failed", 60 + int(image_ratio * 15), f"图片部分失败 · {image_done}/{total} · 可续跑"
        elif image_done < total:
            status, progress, stage = "running", 60 + int(image_ratio * 15), f"生成图片 · {image_done}/{total}"
        elif not audio_ready and assets.get("audio_alignment_status") in {"pending", "processing"}:
            status, progress, stage = "running", 82, "整篇 WAV 时间轴对齐中"
        elif not audio_ready and tts_failed:
            status, progress, stage = "failed", 75 + int(tts_ratio * 15), f"分段语音部分失败 · {tts_done}/{body_total} · 可续跑"
        elif not audio_ready:
            status, progress, stage = "running", 75 + int(tts_ratio * 15), f"等待整篇 WAV 对齐或分段语音 · {tts_done}/{body_total}"
        else:
            video_path = Path(str(parent.task_video_path or "")) if parent.task_video_path else None
            if video_path and video_path.is_file():
                status, progress, stage = "succeeded", 100, f"视频已完成 · {total} 镜头"
            elif parent.waiting_child_label == "视频合成" and parent.waiting_child_status == "failed":
                status, progress, stage = "failed", max(90, int(parent.progress or 90)), "视频合成失败 · 可续跑"
            elif parent.waiting_child_label == "视频合成" and parent.waiting_child_status in {"pending", "processing"}:
                status, progress = "running", max(90, min(99, int(parent.progress or 90)))
                stage = f"视频合成 · {parent.waiting_child_step or parent.waiting_child_status}"
            else:
                audio_label = "整篇 WAV" if whole_audio_ready else "分段 TTS"
                status, progress, stage = "running", 90, f"音画素材完成 · {audio_label} · 等待合成视频"

    title = (material.task_title if material else None) or parent.task_title or parent.report_audio_pdf_topic_title or parent.report_audio_pdf_filename or "研报 PDF 视觉视频"
    parent_error = parent.error_message
    if board_status == "succeeded" and str(parent.current_step or "").startswith("storyboard_"):
        # A later failed regeneration may have marked the parent as failed even
        # though an earlier completed storyboard and its assets remain usable.
        parent_error = None
    return {
        "job_id": parent.id,
        "workflow_job_id": parent.id,
        **_workflow_source_payload(parent, material),
        "storyboard_job_id": storyboard.id if storyboard else None,
        "visual_scheme": scheme.public_payload(),
        "status": status,
        "stage": stage,
        "step": storyboard.current_step if storyboard else (material.current_step if material else parent.current_step),
        "progress": max(0, min(100, int(progress))),
        "title": title,
        "pdf": {
            "attachment_id": (material.report_audio_pdf_attachment_id if material else None) or parent.report_audio_pdf_attachment_id,
            "filename": (material.report_audio_pdf_filename if material else None) or parent.report_audio_pdf_filename,
            "bytes": (material.report_audio_pdf_bytes if material else None) or parent.report_audio_pdf_bytes,
            "topic_title": (material.report_audio_pdf_topic_title if material else None) or parent.report_audio_pdf_topic_title,
        },
        "script_chars": int(material.final_script_chars or 0) if material else 0,
        "segment_count": segment_count,
        "asset_summary": asset_summary,
        "created_at": _iso(parent.created_at),
        "updated_at": _iso(parent.updated_at),
        "error": (storyboard.error_message if storyboard else None) or (material.error_message if material else None) or parent_error,
    }


def _workflow_source_payload(parent, material):
    """Describe the reusable script source without pretending every source is a material job."""
    source_feature = str(
        getattr(parent, "source_feature", None)
        or getattr(material, "feature", None)
        or MATERIAL_FEATURE
    )
    source_job_id = str(
        getattr(parent, "source_task_id", None)
        or getattr(material, "id", None)
        or ""
    )
    payload = {
        # Kept for storyboard APIs and legacy clients; report-audio tasks are
        # also valid script-material sources.
        "material_job_id": source_job_id,
        "source_feature": source_feature,
        "source_job_id": source_job_id,
    }
    if source_feature == REPORT_AUDIO_FEATURE:
        payload["report_audio_job_id"] = source_job_id
    return payload


def _status_label(value):
    return {"pending": "queued", "processing": "running", "completed": "succeeded", "failed": "failed", "canceled": "canceled"}.get(value or "", value or "queued")


def _iso(value):
    return value.isoformat() + "Z" if value else None
