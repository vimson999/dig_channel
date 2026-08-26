import logging
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from models import db
from models.task import PublicationTask
from services.platform_publish_dispatch import dispatch_platform_publish
from services.zsxq_auto_publish_service import (
    list_runs as auto_publish_list_runs,
    load_config as load_auto_publish_config,
    preview_payload as auto_publish_preview_payload,
    run_once_with_config,
)


logger = logging.getLogger(__name__)


DEFAULT_MAX_ROUNDS = 20


def submit_publish_clean_output_task(
    *,
    title,
    file_type,
    output_dir,
    approved_paths=None,
    options=None,
    source_task_id=None,
    trigger="content_pipeline_clean_complete",
    metadata=None,
    user_id=None,
):
    """Create a dedicated Celery task for publishing cleaned output."""
    options = options if isinstance(options, dict) else {}
    metadata = metadata if isinstance(metadata, dict) else {}

    if not _truthy(options.get("enabled", False)):
        return {
            "enabled": False,
            "queued": False,
            "reason": "disabled",
            "message": "清洗后自动发布未启用。",
        }

    normalized_type = _normalize_file_type(file_type)
    source_dir = Path(str(output_dir or "")).expanduser().resolve()
    if not source_dir.exists() or not source_dir.is_dir():
        raise ValueError(f"清洗输出目录不存在：{source_dir}")
    normalized_approved_paths = _normalize_approved_paths(
        approved_paths,
        source_dir=source_dir,
        file_type=normalized_type,
    )

    broker_task_id = str(uuid4())
    task = PublicationTask(
        celery_task_id=broker_task_id,
        status="pending",
        current_step="init",
        progress=0,
        params={
            "feature": "zsxq_clean_output_publish",
            "user_id": user_id,
            "zsxq_publish": {
                "title": (title or "知识星球清洗产物发布任务").strip() or "知识星球清洗产物发布任务",
                "trigger": trigger,
                "source_task_id": source_task_id,
                "metadata": metadata,
                "file_type": normalized_type,
                "output_dir": str(source_dir),
                "approved_paths": normalized_approved_paths,
                "options": options,
            },
        },
        result={
            "enabled": True,
            "queued": False,
            "file_type": normalized_type,
            "output_dir": str(source_dir),
            "approved_paths": normalized_approved_paths,
            "source_task_id": source_task_id,
            "trigger": trigger,
            "metadata": metadata,
        },
    )
    db.session.add(task)
    db.session.flush()
    task_id = str(task.id)
    task_updated_at = task.updated_at
    task_snapshot = task.to_dict()
    initial_result = deepcopy(task.result or {})
    db.session.commit()

    try:
        submit_result = dispatch_platform_publish(
            "zsxq",
            task_id,
            broker_task_id=broker_task_id,
            retry=False,
        )
    except Exception as exc:
        logger.exception("知识星球发布任务入队失败: %s", task_id)
        error_message = f"知识星球发布任务入队失败: {exc}"
        failed_result = {
            **initial_result,
            "queued": False,
            "status": "failed",
            "reason": "queue_failed",
            "message": error_message,
        }
        PublicationTask.query.filter(
            PublicationTask.id == task_id,
            PublicationTask.status == "pending",
            PublicationTask.celery_task_id == broker_task_id,
        ).update({
            PublicationTask.status: "failed",
            PublicationTask.current_step: "failed",
            PublicationTask.error_message: error_message,
            PublicationTask.result: failed_result,
            PublicationTask.completed_at: datetime.utcnow(),
            PublicationTask.updated_at: datetime.utcnow(),
        }, synchronize_session=False)
        db.session.commit()
        raise RuntimeError(error_message) from exc

    celery_task_id = str(submit_result["celery_id"])
    if celery_task_id != broker_task_id:
        raise RuntimeError(
            f"知识星球发布任务 broker ID 不一致: expected={broker_task_id} actual={celery_task_id}"
        )
    final_result = {
        **initial_result,
        "queued": not bool(submit_result.get("deferred")),
        "deferred": bool(submit_result.get("deferred")),
        "task_id": task_id,
        "celery_task_id": celery_task_id,
        "admission": submit_result.get("admission"),
        "message": "知识星球发布任务已提交。",
    }
    compare_and_swap = PublicationTask.query.filter(
        PublicationTask.id == task_id,
        PublicationTask.status == "pending",
        PublicationTask.celery_task_id == broker_task_id,
    )
    if task_updated_at is not None:
        compare_and_swap = compare_and_swap.filter(PublicationTask.updated_at == task_updated_at)
    compare_and_swap.update({
        PublicationTask.result: final_result,
        PublicationTask.updated_at: datetime.utcnow(),
    }, synchronize_session=False)
    db.session.commit()
    task_snapshot.update({
        "celery_task_id": celery_task_id,
        "result": final_result,
    })
    deferred_state = submit_result.get("deferred_state")
    if isinstance(deferred_state, dict):
        for key in ("status", "current_step", "error_message", "completed_at"):
            if key in deferred_state:
                task_snapshot[key] = deferred_state[key]
    try:
        from services.content_pipeline.notification_service import notify_content_pipeline_event

        notify_content_pipeline_event(
            "publish_submitted" if final_result.get("queued") else "publish_failed",
            task_id=task_id,
            task_label="知识星球发布",
            pipeline={"title": title, "trigger": trigger, "metadata": metadata},
            result=final_result,
            stage_status="queued" if final_result.get("queued") else "failed",
            error=None if final_result.get("queued") else final_result.get("message"),
        )
    except Exception:
        logger.exception("知识星球发布启动通知失败: %s", task_id)
    return task_snapshot


