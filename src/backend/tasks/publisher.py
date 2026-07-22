import asyncio
import os
import sys
import logging
import json
import subprocess
import time
from datetime import datetime
from types import SimpleNamespace
from sqlalchemy import cast, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import DBAPIError, OperationalError

# Ensure src/backend is in sys.path for Celery worker
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

# Get project root (tasks/publisher.py -> backend -> src -> root)
PROJECT_ROOT = os.path.dirname(os.path.dirname(backend_dir))

from celery_config import celery_app
from models.bilibili_publish import PublicationTaskBilibiliUpload
from models.task import PublicationTask
from models import db
from services.video_editor import video_editor_service
from services.thumbnail_service import thumbnail_service
from services.bilibili.uploader import BiliUploader, DEFAULT_NEUTRAL_MARK
from services.youtube.uploader import YouTubeUploader
from services.platform_credential_service import resolve_bilibili_auth
from services.platform_publish_dispatch import PLATFORM_UPLOAD_QUEUE
from services.runtime_guard import (
    guard_publication_task_start,
    is_retryable_db_error,
    retry_exception_types,
)
from config import get_config
from sqlalchemy.orm import load_only

logger = logging.getLogger(__name__)

RENDER_QUEUE = "render"
AUDIO_PREPROCESS_VERSION = "v2"
AUDIO_DURATION_TOLERANCE_SECONDS = 2.0
DEFAULT_DB_RETRIES = 5
DEFAULT_DB_RETRY_DELAY_SECONDS = 3
DB_RETRIES_ENV_NAME = "PUBLISHER_DB_RETRIES"
DB_RETRY_DELAY_ENV_NAME = "PUBLISHER_DB_RETRY_DELAY_SECONDS"

TASK_UPDATE_COLUMNS = (
    PublicationTask.id,
    PublicationTask.status,
    PublicationTask.current_step,
    PublicationTask.progress,
    PublicationTask.result,
    PublicationTask.error_message,
    PublicationTask.completed_at,
)

TASK_PAYLOAD_COLUMNS = (
    *TASK_UPDATE_COLUMNS,
    PublicationTask.params,
    PublicationTask.feature,
    PublicationTask.user_id,
    PublicationTask.created_at,
)
TASK_START_COLUMNS = (
    PublicationTask.id,
    PublicationTask.celery_task_id,
    PublicationTask.status,
    PublicationTask.feature,
)


def _load_task(task_id, *columns):
    attrs = columns or TASK_PAYLOAD_COLUMNS
    return (
        PublicationTask.query.options(load_only(*attrs))
        .filter(PublicationTask.id == str(task_id))
        .first()
    )


def _load_task_with_retries(task_id, *columns):
    retries = _bounded_int(os.environ.get(DB_RETRIES_ENV_NAME), DEFAULT_DB_RETRIES, lower=1, upper=20)
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            db.session.rollback()
            return _load_task(task_id, *columns)
        except Exception as exc:
            last_error = exc
            db.session.rollback()
            if not _is_retryable_db_error(exc) or attempt >= retries:
                raise
            delay = _db_retry_delay_seconds(attempt - 1)
            logger.warning(
                "Task %s initial DB load failed (%s/%s), retrying in %ss: %s",
                task_id,
                attempt,
                retries,
                delay,
                exc,
            )
            time.sleep(delay)
    raise last_error


def _load_bilibili_upload_context_with_retries(task_id):
    from services.bilibili_publish import bilibili_upload_execution_context

    retries = _bounded_int(os.environ.get(DB_RETRIES_ENV_NAME), DEFAULT_DB_RETRIES, lower=1, upper=20)
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            db.session.rollback()
            return bilibili_upload_execution_context(task_id)
        except Exception as exc:
            last_error = exc
            db.session.rollback()
            if not _is_retryable_db_error(exc) or attempt >= retries:
                raise
            delay = _db_retry_delay_seconds(attempt - 1)
            logger.warning(
                "Task %s Bilibili execution load failed (%s/%s), retrying in %ss: %s",
                task_id,
                attempt,
                retries,
                delay,
                exc,
            )
            time.sleep(delay)
    raise last_error


def _is_retryable_db_error(exc):
    return is_retryable_db_error(exc)


def _db_retry_delay_seconds(retry_index):
    base = _bounded_int(os.environ.get(DB_RETRY_DELAY_ENV_NAME), DEFAULT_DB_RETRY_DELAY_SECONDS, lower=1, upper=60)
    return min(base * max(int(retry_index or 0) + 1, 1), 60)


def _bounded_int(value, default, *, lower, upper):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = int(default)
    return min(max(parsed, lower), upper)


def _postgres_jsonb_merge(column, patch):
    return func.coalesce(
        cast(column, JSONB),
        cast({}, JSONB),
    ).op("||")(cast(patch, JSONB))


def update_task_record(task_id, retries=3, result_patch=None, **fields):
    """Persist task state in a short transaction with rollback/retry.

    Long media synthesis can outlive a cloud PostgreSQL connection. Keeping task
    updates isolated prevents a transient DB timeout from poisoning the worker
    session and hiding the real task state behind PendingRollbackError.
    """
    import time

    last_error = None
    for attempt in range(1, retries + 1):
        try:
            db.session.rollback()
            if result_patch is not None and not isinstance(result_patch, dict):
                raise ValueError("result_patch must be a dict")
            if result_patch is not None and "result" in fields:
                raise ValueError("result and result_patch cannot be written together")
            projection_fields = dict(fields)
            if result_patch is not None:
                projection_fields["result"] = result_patch
            values = {}
            persisted_fields = dict(fields)
            persisted_fields.update(_publisher_hot_projection_values(projection_fields))
            for key, value in persisted_fields.items():
                column = getattr(PublicationTask, key, None)
                if column is None:
                    raise ValueError(f"Unknown PublicationTask field: {key}")
                values[column] = value
            if result_patch is not None:
                dialect = db.session.get_bind().dialect.name
                if dialect == "postgresql":
                    values[PublicationTask.result] = _postgres_jsonb_merge(
                        PublicationTask.result,
                        result_patch,
                    )
                else:
                    current_result = (
                        db.session.query(PublicationTask.result)
                        .filter(PublicationTask.id == str(task_id))
                        .scalar()
                    )
                    merged_result = dict(current_result or {})
                    merged_result.update(result_patch)
                    values[PublicationTask.result] = merged_result
            updated = (
                PublicationTask.query
                .filter(PublicationTask.id == str(task_id))
                .update(values, synchronize_session=False)
            )
            if not updated:
                raise ValueError(f"Task {task_id} not found")
            projection_values = _bilibili_upload_projection_values(projection_fields)
            if projection_values:
                (
                    PublicationTaskBilibiliUpload.query
                    .filter(PublicationTaskBilibiliUpload.task_id == str(task_id))
                    .update(projection_values, synchronize_session=False)
                )
            db.session.commit()
            return SimpleNamespace(id=str(task_id), **fields)
        except Exception as exc:
            last_error = exc
            db.session.rollback()
            logger.warning(
                "Task %s DB update failed (%s/%s) fields=%s error=%s",
                task_id,
                attempt,
                retries,
                ",".join(fields.keys()),
                exc,
                exc_info=attempt == retries,
            )
            if attempt < retries:
                time.sleep(min(2 * attempt, 5))
    raise last_error


