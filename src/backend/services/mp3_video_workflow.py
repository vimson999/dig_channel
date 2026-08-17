import base64
import copy
import hashlib
import json
import logging
import mimetypes
import os
import re
import subprocess
import time
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from functools import lru_cache, wraps
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps
from sqlalchemy import func, inspect, or_, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import load_only
from sqlalchemy.orm.attributes import flag_modified

from celery_config import celery_app
from models import db
from models.task import PublicationTask
from models.zsxq_sync import ZsxqAttachment, ZsxqTopic
from services.asr_cache_service import probe_media_duration
from services.gemini_browser import (
    GeminiBrowserOptions,
    GeminiBrowserWorker,
    _looks_like_page_shell_text,
    is_configured as gemini_is_configured,
)
from services.publisher_queue_repair import maybe_requeue_stale_publisher_child
from services.runtime_guard import (
    apply_async_after_db_release,
    is_retryable_db_error,
    release_db_session_quietly,
)
from services.workflow_notification_service import workflow_notification_service
from tasks.publisher import run_publication_pipeline


logger = logging.getLogger(__name__)
_active_workflow_state = ContextVar("mp3_video_workflow_state", default=None)
FEATURE_NAME = "mp3_video_workflow"
QUEUE_NAME = "mp3_video_workflow"
DEFAULT_GEMINI_URL = "https://gemini.google.com/gem/f9e84d5e215c"
DEFAULT_GEMINI_INITIAL_PROMPT = "请基于我已上传的音频文件开始处理，严格按照当前 Gem 的既定流程输出结果。"
DEFAULT_TRANSCRIPT_DEEPSEEK_PROMPT = (
    "1：请你使用投资者的视角，用投资者最关心的信息视角为这个内容起一个标题，告诉投资者信息差和赚钱机会，"
    "这个标题要包括足够的信息密度同时又能提高点击率\n"
    "2：再来一个版本的，请你使用投资者的视角，用投资者最关心的信息视角为这个内容起一个标题，告诉投资者信息差和赚钱机会，"
    "这个标题要包括足够的信息密度同时又能提高点击率\n"
    "3：再精简一个短标题\n"
    "4：从这段音频中识别出最精华的一段，要让别人一打开这个音频就立即被吸引住，我们需要提取出来，"
    "需要标识出来开始、结束的时间戳，\n"
    "注意：如果音频总时长超过了30分钟，那么你就识别出来的片段应该大约25分钟，"
    "如果音频总时长不超过30分分钟，那么识别出来片段应该尽量接近15分分钟。\n"
    "5：找出这段音频里的几段金句\n"
    "6：尝试优化下标题，使用 内资闭门会 / xxxx / xxxx / xxxx / 当日日期 ，这样的格式来写\n"
    "7：尝试优化下标题，并给我另外一个版本，使用 内资闭门会 | xxxx | xxxx | xxxx | 当日日期 ，这样的格式来写"
)
DEFAULT_SPEED = 1.2
DEFAULT_CHILD_TIMEOUT_SECONDS = 2400
DEFAULT_PUBLISHER_INLINE_WAIT_SECONDS = 5
DEFAULT_PARENT_RETRY_DELAY_SECONDS = 300
DEFAULT_PUBLISHER_PARENT_RETRY_DELAY_SECONDS = 60
DEFAULT_PARENT_MAX_DEFERS = 144
DEFAULT_PUBLISHER_PARENT_MAX_DEFERS = 180
TERMINAL_STATUSES = {"completed", "failed", "canceled"}
CELERY_FAILURE_STATES = {"FAILURE", "REVOKED"}
AUDIO_EXTENSIONS = {"mp3", "m4a", "wav", "aac", "flac", "ogg", "opus"}
HOT_AUDIO_DOWNLOAD_THRESHOLD = 20
AUDIO_TOPIC_CONTENT_PREVIEW_SOURCE_CHARS = 720
PLANNER_VERSION = "mp3_video_planner_20260719_v12"
VISUAL_ARTIFACT_KEYS = (
    "finance_cover",
    "audio_background",
    "xiaojunjun_cover",
    "xiaojunjun_background",
)
VISUAL_ARTIFACT_VERSION = "mp3_video_visuals_20260719_v12"
KEYFRAME_ARTIFACT_VERSION = "mp3_video_keyframes_20260706_v6"
TRANSCRIPT_DEEPSEEK_CACHE_VERSION = "mp3_transcript_deepseek_cache_20260706_v1"
VISUAL_ARTIFACT_EXPECTED_SIZES = {
    "finance_cover": (1280, 720),
    "audio_background": (1080, 1920),
    "xiaojunjun_cover": (1280, 720),
    "xiaojunjun_background": (1280, 720),
}
VISUAL_ARTIFACT_LABELS = {
    "finance_cover": "此刻此声-横屏视频封皮 1",
    "audio_background": "此刻此声-竖屏视频背景图 1",
    "xiaojunjun_cover": "小君君-横屏视频封皮",
    "xiaojunjun_background": "小君君 横屏视频 背景",
}
VISUAL_ARTIFACT_RENDERERS = {
    "finance_cover": "browser:finance-cover-generator",
    "audio_background": "browser:thumbnail-workbench",
    "xiaojunjun_cover": "browser:closed-door-card-cover-generator",
    "xiaojunjun_background": "browser:closed-door-card-cover-generator",
}
GEMINI_RETRY_LIMIT_ENV_NAME = "MP3_VIDEO_GEMINI_RETRY_LIMIT"
DEFAULT_GEMINI_RETRY_LIMIT = 2
RETRYABLE_GEMINI_ERROR_FRAGMENTS = (
    "超时",
    "timeout",
    "timed out",
    "文件上传失败",
    "上传完成超时",
    "上传被取消",
    "提示词未成功提交",
    "未成功提交",
    "发送按钮",
    "等待 Gemini",
    "等待第一轮",
    "生成图片超时",
    "页面自动化超时",
    "Locator.click",
    "Target closed",
    "Browser closed",
    "Protocol error",
    "net::ERR",
)
NON_RETRYABLE_GEMINI_ERROR_FRAGMENTS = (
    "任务不存在",
    "音频文件不存在",
    "父编排任务已取消",
)
TASK_WORKFLOW_COLUMNS = (
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
TASK_RESULT_COLUMNS = (
    PublicationTask.id,
    PublicationTask.result,
)
TASK_PROGRESS_RESULT_COLUMNS = (
    PublicationTask.id,
    PublicationTask.current_step,
    PublicationTask.progress,
    PublicationTask.result,
)
TASK_PROGRESS_STATUS_COLUMNS = (
    PublicationTask.id,
    PublicationTask.status,
    PublicationTask.current_step,
    PublicationTask.progress,
    PublicationTask.error_message,
)
TASK_STATUS_COLUMNS = (
    PublicationTask.id,
    PublicationTask.status,
)
TASK_WAITING_STATUS_COLUMNS = (
    PublicationTask.id,
    PublicationTask.status,
    PublicationTask.current_step,
    PublicationTask.progress,
    PublicationTask.waiting_child_label,
    PublicationTask.waiting_child_id,
    PublicationTask.waiting_child_status,
    PublicationTask.waiting_child_step,
    PublicationTask.waiting_child_updated_at,
)
TASK_PUBLIC_STATUS_COLUMNS = (
    PublicationTask.id,
    PublicationTask.celery_task_id,
    PublicationTask.status,
    PublicationTask.current_step,
    PublicationTask.progress,
    PublicationTask.error_message,
    PublicationTask.feature,
    PublicationTask.task_video_path,
    PublicationTask.publisher_aspect_ratio,
    PublicationTask.publisher_generate_subtitles,
    PublicationTask.publisher_audio_signature,
    PublicationTask.publisher_vertical_video_path,
    PublicationTask.publisher_vertical_cover_path,
    PublicationTask.publisher_podcast_cover_path,
    PublicationTask.publisher_bilibili_cover_path,
    PublicationTask.created_at,
    PublicationTask.updated_at,
    PublicationTask.completed_at,
)
TASK_PUBLIC_COLUMNS = (
    *TASK_PUBLIC_STATUS_COLUMNS,
    PublicationTask.params,
    PublicationTask.result,
)
TASK_PUBLISHER_MATCH_COLUMNS = (
    *TASK_PUBLIC_STATUS_COLUMNS,
    PublicationTask.publisher_child_role,
    PublicationTask.publisher_aspect_ratio,
    PublicationTask.publisher_generate_subtitles,
    PublicationTask.publisher_audio_signature,
)
TASK_FAIL_COLUMNS = (
    PublicationTask.id,
    PublicationTask.status,
    PublicationTask.current_step,
    PublicationTask.progress,
    PublicationTask.result,
    PublicationTask.error_message,
    PublicationTask.completed_at,
)
TASK_NOTIFICATION_COLUMNS = (
    PublicationTask.id,
    PublicationTask.status,
    PublicationTask.current_step,
    PublicationTask.params,
    PublicationTask.result,
    PublicationTask.error_message,
    PublicationTask.created_at,
    PublicationTask.completed_at,
)
TASK_NOTIFICATION_STATUS_COLUMNS = (
    PublicationTask.id,
    PublicationTask.status,
    PublicationTask.current_step,
    PublicationTask.error_message,
    PublicationTask.created_at,
    PublicationTask.completed_at,
)

INFOGRAPHIC_PROMPT = (
    "根据以上内容\n"
    "请创造一张横向排版的信息图，以简短、干净、极简为排版原则；视觉上采用手绘笔记风格，"
    "并严格参照《华尔街日报》经典插图的黑白钢笔画、点刻/Hedcut、细密排线表现形式来呈现主题图案；"
    "所有说明文字请使用简体中文；排版中不得出现任何机构名称或字符串“FCCNN88”；"
    "保持整体手绘质感，图文协调，适合直接用于展示。"
)


class Mp3VideoWorkflowError(RuntimeError):
    pass


class ChildTaskStillRunning(RuntimeError):
    def __init__(self, label: str, child_id: str, status: str = "", step: str = ""):
        self.label = label
        self.child_id = child_id
        self.status = status
        self.step = step
        super().__init__(f"子任务仍在执行: {label} {child_id} ({status or '-'} / {step or '-'})")


class ParentTaskCanceled(RuntimeError):
    pass


def project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _db_retry(operation, label: str = "db operation", *, attempts: int = 3, delay_seconds: float = 0.25):
    last_error = None
    for attempt in range(1, max(1, attempts) + 1):
        try:
            return operation()
        except Exception as exc:
            if not is_retryable_db_error(exc):
                raise
            last_error = exc
            try:
                db.session.rollback()
            except Exception:
                pass
            try:
                db.session.remove()
            except Exception:
                pass
            if attempt >= attempts:
                break
            logger.warning("MP3 workflow DB retry %s/%s for %s: %s", attempt + 1, attempts, label, exc)
            time.sleep(delay_seconds * attempt)
    raise last_error


def _load_task(task_id: Any, *columns: Any) -> Optional[PublicationTask]:
    def operation():
        query = PublicationTask.query
        if columns:
            query = query.options(load_only(*columns))
        task = query.filter(PublicationTask.id == str(task_id)).first()
        state = _active_workflow_state.get()
        if task and state and state.get("task_id") == str(task_id):
            if "params" in task.__dict__:
                state["params"] = copy.deepcopy(task.__dict__.get("params") or {})
            if "result" in task.__dict__:
                state["result"] = copy.deepcopy(task.__dict__.get("result") or {})
        return task

    return _db_retry(operation, f"load task {task_id}")


def _model_attr_loaded(record: Any, name: str) -> bool:
    try:
        return name not in inspect(record).unloaded
    except Exception:
        return True


def _workflow_result_scope(operation):
    @wraps(operation)
    def wrapped(task_id: str, *args: Any, **kwargs: Any):
        token = _active_workflow_state.set({
            "task_id": str(task_id),
            "params": None,
            "result": None,
        })
        try:
            return operation(task_id, *args, **kwargs)
        finally:
            _active_workflow_state.reset(token)

    return wrapped


def _cached_workflow_result(task_id: Any) -> Optional[Dict[str, Any]]:
    state = _active_workflow_state.get()
    if not state or state.get("task_id") != str(task_id) or state.get("result") is None:
        return None
    return copy.deepcopy(state["result"])


def _cached_workflow_params(task_id: Any) -> Optional[Dict[str, Any]]:
    state = _active_workflow_state.get()
    if not state or state.get("task_id") != str(task_id) or state.get("params") is None:
        return None
    return copy.deepcopy(state["params"])


def _merge_cached_workflow_result(task_id: Any, values: Dict[str, Any]) -> None:
    state = _active_workflow_state.get()
    if not state or state.get("task_id") != str(task_id) or state.get("result") is None:
        return
    state["result"].update(copy.deepcopy(values))


def _replace_cached_workflow_result(task_id: Any, result: Dict[str, Any]) -> None:
    state = _active_workflow_state.get()
    if not state or state.get("task_id") != str(task_id):
        return
    state["result"] = copy.deepcopy(result)



def storage_dir_for_job(job_id: str, created_at: Optional[datetime] = None) -> Path:
    created_at = created_at or datetime.utcnow()
    root = project_root() / "data" / "processed" / FEATURE_NAME / created_at.strftime("%Y%m%d") / str(job_id)
    root.mkdir(parents=True, exist_ok=True)
    return root


def storage_path_for_job(job_id: str, created_at: Optional[datetime] = None) -> str:
    return str(storage_dir_for_job(job_id, created_at) / "workflow_result.json")


def artifact_path_for_job(job_id: str, name: str, ext: str = "jpg", created_at: Optional[datetime] = None) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(name or "artifact")).strip("_") or "artifact"
    return str(storage_dir_for_job(job_id, created_at) / f"{safe}.{ext.lstrip('.')}")


def status_payload() -> Dict[str, Any]:
    planner_configured = bool(os.environ.get("MP3_VIDEO_PLANNER_API_KEY") or os.environ.get("DEEPSEEK_API_KEY"))
    transcript_configured = bool(os.environ.get("MP3_VIDEO_TRANSCRIPT_API_KEY") or planner_configured)
    deepseek_configured = bool(planner_configured or transcript_configured)
    options = gemini_options_from_env()
    gemini_configured = gemini_is_configured(options)
    return {
        "configured": bool(gemini_configured or deepseek_configured),
        "mode": "celery_orchestrator",
        "gemini_configured": gemini_configured,
        "gemini_url": options.gemini_url,
        "deepseek_configured": deepseek_configured,
        "planner_deepseek_configured": planner_configured,
        "transcript_deepseek_configured": transcript_configured,
        "planner_provider": "deepseek" if planner_configured else "local_fallback",
        "planner_model": planner_model() if planner_configured else None,
        "transcript_provider": "deepseek" if transcript_configured else "unavailable",
        "transcript_model": transcript_model() if transcript_configured else None,
        "business_feature": FEATURE_NAME,
    }


def gemini_options_from_env() -> GeminiBrowserOptions:
    options = GeminiBrowserOptions.from_env()
    options.gemini_url = _env("MP3_VIDEO_GEMINI_URL", "GEMINI_MP3_VIDEO_URL", DEFAULT_GEMINI_URL)
    options.response_timeout_seconds = _int_env("MP3_VIDEO_GEMINI_RESPONSE_TIMEOUT", max(options.response_timeout_seconds, 900))
    options.image_timeout_seconds = _int_env("MP3_VIDEO_GEMINI_IMAGE_TIMEOUT", max(options.image_timeout_seconds, 900))
    options.total_timeout_seconds = _int_env("MP3_VIDEO_GEMINI_TOTAL_TIMEOUT", max(options.total_timeout_seconds, 2400))
    options.upload_timeout_seconds = _int_env(
        "MP3_VIDEO_GEMINI_UPLOAD_TIMEOUT",
        _int_env("GEMINI_MP3_VIDEO_UPLOAD_TIMEOUT", max(options.upload_timeout_seconds, 900)),
    )
    return options


def initial_gemini_prompt(workflow: Optional[Dict[str, Any]] = None) -> str:
    workflow = workflow if isinstance(workflow, dict) else {}
    prompt = str(workflow.get("gemini_prompt") or "").strip()
    if prompt:
        return prompt
    return _env("MP3_VIDEO_GEMINI_INITIAL_PROMPT", "GEMINI_MP3_VIDEO_INITIAL_PROMPT", DEFAULT_GEMINI_INITIAL_PROMPT).strip()


def initial_transcript_prompt(workflow: Optional[Dict[str, Any]] = None) -> str:
    workflow = workflow if isinstance(workflow, dict) else {}
    prompt = str(workflow.get("transcript_prompt") or "").strip()
    if prompt:
        return prompt
    return _env("MP3_VIDEO_TRANSCRIPT_PROMPT", DEFAULT_TRANSCRIPT_DEEPSEEK_PROMPT).strip()


def _topic_content_preview_projection():
    # The API returns only a 180-character preview. Fetch a bounded source
    # window so large topic bodies are not transferred through PgBouncer.
    return func.substr(
        ZsxqTopic.content,
        1,
        AUDIO_TOPIC_CONTENT_PREVIEW_SOURCE_CHARS,
    ).label("topic_content")


def list_audio_assets(
    *,
    page: int = 1,
    per_page: int = 30,
    q: str = "",
    group_id: str = "",
    tab_id: str = "",
    scope: str = "all",
) -> Dict[str, Any]:
    page = max(1, int(page or 1))
    per_page = max(1, min(int(per_page or 30), 100))
    q = (q or "").strip()
    group_id = (group_id or "").strip()
    tab_id = (tab_id or "").strip()
    scope = (scope or "all").strip().lower()

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
            ZsxqAttachment.file_ext.label("file_ext"),
            ZsxqAttachment.updated_at.label("attachment_updated_at"),
            ZsxqAttachment.created_at.label("attachment_created_at"),
            ZsxqAttachment.group_id.label("group_id"),
            ZsxqAttachment.tab_id.label("tab_id"),
            ZsxqAttachment.topic_id.label("topic_id"),
            ZsxqTopic.title.label("topic_title"),
            _topic_content_preview_projection(),
            ZsxqTopic.tab_label.label("tab_label"),
            ZsxqTopic.author_name.label("topic_author"),
            ZsxqTopic.create_time.label("topic_create_time"),
            ZsxqTopic.first_synced_at.label("topic_first_synced_at"),
            ZsxqTopic.last_synced_at.label("topic_last_synced_at"),
        )
        .outerjoin(ZsxqTopic, topic_join)
        .filter(ZsxqAttachment.download_status == "downloaded")
        .filter(ZsxqAttachment.local_path.isnot(None))
        .filter(_audio_asset_filter())
    )
    if scope in {"hot", "popular"}:
        query = query.filter(ZsxqAttachment.download_count >= HOT_AUDIO_DOWNLOAD_THRESHOLD)
    if group_id:
        query = query.filter(ZsxqAttachment.group_id == group_id)
    if tab_id:
        query = query.filter(ZsxqAttachment.tab_id == tab_id)
    if q:
        pattern = f"%{q}%"
        query = query.filter(or_(
            ZsxqAttachment.name.ilike(pattern),
            ZsxqAttachment.local_path.ilike(pattern),
            ZsxqTopic.title.ilike(pattern),
            ZsxqTopic.content.ilike(pattern),
        ))

    target_count = page * per_page + 1
    chunk_size = min(max(per_page * 4, 40), 120)
    scan_limit = min(max(target_count * 5, 120), 2500)
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
    valid_items: List[Dict[str, Any]] = []
    skipped_missing = 0
    scanned = 0
    while len(valid_items) < target_count and scanned < scan_limit:
        batch_limit = min(chunk_size, scan_limit - scanned)
        rows = ordered.offset(scanned).limit(batch_limit).all()
        if not rows:
            break
        scanned += len(rows)
        for row in rows:
            item = _audio_asset_row_payload(row)
            if item:
                valid_items.append(item)
            else:
                skipped_missing += 1
        if len(rows) < batch_limit:
            break

    start = (page - 1) * per_page
    end = start + per_page
    return {
        "items": valid_items[start:end],
        "pagination": {
            "page": page,
            "per_page": per_page,
            "has_more": len(valid_items) > end or scanned >= scan_limit,
            "skipped_missing": skipped_missing,
            "scanned": scanned,
            "scope": "hot" if scope in {"hot", "popular"} else "all",
            "hot_download_threshold": HOT_AUDIO_DOWNLOAD_THRESHOLD,
            "order": "published_desc",
        },
    }


def audio_meta_from_zsxq_attachment(asset_id: str) -> Dict[str, Any]:
    asset_id = str(asset_id or "").strip()
    if not asset_id:
        raise ValueError("请选择一个已同步音频")
    row = (
        db.session.query(
            ZsxqAttachment.id.label("attachment_id"),
            ZsxqAttachment.name.label("attachment_name"),
            ZsxqAttachment.download_count.label("download_count"),
            ZsxqAttachment.local_path.label("local_path"),
            ZsxqAttachment.file_ext.label("file_ext"),
            ZsxqAttachment.updated_at.label("attachment_updated_at"),
            ZsxqAttachment.created_at.label("attachment_created_at"),
            ZsxqAttachment.group_id.label("group_id"),
            ZsxqAttachment.tab_id.label("tab_id"),
            ZsxqAttachment.topic_id.label("topic_id"),
            ZsxqTopic.title.label("topic_title"),
            _topic_content_preview_projection(),
            ZsxqTopic.tab_label.label("tab_label"),
            ZsxqTopic.author_name.label("topic_author"),
            ZsxqTopic.create_time.label("topic_create_time"),
            ZsxqTopic.first_synced_at.label("topic_first_synced_at"),
            ZsxqTopic.last_synced_at.label("topic_last_synced_at"),
        )
        .outerjoin(
            ZsxqTopic,
            (ZsxqTopic.group_id == ZsxqAttachment.group_id)
            & (ZsxqTopic.tab_id == ZsxqAttachment.tab_id)
            & (ZsxqTopic.topic_id == ZsxqAttachment.topic_id),
        )
        .filter(ZsxqAttachment.id == asset_id)
        .filter(ZsxqAttachment.download_status == "downloaded")
        .filter(ZsxqAttachment.local_path.isnot(None))
        .filter(_audio_asset_filter())
        .first()
    )
    item = _audio_asset_row_payload(row) if row else None
    if not item:
        raise ValueError("音频不存在、未下载或本地文件缺失")
    try:
        item["duration_seconds"] = probe_media_duration(item["path"])
    except Exception:
        item["duration_seconds"] = 0
    return item


def _resume_waiting_child_lightweight(task_id: str, wait_hint: Any) -> Optional[bool]:
    if not isinstance(wait_hint, dict):
        return None

    label = str(wait_hint.get("label") or "").strip()
    child_id = str(wait_hint.get("child_id") or "").strip()
    expected_token = str(wait_hint.get("updated_at") or "").strip()
    if not label or not child_id or not expected_token:
        return None

    parent = _load_task(task_id, *TASK_WAITING_STATUS_COLUMNS)
    if not parent:
        raise Mp3VideoWorkflowError(f"MP3 视频编排任务不存在: {task_id}")
    if parent.status == "canceled":
        return False
    if parent.status == "completed":
        return True
    if (
        str(parent.waiting_child_label or "") != label
        or str(parent.waiting_child_id or "") != child_id
    ):
        return None
    if str(parent.waiting_child_updated_at or "") != expected_token:
        logger.info(
            "Ignore stale MP3 child wait continuation: parent=%s child=%s token=%s current=%s",
            task_id,
            child_id,
            expected_token,
            parent.waiting_child_updated_at,
        )
        return True
    parent_progress = int(parent.progress or 0)

    child = _load_task(child_id, *TASK_PUBLIC_STATUS_COLUMNS)
    if not child:
        return None

    requeued = False
    if child.status not in TERMINAL_STATUSES:
        child = _publisher_child_wait_snapshot(child)
        repair_due = _publisher_child_repair_due(child)
        release_db_session_quietly()
        disk_video = _publisher_child_disk_video_path(child)
        if disk_video:
            recovery_child = _load_task(child_id, *TASK_PUBLIC_STATUS_COLUMNS)
            if recovery_child:
                child = _recover_completed_publisher_child_from_disk(
                    recovery_child,
                    video_path=disk_video,
                )
        if child and child.status not in TERMINAL_STATUSES and repair_due:
            full_child = _load_task(child_id, *TASK_PUBLIC_COLUMNS)
            if full_child:
                full_child, requeued = maybe_requeue_stale_publisher_child(full_child, log=logger)
                child = _load_task(child_id, *TASK_PUBLIC_STATUS_COLUMNS) or full_child or child

    child_status = str(child.status or "")
    child_step = str(child.current_step or "")
    if child_status in TERMINAL_STATUSES:
        return None

    child = _publisher_child_wait_snapshot(child)
    release_db_session_quietly()
    celery_failure = _celery_failure_message(child)
    if celery_failure:
        full_child = _load_task(child_id, *TASK_PUBLIC_COLUMNS)
        if full_child:
            _mark_child_failed(full_child, celery_failure)
            db.session.expire_all()
        return None

    defer_count = int(wait_hint.get("defer_count") or wait_hint.get("count") or 0) + 1
    max_defers = _parent_max_defers(label)
    if defer_count > max_defers:
        raise RuntimeError(f"等待子任务超过最大续跑次数: {label} {child_id}")

    delay = _parent_retry_delay_seconds(label)
    next_token = datetime.utcnow().isoformat() + "Z"
    next_hint = {
        **wait_hint,
        "status": child_status,
        "step": child_step,
        "retry_delay_seconds": delay,
        "defer_count": defer_count,
        "max_defers": max_defers,
        "updated_at": next_token,
    }
    next_progress = max(parent_progress, 80 if label == "landscape_video" else 92)

    def persist_wait_projection():
        updated = (
            PublicationTask.query
            .filter(PublicationTask.id == str(task_id))
            .filter(PublicationTask.status.notin_(TERMINAL_STATUSES))
            .filter(PublicationTask.waiting_child_id == child_id)
            .filter(PublicationTask.waiting_child_updated_at == expected_token)
            .update(
                {
                    PublicationTask.current_step: _safe_step_code(f"{label}_waiting"),
                    PublicationTask.progress: next_progress,
                    PublicationTask.waiting_child_status: child_status,
                    # _db_retry removes the failed SQLAlchemy session before
                    # invoking this closure again. Never dereference the ORM
                    # child from inside a retryable operation: it is detached
                    # after the first connection failure. Use the scalar
                    # snapshot captured above instead.
                    PublicationTask.waiting_child_step: child_step,
                    PublicationTask.waiting_child_updated_at: next_token,
                    PublicationTask.updated_at: datetime.utcnow(),
                },
                synchronize_session=False,
            )
        )
        db.session.commit()
        return updated

    updated = _db_retry(persist_wait_projection, f"advance child wait projection {task_id}:{child_id}")
    if not updated:
        logger.info(
            "Skip duplicate MP3 child wait continuation after compare-and-set: parent=%s child=%s",
            task_id,
            child_id,
        )
        return True

    try:
        celery_app.send_task(
            "tasks.mp3_video_workflow.process_job",
            args=[str(task_id), next_hint],
            queue=QUEUE_NAME,
            countdown=delay,
        )
    except Exception:
        logger.exception(
            "Failed to enqueue lightweight MP3 child wait continuation: parent=%s child=%s",
            task_id,
            child_id,
        )

        def restore_wait_token():
            PublicationTask.query.filter(
                PublicationTask.id == str(task_id),
                PublicationTask.waiting_child_updated_at == next_token,
            ).update(
                {
                    PublicationTask.waiting_child_updated_at: expected_token,
                    PublicationTask.updated_at: datetime.utcnow(),
                },
                synchronize_session=False,
            )
            db.session.commit()

        _db_retry(restore_wait_token, f"restore child wait token {task_id}:{child_id}")
        return None

    logger.info(
        "MP3 child wait continued without JSON payload read: parent=%s child=%s status=%s defer=%s/%s%s",
        task_id,
        child_id,
        child_status,
        defer_count,
        max_defers,
        " requeued" if requeued else "",
    )
    return True


