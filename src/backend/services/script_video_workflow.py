"""Pure contracts and filesystem boundaries for the generic script-to-video workflow.

The module deliberately has no Flask, database, Celery, browser, ASR, or
FFmpeg dependency.  Those slow/durable concerns belong to the task and API
layers; this file keeps the user-editable text and artifact contracts stable.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

from services.report_audio_workflow import split_tts_text
from services.report_visual_scheme import scene_image_prompt_for_scheme


FEATURE_NAME = "script_video_workflow"
IMAGE_FEATURE_NAME = "script_video_image"
AUDIO_STEP = "audio_ready"
VIDEO_STEP = "completed"

DEFAULT_AUDIO_CHUNK_CHARS = 800
DEFAULT_SCENE_CHARS = 160
MIN_AUDIO_CHUNK_CHARS = 200
MAX_AUDIO_CHUNK_CHARS = 5000
MAX_SCENE_CHARS = 2000

AUDIO_UPLOAD_EXTENSIONS = frozenset({".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus"})
DEFAULT_AUDIO_UPLOAD_MAX_MB = 512
MIN_AUDIO_EDIT_SECONDS = 0.1
MIN_AUDIO_EDIT_SPEED = 0.5
MAX_AUDIO_EDIT_SPEED = 2.0

_WORKFLOW_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_WHITESPACE_RE = re.compile(r"\s+")
_SENTENCE_RE = re.compile(r".*?[。！？!?；;](?:\s+|$)|.+$")


def normalize_script(text: Any) -> str:
    """Normalize user-visible whitespace while retaining all spoken content."""

    normalized = _WHITESPACE_RE.sub(" ", str(text or "")).strip()
    if not normalized:
        raise ValueError("文案不能为空")
    return normalized


def split_audio_chunks(text: str, max_chars: int = DEFAULT_AUDIO_CHUNK_CHARS) -> list[str]:
    """Create sentence-first chunks suitable for the existing Doubao worker."""

    bounded = _bounded_int(
        max_chars,
        default=DEFAULT_AUDIO_CHUNK_CHARS,
        minimum=MIN_AUDIO_CHUNK_CHARS,
        maximum=MAX_AUDIO_CHUNK_CHARS,
    )
    return split_tts_text(normalize_script(text), max_chars=bounded)


def audio_chunks_cover_source(chunks: Sequence[Any], source_text: str) -> bool:
    """Verify audio chunks without treating splitter-only line breaks as edits.

    The established sentence-first TTS splitter adds a newline when several
    short sentences share one browser submission.  That whitespace improves
    readability in Doubao but is not a loss or addition of spoken text.
    """

    if not isinstance(chunks, Sequence) or isinstance(chunks, (str, bytes)):
        return False
    values = [str(item or "") for item in chunks]
    if not values or any(not item.strip() for item in values):
        return False
    return _coverage_text("".join(values)) == _coverage_text(source_text)


def build_initial_scenes(text: str, max_chars: int = DEFAULT_SCENE_CHARS) -> list[dict[str, Any]]:
    """Split a script into short, editable visual-scene rows.

    Scene splitting preserves the normalized source string exactly when rows
    are joined in order.  This is important because a user may later split or
    merge scene rows, and the API must prove that none of the narration was
    dropped or duplicated before it queues Gemini work.
    """

    source = normalize_script(text)
    bounded = _bounded_int(
        max_chars,
        default=DEFAULT_SCENE_CHARS,
        minimum=1,
        maximum=MAX_SCENE_CHARS,
    )
    sentences = [match.group(0) for match in _SENTENCE_RE.finditer(source) if match.group(0)]
    if not sentences:
        sentences = [source]

    rows: list[dict[str, Any]] = []
    current = ""
    for sentence in sentences:
        if len(sentence) > bounded:
            if current:
                rows.append(_scene_row(len(rows) + 1, current))
                current = ""
            for index in range(0, len(sentence), bounded):
                rows.append(_scene_row(len(rows) + 1, sentence[index:index + bounded]))
            continue

        if current and len(current) + len(sentence) > bounded:
            rows.append(_scene_row(len(rows) + 1, current))
            current = sentence
        else:
            current += sentence

    if current:
        rows.append(_scene_row(len(rows) + 1, current))
    return validate_scene_rows(rows, source, require_prompts=False)


def validate_scene_rows(
    rows: Sequence[Mapping[str, Any]],
    source_text: str,
    *,
    require_prompts: bool,
) -> list[dict[str, Any]]:
    """Return canonical scene rows or reject an unsafe image-generation plan."""

    source = normalize_script(source_text)
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)) or not rows:
        raise ValueError("请至少保留一个分镜")

    normalized_rows: list[dict[str, Any]] = []
    expected_sequence = 1
    for position, raw_row in enumerate(rows, start=1):
        if not isinstance(raw_row, Mapping):
            raise ValueError(f"第 {position} 行分镜格式不正确")
        sequence = _positive_int(raw_row.get("sequence"))
        if sequence != expected_sequence:
            raise ValueError("分镜序号必须从 1 开始连续递增")

        script_text = str(raw_row.get("script_text") or "")
        if not script_text.strip():
            raise ValueError(f"第 {sequence} 行分镜文案不能为空")
        image_prompt = str(raw_row.get("image_prompt") or "").strip()
        if require_prompts and not image_prompt:
            raise ValueError(f"第 {sequence} 行图片提示词不能为空")

        normalized_rows.append({
            "sequence": sequence,
            "script_text": script_text,
            "image_prompt": image_prompt,
        })
        expected_sequence += 1

    covered = normalize_script("".join(row["script_text"] for row in normalized_rows))
    if covered != source:
        raise ValueError("分镜文案未完整覆盖原文，不能生成图片")
    return normalized_rows


def compose_scene_image_prompt(scene: Mapping[str, Any], scheme_key: str) -> str:
    """Compose a Gemini request through the centrally versioned scheme contract."""

    if not isinstance(scene, Mapping):
        raise ValueError("分镜格式不正确")
    return scene_image_prompt_for_scheme(
        str(scene.get("image_prompt") or "").strip(),
        str(scene.get("script_text") or "").strip(),
        scheme_key=str(scheme_key or "").strip(),
    )


def text_hash(value: Any) -> str:
    """Stable content hash for safely reusing completed child artifacts."""

    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def workflow_output_root(workflow_id: str) -> Path:
    """Return (and create) the only output directory owned by one workflow."""

    safe_id = _safe_workflow_id(workflow_id)
    configured_root = str(os.getenv("SCRIPT_VIDEO_WORKFLOW_OUTPUT_ROOT") or "").strip()
    base = (
        Path(configured_root).expanduser()
        if configured_root
        else _project_root() / "data" / "processed" / FEATURE_NAME
    ).resolve()
    root = (base / safe_id).resolve()
    try:
        root.relative_to(base)
    except ValueError as exc:  # Defensive even though ``safe_id`` is constrained.
        raise ValueError("工作流输出目录不合法") from exc
    root.mkdir(parents=True, exist_ok=True)
    return root


def audio_output_path(workflow_id: str) -> Path:
    path = workflow_output_root(workflow_id) / "audio" / "merged.wav"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def audio_upload_max_bytes() -> int:
    """Return the bounded per-workflow source-audio upload limit."""

    raw = os.getenv("SCRIPT_VIDEO_WORKFLOW_MAX_AUDIO_UPLOAD_MB", str(DEFAULT_AUDIO_UPLOAD_MAX_MB))
    try:
        megabytes = float(raw)
    except (TypeError, ValueError):
        megabytes = float(DEFAULT_AUDIO_UPLOAD_MAX_MB)
    megabytes = max(1.0, min(megabytes, 2048.0))
    return int(megabytes * 1024 * 1024)


def normalized_audio_filename(value: Any) -> str:
    """Keep a display name without allowing paths or controls into metadata."""

    raw = Path(str(value or "")).name.strip()
    cleaned = "".join(character for character in raw if character >= " " and character != "\x7f")[:255]
    if not cleaned:
        raise ValueError("音频文件名为空")
    suffix = Path(cleaned).suffix.lower()
    if suffix not in AUDIO_UPLOAD_EXTENSIONS:
        supported = "、".join(sorted(extension.lstrip(".").upper() for extension in AUDIO_UPLOAD_EXTENSIONS))
        raise ValueError(f"仅支持 {supported} 格式的音频")
    return cleaned


def audio_upload_path(workflow_id: str, filename: Any, *, upload_key: str) -> Path:
    """Return a private, extension-preserving raw-upload path for one workflow."""

    display_name = normalized_audio_filename(filename)
    safe_key = re.sub(r"[^A-Za-z0-9_-]", "", str(upload_key or ""))[:64]
    if not safe_key:
        raise ValueError("上传标识不合法")
    path = workflow_output_root(workflow_id) / "audio" / "uploads" / f"{safe_key}{Path(display_name).suffix.lower()}"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def source_audio_path(workflow_id: str) -> Path:
    """Return the normalized WAV used by the browser editor, never the final output."""

    path = workflow_output_root(workflow_id) / "audio" / "source.wav"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def final_audio_output_path(workflow_id: str, revision: Any) -> Path:
    """Return a versioned final WAV so a prior source/final artifact is retained."""

    try:
        index = int(revision)
    except (TypeError, ValueError):
        index = 1
    index = max(1, min(index, 10_000))
    path = workflow_output_root(workflow_id) / "audio" / f"final-{index:03d}.wav"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def validate_audio_edit_options(
    value: Mapping[str, Any] | Any,
    *,
    source_seconds: Any,
) -> dict[str, float]:
    """Validate the only supported non-destructive edits: head/tail trim and speed."""

    payload = value if isinstance(value, Mapping) else {}
    duration = _finite_float(source_seconds, "原始音频时长")
    if duration <= MIN_AUDIO_EDIT_SECONDS:
        raise ValueError("原始音频时长过短，无法编辑")
    start = _finite_float(payload.get("trim_start_seconds", 0), "开头裁剪时间")
    end = _finite_float(payload.get("trim_end_seconds", duration), "结尾裁剪时间")
    speed = _finite_float(payload.get("speed", 1), "倍速")
    if start < 0:
        raise ValueError("开头裁剪时间不能小于 0")
    if end > duration + 0.05:
        raise ValueError("结尾裁剪时间不能超过原始音频时长")
    end = min(end, duration)
    if end - start < MIN_AUDIO_EDIT_SECONDS:
        raise ValueError("裁剪后至少保留 0.1 秒语音")
    if not MIN_AUDIO_EDIT_SPEED <= speed <= MAX_AUDIO_EDIT_SPEED:
        raise ValueError("倍速仅支持 0.5x 到 2.0x")
    return {
        "trim_start_seconds": round(start, 3),
        "trim_end_seconds": round(end, 3),
        "speed": round(speed, 3),
    }


def scene_image_output_path(
    workflow_id: str,
    sequence: int,
    *,
    artifact_key: str | None = None,
) -> Path:
    index = _positive_int(sequence)
    suffix = ""
    if artifact_key:
        safe_key = re.sub(r"[^A-Za-z0-9_-]", "", str(artifact_key))[:24]
        if safe_key:
            suffix = f"-{safe_key}"
    path = workflow_output_root(workflow_id) / "images" / f"{index:04d}{suffix}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def video_output_path(workflow_id: str) -> Path:
    path = workflow_output_root(workflow_id) / "video" / "video.mp4"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def safe_output_file(root: Path | str, candidate: Path | str) -> Path:
    """Resolve ``candidate`` only if it is contained by the workflow root."""

    root_path = Path(root).expanduser().resolve()
    candidate_path = Path(candidate).expanduser()
    if not candidate_path.is_absolute():
        candidate_path = root_path / candidate_path
    candidate_path = candidate_path.resolve()
    try:
        candidate_path.relative_to(root_path)
    except ValueError as exc:
        raise ValueError("文件不在工作流目录内") from exc
    return candidate_path


def _scene_row(sequence: int, script_text: str) -> dict[str, Any]:
    return {
        "sequence": sequence,
        "script_text": script_text,
        "image_prompt": "",
    }


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        result = default
    return max(minimum, min(result, maximum))


def _finite_float(value: Any, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label}必须是数字") from exc
    if not math.isfinite(result):
        raise ValueError(f"{label}必须是有限数字")
    return result


def _coverage_text(value: Any) -> str:
    return _WHITESPACE_RE.sub("", normalize_script(value))


def _positive_int(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("分镜序号必须是正整数")
    try:
        integer = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("分镜序号必须是正整数") from exc
    if integer < 1 or str(integer) != str(value).strip():
        raise ValueError("分镜序号必须是正整数")
    return integer


def _safe_workflow_id(value: Any) -> str:
    workflow_id = str(value or "").strip()
    if not _WORKFLOW_ID_RE.fullmatch(workflow_id):
        raise ValueError("工作流 ID 不合法")
    return workflow_id


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]
