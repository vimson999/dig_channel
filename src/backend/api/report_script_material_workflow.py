import json
import os
from datetime import datetime
from pathlib import Path

from flask import Blueprint, jsonify, request, send_file
from sqlalchemy import and_, inspect as sqlalchemy_inspect, or_
from sqlalchemy.orm import load_only
from sqlalchemy.orm.attributes import flag_modified

from celery_config import celery_app
from models import db
from models.task import PublicationTask
from models.zsxq_sync import ZsxqAttachment, ZsxqTopic
from services import gemini_browser
from services.report_script_material_workflow import (
    DEFAULT_INITIAL_PROMPT,
    DEFAULT_REVIEW_PROMPT,
    FEATURE_NAME,
    QUEUE_NAME,
    status_payload as workflow_status_payload,
    storage_path_for_job,
)
from services.report_audio_workflow import clean_final_script_for_tts
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
        for row in rows:
            item = _pdf_asset_payload(row)
            if item:
                valid_items.append(item)
            else:
                skipped_missing += 1
        if len(rows) < batch_limit:
            break

    start = (page - 1) * per_page
    end = start + per_page
    items = valid_items[start:end]
    _attach_material_status(items)
    return jsonify({
        "success": True,
        "data": {
            "items": items,
            "pagination": {
                "page": page,
                "per_page": per_page,
                "has_more": len(valid_items) > end or scanned >= scan_limit,
                "skipped_missing": skipped_missing,
                "scanned": scanned,
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
        pdf = _pdf_meta_from_zsxq_attachment(asset_id)
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
    asset_ids = _parse_asset_ids(data)
    if not asset_ids:
        return jsonify({"success": False, "message": "请选择至少 1 份 PDF"}), 400
    if len(asset_ids) > 50:
        return jsonify({"success": False, "message": "单次最多投递 50 份 PDF"}), 400

    prompt = str(data.get("prompt") or DEFAULT_INITIAL_PROMPT).strip()
    review_prompt = str(data.get("review_prompt") or DEFAULT_REVIEW_PROMPT).strip()
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
    task = _get_owned_task(current_user, job_id, include_result=True)
    if not task:
        return jsonify({"success": False, "message": "任务不存在"}), 404
    payload = _job_payload(task, include_detail=True)
    return jsonify({
        "success": True,
        "job_id": payload.get("job_id"),
        "status": payload.get("status"),
        "data": payload,
    })


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


def _create_task_for_asset(asset_id, *, user_id, prompt, review_prompt, followups, gemini_url, metadata):
    pdf = _pdf_meta_from_zsxq_attachment(asset_id)
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
            "source_feature": "zsxq_attachment",
            "source_task_id": asset_id,
            FEATURE_NAME: {
                "prompt": prompt,
                "review_prompt": review_prompt,
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


def _pdf_meta_from_zsxq_attachment(asset_id):
    attachment = ZsxqAttachment.query.get(str(asset_id))
    if not attachment:
        raise ValueError("PDF 资产不存在")
    if attachment.download_status != "downloaded":
        raise ValueError("选择的同步 PDF 还没有下载完成")
    path = _resolve_pdf_path(attachment.local_path)
    if not path:
        raise ValueError("选择的同步 PDF 本地文件不存在")
    size = path.stat().st_size
    if size <= 0:
        raise ValueError("选择的同步 PDF 文件为空")
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
        "file_id": attachment.file_id,
        "group_id": attachment.group_id,
        "tab_id": attachment.tab_id,
        "topic_id": attachment.topic_id,
        "topic_title": topic.title if topic else None,
        "tab_label": topic.tab_label if topic else None,
        "preview_url": f"/api/report-script-material-workflows/pdf-assets/{attachment.id}/preview",
    }


def _pdf_asset_payload(row):
    path = _resolve_pdf_path(row.local_path)
    if not path:
        return None
    try:
        size = path.stat().st_size
    except OSError:
        return None
    if size <= 0:
        return None
    return {
        "id": row.attachment_id,
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
