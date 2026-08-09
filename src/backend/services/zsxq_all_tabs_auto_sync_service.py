"""Schedule the existing ZSXQ "sync all official tabs" behavior for fixed groups."""

import fcntl
from copy import deepcopy
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from models import SystemConfig, db
from models.zsxq_sync import ZsxqSyncRun
from services.runtime_guard import release_db_session_quietly
from services.zsxq_service import zsxq_service
from services.zsxq_sync_service import (
    STALE_SYNC_RUN_STATUSES,
    ZsxqTransientUpstreamError,
    zsxq_sync_service,
)


logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
STATE_PATH = PROJECT_ROOT / "runtime" / "zsxq_all_tabs_auto_sync_state.json"
LOCK_PATH = PROJECT_ROOT / "runtime" / "zsxq_all_tabs_auto_sync.lock"
ENABLED_ENV_NAME = "ZSXQ_ALL_TABS_AUTO_SYNC_ENABLED"
DEFAULT_INTERVAL_MINUTES = 180
DEFAULT_TOPIC_COUNT = 20
CONFIG_TYPE = "zsxq_sync_schedule"
CONFIG_SCOPE = "production"
CONFIG_KEY_PREFIX = "zsxq.sync.schedule."
CONFIG_CACHE_TTL_SECONDS = 60.0
_config_cache = {"expires_at": 0.0, "configs": None}

DEFAULT_AUTO_SYNC_TARGETS = (
    {
        "key": "shengcai",
        "group_id": "1824528822",
        "group_name": "生财有术",
        "mode": "auto_all_tabs_sc",
        "interval_minutes": 180,
        "topic_count": 40,
        "max_pages": 2,
        "download_attachments": False,
        "allow_attachment_downloads": False,
        "download_file_types": ["all"],
        "download_pdf_only": False,
        "notify_milestones": True,
        # Refresh the complete newest page so comments added to recently known
        # topics can still trigger local award favorites.
        "stop_on_existing": False,
    },
    {
        "key": "honghao",
        "group_id": "88885882121542",
        "group_name": "洪灝的宏观策略",
        "mode": "auto_all_tabs_hh",
        "interval_minutes": 180,
        "topic_count": 20,
        "max_pages": 1,
        "download_attachments": False,
        "allow_attachment_downloads": False,
        "download_file_types": ["all"],
        "download_pdf_only": False,
        "notify_milestones": True,
        "stop_on_existing": True,
    },
)
# Compatibility for callers that only need immutable bootstrap defaults.
AUTO_SYNC_TARGETS = DEFAULT_AUTO_SYNC_TARGETS


def all_tabs_auto_sync_enabled():
    raw = os.environ.get(ENABLED_ENV_NAME)
    if raw is not None and str(raw).strip().lower() not in {"1", "true", "yes", "on"}:
        return False
    return any(
        target.get("enabled")
        for target in load_all_tabs_auto_sync_configs(include_disabled=False)
    )


def load_all_tabs_auto_sync_configs(*, include_disabled=True, ensure_defaults=True):
    """Materialize per-group schedules from the shared system_configs table."""
    now = time.monotonic()
    cached = _config_cache.get("configs")
    if cached is not None and now < float(_config_cache.get("expires_at") or 0.0):
        return [
            deepcopy(config)
            for config in cached
            if include_disabled or config["enabled"]
        ]
    try:
        rows = _db_config_rows()
        existing_keys = {str(row.config_key) for row in rows}
        required_keys = {
            _config_key(target["key"])
            for target in DEFAULT_AUTO_SYNC_TARGETS
        }
        if ensure_defaults and not required_keys.issubset(existing_keys):
            ensure_default_auto_sync_configs()
            rows = _db_config_rows()
        configs = [
            _normalize_stored_config(
                _schedule_key_from_config_key(row.config_key),
                _json_object(row.value),
                enabled=int(row.status or 0) == 1,
                updated_at=row.updated_at,
            )
            for row in rows
        ]
        _config_cache.update({
            "expires_at": time.monotonic() + CONFIG_CACHE_TTL_SECONDS,
            "configs": deepcopy(configs),
        })
        release_db_session_quietly()
        return [
            deepcopy(config)
            for config in configs
            if include_disabled or config["enabled"]
        ]
    except Exception as exc:
        try:
            db.session.rollback()
        except Exception:
            pass
        release_db_session_quietly()
        logger.warning(
            "[ZSXQ_ALL_TABS_AUTO] DB schedule config unavailable; using bootstrap defaults | error=%s",
            exc,
        )
        return [
            _normalize_stored_config(
                target["key"],
                _default_config_payload(
                    target,
                    credential_id=_frontier_preferred_credential_id(),
                ),
                enabled=True,
                source="bootstrap_fallback",
            )
            for target in DEFAULT_AUTO_SYNC_TARGETS
        ]


