# 视频数据模型
from datetime import datetime
from . import db

class Video(db.Model):
    __tablename__ = 'videos'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    video_id = db.Column(db.String(100), nullable=False, unique=True)
    channel_id = db.Column(db.Integer, db.ForeignKey('channels.id'), nullable=False)
    title = db.Column(db.String(500), nullable=False)
    description = db.Column(db.Text, nullable=True)
    published_at = db.Column(db.DateTime, nullable=False)
    duration = db.Column(db.String(20), nullable=True)  # ISO 8601 format: PT4M13S
    view_count = db.Column(db.BigInteger, nullable=False, default=0)
    like_count = db.Column(db.BigInteger, nullable=False, default=0)
    comment_count = db.Column(db.BigInteger, nullable=False, default=0)
    tags = db.Column(db.Text, nullable=True)  # JSON string of tags
    thumbnail_url = db.Column(db.String(500), nullable=True)
    category_id = db.Column(db.String(10), nullable=True)
    category_name = db.Column(db.String(100), nullable=True)
    language = db.Column(db.String(10), nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # 关联关系
    channel = db.relationship('Channel', backref='videos')
    video_data = db.relationship('VideoData', backref='video', lazy=True, cascade='all, delete-orphan')
    
    def __repr__(self):
        return f'<Video {self.title}>'
    
    def to_dict(self):
        import json
        
        # 解析标签
        tags_list = []
        if self.tags:
            try:
                tags_list = json.loads(self.tags)
            except:
                tags_list = []
        
        return {
            'id': self.id,
            'video_id': self.video_id,
            'channel_id': self.channel_id,
            'channel_name': self.channel.name if self.channel else None,
            'title': self.title,
            'description': self.description,
            'published_at': self.published_at.isoformat() if self.published_at else None,
            'duration': self.duration,
            'view_count': self.view_count,
            'like_count': self.like_count,
            'comment_count': self.comment_count,
            'tags': tags_list,
            'thumbnail_url': self.thumbnail_url,
            'category_id': self.category_id,
            'category_name': self.category_name,
            'language': self.language,
            'is_active': self.is_active,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'url': f'https://www.youtube.com/watch?v={self.video_id}'
        }
    
    def get_engagement_rate(self):
        """计算互动率"""
        if self.view_count == 0:
            return 0
        engagement = (self.like_count + self.comment_count) / self.view_count
        return round(engagement * 100, 2)
    
    def get_duration_seconds(self):
        """将ISO 8601时长转换为秒"""
        if not self.duration:
            return 0
        
        import re
        # 匹配 PT4M13S 格式
        match = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', self.duration)
        if not match:
            return 0
        
        hours = int(match.group(1) or 0)
        minutes = int(match.group(2) or 0)
        seconds = int(match.group(3) or 0)
        
        return hours * 3600 + minutes * 60 + seconds
    
    def get_duration_display(self):
        """获取可读的时长显示"""
        total_seconds = self.get_duration_seconds()
        if total_seconds == 0:
            return "未知"
        
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        seconds = total_seconds % 60
        
        if hours > 0:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        else:
            return f"{minutes}:{seconds:02d}"
    
    @classmethod
    def create_video(cls, video_data):
        """创建视频记录"""
        import json
        
        video = cls(
            video_id=video_data['video_id'],
            channel_id=video_data['channel_id'],
            title=video_data['title'],
            description=video_data.get('description', ''),
            published_at=video_data['published_at'],
            duration=video_data.get('duration'),
            view_count=video_data.get('view_count', 0),
            like_count=video_data.get('like_count', 0),
            comment_count=video_data.get('comment_count', 0),
            tags=json.dumps(video_data.get('tags', []), ensure_ascii=False),
            thumbnail_url=video_data.get('thumbnail_url'),
            category_id=video_data.get('category_id'),
            category_name=video_data.get('category_name'),
            language=video_data.get('language')
        )
        
        db.session.add(video)
        db.session.commit()
        return video

class VideoData(db.Model):
    __tablename__ = 'video_data'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    video_id = db.Column(db.Integer, db.ForeignKey('videos.id'), nullable=False)
    date = db.Column(db.Date, nullable=False)
    view_count = db.Column(db.BigInteger, nullable=False, default=0)
    like_count = db.Column(db.BigInteger, nullable=False, default=0)
    comment_count = db.Column(db.BigInteger, nullable=False, default=0)
    view_change = db.Column(db.BigInteger, nullable=False, default=0)
    like_change = db.Column(db.BigInteger, nullable=False, default=0)
    comment_change = db.Column(db.BigInteger, nullable=False, default=0)
    engagement_rate = db.Column(db.Numeric(5, 2), nullable=False, default=0.00)
    estimated_revenue = db.Column(db.Numeric(10, 2), nullable=False, default=0.00)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # 创建复合索引
    __table_args__ = (
        db.Index('idx_video_date', 'video_id', 'date'),
    )
    
    def __repr__(self):
        return f'<VideoData {self.video_id} - {self.date}>'
    
    def to_dict(self):
        return {
            'id': self.id,
            'video_id': self.video_id,
            'date': self.date.isoformat() if self.date else None,
            'view_count': self.view_count,
            'like_count': self.like_count,
            'comment_count': self.comment_count,
            'view_change': self.view_change,
            'like_change': self.like_change,
            'comment_change': self.comment_change,
            'engagement_rate': float(self.engagement_rate),
            'estimated_revenue': float(self.estimated_revenue),
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }
    
    def calculate_changes(self, previous_data=None):
        """计算相对于前一天的变化"""
        if previous_data:
            self.view_change = self.view_count - previous_data.view_count
            self.like_change = self.like_count - previous_data.like_count
            self.comment_change = self.comment_count - previous_data.comment_count
        else:
            self.view_change = 0
            self.like_change = 0
            self.comment_change = 0
        
        # 计算互动率
        if self.view_count > 0:
            self.engagement_rate = (self.like_count + self.comment_count) / self.view_count * 100
        else:
            self.engagement_rate = 0.00
        
        # 计算预估收入(需要频道的CPM数据)
        if self.view_change > 0:
            from .channel import Channel
            channel = Channel.query.get(self.video.channel_id)
            if channel and channel.cpm:
                self.estimated_revenue = (self.view_change * float(channel.cpm)) / 1000
            else:
                self.estimated_revenue = 0.00
        else:
            self.estimated_revenue = 0.00
    
    @classmethod
    def create_video_data(cls, video_id, date, metrics):
        """创建视频数据记录"""
        video_data = cls(
            video_id=video_id,
            date=date,
            view_count=metrics.get('view_count', 0),
            like_count=metrics.get('like_count', 0),
            comment_count=metrics.get('comment_count', 0)
        )
        
        # 计算变化
        previous_data = cls.query.filter_by(video_id=video_id).order_by(cls.date.desc()).first()
        video_data.calculate_changes(previous_data)
        
        db.session.add(video_data)
        db.session.commit()
        return video_data