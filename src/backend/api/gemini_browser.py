import json
import os
from datetime import datetime
from pathlib import Path

from flask import Blueprint, jsonify, request, send_file
from sqlalchemy.orm import load_only
from sqlalchemy.orm.attributes import flag_modified
from werkzeug.utils import secure_filename

from celery_config import celery_app
from models import db
from models.task import PublicationTask
from services.gemini_browser import (
    FEATURE_NAME,
    LEGACY_FEATURE_NAME,
    QUEUE_NAME,
    status_payload as browser_status_payload,
)
from services.runtime_guard import (
    apply_async_after_db_release,
    revoke_celery_task_after_db_commit,
)
from tasks.gemini_browser import process_report_pdf_gemini_job
from utils.auth_utils import require_login


API_PREFIX = "/api/report-pdf-gemini"
LEGACY_API_PREFIX = "/api/gemini-browser"

gemini_browser_bp = Blueprint("gemini_browser", __name__, url_prefix=LEGACY_API_PREFIX)
report_pdf_gemini_bp = Blueprint("report_pdf_gemini", __name__, url_prefix=API_PREFIX)

STATUS_MAP = {
    "pending": "queued",
    "processing": "running",
    "completed": "succeeded",
    "failed": "failed",
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


@gemini_browser_bp.route("/status", methods=["GET"])
@require_login
def service_status(_current_user):
    return jsonify({
        "success": True,
        "data": {
            "provider": {
                "id": FEATURE_NAME,
                "name": "研报 PDF Gem 解析",
                **browser_status_payload(),
            }
        },
    })


@gemini_browser_bp.route("/jobs", methods=["POST"])
@require_login
def create_job(current_user):
    data = _request_payload()
    pdf_file = request.files.get("pdf") or request.files.get("file")
    prompt = (data.get("prompt") or data.get("text") or "").strip()
    followups = _parse_followups(data)
    metadata = data.get("metadata") or {}
    target_url = (data.get("url") or "").strip()
    if not prompt and not pdf_file:
        return jsonify({"success": False, "message": "prompt 和 PDF 不能同时为空"}), 400
    if not isinstance(metadata, dict):
        return jsonify({"success": False, "message": "metadata 必须是对象"}), 400
    if target_url and not target_url.startswith(("http://", "https://")):
        return jsonify({"success": False, "message": "url 必须是 http(s) 地址"}), 400
    if pdf_file:
        pdf_error = _validate_pdf_upload(pdf_file)
        if pdf_error:
            return jsonify({"success": False, "message": pdf_error}), 400

    max_length = _max_prompt_length()
    if len(prompt) > max_length:
        return jsonify({"success": False, "message": f"prompt 超过 {max_length} 字限制"}), 400
    for index, followup in enumerate(followups, start=1):
        if len(followup) > max_length:
            return jsonify({"success": False, "message": f"第 {index} 个追问超过 {max_length} 字限制"}), 400

    browser_status = browser_status_payload()
    if not browser_status.get("configured"):
        return jsonify({
            "success": False,
            "message": "研报 PDF Gem 解析未配置，请设置 REPORT_PDF_GEMINI_USER_DATA_DIR 或 GEMINI_BROWSER_USER_DATA_DIR",
            "data": {"provider": browser_status},
        }), 400

    task = PublicationTask(
        status="pending",
        current_step="queued",
        progress=0,
        params={
            "feature": FEATURE_NAME,
            "user_id": str(getattr(current_user, "id", None)),
            "report_pdf_gemini": {
                "prompt": prompt,
                "followups": followups,
                "url": target_url,
                "metadata": metadata,
                "pdf": None,
                "pdf_path": None,
            },
        },
        result={
            "metadata": metadata,
            "response_text": None,
            "turns": [],
            "pdf": None,
            "logs": [],
        },
    )
    db.session.add(task)
    db.session.flush()

    if pdf_file:
        try:
            pdf_meta = _save_pdf_upload(pdf_file, task.id)
        except ValueError as exc:
            db.session.rollback()
            return jsonify({"success": False, "message": str(exc)}), 400
        except OSError as exc:
            db.session.rollback()
            return jsonify({"success": False, "message": f"PDF 保存失败: {exc}"}), 500

        params = dict(task.params or {})
        payload = dict(params.get("report_pdf_gemini") or {})
        payload["pdf"] = pdf_meta
        payload["pdf_path"] = pdf_meta["path"]
        params["report_pdf_gemini"] = payload
        task.params = params

        result = dict(task.result or {})
        result["pdf"] = pdf_meta
        task.result = result
        flag_modified(task, "params")
        flag_modified(task, "result")

    db.session.commit()

    try:
        celery_task = apply_async_after_db_release(
            process_report_pdf_gemini_job,
            args=[task.id],
            queue=QUEUE_NAME,
            retry=False,
        )
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


@gemini_browser_bp.route("/jobs", methods=["GET"])
@require_login
def list_jobs(current_user):
    page = _bounded_int(request.args.get("page"), 1, 1, 100000)
    per_page = _bounded_int(request.args.get("per_page"), 20, 1, 100)
    status = (request.args.get("status") or "").strip()

    query = PublicationTask.query.filter(
        PublicationTask.feature.in_([FEATURE_NAME, LEGACY_FEATURE_NAME]),
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


@gemini_browser_bp.route("/jobs/<job_id>", methods=["GET"])
@require_login
def get_job(current_user, job_id):
    task = _get_owned_task(current_user, job_id)
    if not task:
        return jsonify({"success": False, "message": "任务不存在"}), 404
    payload = _job_payload(task, include_raw=True)
    return jsonify({"success": True, **payload, "data": payload})


@gemini_browser_bp.route("/jobs/<job_id>/download", methods=["GET"])
@require_login
def download_job(current_user, job_id):
    task = _get_owned_task(current_user, job_id)
    if not task:
        return jsonify({"success": False, "message": "任务不存在"}), 404
    output_path = (task.result or {}).get("output_path")
    if not output_path or not os.path.exists(output_path):
        return jsonify({"success": False, "message": "结果文件不存在"}), 404
    return send_file(
        output_path,
        mimetype="application/json",
        as_attachment=True,
        download_name=f"{task.id}.json",
    )


@gemini_browser_bp.route("/jobs/<job_id>/input-pdf", methods=["GET"])
@require_login
def download_input_pdf(current_user, job_id):
    task = _get_owned_task(current_user, job_id)
    if not task:
        return jsonify({"success": False, "message": "任务不存在"}), 404
    payload = (task.params or {}).get("report_pdf_gemini") or (task.params or {}).get("gemini_browser") or {}
    pdf = payload.get("pdf") or {}
    pdf_path = payload.get("pdf_path") or pdf.get("path")
    if not pdf_path or not os.path.exists(pdf_path):
        return jsonify({"success": False, "message": "PDF 文件不存在"}), 404
    return send_file(
        pdf_path,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=pdf.get("filename") or f"{task.id}.pdf",
    )


@gemini_browser_bp.route("/jobs/<job_id>/cancel", methods=["POST"])
@require_login
def cancel_job(current_user, job_id):
    task = _get_owned_task(current_user, job_id)
    if not task:
        return jsonify({"success": False, "message": "任务不存在"}), 404
    if task.status in {"completed", "failed", "canceled"}:
        payload = _job_payload(task)
        return jsonify({"success": True, "message": "任务已结束", **payload, "data": payload})

    celery_task_id = str(task.celery_task_id or "").strip()
    result = dict(task.result or {})
    result["canceled_at"] = datetime.utcnow().isoformat() + "Z"
    task.status = "canceled"
    task.current_step = "canceled"
    task.completed_at = datetime.utcnow()
    task.result = result
    flag_modified(task, "result")
    payload = _job_payload(task)
    db.session.commit()
    revoked = revoke_celery_task_after_db_commit(
        celery_task_id,
        operation=f"gemini browser cancel revoke task={task.id}",
        business_task_id=str(task.id),
    )
    message = "任务已取消"
    if celery_task_id and not revoked:
        message += "；broker 撤回失败，终态门禁将拦截旧消息"
    return jsonify({"success": True, "message": message, **payload, "data": payload})


def _get_owned_task(current_user, job_id):
    task = PublicationTask.query.options(load_only(*TASK_PAYLOAD_COLUMNS)).filter(PublicationTask.id == str(job_id)).one_or_none()
    if not task or (task.feature or (task.params or {}).get("feature")) not in {FEATURE_NAME, LEGACY_FEATURE_NAME}:
        return None
    if str(task.user_id or (task.params or {}).get("user_id")) != str(getattr(current_user, "id", None)):
        return None
    return task


def _job_payload(task, include_raw=False):
    result = task.result or {}
    params = task.params or {}
    request_payload = params.get("report_pdf_gemini") or params.get("gemini_browser") or {}
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
        "prompt": request_payload.get("prompt"),
        "followups": request_payload.get("followups") or result.get("followup_prompts") or [],
        "request": {
            "prompt": request_payload.get("prompt"),
            "followups": request_payload.get("followups") or [],
            "url": request_payload.get("url"),
            "pdf": request_payload.get("pdf"),
            "metadata": request_payload.get("metadata") or {},
        },
        "pdf": _public_pdf(task.id, request_payload.get("pdf") or result.get("pdf")),
        "response_text": result.get("response_text"),
        "final_response_text": result.get("final_response_text") or result.get("response_text"),
        "initial_response_text": result.get("initial_response_text"),
        "turns": result.get("turns") or [],
        "page_url": result.get("page_url"),
        "target_url": result.get("target_url") or request_payload.get("url"),
        "download_url": f"{API_PREFIX}/jobs/{task.id}/download" if result.get("output_path") else None,
        "metadata": result.get("metadata") or request_payload.get("metadata") or {},
        "logs": result.get("logs") or [],
    }
    if include_raw:
        payload["raw_params"] = params
        payload["raw_result"] = result
    return payload


def _max_prompt_length():
    try:
        return int(os.getenv("REPORT_PDF_GEMINI_MAX_PROMPT_LENGTH", os.getenv("GEMINI_BROWSER_MAX_PROMPT_LENGTH", "20000")))
    except (TypeError, ValueError):
        return 20000


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
    raw = (
        data.get("followups")
        or data.get("followup_prompts")
        or data.get("follow_up_prompts")
        or data.get("followup")
    )
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

    upload_dir = Path(__file__).resolve().parents[3] / "data" / "uploads" / "report_pdf_gemini" / datetime.utcnow().strftime("%Y%m%d")
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
        "download_url": f"{API_PREFIX}/jobs/{job_id}/input-pdf",
    }


