"""
商机发现数据模型
"""

from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime, Float, Boolean, JSON
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship

Base = declarative_base()

class OpportunitySignal(Base):
    """商机信号表"""
    __tablename__ = 'opportunity_signals'
    
    id = Column(String(255), primary_key=True)
    platform = Column(String(50), nullable=False, index=True)
    title = Column(String(500), nullable=False)
    description = Column(Text)
    author = Column(String(255), index=True)
    author_followers = Column(Integer)
    
    # 互动指标 JSON 格式存储
    engagement_metrics = Column(JSON)
    
    # 关键词 JSON 数组
    keywords = Column(JSON)
    
    revenue_mention = Column(String(100))
    user_count_mention = Column(String(100))
    
    # URL列表 JSON 数组
    urls = Column(JSON)
    
    created_at = Column(DateTime, nullable=False, index=True)
    collected_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    confidence_score = Column(Float, nullable=False, index=True)
    category = Column(String(100), index=True)
    
    # 原始数据 JSON 格式
    raw_data = Column(JSON)
    
    # 状态字段
    is_processed = Column(Boolean, default=False, index=True)
    is_verified = Column(Boolean, default=False)
    is_archived = Column(Boolean, default=False)
    
    def to_dict(self):
        """转换为字典格式"""
        return {
            'id': self.id,
            'platform': self.platform,
            'title': self.title,
            'description': self.description,
            'author': self.author,
            'author_followers': self.author_followers,
            'engagement_metrics': self.engagement_metrics,
            'keywords': self.keywords,
            'revenue_mention': self.revenue_mention,
            'user_count_mention': self.user_count_mention,
            'urls': self.urls,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'collected_at': self.collected_at.isoformat() if self.collected_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'confidence_score': self.confidence_score,
            'category': self.category,
            'is_processed': self.is_processed,
            'is_verified': self.is_verified,
            'is_archived': self.is_archived
        }

class UserOpportunityInteraction(Base):
    """用户商机互动表"""
    __tablename__ = 'user_opportunity_interactions'
    
    id = Column(Integer, primary_key=True)
    user_id = Column(String(255), nullable=False, index=True)
    opportunity_id = Column(String(255), nullable=False, index=True)
    
    # 互动类型：view, save, like, dislike, follow, analysis_request
    interaction_type = Column(String(50), nullable=False)
    
    # 互动数据 JSON 格式
    interaction_data = Column(JSON)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    
    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'opportunity_id': self.opportunity_id,
            'interaction_type': self.interaction_type,
            'interaction_data': self.interaction_data,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }

class CollectionKeyword(Base):
    """采集关键词配置表"""
    __tablename__ = 'collection_keywords'
    
    id = Column(Integer, primary_key=True)
    keyword = Column(String(255), nullable=False, unique=True)
    category = Column(String(100), index=True)
    priority = Column(Integer, default=1)  # 1-5, 优先级
    is_active = Column(Boolean, default=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # 关键词统计
    signals_collected = Column(Integer, default=0)
    last_collection = Column(DateTime)
    
    def to_dict(self):
        return {
            'id': self.id,
            'keyword': self.keyword,
            'category': self.category,
            'priority': self.priority,
            'is_active': self.is_active,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'signals_collected': self.signals_collected,
            'last_collection': self.last_collection.isoformat() if self.last_collection else None
        }

class CollectionJob(Base):
    """采集任务记录表"""
    __tablename__ = 'collection_jobs'
    
    id = Column(Integer, primary_key=True)
    job_type = Column(String(50), nullable=False)  # full, keyword, platform
    status = Column(String(50), nullable=False, index=True)  # pending, running, completed, failed
    
    # 任务配置 JSON
    config = Column(JSON)
    
    # 任务结果统计
    signals_collected = Column(Integer, default=0)
    signals_processed = Column(Integer, default=0)
    signals_saved = Column(Integer, default=0)
    
    # 错误信息
    error_message = Column(Text)
    
    started_at = Column(DateTime)
    completed_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    def to_dict(self):
        return {
            'id': self.id,
            'job_type': self.job_type,
            'status': self.status,
            'config': self.config,
            'signals_collected': self.signals_collected,
            'signals_processed': self.signals_processed,
            'signals_saved': self.signals_saved,
            'error_message': self.error_message,
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }