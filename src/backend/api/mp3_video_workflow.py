import mimetypes
import os
from datetime import datetime
from pathlib import Path

from flask import Blueprint, jsonify, request, send_file
from sqlalchemy import String, cast, or_
from sqlalchemy.orm import load_only
from sqlalchemy.orm.attributes import flag_modified

from celery_config import celery_app
from models import db
from models.task import PublicationTask
from services import gemini_browser
from services.mp3_video_workflow import (
    DEFAULT_GEMINI_URL,
    FEATURE_NAME,
    INFOGRAPHIC_PROMPT,
    QUEUE_NAME,
    audio_meta_from_zsxq_attachment,
    initial_gemini_prompt,
    list_audio_assets,
    status_payload as workflow_status_payload,
)
from tasks.mp3_video_workflow import process_mp3_video_workflow_job
from utils.auth_utils import require_login


mp3_video_workflow_bp = Blueprint("mp3_video_workflow", __name__, url_prefix="/api/mp3-video-workflows")

STATUS_MAP = {
    "pending": "queued",
    "processing": "running",
    "completed": "succeeded",
    "failed": "failed",
    "canceled": "canceled",
}
SYSTEM_USER_ID = "system"


def _normalize_search_query(value, max_length=80):
    return " ".join(str(value or "").strip().split())[:max_length]


def _escape_like(value):
    return (
        str(value or "")
        .replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )


def _visible_user_ids(current_user_or_id):
    if hasattr(current_user_or_id, "id"):
        raw_user_id = getattr(current_user_or_id, "id", None)
    else:
        raw_user_id = current_user_or_id
    user_id = str(raw_user_id or "").strip()
    values = {SYSTEM_USER_ID}
    if user_id:
        values.add(user_id)
    return values


@mp3_video_workflow_bp.route("/status", methods=["GET"])
@require_login
def service_status(_current_user):
    return jsonify({
        "success": True,
        "data": {
            "provider": {
                "id": FEATURE_NAME,
                "name": "MP3 到 Gemini 图片与横竖屏视频编排",
                **workflow_status_payload(),
            },
            "gemini_browser": gemini_browser.status_payload(),
        },
    })


@mp3_video_workflow_bp.route("/audio-assets", methods=["GET"])
@require_login
def audio_assets(_current_user):
    page = _bounded_int(request.args.get("page"), 1, 1, 100000)
    per_page = _bounded_int(request.args.get("per_page"), 30, 1, 100)
    payload = list_audio_assets(
        page=page,
        per_page=per_page,
        q=(request.args.get("q") or "").strip(),
        group_id=(request.args.get("group_id") or "").strip(),
        tab_id=(request.args.get("tab_id") or "").strip(),
        scope=(request.args.get("scope") or "all").strip(),
    )
    return jsonify({"success": True, "data": payload, "items": payload["items"]})


@mp3_video_workflow_bp.route("/audio-assets/workflow-stats", methods=["GET"])
@require_login
def audio_asset_workflow_stats(current_user):
    raw_ids = request.args.get("ids") or ""
    ids = [item.strip() for item in raw_ids.split(",") if item.strip()]
    ids = ids[:50]
    stats = _audio_asset_workflow_stats(
        ids,
        user_id=str(getattr(current_user, "id", None)),
    )
    return jsonify({
        "success": True,
        "data": {"stats": stats},
        "stats": stats,
    })


@mp3_video_workflow_bp.route("/audio-assets/<asset_id>/preview", methods=["GET"])
@require_login
def preview_audio_asset(_current_user, asset_id):
    try:
        audio = audio_meta_from_zsxq_attachment(asset_id)
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 404
    mimetype = mimetypes.guess_type(audio["path"])[0] or "audio/mpeg"
    return send_file(
        audio["path"],
        mimetype=mimetype,
        as_attachment=False,
        download_name=audio.get("filename") or f"{asset_id}.mp3",
    )


