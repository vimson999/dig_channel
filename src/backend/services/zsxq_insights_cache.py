"""Bounded response caching for the ZSXQ insights overview."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import hashlib
import json
import os
import threading
import time


OVERVIEW_CACHE_VERSION = "v5"
OVERVIEW_CACHE_FIELDS = (
    "group_id",
    "theme_id",
    "range_days",
    "query",
    "page",
    "page_size",
    "sort",
)
OVERVIEW_LOCAL_CACHE_MAX_ENTRIES = 64
DEFAULT_FRESH_TTL_SECONDS = 1800
DEFAULT_STALE_TTL_SECONDS = 86400
MAX_FRESH_TTL_SECONDS = 21600
MAX_STALE_TTL_SECONDS = 604800


def _bounded_ttl_seconds(env_name: str, default: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(env_name, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    return max(0, min(value, maximum))


OVERVIEW_CACHE_TTL_SECONDS = _bounded_ttl_seconds(
    "ZSXQ_INSIGHTS_REDIS_CACHE_TTL_SECONDS",
    DEFAULT_FRESH_TTL_SECONDS,
    MAX_FRESH_TTL_SECONDS,
)
OVERVIEW_STALE_CACHE_TTL_SECONDS = _bounded_ttl_seconds(
    "ZSXQ_INSIGHTS_STALE_CACHE_TTL_SECONDS",
    DEFAULT_STALE_TTL_SECONDS,
    MAX_STALE_TTL_SECONDS,
)


@dataclass(frozen=True)
class OverviewCacheEntry:
    raw: str | bytes
    tier: str
    stale: bool = False


_overview_local_cache = OrderedDict()
_overview_local_cache_lock = threading.Lock()


def overview_cache_digest(params) -> str:
    identity = {field: params[field] for field in OVERVIEW_CACHE_FIELDS}
    encoded = json.dumps(
        identity,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def overview_cache_key(params, *, stale: bool = False) -> str:
    suffix = ":stale" if stale else ""
    return (
        f"shioa:zsxq-insights:overview:{OVERVIEW_CACHE_VERSION}:"
        f"{overview_cache_digest(params)}{suffix}"
    )


def _redis_client_for_app(app):
    if app.config.get("TESTING") or OVERVIEW_CACHE_TTL_SECONDS <= 0:
        return None
    try:
        from utils.redis_client import redis_client

        return redis_client
    except Exception:
        return None


def _get_local(cache_key: str, *, app):
    if app.config.get("TESTING") or OVERVIEW_CACHE_TTL_SECONDS <= 0:
        return None
    now = time.monotonic()
    with _overview_local_cache_lock:
        cached = _overview_local_cache.get(cache_key)
        if cached is None:
            return None
        expires_at, raw = cached
        if expires_at <= now:
            _overview_local_cache.pop(cache_key, None)
            return None
        _overview_local_cache.move_to_end(cache_key)
        return raw


def _store_local(cache_key: str, raw: str | bytes, *, app) -> None:
    if app.config.get("TESTING") or OVERVIEW_CACHE_TTL_SECONDS <= 0:
        return
    with _overview_local_cache_lock:
        _overview_local_cache[cache_key] = (
            time.monotonic() + OVERVIEW_CACHE_TTL_SECONDS,
            raw,
        )
        _overview_local_cache.move_to_end(cache_key)
        while len(_overview_local_cache) > OVERVIEW_LOCAL_CACHE_MAX_ENTRIES:
            _overview_local_cache.popitem(last=False)


def clear_local_overview_cache() -> None:
    with _overview_local_cache_lock:
        _overview_local_cache.clear()


def get_cached_overview(app, params) -> OverviewCacheEntry | None:
    fresh_key = overview_cache_key(params)
    raw = _get_local(fresh_key, app=app)
    if raw:
        return OverviewCacheEntry(raw=raw, tier="local")

    client = _redis_client_for_app(app)
    if client is None:
        return None
    try:
        raw = client.get(fresh_key)
        if raw:
            _store_local(fresh_key, raw, app=app)
            return OverviewCacheEntry(raw=raw, tier="redis")
        if OVERVIEW_STALE_CACHE_TTL_SECONDS <= 0:
            return None
        stale_raw = client.get(overview_cache_key(params, stale=True))
    except Exception:
        return None
    if not stale_raw:
        return None
    return OverviewCacheEntry(raw=stale_raw, tier="redis-stale", stale=True)


def store_overview_payload(app, params, payload) -> str:
    serialized = app.json.dumps(payload)
    fresh_key = overview_cache_key(params)
    _store_local(fresh_key, serialized, app=app)

    client = _redis_client_for_app(app)
    if client is not None:
        try:
            client.setex(fresh_key, OVERVIEW_CACHE_TTL_SECONDS, serialized)
            if OVERVIEW_STALE_CACHE_TTL_SECONDS > 0:
                client.setex(
                    overview_cache_key(params, stale=True),
                    OVERVIEW_STALE_CACHE_TTL_SECONDS,
                    serialized,
                )
        except Exception:
            pass
    return serialized
