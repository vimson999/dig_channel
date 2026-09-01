"""HTTP API for the generic, resumable script-to-video workflow."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
import uuid

from flask import Blueprint, jsonify, request, send_file
from sqlalchemy import func
from sqlalchemy.orm import load_only
from sqlalchemy.orm.attributes import flag_modified

from models import db
from models.task import PublicationTask
from services.doubao_browser_tts import status_payload as doubao_status_payload
from services.gemini_browser import status_payload as gemini_status_payload
from services.report_visual_scheme import visual_scheme, visual_scheme_payloads
from services.runtime_guard import maybe_guarded_apply_async
from services.script_video_workflow import (
    AUDIO_STEP,
    FEATURE_NAME,
    IMAGE_FEATURE_NAME,
    VIDEO_STEP,
    audio_upload_max_bytes,
    audio_upload_path,
    audio_chunks_cover_source,
    normalized_audio_filename,
    normalize_script,
    safe_output_file,
    split_audio_chunks,
    text_hash,
    validate_audio_edit_options,
    validate_scene_rows,
    workflow_output_root,
)
from tasks.script_video_workflow import (
    _has_usable_image,
    _image_scene_hash,
    _image_sequence,
    _scene_content_hash,
    enqueue_image_stage,
    finalize_audio_edit,
    generate_storyboard,
    prepare_uploaded_audio,
    process_audio,
    render_video,
)
from utils.auth_utils import require_login


script_video_workflow_bp = Blueprint(
    "script_video_workflow",
    __name__,
    url_prefix="/api/script-video-workflows",
)

MAX_SCRIPT_CHARS = 50_000
PARENT_ACTIVE_STEPS = {
    "audio_queued",
    "audio_processing",
    "audio_upload_queued",
    "audio_upload_processing",
    "audio_edit_queued",
    "audio_edit_processing",
    "storyboard_queued",
    "storyboard_processing",
    "images_queued",
    "images_processing",
    "alignment_queued",
    "render_video",
    "video_queued",
}
RENDER_ACTIVE_STEPS = {
    "alignment_queued",
    "alignment_ready",
    "video_queued",
    "render_video",
}

PARENT_DETAIL_COLUMNS = (
    PublicationTask.id,
    PublicationTask.celery_task_id,
    PublicationTask.status,
    PublicationTask.current_step,
    PublicationTask.progress,
    PublicationTask.params,
    PublicationTask.result,
    PublicationTask.error_message,
    PublicationTask.feature,
    PublicationTask.user_id,
    PublicationTask.task_title,
    PublicationTask.report_visual_scheme_key,
    PublicationTask.waiting_child_label,
    PublicationTask.waiting_child_id,
    PublicationTask.waiting_child_status,
    PublicationTask.waiting_child_step,
    PublicationTask.created_at,
    PublicationTask.updated_at,
    PublicationTask.completed_at,
)
PARENT_SUMMARY_COLUMNS = (
    PublicationTask.id,
    PublicationTask.status,
    PublicationTask.current_step,
    PublicationTask.progress,
    PublicationTask.error_message,
    PublicationTask.feature,
    PublicationTask.user_id,
    PublicationTask.task_title,
    PublicationTask.created_at,
    PublicationTask.updated_at,
    PublicationTask.completed_at,
)
PARENT_POLL_COLUMNS = (
    PublicationTask.id,
    PublicationTask.status,
    PublicationTask.current_step,
    PublicationTask.progress,
    PublicationTask.result,
    PublicationTask.error_message,
    PublicationTask.feature,
    PublicationTask.user_id,
    PublicationTask.waiting_child_label,
    PublicationTask.waiting_child_id,
    PublicationTask.waiting_child_status,
    PublicationTask.waiting_child_step,
    PublicationTask.updated_at,
    PublicationTask.completed_at,
)
IMAGE_CHILD_COLUMNS = (
    PublicationTask.id,
    PublicationTask.status,
    PublicationTask.current_step,
    PublicationTask.progress,
    PublicationTask.params,
    PublicationTask.result,
    PublicationTask.error_message,
    PublicationTask.parent_task_id,
    PublicationTask.created_at,
    PublicationTask.completed_at,
)
IMAGE_CHILD_POLL_COLUMNS = tuple(
    column for column in IMAGE_CHILD_COLUMNS if column is not PublicationTask.params
)


@script_video_workflow_bp.route("/status", methods=["GET"])
@require_login
def service_status(_current_user):
    return jsonify({
        "success": True,
        "data": {
            "feature": FEATURE_NAME,
            "visual_schemes": visual_scheme_payloads(),
            "providers": {
                "doubao_browser_tts": doubao_status_payload(),
                "gemini_browser": gemini_status_payload(),
            },
        },
    })


@script_video_workflow_bp.route("/jobs", methods=["POST"])
@require_login
def create_job(current_user):
    data = request.get_json(silent=True) or {}
    try:
        source_text = normalize_script(data.get("text"))
        if len(source_text) > MAX_SCRIPT_CHARS:
            raise ValueError(f"文案不能超过 {MAX_SCRIPT_CHARS} 个字符")
        scheme = visual_scheme(data.get("visual_scheme_key"), require_configured=False)
        audio_chunks = split_audio_chunks(source_text, data.get("audio_chunk_chars") or 800)
        # A visual scheme's Gemini Gem, not a character counter, owns the
        # initial storyboard and prompt semantics.  Existing jobs with local
        # rows remain reopenable and can be explicitly replanned.
        scenes = []
    except (TypeError, ValueError) as exc:
        return jsonify({"success": False, "message": str(exc)}), 400

    workflow = {
        "source_text": source_text,
        "source_text_hash": text_hash(source_text),
        "audio_chunks": audio_chunks,
        "scenes": scenes,
        "visual_scheme_key": scheme.key,
        "visual_scheme_profile": scheme.image_profile_version,
        "render_options": {
            "subtitles_enabled": True,
            "watermark_enabled": False,
            "speed": 1.0,
        },
    }
    parent = PublicationTask(
        status="pending",
        current_step="script_ready",
        progress=5,
        params={
            "feature": FEATURE_NAME,
            "user_id": str(getattr(current_user, "id", "") or ""),
            "visual_scheme_key": scheme.key,
            FEATURE_NAME: workflow,
        },
        result={
            "stage": "script_ready",
            "audio": {"chunks_total": len(audio_chunks), "chunks_completed": 0},
            "storyboard": {"status": "not_submitted", "segment_count": 0},
            "images": {"total": len(scenes), "completed": 0, "items": []},
            "alignment": None,
            "video": None,
            "logs": [_log("script_ready", "文案已保存；完成整篇语音后可投递 Gemini 规划分镜", 5)],
        },
    )
    parent.task_title = source_text[:120]
    db.session.add(parent)
    db.session.commit()
    payload = _workflow_payload(parent, image_children=[])
    return jsonify({
        "success": True,
        "message": "文案工作流已创建",
        "job_id": parent.id,
        "data": payload,
    })


@script_video_workflow_bp.route("/jobs", methods=["GET"])
@require_login
def list_jobs(current_user):
    page = _bounded_int(request.args.get("page"), 1, 1, 100_000)
    per_page = _bounded_int(request.args.get("per_page"), 20, 1, 100)
    summary_only = str(request.args.get("summary") or "").strip().lower() in {
        "1", "true", "yes", "on",
    }
    columns = PARENT_SUMMARY_COLUMNS if summary_only else PARENT_DETAIL_COLUMNS
    filters = (
        PublicationTask.feature == FEATURE_NAME,
        PublicationTask.user_id == str(getattr(current_user, "id", "") or ""),
    )
    total = int(
        db.session.query(func.count(PublicationTask.id)).filter(*filters).scalar()
        or 0
    )
    parents = (
        PublicationTask.query.options(load_only(*columns))
        .filter(*filters)
        .order_by(PublicationTask.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )
    if summary_only:
        items = [_workflow_summary_payload(parent) for parent in parents]
    else:
        image_children = _image_children_for_parents(
            [str(parent.id) for parent in parents]
        )
        items = [
            _workflow_payload(
                parent,
                image_children=image_children.get(str(parent.id), []),
            )
            for parent in parents
        ]
    return jsonify({
        "success": True,
        "data": {
            "items": items,
            "pagination": {
                "page": page,
                "per_page": per_page,
                "total": total,
                "pages": (total + per_page - 1) // per_page,
            },
        },
        "items": items,
    })


@script_video_workflow_bp.route("/jobs/<job_id>", methods=["GET"])
@require_login
def get_job(current_user, job_id):
    poll_only = str(request.args.get("poll") or "").strip().lower() in {
        "1", "true", "yes", "on",
    }
    parent = _owned_parent(current_user, job_id, include_detail=not poll_only)
    if not parent:
        return jsonify({"success": False, "message": "文案到成片任务不存在"}), 404
    if poll_only:
        payload = _workflow_poll_payload(
            parent,
            image_children=_image_children_for_parent(parent.id, poll_only=True),
        )
    else:
        payload = _workflow_payload(parent)
    return jsonify({"success": True, "data": payload, **payload})


@script_video_workflow_bp.route("/jobs/<job_id>/audio-chunks", methods=["PATCH"])
@require_login
def update_audio_chunks(current_user, job_id):
    parent = _owned_parent(current_user, job_id)
    if not parent:
        return jsonify({"success": False, "message": "文案到成片任务不存在"}), 404
    if parent.current_step in PARENT_ACTIVE_STEPS or _audio_is_ready(parent) or _audio_source_is_ready(parent):
        return jsonify({"success": False, "message": "语音已开始或已完成，不能再修改分段"}), 409
    data = request.get_json(silent=True) or {}
    try:
        chunks = _validated_audio_chunks(data.get("audio_chunks"), _source_text(parent))
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400

    params = dict(parent.params or {})
    workflow = dict(_workflow(parent))
    workflow["audio_chunks"] = chunks
    params[FEATURE_NAME] = workflow
    result = dict(parent.result or {})
    result["audio"] = {"chunks_total": len(chunks), "chunks_completed": 0}
    result["stage"] = "script_ready"
    result["logs"] = list(result.get("logs") or [])[-79:] + [_log("audio_chunks_saved", "语音分段已更新", 5)]
    parent.params = params
    parent.result = result
    parent.current_step = "script_ready"
    parent.progress = min(int(parent.progress or 5), 5)
    parent.error_message = None
    parent.completed_at = None
    parent.sync_projection_fields()
    flag_modified(parent, "params")
    flag_modified(parent, "result")
    db.session.commit()
    return jsonify({"success": True, "data": _workflow_payload(parent)})


@script_video_workflow_bp.route("/jobs/<job_id>/audio", methods=["POST"])
@require_login
def submit_audio(current_user, job_id):
    parent = _owned_parent(current_user, job_id)
    if not parent:
        return jsonify({"success": False, "message": "文案到成片任务不存在"}), 404
    if _render_is_active(parent):
        return jsonify({"success": False, "message": "视频正在对齐或合成，暂不能切换语音来源"}), 409
    try:
        _validated_audio_chunks(_workflow(parent).get("audio_chunks"), _source_text(parent))
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    if _audio_is_ready(parent):
        return jsonify({
            "success": True,
            "message": "完整 WAV 已存在，无需重复投递",
            "data": _workflow_payload(parent),
        })
    if _audio_source_is_ready(parent):
        return jsonify({"success": False, "message": "已有可编辑语音，请先生成最终 WAV 或上传新语音"}), 409

    _prepare_parent_enqueue(parent, "audio_queued", 10, "已投递豆包浏览器语音阶段")
    try:
        submission = maybe_guarded_apply_async(
            process_audio,
            args=[parent.id],
            queue="report_audio_workflow",
            feature=FEATURE_NAME,
            task_id=parent.id,
            retry=False,
        )
    except Exception as exc:
        _mark_submit_failure(parent, "audio_enqueue_failed", f"语音任务入队失败: {exc}")
        return jsonify({"success": False, "message": parent.error_message}), 502
    parent = _owned_parent(current_user, job_id)
    parent.celery_task_id = str(getattr(submission.get("async_result"), "id", "") or "") or None
    db.session.commit()
    return jsonify({
        "success": True,
        "message": "已投递豆包浏览器语音阶段",
        "data": {"job_id": parent.id, "celery_id": parent.celery_task_id, "workflow": _workflow_payload(parent)},
    })


@script_video_workflow_bp.route("/jobs/<job_id>/audio/upload", methods=["POST"])
@require_login
def upload_audio(current_user, job_id):
    """Persist one user-supplied audio source and queue normalization for the editor."""

    parent = _owned_parent(current_user, job_id)
    if not parent:
        return jsonify({"success": False, "message": "文案到成片任务不存在"}), 404
    if _render_is_active(parent):
        return jsonify({"success": False, "message": "视频正在对齐或合成，暂不能替换语音"}), 409
    if str(parent.current_step or "") in {
        "audio_queued", "audio_processing", "audio_upload_queued", "audio_upload_processing",
        "audio_edit_queued", "audio_edit_processing", "storyboard_queued", "storyboard_processing",
        "images_queued", "images_processing",
    }:
        return jsonify({"success": False, "message": "当前工作流正在处理，暂不能替换语音"}), 409
    upload = request.files.get("audio") or request.files.get("file")
    if not upload or not str(getattr(upload, "filename", "") or "").strip():
        return jsonify({"success": False, "message": "请选择一段音频文件"}), 400
    try:
        original_filename = normalized_audio_filename(upload.filename)
        output = audio_upload_path(parent.id, original_filename, upload_key=uuid.uuid4().hex)
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400

    try:
        upload.save(str(output))
        bytes_count = int(output.stat().st_size)
    except OSError as exc:
        try:
            output.unlink(missing_ok=True)
        except OSError:
            pass
        return jsonify({"success": False, "message": f"保存音频失败：{exc}"}), 500
    limit = audio_upload_max_bytes()
    if bytes_count <= 0:
        output.unlink(missing_ok=True)
        return jsonify({"success": False, "message": "上传音频为空"}), 400
    if bytes_count > limit:
        output.unlink(missing_ok=True)
        return jsonify({"success": False, "message": f"音频超过 {limit // (1024 * 1024)}MB 限制"}), 400

    result = dict(parent.result or {})
    result["audio"] = {
        "state": "upload_queued",
        "source_type": "upload",
        "upload_path": str(output),
        "upload_bytes": bytes_count,
        "original_filename": original_filename,
    }
    result["alignment"] = None
    result["video"] = None
    parent.result = result
    flag_modified(parent, "result")
    _prepare_parent_enqueue(parent, "audio_upload_queued", 8, "上传音频已保存，正在转换为可编辑 WAV")
    try:
        submission = maybe_guarded_apply_async(
            prepare_uploaded_audio,
            args=[parent.id],
            queue="report_audio_workflow",
            feature=FEATURE_NAME,
            task_id=parent.id,
            retry=False,
        )
    except Exception as exc:
        _mark_submit_failure(parent, "audio_upload_enqueue_failed", f"上传音频转换任务入队失败: {exc}")
        return jsonify({"success": False, "message": parent.error_message}), 502
    parent = _owned_parent(current_user, job_id)
    parent.celery_task_id = str(getattr(submission.get("async_result"), "id", "") or "") or None
    db.session.commit()
    return jsonify({
        "success": True,
        "message": "音频已上传，转换完成后可试听和编辑",
        "data": {"job_id": parent.id, "celery_id": parent.celery_task_id, "workflow": _workflow_payload(parent)},
    })


@script_video_workflow_bp.route("/jobs/<job_id>/audio/edit", methods=["POST"])
@require_login
def submit_audio_edit(current_user, job_id):
    """Queue the one confirmed head/tail trim and speed edit as a final WAV."""

    parent = _owned_parent(current_user, job_id)
    if not parent:
        return jsonify({"success": False, "message": "文案到成片任务不存在"}), 404
    if _render_is_active(parent):
        return jsonify({"success": False, "message": "视频正在对齐或合成，暂不能编辑语音"}), 409
    if str(parent.current_step or "") in {"audio_upload_queued", "audio_upload_processing", "audio_edit_queued", "audio_edit_processing"}:
        return jsonify({"success": False, "message": "语音正在处理中，请稍后再试"}), 409
    audio = _audio_payload(parent)
    source = _bounded_artifact_path(parent, audio.get("source_wav_path"))
    if not source or not source.is_file() or source.stat().st_size <= 0:
        return jsonify({"success": False, "message": "请先上传音频或完成豆包语音合并"}), 409
    try:
        edit = validate_audio_edit_options(
            request.get_json(silent=True) or {},
            source_seconds=audio.get("source_seconds"),
        )
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    previous_edit = audio.get("edit") if isinstance(audio.get("edit"), dict) else {}
    try:
        revision = max(0, int(previous_edit.get("revision") or 0)) + 1
    except (TypeError, ValueError):
        revision = 1
    updated_audio = dict(audio)
    updated_audio.update({"state": "edit_queued", "edit": {**edit, "revision": revision}})
    updated_audio.pop("wav_path", None)
    updated_audio.pop("seconds", None)
    updated_audio.pop("bytes", None)
    result = dict(parent.result or {})
    result["audio"] = updated_audio
    result["alignment"] = None
    result["video"] = None
    parent.result = result
    flag_modified(parent, "result")
    _prepare_parent_enqueue(parent, "audio_edit_queued", 25, "已保存语音编辑参数，正在生成最终 WAV")
    try:
        submission = maybe_guarded_apply_async(
            finalize_audio_edit,
            args=[parent.id],
            queue="report_audio_workflow",
            feature=FEATURE_NAME,
            task_id=parent.id,
            retry=False,
        )
    except Exception as exc:
        _mark_submit_failure(parent, "audio_edit_enqueue_failed", f"最终 WAV 任务入队失败: {exc}")
        return jsonify({"success": False, "message": parent.error_message}), 502
    parent = _owned_parent(current_user, job_id)
    parent.celery_task_id = str(getattr(submission.get("async_result"), "id", "") or "") or None
    db.session.commit()
    return jsonify({
        "success": True,
        "message": "正在生成最终 WAV，完成后即可进入 Gemini 分镜",
        "data": {"job_id": parent.id, "celery_id": parent.celery_task_id, "workflow": _workflow_payload(parent)},
    })


@script_video_workflow_bp.route("/jobs/<job_id>/storyboard", methods=["POST"])
@require_login
def submit_storyboard(current_user, job_id):
    """Queue the selected scheme's text-only Gemini storyboard Gem."""

    parent = _owned_parent(current_user, job_id)
    if not parent:
        return jsonify({"success": False, "message": "文案到成片任务不存在"}), 404
    if parent.current_step == VIDEO_STEP:
        return jsonify({"success": False, "message": "成片已完成，请创建新任务重新规划分镜"}), 409
    if _render_is_active(parent):
        return jsonify({"success": False, "message": "视频正在对齐或合成，暂不能重新规划分镜"}), 409
    if not _audio_is_ready(parent):
        return jsonify({"success": False, "message": "请先完成并确认整篇 WAV，再规划分镜"}), 409
    if parent.current_step in {"storyboard_queued", "storyboard_processing"}:
        return jsonify({"success": False, "message": "Gemini 正在规划分镜，无需重复投递"}), 409
    if str(parent.current_step or "") in {"images_queued", "images_processing"}:
        return jsonify({"success": False, "message": "Gemini 图片正在生成，暂不能覆盖分镜规划"}), 409
    try:
        # Jobs created before the generic workflow stored the scheme in the
        # outer task params/projection only.  Preserve their selected visual
        # Gem when they are replanned instead of silently falling back.
        scheme_key = (
            parent.report_visual_scheme_key
            or (parent.params or {}).get("visual_scheme_key")
            or _workflow(parent).get("visual_scheme_key")
        )
        scheme = visual_scheme(scheme_key)
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400

    _prepare_parent_enqueue(parent, "storyboard_queued", 41, f"已投递 {scheme.name} Gemini 分镜规划")
    try:
        submission = maybe_guarded_apply_async(
            generate_storyboard,
            args=[parent.id],
            queue="report_pdf_gemini",
            feature=FEATURE_NAME,
            task_id=parent.id,
            retry=False,
        )
    except Exception as exc:
        _mark_submit_failure(parent, "storyboard_enqueue_failed", f"Gemini 分镜任务入队失败: {exc}")
        return jsonify({"success": False, "message": parent.error_message}), 502
    parent = _owned_parent(current_user, job_id)
    parent.celery_task_id = str(getattr(submission.get("async_result"), "id", "") or "") or None
    db.session.commit()
    return jsonify({
        "success": True,
        "message": "已投递 Gemini 规划分镜，完成后请审核图片提示词",
        "data": {"job_id": parent.id, "celery_id": parent.celery_task_id, "workflow": _workflow_payload(parent)},
    })


