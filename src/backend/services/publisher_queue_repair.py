import base64
import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, Iterable, Optional, Tuple

import redis
from sqlalchemy.orm.attributes import flag_modified

from celery_config import celery_app
from models import db
from models.task import PublicationTask
from tasks.publisher import run_publication_pipeline


logger = logging.getLogger(__name__)

DEFAULT_ORPHAN_GRACE_SECONDS = 180
DEFAULT_ORPHAN_REQUEUE_LIMIT = 3
DEFAULT_INSPECT_TIMEOUT_SECONDS = 1.5
DEFAULT_QUEUE_SCAN_LIMIT = 1000
REPAIR_RESULT_KEY = "queue_repair"


def maybe_requeue_stale_publisher_child(
    child: PublicationTask,
    *,
    queue: str = "render",
    log: Optional[logging.Logger] = None,
) -> Tuple[PublicationTask, bool]:
    """Requeue a stale publisher child if DB says queued but Celery cannot see it.

    This is intentionally conservative: it only touches old pending/queued
    publisher tasks, and it requires both worker inspect and queue scan to
    succeed before deciding the message is orphaned.
    """
    active_logger = log or logger
    if not _truthy_env("PUBLISHER_CHILD_QUEUE_REPAIR_ENABLED", True):
        return child, False
    if not child or getattr(child, "feature", None) != "publisher":
        return child, False
    if (child.status or "").lower() != "pending":
        return child, False
    if (child.current_step or "queued").lower() not in {"queued", "init", ""}:
        return child, False

    grace_seconds = _int_env("PUBLISHER_CHILD_ORPHAN_GRACE_SECONDS", DEFAULT_ORPHAN_GRACE_SECONDS, 30)
    reference_time = child.updated_at or child.created_at
    if _age_seconds(reference_time) < grace_seconds:
        return child, False

    result = dict(child.result or {})
    repair_state = dict(result.get(REPAIR_RESULT_KEY) or {})
    requeue_limit = _int_env("PUBLISHER_CHILD_ORPHAN_REQUEUE_LIMIT", DEFAULT_ORPHAN_REQUEUE_LIMIT, 1)
    requeue_count = int(repair_state.get("count") or 0)
    if requeue_count >= requeue_limit:
        active_logger.warning(
            "Publisher child queue repair skipped after limit: child=%s count=%s limit=%s",
            child.id,
            requeue_count,
            requeue_limit,
        )
        return child, False

    visible, checked = _celery_message_visible(child.celery_task_id, child_id=child.id, queue=queue)
    if not checked or visible:
        return child, False

    previous_celery_id = child.celery_task_id
    try:
        async_result = run_publication_pipeline.apply_async(args=[child.id], queue=queue, retry=False)
    except Exception as exc:
        active_logger.warning(
            "Publisher child queue repair publish failed: child=%s previous_celery_id=%s error=%s",
            child.id,
            previous_celery_id,
            exc,
        )
        return child, False
    now = datetime.utcnow().isoformat() + "Z"

    repair_state.update({
        "count": requeue_count + 1,
        "last_requeued_at": now,
        "previous_celery_id": previous_celery_id,
        "current_celery_id": async_result.id,
        "queue": queue,
        "reason": "pending_queued_not_visible_in_celery",
    })
    history = list(repair_state.get("history") or [])
    history.append({
        "at": now,
        "previous_celery_id": previous_celery_id,
        "new_celery_id": async_result.id,
    })
    repair_state["history"] = history[-10:]

    result[REPAIR_RESULT_KEY] = repair_state
    result.pop("error", None)
    result.pop("error_type", None)
    result.pop("completed_at", None)

    child.celery_task_id = async_result.id
    child.status = "pending"
    child.current_step = "queued"
    child.progress = 0
    child.error_message = None
    child.completed_at = None
    child.result = result
    flag_modified(child, "result")
    db.session.commit()

    active_logger.warning(
        "Requeued orphan publisher child: child=%s previous_celery_id=%s new_celery_id=%s",
        child.id,
        previous_celery_id,
        async_result.id,
    )
    return child, True


def _celery_message_visible(celery_id: Optional[str], *, child_id: str, queue: str) -> Tuple[bool, bool]:
    if not celery_id:
        return False, True

    worker_visible, worker_checked = _message_visible_in_workers(celery_id, child_id=child_id)
    queue_visible, queue_checked = _message_visible_in_queue(celery_id, child_id=child_id, queue=queue)
    checked = worker_checked and queue_checked
    return worker_visible or queue_visible, checked


