# 频道数据模型
from datetime import datetime, date
from . import db

class ChannelData(db.Model):
    __tablename__ = 'channel_data'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    channel_id = db.Column(db.Integer, db.ForeignKey('channels.id', ondelete='CASCADE'), nullable=False)
    date = db.Column(db.Date, nullable=False, default=date.today)
    subscribers = db.Column(db.Integer, nullable=False, default=0)
    total_views = db.Column(db.BigInteger, nullable=False, default=0)
    video_count = db.Column(db.Integer, nullable=False, default=0)
    subscriber_change = db.Column(db.Integer, nullable=False, default=0)
    view_change = db.Column(db.Integer, nullable=False, default=0)
    video_change = db.Column(db.Integer, nullable=False, default=0)
    estimated_revenue = db.Column(db.Numeric(10, 2), nullable=False, default=0.00)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    
    __table_args__ = (
        db.UniqueConstraint('channel_id', 'date', name='unique_channel_date'),
        db.Index('idx_channel_data_date', 'date'),
        db.Index('idx_channel_data_channel_id', 'channel_id'),
    )
    
    def __repr__(self):
        return f'<ChannelData {self.channel.name} {self.date}>'
    
    def to_dict(self):
        return {
            'id': self.id,
            'channel_id': self.channel_id,
            'date': self.date.isoformat() if self.date else None,
            'subscribers': self.subscribers,
            'total_views': self.total_views,
            'video_count': self.video_count,
            'subscriber_change': self.subscriber_change,
            'view_change': self.view_change,
            'video_change': self.video_change,
            'estimated_revenue': float(self.estimated_revenue),
            'created_at': self.created_at.isoformat() if self.created_at else None
        }
    
    @classmethod
    def create_data_record(cls, channel_id, date_value, subscribers, total_views, video_count):
        data = cls(
            channel_id=channel_id,
            date=date_value,
            subscribers=subscribers,
            total_views=total_views,
            video_count=video_count
        )
        db.session.add(data)
        db.session.commit()
        return data
    
    def calculate_changes(self, previous_data=None):
        from .channel import Channel
        
        if previous_data:
            self.subscriber_change = self.subscribers - previous_data.subscribers
            self.view_change = self.total_views - previous_data.total_views
            self.video_change = self.video_count - previous_data.video_count
            
            # 获取频道信息计算收入
            if self.view_change > 0:
                channel = Channel.query.get(self.channel_id)
                if channel and channel.cpm:
                    self.estimated_revenue = (self.view_change * float(channel.cpm)) / 1000
                else:
                    self.estimated_revenue = 0.00
            else:
                self.estimated_revenue = 0.00
        else:
            self.subscriber_change = 0
            self.view_change = 0
            self.video_change = 0
            self.estimated_revenue = 0.00
        
        db.session.commit()