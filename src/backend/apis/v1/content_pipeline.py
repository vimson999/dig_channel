import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from flask import Blueprint, current_app, jsonify, request
from sqlalchemy.orm import load_only

from models.task import PublicationTask
from services.content_pipeline.audio_clean_task_service import submit_audio_clean_task as enqueue_audio_clean_task
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
from services.watermark.utils import safe_filename
from services.runtime_guard import end_db_transaction_before_external_io
from utils.auth_utils import require_login


logger = logging.getLogger(__name__)

bp = Blueprint("content_pipeline", __name__, url_prefix="/api/v1/content-pipeline")

CONTENT_PIPELINE_FEATURES = {
    "content_pdf_clean",
    "content_audio_clean",
    "zsxq_clean_output_publish",
}

WEB_SUBMISSION_SOURCE_MODE = "web_upload"
WEB_SUBMISSION_CHANNEL = "content_pipeline_web"
WEB_SUBMISSION_EXTENSIONS = {
    ".pdf": "pdf",
    ".mp3": "mp3",
}
PROJECT_ROOT = Path(__file__).resolve().parents[4]

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


@bp.route("/web-submit", methods=["POST"])
@require_login
def submit_web_content_pipeline(current_user):
    """Stage browser-selected MP3/PDF files then enter the shared pipeline.

    The route deliberately creates one clean task per media kind.  A PDF task
    submission error must not discard an otherwise valid MP3 submission (and
    vice versa), because the downstream cleaner already handles item-level
    partial success.
    """
    # Authentication may have checked out a connection. Release it before
    # multipart parsing and upload staging, not just before the later hash.
    end_db_transaction_before_external_io("content pipeline upload staging")
    input_mode = str(request.form.get("input_mode") or "upload").strip().lower()
    if input_mode not in {"upload", "folder"}:
        return jsonify({"success": False, "message": "input_mode 仅支持 upload 或 folder"}), 400

    uploaded_files = request.files.getlist("files")
    if not uploaded_files:
        return jsonify({"success": False, "message": "请至少选择一个 MP3 或 PDF 文件"}), 400

    include_subdirectories = _truthy(request.form.get("include_subdirectories"))
    staged = _stage_web_submission_files(
        uploaded_files,
        relative_paths=request.form.getlist("relative_paths"),
        input_mode=input_mode,
        include_subdirectories=include_subdirectories,
        folder_root=request.form.get("folder_root"),
    )
    accepted = staged["accepted"]
    skipped = staged["skipped"]
    if not accepted:
        return jsonify(
            {
                "success": False,
                "message": "没有可投递的 MP3 或 PDF 文件",
                "data": {
                    "accepted_count": 0,
                    "skipped_count": len(skipped),
                    "skipped": skipped,
                },
            }
        ), 400

    files_by_kind = {"pdf": [], "mp3": []}
    for item in accepted:
        files_by_kind[item["media_type"]].append(item["path"])

    submission_id = staged["submission_id"]
    source_root = staged["source_root"]
    task_titles = {
        "pdf": _web_submission_title("PDF"),
        "mp3": _web_submission_title("MP3"),
    }
    task_submitters = {
        "pdf": enqueue_pdf_clean_task,
        "mp3": enqueue_audio_clean_task,
    }
    tasks = {}
    errors = []
    for kind, files in files_by_kind.items():
        if not files:
            continue
        metadata = {
            "source_mode": WEB_SUBMISSION_SOURCE_MODE,
            "submission_channel": WEB_SUBMISSION_CHANNEL,
            "web_submission_id": submission_id,
            "web_submission_root": source_root,
            "input_mode": input_mode,
            "include_subdirectories": include_subdirectories,
            "submitted_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            task = task_submitters[kind](
                title=task_titles[kind],
                files=files,
                source_root=source_root,
                auto_publish={"enabled": True},
                user_id=getattr(current_user, "id", None),
                trigger="web_upload",
                metadata=metadata,
            )
            tasks[kind] = _web_submission_task_summary(task)
        except Exception as exc:
            logger.exception("网页投递 %s 清洗任务提交失败 | submission=%s", kind, submission_id)
            errors.append({"kind": kind, "error": str(exc)})

    response_data = {
        "source_mode": WEB_SUBMISSION_SOURCE_MODE,
        "submission_id": submission_id,
        "input_mode": input_mode,
        "include_subdirectories": include_subdirectories,
        "accepted_count": len(accepted),
        "accepted": [_public_web_submission_file(item) for item in accepted],
        "skipped_count": len(skipped),
        "skipped": skipped,
        "tasks": tasks,
        "errors": errors,
    }
    if tasks:
        message = "网页投递已进入统一清洗发布链路"
        if errors:
            message += "；部分类型提交失败，其余类型已继续处理"
        return jsonify({"success": True, "message": message, "data": response_data}), 202
    return jsonify(
        {
            "success": False,
            "message": "文件已暂存，但清洗任务未能提交",
            "data": response_data,
        }
    ), 500


def _stage_web_submission_files(
    uploaded_files,
    *,
    relative_paths,
    input_mode,
    include_subdirectories,
    folder_root=None,
):
    """Save accepted browser uploads under a system-owned, per-run directory."""
    submission_id = str(uuid.uuid4())
    submission_root = _web_submission_upload_root() / submission_id
    raw_root = submission_root / "raw"
    accepted = []
    skipped = []

    for index, uploaded in enumerate(uploaded_files):
        original_name = str(getattr(uploaded, "filename", "") or "").strip()
        filename = _safe_web_submission_filename(original_name)
        relative_path, relative_error = _normalized_web_relative_path(
            relative_paths[index] if index < len(relative_paths) else original_name,
            fallback=filename,
            folder_root=folder_root if input_mode == "folder" else None,
        )
        if relative_error:
            skipped.append({"filename": original_name or "未命名文件", "reason": relative_error})
            continue
        if input_mode == "folder" and not include_subdirectories and len(relative_path.parts) > 1:
            skipped.append({"filename": filename, "relative_path": str(relative_path), "reason": "nested_file_not_selected"})
            continue

        media_type = WEB_SUBMISSION_EXTENSIONS.get(Path(filename).suffix.lower())
        if not media_type:
            skipped.append({"filename": filename or "未命名文件", "reason": "unsupported_file_type"})
            continue

        target = None
        try:
            raw_root.mkdir(parents=True, exist_ok=True)
            target = _unique_web_submission_path(raw_root, filename)
            uploaded.save(target)
            bytes_count = target.stat().st_size
        except (OSError, ValueError) as exc:
            if target is not None:
                try:
                    target.unlink(missing_ok=True)
                except OSError:
                    logger.warning("网页投递失败暂存文件清理失败 | path=%s", target)
            logger.warning("网页投递文件暂存失败 | filename=%s error=%s", filename, exc)
            skipped.append({"filename": filename, "reason": "stage_failed", "error": str(exc)})
            continue

        if bytes_count <= 0:
            try:
                target.unlink(missing_ok=True)
            except OSError:
                logger.warning("网页投递空文件清理失败 | path=%s", target)
            skipped.append({"filename": filename, "reason": "empty_file"})
            continue

        accepted.append(
            {
                "path": str(target.resolve()),
                "filename": target.name,
                "original_filename": original_name or target.name,
                "relative_path": str(relative_path),
                "media_type": media_type,
                "bytes": bytes_count,
            }
        )

    if not accepted:
        _remove_empty_web_submission_root(raw_root, submission_root, _web_submission_upload_root())

    return {
        "submission_id": submission_id,
        "source_root": str(raw_root.resolve()),
        "accepted": accepted,
        "skipped": skipped,
    }


def _web_submission_upload_root():
    configured = current_app.config.get("CONTENT_PIPELINE_WEB_UPLOAD_ROOT") or os.getenv(
        "CONTENT_PIPELINE_WEB_UPLOAD_ROOT"
    )
    return Path(configured or (PROJECT_ROOT / "runtime" / "content_pipeline" / "web_uploads")).expanduser().resolve()


def _safe_web_submission_filename(value):
    filename = safe_filename(str(value or "").replace("\x00", "").replace("\\", "/"))
    return filename or "unnamed_file"


def _normalized_web_relative_path(value, *, fallback, folder_root=None):
    raw = str(value or fallback or "").strip().replace("\\", "/")
    if not raw:
        return PurePosixPath(fallback), None
    candidate = PurePosixPath(raw)
    parts = tuple(part for part in candidate.parts if part not in {"", "."})
    if candidate.is_absolute() or any(part == ".." for part in parts):
        return PurePosixPath(fallback), "invalid_relative_path"
    normalized_folder_root = _safe_web_submission_filename(folder_root) if folder_root else ""
    if normalized_folder_root and len(parts) > 1 and parts[0] == normalized_folder_root:
        parts = parts[1:]
    return PurePosixPath(*parts) if parts else PurePosixPath(fallback), None


def _unique_web_submission_path(root, filename):
    candidate = root / filename
    if not candidate.exists():
        return candidate
    stem = Path(filename).stem or "unnamed_file"
    suffix = Path(filename).suffix
    sequence = 2
    while True:
        candidate = root / f"{stem} ({sequence}){suffix}"
        if not candidate.exists():
            return candidate
        sequence += 1


def _remove_empty_web_submission_root(raw_root, submission_root, upload_root):
    for path in (raw_root, submission_root, upload_root):
        try:
            path.rmdir()
        except OSError:
            break


def _web_submission_title(kind):
    stamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")
    return f"网页投递 {kind} 清洗发布 · {stamp}"


def _web_submission_task_summary(task):
    task = task if isinstance(task, dict) else {}
    result = task.get("result") if isinstance(task.get("result"), dict) else {}
    dedupe = result.get("dedupe") if isinstance(result.get("dedupe"), dict) else {}
    return {
        "task_id": str(task.get("id") or ""),
        "celery_task_id": task.get("celery_task_id"),
        "status": task.get("status"),
        "current_step": task.get("current_step"),
        "progress": task.get("progress"),
        "deferred": bool(task.get("deferred")),
        "admission": task.get("admission"),
        "auto_publish_requested": True,
        "dedupe": {
            "received_count": _as_non_negative_int(dedupe.get("received_count")),
            "unique_count": _as_non_negative_int(dedupe.get("unique_count")),
            "duplicate_count": _as_non_negative_int(dedupe.get("duplicate_count")),
        },
    }


def _public_web_submission_file(item):
    return {
        "filename": item.get("filename"),
        "original_filename": item.get("original_filename"),
        "relative_path": item.get("relative_path"),
        "media_type": item.get("media_type"),
        "bytes": _as_non_negative_int(item.get("bytes")),
    }


def _as_non_negative_int(value):
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _truthy(value):
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


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
