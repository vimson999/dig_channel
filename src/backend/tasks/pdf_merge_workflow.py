"""Celery execution for the durable, ordered PDF merge workflow."""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from sqlalchemy.orm.attributes import flag_modified


backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from celery_config import celery_app
from models import db
from models.content_pipeline_asset import ContentPipelineAsset
from models.task import PublicationTask
from services.content_pipeline.canonical_asset_service import (
    register_cleaned_outputs,
    resolve_cleaned_asset,
)
from services.content_pipeline.dedupe_service import mark_task_assets, reserve_files
from services.pdf_merge_workflow import (
    FEATURE_NAME,
    PdfMergeError,
    fingerprint_file,
    merge_pdf_sources,
    safe_task_file,
    validate_source_collection,
    workflow_output_path,
    workflow_upload_root,
)
from services.runtime_guard import guard_publication_task_start, retry_exception_types


logger = logging.getLogger(__name__)
TASK_LABEL = "PDF 合并"


@celery_app.task(bind=True, name="tasks.pdf_merge_workflow.process_job")
def process_job(self, task_id):
    """Run one merge job and persist its user-visible lifecycle."""

    return _run_pdf_merge_job(task_id, celery_task=self)


def _run_pdf_merge_job(task_id: str, *, celery_task=None) -> bool:
    app = _create_pdf_merge_app()
    with app.app_context():
        task_key = str(task_id or "").strip()
        try:
            if celery_task is not None:
                guard_publication_task_start(
                    celery_task,
                    task_key,
                    feature=FEATURE_NAME,
                    queue="content_pipeline",
                )
            task = _load_task(task_key)
            if task is None:
                logger.error("PDF merge task not found: %s", task_key)
                return False
            if str(task.status or "").lower() == "canceled":
                return False

            workflow = _workflow(task)
            sources = _resolve_sources(task_key, workflow)
            progress_result = dict(task.result or {})
            _persist_task(
                task_key,
                status="processing",
                current_step="validating",
                progress=5,
                error_message=None,
                completed_at=None,
                result=_with_log(progress_result, "validating", "输入 PDF 已冻结，开始校验", 5),
            )

            output_path = workflow_output_path(task_key, workflow.get("title"))

            def on_progress(completed: int, total: int, page_count: int) -> None:
                progress = 10 + int((completed / max(total, 1)) * 80)
                _persist_task(
                    task_key,
                    status="processing",
                    current_step="merging",
                    progress=progress,
                    result=_with_log(
                        progress_result,
                        "merging",
                        f"已合并 {completed}/{total} 个 PDF，共 {page_count} 页",
                        progress,
                    ),
                )

            merged = merge_pdf_sources(sources, output_path, progress_callback=on_progress)
            _persist_task(
                task_key,
                status="processing",
                current_step="registering",
                progress=95,
                result=_with_log(progress_result, "registering", "正在登记可复用 PDF 资产", 95),
            )
            output = _register_or_reuse_output(task_key, merged.path)
            output.update({
                "filename": output.get("filename") or merged.path.name,
                "bytes": int(output.get("bytes") or merged.bytes),
                "page_count": merged.page_count,
                "sha256": output.get("sha256") or merged.sha256,
            })
            final_result = _with_log(
                progress_result,
                "completed",
                "PDF 合并完成，产物已登记为可复用资产",
                100,
            )
            final_result["output"] = output
            final_result["source_count"] = len(sources)
            final_result["message"] = "PDF 合并完成"
            _persist_task(
                task_key,
                status="completed",
                current_step="completed",
                progress=100,
                error_message=None,
                completed_at=datetime.utcnow(),
                result=final_result,
            )
            _notify("workflow_completed", task_key, workflow, final_result)
            return True
        except retry_exception_types():
            raise
        except Exception as exc:
            db.session.rollback()
            _mark_failed(task_key, exc)
            return False


def _create_pdf_merge_app():
    """Build the normal app context, with a compact fallback for workers."""

    from app import create_app

    try:
        return create_app()
    except Exception:
        logger.exception("Full Flask app initialization failed for PDF merge worker")

    from flask import Flask
    from config import Config

    app = Flask(__name__)
    app.config.from_object(Config)
    db.init_app(app)
    return app


