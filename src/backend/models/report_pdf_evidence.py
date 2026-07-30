from datetime import datetime
import uuid

from . import db


class ReportPdfEvidenceOutput(db.Model):
    """Versioned evidence index for one authoritative PDF script."""

    __tablename__ = "report_pdf_evidence_outputs"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    source_task_id = db.Column(
        db.String(36),
        db.ForeignKey("publication_tasks.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    pdf_attachment_id = db.Column(db.String(64), nullable=True, index=True)
    schema_version = db.Column(db.String(64), nullable=False)
    pdf_filename = db.Column(db.String(500), nullable=True)
    source_pdf_path = db.Column(db.String(700), nullable=True)
    source_pdf_hash = db.Column(db.String(64), nullable=False, index=True)
    source_pdf_bytes = db.Column(db.BigInteger, nullable=False, default=0)
    visual_pdf_path = db.Column(db.String(700), nullable=True)
    visual_pdf_hash = db.Column(db.String(64), nullable=True)
    visual_pdf_bytes = db.Column(db.BigInteger, nullable=False, default=0)
    visual_pdf_source = db.Column(db.String(40), nullable=False, default="source_pdf")
    page_count = db.Column(db.Integer, nullable=False, default=0)
    final_script_hash = db.Column(db.String(64), nullable=False, index=True)
    raw_response_hash = db.Column(db.String(64), nullable=False, index=True)
    gemini_task_id = db.Column(
        db.String(36),
        db.ForeignKey("publication_tasks.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    status = db.Column(db.String(32), nullable=False, default="unavailable", index=True)
    visuals_enabled = db.Column(db.Boolean, nullable=False, default=False)
    claim_count = db.Column(db.Integer, nullable=False, default=0)
    verified_claim_count = db.Column(db.Integer, nullable=False, default=0)
    mapped_script_chars = db.Column(db.Integer, nullable=False, default=0)
    coverage_ratio = db.Column(db.Float, nullable=False, default=0.0)
    raw_artifact_path = db.Column(db.String(700), nullable=True)
    normalized_artifact_path = db.Column(db.String(700), nullable=True)
    parser_provider = db.Column(db.String(40), nullable=False, default="local")
    parser_version = db.Column(db.String(64), nullable=False)
    error = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class ReportPdfEvidenceClaim(db.Model):
    """One claim in the final script and its locally verified PDF evidence."""

    __tablename__ = "report_pdf_evidence_claims"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    output_id = db.Column(
        db.String(36),
        db.ForeignKey("report_pdf_evidence_outputs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sequence = db.Column(db.Integer, nullable=False)
    claim_type = db.Column(db.String(40), nullable=False, default="claim")
    claim_text = db.Column(db.Text, nullable=False)
    script_quote = db.Column(db.Text, nullable=True)
    script_start = db.Column(db.Integer, nullable=True)
    script_end = db.Column(db.Integer, nullable=True)
    pdf_page = db.Column(db.Integer, nullable=True, index=True)
    pdf_quote = db.Column(db.Text, nullable=True)
    evidence_kind = db.Column(db.String(32), nullable=False, default="page")
    crop_x = db.Column(db.Float, nullable=False, default=0.0)
    crop_y = db.Column(db.Float, nullable=False, default=0.0)
    crop_width = db.Column(db.Float, nullable=False, default=1.0)
    crop_height = db.Column(db.Float, nullable=False, default=1.0)
    confidence = db.Column(db.Float, nullable=False, default=0.0)
    verification_status = db.Column(db.String(32), nullable=False, default="unverified", index=True)
    enabled = db.Column(db.Boolean, nullable=False, default=True)
    use_as_visual = db.Column(db.Boolean, nullable=False, default=False)
    screenshot_path = db.Column(db.String(700), nullable=True)
    source_trace = db.Column(db.String(300), nullable=True)
    user_edited = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint("output_id", "sequence", name="uq_report_pdf_evidence_claim_sequence"),
        db.Index("ix_report_pdf_evidence_claim_output_page", "output_id", "pdf_page"),
    )


class ReportVisualPlanSegmentEvidence(db.Model):
    """Reusable claim link shared by every visual scheme and final renderer."""

    __tablename__ = "report_visual_plan_segment_evidence"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    segment_id = db.Column(
        db.String(36),
        db.ForeignKey("report_visual_plan_segments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    claim_id = db.Column(
        db.String(36),
        db.ForeignKey("report_pdf_evidence_claims.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    match_score = db.Column(db.Float, nullable=False, default=0.0)
    link_source = db.Column(db.String(32), nullable=False, default="deterministic")
    use_as_visual = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint("segment_id", "claim_id", name="uq_report_visual_segment_evidence"),
        db.Index("ix_report_visual_segment_evidence_segment_visual", "segment_id", "use_as_visual"),
    )
