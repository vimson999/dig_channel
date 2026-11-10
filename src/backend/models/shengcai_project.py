"""Independent project-list snapshots; deliberately separate from ZSXQ topics."""
import uuid
from datetime import datetime
from . import db


class ShengcaiProject(db.Model):
    __tablename__ = 'shengcai_projects'
    id = db.Column(db.String(32), primary_key=True)
    name = db.Column(db.String(500), nullable=False)
    summary = db.Column(db.Text, nullable=True)
    raw_payload = db.Column(db.JSON, nullable=False)
    payload_sha256 = db.Column(db.String(64), nullable=False)
    first_imported_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    last_seen_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class ShengcaiProjectImport(db.Model):
    __tablename__ = 'shengcai_project_imports'
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    source_sha256 = db.Column(db.String(64), nullable=False, index=True)
    source_filename = db.Column(db.String(255), nullable=False)
    item_count = db.Column(db.Integer, nullable=False)
    source_total = db.Column(db.Integer, nullable=False)
    new_count = db.Column(db.Integer, nullable=False)
    updated_count = db.Column(db.Integer, nullable=False)
    unchanged_count = db.Column(db.Integer, nullable=False)
    imported_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
