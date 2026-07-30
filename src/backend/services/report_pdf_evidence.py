"""Deterministic PDF evidence mapping shared by every report visual scheme."""

from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable

from PIL import Image, ImageFilter
from sqlalchemy import cast, func
from sqlalchemy.dialects.postgresql import JSONPATH

from models import (
    ReportPdfEvidenceClaim,
    ReportPdfEvidenceOutput,
    ReportVisualPlanOutput,
    ReportVisualPlanSegment,
    ReportVisualPlanSegmentEvidence,
    db,
)


SCHEMA_VERSION = "report_pdf_evidence_v1"
PARSER_VERSION = "local_pdf_verify_v2"
MAX_CLAIMS = 160
MAX_CLAIM_CHARS = 1200
MAX_QUOTE_CHARS = 1600
MIN_SEGMENT_LINK_SCORE = 0.58
MIN_LEGACY_SCRIPT_MAPPING_SCORE = 0.22
VISUAL_ELIGIBLE_STATUSES = {"verified", "manual_verified"}


class PdfEvidenceError(RuntimeError):
    pass


@dataclass(frozen=True)
class PdfPage:
    number: int
    text: str
    normalized_text: str


@dataclass(frozen=True)
class VisualPdfBinding:
    path: str
    sha256: str
    size: int
    source: str


