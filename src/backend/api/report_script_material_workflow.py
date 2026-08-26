import json
import hashlib
import logging
import os
import time
import uuid
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from threading import Lock
from types import SimpleNamespace

from flask import Blueprint, jsonify, request, send_file
from sqlalchemy import and_, inspect as sqlalchemy_inspect, or_
from sqlalchemy.orm import load_only
from sqlalchemy.orm.attributes import flag_modified
from werkzeug.utils import secure_filename

from celery_config import celery_app
from models import db
from models.content_pipeline_asset import ContentPipelineAsset
from models.task import PublicationTask
from models.zsxq_sync import ZsxqAttachment, ZsxqTopic
from services import gemini_browser
from services.content_pipeline.canonical_asset_service import (
    resolve_asset_reference,
    resolve_cleaned_asset,
)
from services.report_script_material_workflow import (
    DEFAULT_INITIAL_PROMPT,
    DEFAULT_REVIEW_PROMPT,
    FEATURE_NAME,
    QUEUE_NAME,
    RESULT_DETAIL_CACHE_VERSION,
    status_payload as workflow_status_payload,
    storage_path_for_job,
)
from services.report_audio_workflow import (
    clean_final_script_for_tts,
    review_prompt_for_pdf_evidence,
)
from services.runtime_guard import maybe_guarded_apply_async
from tasks.report_script_material_workflow import process_report_script_material_workflow_job
from utils.auth_utils import require_login
from utils.date_filters import submission_date_utc_range


report_script_material_workflow_bp = Blueprint(
    "report_script_material_workflow",
    __name__,
    url_prefix="/api/report-script-material-workflows",
)

SYSTEM_USER_ID = "system"
STATUS_MAP = {
    "pending": "queued",
    "processing": "running",
    "completed": "succeeded",
    "failed": "failed",
    "canceled": "canceled",
}
TERMINAL_STATUSES = {"completed", "failed", "canceled"}
MATERIAL_STATUS_FEATURES = (FEATURE_NAME, "report_pdf_audio_workflow", "report_audio_workflow")
TASK_LIST_COLUMNS = (
    PublicationTask.id,
    PublicationTask.celery_task_id,
    PublicationTask.status,
    PublicationTask.current_step,
    PublicationTask.progress,
    PublicationTask.error_message,
    PublicationTask.feature,
    PublicationTask.user_id,
    PublicationTask.task_title,
    PublicationTask.source_task_id,
    PublicationTask.report_audio_pdf_attachment_id,
    PublicationTask.report_audio_pdf_filename,
    PublicationTask.report_audio_pdf_bytes,
    PublicationTask.report_audio_pdf_topic_title,
    PublicationTask.report_audio_structured_title,
    PublicationTask.report_audio_output_path,
    PublicationTask.final_script_chars,
    PublicationTask.created_at,
    PublicationTask.updated_at,
    PublicationTask.completed_at,
)
TASK_REQUEST_COLUMNS = (*TASK_LIST_COLUMNS, PublicationTask.params)
TASK_DETAIL_COLUMNS = (*TASK_REQUEST_COLUMNS, PublicationTask.result)
TERMINAL_DETAIL_CACHE_TTL_SECONDS = 300
TERMINAL_DETAIL_REDIS_TTL_SECONDS = 86400
TERMINAL_DETAIL_CACHE_MAX_ENTRIES = 512
TERMINAL_DETAIL_DISK_ROOT = Path(__file__).resolve().parents[3] / "data" / "cache" / "report_script_material_detail"
_TERMINAL_DETAIL_CACHE = {}
_TERMINAL_DETAIL_CACHE_LOCK = Lock()
logger = logging.getLogger(__name__)


def _terminal_detail_cache_key(current_user, job_id):
    return (str(getattr(current_user, "id", "") or ""), str(job_id))


def _terminal_detail_redis_client():
    try:
        from utils.redis_client import redis_client

        return redis_client
    except Exception as exc:
        logger.warning("material terminal cache unavailable: %s", exc)
        return None


def _terminal_detail_redis_key(current_user, job_id):
    user_id, job_id = _terminal_detail_cache_key(current_user, job_id)
    digest = hashlib.sha256(f"{user_id}:{job_id}".encode("utf-8")).hexdigest()
    return f"shioa:report_script_material:terminal:v1:{digest}"


def _persistent_terminal_detail_get(current_user, job_id):
    client = _terminal_detail_redis_client()
    if client is None:
        return None
    try:
        raw = client.get(_terminal_detail_redis_key(current_user, job_id))
        payload = json.loads(raw) if raw else None
        return payload if isinstance(payload, dict) else None
    except Exception as exc:
        logger.warning("material terminal cache read failed: %s", exc)
        return None


def _persistent_terminal_detail_set(current_user, job_id, payload):
    client = _terminal_detail_redis_client()
    if client is None:
        return
    try:
        client.setex(
            _terminal_detail_redis_key(current_user, job_id),
            TERMINAL_DETAIL_REDIS_TTL_SECONDS,
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        )
    except Exception as exc:
        logger.warning("material terminal cache write failed: %s", exc)


def _terminal_detail_disk_path(current_user, job_id):
    user_id, job_id = _terminal_detail_cache_key(current_user, job_id)
    digest = hashlib.sha256(f"{user_id}:{job_id}".encode("utf-8")).hexdigest()
    return TERMINAL_DETAIL_DISK_ROOT / f"{digest}.json"


def _terminal_detail_disk_get(current_user, job_id):
    path = _terminal_detail_disk_path(current_user, job_id)
    try:
        payload = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
        return payload if isinstance(payload, dict) else None
    except Exception as exc:
        logger.warning("material terminal disk cache read failed: %s", exc)
        return None


def _terminal_detail_disk_set(current_user, job_id, payload):
    path = _terminal_detail_disk_path(current_user, job_id)
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        os.replace(temp_path, path)
    except Exception as exc:
        logger.warning("material terminal disk cache write failed: %s", exc)
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass


def _cached_terminal_detail(current_user, job_id):
    key = _terminal_detail_cache_key(current_user, job_id)
    now = time.time()
    with _TERMINAL_DETAIL_CACHE_LOCK:
        cached = _TERMINAL_DETAIL_CACHE.get(key)
        if cached and float(cached.get("expires_at") or 0) > now:
            return deepcopy(cached["payload"])
        _TERMINAL_DETAIL_CACHE.pop(key, None)
    payload = _terminal_detail_disk_get(current_user, job_id)
    if payload is not None:
        with _TERMINAL_DETAIL_CACHE_LOCK:
            _TERMINAL_DETAIL_CACHE[key] = {
                "expires_at": now + TERMINAL_DETAIL_CACHE_TTL_SECONDS,
                "payload": deepcopy(payload),
            }
        return deepcopy(payload)
    payload = _persistent_terminal_detail_get(current_user, job_id)
    if payload is not None:
        with _TERMINAL_DETAIL_CACHE_LOCK:
            _TERMINAL_DETAIL_CACHE[key] = {
                "expires_at": now + TERMINAL_DETAIL_CACHE_TTL_SECONDS,
                "payload": deepcopy(payload),
            }
        _terminal_detail_disk_set(current_user, job_id, payload)
        return deepcopy(payload)
    return None


def _cache_terminal_detail(current_user, job_id, payload):
    key = _terminal_detail_cache_key(current_user, job_id)
    with _TERMINAL_DETAIL_CACHE_LOCK:
        if len(_TERMINAL_DETAIL_CACHE) >= TERMINAL_DETAIL_CACHE_MAX_ENTRIES:
            _TERMINAL_DETAIL_CACHE.clear()
        _TERMINAL_DETAIL_CACHE[key] = {
            "expires_at": time.time() + TERMINAL_DETAIL_CACHE_TTL_SECONDS,
            "payload": deepcopy(payload),
        }
    _terminal_detail_disk_set(current_user, job_id, payload)
    _persistent_terminal_detail_set(current_user, job_id, payload)


def _clear_terminal_detail_cache():
    with _TERMINAL_DETAIL_CACHE_LOCK:
        _TERMINAL_DETAIL_CACHE.clear()


@report_script_material_workflow_bp.route("/status", methods=["GET"])
@require_login
def service_status(_current_user):
    return jsonify({
        "success": True,
        "data": {
            "provider": {
                "id": FEATURE_NAME,
                "name": "研报基础文案物料",
                **workflow_status_payload(),
            },
            "report_pdf_gemini": gemini_browser.status_payload(),
        },
    })


@report_script_material_workflow_bp.route("/pdf-assets", methods=["GET"])
@require_login
def list_pdf_assets(_current_user):
    page = _bounded_int(request.args.get("page"), 1, 1, 100000)
    per_page = _bounded_int(request.args.get("per_page"), 20, 1, 100)
    q = _normalize_search_query(request.args.get("q"))
    scope = str(request.args.get("scope") or "all").strip().lower()
    hot_download_threshold = _bounded_int(request.args.get("hot_download_threshold"), 40, 0, 1_000_000)

    query = (
        db.session.query(
            ZsxqAttachment.id.label("attachment_id"),
            ZsxqAttachment.group_id.label("group_id"),
            ZsxqAttachment.tab_id.label("tab_id"),
            ZsxqAttachment.topic_id.label("topic_id"),
            ZsxqAttachment.name.label("attachment_name"),
            ZsxqAttachment.file_id.label("file_id"),
            ZsxqAttachment.download_count.label("download_count"),
            ZsxqAttachment.local_path.label("local_path"),
            ZsxqAttachment.created_at.label("attachment_created_at"),
            ZsxqAttachment.updated_at.label("attachment_updated_at"),
            ZsxqTopic.title.label("topic_title"),
            ZsxqTopic.tab_label.label("tab_label"),
            ZsxqTopic.author_name.label("topic_author"),
            ZsxqTopic.create_time.label("topic_create_time"),
            ZsxqTopic.last_synced_at.label("topic_last_synced_at"),
        )
        .outerjoin(
            ZsxqTopic,
            and_(
                ZsxqTopic.group_id == ZsxqAttachment.group_id,
                ZsxqTopic.tab_id == ZsxqAttachment.tab_id,
                ZsxqTopic.topic_id == ZsxqAttachment.topic_id,
            ),
        )
        .filter(
            ZsxqAttachment.attachment_type == "file",
            ZsxqAttachment.download_status == "downloaded",
            ZsxqAttachment.local_path.isnot(None),
            or_(
                ZsxqAttachment.file_ext == "pdf",
                ZsxqAttachment.name.ilike("%.pdf"),
                ZsxqAttachment.local_path.ilike("%.pdf"),
            ),
        )
    )
    if scope in {"hot", "popular"}:
        query = query.filter(ZsxqAttachment.download_count >= hot_download_threshold)
    if q:
        pattern = f"%{_escape_like(q)}%"
        query = query.filter(or_(
            ZsxqAttachment.name.ilike(pattern, escape="\\"),
            ZsxqAttachment.local_path.ilike(pattern, escape="\\"),
            ZsxqTopic.title.ilike(pattern, escape="\\"),
            ZsxqTopic.tab_label.ilike(pattern, escape="\\"),
            ZsxqTopic.content.ilike(pattern, escape="\\"),
        ))

    target_count = page * per_page + 1
    chunk_size = min(max(per_page * 4, 40), 120)
    scan_limit = min(max(target_count * 5, 120), 2000)
    sync_time_order = db.func.coalesce(ZsxqTopic.last_synced_at, ZsxqAttachment.updated_at)
    ordered = query.order_by(
        ZsxqTopic.create_time.desc().nullslast(),
        sync_time_order.desc(),
        ZsxqAttachment.updated_at.desc(),
        ZsxqAttachment.created_at.desc(),
    )
    valid_items = []
    skipped_missing = 0
    scanned = 0
    while len(valid_items) < target_count and scanned < scan_limit:
        batch_limit = min(chunk_size, scan_limit - scanned)
        rows = ordered.offset(scanned).limit(batch_limit).all()
        if not rows:
            break
        scanned += len(rows)
        canonical_assets = _canonical_pdf_assets_by_source_paths(
            getattr(row, "local_path", None) for row in rows
        )
        for row in rows:
            item = _pdf_asset_payload(
                row,
                canonical_asset=canonical_assets.get(
                    _normalized_pdf_source_path(getattr(row, "local_path", None))
                ),
            )
            if item:
                valid_items.append(item)
            else:
                skipped_missing += 1
        if len(rows) < batch_limit:
            break

    pipeline_items = []
    pipeline_scanned = 0
    if scope not in {"hot", "popular"}:
        pipeline_query = ContentPipelineAsset.query.filter(
            ContentPipelineAsset.media_type == "pdf",
            ContentPipelineAsset.status == "cleaned",
        )
        if q:
            pattern = f"%{_escape_like(q)}%"
            pipeline_query = pipeline_query.filter(or_(
                ContentPipelineAsset.filename.ilike(pattern, escape="\\"),
                ContentPipelineAsset.source_path.ilike(pattern, escape="\\"),
                ContentPipelineAsset.source_mode.ilike(pattern, escape="\\"),
            ))
        pipeline_rows = (
            pipeline_query
            .order_by(ContentPipelineAsset.updated_at.desc(), ContentPipelineAsset.created_at.desc())
            .limit(scan_limit)
            .all()
        )
        pipeline_scanned = len(pipeline_rows)
        pipeline_items = [
            item
            for item in (_content_pipeline_pdf_asset_payload(row) for row in pipeline_rows)
            if item
        ]

    all_items = _merge_pdf_asset_items(valid_items, pipeline_items)
    start = (page - 1) * per_page
    end = start + per_page
    items = all_items[start:end]
    _attach_material_status(items)
    return jsonify({
        "success": True,
        "data": {
            "items": items,
            "pagination": {
                "page": page,
                "per_page": per_page,
                "has_more": len(all_items) > end or scanned >= scan_limit or pipeline_scanned >= scan_limit,
                "skipped_missing": skipped_missing,
                "scanned": scanned + pipeline_scanned,
                "scope": "hot" if scope in {"hot", "popular"} else "all",
                "hot_download_threshold": hot_download_threshold,
                "order": "published_desc",
                "q": q,
            },
        },
        "items": items,
    })


@report_script_material_workflow_bp.route("/pdf-assets/<asset_id>/preview", methods=["GET"])
@require_login
def preview_pdf_asset(_current_user, asset_id):
    try:
        pdf = _pdf_meta_from_asset_reference(asset_id)
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 404
    return send_file(
        pdf["path"],
        mimetype="application/pdf",
        as_attachment=False,
        download_name=pdf.get("filename") or f"{asset_id}.pdf",
    )


@report_script_material_workflow_bp.route("/pdf-assets/<asset_id>/material", methods=["GET"])
@require_login
def get_pdf_asset_material(current_user, asset_id):
    task = _latest_material_task_for_asset(asset_id, current_user=current_user, include_result=True)
    if not task:
        return jsonify({"success": False, "message": "这份 PDF 暂无基础文案物料"}), 404
    payload = _material_task_payload(task)
    return jsonify({
        "success": True,
        "job_id": payload.get("job_id"),
        "status": payload.get("status"),
        "data": payload,
    })


@report_script_material_workflow_bp.route("/jobs", methods=["POST"])
@require_login
def create_jobs(current_user):
    data = _request_payload()
    user_id = str(getattr(current_user, "id", None))
    pdf_file = request.files.get("pdf") or request.files.get("file")
    asset_ids = _parse_asset_ids(data)
    if not pdf_file and not asset_ids:
        return jsonify({"success": False, "message": "请上传 PDF 或选择至少 1 份已同步 PDF"}), 400
    if pdf_file:
        upload_error = _validate_pdf_upload(pdf_file)
        if upload_error:
            return jsonify({"success": False, "message": upload_error}), 400
        asset_ids = []
    if len(asset_ids) > 50:
        return jsonify({"success": False, "message": "单次最多投递 50 份 PDF"}), 400

    prompt = str(data.get("prompt") or DEFAULT_INITIAL_PROMPT).strip()
    pdf_evidence_enabled = _truthy(data.get("pdf_evidence_enabled"), False)
    review_prompt = review_prompt_for_pdf_evidence(
        data.get("review_prompt") or DEFAULT_REVIEW_PROMPT,
        enabled=pdf_evidence_enabled,
    )
    followups = _parse_followups(data)
    gemini_url = str(data.get("gemini_url") or data.get("url") or "").strip()
    if gemini_url and not gemini_url.startswith(("http://", "https://")):
        return jsonify({"success": False, "message": "gemini_url 必须是 http(s) 地址"}), 400

    gemini_status = gemini_browser.status_payload()
    if not gemini_status.get("configured"):
        return jsonify({
            "success": False,
            "message": "研报 PDF Gem 解析未配置",
            "data": {"report_pdf_gemini": gemini_status},
        }), 400

    metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    jobs = []
    errors = []
    if pdf_file:
        try:
            task = _create_task_for_pdf(
                _save_pdf_upload(pdf_file),
                user_id=user_id,
                prompt=prompt,
                review_prompt=review_prompt,
                followups=followups,
                gemini_url=gemini_url,
                metadata=metadata,
                pdf_evidence_enabled=pdf_evidence_enabled,
            )
            jobs.append(task)
        except Exception as exc:
            db.session.rollback()
            errors.append({"filename": secure_filename(pdf_file.filename or ""), "message": str(exc)})
    for asset_id in dict.fromkeys(asset_ids):
        try:
            task = _create_task_for_asset(
                asset_id,
                user_id=user_id,
                prompt=prompt,
                review_prompt=review_prompt,
                followups=followups,
                gemini_url=gemini_url,
                metadata=metadata,
                pdf_evidence_enabled=pdf_evidence_enabled,
            )
            jobs.append(task)
        except Exception as exc:
            db.session.rollback()
            errors.append({"asset_id": asset_id, "message": str(exc)})

    if not jobs and errors:
        return jsonify({"success": False, "message": errors[0]["message"], "errors": errors}), 400

    return jsonify({
        "success": True,
        "job_id": jobs[0]["job_id"] if len(jobs) == 1 else None,
        "status": jobs[0]["status"] if len(jobs) == 1 else "submitted",
        "data": {
            "jobs": jobs,
            "errors": errors,
            "submitted_count": len(jobs),
            "failed_count": len(errors),
        },
        "jobs": jobs,
        "errors": errors,
    })


