import json
import shutil
import subprocess
import uuid
from datetime import datetime
from pathlib import Path
import re
from difflib import SequenceMatcher
import logging
import math

from flask import Blueprint, jsonify, request

from models import db
from models.report_video import (
    ReportVideoAnalysis,
    ReportVideoAudio,
    ReportVideoMindmap,
    ReportVideoOutput,
    ReportVideoReport,
    ReportVideoSegment,
    ReportVideoTask,
)
from services.report_video_service import (
    PROJECT_ROOT,
    rel_path,
    read_task_text_file,
    report_code,
    report_dir,
    save_report_pdf_file,
    save_report_pdf,
    task_dir,
)
from services.report_video_ai_service import generate_report_ai_content
from services.report_video_prompt_service import (
    list_prompt_templates,
    reset_prompt_template,
    save_prompt_template,
    render_prompt,
)
from services.report_video_auto_service import (
    auto_scan_preview,
    load_auto_scan_config,
    load_auto_scan_config_from_dict,
    save_auto_scan_config,
)
from services.report_video_render_service import media_duration, render_report_video, render_task_video, safe_filename


bp = Blueprint("report_video", __name__, url_prefix="/api/v1/report-video")
logger = logging.getLogger(__name__)


def _audio_speed_value(value):
    try:
        speed = float(value)
    except (TypeError, ValueError):
        speed = 1.0
    if speed < 0.8 or speed > 1.5:
        speed = 1.0
    return round(speed, 3)


def _audio_speed_label(speed):
    return str(speed).replace(".", "_")


def _atempo_filter(speed):
    speed = float(speed or 1.0)
    if speed <= 0:
        return "atempo=1.0"
    parts = []
    remaining = speed
    while remaining > 2.0:
        parts.append("atempo=2.0")
        remaining /= 2.0
    while remaining < 0.5:
        parts.append("atempo=0.5")
        remaining /= 0.5
    parts.append(f"atempo={remaining:.6f}")
    return ",".join(parts)


def _create_speed_adjusted_audio(source_path, speed):
    speed = _audio_speed_value(speed)
    if abs(speed - 1.0) < 0.001:
        return source_path
    output_path = source_path.with_name(f"{source_path.stem}_speed_{_audio_speed_label(speed)}.m4a")
    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(source_path),
        "-filter:a",
        _atempo_filter(speed),
        "-vn",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        str(output_path),
    ]
    try:
        subprocess.run(command, check=True, capture_output=True, text=True, timeout=900)
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or exc.stdout or str(exc))[-600:]
        raise RuntimeError(f"音频变速失败：{stderr}") from exc
    return output_path


def _scale_timeline_entries(entries, speed, target_duration=None):
    speed = _audio_speed_value(speed)
    if abs(speed - 1.0) < 0.001:
        return entries
    scaled = []
    max_duration = float(target_duration or 0.0)
    for entry in entries or []:
        item = dict(entry)
        start_sec = float(item.get("start_sec") or 0.0) / speed
        end_sec = float(item.get("end_sec") or start_sec) / speed
        if max_duration > 0:
            start_sec = min(start_sec, max_duration)
            end_sec = min(end_sec, max_duration)
        if end_sec <= start_sec:
            end_sec = start_sec + 0.05
        item["start_sec"] = round(start_sec, 3)
        item["end_sec"] = round(end_sec, 3)
        scaled.append(item)
    return scaled


def _should_force_asr_for_full_audio(form_value, audio_mode, has_timeline_payload):
    if audio_mode != "full_audio_srt" or has_timeline_payload:
        return False
    if form_value is None:
        return True
    return str(form_value).strip().lower() in {"true", "1", "yes", "on"}


def _task_or_404(task_id):
    task = ReportVideoTask.query.get(task_id)
    if not task:
        return None, (jsonify({"success": False, "error": "Task not found"}), 404)
    return task, None


def _report_or_404(report_id):
    report = ReportVideoReport.query.get(report_id)
    if not report:
        return None, (jsonify({"success": False, "error": "Report not found"}), 404)
    return report, None


def _next_report_index(task):
    indexes = []
    for report in task.reports:
        if str(report.report_code).isdigit():
            indexes.append(int(report.report_code))
    return (max(indexes) if indexes else 0) + 1


def _task_status_from_reports(task, reports=None):
    reports = list(task.reports if reports is None else reports)
    if not reports:
        return "created"
    failed = any(report.parse_status == "parse_failed" for report in reports)
    parsed = all(report.parse_status == "parsed" for report in reports)
    if parsed:
        return "parsed"
    if failed:
        return "uploaded"
    return "parsing"


def _parse_result_status(parsed):
    cleaned_chars = int(parsed.get("cleaned_text_chars") or 0)
    extraction_method = parsed.get("extraction_method") or "pdf_text"
    if cleaned_chars >= 500 and extraction_method != "pdf_text_low_quality":
        return "parsed", None
    if extraction_method == "pdf_text_low_quality":
        ocr_meta = ((parsed.get("stats") or {}).get("ocr") or {})
        if ocr_meta.get("available") is False:
            return "parse_failed", "PDF 正文疑似图片/扫描版，当前环境 OCR 不可用"
        return "parse_failed", "PDF 可提取正文过少，OCR 未获得有效正文"
    return "parse_failed", "PDF 可提取正文过少，疑似扫描版或图片型 PDF"


def _enqueue_report_video_task(celery_task, *args):
    return celery_task.apply_async(args=list(args), queue="report_video")


def _topic_planning_payload(task):
    result = dict(task.result or {})
    planning = dict(result.get("topic_planning") or {})
    return {
        "task_id": task.id,
        "status": planning.get("status") or "empty",
        "report_cards": planning.get("report_cards") or [],
        "card_errors": planning.get("card_errors") or [],
        "topics": planning.get("topics") or [],
        "ranked_topics": planning.get("ranked_topics") or [],
        "external_heat": planning.get("external_heat") or {
            "search_status": "not_enabled",
            "external_heat_score": None,
            "why_now": "当前未启用实时搜索，仅基于上传研报内部热度排序。",
        },
        "topic_scripts": planning.get("topic_scripts") or {},
        "active_topic_id": planning.get("active_topic_id"),
        "updated_at": planning.get("updated_at"),
    }


def _set_topic_planning_status(task, status, **updates):
    result = dict(task.result or {})
    planning = dict(result.get("topic_planning") or {})
    planning.update(updates)
    planning["status"] = status
    planning["updated_at"] = datetime.utcnow().isoformat()
    result["topic_planning"] = planning
    task.result = result
    task.updated_at = datetime.utcnow()
    return planning


def _task_segments(task):
    segments = []
    for report in task.reports:
        segments.extend(list(report.segments))
    return sorted(segments, key=lambda item: (item.report.report_code if item.report else "", item.segment_index))


def _normalize_timeline_text(value):
    return re.sub(r"[\s，。、“”‘’：:；;,.!?！？（）()\[\]【】《》|·\-—_]+", "", value or "").lower()


def _time_to_seconds(value):
    value = (value or "").strip().replace("：", ":")
    parts = value.split(":")
    if len(parts) == 2:
        minutes, seconds = parts
        return int(minutes) * 60 + float(seconds.replace(",", "."))
    if len(parts) == 3:
        hours, minutes, seconds = parts
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds.replace(",", "."))
    raise ValueError(f"非法时间格式: {value}")


def _parse_timeline_text(text):
    text = (text or "").strip()
    if not text:
        return []

    entries = []
    srt_blocks = re.split(r"\n\s*\n", text)
    for block in srt_blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        time_index = next((idx for idx, line in enumerate(lines) if "-->" in line), -1)
        if time_index >= 0:
            start_raw, end_raw = [part.strip() for part in lines[time_index].split("-->", 1)]
            body = " ".join(lines[time_index + 1:]).strip()
            if body:
                entries.append({
                    "start_sec": _time_to_seconds(start_raw),
                    "end_sec": _time_to_seconds(end_raw),
                    "text": body,
                })

    if entries:
        return sorted(entries, key=lambda item: item["start_sec"])

    line_pattern = re.compile(
        r"^\s*(?P<start>\d{1,2}:\d{2}(?::\d{2})?(?:[,.]\d+)?)\s*[-~至]\s*"
        r"(?P<end>\d{1,2}:\d{2}(?::\d{2})?(?:[,.]\d+)?)\s*(?:\||：|:)?\s*(?P<text>.+?)\s*$"
    )
    for line in text.splitlines():
        match = line_pattern.match(line)
        if not match:
            continue
        body = (match.group("text") or "").strip()
        if not body:
            continue
        entries.append({
            "start_sec": _time_to_seconds(match.group("start")),
            "end_sec": _time_to_seconds(match.group("end")),
            "text": body,
        })
    return sorted(entries, key=lambda item: item["start_sec"])


def _estimate_timeline_text_weight(text):
    source = str(text or "")
    compact = _normalize_timeline_text(source)
    punctuation_count = len(re.findall(r"[，。；：！？,.!?;:]", source))
    return max(len(compact), 8) + punctuation_count * 1.6


