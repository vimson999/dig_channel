import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from services.zsxq_system_sync_test import (
    COOKIE_ENV_NAME,
    COOKIE_OVERRIDE_ENV_NAME,
    config_status,
    transient_validation_retry_timeout_seconds,
)


PROJECT_ROOT = Path(__file__).resolve().parents[3]
RUNTIME_DIR = PROJECT_ROOT / "runtime"
PID_PATH = RUNTIME_DIR / "zsxq_auto_scheduler.pid"
LOG_PATH = PROJECT_ROOT / "logs" / "zsxq_auto_scheduler.log"
LOCK_TTL_SECONDS = 8


def start_scheduler(cookie_override=None):
    status = config_status()
    try:
        from services.zsxq_all_tabs_auto_sync_service import all_tabs_auto_sync_enabled

        all_tabs_enabled = all_tabs_auto_sync_enabled()
    except Exception:
        all_tabs_enabled = False
    if not status.get("exists") and not all_tabs_enabled:
        return {
            "started": False,
            "running": False,
            "message": "未找到系统计划配置，请先保存系统计划配置。",
        }
    if status.get("group_validation_error") and not all_tabs_enabled:
        return {
            "started": False,
            "running": False,
            "message": f"系统计划配置无效：{status.get('group_validation_error')}",
            "scheduler": status,
        }

    has_db_cookie = bool(status.get("cookie_db_configured"))
    has_cookie_override = bool((cookie_override or "").strip()) and not has_db_cookie
    if not status.get("cookie_env_configured") and not has_cookie_override:
        return {
            "started": False,
            "running": False,
            "message": "未找到可用的知识星球同步凭据，请先在平台凭据管理里保存 zsxq.sync Cookie。",
            "scheduler": scheduler_status(),
        }

    running_status = scheduler_status()
    if running_status.get("running"):
        return {
            "started": False,
            "running": True,
            "message": "计划任务已在运行。",
            "scheduler": running_status,
        }

    command = [
        sys.executable,
        str(PROJECT_ROOT / "src" / "backend" / "run_zsxq_auto_scheduler.py"),
        "start",
    ]
    extra_env = {}
    if has_cookie_override:
        extra_env[COOKIE_OVERRIDE_ENV_NAME] = cookie_override.strip()
    env = _scheduler_env(extra=extra_env)

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen(
        command,
        cwd=str(PROJECT_ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )

    if process.poll() is not None:
        raise RuntimeError("调度器进程启动后立即退出，请查看日志确认错误。")

    started_at = datetime.utcnow().replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
    metadata = {
        "pid": process.pid,
        "started_at": started_at,
        "command": " ".join(command),
        "log_path": str(LOG_PATH),
        "log_file_descriptor_opened": True,
        "cookie_source": "db" if has_db_cookie else ("page" if has_cookie_override else "env"),
    }
    _write_pid_file(metadata)

    time.sleep(0.8)
    if process.poll() is not None or not _is_pid_running(process.pid):
        _safe_unlink(PID_PATH)
        return {
            "started": False,
            "running": False,
            "message": "计划任务启动后立即退出，请查看最近日志确认原因。",
            "scheduler": scheduler_status(),
            "log_path": str(LOG_PATH),
        }

    return {
        "started": True,
        "running": True,
        "message": (
            "计划任务已启动；生财有术/洪灝全部 Tab 将每 3 小时同步。"
            if all_tabs_enabled
            else "计划任务已启动，当前使用 DB 平台凭据。"
        ) if has_db_cookie else (
            "计划任务已启动，按配置每隔一定间隔执行。" if not has_cookie_override else "计划任务已启动，当前使用页面里选择的 Cookie。"
        ),
        "scheduler": scheduler_status(),
    }


def stop_scheduler():
    running_status = scheduler_status()
    if not running_status.get("running"):
        return {
            "stopped": True,
            "running": False,
            "already_stopped": True,
            "message": "计划任务未运行。",
        }

    pid = running_status.get("pid")
    started_at = running_status.get("started_at")
    if not pid:
        return {
            "stopped": False,
            "running": False,
            "message": "缺少计划任务 PID，无法执行停止操作。",
        }

    stopped = False
    final_status = {"running": True}
    timeout_seconds = max(5, LOCK_TTL_SECONDS * 2)

    _terminate_process(pid)
    deadline = time.time() + timeout_seconds
    poll_interval = 0.25

    while time.time() < deadline:
        final_status = scheduler_status()
        if not final_status.get("running") or not _is_pid_running(pid):
            stopped = True
            break
        time.sleep(poll_interval)

    if stopped:
        _safe_unlink(PID_PATH)
        return {
            "stopped": True,
            "running": False,
            "message": "计划任务已停止。",
            "pid": pid,
            "started_at": started_at,
        }

    return {
        "stopped": False,
        "running": bool(final_status.get("running", True)),
        "message": "已发送停止信号，计划任务正在关闭中。",
        "pid": pid,
        "started_at": started_at,
        "retry_after_ms": 1000,
    }


def restart_scheduler(cookie_override=None):
    stop_result = stop_scheduler()
    if stop_result.get("running"):
        return {
            "restarted": False,
            "running": True,
            "message": "计划任务尚未完全停止，暂未启动新进程。",
            "stop": stop_result,
            "scheduler": scheduler_status(),
        }

    time.sleep(0.5)
    start_result = start_scheduler(cookie_override=cookie_override)
    return {
        "restarted": bool(start_result.get("running")),
        "running": bool(start_result.get("running")),
        "message": "计划任务已重启。" if start_result.get("running") else start_result.get("message"),
        "stop": stop_result,
        "start": start_result,
        "scheduler": start_result.get("scheduler") or scheduler_status(),
    }


def scheduler_log_path():
    info = _read_pid_file()
    log_path = info.get("log_path") if isinstance(info, dict) else None
    if log_path and Path(log_path).exists():
        return log_path
    if LOG_PATH.exists():
        return str(LOG_PATH)
    if RUNTIME_DIR.exists():
        return str(RUNTIME_DIR / "zsxq_auto_scheduler.log")
    return str(LOG_PATH)


def scheduler_log_tail(lines=120):
    path = Path(scheduler_log_path())
    if not path.exists():
        return []
    try:
        with path.open("rb") as file:
            data = file.read()
    except OSError as exc:
        return [f"读取日志失败: {exc}"]
    return [
        item.decode("utf-8", errors="replace")
        for item in data.splitlines()[-int(max(lines, 1)):]
    ]


def trigger_once(cookie_override=None):
    config = config_status()
    if not config.get("exists"):
        return {
            "queued": False,
            "skipped": True,
            "reason": "missing_config",
            "message": "未找到系统计划配置，请先保存配置。",
            "config": config,
        }
    has_db_cookie = bool(config.get("cookie_db_configured"))
    effective_cookie_override = cookie_override if not has_db_cookie else None
    if not effective_cookie_override and not config.get("cookie_env_configured"):
        return {
            "queued": False,
            "skipped": True,
            "reason": "missing_cookie_env",
            "message": "未找到可用的知识星球同步凭据，请先在平台凭据管理里保存 zsxq.sync Cookie。",
            "config": config,
        }

    extra_env = {}
    if effective_cookie_override:
        extra_env[COOKIE_OVERRIDE_ENV_NAME] = effective_cookie_override.strip()

    process = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "src" / "backend" / "run_zsxq_auto_scheduler.py"),
            "once",
        ],
        cwd=str(PROJECT_ROOT),
        env=_scheduler_env(extra=extra_env),
        capture_output=True,
        text=True,
        timeout=max(60, transient_validation_retry_timeout_seconds()),
    )
    if process.returncode != 0:
        raise RuntimeError(
            f"立即触发失败：{(process.stderr or process.stdout or '').strip() or '未知错误'}"
        )

    stdout = (process.stdout or "").strip()
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]

    payload = None
    if lines:
        last_json_error = None
        for line in reversed(lines):
            try:
                payload = json.loads(line)
                break
            except Exception as exc:
                last_json_error = exc
        if payload is None:
            if last_json_error:
                raise RuntimeError(f"立即触发结果解析失败：{last_json_error}")
            raise RuntimeError("立即触发结果解析失败：未识别到结构化返回")

    if payload is None:
        raise RuntimeError("未返回任何触发结果")

    if not isinstance(payload, dict):
        raise RuntimeError(f"立即触发结果不合法：{payload}")
    if not payload:
        raise RuntimeError(f"立即触发结果为空：{stdout}")

    queued = bool(payload.get("queued"))
    skipped = bool(payload.get("skipped"))
    reason = payload.get("reason")
    message = payload.get("message") or (
        "已提交本次同步任务（立即执行）。" if queued else "本次触发未提交。"
    )
    if reason == "error":
        raise RuntimeError(message or "立即触发失败。")

    if skipped and reason in {"missing_config", "missing_cookie_env", "disabled"}:
        return {
            "queued": False,
            "skipped": True,
            "reason": reason,
            "message": message,
            "config": payload.get("config"),
        }

    return {
        "queued": queued,
        "skipped": skipped,
        "reason": reason,
        "message": message,
        "batch_id": payload.get("batch_id"),
        "round": payload.get("round"),
        "max_rounds": payload.get("max_rounds"),
        "testing_complete": bool(payload.get("testing_complete")),
        "config": payload.get("config"),
        "queued_runs": payload.get("queued_runs", []),
    }


