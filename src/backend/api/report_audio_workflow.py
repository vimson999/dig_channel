import json
import os
import time
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from threading import Lock

from flask import Blueprint, jsonify, request, send_file
from sqlalchemy import and_, or_
from sqlalchemy.orm import load_only
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
    pdf_first_page_image_path_for_job,
    render_pdf_first_page_image,
    status_payload as workflow_status_payload,
)
from services.thumbnail_service import thumbnail_service
from tasks.publisher import run_publication_pipeline
from tasks.report_audio_workflow import (
    _publisher_cta,
    _publisher_date_text,
    _publisher_main_title,
    _publisher_script_subtitles,
    _publisher_sub_burn_enabled,
    _publisher_sub_title,
    _publisher_video_transform,
    process_report_audio_workflow_job,
)
from utils.auth_utils import require_login


report_audio_workflow_bp = Blueprint("report_audio_workflow", __name__, url_prefix="/api/report-audio-workflows")
PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _float_env(name, default):
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return float(default)


def _normalize_search_query(value, max_length=80):
    return " ".join(str(value or "").strip().split())[:max_length]


def _escape_like(value):
    return (
        str(value or "")
        .replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )


REPORT_AUDIO_JOBS_CACHE_TTL_SECONDS = max(0.0, _float_env("REPORT_AUDIO_JOBS_CACHE_TTL", 15))
_REPORT_AUDIO_JOBS_CACHE = {}
_REPORT_AUDIO_JOBS_CACHE_LOCK = Lock()

STATUS_MAP = {
    "pending": "queued",
    "processing": "running",
    "completed": "succeeded",
    "failed": "failed",
    "canceled": "canceled",
}
SYSTEM_USER_ID = "system"
TERMINAL_STATUSES = {"completed", "failed", "canceled"}
VERTICAL_BACKFILL_SOURCE = "report_audio_workflow_vertical_backfill"


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


def _clear_jobs_summary_cache(user_id=None):
    if not _REPORT_AUDIO_JOBS_CACHE:
        return
    user_id = str(user_id or "").strip()
    with _REPORT_AUDIO_JOBS_CACHE_LOCK:
        if not user_id:
            _REPORT_AUDIO_JOBS_CACHE.clear()
            return
        for key in list(_REPORT_AUDIO_JOBS_CACHE):
            if key[0] == user_id:
                _REPORT_AUDIO_JOBS_CACHE.pop(key, None)


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
    scope = (request.args.get("scope") or "all").strip().lower()
    hot_download_threshold = 30

    topic_join = (
        (ZsxqTopic.group_id == ZsxqAttachment.group_id)
        & (ZsxqTopic.tab_id == ZsxqAttachment.tab_id)
        & (ZsxqTopic.topic_id == ZsxqAttachment.topic_id)
    )
    query = (
        db.session.query(
            ZsxqAttachment.id.label("attachment_id"),
            ZsxqAttachment.name.label("attachment_name"),
            ZsxqAttachment.download_count.label("download_count"),
            ZsxqAttachment.local_path.label("local_path"),
            ZsxqAttachment.updated_at.label("attachment_updated_at"),
            ZsxqAttachment.created_at.label("attachment_created_at"),
            ZsxqAttachment.group_id.label("group_id"),
            ZsxqAttachment.tab_id.label("tab_id"),
            ZsxqAttachment.topic_id.label("topic_id"),
            ZsxqTopic.title.label("topic_title"),
            ZsxqTopic.tab_label.label("tab_label"),
            ZsxqTopic.author_name.label("topic_author"),
            ZsxqTopic.create_time.label("topic_create_time"),
            ZsxqTopic.first_synced_at.label("topic_first_synced_at"),
            ZsxqTopic.last_synced_at.label("topic_last_synced_at"),
        )
        .outerjoin(ZsxqTopic, topic_join)
        .filter(ZsxqAttachment.download_status == "downloaded")
        .filter(ZsxqAttachment.local_path.isnot(None))
        .filter(or_(ZsxqAttachment.name.ilike("%.pdf"), ZsxqAttachment.local_path.ilike("%.pdf")))
    )
    if scope in {"hot", "popular"}:
        query = query.filter(ZsxqAttachment.download_count >= hot_download_threshold)
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

    target_count = page * per_page + 1
    chunk_size = min(max(per_page * 4, 40), 120)
    scan_limit = min(max(target_count * 5, 120), 2000)
    sync_time_order = db.func.coalesce(
        ZsxqTopic.last_synced_at,
        ZsxqAttachment.updated_at,
    )
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
            item = _pdf_asset_row_payload(row)
            if item:
                valid_items.append(item)
            else:
                skipped_missing += 1
        if len(rows) < batch_limit:
            break

    start = (page - 1) * per_page
    end = start + per_page
    items = valid_items[start:end]
    has_more = len(valid_items) > end or scanned >= scan_limit
    return jsonify({
        "success": True,
        "data": {
            "items": items,
            "pagination": {
                "page": page,
                "per_page": per_page,
                "has_more": has_more,
                "skipped_missing": skipped_missing,
                "scanned": scanned,
                "scope": "hot" if scope in {"hot", "popular"} else "all",
                "hot_download_threshold": hot_download_threshold,
                "order": "published_desc",
            },
        },
        "items": items,
    })


