import os
import sys
from celery import Celery
from celery.signals import task_failure, task_postrun, task_retry
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
    broker_socket_timeout = int(os.environ.get('CELERY_BROKER_SOCKET_TIMEOUT', '30'))
    broker_connect_timeout = int(os.environ.get('CELERY_BROKER_CONNECT_TIMEOUT', '5'))
    broker_health_check_interval = int(os.environ.get('CELERY_BROKER_HEALTH_CHECK_INTERVAL', '30'))
    broker_visibility_timeout = int(os.environ.get('CELERY_BROKER_VISIBILITY_TIMEOUT', '3600'))
    
    celery = Celery(
        app_name,
        backend=backend_url,
        broker=broker_url,
        include=[
            'tasks.publisher',
            'tasks.downloader',
            'tasks.podcast_visualizer',
            'tasks.tts',
            'tasks.doubao_browser_tts',
            'tasks.gemini_browser',
            'tasks.report_audio_workflow',
            'tasks.mp3_video_workflow',
            'tasks.zsxq_sync',
            'tasks.zsxq_workflow',
            'tasks.workflow_notify',
            'tasks.zsxq_publish',
            'tasks.report_video',
            'tasks.content_pipeline',
        ] # Auto-discover tasks
    )
    
    celery.conf.update(
        result_expires=3600,
        task_serializer='json',
        accept_content=['json'],
        result_serializer='json',
        timezone='Asia/Shanghai',
        enable_utc=True,
        # Business task state is stored in PostgreSQL. Avoid writing Celery
        # result metadata to remote Redis after long-running jobs, because
        # backend socket timeouts can make completed tasks look failed.
        task_ignore_result=True,
        task_store_errors_even_if_ignored=False,
        result_backend_transport_options={
            'socket_connect_timeout': 5,
            'socket_timeout': 5,
            'retry_on_timeout': False,
        },
        # These jobs are long-running and tracked in PostgreSQL. A worker may
        # crash or be killed during FFmpeg/ASR work, so do not ack a task until
        # it actually finishes. Also avoid prefetching multiple long jobs.
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_cancel_long_running_tasks_on_connection_loss=False,
        worker_prefetch_multiplier=1,
        broker_connection_timeout=broker_connect_timeout,
        broker_connection_retry=True,
        broker_connection_retry_on_startup=True,
        broker_transport_options={
            'socket_connect_timeout': broker_connect_timeout,
            'socket_timeout': broker_socket_timeout,
            'retry_on_timeout': True,
            'health_check_interval': broker_health_check_interval,
            'visibility_timeout': broker_visibility_timeout,
        },
        task_publish_retry=False,
        task_default_queue='render',
        task_queues=(
            Queue('render', Exchange('render'), routing_key='render'),
            Queue('cloud_tts', Exchange('cloud_tts'), routing_key='cloud_tts'),
            Queue('doubao_browser_tts', Exchange('doubao_browser_tts'), routing_key='doubao_browser_tts'),
            Queue('report_pdf_gemini', Exchange('report_pdf_gemini'), routing_key='report_pdf_gemini'),
            Queue('report_audio_workflow', Exchange('report_audio_workflow'), routing_key='report_audio_workflow'),
            Queue('mp3_video_workflow', Exchange('mp3_video_workflow'), routing_key='mp3_video_workflow'),
            Queue('zsxq_workflow', Exchange('zsxq_workflow'), routing_key='zsxq_workflow'),
            Queue('zsxq_sync', Exchange('zsxq_sync'), routing_key='zsxq_sync'),
            Queue('zsxq_publish', Exchange('zsxq_publish'), routing_key='zsxq_publish'),
            Queue('podcast_visualizer', Exchange('podcast_visualizer'), routing_key='podcast_visualizer'),
            Queue('report_video', Exchange('report_video'), routing_key='report_video'),
            Queue('content_pipeline', Exchange('content_pipeline'), routing_key='content_pipeline'),
        ),
        task_routes={
            'tts.process_doubao_publication': {'queue': 'cloud_tts'},
            'tts.process_article': {'queue': 'cloud_tts'},
            'tasks.doubao_browser_tts.process_job': {'queue': 'doubao_browser_tts'},
            'tasks.report_pdf_gemini.process_job': {'queue': 'report_pdf_gemini'},
            'tasks.gemini_browser.process_job': {'queue': 'report_pdf_gemini'},
            'tasks.report_audio_workflow.process_job': {'queue': 'report_audio_workflow'},
            'tasks.mp3_video_workflow.process_job': {'queue': 'mp3_video_workflow'},
            'tasks.zsxq_workflow.run_zsxq_workflow': {'queue': 'zsxq_workflow'},
            'tasks.zsxq_workflow.advance_zsxq_workflow': {'queue': 'zsxq_workflow'},
            'tasks.zsxq_workflow.finalize_zsxq_sync_stage': {'queue': 'zsxq_workflow'},
            'tasks.workflow_notify.send_workflow_notification': {'queue': 'zsxq_workflow'},
            'tasks.zsxq_sync.run_zsxq_sync': {'queue': 'zsxq_sync'},
            'tasks.zsxq_publish.run_publish_clean_output_task': {'queue': 'zsxq_publish'},
            'tasks.report_video.parse_report_pdf_task': {'queue': 'report_video'},
            'tasks.report_video.generate_ai_scripts_task': {'queue': 'report_video'},
            'tasks.report_video.generate_videos_task': {'queue': 'report_video'},
            'tasks.report_video.generate_topic_cards_task': {'queue': 'report_video'},
            'tasks.report_video.generate_topic_clusters_task': {'queue': 'report_video'},
            'tasks.report_video.generate_topic_video_script_task': {'queue': 'report_video'},
            'tasks.report_video.process_audio_timeline_task': {'queue': 'report_video'},
            'tasks.report_video.auto_process_report_task': {'queue': 'report_video'},
            'tasks.report_video.scan_report_video_folder_task': {'queue': 'report_video'},
            'tasks.report_video.scan_zsxq_report_assets_task': {'queue': 'report_video'},
            'tasks.content_pipeline.run_pdf_clean_task': {'queue': 'content_pipeline'},
            'tasks.content_pipeline.run_audio_clean_task': {'queue': 'content_pipeline'},
            'tasks.publisher.run_publication_pipeline': {'queue': 'render'},
            'tasks.publisher.run_youtube_upload_only': {'queue': 'render'},
            'tasks.podcast_visualizer.run_podcast_visualizer_task': {'queue': 'podcast_visualizer'},
            'tasks.downloader.download_video_task': {'queue': 'render'},
        },
    )
    
    return celery

celery_app = make_celery('youtube_tracker_worker')


def _cleanup_sqlalchemy_session(**_kwargs):
    try:
        from models import db

        db.session.remove()
    except Exception:
        pass


task_postrun.connect(_cleanup_sqlalchemy_session, weak=False)
task_failure.connect(_cleanup_sqlalchemy_session, weak=False)
task_retry.connect(_cleanup_sqlalchemy_session, weak=False)
