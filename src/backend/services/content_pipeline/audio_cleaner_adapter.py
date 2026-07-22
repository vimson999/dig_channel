import json
import os
import re
import shutil
import subprocess
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional
from xml.sax.saxutils import escape


PROJECT_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_GAP_AUDIO = Path("/Users/v9/Downloads/room/音频/00-头尾/间隔.MP3")
AUDIO_SUFFIXES = {".mp3", ".m4a", ".wav", ".aac", ".flac", ".ogg"}


class AudioCleanerError(RuntimeError):
    """Raised when local audio cleaning cannot complete a batch."""


@dataclass
class AudioCleanOptions:
    head_trim_seconds: float = 11.0
    tail_trim_seconds: float = 90.0
    gap_audio_path: str = ""
    bitrate: str = "128k"
    sample_rate: int = 44100
    channels: int = 2
    fast_copy: bool = True
    max_output_size_mb: float = 49.0


class AudioCleanerAdapter:
    """Local MP3 cleaner used by the content pipeline.

    The current rule follows the Hermes audio skill: trim 11s from the head,
    trim 90s from the tail, and add the configured interval audio before and
    after the clipped middle section.
    """

    def __init__(self, output_root: Optional[str | Path] = None):
        self.output_root = Path(
            output_root
            or os.getenv("CONTENT_PIPELINE_OUTPUT_ROOT")
            or (PROJECT_ROOT / "runtime" / "content_pipeline" / "batches")
        ).expanduser().resolve()

    def run_batch(
        self,
        *,
        batch_id: str,
        files: Iterable[str | Path],
        source_root: Optional[str | Path] = None,
        options: Optional[AudioCleanOptions] = None,
        progress_callback: Optional[Callable[[dict], None]] = None,
        reset_workspace: bool = True,
    ) -> dict:
        options = options or AudioCleanOptions()
        safe_batch_id = _safe_batch_id(batch_id)
        input_files = _resolve_audio_files(files)
        if not input_files:
            raise AudioCleanerError("没有可清洗的音频文件")

        gap_audio = Path(
            options.gap_audio_path
            or os.getenv("AUDIO_CLEAN_GAP_PATH")
            or DEFAULT_GAP_AUDIO
        ).expanduser().resolve()
        if not gap_audio.exists() or not gap_audio.is_file():
            raise AudioCleanerError(f"头尾间隔音频不存在: {gap_audio}")

        local_batch_dir = self.output_root / safe_batch_id
        local_output_dir = local_batch_dir / "audio_cleaned"
        temp_dir = local_batch_dir / "_audio_tmp"
        if reset_workspace and local_output_dir.exists():
            shutil.rmtree(local_output_dir)
        if reset_workspace and temp_dir.exists():
            shutil.rmtree(temp_dir)
        local_output_dir.mkdir(parents=True, exist_ok=True)

        source_base = Path(source_root).expanduser().resolve() if source_root else None
        started_at = time.time()
        files_result = []

        total = len(input_files)
        for index, source in enumerate(input_files, start=1):
            if progress_callback:
                progress_callback({
                    "phase": "cleaning",
                    "progress": 5 + int((index - 1) / max(total, 1) * 85),
                    "message": f"正在清洗第 {index}/{total} 个音频",
                })
            files_result.append(
                self._clean_one(
                    source=source,
                    source_root=source_base,
                    output_dir=local_output_dir,
                    gap_audio=gap_audio,
                    options=options,
                )
            )

        cleaned_files = [item for item in files_result if item.get("status") == "success"]
        skipped_files = [item for item in files_result if item.get("status") == "skipped"]
        failed_files = [item for item in files_result if item.get("status") == "failed"]
        list_paths = self._write_manifests(local_batch_dir, local_output_dir, files_result, options, gap_audio)

        result = {
            "batch_id": safe_batch_id,
            "local_workspace": str(local_batch_dir),
            "local_output_dir": str(local_output_dir),
            "gap_audio_path": str(gap_audio),
            "files": files_result,
            "cleaned_files": cleaned_files,
            "success_count": len(cleaned_files),
            "skipped_count": len(skipped_files),
            "failed_count": len(failed_files),
            "duration_seconds": round(time.time() - started_at, 3),
            **list_paths,
        }
        _write_json(local_batch_dir / "manifest.json", result)
        if progress_callback:
            progress_callback({"phase": "done", "progress": 100, "message": "音频清洗完成"})
        return result

    def _clean_one(self, source: Path, source_root: Optional[Path], output_dir: Path, gap_audio: Path, options: AudioCleanOptions) -> dict:
        started_at = time.time()
        rel_path = _relative_source_path(source, source_root)
        output_name = _cleaned_audio_filename(source.name)
        target = output_dir / rel_path.parent / output_name
        target.parent.mkdir(parents=True, exist_ok=True)

        try:
            duration = _probe_duration(source)
            keep_duration = duration - float(options.head_trim_seconds) - float(options.tail_trim_seconds)
            if keep_duration <= 1:
                return {
                    "status": "skipped",
                    "reason": "too_short",
                    "source_path": str(source),
                    "source_duration": round(duration, 3),
                    "message": "音频时长不足以执行去头去尾",
                }

            fast_error = ""
            if options.fast_copy:
                try:
                    result = self._clean_one_stream_copy(
                        source=source,
                        target=target,
                        duration=duration,
                        keep_duration=keep_duration,
                        gap_audio=gap_audio,
                        options=options,
                        started_at=started_at,
                    )
                    if _exceeds_output_size_limit(target, options.max_output_size_mb):
                        raise AudioCleanerError(
                            f"流复制输出超过 {options.max_output_size_mb:g}MB，自动降码率重编码"
                        )
                    return result
                except Exception as exc:
                    fast_error = str(exc)
                    if target.exists():
                        target.unlink()

            result = self._clean_one_reencode(
                source=source,
                target=target,
                duration=duration,
                keep_duration=keep_duration,
                gap_audio=gap_audio,
                options=options,
                started_at=started_at,
            )
            if fast_error:
                result["stream_copy_fallback_error"] = fast_error
            return result
        except Exception as exc:
            try:
                if target.exists():
                    target.unlink()
            except OSError:
                pass
            return {
                "status": "failed",
                "source_path": str(source),
                "output_path": str(target),
                "error": str(exc),
                "duration_seconds": round(time.time() - started_at, 3),
            }

    def _clean_one_stream_copy(
        self,
        *,
        source: Path,
        target: Path,
        duration: float,
        keep_duration: float,
        gap_audio: Path,
        options: AudioCleanOptions,
        started_at: float,
    ) -> dict:
        source_stream = _probe_audio_stream(source)
        if source_stream.get("codec_name") != "mp3":
            raise AudioCleanerError(f"源音频不是 MP3，无法流复制: {source_stream.get('codec_name') or 'unknown'}")

        matched_gap = _matching_gap_audio(gap_audio, source_stream, options)
        gap_stream = _probe_audio_stream(matched_gap)
        if not _can_stream_copy_concat(source_stream, gap_stream):
            raise AudioCleanerError("源音频与间隔音频参数不一致，无法流复制拼接")

        temp_dir = target.parent.parent / "_audio_tmp" / f"{target.stem}_{int(time.time() * 1000)}"
        temp_dir.mkdir(parents=True, exist_ok=True)
        middle_audio = temp_dir / "middle.mp3"
        concat_list = temp_dir / "concat.txt"
        try:
            cut_command = [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-ss",
                f"{float(options.head_trim_seconds):.3f}",
                "-i",
                str(source),
                "-t",
                f"{keep_duration:.3f}",
                "-map",
                "0:a:0",
                "-vn",
                "-c",
                "copy",
                str(middle_audio),
            ]
            _run_command(cut_command, "音频流复制截取失败")
            if not middle_audio.exists() or middle_audio.stat().st_size <= 0:
                raise AudioCleanerError("截取后的中间音频为空")

            concat_list.write_text(
                "\n".join([
                    _concat_file_line(matched_gap),
                    _concat_file_line(middle_audio),
                    _concat_file_line(matched_gap),
                ])
                + "\n",
                encoding="utf-8",
            )
            concat_command = [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_list),
                "-c",
                "copy",
                str(target),
            ]
            _run_command(concat_command, "音频流复制拼接失败")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

        if not target.exists() or target.stat().st_size <= 0:
            raise AudioCleanerError("输出音频为空")
        output_duration = _probe_duration(target)
        expected_duration = (
            duration
            - float(options.head_trim_seconds)
            - float(options.tail_trim_seconds)
            + 2 * _probe_duration(matched_gap)
        )
        if abs(output_duration - expected_duration) > 3:
            raise AudioCleanerError(
                "流复制输出时长异常，触发重编码兜底: "
                f"output={output_duration:.3f}s expected={expected_duration:.3f}s"
            )
        return {
            "status": "success",
            "method": "stream_copy",
            "source_path": str(source),
            "output_path": str(target),
            "filename": target.name,
            "source_duration": round(duration, 3),
            "output_duration": round(output_duration, 3),
            "trim_head_seconds": float(options.head_trim_seconds),
            "trim_tail_seconds": float(options.tail_trim_seconds),
            "gap_audio_used": str(matched_gap),
            "duration_seconds": round(time.time() - started_at, 3),
            "size_bytes": target.stat().st_size,
        }

    def _clean_one_reencode(
        self,
        *,
        source: Path,
        target: Path,
        duration: float,
        keep_duration: float,
        gap_audio: Path,
        options: AudioCleanOptions,
        started_at: float,
    ) -> dict:
        middle_end = duration - float(options.tail_trim_seconds)
        command = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(gap_audio),
            "-i",
            str(source),
            "-i",
            str(gap_audio),
            "-filter_complex",
            (
                f"[0:a]aresample={int(options.sample_rate)},"
                f"aformat=sample_fmts=fltp:channel_layouts={_channel_layout(options.channels)}[g0];"
                f"[1:a]atrim=start={float(options.head_trim_seconds):.3f}:end={middle_end:.3f},"
                f"asetpts=PTS-STARTPTS,aresample={int(options.sample_rate)},"
                f"aformat=sample_fmts=fltp:channel_layouts={_channel_layout(options.channels)}[mid];"
                f"[2:a]aresample={int(options.sample_rate)},"
                f"aformat=sample_fmts=fltp:channel_layouts={_channel_layout(options.channels)}[g1];"
                "[g0][mid][g1]concat=n=3:v=0:a=1[out]"
            ),
            "-map",
            "[out]",
            "-vn",
            "-codec:a",
            "libmp3lame",
            "-b:a",
            _bounded_audio_bitrate(options.bitrate, output_duration=duration, max_output_size_mb=options.max_output_size_mb),
            str(target),
        ]
        _run_command(command, "音频重编码清洗失败")
        if not target.exists() or target.stat().st_size <= 0:
            raise AudioCleanerError("输出音频为空")
        if _exceeds_output_size_limit(target, options.max_output_size_mb):
            raise AudioCleanerError(f"重编码输出仍超过 {options.max_output_size_mb:g}MB")

        output_duration = _probe_duration(target)
        return {
            "status": "success",
            "method": "reencode",
            "source_path": str(source),
            "output_path": str(target),
            "filename": target.name,
            "source_duration": round(duration, 3),
            "output_duration": round(output_duration, 3),
            "trim_head_seconds": float(options.head_trim_seconds),
            "trim_tail_seconds": float(options.tail_trim_seconds),
            "gap_audio_used": str(gap_audio),
            "duration_seconds": round(time.time() - started_at, 3),
            "size_bytes": target.stat().st_size,
        }

    def _write_manifests(self, batch_dir: Path, output_dir: Path, files_result: list[dict], options: AudioCleanOptions, gap_audio: Path) -> dict:
        date_token = time.strftime("%m%d")
        text_list = output_dir / f"音频文件清单_{date_token}.txt"
        md_list = output_dir / f"音频文件清单_{date_token}.md"
        docx_list = output_dir / f"音频文件清单_{date_token}.docx"

        successful = [item for item in files_result if item.get("status") == "success"]
        lines = [item.get("filename") or Path(item.get("output_path", "")).name for item in successful]
        text_list.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")

        md_lines = [
            "# 音频文件清单",
            "",
            f"- 生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"- 头部裁剪：{options.head_trim_seconds}s",
            f"- 尾部裁剪：{options.tail_trim_seconds}s",
            f"- 间隔音频：{gap_audio}",
            f"- 成功：{len(successful)}",
            "",
            "| 序号 | 文件名 | 大小 MB | 输出时长 |",
            "| --- | --- | ---: | ---: |",
        ]
        for index, item in enumerate(successful, start=1):
            size_mb = round(float(item.get("size_bytes") or 0) / 1024 / 1024, 2)
            md_lines.append(f"| {index} | {item.get('filename')} | {size_mb} | {_format_duration(item.get('output_duration'))} |")
        md_list.write_text("\n".join(md_lines) + "\n", encoding="utf-8")
        _write_docx_table(docx_list, successful)

        _write_json(batch_dir / "audio_clean_manifest.json", {
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "output_dir": str(output_dir),
            "gap_audio_path": str(gap_audio),
            "options": {
                "head_trim_seconds": options.head_trim_seconds,
                "tail_trim_seconds": options.tail_trim_seconds,
                "bitrate": options.bitrate,
                "sample_rate": options.sample_rate,
                "channels": options.channels,
                "fast_copy": options.fast_copy,
                "max_output_size_mb": options.max_output_size_mb,
            },
            "files": files_result,
        })

        return {
            "text_list_path": str(text_list),
            "markdown_list_path": str(md_list),
            "docx_list_path": str(docx_list),
        }


