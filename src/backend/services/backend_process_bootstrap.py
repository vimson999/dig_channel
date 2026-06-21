import importlib
import json
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
RUNTIME_DIR = PROJECT_ROOT / "runtime"
LOG_PATH = PROJECT_ROOT / "logs" / "backend_process_bootstrap.log"

DEFAULT_SCHEDULERS = ()

SCHEDULERS = {
    "zsxq_auto": {
        "label": "知识星球编排计划",
        "module": "services.zsxq_system_sync_scheduler",
    },
    "zsxq_auto_publish": {
        "label": "知识星球自动发帖计划",
        "module": "services.zsxq_auto_publish_scheduler",
    },
    "content_retention": {
        "label": "内容流水线历史清理计划",
        "module": "services.content_pipeline.retention_scheduler_service",
    },
    "report_video_asset": {
        "label": "研报资产补漏计划",
        "module": "services.report_video_asset_scheduler_service",
    },
    "report_video_auto": {
        "label": "研报自动扫描计划",
        "module": "services.report_video_auto_scheduler_service",
    },
}

_STARTED = False
_LOCK_HANDLE = None


def start_backend_process_bootstrap(app=None):
    """Start managed Celery workers and schedulers after the backend starts."""
    global _STARTED
    if _STARTED:
        return {"started": False, "skipped": True, "reason": "already_started"}

    if not _truthy_env("BACKEND_PROCESS_BOOTSTRAP_ENABLED", False):
        return {"started": False, "skipped": True, "reason": "disabled"}

    skip_reason = _current_process_skip_reason()
    if skip_reason:
        return {"started": False, "skipped": True, "reason": skip_reason}

    _STARTED = True
    if _truthy_env("BACKEND_PROCESS_BOOTSTRAP_ASYNC", True):
        thread = threading.Thread(
            target=_run_and_log,
            args=(app,),
            name="backend-process-bootstrap",
            daemon=True,
        )
        thread.start()
        return {"started": True, "async": True}

    return run_backend_process_bootstrap(app=app)


def run_backend_process_bootstrap(app=None):
    lock_handle = _acquire_lock()
    if not lock_handle:
        _log_event(app, "skip", {"reason": "lock_held"})
        return {"started": False, "skipped": True, "reason": "lock_held"}

    started_at = datetime.utcnow().isoformat() + "Z"
    mode = (os.environ.get("BACKEND_PROCESS_BOOTSTRAP_MODE") or "ensure").strip().lower()
    if mode not in {"restart", "ensure"}:
        mode = "ensure"

    config = {
        "mode": mode,
        "celery_services": os.environ.get("BACKEND_PROCESS_BOOTSTRAP_CELERY_SERVICES") or "all",
        "schedulers": os.environ.get("BACKEND_PROCESS_BOOTSTRAP_SCHEDULERS") or ",".join(DEFAULT_SCHEDULERS),
        "watchdog": _truthy_env("BACKEND_PROCESS_BOOTSTRAP_WATCHDOG", True),
    }
    _log_event(app, "start", config)

    results = {
        "started_at": started_at,
        "mode": mode,
        "celery": [],
        "watchdog": None,
        "schedulers": [],
        "errors": [],
    }

    try:
        results["celery"] = _bootstrap_celery_services(app, mode)
        if config["watchdog"]:
            results["watchdog"] = _bootstrap_watchdog(app, mode)
        results["schedulers"] = _bootstrap_schedulers(app, mode)
    finally:
        results["finished_at"] = datetime.utcnow().isoformat() + "Z"
        _log_event(app, "finish", results)
        _release_lock(lock_handle)

    return results


def _run_and_log(app):
    try:
        run_backend_process_bootstrap(app=app)
    except Exception as exc:
        _log_event(app, "error", {"error": str(exc), "type": type(exc).__name__})
        if app:
            app.logger.exception("[BACKEND_BOOTSTRAP] failed")


def _bootstrap_celery_services(app, mode):
    from apis.v1.celery_monitor import (
        SERVICES,
        _service_status,
        _start_service,
        _stop_service,
    )

    service_ids = _selected_ids(
        os.environ.get("BACKEND_PROCESS_BOOTSTRAP_CELERY_SERVICES"),
        default=list(SERVICES.keys()),
        allowed=SERVICES.keys(),
    )
    results = []

    for service_id in service_ids:
        service = SERVICES[service_id]
        item = {
            "id": service_id,
            "label": service.get("label"),
            "action": mode,
            "ok": False,
        }
        try:
            if mode == "restart":
                item["stop"] = _stop_service(service_id, service)
                time.sleep(0.2)
                item["start"] = _start_service(service_id, service)
            else:
                status = _service_status(service_id, service, include_logs=False)
                if status.get("running"):
                    item["action"] = "already_running"
                    item["status"] = {"running": True, "pids": status.get("pids")}
                else:
                    item["action"] = "start"
                    item["start"] = _start_service(service_id, service)
            item["ok"] = True
        except Exception as exc:
            item["error"] = str(exc)
            item["type"] = type(exc).__name__
            _log_event(app, "celery_error", item)
        results.append(item)

    return results


