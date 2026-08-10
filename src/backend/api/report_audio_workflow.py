import json
import logging
import os
import re
import time
from copy import deepcopy
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path
from threading import Lock
from types import SimpleNamespace

from flask import Blueprint, has_request_context, jsonify, request, send_file
from sqlalchemy import and_, func, inspect as sqlalchemy_inspect, or_, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import load_only
from sqlalchemy.orm.attributes import flag_modified
from werkzeug.utils import secure_filename

from celery_config import celery_app
from models import db
from models.report_stick_figure_asset import ReportStickFigureSceneAsset
from models.report_visual_plan import ReportVisualPlanSegment
from models.task import PublicationTask
from models.zsxq_sync import ZsxqAttachment, ZsxqTopic
from services import doubao_browser_tts, gemini_browser
from services.doubao_tts_ws import is_configured as is_doubao_api_configured
from services.report_audio_workflow import (
    DEFAULT_INITIAL_PROMPT,
    DEFAULT_INITIAL_SCRIPT_MIN_CHARS,
    DEFAULT_REVIEW_PROMPT,
    DEFAULT_SCRIPT_REGENERATION_MIN_CHARS,
    FEATURE_NAME,
    QUEUE_NAME,
    initial_submission_prompts,
    pdf_first_page_image_path_for_job,
    render_pdf_first_page_image,
    review_prompt_for_pdf_evidence,
    script_regeneration_prompts,
    status_payload as workflow_status_payload,
    structured_result_quality_issue,
)
from services.report_audio_finalization import (
    create_or_resume_finalization,
    finalization_summary,
    finalization_summary_for_audio,
)
from services.report_stick_figure_assets import has_browser_overlay_artifact
from services.report_visual_scheme import visual_scheme
from services.runtime_guard import (
    apply_async_after_db_release,
    is_retryable_db_error,
    maybe_guarded_apply_async,
)
from services.bilibili_publish import (
    BilibiliUploadRetryConflict,
    BilibiliUploadRetryEnqueueError,
    bilibili_upload_child_payload,
    bilibili_upload_children,
    cancel_bilibili_upload_child,
    clean_upload_text as clean_bilibili_upload_text,
    create_bilibili_upload_task,
    enqueue_bilibili_upload_task,
    retry_bilibili_upload_child,
)
from services.douyin_publish_jobs import (
    FEATURE_NAME as DOUYIN_FEATURE_NAME,
    cancel_douyin_publish_task,
    create_douyin_publish_task,
    douyin_publish_children,
    douyin_publish_payload,
    enqueue_douyin_publish_task,
)
from services.wechat_channels_publish_jobs import (
    FEATURE_NAME as WECHAT_CHANNELS_FEATURE_NAME,
    cancel_wechat_channels_publish_task,
    create_wechat_channels_publish_task,
    enqueue_wechat_channels_publish_task,
    wechat_channels_publish_children,
    wechat_channels_publish_payload,
)
from services.thumbnail_service import thumbnail_service
from tasks.publisher import run_publication_pipeline
from tasks.report_audio_workflow import (
    BANK_ALIASES,
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
from utils.date_filters import submission_date_utc_range as _submission_date_utc_range


report_audio_workflow_bp = Blueprint("report_audio_workflow", __name__, url_prefix="/api/report-audio-workflows")
PROJECT_ROOT = Path(__file__).resolve().parents[3]
logger = logging.getLogger(__name__)
SCRIPT_REGENERATION_PARENT_FEATURE = "report_audio_script_regeneration"
AUDIO_GENERATION_PARENT_FEATURE = "report_audio_script_tts_generation"


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
REPORT_AUDIO_ASSET_STATS_CACHE_TTL_SECONDS = max(
    0.0,
    _float_env("REPORT_AUDIO_ASSET_STATS_CACHE_TTL", 10),
)
REPORT_AUDIO_MEDIA_PATH_CACHE_TTL_SECONDS = max(
    0.0,
    _float_env("REPORT_AUDIO_MEDIA_PATH_CACHE_TTL", 30),
)
REPORT_AUDIO_STALE_CACHE_TTL_SECONDS = max(
    REPORT_AUDIO_JOBS_CACHE_TTL_SECONDS,
    _float_env("REPORT_AUDIO_STALE_CACHE_TTL", 300),
)
REPORT_AUDIO_READ_STATEMENT_TIMEOUT_MS = int(max(
    500,
    min(15000, _float_env("REPORT_AUDIO_READ_STATEMENT_TIMEOUT_MS", 5000)),
))
_REPORT_AUDIO_JOBS_CACHE = {}
_REPORT_AUDIO_JOBS_CACHE_LOCK = Lock()
_REPORT_AUDIO_JOB_DETAIL_CACHE = {}
_REPORT_AUDIO_JOB_DETAIL_CACHE_LOCK = Lock()
_REPORT_AUDIO_ASSET_STATS_CACHE = {}
_REPORT_AUDIO_ASSET_STATS_CACHE_LOCK = Lock()
_REPORT_AUDIO_ASSET_REFERENCE_CACHE = {}
_REPORT_AUDIO_ASSET_REFERENCE_CACHE_LOCK = Lock()
_REPORT_AUDIO_KEYFRAME_TASK_CACHE = {}
_REPORT_AUDIO_KEYFRAME_TASK_CACHE_LOCK = Lock()
_REPORT_AUDIO_MEDIA_PATH_CACHE = {}
_REPORT_AUDIO_MEDIA_PATH_CACHE_LOCK = Lock()

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
BILIBILI_UPLOAD_SOURCE = f"{FEATURE_NAME}:bilibili_upload"
DOUYIN_UPLOAD_SOURCE = f"{FEATURE_NAME}:douyin_upload"
WECHAT_CHANNELS_UPLOAD_SOURCE = f"{FEATURE_NAME}:wechat_channels_upload"
VISUAL_VIDEO_WORKFLOW_FEATURE = "report_stick_figure_video_workflow"
VISUAL_VIDEO_RENDER_FEATURE = "report_stick_figure_video_render"
SCRIPT_MATERIAL_FEATURE_NAME = "report_script_material_workflow"
MATERIAL_STATUS_FEATURES = (SCRIPT_MATERIAL_FEATURE_NAME, FEATURE_NAME, "report_audio_workflow")
VERTICAL_VIDEO_SCHEME_DEFAULT = "scheme1"
VERTICAL_VIDEO_SCHEMES = {"scheme1", "scheme2", "scheme3"}
KEYFRAME_OUTPUTS = {
    "bilibili_cover",
    "vertical_cover",
    "landscape_keyframe",
    "vertical_keyframe_scheme1",
    "vertical_keyframe_scheme2",
    "vertical_keyframe_scheme3",
}
VERTICAL_PREVIEW_SECONDS_DEFAULT = 10
TTS_PROVIDER_DEFAULT = "doubao_browser"
TTS_PROVIDERS = {"doubao_browser", "doubao_api"}
TTS_VOICE_DEFAULT = "zh_female_mizai_uranus_bigtts"
TTS_VOICES = {
    "zh_female_mizai_uranus_bigtts",
    "zh_female_qingchezizi_uranus_bigtts",
    "zh_male_liufei_uranus_bigtts",
    "zh_male_dayi_uranus_bigtts",
}
OWNED_TASK_REFERENCE_COLUMNS = (
    PublicationTask.id,
    PublicationTask.celery_task_id,
    PublicationTask.status,
    PublicationTask.current_step,
    PublicationTask.progress,
    PublicationTask.error_message,
    PublicationTask.feature,
    PublicationTask.user_id,
    PublicationTask.created_at,
    PublicationTask.updated_at,
    PublicationTask.completed_at,
    PublicationTask.report_audio_pdf_attachment_id,
    PublicationTask.report_audio_pdf_filename,
    PublicationTask.report_audio_pdf_bytes,
    PublicationTask.report_audio_output_path,
    PublicationTask.report_audio_wav_path,
    PublicationTask.report_audio_video_path,
    PublicationTask.report_audio_cover_path,
    PublicationTask.report_audio_landscape_keyframe_path,
    PublicationTask.report_audio_vertical_cover_path,
    PublicationTask.report_audio_podcast_cover_path,
    PublicationTask.report_audio_bilibili_cover_path,
    PublicationTask.final_script_chars,
    PublicationTask.waiting_child_label,
    PublicationTask.waiting_child_id,
    PublicationTask.waiting_child_status,
    PublicationTask.waiting_child_step,
    PublicationTask.waiting_child_updated_at,
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


def _clear_jobs_summary_cache(user_id=None):
    user_id = str(user_id or "").strip()
    with _REPORT_AUDIO_JOBS_CACHE_LOCK:
        if not user_id:
            _REPORT_AUDIO_JOBS_CACHE.clear()
        else:
            for key in list(_REPORT_AUDIO_JOBS_CACHE):
                if user_id in str(key[0]).split("+"):
                    _REPORT_AUDIO_JOBS_CACHE.pop(key, None)
    with _REPORT_AUDIO_JOB_DETAIL_CACHE_LOCK:
        if not user_id:
            _REPORT_AUDIO_JOB_DETAIL_CACHE.clear()
        else:
            for key in list(_REPORT_AUDIO_JOB_DETAIL_CACHE):
                if key[0] == user_id:
                    _REPORT_AUDIO_JOB_DETAIL_CACHE.pop(key, None)
    with _REPORT_AUDIO_ASSET_STATS_CACHE_LOCK:
        if not user_id:
            _REPORT_AUDIO_ASSET_STATS_CACHE.clear()
        else:
            for key in list(_REPORT_AUDIO_ASSET_STATS_CACHE):
                if key[0] == user_id:
                    _REPORT_AUDIO_ASSET_STATS_CACHE.pop(key, None)
    for cache, lock in (
        (_REPORT_AUDIO_ASSET_REFERENCE_CACHE, _REPORT_AUDIO_ASSET_REFERENCE_CACHE_LOCK),
        (_REPORT_AUDIO_KEYFRAME_TASK_CACHE, _REPORT_AUDIO_KEYFRAME_TASK_CACHE_LOCK),
        (_REPORT_AUDIO_MEDIA_PATH_CACHE, _REPORT_AUDIO_MEDIA_PATH_CACHE_LOCK),
    ):
        with lock:
            if not user_id:
                cache.clear()
            else:
                for key in list(cache):
                    if key[0] == user_id:
                        cache.pop(key, None)


def _set_hot_read_statement_timeout():
    bind = db.session.get_bind()
    if bind.dialect.name != "postgresql":
        return
    db.session.execute(text(
        f"SET LOCAL statement_timeout = '{REPORT_AUDIO_READ_STATEMENT_TIMEOUT_MS}ms'"
    ))


def _reset_failed_db_session():
    try:
        db.session.rollback()
    finally:
        db.session.remove()


def _stale_success_payload(payload):
    stale = deepcopy(payload)
    stale["stale"] = True
    data = stale.get("data")
    if isinstance(data, dict):
        data["stale"] = True
    return stale


def _retry_transient_db_read(operation):
    """Retry one read request after replacing an invalid DB session.

    ``pool_pre_ping`` only protects checkout.  A PgBouncer/server connection can
    still disappear after checkout, which previously left detail polling on a
    one-off 500 and stopped the browser's continuation view.
    """

    def decorate(callback):
        @wraps(callback)
        def wrapped(*args, **kwargs):
            for attempt in range(2):
                try:
                    _set_hot_read_statement_timeout()
                    return callback(*args, **kwargs)
                except SQLAlchemyError as exc:
                    retryable = is_retryable_db_error(exc)
                    _reset_failed_db_session()
                    if attempt == 0 and retryable:
                        logger.warning("%s DB connection invalidated; retrying once", operation)
                        continue
                    logger.error("%s DB read failed: %s", operation, type(exc).__name__)
                    return jsonify({
                        "success": False,
                        "message": "数据库连接短暂中断，页面会自动重试",
                    }), 503
            raise AssertionError("unreachable")

        return wrapped
    return decorate


def _store_ttl_cache(cache, lock, key, payload, ttl_seconds):
    now = time.time()
    stale_until = now + REPORT_AUDIO_STALE_CACHE_TTL_SECONDS
    with lock:
        for cached_key, entry in list(cache.items()):
            if float(entry.get("stale_until") or 0) <= now:
                cache.pop(cached_key, None)
        cache[key] = {
            "expires_at": now + max(0.0, ttl_seconds),
            "stale_until": stale_until,
            "payload": deepcopy(payload),
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
            "doubao_api_tts": {
                "configured": is_doubao_api_configured(),
                "provider": "doubao_ws_v3",
            },
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
    hot_download_threshold = 40

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
        .filter(ZsxqAttachment.file_ext == "pdf")
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
    ids = list(dict.fromkeys(
        item.strip() for item in raw_ids.split(",") if item.strip()
    ))[:50]
    user_id = str(getattr(current_user, "id", None) or "")
    cache_key = (user_id, tuple(ids))
    cached = None
    if REPORT_AUDIO_ASSET_STATS_CACHE_TTL_SECONDS > 0:
        with _REPORT_AUDIO_ASSET_STATS_CACHE_LOCK:
            cached = _REPORT_AUDIO_ASSET_STATS_CACHE.get(cache_key)
            if cached and cached["expires_at"] > time.time():
                return jsonify(deepcopy(cached["payload"]))

    try:
        _set_hot_read_statement_timeout()
        stats = _pdf_asset_workflow_stats(ids, user_id=user_id)
    except SQLAlchemyError as exc:
        _reset_failed_db_session()
        if cached and float(cached.get("stale_until") or 0) > time.time():
            logger.warning("PDF asset workflow stats DB read failed; serving stale cache: %s", type(exc).__name__)
            return jsonify(_stale_success_payload(cached["payload"]))
        logger.error("PDF asset workflow stats DB read failed without fallback: %s", type(exc).__name__)
        return jsonify({"success": False, "message": "数据库暂时繁忙，请稍后重试"}), 503

    payload = {
        "success": True,
        "data": {"stats": stats},
        "stats": stats,
    }
    if REPORT_AUDIO_ASSET_STATS_CACHE_TTL_SECONDS > 0:
        _store_ttl_cache(
            _REPORT_AUDIO_ASSET_STATS_CACHE,
            _REPORT_AUDIO_ASSET_STATS_CACHE_LOCK,
            cache_key,
            payload,
            REPORT_AUDIO_ASSET_STATS_CACHE_TTL_SECONDS,
        )
    return jsonify(payload)


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

    minimum_script_chars = _bounded_int(
        data.get("gemini_min_final_chars"),
        DEFAULT_INITIAL_SCRIPT_MIN_CHARS,
        DEFAULT_INITIAL_SCRIPT_MIN_CHARS,
        20000,
    )
    prompt = (data.get("prompt") or DEFAULT_INITIAL_PROMPT).strip()
    pdf_evidence_enabled = _parse_pdf_evidence_enabled(data)
    review_prompt = review_prompt_for_pdf_evidence(
        data.get("review_prompt") or DEFAULT_REVIEW_PROMPT,
        enabled=pdf_evidence_enabled,
    )
    submission_prompts = initial_submission_prompts(
        prompt,
        review_prompt,
        minimum_chars=minimum_script_chars,
    )
    prompt = submission_prompts["prompt"]
    review_prompt = submission_prompts["review_prompt"]
    followups = _parse_followups(data)
    video_options = _parse_video_options(data)
    if (
        video_options.get("render_mode") == "full_video"
        and len(video_options.get("vertical_video_schemes") or []) > 2
    ):
        return jsonify({"success": False, "message": "竖屏方案一次最多选择两个"}), 400
    if (
        video_options.get("render_mode") == "keyframes"
        and _has_explicit_keyframe_outputs(data)
        and not video_options.get("keyframe_outputs")
    ):
        return jsonify({"success": False, "message": "请至少选择一个需要生成的关键帧或封皮"}), 400
    try:
        visual_scheme_keys = _parse_visual_scheme_keys(data)
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    script_material_only = _parse_script_material_only(data)
    if not script_material_only:
        visual_scheme_keys = _ensure_scheme3_visual_workflow(
            visual_scheme_keys,
            video_options,
        )
    manual_gemini = _parse_manual_gemini_result(data)
    tts_provider = str(data.get("tts_provider") or TTS_PROVIDER_DEFAULT).strip().lower()
    if tts_provider not in TTS_PROVIDERS:
        return jsonify({"success": False, "message": "tts_provider 仅支持 doubao_browser 或 doubao_api"}), 400
    tts_voice = str(data.get("tts_voice") or TTS_VOICE_DEFAULT).strip()
    if tts_provider == "doubao_api" and tts_voice not in TTS_VOICES:
        return jsonify({"success": False, "message": "不支持所选豆包 API 音色"}), 400
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
    if not manual_gemini and not gemini_status.get("configured"):
        return jsonify({"success": False, "message": "研报 PDF Gem 解析未配置", "data": {"report_pdf_gemini": gemini_status}}), 400
    if not script_material_only and tts_provider == "doubao_browser" and not doubao_status.get("configured"):
        return jsonify({"success": False, "message": "豆包浏览器朗读未配置", "data": {"doubao_browser_tts": doubao_status}}), 400
    if not script_material_only and tts_provider == "doubao_api" and not is_doubao_api_configured():
        return jsonify({"success": False, "message": "豆包 API TTS 未配置，请设置 DOUBAO_TTS_API_KEY"}), 400

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
                "pdf_evidence_enabled": pdf_evidence_enabled,
                "followups": followups,
                "gemini_url": gemini_url,
                "manual_gemini": manual_gemini,
                "gemini_prefer_pro_model": _truthy(data.get("gemini_prefer_pro_model"), True),
                "gemini_require_pro_model": _truthy(data.get("gemini_require_pro_model"), False),
                "gemini_min_final_chars": minimum_script_chars,
                "metadata": metadata,
                "script_material_only": script_material_only,
                "tts_provider": tts_provider,
                "tts_voice": tts_voice if tts_provider == "doubao_api" else None,
                "visual_scheme_keys": visual_scheme_keys,
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
        celery_task = apply_async_after_db_release(
            process_report_audio_workflow_job,
            args=[task.id],
            queue=QUEUE_NAME,
            retry=False,
        )
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


def _query_jobs_page(query, *, page, per_page, include_detail):
    ordered = query.order_by(PublicationTask.created_at.desc())
    if include_detail:
        pagination = ordered.paginate(
            page=page,
            per_page=per_page,
            error_out=False,
        )
        items = [_job_payload(task) for task in pagination.items]
        return items, pagination.total, pagination.pages

    offset = (page - 1) * per_page
    total_query = (
        query.order_by(None)
        .with_entities(func.count(PublicationTask.id))
        .scalar_subquery()
    )
    rows = (
        ordered.with_entities(
                total_query.label("total_count"),
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
    if rows:
        total = int(rows[0].total_count or 0)
    elif offset:
        total = int(
            query.order_by(None)
            .with_entities(func.count(PublicationTask.id))
            .scalar()
            or 0
        )
    else:
        total = 0
    child_summaries = _child_summaries_for_parent_ids([row.id for row in rows])
    items = [_job_summary_payload(row, child_summaries.get(row.id)) for row in rows]
    pages = ((total + per_page - 1) // per_page) if total else 0
    return items, total, pages


@report_audio_workflow_bp.route("/jobs", methods=["GET"])
@require_login
def list_jobs(current_user):
    page = _bounded_int(request.args.get("page"), 1, 1, 100000)
    per_page = _bounded_int(request.args.get("per_page"), 20, 1, 100)
    status = (request.args.get("status") or "").strip()
    search = _normalize_search_query(
        request.args.get("q") or request.args.get("search") or request.args.get("job_search")
    )
    submitted_on = str(request.args.get("submitted_on") or request.args.get("job_date") or "").strip()
    submitted_range = _submission_date_utc_range(submitted_on)
    if not submitted_range:
        submitted_on = ""
    include_detail = str(request.args.get("detail") or "").lower() in {"1", "true", "yes"}

    visible_user_ids = _visible_user_ids(current_user)
    cache_key = ("+".join(sorted(visible_user_ids)), page, per_page, status, search, submitted_on)
    cached = None
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
    if submitted_range:
        submitted_start, submitted_end = submitted_range
        query = query.filter(
            PublicationTask.created_at >= submitted_start,
            PublicationTask.created_at < submitted_end,
        )
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

    try:
        _set_hot_read_statement_timeout()
        items, total, pages = _query_jobs_page(
            query,
            page=page,
            per_page=per_page,
            include_detail=include_detail,
        )
    except SQLAlchemyError as exc:
        _reset_failed_db_session()
        if cached and float(cached.get("stale_until") or 0) > time.time():
            logger.warning("Report audio jobs DB read failed; serving stale cache: %s", type(exc).__name__)
            return jsonify(_stale_success_payload(cached["payload"]))
        logger.error("Report audio jobs DB read failed without fallback: %s", type(exc).__name__)
        return jsonify({"success": False, "message": "数据库暂时繁忙，请稍后重试"}), 503
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
                "submitted_on": submitted_on,
            },
        },
        "items": items,
    }
    if not include_detail and REPORT_AUDIO_JOBS_CACHE_TTL_SECONDS > 0:
        _store_ttl_cache(
            _REPORT_AUDIO_JOBS_CACHE,
            _REPORT_AUDIO_JOBS_CACHE_LOCK,
            cache_key,
            payload,
            REPORT_AUDIO_JOBS_CACHE_TTL_SECONDS,
        )
    return jsonify(payload)


@report_audio_workflow_bp.route("/jobs/<job_id>", methods=["GET"])
@require_login
@_retry_transient_db_read("Report audio detail")
def get_job(current_user, job_id):
    reference = _get_owned_task_reference(current_user, job_id)
    if not reference:
        return jsonify({"success": False, "message": "任务不存在"}), 404
    user_id = str(getattr(current_user, "id", None) or "")
    cache_key = (user_id, str(reference.id))
    version = _job_detail_version(reference)
    with _REPORT_AUDIO_JOB_DETAIL_CACHE_LOCK:
        cached = _REPORT_AUDIO_JOB_DETAIL_CACHE.get(cache_key)
        if cached and cached.get("version") == version:
            payload = deepcopy(cached["payload"])
            # Bilibili uploads are independent child tasks. Their projection
            # rows can be created or completed after the parent report task is
            # already done, so the parent's detail-cache version does not
            # change. Always overlay this small, structured query on a cache
            # hit to avoid hiding newly submitted upload records until the
            # backend process or cache is manually refreshed.
            payload["bilibili_uploads"] = bilibili_upload_children(
                reference.id,
                parent_feature=FEATURE_NAME,
                source_label=BILIBILI_UPLOAD_SOURCE,
            )
            payload["visual_videos"] = _linked_visual_video_payloads(current_user, reference)
            return jsonify({"success": True, **payload, "data": payload})

    task = _get_owned_task(current_user, job_id)
    if not task:
        return jsonify({"success": False, "message": "任务不存在"}), 404
    payload = _job_payload(
        task,
        include_raw=True,
        visual_videos=_linked_visual_video_payloads(current_user, task),
    )
    with _REPORT_AUDIO_JOB_DETAIL_CACHE_LOCK:
        _REPORT_AUDIO_JOB_DETAIL_CACHE[cache_key] = {
            "version": version,
            "payload": deepcopy(payload),
        }
    return jsonify({"success": True, **payload, "data": payload})


@report_audio_workflow_bp.route("/jobs/<job_id>/status", methods=["GET"])
@require_login
@_retry_transient_db_read("Report audio status")
def get_job_status(current_user, job_id):
    task = _get_owned_task_reference(current_user, job_id)
    if not task:
        return jsonify({"success": False, "message": "任务不存在"}), 404
    visual_videos = _linked_visual_video_payloads(current_user, task)
    payload = _job_status_payload(task, visual_videos=visual_videos)
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
    ).filter(PublicationTask.id == str(child_id)).one_or_none()
    if not child or str(parent.id) not in {str(child.parent_task_id or ""), str(child.source_task_id or "")}:
        return jsonify({"success": False, "message": "子任务不存在"}), 404

    payload = _child_task_summary(child)
    payload["raw_params"] = child.params if isinstance(child.params, dict) else {}
    payload["raw_result"] = child.result if isinstance(child.result, dict) else {}
    return jsonify({"success": True, **payload, "data": payload})


def _send_previewable_file(path, mimetype, download_name):
    inline = _truthy(request.args.get("inline"), False)
    response = send_file(
        path,
        mimetype=mimetype,
        as_attachment=not inline,
        download_name=download_name,
        conditional=True,
        max_age=0 if inline else None,
    )
    if inline:
        response.cache_control.no_store = True
        response.cache_control.no_cache = True
        response.cache_control.must_revalidate = True
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


def _cached_owned_media_path(current_user, job_id, asset_key, loader, resolver):
    user_id = str(getattr(current_user, "id", None) or "")
    cache_key = (user_id, str(job_id), str(asset_key))
    now = time.time()
    with _REPORT_AUDIO_MEDIA_PATH_CACHE_LOCK:
        cached = _REPORT_AUDIO_MEDIA_PATH_CACHE.get(cache_key)
        if cached and float(cached.get("expires_at") or 0) > now:
            path = cached.get("path")
            if path and os.path.exists(path):
                return SimpleNamespace(id=str(job_id)), path
            _REPORT_AUDIO_MEDIA_PATH_CACHE.pop(cache_key, None)
        task = loader(current_user, job_id)
        if not task:
            return None, None
        path = resolver(task)
        if path and os.path.exists(path):
            _REPORT_AUDIO_MEDIA_PATH_CACHE[cache_key] = {
                "expires_at": now + REPORT_AUDIO_MEDIA_PATH_CACHE_TTL_SECONDS,
                "path": path,
            }
        return task, path


@report_audio_workflow_bp.route("/jobs/<job_id>/download-audio", methods=["GET"])
@require_login
def download_audio(current_user, job_id):
    task, wav_path = _cached_owned_media_path(
        current_user,
        job_id,
        "audio",
        _get_owned_asset_reference,
        lambda item: item.report_audio_wav_path,
    )
    if not task:
        return jsonify({"success": False, "message": "任务不存在"}), 404
    if not wav_path or not os.path.exists(wav_path):
        return jsonify({"success": False, "message": "WAV 文件不存在"}), 404
    return _send_previewable_file(wav_path, "audio/wav", f"{task.id}.wav")


@report_audio_workflow_bp.route("/jobs/<job_id>/download-result", methods=["GET"])
@require_login
def download_result(current_user, job_id):
    task, output_path = _cached_owned_media_path(
        current_user,
        job_id,
        "result",
        _get_owned_asset_reference,
        lambda item: item.report_audio_output_path,
    )
    if not task:
        return jsonify({"success": False, "message": "任务不存在"}), 404
    if not output_path or not os.path.exists(output_path):
        return jsonify({"success": False, "message": "结果文件不存在"}), 404
    return send_file(output_path, mimetype="application/json", as_attachment=True, download_name=f"{task.id}.json")


@report_audio_workflow_bp.route("/jobs/<job_id>/download-video", methods=["GET"])
@require_login
def download_video(current_user, job_id):
    task, video_path = _cached_owned_media_path(
        current_user,
        job_id,
        "landscape_video",
        _get_owned_asset_reference,
        _landscape_video_path_for_task,
    )
    if not task:
        return jsonify({"success": False, "message": "任务不存在"}), 404
    if not video_path or not os.path.exists(video_path):
        return jsonify({"success": False, "message": "视频文件不存在"}), 404
    return _send_previewable_file(video_path, "video/mp4", f"{task.id}.mp4")


@report_audio_workflow_bp.route("/jobs/<job_id>/download-vertical-video", methods=["GET"])
@require_login
def download_vertical_video(current_user, job_id):
    preview = _truthy(request.args.get("preview"), False)
    requested_scheme = request.args.get("scheme")

    def resolve_vertical_video(task):
        if preview:
            if requested_scheme:
                return _vertical_video_path_for_scheme(task.id, requested_scheme, preview=True)
            child = _latest_vertical_video_child(task.id, preview=True)
            return _vertical_video_path_from_child(child) if child else None
        return _vertical_video_path_for_task(task, scheme=requested_scheme)

    task, video_path = _cached_owned_media_path(
        current_user,
        job_id,
        f"vertical_video:{int(preview)}:{requested_scheme or ''}",
        _get_owned_asset_reference,
        resolve_vertical_video,
    )
    if not task:
        return jsonify({"success": False, "message": "任务不存在"}), 404
    if not video_path or not os.path.exists(video_path):
        return jsonify({"success": False, "message": "竖屏视频文件不存在"}), 404
    scheme_suffix = f"-{_normalize_vertical_video_scheme(requested_scheme)}" if requested_scheme else ""
    suffix = ("vertical-preview" if preview else "vertical") + scheme_suffix
    return _send_previewable_file(video_path, "video/mp4", f"{task.id}-{suffix}.mp4")


@report_audio_workflow_bp.route("/jobs/<job_id>/download-vertical-cover", methods=["GET"])
@require_login
def download_vertical_cover(current_user, job_id):
    task, cover_path = _cached_owned_media_path(
        current_user,
        job_id,
        "vertical_cover",
        _get_owned_media_task,
        _vertical_cover_path_for_task,
    )
    if not task:
        return jsonify({"success": False, "message": "任务不存在"}), 404
    if not cover_path or not os.path.exists(cover_path):
        return jsonify({"success": False, "message": "竖版封皮文件不存在"}), 404
    return _send_previewable_file(cover_path, "image/jpeg", f"{task.id}-vertical-cover.jpg")


@report_audio_workflow_bp.route("/jobs/<job_id>/download-podcast-cover", methods=["GET"])
@require_login
def download_podcast_cover(current_user, job_id):
    task, cover_path = _cached_owned_media_path(
        current_user,
        job_id,
        "podcast_cover",
        _get_owned_media_task,
        _podcast_cover_path_for_task,
    )
    if not task:
        return jsonify({"success": False, "message": "任务不存在"}), 404
    if not cover_path or not os.path.exists(cover_path):
        return jsonify({"success": False, "message": "播客封皮文件不存在"}), 404
    return _send_previewable_file(cover_path, "image/jpeg", f"{task.id}-podcast-cover.jpg")


@report_audio_workflow_bp.route("/jobs/<job_id>/download-bilibili-cover", methods=["GET"])
@require_login
def download_bilibili_cover(current_user, job_id):
    task, cover_path = _cached_owned_media_path(
        current_user,
        job_id,
        "bilibili_cover",
        _get_owned_media_task,
        _bilibili_cover_path_for_task,
    )
    if not task:
        return jsonify({"success": False, "message": "任务不存在"}), 404
    if not cover_path or not os.path.exists(cover_path):
        return jsonify({"success": False, "message": "B站封皮文件不存在"}), 404
    return _send_previewable_file(cover_path, "image/jpeg", f"{task.id}-bilibili-cover.jpg")


@report_audio_workflow_bp.route("/jobs/<job_id>/download-cover", methods=["GET"])
@require_login
def download_cover(current_user, job_id):
    task, cover_path = _cached_owned_media_path(
        current_user,
        job_id,
        "cover",
        _get_owned_asset_reference,
        lambda item: item.report_audio_cover_path,
    )
    if not task:
        return jsonify({"success": False, "message": "任务不存在"}), 404
    if not cover_path or not os.path.exists(cover_path):
        return jsonify({"success": False, "message": "封面文件不存在"}), 404
    return _send_previewable_file(cover_path, "image/jpeg", f"{task.id}.jpg")


@report_audio_workflow_bp.route("/jobs/<job_id>/download-keyframe/<key>", methods=["GET"])
@require_login
def download_keyframe(current_user, job_id, key):
    safe_key = re.sub(r"[^a-zA-Z0-9_-]", "", str(key or ""))[:80]

    loader, resolver = _keyframe_media_accessors(safe_key)

    task, path = _cached_owned_media_path(
        current_user,
        job_id,
        f"keyframe:{safe_key}",
        loader,
        resolver,
    )
    if not task:
        return jsonify({"success": False, "message": "任务不存在"}), 404
    if not path or not os.path.exists(path):
        return jsonify({"success": False, "message": "关键帧文件不存在"}), 404
    return _send_previewable_file(path, "image/jpeg", f"{task.id}-{safe_key}.jpg")


def _keyframe_media_accessors(safe_key):
    if safe_key == "landscape_cover":
        return (
            _get_owned_media_task,
            lambda task: task.report_audio_landscape_keyframe_path,
        )

    def resolve_legacy_keyframe(task):
        video = (task.result or {}).get("video") or {}
        keyframes = video.get("keyframes") if isinstance(video.get("keyframes"), dict) else {}
        artifact = keyframes.get(safe_key) if isinstance(keyframes.get(safe_key), dict) else {}
        return artifact.get("path")

    return _get_owned_keyframe_task, resolve_legacy_keyframe


@report_audio_workflow_bp.route("/jobs/<job_id>/bilibili-upload", methods=["POST"])
@require_login
def submit_bilibili_upload(current_user, job_id):
    task = _get_owned_task(current_user, job_id)
    if not task:
        return jsonify({"success": False, "message": "任务不存在"}), 404

    current_user_id = getattr(current_user, "id", None)
    data = _request_payload()
    video_key = str(data.get("video_key") or data.get("video") or "landscape").strip().lower()
    visual_workflow_id = str(data.get("visual_workflow_id") or "").strip()
    if video_key.startswith("visual:"):
        if not visual_workflow_id:
            return jsonify({"success": False, "message": "视觉方案视频缺少工作流 ID"}), 400
        video_path = _linked_visual_video_path(current_user, task, visual_workflow_id)
        if not video_path:
            return jsonify({"success": False, "message": "视觉方案视频不存在、尚未完成或不属于同一 PDF"}), 400
    elif video_key in {"landscape", "vertical"}:
        video_path = _report_bilibili_video_path(task, video_key)
    else:
        return jsonify({"success": False, "message": "video_key 只能是 landscape、vertical 或已归集的视觉方案视频"}), 400

    if not _path_exists(video_path):
        return jsonify({"success": False, "message": "当前任务还没有可上传的视频文件"}), 400

    cover_key = str(data.get("cover_key") or "").strip()
    cover_path, resolved_cover_key = _report_bilibili_cover_path(task, video_key, cover_key)
    if cover_key and not cover_path:
        return jsonify({"success": False, "message": f"封面产物不存在：{cover_key}"}), 400

    manual_title = clean_bilibili_upload_text(data.get("title"), max_length=120)
    title = manual_title or _report_bilibili_default_title(task, video_key)
    try:
        child = create_bilibili_upload_task(
            task,
            current_user=current_user,
            parent_feature=FEATURE_NAME,
            source_label=BILIBILI_UPLOAD_SOURCE,
            video_path=video_path,
            video_key=video_key,
            title=title,
            data=data,
            cover_path=cover_path,
            cover_key=resolved_cover_key,
            default_tags=["财经", "音频", "研报解读"],
            metadata_extra={
                "title_source": "manual" if manual_title else "report_audio_default",
                "visual_workflow_id": visual_workflow_id or None,
            },
        )
        child_id = child.id
        enqueue_bilibili_upload_task(child)
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    except Exception as exc:
        db.session.rollback()
        return jsonify({"success": False, "message": f"B站上传投递失败: {exc}"}), 502

    _clear_jobs_summary_cache(current_user_id)
    item = getattr(child, "_bilibili_enqueue_response_payload", None)
    if item and item.get("metadata_source") not in {None, BILIBILI_UPLOAD_SOURCE}:
        item = None
    if item is None:
        item = {
            "task_id": child_id,
            "status": "queued",
            "step": "queued",
            "progress": 0,
            "title": title,
            "video_key": video_key,
            "cover_key": resolved_cover_key,
        }
    return jsonify({
        "success": True,
        "message": "已投递 B站上传任务",
        "data": {"upload_task": item},
        "upload_task": item,
    })


@report_audio_workflow_bp.route("/jobs/<job_id>/bilibili-upload/<upload_task_id>/cancel", methods=["POST"])
@require_login
def cancel_bilibili_upload(current_user, job_id, upload_task_id):
    task = _get_owned_task(current_user, job_id)
    if not task:
        return jsonify({"success": False, "message": "任务不存在"}), 404

    child = (
        PublicationTask.query
        .filter(PublicationTask.id == upload_task_id)
        .filter(PublicationTask.feature == "publisher")
        .filter(PublicationTask.parent_task_id == task.id)
        .filter(PublicationTask.parent_feature == FEATURE_NAME)
        .first()
    )
    if not child or not bilibili_upload_child_payload(child, source_label=BILIBILI_UPLOAD_SOURCE):
        return jsonify({"success": False, "message": "B站上传任务不存在"}), 404

    ok, message = cancel_bilibili_upload_child(child)
    _clear_jobs_summary_cache(getattr(current_user, "id", None))
    item = bilibili_upload_child_payload(child, source_label=BILIBILI_UPLOAD_SOURCE)
    return jsonify({
        "success": ok,
        "message": message,
        "data": {"upload_task": item},
        "upload_task": item,
    }), (200 if ok else 409)


@report_audio_workflow_bp.route("/jobs/<job_id>/bilibili-upload/<upload_task_id>/retry", methods=["POST"])
@require_login
def retry_bilibili_upload(current_user, job_id, upload_task_id):
    parent = _get_owned_task(current_user, job_id)
    if not parent:
        return jsonify({"success": False, "message": "任务不存在"}), 404

    try:
        child = retry_bilibili_upload_child(
            upload_task_id,
            parent_task_id=parent.id,
            parent_feature=FEATURE_NAME,
            source_label=BILIBILI_UPLOAD_SOURCE,
        )
    except LookupError:
        return jsonify({"success": False, "message": "B站上传任务不存在"}), 404
    except BilibiliUploadRetryConflict as exc:
        return jsonify({"success": False, "message": str(exc)}), 409
    except BilibiliUploadRetryEnqueueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 502
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    except Exception as exc:
        db.session.rollback()
        logger.exception("B站上传重投失败: %s", exc)
        return jsonify({"success": False, "message": f"B站上传重投失败: {exc}"}), 502

    _clear_jobs_summary_cache(getattr(current_user, "id", None))
    item = bilibili_upload_child_payload(child, source_label=BILIBILI_UPLOAD_SOURCE)
    return jsonify({
        "success": True,
        "message": "B站上传任务已重新投递",
        "data": {"upload_task": item},
        "upload_task": item,
    })


@report_audio_workflow_bp.route("/jobs/<job_id>/douyin-uploads", methods=["GET"])
@require_login
def get_douyin_uploads(current_user, job_id):
    task = _get_owned_task_reference(current_user, job_id)
    if not task:
        return jsonify({"success": False, "message": "任务不存在"}), 404
    items = douyin_publish_children(task.id, parent_feature=FEATURE_NAME)
    return jsonify({"success": True, "data": {"items": items}, "items": items})


@report_audio_workflow_bp.route("/jobs/<job_id>/douyin-upload", methods=["POST"])
@require_login
def submit_douyin_upload(current_user, job_id):
    task = _get_owned_task(current_user, job_id)
    if not task:
        return jsonify({"success": False, "message": "任务不存在"}), 404
    data = _request_payload()
    video_key = str(data.get("video_key") or "vertical").strip().lower()
    if video_key not in {"landscape", "vertical"}:
        return jsonify({"success": False, "message": "video_key 只能是 landscape 或 vertical"}), 400
    video_path = _report_bilibili_video_path(task, video_key)
    if not _path_exists(video_path):
        return jsonify({"success": False, "message": "当前任务还没有可上传的视频文件"}), 400
    submit = _truthy(data.get("submit"), default=False)
    if submit and data.get("publish_confirmation") != "PUBLISH":
        return jsonify({"success": False, "message": "确认发布校验失败，请重新勾选"}), 400
    title = clean_bilibili_upload_text(data.get("title"), max_length=100) or _report_bilibili_default_title(task, video_key)[:100]
    try:
        child = create_douyin_publish_task(
            current_user=current_user,
            parent_task=task,
            parent_feature=FEATURE_NAME,
            source_label=DOUYIN_UPLOAD_SOURCE,
            video_path=video_path,
            video_key=video_key,
            title=title,
            description=data.get("description") or data.get("desc") or "",
            tags=data.get("tags") or ["财经", "音频", "研报解读"],
            credential_id=data.get("credential_id"),
            visibility=data.get("visibility") or "private",
            declaration=data.get("declaration") or "none",
            submit=submit,
        )
        enqueue_douyin_publish_task(child)
        item = douyin_publish_payload(child)
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    except RuntimeError as exc:
        return jsonify({"success": False, "message": str(exc)}), 502
    _clear_jobs_summary_cache(getattr(current_user, "id", None))
    return jsonify({
        "success": True,
        "message": "抖音上传任务已入队；发布成功以作品管理页回查为准",
        "data": {"upload_task": item},
        "upload_task": item,
    })


@report_audio_workflow_bp.route("/jobs/<job_id>/douyin-upload/<upload_task_id>/cancel", methods=["POST"])
@require_login
def cancel_douyin_upload(current_user, job_id, upload_task_id):
    parent = _get_owned_task_reference(current_user, job_id)
    if not parent:
        return jsonify({"success": False, "message": "任务不存在"}), 404
    child = PublicationTask.query.filter(
        PublicationTask.id == str(upload_task_id),
        PublicationTask.feature == DOUYIN_FEATURE_NAME,
        PublicationTask.parent_task_id == str(parent.id),
        PublicationTask.parent_feature == FEATURE_NAME,
    ).one_or_none()
    if not child:
        return jsonify({"success": False, "message": "抖音上传任务不存在"}), 404
    ok, message = cancel_douyin_publish_task(child)
    item = douyin_publish_payload(child)
    return jsonify({"success": ok, "message": message, "data": {"upload_task": item}, "upload_task": item}), (200 if ok else 409)


@report_audio_workflow_bp.route("/jobs/<job_id>/wechat-channels-uploads", methods=["GET"])
@require_login
def get_wechat_channels_uploads(current_user, job_id):
    task = _get_owned_task_reference(current_user, job_id)
    if not task:
        return jsonify({"success": False, "message": "任务不存在"}), 404
    items = wechat_channels_publish_children(task.id, parent_feature=FEATURE_NAME)
    return jsonify({"success": True, "data": {"items": items}, "items": items})


@report_audio_workflow_bp.route("/jobs/<job_id>/wechat-channels-upload", methods=["POST"])
@require_login
def submit_wechat_channels_upload(current_user, job_id):
    task = _get_owned_task(current_user, job_id)
    if not task:
        return jsonify({"success": False, "message": "任务不存在"}), 404
    data = _request_payload()
    video_key = str(data.get("video_key") or "vertical").strip().lower()
    if video_key not in {"landscape", "vertical"}:
        return jsonify({"success": False, "message": "video_key 只能是 landscape 或 vertical"}), 400
    video_path = _report_bilibili_video_path(task, video_key)
    if not _path_exists(video_path):
        return jsonify({"success": False, "message": "当前任务还没有可上传的视频文件"}), 400
    submit = _truthy(data.get("submit"), default=False)
    if submit and data.get("publish_confirmation") != "PUBLISH":
        return jsonify({"success": False, "message": "确认发布校验失败，请重新勾选"}), 400
    title = clean_bilibili_upload_text(data.get("title"), max_length=100) or _report_bilibili_default_title(task, video_key)[:100]
    try:
        child = create_wechat_channels_publish_task(
            current_user=current_user, parent_task=task, parent_feature=FEATURE_NAME,
            source_label=WECHAT_CHANNELS_UPLOAD_SOURCE, video_path=video_path,
            video_key=video_key, title=title,
            description=data.get("description") or data.get("desc") or "",
            tags=data.get("tags") or ["财经", "音频", "研报解读"],
            credential_id=data.get("credential_id"),
            visibility=data.get("visibility") or "private", submit=submit,
        )
        enqueue_wechat_channels_publish_task(child)
        item = wechat_channels_publish_payload(child)
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    except RuntimeError as exc:
        return jsonify({"success": False, "message": str(exc)}), 502
    _clear_jobs_summary_cache(getattr(current_user, "id", None))
    return jsonify({"success": True, "message": "已投递视频号上传任务", "data": {"upload_task": item}, "upload_task": item})


@report_audio_workflow_bp.route("/jobs/<job_id>/wechat-channels-upload/<upload_task_id>/cancel", methods=["POST"])
@require_login
def cancel_wechat_channels_upload(current_user, job_id, upload_task_id):
    parent = _get_owned_task_reference(current_user, job_id)
    if not parent:
        return jsonify({"success": False, "message": "任务不存在"}), 404
    child = PublicationTask.query.filter(
        PublicationTask.id == str(upload_task_id),
        PublicationTask.feature == WECHAT_CHANNELS_FEATURE_NAME,
        PublicationTask.parent_task_id == str(parent.id),
        PublicationTask.parent_feature == FEATURE_NAME,
    ).one_or_none()
    if not child:
        return jsonify({"success": False, "message": "视频号上传任务不存在"}), 404
    ok, message = cancel_wechat_channels_publish_task(child)
    item = wechat_channels_publish_payload(child)
    return jsonify({"success": ok, "message": message, "data": {"upload_task": item}, "upload_task": item}), (200 if ok else 409)


def _merge_cover_response_payload(payload, cover_type, cover):
    """Merge a committed cover result without touching an expired ORM object."""
    merged = dict(payload or {})
    video = dict(merged.get("video") or {})
    cover_payload = {
        "path": cover.get("path"),
        "bytes": cover.get("bytes"),
        "title": cover.get("title"),
        "subtitle": cover.get("subtitle"),
        "download_url": cover.get("download_url"),
        "updated_at": cover.get("updated_at"),
    }
    if cover_type == "vertical_background":
        keyframe_key = str(cover.get("keyframe_key") or "vertical_background")
        keyframes = dict(video.get("keyframes") or {})
        keyframes[keyframe_key] = {
            **cover_payload,
            "label": cover.get("label") or "竖屏背景关键帧",
            "ratio": "9:16",
            "scheme": cover.get("scheme"),
        }
        video["keyframes"] = keyframes
        video["vertical_background"] = cover_payload
        video["vertical_background_title"] = cover.get("title")
        video["vertical_background_subtitle"] = cover.get("subtitle")
        video["vertical_background_regenerated_at"] = cover.get("updated_at")
        merged["video"] = video
        return merged
    field_map = {
        "landscape": ("cover", "download_cover_url"),
        "vertical": ("vertical_cover", "download_vertical_cover_url"),
        "podcast": ("podcast_cover", "download_podcast_cover_url"),
        "bilibili": ("bilibili_cover", "download_bilibili_cover_url"),
    }
    video_key, download_key = field_map[cover_type]
    video[video_key] = cover_payload
    regenerated_key = {
        "landscape": "cover_regenerated_at",
        "vertical": "vertical_cover_regenerated_at",
        "podcast": "podcast_cover_regenerated_at",
        "bilibili": "bilibili_cover_regenerated_at",
    }[cover_type]
    video[regenerated_key] = cover.get("updated_at")
    merged["video"] = video
    merged[download_key] = cover.get("download_url")
    if cover_type == "landscape":
        merged["cover_title"] = cover.get("title") or merged.get("cover_title")
        merged["cover_subtitle"] = cover.get("subtitle") or ""
    return merged


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
    title = _cover_title_with_institution(task, title)
    payload = _job_payload(task)

    try:
        cover = _regenerate_cover_for_task(task, title=title, subtitle=subtitle)
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    except Exception as exc:
        db.session.rollback()
        return jsonify({"success": False, "message": f"封皮重生成失败: {exc}"}), 502

    _clear_jobs_summary_cache(getattr(current_user, "id", None))
    payload = _merge_cover_response_payload(payload, "landscape", cover)
    return jsonify({
        "success": True,
        "message": "封皮已重新生成",
        "cover": cover,
        **payload,
        "data": payload,
    })


@report_audio_workflow_bp.route("/jobs/<job_id>/regenerate-cover-asset", methods=["POST"])
@require_login
def regenerate_cover_asset(current_user, job_id):
    task = _get_owned_task(current_user, job_id)
    if not task:
        return jsonify({"success": False, "message": "任务不存在"}), 404

    data = _request_payload()
    cover_type = str(data.get("cover_type") or "").strip().lower()
    title = _normalize_cover_text(data.get("title") or data.get("main_title"), 160)
    subtitle = _normalize_cover_text(data.get("subtitle") or data.get("sub_title"), 220)
    if cover_type not in {"landscape", "vertical", "podcast", "bilibili", "vertical_background"}:
        return jsonify({"success": False, "message": "不支持的封皮类型"}), 400
    if not title:
        return jsonify({"success": False, "message": "请填写封皮标题"}), 400
    if cover_type != "vertical_background":
        title = _cover_title_with_institution(task, title)
    payload = _job_payload(task)

    try:
        if cover_type == "landscape":
            cover = _regenerate_cover_for_task(task, title=title, subtitle=subtitle)
        elif cover_type == "vertical":
            cover = _generate_vertical_cover_for_task(task, title=title, subtitle=subtitle)
        elif cover_type == "podcast":
            cover = _generate_podcast_cover_for_task(task, title=title, subtitle=subtitle)
        elif cover_type == "vertical_background":
            cover = _generate_vertical_background_for_task(
                task,
                scheme=data.get("scheme"),
                title=title,
                subtitle=subtitle,
            )
        else:
            cover = _generate_bilibili_cover_for_task(task, title=title, subtitle=subtitle)
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    except Exception as exc:
        db.session.rollback()
        return jsonify({"success": False, "message": f"封皮重生成失败: {exc}"}), 502

    _clear_jobs_summary_cache(getattr(current_user, "id", None))
    payload = _merge_cover_response_payload(payload, cover_type, cover)
    return jsonify({
        "success": True,
        "message": "当前封皮已重新生成",
        "cover_type": cover_type,
        "cover": cover,
        **payload,
        "data": payload,
    })


@report_audio_workflow_bp.route("/jobs/<job_id>/generate-podcast-cover", methods=["POST"])
@require_login
def generate_podcast_cover(current_user, job_id):
    task = _get_owned_task(current_user, job_id)
    if not task:
        return jsonify({"success": False, "message": "任务不存在"}), 404

    data = _request_payload()
    force = _truthy(data.get("force"), False)
    scheme = _normalize_vertical_video_scheme(
        data.get("scheme") or data.get("vertical_scheme") or data.get("vertical_video_scheme") or _task_vertical_video_scheme(task)
    )
    existing_path = _podcast_cover_path_for_task(task)
    if existing_path and not force:
        payload = _job_payload(task)
        return jsonify({"success": True, "message": "播客封皮已存在", **payload, "data": payload})

    payload = _job_payload(task)
    try:
        cover = _generate_podcast_cover_for_task(task, scheme=scheme)
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    except Exception as exc:
        db.session.rollback()
        return jsonify({"success": False, "message": f"播客封皮生成失败: {exc}"}), 502

    _clear_jobs_summary_cache(getattr(current_user, "id", None))
    payload = _merge_cover_response_payload(payload, "podcast", cover)
    return jsonify({
        "success": True,
        "message": "播客封皮已生成",
        "podcast_cover": cover,
        **payload,
        "data": payload,
    })


@report_audio_workflow_bp.route("/jobs/<job_id>/generate-bilibili-cover", methods=["POST"])
@require_login
def generate_bilibili_cover(current_user, job_id):
    task = _get_owned_task(current_user, job_id)
    if not task:
        return jsonify({"success": False, "message": "任务不存在"}), 404

    data = _request_payload()
    force = _truthy(data.get("force"), False)
    existing_path = _bilibili_cover_path_for_task(task)
    if existing_path and not force:
        payload = _job_payload(task)
        return jsonify({"success": True, "message": "B站封皮已存在", **payload, "data": payload})

    payload = _job_payload(task)
    try:
        cover = _generate_bilibili_cover_for_task(task)
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    except Exception as exc:
        db.session.rollback()
        return jsonify({"success": False, "message": f"B站封皮生成失败: {exc}"}), 502

    _clear_jobs_summary_cache(getattr(current_user, "id", None))
    payload = _merge_cover_response_payload(payload, "bilibili", cover)
    return jsonify({
        "success": True,
        "message": "B站封皮已生成",
        "bilibili_cover": cover,
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


@report_audio_workflow_bp.route("/jobs/<job_id>/confirm-keyframes", methods=["POST"])
@require_login
def confirm_keyframes(current_user, job_id):
    """Persist one complete post-keyframe intent and continue it idempotently."""

    task = _get_owned_task(current_user, job_id)
    if not task:
        return jsonify({"success": False, "message": "任务不存在"}), 404
    data = _request_payload()
    schemes = _normalize_vertical_video_schemes(
        data.get("vertical_video_schemes") or ["scheme3"]
    )
    include_landscape = _truthy(data.get("include_landscape"), True)
    try:
        finalization = create_or_resume_finalization(
            task.id,
            include_landscape=include_landscape,
            schemes=schemes,
            visual_scheme_key=str(data.get("visual_scheme_key") or "stick_figure"),
            selection_source=str(data.get("selection_source") or "keyframe-confirmation"),
            force=_truthy(data.get("force"), True),
        )
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    except Exception as exc:
        db.session.rollback()
        logger.exception("Keyframe finalization failed to persist job=%s", job_id)
        return jsonify({"success": False, "message": f"确认关键帧失败：{exc}"}), 502

    _clear_jobs_summary_cache(getattr(current_user, "id", None))
    task = _get_owned_task(current_user, job_id)
    visual_videos = _linked_visual_video_payloads(current_user, task)
    payload = _job_payload(task, visual_videos=visual_videos)
    summary = finalization_summary(finalization)
    payload["finalization"] = summary
    payload["children"] = {**(payload.get("children") or {}), "finalization": summary}
    step = str((summary or {}).get("step") or "")
    message = {
        "done": "关键帧已确认；白板、横屏和方案三竖屏均已完成",
        "final_video_queued": "关键帧已确认；白板已就绪，横屏 + 方案三正在合成",
        "waiting_visual": "关键帧已确认；白板正在生成，完成后将自动合成横屏 + 方案三",
        "visual_queued": "关键帧已确认；白板任务已创建，完成后将自动合成横屏 + 方案三",
    }.get(step, (summary or {}).get("message") or "关键帧已确认，完整成片链路已接管")
    status_code = 200 if str((summary or {}).get("status") or "") == "completed" else 202
    return jsonify({
        "success": True,
        "message": message,
        "accepted": status_code == 202,
        "finalization": summary,
        **payload,
        "data": payload,
    }), status_code


@report_audio_workflow_bp.route("/jobs/<job_id>/generate-vertical-video", methods=["POST"])
@require_login
def generate_vertical_video(current_user, job_id):
    task = _get_owned_task(current_user, job_id)
    if not task:
        return jsonify({"success": False, "message": "任务不存在"}), 404

    data = _request_payload()
    has_explicit_options = any(
        key in data
        for key in ("scheme", "vertical_scheme", "vertical_video_scheme", "vertical_video_schemes", "preview_seconds", "duration_seconds", "force", "include_landscape")
    )
    explicit_schemes = "vertical_video_schemes" in data
    requested_schemes = (
        _normalize_vertical_video_schemes(data.get("vertical_video_schemes"))
        if explicit_schemes
        else _normalize_vertical_video_schemes(
            data.get("scheme") or data.get("vertical_scheme") or data.get("vertical_video_scheme"),
            _task_vertical_video_scheme(task),
        )
    )
    if len(requested_schemes) > 2:
        return jsonify({"success": False, "message": "竖屏方案一次最多选择两个"}), 400
    schemes = list(requested_schemes)
    scheme = _primary_vertical_video_scheme(schemes) if schemes else _normalize_vertical_video_scheme(_task_vertical_video_scheme(task))
    preview_seconds = _bounded_int(
        data.get("preview_seconds") or data.get("duration_seconds") or 0,
        0,
        0,
        600,
    )
    force = _truthy(data.get("force"), False)
    include_landscape = _truthy(data.get("include_landscape"), False)
    visual_workflow_id = str(data.get("visual_workflow_id") or "").strip()
    visual_video_path = None
    visual_scene_timeline = None
    skipped_outputs = []
    if "scheme3" in schemes:
        if not visual_workflow_id:
            visual_workflow_id = _ready_linked_visual_workflow_id(
                current_user,
                task,
                str(data.get("visual_scheme_key") or "stick_figure"),
            )
        if not visual_workflow_id:
            reason = "方案三未找到同一 PDF 且正文图片完整的视觉方案"
            if not include_landscape:
                return jsonify({"success": False, "message": reason}), 400
            schemes = [item for item in schemes if item != "scheme3"]
            skipped_outputs.append({"output": "scheme3", "reason": reason})
        else:
            visual_scene_timeline = _linked_visual_scene_timeline(
                current_user,
                task,
                visual_workflow_id,
                audio_seconds=float((((task.result if isinstance(task.result, dict) else {}).get("audio") or {}).get("seconds")) or 0),
            )
            visual_video_path = _linked_visual_video_path(current_user, task, visual_workflow_id)
            if not visual_scene_timeline and not visual_video_path:
                reason = "方案三所选视觉方案的正文分镜图片尚未全部完成，或不属于同一 PDF"
                if not include_landscape:
                    return jsonify({"success": False, "message": reason}), 400
                schemes = [item for item in schemes if item != "scheme3"]
                skipped_outputs.append({"output": "scheme3", "reason": reason})
    if not include_landscape and not schemes:
        return jsonify({"success": False, "message": "请至少选择一个成片版本"}), 400
    if include_landscape and preview_seconds > 0:
        return jsonify({"success": False, "message": "横屏成片不能使用竖屏预览时长"}), 400

    existing_path = _vertical_video_path_for_task(task)
    if existing_path and not has_explicit_options:
        payload = _job_payload(task)
        return jsonify({"success": True, "message": "竖屏 MP4 已存在", **payload, "data": payload})

    existing_child = (
        _latest_matching_final_video_child(task.id, scheme=scheme)
        if include_landscape
        else _latest_matching_vertical_video_child(task.id, scheme=scheme, preview_seconds=preview_seconds)
    )
    if existing_child and existing_child.status not in TERMINAL_STATUSES:
        payload = _job_payload(task)
        return jsonify({"success": True, "message": "竖屏 MP4 已在生成队列中", **payload, "data": payload})
    if existing_child and existing_child.status == "completed" and _path_exists(_vertical_video_path_from_child(existing_child)) and not force:
        payload = _job_payload(task)
        return jsonify({"success": True, "message": "同方案竖屏 MP4 已存在", **payload, "data": payload})

    # _create_vertical_video_backfill_task commits twice.  SQLAlchemy expires the
    # parent/child ORM objects after those commits, so serializing either object
    # afterwards performs another database query.  A transient failure in that
    # response-only query used to turn an already queued task into a false 500.
    # Snapshot the parent response before the durable enqueue operation instead.
    payload = _job_payload(task)
    try:
        child = _create_vertical_video_backfill_task(
            task,
            scheme=scheme,
            schemes=schemes,
            preview_seconds=preview_seconds,
            include_landscape=include_landscape,
            visual_workflow_id=visual_workflow_id,
            visual_video_path=visual_video_path,
            visual_scene_timeline=visual_scene_timeline,
        )
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    except Exception as exc:
        db.session.rollback()
        return jsonify({"success": False, "message": f"竖屏 MP4 入队失败: {exc}"}), 502

    _clear_jobs_summary_cache(getattr(current_user, "id", None))
    child_summary = getattr(child, "_enqueue_response_summary", None) or {
        "job_id": getattr(child, "id", None),
        "status": "pending",
        "step": "queued",
        "progress": 0,
        "vertical_video_scheme": scheme,
        "vertical_video_scheme_label": _vertical_video_scheme_label(scheme),
        "vertical_video_schemes": schemes,
        "preview_seconds": preview_seconds,
        "is_preview": preview_seconds > 0,
    }
    child_key = "publisher_vertical_preview_video" if preview_seconds > 0 else "publisher_vertical_video"
    payload = {
        **payload,
        child_key: child_summary,
        "children": {**(payload.get("children") or {}), child_key: child_summary},
    }
    preview_label = f"{preview_seconds}s 预览" if preview_seconds > 0 else "完整视频"
    selected_labels = (["横屏"] if include_landscape else []) + [
        f"{_vertical_video_scheme_label(item)}竖屏" for item in schemes
    ]
    output_label = " + ".join(selected_labels)
    message = f"{output_label}已提交生成（{preview_label}）"
    if skipped_outputs:
        message += "；方案三竖屏因视觉方案未就绪暂未提交"
    return jsonify({
        "success": True,
        "message": message,
        "partial_success": bool(skipped_outputs),
        "skipped_outputs": skipped_outputs,
        "requested_vertical_video_schemes": requested_schemes,
        "submitted_vertical_video_schemes": schemes,
        "vertical_video_task": child_summary,
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

    retry_options = request.get_json(silent=True) if has_request_context() else {}
    retry_options = retry_options if isinstance(retry_options, dict) else {}
    requested_tts_provider = str(retry_options.get("tts_provider") or "").strip().lower()
    requested_tts_voice = str(retry_options.get("tts_voice") or TTS_VOICE_DEFAULT).strip()
    if requested_tts_provider and requested_tts_provider not in TTS_PROVIDERS:
        return jsonify({"success": False, "message": "tts_provider 仅支持 doubao_browser 或 doubao_api"}), 400
    if requested_tts_provider == "doubao_api":
        if requested_tts_voice not in TTS_VOICES:
            return jsonify({"success": False, "message": "不支持所选豆包 API 音色"}), 400
        if not is_doubao_api_configured():
            return jsonify({"success": False, "message": "豆包 API TTS 未配置，请设置 DOUBAO_TTS_API_KEY"}), 400
    elif requested_tts_provider == "doubao_browser" and not doubao_browser_tts.status_payload().get("configured"):
        return jsonify({"success": False, "message": "豆包浏览器朗读未配置"}), 400

    result = dict(task.result or {})
    retry_requested_at = datetime.utcnow().isoformat() + "Z"
    logs = list(result.get("logs") or [])
    provider_changed = False
    previous_tts_provider = ""
    params = deepcopy(task.params) if isinstance(task.params, dict) else {}
    workflow_key = (
        FEATURE_NAME
        if isinstance(params.get(FEATURE_NAME), dict)
        else "report_audio_workflow"
    )
    workflow = dict(params.get(workflow_key) or {})
    workflow_changed = False
    if "pdf_evidence_enabled" in workflow and not _truthy(workflow.get("pdf_evidence_enabled"), False):
        cleaned_review_prompt = review_prompt_for_pdf_evidence(
            workflow.get("review_prompt") or DEFAULT_REVIEW_PROMPT,
            enabled=False,
        )
        if cleaned_review_prompt != workflow.get("review_prompt"):
            workflow["review_prompt"] = cleaned_review_prompt
            workflow_changed = True
    if requested_tts_provider:
        previous_tts_provider = str(workflow.get("tts_provider") or TTS_PROVIDER_DEFAULT).strip().lower()
        provider_changed = previous_tts_provider != requested_tts_provider or (
            requested_tts_provider == "doubao_api"
            and str(workflow.get("tts_voice") or "") != requested_tts_voice
        )
        workflow["tts_provider"] = requested_tts_provider
        workflow["tts_voice"] = requested_tts_voice if requested_tts_provider == "doubao_api" else None
        workflow_changed = True
    if workflow_changed:
        params[workflow_key] = workflow
        task.params = params
        flag_modified(task, "params")
    logs.append({
        "step": "retry_requested",
        "message": (
            f"用户将 TTS 从 {previous_tts_provider or '原方案'} 切换为 {requested_tts_provider} 后续跑"
            if provider_changed
            else "用户请求从失败处续跑编排"
        ),
        "progress": task.progress or 0,
        "updated_at": retry_requested_at,
    })
    result["logs"] = logs[-120:]
    result["retry_requested_at"] = retry_requested_at
    if requested_tts_provider:
        result["retry_tts_override"] = {
            "previous_provider": previous_tts_provider or None,
            "provider": requested_tts_provider,
            "voice": requested_tts_voice if requested_tts_provider == "doubao_api" else None,
            "changed": provider_changed,
            "updated_at": retry_requested_at,
        }
    result["error"] = None
    result.pop("error_type", None)
    result.pop("completed_at", None)
    queue_repair = result.pop("queue_repair", None)
    if isinstance(queue_repair, dict):
        queue_repair_history = list(result.get("queue_repair_history") or [])
        queue_repair_history.append({
            **queue_repair,
            "archived_at": retry_requested_at,
            "archive_reason": "manual_retry",
        })
        result["queue_repair_history"] = queue_repair_history[-8:]
    task.status = "pending"
    task.current_step = "queued"
    task.error_message = None
    task.completed_at = None
    task.result = result
    flag_modified(task, "result")
    task_key = str(task.id)
    # Preserve the rich response contract while the task payload is already in
    # memory. Commits below expire ORM attributes and must not trigger another
    # params/result point read merely to serialize the enqueue response.
    payload = _job_payload(task)
    db.session.commit()

    try:
        submit_result = maybe_guarded_apply_async(
            process_report_audio_workflow_job,
            args=[task_key],
            queue=QUEUE_NAME,
            feature=FEATURE_NAME,
            task_id=task_key,
            retry=False,
        )
        celery_task = submit_result["async_result"]
    except Exception as exc:
        error_message = f"续跑任务入队失败: {exc}"
        PublicationTask.query.filter(PublicationTask.id == task_key).update(
            {
                PublicationTask.status: "failed",
                PublicationTask.current_step: "failed",
                PublicationTask.error_message: error_message,
                PublicationTask.completed_at: datetime.utcnow(),
                PublicationTask.updated_at: datetime.utcnow(),
            },
            synchronize_session=False,
        )
        db.session.commit()
        _clear_jobs_summary_cache(getattr(current_user, "id", None))
        return jsonify({"success": False, "message": error_message}), 502

    PublicationTask.query.filter(PublicationTask.id == task_key).update(
        {
            PublicationTask.celery_task_id: celery_task.id,
            PublicationTask.updated_at: datetime.utcnow(),
        },
        synchronize_session=False,
    )
    db.session.commit()
    _clear_jobs_summary_cache(getattr(current_user, "id", None))
    deferred_state = submit_result.get("deferred_state")
    if isinstance(deferred_state, dict) and deferred_state.get("current_step"):
        payload["step"] = deferred_state["current_step"]
    payload["deferred"] = bool(submit_result.get("deferred"))
    payload["admission"] = submit_result.get("admission")
    return jsonify({"success": True, "message": "任务已从失败处续跑", **payload, "data": payload})


@report_audio_workflow_bp.route("/jobs/<job_id>/regenerate-script", methods=["POST"])
@require_login
def regenerate_script(current_user, job_id):
    """Create an independent Pro-only script revision without mutating source media."""

    source = _get_owned_task(current_user, job_id)
    if not source:
        return jsonify({"success": False, "message": "任务不存在"}), 404
    source_payload = (
        (source.params or {}).get("report_audio_workflow")
        if isinstance(source.params, dict)
        else {}
    ) or {}
    pdf = deepcopy(source_payload.get("pdf") or (source.result or {}).get("pdf") or {})
    pdf_path = str(source_payload.get("pdf_path") or pdf.get("path") or "").strip()
    if not pdf_path or not os.path.isfile(pdf_path):
        return jsonify({"success": False, "message": "原始 PDF 文件不存在，无法重新生成文案"}), 409

    active = (
        PublicationTask.query
        .filter(
            PublicationTask.feature == FEATURE_NAME,
            PublicationTask.source_task_id == source.id,
            PublicationTask.parent_feature == SCRIPT_REGENERATION_PARENT_FEATURE,
            PublicationTask.status.in_(["pending", "processing"]),
        )
        .order_by(PublicationTask.created_at.desc())
        .first()
    )
    if active:
        active_payload = _job_payload(active)
        detail_url = f"report-audio-workflow-detail.html?job_id={active.id}"
        return jsonify({
            "success": True,
            "message": "已有同一文案重生成任务正在运行，已返回原任务",
            "regeneration_job_id": active.id,
            "detail_url": detail_url,
            **active_payload,
            "data": {**active_payload, "regeneration_job_id": active.id, "detail_url": detail_url},
        }), 202

    data = _request_payload()
    minimum_chars = _bounded_int(
        data.get("minimum_chars"),
        DEFAULT_SCRIPT_REGENERATION_MIN_CHARS,
        DEFAULT_SCRIPT_REGENERATION_MIN_CHARS,
        20000,
    )
    prompts = script_regeneration_prompts(
        source_payload.get("prompt") or DEFAULT_INITIAL_PROMPT,
        source_payload.get("review_prompt") or DEFAULT_REVIEW_PROMPT,
        minimum_chars=minimum_chars,
    )
    now = datetime.utcnow().isoformat() + "Z"
    metadata = deepcopy(source_payload.get("metadata") or {})
    metadata.update({
        "source": SCRIPT_REGENERATION_PARENT_FEATURE,
        "regeneration_source_task_id": source.id,
        "requested_at": now,
        "minimum_chars": minimum_chars,
        "require_pro_model": True,
    })
    video_options = deepcopy(source_payload.get("video") or {})
    video_options.update({
        "enabled": False,
        "vertical_enabled": False,
        "bilibili_enabled": False,
        "keyframe_outputs": [],
    })
    regeneration_payload = {
        **deepcopy(source_payload),
        "prompt": prompts["prompt"],
        "review_prompt": prompts["review_prompt"],
        "followups": [],
        "manual_gemini": None,
        "metadata": metadata,
        "script_material_only": True,
        "pdf_evidence_enabled": False,
        "force_gemini_regeneration": True,
        "gemini_prefer_pro_model": True,
        "gemini_require_pro_model": True,
        "gemini_min_final_chars": minimum_chars,
        "visual_scheme_keys": [],
        "video": video_options,
        "pdf": pdf,
        "pdf_path": pdf_path,
    }
    user_id = str(source.user_id or getattr(current_user, "id", None))
    task = PublicationTask(
        status="pending",
        current_step="queued",
        progress=0,
        feature=FEATURE_NAME,
        user_id=user_id,
        source_task_id=source.id,
        source_feature=FEATURE_NAME,
        parent_feature=SCRIPT_REGENERATION_PARENT_FEATURE,
        params={
            "feature": FEATURE_NAME,
            "user_id": user_id,
            "source_task_id": source.id,
            "source_feature": FEATURE_NAME,
            "parent_feature": SCRIPT_REGENERATION_PARENT_FEATURE,
            "report_audio_workflow": regeneration_payload,
        },
        result={
            "metadata": metadata,
            "source_task_id": source.id,
            "pdf": pdf,
            "children": {},
            "structured_result": None,
            "script_material": {"status": "pending"},
            "audio": None,
            "video": None,
            "logs": [],
            "regeneration": {
                "source_task_id": source.id,
                "minimum_chars": minimum_chars,
                "require_pro_model": True,
                "created_at": now,
            },
            "pdf_evidence": {"status": "disabled", "enabled": False},
        },
    )
    db.session.add(task)
    db.session.flush()
    task_id = str(task.id)
    db.session.commit()

    try:
        submit_result = maybe_guarded_apply_async(
            process_report_audio_workflow_job,
            args=[task_id],
            queue=QUEUE_NAME,
            feature=FEATURE_NAME,
            task_id=task_id,
            retry=False,
        )
        celery_task = submit_result["async_result"]
    except Exception as exc:
        error_message = f"文案重生成任务入队失败: {exc}"
        PublicationTask.query.filter(PublicationTask.id == task_id).update(
            {
                PublicationTask.status: "failed",
                PublicationTask.current_step: "failed",
                PublicationTask.error_message: error_message,
                PublicationTask.completed_at: datetime.utcnow(),
            },
            synchronize_session=False,
        )
        db.session.commit()
        _clear_jobs_summary_cache(getattr(current_user, "id", None))
        return jsonify({"success": False, "message": error_message}), 502

    PublicationTask.query.filter(PublicationTask.id == task_id).update(
        {
            PublicationTask.celery_task_id: celery_task.id,
            PublicationTask.updated_at: datetime.utcnow(),
        },
        synchronize_session=False,
    )
    db.session.commit()
    _clear_jobs_summary_cache(getattr(current_user, "id", None))
    detail_url = f"report-audio-workflow-detail.html?job_id={task_id}"
    response_payload = {
        "job_id": task_id,
        "regeneration_job_id": task_id,
        "status": "queued",
        "step": "queued",
        "progress": 0,
        "minimum_chars": minimum_chars,
        "require_pro_model": True,
        "detail_url": detail_url,
        "deferred": bool(submit_result.get("deferred")),
        "admission": submit_result.get("admission"),
    }
    return jsonify({
        "success": True,
        "message": f"已创建独立文案重生成任务：确认 Pro 后生成至少 {minimum_chars} 字",
        **response_payload,
        "data": response_payload,
    }), 202


@report_audio_workflow_bp.route("/jobs/<job_id>/generate-audio", methods=["POST"])
@require_login
def generate_audio_from_script(current_user, job_id):
    """Continue one completed script material into an independent TTS task."""

    source = _get_owned_task(current_user, job_id)
    if not source:
        return jsonify({"success": False, "message": "任务不存在"}), 404
    if source.status != "completed":
        return jsonify({"success": False, "message": "文案任务尚未完成，暂不能生成语音"}), 409

    source_result = source.result if isinstance(source.result, dict) else {}
    structured = deepcopy(source_result.get("structured_result") or {})
    script = str(structured.get("final_script_for_tts") or "").strip()
    if not script:
        return jsonify({"success": False, "message": "当前任务没有可朗读的最终文案"}), 409
    quality_issue = structured_result_quality_issue(structured)
    if quality_issue:
        return jsonify({"success": False, "message": f"当前文案不适合直接朗读：{quality_issue}"}), 409

    source_payload = (
        (source.params or {}).get("report_audio_workflow")
        if isinstance(source.params, dict)
        else {}
    ) or {}
    pdf = deepcopy(source_payload.get("pdf") or source_result.get("pdf") or {})
    pdf_path = str(source_payload.get("pdf_path") or pdf.get("path") or "").strip()
    if not pdf_path or not os.path.isfile(pdf_path):
        return jsonify({"success": False, "message": "原始 PDF 文件不存在，无法生成语音任务"}), 409

    data = _request_payload()
    tts_provider = str(
        data.get("tts_provider")
        or source_payload.get("tts_provider")
        or TTS_PROVIDER_DEFAULT
    ).strip().lower()
    tts_voice = str(
        data.get("tts_voice")
        or source_payload.get("tts_voice")
        or TTS_VOICE_DEFAULT
    ).strip()
    if tts_provider not in TTS_PROVIDERS:
        return jsonify({"success": False, "message": "tts_provider 仅支持 doubao_browser 或 doubao_api"}), 400
    if tts_provider == "doubao_api":
        if tts_voice not in TTS_VOICES:
            return jsonify({"success": False, "message": "不支持所选豆包 API 音色"}), 400
        if not is_doubao_api_configured():
            return jsonify({"success": False, "message": "豆包 API TTS 未配置，请设置 DOUBAO_TTS_API_KEY"}), 400
    elif not doubao_browser_tts.status_payload().get("configured"):
        return jsonify({"success": False, "message": "豆包浏览器朗读未配置"}), 400

    _lock_audio_generation_source(source.id)
    active = (
        PublicationTask.query
        .filter(
            PublicationTask.feature == FEATURE_NAME,
            PublicationTask.source_task_id == source.id,
            PublicationTask.parent_feature == AUDIO_GENERATION_PARENT_FEATURE,
            PublicationTask.status.in_(["pending", "processing"]),
        )
        .order_by(PublicationTask.created_at.desc())
        .first()
    )
    if active:
        active_payload = _job_payload(active)
        detail_url = f"report-audio-workflow-detail.html?job_id={active.id}"
        response_payload = {
            **active_payload,
            "audio_generation_job_id": active.id,
            "detail_url": detail_url,
        }
        return jsonify({
            "success": True,
            "message": "已有同一文案的语音任务正在运行，已返回原任务",
            **response_payload,
            "data": response_payload,
        }), 202

    now = datetime.utcnow().isoformat() + "Z"
    metadata = deepcopy(source_payload.get("metadata") or source_result.get("metadata") or {})
    metadata.update({
        "source": AUDIO_GENERATION_PARENT_FEATURE,
        "audio_source_task_id": source.id,
        "requested_at": now,
        "script_chars": len(script),
        "tts_provider": tts_provider,
    })
    video_options = deepcopy(source_payload.get("video") or {})
    video_options.update({
        "enabled": False,
        "vertical_enabled": False,
        "bilibili_enabled": False,
        "keyframe_outputs": [],
    })
    audio_payload = {
        **deepcopy(source_payload),
        "manual_gemini": None,
        "script_material_only": False,
        "force_gemini_regeneration": False,
        "pdf_evidence_enabled": False,
        "tts_provider": tts_provider,
        "tts_voice": tts_voice if tts_provider == "doubao_api" else None,
        "visual_scheme_keys": [],
        "video": video_options,
        "pdf": pdf,
        "pdf_path": pdf_path,
        "metadata": metadata,
    }
    # The script has already passed the material task's quality gate. Do not
    # inherit a stricter Gemini-generation threshold into this TTS-only hop.
    audio_payload.pop("gemini_min_final_chars", None)

    script_material = deepcopy(source_result.get("script_material") or {})
    script_material.update({
        "status": "completed",
        "mode": "audio_generation_source",
        "source_task_id": source.id,
        "chars": len(script),
    })
    user_id = str(source.user_id or getattr(current_user, "id", None))
    task = PublicationTask(
        status="pending",
        current_step="queued",
        progress=0,
        feature=FEATURE_NAME,
        user_id=user_id,
        source_task_id=source.id,
        source_feature=FEATURE_NAME,
        parent_feature=AUDIO_GENERATION_PARENT_FEATURE,
        params={
            "feature": FEATURE_NAME,
            "user_id": user_id,
            "source_task_id": source.id,
            "source_feature": FEATURE_NAME,
            "parent_feature": AUDIO_GENERATION_PARENT_FEATURE,
            "report_audio_workflow": audio_payload,
        },
        result={
            "metadata": metadata,
            "source_task_id": source.id,
            "pdf": pdf,
            "children": {},
            "structured_result": structured,
            "script_material": script_material,
            "audio": None,
            "video": None,
            "logs": [],
            "audio_generation": {
                "source_task_id": source.id,
                "tts_provider": tts_provider,
                "tts_voice": tts_voice if tts_provider == "doubao_api" else None,
                "script_chars": len(script),
                "created_at": now,
            },
            "pdf_evidence": {"status": "disabled", "enabled": False},
        },
    )
    db.session.add(task)
    db.session.flush()
    task_id = str(task.id)
    db.session.commit()

    try:
        submit_result = maybe_guarded_apply_async(
            process_report_audio_workflow_job,
            args=[task_id],
            queue=QUEUE_NAME,
            feature=FEATURE_NAME,
            task_id=task_id,
            retry=False,
        )
        celery_task = submit_result["async_result"]
    except Exception as exc:
        error_message = f"语音任务入队失败: {exc}"
        PublicationTask.query.filter(PublicationTask.id == task_id).update(
            {
                PublicationTask.status: "failed",
                PublicationTask.current_step: "failed",
                PublicationTask.error_message: error_message,
                PublicationTask.completed_at: datetime.utcnow(),
                PublicationTask.updated_at: datetime.utcnow(),
            },
            synchronize_session=False,
        )
        db.session.commit()
        _clear_jobs_summary_cache(getattr(current_user, "id", None))
        return jsonify({"success": False, "message": error_message}), 502

    PublicationTask.query.filter(PublicationTask.id == task_id).update(
        {
            PublicationTask.celery_task_id: celery_task.id,
            PublicationTask.updated_at: datetime.utcnow(),
        },
        synchronize_session=False,
    )
    db.session.commit()
    _clear_jobs_summary_cache(getattr(current_user, "id", None))
    detail_url = f"report-audio-workflow-detail.html?job_id={task_id}"
    response_payload = {
        "job_id": task_id,
        "audio_generation_job_id": task_id,
        "status": "queued",
        "step": "queued",
        "progress": 0,
        "tts_provider": tts_provider,
        "tts_voice": tts_voice if tts_provider == "doubao_api" else None,
        "detail_url": detail_url,
        "deferred": bool(submit_result.get("deferred")),
        "admission": submit_result.get("admission"),
    }
    return jsonify({
        "success": True,
        "message": "已使用当前文案创建独立语音任务，跳过 Gemini 且不覆盖旧产物",
        **response_payload,
        "data": response_payload,
    }), 202


def _lock_audio_generation_source(source_task_id):
    """Serialize creation for one source task on PostgreSQL."""

    bind = db.session.get_bind()
    if bind is not None and bind.dialect.name == "postgresql":
        db.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
            {"key": f"report-audio-script-tts:{source_task_id}"},
        )


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
            PublicationTask.report_audio_pdf_attachment_id,
            PublicationTask.report_audio_pdf_filename,
            PublicationTask.report_audio_pdf_bytes,
        )
    ).filter(PublicationTask.id == str(job_id)).one_or_none()
    if not task or (task.feature or (task.params or {}).get("feature")) != FEATURE_NAME:
        return None
    task_user_id = str(task.user_id or (task.params or {}).get("user_id") or "").strip()
    if task_user_id not in _visible_user_ids(current_user):
        return None
    return task


def _get_owned_task_reference(current_user, job_id):
    # A completed detail page opens several platform/asset subrequests at once.
    # Share their immutable ownership snapshot while active status polling stays fresh.
    return _get_owned_asset_reference(current_user, job_id, terminal_only=True)


def _get_owned_media_task(current_user, job_id):
    """Load ownership and projected cover paths without opening task JSON."""
    return _get_owned_asset_reference(current_user, job_id)


def _get_owned_asset_reference(current_user, job_id, *, terminal_only=False):
    user_id = str(getattr(current_user, "id", None) or "")
    cache_key = (user_id, str(job_id))
    now = time.time()
    with _REPORT_AUDIO_ASSET_REFERENCE_CACHE_LOCK:
        cached = _REPORT_AUDIO_ASSET_REFERENCE_CACHE.get(cache_key)
        if cached and float(cached.get("expires_at") or 0) > now:
            cached_task = cached["task"]
            if not terminal_only or cached_task.status in TERMINAL_STATUSES:
                return deepcopy(cached_task)
    # Never hold the process-wide cache lock while waiting on the remote DB.
    # A single dead server connection otherwise blocks every status/media
    # request for this endpoint family until the network timeout expires.
    row = (
        db.session.query(*OWNED_TASK_REFERENCE_COLUMNS)
        .filter(PublicationTask.id == str(job_id))
        .one_or_none()
    )
    if not row:
        return None
    if row.feature != FEATURE_NAME or not row.user_id:
        # Legacy rows were backfilled, but preserve access for an incomplete old row.
        return _get_owned_task(current_user, job_id)
    if str(row.user_id).strip() not in _visible_user_ids(current_user):
        return None
    task = SimpleNamespace(**{
        column.key: getattr(row, column.key)
        for column in OWNED_TASK_REFERENCE_COLUMNS
    })
    if not terminal_only or task.status in TERMINAL_STATUSES:
        with _REPORT_AUDIO_ASSET_REFERENCE_CACHE_LOCK:
            _REPORT_AUDIO_ASSET_REFERENCE_CACHE[cache_key] = {
                "expires_at": now + REPORT_AUDIO_MEDIA_PATH_CACHE_TTL_SECONDS,
                "task": deepcopy(task),
            }
    return task


def _get_owned_keyframe_task(current_user, job_id):
    """Keyframes remain an audit/detail payload and are loaded one task at a time."""
    user_id = str(getattr(current_user, "id", None) or "")
    cache_key = (user_id, str(job_id))
    now = time.time()
    with _REPORT_AUDIO_KEYFRAME_TASK_CACHE_LOCK:
        cached = _REPORT_AUDIO_KEYFRAME_TASK_CACHE.get(cache_key)
        if cached and float(cached.get("expires_at") or 0) > now:
            return deepcopy(cached["task"])
    row = (
        db.session.query(
            PublicationTask.id,
            PublicationTask.result,
            PublicationTask.feature,
            PublicationTask.user_id,
        )
        .filter(PublicationTask.id == str(job_id))
        .one_or_none()
    )
    if not row or row.feature != FEATURE_NAME or not row.user_id:
        return None
    if str(row.user_id).strip() not in _visible_user_ids(current_user):
        return None
    task = SimpleNamespace(
        id=row.id,
        result=deepcopy(row.result or {}),
        feature=row.feature,
        user_id=row.user_id,
    )
    with _REPORT_AUDIO_KEYFRAME_TASK_CACHE_LOCK:
        _REPORT_AUDIO_KEYFRAME_TASK_CACHE[cache_key] = {
            "expires_at": now + REPORT_AUDIO_MEDIA_PATH_CACHE_TTL_SECONDS,
            "task": deepcopy(task),
        }
    return task


def _job_detail_version(task):
    child_updated_at = (
        db.session.query(func.max(PublicationTask.updated_at))
        .filter(or_(
            PublicationTask.parent_task_id == task.id,
            PublicationTask.source_task_id == task.id,
        ))
        .scalar()
    )
    return (
        task.status,
        task.current_step,
        task.updated_at.isoformat() if task.updated_at else None,
        child_updated_at.isoformat() if child_updated_at else None,
    )


def _normalize_cover_text(value, max_length):
    lines = [
        " ".join(line.split())
        for line in str(value or "").replace("\r", "\n").split("\n")
        if line.strip()
    ]
    text = "\n".join(lines).strip()
    return text[:max_length].rstrip()


def _request_payload():
    data = request.get_json(silent=True)
    if isinstance(data, dict):
        return data
    if request.form:
        return request.form.to_dict(flat=True)
    return {}


def _normalize_vertical_video_scheme(value):
    text = str(value or "").strip().lower()
    aliases = {
        "1": "scheme1",
        "方案一": "scheme1",
        "default": "scheme1",
        "legacy": "scheme1",
        "old": "scheme1",
        "2": "scheme2",
        "方案二": "scheme2",
        "workbench": "scheme2",
        "thumbnail_workbench": "scheme2",
        "thumbnail-workbench": "scheme2",
        "3": "scheme3",
        "方案三": "scheme3",
        "visual_frame": "scheme3",
        "visual-frame": "scheme3",
        "video_bg": "scheme3",
        "video-bg": "scheme3",
    }
    normalized = aliases.get(text, text)
    return normalized if normalized in VERTICAL_VIDEO_SCHEMES else VERTICAL_VIDEO_SCHEME_DEFAULT


def _normalize_vertical_video_schemes(value, fallback=None):
    items = value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            items = []
        else:
            try:
                parsed = json.loads(text)
                items = parsed if isinstance(parsed, list) else [text]
            except Exception:
                items = re.split(r"[,;，；\s]+", text)
    elif not isinstance(value, (list, tuple, set)):
        items = [] if value is None else [value]

    schemes = []
    for item in items:
        normalized = _normalize_vertical_video_scheme(item)
        if normalized not in schemes:
            schemes.append(normalized)
    if not schemes and fallback is not None:
        schemes = [_normalize_vertical_video_scheme(fallback)]
    return schemes


def _primary_vertical_video_scheme(schemes):
    normalized = _normalize_vertical_video_schemes(schemes, VERTICAL_VIDEO_SCHEME_DEFAULT)
    if "scheme3" in normalized:
        return "scheme3"
    if "scheme1" in normalized and "scheme2" in normalized:
        return "scheme2"
    return normalized[0]


def _vertical_video_scheme_label(scheme):
    normalized = _normalize_vertical_video_scheme(scheme)
    return {"scheme1": "方案一", "scheme2": "方案二", "scheme3": "方案三"}[normalized]


def _task_vertical_video_scheme(task):
    params = task.params if isinstance(task.params, dict) else {}
    payload = params.get("report_audio_workflow") if isinstance(params.get("report_audio_workflow"), dict) else {}
    video_options = payload.get("video") if isinstance(payload.get("video"), dict) else {}
    return _normalize_vertical_video_scheme(video_options.get("vertical_video_scheme") or video_options.get("vertical_scheme"))


def _workbench_institution(structured, pdf, video_options):
    explicit = str(
        (video_options or {}).get("institution")
        or (video_options or {}).get("vertical_institution")
        or ""
    ).strip()
    allowed = {
        "J.P. Morgan", "Goldman Sachs", "Morgan Stanley", "UBS", "Nomura",
        "BofA", "HSBC", "Jefferies", "Bernstein", "GF Securities",
        "GF Securities (Hong Kong)", "深度研报",
    }
    if explicit in allowed:
        return explicit

    fields = [
        explicit,
        (pdf or {}).get("filename"),
        (pdf or {}).get("topic_title"),
        (structured or {}).get("cover_title"),
        (structured or {}).get("title"),
        (structured or {}).get("summary"),
    ]
    haystack = " ".join(str(item or "") for item in fields)
    institution_aliases = [
        ("Morgan Stanley", "Morgan Stanley"),
        ("摩根士丹利", "Morgan Stanley"),
        ("大摩", "Morgan Stanley"),
        ("J.P. Morgan", "J.P. Morgan"),
        ("JP Morgan", "J.P. Morgan"),
        ("JPMorgan", "J.P. Morgan"),
        ("摩根大通", "J.P. Morgan"),
        ("小摩", "J.P. Morgan"),
        ("Goldman Sachs", "Goldman Sachs"),
        ("高盛", "Goldman Sachs"),
        ("Bernstein", "Bernstein"),
        ("伯恩斯坦", "Bernstein"),
        ("盛博", "Bernstein"),
        ("UBS", "UBS"),
        ("瑞银", "UBS"),
        ("Nomura", "Nomura"),
        ("野村", "Nomura"),
        ("BofA", "BofA"),
        ("Bank of America", "BofA"),
        ("美国银行", "BofA"),
        ("美银", "BofA"),
        ("HSBC", "HSBC"),
        ("汇丰", "HSBC"),
        ("Jefferies", "Jefferies"),
        ("杰富瑞", "Jefferies"),
    ]
    for alias, option in institution_aliases:
        if re.search(re.escape(alias), haystack, flags=re.I):
            return option
    for alias, label in BANK_ALIASES:
        if re.search(re.escape(alias), haystack, flags=re.I):
            mapped = {
                "香港广发": "GF Securities (Hong Kong)",
                "广发证券": "GF Securities",
                "大摩": "Morgan Stanley",
                "摩根大通": "J.P. Morgan",
                "高盛": "Goldman Sachs",
                "瑞银": "UBS",
                "野村": "Nomura",
                "美银": "BofA",
                "杰富瑞": "Jefferies",
                "伯恩斯坦": "Bernstein",
            }.get(label)
            if mapped:
                return mapped
    return "深度研报"


def _regenerate_cover_for_task(task, *, title, subtitle=""):
    task_id = task.id
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
    filename = f"report_audio_cover_{task_id[:8]}_{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}.jpg"
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
        "download_url": f"/api/report-audio-workflows/jobs/{task_id}/download-cover",
        "updated_at": now,
    }


def _generate_vertical_cover_for_task(task, *, title, subtitle=""):
    task_id = task.id
    result = task.result if isinstance(task.result, dict) else {}
    params = task.params if isinstance(task.params, dict) else {}
    payload = params.get("report_audio_workflow") if isinstance(params.get("report_audio_workflow"), dict) else {}
    video_options = payload.get("video") if isinstance(payload.get("video"), dict) else {}
    structured = result.get("structured_result") if isinstance(result.get("structured_result"), dict) else {}
    pdf = payload.get("pdf") if isinstance(payload.get("pdf"), dict) else {}
    if not pdf:
        pdf = result.get("pdf") if isinstance(result.get("pdf"), dict) else {}
    pdf_screenshot = _ensure_pdf_first_page_image(task)
    screenshot_path = pdf_screenshot.get("path") if isinstance(pdf_screenshot, dict) else None
    if not screenshot_path or not os.path.exists(screenshot_path):
        raise ValueError("PDF 首屏图不存在，无法生成竖版封皮")

    title = _cover_title_with_institution(task, _normalize_cover_text(title, 160))
    subtitle = _normalize_cover_text(subtitle, 220)

    transform = {
        "anchor": "top-left",
        "title_max_lines": 2,
        "subtitle_max_lines": 1,
        **(video_options.get("vertical_image_transform") if isinstance(video_options.get("vertical_image_transform"), dict) else {}),
    }
    filename = f"vertical_cover_{task_id[:8]}_{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}.jpg"
    cover_path = thumbnail_service.generate_workbench_vertical_cover(
        main_title=title,
        sub_title=subtitle,
        screenshot_path=screenshot_path,
        output_filename=filename,
        institution=_workbench_institution(structured, pdf, video_options),
        emotion="bull",
        footer_text=str(video_options.get("vertical_footer_text") or "听见资本").strip() or "听见资本",
        image_transform=transform,
    )
    if not cover_path or not os.path.exists(cover_path):
        raise RuntimeError("竖版封皮文件未生成")

    updated_result = dict(task.result or result)
    video = dict(updated_result.get("video") or {}) if isinstance(updated_result.get("video"), dict) else {}
    history = list(updated_result.get("vertical_cover_regenerations") or [])
    if video.get("vertical_cover_path"):
        history.append({
            "vertical_cover_path": video.get("vertical_cover_path"),
            "title": video.get("vertical_cover_title"),
            "subtitle": video.get("vertical_cover_subtitle"),
            "replaced_at": datetime.utcnow().isoformat() + "Z",
        })
    now = datetime.utcnow().isoformat() + "Z"
    video.update({
        "vertical_cover_path": cover_path,
        "vertical_cover_title": title,
        "vertical_cover_subtitle": subtitle,
        "vertical_cover_regenerated_at": now,
        "pdf_first_page_image": pdf_screenshot,
    })
    updated_result["video"] = video
    updated_result["vertical_cover_regenerations"] = history[-20:]
    task.result = updated_result
    flag_modified(task, "result")
    db.session.commit()
    return {
        "path": cover_path,
        "bytes": os.path.getsize(cover_path),
        "title": title,
        "subtitle": subtitle,
        "download_url": f"/api/report-audio-workflows/jobs/{task_id}/download-vertical-cover",
        "updated_at": now,
    }


def _generate_vertical_background_for_task(task, *, scheme=None, title, subtitle=""):
    task_id = task.id
    result = task.result if isinstance(task.result, dict) else {}
    params = task.params if isinstance(task.params, dict) else {}
    payload = params.get("report_audio_workflow") if isinstance(params.get("report_audio_workflow"), dict) else {}
    video_options = payload.get("video") if isinstance(payload.get("video"), dict) else {}
    video = result.get("video") if isinstance(result.get("video"), dict) else {}
    structured = result.get("structured_result") if isinstance(result.get("structured_result"), dict) else {}
    pdf = payload.get("pdf") if isinstance(payload.get("pdf"), dict) else {}
    if not pdf:
        pdf = result.get("pdf") if isinstance(result.get("pdf"), dict) else {}
    pdf_screenshot = _ensure_pdf_first_page_image(task)
    screenshot_path = pdf_screenshot.get("path") if isinstance(pdf_screenshot, dict) else None
    if not screenshot_path or not os.path.exists(screenshot_path):
        raise ValueError("PDF 首屏图不存在，无法生成竖屏背景")

    scheme = _normalize_vertical_video_scheme(
        scheme
        or video.get("vertical_video_scheme")
        or video_options.get("vertical_video_scheme")
        or video_options.get("vertical_scheme")
    )
    title = _normalize_cover_text(title, 160)
    subtitle = _normalize_cover_text(subtitle, 220)
    if not title:
        raise ValueError("请填写竖屏背景标题")
    transform = {
        "anchor": "top-left",
        "title_max_lines": 2,
        "subtitle_max_lines": 1,
        **(video_options.get("vertical_image_transform") if isinstance(video_options.get("vertical_image_transform"), dict) else {}),
    }
    timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S%f")
    filename = f"vertical_background_{scheme}_{task_id[:8]}_{timestamp}.jpg"
    if scheme == "scheme3":
        background_path = thumbnail_service.generate_workbench_video_background(
            main_title=title,
            sub_title=subtitle,
            output_filename=filename,
            institution=_workbench_institution(structured, pdf, video_options),
            emotion="bull",
            footer_text=str(video_options.get("vertical_footer_text") or "听见资本").strip() or "听见资本",
        )
    elif scheme == "scheme2":
        background_path = thumbnail_service.generate_workbench_vertical_thumbnail(
            main_title=title,
            sub_title=subtitle,
            screenshot_path=screenshot_path,
            output_filename=filename,
            institution=_workbench_institution(structured, pdf, video_options),
            emotion="bull",
            footer_text=str(video_options.get("vertical_footer_text") or "听见资本").strip() or "听见资本",
            image_transform=transform,
        )
    else:
        date_text = str(video_options.get("date_text") or "").strip() or _publisher_date_text(task.created_at)
        background_path = thumbnail_service.generate_vertical_thumbnail(
            main_title=title,
            sub_title=subtitle,
            date_text=date_text,
            screenshot_path=screenshot_path,
            output_filename=filename,
        )
    if not background_path or not os.path.exists(background_path):
        raise RuntimeError("竖屏背景文件未生成")

    updated_result = dict(task.result or result)
    updated_video = dict(updated_result.get("video") or {}) if isinstance(updated_result.get("video"), dict) else {}
    primary_scheme = _normalize_vertical_video_scheme(
        updated_video.get("vertical_video_scheme")
        or video_options.get("vertical_video_scheme")
        or video_options.get("vertical_scheme")
    )
    keyframe_key = "vertical_background" if scheme == primary_scheme else f"vertical_background_{scheme}"
    background_paths = dict(updated_video.get("vertical_background_paths") or {})
    previous_path = background_paths.get(scheme)
    background_paths[scheme] = background_path
    keyframes = dict(updated_video.get("keyframes") or {})
    now = datetime.utcnow().isoformat() + "Z"
    label = f"{_vertical_video_scheme_label(scheme)}竖屏背景关键帧"
    keyframes[keyframe_key] = {
        "path": background_path,
        "label": label,
        "ratio": "9:16",
        "width": 1080,
        "height": 1920,
        "bytes": os.path.getsize(background_path),
        "title": title,
        "subtitle": subtitle,
        "scheme": scheme,
        "updated_at": now,
    }
    title_map = dict(updated_video.get("vertical_background_titles") or {})
    subtitle_map = dict(updated_video.get("vertical_background_subtitles") or {})
    title_map[scheme] = title
    subtitle_map[scheme] = subtitle
    updated_video.update({
        "vertical_background_paths": background_paths,
        "vertical_background_titles": title_map,
        "vertical_background_subtitles": subtitle_map,
        "vertical_background_title": title,
        "vertical_background_subtitle": subtitle,
        "vertical_background_regenerated_at": now,
        "keyframes": keyframes,
        "pdf_first_page_image": pdf_screenshot,
    })
    if scheme == primary_scheme or not _path_exists(updated_video.get("vertical_background_path")):
        updated_video["vertical_background_path"] = background_path

    history = list(updated_result.get("vertical_background_regenerations") or [])
    if previous_path:
        history.append({
            "scheme": scheme,
            "vertical_background_path": previous_path,
            "replaced_at": now,
        })
    updated_result["video"] = updated_video
    updated_result["vertical_background_regenerations"] = history[-20:]
    task.result = updated_result
    flag_modified(task, "result")
    db.session.commit()
    return {
        "path": background_path,
        "bytes": os.path.getsize(background_path),
        "title": title,
        "subtitle": subtitle,
        "download_url": f"/api/report-audio-workflows/jobs/{task_id}/download-keyframe/{keyframe_key}",
        "updated_at": now,
        "keyframe_key": keyframe_key,
        "scheme": scheme,
        "label": label,
        "ratio": "9:16",
        "width": 1080,
        "height": 1920,
    }


def _generate_podcast_cover_for_task(task, *, scheme=None, title=None, subtitle=None):
    task_id = task.id
    result = task.result if isinstance(task.result, dict) else {}
    params = task.params if isinstance(task.params, dict) else {}
    payload = params.get("report_audio_workflow") if isinstance(params.get("report_audio_workflow"), dict) else {}
    video_options = payload.get("video") if isinstance(payload.get("video"), dict) else {}
    scheme = _normalize_vertical_video_scheme(scheme or video_options.get("vertical_video_scheme") or video_options.get("vertical_scheme"))
    structured = result.get("structured_result") if isinstance(result.get("structured_result"), dict) else {}
    pdf = payload.get("pdf") if isinstance(payload.get("pdf"), dict) else {}
    if not pdf:
        pdf = result.get("pdf") if isinstance(result.get("pdf"), dict) else {}
    pdf_screenshot = _ensure_pdf_first_page_image(task)
    screenshot_path = pdf_screenshot.get("path") if isinstance(pdf_screenshot, dict) else None
    if not screenshot_path or not os.path.exists(screenshot_path):
        raise ValueError("PDF 首屏图不存在，无法生成播客封皮")

    main_title = _normalize_cover_text(title, 160) or _publisher_main_title(structured, video_options, variant="vertical")
    main_title = _cover_title_with_institution(task, main_title)
    sub_title = _normalize_cover_text(subtitle, 220) if subtitle is not None else _publisher_sub_title(structured, video_options, variant="vertical")
    institution = _workbench_institution(structured, pdf, video_options)
    transform = {
        "anchor": "top-left",
        "title_max_lines": 2,
        "subtitle_max_lines": 1,
        **(video_options.get("vertical_image_transform") if isinstance(video_options.get("vertical_image_transform"), dict) else {}),
    }
    filename = f"podcast_cover_{task_id[:8]}_{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}.jpg"
    cover_path = thumbnail_service.generate_workbench_podcast_cover(
        main_title=main_title,
        sub_title=sub_title,
        screenshot_path=screenshot_path,
        output_filename=filename,
        institution=institution,
        emotion="bull",
        footer_text=str(video_options.get("vertical_footer_text") or "听见资本").strip() or "听见资本",
        image_transform=transform,
    )
    if not cover_path or not os.path.exists(cover_path):
        raise RuntimeError("播客封皮文件未生成")

    updated_result = dict(task.result or result)
    video = dict(updated_result.get("video") or {}) if isinstance(updated_result.get("video"), dict) else {}
    previous_cover = {
        "podcast_cover_path": video.get("podcast_cover_path"),
        "replaced_at": datetime.utcnow().isoformat() + "Z",
    }
    history = list(updated_result.get("podcast_cover_regenerations") or [])
    if previous_cover.get("podcast_cover_path"):
        history.append(previous_cover)

    now = datetime.utcnow().isoformat() + "Z"
    video.update({
        "enabled": True if video.get("enabled") is None else video.get("enabled"),
        "vertical_enabled": True if video.get("vertical_enabled") is None else video.get("vertical_enabled"),
        "vertical_video_scheme": scheme,
        "podcast_cover_path": cover_path,
        "podcast_cover_title": main_title,
        "podcast_cover_subtitle": sub_title,
        "podcast_cover_regenerated_at": now,
        "pdf_first_page_image": pdf_screenshot,
    })
    updated_result["video"] = video
    updated_result["podcast_cover_regenerations"] = history[-20:]
    task.result = updated_result
    flag_modified(task, "result")
    db.session.commit()
    return {
        "path": cover_path,
        "bytes": os.path.getsize(cover_path),
        "title": main_title,
        "subtitle": sub_title,
        "download_url": f"/api/report-audio-workflows/jobs/{task_id}/download-podcast-cover",
        "updated_at": now,
        "scheme": scheme,
    }


def _generate_bilibili_cover_for_task(task, *, title=None, subtitle=None):
    task_id = task.id
    result = task.result if isinstance(task.result, dict) else {}
    params = task.params if isinstance(task.params, dict) else {}
    payload = params.get("report_audio_workflow") if isinstance(params.get("report_audio_workflow"), dict) else {}
    video_options = payload.get("video") if isinstance(payload.get("video"), dict) else {}
    structured = result.get("structured_result") if isinstance(result.get("structured_result"), dict) else {}
    pdf = payload.get("pdf") if isinstance(payload.get("pdf"), dict) else {}
    if not pdf:
        pdf = result.get("pdf") if isinstance(result.get("pdf"), dict) else {}
    pdf_screenshot = _ensure_pdf_first_page_image(task)
    screenshot_path = pdf_screenshot.get("path") if isinstance(pdf_screenshot, dict) else None
    if not screenshot_path or not os.path.exists(screenshot_path):
        raise ValueError("PDF 首屏图不存在，无法生成 B站封皮")

    transform = {
        "anchor": "top-left",
        "title_max_lines": 2,
        "subtitle_max_lines": 1,
        **(video_options.get("bilibili_cover_image_transform") if isinstance(video_options.get("bilibili_cover_image_transform"), dict) else {}),
    }
    filename = f"bilibili_cover_{task_id[:8]}_{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}.jpg"
    main_title = _normalize_cover_text(title, 160) or _publisher_main_title(structured, video_options, variant="vertical")
    main_title = _cover_title_with_institution(task, main_title)
    sub_title = _normalize_cover_text(subtitle, 220) if subtitle is not None else _publisher_sub_title(structured, video_options, variant="vertical")
    cover_path = thumbnail_service.generate_workbench_bilibili_cover(
        main_title=main_title,
        sub_title=sub_title,
        screenshot_path=screenshot_path,
        output_filename=filename,
        institution=_workbench_institution(structured, pdf, video_options),
        emotion="bull",
        footer_text=str(video_options.get("vertical_footer_text") or "听见资本").strip() or "听见资本",
        image_transform=transform,
        show_footer=False,
    )
    if not cover_path or not os.path.exists(cover_path):
        raise RuntimeError("B站封皮文件未生成")

    updated_result = dict(task.result or result)
    video = dict(updated_result.get("video") or {}) if isinstance(updated_result.get("video"), dict) else {}
    history = list(updated_result.get("bilibili_cover_regenerations") or [])
    if video.get("bilibili_cover_path"):
        history.append({
            "bilibili_cover_path": video.get("bilibili_cover_path"),
            "replaced_at": datetime.utcnow().isoformat() + "Z",
        })
    now = datetime.utcnow().isoformat() + "Z"
    video.update({
        "bilibili_cover_path": cover_path,
        "bilibili_cover_title": main_title,
        "bilibili_cover_subtitle": sub_title,
        "bilibili_cover_regenerated_at": now,
        "pdf_first_page_image": pdf_screenshot,
    })
    updated_result["video"] = video
    updated_result["bilibili_cover_regenerations"] = history[-20:]
    task.result = updated_result
    flag_modified(task, "result")
    db.session.commit()
    return {
        "path": cover_path,
        "bytes": os.path.getsize(cover_path),
        "title": main_title,
        "subtitle": sub_title,
        "download_url": f"/api/report-audio-workflows/jobs/{task_id}/download-bilibili-cover",
        "updated_at": now,
        "ratio": "1146:717",
    }


def _create_vertical_video_backfill_task(
    parent,
    *,
    scheme=None,
    schemes=None,
    preview_seconds=0,
    include_landscape=False,
    visual_workflow_id=None,
    visual_video_path=None,
    visual_scene_timeline=None,
    finalization_task_id=None,
):
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
    existing_video = result.get("video") if isinstance(result.get("video"), dict) else {}
    if schemes is None:
        vertical_schemes = _normalize_vertical_video_schemes(
            scheme or video_options.get("vertical_video_scheme") or video_options.get("vertical_scheme"),
            VERTICAL_VIDEO_SCHEME_DEFAULT,
        )
    else:
        vertical_schemes = _normalize_vertical_video_schemes(schemes)
    vertical_enabled = bool(vertical_schemes)
    scheme = _primary_vertical_video_scheme(vertical_schemes) if vertical_enabled else VERTICAL_VIDEO_SCHEME_DEFAULT
    secondary_scheme = next((item for item in vertical_schemes if item != scheme), None)
    existing_vertical_scheme = _normalize_vertical_video_scheme(existing_video.get("vertical_video_scheme"))
    existing_vertical_background = (
        existing_video.get("vertical_background_path")
        if existing_vertical_scheme == scheme and _path_exists(existing_video.get("vertical_background_path"))
        else None
    )
    preview_seconds = _bounded_int(preview_seconds, 0, 0, 600)
    try:
        audio_seconds = float(audio.get("seconds") or 0)
    except (TypeError, ValueError):
        audio_seconds = 0
    segment_end_offset = max(0.0, audio_seconds - preview_seconds) if preview_seconds > 0 and audio_seconds > preview_seconds else 0
    main_title = _publisher_main_title(structured, video_options, variant="vertical")
    sub_title = _publisher_sub_title(structured, video_options, variant="vertical")
    date_text = (
        str(video_options.get("date_text") or "").strip()
        or _publisher_date_text(parent.created_at)
    )
    institution = _workbench_institution(structured, pdf, video_options)
    main_title = _cover_title_with_institution(parent, main_title)
    vertical_subtitle_position = (
        video_options.get("vertical_subtitle_position")
        if isinstance(video_options.get("vertical_subtitle_position"), dict)
        else ({"y_pct": 0.73} if scheme in {"scheme2", "scheme3"} else None)
    )
    vertical_subtitle_style = (
        video_options.get("vertical_subtitle_style")
        if isinstance(video_options.get("vertical_subtitle_style"), dict)
        else (
            {
                "font_name": "Hiragino Sans GB",
                "primary_color": "#FFF2D7",
                "outline_color": "#07111F",
                "back_color": "#000000",
                "outline": 3,
                "shadow": 1,
                "margin_l": 72,
                "margin_r": 72,
                "font_size": 50,
                "wrap_style": 0,
            }
            if scheme in {"scheme2", "scheme3"}
            else None
        )
    )

    metadata = {
        "source": VERTICAL_BACKFILL_SOURCE,
        "parent_task_id": parent.id,
        "pdf": pdf,
        "vertical_video_scheme": scheme,
        "vertical_video_scheme_label": _vertical_video_scheme_label(scheme),
        "vertical_video_schemes": vertical_schemes,
        "preview_seconds": preview_seconds,
        "is_preview": preview_seconds > 0,
        "include_landscape": bool(include_landscape),
        **({"visual_workflow_id": str(visual_workflow_id)} if visual_workflow_id else {}),
        **({"finalization_task_id": str(finalization_task_id)} if finalization_task_id else {}),
    }
    task_params = {
        "feature": "publisher",
        "source_feature": FEATURE_NAME,
        "source_task_id": parent.id,
        "subtitle_alignment": {
            "required": True,
            "strict": True,
            "mode": "asr_forced_alignment",
            "source": "report_audio_final_wav",
        },
        "audio_segments": [
            {
                "path": audio_path,
                "subtitle": _publisher_script_subtitles(structured, audio),
                "start": 0,
                "end_offset": segment_end_offset,
                "speed": 1.0,
            }
        ],
        "video": {
            "enabled": bool(include_landscape),
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
            "enabled": vertical_enabled,
            "asr_model": str(video_options.get("asr_model") or "base"),
            "audio_path": audio_path,
            "image_path": existing_vertical_background or screenshot_path,
            "subtitles": [],
            "scheme": scheme,
            **({"visual_scene_timeline": visual_scene_timeline} if scheme == "scheme3" and visual_scene_timeline else {}),
            **({"visual_video_path": visual_video_path} if scheme == "scheme3" and visual_video_path else {}),
            "preview_seconds": preview_seconds,
            **({"subtitle_position": vertical_subtitle_position} if vertical_subtitle_position else {}),
            **({"subtitle_style": vertical_subtitle_style} if vertical_subtitle_style else {}),
        },
        "vertical_video_2": {
            "enabled": bool(secondary_scheme),
            "scheme": secondary_scheme,
            **({"visual_scene_timeline": visual_scene_timeline} if secondary_scheme == "scheme3" and visual_scene_timeline else {}),
            **({"visual_video_path": visual_video_path} if secondary_scheme == "scheme3" and visual_video_path else {}),
            "asr_model": str(video_options.get("asr_model") or "base"),
            "image_path": screenshot_path,
            "screenshot_path": screenshot_path,
            "main_title": main_title,
            "sub_title": sub_title,
            "date_text": date_text,
            "institution": institution,
            "emotion": "bull",
            "footer_text": str(video_options.get("vertical_footer_text") or "听见资本").strip() or "听见资本",
            "image_transform": {
                "anchor": "top-left",
                "title_max_lines": 2,
                "subtitle_max_lines": 1,
                **(video_options.get("vertical_image_transform") if isinstance(video_options.get("vertical_image_transform"), dict) else {}),
            },
            **({"preview_seconds": preview_seconds} if preview_seconds > 0 else {}),
        },
        "vertical_thumbnail": {
            "enabled": vertical_enabled and not bool(existing_vertical_background),
            "scheme": scheme,
            "main_title": main_title,
            "sub_title": sub_title,
            "date_text": date_text,
            "screenshot_path": screenshot_path,
            "institution": institution,
            "emotion": "bull",
            "footer_text": str(video_options.get("vertical_footer_text") or "听见资本").strip() or "听见资本",
            "image_transform": {
                "anchor": "top-left",
                "title_max_lines": 2,
                "subtitle_max_lines": 1,
                **(video_options.get("vertical_image_transform") if isinstance(video_options.get("vertical_image_transform"), dict) else {}),
            },
        },
        "vertical_cover": {
            "enabled": vertical_enabled and scheme == "scheme2" and not _path_exists(existing_video.get("vertical_cover_path")),
            "scheme": scheme,
            "format": "wx_cover",
            "main_title": main_title,
            "sub_title": sub_title,
            "screenshot_path": screenshot_path,
            "institution": institution,
            "emotion": "bull",
            "footer_text": str(video_options.get("vertical_footer_text") or "听见资本").strip() or "听见资本",
            "image_transform": {
                "anchor": "top-left",
                "title_max_lines": 2,
                "subtitle_max_lines": 1,
                **(video_options.get("vertical_image_transform") if isinstance(video_options.get("vertical_image_transform"), dict) else {}),
            },
        },
        "podcast_cover": {
            "enabled": vertical_enabled and scheme == "scheme2" and not _path_exists(existing_video.get("podcast_cover_path")),
            "scheme": scheme,
            "format": "podcast",
            "main_title": main_title,
            "sub_title": sub_title,
            "screenshot_path": screenshot_path,
            "institution": institution,
            "emotion": "bull",
            "footer_text": str(video_options.get("vertical_footer_text") or "听见资本").strip() or "听见资本",
            "image_transform": {
                "anchor": "top-left",
                "title_max_lines": 2,
                "subtitle_max_lines": 1,
                **(video_options.get("vertical_image_transform") if isinstance(video_options.get("vertical_image_transform"), dict) else {}),
            },
        },
        "bilibili_cover": {
            "enabled": vertical_enabled and scheme == "scheme2" and not _path_exists(existing_video.get("bilibili_cover_path")),
            "scheme": scheme,
            "format": "bilibili_wx_cover",
            "main_title": main_title,
            "sub_title": sub_title,
            "screenshot_path": screenshot_path,
            "institution": institution,
            "emotion": "bull",
            "footer_text": str(video_options.get("vertical_footer_text") or "听见资本").strip() or "听见资本",
            "show_footer": False,
            "image_transform": {
                "anchor": "top-left",
                "title_max_lines": 2,
                "subtitle_max_lines": 1,
                **(video_options.get("bilibili_cover_image_transform") if isinstance(video_options.get("bilibili_cover_image_transform"), dict) else {}),
            },
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
            "vertical_video_scheme": scheme,
            "vertical_preview_seconds": preview_seconds,
            "vertical_cover_path": None,
            "podcast_cover_path": None,
            "bilibili_cover_path": None,
            "cover_path": None,
        },
    )
    db.session.add(child)
    db.session.flush()
    child_id = child.id
    db.session.commit()

    try:
        async_result = apply_async_after_db_release(
            run_publication_pipeline,
            args=[child_id],
            queue="render",
            retry=False,
        )
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
    db.session.flush()
    enqueue_response_summary = _child_task_summary(child)
    enqueue_response_summary.update({
        "vertical_video_scheme": scheme,
        "vertical_video_scheme_label": _vertical_video_scheme_label(scheme),
        "vertical_video_schemes": vertical_schemes,
        "preview_seconds": preview_seconds,
        "is_preview": preview_seconds > 0,
    })
    db.session.commit()
    # Keep a non-ORM snapshot available after commit; reading mapped attributes
    # here would otherwise issue a response-only SELECT on the expired instance.
    child._enqueue_response_summary = enqueue_response_summary
    return child


def _latest_landscape_video_child(parent_id):
    candidates = (
        PublicationTask.query.with_entities(
            PublicationTask.id.label("id"),
            PublicationTask.status.label("status"),
            PublicationTask.task_video_path.label("task_video_path"),
            PublicationTask.created_at.label("created_at"),
        )
        .filter(PublicationTask.feature == "publisher")
        .filter(PublicationTask.source_task_id == parent_id)
        .filter(PublicationTask.publisher_landscape_enabled.is_(True))
        .order_by(PublicationTask.created_at.desc())
        .limit(30)
        .all()
    )
    for child in candidates:
        path = _landscape_video_path_from_child(child)
        if child.status == "completed" and _path_exists(path):
            return child
    return candidates[0] if candidates else None


def _landscape_video_path_from_child(child):
    if not child:
        return None
    projected = getattr(child, "task_video_path", None)
    if projected:
        return projected
    result = _task_mapping_attr(child, "result")
    return result.get("video_path")


def _landscape_video_path_for_task(task):
    parent_path = getattr(task, "report_audio_video_path", None)
    if not parent_path:
        result = _task_mapping_attr(task, "result")
        video = result.get("video") if isinstance(result.get("video"), dict) else {}
        parent_path = video.get("video_path")
    if _path_exists(parent_path):
        return parent_path
    child = _latest_landscape_video_child(task.id)
    if not child or child.status != "completed":
        return None
    return _landscape_video_path_from_child(child)


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


def _latest_vertical_video_child(parent_id, *, preview=None):
    query = (
        _child_task_summary_query()
        .filter(PublicationTask.feature == "publisher")
        .filter(PublicationTask.source_task_id == parent_id)
        .filter(PublicationTask.publisher_vertical_enabled.is_(True))
        .order_by(PublicationTask.created_at.desc())
    )
    if preview is not None:
        if preview:
            query = query.filter(PublicationTask.publisher_preview_seconds > 0)
        else:
            query = query.filter(or_(
                PublicationTask.publisher_preview_seconds == 0,
                PublicationTask.publisher_preview_seconds.is_(None),
            ))
    candidates = query.limit(30).all()
    return max(candidates, key=_vertical_child_priority) if candidates else None


def _latest_matching_vertical_video_child(parent_id, *, scheme, preview_seconds):
    scheme = _normalize_vertical_video_scheme(scheme)
    preview_seconds = _bounded_int(preview_seconds, 0, 0, 600)
    candidates = (
        _child_task_summary_query()
        .filter(PublicationTask.feature == "publisher")
        .filter(PublicationTask.source_task_id == parent_id)
        .filter(PublicationTask.publisher_vertical_enabled.is_(True))
        .filter(PublicationTask.publisher_vertical_scheme == scheme)
        .filter(PublicationTask.publisher_preview_seconds == preview_seconds)
        .order_by(PublicationTask.created_at.desc())
        .limit(30)
        .all()
    )
    return max(candidates, key=_vertical_child_priority) if candidates else None


def _latest_matching_final_video_child(parent_id, *, scheme):
    scheme = _normalize_vertical_video_scheme(scheme)
    return (
        PublicationTask.query
        .filter(PublicationTask.feature == "publisher")
        .filter(PublicationTask.source_task_id == parent_id)
        .filter(PublicationTask.publisher_landscape_enabled.is_(True))
        .filter(PublicationTask.publisher_vertical_enabled.is_(True))
        .filter(PublicationTask.publisher_vertical_scheme == scheme)
        .filter(PublicationTask.publisher_preview_seconds == 0)
        .order_by(PublicationTask.created_at.desc())
        .first()
    )


def _is_vertical_backfill_child(task):
    projected_enabled = getattr(task, "publisher_vertical_enabled", None)
    if projected_enabled is not None:
        return bool(projected_enabled)
    params = _task_mapping_attr(task, "params")
    result = _task_mapping_attr(task, "result")
    metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
    if not metadata:
        metadata = params.get("metadata") if isinstance(params.get("metadata"), dict) else {}
    if metadata.get("source") == VERTICAL_BACKFILL_SOURCE:
        return True
    video = params.get("video") if isinstance(params.get("video"), dict) else {}
    vertical = params.get("vertical_video") if isinstance(params.get("vertical_video"), dict) else {}
    return _truthy(vertical.get("enabled"), False) and not _truthy(video.get("enabled"), False)


def _vertical_video_path_from_child(child):
    projected = getattr(child, "publisher_vertical_video_path", None)
    if projected:
        return projected
    result = _task_mapping_attr(child, "result")
    vertical_path = result.get("vertical_video_path")
    if vertical_path:
        return vertical_path
    if _is_vertical_backfill_child(child):
        return result.get("video_path")
    return None


def _vertical_cover_path_from_child(child):
    if not child:
        return None
    projected = getattr(child, "publisher_vertical_cover_path", None)
    if projected:
        return projected
    result = _task_mapping_attr(child, "result")
    return result.get("vertical_cover_path")


def _podcast_cover_path_from_child(child):
    if not child:
        return None
    projected = getattr(child, "publisher_podcast_cover_path", None)
    if projected:
        return projected
    result = _task_mapping_attr(child, "result")
    return result.get("podcast_cover_path")


def _bilibili_cover_path_from_child(child):
    if not child:
        return None
    projected = getattr(child, "publisher_bilibili_cover_path", None)
    if projected:
        return projected
    result = _task_mapping_attr(child, "result")
    return result.get("bilibili_cover_path")


def _vertical_child_metadata(task):
    params = _task_mapping_attr(task, "params")
    result = _task_mapping_attr(task, "result")
    metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
    if not metadata:
        metadata = params.get("metadata") if isinstance(params.get("metadata"), dict) else {}
    return metadata if isinstance(metadata, dict) else {}


def _vertical_child_scheme(task):
    projected = getattr(task, "publisher_vertical_scheme", None)
    if projected:
        return _normalize_vertical_video_scheme(projected)
    params = _task_mapping_attr(task, "params")
    result = _task_mapping_attr(task, "result")
    vertical = params.get("vertical_video") if isinstance(params.get("vertical_video"), dict) else {}
    thumbnail = params.get("vertical_thumbnail") if isinstance(params.get("vertical_thumbnail"), dict) else {}
    metadata = _vertical_child_metadata(task)
    return _normalize_vertical_video_scheme(
        metadata.get("vertical_video_scheme")
        or result.get("vertical_video_scheme")
        or vertical.get("scheme")
        or thumbnail.get("scheme")
    )


def _vertical_child_preview_seconds(task):
    projected = getattr(task, "publisher_preview_seconds", None)
    if projected is not None:
        return _bounded_int(projected, 0, 0, 600)
    params = _task_mapping_attr(task, "params")
    result = _task_mapping_attr(task, "result")
    vertical = params.get("vertical_video") if isinstance(params.get("vertical_video"), dict) else {}
    metadata = _vertical_child_metadata(task)
    return _bounded_int(
        metadata.get("preview_seconds")
        or result.get("vertical_preview_seconds")
        or vertical.get("preview_seconds")
        or 0,
        0,
        0,
        600,
    )


def _is_vertical_preview_child(task):
    return _vertical_child_preview_seconds(task) > 0


def _is_vertical_video_candidate_child(task):
    projected = getattr(task, "publisher_vertical_enabled", None)
    if projected is not None:
        return bool(projected)
    if _is_vertical_backfill_child(task):
        return True
    if _path_exists(_vertical_video_path_from_child(task)):
        return True
    params = _task_mapping_attr(task, "params")
    vertical = params.get("vertical_video") if isinstance(params.get("vertical_video"), dict) else {}
    return _truthy(vertical.get("enabled"), False)


def _task_mapping_attr(task, name):
    try:
        if name in sqlalchemy_inspect(task).unloaded:
            return {}
    except Exception:
        pass
    try:
        value = getattr(task, name)
    except (AttributeError, KeyError):
        return {}
    return value if isinstance(value, dict) else {}


def _vertical_video_path_for_task(task, scheme=None):
    normalized_scheme = _normalize_vertical_video_scheme(scheme) if scheme else None
    result = _task_mapping_attr(task, "result")
    video = result.get("video") if isinstance(result.get("video"), dict) else {}
    if normalized_scheme:
        paths = video.get("vertical_video_paths") if isinstance(video.get("vertical_video_paths"), dict) else {}
        if _path_exists(paths.get(normalized_scheme)):
            return paths.get(normalized_scheme)
        matching_path = _vertical_video_path_for_scheme(task.id, normalized_scheme, preview=False)
        if matching_path:
            return matching_path
    child = _latest_vertical_video_child(task.id, preview=False)
    if child and child.status == "completed":
        if normalized_scheme:
            child_result = _task_mapping_attr(child, "result")
            child_paths = child_result.get("vertical_video_paths") if isinstance(child_result.get("vertical_video_paths"), dict) else {}
            if _path_exists(child_paths.get(normalized_scheme)):
                return child_paths.get(normalized_scheme)
        path = _vertical_video_path_from_child(child)
        if _path_exists(path) and (not normalized_scheme or _vertical_child_scheme(child) == normalized_scheme):
            return path
    if not normalized_scheme and _path_exists(video.get("vertical_video_path")):
        return video.get("vertical_video_path")
    return None


def _vertical_video_path_for_scheme(parent_id, scheme, *, preview=False):
    normalized_scheme = _normalize_vertical_video_scheme(scheme)
    query = (
        PublicationTask.query
        .options(load_only(
            PublicationTask.id,
            PublicationTask.status,
            PublicationTask.result,
            PublicationTask.publisher_vertical_scheme,
            PublicationTask.publisher_vertical_video_path,
            PublicationTask.publisher_preview_seconds,
        ))
        .filter(PublicationTask.feature == "publisher")
        .filter(PublicationTask.source_task_id == parent_id)
        .filter(PublicationTask.publisher_vertical_enabled.is_(True))
        .order_by(PublicationTask.created_at.desc())
    )
    if preview:
        query = query.filter(PublicationTask.publisher_preview_seconds > 0)
    else:
        query = query.filter(or_(
            PublicationTask.publisher_preview_seconds == 0,
            PublicationTask.publisher_preview_seconds.is_(None),
        ))
    for child in query.limit(30).all():
        if child.status != "completed":
            continue
        result = child.result if isinstance(child.result, dict) else {}
        paths = result.get("vertical_video_paths") if isinstance(result.get("vertical_video_paths"), dict) else {}
        if _path_exists(paths.get(normalized_scheme)):
            return paths.get(normalized_scheme)
        if _normalize_vertical_video_scheme(child.publisher_vertical_scheme) == normalized_scheme:
            path = child.publisher_vertical_video_path or result.get("vertical_video_path")
            if _path_exists(path):
                return path
    return None


def _vertical_cover_path_for_task(task):
    projected = getattr(task, "report_audio_vertical_cover_path", None)
    if _path_exists(projected):
        return projected
    result = _task_mapping_attr(task, "result")
    video = result.get("video") if isinstance(result.get("video"), dict) else {}
    if _path_exists(video.get("vertical_cover_path")):
        return video.get("vertical_cover_path")
    keyframe_path = _video_keyframe_path(video, "vertical_cover")
    if _path_exists(keyframe_path):
        return keyframe_path
    for preview in (True, False):
        child = _latest_vertical_video_child(task.id, preview=preview)
        path = _vertical_cover_path_from_child(child)
        if child and child.status == "completed" and _path_exists(path):
            return path
    return None


def _podcast_cover_path_for_task(task):
    projected = getattr(task, "report_audio_podcast_cover_path", None)
    if _path_exists(projected):
        return projected
    result = _task_mapping_attr(task, "result")
    video = result.get("video") if isinstance(result.get("video"), dict) else {}
    if _path_exists(video.get("podcast_cover_path")):
        return video.get("podcast_cover_path")
    keyframe_path = _video_keyframe_path(video, "podcast_cover")
    if _path_exists(keyframe_path):
        return keyframe_path
    for preview in (True, False):
        child = _latest_vertical_video_child(task.id, preview=preview)
        path = _podcast_cover_path_from_child(child)
        if child and child.status == "completed" and _path_exists(path):
            return path
    return None


def _bilibili_cover_path_for_task(task):
    projected = getattr(task, "report_audio_bilibili_cover_path", None)
    if _path_exists(projected):
        return projected
    result = _task_mapping_attr(task, "result")
    video = result.get("video") if isinstance(result.get("video"), dict) else {}
    direct_path = video.get("bilibili_cover_path")
    if _path_exists(direct_path):
        return direct_path
    path = _video_keyframe_path(video, "bilibili_cover")
    if _path_exists(path):
        return path
    for preview in (True, False):
        child = _latest_vertical_video_child(task.id, preview=preview)
        path = _bilibili_cover_path_from_child(child)
        if child and child.status == "completed" and _path_exists(path):
            return path
    return None


def _video_keyframe_path(video, key):
    keyframes = video.get("keyframes") if isinstance(video, dict) and isinstance(video.get("keyframes"), dict) else {}
    artifact = keyframes.get(key) if isinstance(keyframes.get(key), dict) else {}
    return artifact.get("path")


def _report_bilibili_video_path(task, video_key):
    if video_key == "vertical":
        return _vertical_video_path_for_task(task)
    return _landscape_video_path_for_task(task)


def _report_bilibili_cover_path(task, video_key, requested_key=""):
    result = task.result if isinstance(task.result, dict) else {}
    video = _video_result_with_vertical_backfill(task)
    cover_map = {
        "cover": (result.get("video") or {}).get("cover_path") if isinstance(result.get("video"), dict) else None,
        "landscape_cover": (result.get("video") or {}).get("cover_path") if isinstance(result.get("video"), dict) else None,
        "vertical_cover": _vertical_cover_path_for_task(task),
        "podcast_cover": _podcast_cover_path_for_task(task),
        "bilibili_cover": _bilibili_cover_path_for_task(task),
        "pdf_first_page": (video.get("pdf_first_page_image") or {}).get("path") if isinstance(video.get("pdf_first_page_image"), dict) else None,
    }
    requested_key = str(requested_key or "").strip()
    if requested_key:
        path = cover_map.get(requested_key)
        return (path, requested_key) if _path_exists(path) else (None, requested_key)

    # Bilibili derives both 4:3 and 16:9 displays from one uploaded cover.
    # Prefer the dedicated dual-ratio-safe artifact for every video variant.
    candidates = ["bilibili_cover", "cover", "vertical_cover", "podcast_cover"]
    for key in candidates:
        path = cover_map.get(key)
        if _path_exists(path):
            return path, key
    return None, ""


def _report_bilibili_default_title(task, video_key):
    result = task.result if isinstance(task.result, dict) else {}
    params = task.params if isinstance(task.params, dict) else {}
    request_payload = params.get("report_audio_workflow") if isinstance(params.get("report_audio_workflow"), dict) else {}
    request_metadata = request_payload.get("metadata") if isinstance(request_payload.get("metadata"), dict) else {}
    request_video = request_payload.get("video") if isinstance(request_payload.get("video"), dict) else {}
    public_pdf = request_payload.get("pdf") if isinstance(request_payload.get("pdf"), dict) else {}
    if not public_pdf and isinstance(result.get("pdf"), dict):
        public_pdf = result.get("pdf")
    structured = result.get("structured_result") if isinstance(result.get("structured_result"), dict) else {}
    video = result.get("video") if isinstance(result.get("video"), dict) else {}
    variant_title = _first_compact_title(
        request_video.get("bilibili_title_7") if video_key == "vertical" else request_video.get("bilibili_title_6"),
        structured.get("vertical_title") if video_key == "vertical" else structured.get("landscape_title"),
        request_video.get("title"),
        video.get("cover_title"),
        _display_title_from_structured(structured),
        request_metadata.get("title"),
        public_pdf.get("topic_title"),
        public_pdf.get("filename"),
        task.task_title,
        "研报解读",
    )
    variant_subtitle = _first_compact_title(
        structured.get("vertical_subtitle") if video_key == "vertical" else structured.get("landscape_subtitle"),
        structured.get("cover_subtitle"),
    )
    institution = _report_publish_institution(variant_title, structured, public_pdf)
    date_text = _report_publish_date(public_pdf, structured, task.created_at)
    separator = " | " if video_key == "vertical" else " / "
    main_text = _strip_publish_institution(variant_title, institution)
    main_parts = [part.strip() for part in re.split(r"\s*/\s*", main_text) if part.strip()]
    title_parts = [
        institution,
        *main_parts,
        variant_subtitle,
        date_text,
    ]
    title = separator.join(part for part in title_parts if part)
    return clean_bilibili_upload_text(title, max_length=120)


def _report_publish_institution(title, structured, public_pdf):
    candidates = [
        title,
        (structured or {}).get("cover_title"),
        (structured or {}).get("title"),
        (public_pdf or {}).get("topic_title"),
        (public_pdf or {}).get("filename"),
    ]
    text = " ".join(str(item or "") for item in candidates)
    for alias, label in BANK_ALIASES:
        if re.search(re.escape(alias), text, flags=re.I):
            return label
    prefix = re.match(r"^\s*([^:：|｜/\n]{2,12}(?:证券|投行|研究所|研究院))\s*[:：]", str(title or ""))
    return prefix.group(1).strip() if prefix else "机构研报"


def _cover_title_with_institution(task, title):
    result = task.result if isinstance(task.result, dict) else {}
    params = task.params if isinstance(task.params, dict) else {}
    request_payload = params.get("report_audio_workflow") if isinstance(params.get("report_audio_workflow"), dict) else {}
    structured = result.get("structured_result") if isinstance(result.get("structured_result"), dict) else {}
    pdf = request_payload.get("pdf") if isinstance(request_payload.get("pdf"), dict) else {}
    if not pdf and isinstance(result.get("pdf"), dict):
        pdf = result.get("pdf")
    institution = _report_publish_institution(title, structured, pdf)
    if institution == "机构研报":
        institution = "机构"

    normalized = _normalize_cover_text(title, 160)
    has_generic_prefix = bool(re.match(r"^\s*(?:机构|机构研报|深度研报)\s*[:：]", normalized))
    explicit_full_alias = next(
        (
            alias for alias, label in BANK_ALIASES
            if label == institution
            and alias != institution
            and alias != "盛博"
            and re.match(rf"^\s*{re.escape(alias)}\s*[:：]", normalized, flags=re.I)
        ),
        None,
    )
    if explicit_full_alias and not has_generic_prefix:
        return normalized

    prefixes = [institution, "机构", "机构研报"]
    prefixes.extend(alias for alias, label in BANK_ALIASES if label == institution)
    prefix_pattern = "|".join(re.escape(item) for item in sorted({item for item in prefixes if item}, key=len, reverse=True))
    if prefix_pattern:
        prefix_re = re.compile(
            rf"^\s*(?:{prefix_pattern})(?:证券)?\s*[:：]\s*",
            flags=re.I,
        )
        while prefix_re.search(normalized):
            normalized = prefix_re.sub("", normalized, count=1)
    normalized = normalized.strip(" \t\r\n|｜/：:")
    return _normalize_cover_text(f"{institution}：{normalized}" if normalized else institution, 160)


def _strip_publish_institution(title, institution):
    text = " ".join(str(title or "").split()).strip()
    if not text:
        return ""
    prefixes = [institution]
    prefixes.extend(alias for alias, label in BANK_ALIASES if label == institution)
    for prefix in sorted({item for item in prefixes if item}, key=len, reverse=True):
        text = re.sub(rf"^\s*{re.escape(prefix)}\s*[:：]\s*", "", text, count=1, flags=re.I)
    return text.strip(" |｜/：:")


def _report_publish_date(public_pdf, structured, created_at):
    text = " ".join([
        str((public_pdf or {}).get("filename") or ""),
        str((public_pdf or {}).get("topic_title") or ""),
        str((structured or {}).get("title") or ""),
    ])
    full = re.search(r"\b(20\d{2})[./-]?(\d{2})[./-]?(\d{2})\b", text)
    if full:
        return f"{full.group(1)}-{full.group(2)}-{full.group(3)}"
    short = re.search(r"(?<!\d)(\d{2})(\d{2})(\d{2})(?!\d)", text)
    if short:
        return f"20{short.group(1)}-{short.group(2)}-{short.group(3)}"
    return (created_at or datetime.utcnow()).strftime("%Y-%m-%d")


def _video_result_with_vertical_backfill(task):
    result = task.result if isinstance(task.result, dict) else {}
    video = dict(result.get("video") or {}) if isinstance(result.get("video"), dict) else {}
    child = _latest_vertical_video_child(task.id, preview=False)
    preview_child = _latest_vertical_video_child(task.id, preview=True)
    landscape_child = _latest_landscape_video_child(task.id)
    if not child:
        child = None

    landscape_path = None
    if landscape_child and landscape_child.status == "completed":
        landscape_path = _landscape_video_path_from_child(landscape_child)
    if _path_exists(landscape_path):
        video["enabled"] = True
        video["status"] = "completed"
        video["landscape_enabled"] = True
        video["video_path"] = landscape_path
        video["publisher_task_id"] = landscape_child.id

    child_path = _vertical_video_path_from_child(child) if child else None
    if child and child.status == "completed" and _path_exists(child_path):
        video["enabled"] = True
        video["status"] = video.get("status") or "completed"
        video["vertical_enabled"] = True
        video["vertical_video_path"] = child_path
        video["vertical_publisher_task_id"] = child.id
        video["vertical_video_scheme"] = _vertical_child_scheme(child)
        child_cover_path = _vertical_cover_path_from_child(child)
        if _path_exists(child_cover_path):
            video["vertical_cover_path"] = child_cover_path
        child_podcast_cover_path = _podcast_cover_path_from_child(child)
        if _path_exists(child_podcast_cover_path):
            video["podcast_cover_path"] = child_podcast_cover_path
        child_bilibili_cover_path = _bilibili_cover_path_from_child(child)
        if _path_exists(child_bilibili_cover_path):
            video["bilibili_cover_path"] = child_bilibili_cover_path
    preview_path = _vertical_video_path_from_child(preview_child) if preview_child else None
    if preview_child and preview_child.status == "completed" and _path_exists(preview_path):
        preview_cover_path = _vertical_cover_path_from_child(preview_child)
        if _path_exists(preview_cover_path):
            video["vertical_cover_path"] = preview_cover_path
        preview_podcast_cover_path = _podcast_cover_path_from_child(preview_child)
        if _path_exists(preview_podcast_cover_path):
            video["podcast_cover_path"] = preview_podcast_cover_path
        preview_bilibili_cover_path = _bilibili_cover_path_from_child(preview_child)
        if _path_exists(preview_bilibili_cover_path):
            video["bilibili_cover_path"] = preview_bilibili_cover_path
        video["vertical_preview_video_path"] = preview_path
        video["vertical_preview_publisher_task_id"] = preview_child.id
        video["vertical_preview_seconds"] = _vertical_child_preview_seconds(preview_child)
        video["vertical_preview_scheme"] = _vertical_child_scheme(preview_child)
    return video


def _vertical_child_summary(task, parent_job_id=None, *, lightweight=False):
    if not task:
        return None
    payload = _child_task_summary_row(task) if lightweight else _child_task_summary(task)
    payload["vertical_video_scheme"] = _vertical_child_scheme(task)
    payload["vertical_video_scheme_label"] = _vertical_video_scheme_label(payload["vertical_video_scheme"])
    payload["preview_seconds"] = _vertical_child_preview_seconds(task)
    payload["is_preview"] = payload["preview_seconds"] > 0
    path = _vertical_video_path_from_child(task)
    if task.status == "completed" and _path_exists(path):
        query = "?preview=1" if payload["is_preview"] else ""
        payload["vertical_video"] = {
            "bytes": os.path.getsize(path),
            "download_url": f"/api/report-audio-workflows/jobs/{parent_job_id or task.source_task_id}/download-vertical-video{query}",
        }
    cover_path = _vertical_cover_path_from_child(task)
    if task.status == "completed" and _path_exists(cover_path):
        payload["vertical_cover"] = {
            "bytes": os.path.getsize(cover_path),
            "download_url": f"/api/report-audio-workflows/jobs/{parent_job_id or task.source_task_id}/download-vertical-cover",
        }
    podcast_cover_path = _podcast_cover_path_from_child(task)
    if task.status == "completed" and _path_exists(podcast_cover_path):
        payload["podcast_cover"] = {
            "bytes": os.path.getsize(podcast_cover_path),
            "download_url": f"/api/report-audio-workflows/jobs/{parent_job_id or task.source_task_id}/download-podcast-cover",
        }
    bilibili_cover_path = _bilibili_cover_path_from_child(task)
    if task.status == "completed" and _path_exists(bilibili_cover_path):
        payload["bilibili_cover"] = {
            "bytes": os.path.getsize(bilibili_cover_path),
            "download_url": f"/api/report-audio-workflows/jobs/{parent_job_id or task.source_task_id}/download-bilibili-cover",
        }
    return payload


_FINALIZATION_UNSET = object()


def _orchestration_payload(
    task,
    *,
    result=None,
    request_payload=None,
    finalization=None,
    visual_videos=None,
    bilibili_uploads=None,
):
    """Build a bounded operator-facing state projection without extra queries."""

    result = result if isinstance(result, dict) else {}
    request_payload = request_payload if isinstance(request_payload, dict) else {}
    visual_items = [item for item in (visual_videos or []) if isinstance(item, dict)][:8]
    uploads = [item for item in (bilibili_uploads or []) if isinstance(item, dict)][:8]
    waiting_raw = result.get("waiting_child") if isinstance(result.get("waiting_child"), dict) else {}
    waiting = {
        "label": str(getattr(task, "waiting_child_label", None) or waiting_raw.get("label") or "")[:80],
        "child_id": str(getattr(task, "waiting_child_id", None) or waiting_raw.get("child_id") or "")[:36],
        "status": str(getattr(task, "waiting_child_status", None) or waiting_raw.get("status") or "")[:40],
        "step": str(getattr(task, "waiting_child_step", None) or waiting_raw.get("step") or "")[:80],
        "updated_at": str(
            getattr(task, "waiting_child_updated_at", None) or waiting_raw.get("updated_at") or ""
        )[:64],
    }
    if not waiting["label"] and not waiting["child_id"]:
        waiting = None

    guard = result.get("runtime_guard") if isinstance(result.get("runtime_guard"), dict) else {}
    current_step = str(getattr(task, "current_step", None) or "")
    capacity = _capacity_summary(guard) if current_step.startswith("deferred_") else None
    completion = _completion_contract_summary(
        result.get("completion_contract")
        or ((finalization or {}).get("completion_contract") if isinstance(finalization, dict) else None)
    )
    video_request = request_payload.get("video") if isinstance(request_payload.get("video"), dict) else {}
    stages = _orchestration_stages(
        task,
        result=result,
        video_request=video_request,
        visual_items=visual_items,
        uploads=uploads,
        waiting=waiting,
        completion=completion,
        finalization=finalization,
    )
    return {
        "stages": stages,
        "waiting": waiting,
        "capacity": capacity,
        "completion_contract": completion,
    }


def _capacity_summary(value):
    if not isinstance(value, dict) or not value:
        return None
    countdown = _bounded_int(value.get("countdown"), 0, 0, 86400)
    created_at = str(value.get("created_at") or "")[:64] or None
    next_retry_at = None
    if created_at and countdown:
        try:
            parsed = datetime.fromisoformat(created_at.rstrip("Z"))
            next_retry_at = (parsed + timedelta(seconds=countdown)).isoformat() + "Z"
        except ValueError:
            next_retry_at = None
    return {
        "reason": str(value.get("reason") or "")[:80] or None,
        "message": str(value.get("message") or "")[:240] or None,
        "queue": str(value.get("queue") or "")[:80] or None,
        "active_count": _number_or_none(value.get("active_count"), int),
        "limit": _number_or_none(value.get("limit"), int),
        "countdown": countdown,
        "created_at": created_at,
        "next_retry_at": next_retry_at,
    }


def _completion_contract_summary(value):
    if not isinstance(value, dict) or not value:
        return None
    outputs = {}
    raw_outputs = value.get("outputs") if isinstance(value.get("outputs"), dict) else {}
    for key, item in list(raw_outputs.items())[:8]:
        if not isinstance(item, dict):
            continue
        outputs[str(key)[:80]] = {
            "ready": bool(item.get("ready")),
            "path": str(item.get("path") or "")[:600],
            "reason": str(item.get("reason") or "")[:80] or None,
            **({"duration": item.get("duration")} if item.get("duration") is not None else {}),
            **({"audio_duration": item.get("audio_duration")} if item.get("audio_duration") is not None else {}),
        }
    return {
        "schema_version": str(value.get("schema_version") or "")[:80] or None,
        "ready": bool(value.get("ready")),
        "outputs": outputs,
        "missing": [str(item)[:80] for item in (value.get("missing") or [])[:8]],
    }


def _orchestration_stages(
    task,
    *,
    result,
    video_request,
    visual_items,
    uploads,
    waiting,
    completion,
    finalization,
):
    task_status = str(getattr(task, "status", None) or "").lower()
    step = str(getattr(task, "current_step", None) or "").lower()
    failed = task_status in {"failed", "blocked_by_captcha", "canceled"}
    structured = result.get("structured_result") if isinstance(result.get("structured_result"), dict) else {}
    script_material = result.get("script_material") if isinstance(result.get("script_material"), dict) else {}
    audio = result.get("audio") if isinstance(result.get("audio"), dict) else {}
    audio_reuse = result.get("audio_reuse") if isinstance(result.get("audio_reuse"), dict) else {}
    script_ready = bool(
        str(structured.get("final_script_for_tts") or "").strip()
        or int(getattr(task, "final_script_chars", 0) or 0) > 0
    )
    script_reused = bool(script_ready and str(script_material.get("mode") or "").startswith("reused"))
    audio_ready = bool(str(audio.get("wav_path") or getattr(task, "report_audio_wav_path", None) or "").strip())
    audio_reused = bool(audio_ready and (audio_reuse.get("source_task_id") or audio_reuse.get("reused")))
    requested_schemes = video_request.get("vertical_video_schemes") or []
    if isinstance(requested_schemes, str):
        requested_schemes = [requested_schemes]
    visual_requested = "scheme3" in [str(item).lower() for item in requested_schemes]
    video_requested = _truthy(video_request.get("enabled"), False) or _truthy(
        video_request.get("vertical_enabled"),
        False,
    )
    visual_ready = any(item.get("scheme3_ready") is True for item in visual_items)
    visual_active = any(
        str(item.get("status") or "").lower() in {"queued", "pending", "running", "processing", "ready"}
        and item.get("scheme3_ready") is not True
        for item in visual_items
    )
    publisher_wait = waiting and waiting.get("label") == "publisher_video"
    video_ready = bool(completion and completion.get("outputs") and all(
        item.get("ready") for item in completion["outputs"].values()
    ))
    explicit_publish = _truthy(video_request.get("bilibili_enabled"), False)
    upload_completed = any(str(item.get("status") or "").lower() == "completed" for item in uploads)
    upload_active = any(
        str(item.get("status") or "").lower() in {"queued", "pending", "running", "processing", "submitting"}
        for item in uploads
    )

    def stage(key, label, state, detail=""):
        return {"key": key, "label": label, "state": state, "detail": str(detail or "")[:240] or None}

    script_state = "reused" if script_reused else ("completed" if script_ready else (
        "running" if any(token in step for token in ("gemini", "script", "structured", "review_cover")) else (
            "needs_attention" if failed and not script_ready else "pending"
        )
    ))
    audio_state = "reused" if audio_reused else ("completed" if audio_ready else (
        "waiting" if waiting and str(waiting.get("label") or "").startswith("doubao") else (
            "running" if any(token in step for token in ("doubao", "tts", "audio", "combine")) else (
                "needs_attention" if failed and script_ready else "pending"
            )
        )
    ))
    visual_state = "skipped" if not visual_requested else (
        "completed" if visual_ready else ("running" if visual_active else (
            "needs_attention" if failed and finalization and finalization.get("error") else "waiting"
        ))
    )
    video_state = "skipped" if not video_requested else ("completed" if video_ready else (
        "waiting" if publisher_wait else (
            "running" if any(token in step for token in ("pub_", "publisher", "video")) else (
                "needs_attention" if completion and completion.get("missing") else "pending"
            )
        )
    ))
    acceptance_state = "completed" if completion and completion.get("ready") else (
        "needs_attention" if completion and completion.get("missing") else "pending"
    )
    publish_state = "completed" if upload_completed else (
        "running" if upload_active else ("pending" if explicit_publish else "skipped")
    )
    return [
        stage("input", "输入", "completed"),
        stage("script", "文案", script_state, "复用已有文案" if script_reused else ""),
        stage("audio", "音频", audio_state, "复用已有整篇 WAV" if audio_reused else ""),
        stage("visual", "视觉", visual_state),
        stage("video", "成片", video_state, "等待 Publisher 子任务" if publisher_wait else ""),
        stage(
            "acceptance",
            "验收",
            acceptance_state,
            ", ".join((completion or {}).get("missing") or []),
        ),
        stage("publish", "发布", publish_state, "发布保持显式开关" if not explicit_publish else ""),
    ]


def _job_payload(
    task,
    include_raw=False,
    *,
    visual_videos=None,
    finalization=_FINALIZATION_UNSET,
):
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
    vertical_child = _latest_vertical_video_child(task.id, preview=False)
    vertical_preview_child = _latest_vertical_video_child(task.id, preview=True)
    children = dict(result.get("children") or {})
    if vertical_child:
        children["publisher_vertical_video"] = _vertical_child_summary(vertical_child, task.id, lightweight=True)
    if vertical_preview_child:
        children["publisher_vertical_preview_video"] = _vertical_child_summary(vertical_preview_child, task.id, lightweight=True)
    if finalization is _FINALIZATION_UNSET:
        finalization = finalization_summary_for_audio(task.id)
    if finalization:
        children["finalization"] = finalization
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
    # Older completed jobs may have stored a generic "机构" cover prefix
    # before a filename alias was added.  Derive the display label at read
    # time so the detail page is corrected without rewriting finished assets.
    detail_title = _display_title_with_recognized_institution(task, detail_title)
    display_cover_title = _display_title_with_recognized_institution(
        task,
        (structured_result or {}).get("cover_title") or "",
    )
    audio_script = _audio_script_payload(structured_result, result.get("tts_text"))
    visual_items = list(visual_videos or [])
    bilibili_uploads = bilibili_upload_children(
        task.id,
        parent_feature=FEATURE_NAME,
        source_label=BILIBILI_UPLOAD_SOURCE,
    )
    orchestration = _orchestration_payload(
        task,
        result=result,
        request_payload=request_payload,
        finalization=finalization,
        visual_videos=visual_items,
        bilibili_uploads=bilibili_uploads,
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
            "pdf_evidence_enabled": (
                _truthy(request_payload.get("pdf_evidence_enabled"), True)
                if "pdf_evidence_enabled" in request_payload
                else True
            ),
            "followups": request_payload.get("followups") or [],
            "gemini_url": request_payload.get("gemini_url"),
            "manual_gemini": _public_manual_gemini(request_payload.get("manual_gemini")),
            "tts_provider": request_payload.get("tts_provider") or TTS_PROVIDER_DEFAULT,
            "tts_voice": request_payload.get("tts_voice"),
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
        "cover_title": display_cover_title or (structured_result or {}).get("cover_title") or "",
        "cover_subtitle": cover_subtitle,
        "bilibili_titles": {
            "landscape": _report_bilibili_default_title(task, "landscape"),
            "vertical": _report_bilibili_default_title(task, "vertical"),
        },
        "tts_text": result.get("tts_text"),
        "audio_script": audio_script,
        "audio": _public_audio(task.id, audio),
        "video": _public_video(task.id, video_result),
        "publisher_video": result.get("publisher_video"),
        "publisher_vertical_video": children.get("publisher_vertical_video"),
        "publisher_vertical_preview_video": children.get("publisher_vertical_preview_video"),
        "finalization": finalization,
        "bilibili_uploads": bilibili_uploads,
        "orchestration": orchestration,
        "pdf_first_page_image": _public_image(task.id, result.get("pdf_first_page_image")),
        "download_audio_url": f"/api/report-audio-workflows/jobs/{task.id}/download-audio" if audio and audio.get("wav_path") else None,
        "download_result_url": f"/api/report-audio-workflows/jobs/{task.id}/download-result" if result.get("output_path") else None,
        "download_video_url": f"/api/report-audio-workflows/jobs/{task.id}/download-video" if _path_exists(video_result.get("video_path")) else None,
        "download_vertical_video_url": f"/api/report-audio-workflows/jobs/{task.id}/download-vertical-video" if _path_exists(video_result.get("vertical_video_path")) else None,
        "download_vertical_cover_url": f"/api/report-audio-workflows/jobs/{task.id}/download-vertical-cover" if _path_exists(video_result.get("vertical_cover_path")) else None,
        "download_podcast_cover_url": f"/api/report-audio-workflows/jobs/{task.id}/download-podcast-cover" if _path_exists(video_result.get("podcast_cover_path")) else None,
        "download_bilibili_cover_url": f"/api/report-audio-workflows/jobs/{task.id}/download-bilibili-cover" if _bilibili_cover_path_for_task(task) else None,
        "download_cover_url": f"/api/report-audio-workflows/jobs/{task.id}/download-cover" if _path_exists((result.get("video") or {}).get("cover_path")) else None,
        "metadata": result.get("metadata") or request_payload.get("metadata") or {},
        "notifications": result.get("notifications") or {},
        "logs": result.get("logs") or [],
        "visual_videos": visual_items,
    }
    if include_raw:
        payload["raw_params"] = params
        payload["raw_result"] = result
    return payload


def _build_scheme3_scene_timeline(rows, audio_seconds):
    """Build a complete scene timeline, falling back to script-weighted timing."""
    try:
        audio_seconds = float(audio_seconds or 0)
    except (TypeError, ValueError):
        audio_seconds = 0
    if audio_seconds < 0.1:
        return None

    scenes = []
    for row in sorted(rows or [], key=lambda item: int(getattr(item, "sequence", 0) or 0)):
        sequence = int(getattr(row, "sequence", 0) or 0)
        if sequence < 1:
            continue
        image_path = str(getattr(row, "image_path", None) or "").strip()
        if (
            str(getattr(row, "image_status", "") or "").lower() != "completed"
            or not _path_exists(image_path)
            or has_browser_overlay_artifact(image_path)
        ):
            return None
        scenes.append({
            "asset_id": str(getattr(row, "asset_id", None) or getattr(row, "id", None) or ""),
            "sequence": sequence,
            "image_path": str(Path(image_path).resolve()),
            "script_text": str(getattr(row, "script_text", None) or "").strip(),
            "stored_start": getattr(row, "timeline_start_seconds", None),
            "stored_end": getattr(row, "timeline_end_seconds", None),
        })
    if not scenes:
        return None

    stored_timeline = []
    previous_end = 0.0
    stored_valid = True
    for scene in scenes:
        try:
            start = float(scene["stored_start"])
            end = float(scene["stored_end"])
        except (TypeError, ValueError):
            stored_valid = False
            break
        if end - start < 0.1 or abs(start - previous_end) > 0.25:
            stored_valid = False
            break
        stored_timeline.append((start, end))
        previous_end = end
    if stored_valid and abs(previous_end - audio_seconds) <= 0.5:
        for scene, (start, end) in zip(scenes, stored_timeline):
            scene["start_seconds"] = round(start, 6)
            scene["end_seconds"] = round(end, 6)
            scene["duration_seconds"] = round(end - start, 6)
        timing_source = "aligned_timeline"
    else:
        weights = []
        for scene in scenes:
            compact_chars = len(re.sub(r"\s+", "", scene.get("script_text") or ""))
            weights.append(max(1.0, float(compact_chars)))
        total_weight = sum(weights)
        cursor = 0.0
        for index, (scene, weight) in enumerate(zip(scenes, weights)):
            end = audio_seconds if index == len(scenes) - 1 else audio_seconds * sum(weights[:index + 1]) / total_weight
            scene["start_seconds"] = round(cursor, 6)
            scene["end_seconds"] = round(end, 6)
            scene["duration_seconds"] = round(end - cursor, 6)
            cursor = end
        timing_source = "script_weight"

    for scene in scenes:
        scene.pop("stored_start", None)
        scene.pop("stored_end", None)
    return {
        "version": 1,
        "timing_source": timing_source,
        "audio_seconds": round(audio_seconds, 6),
        "scene_count": len(scenes),
        "scenes": scenes,
    }


def _linked_visual_scene_timeline(current_user, audio_task, workflow_id, *, audio_seconds):
    """Resolve same-PDF completed scene images without requiring an intermediate MP4."""
    workflow_id = str(workflow_id or "").strip()
    if not workflow_id:
        return None
    attachment_id = str(
        getattr(audio_task, "report_audio_pdf_attachment_id", None) or ""
    ).strip()
    filename = str(getattr(audio_task, "report_audio_pdf_filename", None) or "").strip()
    pdf_bytes = _linked_pdf_bytes_identity(audio_task)
    if not attachment_id and (not filename or not pdf_bytes):
        return None

    parent_query = (
        db.session.query(PublicationTask.id)
        .filter(
            PublicationTask.id == workflow_id,
            PublicationTask.feature == VISUAL_VIDEO_WORKFLOW_FEATURE,
            PublicationTask.user_id.in_(_visible_user_ids(current_user)),
        )
    )
    if attachment_id:
        parent_query = parent_query.filter(PublicationTask.report_audio_pdf_attachment_id == attachment_id)
    else:
        parent_query = parent_query.filter(
            PublicationTask.report_audio_pdf_filename == filename,
            PublicationTask.report_audio_pdf_bytes == pdf_bytes,
        )
    if not parent_query.one_or_none():
        return None

    rows = (
        db.session.query(
            ReportStickFigureSceneAsset.id.label("asset_id"),
            ReportStickFigureSceneAsset.sequence,
            ReportStickFigureSceneAsset.image_status,
            ReportStickFigureSceneAsset.image_path,
            ReportStickFigureSceneAsset.timeline_start_seconds,
            ReportStickFigureSceneAsset.timeline_end_seconds,
            ReportVisualPlanSegment.script_text,
        )
        .join(
            ReportVisualPlanSegment,
            ReportVisualPlanSegment.id == ReportStickFigureSceneAsset.segment_id,
        )
        .filter(
            ReportStickFigureSceneAsset.workflow_task_id == workflow_id,
            ReportStickFigureSceneAsset.sequence >= 1,
        )
        .order_by(ReportStickFigureSceneAsset.sequence.asc())
        .all()
    )
    return _build_scheme3_scene_timeline(rows, audio_seconds)


def _linked_visual_video_path(current_user, audio_task, workflow_id):
    """Resolve one same-PDF visual video without trusting a client file path."""
    workflow_id = str(workflow_id or "").strip()
    if not workflow_id:
        return None
    attachment_id = str(
        getattr(audio_task, "report_audio_pdf_attachment_id", None) or ""
    ).strip()
    filename = str(getattr(audio_task, "report_audio_pdf_filename", None) or "").strip()
    pdf_bytes = _linked_pdf_bytes_identity(audio_task)
    if not attachment_id and (not filename or not pdf_bytes):
        return None

    query = (
        db.session.query(
            PublicationTask.id,
            PublicationTask.task_video_path,
        )
        .filter(
            PublicationTask.id == workflow_id,
            PublicationTask.feature == VISUAL_VIDEO_WORKFLOW_FEATURE,
            PublicationTask.user_id.in_(_visible_user_ids(current_user)),
        )
    )
    if attachment_id:
        query = query.filter(PublicationTask.report_audio_pdf_attachment_id == attachment_id)
    else:
        query = query.filter(
            PublicationTask.report_audio_pdf_filename == filename,
            PublicationTask.report_audio_pdf_bytes == pdf_bytes,
        )
    parent = query.one_or_none()
    if not parent:
        return None
    parent_path = str(parent.task_video_path or "").strip()
    if _path_exists(parent_path):
        return parent_path

    child = (
        db.session.query(PublicationTask.task_video_path)
        .filter(
            PublicationTask.feature == VISUAL_VIDEO_RENDER_FEATURE,
            PublicationTask.parent_task_id == workflow_id,
            PublicationTask.status == "completed",
        )
        .order_by(PublicationTask.created_at.desc())
        .first()
    )
    child_path = str(getattr(child, "task_video_path", None) or "").strip()
    return child_path if _path_exists(child_path) else None


def _linked_visual_video_payloads(current_user, audio_task):
    """Return visual-video workflows that belong to the same PDF.

    Synced PDFs use the immutable attachment id.  A manual-upload legacy row
    may not have one, so only an exact filename + byte-size pair is accepted as
    fallback; filename alone is deliberately insufficient.
    """
    attachment_id = str(
        getattr(audio_task, "report_audio_pdf_attachment_id", None) or ""
    ).strip()
    filename = str(getattr(audio_task, "report_audio_pdf_filename", None) or "").strip()
    pdf_bytes = _linked_pdf_bytes_identity(audio_task)
    if not attachment_id and (not filename or not pdf_bytes):
        return []

    query = (
        db.session.query(
            PublicationTask.id,
            PublicationTask.status,
            PublicationTask.current_step,
            PublicationTask.progress,
            PublicationTask.error_message,
            PublicationTask.user_id,
            PublicationTask.source_task_id,
            PublicationTask.source_feature,
            PublicationTask.task_title,
            PublicationTask.task_video_path,
            PublicationTask.report_visual_scheme_key,
            PublicationTask.created_at,
            PublicationTask.updated_at,
            PublicationTask.completed_at,
        )
        .filter(PublicationTask.feature == VISUAL_VIDEO_WORKFLOW_FEATURE)
        .filter(PublicationTask.user_id.in_(_visible_user_ids(current_user)))
    )
    if attachment_id:
        query = query.filter(PublicationTask.report_audio_pdf_attachment_id == attachment_id)
        identity_kind = "attachment_id"
    else:
        query = query.filter(
            PublicationTask.report_audio_pdf_filename == filename,
            PublicationTask.report_audio_pdf_bytes == pdf_bytes,
        )
        identity_kind = "filename_bytes"
    parents = query.order_by(PublicationTask.created_at.desc()).limit(20).all()
    if not parents:
        return []

    workflow_ids = [str(row.id) for row in parents]
    scene_rows = (
        db.session.query(
            ReportStickFigureSceneAsset.workflow_task_id,
            ReportStickFigureSceneAsset.sequence,
            ReportStickFigureSceneAsset.image_status,
            ReportStickFigureSceneAsset.image_path,
        )
        .filter(
            ReportStickFigureSceneAsset.workflow_task_id.in_(workflow_ids),
            ReportStickFigureSceneAsset.sequence >= 1,
        )
        .all()
    )
    scene_summaries = {}
    for row in scene_rows:
        summary = scene_summaries.setdefault(str(row.workflow_task_id), {
            "body_total": 0,
            "images_completed": 0,
            "direct_render_ready": True,
        })
        summary["body_total"] += 1
        image_ready = (
            str(row.image_status or "").lower() == "completed"
            and _path_exists(row.image_path)
        )
        if image_ready:
            summary["images_completed"] += 1
        else:
            summary["direct_render_ready"] = False
    for summary in scene_summaries.values():
        if summary["body_total"] <= 0:
            summary["direct_render_ready"] = False

    child_rows = (
        db.session.query(
            PublicationTask.id,
            PublicationTask.parent_task_id,
            PublicationTask.status,
            PublicationTask.current_step,
            PublicationTask.progress,
            PublicationTask.error_message,
            PublicationTask.task_video_path,
            PublicationTask.created_at,
            PublicationTask.completed_at,
        )
        .filter(
            PublicationTask.feature == VISUAL_VIDEO_RENDER_FEATURE,
            PublicationTask.parent_task_id.in_(workflow_ids),
        )
        .order_by(PublicationTask.created_at.desc())
        .all()
    )
    latest_child = {}
    for row in child_rows:
        latest_child.setdefault(str(row.parent_task_id or ""), row)

    result = []
    audio_task_id = str(getattr(audio_task, "id", None) or "")
    for parent in parents:
        workflow_id = str(parent.id)
        child = latest_child.get(workflow_id)
        video_path = str(
            (parent.task_video_path or "")
            or (getattr(child, "task_video_path", None) or "")
        ).strip()
        video_ready = _path_exists(video_path)
        scene_summary = scene_summaries.get(workflow_id, {
            "body_total": 0,
            "images_completed": 0,
            "direct_render_ready": False,
        })
        state_row = child or parent
        status = "succeeded" if video_ready else STATUS_MAP.get(
            str(state_row.status or ""),
            str(state_row.status or "queued"),
        )
        if not video_ready and not child and status == "queued":
            status = "ready"
        if not video_ready and not child and scene_summary["direct_render_ready"]:
            # Scene-image tasks finish independently from the visual parent.
            # Treat the live asset projection as authoritative even when an
            # older parent row still says images_queued/65%.
            status = "ready"
        scheme_key = str(parent.report_visual_scheme_key or "stick_figure")
        try:
            scheme_payload = visual_scheme(scheme_key, require_configured=False).public_payload()
        except ValueError:
            scheme_payload = {
                "key": scheme_key,
                "name": scheme_key,
                "description": "历史视觉方案",
                "configured": False,
            }
        result.append({
            "workflow_job_id": workflow_id,
            "visual_scheme": scheme_payload,
            "status": status,
            "step": (
                "done" if video_ready
                else ("images_done" if scene_summary["direct_render_ready"] else str(state_row.current_step or status))
            ),
            "progress": (
                100 if video_ready
                else (
                    max(75, min(99, int(state_row.progress or 0)))
                    if scene_summary["direct_render_ready"]
                    else max(0, min(100, int(state_row.progress or 0)))
                )
            ),
            "error": None if video_ready or scene_summary["direct_render_ready"] else state_row.error_message,
            "source": "report_audio" if (
                str(parent.source_feature or "") == FEATURE_NAME
                and str(parent.source_task_id or "") == audio_task_id
            ) else "same_pdf",
            "pdf_identity": identity_kind,
            "title": parent.task_title,
            "created_at": parent.created_at.isoformat() + "Z" if parent.created_at else None,
            "updated_at": parent.updated_at.isoformat() + "Z" if parent.updated_at else None,
            "completed_at": (
                (getattr(child, "completed_at", None) or parent.completed_at).isoformat() + "Z"
                if (getattr(child, "completed_at", None) or parent.completed_at)
                else None
            ),
            "detail_url": (
                "/report-stick-figure-video-workflow-detail.html"
                f"?workflow_job_id={workflow_id}"
            ),
            "scheme3_ready": bool(video_ready or scene_summary["direct_render_ready"]),
            "scene_assets": scene_summary,
            "video": {
                "url": (
                    "/api/report-stick-figure-video-workflows/jobs/"
                    f"{workflow_id}/video/file"
                ),
                "bytes": Path(video_path).stat().st_size,
            } if video_ready else None,
        })
    return result


def _linked_pdf_bytes_identity(audio_task):
    """Canonical string identity matching PublicationTask's varchar projection."""

    try:
        value = int(getattr(audio_task, "report_audio_pdf_bytes", None) or 0)
    except (TypeError, ValueError):
        return ""
    return str(value) if value > 0 else ""


def _ready_linked_visual_workflow_id(current_user, audio_task, visual_scheme_key):
    """Resolve readiness from live same-PDF scene assets, not client cache."""
    requested_key = str(visual_scheme_key or "stick_figure").strip().lower()
    for item in _linked_visual_video_payloads(current_user, audio_task):
        scheme = item.get("visual_scheme") if isinstance(item.get("visual_scheme"), dict) else {}
        if str(scheme.get("key") or "").strip().lower() != requested_key:
            continue
        if item.get("scheme3_ready") is True:
            return str(item.get("workflow_job_id") or "").strip()
    return ""


def _job_status_payload(task, *, visual_videos=None):
    status = STATUS_MAP.get(task.status, task.status or "queued")
    live_children = _live_child_tasks(task.id)
    children = {}
    doubao_items = []
    for child in live_children:
        if child.feature == doubao_browser_tts.FEATURE_NAME:
            doubao_items.append(_child_task_summary_row(child))
            continue
        summary = _child_task_summary_row(child)
        if child.feature in {gemini_browser.FEATURE_NAME, gemini_browser.LEGACY_FEATURE_NAME}:
            children["gemini"] = summary
        elif child.feature == "publisher" and "publisher_video" not in children:
            children["publisher_video"] = summary

    vertical_child = _latest_vertical_video_child(task.id, preview=False)
    vertical_preview_child = _latest_vertical_video_child(task.id, preview=True)
    if vertical_child:
        children["publisher_vertical_video"] = _vertical_child_summary(vertical_child, task.id, lightweight=True)
    if vertical_preview_child:
        children["publisher_vertical_preview_video"] = _vertical_child_summary(vertical_preview_child, task.id, lightweight=True)
    finalization = finalization_summary_for_audio(task.id)
    if finalization:
        children["finalization"] = finalization

    bilibili_uploads = bilibili_upload_children(
        task.id,
        parent_feature=FEATURE_NAME,
        source_label=BILIBILI_UPLOAD_SOURCE,
    )
    task_result = getattr(task, "result", None)
    task_params = getattr(task, "params", None)
    if isinstance(task_result, dict):
        orchestration_result = task_result
        request_payload = (
            task_params.get("report_audio_workflow")
            if isinstance(task_params, dict) and isinstance(task_params.get("report_audio_workflow"), dict)
            else {}
        )
    else:
        orchestration_result, request_payload = _status_orchestration_fragments(task.id)
    orchestration = _orchestration_payload(
        task,
        result=orchestration_result,
        request_payload=request_payload,
        finalization=finalization,
        visual_videos=visual_videos,
        bilibili_uploads=bilibili_uploads,
    )

    payload = {
        "job_id": task.id,
        "status": status,
        "step": task.current_step,
        "progress": task.progress or 0,
        "error": task.error_message,
        "created_at": task.created_at.isoformat() + "Z" if task.created_at else None,
        "updated_at": task.updated_at.isoformat() + "Z" if task.updated_at else None,
        "completed_at": task.completed_at.isoformat() + "Z" if task.completed_at else None,
        "children": children,
        "doubao_browser_tts": doubao_items,
        "finalization": finalization,
        "bilibili_uploads": bilibili_uploads,
        "orchestration": orchestration,
        "visual_videos": list(visual_videos or []),
    }
    return payload


def _status_orchestration_fragments(task_id):
    """Read only the small JSON branches required by status polling."""

    row = db.session.query(
        PublicationTask.result["runtime_guard"].label("runtime_guard"),
        PublicationTask.result["completion_contract"].label("completion_contract"),
        PublicationTask.result["script_material"].label("script_material"),
        PublicationTask.result["audio_reuse"].label("audio_reuse"),
        PublicationTask.params["report_audio_workflow"]["video"].label("video_request"),
    ).filter(PublicationTask.id == str(task_id)).one_or_none()
    if not row:
        return {}, {}
    result = {
        key: getattr(row, key)
        for key in ("runtime_guard", "completion_contract", "script_material", "audio_reuse")
        if isinstance(getattr(row, key), dict)
    }
    request_payload = {
        "video": row.video_request if isinstance(row.video_request, dict) else {}
    }
    return result, request_payload


def _audio_script_payload(structured_result, tts_text):
    if not isinstance(structured_result, dict):
        return None
    text = str(structured_result.get("final_script_for_tts") or "").strip()
    if not text:
        return None
    tts_meta = tts_text if isinstance(tts_text, dict) else {}
    return {
        "text": text,
        "chars": len(text),
        "plain_chars": len(re.sub(r"\s+", "", text)),
        "chunks": tts_meta.get("chunks"),
        "provider": structured_result.get("provider"),
        "quality_issue": structured_result_quality_issue(structured_result),
    }


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
    vertical_preview_child = child_bundle.get("publisher_vertical_preview_video") or {}
    vertical_video = vertical_child.get("vertical_video") if isinstance(vertical_child, dict) else None
    vertical_preview_video = vertical_preview_child.get("vertical_video") if isinstance(vertical_preview_child, dict) else None
    vertical_cover = (
        vertical_preview_child.get("vertical_cover")
        if isinstance(vertical_preview_child, dict)
        else None
    ) or (
        vertical_child.get("vertical_cover")
        if isinstance(vertical_child, dict)
        else None
    )
    podcast_cover = (
        vertical_preview_child.get("podcast_cover")
        if isinstance(vertical_preview_child, dict)
        else None
    ) or (
        vertical_child.get("podcast_cover")
        if isinstance(vertical_child, dict)
        else None
    )
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
    if vertical_preview_video:
        video_payload = video_payload or {
            "enabled": _bool_or_none(row.video_enabled),
            "status": row.video_status,
            "publisher_task_id": row.video_publisher_task_id,
            "message": row.video_message,
            "cover_title": row.cover_title,
            "cover_subtitle": cover_subtitle,
        }
        video_payload["vertical_preview_video"] = vertical_preview_video
        video_payload["vertical_preview_seconds"] = vertical_preview_child.get("preview_seconds")
        video_payload["vertical_preview_scheme"] = vertical_preview_child.get("vertical_video_scheme")
        video_payload = {key: value for key, value in video_payload.items() if value is not None}
    if vertical_cover:
        video_payload = video_payload or {
            "enabled": _bool_or_none(row.video_enabled),
            "status": row.video_status,
            "publisher_task_id": row.video_publisher_task_id,
            "message": row.video_message,
            "cover_title": row.cover_title,
            "cover_subtitle": cover_subtitle,
        }
        video_payload["vertical_cover"] = vertical_cover
        video_payload = {key: value for key, value in video_payload.items() if value is not None}
    if podcast_cover:
        video_payload = video_payload or {
            "enabled": _bool_or_none(row.video_enabled),
            "status": row.video_status,
            "publisher_task_id": row.video_publisher_task_id,
            "message": row.video_message,
            "cover_title": row.cover_title,
            "cover_subtitle": cover_subtitle,
        }
        video_payload["podcast_cover"] = podcast_cover
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
        "publisher_vertical_preview_video": vertical_preview_child or None,
        "download_audio_url": f"/api/report-audio-workflows/jobs/{row.id}/download-audio" if row.audio_wav_path else None,
        "download_result_url": f"/api/report-audio-workflows/jobs/{row.id}/download-result" if row.output_path else None,
        "download_video_url": f"/api/report-audio-workflows/jobs/{row.id}/download-video" if row.video_path and _path_exists(row.video_path) else None,
        "download_vertical_video_url": f"/api/report-audio-workflows/jobs/{row.id}/download-vertical-video" if vertical_video else None,
        "download_vertical_cover_url": f"/api/report-audio-workflows/jobs/{row.id}/download-vertical-cover" if vertical_cover else None,
        "download_podcast_cover_url": f"/api/report-audio-workflows/jobs/{row.id}/download-podcast-cover" if podcast_cover else None,
        "download_cover_url": f"/api/report-audio-workflows/jobs/{row.id}/download-cover" if row.cover_path else None,
        "metadata": {"title": row.metadata_title} if row.metadata_title else {},
        "logs": [],
    }


def _live_child_tasks(parent_id):
    try:
        base_query = _child_task_summary_query()
        rows = (
            base_query
            .filter(or_(
                PublicationTask.parent_task_id == parent_id,
                PublicationTask.source_task_id == parent_id,
            ))
            .order_by(PublicationTask.created_at.asc())
            .all()
        )
        return _dedupe_child_summary_rows(rows)
    except Exception:
        return []


def _child_task_summary_query():
    return PublicationTask.query.with_entities(
        PublicationTask.id.label("id"),
        PublicationTask.celery_task_id.label("celery_task_id"),
        PublicationTask.status.label("status"),
        PublicationTask.current_step.label("current_step"),
        PublicationTask.progress.label("progress"),
        func.substr(PublicationTask.error_message, 1, 500).label("error_message"),
        PublicationTask.feature.label("feature"),
        PublicationTask.parent_task_id.label("parent_task_id"),
        PublicationTask.source_task_id.label("source_task_id"),
        PublicationTask.task_title.label("task_title"),
        PublicationTask.task_video_path.label("task_video_path"),
        PublicationTask.report_audio_wav_path.label("audio_wav_path"),
        PublicationTask.report_audio_seconds.label("audio_seconds"),
        PublicationTask.report_audio_audio_bytes.label("audio_bytes"),
        PublicationTask.publisher_vertical_enabled.label("publisher_vertical_enabled"),
        PublicationTask.publisher_vertical_scheme.label("publisher_vertical_scheme"),
        PublicationTask.publisher_preview_seconds.label("publisher_preview_seconds"),
        PublicationTask.publisher_vertical_video_path.label("publisher_vertical_video_path"),
        PublicationTask.publisher_vertical_cover_path.label("publisher_vertical_cover_path"),
        PublicationTask.publisher_podcast_cover_path.label("publisher_podcast_cover_path"),
        PublicationTask.publisher_bilibili_cover_path.label("publisher_bilibili_cover_path"),
        PublicationTask.created_at.label("created_at"),
        PublicationTask.completed_at.label("completed_at"),
    )


def _dedupe_child_summary_rows(rows):
    deduped = {}
    for row in rows or []:
        if not row or not row.id:
            continue
        deduped.setdefault(row.id, row)
    return sorted(
        deduped.values(),
        key=lambda row: (row.created_at or datetime.min, row.id),
    )


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
        base_query = _child_task_summary_query()
        rows = (
            base_query
            .filter(or_(
                PublicationTask.parent_task_id.in_(ids),
                PublicationTask.source_task_id.in_(ids),
            ))
            .order_by(PublicationTask.created_at.asc())
            .all()
        )
        rows = _dedupe_child_summary_rows(rows)
    except Exception:
        return grouped

    gemini_features = {gemini_browser.FEATURE_NAME, gemini_browser.LEGACY_FEATURE_NAME}
    vertical_selected = {False: {}, True: {}}
    for row in rows:
        parent_id = next(
            (
                candidate
                for candidate in (
                    str(row.parent_task_id or "").strip(),
                    str(row.source_task_id or "").strip(),
                )
                if candidate in grouped
            ),
            "",
        )
        if not parent_id:
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
            if _is_vertical_video_candidate_child(row):
                preview = _is_vertical_preview_child(row)
                current = vertical_selected[preview].get(parent_id)
                if not current or _vertical_child_priority(row) > _vertical_child_priority(current):
                    vertical_selected[preview][parent_id] = row

    for preview, selected in vertical_selected.items():
        key = "publisher_vertical_preview_video" if preview else "publisher_vertical_video"
        for parent_id, row in selected.items():
            summary = _vertical_child_summary(row, parent_id, lightweight=True)
            grouped[parent_id][key] = summary
            grouped[parent_id]["children"][key] = summary
    return grouped


def _vertical_child_summaries_for_parent_ids(parent_ids, *, preview=False):
    regular, previews = _vertical_child_summary_groups(parent_ids)
    return previews if preview else regular


def _vertical_child_summary_groups(parent_ids):
    ids = {str(item or "").strip() for item in parent_ids if str(item or "").strip()}
    if not ids:
        return {}, {}
    try:
        candidates = (
            PublicationTask.query.with_entities(
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
                PublicationTask.publisher_vertical_enabled.label("publisher_vertical_enabled"),
                PublicationTask.publisher_vertical_scheme.label("publisher_vertical_scheme"),
                PublicationTask.publisher_preview_seconds.label("publisher_preview_seconds"),
                PublicationTask.publisher_vertical_video_path.label("publisher_vertical_video_path"),
                PublicationTask.publisher_vertical_cover_path.label("publisher_vertical_cover_path"),
                PublicationTask.publisher_podcast_cover_path.label("publisher_podcast_cover_path"),
                PublicationTask.publisher_bilibili_cover_path.label("publisher_bilibili_cover_path"),
                PublicationTask.created_at.label("created_at"),
                PublicationTask.completed_at.label("completed_at"),
            )
            .filter(PublicationTask.feature == "publisher")
            .filter(PublicationTask.source_task_id.in_(ids))
            .filter(PublicationTask.publisher_vertical_enabled.is_(True))
            .order_by(PublicationTask.created_at.desc())
            .all()
        )
    except Exception:
        return {}, {}

    selected = {False: {}, True: {}}
    for task in candidates:
        parent_id = str(task.source_task_id or "").strip()
        if parent_id not in ids or not _is_vertical_video_candidate_child(task):
            continue
        preview = _is_vertical_preview_child(task)
        current = selected[preview].get(parent_id)
        if not current or _vertical_child_priority(task) > _vertical_child_priority(current):
            selected[preview][parent_id] = task

    return tuple(
        {
            parent_id: _vertical_child_summary(task, parent_id, lightweight=True)
            for parent_id, task in selected[preview].items()
        }
        for preview in (False, True)
    )


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


def _display_title_with_recognized_institution(task, value):
    """Replace a legacy generic cover prefix using the current alias rules.

    This is presentation-only: it deliberately does not write the task's
    stored result or regenerate a completed cover asset.
    """
    text = _normalize_cover_text(value, 160)
    if not text or not re.match(r"^\s*(?:机构|机构研报|深度研报)\s*[:：]", text):
        return text
    result = task.result if isinstance(getattr(task, "result", None), dict) else {}
    params = task.params if isinstance(getattr(task, "params", None), dict) else {}
    request_payload = params.get("report_audio_workflow") if isinstance(params.get("report_audio_workflow"), dict) else {}
    structured = result.get("structured_result") if isinstance(result.get("structured_result"), dict) else {}
    public_pdf = request_payload.get("pdf") if isinstance(request_payload.get("pdf"), dict) else {}
    if not public_pdf and isinstance(result.get("pdf"), dict):
        public_pdf = result.get("pdf")
    institution = _report_publish_institution(text, structured, public_pdf)
    if institution == "机构研报":
        return text
    return _cover_title_with_institution(task, text)


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

    vertical_schemes = _normalize_vertical_video_schemes(
        pick("vertical_video_schemes", "vertical_schemes", default=None),
        pick("vertical_video_scheme", "vertical_scheme", default=VERTICAL_VIDEO_SCHEME_DEFAULT),
    )
    primary_vertical_scheme = _primary_vertical_video_scheme(vertical_schemes)

    return {
        "enabled": _truthy(pick("landscape_video_enabled", "video_enabled", "enabled", default=False)),
        "title": str(pick("video_title", "cover_title", "title", default="") or "").strip(),
        "subtitle": str(pick("video_subtitle", "cover_subtitle", "subtitle", default="") or "").strip(),
        "tag_text": str(pick("video_tag_text", "cover_tag_text", "tag_text", default="") or "").strip(),
        "date_text": str(pick("video_date_text", "cover_date_text", "date_text", default="") or "").strip(),
        "cta_enabled": _truthy(pick("video_cta_enabled", "cta_enabled", default=True), default=True),
        "cta_text": str(pick("video_cta_text", "cta_text", default="") or "").strip(),
        "vertical_enabled": _truthy(pick("vertical_video_enabled", "vertical_enabled", default=False)),
        "vertical_video_scheme": primary_vertical_scheme,
        "vertical_video_schemes": vertical_schemes,
        "render_mode": _normalize_video_render_mode(
            pick("video_render_mode", "render_mode", "output_mode", default="full_video")
        ),
        "keyframe_outputs": _normalize_keyframe_outputs(
            pick("keyframe_outputs", "keyframe_output_keys", default=None)
        ),
        "preview_seconds": _bounded_int(
            pick("preview_seconds", "duration_seconds", "short_video_seconds", default=VERTICAL_PREVIEW_SECONDS_DEFAULT),
            VERTICAL_PREVIEW_SECONDS_DEFAULT,
            3,
            60,
        ),
        "bilibili_enabled": _truthy(pick("bilibili_enabled", "publish_bilibili_enabled", default=False)),
        "sub_burn_enabled": _truthy(pick("sub_burn_enabled", default=True), default=True),
    }


def _has_explicit_keyframe_outputs(data):
    if "keyframe_outputs" in data or "keyframe_output_keys" in data:
        return True
    raw_video = data.get("video")
    if isinstance(raw_video, dict):
        return "keyframe_outputs" in raw_video or "keyframe_output_keys" in raw_video
    if isinstance(raw_video, str) and raw_video.strip():
        try:
            parsed = json.loads(raw_video)
        except (TypeError, ValueError):
            return False
        return isinstance(parsed, dict) and (
            "keyframe_outputs" in parsed or "keyframe_output_keys" in parsed
        )
    return False


def _normalize_keyframe_outputs(value):
    if isinstance(value, str):
        text = value.strip()
        if not text:
            items = []
        else:
            try:
                parsed = json.loads(text)
                items = parsed if isinstance(parsed, list) else [parsed]
            except (TypeError, ValueError):
                items = re.split(r"[,;，；\s]+", text)
    elif isinstance(value, (list, tuple, set)):
        items = value
    else:
        items = [] if value is None else [value]

    outputs = []
    for item in items:
        key = str(item or "").strip().lower()
        if key in KEYFRAME_OUTPUTS and key not in outputs:
            outputs.append(key)
    return outputs


def _parse_visual_scheme_keys(data):
    raw = data.get("visual_scheme_keys")
    if raw is None:
        raw = data.get("visual_schemes")
    if raw is None:
        items = []
        if _truthy(data.get("stick_figure_enabled"), False):
            items.append("stick_figure")
        if _truthy(data.get("doraemon_enabled"), False):
            items.append("doraemon")
    elif isinstance(raw, list):
        items = raw
    elif isinstance(raw, str):
        value = raw.strip()
        if not value:
            items = []
        else:
            try:
                parsed = json.loads(value)
                items = parsed if isinstance(parsed, list) else [parsed]
            except (TypeError, ValueError):
                items = [part.strip() for part in value.split(",")]
    else:
        items = [raw]

    normalized = []
    for item in items:
        key = str(item or "").strip().lower()
        if not key or key in normalized:
            continue
        scheme = visual_scheme(key)
        normalized.append(scheme.key)
    return normalized


def _ensure_scheme3_visual_workflow(visual_scheme_keys, video_options):
    """Add the visual dependency required by a full scheme-3 vertical render."""
    normalized = list(visual_scheme_keys or [])
    needs_scheme3_visual = (
        (video_options or {}).get("render_mode") == "full_video"
        and bool((video_options or {}).get("vertical_enabled"))
        and "scheme3" in ((video_options or {}).get("vertical_video_schemes") or [])
    )
    if needs_scheme3_visual and "stick_figure" not in normalized:
        normalized.append("stick_figure")
    return normalized


def _normalize_video_render_mode(value):
    text = str(value or "").strip().lower()
    if text in {"keyframe", "keyframes", "frames", "preview_keyframes"}:
        return "keyframes"
    if text in {"preview", "short", "short_video", "preview_10s", "10s", "10sec", "ten_seconds"}:
        return "preview_10s"
    return "full_video"


def _parse_script_material_only(data):
    for key in ("script_material_only", "material_only", "script_only"):
        if key in data:
            return _truthy(data.get(key), False)
    return False


def _parse_pdf_evidence_enabled(data):
    """New submissions default off; an explicit opt-in remains available."""

    for key in ("pdf_evidence_enabled", "enable_pdf_evidence", "pdf_evidence"):
        if key in data:
            return _truthy(data.get(key), False)
    return False


def _parse_manual_gemini_result(data):
    raw = None
    for key in (
        "manual_gemini_result",
        "manual_gemini_response",
        "manual_gemini_final_response",
        "gemini_result_text",
        "gemini_final_response_text",
    ):
        if key in data:
            raw = data.get(key)
            break
    if raw is None:
        return None

    initial_text = ""
    final_text = ""
    if isinstance(raw, dict):
        initial_text = str(raw.get("initial_response_text") or raw.get("initial_text") or "").strip()
        final_text = str(
            raw.get("final_response_text")
            or raw.get("response_text")
            or raw.get("text")
            or raw.get("content")
            or ""
        ).strip()
    else:
        text = str(raw or "").strip()
        if not text:
            return None
        if text.startswith("{") and text.endswith("}"):
            try:
                parsed = json.loads(text)
            except Exception:
                parsed = None
            if isinstance(parsed, dict):
                return _parse_manual_gemini_result({"manual_gemini_result": parsed})
        final_text = text

    if not final_text:
        return None
    return {
        "source": "manual_input",
        "initial_response_text": initial_text,
        "final_response_text": final_text,
        "chars": len(final_text),
        "provided_at": datetime.utcnow().isoformat() + "Z",
    }


def _public_manual_gemini(manual):
    if not manual:
        return None
    if isinstance(manual, str):
        chars = len(manual.strip())
        provided_at = None
    elif isinstance(manual, dict):
        text = str(
            manual.get("final_response_text")
            or manual.get("response_text")
            or manual.get("text")
            or manual.get("content")
            or ""
        ).strip()
        chars = int(manual.get("chars") or len(text))
        provided_at = manual.get("provided_at")
    else:
        return None
    return {
        "provided": chars > 0,
        "source": "manual_input",
        "chars": chars,
        "provided_at": provided_at,
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
    attachment = (
        db.session.query(
            ZsxqAttachment.id,
            ZsxqAttachment.group_id,
            ZsxqAttachment.tab_id,
            ZsxqAttachment.topic_id,
            ZsxqAttachment.name,
            ZsxqAttachment.local_path,
            ZsxqAttachment.download_status,
        )
        .filter(ZsxqAttachment.id == str(asset_id))
        .one_or_none()
    )
    if attachment is None:
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

    topic = (
        db.session.query(ZsxqTopic.title, ZsxqTopic.tab_label)
        .filter(
            ZsxqTopic.group_id == attachment.group_id,
            ZsxqTopic.tab_id == attachment.tab_id,
            ZsxqTopic.topic_id == attachment.topic_id,
        )
        .one_or_none()
    )
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
        "material": _empty_pdf_asset_material_stats(),
    }


def _empty_pdf_asset_material_stats():
    return {
        "has_material": False,
        "job_id": "",
        "feature": "",
        "feature_label": "",
        "status": "",
        "step": "",
        "progress": 0,
        "has_script": False,
        "script_chars": 0,
        "created_at": None,
        "completed_at": None,
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
    material_stats = _pdf_asset_material_stats(ids, user_id=user_id)
    for asset_id, bucket in stats.items():
        bucket["material"] = material_stats.get(asset_id) or _empty_pdf_asset_material_stats()
    return stats


def _pdf_asset_material_stats(asset_ids, user_id=None):
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
            PublicationTask.feature.label("feature"),
            PublicationTask.source_task_id.label("source_task_id"),
            PublicationTask.report_audio_pdf_attachment_id.label("attachment_id"),
            PublicationTask.final_script_chars.label("final_script_chars"),
            PublicationTask.created_at.label("created_at"),
            PublicationTask.completed_at.label("completed_at"),
        )
        .filter(PublicationTask.feature.in_(MATERIAL_STATUS_FEATURES))
        .filter(PublicationTask.status != "canceled")
        .filter(or_(
            PublicationTask.source_task_id.in_(ids),
            PublicationTask.report_audio_pdf_attachment_id.in_(ids),
        ))
    )
    if user_id:
        rows = rows.filter(PublicationTask.user_id.in_(_visible_user_ids(user_id)))

    by_asset = {asset_id: [] for asset_id in ids}
    for row in rows.order_by(PublicationTask.created_at.desc()).all():
        keys = {
            str(row.source_task_id or "").strip(),
            str(row.attachment_id or "").strip(),
        }
        for key in keys:
            if key in by_asset:
                by_asset[key].append(row)

    return {
        asset_id: _pdf_asset_material_row_payload(_best_pdf_asset_material_row(rows_for_asset))
        for asset_id, rows_for_asset in by_asset.items()
    }


def _best_pdf_asset_material_row(rows):
    candidates = [row for row in rows or [] if row]
    if not candidates:
        return None
    for row in candidates:
        if int(row.final_script_chars or 0) > 0:
            return row
    for row in candidates:
        if row.status == "completed":
            return row
    return candidates[0]


def _pdf_asset_material_row_payload(row):
    if not row:
        return _empty_pdf_asset_material_stats()
    status = STATUS_MAP.get(row.status, row.status or "queued")
    script_chars = int(row.final_script_chars or 0)
    return {
        "has_material": True,
        "job_id": row.id,
        "feature": row.feature or "",
        "feature_label": _material_feature_label(row.feature),
        "status": status,
        "step": row.current_step or "",
        "progress": row.progress or 0,
        "has_script": script_chars > 0 or status == "succeeded",
        "script_chars": script_chars,
        "created_at": row.created_at.isoformat() + "Z" if row.created_at else None,
        "completed_at": row.completed_at.isoformat() + "Z" if row.completed_at else None,
    }


def _material_feature_label(feature):
    return {
        SCRIPT_MATERIAL_FEATURE_NAME: "基础文案物料",
        FEATURE_NAME: "研报转语音",
        "report_audio_workflow": "研报转语音",
    }.get(feature or "", feature or "")


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
        "render_mode": video.get("render_mode"),
        "publisher_task_id": video.get("publisher_task_id"),
        "message": video.get("message"),
        "landscape_enabled": video.get("landscape_enabled"),
        "vertical_enabled": video.get("vertical_enabled"),
        "cover_title": video.get("cover_title"),
        "cover_subtitle": video.get("cover_subtitle"),
        "landscape_title": video.get("landscape_title"),
        "landscape_subtitle": video.get("landscape_subtitle"),
        "vertical_title": video.get("vertical_title"),
        "vertical_subtitle": video.get("vertical_subtitle"),
        "vertical_background_title": video.get("vertical_background_title"),
        "vertical_background_subtitle": video.get("vertical_background_subtitle"),
        "vertical_background_titles": video.get("vertical_background_titles"),
        "vertical_background_subtitles": video.get("vertical_background_subtitles"),
        "vertical_background_regenerated_at": video.get("vertical_background_regenerated_at"),
        "cover_regenerated_at": video.get("cover_regenerated_at"),
        "vertical_cover_title": video.get("vertical_cover_title"),
        "vertical_cover_subtitle": video.get("vertical_cover_subtitle"),
        "vertical_cover_regenerated_at": video.get("vertical_cover_regenerated_at"),
        "podcast_cover_title": video.get("podcast_cover_title"),
        "podcast_cover_subtitle": video.get("podcast_cover_subtitle"),
        "podcast_cover_regenerated_at": video.get("podcast_cover_regenerated_at"),
        "bilibili_cover_title": video.get("bilibili_cover_title"),
        "bilibili_cover_subtitle": video.get("bilibili_cover_subtitle"),
        "bilibili_cover_regenerated_at": video.get("bilibili_cover_regenerated_at"),
        "vertical_video_scheme": video.get("vertical_video_scheme"),
        "vertical_video_schemes": video.get("vertical_video_schemes"),
        "vertical_preview_scheme": video.get("vertical_preview_scheme"),
        "vertical_preview_seconds": video.get("vertical_preview_seconds"),
        "bilibili_result": video.get("bilibili_result"),
    }
    video_path = video.get("video_path")
    vertical_video_path = video.get("vertical_video_path")
    vertical_video_paths = video.get("vertical_video_paths") if isinstance(video.get("vertical_video_paths"), dict) else {}
    vertical_preview_video_path = video.get("vertical_preview_video_path")
    vertical_preview_video_paths = video.get("vertical_preview_video_paths") if isinstance(video.get("vertical_preview_video_paths"), dict) else {}
    vertical_cover_path = video.get("vertical_cover_path")
    podcast_cover_path = video.get("podcast_cover_path")
    bilibili_cover_path = video.get("bilibili_cover_path")
    if not _path_exists(vertical_cover_path):
        vertical_cover_path = _video_keyframe_path(video, "vertical_cover")
    if not _path_exists(podcast_cover_path):
        podcast_cover_path = _video_keyframe_path(video, "podcast_cover")
    if not _path_exists(bilibili_cover_path):
        bilibili_cover_path = _video_keyframe_path(video, "bilibili_cover")
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
    public_vertical_videos = {}
    for scheme, path in vertical_video_paths.items():
        normalized_scheme = _normalize_vertical_video_scheme(scheme)
        if path and os.path.exists(path):
            public_vertical_videos[normalized_scheme] = {
                "scheme": normalized_scheme,
                "label": _vertical_video_scheme_label(normalized_scheme),
                "bytes": os.path.getsize(path),
                "download_url": f"/api/report-audio-workflows/jobs/{job_id}/download-vertical-video?scheme={normalized_scheme}",
            }
    if public_vertical_videos:
        public["vertical_videos"] = public_vertical_videos
    if vertical_preview_video_path and os.path.exists(vertical_preview_video_path):
        public["vertical_preview_video"] = {
            "bytes": os.path.getsize(vertical_preview_video_path),
            "download_url": f"/api/report-audio-workflows/jobs/{job_id}/download-vertical-video?preview=1",
        }
    public_vertical_previews = {}
    for scheme, path in vertical_preview_video_paths.items():
        normalized_scheme = _normalize_vertical_video_scheme(scheme)
        if path and os.path.exists(path):
            public_vertical_previews[normalized_scheme] = {
                "scheme": normalized_scheme,
                "label": _vertical_video_scheme_label(normalized_scheme),
                "bytes": os.path.getsize(path),
                "download_url": f"/api/report-audio-workflows/jobs/{job_id}/download-vertical-video?preview=1&scheme={normalized_scheme}",
            }
    if public_vertical_previews:
        public["vertical_preview_videos"] = public_vertical_previews
    if vertical_cover_path and os.path.exists(vertical_cover_path):
        public["vertical_cover"] = {
            "bytes": os.path.getsize(vertical_cover_path),
            "download_url": f"/api/report-audio-workflows/jobs/{job_id}/download-vertical-cover",
        }
    if podcast_cover_path and os.path.exists(podcast_cover_path):
        public["podcast_cover"] = {
            "bytes": os.path.getsize(podcast_cover_path),
            "download_url": f"/api/report-audio-workflows/jobs/{job_id}/download-podcast-cover",
        }
    if bilibili_cover_path and os.path.exists(bilibili_cover_path):
        public["bilibili_cover"] = {
            "bytes": os.path.getsize(bilibili_cover_path),
            "download_url": f"/api/report-audio-workflows/jobs/{job_id}/download-bilibili-cover",
            "width": 1146,
            "height": 717,
            "ratio": "1146:717",
        }
    if cover_path and os.path.exists(cover_path):
        public["cover"] = {
            "bytes": os.path.getsize(cover_path),
            "download_url": f"/api/report-audio-workflows/jobs/{job_id}/download-cover",
        }
    keyframes = _public_keyframes(job_id, video)
    if keyframes:
        public["keyframes"] = keyframes
    image = video.get("pdf_first_page_image")
    if isinstance(image, dict):
        public["pdf_first_page_image"] = _public_image(job_id, image)
    return {key: value for key, value in public.items() if value is not None}


def _public_keyframes(job_id, video):
    raw = video.get("keyframes") if isinstance(video, dict) else None
    if not isinstance(raw, dict):
        return {}
    public = {}
    for key, item in raw.items():
        if not isinstance(item, dict):
            continue
        path = item.get("path")
        if not path or not os.path.exists(path):
            continue
        safe_key = re.sub(r"[^a-zA-Z0-9_-]", "", str(key or ""))[:80]
        if not safe_key:
            continue
        public[safe_key] = {
            "label": item.get("label") or safe_key,
            "ratio": item.get("ratio"),
            "bytes": os.path.getsize(path),
            "width": item.get("width"),
            "height": item.get("height"),
            "title": item.get("title"),
            "subtitle": item.get("subtitle"),
            "scheme": item.get("scheme"),
            "updated_at": item.get("updated_at"),
            "download_url": f"/api/report-audio-workflows/jobs/{job_id}/download-keyframe/{safe_key}",
        }
    return public


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
