import json
import os
from datetime import datetime
from pathlib import Path

from flask import Blueprint, jsonify, request, send_file
from sqlalchemy import or_
from sqlalchemy.orm.attributes import flag_modified
from werkzeug.utils import secure_filename

from celery_config import celery_app
from models import db
from models.task import PublicationTask
from models.zsxq_sync import ZsxqAttachment, ZsxqTopic
from services import doubao_browser_tts, gemini_browser
from services.report_audio_workflow import (
    DEFAULT_INITIAL_PROMPT,
    DEFAULT_REVIEW_PROMPT,
    FEATURE_NAME,
    QUEUE_NAME,
    status_payload as workflow_status_payload,
)
from tasks.report_audio_workflow import process_report_audio_workflow_job
from utils.auth_utils import require_login


report_audio_workflow_bp = Blueprint("report_audio_workflow", __name__, url_prefix="/api/report-audio-workflows")
PROJECT_ROOT = Path(__file__).resolve().parents[3]

STATUS_MAP = {
    "pending": "queued",
    "processing": "running",
    "completed": "succeeded",
    "failed": "failed",
    "canceled": "canceled",
}


@report_audio_workflow_bp.route("/status", methods=["GET"])
@require_login
def service_status(_current_user):
    return jsonify({
        "success": True,
        "data": {
            "provider": {
                "id": FEATURE_NAME,
                "name": "研报 PDF 到豆包浏览器语音编排",
                **workflow_status_payload(),
            },
            "report_pdf_gemini": gemini_browser.status_payload(),
            "gemini_browser": gemini_browser.status_payload(),
            "doubao_browser_tts": doubao_browser_tts.status_payload(),
        },
    })


@report_audio_workflow_bp.route("/pdf-assets", methods=["GET"])
@require_login
def list_pdf_assets(_current_user):
    page = _bounded_int(request.args.get("page"), 1, 1, 100000)
    per_page = _bounded_int(request.args.get("per_page"), 30, 1, 100)
    q = (request.args.get("q") or "").strip()
    group_id = (request.args.get("group_id") or "").strip()
    tab_id = (request.args.get("tab_id") or "").strip()

    query = (
        db.session.query(ZsxqAttachment, ZsxqTopic)
        .outerjoin(
            ZsxqTopic,
            (ZsxqTopic.group_id == ZsxqAttachment.group_id)
            & (ZsxqTopic.tab_id == ZsxqAttachment.tab_id)
            & (ZsxqTopic.topic_id == ZsxqAttachment.topic_id),
        )
        .filter(ZsxqAttachment.download_status == "downloaded")
        .filter(ZsxqAttachment.local_path.isnot(None))
        .filter(or_(ZsxqAttachment.name.ilike("%.pdf"), ZsxqAttachment.local_path.ilike("%.pdf")))
    )
    if group_id:
        query = query.filter(ZsxqAttachment.group_id == group_id)
    if tab_id:
        query = query.filter(ZsxqAttachment.tab_id == tab_id)
    if q:
        pattern = f"%{q}%"
        query = query.filter(or_(
            ZsxqAttachment.name.ilike(pattern),
            ZsxqTopic.title.ilike(pattern),
            ZsxqTopic.content.ilike(pattern),
        ))

    scan_limit = min(max(page * per_page * 10, 200), 2000)
    rows = (
        query
        .order_by(ZsxqAttachment.updated_at.desc(), ZsxqAttachment.created_at.desc())
        .limit(scan_limit)
        .all()
    )
    valid_items = []
    skipped_missing = 0
    for attachment, topic in rows:
        item = _pdf_asset_payload(attachment, topic)
        if item:
            valid_items.append(item)
        else:
            skipped_missing += 1

    start = (page - 1) * per_page
    end = start + per_page
    items = valid_items[start:end]
    has_more = len(valid_items) > end or len(rows) == scan_limit
    return jsonify({
        "success": True,
        "data": {
            "items": items,
            "pagination": {
                "page": page,
                "per_page": per_page,
                "has_more": has_more,
                "skipped_missing": skipped_missing,
            },
        },
        "items": items,
    })


