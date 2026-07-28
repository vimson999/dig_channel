#!/usr/bin/env python3
"""Fail startup when configured Redis dependencies are unreachable."""

import os
from pathlib import Path

from dotenv import dotenv_values


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def configured_redis_urls(environ=None):
    environ = environ or os.environ
    values = dotenv_values(PROJECT_ROOT / ".env")

    redis_url = environ.get("REDIS_URL") or values.get("REDIS_URL")
    broker_url = (
        environ.get("CELERY_BROKER_URL")
        or values.get("CELERY_BROKER_URL")
        or redis_url
    )
    backend_url = (
        environ.get("CELERY_RESULT_BACKEND")
        or values.get("CELERY_RESULT_BACKEND")
        or redis_url
    )
    return (
        ("应用缓存 REDIS_URL", redis_url),
        ("Celery broker", broker_url),
        ("Celery result backend", backend_url),
    )


def check_redis_urls(entries=None, client_factory=None):
    if client_factory is None:
        import redis

        client_factory = redis.Redis.from_url

    failures = []
    checked_urls = {}
    for label, url in entries or configured_redis_urls():
        if not url:
            failures.append(f"{label}: 未配置 URL")
            continue
        checked_urls.setdefault(str(url), []).append(label)

    for url, labels in checked_urls.items():
        label = " / ".join(labels)
        try:
            client = client_factory(
                url,
                decode_responses=True,
                socket_connect_timeout=3,
                socket_timeout=3,
                health_check_interval=30,
            )
            if client.ping() is not True:
                failures.append(f"{label}: PING 未返回成功")
        except Exception as error:
            failures.append(f"{label}: {type(error).__name__}")
    return failures


def main():
    failures = check_redis_urls()
    if failures:
        print("❌ Redis 运行依赖预检失败，拒绝启动：")
        for failure in failures:
            print(f"  - {failure}")
        return 2

    print("✅ Redis 运行依赖预检通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
