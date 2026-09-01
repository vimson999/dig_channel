"""Durable orchestration for the generic script-to-video workflow.

Browser automation, ASR, and FFmpeg are intentionally executed by Celery
tasks.  Flask handlers only persist an explicit stage request and enqueue one
of the public tasks in this module.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from celery.exceptions import Retry
from sqlalchemy.orm import load_only
from sqlalchemy.orm.attributes import flag_modified

from app import create_app
from celery_config import celery_app
from models import db
from models.task import PublicationTask
from services.doubao_browser_tts import FEATURE_NAME as DOUBAO_TTS_FEATURE_NAME
from services.doubao_browser_tts import QUEUE_NAME as DOUBAO_TTS_QUEUE_NAME
from services.asr_cache_service import transcribe_with_cache
from services.gemini_browser import (
    GeminiBrowserHumanInterventionError,
    GeminiBrowserOptions,
    GeminiBrowserWorker,
)
from services.report_audio_workflow import combine_wav_files
from services.report_stick_figure_audio_alignment import align_storyboard_to_audio
from services.report_stick_figure_video import audio_duration_seconds, render_scene_timeline
from services.report_visual_plan_service import (
    normalize_storyboard_response,
    storyboard_prompt,
    storyboard_response_covers_script,
)
from services.report_visual_scheme import visual_scheme
from services.runtime_guard import apply_async_after_db_release, end_db_transaction_before_external_io
from services.script_video_workflow import (
    AUDIO_STEP,
    FEATURE_NAME,
    IMAGE_FEATURE_NAME,
    VIDEO_STEP,
    audio_chunks_cover_source,
    compose_scene_image_prompt,
    normalize_script,
    safe_output_file,
    scene_image_output_path,
    split_audio_chunks,
    text_hash,
    validate_audio_edit_options,
    validate_scene_rows,
    workflow_output_root,
)
from tasks.doubao_browser_tts import process_doubao_browser_tts_job


LOGGER = logging.getLogger("SCRIPT_VIDEO_WORKFLOW")

AUDIO_ACTIVE_STATUSES = {"pending", "processing", "retrying"}
AUDIO_FAILURE_STATUSES = {"failed", "blocked_by_captcha", "canceled"}
AUDIO_RETRY_DELAY_SECONDS = 10
AUDIO_RETRY_MAX_RETRIES = 360
AUDIO_EDIT_TIMEOUT_SECONDS = 1_800
IMAGE_ACTIVE_STATUSES = {"pending", "processing", "retrying"}
IMAGE_BLOCKED_STATUSES = {"blocked_by_login", "blocked_by_captcha"}
IMAGE_FAILURE_STATUSES = {"failed", "canceled"}
STORYBOARD_ACTIVE_STEPS = {"storyboard_queued", "storyboard_processing"}
STORYBOARD_INCOMPLETE_FOLLOWUP_PROMPT = (
    "你刚才没有覆盖完最初提供的完整口播文案。"
    "请检查已经输出的所有分镜：凡是存在【文案原句】但缺少【AI 提示词】的分镜，"
    "先按原编号补齐完整四字段；然后从上一次最后一个完整【文案原句】之后继续输出尚未覆盖的剩余分镜，"
    "保持原文顺序且逐字复制，不要重复已经完整记录的内容；"
    "每个分镜必须继续包含【文案原句】【画面动作】【视觉隐喻】【AI 提示词】，"
    "即使上一轮出现【全文分镜完成】但仍有字段缺失，也必须补齐后再结束；"
    "本轮仍然只允许输出文字，严禁调用任何图片生成、绘图或图片编辑工具，"
    "不得生成、插入或展示图片。"
)

IMAGE_CHILD_COLUMNS = (
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
    PublicationTask.parent_task_id,
    PublicationTask.parent_feature,
    PublicationTask.created_at,
    PublicationTask.completed_at,
)
WORKFLOW_PARENT_COLUMNS = (
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
    PublicationTask.task_title,
    PublicationTask.task_video_path,
    PublicationTask.report_visual_scheme_key,
    PublicationTask.waiting_child_label,
    PublicationTask.waiting_child_id,
    PublicationTask.waiting_child_status,
    PublicationTask.waiting_child_step,
    PublicationTask.waiting_child_updated_at,
    PublicationTask.created_at,
    PublicationTask.updated_at,
    PublicationTask.completed_at,
)


@celery_app.task(
    bind=True,
    name="tasks.script_video_workflow.process_audio",
    store_errors_even_if_ignored=True,
)
def process_audio(self, workflow_id: str):
    app = create_app()
    with app.app_context():
        try:
            return _process_audio(workflow_id, celery_task=self)
        except Retry:
            raise
        except Exception as exc:  # Durable error projection; do not lose finished children.
            LOGGER.exception("script-to-video audio stage failed: %s", workflow_id)
            _mark_parent_failed(workflow_id, "audio_failed", str(exc))
            return False


@celery_app.task(
    bind=True,
    name="tasks.script_video_workflow.prepare_uploaded_audio",
    store_errors_even_if_ignored=True,
)
def prepare_uploaded_audio(self, workflow_id: str):
    """Normalize an uploaded source into the non-final WAV used by the editor."""

    app = create_app()
    with app.app_context():
        try:
            return _prepare_uploaded_audio(str(workflow_id))
        except Exception as exc:
            LOGGER.exception("script-to-video uploaded audio preparation failed: %s", workflow_id)
            _mark_parent_failed(str(workflow_id), "audio_upload_failed", str(exc))
            return False
        finally:
            db.session.remove()


@celery_app.task(
    bind=True,
    name="tasks.script_video_workflow.finalize_audio_edit",
    store_errors_even_if_ignored=True,
)
def finalize_audio_edit(self, workflow_id: str):
    """Render one user-confirmed trim/speed revision into the final WAV."""

    app = create_app()
    with app.app_context():
        try:
            return _finalize_audio_edit(str(workflow_id))
        except Exception as exc:
            LOGGER.exception("script-to-video audio edit finalization failed: %s", workflow_id)
            _mark_parent_failed(str(workflow_id), "audio_edit_failed", str(exc))
            return False
        finally:
            db.session.remove()


def _process_audio(workflow_id: str, *, celery_task=None) -> dict[str, Any] | bool:
    parent = _workflow_or_raise(workflow_id)
    if parent.status == "canceled":
        return False
    if parent.status == "completed" and _audio_payload(parent):
        return _audio_payload(parent)

    workflow = _workflow_params(parent)
    source_text = normalize_script(workflow.get("source_text"))
    chunks = _validated_audio_chunks(workflow.get("audio_chunks"), source_text)
    children = _create_or_reuse_audio_children(parent, chunks)

    blocked = next((child for child in children if child.status == "blocked_by_captcha"), None)
    if blocked:
        _mark_parent_blocked(parent, blocked, "豆包浏览器朗读需要人工完成验证")
        return False

    failed = next((child for child in children if child.status in AUDIO_FAILURE_STATUSES), None)
    if failed:
        message = str(failed.error_message or "豆包浏览器朗读子任务失败")
        _mark_parent_failed(parent.id, "audio_failed", message)
        return False

    pending = [child for child in children if child.status in AUDIO_ACTIVE_STATUSES]
    if pending:
        _mark_audio_waiting(parent, children, pending[0])
        if celery_task is not None:
            raise celery_task.retry(
                countdown=AUDIO_RETRY_DELAY_SECONDS,
                max_retries=AUDIO_RETRY_MAX_RETRIES,
            )
        return {
            "waiting": True,
            "chunks_total": len(children),
            "chunks_completed": len(children) - len(pending),
            "waiting_child_id": pending[0].id,
        }

    return _finalize_audio_stage(parent.id)


def _create_or_reuse_audio_children(
    parent: PublicationTask,
    chunks: Iterable[str],
) -> list[PublicationTask]:
    """Return one durable child per audio chunk without repeating valid work."""

    parent = _workflow_or_raise(parent.id)
    chunk_rows = [str(item or "") for item in chunks]
    if not chunk_rows:
        raise ValueError("没有可提交给豆包的语音分段")

    children: list[PublicationTask] = []
    for index, chunk in enumerate(chunk_rows, start=1):
        if not chunk.strip():
            raise ValueError(f"第 {index} 段语音文案不能为空")
        expected_hash = text_hash(chunk)
        matches = _audio_children_matching(parent.id, index, expected_hash)

        completed = next((item for item in matches if _has_usable_wav(item)), None)
        if completed:
            children.append(completed)
            continue

        active = next((item for item in matches if item.status in AUDIO_ACTIVE_STATUSES), None)
        if active:
            children.append(active)
            continue

        attempt = 1 + max(
            [int(item.doubao_tts_chunk_attempt or 0) for item in matches] or [0]
        )
        children.append(_create_audio_child(parent, chunk, index, len(chunk_rows), attempt))
    return children


def _create_audio_child(
    parent: PublicationTask,
    text: str,
    index: int,
    total: int,
    attempt: int,
) -> PublicationTask:
    metadata = {
        "source": FEATURE_NAME,
        "parent_task_id": parent.id,
        "chunk_index": index,
        "chunk_total": total,
        "chunk_attempt": attempt,
        "text_hash": text_hash(text),
    }
    child = PublicationTask(
        status="pending",
        current_step="queued",
        progress=0,
        params={
            "feature": DOUBAO_TTS_FEATURE_NAME,
            "user_id": parent.user_id,
            "parent_feature": FEATURE_NAME,
            "parent_task_id": parent.id,
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
    db.session.add(child)
    db.session.commit()

    try:
        celery_result = apply_async_after_db_release(
            process_doubao_browser_tts_job,
            args=[child.id],
            queue=_doubao_queue_name(),
            retry=False,
        )
    except Exception as exc:
        child.status = "failed"
        child.current_step = "queue_failed"
        child.error_message = f"豆包浏览器朗读入队失败: {exc}"
        child.completed_at = datetime.utcnow()
        db.session.commit()
        return child

    child.celery_task_id = str(getattr(celery_result, "id", "") or "") or None
    db.session.commit()
    return child


def _finalize_audio_stage(workflow_id: str) -> dict[str, Any]:
    """Merge Doubao parts into the editable source WAV, not a final deliverable."""

    parent = _workflow_or_raise(workflow_id)
    workflow = _workflow_params(parent)
    source_text = normalize_script(workflow.get("source_text"))
    chunks = _validated_audio_chunks(workflow.get("audio_chunks"), source_text)
    ordered_children: list[PublicationTask] = []
    wav_paths: list[str] = []

    for index, chunk in enumerate(chunks, start=1):
        child = next(
            (
                item
                for item in _audio_children_matching(parent.id, index, text_hash(chunk))
                if _has_usable_wav(item)
            ),
            None,
        )
        if not child:
            raise ValueError(f"豆包浏览器朗读第 {index}/{len(chunks)} 段尚未完成")
        audio = _child_audio_payload(child)
        ordered_children.append(child)
        wav_paths.append(str(audio["wav_path"]))

    merged = dict(combine_wav_files(wav_paths, str(_source_audio_output_path(parent.id))) or {})
    if not merged.get("wav_path"):
        raise ValueError("WAV 合并没有返回文件路径")
    audio_result = {
        "state": "edit_ready",
        "source_type": "doubao_browser_tts",
        "source_wav_path": str(merged["wav_path"]),
        "source_seconds": float(merged.get("seconds") or 0),
        "source_bytes": int(merged.get("bytes") or 0),
        "source_sample_rate": int(merged.get("sample_rate") or 0),
        "edit": {
            "trim_start_seconds": 0.0,
            "trim_end_seconds": float(merged.get("seconds") or 0),
            "speed": 1.0,
            "revision": 0,
        },
        "provider": "doubao_browser_tts",
        "chunks_total": len(chunks),
        "chunks_completed": len(chunks),
        "child_task_ids": [child.id for child in ordered_children],
        "source_text_hash": text_hash(source_text),
        "chunks": int(merged.get("chunks") or len(chunks)),
    }

    result = dict(parent.result or {})
    result["audio"] = audio_result
    result["stage"] = "audio_edit_ready"
    _append_log(result, "audio_edit_ready", "豆包浏览器语音已合并；请试听、裁剪或调速后生成最终 WAV", 40)
    parent.status = "processing"
    parent.current_step = "audio_edit_ready"
    parent.progress = max(int(parent.progress or 0), 40)
    parent.error_message = None
    parent.completed_at = None
    parent.waiting_child_label = None
    parent.waiting_child_id = None
    parent.waiting_child_status = None
    parent.waiting_child_step = None
    parent.waiting_child_updated_at = None
    parent.result = result
    parent.sync_projection_fields()
    flag_modified(parent, "result")
    db.session.commit()
    return audio_result


def _prepare_uploaded_audio(workflow_id: str) -> dict[str, Any] | bool:
    """Transcode an uploaded source to the editor's canonical WAV without unlocking next steps."""

    parent = _workflow_or_raise(workflow_id)
    audio = _audio_payload(parent)
    state = str(audio.get("state") or "")
    existing_source = _workflow_audio_file(parent, audio.get("source_wav_path"))
    if state == "edit_ready" and existing_source and existing_source.is_file():
        return audio
    if state not in {"upload_queued", "upload_processing"}:
        return False
    upload = _workflow_audio_file(parent, audio.get("upload_path"))
    if not upload or not upload.is_file() or upload.stat().st_size <= 0:
        raise ValueError("上传音频不存在或为空")
    output = _source_audio_output_path(parent.id)
    _set_audio_edit_progress(
        parent,
        state="upload_processing",
        step="audio_upload_processing",
        progress=14,
        message="正在转换上传音频为可编辑 WAV",
    )
    end_db_transaction_before_external_io("uploaded audio normalization")
    _transcode_audio_to_wav(upload, output)
    seconds = audio_duration_seconds(str(output))
    if seconds <= 0:
        raise ValueError("上传音频没有有效时长")

    fresh = _workflow_or_raise(workflow_id)
    fresh_audio = _audio_payload(fresh)
    if str(fresh_audio.get("state") or "") not in {"upload_queued", "upload_processing"}:
        return False
    normalized = dict(fresh_audio)
    normalized.update({
        "state": "edit_ready",
        "source_type": "upload",
        "source_wav_path": str(output),
        "source_seconds": round(float(seconds), 3),
        "source_bytes": int(output.stat().st_size),
        "edit": {
            "trim_start_seconds": 0.0,
            "trim_end_seconds": round(float(seconds), 3),
            "speed": 1.0,
            "revision": 0,
        },
    })
    normalized.pop("wav_path", None)
    normalized.pop("seconds", None)
    normalized.pop("bytes", None)
    result = dict(fresh.result or {})
    result["audio"] = normalized
    result["stage"] = "audio_edit_ready"
    _append_log(result, "audio_edit_ready", "上传语音已转换完成；请试听、裁剪或调速后生成最终 WAV", 20)
    fresh.status = "pending"
    fresh.current_step = "audio_edit_ready"
    fresh.progress = max(20, min(int(fresh.progress or 0), 40))
    fresh.error_message = None
    fresh.completed_at = None
    fresh.waiting_child_label = None
    fresh.waiting_child_id = None
    fresh.waiting_child_status = None
    fresh.waiting_child_step = None
    fresh.waiting_child_updated_at = None
    fresh.result = result
    fresh.sync_projection_fields()
    flag_modified(fresh, "result")
    db.session.commit()
    return normalized


