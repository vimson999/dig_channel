import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from services.zsxq_auto_publish_service import (
    CONFIG_PATH,
    COOKIE_ENV_NAME,
    COOKIE_OVERRIDE_ENV_NAME,
    DEFAULT_INTERVAL_MINUTES,
    config_status,
)


PROJECT_ROOT = Path(__file__).resolve().parents[3]
RUNTIME_DIR = PROJECT_ROOT / "runtime"
PID_PATH = RUNTIME_DIR / "zsxq_auto_publish_scheduler.pid"
LOG_PATH = PROJECT_ROOT / "logs" / "zsxq_auto_publish.log"


def _float_env(name, default):
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return float(default)


STOP_GRACE_SECONDS = max(1.0, _float_env("ZSXQ_AUTO_PUBLISH_SCHEDULER_STOP_GRACE_SECONDS", 3))


def _scheduler_env(extra=None):
    env = dict(os.environ)
    env["DB_PROCESS_ROLE"] = "scheduler"
    env.setdefault("DATABASE_TYPE", "postgresql")
    env.setdefault("DB_POOL_SIZE", "1")
    env.setdefault("DB_MAX_OVERFLOW", "0")
    env.setdefault("DB_CONNECT_TIMEOUT", "5")
    env.setdefault("SCHEDULER_DB_POOL_SIZE", "1")
    env.setdefault("SCHEDULER_DB_MAX_OVERFLOW", "0")
    env.setdefault("PYTHONPATH", str(PROJECT_ROOT / "src" / "backend"))
    env.setdefault("FLASK_DEBUG", "0")
    env.setdefault("FLASK_USE_RELOADER", "0")
    env.setdefault("PYTHONUNBUFFERED", "1")
    if extra:
        env.update(extra)
    return env


