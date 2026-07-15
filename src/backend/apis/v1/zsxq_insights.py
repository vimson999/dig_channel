"""Read-only API for the ZSXQ intelligence dashboard."""

from flask import Blueprint, jsonify, request

from services.zsxq_insights_service import GROUP_ID_SHENGCAI, build_insights, get_topic_detail
from utils.api_utils import handle_api_error


bp = Blueprint("zsxq_insights", __name__, url_prefix="/api/v1/zsxq-insights")


@bp.get("/overview")
def overview():
    try:
        result = build_insights(
            group_id=request.args.get("group_id") or GROUP_ID_SHENGCAI,
            theme_id=request.args.get("theme") or "ai-sales",
            range_days=request.args.get("range_days") or 90,
            query=request.args.get("query") or "",
            page_size=request.args.get("page_size") or 30,
            sort=request.args.get("sort") or "latest",
        )
        return jsonify({"success": True, "data": result})
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
