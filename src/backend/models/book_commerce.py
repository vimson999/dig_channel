from datetime import datetime
from uuid import uuid4

from . import db


def new_id():
    return str(uuid4())


class IdentityMixin:
    id = db.Column(db.String(36), primary_key=True, default=new_id)
    source = db.Column(db.String(128), nullable=False)
    source_key = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_observed_at = db.Column(db.DateTime)


class TrackCategory(db.Model):
    """A normalized node in an account's hierarchical content track."""
    __tablename__ = 'bc_categories'
    __table_args__ = (db.UniqueConstraint('path', name='uq_bc_categories_path'),
                      db.Index('ix_bc_categories_parent_name', 'parent_id', 'name'))
    id = db.Column(db.String(36), primary_key=True, default=new_id)
    parent_id = db.Column(db.String(36), db.ForeignKey('bc_categories.id', ondelete='RESTRICT'))
    name = db.Column(db.String(128), nullable=False)
    path = db.Column(db.String(512), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    accounts = db.relationship('Account', secondary='bc_account_categories', back_populates='track_categories')


class Account(IdentityMixin, db.Model):
    __tablename__ = 'bc_accounts'
    __table_args__ = (db.UniqueConstraint('source', 'source_key'), db.UniqueConstraint('platform', 'external_id'))
    platform = db.Column(db.String(32), nullable=False)
    external_id = db.Column(db.String(255))
    name = db.Column(db.String(255), nullable=False)
    category = db.Column(db.String(128))
    bio = db.Column(db.Text)
    location = db.Column(db.String(128))
    is_ai_content = db.Column(db.Boolean)
    source_url = db.Column(db.Text)
    # Kept as source-provided text for import compatibility; queries use track_categories.
    track_categories = db.relationship('TrackCategory', secondary='bc_account_categories', back_populates='accounts')


class AccountCategory(db.Model):
    """Many-to-many relation between benchmark accounts and track nodes."""
    __tablename__ = 'bc_account_categories'
    __table_args__ = (db.Index('ix_bc_account_categories_category_account', 'category_id', 'account_id'),)
    account_id = db.Column(db.String(36), db.ForeignKey('bc_accounts.id', ondelete='CASCADE'), primary_key=True)
    category_id = db.Column(db.String(36), db.ForeignKey('bc_categories.id', ondelete='RESTRICT'), primary_key=True)


class Book(IdentityMixin, db.Model):
    __tablename__ = 'bc_books'
    __table_args__ = (db.UniqueConstraint('source', 'source_key'),)
    isbn13 = db.Column(db.String(13), unique=True)
    title = db.Column(db.String(500), nullable=False)
    author = db.Column(db.String(255))
    publisher = db.Column(db.String(255))
    edition = db.Column(db.String(128))
    category = db.Column(db.String(128))


class Listing(IdentityMixin, db.Model):
    __tablename__ = 'bc_listings'
    __table_args__ = (db.UniqueConstraint('source', 'source_key'), db.UniqueConstraint('platform', 'external_id'))
    platform = db.Column(db.String(32), nullable=False)
    external_id = db.Column(db.String(255))
    shop_external_id = db.Column(db.String(255))
    shop_name = db.Column(db.String(255))
    book_id = db.Column(db.String(36), db.ForeignKey('bc_books.id', ondelete='RESTRICT'))
    title = db.Column(db.String(500), nullable=False)
    variant = db.Column(db.String(255))
    source_url = db.Column(db.Text)


class AccountListing(db.Model):
    __tablename__ = 'bc_account_listings'
    __table_args__ = (db.UniqueConstraint('account_id', 'listing_id'),
                      db.CheckConstraint("status IN ('active','inactive','unknown')", name='ck_bc_account_listing_status'))
    id = db.Column(db.String(36), primary_key=True, default=new_id)
    account_id = db.Column(db.String(36), db.ForeignKey('bc_accounts.id', ondelete='RESTRICT'), nullable=False)
    listing_id = db.Column(db.String(36), db.ForeignKey('bc_listings.id', ondelete='RESTRICT'), nullable=False)
    first_seen_at = db.Column(db.DateTime, nullable=False)
    last_seen_at = db.Column(db.DateTime, nullable=False)
    status = db.Column(db.String(16), nullable=False, default='unknown')


class Video(IdentityMixin, db.Model):
    __tablename__ = 'bc_videos'
    __table_args__ = (db.UniqueConstraint('source', 'source_key'), db.UniqueConstraint('platform', 'external_id'), db.Index('ix_bc_videos_account_published', 'account_id', 'published_at', 'id'))
    account_id = db.Column(db.String(36), db.ForeignKey('bc_accounts.id', ondelete='RESTRICT'), nullable=False)
    platform = db.Column(db.String(32), nullable=False)
    external_id = db.Column(db.String(255))
    title = db.Column(db.Text, nullable=False)
    published_at = db.Column(db.DateTime)
    source_url = db.Column(db.Text)
    core_hook = db.Column(db.Text)
    script_text = db.Column(db.Text)
    transcript_status = db.Column(db.String(32))
    transcript_source = db.Column(db.String(128))
    transcript_updated_at = db.Column(db.DateTime)


class VideoListingLink(db.Model):
    __tablename__ = 'bc_video_listing_links'
    __table_args__ = (db.UniqueConstraint('video_id', 'account_listing_id', 'valid_from'),
                      db.CheckConstraint("valid_to IS NULL OR valid_to > valid_from", name='ck_bc_link_interval'),
                      db.CheckConstraint("relation_type IN ('attached','mentioned')", name='ck_bc_link_relation'),
                      db.CheckConstraint("verification_status IN ('confirmed','unverified','rejected')", name='ck_bc_link_verification'))
    id = db.Column(db.String(36), primary_key=True, default=new_id)
    video_id = db.Column(db.String(36), db.ForeignKey('bc_videos.id', ondelete='RESTRICT'), nullable=False)
    account_listing_id = db.Column(db.String(36), db.ForeignKey('bc_account_listings.id', ondelete='RESTRICT'), nullable=False)
    valid_from = db.Column(db.DateTime, nullable=False)
    valid_to = db.Column(db.DateTime)
    observed_at = db.Column(db.DateTime, nullable=False)
    relation_type = db.Column(db.String(16), nullable=False)
    verification_status = db.Column(db.String(16), nullable=False)
    evidence_reference = db.Column(db.Text)


class IngestBatch(db.Model):
    __tablename__ = 'bc_ingest_batches'
    __table_args__ = (db.UniqueConstraint('source', 'idempotency_key'),)
    id = db.Column(db.String(36), primary_key=True, default=new_id)
    source = db.Column(db.String(128), nullable=False)
    adapter_version = db.Column(db.String(64), nullable=False)
    idempotency_key = db.Column(db.String(128), nullable=False)
    payload_hash = db.Column(db.String(64), nullable=False)
    actor_user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='RESTRICT'), nullable=False)
    received_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    attempted_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    status = db.Column(db.String(16), nullable=False)
    error_summary = db.Column(db.JSON)
    counts = db.Column(db.JSON)
    attempt_count = db.Column(db.Integer, nullable=False, default=1)
    source_snapshot = db.Column(db.JSON)


