"""Phase-one report-video director contract compiler and fail-closed gate.

This module deliberately has no database, queue, browser, image-generation, or
rendering side effects.  It turns an existing report script material payload
and an existing PDF evidence map into a mixed-media director package, then
checks whether low-cost previews and the mode-specific authorization permit a
full render.

The production orchestrator may call these pure functions, but must keep actual
asset dispatch and rendering behind :func:`evaluate_full_render_gate`.
"""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


DIRECTOR_PACKAGE_SCHEMA_VERSION = "report_video_director_package_v1"
DIRECTOR_INPUT_SCHEMA_VERSION = "report_video_master_input_v1"
PREVIEW_GATE_SCHEMA_VERSION = "report_video_preview_gate_v1"

ALLOWED_MEDIA_TYPES = frozenset(
    {
        "title_card",
        "original_pdf",
        "pdf_highlight",
        "pdf_scroll",
        "chart",
        "logic_card",
        "character_explainer",
    }
)
ALLOWED_TRANSITIONS = frozenset(
    {
        "cut",
        "crossfade",
        "fade_through_black",
        "push",
        "wipe",
        "match_move",
    }
)
ALLOWED_RATIOS = ("16:9", "9:16")
ALLOWED_SHOT_INTENTS = frozenset(
    {
        "hook_conflict",
        "reveal_mechanism",
        "compare",
        "prove_source",
        "explain_causality",
        "escalate",
        "turn_risk",
        "resolve",
    }
)
DIRECTOR_REFERENCE_LIBRARY_VERSION = "director_reference_library_v1"
HIGH_COST_MODULES = frozenset(
    {
        "report_stick_figure_scene_images",
        "report_doraemon_scene_images",
        "report_video_full_render",
        "report_video_remotion_full_render",
        "hyperframes_full_render",
        "publisher",
    }
)

_MEDIA_MODULES = {
    "title_card": "report_visual_plan",
    "original_pdf": "pdf_page_renderer",
    "pdf_highlight": "pdf_page_renderer",
    "pdf_scroll": "pdf_scroll_video",
    "chart": "report_visual_plan",
    "logic_card": "report_visual_plan",
    "character_explainer": "report_stick_figure_or_doraemon",
}
_SENTENCE_BREAK_RE = re.compile(r"(?<=[。！？!?；;])\s*|\n+")
_NUMBER_RE = re.compile(r"(?:\d+(?:\.\d+)?%?|同比|环比|目标价|估值|倍|亿元|万亿)")
_RISK_RE = re.compile(r"(?:风险|不确定|注意|谨慎|假设|边界|挑战|警惕)")
_AUDIENCE_FORBIDDEN_RE = re.compile(
    r"(?:\bevidence\b|\bpdf\s*evidence\b|\beditor\b|\bdirector\b|"
    r"\bpreview\b|\bdetail\b|\btask\b|\bapproval\b|"
    r"制作元数据|制作后台|编辑状态|素材类型|任务标签|审批标签)",
    re.IGNORECASE,
)


class DirectorContractError(ValueError):
    """Raised when a director package cannot be built from the supplied input."""


def split_director_narration(
    script: str,
    *,
    target_segment_chars: int = 130,
) -> List[str]:
    """Return the exact narration chunks used by the director compiler."""

    target_segment_chars = max(60, min(int(target_segment_chars or 130), 260))
    return _split_script(_clean_text(script), target_chars=target_segment_chars)


