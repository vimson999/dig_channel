from datetime import datetime

from sqlalchemy.dialects.postgresql import ARRAY

from . import db


class PublicationTaskBilibiliUpload(db.Model):
    """Structured one-to-one state for Bilibili upload child tasks."""

    __tablename__ = "publication_task_bilibili_uploads"

    task_id = db.Column(
        db.String(36),
        db.ForeignKey("publication_tasks.id", ondelete="CASCADE"),
        primary_key=True,
    )
    parent_task_id = db.Column(db.String(36), nullable=False)
    parent_feature = db.Column(db.String(80), nullable=False)
    source_label = db.Column(db.String(160), nullable=False)
    video_key = db.Column(db.String(32), nullable=True)
    cover_key = db.Column(db.String(80), nullable=True)
    title = db.Column(db.String(120), nullable=False)
    video_path = db.Column(db.String(600), nullable=False)
    cover_path = db.Column(db.String(600), nullable=True)
    description = db.Column(db.Text, nullable=True)
    tags = db.Column(
        db.JSON().with_variant(ARRAY(db.String(20)), "postgresql"),
        nullable=False,
        default=list,
    )
    tid = db.Column(db.Integer, nullable=False, default=207)
    copyright = db.Column(db.Integer, nullable=False, default=1)
    no_reprint = db.Column(db.Integer, nullable=False, default=1)
    dynamic = db.Column(db.String(233), nullable=True)
    neutral_mark = db.Column(
        db.String(80),
        nullable=False,
        default="个人观点，仅供参考",
    )
    is_only_self = db.Column(db.Integer, nullable=False, default=0)
    space_hidden = db.Column(db.Integer, nullable=True)
    creation_statement_id = db.Column(db.Integer, nullable=True)
    credential_id = db.Column(db.String(80), nullable=True)
    credential_name = db.Column(db.String(255), nullable=True)

    charging_enabled = db.Column(db.Boolean, nullable=False, default=False)
    preview_start_time = db.Column(db.Integer, nullable=True)
    preview_end_time = db.Column(db.Integer, nullable=True)
    upower_level_id = db.Column(db.String(80), nullable=True)
    upower_unit_price = db.Column(db.Integer, nullable=True)
    upower_mode = db.Column(db.Integer, nullable=True)
    member_first_exp_time = db.Column(db.Integer, nullable=True)
    delay_time = db.Column(db.BigInteger, nullable=True)
    season_id = db.Column(db.BigInteger, nullable=True)

    aid = db.Column(db.String(32), nullable=True)
    bvid = db.Column(db.String(32), nullable=True)
    queue_repair_count = db.Column(db.Integer, nullable=False, default=0)
    queue_repair_last_at = db.Column(db.DateTime, nullable=True)
    queue_repair_previous_celery_id = db.Column(db.String(50), nullable=True)
    queue_repair_current_celery_id = db.Column(db.String(50), nullable=True)
    queue_repair_queue = db.Column(db.String(80), nullable=True)
    queue_repair_reason = db.Column(db.String(160), nullable=True)
    queue_repair_previous_message_visible = db.Column(db.Boolean, nullable=True)
    canceled_at = db.Column(db.DateTime, nullable=True)
    cancel_reason = db.Column(db.String(160), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    __table_args__ = (
        db.Index(
            "ix_publication_task_bili_uploads_parent_source_created",
            "parent_task_id",
            "parent_feature",
            "source_label",
            db.desc("created_at"),
        ),
    )
