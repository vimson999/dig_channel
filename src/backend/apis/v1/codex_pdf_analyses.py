"""Read-only API for the independent Codex PDF analysis archive."""

import logging

from flask import Blueprint, request

from services.codex_pdf_analysis_service import (
    get_codex_pdf_analysis_detail,
    list_codex_pdf_analysis_runs,
)
from services.runtime_guard import release_db_session_quietly
from utils.api_utils import handle_api_error, success_response
from utils.auth_utils import require_login


logger = logging.getLogger(__name__)
bp = Blueprint(
    "codex_pdf_analyses",
    __name__,
    url_prefix="/api/v1/codex-pdf-analyses",
)


def _pagination(page, per_page, total):
    return {
        "page": page,
        "per_page": per_page,
        "total": total,
        "pages": (total + per_page - 1) // per_page,
        "has_next": page * per_page < total,
        "has_prev": page > 1,
    }


@bp.get("/")
@require_login
def list_runs(current_user):
    try:
        try:
            page = int(request.args.get("page", 1))
            per_page = int(request.args.get("per_page", 20))
        except (TypeError, ValueError) as exc:
            raise ValueError("分页参数必须是整数") from exc
        if page < 1:
            raise ValueError("页码必须大于 0")
        if per_page < 1 or per_page > 50:
            raise ValueError("每页数量必须在 1-50 之间")
        release_db_session_quietly()
        items, total = list_codex_pdf_analysis_runs(
            current_user.id,
            page=page,
            per_page=per_page,
        )
        return success_response({"items": items, "pagination": _pagination(page, per_page, total)})
    except ValueError as exc:
        return handle_api_error(400, str(exc))
    except Exception:
        logger.exception("Codex PDF 分析历史读取失败 user_id=%s", current_user.id)
        return handle_api_error(500, "分析历史读取失败，请稍后重试")


@bp.get("/<run_id>")
@require_login
def get_run_detail(current_user, run_id):
    try:
        release_db_session_quietly()
        detail = get_codex_pdf_analysis_detail(current_user.id, run_id)
        if detail is None:
            return handle_api_error(404, "分析报告不存在")
        return success_response(detail)
    except Exception:
        logger.exception("Codex PDF 分析详情读取失败 user_id=%s run_id=%s", current_user.id, run_id)
        return handle_api_error(500, "分析详情读取失败，请稍后重试")