def _write_pid_file(metadata):
    PID_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = PID_PATH.with_suffix(".tmp")
    with tmp_path.open("w", encoding="utf-8") as fh:
        json.dump(metadata, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.chmod(tmp_path, 0o600)
    tmp_path.replace(PID_PATH)
    os.chmod(PID_PATH, 0o600)


def _read_pid_file():
    if not PID_PATH.exists():
        return {}
    try:
        with PID_PATH.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def _safe_unlink(path):
    try:
        path.unlink(missing_ok=True)
    except Exception:
        return


def _is_pid_running(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except PermissionError:
        return True
    except (ProcessLookupError, ValueError, TypeError):
        return False
    except OSError:
        return False


def _terminate_process(pid):
    try:
        os.kill(int(pid), signal.SIGTERM)
    except Exception:
        return


def _kill_process(pid):
    try:
        os.kill(int(pid), signal.SIGKILL)
    except Exception:
        return


def start_scheduler(cookie_override=None):
    config = config_status()
    if not config.get("exists"):
        return {
            "started": False,
            "running": False,
            "message": f"未找到自动发帖配置，请先保存策略。配置路径：{CONFIG_PATH}",
        }
    has_db_cookie = bool(config.get("cookie_db_configured"))
    has_cookie_override = bool((cookie_override or "").strip()) and not has_db_cookie
    has_cookie_pool = bool(config.get("cookie_pool_ready"))
    if not has_cookie_pool and not config.get("cookie_env_configured") and not has_cookie_override:
        return {
            "started": False,
            "running": False,
            "message": "未找到可用的知识星球发帖凭据，请先在平台凭据管理里保存 zsxq.publish Cookie。",
            "scheduler": scheduler_status(),
        }

    current = scheduler_status()
    if current.get("running"):
        return {
            "started": False,
            "running": True,
            "message": "自动发帖调度器已在运行。",
            "scheduler": current,
        }

    command = [
        sys.executable,
        str(PROJECT_ROOT / "src" / "backend" / "run_zsxq_auto_publish_scheduler.py"),
        "start",
    ]
    extra_env = {}
    if has_cookie_override:
        extra_env[COOKIE_OVERRIDE_ENV_NAME] = cookie_override.strip()
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    log_file = LOG_PATH.open("a", encoding="utf-8")
    try:
        process = subprocess.Popen(
            command,
            cwd=str(PROJECT_ROOT),
            env=_scheduler_env(extra_env),
            stdout=log_file,
            stderr=log_file,
            start_new_session=True,
            close_fds=True,
        )
    finally:
        log_file.close()
    started_at = datetime.utcnow().replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
    _write_pid_file({
        "pid": process.pid,
        "started_at": started_at,
        "command": " ".join(command),
        "log_path": str(LOG_PATH),
        "cookie_source": "db" if has_db_cookie else ("pool" if has_cookie_pool else ("page" if has_cookie_override else "env")),
    })
    return {
        "started": True,
        "running": True,
        "message": (
            "自动发帖调度器已启动，当前使用 DB 平台凭据。"
            if has_db_cookie
            else ("自动发帖调度器已启动，当前优先使用策略账号池。"
            if has_cookie_pool
            else ("自动发帖调度器已启动，当前使用页面里选择的 Cookie。" if has_cookie_override else "自动发帖调度器已启动。")
            )
        ),
        "scheduler": scheduler_status(),
    }


def stop_scheduler():
    status = scheduler_status()
    if not status.get("running"):
        return {
            "stopped": True,
            "running": False,
            "message": "自动发帖调度器未运行。",
        }
    pid = status.get("pid")
    if pid:
        _terminate_process(pid)
    deadline = time.time() + STOP_GRACE_SECONDS
    while time.time() < deadline:
        if not _is_pid_running(pid):
            break
        time.sleep(0.25)
    if pid and _is_pid_running(pid):
        _kill_process(pid)
        time.sleep(0.5)
    still_running = bool(pid and _is_pid_running(pid))
    if still_running:
        return {
            "stopped": False,
            "running": True,
            "message": "自动发帖调度器停止超时，请手动检查进程状态。",
        }
    _safe_unlink(PID_PATH)
    return {
        "stopped": True,
        "running": False,
        "message": "自动发帖调度器已停止。",
    }


def restart_scheduler(cookie_override=None):
    stop_result = stop_scheduler()
    if stop_result.get("running"):
        return {
            "restarted": False,
            "running": True,
            "message": "自动发帖调度器尚未完全停止，暂未启动新进程。",
            "stop": stop_result,
            "scheduler": scheduler_status(),
        }

    time.sleep(0.5)
    start_result = start_scheduler(cookie_override=cookie_override)
    return {
        "restarted": bool(start_result.get("running")),
        "running": bool(start_result.get("running")),
        "message": "自动发帖调度器已重启。" if start_result.get("running") else start_result.get("message"),
        "stop": stop_result,
        "start": start_result,
        "scheduler": start_result.get("scheduler") or scheduler_status(),
    }


def trigger_once(cookie_override=None):
    config = config_status()
    if not config.get("exists"):
        return {
            "queued": False,
            "skipped": True,
            "reason": "missing_config",
            "message": "未找到自动发帖配置，请先保存策略。",
        }
    has_db_cookie = bool(config.get("cookie_db_configured"))
    has_cookie_pool = bool(config.get("cookie_pool_ready"))
    effective_cookie_override = cookie_override if not has_db_cookie else None
    if not has_cookie_pool and not effective_cookie_override and not config.get("cookie_env_configured"):
        return {
            "queued": False,
            "skipped": True,
            "reason": "missing_cookie",
            "message": "未找到可用的知识星球发帖凭据，请先在平台凭据管理里保存 zsxq.publish Cookie。",
        }

    extra_env = {}
    if effective_cookie_override:
        extra_env[COOKIE_OVERRIDE_ENV_NAME] = effective_cookie_override
    command = [
        sys.executable,
        str(PROJECT_ROOT / "src" / "backend" / "run_zsxq_auto_publish_scheduler.py"),
        "once",
    ]
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    log_file = LOG_PATH.open("a", encoding="utf-8")
    try:
        process = subprocess.Popen(
            command,
            cwd=str(PROJECT_ROOT),
            env=_scheduler_env(extra_env),
            stdout=log_file,
            stderr=log_file,
            start_new_session=True,
            close_fds=True,
        )
    finally:
        log_file.close()
    return {
        "queued": True,
        "running": False,
        "message": "已提交一次自动发帖试跑，请稍后刷新历史记录。",
        "pid": process.pid,
    }


def scheduler_log_tail(lines=120):
    if not LOG_PATH.exists():
        return []
    try:
        data = LOG_PATH.read_bytes()
    except Exception as exc:
        return [f"读取日志失败: {exc}"]
    return [
        line.decode("utf-8", errors="replace")
        for line in data.splitlines()[-int(max(lines, 1)):]
    ]


def scheduler_status():
    pid_info = _read_pid_file()
    pid = pid_info.get("pid")
    running = bool(pid and _is_pid_running(pid))
    if pid and not running:
        _safe_unlink(PID_PATH)
        pid = None
    config = config_status()
    interval = (config or {}).get("interval_minutes") or DEFAULT_INTERVAL_MINUTES
    started_at = pid_info.get("started_at")
    next_run = None
    if running and started_at:
        try:
            started = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
            next_run = (datetime.now(timezone.utc) + timedelta(minutes=int(interval))).isoformat().replace("+00:00", "Z")
        except Exception:
            next_run = None
    cookie_source = config.get("cookie_source_mode")
    if not cookie_source:
        cookie_source = (
            "db"
            if config.get("cookie_db_configured")
            else ("pool" if config.get("cookie_pool_ready") else ("env" if config.get("cookie_env_configured") else None))
        )
    if not cookie_source:
        cookie_source = pid_info.get("cookie_source")
    return {
        "running": running,
        "pid": pid,
        "started_at": started_at,
        "interval_minutes": interval,
        "next_run": next_run,
        "log_path": str(LOG_PATH),
        "cookie_source": cookie_source,
        "cookie_override_active": cookie_source == "page",
        "config": config,
        "message": "运行中" if running else "未运行",
    }