def scheduler_status():
    pid_info = _read_pid_file()
    pid = pid_info.get("pid") if isinstance(pid_info, dict) else None
    running, alive = False, False
    if pid:
        alive = _is_pid_running(int(pid))
        if alive:
            running = True
        else:
            _safe_unlink(PID_PATH)
            pid = None

    discovered = None
    if not pid:
        discovered = _discover_scheduler_process()
        if discovered:
            pid = discovered.get("pid")
            alive = True
            running = True

    config = config_status()
    started_at = pid_info.get("started_at") if isinstance(pid_info, dict) else None
    if not started_at and discovered:
        started_at = discovered.get("started_at")
    log_path = None
    if isinstance(pid_info, dict):
        log_path = pid_info.get("log_path")
    if not log_path and discovered:
        log_path = discovered.get("log_path")
    if not log_path:
        log_path = str(LOG_PATH)
    next_run = _guess_next_run(config, started_at)
    cookie_source = config.get("cookie_source_mode")
    if not cookie_source and isinstance(pid_info, dict):
        cookie_source = pid_info.get("cookie_source")

    try:
        from services.zsxq_all_tabs_auto_sync_service import all_tabs_auto_sync_status

        all_tabs_status = all_tabs_auto_sync_status()
    except Exception as exc:
        all_tabs_status = {
            "enabled": None,
            "error": str(exc),
        }

    return {
        "exists": bool(running or config.get("exists")),
        "running": running,
        "pid": int(pid) if pid else None,
        "started_at": started_at,
        "alive": alive,
        "log_path": log_path,
        "cookie_source": cookie_source,
        "cookie_override_active": cookie_source == "page",
        "next_run": next_run,
        "config": config,
        "interval_minutes": config.get("interval_minutes"),
        "last_trigger_at": config.get("last_trigger_at"),
        "rounds_completed": config.get("rounds_completed"),
        "testing_complete": config.get("testing_complete"),
        "continuous": config.get("continuous"),
        "max_rounds": config.get("max_rounds"),
        "all_tabs_auto_sync": all_tabs_status,
        "message": "运行中" if running else "未运行",
    }


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
    env.setdefault("ZSXQ_PROJECT_ROOT", str(PROJECT_ROOT))
    if extra:
        env.update(extra)
    return env


