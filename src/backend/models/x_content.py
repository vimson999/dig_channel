"""X content knowledge base. Timestamps are UTC; metrics are derived at read time."""
from datetime import datetime
from . import db

class RecordMixin:
    id = db.Column(db.String(64), primary_key=True)
    is_mock = db.Column(db.Boolean, nullable=False, default=False, index=True)
    source_batch = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

class MetricsMixin:
    views = db.Column(db.BigInteger)
    likes = db.Column(db.BigInteger)
    replies = db.Column(db.BigInteger)
    reposts = db.Column(db.BigInteger)
    bookmarks = db.Column(db.BigInteger)
    metrics_captured_at = db.Column(db.DateTime)

class XViralPattern(RecordMixin, db.Model):
    __tablename__ = 'x_viral_patterns'
    name = db.Column(db.String(100), nullable=False)
    category = db.Column(db.String(50), default='cold_start_engagement')
    time_slot = db.Column(db.String(50), default='anytime')
    hook_formula = db.Column(db.Text)
    body_formula = db.Column(db.Text)
    cta_formula = db.Column(db.Text)
    template_skeleton = db.Column(db.Text)
    evidence_status = db.Column(db.String(20), default='testing')
    avg_reply_rate = db.Column(db.Numeric(8, 2))
    evidence_notes = db.Column(db.Text)
    tags = db.Column(db.JSON, nullable=False, default=list)

class XViralCase(RecordMixin, MetricsMixin, db.Model):
    __tablename__ = 'x_viral_cases'
    pattern_id = db.Column(db.String(64), db.ForeignKey('x_viral_patterns.id'), index=True)
    author_handle = db.Column(db.String(64))
    author_followers = db.Column(db.BigInteger)
    author_name = db.Column(db.String(100))
    verified_badge = db.Column(db.String(30))
    media_type = db.Column(db.String(30))
    translated_text = db.Column(db.Text)
    reusable_hook = db.Column(db.Text)
    language = db.Column(db.String(20), default='zh')
    raw_text = db.Column(db.Text, nullable=False)
    why_it_worked = db.Column(db.Text)
    is_negative_sample = db.Column(db.Boolean, nullable=False, default=False)
    screenshot_path = db.Column(db.String(1000))
    source_url = db.Column(db.String(1000))
    published_at = db.Column(db.DateTime)

class XOwnPost(RecordMixin, MetricsMixin, db.Model):
    __tablename__ = 'x_own_posts'
    pattern_id = db.Column(db.String(64), db.ForeignKey('x_viral_patterns.id'), index=True)
    ref_case_ids = db.Column(db.JSON, nullable=False, default=list)
    unresolved_case_ids = db.Column(db.JSON, nullable=False, default=list)
    title = db.Column(db.String(200))
    core_interactors = db.Column(db.JSON, nullable=False, default=list)
    total_blue_v_replies = db.Column(db.BigInteger)
    screenshot_path = db.Column(db.String(1000))
    account_handle = db.Column(db.String(64), default='vimson999')
    status = db.Column(db.String(20), default='draft_variant')
    time_slot = db.Column(db.String(50), default='anytime')
    content = db.Column(db.Text, nullable=False)
    early_burst_notes = db.Column(db.Text)
    early_replies = db.Column(db.BigInteger)
    early_window_minutes = db.Column(db.Integer, default=7)
    review_notes = db.Column(db.Text)
    published_at = db.Column(db.DateTime)
    source_url = db.Column(db.String(1000))

class XReplyTactic(RecordMixin, db.Model):
    __tablename__ = 'x_reply_tactics'
    tactic_name = db.Column(db.String(100), nullable=False)
    trigger_condition = db.Column(db.Text)
    reply_logic = db.Column(db.Text)
    sample_replies = db.Column(db.JSON, nullable=False, default=list)
    frequent_friends = db.Column(db.JSON, nullable=False, default=list)
    scenario_type = db.Column(db.String(50))
    algorithm_purpose = db.Column(db.Text)
    priority = db.Column(db.Integer)
    action_type = db.Column(db.String(20), default='reply')

MODELS = {'patterns': XViralPattern, 'cases': XViralCase, 'posts': XOwnPost, 'tactics': XReplyTactic}
