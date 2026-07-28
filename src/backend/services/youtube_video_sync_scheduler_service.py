"""Process controls for the daily YouTube video sync scheduler."""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
PID_PATH = PROJECT_ROOT / "runtime" / "youtube_video_sync_scheduler.pid"
LOG_PATH = PROJECT_ROOT / "logs" / "youtube_video_sync_scheduler.log"
RUNNER_PATH = PROJECT_ROOT / "src" / "backend" / "run_youtube_video_sync_scheduler.py"


def _read_pid():
    try:
        return json.loads(PID_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _is_running(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, TypeError, ValueError):
        return False


def scheduler_status():
    payload = _read_pid()
    pid = payload.get("pid")
    running = bool(pid and _is_running(pid))
    if pid and not running:
        PID_PATH.unlink(missing_ok=True)
    return {
        "running": running,
        "pid": int(pid) if running else None,
        "started_at": payload.get("started_at") if running else None,
        "log_path": str(LOG_PATH),
        "message": "运行中" if running else "未运行",
    }


def start_scheduler():
    current = scheduler_status()
    if current["running"]:
        return {"started": False, "running": True, "scheduler": current}
    env = dict(os.environ)
    env["DB_PROCESS_ROLE"] = "scheduler"
    env["BACKEND_PROCESS_BOOTSTRAP_ENABLED"] = "0"
    process = subprocess.Popen(
        [sys.executable, str(RUNNER_PATH), "start"],
        cwd=str(PROJECT_ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )
    deadline = time.time() + 8
    status = scheduler_status()
    while time.time() < deadline:
        if process.poll() is not None or not _is_running(process.pid):
            break
        status = scheduler_status()
        if status.get("running") and status.get("pid") == process.pid:
            break
        time.sleep(0.2)
    running = bool(
        process.poll() is None
        and status.get("running")
        and status.get("pid") == process.pid
    )
    return {
        "started": running,
        "running": running,
        "scheduler": status if running else scheduler_status(),
    }


def stop_scheduler():
    current = scheduler_status()
    pid = current.get("pid")
    if not pid:
        return {"stopped": True, "running": False, "scheduler": current}
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    deadline = time.time() + 10
    while time.time() < deadline and _is_running(pid):
        time.sleep(0.2)
    running = _is_running(pid)
    if not running:
        PID_PATH.unlink(missing_ok=True)
    return {
        "stopped": not running,
        "running": running,
        "scheduler": scheduler_status(),
    }


def restart_scheduler():
    stopped = stop_scheduler()
    if stopped.get("running"):
        return {"restarted": False, "running": True, "stop": stopped}
    started = start_scheduler()
    return {
        "restarted": bool(started.get("running")),
        "running": bool(started.get("running")),
        "stop": stopped,
        "start": started,
        "scheduler": started.get("scheduler"),
    }
