from datetime import datetime
import uuid

from . import db


class ZsxqWorkflowRun(db.Model):
    __tablename__ = "zsxq_workflow_runs"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    batch_id = db.Column(db.String(64), nullable=True, index=True)
    group_id = db.Column(db.String(32), nullable=False, index=True)
    group_name = db.Column(db.String(255), nullable=True)
    trigger = db.Column(db.String(64), nullable=True, index=True)
    status = db.Column(db.String(20), nullable=False, default="queued", index=True)
    current_stage = db.Column(db.String(20), nullable=False, default="init", index=True)
    round = db.Column(db.Integer, nullable=True)
    max_rounds = db.Column(db.Integer, nullable=True)
    config_snapshot = db.Column(db.JSON, default=dict)
    stage_results = db.Column(db.JSON, default=dict)
    sync_run_id = db.Column(db.String(36), nullable=True, index=True)
    clean_task_ids = db.Column(db.JSON, default=list)
    publish_task_ids = db.Column(db.JSON, default=list)
    error_message = db.Column(db.Text, nullable=True)
    started_at = db.Column(db.DateTime, nullable=True)
    completed_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "batch_id": self.batch_id,
            "group_id": self.group_id,
            "group_name": self.group_name,
            "trigger": self.trigger,
            "status": self.status,
            "current_stage": self.current_stage,
            "round": self.round,
            "max_rounds": self.max_rounds,
            "config_snapshot": self.config_snapshot or {},
            "stage_results": self.stage_results or {},
            "sync_run_id": self.sync_run_id,
            "clean_task_ids": self.clean_task_ids or [],
            "publish_task_ids": self.publish_task_ids or [],
            "error_message": self.error_message,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
