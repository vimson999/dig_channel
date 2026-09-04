import json
import hashlib
import logging
import os
import re
import time
import copy
from collections import OrderedDict
from contextvars import ContextVar
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from threading import Lock
from types import SimpleNamespace

from sqlalchemy import inspect as sqlalchemy_inspect, text
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.orm import load_only
from sqlalchemy.orm.attributes import flag_modified

from app import create_app
from celery_config import celery_app
from models import db
from models.task import PublicationTask
from services import doubao_browser_tts as doubao_service
from services import gemini_browser as gemini_service
from services.report_audio_workflow import (
    DEFAULT_INITIAL_PROMPT,
    DEFAULT_REVIEW_PROMPT,
    FEATURE_NAME,
    audio_path_for_job,
    clean_final_script_for_tts,
    combine_wav_files,
    normalize_for_tts,
    pdf_first_page_image_path_for_job,
    render_pdf_first_page_image,
    review_prompt_for_pdf_evidence,
    review_report_cover_titles,
    split_tts_text,
    storage_path_for_job,
    structured_result_quality_issue,
    synthesize_doubao_api_chunks,
)
from services.report_audio_gem_v2 import (
    SCHEMA_VERSION as GEM_V2_SCHEMA_VERSION,
    build_gem_v2_trigger,
    resolve_gem_v2_mode,
    to_report_audio_structured_result,
    validate_gem_v2_response,
)
from services.report_pdf_evidence import build_and_store_evidence
from services.report_audio_completion_contract import (
    evaluate_report_video_completion,
    probe_report_media,
)
from services.thumbnail_service import thumbnail_service
from services.publisher_queue_repair import maybe_requeue_stale_publisher_child
from services.runtime_guard import (
    apply_async_after_db_release,
    guard_publication_task_start,
    is_retryable_db_error,
    release_db_session_quietly,
    revoke_celery_task_after_db_commit,
    retry_exception_types,
)
from services.workflow_notification_service import (
    public_browser_human_intervention_notification,
    workflow_notification_service,
)
from services.workflow_worker_readiness import require_report_audio_workers_ready
from tasks.doubao_browser_tts import process_doubao_browser_tts_job
from tasks.gemini_browser import process_report_pdf_gemini_job
from tasks.publisher import run_publication_pipeline


logger = logging.getLogger("REPORT_AUDIO_WORKFLOW_TASK")
COMPLETION_MEDIA_PROBE = probe_report_media
_active_workflow_state = ContextVar("report_audio_workflow_state", default=None)
_workflow_resume_cache = OrderedDict()
_workflow_resume_cache_lock = Lock()
_WORKFLOW_RESUME_CACHE_LIMIT = 8

DEFAULT_DB_RETRIES = 5
DEFAULT_DB_RETRY_DELAY_SECONDS = 60
DB_RETRIES_ENV_NAME = "REPORT_AUDIO_WORKFLOW_DB_RETRIES"
DB_RETRY_DELAY_ENV_NAME = "REPORT_AUDIO_WORKFLOW_DB_RETRY_DELAY_SECONDS"
GEMINI_RETRY_LIMIT_ENV_NAME = "REPORT_AUDIO_GEMINI_RETRY_LIMIT"
DEFAULT_GEMINI_RETRY_LIMIT = 2
DEFAULT_CHILD_TIMEOUT_SECONDS = 1800
DEFAULT_DOUBAO_INLINE_WAIT_SECONDS = 5
DEFAULT_PUBLISHER_INLINE_WAIT_SECONDS = 5
DEFAULT_PARENT_RETRY_DELAY_SECONDS = 300
DEFAULT_DOUBAO_PARENT_RETRY_DELAY_SECONDS = 30
DEFAULT_PUBLISHER_PARENT_RETRY_DELAY_SECONDS = 60
DEFAULT_PARENT_MAX_DEFERS = 144
DEFAULT_DOUBAO_PARENT_MAX_DEFERS = 240
DEFAULT_PUBLISHER_PARENT_MAX_DEFERS = 180
KEYFRAME_OUTPUTS = {
    "bilibili_cover",
    "vertical_cover",
    "landscape_keyframe",
    "vertical_keyframe_scheme1",
    "vertical_keyframe_scheme2",
    "vertical_keyframe_scheme3",
}
GENERIC_COVER_TITLE_NORMALIZED = {
    "pdf",
    "mp3",
    "音频",
    "视频",
    "外资",
    "外资研报",
    "研报",
    "调研",
    "调研纪要",
    "纪要",
    "研报播客",
    "研报视频",
    "研报视频编排",
}
MATERIAL_REUSE_FEATURES = (
    "report_script_material_workflow",
    "report_audio_workflow",
    FEATURE_NAME,
)

RETRYABLE_CHILD_ERROR_FRAGMENTS = (
    "超时",
    "子任务仍在执行",
    "timeout",
    "timed out",
    "文件上传失败",
    "上传完成超时",
    "上传被取消",
    "提示词未成功提交",
    "未成功提交",
    "发送按钮",
    "等待 Gemini",
    "页面自动化超时",
    "未捕获到 PCM 音频",
    "Locator.click",
    "Target closed",
    "Browser closed",
    "context or browser has been closed",
    "page has been closed",
    "Protocol error",
    "net::ERR",
)
NON_RETRYABLE_CHILD_ERROR_FRAGMENTS = (
    "父编排任务已取消",
    "子任务不存在",
    "任务不存在",
    "PDF 文件不能为空",
)


TASK_STATE_COLUMNS = (
    PublicationTask.id,
    PublicationTask.celery_task_id,
    PublicationTask.status,
    PublicationTask.current_step,
    PublicationTask.progress,
    PublicationTask.result,
    PublicationTask.error_message,
    PublicationTask.created_at,
    PublicationTask.updated_at,
    PublicationTask.completed_at,
)
TASK_PAYLOAD_COLUMNS = (
    *TASK_STATE_COLUMNS,
    PublicationTask.params,
    PublicationTask.user_id,
    PublicationTask.feature,
)
TASK_CHILD_COLUMNS = (
    *TASK_STATE_COLUMNS,
    PublicationTask.feature,
    PublicationTask.parent_task_id,
    PublicationTask.source_task_id,
)
TASK_CHILD_POLL_COLUMNS = (
    PublicationTask.id,
    PublicationTask.celery_task_id,
    PublicationTask.status,
    PublicationTask.current_step,
    PublicationTask.progress,
    PublicationTask.error_message,
    PublicationTask.created_at,
    PublicationTask.updated_at,
    PublicationTask.completed_at,
    PublicationTask.feature,
    PublicationTask.parent_task_id,
    PublicationTask.source_task_id,
)
TASK_RESUME_COLUMNS = (
    PublicationTask.id,
    PublicationTask.status,
    PublicationTask.current_step,
    PublicationTask.progress,
    PublicationTask.error_message,
    PublicationTask.feature,
    PublicationTask.created_at,
    PublicationTask.updated_at,
    PublicationTask.completed_at,
    PublicationTask.waiting_child_label,
    PublicationTask.waiting_child_id,
    PublicationTask.waiting_child_status,
    PublicationTask.waiting_child_step,
)
TASK_PUBLISHER_MATCH_COLUMNS = (
    *TASK_CHILD_POLL_COLUMNS,
    PublicationTask.task_video_path,
    PublicationTask.publisher_landscape_enabled,
    PublicationTask.publisher_vertical_enabled,
    PublicationTask.publisher_render_mode,
    PublicationTask.publisher_vertical_scheme,
    PublicationTask.publisher_preview_seconds,
    PublicationTask.publisher_vertical_video_path,
    PublicationTask.publisher_vertical_cover_path,
    PublicationTask.publisher_podcast_cover_path,
)
TASK_TTS_MATCH_COLUMNS = (
    *TASK_CHILD_POLL_COLUMNS,
    PublicationTask.doubao_tts_text_hash,
    PublicationTask.doubao_tts_chunk_index,
    PublicationTask.doubao_tts_chunk_attempt,
    PublicationTask.doubao_tts_wav_path,
)


def _load_task(task_id, *columns):
    seen = set()
    attrs = []
    for column in (PublicationTask.id, *columns):
        key = getattr(column, "key", str(column))
        if key in seen:
            continue
        seen.add(key)
        attrs.append(column)
    task = (
        PublicationTask.query.options(load_only(*attrs))
        .filter(PublicationTask.id == task_id)
        .one_or_none()
    )
    state = _active_workflow_state.get()
    if task and state and state.get("task_id") == str(task_id):
        for name in (
            "params", "result", "status", "current_step", "progress",
            "error_message", "created_at", "updated_at", "completed_at",
        ):
            if name in task.__dict__:
                value = task.__dict__.get(name)
                state[name] = copy.deepcopy(value) if name in {"params", "result"} else value
    return task


def _persist_child_celery_task_id(child_id, celery_task_id):
    (
        PublicationTask.query
        .filter(PublicationTask.id == child_id)
        .update(
            {
                "celery_task_id": celery_task_id,
                "updated_at": datetime.utcnow(),
            },
            synchronize_session=False,
        )
    )
    db.session.commit()


def _workflow_result_scope(operation):
    @wraps(operation)
    def wrapped(task_id, *args, **kwargs):
        task_key = str(task_id)
        with _workflow_resume_cache_lock:
            cached = _workflow_resume_cache.pop(task_key, None)
        state = copy.deepcopy(cached) if cached else {"task_id": task_key, "params": None, "result": None}
        state["task_id"] = task_key
        token = _active_workflow_state.set(state)
        try:
            return operation(task_id, *args, **kwargs)
        finally:
            latest = _active_workflow_state.get()
            cacheable = (
                latest
                and latest.get("params") is not None
                and latest.get("result") is not None
                and latest.get("status") not in {"completed", "failed", "blocked_by_captcha", "canceled"}
            )
            if cacheable:
                with _workflow_resume_cache_lock:
                    _workflow_resume_cache[task_key] = copy.deepcopy(latest)
                    _workflow_resume_cache.move_to_end(task_key)
                    while len(_workflow_resume_cache) > _WORKFLOW_RESUME_CACHE_LIMIT:
                        _workflow_resume_cache.popitem(last=False)
            else:
                with _workflow_resume_cache_lock:
                    _workflow_resume_cache.pop(task_key, None)
            _active_workflow_state.reset(token)
    return wrapped


def _cached_workflow_result(task_id):
    state = _active_workflow_state.get()
    if not state or state.get("task_id") != str(task_id) or state.get("result") is None:
        return None
    return copy.deepcopy(state["result"])


def _replace_cached_workflow_result(task_id, result):
    state = _active_workflow_state.get()
    if state and state.get("task_id") == str(task_id):
        state["result"] = copy.deepcopy(result or {})


def _merge_cached_workflow_result(task_id, values):
    state = _active_workflow_state.get()
    if state and state.get("task_id") == str(task_id) and state.get("result") is not None:
        state["result"].update(copy.deepcopy(values or {}))


def _merge_cached_workflow_fields(task_id, **values):
    state = _active_workflow_state.get()
    if state and state.get("task_id") == str(task_id):
        state.update(values)


def _cached_workflow_task(task_id):
    state = _active_workflow_state.get()
    if not state or state.get("task_id") != str(task_id) or state.get("result") is None:
        return None
    return SimpleNamespace(
        id=str(task_id),
        params=copy.deepcopy(state.get("params") or {}),
        result=copy.deepcopy(state.get("result") or {}),
        status=state.get("status"),
        current_step=state.get("current_step"),
        progress=state.get("progress"),
        error_message=state.get("error_message"),
        created_at=state.get("created_at"),
        completed_at=state.get("completed_at"),
    )


def _is_postgresql_session():
    try:
        return db.session.get_bind().dialect.name == "postgresql"
    except Exception:
        return False


def _execute_parent_update(statement, params, description):
    last_error = None
    for attempt in range(1, 4):
        try:
            updated = db.session.execute(text(statement), params)
            db.session.commit()
            return updated.rowcount
        except Exception as exc:
            last_error = exc
            db.session.rollback()
            if attempt < 3:
                time.sleep(0.2 * attempt)
    logger.error("[REPORT_AUDIO_WORKFLOW] %s failed: %s", description, last_error)
    raise last_error


def _persist_parent_result_fallback(parent_id, result, **fields):
    values = {PublicationTask.result: result, PublicationTask.updated_at: datetime.utcnow()}
    projected = PublicationTask(result=copy.deepcopy(result))
    for name in (
        "report_audio_result_pdf_filename",
        "report_audio_result_pdf_bytes",
        "report_audio_result_pdf_topic_title",
        "report_audio_cover_title",
        "report_audio_cover_subtitle",
        "report_audio_structured_title",
        "report_audio_started_at",
        "report_audio_completed_at",
        "report_audio_error",
        "report_audio_output_path",
        "report_audio_wav_path",
        "report_audio_seconds",
        "report_audio_audio_bytes",
        "report_audio_video_enabled",
        "report_audio_video_status",
        "report_audio_video_publisher_task_id",
        "report_audio_video_message",
        "report_audio_video_path",
        "report_audio_cover_path",
        "report_audio_landscape_keyframe_path",
        "report_audio_vertical_cover_path",
        "report_audio_podcast_cover_path",
        "report_audio_bilibili_cover_path",
        "final_script_chars",
    ):
        values[getattr(PublicationTask, name)] = getattr(projected, name)
    for name, value in fields.items():
        values[getattr(PublicationTask, name)] = value
    updated = (
        PublicationTask.query
        .filter(PublicationTask.id == str(parent_id), PublicationTask.status != "canceled")
        .update(values, synchronize_session=False)
    )
    db.session.commit()
    return updated


def _load_task_results(task_ids):
    ids = [str(task_id) for task_id in dict.fromkeys(task_ids or []) if task_id]
    if not ids:
        return {}
    rows = (
        PublicationTask.query.options(load_only(PublicationTask.id, PublicationTask.result))
        .filter(PublicationTask.id.in_(ids))
        .all()
    )
    return {row.id: row for row in rows}

TERMINAL_STATUSES = {"completed", "failed", "blocked_by_captcha", "canceled"}
CELERY_FAILURE_STATES = {"FAILURE", "REVOKED"}
CURRENT_STEP_MAX_LENGTH = 20
WORKFLOW_QUEUE_NAME = "report_audio_workflow"
LEADING_COVER_PUNCTUATION = "，,、。.;；:：!！?？)]）】》> "
TRAILING_COVER_PUNCTUATION = "，,、。.;；:：!！?？([（【《< "
MAX_COVER_TITLE_LINE_CHARS = 18
MAX_COVER_SUBTITLE_CHARS = 34


class ChildTaskStillRunning(RuntimeError):
    def __init__(self, label, child_id, status="", step=""):
        self.label = label
        self.child_id = child_id
        self.status = status
        self.step = step
        super().__init__(f"子任务仍在执行: {label} {child_id} ({status or '-'} / {step or '-'})")

BANK_ALIASES = [
    # GHHK is the stable filename prefix used by GF Securities Hong Kong
    # reports.  Keep these aliases before the broader "广发证券" match so a
    # Hong Kong report is not downgraded to the generic mainland label.
    ("GHHK", "香港广发"),
    ("GF Securities (Hong Kong)", "香港广发"),
    ("GF Securities Hong Kong", "香港广发"),
    ("广发证券（香港）", "香港广发"),
    ("广发证券(香港)", "香港广发"),
    ("香港广发", "香港广发"),
    ("GF Securities", "广发证券"),
    ("广发证券", "广发证券"),
    ("Morgan Stanley", "大摩"),
    ("摩根士丹利", "大摩"),
    ("大摩", "大摩"),
    ("J.P. Morgan", "摩根大通"),
    ("JP Morgan", "摩根大通"),
    ("JPMorgan", "摩根大通"),
    ("摩根大通", "摩根大通"),
    ("小摩", "摩根大通"),
    ("Goldman Sachs", "高盛"),
    ("高盛", "高盛"),
    ("Bernstein", "伯恩斯坦"),
    ("伯恩斯坦", "伯恩斯坦"),
    ("盛博", "伯恩斯坦"),
    ("UBS", "瑞银"),
    ("瑞银", "瑞银"),
    ("Citi", "花旗"),
    ("Citigroup", "花旗"),
    ("花旗", "花旗"),
    ("BofA", "美银"),
    ("Bank of America", "美银"),
    ("美国银行", "美银"),
    ("美银", "美银"),
    ("HSBC", "汇丰"),
    ("汇丰", "汇丰"),
    ("Nomura", "野村"),
    ("野村", "野村"),
    ("Jefferies", "杰富瑞"),
    ("杰富瑞", "杰富瑞"),
]

STEP_CODE_ALIASES = {
    "submit_gemini": "gemini_submit",
    "gemini_prepare_browser": "gemini_prepare",
    "gemini_open_page": "gemini_open",
    "gemini_upload_pdf": "gemini_upload",
    "gemini_submit_prompt": "gemini_prompt",
    "gemini_wait_response": "gemini_wait",
    "gemini_initial_response": "gemini_initial",
    "gemini_save_result": "gemini_save",
    "render_pdf_first_page": "pdf_preview",
    "submit_publisher_video": "publisher_submit",
    "publisher_video_done": "publisher_done",
    "skip_publisher_video": "publisher_skip",
}

DOUBAO_STEP_ALIASES = {
    "queued": "queued",
    "prepare_browser": "prepare",
    "clear_audio_cache": "clear",
    "submit_text": "submit",
    "wait_response": "wait",
    "start_reading": "reading",
    "capture_audio": "capture",
    "download_wav": "download",
    "save_file": "save",
    "done": "done",
    "failed": "failed",
    "canceled": "canceled",
}

PUBLISHER_STEP_ALIASES = {
    "queued": "queued",
    "init": "init",
    "prepare": "prepare",
    "synthesis": "synthesis",
    "thumbnail": "thumbnail",
    "upload": "upload",
    "upload_youtube": "youtube",
    "done": "done",
    "failed": "failed",
    "canceled": "canceled",
}


def _dispatch_requested_visual_workflows(source_task_id, payload):
    requested = []
    for value in (payload or {}).get("visual_scheme_keys") or []:
        key = str(value or "").strip().lower()
        if key and key not in requested:
            requested.append(key)
    if not requested:
        return {"requested": [], "items": []}

    from services.report_visual_scheme import visual_scheme
    from tasks.report_visual_plan import (
        _dispatch_auto_scene_images,
        enqueue_report_visual_plan_for_material,
    )

    source = (
        PublicationTask.query.options(load_only(
            PublicationTask.id,
            PublicationTask.feature,
            PublicationTask.user_id,
            PublicationTask.task_title,
            PublicationTask.final_script_chars,
            PublicationTask.report_audio_pdf_attachment_id,
            PublicationTask.report_audio_pdf_filename,
            PublicationTask.report_audio_pdf_bytes,
            PublicationTask.report_audio_pdf_topic_title,
        ))
        .filter(PublicationTask.id == str(source_task_id))
        .one_or_none()
    )
    if not source or int(source.final_script_chars or 0) <= 0:
        raise RuntimeError("基础文案尚未投影完成，不能投递视觉方案")

    source_snapshot = SimpleNamespace(
        id=str(source.id),
        feature=str(source.feature or FEATURE_NAME),
        user_id=str(source.user_id or ""),
        task_title=source.task_title,
        report_audio_pdf_attachment_id=source.report_audio_pdf_attachment_id,
        report_audio_pdf_filename=source.report_audio_pdf_filename,
        report_audio_pdf_bytes=source.report_audio_pdf_bytes,
        report_audio_pdf_topic_title=source.report_audio_pdf_topic_title,
    )
    summaries = []
    for key in requested:
        parent = None
        try:
            scheme = visual_scheme(key)
            parent = (
                PublicationTask.query.options(load_only(
                    PublicationTask.id,
                    PublicationTask.status,
                    PublicationTask.current_step,
                ))
                .filter(
                    PublicationTask.feature == "report_stick_figure_video_workflow",
                    PublicationTask.source_feature == source_snapshot.feature,
                    PublicationTask.source_task_id == source_snapshot.id,
                    PublicationTask.report_visual_scheme_key == scheme.key,
                    PublicationTask.status.in_(["pending", "processing", "completed"]),
                )
                .order_by(PublicationTask.created_at.desc())
                .first()
            )
            reused_parent = parent is not None
            if parent and parent.status == "completed":
                summaries.append({
                    "visual_scheme_key": scheme.key,
                    "workflow_job_id": str(parent.id),
                    "storyboard_job_id": "",
                    "status": "completed",
                    "reused_workflow": True,
                    "reused_storyboard": True,
                    "image_dispatch": None,
                })
                continue
            if not parent:
                parent = PublicationTask(
                    status="processing",
                    current_step="storyboard_queued",
                    progress=35,
                    params={
                        "feature": "report_stick_figure_video_workflow",
                        "user_id": source_snapshot.user_id,
                        "source_task_id": source_snapshot.id,
                        "source_feature": source_snapshot.feature,
                        "visual_scheme_key": scheme.key,
                        "report_stick_figure_video_workflow": {
                            "schema_version": "20260723_audio_shared_script_v1",
                            "input_mode": "report_audio_shared_script",
                            "visual_scheme_key": scheme.key,
                            "pdf": {
                                "attachment_id": source_snapshot.report_audio_pdf_attachment_id,
                                "filename": source_snapshot.report_audio_pdf_filename,
                                "bytes": source_snapshot.report_audio_pdf_bytes,
                                "topic_title": source_snapshot.report_audio_pdf_topic_title,
                            },
                        },
                    },
                    result={"schema_version": "20260723_audio_shared_script_v1"},
                )
                parent.task_title = (
                    source_snapshot.task_title
                    or source_snapshot.report_audio_pdf_topic_title
                    or source_snapshot.report_audio_pdf_filename
                    or f"研报 PDF 转{scheme.name}视频"
                )
                parent.report_visual_scheme_key = scheme.key
                parent.source_task_id = source_snapshot.id
                parent.source_feature = source_snapshot.feature
                parent.report_audio_pdf_attachment_id = source_snapshot.report_audio_pdf_attachment_id
                parent.report_audio_pdf_filename = source_snapshot.report_audio_pdf_filename
                parent.report_audio_pdf_bytes = source_snapshot.report_audio_pdf_bytes
                parent.report_audio_pdf_topic_title = source_snapshot.report_audio_pdf_topic_title
                db.session.add(parent)
                db.session.commit()

            dispatch = enqueue_report_visual_plan_for_material(
                source_snapshot.id,
                user_id=source_snapshot.user_id,
                parent_feature="report_stick_figure_video_workflow",
                parent_task_id=str(parent.id),
                visual_scheme_key=scheme.key,
            )
            storyboard_done = str(dispatch.get("status") or "pending") == "completed"
            now = datetime.utcnow()
            PublicationTask.query.filter(PublicationTask.id == str(parent.id)).update({
                PublicationTask.status: "processing",
                PublicationTask.current_step: "storyboard_done" if storyboard_done else "storyboard_queued",
                PublicationTask.progress: 60 if storyboard_done else 35,
                PublicationTask.waiting_child_id: dispatch.get("job_id"),
                PublicationTask.waiting_child_label: "标题与正文分镜",
                PublicationTask.waiting_child_status: "completed" if storyboard_done else "pending",
                PublicationTask.waiting_child_step: "done" if storyboard_done else "queued",
                PublicationTask.waiting_child_updated_at: now.isoformat() + "Z",
                PublicationTask.error_message: None,
                PublicationTask.updated_at: now,
            }, synchronize_session=False)
            db.session.commit()
            image_dispatch = None
            if storyboard_done:
                image_dispatch = _dispatch_auto_scene_images(
                    dispatch.get("job_id"),
                    str(parent.id),
                )
            summaries.append({
                "visual_scheme_key": scheme.key,
                "workflow_job_id": str(parent.id),
                "storyboard_job_id": str(dispatch.get("job_id") or ""),
                "status": "storyboard_done" if storyboard_done else "storyboard_queued",
                "reused_workflow": reused_parent,
                "reused_storyboard": bool(dispatch.get("cached")),
                "image_dispatch": image_dispatch,
            })
        except Exception as exc:
            db.session.rollback()
            if parent:
                PublicationTask.query.filter(PublicationTask.id == str(parent.id)).update({
                    PublicationTask.status: "failed",
                    PublicationTask.current_step: "storyboard_submit_failed",
                    PublicationTask.error_message: str(exc)[:4000],
                    PublicationTask.completed_at: datetime.utcnow(),
                    PublicationTask.updated_at: datetime.utcnow(),
                }, synchronize_session=False)
                db.session.commit()
            logger.exception(
                "Failed to dispatch visual workflow source=%s scheme=%s",
                source_snapshot.id,
                key,
            )
            summaries.append({
                "visual_scheme_key": key,
                "workflow_job_id": str(getattr(parent, "id", "") or ""),
                "status": "failed",
                "error": str(exc),
            })
    return {"requested": requested, "items": summaries}