@report_audio_workflow_bp.route("/pdf-assets/workflow-stats", methods=["GET"])
@require_login
def pdf_asset_workflow_stats(current_user):
    raw_ids = request.args.get("ids") or ""
    ids = [item.strip() for item in raw_ids.split(",") if item.strip()]
    ids = ids[:50]
    stats = _pdf_asset_workflow_stats(
        ids,
        user_id=str(getattr(current_user, "id", None)),
    )
    return jsonify({
        "success": True,
        "data": {"stats": stats},
        "stats": stats,
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
    script_material_only = _parse_script_material_only(data)
    if script_material_only:
        video_options.update({
            "enabled": False,
            "vertical_enabled": False,
            "bilibili_enabled": False,
        })
    gemini_url = (data.get("gemini_url") or data.get("url") or "").strip()
    if gemini_url and not gemini_url.startswith(("http://", "https://")):
        return jsonify({"success": False, "message": "gemini_url 必须是 http(s) 地址"}), 400

    gemini_status = gemini_browser.status_payload()
    doubao_status = doubao_browser_tts.status_payload()
    if not gemini_status.get("configured"):
        return jsonify({"success": False, "message": "研报 PDF Gem 解析未配置", "data": {"report_pdf_gemini": gemini_status}}), 400
    if not script_material_only and not doubao_status.get("configured"):
        return jsonify({"success": False, "message": "豆包浏览器朗读未配置", "data": {"doubao_browser_tts": doubao_status}}), 400

    initial_result = {
        "metadata": metadata,
        "pdf": None,
        "children": {},
        "structured_result": None,
        "audio": None,
        "video": None,
        "logs": [],
    }
    if script_material_only:
        initial_result["script_material"] = {"status": "pending"}

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
                "script_material_only": script_material_only,
                "video": video_options,
                "pdf": None,
                "pdf_path": None,
            },
        },
        result=initial_result,
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
        _clear_jobs_summary_cache(user_id)
        return jsonify({"success": False, "message": task.error_message}), 502

    task.celery_task_id = celery_task.id
    db.session.commit()
    _clear_jobs_summary_cache(user_id)

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
    search = _normalize_search_query(
        request.args.get("q") or request.args.get("search") or request.args.get("job_search")
    )
    include_detail = str(request.args.get("detail") or "").lower() in {"1", "true", "yes"}
    user_id = str(getattr(current_user, "id", None))

    visible_user_ids = _visible_user_ids(current_user)
    cache_key = ("+".join(sorted(visible_user_ids)), page, per_page, status, search)
    if not include_detail and REPORT_AUDIO_JOBS_CACHE_TTL_SECONDS > 0:
        with _REPORT_AUDIO_JOBS_CACHE_LOCK:
            cached = _REPORT_AUDIO_JOBS_CACHE.get(cache_key)
            if cached and cached["expires_at"] > time.time():
                return jsonify(deepcopy(cached["payload"]))

    query = PublicationTask.query.filter(
        PublicationTask.feature == FEATURE_NAME,
        PublicationTask.user_id.in_(visible_user_ids),
    )
    if status:
        reverse_status = {value: key for key, value in STATUS_MAP.items()}
        query = query.filter(PublicationTask.status == reverse_status.get(status, status))
    if search:
        pattern = f"%{_escape_like(search)}%"
        query = query.filter(or_(
            PublicationTask.task_title.ilike(pattern, escape="\\"),
            PublicationTask.report_audio_pdf_filename.ilike(pattern, escape="\\"),
            PublicationTask.report_audio_pdf_topic_title.ilike(pattern, escape="\\"),
            PublicationTask.report_audio_result_pdf_filename.ilike(pattern, escape="\\"),
            PublicationTask.report_audio_result_pdf_topic_title.ilike(pattern, escape="\\"),
            PublicationTask.report_audio_metadata_title.ilike(pattern, escape="\\"),
            PublicationTask.report_audio_video_title.ilike(pattern, escape="\\"),
            PublicationTask.report_audio_cover_title.ilike(pattern, escape="\\"),
            PublicationTask.report_audio_structured_title.ilike(pattern, escape="\\"),
        ))

    ordered = query.order_by(PublicationTask.created_at.desc())
    if include_detail:
        pagination = ordered.paginate(
            page=page,
            per_page=per_page,
            error_out=False,
        )
        items = [_job_payload(task) for task in pagination.items]
        total = pagination.total
        pages = pagination.pages
    else:
        total = query.with_entities(PublicationTask.id).order_by(None).count()
        offset = (page - 1) * per_page
        rows = (
            ordered.with_entities(
                PublicationTask.id.label("id"),
                PublicationTask.status.label("status"),
                PublicationTask.current_step.label("current_step"),
                PublicationTask.progress.label("progress"),
                PublicationTask.error_message.label("error_message"),
                PublicationTask.created_at.label("created_at"),
                PublicationTask.completed_at.label("completed_at"),
                PublicationTask.report_audio_started_at.label("started_at"),
                PublicationTask.report_audio_completed_at.label("result_completed_at"),
                PublicationTask.report_audio_error.label("result_error"),
                PublicationTask.report_audio_output_path.label("output_path"),
                PublicationTask.report_audio_pdf_filename.label("request_pdf_filename"),
                PublicationTask.report_audio_pdf_bytes.label("request_pdf_bytes"),
                PublicationTask.report_audio_pdf_topic_title.label("request_pdf_topic_title"),
                PublicationTask.report_audio_result_pdf_filename.label("result_pdf_filename"),
                PublicationTask.report_audio_result_pdf_bytes.label("result_pdf_bytes"),
                PublicationTask.report_audio_result_pdf_topic_title.label("result_pdf_topic_title"),
                PublicationTask.report_audio_metadata_title.label("metadata_title"),
                PublicationTask.report_audio_video_title.label("video_title"),
                PublicationTask.report_audio_cover_title.label("cover_title"),
                PublicationTask.report_audio_cover_subtitle.label("cover_subtitle"),
                PublicationTask.report_audio_structured_title.label("structured_title"),
                PublicationTask.report_audio_wav_path.label("audio_wav_path"),
                PublicationTask.report_audio_seconds.label("audio_seconds"),
                PublicationTask.report_audio_audio_bytes.label("audio_bytes"),
                PublicationTask.report_audio_video_enabled.label("video_enabled"),
                PublicationTask.report_audio_video_status.label("video_status"),
                PublicationTask.report_audio_video_publisher_task_id.label("video_publisher_task_id"),
                PublicationTask.report_audio_video_message.label("video_message"),
                PublicationTask.report_audio_video_path.label("video_path"),
                PublicationTask.report_audio_cover_path.label("cover_path"),
            )
            .offset(offset)
            .limit(per_page)
            .all()
        )
        child_summaries = _child_summaries_for_parent_ids([row.id for row in rows])
        items = [_job_summary_payload(row, child_summaries.get(row.id)) for row in rows]
        pages = ((total + per_page - 1) // per_page) if total else 0
    payload = {
        "success": True,
        "data": {
            "items": items,
            "pagination": {
                "page": page,
                "per_page": per_page,
                "total": total,
                "pages": pages,
                "q": search,
            },
        },
        "items": items,
    }
    if not include_detail and REPORT_AUDIO_JOBS_CACHE_TTL_SECONDS > 0:
        with _REPORT_AUDIO_JOBS_CACHE_LOCK:
            _REPORT_AUDIO_JOBS_CACHE[cache_key] = {
                "expires_at": time.time() + REPORT_AUDIO_JOBS_CACHE_TTL_SECONDS,
                "payload": deepcopy(payload),
            }
    return jsonify(payload)


@report_audio_workflow_bp.route("/jobs/<job_id>", methods=["GET"])
@require_login
def get_job(current_user, job_id):
    task = _get_owned_task(current_user, job_id)
    if not task:
        return jsonify({"success": False, "message": "任务不存在"}), 404
    payload = _job_payload(task, include_raw=True)
    return jsonify({"success": True, **payload, "data": payload})


@report_audio_workflow_bp.route("/jobs/<job_id>/children/<child_id>", methods=["GET"])
@require_login
def get_child_job(current_user, job_id, child_id):
    parent = _get_owned_task(current_user, job_id)
    if not parent:
        return jsonify({"success": False, "message": "任务不存在"}), 404

    child = PublicationTask.query.options(
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
            PublicationTask.parent_task_id,
            PublicationTask.source_task_id,
            PublicationTask.created_at,
            PublicationTask.completed_at,
        )
    ).get(child_id)
    if not child or str(parent.id) not in {str(child.parent_task_id or ""), str(child.source_task_id or "")}:
        return jsonify({"success": False, "message": "子任务不存在"}), 404

    payload = _child_task_summary(child)
    payload["raw_params"] = child.params if isinstance(child.params, dict) else {}
    payload["raw_result"] = child.result if isinstance(child.result, dict) else {}
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