@script_video_workflow_bp.route("/jobs/<job_id>/scenes", methods=["PATCH"])
@require_login
def update_scenes(current_user, job_id):
    parent = _owned_parent(current_user, job_id)
    if not parent:
        return jsonify({"success": False, "message": "文案到成片任务不存在"}), 404
    if parent.current_step == VIDEO_STEP:
        return jsonify({"success": False, "message": "成片已完成，请创建新任务修改分镜"}), 409
    if _render_is_active(parent):
        return jsonify({"success": False, "message": "视频正在对齐或合成，暂不能修改分镜"}), 409
    data = request.get_json(silent=True) or {}
    try:
        scenes = validate_scene_rows(
            data.get("scenes") if isinstance(data.get("scenes"), list) else [],
            _source_text(parent),
            require_prompts=False,
        )
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400

    params = dict(parent.params or {})
    workflow = dict(_workflow(parent))
    workflow["scenes"] = scenes
    params[FEATURE_NAME] = workflow
    parent.params = params
    _invalidate_changed_image_children(parent, scenes)
    result = dict(parent.result or {})
    result["images"] = {"total": len(scenes), "completed": 0, "active": 0, "failed": 0, "blocked": 0, "items": []}
    result["stage"] = "storyboard_ready"
    result["logs"] = list(result.get("logs") or [])[-79:] + [_log("scenes_saved", "视觉分镜已更新；只会使变化分镜的图片失效", max(40, int(parent.progress or 0)))]
    parent.result = result
    parent.status = "processing" if _audio_is_ready(parent) else "pending"
    parent.current_step = "storyboard_ready"
    parent.progress = max(40 if _audio_is_ready(parent) else 5, min(int(parent.progress or 0), 40))
    parent.error_message = None
    parent.completed_at = None
    parent.sync_projection_fields()
    flag_modified(parent, "params")
    flag_modified(parent, "result")
    db.session.commit()
    return jsonify({"success": True, "data": _workflow_payload(parent)})


