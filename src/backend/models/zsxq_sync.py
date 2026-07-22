from datetime import datetime
import uuid

from sqlalchemy import event, inspect as sqlalchemy_inspect

from . import db


class ZsxqSyncRun(db.Model):
    __tablename__ = "zsxq_sync_runs"
    __table_args__ = (
        db.Index("ix_zsxq_sync_runs_group_status_updated", "group_id", "status", "updated_at"),
        db.Index("ix_zsxq_sync_runs_status_updated", "status", "updated_at"),
    )

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    group_id = db.Column(db.String(32), nullable=False, index=True)
    group_name = db.Column(db.String(255), nullable=True)
    tab_id = db.Column(db.String(128), nullable=True, index=True)
    tab_label = db.Column(db.String(255), nullable=True)
    mode = db.Column(db.String(20), nullable=False, default="manual")
    status = db.Column(db.String(20), nullable=False, default="running", index=True)
    requested_tabs = db.Column(db.JSON, default=list)
    result = db.Column(db.JSON, default=dict)
    system_batch_id = db.Column(db.String(80), nullable=True)
    system_attempt_id = db.Column(db.String(80), nullable=True)
    system_round = db.Column(db.Integer, nullable=True)
    system_max_rounds = db.Column(db.Integer, nullable=True)
    system_trigger = db.Column(db.String(120), nullable=True)
    system_queued_at = db.Column(db.String(64), nullable=True)
    system_max_download_files = db.Column(db.Integer, nullable=True)
    topics_fetched = db.Column(db.Integer, nullable=True)
    topics_new = db.Column(db.Integer, nullable=True)
    topics_updated = db.Column(db.Integer, nullable=True)
    topics_existing = db.Column(db.Integer, nullable=True)
    attachments_seen = db.Column(db.Integer, nullable=True)
    attachments_downloaded = db.Column(db.Integer, nullable=True)
    attachments_failed = db.Column(db.Integer, nullable=True)
    attachments_skipped = db.Column(db.Integer, nullable=True)
    download_limit_reached = db.Column(db.Boolean, nullable=True)
    download_ledger_complete = db.Column(db.Boolean, nullable=False, default=False)
    error_message = db.Column(db.Text, nullable=True)
    started_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    completed_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.sync_projection_fields()

    def sync_projection_fields(self):
        result = _loaded_model_attr(self, "result")
        if result is None:
            return self
        result = result if isinstance(result, dict) else {}
        partial = result.get("partial") if isinstance(result.get("partial"), dict) else {}
        meta = result.get("system_sync") if isinstance(result.get("system_sync"), dict) else {}

        self.system_batch_id = _text(meta.get("batch_id"), 80)
        self.system_attempt_id = _text(meta.get("attempt_id"), 80)
        self.system_round = _int_or_none(meta.get("round"))
        self.system_max_rounds = _int_or_none(meta.get("max_rounds"))
        self.system_trigger = _text(meta.get("trigger"), 120)
        self.system_queued_at = _text(meta.get("queued_at"), 64)
        self.system_max_download_files = _int_or_none(meta.get("max_download_files"))

        self.topics_fetched = _int_or_zero(result.get("topics_fetched", partial.get("topics_fetched")))
        self.topics_new = _int_or_zero(result.get("topics_new", partial.get("topics_new")))
        self.topics_updated = _int_or_zero(result.get("topics_updated", partial.get("topics_updated")))
        self.topics_existing = _int_or_zero(result.get("topics_existing", partial.get("topics_existing")))
        self.attachments_seen = _int_or_zero(result.get("attachments_seen", partial.get("attachments_seen")))
        self.attachments_downloaded = _int_or_zero(result.get("attachments_downloaded", partial.get("attachments_downloaded")))
        self.attachments_failed = _int_or_zero(result.get("attachments_failed", partial.get("attachments_failed")))
        self.attachments_skipped = _int_or_zero(result.get("attachments_skipped", partial.get("attachments_skipped")))
        self.download_limit_reached = _bool_or_none(result.get("download_limit_reached", partial.get("download_limit_reached")))
        return self

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
            "system_batch_id": self.system_batch_id,
            "system_attempt_id": self.system_attempt_id,
            "system_round": self.system_round,
            "system_max_rounds": self.system_max_rounds,
            "system_trigger": self.system_trigger,
            "system_queued_at": self.system_queued_at,
            "system_max_download_files": self.system_max_download_files,
            "topics_fetched": self.topics_fetched or 0,
            "topics_new": self.topics_new or 0,
            "topics_updated": self.topics_updated or 0,
            "topics_existing": self.topics_existing or 0,
            "attachments_seen": self.attachments_seen or 0,
            "attachments_downloaded": self.attachments_downloaded or 0,
            "attachments_failed": self.attachments_failed or 0,
            "attachments_skipped": self.attachments_skipped or 0,
            "download_limit_reached": bool(self.download_limit_reached),
            "download_ledger_complete": bool(self.download_ledger_complete),
            "error_message": self.error_message,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class ZsxqSyncRunDownload(db.Model):
    """Structured run-to-file ledger for downstream cleaning and asset scans."""

    __tablename__ = "zsxq_sync_run_downloads"
    __table_args__ = (
        db.Index("ix_zsxq_sync_run_downloads_run_ext", "run_id", "file_ext"),
        db.Index("ix_zsxq_sync_run_downloads_attachment", "attachment_id"),
    )

    run_id = db.Column(
        db.String(36),
        db.ForeignKey("zsxq_sync_runs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    attachment_id = db.Column(
        db.String(36),
        db.ForeignKey("zsxq_attachments.id", ondelete="CASCADE"),
        primary_key=True,
    )
    group_id = db.Column(db.String(32), nullable=False)
    tab_id = db.Column(db.String(128), nullable=False)
    topic_id = db.Column(db.String(64), nullable=False)
    name = db.Column(db.String(500), nullable=True)
    local_path = db.Column(db.String(600), nullable=False)
    file_ext = db.Column(db.String(20), nullable=True)
    downloaded_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    def to_file_dict(self):
        return {
            "path": self.local_path,
            "name": self.name,
            "topic_id": self.topic_id,
            "tab_id": self.tab_id,
            "attachment_id": self.attachment_id,
        }


class ZsxqTopic(db.Model):
    __tablename__ = "zsxq_topics"
    __table_args__ = (
        db.UniqueConstraint("group_id", "tab_id", "topic_id", name="uq_zsxq_topics_group_tab_topic"),
        db.Index("ix_zsxq_topics_group_tab_create_synced_desc", "group_id", "tab_id", db.text("create_time DESC"), db.text("last_synced_at DESC")),
        db.Index("ix_zsxq_topics_group_create_synced_desc", "group_id", db.text("create_time DESC"), db.text("last_synced_at DESC")),
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
    topic_type = db.Column(db.String(32), nullable=True, index=True)
    question_text = db.Column(db.Text, nullable=True)
    answer_text = db.Column(db.Text, nullable=True)
    article_title = db.Column(db.String(500), nullable=True)
    article_excerpt = db.Column(db.Text, nullable=True)
    files_count = db.Column(db.Integer, nullable=False, default=0)
    images_count = db.Column(db.Integer, nullable=False, default=0)
    create_time = db.Column(db.String(64), nullable=True, index=True)
    raw_json_path = db.Column(db.String(600), nullable=True)
    raw_payload = db.Column(db.JSON, default=dict)
    first_sync_run_id = db.Column(db.String(36), nullable=True, index=True)
    last_sync_run_id = db.Column(db.String(36), nullable=True, index=True)
    first_synced_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    last_synced_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.sync_projection_fields()

    def sync_projection_fields(self):
        payload = _loaded_model_attr(self, "raw_payload")
        if payload is None:
            return self
        payload = payload if isinstance(payload, dict) else {}
        question = payload.get("question") if isinstance(payload.get("question"), dict) else {}
        answer = payload.get("answer") if isinstance(payload.get("answer"), dict) else {}
        talk = payload.get("talk") if isinstance(payload.get("talk"), dict) else {}
        article = talk.get("article") if isinstance(talk.get("article"), dict) else payload.get("article")
        article = article if isinstance(article, dict) else {}

        question_text = _normalize_topic_text(question.get("text"))
        answer_text = _normalize_topic_text(answer.get("text"))
        article_content = _normalize_topic_text(article.get("article_content"))
        article_title = _text(article.get("title"), 500)
        talk_text = _normalize_topic_text(talk.get("text"))

        if question_text or answer_text:
            self.topic_type = "question_answer"
        elif article:
            self.topic_type = "article"
        else:
            self.topic_type = "talk"

        self.question_text = question_text or None
        self.answer_text = answer_text or None
        self.article_title = article_title
        self.article_excerpt = _text(article_content or article_title, 1000)
        if question_text and answer_text:
            self.content = f"提问：{question_text}\n\n回答：{answer_text}"
        elif question_text or answer_text:
            self.content = question_text or answer_text
        elif talk_text:
            self.content = talk_text
        elif article_content or article_title:
            self.content = article_content or article_title

        self.files_count = len(_topic_files(payload))
        self.images_count = len(_topic_images(payload))
        return self

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
            "topic_type": self.topic_type,
            "question_text": self.question_text,
            "answer_text": self.answer_text,
            "article_title": self.article_title,
            "article_excerpt": self.article_excerpt,
            "files_count": self.files_count or 0,
            "images_count": self.images_count or 0,
            "create_time": self.create_time,
            "raw_json_path": self.raw_json_path,
            "first_sync_run_id": self.first_sync_run_id,
            "last_sync_run_id": self.last_sync_run_id,
            "first_synced_at": self.first_synced_at.isoformat() if self.first_synced_at else None,
            "last_synced_at": self.last_synced_at.isoformat() if self.last_synced_at else None,
        }


class ZsxqTopicFavorite(db.Model):
    __tablename__ = "zsxq_topic_favorites"
    __table_args__ = (
        db.UniqueConstraint("group_id", "topic_id", name="uq_zsxq_topic_favorites_group_topic"),
        db.Index("ix_zsxq_topic_favorites_group_created", "group_id", db.text("created_at DESC")),
    )

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    group_id = db.Column(db.String(32), nullable=False, index=True)
    topic_id = db.Column(db.String(64), nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    def to_dict(self):
        return {
            "id": self.id,
            "group_id": self.group_id,
            "topic_id": self.topic_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class ZsxqTopicComment(db.Model):
    __tablename__ = "zsxq_topic_comments"
    __table_args__ = (
        db.UniqueConstraint("group_id", "topic_id", "comment_id", name="uq_zsxq_topic_comments_group_topic_comment"),
        db.Index("ix_zsxq_topic_comments_group_topic_created", "group_id", "topic_id", "create_time"),
        db.Index("ix_zsxq_topic_comments_owner_created", "owner_name", db.text("create_time DESC")),
    )

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    group_id = db.Column(db.String(32), nullable=False, index=True)
    tab_id = db.Column(db.String(128), nullable=True, index=True)
    topic_id = db.Column(db.String(64), nullable=False, index=True)
    comment_id = db.Column(db.String(64), nullable=False, index=True)
    parent_comment_id = db.Column(db.String(64), nullable=True, index=True)
    owner_user_id = db.Column(db.String(64), nullable=True, index=True)
    owner_name = db.Column(db.String(255), nullable=True, index=True)
    text = db.Column(db.Text, nullable=True)
    create_time = db.Column(db.String(64), nullable=True, index=True)
    likes_count = db.Column(db.Integer, nullable=False, default=0)
    replies_count = db.Column(db.Integer, nullable=False, default=0)
    rewards_count = db.Column(db.Integer, nullable=False, default=0)
    sticky = db.Column(db.Boolean, nullable=False, default=False)
    raw_payload = db.Column(db.JSON, nullable=False, default=dict)
    first_synced_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    last_synced_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    def to_dict(self):
        return {
            "id": self.id,
            "group_id": self.group_id,
            "tab_id": self.tab_id,
            "topic_id": self.topic_id,
            "comment_id": self.comment_id,
            "parent_comment_id": self.parent_comment_id,
            "owner_user_id": self.owner_user_id,
            "owner_name": self.owner_name,
            "text": self.text,
            "create_time": self.create_time,
            "likes_count": self.likes_count or 0,
            "replies_count": self.replies_count or 0,
            "rewards_count": self.rewards_count or 0,
            "sticky": bool(self.sticky),
            "first_synced_at": self.first_synced_at.isoformat() if self.first_synced_at else None,
            "last_synced_at": self.last_synced_at.isoformat() if self.last_synced_at else None,
        }


class ZsxqAttachment(db.Model):
    __tablename__ = "zsxq_attachments"
    __table_args__ = (
        db.UniqueConstraint("group_id", "tab_id", "topic_id", "attachment_key", name="uq_zsxq_attachments_group_tab_topic_key"),
        db.Index("ix_zsxq_attachments_group_topic_status", "group_id", "topic_id", "download_status"),
        db.Index("ix_zsxq_attachments_group_tab_status_file", "group_id", "tab_id", "download_status", "file_id"),
        db.Index("ix_zsxq_attachments_group_tab_status_key", "group_id", "tab_id", "download_status", "attachment_key"),
        db.Index("ix_zsxq_attachments_group_topic_tab_created", "group_id", "topic_id", "tab_id", "created_at"),
        db.Index("ix_zsxq_attachments_group_tab_topic_type", "group_id", "tab_id", "topic_id", "attachment_type"),
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
    file_ext = db.Column(db.String(20), nullable=True, index=True)
    download_status = db.Column(db.String(20), nullable=False, default="pending", index=True)
    error_message = db.Column(db.Text, nullable=True)
    publish_status = db.Column(db.String(32), nullable=True, index=True)
    publish_task_id = db.Column(db.String(36), nullable=True, index=True)
    publish_source_task_id = db.Column(db.String(36), nullable=True, index=True)
    publish_topic_id = db.Column(db.String(64), nullable=True)
    publish_submitted_count = db.Column(db.Integer, nullable=False, default=0)
    publish_verified_count = db.Column(db.Integer, nullable=False, default=0)
    publish_failed_count = db.Column(db.Integer, nullable=False, default=0)
    publish_updated_at = db.Column(db.DateTime, nullable=True)
    raw_payload = db.Column(db.JSON, default=dict)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.sync_projection_fields()

    def sync_projection_fields(self):
        self.file_ext = _attachment_file_ext(self.name, self.local_path)
        return self

    def effective_publish_status(self):
        status = str(self.publish_status or "").strip()
        if status == "published" and not self.publish_topic_id:
            return "published_unverified"
        return status

    def to_dict(self):
        effective_publish_status = self.effective_publish_status()
        publish_attention_required = (
            self.download_status == "downloaded"
            and effective_publish_status in {"publish_failed", "publish_skipped", "published_unverified"}
        )
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
            "file_ext": self.file_ext,
            "download_status": self.download_status,
            "error_message": self.error_message,
            "publish_status": effective_publish_status,
            "publish_raw_status": self.publish_status,
            "published": effective_publish_status == "published",
            "publish_submitted": effective_publish_status in {"submitted", "published"},
            "publish_attention_required": publish_attention_required,
            "publish_task_id": self.publish_task_id,
            "publish_source_task_id": self.publish_source_task_id,
            "publish_topic_id": self.publish_topic_id,
            "publish_submitted_count": self.publish_submitted_count or 0,
            "publish_verified_count": self.publish_verified_count or 0,
            "publish_failed_count": self.publish_failed_count or 0,
            "publish_updated_at": self.publish_updated_at.isoformat() if self.publish_updated_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


def _attachment_file_ext(*values):
    for value in values:
        text = str(value or "").strip().split("?", 1)[0].rstrip("/\\")
        if not text:
            continue
        basename = text.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        if "." not in basename:
            continue
        ext = basename.rsplit(".", 1)[-1].strip().lower()
        if ext:
            return ext[:20]
    return None


def _normalize_topic_text(value):
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return ""
    lines = [" ".join(line.split()) for line in text.split("\n")]
    return "\n".join(line for line in lines if line).strip()


def _topic_files(payload):
    if not isinstance(payload, dict):
        return []
    talk = payload.get("talk") if isinstance(payload.get("talk"), dict) else {}
    article = payload.get("article") if isinstance(payload.get("article"), dict) else {}
    talk_article = talk.get("article") if isinstance(talk.get("article"), dict) else {}
    for value in (
        payload.get("files"),
        talk.get("files"),
        article.get("files"),
        talk_article.get("files"),
    ):
        if isinstance(value, list):
            return value
    return []


def _topic_images(payload):
    if not isinstance(payload, dict):
        return []
    talk = payload.get("talk") if isinstance(payload.get("talk"), dict) else {}
    article = payload.get("article") if isinstance(payload.get("article"), dict) else {}
    talk_article = talk.get("article") if isinstance(talk.get("article"), dict) else {}
    question = payload.get("question") if isinstance(payload.get("question"), dict) else {}
    answer = payload.get("answer") if isinstance(payload.get("answer"), dict) else {}
    images = []
    for value in (
        payload.get("images"),
        talk.get("images"),
        article.get("images"),
        talk_article.get("images"),
        question.get("images"),
        answer.get("images"),
    ):
        if isinstance(value, list):
            images.extend(value)
    return images


def _loaded_model_attr(record, name, default=None):
    try:
        state = sqlalchemy_inspect(record)
        if name in state.unloaded:
            return default
    except Exception:
        pass
    try:
        return getattr(record, name, default)
    except Exception:
        return default


def _text(value, max_length):
    text = str(value or "").strip()
    if not text:
        return None
    return text[:max_length]


def _int_or_none(value):
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _int_or_zero(value):
    return _int_or_none(value) or 0


def _bool_or_none(value):
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return None


@event.listens_for(ZsxqSyncRun, "before_insert")
@event.listens_for(ZsxqSyncRun, "before_update")
def _sync_zsxq_run_projection_fields(_mapper, _connection, target):
    target.sync_projection_fields()


@event.listens_for(ZsxqTopic, "before_insert")
@event.listens_for(ZsxqTopic, "before_update")
def _sync_zsxq_topic_projection_fields(_mapper, _connection, target):
    target.sync_projection_fields()


@event.listens_for(ZsxqAttachment, "before_insert")
@event.listens_for(ZsxqAttachment, "before_update")
def _sync_zsxq_attachment_projection_fields(_mapper, _connection, target):
    target.sync_projection_fields()


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
