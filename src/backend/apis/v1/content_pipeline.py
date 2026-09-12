import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from flask import Blueprint, current_app, jsonify, request
from sqlalchemy import func
from sqlalchemy.orm import load_only

from models import db
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

WEB_SUBMISSION_HISTORY_COLUMNS = (
    PublicationTask.id,
    PublicationTask.status,
    PublicationTask.current_step,
    PublicationTask.progress,
    PublicationTask.error_message,
    PublicationTask.feature,
    PublicationTask.user_id,
    PublicationTask.task_title,
    PublicationTask.content_clean_files_count,
    PublicationTask.content_clean_success_count,
    PublicationTask.content_clean_failed_count,
    PublicationTask.content_submission_channel,
    PublicationTask.content_web_submission_id,
    PublicationTask.content_submission_input_mode,
    PublicationTask.created_at,
    PublicationTask.updated_at,
    PublicationTask.completed_at,
)

PUBLISH_HISTORY_COLUMNS = (
    PublicationTask.id,
    PublicationTask.status,
    PublicationTask.current_step,
    PublicationTask.progress,
    PublicationTask.error_message,
    PublicationTask.feature,
    PublicationTask.source_task_id,
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
            _record_failed_web_submission_task(
                kind=kind,
                title=task_titles[kind],
                user_id=getattr(current_user, "id", None),
                metadata=metadata,
                files_count=len(files),
                error=exc,
            )
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


@bp.route("/web-submissions", methods=["GET"])
@require_login
def list_web_content_pipeline_submissions(current_user):
    """Return recent browser submissions grouped across PDF and MP3 tasks."""
    try:
        limit = int(request.args.get("limit") or 20)
    except (TypeError, ValueError):
        limit = 20
    limit = max(1, min(50, limit))
    try:
        offset = int(request.args.get("offset") or 0)
    except (TypeError, ValueError):
        offset = 0
    offset = max(0, offset)
    user_id = str(getattr(current_user, "id", "") or "").strip()

    latest_created = func.max(PublicationTask.created_at)
    submission_rows = (
        db.session.query(
            PublicationTask.content_web_submission_id.label("submission_id"),
            latest_created.label("submitted_at"),
        )
        .filter(
            PublicationTask.content_submission_channel == WEB_SUBMISSION_CHANNEL,
            PublicationTask.content_web_submission_id.isnot(None),
            PublicationTask.user_id == user_id,
        )
        .group_by(PublicationTask.content_web_submission_id)
        .order_by(
            latest_created.desc(),
            PublicationTask.content_web_submission_id.desc(),
        )
        .offset(offset)
        .limit(limit + 1)
        .all()
    )
    has_more = len(submission_rows) > limit
    submission_rows = submission_rows[:limit]
    submission_ids = [str(row.submission_id) for row in submission_rows]
    if not submission_ids:
        return jsonify(
            {
                "success": True,
                "data": {
                    "items": [],
                    "has_more": False,
                    "limit": limit,
                    "next_offset": None,
                },
            }
        )

    clean_tasks = (
        PublicationTask.query.options(load_only(*WEB_SUBMISSION_HISTORY_COLUMNS))
        .filter(
            PublicationTask.content_web_submission_id.in_(submission_ids),
            PublicationTask.content_submission_channel == WEB_SUBMISSION_CHANNEL,
            PublicationTask.user_id == user_id,
        )
        .order_by(PublicationTask.created_at.desc(), PublicationTask.id.desc())
        .all()
    )
    clean_task_ids = [str(task.id) for task in clean_tasks]
    publish_tasks = (
        PublicationTask.query.options(load_only(*PUBLISH_HISTORY_COLUMNS))
        .filter(
            PublicationTask.feature == "zsxq_clean_output_publish",
            PublicationTask.source_task_id.in_(clean_task_ids),
            PublicationTask.user_id == user_id,
        )
        .order_by(PublicationTask.created_at.desc(), PublicationTask.id.desc())
        .all()
    )
    publish_by_source = {}
    for task in publish_tasks:
        publish_by_source.setdefault(str(task.source_task_id), task)

    tasks_by_submission = {submission_id: [] for submission_id in submission_ids}
    for task in clean_tasks:
        submission_id = str(task.content_web_submission_id or "")
        if submission_id in tasks_by_submission:
            tasks_by_submission[submission_id].append(task)

    items = []
    for row in submission_rows:
        submission_id = str(row.submission_id)
        items.append(
            _web_submission_history_item(
                submission_id,
                row.submitted_at,
                tasks_by_submission.get(submission_id) or [],
                publish_by_source,
            )
        )
    return jsonify(
        {
            "success": True,
            "data": {
                "items": items,
                "has_more": has_more,
                "limit": limit,
                "next_offset": offset + len(submission_rows) if has_more else None,
            },
        }
    )


def _web_submission_history_item(
    submission_id,
    submitted_at,
    clean_tasks,
    publish_by_source,
):
    kind_by_feature = {
        "content_pdf_clean": "pdf",
        "content_audio_clean": "mp3",
    }
    task_items = []
    for clean_task in clean_tasks:
        kind = kind_by_feature.get(str(clean_task.feature or ""))
        if not kind:
            continue
        publish_task = publish_by_source.get(str(clean_task.id))
        status_source = (
            publish_task
            if str(clean_task.status or "").lower() == "completed" and publish_task
            else clean_task
        )
        item_status = _web_submission_task_status(clean_task, publish_task)
        task_items.append(
            {
                "task_id": str(clean_task.id),
                "kind": kind,
                "status": item_status,
                "progress": max(0, min(100, int(status_source.progress or 0))),
                "current_step": str(status_source.current_step or ""),
                "publish_task_id": str(publish_task.id) if publish_task else None,
                "clean_status": _normalized_history_status(clean_task.status),
                "publish_status": (
                    _normalized_history_status(publish_task.status)
                    if publish_task
                    else None
                ),
                "files_count": int(clean_task.content_clean_files_count or 0),
                "success_count": int(clean_task.content_clean_success_count or 0),
                "failed_count": int(clean_task.content_clean_failed_count or 0),
                "error": str(status_source.error_message or "") or None,
                "updated_at": _history_datetime(status_source.updated_at),
            }
        )

    task_items.sort(key=lambda item: 0 if item["kind"] == "pdf" else 1)
    statuses = [item["status"] for item in task_items]
    return {
        "submission_id": str(submission_id),
        "submitted_at": _history_datetime(submitted_at),
        "input_mode": str(
            next(
                (
                    task.content_submission_input_mode
                    for task in clean_tasks
                    if task.content_submission_input_mode
                ),
                "upload",
            )
        ),
        "media_types": [item["kind"] for item in task_items],
        "status": _aggregate_history_status(statuses),
        "progress": (
            round(sum(item["progress"] for item in task_items) / len(task_items))
            if task_items
            else 0
        ),
        "tasks": task_items,
    }


def _normalized_history_status(value):
    status = str(value or "pending").strip().lower()
    if status in {"cancelled", "canceled"}:
        return "failed"
    if status in {"pending", "processing", "completed", "partial_failed", "failed"}:
        return status
    return "processing"


def _web_submission_task_status(clean_task, publish_task):
    clean_status = _normalized_history_status(clean_task.status)
    failed_count = max(0, int(clean_task.content_clean_failed_count or 0))
    if clean_status != "completed":
        return clean_status

    if publish_task is not None:
        publish_status = _normalized_history_status(publish_task.status)
        if publish_status in {"pending", "processing", "failed"}:
            return publish_status
    return "partial_failed" if failed_count else "completed"


def _aggregate_history_status(statuses):
    if not statuses:
        return "pending"
    if all(status == "completed" for status in statuses):
        return "completed"
    if all(status == "failed" for status in statuses):
        return "failed"
    if "processing" in statuses:
        return "processing"
    if "pending" in statuses:
        return "pending"
    if "failed" in statuses or "partial_failed" in statuses:
        return "partial_failed"
    return "pending"


def _record_failed_web_submission_task(
    *,
    kind,
    title,
    user_id,
    metadata,
    files_count,
    error,
):
    """Persist a compact receipt when task creation or broker handoff raises."""
    feature_by_kind = {
        "pdf": "content_pdf_clean",
        "mp3": "content_audio_clean",
    }
    feature = feature_by_kind.get(str(kind or "").lower())
    submission_id = str((metadata or {}).get("web_submission_id") or "").strip()
    owner_id = str(user_id or "").strip()
    if not feature or not submission_id or not owner_id:
        return None

    db.session.rollback()
    try:
        existing = (
            PublicationTask.query.options(load_only(*WEB_SUBMISSION_HISTORY_COLUMNS))
            .filter(
                PublicationTask.feature == feature,
                PublicationTask.user_id == owner_id,
                PublicationTask.content_submission_channel == WEB_SUBMISSION_CHANNEL,
                PublicationTask.content_web_submission_id == submission_id,
            )
            .order_by(PublicationTask.created_at.desc(), PublicationTask.id.desc())
            .first()
        )
        if existing is not None:
            return existing

        count = max(0, int(files_count or 0))
        clean_key = "pdf_clean" if kind == "pdf" else "audio_clean"
        message = str(error or "清洗任务提交失败")
        task = PublicationTask(
            status="failed",
            current_step="submit_failed",
            progress=0,
            completed_at=datetime.utcnow(),
            error_message=message,
            params={
                "feature": feature,
                "user_id": owner_id,
                "content_pipeline": {
                    "title": title,
                    "trigger": "web_upload",
                    "metadata": dict(metadata or {}),
                    "auto_publish": {"enabled": True},
                    clean_key: {"files": [], "source_root": None},
                },
            },
            result={
                "files_count": count,
                "success_count": 0,
                "failed_count": count,
                "message": "网页投递任务创建失败",
            },
        )
        db.session.add(task)
        db.session.commit()
        return task
    except Exception:
        db.session.rollback()
        logger.exception(
            "网页投递失败回执持久化失败 | submission=%s kind=%s",
            submission_id,
            kind,
        )
        return None


def _history_datetime(value):
    if not value:
        return None
    if value.tzinfo is None:
        return f"{value.isoformat()}Z"
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


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