@script_video_workflow_bp.route("/jobs/<job_id>/images", methods=["POST"])
@require_login
def submit_images(current_user, job_id):
    parent = _owned_parent(current_user, job_id)
    if not parent:
        return jsonify({"success": False, "message": "文案到成片任务不存在"}), 404
    if parent.current_step == VIDEO_STEP:
        return jsonify({"success": False, "message": "成片已完成，请创建新任务生成图片"}), 409
    if _render_is_active(parent):
        return jsonify({"success": False, "message": "视频正在对齐或合成，暂不能重新投递图片"}), 409
    if not _audio_is_ready(parent):
        return jsonify({"success": False, "message": "请先完成并确认整篇 WAV"}), 409
    if not isinstance(_workflow(parent).get("scenes"), list) or not _workflow(parent).get("scenes"):
        return jsonify({"success": False, "message": "请先投递 Gemini 规划分镜"}), 409
    try:
        _current_scenes(parent, require_prompts=True)
        children = enqueue_image_stage(parent)
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    return jsonify({
        "success": True,
        "message": f"已投递 {len(children)} 个 Gemini 图片任务",
        "data": {"queued": len(children), "task_ids": [child.id for child in children], "workflow": _workflow_payload(_owned_parent(current_user, job_id))},
    })


@script_video_workflow_bp.route("/jobs/<job_id>/scenes/<int:sequence>/image/retry", methods=["POST"])
@require_login
def retry_image(current_user, job_id, sequence):
    parent = _owned_parent(current_user, job_id)
    if not parent:
        return jsonify({"success": False, "message": "文案到成片任务不存在"}), 404
    if _render_is_active(parent):
        return jsonify({"success": False, "message": "视频正在对齐或合成，暂不能重试图片"}), 409
    if not _audio_is_ready(parent):
        return jsonify({"success": False, "message": "请先完成整篇 WAV"}), 409
    try:
        scene = next((item for item in _current_scenes(parent, require_prompts=True) if item["sequence"] == int(sequence)), None)
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    if not scene:
        return jsonify({"success": False, "message": "分镜不存在"}), 404
    child = _current_image_child(parent, scene)
    if not child or child.status not in {"failed", "blocked_by_login", "blocked_by_captcha", "canceled"}:
        return jsonify({"success": False, "message": "该分镜没有可重试的失败图片任务"}), 409
    try:
        children = enqueue_image_stage(parent, sequences=[sequence])
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    return jsonify({
        "success": True,
        "message": "图片任务已重新投递",
        "data": {"sequence": sequence, "task_id": children[0].id if children else None, "workflow": _workflow_payload(_owned_parent(current_user, job_id))},
    })