@report_script_material_workflow_bp.route("/jobs", methods=["GET"])
@require_login
def list_jobs(current_user):
    page = _bounded_int(request.args.get("page"), 1, 1, 100000)
    per_page = _bounded_int(request.args.get("per_page"), 20, 1, 100)
    status = str(request.args.get("status") or "").strip()
    search = _normalize_search_query(request.args.get("q") or request.args.get("search") or request.args.get("job_search"))
    submitted_on = str(request.args.get("submitted_on") or request.args.get("job_date") or "").strip()
    submitted_range = submission_date_utc_range(submitted_on)
    if not submitted_range:
        submitted_on = ""
    query = PublicationTask.query.options(load_only(*TASK_LIST_COLUMNS)).filter(
        PublicationTask.feature == FEATURE_NAME,
        PublicationTask.user_id.in_(_visible_user_ids(current_user)),
    )
    if status:
        reverse = {value: key for key, value in STATUS_MAP.items()}
        query = query.filter(PublicationTask.status == reverse.get(status, status))
    if submitted_range:
        submitted_start, submitted_end = submitted_range
        query = query.filter(
            PublicationTask.created_at >= submitted_start,
            PublicationTask.created_at < submitted_end,
        )
    if search:
        pattern = f"%{_escape_like(search)}%"
        query = query.filter(or_(
            PublicationTask.id.ilike(pattern, escape="\\"),
            PublicationTask.task_title.ilike(pattern, escape="\\"),
            PublicationTask.report_audio_pdf_filename.ilike(pattern, escape="\\"),
            PublicationTask.report_audio_pdf_topic_title.ilike(pattern, escape="\\"),
            PublicationTask.report_audio_structured_title.ilike(pattern, escape="\\"),
        ))
    pagination = query.order_by(PublicationTask.created_at.desc()).paginate(
        page=page,
        per_page=per_page,
        error_out=False,
    )
    items = [_job_payload(task, include_detail=False) for task in pagination.items]
    return jsonify({
        "success": True,
        "data": {
            "items": items,
            "pagination": {
                "page": pagination.page,
                "per_page": per_page,
                "total": pagination.total,
                "pages": pagination.pages,
                "q": search,
                "submitted_on": submitted_on,
            },
        },
        "items": items,
    })


@report_script_material_workflow_bp.route("/jobs/<job_id>", methods=["GET"])
@require_login
def get_job(current_user, job_id):
    include_raw = _truthy(request.args.get("include_raw"), False)
    if not include_raw:
        cached = _cached_terminal_detail(current_user, job_id)
        if cached is not None:
            return jsonify(cached)
    reference = _get_owned_task_reference(current_user, job_id)
    if not reference:
        return jsonify({"success": False, "message": "任务不存在"}), 404
    task = None
    if reference.status == "completed" and not include_raw:
        result = _terminal_result_file(reference)
        if result is not None:
            task = _task_detail_snapshot(reference, result)
    if task is None:
        task = _get_owned_task(current_user, job_id, include_result=True)
        if not task:
            return jsonify({"success": False, "message": "任务不存在"}), 404
    payload = _job_payload(task, include_detail=True)
    response_payload = {
        "success": True,
        "job_id": payload.get("job_id"),
        "status": payload.get("status"),
        "data": payload,
    }
    if task.status == "completed" and not include_raw:
        _cache_terminal_detail(current_user, task.id, response_payload)
    return jsonify(response_payload)


@report_script_material_workflow_bp.route("/jobs/<job_id>/cancel", methods=["POST"])
@require_login
def cancel_job(current_user, job_id):
    task = _get_owned_task(current_user, job_id)
    if not task:
        return jsonify({"success": False, "message": "任务不存在"}), 404
    if task.status in TERMINAL_STATUSES:
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


@report_script_material_workflow_bp.route("/jobs/<job_id>/retry", methods=["POST"])
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
        "message": "用户请求从失败处续跑基础文案物料任务",
        "progress": task.progress or 0,
        "updated_at": datetime.utcnow().isoformat() + "Z",
    })
    result["logs"] = logs[-160:]
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
        submit_result = maybe_guarded_apply_async(
            process_report_script_material_workflow_job,
            args=[task.id],
            queue=QUEUE_NAME,
            feature=FEATURE_NAME,
            task=task,
            retry=False,
        )
        celery_task = submit_result["async_result"]
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
    payload["deferred"] = bool(submit_result.get("deferred"))
    payload["admission"] = submit_result.get("admission")
    return jsonify({"success": True, "message": "任务已从失败处续跑", **payload, "data": payload})


@report_script_material_workflow_bp.route("/jobs/<job_id>/download-result", methods=["GET"])
@require_login
def download_result(current_user, job_id):
    task = _get_owned_task(current_user, job_id, include_result=False)
    if not task:
        return jsonify({"success": False, "message": "任务不存在"}), 404
    output_path = _result_file_path(task)
    if not output_path or not os.path.exists(output_path):
        result = _task_result_from_db(task.id)
        output_path = (result or {}).get("output_path")
    if not output_path or not os.path.exists(output_path):
        return jsonify({"success": False, "message": "结果文件不存在"}), 404
    return send_file(
        output_path,
        mimetype="application/json",
        as_attachment=True,
        download_name=f"{task.id}_report_script_material_result.json",
    )


@report_script_material_workflow_bp.route("/jobs/<job_id>/input-pdf", methods=["GET"])
@require_login
def input_pdf(current_user, job_id):
    task = _get_owned_task(current_user, job_id, include_result=False)
    if not task:
        return jsonify({"success": False, "message": "任务不存在"}), 404
    params = task.params if isinstance(task.params, dict) else {}
    payload = params.get(FEATURE_NAME) if isinstance(params.get(FEATURE_NAME), dict) else {}
    pdf = payload.get("pdf") if isinstance(payload.get("pdf"), dict) else {}
    path = str(payload.get("pdf_path") or pdf.get("path") or "").strip()
    if not path or not os.path.exists(path):
        return jsonify({"success": False, "message": "PDF 文件不存在"}), 404
    return send_file(
        path,
        mimetype="application/pdf",
        as_attachment=False,
        download_name=pdf.get("filename") or f"{task.id}.pdf",
    )


def _create_task_for_asset(
    asset_id,
    *,
    user_id,
    prompt,
    review_prompt,
    followups,
    gemini_url,
    metadata,
    pdf_evidence_enabled=False,
):
    pdf = _pdf_meta_from_asset_reference(asset_id)
    return _create_task_for_pdf(
        pdf,
        user_id=user_id,
        prompt=prompt,
        review_prompt=review_prompt,
        followups=followups,
        gemini_url=gemini_url,
        metadata=metadata,
        pdf_evidence_enabled=pdf_evidence_enabled,
    )


