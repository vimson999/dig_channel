#!/usr/bin/env python3
"""Fail startup when configured Redis dependencies are unreachable."""

import os
from pathlib import Path

from dotenv import dotenv_values


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _bool_env(environ, name, default=False):
    raw = environ.get(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


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


def check_runtime_dependencies(environ=None, client_factory=None):
    """Return hard failures and non-critical result-backend warnings.

    Business task state is durable in PostgreSQL and Celery is configured with
    ``task_ignore_result=True``.  Cache and broker availability are therefore
    startup requirements, while the legacy result backend is advisory unless
    an operator explicitly opts back into the hard gate.
    """

    environ = environ or os.environ
    entries = configured_redis_urls(environ)
    required_entries = entries[:2]
    result_entries = entries[2:]
    required_urls = {str(url) for _, url in required_entries if url}
    # Do not ping an identical broker/result URL twice.  If the shared Redis is
    # down, the required broker check already fails closed.
    result_entries = [
        entry for entry in result_entries
        if not entry[1] or str(entry[1]) not in required_urls
    ]
    failures = check_redis_urls(required_entries, client_factory=client_factory)
    result_failures = check_redis_urls(result_entries, client_factory=client_factory)
    if _bool_env(environ, "CELERY_RESULT_BACKEND_REQUIRED", default=False):
        failures.extend(result_failures)
        return failures, []
    return failures, result_failures


def main():
    failures, warnings = check_runtime_dependencies()
    if failures:
        print("❌ Redis 运行依赖预检失败，拒绝启动：")
        for failure in failures:
            print(f"  - {failure}")
        return 2

    if warnings:
        print("⚠️ 非关键 Celery result backend 当前不可用；任务状态以 PostgreSQL 为准，继续启动：")
        for warning in warnings:
            print(f"  - {warning}")

    print("✅ Redis 运行依赖预检通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
