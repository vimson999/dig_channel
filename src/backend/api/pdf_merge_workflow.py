"""Authenticated API for the durable, ordered PDF merge workflow."""

from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from flask import Blueprint, jsonify, request, send_file
from sqlalchemy.orm.attributes import flag_modified

from models import db
from models.content_pipeline_asset import ContentPipelineAsset
from models.task import PublicationTask
from services.content_pipeline.canonical_asset_service import resolve_cleaned_asset
from services.pdf_merge_workflow import (
    FEATURE_NAME,
    PdfMergeError,
    fingerprint_file,
    max_pdf_merge_files,
    max_pdf_merge_single_bytes,
    max_pdf_merge_total_bytes,
    normalize_output_filename,
    safe_task_file,
    validate_pdf_source,
    validate_source_collection,
    workflow_upload_root,
)
from services.runtime_guard import maybe_guarded_apply_async
from tasks.pdf_merge_workflow import process_job
from utils.auth_utils import require_login


pdf_merge_workflow_bp = Blueprint(
    "pdf_merge_workflow",
    __name__,
    url_prefix="/api/pdf-merge-workflows",
)
logger = logging.getLogger(__name__)
QUEUE_NAME = "content_pipeline"
TASK_LABEL = "PDF 合并"
TERMINAL_RETRY_STATUSES = {"failed", "canceled"}
UPLOAD_COPY_CHUNK_BYTES = 1024 * 1024


@pdf_merge_workflow_bp.route("/jobs", methods=["POST"])
@require_login
def create_job(current_user):
    """Freeze a mixed ordered input list and enqueue its merge exactly once."""

    user_id = _user_id(current_user)
    if not user_id:
        return _error("当前用户不可用", 401)

    try:
        items = _submission_items()
        title = normalize_output_filename(_submission_title())
        # ``normalize_output_filename`` also removes path components before the
        # title reaches a task payload or a future Content-Disposition header.
        title = _title_without_extension(title)
        task = PublicationTask(
            feature=FEATURE_NAME,
            user_id=user_id,
            status="pending",
            current_step="freezing_inputs",
            progress=0,
            params={"feature": FEATURE_NAME, "user_id": user_id, FEATURE_NAME: {}},
            result={"logs": []},
        )
        task.task_title = title
        db.session.add(task)
        db.session.flush()
        task_id = str(task.id)
        sources = _freeze_sources(task_id, items, request.files.getlist("files"))
        validate_source_collection(sources)
        workflow = {
            "schema_version": "20260831_pdf_merge_v1",
            "title": title,
            "sources": sources,
            "submitted_at": _iso_now(),
        }
        task.params = {
            "feature": FEATURE_NAME,
            "user_id": user_id,
            FEATURE_NAME: workflow,
        }
        task.result = {
            "logs": [_log("queued", "已冻结 PDF 输入顺序，等待合并", 0)],
            "source_count": len(sources),
        }
        task.status = "pending"
        task.current_step = "queued"
        task.progress = 0
        task.error_message = None
        task.completed_at = None
        task.sync_projection_fields()
        flag_modified(task, "params")
        flag_modified(task, "result")
        payload = _job_payload(task)
        db.session.commit()
    except (PdfMergeError, ValueError) as exc:
        db.session.rollback()
        _cleanup_task_uploads(locals().get("task_id"))
        return _error(str(exc), 400)
    except Exception as exc:
        db.session.rollback()
        _cleanup_task_uploads(locals().get("task_id"))
        logger.exception("Unable to freeze PDF merge submission")
        return _error("PDF 合并任务创建失败", 500)

    try:
        submission = maybe_guarded_apply_async(
            process_job,
            args=[task_id],
            queue=QUEUE_NAME,
            feature=FEATURE_NAME,
            task_id=task_id,
            retry=False,
        )
        celery_task_id = str(submission["async_result"].id)
    except Exception as exc:
        _mark_enqueue_failure(task_id, f"PDF 合并任务入队失败: {exc}")
        return _error("PDF 合并任务入队失败", 502)

    PublicationTask.query.filter(PublicationTask.id == task_id).update(
        {
            PublicationTask.celery_task_id: celery_task_id,
            PublicationTask.updated_at: datetime.utcnow(),
        },
        synchronize_session=False,
    )
    db.session.commit()
    payload["celery_task_id"] = celery_task_id
    payload["deferred"] = bool(submission.get("deferred"))
    payload["admission"] = submission.get("admission")
    deferred_state = submission.get("deferred_state")
    if isinstance(deferred_state, Mapping) and deferred_state.get("current_step"):
        payload["current_step"] = deferred_state["current_step"]
    return jsonify({
        "success": True,
        "message": "PDF 合并任务已提交",
        "data": payload,
        **payload,
    }), 202