def _db_config_rows():
    return (
        db.session.query(
            SystemConfig.key.label("config_key"),
            SystemConfig.value.label("value"),
            SystemConfig.status.label("status"),
            SystemConfig.updated_at.label("updated_at"),
        )
        .filter(
            SystemConfig.type == CONFIG_TYPE,
            SystemConfig.scope == CONFIG_SCOPE,
            SystemConfig.key.like(f"{CONFIG_KEY_PREFIX}%"),
        )
        .order_by(SystemConfig.key.asc())
        .all()
    )


def _invalidate_config_cache():
    _config_cache.update({"expires_at": 0.0, "configs": None})


def ensure_default_auto_sync_configs():
    """Insert missing default rows without overwriting operator-managed values."""
    config_keys = [
        _config_key(target["key"])
        for target in DEFAULT_AUTO_SYNC_TARGETS
    ]
    rows = (
        db.session.query(
            SystemConfig.key.label("config_key"),
        )
        .filter(
            SystemConfig.scope == CONFIG_SCOPE,
            SystemConfig.key.in_(config_keys),
        )
        .all()
    )
    existing = {str(row.config_key) for row in rows}
    preferred_credential_id = _frontier_preferred_credential_id()
    inserted = []
    for target in DEFAULT_AUTO_SYNC_TARGETS:
        config_key = _config_key(target["key"])
        if config_key in existing:
            continue
        db.session.add(SystemConfig(
            key=config_key,
            value=json.dumps(
                _default_config_payload(
                    target,
                    credential_id=preferred_credential_id,
                ),
                ensure_ascii=False,
                sort_keys=True,
            ),
            type=CONFIG_TYPE,
            scope=CONFIG_SCOPE,
            status=1,
            description=f"知识星球全部 Tab 自动同步：{target['group_name']}",
        ))
        inserted.append(target["key"])
    if inserted:
        db.session.commit()
        _invalidate_config_cache()
        logger.info(
            "[ZSXQ_ALL_TABS_AUTO] default DB configs inserted | schedules=%s credential_id=%s",
            inserted,
            preferred_credential_id or "-",
        )
    return inserted


def save_all_tabs_auto_sync_config(schedule_key, updates):
    """Create or update one schedule row and return its normalized public value."""
    schedule_key = _normalize_schedule_key(schedule_key)
    updates = dict(updates or {})
    row = SystemConfig.query.filter_by(
        key=_config_key(schedule_key),
        scope=CONFIG_SCOPE,
    ).first()
    default = _default_target(schedule_key)
    current = _json_object(row.value) if row else (
        _default_config_payload(
            default,
            credential_id=_frontier_preferred_credential_id(),
        )
        if default
        else {}
    )
    allowed = {
        "group_id",
        "group_name",
        "mode",
        "credential_id",
        "interval_minutes",
        "topic_count",
        "max_pages",
        "stop_on_existing",
        "download_attachments",
        "allow_attachment_downloads",
        "download_file_types",
        "download_pdf_only",
        "notify_milestones",
    }
    current.update({
        key: value
        for key, value in updates.items()
        if key in allowed
    })
    enabled = _bool_value(
        updates.get("enabled"),
        default=(int(row.status or 0) == 1 if row else True),
    )
    normalized = _normalize_stored_config(
        schedule_key,
        current,
        enabled=enabled,
    )
    payload = {
        key: normalized[key]
        for key in allowed
    }
    if row is None:
        row = SystemConfig(
            key=_config_key(schedule_key),
            type=CONFIG_TYPE,
            scope=CONFIG_SCOPE,
        )
        db.session.add(row)
    row.type = CONFIG_TYPE
    row.value = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    row.status = 1 if enabled else 0
    row.description = f"知识星球全部 Tab 自动同步：{normalized['group_name']}"
    updated_at = datetime.utcnow()
    row.updated_at = updated_at
    db.session.commit()
    _invalidate_config_cache()
    result = _normalize_stored_config(
        schedule_key,
        payload,
        enabled=enabled,
        updated_at=updated_at,
    )
    release_db_session_quietly()
    return result