def _bootstrap_watchdog(app, mode):
    from apis.v1.celery_monitor import (
        _start_watchdog,
        _stop_watchdog,
        _watchdog_status_payload,
    )

    result = {"id": "celery_watchdog", "action": mode, "ok": False}
    try:
        status = _watchdog_status_payload()
        if mode == "ensure" and status.get("running"):
            result.update({"action": "already_running", "ok": True, "status": status})
            return result

        if mode == "restart":
            result["stop"] = _stop_watchdog()
            time.sleep(0.2)
        result["start"] = _start_watchdog()
        result["ok"] = True
    except Exception as exc:
        result["error"] = str(exc)
        result["type"] = type(exc).__name__
        _log_event(app, "watchdog_error", result)
    return result


def _bootstrap_schedulers(app, mode):
    scheduler_ids = _selected_ids(
        os.environ.get("BACKEND_PROCESS_BOOTSTRAP_SCHEDULERS"),
        default=list(DEFAULT_SCHEDULERS),
        allowed=SCHEDULERS.keys(),
    )
    results = []

    for scheduler_id in scheduler_ids:
        meta = SCHEDULERS[scheduler_id]
        item = {
            "id": scheduler_id,
            "label": meta["label"],
            "action": mode,
            "ok": False,
        }
        try:
            module = importlib.import_module(meta["module"])
            if mode == "ensure":
                status = module.scheduler_status()
                if status.get("running"):
                    item.update({"action": "already_running", "ok": True, "status": status})
                    results.append(item)
                    continue
                item["action"] = "start"
                item["start"] = module.start_scheduler()
            else:
                item["restart"] = module.restart_scheduler()
            item["ok"] = True
        except Exception as exc:
            item["error"] = str(exc)
            item["type"] = type(exc).__name__
            _log_event(app, "scheduler_error", item)
        results.append(item)

    return results


def _selected_ids(raw, *, default, allowed):
    allowed_set = set(allowed)
    if raw is None:
        return [item for item in default if item in allowed_set]

    raw = raw.strip()
    if not raw or raw.lower() in {"none", "off", "false", "0"}:
        return []
    if raw.lower() == "all":
        return [item for item in allowed if item in allowed_set]

    selected = []
    for item in raw.split(","):
        service_id = item.strip()
        if service_id and service_id in allowed_set and service_id not in selected:
            selected.append(service_id)
    return selected


def _truthy_env(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _current_process_skip_reason():
    command = " ".join(str(item) for item in sys.argv)
    markers = (
        "celery",
        "celery_watchdog_loop.py",
        "run_zsxq_auto_scheduler.py",
        "run_zsxq_auto_publish_scheduler.py",
        "run_content_retention_cleanup_scheduler.py",
        "run_report_video_asset_scheduler.py",
        "run_report_video_auto_scheduler.py",
        "pytest",
        "flask db",
        "alembic",
    )
    for marker in markers:
        if marker in command:
            return f"process_context:{marker}"
    return None


def _acquire_lock():
    global _LOCK_HANDLE
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = RUNTIME_DIR / "backend_process_bootstrap.lock"
    handle = lock_path.open("a", encoding="utf-8")
    try:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None
    except Exception:
        handle.close()
        raise

    _LOCK_HANDLE = handle
    return handle


def _release_lock(handle):
    global _LOCK_HANDLE
    try:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()
        if _LOCK_HANDLE is handle:
            _LOCK_HANDLE = None


def _log_event(app, event, payload):
    safe_payload = _redact_payload(payload)
    record = {
        "ts": datetime.utcnow().isoformat() + "Z",
        "event": event,
        "payload": safe_payload,
    }
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False, default=str))
        fh.write("\n")

    if app:
        message = f"[BACKEND_BOOTSTRAP] {event}: {safe_payload}"
        if event.endswith("error") or event == "error":
            app.logger.error(message)
        else:
            app.logger.info(message)


def _redact_payload(value):
    if isinstance(value, dict):
        output = {}
        for key, item in value.items():
            key_text = str(key).lower()
            if key_text in {"cookie", "cookie_override", "authorization", "token", "access_token"}:
                output[key] = "***REDACTED***"
            else:
                output[key] = _redact_payload(item)
        return output
    if isinstance(value, list):
        return [_redact_payload(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_payload(item) for item in value)
    if isinstance(value, str) and ("zsxq_access_token" in value or "Cookie:" in value):
        return "***REDACTED***"
    return value
