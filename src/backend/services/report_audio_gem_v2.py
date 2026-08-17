"""Structured handoff contract for the report-audio Gemini Gem.

This module is deliberately pure: browser, database, Celery, and DeepSeek
concerns stay in their existing layers.  It gives those layers one shared
definition of a response that is safe to consume without a normalizer.
"""

from __future__ import annotations

import copy
import json
import os
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional, Sequence, Tuple


SCHEMA_VERSION = "report_audio_gem_v2"
SUPPORTED_MODES = frozenset({"off", "shadow", "direct"})
DEFAULT_MODE = "shadow"


@dataclass(frozen=True)
class GemV2Issue:
    code: str
    message: str
    path: str = ""

    def to_dict(self) -> dict:
        payload = {"code": self.code, "message": self.message}
        if self.path:
            payload["path"] = self.path
        return payload


@dataclass(frozen=True)
class GemV2Validation:
    raw_text: str
    payload: Optional[dict]
    report: Optional[dict]
    errors: Tuple[GemV2Issue, ...]
    warnings: Tuple[GemV2Issue, ...] = ()

    @property
    def valid(self) -> bool:
        return self.payload is not None and self.report is not None and not self.errors

    @property
    def error_codes(self) -> Tuple[str, ...]:
        return tuple(issue.code for issue in self.errors)

    def diagnostics(self) -> dict:
        report = self.report or {}
        script = _clean_text(report.get("final_script_for_tts"))
        selected = report.get("selected_titles") if isinstance(report.get("selected_titles"), dict) else {}
        return {
            "schema_version": (self.payload or {}).get("schema_version"),
            "valid": self.valid,
            "error_codes": list(self.error_codes),
            "errors": [issue.to_dict() for issue in self.errors],
            "warnings": [issue.to_dict() for issue in self.warnings],
            "script_chars_without_whitespace": _count_without_whitespace(script),
            "script_paragraph_count": len(_script_paragraphs(script)),
            "selected_titles": copy.deepcopy(selected),
            "output_ready": bool((report.get("quality_control") or {}).get("output_ready"))
            if isinstance(report.get("quality_control"), dict)
            else False,
        }


def resolve_gem_v2_mode(value: Optional[str] = None) -> str:
    """Resolve the guarded rollout mode from an explicit value or environment."""

    raw = value if value is not None else os.getenv("REPORT_AUDIO_GEM_V2_MODE", DEFAULT_MODE)
    mode = str(raw or DEFAULT_MODE).strip().lower()
    if mode not in SUPPORTED_MODES:
        allowed = ", ".join(sorted(SUPPORTED_MODES))
        raise ValueError(f"Unsupported report-audio Gem V2 mode {raw!r}; expected one of: {allowed}")
    return mode


def build_gem_v2_trigger(
    pdf_filename: str,
    minimum_script_chars: int,
    output_mode: str = "separate",
    focus: str = "",
    allow_external_information: bool = False,
) -> str:
    """Build only the dynamic trigger; the long editorial rules live in the Gem."""

    filename = _clean_text(pdf_filename)
    minimum = max(1, int(minimum_script_chars or 1))
    mode = _clean_text(output_mode) or "separate"
    lines = [
        "请处理本次已上传的 PDF，并执行此 Gem 中已经配置的完整研报解读规则。",
        "本次运行参数：",
        f"- output_contract: {SCHEMA_VERSION}",
        f"- source_file: {filename}",
        f"- output_mode: {mode}",
        f"- final_script_min_chars_without_whitespace: {minimum}",
        f"- external_information_allowed: {'true' if allow_external_information else 'false'}",
    ]
    if _clean_text(focus):
        lines.append(f"- focus: {_clean_text(focus)}")
    lines.extend([
        "只返回一个可由标准解析器直接读取的完整 JSON 对象；不要输出 Markdown 代码围栏或额外说明。",
        "即使无法达到 output_ready，也要返回完整同构 JSON，并如实填写 blocking_reasons。",
    ])
    return "\n".join(lines)