def _message_visible_in_workers(celery_id: str, *, child_id: str) -> Tuple[bool, bool]:
    timeout = float(os.environ.get("PUBLISHER_CHILD_ORPHAN_INSPECT_TIMEOUT", DEFAULT_INSPECT_TIMEOUT_SECONDS))
    try:
        inspector = celery_app.control.inspect(timeout=timeout)
        snapshots = [
            inspector.active(),
            inspector.reserved(),
            inspector.scheduled(),
        ]
    except Exception as exc:
        logger.warning("Publisher child queue repair inspect failed: child=%s error=%s", child_id, exc)
        return False, False

    if any(snapshot is None for snapshot in snapshots):
        return False, False

    for snapshot in snapshots:
        if _task_id_in_worker_snapshot(snapshot or {}, celery_id):
            return True, True
    return False, True


def _message_visible_in_queue(celery_id: str, *, child_id: str, queue: str) -> Tuple[bool, bool]:
    try:
        client = redis.Redis.from_url(
            celery_app.conf.broker_url,
            socket_connect_timeout=2,
            socket_timeout=3,
            retry_on_timeout=False,
        )
        limit = _int_env("PUBLISHER_CHILD_ORPHAN_QUEUE_SCAN_LIMIT", DEFAULT_QUEUE_SCAN_LIMIT, 20)
        for key in _redis_queue_keys(queue):
            for raw in client.lrange(key, 0, limit - 1):
                if _raw_message_matches(raw, celery_id=celery_id, child_id=child_id):
                    return True, True
        for raw in _iter_unacked_messages(client, limit):
            if _raw_message_matches(raw, celery_id=celery_id, child_id=child_id) or _raw_contains(raw, celery_id, child_id):
                return True, True
    except Exception as exc:
        logger.warning("Publisher child queue repair queue scan failed: child=%s error=%s", child_id, exc)
        return False, False
    return False, True


def _task_id_in_worker_snapshot(snapshot: Dict[str, Any], celery_id: str) -> bool:
    for tasks in snapshot.values():
        if not isinstance(tasks, list):
            continue
        for task in tasks:
            if isinstance(task, dict) and str(task.get("id") or "") == celery_id:
                return True
    return False


def _redis_queue_keys(queue: str) -> Iterable[bytes]:
    encoded = str(queue).encode("utf-8")
    yield encoded
    separator = b"\x06\x16"
    for priority in (b"0", b"3", b"6", b"9"):
        yield encoded + separator + priority


def _raw_message_matches(raw: Any, *, celery_id: str, child_id: str) -> bool:
    try:
        text = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
        envelope = json.loads(text)
    except Exception:
        return False

    headers = envelope.get("headers") if isinstance(envelope, dict) else None
    if isinstance(headers, dict) and str(headers.get("id") or "") == celery_id:
        return True

    body = envelope.get("body") if isinstance(envelope, dict) else None
    if not isinstance(body, str):
        return False
    try:
        decoded = base64.b64decode(body)
        payload = json.loads(decoded.decode("utf-8"))
    except Exception:
        return False
    return child_id in _flatten_strings(payload)


def _iter_unacked_messages(client: redis.Redis, limit: int) -> Iterable[Any]:
    scanned = 0
    for _key, value in client.hscan_iter("unacked", count=min(max(limit, 20), 200)):
        yield value
        scanned += 1
        if scanned >= limit:
            return


def _raw_contains(raw: Any, *needles: str) -> bool:
    if isinstance(raw, bytes):
        return any(needle.encode("utf-8") in raw for needle in needles if needle)
    text = str(raw)
    return any(needle in text for needle in needles if needle)


def _flatten_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _flatten_strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _flatten_strings(item)


def _age_seconds(value: Optional[datetime]) -> float:
    if not value:
        return 0
    return max(0.0, (datetime.utcnow() - value.replace(tzinfo=None)).total_seconds())


def _truthy_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _int_env(name: str, default: int, minimum: int) -> int:
    try:
        value = int(os.environ.get(name, default))
    except (TypeError, ValueError):
        value = default
    return max(value, minimum)