@report_audio_workflow_bp.route("/pdf-assets/<asset_id>/preview", methods=["GET"])
@require_login
def preview_pdf_asset(_current_user, asset_id):
    try:
        pdf = _pdf_meta_from_zsxq_attachment(asset_id, None)
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 404

    return send_file(
        pdf["path"],
        mimetype="application/pdf",
        as_attachment=False,
        download_name=pdf.get("filename") or f"{asset_id}.pdf",
    )


@report_audio_workflow_bp.route("/jobs", methods=["POST"])
@require_login
def create_job(current_user):
    data = _request_payload()
    user_id = str(getattr(current_user, "id", None))
    pdf_file = request.files.get("pdf") or request.files.get("file")
    pdf_asset_id = (data.get("pdf_asset_id") or data.get("pdf_attachment_id") or "").strip()
    if not pdf_file and not pdf_asset_id:
        return jsonify({"success": False, "message": "请上传 PDF 或选择一个已同步 PDF"}), 400

    if pdf_file:
        pdf_error = _validate_pdf_upload(pdf_file)
        if pdf_error:
            return jsonify({"success": False, "message": pdf_error}), 400
    else:
        asset_error = _validate_pdf_asset_id(pdf_asset_id)
        if asset_error:
            return jsonify({"success": False, "message": asset_error}), 400

    metadata = data.get("metadata") or {}
    if not isinstance(metadata, dict):
        return jsonify({"success": False, "message": "metadata 必须是对象"}), 400

    prompt = (data.get("prompt") or DEFAULT_INITIAL_PROMPT).strip()
    review_prompt = (data.get("review_prompt") or DEFAULT_REVIEW_PROMPT).strip()
    followups = _parse_followups(data)
    video_options = _parse_video_options(data)
    gemini_url = (data.get("gemini_url") or data.get("url") or "").strip()
    if gemini_url and not gemini_url.startswith(("http://", "https://")):
        return jsonify({"success": False, "message": "gemini_url 必须是 http(s) 地址"}), 400

    gemini_status = gemini_browser.status_payload()
    doubao_status = doubao_browser_tts.status_payload()
    if not gemini_status.get("configured"):
        return jsonify({"success": False, "message": "研报 PDF Gem 解析未配置", "data": {"report_pdf_gemini": gemini_status}}), 400
    if not doubao_status.get("configured"):
        return jsonify({"success": False, "message": "豆包浏览器朗读未配置", "data": {"doubao_browser_tts": doubao_status}}), 400

    task = PublicationTask(
        status="pending",
        current_step="queued",
        progress=0,
        params={
            "feature": FEATURE_NAME,
            "user_id": user_id,
            "report_audio_workflow": {
                "prompt": prompt,
                "review_prompt": review_prompt,
                "followups": followups,
                "gemini_url": gemini_url,
                "metadata": metadata,
                "video": video_options,
                "pdf": None,
                "pdf_path": None,
            },
        },
        result={
            "metadata": metadata,
            "pdf": None,
            "children": {},
            "structured_result": None,
            "audio": None,
            "video": None,
            "logs": [],
        },
    )
    db.session.add(task)
    db.session.flush()

    try:
        if pdf_file:
            pdf_meta = _save_pdf_upload(pdf_file, task.id)
        else:
            pdf_meta = _pdf_meta_from_zsxq_attachment(pdf_asset_id, task.id)
    except ValueError as exc:
        db.session.rollback()
        return jsonify({"success": False, "message": str(exc)}), 400
    except OSError as exc:
        db.session.rollback()
        return jsonify({"success": False, "message": f"PDF 保存失败: {exc}"}), 500

    params = dict(task.params or {})
    payload = dict(params.get("report_audio_workflow") or {})
    payload["pdf"] = pdf_meta
    payload["pdf_path"] = pdf_meta["path"]
    params["report_audio_workflow"] = payload
    task.params = params

    result = dict(task.result or {})
    result["pdf"] = pdf_meta
    task.result = result
    flag_modified(task, "params")
    flag_modified(task, "result")
    db.session.commit()

    try:
        celery_task = process_report_audio_workflow_job.apply_async(args=[task.id], queue=QUEUE_NAME, retry=False)
    except Exception as exc:
        task.status = "failed"
        task.current_step = "failed"
        task.error_message = f"编排任务入队失败: {exc}"
        task.completed_at = datetime.utcnow()
        db.session.commit()
        return jsonify({"success": False, "message": task.error_message}), 502

    task.celery_task_id = celery_task.id
    db.session.commit()

    return jsonify({
        "success": True,
        "job_id": task.id,
        "status": "queued",
        "data": {"job_id": task.id, "status": "queued", "celery_id": celery_task.id},
    })