@report_audio_workflow_bp.route("/jobs/<job_id>/download-vertical-video", methods=["GET"])
@require_login
def download_vertical_video(current_user, job_id):
    task = _get_owned_task(current_user, job_id)
    if not task:
        return jsonify({"success": False, "message": "任务不存在"}), 404
    video_path = _vertical_video_path_for_task(task)
    if not video_path or not os.path.exists(video_path):
        return jsonify({"success": False, "message": "竖屏视频文件不存在"}), 404
    return send_file(video_path, mimetype="video/mp4", as_attachment=True, download_name=f"{task.id}-vertical.mp4")


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


@report_audio_workflow_bp.route("/jobs/<job_id>/regenerate-cover", methods=["POST"])
@require_login
def regenerate_cover(current_user, job_id):
    task = _get_owned_task(current_user, job_id)
    if not task:
        return jsonify({"success": False, "message": "任务不存在"}), 404
    data = request.get_json(silent=True) or {}
    title = _normalize_cover_text(data.get("title") or data.get("main_title"), 160)
    subtitle = _normalize_cover_text(data.get("subtitle") or data.get("sub_title"), 220)
    if not title:
        return jsonify({"success": False, "message": "请填写封皮标题"}), 400

    try:
        cover = _regenerate_cover_for_task(task, title=title, subtitle=subtitle)
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    except Exception as exc:
        db.session.rollback()
        return jsonify({"success": False, "message": f"封皮重生成失败: {exc}"}), 502

    _clear_jobs_summary_cache(getattr(current_user, "id", None))
    payload = _job_payload(task)
    return jsonify({
        "success": True,
        "message": "封皮已重新生成",
        "cover": cover,
        **payload,
        "data": payload,
    })


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
    preview = _truthy(request.args.get("preview"), False)
    return send_file(
        pdf_path,
        mimetype="application/pdf",
        as_attachment=not preview,
        download_name=pdf.get("filename") or f"{task.id}.pdf",
        conditional=True,
    )


@report_audio_workflow_bp.route("/jobs/<job_id>/generate-vertical-video", methods=["POST"])
@require_login
def generate_vertical_video(current_user, job_id):
    task = _get_owned_task(current_user, job_id)
    if not task:
        return jsonify({"success": False, "message": "任务不存在"}), 404

    existing_path = _vertical_video_path_for_task(task)
    if existing_path:
        payload = _job_payload(task)
        return jsonify({"success": True, "message": "竖屏 MP4 已存在", **payload, "data": payload})

    existing_child = _latest_vertical_video_child(task.id)
    if existing_child and existing_child.status not in TERMINAL_STATUSES:
        payload = _job_payload(task)
        return jsonify({"success": True, "message": "竖屏 MP4 已在生成队列中", **payload, "data": payload})

    try:
        child = _create_vertical_video_backfill_task(task)
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    except Exception as exc:
        db.session.rollback()
        return jsonify({"success": False, "message": f"竖屏 MP4 入队失败: {exc}"}), 502

    _clear_jobs_summary_cache(getattr(current_user, "id", None))
    payload = _job_payload(task)
    return jsonify({
        "success": True,
        "message": "竖屏 MP4 已提交生成",
        "vertical_video_task": _child_task_summary(child),
        **payload,
        "data": payload,
    })


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
    _clear_jobs_summary_cache(getattr(current_user, "id", None))

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
        _clear_jobs_summary_cache(getattr(current_user, "id", None))
        return jsonify({"success": False, "message": task.error_message}), 502

    task.celery_task_id = celery_task.id
    db.session.commit()
    _clear_jobs_summary_cache(getattr(current_user, "id", None))
    payload = _job_payload(task)
    return jsonify({"success": True, "message": "任务已从失败处续跑", **payload, "data": payload})


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


def _normalize_cover_text(value, max_length):
    lines = [
        " ".join(line.split())
        for line in str(value or "").replace("\r", "\n").split("\n")
        if line.strip()
    ]
    text = "\n".join(lines).strip()
    return text[:max_length].rstrip()


def _regenerate_cover_for_task(task, *, title, subtitle=""):
    result = task.result if isinstance(task.result, dict) else {}
    params = task.params if isinstance(task.params, dict) else {}
    payload = params.get("report_audio_workflow") if isinstance(params.get("report_audio_workflow"), dict) else {}
    video_options = payload.get("video") if isinstance(payload.get("video"), dict) else {}
    pdf_screenshot = _ensure_pdf_first_page_image(task)
    screenshot_path = pdf_screenshot.get("path") if isinstance(pdf_screenshot, dict) else None
    if not screenshot_path or not os.path.exists(screenshot_path):
        raise ValueError("PDF 首屏图不存在，无法重新生成封皮")

    date_text = str(video_options.get("date_text") or "").strip() or _publisher_date_text(task.created_at)
    tag_text = str(video_options.get("tag_text") or "").strip() or "研报播客"
    try:
        overlay_opacity = float(video_options.get("overlay_opacity") or 0.5)
    except (TypeError, ValueError):
        overlay_opacity = 0.5
    filename = f"report_audio_cover_{task.id[:8]}_{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}.jpg"
    cover_path = thumbnail_service.generate_thumbnail(
        main_title=title,
        sub_title=subtitle,
        date_text=date_text,
        tag_text=tag_text,
        bg_preset_index=_bounded_int(video_options.get("bg_index"), 0, 0, 1000),
        layout="left",
        output_filename=filename,
        custom_bg_path=screenshot_path,
        overlay_opacity=overlay_opacity,
        transform=video_options.get("cover_transform") or {"x": 0, "y": 0, "scale": 1},
        mode="screenshot",
    )
    if not cover_path or not os.path.exists(cover_path):
        raise RuntimeError("封皮文件未生成")

    refreshed = task.result if isinstance(task.result, dict) else result
    updated_result = dict(refreshed)
    video = dict(updated_result.get("video") or {}) if isinstance(updated_result.get("video"), dict) else {}
    structured = (
        dict(updated_result.get("structured_result") or {})
        if isinstance(updated_result.get("structured_result"), dict)
        else {}
    )
    previous_cover = {
        "cover_path": video.get("cover_path"),
        "title": structured.get("cover_title") or video.get("cover_title"),
        "subtitle": structured.get("cover_subtitle") or video.get("cover_subtitle"),
        "replaced_at": datetime.utcnow().isoformat() + "Z",
    }
    history = list(updated_result.get("cover_regenerations") or [])
    if previous_cover.get("cover_path"):
        history.append(previous_cover)

    now = datetime.utcnow().isoformat() + "Z"
    video.update({
        "enabled": True if video.get("enabled") is None else video.get("enabled"),
        "cover_path": cover_path,
        "cover_title": title,
        "cover_subtitle": subtitle,
        "cover_regenerated_at": now,
    })
    structured["cover_title"] = title
    structured["cover_subtitle"] = subtitle
    updated_result["video"] = video
    updated_result["structured_result"] = structured
    updated_result["cover_regenerations"] = history[-20:]

    task.result = updated_result
    flag_modified(task, "result")
    db.session.commit()
    return {
        "path": cover_path,
        "bytes": os.path.getsize(cover_path),
        "title": title,
        "subtitle": subtitle,
        "download_url": f"/api/report-audio-workflows/jobs/{task.id}/download-cover",
        "updated_at": now,
    }


