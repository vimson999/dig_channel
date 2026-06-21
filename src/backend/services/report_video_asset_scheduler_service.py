import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
RUNTIME_DIR = PROJECT_ROOT / "runtime"
CONFIG_PATH = RUNTIME_DIR / "report_video_asset_scan_scheduler_config.json"
PID_PATH = RUNTIME_DIR / "report_video_asset_scan_scheduler.pid"
LOG_PATH = PROJECT_ROOT / "logs" / "report_video_asset_scan_scheduler.log"


def default_config():
    return {
        "enabled": True,
        "schedule_time": "02:30",
        "interval_minutes": None,
        "run_on_start": False,
        "parse_pdfs": True,
        "target_date_offset_days": 1,
        "config": {
            "max_files": 50,
            "tab_label_keyword": "外资研报",
            "output_ratios": ["16:9"],
            "default_segment_count": 4,
            "style": "professional_finance",
            "generate_subtitles": True,
        },
        "last_run_at": None,
        "next_run_at": None,
        "last_result": None,
    }


def load_config():
    exists = CONFIG_PATH.exists()
    data = {}
    if exists:
        try:
            with CONFIG_PATH.open("r", encoding="utf-8") as fh:
                loaded = json.load(fh)
            if isinstance(loaded, dict):
                data = loaded
        except Exception:
            data = {}
    config = _normalize_config(data)
    config["exists"] = exists
    config["path"] = str(CONFIG_PATH)
    config["log_path"] = str(LOG_PATH)
    return config


def save_config(payload):
    existing = load_config()
    payload = payload if isinstance(payload, dict) else {}
    merged = {
        **existing,
        **payload,
        "config": {**(existing.get("config") or {}), **(payload.get("config") or {})},
        "last_run_at": existing.get("last_run_at"),
        "next_run_at": existing.get("next_run_at"),
        "last_result": existing.get("last_result"),
    }
    _write_config(_normalize_config(merged))
    return load_config()


def update_runtime_status(fields):
    current = load_config()
    for key, value in (fields or {}).items():
        if key in {"last_run_at", "next_run_at", "last_result"}:
            current[key] = value
    _write_config(current)
    return load_config()


def scheduler_status():
    pid_info = _read_pid_file()
    pid = pid_info.get("pid") if isinstance(pid_info, dict) else None
    running = bool(pid and _is_pid_running(pid))
    if pid and not running:
        _safe_unlink(PID_PATH)
        pid = None

    config = load_config()
    return {
        "exists": bool(config.get("exists") or running),
        "running": running,
        "pid": int(pid) if pid else None,
        "started_at": pid_info.get("started_at") if isinstance(pid_info, dict) else None,
        "schedule_time": config.get("schedule_time"),
        "interval_minutes": config.get("interval_minutes"),
        "next_run_at": config.get("next_run_at") if running else None,
        "last_run_at": config.get("last_run_at"),
        "last_result": config.get("last_result"),
        "log_path": str(LOG_PATH),
        "config": config,
        "message": "运行中" if running else "未运行",
    }


def start_scheduler():
    config = load_config()
    if not config.get("enabled", True):
        return {
            "started": False,
            "running": False,
            "message": "研报资产补漏计划已禁用，请先启用并保存配置。",
            "scheduler": scheduler_status(),
        }

    current = scheduler_status()
    if current.get("running"):
        return {
            "started": False,
            "running": True,
            "message": "研报资产补漏计划已在运行。",
            "scheduler": current,
        }

    command = [
        sys.executable,
        str(PROJECT_ROOT / "src" / "backend" / "run_report_video_asset_scheduler.py"),
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

    started_at = utc_now_iso()
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
            "message": "研报资产补漏计划启动后立即退出，请查看最近日志。",
            "scheduler": scheduler_status(),
        }

    return {
        "started": True,
        "running": True,
        "message": "研报资产补漏计划已启动。",
        "scheduler": scheduler_status(),
    }


def stop_scheduler():
    status = scheduler_status()
    if not status.get("running"):
        return {
            "stopped": True,
            "running": False,
            "already_stopped": True,
            "message": "研报资产补漏计划未运行。",
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
        update_runtime_status({"next_run_at": None})
    return {
        "stopped": not still_running,
        "running": still_running,
        "message": "研报资产补漏计划已停止。" if not still_running else "停止超时，请手动检查进程。",
        "scheduler": scheduler_status(),
    }


def restart_scheduler():
    stop_result = stop_scheduler()
    if stop_result.get("running"):
        return {
            "restarted": False,
            "running": True,
            "message": "研报资产补漏计划尚未完全停止，暂未启动新进程。",
            "stop": stop_result,
            "scheduler": scheduler_status(),
        }

    time.sleep(0.5)
    start_result = start_scheduler()
    return {
        "restarted": bool(start_result.get("running")),
        "running": bool(start_result.get("running")),
        "message": "研报资产补漏计划已重启。" if start_result.get("running") else start_result.get("message"),
        "stop": stop_result,
        "start": start_result,
        "scheduler": start_result.get("scheduler") or scheduler_status(),
    }


def trigger_once(target_date=None):
    env_extra = {}
    if target_date:
        env_extra["REPORT_ASSET_BACKFILL_TARGET_DATE"] = str(target_date)
    process = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "src" / "backend" / "run_report_video_asset_scheduler.py"),
            "once",
        ],
        cwd=str(PROJECT_ROOT),
        env=_scheduler_env(env_extra),
        capture_output=True,
        text=True,
        timeout=600,
    )
    if process.returncode != 0:
        raise RuntimeError(
            f"立即执行研报资产补漏失败：{(process.stderr or process.stdout or '').strip() or '未知错误'}"
        )

    lines = [line.strip() for line in (process.stdout or "").splitlines() if line.strip()]
    payload = None
    for line in reversed(lines):
        try:
            payload = json.loads(line)
            break
        except Exception:
            continue
    if not isinstance(payload, dict):
        raise RuntimeError("立即执行研报资产补漏没有返回结构化结果。")
    return payload


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