def _read_pid_file():
    if not PID_PATH.exists():
        return {}
    try:
        with PID_PATH.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict) or not data.get("pid"):
            return {}
        return data
    except (json.JSONDecodeError, OSError, ValueError):
        _safe_unlink(PID_PATH)
        return {}


def _write_pid_file(payload):
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    tmp_path = PID_PATH.with_suffix(".tmp")
    with tmp_path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.chmod(tmp_path, 0o600)
    tmp_path.replace(PID_PATH)


def _safe_unlink(path):
    try:
        path.unlink()
    except FileNotFoundError:
        return


def _is_pid_running(pid):
    try:
        import psutil
    except ImportError:
        psutil = None
    if psutil is not None:
        try:
            process = psutil.Process(int(pid))
            if process.status() == psutil.STATUS_ZOMBIE:
                return False
        except (psutil.NoSuchProcess, psutil.ZombieProcess, ValueError, TypeError):
            return False
        except psutil.AccessDenied:
            pass
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except (ProcessLookupError, ValueError, TypeError):
        return False
    except OSError:
        return False
    return True


def _discover_scheduler_process():
    script_path = str(PROJECT_ROOT / "src" / "backend" / "run_zsxq_auto_scheduler.py")
    try:
        import psutil

        for process in psutil.process_iter(["pid", "cmdline", "create_time"]):
            info = process.info or {}
            try:
                if process.status() == psutil.STATUS_ZOMBIE:
                    continue
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            cmdline = info.get("cmdline") or []
            if not cmdline:
                continue
            if script_path not in cmdline:
                continue
            if "start" not in cmdline:
                continue

            started_at = None
            try:
                started_at = (
                    datetime
                    .fromtimestamp(float(info.get("create_time")), tz=timezone.utc)
                    .isoformat()
                    .replace("+00:00", "Z")
                )
            except Exception:
                started_at = None

            return {
                "pid": int(info.get("pid") or process.pid),
                "started_at": started_at,
                "command": " ".join(str(item) for item in cmdline),
                "log_path": str(LOG_PATH),
            }
    except Exception:
        return None
    return None


def _terminate_process(pid):
    try:
        pgid = os.getpgid(pid)
    except OSError:
        pgid = pid

    try:
        os.killpg(pgid, signal.SIGTERM)
    except OSError:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            if _is_pid_running(pid):
                return False
            return True

    deadline = time.time() + LOCK_TTL_SECONDS
    while time.time() < deadline:
        if not _is_pid_running(pid):
            return True
        time.sleep(0.5)

    try:
        os.killpg(pgid, signal.SIGKILL)
    except OSError:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            return not _is_pid_running(pid)

    time.sleep(0.3)
    return not _is_pid_running(pid)


def _guess_next_run(config, started_at):
    interval = config.get("interval_minutes") if isinstance(config, dict) else None
    if not interval or not isinstance(interval, int):
        return None

    try:
        interval = int(interval)
    except (TypeError, ValueError):
        return None

    anchor = config.get("last_trigger_at") or started_at
    if not anchor:
        return None

    try:
        anchor_time = datetime.fromisoformat(str(anchor).replace("Z", "+00:00"))
    except ValueError:
        return None

    next_time = anchor_time + timedelta(minutes=interval)
    now = datetime.now(timezone.utc)
    if next_time <= now:
        next_time = now + timedelta(minutes=interval)

    return next_time.astimezone().replace(microsecond=0).isoformat()
