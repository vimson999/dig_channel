# 用户数据模型
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
from . import db
import secrets

class User(db.Model):
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    username = db.Column(db.String(80), nullable=False, unique=True)
    email = db.Column(db.String(120), nullable=False, unique=True)
    password_hash = db.Column(db.String(255), nullable=False)
    display_name = db.Column(db.String(100), nullable=True)
    avatar_url = db.Column(db.String(500), nullable=True)
    
    # 用户状态
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    is_verified = db.Column(db.Boolean, nullable=False, default=False)
    is_admin = db.Column(db.Boolean, nullable=False, default=False)
    
    # 用户偏好设置
    timezone = db.Column(db.String(50), nullable=False, default='UTC')
    language = db.Column(db.String(10), nullable=False, default='zh-CN')
    
    # 时间戳
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_login_at = db.Column(db.DateTime, nullable=True)
    
    # 关联关系
    channels = db.relationship('Channel', backref='user', lazy=True, cascade='all, delete-orphan')
    
    def __repr__(self):
        return f'<User {self.username}>'
    
    def set_password(self, password):
        """设置密码"""
        self.password_hash = generate_password_hash(password)
    
    def check_password(self, password):
        """验证密码"""
        return check_password_hash(self.password_hash, password)
    
    def update_last_login(self):
        """更新最后登录时间"""
        self.last_login_at = datetime.utcnow()
        db.session.commit()
    
    def to_dict(self, include_sensitive=False):
        """转换为字典"""
        data = {
            'id': self.id,
            'username': self.username,
            'email': self.email if include_sensitive else None,
            'display_name': self.display_name,
            'avatar_url': self.avatar_url,
            'is_active': self.is_active,
            'is_verified': self.is_verified,
            'is_admin': self.is_admin,
            'timezone': self.timezone,
            'language': self.language,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'last_login_at': self.last_login_at.isoformat() if self.last_login_at else None
        }
        return data
    
    def to_safe_dict(self):
        """转换为安全字典(不包含敏感信息)"""
        return self.to_dict(include_sensitive=False)
    
    @classmethod
    def create_user(cls, username, email, password, display_name=None):
        """创建用户"""
        # 检查用户名和邮箱是否已存在
        if cls.query.filter_by(username=username).first():
            raise ValueError('用户名已存在')
        
        if cls.query.filter_by(email=email).first():
            raise ValueError('邮箱已存在')
        
        user = cls(
            username=username,
            email=email,
            display_name=display_name or username
        )
        user.set_password(password)
        
        db.session.add(user)
        db.session.commit()
        return user
    
    @classmethod
    def authenticate(cls, username_or_email, password):
        """用户认证"""
        # 支持用户名或邮箱登录
        user = cls.query.filter(
            (cls.username == username_or_email) | 
            (cls.email == username_or_email)
        ).first()
        
        if user and user.check_password(password) and user.is_active:
            user.update_last_login()
            return user
        return None
    
    def has_permission(self, permission):
        """检查用户权限"""
        if self.is_admin:
            return True
        
        # 这里可以根据需要扩展权限系统
        permissions = {
            'manage_channels': True,  # 所有用户都可以管理自己的频道
            'view_data': True,       # 所有用户都可以查看自己的数据
            'export_data': True,     # 所有用户都可以导出自己的数据
        }
        
        return permissions.get(permission, False)
    
    def get_channel_count(self):
        """获取用户频道数量"""
        from .channel import Channel
        return Channel.query.filter_by(user_id=self.id, is_active=True).count()
    
    def get_total_subscribers(self):
        """获取用户所有频道的总订阅数"""
        from .channel import Channel
        from .channel_data import ChannelData
        from sqlalchemy import func
        from datetime import date
        
        result = db.session.query(func.sum(ChannelData.subscribers)).join(Channel).filter(
            Channel.user_id == self.id,
            Channel.is_active == True,
            ChannelData.date == date.today()
        ).scalar()
        
        return result or 0
    
    def get_total_views(self):
        """获取用户所有频道的总观看数"""
        from .channel import Channel
        from .channel_data import ChannelData
        from sqlalchemy import func
        from datetime import date
        
        result = db.session.query(func.sum(ChannelData.total_views)).join(Channel).filter(
            Channel.user_id == self.id,
            Channel.is_active == True,
            ChannelData.date == date.today()
        ).scalar()
        
        return result or 0