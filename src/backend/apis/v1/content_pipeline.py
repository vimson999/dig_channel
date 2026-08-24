import logging

from flask import Blueprint, jsonify, request
from sqlalchemy.orm import load_only

from models.task import PublicationTask
from services.content_pipeline.pdf_cleaner_adapter import PdfCleanerAdapter
from services.content_pipeline.pdf_clean_task_service import submit_pdf_clean_task as enqueue_pdf_clean_task
from services.content_pipeline.retention_cleanup_service import (
    ContentPipelineRetentionCleanupService,
    ContentRetentionPolicy,
)
from services.content_pipeline.retention_scheduler_service import (
    load_config as retention_scheduler_config,
    restart_scheduler as restart_retention_scheduler,
    save_config as save_retention_scheduler_config,
    scheduler_log_tail as retention_scheduler_log_tail,
    scheduler_status as retention_scheduler_status,
    start_scheduler as start_retention_scheduler,
    stop_scheduler as stop_retention_scheduler,
    trigger_once as trigger_retention_cleanup_once,
)
from utils.auth_utils import require_login


logger = logging.getLogger(__name__)

bp = Blueprint("content_pipeline", __name__, url_prefix="/api/v1/content-pipeline")

CONTENT_PIPELINE_FEATURES = {
    "content_pdf_clean",
    "content_audio_clean",
    "zsxq_clean_output_publish",
}

TASK_PAYLOAD_COLUMNS = (
    PublicationTask.id,
    PublicationTask.celery_task_id,
    PublicationTask.status,
    PublicationTask.current_step,
    PublicationTask.progress,
    PublicationTask.params,
    PublicationTask.result,
    PublicationTask.error_message,
    PublicationTask.feature,
    PublicationTask.created_at,
    PublicationTask.updated_at,
    PublicationTask.completed_at,
)


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
        task = enqueue_pdf_clean_task(
            title=title,
            files=files,
            source_root=source_root,
            cleaner=cleaner,
            auto_publish=data.get("auto_publish") or {},
            user_id=getattr(current_user, "id", None),
            trigger="manual",
        )
        return jsonify(
            {
                "success": True,
                "message": "PDF 清洗任务已提交" if task.get("celery_task_id") else "PDF 文件全部命中去重，未重复进入清洗",
                "data": {
                    "task_id": task["id"],
                    "celery_id": task.get("celery_task_id"),
                    "status": task.get("status"),
                    "current_step": task.get("current_step"),
                    "dedupe": (task.get("result") or {}).get("dedupe") if isinstance(task.get("result"), dict) else {},
                },
            }
        )
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    except Exception as exc:
        logger.error("提交 PDF 清洗任务失败: %s", exc, exc_info=True)
        return jsonify({"success": False, "message": f"系统错误: {exc}"}), 500


@bp.route("/retention/preview", methods=["GET"])
@require_login
def preview_retention_cleanup(_current_user):
    policy = ContentRetentionPolicy.from_payload(request.args.to_dict())
    service = ContentPipelineRetentionCleanupService()
    result = service.run(policy=policy, dry_run=True)
    return jsonify({"success": True, "data": result})


@bp.route("/retention/cleanup", methods=["POST"])
@require_login
def run_retention_cleanup(_current_user):
    data = request.get_json(silent=True) or {}
    dry_run = str(data.get("dry_run", True)).lower() not in {"0", "false", "no", "off"}
    policy = ContentRetentionPolicy.from_payload(data.get("policy") or {})
    service = ContentPipelineRetentionCleanupService()
    result = service.run(policy=policy, dry_run=dry_run)
    return jsonify({
        "success": len(result.get("errors") or []) == 0,
        "message": "清理预览完成" if dry_run else "清理执行完成",
        "data": result,
    })


@bp.route("/retention/scheduler/config", methods=["GET", "POST"])
@require_login
def retention_scheduler_config_endpoint(_current_user):
    if request.method == "GET":
        return jsonify({"success": True, "data": {"config": retention_scheduler_config()}})
    data = request.get_json(silent=True) or {}
    config = save_retention_scheduler_config(data)
    return jsonify({"success": True, "message": "历史文件清理计划配置已保存", "data": {"config": config}})


@bp.route("/retention/scheduler/status", methods=["GET"])
@require_login
def retention_scheduler_status_endpoint(_current_user):
    return jsonify({"success": True, "data": {"scheduler": retention_scheduler_status()}})


@bp.route("/retention/scheduler/start", methods=["POST"])
@require_login
def retention_scheduler_start_endpoint(_current_user):
    result = start_retention_scheduler()
    return jsonify({"success": True, "data": {"scheduler": result}})


@bp.route("/retention/scheduler/stop", methods=["POST"])
@require_login
def retention_scheduler_stop_endpoint(_current_user):
    result = stop_retention_scheduler()
    return jsonify({"success": True, "data": {"scheduler": result}})


@bp.route("/retention/scheduler/restart", methods=["POST"])
@require_login
def retention_scheduler_restart_endpoint(_current_user):
    result = restart_retention_scheduler()
    return jsonify({"success": True, "data": {"scheduler": result}})


@bp.route("/retention/scheduler/run-once", methods=["POST"])
@require_login
def retention_scheduler_run_once_endpoint(_current_user):
    data = request.get_json(silent=True) or {}
    dry_run = data.get("dry_run")
    if dry_run is not None:
        dry_run = str(dry_run).lower() not in {"0", "false", "no", "off"}
    result = trigger_retention_cleanup_once(dry_run_override=dry_run)
    return jsonify({"success": True, "data": {"scheduler": result}})


@bp.route("/retention/scheduler/logs", methods=["GET"])
@require_login
def retention_scheduler_logs_endpoint(_current_user):
    lines = request.args.get("lines", 120, type=int)
    parsed_lines = min(500, max(20, int(lines or 120)))
    return jsonify({
        "success": True,
        "data": {
            "lines": retention_scheduler_log_tail(lines=parsed_lines),
            "line_count": parsed_lines,
        },
    })


@bp.route("/tasks", methods=["GET"])
@require_login
def list_content_pipeline_tasks(_current_user):
    page = max(1, request.args.get("page", 1, type=int))
    per_page = min(50, max(1, request.args.get("per_page", 20, type=int)))
    query = PublicationTask.query.filter(
        PublicationTask.feature.in_(CONTENT_PIPELINE_FEATURES)
    ).order_by(PublicationTask.created_at.desc())
    pagination = query.options(load_only(*TASK_PAYLOAD_COLUMNS)).paginate(page=page, per_page=per_page, error_out=False)
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
    task = PublicationTask.query.options(load_only(*TASK_PAYLOAD_COLUMNS)).filter(PublicationTask.id == str(task_id)).one_or_none()
    if not task or (task.feature or (task.params or {}).get("feature")) not in CONTENT_PIPELINE_FEATURES:
        return jsonify({"success": False, "message": "任务不存在"}), 404
    return jsonify({"success": True, "data": task.to_dict()})
