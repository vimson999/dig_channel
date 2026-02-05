import os
import sys
from celery import Celery

# Ensure backend directory is in sys.path
backend_dir = os.path.dirname(os.path.abspath(__file__))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

def make_celery(app_name=__name__):
    redis_url = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
    
    celery = Celery(
        app_name,
        backend=redis_url,
        broker=redis_url,
        include=['tasks.publisher'] # Auto-discover tasks
    )
    
    celery.conf.update(
        result_expires=3600,
        task_serializer='json',
        accept_content=['json'],
        result_serializer='json',
        timezone='Asia/Shanghai',
        enable_utc=True,
    )
    
    return celery

celery_app = make_celery('youtube_tracker_worker')
