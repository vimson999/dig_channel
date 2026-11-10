"""Relational storage for Codex-produced PDF list analysis reports."""

from datetime import datetime
import uuid

from sqlalchemy import ForeignKeyConstraint, UniqueConstraint

from . import db


def _new_id():
    return str(uuid.uuid4())


def _iso(value):
    return value.isoformat() if value else None


class CodexPdfAnalysisRun(db.Model):
    __tablename__ = "codex_pdf_analysis_runs"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "source_sha256", "contract_version", "analysis_version",
            name="uq_codex_pdf_analysis_runs_source_version",
        ),
        db.Index(
            "ix_codex_pdf_analysis_runs_user_created",
            "user_id", "created_at",
        ),
        db.Index(
            "ix_codex_pdf_analysis_runs_user_status_created",
            "user_id", "status", "created_at",
        ),
    )

    id = db.Column(db.String(36), primary_key=True, default=_new_id)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_filename = db.Column(db.String(255), nullable=False)
    source_exported_at = db.Column(db.DateTime, nullable=True)
    source_sha256 = db.Column(db.String(64), nullable=False)
    source_record_count = db.Column(db.Integer, nullable=False)
    source_total_download_count = db.Column(db.BigInteger, nullable=False)
    contract_version = db.Column(db.String(64), nullable=False)
    analysis_version = db.Column(db.String(64), nullable=False)
    status = db.Column(db.String(24), nullable=False, default="completed")
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    completed_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    overall_summary = db.Column(db.Text, nullable=False)
    selected_candidate_title = db.Column(db.String(500), nullable=True)
    data_quality_note = db.Column(db.Text, nullable=True)

    def to_history_dict(self):
        return {
            "id": self.id,
            "source_filename": self.source_filename,
            "source_exported_at": _iso(self.source_exported_at),
            "source_record_count": self.source_record_count,
            "source_total_download_count": self.source_total_download_count,
            "contract_version": self.contract_version,
            "analysis_version": self.analysis_version,
            "status": self.status,
            "created_at": _iso(self.created_at),
            "completed_at": _iso(self.completed_at),
            "selected_candidate_title": self.selected_candidate_title,
        }

    def to_detail_dict(self):
        return {
            **self.to_history_dict(),
            "overall_summary": self.overall_summary,
            "source_sha256": self.source_sha256,
            "data_quality_note": self.data_quality_note,
        }