def _resolve_audio_files(files: Iterable[str | Path]) -> list[Path]:
    normalized = []
    seen = set()
    for item in files or []:
        path = Path(str(item)).expanduser().resolve()
        if not path.exists() or not path.is_file():
            raise ValueError(f"文件不存在: {path}")
        if path.suffix.lower() not in AUDIO_SUFFIXES:
            raise ValueError(f"不支持的音频文件: {path}")
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        normalized.append(path)
    return normalized


def _probe_duration(path: Path) -> float:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    completed = subprocess.run(command, text=True, capture_output=True, check=False)
    if completed.returncode != 0:
        raise AudioCleanerError((completed.stderr or completed.stdout or f"无法读取音频时长: {path}").strip())
    try:
        return float((completed.stdout or "").strip())
    except ValueError as exc:
        raise AudioCleanerError(f"无法解析音频时长: {path}") from exc


def _probe_audio_stream(path: Path) -> dict:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "a:0",
        "-show_entries",
        "stream=codec_name,sample_rate,channels",
        "-of",
        "json",
        str(path),
    ]
    completed = subprocess.run(command, text=True, capture_output=True, check=False)
    if completed.returncode != 0:
        raise AudioCleanerError((completed.stderr or completed.stdout or f"无法读取音频参数: {path}").strip())
    payload = json.loads(completed.stdout or "{}")
    streams = payload.get("streams") or []
    if not streams:
        raise AudioCleanerError(f"未找到音频流: {path}")
    stream = streams[0]
    try:
        return {
            "codec_name": str(stream.get("codec_name") or "").lower(),
            "sample_rate": int(stream.get("sample_rate") or 0),
            "channels": int(stream.get("channels") or 0),
        }
    except (TypeError, ValueError) as exc:
        raise AudioCleanerError(f"无法解析音频参数: {path}") from exc


