from datetime import datetime
import uuid

from . import db


class ReportStickFigureSceneAsset(db.Model):
    __tablename__ = "report_stick_figure_scene_assets"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    workflow_task_id = db.Column(db.String(36), db.ForeignKey("publication_tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    storyboard_task_id = db.Column(db.String(36), db.ForeignKey("publication_tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    segment_id = db.Column(db.String(36), db.ForeignKey("report_visual_plan_segments.id", ondelete="CASCADE"), nullable=False, index=True)
    sequence = db.Column(db.Integer, nullable=False)
    script_text_hash = db.Column(db.String(64), nullable=False)
    visual_prompt_hash = db.Column(db.String(64), nullable=False)

    image_task_id = db.Column(db.String(36), nullable=True, index=True)
    image_status = db.Column(db.String(32), nullable=False, default="pending")
    image_progress = db.Column(db.Integer, nullable=False, default=0)
    image_attempt = db.Column(db.Integer, nullable=False, default=0)
    image_path = db.Column(db.String(600), nullable=True)
    image_bytes = db.Column(db.BigInteger, nullable=True)
    image_width = db.Column(db.Integer, nullable=True)
    image_height = db.Column(db.Integer, nullable=True)
    image_error = db.Column(db.Text, nullable=True)

    tts_task_id = db.Column(db.String(36), nullable=True, index=True)
    tts_status = db.Column(db.String(32), nullable=False, default="pending")
    tts_progress = db.Column(db.Integer, nullable=False, default=0)
    tts_attempt = db.Column(db.Integer, nullable=False, default=0)
    audio_path = db.Column(db.String(600), nullable=True)
    audio_bytes = db.Column(db.BigInteger, nullable=True)
    audio_seconds = db.Column(db.Float, nullable=True)
    tts_error = db.Column(db.Text, nullable=True)

    # 整篇音频复用模式下，每个正文分镜直接引用同一音轨的时间区间，
    # 不复制 WAV，也不伪造逐段 TTS 产物。
    timeline_start_seconds = db.Column(db.Float, nullable=True)
    timeline_end_seconds = db.Column(db.Float, nullable=True)
    timeline_confidence = db.Column(db.Float, nullable=True)
    timeline_source = db.Column(db.String(32), nullable=True)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint(
            "workflow_task_id",
            "storyboard_task_id",
            "sequence",
            name="uq_report_stick_scene_workflow_storyboard_sequence",
        ),
        db.Index(
            "ix_report_stick_scene_workflow_sequence",
            "workflow_task_id",
            "sequence",
        ),
    )


class ReportStickFigureAudioTrack(db.Model):
    """火柴人工作流复用的整篇音轨及其对齐摘要。"""

    __tablename__ = "report_stick_figure_audio_tracks"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    workflow_task_id = db.Column(
        db.String(36),
        db.ForeignKey("publication_tasks.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    storyboard_task_id = db.Column(
        db.String(36),
        db.ForeignKey("publication_tasks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_task_id = db.Column(
        db.String(36),
        db.ForeignKey("publication_tasks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    alignment_task_id = db.Column(db.String(36), nullable=True, index=True)
    mode = db.Column(db.String(32), nullable=False, default="whole_audio")
    status = db.Column(db.String(32), nullable=False, default="available", index=True)
    audio_path = db.Column(db.String(600), nullable=False)
    audio_bytes = db.Column(db.BigInteger, nullable=True)
    audio_seconds = db.Column(db.Float, nullable=True)
    source_script_hash = db.Column(db.String(64), nullable=True)
    storyboard_script_hash = db.Column(db.String(64), nullable=True)
    asr_cache_key = db.Column(db.String(128), nullable=True)
    asr_model = db.Column(db.String(40), nullable=True)
    aligned_scene_count = db.Column(db.Integer, nullable=False, default=0)
    average_confidence = db.Column(db.Float, nullable=True)
    minimum_confidence = db.Column(db.Float, nullable=True)
    manifest_path = db.Column(db.String(600), nullable=True)
    error = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
