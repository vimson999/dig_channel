import asyncio
import copy
import json
import math
import mimetypes
import os
import re
import threading
import time
import tempfile
import subprocess
import uuid
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from flask import Blueprint, current_app, g, jsonify, request, send_file
from sqlalchemy import func, or_, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import load_only
from sqlalchemy.orm.attributes import flag_modified
from werkzeug.utils import secure_filename

from celery_config import celery_app
from models import db
from models.platform_credential import PlatformCredentialAccount
from models.task import PublicationTask
from services import gemini_browser
from services.bilibili_publish import (
    bilibili_upload_action_row as shared_bilibili_upload_action_row,
    bilibili_upload_child_payload as shared_bilibili_upload_child_payload,
    bilibili_upload_children as shared_bilibili_upload_children,
    cancel_bilibili_upload_child as shared_cancel_bilibili_upload_child,
    create_bilibili_upload_task,
    enqueue_bilibili_upload_task,
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
from services.mp3_video_workflow import (
    DEFAULT_GEMINI_URL,
    AUDIO_EXTENSIONS,
    FEATURE_NAME,
    INFOGRAPHIC_PROMPT,
    Mp3VideoWorkflowError,
    QUEUE_NAME,
    VISUAL_ARTIFACT_KEYS,
    _db_retry,
    _video_file_ready,
    audio_meta_from_asset_reference,
    initial_gemini_prompt,
    initial_transcript_prompt,
    list_audio_assets,
    regenerate_visual_artifact,
    status_payload as workflow_status_payload,
)
from services.runtime_guard import (
    apply_async_after_db_release,
    assert_db_session_clean,
    release_db_session_quietly,
    revoke_celery_task_after_db_commit,
)
from services.asr_cache_service import probe_media_duration
from tasks.mp3_video_workflow import process_mp3_video_workflow_job
from utils.auth_utils import require_login
from utils.date_filters import submission_date_utc_range


mp3_video_workflow_bp = Blueprint("mp3_video_workflow", __name__, url_prefix="/api/mp3-video-workflows")

STATUS_MAP = {
    "pending": "queued",
    "processing": "running",
    "completed": "succeeded",
    "failed": "failed",
    "canceled": "canceled",
}
SYSTEM_USER_ID = "system"
WORKFLOW_SOURCE_FEATURE = "zsxq_attachment"
UPLOAD_SOURCE_FEATURE = "local_mp3_upload"
CONTENT_PIPELINE_ASSET_SOURCE_FEATURE = "content_pipeline_asset"
DEFAULT_AUDIO_UPLOAD_MAX_MB = 512
AUDIO_ASSET_CLIENT_TRIGGERS = {
    "composition_end",
    "debounce",
    "enter",
    "initial",
    "manual",
    "page_jump",
    "pagination",
    "refresh",
    "scope",
    "search",
}
AUDIO_ASSET_CLIENT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
try:
    DOWNLOAD_RESULT_CACHE_TTL_SECONDS = int(os.environ.get("MP3_VIDEO_WORKFLOW_DOWNLOAD_RESULT_CACHE_TTL", "30"))
except (TypeError, ValueError):
    DOWNLOAD_RESULT_CACHE_TTL_SECONDS = 30
DOWNLOAD_RESULT_CACHE_MAX_SIZE = 512
_download_result_cache = {}
_download_result_cache_lock = threading.RLock()
try:
    JOB_DETAIL_CACHE_TTL_SECONDS = max(
        0,
        int(os.environ.get("MP3_VIDEO_WORKFLOW_DETAIL_CACHE_TTL", "5")),
    )
except (TypeError, ValueError):
    JOB_DETAIL_CACHE_TTL_SECONDS = 5
try:
    JOB_DETAIL_STALE_MAX_SECONDS = max(
        JOB_DETAIL_CACHE_TTL_SECONDS,
        int(os.environ.get("MP3_VIDEO_WORKFLOW_DETAIL_STALE_MAX", "300")),
    )
except (TypeError, ValueError):
    JOB_DETAIL_STALE_MAX_SECONDS = 300
try:
    JOB_DETAIL_STATEMENT_TIMEOUT_MS = max(
        500,
        min(15000, int(os.environ.get("MP3_VIDEO_WORKFLOW_DETAIL_DB_TIMEOUT_MS", "5000"))),
    )
except (TypeError, ValueError):
    JOB_DETAIL_STATEMENT_TIMEOUT_MS = 5000
try:
    JOB_LIST_CACHE_TTL_SECONDS = max(
        0,
        int(os.environ.get("MP3_VIDEO_WORKFLOW_LIST_CACHE_TTL", "5")),
    )
except (TypeError, ValueError):
    JOB_LIST_CACHE_TTL_SECONDS = 5
try:
    JOB_LIST_STALE_MAX_SECONDS = max(
        JOB_LIST_CACHE_TTL_SECONDS,
        int(os.environ.get("MP3_VIDEO_WORKFLOW_LIST_STALE_MAX", "300")),
    )
except (TypeError, ValueError):
    JOB_LIST_STALE_MAX_SECONDS = 300
_job_list_cache = {}
_job_list_cache_lock = threading.RLock()
_job_detail_cache = {}
_job_detail_cache_lock = threading.RLock()
_JOB_PAYLOAD_BILIBILI_UNSET = object()

JOB_LIST_COLUMNS = (
    PublicationTask.id,
    PublicationTask.celery_task_id,
    PublicationTask.status,
    PublicationTask.current_step,
    PublicationTask.progress,
    PublicationTask.error_message,
    PublicationTask.feature,
    PublicationTask.user_id,
    PublicationTask.source_task_id,
    PublicationTask.source_feature,
    PublicationTask.task_title,
    PublicationTask.mp3_audio_filename,
    PublicationTask.mp3_audio_topic_title,
    PublicationTask.mp3_audio_tab_label,
    PublicationTask.mp3_audio_path,
    PublicationTask.mp3_audio_download_count,
    PublicationTask.mp3_audio_duration_seconds,
    PublicationTask.created_at,
    PublicationTask.updated_at,
    PublicationTask.completed_at,
)


def _truthy(value, default=True):
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on", "y", "生成", "开启"}:
        return True
    if text in {"0", "false", "no", "off", "n", "不生成", "关闭"}:
        return False
    return bool(default)


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


def _audio_asset_source_identity(audio_asset_id, *, is_upload=False):
    reference = str(audio_asset_id or "").strip()
    if is_upload:
        return UPLOAD_SOURCE_FEATURE, reference
    if reference.startswith("pipeline:"):
        asset_id = reference.split(":", 1)[1].strip()
        try:
            asset_id = str(uuid.UUID(asset_id))
        except (ValueError, AttributeError, TypeError) as exc:
            raise ValueError("内容流水线音频素材 ID 无效") from exc
        return CONTENT_PIPELINE_ASSET_SOURCE_FEATURE, asset_id
    return WORKFLOW_SOURCE_FEATURE, reference


@mp3_video_workflow_bp.route("/status", methods=["GET"])
@require_login
def service_status(_current_user):
    return jsonify({
        "success": True,
        "data": {
            "provider": {
                "id": FEATURE_NAME,
                "name": "MP3 到 Gemini/IMA+DS 图片与横竖屏视频编排",
                **workflow_status_payload(),
            },
            "gemini_browser": gemini_browser.status_payload(),
        },
    })


@mp3_video_workflow_bp.route("/bilibili-charging-options", methods=["GET"])
@require_login
def bilibili_charging_options(_current_user):
    credential_id = _clean_upload_text(request.args.get("credential_id"), max_length=80)
    if not credential_id:
        return jsonify({"success": False, "message": "请选择 B站账号"}), 400
    try:
        from services.bilibili.uploader import BiliUploader
        from services.platform_credential_service import resolve_bilibili_auth

        auth = resolve_bilibili_auth(account_id=credential_id, required=True)
        uploader = BiliUploader(
            sessdata=auth.get("sessdata"),
            bili_jct=auth.get("bili_jct"),
            buvid3=auth.get("buvid3"),
            dedeuserid=auth.get("dedeuserid"),
        )
        options = uploader.charging_pay_options()
        return jsonify({"success": True, "data": options, **options})
    except Exception as exc:
        return jsonify({"success": False, "message": str(exc)}), 502


@mp3_video_workflow_bp.route("/bilibili-seasons", methods=["GET"])
@require_login
def bilibili_seasons(_current_user):
    credential_id = _clean_upload_text(request.args.get("credential_id"), max_length=80)
    if not credential_id:
        return jsonify({"success": False, "message": "请选择 B站账号"}), 400
    try:
        from bilibili_api import user
        from services.platform_credential_service import resolve_bilibili_auth
        from services.bilibili.uploader import BiliUploader

        auth = resolve_bilibili_auth(account_id=credential_id, required=True)
        uploader = BiliUploader(
            sessdata=auth.get("sessdata"),
            bili_jct=auth.get("bili_jct"),
            buvid3=auth.get("buvid3"),
            dedeuserid=auth.get("dedeuserid"),
        )

        async def _fetch():
            uid = int(auth.get("dedeuserid") or 0)
            if not uid:
                info = await user.get_self_info(uploader.credential)
                uid = int((info or {}).get("mid") or 0)
            if not uid:
                raise ValueError("B站账号缺少 DedeUserID，无法读取合集")
            channels = await user.User(credential=uploader.credential, uid=uid).get_channels()
            seasons = []
            for channel in channels or []:
                meta = getattr(channel, "meta", None) or {}
                season_id = meta.get("season_id") or meta.get("id")
                title = meta.get("title") or meta.get("name")
                if not season_id or not title:
                    continue
                seasons.append({
                    "id": str(season_id),
                    "title": str(title),
                    "name": str(meta.get("name") or title),
                })
            return seasons

        seasons = asyncio.run(_fetch())
        return jsonify({"success": True, "data": {"items": seasons}, "items": seasons})
    except Exception as exc:
        return jsonify({"success": False, "message": str(exc)}), 502


def _audio_asset_client_trace():
    """Return log-safe ownership metadata without persisting user input."""
    raw_client_id = (request.args.get("client_id") or "").strip()
    client_id = (
        raw_client_id
        if AUDIO_ASSET_CLIENT_ID_RE.fullmatch(raw_client_id)
        else "unknown"
    )
    raw_trigger = (request.args.get("client_trigger") or "").strip().lower()
    client_trigger = (
        raw_trigger
        if raw_trigger in AUDIO_ASSET_CLIENT_TRIGGERS
        else "unknown"
    )

    def bounded_trace_int(name, maximum):
        raw_value = (request.args.get(name) or "").strip()
        if not re.fullmatch(r"[0-9]{1,10}", raw_value):
            return 0
        value = int(raw_value)
        return value if 1 <= value <= maximum else 0

    return {
        "client_id": client_id,
        "client_seq": bounded_trace_int("client_seq", 1_000_000_000),
        "client_trigger": client_trigger,
        "client_inflight": bounded_trace_int("client_inflight", 16),
    }


@mp3_video_workflow_bp.route("/audio-assets", methods=["GET"])
@require_login
def audio_assets(_current_user):
    client_trace = _audio_asset_client_trace()
    page = _bounded_int(request.args.get("page"), 1, 1, 100000)
    per_page = _bounded_int(request.args.get("per_page"), 30, 1, 100)
    payload = list_audio_assets(
        page=page,
        per_page=per_page,
        q=(request.args.get("q") or "").strip(),
        group_id=(request.args.get("group_id") or "").strip(),
        tab_id=(request.args.get("tab_id") or "").strip(),
        scope=(request.args.get("scope") or "all").strip(),
        force_refresh=_truthy(request.args.get("refresh"), default=False),
    )
    pagination = payload.get("pagination") or {}
    current_app.logger.info(
        "[MP3_AUDIO_ASSET_CLIENT] request_id=%s client_id=%s client_seq=%s "
        "client_trigger=%s client_inflight=%s force_refresh=%s cache_hit=%s "
        "cache_age_ms=%s items=%s",
        getattr(g, "request_id", "unknown"),
        client_trace["client_id"],
        client_trace["client_seq"],
        client_trace["client_trigger"],
        client_trace["client_inflight"],
        _truthy(request.args.get("refresh"), default=False),
        pagination.get("catalog_cache_hit"),
        pagination.get("catalog_cache_age_ms"),
        len(payload.get("items") or []),
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
        audio = audio_meta_from_asset_reference(asset_id)
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 404
    mimetype = mimetypes.guess_type(audio["path"])[0] or "audio/mpeg"
    return send_file(
        audio["path"],
        mimetype=mimetype,
        as_attachment=False,
        download_name=audio.get("filename") or f"{asset_id}.mp3",
    )


@mp3_video_workflow_bp.route("/audio-export", methods=["POST"])
@require_login
def export_audio_only(_current_user):
    from services.audio_clip_export import clip_options, export_audio_clip

    data = _request_payload()
    try:
        options = dict(start=data.get('start', '0'), end=data.get('end', ''), speed=data.get('speed', '1'))
        clip_options(**options)
        # Authentication may have opened a transaction; never retain it for media I/O.
        release_db_session_quietly()
        uploaded = request.files.get('audio')
        with tempfile.TemporaryDirectory(prefix='audio-export-') as directory:
            if uploaded:
                error = _validate_audio_upload(uploaded)
                if error:
                    raise ValueError(error)
                filename, extension = _audio_upload_filename(uploaded)
                source = Path(directory) / f'input.{extension}'
                uploaded.save(source)
                if source.stat().st_size > _max_audio_upload_bytes():
                    raise ValueError('上传音频超过大小限制')
            else:
                try:
                    audio = dict(audio_meta_from_asset_reference(str(data.get('audio_asset_id') or '')))
                finally:
                    release_db_session_quietly()
                source = Path(audio['path'])
                filename = audio.get('filename') or source.name
            target = Path(directory) / 'clip.mp3'
            export_audio_clip(source, target, **options)
            # send_file opens the file before the temporary directory is removed (POSIX).
            response = send_file(target, mimetype='audio/mpeg', as_attachment=True,
                                 download_name=f'{Path(filename).stem}_剪辑.mp3', conditional=False)
            response.headers['Cache-Control'] = 'no-store'
            return response
    except ValueError as exc:
        return jsonify(success=False, message=str(exc)), 400
    except (subprocess.SubprocessError, OSError):
        current_app.logger.exception('Standalone audio export failed')
        return jsonify(success=False, message='音频导出失败，请确认文件有效，或缩短片段后重试'), 422


@mp3_video_workflow_bp.route("/jobs", methods=["POST"])
@require_login
def create_job(current_user):
    data = _request_payload()
    user_id = str(getattr(current_user, "id", None))
    audio_file = request.files.get("audio") or request.files.get("mp3") or request.files.get("file")
    audio_asset_id = (data.get("audio_asset_id") or data.get("attachment_id") or "").strip()
    if not audio_file and not audio_asset_id:
        return jsonify({"success": False, "message": "请上传 MP3 或选择一个已同步 MP3"}), 400
    if audio_file:
        upload_error = _validate_audio_upload(audio_file)
        if upload_error:
            return jsonify({"success": False, "message": upload_error}), 400

    transcript_text = str(data.get("transcript_text") or data.get("ima_transcript") or "").strip()
    ds_response_text = str(data.get("ds_response_text") or data.get("direct_ds_response") or data.get("source_response_text") or "").strip()
    source_mode = str(data.get("input_source") or data.get("source_mode") or "").strip().lower()
    if not source_mode:
        source_mode = "ds_response" if ds_response_text else ("ima_transcript" if transcript_text else "gemini_audio")
    if source_mode in {"transcript", "deepseek_transcript"}:
        source_mode = "ima_transcript"
    if source_mode in {"direct_ds", "manual_ds", "ds_result", "deepseek_response"}:
        source_mode = "ds_response"
    if source_mode in {"gemini", "gemini_mp3"}:
        source_mode = "gemini_audio"
    if source_mode not in {"gemini_audio", "ima_transcript", "ds_response"}:
        return jsonify({"success": False, "message": "input_source 只能是 gemini_audio、ima_transcript 或 ds_response"}), 400
    render_mode = str(data.get("render_mode") or data.get("output_mode") or "keyframes").strip().lower()
    if render_mode in {"keyframe", "preview", "preview_keyframes", "frames"}:
        render_mode = "keyframes"
    if render_mode in {"full", "video", "videos"}:
        render_mode = "full_video"
    if render_mode not in {"keyframes", "full_video"}:
        return jsonify({"success": False, "message": "render_mode 只能是 keyframes 或 full_video"}), 400
    generate_subtitles_value = False
    for key in ("generate_subtitles", "subtitles_enabled", "enable_subtitles"):
        if key in data:
            generate_subtitles_value = data.get(key)
            break
    generate_subtitles = _truthy(generate_subtitles_value, default=False)

    gemini_url = (data.get("gemini_url") or DEFAULT_GEMINI_URL).strip()
    if gemini_url and not gemini_url.startswith(("http://", "https://")):
        return jsonify({"success": False, "message": "gemini_url 必须是 http(s) 地址"}), 400

    provider = workflow_status_payload()
    if source_mode == "ima_transcript":
        if len(transcript_text) < 20:
            return jsonify({"success": False, "message": "请粘贴 IMA 复制全文后再投递"}), 400
        if not provider.get("transcript_deepseek_configured"):
            return jsonify({"success": False, "message": "DS v4 flash 未配置，无法处理 IMA 转录全文", "data": {"provider": provider}}), 400
    elif source_mode == "ds_response":
        if len(ds_response_text) < 20:
            return jsonify({"success": False, "message": "请粘贴 DS 返回值后再投递"}), 400
    elif not provider.get("gemini_configured"):
        return jsonify({"success": False, "message": "Gemini 浏览器自动化未配置", "data": {"provider": provider}}), 400

    image_prompt = (data.get("image_prompt") or INFOGRAPHIC_PROMPT).strip()
    gemini_prompt = initial_gemini_prompt({"gemini_prompt": data.get("gemini_prompt")})
    transcript_prompt = initial_transcript_prompt({"transcript_prompt": data.get("transcript_prompt") or data.get("deepseek_prompt")})
    metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    uploaded_audio_path = None
    if audio_file:
        audio_asset_id = str(uuid.uuid4())
        try:
            audio = _save_audio_upload(audio_file, audio_asset_id)
        except ValueError as exc:
            return jsonify({"success": False, "message": str(exc)}), 400
        uploaded_audio_path = audio.get("path")
        if metadata.get("title"):
            audio["topic_title"] = str(metadata["title"]).strip()[:500]
    else:
        try:
            audio = audio_meta_from_asset_reference(audio_asset_id)
        except ValueError as exc:
            return jsonify({"success": False, "message": str(exc)}), 400
    source_feature, source_task_id = _audio_asset_source_identity(
        audio_asset_id,
        is_upload=bool(audio_file),
    )
    task = PublicationTask(
        status="pending",
        current_step="queued",
        progress=0,
        params={
            "feature": FEATURE_NAME,
            "user_id": user_id,
            "source_feature": source_feature,
            "source_task_id": source_task_id,
            FEATURE_NAME: {
                "audio_asset_id": audio_asset_id,
                "input_source": source_mode,
                "render_mode": render_mode,
                "generate_subtitles": generate_subtitles,
                "audio": audio,
                "gemini_url": gemini_url,
                "gemini_prompt": gemini_prompt,
                "image_prompt": image_prompt,
                "transcript_text": transcript_text if source_mode == "ima_transcript" else "",
                "transcript_prompt": transcript_prompt if source_mode == "ima_transcript" else "",
                "ds_response_text": ds_response_text if source_mode == "ds_response" else "",
                "metadata": metadata,
            },
        },
        result={
            "metadata": metadata,
            "audio": audio,
            "input_source": source_mode,
            "render_mode": render_mode,
            "generate_subtitles": generate_subtitles,
            "direct_ds_response_chars": len(ds_response_text) if source_mode == "ds_response" else 0,
            "source_feature": source_feature,
            "source_task_id": source_task_id,
            "children": {},
            "logs": [],
        },
    )
    try:
        db.session.add(task)
        db.session.commit()
    except Exception:
        db.session.rollback()
        if uploaded_audio_path:
            _remove_uploaded_audio(uploaded_audio_path)
        raise

    try:
        celery_task = apply_async_after_db_release(
            process_mp3_video_workflow_job,
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
        _clear_job_list_cache(user_id)
        return jsonify({"success": False, "message": task.error_message}), 502

    task.celery_task_id = celery_task.id
    db.session.commit()
    _clear_job_list_cache(user_id)
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
    submitted_on = str(request.args.get("submitted_on") or request.args.get("job_date") or "").strip()
    submitted_range = submission_date_utc_range(submitted_on)
    if not submitted_range:
        submitted_on = ""
    cache_key = _job_list_cache_key(
        current_user,
        page=page,
        per_page=per_page,
        status=status,
        search=search,
        submitted_on=submitted_on,
    )
    cached = _cached_job_list(cache_key)
    if cached is not None:
        return jsonify(cached)

    def load_page():
        _set_job_list_statement_timeout()
        query = PublicationTask.query.options(load_only(*JOB_LIST_COLUMNS)).filter(
            PublicationTask.feature == FEATURE_NAME,
            PublicationTask.user_id.in_(_visible_user_ids(current_user)),
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
                PublicationTask.source_task_id.ilike(pattern, escape="\\"),
                PublicationTask.id.ilike(pattern, escape="\\"),
            ))
        total = int(
            query.order_by(None)
            .with_entities(func.count(PublicationTask.id))
            .scalar()
            or 0
        )
        rows = []
        offset = (page - 1) * per_page
        if offset < total:
            rows = (
                query.order_by(PublicationTask.created_at.desc())
                .offset(offset)
                .limit(per_page)
                .all()
            )
        items = [_job_list_payload(task) for task in rows]
        return {
            "success": True,
            "data": {
                "items": items,
                "pagination": {
                    "page": page,
                    "per_page": per_page,
                    "total": total,
                    "pages": ((total + per_page - 1) // per_page) if total else 0,
                    "q": search,
                    "submitted_on": submitted_on,
                },
            },
            "items": items,
        }

    try:
        payload = _db_retry(
            load_page,
            "mp3 video workflow list jobs",
            attempts=1,
        )
        _store_job_list(cache_key, payload)
        return jsonify(payload)
    except SQLAlchemyError:
        db.session.rollback()
        stale = _cached_job_list(cache_key, allow_stale=True)
        if stale is not None:
            stale["stale"] = True
            stale["stale_reason"] = "database_busy"
            stale["data"]["stale"] = True
            return jsonify(stale)
        return jsonify({
            "success": False,
            "message": "数据库暂时繁忙，请稍后重试",
        }), 503


@mp3_video_workflow_bp.route("/jobs/<job_id>", methods=["GET"])
@require_login
def get_job(current_user, job_id):
    cache_key = _job_detail_cache_key(current_user, job_id)
    try:
        _set_job_detail_statement_timeout()
        reference = _get_owned_task(
            current_user,
            job_id,
            include_params=False,
            include_result=False,
            db_attempts=1,
        )
        if not reference:
            _clear_job_detail_cache(job_id, current_user=current_user)
            return jsonify({"success": False, "message": "任务不存在"}), 404

        version = _job_detail_version(reference)
        cached = _cached_job_detail(cache_key, version=version)
        if cached is not None:
            cached["bilibili_uploads"] = _bilibili_upload_children(reference.id)
            return jsonify({"success": True, **cached, "data": cached})

        task = _get_owned_task(current_user, job_id, db_attempts=1)
        if not task:
            return jsonify({"success": False, "message": "任务不存在"}), 404
        payload = _job_payload(task, include_raw=True)
        _store_job_detail(cache_key, _job_detail_version(task), payload)
        return jsonify({"success": True, **payload, "data": payload})
    except SQLAlchemyError:
        db.session.rollback()
        stale = _cached_job_detail(cache_key, allow_stale=True)
        if stale is not None:
            stale["stale"] = True
            stale["stale_reason"] = "database_busy"
            return jsonify({"success": True, "stale": True, **stale, "data": stale})
        return jsonify({
            "success": False,
            "message": "数据库暂时繁忙，请稍后重试",
        }), 503


@mp3_video_workflow_bp.route("/jobs/<job_id>/bilibili-uploads", methods=["GET"])
@require_login
def get_bilibili_uploads(current_user, job_id):
    task = _get_owned_task(
        current_user,
        job_id,
        include_params=False,
        include_result=False,
    )
    if not task:
        return jsonify({"success": False, "message": "任务不存在"}), 404
    items = _bilibili_upload_children(task.id)
    return jsonify({
        "success": True,
        "items": items,
        "data": {"items": items},
    })


def _task_json_text(column, *path):
    expression = column
    for key in path:
        expression = expression[key]
    return expression.as_string()


def _bilibili_upload_parent_snapshot(
    current_user,
    job_id,
    *,
    credential_id,
    requested_cover_key,
):
    effective_feature = func.coalesce(
        func.nullif(PublicationTask.feature, ""),
        _task_json_text(PublicationTask.params, "feature"),
    )
    effective_user_id = func.coalesce(
        func.nullif(PublicationTask.user_id, ""),
        _task_json_text(PublicationTask.params, "user_id"),
    )
    credential_name = func.coalesce(
        PlatformCredentialAccount.owner_label,
        PlatformCredentialAccount.name,
        PlatformCredentialAccount.masked_summary,
        PlatformCredentialAccount.id,
    )
    result = PublicationTask.result
    allowed_user_ids = _visible_user_ids(current_user)

    def build_query(*, legacy_projection):
        projected_user_id = (
            effective_user_id if legacy_projection else PublicationTask.user_id
        )
        query = (
            db.session.query(
                PublicationTask.id.label("id"),
                projected_user_id.label("user_id"),
                func.coalesce(
                    _task_json_text(result, "landscape_video", "video_path"),
                    _task_json_text(
                        result,
                        "landscape_video",
                        "landscape_video_path",
                    ),
                ).label("landscape_video_path"),
                func.coalesce(
                    _task_json_text(result, "vertical_video", "video_path"),
                    _task_json_text(
                        result,
                        "vertical_video",
                        "vertical_video_path",
                    ),
                ).label("vertical_video_path"),
                _task_json_text(result, "plan", "xiaojunjun_video_title").label(
                    "landscape_title"
                ),
                _task_json_text(result, "plan", "ciketc_video_title").label(
                    "vertical_title"
                ),
                _task_json_text(result, "plan", "title").label("plan_title"),
                _task_json_text(
                    result,
                    "artifacts",
                    requested_cover_key or "__none__",
                    "path",
                ).label("requested_cover_path"),
                _task_json_text(
                    result,
                    "artifacts",
                    "xiaojunjun_cover",
                    "path",
                ).label("xiaojunjun_cover_path"),
                _task_json_text(
                    result,
                    "artifacts",
                    "finance_cover",
                    "path",
                ).label("finance_cover_path"),
                _task_json_text(
                    result,
                    "artifacts",
                    "xiaojunjun_background",
                    "path",
                ).label("xiaojunjun_background_path"),
                _task_json_text(
                    result,
                    "artifacts",
                    "podcast_cover",
                    "path",
                ).label("podcast_cover_path"),
                _task_json_text(
                    result,
                    "artifacts",
                    "vertical_cover",
                    "path",
                ).label("vertical_cover_path"),
                _task_json_text(
                    result,
                    "artifacts",
                    "audio_background",
                    "path",
                ).label("audio_background_path"),
                credential_name.label("credential_name"),
            )
            .outerjoin(
                PlatformCredentialAccount,
                PlatformCredentialAccount.id == str(credential_id or ""),
            )
            .filter(PublicationTask.id == str(job_id))
        )
        if legacy_projection:
            return query.filter(effective_feature == FEATURE_NAME).filter(
                effective_user_id.in_(allowed_user_ids)
            )
        return query.filter(PublicationTask.feature == FEATURE_NAME).filter(
            PublicationTask.user_id.in_(allowed_user_ids)
        )

    def load_projected_snapshot():
        return build_query(legacy_projection=False).first()

    row = _db_retry(
        load_projected_snapshot,
        f"mp3 video workflow get Bilibili upload parent {job_id}",
        attempts=3,
    )
    if row is None:

        def load_legacy_snapshot():
            return build_query(legacy_projection=True).first()

        row = _db_retry(
            load_legacy_snapshot,
            f"mp3 video workflow get legacy Bilibili upload parent {job_id}",
            attempts=3,
        )
    if row is None:
        return None

    artifacts = {
        "xiaojunjun_cover": {"path": row.xiaojunjun_cover_path},
        "finance_cover": {"path": row.finance_cover_path},
        "xiaojunjun_background": {"path": row.xiaojunjun_background_path},
        "podcast_cover": {"path": row.podcast_cover_path},
        "vertical_cover": {"path": row.vertical_cover_path},
        "audio_background": {"path": row.audio_background_path},
    }
    if requested_cover_key:
        artifacts[str(requested_cover_key)] = {"path": row.requested_cover_path}
    return SimpleNamespace(
        id=str(row.id),
        user_id=str(row.user_id or ""),
        credential_name=row.credential_name,
        result={
            "plan": {
                "xiaojunjun_video_title": row.landscape_title,
                "ciketc_video_title": row.vertical_title,
                "title": row.plan_title,
            },
            "landscape_video": {"video_path": row.landscape_video_path},
            "vertical_video": {"video_path": row.vertical_video_path},
            "artifacts": artifacts,
        },
    )


def _bilibili_upload_fallback_response_text(job_id):
    result = PublicationTask.result

    def load_fallback_text():
        return (
            db.session.query(
                _task_json_text(result, "source", "response_text").label("source_response"),
                _task_json_text(result, "source", "initial_response_text").label(
                    "source_initial"
                ),
                _task_json_text(result, "source", "final_response_text").label(
                    "source_final"
                ),
                _task_json_text(result, "initial_response_text").label("initial_response"),
                _task_json_text(result, "gemini", "response_text").label("gemini_response"),
                _task_json_text(result, "gemini", "initial_response_text").label(
                    "gemini_initial"
                ),
                _task_json_text(result, "gemini", "final_response_text").label(
                    "gemini_final"
                ),
            )
            .filter(PublicationTask.id == str(job_id))
            .first()
        )

    row = _db_retry(
        load_fallback_text,
        f"mp3 video workflow get Bilibili title fallback {job_id}",
        attempts=3,
    )
    if row is None:
        return ""
    return next(
        (
            str(value).strip()
            for value in row
            if str(value or "").strip()
        ),
        "",
    )


@mp3_video_workflow_bp.route("/jobs/<job_id>/bilibili-upload", methods=["POST"])
@require_login
def submit_bilibili_upload(current_user, job_id):
    data = _request_payload()
    credential_id = _clean_upload_text(
        data.get("credential_id") or data.get("platform_credential_id"),
        max_length=80,
    )
    cover_key = str(data.get("cover_key") or "").strip()
    task = _bilibili_upload_parent_snapshot(
        current_user,
        job_id,
        credential_id=credential_id,
        requested_cover_key=cover_key,
    )
    release_db_session_quietly()
    if not task:
        return jsonify({"success": False, "message": "任务不存在"}), 404

    video_key = str(data.get("video_key") or data.get("video") or "landscape").strip().lower()
    if video_key not in {"landscape", "vertical"}:
        return jsonify({"success": False, "message": "video_key 只能是 landscape 或 vertical"}), 400

    video = _video_by_key(task, video_key)
    video_path = _video_output_path(video)
    if not _video_file_ready(video_path):
        return jsonify({"success": False, "message": "视频文件损坏或尚未完成，请重新生成后再上传"}), 400

    result = task.result or {}
    ds = _ds_extract_payload(result)
    manual_title = _clean_bilibili_upload_title(data.get("title"))
    title = manual_title or _default_bilibili_title(result, ds, video_key)
    if not title and not manual_title:
        fallback_text = _bilibili_upload_fallback_response_text(job_id)
        if fallback_text:
            result = {**result, "source": {"response_text": fallback_text}}
            ds = _ds_extract_payload(result)
            title = _default_bilibili_title(result, ds, video_key)
    release_db_session_quietly()
    if not title:
        return jsonify({"success": False, "message": "B站标题不能为空"}), 400

    cover_path, resolved_cover_key = _resolve_bilibili_cover(result, video_key, cover_key)
    if cover_key and not cover_path:
        return jsonify({"success": False, "message": f"封面产物不存在：{cover_key}"}), 400

    try:
        child = create_bilibili_upload_task(
            task,
            current_user=current_user,
            parent_feature=FEATURE_NAME,
            source_label=f"{FEATURE_NAME}:bilibili_upload",
            video_path=video_path,
            video_key=video_key,
            title=title,
            data=data,
            cover_path=cover_path,
            cover_key=resolved_cover_key,
            default_tags=["财经", "音频", "内资闭门会"],
            metadata_extra={
                "title_source": (
                    "manual"
                    if _clean_upload_text(data.get("title"))
                    else ("ds_6" if video_key == "landscape" else "ds_7")
                ),
            },
            credential_name=task.credential_name,
        )
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400

    try:
        enqueue_bilibili_upload_task(child)
    except Exception as exc:
        return jsonify({
            "success": False,
            "message": f"B站上传任务入队失败: {exc}",
        }), 502

    _clear_job_detail_cache(job_id)
    item = getattr(child, "_bilibili_enqueue_response_payload", None)
    return jsonify({
        "success": True,
        "message": "已投递 B站上传任务",
        "data": {"upload_task": item},
        "upload_task": item,
    })


@mp3_video_workflow_bp.route("/jobs/<job_id>/bilibili-upload/<upload_task_id>/cancel", methods=["POST"])
@require_login
def cancel_bilibili_upload(current_user, job_id, upload_task_id):
    task = _get_owned_task(
        current_user,
        job_id,
        include_params=False,
        include_result=False,
    )
    if not task:
        return jsonify({"success": False, "message": "任务不存在"}), 404

    source_label = f"{FEATURE_NAME}:bilibili_upload"
    row = shared_bilibili_upload_action_row(
        upload_task_id,
        parent_task_id=task.id,
        parent_feature=FEATURE_NAME,
        source_label=source_label,
        for_update=True,
    )
    if row is None:
        return jsonify({"success": False, "message": "B站上传任务不存在"}), 404
    child, upload = row
    item = shared_bilibili_upload_child_payload(
        child,
        source_label=source_label,
        upload=upload,
    )

    ok, message = shared_cancel_bilibili_upload_child(child, upload=upload)
    if ok:
        _clear_job_detail_cache(job_id)
    item = getattr(child, "_bilibili_cancel_response_payload", None) or item
    status_code = 200 if ok else 409
    return jsonify({
        "success": ok,
        "message": message,
        "data": {"upload_task": item},
        "upload_task": item,
    }), status_code


@mp3_video_workflow_bp.route("/jobs/<job_id>/douyin-uploads", methods=["GET"])
@require_login
def get_douyin_uploads(current_user, job_id):
    task = _get_owned_task(current_user, job_id, include_params=False, include_result=False)
    if not task:
        return jsonify({"success": False, "message": "任务不存在"}), 404
    items = douyin_publish_children(task.id, parent_feature=FEATURE_NAME)
    return jsonify({"success": True, "data": {"items": items}, "items": items})


@mp3_video_workflow_bp.route("/jobs/<job_id>/douyin-upload", methods=["POST"])
@require_login
def submit_douyin_upload(current_user, job_id):
    task = _get_owned_task(current_user, job_id)
    if not task:
        return jsonify({"success": False, "message": "任务不存在"}), 404
    data = _request_payload()
    video_key = str(data.get("video_key") or "vertical").strip().lower()
    if video_key not in {"landscape", "vertical"}:
        return jsonify({"success": False, "message": "video_key 只能是 landscape 或 vertical"}), 400
    video = _video_by_key(task, video_key)
    video_path = _video_output_path(video)
    if not _video_file_ready(video_path):
        return jsonify({"success": False, "message": "视频文件损坏或尚未完成，请重新生成后再上传"}), 400
    submit = _truthy(data.get("submit"), default=False)
    if submit and data.get("publish_confirmation") != "PUBLISH":
        return jsonify({"success": False, "message": "确认发布校验失败，请重新勾选"}), 400
    result = task.result or {}
    ds = _ds_extract_payload(result)
    title = _clean_upload_text(data.get("title"), max_length=100) or _default_bilibili_title(result, ds, video_key)[:100]
    try:
        child = create_douyin_publish_task(
            current_user=current_user,
            parent_task=task,
            parent_feature=FEATURE_NAME,
            source_label=f"{FEATURE_NAME}:douyin_upload",
            video_path=video_path,
            video_key=video_key,
            title=title,
            description=data.get("description") or data.get("desc") or "",
            tags=data.get("tags") or ["财经", "音频", "内资闭门会"],
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
    _clear_job_detail_cache(job_id)
    return jsonify({
        "success": True,
        "message": "抖音上传任务已入队；发布成功以作品管理页回查为准",
        "data": {"upload_task": item},
        "upload_task": item,
    })


@mp3_video_workflow_bp.route("/jobs/<job_id>/douyin-upload/<upload_task_id>/cancel", methods=["POST"])
@require_login
def cancel_douyin_upload(current_user, job_id, upload_task_id):
    parent = _get_owned_task(current_user, job_id, include_params=False, include_result=False)
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
    item = getattr(child, "_browser_publish_cancel_response_payload", None)
    if item is None:
        item = douyin_publish_payload(child)
    if ok:
        _clear_job_detail_cache(job_id)
    return jsonify({"success": ok, "message": message, "data": {"upload_task": item}, "upload_task": item}), (200 if ok else 409)


@mp3_video_workflow_bp.route("/jobs/<job_id>/wechat-channels-uploads", methods=["GET"])
@require_login
def get_wechat_channels_uploads(current_user, job_id):
    task = _get_owned_task(current_user, job_id, include_params=False, include_result=False)
    if not task:
        return jsonify({"success": False, "message": "任务不存在"}), 404
    items = wechat_channels_publish_children(task.id, parent_feature=FEATURE_NAME)
    return jsonify({"success": True, "data": {"items": items}, "items": items})


@mp3_video_workflow_bp.route("/jobs/<job_id>/wechat-channels-upload", methods=["POST"])
@require_login
def submit_wechat_channels_upload(current_user, job_id):
    task = _get_owned_task(current_user, job_id)
    if not task:
        return jsonify({"success": False, "message": "任务不存在"}), 404
    data = _request_payload()
    video_key = str(data.get("video_key") or "vertical").strip().lower()
    if video_key not in {"landscape", "vertical"}:
        return jsonify({"success": False, "message": "video_key 只能是 landscape 或 vertical"}), 400
    video = _video_by_key(task, video_key)
    video_path = _video_output_path(video)
    if not _video_file_ready(video_path):
        return jsonify({"success": False, "message": "视频文件损坏或尚未完成，请重新生成后再上传"}), 400
    submit = _truthy(data.get("submit"), default=False)
    if submit and data.get("publish_confirmation") != "PUBLISH":
        return jsonify({"success": False, "message": "确认发布校验失败，请重新勾选"}), 400
    result = task.result or {}
    ds = _ds_extract_payload(result)
    title = _clean_upload_text(data.get("title"), max_length=100) or _default_bilibili_title(result, ds, video_key)[:100]
    try:
        child = create_wechat_channels_publish_task(
            current_user=current_user, parent_task=task, parent_feature=FEATURE_NAME,
            source_label=f"{FEATURE_NAME}:wechat_channels_upload", video_path=video_path,
            video_key=video_key, title=title,
            description=data.get("description") or data.get("desc") or "",
            tags=data.get("tags") or ["财经", "音频", "内资闭门会"],
            credential_id=data.get("credential_id"),
            visibility=data.get("visibility") or "private", submit=submit,
        )
        enqueue_wechat_channels_publish_task(child)
        item = wechat_channels_publish_payload(child)
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    except RuntimeError as exc:
        return jsonify({"success": False, "message": str(exc)}), 502
    _clear_job_detail_cache(job_id)
    return jsonify({"success": True, "message": "已投递视频号上传任务", "data": {"upload_task": item}, "upload_task": item})


@mp3_video_workflow_bp.route("/jobs/<job_id>/wechat-channels-upload/<upload_task_id>/cancel", methods=["POST"])
@require_login
def cancel_wechat_channels_upload(current_user, job_id, upload_task_id):
    parent = _get_owned_task(current_user, job_id, include_params=False, include_result=False)
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
    item = getattr(child, "_browser_publish_cancel_response_payload", None)
    if item is None:
        item = wechat_channels_publish_payload(child)
    if ok:
        _clear_job_detail_cache(job_id)
    return jsonify({"success": ok, "message": message, "data": {"upload_task": item}, "upload_task": item}), (200 if ok else 409)


@mp3_video_workflow_bp.route("/jobs/<job_id>/cancel", methods=["POST"])
@require_login
def cancel_job(current_user, job_id):
    task = _get_owned_task(current_user, job_id)
    if not task:
        return jsonify({"success": False, "message": "任务不存在"}), 404
    if task.status in {"completed", "failed", "canceled"}:
        payload = _job_payload(task)
        return jsonify({"success": True, "message": "任务已结束", **payload, "data": payload})
    task_id = str(task.id)
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
    _clear_job_detail_cache(task_id)
    revoked = revoke_celery_task_after_db_commit(
        celery_task_id,
        operation=f"mp3 video workflow cancel revoke task={task_id}",
        business_task_id=task_id,
    )
    message = "任务已取消"
    if celery_task_id and not revoked:
        message += "；broker 撤回失败，终态门禁将拦截旧消息"
    return jsonify({"success": True, "message": message, **payload, "data": payload})


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
    payload, enqueue_error = _commit_and_enqueue_prepared_job(
        task,
        enqueue_error_prefix="续跑任务入队失败",
    )
    if enqueue_error:
        return jsonify({"success": False, "message": enqueue_error}), 502
    return jsonify({"success": True, "message": "任务已从失败处续跑", **payload, "data": payload})


@mp3_video_workflow_bp.route("/jobs/<job_id>/continue-full-video", methods=["POST"])
@require_login
def continue_full_video(current_user, job_id):
    task = _get_owned_task(current_user, job_id)
    if not task:
        return jsonify({"success": False, "message": "任务不存在"}), 404
    if task.status in {"pending", "processing"}:
        payload = _job_payload(task)
        return jsonify({"success": True, "message": "任务仍在运行中", **payload, "data": payload})

    ok, message = _prepare_keyframe_job_for_full_video(task, _request_payload())
    if not ok:
        payload = _job_payload(task)
        return jsonify({"success": False, "message": message, **payload, "data": payload}), 400

    payload, enqueue_error = _commit_and_enqueue_prepared_job(
        task,
        enqueue_error_prefix="完整视频续跑任务入队失败",
    )
    if enqueue_error:
        return jsonify({"success": False, "message": enqueue_error}), 502
    return jsonify({"success": True, "message": "已从关键帧继续生成完整视频", **payload, "data": payload})


@mp3_video_workflow_bp.route("/jobs/<job_id>/regenerate-videos", methods=["POST"])
@require_login
def regenerate_videos(current_user, job_id):
    task = _get_owned_task(current_user, job_id)
    if not task:
        return jsonify({"success": False, "message": "任务不存在"}), 404
    if task.status in {"pending", "processing"}:
        payload = _job_payload(task)
        return jsonify({"success": False, "message": "任务仍在运行中，请先等待或取消后再重生成", **payload, "data": payload}), 409

    payload = request.get_json(silent=True) or {}
    ok, message = _prepare_job_for_video_regeneration(task, payload)
    if not ok:
        payload = _job_payload(task)
        return jsonify({"success": False, "message": message, **payload, "data": payload}), 400

    payload, enqueue_error = _commit_and_enqueue_prepared_job(
        task,
        enqueue_error_prefix="视频重生成任务入队失败",
    )
    if enqueue_error:
        return jsonify({"success": False, "message": enqueue_error}), 502
    plan_audio = (payload.get("plan") or {}).get("audio") or {}
    range_text = (
        f"（{_format_audio_time(plan_audio.get('start_seconds'))}"
        f"-{_format_audio_time(plan_audio.get('end_seconds'))}，"
        f"{plan_audio.get('speed') or 1.2}×）"
    )
    return jsonify({
        "success": True,
        "message": f"已按新设置重新投递横屏和竖屏视频生成任务{range_text}",
        **payload,
        "data": payload,
    })


@mp3_video_workflow_bp.route("/jobs/<job_id>/artifacts/<artifact_key>/regenerate", methods=["POST"])
@require_login
def regenerate_artifact(current_user, job_id, artifact_key):
    key = str(artifact_key or "").strip()
    if key not in VISUAL_ARTIFACT_KEYS:
        return jsonify({"success": False, "message": "这个产物不支持重做封皮/背景图"}), 400

    task = _get_owned_task(current_user, job_id)
    if not task:
        release_db_session_quietly()
        return jsonify({"success": False, "message": "任务不存在"}), 404

    expected_updated_at = task.updated_at
    expected_status = str(task.status or "")
    task_snapshot = _detached_job_payload_snapshot(task)
    result = task_snapshot.result
    assert_db_session_clean("MP3 visual artifact regeneration")
    release_db_session_quietly()

    if expected_status not in {"completed", "failed", "canceled"}:
        payload = _job_payload(task_snapshot)
        release_db_session_quietly()
        return jsonify({"success": False, "message": "任务正在运行中，请等本轮结束后再重做封皮", **payload, "data": payload}), 409

    data = _request_payload()
    title_text = data.get("title_text") or data.get("title") or data.get("text") or ""
    plan = result.get("plan") if isinstance(result.get("plan"), dict) else {}
    if not plan:
        return jsonify({"success": False, "message": "当前任务缺少 DS/规划结果，不能重做封皮"}), 400

    infographic = result.get("infographic") if isinstance(result.get("infographic"), dict) else {}
    bilibili_uploads = _bilibili_upload_children(task_snapshot.id)
    release_db_session_quietly()
    try:
        new_artifact = regenerate_visual_artifact(
            task_snapshot.id,
            key,
            plan=plan,
            title_text=title_text,
            infographic_path=infographic.get("path"),
            source_text=(
                result.get("initial_response_text")
                or result.get("source_response_text")
                or (result.get("source") or {}).get("initial_response_text")
                or (result.get("source") or {}).get("response_text")
                or ""
            ),
            audio=result.get("audio") if isinstance(result.get("audio"), dict) else {},
            created_at=task_snapshot.created_at,
        )
    except Mp3VideoWorkflowError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    except Exception as exc:
        return jsonify({"success": False, "message": f"重做封皮失败: {exc}"}), 500

    artifacts = dict(result.get("artifacts") if isinstance(result.get("artifacts"), dict) else {})
    old_artifact = artifacts.get(key) if isinstance(artifacts.get(key), dict) else {}
    history = list(result.get("artifact_regeneration_history") or [])
    now = datetime.utcnow().isoformat() + "Z"
    if old_artifact:
        history.append({
            "artifact_key": key,
            "replaced_at": now,
            "old_artifact": old_artifact,
            "new_artifact": new_artifact,
            "title_text": new_artifact.get("manual_title_text") or title_text,
        })
    artifacts[key] = new_artifact
    result["artifacts"] = artifacts
    if key == "audio_background" and isinstance(new_artifact.get("source_infographic"), dict):
        result["infographic"] = new_artifact.get("source_infographic")
    result["artifact_regeneration_history"] = history[-20:]
    logs = list(result.get("logs") or [])
    logs.append({
        "step": "artifact_regenerated",
        "message": f"用户手动重做封皮/背景图：{key}",
        "progress": task_snapshot.progress or 100,
        "updated_at": now,
    })
    result["logs"] = logs[-160:]

    written_at = datetime.utcnow()
    update_query = PublicationTask.query.filter(
        PublicationTask.id == task_snapshot.id,
        PublicationTask.status == expected_status,
    )
    if expected_updated_at is None:
        update_query = update_query.filter(PublicationTask.updated_at.is_(None))
    else:
        update_query = update_query.filter(PublicationTask.updated_at == expected_updated_at)
    updated = update_query.update(
        {
            PublicationTask.result: result,
            PublicationTask.updated_at: written_at,
        },
        synchronize_session=False,
    )
    if updated != 1:
        db.session.rollback()
        release_db_session_quietly()
        return jsonify({
            "success": False,
            "message": "任务状态已变化，本次新图未覆盖最新任务结果；请刷新后重试",
        }), 409
    db.session.commit()
    release_db_session_quietly()

    _write_result_json_if_present(result)
    _clear_download_result_cache(task_snapshot.id)

    task_snapshot.result = result
    task_snapshot.updated_at = written_at
    payload = _job_payload(
        task_snapshot,
        include_raw=True,
        bilibili_uploads=bilibili_uploads,
    )
    _store_job_detail(
        _job_detail_cache_key(current_user, task_snapshot.id),
        _job_detail_version(task_snapshot),
        payload,
    )
    return jsonify({"success": True, "message": "封皮已重做并替换，重新生成视频时会使用新版图片", **payload, "data": payload})


@mp3_video_workflow_bp.route("/jobs/<job_id>/download-result", methods=["GET"])
@require_login
def download_result(current_user, job_id):
    result = _download_result_for_owned_task(current_user, job_id)
    if result is None:
        return jsonify({"success": False, "message": "任务不存在"}), 404
    output_path = result.get("output_path")
    if not output_path or not os.path.exists(output_path):
        return jsonify({"success": False, "message": "结果文件不存在"}), 404
    return send_file(output_path, mimetype="application/json", as_attachment=True, download_name=f"{job_id}.json")


@mp3_video_workflow_bp.route("/jobs/<job_id>/download-source", methods=["GET"])
@require_login
def download_source_result(current_user, job_id):
    result = _download_result_for_owned_task(current_user, job_id)
    if result is None:
        return jsonify({"success": False, "message": "任务不存在"}), 404
    source = result.get("source") if isinstance(result.get("source"), dict) else {}
    gemini = result.get("gemini") if isinstance(result.get("gemini"), dict) else {}
    output_path = source.get("output_path") or gemini.get("output_path")
    if not output_path or not os.path.exists(output_path):
        return jsonify({"success": False, "message": "DS/Gemini 源结果文件不存在"}), 404
    return send_file(output_path, mimetype="application/json", as_attachment=True, download_name=f"{job_id}-source.json")


@mp3_video_workflow_bp.route("/jobs/<job_id>/input-audio", methods=["GET"])
@require_login
def download_input_audio(current_user, job_id):
    result = _download_result_for_owned_task(current_user, job_id)
    if result is None:
        return jsonify({"success": False, "message": "任务不存在"}), 404
    audio = result.get("audio") or {}
    path = audio.get("path")
    if not path or not os.path.exists(path):
        return jsonify({"success": False, "message": "音频文件不存在"}), 404
    mimetype = mimetypes.guess_type(path)[0] or "audio/mpeg"
    return send_file(path, mimetype=mimetype, as_attachment=True, download_name=audio.get("filename") or f"{job_id}.mp3")


@mp3_video_workflow_bp.route("/jobs/<job_id>/download-artifact/<artifact_key>", methods=["GET"])
@require_login
def download_artifact(current_user, job_id, artifact_key):
    result = _download_result_for_owned_task(current_user, job_id)
    if result is None:
        return jsonify({"success": False, "message": "任务不存在"}), 404
    artifact = _artifact_by_key_from_result(result, artifact_key)
    if not artifact:
        return jsonify({"success": False, "message": "产物不存在"}), 404
    path = artifact.get("path")
    if not path or not os.path.exists(path):
        return jsonify({"success": False, "message": "产物文件不存在"}), 404
    mimetype = mimetypes.guess_type(path)[0] or "image/jpeg"
    suffix = Path(path).suffix or ".jpg"
    return send_file(path, mimetype=mimetype, as_attachment=True, download_name=f"{job_id}-{artifact_key}{suffix}")


@mp3_video_workflow_bp.route("/jobs/<job_id>/download-video/<video_key>", methods=["GET"])
@require_login
def download_video(current_user, job_id, video_key):
    result = _download_result_for_owned_task(current_user, job_id)
    if result is None:
        return jsonify({"success": False, "message": "任务不存在"}), 404
    video = _video_by_key_from_result(result, video_key)
    if not video:
        return jsonify({"success": False, "message": "视频不存在"}), 404
    path = video.get("video_path") or video.get("landscape_video_path") or video.get("vertical_video_path")
    if not _video_file_ready(path):
        return jsonify({"success": False, "message": "视频文件损坏或尚未完成，请重新生成"}), 409
    return send_file(path, mimetype="video/mp4", as_attachment=True, download_name=f"{job_id}-{video_key}.mp4")


def _detached_job_payload_snapshot(task):
    """Freeze the response fields before releasing the request's DB checkout."""
    return SimpleNamespace(
        id=str(task.id),
        celery_task_id=task.celery_task_id,
        status=str(task.status or ""),
        current_step=task.current_step,
        progress=task.progress,
        error_message=task.error_message,
        feature=task.feature,
        user_id=task.user_id,
        created_at=task.created_at,
        updated_at=task.updated_at,
        completed_at=task.completed_at,
        params=copy.deepcopy(task.params) if isinstance(task.params, dict) else {},
        result=copy.deepcopy(task.result) if isinstance(task.result, dict) else {},
    )


def _commit_and_enqueue_prepared_job(task, *, enqueue_error_prefix):
    """Commit one prepared MP3 job without carrying an expired ORM into broker I/O.

    The response snapshot and child summaries are frozen before the first commit.
    Broker publication therefore receives only the stable task ID, and the returned
    Celery ID is persisted with a narrow bulk update instead of reloading the wide
    ``params/result`` row after each commit.
    """
    task_snapshot = _detached_job_payload_snapshot(task)
    task_id = task_snapshot.id
    bilibili_uploads = _bilibili_upload_children(task_id)
    db.session.commit()
    release_db_session_quietly()
    assert_db_session_clean("MP3 workflow broker publish")

    try:
        celery_task = apply_async_after_db_release(
            process_mp3_video_workflow_job,
            args=[task_id],
            queue=QUEUE_NAME,
            retry=False,
        )
    except Exception as exc:
        error_message = f"{enqueue_error_prefix}: {exc}"
        failed_at = datetime.utcnow()
        try:
            PublicationTask.query.filter(
                PublicationTask.id == task_id,
            ).update(
                {
                    PublicationTask.status: "failed",
                    PublicationTask.current_step: "failed",
                    PublicationTask.error_message: error_message,
                    PublicationTask.completed_at: failed_at,
                },
                synchronize_session=False,
            )
            db.session.commit()
        finally:
            release_db_session_quietly()
        return None, error_message

    try:
        PublicationTask.query.filter(
            PublicationTask.id == task_id,
        ).update(
            {PublicationTask.celery_task_id: celery_task.id},
            synchronize_session=False,
        )
        db.session.commit()
    finally:
        release_db_session_quietly()

    task_snapshot.celery_task_id = celery_task.id
    _clear_job_detail_cache(task_id)
    payload = _job_payload(
        task_snapshot,
        bilibili_uploads=bilibili_uploads,
    )
    return payload, None


def _get_owned_task(
    current_user,
    job_id,
    *,
    include_params=True,
    include_result=True,
    db_attempts=3,
    visible_user_ids=None,
):
    def load_task():
        columns = [
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
        ]
        if include_params:
            columns.append(PublicationTask.params)
        if include_result:
            columns.append(PublicationTask.result)
        return PublicationTask.query.options(
            load_only(*columns)
        ).filter(PublicationTask.id == str(job_id)).first()

    task = _db_retry(
        load_task,
        f"mp3 video workflow get job {job_id}",
        attempts=max(1, int(db_attempts or 1)),
    )
    if task and not include_params and (not task.feature or not task.user_id):
        return _get_owned_task(
            current_user,
            job_id,
            include_params=True,
            include_result=include_result,
            db_attempts=db_attempts,
            visible_user_ids=visible_user_ids,
        )
    params = task.params if task and include_params and isinstance(task.params, dict) else {}
    if not task or (task.feature or params.get("feature")) != FEATURE_NAME:
        return None
    task_user_id = str(task.user_id or params.get("user_id") or "").strip()
    allowed_user_ids = visible_user_ids if visible_user_ids is not None else _visible_user_ids(current_user)
    if task_user_id not in allowed_user_ids:
        return None
    return task


def _job_detail_cache_key(current_user, job_id):
    return (
        str(getattr(current_user, "id", "") or "").strip(),
        str(job_id or "").strip(),
    )


def _job_list_cache_key(current_user, *, page, per_page, status, search, submitted_on):
    return (
        "+".join(sorted(_visible_user_ids(current_user))),
        int(page),
        int(per_page),
        str(status or ""),
        str(search or ""),
        str(submitted_on or ""),
    )


def _cached_job_list(cache_key, *, allow_stale=False):
    now = time.monotonic()
    with _job_list_cache_lock:
        cached = _job_list_cache.get(cache_key)
        if not cached:
            return None
        deadline = cached["stale_until"] if allow_stale else cached["expires_at"]
        if deadline <= now:
            _job_list_cache.pop(cache_key, None)
            return None
        return copy.deepcopy(cached["payload"])


def _store_job_list(cache_key, payload):
    if JOB_LIST_CACHE_TTL_SECONDS <= 0:
        return
    now = time.monotonic()
    with _job_list_cache_lock:
        _job_list_cache[cache_key] = {
            "payload": copy.deepcopy(payload),
            "expires_at": now + JOB_LIST_CACHE_TTL_SECONDS,
            "stale_until": now + JOB_LIST_STALE_MAX_SECONDS,
        }
        if len(_job_list_cache) > DOWNLOAD_RESULT_CACHE_MAX_SIZE:
            oldest = min(
                _job_list_cache,
                key=lambda key: _job_list_cache[key]["stale_until"],
            )
            _job_list_cache.pop(oldest, None)


def _clear_job_list_cache(user_id=None):
    user_id = str(user_id or "").strip()
    with _job_list_cache_lock:
        if not user_id:
            _job_list_cache.clear()
            return
        for key in list(_job_list_cache):
            if user_id in str(key[0]).split("+"):
                _job_list_cache.pop(key, None)


def _job_detail_version(task):
    updated_at = getattr(task, "updated_at", None)
    return updated_at.isoformat() if updated_at else ":".join((
        str(getattr(task, "status", "") or ""),
        str(getattr(task, "current_step", "") or ""),
        str(getattr(task, "progress", 0) or 0),
    ))


def _cached_job_detail(cache_key, *, version=None, allow_stale=False):
    if not cache_key[0] or not cache_key[1]:
        return None
    now = time.monotonic()
    with _job_detail_cache_lock:
        cached = _job_detail_cache.get(cache_key)
        if not cached:
            return None
        if version is not None and cached["version"] != version:
            return None
        deadline = cached["stale_until"] if allow_stale or version is not None else cached["expires_at"]
        if deadline <= now:
            _job_detail_cache.pop(cache_key, None)
            return None
        return copy.deepcopy(cached["payload"])


def _store_job_detail(cache_key, version, payload):
    if JOB_DETAIL_CACHE_TTL_SECONDS <= 0 or not cache_key[0] or not cache_key[1]:
        return
    now = time.monotonic()
    with _job_detail_cache_lock:
        _job_detail_cache[cache_key] = {
            "version": version,
            "payload": copy.deepcopy(payload),
            "expires_at": now + JOB_DETAIL_CACHE_TTL_SECONDS,
            "stale_until": now + JOB_DETAIL_STALE_MAX_SECONDS,
        }
        if len(_job_detail_cache) > DOWNLOAD_RESULT_CACHE_MAX_SIZE:
            oldest = min(
                _job_detail_cache,
                key=lambda key: _job_detail_cache[key]["stale_until"],
            )
            _job_detail_cache.pop(oldest, None)


def _clear_job_detail_cache(job_id, *, current_user=None):
    job_key = str(job_id or "").strip()
    with _job_detail_cache_lock:
        if current_user is not None:
            _job_detail_cache.pop(_job_detail_cache_key(current_user, job_key), None)
            return
        for key in [key for key in _job_detail_cache if key[1] == job_key]:
            _job_detail_cache.pop(key, None)


def _set_job_detail_statement_timeout():
    bind = db.session.get_bind()
    if bind is None or bind.dialect.name != "postgresql":
        return
    db.session.execute(text(f"SET LOCAL statement_timeout = {JOB_DETAIL_STATEMENT_TIMEOUT_MS}"))


def _set_job_list_statement_timeout():
    _set_job_detail_statement_timeout()


def _download_result_for_owned_task(current_user, job_id):
    cache_key = str(job_id or "").strip()
    if not cache_key:
        return None
    visible_user_ids = _visible_user_ids(current_user)
    # Authentication may have opened a read transaction. Media requests can
    # wait on this cache lock and then stream a file for seconds, neither of
    # which may retain that checkout.
    release_db_session_quietly()
    with _download_result_cache_lock:
        now = time.monotonic()
        cached = _download_result_cache.get(cache_key)
        if cached and cached["expires_at"] > now and cached["user_id"] in visible_user_ids:
            return cached["result"]

        try:
            task = _get_owned_task(
                current_user,
                cache_key,
                include_params=False,
                include_result=True,
                visible_user_ids=visible_user_ids,
            )
            if not task:
                _download_result_cache.pop(cache_key, None)
                return None
            result = copy.deepcopy(task.result) if isinstance(task.result, dict) else {}
            owner_user_id = str(task.user_id or "").strip()
            if DOWNLOAD_RESULT_CACHE_TTL_SECONDS > 0 and owner_user_id:
                _download_result_cache[cache_key] = {
                    "expires_at": now + DOWNLOAD_RESULT_CACHE_TTL_SECONDS,
                    "user_id": owner_user_id,
                    "result": result,
                }
                _trim_download_result_cache(now)
            return result
        finally:
            release_db_session_quietly()


def _trim_download_result_cache(now=None):
    now = now or time.monotonic()
    expired = [key for key, value in _download_result_cache.items() if value["expires_at"] <= now]
    for key in expired:
        _download_result_cache.pop(key, None)
    overflow = len(_download_result_cache) - DOWNLOAD_RESULT_CACHE_MAX_SIZE
    if overflow <= 0:
        return
    for key, _value in sorted(_download_result_cache.items(), key=lambda item: item[1]["expires_at"])[:overflow]:
        _download_result_cache.pop(key, None)


def _clear_download_result_cache(job_id):
    with _download_result_cache_lock:
        _download_result_cache.pop(str(job_id or "").strip(), None)
    _clear_job_detail_cache(job_id)


def _write_result_json_if_present(result):
    output_path = result.get("output_path") if isinstance(result, dict) else None
    if not _path_exists(output_path):
        return
    try:
        Path(output_path).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


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
    ids = list(dict.fromkeys(str(item or "").strip() for item in asset_ids if str(item or "").strip()))
    if not ids:
        return {}

    stats = {asset_id: _empty_audio_asset_workflow_stats() for asset_id in ids}
    asset_id_by_source_identity = {}
    ambiguous_source_identities = set()
    for asset_id in ids:
        source_identities = [(WORKFLOW_SOURCE_FEATURE, asset_id)]
        if asset_id.startswith("pipeline:"):
            normalized_asset_id = asset_id.split(":", 1)[1].strip()
            source_identities = [
                (CONTENT_PIPELINE_ASSET_SOURCE_FEATURE, normalized_asset_id),
                (WORKFLOW_SOURCE_FEATURE, asset_id[:36]),
            ]
        for source_identity in source_identities:
            _source_feature, source_key = source_identity
            if not source_key:
                continue
            existing = asset_id_by_source_identity.get(source_identity)
            if existing and existing != asset_id:
                ambiguous_source_identities.add(source_identity)
                continue
            asset_id_by_source_identity[source_identity] = asset_id
    for source_identity in ambiguous_source_identities:
        asset_id_by_source_identity.pop(source_identity, None)
    source_keys = list(
        dict.fromkeys(source_key for _source_feature, source_key in asset_id_by_source_identity)
    )
    if not source_keys:
        return stats

    def load_direct_rows():
        query = (
            PublicationTask.query
            .with_entities(
                PublicationTask.id.label("id"),
                PublicationTask.status.label("status"),
                PublicationTask.current_step.label("current_step"),
                PublicationTask.progress.label("progress"),
                PublicationTask.created_at.label("created_at"),
                PublicationTask.completed_at.label("completed_at"),
                PublicationTask.source_feature.label("source_feature"),
                PublicationTask.source_task_id.label("source_task_id"),
            )
            .filter(PublicationTask.feature == FEATURE_NAME)
            .filter(PublicationTask.source_task_id.in_(source_keys))
        )
        if user_id:
            query = query.filter(PublicationTask.user_id.in_(_visible_user_ids(user_id)))
        return query.order_by(PublicationTask.created_at.desc()).all()

    rows = _db_retry(load_direct_rows, "mp3 video workflow audio asset stats by source_task_id")
    for row in rows:
        source_identity = (
            str(row.source_feature or "").strip(),
            str(row.source_task_id or "").strip(),
        )
        asset_id = asset_id_by_source_identity.get(source_identity)
        if not asset_id:
            continue
        _append_workflow_stat(stats, asset_id, row)

    return stats


def _append_workflow_stat(stats, asset_id, row):
    if asset_id not in stats:
        return False
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
    return True


def _job_list_payload(task):
    status = STATUS_MAP.get(task.status, task.status or "queued")
    title = task.task_title or "MP3 视频编排"
    audio = _audio_summary_from_task(task)
    return {
        "job_id": task.id,
        "status": status,
        "step": task.current_step,
        "progress": task.progress or 0,
        "created_at": task.created_at.isoformat() + "Z" if task.created_at else None,
        "completed_at": task.completed_at.isoformat() + "Z" if task.completed_at else None,
        "error": task.error_message,
        "request": {
            "audio": audio,
            "metadata": {"title": title},
        },
        "audio": audio,
        "plan": {},
        "artifacts": {},
        "artifact_download_urls": {},
        "children": {},
        "retry_attempts": {},
        "retry_state": {},
        "logs": [],
        "summary_only": True,
        "download_input_audio_url": f"/api/mp3-video-workflows/jobs/{task.id}/input-audio",
        "download_result_url": None,
        "download_infographic_url": None,
        "download_landscape_video_url": None,
        "download_vertical_video_url": None,
    }


def _audio_summary_from_task(task):
    source_task_id = str(task.source_task_id or "").strip() or None
    attachment_id = source_task_id
    if (
        source_task_id
        and task.source_feature == CONTENT_PIPELINE_ASSET_SOURCE_FEATURE
        and not source_task_id.startswith("pipeline:")
    ):
        attachment_id = f"pipeline:{source_task_id}"
    path = str(task.mp3_audio_path or "").strip()
    filename = str(task.mp3_audio_filename or "").strip()
    if path and not filename:
        filename = Path(path).name
    if not filename and attachment_id:
        filename = f"音频 {attachment_id}"
    if not any((attachment_id, filename, path, task.mp3_audio_topic_title)):
        return None
    return {
        "attachment_id": attachment_id,
        "topic_title": task.mp3_audio_topic_title or "",
        "tab_label": task.mp3_audio_tab_label or "",
        "filename": filename,
        "download_count": task.mp3_audio_download_count,
        "duration_seconds": task.mp3_audio_duration_seconds,
        "path": path or None,
    }


def _job_payload(task, include_raw=False, *, bilibili_uploads=_JOB_PAYLOAD_BILIBILI_UNSET):
    result = task.result or {}
    params = task.params or {}
    request_payload = params.get(FEATURE_NAME) or {}
    artifacts = result.get("artifacts") or {}
    infographic = result.get("infographic")
    landscape_path = _video_output_path(result.get("landscape_video"))
    vertical_path = _video_output_path(result.get("vertical_video"))
    source_output_path = _source_output_path(result)
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
            "input_source": request_payload.get("input_source") or result.get("input_source"),
            "render_mode": request_payload.get("render_mode") or result.get("render_mode"),
            "generate_subtitles": _truthy(request_payload.get("generate_subtitles", result.get("generate_subtitles")), default=False),
            "gemini_url": request_payload.get("gemini_url"),
            "transcript_chars": len(str(request_payload.get("transcript_text") or "")) if request_payload.get("input_source") == "ima_transcript" else 0,
            "metadata": request_payload.get("metadata") or {},
        },
        "input_source": result.get("input_source") or request_payload.get("input_source") or "gemini_audio",
        "render_mode": result.get("render_mode") or request_payload.get("render_mode") or "full_video",
        "generate_subtitles": _truthy(result.get("generate_subtitles", request_payload.get("generate_subtitles")), default=False),
        "audio": result.get("audio") or request_payload.get("audio"),
        "gemini": result.get("gemini"),
        "source": result.get("source"),
        "initial_response_text": result.get("initial_response_text"),
        "plan": result.get("plan"),
        "infographic": infographic,
        "artifacts": artifacts,
        "keyframes": result.get("keyframes") or {},
        "children": result.get("children") or {},
        "retry_attempts": result.get("retry_attempts") or {},
        "retry_state": result.get("retry_state") or {},
        "regeneration": {
            "requested_at": request_payload.get("force_video_regenerate_at") or result.get("force_video_regenerate_at"),
            "audio_range": request_payload.get("audio_range_override") or result.get("continue_full_video_audio_range"),
            "active": bool(
                (request_payload.get("force_video_regenerate_at") or result.get("force_video_regenerate_at"))
                and task.status in {"pending", "processing"}
            ),
            "history_count": len(result.get("regenerated_video_history") or []),
        },
        "bilibili_uploads": (
            _bilibili_upload_children(task.id)
            if bilibili_uploads is _JOB_PAYLOAD_BILIBILI_UNSET
            else bilibili_uploads
        ),
        "landscape_video": result.get("landscape_video"),
        "vertical_video": result.get("vertical_video"),
        "ds_extract": _ds_extract_payload(result),
        "logs": result.get("logs") or [],
        "download_input_audio_url": f"/api/mp3-video-workflows/jobs/{task.id}/input-audio",
        "download_result_url": f"/api/mp3-video-workflows/jobs/{task.id}/download-result" if _path_exists(result.get("output_path")) else None,
        "download_source_result_url": f"/api/mp3-video-workflows/jobs/{task.id}/download-source" if _path_exists(source_output_path) else None,
        "download_infographic_url": f"/api/mp3-video-workflows/jobs/{task.id}/download-artifact/infographic" if _path_exists((infographic or {}).get("path")) else None,
        "download_landscape_video_url": f"/api/mp3-video-workflows/jobs/{task.id}/download-video/landscape" if _video_file_ready(landscape_path) else None,
        "download_vertical_video_url": f"/api/mp3-video-workflows/jobs/{task.id}/download-video/vertical" if _video_file_ready(vertical_path) else None,
    }
    artifact_urls = {}
    for key, artifact in artifacts.items():
        if _path_exists((artifact or {}).get("path")):
            cache_token = _artifact_cache_token(artifact)
            suffix = f"?v={cache_token}" if cache_token else ""
            artifact_urls[key] = f"/api/mp3-video-workflows/jobs/{task.id}/download-artifact/{key}{suffix}"
    payload["artifact_download_urls"] = artifact_urls
    payload["download_items"] = _download_items_for_payload(payload)
    if include_raw:
        payload["raw_params"] = params
        payload["raw_result"] = result
    return payload


def _first_present(mapping, *keys):
    if not isinstance(mapping, dict):
        return None
    for key in keys:
        if key in mapping:
            return mapping.get(key)
    return None


def _parse_audio_time_value(value, label):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        seconds = float(value)
    else:
        text = str(value).strip()
        if not text:
            return None
        if ":" in text:
            parts = [part.strip() for part in text.split(":")]
            if len(parts) not in {2, 3} or any(not part for part in parts):
                raise ValueError(f"{label}格式无效，请使用 MM:SS 或 HH:MM:SS")
            try:
                numbers = [float(part) for part in parts]
            except ValueError as exc:
                raise ValueError(f"{label}格式无效，请使用 MM:SS 或 HH:MM:SS") from exc
            if any(item < 0 for item in numbers):
                raise ValueError(f"{label}不能为负数")
            minute_overflow = len(numbers) == 3 and numbers[1] >= 60
            if numbers[-1] >= 60 or minute_overflow:
                raise ValueError(f"{label}格式无效，分秒不能超过 59")
            if len(numbers) == 2:
                seconds = numbers[0] * 60 + numbers[1]
            else:
                seconds = numbers[0] * 3600 + numbers[1] * 60 + numbers[2]
        else:
            try:
                seconds = float(text)
            except ValueError as exc:
                raise ValueError(f"{label}格式无效，请使用秒数、MM:SS 或 HH:MM:SS") from exc
    if not math.isfinite(seconds):
        raise ValueError(f"{label}格式无效")
    return seconds


def _format_audio_time(seconds):
    if seconds is None:
        return "自动"
    total = max(0, int(round(float(seconds))))
    hours = total // 3600
    minutes = (total % 3600) // 60
    remainder = total % 60
    if hours:
        return f"{hours:02d}:{minutes:02d}:{remainder:02d}"
    return f"{minutes:02d}:{remainder:02d}"


def _apply_continue_audio_range_override(result, workflow, payload, *, allow_speed=False):
    if not isinstance(payload, dict):
        payload = {}
    has_override = any(key in payload for key in (
        "start_seconds",
        "end_seconds",
        "start_time",
        "end_time",
        "start",
        "end",
    ))
    has_override = has_override or (allow_speed and "speed" in payload)
    if not has_override:
        return True, "", None
    plan = dict(result.get("plan") or {})
    if not plan:
        return False, "当前任务缺少 DS/规划结果，不能修改截取时间", None
    plan_audio = dict(plan.get("audio") or {})
    try:
        start = _parse_audio_time_value(
            _first_present(payload, "start_seconds", "start_time", "start"),
            "起点",
        )
        end = _parse_audio_time_value(
            _first_present(payload, "end_seconds", "end_time", "end"),
            "终点",
        )
        if start is None:
            start = _parse_audio_time_value(plan_audio.get("start_seconds"), "起点")
        if end is None:
            end = _parse_audio_time_value(plan_audio.get("end_seconds"), "终点")
        duration = _parse_audio_time_value(
            (result.get("audio") or {}).get("duration_seconds"),
            "音频总时长",
        )
    except ValueError as exc:
        return False, str(exc), None
    start = 0 if start is None else start
    if start < 0:
        return False, "起点不能为负数", None
    if end is not None and end <= start:
        return False, "终点时间必须大于起点时间", None
    if duration and duration > 0:
        if start >= duration:
            return False, "起点不能超过音频总时长", None
        if end is not None and end > duration + 1:
            return False, "终点不能超过音频总时长", None

    speed = None
    if allow_speed and "speed" in payload:
        try:
            if isinstance(payload["speed"], bool):
                raise ValueError
            speed = float(payload["speed"])
        except (TypeError, ValueError):
            return False, "播放倍速必须在 0.5×–2.0× 之间", None
        if not math.isfinite(speed) or not 0.5 <= speed <= 2.0:
            return False, "播放倍速必须在 0.5×–2.0× 之间", None
        speed = round(speed, 3)

    audio_range = {
        "start_seconds": round(float(start), 3),
        "end_seconds": None if end is None else round(float(end), 3),
    }
    if speed is not None:
        audio_range["speed"] = speed
    plan_audio["start_seconds"] = audio_range["start_seconds"]
    plan_audio["end_seconds"] = audio_range["end_seconds"]
    if speed is not None:
        plan_audio["speed"] = speed
    plan["audio"] = plan_audio
    result["plan"] = plan
    workflow["audio_range_override"] = audio_range
    result["continue_full_video_audio_range"] = audio_range
    return True, "", audio_range


def _prepare_keyframe_job_for_full_video(task, payload=None):
    params = dict(task.params or {})
    workflow = dict(params.get(FEATURE_NAME) or {})
    result = dict(task.result or {})
    render_mode = str(
        result.get("render_mode")
        or workflow.get("render_mode")
        or ""
    ).strip().lower()
    artifacts = result.get("artifacts") if isinstance(result.get("artifacts"), dict) else {}
    has_keyframes = bool(result.get("keyframes") or any(
        str(key).startswith("keyframe_")
        for key in artifacts.keys()
    ))
    if render_mode != "keyframes" and not has_keyframes:
        return False, "当前任务不是关键帧预览任务，不能用这个入口续跑完整视频"
    if _video_file_ready(_video_output_path(result.get("landscape_video"))) and _video_file_ready(_video_output_path(result.get("vertical_video"))):
        return False, "当前任务已经有完整横屏和竖屏视频，无需续跑"

    ok, message, audio_range = _apply_continue_audio_range_override(result, workflow, payload)
    if not ok:
        return False, message

    requested_at = datetime.utcnow().isoformat() + "Z"
    workflow["render_mode"] = "full_video"
    params[FEATURE_NAME] = workflow
    result["render_mode"] = "full_video"
    result["continue_full_video_requested_at"] = requested_at
    result["error"] = None
    result.pop("completed_at", None)
    result.pop("output_path", None)
    result.pop("waiting_child", None)
    result.pop("deferred_waits", None)
    result["landscape_video"] = _drop_keyframe_skip_video(result.get("landscape_video"))
    result["vertical_video"] = _drop_keyframe_skip_video(result.get("vertical_video"))
    logs = list(result.get("logs") or [])
    range_text = ""
    if audio_range:
        range_text = f"（音频 {_format_audio_time(audio_range.get('start_seconds'))}-{_format_audio_time(audio_range.get('end_seconds'))}）"
    logs.append({
        "step": "continue_full_video_requested",
        "message": f"用户确认关键帧效果，继续生成完整横屏和竖屏视频{range_text}",
        "progress": max(int(task.progress or 0), 68),
        "updated_at": requested_at,
    })
    result["logs"] = logs[-160:]

    task.status = "pending"
    task.current_step = "queued_full_video"
    task.progress = max(int(task.progress or 0), 68)
    task.error_message = None
    task.completed_at = None
    task.params = params
    task.result = result
    flag_modified(task, "params")
    flag_modified(task, "result")
    return True, "ok"


def _prepare_job_for_video_regeneration(task, payload=None):
    params = dict(task.params or {})
    workflow = dict(params.get(FEATURE_NAME) or {})
    result = dict(task.result or {})
    plan = result.get("plan") if isinstance(result.get("plan"), dict) else {}
    artifacts = result.get("artifacts") if isinstance(result.get("artifacts"), dict) else {}
    if not plan:
        return False, "当前任务缺少 DS/规划结果，不能重生成视频"
    if not _path_exists((artifacts.get("xiaojunjun_background") or {}).get("path")):
        return False, "当前任务缺少小君君横屏背景图，不能重生成视频"
    if not _path_exists((artifacts.get("audio_background") or {}).get("path")):
        return False, "当前任务缺少此刻此声竖屏背景图，不能重生成视频"

    ok, message, audio_range = _apply_continue_audio_range_override(
        result, workflow, payload, allow_speed=True,
    )
    if not ok:
        return False, message

    requested_at = datetime.utcnow().isoformat() + "Z"
    workflow["render_mode"] = "full_video"
    workflow["force_video_regenerate_at"] = requested_at
    params[FEATURE_NAME] = workflow

    history = list(result.get("regenerated_video_history") or [])
    old_videos = {
        "archived_at": requested_at,
        "landscape_video": result.get("landscape_video"),
        "vertical_video": result.get("vertical_video"),
        "children": {
            "landscape_video": (result.get("children") or {}).get("landscape_video") if isinstance(result.get("children"), dict) else None,
            "vertical_video": (result.get("children") or {}).get("vertical_video") if isinstance(result.get("children"), dict) else None,
        },
    }
    if old_videos["landscape_video"] or old_videos["vertical_video"]:
        history.append(old_videos)
        result["regenerated_video_history"] = history[-8:]

    children = dict(result.get("children") or {})
    children.pop("landscape_video", None)
    children.pop("vertical_video", None)

    result["render_mode"] = "full_video"
    result["force_video_regenerate_at"] = requested_at
    result["error"] = None
    result["children"] = children
    result.pop("completed_at", None)
    result.pop("output_path", None)
    result.pop("waiting_child", None)
    result.pop("deferred_waits", None)
    result.pop("landscape_video", None)
    result.pop("vertical_video", None)
    logs = list(result.get("logs") or [])
    logs.append({
        "step": "regenerate_videos_requested",
        "message": (
            "用户要求重新生成横屏和竖屏视频，强制跳过旧视频复用"
            + (
                f"（音频 {_format_audio_time(audio_range.get('start_seconds'))}"
                f"-{_format_audio_time(audio_range.get('end_seconds'))}）"
                if audio_range
                else ""
            )
        ),
        "progress": max(int(task.progress or 0), 68),
        "updated_at": requested_at,
    })
    result["logs"] = logs[-160:]

    task.status = "pending"
    task.current_step = "queued_regenerate_videos"
    task.progress = max(int(task.progress or 0), 68)
    task.error_message = None
    task.completed_at = None
    task.params = params
    task.result = result
    flag_modified(task, "params")
    flag_modified(task, "result")
    return True, "ok"


def _drop_keyframe_skip_video(video):
    if not isinstance(video, dict):
        return video
    if str(video.get("status") or "").strip().lower() == "skipped" and video.get("reason") == "keyframes_preview":
        return {}
    if not _video_output_path(video) and video.get("reason") == "keyframes_preview":
        return {}
    return video


def _artifact_by_key(task, key):
    return _artifact_by_key_from_result(task.result or {}, key)


def _artifact_by_key_from_result(result, key):
    key = str(key or "").strip()
    result = result if isinstance(result, dict) else {}
    if key == "infographic":
        return result.get("infographic")
    artifacts = result.get("artifacts") if isinstance(result.get("artifacts"), dict) else {}
    return artifacts.get(key)


def _video_by_key(task, key):
    return _video_by_key_from_result(task.result or {}, key)


def _video_by_key_from_result(result, key):
    result = result if isinstance(result, dict) else {}
    if key == "landscape":
        return result.get("landscape_video")
    if key == "vertical":
        return result.get("vertical_video")
    return None


def _video_output_path(video):
    if not isinstance(video, dict):
        return None
    return video.get("video_path") or video.get("landscape_video_path") or video.get("vertical_video_path")


def _default_bilibili_title(result, ds, video_key):
    plan = result.get("plan") if isinstance(result, dict) and isinstance(result.get("plan"), dict) else {}
    if video_key == "landscape":
        return _first_clean_bilibili_title(
            ds.get("title_6"),
            plan.get("xiaojunjun_video_title"),
            plan.get("title"),
            "内资闭门会",
        )
    return _first_clean_bilibili_title(
        ds.get("title_7"),
        plan.get("ciketc_video_title"),
        plan.get("title"),
        "内资闭门会",
    )


def _clean_bilibili_upload_title(value):
    return _clean_ds_title(value, prefer_closed_door=True)


def _first_clean_bilibili_title(*values):
    for value in values:
        cleaned = _clean_bilibili_upload_title(value)
        if cleaned and not _is_placeholder_closed_door_title(cleaned):
            return cleaned
    return ""


def _platform_credential_display_name(credential_id):
    account_id = str(credential_id or "").strip()
    if not account_id:
        return None
    account = (
        PlatformCredentialAccount.query.options(load_only(
            PlatformCredentialAccount.id,
            PlatformCredentialAccount.name,
            PlatformCredentialAccount.owner_label,
            PlatformCredentialAccount.masked_summary,
        ))
        .filter(PlatformCredentialAccount.id == account_id)
        .first()
    )
    if not account:
        return None
    return account.owner_label or account.name or account.masked_summary or account.id


def _resolve_bilibili_cover(result, video_key, requested_key=""):
    artifacts = result.get("artifacts") if isinstance(result.get("artifacts"), dict) else {}
    if requested_key:
        artifact = artifacts.get(requested_key)
        path = artifact.get("path") if isinstance(artifact, dict) else None
        return (path, requested_key) if _path_exists(path) else (None, requested_key)

    if video_key == "landscape":
        candidates = ["xiaojunjun_cover", "finance_cover", "xiaojunjun_background"]
    else:
        candidates = ["finance_cover", "podcast_cover", "vertical_cover", "audio_background"]
    for key in candidates:
        artifact = artifacts.get(key)
        path = artifact.get("path") if isinstance(artifact, dict) else None
        if _path_exists(path):
            return path, key
    return None, ""


def _normalize_upload_tags(value, default=None):
    if isinstance(value, list):
        tags = [str(item).strip(" #") for item in value if str(item or "").strip(" #")]
    elif isinstance(value, str):
        tags = [item.strip(" #") for item in re.split(r"[,，、\n]+", value) if item.strip(" #")]
    else:
        tags = []
    seen = set()
    output = []
    for tag in tags:
        if tag in seen:
            continue
        seen.add(tag)
        output.append(tag[:20])
    return output[:10] or list(default or [])


def _clean_upload_text(value, max_length=500):
    text = re.sub(r"\s+", " ", str(value or "").strip())
    return text[:max_length]


def _charging_pay_from_upload_payload(data):
    source = data.get("charging_pay") if isinstance(data.get("charging_pay"), dict) else {}
    enabled_value = source.get("enabled")
    if enabled_value is None:
        enabled_value = data.get("charging_enabled")
    if enabled_value is None:
        enabled_value = data.get("charging_pay_enabled")
    if not _truthy(enabled_value, default=False):
        return None

    preview = source.get("preview") if isinstance(source.get("preview"), dict) else {}
    preview_start = _bounded_seconds(
        source.get("preview_start_time")
        if source.get("preview_start_time") is not None
        else preview.get("start_time", data.get("charging_preview_start_time")),
        0,
        0,
        24 * 60 * 60,
    )
    preview_end = _bounded_seconds(
        source.get("preview_end_time")
        if source.get("preview_end_time") is not None
        else preview.get("end_time", data.get("charging_preview_end_time")),
        5,
        1,
        24 * 60 * 60,
    )
    if preview_end <= preview_start:
        raise ValueError("充电试看结束时间必须大于开始时间")

    payload = {
        "enabled": True,
        "preview": {
            "need_preview": 1,
            "start_time": preview_start,
            "end_time": preview_end,
        },
        "preview_end_time": preview_end,
    }
    level_id = _clean_upload_text(source.get("upower_level_id") or data.get("upower_level_id"), max_length=80)
    if level_id:
        payload["upower_level_id"] = level_id

    unit_price = _bounded_int(
        source.get("upower_unit_price") or data.get("upower_unit_price"),
        0,
        0,
        1_000_000,
    )
    if unit_price:
        payload["upower_unit_price"] = unit_price

    mode = _bounded_int(source.get("upower_mode") or data.get("upower_mode"), 0, 0, 10)
    if mode:
        payload["upower_mode"] = mode

    member_first_exp = _bounded_int(
        source.get("member_first_exp_time") or data.get("member_first_exp_time"),
        0,
        0,
        365 * 24 * 60 * 60,
    )
    if member_first_exp:
        payload["member_first"] = {"enabled": 1, "exp_time": member_first_exp}

    return payload


def _bilibili_upload_delay_time(data):
    enabled = (
        data.get("schedule_enabled")
        if data.get("schedule_enabled") is not None
        else data.get("delay_enabled")
    )
    raw = data.get("delay_time") or data.get("dtime") or data.get("schedule_time")
    if not _truthy(enabled, default=False) and raw in (None, "", 0, "0", False):
        return None
    if raw in (None, "", 0, "0", False):
        raise ValueError("请选择 B站定时发布时间")
    if isinstance(raw, str) and re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", raw):
        try:
            timestamp = int(datetime.fromisoformat(raw).timestamp())
        except ValueError as exc:
            raise ValueError("B站定时发布时间格式无效") from exc
    else:
        try:
            timestamp = int(float(raw))
        except (TypeError, ValueError) as exc:
            raise ValueError("B站定时发布时间必须是秒级时间戳") from exc

    now_ts = int(time.time())
    min_ts = now_ts + 5 * 60
    max_ts = now_ts + 15 * 24 * 60 * 60
    if timestamp < min_ts or timestamp > max_ts:
        raise ValueError("B站定时发布时间需在 5 分钟后、15 天内")
    return timestamp


def _bilibili_upload_season_id(data):
    raw = data.get("season_id") or data.get("bili_season_id")
    if raw in (None, "", 0, "0", False):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("B站合集 ID 必须是整数") from exc


def _bilibili_upload_children(parent_id):
    return shared_bilibili_upload_children(
        parent_id,
        parent_feature=FEATURE_NAME,
        source_label=f"{FEATURE_NAME}:bilibili_upload",
        limit=20,
    )


def _bilibili_upload_child_payload(task):
    return shared_bilibili_upload_child_payload(
        task,
        source_label=f"{FEATURE_NAME}:bilibili_upload",
    )


def _cancel_bilibili_upload_child(task):
    return shared_cancel_bilibili_upload_child(task)


def _source_output_path(result):
    result = result if isinstance(result, dict) else {}
    source = result.get("source") if isinstance(result.get("source"), dict) else {}
    gemini = result.get("gemini") if isinstance(result.get("gemini"), dict) else {}
    return source.get("output_path") or gemini.get("output_path")


def _source_response_text(result):
    result = result if isinstance(result, dict) else {}
    source = result.get("source") if isinstance(result.get("source"), dict) else {}
    gemini = result.get("gemini") if isinstance(result.get("gemini"), dict) else {}
    candidates = [
        source.get("response_text"),
        source.get("initial_response_text"),
        source.get("final_response_text"),
        result.get("initial_response_text"),
        gemini.get("response_text"),
        gemini.get("initial_response_text"),
        gemini.get("final_response_text"),
    ]
    return next((str(item).strip() for item in candidates if str(item or "").strip()), "")


def _ds_extract_payload(result):
    raw_text = _source_response_text(result)
    sections = _numbered_sections(raw_text)
    clip = _extract_clip(sections.get("4") or raw_text)
    plan = result.get("plan") if isinstance(result, dict) and isinstance(result.get("plan"), dict) else {}
    plan_clip = _clip_from_plan(plan)
    # Once the production planner has accepted and persisted a structured
    # result, it is authoritative for display too. Re-parsing the raw model
    # prose here can reintroduce headings or mixed separators that the planner
    # already normalized and validated.
    plan_title_1 = _clean_ds_title(_plan_title_option(plan, 0) or "")
    plan_title_2 = _clean_ds_title(_plan_title_option(plan, 1) or plan.get("title") or "")
    plan_title_6 = _clean_ds_title(plan.get("xiaojunjun_video_title") or "", prefer_closed_door=True)
    plan_title_7 = _clean_ds_title(plan.get("ciketc_video_title") or "", prefer_closed_door=True)
    payload = {
        "title_1": plan_title_1 or _clean_ds_title(sections.get("1", "")),
        "title_2": plan_title_2 or _clean_ds_title(sections.get("2", "")),
        "clip": clip,
        "title_6": plan_title_6 or _clean_ds_title(sections.get("6", ""), prefer_closed_door=True),
        "title_7": plan_title_7 or _clean_ds_title(sections.get("7", ""), prefer_closed_door=True),
        "raw_text": raw_text,
        "raw_sections": sections,
    }
    if not payload["title_1"]:
        payload["title_1"] = _clean_ds_title(_plan_title_option(plan, 0) or plan.get("title") or "")
    if not payload["title_2"]:
        payload["title_2"] = _clean_ds_title(_plan_title_option(plan, 1) or plan.get("title") or "")
    if not payload["title_6"]:
        payload["title_6"] = _clean_ds_title(plan.get("xiaojunjun_video_title") or "", prefer_closed_door=True)
    if not payload["title_7"]:
        payload["title_7"] = _clean_ds_title(plan.get("ciketc_video_title") or "", prefer_closed_door=True)
    if not (clip.get("start_text") and clip.get("end_text")) and plan_clip.get("start_text"):
        payload["clip"] = plan_clip
    payload["has_content"] = any([
        payload["title_1"],
        payload["title_2"],
        payload["title_6"],
        payload["title_7"],
        clip.get("start_text"),
        clip.get("end_text"),
    ])
    return payload


def _plan_title_option(plan, index):
    options = plan.get("title_options") if isinstance(plan, dict) and isinstance(plan.get("title_options"), list) else []
    if index < len(options):
        return options[index]
    return ""


def _clip_from_plan(plan):
    plan_audio = plan.get("audio") if isinstance(plan, dict) and isinstance(plan.get("audio"), dict) else {}
    start_seconds = _coerce_seconds(plan_audio.get("start_seconds"))
    end_seconds = _coerce_seconds(plan_audio.get("end_seconds"))
    if start_seconds is None and end_seconds is None:
        return {}
    return {
        "start_text": _format_timestamp(start_seconds) if start_seconds is not None else "",
        "end_text": _format_timestamp(end_seconds) if end_seconds is not None else "",
        "start_seconds": start_seconds,
        "end_seconds": end_seconds,
        "summary": "来自 DS 规划结果",
    }


def _coerce_seconds(value):
    if value in (None, ""):
        return None
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return _timestamp_to_seconds(value)


def _numbered_sections(text):
    text = str(text or "")
    if not text.strip():
        return {}
    marker_re = re.compile(
        r"(?:(?<=\n)|^)\s*(?:[-*]\s*)?(?:\*\*)?\s*([1-7])\s*[.．、:：]\s*"
        r"|(?:\*\*)\s*([1-7])\s*[.．、:：]\s*"
    )
    matches = list(marker_re.finditer(text))
    sections = {}
    for index, match in enumerate(matches):
        number = match.group(1) or match.group(2)
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        content = text[start:end].strip()
        if number and content:
            sections[number] = content
    return sections


def _clean_ds_text(text):
    value = str(text or "")
    value = value.replace("**", "").replace("__", "")
    value = re.sub(r"^[\s\-*•]+", "", value.strip())
    value = re.sub(r"\s+", " ", value)
    return value.strip(" \t\r\n：:，,。;；")


def _clean_ds_title(text, *, prefer_closed_door=False):
    value = " ".join(_ds_title_content_lines(text)) or _clean_ds_text(text)
    if not value:
        return ""
    if prefer_closed_door:
        value = _strip_closed_door_template_noise(value)
        for pattern in (
            r"内资闭门会\s*[/|｜].*?(?:\d{4}[-./]\d{1,2}[-./]\d{1,2}|\d{6,8})",
            r"内资闭门会\s*[/|｜][^。；\n]+",
        ):
            matches = [
                _trim_title(match.group(0))
                for match in re.finditer(pattern, value)
                if not _is_placeholder_closed_door_title(match.group(0))
            ]
            if matches:
                return matches[-1]
    quote_match = re.fullmatch(r"[“\"]([^”\"]{6,})[”\"]", value)
    if quote_match:
        return _trim_title(quote_match.group(1))
    value = re.sub(
        r"^(?:高信息密度)?(?:投资人|投资者)?(?:视角)?(?:标题|长标题|标题版本[一二三四五六七八九十A-Za-z\d]*|版本[一二]|标题优化|闭门会格式标题)"
        r"(?:（[^）]*）|\([^)]*\))?\s*[:：]?\s*",
        "",
        value,
    )
    value = re.sub(r"^(?:斜杠版|竖线版|另一个版本|版本[一二])\s*[:：]?\s*", "", value)
    return _trim_title(value)


def _ds_title_content_lines(text):
    lines = []
    for raw in str(text or "").replace("\r", "\n").split("\n"):
        clean = _clean_ds_text(raw)
        if not clean or _is_ds_title_heading(clean):
            continue
        clean = re.sub(
            r"^(?:高信息密度)?(?:投资人|投资者)?(?:视角)?(?:标题|长标题|标题版本[一二三四五六七八九十A-Za-z\d]*|版本[一二]|"
            r"短标题版本|标题优化|优化标题格式|优化标题|闭门会格式标题)"
            r"(?:（[^）]*）|\([^)]*\))?\s*[:：-]?\s*",
            "",
            clean,
        ).strip(" ：:，,。;；")
        if clean and not _is_ds_title_heading(clean):
            lines.append(clean)
    return lines


def _is_ds_title_heading(value):
    text = _clean_ds_text(value)
    return bool(re.fullmatch(
        r"(?:高信息密度)?(?:投资人|投资者)?(?:视角)?(?:标题|长标题|标题版本[一二三四五六七八九十A-Za-z\d]*|版本[一二]|"
        r"短标题版本|精简短标题|标题优化|优化标题格式|优化标题|闭门会格式标题)"
        r"(?:（[^）]*）|\([^)]*\))?",
        text,
        flags=re.I,
    ))


def _strip_closed_door_template_noise(value):
    value = re.sub(
        r"(?:使用|格式\s*[:：]?)?\s*内资闭门会(?:议)?\s*[/|｜]\s*xxxx\s*[/|｜]\s*xxxx\s*[/|｜]\s*xxxx\s*[/|｜]\s*当日日期[)）]?",
        " ",
        str(value or ""),
        flags=re.I,
    )
    value = re.sub(r"这样的格式来写", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def _is_placeholder_closed_door_title(value):
    text = str(value or "")
    return bool(re.search(r"xxxx|当日日期", text, flags=re.I))


def _trim_title(value):
    value = _clean_ds_text(value)
    value = value.strip("「」『』“”\"'")
    return value[:500]


def _extract_clip(text):
    value = _clean_ds_text(text)
    time_token = r"(?:\d{1,2}:)?\d{1,2}:\d{2}|\d+(?:\.\d+)?\s*(?:秒|s)?"
    start = end = None
    start_match = re.search(rf"(?:开始|起点|起始)(?:时间戳|时间)?\s*[:：]?\s*[\[【(（]?\s*({time_token})", value)
    end_match = re.search(rf"(?:结束|终点|截止|终止)(?:时间戳|时间)?\s*[:：]?\s*[\[【(（]?\s*({time_token})", value)
    start = start_match.group(1) if start_match else None
    end = end_match.group(1) if end_match else None
    if not (start and end):
        pair = re.search(rf"({time_token})\s*(?:-|—|–|~|～|至|到)\s*({time_token})", value)
        if pair:
            start, end = pair.group(1), pair.group(2)
    start_seconds = _timestamp_to_seconds(start)
    end_seconds = _timestamp_to_seconds(end)
    if start_seconds is not None and end_seconds is not None and end_seconds <= start_seconds:
        start_seconds = None
        end_seconds = None
    return {
        "start_text": _format_timestamp(start_seconds) if start_seconds is not None else "",
        "end_text": _format_timestamp(end_seconds) if end_seconds is not None else "",
        "start_seconds": start_seconds,
        "end_seconds": end_seconds,
        "summary": value[:1000],
    }


def _timestamp_to_seconds(value):
    text = str(value or "").strip().lower().replace("秒", "").replace("s", "")
    if not text:
        return None
    if ":" in text:
        parts = [part for part in text.split(":") if part != ""]
        try:
            numbers = [int(float(part)) for part in parts]
        except ValueError:
            return None
        if len(numbers) == 2:
            return numbers[0] * 60 + numbers[1]
        if len(numbers) == 3:
            return numbers[0] * 3600 + numbers[1] * 60 + numbers[2]
        return None
    try:
        return int(round(float(text)))
    except ValueError:
        return None


def _format_timestamp(seconds):
    if seconds is None:
        return ""
    total = max(0, int(seconds))
    hours = total // 3600
    minutes = (total % 3600) // 60
    remain = total % 60
    if hours:
        return f"{hours:02d}:{minutes:02d}:{remain:02d}"
    return f"{minutes:02d}:{remain:02d}"


def _download_items_for_payload(payload):
    items = []

    def add(key, label, url, kind):
        if url:
            items.append({"key": key, "label": label, "url": url, "kind": kind})

    add("input_audio", "原始 MP3", payload.get("download_input_audio_url"), "audio")
    add("source_result", "DS/Gemini 源结果 JSON", payload.get("download_source_result_url"), "json")
    add("infographic", (payload.get("infographic") or {}).get("label") or "信息图", payload.get("download_infographic_url"), "image")
    artifacts = payload.get("artifacts") if isinstance(payload.get("artifacts"), dict) else {}
    for key, url in (payload.get("artifact_download_urls") or {}).items():
        artifact = artifacts.get(key) if isinstance(artifacts.get(key), dict) else {}
        label = artifact.get("label") or key
        kind = "image"
        if str(key).startswith("keyframe_"):
            kind = "keyframe"
        add(key, label, url, kind)
    add("landscape_video", "小君君横屏视频", payload.get("download_landscape_video_url"), "video")
    add("vertical_video", "此刻此声竖屏视频", payload.get("download_vertical_video_url"), "video")
    add("result_json", "编排结果 JSON", payload.get("download_result_url"), "json")
    return items


def _artifact_cache_token(artifact):
    if not isinstance(artifact, dict):
        return ""
    for key in ("manual_regenerated_at", "updated_at", "generated_at"):
        value = artifact.get(key)
        if value:
            return re.sub(r"[^0-9A-Za-z]+", "", str(value))[:32]
    path = artifact.get("path")
    if _path_exists(path):
        try:
            stat = os.stat(path)
            return str(int(stat.st_mtime * 1000))
        except OSError:
            return ""
    return ""


def _max_audio_upload_bytes():
    try:
        configured_mb = float(os.environ.get("MP3_VIDEO_UPLOAD_MAX_MB", DEFAULT_AUDIO_UPLOAD_MAX_MB))
    except (TypeError, ValueError):
        configured_mb = DEFAULT_AUDIO_UPLOAD_MAX_MB
    configured_mb = max(1.0, min(configured_mb, 2048.0))
    return int(configured_mb * 1024 * 1024)


def _audio_upload_filename(file_storage):
    raw_name = Path(str(getattr(file_storage, "filename", "") or "")).name
    extension = Path(raw_name).suffix.lower()
    safe_name = secure_filename(raw_name)
    original_stem = Path(raw_name).stem
    safe_stem = Path(safe_name).stem.lower() if safe_name else ""
    # Werkzeug's secure_filename intentionally drops non-ASCII characters. That
    # turns mixed names such as ``AI芯片研究报告.mp3`` into ``AI.mp3`` and then
    # leaks the truncated name into the job list. Keep a filesystem-safe Unicode
    # stem for user-facing audio metadata whenever the original contains one.
    if any(ord(char) > 127 for char in original_stem):
        unicode_stem = re.sub(r"[^\w\-]+", "_", original_stem, flags=re.UNICODE).strip("._-")
        safe_name = f"{unicode_stem or 'audio'}{extension or '.mp3'}"
    elif not safe_name or safe_stem in AUDIO_EXTENSIONS:
        original_stem = re.sub(r"[^0-9A-Za-z_\-]+", "_", original_stem).strip("._-")
        safe_name = f"{original_stem or 'audio'}{extension or '.mp3'}"
    if not Path(safe_name).suffix and extension:
        safe_name = f"{safe_name}{extension}"
    return safe_name[:400], extension.lstrip(".").lower()


def _validate_audio_upload(file_storage):
    if not file_storage or not str(getattr(file_storage, "filename", "") or "").strip():
        return "MP3 文件名为空"
    _, extension = _audio_upload_filename(file_storage)
    if extension not in AUDIO_EXTENSIONS:
        supported = ", ".join(sorted(f".{item}" for item in AUDIO_EXTENSIONS))
        return f"只支持音频文件：{supported}"
    max_bytes = _max_audio_upload_bytes()
    content_length = getattr(file_storage, "content_length", None) or request.content_length
    if content_length and int(content_length) > max_bytes:
        return f"音频超过 {max_bytes // (1024 * 1024)}MB 限制"
    return None


def _save_audio_upload(file_storage, upload_id):
    upload_error = _validate_audio_upload(file_storage)
    if upload_error:
        raise ValueError(upload_error)
    filename, extension = _audio_upload_filename(file_storage)
    upload_dir = (
        Path(__file__).resolve().parents[3]
        / "data"
        / "uploads"
        / "mp3_video_workflow"
        / datetime.utcnow().strftime("%Y%m%d")
    )
    upload_dir.mkdir(parents=True, exist_ok=True)
    safe_upload_id = re.sub(r"[^0-9a-fA-F-]", "", str(upload_id or ""))[:36] or str(uuid.uuid4())
    output_path = upload_dir / f"{safe_upload_id}_{filename}"
    try:
        file_storage.save(str(output_path))
    except Exception as exc:
        _remove_uploaded_audio(output_path)
        raise ValueError(f"保存音频失败: {exc}") from exc
    try:
        size = output_path.stat().st_size
    except OSError as exc:
        raise ValueError(f"保存音频失败: {exc}") from exc
    if size <= 0:
        _remove_uploaded_audio(output_path)
        raise ValueError("音频文件为空")
    if size > _max_audio_upload_bytes():
        _remove_uploaded_audio(output_path)
        raise ValueError(f"音频超过 {_max_audio_upload_bytes() // (1024 * 1024)}MB 限制")
    try:
        duration_seconds = float(probe_media_duration(str(output_path)) or 0)
    except Exception:
        duration_seconds = 0
    if duration_seconds <= 0:
        _remove_uploaded_audio(output_path)
        raise ValueError("无法读取音频内容，请确认上传的是有效的 MP3 文件")
    return {
        "attachment_id": str(upload_id),
        "filename": filename,
        "name": filename,
        "path": str(output_path),
        "bytes": size,
        "file_ext": extension,
        "download_count": None,
        "duration_seconds": duration_seconds,
        "topic_title": filename,
        "tab_label": "本地上传",
        "source": "local_upload",
    }


def _remove_uploaded_audio(path_value):
    try:
        path = Path(str(path_value or ""))
        if path.exists() and path.is_file():
            path.unlink()
    except OSError:
        pass


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


def _bounded_seconds(value, default, min_value, max_value):
    text = str(value or "").strip()
    if ":" in text:
        try:
            parts = [int(float(part)) for part in text.split(":")]
        except ValueError:
            return _bounded_int(default, default, min_value, max_value)
        if len(parts) == 2:
            parsed = parts[0] * 60 + parts[1]
        elif len(parts) == 3:
            parsed = parts[0] * 3600 + parts[1] * 60 + parts[2]
        else:
            parsed = default
        return max(min_value, min(max_value, parsed))
    return _bounded_int(value, default, min_value, max_value)
