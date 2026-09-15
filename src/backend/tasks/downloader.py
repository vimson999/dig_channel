import os
import logging
from celery_config import celery_app
from app import create_app
from models import db, DownloadTask
from services.download_service import download_service

logger = logging.getLogger(__name__)

@celery_app.task(bind=True)
def download_video_task(self, task_id, url, download_type='video'):
    """
    Celery task to download a video.
    """
    from app import create_app
    flask_app = create_app(register_apis=False)
    with flask_app.app_context():
        try:
            # Update task with Celery ID
            task = DownloadTask.query.get(task_id)
            if task:
                if task.status == 'cancelled':
                    return {'status': 'cancelled', 'task_id': task_id}
                task.celery_task_id = self.request.id
                task.status = 'downloading'
                db.session.commit()
            
            # Start download
            download_service.download(task_id, url, download_type)
            return {'status': 'completed', 'task_id': task_id}
            
        except Exception as e:
            logger.error(f"Celery download task failed: {e}")
            return {'status': 'failed', 'error': str(e)}
