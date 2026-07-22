"""Douyin configuration for the shared browser-publish test API."""

from apis.v1.browser_publish_test import (
    BrowserPublishTestSpec,
    create_browser_publish_test_blueprint,
    upload_filename_metadata,
)
from services.douyin_publish_jobs import (
    FEATURE_NAME,
    cancel_douyin_publish_task,
    create_douyin_publish_task,
    douyin_publish_payload,
    enqueue_douyin_publish_task,
)


_upload_filename_metadata = upload_filename_metadata
bp = create_browser_publish_test_blueprint(BrowserPublishTestSpec(
    blueprint_name="douyin_publish_test",
    url_prefix="/api/v1/douyin-publish-test",
    feature_name=FEATURE_NAME,
    platform_label="抖音",
    upload_subdir="douyin_publish_test",
    max_upload_env="DOUYIN_TEST_MAX_UPLOAD_MB",
    create_task=create_douyin_publish_task,
    enqueue_task=enqueue_douyin_publish_task,
    task_payload=douyin_publish_payload,
    cancel_task=cancel_douyin_publish_task,
    allowed_visibility=frozenset({"private", "friends", "public"}),
    default_extra={"declaration": "none"},
))