@script_video_workflow_bp.route("/jobs/<job_id>/render", methods=["POST"])
@require_login
def submit_render(current_user, job_id):
    parent = _owned_parent(current_user, job_id)
    if not parent:
        return jsonify({"success": False, "message": "文案到成片任务不存在"}), 404
    if _render_is_active(parent):
        return jsonify({"success": False, "message": "视频正在对齐或合成，无需重复投递"}), 409
    if not _audio_is_ready(parent):
        return jsonify({"success": False, "message": "完整 WAV 尚未就绪"}), 409
    try:
        scenes = _current_scenes(parent, require_prompts=True)
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    image_children = _image_children_for_parent(parent.id)
    missing = [
        scene["sequence"]
        for scene in scenes
        if not _has_current_image(
            parent,
            scene,
            image_children=image_children,
        )
    ]
    if missing:
        return jsonify({"success": False, "message": f"第 {missing[0]} 段图片尚未完成"}), 409

    _prepare_parent_enqueue(parent, "video_queued", 72, "图片已齐全，已投递音画对齐与 MP4 合成")
    try:
        submission = maybe_guarded_apply_async(
            render_video,
            args=[parent.id],
            queue="render",
            feature=FEATURE_NAME,
            task_id=parent.id,
            retry=False,
        )
    except Exception as exc:
        _mark_submit_failure(parent, "render_enqueue_failed", f"视频任务入队失败: {exc}")
        return jsonify({"success": False, "message": parent.error_message}), 502
    parent = _owned_parent(current_user, job_id)
    parent.celery_task_id = str(getattr(submission.get("async_result"), "id", "") or "") or None
    db.session.commit()
    return jsonify({
        "success": True,
        "message": "已投递音画对齐与 MP4 合成",
        "data": {"job_id": parent.id, "celery_id": parent.celery_task_id, "workflow": _workflow_payload(parent)},
    })


