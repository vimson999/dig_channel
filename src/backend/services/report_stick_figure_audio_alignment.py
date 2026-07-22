"""整篇研报音频复用、文本校验和分镜时间轴对齐。

本模块只负责确定性的数据处理与产物落盘。ASR 调用由 Celery 任务负责，
数据库热路径只读取结构化投影；逐镜头时间轴写入场景资产表。
"""

from __future__ import annotations

import hashlib
import json
import os
import unicodedata
import uuid
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from sqlalchemy import or_
from sqlalchemy.orm import load_only

from models.task import PublicationTask
from services.report_stick_figure_video import audio_duration_seconds, workflow_output_root


FEATURE_NAME = "report_stick_figure_audio_alignment"
QUEUE_NAME = "render"
ALIGNMENT_SCHEMA_VERSION = "stick_whole_audio_alignment_v1"
REPORT_AUDIO_FEATURES = ("report_pdf_audio_workflow", "report_audio_workflow")
MIN_SOURCE_SCRIPT_RATIO = 0.86
MIN_ASR_MATCH_RATIO = 0.42


@dataclass(frozen=True)
class AudioCandidate:
    task_id: str
    audio_path: str
    audio_bytes: int
    audio_seconds: float
    final_script_chars: int
    attachment_id: str
    title: str


