import os
import sys
from celery import Celery
from celery.signals import task_failure, task_postrun, task_retry, worker_ready
from kombu import Exchange, Queue
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), '.env'))

# Ensure backend directory is in sys.path
backend_dir = os.path.dirname(os.path.abspath(__file__))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)


def _int_env(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _broker_pool_limit(default):
    raw = os.environ.get('CELERY_BROKER_POOL_LIMIT')
    if raw is None:
        return default
    text = str(raw).strip().lower()
    if text in {'none', 'null'}:
        return None
    try:
        return int(text)
    except (TypeError, ValueError):
        return default


def make_celery(app_name=__name__):
    redis_url = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
    broker_url = os.environ.get('CELERY_BROKER_URL', redis_url)
    backend_url = os.environ.get('CELERY_RESULT_BACKEND', redis_url)
    process_role = str(os.environ.get('DB_PROCESS_ROLE') or '').strip().lower()
    api_like_process = process_role in {'web', 'scheduler'}
    broker_socket_timeout = _int_env('CELERY_BROKER_SOCKET_TIMEOUT', 5 if api_like_process else 30)
    broker_connect_timeout = _int_env('CELERY_BROKER_CONNECT_TIMEOUT', 5)
    broker_health_check_interval = _int_env('CELERY_BROKER_HEALTH_CHECK_INTERVAL', 30)
    broker_visibility_timeout = _int_env('CELERY_BROKER_VISIBILITY_TIMEOUT', 3600)
    broker_pool_limit = _broker_pool_limit(0 if api_like_process else 10)
    
    celery = Celery(
        app_name,
        backend=backend_url,
        broker=broker_url,
        include=[
            'tasks.audio_transcription',
            'tasks.publisher',
            'tasks.downloader',
            'tasks.podcast_visualizer',
            'tasks.tts',
            'tasks.doubao_browser_tts',
            'tasks.gemini_browser',
            'tasks.report_audio_workflow',
            'tasks.report_script_material_workflow',
            'tasks.report_visual_plan',
            'tasks.report_stick_figure_assets',
            'tasks.report_stick_figure_video',
            'tasks.script_video_workflow',
            'tasks.report_top10_workflow',
            'tasks.mp3_video_workflow',
            'tasks.zsxq_sync',
            'tasks.zsxq_browser_import',
            'tasks.zsxq_workflow',
            'tasks.workflow_notify',
            'tasks.zsxq_publish',
            'tasks.report_video',
            'tasks.content_pipeline',
            'tasks.pdf_merge_workflow',
            'tasks.external_documents',
            'tasks.douyin_publish',
            'tasks.wechat_channels_publish',
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
        broker_pool_limit=broker_pool_limit,
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
            Queue('platform_upload', Exchange('platform_upload'), routing_key='platform_upload'),
            Queue('cloud_tts', Exchange('cloud_tts'), routing_key='cloud_tts'),
            Queue('doubao_browser_tts', Exchange('doubao_browser_tts'), routing_key='doubao_browser_tts'),
            Queue('report_pdf_gemini', Exchange('report_pdf_gemini'), routing_key='report_pdf_gemini'),
            Queue('report_audio_workflow', Exchange('report_audio_workflow'), routing_key='report_audio_workflow'),
            Queue('report_script_material_workflow', Exchange('report_script_material_workflow'), routing_key='report_script_material_workflow'),
            Queue('report_visual_plan', Exchange('report_visual_plan'), routing_key='report_visual_plan'),
            Queue('report_top10_workflow', Exchange('report_top10_workflow'), routing_key='report_top10_workflow'),
            Queue('mp3_video_workflow', Exchange('mp3_video_workflow'), routing_key='mp3_video_workflow'),
            Queue('zsxq_workflow', Exchange('zsxq_workflow'), routing_key='zsxq_workflow'),
            Queue('workflow_notify', Exchange('workflow_notify'), routing_key='workflow_notify'),
            Queue('zsxq_sync', Exchange('zsxq_sync'), routing_key='zsxq_sync'),
            Queue('zsxq_publish', Exchange('zsxq_publish'), routing_key='zsxq_publish'),
            Queue('podcast_visualizer', Exchange('podcast_visualizer'), routing_key='podcast_visualizer'),
            Queue('report_video', Exchange('report_video'), routing_key='report_video'),
            Queue('content_pipeline', Exchange('content_pipeline'), routing_key='content_pipeline'),
        ),
        task_routes={
            'tasks.audio_transcription.process_job': {'queue': 'report_audio_workflow'},
            'tts.process_doubao_publication': {'queue': 'cloud_tts'},
            'tts.process_article': {'queue': 'cloud_tts'},
            'tasks.doubao_browser_tts.process_job': {'queue': 'doubao_browser_tts'},
            'tasks.report_pdf_gemini.process_job': {'queue': 'report_pdf_gemini'},
            'tasks.gemini_browser.process_job': {'queue': 'report_pdf_gemini'},
            'tasks.report_audio_workflow.process_job': {'queue': 'report_audio_workflow'},
            'tasks.report_script_material_workflow.process_job': {'queue': 'report_script_material_workflow'},
            'tasks.report_visual_plan.process_job': {'queue': 'report_visual_plan'},
            'tasks.report_stick_figure_assets.generate_scene_image': {'queue': 'report_pdf_gemini'},
            'tasks.report_stick_figure_video.align_whole_audio': {'queue': 'render'},
            'tasks.report_stick_figure_video.render_video': {'queue': 'render'},
            'tasks.script_video_workflow.process_audio': {'queue': 'report_audio_workflow'},
            'tasks.script_video_workflow.prepare_uploaded_audio': {'queue': 'report_audio_workflow'},
            'tasks.script_video_workflow.finalize_audio_edit': {'queue': 'report_audio_workflow'},
            'tasks.script_video_workflow.generate_storyboard': {'queue': 'report_pdf_gemini'},
            'tasks.script_video_workflow.generate_image': {'queue': 'report_pdf_gemini'},
            'tasks.script_video_workflow.render_video': {'queue': 'render'},
            'tasks.report_top10_workflow.process_job': {'queue': 'report_top10_workflow'},
            'tasks.mp3_video_workflow.process_job': {'queue': 'mp3_video_workflow'},
            'tasks.zsxq_workflow.run_zsxq_workflow': {'queue': 'zsxq_workflow'},
            'tasks.zsxq_workflow.advance_zsxq_workflow': {'queue': 'zsxq_workflow'},
            'tasks.zsxq_workflow.finalize_zsxq_sync_stage': {'queue': 'zsxq_workflow'},
            'tasks.workflow_notify.send_workflow_notification': {'queue': 'workflow_notify'},
            'tasks.zsxq_sync.run_zsxq_sync': {'queue': 'zsxq_sync'},
            'tasks.zsxq_browser_import.run_browser_import': {'queue': 'zsxq_sync'},
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
            'tasks.pdf_merge_workflow.process_job': {'queue': 'content_pipeline'},
            'tasks.external_documents.run_backfill': {'queue': 'content_pipeline'},
            'tasks.publisher.run_publication_pipeline': {'queue': 'render'},
            'tasks.publisher.run_youtube_upload_only': {'queue': 'platform_upload'},
            'tasks.publisher.run_bilibili_upload_only': {'queue': 'platform_upload'},
            'tasks.douyin_publish.process_job': {'queue': 'platform_upload'},
            'tasks.wechat_channels_publish.process_job': {'queue': 'platform_upload'},
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


def _publish_worker_readiness(**_kwargs):
    queues = [
        item.strip()
        for item in str(os.environ.get("WORKFLOW_WORKER_READY_QUEUES") or "").split(",")
        if item.strip()
    ]
    if not queues:
        return
    try:
        from services.workflow_worker_readiness import record_worker_readiness

        worker_id = str(
            os.environ.get("DOUBAO_BROWSER_TTS_WORKER_ID")
            or os.environ.get("REPORT_PDF_GEMINI_WORKER_ID")
            or os.environ.get("HOSTNAME")
            or "default"
        )
        for queue in queues:
            readiness = record_worker_readiness(queue, worker_id=worker_id)
            print(
                "[WORKER_READINESS] "
                f"queue={queue} worker={worker_id} revision={readiness['revision']}",
                flush=True,
            )
    except Exception as exc:
        print(f"[WORKER_READINESS] failed to publish readiness: {exc}", flush=True)


worker_ready.connect(_publish_worker_readiness, weak=False)