@script_video_workflow_bp.route("/jobs/<job_id>/audio/file", methods=["GET"])
@require_login
def download_audio(current_user, job_id):
    parent = _owned_parent(current_user, job_id)
    if not parent:
        return jsonify({"success": False, "message": "文案到成片任务不存在"}), 404
    path = _bounded_artifact_path(parent, _audio_payload(parent).get("wav_path"))
    if not path or not path.is_file():
        return jsonify({"success": False, "message": "完整 WAV 不存在或不在工作流目录内"}), 404
    return send_file(path, mimetype="audio/wav", as_attachment=True, download_name=f"{parent.id}.wav", conditional=True)


@script_video_workflow_bp.route("/jobs/<job_id>/audio/source", methods=["GET"])
@require_login
def stream_audio_source(current_user, job_id):
    """Stream the canonical source WAV used by the non-destructive editor."""

    parent = _owned_parent(current_user, job_id)
    if not parent:
        return jsonify({"success": False, "message": "文案到成片任务不存在"}), 404
    audio = _audio_payload(parent)
    path = _bounded_artifact_path(parent, audio.get("source_wav_path"))
    if not path or not path.is_file():
        return jsonify({"success": False, "message": "可编辑的原始 WAV 不存在"}), 404
    return send_file(path, mimetype="audio/wav", as_attachment=False, download_name=f"{parent.id}-source.wav", conditional=True)