@report_audio_workflow_bp.route("/jobs", methods=["GET"])
@require_login
def list_jobs(current_user):
    page = _bounded_int(request.args.get("page"), 1, 1, 100000)
    per_page = _bounded_int(request.args.get("per_page"), 20, 1, 100)
    status = (request.args.get("status") or "").strip()

    query = PublicationTask.query.filter(
        PublicationTask.params["feature"].as_string() == FEATURE_NAME,
        PublicationTask.params["user_id"].as_string() == str(getattr(current_user, "id", None)),
    )
    if status:
        reverse_status = {value: key for key, value in STATUS_MAP.items()}
        query = query.filter(PublicationTask.status == reverse_status.get(status, status))

    pagination = query.order_by(PublicationTask.created_at.desc()).paginate(
        page=page,
        per_page=per_page,
        error_out=False,
    )
    items = [_job_payload(task) for task in pagination.items]
    return jsonify({
        "success": True,
        "data": {
            "items": items,
            "pagination": {
                "page": pagination.page,
                "per_page": per_page,
                "total": pagination.total,
                "pages": pagination.pages,
            },
        },
        "items": items,
    })


@report_audio_workflow_bp.route("/jobs/<job_id>", methods=["GET"])
@require_login
def get_job(current_user, job_id):
    task = _get_owned_task(current_user, job_id)
    if not task:
        return jsonify({"success": False, "message": "任务不存在"}), 404
    payload = _job_payload(task, include_raw=True)
    return jsonify({"success": True, **payload, "data": payload})


@report_audio_workflow_bp.route("/jobs/<job_id>/download-audio", methods=["GET"])
@require_login
def download_audio(current_user, job_id):
    task = _get_owned_task(current_user, job_id)
    if not task:
        return jsonify({"success": False, "message": "任务不存在"}), 404
    audio = (task.result or {}).get("audio") or {}
    wav_path = audio.get("wav_path")
    if not wav_path or not os.path.exists(wav_path):
        return jsonify({"success": False, "message": "WAV 文件不存在"}), 404
    return send_file(wav_path, mimetype="audio/wav", as_attachment=True, download_name=f"{task.id}.wav")


@report_audio_workflow_bp.route("/jobs/<job_id>/download-result", methods=["GET"])
@require_login
def download_result(current_user, job_id):
    task = _get_owned_task(current_user, job_id)
    if not task:
        return jsonify({"success": False, "message": "任务不存在"}), 404
    output_path = (task.result or {}).get("output_path")
    if not output_path or not os.path.exists(output_path):
        return jsonify({"success": False, "message": "结果文件不存在"}), 404
    return send_file(output_path, mimetype="application/json", as_attachment=True, download_name=f"{task.id}.json")