def all_tabs_auto_sync_schedule_signature():
    return tuple(
        (
            target["key"],
            target["interval_minutes"],
            target.get("updated_at"),
        )
        for target in load_all_tabs_auto_sync_configs(include_disabled=False)
    )


def _global_auto_sync_enabled():
    raw = os.environ.get(ENABLED_ENV_NAME)
    return raw is None or str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _normalize_schedule_key(value):
    value = str(value or "").strip().lower()
    if not value or not re.fullmatch(r"[a-z0-9_-]{1,40}", value):
        raise ValueError("schedule_key 仅支持 1-40 位小写字母、数字、下划线或连字符")
    return value


def _config_key(schedule_key):
    return f"{CONFIG_KEY_PREFIX}{_normalize_schedule_key(schedule_key)}"


def _schedule_key_from_config_key(config_key):
    value = str(config_key or "")
    if not value.startswith(CONFIG_KEY_PREFIX):
        raise ValueError(f"未知知识星球同步配置键: {value}")
    return _normalize_schedule_key(value[len(CONFIG_KEY_PREFIX):])


def _default_target(schedule_key):
    schedule_key = _normalize_schedule_key(schedule_key)
    return next(
        (
            target
            for target in DEFAULT_AUTO_SYNC_TARGETS
            if target["key"] == schedule_key
        ),
        None,
    )


def _frontier_preferred_credential_id():
    try:
        from services.zsxq_system_sync_test import load_config

        config = load_config(required=False) or {}
        return str(config.get("credential_id") or "").strip() or None
    except Exception:
        return None


def _default_config_payload(target, *, credential_id=None):
    return {
        "group_id": str(target["group_id"]),
        "group_name": str(target["group_name"]),
        "mode": str(target["mode"]),
        "credential_id": str(credential_id or "").strip() or None,
        "interval_minutes": int(target.get("interval_minutes") or DEFAULT_INTERVAL_MINUTES),
        "topic_count": int(target.get("topic_count") or DEFAULT_TOPIC_COUNT),
        "max_pages": int(target.get("max_pages") or 1),
        "stop_on_existing": bool(target.get("stop_on_existing", True)),
        "download_attachments": bool(target.get("download_attachments", False)),
        "allow_attachment_downloads": bool(target.get("allow_attachment_downloads", False)),
        "download_file_types": list(target.get("download_file_types") or ["all"]),
        "download_pdf_only": bool(target.get("download_pdf_only", False)),
        "notify_milestones": bool(target.get("notify_milestones", True)),
    }


