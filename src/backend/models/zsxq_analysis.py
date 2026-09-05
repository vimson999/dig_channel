"""Structured persistence models for uploaded ZSXQ list analysis runs."""

from datetime import datetime
import uuid

from . import db


def _new_id():
    return str(uuid.uuid4())


class ZsxqAnalysisRun(db.Model):
    """One immutable, user-owned analysis of one uploaded JSON snapshot."""

    __tablename__ = "zsxq_analysis_runs"
    __table_args__ = (
        db.Index(
            "ix_zsxq_analysis_runs_user_kind_created",
            "user_id",
            "kind",
            "created_at",
        ),
        db.Index(
            "ix_zsxq_analysis_runs_user_status_created",
            "user_id",
            "status",
            "created_at",
        ),
    )

    id = db.Column(db.String(36), primary_key=True, default=_new_id)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind = db.Column(db.String(16), nullable=False)
    source_schema = db.Column(db.String(64), nullable=False)
    source_filename = db.Column(db.String(255), nullable=False)
    source_exported_at = db.Column(db.DateTime, nullable=True)
    source_sha256 = db.Column(db.String(64), nullable=False)
    source_size_bytes = db.Column(db.BigInteger, nullable=False, default=0)
    source_artifact_path = db.Column(db.String(600), nullable=False)
    analysis_version = db.Column(db.String(64), nullable=False)
    status = db.Column(
        db.String(24), nullable=False, default="completed", index=True
    )
    error_message = db.Column(db.Text, nullable=True)

    item_count = db.Column(db.Integer, nullable=False, default=0)
    done_count = db.Column(db.Integer, nullable=False, default=0)
    pending_count = db.Column(db.Integer, nullable=False, default=0)
    failed_count = db.Column(db.Integer, nullable=False, default=0)
    processing_count = db.Column(db.Integer, nullable=False, default=0)
    total_download_count = db.Column(db.BigInteger, nullable=False, default=0)
    download_count_min = db.Column(db.Integer, nullable=False, default=0)
    download_count_max = db.Column(db.Integer, nullable=False, default=0)
    download_count_avg = db.Column(db.Float, nullable=False, default=0)
    download_count_median = db.Column(db.Float, nullable=False, default=0)
    total_file_size_bytes = db.Column(db.BigInteger, nullable=False, default=0)
    total_duration_seconds = db.Column(
        db.BigInteger, nullable=False, default=0
    )
    duration_avg_seconds = db.Column(db.Float, nullable=False, default=0)
    duration_median_seconds = db.Column(db.Float, nullable=False, default=0)
    duration_nonzero_count = db.Column(db.Integer, nullable=False, default=0)
    unique_topic_count = db.Column(db.Integer, nullable=False, default=0)
    duplicate_file_id_count = db.Column(db.Integer, nullable=False, default=0)
    period_start = db.Column(db.DateTime, nullable=True)
    period_end = db.Column(db.DateTime, nullable=True)

    created_at = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow
    )
    completed_at = db.Column(db.DateTime, nullable=True)

    # Do not accidentally hydrate every child row from a run object.
    items = db.relationship(
        "ZsxqAnalysisItem",
        back_populates="run",
        cascade="all, delete-orphan",
        lazy="noload",
        passive_deletes=True,
    )
    topics = db.relationship(
        "ZsxqAnalysisTopic",
        back_populates="run",
        cascade="all, delete-orphan",
        lazy="noload",
        passive_deletes=True,
    )
    keywords = db.relationship(
        "ZsxqAnalysisKeyword",
        back_populates="run",
        cascade="all, delete-orphan",
        lazy="noload",
        passive_deletes=True,
    )

    def to_history_dict(self):
        """Return only the narrow fields needed by the history list."""
        return {
            "id": self.id,
            "kind": self.kind,
            "source_filename": self.source_filename,
            "status": self.status,
            "analysis_version": self.analysis_version,
            "source_sha256_prefix": (self.source_sha256 or "")[:12],
            "source_size_bytes": self.source_size_bytes or 0,
            "item_count": self.item_count or 0,
            "done_count": self.done_count or 0,
            "pending_count": self.pending_count or 0,
            "failed_count": self.failed_count or 0,
            "processing_count": self.processing_count or 0,
            "total_download_count": self.total_download_count or 0,
            "total_duration_seconds": self.total_duration_seconds or 0,
            "created_at": self.created_at.isoformat()
            if self.created_at
            else None,
            "completed_at": self.completed_at.isoformat()
            if self.completed_at
            else None,
        }

    def to_summary_dict(self):
        return {
            **self.to_history_dict(),
            "source_schema": self.source_schema,
            "source_exported_at": (
                self.source_exported_at.isoformat()
                if self.source_exported_at
                else None
            ),
            "download_count_min": self.download_count_min or 0,
            "download_count_max": self.download_count_max or 0,
            "download_count_avg": self.download_count_avg or 0,
            "download_count_median": self.download_count_median or 0,
            "total_file_size_bytes": self.total_file_size_bytes or 0,
            "duration_avg_seconds": self.duration_avg_seconds or 0,
            "duration_median_seconds": self.duration_median_seconds or 0,
            "duration_nonzero_count": self.duration_nonzero_count or 0,
            "unique_topic_count": self.unique_topic_count or 0,
            "duplicate_file_id_count": self.duplicate_file_id_count or 0,
            "period_start": self.period_start.isoformat()
            if self.period_start
            else None,
            "period_end": self.period_end.isoformat()
            if self.period_end
            else None,
            "source_sha256": self.source_sha256,
            "source_available": bool(self.source_artifact_path),
        }