def extract_gem_v2_payload(raw_text: Any) -> dict:
    """Extract a top-level JSON object while tolerating a code fence or brief prose."""

    if isinstance(raw_text, Mapping):
        return copy.deepcopy(dict(raw_text))
    text = str(raw_text or "").strip()
    if not text:
        raise ValueError("Gem response is empty")
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except (TypeError, ValueError, json.JSONDecodeError):
        pass

    decoder = json.JSONDecoder()
    first_object = None
    cursor = 0
    while True:
        start = text.find("{", cursor)
        if start < 0:
            break
        try:
            parsed, _end = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            cursor = start + 1
            continue
        if isinstance(parsed, dict):
            if parsed.get("schema_version") == SCHEMA_VERSION:
                return parsed
            if first_object is None:
                first_object = parsed
        cursor = start + 1
    if first_object is not None:
        return first_object
    raise ValueError("Gem response does not contain a complete JSON object")


def validate_gem_v2_response(
    raw_text: Any,
    minimum_script_chars: int,
    expected_pdf_filename: str,
    actual_pdf_page_count: Optional[int] = None,
    allow_external_information: bool = False,
) -> GemV2Validation:
    """Validate all cross-field invariants required for the direct path."""

    original_text = raw_text if isinstance(raw_text, str) else json.dumps(raw_text, ensure_ascii=False)
    issues = []

    def add(code: str, message: str, path: str = "") -> None:
        issues.append(GemV2Issue(code=code, message=message, path=path))

    try:
        payload = extract_gem_v2_payload(raw_text)
    except ValueError as exc:
        add("json_parse", str(exc), "$" )
        return GemV2Validation(original_text, None, None, tuple(issues))

    if payload.get("schema_version") != SCHEMA_VERSION:
        add("schema_version", f"schema_version must equal {SCHEMA_VERSION}", "$.schema_version")
    if payload.get("language") != "zh-CN":
        add("language", "language must equal zh-CN", "$.language")
    if payload.get("output_mode") not in {"separate", "merged"}:
        add("output_mode", "output_mode must be separate or merged", "$.output_mode")

    reports = payload.get("reports")
    if not isinstance(reports, list) or len(reports) != 1 or not isinstance(reports[0], dict):
        add("report_count", "Current workflow requires exactly one report object", "$.reports")
        return GemV2Validation(original_text, payload, None, tuple(issues))
    report = reports[0]

    if report.get("report_id") != "R1":
        add("report_id", "Single-report output must use report_id R1", "$.reports[0].report_id")
    if report.get("provider") != "gemini_gem":
        add("provider", "provider must equal gemini_gem", "$.reports[0].provider")

    expected_file = _clean_text(expected_pdf_filename)
    source_files = report.get("source_files")
    if not isinstance(source_files, list) or expected_file not in source_files:
        add("source_file_mismatch", "source_files must contain the exact uploaded PDF filename", "$.reports[0].source_files")

    boundary = report.get("material_boundary")
    if not isinstance(boundary, dict):
        add("material_boundary", "material_boundary must be an object", "$.reports[0].material_boundary")
        boundary = {}
    if boundary.get("provided_material_only") is not True:
        add("provided_material_only", "provided_material_only must be true", "$.reports[0].material_boundary.provided_material_only")
    if not allow_external_information and boundary.get("external_information_used") is not False:
        add("external_information_used", "External information is not allowed for this run", "$.reports[0].material_boundary.external_information_used")
    if not allow_external_information and _nonempty_list(boundary.get("external_sources")):
        add("external_sources", "external_sources must be empty when external information is disabled", "$.reports[0].material_boundary.external_sources")

    script = _clean_text(report.get("final_script_for_tts"))
    if not script:
        add("script_missing", "final_script_for_tts must be non-empty", "$.reports[0].final_script_for_tts")
    script_chars = _count_without_whitespace(script)
    minimum = max(1, int(minimum_script_chars or 1))
    if script_chars < minimum:
        add("script_too_short", f"Script has {script_chars} non-whitespace characters; minimum is {minimum}", "$.reports[0].final_script_for_tts")
    if re.search(r"https?://|www\.", script, flags=re.IGNORECASE):
        add("script_contains_url", "TTS script must not contain URLs", "$.reports[0].final_script_for_tts")
    if re.search(r"(^|\n)\s{0,3}#{1,6}\s", script):
        add("script_contains_markdown", "TTS script must not contain Markdown headings", "$.reports[0].final_script_for_tts")

    paragraphs = _script_paragraphs(script)
    paragraph_map = _require_list(report.get("paragraph_map"))
    evidence_map = _require_list(report.get("evidence_map"))
    if len(paragraph_map) != len(paragraphs):
        add("paragraph_map_count", "paragraph_map count must match script paragraph count", "$.reports[0].paragraph_map")
    if len(evidence_map) != len(paragraphs):
        add("evidence_map_count", "evidence_map count must match script paragraph count", "$.reports[0].evidence_map")
    expected_indexes = list(range(1, len(paragraphs) + 1))
    if [_integer_or_none(item.get("script_paragraph_index")) for item in paragraph_map if isinstance(item, dict)] != expected_indexes:
        add("paragraph_map_indexes", "paragraph_map indexes must be continuous from 1", "$.reports[0].paragraph_map")
    if [_integer_or_none(item.get("script_paragraph_index")) for item in evidence_map if isinstance(item, dict)] != expected_indexes:
        add("evidence_map_indexes", "evidence_map indexes must be continuous from 1", "$.reports[0].evidence_map")

    ledger = _require_list(report.get("fact_ledger"))
    claim_ids = [_clean_text(item.get("claim_id")) for item in ledger if isinstance(item, dict)]
    claim_id_set = {claim_id for claim_id in claim_ids if claim_id}
    if not claim_ids or len(claim_id_set) != len(claim_ids):
        add("fact_ledger_claim_ids", "fact_ledger claim_id values must be non-empty and unique", "$.reports[0].fact_ledger")

    paragraph_claim_ids = _collect_claim_ids(paragraph_map)
    unknown = sorted(paragraph_claim_ids - claim_id_set)
    if unknown:
        add("unknown_paragraph_claim_ids", f"paragraph_map references unknown claim IDs: {', '.join(unknown)}", "$.reports[0].paragraph_map")

    candidates = report.get("title_candidates")
    candidates = candidates if isinstance(candidates, dict) else {}
    candidate_maps = {}
    title_claim_ids = set()
    for bucket in ("content", "landscape", "vertical"):
        entries = _require_list(candidates.get(bucket))
        if len(entries) != 3:
            add(f"{bucket}_candidate_count", f"title_candidates.{bucket} must contain exactly three candidates", f"$.reports[0].title_candidates.{bucket}")
        mapping = {}
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                add(f"{bucket}_candidate_shape", "Title candidates must be objects", f"$.reports[0].title_candidates.{bucket}[{index}]")
                continue
            candidate_id = _clean_text(entry.get("candidate_id"))
            if not candidate_id or candidate_id in mapping:
                add(f"{bucket}_candidate_ids", "Candidate IDs must be non-empty and unique in each title bucket", f"$.reports[0].title_candidates.{bucket}[{index}].candidate_id")
            else:
                mapping[candidate_id] = entry
            if entry.get("format") != bucket:
                add(f"{bucket}_candidate_format", f"Candidate format must equal {bucket}", f"$.reports[0].title_candidates.{bucket}[{index}].format")
            if not _clean_text(entry.get("title")):
                add(f"{bucket}_candidate_title", "Candidate title must be non-empty", f"$.reports[0].title_candidates.{bucket}[{index}].title")
            title_claim_ids.update(_strings(entry.get("claim_ids")))
        candidate_maps[bucket] = mapping
    unknown = sorted(title_claim_ids - claim_id_set)
    if unknown:
        add("unknown_title_claim_ids", f"Title candidates reference unknown claim IDs: {', '.join(unknown)}", "$.reports[0].title_candidates")

    selected = report.get("selected_titles")
    selected = selected if isinstance(selected, dict) else {}
    selected_entries = {}
    for bucket in ("content", "landscape", "vertical"):
        selected_id = _clean_text(selected.get(f"{bucket}_candidate_id"))
        entry = candidate_maps.get(bucket, {}).get(selected_id)
        if entry is None:
            add(f"selected_{bucket}_candidate", f"Selected {bucket} candidate ID does not exist", f"$.reports[0].selected_titles.{bucket}_candidate_id")
        selected_entries[bucket] = entry

    content_entry = selected_entries.get("content")
    if content_entry is not None and _clean_text(report.get("title")) != _clean_text(content_entry.get("title")):
        add("content_title_mismatch", "title must equal the selected content candidate title", "$.reports[0].title")
    landscape_entry = selected_entries.get("landscape")
    if landscape_entry is not None:
        if _clean_text(report.get("landscape_title")) != _clean_text(landscape_entry.get("title")):
            add("landscape_title_mismatch", "landscape_title must equal the selected landscape title", "$.reports[0].landscape_title")
        if _clean_text(report.get("landscape_subtitle")) != _clean_text(landscape_entry.get("subtitle")):
            add("landscape_subtitle_mismatch", "landscape_subtitle must equal the selected landscape subtitle", "$.reports[0].landscape_subtitle")
    vertical_entry = selected_entries.get("vertical")
    if vertical_entry is not None:
        if _clean_text(report.get("vertical_title")) != _clean_text(vertical_entry.get("title")):
            add("vertical_title_mismatch", "vertical_title must equal the selected vertical title", "$.reports[0].vertical_title")
        if _clean_text(report.get("vertical_subtitle")) != _clean_text(vertical_entry.get("subtitle")):
            add("vertical_subtitle_mismatch", "vertical_subtitle must equal the selected vertical subtitle", "$.reports[0].vertical_subtitle")
    if _clean_text(report.get("cover_title")) != _clean_text(report.get("landscape_title")):
        add("cover_title_mismatch", "cover_title must equal landscape_title", "$.reports[0].cover_title")
    if _clean_text(report.get("cover_subtitle")) != _clean_text(report.get("landscape_subtitle")):
        add("cover_subtitle_mismatch", "cover_subtitle must equal landscape_subtitle", "$.reports[0].cover_subtitle")

    evidence_claim_ids = _collect_claim_ids(evidence_map)
    for item in evidence_map:
        if not isinstance(item, dict):
            continue
        for evidence_item in _require_list(item.get("evidence_items")):
            if isinstance(evidence_item, dict):
                evidence_claim_ids.update(_strings(evidence_item.get("claim_ids")))
    unknown = sorted(evidence_claim_ids - claim_id_set)
    if unknown:
        add("unknown_evidence_claim_ids", f"evidence_map references unknown claim IDs: {', '.join(unknown)}", "$.reports[0].evidence_map")

    metadata = report.get("metadata") if isinstance(report.get("metadata"), dict) else {}
    page_limit = _positive_integer(actual_pdf_page_count) or _positive_integer(metadata.get("actual_pdf_page_count"))
    allowed_files = {str(value) for value in source_files or [] if isinstance(value, str)}
    evidence_records = []
    for ledger_index, claim in enumerate(ledger):
        if not isinstance(claim, dict):
            continue
        for evidence_index, evidence in enumerate(_require_list(claim.get("evidence"))):
            evidence_records.append((evidence, f"$.reports[0].fact_ledger[{ledger_index}].evidence[{evidence_index}]"))
    for map_index, map_item in enumerate(evidence_map):
        if not isinstance(map_item, dict):
            continue
        for evidence_index, evidence in enumerate(_require_list(map_item.get("evidence_items"))):
            evidence_records.append((evidence, f"$.reports[0].evidence_map[{map_index}].evidence_items[{evidence_index}]"))
    out_of_range_found = False
    wrong_file_found = False
    for evidence, path in evidence_records:
        if not isinstance(evidence, dict):
            add("evidence_shape", "Evidence items must be objects", path)
            continue
        page = _positive_integer(evidence.get("pdf_page"))
        if page is None or (page_limit is not None and page > page_limit):
            if not out_of_range_found:
                add("evidence_page_out_of_range", "Every evidence pdf_page must be positive and inside the actual PDF", path + ".pdf_page")
                out_of_range_found = True
        if allowed_files and _clean_text(evidence.get("pdf_file")) not in allowed_files and not wrong_file_found:
            add("evidence_file_mismatch", "Evidence pdf_file must match source_files", path + ".pdf_file")
            wrong_file_found = True

    qc = report.get("quality_control")
    qc = qc if isinstance(qc, dict) else {}
    if qc.get("output_ready") is not True:
        add("output_not_ready", "quality_control.output_ready must be true for direct use", "$.reports[0].quality_control.output_ready")
    if _nonempty_list(qc.get("blocking_reasons")):
        add("blocking_reasons", "Report blocking_reasons must be empty for direct use", "$.reports[0].quality_control.blocking_reasons")
    if qc.get("title_consistency_passed") is not True:
        add("qc_title_consistency", "quality_control.title_consistency_passed must be true", "$.reports[0].quality_control.title_consistency_passed")
    if qc.get("evidence_page_numbers_verified") is not True:
        add("qc_evidence_pages", "quality_control.evidence_page_numbers_verified must be true", "$.reports[0].quality_control.evidence_page_numbers_verified")
    for key in (
        "unsupported_claim_ids",
        "unknown_claim_ids_in_paragraph_map",
        "unknown_claim_ids_in_titles",
        "unmapped_script_paragraphs",
        "prohibited_terms_found",
    ):
        if _nonempty_list(qc.get(key)):
            add(f"qc_{key}", f"quality_control.{key} must be empty", f"$.reports[0].quality_control.{key}")
    if _integer_or_none(qc.get("script_char_count_without_whitespace")) != script_chars:
        add("qc_script_char_count", "Reported script character count must match the actual script", "$.reports[0].quality_control.script_char_count_without_whitespace")
    if _integer_or_none(qc.get("script_paragraph_count")) != len(paragraphs):
        add("qc_script_paragraph_count", "Reported paragraph count must match the actual script", "$.reports[0].quality_control.script_paragraph_count")

    batch_qc = payload.get("batch_quality_control")
    batch_qc = batch_qc if isinstance(batch_qc, dict) else {}
    if _integer_or_none(batch_qc.get("report_count")) != 1:
        add("batch_report_count", "batch_quality_control.report_count must equal 1", "$.batch_quality_control.report_count")
    if batch_qc.get("all_reports_output_ready") is not True:
        add("batch_output_not_ready", "batch_quality_control.all_reports_output_ready must be true", "$.batch_quality_control.all_reports_output_ready")
    if _nonempty_list(batch_qc.get("blocking_reasons")):
        add("batch_blocking_reasons", "Batch blocking_reasons must be empty for direct use", "$.batch_quality_control.blocking_reasons")

    return GemV2Validation(original_text, payload, report, tuple(issues))