def _normalize_stored_config(
    schedule_key,
    payload,
    *,
    enabled,
    updated_at=None,
    source="database",
):
    schedule_key = _normalize_schedule_key(schedule_key)
    payload = dict(payload or {})
    default = _default_target(schedule_key) or {}
    group_id = str(payload.get("group_id") or default.get("group_id") or "").strip()
    if not group_id:
        raise ValueError(f"{schedule_key} 缺少 group_id")
    group_name = str(
        payload.get("group_name")
        or default.get("group_name")
        or group_id
    ).strip()
    mode = str(
        payload.get("mode")
        or default.get("mode")
        or f"auto_{schedule_key}"
    ).strip()[:20]
    interval_minutes = min(max(
        int(payload.get("interval_minutes") or DEFAULT_INTERVAL_MINUTES),
        1,
    ), 24 * 60)
    topic_count = min(max(
        int(payload.get("topic_count") or DEFAULT_TOPIC_COUNT),
        1,
    ), 50)
    max_pages = min(max(int(payload.get("max_pages") or 1), 1), 20)
    file_types = payload.get("download_file_types") or ["all"]
    if not isinstance(file_types, (list, tuple)):
        file_types = [file_types]
    file_types = [
        str(value).strip().lower()
        for value in file_types
        if str(value or "").strip()
    ] or ["all"]
    normalized_updated_at = None
    if updated_at:
        normalized_updated_at = (
            updated_at.isoformat() + "Z"
            if hasattr(updated_at, "isoformat")
            else str(updated_at)
        )
    return {
        "key": schedule_key,
        "group_id": group_id,
        "group_name": group_name,
        "mode": mode,
        "credential_id": str(payload.get("credential_id") or "").strip() or None,
        "enabled": bool(enabled),
        "interval_minutes": interval_minutes,
        "topic_count": topic_count,
        "max_pages": max_pages,
        "stop_on_existing": _bool_value(
            payload.get("stop_on_existing"),
            default=bool(default.get("stop_on_existing", True)),
        ),
        "download_attachments": _bool_value(
            payload.get("download_attachments"),
            default=False,
        ),
        "allow_attachment_downloads": _bool_value(
            payload.get("allow_attachment_downloads"),
            default=False,
        ),
        "download_file_types": file_types,
        "download_pdf_only": _bool_value(
            payload.get("download_pdf_only"),
            default=False,
        ),
        "notify_milestones": _bool_value(
            payload.get("notify_milestones"),
            default=True,
        ),
        "updated_at": normalized_updated_at,
        "source": source,
    }


def _json_object(value):
    if isinstance(value, dict):
        return dict(value)
    try:
        payload = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        raise ValueError("知识星球同步配置 value 必须是 JSON 对象")
    if not isinstance(payload, dict):
        raise ValueError("知识星球同步配置 value 必须是 JSON 对象")
    return payload


def _bool_value(value, *, default=False):
    if value in (None, ""):
        return bool(default)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def all_tabs_auto_sync_status():
    state = _read_state()
    configs = load_all_tabs_auto_sync_configs(include_disabled=True)
    last_trigger_at = state.get("last_trigger_at")
    return {
        "enabled": (
            _global_auto_sync_enabled()
            and any(config["enabled"] for config in configs)
        ),
        "interval_minutes": min(
            (
                config["interval_minutes"]
                for config in configs
                if config["enabled"]
            ),
            default=None,
        ),
        "targets": [
            {
                **target,
                "refresh_existing_page": not target["stop_on_existing"],
                "next_run": _target_next_run(state, target),
            }
            for target in configs
        ],
        "state_path": str(STATE_PATH),
        "last_trigger_at": last_trigger_at,
        "next_run": min(
            (
                next_run
                for next_run in (
                    _target_next_run(state, target)
                    for target in configs
                    if target["enabled"]
                )
                if next_run
            ),
            default=None,
        ),
        "last_result": state.get("last_result"),
    }


