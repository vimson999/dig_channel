from datetime import datetime
import uuid

from . import db


def _new_id():
    return str(uuid.uuid4())


class BilibiliCompetitor(db.Model):
    """A public Bilibili account monitored by the intelligence desk."""

    __tablename__ = "bilibili_competitors"
    __table_args__ = (
        db.UniqueConstraint("uid", name="uq_bilibili_competitors_uid"),
        db.Index("ix_bilibili_competitors_type_active", "content_type", "active"),
    )

    id = db.Column(db.String(36), primary_key=True, default=_new_id)
    name = db.Column(db.String(255), nullable=False)
    uid = db.Column(db.String(32), nullable=False, unique=True, index=True)
    content_type = db.Column(db.String(32), nullable=False, index=True)
    source_url = db.Column(db.Text, nullable=True)
    active = db.Column(db.Boolean, nullable=False, default=True, index=True)
    last_synced_at = db.Column(db.DateTime, nullable=True)
    latest_published_at = db.Column(db.DateTime, nullable=True)
    last_error = db.Column(db.Text, nullable=True)
    video_count = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    videos = db.relationship(
        "BilibiliCompetitorVideo",
        backref="competitor",
        cascade="all, delete-orphan",
        lazy=True,
    )
    charging_snapshots = db.relationship(
        "BilibiliChargingSnapshot",
        backref="competitor",
        cascade="all, delete-orphan",
        lazy=True,
    )

    def compact_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "uid": self.uid,
            "content_type": self.content_type,
            "source_url": self.source_url,
            "active": bool(self.active),
            "last_synced_at": self.last_synced_at.isoformat() + "Z" if self.last_synced_at else None,
            "latest_published_at": self.latest_published_at.isoformat() + "Z" if self.latest_published_at else None,
            "last_error": self.last_error,
            "video_count": self.video_count,
            "created_at": self.created_at.isoformat() + "Z" if self.created_at else None,
            "updated_at": self.updated_at.isoformat() + "Z" if self.updated_at else None,
        }


