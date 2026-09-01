#!/usr/bin/env python3
"""Validate, synthesize, cache, and merge a Doubao dialogue manifest."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import wave
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "doubao-dialogue-v1"
DEFAULT_MODEL = "seed-tts-2.0-standard"
DEFAULT_RESOURCE_ID = "seed-tts-2.0"
PROJECT_CONFIG_NAME = ".doubao-dialogue-tts.json"
PROJECT_CONFIG_SCHEMA = "doubao-dialogue-project-v1"
PROFILE_SCHEMA = "doubao-dialogue-channel-profile-v1"
PROFILE_DIR = Path(__file__).resolve().parent.parent / "references" / "channel-profiles"
GENERIC_PROFILE_IDS = {"generic", "none", "off"}
PRODUCTION_LABELS = (
    "【插入点",
    "原文播放范围",
    "第一段精读",
    "第二段精读",
    "第三段精读",
    "第四段精读",
    "第五段精读",
)


class ManifestError(RuntimeError):
    pass


def read_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"Cannot read {label} {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ManifestError(f"{label} root must be an object: {path}")
    return data


def find_project_config(start: Path) -> Path | None:
    current = start.resolve()
    if current.is_file() or current.suffix:
        current = current.parent
    for directory in (current, *current.parents):
        candidate = directory / PROJECT_CONFIG_NAME
        if candidate.is_file():
            return candidate
    return None


def load_channel_profile(profile_id: str) -> dict[str, Any]:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", profile_id):
        raise ManifestError(f"invalid profile_id: {profile_id!r}")
    path = PROFILE_DIR / f"{profile_id}.json"
    profile = read_json_object(path, "channel profile")
    if profile.get("schema_version") != PROFILE_SCHEMA:
        raise ManifestError(
            f"channel profile schema_version must be {PROFILE_SCHEMA}: {path}"
        )
    if profile.get("profile_id") != profile_id:
        raise ManifestError(
            f"channel profile id mismatch: expected {profile_id!r}, "
            f"got {profile.get('profile_id')!r}"
        )
    return profile


def resolve_profile(
    manifest: dict[str, Any], manifest_path: Path
) -> tuple[dict[str, Any] | None, dict[str, str]]:
    editorial = manifest.get("editorial")
    if editorial is not None and not isinstance(editorial, dict):
        raise ManifestError("editorial must be an object")
    editorial = editorial or {}
    if "profile_id" in editorial:
        profile_id = str(editorial.get("profile_id") or "").strip()
        if not profile_id:
            raise ManifestError("editorial.profile_id cannot be empty")
        if profile_id in GENERIC_PROFILE_IDS:
            return None, {"id": "generic", "source": "manifest_override"}
        return load_channel_profile(profile_id), {
            "id": profile_id,
            "source": "manifest_override",
        }

    config_path = find_project_config(manifest_path)
    if config_path is None:
        config_path = find_project_config(Path.cwd())
    if config_path is None:
        return None, {"id": "generic", "source": "generic_default"}

    config = read_json_object(config_path, "project profile config")
    if config.get("schema_version") != PROJECT_CONFIG_SCHEMA:
        raise ManifestError(
            f"project profile schema_version must be {PROJECT_CONFIG_SCHEMA}: "
            f"{config_path}"
        )
    profile_id = str(config.get("default_profile") or "").strip()
    if not profile_id:
        raise ManifestError(f"default_profile is required: {config_path}")
    if profile_id in GENERIC_PROFILE_IDS:
        return None, {"id": "generic", "source": "project_default"}
    return load_channel_profile(profile_id), {
        "id": profile_id,
        "source": "project_default",
    }


def apply_profile_defaults(
    manifest: dict[str, Any], profile: dict[str, Any] | None
) -> tuple[dict[str, Any], dict[str, str]]:
    effective = copy.deepcopy(manifest)
    supplied_voices = manifest.get("voices")
    supplied_voices = supplied_voices if isinstance(supplied_voices, dict) else {}
    profile_voices = profile.get("voices") if profile else {}
    profile_voices = profile_voices if isinstance(profile_voices, dict) else {}
    effective_voices: dict[str, dict[str, Any]] = {}
    speed_sources: dict[str, str] = {}
    for role in dict.fromkeys((*profile_voices.keys(), *supplied_voices.keys())):
        profile_voice = profile_voices.get(role)
        supplied_voice = supplied_voices.get(role)
        profile_voice = profile_voice if isinstance(profile_voice, dict) else {}
        supplied_voice = supplied_voice if isinstance(supplied_voice, dict) else {}
        effective_voices[role] = {**profile_voice, **supplied_voice}
        if "speed" in supplied_voice:
            speed_sources[role] = "manifest_override"
        elif "speed" in profile_voice:
            speed_sources[role] = "profile_default"
        else:
            speed_sources[role] = "generic_default"
    effective["voices"] = effective_voices
    return effective, speed_sources


def validate_profile_speech(
    profile: dict[str, Any] | None, segments: list[dict[str, Any]]
) -> None:
    if profile is None:
        return
    channel = profile.get("channel")
    editorial = profile.get("editorial")
    channel = channel if isinstance(channel, dict) else {}
    editorial = editorial if isinstance(editorial, dict) else {}
    fixed_opening = str(channel.get("fixed_opening") or "").strip()
    opening = next(
        (
            item
            for item in segments
            if item.get("role") == "host" and item.get("kind") == "opening"
        ),
        None,
    )
    if fixed_opening and (
        opening is None or not str(opening.get("text") or "").startswith(fixed_opening)
    ):
        raise ManifestError(
            f"profile {profile['profile_id']} requires fixed opening: {fixed_opening}"
        )

    aliases = editorial.get("spoken_author_aliases")
    aliases = aliases if isinstance(aliases, dict) else {}
    forbidden = editorial.get("forbidden_host_phrases")
    forbidden = forbidden if isinstance(forbidden, list) else []
    for segment in segments:
        if segment.get("role") != "host":
            continue
        text = str(segment.get("text") or "")
        for full_name, spoken_alias in aliases.items():
            if str(full_name) in text:
                raise ManifestError(
                    f"host segment {segment['id']} uses {full_name!r}; "
                    f"profile requires spoken alias {spoken_alias!r}"
                )
        for phrase in forbidden:
            if str(phrase) and str(phrase) in text:
                raise ManifestError(
                    f"host segment {segment['id']} contains backstage process "
                    f"language: {phrase}"
                )


def request_model(data: dict[str, Any]) -> str:
    model = str(data.get("model") or DEFAULT_MODEL).strip() or DEFAULT_MODEL
    if model == DEFAULT_RESOURCE_ID:
        raise ManifestError(
            f"model {model!r} is the Resource ID, not the request model; "
            f"use {DEFAULT_MODEL!r} in the manifest"
        )
    return model


def load_env_file(path: Path | None) -> None:
    if not path:
        return
    if not path.is_file():
        raise ManifestError(f"Environment file does not exist: {path}")
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value[:1] in {"'", '"'} and value[-1:] == value[:1]:
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value


def read_manifest(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"Cannot read manifest: {exc}") from exc
    if not isinstance(data, dict):
        raise ManifestError("Manifest root must be an object")
    return data


def validate_manifest(
    data: dict[str, Any], profile: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    if data.get("schema_version") != SCHEMA_VERSION:
        raise ManifestError(f"schema_version must be {SCHEMA_VERSION}")
    if not str(data.get("title") or "").strip():
        raise ManifestError("title is required")
    request_model(data)
    voices = data.get("voices")
    segments = data.get("segments")
    if not isinstance(voices, dict) or not voices:
        raise ManifestError("voices must be a non-empty object")
    if not isinstance(segments, list) or not segments:
        raise ManifestError("segments must be a non-empty array")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(segments, 1):
        if not isinstance(raw, dict):
            raise ManifestError(f"segment {index} must be an object")
        segment_id = str(raw.get("id") or "").strip()
        expected = f"{index:03d}"
        if segment_id != expected:
            raise ManifestError(
                f"segment {index} id must be contiguous and zero-padded: {expected}"
            )
        if segment_id in seen:
            raise ManifestError(f"duplicate segment id: {segment_id}")
        seen.add(segment_id)
        role = str(raw.get("role") or "").strip()
        if role not in voices:
            raise ManifestError(f"segment {segment_id} has unknown role: {role}")
        voice = voices[role]
        if not isinstance(voice, dict) or not str(voice.get("voice_id") or "").strip():
            raise ManifestError(f"role {role} has no voice_id")
        text = str(raw.get("text") or "").strip()
        if not text:
            raise ManifestError(f"segment {segment_id} text is empty")
        for label in PRODUCTION_LABELS:
            if label in text:
                raise ManifestError(
                    f"segment {segment_id} contains unspoken production label: {label}"
                )
        slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(raw.get("slug") or "")).strip("-")
        if not slug:
            slug = f"segment-{segment_id}"
        pause = int(raw.get("pause_after_ms", 500))
        if pause < 0 or pause > 10000:
            raise ManifestError(f"segment {segment_id} pause_after_ms is invalid")
        normalized.append(
            {
                **raw,
                "id": segment_id,
                "role": role,
                "slug": slug,
                "text": text,
                "pause_after_ms": pause,
            }
        )
    validate_profile_speech(profile, normalized)
    return normalized


def prepare_manifest(
    manifest: dict[str, Any], manifest_path: Path
) -> tuple[
    dict[str, Any],
    list[dict[str, Any]],
    dict[str, str],
    dict[str, str],
]:
    profile, profile_context = resolve_profile(manifest, manifest_path)
    effective, speed_sources = apply_profile_defaults(manifest, profile)
    editorial = effective.get("editorial")
    editorial = editorial if isinstance(editorial, dict) else {}
    editorial["profile_id"] = profile_context["id"]
    effective["editorial"] = editorial
    effective["model"] = request_model(effective)
    segments = validate_manifest(effective, profile)
    effective["segments"] = segments
    return effective, segments, profile_context, speed_sources


def split_text(text: str, limit: int) -> list[str]:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return [text]
    sentences = [
        item.strip()
        for item in re.findall(r".+?(?:[。！？!?；;]|$)", text)
        if item.strip()
    ]
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        if len(sentence) > limit:
            if current:
                chunks.append(current)
                current = ""
            for start in range(0, len(sentence), limit):
                chunks.append(sentence[start : start + limit])
            continue
        candidate = current + sentence
        if current and len(candidate) > limit:
            chunks.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def settings_for(
    manifest: dict[str, Any], segment: dict[str, Any]
) -> dict[str, Any]:
    audio = manifest.get("audio") if isinstance(manifest.get("audio"), dict) else {}
    voice = manifest["voices"][segment["role"]]
    return {
        "voice_id": str(voice["voice_id"]),
        "voice_label": str(voice.get("label") or segment["role"]),
        "model": request_model(manifest),
        "sample_rate": int(audio.get("sample_rate") or 24000),
        "bit_rate": int(audio.get("bit_rate") or 128000),
        "chunk_chars": max(200, min(int(audio.get("chunk_chars") or 450), 1000)),
        "speed": float(voice.get("speed") or 1.0),
        "loudness": float(voice.get("loudness") or 1.0),
        "emotion": str(voice.get("emotion") or ""),
        "emotion_scale": int(voice.get("emotion_scale") or 4),
    }


def segment_hash(segment: dict[str, Any], settings: dict[str, Any]) -> str:
    payload = {
        "text": segment["text"],
        "settings": settings,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def run_command(args: list[str], label: str) -> None:
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode:
        detail = (result.stderr or result.stdout or "").strip()[-1200:]
        raise RuntimeError(f"{label} failed: {detail}")


def media_duration(path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode:
        return 0.0
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0


def merge_chunks(chunk_paths: list[Path], wav_path: Path, mp3_path: Path) -> None:
    concat_path = wav_path.with_suffix(".concat.txt")
    concat_path.write_text(
        "\n".join(
            "file '" + str(path.resolve()).replace("'", "'\\''") + "'"
            for path in chunk_paths
        )
        + "\n",
        encoding="utf-8",
    )
    run_command(
        [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "24000",
            "-c:a",
            "pcm_s16le",
            str(wav_path),
        ],
        "segment WAV merge",
    )
    run_command(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(wav_path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "24000",
            "-b:a",
            "128k",
            str(mp3_path),
        ],
        "segment MP3 encode",
    )
    concat_path.unlink(missing_ok=True)


def synthesize_segment(
    client: Any,
    manifest: dict[str, Any],
    segment: dict[str, Any],
    output_dir: Path,
    retries: int,
) -> dict[str, Any]:
    settings = settings_for(manifest, segment)
    digest = segment_hash(segment, settings)
    label = re.sub(r"[^\w-]+", "-", settings["voice_label"]).strip("-")
    stem = f"{segment['id']}_{segment['slug']}_{label or segment['role']}"
    segment_dir = output_dir / "segments"
    chunk_dir = output_dir / ".chunks" / stem
    segment_dir.mkdir(parents=True, exist_ok=True)
    chunk_dir.mkdir(parents=True, exist_ok=True)
    wav_path = segment_dir / f"{stem}.wav"
    mp3_path = segment_dir / f"{stem}.mp3"
    meta_path = segment_dir / f"{stem}.json"
    if wav_path.is_file() and mp3_path.is_file() and meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            meta = {}
        if (
            meta.get("hash") == digest
            and media_duration(wav_path) > 0
            and media_duration(mp3_path) > 0
        ):
            return {**meta, "status": "reused"}

    chunks = split_text(segment["text"], settings["chunk_chars"])
    chunk_paths: list[Path] = []
    for index, text in enumerate(chunks, 1):
        chunk_hash = hashlib.sha256(
            f"{digest}:{index}:{text}".encode()
        ).hexdigest()[:16]
        chunk_path = chunk_dir / f"{index:03d}_{chunk_hash}.mp3"
        if not chunk_path.is_file() or media_duration(chunk_path) <= 0:
            error: Exception | None = None
            for attempt in range(1, retries + 2):
                try:
                    response = client.synthesize(
                        text,
                        settings["voice_id"],
                        model=settings["model"],
                        sample_rate=settings["sample_rate"],
                        bit_rate=settings["bit_rate"],
                        speed=settings["speed"],
                        loudness=settings["loudness"],
                        emotion=settings["emotion"],
                        emotion_scale=settings["emotion_scale"],
                        uid=f"dialogue_{segment['id']}_{index}",
                    )
                    chunk_path.write_bytes(response.audio)
                    if media_duration(chunk_path) <= 0:
                        raise RuntimeError("generated chunk is not decodable")
                    error = None
                    break
                except Exception as exc:  # network/API errors are retryable
                    error = exc
                    if attempt <= retries:
                        time.sleep(min(2**attempt, 8))
            if error is not None:
                raise RuntimeError(
                    f"segment {segment['id']} chunk {index} failed: {error}"
                ) from error
        chunk_paths.append(chunk_path)

    merge_chunks(chunk_paths, wav_path, mp3_path)
    meta = {
        "id": segment["id"],
        "slug": segment["slug"],
        "role": segment["role"],
        "kind": segment.get("kind"),
        "voice_label": settings["voice_label"],
        "voice_id": settings["voice_id"],
        "hash": digest,
        "characters": len(segment["text"]),
        "chunks": len(chunks),
        "pause_after_ms": segment["pause_after_ms"],
        "wav_path": str(wav_path.resolve()),
        "mp3_path": str(mp3_path.resolve()),
        "duration_seconds": round(media_duration(wav_path), 3),
        "status": "generated",
    }
    meta_path.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return meta


def combine_program(items: list[dict[str, Any]], output_dir: Path) -> dict[str, Any]:
    wav_path = output_dir / "complete-program.wav"
    mp3_path = output_dir / "complete-program.mp3"
    params = None
    frames_written = 0
    with wave.open(str(wav_path), "wb") as writer:
        for item in items:
            with wave.open(item["wav_path"], "rb") as reader:
                current = (
                    reader.getnchannels(),
                    reader.getsampwidth(),
                    reader.getframerate(),
                )
                if params is None:
                    params = current
                    writer.setnchannels(current[0])
                    writer.setsampwidth(current[1])
                    writer.setframerate(current[2])
                elif current != params:
                    raise RuntimeError(f"WAV parameter mismatch: {item['wav_path']}")
                frames = reader.readframes(reader.getnframes())
                writer.writeframes(frames)
                frames_written += reader.getnframes()
                pause_frames = round(
                    reader.getframerate() * item["pause_after_ms"] / 1000
                )
                writer.writeframes(
                    b"\x00"
                    * pause_frames
                    * reader.getnchannels()
                    * reader.getsampwidth()
                )
                frames_written += pause_frames
    if not params:
        raise RuntimeError("No segment WAV files to combine")
    run_command(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(wav_path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "24000",
            "-b:a",
            "128k",
            str(mp3_path),
        ],
        "complete MP3 encode",
    )
    duration = frames_written / params[2]
    return {
        "wav_path": str(wav_path.resolve()),
        "mp3_path": str(mp3_path.resolve()),
        "duration_seconds": round(duration, 3),
        "segments": len(items),
    }


def write_report(
    output_dir: Path,
    *,
    status: str,
    summary: dict[str, Any],
    items: list[dict[str, Any]],
    failures: list[dict[str, str]],
    program: dict[str, Any] | None,
) -> None:
    report = {
        "status": status,
        "summary": summary,
        "items": items,
        "failures": failures,
        "program": program,
    }
    target = output_dir / "generation-report.json"
    temporary = output_dir / "generation-report.json.tmp"
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(target)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--retries", type=int, default=2)
    args = parser.parse_args()

    manifest = read_manifest(args.manifest)
    effective, segments, profile_context, speed_sources = prepare_manifest(
        manifest, args.manifest
    )
    total_chars = sum(len(item["text"]) for item in segments)
    counts: dict[str, int] = {}
    for item in segments:
        counts[item["role"]] = counts.get(item["role"], 0) + 1
    summary = {
        "title": effective["title"],
        "model": request_model(effective),
        "segments": len(segments),
        "characters": total_chars,
        "roles": counts,
        "voices": {
            role: str(config.get("voice_id") or "")
            for role, config in effective["voices"].items()
            if isinstance(config, dict)
        },
        "voice_settings": {
            role: {
                "label": str(config.get("label") or role),
                "voice_id": str(config.get("voice_id") or ""),
                "speed": float(
                    config["speed"] if config.get("speed") is not None else 1.0
                ),
                "speed_source": speed_sources.get(role, "generic_default"),
            }
            for role, config in effective["voices"].items()
            if isinstance(config, dict)
        },
        "profile": profile_context,
        "api_billing_boundary": "first_external_tts_request",
        "external_data_transfer": (
            "not_started_dry_run"
            if args.dry_run
            else "starts_with_first_external_tts_request"
        ),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.dry_run:
        return 0

    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise RuntimeError("ffmpeg and ffprobe are required")
    load_env_file(args.env_file)
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "effective-manifest.json").write_text(
        json.dumps(effective, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    from doubao_client import DoubaoClient

    client = DoubaoClient()
    items: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    write_report(
        output_dir,
        status="processing",
        summary=summary,
        items=items,
        failures=failures,
        program=None,
    )
    for segment in segments:
        print(
            f"[{segment['id']}/{len(segments):03d}] "
            f"{segment['role']} {len(segment['text'])} chars",
            flush=True,
        )
        try:
            item = synthesize_segment(
                client,
                effective,
                segment,
                output_dir,
                max(0, min(args.retries, 5)),
            )
            items.append(item)
            write_report(
                output_dir,
                status="processing",
                summary=summary,
                items=items,
                failures=failures,
                program=None,
            )
        except Exception as exc:
            failures.append({"id": segment["id"], "error": str(exc)})
            write_report(
                output_dir,
                status="failed",
                summary=summary,
                items=items,
                failures=failures,
                program=None,
            )
            break

    program = None
    if not failures and len(items) == len(segments):
        program = combine_program(items, output_dir)
    write_report(
        output_dir,
        status="completed" if program else "failed",
        summary=summary,
        items=items,
        failures=failures,
        program=program,
    )
    if failures:
        print(json.dumps(failures, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1
    print(json.dumps(program, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