class Observation(db.Model):
    __tablename__ = 'bc_observations'
    __table_args__ = (
        db.Index('ix_bc_obs_target_series_date', 'target_key', 'series_key', 'business_date', 'observed_at', 'id'),
        db.Index('ix_bc_obs_date', 'business_date', 'observed_at', 'id'),
        db.Index('ix_bc_obs_account_history', 'kind', 'account_id', 'observed_at', 'id'),
        db.Index('ix_bc_obs_video_history', 'kind', 'video_id', 'observed_at', 'id'),
        db.Index('ix_bc_obs_listing_history', 'kind', 'account_listing_id', 'observed_at', 'id'),
        db.CheckConstraint("(kind = 'account' AND account_listing_id IS NULL AND listing_id IS NULL AND video_id IS NULL AND metric_scope IS NULL) OR (kind = 'showcase' AND account_listing_id IS NOT NULL AND listing_id IS NOT NULL AND video_id IS NULL AND metric_scope IN ('account_listing','platform_listing','unknown')) OR (kind = 'video' AND video_id IS NOT NULL AND account_listing_id IS NULL AND listing_id IS NULL AND metric_scope IS NULL)", name='ck_bc_observation_target'),
    )
    id = db.Column(db.String(36), primary_key=True, default=new_id)
    batch_id = db.Column(db.String(36), db.ForeignKey('bc_ingest_batches.id', ondelete='RESTRICT'))
    kind = db.Column(db.String(16), nullable=False)
    account_id = db.Column(db.String(36), db.ForeignKey('bc_accounts.id', ondelete='RESTRICT'), nullable=False)
    account_listing_id = db.Column(db.String(36), db.ForeignKey('bc_account_listings.id', ondelete='RESTRICT'))
    listing_id = db.Column(db.String(36), db.ForeignKey('bc_listings.id', ondelete='RESTRICT'))
    video_id = db.Column(db.String(36), db.ForeignKey('bc_videos.id', ondelete='RESTRICT'))
    target_key = db.Column(db.String(80), nullable=False)
    series_key = db.Column(db.String(128), nullable=False)
    slot_key = db.Column(db.String(64), nullable=False, unique=True)
    observed_at = db.Column(db.DateTime, nullable=False)
    business_date = db.Column(db.Date, nullable=False)
    metric_scope = db.Column(db.String(32))
    values = db.Column(db.JSON, nullable=False, default=dict)
    quality = db.Column(db.JSON, nullable=False, default=dict)
    raw_evidence = db.Column(db.JSON)
    content_hash = db.Column(db.String(64), nullable=False, unique=True)
    received_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class AnalysisRun(db.Model):
    __tablename__ = 'bc_analysis_runs'
    __table_args__ = (db.UniqueConstraint('requested_by', 'request_key'),)
    id = db.Column(db.String(36), primary_key=True, default=new_id)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(16), nullable=False)
    input_revision = db.Column(db.Integer, nullable=False)
    rules_version = db.Column(db.Integer, nullable=False)
    rules_config = db.Column(db.JSON, nullable=False, default=dict)
    calculation_version = db.Column(db.String(32), nullable=False)
    requested_by = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='RESTRICT'))
    request_key = db.Column(db.String(128))
    started_at = db.Column(db.DateTime)
    completed_at = db.Column(db.DateTime)
    error_code = db.Column(db.String(64))
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class DailyMetric(db.Model):
    __tablename__ = 'bc_daily_metrics'
    __table_args__ = (db.UniqueConstraint('generation_id', 'kind', 'target_key', 'series_key', 'business_date'), db.Index('ix_bc_daily_rank_sales', 'generation_id', 'kind', 'business_date', 'sales_delta', 'id'), db.Index('ix_bc_daily_rank_shares', 'generation_id', 'kind', 'business_date', 'shares_delta', 'id'))
    id = db.Column(db.String(36), primary_key=True, default=new_id)
    generation_id = db.Column(db.String(36), db.ForeignKey('bc_analysis_runs.id', ondelete='RESTRICT'), nullable=False)
    kind = db.Column(db.String(16), nullable=False)
    target_key = db.Column(db.String(80), nullable=False)
    series_key = db.Column(db.String(128), nullable=False)
    selected_series = db.Column(db.Boolean, nullable=False, default=False)
    business_date = db.Column(db.Date, nullable=False)
    account_id = db.Column(db.String(36), db.ForeignKey('bc_accounts.id', ondelete='RESTRICT'))
    account_listing_id = db.Column(db.String(36), db.ForeignKey('bc_account_listings.id', ondelete='RESTRICT'))
    listing_id = db.Column(db.String(36), db.ForeignKey('bc_listings.id', ondelete='RESTRICT'))
    video_id = db.Column(db.String(36), db.ForeignKey('bc_videos.id', ondelete='RESTRICT'))
    selected_observation_id = db.Column(db.String(36), db.ForeignKey('bc_observations.id', ondelete='RESTRICT'), nullable=False)
    previous_observation_id = db.Column(db.String(36), db.ForeignKey('bc_observations.id', ondelete='RESTRICT'))
    current_values = db.Column(db.JSON, nullable=False, default=dict)
    deltas = db.Column(db.JSON, nullable=False, default=dict)
    quality = db.Column(db.JSON, nullable=False, default=dict)
    window_start = db.Column(db.DateTime)
    window_end = db.Column(db.DateTime)
    sales_delta = db.Column(db.BigInteger)
    shares_delta = db.Column(db.BigInteger)
    calculation_version = db.Column(db.String(32), nullable=False)