@celery_app.task(bind=True, name="tasks.report_audio_workflow.process_job")
def process_report_audio_workflow_job(self, task_id):
    try:
        app = create_app(register_apis=False)
        with app.app_context():
            require_report_audio_workers_ready()
            start = _load_task(
                task_id,
                PublicationTask.celery_task_id,
                PublicationTask.status,
            )
            if not start:
                logger.error("Report audio workflow task not found before start: %s", task_id)
                return False
            request_id = str(getattr(self.request, "id", "") or "")
            current_celery_id = str(start.celery_task_id or "")
            if request_id and current_celery_id and request_id != current_celery_id:
                logger.info(
                    "Report audio workflow received stale delivery; task=%s request=%s current=%s",
                    task_id,
                    request_id,
                    current_celery_id,
                )
                return False
            if start.status == "completed":
                return True
            if start.status in {"failed", "blocked_by_captcha", "canceled"}:
                return False
            guard_publication_task_start(
                self,
                task_id,
                feature=FEATURE_NAME,
                queue=WORKFLOW_QUEUE_NAME,
            )
            return _process_job(task_id)
    except retry_exception_types():
        raise
    except Exception as exc:
        return _handle_report_audio_task_failure(self, task_id, exc)


@_workflow_result_scope
def _process_job(task_id):
    resume = _load_task(task_id, *TASK_RESUME_COLUMNS)
    if not resume:
        logger.error("Report audio workflow task not found: %s", task_id)
        return False
    if resume.status == "completed":
        logger.info("Report audio workflow already completed; skip duplicate delivery: %s", task_id)
        return True
    if resume.status == "canceled":
        return False

    cached = _cached_workflow_task(task_id)
    cached_waiting = cached.result.get("waiting_child") if cached and isinstance(cached.result, dict) else {}
    waiting_label = str(resume.waiting_child_label or "")
    waiting_child_id = str(resume.waiting_child_id or "")
    if (
        cached
        and waiting_label == "publisher_video"
        and waiting_child_id
        and cached_waiting.get("label") == waiting_label
        and str(cached_waiting.get("child_id") or "") == waiting_child_id
    ):
        try:
            _wait_child(task_id, waiting_child_id, waiting_label, start_progress=95, end_progress=99)
        except ChildTaskStillRunning as exc:
            db.session.rollback()
            _defer_parent_retry(task_id, exc)
            logger.info("Report audio workflow resumed with lightweight publisher wait: %s", exc)
            return True

    task = _load_task(task_id, *TASK_PAYLOAD_COLUMNS)
    if not task:
        logger.error("Report audio workflow task not found: %s", task_id)
        return False
    if not isinstance(task.params, dict) or task.params.get("feature") != FEATURE_NAME:
        logger.error("Invalid report audio workflow task params: %s", task_id)
        return False
    workflow_task_id = task.id
    workflow_created_at = task.created_at
    workflow_user_id = task.user_id or (task.params or {}).get("user_id")
    parent_ref = SimpleNamespace(
        id=workflow_task_id,
        created_at=workflow_created_at,
        params={"user_id": workflow_user_id},
    )

    payload = task.params.get("report_audio_workflow") or {}
    pdf = payload.get("pdf") or {}
    pdf_path = (payload.get("pdf_path") or pdf.get("path") or "").strip()
    if not pdf_path:
        _mark_failed(task, "PDF 文件不能为空")
        return False

    minimum_final_chars = _required_gemini_final_chars(payload)
    gem_v2_mode = resolve_gem_v2_mode(payload.get("gem_v2_mode"))
    if gem_v2_mode == "off":
        prompt = (payload.get("prompt") or DEFAULT_INITIAL_PROMPT).strip()
    else:
        prompt = build_gem_v2_trigger(
            pdf.get("filename") or Path(pdf_path).name,
            minimum_final_chars,
            output_mode=str(payload.get("gem_v2_output_mode") or "separate"),
            focus=str(payload.get("gem_v2_focus") or ""),
            allow_external_information=_truthy(
                payload.get("gem_v2_allow_external_information"),
                False,
            ),
        )
    pdf_evidence_enabled = (
        _truthy(payload.get("pdf_evidence_enabled"), True)
        if "pdf_evidence_enabled" in payload
        else True
    )
    if gem_v2_mode == "off":
        review_prompt = review_prompt_for_pdf_evidence(
            payload.get("review_prompt") or DEFAULT_REVIEW_PROMPT,
            enabled=pdf_evidence_enabled,
        )
        followups = [str(item).strip() for item in (payload.get("followups") or []) if str(item).strip()]
        if review_prompt and review_prompt not in followups:
            followups.append(review_prompt)
    else:
        followups = []

    result = dict(task.result or {})
    resume_waiting_child = isinstance(result.get("waiting_child"), dict)
    started_at = datetime.utcnow().isoformat() + "Z"
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
    active_step = task.current_step
    active_progress = task.progress
    flag_modified(task, "result")
    db.session.commit()
    _replace_cached_workflow_result(workflow_task_id, result)
    _merge_cached_workflow_fields(
        workflow_task_id,
        status="processing",
        current_step=active_step,
        progress=active_progress,
        error_message=None,
        completed_at=None,
    )

    try:
        _queue_notification(workflow_task_id, "report_audio_started", "研报 PDF 到语音编排已启动")
        retry_requested_at = result.get("retry_requested_at")
        structured = _valid_structured_result(
            result,
            minimum_chars=minimum_final_chars,
        )
        reused_material = None
        gemini_task = None
        active_gemini_task = None
        gemini_task_id = None
        gemini_result = None
        gemini_public_summary = None
        gemini_task_snapshot = None
        raw_final = ""
        initial_text = ""
        existing_gem_v2 = result.get("gem_v2") if isinstance(result.get("gem_v2"), dict) else {}
        direct_gem_v2_used = bool(
            structured
            and structured.get("schema_version") == GEM_V2_SCHEMA_VERSION
            and existing_gem_v2.get("route") == "direct"
            and existing_gem_v2.get("valid") is True
        )
        manual_gemini_result = _manual_gemini_payload(payload)
        script_material_result = result.get("script_material") if isinstance(result.get("script_material"), dict) else None
        audio_reuse_result = result.get("audio_reuse") if isinstance(result.get("audio_reuse"), dict) else None
        if (
            not structured
            and not manual_gemini_result
            and not _truthy(payload.get("force_gemini_regeneration"), False)
        ):
            reused_material = _reusable_material_for_pdf(
                pdf,
                exclude_task_id=workflow_task_id,
                minimum_chars=minimum_final_chars,
            )
        if reused_material:
            structured = reused_material["structured_result"]
            script_material_result = _script_material_reuse_payload(reused_material, pdf, structured)
            _clear_waiting_child(workflow_task_id, "gemini")
            _emit(workflow_task_id, "reuse_script_material", 58, "复用同 PDF 已完成基础文案物料，跳过 Gemini 解析")
            _update_result(workflow_task_id, {
                "structured_result": structured,
                "script_material": script_material_result,
                "gemini": None,
            })
        else:
            gemini_task = _completed_gemini_task(workflow_task_id, result)
            active_gemini_task = None if gemini_task else _active_gemini_task(workflow_task_id, result)
            gemini_task_id = _task_id(gemini_task)
            gemini_task_snapshot = _task_state_snapshot(gemini_task)
            if structured:
                _clear_waiting_child(workflow_task_id, "gemini")
                _emit(workflow_task_id, "reuse_structured_result", 58, "复用已有 Gemini 结构化结果")
                gemini_result = _task_result_payload(gemini_task_snapshot)
                if gemini_task:
                    _record_child(workflow_task_id, "gemini", gemini_task_snapshot)
                    _update_result(workflow_task_id, {"gemini": _task_public_summary(gemini_task_snapshot)})
            else:
                if manual_gemini_result:
                    _clear_waiting_child(workflow_task_id, "gemini")
                    gemini_result = manual_gemini_result
                    gemini_public_summary = _manual_gemini_public_summary(manual_gemini_result)
                    _emit(workflow_task_id, "manual_gemini_result", 52, "使用手动粘贴的 Gemini 返回值，跳过 Gemini 浏览器子任务")
                    _update_result(workflow_task_id, {"gemini": gemini_public_summary})
                    raw_final = (
                        gemini_result.get("final_response_text")
                        or gemini_result.get("response_text")
                        or ""
                    )
                    initial_text = gemini_result.get("initial_response_text") or ""
                    final_chars = len(_plain_text(raw_final))
                    min_final_chars = _required_gemini_final_chars(payload)
                    if final_chars < min_final_chars:
                        raise RuntimeError(
                            f"手动 Gemini 返回内容过短: {final_chars} 字，低于 {min_final_chars} 字，停止进入 DeepSeek 结构化"
                        )
                    structured, gem_v2_diagnostics, direct_gem_v2_used = _structure_gemini_response(
                        raw_final,
                        initial_text=initial_text,
                        minimum_script_chars=minimum_final_chars,
                        gem_v2_mode=gem_v2_mode,
                        allow_external_information=_truthy(
                            payload.get("gem_v2_allow_external_information"),
                            False,
                        ),
                        pdf=pdf,
                        pdf_path=pdf_path,
                        metadata={
                            "workflow_task_id": workflow_task_id,
                            "gemini_source": "manual_input",
                            "pdf_filename": pdf.get("filename"),
                        },
                    )
                    if gem_v2_diagnostics:
                        _update_result(workflow_task_id, {"gem_v2": gem_v2_diagnostics})
                elif gemini_task:
                    _clear_waiting_child(workflow_task_id, "gemini")
                    _emit(workflow_task_id, "reuse_gemini_result", 52, "复用已完成的 Gemini 子任务")
                    _record_child(workflow_task_id, "gemini", gemini_task_snapshot)
                    _update_result(workflow_task_id, {"gemini": _task_public_summary(gemini_task_snapshot)})
                elif active_gemini_task:
                    active_gemini_snapshot = _task_state_snapshot(active_gemini_task)
                    active_gemini_task_id = _task_id(active_gemini_snapshot)
                    if not active_gemini_task_id:
                        raise RuntimeError("已有 Gemini 子任务缺少任务 ID")
                    _emit(workflow_task_id, "gemini_wait_existing", 10, "继续等待已提交的研报 PDF Gem 解析子任务")
                    _record_child(workflow_task_id, "gemini", active_gemini_snapshot)
                    gemini_task = _wait_child(workflow_task_id, active_gemini_task_id, "gemini", start_progress=10, end_progress=52)
                    gemini_task_id = _task_id(gemini_task) or active_gemini_task_id
                    gemini_task_snapshot = _task_state_snapshot(gemini_task)
                    if (gemini_task_snapshot or {}).get("status") != "completed":
                        raise RuntimeError(f"Gemini 子任务失败: {_child_error_message(gemini_task)}")
                    _update_result(workflow_task_id, {"gemini": _task_public_summary(gemini_task)})
                    _queue_notification(
                        workflow_task_id,
                        "report_audio_gemini_completed",
                        "研报 PDF Gem 结构化解析已完成"
                        if gem_v2_mode != "off"
                        else "研报 PDF Gem 第一轮解析和自审追问已完成",
                    )
                else:
                    _queue_notification(workflow_task_id, "report_audio_gemini_submitted", "研报 PDF Gem 解析子任务已入队")
                    gemini_task = _run_gemini_task_with_retries(
                        parent_ref,
                        pdf=pdf,
                        pdf_path=pdf_path,
                        prompt=prompt,
                        followups=followups,
                        payload=payload,
                        start_progress=10,
                        end_progress=52,
                    )
                    gemini_task_id = _task_id(gemini_task)
                    gemini_task_snapshot = _task_state_snapshot(gemini_task)
                    _update_result(workflow_task_id, {"gemini": _task_public_summary(gemini_task)})
                    _queue_notification(
                        workflow_task_id,
                        "report_audio_gemini_completed",
                        "研报 PDF Gem 结构化解析已完成"
                        if gem_v2_mode != "off"
                        else "研报 PDF Gem 第一轮解析和自审追问已完成",
                    )

                if not manual_gemini_result:
                    fresh_gemini_task = _load_task(gemini_task_id, *TASK_CHILD_COLUMNS) if gemini_task_id else None
                    gemini_result = _task_result_payload(fresh_gemini_task) or _task_result_payload(gemini_task_snapshot)
                    raw_final = (
                        gemini_result.get("final_response_text")
                        or gemini_result.get("response_text")
                        or ""
                    )
                    initial_text = gemini_result.get("initial_response_text") or ""
                    if not raw_final:
                        raise RuntimeError("Gemini 子任务没有返回 final_response_text")
                    final_chars = len(_plain_text(raw_final))
                    min_final_chars = _required_gemini_final_chars(payload)
                    if final_chars < min_final_chars:
                        raise RuntimeError(
                            f"Gemini 子任务返回内容过短: {final_chars} 字，低于 {min_final_chars} 字，疑似未抓到有效研报解析结果"
                        )

                    structured, gem_v2_diagnostics, direct_gem_v2_used = _structure_gemini_response(
                        raw_final,
                        initial_text=initial_text,
                        minimum_script_chars=minimum_final_chars,
                        gem_v2_mode=gem_v2_mode,
                        allow_external_information=_truthy(
                            payload.get("gem_v2_allow_external_information"),
                            False,
                        ),
                        pdf=pdf,
                        pdf_path=pdf_path,
                        metadata={
                            "workflow_task_id": workflow_task_id,
                            "gemini_task_id": gemini_task_id,
                            "pdf_filename": pdf.get("filename"),
                        },
                    )
                    if gem_v2_diagnostics:
                        _update_result(workflow_task_id, {"gem_v2": gem_v2_diagnostics})
        if direct_gem_v2_used:
            _emit(workflow_task_id, "use_gem_v2_titles", 60, "Gem V2 结构与标题通过校验，跳过 DeepSeek 标题复核")
        else:
            _emit(workflow_task_id, "review_cover_titles", 60, "生成标题候选并进行独立评审")
            title_source_text = raw_final or "\n".join(filter(None, [
                str((structured or {}).get("summary") or ""),
                str((structured or {}).get("final_script_for_tts") or ""),
            ]))
            structured = review_report_cover_titles(
                structured or {},
                source_text=title_source_text,
                pdf_filename=pdf.get("filename") or Path(pdf_path).name,
                pdf_topic_title=pdf.get("topic_title") or "",
            )

        # Always sanitize at the final TTS boundary as well. This also repairs
        # reusable/cached structured material created before a cleaner existed.
        structured = dict(structured or {})
        structured["final_script_for_tts"] = clean_final_script_for_tts(
            structured.get("final_script_for_tts") or ""
        )
        resolved_gemini_summary = (
            gemini_public_summary
            or _task_public_summary(gemini_task)
            or result.get("gemini")
        )
        if raw_final and pdf_evidence_enabled:
            try:
                release_db_session_quietly()
                evidence_output = build_and_store_evidence(
                    source_task_id=workflow_task_id,
                    pdf_path=pdf_path,
                    final_script=structured.get("final_script_for_tts") or "",
                    raw_gemini_text=raw_final,
                    pdf_attachment_id=pdf.get("attachment_id"),
                    pdf_filename=pdf.get("filename"),
                    gemini_task_id=gemini_task_id,
                )
                _update_result(workflow_task_id, {
                    "pdf_evidence": {
                        "status": evidence_output.status,
                        "output_id": evidence_output.id,
                        "claim_count": int(evidence_output.claim_count or 0),
                        "verified_claim_count": int(evidence_output.verified_claim_count or 0),
                    },
                })
            except Exception as exc:
                db.session.rollback()
                logger.warning("PDF evidence shadow build failed task=%s error=%s", workflow_task_id, exc)
                try:
                    _update_result(workflow_task_id, {
                        "pdf_evidence": {
                            "status": "failed",
                            "error": str(exc)[:1000],
                        },
                    })
                except Exception:
                    db.session.rollback()
                    logger.exception("Failed to persist PDF evidence failure task=%s", workflow_task_id)
        elif not pdf_evidence_enabled:
            _update_result(workflow_task_id, {
                "pdf_evidence": {
                    "status": "disabled",
                    "enabled": False,
                },
            })
        chunks = split_tts_text(structured.get("final_script_for_tts") or "")
        if not chunks:
            raise RuntimeError("结构化结果没有可朗读正文")
        tts_chars = len(_plain_text(structured.get("final_script_for_tts") or ""))
        min_final_chars = _required_gemini_final_chars(payload)
        if tts_chars < min_final_chars:
            raise RuntimeError(
                f"结构化可朗读正文过短: {tts_chars} 字，低于 {min_final_chars} 字，停止进入豆包浏览器朗读"
            )

        structured_update = {
            "gemini": resolved_gemini_summary,
            "structured_result": structured,
            "tts_text": {
                "chars": len(structured.get("final_script_for_tts") or ""),
                "chunks": len(chunks),
            },
        }
        if script_material_result:
            structured_update["script_material"] = script_material_result
        _update_result(workflow_task_id, structured_update)
        try:
            visual_workflows = _dispatch_requested_visual_workflows(
                workflow_task_id,
                payload,
            )
        except Exception as exc:
            logger.exception(
                "Failed to initialize requested visual workflows for audio job %s",
                workflow_task_id,
            )
            visual_workflows = {
                "requested": list(payload.get("visual_scheme_keys") or []),
                "items": [],
                "status": "failed",
                "error": str(exc),
            }
        if visual_workflows.get("requested"):
            _update_result(workflow_task_id, {"visual_workflows": visual_workflows})
            queued_count = sum(
                1
                for item in visual_workflows.get("items") or []
                if item.get("status") in {"storyboard_queued", "storyboard_done", "completed"}
            )
            _queue_notification(
                workflow_task_id,
                "report_audio_visual_workflows_dispatched",
                f"共享文案已投递 {queued_count}/{len(visual_workflows.get('requested') or [])} 个视觉方案",
            )
        _queue_notification(
            workflow_task_id,
            "report_audio_normalized",
            f"结构化整理完成，可朗读正文 {len(structured.get('final_script_for_tts') or '')} 字，拆分 {len(chunks)} 段",
        )
        if _script_material_only(payload):
            _emit(workflow_task_id, "script_material_done", 100, "研报基础口播文案物料已生成，跳过豆包朗读和视频合成")
            _complete_script_material_task(
                workflow_task_id,
                workflow_created_at,
                structured=structured,
                pdf=pdf,
                gemini_task_id=gemini_task_id,
                gemini_result=gemini_result,
                chunks=chunks,
                tts_chars=tts_chars,
                script_material_reuse=script_material_result,
            )
            _queue_notification(workflow_task_id, "report_audio_script_material_completed", "研报基础口播文案物料已生成")
            logger.info("Report audio script material completed: %s", task_id)
            return True

        tts_provider = str(payload.get("tts_provider") or "doubao_browser").strip().lower()
        reusable_audio = _reusable_audio_for_pdf(
            pdf,
            structured,
            exclude_task_id=workflow_task_id,
            tts_provider=tts_provider,
            tts_voice=str(payload.get("tts_voice") or "zh_female_mizai_uranus_bigtts"),
        )
        if reusable_audio:
            tts_task_ids = []
            audio = reusable_audio["audio"]
            audio_reuse_result = {
                "status": "completed",
                "mode": "reused_audio_material",
                "source_task_id": reusable_audio.get("source_task_id"),
                "source_feature": reusable_audio.get("source_feature"),
                "wav_path": audio.get("wav_path"),
                "seconds": audio.get("seconds"),
                "bytes": audio.get("bytes"),
                "completed_at": datetime.utcnow().isoformat() + "Z",
            }
            _emit(workflow_task_id, "reuse_audio_material", 92, "复用同 PDF 已完成音频，跳过豆包朗读与音频合并")
            _update_result(workflow_task_id, {
                "audio": audio,
                "audio_reuse": audio_reuse_result,
                "doubao_browser_tts": [],
            })
            _queue_notification(workflow_task_id, "report_audio_audio_reused", "已复用同 PDF 历史音频，直接进入视频合成")
        elif tts_provider == "doubao_api":
            tts_task_ids = []
            _emit(workflow_task_id, "doubao_api_tts", 88, "使用豆包 API TTS 合成音频")
            audio = synthesize_doubao_api_chunks(
                chunks,
                audio_path_for_job(workflow_task_id, workflow_created_at),
                voice_type=str(payload.get("tts_voice") or "zh_female_mizai_uranus_bigtts"),
            )
            audio = doubao_service.validate_audio_duration_for_text(
                audio,
                structured.get("final_script_for_tts") or "",
            )
            audio["provider"] = "doubao_api"
            _update_result(workflow_task_id, {"audio": audio, "doubao_browser_tts": []})
            _queue_notification(workflow_task_id, "report_audio_doubao_api_completed", f"豆包 API TTS 完成，共生成 {len(chunks)} 段音频")
        else:
            doubao_retry_limit = max(0, min(_int_env("REPORT_AUDIO_DOUBAO_RETRY_LIMIT", 2), 5))
            tts_task_ids, wav_paths = _run_doubao_tts_chunks(
                parent_ref,
                workflow_task_id,
                chunks,
                payload,
                retry_requested_at=retry_requested_at,
                retry_limit=doubao_retry_limit,
            )
            _update_result(workflow_task_id, {"doubao_browser_tts": _task_public_summaries_by_ids(tts_task_ids)})
            _queue_notification(workflow_task_id, "report_audio_doubao_completed", f"豆包浏览器朗读完成，共生成 {len(wav_paths)} 段 WAV")

            _emit(workflow_task_id, "combine_audio", 92, "合并豆包浏览器朗读音频")
            audio = combine_wav_files(wav_paths, audio_path_for_job(workflow_task_id, workflow_created_at))
            audio = doubao_service.validate_audio_duration_for_text(
                audio,
                structured.get("final_script_for_tts") or "",
            )
            audio["provider"] = "doubao_browser"
            _update_result(workflow_task_id, {"audio": audio})
        publisher_task = None
        publisher_task_id = None
        video_result = None
        if _video_stage_enabled(payload):
            video_render_mode = _video_render_mode(payload)
            _emit(workflow_task_id, "render_pdf_first_page", 94, "生成 PDF 首屏截图")
            pdf_screenshot = render_pdf_first_page_image(
                pdf_path,
                pdf_first_page_image_path_for_job(workflow_task_id, workflow_created_at),
            )
            _update_result(workflow_task_id, {"pdf_first_page_image": pdf_screenshot})

            if video_render_mode == "keyframes":
                _emit(workflow_task_id, "generate_video_keyframes", 96, "生成视频关键帧/封皮预览，跳过 MP4 合成")
                video_result = _generate_report_audio_keyframes(
                    workflow_task_id,
                    workflow_created_at,
                    structured=structured,
                    pdf=pdf,
                    audio=audio,
                    pdf_screenshot=pdf_screenshot,
                    payload=payload,
                )
                _update_result(workflow_task_id, {"video": video_result})
                _emit(workflow_task_id, "video_keyframes_done", 99, "视频关键帧预览已生成")
            else:
                current_result = _fresh_result(workflow_task_id)
                publisher_task = _existing_child_from_result(current_result, "publisher_video")
                expected_preview_seconds = (
                    _video_preview_seconds(payload.get("video") if isinstance(payload.get("video"), dict) else {})
                    if video_render_mode == "preview_10s"
                    else 0
                )
                if publisher_task and (
                    _publisher_child_render_mode(publisher_task) != video_render_mode
                    or _publisher_child_preview_seconds(publisher_task) != expected_preview_seconds
                ):
                    publisher_task = None
                if publisher_task and publisher_task.status not in {"failed", "canceled"}:
                    _emit(workflow_task_id, "publisher_video_wait_existing", 95, "继续等待已提交的视频合成子任务")
                else:
                    step_message = "提交 10 秒短视频预览子任务" if video_render_mode == "preview_10s" else "提交视频合成子任务"
                    _emit(workflow_task_id, "submit_publisher_video", 95, step_message)
                    publisher_task = _create_publisher_video_task(
                        parent_ref,
                        audio=audio,
                        structured=structured,
                        pdf=pdf,
                        pdf_screenshot=pdf_screenshot,
                        payload=payload,
                    )
                publisher_task_id = _task_id(publisher_task)
                if publisher_task_id:
                    publisher_task = _load_task(publisher_task_id, *TASK_PUBLISHER_MATCH_COLUMNS) or publisher_task
                publisher_task_snapshot = _task_state_snapshot(publisher_task)
                _record_child(workflow_task_id, "publisher_video", publisher_task_snapshot)
                publisher_task_id = _task_id(publisher_task_snapshot) or publisher_task_id
                if not publisher_task_id:
                    raise RuntimeError("视频合成子任务缺少任务 ID")
                publisher_task = _wait_child(workflow_task_id, publisher_task_id, "publisher_video", start_progress=95, end_progress=99)
                publisher_snapshot = _task_state_snapshot(publisher_task) or {}
                if publisher_snapshot.get("status") != "completed":
                    publisher_result = publisher_snapshot.get("result") if isinstance(publisher_snapshot.get("result"), dict) else {}
                    raise RuntimeError(f"视频合成子任务失败: {publisher_snapshot.get('error_message') or publisher_result.get('error')}")
                publisher_task_id = _task_id(publisher_snapshot) or publisher_task_id
                video_result = _publisher_result_summary(publisher_task, pdf_screenshot)
                _update_result(workflow_task_id, {
                    "publisher_video": _task_public_summary_by_id(publisher_task_id),
                    "video": video_result,
                })
                done_message = "10 秒短视频预览完成" if video_render_mode == "preview_10s" else "视频合成完成"
                _emit(workflow_task_id, "publisher_video_done", 99, done_message)
        else:
            video_result = {"enabled": False, "status": "skipped", "message": "未勾选生成视频"}
            _update_result(workflow_task_id, {"video": video_result})
            _emit(workflow_task_id, "skip_publisher_video", 94, "跳过视频合成")

        completion_contract = _report_video_completion_contract(video_result, audio, payload)
        _update_result(workflow_task_id, {"completion_contract": completion_contract})
        if not completion_contract["ready"]:
            missing = ", ".join(completion_contract.get("missing") or []) or "unknown"
            raise RuntimeError(f"成片未通过完整性验收，缺失或无效输出: {missing}")

        output_path = storage_path_for_job(workflow_task_id, workflow_created_at)
        final_payload = {
            "workflow_task_id": workflow_task_id,
            "gemini_task_id": gemini_task_id,
            "doubao_browser_task_ids": tts_task_ids,
            "publisher_task_id": publisher_task_id,
            "structured_result": structured,
            "script_material": script_material_result,
            "audio": audio,
            "audio_reuse": audio_reuse_result,
            "video": video_result,
            "completion_contract": completion_contract,
            "gemini": gemini_result,
            "completed_at": datetime.utcnow().isoformat() + "Z",
        }
        with open(output_path, "w", encoding="utf-8") as file:
            import json

            json.dump(final_payload, file, ensure_ascii=False, indent=2)

        final_update = {
            "gemini": _task_public_summary_by_id(gemini_task_id) if gemini_task_id else gemini_public_summary,
            "doubao_browser_tts": _task_public_summaries_by_ids(tts_task_ids),
            "publisher_video": _task_public_summary_by_id(publisher_task_id) if publisher_task_id else None,
            "structured_result": structured,
            "script_material": script_material_result,
            "audio": audio,
            "audio_reuse": audio_reuse_result,
            "video": video_result,
            "completion_contract": completion_contract,
            "output_path": output_path,
            "completed_at": datetime.utcnow().isoformat() + "Z",
        }
        current_result = _fresh_result(workflow_task_id)
        final_update["gemini"] = final_update["gemini"] or current_result.get("gemini")
        _update_result(workflow_task_id, final_update)
        completed_at = datetime.utcnow()
        video_payload = video_result if isinstance(video_result, dict) else {}
        keyframes_payload = video_payload.get("keyframes") if isinstance(video_payload.get("keyframes"), dict) else {}
        landscape_payload = keyframes_payload.get("landscape_cover") if isinstance(keyframes_payload.get("landscape_cover"), dict) else {}
        audio_payload = audio if isinstance(audio, dict) else {}
        structured_payload = structured if isinstance(structured, dict) else {}
        updated = (
            PublicationTask.query
            .filter(PublicationTask.id == str(workflow_task_id), PublicationTask.status != "canceled")
            .update(
                {
                    PublicationTask.status: "completed",
                    PublicationTask.current_step: "done",
                    PublicationTask.progress: 100,
                    PublicationTask.completed_at: completed_at,
                    PublicationTask.error_message: None,
                    PublicationTask.report_audio_completed_at: final_update["completed_at"],
                    PublicationTask.report_audio_output_path: output_path,
                    PublicationTask.report_audio_wav_path: audio_payload.get("wav_path"),
                    PublicationTask.report_audio_seconds: str(audio_payload.get("seconds")) if audio_payload.get("seconds") is not None else None,
                    PublicationTask.report_audio_audio_bytes: str(audio_payload.get("bytes")) if audio_payload.get("bytes") is not None else None,
                    PublicationTask.report_audio_video_enabled: _truthy(video_payload.get("enabled"), False),
                    PublicationTask.report_audio_video_status: video_payload.get("status"),
                    PublicationTask.report_audio_video_publisher_task_id: video_payload.get("publisher_task_id"),
                    PublicationTask.report_audio_video_message: video_payload.get("message"),
                    PublicationTask.report_audio_video_path: video_payload.get("video_path"),
                    PublicationTask.report_audio_cover_path: video_payload.get("cover_path"),
                    PublicationTask.report_audio_landscape_keyframe_path: landscape_payload.get("path"),
                    PublicationTask.report_audio_cover_title: structured_payload.get("cover_title"),
                    PublicationTask.report_audio_cover_subtitle: structured_payload.get("cover_subtitle") or video_payload.get("cover_subtitle"),
                    PublicationTask.report_audio_structured_title: structured_payload.get("title"),
                    PublicationTask.final_script_chars: len(str(structured_payload.get("final_script_for_tts") or "")),
                    PublicationTask.updated_at: completed_at,
                },
                synchronize_session=False,
            )
        )
        if not updated:
            raise RuntimeError("编排任务不存在或已取消")
        db.session.commit()
        _resume_auto_visual_workflows_after_audio(workflow_task_id)
        _merge_cached_workflow_fields(
            workflow_task_id,
            status="completed",
            current_step="done",
            progress=100,
            completed_at=completed_at,
            error_message=None,
        )
        _queue_notification(workflow_task_id, "report_audio_completed", "研报转语音编排完成，最终 WAV 和 JSON 已生成")
        logger.info("Report audio workflow completed: %s", task_id)
        return True
    except ChildTaskStillRunning as exc:
        db.session.rollback()
        _defer_parent_retry(task_id, exc)
        logger.info("Report audio workflow deferred: %s", exc)
        return True
    except Exception as exc:
        db.session.rollback()
        if _is_retryable_db_error(exc):
            release_db_session_quietly()
            raise
        task = _load_task(task_id, *TASK_STATE_COLUMNS)
        notification_queued = False
        if task:
            _mark_failed(task, str(exc))
            notification_queued = _queue_notification(task_id, "report_audio_failed", str(exc))
        if not notification_queued:
            _send_direct_report_audio_failure_email(
                task_id=task_id,
                error=exc,
                persisted=bool(task),
                notification_queued=notification_queued,
            )
        logger.error("Report audio workflow failed: %s", exc, exc_info=True)
        return False


def _resume_auto_visual_workflows_after_audio(audio_task_id: str) -> None:
    """Bind a newly completed automatic WAV to ready whiteboard workflows.

    Scene-image completion may happen before its material-owned audio handoff
    finishes.  In that ordering the original image continuation correctly
    reports ``whole_audio_not_ready`` but has no later wake-up.  Resume only
    handoff-owned workflows and only when every body image is complete, so a
    normal/manual audio task can never unexpectedly enqueue a render.
    """
    task = _load_task(
        audio_task_id,
        PublicationTask.id,
        PublicationTask.params,
        PublicationTask.source_task_id,
    )
    if not task:
        return
    task_params = task.params if isinstance(task.params, dict) else {}
    payload = (
        task_params.get("report_audio_workflow")
        if isinstance(task_params.get("report_audio_workflow"), dict)
        else {}
    )
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    if str(metadata.get("source") or "") != "zsxq_auto_script_material_audio_handoff":
        return
    material_task_id = str(getattr(task, "source_task_id", "") or "").strip()
    if not material_task_id:
        return

    from api.report_stick_figure_video_workflow import (
        SCENE_PARENT_COLUMNS,
        _auto_enqueue_whole_audio_alignment,
        _ensure_scene_assets,
        _scene_parent_snapshot,
        _storyboard_for_workflow,
    )

    parents = (
        PublicationTask.query.options(load_only(*SCENE_PARENT_COLUMNS))
        .filter(
            PublicationTask.feature == "report_stick_figure_video_workflow",
            PublicationTask.source_feature == "report_script_material_workflow",
            PublicationTask.source_task_id == material_task_id,
            PublicationTask.status != "canceled",
        )
        .all()
    )
    for parent in parents:
        try:
            parent_snapshot = _scene_parent_snapshot(parent)
            storyboard = _storyboard_for_workflow(parent_snapshot)
            if not storyboard:
                continue
            storyboard, assets = _ensure_scene_assets(parent_snapshot, force_refresh=True)
            body_assets = [asset for asset in assets if int(asset.sequence or 0) >= 1]
            if not body_assets or any(
                str(asset.image_status or "") != "completed" or not asset.image_path
                for asset in body_assets
            ):
                continue
            result = _auto_enqueue_whole_audio_alignment(parent_snapshot, storyboard)
            logger.info(
                "Auto visual audio resume: audio=%s workflow=%s result=%s",
                audio_task_id,
                parent_snapshot.id,
                result,
            )
        except Exception as exc:
            db.session.rollback()
            logger.warning(
                "Auto visual audio resume skipped: audio=%s workflow=%s error=%s",
                audio_task_id,
                getattr(parent, "id", ""),
                exc,
            )


def _run_gemini_task_with_retries(parent, *, pdf, pdf_path, prompt, followups, payload, start_progress, end_progress):
    retry_limit = _retry_limit(GEMINI_RETRY_LIMIT_ENV_NAME, DEFAULT_GEMINI_RETRY_LIMIT)
    max_attempts = retry_limit + 1
    last_error = None
    last_task = None

    for attempt in range(1, max_attempts + 1):
        retry_suffix = "" if attempt == 1 else f"（自动重试 {attempt - 1}/{retry_limit}）"
        _emit(parent.id, "submit_gemini", 8, f"创建研报 PDF Gem 解析子任务{retry_suffix}")
        gemini_task = _create_gemini_task(
            parent,
            pdf,
            pdf_path,
            prompt,
            followups,
            payload,
            attempt=attempt,
            max_attempts=max_attempts,
        )
        gemini_task_id = _task_id(gemini_task)
        if not gemini_task_id:
            raise RuntimeError("Gemini 子任务缺少任务 ID")
        gemini_task_snapshot = _task_state_snapshot(gemini_task)
        last_task = gemini_task_snapshot
        _record_child(parent.id, "gemini", gemini_task_snapshot)
        _record_child_attempt(parent.id, "gemini", gemini_task_snapshot, attempt, max_attempts, "running")

        try:
            gemini_task = _wait_child(
                parent.id,
                gemini_task_id,
                "gemini",
                start_progress=start_progress,
                end_progress=end_progress,
            )
        except ChildTaskStillRunning:
            latest = _load_task(gemini_task_id, *TASK_CHILD_COLUMNS)
            _record_child(parent.id, "gemini", _task_state_snapshot(latest) or gemini_task_snapshot)
            raise
        except Exception as exc:
            if _is_retryable_db_error(exc):
                raise
            last_error = str(exc)
            latest = _load_task(gemini_task_id, *TASK_CHILD_COLUMNS)
            latest_snapshot = _task_state_snapshot(latest) or gemini_task_snapshot
            if latest and latest_snapshot and latest_snapshot.get("status") not in TERMINAL_STATUSES:
                _revoke_child_task(latest)
                _mark_child_failed(latest, last_error)
                latest = _load_task(gemini_task_id, *TASK_CHILD_COLUMNS)
                latest_snapshot = _task_state_snapshot(latest) or dict(latest_snapshot, status="failed", error=last_error)
            last_task = latest_snapshot
            _record_child_attempt(parent.id, "gemini", latest_snapshot, attempt, max_attempts, "failed", error=last_error)
        else:
            gemini_task_snapshot = _task_state_snapshot(gemini_task)
            last_task = gemini_task_snapshot
            _record_child(parent.id, "gemini", gemini_task_snapshot)
            if gemini_task_snapshot and gemini_task_snapshot.get("status") == "completed":
                _record_child_attempt(parent.id, "gemini", gemini_task_snapshot, attempt, max_attempts, "completed")
                return gemini_task_snapshot
            last_error = _child_error_message(gemini_task_snapshot)
            _record_child_attempt(parent.id, "gemini", gemini_task_snapshot, attempt, max_attempts, "failed", error=last_error)

        if attempt < max_attempts and _is_retryable_child_error("gemini", last_error):
            _emit(
                parent.id,
                "gemini_retry",
                max(start_progress, 10),
                f"Gemini 子任务失败，准备自动重试 {attempt}/{retry_limit}: {_compact_error(last_error)}",
            )
            continue
        break

    if last_task:
        _record_child(parent.id, "gemini", last_task)
    raise RuntimeError(f"Gemini 子任务失败: {last_error or '未知错误'}")


def _structure_gemini_response(
    raw_final,
    *,
    initial_text,
    minimum_script_chars,
    gem_v2_mode,
    allow_external_information,
    pdf,
    pdf_path,
    metadata,
):
    """Choose the direct V2 result or the unchanged DeepSeek compatibility path."""

    mode = resolve_gem_v2_mode(gem_v2_mode)
    diagnostics = None
    if mode != "off":
        validation = validate_gem_v2_response(
            raw_final,
            minimum_script_chars=minimum_script_chars,
            expected_pdf_filename=(pdf or {}).get("filename") or Path(pdf_path).name,
            actual_pdf_page_count=_actual_pdf_page_count(pdf_path, pdf),
            allow_external_information=bool(allow_external_information),
        )
        diagnostics = validation.diagnostics()
        diagnostics["mode"] = mode
        if mode == "direct" and validation.valid:
            diagnostics["route"] = "direct"
            return to_report_audio_structured_result(validation), diagnostics, True
        diagnostics["route"] = "shadow_deepseek" if mode == "shadow" else "deepseek_fallback"

    release_db_session_quietly()
    structured = normalize_for_tts(
        raw_final,
        initial_text=initial_text,
        minimum_script_chars=minimum_script_chars,
        metadata=metadata,
    )
    return structured, diagnostics, False


def _actual_pdf_page_count(pdf_path, pdf=None):
    for value in (
        (pdf or {}).get("actual_pdf_page_count"),
        (pdf or {}).get("page_count"),
    ):
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = 0
        if parsed > 0:
            return parsed
    source = Path(str(pdf_path or ""))
    if not source.is_file():
        return None
    try:
        import fitz  # type: ignore

        with fitz.open(str(source)) as document:
            return int(document.page_count or 0) or None
    except Exception:
        return None


def _create_gemini_task(parent, pdf, pdf_path, prompt, followups, payload, *, attempt=1, max_attempts=1):
    retry_index = max(0, int(attempt or 1) - 1)
    metadata = {
        "source": FEATURE_NAME,
        "parent_task_id": parent.id,
        "attempt": int(attempt or 1),
        "max_attempts": int(max_attempts or 1),
        "retry_index": retry_index,
    }
    gemini_payload = {
        "prompt": prompt,
        "followups": followups,
        "url": (payload.get("gemini_url") or payload.get("url") or "").strip(),
        "prefer_pro_model": _truthy(payload.get("gemini_prefer_pro_model"), True),
        "require_pro_model": _truthy(payload.get("gemini_require_pro_model"), False),
        "minimum_final_response_chars": _required_gemini_final_chars(payload),
        "metadata": metadata,
        "pdf": pdf,
        "pdf_path": pdf_path,
    }
    gem_v2_mode = resolve_gem_v2_mode(payload.get("gem_v2_mode"))
    if gem_v2_mode != "off":
        gemini_payload.update({
            "response_contract": GEM_V2_SCHEMA_VERSION,
            "contract_expected_pdf_filename": (pdf or {}).get("filename") or Path(pdf_path).name,
            "contract_minimum_script_chars": _required_gemini_final_chars(payload),
            "contract_max_repairs": 1,
            "contract_allow_external_information": _truthy(
                payload.get("gem_v2_allow_external_information"),
                False,
            ),
        })
    child = PublicationTask(
        status="pending",
        current_step="queued",
        progress=0,
        params={
            "feature": gemini_service.FEATURE_NAME,
            "user_id": parent.params.get("user_id"),
            "parent_feature": FEATURE_NAME,
            "parent_task_id": parent.id,
            "report_pdf_gemini": gemini_payload,
        },
        result={
            "metadata": metadata,
            "response_text": None,
            "turns": [],
            "pdf": pdf,
            "logs": [],
        },
    )
    db.session.add(child)
    db.session.flush()
    child_id = child.id
    child_snapshot = SimpleNamespace(**_task_state_snapshot(child))
    db.session.commit()
    async_result = apply_async_after_db_release(
        process_report_pdf_gemini_job,
        args=[child_id],
        queue=gemini_service.QUEUE_NAME,
        retry=False,
    )
    _persist_child_celery_task_id(child_id, async_result.id)
    child_snapshot.celery_task_id = async_result.id
    child_snapshot.celery_id = async_result.id
    return child_snapshot


def _manual_gemini_payload(payload):
    manual = (payload or {}).get("manual_gemini")
    if not manual:
        return None
    if isinstance(manual, str):
        final_text = manual.strip()
        initial_text = ""
        provided_at = None
    elif isinstance(manual, dict):
        final_text = str(
            manual.get("final_response_text")
            or manual.get("response_text")
            or manual.get("text")
            or manual.get("content")
            or ""
        ).strip()
        initial_text = str(manual.get("initial_response_text") or manual.get("initial_text") or "").strip()
        provided_at = manual.get("provided_at")
    else:
        return None
    if not final_text:
        return None
    return {
        "source": "manual_input",
        "initial_response_text": initial_text,
        "final_response_text": final_text,
        "chars": len(final_text),
        "provided_at": provided_at or datetime.utcnow().isoformat() + "Z",
    }


def _manual_gemini_public_summary(manual):
    if not manual:
        return None
    return {
        "job_id": "manual-gemini",
        "celery_id": None,
        "status": "completed",
        "step": "manual_input",
        "progress": 100,
        "error": None,
        "metadata": {
            "source": "manual_input",
            "chars": int(manual.get("chars") or len(manual.get("final_response_text") or "")),
        },
        "created_at": manual.get("provided_at"),
        "completed_at": manual.get("provided_at"),
    }


def _valid_structured_result(result, *, minimum_chars=None):
    structured = result.get("structured_result") if isinstance(result, dict) else None
    if not isinstance(structured, dict):
        return None
    script = str(structured.get("final_script_for_tts") or "").strip()
    if not script:
        return None
    quality_issue = structured_result_quality_issue(structured)
    if quality_issue:
        logger.warning("Skip cached structured result with quality issue: %s", quality_issue)
        return None
    required_chars = (
        _min_gemini_final_chars()
        if minimum_chars is None
        else max(_min_gemini_final_chars(), _bounded_int(
            minimum_chars,
            default=_min_gemini_final_chars(),
            lower=0,
            upper=20000,
        ))
    )
    if len(_plain_text(script)) < required_chars:
        return None
    return structured


def _reusable_material_for_pdf(pdf, *, exclude_task_id=None, minimum_chars=None):
    asset_id = str((pdf or {}).get("attachment_id") or "").strip()
    if not asset_id:
        return None
    candidates_by_id = {}
    columns = (
        PublicationTask.report_audio_pdf_attachment_id,
        PublicationTask.source_task_id,
    )
    for column in columns:
        query = (
            PublicationTask.query.options(load_only(
                PublicationTask.id,
                PublicationTask.feature,
                PublicationTask.status,
                PublicationTask.final_script_chars,
                PublicationTask.source_task_id,
                PublicationTask.report_audio_pdf_attachment_id,
                PublicationTask.created_at,
                PublicationTask.completed_at,
            ))
            .filter(PublicationTask.feature.in_(MATERIAL_REUSE_FEATURES))
            .filter(PublicationTask.status == "completed")
            .filter(PublicationTask.final_script_chars > 0)
            .filter(column == asset_id)
        )
        if exclude_task_id:
            query = query.filter(PublicationTask.id != exclude_task_id)
        for candidate in query.order_by(
            PublicationTask.completed_at.desc(),
            PublicationTask.created_at.desc(),
        ).limit(20).all():
            candidates_by_id[candidate.id] = candidate
    candidates = sorted(
        candidates_by_id.values(),
        key=lambda item: (item.completed_at or item.created_at or datetime.min, item.created_at or datetime.min),
        reverse=True,
    )
    details = _load_task_results(candidate.id for candidate in candidates)
    for candidate in candidates:
        detail = details.get(candidate.id)
        structured = _valid_structured_result(
            detail.result if detail else None,
            minimum_chars=minimum_chars,
        )
        if structured:
            return {
                "source": "reused_script_material",
                "source_task_id": candidate.id,
                "source_feature": candidate.feature,
                "structured_result": structured,
            }
    return None


def _script_material_reuse_payload(reusable, pdf, structured):
    script = (structured or {}).get("final_script_for_tts") or ""
    chunks = split_tts_text(script)
    return {
        "status": "completed",
        "mode": "reused_script_material",
        "source": reusable.get("source") or "reused_script_material",
        "source_task_id": reusable.get("source_task_id"),
        "source_feature": reusable.get("source_feature"),
        "pdf_attachment_id": (pdf or {}).get("attachment_id"),
        "pdf_filename": (pdf or {}).get("filename"),
        "chars": len(_plain_text(script)),
        "chunks": len(chunks),
        "completed_at": datetime.utcnow().isoformat() + "Z",
    }


def _reusable_audio_for_pdf(
    pdf,
    structured,
    *,
    exclude_task_id=None,
    tts_provider="doubao_browser",
    tts_voice=None,
):
    asset_id = str((pdf or {}).get("attachment_id") or "").strip()
    expected_script = _plain_text((structured or {}).get("final_script_for_tts") or "")
    if not asset_id or not expected_script:
        return None
    candidates_by_id = {}
    columns = (
        PublicationTask.report_audio_pdf_attachment_id,
        PublicationTask.source_task_id,
    )
    for column in columns:
        query = (
            PublicationTask.query.options(load_only(
                PublicationTask.id,
                PublicationTask.feature,
                PublicationTask.status,
                PublicationTask.final_script_chars,
                PublicationTask.source_task_id,
                PublicationTask.report_audio_pdf_attachment_id,
                PublicationTask.created_at,
                PublicationTask.completed_at,
            ))
            .filter(PublicationTask.feature.in_(MATERIAL_REUSE_FEATURES))
            .filter(PublicationTask.status == "completed")
            .filter(PublicationTask.final_script_chars > 0)
            .filter(column == asset_id)
        )
        if exclude_task_id:
            query = query.filter(PublicationTask.id != exclude_task_id)
        for candidate in query.order_by(
            PublicationTask.completed_at.desc(),
            PublicationTask.created_at.desc(),
        ).limit(20).all():
            candidates_by_id[candidate.id] = candidate
    candidates = sorted(
        candidates_by_id.values(),
        key=lambda item: (item.completed_at or item.created_at or datetime.min, item.created_at or datetime.min),
        reverse=True,
    )
    details = _load_task_results(candidate.id for candidate in candidates)
    for candidate in candidates:
        detail = details.get(candidate.id)
        if not detail:
            continue
        audio = _valid_reusable_audio(
            detail,
            expected_script,
            tts_provider=tts_provider,
            tts_voice=tts_voice,
        )
        if audio:
            return {
                "source_task_id": candidate.id,
                "source_feature": candidate.feature,
                "audio": audio,
            }
    return None