@pdf_merge_workflow_bp.route("/jobs", methods=["GET"])
@require_login
def list_jobs(current_user):
    user_id = _user_id(current_user)
    page = _bounded_int(request.args.get("page"), default=1, minimum=1, maximum=100000)
    per_page = _bounded_int(request.args.get("per_page"), default=20, minimum=1, maximum=100)
    query = PublicationTask.query.filter(
        PublicationTask.feature == FEATURE_NAME,
        PublicationTask.user_id == user_id,
    ).order_by(PublicationTask.created_at.desc(), PublicationTask.id.desc())
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    items = [_job_payload(task) for task in pagination.items]
    data = {
        "items": items,
        "pagination": {
            "page": page,
            "per_page": per_page,
            "total": pagination.total,
            "pages": pagination.pages,
            "has_more": page < pagination.pages,
        },
    }
    return jsonify({"success": True, "data": data, **data})


@pdf_merge_workflow_bp.route("/jobs/<job_id>", methods=["GET"])
@require_login
def get_job(current_user, job_id):
    task = _owned_task(current_user, job_id)
    if not task:
        return _error("任务不存在", 404)
    payload = _job_payload(task)
    return jsonify({"success": True, "data": payload, **payload})


@pdf_merge_workflow_bp.route("/jobs/<job_id>/cancel", methods=["POST"])
@require_login
def cancel_job(current_user, job_id):
    task = _owned_task(current_user, job_id)
    if not task:
        return _error("任务不存在", 404)
    if str(task.status or "").lower() != "pending":
        return _error("只有排队中的 PDF 合并任务可以取消", 409)

    result = dict(task.result or {})
    task.status = "canceled"
    task.current_step = "canceled"
    task.error_message = None
    task.completed_at = datetime.utcnow()
    task.result = _with_log(result, "canceled", "用户已取消 PDF 合并任务", int(task.progress or 0))
    flag_modified(task, "result")
    task.sync_projection_fields()
    db.session.commit()
    payload = _job_payload(task)
    return jsonify({"success": True, "message": "PDF 合并任务已取消", "data": payload, **payload})


@pdf_merge_workflow_bp.route("/jobs/<job_id>/retry", methods=["POST"])
@require_login
def retry_job(current_user, job_id):
    task = _owned_task(current_user, job_id)
    if not task:
        return _error("任务不存在", 404)
    if str(task.status or "").lower() not in TERMINAL_RETRY_STATUSES:
        return _error("只有失败或已取消的 PDF 合并任务可以重试", 409)

    task_id = str(task.id)
    result = dict(task.result or {})
    result.pop("error", None)
    result.pop("output", None)
    task.status = "pending"
    task.current_step = "queued"
    task.progress = 0
    task.error_message = None
    task.completed_at = None
    task.celery_task_id = None
    task.result = _with_log(result, "retry_requested", "用户请求重新合并冻结的 PDF 输入", 0)
    flag_modified(task, "result")
    task.sync_projection_fields()
    payload = _job_payload(task)
    db.session.commit()

    try:
        submission = maybe_guarded_apply_async(
            process_job,
            args=[task_id],
            queue=QUEUE_NAME,
            feature=FEATURE_NAME,
            task_id=task_id,
            retry=False,
        )
        celery_task_id = str(submission["async_result"].id)
    except Exception as exc:
        _mark_enqueue_failure(task_id, f"PDF 合并任务重试入队失败: {exc}")
        return _error("PDF 合并任务重试入队失败", 502)

    PublicationTask.query.filter(PublicationTask.id == task_id).update(
        {
            PublicationTask.celery_task_id: celery_task_id,
            PublicationTask.updated_at: datetime.utcnow(),
        },
        synchronize_session=False,
    )
    db.session.commit()
    payload["celery_task_id"] = celery_task_id
    payload["deferred"] = bool(submission.get("deferred"))
    payload["admission"] = submission.get("admission")
    return jsonify({
        "success": True,
        "message": "PDF 合并任务已重新投递",
        "data": payload,
        **payload,
    }), 202