def _matching_gap_audio(gap_audio: Path, source_stream: dict, options: AudioCleanOptions) -> Path:
    gap_stream = _probe_audio_stream(gap_audio)
    if _can_stream_copy_concat(source_stream, gap_stream):
        return gap_audio

    sample_rate = int(source_stream.get("sample_rate") or 0)
    channels = int(source_stream.get("channels") or 0)
    if source_stream.get("codec_name") != "mp3" or not sample_rate or not channels:
        raise AudioCleanerError("源音频参数不适合生成流复制间隔音频")

    cache_dir = PROJECT_ROOT / "runtime" / "content_pipeline" / "audio_gaps"
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / f"{_safe_filename(gap_audio.stem)}_{sample_rate}hz_{channels}ch.mp3"
    if target.exists() and target.stat().st_size > 0:
        target_stream = _probe_audio_stream(target)
        if _can_stream_copy_concat(source_stream, target_stream):
            return target

    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(gap_audio),
        "-vn",
        "-ar",
        str(sample_rate),
        "-ac",
        str(channels),
        "-codec:a",
        "libmp3lame",
        "-b:a",
        str(options.bitrate),
        str(target),
    ]
    _run_command(command, "生成匹配间隔音频失败")
    target_stream = _probe_audio_stream(target)
    if not _can_stream_copy_concat(source_stream, target_stream):
        raise AudioCleanerError("生成后的间隔音频仍无法与源音频流复制拼接")
    return target


def _can_stream_copy_concat(left: dict, right: dict) -> bool:
    return (
        left.get("codec_name") == "mp3"
        and right.get("codec_name") == "mp3"
        and int(left.get("sample_rate") or 0) == int(right.get("sample_rate") or 0)
        and int(left.get("channels") or 0) == int(right.get("channels") or 0)
    )


def _concat_file_line(path: Path) -> str:
    value = str(path).replace("\\", "\\\\").replace("'", "\\'")
    return f"file '{value}'"


def _channel_layout(channels: int) -> str:
    return "mono" if int(channels or 2) == 1 else "stereo"


def _exceeds_output_size_limit(path: Path, max_output_size_mb: float) -> bool:
    if not max_output_size_mb or float(max_output_size_mb) <= 0:
        return False
    return path.stat().st_size > float(max_output_size_mb) * 1024 * 1024


def _bounded_audio_bitrate(configured_bitrate: str, *, output_duration: float, max_output_size_mb: float) -> str:
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([kKmM]?)\s*", str(configured_bitrate or "128k"))
    if not match:
        raise AudioCleanerError(f"无法解析音频码率: {configured_bitrate}")
    configured_kbps = float(match.group(1)) * (1000 if match.group(2).lower() == "m" else 1)
    if not max_output_size_mb or float(max_output_size_mb) <= 0 or float(output_duration or 0) <= 0:
        return f"{max(8, int(configured_kbps))}k"

    # Reserve 3% for MP3 container/metadata overhead so the result remains below
    # the platform's hard upload limit instead of merely targeting it exactly.
    target_bits = float(max_output_size_mb) * 1024 * 1024 * 8 * 0.97
    size_limited_kbps = int(target_bits / float(output_duration) / 1000)
    requested_kbps = max(8, min(int(configured_kbps), size_limited_kbps))
    # libmp3lame rounds unsupported CBR values upward (for example 90k to
    # 96k), which can put a long recording back over the hard size limit.
    # Select the highest standard MP3 bitrate that does not exceed the budget.
    standard_kbps = (8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320)
    bounded_kbps = max(value for value in standard_kbps if value <= requested_kbps)
    return f"{bounded_kbps}k"