def build_director_package(
    material: Dict[str, Any],
    evidence_map: Sequence[Dict[str, Any]],
    *,
    source_task_id: Optional[str] = None,
    package_id: Optional[str] = None,
    revision: int = 1,
    target_segment_chars: int = 130,
    execution_mode: str = "production",
) -> Dict[str, Any]:
    """Compile an existing script material and evidence map into a draft package.

    ``material`` accepts the payload already returned by
    ``_material_task_payload``: ``structured_result``,
    ``final_script_for_tts``, ``title`` and ``pdf`` are reused directly.
    ``evidence_map`` is expected to come from the existing Gemini/PDF evidence
    stage; this function never reparses the PDF or invents evidence.
    """

    if not isinstance(material, dict):
        raise DirectorContractError("material 必须是对象")
    structured = material.get("structured_result")
    structured = structured if isinstance(structured, dict) else {}
    script = _clean_text(
        material.get("final_script_for_tts")
        or structured.get("final_script_for_tts")
    )
    if not script:
        raise DirectorContractError("缺少已有 final_script_for_tts，导演层不负责重写口播")
    title = _clean_text(
        structured.get("title")
        or structured.get("landscape_title")
        or material.get("title")
        or (material.get("pdf") or {}).get("topic_title")
        or (material.get("pdf") or {}).get("filename")
        or "研报解读"
    )
    source_task_id = str(
        source_task_id
        or material.get("job_id")
        or material.get("source_task_id")
        or ""
    ).strip()
    package_id = str(
        package_id
        or f"director:{source_task_id or hashlib.sha256(script.encode('utf-8')).hexdigest()[:16]}"
    )
    revision = max(1, int(revision or 1))
    target_segment_chars = max(60, min(int(target_segment_chars or 130), 260))
    execution_mode = str(execution_mode or "production").strip().lower()
    if execution_mode not in {"production", "test"}:
        raise DirectorContractError("execution_mode 必须是 production 或 test")

    evidence_rows = _normalize_evidence_map(evidence_map)
    narration_chunks = split_director_narration(
        script,
        target_segment_chars=target_segment_chars,
    )
    if not narration_chunks:
        raise DirectorContractError("口播文案无法切分")

    segments = []
    recent_media_types: List[str] = []
    for index, chunk in enumerate(narration_chunks, start=1):
        segment_id = f"seg-{index:03d}"
        evidence_refs = _evidence_for_chunk(
            chunk,
            evidence_rows,
            segment_id=segment_id,
            segment_index=index,
        )
        media_type = _choose_media_type(chunk, evidence_refs, index, len(narration_chunks))
        media_type = _avoid_repeated_media_grammar(
            media_type,
            evidence_refs,
            recent_media_types,
            is_last=index == len(narration_chunks),
        )
        seconds = _estimate_seconds(chunk)
        segments.append(
            _director_segment(
                segment_id=segment_id,
                sequence=index,
                text=chunk,
                evidence_refs=evidence_refs,
                media_type=media_type,
                seconds=seconds,
                title=title,
            )
        )
        recent_media_types = (recent_media_types + [media_type])[-2:]

    chapters = _build_chapters(segments)
    preview_plan = _build_preview_plan(segments)
    pdf = material.get("pdf") if isinstance(material.get("pdf"), dict) else {}
    institution = _infer_institution(material, pdf)
    package = {
        "schema_version": DIRECTOR_PACKAGE_SCHEMA_VERSION,
        "input_schema_version": DIRECTOR_INPUT_SCHEMA_VERSION,
        "package_id": package_id,
        "revision": revision,
        "source": {
            "material_task_id": source_task_id or None,
            "material_feature": material.get("feature"),
            "pdf_asset_id": pdf.get("attachment_id"),
            "pdf_filename": pdf.get("filename"),
            "pdf_path": pdf.get("path") or pdf.get("local_path"),
            "title": title,
            "script_sha256": hashlib.sha256(script.encode("utf-8")).hexdigest(),
            "script_chars": len(script),
            "reuse_only": True,
        },
        "production_metadata": {
            "visibility": "manifest_only",
            "source_ref": "$.source",
            "fields": [
                "task_id",
                "asset_id",
                "package_hash",
                "evidence_id",
                "media_type",
                "dispatch_module",
                "review_status",
                "approval_status",
            ],
            "must_not_render": True,
        },
        "audience_copy": {
            "program_label": "机构研报解读",
            "logic_label": "逻辑拆解",
            "risk_label": "风险提示",
            "summary_label": "核心结论",
            "source_attribution": {
                "institution": institution,
                "template": "来源：{institution}研报，第{page}页",
                "display_policy": "evidence_detail_only",
            },
            "forbidden_production_terms": [
                "evidence",
                "PDF evidence",
                "editor",
                "director",
                "preview",
                "detail",
                "task",
                "approval",
                "素材类型",
                "状态",
                "任务",
                "审批",
            ],
        },
        "viewer_quality_contract": {
            "reference_library": {
                "version": DIRECTOR_REFERENCE_LIBRARY_VERSION,
                "principle_ids": [
                    "RFL-01-one-idea-one-visual-grammar",
                    "RFL-02-object-led-causal-reveal",
                    "RFL-03-semantic-escalation-not-decoration",
                    "RFL-04-data-then-mechanism-then-proof",
                    "RFL-05-contrast-for-turns-not-every-card",
                ],
                "source_material_policy": "abstract_direction_only_no_reference_footage_or_assets",
            },
            "opening_hook": {
                "conflict_by_seconds": 3,
                "counterintuitive_data_by_seconds": 8,
                "viewer_question_by_seconds": 15,
                "cover_before_hook_allowed": False,
            },
            "card_copy": {
                "max_explanation_lines": 1,
                "max_subtitle_lines": 2,
                "automatic_ellipsis_allowed": False,
            },
            "subtitle": {
                "font_scale_from_baseline": 1.2,
                "player_safe_area_required": True,
                "asr_boundary_alignment_required": True,
            },
            "visual_storytelling": {
                "max_non_card_gap_seconds": 30,
                "visual_climax_required": True,
                "object_led_ai_infrastructure_required": True,
                "shot_intent_required": True,
                "max_consecutive_same_media_type": 2,
                "mechanism_before_summary_required": True,
            },
            "transition": {
                "semantic_transition_required": True,
                "decorative_transition_only_forbidden": True,
            },
            "pdf_evidence_sequence": [
                "full_page_source",
                "push_to_key_region",
                "dim_irrelevant_region",
                "redraw_key_metric_or_curve",
            ],
        },
        "master_control": {
            "objective": "mixed_media_report_video",
            "execution_mode": execution_mode,
            "full_render_default": "blocked",
            "auto_review_role": "advisory_only",
            "human_approval_required": execution_mode == "production",
            "test_mode_policy": {
                "isolated_output_required": True,
                "publish_allowed": False,
                "production_task_mutation_allowed": False,
            },
            "high_cost_modules": sorted(HIGH_COST_MODULES),
        },
        "titles": {
            "content": title,
            "landscape": _clean_text(
                structured.get("landscape_title")
                or structured.get("cover_title")
                or title
            ),
            "landscape_subtitle": _clean_text(
                structured.get("landscape_subtitle")
                or structured.get("cover_subtitle")
            ),
            "portrait": _clean_text(structured.get("vertical_title") or title),
            "portrait_subtitle": _clean_text(structured.get("vertical_subtitle")),
        },
        "audio": {
            "reuse_task_id": material.get("audio_task_id"),
            "reuse_audio_path": material.get("audio_path") or material.get("wav_path"),
            "reuse_subtitle_path": material.get("subtitle_path"),
            "script_text": script,
            "generation_requested": False,
        },
        "chapters": chapters,
        "segments": segments,
        "preview_gate": {
            "schema_version": PREVIEW_GATE_SCHEMA_VERSION,
            "status": "awaiting_director_validation",
            "required_sequence": [
                "director_package_valid",
                "representative_keyframes_completed",
                "representative_preview_clips_completed",
                "mode_authorized",
            ],
            "authorization_policy": {
                "production": "human_approval_bound_to_current_package_hash",
                "test": "explicit_isolated_test_authorization",
            },
            "keyframe_plan": preview_plan["keyframes"],
            "preview_clip_plan": preview_plan["preview_clips"],
            "artifacts": {"keyframes": [], "preview_clips": []},
            "human_review": {
                "decision": "pending",
                "reviewer_id": None,
                "reviewed_at": None,
                "approved_package_hash": None,
                "notes": None,
            },
            "full_render": {
                "allowed": False,
                "status": "blocked",
                "reason": "必须先完成导演包校验、代表预览和当前模式授权",
            },
        },
        "integration": {
            "composition_candidates": [
                "existing_ffmpeg_report_video_renderer",
                "existing_remotion_renderer",
                "hyperframes",
            ],
            "preferred_phase_one": "existing_low_cost_preview_renderers",
            "hyperframes_role": "post-approval composition adapter or bounded preview adapter",
            "dispatch_policy": "director_segment_dispatch_only",
        },
    }
    package["package_hash"] = director_package_hash(package)
    validation = validate_director_package(package)
    package["preview_gate"]["status"] = (
        "awaiting_keyframes" if validation["valid"] else "director_package_invalid"
    )
    package["validation"] = validation
    return package


