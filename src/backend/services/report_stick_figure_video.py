import json
import os
import re
import subprocess
import uuid
import wave
from pathlib import Path

import imageio_ffmpeg


FEATURE_NAME = "report_stick_figure_video_render"
QUEUE_NAME = "render"
RENDER_PROFILE_VERSION = "stick_figure_timeline_v4_audio_strategies"
DEFAULT_WIDTH = 1280
DEFAULT_HEIGHT = 720
DEFAULT_FPS = 30
DEFAULT_RENDER_OPTIONS = {
    "speed": 1.0,
    "subtitles_enabled": False,
    "subtitle_font_name": "Hiragino Sans GB",
    "subtitle_font_size": 42,
    "subtitle_font_color": "#FFFFFF",
    "subtitle_outline_color": "#000000",
    "subtitle_outline": 3,
    "subtitle_position": "bottom",
    "subtitle_background_enabled": True,
    "subtitle_background_color": "#111827",
    "subtitle_background_opacity": 28,
    "watermark_enabled": False,
    "watermark_text": "Shioa",
    "watermark_font_size": 28,
    "watermark_color": "#111827",
    "watermark_opacity": 45,
}
SUBTITLE_FONT_NAMES = {
    "Hiragino Sans GB",
    "PingFang SC",
    "Arial Unicode MS",
}
SUBTITLE_POSITIONS = {"top", "middle", "bottom"}


def normalize_render_options(value=None) -> dict:
    raw = value if isinstance(value, dict) else {}
    options = dict(DEFAULT_RENDER_OPTIONS)
    try:
        speed = float(raw.get("speed", options["speed"]))
    except (TypeError, ValueError):
        raise ValueError("视频倍速必须是数字")
    if speed < 0.5 or speed > 2.0:
        raise ValueError("视频倍速只支持 0.5～2.0")
    options["speed"] = round(speed, 3)
    options["subtitles_enabled"] = _boolish(
        raw.get("subtitles_enabled"),
        options["subtitles_enabled"],
    )

    font_name = str(raw.get("subtitle_font_name") or options["subtitle_font_name"]).strip()
    if font_name not in SUBTITLE_FONT_NAMES:
        raise ValueError("不支持的字幕字体")
    options["subtitle_font_name"] = font_name
    options["subtitle_font_size"] = _bounded_int(
        raw.get("subtitle_font_size"),
        options["subtitle_font_size"],
        24,
        72,
        "字幕字号",
    )
    options["subtitle_font_color"] = _hex_color(
        raw.get("subtitle_font_color"),
        options["subtitle_font_color"],
        "字幕颜色",
    )
    options["subtitle_outline_color"] = _hex_color(
        raw.get("subtitle_outline_color"),
        options["subtitle_outline_color"],
        "字幕描边颜色",
    )
    options["subtitle_outline"] = _bounded_int(
        raw.get("subtitle_outline"),
        options["subtitle_outline"],
        0,
        8,
        "字幕描边宽度",
    )
    position = str(raw.get("subtitle_position") or options["subtitle_position"]).strip().lower()
    if position not in SUBTITLE_POSITIONS:
        raise ValueError("字幕位置只支持 top、middle 或 bottom")
    options["subtitle_position"] = position
    options["subtitle_background_enabled"] = _boolish(
        raw.get("subtitle_background_enabled"),
        options["subtitle_background_enabled"],
    )
    options["subtitle_background_color"] = _hex_color(
        raw.get("subtitle_background_color"),
        options["subtitle_background_color"],
        "字幕背景颜色",
    )
    options["subtitle_background_opacity"] = _bounded_int(
        raw.get("subtitle_background_opacity"),
        options["subtitle_background_opacity"],
        0,
        80,
        "字幕背景不透明度",
    )
    options["watermark_enabled"] = _boolish(
        raw.get("watermark_enabled"),
        options["watermark_enabled"],
    )
    watermark_text = str(raw.get("watermark_text") or options["watermark_text"]).strip()
    if not watermark_text:
        raise ValueError("水印文案不能为空")
    if len(watermark_text) > 40:
        raise ValueError("水印文案不能超过 40 个字符")
    options["watermark_text"] = watermark_text
    options["watermark_font_size"] = _bounded_int(
        raw.get("watermark_font_size"),
        options["watermark_font_size"],
        18,
        56,
        "水印字号",
    )
    options["watermark_color"] = _hex_color(
        raw.get("watermark_color"),
        options["watermark_color"],
        "水印颜色",
    )
    options["watermark_opacity"] = _bounded_int(
        raw.get("watermark_opacity"),
        options["watermark_opacity"],
        10,
        100,
        "水印不透明度",
    )
    return options