def _finalize_audio_edit(workflow_id: str) -> dict[str, Any] | bool:
    """Apply the persisted head/tail trim and speed to one final WAV revision."""

    parent = _workflow_or_raise(workflow_id)
    audio = _audio_payload(parent)
    source = _workflow_audio_file(parent, audio.get("source_wav_path"))
    if not source or not source.is_file() or source.stat().st_size <= 0:
        raise ValueError("可编辑的原始 WAV 不存在")
    source_seconds = float(audio.get("source_seconds") or audio_duration_seconds(str(source)))
    edit = validate_audio_edit_options(audio.get("edit"), source_seconds=source_seconds)
    try:
        revision = int((audio.get("edit") or {}).get("revision") or 1)
    except (TypeError, ValueError):
        revision = 1
    revision = max(1, revision)
    output = _final_audio_output_path(parent.id, revision)
    _set_audio_edit_progress(
        parent,
        state="edit_processing",
        step="audio_edit_processing",
        progress=28,
        message="正在生成裁剪与倍速后的最终 WAV",
    )
    end_db_transaction_before_external_io("audio edit finalization")
    _transcode_audio_to_wav(
        source,
        output,
        trim_start_seconds=edit["trim_start_seconds"],
        trim_end_seconds=edit["trim_end_seconds"],
        speed=edit["speed"],
    )
    seconds = audio_duration_seconds(str(output))
    if seconds <= 0:
        raise ValueError("最终 WAV 没有有效时长")

    fresh = _workflow_or_raise(workflow_id)
    fresh_audio = _audio_payload(fresh)
    pending_edit = dict(fresh_audio.get("edit") or {})
    if int(pending_edit.get("revision") or revision) != revision:
        return False
    finalized = dict(fresh_audio)
    finalized.update({
        "state": "finalized",
        "wav_path": str(output),
        "seconds": round(float(seconds), 3),
        "bytes": int(output.stat().st_size),
        "edit": {**edit, "revision": revision},
    })
    result = dict(fresh.result or {})
    result["audio"] = finalized
    result["alignment"] = None
    result["video"] = None
    result["stage"] = AUDIO_STEP
    _append_log(result, "audio_ready", "最终 WAV 已生成；现在可以投递 Gemini 规划分镜", 40)
    fresh.status = "processing"
    fresh.current_step = AUDIO_STEP
    fresh.progress = max(40, min(int(fresh.progress or 0), 40))
    fresh.error_message = None
    fresh.completed_at = None
    fresh.waiting_child_label = None
    fresh.waiting_child_id = None
    fresh.waiting_child_status = None
    fresh.waiting_child_step = None
    fresh.waiting_child_updated_at = None
    fresh.result = result
    fresh.sync_projection_fields()
    flag_modified(fresh, "result")
    db.session.commit()
    return finalized