def _valid_reusable_audio(
    candidate,
    expected_script,
    *,
    tts_provider="doubao_browser",
    tts_voice=None,
):
    result = candidate.result if isinstance(candidate.result, dict) else {}
    structured = _valid_structured_result(result)
    if not structured:
        return None
    script = structured.get("final_script_for_tts") or ""
    if _plain_text(script) != expected_script:
        return None
    audio = result.get("audio") if isinstance(result.get("audio"), dict) else {}
    audio_provider = str(audio.get("provider") or "doubao_browser").strip().lower()
    if audio_provider != str(tts_provider or "doubao_browser").strip().lower():
        return None
    if audio_provider == "doubao_api" and str(audio.get("voice_type") or "") != str(tts_voice or ""):
        return None
    wav_path = str(audio.get("wav_path") or "").strip()
    if not wav_path or not os.path.exists(wav_path):
        return None
    try:
        reusable_audio = doubao_service.validate_audio_duration_for_text(dict(audio), script)
    except Exception as exc:
        logger.warning("Skip reusable audio with invalid duration: task=%s error=%s", candidate.id, exc)
        return None
    reusable_audio["wav_path"] = wav_path
    return reusable_audio


def _completed_gemini_task(parent_id, result):
    candidate_ids = []
    gemini = result.get("gemini") if isinstance(result, dict) else None
    children = result.get("children") if isinstance(result, dict) else None
    if isinstance(gemini, dict) and gemini.get("job_id"):
        candidate_ids.append(str(gemini["job_id"]))
    if isinstance(children, dict):
        child = children.get("gemini")
        if isinstance(child, dict) and child.get("job_id"):
            candidate_ids.append(str(child["job_id"]))
    for candidate_id in dict.fromkeys(candidate_ids):
        task = _load_task(candidate_id, *TASK_CHILD_COLUMNS)
        if task and task.status == "completed" and _valid_gemini_task_result(task.result):
            return task
    candidate = (
        db.session.query(PublicationTask.id)
        .filter(
            PublicationTask.feature == gemini_service.FEATURE_NAME,
            PublicationTask.parent_task_id == parent_id,
            PublicationTask.status == "completed",
            PublicationTask.gemini_response_chars >= _min_gemini_final_chars(),
        )
        .order_by(PublicationTask.created_at.desc())
        .first()
    )
    return _load_task(candidate.id, *TASK_CHILD_COLUMNS) if candidate else None


def _active_gemini_task(parent_id, result):
    candidate_ids = []
    children = result.get("children") if isinstance(result, dict) and isinstance(result.get("children"), dict) else {}
    gemini = children.get("gemini") if isinstance(children.get("gemini"), dict) else {}
    if gemini.get("job_id"):
        candidate_ids.append(str(gemini["job_id"]))
    for candidate_id in dict.fromkeys(candidate_ids):
        task = _load_task(candidate_id, *TASK_CHILD_COLUMNS)
        if task and task.status not in TERMINAL_STATUSES and task.feature == gemini_service.FEATURE_NAME:
            return task
    return (
        PublicationTask.query.filter(
            PublicationTask.feature == gemini_service.FEATURE_NAME,
            PublicationTask.parent_task_id == parent_id,
            PublicationTask.status.notin_(list(TERMINAL_STATUSES)),
        )
        .options(load_only(*TASK_CHILD_POLL_COLUMNS))
        .order_by(PublicationTask.created_at.desc())
        .first()
    )


def _valid_gemini_task_result(result):
    if not isinstance(result, dict):
        return False
    raw_text = result.get("final_response_text") or result.get("response_text") or ""
    return len(_plain_text(raw_text)) >= _min_gemini_final_chars()


def _script_material_only(payload):
    if not isinstance(payload, dict):
        return False
    for key in ("script_material_only", "material_only", "script_only"):
        if key in payload:
            return _truthy(payload.get(key), False)
    return False


def _complete_script_material_task(
    workflow_task_id,
    workflow_created_at,
    *,
    structured,
    pdf,
    gemini_task_id,
    gemini_result,
    chunks,
    tts_chars,
    script_material_reuse=None,
):
    completed_at = datetime.utcnow()
    completed_at_text = completed_at.isoformat() + "Z"
    video_result = {
        "enabled": False,
        "status": "skipped",
        "message": "script_material_only",
    }
    script_material = {
        "status": "completed",
        "mode": "script_material_only",
        "source": FEATURE_NAME,
        "pdf_attachment_id": pdf.get("attachment_id"),
        "pdf_filename": pdf.get("filename"),
        "chars": tts_chars,
        "chunks": len(chunks or []),
        "completed_at": completed_at_text,
    }
    if isinstance(script_material_reuse, dict):
        script_material.update({
            "mode": script_material_reuse.get("mode") or script_material["mode"],
            "source": script_material_reuse.get("source") or script_material["source"],
            "source_task_id": script_material_reuse.get("source_task_id"),
            "source_feature": script_material_reuse.get("source_feature"),
        })
    output_path = storage_path_for_job(workflow_task_id, workflow_created_at)
    final_payload = {
        "workflow_task_id": workflow_task_id,
        "gemini_task_id": gemini_task_id,
        "structured_result": structured,
        "script_material": script_material,
        "pdf": pdf,
        "audio": None,
        "video": video_result,
        "gemini": gemini_result,
        "completed_at": completed_at_text,
    }
    with open(output_path, "w", encoding="utf-8") as file:
        json.dump(final_payload, file, ensure_ascii=False, indent=2)

    fresh = _load_task(workflow_task_id, *TASK_STATE_COLUMNS)
    if not fresh:
        raise RuntimeError("编排任务不存在")
    final_result = dict(fresh.result or {})
    final_result.update({
        "gemini": _task_public_summary_by_id(gemini_task_id) if gemini_task_id else final_result.get("gemini"),
        "doubao_browser_tts": [],
        "publisher_video": None,
        "structured_result": structured,
        "tts_text": {
            "chars": len(structured.get("final_script_for_tts") or ""),
            "chunks": len(chunks or []),
        },
        "script_material": script_material,
        "audio": None,
        "video": video_result,
        "output_path": output_path,
        "completed_at": completed_at_text,
    })
    fresh.status = "completed"
    fresh.current_step = "done"
    fresh.progress = 100
    fresh.completed_at = completed_at
    fresh.error_message = None
    fresh.result = final_result
    flag_modified(fresh, "result")
    db.session.commit()


def _fresh_result(parent_id):
    cached = _cached_workflow_result(parent_id)
    if cached is not None:
        return cached
    fresh = _load_task(parent_id, *TASK_STATE_COLUMNS)
    return dict(fresh.result or {}) if fresh and isinstance(fresh.result, dict) else {}


def _existing_child_from_result(result, label):
    child_id = _child_id_from_result(result, label)
    if not child_id:
        return None
    columns = TASK_PUBLISHER_MATCH_COLUMNS if label == "publisher_video" else TASK_CHILD_POLL_COLUMNS
    return _load_task(child_id, *columns)


def _child_id_from_result(result, label):
    children = result.get("children") if isinstance(result.get("children"), dict) else {}
    child_summary = children.get(label) if isinstance(children.get(label), dict) else {}
    direct_summary = result.get(label) if isinstance(result.get(label), dict) else {}
    child_id = child_summary.get("job_id") or direct_summary.get("job_id")
    return str(child_id).strip() if child_id else None


def _run_doubao_tts_chunks(parent, parent_id, chunks, payload, *, retry_requested_at=None, retry_limit=2):
    total = len(chunks)
    parallelism = min(total or 1, _doubao_parallelism())
    max_attempts = int(retry_limit or 0) + 1

    while True:
        completed = {}
        active = {}
        pending = []
        summaries = []

        for index, chunk in enumerate(chunks, start=1):
            label = f"doubao_browser_{index}"
            start = _doubao_progress_start(index, total)
            end = _doubao_progress_end(index, total)

            reusable_tts_task = _completed_tts_task_for_chunk(parent_id, index, chunk)
            if reusable_tts_task:
                reusable_summary = _task_state_snapshot(reusable_tts_task)
                _clear_waiting_child(parent_id, label)
                completed[index] = reusable_summary
                summaries.append(_task_id(reusable_summary))
                _record_child(parent_id, label, reusable_summary)
                _emit(parent_id, f"{label}_reuse", end, f"复用豆包浏览器朗读音频 {index}/{total}")
                continue

            active_tts_task = _active_tts_task_for_chunk(parent_id, index, chunk)
            if active_tts_task:
                active_summary = _task_state_snapshot(active_tts_task)
                active[index] = active_summary
                summaries.append(_task_id(active_summary))
                _record_child(parent_id, label, active_summary)
                continue

            failed_tts_tasks = _failed_tts_tasks_for_chunk(parent_id, index, chunk, since=retry_requested_at)
            next_attempt = 1
            if failed_tts_tasks:
                _clear_waiting_child(parent_id, label)
                failed_summaries = [_task_state_snapshot(failed_task) for failed_task in failed_tts_tasks]
                for failed_summary in failed_summaries:
                    failed_attempt = _task_chunk_attempt(failed_summary) or 1
                    _record_child_attempt(
                        parent_id,
                        label,
                        failed_summary,
                        failed_attempt,
                        max_attempts,
                        "failed",
                        error=_child_error_message(failed_summary),
                    )
                latest_failed = failed_summaries[-1]
                failure_class = _child_failure_classification(latest_failed)
                if failure_class:
                    raise RuntimeError(
                        f"{failure_class}: 豆包浏览器朗读 worker 与当前代码不一致，"
                        f"请重启相关 worker 后再续跑: {_child_error_message(latest_failed)}"
                    )
                if _is_doubao_captcha_blocked(latest_failed):
                    raise RuntimeError(f"豆包浏览器朗读被人机验证阻塞: {_child_error_message(latest_failed)}")
                next_attempt = max(_task_chunk_attempt(task) or 1 for task in failed_summaries) + 1
                if next_attempt > max_attempts:
                    raise RuntimeError(f"豆包浏览器朗读子任务失败: {_child_error_message(latest_failed)}")

            pending.append({
                "index": index,
                "chunk": chunk,
                "attempt": next_attempt,
                "start": start,
                "end": end,
            })

        if len(completed) == total:
            return _ordered_doubao_tts_outputs(parent_id, chunks, completed)

        open_slots = max(0, parallelism - len(active))
        for item in pending[:open_slots]:
            index = item["index"]
            label = f"doubao_browser_{index}"
            attempt = item["attempt"]
            retry_suffix = "" if attempt == 1 else f"（重试 {attempt - 1}/{retry_limit}）"
            _emit(
                parent_id,
                f"submit_{label}",
                item["start"],
                f"创建豆包浏览器朗读子任务 {index}/{total}{retry_suffix} · 并发 {parallelism}",
            )
            tts_task = _create_doubao_tts_task(parent, item["chunk"], index, total, payload, attempt=attempt)
            tts_summary = _task_state_snapshot(tts_task)
            active[index] = tts_summary
            summaries.append(_task_id(tts_summary))
            _record_child(parent_id, label, tts_summary)
            _record_child_attempt(parent_id, label, tts_summary, attempt, max_attempts, "running")

        if summaries:
            _update_result(parent_id, {"doubao_browser_tts": _task_public_summaries_by_ids(summaries)})
        if active:
            _queue_notification(parent_id, "report_audio_doubao_submitted", f"豆包浏览器朗读已开始，共 {total} 段，并发 {parallelism}")

        if not active:
            raise RuntimeError("豆包浏览器朗读没有可等待或可投递的分段")

        index, active_task = sorted(active.items(), key=lambda item: item[0])[0]
        label = f"doubao_browser_{index}"
        active_task_id = _task_id(active_task)
        _emit(parent_id, f"{label}_wait_existing", _doubao_progress_start(index, total), f"等待豆包浏览器朗读子任务 {index}/{total}")
        try:
            tts_task = _wait_child(
                parent_id,
                active_task_id,
                label,
                start_progress=_doubao_progress_start(index, total),
                end_progress=_doubao_progress_end(index, total),
            )
        except ChildTaskStillRunning:
            _record_child(
                parent_id,
                label,
                _task_state_snapshot(_load_task(active_task_id, *TASK_TTS_MATCH_COLUMNS))
                or active_task,
            )
            raise

        tts_summary = _task_state_snapshot(tts_task)
        tts_task_id = _task_id(tts_summary)
        _record_child(parent_id, label, tts_summary)
        if tts_summary.get("status") == "completed":
            wav_path = _task_audio_wav_path(tts_summary)
            if wav_path and os.path.exists(wav_path):
                _record_child_attempt(parent_id, label, tts_summary, _task_chunk_attempt(tts_summary) or 1, max_attempts, "completed")
                continue
            message = "豆包浏览器朗读子任务完成但没有返回可用 WAV 路径"
            _mark_child_failed(tts_task, message)
            failed_summary = (
                _task_state_snapshot(_load_task(tts_task_id, *TASK_TTS_MATCH_COLUMNS))
                or dict(tts_summary, error=message)
            )
            _record_child_attempt(parent_id, label, failed_summary, _task_chunk_attempt(failed_summary) or 1, max_attempts, "failed", error=message)
            continue

        last_error = _child_error_message(tts_summary)
        failure_class = _child_failure_classification(tts_summary)
        if failure_class:
            raise RuntimeError(
                f"{failure_class}: 豆包浏览器朗读 worker 与当前代码不一致，"
                f"请重启相关 worker 后再续跑: {last_error}"
            )
        if _is_doubao_captcha_blocked(tts_summary):
            raise RuntimeError(f"豆包浏览器朗读被人机验证阻塞: {last_error}")
        attempt = _task_chunk_attempt(tts_summary) or 1
        _record_child_attempt(parent_id, label, tts_summary, attempt, max_attempts, "failed", error=last_error)
        if attempt < max_attempts:
            _emit(parent_id, f"{label}_retry", _doubao_progress_end(index, total), f"豆包浏览器朗读子任务 {index}/{total} 失败，准备重试：{last_error}")
            continue
        raise RuntimeError(f"豆包浏览器朗读子任务失败: {last_error}")


def _doubao_parallelism():
    # Each browser worker owns an independent signed-in profile.  Default to
    # three chunks per workflow while retaining a conservative cap for
    # deployments that need to tune the pool through the environment.
    return max(1, min(_int_env("REPORT_AUDIO_DOUBAO_PARALLELISM", 3), _int_env("REPORT_AUDIO_DOUBAO_MAX_PARALLELISM", 8)))


def _doubao_progress_start(index, total):
    return 60 + int((index - 1) / max(1, total) * 28)


def _doubao_progress_end(index, total):
    return 60 + int(index / max(1, total) * 28)


def _ordered_doubao_tts_outputs(parent_id, chunks, completed_by_index):
    task_ids = []
    wav_paths = []
    for index, chunk in enumerate(chunks, start=1):
        task = completed_by_index.get(index)
        if not task:
            raise RuntimeError(f"豆包浏览器朗读缺少第 {index}/{len(chunks)} 段")
        expected_hash = _tts_text_hash(chunk)
        task_hash = _task_tts_text_hash(task)
        if task_hash and task_hash != expected_hash:
            raise RuntimeError(f"豆包浏览器朗读第 {index}/{len(chunks)} 段文本校验失败")
        if not task_hash and _plain_text(_task_tts_text(task)) != _plain_text(chunk):
            raise RuntimeError(f"豆包浏览器朗读第 {index}/{len(chunks)} 段文本校验失败")
        wav_path = _task_audio_wav_path(task)
        if not wav_path or not os.path.exists(wav_path):
            raise RuntimeError(f"豆包浏览器朗读第 {index}/{len(chunks)} 段 WAV 文件不存在")
        task_ids.append(_task_id(task))
        wav_paths.append(wav_path)

    if len(task_ids) != len(chunks) or len(wav_paths) != len(chunks):
        raise RuntimeError("豆包浏览器朗读分段数量校验失败")
    _update_result(parent_id, {
        "doubao_browser_order": [
            {
                "chunk_index": index,
                "task_id": task_ids[index - 1],
                "wav_path": wav_paths[index - 1],
                "text_hash": _tts_text_hash(chunks[index - 1]),
            }
            for index in range(1, len(chunks) + 1)
        ]
    })
    return task_ids, wav_paths


def _completed_tts_task_for_chunk(parent_id, chunk_index, expected_text):
    expected_hash = _tts_text_hash(expected_text)
    candidates = PublicationTask.query.filter(
        PublicationTask.feature == doubao_service.FEATURE_NAME,
        PublicationTask.parent_task_id == parent_id,
        PublicationTask.status == "completed",
        PublicationTask.doubao_tts_chunk_index == int(chunk_index),
        PublicationTask.doubao_tts_text_hash == expected_hash,
    ).options(load_only(*TASK_TTS_MATCH_COLUMNS)).order_by(PublicationTask.created_at.asc()).limit(20).all()
    for candidate in candidates:
        if _task_chunk_index(candidate) != chunk_index:
            continue
        if _task_tts_text_hash(candidate) != expected_hash:
            continue
        wav_path = _task_audio_wav_path(candidate)
        if wav_path and os.path.exists(wav_path):
            return candidate
    return _completed_tts_task_for_text(expected_text)


def _completed_tts_task_for_text(expected_text):
    expected_hash = _tts_text_hash(expected_text)
    match_limit = max(1, min(_int_env("REPORT_AUDIO_DOUBAO_CACHE_MATCH_LIMIT", 20), 100))
    candidates = (
        PublicationTask.query.filter(
            PublicationTask.feature == doubao_service.FEATURE_NAME,
            PublicationTask.status == "completed",
            PublicationTask.doubao_tts_text_hash == expected_hash,
        )
        .options(load_only(*TASK_TTS_MATCH_COLUMNS))
        .order_by(PublicationTask.created_at.desc())
        .limit(match_limit)
        .all()
    )
    for candidate in candidates:
        if _task_tts_text_hash(candidate) and _task_tts_text_hash(candidate) != expected_hash:
            continue
        wav_path = _task_audio_wav_path(candidate)
        if wav_path and os.path.exists(wav_path):
            return candidate
    return None


def _active_tts_task_for_chunk(parent_id, chunk_index, expected_text):
    expected_hash = _tts_text_hash(expected_text)
    candidates = PublicationTask.query.filter(
        PublicationTask.feature == doubao_service.FEATURE_NAME,
        PublicationTask.parent_task_id == parent_id,
        PublicationTask.status.notin_(list(TERMINAL_STATUSES)),
        PublicationTask.doubao_tts_chunk_index == int(chunk_index),
        PublicationTask.doubao_tts_text_hash == expected_hash,
    ).options(load_only(*TASK_TTS_MATCH_COLUMNS)).order_by(PublicationTask.created_at.desc()).limit(20).all()
    for candidate in candidates:
        if _task_chunk_index(candidate) != chunk_index:
            continue
        if _task_tts_text_hash(candidate) != expected_hash:
            continue
        if _mark_stale_doubao_child_if_needed(candidate):
            continue
        return candidate
    return None


def _mark_stale_doubao_child_if_needed(child):
    if not _is_stale_doubao_child(child):
        return False
    _mark_child_failed(child, _doubao_stale_child_message(child))
    return True


def _is_stale_doubao_child(child):
    status = _safe_task_attr(child, "status")
    if not child or status in TERMINAL_STATUSES:
        return False
    if _safe_task_attr(child, "feature") != doubao_service.FEATURE_NAME:
        return False
    updated_at = _safe_task_attr(child, "updated_at")
    if not updated_at:
        return False
    if updated_at.tzinfo:
        updated_at = updated_at.astimezone(timezone.utc).replace(tzinfo=None)
    return (datetime.utcnow() - updated_at).total_seconds() >= _doubao_child_stale_seconds(child)


def _doubao_stale_child_message(child):
    stale_seconds = _doubao_child_stale_seconds(child)
    updated_at = _safe_task_attr(child, "updated_at")
    updated_text = updated_at.isoformat() if updated_at else "-"
    return f"豆包浏览器朗读子任务超过 {stale_seconds}s 未更新，判定为 stale 并释放重试；last_updated={updated_text}"


def _doubao_child_stale_seconds(child=None):
    default_seconds = _int_env("DOUBAO_BROWSER_TTS_TOTAL_TIMEOUT", 300) + 180
    processing_seconds = max(60, _int_env("REPORT_AUDIO_DOUBAO_STALE_SECONDS", default_seconds))
    status = str(_safe_task_attr(child, "status") or "").strip().lower()
    step = str(_safe_task_attr(child, "current_step") or "").strip().lower()
    if status in {"pending", "queued", "scheduled", "retry"} or step in {"queued", "auto_retry"}:
        # A queued child does not emit heartbeats until a browser worker claims it.
        # Keep queue backlog separate from a worker that has actually stopped making
        # progress, otherwise a healthy but busy queue is incorrectly failed after
        # the normal browser execution timeout.
        queue_default = max(processing_seconds, 1800)
        return max(
            processing_seconds,
            _int_env("REPORT_AUDIO_DOUBAO_QUEUE_STALE_SECONDS", queue_default),
        )
    return processing_seconds


def _failed_tts_tasks_for_chunk(parent_id, chunk_index, expected_text, *, since=None):
    cutoff = _parse_utc_datetime(since)
    expected_hash = _tts_text_hash(expected_text)
    candidates = PublicationTask.query.filter(
        PublicationTask.feature == doubao_service.FEATURE_NAME,
        PublicationTask.parent_task_id == parent_id,
        PublicationTask.status.in_(["failed", "blocked_by_captcha"]),
        PublicationTask.doubao_tts_chunk_index == int(chunk_index),
        PublicationTask.doubao_tts_text_hash == expected_hash,
    ).options(load_only(*TASK_TTS_MATCH_COLUMNS)).order_by(PublicationTask.created_at.asc()).limit(20).all()
    matched = []
    for candidate in candidates:
        if cutoff and candidate.created_at and candidate.created_at < cutoff:
            continue
        if _task_chunk_index(candidate) != chunk_index:
            continue
        if _task_tts_text_hash(candidate) != expected_hash:
            continue
        matched.append(candidate)
    return matched


def _parse_utc_datetime(value):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo:
        return parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _task_chunk_index(task):
    projected = _safe_task_attr(task, "doubao_tts_chunk_index")
    if isinstance(projected, (int, float, str)) and str(projected).strip():
        try:
            parsed = int(float(projected or 0))
            if parsed > 0:
                return parsed
        except (TypeError, ValueError):
            pass
    metadata = _task_doubao_metadata(task)
    try:
        return int(metadata.get("chunk_index") or 0)
    except (TypeError, ValueError):
        return 0


def _task_chunk_attempt(task):
    projected = _safe_task_attr(task, "doubao_tts_chunk_attempt")
    if isinstance(projected, (int, float, str)) and str(projected).strip():
        try:
            parsed = int(float(projected or 0))
            if parsed > 0:
                return parsed
        except (TypeError, ValueError):
            pass
    metadata = _task_doubao_metadata(task)
    try:
        return int(metadata.get("chunk_attempt") or 0)
    except (TypeError, ValueError):
        return 0