def build_and_store_evidence(
    *,
    source_task_id: str,
    pdf_path: str,
    final_script: str,
    raw_gemini_text: str,
    pdf_attachment_id: str | None = None,
    pdf_filename: str | None = None,
    gemini_task_id: str | None = None,
) -> ReportPdfEvidenceOutput:
    """Build locally verified evidence without using the TTS normalizer channel."""

    source_task_id = str(source_task_id or "").strip()
    source = Path(str(pdf_path or "")).expanduser()
    script = str(final_script or "").strip()
    if not source_task_id or not source.is_file() or not script:
        raise PdfEvidenceError("证据映射缺少任务、PDF 或最终文案")

    source = source.resolve()
    pdf_hash = _sha256_file(source)
    script_hash = _sha256_text(script)
    raw_response_hash = _sha256_text(raw_gemini_text or "")
    existing = ReportPdfEvidenceOutput.query.filter_by(source_task_id=source_task_id).one_or_none()
    if (
        existing
        and existing.schema_version == SCHEMA_VERSION
        and existing.parser_version == PARSER_VERSION
        and existing.source_pdf_hash == pdf_hash
        and existing.final_script_hash == script_hash
        and existing.raw_response_hash == raw_response_hash
        and existing.normalized_artifact_path
        and Path(existing.normalized_artifact_path).is_file()
    ):
        _refresh_visual_pdf_binding(existing)
        return existing

    page_rows = extract_pdf_pages(source)
    candidates = extract_evidence_candidates(raw_gemini_text)
    normalized = [
        verified
        for verified in (
            verify_candidate(candidate, script, page_rows, sequence=index)
            for index, candidate in enumerate(candidates[:MAX_CLAIMS], start=1)
        )
        if verified is not None
    ]
    output_root = _output_root(source_task_id)
    raw_path = output_root / "candidate_evidence.json"
    normalized_path = output_root / "verified_evidence.json"
    raw_path.write_text(
        json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "raw_text_sha256": raw_response_hash,
                "candidate_count": len(candidates),
                "candidates": candidates[:MAX_CLAIMS],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    normalized_path.write_text(
        json.dumps(
            {"schema_version": SCHEMA_VERSION, "claims": normalized},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    visuals_enabled = bool(existing.visuals_enabled) if existing else False
    if existing:
        ReportPdfEvidenceClaim.query.filter_by(output_id=existing.id).delete(synchronize_session=False)
        output = existing
    else:
        output = ReportPdfEvidenceOutput(source_task_id=source_task_id)

    verified_count = sum(1 for item in normalized if item["verification_status"] == "verified")
    mapped_ranges = [
        (int(item["script_start"]), int(item["script_end"]))
        for item in normalized
        if item.get("script_start") is not None and item.get("script_end") is not None
    ]
    mapped_chars = _union_length(mapped_ranges)
    status = "ready" if verified_count and verified_count == len(normalized) else (
        "partial" if verified_count else "unavailable"
    )
    output.pdf_attachment_id = str(pdf_attachment_id or "").strip() or None
    output.schema_version = SCHEMA_VERSION
    output.pdf_filename = str(pdf_filename or source.name)[:500]
    output.source_pdf_path = str(source)
    output.source_pdf_hash = pdf_hash
    output.source_pdf_bytes = source.stat().st_size
    # Resolving the cleaned PDF performs database reads in production.  A new
    # evidence row must not enter the Session before all of its NOT NULL fields
    # are populated, otherwise those reads trigger an autoflush of an incomplete
    # row.  Keep the lookup explicitly no-autoflush as an additional guard for
    # any other pending objects owned by the caller.
    with db.session.no_autoflush:
        visual_binding = _resolve_visual_pdf_binding(
            pdf_attachment_id=pdf_attachment_id,
            source_pdf_path=source,
            expected_page_count=len(page_rows),
            claims=normalized,
        )
    output.visual_pdf_path = visual_binding.path
    output.visual_pdf_hash = visual_binding.sha256
    output.visual_pdf_bytes = visual_binding.size
    output.visual_pdf_source = visual_binding.source
    output.page_count = len(page_rows)
    output.final_script_hash = script_hash
    output.raw_response_hash = raw_response_hash
    output.gemini_task_id = str(gemini_task_id or "").strip() or None
    output.status = status
    output.visuals_enabled = visuals_enabled
    output.claim_count = len(normalized)
    output.verified_claim_count = verified_count
    output.mapped_script_chars = mapped_chars
    output.coverage_ratio = round(mapped_chars / max(1, len(script)), 6)
    output.raw_artifact_path = str(raw_path)
    output.normalized_artifact_path = str(normalized_path)
    output.parser_provider = "local"
    output.parser_version = PARSER_VERSION
    output.error = None if candidates else "Gemini 返回中没有可识别的证据候选"
    if not existing:
        db.session.add(output)
    db.session.flush()

    for item in normalized:
        db.session.add(ReportPdfEvidenceClaim(output_id=output.id, **_claim_model_values(item)))
    db.session.commit()
    return output


def _refresh_visual_pdf_binding(output: ReportPdfEvidenceOutput) -> bool:
    """Refresh screenshot provenance without rebuilding or losing claim edits."""

    source = Path(str(output.source_pdf_path or "")).expanduser()
    if not source.is_file():
        return False
    claims = (
        ReportPdfEvidenceClaim.query.filter_by(output_id=output.id)
        .order_by(ReportPdfEvidenceClaim.sequence.asc())
        .all()
    )
    binding = _resolve_visual_pdf_binding(
        pdf_attachment_id=output.pdf_attachment_id,
        source_pdf_path=source,
        expected_page_count=int(output.page_count or 0),
        claims=claims,
    )
    previous = (
        str(output.visual_pdf_path or ""),
        str(output.visual_pdf_hash or ""),
        str(output.visual_pdf_source or ""),
    )
    current = (binding.path, binding.sha256, binding.source)
    if previous == current and int(output.visual_pdf_bytes or 0) == binding.size:
        return False
    output.visual_pdf_path = binding.path
    output.visual_pdf_hash = binding.sha256
    output.visual_pdf_bytes = binding.size
    output.visual_pdf_source = binding.source
    for claim in claims:
        claim.screenshot_path = None
    db.session.commit()
    return True


def _resolve_visual_pdf_binding(
    *,
    pdf_attachment_id: str | None,
    source_pdf_path: str | Path,
    expected_page_count: int,
    claims: Iterable[Any],
) -> VisualPdfBinding:
    source = Path(str(source_pdf_path or "")).expanduser().resolve()
    cleaned = _cleaned_pdf_path_from_workflow(
        pdf_attachment_id=pdf_attachment_id,
        source_pdf_path=source,
    )
    if cleaned and _visual_pdf_preserves_evidence(
        cleaned,
        source_pdf_path=source,
        expected_page_count=expected_page_count,
        claims=claims,
    ):
        return VisualPdfBinding(
            path=str(cleaned),
            sha256=_sha256_file(cleaned),
            size=cleaned.stat().st_size,
            source="content_pdf_clean",
        )
    return VisualPdfBinding(
        path=str(source),
        sha256=_sha256_file(source),
        size=source.stat().st_size,
        source="source_pdf",
    )


def _cleaned_pdf_path_from_workflow(
    *,
    pdf_attachment_id: str | None,
    source_pdf_path: str | Path,
) -> Path | None:
    """Resolve the latest durable source_ref instead of scanning task JSON."""

    attachment_id = str(pdf_attachment_id or "").strip()
    source = Path(str(source_pdf_path or "")).expanduser().resolve()
    if not attachment_id:
        return None
    try:
        from models.zsxq_sync import ZsxqAttachment
        from models.zsxq_workflow import ZsxqWorkflowRun

        attachment = (
            db.session.query(ZsxqAttachment.group_id)
            .filter(ZsxqAttachment.id == attachment_id)
            .one_or_none()
        )
        if not attachment or not attachment.group_id:
            return None
        if db.session.get_bind().dialect.name != "postgresql":
            return None
        json_path = cast(
            "$.clean.tasks[*].source_refs[*] ? "
            "(@.attachment_id == $attachment_id)",
            JSONPATH,
        )
        variables = func.jsonb_build_object("attachment_id", attachment_id)
        matched_ref = func.jsonb_path_query_first(
            ZsxqWorkflowRun.stage_results,
            json_path,
            variables,
        )
        row = (
            db.session.query(matched_ref.label("source_ref"))
            .filter(ZsxqWorkflowRun.group_id == str(attachment.group_id))
            .filter(matched_ref.isnot(None))
            .order_by(ZsxqWorkflowRun.created_at.desc())
            .limit(1)
            .one_or_none()
        )
    except Exception:
        return None
    ref = row.source_ref if row else None
    if not isinstance(ref, dict):
        return None
    ref_attachment_id = str(ref.get("attachment_id") or "").strip()
    ref_source = _resolved_path(ref.get("source_path"))
    if ref_attachment_id != attachment_id and ref_source != source:
        return None
    for value in ref.get("output_paths") or []:
        candidate = _resolved_path(value)
        if (
            candidate
            and candidate.is_file()
            and candidate.suffix.lower() == ".pdf"
            and candidate.stat().st_size > 0
        ):
            return candidate
    return None


def _visual_pdf_preserves_evidence(
    path: Path,
    *,
    source_pdf_path: Path,
    expected_page_count: int,
    claims: Iterable[Any],
) -> bool:
    try:
        pages = extract_pdf_pages(path)
    except PdfEvidenceError:
        return False
    if expected_page_count <= 0 or len(pages) != expected_page_count:
        return False
    verified = [
        claim
        for claim in claims
        if _claim_value(claim, "verification_status") in VISUAL_ELIGIBLE_STATUSES
    ]
    if not verified:
        return False
    for claim in verified:
        page_number = _positive_int(_claim_value(claim, "pdf_page"))
        quote = _normalized_match_text(_claim_value(claim, "pdf_quote"))
        if not page_number or page_number > len(pages) or not quote:
            return False
        page_text = pages[page_number - 1].normalized_text
        score = 1.0 if quote in page_text else _best_window_score(quote, page_text)
        if score < 0.92:
            # A text-bearing clean PDF must still contain the verified quote.
            # Rasterized cleaners intentionally remove the text layer, so only
            # that case may use strict same-page visual alignment as fallback.
            if len(page_text) >= 20:
                return False
            if _pdf_page_visual_similarity(
                source_pdf_path,
                path,
                page_number,
            ) < 0.75:
                return False
    return True


def _pdf_page_visual_similarity(source: Path, candidate: Path, page_number: int) -> float:
    """Verify page order for rasterized clean PDFs that no longer contain text."""

    try:
        import fitz  # type: ignore

        samples: list[list[float]] = []
        for pdf_path in (source, candidate):
            with fitz.open(str(pdf_path)) as document:
                if page_number < 1 or page_number > document.page_count:
                    return 0.0
                pixmap = document.load_page(page_number - 1).get_pixmap(
                    matrix=fitz.Matrix(0.3, 0.3),
                    colorspace=fitz.csGRAY,
                    alpha=False,
                )
                image = Image.frombytes(
                    "L",
                    (pixmap.width, pixmap.height),
                    pixmap.samples,
                )
                image = image.resize((128, 166)).filter(ImageFilter.FIND_EDGES)
                samples.append([float(value) for value in image.getdata()])
    except Exception:
        return 0.0
    left, right = samples
    left_mean = math.fsum(left) / max(1, len(left))
    right_mean = math.fsum(right) / max(1, len(right))
    covariance = math.fsum(
        (a - left_mean) * (b - right_mean)
        for a, b in zip(left, right)
    )
    left_variance = math.fsum((value - left_mean) ** 2 for value in left)
    right_variance = math.fsum((value - right_mean) ** 2 for value in right)
    denominator = math.sqrt(left_variance * right_variance)
    return covariance / denominator if denominator > 0 else 0.0


def _claim_value(claim: Any, key: str) -> Any:
    return claim.get(key) if isinstance(claim, dict) else getattr(claim, key, None)


def _resolved_path(value: Any) -> Path | None:
    if not value:
        return None
    try:
        return Path(str(value)).expanduser().resolve()
    except (OSError, TypeError, ValueError):
        return None


def extract_pdf_pages(pdf_path: str | Path) -> list[PdfPage]:
    try:
        import fitz  # type: ignore
    except ImportError as exc:
        raise PdfEvidenceError("缺少 PyMuPDF，无法建立 PDF 页索引") from exc
    pages: list[PdfPage] = []
    try:
        with fitz.open(str(pdf_path)) as document:
            for index in range(document.page_count):
                text = document.load_page(index).get_text("text") or ""
                pages.append(PdfPage(index + 1, text, _normalized_match_text(text)))
    except Exception as exc:
        raise PdfEvidenceError(f"读取 PDF 页文本失败: {exc}") from exc
    return pages


def extract_evidence_candidates(raw_text: str) -> list[dict[str, Any]]:
    """Accept fenced, unfenced, multi-block and embedded JSON responses."""

    text = str(raw_text or "")
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for payload in _json_payloads(text):
        for raw in _claim_lists(payload):
            normalized = _normalize_candidate(raw)
            if not normalized:
                continue
            fingerprint = _sha256_text(json.dumps(normalized, ensure_ascii=False, sort_keys=True))
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            candidates.append(normalized)
            if len(candidates) >= MAX_CLAIMS:
                return candidates
    return candidates


def verify_candidate(
    candidate: dict[str, Any],
    final_script: str,
    pages: list[PdfPage],
    *,
    sequence: int,
) -> dict[str, Any] | None:
    claim_text = _clip(candidate.get("claim_text"), MAX_CLAIM_CHARS)
    script_quote = _clip(candidate.get("script_quote"), MAX_QUOTE_CHARS)
    paragraph_index = _positive_int(candidate.get("script_paragraph_index"))
    legacy_mapping_score = None
    if paragraph_index:
        indexed_quote, legacy_mapping_score = _best_legacy_script_unit(
            final_script,
            paragraph_index,
            candidate.get("legacy_mapping_text"),
            candidate.get("pdf_quote"),
        )
        if indexed_quote:
            script_quote = _clip(indexed_quote, MAX_QUOTE_CHARS)
            claim_text = script_quote
    script_quote = script_quote or claim_text
    pdf_quote = _clip(candidate.get("pdf_quote"), MAX_QUOTE_CHARS)
    if not claim_text or not script_quote:
        return None
    page_number = _positive_int(candidate.get("pdf_page"))
    page = pages[page_number - 1] if page_number and page_number <= len(pages) else None
    script_start, script_end, script_score = _locate_script_quote(final_script, script_quote, claim_text)
    pdf_score = 0.0
    if page and pdf_quote:
        normalized_quote = _normalized_match_text(pdf_quote)
        if normalized_quote and normalized_quote in page.normalized_text:
            pdf_score = 1.0
        elif normalized_quote:
            pdf_score = _best_window_score(normalized_quote, page.normalized_text)
    page_valid = page is not None
    quote_valid = pdf_score >= 0.92
    script_valid = (
        script_start is not None
        and script_score >= 0.92
        and (
            legacy_mapping_score is None
            or legacy_mapping_score >= MIN_LEGACY_SCRIPT_MAPPING_SCORE
        )
    )
    verification_status = "verified" if page_valid and quote_valid and script_valid else (
        "partial" if page_valid and (quote_valid or script_valid) else "unverified"
    )
    confidence = min(
        1.0,
        max(0.0, _safe_float(candidate.get("confidence")), (script_score + pdf_score) / 2),
    )
    crop = _normalized_crop(candidate.get("crop"))
    return {
        "sequence": sequence,
        "claim_type": _safe_choice(candidate.get("claim_type"), {"claim", "data", "quote", "chart", "judgement"}, "claim"),
        "claim_text": claim_text,
        "script_quote": script_quote,
        "script_start": script_start,
        "script_end": script_end,
        "pdf_page": page_number,
        "pdf_quote": pdf_quote,
        "evidence_kind": _safe_choice(candidate.get("evidence_kind"), {"page", "crop", "chart", "quote"}, "page"),
        "crop_x": crop[0],
        "crop_y": crop[1],
        "crop_width": crop[2],
        "crop_height": crop[3],
        "confidence": round(confidence, 6),
        "verification_status": verification_status,
        "enabled": True,
        "use_as_visual": bool(candidate.get("use_as_visual")) and verification_status == "verified",
        "screenshot_path": None,
        "source_trace": _clip(candidate.get("source_trace") or "gemini_candidate", 300),
        "user_edited": False,
    }


def update_claim(
    claim: ReportPdfEvidenceClaim,
    values: dict[str, Any],
) -> ReportPdfEvidenceClaim:
    output = ReportPdfEvidenceOutput.query.filter_by(id=claim.output_id).one()
    source = Path(str(output.source_pdf_path or ""))
    if not source.is_file():
        raise PdfEvidenceError("原 PDF 文件不存在，无法更新证据")
    pages = extract_pdf_pages(source)
    final_script = _source_final_script(output.source_task_id)
    candidate = {
        "claim_text": values.get("claim_text", claim.claim_text),
        "script_quote": values.get("script_quote", claim.script_quote),
        "pdf_page": values.get("pdf_page", claim.pdf_page),
        "pdf_quote": values.get("pdf_quote", claim.pdf_quote),
        "claim_type": values.get("claim_type", claim.claim_type),
        "evidence_kind": values.get("evidence_kind", claim.evidence_kind),
        "confidence": values.get("confidence", claim.confidence),
        "crop": {
            "x": values.get("crop_x", claim.crop_x),
            "y": values.get("crop_y", claim.crop_y),
            "width": values.get("crop_width", claim.crop_width),
            "height": values.get("crop_height", claim.crop_height),
        },
        "use_as_visual": values.get("use_as_visual", claim.use_as_visual),
        "source_trace": "manual_edit",
    }
    verified = verify_candidate(candidate, final_script, pages, sequence=claim.sequence)
    if not verified:
        raise PdfEvidenceError("证据内容为空")
    for key, value in _claim_model_values(verified).items():
        setattr(claim, key, value)
    claim.enabled = _bool_value(values.get("enabled"), claim.enabled)
    manually_confirmed = _bool_value(values.get("manual_confirmed"), False)
    requested_visual = _bool_value(values.get("use_as_visual"), claim.use_as_visual)
    if manually_confirmed and claim.enabled and requested_visual:
        claim.verification_status = "manual_verified"
    claim.use_as_visual = (
        requested_visual
        and claim.enabled
        and claim.verification_status in VISUAL_ELIGIBLE_STATUSES
    )
    claim.user_edited = True
    claim.screenshot_path = None
    _refresh_output_summary(output)
    db.session.commit()
    relink_source_storyboards(output.source_task_id)
    return claim


def bulk_set_claim_visuals(
    output: ReportPdfEvidenceOutput,
    *,
    enabled: bool,
) -> int:
    """Apply an explicit human visual decision to every retained claim."""

    claims = (
        ReportPdfEvidenceClaim.query
        .filter_by(output_id=output.id)
        .order_by(ReportPdfEvidenceClaim.sequence.asc())
        .all()
    )
    changed = 0
    for claim in claims:
        if enabled:
            claim.enabled = True
            claim.use_as_visual = True
            claim.user_edited = True
            if claim.verification_status not in VISUAL_ELIGIBLE_STATUSES:
                claim.verification_status = "manual_verified"
            claim.source_trace = "manual_bulk_confirmation"
        else:
            claim.use_as_visual = False
            claim.user_edited = True
        changed += 1
    output.visuals_enabled = bool(enabled)
    _refresh_output_summary(output)
    db.session.commit()
    relink_source_storyboards(output.source_task_id)
    return changed


def set_visuals_enabled(output: ReportPdfEvidenceOutput, enabled: bool) -> None:
    output.visuals_enabled = bool(enabled)
    db.session.commit()
    relink_source_storyboards(output.source_task_id)


def link_storyboard_evidence(storyboard_task_id: str, *, commit: bool = True) -> int:
    plan = ReportVisualPlanOutput.query.filter_by(task_id=str(storyboard_task_id)).one_or_none()
    if not plan:
        return 0
    evidence = ReportPdfEvidenceOutput.query.filter_by(source_task_id=plan.source_task_id).one_or_none()
    ReportVisualPlanSegmentEvidence.query.filter(
        ReportVisualPlanSegmentEvidence.segment_id.in_(
            db.session.query(ReportVisualPlanSegment.id).filter_by(task_id=plan.task_id)
        )
    ).delete(synchronize_session=False)
    plan.evidence_output_id = evidence.id if evidence else None
    plan.evidence_schema_version = evidence.schema_version if evidence else None
    plan.evidence_status = evidence.status if evidence else "legacy_unavailable"
    if not evidence:
        if commit:
            db.session.commit()
        return 0
    claims = (
        ReportPdfEvidenceClaim.query
        .filter_by(output_id=evidence.id, enabled=True)
        .filter(ReportPdfEvidenceClaim.verification_status.in_(VISUAL_ELIGIBLE_STATUSES))
        .order_by(ReportPdfEvidenceClaim.sequence.asc())
        .all()
    )
    segments = (
        ReportVisualPlanSegment.query
        .filter(ReportVisualPlanSegment.task_id == plan.task_id, ReportVisualPlanSegment.sequence >= 1)
        .order_by(ReportVisualPlanSegment.sequence.asc())
        .all()
    )
    count = 0
    for segment in segments:
        matches = []
        for claim in claims:
            score = _segment_claim_score(segment.script_text, claim.script_quote or claim.claim_text)
            if score >= MIN_SEGMENT_LINK_SCORE:
                matches.append((score, claim))
        matches.sort(key=lambda item: item[0], reverse=True)
        for index, (score, claim) in enumerate(matches[:5]):
            db.session.add(ReportVisualPlanSegmentEvidence(
                segment_id=segment.id,
                claim_id=claim.id,
                match_score=round(score, 6),
                link_source="deterministic",
                use_as_visual=bool(
                    index == 0
                    and evidence.visuals_enabled
                    and claim.use_as_visual
                ),
            ))
            count += 1
    if commit:
        db.session.commit()
    return count


def relink_source_storyboards(source_task_id: str) -> int:
    task_ids = [
        row[0]
        for row in db.session.query(ReportVisualPlanOutput.task_id)
        .filter(ReportVisualPlanOutput.source_task_id == str(source_task_id))
        .all()
    ]
    total = 0
    for task_id in task_ids:
        total += link_storyboard_evidence(task_id, commit=False)
    db.session.commit()
    return total


def evidence_visual_for_segment(segment_id: str) -> tuple[ReportPdfEvidenceOutput, ReportPdfEvidenceClaim] | None:
    row = (
        db.session.query(ReportPdfEvidenceOutput, ReportPdfEvidenceClaim)
        .join(ReportPdfEvidenceClaim, ReportPdfEvidenceClaim.output_id == ReportPdfEvidenceOutput.id)
        .join(ReportVisualPlanSegmentEvidence, ReportVisualPlanSegmentEvidence.claim_id == ReportPdfEvidenceClaim.id)
        .filter(
            ReportVisualPlanSegmentEvidence.segment_id == str(segment_id),
            ReportVisualPlanSegmentEvidence.use_as_visual.is_(True),
            ReportPdfEvidenceOutput.visuals_enabled.is_(True),
            ReportPdfEvidenceClaim.enabled.is_(True),
            ReportPdfEvidenceClaim.use_as_visual.is_(True),
            ReportPdfEvidenceClaim.verification_status.in_(VISUAL_ELIGIBLE_STATUSES),
        )
        .order_by(ReportVisualPlanSegmentEvidence.match_score.desc())
        .first()
    )
    return row if row else None


def render_claim_screenshot(
    output: ReportPdfEvidenceOutput,
    claim: ReportPdfEvidenceClaim,
    output_path: str | None = None,
) -> str:
    try:
        import fitz  # type: ignore
    except ImportError as exc:
        raise PdfEvidenceError("缺少 PyMuPDF，无法生成证据截图") from exc
    source = Path(str(output.source_pdf_path or ""))
    page_number = int(claim.pdf_page or 0)
    if not source.is_file() or page_number < 1 or page_number > int(output.page_count or 0):
        raise PdfEvidenceError("证据页无效")
    if _sha256_file(source) != output.source_pdf_hash:
        raise PdfEvidenceError("原 PDF 内容已变化，拒绝生成不可追溯的证据截图")
    visual_source = Path(str(output.visual_pdf_path or ""))
    if (
        not visual_source.is_file()
        or not output.visual_pdf_hash
        or _sha256_file(visual_source) != output.visual_pdf_hash
    ):
        visual_source = source
    if visual_source != source:
        try:
            with fitz.open(str(visual_source)) as document:
                if document.page_count != int(output.page_count or 0):
                    visual_source = source
                else:
                    page = document.load_page(page_number - 1)
                    normalized_quote = _normalized_match_text(claim.pdf_quote)
                    normalized_page = _normalized_match_text(page.get_text("text") or "")
                    quote_score = (
                        1.0
                        if normalized_quote and normalized_quote in normalized_page
                        else _best_window_score(normalized_quote, normalized_page)
                    )
                    visual_alignment = (
                        _pdf_page_visual_similarity(source, visual_source, page_number)
                        if len(normalized_page) < 20
                        else 0.0
                    )
                    if (
                        not normalized_quote
                        or (quote_score < 0.92 and visual_alignment < 0.75)
                    ):
                        visual_source = source
        except Exception:
            visual_source = source
    target = Path(output_path) if output_path else _output_root(output.source_task_id) / "screenshots" / f"{claim.id}.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    crop_x, crop_y, crop_width, crop_height = _normalized_crop((
        claim.crop_x,
        claim.crop_y,
        claim.crop_width,
        claim.crop_height,
    ))
    with fitz.open(str(visual_source)) as document:
        page = document.load_page(page_number - 1)
        rect = page.rect
        crop = fitz.Rect(
            rect.x0 + rect.width * crop_x,
            rect.y0 + rect.height * crop_y,
            rect.x0 + rect.width * (crop_x + crop_width),
            rect.y0 + rect.height * (crop_y + crop_height),
        )
        pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), clip=crop, alpha=False)
        temporary = target.with_name(f".{target.stem}-{uuid.uuid4().hex[:8]}.png")
        pixmap.save(str(temporary))
    with Image.open(temporary) as raw:
        image = raw.convert("RGB")
        canvas = Image.new("RGB", (1280, 720), "white")
        image.thumbnail((1240, 660), Image.Resampling.LANCZOS)
        canvas.paste(image, ((1280 - image.width) // 2, (720 - image.height) // 2))
        canvas.save(target, "PNG", optimize=True)
    temporary.unlink(missing_ok=True)
    return str(target.resolve())


def output_payload(output: ReportPdfEvidenceOutput, *, include_claims: bool = True) -> dict[str, Any]:
    payload = {
        "id": output.id,
        "source_task_id": output.source_task_id,
        "schema_version": output.schema_version,
        "pdf_filename": output.pdf_filename,
        "source_pdf_hash": output.source_pdf_hash,
        "visual_pdf_source": output.visual_pdf_source or "source_pdf",
        "visual_pdf_hash": output.visual_pdf_hash or output.source_pdf_hash,
        "page_count": output.page_count,
        "final_script_hash": output.final_script_hash,
        "status": output.status,
        "visuals_enabled": bool(output.visuals_enabled),
        "claim_count": output.claim_count,
        "verified_claim_count": output.verified_claim_count,
        "mapped_script_chars": output.mapped_script_chars,
        "coverage_ratio": output.coverage_ratio,
        "parser_version": output.parser_version,
        "error": output.error,
        "created_at": output.created_at.isoformat() + "Z" if output.created_at else None,
        "updated_at": output.updated_at.isoformat() + "Z" if output.updated_at else None,
    }
    if include_claims:
        claims = (
            ReportPdfEvidenceClaim.query.filter_by(output_id=output.id)
            .order_by(ReportPdfEvidenceClaim.sequence.asc()).all()
        )
        payload["claims"] = [claim_payload(claim) for claim in claims]
    return payload


def claim_payload(claim: ReportPdfEvidenceClaim) -> dict[str, Any]:
    return {
        "id": claim.id,
        "sequence": claim.sequence,
        "claim_type": claim.claim_type,
        "claim_text": claim.claim_text,
        "script_quote": claim.script_quote,
        "script_start": claim.script_start,
        "script_end": claim.script_end,
        "pdf_page": claim.pdf_page,
        "pdf_quote": claim.pdf_quote,
        "evidence_kind": claim.evidence_kind,
        "crop": {
            "x": claim.crop_x,
            "y": claim.crop_y,
            "width": claim.crop_width,
            "height": claim.crop_height,
        },
        "confidence": claim.confidence,
        "verification_status": claim.verification_status,
        "enabled": bool(claim.enabled),
        "use_as_visual": bool(claim.use_as_visual),
        "has_screenshot": bool(claim.screenshot_path and Path(claim.screenshot_path).is_file()),
        "user_edited": bool(claim.user_edited),
    }


def _json_payloads(text: str) -> Iterable[Any]:
    stripped = text.strip()
    if stripped:
        try:
            yield json.loads(stripped)
        except (TypeError, ValueError):
            pass
    decoder = json.JSONDecoder()
    index = 0
    yielded = 0
    while index < len(text) and yielded < 200:
        match = re.search(r"[\{\[]", text[index:])
        if not match:
            break
        start = index + match.start()
        try:
            payload, consumed = decoder.raw_decode(text[start:])
        except ValueError:
            index = start + 1
            continue
        yield payload
        yielded += 1
        index = start + max(1, consumed)


def _claim_lists(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, list):
        if value and all(isinstance(item, dict) for item in value):
            for item in value:
                if _looks_like_claim(item):
                    yield item
        for item in value:
            yield from _claim_lists(item)
    elif isinstance(value, dict):
        for key, item in value.items():
            normalized_key = str(key).lower()
            if normalized_key in {"claims", "evidence", "evidence_map", "evidence_mapping", "items"}:
                yield from _claim_lists(item)
            elif isinstance(item, (dict, list)):
                yield from _claim_lists(item)


def _looks_like_claim(value: dict[str, Any]) -> bool:
    keys = {str(key).lower() for key in value}
    current_contract = bool(keys & {"claim", "claim_text", "script_quote", "pdf_quote", "quote"}) and bool(
        keys & {"page", "pdf_page", "page_number", "source_page"}
    )
    legacy_v6_contract = (
        "script_paragraph_index" in keys
        and bool(keys & {"pdf_pages", "source_excerpt"})
    )
    return current_contract or legacy_v6_contract


def _normalize_candidate(value: dict[str, Any]) -> dict[str, Any] | None:
    if "needs_pdf_evidence" in value and not _bool_value(value.get("needs_pdf_evidence"), False):
        return None
    legacy_paragraph_index = _positive_int(value.get("script_paragraph_index"))
    legacy_pages = value.get("pdf_pages")
    legacy_page = (
        next((_positive_int(item) for item in legacy_pages if _positive_int(item)), None)
        if isinstance(legacy_pages, list)
        else None
    )
    claim = value.get("claim_text") or value.get("claim") or value.get("statement") or value.get("主张")
    if not claim and legacy_paragraph_index:
        claim = value.get("capture_instruction") or value.get("source_excerpt")
    script_quote = value.get("script_quote") or value.get("script") or value.get("文案原句")
    if not script_quote and not legacy_paragraph_index:
        script_quote = claim
    pdf_quote = (
        value.get("pdf_quote")
        or value.get("quote")
        or value.get("source_quote")
        or value.get("PDF原文")
        or value.get("source_excerpt")
    )
    page = (
        value.get("pdf_page")
        or value.get("page_number")
        or value.get("source_page")
        or value.get("page")
        or legacy_page
    )
    if not claim or not page:
        return None
    legacy_evidence_type = str(value.get("evidence_type") or "").strip().lower()
    claim_type = value.get("claim_type") or value.get("type")
    evidence_kind = value.get("evidence_kind") or value.get("visual_type")
    if legacy_paragraph_index:
        claim_type = {
            "key_data": "data",
            "table": "data",
            "text_quote": "quote",
            "chart": "chart",
        }.get(legacy_evidence_type, claim_type or "claim")
        evidence_kind = {
            "key_data": "crop",
            "table": "chart",
            "text_quote": "quote",
            "chart": "chart",
        }.get(legacy_evidence_type, evidence_kind or "page")
    return {
        "claim_text": _clip(claim, MAX_CLAIM_CHARS),
        "script_quote": _clip(script_quote, MAX_QUOTE_CHARS),
        "pdf_quote": _clip(pdf_quote, MAX_QUOTE_CHARS),
        "pdf_page": _positive_int(page),
        "script_paragraph_index": legacy_paragraph_index,
        "legacy_mapping_text": value.get("capture_instruction"),
        "claim_type": claim_type or "claim",
        "evidence_kind": evidence_kind or ("chart" if value.get("chart") else "page"),
        "crop": value.get("crop") or value.get("bbox") or value.get("region"),
        "confidence": value.get("confidence") or 0,
        "use_as_visual": bool(
            value.get("use_as_visual")
            or value.get("visual_priority")
            or legacy_paragraph_index
        ),
        "source_trace": (
            value.get("source_trace")
            or value.get("id")
            or (
                f"gemini_v6_evidence_map:{legacy_paragraph_index}"
                if legacy_paragraph_index
                else "gemini_candidate"
            )
        ),
    }


def _claim_model_values(value: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "sequence", "claim_type", "claim_text", "script_quote", "script_start", "script_end",
        "pdf_page", "pdf_quote", "evidence_kind", "crop_x", "crop_y", "crop_width",
        "crop_height", "confidence", "verification_status", "enabled", "use_as_visual",
        "screenshot_path", "source_trace", "user_edited",
    )
    return {key: value.get(key) for key in keys}


def _source_final_script(source_task_id: str) -> str:
    from models.task import PublicationTask

    task = PublicationTask.query.with_entities(PublicationTask.result).filter_by(id=str(source_task_id)).one_or_none()
    result = task.result if task and isinstance(task.result, dict) else {}
    structured = result.get("structured_result") if isinstance(result.get("structured_result"), dict) else {}
    return str(structured.get("final_script_for_tts") or result.get("final_script_for_tts") or "").strip()


def _refresh_output_summary(output: ReportPdfEvidenceOutput) -> None:
    claims = ReportPdfEvidenceClaim.query.filter_by(output_id=output.id).all()
    enabled = [claim for claim in claims if claim.enabled]
    verified = [
        claim for claim in enabled
        if claim.verification_status in VISUAL_ELIGIBLE_STATUSES
    ]
    output.claim_count = len(claims)
    output.verified_claim_count = len(verified)
    output.mapped_script_chars = _union_length([
        (claim.script_start, claim.script_end)
        for claim in verified
        if claim.script_start is not None and claim.script_end is not None
    ])
    script_chars = len(_source_final_script(output.source_task_id))
    output.coverage_ratio = round(output.mapped_script_chars / max(1, script_chars), 6)
    output.status = "ready" if enabled and len(verified) == len(enabled) else (
        "partial" if verified else "unavailable"
    )


def _locate_script_quote(script: str, quote: str, claim: str) -> tuple[int | None, int | None, float]:
    for candidate in (quote, claim):
        candidate = str(candidate or "").strip()
        if not candidate:
            continue
        index = script.find(candidate)
        if index >= 0:
            return index, index + len(candidate), 1.0
    normalized_script, positions = _normalized_with_positions(script)
    normalized_quote = _normalized_match_text(quote or claim)
    index = normalized_script.find(normalized_quote)
    if index >= 0 and normalized_quote:
        start = positions[index]
        end = positions[index + len(normalized_quote) - 1] + 1
        return start, end, 1.0
    return None, None, _best_window_score(normalized_quote, normalized_script)


def _script_units(script: str) -> list[str]:
    paragraphs = [
        item.strip()
        for item in re.split(r"(?:\r?\n)+", str(script or ""))
        if item.strip()
    ]
    if len(paragraphs) >= 2:
        return paragraphs
    sentences = [
        item.strip()
        for item in re.split(r"(?<=[。！？!?])\s*", str(script or ""))
        if item.strip()
    ]
    return sentences


def _best_legacy_script_unit(
    script: str,
    one_based_index: int,
    mapping_text: Any,
    pdf_quote: Any,
) -> tuple[str, float]:
    """Map Gem v6 paragraph indexes without trusting the index as proof."""

    units = _script_units(script)
    if not units:
        return "", 0.0
    requested = max(0, int(one_based_index or 1) - 1)
    evidence_text = " ".join(
        item for item in (
            _clip(mapping_text, MAX_QUOTE_CHARS),
            _clip(pdf_quote, MAX_QUOTE_CHARS),
        )
        if item
    )
    scored = [
        (
            _legacy_mapping_score(evidence_text, unit),
            -abs(index - requested),
            index,
            unit,
        )
        for index, unit in enumerate(units)
    ]
    score, _, _, unit = max(scored)
    return unit, score


def _legacy_mapping_score(evidence_text: str, script_unit: str) -> float:
    evidence = _normalized_match_text(evidence_text)
    script = _normalized_match_text(script_unit)
    if not evidence or not script:
        return 0.0
    sequence_score = SequenceMatcher(None, evidence, script, autojunk=False).ratio()
    evidence_bigrams = {
        evidence[index:index + 2]
        for index in range(max(0, len(evidence) - 1))
    }
    script_bigrams = {
        script[index:index + 2]
        for index in range(max(0, len(script) - 1))
    }
    bigram_score = (
        len(evidence_bigrams & script_bigrams) / max(1, min(len(evidence_bigrams), len(script_bigrams)))
    )
    evidence_anchors = set(re.findall(r"\d+(?:\.\d+)?%?|[a-z]{3,}", evidence))
    script_anchors = set(re.findall(r"\d+(?:\.\d+)?%?|[a-z]{3,}", script))
    anchor_score = (
        len(evidence_anchors & script_anchors) / max(1, min(len(evidence_anchors), len(script_anchors)))
        if evidence_anchors and script_anchors
        else 0.0
    )
    return round(max(sequence_score, bigram_score, anchor_score), 6)


def _segment_claim_score(segment_text: str, claim_quote: str) -> float:
    segment = _normalized_match_text(segment_text)
    claim = _normalized_match_text(claim_quote)
    if not segment or not claim:
        return 0.0
    if claim in segment:
        return 1.0
    if segment in claim:
        # Storyboards deliberately split the narration into short spoken
        # clauses.  A clause fully contained in a longer verified claim is a
        # strong deterministic match even though it covers only part of that
        # claim.  Keep very short/generic fragments below the link threshold.
        return min(1.0, len(segment) / 24.0)
    return SequenceMatcher(None, segment, claim, autojunk=False).ratio()


def _best_window_score(needle: str, haystack: str) -> float:
    if not needle or not haystack:
        return 0.0
    if needle in haystack:
        return 1.0
    if len(haystack) <= len(needle) * 2:
        return SequenceMatcher(None, needle, haystack, autojunk=False).ratio()
    step = max(20, len(needle) // 4)
    width = max(len(needle), min(len(haystack), int(len(needle) * 1.4)))
    return max(
        SequenceMatcher(None, needle, haystack[start:start + width], autojunk=False).ratio()
        for start in range(0, max(1, len(haystack) - width + 1), step)
    )


def _normalized_with_positions(value: str) -> tuple[str, list[int]]:
    chars: list[str] = []
    positions: list[int] = []
    for index, char in enumerate(str(value or "")):
        if char.isspace():
            continue
        chars.append(char.lower())
        positions.append(index)
    return "".join(chars), positions


def _normalized_match_text(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).lower()


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return number if math.isfinite(number) else default


def _normalized_crop(value: Any) -> tuple[float, float, float, float]:
    if isinstance(value, (list, tuple)) and len(value) >= 4:
        raw = value[:4]
    elif isinstance(value, dict):
        raw = (
            value.get("x", 0), value.get("y", 0),
            value.get("width", value.get("w", 1)),
            value.get("height", value.get("h", 1)),
        )
    else:
        raw = (0, 0, 1, 1)
    x, y, width, height = (
        _safe_float(raw[0], 0.0),
        _safe_float(raw[1], 0.0),
        _safe_float(raw[2], 1.0),
        _safe_float(raw[3], 1.0),
    )
    x = min(0.98, max(0.0, x))
    y = min(0.98, max(0.0, y))
    width = min(1.0 - x, max(0.02, width))
    height = min(1.0 - y, max(0.02, height))
    return tuple(round(item, 6) for item in (x, y, width, height))


def _union_length(ranges: Iterable[tuple[int, int]]) -> int:
    valid = sorted((int(start), int(end)) for start, end in ranges if end > start)
    total = 0
    cursor_start = cursor_end = None
    for start, end in valid:
        if cursor_start is None:
            cursor_start, cursor_end = start, end
        elif start <= cursor_end:
            cursor_end = max(cursor_end, end)
        else:
            total += cursor_end - cursor_start
            cursor_start, cursor_end = start, end
    if cursor_start is not None:
        total += cursor_end - cursor_start
    return total


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _output_root(source_task_id: str) -> Path:
    root = Path(__file__).resolve().parents[3] / "data" / "processed" / "report_pdf_evidence" / str(source_task_id)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _clip(value: Any, limit: int) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _safe_choice(value: Any, allowed: set[str], fallback: str) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in allowed else fallback


def _bool_value(value: Any, fallback: bool) -> bool:
    if value is None:
        return bool(fallback)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}