def _set_audio_edit_progress(
    parent: PublicationTask,
    *,
    state: str,
    step: str,
    progress: int,
    message: str,
) -> None:
    result = dict(parent.result or {})
    audio = _audio_payload(parent)
    audio["state"] = state
    result["audio"] = audio
    result["stage"] = step
    _append_log(result, step, message, progress)
    parent.status = "processing"
    parent.current_step = step
    parent.progress = int(progress)
    parent.error_message = None
    parent.completed_at = None
    parent.result = result
    parent.sync_projection_fields()
    flag_modified(parent, "result")
    db.session.commit()


def _workflow_audio_file(parent: PublicationTask, path_value: Any) -> Path | None:
    if not path_value:
        return None
    try:
        return safe_output_file(workflow_output_root(parent.id), str(path_value))
    except ValueError:
        return None


def _source_audio_output_path(workflow_id: str) -> Path:
    path = workflow_output_root(workflow_id) / "audio" / "source.wav"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _final_audio_output_path(workflow_id: str, revision: int) -> Path:
    path = workflow_output_root(workflow_id) / "audio" / f"final-{max(1, int(revision)):03d}.wav"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _transcode_audio_to_wav(
    source: Path,
    output: Path,
    *,
    trim_start_seconds: float | None = None,
    trim_end_seconds: float | None = None,
    speed: float = 1.0,
) -> None:
    """Render one bounded input into a canonical mono 24kHz PCM WAV atomically."""

    source = Path(source).resolve()
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    part = output.with_name(f".{output.stem}.{uuid.uuid4().hex}.part.wav")
    filters: list[str] = []
    if trim_start_seconds is not None or trim_end_seconds is not None:
        start = max(0.0, float(trim_start_seconds or 0.0))
        trim = f"atrim=start={start:.3f}"
        if trim_end_seconds is not None:
            trim += f":end={float(trim_end_seconds):.3f}"
        filters.extend([trim, "asetpts=PTS-STARTPTS"])
    if abs(float(speed) - 1.0) > 0.0001:
        filters.append(f"atempo={float(speed):.3f}")
    command = [
        os.getenv("FFMPEG_BINARY", "ffmpeg"),
        "-y",
        "-hide_banner",
        "-loglevel", "error",
        "-i", str(source),
        "-vn",
        "-map", "0:a:0",
    ]
    if filters:
        command.extend(["-af", ",".join(filters)])
    command.extend(["-acodec", "pcm_s16le", "-ar", "24000", "-ac", "1", str(part)])
    try:
        subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=AUDIO_EDIT_TIMEOUT_SECONDS,
        )
        if not part.is_file() or part.stat().st_size <= 44:
            raise ValueError("音频转换没有生成有效 WAV")
        part.replace(output)
    except subprocess.TimeoutExpired as exc:
        raise ValueError("音频处理超时，请裁剪更短的片段后重试") from exc
    except (OSError, subprocess.SubprocessError) as exc:
        detail = str(getattr(exc, "stderr", "") or str(exc)).strip()[-1000:]
        raise ValueError(f"音频转换失败：{detail or 'ffmpeg 执行失败'}") from exc
    finally:
        try:
            part.unlink(missing_ok=True)
        except OSError:
            pass


def _audio_children_matching(
    parent_id: str,
    index: int,
    expected_hash: str,
) -> list[PublicationTask]:
    return (
        PublicationTask.query.filter(
            PublicationTask.feature == DOUBAO_TTS_FEATURE_NAME,
            PublicationTask.parent_feature == FEATURE_NAME,
            PublicationTask.parent_task_id == str(parent_id),
            PublicationTask.doubao_tts_chunk_index == int(index),
            PublicationTask.doubao_tts_text_hash == expected_hash,
        )
        .order_by(PublicationTask.created_at.desc())
        .all()
    )


def _validated_audio_chunks(value: Any, source_text: str) -> list[str]:
    raw_chunks = value if isinstance(value, list) else []
    chunks = [str(item or "") for item in raw_chunks if str(item or "").strip()]
    if not chunks:
        chunks = split_audio_chunks(source_text)
    if not audio_chunks_cover_source(chunks, source_text):
        raise ValueError("语音分段未完整覆盖原文")
    return chunks


def _workflow_or_raise(workflow_id: str) -> PublicationTask:
    parent = PublicationTask.query.options(load_only(*WORKFLOW_PARENT_COLUMNS)).filter(
        PublicationTask.id == str(workflow_id),
        PublicationTask.feature == FEATURE_NAME,
    ).one_or_none()
    if not parent:
        raise ValueError("文案到成片任务不存在")
    return parent


def _workflow_params(parent: PublicationTask) -> dict[str, Any]:
    params = parent.params if isinstance(parent.params, dict) else {}
    value = params.get(FEATURE_NAME)
    if not isinstance(value, dict):
        raise ValueError("文案到成片任务参数不完整")
    return value


def _child_audio_payload(child: PublicationTask) -> dict[str, Any]:
    result = child.result if isinstance(child.result, dict) else {}
    audio = result.get("audio") if isinstance(result.get("audio"), dict) else {}
    return dict(audio)


def _has_usable_wav(child: PublicationTask) -> bool:
    if child.status != "completed":
        return False
    wav_path = str(_child_audio_payload(child).get("wav_path") or "").strip()
    return bool(wav_path and Path(wav_path).is_file())


def _audio_payload(parent: PublicationTask) -> dict[str, Any]:
    result = parent.result if isinstance(parent.result, dict) else {}
    audio = result.get("audio") if isinstance(result.get("audio"), dict) else {}
    return dict(audio)


def _audio_is_ready(parent: PublicationTask) -> bool:
    """Only a user-confirmed final WAV may be used for planning or alignment."""

    audio = _audio_payload(parent)
    state = str(audio.get("state") or "")
    if state and state != "finalized":
        return False
    path = Path(str(audio.get("wav_path") or ""))
    return bool(path.is_file() and path.stat().st_size > 0)