@report_audio_workflow_bp.route("/jobs/<job_id>/download-video", methods=["GET"])
@require_login
def download_video(current_user, job_id):
    task = _get_owned_task(current_user, job_id)
    if not task:
        return jsonify({"success": False, "message": "任务不存在"}), 404
    video = (task.result or {}).get("video") or {}
    video_path = video.get("video_path")
    if not video_path or not os.path.exists(video_path):
        return jsonify({"success": False, "message": "视频文件不存在"}), 404
    return send_file(video_path, mimetype="video/mp4", as_attachment=True, download_name=f"{task.id}.mp4")


@report_audio_workflow_bp.route("/jobs/<job_id>/download-cover", methods=["GET"])
@require_login
def download_cover(current_user, job_id):
    task = _get_owned_task(current_user, job_id)
    if not task:
        return jsonify({"success": False, "message": "任务不存在"}), 404
    video = (task.result or {}).get("video") or {}
    cover_path = video.get("cover_path")
    if not cover_path or not os.path.exists(cover_path):
        return jsonify({"success": False, "message": "封面文件不存在"}), 404
    return send_file(cover_path, mimetype="image/jpeg", as_attachment=True, download_name=f"{task.id}.jpg")


@report_audio_workflow_bp.route("/jobs/<job_id>/input-pdf", methods=["GET"])
@require_login
def download_input_pdf(current_user, job_id):
    task = _get_owned_task(current_user, job_id)
    if not task:
        return jsonify({"success": False, "message": "任务不存在"}), 404
    payload = (task.params or {}).get("report_audio_workflow") or {}
    pdf = payload.get("pdf") or {}
    pdf_path = payload.get("pdf_path") or pdf.get("path")
    if not pdf_path or not os.path.exists(pdf_path):
        return jsonify({"success": False, "message": "PDF 文件不存在"}), 404
    return send_file(pdf_path, mimetype="application/pdf", as_attachment=True, download_name=pdf.get("filename") or f"{task.id}.pdf")


@report_audio_workflow_bp.route("/jobs/<job_id>/cancel", methods=["POST"])
@require_login
def cancel_job(current_user, job_id):
    task = _get_owned_task(current_user, job_id)
    if not task:
        return jsonify({"success": False, "message": "任务不存在"}), 404
    if task.status in {"completed", "failed", "canceled"}:
        payload = _job_payload(task)
        return jsonify({"success": True, "message": "任务已结束", **payload, "data": payload})

    if task.celery_task_id:
        celery_app.control.revoke(task.celery_task_id, terminate=False)

    result = dict(task.result or {})
    result["canceled_at"] = datetime.utcnow().isoformat() + "Z"
    task.status = "canceled"
    task.current_step = "canceled"
    task.completed_at = datetime.utcnow()
    task.result = result
    flag_modified(task, "result")
    db.session.commit()

    payload = _job_payload(task)
    return jsonify({"success": True, "message": "任务已取消", **payload, "data": payload})


@report_audio_workflow_bp.route("/jobs/<job_id>/retry", methods=["POST"])
@require_login
def retry_job(current_user, job_id):
    task = _get_owned_task(current_user, job_id)
    if not task:
        return jsonify({"success": False, "message": "任务不存在"}), 404
    if task.status in {"pending", "processing"}:
        payload = _job_payload(task)
        return jsonify({"success": True, "message": "任务仍在运行中", **payload, "data": payload})
    if task.status == "completed":
        payload = _job_payload(task)
        return jsonify({"success": False, "message": "任务已完成，无需续跑", **payload, "data": payload}), 400

    result = dict(task.result or {})
    logs = list(result.get("logs") or [])
    logs.append({
        "step": "retry_requested",
        "message": "用户请求从失败处续跑编排",
        "progress": task.progress or 0,
        "updated_at": datetime.utcnow().isoformat() + "Z",
    })
    result["logs"] = logs[-120:]
    result["retry_requested_at"] = datetime.utcnow().isoformat() + "Z"
    result["error"] = None
    task.status = "pending"
    task.current_step = "queued"
    task.error_message = None
    task.completed_at = None
    task.result = result
    flag_modified(task, "result")
    db.session.commit()

    try:
        celery_task = process_report_audio_workflow_job.apply_async(args=[task.id], queue=QUEUE_NAME, retry=False)
    except Exception as exc:
        task.status = "failed"
        task.current_step = "failed"
        task.error_message = f"续跑任务入队失败: {exc}"
        task.completed_at = datetime.utcnow()
        db.session.commit()
        return jsonify({"success": False, "message": task.error_message}), 502

    task.celery_task_id = celery_task.id
    db.session.commit()
    payload = _job_payload(task)
    return jsonify({"success": True, "message": "任务已从失败处续跑", **payload, "data": payload})