@mp3_video_workflow_bp.route("/jobs", methods=["POST"])
@require_login
def create_job(current_user):
    data = _request_payload()
    user_id = str(getattr(current_user, "id", None))
    audio_asset_id = (data.get("audio_asset_id") or data.get("attachment_id") or "").strip()
    if not audio_asset_id:
        return jsonify({"success": False, "message": "请选择一个已同步 MP3"}), 400

    gemini_url = (data.get("gemini_url") or DEFAULT_GEMINI_URL).strip()
    if gemini_url and not gemini_url.startswith(("http://", "https://")):
        return jsonify({"success": False, "message": "gemini_url 必须是 http(s) 地址"}), 400

    provider = workflow_status_payload()
    if not provider.get("configured"):
        return jsonify({"success": False, "message": "Gemini 浏览器自动化未配置", "data": {"provider": provider}}), 400

    try:
        audio = audio_meta_from_zsxq_attachment(audio_asset_id)
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400

    image_prompt = (data.get("image_prompt") or INFOGRAPHIC_PROMPT).strip()
    gemini_prompt = initial_gemini_prompt({"gemini_prompt": data.get("gemini_prompt")})
    metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    task = PublicationTask(
        status="pending",
        current_step="queued",
        progress=0,
        params={
            "feature": FEATURE_NAME,
            "user_id": user_id,
            FEATURE_NAME: {
                "audio_asset_id": audio_asset_id,
                "audio": audio,
                "gemini_url": gemini_url,
                "gemini_prompt": gemini_prompt,
                "image_prompt": image_prompt,
                "metadata": metadata,
            },
        },
        result={
            "metadata": metadata,
            "audio": audio,
            "children": {},
            "logs": [],
        },
    )
    db.session.add(task)
    db.session.commit()

    try:
        celery_task = process_mp3_video_workflow_job.apply_async(args=[task.id], queue=QUEUE_NAME, retry=False)
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


@mp3_video_workflow_bp.route("/jobs", methods=["GET"])
@require_login
def list_jobs(current_user):
    page = _bounded_int(request.args.get("page"), 1, 1, 100000)
    per_page = _bounded_int(request.args.get("per_page"), 20, 1, 100)
    status = (request.args.get("status") or "").strip()
    search = _normalize_search_query(
        request.args.get("q") or request.args.get("search") or request.args.get("job_search")
    )
    query = PublicationTask.query.filter(
        PublicationTask.feature == FEATURE_NAME,
        PublicationTask.user_id.in_(_visible_user_ids(current_user)),
    )
    if status:
        reverse_status = {value: key for key, value in STATUS_MAP.items()}
        query = query.filter(PublicationTask.status == reverse_status.get(status, status))
    if search:
        pattern = f"%{_escape_like(search)}%"
        query = query.filter(or_(
            PublicationTask.task_title.ilike(pattern, escape="\\"),
            cast(PublicationTask.params, String).ilike(pattern, escape="\\"),
            cast(PublicationTask.result, String).ilike(pattern, escape="\\"),
        ))
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
                "page": page,
                "per_page": per_page,
                "total": pagination.total,
                "pages": pagination.pages,
                "q": search,
            },
        },
        "items": items,
    })


@mp3_video_workflow_bp.route("/jobs/<job_id>", methods=["GET"])
@require_login
def get_job(current_user, job_id):
    task = _get_owned_task(current_user, job_id)
    if not task:
        return jsonify({"success": False, "message": "任务不存在"}), 404
    payload = _job_payload(task, include_raw=True)
    return jsonify({"success": True, **payload, "data": payload})


@mp3_video_workflow_bp.route("/jobs/<job_id>/cancel", methods=["POST"])
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


@mp3_video_workflow_bp.route("/jobs/<job_id>/retry", methods=["POST"])
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
        "message": "用户请求从失败处续跑 MP3 视频编排",
        "progress": task.progress or 0,
        "updated_at": datetime.utcnow().isoformat() + "Z",
    })
    result["logs"] = logs[-160:]
    result["retry_requested_at"] = datetime.utcnow().isoformat() + "Z"
    result["error"] = None
    notifications = result.get("notifications") if isinstance(result.get("notifications"), dict) else {}
    if notifications:
        history = list(result.get("notification_history") or [])
        history.append({
            "archived_at": result["retry_requested_at"],
            "reason": "retry",
            "notifications": notifications,
        })
        result["notification_history"] = history[-8:]
    result["notifications"] = {}
    result.pop("completed_at", None)
    task.status = "pending"
    task.current_step = "queued"
    task.error_message = None
    task.completed_at = None
    task.result = result
    flag_modified(task, "result")
    db.session.commit()
    try:
        celery_task = process_mp3_video_workflow_job.apply_async(args=[task.id], queue=QUEUE_NAME, retry=False)
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


@mp3_video_workflow_bp.route("/jobs/<job_id>/download-result", methods=["GET"])
@require_login
def download_result(current_user, job_id):
    task = _get_owned_task(current_user, job_id)
    if not task:
        return jsonify({"success": False, "message": "任务不存在"}), 404
    output_path = (task.result or {}).get("output_path")
    if not output_path or not os.path.exists(output_path):
        return jsonify({"success": False, "message": "结果文件不存在"}), 404
    return send_file(output_path, mimetype="application/json", as_attachment=True, download_name=f"{task.id}.json")


@mp3_video_workflow_bp.route("/jobs/<job_id>/input-audio", methods=["GET"])
@require_login
def download_input_audio(current_user, job_id):
    task = _get_owned_task(current_user, job_id)
    if not task:
        return jsonify({"success": False, "message": "任务不存在"}), 404
    audio = ((task.params or {}).get(FEATURE_NAME) or {}).get("audio") or (task.result or {}).get("audio") or {}
    path = audio.get("path")
    if not path or not os.path.exists(path):
        return jsonify({"success": False, "message": "音频文件不存在"}), 404
    mimetype = mimetypes.guess_type(path)[0] or "audio/mpeg"
    return send_file(path, mimetype=mimetype, as_attachment=True, download_name=audio.get("filename") or f"{task.id}.mp3")


@mp3_video_workflow_bp.route("/jobs/<job_id>/download-artifact/<artifact_key>", methods=["GET"])
@require_login
def download_artifact(current_user, job_id, artifact_key):
    task = _get_owned_task(current_user, job_id)
    if not task:
        return jsonify({"success": False, "message": "任务不存在"}), 404
    artifact = _artifact_by_key(task, artifact_key)
    if not artifact:
        return jsonify({"success": False, "message": "产物不存在"}), 404
    path = artifact.get("path")
    if not path or not os.path.exists(path):
        return jsonify({"success": False, "message": "产物文件不存在"}), 404
    mimetype = mimetypes.guess_type(path)[0] or "image/jpeg"
    suffix = Path(path).suffix or ".jpg"
    return send_file(path, mimetype=mimetype, as_attachment=True, download_name=f"{task.id}-{artifact_key}{suffix}")


@mp3_video_workflow_bp.route("/jobs/<job_id>/download-video/<video_key>", methods=["GET"])
@require_login
def download_video(current_user, job_id, video_key):
    task = _get_owned_task(current_user, job_id)
    if not task:
        return jsonify({"success": False, "message": "任务不存在"}), 404
    video = _video_by_key(task, video_key)
    if not video:
        return jsonify({"success": False, "message": "视频不存在"}), 404
    path = video.get("video_path") or video.get("landscape_video_path") or video.get("vertical_video_path")
    if not path or not os.path.exists(path):
        return jsonify({"success": False, "message": "视频文件不存在"}), 404
    return send_file(path, mimetype="video/mp4", as_attachment=True, download_name=f"{task.id}-{video_key}.mp4")


def _get_owned_task(current_user, job_id):
    task = PublicationTask.query.options(
        load_only(
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
            PublicationTask.created_at,
            PublicationTask.completed_at,
        )
    ).get(job_id)
    if not task or (task.feature or (task.params or {}).get("feature")) != FEATURE_NAME:
        return None
    task_user_id = str(task.user_id or (task.params or {}).get("user_id") or "").strip()
    if task_user_id not in _visible_user_ids(current_user):
        return None
    return task


def _empty_audio_asset_workflow_stats():
    return {
        "has_workflow": False,
        "total": 0,
        "succeeded": 0,
        "failed": 0,
        "running": 0,
        "latest": None,
    }


def _audio_asset_workflow_stats(asset_ids, user_id=None):
    ids = [str(item or "").strip() for item in asset_ids if str(item or "").strip()]
    if not ids:
        return {}

    stats = {asset_id: _empty_audio_asset_workflow_stats() for asset_id in ids}
    query = (
        PublicationTask.query
        .with_entities(
            PublicationTask.id.label("id"),
            PublicationTask.status.label("status"),
            PublicationTask.current_step.label("current_step"),
            PublicationTask.progress.label("progress"),
            PublicationTask.created_at.label("created_at"),
            PublicationTask.completed_at.label("completed_at"),
            PublicationTask.params.label("params"),
        )
        .filter(PublicationTask.feature == FEATURE_NAME)
    )
    if user_id:
        query = query.filter(PublicationTask.user_id.in_(_visible_user_ids(user_id)))

    for row in query.order_by(PublicationTask.created_at.desc()).all():
        params = row.params if isinstance(row.params, dict) else {}
        workflow = params.get(FEATURE_NAME) if isinstance(params.get(FEATURE_NAME), dict) else {}
        audio = workflow.get("audio") if isinstance(workflow.get("audio"), dict) else {}
        asset_id = str(workflow.get("audio_asset_id") or audio.get("attachment_id") or "").strip()
        if asset_id not in stats:
            continue

        bucket = stats[asset_id]
        bucket["has_workflow"] = True
        bucket["total"] += 1
        public_status = STATUS_MAP.get(row.status, row.status or "queued")
        if public_status == "succeeded":
            bucket["succeeded"] += 1
        elif public_status == "failed":
            bucket["failed"] += 1
        elif public_status in {"queued", "running", "submitting"}:
            bucket["running"] += 1
        if not bucket["latest"]:
            bucket["latest"] = {
                "job_id": row.id,
                "status": public_status,
                "step": row.current_step,
                "progress": row.progress or 0,
                "created_at": row.created_at.isoformat() + "Z" if row.created_at else None,
                "completed_at": row.completed_at.isoformat() + "Z" if row.completed_at else None,
            }
    return stats


