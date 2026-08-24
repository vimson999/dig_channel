"""Unified milestone notifications for all content-pipeline entry points.

The browser sidebar, scheduled ZSXQ sync, and manual file submission have
different upstreams, but their clean/publish lifecycle is the same.  Workflow
backed runs already emit notifications from ``ZsxqWorkflowService``; this
adapter fills the gap for direct (non-workflow) submissions without creating
duplicate mail for workflow-owned runs.
"""

from __future__ import annotations

import logging
from typing import Any


logger = logging.getLogger(__name__)


def pipeline_metadata(pipeline: dict[str, Any] | None) -> dict[str, Any]:
    """Return the flattened metadata used to identify an upstream run."""
    pipeline = pipeline if isinstance(pipeline, dict) else {}
    metadata = pipeline.get("metadata")
    metadata = dict(metadata) if isinstance(metadata, dict) else {}
    nested = metadata.get("source_metadata")
    if isinstance(nested, dict):
        merged = dict(nested)
        merged.update({key: value for key, value in metadata.items() if key != "source_metadata"})
        metadata = merged
    return metadata


def workflow_owned(pipeline: dict[str, Any] | None) -> bool:
    """Whether the legacy workflow state machine owns milestone notifications."""
    return bool(pipeline_metadata(pipeline).get("workflow_run_id"))


def notify_content_pipeline_event(
    event_type: str,
    *,
    task_id: str | None = None,
    task_label: str = "内容流水线",
    pipeline: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
    error: str | None = None,
    stage_status: str | None = None,
) -> dict[str, Any]:
    """Queue one common workflow-notification event for a direct pipeline run.

    Workflow-backed runs intentionally return ``workflow_owned`` because the
    ZSXQ workflow state machine already sends the same milestone events and
    also needs to advance its state.  Direct scheduled/manual runs use this
    adapter and therefore receive the same email contract.
    """
    pipeline = pipeline if isinstance(pipeline, dict) else {}
    if workflow_owned(pipeline):
        return {"queued": False, "reason": "workflow_owned"}

    metadata = pipeline_metadata(pipeline)
    result = result if isinstance(result, dict) else {}
    event_type = str(event_type or "workflow").strip() or "workflow"
    task_key = str(task_id or "-")
    orchestration_id = str(
        metadata.get("orchestration_id")
        or metadata.get("batch_id")
        or metadata.get("sync_run_id")
        or task_key
    )
    batch_id = str(metadata.get("batch_id") or metadata.get("sync_run_id") or orchestration_id)
    status = stage_status or _event_status(event_type)
    stage = _event_stage(event_type)
    stage_task = _stage_task_summary(event_type, task_key, result, status)
    stage_results = {stage: {"status": status, "tasks": [stage_task]}}
    if error:
        stage_results[stage]["error"] = str(error)[:1000]

    payload = {
        "id": orchestration_id,
        "batch_id": batch_id,
        "group_id": metadata.get("group_id"),
        "group_name": metadata.get("group_name") or pipeline.get("title") or task_label,
        "group_label": "星球" if metadata.get("group_id") else "内容流水线",
        "trigger": pipeline.get("trigger") or metadata.get("trigger") or "content_pipeline",
        "source_mode": metadata.get("source_mode") or metadata.get("mode") or "direct",
        "status": status,
        "current_stage": stage,
        "event_message": _event_message(event_type, task_label, result),
        "error_message": str(error)[:1000] if error else None,
        "stage_results": stage_results,
        "content_pipeline_task_id": task_key,
    }
    try:
        from services.workflow_notification_service import workflow_notification_service

        queued = workflow_notification_service.enqueue_workflow_event(event_type, payload)
        logger.info(
            "[CONTENT_PIPELINE_NOTIFY] event=%s task=%s queued=%s reason=%s",
            event_type,
            task_key,
            bool(queued.get("queued")),
            queued.get("reason") or "-",
        )
        return queued
    except Exception as exc:  # notification must never fail the pipeline
        logger.exception(
            "[CONTENT_PIPELINE_NOTIFY] failed | event=%s task=%s",
            event_type,
            task_key,
        )
        return {"queued": False, "reason": "queue_failed", "error": str(exc)}


def _event_stage(event_type: str) -> str:
    if event_type.startswith("publish_"):
        return "publish"
    if event_type.startswith("clean_"):
        return "clean"
    return "workflow"


def _event_status(event_type: str) -> str:
    suffix = event_type.rsplit("_", 1)[-1]
    return {
        "submitted": "queued",
        "started": "running",
        "completed": "completed",
        "partial": "partial",
        "failed": "failed",
        "skipped": "skipped",
    }.get(suffix, "running")


def _stage_task_summary(event_type: str, task_id: str, result: dict[str, Any], status: str) -> dict[str, Any]:
    if event_type.startswith("publish_"):
        publish = result.get("auto_publish") if isinstance(result.get("auto_publish"), dict) else result
        summary = publish.get("summary") if isinstance(publish.get("summary"), dict) else {}
        return {
            "task_id": task_id,
            "status": status,
            "selected_count": _number(publish.get("selected_count"), summary.get("selected_count")),
            "uploaded_count": _number(publish.get("uploaded_count"), summary.get("uploaded_count")),
            "submitted_count": _number(publish.get("submitted_count"), summary.get("submitted_count")),
            "verified_count": _number(publish.get("verified_count"), summary.get("verified_count")),
            "pending_verify_count": _number(publish.get("pending_verify_count"), summary.get("pending_verify_count")),
            "failed_count": _number(publish.get("failed_count"), summary.get("failed_count")),
        }

    if not event_type.startswith("clean_"):
        return {"task_id": task_id, "status": status}

    cleaned = result.get("cleaned_files") if isinstance(result.get("cleaned_files"), list) else []
    success_count = _number(result.get("success_count"), len(cleaned))
    failed_count = _number(result.get("failed_count"))
    files = result.get("files") if isinstance(result.get("files"), list) else []
    dedupe = result.get("dedupe") if isinstance(result.get("dedupe"), dict) else {}
    return {
        "task_id": task_id,
        "status": status,
        "files_count": _number(result.get("files_count"), len(files), success_count + failed_count),
        "success_count": success_count,
        "failed_count": failed_count,
        "received_count": _number(dedupe.get("received_count"), len(files)),
        "unique_count": _number(dedupe.get("unique_count"), len(files)),
        "duplicate_count": _number(dedupe.get("duplicate_count")),
    }


def _event_message(event_type: str, task_label: str, result: dict[str, Any]) -> str:
    if event_type.endswith("submitted"):
        return f"{task_label}已进入编排队列。"
    if event_type.endswith("completed"):
        return f"{task_label}节点已完成。"
    if event_type.endswith("skipped"):
        return f"{task_label}节点已跳过。"
    if event_type.endswith("failed"):
        return f"{task_label}节点失败。"
    return f"{task_label}节点状态已更新。"


def _number(*values: Any) -> int:
    for value in values:
        try:
            if value is not None:
                return max(0, int(value))
        except (TypeError, ValueError):
            continue
    return 0


__all__ = ["notify_content_pipeline_event", "pipeline_metadata", "workflow_owned"]
