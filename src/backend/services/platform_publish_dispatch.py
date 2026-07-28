"""全平台异步发布的注册表与任务分发门面。

本模块只统一“任务应该交给谁、进入哪个队列、是否需要提交准入”。
各平台的参数校验、命令持久化、凭据解析和远端发布步骤仍由平台适配器负责，
避免把互不相同的内容模型塞进一个条件分支密集的万能发布器。
"""

from dataclasses import dataclass
from typing import Any, Callable, Dict, FrozenSet, Optional


PLATFORM_UPLOAD_QUEUE = "platform_upload"
ZSXQ_PUBLISH_QUEUE = "zsxq_publish"


@dataclass(frozen=True)
class PlatformPublishRoute:
    """描述一个发布平台的稳定调度契约。"""

    platform: str
    label: str
    queue: str
    runtime_feature: str
    task_features: FrozenSet[str]
    task_loader: Callable[[], Any]
    content_kind: str
    transport: str
    guarded_submission: bool = False
    aliases: FrozenSet[str] = frozenset()


def _load_bilibili_task():
    from tasks.publisher import run_bilibili_upload_only

    return run_bilibili_upload_only


def _load_youtube_task():
    from tasks.publisher import run_youtube_upload_only

    return run_youtube_upload_only


def _load_douyin_task():
    from tasks.douyin_publish import process_job

    return process_job


def _load_wechat_channels_task():
    from tasks.wechat_channels_publish import process_job

    return process_job


def _load_zsxq_task():
    from tasks.zsxq_publish import run_publish_clean_output_task

    return run_publish_clean_output_task


_ROUTES = (
    PlatformPublishRoute(
        platform="bilibili",
        label="B站",
        queue=PLATFORM_UPLOAD_QUEUE,
        runtime_feature="publisher_upload",
        task_features=frozenset({"publisher"}),
        task_loader=_load_bilibili_task,
        content_kind="video",
        transport="sdk",
        aliases=frozenset({"bili", "b站"}),
    ),
    PlatformPublishRoute(
        platform="youtube",
        label="YouTube",
        queue=PLATFORM_UPLOAD_QUEUE,
        runtime_feature="publisher_upload",
        task_features=frozenset({"publisher"}),
        task_loader=_load_youtube_task,
        content_kind="video",
        transport="api",
        aliases=frozenset({"yt"}),
    ),
    PlatformPublishRoute(
        platform="douyin",
        label="抖音",
        queue=PLATFORM_UPLOAD_QUEUE,
        runtime_feature="douyin_upload_test",
        task_features=frozenset({"douyin_upload_test"}),
        task_loader=_load_douyin_task,
        content_kind="video",
        transport="browser",
        aliases=frozenset({"抖音"}),
    ),
    PlatformPublishRoute(
        platform="wechat_channels",
        label="视频号",
        queue=PLATFORM_UPLOAD_QUEUE,
        runtime_feature="wechat_channels_upload_test",
        task_features=frozenset({"wechat_channels_upload_test"}),
        task_loader=_load_wechat_channels_task,
        content_kind="video",
        transport="browser",
        aliases=frozenset({"channels", "wechat_channel", "视频号"}),
    ),
    PlatformPublishRoute(
        platform="zsxq",
        label="知识星球",
        queue=ZSXQ_PUBLISH_QUEUE,
        runtime_feature="zsxq_clean_output_publish",
        task_features=frozenset({"zsxq_clean_output_publish"}),
        task_loader=_load_zsxq_task,
        content_kind="topic_attachments",
        transport="api",
        guarded_submission=True,
        aliases=frozenset({"知识星球"}),
    ),
)


def _build_route_index() -> Dict[str, PlatformPublishRoute]:
    index: Dict[str, PlatformPublishRoute] = {}
    for route in _ROUTES:
        for key in {route.platform, *route.aliases}:
            normalized = str(key).strip().lower()
            if normalized in index:
                raise RuntimeError(f"重复的发布平台别名: {key}")
            index[normalized] = route
    return index


_ROUTE_INDEX = _build_route_index()


def platform_publish_routes():
    """返回不可变的平台路由集合，供监控和诊断读取。"""

    return _ROUTES


def get_platform_publish_route(platform: str) -> PlatformPublishRoute:
    """按稳定 ID 或别名取得发布路由。"""

    normalized = str(platform or "").strip().lower()
    route = _ROUTE_INDEX.get(normalized)
    if route is None:
        supported = ", ".join(item.platform for item in _ROUTES)
        raise ValueError(f"不支持的发布平台: {platform!r}；可用平台: {supported}")
    return route


def route_for_unique_feature(feature: str) -> Optional[PlatformPublishRoute]:
    """只在 feature 唯一指向一个平台时返回路由。

    `publisher` 同时承载 B站和 YouTube，不能仅凭 feature 猜测平台，因此返回 None。
    """

    normalized = str(feature or "").strip()
    matches = [route for route in _ROUTES if normalized in route.task_features]
    return matches[0] if len(matches) == 1 else None


def task_for_unique_feature(feature: str):
    """为任务恢复器返回 feature 唯一对应的 Celery task。"""

    route = route_for_unique_feature(feature)
    return route.task_loader() if route is not None else None


def dispatch_platform_publish(
    platform: str,
    task_id: str,
    *,
    broker_task_id: Optional[str] = None,
    retry: bool = False,
) -> Dict[str, Any]:
    """将已持久化的发布任务交给平台注册表指定的 Celery 执行器。"""

    route = get_platform_publish_route(platform)
    task_key = str(task_id or "").strip()
    if not task_key:
        raise ValueError(f"{route.label}发布任务 ID 不能为空")

    celery_callable = route.task_loader()
    if route.guarded_submission:
        from services.runtime_guard import maybe_guarded_apply_async

        submission = maybe_guarded_apply_async(
            celery_callable,
            args=[task_key],
            queue=route.queue,
            feature=route.runtime_feature,
            task_id=task_key,
            broker_task_id=broker_task_id,
            retry=retry,
        )
    else:
        from services.runtime_guard import apply_async_after_db_release

        apply_options = dict(
            args=[task_key],
            queue=route.queue,
            retry=retry,
        )
        if broker_task_id:
            apply_options["task_id"] = str(broker_task_id)
        async_result = apply_async_after_db_release(celery_callable, **apply_options)
        submission = {
            "async_result": async_result,
            "admission": None,
            "deferred": False,
            "deferred_state": None,
            "countdown": 0,
        }

    async_result = submission["async_result"]
    return {
        **submission,
        "platform": route.platform,
        "platform_label": route.label,
        "queue": route.queue,
        "feature": route.runtime_feature,
        "celery_id": str(async_result.id),
    }