def _task_tts_text_hash(task):
    projected = _safe_task_attr(task, "doubao_tts_text_hash")
    if isinstance(projected, str) and projected.strip():
        return projected.strip()
    metadata = _task_doubao_metadata(task)
    return str(metadata.get("text_hash") or "").strip()


def _task_audio_wav_path(task):
    projected = _safe_task_attr(task, "doubao_tts_wav_path")
    if isinstance(projected, str) and projected.strip():
        return projected
    result = _task_result_payload(task)
    if not result and not isinstance(task, dict):
        try:
            raw_result = task.result
            result = raw_result if isinstance(raw_result, dict) else {}
        except Exception:
            result = {}
    audio = result.get("audio") if isinstance(result.get("audio"), dict) else {}
    return audio.get("wav_path") or result.get("wav_path")


def _task_tts_text(task):
    params = _task_params_payload(task)
    if not params and not isinstance(task, dict):
        try:
            raw_params = task.params
            params = raw_params if isinstance(raw_params, dict) else {}
        except Exception:
            params = {}
    doubao_payload = params.get("doubao_browser_tts") if isinstance(params.get("doubao_browser_tts"), dict) else {}
    return str(doubao_payload.get("text") or "")


def _tts_text_hash(text):
    return hashlib.sha256(_plain_text(text).encode("utf-8")).hexdigest()


def _is_doubao_captcha_blocked(task):
    snapshot = _task_state_snapshot(task)
    if not snapshot:
        return False
    result = snapshot.get("result") if isinstance(snapshot.get("result"), dict) else {}
    return (
        snapshot.get("status") == "blocked_by_captcha"
        or result.get("error_type") == "blocked_by_captcha"
        or result.get("blocked_reason") == "captcha"
    )


def _create_doubao_tts_task(parent, text, index, total, payload, *, attempt=1):
    retry_index = max(0, int(attempt or 1) - 1)
    text_hash = _tts_text_hash(text)
    payload_metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    child_metadata = dict(payload_metadata)
    if child_metadata.get("source") and child_metadata.get("source") != FEATURE_NAME:
        child_metadata.setdefault("origin_source", child_metadata.get("source"))
    child_metadata.update({
        "source": FEATURE_NAME,
        "parent_task_id": parent.id,
        "chunk_index": index,
        "chunk_total": total,
        "chunk_attempt": int(attempt or 1),
        "retry_index": retry_index,
        "text_hash": text_hash,
    })
    child = PublicationTask(
        status="pending",
        current_step="queued",
        progress=0,
        params={
            "feature": doubao_service.FEATURE_NAME,
            "user_id": parent.params.get("user_id"),
            "parent_feature": FEATURE_NAME,
            "parent_task_id": parent.id,
            "doubao_browser_tts": {
                "text": text,
                "metadata": child_metadata,
            },
        },
        result={
            "metadata": child_metadata,
            "audio": None,
            "logs": [],
        },
    )
    db.session.add(child)
    db.session.flush()
    child_id = child.id
    child_snapshot = SimpleNamespace(**_task_state_snapshot(child))
    db.session.commit()
    async_result = apply_async_after_db_release(
        process_doubao_browser_tts_job,
        args=[child_id],
        queue=_doubao_queue_name(),
        retry=False,
    )
    _persist_child_celery_task_id(child_id, async_result.id)
    child_snapshot.celery_task_id = async_result.id
    child_snapshot.celery_id = async_result.id
    return child_snapshot


def _doubao_queue_name():
    return os.getenv("DOUBAO_BROWSER_TTS_QUEUE", doubao_service.QUEUE_NAME)


def _create_publisher_video_task(parent, *, audio, structured, pdf, pdf_screenshot, payload):
    video_options = payload.get("video") if isinstance(payload.get("video"), dict) else {}
    render_mode = _video_render_mode(payload)
    preview_seconds = _video_preview_seconds(video_options) if render_mode == "preview_10s" else 0
    vertical_schemes = _normalize_vertical_schemes(
        video_options.get("vertical_video_schemes"),
        video_options.get("vertical_video_scheme") or video_options.get("vertical_scheme") or "scheme1",
    )
    vertical_scheme = _primary_vertical_scheme(vertical_schemes)
    secondary_vertical_scheme = next((item for item in vertical_schemes if item != vertical_scheme), None)
    existing = _find_existing_publisher_video_task(
        parent.id,
        pdf_screenshot,
        render_mode=render_mode,
        preview_seconds=preview_seconds,
        vertical_schemes=vertical_schemes,
    )
    if existing:
        return existing

    try:
        audio_seconds = float((audio or {}).get("seconds") or 0)
    except (TypeError, ValueError):
        audio_seconds = 0
    scheme3_visual = None
    if "scheme3" in vertical_schemes:
        scheme3_visual = _resolve_scheme3_visual_inputs(parent, audio_seconds=audio_seconds)
        if not scheme3_visual.get("workflow_id"):
            raise RuntimeError("方案三未找到同一 PDF 的视觉工作流")
        if not scheme3_visual.get("scene_timeline") and not scheme3_visual.get("video_path"):
            raise ChildTaskStillRunning(
                "scheme3_visual",
                scheme3_visual["workflow_id"],
                scheme3_visual.get("status") or "processing",
                scheme3_visual.get("step") or "images_queued",
            )
    preview_end_offset = max(0.0, audio_seconds - preview_seconds) if preview_seconds > 0 and audio_seconds > preview_seconds else 0.0
    main_title = _publisher_main_title(structured, video_options, variant="landscape")
    sub_title = _publisher_sub_title(structured, video_options, variant="landscape")
    vertical_main_title = _publisher_main_title(structured, video_options, variant="vertical")
    vertical_sub_title = _publisher_sub_title(structured, video_options, variant="vertical")
    institution = _publisher_workbench_institution(structured, pdf, video_options)
    main_title = _publisher_cover_title_with_institution(main_title, institution)
    vertical_main_title = _publisher_cover_title_with_institution(vertical_main_title, institution)
    date_text = (
        str(video_options.get("date_text") or "").strip()
        or _publisher_date_text(parent.created_at)
    )
    tag_text = str(video_options.get("tag_text") or "").strip() or "研报播客"
    audio_path = audio.get("wav_path") if isinstance(audio, dict) else None
    screenshot_path = pdf_screenshot.get("path") if isinstance(pdf_screenshot, dict) else None
    vertical_subtitle_position = (
        video_options.get("vertical_subtitle_position")
        if isinstance(video_options.get("vertical_subtitle_position"), dict)
        else ({"y_pct": 0.73} if vertical_scheme in {"scheme2", "scheme3"} else None)
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
            if vertical_scheme in {"scheme2", "scheme3"}
            else None
        )
    )

    params = {
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
                "end_offset": preview_end_offset,
                "speed": 1.0,
            }
        ],
        "video": {
            "enabled": _truthy(video_options.get("enabled", False)),
            "sub_burn_enabled": _publisher_sub_burn_enabled(video_options),
            "asr_model": str(video_options.get("asr_model") or "base"),
            "video_path": "",
            "audio_path": audio_path,
            "image_path": screenshot_path,
            "audio_start": 0,
            "audio_end_offset": preview_end_offset,
            "transform": _publisher_video_transform(video_options),
            "cta": _publisher_cta(video_options),
        },
        "vertical_video": {
            "enabled": _truthy(video_options.get("vertical_enabled", False)),
            "asr_model": str(video_options.get("asr_model") or "base"),
            "image_path": screenshot_path,
            "subtitles": [],
            "scheme": vertical_scheme,
            **(
                {"visual_scene_timeline": scheme3_visual["scene_timeline"]}
                if vertical_scheme == "scheme3" and scheme3_visual and scheme3_visual.get("scene_timeline")
                else {}
            ),
            **(
                {"visual_video_path": scheme3_visual["video_path"]}
                if vertical_scheme == "scheme3" and scheme3_visual and scheme3_visual.get("video_path")
                else {}
            ),
            **({"preview_seconds": preview_seconds} if preview_seconds > 0 else {}),
            **({"subtitle_position": vertical_subtitle_position} if vertical_subtitle_position else {}),
            **({"subtitle_style": vertical_subtitle_style} if vertical_subtitle_style else {}),
        },
        "vertical_video_2": {
            "enabled": _truthy(video_options.get("vertical_enabled", False)) and bool(secondary_vertical_scheme),
            "scheme": secondary_vertical_scheme,
            **(
                {"visual_scene_timeline": scheme3_visual["scene_timeline"]}
                if secondary_vertical_scheme == "scheme3" and scheme3_visual and scheme3_visual.get("scene_timeline")
                else {}
            ),
            **(
                {"visual_video_path": scheme3_visual["video_path"]}
                if secondary_vertical_scheme == "scheme3" and scheme3_visual and scheme3_visual.get("video_path")
                else {}
            ),
            "asr_model": str(video_options.get("asr_model") or "base"),
            "image_path": screenshot_path,
            "screenshot_path": screenshot_path,
            "main_title": vertical_main_title,
            "sub_title": vertical_sub_title,
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
            "enabled": _truthy(video_options.get("vertical_enabled", False)),
            "scheme": vertical_scheme,
            "main_title": vertical_main_title,
            "sub_title": vertical_sub_title,
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
            "enabled": _truthy(video_options.get("vertical_enabled", False)) and vertical_scheme == "scheme2",
            "scheme": vertical_scheme,
            "format": "wx_cover",
            "main_title": vertical_main_title,
            "sub_title": vertical_sub_title,
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
            "enabled": _truthy(video_options.get("vertical_enabled", False)) and vertical_scheme == "scheme2",
            "scheme": vertical_scheme,
            "format": "podcast",
            "main_title": vertical_main_title,
            "sub_title": vertical_sub_title,
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
            "enabled": _truthy(video_options.get("vertical_enabled", False)) and vertical_scheme == "scheme2",
            "scheme": vertical_scheme,
            "format": "bilibili_wx_cover",
            "main_title": vertical_main_title,
            "sub_title": vertical_sub_title,
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
        "thumbnail": {
            "main_title": main_title,
            "sub_title": sub_title,
            "tag_text": tag_text,
            "date_text": date_text,
            "bg_index": int(video_options.get("bg_index") or 0),
            "layout": "left",
            "mode": "screenshot",
            "custom_bg_path": screenshot_path,
            "overlay_opacity": float(video_options.get("overlay_opacity") or 0.5),
            "transform": video_options.get("cover_transform") or {"x": 0, "y": 0, "scale": 1},
        },
        "bilibili": {
            "enabled": _truthy(video_options.get("bilibili_enabled", False)),
            "title": _single_line(main_title),
            "desc": _publisher_description(structured, pdf),
            "tid": int(video_options.get("bilibili_tid") or 207),
            "tags": _publisher_tags(video_options),
            "copyright": 1,
            "no_reprint": 1,
            "neutral_mark": "个人观点，仅供参考",
            "charging_pay": _publisher_bilibili_charging_pay(video_options),
            "delay_time": _publisher_bilibili_delay_time(video_options),
            "season_id": _publisher_bilibili_season_id(video_options),
        },
        "youtube": {"enabled": False},
        "metadata": {
            "source": FEATURE_NAME,
            "parent_task_id": parent.id,
            "pdf": pdf,
            "render_mode": render_mode,
            "preview_seconds": preview_seconds,
            "vertical_video_scheme": vertical_scheme,
            "vertical_video_schemes": vertical_schemes,
            **(
                {"visual_workflow_id": scheme3_visual["workflow_id"]}
                if scheme3_visual and scheme3_visual.get("workflow_id")
                else {}
            ),
        },
    }

    child = PublicationTask(
        status="pending",
        current_step="queued",
        progress=0,
        params=params,
        result={
            "metadata": params["metadata"],
            "source_task_id": parent.id,
            "pdf_first_page_image": pdf_screenshot,
            "video_path": None,
            "cover_path": None,
            "bilibili_cover_path": None,
            "render_mode": render_mode,
            "preview_seconds": preview_seconds,
            "vertical_video_scheme": vertical_scheme,
            "vertical_video_schemes": vertical_schemes,
        },
    )
    db.session.add(child)
    db.session.flush()
    child_id = child.id
    child_snapshot = SimpleNamespace(**_task_state_snapshot(child))
    db.session.commit()
    async_result = apply_async_after_db_release(
        run_publication_pipeline,
        args=[child_id],
        queue="render",
        retry=False,
    )
    _persist_child_celery_task_id(child_id, async_result.id)
    child_snapshot.celery_task_id = async_result.id
    child_snapshot.celery_id = async_result.id
    return child_snapshot


def _resolve_scheme3_visual_inputs(parent, *, audio_seconds):
    """Resolve the live same-PDF scene assets before creating a scheme-3 render.

    The visual workflow runs independently from TTS.  Returning its live state
    lets the parent defer without creating a publisher task that is guaranteed
    to fail while scene images are still being produced.
    """
    parent_id = str(getattr(parent, "id", "") or "").strip()
    if not parent_id:
        return {}

    audio_task = (
        PublicationTask.query
        .options(load_only(
            PublicationTask.id,
            PublicationTask.user_id,
            PublicationTask.report_audio_pdf_attachment_id,
            PublicationTask.report_audio_pdf_filename,
            PublicationTask.report_audio_pdf_bytes,
        ))
        .filter(PublicationTask.id == parent_id)
        .one_or_none()
    )
    if not audio_task:
        return {}

    # Imported lazily to avoid the API/task import cycle during Celery startup.
    from api.report_audio_workflow import (
        _linked_visual_scene_timeline,
        _linked_visual_video_path,
        _linked_visual_video_payloads,
    )

    current_user = SimpleNamespace(id=audio_task.user_id)
    candidates = _linked_visual_video_payloads(current_user, audio_task)
    selected = next(
        (
            item for item in candidates
            if str(((item.get("visual_scheme") or {}).get("key")) or "").strip().lower()
            == "stick_figure"
        ),
        None,
    )
    if not selected:
        return {}

    workflow_id = str(selected.get("workflow_job_id") or "").strip()
    scene_timeline = _linked_visual_scene_timeline(
        current_user,
        audio_task,
        workflow_id,
        audio_seconds=audio_seconds,
    )
    video_path = _linked_visual_video_path(current_user, audio_task, workflow_id)
    return {
        "workflow_id": workflow_id,
        "scene_timeline": scene_timeline,
        "video_path": video_path,
        "status": selected.get("status"),
        "step": selected.get("step"),
    }


def _find_existing_publisher_video_task(
    parent_id,
    pdf_screenshot,
    *,
    render_mode="full_video",
    preview_seconds=0,
    vertical_schemes=None,
):
    candidates = (
        PublicationTask.query
        .filter(PublicationTask.feature == "publisher")
        .filter(PublicationTask.source_task_id == parent_id)
        .options(load_only(*TASK_PUBLISHER_MATCH_COLUMNS))
        .order_by(PublicationTask.created_at.desc())
        .limit(30)
        .all()
    )
    if not candidates:
        return None

    matching = [
        task for task in candidates
        if _publisher_child_render_mode(task) == render_mode
        and _publisher_child_preview_seconds(task) == int(preview_seconds or 0)
        and (
            vertical_schemes is None
            or _publisher_child_vertical_schemes(task) == _normalize_vertical_schemes(vertical_schemes, "scheme1")
        )
    ]
    if not matching:
        return None

    for task in matching:
        if task.status == "completed" and _publisher_video_output_ready(task, pdf_screenshot):
            _cancel_duplicate_publisher_tasks(matching, keep_id=task.id)
            return task

    active = [task for task in matching if task.status not in TERMINAL_STATUSES]
    if active:
        keep = active[0]
        _cancel_duplicate_publisher_tasks(active[1:], keep_id=keep.id)
        return keep
    return None


def _publisher_child_render_mode(task):
    projected = _safe_task_attr(task, "publisher_render_mode")
    if projected:
        return str(projected)
    result = _copy_dict(_safe_task_attr(task, "result"))
    params = _copy_dict(_safe_task_attr(task, "params"))
    metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
    if not metadata:
        metadata = params.get("metadata") if isinstance(params.get("metadata"), dict) else {}
    raw = metadata.get("render_mode") or result.get("render_mode") or params.get("render_mode") or "full_video"
    return "preview_10s" if str(raw).strip().lower() in {"preview", "preview_10s", "short", "short_video"} else "full_video"


def _publisher_child_preview_seconds(task):
    projected = _safe_task_attr(task, "publisher_preview_seconds")
    if projected is not None:
        return int(projected or 0)
    result = _copy_dict(_safe_task_attr(task, "result"))
    params = _copy_dict(_safe_task_attr(task, "params"))
    vertical = params.get("vertical_video") if isinstance(params.get("vertical_video"), dict) else {}
    metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
    if not metadata:
        metadata = params.get("metadata") if isinstance(params.get("metadata"), dict) else {}
    raw = metadata.get("preview_seconds") or result.get("preview_seconds") or vertical.get("preview_seconds") or 0
    try:
        return int(float(raw or 0))
    except (TypeError, ValueError):
        return 0


def _publisher_child_vertical_schemes(task):
    result = _copy_dict(_safe_task_attr(task, "result"))
    params = _copy_dict(_safe_task_attr(task, "params"))
    metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
    if not metadata:
        metadata = params.get("metadata") if isinstance(params.get("metadata"), dict) else {}
    vertical = params.get("vertical_video") if isinstance(params.get("vertical_video"), dict) else {}
    return _normalize_vertical_schemes(
        metadata.get("vertical_video_schemes") or result.get("vertical_video_schemes"),
        metadata.get("vertical_video_scheme") or vertical.get("scheme") or result.get("vertical_video_scheme") or "scheme1",
    )


def _publisher_video_output_ready(task, pdf_screenshot):
    summary = _publisher_result_summary(task, pdf_screenshot)
    path = (
        summary.get("video_path")
        or summary.get("landscape_video_path")
        or summary.get("vertical_video_path")
        or summary.get("vertical_preview_video_path")
    )
    if not (path and os.path.exists(path)):
        return False
    vertical_schemes = summary.get("vertical_video_schemes") or []
    vertical_paths = (
        summary.get("vertical_preview_video_paths")
        if summary.get("render_mode") == "preview_10s"
        else summary.get("vertical_video_paths")
    ) or {}
    return not summary.get("vertical_enabled") or not vertical_schemes or all(
        vertical_paths.get(scheme) and os.path.exists(vertical_paths[scheme])
        for scheme in vertical_schemes
    )


def _report_video_completion_contract(video_result, audio, payload):
    video = _copy_dict(video_result)
    audio = _copy_dict(audio)
    render_mode = _video_render_mode(payload)
    requires_full_video = _video_stage_enabled(payload) and render_mode == "full_video"
    include_landscape = requires_full_video and _truthy(video.get("landscape_enabled"), False)
    vertical_schemes = (
        _normalize_vertical_schemes(video.get("vertical_video_schemes"), video.get("vertical_video_scheme"))
        if requires_full_video and _truthy(video.get("vertical_enabled"), False)
        else []
    )
    vertical_paths = _copy_dict(video.get("vertical_video_paths"))
    if len(vertical_schemes) == 1 and video.get("vertical_video_path"):
        vertical_paths.setdefault(vertical_schemes[0], video.get("vertical_video_path"))
    landscape_path = str(video.get("landscape_video_path") or "").strip()
    if include_landscape and not landscape_path:
        landscape_path = str(video.get("video_path") or "").strip()
    return evaluate_report_video_completion(
        include_landscape=include_landscape,
        vertical_schemes=vertical_schemes,
        landscape_path=landscape_path,
        vertical_paths=vertical_paths,
        audio_path=str(audio.get("wav_path") or ""),
        probe=COMPLETION_MEDIA_PROBE,
    )


def _cancel_duplicate_publisher_tasks(tasks, *, keep_id):
    changed = False
    revoke_snapshots = []
    for task in tasks:
        if not task or task.id == keep_id or task.status in TERMINAL_STATUSES:
            continue
        revoke_snapshots.append(SimpleNamespace(
            id=str(task.id),
            celery_task_id=str(task.celery_task_id or ""),
        ))
        result = dict(task.result or {})
        result["error"] = f"重复视频子任务已取消，保留 {keep_id}"
        task.status = "canceled"
        task.current_step = "canceled"
        task.error_message = result["error"]
        task.completed_at = datetime.utcnow()
        task.result = result
        flag_modified(task, "result")
        changed = True
    if changed:
        db.session.commit()
        for snapshot in revoke_snapshots:
            _revoke_child_task(snapshot)


def _wait_child(parent_id, child_id, label, *, start_progress, end_progress):
    timeout = _child_inline_wait_seconds(label)
    poll_seconds = max(1, _int_env("REPORT_AUDIO_CHILD_POLL_SECONDS", 3))
    deadline = time.monotonic() + timeout
    last_step = None
    while True:
        # Child tasks are updated by other workers; avoid reusing stale ORM snapshots.
        db.session.expire_all()
        parent = _load_task(parent_id, PublicationTask.status)
        if not parent or parent.status == "canceled":
            raise RuntimeError("父编排任务已取消")
        child = _load_task(child_id, *TASK_CHILD_POLL_COLUMNS)
        if not child:
            raise RuntimeError(f"子任务不存在: {child_id}")
        child_snapshot = _task_state_snapshot(child) or {}
        child_step = str(child_snapshot.get("current_step") or child_snapshot.get("step") or "")
        child_status = str(child_snapshot.get("status") or "")
        if child_step != last_step:
            last_step = child_step
            child_progress = int(child_snapshot.get("progress") or 0)
            mapped = start_progress + int((end_progress - start_progress) * child_progress / 100)
            _emit(parent_id, f"{label}_{child_step}", mapped, f"{label}: {child_step}")
            _record_child(parent_id, label, child_snapshot)
        if child_status in TERMINAL_STATUSES:
            terminal_columns = (
                TASK_TTS_MATCH_COLUMNS
                if _is_doubao_child_label(label)
                else TASK_CHILD_COLUMNS
            )
            completed_child = _load_task(child_id, *terminal_columns) or child
            completed_snapshot = _task_state_snapshot(completed_child)
            _record_child(parent_id, label, completed_snapshot)
            _clear_waiting_child(parent_id, label)
            return _load_task(child_id, *terminal_columns) or completed_snapshot
        celery_failure = _celery_child_failure_message(child_snapshot)
        if celery_failure:
            failed_child = _load_task(child_id, *TASK_STATE_COLUMNS) or child
            _mark_child_failed(failed_child, celery_failure)
            db.session.expire_all()
            _clear_waiting_child(parent_id, label)
            return _load_task(
                child_id,
                *(TASK_TTS_MATCH_COLUMNS if _is_doubao_child_label(label) else TASK_CHILD_COLUMNS),
            )
        if _is_doubao_child_label(label) and _is_stale_doubao_child(child_snapshot):
            stale_child = _load_task(child_id, *TASK_STATE_COLUMNS) or child
            _mark_stale_doubao_child_if_needed(stale_child)
            db.session.expire_all()
            _clear_waiting_child(parent_id, label)
            return _load_task(child_id, *TASK_TTS_MATCH_COLUMNS)
        if label == "publisher_video":
            child = _load_task(child_id, *TASK_CHILD_POLL_COLUMNS) or child
            child, requeued = maybe_requeue_stale_publisher_child(child, log=logger)
            if requeued:
                _emit(parent_id, "publisher_video_requeued", start_progress, "重新入队视频合成子任务")
                _record_child(parent_id, label, child)
                last_step = None
        if time.monotonic() >= deadline:
            waiting_child = _load_task(child_id, *TASK_CHILD_POLL_COLUMNS) or child
            waiting_snapshot = _task_state_snapshot(waiting_child)
            _record_child(parent_id, label, waiting_snapshot)
            raise ChildTaskStillRunning(
                label,
                _task_id(waiting_snapshot) or child_id,
                str((waiting_snapshot or {}).get("status") or ""),
                str((waiting_snapshot or {}).get("current_step") or (waiting_snapshot or {}).get("step") or ""),
            )
        release_db_session_quietly()
        sleep_seconds = min(poll_seconds, max(0.2, deadline - time.monotonic()))
        time.sleep(sleep_seconds)


