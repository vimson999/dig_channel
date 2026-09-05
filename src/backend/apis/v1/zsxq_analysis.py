"""User-scoped API for independent ZSXQ PDF/audio list analyses."""

import logging

from flask import Blueprint, request, send_file

from services.zsxq_analysis_service import (
    MAX_SOURCE_BYTES,
    SUPPORTED_KINDS,
    SUPPORTED_STATUSES,
    ZsxqAnalysisValidationError,
    build_analysis_run_detail,
    create_analysis_run,
    get_analysis_run,
    list_analysis_items,
    list_analysis_runs,
    resolve_source_artifact,
)
from services.runtime_guard import release_db_session_quietly
from utils.api_utils import handle_api_error, success_response
from utils.auth_utils import require_login


logger = logging.getLogger(__name__)
bp = Blueprint(
    "zsxq_analysis",
    __name__,
    url_prefix="/api/v1/zsxq-analysis",
)


def _page_args():
    try:
        page = int(request.args.get("page", 1))
        per_page = int(request.args.get("per_page", 20))
    except (TypeError, ValueError):
        raise ZsxqAnalysisValidationError("分页参数必须是整数")
    if page < 1:
        raise ZsxqAnalysisValidationError("页码必须大于 0")
    if per_page < 1 or per_page > 100:
        raise ZsxqAnalysisValidationError("每页数量必须在 1-100 之间")
    return page, per_page


def _pagination(page, per_page, total):
    return {
        "page": page,
        "per_page": per_page,
        "total": total,
        "pages": (total + per_page - 1) // per_page,
        "has_next": page * per_page < total,
        "has_prev": page > 1,
    }


@bp.post("/runs")
@require_login
def create_run(current_user):
    user_id = current_user.id
    release_db_session_quietly()
    upload = request.files.get("file")
    if upload is None:
        return handle_api_error(400, "请选择一个 JSON 文件")

    # Read at most one byte over the service limit so oversized uploads are
    # rejected without ever becoming an unbounded in-memory object.
    source_bytes = upload.stream.read(MAX_SOURCE_BYTES + 1)
    expected_kind = request.form.get("kind") or None
    if expected_kind and expected_kind.strip().lower() not in SUPPORTED_KINDS:
        return handle_api_error(400, "kind 只能是 pdf 或 audio")
    try:
        run = create_analysis_run(
            user_id=user_id,
            source_bytes=source_bytes,
            source_filename=upload.filename,
            expected_kind=expected_kind,
        )
        detail = build_analysis_run_detail(run)
        return success_response(detail, message="分析完成", status_code=201)
    except ZsxqAnalysisValidationError as exc:
        return handle_api_error(400, str(exc))
    except Exception:
        logger.exception("知识星球清单分析保存失败")
        return handle_api_error(500, "分析结果保存失败，请稍后重试")


@bp.get("/runs")
@require_login
def list_runs(current_user):
    try:
        page, per_page = _page_args()
        kind = request.args.get("kind")
        if not kind or kind.strip().lower() not in SUPPORTED_KINDS:
            raise ZsxqAnalysisValidationError("kind 只能是 pdf 或 audio")
        items, total = list_analysis_runs(
            user_id=current_user.id,
            kind=kind,
            page=page,
            per_page=per_page,
        )
        return success_response(
            {
                "items": items,
                "pagination": _pagination(page, per_page, total),
            }
        )
    except ZsxqAnalysisValidationError as exc:
        return handle_api_error(400, str(exc))
    except Exception:
        logger.exception("知识星球清单分析历史读取失败")
        return handle_api_error(500, "历史读取失败，请稍后重试")


@bp.get("/runs/<run_id>")
@require_login
def get_run_detail(current_user, run_id):
    run = get_analysis_run(user_id=current_user.id, run_id=run_id)
    if run is None:
        return handle_api_error(404, "分析批次不存在")
    try:
        return success_response(build_analysis_run_detail(run))
    except Exception:
        logger.exception("知识星球清单分析详情读取失败 run_id=%s", run_id)
        return handle_api_error(500, "分析详情读取失败，请稍后重试")


@bp.get("/runs/<run_id>/items")
@require_login
def get_run_items(current_user, run_id):
    run = get_analysis_run(user_id=current_user.id, run_id=run_id)
    if run is None:
        return handle_api_error(404, "分析批次不存在")
    try:
        page, per_page = _page_args()
        status = request.args.get("status")
        if status and status.strip().lower() not in SUPPORTED_STATUSES:
            raise ZsxqAnalysisValidationError("status 参数不受支持")
        items, total = list_analysis_items(
            user_id=current_user.id,
            run_id=run.id,
            page=page,
            per_page=per_page,
            status=status,
            sort=request.args.get("sort", "downloads"),
        )
        return success_response(
            {
                "items": items,
                "pagination": _pagination(page, per_page, total),
            }
        )
    except ZsxqAnalysisValidationError as exc:
        return handle_api_error(400, str(exc))
    except Exception:
        logger.exception("知识星球清单分析条目读取失败 run_id=%s", run_id)
        return handle_api_error(500, "分析条目读取失败，请稍后重试")


@bp.get("/runs/<run_id>/source")
@require_login
def download_source(current_user, run_id):
    run = get_analysis_run(user_id=current_user.id, run_id=run_id)
    if run is None:
        return handle_api_error(404, "分析批次不存在")
    try:
        source_path = resolve_source_artifact(run)
    except FileNotFoundError:
        return handle_api_error(410, "该批次的原始文件已不可用")
    if not source_path.is_file():
        return handle_api_error(410, "该批次的原始文件已不可用")
    response = send_file(
        source_path,
        mimetype="application/json",
        as_attachment=True,
        download_name=run.source_filename or "analysis.json",
        max_age=0,
    )
    response.headers["Cache-Control"] = "private, no-store"
    return response
