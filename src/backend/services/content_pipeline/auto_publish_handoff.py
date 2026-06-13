import logging
from copy import deepcopy
from pathlib import Path

from models import db
from models.task import PublicationTask
from services.zsxq_auto_publish_service import (
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

    task = PublicationTask(
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
                "options": options,
            },
        },
        result={
            "enabled": True,
            "queued": False,
            "file_type": normalized_type,
            "output_dir": str(source_dir),
            "source_task_id": source_task_id,
            "trigger": trigger,
            "metadata": metadata,
        },
    )
    db.session.add(task)
    db.session.commit()

    try:
        from tasks.zsxq_publish import run_publish_clean_output_task

        celery_task = run_publish_clean_output_task.apply_async(
            args=[task.id],
            queue="zsxq_publish",
            retry=False,
        )
    except Exception as exc:
        logger.exception("知识星球发布任务入队失败: %s", task.id)
        task.status = "failed"
        task.current_step = "failed"
        task.error_message = f"知识星球发布任务入队失败: {exc}"
        task.result = {
            **(task.result or {}),
            "queued": False,
            "status": "failed",
            "reason": "queue_failed",
            "message": task.error_message,
        }
        db.session.commit()
        raise RuntimeError(task.error_message) from exc

    task.celery_task_id = celery_task.id
    task.result = {
        **(task.result or {}),
        "queued": True,
        "task_id": task.id,
        "celery_task_id": celery_task.id,
        "message": "知识星球发布任务已提交。",
    }
    db.session.commit()
    return task.to_dict()


def publish_clean_output(*, file_type, output_dir, options=None, requested_by="content_pipeline"):
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

    config = _build_transient_publish_config(
        file_type=normalized_type,
        source_dir=source_dir,
        options=options,
    )
    max_rounds = _bounded_int(options.get("max_rounds"), DEFAULT_MAX_ROUNDS, lower=1, upper=100)
    runs = []

    for _round in range(1, max_rounds + 1):
        preview = auto_publish_preview_payload(config)
        rule_preview = _preview_for_type(preview, normalized_type)
        eligible_count = int((rule_preview or {}).get("eligible_count") or 0)
        if eligible_count <= 0:
            return {
                "enabled": True,
                "queued": bool(runs),
                "status": "completed" if runs else "skipped",
                "reason": "no_eligible_files" if not runs else "drained",
                "message": "清洗输出目录已无待发布文件。" if runs else "清洗输出目录没有可发布文件。",
                "source_dir": str(source_dir),
                "rounds": runs,
                "preview": rule_preview,
            }

        result = run_once_with_config(
            config,
            trigger=str(options.get("trigger") or "content_pipeline_clean_complete"),
            requested_by=requested_by,
        )
        result["round"] = _round
        runs.append(result)

        if result.get("skipped") or result.get("status") in {"failed"}:
            return {
                "enabled": True,
                "queued": bool(runs),
                "status": result.get("status") or "failed",
                "reason": result.get("reason") or "run_failed",
                "message": result.get("message") or "清洗后自动发布失败。",
                "source_dir": str(source_dir),
                "rounds": runs,
                "preview": rule_preview,
            }

    return {
        "enabled": True,
        "queued": bool(runs),
        "status": "partial",
        "reason": "max_rounds_reached",
        "message": f"清洗后自动发布达到最大轮次 {max_rounds}，可能仍有未发布文件。",
        "source_dir": str(source_dir),
        "rounds": runs,
    }


def _build_transient_publish_config(*, file_type, source_dir, options):
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

    rule_options = options.get("rule") if isinstance(options.get("rule"), dict) else {}
    for key in (
        "batch_size",
        "body_prefix",
        "include_filenames",
        "hashtag_id",
        "hashtag_title",
        "upload_retry_count",
        "max_run_attempts",
        "max_file_size_mb",
    ):
        if key in rule_options:
            target_rule[key] = rule_options[key]

    if options.get("group_id"):
        config["group_id"] = str(options.get("group_id")).strip()
    if options.get("group_name"):
        config["group_name"] = str(options.get("group_name")).strip()

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


def _normalize_file_type(value):
    normalized = str(value or "").strip().lower()
    if normalized in {"mp3", "audio"}:
        return "audio"
    if normalized == "pdf":
        return "pdf"
    raise ValueError(f"不支持的发布文件类型：{value}")


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
