from datetime import datetime
import uuid
from . import db

class PublicationTask(db.Model):
    __tablename__ = 'publication_tasks'

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    celery_task_id = db.Column(db.String(50), nullable=True, index=True)
    
    # 任务状态: pending, processing, completed, failed
    status = db.Column(db.String(20), default='pending', index=True)
    
    # 当前步骤: init, synthesis, thumbnail, upload, done
    current_step = db.Column(db.String(20), default='init')
    
    # 进度百分比 (0-100)
    progress = db.Column(db.Integer, default=0)
    
    # 用户提交的原始参数 (视频配置、封面配置、B站配置)
    # 使用 JSON 类型以兼容 SQLite (开发环境) 和 PostgreSQL
    params = db.Column(db.JSON, nullable=False)
    
    # 执行结果 (生成的文件路径、B站链接等)
    result = db.Column(db.JSON, default=dict)
    
    # 错误信息
    error_message = db.Column(db.Text, nullable=True)
    
    # 时间戳
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    completed_at = db.Column(db.DateTime, nullable=True)

    def to_dict(self):
        return {
            'id': self.id,
            'celery_task_id': self.celery_task_id,
            'status': self.status,
            'current_step': self.current_step,
            'progress': self.progress,
            'params': self.params,
            'result': self.result,
            'error_message': self.error_message,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None
        }