def _create_vertical_video_backfill_task(parent):
    result = parent.result if isinstance(parent.result, dict) else {}
    params = parent.params if isinstance(parent.params, dict) else {}
    payload = params.get("report_audio_workflow") if isinstance(params.get("report_audio_workflow"), dict) else {}
    audio = result.get("audio") if isinstance(result.get("audio"), dict) else {}
    audio_path = audio.get("wav_path")
    if not audio_path or not os.path.exists(audio_path):
        raise ValueError("音频文件不存在，无法后补竖屏 MP4")

    structured = result.get("structured_result") if isinstance(result.get("structured_result"), dict) else {}
    pdf = payload.get("pdf") if isinstance(payload.get("pdf"), dict) else {}
    if not pdf:
        pdf = result.get("pdf") if isinstance(result.get("pdf"), dict) else {}
    pdf_screenshot = _ensure_pdf_first_page_image(parent)
    screenshot_path = pdf_screenshot.get("path") if isinstance(pdf_screenshot, dict) else None
    if not screenshot_path or not os.path.exists(screenshot_path):
        raise ValueError("PDF 首屏图不存在，无法后补竖屏 MP4")

    video_options = payload.get("video") if isinstance(payload.get("video"), dict) else {}
    main_title = _publisher_main_title(structured, video_options)
    sub_title = _publisher_sub_title(structured, video_options)
    date_text = (
        str(video_options.get("date_text") or "").strip()
        or _publisher_date_text(parent.created_at)
    )

    metadata = {
        "source": VERTICAL_BACKFILL_SOURCE,
        "parent_task_id": parent.id,
        "pdf": pdf,
    }
    task_params = {
        "feature": "publisher",
        "source_feature": FEATURE_NAME,
        "source_task_id": parent.id,
        "audio_segments": [
            {
                "path": audio_path,
                "subtitle": _publisher_script_subtitles(structured, audio),
                "start": 0,
                "end_offset": 0,
                "speed": 1.0,
            }
        ],
        "video": {
            "enabled": False,
            "sub_burn_enabled": _publisher_sub_burn_enabled(video_options),
            "asr_model": str(video_options.get("asr_model") or "base"),
            "video_path": "",
            "audio_path": audio_path,
            "image_path": screenshot_path,
            "audio_start": 0,
            "audio_end_offset": 0,
            "transform": _publisher_video_transform(video_options),
            "cta": _publisher_cta(video_options),
        },
        "vertical_video": {
            "enabled": True,
            "asr_model": str(video_options.get("asr_model") or "base"),
            "audio_path": audio_path,
            "image_path": screenshot_path,
            "subtitles": [],
        },
        "vertical_video_2": {"enabled": False, "image_path": ""},
        "vertical_thumbnail": {
            "enabled": True,
            "main_title": main_title,
            "sub_title": sub_title,
            "date_text": date_text,
            "screenshot_path": screenshot_path,
        },
        "bilibili": {
            "enabled": False,
            "title": " ".join(str(main_title or "").split())[:80],
        },
        "youtube": {"enabled": False},
        "metadata": metadata,
    }

    child = PublicationTask(
        status="pending",
        current_step="queued",
        progress=0,
        params=task_params,
        result={
            "metadata": metadata,
            "source_task_id": parent.id,
            "pdf_first_page_image": pdf_screenshot,
            "video_path": None,
            "vertical_video_path": None,
            "cover_path": None,
        },
    )
    db.session.add(child)
    db.session.flush()
    child_id = child.id
    db.session.commit()

    try:
        async_result = run_publication_pipeline.apply_async(args=[child_id], queue="render", retry=False)
    except Exception:
        child = db.session.get(PublicationTask, child_id)
        if child:
            child.status = "failed"
            child.current_step = "failed"
            child.completed_at = datetime.utcnow()
            child.error_message = "竖屏 MP4 入队失败"
            db.session.commit()
        raise

    child = db.session.get(PublicationTask, child_id)
    child.celery_task_id = async_result.id
    db.session.commit()
    return child


def _ensure_pdf_first_page_image(task):
    result = task.result if isinstance(task.result, dict) else {}
    video = result.get("video") if isinstance(result.get("video"), dict) else {}
    candidates = [
        result.get("pdf_first_page_image"),
        video.get("pdf_first_page_image"),
    ]
    for image in candidates:
        if isinstance(image, dict) and _path_exists(image.get("path")):
            return image

    params = task.params if isinstance(task.params, dict) else {}
    payload = params.get("report_audio_workflow") if isinstance(params.get("report_audio_workflow"), dict) else {}
    request_pdf = payload.get("pdf") if isinstance(payload.get("pdf"), dict) else {}
    result_pdf = result.get("pdf") if isinstance(result.get("pdf"), dict) else {}
    pdf_path = payload.get("pdf_path") or request_pdf.get("path") or result_pdf.get("path")
    if not pdf_path or not os.path.exists(pdf_path):
        raise ValueError("PDF 文件不存在，无法生成竖屏物料")

    image = render_pdf_first_page_image(
        pdf_path,
        pdf_first_page_image_path_for_job(task.id, task.created_at),
    )
    updated_result = dict(result)
    updated_result["pdf_first_page_image"] = image
    task.result = updated_result
    flag_modified(task, "result")
    db.session.commit()
    return image