def queue_all_tabs_auto_sync_targets(trigger="scheduler", schedule_key=None):
    """Queue both fixed groups while preserving per-group idempotency."""
    if not _global_auto_sync_enabled():
        return {
            "queued": False,
            "skipped": True,
            "reason": "disabled",
            "targets": [],
        }
    targets = load_all_tabs_auto_sync_configs(include_disabled=False)
    if schedule_key:
        normalized_key = _normalize_schedule_key(schedule_key)
        targets = [
            target
            for target in targets
            if target["key"] == normalized_key
        ]
        if not targets:
            return {
                "queued": False,
                "skipped": True,
                "reason": "schedule_missing_or_disabled",
                "schedule_key": normalized_key,
                "targets": [],
            }

    lock_handle = _try_acquire_lock()
    if lock_handle is None:
        return {
            "queued": False,
            "skipped": True,
            "reason": "trigger_already_running",
            "targets": [],
        }

    attempt_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    triggered_at = _now_iso()
    try:
        credentials = {}
        target_results = []
        for target in targets:
            try:
                preferred_credential_id = target.get("credential_id")
                credential_cache_key = preferred_credential_id or "__active__"
                credential = credentials.get(credential_cache_key)
                if credential is None:
                    credential = _resolve_shared_sync_credential(
                        preferred_id=preferred_credential_id,
                    )
                    credentials[credential_cache_key] = {
                        "cookie": credential["cookie"],
                        "credential_id": credential.get("credential_id"),
                    }
                    credential = credentials[credential_cache_key]
                    # Credential resolution may open a transaction. Menus are
                    # remote I/O, so only scalar values cross this boundary.
                    release_db_session_quietly()
                target_results.append(
                    _queue_target(
                        target,
                        cookie=credential["cookie"],
                        credential_id=credential.get("credential_id"),
                        trigger=trigger,
                        attempt_id=attempt_id,
                    )
                )
            except Exception as exc:
                release_db_session_quietly()
                logger.exception(
                    "[ZSXQ_ALL_TABS_AUTO] target failed | attempt_id=%s group_id=%s error=%s",
                    attempt_id,
                    target["group_id"],
                    exc,
                )
                _enqueue_all_tabs_notification(
                    "sync_failed",
                    target,
                    trigger=trigger,
                    attempt_id=attempt_id,
                    status="failed",
                    error=str(exc),
                )
                target_results.append({
                    "key": target["key"],
                    "group_id": target["group_id"],
                    "group_name": target["group_name"],
                    "queued": False,
                    "skipped": False,
                    "reason": "error",
                    "error": str(exc),
                })

        result = {
            "queued": any(item.get("queued") for item in target_results),
            "skipped": all(item.get("skipped") for item in target_results),
            "attempt_id": attempt_id,
            "trigger": trigger,
            "triggered_at": triggered_at,
            "schedule_key": schedule_key,
            "targets": target_results,
        }
        _record_trigger_state(triggered_at, result, targets)
        return result
    except Exception as exc:
        release_db_session_quietly()
        result = {
            "queued": False,
            "skipped": False,
            "reason": "credential_or_trigger_error",
            "attempt_id": attempt_id,
            "trigger": trigger,
            "triggered_at": triggered_at,
            "error": str(exc),
            "targets": [],
        }
        _record_trigger_state(triggered_at, result, targets)
        logger.exception(
            "[ZSXQ_ALL_TABS_AUTO] trigger failed | attempt_id=%s error=%s",
            attempt_id,
            exc,
        )
        return result
    finally:
        release_db_session_quietly()
        _release_lock(lock_handle)


def tabs_from_menus(menus):
    tabs = []
    seen = set()
    for menu in menus or []:
        tab = _menu_to_tab(menu)
        tab_id = str((tab or {}).get("id") or "")
        if not tab_id or tab_id in seen:
            continue
        seen.add(tab_id)
        tabs.append(tab)
    return tabs


def build_auto_sync_payload(target, *, credential_id, tabs, trigger, attempt_id):
    return {
        "cookie": None,
        "credential_id": credential_id,
        "group_id": target["group_id"],
        "group_name": target["group_name"],
        "tabs": list(tabs or []),
        "count": int(target.get("topic_count") or DEFAULT_TOPIC_COUNT),
        "max_pages": int(target.get("max_pages") or 1),
        "stop_on_existing": bool(target["stop_on_existing"]),
        "download_attachments": bool(target.get("download_attachments", False)),
        "allow_attachment_downloads": bool(target.get("allow_attachment_downloads", False)),
        "download_file_types": list(target.get("download_file_types") or ["all"]),
        "download_pdf_only": bool(target.get("download_pdf_only", False)),
        "mode": target["mode"],
        "trigger": trigger,
        "auto_all_tabs": {
            "attempt_id": attempt_id,
            "trigger": trigger,
            "interval_minutes": target["interval_minutes"],
            "comments_refresh_enabled": not target["stop_on_existing"],
            "notify_milestones": bool(target.get("notify_milestones", True)),
        },
    }


