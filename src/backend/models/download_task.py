from datetime import datetime
import uuid
from . import db

class DownloadTask(db.Model):
    __tablename__ = 'download_tasks'

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    celery_task_id = db.Column(db.String(50), nullable=True, index=True)
    
    # 原始 URL
    url = db.Column(db.String(500), nullable=False)
    
    # 视频信息
    title = db.Column(db.String(255), nullable=True)
    thumbnail = db.Column(db.String(500), nullable=True)
    duration = db.Column(db.Integer, nullable=True) # 秒
    
    # 任务状态: pending, downloading, completed, failed
    status = db.Column(db.String(20), default='pending', index=True)
    
    # 进度百分比 (0-100)
    progress = db.Column(db.Float, default=0.0)
    
    # 文件信息
    filename = db.Column(db.String(255), nullable=True)
    filepath = db.Column(db.String(500), nullable=True)
    file_size = db.Column(db.BigInteger, nullable=True) # 字节
    
    # 错误信息
    error_message = db.Column(db.Text, nullable=True)
    
    # 时间戳
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    completed_at = db.Column(db.DateTime, nullable=True)

    def to_dict(self):
        return {
            'id': self.id,
            'url': self.url,
            'title': self.title or self.url,
            'thumbnail': self.thumbnail,
            'duration': self.duration,
            'status': self.status,
            'progress': self.progress,
            'filename': self.filename,
            'filepath': self.filepath,
            'file_size': self.file_size,
            'error_message': self.error_message,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None
        }