@_workflow_result_scope
def process_job(task_id: str, wait_hint: Optional[Dict[str, Any]] = None) -> bool:
    lightweight_result = _resume_waiting_child_lightweight(task_id, wait_hint)
    if lightweight_result is not None:
        return lightweight_result

    task = _load_task(task_id, *TASK_WORKFLOW_COLUMNS)
    if not task:
        raise Mp3VideoWorkflowError(f"MP3 视频编排任务不存在: {task_id}")
    if not isinstance(task.params, dict) or task.params.get("feature") != FEATURE_NAME:
        raise Mp3VideoWorkflowError(f"无效的 MP3 视频编排任务参数: {task_id}")
    if task.status == "canceled":
        return False
    if task.status == "completed":
        return True

    workflow = task.params.get(FEATURE_NAME) or {}
    generate_subtitles = _workflow_generate_subtitles(workflow, task.result)
    audio = workflow.get("audio") if isinstance(workflow.get("audio"), dict) else {}
    task_key = task.id
    audio_path = _resolve_project_path(audio.get("path"))
    if not audio_path or not audio_path.exists():
        _mark_failed(task, f"音频文件不存在: {audio.get('path')}")
        _queue_notification(task_key, "mp3_video_failed", f"音频文件不存在: {audio.get('path')}")
        return False

    user_id = task.user_id or (task.params or {}).get("user_id")
    created_at = task.created_at or datetime.utcnow()
    _start_task(task)
    _update_result(task_key, {"generate_subtitles": generate_subtitles})
    _queue_notification(task_key, "mp3_video_started", "MP3 视频编排已启动")

    try:
        image_path = artifact_path_for_job(task_key, "gemini_infographic", "png", created_at)
        gemini_result_path = artifact_path_for_job(task_key, "gemini_result", "json", created_at)
        if _workflow_uses_direct_ds_response(workflow):
            image_path = artifact_path_for_job(task_key, "direct_ds_infographic", "png", created_at)
            direct_result_path = artifact_path_for_job(task_key, "direct_ds_result", "json", created_at)
            existing = _fresh_result(task_key)
            gemini_result, first_response, infographic = _reusable_text_source_result(existing, fallback_image_path=image_path)
            if gemini_result:
                _emit(task_key, "direct_ds_reused", 54, "复用已粘贴的 DS 返回值")
                _update_result(task_key, {
                    "input_source": "ds_response",
                    "source": _public_text_source_result(gemini_result),
                    "initial_response_text": first_response,
                    "infographic": _public_artifact(infographic, "DS 文本信息图"),
                })
            else:
                _emit(task_key, "direct_ds_response", 22, "读取手动粘贴的 DS 返回值")
                gemini_result = _generate_from_direct_ds_response(
                    task_id=task_key,
                    workflow=workflow,
                    audio=audio,
                    output_path=direct_result_path,
                )
                first_response = gemini_result.get("initial_response_text") or gemini_result.get("response_text") or ""
                infographic = {"path": image_path, "label": "DS 文本信息图"}
                _update_result(task_key, {
                    "input_source": "ds_response",
                    "source": _public_text_source_result(gemini_result),
                    "initial_response_text": first_response,
                    "infographic": _public_artifact(infographic, "DS 文本信息图"),
                })
            _queue_notification(task_key, "mp3_video_gemini_completed", "手动 DS 返回值已载入")
        elif _workflow_uses_transcript(workflow):
            image_path = artifact_path_for_job(task_key, "transcript_infographic", "png", created_at)
            transcript_result_path = artifact_path_for_job(task_key, "transcript_deepseek_result", "json", created_at)
            existing = _fresh_result(task_key)
            gemini_result, first_response, infographic = _reusable_text_source_result(existing, fallback_image_path=image_path)
            if gemini_result:
                _emit(task_key, "transcript_source_reused", 54, "复用已完成的 IMA 全文 DS 处理结果")
                _update_result(task_key, {
                    "input_source": "ima_transcript",
                    "source": _public_text_source_result(gemini_result),
                    "initial_response_text": first_response,
                    "infographic": _public_artifact(infographic, "DS 文本信息图"),
                })
            else:
                _queue_notification(task_key, "mp3_video_gemini_started", "使用 IMA 全文调用 DS v4 flash")
                _emit(task_key, "transcript_deepseek", 18, "使用 IMA 全文调用 DS v4 flash")
                gemini_result = _generate_from_transcript(
                    task_id=task_key,
                    workflow=workflow,
                    audio=audio,
                    output_path=transcript_result_path,
                )
                first_response = gemini_result.get("initial_response_text") or gemini_result.get("response_text") or ""
                infographic = {"path": image_path, "label": "DS 文本信息图"}
                _update_result(task_key, {
                    "input_source": "ima_transcript",
                    "source": _public_text_source_result(gemini_result),
                    "initial_response_text": first_response,
                    "infographic": _public_artifact(infographic, "DS 文本信息图"),
                })
            _queue_notification(task_key, "mp3_video_gemini_completed", "IMA 全文 DS 处理结果已生成")
        else:
            existing = _clear_invalid_gemini_shell_result(task_key, _fresh_result(task_key))
            gemini_result, first_response, infographic = _reusable_gemini_result(existing, fallback_image_path=image_path)
            if gemini_result:
                _emit(task_key, "gemini_reused", 54, "复用已完成的 Gemini 第一轮返回和横向信息图")
                _update_result(task_key, {
                    "input_source": "gemini_audio",
                    "gemini": _public_gemini_result(gemini_result),
                    "initial_response_text": first_response,
                    "infographic": _public_artifact(infographic, "Gemini 横向信息图"),
                })
            else:
                _queue_notification(task_key, "mp3_video_gemini_started", "上传 MP3 到 Gemini Gem 并等待第一轮返回")
                gemini_result = _generate_gemini_with_retries(
                    task_id=task_key,
                    audio_path=audio_path,
                    output_path=gemini_result_path,
                    workflow=workflow,
                    image_output_path=image_path,
                )
                first_response = gemini_result.get("initial_response_text") or gemini_result.get("response_text") or ""
                infographic = gemini_result.get("image") or {"path": image_path}
                _update_result(task_key, {
                    "input_source": "gemini_audio",
                    "gemini": _public_gemini_result(gemini_result),
                    "initial_response_text": first_response,
                    "infographic": _public_artifact(infographic, "Gemini 横向信息图"),
                })
            _queue_notification(task_key, "mp3_video_gemini_completed", "Gemini 第一轮返回和横向信息图已生成")

        existing = _fresh_result(task_key)
        plan = _reusable_plan(existing)
        if plan and (_workflow_uses_direct_ds_response(workflow) or _workflow_uses_transcript(workflow)):
            try:
                _validate_strict_ds_plan(
                    plan,
                    source_label="手动 DS 返回值" if _workflow_uses_direct_ds_response(workflow) else "IMA 全文 DS 返回值",
                )
            except Mp3VideoWorkflowError:
                # A previous attempt may have persisted a model-rewritten plan
                # immediately before strict validation rejected it. Never keep
                # reusing that poisoned intermediate result on every retry.
                plan = None
                _emit(task_key, "invalid_ds_plan_rebuild", 58, "旧规划未通过严格校验，按已验收 DS 原文重新解析")
        if plan:
            audio = existing.get("audio") if isinstance(existing.get("audio"), dict) else audio
            _emit(task_key, "deepseek_plan_reused", 58, "复用已完成的 DS v4 flash 标题和音频时间戳规划")
        else:
            if _workflow_uses_direct_ds_response(workflow):
                _emit(task_key, "direct_ds_plan", 58, "本地解析 DS 返回值中的标题和音频时间戳")
            else:
                _emit(task_key, "deepseek_plan", 58, "DS v4 flash 选择标题和音频时间戳")
            duration = float(audio.get("duration_seconds") or 0) or probe_media_duration(str(audio_path))
            audio["duration_seconds"] = duration
            if _workflow_uses_direct_ds_response(workflow):
                plan = plan_from_direct_ds_response(first_response, audio=audio)
            else:
                if _workflow_uses_transcript(workflow):
                    _validate_ds_response_contract(first_response, source_label="IMA 全文 DS 返回值")
                    # IMA has already returned the seven required DS sections.
                    # Parse that accepted response deterministically instead of
                    # asking a second model to rewrite it; the rewrite can drop
                    # valid cover fields and create a false strict-plan failure.
                    plan = _local_plan_from_direct_ds_response(first_response, audio=audio)
                    plan["provider"] = "ima_transcript_local"
                else:
                    plan = plan_from_first_response(first_response, audio=audio)
            _update_result(task_key, {"plan": plan, "audio": audio})
        plan = _apply_workflow_audio_range_override(plan, workflow)
        if workflow.get("audio_range_override"):
            _update_result(task_key, {"plan": plan})
        if _workflow_uses_direct_ds_response(workflow) or _workflow_uses_transcript(workflow):
            _validate_strict_ds_plan(
                plan,
                source_label="手动 DS 返回值" if _workflow_uses_direct_ds_response(workflow) else "IMA 全文 DS 返回值",
            )
            draw_transcript_infographic(image_path, first_response, audio=audio, plan=plan)
            infographic = _artifact_meta(
                image_path,
                "DS 金句观点信息图",
                renderer=(
                    "pil:direct-ds-structured-infographic"
                    if _workflow_uses_direct_ds_response(workflow)
                    else "pil:transcript-structured-infographic"
                ),
            )
            _update_result(task_key, {
                "infographic": _public_artifact(infographic, "DS 金句观点信息图"),
            })
        _queue_notification(
            task_key,
            "mp3_video_plan_completed",
            "已从手动 DS 返回值解析标题、封面文案和音频时间戳规划"
            if _workflow_uses_direct_ds_response(workflow)
            else "DS v4 flash 已完成标题、封面文案和音频时间戳规划",
        )
        _raise_if_parent_canceled(task_key)

        existing = _fresh_result(task_key)
        artifacts = existing.get("artifacts") if isinstance(existing.get("artifacts"), dict) else {}
        if _visual_artifacts_ready(artifacts):
            _emit(task_key, "generate_visual_assets_reused", 68, "复用已生成的四张封皮/背景图")
        else:
            _emit(task_key, "generate_visual_assets", 68, "生成四张封皮/背景图")
            artifacts = generate_visual_artifacts(
                task_key,
                plan=plan,
                infographic_path=infographic.get("path") or image_path,
                created_at=created_at,
            )
            _update_result(task_key, {"artifacts": artifacts})
        _queue_notification(task_key, "mp3_video_visuals_completed", "四张封皮/背景图已生成")
        _raise_if_parent_canceled(task_key)

        render_mode = _workflow_render_mode(workflow)
        if render_mode == "keyframes":
            existing = _fresh_result(task_key)
            keyframes = existing.get("keyframes") if isinstance(existing.get("keyframes"), dict) else {}
            if _keyframe_artifacts_ready(keyframes):
                _emit(task_key, "video_keyframes_reused", 96, "复用已生成的视频关键帧预览")
            else:
                _emit(task_key, "generate_video_keyframes", 90, "生成横屏/竖屏视频关键帧预览")
                keyframes = generate_video_keyframes(
                    task_key,
                    plan=plan,
                    artifacts=artifacts,
                    audio=audio,
                    source_text=first_response,
                    created_at=created_at,
                )
            artifacts = {**artifacts, **keyframes}
            _update_result(task_key, {
                "render_mode": render_mode,
                "generate_subtitles": generate_subtitles,
                "artifacts": artifacts,
                "keyframes": keyframes,
                "landscape_video": {"status": "skipped", "reason": "keyframes_preview"},
                "vertical_video": {"status": "skipped", "reason": "keyframes_preview"},
            })
            _raise_if_parent_canceled(task_key)
            output_path = storage_path_for_job(task_key, created_at)
            fresh = _load_task(task_key, *TASK_STATUS_COLUMNS)
            if not fresh or fresh.status == "canceled":
                raise ParentTaskCanceled("父编排任务已取消")
            final_result = _without_waiting_state(_fresh_result(task_key))
            final_result.update({
                "output_path": output_path,
                "completed_at": datetime.utcnow().isoformat() + "Z",
            })
            Path(output_path).write_text(json.dumps({
                "workflow_task_id": task_key,
                "input_source": workflow.get("input_source") or ("ds_response" if _workflow_uses_direct_ds_response(workflow) else ("ima_transcript" if _workflow_uses_transcript(workflow) else "gemini_audio")),
                "render_mode": render_mode,
                "generate_subtitles": generate_subtitles,
                "audio": audio,
                "gemini": _public_gemini_result(gemini_result),
                "source": _public_text_source_result(gemini_result),
                "plan": plan,
                "artifacts": artifacts,
                "keyframes": keyframes,
                "completed_at": final_result["completed_at"],
            }, ensure_ascii=False, indent=2), encoding="utf-8")

            fresh.status = "completed"
            fresh.current_step = "keyframes_ready"
            fresh.progress = 100
            fresh.completed_at = datetime.utcnow()
            fresh.error_message = None
            fresh.result = final_result
            flag_modified(fresh, "result")
            db.session.commit()
            _replace_cached_workflow_result(task_key, final_result)
            _queue_notification(task_key, "mp3_video_completed", "MP3 视频关键帧预览已生成，未合成完整视频")
            return True

        _raise_if_parent_canceled(task_key)
        existing = _fresh_result(task_key)
        audio_segment = audio_segment_from_plan(audio, plan)
        audio_signature = _audio_segment_signature(audio_segment)
        force_video_regenerate_at = _workflow_force_video_regenerate_at(
            workflow,
            existing,
        )
        force_video_regenerate = force_video_regenerate_at is not None
        landscape_summary, landscape_task = _reusable_video_summary(
            existing,
            "landscape_video",
            parent_id=task_key,
            generate_subtitles=generate_subtitles,
            audio_signature=audio_signature,
            created_not_before=force_video_regenerate_at,
        )
        if landscape_summary:
            landscape_child_summary = _task_public_summary(landscape_task) if landscape_task else None
            _emit(task_key, "landscape_video_reused", 90, "复用已完成的小君君横屏视频")
            if landscape_child_summary:
                _record_child(task_key, "landscape_video", landscape_child_summary)
            _update_result(task_key, {"landscape_video": landscape_summary})
        else:
            landscape_task = _active_child_from_result(
                existing,
                "landscape_video",
                generate_subtitles=generate_subtitles,
                audio_signature=audio_signature,
                created_not_before=force_video_regenerate_at,
            )
            if landscape_task:
                landscape_task_key = str(landscape_task.id)
                _sync_publisher_video_task_title(landscape_task, _publisher_video_title(plan, role="xiaojunjun_landscape"))
                landscape_child_summary = _task_public_summary(landscape_task)
                _emit(task_key, "landscape_video_wait_existing", 80, "继续等待已提交的小君君横屏视频")
                _record_child(task_key, "landscape_video", landscape_child_summary)
            else:
                _raise_if_parent_canceled(task_key)
                _emit(task_key, "submit_landscape_video", 78, "提交小君君横屏视频合成任务")
                landscape_task = create_publisher_video_task(
                    parent_id=task_key,
                    user_id=user_id,
                    audio=audio,
                    plan=plan,
                    image_path=artifacts["xiaojunjun_background"]["path"],
                    role="xiaojunjun_landscape",
                    aspect_ratio="16:9",
                    generate_subtitles=generate_subtitles,
                    force_new=force_video_regenerate,
                    created_not_before=force_video_regenerate_at,
                )
                landscape_task_key = str(landscape_task.id)
                _record_child(task_key, "landscape_video", _task_public_summary(landscape_task))
                _queue_notification(task_key, "mp3_video_landscape_submitted", "小君君横屏视频合成任务已提交")
            landscape_task = _wait_child(task_key, landscape_task_key, "landscape_video", start_progress=80, end_progress=90)
            if landscape_task.status != "completed":
                raise RuntimeError(f"横屏视频子任务失败: {landscape_task.error_message or (landscape_task.result or {}).get('error')}")
            landscape_summary = _publisher_result_summary(landscape_task)
            _update_result(task_key, {"landscape_video": landscape_summary})
        _queue_notification(task_key, "mp3_video_landscape_completed", "小君君横屏视频已完成")

        _raise_if_parent_canceled(task_key)
        existing = _fresh_result(task_key)
        vertical_summary, vertical_task = _reusable_video_summary(
            existing,
            "vertical_video",
            parent_id=task_key,
            generate_subtitles=generate_subtitles,
            audio_signature=audio_signature,
            created_not_before=force_video_regenerate_at,
        )
        if vertical_summary:
            vertical_child_summary = _task_public_summary(vertical_task) if vertical_task else None
            _emit(task_key, "vertical_video_reused", 99, "复用已完成的此刻此声竖屏视频")
            if vertical_child_summary:
                _record_child(task_key, "vertical_video", vertical_child_summary)
            _update_result(task_key, {"vertical_video": vertical_summary})
        else:
            vertical_task = _active_child_from_result(
                existing,
                "vertical_video",
                generate_subtitles=generate_subtitles,
                audio_signature=audio_signature,
                created_not_before=force_video_regenerate_at,
            )
            if vertical_task:
                vertical_task_key = str(vertical_task.id)
                _sync_publisher_video_task_title(vertical_task, _publisher_video_title(plan, role="ciketc_vertical"))
                vertical_child_summary = _task_public_summary(vertical_task)
                _emit(task_key, "vertical_video_wait_existing", 92, "继续等待已提交的此刻此声竖屏视频")
                _record_child(task_key, "vertical_video", vertical_child_summary)
            else:
                _raise_if_parent_canceled(task_key)
                _emit(task_key, "submit_vertical_video", 91, "提交此刻此声竖屏视频合成任务")
                vertical_task = create_publisher_video_task(
                    parent_id=task_key,
                    user_id=user_id,
                    audio=audio,
                    plan=plan,
                    image_path=artifacts["audio_background"]["path"],
                    role="ciketc_vertical",
                    aspect_ratio="9:16",
                    generate_subtitles=generate_subtitles,
                    force_new=force_video_regenerate,
                    created_not_before=force_video_regenerate_at,
                )
                vertical_task_key = str(vertical_task.id)
                _record_child(task_key, "vertical_video", _task_public_summary(vertical_task))
                _queue_notification(task_key, "mp3_video_vertical_submitted", "此刻此声竖屏视频合成任务已提交")
            vertical_task = _wait_child(task_key, vertical_task_key, "vertical_video", start_progress=92, end_progress=99)
            if vertical_task.status != "completed":
                raise RuntimeError(f"竖屏视频子任务失败: {vertical_task.error_message or (vertical_task.result or {}).get('error')}")
            vertical_summary = _publisher_result_summary(vertical_task)
            _update_result(task_key, {"vertical_video": vertical_summary})
        _queue_notification(task_key, "mp3_video_vertical_completed", "此刻此声竖屏视频已完成")

        _raise_if_parent_canceled(task_key)
        output_path = storage_path_for_job(task_key, created_at)
        fresh = _load_task(task_key, *TASK_STATUS_COLUMNS)
        if not fresh or fresh.status == "canceled":
            raise ParentTaskCanceled("父编排任务已取消")
        final_result = _without_waiting_state(_fresh_result(task_key))
        final_result.update({
            "output_path": output_path,
            "completed_at": datetime.utcnow().isoformat() + "Z",
        })
        Path(output_path).write_text(json.dumps({
            "workflow_task_id": task_key,
            "input_source": workflow.get("input_source") or ("ds_response" if _workflow_uses_direct_ds_response(workflow) else ("ima_transcript" if _workflow_uses_transcript(workflow) else "gemini_audio")),
            "render_mode": _workflow_render_mode(workflow),
            "generate_subtitles": generate_subtitles,
            "audio": audio,
            "gemini": _public_gemini_result(gemini_result),
            "source": _public_text_source_result(gemini_result),
            "plan": plan,
            "artifacts": artifacts,
            "landscape_video": landscape_summary,
            "vertical_video": vertical_summary,
            "completed_at": final_result["completed_at"],
        }, ensure_ascii=False, indent=2), encoding="utf-8")

        fresh.status = "completed"
        fresh.current_step = "done"
        fresh.progress = 100
        fresh.completed_at = datetime.utcnow()
        fresh.error_message = None
        fresh.result = final_result
        flag_modified(fresh, "result")
        db.session.commit()
        _replace_cached_workflow_result(task_key, final_result)
        _queue_notification(task_key, "mp3_video_completed", "MP3 视频编排完成，横屏和竖屏视频均已生成")
        return True
    except ParentTaskCanceled:
        logger.info("MP3 video workflow stopped because parent task is canceled: %s", task_id)
        return False
    except ChildTaskStillRunning as exc:
        _defer_parent_retry(task_id, exc)
        logger.info("MP3 video workflow deferred: %s", exc)
        return True
    except OperationalError:
        try:
            db.session.rollback()
        except Exception:
            pass
        try:
            db.session.remove()
        except Exception:
            pass
        logger.warning("MP3 video workflow hit a transient database error; letting Celery retry: %s", task_id, exc_info=True)
        raise
    except Exception as exc:
        mark_job_failed_safely(task_id, str(exc), notify=True)
        logger.error("MP3 video workflow failed: %s", exc, exc_info=True)
        return False


def _generate_gemini_with_retries(
    *,
    task_id: str,
    audio_path: Path,
    output_path: str,
    workflow: Dict[str, Any],
    image_output_path: str,
) -> Dict[str, Any]:
    retry_limit = _retry_limit(GEMINI_RETRY_LIMIT_ENV_NAME, DEFAULT_GEMINI_RETRY_LIMIT)
    max_attempts = retry_limit + 1
    last_error = None

    for attempt in range(1, max_attempts + 1):
        retry_suffix = "" if attempt == 1 else f"（自动重试 {attempt - 1}/{retry_limit}）"
        _emit(task_id, "gemini_upload_audio", 10, f"上传 MP3 到 Gemini Gem 并等待第一轮返回{retry_suffix}")
        _record_retry_attempt(task_id, "gemini", attempt, max_attempts, "running")

        def progress_callback(event: Dict[str, Any]) -> None:
            message = event.get("message") or "Gemini 自动化执行中"
            if attempt > 1:
                message = f"第 {attempt}/{max_attempts} 次尝试：{message}"
            _emit(
                task_id,
                f"gemini_{event.get('step') or 'running'}",
                min(54, 8 + int((event.get("progress") or 0) * 0.46)),
                message,
                result=event.get("result"),
            )

        try:
            gemini_result = GeminiBrowserWorker(gemini_options_from_env()).generate_from_file(
                job_id=task_id,
                file_path=str(audio_path),
                output_path=output_path,
                prompt=initial_gemini_prompt(workflow),
                target_url=(workflow.get("gemini_url") or DEFAULT_GEMINI_URL),
                image_followup_prompt=(workflow.get("image_prompt") or INFOGRAPHIC_PROMPT),
                image_output_path=image_output_path,
                progress_callback=progress_callback,
            )
        except Exception as exc:
            last_error = str(exc)
            _record_retry_attempt(task_id, "gemini", attempt, max_attempts, "failed", error=last_error)
            if attempt < max_attempts and _is_retryable_gemini_error(last_error):
                _emit(
                    task_id,
                    "gemini_retry",
                    8,
                    f"Gemini 自动化失败，准备自动重试 {attempt}/{retry_limit}: {_compact_error(last_error)}",
                )
                continue
            raise

        _record_retry_attempt(task_id, "gemini", attempt, max_attempts, "completed")
        return gemini_result

    raise Mp3VideoWorkflowError(f"Gemini 自动化失败: {last_error or '未知错误'}")


def _workflow_uses_transcript(workflow: Dict[str, Any]) -> bool:
    source = str(workflow.get("input_source") or workflow.get("source_mode") or "").strip().lower()
    if source in {"ima_transcript", "transcript", "deepseek_transcript"}:
        return True
    return bool(str(workflow.get("transcript_text") or "").strip())


def _workflow_uses_direct_ds_response(workflow: Dict[str, Any]) -> bool:
    source = str(workflow.get("input_source") or workflow.get("source_mode") or "").strip().lower()
    if source in {"ds_response", "direct_ds", "manual_ds", "ds_result", "deepseek_response"}:
        return True
    return bool(str(workflow.get("ds_response_text") or workflow.get("direct_ds_response") or "").strip())


def _workflow_render_mode(workflow: Dict[str, Any]) -> str:
    mode = str(workflow.get("render_mode") or workflow.get("output_mode") or "").strip().lower()
    if mode in {"keyframe", "keyframes", "preview", "preview_keyframes", "frames"}:
        return "keyframes"
    if workflow.get("keyframes_only") is True or workflow.get("preview_only") is True:
        return "keyframes"
    return "full_video"


def _workflow_force_video_regenerate(workflow: Dict[str, Any], result: Optional[Dict[str, Any]] = None) -> bool:
    return _workflow_force_video_regenerate_at(workflow, result) is not None


def _apply_workflow_audio_range_override(
    plan: Dict[str, Any],
    workflow: Dict[str, Any],
) -> Dict[str, Any]:
    plan = dict(plan or {})
    workflow = workflow if isinstance(workflow, dict) else {}
    override = workflow.get("audio_range_override")
    if not isinstance(override, dict):
        return plan
    start = _parse_seconds(override.get("start_seconds"), default=None)
    end = _parse_seconds(override.get("end_seconds"), default=None)
    if start is None or start < 0 or end is None or end <= start:
        raise Mp3VideoWorkflowError("用户重生成音频截取范围无效，请重新设置起止时间")
    audio_plan = dict(plan.get("audio") or {})
    audio_plan["start_seconds"] = round(float(start), 3)
    audio_plan["end_seconds"] = round(float(end), 3)
    plan["audio"] = audio_plan
    return plan


def _workflow_force_video_regenerate_at(
    workflow: Dict[str, Any],
    result: Optional[Dict[str, Any]] = None,
) -> Optional[datetime]:
    workflow = workflow if isinstance(workflow, dict) else {}
    result = result if isinstance(result, dict) else {}
    raw = workflow.get("force_video_regenerate_at") or result.get(
        "force_video_regenerate_at"
    )
    text_value = str(raw or "").strip()
    if not text_value:
        return None
    try:
        parsed = datetime.fromisoformat(text_value.replace("Z", "+00:00"))
    except ValueError:
        logger.error(
            "Ignore invalid force_video_regenerate_at value: %s",
            text_value,
        )
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _truthy(value: Any, default: bool = True) -> bool:
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


def _workflow_generate_subtitles(workflow: Dict[str, Any], result: Optional[Dict[str, Any]] = None) -> bool:
    for key in ("generate_subtitles", "subtitles_enabled", "enable_subtitles"):
        if key in workflow:
            return _truthy(workflow.get(key), default=False)
    metadata = workflow.get("metadata") if isinstance(workflow.get("metadata"), dict) else {}
    for key in ("generate_subtitles", "subtitles_enabled", "enable_subtitles"):
        if key in metadata:
            return _truthy(metadata.get(key), default=False)
    result = result if isinstance(result, dict) else {}
    for key in ("generate_subtitles", "subtitles_enabled", "enable_subtitles"):
        if key in result:
            return _truthy(result.get(key), default=False)
    result_metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
    for key in ("generate_subtitles", "subtitles_enabled", "enable_subtitles"):
        if key in result_metadata:
            return _truthy(result_metadata.get(key), default=False)
    return False


def _generate_from_direct_ds_response(
    *,
    task_id: str,
    workflow: Dict[str, Any],
    audio: Dict[str, Any],
    output_path: str,
) -> Dict[str, Any]:
    response_text = str(workflow.get("ds_response_text") or workflow.get("direct_ds_response") or "").strip()
    if len(response_text) < 20:
        raise Mp3VideoWorkflowError("DS 返回值为空或过短")
    result = {
        "provider": "user_supplied_ds",
        "input_source": "ds_response",
        "model": "manual",
        "response_text": response_text,
        "initial_response_text": response_text,
        "transcript_chars": 0,
        "audio": {
            "filename": audio.get("filename") or audio.get("name"),
            "attachment_id": audio.get("attachment_id"),
            "duration_seconds": audio.get("duration_seconds"),
        },
        "output_path": output_path,
        "completed_at": datetime.utcnow().isoformat() + "Z",
    }
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    _emit(task_id, "direct_ds_response_loaded", 54, "手动 DS 返回值已载入")
    return result


def _generate_from_transcript(
    *,
    task_id: str,
    workflow: Dict[str, Any],
    audio: Dict[str, Any],
    output_path: str,
) -> Dict[str, Any]:
    transcript_text = str(workflow.get("transcript_text") or "").strip()
    if len(transcript_text) < 20:
        raise Mp3VideoWorkflowError("IMA 转录全文为空或过短，无法调用 DS")
    prompt = initial_transcript_prompt(workflow)
    if not prompt:
        raise Mp3VideoWorkflowError("IMA 全文 DS Prompt 为空")

    cache_key, cache_meta = _transcript_deepseek_cache_key(transcript_text, prompt=prompt, audio=audio)
    cache_disabled = _bool_env("MP3_VIDEO_TRANSCRIPT_CACHE_DISABLED", False)
    cache_payload = None if cache_disabled else _load_transcript_deepseek_cache(cache_key)
    cache_status = "hit" if cache_payload else "miss"
    cache_path = str(_transcript_deepseek_cache_path(cache_key))
    if cache_payload:
        response_text = str(cache_payload.get("response_text") or "").strip()
        _emit(task_id, "transcript_deepseek_cache_hit", 54, "命中 IMA 全文 DS 缓存，跳过重复调用")
    elif not cache_disabled:
        previous_payload = _find_recent_transcript_deepseek_result(
            transcript_text,
            prompt=prompt,
            audio=audio,
            exclude_task_id=task_id,
        )
        if previous_payload:
            response_text = str(previous_payload.get("response_text") or "").strip()
            cache_status = "previous_task"
            _write_transcript_deepseek_cache(cache_key, cache_meta, response_text, source="previous_task")
            _emit(task_id, "transcript_deepseek_cache_seeded", 54, "复用近期同音频 IMA 全文 DS 结果，跳过重复调用")
        else:
            _emit(task_id, "transcript_deepseek_request", 26, "DS v4 flash 正在处理 IMA 转录全文")
            response_text = deepseek_transcript_response(transcript_text, prompt=prompt, audio=audio)
            _write_transcript_deepseek_cache(cache_key, cache_meta, response_text, source="deepseek")
    else:
        _emit(task_id, "transcript_deepseek_request", 26, "DS v4 flash 正在处理 IMA 转录全文")
        response_text = deepseek_transcript_response(transcript_text, prompt=prompt, audio=audio)
    if not response_text:
        raise Mp3VideoWorkflowError("DS v4 flash 未返回可用文本")

    result = {
        "provider": "deepseek",
        "input_source": "ima_transcript",
        "model": transcript_model(),
        "prompt": prompt,
        "transcript_chars": len(transcript_text),
        "response_text": response_text,
        "initial_response_text": response_text,
        "cache": {
            "key": cache_key,
            "status": cache_status,
            "path": cache_path if Path(cache_path).exists() else None,
            "version": TRANSCRIPT_DEEPSEEK_CACHE_VERSION,
        },
        "output_path": output_path,
        "completed_at": datetime.utcnow().isoformat() + "Z",
    }
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    _emit(task_id, "transcript_deepseek_completed", 54, "IMA 全文 DS 处理完成")
    return result


def deepseek_transcript_response(transcript_text: str, *, prompt: str, audio: Dict[str, Any]) -> str:
    api_key = os.environ.get("MP3_VIDEO_TRANSCRIPT_API_KEY") or os.environ.get("MP3_VIDEO_PLANNER_API_KEY") or os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        raise Mp3VideoWorkflowError("缺少 MP3_VIDEO_TRANSCRIPT_API_KEY / MP3_VIDEO_PLANNER_API_KEY / DEEPSEEK_API_KEY")
    base_url = os.environ.get("MP3_VIDEO_TRANSCRIPT_BASE_URL") or os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    timeout = _int_env("MP3_VIDEO_TRANSCRIPT_TIMEOUT", _int_env("MP3_VIDEO_PLANNER_TIMEOUT", 180))
    system_prompt = (
        "你是财经音频内容处理助手。严格根据用户给出的 Prompt 和 IMA 转录全文处理，"
        "不要编造事实；如果转录中没有明确时间戳，可根据内容结构给出保守建议。"
    )
    user_prompt = _build_transcript_user_prompt(transcript_text, prompt=prompt, audio=audio)
    response = requests.post(
        f"{base_url.rstrip('/')}/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": transcript_model(),
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.1,
        },
        timeout=timeout,
    )
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        raise Mp3VideoWorkflowError(f"DeepSeek 转录全文处理失败: HTTP {response.status_code} {response.text[:600]}") from exc
    content = response.json()["choices"][0]["message"]["content"]
    return str(content or "").strip()


def _build_transcript_user_prompt(transcript_text: str, *, prompt: str, audio: Dict[str, Any]) -> str:
    compact_transcript = _compact_text(transcript_text, _int_env("MP3_VIDEO_TRANSCRIPT_INPUT_CHARS", 40000))
    replacements = {
        "{{transcript}}": compact_transcript,
        "{transcript}": compact_transcript,
        "{{audio_filename}}": str(audio.get("filename") or audio.get("name") or ""),
        "{audio_filename}": str(audio.get("filename") or audio.get("name") or ""),
    }
    rendered = str(prompt or "")
    for token, value in replacements.items():
        rendered = rendered.replace(token, value)
    if compact_transcript in rendered:
        return rendered
    return "\n".join([
        rendered.strip(),
        "",
        f"音频文件名：{audio.get('filename') or audio.get('name') or ''}",
        f"当前日期：{datetime.now().strftime('%Y-%m-%d')}",
        f"音频总时长秒：{float(audio.get('duration_seconds') or 0):.3f}" if audio.get("duration_seconds") else "音频总时长秒：未知",
        "",
        "【IMA 转录全文】",
        compact_transcript,
    ])


