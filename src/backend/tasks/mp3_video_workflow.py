import logging

from sqlalchemy.exc import OperationalError

from app import create_app
from celery_config import celery_app
from services.mp3_video_workflow import mark_job_failed_if_unfinished, mark_job_failed_safely, process_job


logger = logging.getLogger("MP3_VIDEO_WORKFLOW_TASK")


@celery_app.task(
    bind=True,
    name="tasks.mp3_video_workflow.process_job",
    store_errors_even_if_ignored=True,
    autoretry_for=(OperationalError,),
    retry_backoff=True,
    retry_jitter=True,
    retry_kwargs={"max_retries": 3},
)
def process_mp3_video_workflow_job(self, task_id):
    app = create_app()
    with app.app_context():
        try:
            result = process_job(task_id)
            if result is False:
                mark_job_failed_if_unfinished(task_id, "MP3 视频编排任务异常结束，未写入失败状态", notify=True)
            return result
        except Exception as exc:
            logger.error("MP3 video workflow failed: %s", exc, exc_info=True)
            mark_job_failed_safely(task_id, str(exc), notify=True)
            return False