def _get_owned_task(current_user, job_id):
    task = PublicationTask.query.get(job_id)
    if not task or not isinstance(task.params, dict) or task.params.get("feature") != FEATURE_NAME:
        return None
    if str(task.params.get("user_id")) != str(getattr(current_user, "id", None)):
        return None
    return task


def _job_payload(task, include_raw=False):
    result = task.result or {}
    params = task.params or {}
    request_payload = params.get("report_audio_workflow") or {}
    structured_result = result.get("structured_result") if isinstance(result.get("structured_result"), dict) else None
    audio = result.get("audio") or None
    status = STATUS_MAP.get(task.status, task.status or "queued")
    live_children = _live_child_tasks(task.id)
    live_doubao_items = [
        _child_task_summary(child)
        for child in live_children
        if (child.params or {}).get("feature") == doubao_browser_tts.FEATURE_NAME
    ]
    payload = {
        "job_id": task.id,
        "status": status,
        "step": task.current_step,
        "progress": task.progress or 0,
        "created_at": task.created_at.isoformat() + "Z" if task.created_at else None,
        "started_at": result.get("started_at"),
        "completed_at": task.completed_at.isoformat() + "Z" if task.completed_at else result.get("completed_at"),
        "error": task.error_message or result.get("error"),
        "request": {
            "prompt": request_payload.get("prompt"),
            "review_prompt": request_payload.get("review_prompt"),
            "followups": request_payload.get("followups") or [],
            "gemini_url": request_payload.get("gemini_url"),
            "video": request_payload.get("video") or {},
            "pdf": _public_pdf(task.id, request_payload.get("pdf") or result.get("pdf")),
            "metadata": request_payload.get("metadata") or {},
        },
        "pdf": _public_pdf(task.id, request_payload.get("pdf") or result.get("pdf")),
        "children": result.get("children") or {},
        "gemini": result.get("gemini"),
        "doubao_browser_tts": live_doubao_items or result.get("doubao_browser_tts") or [],
        "structured_result": structured_result,
        "title": _display_title_from_structured(structured_result),
        "cover_title": (structured_result or {}).get("cover_title") or "",
        "cover_subtitle": (structured_result or {}).get("cover_subtitle") or "",
        "tts_text": result.get("tts_text"),
        "audio": _public_audio(task.id, audio),
        "video": _public_video(task.id, result.get("video")),
        "publisher_video": result.get("publisher_video"),
        "pdf_first_page_image": _public_image(task.id, result.get("pdf_first_page_image")),
        "download_audio_url": f"/api/report-audio-workflows/jobs/{task.id}/download-audio" if audio and audio.get("wav_path") else None,
        "download_result_url": f"/api/report-audio-workflows/jobs/{task.id}/download-result" if result.get("output_path") else None,
        "download_video_url": f"/api/report-audio-workflows/jobs/{task.id}/download-video" if _path_exists((result.get("video") or {}).get("video_path")) else None,
        "download_cover_url": f"/api/report-audio-workflows/jobs/{task.id}/download-cover" if _path_exists((result.get("video") or {}).get("cover_path")) else None,
        "metadata": result.get("metadata") or request_payload.get("metadata") or {},
        "notifications": result.get("notifications") or {},
        "logs": result.get("logs") or [],
    }
    if include_raw:
        payload["raw_params"] = params
        payload["raw_result"] = result
    return payload


