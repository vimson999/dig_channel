import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path

import redis

from celery_config import celery_app
from models.zsxq_sync import ZsxqSyncRun
from services.zsxq_service import zsxq_service
from services.zsxq_sync_service import zsxq_sync_service


logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
CONFIG_PATH = PROJECT_ROOT / "runtime" / "zsxq_scheduled_sync_test.json"
DEFAULT_COUNT = 12
DEFAULT_MAX_DOWNLOAD_FILES = 9
DEFAULT_MAX_ROUNDS = 0
COOKIE_ENV_NAME = "ZSXQ_SYNC_TEST_COOKIE"
COOKIE_OVERRIDE_ENV_NAME = "ZSXQ_SYNC_TEST_COOKIE_OVERRIDE"
ENV_INTERVAL_MINUTES = 25
GROUP_ID_PLACEHOLDER_RE = re.compile(r"^group[-_]?test", re.IGNORECASE)


def config_exists():
    return CONFIG_PATH.exists()


def config_status():
    if not CONFIG_PATH.exists():
        return {
            "exists": False,
            "path": str(CONFIG_PATH),
            "cookie_env_name": COOKIE_ENV_NAME,
            "cookie_env_configured": bool(os.environ.get(COOKIE_ENV_NAME)),
        }

    config = load_config()
    return _snapshot_config(config)


def _snapshot_config(config):
    max_rounds = _normalize_max_rounds(config.get("max_rounds"), default=DEFAULT_MAX_ROUNDS, none_means_continuous=True)
    rounds_completed = _bounded_int(config.get("rounds_completed"), default=0, lower=0, upper=1_000_000)
    group_id = str(config.get("group_id") or "").strip()
    group_validation_error = _validate_group_id_local(group_id)
    enabled = bool(config.get("enabled", True))
    if group_validation_error and _is_placeholder_group_id(group_id):
        enabled = False
    return {
        "exists": True,
        "path": str(CONFIG_PATH),
        "updated_at": config.get("updated_at"),
        "enabled": enabled,
        "cookie_env_name": COOKIE_ENV_NAME,
        "cookie_env_configured": bool(os.environ.get(COOKIE_ENV_NAME)),
        "interval_minutes": _scheduler_interval_minutes(config.get("interval_minutes")),
        "group_id": group_id,
        "group_validation_error": group_validation_error,
        "group_name": config.get("group_name"),
        "count": config.get("count"),
        "max_pages": config.get("max_pages"),
        "max_download_files": config.get("max_download_files"),
        "auto_pdf_clean": bool(config.get("auto_pdf_clean", True)),
        "max_rounds": max_rounds,
        "continuous": max_rounds is None,
        "rounds_completed": rounds_completed,
        "testing_complete": False if max_rounds is None else rounds_completed >= max_rounds,
        "last_batch_id": config.get("last_batch_id"),
        "last_trigger_at": config.get("last_trigger_at"),
        "last_queued_run_id": config.get("last_queued_run_id"),
        "jobs": [
            {
                "key": job.get("key"),
                "tab_label": (job.get("tab") or {}).get("label"),
                "file_type": job.get("file_type"),
                "mode_code": job.get("mode_code"),
            }
            for job in config.get("jobs", [])
        ],
    }


def save_config(payload, cookie):
    group_id = str(payload.get("group_id") or "").strip()
    if not group_id:
        raise ValueError("group_id 不能为空")

    if cookie:
        cookie = zsxq_service.normalize_cookie(cookie)
    else:
        env_cookie = _strip_wrapped_quotes((os.environ.get(COOKIE_ENV_NAME) or "").strip())
        if env_cookie:
            try:
                cookie = zsxq_service.normalize_cookie(env_cookie)
            except Exception:
                cookie = None

    _validate_group_id(group_id, cookie=cookie)
    jobs = _normalize_jobs(payload.get("jobs"))
    if not jobs:
        raise ValueError("系统计划测试至少需要一个同步目标")

    now = datetime.utcnow().isoformat() + "Z"
    previous = load_config(required=False) or {}
    config = {
        "enabled": True,
        "created_at": previous.get("created_at") or now,
        "updated_at": now,
        "cookie_env_name": COOKIE_ENV_NAME,
        "group_id": group_id,
        "group_name": payload.get("group_name") or "",
        "interval_minutes": _normalize_interval_minutes(
            payload.get("interval_minutes"),
            default=_scheduler_interval_minutes(),
        ),
        "count": _bounded_int(payload.get("count"), default=DEFAULT_COUNT, lower=1, upper=50),
        "max_pages": _bounded_int(payload.get("max_pages"), default=1, lower=1, upper=5),
        "stop_on_existing": bool(payload.get("stop_on_existing", True)),
        "download_attachments": bool(payload.get("download_attachments", True)),
        "max_download_files": _bounded_int(
            payload.get("max_download_files"),
            default=DEFAULT_MAX_DOWNLOAD_FILES,
            lower=1,
            upper=10,
        ),
        "auto_pdf_clean": bool(payload.get("auto_pdf_clean", previous.get("auto_pdf_clean", True))),
        "max_rounds": _normalize_max_rounds(
            payload["max_rounds"] if "max_rounds" in payload else DEFAULT_MAX_ROUNDS,
            default=DEFAULT_MAX_ROUNDS,
            none_means_continuous=True,
        ),
        "rounds_completed": 0,
        "last_batch_id": None,
        "last_trigger_at": None,
        "last_queued_run_id": None,
        "jobs": jobs,
    }

    _write_config(config)
    return config_status()