def _create_task_for_pdf(
    pdf,
    *,
    user_id,
    prompt,
    review_prompt,
    followups,
    gemini_url,
    metadata,
    pdf_evidence_enabled=False,
):
    title = str(pdf.get("topic_title") or pdf.get("filename") or "研报基础文案物料").strip()
    task_metadata = dict(metadata or {})
    task_metadata.setdefault("source", "report-script-material-workflow-test-page")
    task_metadata.setdefault("title", title)
    task = PublicationTask(
        status="pending",
        current_step="queued",
        progress=0,
        params={
            "feature": FEATURE_NAME,
            "user_id": user_id,
            "source_feature": "zsxq_attachment" if pdf.get("attachment_id") else "uploaded_pdf",
            "source_task_id": pdf.get("attachment_id"),
            FEATURE_NAME: {
                "prompt": prompt,
                "review_prompt": review_prompt,
                "pdf_evidence_enabled": bool(pdf_evidence_enabled),
                "followups": followups,
                "gemini_url": gemini_url,
                "metadata": task_metadata,
                "video": {
                    "enabled": False,
                    "vertical_enabled": False,
                    "bilibili_enabled": False,
                    "source": "report-script-material-workflow-test-page",
                },
                "pdf": pdf,
                "pdf_path": pdf["path"],
            },
        },
        result={
            "metadata": task_metadata,
            "pdf": _public_pdf_meta(pdf),
            "children": {},
            "structured_result": None,
            "script_material": {"status": "pending"},
            "audio": None,
            "video": {
                "enabled": False,
                "status": "skipped",
                "message": FEATURE_NAME,
            },
            "logs": [],
        },
    )
    db.session.add(task)
    db.session.commit()

    try:
        submit_result = maybe_guarded_apply_async(
            process_report_script_material_workflow_job,
            args=[task.id],
            queue=QUEUE_NAME,
            feature=FEATURE_NAME,
            task=task,
            retry=False,
        )
        celery_task = submit_result["async_result"]
    except Exception as exc:
        task.status = "failed"
        task.current_step = "failed"
        task.error_message = f"基础文案物料任务入队失败: {exc}"
        task.completed_at = datetime.utcnow()
        db.session.commit()
        raise ValueError(task.error_message) from exc

    task.celery_task_id = celery_task.id
    db.session.commit()
    deferred = bool(submit_result.get("deferred"))
    return {
        "job_id": task.id,
        "celery_id": celery_task.id,
        "status": "deferred" if deferred else "queued",
        "deferred": deferred,
        "admission": submit_result.get("admission"),
        "pdf": _public_pdf_meta(pdf),
    }


def _validate_pdf_upload(file_storage):
    filename = secure_filename(file_storage.filename or "")
    if not filename:
        return "PDF 文件名为空"
    if not filename.lower().endswith(".pdf"):
        return "只支持上传 PDF 文件"
    if request.content_length and request.content_length > _max_pdf_bytes():
        return f"PDF 超过 {_max_pdf_mb()}MB 限制"
    return None


def _save_pdf_upload(file_storage):
    filename = secure_filename(file_storage.filename or "")
    upload_dir = Path(__file__).resolve().parents[3] / "data" / "uploads" / FEATURE_NAME / datetime.utcnow().strftime("%Y%m%d")
    upload_dir.mkdir(parents=True, exist_ok=True)
    output_path = upload_dir / f"{uuid.uuid4().hex}_{filename}"
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
        "source": "uploaded_pdf",
    }


def _job_payload(task, include_detail=False):
    result = task.result if include_detail and isinstance(task.result, dict) else {}
    params = task.params if include_detail and isinstance(task.params, dict) else {}
    payload = params.get(FEATURE_NAME) if isinstance(params.get(FEATURE_NAME), dict) else {}
    structured = _structured_result_from_task_result(result)
    script = _final_script_from_structured(structured)
    script_chars = len(script) if include_detail else int(task.final_script_chars or 0)
    script_material = result.get("script_material") if isinstance(result.get("script_material"), dict) else {}
    pdf = payload.get("pdf") if isinstance(payload.get("pdf"), dict) else {}
    if not pdf:
        pdf = result.get("pdf") if isinstance(result.get("pdf"), dict) else {}
    item = {
        "job_id": task.id,
        "status": STATUS_MAP.get(task.status, task.status or "queued"),
        "step": task.current_step,
        "progress": task.progress or 0,
        "created_at": task.created_at.isoformat() + "Z" if task.created_at else None,
        "updated_at": task.updated_at.isoformat() + "Z" if task.updated_at else None,
        "completed_at": task.completed_at.isoformat() + "Z" if task.completed_at else result.get("completed_at"),
        "error": task.error_message or result.get("error"),
        "title": task.task_title or task.report_audio_pdf_topic_title or task.report_audio_pdf_filename or "研报基础文案物料",
        "pdf": _public_pdf_meta(pdf) or {
            "attachment_id": task.report_audio_pdf_attachment_id,
            "filename": task.report_audio_pdf_filename,
            "bytes": task.report_audio_pdf_bytes,
            "topic_title": task.report_audio_pdf_topic_title,
        },
        "script_material": script_material or None,
        "script_chars": script_chars,
        "download_result_url": f"/api/report-script-material-workflows/jobs/{task.id}/download-result" if task.status == "completed" else None,
    }
    if include_detail:
        children = result.get("children") if isinstance(result.get("children"), dict) else {}
        item.update({
            "children": _fresh_child_summaries(children),
            "structured_result": structured or None,
            "final_script_for_tts": script,
            "tts_text": result.get("tts_text"),
            "logs": result.get("logs") or [],
            "input_pdf_url": f"/api/report-script-material-workflows/jobs/{task.id}/input-pdf",
        })
        if _truthy(request.args.get("include_raw"), False):
            item["raw_result"] = result
            item["raw_params"] = params
    return item


def _get_owned_task(current_user, job_id, include_result=True):
    columns = TASK_DETAIL_COLUMNS if include_result else TASK_REQUEST_COLUMNS
    task = PublicationTask.query.options(load_only(*columns)).filter(PublicationTask.id == str(job_id)).one_or_none()
    if not task or (task.feature or (task.params or {}).get("feature")) != FEATURE_NAME:
        return None
    if str(task.user_id or (task.params or {}).get("user_id") or "").strip() not in _visible_user_ids(current_user):
        return None
    return task


def _get_owned_task_reference(current_user, job_id):
    task = PublicationTask.query.options(load_only(*TASK_LIST_COLUMNS)).filter(
        PublicationTask.id == str(job_id)
    ).one_or_none()
    if not task:
        return None
    if task.feature:
        if task.feature != FEATURE_NAME:
            return None
        if task.user_id:
            return task if str(task.user_id).strip() in _visible_user_ids(current_user) else None
    return _get_owned_task(current_user, job_id, include_result=False)


def _terminal_result_file(task):
    path = _result_file_path(task)
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8")) if path and Path(path).is_file() else None
    except Exception as exc:
        logger.warning("material result detail file read failed: %s", exc)
        return None
    if not isinstance(payload, dict):
        return None
    if int(payload.get("detail_cache_version") or 0) < RESULT_DETAIL_CACHE_VERSION:
        return None
    return payload


def _task_detail_snapshot(task, result):
    values = {column.key: getattr(task, column.key) for column in TASK_LIST_COLUMNS}
    return SimpleNamespace(**values, params={}, result=result)


