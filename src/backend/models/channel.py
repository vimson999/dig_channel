# 频道数据模型
from datetime import datetime
from . import db

class Channel(db.Model):
    __tablename__ = 'channels'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    channel_id = db.Column(db.String(100), nullable=False)
    url = db.Column(db.String(255), nullable=False)
    cpm = db.Column(db.Numeric(5, 2), nullable=False, default=1.00)
    category = db.Column(db.String(50), nullable=True)
    sort = db.Column(db.Integer, nullable=False, default=0)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # 添加复合唯一索引：同一用户下频道ID唯一
    __table_args__ = (
        db.UniqueConstraint('user_id', 'channel_id', name='unique_user_channel'),
        db.Index('idx_channel_user_id', 'user_id'),
    )
    
    channel_data = db.relationship('ChannelData', backref='channel', lazy=True, cascade='all, delete-orphan')
    
    def __repr__(self):
        return f'<Channel {self.name}>'
    
    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'name': self.name,
            'channel_id': self.channel_id,
            'url': self.url,
            'cpm': float(self.cpm),
            'category': self.category,
            'sort': self.sort,
            'is_active': self.is_active,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }
    
    @classmethod
    def create_channel(cls, user_id, name, channel_id, url, cpm=1.00, category=None, sort=0):
        # 检查用户下是否已存在相同的频道ID
        existing = cls.query.filter_by(user_id=user_id, channel_id=channel_id).first()
        if existing:
            raise ValueError('该频道已存在于您的账户中')
        
        channel = cls(
            user_id=user_id,
            name=name,
            channel_id=channel_id,
            url=url,
            cpm=cpm,
            category=category,
            sort=sort
        )
        db.session.add(channel)
        db.session.commit()
        return channel
    
    @classmethod
    def get_user_channels(cls, user_id, active_only=True):
        """获取用户的频道列表"""
        query = cls.query.filter_by(user_id=user_id)
        if active_only:
            query = query.filter_by(is_active=True)
        return query.order_by(cls.sort.asc(), cls.created_at.desc()).all()
    
    def belongs_to_user(self, user_id):
        """检查频道是否属于指定用户"""
        return self.user_id == user_id