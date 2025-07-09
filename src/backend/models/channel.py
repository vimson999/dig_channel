# 频道数据模型
from datetime import datetime
from . import db

class Channel(db.Model):
    __tablename__ = 'channels'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(100), nullable=False)
    channel_id = db.Column(db.String(100), nullable=False, unique=True)
    url = db.Column(db.String(255), nullable=False)
    cpm = db.Column(db.Numeric(5, 2), nullable=False, default=1.00)
    category = db.Column(db.String(50), nullable=True)
    sort = db.Column(db.Integer, nullable=False, default=0)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    channel_data = db.relationship('ChannelData', backref='channel', lazy=True, cascade='all, delete-orphan')
    
    def __repr__(self):
        return f'<Channel {self.name}>'
    
    def to_dict(self):
        return {
            'id': self.id,
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
    def create_channel(cls, name, channel_id, url, cpm=1.00, category=None, sort=0):
        channel = cls(
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