class ZsxqAnalysisItem(db.Model):
    """One normalized source item belonging to one analysis run."""

    __tablename__ = "zsxq_analysis_items"
    __table_args__ = (
        db.UniqueConstraint(
            "run_id", "item_index", name="uq_zsxq_analysis_items_run_index"
        ),
        db.Index(
            "ix_zsxq_analysis_items_run_download",
            "run_id",
            "download_count",
        ),
        db.Index(
            "ix_zsxq_analysis_items_run_status",
            "run_id",
            "status",
        ),
        db.Index(
            "ix_zsxq_analysis_items_run_topic",
            "run_id",
            "topic_id",
        ),
    )

    id = db.Column(db.String(36), primary_key=True, default=_new_id)
    run_id = db.Column(
        db.String(36),
        db.ForeignKey("zsxq_analysis_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    item_index = db.Column(db.Integer, nullable=False)
    file_id = db.Column(db.String(160), nullable=False)
    topic_id = db.Column(db.String(160), nullable=True)
    name = db.Column(db.Text, nullable=False)
    normalized_name = db.Column(db.Text, nullable=False)
    group_name = db.Column(db.String(255), nullable=True)
    status = db.Column(db.String(24), nullable=False)
    download_count = db.Column(db.Integer, nullable=False, default=0)
    file_size_bytes = db.Column(db.BigInteger, nullable=False, default=0)
    duration_seconds = db.Column(db.BigInteger, nullable=False, default=0)
    upload_time_text = db.Column(db.String(100), nullable=True)
    upload_time = db.Column(db.DateTime, nullable=True)
    is_duplicate_file = db.Column(db.Boolean, nullable=False, default=False)

    run = db.relationship(
        "ZsxqAnalysisRun", back_populates="items", lazy="noload"
    )

    def to_dict(self):
        return {
            "id": self.id,
            "item_index": self.item_index,
            "file_id": self.file_id,
            "topic_id": self.topic_id,
            "name": self.name,
            "normalized_name": self.normalized_name,
            "group_name": self.group_name,
            "status": self.status,
            "download_count": self.download_count or 0,
            "file_size_bytes": self.file_size_bytes or 0,
            "duration_seconds": self.duration_seconds or 0,
            "upload_time": self.upload_time.isoformat()
            if self.upload_time
            else None,
            "upload_time_text": self.upload_time_text,
            "is_duplicate_file": bool(self.is_duplicate_file),
        }


class ZsxqAnalysisTopic(db.Model):
    """Per-run topic projection used by the detail view."""

    __tablename__ = "zsxq_analysis_topics"
    __table_args__ = (
        db.UniqueConstraint(
            "run_id", "topic_id", name="uq_zsxq_analysis_topics_run_topic"
        ),
        db.Index(
            "ix_zsxq_analysis_topics_run_rank",
            "run_id",
            "rank",
        ),
        db.Index(
            "ix_zsxq_analysis_topics_run_download",
            "run_id",
            "total_download_count",
        ),
    )

    id = db.Column(db.String(36), primary_key=True, default=_new_id)
    run_id = db.Column(
        db.String(36),
        db.ForeignKey("zsxq_analysis_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    topic_id = db.Column(db.String(160), nullable=False)
    rank = db.Column(db.Integer, nullable=False)
    item_count = db.Column(db.Integer, nullable=False, default=0)
    done_count = db.Column(db.Integer, nullable=False, default=0)
    total_download_count = db.Column(db.BigInteger, nullable=False, default=0)
    max_download_count = db.Column(db.Integer, nullable=False, default=0)
    latest_upload_time = db.Column(db.DateTime, nullable=True)
    representative_name = db.Column(db.Text, nullable=True)

    run = db.relationship(
        "ZsxqAnalysisRun", back_populates="topics", lazy="noload"
    )

    def to_dict(self):
        return {
            "topic_id": self.topic_id,
            "rank": self.rank,
            "item_count": self.item_count or 0,
            "done_count": self.done_count or 0,
            "total_download_count": self.total_download_count or 0,
            "max_download_count": self.max_download_count or 0,
            "latest_upload_time": (
                self.latest_upload_time.isoformat()
                if self.latest_upload_time
                else None
            ),
            "representative_name": self.representative_name,
        }


class ZsxqAnalysisKeyword(db.Model):
    """Explainable per-run keyword projection for the word cloud/rank list."""

    __tablename__ = "zsxq_analysis_keywords"
    __table_args__ = (
        db.UniqueConstraint(
            "run_id", "keyword", name="uq_zsxq_analysis_keywords_run_keyword"
        ),
        db.Index(
            "ix_zsxq_analysis_keywords_run_rank",
            "run_id",
            "rank",
        ),
    )

    id = db.Column(db.String(36), primary_key=True, default=_new_id)
    run_id = db.Column(
        db.String(36),
        db.ForeignKey("zsxq_analysis_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    keyword = db.Column(db.String(120), nullable=False)
    category = db.Column(db.String(32), nullable=False, default="phrase")
    rank = db.Column(db.Integer, nullable=False)
    item_count = db.Column(db.Integer, nullable=False, default=0)
    total_download_count = db.Column(db.BigInteger, nullable=False, default=0)

    run = db.relationship(
        "ZsxqAnalysisRun", back_populates="keywords", lazy="noload"
    )

    def to_dict(self):
        return {
            "keyword": self.keyword,
            "category": self.category,
            "rank": self.rank,
            "item_count": self.item_count or 0,
            "total_download_count": self.total_download_count or 0,
        }
