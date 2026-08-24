from datetime import datetime
import uuid

from . import db


class ContentPipelineAsset(db.Model):
    """Canonical content fingerprint reserved by the shared clean pipeline.

    One row represents the first accepted submission of a file's content hash.
    Later submissions keep their audit details in the submitting task result and
    point back to this row instead of creating another clean task.
    """

    __tablename__ = "content_pipeline_assets"
    __table_args__ = (
        db.UniqueConstraint(
            "media_type",
            "content_sha256",
            name="uq_content_pipeline_assets_type_sha256",
        ),
        db.Index(
            "ix_content_pipeline_assets_task_status",
            "canonical_task_id",
            "status",
        ),
        db.Index(
            "ix_content_pipeline_assets_created",
            "created_at",
        ),
    )

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    media_type = db.Column(db.String(20), nullable=False)
    content_sha256 = db.Column(db.String(64), nullable=False)
    canonical_task_id = db.Column(
        db.String(36),
        db.ForeignKey("publication_tasks.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_path = db.Column(db.String(600), nullable=False)
    filename = db.Column(db.String(500), nullable=True)
    source_mode = db.Column(db.String(80), nullable=True)
    status = db.Column(db.String(24), nullable=False, default="reserved")
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    def to_dict(self):
        return {
            "id": self.id,
            "media_type": self.media_type,
            "content_sha256": self.content_sha256,
            "canonical_task_id": self.canonical_task_id,
            "source_path": self.source_path,
            "filename": self.filename,
            "source_mode": self.source_mode,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