def to_report_audio_structured_result(validation: GemV2Validation) -> dict:
    """Map a valid V2 report into the compatibility shape used downstream."""

    if not validation.valid or validation.report is None or validation.payload is None:
        codes = ", ".join(validation.error_codes) or "unknown"
        raise ValueError(f"Cannot map invalid {SCHEMA_VERSION} response: {codes}")
    result = copy.deepcopy(validation.report)
    result["schema_version"] = SCHEMA_VERSION
    result["language"] = validation.payload.get("language")
    result["output_mode"] = validation.payload.get("output_mode")
    result["batch_quality_control"] = copy.deepcopy(validation.payload.get("batch_quality_control"))
    result["provider"] = "gemini_gem"
    result["cover_title"] = result.get("landscape_title", "")
    result["cover_subtitle"] = result.get("landscape_subtitle", "")
    return result


def build_gem_v2_repair_prompt(errors: Iterable[Any]) -> str:
    """Request one bounded, complete replacement after contract validation fails."""

    rendered = []
    for item in errors:
        if isinstance(item, GemV2Issue):
            rendered.append(f"- {item.code}: {item.message}" + (f" ({item.path})" if item.path else ""))
        elif isinstance(item, Mapping):
            rendered.append(f"- {item.get('code', 'validation_error')}: {item.get('message', '')}")
        else:
            rendered.append(f"- validation_error: {item}")
    if not rendered:
        rendered.append("- validation_error: 返回值未通过结构契约")
    return "\n".join([
        f"你刚才的返回值未通过 {SCHEMA_VERSION} 机器校验。请依据下列问题完成一次定向修复：",
        *rendered,
        "请重新输出完整替换版本，而不是局部补丁或只补充缺失字段。",
        "最终只能包含一个顶层 JSON 对象，不要输出 Markdown 代码围栏、解释或致歉。",
        "所有事实、页码和标题仍必须只来自已上传 PDF；无法可靠修复时保持完整结构并将 output_ready 设为 false。",
    ])


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _count_without_whitespace(value: str) -> int:
    return len(re.sub(r"\s+", "", value or ""))


def _script_paragraphs(value: str) -> list[str]:
    if not _clean_text(value):
        return []
    return [part.strip() for part in re.split(r"\n\s*\n", value.strip()) if part.strip()]


def _require_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _nonempty_list(value: Any) -> bool:
    return isinstance(value, list) and bool(value)


def _strings(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {_clean_text(item) for item in value if _clean_text(item)}


def _collect_claim_ids(items: Sequence[Any]) -> set[str]:
    collected = set()
    for item in items:
        if isinstance(item, dict):
            collected.update(_strings(item.get("claim_ids")))
    return collected


def _integer_or_none(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _positive_integer(value: Any) -> Optional[int]:
    parsed = _integer_or_none(value)
    return parsed if parsed is not None and parsed > 0 else None