def _publisher_hot_projection_values(fields):
    values = {}
    params = fields.get("params") if isinstance(fields.get("params"), dict) else None
    result = fields.get("result") if isinstance(fields.get("result"), dict) else None

    if params is not None:
        landscape = params.get("video") if isinstance(params.get("video"), dict) else {}
        vertical = params.get("vertical_video") if isinstance(params.get("vertical_video"), dict) else {}
        metadata = params.get("metadata") if isinstance(params.get("metadata"), dict) else {}
        if "enabled" in landscape:
            values["publisher_landscape_enabled"] = _enabled(landscape.get("enabled"), False)
        values["publisher_render_mode"] = str(
            metadata.get("render_mode") or params.get("render_mode") or "full_video"
        )[:32]
        if "enabled" in vertical:
            values["publisher_vertical_enabled"] = _enabled(vertical.get("enabled"), False)
        if vertical.get("scheme"):
            values["publisher_vertical_scheme"] = str(vertical.get("scheme"))[:32]
        if "preview_seconds" in vertical:
            values["publisher_preview_seconds"] = _bounded_non_negative_int(vertical.get("preview_seconds"))

    if result is not None:
        metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
        if metadata.get("render_mode") or result.get("render_mode"):
            values["publisher_render_mode"] = str(
                metadata.get("render_mode") or result.get("render_mode")
            )[:32]
        if "video_path" in result:
            values["task_video_path"] = str(result.get("video_path") or "")[:600] or None
        if "vertical_video_path" in result:
            values["publisher_vertical_video_path"] = str(result.get("vertical_video_path") or "")[:600] or None
        if "vertical_cover_path" in result:
            values["publisher_vertical_cover_path"] = str(result.get("vertical_cover_path") or "")[:600] or None
        if "podcast_cover_path" in result:
            values["publisher_podcast_cover_path"] = str(result.get("podcast_cover_path") or "")[:600] or None
        if "bilibili_cover_path" in result:
            values["publisher_bilibili_cover_path"] = str(result.get("bilibili_cover_path") or "")[:600] or None
        if result.get("vertical_video_scheme"):
            values["publisher_vertical_scheme"] = str(result.get("vertical_video_scheme"))[:32]
        if "vertical_preview_seconds" in result:
            values["publisher_preview_seconds"] = _bounded_non_negative_int(result.get("vertical_preview_seconds"))
    return values


def _bilibili_upload_projection_values(fields):
    result = fields.get("result") if isinstance(fields.get("result"), dict) else None
    if result is None:
        return {}

    values = {}
    upload_result = result.get("bilibili_result")
    if isinstance(upload_result, dict):
        if upload_result.get("aid") not in (None, ""):
            values[PublicationTaskBilibiliUpload.aid] = str(upload_result.get("aid"))[:32]
        if upload_result.get("bvid") not in (None, ""):
            values[PublicationTaskBilibiliUpload.bvid] = str(upload_result.get("bvid"))[:32]
    if result.get("bilibili_title") not in (None, ""):
        values[PublicationTaskBilibiliUpload.title] = str(result.get("bilibili_title"))[:120]
    if result.get("bilibili_credential_id") not in (None, ""):
        values[PublicationTaskBilibiliUpload.credential_id] = str(
            result.get("bilibili_credential_id")
        )[:80]
    if result.get("bilibili_credential_name") not in (None, ""):
        values[PublicationTaskBilibiliUpload.credential_name] = str(
            result.get("bilibili_credential_name")
        )[:255]
    if values:
        values[PublicationTaskBilibiliUpload.updated_at] = datetime.utcnow()
    return values


def _bounded_non_negative_int(value):
    try:
        return max(0, int(float(value or 0)))
    except (TypeError, ValueError):
        return 0

def to_abs_path(rel_path):
    """Helper to convert path to absolute, staying robust regardless of current working directory."""
    if not rel_path: return None
    
    # 1. 如果已经是绝对路径且存在，直接返回
    if os.path.isabs(rel_path) and os.path.exists(rel_path):
        return rel_path
        
    # 2. 优先尝试相对于项目根目录
    abs_root_path = os.path.abspath(os.path.join(PROJECT_ROOT, rel_path))
    if os.path.exists(abs_root_path):
        return abs_root_path
        
    # 3. 备选方案：尝试相对于当前工作目录
    cwd_path = os.path.abspath(os.path.join(os.getcwd(), rel_path))
    if os.path.exists(cwd_path):
        return cwd_path
    
    # 如果都找不到，返回基于项目根目录的路径作为默认期望值
    return abs_root_path

def sanitize_filename(name):
    """Sanitize filename to keep Chinese characters but remove illegal fs chars."""
    import re
    # Remove / \ : * ? " < > |
    name = re.sub(r'[\\/*?:"<>|]', "", name)
    # Replace spaces with underscores
    name = name.replace(" ", "_")
    return name.strip()

def has_usable_subtitles(value):
    """Return True only when subtitle payload contains real subtitle text."""
    if not value:
        return False
    if isinstance(value, str):
        stripped = value.strip()
        return bool(stripped and stripped not in {"识别中...", "识别失败"})
    if isinstance(value, dict):
        return has_usable_subtitles(value.get("text"))
    if isinstance(value, list):
        return any(has_usable_subtitles(item) for item in value)
    return False