def _public_pdf(job_id, pdf):
    if not isinstance(pdf, dict) or not pdf:
        return None
    public = {
        "filename": pdf.get("filename"),
        "bytes": pdf.get("bytes"),
    }
    if pdf.get("path"):
        public["download_url"] = f"{API_PREFIX}/jobs/{job_id}/input-pdf"
    return public


def _max_pdf_mb():
    try:
        return max(1, int(os.getenv("REPORT_PDF_GEMINI_MAX_PDF_MB", os.getenv("GEMINI_BROWSER_MAX_PDF_MB", "200"))))
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


report_pdf_gemini_bp.add_url_rule("/status", view_func=service_status, methods=["GET"])
report_pdf_gemini_bp.add_url_rule("/jobs", view_func=create_job, methods=["POST"])
report_pdf_gemini_bp.add_url_rule("/jobs", view_func=list_jobs, methods=["GET"])
report_pdf_gemini_bp.add_url_rule("/jobs/<job_id>", view_func=get_job, methods=["GET"])
report_pdf_gemini_bp.add_url_rule("/jobs/<job_id>/download", view_func=download_job, methods=["GET"])
report_pdf_gemini_bp.add_url_rule("/jobs/<job_id>/input-pdf", view_func=download_input_pdf, methods=["GET"])
report_pdf_gemini_bp.add_url_rule("/jobs/<job_id>/cancel", view_func=cancel_job, methods=["POST"])