def _fresh_child_summaries(children):
    if not isinstance(children, dict) or not children:
        return children or {}
    by_label = {label: dict(value) for label, value in children.items() if isinstance(value, dict)}
    child_ids = [
        str(value.get("job_id") or "").strip()
        for value in by_label.values()
        if str(value.get("job_id") or "").strip()
    ]
    if not child_ids:
        return by_label
    rows = (
        PublicationTask.query
        .with_entities(
            PublicationTask.id,
            PublicationTask.celery_task_id,
            PublicationTask.status,
            PublicationTask.current_step,
            PublicationTask.progress,
            PublicationTask.error_message,
            PublicationTask.feature,
            PublicationTask.created_at,
            PublicationTask.completed_at,
        )
        .filter(PublicationTask.id.in_(child_ids))
        .all()
    )
    row_map = {str(row.id): row for row in rows}
    for value in by_label.values():
        row = row_map.get(str(value.get("job_id") or ""))
        if not row:
            continue
        value.update({
            "celery_id": row.celery_task_id,
            "status": row.status,
            "step": row.current_step,
            "progress": row.progress or 0,
            "feature": row.feature,
            "error": row.error_message,
            "created_at": row.created_at.isoformat() + "Z" if row.created_at else None,
            "completed_at": row.completed_at.isoformat() + "Z" if row.completed_at else None,
        })
    return by_label


def _latest_material_task_for_asset(asset_id, *, current_user=None, include_result=False):
    asset_id = str(asset_id or "").strip()
    if not asset_id:
        return None
    columns = TASK_DETAIL_COLUMNS if include_result else TASK_LIST_COLUMNS
    query = (
        PublicationTask.query
        .options(load_only(*columns))
        .filter(PublicationTask.feature.in_(MATERIAL_STATUS_FEATURES))
        .filter(PublicationTask.status != "canceled")
        .filter(or_(
            PublicationTask.source_task_id == asset_id,
            PublicationTask.report_audio_pdf_attachment_id == asset_id,
        ))
    )
    if current_user is not None:
        visible_user_ids = _visible_user_ids(current_user)
        query = query.filter(or_(
            PublicationTask.user_id.in_(visible_user_ids),
            PublicationTask.user_id.is_(None),
        ))
    candidates = query.order_by(PublicationTask.created_at.desc()).limit(20).all()
    return _best_material_task(candidates)


def _best_material_task(candidates):
    tasks = [task for task in candidates or [] if task]
    if not tasks:
        return None
    for task in tasks:
        if int(getattr(task, "final_script_chars", 0) or 0) > 0:
            return task
    for task in tasks:
        result = _loaded_task_result(task)
        if result and _final_script_from_task_result(result):
            return task
    for task in tasks:
        if task.status == "completed":
            return task
    return tasks[0]


def _loaded_task_result(task):
    try:
        if "result" in sqlalchemy_inspect(task).unloaded:
            return {}
    except Exception:
        pass
    result = getattr(task, "result", None)
    return result if isinstance(result, dict) else {}


def _material_task_payload(task):
    result = task.result if isinstance(task.result, dict) else {}
    params = task.params if isinstance(task.params, dict) else {}
    payload = _workflow_payload_from_params(params)
    structured = _structured_result_from_task_result(result)
    script = _final_script_from_structured(structured)
    script_chars = len(script) if script else int(task.final_script_chars or 0)
    script_material = result.get("script_material") if isinstance(result.get("script_material"), dict) else {}
    pdf = payload.get("pdf") if isinstance(payload.get("pdf"), dict) else {}
    if not pdf:
        pdf = result.get("pdf") if isinstance(result.get("pdf"), dict) else {}
    if not pdf:
        pdf = {
            "attachment_id": task.report_audio_pdf_attachment_id,
            "filename": task.report_audio_pdf_filename,
            "topic_title": task.report_audio_pdf_topic_title,
        }
    item = {
        "job_id": task.id,
        "feature": task.feature,
        "feature_label": _material_feature_label(task.feature),
        "status": STATUS_MAP.get(task.status, task.status or "queued"),
        "step": task.current_step,
        "progress": task.progress or 0,
        "created_at": task.created_at.isoformat() + "Z" if task.created_at else None,
        "updated_at": task.updated_at.isoformat() + "Z" if task.updated_at else None,
        "completed_at": task.completed_at.isoformat() + "Z" if task.completed_at else result.get("completed_at"),
        "error": task.error_message or result.get("error"),
        "title": task.task_title or task.report_audio_pdf_topic_title or task.report_audio_pdf_filename or "研报基础文案物料",
        "pdf": _public_pdf_meta(pdf) or {},
        "script_material": script_material or None,
        "structured_result": structured or None,
        "final_script_for_tts": script,
        "script_chars": script_chars,
        "logs": result.get("logs") or [],
    }
    if task.feature == FEATURE_NAME and task.status == "completed":
        item["download_result_url"] = f"/api/report-script-material-workflows/jobs/{task.id}/download-result"
    return item


def _workflow_payload_from_params(params):
    if not isinstance(params, dict):
        return {}
    for key in (FEATURE_NAME, "report_audio_workflow", "report_pdf_audio_workflow"):
        value = params.get(key)
        if isinstance(value, dict):
            return value
    return {}


def _structured_result_from_task_result(result):
    if not isinstance(result, dict):
        return {}
    structured = result.get("structured_result")
    return structured if isinstance(structured, dict) else {}


def _final_script_from_task_result(result):
    return _final_script_from_structured(_structured_result_from_task_result(result))


def _final_script_from_structured(structured):
    return clean_final_script_for_tts((structured or {}).get("final_script_for_tts") or "")


def _material_feature_label(feature):
    return {
        FEATURE_NAME: "基础文案物料",
        "report_pdf_audio_workflow": "研报转语音",
        "report_audio_workflow": "研报转语音",
    }.get(feature or "", feature or "")


def _pdf_meta_from_asset_reference(asset_id):
    reference = str(asset_id or "").strip()
    if reference.startswith("pipeline:"):
        pipeline_id = reference.split(":", 1)[1].strip()
        asset = (
            ContentPipelineAsset.query
            .filter(
                ContentPipelineAsset.id == pipeline_id,
                ContentPipelineAsset.media_type == "pdf",
            )
            .one_or_none()
        )
        if not asset:
            raise ValueError("选择的清洗 PDF 不存在")
        resolution = resolve_cleaned_asset(asset, media_type="pdf")
        if resolution.state != "ready" or not resolution.path:
            raise ValueError("尚无可用清洗产物，请修复或重新投递")
        return _content_pipeline_pdf_meta(asset, resolution.path, attachment_id=reference)
    return _pdf_meta_from_zsxq_attachment(reference)


def _pdf_meta_from_zsxq_attachment(asset_id):
    attachment = db.session.get(ZsxqAttachment, str(asset_id))
    if not attachment:
        raise ValueError("PDF 资产不存在")
    if attachment.download_status != "downloaded":
        raise ValueError("选择的同步 PDF 还没有下载完成")
    resolution = resolve_asset_reference(
        "",
        media_type="pdf",
        legacy_source_path=attachment.local_path,
    )
    if resolution.state != "ready" or not resolution.path or not resolution.asset:
        raise ValueError("选择的同步 PDF 尚无可用清洗产物，请重新投递")
    path = resolution.path
    size = path.stat().st_size
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
        "source_mode": resolution.asset.source_mode,
        "pipeline_asset_id": resolution.asset.id,
        "pipeline_task_id": resolution.asset.canonical_task_id,
        "attachment_id": attachment.id,
        "file_id": attachment.file_id,
        "group_id": attachment.group_id,
        "tab_id": attachment.tab_id,
        "topic_id": attachment.topic_id,
        "topic_title": topic.title if topic else None,
        "tab_label": topic.tab_label if topic else None,
        "preview_url": f"/api/report-script-material-workflows/pdf-assets/{attachment.id}/preview",
    }


def _pdf_asset_payload(row, *, canonical_asset=None):
    asset = canonical_asset or _canonical_pdf_asset_by_source_path(
        getattr(row, "local_path", None)
    )
    resolution = resolve_cleaned_asset(asset, media_type="pdf")
    path = resolution.path
    if resolution.state != "ready" or not path:
        return None
    size = path.stat().st_size
    return {
        "id": row.attachment_id,
        "asset_id": row.attachment_id,
        "asset_kind": "zsxq_attachment",
        "source_label": _zsxq_pdf_source_label(asset),
        "source_mode": getattr(asset, "source_mode", None) or "zsxq_sync",
        "pipeline_status": getattr(asset, "status", None),
        "canonical_status": getattr(asset, "status", None),
        "pipeline_asset_id": str(getattr(asset, "id", "") or "") or None,
        "pipeline_task_id": getattr(asset, "canonical_task_id", None),
        "filename": row.attachment_name or path.name,
        "bytes": size,
        "download_count": row.download_count,
        "updated_at": row.attachment_updated_at.isoformat() + "Z" if row.attachment_updated_at else None,
        "created_at": row.attachment_created_at.isoformat() + "Z" if row.attachment_created_at else None,
        "group_id": row.group_id,
        "tab_id": row.tab_id,
        "topic_id": row.topic_id,
        "topic_title": row.topic_title,
        "topic_author": row.topic_author,
        "topic_create_time": row.topic_create_time,
        "topic_last_synced_at": _isoformat_z(getattr(row, "topic_last_synced_at", None)),
        "tab_label": row.tab_label,
        "file_id": row.file_id,
        "local_path": str(path),
        "preview_url": f"/api/report-script-material-workflows/pdf-assets/{row.attachment_id}/preview",
    }


def _content_pipeline_source_label(source_mode):
    return {
        "scheduled_api": "计划任务",
        "sidebar": "侧边栏下载",
        "browser_sidebar": "侧边栏下载",
        "manual_download": "手动投递",
    }.get(str(source_mode or "").strip().lower(), "内容流水线")


def _content_pipeline_pdf_meta(asset, path, *, attachment_id):
    source_label = _content_pipeline_source_label(asset.source_mode)
    return {
        "filename": asset.filename or path.name,
        "path": str(path),
        "bytes": path.stat().st_size,
        "source": "content_pipeline_asset",
        "source_mode": asset.source_mode,
        "attachment_id": attachment_id,
        "pipeline_asset_id": asset.id,
        "pipeline_task_id": asset.canonical_task_id,
        "topic_title": asset.filename or source_label,
        "tab_label": source_label,
        "preview_url": f"/api/report-script-material-workflows/pdf-assets/{attachment_id}/preview",
    }


def _content_pipeline_pdf_asset_payload(asset):
    resolution = resolve_cleaned_asset(asset, media_type="pdf")
    path = resolution.path
    if resolution.state != "ready" or not path:
        return None
    source_label = _content_pipeline_source_label(asset.source_mode)
    asset_id = str(asset.id)
    return {
        "id": f"pipeline:{asset_id}",
        "asset_id": asset_id,
        "asset_kind": "content_pipeline",
        "source_label": source_label,
        "source_mode": asset.source_mode,
        "pipeline_status": asset.status,
        "canonical_status": asset.status,
        "pipeline_asset_id": asset_id,
        "pipeline_task_id": asset.canonical_task_id,
        "filename": asset.filename or path.name,
        "bytes": path.stat().st_size,
        "download_count": None,
        "updated_at": _isoformat_z(asset.updated_at),
        "created_at": _isoformat_z(asset.created_at),
        "group_id": None,
        "tab_id": None,
        "topic_id": None,
        "topic_title": asset.filename or source_label,
        "topic_author": None,
        "topic_create_time": None,
        "topic_last_synced_at": None,
        "tab_label": source_label,
        "file_id": None,
        "local_path": str(path),
        "preview_url": f"/api/report-script-material-workflows/pdf-assets/pipeline:{asset_id}/preview",
    }


def _normalized_pdf_source_path(path_value):
    raw = str(path_value or "").strip()
    if not raw:
        return None
    try:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = Path(__file__).resolve().parents[3] / path
        return str(path.resolve())
    except OSError:
        return None


def _canonical_pdf_assets_by_source_paths(path_values):
    source_paths = {
        path
        for path in (_normalized_pdf_source_path(value) for value in (path_values or []))
        if path
    }
    if not source_paths:
        return {}
    rows = (
        ContentPipelineAsset.query
        .filter(
            ContentPipelineAsset.media_type == "pdf",
            ContentPipelineAsset.status == "cleaned",
            ContentPipelineAsset.source_path.in_(source_paths),
        )
        .order_by(ContentPipelineAsset.updated_at.desc(), ContentPipelineAsset.created_at.desc())
        .all()
    )
    matched = {}
    for asset in rows:
        source_path = _normalized_pdf_source_path(asset.source_path)
        if source_path and source_path not in matched:
            matched[source_path] = asset
    return matched


def _canonical_pdf_asset_by_source_path(path_value):
    source_path = _normalized_pdf_source_path(path_value)
    if not source_path:
        return None
    return _canonical_pdf_assets_by_source_paths([source_path]).get(source_path)


