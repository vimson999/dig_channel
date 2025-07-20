# 收藏视频数据模型
from datetime import datetime
from . import db

class FavoriteVideo(db.Model):
    """用户收藏的视频模型"""
    __tablename__ = 'favorite_videos'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    
    # 视频基本信息
    video_id = db.Column(db.String(20), nullable=False, index=True)  # YouTube视频ID
    title = db.Column(db.Text, nullable=False)
    description = db.Column(db.Text, nullable=True)
    thumbnail_url = db.Column(db.String(500), nullable=True)
    duration = db.Column(db.String(20), nullable=True)  # ISO 8601 duration format
    
    # 频道信息
    channel_id = db.Column(db.String(30), nullable=False)
    channel_title = db.Column(db.String(200), nullable=False)
    channel_thumbnail_url = db.Column(db.String(500), nullable=True)
    channel_published_at = db.Column(db.DateTime, nullable=True)  # 频道创建时间
    
    # 统计数据 (收藏时的快照)
    view_count = db.Column(db.BigInteger, nullable=False, default=0)
    like_count = db.Column(db.BigInteger, nullable=False, default=0)
    comment_count = db.Column(db.BigInteger, nullable=False, default=0)
    
    # 频道统计数据
    channel_subscriber_count = db.Column(db.BigInteger, nullable=False, default=0)
    channel_video_count = db.Column(db.BigInteger, nullable=False, default=0)
    channel_view_count = db.Column(db.BigInteger, nullable=False, default=0)
    
    # 视频发布时间
    published_at = db.Column(db.DateTime, nullable=False)
    
    # 收藏时间
    favorited_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    
    # 热度评分 (收藏时计算)
    hot_score = db.Column(db.Float, nullable=False, default=0.0)
    
    # 用户备注
    note = db.Column(db.Text, nullable=True)
    
    # 标签 (JSON格式存储)
    tags = db.Column(db.Text, nullable=True)  # 存储JSON字符串
    
    # 关联关系
    user = db.relationship('User', backref='favorite_videos')
    
    # 唯一约束：每个用户对每个视频只能收藏一次
    __table_args__ = (
        db.UniqueConstraint('user_id', 'video_id', name='unique_user_video'),
        db.Index('idx_user_favorited', 'user_id', 'favorited_at'),
        db.Index('idx_video_id', 'video_id'),
    )
    
    def __repr__(self):
        return f'<FavoriteVideo {self.video_id} by user {self.user_id}>'
    
    def to_dict(self):
        """转换为字典"""
        return {
            'id': self.id,
            'video_id': self.video_id,
            'title': self.title,
            'description': self.description,
            'thumbnail_url': self.thumbnail_url,
            'duration': self.duration,
            'channel_id': self.channel_id,
            'channel_title': self.channel_title,
            'channel_thumbnail_url': self.channel_thumbnail_url,
            'channel_published_at': self.channel_published_at.isoformat() if self.channel_published_at else None,
            'view_count': self.view_count,
            'like_count': self.like_count,
            'comment_count': self.comment_count,
            'channel_subscriber_count': self.channel_subscriber_count,
            'channel_video_count': self.channel_video_count,
            'channel_view_count': self.channel_view_count,
            'published_at': self.published_at.isoformat() if self.published_at else None,
            'favorited_at': self.favorited_at.isoformat() if self.favorited_at else None,
            'hot_score': self.hot_score,
            'note': self.note,
            'tags': self.tags
        }
    
    @classmethod
    def create_favorite(cls, user_id, video_data):
        """创建收藏记录"""
        # 检查是否已经收藏
        existing = cls.query.filter_by(user_id=user_id, video_id=video_data['video_id']).first()
        if existing:
            return existing, False  # 已存在
        
        # 计算热度评分
        from ..utils.youtube_api import calculate_hot_score  # 假设有这个函数
        hot_score = calculate_hot_score(
            video_data.get('view_count', 0),
            video_data.get('like_count', 0),
            video_data.get('comment_count', 0)
        )
        
        # 解析发布时间
        published_at = None
        if video_data.get('published_at'):
            try:
                if isinstance(video_data['published_at'], str):
                    # 处理ISO格式时间字符串
                    published_at = datetime.fromisoformat(video_data['published_at'].replace('Z', '+00:00'))
                else:
                    published_at = video_data['published_at']
            except:
                published_at = datetime.utcnow()
        
        # 解析频道创建时间
        channel_published_at = None
        if video_data.get('channel_published_at'):
            try:
                if isinstance(video_data['channel_published_at'], str):
                    channel_published_at = datetime.fromisoformat(video_data['channel_published_at'].replace('Z', '+00:00'))
                else:
                    channel_published_at = video_data['channel_published_at']
            except:
                pass
        
        favorite = cls(
            user_id=user_id,
            video_id=video_data['video_id'],
            title=video_data.get('title', ''),
            description=video_data.get('description', ''),
            thumbnail_url=video_data.get('thumbnail_url'),
            duration=video_data.get('duration'),
            channel_id=video_data.get('channel_id', ''),
            channel_title=video_data.get('channel_title', ''),
            channel_thumbnail_url=video_data.get('channel_thumbnail_url'),
            channel_published_at=channel_published_at,
            view_count=int(video_data.get('view_count', 0)),
            like_count=int(video_data.get('like_count', 0)),
            comment_count=int(video_data.get('comment_count', 0)),
            channel_subscriber_count=int(video_data.get('channel_subscriber_count', 0)),
            channel_video_count=int(video_data.get('channel_video_count', 0)),
            channel_view_count=int(video_data.get('channel_view_count', 0)),
            published_at=published_at or datetime.utcnow(),
            hot_score=hot_score
        )
        
        db.session.add(favorite)
        db.session.commit()
        return favorite, True  # 新创建
    
    @classmethod
    def get_user_favorites(cls, user_id, limit=None, offset=None):
        """获取用户收藏列表"""
        query = cls.query.filter_by(user_id=user_id).order_by(cls.favorited_at.desc())
        
        if limit:
            query = query.limit(limit)
        if offset:
            query = query.offset(offset)
            
        return query.all()
    
    @classmethod
    def get_user_favorite_count(cls, user_id):
        """获取用户收藏数量"""
        return cls.query.filter_by(user_id=user_id).count()
    
    @classmethod
    def is_favorited(cls, user_id, video_id):
        """检查视频是否已被收藏"""
        return cls.query.filter_by(user_id=user_id, video_id=video_id).first() is not None
    
    @classmethod
    def remove_favorite(cls, user_id, video_id):
        """移除收藏"""
        favorite = cls.query.filter_by(user_id=user_id, video_id=video_id).first()
        if favorite:
            db.session.delete(favorite)
            db.session.commit()
            return True
        return False


def calculate_hot_score(view_count=0, like_count=0, comment_count=0):
    """计算热度评分"""
    import math
    
    # 归一化权重：播放量(50%) + 点赞(30%) + 评论(20%)
    # 使用对数函数平滑大数值差异
    normalized_views = math.log10(max(view_count, 1))
    normalized_likes = math.log10(max(like_count, 1))
    normalized_comments = math.log10(max(comment_count, 1))
    
    # 加权计算，最高分100
    score = (normalized_views * 0.5 + normalized_likes * 0.3 + normalized_comments * 0.2) * 10
    
    # 限制在0-100范围内
    return min(max(score, 0), 100)