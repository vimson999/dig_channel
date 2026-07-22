"""WeChat Channels adapter for the shared browser-publish Celery executor."""

from celery_config import celery_app
from services.platform_credential_service import (
    mark_credential_error,
    mark_credential_success,
    resolve_wechat_channels_cookies,
)
from services.runtime_guard import guard_publication_task_start
from services.wechat_channels_publish import (
    WechatChannelsAuthenticationRequired,
    WechatChannelsBrowserError,
    WechatChannelsVerificationRequired,
    publish_wechat_channels_video,
)
from services.wechat_channels_publish_jobs import FEATURE_NAME, QUEUE_NAME
from tasks.browser_publish import BrowserPublishTaskSpec, run_browser_publish_job


@celery_app.task(bind=True, name="tasks.wechat_channels_publish.process_job")
def process_job(self, job_id):
    spec = BrowserPublishTaskSpec(
        feature_name=FEATURE_NAME, queue_name=QUEUE_NAME, platform_label="视频号",
        resolve_credential=resolve_wechat_channels_cookies,
        publish=publish_wechat_channels_video,
        authentication_error=WechatChannelsAuthenticationRequired,
        verification_error=WechatChannelsVerificationRequired,
        browser_error=WechatChannelsBrowserError,
        guard_start=guard_publication_task_start,
        mark_success=mark_credential_success,
        mark_error=mark_credential_error,
    )
    return run_browser_publish_job(self, job_id, spec)
