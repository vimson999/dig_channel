"""商机发现相关的数据模型"""

from datetime import datetime

from . import db


class OpportunitySignal(db.Model):
    """商机信号表，可支持多平台扩展"""

    __tablename__ = 'opportunity_signals'
    __table_args__ = (
        db.UniqueConstraint('platform', 'source_id', name='uq_opportunity_platform_source'),
        db.Index('ix_opportunity_category_created', 'category', 'created_at'),
    )

    id = db.Column(db.Integer, primary_key=True)
    source_id = db.Column(db.String(255), nullable=False)
    source_type = db.Column(db.String(50), nullable=False, default='post')
    platform = db.Column(db.String(50), nullable=False, index=True)

    title = db.Column(db.String(500), nullable=False)
    description = db.Column(db.Text)
    author = db.Column(db.String(255), index=True)
    author_followers = db.Column(db.Integer)

    engagement_metrics = db.Column(db.JSON, default=dict)
    keywords = db.Column(db.JSON, default=list)
    tags = db.Column(db.JSON, default=list)

    revenue_mention = db.Column(db.String(100))
    user_count_mention = db.Column(db.String(100))
    language = db.Column(db.String(16))
    country = db.Column(db.String(8))

    urls = db.Column(db.JSON, default=list)

    created_at = db.Column(db.DateTime, nullable=False, index=True)
    collected_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    confidence_score = db.Column(db.Float, nullable=False, index=True)
    category = db.Column(db.String(100), index=True)

    attributes = db.Column(db.JSON, default=dict)
    raw_data = db.Column(db.JSON, default=dict)

    is_processed = db.Column(db.Boolean, default=False, index=True)
    is_verified = db.Column(db.Boolean, default=False, index=True)
    is_archived = db.Column(db.Boolean, default=False, index=True)

    def to_dict(self) -> dict:
        """转换为字典格式，方便API序列化"""

        return {
            'id': str(self.id),
            'platform': self.platform,
            'source_id': self.source_id,
            'source_type': self.source_type,
            'title': self.title,
            'description': self.description,
            'author': self.author,
            'author_followers': self.author_followers,
            'engagement_metrics': self.engagement_metrics or {},
            'keywords': self.keywords or [],
            'tags': self.tags or [],
            'revenue_mention': self.revenue_mention,
            'user_count_mention': self.user_count_mention,
            'language': self.language,
            'country': self.country,
            'urls': self.urls or [],
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'collected_at': self.collected_at.isoformat() if self.collected_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'confidence_score': self.confidence_score,
            'category': self.category,
            'metadata': self.attributes or {},
            'raw_data': self.raw_data or {},
            'is_processed': self.is_processed,
            'is_verified': self.is_verified,
            'is_archived': self.is_archived,
        }


class UserOpportunityInteraction(db.Model):
    """用户商机互动表"""

    __tablename__ = 'user_opportunity_interactions'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(255), nullable=False, index=True)
    opportunity_id = db.Column(
        db.Integer,
        db.ForeignKey('opportunity_signals.id', ondelete='CASCADE'),
        nullable=False,
        index=True,
    )

    interaction_type = db.Column(db.String(50), nullable=False)
    interaction_data = db.Column(db.JSON, default=dict)
    context = db.Column(db.JSON, default=dict)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'user_id': self.user_id,
            'opportunity_id': self.opportunity_id,
            'interaction_type': self.interaction_type,
            'interaction_data': self.interaction_data or {},
            'context': self.context or {},
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class CollectionKeyword(db.Model):
    """采集关键词配置表"""

    __tablename__ = 'collection_keywords'

    id = db.Column(db.Integer, primary_key=True)
    keyword = db.Column(db.String(255), nullable=False, unique=True)
    category = db.Column(db.String(100), index=True)
    priority = db.Column(db.Integer, default=1)
    platform = db.Column(db.String(50), nullable=False, default='reddit', index=True)
    tags = db.Column(db.JSON, default=list)
    is_active = db.Column(db.Boolean, default=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    signals_collected = db.Column(db.Integer, default=0)
    last_collection = db.Column(db.DateTime)
    attributes = db.Column(db.JSON, default=dict)

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'keyword': self.keyword,
            'category': self.category,
            'priority': self.priority,
            'platform': self.platform,
            'tags': self.tags or [],
            'is_active': self.is_active,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'signals_collected': self.signals_collected,
            'last_collection': self.last_collection.isoformat() if self.last_collection else None,
            'metadata': self.attributes or {},
        }


class CollectionJob(db.Model):
    """采集任务记录表"""

    __tablename__ = 'collection_jobs'

    id = db.Column(db.Integer, primary_key=True)
    job_type = db.Column(db.String(50), nullable=False)
    status = db.Column(db.String(50), nullable=False, index=True)

    config = db.Column(db.JSON, default=dict)
    platforms = db.Column(db.JSON, default=list)

    signals_collected = db.Column(db.Integer, default=0)
    signals_processed = db.Column(db.Integer, default=0)
    signals_saved = db.Column(db.Integer, default=0)

    error_message = db.Column(db.Text)
    result_snapshot = db.Column(db.JSON, default=dict)

    started_at = db.Column(db.DateTime)
    completed_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'job_type': self.job_type,
            'status': self.status,
            'config': self.config or {},
            'platforms': self.platforms or [],
            'signals_collected': self.signals_collected,
            'signals_processed': self.signals_processed,
            'signals_saved': self.signals_saved,
            'error_message': self.error_message,
            'result_snapshot': self.result_snapshot or {},
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }
