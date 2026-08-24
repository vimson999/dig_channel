import json
import logging
import os
import time
from datetime import datetime

from celery.exceptions import Retry
from flask import has_app_context
from sqlalchemy import func, text
from sqlalchemy.exc import DBAPIError, OperationalError, PendingRollbackError
from sqlalchemy.orm import load_only
from sqlalchemy.orm.attributes import flag_modified

from models import db
from models.task import PublicationTask


logger = logging.getLogger(__name__)

ACTIVE_STATUSES = ("pending", "processing")
RUNNING_STATUSES = ("processing",)

DEFAULT_FEATURE_LIMITS = {
    "publisher": 4,
    "publisher_upload": 2,
    "douyin_upload_test": 1,
    "wechat_channels_upload_test": 1,
    "report_audio_workflow": 2,
    "report_pdf_audio_workflow": 2,
    "report_script_material_workflow": 2,
    "report_visual_plan": 3,
    "report_top10_workflow": 1,
    "mp3_video_workflow": 1,
    "content_pdf_clean": 2,
    "content_audio_clean": 1,
    "zsxq_clean_output_publish": 1,
}

DEFAULT_FEATURE_BACKLOG_LIMITS = {
    "publisher": 12,
    "publisher_upload": 20,
    "douyin_upload_test": 5,
    "wechat_channels_upload_test": 5,
    "report_audio_workflow": 10,
    "report_pdf_audio_workflow": 10,
    "report_script_material_workflow": 50,
    "report_visual_plan": 20,
    "report_top10_workflow": 5,
    "mp3_video_workflow": 5,
    "content_pdf_clean": 10,
    "content_audio_clean": 5,
    "zsxq_clean_output_publish": 5,
}

FEATURE_ALIASES = {
    "report_audio_workflow": ("report_audio_workflow", "report_pdf_audio_workflow"),
    "report_pdf_audio_workflow": ("report_audio_workflow", "report_pdf_audio_workflow"),
}
PASSIVE_RUNNING_STEPS = {
    "mp3_video_workflow": (
        "landscape_video_waiting",
        "vertical_video_waiting",
    ),
}
PASSIVE_RUNNING_STEP_PREFIXES = {
    # Report-audio parents return after scheduling a delayed retry while their
    # Gemini, browser-TTS, visual, or publisher child is still running.  These
    # waiting rows must not occupy the limited parent-worker start capacity,
    # otherwise two slow children can stall every queued report indefinitely.
    "report_audio_workflow": (
        "gemini_",
        "gem_",
        "doubao_browser_",
        "doubao_",
        "scheme3_visual_",
        "publisher_video_",
        "pub_",
    ),
    "report_pdf_audio_workflow": (
        "gemini_",
        "gem_",
        "doubao_browser_",
        "doubao_",
        "scheme3_visual_",
        "publisher_video_",
        "pub_",
    ),
    # While a publisher child renders, the parent mirrors child steps as
    # landscape_video_queued, landscape_video_synthesis, and so on. Every one
    # of those states is passive and must not occupy workflow start capacity.
    "mp3_video_workflow": (
        "landscape_video_",
        "vertical_video_",
    ),
}

_DB_PRESSURE_CACHE = {
    "expires_at": 0.0,
    "snapshot": None,
}
_IDLE_OFFENDER_LOG_CACHE = {
    "expires_at": 0.0,
    "signature": None,
}

DEFERRED_TASK_COLUMNS = (
    PublicationTask.id,
    PublicationTask.status,
    PublicationTask.current_step,
    PublicationTask.result,
    PublicationTask.error_message,
    PublicationTask.completed_at,
)


class TaskAdmissionDenied(RuntimeError):
    def __init__(self, admission):
        self.admission = admission
        super().__init__(admission.get("message") or admission.get("reason") or "task admission denied")


class DatabaseSessionBoundaryError(RuntimeError):
    """Raised when external I/O would cross an uncommitted DB unit of work."""


def task_guard_enabled():
    return _truthy_env("TASK_ADMISSION_GUARD_ENABLED", True)


def check_publication_task_admission(feature, *, task_id=None, mode="start"):
    if not task_guard_enabled():
        return _allowed("disabled", feature=feature, task_id=task_id)

    pressure = db_pressure_snapshot()
    if not pressure.get("allowed", True):
        return _denied(
            pressure.get("reason") or "db_pressure",
            _db_pressure_message(pressure),
            feature=feature,
            task_id=task_id,
            pressure=pressure,
        )

    limit = _feature_backlog_limit(feature) if mode == "submit" else _feature_limit(feature)
    if limit is None:
        return _allowed("no_limit", feature=feature, task_id=task_id, pressure=pressure)

    active_count = _publication_task_active_count(
        feature,
        task_id=task_id,
        mode=mode,
    )
    if active_count >= limit:
        is_backlog = mode == "submit"
        reason = "feature_backlog" if is_backlog else "feature_capacity"
        limit_label = "排队/运行积压上限" if is_backlog else "当前运行上限"
        return _denied(
            reason,
            f"系统保护：{feature} 同类任务达到{limit_label} {limit}，稍后自动重试。",
            feature=feature,
            task_id=task_id,
            pressure=pressure,
            active_count=active_count,
            limit=limit,
            mode=mode,
        )

    return _allowed(
        "ok",
        feature=feature,
        task_id=task_id,
        pressure=pressure,
        active_count=active_count,
        limit=limit,
        mode=mode,
    )


def guard_publication_task_start(celery_task, task_id, *, feature, queue):
    admission = check_publication_task_admission(feature, task_id=task_id, mode="start")
    if admission.get("allowed"):
        end_db_transaction_before_external_io("celery task execution")
        return admission

    countdown = _retry_delay_seconds(admission)
    _mark_publication_task_deferred(
        task_id,
        admission=admission,
        queue=queue,
        countdown=countdown,
    )
    logger.warning(
        "[TASK_GUARD] defer task start | task=%s feature=%s queue=%s countdown=%ss reason=%s",
        task_id,
        feature,
        queue,
        countdown,
        admission.get("reason"),
    )
    end_db_transaction_before_external_io("celery retry publish")
    raise celery_task.retry(
        exc=TaskAdmissionDenied(admission),
        countdown=countdown,
        max_retries=_admission_retry_max_retries(),
    )


def maybe_guarded_apply_async(
    celery_callable,
    *,
    args,
    queue,
    feature,
    task=None,
    task_id=None,
    broker_task_id=None,
    retry=False,
):
    assert_db_session_clean("celery task publish")
    business_task_id = task_id or getattr(task, "id", None)
    admission = check_publication_task_admission(feature, task_id=business_task_id, mode="submit")
    countdown = 0 if admission.get("allowed") else _retry_delay_seconds(admission)
    deferred_state = None
    if not admission.get("allowed") and business_task_id:
        deferred_state = _mark_publication_task_deferred(
            business_task_id,
            admission=admission,
            queue=queue,
            countdown=countdown,
        )
    apply_options = {
        "args": list(args or []),
        "queue": queue,
        "countdown": countdown,
        "retry": retry,
    }
    if broker_task_id:
        apply_options["task_id"] = str(broker_task_id)
    async_result = apply_async_after_db_release(celery_callable, **apply_options)
    return {
        "async_result": async_result,
        "admission": admission,
        "deferred": not admission.get("allowed"),
        "deferred_state": deferred_state,
        "countdown": countdown,
    }


def db_pressure_snapshot(force_refresh=False):
    if not force_refresh:
        cached = _DB_PRESSURE_CACHE.get("snapshot")
        if cached and _DB_PRESSURE_CACHE.get("expires_at", 0.0) > time.time():
            return dict(cached)

    if not _is_postgres():
        snapshot = {
            "allowed": True,
            "reason": "not_postgres",
            "postgres": False,
        }
        _cache_pressure(snapshot)
        return snapshot

    try:
        with db.engine.connect() as conn:
            row = conn.execute(text("""
                SELECT
                    current_setting('max_connections')::int AS max_connections,
                    current_setting('superuser_reserved_connections')::int AS reserved_connections,
                    count(*) FILTER (WHERE pid <> pg_backend_pid())::int AS total_connections,
                    count(*) FILTER (
                        WHERE pid <> pg_backend_pid() AND usename = current_user
                    )::int AS current_user_connections,
                    count(*) FILTER (
                        WHERE pid <> pg_backend_pid() AND state = 'idle in transaction'
                    )::int AS idle_in_transaction_connections,
                    COALESCE((
                        SELECT jsonb_agg(to_jsonb(offender))
                        FROM (
                            SELECT
                                pid,
                                COALESCE(application_name, '') AS application_name,
                                COALESCE(client_addr::text, '') AS client_addr,
                                COALESCE(backend_type, '') AS backend_type,
                                ROUND(EXTRACT(EPOCH FROM (NOW() - xact_start))::numeric, 3)
                                    AS xact_age_seconds,
                                ROUND(EXTRACT(EPOCH FROM (NOW() - state_change))::numeric, 3)
                                    AS idle_age_seconds,
                                ROUND(EXTRACT(EPOCH FROM (NOW() - query_start))::numeric, 3)
                                    AS query_age_seconds,
                                COALESCE(wait_event_type, '') AS wait_event_type,
                                COALESCE(wait_event, '') AS wait_event,
                                md5(regexp_replace(COALESCE(query, ''), E'\\s+', ' ', 'g'))
                                    AS query_fingerprint
                            FROM pg_stat_activity
                            WHERE pid <> pg_backend_pid()
                              AND state = 'idle in transaction'
                            ORDER BY xact_start NULLS LAST
                            LIMIT 5
                        ) AS offender
                    ), '[]'::jsonb) AS idle_in_transaction_offenders
                FROM pg_stat_activity
            """)).mappings().first()
    except Exception as exc:
        snapshot = {
            "allowed": not _truthy_env("TASK_ADMISSION_DB_FAIL_CLOSED", True),
            "reason": "db_probe_failed",
            "postgres": True,
            "error": str(exc),
        }
        _cache_pressure(snapshot)
        return snapshot

    max_connections = int(row.get("max_connections") or 0)
    reserved = int(row.get("reserved_connections") or 0)
    total = int(row.get("total_connections") or 0)
    available = max(1, max_connections - reserved)
    free = max(0, available - total)
    used_ratio = total / available if available else 1.0
    idle_in_transaction = int(row.get("idle_in_transaction_connections") or 0)

    min_free = _int_env("TASK_ADMISSION_DB_MIN_FREE_CONNECTIONS", 10, lower=1, upper=1000)
    max_used_ratio = _float_env("TASK_ADMISSION_DB_MAX_USED_RATIO", 0.85, lower=0.1, upper=1.0)
    max_idle_tx = _int_env("TASK_ADMISSION_DB_MAX_IDLE_IN_TX", 5, lower=0, upper=1000)
    idle_offenders = _normalize_idle_offenders(
        row.get("idle_in_transaction_offenders")
    )
    if idle_in_transaction:
        _log_idle_transaction_offenders(idle_offenders)

    allowed = True
    reason = "ok"
    if free < min_free:
        allowed = False
        reason = "db_low_free_connections"
    elif used_ratio >= max_used_ratio:
        allowed = False
        reason = "db_high_used_ratio"
    elif idle_in_transaction > max_idle_tx:
        allowed = False
        reason = "db_idle_in_transaction_pressure"

    snapshot = {
        "allowed": allowed,
        "reason": reason,
        "postgres": True,
        "max_connections": max_connections,
        "reserved_connections": reserved,
        "available_connections": available,
        "total_connections": total,
        "current_user_connections": int(row.get("current_user_connections") or 0),
        "idle_in_transaction_connections": idle_in_transaction,
        "free_connections": free,
        "used_ratio": round(used_ratio, 4),
        "min_free_connections": min_free,
        "max_used_ratio": max_used_ratio,
        "max_idle_in_transaction": max_idle_tx,
    }
    _cache_pressure(snapshot)
    return snapshot


