import logging

from flask import Blueprint, jsonify, request

from models.task import PublicationTask
from services.content_pipeline.pdf_cleaner_adapter import PdfCleanerAdapter
from services.content_pipeline.pdf_clean_task_service import submit_pdf_clean_task
from utils.auth_utils import require_login


logger = logging.getLogger(__name__)

bp = Blueprint("content_pipeline", __name__, url_prefix="/api/v1/content-pipeline")


@bp.route("/pdf-clean/health", methods=["GET"])
@require_login
def pdf_clean_health(_current_user):
    adapter = PdfCleanerAdapter()
    return jsonify({"success": True, "data": adapter.check_service()})


@bp.route("/pdf-clean/submit", methods=["POST"])
@require_login
def submit_pdf_clean_task(current_user):
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "PDF 清洗任务").strip()
    files = data.get("files") or []
    source_root = data.get("source_root") or None
    cleaner = data.get("cleaner") or {}

    if not isinstance(files, list) or not files:
        return jsonify({"success": False, "message": "files 不能为空"}), 400

    try:
        task = submit_pdf_clean_task(
            title=title,
            files=files,
            source_root=source_root,
            cleaner=cleaner,
            user_id=getattr(current_user, "id", None),
            trigger="manual",
        )
        return jsonify(
            {
                "success": True,
                "message": "PDF 清洗任务已提交",
                "data": {"task_id": task["id"], "celery_id": task["celery_task_id"]},
            }
        )
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    except Exception as exc:
        logger.error("提交 PDF 清洗任务失败: %s", exc, exc_info=True)
        return jsonify({"success": False, "message": f"系统错误: {exc}"}), 500


@bp.route("/tasks", methods=["GET"])
@require_login
def list_content_pipeline_tasks(_current_user):
    page = max(1, request.args.get("page", 1, type=int))
    per_page = min(50, max(1, request.args.get("per_page", 20, type=int)))
    query = PublicationTask.query.filter(
        PublicationTask.params["feature"].as_string() == "content_pdf_clean"
    ).order_by(PublicationTask.created_at.desc())
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    return jsonify(
        {
            "success": True,
            "data": {
                "items": [task.to_dict() for task in pagination.items],
                "pagination": {
                    "page": page,
                    "per_page": per_page,
                    "total": pagination.total,
                    "pages": pagination.pages,
                },
            },
        }
    )


@bp.route("/task/<task_id>", methods=["GET"])
@require_login
def get_content_pipeline_task(_current_user, task_id):
    task = PublicationTask.query.get(task_id)
    if not task or not isinstance(task.params, dict) or task.params.get("feature") != "content_pdf_clean":
        return jsonify({"success": False, "message": "任务不存在"}), 404
    return jsonify({"success": True, "data": task.to_dict()})
