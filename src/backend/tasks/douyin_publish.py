"""Douyin adapter for the shared browser-publish Celery executor."""

from celery_config import celery_app
from services.douyin_publish import (
    DouyinAuthenticationRequired,
    DouyinBrowserError,
    DouyinSubmissionPendingVerification,
    DouyinVerificationRequired,
    douyin_publish_execution_guard,
    publish_douyin_video,
)
from services.douyin_publish_jobs import FEATURE_NAME, QUEUE_NAME
from services.platform_credential_service import (
    mark_credential_error,
    mark_credential_success,
    resolve_douyin_cookies,
)
from services.runtime_guard import guard_publication_task_start
from tasks.browser_publish import BrowserPublishTaskSpec, run_browser_publish_job


@celery_app.task(bind=True, name="tasks.douyin_publish.process_job")
def process_job(self, job_id):
    spec = BrowserPublishTaskSpec(
        feature_name=FEATURE_NAME, queue_name=QUEUE_NAME, platform_label="抖音",
        resolve_credential=resolve_douyin_cookies, publish=publish_douyin_video,
        authentication_error=DouyinAuthenticationRequired,
        verification_error=DouyinVerificationRequired,
        browser_error=DouyinBrowserError,
        guard_start=guard_publication_task_start,
        mark_success=mark_credential_success,
        mark_error=mark_credential_error,
        extra_command_keys=("declaration",),
        execution_guard=douyin_publish_execution_guard,
        include_credential_context=True,
        submission_pending_error=DouyinSubmissionPendingVerification,
    )
    return run_browser_publish_job(self, job_id, spec)