def _zsxq_pdf_source_label(asset):
    source_mode = str(getattr(asset, "source_mode", "") or "").strip().lower()
    if source_mode in {"scheduled_api", "sidebar", "browser_sidebar"}:
        return f"知识星球·{_content_pipeline_source_label(source_mode)}"
    return "知识星球同步"


def _pdf_asset_sort_timestamp(item):
    for key in ("topic_create_time", "updated_at", "created_at"):
        value = item.get(key)
        if not value:
            continue
        if isinstance(value, datetime):
            return value.timestamp()
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
        except (TypeError, ValueError, OverflowError):
            continue
    return 0.0


def _merge_pdf_asset_items(synced_items, pipeline_items):
    merged = []
    seen_paths = {}
    for item in sorted(
        [*(synced_items or []), *(pipeline_items or [])],
        key=_pdf_asset_sort_timestamp,
        reverse=True,
    ):
        path = str(item.get("local_path") or "").strip()
        identity = os.path.normcase(os.path.realpath(path)) if path else ""
        prior_index = seen_paths.get(identity) if identity else None
        if prior_index is not None:
            existing = merged[prior_index]
            if item.get("asset_kind") == "zsxq_attachment" and existing.get("asset_kind") == "content_pipeline":
                merged[prior_index] = item
            continue
        if identity:
            seen_paths[identity] = len(merged)
        merged.append(item)
    return merged


def _attach_material_status(items):
    asset_ids = [str(item.get("id") or "").strip() for item in items if item.get("id")]
    if not asset_ids:
        return
    rows = (
        PublicationTask.query
        .with_entities(
            PublicationTask.id,
            PublicationTask.status,
            PublicationTask.current_step,
            PublicationTask.feature,
            PublicationTask.source_task_id,
            PublicationTask.report_audio_pdf_attachment_id,
            PublicationTask.created_at,
            PublicationTask.completed_at,
            PublicationTask.final_script_chars,
        )
        .filter(PublicationTask.feature.in_(MATERIAL_STATUS_FEATURES))
        .filter(PublicationTask.status != "canceled")
        .filter(or_(
            PublicationTask.source_task_id.in_(asset_ids),
            PublicationTask.report_audio_pdf_attachment_id.in_(asset_ids),
        ))
        .order_by(PublicationTask.created_at.desc())
        .all()
    )
    by_asset = {}
    for row in rows:
        key = str(row.source_task_id or row.report_audio_pdf_attachment_id or "").strip()
        by_asset.setdefault(key, []).append(row)
    for item in items:
        row = _best_material_task(by_asset.get(str(item.get("id") or "")))
        script_chars = int(row.final_script_chars or 0) if row else 0
        item["material_task_status"] = row.status if row else ""
        item["material_task_id"] = row.id if row else ""
        item["material_task_step"] = row.current_step if row else ""
        item["material_task_feature"] = row.feature if row else ""
        item["material_task_feature_label"] = _material_feature_label(row.feature) if row else ""
        item["material_has_script"] = script_chars > 0
        item["material_script_chars"] = script_chars
        item["material_completed_at"] = row.completed_at.isoformat() + "Z" if row and row.completed_at else None


def _result_file_path(task):
    try:
        return storage_path_for_job(task.id, task.created_at)
    except Exception:
        return None


def _task_result_from_db(task_id):
    row = db.session.query(PublicationTask.result).filter(PublicationTask.id == task_id).first()
    if not row:
        return {}
    result = row[0]
    return result if isinstance(result, dict) else {}


def _public_pdf_meta(pdf):
    if not isinstance(pdf, dict):
        return None
    return {
        key: pdf.get(key)
        for key in (
            "filename",
            "bytes",
            "source",
            "attachment_id",
            "file_id",
            "group_id",
            "tab_id",
            "topic_id",
            "topic_title",
            "tab_label",
            "preview_url",
        )
        if pdf.get(key) is not None
    }


def _request_payload():
    if request.is_json:
        data = request.get_json(silent=True) or {}
    else:
        data = request.form.to_dict(flat=True)
    if isinstance(data.get("metadata"), str):
        try:
            data["metadata"] = json.loads(data["metadata"] or "{}")
        except Exception:
            data["metadata"] = {}
    return data if isinstance(data, dict) else {}


def _parse_asset_ids(data):
    raw = data.get("pdf_asset_ids") or data.get("asset_ids") or data.get("pdf_asset_id") or data.get("asset_id") or []
    if isinstance(raw, str):
        value = raw.strip()
        if not value:
            return []
        try:
            parsed = json.loads(value)
            raw = parsed
        except Exception:
            raw = [item.strip() for item in value.split(",")]
    if not isinstance(raw, list):
        raw = [raw]
    return [str(item).strip() for item in raw if str(item).strip()]


def _parse_followups(data):
    raw = data.get("followups") or []
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            raw = parsed
        except Exception:
            raw = [line.strip() for line in raw.splitlines()]
    if not isinstance(raw, list):
        raw = [raw]
    return [str(item).strip() for item in raw if str(item).strip()]


def _resolve_pdf_path(value):
    raw = str(value or "").strip()
    if not raw:
        return None
    path = Path(raw)
    candidates = [path]
    if not path.is_absolute():
        candidates.append(Path(__file__).resolve().parents[3] / raw)
    for candidate in candidates:
        try:
            resolved = candidate.expanduser().resolve()
        except OSError:
            continue
        if resolved.exists() and resolved.is_file() and resolved.suffix.lower() == ".pdf":
            return resolved
    return None


def _visible_user_ids(current_user_or_id):
    raw = getattr(current_user_or_id, "id", current_user_or_id)
    user_id = str(raw or "").strip()
    values = {SYSTEM_USER_ID}
    if user_id:
        values.add(user_id)
    return values


def _normalize_search_query(value, max_length=80):
    return " ".join(str(value or "").strip().split())[:max_length]


def _escape_like(value):
    return str(value or "").replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _max_pdf_mb():
    try:
        return max(1, int(os.getenv("REPORT_SCRIPT_MATERIAL_MAX_PDF_MB", os.getenv("GEMINI_BROWSER_MAX_PDF_MB", "200"))))
    except (TypeError, ValueError):
        return 200


def _max_pdf_bytes():
    return _max_pdf_mb() * 1024 * 1024


def _bounded_int(value, default, lower, upper):
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(lower, min(upper, number))


def _truthy(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled", "y"}


def _isoformat_z(value):
    return value.isoformat() + "Z" if hasattr(value, "isoformat") else value
