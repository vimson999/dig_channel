"""Formal task API for the mixed-media report-video director workflow."""

from __future__ import annotations

from datetime import datetime

from flask import Blueprint, jsonify, request
from sqlalchemy import or_
from sqlalchemy.orm.attributes import flag_modified

from api.report_script_material_workflow import (
    MATERIAL_STATUS_FEATURES,
    _material_task_payload,
    _pdf_meta_from_zsxq_attachment,
    _visible_user_ids,
)
from models import db
from models.task import PublicationTask
from services.report_video_director_workflow import (
    DirectorWorkflowError,
    attach_workflow_alignment,
    build_workflow_dispatch_bundle,
    create_workflow_state,
    record_workflow_human_review,
    record_workflow_previews,
)
from services.report_video_director_enrichment import (
    derive_verified_pdf_evidence_map,
)
from utils.auth_utils import require_login


FEATURE_NAME = "report_video_director_workflow"
report_video_director_workflow_bp = Blueprint(
    FEATURE_NAME,
    __name__,
    url_prefix="/api/report-video-director-workflows",
)


@report_video_director_workflow_bp.route("/status", methods=["GET"])
def status():
    return jsonify(
        {
            "success": True,
            "data": {
                "feature": FEATURE_NAME,
                "production_human_gate": "required",
                "test_auto_gate": "isolated_only",
                "publish_allowed": False,
                "existing_production_task_mutation": False,
            },
        }
    )


@report_video_director_workflow_bp.route("/jobs", methods=["POST"])
@require_login
def create_job(current_user):
    data = request.get_json(silent=True) or {}
    material_job_id = str(data.get("material_job_id") or "").strip()
    if not material_job_id:
        return jsonify({"success": False, "message": "请提供 material_job_id"}), 400
    mode = str(data.get("execution_mode") or "production").strip().lower()
    if mode not in {"production", "test"}:
        return jsonify({"success": False, "message": "execution_mode 不受支持"}), 400
    material_task = _material_task(material_job_id, current_user)
    if not material_task:
        return jsonify({"success": False, "message": "完整研报文案任务不存在"}), 404
    evidence_map = _evidence_map(data, material_task)
    try:
        pdf_meta = _pdf_meta_from_zsxq_attachment(
            material_task.report_audio_pdf_attachment_id
        )
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    material = _material_task_payload(material_task)
    material["pdf"] = pdf_meta
    material["audio_task_id"] = str(material_task.id)
    material["audio_path"] = material_task.report_audio_wav_path
    evidence_source = "existing_upstream_mapping"
    if not evidence_map:
        try:
            evidence_map = derive_verified_pdf_evidence_map(
                material,
                pdf_path=pdf_meta["path"],
                target_segment_chars=int(data.get("target_segment_chars") or 130),
            )
            evidence_source = "verified_real_pdf_text_fallback"
        except ValueError as exc:
            return jsonify(
                {
                    "success": False,
                    "message": (
                        "缺少已有 PDF 证据映射，且真实 PDF 文本回退失败："
                        f"{exc}"
                    ),
                }
            ), 400

    task = PublicationTask(
        status="processing",
        current_step="building_director_package",
        progress=5,
        params={
            "feature": FEATURE_NAME,
            "user_id": str(getattr(current_user, "id", "") or ""),
            "source_task_id": str(material_task.id),
            "source_feature": material_task.feature,
            FEATURE_NAME: {
                "execution_mode": mode,
                "evidence_source": evidence_source,
                "publish_allowed": False,
            },
        },
        result={},
    )
    task.task_title = (
        material_task.task_title
        or material_task.report_audio_pdf_topic_title
        or material_task.report_audio_pdf_filename
        or "研报成片总控"
    )
    task.report_audio_pdf_attachment_id = (
        material_task.report_audio_pdf_attachment_id
    )
    task.report_audio_pdf_filename = material_task.report_audio_pdf_filename
    task.report_audio_pdf_topic_title = (
        material_task.report_audio_pdf_topic_title
    )
    db.session.add(task)
    db.session.flush()
    try:
        state = create_workflow_state(
            material,
            evidence_map,
            pdf_path=pdf_meta["path"],
            execution_mode=mode,
            workflow_task_id=str(task.id),
            target_segment_chars=int(data.get("target_segment_chars") or 130),
        )
        _persist_state(task, state)
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        return jsonify({"success": False, "message": str(exc)}), 400
    return jsonify(
        {
            "success": True,
            "job_id": task.id,
            "status": task.status,
            "step": task.current_step,
            "publish_allowed": False,
        }
    )


@report_video_director_workflow_bp.route("/jobs/<job_id>", methods=["GET"])
@require_login
def get_job(current_user, job_id):
    task = _workflow_task(job_id, current_user)
    if not task:
        return jsonify({"success": False, "message": "总控任务不存在"}), 404
    return jsonify({"success": True, "data": _task_payload(task)})


@report_video_director_workflow_bp.route(
    "/jobs/<job_id>/audio-alignment",
    methods=["POST"],
)
@require_login
def attach_alignment(current_user, job_id):
    task = _workflow_task(job_id, current_user)
    if not task:
        return jsonify({"success": False, "message": "总控任务不存在"}), 404
    data = request.get_json(silent=True) or {}
    alignment = data.get("alignment")
    if not isinstance(alignment, dict):
        return jsonify({"success": False, "message": "alignment 必须是对象"}), 400
    source = PublicationTask.query.filter(
        PublicationTask.id == task.source_task_id
    ).first()
    audio_path = str(
        data.get("audio_path")
        or (source.report_audio_wav_path if source else "")
        or ""
    )
    try:
        audio_seconds = float(
            data.get("audio_seconds")
            or (source.report_audio_seconds if source else 0)
            or 0
        )
        state = attach_workflow_alignment(
            task.result,
            alignment=alignment,
            audio_path=audio_path,
            audio_seconds=audio_seconds,
        )
        _persist_state(task, state)
        db.session.commit()
    except (DirectorWorkflowError, ValueError) as exc:
        db.session.rollback()
        return jsonify({"success": False, "message": str(exc)}), 400
    return jsonify({"success": True, "data": _task_payload(task)})