def _child_inline_wait_seconds(label):
    if _is_doubao_child_label(label):
        return max(0, _int_env("REPORT_AUDIO_DOUBAO_INLINE_WAIT_SECONDS", DEFAULT_DOUBAO_INLINE_WAIT_SECONDS))
    if label == "publisher_video":
        return max(0, _int_env("REPORT_AUDIO_PUBLISHER_INLINE_WAIT_SECONDS", DEFAULT_PUBLISHER_INLINE_WAIT_SECONDS))
    return max(1, _int_env("REPORT_AUDIO_CHILD_TIMEOUT_SECONDS", DEFAULT_CHILD_TIMEOUT_SECONDS))


def _parent_retry_delay_seconds(label):
    if _is_doubao_child_label(label):
        return max(5, _int_env("REPORT_AUDIO_DOUBAO_PARENT_RETRY_DELAY_SECONDS", DEFAULT_DOUBAO_PARENT_RETRY_DELAY_SECONDS))
    if label == "publisher_video":
        return max(5, _int_env("REPORT_AUDIO_PUBLISHER_PARENT_RETRY_DELAY_SECONDS", DEFAULT_PUBLISHER_PARENT_RETRY_DELAY_SECONDS))
    return max(30, _int_env("REPORT_AUDIO_PARENT_RETRY_DELAY_SECONDS", DEFAULT_PARENT_RETRY_DELAY_SECONDS))


def _parent_max_defers(label):
    if _is_doubao_child_label(label):
        return max(1, _int_env("REPORT_AUDIO_DOUBAO_PARENT_MAX_DEFERS", DEFAULT_DOUBAO_PARENT_MAX_DEFERS))
    if label == "publisher_video":
        return max(1, _int_env("REPORT_AUDIO_PUBLISHER_PARENT_MAX_DEFERS", DEFAULT_PUBLISHER_PARENT_MAX_DEFERS))
    return max(1, _int_env("REPORT_AUDIO_PARENT_MAX_DEFERS", DEFAULT_PARENT_MAX_DEFERS))


def _is_doubao_child_label(label):
    return str(label or "").startswith("doubao_browser_")


def _defer_parent_retry(parent_id, exc):
    delay = _parent_retry_delay_seconds(exc.label)
    max_defers = _parent_max_defers(exc.label)
    fresh = _cached_workflow_task(parent_id)
    if fresh is None:
        fresh = _load_task(parent_id, *TASK_STATE_COLUMNS)
    if not fresh or fresh.status == "canceled":
        return

    result = _fresh_result(parent_id)
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
        "defer_count": count,
        "max_defers": max_defers,
        "updated_at": datetime.utcnow().isoformat() + "Z",
    }
    deferred_waits[exc.label] = waiting_child
    result["waiting_child"] = waiting_child
    result["deferred_waits"] = deferred_waits
    step_code = _current_step_code(f"{exc.label}_waiting")
    progress_value = max(int(fresh.progress or 0), 95 if exc.label == "publisher_video" else int(fresh.progress or 0))
    if _is_postgresql_session():
        _execute_parent_update(
            """
            UPDATE public.publication_tasks
            SET status = 'processing',
                current_step = :step,
                progress = :progress,
                error_message = NULL,
                result = COALESCE(result, '{}'::jsonb) || CAST(:patch AS jsonb),
                waiting_child_label = :waiting_label,
                waiting_child_id = :waiting_id,
                waiting_child_status = :waiting_status,
                waiting_child_step = :waiting_step,
                waiting_child_updated_at = :waiting_updated_at,
                updated_at = NOW()
            WHERE id = :task_id AND status IS DISTINCT FROM 'canceled'
            """,
            {
                "task_id": str(parent_id),
                "step": step_code,
                "progress": progress_value,
                "patch": json.dumps({
                    "waiting_child": waiting_child,
                    "deferred_waits": deferred_waits,
                }, ensure_ascii=False),
                "waiting_label": str(exc.label),
                "waiting_id": str(exc.child_id),
                "waiting_status": str(exc.status or "") or None,
                "waiting_step": str(exc.step or "") or None,
                "waiting_updated_at": waiting_child["updated_at"],
            },
            f"defer parent {parent_id}:{exc.label}",
        )
    else:
        _persist_parent_result_fallback(
            parent_id,
            result,
            status="processing",
            current_step=step_code,
            progress=progress_value,
            error_message=None,
            waiting_child_label=str(exc.label),
            waiting_child_id=str(exc.child_id),
            waiting_child_status=str(exc.status or "") or None,
            waiting_child_step=str(exc.step or "") or None,
            waiting_child_updated_at=waiting_child["updated_at"],
        )
    _replace_cached_workflow_result(parent_id, result)
    _merge_cached_workflow_fields(
        parent_id,
        status="processing",
        current_step=step_code,
        progress=progress_value,
        error_message=None,
    )

    # Persist the replacement delivery id.  The task entry point rejects stale
    # Celery deliveries, so leaving the previous request id on the parent would
    # make every legitimate deferred resume look stale and strand the workflow
    # after its child has already completed.
    async_result = apply_async_after_db_release(
        process_report_audio_workflow_job,
        args=[parent_id],
        queue=WORKFLOW_QUEUE_NAME,
        countdown=delay,
        retry=False,
    )
    _persist_child_celery_task_id(str(parent_id), str(async_result.id))
    _merge_cached_workflow_fields(parent_id, celery_task_id=str(async_result.id))


def _clear_waiting_child(parent_id, label):
    result = _cached_workflow_result(parent_id)
    if result is None:
        fresh = _load_task(parent_id, *TASK_STATE_COLUMNS)
        if not fresh or not isinstance(fresh.result, dict):
            return
        result = dict(fresh.result or {})
    waiting_child = result.get("waiting_child")
    if not isinstance(waiting_child, dict) or waiting_child.get("label") != label:
        return
    result.pop("waiting_child", None)
    if _is_postgresql_session():
        _execute_parent_update(
            """
            UPDATE public.publication_tasks
            SET result = COALESCE(result, '{}'::jsonb) - 'waiting_child',
                waiting_child_label = NULL,
                waiting_child_id = NULL,
                waiting_child_status = NULL,
                waiting_child_step = NULL,
                waiting_child_updated_at = NULL,
                updated_at = NOW()
            WHERE id = :task_id AND status IS DISTINCT FROM 'canceled'
            """,
            {"task_id": str(parent_id)},
            f"clear waiting child {parent_id}:{label}",
        )
    else:
        _persist_parent_result_fallback(parent_id, result)
    _replace_cached_workflow_result(parent_id, result)


def _celery_child_failure_message(child):
    celery_task_id = _safe_task_attr(child, "celery_task_id") or _safe_task_attr(child, "celery_id")
    if not celery_task_id:
        return None
    try:
        async_result = celery_app.AsyncResult(celery_task_id)
        state = async_result.state
    except Exception as exc:
        logger.warning("读取 Celery 子任务状态失败: child=%s error=%s", _task_id(child), exc)
        return None
    if state not in CELERY_FAILURE_STATES:
        return None
    detail = ""
    try:
        raw = async_result.result
        detail = str(raw) if raw else ""
    except Exception as exc:
        detail = str(exc)
    if detail:
        return f"Celery 子任务异常退出: {state}: {detail}"
    return f"Celery 子任务异常退出: {state}"


def _mark_child_failed(child, message):
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


def _record_child(parent_id, label, child):
    result = _fresh_result(parent_id)
    children = dict(result.get("children") or {})
    previous = children.get(label) if isinstance(children.get(label), dict) else {}
    child_summary = _merge_child_summary(previous, _task_public_summary(child) or {})
    children[label] = child_summary
    result["children"] = children
    if _is_postgresql_session():
        _execute_parent_update(
            """
            UPDATE public.publication_tasks
            SET result = jsonb_set(
                    COALESCE(result, '{}'::jsonb),
                    '{children}',
                    COALESCE(result->'children', '{}'::jsonb)
                        || jsonb_build_object(CAST(:label AS text), CAST(:child AS jsonb)),
                    true
                ),
                updated_at = NOW()
            WHERE id = :task_id AND status IS DISTINCT FROM 'canceled'
            """,
            {
                "task_id": str(parent_id),
                "label": str(label),
                "child": json.dumps(child_summary, ensure_ascii=False),
            },
            f"record child {parent_id}:{label}",
        )
    else:
        _persist_parent_result_fallback(parent_id, result)
    _replace_cached_workflow_result(parent_id, result)


def _record_child_attempt(parent_id, label, child, attempt, max_attempts, status, *, error=None):
    result = _fresh_result(parent_id)
    attempts = dict(result.get("child_attempts") or {})
    items = list(attempts.get(label) or [])
    summary = _task_public_summary(child) or {}
    updated_at = datetime.utcnow().isoformat() + "Z"
    summary.update({
        "attempt": int(attempt or 1),
        "max_attempts": int(max_attempts or 1),
        "attempt_status": status,
        "error": error or summary.get("error"),
        "updated_at": updated_at,
    })
    items = [item for item in items if int(item.get("attempt") or 0) != int(attempt or 1)]
    items.append(summary)
    attempts[label] = items[-int(max_attempts or 1):]
    result["child_attempts"] = attempts
    retry_state = dict(result.get("retry_state") or {})
    retry_state[label] = {
        "attempt": int(attempt or 1),
        "max_attempts": int(max_attempts or 1),
        "status": status,
        "error": error or summary.get("error"),
        "updated_at": updated_at,
    }
    result["retry_state"] = retry_state
    _update_result(parent_id, {
        "child_attempts": attempts,
        "retry_state": retry_state,
    })


def _update_result(parent_id, values):
    if not isinstance(values, dict) or not values:
        return
    if _is_postgresql_session():
        _execute_parent_update(
            """
            WITH merged AS (
                SELECT
                    id,
                    COALESCE(result, '{}'::jsonb) || CAST(:patch AS jsonb) AS payload
                FROM public.publication_tasks
                WHERE id = :task_id AND status IS DISTINCT FROM 'canceled'
                FOR UPDATE
            )
            UPDATE public.publication_tasks AS task
            SET
                result = merged.payload,
                report_audio_result_pdf_filename = LEFT(NULLIF(
                    merged.payload #>> '{pdf,filename}', ''
                ), 500),
                report_audio_result_pdf_bytes = LEFT(NULLIF(
                    merged.payload #>> '{pdf,bytes}', ''
                ), 40),
                report_audio_result_pdf_topic_title = LEFT(NULLIF(
                    merged.payload #>> '{pdf,topic_title}', ''
                ), 500),
                report_audio_cover_title = LEFT(NULLIF(
                    merged.payload #>> '{structured_result,cover_title}', ''
                ), 500),
                report_audio_cover_subtitle = LEFT(COALESCE(
                    NULLIF(merged.payload #>> '{structured_result,cover_subtitle}', ''),
                    NULLIF(merged.payload #>> '{video,cover_subtitle}', '')
                ), 220),
                report_audio_structured_title = LEFT(NULLIF(
                    merged.payload #>> '{structured_result,title}', ''
                ), 500),
                report_audio_started_at = LEFT(NULLIF(merged.payload ->> 'started_at', ''), 64),
                report_audio_completed_at = LEFT(NULLIF(merged.payload ->> 'completed_at', ''), 64),
                report_audio_error = NULLIF(merged.payload ->> 'error', ''),
                report_audio_output_path = LEFT(NULLIF(merged.payload ->> 'output_path', ''), 600),
                report_audio_wav_path = LEFT(NULLIF(
                    merged.payload #>> '{audio,wav_path}', ''
                ), 600),
                report_audio_seconds = LEFT(NULLIF(
                    merged.payload #>> '{audio,seconds}', ''
                ), 40),
                report_audio_audio_bytes = LEFT(NULLIF(
                    merged.payload #>> '{audio,bytes}', ''
                ), 40),
                report_audio_video_enabled = CASE
                    WHEN LOWER(merged.payload #>> '{video,enabled}') IN ('1', 'true', 'yes', 'on') THEN TRUE
                    WHEN LOWER(merged.payload #>> '{video,enabled}') IN ('0', 'false', 'no', 'off') THEN FALSE
                    ELSE NULL
                END,
                report_audio_video_status = LEFT(NULLIF(
                    merged.payload #>> '{video,status}', ''
                ), 80),
                report_audio_video_publisher_task_id = LEFT(NULLIF(
                    merged.payload #>> '{video,publisher_task_id}', ''
                ), 36),
                report_audio_video_message = NULLIF(merged.payload #>> '{video,message}', ''),
                report_audio_video_path = LEFT(NULLIF(
                    merged.payload #>> '{video,video_path}', ''
                ), 600),
                report_audio_cover_path = LEFT(NULLIF(
                    merged.payload #>> '{video,cover_path}', ''
                ), 600),
                report_audio_landscape_keyframe_path = LEFT(NULLIF(
                    merged.payload #>> '{video,keyframes,landscape_cover,path}', ''
                ), 600),
                report_audio_vertical_cover_path = LEFT(COALESCE(
                    NULLIF(merged.payload #>> '{video,vertical_cover_path}', ''),
                    NULLIF(merged.payload #>> '{video,keyframes,vertical_cover,path}', '')
                ), 600),
                report_audio_podcast_cover_path = LEFT(COALESCE(
                    NULLIF(merged.payload #>> '{video,podcast_cover_path}', ''),
                    NULLIF(merged.payload #>> '{video,keyframes,podcast_cover,path}', '')
                ), 600),
                report_audio_bilibili_cover_path = LEFT(COALESCE(
                    NULLIF(merged.payload #>> '{video,bilibili_cover_path}', ''),
                    NULLIF(merged.payload #>> '{video,keyframes,bilibili_cover,path}', '')
                ), 600),
                final_script_chars = CASE
                    WHEN COALESCE(
                        NULLIF(merged.payload #>> '{structured_result,final_script_for_tts}', ''),
                        NULLIF(merged.payload ->> 'final_script_for_tts', ''),
                        NULLIF(merged.payload #>> '{script_material,final_script_for_tts}', '')
                    ) IS NOT NULL THEN CHAR_LENGTH(BTRIM(COALESCE(
                        NULLIF(merged.payload #>> '{structured_result,final_script_for_tts}', ''),
                        NULLIF(merged.payload ->> 'final_script_for_tts', ''),
                        NULLIF(merged.payload #>> '{script_material,final_script_for_tts}', '')
                    )))
                    WHEN COALESCE(merged.payload #>> '{script_material,chars}', '') ~ '^[0-9]+$'
                        THEN (merged.payload #>> '{script_material,chars}')::integer
                    ELSE 0
                END,
                updated_at = NOW()
            FROM merged
            WHERE task.id = merged.id
            """,
            {
                "task_id": str(parent_id),
                "patch": json.dumps(values, ensure_ascii=False),
            },
            f"update result {parent_id}",
        )
    else:
        result = _fresh_result(parent_id)
        result.update(copy.deepcopy(values))
        _persist_parent_result_fallback(parent_id, result)
    _merge_cached_workflow_result(parent_id, values)


def _child_error_message(child):
    snapshot = _task_state_snapshot(child)
    if not snapshot:
        return "子任务不存在"
    result = snapshot.get("result") if isinstance(snapshot.get("result"), dict) else {}
    return (
        snapshot.get("error_message")
        or snapshot.get("error")
        or result.get("error")
        or f"子任务状态异常: {snapshot.get('status') or '-'} / {snapshot.get('current_step') or snapshot.get('step') or '-'}"
    )


def _is_retryable_child_error(label, message):
    text = str(message or "")
    if not text:
        return False
    lower_text = text.lower()
    if any(fragment.lower() in lower_text for fragment in NON_RETRYABLE_CHILD_ERROR_FRAGMENTS):
        return False
    return any(fragment.lower() in lower_text for fragment in RETRYABLE_CHILD_ERROR_FRAGMENTS)


def _child_failure_classification(child):
    message = _child_error_message(child).lower()
    if "cannot import name" in message or "modulenotfounderror" in message or "no module named" in message:
        return "worker_code_mismatch"
    return None


def _retry_limit(env_name, default):
    return max(0, min(_int_env(env_name, default), 5))


def _revoke_child_task(child):
    child_id = str(getattr(child, "id", None) or "-")
    celery_task_id = str(getattr(child, "celery_task_id", None) or "").strip()
    if not celery_task_id:
        return
    revoke_celery_task_after_db_commit(
        celery_task_id,
        operation=f"report audio child revoke child={child_id}",
        business_task_id=child_id,
        log=logger,
    )


def _compact_error(message, limit=180):
    text = re.sub(r"\s+", " ", str(message or "")).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _emit(parent_id, step, progress, message):
    result = _fresh_result(parent_id)
    logs = list(result.get("logs") or [])
    log_entry = {
        "step": step,
        "message": message,
        "progress": progress,
        "updated_at": datetime.utcnow().isoformat() + "Z",
    }
    logs.append(log_entry)
    result["logs"] = logs[-120:]
    step_code = _current_step_code(step)
    progress_value = max(0, min(100, int(progress or 0)))
    if _is_postgresql_session():
        _execute_parent_update(
            """
            UPDATE public.publication_tasks
            SET current_step = :step,
                progress = :progress,
                result = jsonb_set(
                    COALESCE(result, '{}'::jsonb),
                    '{logs}',
                    CASE
                        WHEN jsonb_array_length(COALESCE(result->'logs', '[]'::jsonb)) >= 120
                        THEN (COALESCE(result->'logs', '[]'::jsonb) #- '{0}') || CAST(:entry AS jsonb)
                        ELSE COALESCE(result->'logs', '[]'::jsonb) || CAST(:entry AS jsonb)
                    END,
                    true
                ),
                updated_at = NOW()
            WHERE id = :task_id AND status IS DISTINCT FROM 'canceled'
            """,
            {
                "task_id": str(parent_id),
                "step": step_code,
                "progress": progress_value,
                "entry": json.dumps([log_entry], ensure_ascii=False),
            },
            f"emit progress {parent_id}:{step}",
        )
    else:
        _persist_parent_result_fallback(
            parent_id,
            result,
            current_step=step_code,
            progress=progress_value,
        )
    _replace_cached_workflow_result(parent_id, result)
    _merge_cached_workflow_fields(parent_id, current_step=step_code, progress=progress_value)


def _current_step_code(step):
    raw = str(step or "running")
    alias = STEP_CODE_ALIASES.get(raw)
    if alias:
        return alias[:CURRENT_STEP_MAX_LENGTH]
    if len(raw) <= CURRENT_STEP_MAX_LENGTH:
        return raw

    if raw.startswith("submit_doubao_browser_"):
        index = raw.rsplit("_", 1)[-1]
        return f"doubao_{index}_submit"[:CURRENT_STEP_MAX_LENGTH]

    if raw.startswith("doubao_browser_"):
        parts = raw.split("_", 3)
        if len(parts) == 4:
            index = parts[2]
            child_step = parts[3]
            child_code = DOUBAO_STEP_ALIASES.get(child_step) or _safe_step_segment(child_step, 8)
            return f"doubao_{index}_{child_code}"[:CURRENT_STEP_MAX_LENGTH]

    if raw.startswith("publisher_video_"):
        child_step = raw[len("publisher_video_"):]
        child_code = PUBLISHER_STEP_ALIASES.get(child_step) or _safe_step_segment(child_step, 10)
        return f"pub_{child_code}"[:CURRENT_STEP_MAX_LENGTH]

    if raw.startswith("gemini_followup_"):
        parts = raw.split("_")
        if len(parts) >= 4:
            followup_index = parts[2]
            action = _safe_step_segment("_".join(parts[3:]), 8)
            return f"gem_fup{followup_index}_{action}"[:CURRENT_STEP_MAX_LENGTH]

    if raw.startswith("gemini_"):
        return f"gem_{_safe_step_segment(raw[len('gemini_'):], 16)}"[:CURRENT_STEP_MAX_LENGTH]

    return _safe_step_segment(raw, CURRENT_STEP_MAX_LENGTH)


def _safe_step_segment(value, max_length):
    text = str(value or "running")
    safe = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in text)
    safe = safe.strip("_") or "running"
    return safe[:max_length]


def _landscape_video_enabled(payload):
    video_options = payload.get("video") if isinstance(payload.get("video"), dict) else {}
    return _truthy(video_options.get("enabled", False))


def _vertical_video_enabled(payload):
    video_options = payload.get("video") if isinstance(payload.get("video"), dict) else {}
    return _truthy(video_options.get("vertical_enabled", False))


def _video_stage_enabled(payload):
    video_options = payload.get("video") if isinstance(payload.get("video"), dict) else {}
    return (
        _landscape_video_enabled(payload)
        or _vertical_video_enabled(payload)
        or (
            _video_render_mode(payload) == "keyframes"
            and bool(_normalize_keyframe_outputs(video_options.get("keyframe_outputs")))
        )
    )


def _video_render_mode(payload):
    video_options = payload.get("video") if isinstance(payload.get("video"), dict) else {}
    raw = str(
        video_options.get("render_mode")
        or video_options.get("video_render_mode")
        or video_options.get("output_mode")
        or "full_video"
    ).strip().lower()
    if raw in {"keyframe", "keyframes", "frames", "preview_keyframes"}:
        return "keyframes"
    if raw in {"preview", "short", "short_video", "preview_10s", "10s", "10sec", "ten_seconds"}:
        return "preview_10s"
    return "full_video"


def _video_preview_seconds(video_options):
    raw = (
        video_options.get("preview_seconds")
        or video_options.get("duration_seconds")
        or video_options.get("short_video_seconds")
        or 10
    )
    try:
        value = int(float(raw))
    except (TypeError, ValueError):
        value = 10
    return max(3, min(value, 60))


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