@pdf_merge_workflow_bp.route("/jobs/<job_id>/download", methods=["GET"])
@require_login
def download_job(current_user, job_id):
    task = _owned_task(current_user, job_id)
    if not task:
        return _error("任务不存在", 404)
    if str(task.status or "").lower() != "completed":
        return _error("PDF 合并任务尚未完成", 409)
    result = task.result if isinstance(task.result, Mapping) else {}
    output = result.get("output") if isinstance(result.get("output"), Mapping) else {}
    asset_id = str(output.get("asset_id") or "").strip()
    asset_ref = str(output.get("asset_ref") or "").strip()
    if not asset_id or asset_ref != f"pipeline:{asset_id}":
        return _error("PDF 合并产物不可用", 409)
    asset = ContentPipelineAsset.query.filter(
        ContentPipelineAsset.id == asset_id,
        ContentPipelineAsset.media_type == "pdf",
    ).one_or_none()
    resolution = resolve_cleaned_asset(asset, media_type="pdf")
    if resolution.state != "ready" or not resolution.path:
        return _error("PDF 合并产物不可用", 409)
    return send_file(
        resolution.path,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=normalize_output_filename(output.get("filename") or resolution.path.name),
        conditional=True,
    )


def _submission_items() -> list[dict[str, Any]]:
    raw = request.form.get("items")
    if raw is None and request.is_json:
        payload = request.get_json(silent=True) or {}
        raw = payload.get("items") if isinstance(payload, Mapping) else None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise PdfMergeError("PDF 列表格式不正确") from exc
    if not isinstance(raw, list):
        raise PdfMergeError("请按顺序选择至少 2 个 PDF")
    items = []
    for item in raw:
        if not isinstance(item, Mapping):
            raise PdfMergeError("PDF 列表格式不正确")
        kind = str(item.get("kind") or "").strip()
        if kind not in {"upload", "pipeline_asset"}:
            raise PdfMergeError("PDF 来源类型不受支持")
        items.append(dict(item))
    return items


def _submission_title() -> Any:
    if request.form.get("title") is not None:
        return request.form.get("title")
    if request.is_json:
        payload = request.get_json(silent=True) or {}
        if isinstance(payload, Mapping):
            return payload.get("title")
    return None


def _freeze_sources(task_id: str, items: list[dict[str, Any]], uploads) -> list[dict[str, Any]]:
    """Copy, fingerprint, and validate the exact request order before enqueue."""

    if len(items) < 2:
        raise PdfMergeError("至少需要 2 个 PDF 才能合并")
    if len(items) > max_pdf_merge_files():
        raise PdfMergeError(f"一次最多合并 {max_pdf_merge_files()} 个 PDF")

    upload_indices = []
    for item in items:
        if item.get("kind") != "upload":
            continue
        try:
            index = int(item.get("upload_index"))
        except (TypeError, ValueError) as exc:
            raise PdfMergeError("上传 PDF 序号不正确") from exc
        if index < 0 or index >= len(uploads):
            raise PdfMergeError("上传 PDF 不存在")
        upload_indices.append(index)
    if set(upload_indices) != set(range(len(uploads))):
        raise PdfMergeError("上传 PDF 与顺序列表不一致")

    frozen_assets: dict[int, dict[str, Any]] = {}
    frozen_total_bytes = 0
    for sequence, item in enumerate(items, start=1):
        if item.get("kind") != "pipeline_asset":
            continue
        frozen = _freeze_pipeline_asset(sequence, item)
        frozen_assets[sequence] = frozen
        frozen_total_bytes += int(frozen["bytes"])
    if frozen_total_bytes > max_pdf_merge_total_bytes():
        raise PdfMergeError("PDF 总大小超过限制")

    upload_root = workflow_upload_root(task_id)
    frozen_uploads: dict[int, dict[str, Any]] = {}
    for index in sorted(set(upload_indices)):
        file_storage = uploads[index]
        original_name = _safe_source_filename(getattr(file_storage, "filename", None))
        destination = safe_task_file(upload_root, f"input-{index + 1:04d}.pdf")
        uploaded_bytes = _copy_upload_bounded(
            file_storage,
            destination,
            current_total_bytes=frozen_total_bytes,
        )
        fingerprint = fingerprint_file(destination)
        if fingerprint["bytes"] != uploaded_bytes:
            raise PdfMergeError("上传 PDF 文件大小不正确")
        frozen = {
            **fingerprint,
            "filename": original_name,
        }
        validate_pdf_source(frozen)
        frozen_uploads[index] = frozen
        frozen_total_bytes += uploaded_bytes

    sources = []
    for sequence, item in enumerate(items, start=1):
        kind = str(item.get("kind") or "").strip()
        if kind == "upload":
            frozen = frozen_uploads[int(item["upload_index"])]
            sources.append({
                "sequence": sequence,
                "kind": "upload",
                "filename": frozen["filename"],
                "path": frozen["path"],
                "sha256": frozen["sha256"],
                "bytes": frozen["bytes"],
            })
            continue
        sources.append(frozen_assets[sequence])
    return sources