def _live_child_tasks(parent_id):
    try:
        return PublicationTask.query.filter(
            or_(
                PublicationTask.params["parent_task_id"].as_string() == parent_id,
                PublicationTask.params["source_task_id"].as_string() == parent_id,
            )
        ).order_by(PublicationTask.created_at.asc()).all()
    except Exception:
        return []


def _child_task_summary(task):
    result = task.result if isinstance(task.result, dict) else {}
    params = task.params if isinstance(task.params, dict) else {}
    return {
        "job_id": task.id,
        "celery_id": task.celery_task_id,
        "status": task.status,
        "step": task.current_step,
        "progress": task.progress or 0,
        "error": task.error_message or result.get("error"),
        "metadata": result.get("metadata") or _child_metadata_from_params(params),
        "audio": result.get("audio"),
        "created_at": task.created_at.isoformat() + "Z" if task.created_at else None,
        "completed_at": task.completed_at.isoformat() + "Z" if task.completed_at else None,
    }


def _child_metadata_from_params(params):
    if not isinstance(params, dict):
        return {}
    for key in ("doubao_browser_tts", "report_pdf_gemini", "metadata"):
        value = params.get(key)
        if isinstance(value, dict) and isinstance(value.get("metadata"), dict):
            return value.get("metadata") or {}
    return {}


def _display_title_from_structured(structured):
    if not isinstance(structured, dict):
        return ""
    titles = structured.get("titles") if isinstance(structured.get("titles"), dict) else {}
    candidates = [
        structured.get("cover_title"),
        structured.get("title"),
        (titles.get("short") or [None])[0] if isinstance(titles.get("short"), list) else None,
        (titles.get("standard") or [None])[0] if isinstance(titles.get("standard"), list) else None,
    ]
    for candidate in candidates:
        title = _compact_title(candidate)
        if title:
            return title
    return ""


def _compact_title(value):
    lines = [
        " ".join(line.split())
        for line in str(value or "").replace("\r", "\n").split("\n")
        if line.strip()
    ]
    return " / ".join(lines).strip()


def _request_payload():
    if request.is_json:
        data = request.get_json(silent=True) or {}
    else:
        data = request.form.to_dict(flat=True)
        if "metadata" in data and isinstance(data["metadata"], str):
            try:
                data["metadata"] = json.loads(data["metadata"] or "{}")
            except Exception:
                data["metadata"] = {}
    return data if isinstance(data, dict) else {}


def _parse_followups(data):
    raw = data.get("followups") or data.get("followup_prompts") or data.get("followup")
    if raw is None:
        return []
    if isinstance(raw, list):
        items = raw
    elif isinstance(raw, str):
        value = raw.strip()
        if not value:
            return []
        try:
            parsed = json.loads(value)
            items = parsed if isinstance(parsed, list) else [value]
        except Exception:
            items = value.splitlines()
    else:
        items = [raw]
    return [str(item).strip() for item in items if str(item).strip()]


def _parse_video_options(data):
    raw = data.get("video")
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            raw = parsed if isinstance(parsed, dict) else {}
        except Exception:
            raw = {}
    options = raw if isinstance(raw, dict) else {}

    def pick(*keys, default=None):
        for key in keys:
            if key in data:
                return data.get(key)
            if key in options:
                return options.get(key)
        return default

    return {
        "enabled": _truthy(pick("landscape_video_enabled", "video_enabled", "enabled", default=False)),
        "title": str(pick("video_title", "cover_title", "title", default="") or "").strip(),
        "subtitle": str(pick("video_subtitle", "cover_subtitle", "subtitle", default="") or "").strip(),
        "tag_text": str(pick("video_tag_text", "cover_tag_text", "tag_text", default="") or "").strip(),
        "date_text": str(pick("video_date_text", "cover_date_text", "date_text", default="") or "").strip(),
        "cta_enabled": _truthy(pick("video_cta_enabled", "cta_enabled", default=True), default=True),
        "cta_text": str(pick("video_cta_text", "cta_text", default="") or "").strip(),
        "vertical_enabled": _truthy(pick("vertical_video_enabled", "vertical_enabled", default=False)),
        "bilibili_enabled": _truthy(pick("bilibili_enabled", "publish_bilibili_enabled", default=False)),
        "sub_burn_enabled": _truthy(pick("sub_burn_enabled", default=False)),
    }