def _generate_report_audio_keyframes(parent_id, created_at, *, structured, pdf, audio, pdf_screenshot, payload):
    video_options = payload.get("video") if isinstance(payload.get("video"), dict) else {}
    requested_outputs = _normalize_keyframe_outputs(video_options.get("keyframe_outputs"))
    explicit_output_selection = bool(requested_outputs)
    if explicit_output_selection:
        vertical_schemes = [
            scheme
            for scheme in ("scheme1", "scheme2", "scheme3")
            if f"vertical_keyframe_{scheme}" in requested_outputs
        ]
    else:
        vertical_schemes = _normalize_vertical_schemes(
            video_options.get("vertical_video_schemes"),
            video_options.get("vertical_video_scheme") or video_options.get("vertical_scheme") or "scheme1",
        )
    scheme = _primary_vertical_scheme(vertical_schemes)
    landscape_keyframe_requested = (
        "landscape_keyframe" in requested_outputs
        if explicit_output_selection
        else _truthy(video_options.get("enabled", False))
    )
    vertical_keyframes_requested = (
        bool(vertical_schemes)
        if explicit_output_selection
        else _vertical_keyframes_requested(video_options, vertical_schemes)
    )
    vertical_cover_requested = (
        "vertical_cover" in requested_outputs
        if explicit_output_selection
        else vertical_keyframes_requested and "scheme2" in vertical_schemes
    )
    podcast_cover_requested = (
        False
        if explicit_output_selection
        else vertical_keyframes_requested and "scheme2" in vertical_schemes
    )
    bilibili_cover_requested = (
        "bilibili_cover" in requested_outputs
        if explicit_output_selection
        else True
    )
    main_title = _publisher_main_title(structured, video_options, variant="landscape")
    sub_title = _publisher_sub_title(structured, video_options, variant="landscape")
    vertical_main_title = _publisher_main_title(structured, video_options, variant="vertical")
    vertical_sub_title = _publisher_sub_title(structured, video_options, variant="vertical")
    date_text = str(video_options.get("date_text") or "").strip() or _publisher_date_text(created_at)
    tag_text = str(video_options.get("tag_text") or "").strip() or "研报播客"
    screenshot_path = pdf_screenshot.get("path") if isinstance(pdf_screenshot, dict) else None
    transform = {
        "anchor": "top-left",
        "title_max_lines": 2,
        "subtitle_max_lines": 1,
        **(video_options.get("vertical_image_transform") if isinstance(video_options.get("vertical_image_transform"), dict) else {}),
    }
    institution = _publisher_workbench_institution(structured, pdf, video_options)
    main_title = _publisher_cover_title_with_institution(main_title, institution)
    vertical_main_title = _publisher_cover_title_with_institution(vertical_main_title, institution)
    footer_text = str(video_options.get("vertical_footer_text") or "听见资本").strip() or "听见资本"

    keyframes = {}
    cover_path = None
    vertical_background_path = None
    vertical_background_paths = {}
    vertical_cover_path = None
    podcast_cover_path = None
    bilibili_cover_path = None

    if landscape_keyframe_requested:
        cover_path = _latest_report_audio_keyframe(parent_id, "landscape", (1920, 1080))
        if not cover_path:
            cover_path = thumbnail_service.generate_thumbnail(
                main_title=main_title,
                sub_title=sub_title,
                date_text=date_text,
                tag_text=tag_text,
                bg_preset_index=int(video_options.get("bg_index") or 0),
                layout="left",
                output_filename=f"report_audio_keyframe_landscape_{parent_id[:8]}_{int(time.time())}.jpg",
                custom_bg_path=screenshot_path,
                overlay_opacity=float(video_options.get("overlay_opacity") or 0.5),
                transform=video_options.get("cover_transform") or {"x": 0, "y": 0, "scale": 1},
                mode="screenshot",
            )
        keyframes["landscape_cover"] = _keyframe_artifact(cover_path, "横版视频关键帧", "16:9")

    if vertical_keyframes_requested:
        for selected_scheme in vertical_schemes:
            asset_name = f"vertical_bg_{selected_scheme}"
            selected_background_path = _latest_report_audio_keyframe(parent_id, asset_name, (1080, 1920))
            if not selected_background_path and selected_scheme == "scheme3":
                selected_background_path = thumbnail_service.generate_workbench_video_background(
                    main_title=vertical_main_title,
                    sub_title=vertical_sub_title,
                    output_filename=f"report_audio_keyframe_{asset_name}_{parent_id[:8]}_{int(time.time())}.jpg",
                    institution=institution,
                    emotion="bull",
                    footer_text=footer_text,
                )
            elif not selected_background_path and selected_scheme == "scheme2":
                selected_background_path = thumbnail_service.generate_workbench_vertical_thumbnail(
                    main_title=vertical_main_title,
                    sub_title=vertical_sub_title,
                    screenshot_path=screenshot_path,
                    output_filename=f"report_audio_keyframe_{asset_name}_{parent_id[:8]}_{int(time.time())}.jpg",
                    institution=institution,
                    emotion="bull",
                    footer_text=footer_text,
                    image_transform=transform,
                )
            elif not selected_background_path:
                selected_background_path = thumbnail_service.generate_vertical_thumbnail(
                    main_title=vertical_main_title,
                    sub_title=vertical_sub_title,
                    date_text=date_text,
                    screenshot_path=screenshot_path,
                    output_filename=f"report_audio_keyframe_{asset_name}_{parent_id[:8]}_{int(time.time())}.jpg",
                )
            vertical_background_paths[selected_scheme] = selected_background_path
            key = "vertical_background" if selected_scheme == scheme else f"vertical_background_{selected_scheme}"
            keyframes[key] = _keyframe_artifact(
                selected_background_path,
                f"{'方案三' if selected_scheme == 'scheme3' else ('方案二' if selected_scheme == 'scheme2' else '方案一')}竖屏背景关键帧",
                "9:16",
            )

        vertical_background_path = vertical_background_paths.get(scheme)

    if vertical_cover_requested:
        vertical_cover_path = _latest_report_audio_keyframe(parent_id, "vertical_cover", (1080, 1440))
        if not vertical_cover_path:
            vertical_cover_path = thumbnail_service.generate_workbench_vertical_cover(
                main_title=vertical_main_title,
                sub_title=vertical_sub_title,
                screenshot_path=screenshot_path,
                output_filename=f"report_audio_keyframe_vertical_cover_{parent_id[:8]}_{int(time.time())}.jpg",
                institution=institution,
                emotion="bull",
                footer_text=footer_text,
                image_transform=transform,
            )
        keyframes["vertical_cover"] = _keyframe_artifact(vertical_cover_path, "竖版封皮", "3:4")

    if podcast_cover_requested:
        podcast_cover_path = _latest_report_audio_keyframe(parent_id, "podcast_cover", (1400, 1400))
        if not podcast_cover_path:
            podcast_cover_path = thumbnail_service.generate_workbench_podcast_cover(
                main_title=vertical_main_title,
                sub_title=vertical_sub_title,
                screenshot_path=screenshot_path,
                output_filename=f"report_audio_keyframe_podcast_cover_{parent_id[:8]}_{int(time.time())}.jpg",
                institution=institution,
                emotion="bull",
                footer_text=footer_text,
                image_transform=transform,
            )
        keyframes["podcast_cover"] = _keyframe_artifact(podcast_cover_path, "播客封皮关键帧", "1:1")

    bilibili_transform = {
        **transform,
        **(
            video_options.get("bilibili_cover_image_transform")
            if isinstance(video_options.get("bilibili_cover_image_transform"), dict)
            else {}
        ),
    }
    if bilibili_cover_requested:
        bilibili_cover_path = _latest_report_audio_keyframe(parent_id, "bilibili_cover", (1146, 717))
        if not bilibili_cover_path:
            bilibili_cover_path = thumbnail_service.generate_workbench_bilibili_cover(
                main_title=vertical_main_title,
                sub_title=vertical_sub_title,
                screenshot_path=screenshot_path,
                output_filename=f"report_audio_keyframe_bilibili_cover_{parent_id[:8]}_{int(time.time())}.jpg",
                institution=institution,
                emotion="bull",
                footer_text=footer_text,
                image_transform=bilibili_transform,
                show_footer=False,
            )
        keyframes["bilibili_cover"] = _keyframe_artifact(bilibili_cover_path, "横版封皮", "1146:717")

    return {
        "enabled": True,
        "status": "keyframes_ready",
        "render_mode": "keyframes",
        "message": "仅生成关键帧/封皮预览，未合成 MP4",
        "landscape_enabled": landscape_keyframe_requested,
        "vertical_enabled": vertical_keyframes_requested or vertical_cover_requested,
        "keyframe_outputs": requested_outputs,
        "cover_title": main_title,
        "cover_subtitle": sub_title,
        "landscape_title": main_title,
        "landscape_subtitle": sub_title,
        "vertical_title": vertical_main_title,
        "vertical_subtitle": vertical_sub_title,
        "vertical_video_scheme": scheme,
        "vertical_video_schemes": vertical_schemes,
        "cover_path": cover_path,
        "vertical_background_path": vertical_background_path,
        "vertical_background_paths": vertical_background_paths,
        "vertical_cover_path": vertical_cover_path,
        "podcast_cover_path": podcast_cover_path,
        "bilibili_cover_path": bilibili_cover_path,
        "pdf_first_page_image": pdf_screenshot,
        "keyframes": {key: value for key, value in keyframes.items() if value},
        "audio_seconds": (audio or {}).get("seconds"),
    }


def _keyframe_artifact(path, label, ratio):
    if not path or not os.path.exists(path):
        return None
    artifact = {
        "path": path,
        "label": label,
        "ratio": ratio,
        "bytes": os.path.getsize(path),
    }
    try:
        from PIL import Image

        with Image.open(path) as image:
            artifact["width"] = image.width
            artifact["height"] = image.height
    except Exception:
        pass
    return artifact


def _latest_report_audio_keyframe(parent_id, asset_name, expected_size):
    """Reuse a valid artifact after a partial keyframe-stage failure."""
    output_dir = getattr(thumbnail_service, "output_dir", None)
    if not output_dir:
        return None
    prefix = f"report_audio_keyframe_{asset_name}_{str(parent_id)[:8]}_"
    candidates = sorted(
        Path(output_dir).glob(f"{prefix}*.jpg"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for path in candidates:
        try:
            from PIL import Image

            with Image.open(path) as image:
                if image.size == tuple(expected_size):
                    image.verify()
                    return str(path)
        except Exception:
            continue
    return None


def _normalize_vertical_scheme(value):
    text = str(value or "").strip().lower()
    if text in {"scheme3", "3", "方案三", "visual_frame", "visual-frame", "video_bg", "video-bg"}:
        return "scheme3"
    if text in {"scheme2", "2", "方案二", "workbench", "thumbnail_workbench", "thumbnail-workbench"}:
        return "scheme2"
    return "scheme1"


def _normalize_vertical_schemes(value, fallback=None):
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
        normalized = _normalize_vertical_scheme(item)
        if normalized not in schemes:
            schemes.append(normalized)
    if not schemes and fallback is not None:
        schemes = [_normalize_vertical_scheme(fallback)]
    return schemes


def _vertical_keyframes_requested(video_options, vertical_schemes):
    if _truthy((video_options or {}).get("vertical_enabled", False)):
        return True
    # 方案一在页面上默认选中，不能单独代表用户要求竖版；方案二/三是显式选择。
    explicitly_selected = _normalize_vertical_schemes(
        (video_options or {}).get("vertical_video_schemes"),
        fallback=None,
    )
    return any(scheme in {"scheme2", "scheme3"} for scheme in explicitly_selected)


def _primary_vertical_scheme(schemes):
    normalized = _normalize_vertical_schemes(schemes, "scheme1")
    if "scheme3" in normalized:
        return "scheme3"
    if "scheme1" in normalized and "scheme2" in normalized:
        return "scheme2"
    return normalized[0]


def _is_generic_cover_title_override(value):
    text = _single_line(value).strip()
    if not text:
        return False
    compact = re.sub(r"[\s#＃:：|｜/_\\-]+", "", text).lower()
    if compact in GENERIC_COVER_TITLE_NORMALIZED:
        return True
    if text.startswith(("#", "＃")) and len(compact) <= 6 and not re.search(r"\d", compact):
        return True
    return False


def _publisher_main_title(structured, video_options, *, variant="landscape"):
    is_vertical = str(variant or "").lower() == "vertical"
    override = str(
        (video_options.get("vertical_title") if is_vertical else video_options.get("landscape_title"))
        or video_options.get("title")
        or video_options.get("main_title")
        or ""
    ).strip()
    if override and not _is_generic_cover_title_override(override):
        return _format_cover_title(override)
    variant_title = str((structured or {}).get("vertical_title" if is_vertical else "landscape_title") or "").strip()
    if variant_title:
        return _format_cover_title(variant_title)
    cover_title = str((structured or {}).get("cover_title") or "").strip()
    if cover_title:
        return _format_cover_title(cover_title)
    title = str((structured or {}).get("title") or "").strip()
    if title:
        return _format_cover_title(title)
    titles = (structured or {}).get("titles") if isinstance((structured or {}).get("titles"), dict) else {}
    for key in ("short", "standard"):
        values = titles.get(key)
        if isinstance(values, list) and values:
            return _format_cover_title(str(values[0]))
    if override:
        return _format_cover_title(override)
    return "研报核心观点解读"


def _publisher_sub_title(structured, video_options, *, variant="landscape"):
    is_vertical = str(variant or "").lower() == "vertical"
    override = str(
        (video_options.get("vertical_subtitle") if is_vertical else video_options.get("landscape_subtitle"))
        or video_options.get("subtitle")
        or video_options.get("sub_title")
        or ""
    ).strip()
    if override:
        return _format_cover_subtitle(override)
    variant_subtitle = str((structured or {}).get("vertical_subtitle" if is_vertical else "landscape_subtitle") or "").strip()
    if variant_subtitle:
        return _format_cover_subtitle(variant_subtitle)
    cover_subtitle = str((structured or {}).get("cover_subtitle") or "").strip()
    if cover_subtitle:
        return _format_cover_subtitle(cover_subtitle)
    summary = str((structured or {}).get("summary") or "").strip()
    if summary:
        return _format_cover_subtitle(summary)
    notes = (structured or {}).get("risk_notes")
    if isinstance(notes, list) and notes:
        return _format_cover_subtitle(str(notes[0]))
    return "AI 自审整理后的研报音频版"


def _publisher_date_text(created_at):
    created_at = created_at or datetime.utcnow()
    return created_at.strftime("%b %Y") + " | 研报播客"


def _publisher_workbench_institution(structured, pdf, video_options):
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


def _publisher_cover_title_with_institution(title, institution):
    label = {
        "GF Securities (Hong Kong)": "香港广发",
        "GF Securities": "广发证券",
        "Morgan Stanley": "大摩",
        "J.P. Morgan": "摩根大通",
        "Goldman Sachs": "高盛",
        "UBS": "瑞银",
        "Nomura": "野村",
        "BofA": "美银",
        "HSBC": "汇丰",
        "Jefferies": "杰富瑞",
        "Bernstein": "伯恩斯坦",
        "深度研报": "机构",
    }.get(str(institution or "").strip(), str(institution or "").strip() or "机构")
    raw = str(title or "").replace("\\r\\n", "\n").replace("\\n", "\n").replace("\r", "\n")
    lines = raw.splitlines() or ["研报核心观点解读"]
    first_line, generic_prefix_count = re.subn(
        r"^\s*(?:(?:机构研报|机构|深度研报)\s*[:：]\s*)+",
        "",
        lines[0],
        flags=re.I,
    )
    lines[0] = first_line
    compact = _single_line("\n".join(lines))
    matched_alias = next(
        (
            (alias, short_label)
            for alias, short_label in BANK_ALIASES
            if re.search(re.escape(alias), compact, flags=re.I)
        ),
        None,
    )
    if matched_alias and matched_alias[1] == label:
        if not generic_prefix_count:
            return _format_cover_title(raw)
        normalized = re.sub(
            re.escape(matched_alias[0]),
            label,
            "\n".join(lines),
            count=1,
            flags=re.I,
        )
        normalized = re.sub(rf"^{re.escape(label)}\s*[:：]\s*", f"{label}：", normalized, count=1)
        formatted = _format_cover_title(normalized)
        return re.sub(rf"^{re.escape(label)}:", f"{label}：", formatted, count=1)

    if matched_alias:
        lines = re.sub(
            re.escape(matched_alias[0]),
            "",
            "\n".join(lines),
            count=1,
            flags=re.I,
        ).splitlines()
    first_line = lines[0].strip(" |｜/：:") or "研报核心观点解读"
    formatted = _format_cover_title("\n".join([f"{label}：{first_line}", *lines[1:]]))
    return re.sub(rf"^{re.escape(label)}:", f"{label}：", formatted, count=1)


def _publisher_description(structured, pdf):
    title = str((structured or {}).get("title") or "").strip()
    summary = str((structured or {}).get("summary") or "").strip()
    filename = (pdf or {}).get("filename") or ""
    parts = [part for part in [title, summary, f"来源 PDF：{filename}" if filename else ""] if part]
    return "\n\n".join(parts)[:1800] if parts else "由研报转语音编排自动生成。"


def _publisher_tags(video_options):
    raw = video_options.get("bilibili_tags") or video_options.get("tags")
    if isinstance(raw, list):
        values = [str(item).strip() for item in raw if str(item).strip()]
    elif isinstance(raw, str) and raw.strip():
        values = [item.strip() for item in raw.replace("，", ",").split(",") if item.strip()]
    else:
        values = ["财经", "研报", "投资"]
    return values[:10]


def _publisher_bilibili_charging_pay(video_options):
    enabled = _truthy(video_options.get("bilibili_charging_pay", False))
    preview_end = video_options.get("bilibili_charging_preview_end")
    try:
        preview_end = int(preview_end or 5)
    except (TypeError, ValueError):
        preview_end = 5

    return {
        "enabled": enabled,
        "upower_mode": 0,
        "preview": {
            "need_preview": 1,
            "start_time": 0,
            "end_time": preview_end,
        },
        "qa": {"qa_enabled": 0, "qa_id": None},
        "member_first": {"enabled": 0, "exp_time": 0},
    }


def _publisher_bilibili_delay_time(video_options):
    raw = (
        video_options.get("bilibili_delay_time")
        or video_options.get("bilibili_dtime")
        or video_options.get("delay_time")
    )
    if not raw:
        return None
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return None


def _publisher_bilibili_season_id(video_options):
    raw = video_options.get("bilibili_season_id") or video_options.get("season_id")
    if not raw:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _publisher_cta(video_options):
    if not _truthy(video_options.get("cta_enabled", True)):
        return None
    return {
        "text": str(video_options.get("cta_text") or "简介查看研报资料入口\n每日更新100+外资研报与机构调研"),
        "position": str(video_options.get("cta_position") or "bottom_right"),
        "style": str(video_options.get("cta_style") or "police_yellow"),
    }


def _publisher_video_transform(video_options):
    transform = video_options.get("video_transform") if isinstance(video_options, dict) else None
    if not isinstance(transform, dict):
        return None
    try:
        scale = float(transform.get("scale", 1) or 1)
        offset_x = float(transform.get("x", 0) or 0)
        offset_y = float(transform.get("y", 0) or 0)
    except (TypeError, ValueError):
        return transform
    if abs(scale - 1.0) < 0.0001 and abs(offset_x) < 0.0001 and abs(offset_y) < 0.0001:
        return None
    return transform


def _publisher_sub_burn_enabled(video_options):
    if not isinstance(video_options, dict):
        return True
    return _truthy(video_options.get("sub_burn_enabled"), True)


def _publisher_script_subtitles(structured, audio):
    script = str((structured or {}).get("final_script_for_tts") or "").strip()
    if not script:
        return ""
    try:
        duration = float((audio or {}).get("seconds") or 0)
    except (TypeError, ValueError):
        duration = 0
    if duration <= 0:
        return ""

    chunks = _split_subtitle_script(script)
    if not chunks:
        return ""
    weights = [max(1, len(re.sub(r"\s+", "", chunk))) for chunk in chunks]
    total_weight = sum(weights) or len(chunks)
    cursor = 0.0
    subtitles = []
    for index, (chunk, weight) in enumerate(zip(chunks, weights)):
        if index == len(chunks) - 1:
            end = duration
        else:
            end = min(duration, cursor + duration * weight / total_weight)
        if end <= cursor:
            end = min(duration, cursor + 0.8)
        subtitles.append({
            "start": round(cursor, 3),
            "end": round(end, 3),
            "text": chunk,
        })
        cursor = end
        if cursor >= duration:
            break
    return subtitles


def _split_subtitle_script(script, max_chars=18):
    text = _single_line(script)
    if not text:
        return []
    parts = [part for part in re.split(r"([。！？!?；;，,、])", text) if part]
    units = []
    index = 0
    while index < len(parts):
        unit = parts[index]
        if index + 1 < len(parts) and re.fullmatch(r"[。！？!?；;，,、]", parts[index + 1]):
            unit += parts[index + 1]
            index += 1
        units.append(unit)
        index += 1

    chunks = []
    current = ""
    for unit in units:
        unit = unit.strip()
        if not unit:
            continue
        while len(unit) > max_chars:
            prefix = unit[:max_chars]
            if current:
                chunks.append(current)
                current = ""
            chunks.append(prefix)
            unit = unit[max_chars:]
        if not current:
            current = unit
        elif len(current) + len(unit) <= max_chars:
            current += unit
        else:
            chunks.append(current)
            current = unit
    if current:
        chunks.append(current)
    return chunks


def _publisher_result_summary(publisher_task, pdf_screenshot):
    result = _copy_dict(_safe_task_attr(publisher_task, "result"))
    params = _copy_dict(_safe_task_attr(publisher_task, "params"))
    landscape_options = params.get("video") if isinstance(params.get("video"), dict) else {}
    vertical_options = params.get("vertical_video") if isinstance(params.get("vertical_video"), dict) else {}
    render_mode = _publisher_child_render_mode(publisher_task)
    preview_seconds = _publisher_child_preview_seconds(publisher_task)
    landscape_enabled = _safe_task_attr(publisher_task, "publisher_landscape_enabled")
    vertical_enabled = _safe_task_attr(publisher_task, "publisher_vertical_enabled")
    landscape_path = result.get("video_path") or _safe_task_attr(publisher_task, "task_video_path")
    vertical_path = result.get("vertical_video_path") or _safe_task_attr(
        publisher_task,
        "publisher_vertical_video_path",
    )
    vertical_schemes = _publisher_child_vertical_schemes(publisher_task)
    primary_vertical_scheme = _primary_vertical_scheme(vertical_schemes)
    secondary_vertical_scheme = next((item for item in vertical_schemes if item != primary_vertical_scheme), None)
    vertical_paths = dict(result.get("vertical_video_paths") or {})
    if vertical_path:
        vertical_paths.setdefault(primary_vertical_scheme, vertical_path)
    if secondary_vertical_scheme and result.get("vertical_video_2_path"):
        vertical_paths.setdefault(secondary_vertical_scheme, result.get("vertical_video_2_path"))
    video_path = landscape_path or vertical_path
    summary = {
        "enabled": True,
        "status": "preview_ready" if publisher_task.status == "completed" and render_mode == "preview_10s" else publisher_task.status,
        "render_mode": render_mode,
        "publisher_task_id": publisher_task.id,
        "celery_id": publisher_task.celery_task_id,
        "landscape_enabled": bool(landscape_enabled) if landscape_enabled is not None else _truthy(landscape_options.get("enabled", False)),
        "vertical_enabled": bool(vertical_enabled) if vertical_enabled is not None else _truthy(vertical_options.get("enabled", False)),
        "video_path": video_path,
        "landscape_video_path": landscape_path,
        "vertical_video_path": vertical_path,
        "vertical_video_2_path": result.get("vertical_video_2_path"),
        "vertical_video_scheme": primary_vertical_scheme,
        "vertical_video_schemes": vertical_schemes,
        "vertical_video_paths": vertical_paths,
        "vertical_background_paths": result.get("vertical_background_paths") or {},
        "vertical_preview_video_path": vertical_path if preview_seconds > 0 else result.get("vertical_preview_video_path"),
        "vertical_preview_video_paths": vertical_paths if preview_seconds > 0 else (result.get("vertical_preview_video_paths") or {}),
        "vertical_preview_seconds": preview_seconds or result.get("vertical_preview_seconds"),
        "vertical_preview_scheme": _safe_task_attr(publisher_task, "publisher_vertical_scheme") or vertical_options.get("scheme") or result.get("vertical_video_scheme"),
        "vertical_cover_path": result.get("vertical_cover_path") or _safe_task_attr(publisher_task, "publisher_vertical_cover_path"),
        "podcast_cover_path": result.get("podcast_cover_path") or _safe_task_attr(publisher_task, "publisher_podcast_cover_path"),
        "bilibili_cover_path": result.get("bilibili_cover_path"),
        "cover_path": result.get("cover_path"),
        "pdf_first_page_image": pdf_screenshot,
        "bilibili_result": result.get("bilibili_result"),
        "youtube_result": result.get("youtube_result"),
    }
    if preview_seconds > 0:
        summary["message"] = f"{preview_seconds} 秒短视频预览已生成，未生成完整视频"
        summary["vertical_video_path"] = None
        summary["vertical_video_2_path"] = None
        summary["vertical_video_paths"] = {}
        summary["video_path"] = None
        summary["landscape_video_path"] = None
    return summary


def _format_cover_title(value):
    raw_lines = [
        _clean_cover_line(line)
        for line in str(value or "").replace("\r", "\n").split("\n")
    ]
    raw_lines = [line for line in raw_lines if line]
    if len(raw_lines) >= 2:
        first = _clip_cover_line(raw_lines[0], MAX_COVER_TITLE_LINE_CHARS)
        second = _clip_cover_line(raw_lines[1], MAX_COVER_TITLE_LINE_CHARS)
        return "\n".join(line for line in [first, second] if line) or "研报核心观点解读"

    text = _clean_cover_line(_single_line(value))[:120]
    if not text:
        return "研报核心观点解读"

    bank, subject = _extract_bank_subject(text)
    if bank:
        first_budget = max(6, MAX_COVER_TITLE_LINE_CHARS - len(bank) - 1)
        first_subject, rest = _take_cover_piece(subject, first_budget)
        first = _clean_cover_line(f"{bank}:{first_subject}" if first_subject else bank)
        second = _clip_cover_line(rest, MAX_COVER_TITLE_LINE_CHARS)
        return "\n".join(line for line in [first, second] if line)

    first, rest = _take_cover_piece(text, MAX_COVER_TITLE_LINE_CHARS)
    second = _clip_cover_line(rest, MAX_COVER_TITLE_LINE_CHARS)
    return "\n".join(line for line in [first, second] if line) or "研报核心观点解读"


def _format_cover_subtitle(value):
    text = _clean_cover_line(_single_line(value))
    text = re.sub(r"^(本报告|本文|本期|本研报)(解读|认为|指出)?", "", text).strip()
    return _clip_cover_line(text, MAX_COVER_SUBTITLE_CHARS) or "AI 自审整理后的研报音频版"


def _extract_bank_subject(text):
    normalized = _clean_cover_line(text)
    for alias, label in BANK_ALIASES:
        pattern = re.compile(re.escape(alias), flags=re.I)
        match = pattern.search(normalized)
        if not match:
            continue
        subject = (normalized[:match.start()] + normalized[match.end():]).strip()
        subject = _clean_cover_line(subject)
        subject = re.sub(r"^(研报|报告|证券|研究|全球|亚洲|中国)?[：:，,、\\-\\s]+", "", subject)
        return label, subject

    for separator in ("：", ":"):
        if separator in normalized:
            left, right = normalized.split(separator, 1)
            left = _clean_cover_line(left)
            right = _clean_cover_line(right)
            if 1 <= len(left) <= 6 and right:
                return left, right
    return None, normalized


def _take_cover_piece(text, max_chars):
    text = _clean_cover_line(text)
    if len(text) <= max_chars:
        return text, ""
    split_at = -1
    for index, char in enumerate(text[:max_chars + 1]):
        if index >= 4 and char in "，,、；;|/ ":
            split_at = index
    if split_at > 0:
        return _clean_cover_line(text[:split_at]), _clean_cover_line(text[split_at + 1:])
    return _clean_cover_line(text[:max_chars]), _clean_cover_line(text[max_chars:])


def _clip_cover_line(text, max_chars):
    text = _clean_cover_line(text)
    if len(text) <= max_chars:
        return text
    piece, _rest = _take_cover_piece(text, max_chars)
    return piece or _clean_cover_line(text[:max_chars])


def _clean_cover_line(value):
    text = _single_line(value)
    text = re.sub(r"(行业)?(深度)?研报(解读|拆解)?$", "", text)
    text = re.sub(r"(报告|专题)(解读|拆解)$", "", text)
    text = re.sub(r"\s*[-—_]+\s*", "-", text)
    text = text.strip()
    text = text.lstrip(LEADING_COVER_PUNCTUATION)
    text = text.rstrip(TRAILING_COVER_PUNCTUATION)
    return text.strip()


def _single_line(value):
    return " ".join(str(value or "").replace("\n", " ").split())


def _truthy(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled", "y"}


def _queue_notification(task_id, event_type, message=None):
    try:
        fresh = _cached_workflow_task(task_id)
        if fresh is None:
            fresh = _load_task(task_id, *TASK_PAYLOAD_COLUMNS)
        if not fresh:
            return False
        result = dict(fresh.result or {})
        notifications = dict(result.get("notifications") or {})
        if notifications.get(event_type, {}).get("queued"):
            return True

        payload = _notification_payload(fresh, event_type, message)
        queued = workflow_notification_service.enqueue_workflow_event(event_type, payload)
        if not queued.get("queued"):
            return False

        notifications[event_type] = {
            "queued": True,
            "event_type": event_type,
            "celery_task_id": queued.get("celery_task_id"),
            "queued_at": datetime.utcnow().isoformat() + "Z",
        }
        _update_result(task_id, {"notifications": notifications})
        return True
    except Exception:
        db.session.rollback()
        logger.exception("[REPORT_AUDIO_WORKFLOW] notification queue failed | task=%s event=%s", task_id, event_type)
        return False


def _notification_payload(task, event_type, message=None):
    result = task.result or {}
    params = task.params or {}
    workflow = params.get("report_audio_workflow") or {}
    metadata = workflow.get("metadata") if isinstance(workflow.get("metadata"), dict) else {}
    pdf = workflow.get("pdf") or result.get("pdf") or {}
    audio = result.get("audio") or {}
    video = result.get("video") or {}
    structured = result.get("structured_result") or {}
    tts_text = result.get("tts_text") or {}

    return {
        "id": task.id,
        "batch_id": task.id,
        "group_id": FEATURE_NAME,
        "group_name": "研报转语音编排",
        "group_label": "编排",
        "subject_prefix": "[研报转语音编排]",
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
            "stages": _notification_stages(task, result, pdf, structured, tts_text, audio, video),
            "outputs": _notification_outputs(result, pdf, structured, tts_text, audio, video),
        },
    }


def _notification_stages(task, result, pdf, structured, tts_text, audio, video):
    children = result.get("children") if isinstance(result.get("children"), dict) else {}
    gemini = result.get("gemini") or children.get("gemini")
    doubao_items = result.get("doubao_browser_tts") if isinstance(result.get("doubao_browser_tts"), list) else []
    if not doubao_items:
        doubao_items = [value for key, value in children.items() if str(key).startswith("doubao_browser_")]
    publisher = result.get("publisher_video") or children.get("publisher_video")

    return [
        {
            "name": "PDF 上传",
            "status": "completed" if pdf else "pending",
            "message": _pdf_message(pdf),
        },
        {
            "name": "研报 PDF Gem 解析/自审",
            "status": _child_stage_status(gemini),
            "message": _child_message(gemini),
        },
        {
            "name": "DeepSeek 结构化整理",
            "status": "completed" if structured else ("running" if task.current_step == "normalize_result" else "pending"),
            "message": _structured_message(structured, tts_text),
        },
        {
            "name": "豆包浏览器朗读捕获",
            "status": _children_stage_status(doubao_items),
            "message": _doubao_message(doubao_items, tts_text),
        },
        {
            "name": "WAV 合并",
            "status": "completed" if audio else ("running" if task.current_step == "combine_audio" else "pending"),
            "message": _audio_message(audio),
        },
        {
            "name": "横屏视频合成",
            "status": _video_stage_status(video, publisher),
            "message": _video_message(video, publisher),
        },
    ]


def _notification_outputs(result, pdf, structured, tts_text, audio, video):
    outputs = {
        "PDF": _pdf_message(pdf),
        "标题": (structured or {}).get("title"),
        "可朗读正文": f"{tts_text.get('chars')} 字 / {tts_text.get('chunks')} 段" if tts_text else None,
        "音频": _audio_message(audio),
        "视频": _video_output_message(video),
        "封面": (video or {}).get("cover_path") if isinstance(video, dict) else None,
        "PDF 首屏图": ((video or {}).get("pdf_first_page_image") or {}).get("path") if isinstance(video, dict) else None,
        "结果 JSON": result.get("output_path"),
    }
    return {key: value for key, value in outputs.items() if value}


def _child_stage_status(child):
    if not isinstance(child, dict) or not child:
        return "pending"
    status = str(child.get("status") or "").lower()
    if status in {"blocked", "blocked_by_captcha"}:
        return "blocked"
    if status == "completed":
        return "completed"
    if status in {"failed", "canceled"}:
        return status
    if status in {"pending", "queued"}:
        return "queued"
    return "running"


def _children_stage_status(children):
    if not children:
        return "pending"
    statuses = [_child_stage_status(child) for child in children if isinstance(child, dict)]
    if not statuses:
        return "pending"
    if any(status == "failed" for status in statuses):
        return "failed"
    if any(status == "blocked" for status in statuses):
        return "blocked"
    if any(status == "canceled" for status in statuses):
        return "canceled"
    if all(status == "completed" for status in statuses):
        return "completed"
    if any(status in {"running", "queued"} for status in statuses):
        return "running"
    return statuses[-1]


def _notification_status(status):
    mapping = {
        "pending": "queued",
        "processing": "running",
        "completed": "completed",
        "failed": "failed",
        "blocked_by_captcha": "blocked",
        "canceled": "canceled",
    }
    return mapping.get(status or "", status or "-")


def _pdf_message(pdf):
    if not isinstance(pdf, dict) or not pdf:
        return None
    filename = pdf.get("filename") or "PDF"
    size = _format_bytes(pdf.get("bytes"))
    return f"{filename} / {size}" if size else filename


def _child_message(child):
    if not isinstance(child, dict) or not child:
        return None
    parts = []
    if child.get("job_id"):
        parts.append(f"任务 {child.get('job_id')}")
    if child.get("progress") is not None:
        parts.append(f"{child.get('progress')}%")
    if child.get("error"):
        parts.append(f"错误：{child.get('error')}")
    return " / ".join(parts) if parts else None


def _structured_message(structured, tts_text):
    if not isinstance(structured, dict) or not structured:
        return None
    provider = structured.get("provider") or "-"
    chars = tts_text.get("chars") if isinstance(tts_text, dict) else None
    chunks = tts_text.get("chunks") if isinstance(tts_text, dict) else None
    suffix = f" / {chars} 字 / {chunks} 段" if chars is not None and chunks is not None else ""
    return f"{provider}{suffix}"


def _doubao_message(children, tts_text):
    total = tts_text.get("chunks") if isinstance(tts_text, dict) else None
    if children:
        completed = sum(1 for child in children if _child_stage_status(child) == "completed")
        blocked = sum(1 for child in children if _child_stage_status(child) == "blocked")
        failed = sum(1 for child in children if _child_stage_status(child) == "failed")
        parts = [f"子任务 {len(children)} 个", f"完成 {completed}"]
        if blocked:
            parts.append(f"阻塞 {blocked}")
        if failed:
            parts.append(f"失败 {failed}")
        return "，".join(parts)
    if total:
        return f"等待 {total} 段朗读"
    return None


def _audio_message(audio):
    if not isinstance(audio, dict) or not audio:
        return None
    seconds = audio.get("seconds")
    chunks = audio.get("chunks")
    size = _format_bytes(audio.get("bytes"))
    parts = []
    if seconds is not None:
        parts.append(f"{round(float(seconds), 1)} 秒")
    if chunks is not None:
        parts.append(f"{chunks} 段")
    if size:
        parts.append(size)
    return " / ".join(parts) if parts else None


def _video_stage_status(video, publisher):
    if isinstance(video, dict) and video.get("enabled") is False:
        return "skipped"
    if isinstance(video, dict) and video.get("video_path"):
        return "completed"
    return _child_stage_status(publisher)


def _video_message(video, publisher):
    if isinstance(video, dict) and video.get("enabled") is False:
        return video.get("message") or "未勾选生成横屏视频"
    if isinstance(video, dict) and video.get("video_path"):
        return f"任务 {video.get('publisher_task_id')} / MP4 已生成"
    return _child_message(publisher)


def _video_output_message(video):
    if not isinstance(video, dict) or not video:
        return None
    if video.get("enabled") is False:
        return video.get("message")
    path = video.get("video_path")
    if not path:
        return None
    try:
        size = _format_bytes(os.path.getsize(path))
    except OSError:
        size = None
    return f"{path} / {size}" if size else path


def _format_bytes(value):
    try:
        bytes_value = int(value or 0)
    except (TypeError, ValueError):
        return None
    if bytes_value <= 0:
        return None
    if bytes_value < 1024:
        return f"{bytes_value} B"
    if bytes_value < 1024 * 1024:
        return f"{bytes_value / 1024:.1f} KB"
    return f"{bytes_value / 1024 / 1024:.1f} MB"


def _safe_task_attr(task, name, default=None):
    if isinstance(task, dict):
        return task.get(name, default)
    try:
        state = sqlalchemy_inspect(task)
        if name == "id" and state.identity:
            return state.identity[0]
        if name in state.unloaded:
            return default
    except Exception:
        pass
    try:
        return getattr(task, name, default)
    except Exception:
        return default


def _copy_dict(value):
    return dict(value) if isinstance(value, dict) else {}


def _isoformat_z(value):
    if not value:
        return None
    if isinstance(value, str):
        return value
    try:
        text = value.isoformat()
    except AttributeError:
        return str(value)
    return text if text.endswith("Z") else f"{text}Z"


def _task_id(task):
    if isinstance(task, dict):
        value = task.get("id") or task.get("job_id")
    else:
        value = _safe_task_attr(task, "id")
        if not value:
            try:
                identity = sqlalchemy_inspect(task).identity
                value = identity[0] if identity else None
            except Exception:
                value = None
    text = str(value or "").strip()
    return text or None


def _task_state_snapshot(task):
    if not task:
        return None
    if isinstance(task, dict):
        snapshot = dict(task)
        result = _copy_dict(snapshot.get("result"))
        params = _copy_dict(snapshot.get("params"))
        metadata = _copy_dict(snapshot.get("metadata"))
        audio = _copy_dict(snapshot.get("audio"))
        if metadata and not isinstance(result.get("metadata"), dict):
            result["metadata"] = metadata
        if audio and not isinstance(result.get("audio"), dict):
            result["audio"] = audio
        for key in ("error_type", "blocked_reason", "captcha_blocked"):
            if key in snapshot and key not in result:
                result[key] = snapshot.get(key)
        snapshot["id"] = snapshot.get("id") or snapshot.get("job_id")
        snapshot["job_id"] = snapshot.get("job_id") or snapshot.get("id")
        snapshot["celery_task_id"] = snapshot.get("celery_task_id") or snapshot.get("celery_id")
        snapshot["celery_id"] = snapshot.get("celery_id") or snapshot.get("celery_task_id")
        snapshot["current_step"] = snapshot.get("current_step") or snapshot.get("step")
        snapshot["step"] = snapshot.get("step") or snapshot.get("current_step")
        snapshot["progress"] = snapshot.get("progress") or 0
        snapshot["error_message"] = snapshot.get("error_message") or snapshot.get("error") or result.get("error")
        snapshot["error"] = snapshot.get("error") or snapshot.get("error_message") or result.get("error")
        snapshot["result"] = result
        snapshot["params"] = params
        return snapshot

    result = _copy_dict(_safe_task_attr(task, "result"))
    params = _copy_dict(_safe_task_attr(task, "params"))
    raw_text_hash = _safe_task_attr(task, "doubao_tts_text_hash")
    raw_chunk_index = _safe_task_attr(task, "doubao_tts_chunk_index")
    raw_chunk_attempt = _safe_task_attr(task, "doubao_tts_chunk_attempt")
    raw_wav_path = _safe_task_attr(task, "doubao_tts_wav_path")
    text_hash = raw_text_hash if isinstance(raw_text_hash, str) else None
    chunk_index = raw_chunk_index if isinstance(raw_chunk_index, (int, float, str)) else None
    chunk_attempt = raw_chunk_attempt if isinstance(raw_chunk_attempt, (int, float, str)) else None
    wav_path = raw_wav_path if isinstance(raw_wav_path, str) else None
    if text_hash or chunk_index or chunk_attempt:
        metadata = dict(result.get("metadata") or {})
        if text_hash:
            metadata.setdefault("text_hash", text_hash)
        if chunk_index:
            metadata.setdefault("chunk_index", chunk_index)
        if chunk_attempt:
            metadata.setdefault("chunk_attempt", chunk_attempt)
        result["metadata"] = metadata
    if wav_path:
        audio = dict(result.get("audio") or {})
        audio.setdefault("wav_path", wav_path)
        result["audio"] = audio
    task_id = _safe_task_attr(task, "id")
    celery_task_id = _safe_task_attr(task, "celery_task_id")
    current_step = _safe_task_attr(task, "current_step")
    error_message = _safe_task_attr(task, "error_message")
    return {
        "id": task_id,
        "job_id": task_id,
        "celery_task_id": celery_task_id,
        "celery_id": celery_task_id,
        "status": _safe_task_attr(task, "status"),
        "current_step": current_step,
        "step": current_step,
        "progress": _safe_task_attr(task, "progress") or 0,
        "error_message": error_message,
        "error": error_message or result.get("error"),
        "feature": _safe_task_attr(task, "feature"),
        "parent_task_id": _safe_task_attr(task, "parent_task_id"),
        "source_task_id": _safe_task_attr(task, "source_task_id"),
        "doubao_tts_text_hash": text_hash,
        "doubao_tts_chunk_index": chunk_index,
        "doubao_tts_chunk_attempt": chunk_attempt,
        "doubao_tts_wav_path": wav_path,
        "params": params,
        "result": result,
        "created_at": _safe_task_attr(task, "created_at"),
        "completed_at": _safe_task_attr(task, "completed_at"),
    }


def _task_result_payload(task):
    snapshot = _task_state_snapshot(task) or {}
    return snapshot.get("result") if isinstance(snapshot.get("result"), dict) else {}


def _task_params_payload(task):
    snapshot = _task_state_snapshot(task) or {}
    return snapshot.get("params") if isinstance(snapshot.get("params"), dict) else {}


def _task_doubao_metadata(task):
    snapshot = _task_state_snapshot(task) or {}
    result = snapshot.get("result") if isinstance(snapshot.get("result"), dict) else {}
    metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
    if metadata:
        return metadata
    metadata = snapshot.get("metadata") if isinstance(snapshot.get("metadata"), dict) else {}
    if metadata:
        return metadata
    params = snapshot.get("params") if isinstance(snapshot.get("params"), dict) else {}
    if not params and not isinstance(task, dict):
        try:
            raw_params = task.params
            params = raw_params if isinstance(raw_params, dict) else {}
        except Exception:
            params = {}
    doubao_payload = params.get("doubao_browser_tts") if isinstance(params.get("doubao_browser_tts"), dict) else {}
    metadata = doubao_payload.get("metadata") if isinstance(doubao_payload.get("metadata"), dict) else {}
    return metadata


def _task_public_summary(task):
    snapshot = _task_state_snapshot(task)
    if not snapshot:
        return None
    result = snapshot.get("result") if isinstance(snapshot.get("result"), dict) else {}
    summary = {
        "job_id": snapshot.get("job_id") or snapshot.get("id"),
        "celery_id": snapshot.get("celery_id") or snapshot.get("celery_task_id"),
        "status": snapshot.get("status"),
        "step": snapshot.get("step") or snapshot.get("current_step"),
        "progress": snapshot.get("progress") or 0,
        "error": snapshot.get("error") or snapshot.get("error_message") or result.get("error"),
        "error_type": result.get("error_type"),
        "blocked_reason": result.get("blocked_reason"),
        "captcha_blocked": result.get("captcha_blocked"),
        "metadata": _task_doubao_metadata(snapshot),
        "audio": result.get("audio"),
        "created_at": _isoformat_z(snapshot.get("created_at")),
        "completed_at": _isoformat_z(snapshot.get("completed_at")),
    }
    notifications = result.get("notifications") if isinstance(result.get("notifications"), dict) else {}
    captcha_notification = public_browser_human_intervention_notification(
        notifications.get("doubao_captcha_blocked")
    )
    if captcha_notification:
        summary["notifications"] = {
            "doubao_captcha_blocked": captcha_notification,
        }
    return summary


def _merge_child_summary(previous, summary):
    merged = dict(previous or {})
    merged.update(summary or {})
    for key in ("metadata", "audio", "captcha_blocked", "notifications"):
        if previous.get(key) and not merged.get(key):
            merged[key] = previous.get(key)
    return merged


def _task_public_summary_by_id(task_id):
    if not task_id:
        return None
    return _task_public_summary(_load_task(task_id, *TASK_CHILD_COLUMNS))


def _task_public_summaries_by_ids(task_ids):
    summaries = []
    seen = set()
    for task_id in task_ids or []:
        if not task_id or task_id in seen:
            continue
        seen.add(task_id)
        summary = _task_public_summary_by_id(task_id)
        if summary:
            summaries.append(summary)
    return summaries


def _mark_failed(task, message):
    result = dict(task.result or {})
    result["error"] = message
    result["completed_at"] = datetime.utcnow().isoformat() + "Z"
    task.status = "failed"
    task.current_step = "failed"
    task.error_message = message
    task.completed_at = datetime.utcnow()
    task.result = result
    flag_modified(task, "result")
    db.session.commit()


def _handle_report_audio_task_failure(celery_task, task_id, exc):
    if _is_retryable_db_error(exc) and celery_task.request.retries < _db_retry_count():
        countdown = _db_retry_delay_seconds(celery_task.request.retries)
        logger.warning(
            "[REPORT_AUDIO_WORKFLOW] retry transient db error | task=%s retry=%s/%s countdown=%ss error=%s",
            task_id,
            celery_task.request.retries + 1,
            _db_retry_count(),
            countdown,
            exc,
        )
        raise celery_task.retry(exc=exc, countdown=countdown, max_retries=_db_retry_count())

    details = _mark_report_audio_failed_safely(task_id, str(exc))
    if not details.get("notification_queued"):
        _send_direct_report_audio_failure_email(
            task_id=task_id,
            error=exc,
            persisted=details.get("persisted"),
            notification_queued=details.get("notification_queued"),
            persist_error=details.get("persist_error"),
        )
    logger.error(
        "[REPORT_AUDIO_WORKFLOW] task failed outside normal handler | task=%s persisted=%s notification_queued=%s error=%s",
        task_id,
        details.get("persisted"),
        details.get("notification_queued"),
        exc,
        exc_info=bool(getattr(exc, "__traceback__", None)),
    )
    return False


def _mark_report_audio_failed_safely(task_id, message):
    details = {
        "persisted": False,
        "notification_queued": False,
        "persist_error": None,
    }
    try:
        app = create_app(register_apis=False)
        with app.app_context():
            db.session.rollback()
            task = _load_task(task_id, *TASK_STATE_COLUMNS)
            if not task:
                details["persist_error"] = f"任务不存在: {task_id}"
                return details
            _mark_failed(task, message)
            details["persisted"] = True
            details["notification_queued"] = _queue_notification(task_id, "report_audio_failed", message)
    except Exception as persist_exc:
        details["persist_error"] = str(persist_exc)
        try:
            db.session.rollback()
        except Exception:
            pass
        logger.exception("[REPORT_AUDIO_WORKFLOW] failed to persist failure state | task=%s", task_id)
    return details


def _send_direct_report_audio_failure_email(
    *,
    task_id,
    error,
    persisted=False,
    notification_queued=False,
    persist_error=None,
):
    try:
        subject = f"[研报转语音] 编排任务异常 · {str(task_id)[:8]}"
        lines = [
            "研报转语音编排任务异常退出，已触发兜底通知。",
            "",
            f"任务 ID：{task_id}",
            f"失败已写入数据库：{'是' if persisted else '否'}",
            f"失败通知已入队：{'是' if notification_queued else '否'}",
            f"异常：{error}",
        ]
        if persist_error:
            lines.append(f"落库/通知异常：{persist_error}")
        lines.extend([
            "",
            f"发送时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        ])
        workflow_notification_service.send_email_message(subject, "\n".join(lines))
    except Exception:
        logger.exception("[REPORT_AUDIO_WORKFLOW] direct failure email failed | task=%s", task_id)


def _is_retryable_db_error(exc):
    return is_retryable_db_error(exc)


def _db_retry_count():
    return _bounded_int(os.environ.get(DB_RETRIES_ENV_NAME), default=DEFAULT_DB_RETRIES, lower=0, upper=20)


def _db_retry_delay_seconds(retry_index):
    base = _bounded_int(
        os.environ.get(DB_RETRY_DELAY_ENV_NAME),
        default=DEFAULT_DB_RETRY_DELAY_SECONDS,
        lower=1,
        upper=600,
    )
    return min(base * max(int(retry_index or 0) + 1, 1), 600)


def _plain_text(value):
    return re.sub(r"\s+", "", str(value or ""))


def _min_gemini_final_chars():
    return max(0, _int_env(
        "REPORT_AUDIO_GEMINI_MIN_FINAL_CHARS",
        _int_env("REPORT_PDF_GEMINI_MIN_RESPONSE_CHARS", 120),
    ))


def _required_gemini_final_chars(payload):
    configured = _min_gemini_final_chars()
    if not isinstance(payload, dict):
        return configured
    requested = _bounded_int(
        payload.get("gemini_min_final_chars"),
        default=configured,
        lower=0,
        upper=20000,
    )
    return max(configured, requested)


def _bounded_int(value, *, default, lower, upper):
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(lower, min(upper, number))


def _int_env(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default