def text_hash(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def normalize_alignment_text(value: str) -> str:
    """保留中英文和数字，消除标点、空白与全半角差异。"""

    normalized = unicodedata.normalize("NFKC", str(value or "")).lower()
    return "".join(char for char in normalized if char.isalnum())


def find_reusable_audio_candidate(
    *,
    attachment_id: str = "",
    material_task_id: str = "",
    visible_user_ids: Iterable[str] = (),
) -> AudioCandidate | None:
    """按结构化列寻找同一研报已经完成且文件完整的整篇 WAV。"""

    attachment_id = str(attachment_id or "").strip()
    material_task_id = str(material_task_id or "").strip()
    if not attachment_id and not material_task_id:
        return None
    identity_filters = []
    if material_task_id:
        identity_filters.append(PublicationTask.id == material_task_id)
    if attachment_id:
        identity_filters.append(PublicationTask.report_audio_pdf_attachment_id == attachment_id)
    query = (
        PublicationTask.query.options(load_only(
            PublicationTask.id,
            PublicationTask.user_id,
            PublicationTask.feature,
            PublicationTask.status,
            PublicationTask.report_audio_wav_path,
            PublicationTask.report_audio_seconds,
            PublicationTask.report_audio_audio_bytes,
            PublicationTask.report_audio_pdf_attachment_id,
            PublicationTask.report_audio_pdf_topic_title,
            PublicationTask.report_audio_pdf_filename,
            PublicationTask.final_script_chars,
            PublicationTask.completed_at,
            PublicationTask.created_at,
        ))
        .filter(PublicationTask.feature.in_(REPORT_AUDIO_FEATURES))
        .filter(PublicationTask.status == "completed")
        .filter(PublicationTask.report_audio_wav_path.isnot(None))
        .filter(or_(*identity_filters))
    )
    user_ids = tuple(str(value) for value in visible_user_ids if str(value or "").strip())
    if user_ids:
        query = query.filter(or_(PublicationTask.user_id.in_(user_ids), PublicationTask.user_id.is_(None)))
    rows = query.order_by(PublicationTask.completed_at.desc(), PublicationTask.created_at.desc()).limit(10).all()
    for row in rows:
        path = Path(str(row.report_audio_wav_path or "")).expanduser()
        if not path.is_file() or path.stat().st_size <= 0:
            continue
        try:
            seconds = float(row.report_audio_seconds or 0)
        except (TypeError, ValueError):
            seconds = 0.0
        if seconds <= 0:
            seconds = audio_duration_seconds(str(path))
        if seconds <= 0.1:
            continue
        return AudioCandidate(
            task_id=str(row.id),
            audio_path=str(path.resolve()),
            audio_bytes=int(row.report_audio_audio_bytes or path.stat().st_size),
            audio_seconds=float(seconds),
            final_script_chars=int(row.final_script_chars or 0),
            attachment_id=str(row.report_audio_pdf_attachment_id or ""),
            title=str(row.report_audio_pdf_topic_title or row.report_audio_pdf_filename or "整篇研报语音"),
        )
    return None


def extract_final_script(result: Mapping | None) -> str:
    if not isinstance(result, Mapping):
        return ""
    structured = result.get("structured_result")
    if isinstance(structured, Mapping):
        value = structured.get("final_script_for_tts")
        if value:
            return str(value).strip()
    material = result.get("script_material")
    if isinstance(material, Mapping):
        value = material.get("final_script_for_tts")
        if value:
            return str(value).strip()
    for key in ("final_script_for_tts", "tts_text", "audio_script"):
        value = result.get(key)
        if value:
            return str(value).strip()
    return ""


def align_storyboard_to_audio(
    scenes: Sequence[Mapping],
    asr_segments: Sequence[Mapping],
    *,
    audio_seconds: float,
    source_script: str = "",
) -> dict:
    """把顺序分镜原文映射到带时间戳的 ASR 文本，并生成连续时间轴。"""

    ordered = [item for item in sorted(scenes, key=lambda row: int(row["sequence"])) if int(item["sequence"]) >= 1]
    if not ordered:
        raise ValueError("没有可对齐的正文分镜（正文必须从 001 开始）")
    audio_seconds = float(audio_seconds or 0)
    if audio_seconds <= 0.1:
        raise ValueError("整篇音频时长无效")

    scene_texts = [normalize_alignment_text(item.get("script_text") or "") for item in ordered]
    if any(not text for text in scene_texts):
        empty_sequence = ordered[scene_texts.index("")]["sequence"]
        raise ValueError(f"第 {empty_sequence} 段正文为空，无法进行音频对齐")
    storyboard_text = "".join(scene_texts)
    source_text = normalize_alignment_text(source_script)
    source_ratio = (
        SequenceMatcher(None, storyboard_text, source_text, autojunk=False).ratio()
        if source_text
        else 1.0
    )
    if source_text and source_ratio < MIN_SOURCE_SCRIPT_RATIO:
        raise ValueError(
            f"整篇音频文案与当前分镜正文不一致：匹配度 {source_ratio:.1%}，"
            f"低于 {MIN_SOURCE_SCRIPT_RATIO:.0%}"
        )

    asr_text, asr_char_times = _flatten_asr_segments(asr_segments, audio_seconds)
    if not asr_text or not asr_char_times:
        raise ValueError("ASR 没有返回可用于时间轴对齐的文本")

    matcher = SequenceMatcher(None, storyboard_text, asr_text, autojunk=False)
    matching_blocks = [block for block in matcher.get_matching_blocks() if block.size > 0]
    matched_chars = sum(block.size for block in matching_blocks)
    asr_ratio = matched_chars / max(1, len(storyboard_text))
    if asr_ratio < MIN_ASR_MATCH_RATIO:
        raise ValueError(
            f"整篇音频 ASR 与分镜正文匹配度过低：{asr_ratio:.1%}，"
            f"低于 {MIN_ASR_MATCH_RATIO:.0%}"
        )

    source_to_asr = {}
    for block in matching_blocks:
        for offset in range(block.size):
            source_to_asr[block.a + offset] = block.b + offset

    spans = []
    cursor = 0
    for item, normalized in zip(ordered, scene_texts):
        start_index = cursor
        end_index = cursor + len(normalized)
        mapped = [source_to_asr[index] for index in range(start_index, end_index) if index in source_to_asr]
        confidence = len(mapped) / max(1, len(normalized))
        spans.append({
            "asset_id": str(item.get("asset_id") or ""),
            "segment_id": str(item.get("segment_id") or ""),
            "sequence": int(item["sequence"]),
            "script_text": str(item.get("script_text") or "").strip(),
            "normalized_chars": len(normalized),
            "matched_chars": len(mapped),
            "confidence": confidence,
            "anchor_start": asr_char_times[min(mapped)][0] if mapped else None,
            "anchor_end": asr_char_times[max(mapped)][1] if mapped else None,
            "source_end_ratio": end_index / max(1, len(storyboard_text)),
        })
        cursor = end_index

    boundaries = _continuous_boundaries(spans, audio_seconds)
    timeline = []
    for index, span in enumerate(spans):
        timeline.append({
            "asset_id": span["asset_id"],
            "segment_id": span["segment_id"],
            "sequence": span["sequence"],
            "script_text": span["script_text"],
            "start_seconds": round(boundaries[index], 6),
            "end_seconds": round(boundaries[index + 1], 6),
            "duration_seconds": round(boundaries[index + 1] - boundaries[index], 6),
            "confidence": round(span["confidence"], 6),
            "matched_chars": span["matched_chars"],
            "normalized_chars": span["normalized_chars"],
        })
    confidences = [entry["confidence"] for entry in timeline]
    return {
        "schema_version": ALIGNMENT_SCHEMA_VERSION,
        "audio_seconds": round(audio_seconds, 6),
        "scene_count": len(timeline),
        "source_script_ratio": round(source_ratio, 6),
        "asr_match_ratio": round(asr_ratio, 6),
        "average_confidence": round(sum(confidences) / len(confidences), 6),
        "minimum_confidence": round(min(confidences), 6),
        "storyboard_script_hash": text_hash(storyboard_text),
        "source_script_hash": text_hash(source_text) if source_text else None,
        "timeline": timeline,
    }


def write_alignment_manifest(workflow_task_id: str, payload: Mapping) -> str:
    root = workflow_output_root(str(workflow_task_id))
    root.mkdir(parents=True, exist_ok=True)
    target = root / "audio_alignment.json"
    part = target.with_name(f"{target.name}.part-{uuid.uuid4().hex[:8]}")
    part.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(part, target)
    return str(target)


def _flatten_asr_segments(segments: Sequence[Mapping], audio_seconds: float):
    text_parts = []
    char_times = []
    for raw in segments or []:
        normalized = normalize_alignment_text(raw.get("text") or "")
        if not normalized:
            continue
        try:
            start = max(0.0, float(raw.get("start") or 0.0))
            end = min(audio_seconds, float(raw.get("end") or start))
        except (TypeError, ValueError):
            continue
        if end <= start:
            end = min(audio_seconds, start + 0.05 * len(normalized))
        unit = max(0.001, (end - start) / len(normalized))
        for index, char in enumerate(normalized):
            text_parts.append(char)
            char_times.append((start + unit * index, min(end, start + unit * (index + 1))))
    return "".join(text_parts), char_times


def _continuous_boundaries(spans: Sequence[Mapping], audio_seconds: float) -> list[float]:
    scene_count = len(spans)
    if scene_count == 1:
        return [0.0, audio_seconds]
    minimum = min(0.25, audio_seconds / max(4.0, scene_count * 4.0))
    boundaries = [0.0]
    for index in range(1, scene_count):
        previous = spans[index - 1]
        current = spans[index]
        previous_end = previous.get("anchor_end")
        current_start = current.get("anchor_start")
        if previous_end is not None and current_start is not None:
            candidate = (float(previous_end) + float(current_start)) / 2.0
        elif previous_end is not None:
            candidate = float(previous_end)
        elif current_start is not None:
            candidate = float(current_start)
        else:
            candidate = audio_seconds * float(previous["source_end_ratio"])
        lower = boundaries[-1] + minimum
        upper = audio_seconds - minimum * (scene_count - index)
        boundaries.append(max(lower, min(candidate, upper)))
    boundaries.append(audio_seconds)
    return boundaries