def _validate_pdf_upload(file_storage):
    filename = secure_filename(file_storage.filename or "")
    if not filename:
        return "PDF 文件名为空"
    if not filename.lower().endswith(".pdf"):
        return "只支持上传 PDF 文件"
    max_bytes = _max_pdf_bytes()
    if request.content_length and request.content_length > max_bytes:
        return f"PDF 超过 {_max_pdf_mb()}MB 限制"
    return None


def _save_pdf_upload(file_storage, job_id):
    filename = secure_filename(file_storage.filename or "")
    if not filename.lower().endswith(".pdf"):
        raise ValueError("只支持上传 PDF 文件")

    upload_dir = Path(__file__).resolve().parents[3] / "data" / "uploads" / "report_audio_workflow" / datetime.utcnow().strftime("%Y%m%d")
    upload_dir.mkdir(parents=True, exist_ok=True)
    output_path = upload_dir / f"{job_id}_{filename}"
    file_storage.save(str(output_path))
    size = output_path.stat().st_size
    if size <= 0:
        raise ValueError("PDF 文件为空")
    if size > _max_pdf_bytes():
        try:
            output_path.unlink()
        except OSError:
            pass
        raise ValueError(f"PDF 超过 {_max_pdf_mb()}MB 限制")
    return {
        "filename": filename,
        "path": str(output_path),
        "bytes": size,
        "download_url": f"/api/report-audio-workflows/jobs/{job_id}/input-pdf",
    }


def _validate_pdf_asset_id(asset_id):
    if not asset_id:
        return "请选择已同步 PDF"
    try:
        _pdf_meta_from_zsxq_attachment(asset_id, None)
    except ValueError as exc:
        return str(exc)
    return None


def _pdf_meta_from_zsxq_attachment(asset_id, job_id):
    attachment = ZsxqAttachment.query.get(str(asset_id))
    if not attachment:
        raise ValueError("选择的同步 PDF 不存在")
    if attachment.download_status != "downloaded":
        raise ValueError("选择的同步 PDF 还没有下载完成")

    path = _resolve_pdf_path(attachment.local_path)
    if not path:
        raise ValueError("选择的同步 PDF 本地文件不存在")
    size = path.stat().st_size
    if size <= 0:
        raise ValueError("选择的同步 PDF 文件为空")
    if size > _max_pdf_bytes():
        raise ValueError(f"PDF 超过 {_max_pdf_mb()}MB 限制")

    topic = ZsxqTopic.query.filter_by(
        group_id=attachment.group_id,
        tab_id=attachment.tab_id,
        topic_id=attachment.topic_id,
    ).first()
    return {
        "filename": attachment.name or path.name,
        "path": str(path),
        "bytes": size,
        "source": "zsxq_attachment",
        "attachment_id": attachment.id,
        "group_id": attachment.group_id,
        "tab_id": attachment.tab_id,
        "topic_id": attachment.topic_id,
        "topic_title": topic.title if topic else None,
        "tab_label": topic.tab_label if topic else None,
        "download_url": f"/api/report-audio-workflows/jobs/{job_id}/input-pdf" if job_id else None,
    }