def _latest_vertical_video_child(parent_id):
    candidates = (
        PublicationTask.query.options(
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
                PublicationTask.parent_task_id,
                PublicationTask.source_task_id,
                PublicationTask.created_at,
                PublicationTask.completed_at,
            )
        )
        .filter(PublicationTask.feature == "publisher")
        .filter(PublicationTask.source_task_id == parent_id)
        .order_by(PublicationTask.created_at.desc())
        .limit(30)
        .all()
    )
    vertical_children = [task for task in candidates if _is_vertical_video_candidate_child(task)]
    if not vertical_children:
        return None
    return max(vertical_children, key=_vertical_child_priority)


def _is_vertical_backfill_child(task):
    params = task.params if isinstance(task.params, dict) else {}
    result = task.result if isinstance(task.result, dict) else {}
    metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
    if not metadata:
        metadata = params.get("metadata") if isinstance(params.get("metadata"), dict) else {}
    if metadata.get("source") == VERTICAL_BACKFILL_SOURCE:
        return True
    video = params.get("video") if isinstance(params.get("video"), dict) else {}
    vertical = params.get("vertical_video") if isinstance(params.get("vertical_video"), dict) else {}
    return _truthy(vertical.get("enabled"), False) and not _truthy(video.get("enabled"), False)


def _vertical_video_path_from_child(child):
    result = child.result if isinstance(child.result, dict) else {}
    vertical_path = result.get("vertical_video_path")
    if vertical_path:
        return vertical_path
    if _is_vertical_backfill_child(child):
        return result.get("video_path")
    return None


def _is_vertical_video_candidate_child(task):
    if _is_vertical_backfill_child(task):
        return True
    if _path_exists(_vertical_video_path_from_child(task)):
        return True
    params = task.params if isinstance(task.params, dict) else {}
    vertical = params.get("vertical_video") if isinstance(params.get("vertical_video"), dict) else {}
    return _truthy(vertical.get("enabled"), False)


def _vertical_video_path_for_task(task):
    result = task.result if isinstance(task.result, dict) else {}
    video = result.get("video") if isinstance(result.get("video"), dict) else {}
    if _path_exists(video.get("vertical_video_path")):
        return video.get("vertical_video_path")
    child = _latest_vertical_video_child(task.id)
    if child and child.status == "completed":
        path = _vertical_video_path_from_child(child)
        if _path_exists(path):
            return path
    return None


def _video_result_with_vertical_backfill(task):
    result = task.result if isinstance(task.result, dict) else {}
    video = dict(result.get("video") or {}) if isinstance(result.get("video"), dict) else {}
    child = _latest_vertical_video_child(task.id)
    if not child:
        return video

    child_path = _vertical_video_path_from_child(child)
    if child.status == "completed" and _path_exists(child_path):
        video["enabled"] = True
        video["status"] = video.get("status") or "completed"
        video["vertical_enabled"] = True
        video["vertical_video_path"] = child_path
        video["vertical_publisher_task_id"] = child.id
        child_result = child.result if isinstance(child.result, dict) else {}
        if isinstance(child_result.get("pdf_first_page_image"), dict) and not video.get("pdf_first_page_image"):
            video["pdf_first_page_image"] = child_result.get("pdf_first_page_image")
    return video


def _vertical_child_summary(task, parent_job_id=None):
    if not task:
        return None
    payload = _child_task_summary(task)
    path = _vertical_video_path_from_child(task)
    if task.status == "completed" and _path_exists(path):
        payload["vertical_video"] = {
            "bytes": os.path.getsize(path),
            "download_url": f"/api/report-audio-workflows/jobs/{parent_job_id or task.source_task_id}/download-vertical-video",
        }
    return payload


def _job_payload(task, include_raw=False):
    result = task.result or {}
    params = task.params or {}
    request_payload = params.get("report_audio_workflow") or {}
    structured_result = result.get("structured_result") if isinstance(result.get("structured_result"), dict) else None
    audio = result.get("audio") or None
    status = STATUS_MAP.get(task.status, task.status or "queued")
    live_children = _live_child_tasks(task.id)
    live_doubao_children = [
        child for child in live_children if child.feature == doubao_browser_tts.FEATURE_NAME
    ]
    live_doubao_items = [
        _child_task_summary_row(child)
        for child in live_doubao_children
    ]
    video_result = _video_result_with_vertical_backfill(task)
    vertical_child = _latest_vertical_video_child(task.id)
    children = dict(result.get("children") or {})
    if vertical_child:
        children["publisher_vertical_video"] = _vertical_child_summary(vertical_child, task.id)
    cover_subtitle = (structured_result or {}).get("cover_subtitle") or video_result.get("cover_subtitle") or ""
    public_pdf = _public_pdf(task.id, request_payload.get("pdf") or result.get("pdf"))
    request_metadata = request_payload.get("metadata") if isinstance(request_payload.get("metadata"), dict) else {}
    request_video = request_payload.get("video") if isinstance(request_payload.get("video"), dict) else {}
    detail_title = _first_compact_title(
        _display_title_from_structured(structured_result),
        request_video.get("title"),
        request_metadata.get("title"),
        (public_pdf or {}).get("filename"),
        (public_pdf or {}).get("topic_title"),
    )
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
            "pdf": public_pdf,
            "metadata": request_metadata,
        },
        "pdf": public_pdf,
        "children": children,
        "child_attempts": result.get("child_attempts") or {},
        "retry_state": result.get("retry_state") or {},
        "gemini": result.get("gemini"),
        "doubao_browser_tts": live_doubao_items or result.get("doubao_browser_tts") or [],
        "structured_result": structured_result,
        "script_material": result.get("script_material") if isinstance(result.get("script_material"), dict) else None,
        "title": detail_title,
        "cover_title": (structured_result or {}).get("cover_title") or "",
        "cover_subtitle": cover_subtitle,
        "tts_text": result.get("tts_text"),
        "audio": _public_audio(task.id, audio),
        "video": _public_video(task.id, video_result),
        "publisher_video": result.get("publisher_video"),
        "publisher_vertical_video": children.get("publisher_vertical_video"),
        "pdf_first_page_image": _public_image(task.id, result.get("pdf_first_page_image")),
        "download_audio_url": f"/api/report-audio-workflows/jobs/{task.id}/download-audio" if audio and audio.get("wav_path") else None,
        "download_result_url": f"/api/report-audio-workflows/jobs/{task.id}/download-result" if result.get("output_path") else None,
        "download_video_url": f"/api/report-audio-workflows/jobs/{task.id}/download-video" if _path_exists((result.get("video") or {}).get("video_path")) else None,
        "download_vertical_video_url": f"/api/report-audio-workflows/jobs/{task.id}/download-vertical-video" if _path_exists(video_result.get("vertical_video_path")) else None,
        "download_cover_url": f"/api/report-audio-workflows/jobs/{task.id}/download-cover" if _path_exists((result.get("video") or {}).get("cover_path")) else None,
        "metadata": result.get("metadata") or request_payload.get("metadata") or {},
        "notifications": result.get("notifications") or {},
        "logs": result.get("logs") or [],
    }
    if include_raw:
        payload["raw_params"] = params
        payload["raw_result"] = result
    return payload


