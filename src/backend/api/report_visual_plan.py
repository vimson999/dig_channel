from datetime import datetime
from pathlib import Path

from flask import Blueprint, jsonify, request, send_file
from sqlalchemy import or_
from sqlalchemy.orm import load_only
from sqlalchemy.orm.attributes import flag_modified

from api.report_script_material_workflow import (
    MATERIAL_STATUS_FEATURES,
    _latest_material_task_for_asset,
    _material_task_payload,
    _pdf_meta_from_zsxq_attachment,
    _request_payload,
    _resolve_pdf_path,
    _visible_user_ids,
)
from models import db
from models.task import PublicationTask
from services.report_visual_plan_service import (
    FEATURE_NAME,
    OUTPUT_ROOT,
    SCHEMA_VERSION,
    build_visual_plan_preview,
    provider_status,
)
from utils.auth_utils import require_login


report_visual_plan_bp = Blueprint(
    "report_visual_plan",
    __name__,
    url_prefix="/api/report-visual-plans",
)


@report_visual_plan_bp.route("/status", methods=["GET"])
@require_login
def status(_current_user):
    return jsonify({"success": True, "data": {"provider": provider_status()}})


@report_visual_plan_bp.route("/preview", methods=["POST"])
@require_login
def create_preview(current_user):
    data = _request_payload()
    force = _truthy(data.get("force") or data.get("refresh"), default=False)
    try:
        material_task, material_payload, pdf_path = _resolve_material_input(data, current_user)
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400

    if not force:
        existing = _latest_visual_plan_task(material_task.id, current_user)
        if existing:
            payload = _job_payload(existing, include_detail=True)
            return jsonify({
                "success": True,
                "cached": True,
                "job_id": payload.get("job_id"),
                "status": payload.get("status"),
                "data": payload,
            })

    task = _create_visual_plan_task(material_task, material_payload, current_user)
    db.session.add(task)
    db.session.commit()
    try:
        task.progress = 25
        task.current_step = "generate_visual_plan"
        db.session.commit()

        result = build_visual_plan_preview(
            material_payload,
            pdf_path=str(pdf_path) if pdf_path else None,
            job_id=task.id,
        )
        result["schema_version"] = SCHEMA_VERSION
        result["source_task_id"] = material_task.id
        result["source_feature"] = material_task.feature
        result["material"] = _material_summary(material_payload)
        task.status = "completed"
        task.current_step = "done"
        task.progress = 100
        task.completed_at = datetime.utcnow()
        task.result = result
        task.task_title = _visual_task_title(material_payload)
        task.report_audio_output_path = _first_asset_path(result)
        flag_modified(task, "result")
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        task = (
            PublicationTask.query.options(load_only(
                PublicationTask.id,
                PublicationTask.status,
                PublicationTask.current_step,
                PublicationTask.progress,
                PublicationTask.result,
                PublicationTask.error_message,
            ))
            .filter(PublicationTask.id == task.id)
            .first()
        )
        if task:
            task.status = "failed"
            task.current_step = "failed"
            task.progress = max(task.progress or 0, 5)
            task.error_message = str(exc)
            task.result = {**(task.result or {}), "error": str(exc), "failed_at": datetime.utcnow().isoformat() + "Z"}
            flag_modified(task, "result")
            db.session.commit()
        return jsonify({"success": False, "message": f"visual_plan 生成失败：{exc}"}), 500

    payload = _job_payload(task, include_detail=True)
    return jsonify({
        "success": True,
        "cached": False,
        "job_id": task.id,
        "status": payload.get("status"),
        "data": payload,
    })


@report_visual_plan_bp.route("/jobs/<job_id>", methods=["GET"])
@require_login
def get_job(current_user, job_id):
    task = _get_owned_visual_task(current_user, job_id, include_result=True)
    if not task:
        return jsonify({"success": False, "message": "visual_plan 任务不存在"}), 404
    payload = _job_payload(task, include_detail=True)
    return jsonify({"success": True, "job_id": task.id, "status": payload.get("status"), "data": payload})


@report_visual_plan_bp.route("/jobs/<job_id>/asset/<path:filename>", methods=["GET"])
@require_login
def get_asset(current_user, job_id, filename):
    task = _get_owned_visual_task(current_user, job_id, include_result=True)
    if not task:
        return jsonify({"success": False, "message": "visual_plan 任务不存在"}), 404
    safe_name = Path(filename).name
    path = (OUTPUT_ROOT / task.id / safe_name).resolve()
    root = (OUTPUT_ROOT / task.id).resolve()
    if root not in path.parents and path != root:
        return jsonify({"success": False, "message": "非法文件路径"}), 400
    if not path.exists() or not path.is_file():
        return jsonify({"success": False, "message": "预览文件不存在"}), 404
    return send_file(path, as_attachment=False)


