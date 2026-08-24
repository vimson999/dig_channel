"""Read-only API for the ZSXQ intelligence dashboard."""

import hashlib
import os
import threading
import time
from collections import OrderedDict

from flask import Blueprint, current_app, jsonify, request

from services.zsxq_insights_service import GROUP_ID_SHENGCAI, build_insights, get_topic_detail
from utils.api_utils import handle_api_error


bp = Blueprint("zsxq_insights", __name__, url_prefix="/api/v1/zsxq-insights")


def _cache_ttl_seconds():
    try:
        value = int(os.environ.get("ZSXQ_INSIGHTS_REDIS_CACHE_TTL_SECONDS", "1800") or 1800)
    except (TypeError, ValueError):
        value = 1800
    return max(0, min(value, 21600))


def _stale_cache_ttl_seconds():
    try:
        value = int(os.environ.get("ZSXQ_INSIGHTS_STALE_CACHE_TTL_SECONDS", "86400") or 86400)
    except (TypeError, ValueError):
        value = 86400
    return max(0, min(value, 604800))


OVERVIEW_CACHE_TTL_SECONDS = _cache_ttl_seconds()
OVERVIEW_STALE_CACHE_TTL_SECONDS = _stale_cache_ttl_seconds()
OVERVIEW_CACHE_VERSION = "v4"
OVERVIEW_LOCAL_CACHE_MAX_ENTRIES = 64
_overview_local_cache = OrderedDict()
_overview_local_cache_lock = threading.Lock()


def _overview_redis_client():
    if current_app.config.get("TESTING") or OVERVIEW_CACHE_TTL_SECONDS <= 0:
        return None
    try:
        from utils.redis_client import redis_client

        return redis_client
    except Exception:
        return None


def _overview_cache_key(stale=False):
    query_string = b"&".join(
        part
        for part in request.query_string.split(b"&")
        if not part.startswith(b"_refresh=")
    )
    digest = hashlib.sha256(query_string).hexdigest()
    suffix = ":stale" if stale else ""
    return f"shioa:zsxq-insights:overview:{OVERVIEW_CACHE_VERSION}:{digest}{suffix}"


def _local_cached_overview(cache_key):
    if current_app.config.get("TESTING") or OVERVIEW_CACHE_TTL_SECONDS <= 0:
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


def _store_local_overview(cache_key, raw):
    if current_app.config.get("TESTING") or OVERVIEW_CACHE_TTL_SECONDS <= 0:
        return
    with _overview_local_cache_lock:
        _overview_local_cache[cache_key] = (
            time.monotonic() + OVERVIEW_CACHE_TTL_SECONDS,
            raw,
        )
        _overview_local_cache.move_to_end(cache_key)
        while len(_overview_local_cache) > OVERVIEW_LOCAL_CACHE_MAX_ENTRIES:
            _overview_local_cache.popitem(last=False)


def _cached_response(raw, tier, stale=False):
    response = current_app.response_class(raw, status=200, mimetype="application/json")
    response.headers["X-Insights-Cache"] = "STALE" if stale else "HIT"
    response.headers["X-Insights-Cache-Tier"] = tier
    response.headers["Cache-Control"] = "private, max-age=30"
    if stale:
        response.headers["Warning"] = '110 - "Response is stale; refreshing in background"'
    return response


def _cached_overview_response():
    cache_key = _overview_cache_key()
    raw = _local_cached_overview(cache_key)
    if raw:
        return _cached_response(raw, "local")

    client = _overview_redis_client()
    if client is None:
        return None
    try:
        raw = client.get(cache_key)
    except Exception:
        return None
    if raw:
        _store_local_overview(cache_key, raw)
        return _cached_response(raw, "redis")
    if OVERVIEW_STALE_CACHE_TTL_SECONDS <= 0:
        return None
    try:
        stale_raw = client.get(_overview_cache_key(stale=True))
    except Exception:
        return None
    if not stale_raw:
        return None
    return _cached_response(stale_raw, "redis-stale", stale=True)


def _store_overview_response(payload):
    serialized = current_app.json.dumps(payload)
    cache_key = _overview_cache_key()
    _store_local_overview(cache_key, serialized)
    client = _overview_redis_client()
    if client is not None:
        try:
            client.setex(cache_key, OVERVIEW_CACHE_TTL_SECONDS, serialized)
            if OVERVIEW_STALE_CACHE_TTL_SECONDS > 0:
                client.setex(
                    _overview_cache_key(stale=True),
                    OVERVIEW_STALE_CACHE_TTL_SECONDS,
                    serialized,
                )
        except Exception:
            pass
    response = current_app.response_class(serialized, status=200, mimetype="application/json")
    response.headers["X-Insights-Cache"] = "MISS"
    response.headers["X-Insights-Cache-Tier"] = "origin"
    response.headers["Cache-Control"] = "private, max-age=30"
    return response


@bp.get("/overview")
def overview():
    try:
        force_refresh = request.args.get("_refresh") == "1"
        if not force_refresh:
            cached = _cached_overview_response()
            if cached is not None:
                return cached
        result = build_insights(
            group_id=request.args.get("group_id") or GROUP_ID_SHENGCAI,
            theme_id=request.args.get("theme") or "ai-sales",
            range_days=request.args.get("range_days") or 90,
            query=request.args.get("query") or "",
            page=request.args.get("page") or 1,
            page_size=request.args.get("page_size") or 30,
            sort=request.args.get("sort") or "latest",
        )
        return _store_overview_response({"success": True, "data": result})
    except (TypeError, ValueError) as error:
        return handle_api_error(400, str(error))
    except Exception as error:
        return handle_api_error(502, f"生财情报聚合失败: {error}")


@bp.get("/topic/<topic_id>")
def topic_detail(topic_id):
    try:
        result = get_topic_detail(request.args.get("group_id") or GROUP_ID_SHENGCAI, topic_id)
        if not result:
            return handle_api_error(404, "主题不存在")
        return jsonify({"success": True, "data": result})
    except Exception as error:
        return handle_api_error(502, f"主题详情读取失败: {error}")