def _enabled(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def _normalize_tag_list(value, default=None):
    if isinstance(value, list):
        tags = [str(item).strip(" #") for item in value if str(item or "").strip(" #")]
    elif isinstance(value, str):
        import re
        tags = [item.strip(" #") for item in re.split(r"[,，、\n]+", value) if item.strip(" #")]
    else:
        tags = []
    tags = [tag[:20] for tag in tags if tag]
    return tags[:10] or list(default or [])


def _audio_needs_preprocess(start_offset, end_offset, speed):
    return (
        abs(float(start_offset or 0)) >= 0.001
        or abs(float(end_offset or 0)) >= 0.001
        or abs(float(speed or 1.0) - 1.0) >= 0.001
    )


def _atempo_filter(speed):
    speed = float(speed or 1.0)
    if speed <= 0:
        raise ValueError("音频倍速必须大于 0")
    if abs(speed - 1.0) < 0.001:
        return None
    remaining = speed
    factors = []
    while remaining > 2.0:
        factors.append(2.0)
        remaining /= 2.0
    while remaining < 0.5:
        factors.append(0.5)
        remaining /= 0.5
    if abs(remaining - 1.0) >= 0.001:
        factors.append(remaining)
    return ",".join(f"atempo={factor:.8g}" for factor in factors)


def _probe_audio_duration(media_path):
    try:
        from services.asr_cache_service import probe_media_duration

        return float(probe_media_duration(media_path) or 0)
    except Exception as exc:
        logger.warning("⚠️ [TASK] 无法读取音频时长: %s | %s", media_path, exc)
        return 0.0


def _expected_prepared_audio_duration(source_duration, start_offset, end_seconds, speed):
    source_duration = float(source_duration or 0)
    start_offset = max(0.0, float(start_offset or 0))
    speed = float(speed or 1.0)
    if speed <= 0:
        raise ValueError("音频倍速必须大于 0")
    if end_seconds is not None:
        resolved_end = float(end_seconds)
    elif source_duration > 0:
        resolved_end = source_duration
    else:
        return None
    source_length = max(0.1, resolved_end - start_offset)
    return source_length / speed


def _duration_matches_expected(actual, expected):
    if expected is None or expected <= 0:
        return True
    actual = float(actual or 0)
    if actual <= 0:
        return False
    tolerance = max(AUDIO_DURATION_TOLERANCE_SECONDS, expected * 0.01)
    return abs(actual - expected) <= tolerance


def _prepared_audio_needs_rebuild(edit_path, raw_path, expected_duration):
    if not os.path.exists(edit_path) or os.path.getsize(edit_path) <= 0:
        return True
    if os.path.getmtime(raw_path) > os.path.getmtime(edit_path):
        logger.info("  - 🔄 原始文件已更新，准备重新生成剪辑片段")
        return True
    if expected_duration is not None:
        actual_duration = _probe_audio_duration(edit_path)
        if not _duration_matches_expected(actual_duration, expected_duration):
            logger.warning(
                "  - 🔄 剪辑音频时长不匹配，准备重建: path=%s expected=%.3fs actual=%.3fs",
                edit_path,
                expected_duration,
                actual_duration,
            )
            return True
    return False


def _build_audio_preprocess_command(raw_path, edit_path, start_offset, end_seconds, speed):
    cmd = ["ffmpeg", "-y", "-ss", str(start_offset)]
    if end_seconds is not None:
        cmd.extend(["-t", str(max(0.1, float(end_seconds) - float(start_offset or 0)))])
    cmd.extend(["-i", raw_path])
    atempo = _atempo_filter(speed)
    if atempo:
        cmd.extend(["-filter:a", atempo])
    cmd.extend(["-vn", edit_path])
    return cmd


def _first_existing_path(*candidates):
    for candidate in candidates:
        path = to_abs_path(candidate)
        if path and os.path.exists(path):
            return path
    return None


def _generate_scheme2_bilibili_cover(*, params, main_title, sub_title, screenshot_path, base_name,
                                      institution, emotion, footer_text, image_transform):
    if not isinstance(params, dict) or not _enabled(params.get('enabled'), False):
        return None
    return thumbnail_service.generate_workbench_bilibili_cover(
        main_title=params.get('main_title') or main_title,
        sub_title=params.get('sub_title') or sub_title,
        screenshot_path=params.get('screenshot_path') or screenshot_path,
        output_filename=f"bcover2_{base_name}.jpg",
        institution=params.get('institution') or institution,
        emotion=params.get('emotion') or emotion,
        footer_text=params.get('footer_text') or footer_text,
        image_transform=params.get('image_transform') or image_transform,
        show_footer=_enabled(params.get('show_footer'), False),
    )

@celery_app.task(bind=True)
def run_publication_pipeline(self, task_id):
    """
    Execute the full publication pipeline: Synthesis -> Thumbnail -> Upload.
    """
    import os
    import sys
    # 确保后端目录在路径中
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)
        
    from app import create_app
    overall_start = time.time()
    app = create_app()
    
    with app.app_context():
        start_state = _load_task_with_retries(task_id, *TASK_START_COLUMNS)
        if not start_state:
            logger.error(f"Task {task_id} not found in DB")
            return
        request_id = str(getattr(getattr(self, "request", None), "id", "") or "")
        current_celery_id = str(start_state.celery_task_id or "")
        if request_id and current_celery_id and request_id != current_celery_id:
            logger.info(
                "Task %s received stale celery message %s, current celery id is %s; skipping.",
                task_id,
                request_id,
                current_celery_id,
            )
            return
        if start_state.status in {"completed", "failed", "canceled"}:
            logger.info(
                "Task %s is terminal (%s) before publisher start; skipping duplicate message.",
                task_id,
                start_state.status,
            )
            return

        task = _load_task_with_retries(task_id, *TASK_PAYLOAD_COLUMNS)
        if not task:
            logger.error(f"Task {task_id} not found in DB after start check")
            return
        guard_publication_task_start(
            self,
            task_id,
            feature=task.feature or "publisher",
            queue=RENDER_QUEUE,
        )
    
        try:
            overall_start = time.time()
            params = task.params
            result_state = dict(task.result or {})

            task = update_task_record(
                task_id,
                status='processing',
                current_step='prepare',
                progress=max(int(task.progress or 0), 1),
                result=result_state,
            )

            # 🚀 [DEBUG LOG] 记录输入原始参数
            logger.info(f"🔍 [TASK] 任务启动参数详情: {json.dumps(params, ensure_ascii=False, indent=2)}")

            vid_params = params.get('video', {})
            v_vid_params = params.get('vertical_video', {})
            bili_params = params.get('bilibili', {})
            thumb_params = params.get('thumbnail', {})

            # 初始化路径变量
            final_video_path = None
            v_video_path = None

            logger.info(f"🚀 [TASK] 开始执行发布流水线 | 任务ID: {task_id}")
            
            # Determine base filename from Title -> (Main Title lines + Sub Title) -> Task ID
            raw_title = bili_params.get('title')
            if not raw_title:
                # 自动合成逻辑：主标题各行 + 副标题
                main_lines = [l.strip() for l in thumb_params.get('main_title', '').split('\n') if l.strip()]
                sub_text = thumb_params.get('sub_title', '').strip()
                if sub_text:
                    main_lines.append(sub_text)
                raw_title = " | ".join(main_lines) if main_lines else ""
    
            if raw_title:
                base_name = sanitize_filename(raw_title)
                # Add short task_id suffix to ensure uniqueness if title repeats
                base_name = f"{base_name}_{task_id[:6]}"
            else:
                base_name = f"pub_{task_id}"
    
            # --- Step 0: Pre-process Audio & Subtitles (Shared for H and V) ---
            audio_segments_raw = params.get('audio_segments', [])
            audio_path_abs = []
            asr_request_specs = []
            subtitles = []

            # 1. 尝试从新页面的“时间轴轨道”获取数据
            if audio_segments_raw:
                logger.info(f"🎞️ [TASK] 模式: 多轨道剪辑 | 片段数: {len(audio_segments_raw)}")
                for i, seg in enumerate(audio_segments_raw):
                    raw_path = to_abs_path(seg.get('path'))
                    if not raw_path or not os.path.exists(raw_path): continue
                    
                    speed = float(seg.get('speed', 1.0))
                    start_offset = float(seg.get('start', 0))
                    end_offset = float(seg.get('end_offset', 0))
                    source_duration = 0
                    end_seconds = None
                    strict_preprocess = _audio_needs_preprocess(start_offset, end_offset, speed)
                    if strict_preprocess:
                        source_duration = _probe_audio_duration(raw_path)
                    if end_offset > 0 and source_duration > 0:
                        end_seconds = max(start_offset + 0.1, source_duration - end_offset)
                    elif end_offset > 0:
                        logger.warning("⚠️ [TASK] 无法读取音频片段时长，不能安全执行 end_offset 裁切: %s", raw_path)
                        raise RuntimeError(f"无法读取音频时长，不能安全裁切结尾: {raw_path}")
                    
                    # 生成物理剪辑后的临时文件
                    file_dir, file_name = os.path.split(raw_path)
                    name_part, ext_part = os.path.splitext(file_name)
                    edit_path = os.path.join(
                        file_dir,
                        f"{name_part}_edit_{AUDIO_PREPROCESS_VERSION}_s{speed}_st{start_offset}_eo{end_offset}{ext_part}",
                    )
                    expected_duration = _expected_prepared_audio_duration(
                        source_duration,
                        start_offset,
                        end_seconds,
                        speed,
                    )
                    need_rebuild = _prepared_audio_needs_rebuild(edit_path, raw_path, expected_duration)

                    if need_rebuild:
                        cmd = _build_audio_preprocess_command(raw_path, edit_path, start_offset, end_seconds, speed)
                        try:
                            subprocess.run(cmd, check=True, capture_output=True)
                            actual_duration = _probe_audio_duration(edit_path)
                            if not _duration_matches_expected(actual_duration, expected_duration):
                                raise RuntimeError(
                                    f"音频剪辑/倍速结果时长异常: expected={expected_duration:.3f}s actual={actual_duration:.3f}s"
                                )
                            audio_path_abs.append(edit_path)
                            prepared_audio_path = edit_path
                        except Exception as exc:
                            logger.error("音频剪辑/倍速处理失败: %s", exc, exc_info=True)
                            if strict_preprocess:
                                raise RuntimeError(f"音频剪辑/倍速处理失败，已中止合成: {exc}") from exc
                            audio_path_abs.append(raw_path)
                            prepared_audio_path = None
                    else:
                        audio_path_abs.append(edit_path)
                        prepared_audio_path = edit_path
                    
                    asr_request_specs.append({
                        'source_path': raw_path,
                        'start_seconds': start_offset,
                        'end_seconds': end_seconds,
                        'speed': speed,
                        'prepared_audio_path': prepared_audio_path,
                    })
                    subtitles.append(seg.get('subtitle', ''))

            # 2. 如果没传轨道数据，则回退到旧页面的“全局模式”
            if not audio_path_abs:
                logger.info("🎞️ [TASK] 模式: 全局单文件 (兼容旧版)")
                audio_raw_val = vid_params.get('audio_path') or v_vid_params.get('audio_path')
                if audio_raw_val:
                    if isinstance(audio_raw_val, str) and audio_raw_val.strip().startswith('['):
                        try:
                            paths_list = json.loads(audio_raw_val)
                            audio_path_abs = [to_abs_path(p) for p in paths_list]
                        except: audio_path_abs = to_abs_path(audio_raw_val)
                    else: audio_path_abs = to_abs_path(audio_raw_val)
                
                # 兼容旧版字幕存放位置
                subtitles = vid_params.get('subtitles') or v_vid_params.get('subtitles') or []

            # 3. 基础参数初始化
            asr_enabled = vid_params.get('asr_enabled', False)
            sub_burn_enabled = vid_params.get('sub_burn_enabled', False)
            asr_model_size = vid_params.get('asr_model') or params.get('asr_model') or 'base'
            
            logger.info(f"  - 初始配置 | ASR开关: {asr_enabled} | 字幕烧录开关: {sub_burn_enabled} | 模型: {asr_model_size}")
            
            # --- 智能 ASR 补偿逻辑 ---
            # 改进：如果开启了字幕烧录 (sub_burn_enabled) 且当前字幕为空，强制触发 ASR
            should_trigger_asr = asr_enabled or (sub_burn_enabled and not has_usable_subtitles(subtitles))
            
            if audio_path_abs and should_trigger_asr:
                from services.asr_cache_service import transcribe_with_cache
                logger.info(f"🔄 [TASK] 触发自动 ASR 识别 (模型: {asr_model_size})")
                processed_subs = []
                audio_list = audio_path_abs if isinstance(audio_path_abs, list) else [audio_path_abs]
                if not asr_request_specs:
                    asr_request_specs = [{'source_path': path} for path in audio_list]
                
                # 确保 subtitles 列表长度与音频列表一致
                if not subtitles: 
                    subtitles = ["" for _ in audio_list]
                elif not isinstance(subtitles, list):
                    subtitles = [subtitles]
                
                for i, a_p in enumerate(audio_list):
                    s_data = subtitles[i] if i < len(subtitles) else ""
                    if not s_data or s_data in ["识别中...", "识别失败"]:
                        try:
                            spec = asr_request_specs[i] if i < len(asr_request_specs) else {'source_path': a_p}
                            logger.info(f"  - 🎙️ 正在读取/生成第 {i+1}/{len(audio_list)} 个片段字幕: {os.path.basename(a_p)}")
                            s_data = transcribe_with_cache(
                                spec.get('source_path') or a_p,
                                model_size=asr_model_size,
                                start_seconds=spec.get('start_seconds'),
                                end_seconds=spec.get('end_seconds'),
                                speed=spec.get('speed', 1.0),
                                prepared_audio_path=spec.get('prepared_audio_path'),
                            )
                            
                        except Exception as e:
                            logger.error(f"❌ [TASK] 第 {i+1} 个片段 ASR 识别失败: {e}")
                            s_data = ""
                    processed_subs.append(s_data)
                subtitles = processed_subs
                logger.info(f"✅ [TASK] ASR 流程完成")
            
            # If sub_burn_enabled is False, we clear subtitles so synthesis methods don't render them
            final_subtitles = subtitles if sub_burn_enabled and has_usable_subtitles(subtitles) else None
            if final_subtitles:
                logger.info(f"📝 [TASK] 最终参与烧录的字幕条数: {len(final_subtitles)}")
            elif sub_burn_enabled:
                logger.warning("⚠️ [TASK] 字幕烧录已开启，但没有可用字幕，跳过字幕烧录")
    
            # --- Step 1: Video Synthesis Routing based on Aspect Ratio ---
            # 新版编辑器通过 aspect_ratio 参数显式指定比例，而将所有参数都放在 video 对象中
            global_aspect_ratio = params.get('aspect_ratio', '16:9')
            is_explicit_vertical = global_aspect_ratio == '9:16'
            
            # 判断逻辑：
            # 如果显式指定了 9:16，则只执行竖屏合成 (将 video 块的参数当做竖屏参数处理)
            # 否则，按照旧版逻辑：如果有 video 块则横屏，如果有 vertical_video 块则竖屏
            
            landscape_enabled = vid_params.get('enabled', False) and not is_explicit_vertical
            vertical_enabled = v_vid_params.get('enabled', False) or (vid_params.get('enabled', False) and is_explicit_vertical)

            if landscape_enabled:
                task = update_task_record(
                    task_id,
                    status='processing',
                    current_step='synthesis',
                    progress=10,
                    result=result_state,
                )
                
                video_path = to_abs_path(vid_params.get('video_path'))
                image_path = to_abs_path(vid_params.get('image_path'))
                
                logger.info(f"Task {task_id}: Starting landscape video synthesis...")
                logger.info(f"  - Video: {video_path}")
                logger.info(f"  - Image: {image_path}")
                logger.info(f"  - Audio Segments: {len(audio_path_abs) if isinstance(audio_path_abs, list) else 1}")
                
                video_c_path = to_abs_path(vid_params.get('video_c_path'))
                audio_c_path = to_abs_path(vid_params.get('audio_c_path'))
                audio_start = float(vid_params.get('audio_start', 0))
                audio_end_offset = float(vid_params.get('audio_end_offset', 0))
                cta_config = vid_params.get('cta')
                transform = vid_params.get('transform')
                subtitle_position = vid_params.get('subtitle_position')
                subtitle_size = vid_params.get('subtitle_size', 'normal')
                export_params = params.get('export_params')

                final_video_path = video_editor_service.process_mixed_content(
                    video_file=video_path,
                    image_file=image_path,
                    audio_file=audio_path_abs,
                    video_c_path=video_c_path,
                    audio_c_path=audio_c_path,
                    audio_start=audio_start,
                    audio_end_offset=audio_end_offset,
                    output_filename=f"{base_name}.mp4",
                    cta_config=cta_config,
                    subtitles=final_subtitles,
                    transform=transform,
                    subtitle_position=subtitle_position,
                    subtitle_size=subtitle_size,
                    export_params=export_params
                )
                logger.info(f"  - ✅ Landscape Done: {final_video_path}")
                result_state['video_path'] = final_video_path
                task = update_task_record(task_id, result=result_state)

            # --- Step 1.5: Vertical Synthesis ---
            if vertical_enabled:
                task = update_task_record(
                    task_id,
                    status='processing',
                    current_step='v_synthesis',
                    progress=30,
                    result=result_state,
                )
                
                # 如果是显式指定竖屏，则参数从 vid_params 取；否则从 v_vid_params 取
                active_v_params = vid_params if is_explicit_vertical else v_vid_params
                vertical_scheme = str(
                    active_v_params.get('scheme')
                    or active_v_params.get('render_scheme')
                    or 'scheme1'
                ).strip().lower()
                if vertical_scheme not in {'scheme1', 'scheme2', 'workbench', 'thumbnail_workbench'}:
                    vertical_scheme = 'scheme1'
                if vertical_scheme in {'workbench', 'thumbnail_workbench'}:
                    vertical_scheme = 'scheme2'
                vertical_thumb_params = params.get('vertical_thumbnail') if isinstance(params.get('vertical_thumbnail'), dict) else {}
                vertical_cover_params = params.get('vertical_cover') if isinstance(params.get('vertical_cover'), dict) else {}
                podcast_cover_params = params.get('podcast_cover') if isinstance(params.get('podcast_cover'), dict) else {}
                bilibili_cover_params = params.get('bilibili_cover') if isinstance(params.get('bilibili_cover'), dict) else {}
                
                v_image_path = to_abs_path(active_v_params.get('image_path'))
                if vertical_thumb_params and _enabled(vertical_thumb_params.get('enabled'), True):
                    vertical_scheme = str(
                        vertical_thumb_params.get('scheme')
                        or active_v_params.get('scheme')
                        or active_v_params.get('render_scheme')
                        or 'scheme1'
                    ).strip().lower()
                    screenshot_path = to_abs_path(
                        vertical_thumb_params.get('screenshot_path')
                        or active_v_params.get('image_path')
                        or vid_params.get('image_path')
                    )
                    main_title = vertical_thumb_params.get('main_title') or thumb_params.get('main_title') or bili_params.get('title') or ""
                    sub_title = vertical_thumb_params.get('sub_title') or thumb_params.get('sub_title') or ""
                    if vertical_scheme in {'scheme2', 'workbench', 'thumbnail_workbench'}:
                        workbench_institution = vertical_thumb_params.get('institution') or vertical_cover_params.get('institution') or '深度研报'
                        workbench_emotion = vertical_thumb_params.get('emotion') or vertical_cover_params.get('emotion') or 'bull'
                        workbench_footer = vertical_thumb_params.get('footer_text') or vertical_cover_params.get('footer_text') or '听见资本'
                        workbench_transform = vertical_thumb_params.get('image_transform') or vertical_cover_params.get('image_transform') or {}
                        v_image_path = thumbnail_service.generate_workbench_vertical_thumbnail(
                            main_title=main_title,
                            sub_title=sub_title,
                            screenshot_path=screenshot_path,
                            output_filename=f"vbg2_{base_name}.jpg",
                            institution=workbench_institution,
                            emotion=workbench_emotion,
                            footer_text=workbench_footer,
                            image_transform=workbench_transform,
                        )
                        vertical_cover_path = thumbnail_service.generate_workbench_vertical_cover(
                            main_title=vertical_cover_params.get('main_title') or main_title,
                            sub_title=vertical_cover_params.get('sub_title') or sub_title,
                            screenshot_path=vertical_cover_params.get('screenshot_path') or screenshot_path,
                            output_filename=f"vcover2_{base_name}.jpg",
                            institution=vertical_cover_params.get('institution') or workbench_institution,
                            emotion=vertical_cover_params.get('emotion') or workbench_emotion,
                            footer_text=vertical_cover_params.get('footer_text') or workbench_footer,
                            image_transform=vertical_cover_params.get('image_transform') or workbench_transform,
                        )
                        podcast_cover_path = thumbnail_service.generate_workbench_podcast_cover(
                            main_title=podcast_cover_params.get('main_title') or main_title,
                            sub_title=podcast_cover_params.get('sub_title') or sub_title,
                            screenshot_path=podcast_cover_params.get('screenshot_path') or screenshot_path,
                            output_filename=f"pcover2_{base_name}.jpg",
                            institution=podcast_cover_params.get('institution') or workbench_institution,
                            emotion=podcast_cover_params.get('emotion') or workbench_emotion,
                            footer_text=podcast_cover_params.get('footer_text') or workbench_footer,
                            image_transform=podcast_cover_params.get('image_transform') or workbench_transform,
                        )
                        bilibili_cover_path = _generate_scheme2_bilibili_cover(
                            params=bilibili_cover_params,
                            main_title=main_title,
                            sub_title=sub_title,
                            screenshot_path=screenshot_path,
                            base_name=base_name,
                            institution=workbench_institution,
                            emotion=workbench_emotion,
                            footer_text=workbench_footer,
                            image_transform=workbench_transform,
                        )
                    else:
                        vertical_scheme = 'scheme1'
                        vertical_cover_path = None
                        podcast_cover_path = None
                        bilibili_cover_path = None
                        v_image_path = thumbnail_service.generate_vertical_thumbnail(
                            main_title=main_title,
                            sub_title=sub_title,
                            date_text=vertical_thumb_params.get('date_text') or thumb_params.get('date_text') or "",
                            screenshot_path=screenshot_path,
                            output_filename=f"vbg_{base_name}.jpg",
                        )
                    vertical_background_paths = dict(result_state.get('vertical_background_paths') or {})
                    vertical_background_paths[vertical_scheme] = v_image_path
                    result_state = {
                        **result_state,
                        'vertical_background_path': v_image_path,
                        'vertical_background_paths': vertical_background_paths,
                        'vertical_video_scheme': vertical_scheme,
                        **({'vertical_cover_path': vertical_cover_path} if vertical_cover_path else {}),
                        **({'podcast_cover_path': podcast_cover_path} if podcast_cover_path else {}),
                        **({'bilibili_cover_path': bilibili_cover_path} if bilibili_cover_path else {}),
                    }
                    task = update_task_record(task_id, result=result_state)

                if not v_image_path or not os.path.exists(v_image_path):
                    # Fallback
                    v_image_path = to_abs_path(vid_params.get('image_path'))

                logger.info(f"Task {task_id}: Starting vertical video synthesis...")
                logger.info(f"  - Image: {v_image_path}")
                logger.info(f"  - Audio Segments: {len(audio_path_abs) if isinstance(audio_path_abs, list) else 1}")

                v_subtitle_position = active_v_params.get('subtitle_position')
                v_subtitle_size = active_v_params.get('subtitle_size', 'normal')
                v_subtitle_style = active_v_params.get('subtitle_style') if isinstance(active_v_params.get('subtitle_style'), dict) else None
                v_export_params = params.get('export_params')
                preview_seconds = active_v_params.get('preview_seconds') or params.get('preview_seconds')
                try:
                    preview_seconds = float(preview_seconds or 0)
                except (TypeError, ValueError):
                    preview_seconds = 0

                v_video_path = video_editor_service.process_vertical_video(
                    image_file=v_image_path,
                    audio_files=audio_path_abs,
                    subtitles=final_subtitles,
                    output_filename=f"v_{base_name}.mp4",
                    subtitle_position=v_subtitle_position,
                    subtitle_size=v_subtitle_size,
                    subtitle_style=v_subtitle_style,
                    export_params=v_export_params,
                    duration_limit=preview_seconds if preview_seconds > 0 else None
                )
                logger.info(f"  - ✅ Vertical Done: {v_video_path}")
                
                # 如果这是唯一的合成任务，把它也放到主 video_path 里方便前端下载
                vertical_paths = dict(result_state.get('vertical_video_paths') or {})
                vertical_paths[vertical_scheme] = v_video_path
                res_dict = {
                    **result_state,
                    'vertical_video_path': v_video_path,
                    'vertical_video_scheme': vertical_scheme,
                    'vertical_video_paths': vertical_paths,
                }
                if preview_seconds > 0:
                    res_dict['vertical_preview_seconds'] = preview_seconds
                if is_explicit_vertical or not final_video_path:
                    res_dict['video_path'] = v_video_path
                    
                result_state = res_dict
                task = update_task_record(task_id, result=result_state)
            
            # --- Step 1.6: Alternative Vertical Synthesis (Vertical Video 2) ---
            v_vid_2_params = params.get('vertical_video_2', {})
            if v_vid_2_params.get('enabled', False):
                task = update_task_record(task_id, current_step='v_synthesis_2', result=result_state)
                
                logger.info(f"Task {task_id}: Starting alternative vertical video synthesis...")
                
                v2_scheme = str(v_vid_2_params.get('scheme') or 'scheme1').strip().lower()
                if v2_scheme not in {'scheme1', 'scheme2'}:
                    v2_scheme = 'scheme1'
                v2_image_path = to_abs_path(v_vid_2_params.get('image_path'))
                v2_screenshot_path = to_abs_path(v_vid_2_params.get('screenshot_path') or v2_image_path)
                v2_main_title = v_vid_2_params.get('main_title') or ""
                v2_sub_title = v_vid_2_params.get('sub_title') or ""
                if v2_scheme == 'scheme2':
                    v2_image_path = thumbnail_service.generate_workbench_vertical_thumbnail(
                        main_title=v2_main_title,
                        sub_title=v2_sub_title,
                        screenshot_path=v2_screenshot_path,
                        output_filename=f"vbg2_alt_{base_name}.jpg",
                        institution=v_vid_2_params.get('institution') or '深度研报',
                        emotion=v_vid_2_params.get('emotion') or 'bull',
                        footer_text=v_vid_2_params.get('footer_text') or '听见资本',
                        image_transform=v_vid_2_params.get('image_transform') or {},
                    )
                else:
                    v2_image_path = thumbnail_service.generate_vertical_thumbnail(
                        main_title=v2_main_title,
                        sub_title=v2_sub_title,
                        date_text=v_vid_2_params.get('date_text') or "",
                        screenshot_path=v2_screenshot_path,
                        output_filename=f"vbg_alt_{base_name}.jpg",
                    )
                if v2_image_path and os.path.exists(v2_image_path):
                    v2_preview_seconds = v_vid_2_params.get('preview_seconds') or params.get('preview_seconds')
                    try:
                        v2_preview_seconds = float(v2_preview_seconds or 0)
                    except (TypeError, ValueError):
                        v2_preview_seconds = 0
                    v2_video_path = video_editor_service.process_vertical_video(
                        image_file=v2_image_path,
                        audio_files=audio_path_abs,
                        subtitles=final_subtitles,
                        output_filename=f"v2_{base_name}.mp4",
                        subtitle_position=v_vid_2_params.get('subtitle_position'),
                        subtitle_size=v_vid_2_params.get('subtitle_size', 'normal'),
                        subtitle_style=v_vid_2_params.get('subtitle_style') if isinstance(v_vid_2_params.get('subtitle_style'), dict) else None,
                        export_params=params.get('export_params'),
                        duration_limit=v2_preview_seconds if v2_preview_seconds > 0 else None,
                    )
                    vertical_paths = dict(result_state.get('vertical_video_paths') or {})
                    vertical_paths[v2_scheme] = v2_video_path
                    vertical_background_paths = dict(result_state.get('vertical_background_paths') or {})
                    vertical_background_paths[v2_scheme] = v2_image_path
                    result_state = {
                        **result_state,
                        'vertical_video_2_path': v2_video_path,
                        'vertical_video_2_scheme': v2_scheme,
                        'vertical_video_paths': vertical_paths,
                        'vertical_background_paths': vertical_background_paths,
                    }
                    task = update_task_record(task_id, result=result_state)
                else:
                    logger.warning(f"Task {task_id}: Skipping vertical_video_2, image path missing or invalid.")
            
            task = update_task_record(task_id, progress=50, result=result_state)
    
            # --- Step 2: Thumbnail ---
            final_cover_path = None
            if thumb_params:
                task = update_task_record(task_id, current_step='thumbnail', result=result_state)
                
                logger.info(f"Task {task_id}: Generating thumbnail...")
                
                final_cover_path = thumbnail_service.generate_thumbnail(
                    main_title=thumb_params.get('main_title'),
                    sub_title=thumb_params.get('sub_title'),
                    date_text=thumb_params.get('date_text'),
                    tag_text=thumb_params.get('tag_text'),
                    bg_preset_index=int(thumb_params.get('bg_index', 0)),
                    layout=thumb_params.get('layout', 'center'),
                    output_filename=f"{base_name}.jpg",
                    custom_bg_path=to_abs_path(thumb_params.get('custom_bg_path')),
                    overlay_opacity=float(thumb_params.get('overlay_opacity', 0.5)),
                    transform=thumb_params.get('transform'),
                    mode=thumb_params.get('mode', 'preset')
                )
                
                result_state = {**result_state, 'cover_path': final_cover_path}
                task = update_task_record(task_id, result=result_state, progress=70)
            else:
                task = update_task_record(task_id, progress=70, result=result_state)
    
            # --- Step 3: Upload ---
            if bili_params.get('enabled', False):
                task = update_task_record(task_id, current_step='upload', result=result_state)
                
                # 智能选择上传文件：如果没开横屏开了竖屏，就传竖屏；否则传横屏
                upload_path = final_video_path
                if not vid_params.get('enabled', True) and v_vid_params.get('enabled', False):
                    upload_path = v_video_path
                
                logger.info(f"Task {task_id}: Uploading to Bilibili... [Path: {upload_path}]")
                if not upload_path or not os.path.exists(upload_path):
                    logger.error(f"❌ [UPLOAD] 找不到可上传的视频文件: {upload_path}")
                    raise FileNotFoundError(f"找不到上传视频: {upload_path}")

                # 从平台凭据表读取 B 站凭证
                bili_auth = resolve_bilibili_auth(
                    account_id=bili_params.get("credential_id") or bili_params.get("platform_credential_id"),
                    required=True,
                )
                db.session.remove()
                uploader = BiliUploader(
                    sessdata=bili_auth.get('sessdata'),
                    bili_jct=bili_auth.get('bili_jct'),
                    buvid3=bili_auth.get('buvid3'),
                    dedeuserid=bili_auth.get('dedeuserid')
                )
                
                async def _do_upload():
                    return await uploader.upload_single(
                        video_path=upload_path,
                        title=bili_params.get('title'),
                        desc=bili_params.get('desc', ''),
                        tid=int(bili_params.get('tid', 36)),
                        tags=bili_params.get('tags', []),
                        cover_path=final_cover_path,
                        copyright=int(bili_params.get('copyright', 1)),
                        no_reprint=int(bili_params.get('no_reprint', 1)),
                        dynamic=bili_params.get('dynamic', ''),
                        neutral_mark=bili_params.get('neutral_mark') or DEFAULT_NEUTRAL_MARK,
                        charging_pay=bili_params.get('charging_pay'),
                        delay_time=bili_params.get('delay_time') or bili_params.get('dtime'),
                        season_id=bili_params.get('season_id'),
                        is_only_self=(
                            bili_params.get('is_only_self')
                            or bili_params.get('isOnlySelf')
                            or bili_params.get('only_self')
                            or 0
                        ),
                        space_hidden=bili_params.get('space_hidden'),
                        creation_statement_id=(
                            bili_params.get('creation_statement_id')
                            or bili_params.get('creationStatementId')
                        )
                    )
                
                try:
                    upload_result = asyncio.run(_do_upload())
                    try:
                        from services.platform_credential_service import mark_credential_success

                        mark_credential_success(bili_auth.get("credential_id"))
                    except Exception:
                        logger.exception("[PUBLISHER] Bilibili credential success mark failed")
                except Exception as exc:
                    try:
                        from services.platform_credential_service import is_auth_error, mark_credential_error

                        mark_credential_error(
                            bili_auth.get("credential_id"),
                            exc,
                            expired=is_auth_error(exc),
                        )
                    except Exception:
                        logger.exception("[PUBLISHER] Bilibili credential error mark failed")
                    raise
                
                result_state = {**result_state, 'bilibili_result': upload_result}
                task = update_task_record(task_id, result=result_state)
            else:
                logger.info(f"Task {task_id}: Skipping Bilibili upload (disabled or not enabled).")
                task = update_task_record(task_id, progress=90, result=result_state)
    
            # --- Step 4: YouTube Upload ---
            yt_params = params.get('youtube', {})
            if yt_params and yt_params.get('enabled', False):
                task = update_task_record(task_id, current_step='upload_youtube', result=result_state)
                
                # 智能选择上传文件
                upload_path_yt = final_video_path
                if not vid_params.get('enabled', True) and v_vid_params.get('enabled', False):
                    upload_path_yt = v_video_path

                logger.info(f"Task {task_id}: Uploading to YouTube... [Path: {upload_path_yt}]")
                try:
                    yt_uploader = YouTubeUploader()
                    yt_response = yt_uploader.upload_video(
                        video_path=upload_path_yt,
                        title=yt_params.get('title', bili_params.get('title')), # Fallback to Bili title
                        description=yt_params.get('desc', bili_params.get('desc', '')),
                        tags=yt_params.get('tags', bili_params.get('tags', [])),
                        category_id=yt_params.get('category_id', '22'),
                        privacy_status=yt_params.get('privacy', 'private'),
                        thumbnail_path=final_cover_path
                    )
                    result_state = {**result_state, 'youtube_result': yt_response}
                except Exception as ye:
                    logger.error(f"YouTube upload failed for task {task_id}: {ye}", exc_info=True)
                    result_state = {**result_state, 'youtube_error': str(ye)}
                
                task = update_task_record(task_id, result=result_state)
    
            task = update_task_record(
                task_id,
                status='completed',
                current_step='done',
                progress=100,
                result=result_state,
                error_message=None,
                completed_at=datetime.utcnow(),
            )
            
            total_duration = time.time() - overall_start
            logger.info(f"✨ [TASK] 任务 {task_id} 全部处理完成! 总耗时: {total_duration:.2f}s")
    
        except retry_exception_types():
            raise
        except Exception as e:
            logger.error(f"Task {task_id} failed: {e}", exc_info=True)
            try:
                update_task_record(
                    task_id,
                    status='failed',
                    error_message=str(e),
                    completed_at=datetime.utcnow(),
                )
            except Exception as persist_error:
                logger.error(
                    "Task %s failed and failed-state persistence also failed: %s",
                    task_id,
                    persist_error,
                    exc_info=True,
                )
            raise