def validate_director_package(package: Dict[str, Any]) -> Dict[str, Any]:
    """Validate structure, evidence coverage, mixed media, and preview plan."""

    errors: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []
    if not isinstance(package, dict):
        return _validation_result(
            [{"code": "package_not_object", "path": "$", "message": "导演包必须是对象"}],
            [],
        )
    if package.get("schema_version") != DIRECTOR_PACKAGE_SCHEMA_VERSION:
        errors.append(_issue("schema_version", "$.schema_version", "导演包 schema_version 不受支持"))
    if not str(package.get("package_id") or "").strip():
        errors.append(_issue("missing_package_id", "$.package_id", "缺少 package_id"))
    if int(package.get("revision") or 0) < 1:
        errors.append(_issue("invalid_revision", "$.revision", "revision 必须大于等于 1"))
    master_control = package.get("master_control")
    master_control = master_control if isinstance(master_control, dict) else {}
    execution_mode = str(master_control.get("execution_mode") or "production").strip().lower()
    if execution_mode not in {"production", "test"}:
        errors.append(_issue("invalid_execution_mode", "$.master_control.execution_mode", "执行模式必须是 production 或 test"))
    if execution_mode == "production" and master_control.get("human_approval_required") is not True:
        errors.append(_issue("production_human_gate_required", "$.master_control", "生产模式必须强制人工放行"))
    if execution_mode == "test":
        test_policy = master_control.get("test_mode_policy")
        test_policy = test_policy if isinstance(test_policy, dict) else {}
        if (
            test_policy.get("isolated_output_required") is not True
            or test_policy.get("publish_allowed") is not False
            or test_policy.get("production_task_mutation_allowed") is not False
        ):
            errors.append(_issue("unsafe_test_policy", "$.master_control.test_mode_policy", "测试模式必须隔离输出、禁发布、禁改生产任务"))

    source = package.get("source")
    source = source if isinstance(source, dict) else {}
    if not source.get("script_sha256") or int(source.get("script_chars") or 0) <= 0:
        errors.append(_issue("invalid_source_script", "$.source", "缺少权威口播文案指纹"))
    if source.get("reuse_only") is not True:
        errors.append(_issue("reuse_only_required", "$.source.reuse_only", "阶段一必须只复用已有文案"))

    production_metadata = package.get("production_metadata")
    production_metadata = (
        production_metadata if isinstance(production_metadata, dict) else {}
    )
    if (
        production_metadata.get("visibility") != "manifest_only"
        or production_metadata.get("must_not_render") is not True
    ):
        errors.append(
            _issue(
                "production_metadata_visibility",
                "$.production_metadata",
                "制作元数据必须仅保留在 manifest，禁止进入观众画面",
            )
        )
    audience_copy = package.get("audience_copy")
    audience_copy = audience_copy if isinstance(audience_copy, dict) else {}
    attribution = audience_copy.get("source_attribution")
    attribution = attribution if isinstance(attribution, dict) else {}
    if (
        not _clean_text(audience_copy.get("program_label"))
        or attribution.get("display_policy") != "evidence_detail_only"
        or "{institution}" not in str(attribution.get("template") or "")
        or "{page}" not in str(attribution.get("template") or "")
    ):
        errors.append(
            _issue(
                "invalid_audience_copy_contract",
                "$.audience_copy",
                "观众文案必须使用栏目化标签，来源只允许在关键证据镜头出现",
            )
        )
    rendered_copy = {
        "program_label": audience_copy.get("program_label"),
        "logic_label": audience_copy.get("logic_label"),
        "risk_label": audience_copy.get("risk_label"),
        "summary_label": audience_copy.get("summary_label"),
        "source_institution": attribution.get("institution"),
        "source_template": attribution.get("template"),
    }
    leaked = [
        value
        for value in _iter_text_values(rendered_copy)
        if _AUDIENCE_FORBIDDEN_RE.search(value)
    ]
    if leaked:
        errors.append(
            _issue(
                "production_metadata_leak",
                "$.audience_copy",
                "观众文案包含制作或审计元数据",
                {"values": leaked[:5]},
            )
        )

    viewer_contract = package.get("viewer_quality_contract")
    viewer_contract = (
        viewer_contract if isinstance(viewer_contract, dict) else {}
    )
    opening_hook = viewer_contract.get("opening_hook")
    opening_hook = opening_hook if isinstance(opening_hook, dict) else {}
    card_copy = viewer_contract.get("card_copy")
    card_copy = card_copy if isinstance(card_copy, dict) else {}
    subtitle_contract = viewer_contract.get("subtitle")
    subtitle_contract = (
        subtitle_contract if isinstance(subtitle_contract, dict) else {}
    )
    visual_story = viewer_contract.get("visual_storytelling")
    visual_story = visual_story if isinstance(visual_story, dict) else {}
    reference_library = viewer_contract.get("reference_library")
    reference_library = reference_library if isinstance(reference_library, dict) else {}
    transition_contract = viewer_contract.get("transition")
    transition_contract = transition_contract if isinstance(transition_contract, dict) else {}
    evidence_sequence = viewer_contract.get("pdf_evidence_sequence")
    if (
        opening_hook.get("conflict_by_seconds") != 3
        or opening_hook.get("viewer_question_by_seconds") != 15
        or opening_hook.get("cover_before_hook_allowed") is not False
        or card_copy.get("max_explanation_lines") != 1
        or card_copy.get("max_subtitle_lines") != 2
        or card_copy.get("automatic_ellipsis_allowed") is not False
        or float(subtitle_contract.get("font_scale_from_baseline") or 0) < 1.2
        or subtitle_contract.get("player_safe_area_required") is not True
        or float(visual_story.get("max_non_card_gap_seconds") or 999) > 30
        or visual_story.get("visual_climax_required") is not True
        or visual_story.get("shot_intent_required") is not True
        or int(visual_story.get("max_consecutive_same_media_type") or 0) != 2
        or visual_story.get("mechanism_before_summary_required") is not True
        or reference_library.get("version") != DIRECTOR_REFERENCE_LIBRARY_VERSION
        or reference_library.get("source_material_policy")
        != "abstract_direction_only_no_reference_footage_or_assets"
        or transition_contract.get("semantic_transition_required") is not True
        or transition_contract.get("decorative_transition_only_forbidden") is not True
        or evidence_sequence
        != [
            "full_page_source",
            "push_to_key_region",
            "dim_irrelevant_region",
            "redraw_key_metric_or_curve",
        ]
    ):
        errors.append(
            _issue(
                "invalid_viewer_quality_contract",
                "$.viewer_quality_contract",
                "导演包必须约束参考库原则、镜头意图、前三秒矛盾、15秒问题、卡片/字幕可读性、30秒非卡片镜头、视觉高潮与四步证据镜头",
            )
        )

    chapters = package.get("chapters")
    chapters = chapters if isinstance(chapters, list) else []
    segments = package.get("segments")
    segments = segments if isinstance(segments, list) else []
    if not chapters:
        errors.append(_issue("missing_chapters", "$.chapters", "至少需要一个章节"))
    if not segments:
        errors.append(_issue("missing_segments", "$.segments", "至少需要一个导演段落"))
        return _validation_result(errors, warnings)

    segment_ids = set()
    media_types = set()
    consecutive_media = 0
    previous_media = None
    for index, segment in enumerate(segments):
        path = f"$.segments[{index}]"
        if not isinstance(segment, dict):
            errors.append(_issue("segment_not_object", path, "导演段落必须是对象"))
            continue
        segment_id = str(segment.get("segment_id") or "").strip()
        if not segment_id or segment_id in segment_ids:
            errors.append(_issue("invalid_segment_id", f"{path}.segment_id", "segment_id 缺失或重复"))
        segment_ids.add(segment_id)
        narration = segment.get("narration")
        narration = narration if isinstance(narration, dict) else {}
        if not _clean_text(narration.get("text")):
            errors.append(_issue("missing_narration", f"{path}.narration.text", "口播段落不能为空"))
        seconds = _number(narration.get("estimated_seconds"))
        if seconds is None or seconds <= 0 or seconds > 45:
            errors.append(_issue("invalid_duration", f"{path}.narration.estimated_seconds", "段落时长必须在 0-45 秒"))
        direction = segment.get("direction")
        direction = direction if isinstance(direction, dict) else {}
        shot_intent = str(direction.get("shot_intent") or "")
        if shot_intent not in ALLOWED_SHOT_INTENTS:
            errors.append(_issue("invalid_shot_intent", f"{path}.direction.shot_intent", "每段必须声明观众镜头意图"))
        if not isinstance(direction.get("reference_rule_ids"), list) or not direction.get("reference_rule_ids"):
            errors.append(_issue("missing_reference_rule_ids", f"{path}.direction.reference_rule_ids", "每段必须声明采用的参考库规则"))
        evidence_refs = segment.get("evidence_refs")
        evidence_refs = evidence_refs if isinstance(evidence_refs, list) else []
        if not evidence_refs:
            errors.append(_issue("missing_evidence", f"{path}.evidence_refs", "每段口播必须有 PDF 证据映射"))
        else:
            for evidence_index, evidence in enumerate(evidence_refs):
                evidence_path = f"{path}.evidence_refs[{evidence_index}]"
                if not isinstance(evidence, dict) or not evidence.get("evidence_id"):
                    errors.append(_issue("invalid_evidence", evidence_path, "证据引用缺少 evidence_id"))
                    continue
                if not evidence.get("page") and not evidence.get("source_note"):
                    errors.append(_issue("unlocatable_evidence", evidence_path, "证据必须包含页码或来源说明"))
                if evidence.get("page") and not evidence.get("quote"):
                    warnings.append(_issue("evidence_without_quote", evidence_path, "有页码但没有证据摘录，建议人工核对"))

        media = segment.get("media")
        media = media if isinstance(media, dict) else {}
        media_type = str(media.get("type") or "")
        if media_type not in ALLOWED_MEDIA_TYPES:
            errors.append(_issue("invalid_media_type", f"{path}.media.type", "未知素材类型"))
        else:
            media_types.add(media_type)
            if media_type == previous_media:
                consecutive_media += 1
            else:
                previous_media = media_type
                consecutive_media = 1
            if consecutive_media > 2:
                errors.append(_issue("repeated_media_grammar", f"{path}.media.type", "连续三段不得使用同一素材语法"))
        dispatch = segment.get("dispatch")
        dispatch = dispatch if isinstance(dispatch, dict) else {}
        if dispatch.get("module") != _MEDIA_MODULES.get(media_type):
            errors.append(_issue("invalid_dispatch", f"{path}.dispatch.module", "素材类型与下发模块不匹配"))
        if dispatch.get("full_asset_generation_allowed") is not False:
            errors.append(_issue("unsafe_dispatch_default", f"{path}.dispatch", "阶段一不得默认允许完整素材生成"))

        direction = segment.get("direction")
        direction = direction if isinstance(direction, dict) else {}
        if direction.get("transition_in") not in ALLOWED_TRANSITIONS:
            errors.append(_issue("invalid_transition", f"{path}.direction.transition_in", "缺少或不支持的入场转场"))
        variants = segment.get("variants")
        variants = variants if isinstance(variants, dict) else {}
        for ratio in ALLOWED_RATIOS:
            variant = variants.get(ratio)
            if not isinstance(variant, dict):
                errors.append(_issue("missing_ratio_variant", f"{path}.variants.{ratio}", f"缺少 {ratio} 差异配置"))
                continue
            if not variant.get("composition") or not variant.get("safe_area"):
                errors.append(_issue("incomplete_ratio_variant", f"{path}.variants.{ratio}", f"{ratio} 配置不完整"))

    if len(media_types) < 2:
        errors.append(_issue("not_mixed_media", "$.segments", "导演层至少需要两种素材类型"))
    if media_types and media_types <= {"character_explainer"}:
        errors.append(_issue("single_style_storyboard", "$.segments", "导演层不能退化为单一角色分镜"))

    chapter_segment_ids = []
    for chapter_index, chapter in enumerate(chapters):
        if not isinstance(chapter, dict):
            errors.append(_issue("chapter_not_object", f"$.chapters[{chapter_index}]", "章节必须是对象"))
            continue
        ids = chapter.get("segment_ids")
        ids = ids if isinstance(ids, list) else []
        chapter_segment_ids.extend(ids)
    missing_from_chapters = segment_ids.difference(chapter_segment_ids)
    unknown_in_chapters = set(chapter_segment_ids).difference(segment_ids)
    if missing_from_chapters or unknown_in_chapters:
        errors.append(
            _issue(
                "chapter_segment_mismatch",
                "$.chapters",
                "章节与段落引用不一致",
                {
                    "missing": sorted(missing_from_chapters),
                    "unknown": sorted(unknown_in_chapters),
                },
            )
        )

    _validate_preview_plan(package.get("preview_gate"), segment_ids, errors, warnings)
    expected_hash = director_package_hash(package)
    supplied_hash = str(package.get("package_hash") or "")
    if supplied_hash and supplied_hash != expected_hash:
        errors.append(_issue("package_hash_mismatch", "$.package_hash", "导演包内容已变化但 hash 未更新"))
    return _validation_result(errors, warnings)


def evaluate_full_render_gate(
    package: Dict[str, Any],
    *,
    preview_artifacts: Optional[Dict[str, Sequence[Dict[str, Any]]]] = None,
    human_review: Optional[Dict[str, Any]] = None,
    execution_mode: Optional[str] = None,
    test_authorization: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Return whether full asset production/rendering is authorized.

    This function never dispatches anything.  It is intentionally fail-closed:
    validation and every planned preview artifact are always required.
    Production additionally requires a human approval bound to the current
    package hash; isolated test mode requires explicit no-publish authorization.
    """

    validation = validate_director_package(package)
    package_hash = director_package_hash(package) if isinstance(package, dict) else ""
    gate = package.get("preview_gate") if isinstance(package, dict) else {}
    gate = gate if isinstance(gate, dict) else {}
    artifacts = preview_artifacts if isinstance(preview_artifacts, dict) else gate.get("artifacts")
    artifacts = artifacts if isinstance(artifacts, dict) else {}
    review = human_review if isinstance(human_review, dict) else gate.get("human_review")
    review = review if isinstance(review, dict) else {}
    master_control = package.get("master_control") if isinstance(package, dict) else {}
    master_control = master_control if isinstance(master_control, dict) else {}
    package_mode = str(master_control.get("execution_mode") or "production").strip().lower()
    mode = str(execution_mode or package_mode).strip().lower()

    blockers = []
    if execution_mode is not None and mode != package_mode:
        blockers.append(
            {
                "code": "execution_mode_mismatch",
                "message": "调用方执行模式与导演包模式不一致",
            }
        )
    if not validation["valid"]:
        blockers.append(
            {
                "code": "director_package_invalid",
                "message": "导演包校验未通过",
                "details": validation["errors"],
            }
        )

    keyframe_plan = gate.get("keyframe_plan") if isinstance(gate.get("keyframe_plan"), list) else []
    clip_plan = gate.get("preview_clip_plan") if isinstance(gate.get("preview_clip_plan"), list) else []
    keyframes = artifacts.get("keyframes") if isinstance(artifacts.get("keyframes"), list) else []
    clips = artifacts.get("preview_clips") if isinstance(artifacts.get("preview_clips"), list) else []
    missing_keyframes = _missing_preview_artifacts(keyframe_plan, keyframes, "preview_id")
    missing_clips = _missing_preview_artifacts(clip_plan, clips, "preview_id")
    if missing_keyframes:
        blockers.append(
            {
                "code": "representative_keyframes_incomplete",
                "message": "代表性关键帧未全部完成",
                "missing": missing_keyframes,
            }
        )
    if missing_clips:
        blockers.append(
            {
                "code": "representative_preview_clips_incomplete",
                "message": "2 至 3 段代表性样片未全部完成",
                "missing": missing_clips,
            }
        )

    if mode == "production":
        decision = str(review.get("decision") or "pending").strip().lower()
        if decision != "approved":
            blockers.append(
                {
                    "code": "human_approval_missing",
                    "message": "人工尚未放行" if decision == "pending" else f"人工决定为 {decision}",
                }
            )
        if not str(review.get("reviewer_id") or "").strip() or not str(review.get("reviewed_at") or "").strip():
            blockers.append(
                {
                    "code": "human_approval_audit_missing",
                    "message": "人工放行必须记录 reviewer_id 和 reviewed_at",
                }
            )
        if str(review.get("approved_package_hash") or "") != package_hash:
            blockers.append(
                {
                    "code": "human_approval_stale",
                    "message": "人工放行未绑定当前导演包版本或导演包已被修改",
                }
            )
    elif mode == "test":
        authorization = test_authorization if isinstance(test_authorization, dict) else {}
        if (
            authorization.get("isolated") is not True
            or authorization.get("publish_allowed") is not False
            or authorization.get("production_task_mutation_allowed") is not False
            or not str(authorization.get("test_task_id") or "").startswith("test:")
            or not str(authorization.get("output_root") or "").strip()
        ):
            blockers.append(
                {
                    "code": "unsafe_test_authorization",
                    "message": "测试自动放行必须显式声明隔离目录、test: 任务、禁发布和禁改生产任务",
                }
            )
    else:
        blockers.append(
            {
                "code": "invalid_execution_mode",
                "message": f"未知执行模式：{mode or '-'}",
            }
        )

    allowed = not blockers
    return {
        "schema_version": PREVIEW_GATE_SCHEMA_VERSION,
        "package_id": package.get("package_id") if isinstance(package, dict) else None,
        "revision": package.get("revision") if isinstance(package, dict) else None,
        "package_hash": package_hash,
        "execution_mode": mode,
        "allowed": allowed,
        "status": (
            "human_approved"
            if allowed and mode == "production"
            else ("test_auto_authorized" if allowed else "blocked")
        ),
        "blockers": blockers,
        "next_step": (
            (
                "full_asset_production_may_be_dispatched"
                if mode == "production"
                else "isolated_test_render_may_be_dispatched"
            )
            if allowed
            else _next_gate_step(blockers)
        ),
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
    }


def director_package_hash(package: Dict[str, Any]) -> str:
    """Return a stable content hash, excluding mutable gate/runtime fields."""

    if not isinstance(package, dict):
        return ""
    payload = deepcopy(package)
    payload.pop("package_hash", None)
    payload.pop("validation", None)
    gate = payload.get("preview_gate")
    if isinstance(gate, dict):
        gate.pop("status", None)
        gate.pop("artifacts", None)
        gate.pop("human_review", None)
        gate.pop("full_render", None)
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def human_approval(
    package: Dict[str, Any],
    *,
    reviewer_id: str,
    decision: str,
    notes: str = "",
    reviewed_at: Optional[str] = None,
) -> Dict[str, Any]:
    """Create an auditable review record bound to the current package hash."""

    decision = str(decision or "").strip().lower()
    if decision not in {"approved", "modify", "terminated"}:
        raise DirectorContractError("decision 必须是 approved、modify 或 terminated")
    reviewer_id = str(reviewer_id or "").strip()
    if not reviewer_id:
        raise DirectorContractError("reviewer_id 不能为空")
    return {
        "decision": decision,
        "reviewer_id": reviewer_id,
        "reviewed_at": reviewed_at or datetime.now(timezone.utc).isoformat(),
        "approved_package_hash": (
            director_package_hash(package) if decision == "approved" else None
        ),
        "notes": _clean_text(notes) or None,
        "source": "human",
        "auto_review": "advisory_only",
    }


def _normalize_evidence_map(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        return []
    result = []
    for index, raw in enumerate(rows, start=1):
        if not isinstance(raw, dict):
            continue
        evidence_id = str(raw.get("evidence_id") or raw.get("id") or f"evidence-{index:03d}").strip()
        page = _positive_int(raw.get("page") or raw.get("page_number"))
        bbox = raw.get("bbox")
        if not isinstance(bbox, list) or len(bbox) != 4:
            bbox = None
        result.append(
            {
                "evidence_id": evidence_id,
                "kind": str(raw.get("kind") or raw.get("type") or "pdf_quote").strip(),
                "page": page,
                "bbox": bbox,
                "quote": _clean_text(raw.get("quote") or raw.get("text")),
                "script_excerpt": _clean_text(
                    raw.get("script_excerpt")
                    or raw.get("narration_text")
                    or raw.get("mapped_text")
                ),
                "segment_id": str(raw.get("segment_id") or "").strip() or None,
                "segment_index": _positive_int(raw.get("segment_index")),
                "source_note": _clean_text(raw.get("source_note") or raw.get("source")),
                "asset_id": raw.get("asset_id") or raw.get("pdf_asset_id"),
                "confidence": _number(raw.get("confidence")),
            }
        )
    return result


def _split_script(script: str, *, target_chars: int) -> List[str]:
    units = [_clean_text(item) for item in _SENTENCE_BREAK_RE.split(script) if _clean_text(item)]
    if not units:
        return []
    chunks = []
    current = ""
    for unit in units:
        if len(unit) > target_chars * 2:
            hard_units = [
                unit[start : start + target_chars]
                for start in range(0, len(unit), target_chars)
            ]
        else:
            hard_units = [unit]
        for hard_unit in hard_units:
            if current and len(current) + len(hard_unit) > target_chars:
                chunks.append(current)
                current = hard_unit
            else:
                current = f"{current}{hard_unit}" if current else hard_unit
    if current:
        chunks.append(current)
    return chunks


def _evidence_for_chunk(
    chunk: str,
    rows: Sequence[Dict[str, Any]],
    *,
    segment_id: str,
    segment_index: int,
) -> List[Dict[str, Any]]:
    compact_chunk = _compact(chunk)
    matches: List[Tuple[int, Dict[str, Any]]] = []
    for row in rows:
        score = 0
        if row.get("segment_id") == segment_id:
            score = 100
        elif row.get("segment_index") == segment_index:
            score = 90
        else:
            for candidate in (row.get("script_excerpt"), row.get("quote")):
                compact_candidate = _compact(candidate)
                if not compact_candidate:
                    continue
                if compact_candidate in compact_chunk or compact_chunk in compact_candidate:
                    score = max(score, 80)
                elif len(compact_candidate) >= 12:
                    overlap = _longest_common_substring_size(compact_chunk, compact_candidate)
                    if overlap >= min(18, len(compact_candidate)):
                        score = max(score, 50 + min(overlap, 29))
        if score:
            matches.append((score, row))
    matches.sort(key=lambda item: (-item[0], str(item[1].get("evidence_id"))))
    return [
        {
            "evidence_id": row["evidence_id"],
            "kind": row["kind"],
            "page": row["page"],
            "bbox": row["bbox"],
            "quote": row["quote"],
            "source_note": row["source_note"],
            "asset_id": row["asset_id"],
            "confidence": row["confidence"],
        }
        for _, row in matches[:3]
    ]


def _choose_media_type(
    text: str,
    evidence_refs: Sequence[Dict[str, Any]],
    index: int,
    total: int,
) -> str:
    kinds = {str(item.get("kind") or "").lower() for item in evidence_refs}
    if index == 1:
        return "title_card"
    if (
        total <= 2
        and any(token in kind for kind in kinds for token in ("chart", "table", "figure"))
    ):
        return "chart"
    if _RISK_RE.search(text) or index == total:
        return "character_explainer"
    # Long reports often contain a chart on almost every page. Preserve the
    # chart authority without letting it collapse the whole program into one
    # repeated visual grammar.
    if index % 5 == 0:
        return "logic_card"
    if evidence_refs and len(text) >= 150 and index % 4 == 0:
        return "pdf_scroll"
    if (
        any(token in kind for kind in kinds for token in ("chart", "table", "figure"))
        and index % 3 != 0
    ):
        return "chart"
    if evidence_refs and _NUMBER_RE.search(text):
        return "pdf_highlight"
    if evidence_refs and len(text) >= 150:
        return "pdf_scroll"
    if evidence_refs and index % 3 == 0:
        return "pdf_highlight"
    if evidence_refs and index % 2 == 0:
        return "original_pdf"
    if index % 4 == 0:
        return "original_pdf"
    return "logic_card"


def _avoid_repeated_media_grammar(
    media_type: str,
    evidence_refs: Sequence[Dict[str, Any]],
    recent_media_types: Sequence[str],
    *,
    is_last: bool,
) -> str:
    """Keep proof authority while avoiding a third identical visual sentence."""
    if len(recent_media_types) < 2 or len(set(recent_media_types)) != 1:
        return media_type
    if media_type != recent_media_types[-1]:
        return media_type
    kinds = {str(item.get("kind") or "").lower() for item in evidence_refs}
    if any(token in kind for kind in kinds for token in ("chart", "table", "figure")):
        return "chart"
    if is_last or _RISK_RE.search(" ".join(str(item.get("quote") or "") for item in evidence_refs)):
        return "character_explainer"
    # A causal/object frame between two proof frames gives the viewer the
    # mechanism before returning to a new PDF page.
    return "logic_card"


def _director_segment(
    *,
    segment_id: str,
    sequence: int,
    text: str,
    evidence_refs: Sequence[Dict[str, Any]],
    media_type: str,
    seconds: float,
    title: str,
) -> Dict[str, Any]:
    shot_intent = _shot_intent(sequence, media_type, text)
    transition = "fade_through_black" if sequence == 1 else (
        "match_move" if media_type in {"pdf_highlight", "pdf_scroll", "original_pdf"} else "crossfade"
    )
    module = _MEDIA_MODULES[media_type]
    style = (
        "source_faithful"
        if media_type in {"original_pdf", "pdf_highlight", "pdf_scroll"}
        else ("finance_editorial" if media_type in {"title_card", "chart", "logic_card"} else "delegated_character_style")
    )
    return {
        "segment_id": segment_id,
        "sequence": sequence,
        "chapter_id": None,
        "narration": {
            "text": text,
            "estimated_seconds": seconds,
            "timing_policy": "reuse_audio_alignment_if_available",
        },
        "evidence_refs": list(evidence_refs),
        "media": {
            "type": media_type,
            "purpose": _media_purpose(media_type),
            "source_priority": "original_pdf_first",
        },
        "direction": {
            "shot_intent": shot_intent,
            "story_beat": _story_beat(shot_intent),
            "reference_rule_ids": _reference_rule_ids(shot_intent, media_type),
            "style": style,
            "shot_size": _shot_size(media_type),
            "camera": _camera(media_type),
            "motion": _motion(media_type),
            "overlay": _overlay(media_type),
            "transition_in": transition,
            "transition_out": "transition_owned_by_next_segment",
        },
        "dispatch": {
            "module": module,
            "action": _dispatch_action(media_type),
            "input": {
                "segment_id": segment_id,
                "narration_text": text,
                "evidence_ids": [item.get("evidence_id") for item in evidence_refs],
                "title": title,
            },
            "full_asset_generation_allowed": False,
            "phase_one_output": "keyframe_or_bounded_preview_only",
        },
        "variants": {
            "16:9": {
                "composition": _landscape_composition(media_type),
                "safe_area": "title_safe_8pct; subtitles_bottom_12pct",
                "evidence_layout": "document_and_explainer_side_by_side",
                "caption_lines": 2,
            },
            "9:16": {
                "composition": _portrait_composition(media_type),
                "safe_area": "platform_safe_top_12pct_bottom_18pct",
                "evidence_layout": "stacked_crop_with_readable_highlight",
                "caption_lines": 3,
            },
        },
    }


def _shot_intent(sequence: int, media_type: str, text: str) -> str:
    """Assign a viewer-facing intent before choosing a visual treatment.

    This is deliberately distinct from ``media.type``: a PDF close-up can
    prove a claim, while a chart can compare or escalate it.
    """
    if sequence == 1:
        return "hook_conflict"
    if _RISK_RE.search(text):
        return "turn_risk"
    if media_type in {"original_pdf", "pdf_highlight", "pdf_scroll"}:
        return "prove_source"
    if media_type == "chart":
        return "compare"
    if media_type == "character_explainer":
        return "resolve"
    if media_type == "logic_card":
        return "explain_causality"
    return "reveal_mechanism"


def _story_beat(shot_intent: str) -> str:
    return {
        "hook_conflict": "矛盾先行",
        "reveal_mechanism": "机制揭示",
        "compare": "数据对比",
        "prove_source": "证据落点",
        "explain_causality": "因果拆解",
        "escalate": "信息升级",
        "turn_risk": "风险打断",
        "resolve": "结论回收",
    }[shot_intent]


def _reference_rule_ids(shot_intent: str, media_type: str) -> List[str]:
    rule_ids = ["RFL-01-one-idea-one-visual-grammar"]
    if shot_intent in {"reveal_mechanism", "explain_causality"}:
        rule_ids.append("RFL-02-object-led-causal-reveal")
    if shot_intent in {"compare", "prove_source"}:
        rule_ids.append("RFL-04-data-then-mechanism-then-proof")
    if shot_intent in {"turn_risk", "escalate"}:
        rule_ids.append("RFL-05-contrast-for-turns-not-every-card")
    if media_type != "title_card":
        rule_ids.append("RFL-03-semantic-escalation-not-decoration")
    return rule_ids


def _build_chapters(segments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    count = len(segments)
    chapter_count = 1 if count <= 3 else (2 if count <= 7 else 3)
    chapters = []
    for chapter_index in range(chapter_count):
        start = round(chapter_index * count / chapter_count)
        end = round((chapter_index + 1) * count / chapter_count)
        selected = segments[start:end]
        if not selected:
            continue
        chapter_id = f"chapter-{chapter_index + 1:02d}"
        for segment in selected:
            segment["chapter_id"] = chapter_id
        chapters.append(
            {
                "chapter_id": chapter_id,
                "sequence": chapter_index + 1,
                "title": _chapter_title(chapter_index, chapter_count),
                "segment_ids": [item["segment_id"] for item in selected],
                "estimated_seconds": round(
                    sum(item["narration"]["estimated_seconds"] for item in selected),
                    2,
                ),
                "transition_to_next": (
                    "fade_through_black"
                    if chapter_index < chapter_count - 1
                    else "final_hold"
                ),
            }
        )
    return chapters


def _build_preview_plan(segments: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    keyframe_segments = _representative_preview_segments(
        segments,
        min(3, len(segments)),
    )
    clip_count = min(3, max(2, len(segments)))
    clip_segments = _representative_preview_segments(
        segments,
        min(clip_count, len(segments)),
    )
    if len(clip_segments) == 1:
        clip_segments = [clip_segments[0], clip_segments[0]]
    keyframes = []
    for index, segment in enumerate(keyframe_segments, start=1):
        ratio = "9:16" if index == len(keyframe_segments) and len(keyframe_segments) > 1 else "16:9"
        keyframes.append(
            {
                "preview_id": f"kf-{index:02d}-{segment['segment_id']}",
                "segment_id": segment["segment_id"],
                "ratio": ratio,
                "media_type": segment["media"]["type"],
                "max_cost": "single_frame",
                "required": True,
            }
        )
    preview_clips = []
    for index, segment in enumerate(clip_segments[:3], start=1):
        ratio = "9:16" if index == len(clip_segments[:3]) and len(clip_segments) > 2 else "16:9"
        preview_clips.append(
            {
                "preview_id": f"clip-{index:02d}-{segment['segment_id']}",
                "segment_id": segment["segment_id"],
                "ratio": ratio,
                "media_type": segment["media"]["type"],
                "duration_seconds": min(10.0, max(4.0, segment["narration"]["estimated_seconds"])),
                "max_cost": "bounded_preview_clip",
                "required": True,
            }
        )
    return {"keyframes": keyframes, "preview_clips": preview_clips}


def _representative_preview_segments(
    segments: Sequence[Dict[str, Any]],
    count: int,
) -> List[Dict[str, Any]]:
    result = _representative_segments(segments, count)
    if count < 2 or not segments:
        return result
    evidence_media = {
        "original_pdf",
        "pdf_highlight",
        "pdf_scroll",
        "chart",
    }
    target_position = (len(segments) - 1) * 0.4
    priority = {
        "chart": 0,
        "pdf_highlight": 1,
        "original_pdf": 2,
        "pdf_scroll": 3,
    }
    candidates = [
        (
            priority.get(item["media"].get("type"), 9),
            abs(index - target_position),
            item,
        )
        for index, item in enumerate(segments)
        if isinstance(item, dict)
        and isinstance(item.get("media"), dict)
        and item["media"].get("type") in evidence_media
    ]
    if not candidates:
        return result
    evidence_segment = min(candidates, key=lambda item: (item[0], item[1]))[2]
    replace_index = 1 if len(result) > 2 else len(result) - 1
    result[replace_index] = evidence_segment
    return result


def _validate_preview_plan(
    gate: Any,
    segment_ids: set,
    errors: List[Dict[str, Any]],
    warnings: List[Dict[str, Any]],
) -> None:
    if not isinstance(gate, dict):
        errors.append(_issue("missing_preview_gate", "$.preview_gate", "缺少低成本预览关口"))
        return
    if gate.get("schema_version") != PREVIEW_GATE_SCHEMA_VERSION:
        errors.append(_issue("preview_gate_schema", "$.preview_gate.schema_version", "预览关口版本不受支持"))
    keyframes = gate.get("keyframe_plan")
    keyframes = keyframes if isinstance(keyframes, list) else []
    clips = gate.get("preview_clip_plan")
    clips = clips if isinstance(clips, list) else []
    if not keyframes:
        errors.append(_issue("missing_keyframe_plan", "$.preview_gate.keyframe_plan", "至少需要代表性关键帧"))
    if len(clips) < 2 or len(clips) > 3:
        errors.append(_issue("invalid_preview_clip_count", "$.preview_gate.preview_clip_plan", "代表性样片必须为 2 至 3 段"))
    preview_ids = set()
    selected_media = set()
    for kind, rows in (("keyframe", keyframes), ("clip", clips)):
        for index, row in enumerate(rows):
            path = f"$.preview_gate.{kind}_plan[{index}]"
            if not isinstance(row, dict):
                errors.append(_issue("preview_not_object", path, "预览计划必须是对象"))
                continue
            preview_id = str(row.get("preview_id") or "")
            if not preview_id or preview_id in preview_ids:
                errors.append(_issue("invalid_preview_id", f"{path}.preview_id", "preview_id 缺失或重复"))
            preview_ids.add(preview_id)
            if row.get("segment_id") not in segment_ids:
                errors.append(_issue("unknown_preview_segment", f"{path}.segment_id", "预览引用了未知段落"))
            if row.get("ratio") not in ALLOWED_RATIOS:
                errors.append(_issue("invalid_preview_ratio", f"{path}.ratio", "预览比例必须是 16:9 或 9:16"))
            selected_media.add(row.get("media_type"))
    if len(selected_media) < 2 and len(segment_ids) > 1:
        warnings.append(_issue("preview_not_representative", "$.preview_gate", "预览计划覆盖的媒介类型不足两种"))
    full_render = gate.get("full_render")
    if not isinstance(full_render, dict) or full_render.get("allowed") is not False:
        errors.append(_issue("full_render_not_blocked", "$.preview_gate.full_render", "导演包初始状态必须阻止完整渲染"))


def _missing_preview_artifacts(
    plan: Sequence[Dict[str, Any]],
    artifacts: Sequence[Dict[str, Any]],
    key: str,
) -> List[str]:
    completed = {
        str(item.get(key) or "")
        for item in artifacts
        if isinstance(item, dict)
        and str(item.get("status") or "").lower() in {"completed", "approved", "succeeded"}
        and str(item.get(key) or "")
        and (item.get("file_path") or item.get("url") or item.get("artifact_id"))
    }
    return [
        str(item.get(key) or "")
        for item in plan
        if isinstance(item, dict)
        and item.get("required", True)
        and str(item.get(key) or "") not in completed
    ]


def _representative_segments(
    segments: Sequence[Dict[str, Any]],
    count: int,
) -> List[Dict[str, Any]]:
    if not segments or count <= 0:
        return []
    if count >= len(segments):
        return list(segments)
    indexes = sorted(
        {
            round(position * (len(segments) - 1) / max(count - 1, 1))
            for position in range(count)
        }
    )
    result = [segments[index] for index in indexes]
    if len(result) < count:
        for segment in segments:
            if segment not in result:
                result.append(segment)
            if len(result) >= count:
                break
    return result[:count]


def _validation_result(
    errors: Sequence[Dict[str, Any]],
    warnings: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    return {
        "schema_version": DIRECTOR_PACKAGE_SCHEMA_VERSION,
        "valid": not errors,
        "errors": list(errors),
        "warnings": list(warnings),
        "error_count": len(errors),
        "warning_count": len(warnings),
    }


def _issue(code: str, path: str, message: str, details: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = {"code": code, "path": path, "message": message}
    if details:
        payload["details"] = details
    return payload


def _next_gate_step(blockers: Sequence[Dict[str, Any]]) -> str:
    codes = {item.get("code") for item in blockers}
    if "director_package_invalid" in codes:
        return "fix_director_package"
    if "representative_keyframes_incomplete" in codes:
        return "generate_representative_keyframes"
    if "representative_preview_clips_incomplete" in codes:
        return "render_2_to_3_representative_preview_clips"
    return "await_human_review"


def _estimate_seconds(text: str) -> float:
    # Chinese financial narration is typically 3.5-4.5 chars/sec.  This is a
    # planning estimate only; existing audio/ASR alignment remains authoritative.
    return round(max(3.0, min(len(_compact(text)) / 4.0, 45.0)), 2)


def _media_purpose(media_type: str) -> str:
    return {
        "title_card": "建立主题与信息层级",
        "original_pdf": "展示原始证据语境",
        "pdf_highlight": "对齐口播与可核验原文",
        "pdf_scroll": "展示连续原文或长表格",
        "chart": "解释图表数据与变化",
        "logic_card": "提炼因果链路",
        "character_explainer": "解释抽象机制或风险",
    }[media_type]


def _shot_size(media_type: str) -> str:
    return "detail" if media_type in {"pdf_highlight", "chart"} else (
        "medium" if media_type == "character_explainer" else "wide"
    )


def _camera(media_type: str) -> str:
    return "evidence_locked" if media_type in {"original_pdf", "pdf_highlight", "chart"} else "editorial_static"


def _motion(media_type: str) -> str:
    return {
        "title_card": "layered_reveal",
        "original_pdf": "slow_push_in",
        "pdf_highlight": "highlight_then_push_in",
        "pdf_scroll": "bounded_vertical_scroll",
        "chart": "axis_then_data_reveal",
        "logic_card": "progressive_logic_reveal",
        "character_explainer": "bounded_character_gesture",
    }[media_type]


def _overlay(media_type: str) -> str:
    return "evidence_page_and_source" if media_type in {"original_pdf", "pdf_highlight", "pdf_scroll", "chart"} else "title_and_key_fact"


def _dispatch_action(media_type: str) -> str:
    return {
        "title_card": "render_existing_opening_cover",
        "original_pdf": "render_pdf_page",
        "pdf_highlight": "render_pdf_highlight",
        "pdf_scroll": "render_bounded_pdf_scroll",
        "chart": "render_existing_logic_or_chart_card",
        "logic_card": "render_existing_logic_card",
        "character_explainer": "delegate_single_segment_storyboard",
    }[media_type]


def _landscape_composition(media_type: str) -> str:
    if media_type in {"original_pdf", "pdf_highlight", "pdf_scroll", "chart"}:
        return "evidence_left_explanation_right"
    return "centered_editorial_with_subtitle_lane"


def _portrait_composition(media_type: str) -> str:
    if media_type in {"original_pdf", "pdf_highlight", "pdf_scroll", "chart"}:
        return "readable_evidence_crop_top_explanation_bottom"
    return "stacked_subject_title_captions"


def _chapter_title(index: int, count: int) -> str:
    if count == 1:
        return "核心观点与证据"
    labels = ("结论先行", "证据与机制", "影响与风险")
    return labels[min(index, len(labels) - 1)]


def _infer_institution(material: Dict[str, Any], pdf: Dict[str, Any]) -> str:
    structured = (
        material.get("structured_result")
        if isinstance(material.get("structured_result"), dict)
        else {}
    )
    candidates = (
        material.get("institution"),
        structured.get("institution"),
        pdf.get("institution"),
        pdf.get("filename"),
        structured.get("title"),
        material.get("title"),
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
    joined = " ".join(_clean_text(item) for item in candidates if item)
    for name in known:
        if name in joined:
            return name
    filename = _clean_text(pdf.get("filename"))
    prefix = re.split(r"[-—_:：|｜]", filename, maxsplit=1)[0].strip()
    if 1 < len(prefix) <= 12 and not re.search(r"(?:report|研报|报告)", prefix, re.I):
        return prefix
    return "机构"


def _iter_text_values(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _iter_text_values(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _iter_text_values(item)


def _positive_int(value: Any) -> Optional[int]:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _number(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _compact(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or ""))


def _longest_common_substring_size(left: str, right: str) -> int:
    if not left or not right:
        return 0
    previous = [0] * (len(right) + 1)
    longest = 0
    for left_char in left:
        current = [0]
        for index, right_char in enumerate(right, start=1):
            size = previous[index - 1] + 1 if left_char == right_char else 0
            current.append(size)
            longest = max(longest, size)
        previous = current
    return longest