class CodexPdfAnalysisSource(db.Model):
    __tablename__ = "codex_pdf_analysis_sources"
    __table_args__ = (
        UniqueConstraint("run_id", "source_key", name="uq_codex_pdf_analysis_sources_key"),
        UniqueConstraint("id", "run_id", name="uq_codex_pdf_analysis_sources_id_run"),
    )

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    run_id = db.Column(
        db.String(36),
        db.ForeignKey("codex_pdf_analysis_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_key = db.Column(db.String(160), nullable=False)
    name = db.Column(db.Text, nullable=False)
    institution = db.Column(db.String(255), nullable=True)
    report_date_text = db.Column(db.String(64), nullable=True)
    upload_time_text = db.Column(db.String(64), nullable=True)
    status = db.Column(db.String(64), nullable=True)
    download_count = db.Column(db.BigInteger, nullable=True)

    def to_dict(self):
        return {
            "id": self.id,
            "source_key": self.source_key,
            "name": self.name,
            "institution": self.institution,
            "report_date_text": self.report_date_text,
            "upload_time_text": self.upload_time_text,
            "status": self.status,
            "download_count": self.download_count,
        }


class CodexPdfAnalysisSection(db.Model):
    __tablename__ = "codex_pdf_analysis_sections"
    __table_args__ = (
        UniqueConstraint("run_id", "module_code", name="uq_codex_pdf_analysis_sections_module"),
        UniqueConstraint("run_id", "ordinal", name="uq_codex_pdf_analysis_sections_ordinal"),
        UniqueConstraint("id", "run_id", name="uq_codex_pdf_analysis_sections_id_run"),
        db.Index("ix_codex_pdf_analysis_sections_run_order", "run_id", "ordinal"),
    )

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    run_id = db.Column(
        db.String(36),
        db.ForeignKey("codex_pdf_analysis_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    module_code = db.Column(db.String(64), nullable=False)
    title = db.Column(db.String(255), nullable=False)
    ordinal = db.Column(db.Integer, nullable=False)
    summary_text = db.Column(db.Text, nullable=True)
    body_text = db.Column(db.Text, nullable=False)
    evidence_state = db.Column(db.String(48), nullable=True)

    def to_dict(self):
        return {
            "module_code": self.module_code,
            "title": self.title,
            "ordinal": self.ordinal,
            "summary_text": self.summary_text,
            "body_text": self.body_text,
            "evidence_state": self.evidence_state,
        }


class CodexPdfAnalysisBlock(db.Model):
    __tablename__ = "codex_pdf_analysis_blocks"
    __table_args__ = (
        UniqueConstraint("section_id", "ordinal", name="uq_codex_pdf_blocks_order"),
        db.CheckConstraint("kind IN ('heading','finding','limitation','metric','table','ranking')", name="ck_codex_pdf_blocks_kind"),
    )

    id = db.Column(db.Integer, primary_key=True)
    section_id = db.Column(db.Integer, db.ForeignKey("codex_pdf_analysis_sections.id", ondelete="CASCADE"), nullable=False)
    ordinal = db.Column(db.Integer, nullable=False)
    kind = db.Column(db.String(16), nullable=False)
    title = db.Column(db.String(255), nullable=False)
    content_text = db.Column(db.Text)
    number_value = db.Column(db.Float)
    unit = db.Column(db.String(32))
    ranking_type = db.Column(db.String(48))


class CodexPdfAnalysisCell(db.Model):
    __tablename__ = "codex_pdf_analysis_cells"

    block_id = db.Column(db.Integer, db.ForeignKey("codex_pdf_analysis_blocks.id", ondelete="CASCADE"), primary_key=True)
    row_no = db.Column(db.Integer, primary_key=True)
    column_no = db.Column(db.Integer, primary_key=True)
    text_value = db.Column(db.Text)
    number_value = db.Column(db.Float)


class CodexPdfAnalysisTopic(db.Model):
    __tablename__ = "codex_pdf_analysis_topics"
    __table_args__ = (
        UniqueConstraint(
            "run_id", "classification_scope", "topic_name",
            name="uq_codex_pdf_analysis_topics_scope_name",
        ),
        UniqueConstraint(
            "run_id", "classification_scope", "rank",
            name="uq_codex_pdf_analysis_topics_scope_rank",
        ),
        db.Index("ix_codex_pdf_analysis_topics_run_rank", "run_id", "classification_scope", "rank"),
    )

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    run_id = db.Column(
        db.String(36),
        db.ForeignKey("codex_pdf_analysis_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    topic_name = db.Column(db.String(255), nullable=False)
    classification_scope = db.Column(db.String(16), nullable=False)
    rank = db.Column(db.Integer, nullable=False)
    pdf_count = db.Column(db.Integer, nullable=False)
    total_download_count = db.Column(db.BigInteger, nullable=False)
    average_download_count = db.Column(db.Float, nullable=False)
    institution_count = db.Column(db.Integer, nullable=False)
    keywords_text = db.Column(db.Text, nullable=True)

    def to_dict(self):
        return {
            "topic_name": self.topic_name,
            "classification_scope": self.classification_scope,
            "rank": self.rank,
            "pdf_count": self.pdf_count,
            "total_download_count": self.total_download_count,
            "average_download_count": self.average_download_count,
            "institution_count": self.institution_count,
            "keywords_text": self.keywords_text,
        }


class CodexPdfAnalysisRanking(db.Model):
    __tablename__ = "codex_pdf_analysis_rankings"
    __table_args__ = (
        ForeignKeyConstraint(
            ["source_id", "run_id"],
            ["codex_pdf_analysis_sources.id", "codex_pdf_analysis_sources.run_id"],
            ondelete="CASCADE",
            name="fk_codex_pdf_analysis_rankings_source_run",
        ),
        UniqueConstraint(
            "run_id", "ranking_type", "topic_name", "rank",
            name="uq_codex_pdf_analysis_rankings_position",
        ),
        db.Index("ix_codex_pdf_analysis_rankings_run_type", "run_id", "ranking_type", "rank"),
    )

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    run_id = db.Column(
        db.String(36),
        db.ForeignKey("codex_pdf_analysis_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    ranking_type = db.Column(db.String(64), nullable=False)
    rank = db.Column(db.Integer, nullable=False)
    source_id = db.Column(db.Integer, nullable=False)
    topic_name = db.Column(db.String(255), nullable=True)

    def to_dict(self, source):
        return {
            "ranking_type": self.ranking_type,
            "rank": self.rank,
            "topic_name": self.topic_name,
            "source": source.to_dict(),
        }


class CodexPdfAnalysisTarget(db.Model):
    __tablename__ = "codex_pdf_analysis_targets"
    __table_args__ = (
        UniqueConstraint("run_id", "ticker", name="uq_codex_pdf_analysis_targets_ticker"),
        db.Index("ix_codex_pdf_analysis_targets_run_downloads", "run_id", "total_download_count"),
    )

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    run_id = db.Column(
        db.String(36),
        db.ForeignKey("codex_pdf_analysis_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    ticker = db.Column(db.String(32), nullable=False)
    display_name = db.Column(db.String(255), nullable=False)
    main_topic = db.Column(db.String(255), nullable=True)
    pdf_count = db.Column(db.Integer, nullable=False)
    total_download_count = db.Column(db.BigInteger, nullable=False)
    average_download_count = db.Column(db.Float, nullable=False)
    institution_count = db.Column(db.Integer, nullable=False)
    institutions_text = db.Column(db.Text, nullable=True)
    aggregation_note = db.Column(db.Text, nullable=True)

    def to_dict(self):
        return {
            "ticker": self.ticker,
            "display_name": self.display_name,
            "main_topic": self.main_topic,
            "pdf_count": self.pdf_count,
            "total_download_count": self.total_download_count,
            "average_download_count": self.average_download_count,
            "institution_count": self.institution_count,
            "institutions_text": self.institutions_text,
            "aggregation_note": self.aggregation_note,
        }


class CodexPdfAnalysisCandidate(db.Model):
    __tablename__ = "codex_pdf_analysis_candidates"
    __table_args__ = (
        UniqueConstraint("run_id", "candidate_key", name="uq_codex_pdf_analysis_candidates_key"),
        UniqueConstraint("run_id", "ordinal", name="uq_codex_pdf_analysis_candidates_ordinal"),
        UniqueConstraint("id", "run_id", name="uq_codex_pdf_analysis_candidates_id_run"),
        db.Index("ix_codex_pdf_analysis_candidates_run_order", "run_id", "ordinal"),
    )

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    run_id = db.Column(
        db.String(36),
        db.ForeignKey("codex_pdf_analysis_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    candidate_key = db.Column(db.String(80), nullable=False)
    track_code = db.Column(db.String(48), nullable=False)
    title = db.Column(db.String(500), nullable=False)
    parent_question = db.Column(db.Text, nullable=False)
    audience = db.Column(db.Text, nullable=True)
    priority = db.Column(db.Integer, nullable=False)
    new_evidence = db.Column(db.Text, nullable=True)
    rationale = db.Column(db.Text, nullable=False)
    uncertainty_text = db.Column(db.Text, nullable=True)
    is_primary = db.Column(db.Boolean, nullable=False, default=False)
    ordinal = db.Column(db.Integer, nullable=False)

    def to_dict(self):
        return {
            "candidate_key": self.candidate_key,
            "track_code": self.track_code,
            "title": self.title,
            "parent_question": self.parent_question,
            "audience": self.audience,
            "priority": self.priority,
            "new_evidence": self.new_evidence,
            "rationale": self.rationale,
            "uncertainty_text": self.uncertainty_text,
            "is_primary": self.is_primary,
            "ordinal": self.ordinal,
        }


class CodexPdfAnalysisCandidateSource(db.Model):
    __tablename__ = "codex_pdf_analysis_candidate_sources"
    __table_args__ = (
        ForeignKeyConstraint(
            ["candidate_id", "run_id"],
            ["codex_pdf_analysis_candidates.id", "codex_pdf_analysis_candidates.run_id"],
            ondelete="CASCADE",
            name="fk_codex_pdf_analysis_candidate_sources_candidate_run",
        ),
        ForeignKeyConstraint(
            ["source_id", "run_id"],
            ["codex_pdf_analysis_sources.id", "codex_pdf_analysis_sources.run_id"],
            ondelete="CASCADE",
            name="fk_codex_pdf_analysis_candidate_sources_source_run",
        ),
        UniqueConstraint(
            "candidate_id", "source_id", "source_role",
            name="uq_codex_pdf_analysis_candidate_sources_role",
        ),
        db.Index("ix_codex_pdf_analysis_candidate_sources_run", "run_id"),
    )

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    run_id = db.Column(db.String(36), nullable=False)
    candidate_id = db.Column(db.Integer, nullable=False)
    source_id = db.Column(db.Integer, nullable=False)
    source_role = db.Column(db.String(48), nullable=False)
    selection_reason = db.Column(db.Text, nullable=False)

    def to_dict(self, source):
        return {
            "source_role": self.source_role,
            "selection_reason": self.selection_reason,
            "source": source.to_dict(),
        }


class CodexPdfAnalysisSectionSource(db.Model):
    __tablename__ = "codex_pdf_analysis_section_sources"
    __table_args__ = (
        ForeignKeyConstraint(
            ["section_id", "run_id"],
            ["codex_pdf_analysis_sections.id", "codex_pdf_analysis_sections.run_id"],
            ondelete="CASCADE",
            name="fk_codex_pdf_analysis_section_sources_section_run",
        ),
        ForeignKeyConstraint(
            ["source_id", "run_id"],
            ["codex_pdf_analysis_sources.id", "codex_pdf_analysis_sources.run_id"],
            ondelete="CASCADE",
            name="fk_codex_pdf_analysis_section_sources_source_run",
        ),
        UniqueConstraint(
            "section_id", "source_id", "source_role",
            name="uq_codex_pdf_analysis_section_sources_role",
        ),
        db.Index("ix_codex_pdf_analysis_section_sources_run", "run_id"),
    )

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    run_id = db.Column(db.String(36), nullable=False)
    section_id = db.Column(db.Integer, nullable=False)
    source_id = db.Column(db.Integer, nullable=False)
    source_role = db.Column(db.String(48), nullable=False)
    evidence_note = db.Column(db.Text, nullable=True)

    def to_dict(self, source):
        return {
            "source_role": self.source_role,
            "evidence_note": self.evidence_note,
            "source": source.to_dict(),
        }
