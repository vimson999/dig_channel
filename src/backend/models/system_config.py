from datetime import datetime
from . import db

class SystemConfig(db.Model):
    """
    通用系统配置表
    用于存储各环境(scope)下的业务配置(type)
    """
    __tablename__ = 'system_configs'
    
    id = db.Column(db.Integer, primary_key=True)
    
    # 核心配置键 (如: sessdata, refresh_token, max_duration)
    key = db.Column(db.String(100), nullable=False)
    
    # 配置值 (Text类型支持长文本，如长Token或JSON字符串)
    value = db.Column(db.Text, nullable=True)
    
    # 业务类型 (如: bilibili_auth, youtube_auth, video_params)
    type = db.Column(db.String(50), nullable=False, index=True, default='general')
    
    # 环境作用域 (如: production, development, testing)
    # 结合 unique_constraint 确保同一环境下 key 唯一
    scope = db.Column(db.String(20), nullable=False, default='production')
    
    # 状态 (1=启用, 0=禁用)
    status = db.Column(db.Integer, default=1)
    
    # 备注说明
    description = db.Column(db.String(255), nullable=True)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # 联合唯一索引：同一个环境下，Key必须唯一
    __table_args__ = (
        db.UniqueConstraint('key', 'scope', name='uq_config_key_scope'),
    )

    def to_dict(self):
        return {
            'id': self.id,
            'key': self.key,
            'value': self.value,
            'type': self.type,
            'scope': self.scope,
            'status': self.status,
            'description': self.description,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }

    def __repr__(self):
        return f'<SystemConfig {self.key} ({self.scope})>'