def publish_clean_output(
    *,
    file_type,
    output_dir,
    approved_paths=None,
    options=None,
    requested_by="content_pipeline",
):
    """Publish a completed content-pipeline output directory using the saved strategy.

    This is intentionally transient: it reuses the saved target group, hashtags,
    batch sizes and cookie pool, but does not rewrite the strategy source_dir.
    """
    options = options if isinstance(options, dict) else {}
    if not _truthy(options.get("enabled", False)):
        return {
            "enabled": False,
            "queued": False,
            "reason": "disabled",
            "message": "清洗后自动发布未启用。",
        }

    normalized_type = _normalize_file_type(file_type)
    source_dir = Path(str(output_dir or "")).expanduser().resolve()
    if not source_dir.exists() or not source_dir.is_dir():
        return {
            "enabled": True,
            "queued": False,
            "reason": "missing_output_dir",
            "message": f"清洗输出目录不存在：{source_dir}",
            "source_dir": str(source_dir),
        }
    normalized_approved_paths = _normalize_approved_paths(
        approved_paths,
        source_dir=source_dir,
        file_type=normalized_type,
    )

    config = _build_transient_publish_config(
        file_type=normalized_type,
        source_dir=source_dir,
        options=options,
        approved_paths=normalized_approved_paths,
    )
    max_rounds = _bounded_int(options.get("max_rounds"), DEFAULT_MAX_ROUNDS, lower=1, upper=100)
    runs = []

    for _round in range(1, max_rounds + 1):
        preview = auto_publish_preview_payload(config)
        rule_preview = _preview_for_type(preview, normalized_type)
        eligible_count = int((rule_preview or {}).get("eligible_count") or 0)
        if eligible_count <= 0:
            summary = _summarize_publish_runs(runs)
            return {
                "enabled": True,
                "queued": bool(runs),
                "status": "completed" if runs else "skipped",
                "reason": "no_eligible_files" if not runs else "drained",
                "message": "清洗输出目录已无待发布文件。" if runs else "清洗输出目录没有可发布文件。",
                "source_dir": str(source_dir),
                "rounds": runs,
                "preview": rule_preview,
                "summary": summary,
                **summary,
            }

        result = run_once_with_config(
            config,
            trigger=str(options.get("trigger") or "content_pipeline_clean_complete"),
            requested_by=requested_by,
        )
        result["round"] = _round
        runs.append(result)

        if result.get("skipped") or result.get("status") in {"failed"}:
            summary = _summarize_publish_runs(runs)
            return {
                "enabled": True,
                "queued": bool(runs),
                "status": result.get("status") or "failed",
                "reason": result.get("reason") or "run_failed",
                "message": result.get("message") or "清洗后自动发布失败。",
                "source_dir": str(source_dir),
                "rounds": runs,
                "preview": rule_preview,
                "summary": summary,
                **summary,
            }

    summary = _summarize_publish_runs(runs)
    return {
        "enabled": True,
        "queued": bool(runs),
        "status": "partial",
        "reason": "max_rounds_reached",
        "message": f"清洗后自动发布达到最大轮次 {max_rounds}，可能仍有未发布文件。",
        "source_dir": str(source_dir),
        "rounds": runs,
        "summary": summary,
        **summary,
    }


def _build_transient_publish_config(*, file_type, source_dir, options, approved_paths=None):
    base_config = load_auto_publish_config(required=True)
    config = deepcopy(base_config)
    config["enabled"] = True

    target_rule = _find_rule(config, file_type)
    if target_rule is None:
        target_rule = _default_rule(file_type)
        config.setdefault("rules", []).append(target_rule)

    for rule in config.get("rules") or []:
        rule["enabled"] = rule is target_rule

    target_rule["source_dir"] = str(source_dir)
    target_rule["recursive"] = True
    target_rule["file_type"] = file_type
    target_rule["extensions"] = [".pdf"] if file_type == "pdf" else [".mp3", ".m4a", ".wav", ".aac", ".flac", ".ogg"]
    if approved_paths is not None:
        target_rule["approved_paths"] = list(approved_paths)

    rule_options = options.get("rule") if isinstance(options.get("rule"), dict) else {}
    for key in (
        "batch_size",
        "body_prefix",
        "include_filenames",
        "hashtag_id",
        "hashtag_title",
        "upload_retry_count",
        "publish_retry_count",
        "publish_retry_delay_seconds",
        "publish_verify_attempts",
        "publish_verify_delay_seconds",
        "publish_verify_confirmations",
        "max_run_attempts",
        "max_file_size_mb",
    ):
        if key in rule_options:
            target_rule[key] = rule_options[key]

    if options.get("group_id"):
        config["group_id"] = str(options.get("group_id")).strip()
    if options.get("group_name"):
        config["group_name"] = str(options.get("group_name")).strip()
    requested_account_id = (
        options.get("publish_account_id")
        or options.get("credential_id")
        or options.get("cookie_account_id")
    )
    if requested_account_id:
        config["publish_account_id"] = str(requested_account_id).strip()

    return config


def _find_rule(config, file_type):
    for rule in config.get("rules") or []:
        if rule.get("file_type") == file_type or rule.get("id") == file_type:
            return rule
    return None


def _default_rule(file_type):
    if file_type == "pdf":
        return {
            "id": "pdf",
            "label": "PDF 批次",
            "file_type": "pdf",
            "batch_size": 8,
            "include_filenames": False,
            "extensions": [".pdf"],
        }
    return {
        "id": "audio",
        "label": "音频批次",
        "file_type": "audio",
        "batch_size": 8,
        "include_filenames": True,
        "extensions": [".mp3", ".m4a", ".wav", ".aac", ".flac", ".ogg"],
    }


def _preview_for_type(preview, file_type):
    for item in ((preview or {}).get("preview") or {}).get("rules") or []:
        rule = item.get("rule") or {}
        if rule.get("file_type") == file_type or rule.get("id") == file_type:
            return item
    return None


def _summarize_publish_runs(runs):
    totals = {
        "selected_count": 0,
        "uploaded_count": 0,
        "submitted_count": 0,
        "verified_count": 0,
        "pending_verify_count": 0,
        "failed_count": 0,
        "topic_count": 0,
        "verified_topic_count": 0,
        "pending_verify_topic_count": 0,
        "round_count": len(runs or []),
    }
    for run in runs or []:
        summary = run.get("summary") if isinstance(run, dict) and isinstance(run.get("summary"), dict) else None
        if summary:
            for key in totals:
                if key == "round_count":
                    continue
                totals[key] += int(summary.get(key) or 0)
            continue

        for rule in (run.get("rules") if isinstance(run, dict) else []) or []:
            if not isinstance(rule, dict):
                continue
            uploaded = int(rule.get("uploaded_count") or 0)
            submitted = int(rule.get("submitted_count") if rule.get("submitted_count") is not None else uploaded)
            verified = int(rule.get("verified_count") or 0)
            totals["selected_count"] += int(rule.get("selected_count") or 0)
            totals["uploaded_count"] += uploaded
            totals["submitted_count"] += submitted
            totals["verified_count"] += verified
            totals["pending_verify_count"] += int(rule.get("pending_verify_count") or max(0, submitted - verified))
            totals["failed_count"] += int(rule.get("failed_count") or 0)
            if rule.get("topic_id"):
                totals["topic_count"] += 1
                verification = rule.get("verification") if isinstance(rule.get("verification"), dict) else {}
                if verification.get("confirmed"):
                    totals["verified_topic_count"] += 1
                else:
                    totals["pending_verify_topic_count"] += 1
    return totals


def summarize_publish_runs_for_request(requested_by, limit=100):
    requested_by = str(requested_by or "").strip()
    if not requested_by:
        return {"summary": _summarize_publish_runs([]), "runs": [], "batches": []}

    runs = [
        deepcopy(run)
        for run in auto_publish_list_runs(limit=limit)
        if isinstance(run, dict) and str(run.get("requested_by") or "").strip() == requested_by
    ]
    runs.sort(key=lambda run: (run.get("started_at") or "", run.get("id") or ""))
    summary = _summarize_publish_runs(runs)
    batches = []
    topic_ids = []
    for run in runs:
        for rule in (run.get("rules") if isinstance(run, dict) else []) or []:
            if not isinstance(rule, dict):
                continue
            uploaded = int(rule.get("uploaded_count") or 0)
            submitted = int(rule.get("submitted_count") if rule.get("submitted_count") is not None else uploaded)
            verified = int(rule.get("verified_count") or 0)
            pending = int(rule.get("pending_verify_count") or max(0, submitted - verified))
            failed = int(rule.get("failed_count") or 0)
            topic_id = str(rule.get("topic_id") or "").strip()
            if topic_id:
                topic_ids.append(topic_id)
            if not any([submitted, verified, pending, failed, topic_id]):
                continue
            batches.append({
                "run_id": run.get("id"),
                "rule_id": rule.get("rule_id") or rule.get("id"),
                "file_type": rule.get("file_type"),
                "submitted_count": submitted,
                "verified_count": verified,
                "pending_verify_count": pending,
                "failed_count": failed,
                "topic_id": topic_id,
                "status": rule.get("status"),
            })

    return {
        "summary": summary,
        "runs": runs,
        "batches": batches,
        "batch_counts": [item["submitted_count"] for item in batches if item.get("submitted_count")],
        "topic_ids": topic_ids,
        "recovered_from_runtime": bool(runs),
    }


def _normalize_file_type(value):
    normalized = str(value or "").strip().lower()
    if normalized in {"mp3", "audio"}:
        return "audio"
    if normalized == "pdf":
        return "pdf"
    raise ValueError(f"不支持的发布文件类型：{value}")


def _normalize_approved_paths(paths, *, source_dir, file_type):
    """Validate an explicit publish allow-list without probing raw sources."""
    if paths is None:
        return None
    if not isinstance(paths, (list, tuple, set)):
        raise ValueError("approved_paths 必须是文件路径数组")

    root = Path(source_dir).expanduser().resolve()
    allowed_extensions = (
        {".pdf"}
        if file_type == "pdf"
        else {".mp3", ".m4a", ".wav", ".aac", ".flac", ".ogg"}
    )
    normalized = []
    seen = set()
    for raw_path in paths:
        value = str(raw_path or "").strip()
        if not value:
            continue
        try:
            candidate = Path(value).expanduser().resolve()
            candidate.relative_to(root)
        except (OSError, ValueError) as exc:
            raise ValueError(f"清洗发布白名单路径不在输出目录内：{value}") from exc
        if candidate.suffix.lower() not in allowed_extensions:
            raise ValueError(f"清洗发布白名单文件类型不匹配：{candidate}")
        key = str(candidate)
        if key not in seen:
            seen.add(key)
            normalized.append(key)
    return normalized


def _truthy(value):
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _bounded_int(value, default, *, lower, upper):
    try:
        number = int(str(value).strip())
    except Exception:
        number = default
    return min(max(number, lower), upper)
