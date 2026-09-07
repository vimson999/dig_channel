"""Read-only API for the ZSXQ intelligence dashboard."""

from flask import Blueprint, current_app, jsonify, request

from services.zsxq_insights_cache import (
    OVERVIEW_CACHE_TTL_SECONDS,
    OVERVIEW_STALE_CACHE_TTL_SECONDS,
    OverviewCacheEntry,
    get_cached_overview,
    store_overview_payload,
)
from services.zsxq_insights_service import (
    GROUP_ID_SHENGCAI,
    build_insights,
    get_topic_detail,
    normalize_insights_arguments,
)
from utils.api_utils import handle_api_error


bp = Blueprint("zsxq_insights", __name__, url_prefix="/api/v1/zsxq-insights")


def _cached_response(entry: OverviewCacheEntry):
    response = current_app.response_class(entry.raw, status=200, mimetype="application/json")
    response.headers["X-Insights-Cache"] = "STALE" if entry.stale else "HIT"
    response.headers["X-Insights-Cache-Tier"] = entry.tier
    response.headers["Cache-Control"] = "private, max-age=30"
    if entry.stale:
        response.headers["Warning"] = '110 - "Response is stale; refreshing in background"'
    return response


def _origin_response(serialized):
    response = current_app.response_class(serialized, status=200, mimetype="application/json")
    response.headers["X-Insights-Cache"] = "MISS"
    response.headers["X-Insights-Cache-Tier"] = "origin"
    response.headers["Cache-Control"] = "private, max-age=30"
    return response


@bp.get("/overview")
def overview():
    try:
        force_refresh = request.args.get("_refresh") == "1"
        params = normalize_insights_arguments(
            group_id=request.args.get("group_id"),
            theme_id=request.args.get("theme"),
            range_days=request.args.get("range_days"),
            query=request.args.get("query"),
            page=request.args.get("page"),
            page_size=request.args.get("page_size"),
            sort=request.args.get("sort"),
        )
        app = current_app._get_current_object()
        entry = None if force_refresh else get_cached_overview(app, params)
        if entry is not None:
            return _cached_response(entry)

        result = build_insights(**params)
        serialized = store_overview_payload(app, params, {"success": True, "data": result})
        return _origin_response(serialized)
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