def _resolve_material_input(data, current_user):
    material_job_id = str(data.get("material_job_id") or data.get("job_id") or "").strip()
    asset_id = str(data.get("asset_id") or data.get("pdf_asset_id") or "").strip()
    material_task = None
    if material_job_id:
        material_task = (
            PublicationTask.query.options(load_only(PublicationTask.id, PublicationTask.feature, PublicationTask.user_id, PublicationTask.params, PublicationTask.result, PublicationTask.status))
            .filter(PublicationTask.id == material_job_id)
            .filter(PublicationTask.feature.in_(MATERIAL_STATUS_FEATURES))
            .filter(or_(PublicationTask.user_id.in_(_visible_user_ids(current_user)), PublicationTask.user_id.is_(None)))
            .first()
        )
    elif asset_id:
        material_task = _latest_material_task_for_asset(asset_id, current_user=current_user, include_result=True)
    else:
        raise ValueError("请提供 asset_id 或 material_job_id")

    if not material_task:
        raise ValueError("这份 PDF 暂无可复用的基础文案")

    material_payload = _material_task_payload(material_task)
    if not str(material_payload.get("final_script_for_tts") or "").strip():
        raise ValueError("基础文案任务还没有 final_script_for_tts")

    pdf_path = _pdf_path_from_material_task(material_task)
    if not pdf_path and asset_id:
        try:
            pdf_meta = _pdf_meta_from_zsxq_attachment(asset_id)
            pdf_path = Path(pdf_meta["path"])
            material_payload.setdefault("pdf", {}).update({key: value for key, value in pdf_meta.items() if key != "path"})
        except ValueError:
            pdf_path = None
    return material_task, material_payload, pdf_path


def _pdf_path_from_material_task(task):
    candidates = []
    for payload in (task.params if isinstance(task.params, dict) else {}, task.result if isinstance(task.result, dict) else {}):
        for key in (task.feature, "report_script_material_workflow", "report_audio_workflow", "report_pdf_audio_workflow"):
            value = payload.get(key) if isinstance(payload.get(key), dict) else None
            pdf = value.get("pdf") if isinstance(value, dict) and isinstance(value.get("pdf"), dict) else None
            if pdf:
                candidates.append(pdf.get("path") or pdf.get("local_path"))
        pdf = payload.get("pdf") if isinstance(payload.get("pdf"), dict) else None
        if pdf:
            candidates.append(pdf.get("path") or pdf.get("local_path"))
    for candidate in candidates:
        path = _resolve_pdf_path(candidate)
        if path:
            return path
    return None


def _create_visual_plan_task(material_task, material_payload, current_user):
    user_id = str(getattr(current_user, "id", "") or "")
    pdf = material_payload.get("pdf") if isinstance(material_payload.get("pdf"), dict) else {}
    params = {
        "feature": FEATURE_NAME,
        "user_id": user_id,
        "source_task_id": material_task.id,
        "source_feature": material_task.feature,
        FEATURE_NAME: {
            "material_task_id": material_task.id,
            "material_feature": material_task.feature,
            "pdf": {key: pdf.get(key) for key in ("attachment_id", "filename", "topic_title", "tab_label", "preview_url") if pdf.get(key) is not None},
        },
    }
    task = PublicationTask(
        status="processing",
        current_step="init",
        progress=5,
        params=params,
        result={"schema_version": SCHEMA_VERSION, "visual_plan": {"status": "pending", "schema_version": SCHEMA_VERSION}},
    )
    task.task_title = _visual_task_title(material_payload)
    task.report_audio_pdf_attachment_id = pdf.get("attachment_id")
    task.report_audio_pdf_filename = pdf.get("filename")
    task.report_audio_pdf_topic_title = pdf.get("topic_title")
    return task