def _number_or_none(value, cast=float):
    if value is None or value == "":
        return None
    try:
        return cast(value)
    except (TypeError, ValueError):
        return None


def _bool_or_none(value):
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    return str(value).lower() in {"1", "true", "yes", "on"}


def _first_compact_title(*values):
    generic_fallback = ""
    for value in values:
        title = _compact_title(value)
        if title:
            if _is_generic_report_title(title):
                generic_fallback = generic_fallback or title
                continue
            return title
    return generic_fallback


def _is_generic_report_title(value):
    normalized = "".join(str(value or "").strip().split()).lstrip("#")
    return normalized in {"外资研报", "研报", "PDF", "pdf"}


def _job_summary_payload(row, child_bundle=None):
    child_bundle = child_bundle or {}
    cover_subtitle = _normalize_cover_text(getattr(row, "cover_subtitle", "") or "", 220)
    pdf = {
        "filename": row.request_pdf_filename or row.result_pdf_filename,
        "bytes": _number_or_none(row.request_pdf_bytes or row.result_pdf_bytes, int),
        "topic_title": row.request_pdf_topic_title or row.result_pdf_topic_title,
    }
    pdf = {key: value for key, value in pdf.items() if value is not None}
    if pdf:
        pdf["download_url"] = f"/api/report-audio-workflows/jobs/{row.id}/input-pdf"
        pdf["preview_url"] = f"/api/report-audio-workflows/jobs/{row.id}/input-pdf?preview=1"

    structured_result = {
        "cover_title": row.cover_title,
        "cover_subtitle": cover_subtitle,
        "title": row.structured_title,
    }
    structured_result = {key: value for key, value in structured_result.items() if value}

    audio_payload = None
    if row.audio_wav_path:
        audio_payload = {
            "seconds": _number_or_none(row.audio_seconds),
            "bytes": _number_or_none(row.audio_bytes, int),
            "download_url": f"/api/report-audio-workflows/jobs/{row.id}/download-audio",
        }
        audio_payload = {key: value for key, value in audio_payload.items() if value is not None}

    video_payload = None
    if (
        row.video_enabled is not None
        or row.video_status
        or row.video_publisher_task_id
        or row.video_path
        or row.cover_path
    ):
        video_payload = {
            "enabled": _bool_or_none(row.video_enabled),
            "status": row.video_status,
            "publisher_task_id": row.video_publisher_task_id,
            "message": row.video_message,
            "cover_title": row.cover_title,
            "cover_subtitle": cover_subtitle,
        }
        if row.video_path and _path_exists(row.video_path):
            video_payload["video"] = {"download_url": f"/api/report-audio-workflows/jobs/{row.id}/download-video"}
        if row.cover_path:
            video_payload["cover"] = {"download_url": f"/api/report-audio-workflows/jobs/{row.id}/download-cover"}
        video_payload = {key: value for key, value in video_payload.items() if value is not None}

    title = _first_compact_title(
        row.cover_title,
        row.structured_title,
        row.video_title,
        row.metadata_title,
        row.request_pdf_filename,
        row.result_pdf_filename,
        row.request_pdf_topic_title,
        row.result_pdf_topic_title,
    )
    status = STATUS_MAP.get(row.status, row.status or "queued")
    result_error = row.result_error
    vertical_child = child_bundle.get("publisher_vertical_video") or {}
    vertical_video = vertical_child.get("vertical_video") if isinstance(vertical_child, dict) else None
    if vertical_video:
        video_payload = video_payload or {
            "enabled": _bool_or_none(row.video_enabled),
            "status": row.video_status,
            "publisher_task_id": row.video_publisher_task_id,
            "message": row.video_message,
            "cover_title": row.cover_title,
            "cover_subtitle": cover_subtitle,
        }
        video_payload["vertical_enabled"] = True
        video_payload["vertical_video"] = vertical_video
        video_payload = {key: value for key, value in video_payload.items() if value is not None}
    return {
        "job_id": row.id,
        "status": status,
        "step": row.current_step,
        "progress": row.progress or 0,
        "created_at": row.created_at.isoformat() + "Z" if row.created_at else None,
        "started_at": row.started_at,
        "completed_at": row.completed_at.isoformat() + "Z" if row.completed_at else row.result_completed_at,
        "error": row.error_message or result_error,
        "title": title,
        "request": {"pdf": pdf or None, "metadata": {"title": row.metadata_title} if row.metadata_title else {}},
        "pdf": pdf or None,
        "gemini": child_bundle.get("gemini"),
        "doubao_browser_tts": child_bundle.get("doubao_browser_tts") or [],
        "structured_result": structured_result or None,
        "cover_title": row.cover_title or "",
        "cover_subtitle": cover_subtitle,
        "audio": audio_payload,
        "video": video_payload,
        "publisher_video": child_bundle.get("publisher_video"),
        "publisher_vertical_video": vertical_child or None,
        "download_audio_url": f"/api/report-audio-workflows/jobs/{row.id}/download-audio" if row.audio_wav_path else None,
        "download_result_url": f"/api/report-audio-workflows/jobs/{row.id}/download-result" if row.output_path else None,
        "download_video_url": f"/api/report-audio-workflows/jobs/{row.id}/download-video" if row.video_path and _path_exists(row.video_path) else None,
        "download_vertical_video_url": f"/api/report-audio-workflows/jobs/{row.id}/download-vertical-video" if vertical_video else None,
        "download_cover_url": f"/api/report-audio-workflows/jobs/{row.id}/download-cover" if row.cover_path else None,
        "metadata": {"title": row.metadata_title} if row.metadata_title else {},
        "logs": [],
    }


def _live_child_tasks(parent_id):
    try:
        return (
            PublicationTask.query
            .with_entities(
                PublicationTask.id.label("id"),
                PublicationTask.celery_task_id.label("celery_task_id"),
                PublicationTask.status.label("status"),
                PublicationTask.current_step.label("current_step"),
                PublicationTask.progress.label("progress"),
                PublicationTask.error_message.label("error_message"),
                PublicationTask.feature.label("feature"),
                PublicationTask.parent_task_id.label("parent_task_id"),
                PublicationTask.source_task_id.label("source_task_id"),
                PublicationTask.task_title.label("task_title"),
                PublicationTask.task_video_path.label("task_video_path"),
                PublicationTask.report_audio_wav_path.label("audio_wav_path"),
                PublicationTask.report_audio_seconds.label("audio_seconds"),
                PublicationTask.report_audio_audio_bytes.label("audio_bytes"),
                PublicationTask.created_at.label("created_at"),
                PublicationTask.completed_at.label("completed_at"),
            )
            .filter(or_(PublicationTask.parent_task_id == parent_id, PublicationTask.source_task_id == parent_id))
            .order_by(PublicationTask.created_at.asc())
            .all()
        )
    except Exception:
        return []