def _boolish(value, default=False) -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled", "y"}


def _bounded_int(value, default, minimum, maximum, label) -> int:
    try:
        parsed = int(value if value is not None else default)
    except (TypeError, ValueError):
        raise ValueError(f"{label}必须是整数")
    if parsed < minimum or parsed > maximum:
        raise ValueError(f"{label}只支持 {minimum}～{maximum}")
    return parsed


def _hex_color(value, default, label) -> str:
    text = str(value or default).strip().upper()
    if not re.fullmatch(r"#[0-9A-F]{6}", text):
        raise ValueError(f"{label}必须是 #RRGGBB 格式")
    return text


def workflow_output_root(workflow_task_id: str) -> Path:
    root = (
        Path(__file__).resolve().parents[3]
        / "data"
        / "processed"
        / "report_stick_figure_video_workflow"
        / str(workflow_task_id)
    )
    root.mkdir(parents=True, exist_ok=True)
    return root


def video_output_path(workflow_task_id: str) -> str:
    root = workflow_output_root(workflow_task_id) / "video"
    root.mkdir(parents=True, exist_ok=True)
    return str(root / "stick_figure_video.mp4")


def timeline_manifest_path(workflow_task_id: str) -> str:
    root = workflow_output_root(workflow_task_id) / "video"
    root.mkdir(parents=True, exist_ok=True)
    return str(root / "timeline.json")


def subtitle_output_path(workflow_task_id: str) -> str:
    root = workflow_output_root(workflow_task_id) / "video"
    root.mkdir(parents=True, exist_ok=True)
    return str(root / "subtitles.ass")


def audio_duration_seconds(path: str) -> float:
    source = Path(path)
    if not source.is_file() or source.stat().st_size <= 0:
        raise ValueError(f"音频文件不存在或为空：{source}")
    try:
        with wave.open(str(source), "rb") as audio:
            rate = audio.getframerate()
            frames = audio.getnframes()
            if rate > 0 and frames > 0:
                return frames / float(rate)
    except (OSError, wave.Error):
        pass
    completed = subprocess.run(
        [
            os.getenv("FFPROBE_BINARY", "ffprobe"),
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(source),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    duration = float((completed.stdout or "0").strip() or 0)
    if duration <= 0:
        raise ValueError(f"无法读取音频时长：{source}")
    return duration


def probe_video(path: str) -> dict:
    completed = subprocess.run(
        [
            os.getenv("FFPROBE_BINARY", "ffprobe"),
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height:format=duration",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout or "{}")
    stream = (payload.get("streams") or [{}])[0]
    return {
        "duration_seconds": round(float((payload.get("format") or {}).get("duration") or 0), 3),
        "width": int(stream.get("width") or 0),
        "height": int(stream.get("height") or 0),
    }


def render_scene_timeline(
    workflow_task_id: str,
    scenes: list,
    *,
    audio_mode: str = "scene_tts",
    whole_audio_path: str | None = None,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    fps: int = DEFAULT_FPS,
    render_options=None,
    progress_callback=None,
) -> dict:
    if not scenes:
        raise ValueError("没有可合成的音画分镜")

    # Sequence 000 is a reusable title/cover asset. The narrated body starts at
    # 001, so the title asset must never enter the main-video timeline.
    renderable_scenes = [
        raw for raw in sorted(scenes, key=lambda item: int(item["sequence"]))
        if int(raw["sequence"]) >= 1
    ]
    if not renderable_scenes:
        raise ValueError("没有可合成的正文分镜（正文必须从 001 开始）")
    audio_mode = str(audio_mode or "scene_tts").strip()
    if audio_mode not in {"scene_tts", "whole_audio"}:
        raise ValueError(f"不支持的音频模式：{audio_mode}")

    options = normalize_render_options(render_options)
    speed = float(options["speed"])
    timeline = []
    cursor = 0.0
    whole_audio = Path(str(whole_audio_path or "")) if audio_mode == "whole_audio" else None
    whole_audio_seconds = 0.0
    if audio_mode == "whole_audio":
        if not whole_audio or not whole_audio.is_file() or whole_audio.stat().st_size <= 0:
            raise ValueError("整篇音频文件不存在或为空")
        whole_audio_seconds = audio_duration_seconds(str(whole_audio))
        if whole_audio_seconds < 0.1:
            raise ValueError(f"整篇音频时长异常：{whole_audio_seconds:.3f} 秒")
    previous_source_end = 0.0
    for raw in renderable_scenes:
        image_path = Path(str(raw.get("image_path") or ""))
        if not image_path.is_file() or image_path.stat().st_size <= 0:
            raise ValueError(f"第 {raw.get('sequence')} 段图片不存在或为空")
        audio_path = None
        if audio_mode == "scene_tts":
            audio_path = Path(str(raw.get("audio_path") or ""))
            if not audio_path.is_file() or audio_path.stat().st_size <= 0:
                raise ValueError(f"第 {raw.get('sequence')} 段音频不存在或为空")
            source_start = previous_source_end
            source_duration = audio_duration_seconds(str(audio_path))
            source_end = source_start + source_duration
            if source_duration < 0.1:
                raise ValueError(f"第 {raw.get('sequence')} 段音频时长异常：{source_duration:.3f} 秒")
        else:
            try:
                source_start = float(raw.get("start_seconds"))
                source_end = float(raw.get("end_seconds"))
            except (TypeError, ValueError):
                raise ValueError(f"第 {raw.get('sequence')} 段缺少整篇音频时间轴")
            source_duration = source_end - source_start
            if source_duration < 0.1:
                raise ValueError(f"第 {raw.get('sequence')} 段时间轴时长异常：{source_duration:.3f} 秒")
            if abs(source_start - previous_source_end) > 0.25:
                raise ValueError(
                    f"第 {raw.get('sequence')} 段时间轴不连续："
                    f"上一段结束 {previous_source_end:.3f}，本段开始 {source_start:.3f}"
                )
            if source_end > whole_audio_seconds + 0.5:
                raise ValueError(f"第 {raw.get('sequence')} 段时间轴超过整篇音频时长")
        duration = source_duration / speed
        entry = {
            "sequence": int(raw["sequence"]),
            "asset_id": str(raw.get("asset_id") or ""),
            "image_path": str(image_path.resolve()),
            "audio_path": str(audio_path.resolve()) if audio_path else None,
            "script_text": str(raw.get("script_text") or "").strip(),
            "start_seconds": round(cursor, 6),
            "source_start_seconds": round(source_start, 6),
            "source_end_seconds": round(source_end, 6),
            "source_audio_duration_seconds": round(source_duration, 6),
            "duration_seconds": round(duration, 6),
            "end_seconds": round(cursor + duration, 6),
            "alignment_confidence": raw.get("alignment_confidence") if audio_mode == "whole_audio" else None,
        }
        timeline.append(entry)
        cursor += duration
        previous_source_end = source_end
    if audio_mode == "whole_audio" and abs(previous_source_end - whole_audio_seconds) > 0.5:
        raise ValueError(
            f"整篇音频时间轴未覆盖音频结尾：时间轴 {previous_source_end:.3f} 秒，"
            f"音频 {whole_audio_seconds:.3f} 秒"
        )

    output_path = Path(video_output_path(workflow_task_id))
    part_path = output_path.with_name(f"{output_path.stem}.part-{uuid.uuid4().hex[:8]}{output_path.suffix}")
    log_path = output_path.with_suffix(".ffmpeg.log")
    manifest_path = Path(timeline_manifest_path(workflow_task_id))
    manifest_part = manifest_path.with_name(f"{manifest_path.name}.part-{uuid.uuid4().hex[:8]}")
    subtitle_path = Path(subtitle_output_path(workflow_task_id))
    subtitle_part = subtitle_path.with_name(f"{subtitle_path.name}.part-{uuid.uuid4().hex[:8]}")

    subtitle_entries = _build_subtitle_entries(timeline) if options["subtitles_enabled"] else []
    overlay_enabled = options["subtitles_enabled"] or options["watermark_enabled"]
    if overlay_enabled:
        subtitle_part.write_text(
            _build_ass(subtitle_entries, width, height, options, duration_seconds=cursor),
            encoding="utf-8",
        )

    ffmpeg_binary = (
        os.getenv("VIDEO_EDITOR_FFMPEG_BINARY")
        or imageio_ffmpeg.get_ffmpeg_exe()
        if overlay_enabled
        else os.getenv("FFMPEG_BINARY", "ffmpeg")
    )
    cmd = [ffmpeg_binary, "-y", "-hide_banner"]
    for entry in timeline:
        cmd.extend([
            "-loop", "1",
            "-framerate", str(fps),
            "-t", f"{entry['duration_seconds']:.6f}",
            "-i", entry["image_path"],
        ])
        if audio_mode == "scene_tts":
            cmd.extend(["-i", entry["audio_path"]])
    if audio_mode == "whole_audio":
        cmd.extend(["-i", str(whole_audio.resolve())])

    filters = []
    concat_inputs = []
    for index, entry in enumerate(timeline):
        video_input = index * 2 if audio_mode == "scene_tts" else index
        duration = entry["duration_seconds"]
        filters.append(
            f"[{video_input}:v]"
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=white,"
            f"setsar=1,fps={fps},format=yuv420p,"
            f"trim=duration={duration:.6f},setpts=PTS-STARTPTS[v{index}]"
        )
        if audio_mode == "scene_tts":
            audio_input = video_input + 1
            audio_filters = f"[{audio_input}:a]aresample=48000,"
            if abs(speed - 1.0) > 0.0001:
                audio_filters += f"atempo={speed:.6f},"
            filters.append(
                audio_filters +
                f"apad=pad_dur={duration:.6f},atrim=duration={duration:.6f},"
                f"asetpts=PTS-STARTPTS[a{index}]"
            )
            concat_inputs.append(f"[v{index}][a{index}]")
        else:
            concat_inputs.append(f"[v{index}]")
    if audio_mode == "whole_audio":
        audio_input = len(timeline)
        audio_filters = (
            f"[{audio_input}:a]aresample=48000,"
            f"atrim=start=0:end={whole_audio_seconds:.6f},asetpts=PTS-STARTPTS,"
        )
        if abs(speed - 1.0) > 0.0001:
            audio_filters += f"atempo={speed:.6f},"
        filters.append(audio_filters + f"atrim=duration={cursor:.6f}[aout]")
        filters.append(f"{''.join(concat_inputs)}concat=n={len(timeline)}:v=1:a=0[vcat]")
    elif overlay_enabled:
        filters.append(f"{''.join(concat_inputs)}concat=n={len(timeline)}:v=1:a=1[vcat][aout]")
    else:
        filters.append(f"{''.join(concat_inputs)}concat=n={len(timeline)}:v=1:a=1[vout][aout]")
    if overlay_enabled:
        filters.append(f"[vcat]ass=filename='{_filter_path(subtitle_part)}'[vout]")
    elif audio_mode == "whole_audio":
        filters.append("[vcat]null[vout]")
    cmd.extend([
        "-filter_complex", ";".join(filters),
        "-map", "[vout]",
        "-map", "[aout]",
        "-c:v", "libx264",
        "-preset", os.getenv("REPORT_STICK_FIGURE_VIDEO_PRESET", "veryfast"),
        "-crf", os.getenv("REPORT_STICK_FIGURE_VIDEO_CRF", "21"),
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        "-movflags", "+faststart",
        "-progress", "pipe:1",
        "-nostats",
        str(part_path),
    ])

    if progress_callback:
        progress_callback({"step": "prepare_timeline", "progress": 5, "message": f"已校验 {len(timeline)} 段音画素材"})
    try:
        with log_path.open("w", encoding="utf-8") as log_file:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=log_file,
                text=True,
                bufsize=1,
            )
            assert process.stdout is not None
            for line in process.stdout:
                key, _, value = line.strip().partition("=")
                if key != "out_time" or not value:
                    continue
                rendered = _parse_ffmpeg_time(value)
                progress = 8 + int(min(1.0, rendered / max(cursor, 0.001)) * 87)
                if progress_callback:
                    progress_callback({
                        "step": "render_video",
                        "progress": progress,
                        "message": f"正在合成 {rendered:.1f}/{cursor:.1f} 秒",
                    })
            process.stdout.close()
            return_code = process.wait()
        if return_code != 0:
            detail = log_path.read_text(encoding="utf-8", errors="replace")[-4000:]
            raise RuntimeError(f"FFmpeg 合成失败（exit={return_code}）：{detail}")
        if not part_path.is_file() or part_path.stat().st_size < 1024:
            raise RuntimeError("FFmpeg 未生成有效视频文件")
        metadata = probe_video(str(part_path))
        if metadata["duration_seconds"] <= 0 or metadata["width"] != width or metadata["height"] != height:
            raise RuntimeError(f"视频验收失败：{metadata}")

        manifest_payload = {
            "schema_version": RENDER_PROFILE_VERSION,
            "workflow_task_id": str(workflow_task_id),
            "body_start_sequence": 1,
            "title_scene_excluded": True,
            "audio_mode": audio_mode,
            "whole_audio_path": str(whole_audio.resolve()) if whole_audio else None,
            "scene_count": len(timeline),
            "duration_seconds": round(cursor, 6),
            "width": width,
            "height": height,
            "fps": fps,
            "render_options": options,
            "subtitle_path": str(subtitle_path) if options["subtitles_enabled"] else None,
            "subtitle_entry_count": len(subtitle_entries),
            "timeline": timeline,
        }
        manifest_part.write_text(json.dumps(manifest_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(part_path, output_path)
        os.replace(manifest_part, manifest_path)
        if overlay_enabled:
            os.replace(subtitle_part, subtitle_path)
        elif subtitle_path.exists():
            subtitle_path.unlink()
        if progress_callback:
            progress_callback({"step": "verify_video", "progress": 98, "message": "视频文件已生成并通过验收"})
        return {
            "video_path": str(output_path),
            "manifest_path": str(manifest_path),
            "bytes": output_path.stat().st_size,
            "scene_count": len(timeline),
            "duration_seconds": metadata["duration_seconds"],
            "width": metadata["width"],
            "height": metadata["height"],
            "fps": fps,
            "render_profile": RENDER_PROFILE_VERSION,
            "render_options": options,
            "audio_mode": audio_mode,
            "subtitle_path": str(subtitle_path) if options["subtitles_enabled"] else None,
            "subtitle_entry_count": len(subtitle_entries),
        }
    finally:
        for candidate in (part_path, manifest_part, subtitle_part):
            try:
                if candidate.exists():
                    candidate.unlink()
            except OSError:
                pass


def _parse_ffmpeg_time(value: str) -> float:
    try:
        hours, minutes, seconds = str(value).split(":", 2)
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    except (TypeError, ValueError):
        return 0.0


def _filter_path(path: Path) -> str:
    return str(path.resolve()).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def _build_subtitle_entries(timeline: list, max_chars: int = 36) -> list:
    entries = []
    for scene in timeline:
        chunks = _split_subtitle_text(scene.get("script_text"), max_chars=max_chars)
        if not chunks:
            continue
        weights = [max(1, len(re.sub(r"\s+", "", chunk))) for chunk in chunks]
        total_weight = sum(weights)
        scene_start = float(scene["start_seconds"])
        scene_duration = float(scene["duration_seconds"])
        cursor = scene_start
        for index, (chunk, weight) in enumerate(zip(chunks, weights)):
            end = (
                scene_start + scene_duration
                if index == len(chunks) - 1
                else cursor + scene_duration * weight / total_weight
            )
            entries.append({
                "start_seconds": round(cursor, 6),
                "end_seconds": round(end, 6),
                "text": _wrap_subtitle_text(chunk),
                "sequence": scene["sequence"],
            })
            cursor = end
    return entries


def _split_subtitle_text(value, max_chars: int = 36) -> list:
    text = re.sub(r"\s+", "", str(value or "").strip())
    if not text:
        return []
    sentences = [part for part in re.split(r"(?<=[。！？；!?;])", text) if part]
    chunks = []
    current = ""
    for sentence in sentences:
        while len(sentence) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(sentence[:max_chars])
            sentence = sentence[max_chars:]
        if current and len(current) + len(sentence) > max_chars:
            chunks.append(current)
            current = sentence
        else:
            current += sentence
    if current:
        chunks.append(current)
    return chunks


def _wrap_subtitle_text(value: str, line_chars: int = 18) -> str:
    text = str(value or "")
    return "\\N".join(text[index:index + line_chars] for index in range(0, len(text), line_chars))


def _ass_time(seconds: float) -> str:
    centiseconds = max(0, int(round(float(seconds) * 100)))
    hours, remainder = divmod(centiseconds, 360000)
    minutes, remainder = divmod(remainder, 6000)
    secs, fraction = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{fraction:02d}"


def _ass_color(value: str, opacity: int = 100) -> str:
    raw = str(value).lstrip("#")
    alpha = max(0, min(255, round(255 * (1 - int(opacity) / 100))))
    return f"&H{alpha:02X}{raw[4:6]}{raw[2:4]}{raw[0:2]}".upper()


def _ass_text(value: str) -> str:
    return str(value or "").replace("{", "（").replace("}", "）")


def _build_ass(entries: list, width: int, height: int, options: dict, *, duration_seconds: float) -> str:
    position = options["subtitle_position"]
    alignment = {"bottom": 2, "middle": 5, "top": 8}[position]
    margin_v = 46 if position in {"top", "bottom"} else 0
    background_enabled = bool(options.get("subtitle_background_enabled"))
    subtitle_border_style = 3 if background_enabled else 1
    subtitle_outline = max(7, int(options["subtitle_outline"])) if background_enabled else int(options["subtitle_outline"])
    subtitle_outline_color = (
        _ass_color(
            options["subtitle_background_color"],
            options["subtitle_background_opacity"],
        )
        if background_enabled
        else _ass_color(options["subtitle_outline_color"])
    )
    subtitle_back_color = (
        _ass_color(
            options["subtitle_background_color"],
            options["subtitle_background_opacity"],
        )
        if background_enabled
        else "&H00000000"
    )
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {width}\n"
        f"PlayResY: {height}\n"
        "ScaledBorderAndShadow: yes\n"
        "WrapStyle: 2\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{options['subtitle_font_name']},{options['subtitle_font_size']},"
        f"{_ass_color(options['subtitle_font_color'])},&H000000FF,"
        f"{subtitle_outline_color},{subtitle_back_color},"
        f"-1,0,0,0,100,100,0,0,{subtitle_border_style},{subtitle_outline},0,"
        f"{alignment},30,30,{margin_v},1\n"
        f"Style: Watermark,{options['subtitle_font_name']},{options['watermark_font_size']},"
        f"{_ass_color(options['watermark_color'], options['watermark_opacity'])},&H000000FF,"
        f"&H4DFFFFFF,&H00000000,-1,0,0,0,100,100,0,0,1,1,0,9,20,32,24,1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    events = [
        f"Dialogue: 0,{_ass_time(item['start_seconds'])},{_ass_time(item['end_seconds'])},"
        f"Default,,0,0,0,,{_ass_text(item['text'])}"
        for item in entries
    ]
    if options["watermark_enabled"]:
        events.append(
            f"Dialogue: 10,0:00:00.00,{_ass_time(duration_seconds)},Watermark,,0,0,0,,"
            f"{_ass_text(options['watermark_text'])}"
        )
    return header + "\n".join(events) + "\n"