def _mark_audio_waiting(
    parent: PublicationTask,
    children: list[PublicationTask],
    waiting_child: PublicationTask,
) -> None:
    result = dict(parent.result or {})
    audio = dict(result.get("audio") or {})
    completed = sum(1 for child in children if _has_usable_wav(child))
    audio.update({
        "chunks_total": len(children),
        "chunks_completed": completed,
        "child_task_ids": [child.id for child in children],
    })
    result["audio"] = audio
    result["stage"] = "audio_processing"
    _append_log(result, "audio_processing", f"等待豆包浏览器朗读第 {waiting_child.doubao_tts_chunk_index}/{len(children)} 段", 10 + int(30 * completed / max(1, len(children))))
    parent.status = "processing"
    parent.current_step = "audio_processing"
    parent.progress = 10 + int(30 * completed / max(1, len(children)))
    parent.error_message = None
    parent.waiting_child_label = "doubao_audio"
    parent.waiting_child_id = waiting_child.id
    parent.waiting_child_status = waiting_child.status
    parent.waiting_child_step = waiting_child.current_step
    parent.waiting_child_updated_at = datetime.utcnow().isoformat() + "Z"
    parent.result = result
    parent.sync_projection_fields()
    flag_modified(parent, "result")
    db.session.commit()


def _mark_parent_blocked(parent: PublicationTask, child: PublicationTask, message: str) -> None:
    result = dict(parent.result or {})
    result["stage"] = "blocked_by_captcha"
    result["blocked_child"] = {
        "id": child.id,
        "status": child.status,
        "step": child.current_step,
        "message": child.error_message,
    }
    _append_log(result, "blocked_by_captcha", message, int(parent.progress or 0))
    parent.status = "blocked_by_captcha"
    parent.current_step = "blocked_by_captcha"
    parent.error_message = message
    parent.waiting_child_label = "doubao_audio"
    parent.waiting_child_id = child.id
    parent.waiting_child_status = child.status
    parent.waiting_child_step = child.current_step
    parent.waiting_child_updated_at = datetime.utcnow().isoformat() + "Z"
    parent.result = result
    parent.sync_projection_fields()
    flag_modified(parent, "result")
    db.session.commit()


def _mark_parent_failed(workflow_id: str, step: str, message: str) -> None:
    parent = PublicationTask.query.filter(
        PublicationTask.id == str(workflow_id),
        PublicationTask.feature == FEATURE_NAME,
    ).one_or_none()
    if not parent or parent.status == "canceled":
        return
    result = dict(parent.result or {})
    result["stage"] = step
    result["error"] = str(message or "任务失败")
    _append_log(result, step, str(message or "任务失败"), int(parent.progress or 0))
    parent.status = "failed"
    parent.current_step = step
    parent.error_message = str(message or "任务失败")
    parent.completed_at = datetime.utcnow()
    parent.result = result
    parent.sync_projection_fields()
    flag_modified(parent, "result")
    db.session.commit()


def _append_log(result: dict[str, Any], step: str, message: str, progress: int) -> None:
    logs = list(result.get("logs") or [])
    logs.append({
        "step": step,
        "message": message,
        "progress": int(progress or 0),
        "updated_at": datetime.utcnow().isoformat() + "Z",
    })
    result["logs"] = logs[-80:]


def _doubao_queue_name() -> str:
    return str(os.getenv("DOUBAO_BROWSER_TTS_QUEUE") or DOUBAO_TTS_QUEUE_NAME)


@celery_app.task(
    bind=True,
    name="tasks.script_video_workflow.generate_storyboard",
    store_errors_even_if_ignored=True,
)
def generate_storyboard(self, workflow_id: str):
    """Ask the selected storyboard Gem for the editable scene/prompt table."""

    app = create_app()
    with app.app_context():
        try:
            return _generate_storyboard(
                str(workflow_id),
                celery_request_id=str(getattr(getattr(self, "request", None), "id", "") or ""),
            )
        except GeminiBrowserHumanInterventionError as exc:
            _mark_storyboard_blocked(str(workflow_id), str(exc))
            return False
        except Exception as exc:
            LOGGER.exception("script-to-video storyboard stage failed: %s", workflow_id)
            _mark_parent_failed(str(workflow_id), "storyboard_failed", str(exc))
            return False
        finally:
            db.session.remove()