def _copy_upload_bounded(file_storage, destination: Path, *, current_total_bytes: int) -> int:
    """Stream one request upload under the configured file and batch limits."""

    if destination.exists():
        raise PdfMergeError("上传 PDF 存储冲突")
    stream = getattr(file_storage, "stream", None)
    if stream is None or not callable(getattr(stream, "read", None)):
        raise PdfMergeError("上传 PDF 无法读取")
    try:
        stream.seek(0)
    except (AttributeError, OSError):
        pass

    temporary = destination.with_suffix(".uploading")
    _remove_file(temporary)
    copied_bytes = 0
    single_limit = max_pdf_merge_single_bytes()
    total_limit = max_pdf_merge_total_bytes()
    try:
        with temporary.open("xb") as target:
            while True:
                chunk = stream.read(UPLOAD_COPY_CHUNK_BYTES)
                if not chunk:
                    break
                if not isinstance(chunk, (bytes, bytearray)):
                    raise PdfMergeError("上传 PDF 内容无效")
                copied_bytes += len(chunk)
                if copied_bytes > single_limit:
                    raise PdfMergeError("单个 PDF 超过大小限制")
                if current_total_bytes + copied_bytes > total_limit:
                    raise PdfMergeError("PDF 总大小超过限制")
                target.write(chunk)
        if copied_bytes <= 0:
            raise PdfMergeError("PDF 文件为空")
        temporary.replace(destination)
        return copied_bytes
    except Exception:
        _remove_file(temporary)
        _remove_file(destination)
        raise


def _freeze_pipeline_asset(sequence: int, item: Mapping[str, Any]) -> dict[str, Any]:
    asset_ref = str(item.get("asset_ref") or "").strip()
    if not asset_ref.startswith("pipeline:"):
        raise PdfMergeError("请选择已完成清洗的 PDF 资产")
    asset_id = asset_ref.split(":", 1)[1].strip()
    if not asset_id or asset_ref != f"pipeline:{asset_id}":
        raise PdfMergeError("PDF 资产引用不正确")
    asset = ContentPipelineAsset.query.filter(
        ContentPipelineAsset.id == asset_id,
        ContentPipelineAsset.media_type == "pdf",
    ).one_or_none()
    resolution = resolve_cleaned_asset(asset, media_type="pdf")
    if resolution.state != "ready" or not resolution.path or not resolution.asset:
        raise PdfMergeError("选择的 PDF 资产不可用，请重新选择")
    declared_bytes = int(resolution.asset.cleaned_bytes or 0)
    if declared_bytes <= 0:
        raise PdfMergeError("选择的 PDF 资产大小不正确")
    if declared_bytes > max_pdf_merge_single_bytes():
        raise PdfMergeError("单个 PDF 超过大小限制")
    fingerprint = fingerprint_file(resolution.path)
    if (
        fingerprint["sha256"] != str(resolution.asset.cleaned_sha256 or "").lower()
        or fingerprint["bytes"] != int(resolution.asset.cleaned_bytes or 0)
    ):
        raise PdfMergeError("选择的 PDF 资产内容已变化，请重新选择")
    validate_pdf_source({
        "path": fingerprint["path"],
        "filename": resolution.asset.filename or fingerprint["filename"],
        "sha256": fingerprint["sha256"],
        "bytes": fingerprint["bytes"],
    })
    return {
        "sequence": sequence,
        "kind": "pipeline_asset",
        "asset_ref": asset_ref,
        "asset_id": asset_id,
        "filename": _safe_source_filename(resolution.asset.filename or fingerprint["filename"]),
        "cleaned_sha256": fingerprint["sha256"],
        "bytes": fingerprint["bytes"],
    }


def _owned_task(current_user, job_id: str) -> PublicationTask | None:
    task = PublicationTask.query.filter(
        PublicationTask.id == str(job_id or "").strip(),
        PublicationTask.feature == FEATURE_NAME,
    ).one_or_none()
    if not task:
        return None
    return task if str(task.user_id or "") == _user_id(current_user) else None


