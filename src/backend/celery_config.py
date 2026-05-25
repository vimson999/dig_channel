import os
import sys
from celery import Celery
from kombu import Exchange, Queue
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), '.env'))

# Ensure backend directory is in sys.path
backend_dir = os.path.dirname(os.path.abspath(__file__))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

def make_celery(app_name=__name__):
    redis_url = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
    broker_url = os.environ.get('CELERY_BROKER_URL', redis_url)
    backend_url = os.environ.get('CELERY_RESULT_BACKEND', redis_url)
    
    celery = Celery(
        app_name,
        backend=backend_url,
        broker=broker_url,
        include=['tasks.publisher', 'tasks.downloader', 'tasks.podcast_visualizer', 'tasks.tts'] # Auto-discover tasks
    )
    
    celery.conf.update(
        result_expires=3600,
        task_serializer='json',
        accept_content=['json'],
        result_serializer='json',
        timezone='Asia/Shanghai',
        enable_utc=True,
        task_default_queue='render',
        task_queues=(
            Queue('render', Exchange('render'), routing_key='render'),
            Queue('cloud_tts', Exchange('cloud_tts'), routing_key='cloud_tts'),
        ),
        task_routes={
            'tts.process_doubao_publication': {'queue': 'cloud_tts'},
            'tts.process_article': {'queue': 'cloud_tts'},
            'tasks.publisher.run_publication_pipeline': {'queue': 'render'},
            'tasks.publisher.run_youtube_upload_only': {'queue': 'render'},
            'tasks.podcast_visualizer.run_podcast_visualizer_task': {'queue': 'render'},
            'tasks.downloader.download_video_task': {'queue': 'render'},
        },
    )
    
    return celery

celery_app = make_celery('youtube_tracker_worker')
