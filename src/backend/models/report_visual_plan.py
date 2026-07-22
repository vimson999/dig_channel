from datetime import datetime
import uuid

from . import db


class ReportVisualPlanOutput(db.Model):
    __tablename__ = "report_visual_plan_outputs"

    task_id = db.Column(db.String(36), db.ForeignKey("publication_tasks.id", ondelete="CASCADE"), primary_key=True)
    source_task_id = db.Column(db.String(36), nullable=False, index=True)
    schema_version = db.Column(db.String(64), nullable=False)
    source_script_hash = db.Column(db.String(64), nullable=False, index=True)
    source_script_chars = db.Column(db.Integer, nullable=False, default=0)
    covered_script_chars = db.Column(db.Integer, nullable=False, default=0)
    segment_count = db.Column(db.Integer, nullable=False, default=0)
    visual_scheme_key = db.Column(db.String(64), nullable=False, default="stick_figure", index=True)
    visual_scheme_name = db.Column(db.String(80), nullable=False, default="白板火柴人")
    gemini_url = db.Column(db.String(600), nullable=False)
    image_gemini_url = db.Column(db.String(600), nullable=False, default="https://gemini.google.com/app")
    image_style_profile = db.Column(db.String(80), nullable=False, default="whiteboard_stick_figure_zh_v1")
    gemini_output_path = db.Column(db.String(600), nullable=True)
    normalized_output_path = db.Column(db.String(600), nullable=True)
    normalizer_provider = db.Column(db.String(40), nullable=True)
    normalizer_model = db.Column(db.String(120), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class ReportVisualPlanSegment(db.Model):
    __tablename__ = "report_visual_plan_segments"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    task_id = db.Column(db.String(36), db.ForeignKey("publication_tasks.id", ondelete="CASCADE"), nullable=False)
    sequence = db.Column(db.Integer, nullable=False)
    scene_title = db.Column(db.String(240), nullable=True)
    script_text = db.Column(db.Text, nullable=False)
    visual_prompt = db.Column(db.Text, nullable=False)
    estimated_seconds = db.Column(db.Float, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint("task_id", "sequence", name="uq_report_visual_plan_segment_task_sequence"),
        db.Index("ix_report_visual_plan_segments_task_sequence", "task_id", "sequence"),
    )