@celery_app.task(bind=True)
def run_youtube_upload_only(self, task_id):
    """
    Supplements an existing task by uploading its already-synthesized video to YouTube.
    """
    from app import create_app
    app = create_app()
    
    with app.app_context():
        task = _load_task(task_id, *TASK_PAYLOAD_COLUMNS)
        if not task:
            logger.error(f"Task {task_id} not found")
            return

        guard_publication_task_start(
            self,
            task_id,
            feature="publisher_upload",
            queue=PLATFORM_UPLOAD_QUEUE,
        )

        params = task.params or {}
        result_state = dict(task.result or {})

        # Check if we have the necessary files
        video_path = result_state.get('video_path')
        if not video_path or not os.path.exists(video_path):
            logger.error(f"Cannot supplement YouTube: Video file missing at {video_path}")
            update_task_record(task_id, error_message="无法补发：找不到原始视频文件")
            return
    
        try:
            update_task_record(task_id, status='processing', current_step='upload_youtube')
            yt_params = params.get('youtube') or {}
            bili_params = params.get('bilibili') or {}
            final_cover_path = result_state.get('cover_path')
    
            logger.info(f"Task {task_id}: Supplementing YouTube upload...")
            
            yt_uploader = YouTubeUploader()
            yt_response = yt_uploader.upload_video(
                video_path=video_path,
                title=yt_params.get('title') or bili_params.get('title'),
                description=yt_params.get('desc') or bili_params.get('desc', ''),
                tags=yt_params.get('tags') or bili_params.get('tags', []),
                category_id=yt_params.get('category_id', '22'),
                privacy_status=yt_params.get('privacy', 'private'),
                thumbnail_path=final_cover_path
            )
            
            result_state = {**result_state, 'youtube_result': yt_response}
            update_task_record(
                task_id,
                result=result_state,
                status='completed',
                current_step='done',
                progress=100,
            )
            logger.info(f"Task {task_id}: YouTube supplement successful.")
    
        except Exception as e:
            logger.error(f"YouTube supplement failed for task {task_id}: {e}", exc_info=True)
            # Don't mark the whole task as failed if it was previously completed, 
            # just record the error in result
            result_state = {**result_state, 'youtube_error': str(e)}
            update_task_record(
                task_id,
                result=result_state,
                status='completed',
                current_step='done',
            )


