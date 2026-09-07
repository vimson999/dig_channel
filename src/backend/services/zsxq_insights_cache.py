"""Bounded response caching for the ZSXQ insights overview."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import hashlib
import json
import logging
import os
import threading
import time
import uuid

from services.zsxq_insights_service import (
    GROUP_ID_SHENGCAI,
    build_insights,
    normalize_insights_arguments,
)


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

logger = logging.getLogger(__name__)

_RELEASE_LOCK_LUA = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
end
return 0
"""


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
_refresh_slot = threading.BoundedSemaphore(1)
_warmup_guard = threading.Lock()
_warmup_pid = None
_warmup_thread = None


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


def overview_refresh_lock_key(params) -> str:
    return (
        f"shioa:zsxq-insights:refresh-lock:{OVERVIEW_CACHE_VERSION}:"
        f"{overview_cache_digest(params)}"
    )


def _refresh_lock_ttl_seconds() -> int:
    raw = os.environ.get("ZSXQ_INSIGHTS_REFRESH_LOCK_TTL_SECONDS", "120")
    try:
        value = int(raw or 120)
    except (TypeError, ValueError):
        value = 120
    return max(61, min(value, 600))


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


def _remove_db_session() -> None:
    try:
        from models import db

        db.session.remove()
    except Exception:
        pass


def _release_refresh_lock(redis_client, lock_key: str, token: str) -> None:
    try:
        redis_client.eval(_RELEASE_LOCK_LUA, 1, lock_key, token)
    except Exception:
        pass


def _run_overview_refresh(
    app,
    params,
    reason: str,
    redis_client,
    lock_key: str,
    token: str,
    digest: str,
) -> None:
    started_at = time.monotonic()
    logger.info(
        "[ZSXQ_INSIGHTS_CACHE] event=refresh_started reason=%s digest=%s pid=%s",
        reason,
        digest,
        os.getpid(),
    )
    try:
        with app.app_context():
            result = build_insights(**params)
            store_overview_payload(
                app,
                params,
                {"success": True, "data": result},
            )
        duration_ms = (time.monotonic() - started_at) * 1000
        logger.info(
            "[ZSXQ_INSIGHTS_CACHE] event=refresh_completed reason=%s digest=%s "
            "pid=%s duration_ms=%.2f",
            reason,
            digest,
            os.getpid(),
            duration_ms,
        )
    except Exception as error:
        duration_ms = (time.monotonic() - started_at) * 1000
        logger.warning(
            "[ZSXQ_INSIGHTS_CACHE] event=refresh_failed reason=%s digest=%s "
            "pid=%s duration_ms=%.2f error_type=%s",
            reason,
            digest,
            os.getpid(),
            duration_ms,
            type(error).__name__,
        )
    finally:
        try:
            _remove_db_session()
        finally:
            try:
                _release_refresh_lock(redis_client, lock_key, token)
            finally:
                _refresh_slot.release()


def request_overview_refresh(app, params, reason: str) -> str:
    digest = overview_cache_digest(params)
    pid = os.getpid()
    if not _refresh_slot.acquire(blocking=False):
        logger.info(
            "[ZSXQ_INSIGHTS_CACHE] event=refresh_skipped result=local-busy "
            "reason=%s digest=%s pid=%s",
            reason,
            digest,
            pid,
        )
        return "in-flight"

    redis_client = _redis_client_for_app(app)
    if redis_client is None:
        _refresh_slot.release()
        logger.info(
            "[ZSXQ_INSIGHTS_CACHE] event=refresh_skipped result=redis-unavailable "
            "reason=%s digest=%s pid=%s",
            reason,
            digest,
            pid,
        )
        return "client-required"

    lock_key = overview_refresh_lock_key(params)
    token = uuid.uuid4().hex
    try:
        acquired = redis_client.set(
            lock_key,
            token,
            nx=True,
            ex=_refresh_lock_ttl_seconds(),
        )
    except Exception as error:
        _refresh_slot.release()
        logger.warning(
            "[ZSXQ_INSIGHTS_CACHE] event=refresh_skipped result=redis-error "
            "reason=%s digest=%s pid=%s error_type=%s",
            reason,
            digest,
            pid,
            type(error).__name__,
        )
        return "client-required"
    if not acquired:
        _refresh_slot.release()
        logger.info(
            "[ZSXQ_INSIGHTS_CACHE] event=refresh_skipped result=redis-lock-held "
            "reason=%s digest=%s pid=%s",
            reason,
            digest,
            pid,
        )
        return "in-flight"

    thread = threading.Thread(
        target=_run_overview_refresh,
        args=(
            app,
            dict(params),
            str(reason),
            redis_client,
            lock_key,
            token,
            digest,
        ),
        name=f"zsxq-insights-refresh-{digest[:12]}",
        daemon=True,
    )
    try:
        thread.start()
    except Exception as error:
        try:
            _release_refresh_lock(redis_client, lock_key, token)
        finally:
            _refresh_slot.release()
        logger.warning(
            "[ZSXQ_INSIGHTS_CACHE] event=refresh_skipped result=thread-start-error "
            "reason=%s digest=%s pid=%s error_type=%s",
            reason,
            digest,
            pid,
            type(error).__name__,
        )
        return "client-required"
    return "scheduled"


def default_overview_params():
    return normalize_insights_arguments(
        group_id=GROUP_ID_SHENGCAI,
        theme_id="ai-sales",
        range_days=90,
        query="",
        page=1,
        page_size=48,
        sort="latest",
    )


def _run_default_warmup(app) -> None:
    params = default_overview_params()
    digest = overview_cache_digest(params)
    try:
        with app.app_context():
            entry = get_cached_overview(app, params)
            if entry is not None and not entry.stale:
                logger.info(
                    "[ZSXQ_INSIGHTS_CACHE] event=warmup_skipped result=fresh "
                    "digest=%s pid=%s",
                    digest,
                    os.getpid(),
                )
                return
            request_overview_refresh(app, params, reason="startup")
    except Exception as error:
        logger.warning(
            "[ZSXQ_INSIGHTS_CACHE] event=warmup_skipped result=bootstrap-error "
            "digest=%s pid=%s error_type=%s",
            digest,
            os.getpid(),
            type(error).__name__,
        )


def schedule_default_overview_warmup(app) -> bool:
    global _warmup_pid, _warmup_thread

    pid = os.getpid()
    with _warmup_guard:
        if _warmup_pid == pid and _warmup_thread is not None:
            return False
        thread = threading.Thread(
            target=_run_default_warmup,
            args=(app,),
            name="zsxq-insights-warmup",
            daemon=True,
        )
        _warmup_pid = pid
        _warmup_thread = thread
        try:
            thread.start()
        except Exception as error:
            _warmup_pid = None
            _warmup_thread = None
            logger.warning(
                "[ZSXQ_INSIGHTS_CACHE] event=warmup_skipped result=thread-start-error "
                "pid=%s error_type=%s",
                pid,
                type(error).__name__,
            )
            return False
    logger.info(
        "[ZSXQ_INSIGHTS_CACHE] event=warmup_scheduled pid=%s",
        pid,
    )
    return True
