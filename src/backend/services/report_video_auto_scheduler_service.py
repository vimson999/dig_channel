import os
import signal
import subprocess
import sys
import time
import json
from datetime import datetime, timezone
from pathlib import Path

from services.report_video_auto_service import AUTO_CONFIG_PATH, load_auto_scan_config


PROJECT_ROOT = Path(__file__).resolve().parents[3]
RUNTIME_DIR = PROJECT_ROOT / "runtime"
PID_PATH = RUNTIME_DIR / "report_video_auto_scheduler.pid"
LOG_PATH = PROJECT_ROOT / "logs" / "report_video_auto_scheduler.log"


def scheduler_status():
    pid_info = _read_pid_file()
    pid = pid_info.get("pid") if isinstance(pid_info, dict) else None
    running = bool(pid and _is_pid_running(pid))
    if pid and not running:
        _safe_unlink(PID_PATH)
        pid = None

    config = load_auto_scan_config()
    return {
        "exists": bool(AUTO_CONFIG_PATH.exists() or running),
        "running": running,
        "pid": int(pid) if pid else None,
        "started_at": pid_info.get("started_at") if isinstance(pid_info, dict) else None,
        "enabled": bool(config.get("enabled")),
        "schedule_time": config.get("schedule_time"),
        "interval_minutes": config.get("interval_minutes"),
        "log_path": str(LOG_PATH),
        "config": config,
        "message": "运行中" if running else "未运行",
    }


def start_scheduler():
    config = load_auto_scan_config()
    if not config.get("enabled"):
        return {
            "started": False,
            "running": False,
            "message": "研报自动扫描计划已禁用，请先启用并保存配置。",
            "scheduler": scheduler_status(),
        }

    current = scheduler_status()
    if current.get("running"):
        return {
            "started": False,
            "running": True,
            "message": "研报自动扫描计划已在运行。",
            "scheduler": current,
        }

    command = [
        sys.executable,
        str(PROJECT_ROOT / "src" / "backend" / "run_report_video_auto_scheduler.py"),
        "start",
    ]
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen(
        command,
        cwd=str(PROJECT_ROOT),
        env=_scheduler_env(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )

    started_at = _utc_now_iso()
    _write_pid_file({
        "pid": process.pid,
        "started_at": started_at,
        "command": " ".join(command),
        "log_path": str(LOG_PATH),
    })
    time.sleep(0.6)
    if process.poll() is not None or not _is_pid_running(process.pid):
        _safe_unlink(PID_PATH)
        return {
            "started": False,
            "running": False,
            "message": "研报自动扫描计划启动后立即退出，请查看最近日志。",
            "scheduler": scheduler_status(),
        }

    return {
        "started": True,
        "running": True,
        "message": "研报自动扫描计划已启动。",
        "scheduler": scheduler_status(),
    }


def stop_scheduler():
    status = scheduler_status()
    if not status.get("running"):
        return {
            "stopped": True,
            "running": False,
            "already_stopped": True,
            "message": "研报自动扫描计划未运行。",
            "scheduler": status,
        }

    pid = status.get("pid")
    if not pid:
        return {
            "stopped": False,
            "running": False,
            "message": "缺少计划任务 PID，无法停止。",
            "scheduler": status,
        }

    _terminate_process(pid)
    deadline = time.time() + 10
    while time.time() < deadline:
        if not _is_pid_running(pid):
            break
        time.sleep(0.25)
    if _is_pid_running(pid):
        _kill_process(pid)
        time.sleep(0.5)

    still_running = _is_pid_running(pid)
    if not still_running:
        _safe_unlink(PID_PATH)
    return {
        "stopped": not still_running,
        "running": still_running,
        "message": "研报自动扫描计划已停止。" if not still_running else "停止超时，请手动检查进程。",
        "scheduler": scheduler_status(),
    }


def restart_scheduler():
    stop_result = stop_scheduler()
    if stop_result.get("running"):
        return {
            "restarted": False,
            "running": True,
            "message": "研报自动扫描计划尚未完全停止，暂未启动新进程。",
            "stop": stop_result,
            "scheduler": scheduler_status(),
        }

    time.sleep(0.5)
    start_result = start_scheduler()
    return {
        "restarted": bool(start_result.get("running")),
        "running": bool(start_result.get("running")),
        "message": "研报自动扫描计划已重启。" if start_result.get("running") else start_result.get("message"),
        "stop": stop_result,
        "start": start_result,
        "scheduler": start_result.get("scheduler") or scheduler_status(),
    }


def trigger_once():
    process = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "src" / "backend" / "run_report_video_auto_scheduler.py"),
            "once",
        ],
        cwd=str(PROJECT_ROOT),
        env=_scheduler_env(),
        capture_output=True,
        text=True,
        timeout=600,
    )
    if process.returncode != 0:
        raise RuntimeError(
            f"立即执行研报自动扫描失败：{(process.stderr or process.stdout or '').strip() or '未知错误'}"
        )

    stdout_lines = [line.strip() for line in (process.stdout or "").splitlines() if line.strip()]
    stderr_lines = [line.strip() for line in (process.stderr or "").splitlines() if line.strip()]
    return {
        "triggered": True,
        "queued": None,
        "running": False,
        "message": "已触发一次研报自动扫描；是否投递任务请查看日志或任务列表。",
        "stdout": stdout_lines[-20:],
        "stderr": stderr_lines[-20:],
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


def _scheduler_env(extra=None):
    env = dict(os.environ)
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


def _utc_now_iso():
    return datetime.utcnow().replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