class Recommendation(db.Model):
    __tablename__ = 'bc_recommendations'
    __table_args__ = (db.UniqueConstraint('run_id', 'video_id', 'account_listing_id', 'business_date'), db.Index('ix_bc_recommendation_rank', 'run_id', 'business_date', 'status', 'shares_delta', 'id'))
    id = db.Column(db.String(36), primary_key=True, default=new_id)
    run_id = db.Column(db.String(36), db.ForeignKey('bc_analysis_runs.id', ondelete='RESTRICT'), nullable=False)
    account_id = db.Column(db.String(36), db.ForeignKey('bc_accounts.id', ondelete='RESTRICT'), nullable=False)
    account_listing_id = db.Column(db.String(36), db.ForeignKey('bc_account_listings.id', ondelete='RESTRICT'), nullable=False)
    video_id = db.Column(db.String(36), db.ForeignKey('bc_videos.id', ondelete='RESTRICT'), nullable=False)
    business_date = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(16), nullable=False)
    reason_codes = db.Column(db.JSON, nullable=False, default=list)
    evidence = db.Column(db.JSON, nullable=False, default=dict)
    sales_delta = db.Column(db.BigInteger)
    shares_delta = db.Column(db.BigInteger)
    window_start = db.Column(db.DateTime)
    window_end = db.Column(db.DateTime)
    rule_version = db.Column(db.Integer, nullable=False)


