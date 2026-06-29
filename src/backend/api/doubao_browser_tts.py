import hashlib
import os
from datetime import datetime

from flask import Blueprint, jsonify, request, send_file
from sqlalchemy.orm import load_only
from sqlalchemy.orm.attributes import flag_modified

from celery_config import celery_app
from models import db
from models.task import PublicationTask
from services.doubao_browser_tts import (
    FEATURE_NAME,
    QUEUE_NAME,
    status_payload as browser_status_payload,
)
from tasks.doubao_browser_tts import process_doubao_browser_tts_job
from utils.auth_utils import require_login


doubao_browser_tts_bp = Blueprint("doubao_browser_tts", __name__, url_prefix="/api/doubao-tts")

STATUS_MAP = {
    "pending": "queued",
    "processing": "running",
    "completed": "succeeded",
    "failed": "failed",
    "blocked_by_captcha": "blocked",
    "canceled": "canceled",
}

TASK_PAYLOAD_COLUMNS = (
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
    PublicationTask.updated_at,
    PublicationTask.completed_at,
)


@doubao_browser_tts_bp.route("/status", methods=["GET"])
@require_login
def service_status(_current_user):
    return jsonify({
        "success": True,
        "data": {
            "provider": {
                "id": FEATURE_NAME,
                "name": "豆包网页朗读捕获",
                **browser_status_payload(),
            }
        },
    })


@doubao_browser_tts_bp.route("/jobs", methods=["POST"])
@require_login
def create_job(current_user):
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    metadata = data.get("metadata") or {}
    if not text:
        return jsonify({"success": False, "message": "text 不能为空"}), 400
    if not isinstance(metadata, dict):
        return jsonify({"success": False, "message": "metadata 必须是对象"}), 400
    metadata = dict(metadata)
    metadata.setdefault("text_hash", _text_hash(text))

    max_length = _max_text_length()
    if len(text) > max_length:
        return jsonify({"success": False, "message": f"text 超过 {max_length} 字限制"}), 400

    if _truthy(data.get("reuse_completed"), False):
        cached_task = _completed_task_for_text(text, current_user=current_user)
        if cached_task:
            payload = _job_payload(cached_task)
            payload["reused"] = True
            return jsonify({
                "success": True,
                "message": "已复用同文本的豆包音频任务",
                "job_id": cached_task.id,
                "status": payload.get("status"),
                "reused": True,
                "data": payload,
            })

    browser_status = browser_status_payload()
    if not browser_status.get("configured"):
        return jsonify({
            "success": False,
            "message": "豆包浏览器 TTS 未配置，请设置 userDataDir 和插件路径",
            "data": {"provider": browser_status},
        }), 400

    task = PublicationTask(
        status="pending",
        current_step="queued",
        progress=0,
        params={
            "feature": FEATURE_NAME,
            "user_id": getattr(current_user, "id", None),
            "doubao_browser_tts": {
                "text": text,
                "metadata": metadata,
            },
        },
        result={
            "metadata": metadata,
            "audio": None,
            "logs": [],
        },
    )
    db.session.add(task)
    db.session.commit()

    try:
        celery_task = process_doubao_browser_tts_job.apply_async(args=[task.id], queue=_queue_name(), retry=False)
    except Exception as exc:
        task.status = "failed"
        task.current_step = "failed"
        task.error_message = f"任务入队失败: {exc}"
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


@doubao_browser_tts_bp.route("/jobs", methods=["GET"])
@require_login
def list_jobs(current_user):
    page = _bounded_int(request.args.get("page"), 1, 1, 100000)
    per_page = _bounded_int(request.args.get("per_page"), 20, 1, 100)
    status = (request.args.get("status") or "").strip()

    query = PublicationTask.query.filter(
        PublicationTask.feature == FEATURE_NAME,
        PublicationTask.user_id == str(getattr(current_user, "id", None)),
    )
    if status:
        reverse_status = {value: key for key, value in STATUS_MAP.items()}
        query = query.filter(PublicationTask.status == reverse_status.get(status, status))

    pagination = query.options(load_only(*TASK_PAYLOAD_COLUMNS)).order_by(PublicationTask.created_at.desc()).paginate(
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


@doubao_browser_tts_bp.route("/jobs/<job_id>", methods=["GET"])
@require_login
def get_job(current_user, job_id):
    task = _get_owned_task(current_user, job_id)
    if not task:
        return jsonify({"success": False, "message": "任务不存在"}), 404
    payload = _job_payload(task, include_raw=True)
    return jsonify({"success": True, **payload, "data": payload})


@doubao_browser_tts_bp.route("/jobs/<job_id>/download", methods=["GET"])
@require_login
def download_job(current_user, job_id):
    task = _get_owned_task(current_user, job_id)
    if not task:
        return jsonify({"success": False, "message": "任务不存在"}), 404
    audio = (task.result or {}).get("audio") or {}
    wav_path = audio.get("wav_path")
    if not wav_path or not os.path.exists(wav_path):
        return jsonify({"success": False, "message": "WAV 文件不存在"}), 404
    return send_file(
        wav_path,
        mimetype="audio/wav",
        as_attachment=True,
        download_name=f"{task.id}.wav",
    )


@doubao_browser_tts_bp.route("/jobs/<job_id>/cancel", methods=["POST"])
@require_login
def cancel_job(current_user, job_id):
    task = _get_owned_task(current_user, job_id)
    if not task:
        return jsonify({"success": False, "message": "任务不存在"}), 404
    if task.status in {"completed", "failed", "blocked_by_captcha", "canceled"}:
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


@doubao_browser_tts_bp.route("/jobs/<job_id>/retry", methods=["POST"])
@require_login
def retry_job(current_user, job_id):
    task = _get_owned_task(current_user, job_id)
    if not task:
        return jsonify({"success": False, "message": "任务不存在"}), 404
    if task.status not in {"failed", "blocked_by_captcha", "canceled"}:
        payload = _job_payload(task)
        return jsonify({"success": True, "message": "任务仍在队列或已完成，无需重试", **payload, "data": payload})

    result = dict(task.result or {})
    logs = list(result.get("logs") or [])
    now = datetime.utcnow().isoformat() + "Z"
    logs.append({
        "step": "manual_retry",
        "message": "手动重新投递豆包浏览器朗读任务",
        "progress": 0,
        "updated_at": now,
    })
    result.update({
        "audio": None,
        "logs": logs[-80:],
        "manual_retry_requested_at": now,
    })
    result.pop("error", None)
    result.pop("error_type", None)
    result.pop("blocked_reason", None)
    result.pop("captcha_blocked", None)

    task.status = "pending"
    task.current_step = "queued"
    task.progress = 0
    task.error_message = None
    task.completed_at = None
    task.result = result
    flag_modified(task, "result")
    db.session.commit()

    try:
        celery_task = process_doubao_browser_tts_job.apply_async(args=[task.id], queue=_queue_name(), retry=False)
    except Exception as exc:
        task.status = "failed"
        task.current_step = "failed"
        task.error_message = f"任务重新入队失败: {exc}"
        task.completed_at = datetime.utcnow()
        db.session.commit()
        return jsonify({"success": False, "message": task.error_message}), 502

    task.celery_task_id = celery_task.id
    db.session.commit()

    payload = _job_payload(task)
    return jsonify({"success": True, "message": "任务已重新投递", **payload, "data": payload})


def _get_owned_task(current_user, job_id):
    task = PublicationTask.query.options(load_only(*TASK_PAYLOAD_COLUMNS)).get(job_id)
    if not task or (task.feature or (task.params or {}).get("feature")) != FEATURE_NAME:
        return None
    if str(task.user_id or (task.params or {}).get("user_id")) != str(getattr(current_user, "id", None)):
        return None
    return task


def _job_payload(task, include_raw=False):
    result = task.result or {}
    audio = result.get("audio") or None
    params = task.params or {}
    request_payload = params.get("doubao_browser_tts") or {}
    status = STATUS_MAP.get(task.status, task.status or "queued")
    payload = {
        "job_id": task.id,
        "status": status,
        "step": task.current_step,
        "progress": task.progress or 0,
        "created_at": task.created_at.isoformat() + "Z" if task.created_at else None,
        "started_at": result.get("started_at"),
        "completed_at": task.completed_at.isoformat() + "Z" if task.completed_at else result.get("completed_at"),
        "error": task.error_message or result.get("error"),
        "error_type": result.get("error_type"),
        "blocked_reason": result.get("blocked_reason"),
        "captcha_blocked": result.get("captcha_blocked") or None,
        "text": request_payload.get("text"),
        "request": {
            "text": request_payload.get("text"),
            "metadata": request_payload.get("metadata") or {},
        },
        "audio": _public_audio(task.id, audio),
        "metadata": result.get("metadata") or request_payload.get("metadata") or {},
        "logs": result.get("logs") or [],
    }
    if include_raw:
        payload["raw_params"] = params
        payload["raw_result"] = result
    return payload


def _public_audio(job_id, audio):
    if not audio:
        return None
    public = {
        "chunks": audio.get("chunks"),
        "seconds": audio.get("seconds"),
        "sample_rate": audio.get("sample_rate"),
        "bytes": audio.get("bytes"),
    }
    if audio.get("wav_path"):
        public["download_url"] = f"/api/doubao-tts/jobs/{job_id}/download"
    return public


def _max_text_length():
    try:
        return int(os.getenv("DOUBAO_BROWSER_TTS_MAX_TEXT_LENGTH", "5000"))
    except (TypeError, ValueError):
        return 5000


def _queue_name():
    return os.getenv("DOUBAO_BROWSER_TTS_QUEUE", QUEUE_NAME)


def _text_hash(text):
    compact = "".join(str(text or "").split())
    return hashlib.sha256(compact.encode("utf-8")).hexdigest()


def _completed_task_for_text(text, *, current_user):
    expected_hash = _text_hash(text)
    expected_text = _plain_text(text)
    user_id = str(getattr(current_user, "id", None))
    scan_limit = _bounded_int(os.getenv("DOUBAO_BROWSER_TTS_CACHE_SCAN_LIMIT"), 500, 20, 2000)
    candidates = (
        PublicationTask.query.filter(
            PublicationTask.feature == FEATURE_NAME,
            PublicationTask.user_id == user_id,
            PublicationTask.status == "completed",
        )
        .options(load_only(*TASK_PAYLOAD_COLUMNS))
        .order_by(PublicationTask.created_at.desc())
        .limit(scan_limit)
        .all()
    )
    for candidate in candidates:
        if _task_text_hash(candidate) and _task_text_hash(candidate) != expected_hash:
            continue
        if _plain_text(_task_text(candidate)) != expected_text:
            continue
        audio = (candidate.result or {}).get("audio") if isinstance(candidate.result, dict) else {}
        wav_path = audio.get("wav_path") if isinstance(audio, dict) else ""
        if wav_path and os.path.exists(wav_path):
            return candidate
    return None


def _task_text_hash(task):
    result = task.result if isinstance(task.result, dict) else {}
    metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
    if not metadata:
        payload = _task_request_payload(task)
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    return str(metadata.get("text_hash") or "").strip()


def _task_text(task):
    return str(_task_request_payload(task).get("text") or "")


def _task_request_payload(task):
    params = task.params if isinstance(task.params, dict) else {}
    payload = params.get("doubao_browser_tts")
    return payload if isinstance(payload, dict) else {}


def _plain_text(text):
    return "".join(str(text or "").split())


def _truthy(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "on", "y"}


def _bounded_int(value, default, minimum, maximum):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))