def load_config(required=True):
    if not CONFIG_PATH.exists():
        if required:
            raise FileNotFoundError(f"系统计划测试配置不存在: {CONFIG_PATH}")
        return None

    with CONFIG_PATH.open("r", encoding="utf-8") as fh:
        config = json.load(fh)
    if not isinstance(config, dict):
        raise ValueError("系统计划测试配置格式不正确")
    return config


def _write_config(config):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = CONFIG_PATH.with_suffix(".json.tmp")
    with tmp_path.open("w", encoding="utf-8") as fh:
        json.dump(config, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.chmod(tmp_path, 0o600)
    tmp_path.replace(CONFIG_PATH)
    os.chmod(CONFIG_PATH, 0o600)


def queue_configured_jobs(trigger="scheduler", attempt_id=None):
    config = load_config()
    if not config.get("enabled", True):
        logger.info("[ZSXQ_SYSTEM_SYNC_TEST] skipped disabled config")
        return {"queued": [], "skipped": True, "reason": "disabled"}

    group_id = str(config.get("group_id") or "").strip()
    if not group_id:
        raise ValueError("系统计划测试配置缺少 group_id")

    jobs = _normalize_jobs(config.get("jobs"))
    if not jobs:
        raise ValueError("系统计划测试配置缺少 jobs")

    max_rounds = _normalize_max_rounds(config.get("max_rounds"), default=DEFAULT_MAX_ROUNDS, none_means_continuous=True)
    rounds_completed = _bounded_int(config.get("rounds_completed"), default=0, lower=0, upper=1_000_000)
    if max_rounds is not None and rounds_completed >= max_rounds:
        logger.info(
            "[ZSXQ_SYSTEM_SYNC_TEST] skipped max rounds reached | rounds_completed=%s max_rounds=%s",
            rounds_completed,
            max_rounds,
        )
        return {
            "queued": [],
            "skipped": True,
            "reason": "max_rounds_reached",
            "config": _snapshot_config(config),
        }

    pending_work = _pending_system_sync_work(group_id)
    if pending_work.get("pending"):
        logger.info(
            "[ZSXQ_SYSTEM_SYNC_TEST] skipped previous work pending | group_id=%s queue_length=%s run_id=%s status=%s",
            group_id,
            pending_work.get("queue_length"),
            pending_work.get("run_id") or "-",
            pending_work.get("run_status") or "-",
        )
        return {
            "queued": [],
            "skipped": True,
            "reason": "previous_run_pending",
            "pending": pending_work,
            "config": _snapshot_config(config),
        }

    try:
        cookie = runtime_cookie()
        _validate_group_id(group_id, cookie=cookie)
    except ValueError as exc:
        if _is_placeholder_group_id(group_id):
            config["enabled"] = False
            _write_config(config)
        return {
            "queued": [],
            "skipped": True,
            "reason": "invalid_group",
            "error": str(exc),
            "config": _snapshot_config(config),
        }

    from tasks.zsxq_sync import run_zsxq_sync

    batch_round = rounds_completed + 1
    queued_at = datetime.utcnow().isoformat() + "Z"
    batch_id = attempt_id or f"{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}"
    stamp = datetime.utcnow().strftime("%H%M%S")
    mode = f"sys{batch_round}_mix_{stamp}"
    tabs = [job["tab"] for job in jobs]
    download_file_types_by_tab = {
        job["tab"]["id"]: [job["file_type"]]
        for job in jobs
    }
    max_download_files = _bounded_int(
        config.get("max_download_files"),
        default=DEFAULT_MAX_DOWNLOAD_FILES,
        lower=1,
        upper=10,
    )
    batch_meta = {
        "batch_id": batch_id,
        "attempt_id": batch_id,
        "round": batch_round,
        "max_rounds": max_rounds,
        "continuous": max_rounds is None,
        "trigger": trigger,
        "queued_at": queued_at,
        "max_download_files": max_download_files,
        "auto_pdf_clean": bool(config.get("auto_pdf_clean", True)),
        "jobs": [
            {
                "key": job["key"],
                "tab_label": job["tab"].get("label"),
                "tab_id": job["tab"].get("id"),
                "file_type": job["file_type"],
            }
            for job in jobs
        ],
    }
    queued = []
    run = None
    queue_error = None
    try:
        run = zsxq_sync_service.create_sync_run(
            group_id=group_id,
            group_name=config.get("group_name"),
            tabs=tabs,
            mode=mode,
            initial_result={
                "queued": True,
                "system_sync": batch_meta,
            },
        )
        payload = {
            "cookie": cookie,
            "group_id": group_id,
            "group_name": config.get("group_name"),
            "tabs": tabs,
            "count": config.get("count", DEFAULT_COUNT),
            "max_pages": config.get("max_pages", 1),
            "stop_on_existing": config.get("stop_on_existing", True),
            "download_attachments": config.get("download_attachments", True),
            "download_pdf_only": False,
            "download_file_types": ["all"],
            "download_file_types_by_tab": download_file_types_by_tab,
            "max_download_files": max_download_files,
            "mode": mode,
            "trigger": trigger,
            "sync_meta": batch_meta,
            "auto_pdf_clean": bool(config.get("auto_pdf_clean", True)),
            "pdf_cleaner": config.get("pdf_cleaner") or {},
        }
        async_result = run_zsxq_sync.apply_async(
            args=[run["id"], payload],
            queue="zsxq_sync",
            retry=False,
        )
        queued = [{
            "batch_id": batch_id,
            "round": batch_round,
            "max_rounds": max_rounds,
            "run_id": run["id"],
            "celery_task_id": async_result.id,
            "mode": mode,
            "jobs": batch_meta["jobs"],
            "max_download_files": max_download_files,
        }]
    except Exception as exc:
        queue_error = str(exc)
        logger.exception(
            "[ZSXQ_SYSTEM_SYNC_TEST] queue failed | batch_id=%s round=%s run_id=%s error=%s",
            batch_id,
            batch_round,
            run["id"] if isinstance(run, dict) and run.get("id") else "-",
            queue_error,
        )

    logger.info(
        "[ZSXQ_SYSTEM_SYNC_TEST] queued batch | batch_id=%s round=%s/%s run_id=%s queue_error=%s jobs=%s trigger=%s max_download_files=%s",
        batch_id,
        batch_round,
        max_rounds if max_rounds is not None else "continuous",
        run.get("id") if isinstance(run, dict) and run.get("id") else "-",
        bool(queue_error),
        batch_meta["jobs"],
        trigger,
        max_download_files,
    )

    config["rounds_completed"] = batch_round
    config["max_rounds"] = max_rounds
    config["max_download_files"] = max_download_files
    config["last_batch_id"] = batch_id
    config["last_trigger_at"] = queued_at
    if isinstance(run, dict):
        config["last_queued_run_id"] = run["id"]
    config["updated_at"] = queued_at
    _write_config(config)

    return {
        "queued": queued,
        "skipped": False,
        "batch_id": batch_id,
        "round": batch_round,
        "max_rounds": max_rounds,
        "testing_complete": False if max_rounds is None else batch_round >= max_rounds,
        "reason": "queue_failed" if queue_error else None,
        "error": queue_error,
        "config": _snapshot_config(config),
    }


def _pending_system_sync_work(group_id):
    queue_length = None
    queue_error = None
    try:
        client = redis.Redis.from_url(
            celery_app.conf.broker_url,
            socket_connect_timeout=2,
            socket_timeout=2,
            retry_on_timeout=False,
        )
        queue_length = int(client.llen("zsxq_sync"))
    except Exception as exc:
        queue_error = str(exc)

    pending_run = (
        ZsxqSyncRun.query
        .filter(
            ZsxqSyncRun.group_id == str(group_id),
            ZsxqSyncRun.mode.like("sys%"),
            ZsxqSyncRun.status.in_(("queued", "running")),
        )
        .order_by(ZsxqSyncRun.created_at.desc())
        .first()
    )

    return {
        "pending": bool(pending_run) or bool(queue_length and queue_length > 0),
        "queue_length": queue_length,
        "queue_error": queue_error,
        "run_id": pending_run.id if pending_run else None,
        "run_status": pending_run.status if pending_run else None,
        "run_updated_at": pending_run.updated_at.isoformat() if pending_run and pending_run.updated_at else None,
    }


def runtime_cookie():
    override = _strip_wrapped_quotes((os.environ.get(COOKIE_OVERRIDE_ENV_NAME) or "").strip())
    if override:
        return zsxq_service.normalize_cookie(override)

    cookie = _strip_wrapped_quotes((os.environ.get(COOKIE_ENV_NAME) or "").strip())
    if not cookie:
        raise ValueError(f"环境变量 {COOKIE_ENV_NAME} 未配置，系统计划任务无法访问知识星球")
    return zsxq_service.normalize_cookie(cookie)


def _strip_wrapped_quotes(value):
    if not isinstance(value, str):
        return value

    value = value.strip()
    if len(value) >= 2 and ((value[0] == value[-1]) and value[0] in {"'", '"'}):
        value = value[1:-1].strip()
    return value


def _validate_group_id(group_id, cookie=None):
    group_id = str(group_id or "").strip()
    if not group_id:
        raise ValueError("group_id 不能为空")

    if GROUP_ID_PLACEHOLDER_RE.match(group_id):
        raise ValueError(f"检测到占位 group_id：{group_id}。请在页面重新选择实际的星球。")

    if not cookie:
        return

    try:
        result = zsxq_service.get_menus(cookie, group_id)
    except Exception as exc:
        raise ValueError(f"读取星球菜单失败（group_id={group_id}）：{exc}") from exc

    if not isinstance(result, dict):
        raise ValueError(f"群组配置返回格式异常（group_id={group_id}）。")

    if result.get("succeeded") is False:
        code = result.get("code") or "UNKNOWN"
        detail = (
            (result.get("resp_data") or {}).get("message")
            or result.get("message")
            or result.get("error")
            or result
        )
        raise ValueError(f"知识星球返回失败 code={code}：{detail}（group_id={group_id}）")


def _is_placeholder_group_id(group_id):
    return bool(GROUP_ID_PLACEHOLDER_RE.match(str(group_id or "").strip()))


def _validate_group_id_local(group_id):
    try:
        _validate_group_id(str(group_id or "").strip())
        return None
    except ValueError as exc:
        return str(exc)


def _normalize_jobs(jobs):
    normalized = []
    for item in jobs or []:
        if not isinstance(item, dict):
            continue
        tab = item.get("tab")
        if not isinstance(tab, dict) or not tab.get("id") or not tab.get("label"):
            continue
        file_type = str(item.get("file_type") or "").strip().lower()
        if file_type not in {"pdf", "mp3", "all"}:
            continue
        mode_code = str(item.get("mode_code") or file_type[:2] or "x").strip().lower()
        mode_code = "".join(ch for ch in mode_code if ch.isalnum())[:4] or file_type[:2]
        normalized.append({
            "key": str(item.get("key") or f"{tab['id']}:{file_type}")[:80],
            "mode_code": mode_code,
            "file_type": file_type,
            "tab": {
                "id": str(tab.get("id")),
                "label": str(tab.get("label")),
                "scope": str(tab.get("scope") or "all"),
                **({"hashtagId": str(tab.get("hashtagId"))} if tab.get("hashtagId") else {}),
            },
        })
    return normalized


def _bounded_int(value, default, lower, upper):
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return min(max(number, lower), upper)


def _normalize_max_rounds(value, default, none_means_continuous=False):
    if value is None:
        return None if none_means_continuous else default
    if value in ("", 0, "0", False):
        return None if value in (0, "0", False) else default
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    if number <= 0:
        return None
    return min(number, 100)


def _normalize_interval_minutes(value, default=ENV_INTERVAL_MINUTES):
    if value is None or str(value).strip() == "":
        return default

    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    if number <= 0:
        return default
    return min(max(number, 1), 24 * 60)


def _scheduler_interval_minutes(interval_override=None):
    if interval_override is not None:
        return _normalize_interval_minutes(interval_override, default=ENV_INTERVAL_MINUTES)

    try:
        value = int(os.environ.get("ZSXQ_SYNC_TEST_INTERVAL_MINUTES", str(ENV_INTERVAL_MINUTES)))
    except ValueError:
        value = ENV_INTERVAL_MINUTES
    return min(max(value, 1), 24 * 60)