class State(db.Model):
    __tablename__ = 'bc_state'
    id = db.Column(db.Integer, primary_key=True)
    input_revision = db.Column(db.Integer, nullable=False, default=0)
    published_run_id = db.Column(db.String(36), db.ForeignKey('bc_analysis_runs.id', ondelete='RESTRICT'))
    active_rules_version = db.Column(db.Integer, nullable=False, default=1)
    rules_config = db.Column(db.JSON, nullable=False, default=dict)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class CandidateDecision(db.Model):
    __tablename__ = 'bc_candidate_decisions'
    __table_args__ = (db.UniqueConstraint('owner_user_id', 'video_id', 'account_listing_id', 'business_date'),
                      db.Index('ix_bc_decisions_owner_status', 'owner_user_id', 'decision', 'updated_at', 'id'),
                      db.CheckConstraint("decision IN ('pending','saved','dismissed','to_research')", name='ck_bc_decision_status'),
                      db.CheckConstraint('version > 0', name='ck_bc_decision_version'))
    id = db.Column(db.String(36), primary_key=True, default=new_id)
    owner_user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='RESTRICT'), nullable=False)
    video_id = db.Column(db.String(36), db.ForeignKey('bc_videos.id', ondelete='RESTRICT'), nullable=False)
    account_listing_id = db.Column(db.String(36), db.ForeignKey('bc_account_listings.id', ondelete='RESTRICT'), nullable=False)
    business_date = db.Column(db.Date, nullable=False)
    decision = db.Column(db.String(16), nullable=False, default='pending')
    note = db.Column(db.Text)
    version = db.Column(db.Integer, nullable=False, default=1)
    evidence_run_id = db.Column(db.String(36), db.ForeignKey('bc_analysis_runs.id', ondelete='RESTRICT'), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