@celery_app.task(bind=True)
def run_bilibili_upload_only(self, task_id):
    """
    Upload an already-rendered video to Bilibili without running synthesis again.
    """
    from app import create_app

    app = create_app()

    with app.app_context():
        context = _load_bilibili_upload_context_with_retries(task_id)
        if not context:
            logger.error("Task %s not found", task_id)
            return

        request_id = str(getattr(getattr(self, "request", None), "id", "") or "")
        current_celery_id = context.celery_task_id
        if request_id and current_celery_id and request_id != current_celery_id:
            logger.info(
                "Task %s received stale Bilibili upload message %s, current celery id is %s; skipping.",
                task_id,
                request_id,
                current_celery_id,
            )
            return
        if context.status in {"completed", "canceled"}:
            logger.info("Task %s is terminal (%s) before Bilibili upload; skipping.", task_id, context.status)
            return
        guard_publication_task_start(
            self,
            task_id,
            feature="publisher_upload",
            queue=PLATFORM_UPLOAD_QUEUE,
        )

        video_path = _first_existing_path(context.video_path)
        cover_path = _first_existing_path(context.cover_path)
        if not video_path:
            message = "无法补发 B站：找不到已生成的视频文件"
            update_task_record(
                task_id,
                status="failed",
                current_step="failed",
                progress=0,
                error_message=message,
                completed_at=datetime.utcnow(),
            )
            raise FileNotFoundError(message)

        title = str(context.title or "").strip() or os.path.splitext(os.path.basename(video_path))[0]
        desc = str(context.description or "").strip()
        tags = _normalize_tag_list(context.tags, default=["财经", "音频", "视频"])

        try:
            update_task_record(
                task_id,
                status="processing",
                current_step="upload_bilibili",
                progress=max(context.progress, 10),
                error_message=None,
            )

            bili_auth = resolve_bilibili_auth(
                account_id=context.credential_id,
                required=True,
            )
            db.session.remove()
            uploader = BiliUploader(
                sessdata=bili_auth.get("sessdata"),
                bili_jct=bili_auth.get("bili_jct"),
                buvid3=bili_auth.get("buvid3"),
                dedeuserid=bili_auth.get("dedeuserid"),
            )

            async def _do_upload():
                return await uploader.upload_single(
                    video_path=video_path,
                    title=title,
                    desc=desc,
                    tid=context.tid,
                    tags=tags,
                    cover_path=cover_path,
                    copyright=context.copyright,
                    no_reprint=context.no_reprint,
                    dynamic=context.dynamic,
                    neutral_mark=context.neutral_mark or DEFAULT_NEUTRAL_MARK,
                    charging_pay=context.charging_pay,
                    delay_time=context.delay_time,
                    season_id=context.season_id,
                    is_only_self=context.is_only_self,
                    space_hidden=context.space_hidden,
                    creation_statement_id=context.creation_statement_id,
                )

            try:
                upload_result = asyncio.run(_do_upload())
                try:
                    from services.platform_credential_service import mark_credential_success

                    mark_credential_success(bili_auth.get("credential_id"))
                except Exception:
                    logger.exception("[PUBLISHER] Bilibili credential success mark failed")
            except Exception as exc:
                try:
                    from services.platform_credential_service import is_auth_error, mark_credential_error

                    mark_credential_error(
                        bili_auth.get("credential_id"),
                        exc,
                        expired=is_auth_error(exc),
                    )
                except Exception:
                    logger.exception("[PUBLISHER] Bilibili credential error mark failed")
                raise

            result_patch = {
                "video_path": video_path,
                **({"cover_path": cover_path} if cover_path else {}),
                "bilibili_result": upload_result,
                "bilibili_title": title,
                "bilibili_credential_id": bili_auth.get("credential_id"),
                "bilibili_credential_name": bili_auth.get("credential_name"),
            }
            update_task_record(
                task_id,
                status="completed",
                current_step="done",
                progress=100,
                result_patch=result_patch,
                completed_at=datetime.utcnow(),
            )
            logger.info("Task %s: Bilibili upload supplement successful.", task_id)

        except retry_exception_types():
            raise
        except Exception as exc:
            logger.error("Bilibili upload supplement failed for task %s: %s", task_id, exc, exc_info=True)
            update_task_record(
                task_id,
                result_patch={"bilibili_error": str(exc)},
                status="failed",
                current_step="failed",
                progress=10,
                error_message=str(exc),
                completed_at=datetime.utcnow(),
            )
            raise