def _latest_visual_plan_task(material_task_id, current_user):
    candidates = (
        PublicationTask.query.options(load_only(PublicationTask.id, PublicationTask.status, PublicationTask.result, PublicationTask.params, PublicationTask.created_at, PublicationTask.completed_at, PublicationTask.task_title, PublicationTask.progress, PublicationTask.current_step, PublicationTask.error_message, PublicationTask.report_audio_pdf_filename, PublicationTask.report_audio_pdf_topic_title))
        .filter(PublicationTask.feature == FEATURE_NAME)
        .filter(PublicationTask.source_task_id == material_task_id)
        .filter(PublicationTask.status == "completed")
        .filter(or_(PublicationTask.user_id.in_(_visible_user_ids(current_user)), PublicationTask.user_id.is_(None)))
        .order_by(PublicationTask.completed_at.desc().nullslast(), PublicationTask.created_at.desc())
        .limit(10)
        .all()
    )
    for task in candidates:
        if _matches_schema_version(task.result):
            return task
    return None


def _get_owned_visual_task(current_user, job_id, include_result=False):
    columns = [
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
        PublicationTask.report_audio_pdf_topic_title,
        PublicationTask.report_audio_output_path,
        PublicationTask.created_at,
        PublicationTask.updated_at,
        PublicationTask.completed_at,
        PublicationTask.params,
    ]
    if include_result:
        columns.append(PublicationTask.result)
    return (
        PublicationTask.query.options(load_only(*columns))
        .filter(PublicationTask.id == str(job_id))
        .filter(PublicationTask.feature == FEATURE_NAME)
        .filter(or_(PublicationTask.user_id.in_(_visible_user_ids(current_user)), PublicationTask.user_id.is_(None)))
        .first()
    )


def _job_payload(task, include_detail=False):
    result = task.result if isinstance(task.result, dict) else {}
    params = task.params if isinstance(task.params, dict) else {}
    assets = []
    for asset in result.get("preview_assets") or []:
        if not isinstance(asset, dict):
            continue
        filename = Path(str(asset.get("filename") or "")).name
        if not filename:
            continue
        assets.append({
            **asset,
            "url": f"/api/report-visual-plans/jobs/{task.id}/asset/{filename}",
        })
    payload = {
        "job_id": task.id,
        "status": _status_label(task.status),
        "step": task.current_step,
        "progress": task.progress or 0,
        "title": task.task_title or task.report_audio_pdf_topic_title or task.report_audio_pdf_filename or "研报视觉编排",
        "source_task_id": task.source_task_id,
        "source_feature": task.source_feature,
        "created_at": _isoformat_z(task.created_at),
        "updated_at": _isoformat_z(task.updated_at),
        "completed_at": _isoformat_z(task.completed_at),
        "error": task.error_message or result.get("error"),
        "provider": result.get("provider"),
        "provider_error": result.get("provider_error"),
        "schema_version": result.get("schema_version") or (result.get("visual_plan") or {}).get("schema_version"),
        "preview_assets": assets,
        "material": result.get("material") or ((params.get(FEATURE_NAME) or {}) if isinstance(params.get(FEATURE_NAME), dict) else {}),
    }
    if include_detail:
        payload["visual_plan"] = result.get("visual_plan") if isinstance(result.get("visual_plan"), dict) else None
        payload["result"] = result
    return payload


def _material_summary(material):
    pdf = material.get("pdf") if isinstance(material.get("pdf"), dict) else {}
    return {
        "title": material.get("title"),
        "script_chars": material.get("script_chars") or len(str(material.get("final_script_for_tts") or "")),
        "pdf": {key: pdf.get(key) for key in ("attachment_id", "filename", "topic_title", "tab_label", "preview_url") if pdf.get(key) is not None},
    }


def _visual_task_title(material):
    pdf = material.get("pdf") if isinstance(material.get("pdf"), dict) else {}
    return str(material.get("title") or pdf.get("topic_title") or pdf.get("filename") or "研报视觉编排").strip()[:500]


def _first_asset_path(result):
    for asset in result.get("preview_assets") or []:
        if isinstance(asset, dict) and asset.get("path"):
            return str(asset.get("path"))[:600]
    return None


def _matches_schema_version(result):
    if not isinstance(result, dict):
        return False
    if result.get("schema_version") == SCHEMA_VERSION:
        return True
    plan = result.get("visual_plan") if isinstance(result.get("visual_plan"), dict) else {}
    return plan.get("schema_version") == SCHEMA_VERSION


def _status_label(status):
    return {
        "pending": "queued",
        "processing": "running",
        "completed": "succeeded",
        "failed": "failed",
        "canceled": "canceled",
    }.get(status or "", status or "queued")


def _truthy(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled", "y"}


def _isoformat_z(value):
    return value.isoformat() + "Z" if hasattr(value, "isoformat") else value