def _write_timeline_source_text(task, entries, suffix="timeline"):
    timeline_dir = task_dir(task.id) / "timelines"
    timeline_dir.mkdir(parents=True, exist_ok=True)
    target = timeline_dir / f"{uuid.uuid4().hex[:8]}_{suffix}.txt"
    rows = []
    for entry in entries:
        start_min = int(float(entry["start_sec"]) // 60)
        start_sec = float(entry["start_sec"]) % 60
        end_min = int(float(entry["end_sec"]) // 60)
        end_sec = float(entry["end_sec"]) % 60
        rows.append(f"{start_min:02d}:{start_sec:05.2f}-{end_min:02d}:{end_sec:05.2f} | {entry.get('text') or ''}")
    target.write_text("\n".join(rows) + ("\n" if rows else ""), encoding="utf-8")
    return rel_path(target)


def _auto_generate_timeline_entries(task, target_duration):
    segments = _task_segments(task)
    if not segments:
        return [], {"mode": "auto_generated", "reason": "no_segments"}

    target_duration = max(float(target_duration or 0), 1.0)
    weighted_segments = []
    estimated_sum = sum(float(segment.estimated_duration_sec or 0) for segment in segments if (segment.estimated_duration_sec or 0) > 0)

    for segment in segments:
        text = (segment.voice_text or segment.segment_title or "").strip()
        if not text:
            text = segment.segment_title or "本段解读"
        if estimated_sum > 0:
            weight = max(float(segment.estimated_duration_sec or 0), 1.0)
        else:
            weight = _estimate_timeline_text_weight(text)
        weighted_segments.append((segment, text, weight))

    total_weight = sum(item[2] for item in weighted_segments) or float(len(weighted_segments))
    entries = []
    cursor = 0.0
    for index, (segment, text, weight) in enumerate(weighted_segments):
        if index == len(weighted_segments) - 1:
            end_sec = target_duration
        else:
            duration = max(target_duration * (weight / total_weight), 1.0)
            end_sec = min(target_duration, cursor + duration)
        if end_sec <= cursor:
            end_sec = min(target_duration, cursor + 1.0)
        entries.append({
            "segment_id": segment.id,
            "segment_key": segment.segment_id,
            "start_sec": round(cursor, 3),
            "end_sec": round(end_sec, 3),
            "text": text,
        })
        cursor = end_sec

    if entries:
        entries[-1]["end_sec"] = round(target_duration, 3)
    return entries, {
        "mode": "auto_generated",
        "strategy": "script_duration_estimate",
        "segment_count": len(entries),
        "target_duration_sec": round(target_duration, 3),
        "used_estimated_segment_durations": estimated_sum > 0,
    }


def _merge_asr_timeline_entries(entries, min_chars=10, max_chars=28):
    merged = []
    buffer = None

    def text_len(value):
        return len(_normalize_timeline_text(value))

    def is_tiny_fragment(value):
        return len(_normalize_timeline_text(value)) <= 2

    def should_merge(current_text, next_text):
        current_text = current_text or ""
        next_text = next_text or ""
        current_norm_len = text_len(current_text)
        next_norm_len = text_len(next_text)
        stripped_next = next_text.strip()
        if current_norm_len <= 0:
            return True
        if is_tiny_fragment(next_text):
            return True
        if re.match(r"^[%％,，.。;；:：!?！？]+$", stripped_next):
            return True
        if re.search(r"[A-Za-z]$", current_text) and re.match(r"^[A-Za-z]", stripped_next):
            return True
        if re.search(r"\d$", current_text) and re.match(r"^[%％]", stripped_next):
            return True
        if current_norm_len < min_chars:
            return True
        if not re.search(r"[。！？!?]$", current_text.strip()) and current_norm_len + next_norm_len <= max_chars:
            return True
        return False

    def flush():
        nonlocal buffer
        if buffer:
            merged.append(buffer)
            buffer = None

    for entry in entries or []:
        text = re.sub(r"\s+", " ", str(entry.get("text") or "")).strip()
        if not text:
            continue
        item = dict(entry)
        item["text"] = text
        if buffer is None:
            buffer = item
            continue
        if should_merge(buffer.get("text") or "", text):
            joiner = "" if re.search(r"[A-Za-z]$", buffer.get("text") or "") and re.match(r"^[A-Za-z]", text) else " "
            buffer["text"] = f"{buffer.get('text') or ''}{joiner}{text}".strip()
            buffer["end_sec"] = item.get("end_sec", buffer.get("end_sec"))
        else:
            flush()
            buffer = item
    flush()

    for index, item in enumerate(merged, start=1):
        item["entry_id"] = f"asr_{index:04d}"
    return merged


def _generate_timeline_entries_from_asr(task, audio_path, target_duration, model_size="medium", initial_prompt=None):
    from services.asr_cache_service import transcribe_with_cache

    if not audio_path or not Path(audio_path).exists():
        return [], {"mode": "asr_transcript", "status": "fallback", "reason": "audio_missing"}

    raw_result = transcribe_with_cache(
        str(audio_path),
        model_size=model_size or "medium",
        initial_prompt=initial_prompt,
        include_metadata=True,
    )
    cache_meta = {}
    raw_segments = raw_result
    if isinstance(raw_result, dict) and isinstance(raw_result.get("metadata"), dict):
        cache_meta = raw_result.get("metadata") or {}
        raw_segments = raw_result.get("segments") or []

    if isinstance(raw_segments, dict):
        if isinstance(raw_segments.get("segments"), list):
            segments = raw_segments.get("segments") or []
        elif raw_segments.get("text"):
            segments = [{
                "start": 0.0,
                "end": float(target_duration or 0) or 1.0,
                "text": raw_segments.get("text"),
            }]
        else:
            segments = []
    elif isinstance(raw_segments, str):
        segments = [{
            "start": 0.0,
            "end": float(target_duration or 0) or 1.0,
            "text": raw_segments,
        }]
    else:
        segments = raw_segments or []

    entries = []
    cursor = 0.0
    for index, segment in enumerate(segments or []):
        if isinstance(segment, dict):
            text = re.sub(r"\s+", " ", str(segment.get("text") or "")).strip()
            start_value = segment.get("start") if segment.get("start") is not None else cursor
            end_value = segment.get("end") if segment.get("end") is not None else float(start_value) + 1.0
        else:
            text = re.sub(r"\s+", " ", str(segment or "")).strip()
            start_value = cursor
            end_value = float(start_value) + 1.0
        if not text:
            continue
        start_sec = float(start_value)
        end_sec = float(end_value)
        if end_sec <= start_sec:
            end_sec = start_sec + 0.5
        entries.append({
            "entry_id": f"asr_{index + 1:04d}",
            "start_sec": round(max(start_sec, 0.0), 3),
            "end_sec": round(max(end_sec, start_sec + 0.2), 3),
            "text": text,
        })
        cursor = end_sec

    raw_entry_count = len(entries)
    entries = _merge_asr_timeline_entries(entries)

    if entries and target_duration:
        entries[-1]["end_sec"] = min(max(float(entries[-1]["end_sec"]), float(entries[-1]["start_sec"]) + 0.2), float(target_duration))
    return entries, {
        "mode": "asr_transcript",
        "strategy": "full_audio_asr",
        "status": "calibrated" if entries else "fallback",
        "reason": None if entries else "empty_asr_result",
        "model_size": model_size or "medium",
        "segment_count": len(entries),
        "raw_segment_count": raw_entry_count,
        "target_duration_sec": round(float(target_duration or 0), 3),
        "subtitle_source": "full_audio_asr",
        "asr_cache": cache_meta,
    }


def _seconds_to_srt_time(value):
    value = max(float(value or 0), 0)
    hours = int(value // 3600)
    minutes = int((value % 3600) // 60)
    seconds = int(value % 60)
    millis = int(round((value - int(value)) * 1000))
    if millis >= 1000:
        seconds += 1
        millis -= 1000
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def _split_subtitle_sentences(text):
    pieces = re.split(r"(?<=[，。；：！？])", str(text or ""))
    sentences = []
    for piece in pieces:
        candidate = re.sub(r"\s+", " ", piece).strip()
        if candidate:
            sentences.append(candidate)
    return sentences


SUBTITLE_PROTECTED_TERMS = (
    "全面",
    "数据",
    "中心",
    "数据中心",
    "效应",
    "复合效应",
    "输电",
    "配电",
    "电力",
    "研报",
    "资本",
    "支出",
    "需求",
    "储能",
    "投资",
    "锂电池",
)


SUBTITLE_DISPLAY_REPLACEMENTS = (
    ("博恩斯坦", "伯恩斯坦"),
    ("言报", "研报"),
    ("基础事实", "基础设施"),
    ("基础试试", "基础设施"),
    ("电器设备", "电气设备"),
    ("电 器", "电气"),
    ("Gwanta", "Quanta"),
    ("Ethan", "Eaton"),
    ("Hubble", "Hubbell"),
    ("标普8", "标普"),
    ("书店投资", "输电投资"),
    ("书店增长", "输电增长"),
    ("书店", "输电"),
    ("复核效应", "复合效应"),
    ("铃电池", "锂电池"),
    ("理电池", "锂电池"),
    ("TW时", "TWh"),
)


def _normalize_subtitle_display_text(text):
    clean = re.sub(r"\s+", " ", str(text or "")).strip()
    clean = clean.replace(",", "，")
    clean = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", clean)
    clean = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[，。！？；：、,.!?;:%％])", "", clean)
    clean = re.sub(r"(?<=[，。！？；：、,.!?;:])\s+(?=[\u4e00-\u9fff])", "", clean)
    clean = re.sub(r"(?<=[\u4e00-\u9fff])[,](?=[\u4e00-\u9fffA-Za-z0-9])", "，", clean)
    clean = re.sub(r"(?<=[A-Za-z0-9%％])[,](?=[\u4e00-\u9fff])", "，", clean)
    clean = re.sub(r"(?<=\d)\s+(?=[%％])", "", clean)
    clean = re.sub(r"(?<=[%％])\s+(?=[\u4e00-\u9fff])", "", clean)
    clean = re.sub(r"(?<=\d)\.\s+(?=\d)", ".", clean)
    clean = re.sub(
        r"(?<=\d)\s+(?=\d+(?:GW|GWh|TWh|MWh|KWh|%|％|港元|美元|元|万|亿|兆|个|份|家|条|股|倍|年|月|日))",
        "",
        clean,
    )
    for source, target in SUBTITLE_DISPLAY_REPLACEMENTS:
        clean = clean.replace(source, target)
    return clean


def _adjust_subtitle_cut(value, cut_at):
    if cut_at <= 0 or cut_at >= len(value):
        return cut_at
    for term in sorted(SUBTITLE_PROTECTED_TERMS, key=len, reverse=True):
        start = value.rfind(term, 0, min(len(value), cut_at + len(term)))
        while start >= 0:
            end = start + len(term)
            if start < cut_at < end:
                return start
            if cut_at == end:
                return cut_at
            start = value.rfind(term, 0, start)
    return cut_at


def _wrap_subtitle_lines(text, max_chars_per_line=18, max_lines=2):
    clean = _normalize_subtitle_display_text(text)
    if not clean:
        return []

    def char_width(ch):
        return 1.0 if re.match(r"[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]", ch) else 0.56

    def is_ascii_token_char(ch):
        return bool(ch) and ch.isascii() and (ch.isalnum() or ch in ".%％+-_/")

    def adjust_token_cut(value, cut_at):
        if cut_at <= 0 or cut_at >= len(value):
            return cut_at
        if not (is_ascii_token_char(value[cut_at - 1]) and is_ascii_token_char(value[cut_at])):
            return cut_at

        left = cut_at
        while left > 0 and is_ascii_token_char(value[left - 1]):
            left -= 1
        right = cut_at
        while right < len(value) and is_ascii_token_char(value[right]):
            right += 1

        if left > 0 and cut_at - left <= 8:
            return left
        if right - cut_at <= 8:
            return right
        return cut_at

    lines = []
    remaining = clean
    while remaining and len(lines) < max_lines:
        width = 0.0
        cut_at = len(remaining)
        last_soft_break = -1
        for index, ch in enumerate(remaining):
            width += char_width(ch)
            if ch.isspace() or ch in "，。！？；：、,.!?;:":
                last_soft_break = index + 1
            if width > max_chars_per_line:
                cut_at = index
                if last_soft_break >= max(1, index - 8):
                    cut_at = last_soft_break
                break
        cut_at = adjust_token_cut(remaining, cut_at)
        cut_at = _adjust_subtitle_cut(remaining, cut_at)
        line = remaining[:max(cut_at, 1)].strip()
        if not line:
            line = remaining[:1]
            cut_at = 1
        lines.append(line)
        remaining = remaining[max(cut_at, 1):].strip()
    return lines


def _split_subtitle_entry(entry, max_chars_per_line=18, max_lines=2):
    text = _normalize_subtitle_display_text(entry.get("text"))
    if not text:
        return []

    max_chars = max_chars_per_line * max_lines
    sentences = _split_subtitle_sentences(text)
    chunks = []
    current = ""

    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        if len(re.sub(r"\s+", "", current + sentence)) <= max_chars:
            current += sentence
            continue
        if current:
            chunks.append(current)
        current = sentence

        while len(re.sub(r"\s+", "", current)) > max_chars:
            compact = re.sub(r"\s+", "", current)
            cut_at = _adjust_subtitle_cut(compact, max_chars)
            chunks.append(compact[:cut_at])
            current = compact[cut_at:]

    if current:
        chunks.append(current)

    if not chunks:
        chunks = [text]

    start_sec = float(entry.get("start_sec") or 0)
    end_sec = float(entry.get("end_sec") or start_sec + 1)
    total_duration = max(end_sec - start_sec, 0.2)
    chunk_duration = total_duration / len(chunks)
    split_entries = []
    cursor = start_sec
    for index, chunk in enumerate(chunks):
        chunk_start = cursor
        chunk_end = end_sec if index == len(chunks) - 1 else min(end_sec, chunk_start + chunk_duration)
        lines = _wrap_subtitle_lines(chunk, max_chars_per_line=max_chars_per_line, max_lines=max_lines)
        split_entries.append({
            "start_sec": round(chunk_start, 3),
            "end_sec": round(max(chunk_end, chunk_start + 0.15), 3),
            "text": "\n".join(lines),
        })
        cursor = chunk_end
    return split_entries


def _write_timeline_srt(task, entries):
    timeline_dir = task_dir(task.id) / "timelines"
    timeline_dir.mkdir(parents=True, exist_ok=True)
    target = timeline_dir / f"{uuid.uuid4().hex[:8]}_timeline.srt"
    subtitle_entries = []
    for entry in entries:
        subtitle_entries.extend(_split_subtitle_entry(entry, max_chars_per_line=18, max_lines=2))
    normalized_entries = []
    previous_end = 0.0
    for entry in sorted(subtitle_entries, key=lambda item: (float(item.get("start_sec") or 0.0), float(item.get("end_sec") or 0.0))):
        start_sec = max(float(entry.get("start_sec") or 0.0), previous_end + 0.02 if normalized_entries else 0.0)
        end_sec = max(float(entry.get("end_sec") or start_sec + 0.2), start_sec + 0.15)
        normalized_entries.append({
            **entry,
            "start_sec": round(start_sec, 3),
            "end_sec": round(end_sec, 3),
        })
        previous_end = end_sec

    blocks = []
    for index, entry in enumerate(normalized_entries, start=1):
        blocks.append(
            "\n".join([
                str(index),
                f"{_seconds_to_srt_time(entry['start_sec'])} --> {_seconds_to_srt_time(entry['end_sec'])}",
                entry["text"],
            ])
        )
    target.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
    return rel_path(target)


def _segment_script_subtitle_text(segment):
    if not segment:
        return ""
    candidates = [
        getattr(segment, "voice_text", None),
        getattr(segment, "segment_title", None),
    ]
    for candidate in candidates:
        text = re.sub(r"\s+", " ", str(candidate or "")).strip()
        if text:
            return text
    return ""


def _build_script_aligned_subtitle_entries(spans, segments):
    segment_map = {segment.id: segment for segment in segments or []}
    subtitle_entries = []
    stats = {
        "mode": "script_text_with_asr_timing",
        "total_span_count": 0,
        "subtitle_entry_count": 0,
        "script_span_count": 0,
        "asr_context_span_count": 0,
        "empty_span_count": 0,
    }

    for span in sorted(spans or [], key=lambda item: float(item.get("start_sec") or 0.0)):
        stats["total_span_count"] += 1
        start_sec = float(span.get("start_sec") or 0.0)
        end_sec = float(span.get("end_sec") or start_sec)
        if end_sec <= start_sec:
            stats["empty_span_count"] += 1
            continue

        text_source = "asr_context"
        text = re.sub(r"\s+", " ", str(span.get("text") or "")).strip()
        if span.get("type") == "segment":
            script_text = _segment_script_subtitle_text(segment_map.get(span.get("segment_id")))
            if script_text:
                text = script_text
                text_source = "script"

        if not text:
            stats["empty_span_count"] += 1
            continue

        if text_source == "script":
            stats["script_span_count"] += 1
        else:
            stats["asr_context_span_count"] += 1

        subtitle_entries.append({
            "start_sec": round(start_sec, 3),
            "end_sec": round(end_sec, 3),
            "text": text,
            "text_source": text_source,
            "segment_id": span.get("segment_id"),
        })

    stats["subtitle_entry_count"] = len(subtitle_entries)
    return subtitle_entries, stats


def _retime_timeline_entries(entries, target_duration, tolerance=2.0):
    if not entries or not target_duration:
        return entries, None
    source_duration = max(float(entry.get("end_sec") or 0) for entry in entries)
    if source_duration <= 0:
        return entries, None
    target_duration = float(target_duration)
    if abs(source_duration - target_duration) <= tolerance:
        return entries, None

    factor = target_duration / source_duration
    retimed = []
    for entry in entries:
        retimed.append({
            **entry,
            "original_start_sec": entry["start_sec"],
            "original_end_sec": entry["end_sec"],
            "start_sec": round(float(entry["start_sec"]) * factor, 3),
            "end_sec": round(float(entry["end_sec"]) * factor, 3),
        })
    return retimed, {
        "source_duration_sec": source_duration,
        "target_duration_sec": target_duration,
        "scale_factor": factor,
    }


def _build_calibration_windows(duration, sample_seconds=10.0, sample_interval_sec=180.0, max_windows=None):
    duration = float(duration or 0)
    if duration <= 0:
        return []

    sample_seconds = max(min(float(sample_seconds or 10.0), duration), 6.0)
    if duration <= sample_seconds + 2:
        return [(0.0, round(duration, 3))]

    if duration < 180:
        positions = [
            0.0,
            max(0.0, (duration - sample_seconds) / 2),
            max(0.0, duration - sample_seconds),
        ]
    else:
        interval = float(sample_interval_sec or 180.0)
        if duration > 3600 and interval < 300.0:
            interval = 300.0
        positions = [0.0]
        cursor = interval
        while cursor < duration - sample_seconds:
            positions.append(cursor)
            cursor += interval
        positions.append(max(0.0, duration - sample_seconds))

    windows = []
    for pos in positions:
        start = round(max(0.0, pos), 3)
        end = round(min(duration, start + sample_seconds), 3)
        if end - start < 6:
            continue
        if windows and abs(windows[-1][0] - start) < sample_seconds * 0.35:
            continue
        windows.append((start, end))
    return windows[: max_windows or len(windows)]


def _segments_to_text(segments):
    return " ".join(str(item.get("text") or "").strip() for item in (segments or []) if str(item.get("text") or "").strip()).strip()


def _sample_asr_window(audio_path, start_sec, end_sec, model_size="small", initial_prompt=None):
    from services.asr_cache_service import transcribe_with_cache

    return transcribe_with_cache(
        str(audio_path),
        model_size=model_size,
        start_seconds=start_sec,
        end_seconds=end_sec,
        initial_prompt=initial_prompt,
    )


def _collect_prompt_terms(values, limit=36):
    terms = []
    seen = set()

    def add(value):
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        if not text:
            return
        chunks = re.split(r"[，,、/｜|;；：:\n]+", text)
        for chunk in chunks:
            item = chunk.strip(" 《》“”\"'（）()[]【】")
            if not item or len(item) < 2:
                continue
            if len(item) > 32:
                item = item[:32]
            key = item.lower()
            if key in seen:
                continue
            seen.add(key)
            terms.append(item)
            if len(terms) >= limit:
                return

    for value in values:
        if isinstance(value, (list, tuple, set)):
            for item in value:
                add(item)
                if len(terms) >= limit:
                    return terms
        else:
            add(value)
            if len(terms) >= limit:
                return terms
    return terms


def _build_report_video_asr_prompt(task):
    values = []
    planning = (task.result or {}).get("topic_planning") or {}
    active_topic_id = planning.get("active_topic_id")
    topics = planning.get("topics") or []
    active_topic = next((item for item in topics if item.get("topic_id") == active_topic_id), None)
    if active_topic:
        values.extend([
            active_topic.get("topic_name"),
            active_topic.get("topic_category"),
            active_topic.get("recommended_title"),
            active_topic.get("common_views"),
            active_topic.get("conflicting_views"),
        ])
    topic_scripts = planning.get("topic_scripts") or {}
    active_script = topic_scripts.get(active_topic_id) if active_topic_id else None
    if isinstance(active_script, dict):
        values.extend([
            active_script.get("video_title"),
            active_script.get("video_subtitle"),
            active_script.get("topic_core_view"),
            (active_script.get("source_card") or {}).get("main_institutions"),
            (active_script.get("source_card") or {}).get("focus_points"),
        ])
        for section in active_script.get("sections") or []:
            if isinstance(section, dict):
                values.extend([
                    section.get("section_title"),
                    section.get("bullet_points"),
                    section.get("visual_explanation"),
                ])

    for report in task.reports:
        if report.analysis:
            values.extend([
                report.analysis.report_title,
                report.analysis.institution,
                report.analysis.theme,
                report.analysis.core_view,
                report.analysis.key_points,
            ])
        if report.mindmap:
            values.append(report.mindmap.center)
            for node in report.mindmap.nodes or []:
                if isinstance(node, dict):
                    values.append(node.get("label"))
        for segment in report.segments:
            values.append(segment.segment_title)

    terms = _collect_prompt_terms(values, limit=36)
    if not terms:
        return None
    _, user_prompt = render_prompt("asr_calibration", {"terms": "、".join(terms)})
    return user_prompt


def _find_best_timeline_span(entries, sample_text, predicted_start, predicted_end, search_padding=45.0):
    sample_norm = _normalize_timeline_text(sample_text)
    if len(sample_norm) < 4 or not entries:
        return None

    predicted_start = float(predicted_start or 0)
    predicted_end = float(predicted_end or predicted_start + 1)
    predicted_mid = (predicted_start + predicted_end) / 2

    candidate_indexes = [
        index for index, entry in enumerate(entries)
        if float(entry.get("end_sec") or 0) >= predicted_start - search_padding
        and float(entry.get("start_sec") or 0) <= predicted_end + search_padding
    ]
    if not candidate_indexes:
        candidate_indexes = list(range(len(entries)))

    max_span_entries = min(12, len(entries))
    min_chars = max(4, int(len(sample_norm) * 0.45))
    max_chars = max(min_chars + 4, int(len(sample_norm) * 1.85))
    best = None

    for start_idx in candidate_indexes:
        combined_norm = ""
        for end_idx in range(start_idx, min(len(entries), start_idx + max_span_entries)):
            combined_norm += _normalize_timeline_text(entries[end_idx].get("text") or "")
            if len(combined_norm) < min_chars:
                continue
            if len(combined_norm) > max_chars:
                break

            score = SequenceMatcher(None, combined_norm, sample_norm).ratio()
            if combined_norm in sample_norm or sample_norm in combined_norm:
                score += 0.12

            start_sec = float(entries[start_idx].get("start_sec") or 0)
            end_sec = float(entries[end_idx].get("end_sec") or start_sec)
            span_mid = (start_sec + end_sec) / 2
            distance_penalty = min(0.16, abs(span_mid - predicted_mid) / max(120.0, search_padding * 3) * 0.16)
            adjusted_score = score - distance_penalty

            if not best or adjusted_score > best["score"]:
                best = {
                    "start_idx": start_idx,
                    "end_idx": end_idx,
                    "start_sec": start_sec,
                    "end_sec": end_sec,
                    "score": round(adjusted_score, 4),
                    "text": " ".join(str(entries[i].get("text") or "").strip() for i in range(start_idx, end_idx + 1)).strip(),
                }

    if not best or best["score"] < 0.50:
        return None
    return best


def _fit_linear_correction(anchors):
    if not anchors:
        return None

    if len(anchors) == 1:
        anchor = anchors[0]
        return {"slope": 1.0, "intercept": anchor["audio_mid"] - anchor["timeline_mid"]}

    xs = [float(anchor["timeline_mid"]) for anchor in anchors]
    ys = [float(anchor["audio_mid"]) for anchor in anchors]
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    denominator = sum((x - mean_x) ** 2 for x in xs)
    if denominator <= 1e-6:
        return None

    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / denominator
    intercept = mean_y - slope * mean_x
    if not math.isfinite(slope) or not math.isfinite(intercept) or slope <= 0:
        return None
    return {"slope": slope, "intercept": intercept}


def _apply_piecewise_correction(entries, anchors, target_duration=None):
    anchors = sorted(anchors, key=lambda item: float(item["timeline_mid"]))
    if len(anchors) < 2:
        anchor = anchors[0]
        return _apply_linear_correction(entries, 1.0, anchor["audio_mid"] - anchor["timeline_mid"], target_duration=target_duration)

    def map_time(value):
        value = float(value or 0.0)
        if value <= float(anchors[0]["timeline_mid"]):
            left, right = anchors[0], anchors[1]
        elif value >= float(anchors[-1]["timeline_mid"]):
            left, right = anchors[-2], anchors[-1]
        else:
            left = anchors[0]
            right = anchors[1]
            for index in range(len(anchors) - 1):
                candidate_left = anchors[index]
                candidate_right = anchors[index + 1]
                if float(candidate_left["timeline_mid"]) <= value <= float(candidate_right["timeline_mid"]):
                    left, right = candidate_left, candidate_right
                    break

        x1 = float(left["timeline_mid"])
        x2 = float(right["timeline_mid"])
        y1 = float(left["audio_mid"])
        y2 = float(right["audio_mid"])
        if abs(x2 - x1) <= 1e-6:
            return value + (y1 - x1)
        slope = (y2 - y1) / (x2 - x1)
        return y1 + (value - x1) * slope

    corrected = []
    prev_end = 0.0
    for entry in entries:
        start_sec = max(0.0, map_time(entry.get("start_sec")))
        end_sec = max(start_sec + 0.1, map_time(entry.get("end_sec")))
        if start_sec < prev_end:
            start_sec = prev_end
            end_sec = max(start_sec + 0.1, end_sec)
        corrected.append({
            **entry,
            "start_sec": round(start_sec, 3),
            "end_sec": round(end_sec, 3),
        })
        prev_end = end_sec

    if target_duration and corrected:
        last_end = float(corrected[-1]["end_sec"])
        if last_end > 0 and abs(last_end - float(target_duration)) > 1.5:
            factor = float(target_duration) / last_end
            for entry in corrected:
                entry["start_sec"] = round(float(entry["start_sec"]) * factor, 3)
                entry["end_sec"] = round(float(entry["end_sec"]) * factor, 3)
    return corrected


def _apply_linear_correction(entries, slope, intercept, target_duration=None):
    corrected = []
    prev_end = 0.0
    for entry in entries:
        start_sec = float(entry.get("start_sec") or 0) * slope + intercept
        end_sec = float(entry.get("end_sec") or 0) * slope + intercept
        start_sec = max(0.0, start_sec)
        end_sec = max(start_sec + 0.1, end_sec)
        if start_sec < prev_end:
            start_sec = prev_end
            end_sec = max(start_sec + 0.1, end_sec)
        corrected.append({
            **entry,
            "start_sec": round(start_sec, 3),
            "end_sec": round(end_sec, 3),
        })
        prev_end = end_sec

    if target_duration and corrected:
        last_end = float(corrected[-1]["end_sec"])
        if last_end > 0 and abs(last_end - float(target_duration)) > 1.5:
            factor = float(target_duration) / last_end
            prev_end = 0.0
            normalized = []
            for entry in corrected:
                start_sec = max(0.0, float(entry["start_sec"]) * factor)
                end_sec = max(start_sec + 0.1, float(entry["end_sec"]) * factor)
                if start_sec < prev_end:
                    start_sec = prev_end
                    end_sec = max(start_sec + 0.1, end_sec)
                normalized.append({
                    **entry,
                    "start_sec": round(start_sec, 3),
                    "end_sec": round(end_sec, 3),
                })
                prev_end = end_sec
            corrected = normalized
    return corrected


def _smart_retime_timeline_entries(
    entries,
    target_duration,
    audio_path=None,
    tolerance=2.0,
    calibration_mode="smart",
    sample_seconds=10.0,
    sample_interval_sec=180.0,
    min_anchor_confidence=0.75,
    enable_vad=True,
    sample_model="small",
    sampler=None,
    sample_windows=None,
    asr_initial_prompt=None,
):
    base_entries, base_meta = _retime_timeline_entries(entries, target_duration, tolerance=tolerance)
    working_entries = base_entries or entries

    meta = {
        "strategy": "identity",
        "source_duration_sec": max((float(entry.get("end_sec") or 0) for entry in entries), default=0.0),
        "target_duration_sec": float(target_duration or 0),
        "duration_scale": base_meta,
    }
    if base_meta:
        meta["strategy"] = "duration_scale"

    requested_mode = str(calibration_mode or "sampled_asr").strip().lower()
    if requested_mode == "smart":
        requested_mode = "sampled_asr"
    if requested_mode == "sampled":
        requested_mode = "sampled_asr"
    if requested_mode in {"off", "disabled", "none"}:
        meta["calibration_mode"] = calibration_mode
        meta["status"] = "skipped"
        meta["calibration_method"] = "none"
        return working_entries, meta

    if not audio_path or not target_duration or not working_entries:
        meta["calibration_mode"] = requested_mode
        meta["status"] = "skipped"
        meta["calibration_method"] = "none"
        return working_entries, meta

    try:
        windows = sample_windows or _build_calibration_windows(target_duration, sample_seconds=sample_seconds)
        if not windows:
            meta["calibration_mode"] = requested_mode
            meta["reason"] = "no_sample_windows"
            meta["status"] = "fallback"
            meta["calibration_method"] = "duration_scale" if base_meta else "identity"
            return working_entries, meta

        sampler = sampler or _sample_asr_window
        anchors = []
        sample_details = []
        for start_sec, end_sec in windows:
            try:
                segments = sampler(audio_path, start_sec, end_sec, sample_model, asr_initial_prompt)
            except TypeError:
                segments = sampler(audio_path, start_sec, end_sec, sample_model)
            sample_text = _segments_to_text(segments)
            speech_mid = round((start_sec + end_sec) / 2, 3)
            if segments:
                speech_start = start_sec + float(segments[0].get("start") or 0.0)
                speech_end = start_sec + float(segments[-1].get("end") or 0.0)
                if enable_vad and speech_end - speech_start >= 1.0:
                    speech_mid = round((speech_start + speech_end) / 2, 3)
            sample_details.append({
                "start_sec": round(start_sec, 3),
                "end_sec": round(end_sec, 3),
                "speech_mid_sec": speech_mid,
                "text_preview": sample_text[:100],
            })
            if len(_normalize_timeline_text(sample_text)) < 4:
                continue
            best = _find_best_timeline_span(
                working_entries,
                sample_text,
                speech_mid - max(sample_seconds / 2.0, 3.0),
                speech_mid + max(sample_seconds / 2.0, 3.0),
                search_padding=max(sample_interval_sec * 0.55, 60.0),
            )
            if not best:
                continue
            if best["score"] < float(min_anchor_confidence or 0.75):
                continue
            anchors.append({
                "audio_start": round(start_sec, 3),
                "audio_end": round(end_sec, 3),
                "audio_mid": speech_mid,
                "timeline_start_idx": best["start_idx"],
                "timeline_end_idx": best["end_idx"],
                "timeline_start": round(best["start_sec"], 3),
                "timeline_end": round(best["end_sec"], 3),
                "timeline_mid": round((best["start_sec"] + best["end_sec"]) / 2, 3),
                "score": round(float(best["score"]), 4),
                "offset": round(speech_mid - ((best["start_sec"] + best["end_sec"]) / 2), 3),
                "sample_text_preview": sample_text[:120],
                "timeline_text_preview": best["text"][:120],
            })

        meta["calibration_mode"] = requested_mode
        meta["sample_windows"] = [{"start_sec": round(s, 3), "end_sec": round(e, 3)} for s, e in windows]
        meta["sample_count"] = len(windows)
        meta["sample_details"] = sample_details
        meta["sample_interval_sec"] = float(sample_interval_sec or 180.0)
        meta["sample_duration_sec"] = float(sample_seconds or 10.0)
        meta["asr_initial_prompt_preview"] = (asr_initial_prompt or "")[:220]
        meta["min_anchor_confidence"] = float(min_anchor_confidence or 0.75)
        meta["enable_vad"] = bool(enable_vad)
        meta["anchors"] = anchors
        if not anchors:
            meta["reason"] = "no_asr_anchor"
            meta["status"] = "fallback"
            meta["calibration_method"] = "duration_scale" if base_meta else "identity"
            meta["message"] = "字幕已生成，但校准锚点不足，可能存在轻微偏移。"
            return working_entries, meta

        if len(anchors) == 1:
            corrected_entries = _apply_linear_correction(
                working_entries,
                1.0,
                anchors[0]["audio_mid"] - anchors[0]["timeline_mid"],
                target_duration=target_duration,
            )
            meta["calibration_method"] = "shift"
        elif len(anchors) <= 3:
            fit = _fit_linear_correction(anchors)
            if not fit:
                meta["reason"] = "fit_failed"
                meta["status"] = "fallback"
                meta["calibration_method"] = "duration_scale"
                return working_entries, meta
            corrected_entries = _apply_linear_correction(working_entries, fit["slope"], fit["intercept"], target_duration=target_duration)
            meta["calibration_method"] = "linear"
            meta["correction"] = {
                "slope": round(float(fit["slope"]), 6),
                "intercept": round(float(fit["intercept"]), 6),
            }
        else:
            corrected_entries = _apply_piecewise_correction(working_entries, anchors, target_duration=target_duration)
            meta["calibration_method"] = "piecewise_linear"

        meta["strategy"] = "sampled_asr"
        meta["status"] = "calibrated"
        meta["message"] = "字幕已通过抽样 ASR 校准。"
        meta["audio_duration_sec"] = round(float(target_duration or 0.0), 3)
        meta["asr_total_duration_sec"] = round(sum(max(end - start, 0.0) for start, end in windows), 3)
        meta["asr_ratio"] = f"{(meta['asr_total_duration_sec'] / max(float(target_duration or 1.0), 1.0)) * 100:.1f}%"
        meta["anchor_count"] = len(anchors)
        meta["average_offset_sec"] = round(sum(abs(float(item["offset"])) for item in anchors) / max(len(anchors), 1), 3)
        meta["max_offset_sec"] = round(max(abs(float(item["offset"])) for item in anchors), 3)
        meta["confidence_avg"] = round(sum(float(item["score"]) for item in anchors) / max(len(anchors), 1), 4)
        return corrected_entries, meta
    except Exception as exc:
        logger.warning("⚠️ [REPORT_VIDEO] smart retime fallback: %s", exc)
        meta["calibration_mode"] = requested_mode
        meta["warning"] = str(exc)
        meta["status"] = "fallback"
        meta["calibration_method"] = "duration_scale" if base_meta else "identity"
        meta["message"] = "字幕已生成，但抽样校准失败，已回退到估算时间轴。"
        return working_entries, meta


def _subtitle_calibration_report(meta):
    meta = meta or {}
    anchors = meta.get("anchors") or []
    windows = meta.get("sample_windows") or []
    audio_duration = float(meta.get("audio_duration_sec") or meta.get("target_duration_sec") or 0.0)
    asr_total = float(
        meta.get("asr_total_duration_sec")
        if meta.get("asr_total_duration_sec") is not None
        else sum(max(float(item.get("end_sec") or 0) - float(item.get("start_sec") or 0), 0.0) for item in windows)
    )
    anchor_details = []
    for item in anchors:
        start_idx = item.get("timeline_start_idx")
        end_idx = item.get("timeline_end_idx")
        if start_idx is not None and end_idx is not None:
            matched_sentence_id = f"timeline_{int(start_idx) + 1:03d}_{int(end_idx) + 1:03d}"
        else:
            matched_sentence_id = item.get("matched_sentence_id") or ""
        anchor_details.append({
            "sample_start": round(float(item.get("audio_start") or 0.0), 3),
            "sample_end": round(float(item.get("audio_end") or 0.0), 3),
            "matched_sentence_id": matched_sentence_id,
            "estimated_time": round(float(item.get("timeline_mid") or 0.0), 3),
            "actual_time": round(float(item.get("audio_mid") or 0.0), 3),
            "offset": round(float(item.get("offset") or 0.0), 3),
            "confidence": round(float(item.get("score") or 0.0), 4),
            "timeline_text_preview": item.get("timeline_text_preview") or "",
            "sample_text_preview": item.get("sample_text_preview") or "",
        })

    fallback_reason = (
        meta.get("reason")
        or meta.get("warning")
        or ("calibrated" if meta.get("status") == "calibrated" else None)
    )
    report = {
        "subtitle_calibration_mode": meta.get("calibration_mode") or meta.get("mode") or "sampled_asr",
        "audio_duration_sec": round(audio_duration, 3),
        "sample_count": int(meta.get("sample_count") or len(windows) or 0),
        "asr_total_duration_sec": round(asr_total, 3),
        "anchor_count": int(meta.get("anchor_count") if meta.get("anchor_count") is not None else len(anchors)),
        "confidence_avg": round(float(meta.get("confidence_avg") or 0.0), 4),
        "average_offset_sec": round(float(meta.get("average_offset_sec") or 0.0), 3),
        "max_offset_sec": round(float(meta.get("max_offset_sec") or 0.0), 3),
        "calibration_method": meta.get("calibration_method") or meta.get("strategy") or "none",
        "status": meta.get("status") or "unknown",
        "fallback_reason": fallback_reason if meta.get("status") != "calibrated" else None,
        "message": meta.get("message") or "",
        "subtitle_source": meta.get("subtitle_source") or meta.get("mode") or "",
        "audio_speed": meta.get("audio_speed") or {},
        "timeline_speed_adjustment": meta.get("timeline_speed_adjustment") or {},
        "asr_cache": ((meta.get("timeline_generation") or {}).get("asr_cache") or meta.get("asr_cache") or {}),
        "anchor_details": anchor_details,
        "segment_mapping": meta.get("segment_mapping") or {},
    }
    return report


def _timeline_row_score(row_text, segment_text):
    row_norm = _normalize_timeline_text(row_text)
    segment_norm = _normalize_timeline_text(segment_text)
    if not row_norm or not segment_norm:
        return 0
    if row_norm in segment_norm:
        return min(1.0, len(row_norm) / max(len(segment_norm), 1) + 0.35)
    return SequenceMatcher(None, row_norm, segment_norm).ratio()


def _timeline_window_score(window_norm, segment_norm):
    if not window_norm or not segment_norm:
        return {"score": 0.0, "ratio": 0.0, "coverage": 0.0, "length_ratio": 0.0}
    length_ratio = len(window_norm) / max(len(segment_norm), 1)
    coverage = min(length_ratio, 1.0)
    ratio = SequenceMatcher(None, window_norm, segment_norm).ratio()
    contains_bonus = 0.0
    if window_norm in segment_norm or segment_norm in window_norm:
        contains_bonus = 0.08
    short_penalty = max(0.35 - coverage, 0.0) * 0.55
    overrun_penalty = max(length_ratio - 1.35, 0.0) * 0.35
    score = (ratio * 0.68) + (coverage * 0.32) + contains_bonus - short_penalty - overrun_penalty
    return {
        "score": max(0.0, min(1.0, score)),
        "ratio": ratio,
        "coverage": coverage,
        "length_ratio": length_ratio,
    }


def _find_timeline_window(timeline_entries, cursor, segment_text):
    segment_norm = _normalize_timeline_text(segment_text)
    if not segment_norm:
        return None

    entry_count = len(timeline_entries)
    max_start = min(entry_count, cursor + 36)
    max_entries = 72
    best = None

    for start in range(cursor, max_start):
        combined_norm = ""
        combined_text = []
        for end in range(start, min(entry_count, start + max_entries)):
            entry = timeline_entries[end]
            entry_norm = _normalize_timeline_text(entry.get("text") or "")
            if not entry_norm:
                continue
            combined_norm += entry_norm
            combined_text.append(entry.get("text") or "")
            metrics = _timeline_window_score(combined_norm, segment_norm)
            # Prefer nearby starts, but allow skipping true intro/source context before the next segment.
            start_penalty = min(max(start - cursor, 0), 36) * 0.002
            final_score = metrics["score"] - start_penalty
            if best is None or final_score > best["score"]:
                best = {
                    "start": start,
                    "end": end,
                    "score": final_score,
                    "raw_score": metrics["score"],
                    "ratio": metrics["ratio"],
                    "coverage": metrics["coverage"],
                    "length_ratio": metrics["length_ratio"],
                    "text": " ".join(combined_text),
                }
            if metrics["length_ratio"] > 1.65:
                break

    if not best:
        return None
    if best["score"] < 0.42 and not (best["raw_score"] >= 0.58 and best["coverage"] >= 0.30):
        return None
    if best["coverage"] < 0.22 and best["raw_score"] < 0.70:
        return None
    return best


def _apply_full_audio_timeline(task, audio, timeline_entries, timeline_rel_path=None, subtitle_rel_path=None, retime_meta=None):
    segments = _task_segments(task)
    if not segments:
        audio.match_status = "unmatched"
        audio.error_message = "当前任务还没有 segment，请先生成 AI 文案"
        return {"matched": 0, "unmatched": len(timeline_entries), "spans": []}
    if not timeline_entries:
        audio.match_status = "unmatched"
        audio.error_message = "没有解析到有效时间轴"
        return {"matched": 0, "unmatched": 0, "spans": []}

    cursor = 0
    spans = []
    matched_count = 0
    match_details = []

    for segment_index, segment in enumerate(segments):
        segment_text = segment.voice_text or segment.segment_title or ""
        window = _find_timeline_window(timeline_entries, cursor, segment_text)
        if not window:
            match_details.append({
                "segment_index": segment_index,
                "segment_id": segment.id,
                "segment_key": segment.segment_id,
                "status": "unmatched",
                "cursor": cursor,
                "segment_text_preview": segment_text[:80],
            })
            continue

        for index in range(cursor, window["start"]):
            entry = timeline_entries[index]
            spans.append({
                "type": "context",
                "start_sec": entry["start_sec"],
                "end_sec": entry["end_sec"],
                "text": entry["text"],
            })

        selected = timeline_entries[window["start"]:window["end"] + 1]

        start_sec = selected[0]["start_sec"]
        end_sec = selected[-1]["end_sec"]
        duration = max(end_sec - start_sec, 1.0)
        segment.actual_duration_sec = duration
        segment.audio_filename = audio.file_name
        segment.audio_path = audio.file_path
        segment.status = "audio_ready"
        raw_payload = dict(segment.raw_payload or {})
        raw_payload["timeline"] = {
            "mode": "full_audio_srt",
            "start_sec": start_sec,
            "end_sec": end_sec,
            "duration_sec": duration,
            "text": " ".join(item["text"] for item in selected),
            "match_score": round(float(window["raw_score"]), 4),
            "match_ratio": round(float(window["ratio"]), 4),
            "match_coverage": round(float(window["coverage"]), 4),
        }
        segment.raw_payload = raw_payload
        spans.append({
            "type": "segment",
            "segment_id": segment.id,
            "segment_key": segment.segment_id,
            "report_id": segment.report_id,
            "start_sec": start_sec,
            "end_sec": end_sec,
            "text": raw_payload["timeline"]["text"],
        })
        matched_count += 1
        match_details.append({
            "segment_index": segment_index,
            "segment_id": segment.id,
            "segment_key": segment.segment_id,
            "segment_title": segment.segment_title,
            "status": "matched",
            "start_sec": round(float(start_sec), 3),
            "end_sec": round(float(end_sec), 3),
            "start_index": window["start"],
            "end_index": window["end"],
            "entry_count": len(selected),
            "score": round(float(window["raw_score"]), 4),
            "ratio": round(float(window["ratio"]), 4),
            "coverage": round(float(window["coverage"]), 4),
            "matched_text_preview": raw_payload["timeline"]["text"][:120],
        })
        cursor = window["end"] + 1

    for index in range(cursor, len(timeline_entries)):
        entry = timeline_entries[index]
        spans.append({
            "type": "context",
            "start_sec": entry["start_sec"],
            "end_sec": entry["end_sec"],
            "text": entry["text"],
        })

    audio.segment_id = "__full_timeline__"
    audio.match_status = "timeline_matched" if matched_count else "unmatched"
    audio.error_message = None if matched_count else "时间轴没有匹配到任何 segment"

    if isinstance(retime_meta, dict):
        retime_meta = dict(retime_meta)
        retime_meta["segment_mapping"] = {
            "segment_count": len(segments),
            "matched_segment_count": matched_count,
            "unmatched_segment_count": max(len(segments) - matched_count, 0),
            "status": "matched" if matched_count == len(segments) else ("partial" if matched_count else "unmatched"),
            "details": match_details,
        }
        if matched_count != len(segments):
            retime_meta["status"] = "partial" if matched_count else "fallback"
            retime_meta["reason"] = "segment_mapping_incomplete"

    subtitle_text_meta = None
    if matched_count:
        subtitle_entries, subtitle_text_meta = _build_script_aligned_subtitle_entries(spans, segments)
        if subtitle_entries:
            subtitle_rel_path = _write_timeline_srt(task, subtitle_entries)
            if isinstance(retime_meta, dict):
                retime_meta["subtitle_source"] = "full_audio_asr_timing_script_text"
                retime_meta["subtitle_text_generation"] = subtitle_text_meta

    calibration_report = _subtitle_calibration_report(retime_meta)
    try:
        logger.info(
            "[REPORT_VIDEO_SUBTITLE_CALIBRATION] task_id=%s audio_id=%s report=%s",
            task.id,
            audio.id,
            json.dumps(calibration_report, ensure_ascii=False),
        )
        for anchor in calibration_report.get("anchor_details") or []:
            logger.info(
                "[REPORT_VIDEO_SUBTITLE_ANCHOR] task_id=%s audio_id=%s anchor=%s",
                task.id,
                audio.id,
                json.dumps(anchor, ensure_ascii=False),
            )
        for item in calibration_report.get("segment_mapping", {}).get("details") or []:
            logger.info(
                "[REPORT_VIDEO_SUBTITLE_SEGMENT_MATCH] task_id=%s audio_id=%s match=%s",
                task.id,
                audio.id,
                json.dumps(item, ensure_ascii=False),
            )
    except Exception:
        logger.exception("[REPORT_VIDEO_SUBTITLE_CALIBRATION] failed to log calibration report")

    result = dict(task.result or {})
    result["audio_mode"] = "full_audio_srt"
    result["full_audio"] = {
        "audio_id": audio.id,
        "file_name": audio.file_name,
        "file_path": audio.file_path,
        "duration_sec": audio.duration_sec,
        "timeline_path": timeline_rel_path,
        "subtitle_path": subtitle_rel_path,
        "timeline_entry_count": len(timeline_entries),
        "matched_segment_count": matched_count,
        "retime": retime_meta,
        "subtitle_calibration": calibration_report,
        "subtitle_source": calibration_report.get("subtitle_source") or "uploaded_or_estimated",
        "subtitle_text_generation": subtitle_text_meta,
    }
    result["subtitle_calibration"] = calibration_report
    result["timeline_spans"] = sorted(spans, key=lambda item: item["start_sec"])
    task.result = result
    return {
        "matched": matched_count,
        "unmatched": max(len(segments) - matched_count, 0),
        "spans": result["timeline_spans"],
        "retime": retime_meta,
        "subtitle_calibration": calibration_report,
    }


def _match_audio_to_segments(task, audio_rows):
    segments = _task_segments(task)
    if not segments:
        for audio in audio_rows:
            audio.match_status = "unmatched"
            audio.error_message = "当前任务还没有 segment，请先生成 AI 文案"
        return

    matched_segment_ids = set()
    for audio in audio_rows:
        name = (audio.file_name or "").lower()
        matched = None
        for segment in segments:
            if segment.id in matched_segment_ids:
                continue
            candidates = [
                (segment.segment_id or "").lower(),
                f"seg_{segment.segment_index:03d}",
                f"{segment.report.report_code}_seg_{segment.segment_index:03d}" if segment.report else "",
            ]
            if any(candidate and candidate in name for candidate in candidates):
                matched = segment
                break
        if not matched and len(audio_rows) == len(segments):
            index = audio_rows.index(audio)
            matched = segments[index] if index < len(segments) else None
        if not matched:
            audio.match_status = "unmatched"
            audio.error_message = "未从文件名匹配到 segment_id"
            continue

        matched_segment_ids.add(matched.id)
        audio.report_id = matched.report_id
        audio.segment_id = matched.segment_id
        audio.match_status = "matched"
        audio.error_message = None
        matched.audio_filename = audio.file_name
        matched.audio_path = audio.file_path
        matched.actual_duration_sec = audio.duration_sec
        matched.status = "audio_ready"


@bp.route("/tasks", methods=["GET"])
def list_tasks():
    tasks = (
        ReportVideoTask.query
        .order_by(ReportVideoTask.created_at.desc())
        .limit(100)
        .all()
    )
    return jsonify({
        "success": True,
        "data": [task.to_dict(include_reports=True, include_ai=True) for task in tasks],
    })


@bp.route("/tasks", methods=["POST"])
def create_task():
    data = request.get_json(silent=True) or {}
    task_name = (data.get("task_name") or "").strip()
    if not task_name:
        return jsonify({"success": False, "error": "任务名称不能为空"}), 400

    task = ReportVideoTask(
        task_name=task_name,
        video_type=data.get("video_type") or "daily_digest",
        default_segment_count=int(data.get("default_segment_count") or 4),
        output_ratios=data.get("output_ratios") or ["16:9", "9:16"],
        style=data.get("style") or "professional_finance",
        background_music_path=data.get("background_music_path"),
        generate_subtitles=bool(data.get("generate_subtitles", True)),
        status="created",
    )
    db.session.add(task)
    db.session.commit()
    return jsonify({"success": True, "data": task.to_dict(include_reports=True, include_ai=True)})


@bp.route("/ai-prompts", methods=["GET"])
def get_ai_prompts():
    return jsonify({"success": True, "data": list_prompt_templates()})


@bp.route("/ai-prompts/<prompt_id>", methods=["PUT"])
def update_ai_prompt(prompt_id):
    data = request.get_json(silent=True) or {}
    try:
        prompt = save_prompt_template(
            prompt_id,
            system_prompt=data.get("system_prompt"),
            user_prompt=data.get("user_prompt"),
        )
    except KeyError:
        return jsonify({"success": False, "error": "Prompt 不存在"}), 404
    return jsonify({"success": True, "data": prompt})


@bp.route("/ai-prompts/<prompt_id>/reset", methods=["POST"])
def reset_ai_prompt(prompt_id):
    try:
        prompt = reset_prompt_template(prompt_id)
    except KeyError:
        return jsonify({"success": False, "error": "Prompt 不存在"}), 404
    return jsonify({"success": True, "data": prompt})


@bp.route("/auto-scan", methods=["GET"])
def get_auto_scan():
    config = load_auto_scan_config()
    preview = auto_scan_preview(config)
    return jsonify({"success": True, "data": {"config": config, "preview": preview}})


@bp.route("/auto-scan/config", methods=["PUT"])
def update_auto_scan_config():
    data = request.get_json(silent=True) or {}
    config = save_auto_scan_config(data)
    return jsonify({"success": True, "data": {"config": config, "preview": auto_scan_preview(config)}})


@bp.route("/auto-scan/preview", methods=["POST"])
def preview_auto_scan():
    data = request.get_json(silent=True) or {}
    config = load_auto_scan_config()
    if isinstance(data.get("config"), dict):
        config.update(data["config"])
        config = load_auto_scan_config_from_dict(config)
    return jsonify({"success": True, "data": auto_scan_preview(config)})


@bp.route("/auto-scan/run", methods=["POST"])
def run_auto_scan():
    data = request.get_json(silent=True) or {}
    config_override = data.get("config") if isinstance(data.get("config"), dict) else None
    dry_run = bool(data.get("dry_run", False))
    trigger = data.get("trigger") or "manual"

    if dry_run:
        config = load_auto_scan_config()
        if config_override:
            config.update(config_override)
            config = load_auto_scan_config_from_dict(config)
        return jsonify({"success": True, "data": {"queued": False, "dry_run": True, "preview": auto_scan_preview(config)}})

    from tasks.report_video import scan_report_video_folder_task

    celery_task = _enqueue_report_video_task(scan_report_video_folder_task, config_override, False, trigger)
    return jsonify({
        "success": True,
        "data": {
            "queued": True,
            "celery_id": celery_task.id,
        },
    })


@bp.route("/auto-scan/scheduler/status", methods=["GET"])
def auto_scan_scheduler_status():
    from services.report_video_auto_scheduler_service import scheduler_status

    return jsonify({"success": True, "data": {"scheduler": scheduler_status()}})


@bp.route("/auto-scan/scheduler/start", methods=["POST"])
def auto_scan_scheduler_start():
    from services.report_video_auto_scheduler_service import start_scheduler

    result = start_scheduler()
    return jsonify({"success": True, "data": {"scheduler": result}})


@bp.route("/auto-scan/scheduler/stop", methods=["POST"])
def auto_scan_scheduler_stop():
    from services.report_video_auto_scheduler_service import stop_scheduler

    result = stop_scheduler()
    return jsonify({"success": True, "data": {"scheduler": result}})


@bp.route("/auto-scan/scheduler/restart", methods=["POST"])
def auto_scan_scheduler_restart():
    from services.report_video_auto_scheduler_service import restart_scheduler

    result = restart_scheduler()
    return jsonify({"success": True, "data": {"scheduler": result}})


@bp.route("/auto-scan/scheduler/run-once", methods=["POST"])
def auto_scan_scheduler_run_once():
    from services.report_video_auto_scheduler_service import trigger_once

    try:
        result = trigger_once()
    except RuntimeError as exc:
        return jsonify({"success": False, "error": str(exc)}), 500
    return jsonify({"success": True, "data": {"scheduler": result}})


@bp.route("/auto-scan/scheduler/logs", methods=["GET"])
def auto_scan_scheduler_logs():
    from services.report_video_auto_scheduler_service import scheduler_log_tail

    lines = request.args.get("lines", 120, type=int)
    parsed_lines = min(500, max(20, int(lines or 120)))
    return jsonify({
        "success": True,
        "data": {
            "lines": scheduler_log_tail(lines=parsed_lines),
            "line_count": parsed_lines,
        },
    })


@bp.route("/asset-scan/preview", methods=["POST"])
def preview_asset_scan():
    data = request.get_json(silent=True) or {}
    try:
        from services.report_video_asset_service import preview_zsxq_report_assets

        preview = preview_zsxq_report_assets(data)
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    return jsonify({"success": True, "data": preview})


@bp.route("/asset-scan/run", methods=["POST"])
def run_asset_scan():
    data = request.get_json(silent=True) or {}
    dry_run = bool(data.get("dry_run", False))
    if dry_run:
        try:
            from services.report_video_asset_service import preview_zsxq_report_assets

            preview = preview_zsxq_report_assets(data)
        except ValueError as exc:
            return jsonify({"success": False, "error": str(exc)}), 400
        return jsonify({"success": True, "data": {"queued": False, "dry_run": True, "preview": preview}})

    from tasks.report_video import scan_zsxq_report_assets_task

    celery_task = _enqueue_report_video_task(scan_zsxq_report_assets_task, data, False)
    return jsonify({
        "success": True,
        "data": {
            "queued": True,
            "celery_id": celery_task.id,
        },
    })


@bp.route("/asset-scan/scheduler/config", methods=["GET", "POST"])
def asset_scan_scheduler_config():
    from services.report_video_asset_scheduler_service import (
        load_config as load_asset_scheduler_config,
        save_config as save_asset_scheduler_config,
    )

    if request.method == "GET":
        return jsonify({"success": True, "data": {"config": load_asset_scheduler_config()}})
    data = request.get_json(silent=True) or {}
    config = save_asset_scheduler_config(data)
    return jsonify({"success": True, "message": "研报资产补漏计划配置已保存", "data": {"config": config}})


@bp.route("/asset-scan/scheduler/status", methods=["GET"])
def asset_scan_scheduler_status():
    from services.report_video_asset_scheduler_service import scheduler_status

    return jsonify({"success": True, "data": {"scheduler": scheduler_status()}})


@bp.route("/asset-scan/scheduler/start", methods=["POST"])
def asset_scan_scheduler_start():
    from services.report_video_asset_scheduler_service import start_scheduler

    result = start_scheduler()
    return jsonify({"success": True, "data": {"scheduler": result}})


@bp.route("/asset-scan/scheduler/stop", methods=["POST"])
def asset_scan_scheduler_stop():
    from services.report_video_asset_scheduler_service import stop_scheduler

    result = stop_scheduler()
    return jsonify({"success": True, "data": {"scheduler": result}})


@bp.route("/asset-scan/scheduler/restart", methods=["POST"])
def asset_scan_scheduler_restart():
    from services.report_video_asset_scheduler_service import restart_scheduler

    result = restart_scheduler()
    return jsonify({"success": True, "data": {"scheduler": result}})


@bp.route("/asset-scan/scheduler/run-once", methods=["POST"])
def asset_scan_scheduler_run_once():
    from services.report_video_asset_scheduler_service import trigger_once

    data = request.get_json(silent=True) or {}
    try:
        result = trigger_once(target_date=data.get("target_date"))
    except RuntimeError as exc:
        return jsonify({"success": False, "error": str(exc)}), 500
    return jsonify({"success": True, "data": {"scheduler": result}})


@bp.route("/asset-scan/scheduler/logs", methods=["GET"])
def asset_scan_scheduler_logs():
    from services.report_video_asset_scheduler_service import scheduler_log_tail

    lines = request.args.get("lines", 120, type=int)
    parsed_lines = min(500, max(20, int(lines or 120)))
    return jsonify({
        "success": True,
        "data": {
            "lines": scheduler_log_tail(lines=parsed_lines),
            "line_count": parsed_lines,
        },
    })


@bp.route("/tasks/<task_id>", methods=["GET"])
def get_task(task_id):
    task, error = _task_or_404(task_id)
    if error:
        return error
    return jsonify({"success": True, "data": task.to_dict(include_reports=True)})


@bp.route("/tasks/<task_id>", methods=["DELETE"])
def delete_task(task_id):
    task, error = _task_or_404(task_id)
    if error:
        return error
    data_dir = task_dir(task.id)
    db.session.delete(task)
    db.session.commit()
    shutil.rmtree(data_dir, ignore_errors=True)
    return jsonify({"success": True, "message": "Task deleted"})


@bp.route("/tasks/<task_id>/reports", methods=["POST"])
def upload_reports(task_id):
    task, error = _task_or_404(task_id)
    if error:
        return error

    files = request.files.getlist("files")
    if not files and request.files.get("file"):
        files = [request.files["file"]]
    files = [file for file in files if file and file.filename]
    if not files:
        return jsonify({"success": False, "error": "请上传 PDF 文件"}), 400

    created_reports = []
    failed = []
    start_index = _next_report_index(task)

    for offset, file in enumerate(files):
        filename = file.filename or ""
        if not filename.lower().endswith(".pdf"):
            failed.append({"file_name": filename, "error": "仅支持 PDF 文件"})
            continue

        code = report_code(start_index + offset)
        try:
            saved = save_report_pdf_file(task.id, code, file)

            report = ReportVideoReport(
                task_id=task.id,
                report_code=code,
                file_name=saved["file_name"],
                file_path=saved["file_path"],
                page_count=None,
                raw_text_chars=0,
                cleaned_text_chars=0,
                is_scanned=False,
                parse_status="parsing",
                ai_status="ai_pending",
                error_message=None,
                raw_meta={
                    "queued_at": datetime.utcnow().isoformat(),
                    "async": True,
                },
            )
            db.session.add(report)
            created_reports.append(report)
        except Exception as exc:
            failed.append({"file_name": filename, "error": str(exc)})

    db.session.flush()
    task.status = "parsing" if created_reports else _task_status_from_reports(task)
    task.updated_at = datetime.utcnow()
    db.session.commit()

    if created_reports:
        from tasks.report_video import parse_report_pdf_task

        for report in created_reports:
            _enqueue_report_video_task(parse_report_pdf_task, report.id)

    return jsonify({
        "success": True,
        "data": {
            "task": task.to_dict(include_reports=True, include_ai=True),
            "reports": [report.to_dict() for report in created_reports],
            "failed": failed,
        },
    })


@bp.route("/reports/<report_id>/text", methods=["GET"])
def get_report_text(report_id):
    report, error = _report_or_404(report_id)
    if error:
        return error

    text_type = request.args.get("type") or "cleaned"
    if text_type == "raw":
        path = report.raw_text_path
    else:
        path = report.cleaned_text_path

    try:
        text = read_task_text_file(path)
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 403

    return jsonify({
        "success": True,
        "data": {
            "report": report.to_dict(),
            "type": text_type,
            "text": text,
        },
    })


@bp.route("/reports/<report_id>", methods=["DELETE"])
def delete_report(report_id):
    report, error = _report_or_404(report_id)
    if error:
        return error

    task = report.task
    data_dir = report_dir(report.task_id, report.report_code)
    db.session.delete(report)
    db.session.flush()

    remaining_reports = (
        ReportVideoReport.query
        .filter(ReportVideoReport.task_id == task.id)
        .order_by(ReportVideoReport.report_code.asc())
        .all()
    )
    for index, item in enumerate(remaining_reports, start=1):
        item.report_code = report_code(index)

    task.status = _task_status_from_reports(task)
    task.updated_at = datetime.utcnow()
    db.session.commit()
    shutil.rmtree(data_dir, ignore_errors=True)

    return jsonify({
        "success": True,
        "data": task.to_dict(include_reports=True, include_ai=True),
    })


@bp.route("/tasks/<task_id>/reports/reorder", methods=["POST"])
def reorder_reports(task_id):
    task, error = _task_or_404(task_id)
    if error:
        return error

    data = request.get_json(silent=True) or {}
    ordered_ids = data.get("report_ids") or []
    if not isinstance(ordered_ids, list) or not ordered_ids:
        return jsonify({"success": False, "error": "report_ids 不能为空"}), 400

    reports = list(task.reports)
    report_map = {report.id: report for report in reports}
    if set(ordered_ids) != set(report_map.keys()):
        return jsonify({"success": False, "error": "report_ids 必须包含当前任务的全部研报"}), 400

    # Avoid the task_id/report_code unique constraint while renumbering.
    for index, report_id in enumerate(ordered_ids, start=1):
        report_map[report_id].report_code = f"tmp{index:05d}"
    db.session.flush()

    for index, report_id in enumerate(ordered_ids, start=1):
        report_map[report_id].report_code = report_code(index)

    task.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify({
        "success": True,
        "data": task.to_dict(include_reports=True, include_ai=True),
    })


@bp.route("/tasks/<task_id>/ai-scripts", methods=["POST"])
def generate_task_ai_scripts(task_id):
    task, error = _task_or_404(task_id)
    if error:
        return error

    data = request.get_json(silent=True) or {}
    segment_count = int(data.get("segment_count") or task.default_segment_count or 4)
    segment_count = min(max(segment_count, 3), 6)
    parsed_reports = [report for report in task.reports if report.parse_status == "parsed"]
    if not parsed_reports:
        return jsonify({"success": False, "error": "当前任务没有可生成文案的解析成功研报"}), 400

    from tasks.report_video import generate_ai_scripts_task

    for report in parsed_reports:
        report.ai_status = "ai_generating"
        report.error_message = None

    task.status = "ai_generating"
    task.updated_at = datetime.utcnow()
    db.session.commit()
    celery_task = _enqueue_report_video_task(generate_ai_scripts_task, task.id, segment_count)

    return jsonify({
        "success": True,
        "data": {
            "task": task.to_dict(include_reports=True, include_ai=True),
            "queued": True,
            "celery_id": celery_task.id,
            "generated": [],
            "failed": [],
        },
    })


@bp.route("/tasks/<task_id>/topic-planning", methods=["GET"])
def get_topic_planning(task_id):
    task, error = _task_or_404(task_id)
    if error:
        return error
    return jsonify({"success": True, "data": _topic_planning_payload(task)})


@bp.route("/tasks/<task_id>/topic-planning/cards", methods=["POST"])
def generate_topic_cards(task_id):
    task, error = _task_or_404(task_id)
    if error:
        return error
    if not task.reports:
        return jsonify({"success": False, "error": "当前任务还没有研报，不能生成主题摘要卡"}), 400

    from tasks.report_video import generate_topic_cards_task

    task.status = "topic_cards_generating"
    _set_topic_planning_status(task, "cards_queued")
    db.session.commit()
    celery_task = _enqueue_report_video_task(generate_topic_cards_task, task.id)
    return jsonify({
        "success": True,
        "data": {
            "task": task.to_dict(include_reports=True, include_ai=True),
            "queued": True,
            "celery_id": celery_task.id,
            "topic_planning": _topic_planning_payload(task),
        },
    })


@bp.route("/tasks/<task_id>/topic-planning/clusters", methods=["POST"])
def generate_topic_clusters_route(task_id):
    task, error = _task_or_404(task_id)
    if error:
        return error
    planning = _topic_planning_payload(task)
    if not task.reports and not planning["report_cards"]:
        return jsonify({"success": False, "error": "当前任务没有可聚类的研报摘要卡"}), 400

    from tasks.report_video import generate_topic_clusters_task

    task.status = "topic_clustering"
    _set_topic_planning_status(task, "cluster_queued")
    db.session.commit()
    celery_task = _enqueue_report_video_task(generate_topic_clusters_task, task.id)
    return jsonify({
        "success": True,
        "data": {
            "task": task.to_dict(include_reports=True, include_ai=True),
            "queued": True,
            "celery_id": celery_task.id,
            "topic_planning": _topic_planning_payload(task),
        },
    })


@bp.route("/tasks/<task_id>/topics/<topic_id>/script", methods=["POST"])
def generate_topic_script_route(task_id, topic_id):
    task, error = _task_or_404(task_id)
    if error:
        return error
    planning = _topic_planning_payload(task)
    topics = planning["topics"]
    if not any(topic.get("topic_id") == topic_id for topic in topics):
        return jsonify({"success": False, "error": "主题不存在，请先生成主题榜"}), 400

    from tasks.report_video import generate_topic_video_script_task

    task.status = "topic_script_generating"
    _set_topic_planning_status(task, "topic_script_queued", active_topic_id=topic_id)
    db.session.commit()
    celery_task = _enqueue_report_video_task(generate_topic_video_script_task, task.id, topic_id)
    return jsonify({
        "success": True,
        "data": {
            "task": task.to_dict(include_reports=True, include_ai=True),
            "queued": True,
            "celery_id": celery_task.id,
            "topic_planning": _topic_planning_payload(task),
        },
    })


def _topic_script_report(task, topic, script):
    report_cards = ((_topic_planning_payload(task).get("report_cards")) or [])
    report_codes = []
    for section in script.get("sections") or []:
        report_codes.extend(section.get("source_report_ids") or [])
    report_codes.extend(topic.get("reports") or [])
    for report_code in report_codes:
        report = next((item for item in task.reports if item.report_code == str(report_code)), None)
        if report:
            return report

    for card in report_cards:
        if card.get("db_report_id"):
            report = next((item for item in task.reports if item.id == card.get("db_report_id")), None)
            if report:
                return report
    return task.reports[0] if task.reports else None


def _apply_topic_script_to_task(task, topic_id):
    planning = _topic_planning_payload(task)
    topics = planning["topics"]
    topic = next((item for item in topics if item.get("topic_id") == topic_id), None)
    script = (planning.get("topic_scripts") or {}).get(topic_id)
    if not topic:
        raise ValueError("主题不存在，请先生成主题榜")
    if not script:
        raise ValueError("主题脚本不存在，请先生成主题脚本")

    report = _topic_script_report(task, topic, script)
    if not report:
        raise ValueError("当前任务没有可承载主题脚本的研报")

    report_ids = [item.id for item in task.reports]
    if report_ids:
        ReportVideoSegment.query.filter(ReportVideoSegment.report_id.in_(report_ids)).delete(synchronize_session=False)
        ReportVideoMindmap.query.filter(ReportVideoMindmap.report_id.in_(report_ids)).delete(synchronize_session=False)
        ReportVideoAnalysis.query.filter(ReportVideoAnalysis.report_id.in_(report_ids)).delete(synchronize_session=False)
    db.session.flush()

    sections = list(script.get("sections") or [])
    source_card = script.get("source_card") or {}
    analysis = ReportVideoAnalysis(
        report_id=report.id,
        report_title=script.get("video_title") or topic.get("recommended_title") or topic.get("topic_name"),
        institution=source_card.get("institution_summary") or "多机构研报",
        theme=topic.get("topic_name") or topic.get("topic_category"),
        core_view=script.get("topic_core_view") or topic.get("topic_summary") or script.get("opening_text"),
        key_points=topic.get("common_views") or source_card.get("focus_points") or [],
        video_value_score=int(float(topic.get("final_score") or topic.get("internal_heat_score") or 0)),
        recommended_video_type="topic_mix",
        recommended_duration_sec=sum(int(section.get("estimated_duration_sec") or 30) for section in sections) or None,
        reason=topic.get("why_it_is_worth_making") or topic.get("recommended_video_angle"),
        raw_payload={
            **script,
            "applied_from_topic_id": topic_id,
            "topic": topic,
            "provider": script.get("provider"),
        },
    )
    db.session.add(analysis)

    mindmap = script.get("theme_mindmap") or {}
    db.session.add(ReportVideoMindmap(
        report_id=report.id,
        center=mindmap.get("center") or script.get("video_title") or topic.get("topic_name"),
        nodes=mindmap.get("nodes") or [],
        raw_payload=mindmap,
    ))

    for index, section in enumerate(sections, start=1):
        segment = ReportVideoSegment(
            task_id=task.id,
            report_id=report.id,
            segment_id=f"topic_{topic_id}_{section.get('section_id') or f'S{index:03d}'}"[:32],
            segment_index=index,
            segment_type=section.get("section_type") or "topic_section",
            segment_title=section.get("section_title") or f"主题段落 {index}",
            voice_text=section.get("voice_text") or "",
            estimated_duration_sec=section.get("estimated_duration_sec") or 30,
            focus_node_id=section.get("focus_node_id") or f"node_{index:03d}",
            status="script_ready",
            raw_payload={
                **section,
                "topic_id": topic_id,
                "topic_name": topic.get("topic_name"),
                "video_title": script.get("video_title"),
                "opening_text": script.get("opening_text") if index == 1 else None,
            },
        )
        db.session.add(segment)

    for item in task.reports:
        item.ai_status = "script_ready" if item.id == report.id else "ai_pending"
        item.error_message = None
        item.updated_at = datetime.utcnow()
    task.status = "script_ready"
    result = dict(task.result or {})
    planning_result = dict(result.get("topic_planning") or {})
    planning_result["status"] = "topic_script_applied"
    planning_result["active_topic_id"] = topic_id
    planning_result["applied_topic_id"] = topic_id
    planning_result["applied_report_id"] = report.id
    planning_result["applied_at"] = datetime.utcnow().isoformat()
    result["topic_planning"] = planning_result
    task.result = result
    task.updated_at = datetime.utcnow()
    return report


@bp.route("/tasks/<task_id>/topics/<topic_id>/apply-script", methods=["POST"])
def apply_topic_script_route(task_id, topic_id):
    task, error = _task_or_404(task_id)
    if error:
        return error
    try:
        _apply_topic_script_to_task(task, topic_id)
        db.session.commit()
        task = ReportVideoTask.query.get(task.id)
        return jsonify({
            "success": True,
            "data": {
                "task": task.to_dict(include_reports=True, include_ai=True),
                "topic_planning": _topic_planning_payload(task),
            },
        })
    except Exception as exc:
        db.session.rollback()
        return jsonify({"success": False, "error": str(exc)}), 400


@bp.route("/tasks/<task_id>/audios", methods=["POST"])
def upload_audios(task_id):
    task, error = _task_or_404(task_id)
    if error:
        return error

    timeline_file = request.files.get("timeline_file")
    timeline_text = request.form.get("timeline_text") or ""
    raw_audio_mode = str(request.form.get("audio_mode") or "segments").strip()
    force_asr_form_value = request.form.get("force_asr_timeline")
    force_asr_requested = str(force_asr_form_value or "false").strip().lower() in {"true", "1", "yes", "on"}
    has_timeline_payload = bool((timeline_file and timeline_file.filename) or timeline_text.strip())
    audio_mode = "full_audio_srt" if (has_timeline_payload or force_asr_requested or raw_audio_mode == "full_audio_srt") else raw_audio_mode
    force_asr_timeline = _should_force_asr_for_full_audio(force_asr_form_value, audio_mode, has_timeline_payload)
    audio_speed = _audio_speed_value(request.form.get("audio_speed") or ("1.2" if audio_mode == "full_audio_srt" else "1.0"))
    if audio_mode != "full_audio_srt":
        audio_speed = 1.0
    files = request.files.getlist("files")
    if not files and request.files.get("file"):
        files = [request.files["file"]]
    files = [file for file in files if file and file.filename]
    if not files:
        return jsonify({"success": False, "error": "请上传音频文件"}), 400

    allowed = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}
    audio_dir = task_dir(task.id) / "audios"
    audio_dir.mkdir(parents=True, exist_ok=True)
    created = []
    failed = []

    for file in files:
        filename = file.filename or ""
        suffix = Path(filename).suffix.lower()
        if suffix not in allowed:
            failed.append({"file_name": filename, "error": "仅支持 mp3/wav/m4a/aac/flac/ogg"})
            continue
        target = audio_dir / f"{uuid.uuid4().hex[:8]}_{safe_filename(filename)}{suffix if not filename.lower().endswith(suffix) else ''}"
        try:
            file.save(str(target))
            final_target = target
            if audio_mode == "full_audio_srt" and abs(audio_speed - 1.0) >= 0.001:
                final_target = _create_speed_adjusted_audio(target, audio_speed)
            duration = media_duration(final_target)
            audio = ReportVideoAudio(
                task_id=task.id,
                file_name=filename,
                file_path=rel_path(final_target),
                duration_sec=duration,
                format=final_target.suffix.lower().lstrip(".") or suffix.lstrip("."),
                match_status="pending",
            )
            db.session.add(audio)
            created.append(audio)
        except Exception as exc:
            failed.append({"file_name": filename, "error": str(exc)})

    db.session.flush()
    if audio_mode == "full_audio_srt":
        result = dict(task.result or {})
        result["audio_speed"] = {
            "speed": audio_speed,
            "applied": abs(audio_speed - 1.0) >= 0.001,
            "strategy": "ffmpeg_atempo_before_asr",
            "updated_at": datetime.utcnow().isoformat(),
        }
        task.result = result
    timeline_result = None
    if audio_mode == "full_audio_srt" and force_asr_timeline and not has_timeline_payload and len(created) == 1:
        from tasks.report_video import process_audio_timeline_task

        audio = created[0]
        audio.match_status = "asr_queued"
        audio.error_message = "已投递 ASR 字幕生成队列"
        task.status = "audio_processing"
        result = dict(task.result or {})
        result["audio_mode"] = "full_audio_srt"
        result["audio_speed"] = {
            "speed": audio_speed,
            "applied": abs(audio_speed - 1.0) >= 0.001,
            "strategy": "ffmpeg_atempo_before_asr",
            "updated_at": datetime.utcnow().isoformat(),
        }
        result["audio_timeline_job"] = {
            "status": "queued",
            "mode": "full_audio_asr",
            "calibration_model": str(request.form.get("timeline_calibration_model") or "medium").strip().lower() or "medium",
            "audio_speed": audio_speed,
            "audio_id": audio.id,
            "file_name": audio.file_name,
            "queued_at": datetime.utcnow().isoformat(),
        }
        task.result = result
        task.updated_at = datetime.utcnow()
        db.session.commit()

        try:
            celery_task = _enqueue_report_video_task(
                process_audio_timeline_task,
                audio.id,
                {
                    "calibration_model": str(request.form.get("timeline_calibration_model") or "medium").strip().lower() or "medium",
                    "fallback_to_script_estimate": False,
                    "audio_speed": audio_speed,
                },
            )
        except Exception as exc:
            db.session.rollback()
            audio = ReportVideoAudio.query.get(audio.id)
            task = ReportVideoTask.query.get(task.id)
            if audio:
                audio.match_status = "unmatched"
                audio.error_message = f"ASR 队列投递失败：{exc}"
            if task:
                task.status = "audio_failed"
                task.error_message = f"ASR 队列投递失败：{exc}"
                result = dict(task.result or {})
                result["audio_timeline_job"] = {
                    **(result.get("audio_timeline_job") or {}),
                    "status": "failed",
                    "error_message": str(exc),
                    "updated_at": datetime.utcnow().isoformat(),
                }
                task.result = result
                task.updated_at = datetime.utcnow()
            db.session.commit()
            return jsonify({"success": False, "error": f"ASR 队列投递失败：{exc}"}), 500

        task = ReportVideoTask.query.get(task.id)
        audio = ReportVideoAudio.query.get(audio.id)
        result = dict(task.result or {})
        job = dict(result.get("audio_timeline_job") or {})
        job["celery_task_id"] = celery_task.id
        job["updated_at"] = datetime.utcnow().isoformat()
        result["audio_timeline_job"] = job
        task.result = result
        task.updated_at = datetime.utcnow()
        db.session.commit()

        return jsonify({
            "success": True,
            "data": {
                "task": task.to_dict(include_reports=True, include_ai=True),
                "audios": [audio.to_dict()] if audio else [],
                "failed": failed,
                "timeline": {
                    "queued": True,
                    "timeline_source": "full_audio_asr",
                    "audio_speed": audio_speed,
                    "message": "已投递 ASR 字幕生成队列，页面会自动刷新状态。",
                    "celery_id": celery_task.id,
                },
            },
        })

    if audio_mode == "full_audio_srt":
        if len(created) != 1:
            for audio in created:
                audio.match_status = "unmatched"
                audio.error_message = "整片音频 + 时间轴模式只支持上传 1 个完整音频"
        else:
            calibration_mode = str(request.form.get("timeline_calibration_mode") or "sampled_asr").strip().lower()
            calibration_model = str(request.form.get("timeline_calibration_model") or "medium").strip().lower() or "medium"
            sample_interval_sec = float(request.form.get("sample_interval_sec") or 180)
            sample_duration_sec = float(request.form.get("sample_duration_sec") or 10)
            min_anchor_confidence = float(request.form.get("min_anchor_confidence") or 0.75)
            enable_vad = str(request.form.get("enable_vad") or "true").strip().lower() not in {"false", "0", "no"}
            timeline_rel_path = None
            timeline_generation_meta = None
            timeline_source = "uploaded_or_pasted" if has_timeline_payload else ("full_audio_asr" if force_asr_timeline else "script_estimate")
            if timeline_file and timeline_file.filename:
                timeline_dir = task_dir(task.id) / "timelines"
                timeline_dir.mkdir(parents=True, exist_ok=True)
                timeline_target = timeline_dir / f"{uuid.uuid4().hex[:8]}_{safe_filename(timeline_file.filename)}"
                timeline_file.save(str(timeline_target))
                timeline_text = timeline_target.read_text(encoding="utf-8", errors="ignore")
                timeline_rel_path = rel_path(timeline_target)
            timeline_entries = _parse_timeline_text(timeline_text)
            audio_path = PROJECT_ROOT / created[0].file_path if created[0].file_path else None
            asr_initial_prompt = _build_report_video_asr_prompt(task)
            timeline_speed_meta = None
            if timeline_entries and abs(audio_speed - 1.0) >= 0.001:
                timeline_entries = _scale_timeline_entries(timeline_entries, audio_speed, target_duration=created[0].duration_sec)
                timeline_rel_path = _write_timeline_source_text(task, timeline_entries, suffix="speed_scaled_timeline")
                timeline_speed_meta = {
                    "speed": audio_speed,
                    "strategy": "timeline_scaled_to_speed_adjusted_audio",
                    "message": "上传/粘贴的时间轴已按音频变速比例同步缩放。",
                }
            if not timeline_entries and force_asr_timeline:
                timeline_entries, timeline_generation_meta = _generate_timeline_entries_from_asr(
                    task,
                    audio_path if audio_path and audio_path.exists() else None,
                    created[0].duration_sec,
                    model_size=calibration_model,
                    initial_prompt=asr_initial_prompt,
                )
                if timeline_entries:
                    timeline_rel_path = _write_timeline_source_text(task, timeline_entries, suffix="asr_generated_timeline")
            if not timeline_entries:
                if force_asr_timeline:
                    timeline_generation_meta = {
                        **(timeline_generation_meta or {}),
                        "status": "failed",
                        "mode": "asr_transcript",
                        "reason": "asr_empty_or_unmatched",
                        "message": "强制 ASR 没有生成有效字幕时间轴，未回退到脚本推算。",
                    }
                else:
                    timeline_entries, timeline_generation_meta = _auto_generate_timeline_entries(task, created[0].duration_sec)
                    if timeline_entries:
                        timeline_rel_path = _write_timeline_source_text(task, timeline_entries, suffix="auto_generated_timeline")
            if not timeline_entries:
                failure_message = (
                    "强制 ASR 没有生成有效字幕时间轴，请检查音频或上传 SRT"
                    if force_asr_timeline
                    else "没有解析到有效时间轴，也无法生成脚本估算时间轴"
                )
                for audio in created:
                    audio.match_status = "unmatched"
                    audio.error_message = failure_message
                task.status = "audio_failed"
                task.error_message = failure_message
            else:
                if timeline_generation_meta and timeline_generation_meta.get("mode") == "asr_transcript":
                    retime_meta = {
                        "calibration_mode": "full_audio_asr",
                        "calibration_method": "full_audio_asr",
                        "strategy": "full_audio_asr",
                        "status": timeline_generation_meta.get("status") or "calibrated",
                        "message": "已通过整片 ASR 生成字幕时间轴。",
                        "audio_duration_sec": round(float(created[0].duration_sec or 0), 3),
                        "target_duration_sec": round(float(created[0].duration_sec or 0), 3),
                        "sample_count": 1,
                        "asr_total_duration_sec": round(float(created[0].duration_sec or 0), 3),
                        "anchor_count": 0,
                        "confidence_avg": 0,
                        "average_offset_sec": 0,
                        "max_offset_sec": 0,
                        "subtitle_source": "full_audio_asr",
                        "timeline_generation": timeline_generation_meta,
                    }
                else:
                    timeline_entries, retime_meta = _smart_retime_timeline_entries(
                        timeline_entries,
                        created[0].duration_sec,
                        audio_path=audio_path if audio_path and audio_path.exists() else None,
                        calibration_mode=calibration_mode,
                        sample_interval_sec=sample_interval_sec,
                        sample_seconds=sample_duration_sec,
                        min_anchor_confidence=min_anchor_confidence,
                        enable_vad=enable_vad,
                        sample_model=calibration_model,
                        asr_initial_prompt=asr_initial_prompt,
                    )
                retime_meta = {
                    **(retime_meta or {}),
                    "audio_speed": {
                        "speed": audio_speed,
                        "applied": abs(audio_speed - 1.0) >= 0.001,
                        "strategy": "ffmpeg_atempo_before_timeline_matching",
                    },
                }
                if timeline_speed_meta:
                    retime_meta["timeline_speed_adjustment"] = timeline_speed_meta
                if timeline_generation_meta:
                    retime_meta = {**(retime_meta or {}), "timeline_generation": timeline_generation_meta}
                subtitle_rel_path = _write_timeline_srt(task, timeline_entries) if timeline_entries else None
                timeline_result = _apply_full_audio_timeline(task, created[0], timeline_entries, timeline_rel_path, subtitle_rel_path, retime_meta)
                if timeline_result is not None:
                    timeline_result["auto_generated"] = bool(timeline_generation_meta)
                    timeline_result["timeline_source"] = timeline_source
                    timeline_result["audio_speed"] = audio_speed
    else:
        _match_audio_to_segments(task, created)
    matched_statuses = {"matched", "timeline_matched"}
    task.status = "waiting_video" if any(audio.match_status in matched_statuses for audio in created) else task.status
    task.updated_at = datetime.utcnow()
    db.session.commit()

    return jsonify({
        "success": True,
        "data": {
            "task": task.to_dict(include_reports=True, include_ai=True),
            "audios": [audio.to_dict() for audio in created],
            "failed": failed,
            "timeline": timeline_result,
        },
    })


@bp.route("/tasks/<task_id>/videos/generate", methods=["POST"])
def generate_videos(task_id):
    task, error = _task_or_404(task_id)
    if error:
        return error

    data = request.get_json(silent=True) or {}
    ratios = data.get("ratios") or task.output_ratios or ["16:9"]
    report_id = data.get("report_id")
    reports = [report for report in task.reports if report.analysis and report.segments]
    if report_id:
        reports = [report for report in reports if report.id == report_id]
    if not reports:
        return jsonify({"success": False, "error": "没有可生成视频的研报，请先生成 AI 文案"}), 400

    from tasks.report_video import generate_videos_task

    task.status = "video_generating"
    task.updated_at = datetime.utcnow()
    db.session.commit()
    celery_task = _enqueue_report_video_task(generate_videos_task, task.id, ratios, report_id)

    return jsonify({
        "success": True,
        "data": {
            "task": task.to_dict(include_reports=True, include_ai=True),
            "queued": True,
            "celery_id": celery_task.id,
            "outputs": [],
            "failed": [],
        },
    })


@bp.route("/outputs/<output_id>/download", methods=["GET"])
def download_output(output_id):
    from flask import send_file

    output = ReportVideoOutput.query.get(output_id)
    if not output:
        return jsonify({"success": False, "error": "Output not found"}), 404
    if not output.file_path:
        return jsonify({"success": False, "error": "输出文件不存在"}), 404
    path = (PROJECT_ROOT / output.file_path).resolve()
    if not str(path).startswith(str(PROJECT_ROOT)) or not path.exists():
        return jsonify({"success": False, "error": "输出文件不存在"}), 404
    return send_file(path, as_attachment=True, download_name=path.name)


@bp.route("/reports/<report_id>/reparse", methods=["POST"])
def reparse_report(report_id):
    report, error = _report_or_404(report_id)
    if error:
        return error

    try:
        ReportVideoAnalysis.query.filter_by(report_id=report.id).delete()
        ReportVideoMindmap.query.filter_by(report_id=report.id).delete()
        ReportVideoSegment.query.filter_by(report_id=report.id).delete()
        report.page_count = None
        report.raw_text_path = None
        report.cleaned_text_path = None
        report.raw_text_chars = 0
        report.cleaned_text_chars = 0
        report.is_scanned = False
        report.parse_status = "parsing"
        report.ai_status = "ai_pending"
        report.error_message = None
        report.raw_meta = {
            "queued_at": datetime.utcnow().isoformat(),
            "async": True,
            "reparse": True,
        }
        report.task.status = "parsing"
        report.task.updated_at = datetime.utcnow()
        db.session.commit()
        from tasks.report_video import parse_report_pdf_task

        celery_task = _enqueue_report_video_task(parse_report_pdf_task, report.id)
        return jsonify({
            "success": True,
            "data": report.to_dict(include_ai=True),
            "queued": True,
            "celery_id": celery_task.id,
        })
    except Exception as exc:
        report.parse_status = "parse_failed"
        report.error_message = str(exc)
        report.task.status = _task_status_from_reports(report.task)
        db.session.commit()
        return jsonify({"success": False, "error": str(exc)}), 500