def _load_task(task_id: str) -> PublicationTask | None:
    return PublicationTask.query.filter(
        PublicationTask.id == str(task_id),
        PublicationTask.feature == FEATURE_NAME,
    ).one_or_none()


def _workflow(task: PublicationTask) -> dict[str, Any]:
    params = task.params if isinstance(task.params, dict) else {}
    workflow = params.get(FEATURE_NAME)
    if not isinstance(workflow, dict):
        raise PdfMergeError("PDF 合并任务参数缺失")
    sources = workflow.get("sources")
    if not isinstance(sources, list):
        raise PdfMergeError("PDF 合并任务来源缺失")
    validate_source_collection(sources)
    return dict(workflow)


def _resolve_sources(task_id: str, workflow: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw_sources = workflow.get("sources") if isinstance(workflow.get("sources"), list) else []
    values = validate_source_collection(raw_sources)
    upload_root = workflow_upload_root(task_id, create=False)
    resolved: list[dict[str, Any]] = []
    for source in values:
        kind = str(source.get("kind") or "").strip()
        if kind == "upload":
            path = safe_task_file(upload_root, str(source.get("path") or ""))
            resolved.append({**source, "path": str(path)})
            continue
        if kind != "pipeline_asset":
            raise PdfMergeError("PDF 来源类型不受支持")

        asset_id = str(source.get("asset_id") or "").strip()
        asset_ref = str(source.get("asset_ref") or "").strip()
        if not asset_id or asset_ref != f"pipeline:{asset_id}":
            raise PdfMergeError("PDF 资产引用不正确")
        asset = ContentPipelineAsset.query.filter(
            ContentPipelineAsset.id == asset_id,
            ContentPipelineAsset.media_type == "pdf",
        ).one_or_none()
        resolution = resolve_cleaned_asset(asset, media_type="pdf")
        if resolution.state != "ready" or not resolution.path or not resolution.asset:
            raise PdfMergeError("PDF 资产不可用，请重新选择")
        expected_hash = str(source.get("cleaned_sha256") or "").strip().lower()
        expected_bytes = _positive_int(source.get("bytes"), "PDF 资产大小不正确")
        if not expected_hash:
            raise PdfMergeError("PDF 资产指纹缺失")
        if (
            str(resolution.asset.cleaned_sha256 or "").strip().lower() != expected_hash
            or int(resolution.asset.cleaned_bytes or 0) != expected_bytes
        ):
            raise PdfMergeError("PDF 资产内容已变化，请重新选择")
        fingerprint = fingerprint_file(resolution.path)
        if fingerprint["sha256"] != expected_hash or fingerprint["bytes"] != expected_bytes:
            raise PdfMergeError("PDF 资产内容已变化，请重新选择")
        resolved.append({
            **source,
            "path": fingerprint["path"],
            "sha256": expected_hash,
            "bytes": expected_bytes,
            "filename": source.get("filename") or resolution.asset.filename or resolution.path.name,
        })
    return resolved


def _register_or_reuse_output(task_id: str, output_path: Path | str) -> dict[str, Any]:
    """Register a verified output or point the job at the canonical duplicate."""

    output = Path(output_path).expanduser().resolve()
    dedupe = reserve_files(
        task_id=str(task_id),
        media_type="pdf",
        files=[str(output)],
        source_mode="pdf_merge",
    )
    unique = list(dedupe.get("unique_files") or [])
    if unique:
        registration = register_cleaned_outputs(
            str(task_id),
            media_type="pdf",
            cleaned_files=[{
                "source_path": str(output),
                "output_path": str(output),
                "status": "success",
            }],
        )
        if len(registration.registered_asset_ids) != 1:
            raise RuntimeError("合并产物资产登记失败")
        asset = db.session.get(ContentPipelineAsset, registration.registered_asset_ids[0])
        resolution = resolve_cleaned_asset(asset, media_type="pdf")
        if resolution.state != "ready" or not resolution.path or not resolution.asset:
            raise RuntimeError("合并产物资产不可用")
        return _asset_payload(resolution.asset, resolution.path, reused_asset=False)

    duplicates = list(dedupe.get("duplicates") or [])
    canonical_id = str((duplicates[0] if duplicates else {}).get("canonical_asset_id") or "").strip()
    asset = db.session.get(ContentPipelineAsset, canonical_id) if canonical_id else None
    resolution = resolve_cleaned_asset(asset, media_type="pdf")
    if resolution.state != "ready" or not resolution.path or not resolution.asset:
        raise RuntimeError("相同合并产物尚不可复用")
    if output != resolution.path.resolve():
        try:
            output.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            logger.warning("Unable to remove duplicate merge output: %s", output)
    return _asset_payload(resolution.asset, resolution.path, reused_asset=True)


def _asset_payload(asset: ContentPipelineAsset, path: Path, *, reused_asset: bool) -> dict[str, Any]:
    return {
        "asset_id": str(asset.id),
        "asset_ref": f"pipeline:{asset.id}",
        "filename": asset.filename or path.name,
        "bytes": int(asset.cleaned_bytes or path.stat().st_size),
        "sha256": asset.cleaned_sha256,
        "reused_asset": bool(reused_asset),
    }


def _persist_task(
    task_id: str,
    *,
    status: str | None = None,
    current_step: str | None = None,
    progress: int | None = None,
    error_message: str | None = None,
    completed_at: datetime | None = None,
    result: dict[str, Any] | None = None,
) -> None:
    task = _load_task(task_id)
    if task is None:
        raise RuntimeError("PDF 合并任务不存在")
    if status is not None:
        task.status = status
    if current_step is not None:
        task.current_step = current_step
    if progress is not None:
        task.progress = max(0, min(100, int(progress)))
    task.error_message = error_message
    task.completed_at = completed_at
    if result is not None:
        task.result = dict(result)
        flag_modified(task, "result")
    task.sync_projection_fields()
    db.session.commit()


def _mark_failed(task_id: str, exc: Exception) -> None:
    if not task_id:
        return
    try:
        task = _load_task(task_id)
        if task is None:
            return
        previous_result = dict(task.result or {})
        previous_result["error"] = str(exc)
        result = _with_log(previous_result, "failed", str(exc), int(task.progress or 0))
        _persist_task(
            task_id,
            status="failed",
            current_step="failed",
            progress=int(task.progress or 0),
            error_message=str(exc),
            completed_at=datetime.utcnow(),
            result=result,
        )
        mark_task_assets(task_id, status="failed")
        _notify("workflow_failed", task_id, _workflow(task), result, error=str(exc))
    except Exception:
        db.session.rollback()
        logger.exception("Unable to persist PDF merge failure: %s", task_id)


def _with_log(result: Mapping[str, Any] | None, stage: str, message: str, progress: int) -> dict[str, Any]:
    merged = dict(result or {})
    logs = [item for item in (merged.get("logs") or []) if isinstance(item, dict)][-79:]
    logs.append({
        "stage": stage,
        "message": str(message),
        "progress": max(0, min(100, int(progress))),
        "at": datetime.utcnow().isoformat() + "Z",
    })
    merged["logs"] = logs
    return merged


def _notify(
    event_type: str,
    task_id: str,
    workflow: Mapping[str, Any] | None,
    result: Mapping[str, Any] | None = None,
    *,
    error: str | None = None,
) -> dict[str, Any]:
    try:
        from services.content_pipeline.notification_service import notify_content_pipeline_event

        pipeline = {
            "title": str((workflow or {}).get("title") or TASK_LABEL),
            "trigger": FEATURE_NAME,
            "metadata": {
                "source_mode": "pdf_merge",
                "orchestration_id": str(task_id),
            },
        }
        return notify_content_pipeline_event(
            event_type,
            task_id=str(task_id),
            task_label=TASK_LABEL,
            pipeline=pipeline,
            result=dict(result or {}),
            error=error,
        )
    except Exception:
        logger.exception("PDF merge notification failed: %s", task_id)
        return {"queued": False, "reason": "queue_failed"}


def _positive_int(value: Any, error_message: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise PdfMergeError(error_message) from exc
    if result <= 0:
        raise PdfMergeError(error_message)
    return result


__all__ = ["_register_or_reuse_output", "_run_pdf_merge_job", "process_job"]
