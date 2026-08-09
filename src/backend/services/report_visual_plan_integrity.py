"""Durable completion checks for report storyboards.

Task status is a projection and can be stale after an ambiguous database commit.
The normalized output and its ordered segment rows are the durable source of
truth used by both workers and workflow APIs.
"""

from models import db, ReportVisualPlanOutput, ReportVisualPlanSegment
from sqlalchemy.orm import load_only


def storyboard_integrity_from_rows(output, segments):
    """Return a compact completion snapshot, or ``None`` when incomplete."""
    if not output:
        return None
    source_chars = int(getattr(output, "source_script_chars", 0) or 0)
    covered_chars = int(getattr(output, "covered_script_chars", 0) or 0)
    expected_count = int(getattr(output, "segment_count", 0) or 0)
    rows = list(segments or [])
    if source_chars <= 0 or covered_chars != source_chars or expected_count <= 0:
        return None
    if len(rows) != expected_count:
        return None
    sequences = [int(getattr(row, "sequence", -1)) for row in rows]
    if sequences != list(range(expected_count)):
        return None
    if any(
        not str(getattr(row, "script_text", "") or "").strip()
        or not str(getattr(row, "visual_prompt", "") or "").strip()
        for row in rows
    ):
        return None
    return {
        "task_id": str(output.task_id),
        "source_task_id": str(output.source_task_id),
        "schema_version": str(output.schema_version),
        "segment_count": expected_count,
        "source_script_chars": source_chars,
        "covered_script_chars": covered_chars,
        "visual_scheme_key": str(output.visual_scheme_key),
        "visual_scheme_name": str(output.visual_scheme_name),
        "gemini_output_path": output.gemini_output_path,
        "normalized_output_path": output.normalized_output_path,
        "normalizer_model": output.normalizer_model,
        "evidence_status": output.evidence_status or "legacy_unavailable",
    }


def durable_storyboard_integrity(task_id):
    """Read the minimum durable rows needed to prove storyboard completion."""
    task_id = str(task_id or "").strip()
    if not task_id:
        return None
    output = (
        ReportVisualPlanOutput.query.options(load_only(
            ReportVisualPlanOutput.task_id,
            ReportVisualPlanOutput.source_task_id,
            ReportVisualPlanOutput.schema_version,
            ReportVisualPlanOutput.source_script_chars,
            ReportVisualPlanOutput.covered_script_chars,
            ReportVisualPlanOutput.segment_count,
            ReportVisualPlanOutput.visual_scheme_key,
            ReportVisualPlanOutput.visual_scheme_name,
            ReportVisualPlanOutput.gemini_output_path,
            ReportVisualPlanOutput.normalized_output_path,
            ReportVisualPlanOutput.normalizer_model,
            ReportVisualPlanOutput.evidence_status,
        ))
        .filter(ReportVisualPlanOutput.task_id == task_id)
        .one_or_none()
    )
    if not output:
        return None
    segments = (
        db.session.query(
            ReportVisualPlanSegment.sequence,
            ReportVisualPlanSegment.script_text,
            ReportVisualPlanSegment.visual_prompt,
        )
        .filter(ReportVisualPlanSegment.task_id == task_id)
        .order_by(ReportVisualPlanSegment.sequence.asc())
        .all()
    )
    return storyboard_integrity_from_rows(output, segments)