def _queue_target(target, *, cookie, credential_id, trigger, attempt_id):
    active = _active_run_snapshot(target["group_id"])
    if active:
        return {
            "key": target["key"],
            "group_id": target["group_id"],
            "group_name": target["group_name"],
            "queued": False,
            "skipped": True,
            "reason": "active_run_exists",
            "active_run": active,
        }

    # The DB session was released by the active-run snapshot. Menu discovery
    # intentionally happens without an open transaction.
    response = _get_menus_with_retry(cookie, target)
    resp_data = response.get("resp_data") if isinstance(response, dict) else {}
    menus = (resp_data or {}).get("menus") or (resp_data or {}).get("menu") or []
    tabs = tabs_from_menus(menus)
    if not tabs:
        raise ValueError(f"{target['group_name']}未返回可同步的官方 Tab")

    initial_result = {
        "queued": True,
        "auto_all_tabs": {
            "attempt_id": attempt_id,
            "trigger": trigger,
            "interval_minutes": target["interval_minutes"],
            "tabs_total": len(tabs),
            "topic_count": target["topic_count"],
            "comments_refresh_enabled": not target["stop_on_existing"],
        },
    }
    run = zsxq_sync_service.create_sync_run(
        group_id=target["group_id"],
        group_name=target["group_name"],
        tabs=tabs,
        mode=target["mode"],
        initial_result=initial_result,
    )
    run_id = str(run["id"])
    payload = build_auto_sync_payload(
        target,
        credential_id=credential_id,
        tabs=tabs,
        trigger=trigger,
        attempt_id=attempt_id,
    )
    release_db_session_quietly()

    try:
        from tasks.zsxq_sync import run_zsxq_sync

        async_result = run_zsxq_sync.apply_async(
            args=[run_id, payload],
            queue="zsxq_sync",
            retry=False,
        )
    except Exception as exc:
        release_db_session_quietly()
        zsxq_sync_service.mark_run_failed(
            run_id,
            f"全部 Tab 自动同步任务入队失败: {exc}",
            {
                "queued": False,
                "running": False,
                "queue_publish_failed": True,
                "auto_all_tabs": initial_result["auto_all_tabs"],
            },
        )
        release_db_session_quietly()
        raise

    logger.info(
        "[ZSXQ_ALL_TABS_AUTO] queued | attempt_id=%s group_id=%s run_id=%s celery_id=%s tabs=%s",
        attempt_id,
        target["group_id"],
        run_id,
        async_result.id,
        len(tabs),
    )
    _enqueue_all_tabs_notification(
        "workflow_started",
        target,
        trigger=trigger,
        attempt_id=attempt_id,
        run_id=run_id,
        status="queued",
        tabs_total=len(tabs),
    )
    return {
        "key": target["key"],
        "group_id": target["group_id"],
        "group_name": target["group_name"],
        "queued": True,
        "skipped": False,
        "run_id": run_id,
        "celery_task_id": async_result.id,
        "tabs_total": len(tabs),
        "topic_count": target["topic_count"],
        "mode": target["mode"],
        "comments_refresh_enabled": not target["stop_on_existing"],
    }


def _get_menus_with_retry(cookie, target):
    """Discover official tabs with the same transient retry policy as topic pages."""
    attempts = zsxq_sync_service._sync_retry_attempts()
    for attempt in range(1, attempts + 1):
        try:
            response = zsxq_service.get_menus(cookie, target["group_id"])
            zsxq_sync_service._assert_upstream_success(response)
            if attempt > 1:
                logger.info(
                    "[ZSXQ_ALL_TABS_AUTO] menu retry recovered | group_id=%s attempt=%s/%s",
                    target["group_id"],
                    attempt,
                    attempts,
                )
            return response
        except (ZsxqTransientUpstreamError, requests.RequestException) as exc:
            if attempt >= attempts:
                logger.warning(
                    "[ZSXQ_ALL_TABS_AUTO] menu retry exhausted | group_id=%s attempts=%s error=%s",
                    target["group_id"],
                    attempts,
                    exc,
                )
                raise
            delay = zsxq_sync_service._sync_retry_delay_seconds(attempt)
            logger.warning(
                "[ZSXQ_ALL_TABS_AUTO] menu transient failure | group_id=%s attempt=%s/%s retry_in=%.1fs error=%s",
                target["group_id"],
                attempt,
                attempts,
                delay,
                exc,
            )
            release_db_session_quietly()
            time.sleep(delay)

    raise RuntimeError("ZSXQ_ALL_TABS_MENU_RETRY_LOOP_UNREACHABLE")