def _transcript_deepseek_cache_key(transcript_text: str, *, prompt: str, audio: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    user_prompt = _build_transcript_user_prompt(transcript_text, prompt=prompt, audio=audio)
    audio_identity = _transcript_audio_cache_identity(audio)
    prompt_hash = hashlib.sha256(user_prompt.encode("utf-8")).hexdigest()
    transcript_hash = hashlib.sha256(str(transcript_text or "").strip().encode("utf-8")).hexdigest()
    payload = {
        "version": TRANSCRIPT_DEEPSEEK_CACHE_VERSION,
        "provider": "deepseek",
        "base_url": _transcript_base_url(),
        "model": transcript_model(),
        "audio": audio_identity,
        "prompt_hash": prompt_hash,
        "transcript_hash": transcript_hash,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    cache_key = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    return cache_key, {
        **payload,
        "key": cache_key,
        "prompt_chars": len(user_prompt),
        "transcript_chars": len(str(transcript_text or "").strip()),
    }


def _transcript_base_url() -> str:
    return (os.environ.get("MP3_VIDEO_TRANSCRIPT_BASE_URL") or os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")).rstrip("/")


def _transcript_audio_cache_identity(audio: Dict[str, Any]) -> Dict[str, Any]:
    path_value = str(audio.get("path") or "").strip()
    resolved_path = ""
    if path_value:
        try:
            resolved = _resolve_project_path(path_value)
            resolved_path = str(resolved) if resolved else path_value
        except Exception:
            resolved_path = path_value
    duration = None
    try:
        duration = round(float(audio.get("duration_seconds") or 0), 3) if audio.get("duration_seconds") else None
    except (TypeError, ValueError):
        duration = None
    return {
        "attachment_id": _clean_line(audio.get("attachment_id")),
        "path": resolved_path,
        "filename": _clean_line(audio.get("filename") or audio.get("name")),
        "duration_seconds": duration,
        "group_id": _clean_line(audio.get("group_id")),
        "tab_id": _clean_line(audio.get("tab_id")),
        "topic_id": _clean_line(audio.get("topic_id")),
    }


def _transcript_deepseek_cache_root() -> Path:
    configured = os.environ.get("MP3_VIDEO_TRANSCRIPT_CACHE_DIR")
    if configured:
        return Path(configured).expanduser()
    return project_root() / "data" / "processed" / FEATURE_NAME / "transcript_deepseek_cache"


def _transcript_deepseek_cache_path(cache_key: str) -> Path:
    safe_key = re.sub(r"[^a-f0-9]+", "", str(cache_key or "").lower())[:64]
    return _transcript_deepseek_cache_root() / f"{safe_key or 'invalid'}.json"


def _load_transcript_deepseek_cache(cache_key: str) -> Optional[Dict[str, Any]]:
    if _bool_env("MP3_VIDEO_TRANSCRIPT_CACHE_DISABLED", False):
        return None
    payload = _load_json_file(_transcript_deepseek_cache_path(cache_key))
    if not isinstance(payload, dict):
        return None
    if payload.get("version") != TRANSCRIPT_DEEPSEEK_CACHE_VERSION or payload.get("key") != cache_key:
        return None
    if not str(payload.get("response_text") or "").strip():
        return None
    return payload


def _write_transcript_deepseek_cache(cache_key: str, cache_meta: Dict[str, Any], response_text: str, *, source: str) -> str:
    if _bool_env("MP3_VIDEO_TRANSCRIPT_CACHE_DISABLED", False):
        return ""
    response_text = str(response_text or "").strip()
    if not response_text:
        return ""
    path = _transcript_deepseek_cache_path(cache_key)
    payload = {
        **cache_meta,
        "version": TRANSCRIPT_DEEPSEEK_CACHE_VERSION,
        "key": cache_key,
        "source": source,
        "response_text": response_text,
        "response_chars": len(response_text),
        "completed_at": datetime.utcnow().isoformat() + "Z",
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(path)
        return str(path)
    except Exception:
        logger.exception("Failed to write MP3 transcript DS cache: %s", path)
        return ""


def _find_recent_transcript_deepseek_result(
    transcript_text: str,
    *,
    prompt: str,
    audio: Dict[str, Any],
    exclude_task_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    lookback_days = max(0, _int_env("MP3_VIDEO_TRANSCRIPT_PREVIOUS_TASK_CACHE_DAYS", 2))
    if lookback_days <= 0:
        return None
    target_text = str(transcript_text or "").strip()
    target_prompt = str(prompt or "").strip()
    target_audio = _transcript_audio_cache_identity(audio)
    since = datetime.utcnow() - timedelta(days=lookback_days)

    def load_candidates():
        return (
            PublicationTask.query
            .options(load_only(PublicationTask.id, PublicationTask.params, PublicationTask.result, PublicationTask.created_at))
            .filter(PublicationTask.feature == FEATURE_NAME)
            .filter(PublicationTask.created_at >= since)
            .order_by(PublicationTask.created_at.desc())
            .limit(_int_env("MP3_VIDEO_TRANSCRIPT_PREVIOUS_TASK_CACHE_LIMIT", 80))
            .all()
        )

    candidates = _db_retry(load_candidates, "find recent transcript DS result")
    today = datetime.now().date()
    for candidate in candidates:
        if exclude_task_id and str(candidate.id) == str(exclude_task_id):
            continue
        if candidate.created_at and candidate.created_at.date() != today:
            continue
        params = candidate.params if isinstance(candidate.params, dict) else {}
        workflow = params.get(FEATURE_NAME) if isinstance(params.get(FEATURE_NAME), dict) else {}
        if not _workflow_uses_transcript(workflow):
            continue
        if str(workflow.get("transcript_text") or "").strip() != target_text:
            continue
        if initial_transcript_prompt(workflow).strip() != target_prompt:
            continue
        candidate_audio = workflow.get("audio") if isinstance(workflow.get("audio"), dict) else {}
        if _transcript_audio_cache_identity(candidate_audio) != target_audio:
            continue
        result = candidate.result if isinstance(candidate.result, dict) else {}
        source = result.get("source") if isinstance(result.get("source"), dict) else {}
        response_text = (
            source.get("response_text")
            or source.get("initial_response_text")
            or result.get("initial_response_text")
            or result.get("source_response_text")
            or ""
        )
        response_text = str(response_text or "").strip()
        if response_text:
            return {
                "response_text": response_text,
                "source_task_id": str(candidate.id),
                "completed_at": source.get("completed_at") or result.get("completed_at"),
            }
    return None


def _record_retry_attempt(
    task_id: str,
    label: str,
    attempt: int,
    max_attempts: int,
    status: str,
    *,
    error: Optional[str] = None,
) -> None:
    result = _fresh_result(task_id)
    attempts = dict(result.get("retry_attempts") or {})
    items = list(attempts.get(label) or [])
    updated_at = datetime.utcnow().isoformat() + "Z"
    entry = {
        "attempt": int(attempt or 1),
        "max_attempts": int(max_attempts or 1),
        "status": status,
        "error": error,
        "updated_at": updated_at,
    }
    items = [item for item in items if int(item.get("attempt") or 0) != int(attempt or 1)]
    items.append(entry)
    attempts[label] = items[-int(max_attempts or 1):]
    result["retry_attempts"] = attempts
    retry_state = dict(result.get("retry_state") or {})
    retry_state[label] = entry
    _update_result(task_id, {
        "retry_attempts": attempts,
        "retry_state": retry_state,
    })


def _is_retryable_gemini_error(message: Any) -> bool:
    text = str(message or "")
    if not text:
        return False
    lower_text = text.lower()
    if any(fragment.lower() in lower_text for fragment in NON_RETRYABLE_GEMINI_ERROR_FRAGMENTS):
        return False
    return any(fragment.lower() in lower_text for fragment in RETRYABLE_GEMINI_ERROR_FRAGMENTS)


def _retry_limit(env_name: str, default: int) -> int:
    return max(0, min(_int_env(env_name, default), 5))


def _compact_error(message: Any, limit: int = 180) -> str:
    text = re.sub(r"\s+", " ", str(message or "")).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def plan_from_first_response(first_response: str, *, audio: Dict[str, Any]) -> Dict[str, Any]:
    first_response = (first_response or "").strip()
    if not first_response:
        raise Mp3VideoWorkflowError("Gemini 第一轮返回为空，无法规划视频")
    fallback = fallback_plan(first_response, audio=audio)
    try:
        payload = deepseek_plan(first_response, audio=audio)
    except Exception as exc:
        fallback["planner_error"] = str(exc)
        return fallback
    return validate_plan(payload, fallback=fallback, audio=audio)


def plan_from_direct_ds_response(first_response: str, *, audio: Dict[str, Any]) -> Dict[str, Any]:
    first_response = (first_response or "").strip()
    if not first_response:
        raise Mp3VideoWorkflowError("DS 返回值为空，无法规划视频")
    local_plan = _local_plan_from_direct_ds_response(first_response, audio=audio)
    try:
        planner_payload = deepseek_plan(first_response, audio=audio)
    except Exception as exc:
        local_plan["planner_error"] = str(exc)
        _validate_strict_ds_plan(local_plan, source_label="手动 DS 返回值")
        return local_plan
    if planner_payload:
        plan = validate_plan(planner_payload, fallback=local_plan, audio=audio)
        plan["provider"] = planner_payload.get("provider") or "deepseek"
        plan["planner_input_source"] = "direct_ds_response"
    else:
        plan = local_plan
    _validate_strict_ds_plan(plan, source_label="手动 DS 返回值")
    return plan


def _local_plan_from_direct_ds_response(first_response: str, *, audio: Dict[str, Any]) -> Dict[str, Any]:
    """Build a complete plan from an already accepted seven-section DS response."""
    fallback = fallback_plan(first_response, audio=audio)
    sections = _direct_ds_numbered_sections(first_response)
    title_1 = _direct_ds_title_value(sections.get("1", ""))
    title_2 = _direct_ds_title_value(sections.get("2", ""))
    title_3 = _direct_ds_title_value(sections.get("3", ""))
    title_6 = _direct_ds_closed_door_title(sections.get("6", ""), separator="/")
    title_7 = _direct_ds_closed_door_title(sections.get("7", ""), separator="|")
    audio_range = extract_audio_range(sections.get("4", "") or first_response)

    title_options = [item for item in [title_1, title_2, title_3] if item]
    base_title = title_2 or title_1 or title_3 or fallback.get("title")
    finance_title = title_1 or base_title
    vertical_title = title_2 or title_1 or base_title
    audio_plan = dict(fallback.get("audio") if isinstance(fallback.get("audio"), dict) else {})
    if audio_range:
        audio_plan["start_seconds"] = round(audio_range[0], 3)
        audio_plan["end_seconds"] = round(audio_range[1], 3)
    audio_plan["speed"] = DEFAULT_SPEED

    payload = {
        "title": base_title,
        "title_options": title_options,
        "subtitle": title_3 or fallback.get("subtitle"),
        "cover_main_title": "内资闭门会议",
        "cover_title": _direct_ds_vertical_cover_title(vertical_title, title_7),
        "cover_subtitle": _direct_ds_vertical_cover_subtitle(vertical_title, title_7, title_3),
        "finance_cover_subtitle": _direct_ds_finance_cover_title(finance_title, title_6),
        "xiaojunjun_title": "内资闭门会议",
        # Section 6 remains the full landscape video title; the cover copy uses
        # the independent high-density title version 2 so it cannot collapse
        # into the finance cover derived from version 1.
        "xiaojunjun_subtitle": _xiaojunjun_cover_subtitle(
            title_2 or title_3 or base_title,
            fallback=title_3 or base_title,
        ),
        "xiaojunjun_video_title": title_6 or fallback.get("xiaojunjun_video_title"),
        "ciketc_video_title": title_7 or fallback.get("ciketc_video_title"),
        "date_text": _direct_ds_date(title_7 or title_6 or first_response) or fallback.get("date_text"),
        "emotion": fallback.get("emotion", "bull"),
        "audio": audio_plan,
        "provider": "direct_ds_local",
    }
    return validate_plan(payload, fallback=fallback, audio=audio)


def _validate_ds_response_contract(first_response: str, *, source_label: str = "DS 返回值") -> None:
    sections = _direct_ds_numbered_sections(first_response)
    title_1 = _direct_ds_title_value(sections.get("1", ""))
    title_2 = _direct_ds_title_value(sections.get("2", ""))
    title_6 = _direct_ds_closed_door_title(sections.get("6", ""), separator="/")
    title_7 = _direct_ds_closed_door_title(sections.get("7", ""), separator="|")
    audio_range = extract_audio_range(sections.get("4", "") or first_response)
    missing = []
    if not (title_1 or title_2):
        missing.append("第 1/2 条高密度标题")
    if not audio_range:
        missing.append("第 4 条开始/结束时间戳")
    if not title_6:
        missing.append("第 6 条斜杠版闭门会标题")
    if not title_7:
        missing.append("第 7 条竖线版闭门会标题")
    if missing:
        raise Mp3VideoWorkflowError(
            f"{source_label}不合格，未识别到：{'、'.join(missing)}。"
            "请粘贴完整 DS 返回值后重试；系统不会用占位文案继续生成封面或视频。"
        )


def _validate_strict_ds_plan(plan: Dict[str, Any], *, source_label: str = "DS 返回值") -> None:
    plan = plan if isinstance(plan, dict) else {}
    audio_plan = plan.get("audio") if isinstance(plan.get("audio"), dict) else {}
    start = _parse_seconds(audio_plan.get("start_seconds"), default=0.0) or 0.0
    end = _parse_seconds(audio_plan.get("end_seconds"), default=None)
    missing = []
    if end is None or end <= start:
        missing.append("有效的精华片段结束时间戳")
    if not _clean_closed_door_video_title(plan.get("xiaojunjun_video_title"), expected_separator="/"):
        missing.append("小君君横屏第 6 条标题")
    if not _clean_closed_door_video_title(plan.get("ciketc_video_title"), expected_separator="|"):
        missing.append("此刻此声竖屏第 7 条标题")

    bad_fields = []
    for key in (
        "title",
        "subtitle",
        "cover_title",
        "cover_subtitle",
        "finance_cover_subtitle",
        "xiaojunjun_title",
        "xiaojunjun_subtitle",
        "xiaojunjun_video_title",
        "ciketc_video_title",
    ):
        if _is_invalid_ds_plan_text(plan.get(key)):
            bad_fields.append(key)
    if bad_fields:
        missing.append("非占位的封面/视频文案")

    if missing:
        raise Mp3VideoWorkflowError(
            f"{source_label}规划不合格，缺少：{'、'.join(missing)}。"
            "已停止生成封面/背景图/视频，请修正 DS 返回值后重新投递。"
        )


def _is_invalid_ds_plan_text(value: Any) -> bool:
    text = _clean_line(value)
    if not text:
        return False
    if _is_cover_process_text(text):
        return True
    return bool(re.search(
        r"xxxx|当日日期|格式[（(]\s*版本|标题版本[一二三四五六七八九十A-Za-z]*|未识别到时间戳|"
        r"DS\s*v4|IMA\s*转录|Gemini|Generated from transcript|推荐截取|最精华音频片段",
        text,
        flags=re.I,
    ))


def _direct_ds_numbered_sections(text: str) -> Dict[str, str]:
    source = str(text or "").replace("\r", "\n")
    if not source.strip():
        return {}
    # DeepSeek commonly emits top-level sections as Markdown headings such as
    # ``### 6.`` or ``### 6｜``. Prefer those explicit headings when present so
    # numbered quotes inside section 5 cannot be mistaken for new sections.
    markdown_marker_re = re.compile(
        r"^[ \t]*#{1,6}[ \t]*(?:\*\*)?[ \t]*([1-7])[ \t]*[.．、:：|｜][ \t]*",
        flags=re.M,
    )
    matches = list(markdown_marker_re.finditer(source))
    if not matches:
        marker_re = re.compile(
            r"(?:(?<=\n)|^)\s*(?:[-*]\s*)?(?:\*\*)?\s*([1-7])\s*[.．、:：]\s*"
            r"|(?:\*\*)\s*([1-7])\s*[.．、:：]\s*"
        )
        matches = list(marker_re.finditer(source))
    sections: Dict[str, str] = {}
    for index, match in enumerate(matches):
        number = next((group for group in match.groups() if group), "")
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(source)
        content = source[start:end].strip()
        if number and content:
            sections[number] = content
    return sections


def _direct_ds_content_lines(section: str) -> List[str]:
    lines = []
    for raw in str(section or "").replace("\r", "\n").split("\n"):
        clean = _clean_line(raw).strip("* ：:，,。;；")
        if not clean:
            continue
        if _is_direct_ds_section_heading(clean):
            continue
        cleaned_heading = re.sub(
            r"^(?:标题版本[一二三四五六七八九十A-Za-z\d]*|短标题版本|投资(?:者)?视角标题|"
            r"高信息密度.*标题|精简短标题|优化标题格式|优化标题|闭门会格式标题|"
            r"最精华片段|音频片段|金句|说明)"
            r"(?:（[^）]*）|\([^)]*\))?\s*[:：-]?\s*",
            "",
            clean,
        ).strip()
        if cleaned_heading != clean:
            clean = cleaned_heading.strip("* ：:，,。;；")
            if not clean or _is_direct_ds_section_heading(clean):
                continue
        lines.append(clean)
    if lines:
        return lines
    clean = _clean_line(section).strip("* ：:，,。;；")
    clean = re.sub(
        r"^(?:投资(?:者)?视角标题|高信息密度.*标题|精简短标题|优化标题|闭门会格式标题|最精华片段)"
        r"(?:（[^）]*）|\([^)]*\))?\s*[:：]?\s*",
        "",
        clean,
    ).strip()
    return [clean] if clean else []


def _is_direct_ds_section_heading(value: Any) -> bool:
    text = _clean_line(value).strip("* ：:，,。;；")
    if not text:
        return True
    return bool(re.fullmatch(
        r"(?:标题版本[一二三四五六七八九十A-Za-z\d]*(?:（[^）]*）|\([^)]*\))?|"
        r"短标题版本|精简短标题|优化标题格式\s*(?:（[^）]*）|\([^)]*\))?|"
        r"优化标题(?:（[^）]*）|\([^)]*\))?|闭门会格式标题(?:（[^）]*）|\([^)]*\))?|"
        r"最精华(?:音频)?片段(?:（[^）]*）|\([^)]*\))?|音频金句|金句|说明)",
        text,
        flags=re.I,
    ))


def _direct_ds_title_value(section: str) -> str:
    for line in _direct_ds_content_lines(section):
        title = _normalize_title_candidate(line)
        if title and not _is_cover_process_text(title):
            return title
    return ""


def _direct_ds_closed_door_title(section: str, *, separator: str) -> str:
    expected = "|" if separator in {"|", "｜"} else "/"
    lines = _direct_ds_content_lines(section)
    separator_re = r"[|｜]" if expected == "|" else r"/"
    # Prefer the actual formatted title. A heading such as
    # "另一个版本（内资闭门会竖线格式）" mentions the format but is not itself
    # usable cover copy and must not be auto-formatted into a false positive.
    for line in lines:
        if not re.search(rf"内资闭门会(?:议)?\s*{separator_re}", line):
            continue
        title = _clean_closed_door_video_title(line, expected_separator=expected)
        if title:
            return title
    text = _clean_line(section)
    text = re.sub(r"格式\s*[:：]\s*内资闭门会(?:议)?\s*[/|｜].*?(?:当日日期|日期)[)）]?", "", text)
    pattern = r"内资闭门会(?:议)?\s*" + (r"/" if expected == "/" else r"[|｜]") + r"\s*.+"
    matches = list(re.finditer(pattern, text))
    for match in reversed(matches):
        title = _clean_closed_door_video_title(match.group(0), expected_separator=expected)
        if title:
            return title
    return ""


def _direct_ds_date(value: Any) -> str:
    date = _closed_door_video_date(value)
    if re.match(r"[12]\d{3}-\d{2}-\d{2}$", date):
        return date.replace("-", ".")
    match = re.search(r"([12]\d{3})[./-](\d{1,2})[./-](\d{1,2})", str(value or ""))
    if match:
        return f"{int(match.group(1)):04d}.{int(match.group(2)):02d}.{int(match.group(3)):02d}"
    return ""


def _direct_ds_closed_door_parts(value: Any) -> List[str]:
    text = _clean_line(value).replace("｜", "|")
    parts = [_clean_line(part).strip("“”\"'《》") for part in re.split(r"\s*[|/]\s*", text) if _clean_line(part)]
    output = []
    for part in parts:
        if re.match(r"^内资闭门会(?:议)?$", part):
            continue
        if _closed_door_video_date(part):
            continue
        if not _is_cover_process_text(part):
            output.append(part)
    return output


def _closed_door_visual_lines(value: Any, *, max_units: float, max_lines: int = 3) -> str:
    lines: List[str] = []
    for part in _direct_ds_closed_door_parts(value):
        line = _clip_finance_visual_line(part, max_units=max_units)
        if not line or line in lines:
            continue
        lines.append(line)
        if len(lines) >= max_lines:
            break
    return "\n".join(lines)


def _direct_ds_finance_cover_title(title: Any, closed_door_title: Any = "") -> str:
    closed_parts = _direct_ds_closed_door_parts(closed_door_title)
    closed_visual = _closed_door_visual_lines(closed_door_title, max_units=14, max_lines=3)
    if closed_visual:
        return closed_visual
    text = _normalize_title_candidate(title)
    text = re.sub(r"^(?:专家密讲|独家)\s*[:：]\s*", "", text).strip()
    clauses = [
        _clean_line(item)
        for item in re.split(r"[，,；;。.!！?？]", text)
        if _clean_line(item) and not _is_cover_process_text(item)
    ]
    lines: List[str] = []
    if closed_parts:
        lines.append(closed_parts[0])
    for clause in clauses:
        if any(_clean_line(clause) in _clean_line(line) or _clean_line(line) in _clean_line(clause) for line in lines):
            continue
        lines.append(clause)
        if len(lines) >= 3:
            break
    if lines:
        return "\n".join(_clip_finance_visual_line(part, max_units=15) for part in lines[:3] if _clean_line(part))
    clauses = [item for item in _title_clause_parts(text) if _clean_line(item)]
    if clauses:
        return "\n".join(_clip_finance_visual_line(part, max_units=15) for part in clauses[:3] if _clean_line(part))
    if len(closed_parts) >= 2:
        return "\n".join(_clip_finance_visual_line(part, max_units=15) for part in closed_parts[:3] if _clean_line(part))
    return _finance_cover_title(text)


def _direct_ds_vertical_cover_title(title: Any, closed_door_title: Any = "") -> str:
    text = _normalize_title_candidate(title)
    if re.search(r"Micro-?TEC", text, flags=re.I):
        if "需求井喷" in text:
            return "Micro-TEC\n需求井喷"
        if "国产" in text and ("替代" in text or "突破" in text):
            return "Micro-TEC\n国产突破"
        return "Micro-TEC\n核心器件"
    parts = _direct_ds_closed_door_parts(closed_door_title)
    if parts:
        return _cover_lines(parts[0], max_chars=8, max_lines=2)
    return _vertical_cover_title(text)


def _direct_ds_vertical_cover_subtitle(title: Any, closed_door_title: Any = "", fallback: Any = "") -> str:
    parts = _direct_ds_closed_door_parts(closed_door_title)
    for part in parts[1:]:
        clean = _clip_title_line(part, 16)
        if clean:
            return clean
    text = _normalize_title_candidate(title)
    if "出口管制" in text and "误读" in text:
        return "出口管制被误读"
    if "客户验证" in text:
        return "客户验证超预期"
    return _vertical_cover_subtitle(text, fallback=fallback)


def _direct_ds_xiaojunjun_subtitle(closed_door_title: Any, title: Any = "", fallback: Any = "") -> str:
    parts = _direct_ds_closed_door_parts(closed_door_title)
    if parts:
        return "\n".join(_clip_title_line(part, 18) for part in parts[:3] if _clean_line(part))
    return _xiaojunjun_cover_subtitle(title, fallback=fallback)


def deepseek_plan(first_response: str, *, audio: Dict[str, Any]) -> Dict[str, Any]:
    api_key = os.environ.get("MP3_VIDEO_PLANNER_API_KEY") or os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        return {}
    base_url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    timeout = _int_env("MP3_VIDEO_PLANNER_TIMEOUT", 120)
    duration = float(audio.get("duration_seconds") or 0)
    local_titles = extract_title_candidates(first_response)[:5]
    local_audio_range = extract_audio_range(first_response)
    local_audio_hint = ""
    if local_audio_range:
        local_audio_hint = f"{local_audio_range[0]:.3f}-{local_audio_range[1]:.3f} 秒"
    system_prompt = (
        "你是财经音频视频编排策划器。你只能基于输入的第一轮返回做选择、重组和规整，"
        "不得新增事实。第一轮返回可能来自 Gemini，也可能是 IMA 转录全文经 DS 处理后的结果。"
        "输出严格 JSON，不要 Markdown，不要解释。"
    )
    user_prompt = "\n".join([
        "请从第一轮返回中选择一个最适合成片的标题版本，并规划视频封面与音频截取。",
        "标题规则：如果原文包含“投资者视角长标题（3个版本）”或类似小节，必须优先从该小节的长标题中选择或融合；",
        "如果候选标题形如“内资闭门会 | 内容标题 | 260622”，title/title_options 必须去掉开头栏目词和末尾日期，只保留中间内容标题。",
        "封面标题规则：你要负责判断第一轮返回里哪些信息最适合作为此刻此声竖屏背景的主标题，"
        "优先选择投资者最想点击的资产结论、价格信号、风险变化、机会窗口或信息差；"
        "不要机械使用高密度标题的第一句或第一个问句，上游原因/分析过程只有在点击价值更高时才放主标题。",
        "音频规则：如果原文出现“推荐截取时间戳：01:10 - 09:45”这类明确范围，audio.start_seconds/audio.end_seconds 必须使用该范围。",
        "必须输出 JSON 字段：",
        "- title: 最终视频标题，优先选择原文中已给出的标题版本，可轻微润色",
        "- title_options: 字符串数组，最多 5 个候选标题",
        "- subtitle: 一句事实型副标题，30 字以内",
        "- cover_main_title: 此刻此声横屏封皮栏目/场景主标题，默认可用“内资闭门会议”，不要把长内容标题放这里",
        "- cover_title: 此刻此声竖屏纯音频背景板主标题，必须是你从第一轮返回中判断出的最佳点击点，用 \\n 分成最多 2 行，每行不超过 10 个中文字符",
        "- cover_subtitle: 此刻此声竖屏背景副标题，必须 1 行，不超过 16 个中文字符，只补充主标题背后的逻辑/信号/机会，不要重复主标题",
        "- finance_cover_subtitle: 此刻此声横屏封皮黄色大标题，优先使用高信息密度标题版本一，必须改写成恰好 3 行短句，每行约 10-13 个中文字符或等价视觉宽度，约占画面宽度 60%-75%，必须避免占满整行；不要直接截断长句，要提炼为短促标题文案",
        "- xiaojunjun_title: 小君君横屏封皮/背景栏目主标题，默认可用“内资闭门会议”，不要把长内容标题放这里",
        "- xiaojunjun_subtitle: 小君君横屏封皮/背景黄色高亮条文案，2-3 行，每行不超过 18 个中文字符，只基于原文事实",
        "- xiaojunjun_video_title: 小君君横屏视频合成传入标题，必须优先使用原文第 6 条斜杠版完整标题，保留“内资闭门会 / ... / 日期”的完整格式，不要裁剪",
        "- ciketc_video_title: 此刻此声视频合成传入标题，必须优先使用原文第 7 条竖线版完整标题，保留“内资闭门会 | ... | 日期”的完整格式，不要裁剪",
        "- infographic_title: 信息墙主题标题，8-18 字，必须是具体投资主题或判断，不能是流程说明",
        "- infographic_subtitle: 信息墙核心结论，16-36 字，优先选择最有信息差的观点",
        "- infographic_points: 严格包含 3 个字符串的数组；优先使用原文第 5 条金句，其次使用第 4 条核心逻辑；每条必须是可独立阅读的金句、事实或投资判断，不能出现寒暄、角色介绍、任务说明、栏目标题或时间戳",
        "双封皮文案规则：finance_cover_subtitle 与 xiaojunjun_subtitle 是两个独立创意版本，必须有实质不同的切入点和措辞，严禁只换换行、标点或顺序。"
        "xiaojunjun_subtitle 必须优先使用不同于高信息密度标题版本一的候选标题/观点，不能照抄 cover_title、cover_subtitle 或 finance_cover_subtitle；"
        "它应该更完整地保留闭门会标题的信息密度和投资逻辑，而不是此刻此声竖屏那种短促标题。",
        "- date_text: 日期，格式 YYYY.M.D",
        "- emotion: pro/macro/bull/bear 之一",
        "- audio: 对象，包含 start_seconds、end_seconds、speed。speed 必须是 1.2；start/end 要按第一轮返回中最适合成片的内容段落判断，如果没有明确范围则 start_seconds=0、end_seconds=null。",
        "禁止把 DS v4 flash、IMA、Gemini、转录全文、Generated from transcript、推荐截取、源文件、最精华音频片段等流程说明写入任何封面字段。",
        "",
        f"音频文件名：{audio.get('filename') or audio.get('name') or ''}",
        f"音频总时长秒：{duration:.3f}" if duration else "音频总时长秒：未知",
        "本地已识别候选标题：" + ("；".join(local_titles) if local_titles else "无"),
        "本地已识别推荐截取：" + (local_audio_hint or "无"),
        "",
        "【第一轮返回】",
        _compact_text(first_response, _int_env("MP3_VIDEO_PLANNER_INPUT_CHARS", 18000)),
    ])
    response = requests.post(
        f"{base_url.rstrip('/')}/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": planner_model(),
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.1,
        },
        timeout=timeout,
    )
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        raise Mp3VideoWorkflowError(f"DeepSeek 规划失败: HTTP {response.status_code} {response.text[:600]}") from exc
    content = response.json()["choices"][0]["message"]["content"]
    payload = _json_from_text(content)
    if isinstance(payload, dict):
        payload["provider"] = "deepseek"
    return payload if isinstance(payload, dict) else {}


def fallback_plan(first_response: str, *, audio: Dict[str, Any]) -> Dict[str, Any]:
    candidates = extract_title_candidates(first_response)
    title = candidates[0] if candidates else _clip_text(_clean_line(first_response), 36) or "此刻此声精选"
    subtitle = extract_subtitle(first_response)
    finance_title = candidates[0] if candidates else title
    vertical_title = candidates[1] if len(candidates) > 1 else title
    role_video_titles = extract_closed_door_video_titles(first_response)
    duration = float(audio.get("duration_seconds") or 0)
    audio_range = extract_audio_range(first_response)
    infographic_copy = _infographic_copy_from_source(
        first_response,
        fallback_title=title,
        fallback_subtitle=subtitle,
    )
    audio_plan = {"start_seconds": 0, "end_seconds": None if duration else None, "speed": DEFAULT_SPEED}
    if audio_range:
        audio_plan["start_seconds"] = round(audio_range[0], 3)
        audio_plan["end_seconds"] = round(audio_range[1], 3)
    xiaojunjun_copy_source = candidates[1] if len(candidates) > 1 else title
    return {
        "title": title,
        "title_options": candidates[:5],
        "subtitle": subtitle,
        "cover_main_title": "内资闭门会议",
        "cover_title": _vertical_cover_title(vertical_title),
        "cover_subtitle": _vertical_cover_subtitle(vertical_title, fallback=subtitle or title),
        "finance_cover_subtitle": _finance_cover_title(finance_title),
        "xiaojunjun_title": "内资闭门会议",
        "xiaojunjun_subtitle": _xiaojunjun_cover_subtitle(xiaojunjun_copy_source, fallback=subtitle),
        "xiaojunjun_video_title": role_video_titles.get("xiaojunjun_video_title") or _format_closed_door_video_title(title, separator="/"),
        "ciketc_video_title": role_video_titles.get("ciketc_video_title") or _format_closed_door_video_title(title, separator="|"),
        **infographic_copy,
        "date_text": datetime.now().strftime("%Y.%m.%d"),
        "emotion": "bull",
        "audio": audio_plan,
        "provider": "local_fallback",
        "planner_version": PLANNER_VERSION,
    }


def validate_plan(payload: Dict[str, Any], *, fallback: Dict[str, Any], audio: Dict[str, Any]) -> Dict[str, Any]:
    payload = payload if isinstance(payload, dict) else {}
    duration = float(audio.get("duration_seconds") or 0)
    raw_title = payload.get("title") or fallback["title"]
    title = _clip_text(_normalize_title_candidate(raw_title) or raw_title, 120)
    subtitle = _clip_text(payload.get("subtitle") or fallback.get("subtitle"), 60)
    cover_main_title = _clip_text(payload.get("cover_main_title") or fallback.get("cover_main_title") or "内资闭门会议", 16)
    planner_cover_title = _format_planner_vertical_title(payload.get("cover_title"))
    planner_cover_subtitle = _format_planner_vertical_subtitle(payload.get("cover_subtitle"))
    x_title = _clean_multiline(payload.get("xiaojunjun_title") or fallback.get("xiaojunjun_title"))
    plan_audio = payload.get("audio") if isinstance(payload.get("audio"), dict) else {}
    fallback_audio = fallback.get("audio") if isinstance(fallback.get("audio"), dict) else {}
    fallback_start = _parse_seconds(fallback_audio.get("start_seconds"), default=0.0)
    fallback_end = _parse_seconds(fallback_audio.get("end_seconds"), default=None)
    start = _parse_seconds(plan_audio.get("start_seconds"), default=fallback_start)
    end = _parse_seconds(plan_audio.get("end_seconds"), default=fallback_end)
    start = max(0.0, start or 0.0)
    if duration and start >= duration:
        start = 0.0
    if end is not None:
        end = max(start + 0.1, float(end))
        if duration:
            end = min(duration, end)
        if end <= start + 0.1:
            end = None
    title_options = []
    if isinstance(payload.get("title_options"), list):
        title_options.extend(payload.get("title_options") or [])
    title_options.extend(fallback.get("title_options", []))
    normalized_options = []
    seen_options = set()
    for item in title_options:
        normalized = _normalize_title_candidate(item)
        if not normalized or normalized in seen_options:
            continue
        seen_options.add(normalized)
        normalized_options.append(normalized)
    finance_title = normalized_options[0] if normalized_options else title
    vertical_title = normalized_options[1] if len(normalized_options) > 1 else title
    fallback_cover_title = _format_planner_vertical_title(fallback.get("cover_title")) or _vertical_cover_title(vertical_title)
    fallback_cover_subtitle = (
        _format_planner_vertical_subtitle(fallback.get("cover_subtitle"))
        or _vertical_cover_subtitle(vertical_title, fallback=subtitle or title)
    )
    xiaojunjun_video_title = (
        _clean_closed_door_video_title(payload.get("xiaojunjun_video_title"), expected_separator="/")
        or _clean_closed_door_video_title(fallback.get("xiaojunjun_video_title"), expected_separator="/")
        or _format_closed_door_video_title(title, separator="/", date_text=payload.get("date_text") or fallback.get("date_text"))
    )
    ciketc_video_title = (
        _clean_closed_door_video_title(payload.get("ciketc_video_title"), expected_separator="|")
        or _clean_closed_door_video_title(fallback.get("ciketc_video_title"), expected_separator="|")
        or _format_closed_door_video_title(title, separator="|", date_text=payload.get("date_text") or fallback.get("date_text"))
    )
    finance_cover_subtitle = (
        _format_planner_finance_cover_subtitle(payload.get("finance_cover_subtitle"))
        or _format_planner_finance_cover_subtitle(fallback.get("finance_cover_subtitle"))
        or _closed_door_visual_lines(ciketc_video_title, max_units=14, max_lines=3)
        or _finance_cover_title(finance_title)
    )
    xiaojunjun_disallowed = [planner_cover_title, planner_cover_subtitle, finance_cover_subtitle]
    # The planner's dedicated second creative must win.  Previously the formatted
    # section-6 video title was evaluated first, so similar section-6/7 titles
    # silently replaced the AI's distinct xiaojunjun copy with the finance copy.
    role_title_copy = _closed_door_visual_lines(xiaojunjun_video_title, max_units=18, max_lines=3)
    fallback_xiaojunjun_copy = fallback.get("xiaojunjun_subtitle")
    alternate_title_copy = _xiaojunjun_cover_subtitle(vertical_title or title, fallback=subtitle)
    fallback_candidates = [role_title_copy, fallback_xiaojunjun_copy, alternate_title_copy]
    xiaojunjun_subtitle = _first_distinct_xiaojunjun_copy(
        payload.get("xiaojunjun_subtitle"),
        *fallback_candidates,
        disallow=xiaojunjun_disallowed,
    )
    infographic_title = _clean_infographic_copy(
        payload.get("infographic_title") or fallback.get("infographic_title"),
        max_chars=36,
    ) or _clean_infographic_copy(title, max_chars=36)
    infographic_subtitle = _clean_infographic_copy(
        payload.get("infographic_subtitle") or fallback.get("infographic_subtitle"),
        max_chars=68,
    ) or _clean_infographic_copy(subtitle, max_chars=68)
    infographic_points = _normalize_infographic_points(payload.get("infographic_points"))
    if len(infographic_points) < 3:
        infographic_points = _merge_infographic_points(
            infographic_points,
            _normalize_infographic_points(fallback.get("infographic_points")),
        )
    infographic_points = _exclude_infographic_copy(
        infographic_points,
        [infographic_title, infographic_subtitle],
    )
    return {
        "title": title,
        "title_options": [_clip_text(item, 120) for item in normalized_options if _clean_line(item)][:5],
        "subtitle": subtitle,
        "cover_main_title": cover_main_title or "内资闭门会议",
        "cover_title": planner_cover_title or fallback_cover_title,
        "cover_subtitle": planner_cover_subtitle or fallback_cover_subtitle,
        "finance_cover_subtitle": finance_cover_subtitle,
        "xiaojunjun_title": _clip_text(x_title or payload.get("cover_main_title") or fallback.get("xiaojunjun_title") or "内资闭门会议", 16),
        "xiaojunjun_subtitle": xiaojunjun_subtitle,
        "xiaojunjun_video_title": _clip_text(xiaojunjun_video_title, 180),
        "ciketc_video_title": _clip_text(ciketc_video_title, 180),
        "infographic_title": infographic_title,
        "infographic_subtitle": infographic_subtitle,
        "infographic_points": infographic_points[:3],
        "date_text": _clip_text(payload.get("date_text") or fallback.get("date_text"), 20),
        "emotion": payload.get("emotion") if payload.get("emotion") in {"pro", "macro", "bull", "bear"} else fallback.get("emotion", "bull"),
        "audio": {"start_seconds": round(start, 3), "end_seconds": round(end, 3) if end is not None else None, "speed": DEFAULT_SPEED},
        "provider": payload.get("provider") or fallback.get("provider") or "local_fallback",
        "planner_error": fallback.get("planner_error"),
        "planner_version": PLANNER_VERSION,
    }


def generate_visual_artifacts(
    job_id: str,
    *,
    plan: Dict[str, Any],
    infographic_path: str,
    created_at: Optional[datetime] = None,
) -> Dict[str, Dict[str, Any]]:
    return generate_visual_artifacts_with_browser(
        job_id,
        plan=plan,
        infographic_path=infographic_path,
        created_at=created_at,
    )


def generate_visual_artifacts_with_browser(
    job_id: str,
    *,
    plan: Dict[str, Any],
    infographic_path: str,
    created_at: Optional[datetime] = None,
) -> Dict[str, Dict[str, Any]]:
    finance_cover = artifact_path_for_job(job_id, "ciketc_landscape_cover_1", "jpg", created_at)
    audio_background = artifact_path_for_job(job_id, "ciketc_vertical_audio_background_1", "jpg", created_at)
    xiaojunjun_cover = artifact_path_for_job(job_id, "xiaojunjun_landscape_cover", "jpg", created_at)
    xiaojunjun_background = artifact_path_for_job(job_id, "xiaojunjun_landscape_background", "jpg", created_at)

    infographic_data_url = _image_data_url(infographic_path)
    with _cover_browser_context() as context:
        _render_canvas_artifact(
            context,
            page_name="finance-cover-generator.html",
            function_name="renderWorkflowFinanceCover",
            payload=_finance_cover_payload(plan),
            output_path=finance_cover,
            expected_size=VISUAL_ARTIFACT_EXPECTED_SIZES["finance_cover"],
        )
        _render_canvas_artifact(
            context,
            page_name="thumbnail-workbench.html",
            function_name="renderWorkflowThumbnail",
            payload=_audio_background_payload(plan, infographic_data_url),
            output_path=audio_background,
            expected_size=VISUAL_ARTIFACT_EXPECTED_SIZES["audio_background"],
        )
        _render_canvas_artifact(
            context,
            page_name="closed-door-card-cover-generator.html",
            function_name="renderWorkflowClosedDoorCover",
            payload=_closed_door_payload(plan, include_date=True),
            output_path=xiaojunjun_cover,
            expected_size=VISUAL_ARTIFACT_EXPECTED_SIZES["xiaojunjun_cover"],
        )
        _render_canvas_artifact(
            context,
            page_name="closed-door-card-cover-generator.html",
            function_name="renderWorkflowClosedDoorCover",
            payload=_closed_door_payload(plan, include_date=False),
            output_path=xiaojunjun_background,
            expected_size=VISUAL_ARTIFACT_EXPECTED_SIZES["xiaojunjun_background"],
        )

    return {
        key: _artifact_meta(path, VISUAL_ARTIFACT_LABELS[key], renderer=VISUAL_ARTIFACT_RENDERERS[key])
        for key, path in {
            "finance_cover": finance_cover,
            "audio_background": audio_background,
            "xiaojunjun_cover": xiaojunjun_cover,
            "xiaojunjun_background": xiaojunjun_background,
        }.items()
    }


def regenerate_visual_artifact(
    job_id: str,
    artifact_key: str,
    *,
    plan: Dict[str, Any],
    title_text: Any,
    infographic_path: Optional[str] = None,
    source_text: str = "",
    audio: Optional[Dict[str, Any]] = None,
    created_at: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Regenerate one visual artifact with manually supplied title copy."""
    key = str(artifact_key or "").strip()
    if key not in VISUAL_ARTIFACT_KEYS:
        raise Mp3VideoWorkflowError(f"不支持重做这个封皮/背景图: {key}")

    lines = _manual_visual_title_lines(title_text)
    if not lines:
        raise Mp3VideoWorkflowError("请输入 1-3 行标题文案")

    render_plan = _visual_artifact_plan_with_manual_title(plan, key, lines)
    structured_infographic = None
    if key == "audio_background" and _clean_line(source_text):
        structured_infographic_path = artifact_path_for_job(
            job_id,
            f"structured_infographic_manual_{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}",
            "png",
            created_at,
        )
        draw_transcript_infographic(
            structured_infographic_path,
            source_text,
            audio=audio if isinstance(audio, dict) else {},
            plan=plan,
        )
        structured_infographic = _artifact_meta(
            structured_infographic_path,
            "DS 金句观点信息图",
            renderer="pil:manual-structured-infographic",
        )
        infographic_path = structured_infographic_path
    output_path = artifact_path_for_job(
        job_id,
        f"{key}_manual_{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}",
        "jpg",
        created_at,
    )
    expected_size = VISUAL_ARTIFACT_EXPECTED_SIZES[key]

    with _cover_browser_context() as context:
        if key == "finance_cover":
            _render_canvas_artifact(
                context,
                page_name="finance-cover-generator.html",
                function_name="renderWorkflowFinanceCover",
                payload=_finance_cover_payload(render_plan),
                output_path=output_path,
                expected_size=expected_size,
            )
        elif key == "audio_background":
            if not infographic_path:
                raise Mp3VideoWorkflowError("当前任务缺少信息图，不能重做此刻此声竖屏背景图")
            _render_canvas_artifact(
                context,
                page_name="thumbnail-workbench.html",
                function_name="renderWorkflowThumbnail",
                payload=_audio_background_payload(render_plan, _image_data_url(infographic_path)),
                output_path=output_path,
                expected_size=expected_size,
            )
        elif key in {"xiaojunjun_cover", "xiaojunjun_background"}:
            _render_canvas_artifact(
                context,
                page_name="closed-door-card-cover-generator.html",
                function_name="renderWorkflowClosedDoorCover",
                payload=_closed_door_payload(render_plan, include_date=(key == "xiaojunjun_cover")),
                output_path=output_path,
                expected_size=expected_size,
            )

    artifact = _artifact_meta(output_path, VISUAL_ARTIFACT_LABELS[key], renderer=VISUAL_ARTIFACT_RENDERERS[key])
    if structured_infographic:
        artifact["source_infographic"] = structured_infographic
    artifact["manual_regenerated_at"] = datetime.utcnow().isoformat() + "Z"
    artifact["manual_title_text"] = "\n".join(lines)
    artifact["manual_title_lines"] = lines
    return artifact


def _manual_visual_title_lines(value: Any) -> List[str]:
    if isinstance(value, list):
        raw_lines = [str(item or "") for item in value]
    else:
        raw_lines = re.split(r"[\r\n]+", str(value or ""))
    return [_clean_line(line)[:80] for line in raw_lines if _clean_line(line)][:3]


def _visual_artifact_plan_with_manual_title(plan: Dict[str, Any], artifact_key: str, lines: List[str]) -> Dict[str, Any]:
    updated = dict(plan or {})
    text = "\n".join(lines)
    if artifact_key == "finance_cover":
        updated["manual_finance_cover_subtitle"] = text
        updated["finance_cover_subtitle"] = text
    elif artifact_key == "audio_background":
        updated["manual_cover_title"] = "\n".join(lines[:2])
        updated["manual_cover_subtitle"] = lines[2] if len(lines) >= 3 else ""
        updated["cover_title"] = "\n".join(lines[:2])
        updated["cover_subtitle"] = lines[2] if len(lines) >= 3 else (updated.get("cover_subtitle") or updated.get("subtitle") or "")
    elif artifact_key in {"xiaojunjun_cover", "xiaojunjun_background"}:
        updated["manual_xiaojunjun_subtitle"] = text
        updated["xiaojunjun_subtitle"] = text
    return updated


def generate_video_keyframes(
    job_id: str,
    *,
    plan: Dict[str, Any],
    artifacts: Dict[str, Dict[str, Any]],
    audio: Dict[str, Any],
    source_text: str = "",
    created_at: Optional[datetime] = None,
) -> Dict[str, Dict[str, Any]]:
    captions = _preview_caption_candidates(source_text, plan)
    milestones = _keyframe_milestones(audio, plan, frame_count=3)
    output: Dict[str, Dict[str, Any]] = {}
    specs = [
        ("landscape", "xiaojunjun_background", (1280, 720), "16:9"),
        ("vertical", "audio_background", (1080, 1920), "9:16"),
    ]
    for prefix, source_key, size, ratio in specs:
        source_path = (artifacts.get(source_key) or {}).get("path")
        for index, milestone in enumerate(milestones, start=1):
            key = f"keyframe_{prefix}_{index:02d}"
            output_path = artifact_path_for_job(job_id, key, "jpg", created_at)
            caption = captions[(index - 1) % len(captions)] if captions else (plan.get("subtitle") or plan.get("title") or "")
            draw_video_keyframe(
                output_path,
                background_path=source_path,
                size=size,
                ratio=ratio,
                title=plan.get("title") or "MP3 视频编排",
                caption=caption,
                timestamp=milestone.get("label") or "",
            )
            output[key] = _artifact_meta(
                output_path,
                f"{'横屏' if prefix == 'landscape' else '竖屏'}关键帧 {index}",
                renderer="pil:mp3-video-keyframe",
            )
            output[key]["template_version"] = KEYFRAME_ARTIFACT_VERSION
            output[key]["ratio"] = ratio
            output[key]["timestamp_seconds"] = milestone.get("seconds")
    return output


def draw_video_keyframe(
    output_path: str,
    *,
    background_path: Optional[str],
    size: Tuple[int, int],
    ratio: str,
    title: str,
    caption: str,
    timestamp: str,
) -> str:
    w, h = size
    source = _open_image(background_path or "")
    if source:
        img = ImageOps.fit(source.convert("RGB"), (w, h), method=Image.Resampling.LANCZOS)
    else:
        img = Image.new("RGB", (w, h), "#07111f")
        draw = ImageDraw.Draw(img)
        _draw_vertical_gradient(draw, w, h, (6, 12, 26), (9, 23, 42))
        _draw_grid(draw, w, h, spacing=64 if ratio == "16:9" else 80, color=(255, 255, 255, 12))

    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    bottom_alpha = 142 if ratio == "9:16" else 126
    gradient_height = int(h * (0.32 if ratio == "9:16" else 0.26))
    for offset in range(gradient_height):
        alpha = int(bottom_alpha * (offset / max(1, gradient_height - 1)))
        y = h - gradient_height + offset
        overlay_draw.line([(0, y), (w, y)], fill=(2, 6, 14, alpha))
    img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    draw = ImageDraw.Draw(img)

    is_vertical = ratio == "9:16"
    caption_font = _font(48 if is_vertical else 34, bold=True)
    title_font = _font(30 if is_vertical else 24, bold=True)
    time_font = _font(24 if is_vertical else 20, bold=True)
    margin_x = 72 if is_vertical else 82
    caption_box_w = w - margin_x * 2
    caption_lines = _wrap_text(draw, _clean_line(caption), caption_font, caption_box_w, max_lines=2)
    line_h = 66 if is_vertical else 48
    caption_h = len(caption_lines) * line_h + (48 if is_vertical else 34)
    box_bottom = h - (150 if is_vertical else 58)
    box_top = max(40, box_bottom - caption_h)
    draw.rounded_rectangle(
        [margin_x, box_top, w - margin_x, box_bottom],
        radius=22 if is_vertical else 16,
        fill=(5, 12, 24, 205),
        outline=(255, 255, 255, 48),
        width=2,
    )
    text_y = box_top + (24 if is_vertical else 17)
    for line in caption_lines:
        _draw_centered_text(draw, line, w // 2 + 2, text_y + 3, caption_font, fill=(0, 0, 0, 160))
        _draw_centered_text(draw, line, w // 2, text_y, caption_font, fill=(248, 250, 252))
        text_y += line_h

    title_text = _trim_text_to_width(draw, _clean_line(title), title_font, w - margin_x * 2)
    draw.rounded_rectangle(
        [margin_x, 34 if is_vertical else 26, margin_x + 210, 82 if is_vertical else 64],
        radius=24 if is_vertical else 19,
        fill=(15, 23, 42, 190),
    )
    draw.text((margin_x + 22, 46 if is_vertical else 34), "KEYFRAME", font=time_font, fill=(203, 213, 225))
    draw.text((margin_x, 104 if is_vertical else 82), title_text, font=title_font, fill=(226, 232, 240))
    if timestamp:
        time_text = timestamp
        bbox = draw.textbbox((0, 0), time_text, font=time_font)
        pill_w = bbox[2] - bbox[0] + 40
        pill_h = 46 if is_vertical else 38
        x = w - margin_x - pill_w
        y = 38 if is_vertical else 28
        draw.rounded_rectangle([x, y, x + pill_w, y + pill_h], radius=pill_h // 2, fill=(22, 163, 74, 218))
        draw.text((x + 20, y + (10 if is_vertical else 7)), time_text, font=time_font, fill=(255, 255, 255))

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path, quality=95)
    return output_path


def _cover_browser_context():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise Mp3VideoWorkflowError("缺少 playwright 依赖，无法调用前端封面生成器") from exc

    class _ContextManager:
        def __enter__(self):
            self._playwright = sync_playwright().start()
            headless = _bool_env("MP3_VIDEO_COVER_BROWSER_HEADLESS", True)
            args = ["--disable-dev-shm-usage", "--disable-gpu", "--no-sandbox"]
            kwargs = {
                "headless": headless,
                "args": args,
            }
            chrome_path = _cover_chrome_executable_path()
            chrome_channel = _env(
                "MP3_VIDEO_COVER_BROWSER_CHANNEL",
                "REPORT_PDF_GEMINI_CHANNEL",
                os.environ.get("GEMINI_BROWSER_CHANNEL") or "",
            )
            if chrome_path:
                kwargs["executable_path"] = chrome_path
            elif chrome_channel:
                kwargs["channel"] = chrome_channel
            else:
                kwargs["channel"] = "chrome"
            self._browser = _launch_cover_browser(self._playwright.chromium, kwargs)
            self._context = self._browser.new_context(
                viewport={"width": 1440, "height": 1000},
                device_scale_factor=1,
            )
            self._context.set_default_timeout(_int_env("MP3_VIDEO_COVER_BROWSER_TIMEOUT_MS", 30000))
            self._context.route("**/*", _route_cover_render_request)
            return self._context

        def __exit__(self, exc_type, exc, tb):
            try:
                self._context.close()
            finally:
                try:
                    self._browser.close()
                finally:
                    self._playwright.stop()

    return _ContextManager()


def _route_cover_render_request(route):
    url = route.request.url
    parsed = urlparse(url)
    if parsed.scheme in {"http", "https"} and parsed.hostname not in {
        "localhost",
        "127.0.0.1",
        "::1",
    }:
        route.abort()
        return
    route.continue_()


def _launch_cover_browser(chromium, kwargs: Dict[str, Any]):
    attempts = [dict(kwargs)]
    if kwargs.get("executable_path"):
        chrome_channel = dict(kwargs)
        chrome_channel.pop("executable_path", None)
        chrome_channel["channel"] = "chrome"
        attempts.append(chrome_channel)
    if kwargs.get("executable_path") or kwargs.get("channel"):
        fallback = dict(kwargs)
        fallback.pop("executable_path", None)
        fallback.pop("channel", None)
        attempts.append(fallback)

    errors = []
    for index, attempt in enumerate(attempts, start=1):
        try:
            return chromium.launch(**attempt)
        except Exception as exc:
            target = attempt.get("executable_path") or attempt.get("channel") or "bundled-chromium"
            errors.append(f"{index}:{target}: {str(exc).splitlines()[0]}")
    raise Mp3VideoWorkflowError(f"封面生成浏览器启动失败: {' | '.join(errors)}")


def _cover_chrome_executable_path() -> str:
    configured = _env(
        "MP3_VIDEO_COVER_CHROME_PATH",
        "REPORT_PDF_GEMINI_CHROME_PATH",
        os.environ.get("GEMINI_BROWSER_CHROME_PATH") or "",
    )
    if configured and Path(configured).expanduser().exists():
        return str(Path(configured).expanduser())
    for candidate in (
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/usr/bin/chromium-browser",
        "/usr/bin/chromium",
    ):
        if os.path.exists(candidate):
            return candidate
    return ""


def _render_canvas_artifact(
    context,
    *,
    page_name: str,
    function_name: str,
    payload: Dict[str, Any],
    output_path: str,
    expected_size: Optional[Tuple[int, int]] = None,
) -> None:
    page = context.new_page()
    try:
        last_error: Optional[Exception] = None
        for url in _frontend_page_urls(page_name):
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=_int_env("MP3_VIDEO_COVER_PAGE_TIMEOUT_MS", 25000))
                page.wait_for_function(f"() => typeof window.{function_name} === 'function'")
                result = page.evaluate(f"(payload) => window.{function_name}(payload)", payload)
                data_url = result.get("dataUrl") if isinstance(result, dict) else None
                returned_size = _canvas_result_size(result)
                if expected_size and returned_size and returned_size != expected_size:
                    raise Mp3VideoWorkflowError(
                        f"前端封面生成器返回尺寸异常: {page_name} {returned_size[0]}x{returned_size[1]}，期望 {expected_size[0]}x{expected_size[1]}"
                    )
                _write_data_url(data_url, output_path, expected_size=expected_size, label=page_name)
                return
            except Exception as exc:
                last_error = exc
        raise Mp3VideoWorkflowError(f"无法调用前端封面页面 {page_name} / {function_name}: {last_error}")
    finally:
        page.close()


def _frontend_page_urls(page_name: str) -> List[str]:
    urls = []
    base_url = _env("MP3_VIDEO_FRONTEND_BASE_URL", "FRONTEND_BASE_URL", "http://localhost:8888").strip()
    if base_url and base_url.lower() not in {"file", "file://"}:
        urls.append(f"{base_url.rstrip('/')}/{page_name.lstrip('/')}")
    local_path = project_root() / "src" / "frontend" / page_name
    if local_path.exists():
        urls.append(local_path.resolve().as_uri())
    return urls


def _canvas_result_size(result: Any) -> Optional[Tuple[int, int]]:
    if not isinstance(result, dict):
        return None
    try:
        width = int(result.get("width") or 0)
        height = int(result.get("height") or 0)
    except (TypeError, ValueError):
        return None
    return (width, height) if width > 0 and height > 0 else None


def _finance_cover_payload(plan: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "mainTitle": _cover_main_title(plan),
        "subTitle": _finance_cover_highlight_text(plan),
        "dateText": plan.get("date_text") or datetime.now().strftime("%Y.%m.%d"),
        "mainColor": "#ffffff",
        "subColor": "#FFE900",
        "mimeType": "image/jpeg",
        "quality": 0.95,
    }


def _audio_background_payload(plan: Dict[str, Any], infographic_data_url: str) -> Dict[str, Any]:
    return {
        "format": "video_bg_audio",
        "showGuides": False,
        "inst": "深度研报",
        "mainTitle": _cover_lines(plan.get("manual_cover_title") or plan.get("cover_title") or plan.get("title"), max_chars=10, max_lines=2),
        "subTitle": _cover_lines(plan.get("manual_cover_subtitle") or plan.get("cover_subtitle") or plan.get("subtitle"), max_chars=18, max_lines=2),
        "footerText": "此刻此声",
        "emotion": "bull",
        "imageDataUrl": infographic_data_url,
        "mimeType": "image/jpeg",
        "quality": 0.95,
    }


def _closed_door_payload(plan: Dict[str, Any], *, include_date: bool) -> Dict[str, Any]:
    return {
        "mainTitle": plan.get("xiaojunjun_title") or _cover_main_title(plan),
        "subTitle": _highlight_text(plan, preferred_key="xiaojunjun_subtitle"),
        "dateText": plan.get("date_text") if include_date else "",
        "subtitleSafeSpace": False,
        "mainColor": "#FFFFFF",
        "subColor": "#FFE900",
        "backgroundDataUrl": _closed_door_reference_data_url(),
        "mimeType": "image/jpeg",
        "quality": 0.95,
    }


def _cover_main_title(plan: Dict[str, Any]) -> str:
    return _clip_text(plan.get("cover_main_title") or "内资闭门会议", 16) or "内资闭门会议"


def _highlight_text(plan: Dict[str, Any], *, preferred_key: str) -> str:
    if preferred_key == "xiaojunjun_subtitle":
        manual = plan.get("manual_xiaojunjun_subtitle")
        if manual:
            return _cover_lines(manual, max_chars=18, max_lines=3)
        closed_visual = _closed_door_visual_lines(plan.get("xiaojunjun_video_title"), max_units=18, max_lines=3)
        if closed_visual:
            return closed_visual
    value = plan.get(preferred_key) or plan.get("cover_subtitle") or plan.get("subtitle") or plan.get("title")
    return _cover_lines(value, max_chars=18, max_lines=3)


def _finance_cover_highlight_text(plan: Dict[str, Any]) -> str:
    manual = plan.get("manual_finance_cover_subtitle")
    if manual:
        return _finance_cover_copy_lines(manual)
    value = plan.get("finance_cover_subtitle")
    if value:
        return _finance_cover_copy_lines(value)
    closed_visual = _closed_door_visual_lines(plan.get("ciketc_video_title"), max_units=14, max_lines=3)
    if closed_visual:
        return closed_visual
    value = plan.get("cover_subtitle") or plan.get("subtitle") or plan.get("title")
    return _finance_cover_copy_lines(value)


def _finance_cover_title(value: Any) -> str:
    text = _normalize_title_candidate(value)
    if not text:
        return ""
    compact = text
    compact = re.sub(r"多数人还在用\s*", "", compact)
    compact = compact.replace("和信用周期分析", "和信用周期看")
    compact = compact.replace("看美元美债时", "看美元美债")
    compact = compact.replace("我们已全部抛掉", "抛掉旧框架")
    compact = compact.replace("美债利率回不去了", "美债回不去")
    compact = compact.replace("美元指数破不了90", "美元难破90")
    compact = compact.replace("黄金今年不行但等这个信号就是暴利机会", "黄金等这个信号")
    parts = _title_clause_parts(compact)
    if re.search(r"GDP|信用周期", compact, flags=re.I) and "抛掉" in compact:
        if parts:
            parts[0] = "抛掉GDP和信用周期"
    question_parts = [part for part in parts if re.search(r"科技|美债|美元|黄金|算力|泡沫|信用", part)]
    parts = question_parts or parts
    if len(parts) >= 3:
        third_parts = parts[3:5] if len(parts) > 3 else parts[2:3]
        lines = [parts[0], _join_short_title_parts(parts[1:3], max_chars=13), _join_short_title_parts(third_parts, max_chars=13)]
    else:
        lines = _smart_cover_lines(compact, max_chars=13, max_lines=3)
    return "\n".join(_short_finance_cover_line(line, 13) for line in lines if _clean_line(line))[:80].strip()


def _finance_cover_copy_lines(value: Any) -> str:
    text = _clean_multiline(value)
    if not text:
        return ""
    raw_lines = [
        _clean_line(line).strip("“”\"'《》")
        for line in text.split("\n")
        if _clean_line(line) and not _is_cover_process_text(line)
    ]
    if not raw_lines:
        return ""
    if len(raw_lines) == 1:
        return _finance_cover_title(raw_lines[0])
    lines = [_short_finance_cover_line(line, 13) for line in raw_lines[:3]]
    return "\n".join(line for line in lines if line)


def _short_finance_cover_line(value: Any, max_chars: int = 13) -> str:
    clean = _clean_line(value).strip("，,、。.;；:：!！?？ ")
    if len(clean) <= max_chars:
        return clean
    compact = clean
    replacements = (
        ("市场恐慌", "市场恐慌"),
        ("全球化工", "化工"),
        ("供给侧", "供给"),
        ("逻辑已定", ""),
        ("为何", ""),
        ("高呼", ""),
        ("短期", ""),
        ("当前", ""),
        ("当下", ""),
        ("砸出", ""),
        ("品类", ""),
        ("已经", "已"),
        ("可能", ""),
        ("是否", ""),
        ("的", ""),
    )
    for old, new in replacements:
        compact = compact.replace(old, new)
    compact = _clean_line(compact).strip("，,、。.;；:：!！?？ ")
    return _clip_finance_visual_line(compact or clean, max_units=max_chars)


def _clip_finance_visual_line(value: Any, *, max_units: float = 13.0) -> str:
    text = _clean_line(value).strip("，,、。.;；:：!！?？ ")
    if not text:
        return ""
    output = ""
    units = 0.0
    for char in text:
        char_units = 0.55 if char.isascii() else 1.0
        if output and units + char_units > max_units:
            break
        output += char
        units += char_units
    return output.strip("，,、。.;；:：!！?？ ")


def _vertical_cover_title(value: Any) -> str:
    text = _normalize_title_candidate(value)
    asset_title = _asset_focused_vertical_title(text)
    if asset_title:
        return asset_title
    first = _title_clause_parts(text)[0] if _title_clause_parts(text) else text
    first = first.replace("可能已经过剩", "可能过剩").replace("可能已经", "可能")
    first = first.replace("？", "?")
    first = re.sub(r"\?$", "？", first)
    if "算力投资" in first and "过剩" in first:
        return "算力投资\n可能过剩？"
    if len(first) <= 6:
        return first
    for marker in ("可能", "已经", "会不会", "能否", "是否", "还能"):
        index = first.find(marker)
        if 2 <= index <= 8:
            return "\n".join([first[:index], first[index:]])[:24]
    return _cover_lines(first, max_chars=8, max_lines=2)


def _vertical_cover_subtitle(value: Any, *, fallback: Any = "") -> str:
    text = _normalize_title_candidate(value)
    asset_subtitle = _asset_focused_vertical_subtitle(text)
    if asset_subtitle:
        return asset_subtitle
    parts = _title_clause_parts(text)
    if len(parts) > 1:
        for part in parts[1:]:
            clean = _clip_title_line(part, 16)
            if 5 <= len(clean) <= 16:
                return clean
    if "三条脆弱线索" in text:
        return "群体性误判的三条脆弱线索"
    if "国家信用" in text:
        return "回归国家信用看美债"
    return _clip_title_line(fallback or text, 16)


def _format_planner_vertical_title(value: Any) -> str:
    text = _clean_multiline(value)
    if not text:
        return ""
    raw_lines = [
        _clean_line(line).strip("“”\"'《》")
        for line in text.split("\n")
        if _clean_line(line) and not _is_cover_process_text(line)
    ]
    if not raw_lines:
        return ""
    if len(raw_lines) >= 2:
        lines = [_clip_title_line(line, 10) for line in raw_lines[:2]]
    else:
        lines = [
            _clip_title_line(line, 10)
            for line in _cover_lines(raw_lines[0], max_chars=10, max_lines=2).split("\n")
            if _clean_line(line)
        ]
    return "\n".join(line for line in lines[:2] if line)


def _format_planner_vertical_subtitle(value: Any) -> str:
    text = _clean_multiline(value)
    if not text:
        return ""
    for line in text.split("\n"):
        clean = _clean_line(line).strip("“”\"'《》")
        if clean and not _is_cover_process_text(clean):
            return _clip_title_line(clean, 16)
    return ""


def _format_planner_finance_cover_subtitle(value: Any) -> str:
    return _finance_cover_copy_lines(value)


def _format_planner_xiaojunjun_subtitle(value: Any, *, disallow: Optional[List[Any]] = None) -> str:
    text = _clean_multiline(value)
    if not text:
        return ""
    raw_lines = [
        _clean_line(line).strip("“”\"'《》")
        for line in text.split("\n")
        if _clean_line(line) and not _is_cover_process_text(line)
    ]
    if not raw_lines:
        return ""
    if len(raw_lines) == 1:
        raw_lines = _cover_lines(raw_lines[0], max_chars=18, max_lines=3).split("\n")
    result = "\n".join(_clip_title_line(line, 18) for line in raw_lines[:3] if _clean_line(line))
    if _is_same_cover_copy(result, disallow or []):
        return ""
    return result


def _first_distinct_xiaojunjun_copy(*values: Any, disallow: Optional[List[Any]] = None) -> str:
    blocked = list(disallow or [])
    for value in values:
        result = _format_planner_xiaojunjun_subtitle(value, disallow=blocked)
        if result:
            return result
    return ""


def _xiaojunjun_cover_subtitle(value: Any, *, fallback: Any = "") -> str:
    text = _normalize_title_candidate(value) or _clean_line(fallback)
    if not text:
        return ""
    pipe_parts = [
        _clip_title_line(part, 18)
        for part in re.split(r"[|｜]", text)
        if _clean_line(part) and not _is_cover_process_text(part)
    ]
    if len(pipe_parts) >= 2:
        return "\n".join(pipe_parts[:3])
    parts = _title_clause_parts(text)
    if len(parts) >= 2:
        return "\n".join(_clip_title_line(part, 18) for part in parts[:3] if _clean_line(part))
    return _cover_lines(text, max_chars=18, max_lines=3)


def _is_same_cover_copy(value: Any, others: List[Any]) -> bool:
    key = _cover_compare_key(value)
    if not key:
        return False
    return any(key == _cover_compare_key(other) for other in others if _cover_compare_key(other))


def _cover_compare_key(value: Any) -> str:
    return re.sub(r"[\s\n\r\t，,。.;；:：!！?？|｜、'\"“”‘’《》\-_()（）]+", "", _clean_line(value)).lower()


def _is_cover_process_text(value: Any) -> bool:
    text = _clean_line(value)
    return bool(re.search(
        r"DS\s*v?\d|IMA|Gemini|转录全文|Generated from transcript|推荐截取|源文件|最精华音频片段|"
        r"精华音频片段|音频片段|片段说明|开始时间戳|结束时间戳|时间戳|无 Gemini upload|"
        r"xxxx|当日日期|好的[，,]?\s*收到|收到您的指令|以下是基于|处理要求|输出结果|格式来写|"
        r"请你|尝试优化|使用\s*内资闭门会",
        text,
        flags=re.I,
    ))


def _asset_focused_vertical_title(value: Any) -> str:
    text = _clean_line(value)
    assets = _extract_finance_asset_terms(text)
    if len(assets) < 2:
        return ""
    line1 = "".join(assets[:2])
    tail_asset = next((asset for asset in assets[2:] if asset not in line1), "")
    tail_word = _asset_opportunity_word(text)
    line2 = f"{tail_asset}{tail_word}" if tail_asset else _asset_fallback_theme(text)
    return "\n".join([
        _clip_title_line(line1, 8),
        _clip_title_line(line2, 8),
    ])


def _asset_focused_vertical_subtitle(value: Any) -> str:
    text = _clean_line(value)
    if len(_extract_finance_asset_terms(text)) < 2:
        return ""
    if "国家信用" in text:
        if re.search(r"回归|定价|锚|秤|底层逻辑", text):
            return "回归国家信用定价锚"
        return "国家信用重新定价"
    info_diff = re.search(r"((?:20\d{2})?[^，。；;？?！!]{0,8}信息差)", text)
    if info_diff:
        return _clip_title_line(info_diff.group(1), 16)
    if re.search(r"窗口|机会|信号", text):
        return "资产重估机会窗口"
    return ""


def _extract_finance_asset_terms(value: Any) -> List[str]:
    text = _clean_line(value)
    priority = (
        "美债", "美元", "黄金", "人民币", "日元", "港股", "A股", "美股", "纳指",
        "科技股", "半导体", "铜", "原油", "油价", "国债", "债券", "利率", "汇率",
    )
    terms = []
    for term in priority:
        if term in text and term not in terms:
            terms.append(term)
    return terms


def _asset_opportunity_word(value: Any) -> str:
    text = _clean_line(value)
    if re.search(r"窗口|机会|暴利|赚钱|信号", text):
        return "窗口"
    if re.search(r"定价|锚|信用|秤", text):
        return "定价"
    if re.search(r"拐点|反转", text):
        return "拐点"
    return "机会"


def _asset_fallback_theme(value: Any) -> str:
    text = _clean_line(value)
    if re.search(r"定价|锚|信用|秤", text):
        return "重新定价"
    if re.search(r"信息差", text):
        return "信息差"
    return "机会窗口"


def _title_clause_parts(value: Any) -> List[str]:
    text = _normalize_title_candidate(value)
    text = re.sub(r"[‘’“”\"《》]", "", text)
    text = text.replace("——", "？").replace("--", "？")
    raw_parts = re.split(r"[?？。；;！!]+|以及|，以及|、以及", text)
    parts = []
    for part in raw_parts:
        clean = _clean_line(part).strip("，,、：: ")
        clean = re.sub(r"^一个", "", clean)
        if clean:
            parts.append(clean)
    return parts


def _join_short_title_parts(parts: List[str], *, max_chars: int) -> str:
    line = ""
    for part in parts:
        clean = _clip_title_line(part, max_chars)
        candidate = f"{line}，{clean}" if line else clean
        if len(candidate) <= max_chars:
            line = candidate
    return line or (_clip_title_line(parts[0], max_chars) if parts else "")


def _smart_cover_lines(value: Any, *, max_chars: int, max_lines: int) -> List[str]:
    parts = _title_clause_parts(value)
    lines = []
    for part in parts:
        if len(lines) >= max_lines:
            break
        lines.extend(_cover_lines(part, max_chars=max_chars, max_lines=max_lines - len(lines)).split("\n"))
    return lines[:max_lines] or _cover_lines(value, max_chars=max_chars, max_lines=max_lines).split("\n")


def _clip_title_line(value: Any, max_chars: int) -> str:
    clean = _clean_line(value).strip("，,、。.;；:：!！?？ ")
    return clean[:max_chars]


def _image_data_url(path_value: str) -> str:
    path = _resolve_project_path(path_value)
    if not path or not path.exists():
        raise Mp3VideoWorkflowError(f"前端封面生成器需要的图片不存在: {path_value}")
    mime = mimetypes.guess_type(str(path))[0] or "image/png"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _closed_door_reference_data_url() -> str:
    path = project_root() / "src" / "frontend" / "assets" / "cover" / "default" / "closed-door-reference-bg.png"
    return _image_data_url(str(path)) if path.exists() else ""


def _write_data_url(
    data_url: Optional[str],
    output_path: str,
    *,
    expected_size: Optional[Tuple[int, int]] = None,
    label: str = "前端封面生成器",
) -> None:
    if not data_url or "," not in data_url:
        raise Mp3VideoWorkflowError("前端封面生成器未返回有效图片")
    header, encoded = data_url.split(",", 1)
    if not header.lower().startswith("data:image/"):
        raise Mp3VideoWorkflowError(f"{label} 返回的不是图片 dataURL")
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    try:
        payload = base64.b64decode(encoded, validate=True)
    except Exception as exc:
        raise Mp3VideoWorkflowError(f"{label} 返回的图片 dataURL 无法解码") from exc
    Path(output_path).write_bytes(payload)
    if not _image_file_ready(output_path, expected_size=expected_size):
        expected = f"，期望 {expected_size[0]}x{expected_size[1]}" if expected_size else ""
        raise Mp3VideoWorkflowError(f"{label} 生成的图片不可用或尺寸异常{expected}: {output_path}")


def create_publisher_video_task(
    *,
    parent_id: str,
    user_id: Optional[str],
    audio: Dict[str, Any],
    plan: Dict[str, Any],
    image_path: str,
    role: str,
    aspect_ratio: str,
    generate_subtitles: bool = False,
    force_new: bool = False,
    created_not_before: Optional[datetime] = None,
) -> PublicationTask:
    title = _publisher_video_title(plan, role=role)
    generate_subtitles = bool(generate_subtitles)
    audio_segment = audio_segment_from_plan(audio, plan)
    audio_signature = _audio_segment_signature(audio_segment)
    existing = None
    if not force_new or created_not_before is not None:
        existing = _find_existing_publisher_video_task(
            parent_id,
            role=role,
            aspect_ratio=aspect_ratio,
            generate_subtitles=generate_subtitles,
            audio_signature=audio_signature,
            created_not_before=created_not_before,
        )
    if existing:
        _sync_publisher_video_task_title(existing, title)
        return existing

    subtitle_y = 84 if aspect_ratio == "9:16" else 86
    params = {
        "feature": "publisher",
        "user_id": user_id,
        "parent_feature": FEATURE_NAME,
        "parent_task_id": parent_id,
        "source_feature": FEATURE_NAME,
        "source_task_id": parent_id,
        "audio_segments": [audio_segment],
        "aspect_ratio": aspect_ratio,
        "generate_subtitles": generate_subtitles,
        "asr_model": os.environ.get("MP3_VIDEO_ASR_MODEL", "medium"),
        "video": {
            "enabled": True,
            "image_path": image_path,
            "video_path": "",
            "media_type": "image",
            "sub_burn_enabled": generate_subtitles,
            "asr_enabled": generate_subtitles,
            "asr_model": os.environ.get("MP3_VIDEO_ASR_MODEL", "medium"),
            "subtitle_position": {"y_pct": subtitle_y},
            "subtitle_size": "normal",
            "cta": None,
        },
        "thumbnail": {},
        "bilibili": {
            "enabled": False,
            "title": title,
            "desc": "MP3 视频编排自动生成",
            "tags": ["财经", "音频", "视频"],
        },
        "youtube": {"enabled": False},
        "metadata": {
            "source": FEATURE_NAME,
            "parent_task_id": parent_id,
            "role": role,
            "title": title,
            "generate_subtitles": generate_subtitles,
            "force_new": bool(force_new),
            "audio": audio,
            "audio_signature": audio_signature,
        },
    }
    child = PublicationTask(
        status="pending",
        current_step="queued",
        progress=0,
        params=params,
        result={
            "metadata": params["metadata"],
            "source_task_id": parent_id,
            "generate_subtitles": generate_subtitles,
            "audio_signature": audio_signature,
            "video_path": None,
            "cover_path": None,
        },
    )
    db.session.add(child)
    db.session.flush()
    child_id = str(child.id)
    db.session.commit()
    async_result = apply_async_after_db_release(
        run_publication_pipeline,
        args=[child_id],
        queue="render",
        retry=False,
    )
    PublicationTask.query.filter(PublicationTask.id == child_id).update(
        {PublicationTask.celery_task_id: async_result.id},
        synchronize_session=False,
    )
    db.session.commit()
    return _load_task(child_id, *TASK_PUBLIC_STATUS_COLUMNS)


def _publisher_video_title(plan: Dict[str, Any], *, role: str) -> str:
    if role == "xiaojunjun_landscape":
        return _clean_closed_door_video_title(plan.get("xiaojunjun_video_title"), expected_separator="/") or _format_closed_door_video_title(
            plan.get("title") or plan.get("xiaojunjun_subtitle"),
            separator="/",
            date_text=plan.get("date_text"),
        ) or "MP3 视频编排"
    if role == "ciketc_vertical":
        return _clean_closed_door_video_title(plan.get("ciketc_video_title"), expected_separator="|") or _format_closed_door_video_title(
            plan.get("title") or plan.get("cover_subtitle"),
            separator="|",
            date_text=plan.get("date_text"),
        ) or "MP3 视频编排"
    return _clip_text(plan.get("title") or "MP3 视频编排", 180)


def _sync_publisher_video_task_title(task: PublicationTask, title: str) -> None:
    title = _clip_text(title, 180)
    if not task or not title or task.status in TERMINAL_STATUSES:
        return
    params = dict(task.params or {})
    result = dict(task.result or {})
    changed = False

    bilibili = dict(params.get("bilibili") or {})
    if bilibili.get("title") != title:
        bilibili["title"] = title
        params["bilibili"] = bilibili
        changed = True

    metadata = dict(params.get("metadata") or {})
    if metadata.get("title") != title:
        metadata["title"] = title
        params["metadata"] = metadata
        changed = True

    result_metadata = dict(result.get("metadata") or {})
    if result_metadata.get("title") != title:
        result_metadata["title"] = title
        result["metadata"] = result_metadata
        changed = True

    if not changed:
        return
    task.params = params
    task.result = result
    flag_modified(task, "params")
    flag_modified(task, "result")


def _find_existing_publisher_video_task(
    parent_id: str,
    *,
    role: str,
    aspect_ratio: str,
    generate_subtitles: Optional[bool] = None,
    audio_signature: Optional[str] = None,
    created_not_before: Optional[datetime] = None,
) -> Optional[PublicationTask]:
    query = (
        PublicationTask.query
        .filter(PublicationTask.feature == "publisher")
        .filter(PublicationTask.source_task_id == parent_id)
        .filter(PublicationTask.publisher_child_role == role)
        .filter(PublicationTask.publisher_aspect_ratio == aspect_ratio)
        .options(load_only(*TASK_PUBLISHER_MATCH_COLUMNS))
        .order_by(PublicationTask.created_at.desc())
    )
    if generate_subtitles is not None:
        query = query.filter(PublicationTask.publisher_generate_subtitles.is_(bool(generate_subtitles)))
    if audio_signature:
        query = query.filter(PublicationTask.publisher_audio_signature == audio_signature)
    if created_not_before is not None:
        query = query.filter(PublicationTask.created_at >= created_not_before)
    matching = query.limit(30).all()
    if not matching:
        return None

    completed = next(
        (task for task in matching if task.status == "completed" and _path_ready(task.task_video_path)),
        None,
    )
    if completed:
        selected = _load_task(completed.id, *TASK_PUBLIC_COLUMNS)
        if not selected:
            return None
        _cancel_duplicate_publisher_tasks(
            matching,
            keep_id=completed.id,
        )
        return selected

    active = [task for task in matching if task.status not in TERMINAL_STATUSES]
    if active:
        keep = _load_task(active[0].id, *TASK_PUBLIC_COLUMNS)
        if not keep:
            return None
        keep = _recover_completed_publisher_child_from_disk(keep)
        _cancel_duplicate_publisher_tasks(
            active[1:],
            keep_id=keep.id,
        )
        return keep
    return None


def _publisher_child_role(task: PublicationTask) -> str:
    if _model_attr_loaded(task, "publisher_child_role"):
        projected = str(task.publisher_child_role or "").strip()
        if projected or not _model_attr_loaded(task, "params"):
            return projected
    params = task.params if isinstance(task.params, dict) else {}
    metadata = params.get("metadata") if isinstance(params.get("metadata"), dict) else {}
    result = task.result if isinstance(task.result, dict) else {}
    result_metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
    return str(metadata.get("role") or result_metadata.get("role") or "").strip()


def _publisher_child_aspect_ratio(task: PublicationTask) -> str:
    if _model_attr_loaded(task, "publisher_aspect_ratio"):
        projected = str(task.publisher_aspect_ratio or "").strip()
        if projected or not _model_attr_loaded(task, "params"):
            return projected
    params = task.params if isinstance(task.params, dict) else {}
    return str(params.get("aspect_ratio") or "").strip()


def _publisher_child_generate_subtitles(task: PublicationTask, *, default: bool = True) -> bool:
    if _model_attr_loaded(task, "publisher_generate_subtitles"):
        projected = task.publisher_generate_subtitles
        if projected is not None or not _model_attr_loaded(task, "params"):
            return bool(default) if projected is None else bool(projected)
    params = task.params if isinstance(task.params, dict) else {}
    if "generate_subtitles" in params:
        return _truthy(params.get("generate_subtitles"), default=default)
    video = params.get("video") if isinstance(params.get("video"), dict) else {}
    if "sub_burn_enabled" in video:
        return _truthy(video.get("sub_burn_enabled"), default=default)
    metadata = params.get("metadata") if isinstance(params.get("metadata"), dict) else {}
    if "generate_subtitles" in metadata:
        return _truthy(metadata.get("generate_subtitles"), default=default)
    result = task.result if isinstance(task.result, dict) else {}
    if "generate_subtitles" in result:
        return _truthy(result.get("generate_subtitles"), default=default)
    result_metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
    if "generate_subtitles" in result_metadata:
        return _truthy(result_metadata.get("generate_subtitles"), default=default)
    return bool(default)


def _publisher_child_matches_generate_subtitles(task: PublicationTask, expected: Optional[bool]) -> bool:
    if expected is None:
        return True
    return _publisher_child_generate_subtitles(task, default=True) == bool(expected)


def _publisher_child_audio_signature(task: PublicationTask) -> Optional[str]:
    if _model_attr_loaded(task, "publisher_audio_signature"):
        projected = str(task.publisher_audio_signature or "").strip()
        if projected or not _model_attr_loaded(task, "params"):
            return projected or None
    params = task.params if isinstance(task.params, dict) else {}
    metadata = params.get("metadata") if isinstance(params.get("metadata"), dict) else {}
    result = task.result if isinstance(task.result, dict) else {}
    result_metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
    signature = (
        metadata.get("audio_signature")
        or result_metadata.get("audio_signature")
        or result.get("audio_signature")
    )
    if signature:
        return str(signature)
    segments = params.get("audio_segments") if isinstance(params.get("audio_segments"), list) else []
    if segments:
        return _audio_segment_signature(segments[0])
    return None


def _publisher_child_matches_audio_signature(task: PublicationTask, expected: Optional[str]) -> bool:
    if not expected:
        return True
    return _publisher_child_audio_signature(task) == expected


def _cancel_duplicate_publisher_tasks(tasks: List[PublicationTask], *, keep_id: str) -> None:
    changed = False
    for task in tasks:
        if not task or task.id == keep_id or task.status in TERMINAL_STATUSES:
            continue
        _revoke_celery_task(task)
        error = f"重复视频子任务已取消，保留 {keep_id}"
        updated = db.session.execute(
            text("""
                UPDATE public.publication_tasks
                SET status = 'canceled',
                    current_step = 'canceled',
                    error_message = :error,
                    completed_at = NOW(),
                    result = COALESCE(result, '{}'::jsonb)
                        || jsonb_build_object('error', CAST(:error_json AS jsonb)),
                    updated_at = NOW()
                WHERE id = :task_id
                  AND status NOT IN ('completed', 'failed', 'canceled')
            """),
            {
                "task_id": str(task.id),
                "error": error,
                "error_json": json.dumps(error, ensure_ascii=False),
            },
        )
        changed = bool(updated.rowcount) or changed
    if changed:
        db.session.commit()


def _revoke_celery_task(task: PublicationTask) -> None:
    celery_task_id = getattr(task, "celery_task_id", None)
    if not celery_task_id:
        return
    try:
        celery_app.control.revoke(celery_task_id, terminate=False)
    except Exception as exc:
        logger.warning("撤销重复 Celery 子任务失败: task=%s celery=%s error=%s", task.id, celery_task_id, exc)


def audio_segment_from_plan(audio: Dict[str, Any], plan: Dict[str, Any]) -> Dict[str, Any]:
    duration = float(audio.get("duration_seconds") or 0)
    plan_audio = plan.get("audio") if isinstance(plan.get("audio"), dict) else {}
    start = max(0.0, _parse_seconds(plan_audio.get("start_seconds"), default=0.0) or 0.0)
    end = _parse_seconds(plan_audio.get("end_seconds"), default=None)
    if duration and start >= duration:
        start = 0.0
    end_offset = 0.0
    if end is not None and duration:
        end = min(duration, max(start + 0.1, end))
        end_offset = max(0.0, duration - end)
    return {
        "path": audio.get("path"),
        "subtitle": "",
        "start": round(start, 3),
        "end_offset": round(end_offset, 3),
        "speed": DEFAULT_SPEED,
    }


def _audio_segment_signature(segment: Optional[Dict[str, Any]]) -> Optional[str]:
    if not isinstance(segment, dict) or not segment.get("path"):
        return None
    payload = {
        "path": str(segment.get("path") or ""),
        "start": round(float(segment.get("start") or 0), 3),
        "end_offset": round(float(segment.get("end_offset") or 0), 3),
        "speed": round(float(segment.get("speed") or DEFAULT_SPEED), 3),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def draw_finance_cover(output_path: str, plan: Dict[str, Any]) -> str:
    w, h = 1920, 1080
    accent = _accent_color(plan.get("emotion"))
    img = Image.new("RGB", (w, h), "#08111f")
    draw = ImageDraw.Draw(img)
    _draw_vertical_gradient(draw, w, h, (6, 14, 27), (12, 31, 55))
    _draw_grid(draw, w, h, spacing=64, color=(255, 255, 255, 13))
    _draw_market_silhouette(draw, w, h)
    _draw_curve(draw, w, h, accent)

    title_font = _font(132, bold=True)
    sub_font = _font(58, bold=True)
    label_font = _font(34, bold=True)
    footer_font = _font(40, bold=True)
    margin = 130
    draw.rectangle([margin, 150, margin + 12, 195], fill=accent)
    draw.text((margin + 34, 151), "此刻此声 · 财经音频", font=label_font, fill=(231, 238, 248))
    next_y = _draw_wrapped_text(
        draw,
        plan.get("cover_title") or plan.get("title"),
        margin,
        330,
        1160,
        title_font,
        fill=accent,
        line_height=154,
        shadow=(0, 0, 0, 150),
        max_lines=3,
    )
    subtitle = plan.get("cover_subtitle") or plan.get("subtitle")
    if subtitle:
        _draw_wrapped_text(draw, subtitle, margin, next_y + 42, 1150, sub_font, fill=(255, 255, 255), line_height=76, max_lines=2)
    _draw_footer_mark(draw, margin, h - 122, "此刻此声", accent, footer_font)
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path, quality=95)
    return output_path


def draw_audio_background(output_path: str, plan: Dict[str, Any], infographic_path: str) -> str:
    w, h = 1080, 1920
    accent = _accent_color(plan.get("emotion"))
    img = Image.new("RGB", (w, h), "#060b16")
    draw = ImageDraw.Draw(img)
    _draw_vertical_gradient(draw, w, h, (6, 12, 26), (4, 8, 20))
    _draw_grid(draw, w, h, spacing=80, color=(255, 255, 255, 10))

    source = _open_image(infographic_path)
    if source:
        faint = ImageOps.fit(source.convert("RGB"), (w, h), method=Image.Resampling.LANCZOS).filter(ImageFilter.GaussianBlur(radius=1.5))
        overlay = Image.new("RGB", (w, h), "#060b16")
        img.paste(Image.blend(overlay, faint, 0.15))
        draw = ImageDraw.Draw(img)

    margin = 80
    max_w = w - margin * 2
    label_font = _font(36, bold=True)
    sub_font = _font(55, bold=True)
    footer_font = _font(40, bold=True)
    draw.rectangle([margin, 120, margin + 10, 160], fill=accent)
    draw.text((margin + 25, 122), "深度研报 · 研报解构", font=label_font, fill=(226, 232, 240))
    next_y = _draw_two_line_title(
        draw,
        plan.get("cover_title") or plan.get("title"),
        margin,
        260,
        max_w,
        base_size=90,
        min_size=76,
        fill=accent,
        shadow=(0, 0, 0, 120),
    )
    subtitle = plan.get("cover_subtitle") or plan.get("subtitle")
    if subtitle:
        next_y = _draw_wrapped_text(draw, subtitle, margin, next_y + 40, max_w, sub_font, fill=(255, 255, 255), line_height=75, max_lines=2)

    box_y = max(next_y + 78, 550)
    box_h = 550
    draw.rounded_rectangle([margin, box_y, margin + max_w, box_y + box_h], radius=20, fill=(255, 255, 255, 12), outline=(255, 255, 255, 32), width=4)
    if source:
        fitted = ImageOps.fit(source.convert("RGB"), (max_w, box_h), method=Image.Resampling.LANCZOS)
        mask = Image.new("L", (max_w, box_h), 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, max_w, box_h], radius=20, fill=255)
        img.paste(fitted, (margin, box_y), mask)

    _draw_footer_mark(draw, margin, 1750, "此刻此声", accent, footer_font)
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path, quality=95)
    return output_path


def draw_transcript_infographic(
    output_path: str,
    source_text: str,
    *,
    audio: Dict[str, Any],
    plan: Optional[Dict[str, Any]] = None,
) -> str:
    w, h = 1280, 720
    img = Image.new("RGB", (w, h), "#f8fafc")
    draw = ImageDraw.Draw(img)
    _draw_vertical_gradient(draw, w, h, (248, 250, 252), (226, 232, 240))
    _draw_grid(draw, w, h, spacing=48, color=(15, 23, 42, 10))

    accent = (22, 163, 74)
    title_font = _font(48, bold=True)
    body_font = _font(30, bold=False)
    small_font = _font(20, bold=False)

    margin = 70
    draw.rounded_rectangle([margin, 54, w - margin, h - 54], radius=28, fill=(255, 255, 255), outline=(203, 213, 225), width=2)
    draw.rectangle([margin, 54, margin + 12, h - 54], fill=accent)

    plan = plan if isinstance(plan, dict) else {}
    fallback_copy = _infographic_copy_from_source(
        source_text,
        fallback_title=(audio.get("filename") or "音频编排结果"),
    )
    title_text = (
        _clean_infographic_copy(plan.get("infographic_title"), max_chars=44)
        or fallback_copy.get("infographic_title")
        or "核心投资观点"
    )
    _draw_wrapped_text(draw, title_text, margin + 40, 100, w - margin * 2 - 96, title_font, fill=(15, 23, 42), line_height=62, max_lines=2)

    subtitle = (
        _clean_infographic_copy(plan.get("infographic_subtitle"), max_chars=72)
        or fallback_copy.get("infographic_subtitle")
    )
    y = 250
    if subtitle:
        y = _draw_wrapped_text(draw, subtitle, margin + 40, y, w - margin * 2 - 96, body_font, fill=(51, 65, 85), line_height=42, max_lines=2) + 24

    bullets = _normalize_infographic_points(plan.get("infographic_points"))
    if len(bullets) < 3:
        bullets = _merge_infographic_points(bullets, fallback_copy.get("infographic_points") or [])
    for index, bullet in enumerate(bullets[:3], start=1):
        top = y + (index - 1) * 76
        draw.rounded_rectangle([margin + 40, top + 5, margin + 78, top + 43], radius=19, fill=(220, 252, 231))
        _draw_centered_text(draw, str(index), margin + 59, top + 9, small_font, fill=(22, 101, 52))
        _draw_wrapped_text(draw, bullet, margin + 98, top, w - margin * 2 - 150, body_font, fill=(30, 41, 59), line_height=40, max_lines=1)
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path, quality=95)
    return output_path


def draw_closed_door_cover(output_path: str, plan: Dict[str, Any], *, include_date: bool) -> str:
    w, h = 1920, 1080
    img = _closed_door_background(w, h)
    draw = ImageDraw.Draw(img)
    shade = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    shade_draw = ImageDraw.Draw(shade)
    for y in range(h):
        alpha = int(58 + 48 * (y / h))
        shade_draw.line([(0, y), (w, y)], fill=(2, 10, 22, alpha))
    img = Image.alpha_composite(img.convert("RGBA"), shade).convert("RGB")
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([8, 8, w - 8, h - 8], radius=16, outline=(255, 255, 255, 110), width=5)

    title_font = _font(184, bold=True)
    sub_font = _font(66, bold=True)
    date_font = _font(38, bold=True)
    disclaimer_font = _font(27, bold=False)
    center_x = w // 2
    y_shift = -62 if not include_date else 0
    _draw_centered_wrapped(
        draw,
        plan.get("xiaojunjun_title") or plan.get("title"),
        center_x,
        258 + y_shift,
        1520,
        title_font,
        fill=(255, 255, 255),
        line_height=190,
        max_lines=2,
        shadow=(0, 0, 0, 160),
    )

    highlight_w = 1254
    highlight_h = 270
    highlight_x = (w - highlight_w) // 2
    highlight_y = 507 + y_shift
    draw.rectangle([highlight_x, highlight_y, highlight_x + highlight_w, highlight_y + highlight_h], fill=(255, 233, 0))
    sub_lines = _wrap_text(draw, _clean_multiline(plan.get("xiaojunjun_subtitle") or plan.get("subtitle")), sub_font, highlight_w - 110, max_lines=3)
    line_h = 84 if len(sub_lines) < 3 else 78
    sub_y = highlight_y + max(36, (highlight_h - len(sub_lines) * line_h) // 2)
    for index, line in enumerate(sub_lines):
        _draw_centered_text(draw, line, center_x, sub_y + index * line_h, sub_font, fill=(7, 20, 35))

    if include_date:
        _draw_date_pill(draw, center_x, 858, plan.get("date_text") or datetime.now().strftime("%Y.%m.%d"), date_font)
    disclaimer = "本内容只供交流学习，不作为任何投资建议。理财有风险，投资需谨慎。"
    _draw_centered_text(draw, disclaimer, center_x, 982, disclaimer_font, fill=(255, 255, 255, 194))

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path, quality=95)
    return output_path


def planner_model() -> str:
    return (
        os.environ.get("MP3_VIDEO_PLANNER_MODEL")
        or os.environ.get("DEEPSEEK_FLASH_MODEL")
        or os.environ.get("DEEPSEEK_MODEL")
        or "deepseek-v4-flash"
    )


def transcript_model() -> str:
    return os.environ.get("MP3_VIDEO_TRANSCRIPT_MODEL") or planner_model()


def extract_closed_door_video_titles(text: str) -> Dict[str, str]:
    # Section-aware extraction is safest for explicit Markdown headings. For
    # legacy one-line responses ("6. ... 7. ..."), retain the date-bounded
    # full-text extractor below so the section 6 value cannot absorb section 7.
    has_markdown_sections = bool(re.search(r"^[ \t]*#{1,6}[ \t]*[1-7][ \t]*[.．、:：|｜]", str(text or ""), flags=re.M))
    sections = _direct_ds_numbered_sections(text) if has_markdown_sections else {}
    slash_title = _direct_ds_closed_door_title(sections.get("6", ""), separator="/")
    pipe_title = _direct_ds_closed_door_title(sections.get("7", ""), separator="|")
    candidates = _closed_door_full_title_candidates(text)
    if not slash_title:
        slash_title = _select_closed_door_video_title(candidates, separator="/")
    if not pipe_title:
        pipe_title = _select_closed_door_video_title(candidates, separator="|")
    return {
        "xiaojunjun_video_title": slash_title,
        "ciketc_video_title": pipe_title,
    }


def _closed_door_full_title_candidates(text: str) -> List[Dict[str, Any]]:
    compact = _clean_line(text)
    if not compact:
        return []
    date_re = r"(?:\d{6}|\d{8}|[12]\d{3}[./-]\d{1,2}[./-]\d{1,2})"
    title_re = re.compile(
        rf"(?P<title>"
        rf"内资闭门会(?:议)?\s*(?P<sep>[/|｜])\s*"
        rf".+?\s*(?P=sep)\s*(?P<date>{date_re})"
        rf")(?=\s*(?:\*\*)?\s*\d+\s*[.、:：)]|$)",
        flags=re.I,
    )
    candidates: List[Dict[str, Any]] = []
    for match in title_re.finditer(compact):
        separator = "|" if match.group("sep") in {"|", "｜"} else "/"
        title = _clean_closed_door_video_title(match.group("title"), expected_separator=separator)
        if not title:
            continue
        before = compact[max(0, match.start() - 120):match.start()]
        candidates.append({
            "title": title,
            "separator": separator,
            "context": before,
            "start": match.start(),
        })
    return candidates


def _select_closed_door_video_title(candidates: List[Dict[str, Any]], *, separator: str) -> str:
    expected = "|" if separator in {"|", "｜"} else "/"
    matches = [item for item in candidates if item.get("separator") == expected and _clean_line(item.get("title"))]
    if not matches:
        return ""

    def score(item: Dict[str, Any]) -> Tuple[int, int]:
        context = _clean_line(item.get("context"))
        current = 0
        if expected == "/" and re.search(r"斜杠|第?\s*6\s*[.、:：)]|^6\s*[.、:：)]", context):
            current += 10
        if expected == "|" and re.search(r"竖线|第?\s*7\s*[.、:：)]|^7\s*[.、:：)]", context):
            current += 10
        if "闭门会格式标题" in context or "标题优化" in context:
            current += 2
        return current, -int(item.get("start") or 0)

    return sorted(matches, key=score, reverse=True)[0]["title"]


def _clean_closed_door_video_title(value: Any, *, expected_separator: str) -> str:
    text = _clean_line(value).strip("* ：:，,。;；")
    if not text:
        return ""
    text = re.sub(r"^[“”\"'《》]+|[“”\"'《》]+$", "", text).strip()
    if _is_cover_process_text(text):
        return ""
    expected = "|" if expected_separator in {"|", "｜"} else "/"
    split_re = r"\s*[|｜]\s*" if expected == "|" else r"\s*/\s*"
    if expected == "|" and "｜" in text:
        text = text.replace("｜", "|")
    if expected not in text:
        return _format_closed_door_video_title(text, separator=expected)
    parts = [_clean_line(part).strip("“”\"'《》") for part in re.split(split_re, text) if _clean_line(part)]
    if len(parts) < 3:
        return ""
    if not re.match(r"^内资闭门会(?:议)?$", parts[0]):
        if "内资闭门会" in parts[0]:
            parts[0] = "内资闭门会"
        else:
            parts.insert(0, "内资闭门会")
    if any(_is_cover_process_text(part) for part in parts):
        return ""
    sep_text = " | " if expected == "|" else " / "
    return sep_text.join(parts[:5])[:180].strip()


def _format_closed_door_video_title(value: Any, *, separator: str, date_text: Any = "") -> str:
    title = _normalize_title_candidate(value)
    if not title or _is_cover_process_text(title):
        return ""
    expected = "|" if separator in {"|", "｜"} else "/"
    raw_parts = [
        _clean_line(part).strip("“”\"'《》")
        for part in re.split(r"\s*[|｜/]\s*", title)
        if _clean_line(part)
    ]
    content_parts = []
    for part in raw_parts:
        if re.match(r"^内资闭门会(?:议)?$", part):
            continue
        if _closed_door_video_date(part):
            continue
        if not _is_cover_process_text(part):
            content_parts.append(_clip_title_line(part, 24))
    if not content_parts:
        content_parts = [_clip_title_line(title, 36)]
    date = _closed_door_video_date(date_text) or datetime.now().strftime("%Y-%m-%d")
    sep_text = " | " if expected == "|" else " / "
    return sep_text.join(["内资闭门会", *content_parts[:3], date])[:180].strip()


def _closed_door_video_date(value: Any) -> str:
    text = _clean_line(value)
    match = re.search(r"([12]\d{3})[./-](\d{1,2})[./-](\d{1,2})", text)
    if match:
        return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
    match = re.search(r"\b(\d{6}|\d{8})\b", text)
    return match.group(1) if match else ""


def extract_title_candidates(text: str) -> List[str]:
    text = str(text or "")
    candidates: List[str] = []
    compact_text = _clean_line(text)
    high_density_inline_re = re.compile(
        r"\d+\.\s*[^“”\"《》]{0,80}?(?:高信息密度|投资人|投资者视角)[^“”\"《》]{0,80}?(?:标题|题目|选题)[^“”\"《》]{0,80}?[“\"《](?P<title>[^”\"》]{4,220})[”\"》]"
    )
    for match in high_density_inline_re.finditer(compact_text):
        candidate = _normalize_title_candidate(match.group("title"))
        if _looks_like_title_candidate(candidate, prefer_long=True):
            candidates.append(candidate)

    pipe_title_re = re.compile(
        r"(?:内资闭门会(?:议)?|内资闭门|投研闭门会|投研会议|内部交流)\s*[|｜]\s*"
        r"(?P<title>.+?)"
        r"\s*[|｜]\s*(?:\d{6}|\d{8}|[12]\d{3}[./-]\d{1,2}[./-]\d{1,2})(?=\s|$)"
    )
    for match in pipe_title_re.finditer(compact_text):
        candidate = _normalize_title_candidate(match.group("title"))
        if _looks_like_title_candidate(candidate, prefer_long=True):
            candidates.append(candidate)

    lines = [_clean_line(line) for line in text.replace("\r", "\n").split("\n")]
    in_investor_title_section = False
    section_stop_re = re.compile(r"^\s*\d+\.\s*(?:精简|音频|优化|核心|金句|片段|摘要|总结|结论)")

    for line in lines:
        clean = line.strip("#-—:： ")
        if not clean:
            continue
        pipe_title = _extract_pipe_title_from_line(clean)
        if pipe_title:
            candidates.append(pipe_title)
        if re.search(r"投资者视角长标题|高信息密度.*标题|投资人标题|长标题\s*[（(]?\s*\d*\s*个版本|长标题", clean):
            in_investor_title_section = True
            inline = _extract_inline_title_after_heading(clean)
            if inline:
                candidate = _normalize_title_candidate(inline)
                if _looks_like_title_candidate(candidate, prefer_long=True):
                    candidates.append(candidate)
            continue
        if in_investor_title_section and section_stop_re.search(clean):
            in_investor_title_section = False

        if in_investor_title_section:
            candidate = _normalize_title_candidate(clean)
            if _looks_like_title_candidate(candidate, prefer_long=True):
                candidates.append(candidate)

    for line in lines:
        clean = line.strip("#-—:： ")
        if not clean:
            continue
        normalized = _normalize_title_candidate(clean)
        pipe_title = _extract_pipe_title_from_line(clean)
        if pipe_title:
            candidates.append(pipe_title)
        if "｜" in clean or "|" in clean:
            if _looks_like_title_candidate(normalized, prefer_long=True):
                candidates.append(normalized)
        if re.search(r"标题|题目|选题|封面", clean):
            clean = _extract_inline_title_after_heading(clean) or re.sub(r"^(?:候选|标准|短|封面)?标题(?:方案)?\s*\d*\s*[:：-]?", "", clean).strip()
            for piece in re.split(r"[。；;]", clean):
                piece = _normalize_title_candidate(piece)
                if _looks_like_title_candidate(piece):
                    candidates.append(piece)
        if _looks_like_title_candidate(normalized):
            candidates.append(normalized)
    for match in re.finditer(r"[“\"《](.{4,80}?)[”\"》]", text):
        candidate = _normalize_title_candidate(match.group(1))
        if _looks_like_title_candidate(candidate):
            candidates.append(candidate)
    seen = set()
    unique = []
    for item in candidates:
        item = _normalize_title_candidate(item)
        if not item or item in seen:
            continue
        seen.add(item)
        unique.append(item)
    pipe_titles = [item for item in unique if "|" in item or "｜" in item]
    if len(pipe_titles) >= 3:
        return pipe_titles[:8]
    return unique[:8]


def _extract_pipe_title_from_line(value: Any) -> str:
    clean = _clean_line(value)
    if _is_cover_process_text(clean):
        return ""
    match = re.search(
        r"(?:内资闭门会(?:议)?|内资闭门|投研闭门会|投研会议|内部交流)\s*[|｜]\s*"
        r"(?P<title>.+?)"
        r"\s*[|｜]\s*(?:\d{6}|\d{8}|[12]\d{3}[./-]\d{1,2}[./-]\d{1,2})(?=\s|$)",
        clean,
    )
    if not match:
        return ""
    candidate = _normalize_title_candidate(match.group("title"))
    return candidate if _looks_like_title_candidate(candidate, prefer_long=True) else ""


def _extract_inline_title_after_heading(value: Any) -> str:
    text = _clean_line(value)
    if not re.search(r"标题|题目|选题|版本", text):
        return ""
    quoted = re.search(r"[“\"《](?P<title>[^”\"》]{4,180})[”\"》]", text)
    if quoted:
        return quoted.group("title")
    cleaned = re.sub(r"^\s*(?:\*\*)?\s*\d+\s*[.、)]\s*", "", text).strip()
    cleaned = re.sub(r"^(?:高信息密度)?(?:投资人|投资者视角)?(?:标题|题目|选题)(?:（[^）]+）|\([^)]*\))?\s*[:：-]?", "", cleaned).strip()
    cleaned = re.sub(r"^(?:版本|方案)\s*[一二三四五六七八九十A-Za-z\d]*\s*[:：-]?", "", cleaned).strip()
    cleaned = cleaned.strip("* ：:")
    return cleaned if cleaned != text else ""


def extract_audio_range(text: str) -> Optional[Tuple[float, float]]:
    normalized = str(text or "").replace("：", ":").replace("–", "-").replace("—", "-")
    token = r"\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?"

    # Explicit start/end fields are authoritative. Check them before generic
    # inline ranges so a later "关键钩子 10:51-11:27" cannot override the
    # recommended clip's "开始时间 05:43 / 结束时间 20:35".
    start_re = re.compile(rf"(?:开始|起始)(?:时间戳|时间)?[*_\s]*:\s*[\[【(（]?\s*(?P<start>{token})")
    end_re = re.compile(rf"(?:结束|截止|终止)(?:时间戳|时间)?[*_\s]*:\s*[\[【(（]?\s*(?P<end>{token})")
    start_match = start_re.search(normalized)
    end_match = end_re.search(normalized)
    if start_match and end_match:
        context_start = max(0, min(start_match.start(), end_match.start()) - 120)
        context_end = min(len(normalized), max(start_match.end(), end_match.end()) + 120)
        context = normalized[context_start:context_end]
        if re.search(r"截取|剪辑|片段|精华|时间戳|时段|时间段|推荐", context):
            start = _parse_seconds(start_match.group("start"), default=None)
            end = _parse_seconds(end_match.group("end"), default=None)
            if start is not None and end is not None and end > start:
                return float(start), float(end)

    range_re = re.compile(
        rf"[\[【(（]?\s*(?P<start>{token})\s*[\]】)）]?\s*"
        rf"(?:-|~|～|至|到)\s*[\[【(（]?\s*(?P<end>{token})\s*[\]】)）]?"
    )
    candidates = []
    for match in range_re.finditer(normalized):
        context = normalized[max(0, match.start() - 80): min(len(normalized), match.end() + 80)]
        if not re.search(r"截取|剪辑|片段|精华|时间戳|时段|时间段|推荐", context):
            continue
        start = _parse_seconds(match.group("start"), default=None)
        end = _parse_seconds(match.group("end"), default=None)
        if start is None or end is None or end <= start:
            continue
        before = normalized[max(0, match.start() - 100):match.start()]
        score = 0
        if re.search(r"推荐片段|建议精华片段|精华片段时间戳|推荐截取|建议截取|推荐时段", before):
            score += 20
        if re.search(r"推荐|精华|片段|截取", context):
            score += 5
        if re.search(r"钩子|金句|内部|位置", before):
            score -= 20
        candidates.append((score, match.start(), float(start), float(end)))
    if candidates:
        _, _, start, end = sorted(candidates, key=lambda item: (-item[0], item[1]))[0]
        return start, end
    return None


def _normalize_title_candidate(value: Any) -> str:
    clean = _clean_line(value)
    clean = clean.strip(" \t\r\n：:，,。；;|｜*_")
    clean = re.sub(r"^\*+\s*", "", clean).strip()
    clean = re.sub(r"^(?:[-*•·●○o]\s*)+", "", clean, flags=re.I).strip()
    clean = re.sub(r"^\d+\s*[.、)]\s*", "", clean).strip()
    clean = re.sub(r"^[(（]\s*\d+\s*[)）]\s*", "", clean).strip()
    clean = re.sub(r"^(?:标题优化|优化标题)\s*\**\s*[:：-]?\s*", "", clean).strip()
    clean = re.sub(
        r"^(?:标题|题目|版本|方案|候选标题|封面标题)\s*[一二三四五六七八九十A-Za-z\d]*\s*[:：-]\s*",
        "",
        clean,
    ).strip()
    clean = re.sub(r"^优化\s*\**\s*(?=(?:内资闭门会|内资闭门|投研闭门会|投研会议|内部交流)\s*[|｜])", "", clean).strip()
    clean = re.sub(r"^(?:内资闭门会(?:议)?|内资闭门|投研闭门会|投研会议|内部交流)\s*[|｜]\s*", "", clean).strip()
    clean = re.sub(r"\s*[|｜]\s*(?:\d{6}|\d{8}|[12]\d{3}[./-]\d{1,2}[./-]\d{1,2})\s*$", "", clean).strip()
    clean = re.sub(r"\s+(?:\d{6}|\d{8})\s*$", "", clean).strip()
    clean = re.sub(r"[“”‘’\"']", "", clean)
    return clean.strip("《》：:，,。；;|｜ ")


def _looks_like_title_candidate(value: Any, *, prefer_long: bool = False) -> bool:
    clean = _clean_line(value)
    if not clean or clean.lower().startswith(("http", "source", "参考")):
        return False
    if _is_cover_process_text(clean):
        return False
    max_length = 180 if prefer_long else 120
    if len(clean) < 4 or len(clean) > max_length:
        return False
    bad_fragments = (
        "投资者视角长标题",
        "精简短标题",
        "优化标题",
        "音频精华片段",
        "这三个标题",
        "兼顾了",
        "好的，很高兴",
        "根据您的要求",
        "推荐截取",
        "时间戳",
        "预估时长",
        "内容概述",
        "核心金句",
        "可以直接用于",
        "输出结果",
        "字段",
        "标题优化",
        "优化**",
    )
    if any(fragment in clean for fragment in bad_fragments):
        return False
    if ("|" not in clean and "｜" not in clean) and len(clean) > (180 if prefer_long else 60):
        return False
    return True


def extract_subtitle(text: str) -> str:
    clean = _clean_line(text)
    pieces = [piece.strip() for piece in re.split(r"[。！？!?；;]", clean) if piece.strip()]
    for piece in pieces:
        if 8 <= len(piece) <= 55 and "标题" not in piece and not _is_cover_process_text(piece):
            return _clip_text(piece, 40)
    return _clip_text(clean, 36)


def _fresh_result(parent_id: str) -> Dict[str, Any]:
    cached = _cached_workflow_result(parent_id)
    if cached is not None:
        return cached
    fresh = _load_task(parent_id, *TASK_RESULT_COLUMNS)
    return dict(fresh.result or {}) if fresh and isinstance(fresh.result, dict) else {}


def _clear_invalid_gemini_shell_result(parent_id: str, result: Dict[str, Any]) -> Dict[str, Any]:
    gemini = result.get("gemini") if isinstance(result.get("gemini"), dict) else {}
    candidates = [
        result.get("initial_response_text"),
        result.get("response_text"),
        result.get("final_response_text"),
        gemini.get("initial_response_text"),
        gemini.get("response_text"),
        gemini.get("final_response_text"),
    ]
    turns = result.get("turns") if isinstance(result.get("turns"), list) else gemini.get("turns")
    if isinstance(turns, list):
        candidates.extend(item.get("response_text") for item in turns if isinstance(item, dict))
    has_shell_text = any(_looks_like_page_shell_text(str(item or "")) for item in candidates)
    if not has_shell_text:
        return result

    fresh = _load_task(parent_id, *TASK_RESULT_COLUMNS)
    if not fresh:
        return result
    current = dict(fresh.result or {})
    for key in ("gemini", "initial_response_text", "response_text", "final_response_text", "turns"):
        current.pop(key, None)
    current["invalid_gemini_response_cleared_at"] = datetime.utcnow().isoformat() + "Z"
    logs = list(current.get("logs") or [])
    logs.append({
        "step": "gemini_shell_response_cleared",
        "message": "清理误识别的 Gemini 页面壳子文本，重新等待真实回复",
        "progress": 8,
        "updated_at": datetime.utcnow().isoformat() + "Z",
    })
    current["logs"] = logs[-160:]
    fresh.result = current
    flag_modified(fresh, "result")
    db.session.commit()
    return current


def _reusable_gemini_result(result: Dict[str, Any], *, fallback_image_path: str) -> Tuple[Optional[Dict[str, Any]], str, Dict[str, Any]]:
    gemini = result.get("gemini") if isinstance(result.get("gemini"), dict) else {}
    first_response = (
        result.get("initial_response_text")
        or gemini.get("initial_response_text")
        or gemini.get("response_text")
        or ""
    )
    if not first_response and _path_ready(gemini.get("output_path")):
        loaded = _load_json_file(gemini.get("output_path"))
        if isinstance(loaded, dict):
            gemini = {**loaded, **gemini}
            first_response = gemini.get("initial_response_text") or gemini.get("response_text") or ""

    infographic = result.get("infographic") if isinstance(result.get("infographic"), dict) else {}
    gemini_image = gemini.get("image") if isinstance(gemini.get("image"), dict) else {}
    image_path = infographic.get("path") or gemini_image.get("path") or fallback_image_path
    if not first_response or not _path_ready(image_path):
        return None, "", {}

    if not infographic:
        infographic = _public_artifact(image_path, "Gemini 横向信息图")
    reusable = {
        **gemini,
        "initial_response_text": first_response,
        "image": gemini_image or infographic,
    }
    return reusable, first_response, infographic


def _reusable_text_source_result(result: Dict[str, Any], *, fallback_image_path: str) -> Tuple[Optional[Dict[str, Any]], str, Dict[str, Any]]:
    source = result.get("source") if isinstance(result.get("source"), dict) else {}
    first_response = (
        result.get("initial_response_text")
        or result.get("source_response_text")
        or source.get("initial_response_text")
        or source.get("response_text")
        or ""
    )
    if not first_response and _path_ready(source.get("output_path")):
        loaded = _load_json_file(source.get("output_path"))
        if isinstance(loaded, dict):
            source = {**loaded, **source}
            first_response = source.get("initial_response_text") or source.get("response_text") or ""

    infographic = result.get("infographic") if isinstance(result.get("infographic"), dict) else {}
    source_image = source.get("image") if isinstance(source.get("image"), dict) else {}
    image_path = infographic.get("path") or source_image.get("path") or fallback_image_path
    if not first_response:
        return None, "", {}

    if not infographic:
        infographic = {"path": image_path, "label": "DS 文本信息图"}
    reusable = {
        **source,
        "initial_response_text": first_response,
        "response_text": source.get("response_text") or first_response,
        "image": source_image or infographic,
    }
    return reusable, first_response, infographic


def _keyframe_artifacts_ready(keyframes: Any) -> bool:
    if not isinstance(keyframes, dict) or len(keyframes) < 2:
        return False
    for artifact in keyframes.values():
        if not isinstance(artifact, dict) or artifact.get("template_version") != KEYFRAME_ARTIFACT_VERSION:
            return False
        if not _path_ready(artifact.get("path")):
            return False
    return True


def _reusable_plan(result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    plan = result.get("plan") if isinstance(result.get("plan"), dict) else {}
    plan_audio = plan.get("audio") if isinstance(plan.get("audio"), dict) else {}
    if _clean_line(plan.get("title")) and plan_audio and plan.get("planner_version") == PLANNER_VERSION:
        return plan
    return None


def _without_waiting_state(result: Dict[str, Any]) -> Dict[str, Any]:
    cleaned = dict(result or {})
    cleaned.pop("waiting_child", None)
    cleaned.pop("deferred_waits", None)
    return cleaned


def _visual_artifacts_ready(artifacts: Any) -> bool:
    if not isinstance(artifacts, dict):
        return False
    for key in VISUAL_ARTIFACT_KEYS:
        artifact = artifacts.get(key)
        if not isinstance(artifact, dict):
            return False
        if artifact.get("template_version") != VISUAL_ARTIFACT_VERSION:
            return False
        if not _image_file_ready(artifact.get("path"), expected_size=VISUAL_ARTIFACT_EXPECTED_SIZES.get(key)):
            return False
    return True


def _reusable_video_summary(
    result: Dict[str, Any],
    label: str,
    *,
    parent_id: Optional[str] = None,
    generate_subtitles: Optional[bool] = None,
    audio_signature: Optional[str] = None,
    created_not_before: Optional[datetime] = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[PublicationTask]]:
    summary = result.get(label) if isinstance(result.get(label), dict) else {}
    if (
        created_not_before is None
        and _video_summary_ready(summary)
        and _summary_matches_generate_subtitles(summary, generate_subtitles)
        and _summary_matches_audio_signature(summary, audio_signature)
    ):
        return summary, None

    child = _completed_child_from_result(result, label)
    if (
        child
        and _task_created_not_before(child, created_not_before)
        and _publisher_child_matches_generate_subtitles(child, generate_subtitles)
        and _publisher_child_matches_audio_signature(child, audio_signature)
    ):
        child_summary = _publisher_result_summary(child)
        if _video_summary_ready(child_summary):
            return child_summary, child
    existing = _find_existing_publisher_video_task_for_label(
        result,
        label,
        parent_id=parent_id,
        generate_subtitles=generate_subtitles,
        audio_signature=audio_signature,
        created_not_before=created_not_before,
    )
    if existing and existing.status == "completed":
        existing_summary = _publisher_result_summary(existing)
        if _video_summary_ready(existing_summary):
            return existing_summary, existing
    return None, None


def _find_existing_publisher_video_task_for_label(
    result: Dict[str, Any],
    label: str,
    *,
    parent_id: Optional[str] = None,
    generate_subtitles: Optional[bool] = None,
    audio_signature: Optional[str] = None,
    created_not_before: Optional[datetime] = None,
) -> Optional[PublicationTask]:
    resolved_parent_id = str(parent_id or (result or {}).get("workflow_task_id") or "").strip()
    if not resolved_parent_id:
        return None
    spec = _publisher_video_spec_for_label(label)
    if not spec:
        return None
    kwargs = {"role": spec["role"], "aspect_ratio": spec["aspect_ratio"]}
    if generate_subtitles is not None:
        kwargs["generate_subtitles"] = generate_subtitles
    if audio_signature:
        kwargs["audio_signature"] = audio_signature
    if created_not_before is not None:
        kwargs["created_not_before"] = created_not_before
    return _find_existing_publisher_video_task(resolved_parent_id, **kwargs)


def _publisher_video_spec_for_label(label: str) -> Optional[Dict[str, str]]:
    if label == "landscape_video":
        return {"role": "xiaojunjun_landscape", "aspect_ratio": "16:9"}
    if label == "vertical_video":
        return {"role": "ciketc_vertical", "aspect_ratio": "9:16"}
    return None


def _active_child_from_result(
    result: Dict[str, Any],
    label: str,
    *,
    generate_subtitles: Optional[bool] = None,
    audio_signature: Optional[str] = None,
    created_not_before: Optional[datetime] = None,
) -> Optional[PublicationTask]:
    child = _child_from_result(result, label)
    if (
        child
        and child.status not in TERMINAL_STATUSES
        and _task_created_not_before(child, created_not_before)
        and _publisher_child_matches_generate_subtitles(child, generate_subtitles)
        and _publisher_child_matches_audio_signature(child, audio_signature)
    ):
        return child
    return None


def _task_created_not_before(
    task: Optional[PublicationTask],
    cutoff: Optional[datetime],
) -> bool:
    if cutoff is None:
        return True
    created_at = getattr(task, "created_at", None)
    return bool(created_at and created_at >= cutoff)


def _child_from_result(result: Dict[str, Any], label: str) -> Optional[PublicationTask]:
    child_id = _child_id_from_result(result, label)
    if not child_id:
        return None
    return _load_task(child_id, *TASK_PUBLIC_COLUMNS)


def _child_id_from_result(result: Dict[str, Any], label: str) -> Optional[str]:
    children = result.get("children") if isinstance(result.get("children"), dict) else {}
    child_summary = children.get(label) if isinstance(children.get(label), dict) else {}
    direct_summary = result.get(label) if isinstance(result.get(label), dict) else {}
    child_id = child_summary.get("job_id") or direct_summary.get("job_id")
    return str(child_id).strip() if child_id else None


def _completed_child_from_result(result: Dict[str, Any], label: str) -> Optional[PublicationTask]:
    child = _child_from_result(result, label)
    if child and child.status == "completed":
        return child
    return None


def _video_summary_ready(summary: Any) -> bool:
    if not isinstance(summary, dict) or not summary:
        return False
    status = str(summary.get("status") or "").lower()
    if status and status not in {"completed", "succeeded"}:
        return False
    return _video_file_ready(_video_output_message(summary))


def _summary_matches_generate_subtitles(summary: Any, expected: Optional[bool]) -> bool:
    if expected is None or not isinstance(summary, dict):
        return True
    if "generate_subtitles" not in summary:
        return bool(expected) is True
    return _truthy(summary.get("generate_subtitles"), default=True) == bool(expected)


def _summary_matches_audio_signature(summary: Any, expected: Optional[str]) -> bool:
    if not expected or not isinstance(summary, dict):
        return True
    return str(summary.get("audio_signature") or "").strip() == expected


def _path_ready(path: Any) -> bool:
    text = str(path or "").strip()
    resolved = _resolve_project_path(text) if text else None
    return bool(resolved and resolved.exists() and resolved.is_file() and resolved.stat().st_size > 0)


def _video_file_ready(path: Any) -> bool:
    """Return True only for a finalized, probeable video with positive duration."""
    text_value = str(path or "").strip()
    resolved = _resolve_project_path(text_value) if text_value else None
    if not resolved or not resolved.exists() or not resolved.is_file():
        return False
    try:
        stat = resolved.stat()
    except OSError:
        return False
    if stat.st_size <= 0:
        return False
    return _probe_video_file_cached(str(resolved), int(stat.st_size), int(stat.st_mtime_ns))


@lru_cache(maxsize=512)
def _probe_video_file_cached(path: str, _size: int, _mtime_ns: int) -> bool:
    try:
        probe = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=codec_type,width,height:format=duration",
                "-of",
                "json",
                path,
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        )
        payload = json.loads(probe.stdout or "{}")
        streams = payload.get("streams") if isinstance(payload, dict) else []
        duration = float(((payload.get("format") or {}).get("duration")) or 0)
        return duration > 0.1 and any(
            isinstance(stream, dict)
            and stream.get("codec_type") == "video"
            and int(stream.get("width") or 0) > 0
            and int(stream.get("height") or 0) > 0
            for stream in (streams or [])
        )
    except Exception as exc:
        logger.warning("Reject invalid/incomplete video file: path=%s error=%s", path, exc)
        return False


def _image_file_ready(path: Any, *, expected_size: Optional[Tuple[int, int]] = None) -> bool:
    text = str(path or "").strip()
    resolved = _resolve_project_path(text) if text else None
    if not resolved or not resolved.exists() or not resolved.is_file() or resolved.stat().st_size <= 0:
        return False
    try:
        with Image.open(resolved) as img:
            size = img.size
            img.verify()
    except Exception:
        return False
    if expected_size and size != expected_size:
        return False
    return True


def _load_json_file(path: Any) -> Optional[Dict[str, Any]]:
    try:
        with open(str(path), "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return payload if isinstance(payload, dict) else None
    except Exception:
        return None


def _recover_completed_publisher_child_from_disk(
    child: PublicationTask,
    *,
    video_path: Optional[Path] = None,
) -> PublicationTask:
    if not child or child.feature != "publisher" or child.status in TERMINAL_STATUSES:
        return child
    video_path = video_path or _publisher_child_disk_video_path(child)
    if not video_path:
        return child

    child_id = str(child.id)
    now = datetime.utcnow()
    result_patch = {
        "video_path": str(video_path),
        "completed_at": now.isoformat() + "Z",
        "disk_recovered_at": now.isoformat() + "Z",
        "generate_subtitles": _publisher_child_generate_subtitles(child, default=True),
    }
    aspect_ratio = _publisher_child_aspect_ratio(child)
    if aspect_ratio == "9:16":
        result_patch["vertical_video_path"] = str(video_path)
    else:
        result_patch["landscape_video_path"] = str(video_path)

    bind = db.session.get_bind()
    if bind is not None and bind.dialect.name == "postgresql":
        def persist_recovery():
            updated = db.session.execute(
                text("""
                    UPDATE public.publication_tasks
                    SET status = 'completed',
                        current_step = 'done',
                        progress = 100,
                        error_message = NULL,
                        completed_at = :completed_at,
                        task_video_path = :video_path,
                        result = COALESCE(result, '{}'::jsonb) || CAST(:result_patch AS jsonb),
                        updated_at = :completed_at
                    WHERE id = :task_id
                      AND status NOT IN ('completed', 'failed', 'canceled')
                """),
                {
                    "task_id": child_id,
                    "completed_at": now,
                    "video_path": str(video_path),
                    "result_patch": json.dumps(result_patch, ensure_ascii=False),
                },
            )
            db.session.commit()
            return int(updated.rowcount or 0)

        if not _db_retry(persist_recovery, f"recover publisher child from disk {child_id}"):
            return child
        recovered = _load_task(child_id, *TASK_PUBLIC_STATUS_COLUMNS) or child
        logger.warning(
            "Recovered completed publisher child from existing video file: child=%s path=%s",
            child_id,
            video_path,
        )
        return recovered

    result = dict(child.result or {})
    result.update(result_patch)

    child.status = "completed"
    child.current_step = "done"
    child.progress = 100
    child.error_message = None
    child.completed_at = now
    child.task_video_path = str(video_path)
    child.result = result
    flag_modified(child, "result")
    db.session.flush()
    db.session.expunge(child)
    db.session.commit()
    logger.warning(
        "Recovered completed publisher child from existing video file: child=%s path=%s",
        child_id,
        video_path,
    )
    return child


def _publisher_child_wait_snapshot(child: PublicationTask) -> Any:
    payload = {
        column.key: getattr(child, column.key, None)
        for column in TASK_PUBLIC_STATUS_COLUMNS
    }
    payload["params"] = {}
    payload["result"] = {}
    return SimpleNamespace(**payload)


def _publisher_child_disk_video_path(child: PublicationTask) -> Optional[Path]:
    task_id = str(getattr(child, "id", "") or "")
    if not task_id:
        return None
    processed_dir = project_root() / "data" / "processed"
    if not processed_dir.exists():
        return None
    short_id = task_id[:6]
    try:
        candidates = [
            path
            for path in processed_dir.glob(f"*{short_id}.mp4")
            if path.is_file() and _publisher_video_file_is_stable(path)
        ]
    except Exception:
        logger.exception("Failed to inspect publisher disk video candidates: child=%s", task_id)
        return None
    if not candidates:
        return None
    aspect_ratio = _publisher_child_aspect_ratio(child)
    if aspect_ratio == "9:16":
        candidates.sort(key=lambda path: (not path.name.startswith("v_"), -path.stat().st_mtime))
    else:
        candidates.sort(key=lambda path: (path.name.startswith("v_"), -path.stat().st_mtime))
    return candidates[0]


def _publisher_video_file_is_stable(path: Path) -> bool:
    try:
        stat = path.stat()
    except OSError:
        return False
    min_bytes = max(1024 * 1024, _int_env("MP3_VIDEO_PUBLISHER_RECOVERY_MIN_BYTES", 1024 * 1024))
    if stat.st_size < min_bytes:
        return False
    grace_seconds = max(0, _int_env("MP3_VIDEO_PUBLISHER_DISK_RECOVERY_GRACE_SECONDS", 20))
    if (time.time() - stat.st_mtime) < grace_seconds:
        return False
    return _video_file_ready(path)


def _wait_child(parent_id: str, child_id: str, label: str, *, start_progress: int, end_progress: int) -> PublicationTask:
    timeout = _child_inline_wait_seconds(label)
    poll_seconds = max(1, _int_env("MP3_VIDEO_CHILD_POLL_SECONDS", 3))
    recovery_check_seconds = max(10, _int_env("MP3_VIDEO_CHILD_FULL_RECOVERY_CHECK_SECONDS", 30))
    deadline = time.monotonic() + timeout
    next_full_recovery_check = 0.0
    last_step = None
    while time.monotonic() < deadline:
        parent = _load_task(parent_id, *TASK_STATUS_COLUMNS)
        if not parent or parent.status == "canceled":
            raise ParentTaskCanceled("父编排任务已取消")
        child = _load_task(child_id, *TASK_PUBLIC_STATUS_COLUMNS)
        if not child:
            raise RuntimeError(f"子任务不存在: {child_id}")

        now_mono = time.monotonic()
        requeued = False
        if child.status not in TERMINAL_STATUSES and now_mono >= next_full_recovery_check:
            child = _publisher_child_wait_snapshot(child)
            repair_due = _publisher_child_repair_due(child)
            release_db_session_quietly()
            disk_video = _publisher_child_disk_video_path(child)
            if disk_video or repair_due:
                if disk_video:
                    recovery_child = _load_task(child_id, *TASK_PUBLIC_STATUS_COLUMNS)
                    if recovery_child:
                        child = _recover_completed_publisher_child_from_disk(
                            recovery_child,
                            video_path=disk_video,
                        )
                if child and child.status not in TERMINAL_STATUSES and repair_due:
                    full_child = _load_task(child_id, *TASK_PUBLIC_COLUMNS)
                    if full_child:
                        full_child, requeued = maybe_requeue_stale_publisher_child(full_child, log=logger)
                        child = full_child or child
                    if requeued:
                        child = _load_task(child_id, *TASK_PUBLIC_STATUS_COLUMNS) or child
            next_full_recovery_check = now_mono + recovery_check_seconds

        child = _publisher_child_wait_snapshot(child)
        child_status = str(child.status or "")
        child_step = str(child.current_step or "")
        child_progress = int(child.progress or 0)
        child_summary = _task_public_summary(child)
        release_db_session_quietly()
        celery_failure = None if child_status in TERMINAL_STATUSES else _celery_failure_message(child)

        if child_step != last_step or requeued:
            last_step = None if requeued else child_step
            mapped = start_progress + int((end_progress - start_progress) * child_progress / 100)
            suffix = "requeued" if requeued else child_step
            message = f"{label}: requeued" if requeued else f"{label}: {child_step}"
            _emit(parent_id, f"{label}_{suffix}", mapped, message)
            _record_child(parent_id, label, child_summary)
        if child_status in TERMINAL_STATUSES:
            _clear_waiting_child(parent_id, label)
            return _load_task(child_id, *TASK_PUBLIC_COLUMNS) or child
        if celery_failure:
            full_child = _load_task(child_id, *TASK_PUBLIC_COLUMNS) or child
            _mark_child_failed(full_child, celery_failure)
            db.session.expire_all()
            _clear_waiting_child(parent_id, label)
            return _load_task(child_id, *TASK_PUBLIC_COLUMNS)
        release_db_session_quietly()
        time.sleep(poll_seconds)

    child = _load_task(child_id, *TASK_PUBLIC_STATUS_COLUMNS)
    if not child:
        raise RuntimeError(f"子任务不存在: {child_id}")
    child_status = str(child.status or "")
    child_step = str(child.current_step or "")
    child_key = str(child.id)
    child_summary = _task_public_summary(child)
    # The child can reach a terminal state between the final loop poll and this
    # deadline read. Treat that as a normal terminal result, not as a timeout.
    if child_status in TERMINAL_STATUSES:
        _clear_waiting_child(parent_id, label)
        return _load_task(child_id, *TASK_PUBLIC_COLUMNS) or child
    _record_child(parent_id, label, child_summary)
    raise ChildTaskStillRunning(label, child_key, child_status, child_step)


def _publisher_child_repair_due(child: PublicationTask) -> bool:
    if not child or child.feature != "publisher" or str(child.status or "").lower() != "pending":
        return False
    reference_time = child.updated_at or child.created_at
    if not reference_time:
        return True
    grace_seconds = max(30, _int_env("PUBLISHER_CHILD_ORPHAN_GRACE_SECONDS", 180))
    return (datetime.utcnow() - reference_time).total_seconds() >= grace_seconds


def _raise_if_parent_canceled(parent_id: str) -> None:
    parent = _load_task(parent_id, *TASK_STATUS_COLUMNS)
    if not parent or parent.status == "canceled":
        raise ParentTaskCanceled("父编排任务已取消")


def _defer_parent_retry(parent_id: str, exc: ChildTaskStillRunning) -> None:
    delay = _parent_retry_delay_seconds(exc.label)
    max_defers = _parent_max_defers(exc.label)
    cached_result = _cached_workflow_result(parent_id)
    if cached_result is None:
        fresh = _load_task(
            parent_id,
            *TASK_PROGRESS_RESULT_COLUMNS,
            PublicationTask.status,
            PublicationTask.error_message,
        )
        result = dict(fresh.result or {}) if fresh and isinstance(fresh.result, dict) else {}
    else:
        fresh = _load_task(parent_id, *TASK_PROGRESS_STATUS_COLUMNS)
        result = cached_result
    if not fresh or fresh.status == "canceled":
        return

    deferred_waits = dict(result.get("deferred_waits") or {})
    state = dict(deferred_waits.get(exc.label) or {})
    count = int(state.get("defer_count") or state.get("count") or 0) + 1
    if count > max_defers:
        raise RuntimeError(f"等待子任务超过最大续跑次数: {exc.label} {exc.child_id}")

    waiting_child = {
        "label": exc.label,
        "child_id": exc.child_id,
        "status": exc.status,
        "step": exc.step,
        "retry_delay_seconds": delay,
        "inline_wait_seconds": _child_inline_wait_seconds(exc.label),
        "defer_count": count,
        "max_defers": max_defers,
        "updated_at": datetime.utcnow().isoformat() + "Z",
    }
    deferred_waits[exc.label] = waiting_child
    result["waiting_child"] = waiting_child
    result["deferred_waits"] = deferred_waits
    fresh.status = "processing"
    fresh.current_step = _safe_step_code(f"{exc.label}_waiting")
    fresh.progress = max(int(fresh.progress or 0), 80 if exc.label == "landscape_video" else 92)
    fresh.error_message = None
    fresh.result = result
    flag_modified(fresh, "result")
    db.session.commit()
    _replace_cached_workflow_result(parent_id, result)

    celery_app.send_task(
        "tasks.mp3_video_workflow.process_job",
        args=[parent_id, waiting_child],
        queue=QUEUE_NAME,
        countdown=delay,
    )


def _is_publisher_video_label(label: str) -> bool:
    return str(label or "").strip() in {"landscape_video", "vertical_video"}


def _child_inline_wait_seconds(label: str) -> int:
    if _is_publisher_video_label(label):
        return max(1, _int_env("MP3_VIDEO_PUBLISHER_INLINE_WAIT_SECONDS", DEFAULT_PUBLISHER_INLINE_WAIT_SECONDS))
    return max(1, _int_env("MP3_VIDEO_CHILD_TIMEOUT_SECONDS", DEFAULT_CHILD_TIMEOUT_SECONDS))


def _parent_retry_delay_seconds(label: str) -> int:
    if _is_publisher_video_label(label):
        return max(
            10,
            _int_env(
                "MP3_VIDEO_PUBLISHER_PARENT_RETRY_DELAY_SECONDS",
                DEFAULT_PUBLISHER_PARENT_RETRY_DELAY_SECONDS,
            ),
        )
    return max(30, _int_env("MP3_VIDEO_PARENT_RETRY_DELAY_SECONDS", DEFAULT_PARENT_RETRY_DELAY_SECONDS))


def _parent_max_defers(label: str) -> int:
    if _is_publisher_video_label(label):
        return max(1, _int_env("MP3_VIDEO_PUBLISHER_PARENT_MAX_DEFERS", DEFAULT_PUBLISHER_PARENT_MAX_DEFERS))
    return max(1, _int_env("MP3_VIDEO_PARENT_MAX_DEFERS", DEFAULT_PARENT_MAX_DEFERS))


def _clear_waiting_child(parent_id: str, label: str) -> None:
    cached_result = _cached_workflow_result(parent_id)
    if cached_result is None:
        fresh = _load_task(parent_id, *TASK_PROGRESS_RESULT_COLUMNS)
        result = dict(fresh.result or {}) if fresh and isinstance(fresh.result, dict) else {}
    else:
        fresh = _load_task(parent_id, *TASK_PROGRESS_STATUS_COLUMNS)
        result = cached_result
    if not fresh or not result:
        return
    waiting_child = result.get("waiting_child")
    if not isinstance(waiting_child, dict) or waiting_child.get("label") != label:
        return
    result.pop("waiting_child", None)
    fresh.result = result
    flag_modified(fresh, "result")
    db.session.commit()
    _replace_cached_workflow_result(parent_id, result)


def _start_task(task: PublicationTask) -> None:
    task_id = str(task.id)
    result = dict(task.result or {})
    started_at = datetime.utcnow().isoformat() + "Z"
    resume_waiting_child = isinstance(result.get("waiting_child"), dict)
    notifications = result.get("notifications") if isinstance(result.get("notifications"), dict) else {}
    if notifications and not resume_waiting_child:
        history = list(result.get("notification_history") or [])
        history.append({
            "archived_at": started_at,
            "reason": "new_attempt",
            "notifications": notifications,
        })
        result["notification_history"] = history[-8:]
    if not resume_waiting_child:
        result["notifications"] = {}
    result.pop("completed_at", None)
    if not resume_waiting_child:
        result.pop("resumed_at", None)
    result.update({
        "started_at": (result.get("started_at") or started_at) if resume_waiting_child else started_at,
        "error": None,
        "logs": result.get("logs") or [],
        "children": result.get("children") or {},
    })
    if resume_waiting_child:
        result["resumed_at"] = started_at
    task.status = "processing"
    task.current_step = task.current_step if resume_waiting_child and task.current_step else "start"
    task.progress = max(int(task.progress or 0), 2) if resume_waiting_child else 2
    task.error_message = None
    task.completed_at = None
    task.result = result
    flag_modified(task, "result")
    db.session.commit()
    _replace_cached_workflow_result(task_id, result)


def _emit(parent_id: str, step: str, progress: int, message: str, *, result: Optional[Dict[str, Any]] = None) -> None:
    log_entry = {
        "step": step,
        "message": message,
        "progress": progress,
        "updated_at": datetime.utcnow().isoformat() + "Z",
    }
    patch = result if isinstance(result, dict) else {}

    def operation():
        updated = db.session.execute(
            text("""
                UPDATE public.publication_tasks
                SET current_step = :step,
                    progress = :progress,
                    updated_at = NOW()
                WHERE id = :task_id
                  AND status IS DISTINCT FROM 'canceled'
            """),
            {
                "task_id": str(parent_id),
                "step": _safe_step_code(step),
                "progress": max(0, min(100, int(progress or 0))),
            },
        )
        db.session.commit()
        return updated.rowcount

    if _db_retry(operation, f"emit workflow progress {parent_id}"):
        cached = _cached_workflow_result(parent_id)
        if cached is not None:
            logs = list(cached.get("logs") or [])
            logs.append(log_entry)
            cached["logs"] = logs[-160:]
            cached.update(copy.deepcopy(patch))
            state = _active_workflow_state.get()
            state["result"] = cached


def _update_result(parent_id: str, values: Dict[str, Any]) -> None:
    if not isinstance(values, dict) or not values:
        return

    def operation():
        updated = db.session.execute(
            text("""
                UPDATE public.publication_tasks
                SET result = COALESCE(result, '{}'::jsonb) || CAST(:patch AS jsonb),
                    updated_at = NOW()
                WHERE id = :task_id
                  AND status IS DISTINCT FROM 'canceled'
            """),
            {
                "task_id": str(parent_id),
                "patch": json.dumps(values, ensure_ascii=False),
            },
        )
        db.session.commit()
        return updated.rowcount

    if _db_retry(operation, f"update workflow result {parent_id}"):
        _merge_cached_workflow_result(parent_id, values)


def _record_child(parent_id: str, label: str, child: Any) -> None:
    child_summary = copy.deepcopy(child) if isinstance(child, dict) else _task_public_summary(child)

    def operation():
        updated = db.session.execute(
            text("""
                UPDATE public.publication_tasks
                SET result = jsonb_set(
                        COALESCE(result, '{}'::jsonb),
                        '{children}',
                        COALESCE(result->'children', '{}'::jsonb)
                            || jsonb_build_object(CAST(:label AS text), CAST(:child AS jsonb)),
                        true
                    ),
                    updated_at = NOW()
                WHERE id = :task_id
                  AND status IS DISTINCT FROM 'canceled'
            """),
            {
                "task_id": str(parent_id),
                "label": str(label),
                "child": json.dumps(child_summary, ensure_ascii=False),
            },
        )
        db.session.commit()
        return updated.rowcount

    if _db_retry(operation, f"record workflow child {parent_id}:{label}"):
        cached = _cached_workflow_result(parent_id)
        if cached is not None:
            children = dict(cached.get("children") or {})
            children[label] = copy.deepcopy(child_summary)
            _merge_cached_workflow_result(parent_id, {"children": children})


def _mark_failed(task: PublicationTask, message: str) -> None:
    result = _cached_workflow_result(task.id)
    if result is None:
        result = dict(task.result or {})
    result["error"] = message
    result["completed_at"] = datetime.utcnow().isoformat() + "Z"
    task.status = "failed"
    task.current_step = "failed"
    task.progress = task.progress or 0
    task.error_message = message
    task.completed_at = datetime.utcnow()
    task.result = result
    flag_modified(task, "result")
    db.session.commit()
    _replace_cached_workflow_result(task.id, result)


def mark_job_failed_safely(task_id: str, message: str, *, notify: bool = True) -> bool:
    try:
        db.session.rollback()
    except Exception:
        pass

    persisted = False
    persist_error = None
    try:
        fresh = _load_task(task_id, *TASK_FAIL_COLUMNS)
        if not fresh or fresh.status == "canceled":
            if notify and not fresh:
                _send_direct_mp3_workflow_failure_email(
                    task_id=task_id,
                    message=message,
                    persisted=False,
                    notification_queued=False,
                    persist_error=f"任务不存在: {task_id}",
                )
            return False
        _mark_failed(fresh, message)
        persisted = True
    except Exception as exc:
        persist_error = str(exc)
        try:
            db.session.rollback()
        except Exception:
            pass
        logger.exception("[MP3_VIDEO_WORKFLOW] failed to persist failure state | task=%s", task_id)
        if notify:
            _send_direct_mp3_workflow_failure_email(
                task_id=task_id,
                message=message,
                persisted=False,
                notification_queued=False,
                persist_error=persist_error,
            )
        return False

    notification_queued = False
    if notify:
        notification_queued = _queue_notification(task_id, "mp3_video_failed", message)
        if not notification_queued:
            _send_direct_mp3_workflow_failure_email(
                task_id=task_id,
                message=message,
                persisted=persisted,
                notification_queued=notification_queued,
                persist_error=persist_error,
            )
    return True


def mark_job_failed_if_unfinished(task_id: str, message: str, *, notify: bool = True) -> bool:
    try:
        db.session.rollback()
    except Exception:
        pass
    try:
        fresh = _load_task(task_id, *TASK_STATUS_COLUMNS)
        if not fresh or fresh.status in TERMINAL_STATUSES:
            return False
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass
        logger.exception("[MP3_VIDEO_WORKFLOW] failed to inspect unfinished task | task=%s", task_id)
        return False
    return mark_job_failed_safely(task_id, message, notify=notify)


def _mark_child_failed(child: PublicationTask, message: str) -> None:
    result = dict(child.result or {})
    result["error"] = message
    result["completed_at"] = datetime.utcnow().isoformat() + "Z"
    child.status = "failed"
    child.current_step = "failed"
    child.error_message = message
    child.completed_at = datetime.utcnow()
    child.result = result
    flag_modified(child, "result")
    db.session.commit()


def _queue_notification(task_id: str, event_type: str, message: Optional[str] = None) -> bool:
    try:
        params_snapshot = _cached_workflow_params(task_id)
        result_snapshot = _cached_workflow_result(task_id)
        if params_snapshot is None or result_snapshot is None:
            fresh = _load_task(task_id, *TASK_NOTIFICATION_COLUMNS)
            params_snapshot = dict(fresh.params or {}) if fresh and isinstance(fresh.params, dict) else {}
            result_snapshot = dict(fresh.result or {}) if fresh and isinstance(fresh.result, dict) else {}
        else:
            fresh = _load_task(task_id, *TASK_NOTIFICATION_STATUS_COLUMNS)
        if not fresh or fresh.status == "canceled":
            return False
        notifications = dict(result_snapshot.get("notifications") or {})
        if notifications.get(event_type, {}).get("queued"):
            return True

        payload = _notification_payload(
            fresh,
            event_type,
            message,
            params_snapshot=params_snapshot,
            result_snapshot=result_snapshot,
        )
        queued = workflow_notification_service.enqueue_workflow_event(event_type, payload)
        if not queued.get("queued"):
            return False

        notification = {
            "queued": True,
            "event_type": event_type,
            "celery_task_id": queued.get("celery_task_id"),
            "queued_at": datetime.utcnow().isoformat() + "Z",
        }
        notifications[event_type] = notification

        def persist_notification():
            updated = db.session.execute(
                text("""
                    UPDATE public.publication_tasks
                    SET result = jsonb_set(
                            COALESCE(result, '{}'::jsonb),
                            '{notifications}',
                            COALESCE(result->'notifications', '{}'::jsonb)
                                || jsonb_build_object(
                                    CAST(:event_type AS text),
                                    CAST(:notification AS jsonb)
                                ),
                            true
                        ),
                        updated_at = NOW()
                    WHERE id = :task_id
                      AND status IS DISTINCT FROM 'canceled'
                """),
                {
                    "task_id": str(task_id),
                    "event_type": str(event_type),
                    "notification": json.dumps(notification, ensure_ascii=False),
                },
            )
            db.session.commit()
            return updated.rowcount

        if not _db_retry(persist_notification, f"persist notification {task_id}:{event_type}"):
            return False
        result_snapshot["notifications"] = notifications
        _replace_cached_workflow_result(task_id, result_snapshot)
        return True
    except Exception:
        db.session.rollback()
        logger.exception("[MP3_VIDEO_WORKFLOW] notification queue failed | task=%s event=%s", task_id, event_type)
        return False


def _send_direct_mp3_workflow_failure_email(
    *,
    task_id: str,
    message: str,
    persisted: bool = False,
    notification_queued: bool = False,
    persist_error: Optional[str] = None,
) -> None:
    try:
        subject = f"[MP3 视频编排] 任务异常 · {str(task_id)[:8]}"
        lines = [
            "MP3 视频编排任务异常退出，已触发兜底通知。",
            "",
            f"任务 ID：{task_id}",
            f"失败已写入数据库：{'是' if persisted else '否'}",
            f"失败通知已入队：{'是' if notification_queued else '否'}",
            f"异常：{message}",
        ]
        if persist_error:
            lines.append(f"落库/通知异常：{persist_error}")
        lines.extend([
            "",
            f"发送时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        ])
        workflow_notification_service.send_email_message(subject, "\n".join(lines))
    except Exception:
        logger.exception("[MP3_VIDEO_WORKFLOW] direct failure email failed | task=%s", task_id)


def _notification_payload(
    task: PublicationTask,
    event_type: str,
    message: Optional[str] = None,
    *,
    params_snapshot: Optional[Dict[str, Any]] = None,
    result_snapshot: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    result = result_snapshot if isinstance(result_snapshot, dict) else (
        task.result if isinstance(task.result, dict) else {}
    )
    params = params_snapshot if isinstance(params_snapshot, dict) else (
        task.params if isinstance(task.params, dict) else {}
    )
    workflow = params.get(FEATURE_NAME) if isinstance(params.get(FEATURE_NAME), dict) else {}
    metadata = workflow.get("metadata") if isinstance(workflow.get("metadata"), dict) else {}
    audio = result.get("audio") or workflow.get("audio") or {}
    plan = result.get("plan") or {}
    artifacts = result.get("artifacts") if isinstance(result.get("artifacts"), dict) else {}
    children = result.get("children") if isinstance(result.get("children"), dict) else {}
    gemini = result.get("gemini") if isinstance(result.get("gemini"), dict) else {}
    source = result.get("source") if isinstance(result.get("source"), dict) else {}
    first_stage = source or gemini
    infographic = result.get("infographic") if isinstance(result.get("infographic"), dict) else {}
    landscape = result.get("landscape_video") or children.get("landscape_video") or {}
    vertical = result.get("vertical_video") or children.get("vertical_video") or {}

    return {
        "id": task.id,
        "batch_id": task.id,
        "group_id": FEATURE_NAME,
        "group_name": "MP3 视频编排",
        "group_label": "编排",
        "subject_prefix": "[MP3 视频编排]",
        "trigger": metadata.get("source") or "manual",
        "status": _notification_status(task.status),
        "current_stage": task.current_step,
        "event_type": event_type,
        "event_message": message,
        "error_message": task.error_message or result.get("error"),
        "created_at": task.created_at.isoformat() + "Z" if task.created_at else None,
        "started_at": result.get("started_at"),
        "completed_at": task.completed_at.isoformat() + "Z" if task.completed_at else result.get("completed_at"),
        "stage_results": {
            "stages": _notification_stages(task, audio, first_stage, infographic, plan, artifacts, landscape, vertical),
            "outputs": _notification_outputs(result, audio, infographic, plan, artifacts, landscape, vertical),
        },
    }


def _notification_stages(
    task: PublicationTask,
    audio: Dict[str, Any],
    source: Dict[str, Any],
    infographic: Dict[str, Any],
    plan: Dict[str, Any],
    artifacts: Dict[str, Any],
    landscape: Dict[str, Any],
    vertical: Dict[str, Any],
) -> List[Dict[str, Any]]:
    if source.get("input_source") == "ds_response":
        source_name = "手动 DS 返回/信息图"
        source_step = "direct_ds"
        plan_name = "本地标题与时间戳规划"
        plan_step = "direct_ds_plan"
    elif source.get("input_source") == "ima_transcript":
        source_name = "IMA 全文 DS 处理/信息图"
        source_step = "transcript_deepseek"
        plan_name = "DS v4 flash 标题与时间戳规划"
        plan_step = "deepseek_plan"
    else:
        source_name = "Gemini Gem 解析/信息图"
        source_step = "gemini"
        plan_name = "DS v4 flash 标题与时间戳规划"
        plan_step = "deepseek_plan"
    return [
        {
            "name": "MP3 输入",
            "status": "completed" if audio else "pending",
            "message": _audio_message(audio),
        },
        {
            "name": source_name,
            "status": _stage_status(task, bool(source or infographic), source_step),
            "message": _gemini_message(source, infographic),
        },
        {
            "name": plan_name,
            "status": _stage_status(task, bool(plan), plan_step),
            "message": _plan_message(plan),
        },
        {
            "name": "封皮/背景图生成",
            "status": _stage_status(task, _visual_artifacts_ready(artifacts), "generate_visual_assets"),
            "message": _artifacts_message(artifacts),
        },
        {
            "name": "小君君横屏视频合成",
            "status": _child_stage_status(landscape),
            "message": _video_message(landscape),
        },
        {
            "name": "此刻此声竖屏视频合成",
            "status": _child_stage_status(vertical),
            "message": _video_message(vertical),
        },
    ]


def _notification_outputs(
    result: Dict[str, Any],
    audio: Dict[str, Any],
    infographic: Dict[str, Any],
    plan: Dict[str, Any],
    artifacts: Dict[str, Any],
    landscape: Dict[str, Any],
    vertical: Dict[str, Any],
) -> Dict[str, Any]:
    infographic_label = infographic.get("label") if isinstance(infographic, dict) else ""
    outputs = {
        "音频": _audio_message(audio),
        "标题": plan.get("title") if isinstance(plan, dict) else None,
        "音频时间戳": _plan_audio_message(plan),
        infographic_label or "信息图": _artifact_message(infographic),
        "封皮/背景图": _artifacts_message(artifacts),
        "横屏视频": _video_output_message(landscape),
        "竖屏视频": _video_output_message(vertical),
        "结果 JSON": result.get("output_path"),
    }
    return {key: value for key, value in outputs.items() if value}


def _notification_status(status: Any) -> str:
    mapping = {
        "pending": "queued",
        "processing": "running",
        "completed": "completed",
        "failed": "failed",
        "canceled": "canceled",
    }
    return mapping.get(status or "", status or "-")


def _stage_status(task: PublicationTask, completed: bool, active_step: str) -> str:
    if completed:
        return "completed"
    step = str(task.current_step or "")
    if task.status in {"pending", "processing"} and active_step in step:
        return "running"
    if task.status == "failed" and active_step in step:
        return "failed"
    return "pending"


def _child_stage_status(child: Any) -> str:
    if not isinstance(child, dict) or not child:
        return "pending"
    status = str(child.get("status") or "").lower()
    if status in {"completed", "succeeded"}:
        return "completed"
    if status in {"skipped", "skip"}:
        return "skipped"
    if status in {"failed", "canceled"}:
        return status
    if status in {"pending", "queued"}:
        return "queued"
    return "running"


def _audio_message(audio: Any) -> Optional[str]:
    if not isinstance(audio, dict) or not audio:
        return None
    parts = [audio.get("filename") or audio.get("name") or "MP3"]
    size = _format_bytes(audio.get("bytes"))
    if size:
        parts.append(size)
    duration = _format_seconds(audio.get("duration_seconds"))
    if duration:
        parts.append(duration)
    return " / ".join(str(item) for item in parts if item)


def _gemini_message(gemini: Any, infographic: Any) -> Optional[str]:
    if not isinstance(gemini, dict) and not isinstance(infographic, dict):
        return None
    parts = []
    if isinstance(gemini, dict):
        if gemini.get("initial_response_text") or gemini.get("response_text"):
            if gemini.get("input_source") == "ds_response":
                parts.append("手动 DS 返回已载入")
            elif gemini.get("input_source") == "ima_transcript":
                parts.append("DS 文本已返回")
            else:
                parts.append("第一轮返回已获取")
        if gemini.get("transcript_chars"):
            parts.append(f"转录 {gemini.get('transcript_chars')} 字")
        if gemini.get("target_url"):
            parts.append(gemini.get("target_url"))
    image_text = _artifact_message(infographic)
    if image_text:
        parts.append(f"信息图 {image_text}")
    return " / ".join(parts) if parts else None


def _plan_message(plan: Any) -> Optional[str]:
    if not isinstance(plan, dict) or not plan:
        return None
    title = plan.get("title") or "已规划"
    audio = _plan_audio_message(plan)
    return f"{title} / {audio}" if audio else title


def _plan_audio_message(plan: Any) -> Optional[str]:
    if not isinstance(plan, dict):
        return None
    audio = plan.get("audio") if isinstance(plan.get("audio"), dict) else {}
    if not audio:
        return None
    start = audio.get("start_seconds")
    end = audio.get("end_seconds")
    speed = audio.get("speed")
    end_text = "自动" if end is None else f"{end}s"
    return f"{start or 0}s - {end_text} / {speed or DEFAULT_SPEED}x"


def _artifacts_message(artifacts: Any) -> Optional[str]:
    if not isinstance(artifacts, dict) or not artifacts:
        return None
    labels = []
    for value in artifacts.values():
        if isinstance(value, dict):
            labels.append(value.get("label") or value.get("path"))
    labels = [item for item in labels if item]
    return f"{len(artifacts)} 张：{'、'.join(labels[:4])}" if labels else f"{len(artifacts)} 张"


def _artifact_message(artifact: Any) -> Optional[str]:
    if not isinstance(artifact, dict) or not artifact:
        return None
    path = artifact.get("path")
    size = _format_bytes(artifact.get("bytes"))
    label = artifact.get("label")
    return " / ".join(str(item) for item in [label, size, path] if item)


def _video_message(video: Any) -> Optional[str]:
    if not isinstance(video, dict) or not video:
        return None
    parts = []
    status = video.get("status")
    if status:
        parts.append(str(status))
    if video.get("step"):
        parts.append(str(video.get("step")))
    if video.get("progress") is not None:
        parts.append(f"{video.get('progress')}%")
    output = _video_output_message(video)
    if output:
        parts.append(output)
    if video.get("error"):
        parts.append(str(video.get("error")))
    return " / ".join(parts) if parts else None


def _video_output_message(video: Any) -> Optional[str]:
    if not isinstance(video, dict) or not video:
        return None
    return video.get("video_path") or video.get("landscape_video_path") or video.get("vertical_video_path")


def _format_bytes(value: Any) -> Optional[str]:
    try:
        size = float(value or 0)
    except (TypeError, ValueError):
        return None
    if size <= 0:
        return None
    units = ["B", "KB", "MB", "GB"]
    index = 0
    while size >= 1024 and index < len(units) - 1:
        size /= 1024
        index += 1
    digits = 0 if index == 0 else 1
    return f"{size:.{digits}f} {units[index]}"


def _format_seconds(value: Any) -> Optional[str]:
    try:
        seconds = float(value or 0)
    except (TypeError, ValueError):
        return None
    if seconds <= 0:
        return None
    total = int(round(seconds))
    minutes = total // 60
    remain = total % 60
    return f"{minutes:02d}:{remain:02d}"


def _celery_failure_message(child: PublicationTask) -> Optional[str]:
    if not child.celery_task_id:
        return None
    try:
        async_result = celery_app.AsyncResult(child.celery_task_id)
        state = async_result.state
    except Exception:
        return None
    if state not in CELERY_FAILURE_STATES:
        return None
    detail = ""
    try:
        detail = str(async_result.result) if async_result.result else ""
    except Exception as exc:
        detail = str(exc)
    return f"Celery 子任务异常退出: {state}: {detail}" if detail else f"Celery 子任务异常退出: {state}"


def _task_public_summary(task: Optional[PublicationTask]) -> Optional[Dict[str, Any]]:
    if not task:
        return None
    params = task.params if _model_attr_loaded(task, "params") and isinstance(task.params, dict) else {}
    result = task.result if _model_attr_loaded(task, "result") and isinstance(task.result, dict) else {}
    return {
        "job_id": task.id,
        "celery_id": task.celery_task_id,
        "status": task.status,
        "step": task.current_step,
        "progress": task.progress or 0,
        "feature": task.feature or params.get("feature"),
        "error": task.error_message or result.get("error"),
        "created_at": task.created_at.isoformat() + "Z" if task.created_at else None,
        "completed_at": task.completed_at.isoformat() + "Z" if task.completed_at else None,
    }


def _publisher_result_summary(task: PublicationTask) -> Dict[str, Any]:
    result = task.result if _model_attr_loaded(task, "result") and isinstance(task.result, dict) else {}
    video_path = (
        result.get("video_path")
        or result.get("landscape_video_path")
        or result.get("vertical_video_path")
        or getattr(task, "task_video_path", None)
    )
    aspect_ratio = _publisher_child_aspect_ratio(task)
    projected_cover_path = (
        getattr(task, "publisher_vertical_cover_path", None)
        or getattr(task, "publisher_podcast_cover_path", None)
        or getattr(task, "publisher_bilibili_cover_path", None)
    )
    return {
        **(_task_public_summary(task) or {}),
        "generate_subtitles": _publisher_child_generate_subtitles(task, default=True),
        "audio_signature": _publisher_child_audio_signature(task),
        "video_path": video_path,
        "landscape_video_path": video_path if aspect_ratio != "9:16" else None,
        "vertical_video_path": result.get("vertical_video_path") or (video_path if aspect_ratio == "9:16" else None),
        "cover_path": result.get("cover_path") or projected_cover_path,
        "bilibili_result": result.get("bilibili_result"),
        "youtube_result": result.get("youtube_result"),
    }


def _public_gemini_result(result: Dict[str, Any]) -> Dict[str, Any]:
    image_label = "DS 文本信息图" if result.get("input_source") == "ima_transcript" else "Gemini 横向信息图"
    return {
        "provider": result.get("provider"),
        "model": result.get("model"),
        "input_source": result.get("input_source"),
        "initial_response_text": result.get("initial_response_text"),
        "response_text": result.get("response_text"),
        "final_response_text": result.get("final_response_text"),
        "turns": result.get("turns") or [],
        "file": result.get("file"),
        "image": _public_artifact(result.get("image"), image_label) if result.get("image") else None,
        "target_url": result.get("target_url"),
        "page_url": result.get("page_url"),
        "output_path": result.get("output_path"),
        "completed_at": result.get("completed_at"),
    }


def _public_text_source_result(result: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "provider": result.get("provider"),
        "model": result.get("model"),
        "input_source": result.get("input_source"),
        "initial_response_text": result.get("initial_response_text"),
        "response_text": result.get("response_text"),
        "transcript_chars": result.get("transcript_chars"),
        "cache": result.get("cache"),
        "image": _public_artifact(result.get("image"), "DS 文本信息图") if result.get("image") else None,
        "output_path": result.get("output_path"),
        "completed_at": result.get("completed_at"),
    }


def _public_artifact(value: Any, label: str = "") -> Dict[str, Any]:
    if isinstance(value, dict):
        path = value.get("path")
        payload = dict(value)
    else:
        path = str(value or "")
        payload = {"path": path}
    if path and os.path.exists(path):
        payload["bytes"] = os.path.getsize(path)
    payload["label"] = label or payload.get("label")
    return payload


def _artifact_meta(path: str, label: str, *, renderer: Optional[str] = None) -> Dict[str, Any]:
    return {
        "path": path,
        "label": label,
        "template_version": VISUAL_ARTIFACT_VERSION,
        "renderer": renderer,
        "bytes": os.path.getsize(path) if path and os.path.exists(path) else None,
    }


def _audio_asset_filter():
    ext_conditions = [ZsxqAttachment.file_ext == ext for ext in sorted(AUDIO_EXTENSIONS)]
    path_conditions = []
    for ext in sorted(AUDIO_EXTENSIONS):
        path_conditions.append(ZsxqAttachment.name.ilike(f"%.{ext}"))
        path_conditions.append(ZsxqAttachment.local_path.ilike(f"%.{ext}"))
    return or_(*(ext_conditions + path_conditions))


def _audio_asset_row_payload(row) -> Optional[Dict[str, Any]]:
    if not row:
        return None
    path = _resolve_project_path(row.local_path)
    if not path or not path.exists() or not path.is_file():
        return None
    stat = path.stat()
    filename = row.attachment_name or path.name
    return {
        "attachment_id": row.attachment_id,
        "filename": filename,
        "name": filename,
        "path": str(path),
        "bytes": stat.st_size,
        "file_ext": (row.file_ext or path.suffix.lstrip(".")).lower(),
        "download_count": row.download_count,
        "updated_at": row.attachment_updated_at.isoformat() + "Z" if row.attachment_updated_at else None,
        "created_at": row.attachment_created_at.isoformat() + "Z" if row.attachment_created_at else None,
        "group_id": row.group_id,
        "tab_id": row.tab_id,
        "topic_id": row.topic_id,
        "topic_title": row.topic_title,
        "topic_author": getattr(row, "topic_author", None),
        "topic_content_excerpt": _clip_text(_clean_line(row.topic_content), 180),
        "topic_create_time": row.topic_create_time,
        "topic_first_synced_at": _isoformat_z(getattr(row, "topic_first_synced_at", None)),
        "topic_last_synced_at": _isoformat_z(getattr(row, "topic_last_synced_at", None)),
        "tab_label": getattr(row, "tab_label", None),
        "preview_url": f"/api/mp3-video-workflows/audio-assets/{row.attachment_id}/preview",
    }


def _isoformat_z(value):
    if not value:
        return None
    if isinstance(value, str):
        return value
    return value.isoformat() + "Z"


def _resolve_project_path(path_value: Any) -> Optional[Path]:
    text = str(path_value or "").strip()
    if not text:
        return None
    path = Path(text).expanduser()
    if path.is_absolute():
        return path
    return (project_root() / path).resolve()


def _font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    candidates = [
        os.environ.get("MP3_VIDEO_FONT_PATH"),
        os.environ.get("REPORT_THUMBNAIL_FONT_PATH"),
        str(project_root() / "assets" / "fonts" / ("SourceHanSansSC-Heavy.otf" if bold else "SourceHanSansSC-Regular.otf")),
        str(project_root() / "assets" / "fonts" / "SourceHanSansSC-Bold.otf"),
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/System/Library/Fonts/Supplemental/Songti.ttc",
        "/System/Library/Fonts/PingFang.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "C:\\Windows\\Fonts\\msyhbd.ttc",
        "C:\\Windows\\Fonts\\msyh.ttc",
    ]
    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default()


def _accent_color(emotion: Any) -> Tuple[int, int, int]:
    return {
        "pro": (191, 161, 95),
        "macro": (250, 204, 21),
        "bull": (74, 222, 128),
        "bear": (239, 68, 68),
    }.get(str(emotion or "bull"), (74, 222, 128))


def _draw_vertical_gradient(draw: ImageDraw.ImageDraw, w: int, h: int, start: Tuple[int, int, int], end: Tuple[int, int, int]) -> None:
    for y in range(h):
        ratio = y / max(1, h - 1)
        color = tuple(int(start[i] + (end[i] - start[i]) * ratio) for i in range(3))
        draw.line([(0, y), (w, y)], fill=color)


def _draw_grid(draw: ImageDraw.ImageDraw, w: int, h: int, *, spacing: int, color: Tuple[int, int, int, int]) -> None:
    for x in range(0, w, spacing):
        draw.line([(x, 0), (x, h)], fill=color, width=1)
    for y in range(0, h, spacing):
        draw.line([(0, y), (w, y)], fill=color, width=1)


def _draw_market_silhouette(draw: ImageDraw.ImageDraw, w: int, h: int) -> None:
    base = h
    x = int(w * 0.55)
    widths = [62, 78, 58, 112, 72, 96, 72, 120, 84]
    heights = [250, 420, 320, 560, 380, 650, 455, 710, 500]
    for width, height in zip(widths, heights):
        draw.rectangle([x, base - height, x + width, base], fill=(255, 255, 255, 12))
        draw.rectangle([x, base - height, x + 7, base], fill=(255, 255, 255, 24))
        x += width + 22


def _draw_curve(draw: ImageDraw.ImageDraw, w: int, h: int, accent: Tuple[int, int, int]) -> None:
    points = [
        (int(w * 0.45), h - 20),
        (int(w * 0.55), int(h * 0.74)),
        (int(w * 0.67), int(h * 0.62)),
        (int(w * 0.78), int(h * 0.38)),
        (int(w * 0.88), int(h * 0.29)),
        (w, int(h * 0.12)),
    ]
    for offset, alpha in [(16, 42), (8, 72), (0, 180)]:
        shifted = [(x, y + offset) for x, y in points]
        draw.line(shifted, fill=accent + (alpha,), width=8 if offset else 5, joint="curve")


def _draw_footer_mark(draw: ImageDraw.ImageDraw, x: int, y: int, text: str, accent: Tuple[int, int, int], font: ImageFont.ImageFont) -> None:
    bars = [15, 30, 20, 35, 10]
    for index, height in enumerate(bars):
        draw.rectangle([x + index * 12, y - height, x + index * 12 + 6, y], fill=accent)
    draw.text((x + 72, y - 34), text, font=font, fill=(148, 163, 184))


def _draw_wave_hint(draw: ImageDraw.ImageDraw, center_x: int, y: int, accent: Tuple[int, int, int]) -> None:
    widths = [8, 8, 8, 8, 8, 8, 8]
    heights = [24, 42, 58, 36, 68, 44, 28]
    total_w = len(widths) * 18
    x = center_x - total_w // 2
    for index, height in enumerate(heights):
        draw.rounded_rectangle([x + index * 18, y - height // 2, x + index * 18 + 8, y + height // 2], radius=4, fill=accent)


def _draw_two_line_title(
    draw: ImageDraw.ImageDraw,
    text: Any,
    x: int,
    y: int,
    max_width: int,
    *,
    base_size: int,
    min_size: int,
    fill: Tuple[int, int, int],
    shadow: Optional[Tuple[int, int, int, int]] = None,
) -> int:
    raw = _clean_multiline(text)
    clean = re.sub(r"\s+", "", raw) if re.search(r"[\u4e00-\u9fff]", raw) else _clean_line(raw.replace("\n", " "))
    clean = clean or _clean_line(text)
    for size in range(base_size, min_size - 1, -4):
        font = _font(size, bold=True)
        lines = _best_two_line_title(draw, clean, font, max_width)
        if lines and all(draw.textlength(line, font=font) <= max_width for line in lines):
            return _draw_title_lines(draw, lines, x, y, font, fill=fill, line_height=int(size * 1.22), shadow=shadow)

    font = _font(min_size, bold=True)
    lines = _best_two_line_title(draw, clean, font, max_width) or _wrap_text(draw, clean, font, max_width, max_lines=2)
    lines = [_trim_text_to_width(draw, line, font, max_width) for line in lines[:2]]
    return _draw_title_lines(draw, lines, x, y, font, fill=fill, line_height=int(min_size * 1.22), shadow=shadow)


def _best_two_line_title(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> List[str]:
    clean = _clean_line(text).strip("，,。；;：:、 ")
    if not clean:
        return [""]
    if draw.textlength(clean, font=font) <= max_width:
        return [clean]
    if len(clean) <= 2:
        return [_trim_text_to_width(draw, clean, font, max_width)]

    best_lines: Optional[List[str]] = None
    best_score: Optional[float] = None
    midpoint = len(clean) / 2
    for index in range(1, len(clean)):
        left = clean[:index].strip("，,。；;：:、 ")
        right = clean[index:].strip("，,。；;：:、 ")
        if not left or not right:
            continue
        left_w = draw.textlength(left, font=font)
        right_w = draw.textlength(right, font=font)
        if left_w > max_width or right_w > max_width:
            continue
        punctuation_bonus = -180 if clean[index - 1] in "，,。；;：:、" else 0
        score = abs(left_w - right_w) + abs(index - midpoint) * 18 + punctuation_bonus + _title_split_penalty(clean, index)
        if best_score is None or score < best_score:
            best_score = score
            best_lines = [left, right]
    if best_lines:
        return best_lines
    return _wrap_text(draw, clean, font, max_width, max_lines=2)


def _title_split_penalty(text: str, index: int) -> int:
    protected_terms = (
        "半导体",
        "人工智能",
        "碳化硅",
        "先进材料",
        "电子级",
        "新能源",
        "机器人",
        "数据中心",
    )
    for term in protected_terms:
        start = text.find(term)
        while start >= 0:
            if start < index < start + len(term):
                return 500
            start = text.find(term, start + 1)
    return 0


def _draw_title_lines(
    draw: ImageDraw.ImageDraw,
    lines: List[str],
    x: int,
    y: int,
    font: ImageFont.ImageFont,
    *,
    fill: Tuple[int, int, int],
    line_height: int,
    shadow: Optional[Tuple[int, int, int, int]] = None,
) -> int:
    for index, line in enumerate(lines):
        yy = y + index * line_height
        if shadow:
            draw.text((x + 4, yy + 5), line, font=font, fill=shadow)
        draw.text((x, yy), line, font=font, fill=fill)
    return y + len(lines) * line_height


def _trim_text_to_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> str:
    clean = _clean_line(text)
    if draw.textlength(clean, font=font) <= max_width:
        return clean
    suffix = "..."
    while clean and draw.textlength(clean + suffix, font=font) > max_width:
        clean = clean[:-1]
    return clean + suffix if clean else suffix


def _closed_door_background(w: int, h: int) -> Image.Image:
    ref_path = project_root() / "src" / "frontend" / "assets" / "cover" / "default" / "closed-door-reference-bg.png"
    if ref_path.exists():
        return ImageOps.fit(Image.open(ref_path).convert("RGB"), (w, h), method=Image.Resampling.LANCZOS)
    img = Image.new("RGB", (w, h), "#06182c")
    draw = ImageDraw.Draw(img)
    _draw_vertical_gradient(draw, w, h, (6, 24, 44), (2, 9, 21))
    _draw_grid(draw, w, h, spacing=78, color=(121, 169, 206, 22))
    for i in range(8):
        x = 80 + i * 210
        y = 120 + (i % 3) * 72
        draw.rounded_rectangle([x, y, x + 520, y + 190], radius=16, fill=(255, 255, 255, 16), outline=(255, 255, 255, 22), width=2)
    return img


def _open_image(path: str) -> Optional[Image.Image]:
    try:
        if path and os.path.exists(path):
            return Image.open(path)
    except Exception:
        return None
    return None


def _draw_wrapped_text(
    draw: ImageDraw.ImageDraw,
    text: Any,
    x: int,
    y: int,
    max_width: int,
    font: ImageFont.ImageFont,
    *,
    fill: Tuple[int, int, int],
    line_height: int,
    max_lines: int = 4,
    shadow: Optional[Tuple[int, int, int, int]] = None,
) -> int:
    lines = _wrap_text(draw, text, font, max_width, max_lines=max_lines)
    for index, line in enumerate(lines):
        yy = y + index * line_height
        if shadow:
            draw.text((x + 4, yy + 5), line, font=font, fill=shadow)
        draw.text((x, yy), line, font=font, fill=fill)
    return y + len(lines) * line_height


def _draw_centered_wrapped(
    draw: ImageDraw.ImageDraw,
    text: Any,
    center_x: int,
    y: int,
    max_width: int,
    font: ImageFont.ImageFont,
    *,
    fill: Tuple[int, int, int],
    line_height: int,
    max_lines: int = 2,
    shadow: Optional[Tuple[int, int, int, int]] = None,
) -> int:
    lines = _wrap_text(draw, text, font, max_width, max_lines=max_lines)
    for index, line in enumerate(lines):
        yy = y + index * line_height
        if shadow:
            _draw_centered_text(draw, line, center_x + 4, yy + 5, font, fill=shadow)
        _draw_centered_text(draw, line, center_x, yy, font, fill=fill)
    return y + len(lines) * line_height


def _draw_centered_text(draw: ImageDraw.ImageDraw, text: str, center_x: int, y: int, font: ImageFont.ImageFont, *, fill: Any) -> None:
    bbox = draw.textbbox((0, 0), text, font=font)
    draw.text((center_x - (bbox[2] - bbox[0]) // 2, y), text, font=font, fill=fill)


def _draw_date_pill(draw: ImageDraw.ImageDraw, center_x: int, y: int, text: str, font: ImageFont.ImageFont) -> None:
    text = _clean_line(text)
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    pill_w = text_w + 96
    pill_h = 74
    x = center_x - pill_w // 2
    draw.rounded_rectangle([x, y, x + pill_w, y + pill_h], radius=37, fill=(15, 23, 42, 210), outline=(255, 255, 255, 80), width=2)
    draw.text((x + 48, y + 14), text, font=font, fill=(255, 255, 255))


def _infographic_bullets(text: str) -> List[str]:
    sections = _direct_ds_numbered_sections(text)
    preferred_sections = [sections.get("5", ""), sections.get("4", "")]
    candidates: List[str] = []
    sources = [item for item in preferred_sections if _clean_line(item)] or [str(text or "")]
    for source in sources:
        for raw in str(source or "").replace("\r", "\n").split("\n"):
            clean = _clean_infographic_copy(raw, max_chars=96)
            if not clean:
                continue
            candidates.append(clean)
        if len(candidates) >= 3:
            break
    if len(candidates) < 3 and sections:
        for raw in str(text or "").replace("\r", "\n").split("\n"):
            clean = _clean_infographic_copy(raw, max_chars=96)
            if clean:
                candidates.append(clean)
    return _merge_infographic_points([], candidates)[:4]


def _clean_infographic_copy(value: Any, *, max_chars: int = 96) -> str:
    clean = _clean_line(value).strip("#*-•·、 ：:，,。")
    clean = re.sub(r"^\d+\s*[.、)]\s*", "", clean).strip()
    clean = re.sub(r"^(?:要点|核心观点|观点|金句|推荐理由)\s*[:：]\s*", "", clean).strip()
    clean = re.sub(r"[（(]\s*(?:约)?\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?\s*(?:附近)?\s*[)）]$", "", clean).strip()
    if not clean or len(clean) < 8 or _is_cover_process_text(clean):
        return ""
    if re.search(
        r"作为(?:您的|一个)?(?:投研|投资|财经)?助手|根据您提供|我已为您|为您整理|"
        r"以下是(?:针对|关于)|\d+个问题|具体回答|(?:第一|本)轮返回|输出如下|"
        r"投资者视角标题|高信息密度.*标题|精简短标题|优化标题|闭门会格式",
        clean,
        flags=re.I,
    ):
        return ""
    return _clip_text(clean, max_chars)


def _normalize_infographic_points(value: Any) -> List[str]:
    values = value if isinstance(value, list) else []
    return _merge_infographic_points([], values)


def _merge_infographic_points(primary: List[Any], secondary: List[Any]) -> List[str]:
    merged: List[str] = []
    seen = set()
    for value in [*primary, *secondary]:
        clean = _clean_infographic_copy(value, max_chars=68)
        key = _cover_compare_key(clean)
        if not clean or not key or key in seen:
            continue
        if any(min(len(key), len(existing)) >= 18 and (key in existing or existing in key) for existing in seen):
            continue
        seen.add(key)
        merged.append(clean)
    return merged


def _exclude_infographic_copy(points: List[Any], excluded: List[Any]) -> List[str]:
    excluded_keys = [_cover_compare_key(value) for value in excluded if _cover_compare_key(value)]
    kept = []
    for point in _normalize_infographic_points(points):
        key = _cover_compare_key(point)
        if any(
            key == excluded_key
            or (min(len(key), len(excluded_key)) >= 18 and (key in excluded_key or excluded_key in key))
            for excluded_key in excluded_keys
        ):
            continue
        kept.append(point)
    return kept


def _infographic_copy_from_source(
    source_text: str,
    *,
    fallback_title: Any = "",
    fallback_subtitle: Any = "",
) -> Dict[str, Any]:
    sections = _direct_ds_numbered_sections(source_text)
    short_title = _direct_ds_title_value(sections.get("3", "")) if sections else ""
    title_candidates = extract_title_candidates(source_text)
    title = (
        _clean_infographic_copy(short_title, max_chars=36)
        or _clean_infographic_copy(fallback_title, max_chars=36)
        or (_clean_infographic_copy(title_candidates[0], max_chars=36) if title_candidates else "")
        or "核心投资观点"
    )
    points = _infographic_bullets(source_text)
    subtitle = (
        _clean_infographic_copy(fallback_subtitle, max_chars=68)
        or (points[0] if points else "")
    )
    display_points = _exclude_infographic_copy(points, [title, subtitle])
    return {
        "infographic_title": title,
        "infographic_subtitle": subtitle,
        "infographic_points": display_points[:3],
    }


def _preview_caption_candidates(source_text: str, plan: Dict[str, Any]) -> List[str]:
    captions: List[str] = []
    for value in (
        plan.get("subtitle"),
        plan.get("cover_subtitle"),
        plan.get("xiaojunjun_subtitle"),
        plan.get("title"),
    ):
        for line in str(value or "").replace("\r", "\n").split("\n"):
            clean = _clean_line(line)
            if 6 <= len(clean) <= 72:
                captions.append(clean)

    for raw in re.split(r"[。！？!?；;\n]", str(source_text or "")):
        clean = _clean_line(raw).strip("#*-•·、 ")
        clean = re.sub(r"^\d+\s*[.、)]\s*", "", clean).strip()
        if not clean or len(clean) < 8:
            continue
        if re.search(r"标题|时间戳|推荐截取|字段|JSON|封面|Prompt", clean, flags=re.I):
            continue
        captions.append(_clip_text(clean, 64))
        if len(captions) >= 8:
            break

    seen = set()
    unique = []
    for caption in captions:
        caption = _clean_line(caption)
        if not caption or caption in seen:
            continue
        seen.add(caption)
        unique.append(caption)
    return unique[:6] or ["这是视频字幕预览位置，用于验证画面和字幕风格"]


def _keyframe_milestones(audio: Dict[str, Any], plan: Dict[str, Any], *, frame_count: int = 3) -> List[Dict[str, Any]]:
    plan_audio = plan.get("audio") if isinstance(plan.get("audio"), dict) else {}
    duration = float(audio.get("duration_seconds") or 0)
    start = _parse_seconds(plan_audio.get("start_seconds"), default=0.0) or 0.0
    end = _parse_seconds(plan_audio.get("end_seconds"), default=None)
    if end is None:
        if duration and duration > start:
            end = duration
        else:
            end = start + 120.0
    end = max(start + 1.0, float(end))
    if frame_count <= 1:
        points = [start]
    else:
        span = end - start
        ratios = [0.04, 0.5, 0.92][:frame_count]
        points = [start + span * ratio for ratio in ratios]
    return [{"seconds": round(point, 3), "label": _seconds_label(point)} for point in points]


def _seconds_label(value: float) -> str:
    total = max(0, int(round(float(value or 0))))
    minutes, seconds = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def _wrap_text(draw: ImageDraw.ImageDraw, text: Any, font: ImageFont.ImageFont, max_width: int, *, max_lines: int = 4) -> List[str]:
    raw_lines = str(text or "").replace("\r", "\n").split("\n")
    lines: List[str] = []
    for raw in raw_lines:
        current = ""
        for ch in raw.strip():
            candidate = current + ch
            if draw.textlength(candidate, font=font) > max_width and current:
                lines.append(current)
                current = ch
                if len(lines) >= max_lines:
                    return lines[:max_lines]
            else:
                current = candidate
        if current:
            lines.append(current)
        if len(lines) >= max_lines:
            break
    return lines[:max_lines] or [""]


def _cover_lines(text: Any, *, max_chars: int, max_lines: int = 2) -> str:
    lines = [_clean_line(line) for line in str(text or "").replace("\r", "\n").split("\n") if _clean_line(line)]
    if len(lines) >= max_lines:
        return "\n".join(line[:max_chars] for line in lines[:max_lines])
    single = _clean_line(text)
    if len(single) <= max_chars:
        return single
    output = []
    cursor = 0
    while cursor < len(single) and len(output) < max_lines:
        piece = single[cursor:cursor + max_chars].strip("，,、。.;；:：!！?？ ")
        if piece:
            output.append(piece)
        cursor += max_chars
    return "\n".join(output)


def _parse_seconds(value: Any, default: Optional[float] = None) -> Optional[float]:
    if value in (None, ""):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return default
    if ":" in text:
        parts = [float(part or 0) for part in text.split(":")]
        seconds = 0.0
        for part in parts:
            seconds = seconds * 60 + part
        return seconds
    try:
        return float(text)
    except (TypeError, ValueError):
        return default


def _json_from_text(value: str) -> Dict[str, Any]:
    text = (value or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()
    match = re.search(r"\{.*\}", text, flags=re.S)
    if match:
        text = match.group(0)
    return json.loads(text)


def _clean_line(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _clean_multiline(value: Any) -> str:
    lines = [_clean_line(line) for line in str(value or "").replace("\r", "\n").split("\n")]
    return "\n".join(line for line in lines if line)


def _clip_text(value: Any, max_length: int) -> str:
    text = _clean_line(value)
    return text[:max_length] if max_length and len(text) > max_length else text


def _compact_text(value: Any, max_chars: int) -> str:
    text = str(value or "").strip()
    if len(text) <= max_chars:
        return text
    head = max_chars // 2
    tail = max_chars - head
    return f"{text[:head]}\n\n...[中间省略 {len(text) - max_chars} 字]...\n\n{text[-tail:]}"


def _safe_step_code(value: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9_]+", "_", str(value or "running")).strip("_") or "running"
    return text[:80]


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _env(*names: str) -> str:
    default = names[-1] if names else ""
    for name in names[:-1]:
        value = os.environ.get(name)
        if value:
            return value
    return default