def _job_payload(task, include_raw=False):
    result = task.result or {}
    params = task.params or {}
    request_payload = params.get(FEATURE_NAME) or {}
    artifacts = result.get("artifacts") or {}
    infographic = result.get("infographic")
    status = STATUS_MAP.get(task.status, task.status or "queued")
    payload = {
        "job_id": task.id,
        "status": status,
        "step": task.current_step,
        "progress": task.progress or 0,
        "created_at": task.created_at.isoformat() + "Z" if task.created_at else None,
        "completed_at": task.completed_at.isoformat() + "Z" if task.completed_at else result.get("completed_at"),
        "error": task.error_message or result.get("error"),
        "request": {
            "audio": request_payload.get("audio") or result.get("audio"),
            "gemini_url": request_payload.get("gemini_url"),
            "metadata": request_payload.get("metadata") or {},
        },
        "audio": result.get("audio") or request_payload.get("audio"),
        "gemini": result.get("gemini"),
        "initial_response_text": result.get("initial_response_text"),
        "plan": result.get("plan"),
        "infographic": infographic,
        "artifacts": artifacts,
        "children": result.get("children") or {},
        "retry_attempts": result.get("retry_attempts") or {},
        "retry_state": result.get("retry_state") or {},
        "landscape_video": result.get("landscape_video"),
        "vertical_video": result.get("vertical_video"),
        "logs": result.get("logs") or [],
        "download_input_audio_url": f"/api/mp3-video-workflows/jobs/{task.id}/input-audio",
        "download_result_url": f"/api/mp3-video-workflows/jobs/{task.id}/download-result" if _path_exists(result.get("output_path")) else None,
        "download_infographic_url": f"/api/mp3-video-workflows/jobs/{task.id}/download-artifact/infographic" if _path_exists((infographic or {}).get("path")) else None,
        "download_landscape_video_url": f"/api/mp3-video-workflows/jobs/{task.id}/download-video/landscape" if _path_exists(((result.get("landscape_video") or {}).get("video_path"))) else None,
        "download_vertical_video_url": f"/api/mp3-video-workflows/jobs/{task.id}/download-video/vertical" if _path_exists(((result.get("vertical_video") or {}).get("video_path"))) else None,
    }
    artifact_urls = {}
    for key, artifact in artifacts.items():
        if _path_exists((artifact or {}).get("path")):
            artifact_urls[key] = f"/api/mp3-video-workflows/jobs/{task.id}/download-artifact/{key}"
    payload["artifact_download_urls"] = artifact_urls
    if include_raw:
        payload["raw_params"] = params
        payload["raw_result"] = result
    return payload


def _artifact_by_key(task, key):
    key = str(key or "").strip()
    result = task.result or {}
    if key == "infographic":
        return result.get("infographic")
    artifacts = result.get("artifacts") if isinstance(result.get("artifacts"), dict) else {}
    return artifacts.get(key)


def _video_by_key(task, key):
    result = task.result or {}
    if key == "landscape":
        return result.get("landscape_video")
    if key == "vertical":
        return result.get("vertical_video")
    return None


def _path_exists(path):
    return bool(path and os.path.exists(path))


def _request_payload():
    if request.is_json:
        return request.get_json(silent=True) or {}
    data = dict(request.form.items())
    for key in ("metadata",):
        value = data.get(key)
        if isinstance(value, str) and value.strip().startswith(("{", "[")):
            import json
            try:
                data[key] = json.loads(value)
            except Exception:
                pass
    return data


def _bounded_int(value, default, min_value, max_value):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(min_value, min(max_value, parsed))