def _enqueue_all_tabs_notification(
    event_type,
    target,
    *,
    trigger,
    attempt_id,
    run_id=None,
    status,
    error=None,
    tabs_total=None,
    summary=None,
):
    if not target.get("notify_milestones", True):
        return {"queued": False, "reason": "target_disabled"}
    try:
        from services.workflow_notification_service import workflow_notification_service

        stage_status = {
            "queued": "queued",
            "completed": "completed",
            "failed": "failed",
        }.get(status, status)
        payload = {
            "id": str(run_id or attempt_id),
            "batch_id": str(attempt_id),
            "group_id": target.get("group_id"),
            "group_name": target.get("group_name"),
            "trigger": trigger,
            "status": status,
            "current_stage": "sync" if status != "completed" else "done",
            "error_message": str(error or "")[:1000] or None,
            "stage_results": {
                "sync": {
                    "status": stage_status,
                    "summary": summary or {},
                    "tabs_total": tabs_total,
                },
            },
        }
        result = workflow_notification_service.enqueue_workflow_event(
            event_type,
            payload,
        )
        logger.info(
            "[ZSXQ_ALL_TABS_AUTO] notification | event=%s group=%s run_id=%s queued=%s reason=%s",
            event_type,
            target.get("group_id") or "-",
            run_id or "-",
            bool(result.get("queued")),
            result.get("reason") or "-",
        )
        return result
    except Exception as exc:
        logger.exception(
            "[ZSXQ_ALL_TABS_AUTO] notification failed | event=%s group=%s error=%s",
            event_type,
            target.get("group_id") or "-",
            exc,
        )
        return {"queued": False, "reason": "queue_failed", "error": str(exc)}


def _active_run_snapshot(group_id):
    zsxq_sync_service._mark_stale_runs_failed(group_id=group_id)
    row = (
        db.session.query(
            ZsxqSyncRun.id.label("id"),
            ZsxqSyncRun.status.label("status"),
            ZsxqSyncRun.mode.label("mode"),
            ZsxqSyncRun.updated_at.label("updated_at"),
        )
        .filter(
            ZsxqSyncRun.group_id == str(group_id),
            ZsxqSyncRun.status.in_(STALE_SYNC_RUN_STATUSES),
        )
        .order_by(ZsxqSyncRun.created_at.desc())
        .first()
    )
    snapshot = None
    if row:
        snapshot = {
            "id": str(row.id),
            "status": row.status,
            "mode": row.mode,
            "updated_at": row.updated_at.isoformat() + "Z" if row.updated_at else None,
        }
    release_db_session_quietly()
    return snapshot


def _resolve_shared_sync_credential(preferred_id=None):
    from services.zsxq_system_sync_test import load_config, runtime_cookie_info

    preferred_id = str(preferred_id or "").strip() or None
    if not preferred_id:
        frontier_config = load_config(required=False) or {}
        preferred_id = str(frontier_config.get("credential_id") or "").strip() or None
    return runtime_cookie_info(account_id=preferred_id, required=True)


