"""Authenticated Bilibili UPower archive statistics snapshots."""

from datetime import datetime
from . import db


class BilibiliUpowerArchiveStat(db.Model):
    __tablename__ = "bilibili_upower_archive_stats"
    __table_args__ = (
        db.UniqueConstraint("account_uid", "period_days", "aid", name="uq_bilibili_upower_archive_account_period_aid"),
        db.Index("ix_bilibili_upower_archive_period_pub", "account_uid", "period_days", "pub_time"),
    )

    id = db.Column(db.Integer, primary_key=True)
    account_uid = db.Column(db.String(32), nullable=False)
    period_days = db.Column(db.Integer, nullable=False)
    aid = db.Column(db.BigInteger, nullable=False)
    title = db.Column(db.Text, nullable=False)
    cover_url = db.Column(db.Text)
    video_url = db.Column(db.Text)
    pub_time = db.Column(db.DateTime)
    duration_seconds = db.Column(db.Integer)
    play_count = db.Column(db.BigInteger)
    income = db.Column(db.BigInteger)
    level_price = db.Column(db.BigInteger)
    level_name = db.Column(db.String(255))
    badge_text = db.Column(db.String(255))
    raw_payload = db.Column(db.JSON, nullable=False)
    fetched_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class BilibiliUpowerTopicReport(db.Model):
    __tablename__ = "bilibili_upower_topic_reports"
    id = db.Column(db.String(64), primary_key=True)
    account_uid = db.Column(db.String(32), nullable=False, index=True)
    period_days = db.Column(db.Integer, nullable=False)
    source_fetched_at = db.Column(db.DateTime, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    summary = db.Column(db.JSON, nullable=False)


class BilibiliUpowerTopic(db.Model):
    __tablename__ = "bilibili_upower_topics"
    __table_args__ = (db.UniqueConstraint("report_id", "name", name="uq_upower_topic_report_name"),)
    id = db.Column(db.Integer, primary_key=True)
    report_id = db.Column(db.String(64), db.ForeignKey("bilibili_upower_topic_reports.id"), nullable=False, index=True)
    name = db.Column(db.String(80), nullable=False)
    metrics = db.Column(db.JSON, nullable=False)
    archive_aids = db.Column(db.JSON, nullable=False)
    conclusion = db.Column(db.Text, nullable=False)
