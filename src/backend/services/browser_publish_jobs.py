"""Shared PublicationTask lifecycle for Cookie-backed browser publishers."""

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Dict, FrozenSet, Optional

from models import db
from models.task import PublicationTask
from services.platform_publish_dispatch import PLATFORM_UPLOAD_QUEUE, dispatch_platform_publish
from services.runtime_guard import revoke_celery_task_after_db_commit


QUEUE_NAME = PLATFORM_UPLOAD_QUEUE
STATUS_MAP = {
    "pending": "queued", "processing": "running", "completed": "succeeded",
    "failed": "failed", "canceled": "canceled",
}
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BrowserPublishJobSpec:
    platform: str
    feature_name: str
    platform_label: str
    resolve_credential: Callable[..., Dict[str, Any]]
    allowed_visibility: FrozenSet[str]
    allowed_extensions: FrozenSet[str] = frozenset({".mp4", ".mov", ".m4v"})
    title_min_chars: int = 1
    default_visibility: str = "private"
    default_extra: Optional[Dict[str, Any]] = None
    prevent_active_credential_overlap: bool = False


def normalize_tags(value, default=None):
    items = value.replace("，", ",").split(",") if isinstance(value, str) else value
    if not isinstance(items, (list, tuple, set)):
        items = default or []
    result, seen = [], set()
    for item in items:
        tag = "".join(str(item or "").strip().lstrip("#").split())[:20]
        if tag and tag not in seen:
            seen.add(tag)
            result.append(tag)
        if len(result) >= 10:
            break
    return result


def clean_text(value, max_length):
    return " ".join(str(value or "").strip().split())[:max_length]


def create_browser_publish_task(
    spec: BrowserPublishJobSpec,
    *, current_user, video_path, title, credential_id, description="", tags=None,
    visibility=None, submit=False, original_filename="", parent_task=None,
    parent_feature=None, source_label="browser_publish:manual_upload",
    video_key="landscape", extra=None,
):
    path = Path(str(video_path or "")).expanduser().resolve()
    if not path.is_file() or path.stat().st_size <= 0:
        raise ValueError("当前任务还没有可上传的视频文件")
    if path.suffix.lower() not in spec.allowed_extensions:
        raise ValueError(f"{spec.platform_label}上传仅支持 MP4、MOV、M4V 视频")
    title = clean_text(title, 100)
    if len("".join(title.split())) < spec.title_min_chars:
        suffix = f"至少需要 {spec.title_min_chars} 个字符" if spec.title_min_chars > 1 else "不能为空"
        raise ValueError(f"{spec.platform_label}标题{suffix}")
    credential_id = clean_text(credential_id, 80)
    if not credential_id:
        raise ValueError(f"请选择{spec.platform_label}账号凭据")
    visibility = str(visibility or spec.default_visibility).strip().lower()
    if visibility not in spec.allowed_visibility:
        raise ValueError(f"不支持的{spec.platform_label}可见范围")

    auth = spec.resolve_credential(account_id=credential_id, required=True)
    if spec.prevent_active_credential_overlap:
        active_rows = (
            PublicationTask.query
            .filter(PublicationTask.feature == spec.feature_name)
            .filter(PublicationTask.status.in_(["pending", "processing"]))
            .order_by(PublicationTask.created_at.desc())
            .limit(100)
            .all()
        )
        for active in active_rows:
            active_command = dict(active.params or {}).get(spec.feature_name) or {}
            if str(active_command.get("credential_id") or "") == credential_id:
                raise ValueError(
                    f"该{spec.platform_label}账号已有上传任务正在排队或执行，请等待完成后再提交"
                )
    parent_id = str(getattr(parent_task, "id", "") or "") or None
    parent_feature = str(parent_feature or getattr(parent_task, "feature", "") or "").strip() or None
    user_id = str(getattr(current_user, "id", None) or getattr(parent_task, "user_id", None) or "system")
    command = {
        "credential_id": credential_id, "title": title,
        "description": str(description or "").strip()[:3000],
        "tags": normalize_tags(tags), "submit": bool(submit),
        "visibility": visibility, "video_path": str(path),
        "video_key": clean_text(video_key, 32) or "landscape",
        "original_filename": clean_text(original_filename, 255) or path.name,
        "source": source_label,
        **dict(spec.default_extra or {}), **dict(extra or {}),
    }
    broker_task_id = str(uuid.uuid4())
    params = {
            "feature": spec.feature_name, "user_id": user_id,
            "parent_feature": parent_feature, "parent_task_id": parent_id,
            "source_feature": parent_feature, "source_task_id": parent_id,
            spec.feature_name: command,
        }
    result = {
            "publish_status": "queued", "submit_requested": bool(submit),
            "visibility": visibility, "credential_id": auth.get("credential_id"),
            "credential_name": auth.get("credential_name"), "source": source_label,
            "video_key": command["video_key"],
            **{key: command.get(key) for key in (spec.default_extra or {})},
        }
    task = PublicationTask(
        celery_task_id=broker_task_id,
        status="pending", current_step="queued", progress=0,
        params=params,
        result=result,
    )
    db.session.add(task)
    db.session.flush()
    snapshot = SimpleNamespace(
        id=str(task.id),
        celery_task_id=broker_task_id,
        status="pending",
        current_step="queued",
        progress=0,
        params=dict(params),
        result=dict(result),
        error_message=None,
        created_at=task.created_at,
        updated_at=task.updated_at,
        completed_at=None,
    )
    db.session.commit()
    return snapshot


def enqueue_browser_publish_task(spec: BrowserPublishJobSpec, task):
    task_id = str(task.id)
    broker_task_id = str(getattr(task, "celery_task_id", None) or "").strip()
    if not broker_task_id:
        broker_task_id = str(uuid.uuid4())
        updated = PublicationTask.query.filter(
            PublicationTask.id == task_id,
        ).update(
            {PublicationTask.celery_task_id: broker_task_id},
            synchronize_session=False,
        )
        if not updated:
            db.session.rollback()
            raise RuntimeError(f"{spec.platform_label}上传任务不存在: {task_id}")
        db.session.commit()
        task.celery_task_id = broker_task_id
    try:
        submission = dispatch_platform_publish(
            spec.platform,
            task_id,
            broker_task_id=broker_task_id,
            retry=False,
        )
    except Exception as exc:
        db.session.rollback()
        PublicationTask.query.filter(PublicationTask.id == task_id).update({
            PublicationTask.status: "failed",
            PublicationTask.current_step: "failed",
            PublicationTask.error_message: f"{spec.platform_label}上传任务入队失败: {exc}",
            PublicationTask.completed_at: datetime.utcnow(),
            PublicationTask.updated_at: datetime.utcnow(),
        }, synchronize_session=False)
        db.session.commit()
        raise RuntimeError(f"{spec.platform_label}上传任务入队失败: {exc}") from exc
    actual_celery_id = str(submission.get("celery_id") or broker_task_id)
    if actual_celery_id != broker_task_id:
        PublicationTask.query.filter(PublicationTask.id == task_id).update(
            {
                PublicationTask.celery_task_id: actual_celery_id,
                PublicationTask.updated_at: datetime.utcnow(),
            },
            synchronize_session=False,
        )
        db.session.commit()
    task.celery_task_id = actual_celery_id
    return task


def browser_publish_payload(spec: BrowserPublishJobSpec, task):
    params = dict(task.params or {}).get(spec.feature_name) or {}
    result = dict(task.result or {})
    payload = {
        "task_id": str(task.id), "job_id": str(task.id), "celery_id": task.celery_task_id,
        "status": STATUS_MAP.get(task.status, task.status or "queued"),
        "step": task.current_step, "progress": int(task.progress or 0),
        "title": params.get("title"), "original_filename": params.get("original_filename"),
        "video_key": params.get("video_key"), "submit_requested": bool(params.get("submit")),
        "visibility": params.get("visibility") or spec.default_visibility,
        "publish_status": result.get("publish_status"), "message": result.get("message"),
        "credential_id": params.get("credential_id"), "credential_name": result.get("credential_name"),
        "source": params.get("source") or result.get("source"), "error": task.error_message,
        "created_at": task.created_at.isoformat() + "Z" if task.created_at else None,
        "updated_at": task.updated_at.isoformat() + "Z" if task.updated_at else None,
        "completed_at": task.completed_at.isoformat() + "Z" if task.completed_at else None,
    }
    for key in (spec.default_extra or {}):
        payload[key] = params.get(key, result.get(key))
    return payload


def browser_publish_children(spec: BrowserPublishJobSpec, parent_id, *, parent_feature=None, limit=20):
    params = PublicationTask.params[spec.feature_name]
    columns = [
        PublicationTask.id.label("id"),
        PublicationTask.celery_task_id.label("celery_task_id"),
        PublicationTask.status.label("status"),
        PublicationTask.current_step.label("current_step"),
        PublicationTask.progress.label("progress"),
        PublicationTask.error_message.label("error_message"),
        PublicationTask.created_at.label("created_at"),
        PublicationTask.updated_at.label("updated_at"),
        PublicationTask.completed_at.label("completed_at"),
        params["title"].as_string().label("title"),
        params["original_filename"].as_string().label("original_filename"),
        params["video_key"].as_string().label("video_key"),
        params["submit"].as_boolean().label("submit_requested"),
        params["visibility"].as_string().label("visibility"),
        params["credential_id"].as_string().label("credential_id"),
        params["source"].as_string().label("param_source"),
        PublicationTask.result["publish_status"].as_string().label("publish_status"),
        PublicationTask.result["message"].as_string().label("message"),
        PublicationTask.result["credential_name"].as_string().label("credential_name"),
        PublicationTask.result["source"].as_string().label("result_source"),
    ]
    extra_keys = tuple((spec.default_extra or {}).keys())
    for index, key in enumerate(extra_keys):
        columns.extend((
            params[key].label(f"param_extra_{index}"),
            PublicationTask.result[key].label(f"result_extra_{index}"),
        ))

    query = db.session.query(*columns).filter(
        PublicationTask.feature == spec.feature_name,
        PublicationTask.parent_task_id == str(parent_id),
    )
    if parent_feature:
        query = query.filter(PublicationTask.parent_feature == str(parent_feature))
    rows = query.order_by(PublicationTask.created_at.desc()).limit(max(1, min(int(limit), 100))).all()
    output = []
    for row in rows:
        payload = {
            "task_id": str(row.id),
            "job_id": str(row.id),
            "celery_id": row.celery_task_id,
            "status": STATUS_MAP.get(row.status, row.status or "queued"),
            "step": row.current_step,
            "progress": int(row.progress or 0),
            "title": row.title,
            "original_filename": row.original_filename,
            "video_key": row.video_key,
            "submit_requested": bool(row.submit_requested),
            "visibility": row.visibility or spec.default_visibility,
            "publish_status": row.publish_status,
            "message": row.message,
            "credential_id": row.credential_id,
            "credential_name": row.credential_name,
            "source": row.param_source or row.result_source,
            "error": row.error_message,
            "created_at": row.created_at.isoformat() + "Z" if row.created_at else None,
            "updated_at": row.updated_at.isoformat() + "Z" if row.updated_at else None,
            "completed_at": row.completed_at.isoformat() + "Z" if row.completed_at else None,
        }
        for index, key in enumerate(extra_keys):
            payload[key] = getattr(row, f"param_extra_{index}") or getattr(row, f"result_extra_{index}")
        output.append(payload)
    return output


def cancel_browser_publish_task(spec: BrowserPublishJobSpec, task):
    if task.status != "pending":
        return False, f"仅排队中的{spec.platform_label}任务可以取消"
    task_id = str(task.id)
    celery_task_id = str(task.celery_task_id or "").strip()
    now = datetime.utcnow()
    snapshot = SimpleNamespace(
        id=task_id,
        celery_task_id=celery_task_id or None,
        status="canceled",
        current_step="canceled",
        progress=int(task.progress or 0),
        params=dict(task.params or {}),
        result=dict(task.result or {}),
        error_message=None,
        created_at=task.created_at,
        updated_at=now,
        completed_at=now,
    )
    response_payload = browser_publish_payload(spec, snapshot)
    updated = PublicationTask.query.filter(
        PublicationTask.id == task_id,
        PublicationTask.status == "pending",
    ).update({
        PublicationTask.status: "canceled",
        PublicationTask.current_step: "canceled",
        PublicationTask.completed_at: now,
        PublicationTask.updated_at: now,
    }, synchronize_session=False)
    if not updated:
        db.session.rollback()
        return False, f"{spec.platform_label}任务状态已变化，请刷新后重试"
    db.session.commit()
    task._browser_publish_cancel_response_payload = response_payload
    if celery_task_id:
        revoked = revoke_celery_task_after_db_commit(
            celery_task_id,
            operation=(
                f"{spec.platform} browser upload cancel revoke task={task_id}"
            ),
            business_task_id=task_id,
            log=logger,
        )
        if not revoked:
            return True, (
                f"{spec.platform_label}上传任务已取消；"
                "broker 撤回失败，终态门禁将拦截旧消息"
            )
    return True, f"{spec.platform_label}上传任务已取消"