def _pdf_asset_payload(attachment, topic=None):
    path = _resolve_pdf_path(attachment.local_path)
    if not path:
        return None
    try:
        size = path.stat().st_size
    except OSError:
        return None
    if size <= 0:
        return None
    return {
        "id": attachment.id,
        "filename": attachment.name or path.name,
        "bytes": size,
        "updated_at": attachment.updated_at.isoformat() + "Z" if attachment.updated_at else None,
        "created_at": attachment.created_at.isoformat() + "Z" if attachment.created_at else None,
        "group_id": attachment.group_id,
        "tab_id": attachment.tab_id,
        "topic_id": attachment.topic_id,
        "topic_title": topic.title if topic else None,
        "topic_author": topic.author_name if topic else None,
        "topic_create_time": topic.create_time if topic else None,
        "tab_label": topic.tab_label if topic else None,
        "local_path": str(path),
        "preview_url": f"/api/report-audio-workflows/pdf-assets/{attachment.id}/preview",
    }


def _resolve_pdf_path(value):
    raw = str(value or "").strip()
    if not raw:
        return None
    candidates = []
    path = Path(raw)
    candidates.append(path)
    if not path.is_absolute():
        candidates.append(PROJECT_ROOT / raw)
    for candidate in candidates:
        try:
            resolved = candidate.expanduser().resolve()
        except OSError:
            continue
        if resolved.exists() and resolved.is_file() and resolved.suffix.lower() == ".pdf":
            return resolved
    return None


def _public_pdf(job_id, pdf):
    if not isinstance(pdf, dict) or not pdf:
        return None
    public = {
        "filename": pdf.get("filename"),
        "bytes": pdf.get("bytes"),
        "source": pdf.get("source") or "upload",
        "attachment_id": pdf.get("attachment_id"),
        "topic_title": pdf.get("topic_title"),
        "tab_label": pdf.get("tab_label"),
    }
    if pdf.get("path"):
        public["download_url"] = f"/api/report-audio-workflows/jobs/{job_id}/input-pdf"
    return {key: value for key, value in public.items() if value is not None}


def _public_audio(job_id, audio):
    if not isinstance(audio, dict) or not audio:
        return None
    public = {
        "chunks": audio.get("chunks"),
        "seconds": audio.get("seconds"),
        "sample_rate": audio.get("sample_rate"),
        "bytes": audio.get("bytes"),
    }
    if audio.get("wav_path"):
        public["download_url"] = f"/api/report-audio-workflows/jobs/{job_id}/download-audio"
    return public


def _public_video(job_id, video):
    if not isinstance(video, dict) or not video:
        return None
    public = {
        "enabled": video.get("enabled"),
        "status": video.get("status"),
        "publisher_task_id": video.get("publisher_task_id"),
        "message": video.get("message"),
        "bilibili_result": video.get("bilibili_result"),
    }
    video_path = video.get("video_path")
    cover_path = video.get("cover_path")
    if video_path and os.path.exists(video_path):
        public["video"] = {
            "bytes": os.path.getsize(video_path),
            "download_url": f"/api/report-audio-workflows/jobs/{job_id}/download-video",
        }
    if cover_path and os.path.exists(cover_path):
        public["cover"] = {
            "bytes": os.path.getsize(cover_path),
            "download_url": f"/api/report-audio-workflows/jobs/{job_id}/download-cover",
        }
    image = video.get("pdf_first_page_image")
    if isinstance(image, dict):
        public["pdf_first_page_image"] = _public_image(job_id, image)
    return {key: value for key, value in public.items() if value is not None}


def _public_image(job_id, image):
    if not isinstance(image, dict) or not image:
        return None
    return {
        "bytes": image.get("bytes"),
        "width": image.get("width"),
        "height": image.get("height"),
        "page": image.get("page"),
    }


def _path_exists(path):
    return bool(path and os.path.exists(path))


def _truthy(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled", "y"}


def _max_pdf_mb():
    try:
        return max(1, int(os.getenv("REPORT_AUDIO_WORKFLOW_MAX_PDF_MB", os.getenv("GEMINI_BROWSER_MAX_PDF_MB", "200"))))
    except (TypeError, ValueError):
        return 200


def _max_pdf_bytes():
    return _max_pdf_mb() * 1024 * 1024


def _bounded_int(value, default, minimum, maximum):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))
