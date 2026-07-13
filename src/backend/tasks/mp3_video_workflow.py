import logging

from sqlalchemy.exc import OperationalError

from app import create_app
from celery_config import celery_app
from models import db
from services.runtime_guard import (
    guard_publication_task_start,
    is_retryable_db_error,
    release_db_session_quietly,
    retry_exception_types,
)
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
            guard_publication_task_start(
                self,
                task_id,
                feature="mp3_video_workflow",
                queue="mp3_video_workflow",
            )
            result = process_job(task_id)
            if result is False:
                mark_job_failed_if_unfinished(task_id, "MP3 视频编排任务异常结束，未写入失败状态", notify=True)
            return result
        except retry_exception_types():
            raise
        except Exception as exc:
            if is_retryable_db_error(exc):
                logger.warning("MP3 video workflow database connection failed; retrying: %s", exc)
                release_db_session_quietly()
                raise self.retry(exc=exc, countdown=15, max_retries=3)
            logger.error("MP3 video workflow failed: %s", exc, exc_info=True)
            mark_job_failed_safely(task_id, str(exc), notify=True)
            return False
        finally:
            db.session.remove()
