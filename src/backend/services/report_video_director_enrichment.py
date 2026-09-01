"""Evidence, timeline, and mixed-media enrichment for report director packages.

The functions in this module are deterministic adapters around existing
capabilities. They do not call models, create tasks, generate images, render a
full video, or publish anything.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence

import fitz  # type: ignore

from services.report_video_director import (
    DIRECTOR_PACKAGE_SCHEMA_VERSION,
    director_package_hash,
    split_director_narration,
    validate_director_package,
)


ENRICHMENT_SCHEMA_VERSION = "report_video_director_enrichment_v1"
TIMELINE_SCHEMA_VERSION = "report_video_director_timeline_v1"
ASSET_PLAN_SCHEMA_VERSION = "report_video_director_asset_plan_v1"
_SOURCE_MEDIA_TYPES = {"original_pdf", "pdf_highlight", "pdf_scroll", "chart"}
_CHART_TOKENS = re.compile(
    r"(?:exhibit|figure|table|chart|图表|表格|价格|库存|产量|需求|同比|环比|%|％)",
    re.IGNORECASE,
)


class DirectorEnrichmentError(ValueError):
    """Raised when enrichment would weaken source traceability."""


def derive_verified_pdf_evidence_map(
    material: Mapping[str, Any],
    *,
    pdf_path: str,
    target_segment_chars: int = 130,
) -> list[Dict[str, Any]]:
    """Build a fail-closed real-PDF evidence fallback for legacy materials.

    Newer upstream tasks should supply their Gemini page/quote mapping directly.
    Some existing selection-pool tasks predate that field. For those tasks this
    adapter aligns the unchanged narration chunks to extractable PDF pages using
    numeric claims and document order, and returns only verbatim PDF text. It
    refuses image-only or garbled PDFs rather than fabricating evidence.
    """

    if not isinstance(material, Mapping):
        raise DirectorEnrichmentError("真实文案物料必须是对象")
    structured = (
        material.get("structured_result")
        if isinstance(material.get("structured_result"), Mapping)
        else {}
    )
    script = str(
        material.get("final_script_for_tts")
        or structured.get("final_script_for_tts")
        or ""
    ).strip()
    if not script:
        raise DirectorEnrichmentError("缺少已有真实口播文案")
    chunks = split_director_narration(
        script,
        target_segment_chars=target_segment_chars,
    )
    pdf = Path(pdf_path).resolve()
    if not pdf.is_file() or pdf.suffix.lower() != ".pdf":
        raise DirectorEnrichmentError("真实 PDF 不存在或格式不正确")

    pages = []
    disclosure_cutoff = None
    with fitz.open(pdf) as document:
        for index, page in enumerate(document, start=1):
            text = page.get_text("text") or ""
            if index >= 5 and re.search(
                r"(?:^|\n)\s*(?:DISCLOSURE APPENDIX|Disclosure Section)\b",
                text[:900],
                re.IGNORECASE,
            ):
                disclosure_cutoff = index
                break
            lines = _meaningful_pdf_lines(text)
            compact = _compact(text)
            if len(compact) < 120 or not lines:
                continue
            pages.append(
                {
                    "page": index,
                    "text": text,
                    "compact": compact,
                    "lines": lines,
                    "numbers": _numeric_tokens(text),
                    "chart": bool(_CHART_TOKENS.search(text)),
                }
            )
    if not pages:
        raise DirectorEnrichmentError(
            "PDF 没有可核验文本层，必须使用已有 OCR/Gemini 页级证据映射"
        )

    rows = []
    total_chunks = max(1, len(chunks))
    previous_page = pages[0]["page"]
    for index, chunk in enumerate(chunks, start=1):
        claim_numbers = _numeric_tokens(chunk)
        ranked = []
        expected_position = (index - 1) / max(1, total_chunks - 1)
        for page_index, page in enumerate(pages):
            page_position = page_index / max(1, len(pages) - 1)
            shared = claim_numbers.intersection(page["numbers"])
            numeric_score = sum(
                1.0 if token in {"2026", "2027", "2030"} else 4.0
                for token in shared
            )
            order_score = max(0.0, 1.2 - abs(page_position - expected_position))
            continuity_score = 0.6 if page["page"] >= previous_page else 0.0
            ranked.append(
                (
                    numeric_score + order_score + continuity_score,
                    len(shared),
                    -abs(page_position - expected_position),
                    page,
                    shared,
                )
            )
        ranked.sort(key=lambda item: item[:3], reverse=True)
        _score, _shared_count, _position, selected, shared = ranked[0]
        quote = _select_verbatim_quote(selected, shared)
        if not quote:
            raise DirectorEnrichmentError(
                f"PDF 第 {selected['page']} 页没有可核验引用文本"
            )
        previous_page = selected["page"]
        rows.append(
            {
                "evidence_id": f"pdf-derived-{index:03d}",
                "segment_id": f"seg-{index:03d}",
                "page": selected["page"],
                "quote": quote,
                "kind": (
                    "chart"
                    if re.search(
                        r"(?:exhibit|figure|table|chart|图表|表格)",
                        quote,
                        re.IGNORECASE,
                    )
                    else "quote"
                ),
                "claim_tags": sorted(shared),
                "mapping": {
                    "source": "deterministic_real_pdf_text_fallback",
                    "verbatim_pdf_text": True,
                    "numeric_claim_matches": len(shared),
                    "legacy_material_without_page_map": True,
                    "disclosure_cutoff_page": disclosure_cutoff,
                },
            }
        )
    return rows


def enrich_package_evidence(
    package: Mapping[str, Any],
    *,
    pdf_path: str,
) -> Dict[str, Any]:
    """Verify evidence against the real PDF and add page/bbox fingerprints.

    Existing page and quote mappings remain authoritative. This adapter checks
    that each referenced page exists and that the quote is recoverable from the
    page text; it never invents a replacement quote.
    """

    payload = _copy_package(package)
    pdf = Path(pdf_path).resolve()
    if not pdf.is_file() or pdf.suffix.lower() != ".pdf":
        raise DirectorEnrichmentError("真实 PDF 不存在或格式不正确")
    source = payload.get("source")
    source = source if isinstance(source, dict) else {}
    configured_path = str(source.get("pdf_path") or "").strip()
    if configured_path and Path(configured_path).resolve() != pdf:
        raise DirectorEnrichmentError("导演包 PDF 与证据核验 PDF 不一致")

    verified = 0
    unverified = 0
    page_cache: Dict[int, Dict[str, Any]] = {}
    with fitz.open(pdf) as document:
        for segment in payload.get("segments") or []:
            for evidence in segment.get("evidence_refs") or []:
                page_number = _positive_int(evidence.get("page"))
                quote = str(evidence.get("quote") or "").strip()
                if not page_number or page_number > len(document):
                    evidence["verification"] = {
                        "status": "invalid_page",
                        "pdf_sha256": _file_sha256(pdf),
                    }
                    unverified += 1
                    continue
                page_info = page_cache.get(page_number)
                if page_info is None:
                    page = document[page_number - 1]
                    text = page.get_text("text") or ""
                    page_info = {
                        "page": page,
                        "text": text,
                        "compact": _compact(text),
                        "text_sha256": hashlib.sha256(
                            text.encode("utf-8")
                        ).hexdigest(),
                    }
                    page_cache[page_number] = page_info
                match = _locate_quote(
                    page_info["page"],
                    page_info["text"],
                    page_info["compact"],
                    quote,
                )
                evidence["bbox"] = match.get("bbox") or evidence.get("bbox")
                evidence["verification"] = {
                    "status": match["status"],
                    "method": match["method"],
                    "page_text_sha256": page_info["text_sha256"],
                    "pdf_sha256": _file_sha256(pdf),
                    "quote_sha256": (
                        hashlib.sha256(quote.encode("utf-8")).hexdigest()
                        if quote
                        else None
                    ),
                    "visual_hint": _visual_hint(
                        str(evidence.get("kind") or ""),
                        quote,
                        page_info["text"],
                    ),
                }
                if match["status"] == "verified":
                    verified += 1
                else:
                    unverified += 1

    payload["evidence_enrichment"] = {
        "schema_version": ENRICHMENT_SCHEMA_VERSION,
        "pdf_path": str(pdf),
        "pdf_sha256": _file_sha256(pdf),
        "verified_refs": verified,
        "unverified_refs": unverified,
        "fail_closed": unverified > 0,
    }
    payload["package_hash"] = director_package_hash(payload)
    payload["validation"] = validate_director_package(payload)
    return payload


def attach_audio_timeline(
    package: Mapping[str, Any],
    *,
    audio_path: str,
    audio_seconds: float,
    alignment: Mapping[str, Any] | None = None,
    allow_test_estimate: bool = False,
) -> Dict[str, Any]:
    """Attach an ASR-backed timeline or an explicit low-confidence test estimate."""

    payload = _copy_package(package)
    audio = Path(audio_path).resolve()
    if not audio.is_file():
        raise DirectorEnrichmentError("真实音频不存在")
    total = float(audio_seconds or 0)
    if total <= 0.1:
        raise DirectorEnrichmentError("真实音频时长无效")
    segments = payload.get("segments") or []
    if not segments:
        raise DirectorEnrichmentError("导演包没有段落")

    timeline_rows = []
    source = ""
    if isinstance(alignment, Mapping) and alignment.get("timeline"):
        alignment_rows = alignment.get("timeline")
        if len(alignment_rows) != len(segments):
            raise DirectorEnrichmentError("ASR 时间线段落数与导演包不一致")
        for segment, row in zip(segments, alignment_rows):
            timeline_rows.append(
                {
                    "segment_id": segment["segment_id"],
                    "start_seconds": round(float(row["start_seconds"]), 6),
                    "end_seconds": round(float(row["end_seconds"]), 6),
                    "duration_seconds": round(float(row["duration_seconds"]), 6),
                    "confidence": round(float(row.get("confidence") or 0), 6),
                    "source": "existing_whole_audio_asr_alignment",
                }
            )
        source = "existing_whole_audio_asr_alignment"
    elif allow_test_estimate:
        weights = [
            max(1, len(str(item.get("narration", {}).get("text") or "")))
            for item in segments
        ]
        weight_total = max(1, sum(weights))
        cursor = 0.0
        for index, (segment, weight) in enumerate(zip(segments, weights)):
            end = (
                total
                if index == len(segments) - 1
                else cursor + total * weight / weight_total
            )
            timeline_rows.append(
                {
                    "segment_id": segment["segment_id"],
                    "start_seconds": round(cursor, 6),
                    "end_seconds": round(end, 6),
                    "duration_seconds": round(end - cursor, 6),
                    "confidence": 0.0,
                    "source": "test_character_ratio_estimate",
                }
            )
            cursor = end
        source = "test_character_ratio_estimate"
    else:
        raise DirectorEnrichmentError("生产时间线必须来自已有整篇音频 ASR 对齐")

    _validate_continuous_timeline(timeline_rows, total)
    by_id = {row["segment_id"]: row for row in timeline_rows}
    for segment in segments:
        timing = by_id[segment["segment_id"]]
        segment["timeline"] = timing
        segment["narration"]["actual_seconds"] = timing["duration_seconds"]
        segment["narration"]["timing_policy"] = source
    payload["audio_timeline"] = {
        "schema_version": TIMELINE_SCHEMA_VERSION,
        "audio_path": str(audio),
        "audio_sha256": _file_sha256(audio),
        "audio_seconds": round(total, 6),
        "source": source,
        "production_ready": source == "existing_whole_audio_asr_alignment",
        "timeline": timeline_rows,
    }
    payload["package_hash"] = director_package_hash(payload)
    payload["validation"] = validate_director_package(payload)
    return payload


def build_mixed_media_asset_plan(package: Mapping[str, Any]) -> Dict[str, Any]:
    """Compile director decisions into bounded submodule dispatch instructions."""

    payload = _copy_package(package)
    validation = validate_director_package(payload)
    if not validation["valid"]:
        codes = ",".join(
            str(item.get("code") or "")
            for item in validation.get("errors") or []
        )
        raise DirectorEnrichmentError(
            f"导演包校验未通过，禁止下发素材计划：{codes}"
        )
    timeline = payload.get("audio_timeline")
    timeline = timeline if isinstance(timeline, dict) else {}
    items = []
    for segment in payload.get("segments") or []:
        media = segment.get("media") or {}
        media_type = str(media.get("type") or "")
        evidence = segment.get("evidence_refs") or []
        pages = sorted(
            {
                int(item["page"])
                for item in evidence
                if _positive_int(item.get("page"))
            }
        )
        instruction = {
            "segment_id": segment["segment_id"],
            "sequence": segment["sequence"],
            "media_type": media_type,
            "module": segment["dispatch"]["module"],
            "timeline": segment.get("timeline"),
            "source_pages": pages,
            "evidence_ids": [
                item.get("evidence_id") for item in evidence if item.get("evidence_id")
            ],
            "ratios": segment.get("variants"),
            "transition": segment.get("direction", {}).get("transition_in"),
            "full_asset_generation_allowed": False,
        }
        if media_type in _SOURCE_MEDIA_TYPES:
            instruction["source_render"] = {
                "mode": (
                    "chart_focus"
                    if media_type == "chart"
                    else (
                        "scroll"
                        if media_type == "pdf_scroll"
                        else (
                            "highlight"
                            if media_type == "pdf_highlight"
                            else "page"
                        )
                    )
                ),
                "preserve_pdf_page_number": True,
                "preserve_source_footer": True,
            }
        if media_type == "chart":
            instruction["motion_spec"] = {
                "adapter": "hyperframes_or_existing_ffmpeg",
                "entry": "page_context",
                "beats": [
                    "crop_to_chart",
                    "highlight_primary_series_or_cells",
                    "annotate_claim_and_unit",
                    "return_to_source_context",
                ],
                "camera": "bounded_scale_and_pan",
                "fabrication_allowed": False,
            }
        elif media_type in {"logic_card", "title_card"}:
            instruction["motion_spec"] = {
                "adapter": "hyperframes_or_existing_ffmpeg",
                "entry": "editorial_card",
                "beats": ["headline", "supporting_fact", "source_anchor"],
                "camera": "layered_reveal",
            }
        elif media_type == "character_explainer":
            instruction["style_dispatch"] = {
                "allowed_modules": [
                    "report_stick_figure_scene_images",
                    "report_doraemon_scene_images",
                ],
                "director_remains_authority": True,
            }
        items.append(instruction)
    return {
        "schema_version": ASSET_PLAN_SCHEMA_VERSION,
        "package_id": payload.get("package_id"),
        "package_hash": director_package_hash(payload),
        "timeline_source": timeline.get("source"),
        "production_ready": bool(
            timeline.get("production_ready")
            and payload.get("evidence_enrichment", {}).get("fail_closed") is False
        ),
        "items": items,
        "dispatch_default": "blocked_until_mode_gate",
        "publish_allowed": False,
    }


def _copy_package(package: Mapping[str, Any]) -> Dict[str, Any]:
    import copy

    if not isinstance(package, Mapping):
        raise DirectorEnrichmentError("导演包必须是对象")
    payload = copy.deepcopy(dict(package))
    if payload.get("schema_version") != DIRECTOR_PACKAGE_SCHEMA_VERSION:
        raise DirectorEnrichmentError("导演包 schema 不受支持")
    payload.pop("validation", None)
    return payload


def _locate_quote(page, page_text: str, compact_page: str, quote: str) -> Dict[str, Any]:
    if not quote:
        return {"status": "unverified", "method": "missing_quote", "bbox": None}
    candidates = [quote]
    words = quote.split()
    if len(words) > 10:
        candidates.extend(
            [
                " ".join(words[:10]),
                " ".join(words[-10:]),
            ]
        )
    for candidate in candidates:
        rects = page.search_for(candidate)
        if rects:
            rect = rects[0]
            return {
                "status": "verified",
                "method": "fitz_exact_search",
                "bbox": [
                    round(rect.x0, 3),
                    round(rect.y0, 3),
                    round(rect.x1, 3),
                    round(rect.y1, 3),
                ],
            }
    compact_quote = _compact(quote)
    if compact_quote and compact_quote in compact_page:
        return {
            "status": "verified",
            "method": "normalized_page_text",
            "bbox": None,
        }
    if len(compact_quote) >= 80:
        head = compact_quote[:80]
        if head in compact_page:
            return {
                "status": "verified",
                "method": "normalized_quote_prefix",
                "bbox": None,
            }
    return {"status": "unverified", "method": "quote_not_found", "bbox": None}


def _visual_hint(kind: str, quote: str, page_text: str) -> str:
    value = f"{kind} {quote} {page_text[:800]}"
    if _CHART_TOKENS.search(value):
        return "chart_or_table_focus"
    return "quote_highlight"


def _meaningful_pdf_lines(text: str) -> list[str]:
    lines = []
    for raw in str(text or "").splitlines():
        value = " ".join(raw.split()).strip()
        if len(value) < 12:
            continue
        lowered = value.lower()
        if (
            "knowledge planet" in lowered
            or "知识星球" in value
            or re.fullmatch(r"[\W\d_]+", value)
        ):
            continue
        lines.append(value)
    return lines


def _select_verbatim_quote(
    page: Mapping[str, Any],
    shared_numbers: set[str],
) -> str:
    lines = list(page.get("lines") or [])
    if not lines:
        return ""
    ranked = []
    for line in lines:
        line_numbers = _numeric_tokens(line)
        shared = shared_numbers.intersection(line_numbers)
        chart_bonus = 2 if re.search(
            r"(?:exhibit|figure|table|chart|图表|表格)",
            line,
            re.IGNORECASE,
        ) else 0
        length_score = min(len(line), 180) / 180
        ranked.append((len(shared) * 5 + chart_bonus + length_score, line))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return ranked[0][1][:360]


_ARABIC_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
_CHINESE_NUMBER_RE = re.compile(r"[零〇一二两三四五六七八九十百千万亿点]+")
_CHINESE_DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_SMALL_UNITS = {"十": 10, "百": 100, "千": 1000}
_LARGE_UNITS = {"万": 10_000, "亿": 100_000_000}


def _numeric_tokens(text: str) -> set[str]:
    value = str(text or "")
    tokens = {
        _normalize_number_token(match.group(0))
        for match in _ARABIC_NUMBER_RE.finditer(value.replace(",", ""))
    }
    for match in _CHINESE_NUMBER_RE.finditer(value):
        number = _chinese_number(match.group(0))
        if number is not None:
            tokens.add(_normalize_number_token(str(number)))
    return {token for token in tokens if token}


def _normalize_number_token(value: str) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    if number.is_integer():
        return str(int(number))
    return (f"{number:.6f}").rstrip("0").rstrip(".")


def _chinese_number(value: str) -> float | int | None:
    text = str(value or "")
    if not text:
        return None
    if "点" in text:
        integer, decimal = text.split("点", 1)
        whole = _chinese_integer(integer)
        if whole is None or any(char not in _CHINESE_DIGITS for char in decimal):
            return None
        fraction = "".join(str(_CHINESE_DIGITS[char]) for char in decimal)
        return float(f"{whole}.{fraction}")
    return _chinese_integer(text)


def _chinese_integer(text: str) -> int | None:
    if not text:
        return 0
    if all(char in _CHINESE_DIGITS for char in text):
        return int("".join(str(_CHINESE_DIGITS[char]) for char in text))
    total = 0
    section = 0
    number = 0
    for char in text:
        if char in _CHINESE_DIGITS:
            number = _CHINESE_DIGITS[char]
        elif char in _SMALL_UNITS:
            unit = _SMALL_UNITS[char]
            section += (number or 1) * unit
            number = 0
        elif char in _LARGE_UNITS:
            section += number
            total += (section or 1) * _LARGE_UNITS[char]
            section = 0
            number = 0
        else:
            return None
    return total + section + number


def _validate_continuous_timeline(rows: Sequence[Mapping[str, Any]], total: float) -> None:
    if not rows:
        raise DirectorEnrichmentError("时间线为空")
    cursor = 0.0
    for row in rows:
        start = float(row.get("start_seconds") or 0)
        end = float(row.get("end_seconds") or 0)
        if abs(start - cursor) > 0.02 or end <= start:
            raise DirectorEnrichmentError("时间线不连续或包含无效区间")
        cursor = end
    if abs(cursor - total) > 0.05:
        raise DirectorEnrichmentError("时间线末尾与音频时长不一致")


def _positive_int(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "")).lower()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