def build_daily_backfill_payload(config=None, trigger="scheduler", target_date=None):
    config = _normalize_config(config or load_config())
    return {
        "source": "daily_backfill",
        "target_date": target_date or _target_date_from_config(config),
        "trigger": trigger,
        "parse_pdfs": config.get("parse_pdfs", True),
        "config": config.get("config") or {},
    }


def _target_date_from_config(config):
    offset = _bounded_int(config.get("target_date_offset_days"), 1, lower=0, upper=30)
    return (datetime.now().date() - timedelta(days=offset)).isoformat()


def _normalize_config(payload):
    payload = payload if isinstance(payload, dict) else {}
    defaults = default_config()
    nested = {**defaults["config"], **(payload.get("config") or {})}
    return {
        "enabled": _truthy(payload.get("enabled", defaults["enabled"])),
        "schedule_time": _normalize_time(payload.get("schedule_time") or defaults["schedule_time"]),
        "interval_minutes": _optional_int(payload.get("interval_minutes"), lower=1, upper=24 * 60),
        "run_on_start": _truthy(payload.get("run_on_start", defaults["run_on_start"])),
        "parse_pdfs": _truthy(payload.get("parse_pdfs", defaults["parse_pdfs"])),
        "target_date_offset_days": _bounded_int(
            payload.get("target_date_offset_days"),
            defaults["target_date_offset_days"],
            lower=0,
            upper=30,
        ),
        "config": {
            "max_files": _bounded_int(nested.get("max_files"), defaults["config"]["max_files"], lower=1, upper=500),
            "tab_label_keyword": str(nested.get("tab_label_keyword") or "外资研报").strip(),
            "output_ratios": [item for item in (nested.get("output_ratios") or ["16:9"]) if item in {"16:9", "9:16"}] or ["16:9"],
            "default_segment_count": _bounded_int(nested.get("default_segment_count"), 4, lower=3, upper=6),
            "style": str(nested.get("style") or "professional_finance"),
            "generate_subtitles": _truthy(nested.get("generate_subtitles", True)),
        },
        "last_run_at": payload.get("last_run_at"),
        "next_run_at": payload.get("next_run_at"),
        "last_result": payload.get("last_result") if isinstance(payload.get("last_result"), dict) else None,
    }


def _write_config(config):
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    tmp_path = CONFIG_PATH.with_suffix(".tmp")
    with tmp_path.open("w", encoding="utf-8") as fh:
        json.dump(_normalize_config(config), fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.chmod(tmp_path, 0o600)
    tmp_path.replace(CONFIG_PATH)
    os.chmod(CONFIG_PATH, 0o600)


def _write_pid_file(payload):
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    tmp_path = PID_PATH.with_suffix(".tmp")
    with tmp_path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.chmod(tmp_path, 0o600)
    tmp_path.replace(PID_PATH)
    os.chmod(PID_PATH, 0o600)


def _read_pid_file():
    if not PID_PATH.exists():
        return {}
    try:
        with PID_PATH.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        _safe_unlink(PID_PATH)
        return {}


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


def _bounded_int(value, default, *, lower, upper):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return min(max(parsed, lower), upper)


def _optional_int(value, *, lower, upper):
    if value in (None, "", 0, "0"):
        return None
    return _bounded_int(value, lower, lower=lower, upper=upper)


def _truthy(value):
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"0", "false", "no", "off", ""}


def _normalize_time(value):
    raw = str(value or "02:30").strip()
    try:
        hour, minute = raw.split(":", 1)
        hour_i = min(max(int(hour), 0), 23)
        minute_i = min(max(int(minute), 0), 59)
        return f"{hour_i:02d}:{minute_i:02d}"
    except Exception:
        return "02:30"


def utc_now_iso():
    return datetime.utcnow().replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


def next_interval_run_iso(interval_minutes):
    return (datetime.now(timezone.utc) + timedelta(minutes=int(interval_minutes))).isoformat().replace("+00:00", "Z")
