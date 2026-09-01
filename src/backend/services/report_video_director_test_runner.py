"""Isolated end-to-end runner for the phase-one report-video director.

The runner reuses the existing visual-plan preview renderer and FFmpeg concat
renderer. It never touches the database, Celery, Gemini, image generation, or
publish APIs. It requires an explicit ``test:`` task id and a caller-provided
isolated output directory.
"""

from __future__ import annotations

import json
import hashlib
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any, Dict, Sequence

from PIL import Image, ImageDraw, ImageFont, ImageOps

from services.report_video_director import (
    build_director_package,
    evaluate_full_render_gate,
)
from services.report_video_render_service import make_concat_video, media_duration
from services.report_video_render_service import escape_subtitle_filter_path
from services.report_visual_plan_service import (
    normalize_visual_plan,
    render_visual_plan_previews,
)


TEST_RUN_SCHEMA_VERSION = "report_video_director_test_run_v1"
FULL_TEST_RUN_SCHEMA_VERSION = "report_video_director_full_test_run_v1"
PROJECT_ROOT = Path(__file__).resolve().parents[3]
_FORBIDDEN_OUTPUT_ROOTS = (
    PROJECT_ROOT / "data" / "processed",
    PROJECT_ROOT / "data" / "tasks",
    PROJECT_ROOT / "runtime",
)
_EVIDENCE_MEDIA_TYPES = frozenset(
    {"original_pdf", "pdf_highlight", "pdf_scroll", "chart"}
)
_AUDIENCE_FORBIDDEN_RE = re.compile(
    r"(?:\bevidence\b|\bpdf\s*evidence\b|\beditor\b|\bdirector\b|"
    r"\bpreview\b|\bdetail\b|\btask\b|\bapproval\b|"
    r"制作元数据|制作后台|编辑状态|素材类型|任务标签|审批标签)",
    re.IGNORECASE,
)


def run_isolated_director_test(
    material: Dict[str, Any],
    evidence_map: Sequence[Dict[str, Any]],
    *,
    pdf_path: str,
    output_root: str,
    test_task_id: str,
    target_segment_chars: int = 130,
) -> Dict[str, Any]:
    """Run contract -> keyframes -> 2-3 clips -> stitched test reel."""

    test_task_id = str(test_task_id or "").strip()
    if not test_task_id.startswith("test:"):
        raise ValueError("隔离测试任务 ID 必须以 test: 开头")
    root = Path(output_root).resolve()
    _validate_test_output_root(root)
    root.mkdir(parents=True, exist_ok=True)
    pdf = Path(pdf_path).resolve()
    if not pdf.is_file() or pdf.suffix.lower() != ".pdf":
        raise ValueError("测试 PDF 不存在或不是 PDF")

    material = dict(material or {})
    audio_path = Path(str(material.get("audio_path") or "")).resolve()
    reusable_audio = audio_path if audio_path.is_file() else None
    original_material_task_id = str(
        material.get("job_id") or material.get("source_task_id") or test_task_id
    ).strip()
    material["pdf"] = {
        **(material.get("pdf") if isinstance(material.get("pdf"), dict) else {}),
        "path": str(pdf),
    }
    source_label = _source_label(material, pdf)
    source_trace = _source_trace(
        material,
        evidence_map,
        pdf,
        original_material_task_id=original_material_task_id,
    )
    package = build_director_package(
        material,
        evidence_map,
        source_task_id=original_material_task_id,
        package_id=f"director:{test_task_id}",
        execution_mode="test",
        target_segment_chars=target_segment_chars,
    )
    if not package["validation"]["valid"]:
        return {
            "schema_version": TEST_RUN_SCHEMA_VERSION,
            "status": "director_package_invalid",
            "test_task_id": test_task_id,
            "director_package": package,
            "publish_attempted": False,
            "production_task_mutated": False,
        }
    audience_copy_audit = _audit_audience_copy(package, source_label)

    preview_root = root / "existing_visual_plan"
    visual_plan = normalize_visual_plan({}, material)
    preview_assets = render_visual_plan_previews(
        visual_plan,
        material,
        pdf_path=str(pdf),
        job_id="director-preview",
        output_root=preview_root,
    )
    source_frames = {
        item["kind"]: Path(item["path"]).resolve()
        for item in preview_assets
        if Path(str(item.get("path") or "")).is_file()
    }
    if not source_frames:
        raise RuntimeError("现有 visual_plan 未生成任何预览帧")

    artifacts = {"keyframes": [], "preview_clips": []}
    segments_by_id = {
        str(item.get("segment_id")): item
        for item in package["segments"]
        if isinstance(item, dict) and item.get("segment_id")
    }
    keyframe_root = root / "keyframes"
    keyframe_root.mkdir(parents=True, exist_ok=True)
    for index, planned in enumerate(package["preview_gate"]["keyframe_plan"], start=1):
        target = keyframe_root / f"{planned['preview_id']}.jpg"
        size = (1080, 1920) if planned["ratio"] == "9:16" else (1920, 1080)
        _render_planned_frame(
            planned,
            segments_by_id,
            source_frames,
            pdf,
            target,
            size,
            source_label,
            package.get("titles") or {},
        )
        artifacts["keyframes"].append(
            {
                "preview_id": planned["preview_id"],
                "status": "completed",
                "artifact_id": f"{test_task_id}:keyframe:{index}",
                "file_path": str(target),
                "ratio": planned["ratio"],
                "bytes": target.stat().st_size,
                "audience_source_attribution": (
                    source_label
                    if planned.get("media_type") in _EVIDENCE_MEDIA_TYPES
                    else None
                ),
            }
        )

    clip_root = root / "preview_clips"
    clip_root.mkdir(parents=True, exist_ok=True)
    clip_frames = []
    audio_windows = []
    source_audio_duration = (
        float(media_duration(reusable_audio) or 0.0)
        if reusable_audio
        else 0.0
    )
    for index, planned in enumerate(package["preview_gate"]["preview_clip_plan"], start=1):
        ratio = planned["ratio"]
        size = (1080, 1920) if ratio == "9:16" else (1920, 1080)
        clip_frame = clip_root / f"{planned['preview_id']}.jpg"
        _render_planned_frame(
            planned,
            segments_by_id,
            source_frames,
            pdf,
            clip_frame,
            size,
            source_label,
            package.get("titles") or {},
        )
        clip_path = clip_root / f"{planned['preview_id']}.mp4"
        duration = min(5.0, float(planned["duration_seconds"]))
        make_concat_video(
            [(clip_frame, duration)],
            clip_path,
            size[0],
            size[1],
            transition_duration=0.0,
        )
        audio_window = None
        if reusable_audio and source_audio_duration > 0:
            offset = _segment_audio_offset(
                package["segments"],
                str(planned.get("segment_id") or ""),
                source_audio_duration,
                duration,
            )
            _mux_audio_excerpt(
                clip_path,
                reusable_audio,
                offset_seconds=offset,
                duration_seconds=duration,
            )
            audio_window = {
                "source_audio_path": str(reusable_audio),
                "offset_seconds": round(offset, 3),
                "duration_seconds": round(duration, 3),
            }
            audio_windows.append(audio_window)
        measured = media_duration(clip_path)
        if not clip_path.is_file() or not measured or measured < 3.0:
            raise RuntimeError(f"代表样片校验失败：{planned['preview_id']}")
        artifacts["preview_clips"].append(
            {
                "preview_id": planned["preview_id"],
                "status": "completed",
                "artifact_id": f"{test_task_id}:clip:{index}",
                "file_path": str(clip_path),
                "ratio": ratio,
                "duration_seconds": round(measured, 3),
                "bytes": clip_path.stat().st_size,
                "audience_source_attribution": (
                    source_label
                    if planned.get("media_type") in _EVIDENCE_MEDIA_TYPES
                    else None
                ),
                "audio_reuse": audio_window,
            }
        )
        if ratio == "16:9":
            clip_frames.append((clip_frame, duration))
        else:
            adapted = clip_root / f"{clip_frame.stem}_landscape.jpg"
            _fit_frame(clip_frame, adapted, (1920, 1080), contain=True)
            clip_frames.append((adapted, duration))

    authorization = {
        "isolated": True,
        "publish_allowed": False,
        "production_task_mutation_allowed": False,
        "test_task_id": test_task_id,
        "output_root": str(root),
    }
    gate = evaluate_full_render_gate(
        package,
        preview_artifacts=artifacts,
        execution_mode="test",
        test_authorization=authorization,
    )
    if not gate["allowed"]:
        return {
            "schema_version": TEST_RUN_SCHEMA_VERSION,
            "status": "blocked",
            "test_task_id": test_task_id,
            "director_package": package,
            "preview_assets": preview_assets,
            "preview_artifacts": artifacts,
            "full_render_gate": gate,
            "publish_attempted": False,
            "production_task_mutated": False,
        }

    reel_path = root / "director_test_reel_16x9.mp4"
    make_concat_video(
        clip_frames[:3],
        reel_path,
        1920,
        1080,
        transition_duration=0.18,
        motion=True,
    )
    preview_audio_path = None
    if reusable_audio and audio_windows:
        preview_audio_path = root / "preview_audio.m4a"
        _build_preview_audio_track(
            reusable_audio,
            audio_windows,
            preview_audio_path,
        )
        _mux_audio_track(reel_path, preview_audio_path)
    reel_duration = media_duration(reel_path)
    if not reel_path.is_file() or not reel_duration or reel_duration < 6.0:
        raise RuntimeError("隔离测试合成片校验失败")

    manifest = {
        "schema_version": TEST_RUN_SCHEMA_VERSION,
        "status": "completed",
        "execution_mode": "test",
        "test_task_id": test_task_id,
        "safety": authorization,
        "source_trace": source_trace,
        "director_package": package,
        "preview_assets": preview_assets,
        "preview_artifacts": artifacts,
        "full_render_gate": gate,
        "audience_copy_audit": audience_copy_audit,
        "test_reel": {
            "file_path": str(reel_path),
            "duration_seconds": round(reel_duration, 3),
            "bytes": reel_path.stat().st_size,
            "renderer": "existing_report_video_render_service.make_concat_video",
            "audio_reused": bool(preview_audio_path),
            "audio_source_path": str(reusable_audio) if reusable_audio else None,
            "audio_windows": audio_windows,
        },
        "publish_attempted": False,
        "production_task_mutated": False,
    }
    manifest_path = root / "run_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    manifest["manifest_path"] = str(manifest_path)
    return manifest