def _menu_to_tab(menu):
    if not isinstance(menu, dict):
        return None
    label = _clean_menu_title(menu.get("title") or menu.get("name") or menu.get("label") or menu.get("menu_id"))
    hashtag_id = _menu_hashtag_id(menu)
    if hashtag_id:
        return {
            "id": f"hashtag:{hashtag_id}",
            "label": label,
            "scope": "all",
            "hashtagId": hashtag_id,
        }

    if label == "最新":
        return {"id": "preset:all", "label": label, "scope": "all"}
    if label == "精华":
        return {"id": "preset:digests", "label": label, "scope": "digests"}

    preset = menu.get("preset_type") or menu.get("preset") or menu.get("type")
    if not preset:
        return None
    preset = str(preset)
    scope = {
        "all": "all",
        "with_files": "with_files",
        "with_images": "with_images",
        "digests": "digests",
    }.get(preset, preset)
    return {"id": f"preset:{preset}", "label": label, "scope": scope}


def _menu_hashtag_id(menu):
    hashtag = menu.get("hashtag") if isinstance(menu.get("hashtag"), dict) else {}
    target = menu.get("target") if isinstance(menu.get("target"), dict) else {}
    value = (
        hashtag.get("hashtag_id")
        or menu.get("hashtag_id")
        or target.get("hashtag_id")
        or target.get("id")
        or (menu.get("menu_id") if menu.get("type") == "hashtag" else None)
    )
    return str(value).strip() if value not in (None, "") else None


def _clean_menu_title(value):
    return str(value or "").strip().strip("#").strip() or "未命名"


def _try_acquire_lock():
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    handle = LOCK_PATH.open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None
    handle.seek(0)
    handle.truncate()
    handle.write(f"{os.getpid()}\n")
    handle.flush()
    return handle


def _release_lock(handle):
    if handle is None:
        return
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def _read_state():
    if not STATE_PATH.exists():
        return {}
    try:
        payload = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError, json.JSONDecodeError):
        return {}


def _write_state(payload):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = STATE_PATH.with_suffix(".json.tmp")
    tmp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.chmod(tmp_path, 0o600)
    tmp_path.replace(STATE_PATH)
    os.chmod(STATE_PATH, 0o600)


def _record_trigger_state(triggered_at, result, targets):
    state = _read_state()
    schedule_states = dict(state.get("schedules") or {})
    results_by_key = {
        str(item.get("key")): item
        for item in result.get("targets") or []
        if item.get("key")
    }
    for target in targets or []:
        schedule_key = target["key"]
        schedule_states[schedule_key] = {
            **dict(schedule_states.get(schedule_key) or {}),
            "last_trigger_at": triggered_at,
            "last_result": results_by_key.get(schedule_key),
            "scheduled_next_run": _next_run(
                triggered_at,
                target["interval_minutes"],
            ),
        }
    _write_state({
        "last_trigger_at": triggered_at,
        "last_result": result,
        "schedules": schedule_states,
    })


def record_all_tabs_schedule_plan(plans):
    """Persist scheduler-calculated next runs for status APIs and monitoring."""
    state = _read_state()
    schedule_states = dict(state.get("schedules") or {})
    for plan in plans or []:
        schedule_key = _normalize_schedule_key(plan.get("key"))
        schedule_states[schedule_key] = {
            **dict(schedule_states.get(schedule_key) or {}),
            "scheduled_next_run": plan.get("next_run"),
            "interval_minutes": int(plan.get("interval_minutes") or DEFAULT_INTERVAL_MINUTES),
            "topic_count": int(plan.get("topic_count") or DEFAULT_TOPIC_COUNT),
        }
    _write_state({
        **state,
        "schedules": schedule_states,
    })


def _target_next_run(state, target):
    schedule_state = (state.get("schedules") or {}).get(target["key"]) or {}
    return schedule_state.get("scheduled_next_run") or _next_run(
        schedule_state.get("last_trigger_at") or state.get("last_trigger_at"),
        target["interval_minutes"],
    )


def _next_run(last_trigger_at, interval_minutes):
    if not last_trigger_at:
        return None
    try:
        anchor = datetime.fromisoformat(str(last_trigger_at).replace("Z", "+00:00"))
    except ValueError:
        return None
    return (anchor + timedelta(minutes=interval_minutes)).isoformat().replace("+00:00", "Z")


def _now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
