"""Stable HyperFrames adapter contract for report-video director packages."""

from __future__ import annotations

from typing import Any, Dict, Mapping, Sequence

from services.report_video_director import director_package_hash


HYPERFRAMES_SPEC_SCHEMA_VERSION = "report_video_hyperframes_spec_v1"


class HyperFramesAdapterError(ValueError):
    """Raised when a director package is unsafe or incomplete for adaptation."""


def build_hyperframes_spec(
    package: Mapping[str, Any],
    asset_plan: Mapping[str, Any],
    *,
    execution_mode: str,
) -> Dict[str, Any]:
    """Map director segments into deterministic HyperFrames clip contracts.

    This function writes no HTML and dispatches no render. It is the stable
    boundary used by a renderer adapter or a project template generator.
    """

    mode = str(execution_mode or "").strip().lower()
    if mode not in {"test", "production"}:
        raise HyperFramesAdapterError("execution_mode 必须是 test 或 production")
    if not isinstance(package, Mapping) or not isinstance(asset_plan, Mapping):
        raise HyperFramesAdapterError("导演包和素材计划必须是对象")
    package_hash = director_package_hash(dict(package))
    if str(asset_plan.get("package_hash") or "") != package_hash:
        raise HyperFramesAdapterError("素材计划与导演包 hash 不一致")
    if asset_plan.get("dispatch_default") != "blocked_until_mode_gate":
        raise HyperFramesAdapterError("素材计划未保持失败关闭")
    if asset_plan.get("publish_allowed") is not False:
        raise HyperFramesAdapterError("HyperFrames 适配不得允许发布")
    timeline = package.get("audio_timeline")
    timeline = timeline if isinstance(timeline, Mapping) else {}
    if mode == "production" and timeline.get("production_ready") is not True:
        raise HyperFramesAdapterError("生产适配必须使用 ASR 对齐时间线")
    enrichment = package.get("evidence_enrichment")
    enrichment = enrichment if isinstance(enrichment, Mapping) else {}
    if mode == "production" and enrichment.get("fail_closed") is not False:
        raise HyperFramesAdapterError("生产适配必须通过真实 PDF 证据核验")

    plan_by_id = {
        str(item.get("segment_id") or ""): item
        for item in asset_plan.get("items") or []
        if isinstance(item, Mapping)
    }
    clips = []
    for index, segment in enumerate(package.get("segments") or []):
        segment_id = str(segment.get("segment_id") or "")
        item = plan_by_id.get(segment_id)
        if not item:
            raise HyperFramesAdapterError(f"缺少素材计划：{segment_id}")
        timing = segment.get("timeline")
        timing = timing if isinstance(timing, Mapping) else {}
        start = _number(timing.get("start_seconds"))
        duration = _number(timing.get("duration_seconds"))
        if start is None or duration is None or duration <= 0:
            raise HyperFramesAdapterError(f"缺少有效时间线：{segment_id}")
        media_type = str(item.get("media_type") or "")
        clips.append(
            {
                "id": f"hf-{segment_id}",
                "segment_id": segment_id,
                "data_start": round(start, 6),
                "data_duration": round(duration, 6),
                "data_track_index": 1 + (index % 2),
                "role": _clip_role(media_type),
                "media_type": media_type,
                "source_pages": list(item.get("source_pages") or []),
                "evidence_ids": list(item.get("evidence_ids") or []),
                "transition": _transition_spec(
                    str(item.get("transition") or "crossfade"),
                    index=index,
                ),
                "motion_spec": item.get("motion_spec"),
                "ratios": item.get("ratios"),
                "source_trace_required": True,
                "caption_policy": "audio_timeline_segment",
            }
        )
    _validate_clip_timeline(
        clips,
        float(timeline.get("audio_seconds") or 0),
    )
    return {
        "schema_version": HYPERFRAMES_SPEC_SCHEMA_VERSION,
        "composition_id": f"report-director-{str(package.get('package_id') or 'package')}",
        "package_id": package.get("package_id"),
        "package_hash": package_hash,
        "execution_mode": mode,
        "width": 1920,
        "height": 1080,
        "duration_seconds": round(float(timeline["audio_seconds"]), 6),
        "audio": {
            "path": timeline.get("audio_path"),
            "sha256": timeline.get("audio_sha256"),
            "track_index": 10,
            "framework_owned_playback": True,
        },
        "visual_identity": {
            "design_contract": "institutional_finance_editorial_v1",
            "canvas": "#07111F",
            "panel": "#0D263A",
            "text": "#F4F8FB",
            "evidence": "#FFDB66",
            "analysis": "#6FCDDE",
        },
        "clips": clips,
        "deterministic": True,
        "publish_allowed": False,
        "render_dispatch_allowed": False,
        "required_checks": [
            "hyperframes_lint",
            "hyperframes_runtime",
            "hyperframes_layout",
            "hyperframes_motion",
            "hyperframes_contrast",
            "decoded_output",
        ],
    }