def run_isolated_director_full_render(
    material: Dict[str, Any],
    evidence_map: Sequence[Dict[str, Any]],
    *,
    pdf_path: str,
    output_root: str,
    test_task_id: str,
    target_segment_chars: int = 130,
    audio_alignment: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Render a complete isolated video after the real preview gate passes."""

    root = Path(output_root).resolve()
    preview_result = run_isolated_director_test(
        material,
        evidence_map,
        pdf_path=pdf_path,
        output_root=str(root / "preview"),
        test_task_id=test_task_id,
        target_segment_chars=target_segment_chars,
    )
    if preview_result.get("status") != "completed":
        return {
            "schema_version": FULL_TEST_RUN_SCHEMA_VERSION,
            "status": "preview_failed",
            "preview_result": preview_result,
            "publish_attempted": False,
            "production_task_mutated": False,
        }
    if not preview_result.get("full_render_gate", {}).get("allowed"):
        return {
            "schema_version": FULL_TEST_RUN_SCHEMA_VERSION,
            "status": "blocked",
            "preview_result": preview_result,
            "publish_attempted": False,
            "production_task_mutated": False,
        }

    package = preview_result["director_package"]
    material = dict(material or {})
    audio = Path(str(material.get("audio_path") or "")).resolve()
    if not audio.is_file():
        raise ValueError("完整隔离渲染必须复用已存在的真实音频")
    audio_duration = float(media_duration(audio) or 0.0)
    if audio_duration <= 0:
        raise ValueError("真实音频时长无效")
    reusable_visual_track = Path(
        str(material.get("reuse_visual_track_path") or "")
    ).resolve()
    if (
        not reusable_visual_track.is_file()
        or abs(float(media_duration(reusable_visual_track) or 0.0) - audio_duration) > 0.35
    ):
        reusable_visual_track = None
    pdf = Path(pdf_path).resolve()
    source_label = _source_label(material, pdf)
    hyperframes_hook = Path(
        str(material.get("hyperframes_hook_path") or "")
    ).resolve()
    if not hyperframes_hook.is_file():
        hyperframes_hook = None
    source_frames = {
        item["kind"]: Path(item["path"]).resolve()
        for item in preview_result.get("preview_assets") or []
        if isinstance(item, dict) and Path(str(item.get("path") or "")).is_file()
    }
    if not source_frames:
        raise RuntimeError("完整隔离渲染缺少既有 visual_plan 预览资产")

    full_root = root / "full_render"
    frame_root = full_root / "frames"
    frame_root.mkdir(parents=True, exist_ok=True)
    segments = package["segments"]
    segment_durations, subtitle_timing = _render_segment_durations(
        segments,
        audio_duration,
        audio_alignment,
    )
    frame_items = []
    frame_manifest = []
    climax_segment_id = _select_visual_climax(segments)
    chapter_by_start = {
        str(chapter.get("segment_ids", [None])[0]): chapter
        for chapter in package.get("chapters") or []
        if isinstance(chapter, dict)
        and isinstance(chapter.get("segment_ids"), list)
        and chapter.get("segment_ids")
    }
    for index, (segment, segment_duration) in enumerate(
        zip(segments, segment_durations),
        start=1,
    ):
        segment_id = str(segment.get("segment_id") or f"seg-{index:03d}")
        planned = {
            "segment_id": segment_id,
            "media_type": segment.get("media", {}).get("type"),
            "ratio": "16:9",
        }
        base_frame = frame_root / f"{index:02d}-{segment_id}-01-base.jpg"
        chapter = chapter_by_start.get(segment_id)
        if chapter and index > 1:
            _render_chapter_card(
                base_frame,
                (1920, 1080),
                chapter=chapter,
                segment=segment,
            )
        else:
            _render_planned_frame(
                planned,
                {segment_id: segment},
                source_frames,
                pdf,
                base_frame,
                (1920, 1080),
                source_label,
                package.get("titles") or {},
                show_source_attribution=False,
            )
        detail_frame = frame_root / f"{index:02d}-{segment_id}-02-detail.jpg"
        evidence_pages = [
            int(item.get("page"))
            for item in segment.get("evidence_refs") or []
            if isinstance(item, dict) and str(item.get("page") or "").isdigit()
        ]
        if planned["media_type"] in _EVIDENCE_MEDIA_TYPES and evidence_pages:
            _render_evidence_detail(
                pdf,
                evidence_pages[0],
                detail_frame,
                (1920, 1080),
                segment=segment,
                source_label=source_label,
            )
        elif chapter and index > 1:
            _render_planned_frame(
                planned,
                {segment_id: segment},
                source_frames,
                pdf,
                detail_frame,
                (1920, 1080),
                source_label,
                package.get("titles") or {},
                show_source_attribution=False,
            )
        else:
            _render_detail_variation(
                base_frame,
                detail_frame,
                source_label=source_label,
                sequence=index,
                media_type=str(planned["media_type"] or ""),
            )
        insight_frame = frame_root / f"{index:02d}-{segment_id}-03-visual-build.jpg"
        _render_insight_card(
            insight_frame,
            (1920, 1080),
            segment=segment,
            sequence=index,
            total=len(segments),
            progress=0.42,
            is_climax=segment_id == climax_segment_id,
        )
        insight_final_frame = frame_root / f"{index:02d}-{segment_id}-04-visual-payoff.jpg"
        _render_insight_card(
            insight_final_frame,
            (1920, 1080),
            segment=segment,
            sequence=index,
            total=len(segments),
            progress=1.0,
            is_climax=segment_id == climax_segment_id,
        )
        focus_frame = frame_root / f"{index:02d}-{segment_id}-05-focus.jpg"
        _render_focus_card(
            focus_frame,
            (1920, 1080),
            segment=segment,
            sequence=index,
            total=len(segments),
            source_label=source_label,
        )
        narration = _narration_text(segment)
        if index == 1 and hyperframes_hook and segment_duration > 15.2:
            remaining = segment_duration - 15.0
            frames = (hyperframes_hook, detail_frame, insight_final_frame)
            states = (
                "hook_hyperframes",
                "source_proof",
                "visual_resolution",
            )
            state_durations = (15.0, remaining / 2.0, remaining / 2.0)
        elif index == 1:
            hook_frames = []
            for phase in (1, 2, 3):
                hook_frame = frame_root / f"{index:02d}-{segment_id}-hook-{phase}.jpg"
                _render_hook_frame(
                    hook_frame,
                    (1920, 1080),
                    segments=segments,
                    phase=phase,
                )
                hook_frames.append(hook_frame)
            state_durations = _hook_durations(segment_duration)
            frames = hook_frames[: len(state_durations)]
            states = ["hook_conflict", "hook_counterfact", "hook_question"][: len(frames)]
            if len(state_durations) == 5:
                frames.extend((detail_frame, insight_final_frame))
                states.extend(("source_proof", "visual_resolution"))
        else:
            frames = (
                base_frame,
                detail_frame,
                insight_frame,
                insight_final_frame,
                focus_frame,
            )
            states = (
                "establish",
                "source_proof",
                "visual_build",
                "visual_payoff",
                "takeaway",
            )
            state_durations = _split_state_durations(
                segment_duration,
                ["0.7", "0.9", "1.15", "1.35", "0.9"],
            )
        state_captions = _split_caption_states(narration, len(frames))
        state_rows = []
        for state, frame, duration, caption in zip(
            states,
            frames,
            state_durations,
            state_captions,
        ):
            frame_items.append((frame, duration))
            state_rows.append(
                {
                    "state": state,
                    "file_path": str(frame),
                    "duration_seconds": round(duration, 3),
                    "caption": caption,
                }
            )
        frame_manifest.append(
            {
                "segment_id": segment_id,
                "media_type": planned["media_type"],
                "chapter_id": segment.get("chapter_id"),
                "chapter_opening": bool(chapter and index > 1),
                "visual_climax": segment_id == climax_segment_id,
                "duration_seconds": round(segment_duration, 3),
                "evidence_pages": evidence_pages,
                "states": state_rows,
            }
        )

    caption_cues = _build_asr_caption_cues(
        segments,
        audio_alignment,
        audio_duration,
    )
    subtitle_path = full_root / "director_captions.srt"
    subtitle_count = _write_caption_cues_srt(caption_cues, subtitle_path)
    visual_video_path = full_root / "director_visual_track.mp4"
    caption_root = full_root / "caption_bands"
    final_video_path = full_root / "director_full_with_captions.mp4"
    if reusable_visual_track:
        shutil.copy2(reusable_visual_track, visual_video_path)
    else:
        make_concat_video(
            frame_items,
            visual_video_path,
            1920,
            1080,
            audio_path=audio,
            transition_duration=0.18,
            motion=True,
        )
    _burn_caption_bands(
        visual_video_path,
        caption_cues,
        final_video_path,
        caption_root=caption_root,
    )
    _normalize_program_audio(final_video_path)
    final_duration = float(media_duration(final_video_path) or 0.0)
    if (
        not final_video_path.is_file()
        or abs(final_duration - audio_duration) > 0.35
    ):
        raise RuntimeError("完整隔离成片时长与真实音频不一致")

    manifest = {
        "schema_version": FULL_TEST_RUN_SCHEMA_VERSION,
        "status": "completed",
        "execution_mode": "test",
        "test_task_id": test_task_id,
        "safety": preview_result["safety"],
        "source_trace": preview_result["source_trace"],
        "director_package": package,
        "full_render_gate": preview_result["full_render_gate"],
        "preview_manifest_path": preview_result["manifest_path"],
        "audience_copy_audit": preview_result["audience_copy_audit"],
        "full_video": {
            "file_path": str(final_video_path),
            "raw_file_path": str(visual_video_path),
            "duration_seconds": round(final_duration, 3),
            "bytes": final_video_path.stat().st_size,
            "width": 1920,
            "height": 1080,
            "audio_path": str(audio),
            "audio_duration_seconds": round(audio_duration, 3),
            "audio_normalization": "ebu_r128_I-16_TP-1.5_LRA11",
            "subtitles_path": str(subtitle_path),
            "subtitle_cues": subtitle_count,
            "subtitle_timing": (
                "fine_grained_asr_boundaries_with_script_preserved"
                if caption_cues
                else subtitle_timing
            ),
            "subtitle_rendering": "pillow_caption_band_video_overlay",
            "subtitle_font_px": 50,
            "subtitle_max_lines": 2,
            "subtitle_player_safe_bottom_px": 126,
            "transition_seconds": 0.18,
            "visual_state_count": len(frame_items),
            "non_card_visual_state_count": sum(
                1
                for row in frame_manifest
                for state in row.get("states") or []
                if state.get("state")
                in {
                    "hook_hyperframes",
                    "hook_conflict",
                    "hook_counterfact",
                    "hook_question",
                    "visual_build",
                    "visual_payoff",
                }
            ),
            "visual_climax_segment_id": climax_segment_id,
            "renderer": (
                "reused_isolated_visual_track_for_caption_revision"
                if reusable_visual_track
                else "existing_report_video_render_service.make_concat_video"
            ),
            "reused_visual_track_path": (
                str(reusable_visual_track)
                if reusable_visual_track
                else None
            ),
            "motion": "bounded_center_push_in",
            "hyperframes_hook_path": (
                str(hyperframes_hook)
                if hyperframes_hook
                else None
            ),
        },
        "segments": frame_manifest,
        "publish_attempted": False,
        "production_task_mutated": False,
    }
    manifest_path = root / "full_run_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    manifest["manifest_path"] = str(manifest_path)
    return manifest


def _source_frame_for_media(source_frames: Dict[str, Path], media_type: str) -> Path:
    if media_type == "title_card":
        preferred = "opening_cover"
    elif media_type in {"logic_card", "chart", "character_explainer"}:
        preferred = "logic_card"
    else:
        preferred = "pdf_preview"
    return source_frames.get(preferred) or next(iter(source_frames.values()))


def _render_planned_frame(
    planned: Dict[str, Any],
    segments_by_id: Dict[str, Dict[str, Any]],
    source_frames: Dict[str, Path],
    pdf: Path,
    target: Path,
    size,
    source_label: str,
    titles: Dict[str, Any],
    *,
    show_source_attribution: bool = True,
) -> None:
    segment = segments_by_id.get(str(planned.get("segment_id") or "")) or {}
    media_type = str(planned.get("media_type") or "")
    evidence_refs = (
        segment.get("evidence_refs")
        if isinstance(segment.get("evidence_refs"), list)
        else []
    )
    page = next(
        (
            int(item["page"])
            for item in evidence_refs
            if isinstance(item, dict) and str(item.get("page") or "").isdigit()
        ),
        None,
    )
    if media_type == "title_card":
        _render_title_card(
            target,
            size,
            segment=segment,
            titles=titles,
            source_label=source_label,
        )
        return
    if media_type == "character_explainer":
        _render_character_explainer(
            target,
            size,
            segment=segment,
            source_label=source_label,
            page_number=page,
        )
        return
    if media_type == "logic_card":
        _render_logic_card(
            target,
            size,
            segment=segment,
            source_label=source_label,
        )
        return
    if media_type in _EVIDENCE_MEDIA_TYPES and page:
        _render_evidence_composite(
            pdf,
            page,
            target,
            size,
            segment=segment,
            source_label=source_label,
            show_source_attribution=show_source_attribution,
        )
        return
    source = _source_frame_for_media(source_frames, media_type)
    _fit_frame(source, target, size)


def _render_title_card(
    target: Path,
    size,
    *,
    segment: Dict[str, Any],
    titles: Dict[str, Any],
    source_label: str,
) -> None:
    width, height = size
    canvas = Image.new("RGB", size, "#07111f")
    draw = ImageDraw.Draw(canvas, "RGBA")
    grid = max(64, round(width * 0.055))
    for x in range(0, width, grid):
        draw.line((x, 0, x, height), fill=(42, 89, 112, 30), width=1)
    for y in range(0, height, grid):
        draw.line((0, y, width, y), fill=(42, 89, 112, 30), width=1)

    margin = round(width * 0.075)
    small_font = _load_overlay_font(max(22, round(width * 0.015)))
    title_font = _load_overlay_font(max(62, round(width * 0.047)))
    body_font = _load_overlay_font(max(28, round(width * 0.021)))
    stat_font = _load_overlay_font(max(34, round(width * 0.026)))
    draw.text(
        (margin, round(height * 0.09)),
        "机构研报解读",
        font=small_font,
        fill=(111, 205, 222, 255),
    )
    draw.rectangle(
        (
            margin,
            round(height * 0.16),
            margin + round(width * 0.11),
            round(height * 0.169),
        ),
        fill=(255, 214, 87, 255),
    )
    title = str(
        titles.get("landscape")
        or titles.get("content")
        or "研报解读"
    ).replace("\n", " ")
    title_lines = _wrap_text(
        draw,
        title,
        title_font,
        round(width * 0.78),
        max_lines=3,
    )
    title_y = round(height * 0.22)
    draw.multiline_text(
        (margin, title_y),
        title_lines,
        font=title_font,
        fill=(247, 250, 252, 255),
        spacing=12,
    )
    body_y = title_y + _multiline_height(draw, title_lines, title_font, 12) + round(height * 0.07)
    narration = _audience_takeaway(segment, max_chars=28)
    narration_lines = _wrap_text(
        draw,
        str(narration or ""),
        body_font,
        round(width * 0.78),
        max_lines=1,
    )
    draw.multiline_text(
        (margin, body_y),
        narration_lines,
        font=body_font,
        fill=(190, 207, 220, 255),
        spacing=10,
    )

    card_top = round(height * 0.62)
    draw.rounded_rectangle(
        (margin, card_top, width - margin, round(height * 0.78)),
        radius=22,
        fill=(11, 32, 52, 235),
        outline=(70, 161, 188, 160),
        width=2,
    )
    draw.text(
        (margin + 34, card_top + 25),
        "本期核心变量",
        font=small_font,
        fill=(255, 219, 102, 255),
    )
    keywords = _director_card_keywords(segment, titles)
    keyword_lines = _wrap_text(
        draw,
        " / ".join(keywords),
        stat_font,
        width - margin * 2 - 68,
        max_lines=2,
    )
    draw.multiline_text(
        (margin + 34, card_top + 70),
        keyword_lines,
        font=stat_font,
        fill=(239, 246, 250, 255),
        spacing=8,
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(target, "JPEG", quality=93, optimize=True)


def _render_logic_card(
    target: Path,
    size,
    *,
    segment: Dict[str, Any],
    source_label: str,
) -> None:
    width, height = size
    canvas = Image.new("RGB", size, "#07111f")
    draw = ImageDraw.Draw(canvas, "RGBA")
    margin = round(width * 0.07)
    small_font = _load_overlay_font(max(22, round(width * 0.015)))
    title_font = _load_overlay_font(max(48, round(width * 0.035)))
    body_font = _load_overlay_font(max(28, round(width * 0.021)))
    draw.text(
        (margin, round(height * 0.09)),
        "逻辑拆解",
        font=small_font,
        fill=(255, 219, 102, 255),
    )
    draw.text(
        (margin, round(height * 0.17)),
        _audience_headline(segment, max_chars=22),
        font=title_font,
        fill=(245, 249, 252, 255),
    )
    narration = _audience_takeaway(segment, max_chars=30)
    lines = _wrap_text(
        draw,
        str(narration or ""),
        body_font,
        round(width * 0.72),
        max_lines=1,
    )
    draw.multiline_text(
        (margin, round(height * 0.31)),
        lines,
        font=body_font,
        fill=(207, 222, 232, 255),
        spacing=12,
    )
    steps = ("原始事实", "机制传导", "结论边界")
    card_top = round(height * 0.58)
    card_width = round((width - margin * 2 - 48) / 3)
    for index, step in enumerate(steps):
        left = margin + index * (card_width + 24)
        draw.rounded_rectangle(
            (left, card_top, left + card_width, round(height * 0.75)),
            radius=20,
            fill=(12, 38 + index * 8, 58 + index * 10, 235),
            outline=(75, 169, 194, 150),
            width=2,
        )
        draw.text(
            (left + 24, card_top + 32),
            f"0{index + 1}",
            font=small_font,
            fill=(255, 219, 102, 255),
        )
        draw.text(
            (left + 24, card_top + 82),
            step,
            font=body_font,
            fill=(235, 242, 247, 255),
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(target, "JPEG", quality=93, optimize=True)


def _render_chapter_card(
    target: Path,
    size,
    *,
    chapter: Dict[str, Any],
    segment: Dict[str, Any],
) -> None:
    width, height = size
    canvas = Image.new("RGB", size, "#06131f")
    draw = ImageDraw.Draw(canvas, "RGBA")
    margin = round(width * 0.085)
    sequence = int(chapter.get("sequence") or 1)
    small_font = _load_overlay_font(max(23, round(width * 0.016)))
    number_font = _load_overlay_font(max(120, round(width * 0.10)))
    title_font = _load_overlay_font(max(58, round(width * 0.045)))
    body_font = _load_overlay_font(max(29, round(width * 0.021)))
    accent = (
        (111, 205, 222, 255),
        (255, 211, 94, 255),
        (124, 214, 179, 255),
    )[(sequence - 1) % 3]
    draw.text(
        (margin, round(height * 0.09)),
        f"第 {sequence} 章",
        font=small_font,
        fill=accent,
    )
    draw.text(
        (margin, round(height * 0.20)),
        f"{sequence:02d}",
        font=number_font,
        fill=(*accent[:3], 52),
    )
    title = str(chapter.get("title") or "核心观点")
    draw.text(
        (round(width * 0.29), round(height * 0.25)),
        title,
        font=title_font,
        fill=(244, 248, 251, 255),
    )
    narration = _audience_takeaway(segment, max_chars=22)
    lines = _wrap_text(
        draw,
        narration,
        body_font,
        round(width * 0.58),
        max_lines=1,
    )
    draw.multiline_text(
        (round(width * 0.29), round(height * 0.40)),
        lines,
        font=body_font,
        fill=(195, 216, 228, 255),
        spacing=12,
    )
    draw.rectangle(
        (
            round(width * 0.29),
            round(height * 0.70),
            round(width * 0.76),
            round(height * 0.708),
        ),
        fill=accent,
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(target, "JPEG", quality=93, optimize=True)


def _render_character_explainer(
    target: Path,
    size,
    *,
    segment: Dict[str, Any],
    source_label: str,
    page_number: int | None,
) -> None:
    width, height = size
    canvas = Image.new("RGB", size, "#07111f")
    draw = ImageDraw.Draw(canvas, "RGBA")
    margin = round(width * 0.065)
    title_font = _load_overlay_font(max(46, round(width * 0.032)))
    body_font = _load_overlay_font(max(27, round(width * 0.019)))
    small_font = _load_overlay_font(max(20, round(width * 0.014)))

    draw.text(
        (margin, round(height * 0.055)),
        "风险提示",
        font=small_font,
        fill=(255, 219, 102, 255),
    )
    draw.text(
        (margin, round(height * 0.11)),
        "结论之外，还要看清三类变量",
        font=title_font,
        fill=(244, 248, 251, 255),
        spacing=10,
    )
    note_box = (
        margin,
        round(height * 0.22),
        width - margin,
        round(height * 0.46),
    )
    draw.rounded_rectangle(
        note_box,
        radius=24,
        fill=(13, 38, 58, 245),
        outline=(73, 172, 196, 210),
        width=3,
    )
    narration = _narration_text(segment)
    note_lines = _wrap_text(
        draw,
        _audience_takeaway(segment, max_chars=22),
        body_font,
        note_box[2] - note_box[0] - 56,
        max_lines=1,
    )
    draw.multiline_text(
        (note_box[0] + 28, note_box[1] + 34),
        note_lines,
        font=body_font,
        fill=(232, 240, 246, 255),
        spacing=11,
    )

    if re.search(r"(?:不构成.*投资建议|谨慎决策|下期见)", str(narration)):
        labels = ("观点边界", "数据时点", "决策责任")
        checks = (
            "机构观点仅代表原研报作者，不等同于确定性结论。",
            "价格、库存与需求会变化，需结合最新数据复核。",
            "内容用于信息交流，不替代个人独立判断。",
        )
    else:
        labels = ("需求验证", "价格与库存", "政策与供给")
        checks = _director_checkpoints(segment)
    card_top = round(height * 0.51)
    card_bottom = round(height * 0.77)
    gap = 24
    card_width = round((width - margin * 2 - gap * 2) / 3)
    for index, label in enumerate(labels):
        left = margin + index * (card_width + gap)
        draw.rounded_rectangle(
            (left, card_top, left + card_width, card_bottom),
            radius=22,
            fill=(9, 28 + index * 6, 46 + index * 8, 240),
            outline=(255, 219, 102, 115),
            width=2,
        )
        draw.text(
            (left + 26, card_top + 26),
            label,
            font=small_font,
            fill=(255, 219, 102, 255),
        )
        check_value = _ellipsize_to_width(
            draw,
            checks[index] if index < len(checks) else "持续跟踪假设变化",
            body_font,
            card_width - 52,
        )
        draw.text(
            (left + 26, card_top + 86),
            check_value,
            font=body_font,
            fill=(225, 236, 243, 255),
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(target, "JPEG", quality=93, optimize=True)


def _render_evidence_composite(
    pdf: Path,
    page_number: int,
    target: Path,
    size,
    *,
    segment: Dict[str, Any],
    source_label: str,
    show_source_attribution: bool = True,
) -> None:
    import fitz

    width, height = size
    canvas = Image.new("RGB", size, "#07111f")
    with fitz.open(pdf) as document:
        page_number = max(1, min(page_number, len(document)))
        page = document[page_number - 1]
        pixmap = page.get_pixmap(matrix=fitz.Matrix(1.7, 1.7), alpha=False)
        page_image = Image.frombytes(
            "RGB",
            (pixmap.width, pixmap.height),
            pixmap.samples,
        )
    media_type = (
        segment.get("media", {}).get("type")
        if isinstance(segment.get("media"), dict)
        else ""
    )
    if media_type == "chart":
        page_image = page_image.crop(
            (
                0,
                0,
                page_image.width,
                round(page_image.height * 0.72),
            )
        )

    margin = round(width * 0.035)
    footer_height = max(74, round(height * 0.075))
    content_bottom = min(
        height - footer_height - margin,
        round(height * 0.74),
    )
    if width > height:
        page_box = (
            margin,
            margin,
            round(width * 0.55),
            content_bottom,
        )
        panel_box = (
            round(width * 0.59),
            margin,
            width - margin,
            content_bottom,
        )
    else:
        page_box = (
            margin,
            margin,
            width - margin,
            round(height * 0.55),
        )
        panel_box = (
            margin,
            round(height * 0.58),
            width - margin,
            content_bottom,
        )

    page_size = (page_box[2] - page_box[0], page_box[3] - page_box[1])
    page_fitted = ImageOps.contain(
        page_image,
        page_size,
        method=Image.Resampling.LANCZOS,
    )
    page_x = page_box[0] + (page_size[0] - page_fitted.width) // 2
    page_y = page_box[1] + (page_size[1] - page_fitted.height) // 2
    canvas.paste(page_fitted, (page_x, page_y))

    draw = ImageDraw.Draw(canvas, "RGBA")
    draw.rounded_rectangle(
        panel_box,
        radius=max(18, round(width * 0.012)),
        fill=(10, 29, 48, 245),
        outline=(66, 180, 210, 210),
        width=max(2, round(width * 0.0015)),
    )
    title_font = _load_overlay_font(max(30, round(width * 0.025)))
    body_font = _load_overlay_font(max(22, round(width * 0.016)))
    small_font = _load_overlay_font(max(19, round(width * 0.013)))
    panel_width = panel_box[2] - panel_box[0]
    padding = max(24, round(panel_width * 0.065))
    x = panel_box[0] + padding
    y = panel_box[1] + padding
    draw.text(
        (x, y),
        "研报关键数据",
        font=small_font,
        fill=(255, 221, 105, 255),
    )
    y += round(small_font.size * 1.8)
    # ``media.purpose`` is an internal dispatch instruction.  It must never
    # become audience copy (values such as “展示原始证据块” describe the edit,
    # not the report).  Derive the headline from the real narration instead.
    purpose = _audience_headline(segment, max_chars=26)
    purpose_lines = _wrap_text(
        draw,
        str(purpose or "原始研报依据"),
        title_font,
        panel_width - padding * 2,
        max_lines=2,
    )
    draw.multiline_text(
        (x, y),
        purpose_lines,
        font=title_font,
        fill=(245, 249, 255, 255),
        spacing=8,
    )
    y += _multiline_height(draw, purpose_lines, title_font, 8) + round(title_font.size * 0.7)

    evidence_refs = (
        segment.get("evidence_refs")
        if isinstance(segment.get("evidence_refs"), list)
        else []
    )
    quote = next(
        (
            str(item.get("quote") or "").strip()
            for item in evidence_refs
            if isinstance(item, dict) and str(item.get("quote") or "").strip()
        ),
        "",
    )
    metrics = _metric_tokens(quote or _narration_text(segment))
    if metrics:
        metric_font = _load_overlay_font(max(54, round(width * 0.042)))
        draw.text(
            (x, y),
            metrics[0],
            font=metric_font,
            fill=(255, 221, 105, 255),
        )
        y += round(metric_font.size * 1.35)
    takeaway = _audience_takeaway(segment, max_chars=30)
    narration_lines = _wrap_text(
        draw,
        takeaway,
        body_font,
        panel_width - padding * 2,
        max_lines=2,
    )
    draw.multiline_text(
        (x, y),
        narration_lines,
        font=body_font,
        fill=(224, 236, 246, 255),
        spacing=10,
    )

    target.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(target, "JPEG", quality=93, optimize=True)
    if show_source_attribution:
        _overlay_source_label(
            target,
            source_label,
            page_number=page_number,
        )


def _render_evidence_detail(
    pdf: Path,
    page_number: int,
    target: Path,
    size,
    *,
    segment: Dict[str, Any],
    source_label: str,
) -> None:
    """Render a readable evidence crop instead of a second whole-page still."""

    import fitz

    evidence_refs = (
        segment.get("evidence_refs")
        if isinstance(segment.get("evidence_refs"), list)
        else []
    )
    evidence = next(
        (
            item
            for item in evidence_refs
            if isinstance(item, dict) and int(item.get("page") or 0) == page_number
        ),
        {},
    )
    quote = str(evidence.get("quote") or "").strip()
    with fitz.open(pdf) as document:
        page_number = max(1, min(page_number, len(document)))
        page = document[page_number - 1]
        focus_rect = _evidence_focus_rect(page, evidence, quote)
        page_rect = page.rect
        if focus_rect:
            pad_x = page_rect.width * 0.06
            pad_y = page_rect.height * 0.09
            media_type = str(
                (segment.get("media") or {}).get("type")
                if isinstance(segment.get("media"), dict)
                else ""
            )
            lower_pad = page_rect.height * (0.28 if media_type == "chart" else 0.13)
            clip = fitz.Rect(
                max(page_rect.x0, focus_rect.x0 - pad_x),
                max(page_rect.y0, focus_rect.y0 - pad_y),
                min(page_rect.x1, focus_rect.x1 + pad_x),
                min(page_rect.y1, focus_rect.y1 + lower_pad),
            )
            min_height = page_rect.height * (0.32 if media_type == "chart" else 0.22)
            if clip.height < min_height:
                center = (clip.y0 + clip.y1) / 2
                clip.y0 = max(page_rect.y0, center - min_height / 2)
                clip.y1 = min(page_rect.y1, clip.y0 + min_height)
        else:
            clip = fitz.Rect(
                page_rect.x0,
                page_rect.y0,
                page_rect.x1,
                page_rect.y0 + page_rect.height * 0.55,
            )
        matrix = fitz.Matrix(2.4, 2.4)
        pixmap = page.get_pixmap(matrix=matrix, clip=clip, alpha=False)
        crop_image = Image.frombytes(
            "RGB",
            (pixmap.width, pixmap.height),
            pixmap.samples,
        )
        if focus_rect and focus_rect.intersects(clip):
            highlight = fitz.Rect(focus_rect)
            highlight.intersect(clip)
            draw_crop = ImageDraw.Draw(crop_image, "RGBA")
            x0 = round((highlight.x0 - clip.x0) * matrix.a)
            y0 = round((highlight.y0 - clip.y0) * matrix.d)
            x1 = round((highlight.x1 - clip.x0) * matrix.a)
            y1 = round((highlight.y1 - clip.y0) * matrix.d)
            original_crop = crop_image.copy()
            dim = Image.new("RGBA", crop_image.size, (3, 11, 20, 155))
            crop_image = Image.alpha_composite(
                crop_image.convert("RGBA"),
                dim,
            ).convert("RGB")
            focus_box = (
                max(0, x0 - 18),
                max(0, y0 - 16),
                min(crop_image.width, x1 + 18),
                min(crop_image.height, y1 + 16),
            )
            crop_image.paste(
                original_crop.crop(focus_box),
                (focus_box[0], focus_box[1]),
            )
            draw_crop = ImageDraw.Draw(crop_image, "RGBA")
            draw_crop.rounded_rectangle(
                (x0 - 8, y0 - 7, x1 + 8, y1 + 7),
                radius=10,
                fill=(255, 220, 72, 55),
                outline=(255, 184, 40, 245),
                width=5,
            )

    width, height = size
    canvas = Image.new("RGB", size, "#07111f")
    draw = ImageDraw.Draw(canvas, "RGBA")
    margin = round(width * 0.045)
    content_bottom = round(height * 0.74)
    crop_box = (margin, margin, round(width * 0.64), content_bottom)
    crop_size = (crop_box[2] - crop_box[0], crop_box[3] - crop_box[1])
    fitted = ImageOps.contain(
        crop_image,
        crop_size,
        method=Image.Resampling.LANCZOS,
    )
    crop_x = crop_box[0] + (crop_size[0] - fitted.width) // 2
    crop_y = crop_box[1] + (crop_size[1] - fitted.height) // 2
    canvas.paste(fitted, (crop_x, crop_y))

    panel = (
        round(width * 0.68),
        margin,
        width - margin,
        content_bottom,
    )
    draw.rounded_rectangle(
        panel,
        radius=24,
        fill=(10, 31, 50, 248),
        outline=(69, 177, 204, 205),
        width=3,
    )
    small_font = _load_overlay_font(max(22, round(width * 0.014)))
    title_font = _load_overlay_font(max(34, round(width * 0.025)))
    body_font = _load_overlay_font(max(24, round(width * 0.016)))
    x = panel[0] + 32
    y = panel[1] + 32
    draw.text(
        (x, y),
        "原文依据",
        font=small_font,
        fill=(255, 219, 102, 255),
    )
    y += round(small_font.size * 2.0)
    draw.text(
        (x, y),
        _audience_headline(segment, max_chars=18),
        font=title_font,
        fill=(243, 248, 251, 255),
    )
    y += round(title_font.size * 1.8)
    metrics = _metric_tokens(quote or _narration_text(segment))
    if metrics:
        metric_font = _load_overlay_font(max(56, round(width * 0.044)))
        draw.text(
            (x, y),
            metrics[0],
            font=metric_font,
            fill=(255, 224, 111, 255),
        )
        y += round(metric_font.size * 1.35)
    summary_lines = _wrap_text(
        draw,
        _audience_takeaway(segment, max_chars=28),
        body_font,
        panel[2] - panel[0] - 64,
        max_lines=2,
    )
    draw.multiline_text(
        (x, y),
        summary_lines,
        font=body_font,
        fill=(203, 225, 235, 255),
        spacing=10,
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(target, "JPEG", quality=94, optimize=True)
    _overlay_source_label(
        target,
        source_label,
        page_number=page_number,
    )


def _evidence_focus_rect(page, evidence: Dict[str, Any], quote: str):
    import fitz

    bbox = evidence.get("bbox")
    if isinstance(bbox, list) and len(bbox) == 4:
        try:
            values = [float(item) for item in bbox]
            if all(0.0 <= item <= 1.0 for item in values):
                values = [
                    values[0] * page.rect.width,
                    values[1] * page.rect.height,
                    values[2] * page.rect.width,
                    values[3] * page.rect.height,
                ]
            rect = fitz.Rect(*values)
            if not rect.is_empty and rect.intersects(page.rect):
                return rect
        except (TypeError, ValueError):
            pass
    normalized = re.sub(r"\s+", " ", quote or "").strip()
    candidates = []
    if normalized:
        candidates.extend((normalized[:120], normalized[:80], normalized[:48]))
        words = re.findall(r"[A-Za-z0-9$%+.-]+", normalized)
        for size in (10, 8, 6, 4):
            if len(words) >= size:
                candidates.append(" ".join(words[:size]))
    for candidate in candidates:
        matches = page.search_for(candidate)
        if matches:
            combined = fitz.Rect(matches[0])
            for match in matches[1:4]:
                combined.include_rect(match)
            return combined
    return None


def _render_insight_card(
    target: Path,
    size,
    *,
    segment: Dict[str, Any],
    sequence: int,
    total: int,
    progress: float = 1.0,
    is_climax: bool = False,
) -> None:
    """Render an object-led visual beat, not another editorial text card."""

    progress = max(0.05, min(1.0, float(progress)))
    width, height = size
    background = "#160d14" if is_climax else "#06131f"
    canvas = Image.new("RGB", size, background)
    draw = ImageDraw.Draw(canvas, "RGBA")
    margin = round(width * 0.07)
    small_font = _load_overlay_font(max(24, round(width * 0.016)))
    title_font = _load_overlay_font(max(54, round(width * 0.038)))
    metric_font = _load_overlay_font(max(76, round(width * 0.058)))
    body_font = _load_overlay_font(max(28, round(width * 0.020)))
    narration = _narration_text(segment)
    metrics = _metric_tokens(narration)
    checkpoints = _director_checkpoints(segment)
    variant = _visual_story_variant(segment, sequence)
    accent = (255, 178, 74, 255) if is_climax else (111, 205, 222, 255)

    if is_climax:
        visual_label = (
            "竞争转折"
            if re.search(
                r"接口芯片|Rambus|RDIMM|MRDIMM|SOCAMM|CXL|PCIe|PHY",
                narration,
                re.IGNORECASE,
            )
            else "结构性风险"
        )
    else:
        visual_label = {
            "infrastructure": "算力基础设施",
            "interface_stack": "存储接口",
            "timeline": "时间轴",
            "comparison": "数据对比",
            "balance": "供需天平",
            "causal_chain": "因果链",
            "industry_chain": "产业链",
        }[variant]
    draw.text(
        (margin, round(height * 0.065)),
        visual_label,
        font=small_font,
        fill=accent,
    )
    if not is_climax:
        headline = _audience_headline(segment, max_chars=23)
        draw.text(
            (margin, round(height * 0.125)),
            headline,
            font=title_font,
            fill=(246, 249, 251, 255),
        )

    if is_climax:
        _draw_visual_climax(
            draw,
            size,
            progress=progress,
            narration=narration,
            accent=accent,
        )
    elif variant == "infrastructure":
        _draw_infrastructure_story(
            draw,
            size,
            progress=progress,
            labels=checkpoints,
            accent=accent,
        )
    elif variant == "interface_stack":
        _draw_interface_stack_story(
            draw,
            size,
            progress=progress,
            narration=narration,
            accent=accent,
        )
    elif variant == "timeline":
        _draw_timeline_story(
            draw,
            size,
            progress=progress,
            metrics=metrics,
            labels=checkpoints,
            accent=accent,
        )
    elif variant == "comparison":
        _draw_comparison_story(
            draw,
            size,
            progress=progress,
            metrics=metrics,
            labels=checkpoints,
            accent=accent,
            metric_font=metric_font,
            body_font=body_font,
        )
    elif variant == "balance":
        _draw_balance_story(
            draw,
            size,
            progress=progress,
            metrics=metrics,
            labels=checkpoints,
            accent=accent,
        )
    elif variant == "causal_chain":
        _draw_causal_story(
            draw,
            size,
            progress=progress,
            labels=checkpoints,
            accent=accent,
        )
    else:
        _draw_industry_chain_story(
            draw,
            size,
            progress=progress,
            labels=checkpoints,
            accent=accent,
        )

    if is_climax:
        draw.rectangle(
            (0, round(height * 0.79), round(width * progress), round(height * 0.805)),
            fill=(255, 178, 74, 255),
        )
        draw.text(
            (width - margin, round(height * 0.065)),
            "关键转折",
            font=small_font,
            fill=(255, 178, 74, 255),
            anchor="ra",
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(target, "JPEG", quality=93, optimize=True)


def _draw_visual_climax(
    draw,
    size,
    *,
    progress: float,
    narration: str,
    accent,
) -> None:
    """Give the report one unmistakable peak instead of another chart card."""

    width, height = size
    margin = round(width * 0.07)
    percent_metrics = [
        item
        for item in _metric_tokens(narration)
        if "%" in item or "百分之" in item
    ]
    years = [
        item
        for item in _metric_tokens(narration)
        if item.endswith("年")
    ]
    is_capacity_climax = "产能利用率" in narration
    is_jevons_climax = bool(
        re.search(
            r"杰文斯|不会减少.{0,8}需求|成本.{0,8}下降.{0,12}需求|效率.{0,12}需求",
            narration,
        )
    )
    is_interface_climax = bool(
        re.search(
            r"接口芯片|Rambus|RDIMM|MRDIMM|SOCAMM|CXL|PCIe|PHY",
            narration,
            re.IGNORECASE,
        )
    )
    is_infrastructure_climax = bool(
        re.search(
            r"数据中心|电力|电网|算力基础设施|资本开支|Capex",
            narration,
            re.IGNORECASE,
        )
    )
    if is_capacity_climax:
        metric = next(
            (
                item
                for item in percent_metrics
                if "三十" in item or "30" in item
            ),
            percent_metrics[0] if percent_metrics else "30%",
        )
    else:
        metric = (
            percent_metrics[0]
            if percent_metrics
            else (_metric_tokens(narration) or ["关键拐点"])[0]
        )
    metric_value = _spoken_metric_display(metric)
    period = " → ".join(_spoken_metric_display(item) for item in years[:2])
    if not period:
        period = "当前 → 下一验证点"

    headline_font = _load_overlay_font(max(54, round(width * 0.040)))
    metric_font = _load_overlay_font(max(150, round(width * 0.09)))
    body_font = _load_overlay_font(max(34, round(width * 0.024)))
    small_font = _load_overlay_font(max(27, round(width * 0.018)))
    if is_capacity_climax:
        headline = "产能利用率被锁在长期低位"
        gauge_label = "行业有效利用"
        ring_top, ring_bottom = "长期", "低位"
        bottom_copy = "供给出清速度  <  需求消化速度"
        fill_ratio = 0.30
    elif is_jevons_climax:
        headline = "成本下降，反而放大总需求"
        gauge_label = "效率提升"
        ring_top, ring_bottom = "杰文斯", "悖论"
        bottom_copy = "单位成本下降  →  应用扩张  →  总需求上升"
        fill_ratio = 0.76
        metric_value = "需求 ↑"
        period = "当前效率提升 → 下一轮需求扩张"
    elif is_interface_climax:
        headline = "接口芯片竞争进入第二轮洗牌"
        gauge_label = "核心业务收入占比"
        ring_top, ring_bottom = "竞争", "壁垒"
        bottom_copy = "份额 × 客户认证 × 产品迭代"
        fill_ratio = 0.49
    elif is_infrastructure_climax:
        headline = "资本开支回报正在加速分化"
        gauge_label = "关键投入回报"
        ring_top, ring_bottom = "风险", "定价"
        bottom_copy = "收入弹性 × 资本开支 × 电力约束"
        fill_ratio = 0.62
    else:
        headline = _audience_headline(
            {"narration": {"text": narration}},
            max_chars=20,
        )
        gauge_label = "关键指标"
        ring_top, ring_bottom = "核心", "转折"
        bottom_copy = "数据反差正在改写原有判断"
        fill_ratio = 0.55
    draw.text(
        (margin, round(height * 0.13)),
        headline,
        font=headline_font,
        fill=(250, 248, 244, 255),
    )
    draw.text(
        (margin, round(height * 0.24)),
        period,
        font=body_font,
        fill=(255, 203, 119, 255),
    )

    gauge_left = margin
    gauge_top = round(height * 0.39)
    gauge_right = round(width * 0.62)
    gauge_bottom = round(height * 0.64)
    draw.rounded_rectangle(
        (gauge_left, gauge_top, gauge_right, gauge_bottom),
        radius=30,
        fill=(34, 19, 26, 255),
        outline=(255, 178, 74, 150),
        width=4,
    )
    live_ratio = max(0.05, fill_ratio * progress)
    bar_left = gauge_left + 430
    bar_width = gauge_right - bar_left - 30
    draw.rounded_rectangle(
        (
            bar_left,
            gauge_top + 135,
            bar_left + round(bar_width * live_ratio),
            gauge_top + 190,
        ),
        radius=20,
        fill=accent,
    )
    draw.text(
        (gauge_left + 34, gauge_top + 34),
        gauge_label,
        font=small_font,
        fill=(218, 205, 200, 255),
    )
    draw.text(
        (gauge_left + 32, gauge_top + 82),
        metric_value,
        font=metric_font,
        fill=(255, 190, 88, 255),
    )

    x = round(width * 0.76)
    y = round(height * 0.50)
    ring_radius = round(130 + 22 * progress)
    draw.ellipse(
        (x - ring_radius, y - ring_radius, x + ring_radius, y + ring_radius),
        fill=(54, 22, 30, 255),
        outline=(255, 178, 74, 255),
        width=8,
    )
    draw.text(
        (x, y - 18),
        ring_top,
        font=body_font,
        fill=(255, 228, 190, 255),
        anchor="mm",
    )
    draw.text(
        (x, y + 45),
        ring_bottom,
        font=headline_font,
        fill=(255, 178, 74, 255),
        anchor="mm",
    )
    draw.text(
        (margin, round(height * 0.67)),
        bottom_copy,
        font=body_font,
        fill=(236, 226, 222, 255),
    )


def _spoken_metric_display(value: str) -> str:
    replacements = (
        ("百分之三十三", "33%"),
        ("百分之三十", "30%"),
        ("百分之六十六", "66%"),
        ("百分之五十一", "51%"),
        ("百分之四十四", "44%"),
        ("百分之十五", "15%"),
        ("百分之十一", "11%"),
        ("百分之十", "10%"),
        ("百分之七", "7%"),
        ("百分之六", "6%"),
        ("二零二六年", "2026"),
        ("二零二七年", "2027"),
        ("二零二八年", "2028"),
        ("二零二九年", "2029"),
        ("二零三零年", "2030"),
    )
    output = str(value or "")
    for source, target in replacements:
        output = output.replace(source, target)
    return output


def _narration_text(segment: Dict[str, Any]) -> str:
    return str(
        (segment.get("narration") or {}).get("text")
        if isinstance(segment.get("narration"), dict)
        else ""
    )


def _metric_tokens(text: str) -> list[str]:
    metrics = []
    for token in re.findall(
        r"(?:[-+]?\d+(?:\.\d+)?\s*(?:%|pp|ppt|倍|亿美元|亿元|万亿|美元|年|天)?|"
        r"百分之[零一二三四五六七八九十百点]+|"
        r"[零一二三四五六七八九十百千万亿点]+(?:倍|亿美元|亿元|万亿|美元|年|天))",
        str(text or ""),
        flags=re.IGNORECASE,
    ):
        token = re.sub(r"\s+", "", token)
        if token and token not in metrics:
            metrics.append(token)
    return metrics


def _visual_story_variant(segment: Dict[str, Any], sequence: int) -> str:
    narration = _narration_text(segment)
    metrics = _metric_tokens(narration)
    if re.search(
        r"接口芯片|存储器接口|内存接口|Rambus|DRAM|RDIMM|MRDIMM|SOCAMM|CXL|PCIe|PHY",
        narration,
        re.IGNORECASE,
    ):
        year_count = len(
            re.findall(
                r"(?:20\d{2}|二零[一二三四五六七八九十]{2})年",
                narration,
            )
        )
        if year_count >= 2:
            return "timeline"
        if len(metrics) >= 2 and sequence % 3 == 1:
            return "comparison"
        if (
            re.search(r"因此|意味着|导致|传导|源于|推动|制约|竞争|挤压", narration)
            and sequence % 3 == 2
        ):
            return "causal_chain"
        return "interface_stack"
    if re.search(
        r"数据中心|电力|电网|算力基础设施|资本开支|Capex",
        narration,
        re.IGNORECASE,
    ):
        return "infrastructure"
    if len(re.findall(r"(?:20\d{2}|二零[一二三四五六七八九十]{2})年", narration)) >= 2:
        return "timeline"
    if re.search(r"供给|需求|库存|产能|价格|成本", narration) and sequence % 3 == 0:
        return "balance"
    if len(metrics) >= 2:
        return "comparison"
    if re.search(r"因此|意味着|导致|传导|源于|推动|制约", narration):
        return "causal_chain"
    return "industry_chain"


def _draw_arrow(draw, start, end, *, color, width=10) -> None:
    x0, y0 = start
    x1, y1 = end
    draw.line((x0, y0, x1, y1), fill=color, width=width)
    angle = 18
    draw.polygon(
        [
            (x1, y1),
            (x1 - angle, y1 - angle),
            (x1 - angle, y1 + angle),
        ],
        fill=color,
    )


def _draw_server_rack(draw, box, *, color, progress=1.0) -> None:
    x0, y0, x1, y1 = box
    draw.rounded_rectangle(box, radius=18, outline=color, width=6, fill=(8, 31, 48, 255))
    rows = 6
    for index in range(rows):
        if (index + 1) / rows > progress:
            continue
        top = y0 + 30 + index * ((y1 - y0 - 60) / rows)
        draw.line((x0 + 24, top, x1 - 24, top), fill=color, width=4)
        draw.ellipse((x1 - 48, top - 7, x1 - 34, top + 7), fill=(255, 219, 102, 255))


def _draw_chip(draw, center, size, *, color, progress=1.0) -> None:
    cx, cy = center
    half = size // 2
    draw.rounded_rectangle(
        (cx - half, cy - half, cx + half, cy + half),
        radius=18,
        fill=(11, 39, 58, 255),
        outline=color,
        width=7,
    )
    inner = round(half * progress)
    draw.rectangle(
        (cx - inner, cy - inner, cx + inner, cy + inner),
        fill=(30, 94, 112, 190),
        outline=(255, 219, 102, 255),
        width=4,
    )
    for offset in (-half // 2, 0, half // 2):
        draw.line((cx + offset, cy - half - 28, cx + offset, cy - half), fill=color, width=5)
        draw.line((cx + offset, cy + half, cx + offset, cy + half + 28), fill=color, width=5)


def _draw_power_tower(draw, center, size, *, color, progress=1.0) -> None:
    cx, cy = center
    h = round(size * progress)
    top = cy + size // 2 - h
    bottom = cy + size // 2
    draw.line((cx, top, cx - size // 3, bottom), fill=color, width=7)
    draw.line((cx, top, cx + size // 3, bottom), fill=color, width=7)
    for ratio in (0.28, 0.52, 0.75):
        y = top + h * ratio
        span = size * ratio * 0.32
        draw.line((cx - span, y, cx + span, y), fill=color, width=6)


def _draw_infrastructure_story(draw, size, *, progress, labels, accent) -> None:
    width, height = size
    y = round(height * 0.50)
    points = (
        (round(width * 0.20), "电力"),
        (round(width * 0.48), "数据中心"),
        (round(width * 0.77), "芯片与 DRAM"),
    )
    visible_x = round(width * (0.13 + 0.72 * progress))
    draw.line(
        (round(width * 0.13), y, visible_x, y),
        fill=(*accent[:3], 190),
        width=12,
    )
    _draw_power_tower(
        draw,
        (points[0][0], y),
        220,
        color=accent,
        progress=progress,
    )
    _draw_server_rack(
        draw,
        (points[1][0] - 100, y - 130, points[1][0] + 100, y + 130),
        color=accent,
        progress=progress,
    )
    _draw_chip(
        draw,
        (points[2][0], y),
        190,
        color=accent,
        progress=progress,
    )
    font = _load_overlay_font(34)
    for x, label in points:
        draw.text((x, round(height * 0.70)), label, font=font, fill=(235, 244, 248, 255), anchor="ma")


def _draw_interface_stack_story(
    draw,
    size,
    *,
    progress: float,
    narration: str,
    accent,
) -> None:
    """Visualize the actual memory-interface stack instead of generic AI objects."""

    width, height = size
    y = round(height * 0.53)
    left_x, center_x, right_x = (
        round(width * 0.20),
        round(width * 0.50),
        round(width * 0.80),
    )
    active_color = accent
    muted = (47, 83, 99, 255)
    _draw_chip(
        draw,
        (left_x, y),
        170,
        color=active_color,
        progress=max(0.18, min(1.0, progress * 1.7)),
    )
    _draw_chip(
        draw,
        (center_x, y),
        220,
        color=active_color if progress >= 0.30 else muted,
        progress=max(0.12, min(1.0, progress * 1.5 - 0.22)),
    )
    for offset in (-100, 0, 100):
        _draw_chip(
            draw,
            (right_x, y + offset),
            92,
            color=active_color if progress >= 0.65 else muted,
            progress=max(0.12, min(1.0, progress * 1.4 - 0.48)),
        )
    lane_color = (*accent[:3], 210)
    lane_count = 4
    for index in range(lane_count):
        offset = (index - (lane_count - 1) / 2) * 22
        if progress >= 0.22:
            draw.line(
                (left_x + 110, y + offset, center_x - 135, y + offset),
                fill=lane_color,
                width=6,
            )
        if progress >= 0.58:
            draw.line(
                (center_x + 135, y + offset, right_x - 105, y + offset),
                fill=lane_color,
                width=6,
            )
    font = _load_overlay_font(34)
    small = _load_overlay_font(26)
    left_label = "CPU / AI ASIC"
    center_label = (
        "CXL / PCIe 接口"
        if re.search(r"CXL|PCIe", narration, re.IGNORECASE)
        else "内存接口芯片"
    )
    right_label = (
        "MRDIMM / SOCAMM"
        if re.search(r"MRDIMM|SOCAMM", narration, re.IGNORECASE)
        else "DRAM 通道"
    )
    for x, label in (
        (left_x, left_label),
        (center_x, center_label),
        (right_x, right_label),
    ):
        draw.text(
            (x, round(height * 0.76)),
            label,
            font=font,
            fill=(237, 244, 248, 255),
            anchor="ma",
        )
    draw.text(
        (center_x, round(height * 0.83)),
        "带宽、认证与协议 IP 共同构成壁垒",
        font=small,
        fill=(177, 205, 217, 255),
        anchor="ma",
    )


def _draw_timeline_story(draw, size, *, progress, metrics, labels, accent) -> None:
    width, height = size
    left, right, y = round(width * 0.12), round(width * 0.88), round(height * 0.52)
    end = left + round((right - left) * progress)
    draw.line((left, y, end, y), fill=accent, width=12)
    font = _load_overlay_font(38)
    small = _load_overlay_font(27)
    values = (metrics + labels)[:3]
    for index in range(3):
        ratio = index / 2
        x = round(left + (right - left) * ratio)
        active = progress + 0.02 >= ratio
        radius = 30 if active else 16
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=accent if active else (42, 72, 88, 255))
        value = values[index] if index < len(values) else f"阶段 {index + 1}"
        draw.text((x, y - 92), value, font=font, fill=(246, 249, 251, 255), anchor="ms")
        draw.text((x, y + 70), ("起点", "拐点", "验证")[index], font=small, fill=(171, 203, 216, 255), anchor="ma")


def _draw_comparison_story(
    draw,
    size,
    *,
    progress,
    metrics,
    labels,
    accent,
    metric_font,
    body_font,
) -> None:
    width, height = size
    baseline = round(height * 0.72)
    bar_width = round(width * 0.16)
    positions = (round(width * 0.34), round(width * 0.66))
    values = (metrics + ["关键变化", "验证变量"])[:2]
    heights = (round(height * 0.24 * progress), round(height * 0.42 * progress))
    colors = ((64, 137, 162, 255), accent)
    for index, x in enumerate(positions):
        top = baseline - heights[index]
        draw.rectangle((x - bar_width // 2, top, x + bar_width // 2, baseline), fill=colors[index])
        draw.text((x, top - 54), values[index], font=metric_font, fill=(246, 249, 251, 255), anchor="ms")
        label = labels[index] if index < len(labels) else ("基准", "变化")
        draw.text((x, baseline + 38), label, font=body_font, fill=(189, 215, 225, 255), anchor="ma")
    _draw_arrow(
        draw,
        (positions[0] + bar_width, round(height * 0.48)),
        (positions[1] - bar_width, round(height * 0.48)),
        color=accent,
        width=9,
    )


def _draw_balance_story(draw, size, *, progress, metrics, labels, accent) -> None:
    width, height = size
    cx, cy = round(width * 0.5), round(height * 0.53)
    span = round(width * 0.30)
    tilt = round(70 * progress)
    draw.line((cx - span, cy - tilt, cx + span, cy + tilt), fill=accent, width=13)
    draw.line((cx, cy - 20, cx, round(height * 0.73)), fill=(211, 226, 233, 255), width=10)
    draw.polygon(
        [(cx - 54, round(height * 0.74)), (cx + 54, round(height * 0.74)), (cx, cy - 36)],
        outline=(211, 226, 233, 255),
        fill=(17, 50, 68, 255),
    )
    font = _load_overlay_font(42)
    small = _load_overlay_font(28)
    left_value = metrics[0] if metrics else "供给"
    right_value = metrics[1] if len(metrics) > 1 else "需求"
    draw.text((cx - span, cy - tilt - 80), left_value, font=font, fill=(246, 249, 251, 255), anchor="ms")
    draw.text((cx + span, cy + tilt - 80), right_value, font=font, fill=(255, 219, 102, 255), anchor="ms")
    draw.text((cx - span, cy - tilt + 36), "供给与库存", font=small, fill=(177, 205, 217, 255), anchor="ma")
    draw.text((cx + span, cy + tilt + 36), "需求与价格", font=small, fill=(177, 205, 217, 255), anchor="ma")


def _draw_causal_story(draw, size, *, progress, labels, accent) -> None:
    width, height = size
    y = round(height * 0.53)
    xs = (round(width * 0.19), round(width * 0.50), round(width * 0.81))
    font = _load_overlay_font(32)
    for index, x in enumerate(xs):
        active = progress >= (index + 1) / 3 - 0.05
        radius = 112 if active else 72
        draw.ellipse(
            (x - radius, y - radius, x + radius, y + radius),
            fill=(13, 51 + index * 8, 69 + index * 8, 255),
            outline=accent if active else (59, 91, 104, 255),
            width=7,
        )
        label = labels[index] if index < len(labels) else ("事实", "机制", "结果")[index]
        wrapped = _wrap_text(draw, label, font, radius * 1.45, max_lines=2)
        draw.multiline_text((x, y), wrapped, font=font, fill=(240, 246, 249, 255), anchor="mm", align="center", spacing=8)
        if index < 2 and progress >= (index + 1) / 3:
            _draw_arrow(draw, (x + radius + 20, y), (xs[index + 1] - radius - 20, y), color=accent, width=9)


def _draw_industry_chain_story(draw, size, *, progress, labels, accent) -> None:
    width, height = size
    y = round(height * 0.54)
    xs = (round(width * 0.20), round(width * 0.50), round(width * 0.80))
    font = _load_overlay_font(31)
    for index, x in enumerate(xs):
        active = progress >= (index + 1) / 3 - 0.05
        _draw_chip(
            draw,
            (x, y),
            150 if index != 1 else 185,
            color=accent if active else (50, 82, 96, 255),
            progress=max(0.18, min(1.0, progress * 1.4 - index * 0.25)),
        )
        label = labels[index] if index < len(labels) else ("上游", "中游", "下游")[index]
        draw.text((x, round(height * 0.72)), label, font=font, fill=(237, 244, 248, 255), anchor="ma")
        if index < 2 and progress >= (index + 1) / 3:
            _draw_arrow(draw, (x + 120, y), (xs[index + 1] - 120, y), color=accent, width=9)


def _select_visual_climax(segments: Sequence[Dict[str, Any]]) -> str:
    ranked = []
    for index, segment in enumerate(segments, start=1):
        narration = _narration_text(segment)
        score = len(_metric_tokens(narration))
        score += len(
            re.findall(
                r"但|却|唯一|反而|拐点|暴跌|暴涨|高达|仅|远超|瓶颈|风险|失衡|残酷",
                narration,
            )
        ) * 2
        # Avoid using the opening or disclaimer as the only peak.
        if index in (1, len(segments)):
            score -= 4
        ranked.append((score, index, str(segment.get("segment_id") or f"seg-{index:03d}")))
    return max(ranked)[2] if ranked else ""


def _hook_story_copy(segments: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    corpus = " ".join(_narration_text(item) for item in segments)
    if "光伏" in corpus and "玻璃" in corpus:
        return {
            "left": "全产业链仍在下跌",
            "right": "玻璃却逆势涨价",
            "metric_left": "全链 −7%",
            "metric_right": "玻璃 +6%",
            "metric_left_ratio": 0.25,
            "metric_right_ratio": 0.46,
            "question": "反转来自需求，还是供给先退出？",
            "object_left": "价格下行",
            "object_mid": "协同减产",
            "object_right": "库存拐点",
        }
    if re.search(r"Rambus|接口芯片|存储器接口", corpus, re.IGNORECASE):
        return {
            "left": "AI 推理需求激增",
            "right": "接口芯片成为瓶颈",
            "metric_left": "市场 10 倍",
            "metric_right": "年增 65%",
            "metric_left_ratio": 0.32,
            "metric_right_ratio": 0.46,
            "question": "增长红利最终由谁拿走？",
            "object_left": "CPU 推理",
            "object_mid": "接口芯片",
            "object_right": "竞争壁垒",
        }
    if re.search(r"AI基础设施|算力|数据中心|电力", corpus, re.IGNORECASE):
        return {
            "left": "算力需求继续狂飙",
            "right": "供给瓶颈正在上移",
            "metric_left": "55 美元收入",
            "metric_right": "5 美元成本",
            "metric_left_ratio": 0.52,
            "metric_right_ratio": 0.22,
            "question": "真正稀缺的是芯片，还是电力？",
            "object_left": "AI 芯片",
            "object_mid": "数据中心",
            "object_right": "电力与电网",
        }
    first = segments[0] if segments else {}
    return {
        "left": _audience_headline(first, max_chars=12),
        "right": _audience_takeaway(first, max_chars=12),
        "metric_left": "事实",
        "metric_right": "反差",
        "metric_left_ratio": 0.32,
        "metric_right_ratio": 0.46,
        "question": "这条判断究竟靠什么成立？",
        "object_left": "数据",
        "object_mid": "机制",
        "object_right": "结论",
    }


def _render_hook_frame(
    target: Path,
    size,
    *,
    segments: Sequence[Dict[str, Any]],
    phase: int,
) -> None:
    width, height = size
    copy = _hook_story_copy(segments)
    canvas = Image.new("RGB", size, "#050d17")
    draw = ImageDraw.Draw(canvas, "RGBA")
    margin = round(width * 0.07)
    label_font = _load_overlay_font(28)
    title_font = _load_overlay_font(82)
    metric_font = _load_overlay_font(104)
    body_font = _load_overlay_font(38)
    cyan = (111, 205, 222, 255)
    gold = (255, 202, 72, 255)
    red = (225, 89, 78, 255)

    if phase == 1:
        split = round(width * 0.50)
        draw.rectangle((0, 0, split, height), fill=(28, 13, 20, 255))
        draw.rectangle((split, 0, width, height), fill=(7, 31, 39, 255))
        draw.line((split, 0, split, height), fill=(245, 245, 245, 80), width=4)
        draw.text((margin, round(height * 0.10)), "核心矛盾", font=label_font, fill=gold)
        left = _wrap_text(draw, copy["left"], title_font, round(width * 0.36), max_lines=2)
        right = _wrap_text(draw, copy["right"], title_font, round(width * 0.36), max_lines=2)
        draw.multiline_text((margin, round(height * 0.30)), left, font=title_font, fill=(249, 242, 241, 255), spacing=14)
        draw.multiline_text((split + margin, round(height * 0.30)), right, font=title_font, fill=(238, 249, 250, 255), spacing=14)
        _draw_arrow(
            draw,
            (round(width * 0.40), round(height * 0.66)),
            (round(width * 0.60), round(height * 0.66)),
            color=gold,
            width=12,
        )
    elif phase == 2:
        draw.text((margin, round(height * 0.09)), "反常识数据", font=label_font, fill=gold)
        baseline = round(height * 0.72)
        positions = (round(width * 0.32), round(width * 0.68))
        heights = (
            round(height * float(copy.get("metric_left_ratio") or 0.32)),
            round(height * float(copy.get("metric_right_ratio") or 0.46)),
        )
        colors = (red, cyan)
        for index, x in enumerate(positions):
            bar_width = round(width * 0.18)
            top = baseline - heights[index]
            draw.rectangle((x - bar_width // 2, top, x + bar_width // 2, baseline), fill=colors[index])
            value = copy["metric_left"] if index == 0 else copy["metric_right"]
            draw.text((x, top - 46), value, font=metric_font, fill=(248, 250, 251, 255), anchor="ms")
        draw.text((width // 2, round(height * 0.82)), "同一份研报里的两种方向", font=body_font, fill=(187, 215, 225, 255), anchor="ma")
    else:
        draw.text((margin, round(height * 0.09)), "接下来要回答", font=label_font, fill=gold)
        question = _wrap_text(draw, copy["question"], title_font, round(width * 0.78), max_lines=2)
        draw.multiline_text((margin, round(height * 0.20)), question, font=title_font, fill=(246, 249, 251, 255), spacing=14)
        y = round(height * 0.66)
        xs = (round(width * 0.22), round(width * 0.50), round(width * 0.78))
        for index, x in enumerate(xs):
            draw.ellipse((x - 72, y - 72, x + 72, y + 72), fill=(12, 45 + index * 8, 62 + index * 8, 255), outline=cyan, width=6)
            draw.text(
                (x, y),
                (copy["object_left"], copy["object_mid"], copy["object_right"])[index],
                font=body_font,
                fill=(240, 246, 249, 255),
                anchor="mm",
            )
            if index < 2:
                _draw_arrow(draw, (x + 92, y), (xs[index + 1] - 92, y), color=gold, width=9)
    target.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(target, "JPEG", quality=95, optimize=True)


def _hook_durations(total_duration: float) -> list[float]:
    total_duration = max(0.1, float(total_duration))
    if total_duration <= 3.0:
        return [total_duration]
    if total_duration <= 8.0:
        return [3.0, total_duration - 3.0]
    if total_duration <= 15.0:
        return [3.0, 5.0, total_duration - 8.0]
    remaining = total_duration - 15.0
    return [3.0, 5.0, 7.0, remaining / 2.0, remaining / 2.0]


def _source_trace(
    material: Dict[str, Any],
    evidence_map: Sequence[Dict[str, Any]],
    pdf: Path,
    *,
    original_material_task_id: str,
) -> Dict[str, Any]:
    pdf_meta = material.get("pdf") if isinstance(material.get("pdf"), dict) else {}
    structured = (
        material.get("structured_result")
        if isinstance(material.get("structured_result"), dict)
        else {}
    )
    return {
        "source_type": str(
            material.get("source_type")
            or material.get("provenance_type")
            or "existing_material_payload"
        ),
        "material_task_id": original_material_task_id,
        "material_feature": material.get("feature"),
        "pdf_attachment_id": pdf_meta.get("attachment_id"),
        "pdf_filename": pdf.name,
        "pdf_title": (
            structured.get("title")
            or material.get("title")
            or pdf_meta.get("topic_title")
            or pdf.stem
        ),
        "pdf_path": str(pdf),
        "pdf_sha256": _file_sha256(pdf),
        "script_sha256": hashlib.sha256(
            str(structured.get("final_script_for_tts") or "").encode("utf-8")
        ).hexdigest(),
        "evidence_pages": sorted(
            {
                int(item["page"])
                for item in evidence_map
                if isinstance(item, dict) and str(item.get("page") or "").isdigit()
            }
        ),
        "evidence_ids": [
            str(item.get("evidence_id"))
            for item in evidence_map
            if isinstance(item, dict) and item.get("evidence_id")
        ],
        "audio_task_id": material.get("audio_task_id"),
        "audio_path": str(material.get("audio_path") or "") or None,
        "audio_reused": bool(
            material.get("audio_path")
            and Path(str(material.get("audio_path"))).is_file()
        ),
    }


def _source_label(material: Dict[str, Any], pdf: Path) -> str:
    structured = (
        material.get("structured_result")
        if isinstance(material.get("structured_result"), dict)
        else {}
    )
    candidates = " ".join(
        str(item or "")
        for item in (
            material.get("institution"),
            structured.get("institution"),
            pdf.name,
            structured.get("title"),
            material.get("title"),
        )
    )
    known = (
        "摩根士丹利",
        "摩根大通",
        "高盛",
        "伯恩斯坦",
        "瑞银",
        "花旗",
        "美银",
        "德银",
        "中金",
        "华泰",
    )
    institution = next((name for name in known if name in candidates), "")
    if not institution:
        prefix = re.split(r"[-—_:：|｜]", pdf.stem, maxsplit=1)[0].strip()
        if 1 < len(prefix) <= 12 and not re.search(
            r"(?:report|研报|报告)",
            prefix,
            re.IGNORECASE,
        ):
            institution = prefix
    return f"来源：{institution or '机构'}研报"


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _overlay_source_label(
    target: Path,
    label: str,
    *,
    page_number: int | None = None,
) -> None:
    display_source = str(label or "").strip()
    if page_number:
        display_source = f"{display_source}，第{int(page_number)}页"
    _assert_audience_copy_safe(display_source)
    with Image.open(target) as image:
        frame = image.convert("RGB")
    draw = ImageDraw.Draw(frame, "RGBA")
    font_size = max(22, round(frame.width * 0.017))
    font = _load_overlay_font(font_size)
    padding_x = max(24, round(frame.width * 0.018))
    padding_y = max(14, round(frame.height * 0.012))
    display_label = _ellipsize_to_width(
        draw,
        display_source,
        font,
        frame.width - padding_x * 2,
    )
    text_box = draw.textbbox((0, 0), display_label, font=font)
    text_height = text_box[3] - text_box[1]
    top = frame.height - text_height - padding_y * 2
    draw.rectangle((0, top, frame.width, frame.height), fill=(3, 12, 24, 224))
    draw.text(
        (padding_x, top + padding_y),
        display_label,
        font=font,
        fill=(255, 231, 125, 255),
    )
    frame.save(target, "JPEG", quality=92, optimize=True)


def _assert_audience_copy_safe(value: str) -> None:
    if _AUDIENCE_FORBIDDEN_RE.search(str(value or "")):
        raise ValueError(f"观众画面包含制作或审计元数据：{value}")


def _audit_audience_copy(
    package: Dict[str, Any],
    source_label: str,
) -> Dict[str, Any]:
    copy_contract = (
        package.get("audience_copy")
        if isinstance(package.get("audience_copy"), dict)
        else {}
    )
    attribution = (
        copy_contract.get("source_attribution")
        if isinstance(copy_contract.get("source_attribution"), dict)
        else {}
    )
    rendered_values = [
        copy_contract.get("program_label"),
        copy_contract.get("logic_label"),
        copy_contract.get("risk_label"),
        copy_contract.get("summary_label"),
        attribution.get("template"),
        source_label,
        "机构研报解读",
        "逻辑拆解",
        "风险提示",
        "研报关键数据",
        "研报原文定位",
        "本段要点",
        "核心结论",
    ]
    for value in rendered_values:
        if value:
            _assert_audience_copy_safe(str(value))
    return {
        "passed": True,
        "production_metadata_visibility": "manifest_only",
        "source_display_policy": attribution.get("display_policy"),
        "forbidden_terms_checked": True,
        "checked_rendered_copy_count": len(
            [item for item in rendered_values if item]
        ),
    }


def _segment_audio_offset(
    segments: Sequence[Dict[str, Any]],
    segment_id: str,
    audio_duration: float,
    clip_duration: float,
) -> float:
    lengths = [
        len(
            str(
                item.get("narration", {}).get("text")
                if isinstance(item, dict)
                and isinstance(item.get("narration"), dict)
                else ""
            )
        )
        for item in segments
    ]
    total_chars = max(1, sum(lengths))
    preceding_chars = 0
    for item, length in zip(segments, lengths):
        if str(item.get("segment_id") or "") == segment_id:
            break
        preceding_chars += length
    offset = audio_duration * preceding_chars / total_chars
    return max(0.0, min(offset, max(0.0, audio_duration - clip_duration)))


def _mux_audio_excerpt(
    video_path: Path,
    audio_path: Path,
    *,
    offset_seconds: float,
    duration_seconds: float,
) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("复用真实配音需要 ffmpeg")
    output_path = video_path.with_name(f"{video_path.stem}.with-audio.mp4")
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{offset_seconds:.3f}",
        "-t",
        f"{duration_seconds:.3f}",
        "-i",
        str(audio_path),
        "-i",
        str(video_path),
        "-map",
        "1:v:0",
        "-map",
        "0:a:0",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "160k",
        "-shortest",
        str(output_path),
    ]
    _run_ffmpeg(command, output_path)
    output_path.replace(video_path)


def _build_preview_audio_track(
    audio_path: Path,
    windows: Sequence[Dict[str, Any]],
    output_path: Path,
) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("复用真实配音需要 ffmpeg")
    filters = []
    labels = []
    for index, window in enumerate(windows):
        offset = float(window["offset_seconds"])
        duration = float(window["duration_seconds"])
        label = f"a{index}"
        filters.append(
            f"[0:a]atrim=start={offset:.3f}:duration={duration:.3f},"
            f"asetpts=PTS-STARTPTS[{label}]"
        )
        labels.append(f"[{label}]")
    filters.append(
        f"{''.join(labels)}concat=n={len(labels)}:v=0:a=1[outa]"
    )
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(audio_path),
        "-filter_complex",
        ";".join(filters),
        "-map",
        "[outa]",
        "-c:a",
        "aac",
        "-b:a",
        "160k",
        str(output_path),
    ]
    _run_ffmpeg(command, output_path)


def _mux_audio_track(video_path: Path, audio_path: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("复用真实配音需要 ffmpeg")
    output_path = video_path.with_name(f"{video_path.stem}.with-audio.mp4")
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(video_path),
        "-i",
        str(audio_path),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-shortest",
        str(output_path),
    ]
    _run_ffmpeg(command, output_path)
    output_path.replace(video_path)


def _normalize_program_audio(video_path: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("节目响度归一化需要 ffmpeg")
    output_path = video_path.with_name(f"{video_path.stem}.normalized.mp4")
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(video_path),
        "-map",
        "0:v:0",
        "-map",
        "0:a:0",
        "-c:v",
        "copy",
        "-af",
        "loudnorm=I=-16:TP=-1.5:LRA=11",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    _run_ffmpeg(command, output_path)
    output_path.replace(video_path)


def _run_ffmpeg(command, output_path: Path) -> None:
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0 or not output_path.is_file():
        raise RuntimeError(
            f"FFmpeg 音频复用失败：{completed.stderr[-800:]}"
        )


def _render_segment_durations(
    segments: Sequence[Dict[str, Any]],
    total_duration: float,
    alignment: Dict[str, Any] | None,
) -> tuple[list[float], str]:
    if isinstance(alignment, dict) and isinstance(alignment.get("timeline"), list):
        rows = alignment["timeline"]
        if len(rows) != len(segments):
            raise ValueError("ASR 时间线段落数与完整渲染段落数不一致")
        durations = []
        cursor = 0.0
        for segment, row in zip(segments, rows):
            if str(row.get("segment_id") or "") != str(segment.get("segment_id") or ""):
                raise ValueError("ASR 时间线段落 ID 与导演包不一致")
            start = float(row.get("start_seconds") or 0.0)
            end = float(row.get("end_seconds") or 0.0)
            if abs(start - cursor) > 0.03 or end <= start:
                raise ValueError("ASR 时间线不连续或包含无效区间")
            durations.append(end - start)
            cursor = end
        if abs(cursor - total_duration) > 0.08:
            raise ValueError("ASR 时间线末尾与真实音频时长不一致")
        durations[-1] += total_duration - sum(durations)
        return durations, "existing_whole_audio_asr_alignment"
    return (
        _allocate_segment_durations(segments, total_duration),
        "test_character_ratio_estimate",
    )


def _allocate_segment_durations(
    segments: Sequence[Dict[str, Any]],
    total_duration: float,
) -> list[float]:
    weights = [
        max(
            1,
            len(
                str(
                    item.get("narration", {}).get("text")
                    if isinstance(item, dict)
                    and isinstance(item.get("narration"), dict)
                    else ""
                )
            ),
        )
        for item in segments
    ]
    total_weight = max(1, sum(weights))
    durations = [total_duration * weight / total_weight for weight in weights]
    if durations:
        durations[-1] += total_duration - sum(durations)
    return durations


def _split_state_durations(
    segment_duration: float,
    captions: Sequence[str] | None = None,
) -> list[float]:
    if captions:
        weights = [max(1, len(re.sub(r"\s+", "", item))) for item in captions]
    else:
        weights = [1, 1, 1, 1]
    total = max(1, sum(weights))
    values = [segment_duration * weight / total for weight in weights]
    values[-1] += segment_duration - sum(values)
    return values


def _render_detail_variation(
    source: Path,
    target: Path,
    *,
    source_label: str,
    sequence: int,
    media_type: str,
) -> None:
    with Image.open(source) as image:
        frame = image.convert("RGB")
    if media_type == "character_explainer":
        zoomed = frame.copy()
    else:
        inset_x = round(frame.width * 0.028)
        inset_y = round(frame.height * 0.028)
        crop = frame.crop(
            (
                inset_x,
                inset_y,
                frame.width - inset_x,
                frame.height - inset_y,
            )
        )
        zoomed = crop.resize(frame.size, Image.Resampling.LANCZOS)
    target.parent.mkdir(parents=True, exist_ok=True)
    zoomed.save(target, "JPEG", quality=93, optimize=True)


def _render_focus_card(
    target: Path,
    size,
    *,
    segment: Dict[str, Any],
    sequence: int,
    total: int,
    source_label: str,
) -> None:
    width, height = size
    palettes = (
        ("#081522", (109, 204, 222, 255)),
        ("#0b1720", (255, 208, 92, 255)),
        ("#071827", (126, 211, 180, 255)),
    )
    background, accent = palettes[(sequence - 1) % len(palettes)]
    canvas = Image.new("RGB", size, background)
    draw = ImageDraw.Draw(canvas, "RGBA")
    margin = round(width * 0.09)
    small_font = _load_overlay_font(max(22, round(width * 0.015)))
    title_font = _load_overlay_font(max(48, round(width * 0.036)))
    body_font = _load_overlay_font(max(31, round(width * 0.022)))
    draw.text(
        (margin, round(height * 0.1)),
        "判断落点",
        font=small_font,
        fill=accent,
    )
    draw.rectangle(
        (
            margin,
            round(height * 0.17),
            margin + round(width * 0.12),
            round(height * 0.179),
        ),
        fill=accent,
    )
    narration = (
        segment.get("narration", {}).get("text")
        if isinstance(segment.get("narration"), dict)
        else ""
    )
    takeaway = _audience_takeaway(segment, max_chars=22)
    layout = (sequence - 1) % 3
    if layout == 0:
        lines = _wrap_text(
            draw,
            takeaway,
            title_font,
            round(width * 0.75),
            max_lines=1,
        )
        draw.multiline_text(
            (margin, round(height * 0.25)),
            lines,
            font=title_font,
            fill=(243, 248, 251, 255),
            spacing=14,
        )
        draw.rounded_rectangle(
            (margin, round(height * 0.68), width - margin, round(height * 0.77)),
            radius=18,
            fill=(10, 33, 51, 230),
            outline=(72, 166, 190, 150),
            width=2,
        )
        draw.text(
            (margin + 28, round(height * 0.705)),
            _audience_context_line(segment),
            font=body_font,
            fill=accent,
        )
    elif layout == 1:
        draw.text(
            (margin, round(height * 0.25)),
            "一句话结论",
            font=body_font,
            fill=accent,
        )
        lines = _wrap_text(
            draw,
            takeaway,
            title_font,
            round(width * 0.66),
            max_lines=1,
        )
        draw.multiline_text(
            (margin, round(height * 0.34)),
            lines,
            font=title_font,
            fill=(243, 248, 251, 255),
            spacing=14,
        )
        draw.rounded_rectangle(
            (
                round(width * 0.78),
                round(height * 0.27),
                width - margin,
                round(height * 0.70),
            ),
            radius=24,
            fill=(14, 46, 62, 225),
            outline=accent,
            width=3,
        )
        draw.text(
            (round(width * 0.82), round(height * 0.38)),
            f"{sequence:02d}",
            font=_load_overlay_font(max(78, round(width * 0.065))),
            fill=accent,
        )
    else:
        draw.text(
            (margin, round(height * 0.25)),
            "判断路径",
            font=body_font,
            fill=accent,
        )
        checkpoints = _director_checkpoints(segment)
        card_top = round(height * 0.36)
        card_width = round((width - margin * 2 - 48) / 3)
        for index, checkpoint in enumerate(checkpoints[:3]):
            left = margin + index * (card_width + 24)
            draw.rounded_rectangle(
                (left, card_top, left + card_width, round(height * 0.70)),
                radius=20,
                fill=(10, 35 + index * 6, 52 + index * 8, 235),
                outline=accent,
                width=2,
            )
            draw.text(
                (left + 24, card_top + 24),
                f"0{index + 1}",
                font=small_font,
                fill=accent,
            )
            lines = _wrap_text(
                draw,
                checkpoint,
                body_font,
                card_width - 48,
                max_lines=2,
            )
            draw.multiline_text(
                (left + 24, card_top + 82),
                lines,
                font=body_font,
                fill=(230, 239, 244, 255),
                spacing=10,
            )
    target.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(target, "JPEG", quality=93, optimize=True)


def _write_approximate_srt(
    segments: Sequence[Dict[str, Any]],
    durations: Sequence[float],
    output_path: Path,
) -> int:
    cues = []
    cursor = 0.0
    cue_index = 1
    for segment, segment_duration in zip(segments, durations):
        narration = (
            segment.get("narration", {}).get("text")
            if isinstance(segment, dict)
            and isinstance(segment.get("narration"), dict)
            else ""
        )
        chunks = _caption_chunks(str(narration or ""), target_chars=28)
        weights = [max(1, len(item)) for item in chunks]
        weight_total = max(1, sum(weights))
        for chunk, weight in zip(chunks, weights):
            duration = segment_duration * weight / weight_total
            start = cursor
            end = cursor + duration
            cues.append(
                f"{cue_index}\n"
                f"{_srt_timestamp(start)} --> {_srt_timestamp(end)}\n"
                f"{chunk}\n"
            )
            cue_index += 1
            cursor = end
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(cues), encoding="utf-8")
    return len(cues)


def _write_state_srt(
    frame_manifest: Sequence[Dict[str, Any]],
    output_path: Path,
) -> int:
    cues = []
    cursor = 0.0
    cue_index = 1
    for segment in frame_manifest:
        for state in segment.get("states") or []:
            duration = float(state.get("duration_seconds") or 0.0)
            caption = str(state.get("caption") or "").strip()
            end = cursor + duration
            if caption:
                cues.append(
                    f"{cue_index}\n"
                    f"{_srt_timestamp(cursor)} --> {_srt_timestamp(end)}\n"
                    f"{caption}\n"
                )
                cue_index += 1
            cursor = end
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(cues), encoding="utf-8")
    return len(cues)


def _build_asr_caption_cues(
    segments: Sequence[Dict[str, Any]],
    alignment: Dict[str, Any] | None,
    total_duration: float,
) -> list[Dict[str, Any]]:
    if not isinstance(alignment, dict):
        return []
    timeline = alignment.get("timeline")
    metadata = alignment.get("asr_metadata")
    cache_path = (
        Path(str(metadata.get("cache_path") or ""))
        if isinstance(metadata, dict)
        else Path("")
    )
    if not isinstance(timeline, list) or len(timeline) != len(segments):
        return []
    if not cache_path.is_file():
        return []
    try:
        asr_rows = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(asr_rows, list):
        return []

    cues = []
    for segment, row in zip(segments, timeline):
        start = float(row.get("start_seconds") or 0.0)
        end = float(row.get("end_seconds") or 0.0)
        narration = _narration_text(segment)
        matched = []
        for asr in asr_rows:
            if not isinstance(asr, dict):
                continue
            cue_start = max(start, float(asr.get("start") or 0.0))
            cue_end = min(end, float(asr.get("end") or 0.0))
            if cue_end - cue_start < 0.08:
                continue
            matched.append(
                {
                    "start": cue_start,
                    "end": cue_end,
                    "weight": max(
                        1,
                        len(re.sub(r"\s+", "", str(asr.get("text") or ""))),
                    ),
                }
            )
        if not matched:
            matched = [{"start": start, "end": end, "weight": max(1, len(narration))}]
        chunks = _split_caption_for_screen(narration, target_chars=18)
        if not chunks:
            continue
        chunk_weights = [
            max(1, len(re.sub(r"\s+", "", item)))
            for item in chunks
        ]
        chunk_total = sum(chunk_weights)
        asr_total = sum(int(item["weight"]) for item in matched)
        asr_cumulative = []
        running = 0
        for item in matched:
            running += int(item["weight"])
            asr_cumulative.append(running)

        cursor = start
        consumed = 0
        matched_index = 0
        for index, (chunk, weight) in enumerate(zip(chunks, chunk_weights)):
            consumed += weight
            if index == len(chunks) - 1:
                cue_end = end
            else:
                target_weight = asr_total * consumed / max(1, chunk_total)
                while (
                    matched_index < len(asr_cumulative) - 1
                    and asr_cumulative[matched_index] < target_weight
                ):
                    matched_index += 1
                cue_end = float(matched[matched_index]["end"])
                while (
                    matched_index < len(matched) - 1
                    and cue_end - cursor < 0.62
                ):
                    matched_index += 1
                    cue_end = float(matched[matched_index]["end"])
            cue_end = max(cursor + 0.18, min(end, cue_end))
            cues.append(
                {
                    "start": round(cursor, 3),
                    "end": round(cue_end, 3),
                    "text": chunk,
                    "segment_id": str(segment.get("segment_id") or ""),
                }
            )
            cursor = cue_end
    if cues:
        cues = _merge_tiny_caption_cues(cues, min_chars=4, max_chars=22)
        cues[0]["start"] = 0.0
        cues[-1]["end"] = round(float(total_duration), 3)
    return cues


def _merge_tiny_caption_cues(
    cues: Sequence[Dict[str, Any]],
    *,
    min_chars: int,
    max_chars: int,
) -> list[Dict[str, Any]]:
    """Keep ASR timing useful without flashing one-character subtitle cards."""

    merged = [dict(item) for item in cues]
    index = 0
    while index < len(merged) - 1:
        current = merged[index]
        following = merged[index + 1]
        current_text = str(current.get("text") or "").strip()
        following_text = str(following.get("text") or "").strip()
        current_chars = len(re.sub(r"\s+", "", current_text))
        following_chars = len(re.sub(r"\s+", "", following_text))
        same_segment = current.get("segment_id") == following.get("segment_id")
        gap = float(following.get("start") or 0.0) - float(current.get("end") or 0.0)
        combined_chars = current_chars + following_chars
        if (
            same_segment
            and gap <= 0.42
            and combined_chars <= max_chars
            and (current_chars < min_chars or following_chars < min_chars)
        ):
            current["end"] = following.get("end")
            current["text"] = f"{current_text}{following_text}"
            merged.pop(index + 1)
            if index:
                index -= 1
            continue
        index += 1
    return merged


def _split_text_by_weights(text: str, weights: Sequence[int]) -> list[str]:
    value = " ".join(str(text or "").split())
    if not weights:
        return []
    if len(weights) == 1:
        return [value]
    total_weight = max(1, sum(max(1, int(item)) for item in weights))
    boundaries = []
    cumulative = 0
    punctuation = "。！？；，、,.!?; "
    for weight in weights[:-1]:
        cumulative += max(1, int(weight))
        target = round(len(value) * cumulative / total_weight)
        search_start = max((boundaries[-1] if boundaries else 0) + 1, target - 8)
        search_end = min(len(value) - 1, target + 8)
        boundary = None
        for offset in range(0, 9):
            for candidate in (target + offset, target - offset):
                if (
                    search_start <= candidate <= search_end
                    and value[candidate - 1] in punctuation
                ):
                    boundary = candidate
                    break
            if boundary is not None:
                break
        boundary = boundary or max(search_start, min(search_end, target))
        if boundaries and boundary <= boundaries[-1]:
            boundary = min(len(value) - 1, boundaries[-1] + 1)
        boundaries.append(boundary)
    chunks = []
    start = 0
    for boundary in boundaries:
        chunks.append(value[start:boundary].strip())
        start = boundary
    chunks.append(value[start:].strip())
    return chunks


def _split_caption_for_screen(text: str, *, target_chars: int) -> list[str]:
    value = " ".join(str(text or "").split())
    if not value:
        return []
    count = max(1, (len(re.sub(r"\s+", "", value)) + target_chars - 1) // target_chars)
    return [
        item
        for item in _split_caption_states(value, count)
        if str(item or "").strip()
    ]


def _write_caption_cues_srt(
    cues: Sequence[Dict[str, Any]],
    output_path: Path,
) -> int:
    rows = []
    for index, cue in enumerate(cues, start=1):
        rows.append(
            f"{index}\n"
            f"{_srt_timestamp(float(cue['start']))} --> "
            f"{_srt_timestamp(float(cue['end']))}\n"
            f"{str(cue.get('text') or '').strip()}\n"
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(rows), encoding="utf-8")
    return len(rows)


def _render_caption_band(
    target: Path,
    text: str,
    *,
    width: int = 1660,
    height: int = 180,
) -> None:
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    value = str(text or "").strip()
    if not value:
        image.save(target, "PNG", optimize=True)
        return
    draw = ImageDraw.Draw(image, "RGBA")
    font = _load_overlay_font(50)
    wrapped = _wrap_text(draw, value, font, width - 130, max_lines=2)
    spacing = 9
    text_height = _multiline_height(draw, wrapped, font, spacing)
    line_width = max(
        draw.textlength(line, font=font)
        for line in wrapped.splitlines()
    )
    box_width = min(width - 40, round(line_width + 88))
    box_height = min(height - 8, text_height + 38)
    left = (width - box_width) // 2
    top = (height - box_height) // 2
    draw.rounded_rectangle(
        (left, top, left + box_width, top + box_height),
        radius=14,
        fill=(2, 9, 17, 205),
    )
    draw.multiline_text(
        (width // 2, top + 18),
        wrapped,
        font=font,
        fill=(250, 252, 253, 255),
        anchor="ma",
        align="center",
        spacing=spacing,
        stroke_width=1,
        stroke_fill=(2, 9, 17, 255),
    )
    image.save(target, "PNG", optimize=True)


def _burn_caption_bands(
    video_path: Path,
    cues: Sequence[Dict[str, Any]],
    output_path: Path,
    *,
    caption_root: Path,
) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("细粒度字幕合成需要 ffmpeg")
    if not cues:
        shutil.copy2(video_path, output_path)
        return
    caption_root.mkdir(parents=True, exist_ok=True)
    rows = []
    cursor = 0.0
    file_index = 0

    def append_band(text_value: str, duration: float) -> None:
        nonlocal file_index
        if duration <= 0.02:
            return
        band_path = caption_root / f"caption-{file_index:04d}.png"
        _render_caption_band(band_path, text_value)
        escaped = str(band_path.resolve()).replace("'", "'\\''")
        rows.append(f"file '{escaped}'")
        rows.append(f"duration {duration:.6f}")
        file_index += 1

    for cue in cues:
        start = float(cue.get("start") or 0.0)
        end = float(cue.get("end") or 0.0)
        if start > cursor + 0.02:
            append_band("", start - cursor)
        append_band(str(cue.get("text") or ""), max(0.02, end - start))
        cursor = max(cursor, end)
    if not rows:
        shutil.copy2(video_path, output_path)
        return
    # The concat demuxer applies the last duration only when the final file is
    # repeated. This is a generated test artifact, not source-controlled input.
    rows.append(rows[-2])
    concat_path = caption_root / "caption-concat.txt"
    concat_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    band_video = caption_root / "caption-track.mov"
    band_command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_path),
        "-vf",
        "fps=30,format=argb",
        "-c:v",
        "qtrle",
        "-pix_fmt",
        "argb",
        str(band_video),
    ]
    _run_ffmpeg(band_command, band_video)
    overlay_command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(video_path),
        "-i",
        str(band_video),
        "-filter_complex",
        "[0:v][1:v]overlay=(W-w)/2:H-h-126:shortest=1[v]",
        "-map",
        "[v]",
        "-map",
        "0:a:0",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "18",
        "-c:a",
        "copy",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    _run_ffmpeg(overlay_command, output_path)


def _director_card_keywords(
    segment: Dict[str, Any],
    titles: Dict[str, Any],
) -> list[str]:
    """Derive title-card labels from this report instead of a fixed sample."""

    title = str(
        titles.get("landscape")
        or titles.get("content")
        or ""
    ).replace("\n", " ")
    narration = str(
        segment.get("narration", {}).get("text")
        if isinstance(segment.get("narration"), dict)
        else ""
    )
    candidates = re.split(r"[\s:：|｜/／,，;；、\-—]+", title)
    candidates.extend(re.split(r"[。！？；;，,]", narration))
    keywords = []
    for candidate in candidates:
        value = re.sub(r"\s+", "", str(candidate or "")).strip("：:，,。！？")
        if len(value) < 2:
            continue
        value = value[:16]
        if value not in keywords:
            keywords.append(value)
        if len(keywords) == 3:
            break
    return keywords or ["原始证据", "关键变量", "结论边界"]


def _audience_sentences(segment: Dict[str, Any]) -> list[str]:
    narration = str(
        segment.get("narration", {}).get("text")
        if isinstance(segment.get("narration"), dict)
        else ""
    )
    values = []
    for raw in re.split(r"[。！？；;]", narration):
        value = re.sub(r"\s+", " ", raw).strip(" ，,：:")
        value = re.sub(
            r"^(?:但是|但|而|那么|具体来看|换句话说|这意味着|也就是说|"
            r"我们(?:先|再|接着)?(?:看|来看|把目光转向)|如果我们[^，,]{0,12}[，,])",
            "",
            value,
        ).strip(" ，,：:")
        if len(value) >= 4:
            values.append(value)
    return values


def _audience_headline(
    segment: Dict[str, Any],
    *,
    max_chars: int = 26,
) -> str:
    """Return report-specific copy without exposing director instructions."""

    candidates = _audience_sentences(segment)
    if not candidates:
        return "研报中的关键判断"
    ranked = []
    for index, value in enumerate(candidates):
        displays = _complete_audience_clauses(value, max_chars=max_chars)
        for display in displays:
            score = 0
            if re.search(r"\d|百分之|增长|下降|上涨|回落|风险|利润|需求|供给", display):
                score += 3
            if re.search(r"意味着|核心|关键|因此|但|却|唯一|拐点", value):
                score += 2
            if 8 <= len(display) <= max_chars:
                score += 2
            score -= index * 0.15
            ranked.append((score, -index, display))
    if not ranked:
        return "关键变量决定结论能否持续"
    headline = max(ranked)[2]
    headline = re.sub(r"\s+", " ", headline).strip(" ，,。！？；;")
    _assert_audience_copy_safe(headline)
    return headline or "研报中的关键判断"


def _audience_takeaway(
    segment: Dict[str, Any],
    *,
    max_chars: int = 54,
) -> str:
    candidates = _audience_sentences(segment)
    if not candidates:
        return "关键判断仍需结合原研报数据与后续变化持续验证。"
    ranked = []
    for index, value in enumerate(candidates):
        for clause in _complete_audience_clauses(value, max_chars=max_chars):
            score = 0
            if re.search(r"意味着|因此|判断|结论|核心|风险|空间|仍|将|需要", clause):
                score += 4
            if re.search(r"\d|百分之|增长|下降|上涨|回落|利润|需求|供给", clause):
                score += 2
            score += index * 0.2
            ranked.append((score, index, clause))
    if not ranked:
        return "关键变量仍需用后续数据验证"
    takeaway = max(ranked)[2].strip(" ，,。")
    _assert_audience_copy_safe(takeaway)
    return takeaway


def _complete_audience_clauses(text: str, *, max_chars: int) -> list[str]:
    """Select complete clauses only; never display a sliced sentence fragment."""

    value = re.sub(r"\s+", " ", str(text or "")).strip(" ，,。！？；;：:")
    candidates = [value]
    candidates.extend(
        item.strip(" ，,。！？；;：:")
        for item in re.split(r"[，,：:、]", value)
    )
    output = []
    for item in candidates:
        item = re.sub(
            r"^(?:基于|按照|与此同时|相比之下|整体来看|具体来看|这意味着)",
            "",
            item,
        ).strip(" ，,。！？；;：:")
        if 6 <= len(item) <= max_chars and item not in output:
            output.append(item)
    return output


def _audience_context_line(segment: Dict[str, Any]) -> str:
    narration = " ".join(_audience_sentences(segment))
    if re.search(r"风险|不确定|谨慎|压力|下滑|疲软|衰退", narration):
        return "先确认风险是否兑现，再判断结论能否持续"
    if re.search(r"政策|供给|产能|库存", narration):
        return "结论取决于供给、库存与政策的后续变化"
    if re.search(r"需求|收入|利润|价格|增长", narration):
        return "继续跟踪需求、价格与盈利的边际变化"
    return "用后续数据验证这条判断是否继续成立"


def _director_checkpoints(segment: Dict[str, Any]) -> list[str]:
    """Use the current real narration/evidence as the risk-card checklist."""

    narration = str(
        segment.get("narration", {}).get("text")
        if isinstance(segment.get("narration"), dict)
        else ""
    )
    candidates = [
        item.strip()
        for item in re.split(r"[。！？；;]", narration)
        if item.strip()
    ]
    evidence_refs = (
        segment.get("evidence_refs")
        if isinstance(segment.get("evidence_refs"), list)
        else []
    )
    candidates.extend(
        str(item.get("quote") or "").strip()
        for item in evidence_refs
        if isinstance(item, dict) and str(item.get("quote") or "").strip()
    )
    checkpoints = []
    for candidate in candidates:
        compact = re.sub(r"\s+", " ", candidate).strip()
        if not compact:
            continue
        cjk_count = len(re.findall(r"[\u3400-\u9fff]", compact))
        latin_count = len(re.findall(r"[A-Za-z]", compact))
        if re.search(r"\bEXHIBIT\b", compact, re.IGNORECASE):
            continue
        if latin_count > max(8, cjk_count * 2):
            continue
        display = compact[:14].rstrip(" ，,：:")
        if display not in checkpoints:
            checkpoints.append(display)
        if len(checkpoints) == 3:
            break
    while len(checkpoints) < 3:
        checkpoints.append(("核对原始证据", "验证传导假设", "跟踪边际变化")[len(checkpoints)])
    return checkpoints


def _split_caption_states(text: str, count: int) -> list[str]:
    value = " ".join(str(text or "").split())
    if count <= 0:
        return []
    if not value:
        return [""] * count
    chunks = []
    remaining = value
    punctuation = "。！？；，、"
    for index in range(count - 1):
        slots = count - index
        target = max(1, round(len(remaining) / slots))
        search_start = max(1, target - 12)
        search_end = min(len(remaining) - 1, target + 12)
        boundary = None
        for offset in range(0, max(target - search_start, search_end - target) + 1):
            for candidate in (target + offset, target - offset):
                if (
                    search_start <= candidate <= search_end
                    and remaining[candidate - 1] in punctuation
                ):
                    boundary = candidate
                    break
            if boundary is not None:
                break
        boundary = boundary or target
        if (
            0 < boundary < len(remaining)
            and re.match(r"[A-Za-z0-9]", remaining[boundary - 1])
            and re.match(r"[A-Za-z0-9]", remaining[boundary])
        ):
            next_space = remaining.find(
                " ",
                boundary,
                min(len(remaining), boundary + 18),
            )
            previous_space = remaining.rfind(
                " ",
                max(0, boundary - 18),
                boundary,
            )
            if next_space > boundary:
                boundary = next_space
            elif previous_space > 0:
                boundary = previous_space
        chunks.append(remaining[:boundary].strip())
        remaining = remaining[boundary:]
    chunks.append(remaining.strip())
    return (chunks + [""] * count)[:count]


def _overlay_caption(image_path: Path, caption: str) -> None:
    caption = str(caption or "").strip()
    if not caption:
        return
    with Image.open(image_path) as image:
        frame = image.convert("RGB")
    draw = ImageDraw.Draw(frame, "RGBA")
    font = _load_overlay_font(38)
    max_width = round(frame.width * 0.78)
    wrapped = _wrap_text(
        draw,
        caption,
        font,
        max_width,
        max_lines=2,
    )
    spacing = 10
    text_height = _multiline_height(draw, wrapped, font, spacing)
    box_width = min(
        round(frame.width * 0.88),
        round(
            max(
                draw.textlength(line, font=font)
                for line in wrapped.splitlines()
            )
            + 100
        ),
    )
    box_height = text_height + 48
    footer_height = max(74, round(frame.height * 0.075))
    box_bottom = frame.height - footer_height - 18
    box_top = box_bottom - box_height
    box_left = (frame.width - box_width) // 2
    draw.rounded_rectangle(
        (box_left, box_top, box_left + box_width, box_bottom),
        radius=18,
        fill=(3, 12, 23, 225),
        outline=(111, 205, 222, 125),
        width=2,
    )
    draw.multiline_text(
        (frame.width // 2, box_top + 24),
        wrapped,
        font=font,
        fill=(248, 250, 252, 255),
        anchor="ma",
        align="center",
        spacing=spacing,
        stroke_width=1,
        stroke_fill=(4, 11, 19, 255),
    )
    frame.save(image_path, "JPEG", quality=94, optimize=True)


def _caption_chunks(text: str, *, target_chars: int) -> list[str]:
    compact = " ".join(str(text or "").split())
    if not compact:
        return []
    pieces = [
        item.strip()
        for item in re.split(r"(?<=[。！？；])", compact)
        if item.strip()
    ]
    chunks = []
    current = ""
    for piece in pieces:
        if current and len(current) + len(piece) > target_chars * 2:
            chunks.append(current)
            current = piece
        else:
            current = f"{current}{piece}"
    if current:
        chunks.append(current)
    result = []
    for chunk in chunks:
        result.extend(
            chunk[index : index + target_chars * 2]
            for index in range(0, len(chunk), target_chars * 2)
        )
    return result


def _srt_timestamp(seconds: float) -> str:
    milliseconds = max(0, round(seconds * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _burn_subtitles(
    video_path: Path,
    subtitle_path: Path,
    output_path: Path,
) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("烧录字幕需要 ffmpeg")
    subtitle = escape_subtitle_filter_path(subtitle_path)
    video_filter = (
        f"subtitles='{subtitle}':"
        "force_style='FontName=Arial Unicode MS,FontSize=18,"
        "PrimaryColour=&H00F8FAFC,OutlineColour=&H00131B29,"
        "BorderStyle=1,Outline=2,Shadow=0,Alignment=2,MarginV=92'"
    )
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(video_path),
        "-vf",
        video_filter,
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "20",
        "-c:a",
        "copy",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    _run_ffmpeg(command, output_path)


def _ellipsize_to_width(draw, text: str, font, max_width: int) -> str:
    value = str(text or "").strip()
    if draw.textlength(value, font=font) <= max_width:
        return value
    while value and draw.textlength(value, font=font) > max_width:
        value = value[:-1]
    return value.rstrip(" ，,：:")


def _wrap_text(draw, text: str, font, max_width: int, *, max_lines: int) -> str:
    compact = " ".join(str(text or "").split())
    if not compact:
        return ""
    lines = []
    current = ""
    tokens = re.findall(
        r"[A-Za-z0-9][A-Za-z0-9+./%$&_-]*|\s+|.",
        compact,
    )
    consumed_tokens = 0
    for token in tokens:
        candidate = f"{current}{token}"
        if current and draw.textlength(candidate, font=font) > max_width:
            lines.append(current.rstrip())
            current = token.lstrip()
            if len(lines) >= max_lines:
                break
        else:
            current = candidate
        consumed_tokens += 1
    if current and len(lines) < max_lines:
        lines.append(current.rstrip())
    if consumed_tokens < len(tokens) and lines:
        lines[-1] = _ellipsize_to_width(draw, lines[-1], font, max_width)
    return "\n".join(lines)


def _wrap_mixed_text(
    draw,
    text: str,
    font,
    max_width: int,
    *,
    max_lines: int,
) -> str:
    compact = " ".join(str(text or "").split())
    if not compact:
        return ""
    if not re.search(r"[A-Za-z]", compact) or " " not in compact:
        return _wrap_text(
            draw,
            compact,
            font,
            max_width,
            max_lines=max_lines,
        )
    words = compact.split()
    lines = []
    current = ""
    consumed_words = 0
    for word in words:
        candidate = f"{current} {word}".strip()
        if current and draw.textlength(candidate, font=font) > max_width:
            lines.append(current)
            if len(lines) >= max_lines:
                break
            current = word
        else:
            current = candidate
        consumed_words += 1
    if current and len(lines) < max_lines:
        lines.append(current)
    if consumed_words < len(words) and lines:
        lines[-1] = _ellipsize_to_width(
            draw,
            lines[-1],
            font,
            max_width,
        )
    return "\n".join(lines)


def _multiline_height(draw, text: str, font, spacing: int) -> int:
    if not text:
        return 0
    box = draw.multiline_textbbox((0, 0), text, font=font, spacing=spacing)
    return box[3] - box[1]


def _load_overlay_font(size: int):
    candidates = (
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    )
    for candidate in candidates:
        if Path(candidate).is_file():
            try:
                return ImageFont.truetype(candidate, size=size)
            except OSError:
                continue
    return ImageFont.load_default()


def _validate_test_output_root(root: Path) -> None:
    home = Path.home().resolve()
    if str(root) in {"/", str(home), str(PROJECT_ROOT.resolve())}:
        raise ValueError("测试输出目录不能是根目录、用户主目录或项目根目录")
    for forbidden in _FORBIDDEN_OUTPUT_ROOTS:
        forbidden = forbidden.resolve()
        if root == forbidden or forbidden in root.parents:
            raise ValueError(f"测试输出目录不能位于生产数据目录：{forbidden}")

    root_text = str(root)
    in_system_tmp = root_text.startswith(("/tmp/", "/private/tmp/", "/private/var/folders/"))
    has_test_marker = any(
        part in {".tmp", "tmp", "test", "tests"} or "test" in part.lower()
        for part in root.parts
    )
    if not in_system_tmp and not has_test_marker:
        raise ValueError("测试输出目录必须位于系统临时区、项目 .tmp 或显式 test 目录")


def _fit_frame(source: Path, target: Path, size, *, contain: bool = False) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as image:
        source_image = image.convert("RGB")
        if contain:
            frame = ImageOps.pad(
                source_image,
                size,
                method=Image.Resampling.LANCZOS,
                color="#07111f",
                centering=(0.5, 0.5),
            )
        else:
            frame = ImageOps.fit(
                source_image,
                size,
                method=Image.Resampling.LANCZOS,
                centering=(0.5, 0.5),
            )
        frame.save(target, "JPEG", quality=92, optimize=True)
