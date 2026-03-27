from datetime import datetime
import uuid
import os
from . import db

class TTSArticle(db.Model):
    """TTS文章任务模型"""
    __tablename__ = 'tts_articles'

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    title = db.Column(db.String(255), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    
    # 状态: pending, processing, completed, failed, partial_failed
    status = db.Column(db.String(20), default='pending', index=True)
    
    # 配置信息 (JSON存储音色、语速等)
    config = db.Column(db.JSON, nullable=True)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # 关系映射
    segments = db.relationship('TTSSegment', backref='article', lazy=True, cascade="all, delete-orphan")

    def to_dict(self):
        return {
            'id': self.id,
            'title': self.title,
            'status': self.status,
            'config': self.config,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'segment_count': len(self.segments)
        }

class TTSSegment(db.Model):
    """TTS段落明细模型"""
    __tablename__ = 'tts_segments'

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    article_id = db.Column(db.String(36), db.ForeignKey('tts_articles.id'), nullable=False)
    
    sequence = db.Column(db.Integer, nullable=False)  # 段落顺序
    text = db.Column(db.Text, nullable=False)        # 段落文本内容
    
    # 状态: waiting, processing, success, failed
    status = db.Column(db.String(20), default='waiting')
    
    audio_path = db.Column(db.String(512), nullable=True)  # 生成的音频路径
    error_log = db.Column(db.Text, nullable=True)          # 失败原因记录
    
    processed_at = db.Column(db.DateTime, nullable=True)

    def to_dict(self):
        return {
            'id': self.id,
            'article_id': self.article_id,
            'sequence': self.sequence,
            'text': self.text,
            'status': self.status,
            'audio_path': self.audio_path,
            'error_log': self.error_log,
            'processed_at': self.processed_at.isoformat() if self.processed_at else None
        }
