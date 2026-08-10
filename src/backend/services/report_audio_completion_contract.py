"""Shared quality gate for completed report-video artifacts."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Callable


SCHEMA_VERSION = "report_video_completion_v1"
VALID_VERTICAL_SCHEMES = ("scheme1", "scheme2", "scheme3")
DEFAULT_DURATION_TOLERANCE_SECONDS = 2.0
DEFAULT_DURATION_TOLERANCE_RATIO = 0.02
DEFAULT_PROBE_TIMEOUT_SECONDS = 20


def evaluate_report_video_completion(
    *,
    include_landscape: bool,
    vertical_schemes: list[str],
    landscape_path: str,
    vertical_paths: dict[str, str],
    audio_path: str = "",
    probe: Callable[[str], dict] | None = None,
) -> dict:
    """Return a bounded, serializable readiness summary for required videos."""

    media_probe = probe or probe_report_media
    schemes = _normalize_schemes(vertical_schemes)
    vertical_paths = vertical_paths if isinstance(vertical_paths, dict) else {}
    required = []
    if include_landscape:
        required.append(("landscape", landscape_path))
    required.extend(
        (f"vertical_{scheme}", vertical_paths.get(scheme))
        for scheme in schemes
    )

    audio_reference = _audio_reference(audio_path, media_probe) if audio_path else None
    outputs = {}
    for key, path in required:
        outputs[key] = _video_output(
            path,
            media_probe=media_probe,
            audio_reference=audio_reference,
        )

    missing = [key for key, value in outputs.items() if not value.get("ready")]
    return {
        "schema_version": SCHEMA_VERSION,
        "ready": not missing,
        "outputs": outputs,
        "missing": missing,
    }


def probe_report_media(path: str) -> dict:
    """Inspect a media file with a finite FFprobe call."""

    timeout = _positive_float(
        os.environ.get("REPORT_COMPLETION_FFPROBE_TIMEOUT_SECONDS"),
        DEFAULT_PROBE_TIMEOUT_SECONDS,
    )
    completed = subprocess.run(
        [
            os.environ.get("FFPROBE_BINARY", "ffprobe"),
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=codec_type",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    payload = json.loads(completed.stdout or "{}")
    streams = payload.get("streams") if isinstance(payload, dict) else []
    streams = streams if isinstance(streams, list) else []
    format_payload = payload.get("format") if isinstance(payload, dict) else {}
    format_payload = format_payload if isinstance(format_payload, dict) else {}
    return {
        "duration": _non_negative_float(format_payload.get("duration")),
        "video_streams": sum(
            1 for stream in streams
            if isinstance(stream, dict) and stream.get("codec_type") == "video"
        ),
        "audio_streams": sum(
            1 for stream in streams
            if isinstance(stream, dict) and stream.get("codec_type") == "audio"
        ),
    }


def _audio_reference(path, media_probe):
    basic = _file_check(path)
    if basic["reason"]:
        return {"ready": False, "reason": f"audio_{basic['reason']}"}
    try:
        profile = _normalize_profile(media_probe(basic["path"]))
    except Exception as exc:
        return {
            "ready": False,
            "reason": "audio_probe_failed",
            "error": _compact_error(exc),
        }
    if profile["audio_streams"] < 1:
        return {"ready": False, "reason": "audio_missing_audio_stream"}
    if profile["duration"] <= 0:
        return {"ready": False, "reason": "audio_invalid_duration"}
    return {
        "ready": True,
        "reason": None,
        "duration": profile["duration"],
    }


def _video_output(path, *, media_probe, audio_reference):
    basic = _file_check(path)
    summary = {
        "ready": False,
        "path": basic["path"],
        "reason": basic["reason"],
    }
    if basic["reason"]:
        return summary
    if audio_reference and not audio_reference.get("ready"):
        summary["reason"] = audio_reference.get("reason") or "audio_reference_invalid"
        if audio_reference.get("error"):
            summary["error"] = audio_reference["error"]
        return summary
    try:
        profile = _normalize_profile(media_probe(basic["path"]))
    except Exception as exc:
        summary.update(reason="probe_failed", error=_compact_error(exc))
        return summary
    summary["duration"] = profile["duration"]
    if profile["video_streams"] < 1:
        summary["reason"] = "missing_video_stream"
        return summary
    if profile["audio_streams"] < 1:
        summary["reason"] = "missing_audio_stream"
        return summary
    if profile["duration"] <= 0:
        summary["reason"] = "invalid_duration"
        return summary
    if audio_reference:
        audio_duration = float(audio_reference["duration"])
        tolerance = max(
            _positive_float(
                os.environ.get("REPORT_COMPLETION_DURATION_TOLERANCE_SECONDS"),
                DEFAULT_DURATION_TOLERANCE_SECONDS,
            ),
            audio_duration * _positive_float(
                os.environ.get("REPORT_COMPLETION_DURATION_TOLERANCE_RATIO"),
                DEFAULT_DURATION_TOLERANCE_RATIO,
            ),
        )
        summary["audio_duration"] = audio_duration
        summary["duration_tolerance"] = round(tolerance, 3)
        if abs(profile["duration"] - audio_duration) > tolerance:
            summary["reason"] = "duration_mismatch"
            return summary
    summary.update(ready=True, reason=None)
    return summary


def _file_check(path):
    text = str(path or "").strip()
    if not text:
        return {"path": "", "reason": "missing_path"}
    bounded = text[:600]
    candidate = Path(text).expanduser()
    try:
        if not candidate.is_file():
            return {"path": bounded, "reason": "file_missing"}
        if candidate.stat().st_size <= 0:
            return {"path": bounded, "reason": "empty_file"}
    except OSError:
        return {"path": bounded, "reason": "file_unreadable"}
    return {"path": bounded, "reason": None}


def _normalize_schemes(value):
    raw = value if isinstance(value, (list, tuple, set)) else [value]
    normalized = []
    for item in raw:
        scheme = str(item or "").strip().lower()
        if scheme in VALID_VERTICAL_SCHEMES and scheme not in normalized:
            normalized.append(scheme)
    return normalized


def _normalize_profile(value):
    profile = value if isinstance(value, dict) else {}
    return {
        "duration": _non_negative_float(profile.get("duration")),
        "video_streams": _non_negative_int(profile.get("video_streams")),
        "audio_streams": _non_negative_int(profile.get("audio_streams")),
    }


def _non_negative_float(value):
    try:
        return round(max(0.0, float(value or 0)), 3)
    except (TypeError, ValueError):
        return 0.0


def _positive_float(value, default):
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = float(default)
    return parsed if parsed > 0 else float(default)


def _non_negative_int(value):
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _compact_error(exc):
    return str(exc or "media probe failed").strip()[:240]