def _child_summaries_for_parent_ids(parent_ids):
    ids = [str(item or "").strip() for item in parent_ids if str(item or "").strip()]
    if not ids:
        return {}
    grouped = {
        parent_id: {
            "children": {},
            "doubao_browser_tts": [],
        }
        for parent_id in ids
    }
    try:
        base_query = PublicationTask.query.with_entities(
            PublicationTask.id.label("id"),
            PublicationTask.celery_task_id.label("celery_task_id"),
            PublicationTask.status.label("status"),
            PublicationTask.current_step.label("current_step"),
            PublicationTask.progress.label("progress"),
            PublicationTask.error_message.label("error_message"),
            PublicationTask.feature.label("feature"),
            PublicationTask.parent_task_id.label("parent_task_id"),
            PublicationTask.source_task_id.label("source_task_id"),
            PublicationTask.task_title.label("task_title"),
            PublicationTask.task_video_path.label("task_video_path"),
            PublicationTask.report_audio_wav_path.label("audio_wav_path"),
            PublicationTask.report_audio_seconds.label("audio_seconds"),
            PublicationTask.report_audio_audio_bytes.label("audio_bytes"),
            PublicationTask.created_at.label("created_at"),
            PublicationTask.completed_at.label("completed_at"),
        )
        rows = (
            base_query
            .filter(or_(PublicationTask.parent_task_id.in_(ids), PublicationTask.source_task_id.in_(ids)))
            .order_by(PublicationTask.created_at.asc())
            .all()
        )
    except Exception:
        return grouped

    gemini_features = {gemini_browser.FEATURE_NAME, gemini_browser.LEGACY_FEATURE_NAME}
    for row in rows:
        parent_id = row.parent_task_id or row.source_task_id
        parent_id = str(parent_id or "").strip()
        if parent_id not in grouped:
            continue
        feature = row.feature
        summary = _child_task_summary_row(row)
        if feature in gemini_features:
            grouped[parent_id]["gemini"] = summary
            grouped[parent_id]["children"]["gemini"] = summary
        elif feature == doubao_browser_tts.FEATURE_NAME:
            grouped[parent_id]["doubao_browser_tts"].append(summary)
            grouped[parent_id]["children"]["doubao_browser_tts"] = grouped[parent_id]["doubao_browser_tts"]
        elif feature == "publisher":
            if not grouped[parent_id].get("publisher_video"):
                grouped[parent_id]["publisher_video"] = summary
                grouped[parent_id]["children"]["publisher_video"] = summary
    vertical_summaries = _vertical_child_summaries_for_parent_ids(ids)
    for parent_id, summary in vertical_summaries.items():
        if parent_id not in grouped:
            continue
        grouped[parent_id]["publisher_vertical_video"] = summary
        grouped[parent_id]["children"]["publisher_vertical_video"] = summary
    return grouped


def _vertical_child_summaries_for_parent_ids(parent_ids):
    ids = {str(item or "").strip() for item in parent_ids if str(item or "").strip()}
    if not ids:
        return {}
    try:
        candidates = (
            PublicationTask.query.options(
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
                    PublicationTask.parent_task_id,
                    PublicationTask.source_task_id,
                    PublicationTask.created_at,
                    PublicationTask.completed_at,
                )
            )
            .filter(PublicationTask.feature == "publisher")
            .filter(PublicationTask.source_task_id.in_(ids))
            .order_by(PublicationTask.created_at.desc())
            .all()
        )
    except Exception:
        return {}

    selected = {}
    for task in candidates:
        parent_id = str(task.source_task_id or "").strip()
        if parent_id not in ids or not _is_vertical_video_candidate_child(task):
            continue
        current = selected.get(parent_id)
        if not current or _vertical_child_priority(task) > _vertical_child_priority(current):
            selected[parent_id] = task

    return {
        parent_id: _vertical_child_summary(task, parent_id)
        for parent_id, task in selected.items()
    }


def _vertical_child_priority(task):
    if task.status == "completed" and _path_exists(_vertical_video_path_from_child(task)):
        return 4
    if task.status not in TERMINAL_STATUSES:
        return 3
    if _is_vertical_backfill_child(task):
        return 2
    return 1


def _child_task_summary_row(row, result_payload=None):
    result = result_payload if isinstance(result_payload, dict) else {}
    metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
    audio = result.get("audio") if isinstance(result.get("audio"), dict) else {}
    metadata = dict(metadata)
    if row.task_title and not metadata.get("title"):
        metadata["title"] = row.task_title
    payload = {
        "job_id": row.id,
        "celery_id": row.celery_task_id,
        "status": row.status,
        "step": row.current_step,
        "progress": row.progress or 0,
        "feature": row.feature,
        "error": row.error_message,
        "metadata": metadata,
        "created_at": row.created_at.isoformat() + "Z" if row.created_at else None,
        "completed_at": row.completed_at.isoformat() + "Z" if row.completed_at else None,
    }
    seconds = audio.get("seconds") if audio else getattr(row, "audio_seconds", None)
    audio_bytes = audio.get("bytes") if audio else getattr(row, "audio_bytes", None)
    if audio or row.audio_wav_path or seconds or audio_bytes:
        payload["audio"] = {
            "seconds": seconds,
            "bytes": audio_bytes,
        }
        if row.audio_wav_path:
            payload["audio"]["download_url"] = f"/api/report-audio-workflows/jobs/{row.id}/download-audio"
        payload["audio"] = {key: value for key, value in payload["audio"].items() if value is not None}
    if row.task_video_path:
        payload["video"] = {
            "download_url": f"/api/report-audio-workflows/jobs/{row.id}/download-video",
        }
    return payload


def _child_task_summary(task):
    result = task.result if isinstance(task.result, dict) else {}
    params = task.params if isinstance(task.params, dict) else {}
    feature = task.feature or params.get("feature")
    payload = {
        "job_id": task.id,
        "celery_id": task.celery_task_id,
        "status": task.status,
        "step": task.current_step,
        "progress": task.progress or 0,
        "feature": feature,
        "error": task.error_message or result.get("error"),
        "metadata": result.get("metadata") or _child_metadata_from_params(params),
        "audio": result.get("audio"),
        "created_at": task.created_at.isoformat() + "Z" if task.created_at else None,
        "completed_at": task.completed_at.isoformat() + "Z" if task.completed_at else None,
    }
    preview = _child_text_preview(result, feature)
    if preview:
        payload["text_preview"] = preview
    turns = result.get("turns") if isinstance(result.get("turns"), list) else None
    if turns:
        payload["turn_count"] = len(turns)
    return payload


