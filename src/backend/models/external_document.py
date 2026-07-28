from datetime import datetime
import uuid

from . import db


def _new_id():
    return str(uuid.uuid4())


class ExternalDocument(db.Model):
    """Fetched external document content and derived artifacts.

    One row represents one canonical external URL. Topic-level ownership lives
    in ZsxqTopicExternalDocument so a popular link can be reused across topics.
    """

    __tablename__ = "external_documents"
    __table_args__ = (
        db.UniqueConstraint("url_hash", name="uq_external_documents_url_hash"),
        db.Index("ix_external_documents_provider_status", "provider", "fetch_status", "updated_at"),
        db.Index("ix_external_documents_summary_status", "summary_status", "updated_at"),
        db.Index("ix_external_documents_tts_status", "tts_status", "updated_at"),
        db.Index("ix_external_documents_content_hash", "content_hash"),
    )

    id = db.Column(db.String(36), primary_key=True, default=_new_id)
    provider = db.Column(db.String(40), nullable=False, index=True)
    document_type = db.Column(db.String(40), nullable=True, index=True)
    source_url = db.Column(db.Text, nullable=False)
    canonical_url = db.Column(db.Text, nullable=False)
    url_hash = db.Column(db.String(64), nullable=False)
    title = db.Column(db.String(500), nullable=True)
    author_name = db.Column(db.String(255), nullable=True)
    content_text = db.Column(db.Text, nullable=True)
    content_html = db.Column(db.Text, nullable=True)
    content_hash = db.Column(db.String(64), nullable=True)
    content_chars = db.Column(db.Integer, nullable=False, default=0)
    fetch_status = db.Column(db.String(32), nullable=False, default="pending", index=True)
    fetch_error = db.Column(db.Text, nullable=True)
    fetched_at = db.Column(db.DateTime, nullable=True)
    fetch_credential_id = db.Column(db.String(36), nullable=True, index=True)
    raw_payload = db.Column(db.JSON, nullable=False, default=dict)
    summary_status = db.Column(db.String(32), nullable=False, default="pending")
    summary_text = db.Column(db.Text, nullable=True)
    summary_model = db.Column(db.String(120), nullable=True)
    summary_error = db.Column(db.Text, nullable=True)
    summarized_at = db.Column(db.DateTime, nullable=True)
    tts_status = db.Column(db.String(32), nullable=False, default="not_submitted")
    tts_task_id = db.Column(db.String(36), nullable=True, index=True)
    tts_audio_path = db.Column(db.String(600), nullable=True)
    tts_error = db.Column(db.Text, nullable=True)
    tts_submitted_at = db.Column(db.DateTime, nullable=True)
    tts_completed_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    def compact_dict(self):
        return {
            "id": self.id,
            "provider": self.provider,
            "document_type": self.document_type,
            "source_url": self.source_url,
            "canonical_url": self.canonical_url,
            "url_hash": self.url_hash,
            "title": self.title,
            "content_chars": self.content_chars or 0,
            "fetch_status": self.fetch_status,
            "fetch_error": self.fetch_error,
            "fetched_at": self.fetched_at.isoformat() if self.fetched_at else None,
            "summary_status": self.summary_status,
            "summary_text": self.summary_text,
            "summary_model": self.summary_model,
            "summary_error": self.summary_error,
            "summarized_at": self.summarized_at.isoformat() if self.summarized_at else None,
            "tts_status": self.tts_status,
            "tts_task_id": self.tts_task_id,
            "tts_audio_path": self.tts_audio_path,
            "tts_error": self.tts_error,
            "tts_submitted_at": self.tts_submitted_at.isoformat() if self.tts_submitted_at else None,
            "tts_completed_at": self.tts_completed_at.isoformat() if self.tts_completed_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def detail_dict(self):
        payload = self.compact_dict()
        payload.update({
            "author_name": self.author_name,
            "content_text": self.content_text or "",
            "content_html": self.content_html,
            "content_hash": self.content_hash,
            "raw_payload": self.raw_payload or {},
            "fetch_credential_id": self.fetch_credential_id,
        })
        return payload


class ZsxqTopicExternalDocument(db.Model):
    """Join table from cached ZSXQ topic to an external document."""

    __tablename__ = "zsxq_topic_external_documents"
    __table_args__ = (
        db.UniqueConstraint(
            "group_id",
            "tab_id",
            "topic_id",
            "url_hash",
            name="uq_zsxq_topic_external_documents_topic_url",
        ),
        db.Index("ix_zsxq_topic_external_documents_topic", "group_id", "tab_id", "topic_id"),
        db.Index("ix_zsxq_topic_external_documents_group_provider", "group_id", "provider", "updated_at"),
        db.Index("ix_zsxq_topic_external_documents_document", "external_document_id"),
    )

    id = db.Column(db.String(36), primary_key=True, default=_new_id)
    group_id = db.Column(db.String(32), nullable=False, index=True)
    group_name = db.Column(db.String(255), nullable=True)
    tab_id = db.Column(db.String(128), nullable=False, index=True)
    tab_label = db.Column(db.String(255), nullable=True)
    topic_id = db.Column(db.String(64), nullable=False, index=True)
    topic_title = db.Column(db.String(500), nullable=True)
    provider = db.Column(db.String(40), nullable=False, index=True)
    external_document_id = db.Column(
        db.String(36),
        db.ForeignKey("external_documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_url = db.Column(db.Text, nullable=False)
    canonical_url = db.Column(db.Text, nullable=False)
    url_hash = db.Column(db.String(64), nullable=False, index=True)
    link_text = db.Column(db.String(500), nullable=True)
    link_source = db.Column(db.String(80), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    document = db.relationship("ExternalDocument", backref=db.backref("topic_links", lazy=True))

    def to_dict(self, include_document=True):
        payload = {
            "id": self.id,
            "group_id": self.group_id,
            "group_name": self.group_name,
            "tab_id": self.tab_id,
            "tab_label": self.tab_label,
            "topic_id": self.topic_id,
            "topic_title": self.topic_title,
            "provider": self.provider,
            "external_document_id": self.external_document_id,
            "source_url": self.source_url,
            "canonical_url": self.canonical_url,
            "url_hash": self.url_hash,
            "link_text": self.link_text,
            "link_source": self.link_source,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
        if include_document and self.document:
            payload["document"] = self.document.compact_dict()
        return payload
