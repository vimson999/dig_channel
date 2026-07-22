"""WeChat Channels configuration for the shared browser-publish test API."""

from apis.v1.browser_publish_test import (
    BrowserPublishTestSpec,
    create_browser_publish_test_blueprint,
    upload_filename_metadata,
)
from services.wechat_channels_publish_jobs import (
    FEATURE_NAME,
    cancel_wechat_channels_publish_task,
    create_wechat_channels_publish_task,
    enqueue_wechat_channels_publish_task,
    wechat_channels_publish_payload,
)


_upload_filename_metadata = upload_filename_metadata
bp = create_browser_publish_test_blueprint(BrowserPublishTestSpec(
    blueprint_name="wechat_channels_publish_test",
    url_prefix="/api/v1/wechat-channels-publish-test",
    feature_name=FEATURE_NAME,
    platform_label="视频号",
    upload_subdir="wechat_channels_publish_test",
    max_upload_env="WECHAT_CHANNELS_TEST_MAX_UPLOAD_MB",
    create_task=create_wechat_channels_publish_task,
    enqueue_task=enqueue_wechat_channels_publish_task,
    task_payload=wechat_channels_publish_payload,
    cancel_task=cancel_wechat_channels_publish_task,
    allowed_visibility=frozenset({"private", "public"}),
    title_min_chars=6,
))
