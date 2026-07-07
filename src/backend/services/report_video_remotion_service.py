import json
import subprocess
import uuid
from pathlib import Path

from services.report_video_render_service import concat_audio_for_segments, media_duration, safe_filename
from services.report_video_service import PROJECT_ROOT, rel_path, task_dir


REMOTION_RENDERER_VERSION = "remotion_storyline_v1"
REMOTION_RENDERER_DIR = PROJECT_ROOT / "src" / "remotion_renderer"


def _ratio_dimensions(ratio):
    return (1080, 1920) if ratio == "9:16" else (1920, 1080)


def _task_segments(task):
    segments = []
    for report in task.reports or []:
        segments.extend(list(report.segments or []))
    return sorted(segments, key=lambda item: (item.report.report_code if item.report else "", item.segment_index or 0))


def _text(value, default=""):
    return str(value or default).strip()


def _duration_for_segment(segment):
    return float(segment.actual_duration_sec or segment.estimated_duration_sec or 8.0)


def _report_payload(report):
    analysis = report.analysis
    return {
        "id": report.id,
        "code": report.report_code,
        "title": _text(getattr(analysis, "report_title", None) or report.file_name, "机构研报"),
        "institution": _text(((analysis.raw_payload or {}) if analysis else {}).get("institution")) if analysis else None,
        "coreView": _text(getattr(analysis, "core_view", None)) if analysis else None,
        "keyPoints": [str(item) for item in (getattr(analysis, "key_points", None) or [])[:5]] if analysis else [],
    }


def _segment_payload(segment):
    report = segment.report
    analysis = report.analysis if report else None
    return {
        "id": segment.id,
        "segmentId": segment.segment_id or segment.id,
        "reportCode": report.report_code if report else "001",
        "reportTitle": _text(getattr(analysis, "report_title", None) or (report.file_name if report else ""), "机构研报"),
        "title": _text(segment.segment_title, f"段落 {segment.segment_index or 1}"),
        "text": _text(segment.voice_text),
        "focusNodeId": segment.focus_node_id,
        "durationSec": _duration_for_segment(segment),
    }


def _timeline_payload(task, segments):
    segment_by_id = {segment.id: segment for segment in segments}
    spans = []
    for raw in sorted((task.result or {}).get("timeline_spans") or [], key=lambda item: item.get("start_sec") or 0):
        if raw.get("type") != "segment":
            continue
        segment_id = raw.get("segment_id")
        if segment_id not in segment_by_id:
            continue
        start_sec = float(raw.get("start_sec") or 0.0)
        end_sec = float(raw.get("end_sec") or start_sec + _duration_for_segment(segment_by_id[segment_id]))
        if end_sec <= start_sec:
            continue
        spans.append({
            "type": "segment",
            "segmentId": segment_id,
            "startSec": round(start_sec, 3),
            "endSec": round(end_sec, 3),
            "text": _text(raw.get("text") or segment_by_id[segment_id].voice_text),
        })
    if spans:
        return spans

    cursor = 0.0
    for segment in segments:
        duration = _duration_for_segment(segment)
        spans.append({
            "type": "segment",
            "segmentId": segment.id,
            "startSec": round(cursor, 3),
            "endSec": round(cursor + duration, 3),
            "text": _text(segment.voice_text),
        })
        cursor += duration
    return spans


def _full_audio_path(task):
    full_audio = (task.result or {}).get("full_audio") or {}
    file_path = full_audio.get("file_path")
    if not file_path:
        return None
    path = PROJECT_ROOT / file_path
    return path if path.exists() else None


def _audio_path_for_task(task, segments, output_root):
    audio_path = _full_audio_path(task)
    if audio_path:
        return audio_path
    if not segments:
        return None
    report = segments[0].report
    if not report:
        return None
    return concat_audio_for_segments(report, segments, output_root / "combined_remotion_audio.m4a")


def _duration_for_task(task, segments, audio_path):
    if audio_path and Path(audio_path).exists():
        duration = media_duration(audio_path)
        if duration:
            return float(duration)
    spans = _timeline_payload(task, segments)
    if spans:
        return max(float(span["endSec"]) for span in spans)
    return sum(_duration_for_segment(segment) for segment in segments)


def _theme_payload(task):
    result = task.result or {}
    planning = result.get("topic_planning") or {}
    script = planning.get("topic_video_script") or {}
    topic = planning.get("selected_topic") or {}
    title = script.get("video_title") or topic.get("topic_name") or task.task_name
    subtitle = script.get("video_subtitle") or topic.get("topic_summary") or ""
    reports = task.reports or []
    institutions = []
    for report in reports:
        analysis = report.analysis
        raw = (analysis.raw_payload or {}) if analysis else {}
        institution = _text(raw.get("institution"))
        if institution and institution not in institutions:
            institutions.append(institution)
    return {
        "title": _text(title, task.task_name),
        "subtitle": _text(subtitle),
        "sourceLine": f"{len(reports)} 份研报" + (f" · {' / '.join(institutions[:4])}" if institutions else ""),
    }


def build_remotion_payload(task, ratio, output_root):
    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    segments = _task_segments(task)
    if not segments:
        raise ValueError("没有可渲染的口播段落，请先生成 AI 文案")
    audio_path = _audio_path_for_task(task, segments, output_root)
    duration_sec = max(_duration_for_task(task, segments, audio_path), 3.0)
    return {
        "taskId": task.id,
        "taskName": task.task_name,
        "ratio": ratio,
        "durationSec": round(duration_sec, 3),
        "reports": [_report_payload(report) for report in task.reports or []],
        "segments": [_segment_payload(segment) for segment in segments],
        "timelineSpans": _timeline_payload(task, segments),
        "theme": _theme_payload(task),
        "rendererVersion": REMOTION_RENDERER_VERSION,
    }, audio_path


def ensure_remotion_renderer_ready():
    package_json = REMOTION_RENDERER_DIR / "package.json"
    node_modules = REMOTION_RENDERER_DIR / "node_modules"
    if not package_json.exists():
        raise RuntimeError(f"Remotion renderer package missing: {package_json}")
    if not node_modules.exists():
        raise RuntimeError("Remotion 生产线尚未安装依赖，请先执行：cd src/remotion_renderer && npm install")


def _run_remotion(payload_path, output_path, timeout=3600):
    ensure_remotion_renderer_ready()
    result = subprocess.run(
        ["npm", "run", "render", "--", str(payload_path), str(output_path)],
        cwd=REMOTION_RENDERER_DIR,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        tail = "\n".join([result.stdout[-2000:], result.stderr[-4000:]]).strip()
        raise RuntimeError(f"Remotion 渲染失败：{tail or result.returncode}")
    return result


def _mux_audio(video_path, audio_path, output_path):
    if not audio_path or not Path(audio_path).exists():
        return video_path
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            str(video_path),
            "-i",
            str(audio_path),
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-shortest",
            "-movflags",
            "+faststart",
            str(output_path),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=600,
    )
    return output_path if output_path.exists() else video_path


def render_task_video_with_remotion(task, ratio="16:9"):
    ratio_key = ratio.replace(":", "x")
    output_root = task_dir(task.id) / "outputs" / "remotion" / ratio_key / uuid.uuid4().hex[:8]
    output_root.mkdir(parents=True, exist_ok=True)
    payload, audio_path = build_remotion_payload(task, ratio, output_root)
    payload_path = output_root / "payload.json"
    payload_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    output_name = safe_filename(f"{task.task_name}_remotion_{ratio_key}")[:120]
    silent_output_path = output_root / f"{output_name}_silent.mp4"
    final_output_path = output_root / f"{output_name}_{uuid.uuid4().hex[:8]}.mp4"
    _run_remotion(payload_path, silent_output_path)
    video_path = _mux_audio(silent_output_path, audio_path, final_output_path)
    duration = media_duration(video_path)
    width, height = _ratio_dimensions(ratio)
    return {
        "file_path": rel_path(video_path),
        "duration_sec": duration,
        "ratio": ratio,
        "video_type": "remotion_task_storyline",
        "renderer_version": REMOTION_RENDERER_VERSION,
        "payload_path": rel_path(payload_path),
        "width": width,
        "height": height,
    }
