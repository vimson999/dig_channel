"""WeChat Channels configuration for the shared browser-publish job lifecycle."""

from functools import partial

from services.browser_publish_jobs import (
    QUEUE_NAME,
    BrowserPublishJobSpec,
    browser_publish_children,
    browser_publish_payload,
    cancel_browser_publish_task,
    create_browser_publish_task,
    enqueue_browser_publish_task,
)
from services.platform_credential_service import resolve_wechat_channels_cookies


FEATURE_NAME = "wechat_channels_upload_test"

SPEC = BrowserPublishJobSpec(
    platform="wechat_channels",
    feature_name=FEATURE_NAME,
    platform_label="视频号",
    resolve_credential=resolve_wechat_channels_cookies,
    allowed_visibility=frozenset({"private", "public"}),
    title_min_chars=6,
)

create_wechat_channels_publish_task = partial(create_browser_publish_task, SPEC)
enqueue_wechat_channels_publish_task = partial(enqueue_browser_publish_task, SPEC)
wechat_channels_publish_payload = partial(browser_publish_payload, SPEC)
wechat_channels_publish_children = partial(browser_publish_children, SPEC)
cancel_wechat_channels_publish_task = partial(cancel_browser_publish_task, SPEC)