@script_video_workflow_bp.route("/jobs/<job_id>/images/<int:sequence>", methods=["GET"])
@require_login
def download_image(current_user, job_id, sequence):
    parent = _owned_parent(current_user, job_id)
    if not parent:
        return jsonify({"success": False, "message": "文案到成片任务不存在"}), 404
    try:
        scene = next((item for item in _current_scenes(parent, require_prompts=False) if item["sequence"] == int(sequence)), None)
    except ValueError:
        scene = None
    child = _current_image_child(parent, scene) if scene else None
    path = _bounded_artifact_path(parent, _child_image_path(child))
    if not path or not path.is_file():
        return jsonify({"success": False, "message": "分镜图片不存在或不在工作流目录内"}), 404
    return send_file(path, mimetype="image/png", as_attachment=False, download_name=path.name, conditional=True)


@script_video_workflow_bp.route("/jobs/<job_id>/video/file", methods=["GET"])
@require_login
def download_video(current_user, job_id):
    parent = _owned_parent(current_user, job_id)
    if not parent:
        return jsonify({"success": False, "message": "文案到成片任务不存在"}), 404
    path = _bounded_artifact_path(parent, _video_payload(parent).get("video_path"))
    if not path or not path.is_file():
        return jsonify({"success": False, "message": "MP4 不存在或不在工作流目录内"}), 404
    return send_file(path, mimetype="video/mp4", as_attachment=True, download_name=f"{parent.id}.mp4", conditional=True)


def _owned_parent(
    current_user,
    job_id: str,
    *,
    include_detail: bool = True,
) -> PublicationTask | None:
    columns = PARENT_DETAIL_COLUMNS if include_detail else PARENT_POLL_COLUMNS
    return PublicationTask.query.options(load_only(*columns)).filter(
        PublicationTask.id == str(job_id),
        PublicationTask.feature == FEATURE_NAME,
        PublicationTask.user_id == str(getattr(current_user, "id", "") or ""),
    ).one_or_none()


def _workflow(parent: PublicationTask) -> dict[str, Any]:
    params = parent.params if isinstance(parent.params, dict) else {}
    value = params.get(FEATURE_NAME)
    if not isinstance(value, dict):
        raise ValueError("文案到成片任务参数不完整")
    return value


def _source_text(parent: PublicationTask) -> str:
    return normalize_script(_workflow(parent).get("source_text"))