def _job_payload(task: PublicationTask) -> dict[str, Any]:
    params = task.params if isinstance(task.params, Mapping) else {}
    workflow = params.get(FEATURE_NAME) if isinstance(params.get(FEATURE_NAME), Mapping) else {}
    result = task.result if isinstance(task.result, Mapping) else {}
    output = result.get("output") if isinstance(result.get("output"), Mapping) else None
    sources = []
    for source in workflow.get("sources") or []:
        if not isinstance(source, Mapping):
            continue
        item = {
            "sequence": int(source.get("sequence") or 0),
            "kind": str(source.get("kind") or ""),
            "filename": _safe_source_filename(source.get("filename")),
            "bytes": _nonnegative_int(source.get("bytes")),
        }
        if item["kind"] == "pipeline_asset":
            item["asset_ref"] = str(source.get("asset_ref") or "")
        sources.append(item)
    public_output = None
    if output:
        asset_id = str(output.get("asset_id") or "").strip()
        asset_ref = str(output.get("asset_ref") or "").strip()
        if asset_id and asset_ref == f"pipeline:{asset_id}":
            public_output = {
                "asset_id": asset_id,
                "asset_ref": asset_ref,
                "filename": normalize_output_filename(output.get("filename")),
                "bytes": _nonnegative_int(output.get("bytes")),
                "page_count": _nonnegative_int(output.get("page_count")),
                "sha256": str(output.get("sha256") or ""),
                "reused_asset": bool(output.get("reused_asset")),
            }
    logs = []
    for log in (result.get("logs") or [])[-80:]:
        if not isinstance(log, Mapping):
            continue
        logs.append({
            "stage": str(log.get("stage") or ""),
            "message": str(log.get("message") or ""),
            "progress": _nonnegative_int(log.get("progress")),
            "at": str(log.get("at") or ""),
        })
    payload = {
        "job_id": str(task.id),
        "status": str(task.status or "pending"),
        "current_step": str(task.current_step or ""),
        "progress": _nonnegative_int(task.progress),
        "title": _title_without_extension(normalize_output_filename(workflow.get("title"))),
        "sources": sources,
        "source_count": len(sources),
        "output": public_output,
        "error_message": str(task.error_message or "") or None,
        "created_at": _iso(task.created_at),
        "updated_at": _iso(task.updated_at),
        "completed_at": _iso(task.completed_at),
        "celery_task_id": str(task.celery_task_id or "") or None,
        "logs": logs,
    }
    if public_output and str(task.status or "").lower() == "completed":
        payload["download_url"] = f"/api/pdf-merge-workflows/jobs/{task.id}/download"
    return payload


def _mark_enqueue_failure(task_id: str, message: str) -> None:
    task = PublicationTask.query.filter(
        PublicationTask.id == task_id,
        PublicationTask.feature == FEATURE_NAME,
    ).one_or_none()
    if not task:
        return
    result = dict(task.result or {})
    result["error"] = message
    task.status = "failed"
    task.current_step = "enqueue_failed"
    task.error_message = message
    task.completed_at = datetime.utcnow()
    task.result = _with_log(result, "enqueue_failed", message, int(task.progress or 0))
    flag_modified(task, "result")
    task.sync_projection_fields()
    db.session.commit()


def _cleanup_task_uploads(task_id: str | None) -> None:
    if not task_id:
        return
    try:
        root = workflow_upload_root(str(task_id), create=False)
    except (PdfMergeError, ValueError):
        return
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)


def _remove_file(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return
    except OSError:
        return


def _safe_source_filename(value: Any) -> str:
    raw = str(value or "").replace("\\", "/")
    filename = Path(raw).name
    filename = "".join(
        character for character in filename
        if character >= " " and character != "\x7f"
    ).strip(" .")
    if not filename.lower().endswith(".pdf"):
        raise PdfMergeError("只支持 PDF 文件")
    if not filename[:-4].strip(" ."):
        raise PdfMergeError("PDF 文件名为空")
    return normalize_output_filename(filename)


def _title_without_extension(filename: str) -> str:
    value = str(filename or "").strip()
    return value[:-4] if value.lower().endswith(".pdf") else value


def _with_log(result: Mapping[str, Any] | None, stage: str, message: str, progress: int) -> dict[str, Any]:
    payload = dict(result or {})
    logs = [item for item in (payload.get("logs") or []) if isinstance(item, Mapping)][-79:]
    logs.append(_log(stage, message, progress))
    payload["logs"] = logs
    return payload


def _log(stage: str, message: str, progress: int) -> dict[str, Any]:
    return {
        "stage": stage,
        "message": message,
        "progress": max(0, min(100, int(progress))),
        "at": _iso_now(),
    }


def _user_id(current_user) -> str:
    return str(getattr(current_user, "id", "") or "").strip()


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _nonnegative_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _iso(value) -> str | None:
    return value.isoformat() + "Z" if value else None


def _iso_now() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _error(message: str, status: int):
    return jsonify({"success": False, "message": str(message)}), status


__all__ = ["pdf_merge_workflow_bp"]