def authorize_hyperframes_render(
    spec: Mapping[str, Any],
    *,
    full_render_gate: Mapping[str, Any],
) -> Dict[str, Any]:
    """Bind a render authorization to both package and mode gate."""

    blockers = []
    if spec.get("schema_version") != HYPERFRAMES_SPEC_SCHEMA_VERSION:
        blockers.append("unsupported_hyperframes_spec")
    if spec.get("publish_allowed") is not False:
        blockers.append("publish_not_blocked")
    if full_render_gate.get("allowed") is not True:
        blockers.append("director_full_render_gate_blocked")
    if str(full_render_gate.get("package_hash") or "") != str(
        spec.get("package_hash") or ""
    ):
        blockers.append("gate_package_hash_mismatch")
    if str(full_render_gate.get("execution_mode") or "") != str(
        spec.get("execution_mode") or ""
    ):
        blockers.append("gate_execution_mode_mismatch")
    return {
        "allowed": not blockers,
        "blockers": blockers,
        "package_hash": spec.get("package_hash"),
        "execution_mode": spec.get("execution_mode"),
        "render_dispatch": (
            "authorized" if not blockers else "blocked"
        ),
        "publish_allowed": False,
    }


def _clip_role(media_type: str) -> str:
    if media_type in {"original_pdf", "pdf_highlight", "pdf_scroll"}:
        return "source_evidence"
    if media_type == "chart":
        return "source_chart_motion"
    if media_type in {"title_card", "logic_card"}:
        return "editorial_motion_graphics"
    if media_type == "character_explainer":
        return "delegated_character_asset"
    raise HyperFramesAdapterError(f"不支持的素材类型：{media_type}")


def _transition_spec(name: str, *, index: int) -> Dict[str, Any]:
    if index == 0:
        return {
            "type": "editorial_push",
            "duration_seconds": 0.5,
            "ease": "power3.inOut",
        }
    if name in {"push", "wipe", "match_move"}:
        return {
            "type": "editorial_push",
            "duration_seconds": 0.4,
            "ease": "power2.inOut",
        }
    if name == "fade_through_black":
        return {
            "type": "color_dip",
            "duration_seconds": 0.6,
            "ease": "sine.inOut",
        }
    return {
        "type": "blur_crossfade",
        "duration_seconds": 0.5,
        "ease": "sine.inOut",
    }


def _validate_clip_timeline(
    clips: Sequence[Mapping[str, Any]],
    total: float,
) -> None:
    if total <= 0 or not clips:
        raise HyperFramesAdapterError("HyperFrames 时间线为空")
    previous_end = 0.0
    for clip in clips:
        start = float(clip["data_start"])
        duration = float(clip["data_duration"])
        if abs(start - previous_end) > 0.02:
            raise HyperFramesAdapterError("HyperFrames 段落时间线不连续")
        previous_end = start + duration
    if abs(previous_end - total) > 0.05:
        raise HyperFramesAdapterError("HyperFrames 时间线末尾与音频不一致")


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
