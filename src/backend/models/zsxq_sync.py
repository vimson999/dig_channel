from datetime import datetime
import uuid

from . import db


class ZsxqSyncRun(db.Model):
    __tablename__ = "zsxq_sync_runs"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    group_id = db.Column(db.String(32), nullable=False, index=True)
    group_name = db.Column(db.String(255), nullable=True)
    tab_id = db.Column(db.String(128), nullable=True, index=True)
    tab_label = db.Column(db.String(255), nullable=True)
    mode = db.Column(db.String(20), nullable=False, default="manual")
    status = db.Column(db.String(20), nullable=False, default="running", index=True)
    requested_tabs = db.Column(db.JSON, default=list)
    result = db.Column(db.JSON, default=dict)
    error_message = db.Column(db.Text, nullable=True)
    started_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    completed_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "group_id": self.group_id,
            "group_name": self.group_name,
            "tab_id": self.tab_id,
            "tab_label": self.tab_label,
            "mode": self.mode,
            "status": self.status,
            "requested_tabs": self.requested_tabs or [],
            "result": self.result or {},
            "error_message": self.error_message,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class ZsxqTopic(db.Model):
    __tablename__ = "zsxq_topics"
    __table_args__ = (
        db.UniqueConstraint("group_id", "tab_id", "topic_id", name="uq_zsxq_topics_group_tab_topic"),
    )

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    group_id = db.Column(db.String(32), nullable=False, index=True)
    group_name = db.Column(db.String(255), nullable=True)
    tab_id = db.Column(db.String(128), nullable=False, index=True)
    tab_label = db.Column(db.String(255), nullable=True)
    topic_id = db.Column(db.String(64), nullable=False, index=True)
    topic_uid = db.Column(db.String(64), nullable=True)
    title = db.Column(db.String(500), nullable=True)
    author_name = db.Column(db.String(255), nullable=True)
    content = db.Column(db.Text, nullable=True)
    create_time = db.Column(db.String(64), nullable=True, index=True)
    raw_json_path = db.Column(db.String(600), nullable=True)
    raw_payload = db.Column(db.JSON, default=dict)
    first_sync_run_id = db.Column(db.String(36), nullable=True, index=True)
    last_sync_run_id = db.Column(db.String(36), nullable=True, index=True)
    first_synced_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    last_synced_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "group_id": self.group_id,
            "group_name": self.group_name,
            "tab_id": self.tab_id,
            "tab_label": self.tab_label,
            "topic_id": self.topic_id,
            "topic_uid": self.topic_uid,
            "title": self.title,
            "author_name": self.author_name,
            "content": self.content,
            "create_time": self.create_time,
            "raw_json_path": self.raw_json_path,
            "first_sync_run_id": self.first_sync_run_id,
            "last_sync_run_id": self.last_sync_run_id,
            "first_synced_at": self.first_synced_at.isoformat() if self.first_synced_at else None,
            "last_synced_at": self.last_synced_at.isoformat() if self.last_synced_at else None,
        }


class ZsxqAttachment(db.Model):
    __tablename__ = "zsxq_attachments"
    __table_args__ = (
        db.UniqueConstraint("group_id", "tab_id", "topic_id", "attachment_key", name="uq_zsxq_attachments_group_tab_topic_key"),
    )

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    group_id = db.Column(db.String(32), nullable=False, index=True)
    tab_id = db.Column(db.String(128), nullable=False, index=True)
    topic_id = db.Column(db.String(64), nullable=False, index=True)
    attachment_key = db.Column(db.String(255), nullable=False)
    attachment_type = db.Column(db.String(20), nullable=False, default="file")
    name = db.Column(db.String(500), nullable=True)
    file_id = db.Column(db.String(128), nullable=True, index=True)
    download_count = db.Column(db.Integer, nullable=True)
    local_path = db.Column(db.String(600), nullable=True)
    download_status = db.Column(db.String(20), nullable=False, default="pending", index=True)
    error_message = db.Column(db.Text, nullable=True)
    raw_payload = db.Column(db.JSON, default=dict)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "group_id": self.group_id,
            "tab_id": self.tab_id,
            "topic_id": self.topic_id,
            "attachment_key": self.attachment_key,
            "attachment_type": self.attachment_type,
            "name": self.name,
            "file_id": self.file_id,
            "download_count": self.download_count,
            "local_path": self.local_path,
            "download_status": self.download_status,
            "error_message": self.error_message,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class ZsxqSyncCursor(db.Model):
    __tablename__ = "zsxq_sync_cursors"
    __table_args__ = (
        db.UniqueConstraint("group_id", "tab_id", name="uq_zsxq_sync_cursors_group_tab"),
    )

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    group_id = db.Column(db.String(32), nullable=False, index=True)
    tab_id = db.Column(db.String(128), nullable=False, index=True)
    tab_label = db.Column(db.String(255), nullable=True)
    newest_topic_id = db.Column(db.String(64), nullable=True)
    newest_create_time = db.Column(db.String(64), nullable=True)
    last_end_time = db.Column(db.String(64), nullable=True)
    total_topics = db.Column(db.Integer, default=0)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "group_id": self.group_id,
            "tab_id": self.tab_id,
            "tab_label": self.tab_label,
            "newest_topic_id": self.newest_topic_id,
            "newest_create_time": self.newest_create_time,
            "last_end_time": self.last_end_time,
            "total_topics": self.total_topics,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