def _normalize_idle_offenders(value):
    if not value:
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return []
    if not isinstance(value, list):
        return []
    allowed_keys = (
        "pid",
        "application_name",
        "client_addr",
        "backend_type",
        "xact_age_seconds",
        "idle_age_seconds",
        "query_age_seconds",
        "wait_event_type",
        "wait_event",
        "query_fingerprint",
    )
    return [
        {key: item.get(key) for key in allowed_keys}
        for item in value[:5]
        if isinstance(item, dict)
    ]


def _log_idle_transaction_offenders(offenders):
    if not offenders:
        return
    signature = json.dumps(offenders, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    now = time.time()
    if (
        _IDLE_OFFENDER_LOG_CACHE.get("signature") == signature
        and float(_IDLE_OFFENDER_LOG_CACHE.get("expires_at") or 0.0) > now
    ):
        return
    _IDLE_OFFENDER_LOG_CACHE.update({
        "signature": signature,
        "expires_at": now + 60.0,
    })
    logger.warning(
        "[TASK_GUARD] idle-in-transaction offenders count=%s details=%s",
        len(offenders),
        signature,
    )


def release_db_session_quietly():
    try:
        db.session.rollback()
    except Exception:
        pass
    try:
        db.session.remove()
    except Exception:
        pass


def assert_db_session_clean(operation="external I/O"):
    """Fail before broker/network I/O if the caller forgot to persist DB changes."""

    if not has_app_context():
        return
    session = db.session()
    pending = {
        "new": len(session.new),
        "dirty": len(session.dirty),
        "deleted": len(session.deleted),
    }
    if any(pending.values()):
        raise DatabaseSessionBoundaryError(
            f"{operation} blocked: uncommitted database changes "
            f"(new={pending['new']}, dirty={pending['dirty']}, deleted={pending['deleted']})"
        )


def end_db_transaction_before_external_io(operation="external I/O", *, remove=False):
    """Return the checked-out connection before a broker/network wait.

    The clean read-only transaction is committed with expiration disabled.
    This returns the connection without expiring loaded projections or causing
    a full-row refresh after the external operation. ``remove=True`` is for
    DTO/snapshot paths that intentionally detach every ORM object.
    """

    if not has_app_context():
        return
    assert_db_session_clean(operation)
    session = db.session()
    previous_expire_on_commit = session.expire_on_commit
    session.expire_on_commit = False
    try:
        session.commit()
    except Exception:
        release_db_session_quietly()
        raise
    finally:
        session.expire_on_commit = previous_expire_on_commit
    if remove:
        db.session.remove()


def apply_async_after_db_release(celery_callable, **apply_options):
    end_db_transaction_before_external_io("celery task publish")
    return celery_callable.apply_async(**apply_options)


def retry_exception_types():
    return (Retry,)


def is_retryable_db_error(exc):
    if isinstance(exc, (OperationalError, PendingRollbackError)):
        return True
    if isinstance(exc, DBAPIError) and getattr(exc, "connection_invalidated", False):
        return True

    text_value = str(exc).lower()
    retryable_fragments = (
        "queuepool limit",
        "remaining connection slots",
        "too many connections",
        "timeout expired",
        "connection to server",
        "server closed the connection unexpectedly",
        "could not connect to server",
        "connection refused",
        "terminating connection",
        "connection not open",
        "can't reconnect until invalid transaction is rolled back",
        "read-only transaction",
        "read only transaction",
    )
    return any(fragment in text_value for fragment in retryable_fragments)


def _publication_task_active_count(feature, *, task_id=None, mode="start"):
    for attempt in range(2):
        aliases = FEATURE_ALIASES.get(feature, (feature,))
        statuses = RUNNING_STATUSES if mode == "start" else ACTIVE_STATUSES
        query = (
            db.session.query(func.count(PublicationTask.id))
            .filter(PublicationTask.feature.in_(aliases))
            .filter(PublicationTask.status.in_(statuses))
        )
        if mode == "start":
            passive_steps = _passive_running_steps(feature)
            if passive_steps:
                query = query.filter(~PublicationTask.current_step.in_(passive_steps))
            for prefix in _passive_running_step_prefixes(feature):
                query = query.filter(~PublicationTask.current_step.like(f"{prefix}%"))
        if task_id:
            query = query.filter(PublicationTask.id != str(task_id))
        try:
            return int(query.scalar() or 0)
        except Exception as exc:
            if attempt or not is_retryable_db_error(exc):
                raise
            logger.warning(
                "[TASK_GUARD] active-count connection invalidated; retrying once | feature=%s",
                feature,
            )
            release_db_session_quietly()
    return 0


def _passive_running_steps(feature):
    values = []
    for item in FEATURE_ALIASES.get(feature, (feature,)):
        values.extend(PASSIVE_RUNNING_STEPS.get(item, ()))
    return tuple(dict.fromkeys(values))


def _passive_running_step_prefixes(feature):
    values = []
    for item in FEATURE_ALIASES.get(feature, (feature,)):
        values.extend(PASSIVE_RUNNING_STEP_PREFIXES.get(item, ()))
    return tuple(dict.fromkeys(values))


def _mark_publication_task_deferred(task_id, *, admission, queue, countdown):
    if not task_id:
        return False
    try:
        now = datetime.utcnow().isoformat() + "Z"
        item = {
            "reason": admission.get("reason"),
            "message": admission.get("message"),
            "queue": queue,
            "countdown": countdown,
            "created_at": now,
            "pressure": admission.get("pressure"),
            "active_count": admission.get("active_count"),
            "limit": admission.get("limit"),
        }
        current_step = (
            "deferred_db_pressure"
            if str(admission.get("reason") or "").startswith("db_")
            else ("deferred_backlog" if admission.get("reason") == "feature_backlog" else "deferred_capacity")
        )
        if db.session.get_bind().dialect.name == "postgresql":
            return _mark_publication_task_deferred_postgresql(
                task_id,
                item=item,
                current_step=current_step,
            )

        task = (
            PublicationTask.query.options(load_only(*DEFERRED_TASK_COLUMNS))
            .filter(PublicationTask.id == str(task_id))
            .first()
        )
        if not task or task.status == "canceled":
            return False
        result = dict(task.result or {})
        history = list(result.get("runtime_guard_history") or [])
        history.append(item)
        result["runtime_guard"] = item
        result["runtime_guard_history"] = history[-20:]
        task.status = "pending"
        task.current_step = current_step
        task.error_message = None
        task.completed_at = None
        task.result = result
        flag_modified(task, "result")
        state = {
            "status": task.status,
            "current_step": task.current_step,
            "result": result,
            "error_message": task.error_message,
            "completed_at": None,
        }
        db.session.commit()
        return state
    except Exception:
        db.session.rollback()
        logger.exception("[TASK_GUARD] failed to persist deferred state | task=%s", task_id)
        return False


def _mark_publication_task_deferred_postgresql(task_id, *, item, current_step):
    item_json = json.dumps(item, ensure_ascii=False, separators=(",", ":"))
    updated = db.session.execute(
        text(
            """
            WITH candidate AS (
                SELECT
                    id,
                    COALESCE(result::jsonb, '{}'::jsonb) AS payload
                FROM public.publication_tasks
                WHERE id = :task_id
                  AND status <> 'canceled'
                FOR UPDATE
            ), trimmed AS (
                SELECT
                    candidate.id,
                    candidate.payload,
                    COALESCE((
                        SELECT jsonb_agg(history.value ORDER BY history.ordinality)
                        FROM (
                            SELECT entry.value, entry.ordinality
                            FROM jsonb_array_elements(
                                CASE
                                    WHEN jsonb_typeof(candidate.payload -> 'runtime_guard_history') = 'array'
                                    THEN candidate.payload -> 'runtime_guard_history'
                                    ELSE '[]'::jsonb
                                END
                            ) WITH ORDINALITY AS entry(value, ordinality)
                            ORDER BY entry.ordinality DESC
                            LIMIT 19
                        ) AS history
                    ), '[]'::jsonb) AS previous_history
                FROM candidate
            )
            UPDATE public.publication_tasks AS task
            SET
                status = 'pending',
                current_step = :current_step,
                error_message = NULL,
                completed_at = NULL,
                updated_at = NOW(),
                result = jsonb_set(
                    jsonb_set(
                        trimmed.payload,
                        '{runtime_guard}',
                        CAST(:item_json AS jsonb),
                        true
                    ),
                    '{runtime_guard_history}',
                    trimmed.previous_history || jsonb_build_array(CAST(:item_json AS jsonb)),
                    true
                )::json
            FROM trimmed
            WHERE task.id = trimmed.id
            RETURNING task.id
            """
        ),
        {
            "task_id": str(task_id),
            "current_step": current_step,
            "item_json": item_json,
        },
    ).first()
    db.session.commit()
    if not updated:
        return False
    return {
        "status": "pending",
        "current_step": current_step,
        "error_message": None,
        "completed_at": None,
    }


def _retry_delay_seconds(admission):
    reason = str(admission.get("reason") or "")
    if reason.startswith("db_"):
        default = 180
    else:
        default = 300
    return _int_env("TASK_ADMISSION_RETRY_DELAY_SECONDS", default, lower=30, upper=3600)


def _admission_retry_max_retries():
    return _int_env("TASK_ADMISSION_MAX_RETRIES", 100000, lower=1, upper=1000000)


def _feature_limit(feature):
    overrides = _feature_limit_overrides()
    if feature in overrides:
        return overrides[feature]
    aliases = FEATURE_ALIASES.get(feature, (feature,))
    for item in aliases:
        if item in overrides:
            return overrides[item]
    return DEFAULT_FEATURE_LIMITS.get(feature)


def _feature_backlog_limit(feature):
    overrides = _feature_limit_overrides("TASK_ADMISSION_FEATURE_BACKLOG_LIMITS")
    if feature in overrides:
        return overrides[feature]
    aliases = FEATURE_ALIASES.get(feature, (feature,))
    for item in aliases:
        if item in overrides:
            return overrides[item]
    return DEFAULT_FEATURE_BACKLOG_LIMITS.get(feature)


def _feature_limit_overrides(env_name="TASK_ADMISSION_FEATURE_LIMITS"):
    raw = os.environ.get(env_name, "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return {
                str(key).strip(): int(value)
                for key, value in data.items()
                if str(key).strip() and int(value) >= 0
            }
    except Exception:
        pass

    limits = {}
    for item in raw.split(","):
        if "=" not in item:
            continue
        key, value = item.split("=", 1)
        key = key.strip()
        try:
            limits[key] = max(0, int(value.strip()))
        except ValueError:
            continue
    return limits


def _db_pressure_message(pressure):
    if pressure.get("reason") == "db_probe_failed":
        return f"系统保护：数据库探测失败，任务稍后自动重试。错误：{pressure.get('error')}"
    return (
        "系统保护：数据库连接压力过高，任务稍后自动重试。"
        f"free={pressure.get('free_connections')}, "
        f"used={pressure.get('used_ratio')}, "
        f"idle_tx={pressure.get('idle_in_transaction_connections')}"
    )


def _allowed(reason, **extra):
    return {"allowed": True, "reason": reason, **extra}


def _denied(reason, message, **extra):
    return {"allowed": False, "reason": reason, "message": message, **extra}


def _cache_pressure(snapshot):
    # Admission already has per-feature concurrency limits. A 15-second local
    # pressure snapshot avoids turning the safety probe itself into a frequent
    # multi-process round-trip over PgBouncer while still reacting quickly.
    ttl = _float_env("TASK_ADMISSION_DB_PRESSURE_CACHE_SECONDS", 15.0, lower=0.0, upper=60.0)
    _DB_PRESSURE_CACHE.update({
        "expires_at": time.time() + ttl,
        "snapshot": dict(snapshot),
    })


def _is_postgres():
    try:
        backend = db.engine.url.get_backend_name()
    except Exception:
        return False
    return str(backend or "").startswith("postgres")


def _truthy_env(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _int_env(name, default, *, lower, upper):
    try:
        value = int(os.environ.get(name, default))
    except (TypeError, ValueError):
        value = default
    return max(lower, min(upper, value))


def _float_env(name, default, *, lower, upper):
    try:
        value = float(os.environ.get(name, default))
    except (TypeError, ValueError):
        value = default
    return max(lower, min(upper, value))
