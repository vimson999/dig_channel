"""Douyin configuration for the shared browser-publish job lifecycle."""

from functools import partial

from services.browser_publish_jobs import (
    QUEUE_NAME,
    BrowserPublishJobSpec,
    browser_publish_children,
    browser_publish_payload,
    cancel_browser_publish_task,
    clean_text,
    create_browser_publish_task,
    enqueue_browser_publish_task,
    normalize_tags,
)
from services.platform_credential_service import resolve_douyin_cookies


FEATURE_NAME = "douyin_upload_test"

SPEC = BrowserPublishJobSpec(
    platform="douyin",
    feature_name=FEATURE_NAME,
    platform_label="抖音",
    resolve_credential=resolve_douyin_cookies,
    allowed_visibility=frozenset({"private", "friends", "public"}),
    default_extra={"declaration": "none"},
    prevent_active_credential_overlap=True,
)

def create_douyin_publish_task(*, declaration="none", **kwargs):
    return create_browser_publish_task(SPEC, extra={"declaration": declaration}, **kwargs)


enqueue_douyin_publish_task = partial(enqueue_browser_publish_task, SPEC)
douyin_publish_payload = partial(browser_publish_payload, SPEC)
douyin_publish_children = partial(browser_publish_children, SPEC)
cancel_douyin_publish_task = partial(cancel_browser_publish_task, SPEC)
