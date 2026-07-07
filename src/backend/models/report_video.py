from datetime import datetime
import uuid

from sqlalchemy import inspect

from . import db


class ReportVideoTask(db.Model):
    __tablename__ = "report_video_tasks"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    task_name = db.Column(db.String(255), nullable=False)
    video_type = db.Column(db.String(40), nullable=False, default="daily_digest")
    default_segment_count = db.Column(db.Integer, nullable=False, default=4)
    output_ratios = db.Column(db.JSON, default=lambda: ["16:9", "9:16"])
    style = db.Column(db.String(60), nullable=False, default="professional_finance")
    background_music_path = db.Column(db.String(600), nullable=True)
    generate_subtitles = db.Column(db.Boolean, nullable=False, default=True)
    status = db.Column(db.String(30), nullable=False, default="created", index=True)
    result = db.Column(db.JSON, default=dict)
    error_message = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    reports = db.relationship(
        "ReportVideoReport",
        backref="task",
        cascade="all, delete-orphan",
        lazy=True,
        order_by="ReportVideoReport.report_code.asc()",
    )
    audios = db.relationship(
        "ReportVideoAudio",
        backref="task",
        cascade="all, delete-orphan",
        lazy=True,
        order_by="ReportVideoAudio.created_at.asc()",
    )
    outputs = db.relationship(
        "ReportVideoOutput",
        backref="task",
        cascade="all, delete-orphan",
        lazy=True,
        order_by="ReportVideoOutput.created_at.desc()",
    )
    render_previews = db.relationship(
        "ReportVideoRenderPreview",
        backref="task",
        cascade="all, delete-orphan",
        lazy=True,
        order_by="ReportVideoRenderPreview.created_at.desc()",
    )

    def to_dict(self, include_reports=False, include_ai=False):
        reports_loaded = "reports" not in inspect(self).unloaded
        report_count = len(self.reports) if include_reports or reports_loaded else None
        data = {
            "id": self.id,
            "task_name": self.task_name,
            "video_type": self.video_type,
            "default_segment_count": self.default_segment_count,
            "output_ratios": self.output_ratios or [],
            "style": self.style,
            "background_music_path": self.background_music_path,
            "generate_subtitles": self.generate_subtitles,
            "status": self.status,
            "result": self.result or {},
            "error_message": self.error_message,
            "report_count": report_count,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
        if include_reports:
            data["reports"] = [report.to_dict(include_ai=include_ai) for report in self.reports]
            data["audios"] = [audio.to_dict() for audio in self.audios]
            data["outputs"] = [output.to_dict() for output in self.outputs]
            data["render_previews"] = [preview.to_dict() for preview in self.render_previews]
        return data


class ReportVideoReport(db.Model):
    __tablename__ = "report_video_reports"
    __table_args__ = (
        db.UniqueConstraint("task_id", "report_code", name="uq_report_video_reports_task_code"),
    )

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    task_id = db.Column(db.String(36), db.ForeignKey("report_video_tasks.id"), nullable=False, index=True)
    report_code = db.Column(db.String(8), nullable=False)
    file_name = db.Column(db.String(500), nullable=False)
    file_path = db.Column(db.String(600), nullable=False)
    page_count = db.Column(db.Integer, nullable=True)
    raw_text_path = db.Column(db.String(600), nullable=True)
    cleaned_text_path = db.Column(db.String(600), nullable=True)
    raw_text_chars = db.Column(db.Integer, default=0)
    cleaned_text_chars = db.Column(db.Integer, default=0)
    is_scanned = db.Column(db.Boolean, nullable=False, default=False)
    parse_status = db.Column(db.String(30), nullable=False, default="uploaded", index=True)
    ai_status = db.Column(db.String(30), nullable=False, default="ai_pending", index=True)
    error_message = db.Column(db.Text, nullable=True)
    raw_meta = db.Column(db.JSON, default=dict)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    analysis = db.relationship(
        "ReportVideoAnalysis",
        backref="report",
        cascade="all, delete-orphan",
        uselist=False,
        lazy=True,
    )
    mindmap = db.relationship(
        "ReportVideoMindmap",
        backref="report",
        cascade="all, delete-orphan",
        uselist=False,
        lazy=True,
    )
    segments = db.relationship(
        "ReportVideoSegment",
        backref="report",
        cascade="all, delete-orphan",
        lazy=True,
        order_by="ReportVideoSegment.segment_index.asc()",
    )

    def to_dict(self, include_ai=False):
        data = {
            "id": self.id,
            "task_id": self.task_id,
            "report_code": self.report_code,
            "file_name": self.file_name,
            "file_path": self.file_path,
            "page_count": self.page_count,
            "raw_text_path": self.raw_text_path,
            "cleaned_text_path": self.cleaned_text_path,
            "raw_text_chars": self.raw_text_chars,
            "cleaned_text_chars": self.cleaned_text_chars,
            "is_scanned": self.is_scanned,
            "parse_status": self.parse_status,
            "ai_status": self.ai_status,
            "error_message": self.error_message,
            "raw_meta": self.raw_meta or {},
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
        if include_ai:
            data["analysis"] = self.analysis.to_dict() if self.analysis else None
            data["mindmap"] = self.mindmap.to_dict() if self.mindmap else None
            data["segments"] = [segment.to_dict() for segment in self.segments]
        return data


class ReportVideoAnalysis(db.Model):
    __tablename__ = "report_video_analyses"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    report_id = db.Column(db.String(36), db.ForeignKey("report_video_reports.id"), nullable=False, unique=True, index=True)
    report_title = db.Column(db.String(500), nullable=True)
    institution = db.Column(db.String(255), nullable=True)
    theme = db.Column(db.String(80), nullable=True)
    core_view = db.Column(db.Text, nullable=True)
    key_points = db.Column(db.JSON, default=list)
    video_value_score = db.Column(db.Integer, nullable=True)
    recommended_video_type = db.Column(db.String(60), nullable=True)
    recommended_duration_sec = db.Column(db.Integer, nullable=True)
    reason = db.Column(db.Text, nullable=True)
    raw_payload = db.Column(db.JSON, default=dict)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "report_id": self.report_id,
            "report_title": self.report_title,
            "institution": self.institution,
            "theme": self.theme,
            "core_view": self.core_view,
            "key_points": self.key_points or [],
            "video_value_score": self.video_value_score,
            "recommended_video_type": self.recommended_video_type,
            "recommended_duration_sec": self.recommended_duration_sec,
            "reason": self.reason,
            "raw_payload": self.raw_payload or {},
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class ReportVideoSegment(db.Model):
    __tablename__ = "report_video_segments"
    __table_args__ = (
        db.UniqueConstraint("task_id", "segment_id", name="uq_report_video_segments_task_segment"),
    )

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    task_id = db.Column(db.String(36), db.ForeignKey("report_video_tasks.id"), nullable=False, index=True)
    report_id = db.Column(db.String(36), db.ForeignKey("report_video_reports.id"), nullable=False, index=True)
    segment_id = db.Column(db.String(32), nullable=False)
    segment_index = db.Column(db.Integer, nullable=False, default=1)
    segment_type = db.Column(db.String(60), nullable=True)
    segment_title = db.Column(db.String(255), nullable=True)
    voice_text = db.Column(db.Text, nullable=True)
    estimated_duration_sec = db.Column(db.Integer, nullable=True)
    focus_node_id = db.Column(db.String(128), nullable=True, index=True)
    audio_filename = db.Column(db.String(255), nullable=True)
    audio_path = db.Column(db.String(600), nullable=True)
    actual_duration_sec = db.Column(db.Float, nullable=True)
    status = db.Column(db.String(30), nullable=False, default="draft", index=True)
    raw_payload = db.Column(db.JSON, default=dict)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "task_id": self.task_id,
            "report_id": self.report_id,
            "segment_id": self.segment_id,
            "segment_index": self.segment_index,
            "segment_type": self.segment_type,
            "segment_title": self.segment_title,
            "voice_text": self.voice_text,
            "estimated_duration_sec": self.estimated_duration_sec,
            "focus_node_id": self.focus_node_id,
            "audio_filename": self.audio_filename,
            "audio_path": self.audio_path,
            "actual_duration_sec": self.actual_duration_sec,
            "status": self.status,
            "raw_payload": self.raw_payload or {},
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class ReportVideoMindmap(db.Model):
    __tablename__ = "report_video_mindmaps"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    report_id = db.Column(db.String(36), db.ForeignKey("report_video_reports.id"), nullable=False, unique=True, index=True)
    center = db.Column(db.String(255), nullable=True)
    nodes = db.Column(db.JSON, default=list)
    raw_payload = db.Column(db.JSON, default=dict)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "report_id": self.report_id,
            "center": self.center,
            "nodes": self.nodes or [],
            "raw_payload": self.raw_payload or {},
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class ReportVideoAudio(db.Model):
    __tablename__ = "report_video_audios"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    task_id = db.Column(db.String(36), db.ForeignKey("report_video_tasks.id"), nullable=False, index=True)
    report_id = db.Column(db.String(36), db.ForeignKey("report_video_reports.id"), nullable=True, index=True)
    segment_id = db.Column(db.String(32), nullable=True, index=True)
    file_name = db.Column(db.String(255), nullable=False)
    file_path = db.Column(db.String(600), nullable=False)
    duration_sec = db.Column(db.Float, nullable=True)
    format = db.Column(db.String(20), nullable=True)
    match_status = db.Column(db.String(30), nullable=False, default="pending", index=True)
    error_message = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "task_id": self.task_id,
            "report_id": self.report_id,
            "segment_id": self.segment_id,
            "file_name": self.file_name,
            "file_path": self.file_path,
            "duration_sec": self.duration_sec,
            "format": self.format,
            "match_status": self.match_status,
            "error_message": self.error_message,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class ReportVideoOutput(db.Model):
    __tablename__ = "report_video_outputs"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    task_id = db.Column(db.String(36), db.ForeignKey("report_video_tasks.id"), nullable=False, index=True)
    report_id = db.Column(db.String(36), db.ForeignKey("report_video_reports.id"), nullable=True, index=True)
    ratio = db.Column(db.String(10), nullable=False)
    video_type = db.Column(db.String(40), nullable=False, default="single_report")
    duration_sec = db.Column(db.Float, nullable=True)
    file_path = db.Column(db.String(600), nullable=True)
    subtitle_path = db.Column(db.String(600), nullable=True)
    status = db.Column(db.String(30), nullable=False, default="pending", index=True)
    error_message = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "task_id": self.task_id,
            "report_id": self.report_id,
            "ratio": self.ratio,
            "video_type": self.video_type,
            "duration_sec": self.duration_sec,
            "file_path": self.file_path,
            "subtitle_path": self.subtitle_path,
            "status": self.status,
            "error_message": self.error_message,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class ReportVideoRenderPreview(db.Model):
    __tablename__ = "report_video_render_previews"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    task_id = db.Column(db.String(36), db.ForeignKey("report_video_tasks.id"), nullable=False, index=True)
    report_id = db.Column(db.String(36), db.ForeignKey("report_video_reports.id"), nullable=True, index=True)
    preview_type = db.Column(db.String(30), nullable=False, index=True)
    ratio = db.Column(db.String(10), nullable=False, index=True)
    status = db.Column(db.String(30), nullable=False, default="pending", index=True)
    duration_sec = db.Column(db.Float, nullable=True)
    file_path = db.Column(db.String(600), nullable=True)
    file_paths = db.Column(db.JSON, default=list)
    segment_ids = db.Column(db.JSON, default=list)
    renderer_version = db.Column(db.String(80), nullable=True)
    celery_task_id = db.Column(db.String(80), nullable=True, index=True)
    meta = db.Column(db.JSON, default=dict)
    approved_at = db.Column(db.DateTime, nullable=True)
    rejected_at = db.Column(db.DateTime, nullable=True)
    reject_reason = db.Column(db.Text, nullable=True)
    error_message = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "task_id": self.task_id,
            "report_id": self.report_id,
            "preview_type": self.preview_type,
            "ratio": self.ratio,
            "status": self.status,
            "duration_sec": self.duration_sec,
            "file_path": self.file_path,
            "file_paths": self.file_paths or [],
            "segment_ids": self.segment_ids or [],
            "renderer_version": self.renderer_version,
            "celery_task_id": self.celery_task_id,
            "meta": self.meta or {},
            "approved_at": self.approved_at.isoformat() if self.approved_at else None,
            "rejected_at": self.rejected_at.isoformat() if self.rejected_at else None,
            "reject_reason": self.reject_reason,
            "error_message": self.error_message,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