def _generate_storyboard(
    workflow_id: str,
    *,
    celery_request_id: str = "",
) -> dict[str, Any] | bool:
    """Generate and validate a Gem-owned storyboard before any image work.

    The generic workbench deliberately shares the same response contract as the
    established stick-figure and Doraemon workspaces: Gemini owns the scene
    semantics and visual prompts, while the authoritative source script is
    retained to reject omissions, reordering, or rewritten narration.
    """

    parent = _workflow_or_raise(workflow_id)
    if parent.status == "canceled" or parent.current_step == VIDEO_STEP:
        return False
    current_celery_id = str(parent.celery_task_id or "")
    if celery_request_id and current_celery_id and celery_request_id != current_celery_id:
        LOGGER.info(
            "Ignore stale script-to-video storyboard delivery workflow=%s request=%s current=%s",
            workflow_id,
            celery_request_id,
            current_celery_id,
        )
        return False

    workflow = _workflow_params(parent)
    source_text = normalize_script(workflow.get("source_text"))
    scheme = visual_scheme(_visual_scheme_key(parent))
    material = {
        "title": str(parent.task_title or source_text[:120] or "文案到成片").strip(),
        "final_script_for_tts": source_text,
    }
    prompt = storyboard_prompt(material)
    output_dir = workflow_output_root(parent.id) / "storyboard"
    output_dir.mkdir(parents=True, exist_ok=True)
    gemini_path = output_dir / "gemini_storyboard.json"
    normalized_path = output_dir / "storyboard_segments.json"
    embedded_image_dir = output_dir / "gemini_embedded_images"

    _update_storyboard_progress(parent.id, {
        "step": "prepare_storyboard",
        "message": "准备调用 Gemini 规划分镜与图片提示词",
        "progress": 4,
    })
    end_db_transaction_before_external_io("Gemini script-to-video storyboard planning")

    gemini_result = _storyboard_gemini_worker().generate(
        job_id=parent.id,
        prompt=prompt,
        output_path=str(gemini_path),
        target_url=scheme.storyboard_gemini_url,
        followup_prompts=[],
        embedded_image_output_dir=str(embedded_image_dir),
        recover_image_only_storyboard=True,
        response_complete_callback=lambda text: storyboard_response_covers_script(material, text),
        incomplete_response_followup_prompt=STORYBOARD_INCOMPLETE_FOLLOWUP_PROMPT,
        human_intervention_details=_storyboard_human_intervention_details(parent.id),
        progress_callback=lambda event: _update_storyboard_progress(parent.id, event),
    )
    raw_text = str(
        (gemini_result or {}).get("final_response_text")
        or (gemini_result or {}).get("response_text")
        or ""
    ).strip()
    if not raw_text:
        raise ValueError("Gemini 未返回可用的分镜规划文本")

    _update_storyboard_progress(parent.id, {
        "step": "normalize_storyboard",
        "message": "校验 Gemini 分镜是否完整覆盖原文",
        "progress": 78,
    })
    normalized = normalize_storyboard_response(material, raw_text)
    raw_segments = normalized.get("segments") if isinstance(normalized, dict) else None
    if not isinstance(raw_segments, list) or not raw_segments:
        raise ValueError("Gemini 未返回有效的正文分镜")
    candidates = [
        {
            "sequence": index,
            "script_text": item.get("script_text") if isinstance(item, dict) else "",
            "image_prompt": item.get("visual_prompt") if isinstance(item, dict) else "",
        }
        for index, item in enumerate(raw_segments, start=1)
    ]
    scenes = validate_scene_rows(candidates, source_text, require_prompts=True)
    normalized_path.write_text(
        json.dumps(normalized, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    fresh = _workflow_or_raise(parent.id)
    if fresh.status == "canceled" or fresh.current_step == VIDEO_STEP:
        return False
    if celery_request_id and fresh.celery_task_id and str(fresh.celery_task_id) != celery_request_id:
        LOGGER.info("Ignore stale completed storyboard workflow=%s", parent.id)
        return False

    _invalidate_images_after_storyboard_refresh(fresh)
    params = dict(fresh.params or {})
    updated_workflow = dict(_workflow_params(fresh))
    updated_workflow["scenes"] = scenes
    params[FEATURE_NAME] = updated_workflow
    result = dict(fresh.result or {})
    title_scene = normalized.get("title_scene") if isinstance(normalized.get("title_scene"), dict) else None
    result["storyboard"] = {
        "status": "completed",
        "segment_count": len(scenes),
        "source_script_hash": normalized.get("source_script_hash") or text_hash(source_text),
        "source_script_chars": normalized.get("source_script_chars") or len(source_text),
        "covered_script_chars": normalized.get("covered_script_chars") or len(source_text),
        "provider": normalized.get("provider") or "gemini_browser",
        "model": normalized.get("model"),
        "gemini_output_path": str(gemini_path),
        "normalized_output_path": str(normalized_path),
        "title_scene": title_scene,
    }
    result["images"] = {
        "total": len(scenes),
        "completed": 0,
        "active": 0,
        "failed": 0,
        "blocked": 0,
        "items": [],
    }
    result["stage"] = "storyboard_ready"
    _append_log(result, "storyboard_ready", f"Gemini 已规划 {len(scenes)} 个正文分镜，请确认后投递图片", 45)
    fresh.params = params
    fresh.result = result
    fresh.status = "processing" if _audio_is_ready(fresh) else "pending"
    fresh.current_step = "storyboard_ready"
    fresh.progress = max(int(fresh.progress or 0), 45)
    fresh.error_message = None
    fresh.completed_at = None
    fresh.waiting_child_label = None
    fresh.waiting_child_id = None
    fresh.waiting_child_status = None
    fresh.waiting_child_step = None
    fresh.waiting_child_updated_at = None
    fresh.sync_projection_fields()
    flag_modified(fresh, "params")
    flag_modified(fresh, "result")
    db.session.commit()
    return dict(result["storyboard"])


def _storyboard_gemini_worker() -> GeminiBrowserWorker:
    """Match the durable long-form Gem settings used by existing visual schemes."""

    options = GeminiBrowserOptions.from_env()
    pool_profile = "report_visual_plan_gemini_pool" in str(options.user_data_dir or "")
    raw_recover = str(os.getenv("SCRIPT_VIDEO_STORYBOARD_AUTO_RECOVER_ORPHANED_CHROME") or "").strip().lower()
    options.auto_recover_orphaned_chrome = (
        pool_profile if not raw_recover else raw_recover not in {"0", "false", "no", "off"}
    )
    response_floor = max(1800, int(options.response_timeout_seconds or 0))
    total_floor = max(7200, response_floor * 3 + 300)
    options.response_timeout_seconds = _positive_env_int(
        "SCRIPT_VIDEO_STORYBOARD_GEMINI_RESPONSE_TIMEOUT",
        response_floor,
    )
    options.total_timeout_seconds = _positive_env_int(
        "SCRIPT_VIDEO_STORYBOARD_GEMINI_TOTAL_TIMEOUT",
        max(total_floor, options.response_timeout_seconds * 3 + 300),
    )
    return GeminiBrowserWorker(options)


def _positive_env_int(name: str, default: int) -> int:
    raw = str(os.getenv(name) or "").strip()
    if not raw:
        return int(default)
    try:
        return max(1, int(raw))
    except ValueError:
        return int(default)


def _storyboard_human_intervention_details(workflow_id: str) -> dict[str, str]:
    base_url = str(
        os.getenv("SCRIPT_VIDEO_WORKFLOW_FRONTEND_BASE_URL") or "http://localhost:8888"
    ).strip().rstrip("/")
    return {
        "工作流详情": (
            f"{base_url}/script-video-workflow.html?job_id={workflow_id}"
        ),
    }


def _update_storyboard_progress(workflow_id: str, event: dict[str, Any]) -> None:
    parent = PublicationTask.query.filter(
        PublicationTask.id == str(workflow_id),
        PublicationTask.feature == FEATURE_NAME,
        PublicationTask.status != "canceled",
    ).one_or_none()
    if not parent or parent.current_step not in STORYBOARD_ACTIVE_STEPS:
        return
    event_progress = max(0, min(99, int(event.get("progress") or 0)))
    workflow_progress = min(44, 8 + int(event_progress * 0.36))
    result = dict(parent.result or {})
    storyboard = dict(result.get("storyboard") or {})
    storyboard.update({
        "status": "processing",
        "last_step": str(event.get("step") or "storyboard_processing")[:80],
        "last_message": str(event.get("message") or "Gemini 正在规划分镜")[:500],
    })
    result["storyboard"] = storyboard
    result["stage"] = "storyboard_processing"
    _append_log(
        result,
        str(event.get("step") or "storyboard_processing"),
        str(event.get("message") or "Gemini 正在规划分镜"),
        max(int(parent.progress or 0), workflow_progress),
    )
    parent.status = "processing"
    parent.current_step = "storyboard_processing"
    parent.progress = max(int(parent.progress or 0), workflow_progress)
    parent.error_message = None
    parent.completed_at = None
    parent.waiting_child_label = "gemini_storyboard"
    parent.waiting_child_id = parent.id
    parent.waiting_child_status = "processing"
    parent.waiting_child_step = str(event.get("step") or "storyboard_processing")[:80]
    parent.waiting_child_updated_at = datetime.utcnow().isoformat() + "Z"
    parent.result = result
    parent.sync_projection_fields()
    flag_modified(parent, "result")
    db.session.commit()


def _mark_storyboard_blocked(workflow_id: str, message: str) -> None:
    parent = PublicationTask.query.filter(
        PublicationTask.id == str(workflow_id),
        PublicationTask.feature == FEATURE_NAME,
    ).one_or_none()
    if not parent or parent.status == "canceled":
        return
    text = str(message or "Gemini 浏览器需要人工登录或验证")
    status = "blocked_by_captcha" if "captcha" in text.lower() or "验证" in text else "blocked_by_login"
    result = dict(parent.result or {})
    storyboard = dict(result.get("storyboard") or {})
    storyboard.update({"status": status, "error": text})
    result["storyboard"] = storyboard
    result["stage"] = status
    _append_log(result, status, text, int(parent.progress or 0))
    parent.status = status
    parent.current_step = status
    parent.error_message = text[:4000]
    parent.completed_at = datetime.utcnow()
    parent.waiting_child_label = "gemini_storyboard"
    parent.waiting_child_id = parent.id
    parent.waiting_child_status = status
    parent.waiting_child_step = status
    parent.waiting_child_updated_at = datetime.utcnow().isoformat() + "Z"
    parent.result = result
    parent.sync_projection_fields()
    flag_modified(parent, "result")
    db.session.commit()


def _invalidate_images_after_storyboard_refresh(parent: PublicationTask) -> None:
    """A fresh Gemini plan supersedes every previous scene-image artifact."""

    for child in _image_children_for_parent(parent.id):
        if child.status == "canceled":
            continue
        result = dict(child.result or {})
        result["invalidated_at"] = datetime.utcnow().isoformat() + "Z"
        result["invalidated_reason"] = "Gemini 分镜规划已重新生成"
        child.status = "canceled"
        child.current_step = "storyboard_replanned"
        child.error_message = "Gemini 分镜规划已重新生成，请按新分镜重新投递图片"
        child.completed_at = datetime.utcnow()
        child.result = result
        flag_modified(child, "result")


@celery_app.task(
    bind=True,
    name="tasks.script_video_workflow.generate_image",
    store_errors_even_if_ignored=True,
)
def generate_image(self, image_task_id: str):
    app = create_app()
    with app.app_context():
        try:
            return _generate_image(
                str(image_task_id),
                celery_request_id=str(getattr(getattr(self, "request", None), "id", "") or ""),
            )
        except GeminiBrowserHumanInterventionError as exc:
            _mark_image_blocked(str(image_task_id), str(exc))
            return False
        except Exception as exc:
            LOGGER.exception("script-to-video image stage failed: %s", image_task_id)
            _mark_image_failed(str(image_task_id), str(exc))
            return False
        finally:
            db.session.remove()


def enqueue_image_stage(
    parent: PublicationTask,
    *,
    sequences: Iterable[int] | None = None,
) -> list[PublicationTask]:
    """Persist and enqueue the selected editable scenes for Gemini generation."""

    parent = _workflow_or_raise(parent.id)
    scenes = _current_scenes(parent, require_prompts=True)
    requested = {int(value) for value in sequences or []}
    if requested:
        scenes = [scene for scene in scenes if scene["sequence"] in requested]
        if not scenes:
            raise ValueError("没有匹配的分镜图片可重试")
    children = _create_or_reuse_image_children(parent, scenes, retry_failed=True)
    _refresh_parent_image_state(parent.id)
    return children


def _create_or_reuse_image_children(
    parent: PublicationTask,
    scenes: Iterable[dict[str, Any]],
    *,
    retry_failed: bool = False,
) -> list[PublicationTask]:
    """Create only missing/explicitly retried Gemini children for current scenes."""

    scheme_key = _visual_scheme_key(parent)
    existing_children = _image_children_for_parent(parent.id)
    child_rows: list[PublicationTask] = []
    new_children: list[PublicationTask] = []
    for scene in scenes:
        sequence = int(scene["sequence"])
        expected_hash = _scene_content_hash(scene, scheme_key)
        matches = [
            child for child in existing_children
            if _image_sequence(child) == sequence
            and _image_scene_hash(child) == expected_hash
        ]
        completed = next((item for item in matches if _has_usable_image(item)), None)
        if completed:
            child_rows.append(completed)
            continue
        active = next((item for item in matches if item.status in IMAGE_ACTIVE_STATUSES), None)
        if active:
            child_rows.append(active)
            continue
        if matches and not retry_failed:
            child_rows.append(matches[0])
            continue
        attempt = 1 + max([_image_attempt(item) for item in matches] or [0])
        child = _new_image_child(parent, scene, expected_hash, attempt)
        child_rows.append(child)
        new_children.append(child)

    if not new_children:
        return child_rows

    db.session.add_all(new_children)
    session = db.session()
    previous_expire_on_commit = session.expire_on_commit
    session.expire_on_commit = False
    try:
        session.commit()
    finally:
        session.expire_on_commit = previous_expire_on_commit

    publish_failures: list[tuple[PublicationTask, Exception]] = []
    for child in new_children:
        try:
            apply_async_after_db_release(
                generate_image,
                args=[child.id],
                queue="report_pdf_gemini",
                task_id=child.celery_task_id,
                retry=False,
            )
        except Exception as exc:
            publish_failures.append((child, exc))

    if publish_failures:
        for child, exc in publish_failures:
            child.status = "failed"
            child.current_step = "queue_failed"
            child.error_message = f"Gemini 图片任务入队失败: {exc}"
            child.completed_at = datetime.utcnow()
        db.session.commit()
    return child_rows


def _new_image_child(
    parent: PublicationTask,
    scene: dict[str, Any],
    scene_hash: str,
    attempt: int,
) -> PublicationTask:
    sequence = int(scene["sequence"])
    metadata = {
        "source": FEATURE_NAME,
        "parent_task_id": parent.id,
        "sequence": sequence,
        "attempt": attempt,
        "scene_hash": scene_hash,
        "visual_scheme_key": _visual_scheme_key(parent),
    }
    child = PublicationTask(
        id=str(uuid.uuid4()),
        celery_task_id=str(uuid.uuid4()),
        status="pending",
        current_step="queued",
        progress=0,
        params={
            "feature": IMAGE_FEATURE_NAME,
            "user_id": parent.user_id,
            "parent_feature": FEATURE_NAME,
            "parent_task_id": parent.id,
            IMAGE_FEATURE_NAME: {
                "sequence": sequence,
                "script_text": scene["script_text"],
                "image_prompt": scene["image_prompt"],
                "scene_hash": scene_hash,
                "attempt": attempt,
                "visual_scheme_key": _visual_scheme_key(parent),
            },
        },
        result={"metadata": metadata, "image": None, "logs": []},
    )
    return child


def _generate_image(image_task_id: str, *, celery_request_id: str = "") -> bool:
    child = _image_child_or_raise(image_task_id)
    if child.status in {"canceled", *IMAGE_BLOCKED_STATUSES}:
        return False
    current_celery_id = str(child.celery_task_id or "")
    if celery_request_id and current_celery_id and celery_request_id != current_celery_id:
        LOGGER.info(
            "Ignore stale script-to-video image delivery child=%s request=%s current=%s",
            image_task_id,
            celery_request_id,
            current_celery_id,
        )
        return False
    if _has_usable_image(child):
        _refresh_parent_image_state(str(child.parent_task_id))
        return True

    parent = _workflow_or_raise(str(child.parent_task_id))
    scene = _scene_for_image_child(parent, child)
    scheme_key = _visual_scheme_key(parent)
    scene_hash = _scene_content_hash(scene, scheme_key)
    prompt = compose_scene_image_prompt(scene, scheme_key)
    output_path = Path(scene_image_output_path(
        parent.id,
        scene["sequence"],
        artifact_key=scene_hash,
    )).resolve()
    scheme = visual_scheme(scheme_key, require_configured=False)

    result = dict(child.result or {})
    _append_log(result, "prepare_browser", "准备通过 Gemini 浏览器生成分镜图片", 3)
    child.status = "processing"
    child.current_step = "prepare_browser"
    child.progress = 3
    child.error_message = None
    child.completed_at = None
    child.result = result
    flag_modified(child, "result")
    db.session.commit()
    end_db_transaction_before_external_io("Gemini script-to-video image generation")

    def progress_callback(event: dict[str, Any]) -> None:
        _update_image_progress(child.id, event)

    image = GeminiBrowserWorker().generate_image(
        job_id=child.id,
        prompt=prompt,
        image_output_path=str(output_path),
        target_url=scheme.image_gemini_url,
        progress_callback=progress_callback,
    )
    reported_path = Path(str((image or {}).get("path") or output_path)).resolve()
    if reported_path != output_path:
        raise ValueError("Gemini 图片返回了非当前工作流的文件路径")
    if not output_path.is_file() or output_path.stat().st_size <= 0:
        raise ValueError("Gemini 图片生成后没有找到有效文件")

    fresh = _image_child_or_raise(child.id)
    if fresh.status == "canceled" or _image_scene_hash(fresh) != scene_hash:
        # A user edited this scene while Gemini was working. The image is saved
        # under the old content hash and must never become the current asset.
        return False
    final_result = dict(fresh.result or {})
    image_payload = dict(image or {})
    image_payload.update({
        "path": str(output_path),
        "bytes": int(output_path.stat().st_size),
        "sequence": scene["sequence"],
        "scene_hash": scene_hash,
        "prompt_hash": text_hash(prompt),
    })
    final_result["image"] = image_payload
    _append_log(final_result, "image_ready", "Gemini 图片已保存", 100)
    fresh.status = "completed"
    fresh.current_step = "completed"
    fresh.progress = 100
    fresh.error_message = None
    fresh.completed_at = datetime.utcnow()
    fresh.result = final_result
    flag_modified(fresh, "result")
    db.session.commit()
    _refresh_parent_image_state(parent.id)
    return True


def _update_image_progress(image_task_id: str, event: dict[str, Any]) -> None:
    child = _image_child_or_raise(image_task_id)
    if child.status in {"canceled", *IMAGE_BLOCKED_STATUSES, "completed"}:
        return
    result = dict(child.result or {})
    _append_log(
        result,
        str(event.get("step") or "processing"),
        str(event.get("message") or "Gemini 图片处理中"),
        int(event.get("progress") or 0),
    )
    child.current_step = str(event.get("step") or "processing")[:80]
    child.progress = max(0, min(99, int(event.get("progress") or 0)))
    child.result = result
    flag_modified(child, "result")
    db.session.commit()


def _mark_image_failed(image_task_id: str, message: str) -> None:
    child = PublicationTask.query.filter(
        PublicationTask.id == str(image_task_id),
        PublicationTask.feature == IMAGE_FEATURE_NAME,
    ).one_or_none()
    if not child or child.status == "canceled":
        return
    result = dict(child.result or {})
    result["error"] = str(message or "Gemini 图片生成失败")
    _append_log(result, "image_failed", str(message or "Gemini 图片生成失败"), int(child.progress or 0))
    child.status = "failed"
    child.current_step = "image_failed"
    child.error_message = str(message or "Gemini 图片生成失败")[:4000]
    child.completed_at = datetime.utcnow()
    child.result = result
    flag_modified(child, "result")
    db.session.commit()
    _refresh_parent_image_state(str(child.parent_task_id))


def _mark_image_blocked(image_task_id: str, message: str) -> None:
    child = PublicationTask.query.filter(
        PublicationTask.id == str(image_task_id),
        PublicationTask.feature == IMAGE_FEATURE_NAME,
    ).one_or_none()
    if not child or child.status == "canceled":
        return
    text = str(message or "Gemini 浏览器需要人工登录或验证")
    status = "blocked_by_captcha" if "captcha" in text.lower() or "验证" in text else "blocked_by_login"
    result = dict(child.result or {})
    result["blocked_reason"] = status
    _append_log(result, status, text, int(child.progress or 0))
    child.status = status
    child.current_step = status
    child.error_message = text[:4000]
    child.completed_at = datetime.utcnow()
    child.result = result
    flag_modified(child, "result")
    db.session.commit()
    _refresh_parent_image_state(str(child.parent_task_id))


def _refresh_parent_image_state(parent_id: str) -> None:
    parent = PublicationTask.query.options(load_only(*WORKFLOW_PARENT_COLUMNS)).filter(
        PublicationTask.id == str(parent_id),
        PublicationTask.feature == FEATURE_NAME,
    ).one_or_none()
    if not parent or parent.status == "canceled" or parent.current_step in {
        "alignment_queued", "alignment_ready", "video_queued", "render_video", VIDEO_STEP,
    }:
        return
    try:
        scenes = _current_scenes(parent, require_prompts=True)
    except ValueError:
        return
    scheme_key = _visual_scheme_key(parent)
    all_children = _image_children_for_parent(parent.id)
    items = []
    completed = failed = blocked = active = 0
    for scene in scenes:
        matches = [
            child for child in all_children
            if _image_sequence(child) == scene["sequence"]
            and _image_scene_hash(child) == _scene_content_hash(scene, scheme_key)
        ]
        child = next((item for item in matches if _has_usable_image(item)), None)
        if child:
            state = "completed"
            completed += 1
        else:
            child = next((item for item in matches if item.status in IMAGE_ACTIVE_STATUSES), None)
            if child:
                state = child.status
                active += 1
            else:
                child = matches[0] if matches else None
                state = str(child.status if child else "missing")
                if state in IMAGE_BLOCKED_STATUSES:
                    blocked += 1
                else:
                    failed += 1
        image = _child_image_payload(child) if child else {}
        items.append({
            "sequence": scene["sequence"],
            "task_id": child.id if child else None,
            "status": state,
            "image_path": image.get("path"),
            "bytes": image.get("bytes"),
            "error": child.error_message if child else "尚未提交图片任务",
        })

    total = len(scenes)
    result = dict(parent.result or {})
    result["images"] = {
        "total": total,
        "completed": completed,
        "active": active,
        "failed": failed,
        "blocked": blocked,
        "items": items,
    }
    parent.waiting_child_label = "gemini_images"
    parent.waiting_child_updated_at = datetime.utcnow().isoformat() + "Z"
    if completed == total:
        result["stage"] = "images_ready"
        _append_log(result, "images_ready", f"Gemini 图片已全部完成：{completed}/{total}", 70)
        parent.status = "processing"
        parent.current_step = "images_ready"
        parent.progress = max(int(parent.progress or 0), 70)
        parent.error_message = None
        parent.completed_at = None
        parent.waiting_child_id = None
        parent.waiting_child_status = "completed"
        parent.waiting_child_step = "completed"
    elif blocked:
        result["stage"] = "blocked_by_login"
        _append_log(result, "blocked_by_login", "Gemini 图片生成需要人工登录或验证", int(parent.progress or 0))
        parent.status = "blocked_by_login"
        parent.current_step = "blocked_by_login"
        parent.error_message = "Gemini 图片生成需要人工登录或验证"
        first = next((item for item in items if item["status"] in IMAGE_BLOCKED_STATUSES), None)
        parent.waiting_child_id = first.get("task_id") if first else None
        parent.waiting_child_status = "blocked"
        parent.waiting_child_step = "blocked_by_login"
    elif failed:
        result["stage"] = "images_failed"
        _append_log(result, "images_failed", f"图片生成完成：{completed}/{total} 成功，{failed}/{total} 失败", 45 + int(25 * completed / max(1, total)))
        parent.status = "failed"
        parent.current_step = "images_failed"
        parent.progress = max(int(parent.progress or 0), 45 + int(25 * completed / max(1, total)))
        parent.error_message = "存在失败图片，可逐张重试"
        parent.completed_at = datetime.utcnow()
        first = next((item for item in items if item["status"] not in {"completed", *IMAGE_ACTIVE_STATUSES}), None)
        parent.waiting_child_id = first.get("task_id") if first else None
        parent.waiting_child_status = "failed"
        parent.waiting_child_step = "image_failed"
    else:
        result["stage"] = "images_processing"
        _append_log(result, "images_processing", f"正在生成 Gemini 图片：{completed}/{total}", 45 + int(25 * completed / max(1, total)))
        parent.status = "processing"
        parent.current_step = "images_processing"
        parent.progress = max(int(parent.progress or 0), 45 + int(25 * completed / max(1, total)))
        parent.error_message = None
        parent.completed_at = None
        first = next((item for item in items if item["status"] in IMAGE_ACTIVE_STATUSES), None)
        parent.waiting_child_id = first.get("task_id") if first else None
        parent.waiting_child_status = "processing"
        parent.waiting_child_step = "image_processing"
    parent.result = result
    parent.sync_projection_fields()
    flag_modified(parent, "result")
    db.session.commit()


@celery_app.task(
    bind=True,
    name="tasks.script_video_workflow.render_video",
    store_errors_even_if_ignored=True,
)
def render_video(self, workflow_id: str):
    app = create_app()
    with app.app_context():
        try:
            return _render_video(str(workflow_id))
        except Exception as exc:
            LOGGER.exception("script-to-video render stage failed: %s", workflow_id)
            _mark_parent_failed(str(workflow_id), "render_failed", str(exc))
            return False
        finally:
            db.session.remove()


def _render_video(workflow_id: str) -> bool:
    parent = _workflow_or_raise(workflow_id)
    if parent.status == "completed" and _video_payload(parent).get("video_path"):
        return True
    workflow = _workflow_params(parent)
    source_text = normalize_script(workflow.get("source_text"))
    scenes = _current_scenes(parent, require_prompts=True)
    audio = _audio_payload(parent)
    audio_path = Path(str(audio.get("wav_path") or ""))
    if not audio_path.is_file() or audio_path.stat().st_size <= 0:
        raise ValueError("完整 WAV 文件不存在，不能开始音画对齐")
    try:
        audio_seconds = float(audio.get("seconds") or 0)
    except (TypeError, ValueError):
        audio_seconds = 0.0
    if audio_seconds <= 0:
        raise ValueError("完整 WAV 时长异常，不能开始音画对齐")

    render_scenes = _render_scenes_with_images(parent, scenes)
    _mark_render_progress(parent, "alignment_queued", 74, "所有图片已就绪，开始整篇音频对齐")
    end_db_transaction_before_external_io("script-to-video ASR alignment")
    asr = transcribe_with_cache(
        str(audio_path),
        model_size="base",
        initial_prompt=source_text,
        include_metadata=True,
    )
    asr_payload = asr if isinstance(asr, dict) else {"segments": asr, "metadata": {}}
    segments = asr_payload.get("segments") if isinstance(asr_payload.get("segments"), list) else []
    alignment = align_storyboard_to_audio(
        render_scenes,
        segments,
        audio_seconds=audio_seconds,
        source_script=source_text,
    )
    timeline = alignment.get("timeline") if isinstance(alignment, dict) else None
    if not isinstance(timeline, list) or len(timeline) != len(render_scenes):
        raise ValueError("音频对齐没有返回完整连续的分镜时间轴")
    timeline_by_sequence = {int(item.get("sequence")): item for item in timeline}
    for scene in render_scenes:
        timed = timeline_by_sequence.get(int(scene["sequence"]))
        if not timed:
            raise ValueError(f"第 {scene['sequence']} 段缺少音频对齐时间轴")
        scene.update({
            "start_seconds": timed.get("start_seconds"),
            "end_seconds": timed.get("end_seconds"),
            "alignment_confidence": timed.get("alignment_confidence"),
        })

    _mark_render_progress(parent, "render_video", 82, "音频对齐完成，开始合成 MP4")
    end_db_transaction_before_external_io("script-to-video FFmpeg render")
    video = render_scene_timeline(
        parent.id,
        render_scenes,
        audio_mode="whole_audio",
        whole_audio_path=str(audio_path),
        render_options=_render_options(workflow),
        progress_callback=lambda event: _update_render_progress(parent.id, event),
        output_root=workflow_output_root(parent.id),
    )
    video = dict(video or {})
    video_path = Path(str(video.get("video_path") or ""))
    if not video_path.is_file() or video_path.stat().st_size <= 0:
        raise ValueError("视频合成没有返回有效 MP4 文件")

    fresh = _workflow_or_raise(parent.id)
    result = dict(fresh.result or {})
    result["alignment"] = {
        **dict(alignment or {}),
        "asr_metadata": dict(asr_payload.get("metadata") or {}),
    }
    result["video"] = video
    result["stage"] = VIDEO_STEP
    _append_log(result, VIDEO_STEP, "音画已对齐并完成 MP4 合成", 100)
    fresh.status = "completed"
    fresh.current_step = VIDEO_STEP
    fresh.progress = 100
    fresh.error_message = None
    fresh.completed_at = datetime.utcnow()
    fresh.task_video_path = str(video_path)
    fresh.waiting_child_label = None
    fresh.waiting_child_id = None
    fresh.waiting_child_status = None
    fresh.waiting_child_step = None
    fresh.waiting_child_updated_at = None
    fresh.result = result
    fresh.sync_projection_fields()
    flag_modified(fresh, "result")
    db.session.commit()
    return True


def _render_scenes_with_images(
    parent: PublicationTask,
    scenes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    scheme_key = _visual_scheme_key(parent)
    rows = []
    for scene in scenes:
        expected_hash = _scene_content_hash(scene, scheme_key)
        child = next(
            (
                item
                for item in _image_children_matching(parent.id, int(scene["sequence"]), expected_hash)
                if _has_usable_image(item)
            ),
            None,
        )
        if not child:
            raise ValueError(f"第 {scene['sequence']} 段图片尚未完成，不能开始音画对齐")
        image = _child_image_payload(child)
        rows.append({
            "asset_id": child.id,
            "sequence": scene["sequence"],
            "script_text": scene["script_text"],
            "image_path": image["path"],
        })
    return rows


def _mark_render_progress(parent: PublicationTask, step: str, progress: int, message: str) -> None:
    fresh = _workflow_or_raise(parent.id)
    result = dict(fresh.result or {})
    result["stage"] = step
    _append_log(result, step, message, progress)
    fresh.status = "processing"
    fresh.current_step = step
    fresh.progress = max(int(fresh.progress or 0), int(progress))
    fresh.error_message = None
    fresh.completed_at = None
    fresh.result = result
    fresh.sync_projection_fields()
    flag_modified(fresh, "result")
    db.session.commit()


def _update_render_progress(workflow_id: str, event: dict[str, Any]) -> None:
    parent = PublicationTask.query.filter(
        PublicationTask.id == str(workflow_id),
        PublicationTask.feature == FEATURE_NAME,
        PublicationTask.status != "canceled",
    ).one_or_none()
    if not parent or parent.current_step not in {"render_video", "alignment_queued", "alignment_ready"}:
        return
    result = dict(parent.result or {})
    step = str(event.get("step") or "render_video")
    progress = max(0, min(99, int(event.get("progress") or 0)))
    _append_log(result, step, str(event.get("message") or "正在合成视频"), progress)
    # Keep the durable stage at ``render_video`` even when the renderer emits
    # fine-grained progress event names.  API and UI edit locks rely on this
    # stable state to prevent a concurrent scene edit from invalidating an
    # in-flight MP4 render.
    parent.current_step = "render_video"
    parent.progress = max(int(parent.progress or 0), progress)
    parent.result = result
    flag_modified(parent, "result")
    db.session.commit()


def _render_options(workflow: dict[str, Any]) -> dict[str, Any]:
    raw = workflow.get("render_options") if isinstance(workflow.get("render_options"), dict) else {}
    return {
        "subtitles_enabled": True,
        "watermark_enabled": False,
        "speed": 1.0,
        **raw,
    }


def _current_scenes(parent: PublicationTask, *, require_prompts: bool) -> list[dict[str, Any]]:
    workflow = _workflow_params(parent)
    return validate_scene_rows(
        workflow.get("scenes") if isinstance(workflow.get("scenes"), list) else [],
        workflow.get("source_text"),
        require_prompts=require_prompts,
    )


def _visual_scheme_key(parent: PublicationTask) -> str:
    params = parent.params if isinstance(parent.params, dict) else {}
    key = str(parent.report_visual_scheme_key or params.get("visual_scheme_key") or "stick_figure").strip()
    visual_scheme(key, require_configured=False)
    return key


def _scene_content_hash(scene: dict[str, Any], scheme_key: str) -> str:
    return text_hash("\0".join((
        str(scheme_key or "").strip(),
        str(scene.get("script_text") or ""),
        str(scene.get("image_prompt") or "").strip(),
    )))


def _image_children_for_parent(parent_id: str) -> list[PublicationTask]:
    return (
        PublicationTask.query.options(load_only(*IMAGE_CHILD_COLUMNS)).filter(
            PublicationTask.feature == IMAGE_FEATURE_NAME,
            PublicationTask.parent_feature == FEATURE_NAME,
            PublicationTask.parent_task_id == str(parent_id),
        )
        .order_by(PublicationTask.created_at.desc())
        .all()
    )


def _image_children_matching(parent_id: str, sequence: int, scene_hash: str) -> list[PublicationTask]:
    return [
        child for child in _image_children_for_parent(parent_id)
        if _image_sequence(child) == int(sequence) and _image_scene_hash(child) == scene_hash
    ]


def _image_child_or_raise(image_task_id: str) -> PublicationTask:
    child = PublicationTask.query.filter(
        PublicationTask.id == str(image_task_id),
        PublicationTask.feature == IMAGE_FEATURE_NAME,
    ).one_or_none()
    if not child:
        raise ValueError("分镜图片任务不存在")
    return child


def _image_params(child: PublicationTask) -> dict[str, Any]:
    params = child.params if isinstance(child.params, dict) else {}
    payload = params.get(IMAGE_FEATURE_NAME)
    return payload if isinstance(payload, dict) else {}


def _image_sequence(child: PublicationTask) -> int:
    try:
        return int(_image_params(child).get("sequence") or 0)
    except (TypeError, ValueError):
        return 0


def _image_scene_hash(child: PublicationTask) -> str:
    return str(_image_params(child).get("scene_hash") or "")


def _image_attempt(child: PublicationTask) -> int:
    try:
        return int(_image_params(child).get("attempt") or 0)
    except (TypeError, ValueError):
        return 0


def _child_image_payload(child: PublicationTask | None) -> dict[str, Any]:
    if not child:
        return {}
    result = child.result if isinstance(child.result, dict) else {}
    image = result.get("image") if isinstance(result.get("image"), dict) else {}
    return dict(image)


def _has_usable_image(child: PublicationTask) -> bool:
    if child.status != "completed":
        return False
    path = str(_child_image_payload(child).get("path") or "").strip()
    return bool(path and Path(path).is_file() and Path(path).stat().st_size > 0)


def _scene_for_image_child(parent: PublicationTask, child: PublicationTask) -> dict[str, Any]:
    sequence = _image_sequence(child)
    scene = next((item for item in _current_scenes(parent, require_prompts=True) if item["sequence"] == sequence), None)
    if not scene:
        raise ValueError("当前分镜已不存在，请重新提交图片")
    if _image_scene_hash(child) != _scene_content_hash(scene, _visual_scheme_key(parent)):
        raise ValueError("分镜文案或图片提示词已变化，请重新提交图片")
    return scene


def _video_payload(parent: PublicationTask) -> dict[str, Any]:
    result = parent.result if isinstance(parent.result, dict) else {}
    video = result.get("video") if isinstance(result.get("video"), dict) else {}
    return dict(video)


# Imported by the later image/render stage. Keeping the name here makes the
# public module contract explicit while the stage is added incrementally.
__all__ = [
    "process_audio",
    "_create_or_reuse_audio_children",
    "_finalize_audio_stage",
    "generate_image",
    "enqueue_image_stage",
    "_create_or_reuse_image_children",
    "_generate_image",
    "render_video",
    "_render_video",
]