def _run_command(command: list[str], message: str):
    completed = subprocess.run(command, text=True, capture_output=True, check=False)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "ffmpeg failed").strip()
        raise AudioCleanerError(f"{message}: {detail}")


def _cleaned_audio_filename(name: str) -> str:
    path = Path(name)
    stem = path.stem.strip()
    suffix = ".mp3"
    match = re.search(r"(?<!\d)(\d{6})$", stem)
    if match and not re.match(r"^\d{6}[-_]", stem):
        date_token = match.group(1)
        title = stem[: match.start()].strip(" -_")
        stem = f"{date_token}-{title}" if title else date_token
    return f"{_safe_filename(stem)}{suffix}"


def _relative_source_path(source: Path, source_root: Optional[Path]) -> Path:
    if source_root:
        try:
            return source.resolve().relative_to(source_root)
        except ValueError:
            pass
    return Path(source.name)


def _safe_batch_id(value: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(value or "")).strip("._-")
    if not safe:
        raise AudioCleanerError("batch_id 不能为空")
    return safe[:120]


def _safe_filename(value: str) -> str:
    safe = re.sub(r'[\\/:*?"<>|\r\n]+', "_", str(value or "audio")).strip()
    return safe[:180] or "audio"


def _format_duration(value) -> str:
    try:
        seconds = int(round(float(value)))
    except (TypeError, ValueError):
        return "-"
    minutes, sec = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{sec:02d}"
    return f"{minutes}:{sec:02d}"


def _write_json(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_docx_table(path: Path, rows: list[dict]):
    body_rows = [
        ["序号", "文件名", "大小 MB", "输出时长"],
    ]
    for index, item in enumerate(rows, start=1):
        body_rows.append([
            str(index),
            str(item.get("filename") or ""),
            str(round(float(item.get("size_bytes") or 0) / 1024 / 1024, 2)),
            _format_duration(item.get("output_duration")),
        ])

    table_rows = "".join(
        "<w:tr>"
        + "".join(
            "<w:tc><w:tcPr><w:tcW w:w=\"2400\" w:type=\"dxa\"/></w:tcPr>"
            f"<w:p><w:r><w:t>{escape(cell)}</w:t></w:r></w:p></w:tc>"
            for cell in row
        )
        + "</w:tr>"
        for row in body_rows
    )
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body>"
        "<w:p><w:r><w:t>音频文件清单</w:t></w:r></w:p>"
        "<w:tbl>"
        "<w:tblPr><w:tblW w:w=\"0\" w:type=\"auto\"/></w:tblPr>"
        f"{table_rows}"
        "</w:tbl>"
        "<w:sectPr><w:pgSz w:w=\"11906\" w:h=\"16838\"/><w:pgMar w:top=\"1440\" w:right=\"1440\" w:bottom=\"1440\" w:left=\"1440\"/></w:sectPr>"
        "</w:body></w:document>"
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as docx:
        docx.writestr("[Content_Types].xml", (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            "</Types>"
        ))
        docx.writestr("_rels/.rels", (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            "</Relationships>"
        ))
        docx.writestr("word/document.xml", document_xml)
