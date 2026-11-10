"""Persisted analysis records for the independent voyage analysis library."""
from datetime import datetime

from . import db


class VoyageAnalysis(db.Model):
    __tablename__ = 'voyage_analyses'

    id = db.Column(db.String(200), primary_key=True)
    activity_id = db.Column(db.String(32), nullable=False, unique=True)
    title = db.Column(db.String(500), nullable=False)
    analysis_date = db.Column(db.Date, nullable=True)
    status = db.Column(db.String(100), nullable=False, default='初步分析')
    summary = db.Column(db.Text, nullable=False, default='')
    content_markdown = db.Column(db.Text, nullable=False)
    sort_order = db.Column(db.Integer, nullable=False, default=0, index=True)
    content_sha256 = db.Column(db.String(64), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