@report_video_director_workflow_bp.route(
    "/jobs/<job_id>/previews",
    methods=["POST"],
)
@require_login
def record_previews(current_user, job_id):
    task = _workflow_task(job_id, current_user)
    if not task:
        return jsonify({"success": False, "message": "总控任务不存在"}), 404
    data = request.get_json(silent=True) or {}
    artifacts = data.get("preview_artifacts")
    if not isinstance(artifacts, dict):
        return jsonify({"success": False, "message": "preview_artifacts 必须是对象"}), 400
    try:
        state = record_workflow_previews(
            task.result,
            preview_artifacts=artifacts,
            test_authorization=data.get("test_authorization"),
        )
        _persist_state(task, state)
        db.session.commit()
    except (DirectorWorkflowError, ValueError) as exc:
        db.session.rollback()
        return jsonify({"success": False, "message": str(exc)}), 400
    return jsonify({"success": True, "data": _task_payload(task)})


@report_video_director_workflow_bp.route(
    "/jobs/<job_id>/review",
    methods=["POST"],
)
@require_login
def review(current_user, job_id):
    task = _workflow_task(job_id, current_user)
    if not task:
        return jsonify({"success": False, "message": "总控任务不存在"}), 404
    data = request.get_json(silent=True) or {}
    try:
        state = record_workflow_human_review(
            task.result,
            reviewer_id=str(getattr(current_user, "id", "") or ""),
            decision=data.get("decision"),
            notes=data.get("notes") or "",
        )
        _persist_state(task, state)
        db.session.commit()
    except (DirectorWorkflowError, ValueError) as exc:
        db.session.rollback()
        return jsonify({"success": False, "message": str(exc)}), 400
    return jsonify({"success": True, "data": _task_payload(task)})


@report_video_director_workflow_bp.route(
    "/jobs/<job_id>/dispatch-bundle",
    methods=["POST"],
)
@require_login
def create_dispatch_bundle(current_user, job_id):
    """Create an authorized bundle; actual child-task enqueue is separate."""

    task = _workflow_task(job_id, current_user)
    if not task:
        return jsonify({"success": False, "message": "总控任务不存在"}), 404
    try:
        state = build_workflow_dispatch_bundle(task.result)
        _persist_state(task, state)
        db.session.commit()
    except DirectorWorkflowError as exc:
        db.session.rollback()
        return jsonify({"success": False, "message": str(exc)}), 409
    return jsonify({"success": True, "data": _task_payload(task)})


def _material_task(job_id, current_user):
    return (
        PublicationTask.query.filter(PublicationTask.id == str(job_id))
        .filter(PublicationTask.feature.in_(MATERIAL_STATUS_FEATURES))
        .filter(PublicationTask.status == "completed")
        .filter(PublicationTask.final_script_chars > 0)
        .filter(
            or_(
                PublicationTask.user_id.in_(_visible_user_ids(current_user)),
                PublicationTask.user_id.is_(None),
            )
        )
        .first()
    )


def _workflow_task(job_id, current_user):
    return (
        PublicationTask.query.filter(
            PublicationTask.id == str(job_id),
            PublicationTask.feature == FEATURE_NAME,
        )
        .filter(
            or_(
                PublicationTask.user_id.in_(_visible_user_ids(current_user)),
                PublicationTask.user_id.is_(None),
            )
        )
        .first()
    )


def _evidence_map(data, material_task):
    rows = data.get("evidence_map")
    if isinstance(rows, list):
        return rows
    result = (
        material_task.result if isinstance(material_task.result, dict) else {}
    )
    for candidate in (
        result.get("evidence_map"),
        result.get("pdf_evidence_map"),
        (
            result.get("structured_result", {}).get("evidence_map")
            if isinstance(result.get("structured_result"), dict)
            else None
        ),
    ):
        if isinstance(candidate, list) and candidate:
            return candidate
    return []


def _persist_state(task, state):
    task.result = state
    task.status = state.get("status") or "processing"
    task.current_step = state.get("current_step") or "processing"
    task.progress = _progress(task.current_step)
    if task.status in {"completed", "failed", "canceled"}:
        task.completed_at = datetime.utcnow()
    flag_modified(task, "result")


def _progress(step):
    return {
        "awaiting_audio_alignment": 25,
        "awaiting_previews": 45,
        "awaiting_human_review": 60,
        "full_render_authorized": 75,
        "dispatch_ready": 80,
        "revision_required": 50,
        "terminated": 100,
    }.get(str(step or ""), 10)


def _task_payload(task):
    return {
        "job_id": str(task.id),
        "source_task_id": task.source_task_id,
        "status": task.status,
        "step": task.current_step,
        "progress": task.progress or 0,
        "title": task.task_title,
        "result": task.result,
        "publish_allowed": False,
        "created_at": (
            task.created_at.isoformat() + "Z" if task.created_at else None
        ),
        "updated_at": (
            task.updated_at.isoformat() + "Z" if task.updated_at else None
        ),
    }