def _child_text_preview(result, feature=None, max_chars=180):
    if not isinstance(result, dict):
        return ""
    if feature not in {gemini_browser.FEATURE_NAME, gemini_browser.LEGACY_FEATURE_NAME}:
        return ""
    candidates = [
        result.get("final_response_text"),
        result.get("response_text"),
        result.get("text"),
    ]
    turns = result.get("turns") if isinstance(result.get("turns"), list) else []
    for turn in reversed(turns):
        if isinstance(turn, dict):
            candidates.extend([
                turn.get("final_response_text"),
                turn.get("response_text"),
                turn.get("text"),
                turn.get("content"),
            ])
    for candidate in candidates:
        text = " ".join(str(candidate or "").split())
        if not text:
            continue
        if len(text) > max_chars:
            return f"{text[:max_chars].rstrip()}..."
        return text
    return ""


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
        "sub_burn_enabled": _truthy(pick("sub_burn_enabled", default=True), default=True),
    }


def _parse_script_material_only(data):
    for key in ("script_material_only", "material_only", "script_only"):
        if key in data:
            return _truthy(data.get(key), False)
    return False


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
        "download_count": attachment.download_count,
        "updated_at": attachment.updated_at.isoformat() + "Z" if attachment.updated_at else None,
        "created_at": attachment.created_at.isoformat() + "Z" if attachment.created_at else None,
        "group_id": attachment.group_id,
        "tab_id": attachment.tab_id,
        "topic_id": attachment.topic_id,
        "topic_title": topic.title if topic else None,
        "topic_author": topic.author_name if topic else None,
        "topic_create_time": topic.create_time if topic else None,
        "topic_first_synced_at": topic.first_synced_at.isoformat() + "Z" if topic and topic.first_synced_at else None,
        "topic_last_synced_at": topic.last_synced_at.isoformat() + "Z" if topic and topic.last_synced_at else None,
        "tab_label": topic.tab_label if topic else None,
        "local_path": str(path),
        "preview_url": f"/api/report-audio-workflows/pdf-assets/{attachment.id}/preview",
    }


def _pdf_asset_row_payload(row):
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
        "topic_title": getattr(row, "topic_title", None),
        "topic_author": getattr(row, "topic_author", None),
        "topic_create_time": getattr(row, "topic_create_time", None),
        "topic_first_synced_at": _isoformat_z(getattr(row, "topic_first_synced_at", None)),
        "topic_last_synced_at": _isoformat_z(getattr(row, "topic_last_synced_at", None)),
        "tab_label": getattr(row, "tab_label", None),
        "local_path": str(path),
        "preview_url": f"/api/report-audio-workflows/pdf-assets/{row.attachment_id}/preview",
    }


def _isoformat_z(value):
    if not value:
        return None
    if isinstance(value, str):
        return value
    return value.isoformat() + "Z"


def _attach_pdf_asset_topics(items):
    keys = {
        (item.get("group_id"), item.get("tab_id"), item.get("topic_id"))
        for item in items
        if item.get("group_id") and item.get("tab_id") and item.get("topic_id")
    }
    if not keys:
        return
    filters = [
        and_(
            ZsxqTopic.group_id == group_id,
            ZsxqTopic.tab_id == tab_id,
            ZsxqTopic.topic_id == topic_id,
        )
        for group_id, tab_id, topic_id in keys
    ]
    rows = (
        db.session.query(
            ZsxqTopic.group_id.label("group_id"),
            ZsxqTopic.tab_id.label("tab_id"),
            ZsxqTopic.topic_id.label("topic_id"),
            ZsxqTopic.title.label("topic_title"),
            ZsxqTopic.tab_label.label("tab_label"),
            ZsxqTopic.author_name.label("topic_author"),
            ZsxqTopic.create_time.label("topic_create_time"),
        )
        .filter(or_(*filters))
        .all()
    )
    topics = {
        (row.group_id, row.tab_id, row.topic_id): row
        for row in rows
    }
    for item in items:
        topic = topics.get((item.get("group_id"), item.get("tab_id"), item.get("topic_id")))
        if not topic:
            continue
        item["topic_title"] = topic.topic_title
        item["topic_author"] = topic.topic_author
        item["topic_create_time"] = topic.topic_create_time
        item["tab_label"] = topic.tab_label


def _empty_pdf_asset_workflow_stats():
    return {
        "has_workflow": False,
        "total": 0,
        "succeeded": 0,
        "failed": 0,
        "running": 0,
        "latest": None,
    }


def _pdf_asset_workflow_stats(asset_ids, user_id=None):
    ids = [str(item or "").strip() for item in asset_ids if str(item or "").strip()]
    if not ids:
        return {}

    rows = (
        PublicationTask.query
        .with_entities(
            PublicationTask.id.label("id"),
            PublicationTask.status.label("status"),
            PublicationTask.current_step.label("current_step"),
            PublicationTask.progress.label("progress"),
            PublicationTask.created_at.label("created_at"),
            PublicationTask.completed_at.label("completed_at"),
            PublicationTask.report_audio_pdf_attachment_id.label("attachment_id"),
        )
        .filter(PublicationTask.feature == FEATURE_NAME)
        .filter(PublicationTask.report_audio_pdf_attachment_id.in_(ids))
    )
    if user_id:
        rows = rows.filter(PublicationTask.user_id.in_(_visible_user_ids(user_id)))

    stats = {asset_id: _empty_pdf_asset_workflow_stats() for asset_id in ids}
    for row in rows.order_by(PublicationTask.created_at.desc()).all():
        asset_id = str(row.attachment_id or "").strip()
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
        public["preview_url"] = f"/api/report-audio-workflows/jobs/{job_id}/input-pdf?preview=1"
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
        "landscape_enabled": video.get("landscape_enabled"),
        "vertical_enabled": video.get("vertical_enabled"),
        "cover_title": video.get("cover_title"),
        "cover_subtitle": video.get("cover_subtitle"),
        "cover_regenerated_at": video.get("cover_regenerated_at"),
        "bilibili_result": video.get("bilibili_result"),
    }
    video_path = video.get("video_path")
    vertical_video_path = video.get("vertical_video_path")
    cover_path = video.get("cover_path")
    if video_path and os.path.exists(video_path):
        public["video"] = {
            "bytes": os.path.getsize(video_path),
            "download_url": f"/api/report-audio-workflows/jobs/{job_id}/download-video",
        }
    if vertical_video_path and os.path.exists(vertical_video_path):
        public["vertical_video"] = {
            "bytes": os.path.getsize(vertical_video_path),
            "download_url": f"/api/report-audio-workflows/jobs/{job_id}/download-vertical-video",
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