def _validated_audio_chunks(value: Any, source_text: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError("audio_chunks 必须是数组")
    chunks = [str(item or "") for item in value]
    if not chunks or any(not item.strip() for item in chunks):
        raise ValueError("语音分段不能为空")
    if not audio_chunks_cover_source(chunks, source_text):
        raise ValueError("语音分段未完整覆盖原文")
    return chunks


def _current_scenes(parent: PublicationTask, *, require_prompts: bool) -> list[dict[str, Any]]:
    workflow = _workflow(parent)
    return validate_scene_rows(workflow.get("scenes") or [], _source_text(parent), require_prompts=require_prompts)


def _audio_payload(parent: PublicationTask) -> dict[str, Any]:
    result = parent.result if isinstance(parent.result, dict) else {}
    audio = result.get("audio") if isinstance(result.get("audio"), dict) else {}
    return dict(audio)


def _video_payload(parent: PublicationTask) -> dict[str, Any]:
    result = parent.result if isinstance(parent.result, dict) else {}
    video = result.get("video") if isinstance(result.get("video"), dict) else {}
    return dict(video)


def _storyboard_payload(parent: PublicationTask) -> dict[str, Any]:
    result = parent.result if isinstance(parent.result, dict) else {}
    storyboard = result.get("storyboard") if isinstance(result.get("storyboard"), dict) else {}
    if storyboard:
        return dict(storyboard)
    workflow = _workflow(parent)
    scenes = workflow.get("scenes") if isinstance(workflow.get("scenes"), list) else []
    return {
        "status": "legacy_local_draft" if scenes else "not_submitted",
        "segment_count": len(scenes),
    }


def _audio_is_ready(parent: PublicationTask) -> bool:
    payload = _audio_payload(parent)
    state = str(payload.get("state") or "")
    if state and state != "finalized":
        return False
    path = Path(str(payload.get("wav_path") or ""))
    return bool(path.is_file() and path.stat().st_size > 0)


def _audio_source_is_ready(parent: PublicationTask) -> bool:
    payload = _audio_payload(parent)
    path = _bounded_artifact_path(parent, payload.get("source_wav_path"))
    return bool(path and path.is_file() and path.stat().st_size > 0)


def _render_is_active(parent: PublicationTask) -> bool:
    result = parent.result if isinstance(parent.result, dict) else {}
    return (
        str(parent.current_step or "") in RENDER_ACTIVE_STEPS
        or str(result.get("stage") or "") in RENDER_ACTIVE_STEPS
    )


def _image_children_for_parents(
    parent_ids: list[str],
    *,
    poll_only: bool = False,
) -> dict[str, list[PublicationTask]]:
    normalized_ids = list(dict.fromkeys(
        str(parent_id).strip()
        for parent_id in parent_ids
        if str(parent_id or "").strip()
    ))
    grouped = {parent_id: [] for parent_id in normalized_ids}
    if not normalized_ids:
        return grouped
    columns = IMAGE_CHILD_POLL_COLUMNS if poll_only else IMAGE_CHILD_COLUMNS
    children = (
        PublicationTask.query.options(load_only(*columns))
        .filter(
            PublicationTask.feature == IMAGE_FEATURE_NAME,
            PublicationTask.parent_feature == FEATURE_NAME,
            PublicationTask.parent_task_id.in_(normalized_ids),
        )
        .order_by(
            PublicationTask.parent_task_id.asc(),
            PublicationTask.created_at.desc(),
        )
        .all()
    )
    for child in children:
        grouped.setdefault(str(child.parent_task_id), []).append(child)
    return grouped


def _image_children_for_parent(
    parent_id: str,
    *,
    poll_only: bool = False,
) -> list[PublicationTask]:
    normalized_id = str(parent_id or "").strip()
    if not normalized_id:
        return []
    return _image_children_for_parents(
        [normalized_id],
        poll_only=poll_only,
    ).get(normalized_id, [])


def _current_image_child(
    parent: PublicationTask,
    scene: dict[str, Any] | None,
    *,
    image_children: list[PublicationTask] | None = None,
) -> PublicationTask | None:
    if not scene:
        return None
    scheme_key = str(parent.report_visual_scheme_key or (parent.params or {}).get("visual_scheme_key") or "stick_figure")
    expected_hash = _scene_content_hash(scene, scheme_key)
    candidate_children = (
        image_children
        if image_children is not None
        else _image_children_for_parent(parent.id)
    )
    children = [
        child for child in candidate_children
        if _image_sequence(child) == int(scene["sequence"])
        and _image_scene_hash(child) == expected_hash
    ]
    return next((child for child in children if _has_usable_image(child)), None) or (children[0] if children else None)


def _has_current_image(
    parent: PublicationTask,
    scene: dict[str, Any],
    *,
    image_children: list[PublicationTask] | None = None,
) -> bool:
    child = _current_image_child(parent, scene, image_children=image_children)
    return bool(child and _has_usable_image(child))


def _child_image_path(child: PublicationTask | None) -> str:
    if not child or not isinstance(child.result, dict):
        return ""
    image = child.result.get("image") if isinstance(child.result.get("image"), dict) else {}
    return str(image.get("path") or "")


def _invalidate_changed_image_children(parent: PublicationTask, scenes: list[dict[str, Any]]) -> None:
    scheme_key = str(parent.report_visual_scheme_key or (parent.params or {}).get("visual_scheme_key") or "stick_figure")
    expected = {scene["sequence"]: _scene_content_hash(scene, scheme_key) for scene in scenes}
    for child in _image_children_for_parent(parent.id):
        if expected.get(_image_sequence(child)) == _image_scene_hash(child):
            continue
        if child.status == "canceled":
            continue
        result = dict(child.result or {})
        result["invalidated_at"] = datetime.utcnow().isoformat() + "Z"
        result["invalidated_reason"] = "分镜文案或图片提示词已变化"
        child.status = "canceled"
        child.current_step = "scene_changed"
        child.error_message = "分镜文案或图片提示词已变化，请重新提交图片"
        child.completed_at = datetime.utcnow()
        child.result = result
        flag_modified(child, "result")


def _prepare_parent_enqueue(parent: PublicationTask, step: str, progress: int, message: str) -> None:
    result = dict(parent.result or {})
    result["stage"] = step
    result["logs"] = list(result.get("logs") or [])[-79:] + [_log(step, message, progress)]
    parent.status = "pending"
    parent.current_step = step
    parent.progress = max(int(parent.progress or 0), int(progress))
    parent.error_message = None
    parent.completed_at = None
    parent.result = result
    parent.sync_projection_fields()
    flag_modified(parent, "result")
    db.session.commit()


def _mark_submit_failure(parent: PublicationTask, step: str, message: str) -> None:
    result = dict(parent.result or {})
    result["stage"] = step
    result["error"] = message
    result["logs"] = list(result.get("logs") or [])[-79:] + [_log(step, message, int(parent.progress or 0))]
    parent.status = "failed"
    parent.current_step = step
    parent.error_message = message
    parent.completed_at = datetime.utcnow()
    parent.result = result
    parent.sync_projection_fields()
    flag_modified(parent, "result")
    db.session.commit()


def _bounded_artifact_path(parent: PublicationTask, candidate: Any) -> Path | None:
    if not candidate:
        return None
    try:
        return safe_output_file(workflow_output_root(parent.id), str(candidate))
    except ValueError:
        return None


def _workflow_payload(
    parent: PublicationTask,
    *,
    image_children: list[PublicationTask] | None = None,
) -> dict[str, Any]:
    workflow = _workflow(parent)
    scheme = visual_scheme(parent.report_visual_scheme_key or workflow.get("visual_scheme_key"), require_configured=False)
    try:
        scenes = _current_scenes(parent, require_prompts=False)
    except ValueError:
        scenes = list(workflow.get("scenes") or [])
    loaded_image_children = (
        image_children
        if image_children is not None
        else _image_children_for_parent(parent.id)
    )
    scene_payloads = []
    for scene in scenes:
        expected_hash = _scene_content_hash(scene, scheme.key)
        child = _current_image_child(
            parent,
            scene,
            image_children=loaded_image_children,
        )
        image = {}
        if child and isinstance(child.result, dict):
            image = child.result.get("image") if isinstance(child.result.get("image"), dict) else {}
        scene_payloads.append({
            **scene,
            "image_scene_hash": expected_hash,
            "image_task_id": child.id if child else None,
            "image_status": child.status if child else "not_submitted",
            "image_progress": int(child.progress or 0) if child else 0,
            "image_error": child.error_message if child else None,
            "image_url": f"/api/script-video-workflows/jobs/{parent.id}/images/{scene['sequence']}" if image else None,
        })
    result = parent.result if isinstance(parent.result, dict) else {}
    return {
        "job_id": parent.id,
        "status": parent.status,
        "step": parent.current_step,
        "stage": result.get("stage") or parent.current_step,
        "progress": max(0, min(100, int(parent.progress or 0))),
        "title": parent.task_title or _source_text(parent)[:120],
        "source_text": _source_text(parent),
        "source_chars": len(_source_text(parent)),
        "audio_chunks": list(workflow.get("audio_chunks") or []),
        "scenes": scene_payloads,
        "visual_scheme": scheme.public_payload(),
        "visual_schemes": visual_scheme_payloads(),
        "audio": _audio_payload(parent),
        "storyboard": _storyboard_payload(parent),
        "images": result.get("images") if isinstance(result.get("images"), dict) else {},
        "alignment": result.get("alignment") if isinstance(result.get("alignment"), dict) else None,
        "video": _video_payload(parent),
        "error": parent.error_message,
        "waiting_child": {
            "label": parent.waiting_child_label,
            "id": parent.waiting_child_id,
            "status": parent.waiting_child_status,
            "step": parent.waiting_child_step,
        },
        "created_at": _iso(parent.created_at),
        "updated_at": _iso(parent.updated_at),
        "completed_at": _iso(parent.completed_at),
        "logs": list(result.get("logs") or [])[-30:],
    }


def _image_poll_identity(child: PublicationTask) -> tuple[int, str]:
    """Read the immutable scene identity without loading a child's params JSON."""
    result = child.result if isinstance(child.result, dict) else {}
    metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
    image = result.get("image") if isinstance(result.get("image"), dict) else {}
    try:
        sequence = int(metadata.get("sequence") or image.get("sequence") or 0)
    except (TypeError, ValueError):
        sequence = 0
    scene_hash = str(metadata.get("scene_hash") or image.get("scene_hash") or "")
    return sequence, scene_hash


def _workflow_poll_payload(
    parent: PublicationTask,
    *,
    image_children: list[PublicationTask],
) -> dict[str, Any]:
    """Return mutable workflow state while keeping the large params JSON unloaded."""
    selected_children: dict[tuple[int, str], PublicationTask] = {}
    for child in image_children:
        identity = _image_poll_identity(child)
        if identity[0] <= 0 or not identity[1]:
            continue
        selected = selected_children.get(identity)
        if selected is None or (
            _has_usable_image(child) and not _has_usable_image(selected)
        ):
            selected_children[identity] = child

    scene_updates = []
    for (sequence, scene_hash), child in sorted(selected_children.items()):
        result = child.result if isinstance(child.result, dict) else {}
        image = result.get("image") if isinstance(result.get("image"), dict) else {}
        scene_updates.append({
            "sequence": sequence,
            "image_scene_hash": scene_hash,
            "image_task_id": child.id,
            "image_status": child.status,
            "image_progress": int(child.progress or 0),
            "image_error": child.error_message,
            "image_url": (
                f"/api/script-video-workflows/jobs/{parent.id}/images/{sequence}"
                if image else None
            ),
        })

    result = parent.result if isinstance(parent.result, dict) else {}
    payload = {
        "partial": True,
        "job_id": parent.id,
        "status": parent.status,
        "step": parent.current_step,
        "stage": result.get("stage") or parent.current_step,
        "progress": max(0, min(100, int(parent.progress or 0))),
        "audio": dict(result.get("audio")) if isinstance(result.get("audio"), dict) else {},
        "images": dict(result.get("images")) if isinstance(result.get("images"), dict) else {},
        "alignment": dict(result.get("alignment")) if isinstance(result.get("alignment"), dict) else None,
        "video": dict(result.get("video")) if isinstance(result.get("video"), dict) else {},
        "error": parent.error_message,
        "waiting_child": {
            "label": parent.waiting_child_label,
            "id": parent.waiting_child_id,
            "status": parent.waiting_child_status,
            "step": parent.waiting_child_step,
        },
        "updated_at": _iso(parent.updated_at),
        "completed_at": _iso(parent.completed_at),
        "logs": list(result.get("logs") or [])[-30:],
        "scene_updates": scene_updates,
    }
    if isinstance(result.get("storyboard"), dict):
        payload["storyboard"] = dict(result["storyboard"])
    return payload


def _workflow_summary_payload(parent: PublicationTask) -> dict[str, Any]:
    return {
        "job_id": parent.id,
        "status": parent.status,
        "step": parent.current_step,
        "stage": parent.current_step,
        "progress": max(0, min(100, int(parent.progress or 0))),
        "title": parent.task_title or "未命名文案",
        "error": parent.error_message,
        "created_at": _iso(parent.created_at),
        "updated_at": _iso(parent.updated_at),
        "completed_at": _iso(parent.completed_at),
    }


def _log(step: str, message: str, progress: int) -> dict[str, Any]:
    return {
        "step": step,
        "message": message,
        "progress": int(progress),
        "updated_at": datetime.utcnow().isoformat() + "Z",
    }


def _iso(value) -> str | None:
    return value.isoformat() + "Z" if value else None


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


__all__ = ["script_video_workflow_bp"]