class BilibiliCompetitorVideo(db.Model):
    """A competitor upload plus compact projections of its local match state."""

    __tablename__ = "bilibili_competitor_videos"
    __table_args__ = (
        db.UniqueConstraint("bvid", name="uq_bilibili_competitor_videos_bvid"),
        db.Index("ix_bilibili_competitor_videos_competitor_published", "competitor_id", db.text("published_at DESC")),
        db.Index("ix_bilibili_competitor_videos_type_match_published", "content_type", "match_status", db.text("published_at DESC")),
        db.Index("ix_bilibili_competitor_videos_decision_published", "decision_status", db.text("published_at DESC")),
    )

    id = db.Column(db.String(36), primary_key=True, default=_new_id)
    competitor_id = db.Column(
        db.String(36),
        db.ForeignKey("bilibili_competitors.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    content_type = db.Column(db.String(32), nullable=False, index=True)
    bvid = db.Column(db.String(32), nullable=False, unique=True, index=True)
    aid = db.Column(db.String(32), nullable=True)
    title = db.Column(db.String(500), nullable=False)
    description = db.Column(db.Text, nullable=True)
    cover_url = db.Column(db.Text, nullable=True)
    duration_seconds = db.Column(db.Integer, nullable=True)
    published_at = db.Column(db.DateTime, nullable=True, index=True)
    play_count = db.Column(db.Integer, nullable=True)
    comment_count = db.Column(db.Integer, nullable=True)
    danmaku_count = db.Column(db.Integer, nullable=True)
    is_charging = db.Column(db.Boolean, nullable=False, default=False)
    raw_payload = db.Column(db.JSON, nullable=False, default=dict)

    match_status = db.Column(db.String(32), nullable=False, default="pending", index=True)
    match_method = db.Column(db.String(32), nullable=True)
    match_confidence = db.Column(db.Float, nullable=True)
    match_relation = db.Column(db.String(32), nullable=True)
    matched_local_type = db.Column(db.String(40), nullable=True)
    matched_local_id = db.Column(db.String(64), nullable=True, index=True)
    matched_local_title = db.Column(db.String(500), nullable=True)
    local_exists = db.Column(db.Boolean, nullable=True)
    orchestration_status = db.Column(db.String(32), nullable=True, index=True)
    orchestration_task_id = db.Column(db.String(36), nullable=True, index=True)
    match_reason = db.Column(db.Text, nullable=True)
    match_error = db.Column(db.Text, nullable=True)
    match_payload = db.Column(db.JSON, nullable=False, default=dict)
    matched_at = db.Column(db.DateTime, nullable=True)

    decision_status = db.Column(db.String(32), nullable=False, default="pending", index=True)
    decision_note = db.Column(db.Text, nullable=True)
    decided_at = db.Column(db.DateTime, nullable=True)

    first_seen_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    last_seen_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    @property
    def video_url(self):
        return f"https://www.bilibili.com/video/{self.bvid}/"

    def compact_dict(self, competitor_name=None):
        return {
            "id": self.id,
            "competitor_id": self.competitor_id,
            "competitor_name": competitor_name or getattr(self.competitor, "name", None),
            "content_type": self.content_type,
            "bvid": self.bvid,
            "video_url": self.video_url,
            "title": self.title,
            "cover_url": self.cover_url,
            "duration_seconds": self.duration_seconds,
            "published_at": self.published_at.isoformat() + "Z" if self.published_at else None,
            "play_count": self.play_count,
            "comment_count": self.comment_count,
            "danmaku_count": self.danmaku_count,
            "is_charging": bool(self.is_charging),
            "match_status": self.match_status,
            "match_method": self.match_method,
            "match_confidence": self.match_confidence,
            "match_relation": self.match_relation,
            "matched_local_type": self.matched_local_type,
            "matched_local_id": self.matched_local_id,
            "matched_local_title": self.matched_local_title,
            "local_exists": self.local_exists,
            "orchestration_status": self.orchestration_status,
            "orchestration_task_id": self.orchestration_task_id,
            "match_reason": self.match_reason,
            "match_error": self.match_error,
            "matched_at": self.matched_at.isoformat() + "Z" if self.matched_at else None,
            "decision_status": self.decision_status,
            "decision_note": self.decision_note,
            "decided_at": self.decided_at.isoformat() + "Z" if self.decided_at else None,
            "first_seen_at": self.first_seen_at.isoformat() + "Z" if self.first_seen_at else None,
            "last_seen_at": self.last_seen_at.isoformat() + "Z" if self.last_seen_at else None,
        }

    def detail_dict(self, competitor_name=None):
        payload = self.compact_dict(competitor_name=competitor_name)
        payload.update({
            "aid": self.aid,
            "description": self.description,
            "match_payload": self.match_payload or {},
            "raw_payload": self.raw_payload or {},
        })
        return payload


class BilibiliChargingSnapshot(db.Model):
    """One business-day snapshot of a competitor's public charger count."""

    __tablename__ = "bilibili_charging_snapshots"
    __table_args__ = (
        db.UniqueConstraint(
            "competitor_id",
            "snapshot_date",
            name="uq_bilibili_charging_snapshot_competitor_date",
        ),
        db.Index(
            "ix_bilibili_charging_snapshots_date_competitor",
            "snapshot_date",
            "competitor_id",
        ),
    )

    id = db.Column(db.String(36), primary_key=True, default=_new_id)
    competitor_id = db.Column(
        db.String(36),
        db.ForeignKey("bilibili_competitors.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    snapshot_date = db.Column(db.Date, nullable=False, index=True)
    charger_count = db.Column(db.Integer, nullable=False)
    fetched_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    source = db.Column(db.String(40), nullable=False, default="bilibili_elec_monthly")
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )
