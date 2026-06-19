import json
import os
import re
import signal
import subprocess
import threading
import time
from copy import deepcopy
from datetime import datetime
from pathlib import Path

import redis
from flask import Blueprint, jsonify, request


bp = Blueprint("celery_monitor", __name__, url_prefix="/api/v1/celery")

PROJECT_ROOT = Path(__file__).resolve().parents[4]
LOG_DIR = PROJECT_ROOT / "logs" / "celery"
RUNTIME_DIR = PROJECT_ROOT / "runtime" / "celery"
WATCHDOG_STATE_FILE = RUNTIME_DIR / "watchdog_state.json"
WATCHDOG_LOG_FILE = LOG_DIR / "celery_watchdog.log"
WATCHDOG_SCRIPT = PROJECT_ROOT / "scripts" / "dev" / "run-celery-watchdog.sh"
DEFAULT_WATCHDOG_SERVICES = (
    "zsxq_workflow",
    "zsxq_sync",
    "content_pipeline",
    "zsxq_publish",
    "doubao_browser_tts",
    "report_pdf_gemini",
    "report_audio_workflow",
    "mp3_video_workflow",
)

BUSINESS_TASK_FEATURES = {
    "render": ("publisher",),
    "report_audio_workflow": ("report_audio_workflow",),
    "mp3_video_workflow": ("mp3_video_workflow",),
    "content_pipeline": ("content_pdf_clean", "content_audio_clean"),
    "zsxq_publish": ("zsxq_clean_output_publish",),
}
ZSXQ_BUSINESS_TASK_SERVICES = ("zsxq_sync", "zsxq_workflow")
ZSXQ_ACTIVE_STATUSES = ("queued", "running", "pending", "processing")
BUSINESS_TASK_ACTIVE_STATUSES = ("pending", "processing")
BUSINESS_TASK_LIMIT_PER_SERVICE = 8
BUSINESS_TASK_UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)

SERVICES = {
    "render": {
        "label": "主视频合成",
        "queue": "render",
        "hostname_prefix": "render@",
        "run_script": "scripts/dev/run-celery.sh",
        "log_file": "render.log",
        "description": "publisher / video-editor 主视频渲染队列",
    },
    "cloud_tts": {
        "label": "豆包 TTS",
        "queue": "cloud_tts",
        "hostname_prefix": "cloud_tts@",
        "run_script": "scripts/dev/run-celery-tts.sh",
        "log_file": "cloud_tts.log",
        "description": "豆包语音合成队列",
    },
    "doubao_browser_tts": {
        "label": "豆包网页朗读捕获",
        "queue": "doubao_browser_tts",
        "hostname_prefix": "doubao_browser_tts@",
        "run_script": "scripts/dev/run-celery-doubao-browser-tts.sh",
        "log_file": "doubao_browser_tts.log",
        "description": "真实浏览器控制豆包网页并通过插件捕获 WAV 的串行队列",
    },
    "report_pdf_gemini": {
        "label": "研报 PDF Gem 解析",
        "queue": "report_pdf_gemini",
        "hostname_prefix": "report_pdf_gemini@",
        "run_script": "scripts/dev/run-celery-report-pdf-gemini.sh",
        "log_file": "report_pdf_gemini.log",
        "description": "真实浏览器控制研报解析 Gem，上传 PDF、追问自审并提取回复的串行队列",
    },
    "report_audio_workflow": {
        "label": "研报转语音编排",
        "queue": "report_audio_workflow",
        "hostname_prefix": "report_audio_workflow@",
        "run_script": "scripts/dev/run-celery-report-audio-workflow.sh",
        "log_file": "report_audio_workflow.log",
        "description": "调度研报 PDF Gem 解析、自审结构化、豆包浏览器朗读并汇总音频",
    },
    "mp3_video_workflow": {
        "label": "MP3 视频编排",
        "queue": "mp3_video_workflow",
        "hostname_prefix": "mp3_video_workflow@",
        "run_script": "scripts/dev/run-celery-mp3-video-workflow.sh",
        "log_file": "mp3_video_workflow.log",
        "description": "调度已同步 MP3、Gemini 信息图、DS 规划与横竖屏视频子任务",
    },
    "zsxq_sync": {
        "label": "知识星球同步",
        "queue": "zsxq_sync",
        "hostname_prefix": "zsxq_sync@",
        "run_script": "scripts/dev/run-celery-zsxq.sh",
        "log_file": "zsxq_sync.log",
        "description": "知识星球增量同步与附件下载队列",
    },
    "zsxq_workflow": {
        "label": "知识星球编排",
        "queue": "zsxq_workflow",
        "hostname_prefix": "zsxq_workflow@",
        "run_script": "scripts/dev/run-celery-zsxq-workflow.sh",
        "log_file": "zsxq_workflow.log",
        "description": "同步、清洗、发布三阶段状态机与下一步调度队列",
    },
    "zsxq_publish": {
        "label": "知识星球发布",
        "queue": "zsxq_publish",
        "hostname_prefix": "zsxq_publish@",
        "run_script": "scripts/dev/run-celery-zsxq-publish.sh",
        "log_file": "zsxq_publish.log",
        "description": "清洗产物自动发帖、附件上传与 Cookie 池切换队列",
    },
    "podcast_visualizer": {
        "label": "播客视觉化",
        "queue": "podcast_visualizer",
        "hostname_prefix": "podcast_visualizer@",
        "run_script": "scripts/dev/run-celery-podcast.sh",
        "log_file": "podcast_visualizer.log",
        "description": "播客视觉化视频渲染队列",
    },
    "report_video": {
        "label": "研报导图视频工厂",
        "queue": "report_video",
        "hostname_prefix": "report_video@",
        "run_script": "scripts/dev/run-celery-report-video.sh",
        "log_file": "report_video.log",
        "description": "研报解析、AI 文案与导图视频渲染队列",
    },
    "content_pipeline": {
        "label": "内容流水线",
        "queue": "content_pipeline",
        "hostname_prefix": "content_pipeline@",
        "run_script": "scripts/dev/run-celery-content-pipeline.sh",
        "log_file": "content_pipeline.log",
        "description": "同步内容清洗、PDF 清洗引擎适配与结果搬运队列",
    },
}

SCHEDULERS = {
    "zsxq_auto_scheduler": {
        "label": "ZSXQ 自动同步调度器",
        "pattern": "run_zsxq_auto_scheduler.py",
        "run_script": "scripts/dev/run-zsxq-auto-scheduler.sh",
        "log_file": "zsxq_auto_scheduler.log",
        "description": "知识星球自动同步调度器",
    },
    "zsxq_auto_publish_scheduler": {
        "label": "ZSXQ 自动发帖调度器",
        "pattern": "run_zsxq_auto_publish_scheduler.py",
        "run_script": "scripts/dev/run-zsxq-auto-publish-scheduler.sh",
        "log_file": "zsxq_auto_publish.log",
        "description": "知识星球自动发帖调度器",
    },
    "content_retention_cleanup_scheduler": {
        "label": "内容保留清理调度器",
        "pattern": "run_content_retention_cleanup_scheduler.py",
        "argv": [
            "venv/bin/python",
            "src/backend/run_content_retention_cleanup_scheduler.py",
            "start",
        ],
        "log_file": "content_retention_cleanup_scheduler.log",
        "description": "内容流水线历史文件清理调度器",
    },
    "report_video_asset_scheduler": {
        "label": "研报资产扫描调度器",
        "pattern": "run_report_video_asset_scheduler.py",
        "run_script": "scripts/dev/run-report-video-asset-scheduler.sh",
        "log_file": "report_video_asset_scan_scheduler.log",
        "description": "研报资产补漏扫描调度器",
    },
    "report_video_auto_scheduler": {
        "label": "研报视频自动调度器",
        "pattern": "run_report_video_auto_scheduler.py",
        "run_script": "scripts/dev/run-report-video-auto-scheduler.sh",
        "log_file": "report_video_auto_scheduler.log",
        "description": "研报视频自动扫描调度器",
    },
    "youtube_data_scheduler": {
        "label": "YouTube 数据抓取调度器",
        "pattern": "run_scheduler.py",
        "argv": [
            "venv/bin/python",
            "src/backend/run_scheduler.py",
            "start",
        ],
        "log_file": "scheduler.log",
        "description": "YouTube 数据抓取调度器",
    },
}


def _truthy_env(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _int_env(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


STATUS_CACHE_TTL_SECONDS = float(os.environ.get("CELERY_MONITOR_STATUS_CACHE_TTL", "5"))
REPORT_VIDEO_STATS_CACHE_TTL_SECONDS = float(os.environ.get("CELERY_MONITOR_REPORT_VIDEO_STATS_CACHE_TTL", "60"))
BUSINESS_TASKS_CACHE_TTL_SECONDS = float(os.environ.get("CELERY_MONITOR_BUSINESS_TASKS_CACHE_TTL", "30"))
BUSINESS_TASKS_AUTO_CLEAN_STALE = _truthy_env("CELERY_MONITOR_AUTO_CLEAN_STALE_BUSINESS_TASKS", False)
BUSINESS_TASKS_STALE_PENDING_SECONDS = max(3600, _int_env("CELERY_MONITOR_STALE_PENDING_MINUTES", 120) * 60)
BUSINESS_TASKS_STALE_PROCESSING_SECONDS = max(7200, _int_env("CELERY_MONITOR_STALE_PROCESSING_MINUTES", 240) * 60)
BUSINESS_TASKS_QUEUE_SCAN_LIMIT = max(20, _int_env("CELERY_MONITOR_BUSINESS_TASK_QUEUE_SCAN_LIMIT", 1000))
STATUS_CACHE = {
    "key": None,
    "expires_at": 0.0,
    "payload": None,
}
REPORT_VIDEO_STATS_CACHE = {
    "expires_at": 0.0,
    "payload": None,
    "error": None,
}
BUSINESS_TASKS_CACHE = {
    "expires_at": 0.0,
    "tasks": None,
    "errors": None,
}
STATUS_CACHE_LOCK = threading.Lock()
REPORT_VIDEO_STATS_CACHE_LOCK = threading.Lock()
BUSINESS_TASKS_CACHE_LOCK = threading.Lock()


def _service_or_404(service_id):
    service = SERVICES.get(service_id)
    if not service:
        return None, (jsonify({"success": False, "message": "未知 Celery 服务"}), 404)
    return service, None


def _clear_status_cache():
    with STATUS_CACHE_LOCK:
        STATUS_CACHE.update({
            "key": None,
            "expires_at": 0.0,
            "payload": None,
        })
    with REPORT_VIDEO_STATS_CACHE_LOCK:
        REPORT_VIDEO_STATS_CACHE.update({
            "expires_at": 0.0,
            "payload": None,
            "error": None,
        })
    with BUSINESS_TASKS_CACHE_LOCK:
        BUSINESS_TASKS_CACHE.update({
            "expires_at": 0.0,
            "tasks": None,
            "errors": None,
        })


def _ensure_dirs():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)


def _pid_file(service_id):
    return RUNTIME_DIR / f"{service_id}.pid"


def _watchdog_pid_file():
    return RUNTIME_DIR / "celery_watchdog.pid"


def _log_path(service):
    return LOG_DIR / service["log_file"]


def _scheduler_log_path(scheduler):
    return PROJECT_ROOT / "logs" / scheduler["log_file"]


def _read_pid_file(service_id):
    path = _pid_file(service_id)
    if not path.exists():
        return None
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return None


def _process_exists(pid):
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except (ProcessLookupError, ValueError, TypeError):
        return False
    except OSError:
        return False


def _list_processes():
    try:
        proc = subprocess.run(
            ["ps", "-axo", "pid=,etime=,command="],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return []

    processes = []
    for raw in proc.stdout.splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        processes.append({
            "pid": pid,
            "etime": parts[1],
            "command": parts[2],
        })
    return processes


def _matches_service(proc, service):
    command = proc.get("command", "")
    if "celery" not in command or " worker" not in command:
        return False
    queue = service["queue"]
    hostname = service["hostname_prefix"]
    return (
        f"-Q {queue}" in command
        or f"--queues={queue}" in command
        or f"--hostname={hostname}" in command
        or hostname in command
    )


def _service_processes(service):
    return [proc for proc in _list_processes() if _matches_service(proc, service)]


def _watchdog_processes():
    return [proc for proc in _list_processes() if "celery_watchdog_loop.py" in proc.get("command", "")]


def _scheduler_processes(scheduler):
    pattern = scheduler.get("pattern")
    if not pattern:
        return []
    return [proc for proc in _list_processes() if pattern in proc.get("command", "")]


def _terminate_pids(pids, timeout=3):
    pids = sorted(set(pid for pid in pids if pid and pid != os.getpid()))
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass

    deadline = time.time() + timeout
    while time.time() < deadline:
        if not any(_process_exists(pid) for pid in pids):
            break
        time.sleep(0.2)

    killed = []
    for pid in pids:
        if _process_exists(pid):
            try:
                os.kill(pid, signal.SIGKILL)
                killed.append(pid)
            except OSError:
                pass
    return {"terminated_pids": pids, "force_killed_pids": killed}


def _stop_scheduler(scheduler_id, scheduler):
    processes = _scheduler_processes(scheduler)
    result = _terminate_pids([proc["pid"] for proc in processes], timeout=5)
    result["was_running"] = bool(processes)
    result["processes"] = processes
    result["label"] = scheduler.get("label") or scheduler_id
    return result


def _scheduler_argv(scheduler):
    if scheduler.get("run_script"):
        return ["/bin/bash", str(PROJECT_ROOT / scheduler["run_script"])]
    return [
        str(PROJECT_ROOT / item) if index == 0 or "/" in item else item
        for index, item in enumerate(scheduler.get("argv") or [])
    ]


def _start_scheduler(scheduler_id, scheduler):
    existing = _scheduler_processes(scheduler)
    if existing:
        return {
            "already_running": True,
            "pids": [proc["pid"] for proc in existing],
            "label": scheduler.get("label") or scheduler_id,
            "log_file": str(_scheduler_log_path(scheduler).relative_to(PROJECT_ROOT)),
        }

    argv = _scheduler_argv(scheduler)
    if not argv:
        raise ValueError(f"调度器缺少启动命令: {scheduler_id}")

    log_path = _scheduler_log_path(scheduler)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env.setdefault("DATABASE_TYPE", "postgresql")
    env["PYTHONPATH"] = str(PROJECT_ROOT / "src" / "backend")

    with log_path.open("ab") as log:
        log.write(f"\n\n===== UI START {datetime.now().isoformat()} scheduler={scheduler_id} =====\n".encode("utf-8"))
        proc = subprocess.Popen(
            argv,
            cwd=str(PROJECT_ROOT),
            stdout=log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            env=env,
            start_new_session=True,
        )

    time.sleep(0.8)
    return {
        "started": True,
        "starter_pid": proc.pid,
        "label": scheduler.get("label") or scheduler_id,
        "log_file": str(log_path.relative_to(PROJECT_ROOT)),
    }


def _watchdog_status_payload():
    _ensure_dirs()
    processes = _watchdog_processes()
    starter_pid = _read_watchdog_pid_file()
    return {
        "running": bool(processes),
        "processes": processes,
        "pids": [proc["pid"] for proc in processes],
        "starter_pid": starter_pid if _process_exists(starter_pid) else None,
        "log_file": str(WATCHDOG_LOG_FILE.relative_to(PROJECT_ROOT)),
        "log_exists": WATCHDOG_LOG_FILE.exists(),
        "config": _watchdog_config(),
        "state": _load_watchdog_state(),
        "state_file": str(WATCHDOG_STATE_FILE.relative_to(PROJECT_ROOT)),
        "updated_at": datetime.utcnow().isoformat() + "Z",
    }


def _read_watchdog_pid_file():
    path = _watchdog_pid_file()
    if not path.exists():
        return None
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return None


def _start_watchdog():
    _ensure_dirs()
    existing = _watchdog_processes()
    if existing:
        return {
            "already_running": True,
            "pids": [proc["pid"] for proc in existing],
            "log_file": str(WATCHDOG_LOG_FILE.relative_to(PROJECT_ROOT)),
        }
    if not WATCHDOG_SCRIPT.exists():
        raise FileNotFoundError(f"Watchdog 启动脚本不存在: {WATCHDOG_SCRIPT.relative_to(PROJECT_ROOT)}")

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env.setdefault("DATABASE_TYPE", "postgresql")
    with WATCHDOG_LOG_FILE.open("ab") as log:
        log.write(f"\n\n===== UI START {datetime.now().isoformat()} service=celery_watchdog =====\n".encode("utf-8"))
        proc = subprocess.Popen(
            [str(WATCHDOG_SCRIPT)],
            cwd=str(PROJECT_ROOT),
            stdout=log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            env=env,
            start_new_session=True,
        )

    _watchdog_pid_file().write_text(str(proc.pid))
    time.sleep(0.8)
    return {
        "started": True,
        "starter_pid": proc.pid,
        "log_file": str(WATCHDOG_LOG_FILE.relative_to(PROJECT_ROOT)),
    }


def _stop_watchdog():
    _ensure_dirs()
    processes = _watchdog_processes()
    pids = [proc["pid"] for proc in processes]
    starter_pid = _read_watchdog_pid_file()
    if starter_pid and starter_pid not in pids and _process_exists(starter_pid):
        pids.append(starter_pid)

    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass

    deadline = time.time() + 3
    while time.time() < deadline:
        if not any(_process_exists(pid) for pid in pids):
            break
        time.sleep(0.2)

    killed = []
    for pid in pids:
        if _process_exists(pid):
            try:
                os.kill(pid, signal.SIGKILL)
                killed.append(pid)
            except OSError:
                pass

    try:
        _watchdog_pid_file().unlink(missing_ok=True)
    except OSError:
        pass

    return {"terminated_pids": pids, "force_killed_pids": killed}


def _result_has_flag(result, *keys):
    if not isinstance(result, dict):
        return False
    return any(bool(result.get(key)) for key in keys)


def _tail_file(path, lines=80):
    if not path.exists():
        return ""
    try:
        with path.open("rb") as file:
            file.seek(0, os.SEEK_END)
            size = file.tell()
            block = 4096
            data = b""
            while size > 0 and data.count(b"\n") <= lines:
                read_size = min(block, size)
                size -= read_size
                file.seek(size)
                data = file.read(read_size) + data
        return b"\n".join(data.splitlines()[-lines:]).decode("utf-8", errors="replace")
    except OSError as exc:
        return f"读取日志失败: {exc}"


def _broker_url():
    return os.environ.get("CELERY_BROKER_URL") or os.environ.get("REDIS_URL") or "redis://localhost:6379/0"


def _queue_length(queue_name):
    try:
        client = redis.Redis.from_url(
            _broker_url(),
            socket_connect_timeout=2,
            socket_timeout=2,
            retry_on_timeout=False,
        )
        return int(client.llen(queue_name)), None
    except Exception as exc:
        return None, str(exc)


def _queue_lengths():
    queues = [service["queue"] for service in SERVICES.values()]
    try:
        client = redis.Redis.from_url(
            _broker_url(),
            socket_connect_timeout=1,
            socket_timeout=1,
            retry_on_timeout=False,
        )
        pipe = client.pipeline()
        for queue_name in queues:
            pipe.llen(queue_name)
        values = pipe.execute()
        return {
            queue_name: (int(value), None)
            for queue_name, value in zip(queues, values)
        }
    except Exception as exc:
        error = str(exc)
        return {queue_name: (None, error) for queue_name in queues}


def _purge_queue(queue_name):
    client = redis.Redis.from_url(
        _broker_url(),
        socket_connect_timeout=2,
        socket_timeout=5,
        retry_on_timeout=False,
    )
    before = int(client.llen(queue_name))
    deleted = int(client.delete(queue_name))
    after = int(client.llen(queue_name))
    return {
        "queue": queue_name,
        "before": before,
        "after": after,
        "deleted_key": bool(deleted),
        "purged": max(0, before - after),
    }


def _watchdog_services():
    raw = os.environ.get("CELERY_AUTO_HEAL_SERVICES")
    if raw:
        service_ids = [item.strip() for item in raw.split(",") if item.strip()]
    else:
        service_ids = list(DEFAULT_WATCHDOG_SERVICES)
    return [service_id for service_id in service_ids if service_id in SERVICES]


def _watchdog_config():
    return {
        "enabled": _truthy_env("CELERY_AUTO_HEAL_ENABLED", False),
        "notify_enabled": _truthy_env("CELERY_AUTO_HEAL_NOTIFY_ENABLED", True),
        "services": _watchdog_services(),
        "confirmations": max(1, _int_env("CELERY_AUTO_HEAL_CONFIRMATIONS", 3)),
        "cooldown_seconds": max(60, _int_env("CELERY_AUTO_HEAL_COOLDOWN_SECONDS", 900)),
        "poll_seconds": max(10, _int_env("CELERY_AUTO_HEAL_POLL_SECONDS", 60)),
    }


def _load_watchdog_state():
    if not WATCHDOG_STATE_FILE.exists():
        return {"services": {}, "events": []}
    try:
        data = json.loads(WATCHDOG_STATE_FILE.read_text())
        if not isinstance(data, dict):
            return {"services": {}, "events": []}
        data.setdefault("services", {})
        data.setdefault("events", [])
        return data
    except (OSError, json.JSONDecodeError):
        return {"services": {}, "events": []}


def _save_watchdog_state(state):
    _ensure_dirs()
    compact = {
        "services": state.get("services") or {},
        "events": (state.get("events") or [])[-80:],
        "updated_at": datetime.utcnow().isoformat() + "Z",
    }
    WATCHDOG_STATE_FILE.write_text(json.dumps(compact, ensure_ascii=False, indent=2))


def _watchdog_event(state, service_id, event_type, message, extra=None):
    event = {
        "service_id": service_id,
        "event_type": event_type,
        "message": message,
        "created_at": datetime.utcnow().isoformat() + "Z",
    }
    if extra:
        event["extra"] = extra
    events = list(state.get("events") or [])
    events.append(event)
    state["events"] = events[-80:]
    return event


def _watchdog_notify(subject, body):
    try:
        from services.workflow_notification_service import workflow_notification_service

        return workflow_notification_service.send_email_message(subject, body)
    except Exception as exc:
        return {"sent": False, "reason": "exception", "error": str(exc)}


def _consumer_stuck(status):
    return bool(status.get("consumer_warning"))


def run_watchdog_once():
    """Detect stuck Celery consumers and optionally restart the affected worker."""
    _ensure_dirs()
    config = _watchdog_config()
    state = _load_watchdog_state()
    services_state = state.setdefault("services", {})
    now = time.time()
    inspect_data, inspect_error = _inspect_tasks_by_queue()
    results = []

    for service_id in config["services"]:
        service = SERVICES[service_id]
        status = _service_status(
            service_id,
            service,
            include_logs=False,
            inspect_data=inspect_data,
            inspect_error=inspect_error,
        )
        item_state = services_state.setdefault(service_id, {})
        stuck = _consumer_stuck(status)
        result = {
            "service_id": service_id,
            "label": service["label"],
            "stuck": stuck,
            "action": "none",
            "queue_length": status.get("queue_length"),
            "warning": status.get("consumer_warning"),
        }

        if not stuck:
            if item_state.get("stuck_count"):
                _watchdog_event(state, service_id, "recovered", f"{service['label']} 已恢复消费。")
            item_state["stuck_count"] = 0
            item_state["first_seen_at"] = None
            item_state["last_seen_at"] = None
            results.append(result)
            continue

        stuck_count = int(item_state.get("stuck_count") or 0) + 1
        item_state["stuck_count"] = stuck_count
        item_state["first_seen_at"] = item_state.get("first_seen_at") or datetime.utcnow().isoformat() + "Z"
        item_state["last_seen_at"] = datetime.utcnow().isoformat() + "Z"
        result["stuck_count"] = stuck_count

        if config["notify_enabled"] and stuck_count == 1:
            subject = f"[Celery自愈] 发现 worker 消费异常 · {service['label']}"
            body = "\n".join([
                f"服务：{service['label']} ({service_id})",
                f"队列：{service['queue']}",
                f"积压：{status.get('queue_length')}",
                f"提示：{status.get('consumer_warning')}",
                f"策略：连续 {config['confirmations']} 次确认后"
                f"{'自动重启' if config['enabled'] else '只告警，不自动重启'}。",
            ])
            notify_result = _watchdog_notify(subject, body)
            item_state["last_detect_notification_at"] = datetime.utcnow().isoformat() + "Z"
            _watchdog_event(state, service_id, "detected", subject, {"notify": notify_result})

        last_action_at = float(item_state.get("last_action_ts") or 0)
        cooldown_ready = now - last_action_at >= config["cooldown_seconds"]
        should_restart = config["enabled"] and stuck_count >= config["confirmations"] and cooldown_ready
        if not should_restart:
            result["action"] = "wait"
            results.append(result)
            continue

        subject = f"[Celery自愈] 正在自动重启 worker · {service['label']}"
        try:
            stop_result = _stop_service(service_id, service)
            time.sleep(0.6)
            start_result = _start_service(service_id, service)
            item_state["last_action_ts"] = now
            item_state["last_action_at"] = datetime.utcnow().isoformat() + "Z"
            item_state["stuck_count"] = 0
            item_state["first_seen_at"] = None
            result.update({
                "action": "restarted",
                "stop": stop_result,
                "start": start_result,
            })
            message = "\n".join([
                f"服务：{service['label']} ({service_id})",
                f"队列：{service['queue']}",
                f"触发原因：{status.get('consumer_warning')}",
                f"重启结果：已停止 {stop_result.get('terminated_pids') or []}，已启动 {start_result.get('starter_pid') or start_result.get('pids')}",
            ])
            notify_result = _watchdog_notify(subject, message) if config["notify_enabled"] else {"sent": False, "reason": "notify_disabled"}
            _watchdog_event(state, service_id, "restarted", subject, {"notify": notify_result, "result": result})
        except Exception as exc:
            result.update({"action": "restart_failed", "error": str(exc)})
            notify_result = _watchdog_notify(
                f"[Celery自愈] 自动重启失败 · {service['label']}",
                f"服务：{service['label']} ({service_id})\n错误：{exc}",
            ) if config["notify_enabled"] else {"sent": False, "reason": "notify_disabled"}
            _watchdog_event(state, service_id, "restart_failed", str(exc), {"notify": notify_result})
        results.append(result)

    _save_watchdog_state(state)
    return {
        "config": config,
        "inspect_error": inspect_error,
        "results": results,
        "state": state,
    }


def _broker_ping():
    try:
        client = redis.Redis.from_url(
            _broker_url(),
            socket_connect_timeout=2,
            socket_timeout=2,
            retry_on_timeout=False,
        )
        return bool(client.ping()), None
    except Exception as exc:
        return False, str(exc)


def _task_summary(task, state_name):
    request = task.get("request") if isinstance(task.get("request"), dict) else {}
    args = task.get("args") if task.get("args") is not None else request.get("args")
    kwargs = task.get("kwargs") if task.get("kwargs") is not None else request.get("kwargs")
    task_id = task.get("id") or request.get("id") or "-"
    name = task.get("name") or request.get("name") or "-"
    return {
        "id": task_id,
        "short_id": str(task_id)[:8],
        "name": name,
        "state": state_name,
        "args": args,
        "kwargs": kwargs,
        "time_start": task.get("time_start"),
        "worker_pid": task.get("worker_pid"),
        "hostname": task.get("hostname"),
        "delivery_info": task.get("delivery_info") or request.get("delivery_info"),
    }


def _inspect_tasks_by_queue():
    try:
        from celery_config import celery_app

        timeout = float(os.environ.get("CELERY_MONITOR_INSPECT_TIMEOUT", "1.2"))
        inspector = celery_app.control.inspect(timeout=timeout)
        replies = {
            "active": inspector.active() or {},
            "reserved": inspector.reserved() or {},
            "scheduled": inspector.scheduled() or {},
        }
    except Exception as exc:
        return {}, str(exc)

    grouped = {service["queue"]: {"active": [], "reserved": [], "scheduled": []} for service in SERVICES.values()}
    for state_name, worker_map in replies.items():
        for hostname, tasks in (worker_map or {}).items():
            queue_name = None
            for service in SERVICES.values():
                if hostname.startswith(service["hostname_prefix"]):
                    queue_name = service["queue"]
                    break
            if not queue_name:
                continue
            bucket = grouped.setdefault(queue_name, {"active": [], "reserved": [], "scheduled": []})
            for item in tasks or []:
                task_payload = item.get("request") if state_name == "scheduled" and isinstance(item, dict) else item
                if not isinstance(task_payload, dict):
                    continue
                summary = _task_summary(task_payload, state_name)
                summary["hostname"] = summary.get("hostname") or hostname
                if state_name == "scheduled":
                    summary["eta"] = item.get("eta")
                    summary["priority"] = item.get("priority")
                bucket[state_name].append(summary)
    return grouped, None


def _uuid_refs_from_value(value):
    if value is None:
        return set()
    if isinstance(value, bytes):
        text = value.decode("utf-8", errors="replace")
    elif isinstance(value, (dict, list, tuple)):
        text = json.dumps(value, ensure_ascii=False, default=str)
    else:
        text = str(value)
    return set(BUSINESS_TASK_UUID_RE.findall(text))


def _business_task_runtime_refs():
    refs = set()
    queue_names = []
    for service_id in BUSINESS_TASK_FEATURES.keys():
        service = SERVICES.get(service_id)
        if not service:
            continue
        queue_name = service["queue"]
        if queue_name not in queue_names:
            queue_names.append(queue_name)

    try:
        client = redis.Redis.from_url(
            _broker_url(),
            socket_connect_timeout=1,
            socket_timeout=2,
            retry_on_timeout=False,
        )
        for queue_name in queue_names:
            queue_length = int(client.llen(queue_name))
            if queue_length > BUSINESS_TASKS_QUEUE_SCAN_LIMIT:
                return refs, (
                    f"{queue_name} 队列长度 {queue_length} 超过扫描上限 "
                    f"{BUSINESS_TASKS_QUEUE_SCAN_LIMIT}，跳过历史残留自动收口"
                )
            for item in client.lrange(queue_name, 0, BUSINESS_TASKS_QUEUE_SCAN_LIMIT - 1):
                refs.update(_uuid_refs_from_value(item))
    except Exception as exc:
        return refs, f"Redis 队列扫描失败: {exc}"

    inspect_data, inspect_error = _inspect_tasks_by_queue()
    if inspect_error:
        return refs, f"Celery 运行态检查失败: {inspect_error}"

    for service_id in BUSINESS_TASK_FEATURES.keys():
        service = SERVICES.get(service_id)
        if not service:
            continue
        tasks_by_state = inspect_data.get(service["queue"]) or {}
        for tasks in tasks_by_state.values():
            for task in tasks or []:
                refs.update(_uuid_refs_from_value(task))
    return refs, None


def _business_task_stale_threshold(row):
    return (
        BUSINESS_TASKS_STALE_PENDING_SECONDS
        if row.status == "pending"
        else BUSINESS_TASKS_STALE_PROCESSING_SECONDS
    )


def _business_task_should_auto_clean(row, runtime_refs, now):
    task_id = str(row.id or "")
    celery_task_id = str(row.celery_task_id or "")
    if task_id and task_id in runtime_refs:
        return False
    if celery_task_id and celery_task_id in runtime_refs:
        return False
    marker = row.updated_at or row.created_at
    if not marker:
        return False
    return (now - marker).total_seconds() >= _business_task_stale_threshold(row)


def _business_task_stale_by_age(row, now):
    marker = row.updated_at or row.created_at
    if not marker:
        return False
    return (now - marker).total_seconds() >= _business_task_stale_threshold(row)


def _auto_clean_stale_business_tasks(PublicationTask, stale_rows):
    ids = [str(row.id) for row in stale_rows if row.id]
    if not ids:
        return None

    try:
        from models import db
    except Exception as exc:
        return f"加载数据库失败: {exc}"

    now = datetime.utcnow()
    reason = "数据库仍是未终态，但 Redis 队列和 Celery 运行态均没有对应任务；监控自动收口为 failed。"
    try:
        tasks = (
            PublicationTask.query
            .filter(PublicationTask.id.in_(ids))
            .filter(PublicationTask.status.in_(BUSINESS_TASK_ACTIVE_STATUSES))
            .all()
        )
        for task in tasks:
            previous_result = task.result if isinstance(task.result, dict) else {}
            task.result = {
                **previous_result,
                "stale_cleanup": {
                    "cleaned_at": now.isoformat() + "Z",
                    "reason": reason,
                    "previous_status": task.status,
                    "previous_step": task.current_step,
                    "previous_progress": task.progress,
                    "previous_updated_at": task.updated_at.isoformat() + "Z" if task.updated_at else None,
                    "auto_cleaned_by": "celery_monitor",
                },
            }
            task.status = "failed"
            task.current_step = "stale_cleaned"
            task.error_message = f"Celery Monitor 自动清理历史残留任务：{reason}"
            task.completed_at = now
            task.updated_at = now
            task.sync_projection_fields()
        if tasks:
            db.session.commit()
        return None
    except Exception as exc:
        db.session.rollback()
        return str(exc)


def _report_video_stage_stats(force_refresh=False):
    if REPORT_VIDEO_STATS_CACHE_TTL_SECONDS > 0 and not force_refresh:
        with REPORT_VIDEO_STATS_CACHE_LOCK:
            if REPORT_VIDEO_STATS_CACHE["expires_at"] > time.time():
                return deepcopy(REPORT_VIDEO_STATS_CACHE["payload"]), REPORT_VIDEO_STATS_CACHE["error"]

    try:
        from models.report_video import ReportVideoReport, ReportVideoTask
        from sqlalchemy import func
    except Exception as exc:
        return None, f"加载研报模型失败: {exc}"

    try:
        task_rows = (
            ReportVideoTask.query
            .with_entities(ReportVideoTask.status, func.count(ReportVideoTask.id))
            .group_by(ReportVideoTask.status)
            .all()
        )
        parse_rows = (
            ReportVideoReport.query
            .with_entities(ReportVideoReport.parse_status, func.count(ReportVideoReport.id))
            .group_by(ReportVideoReport.parse_status)
            .all()
        )
        ai_rows = (
            ReportVideoReport.query
            .with_entities(ReportVideoReport.ai_status, func.count(ReportVideoReport.id))
            .group_by(ReportVideoReport.ai_status)
            .all()
        )
    except Exception as exc:
        return None, str(exc)

    task_counter = {status: int(count or 0) for status, count in task_rows if status}
    parse_counter = {status: int(count or 0) for status, count in parse_rows if status}
    ai_counter = {status: int(count or 0) for status, count in ai_rows if status}

    payload = {
        "task_status": task_counter,
        "parse_status": parse_counter,
        "ai_status": ai_counter,
        "totals": {
            "tasks": sum(task_counter.values()),
            "reports": max(sum(parse_counter.values()), sum(ai_counter.values())),
        },
    }
    if REPORT_VIDEO_STATS_CACHE_TTL_SECONDS > 0:
        with REPORT_VIDEO_STATS_CACHE_LOCK:
            REPORT_VIDEO_STATS_CACHE.update({
                "expires_at": time.time() + REPORT_VIDEO_STATS_CACHE_TTL_SECONDS,
                "payload": deepcopy(payload),
                "error": None,
            })
    return payload, None


def _business_tasks_snapshot(force_refresh=False):
    service_ids = _business_task_service_ids()
    publication_service_ids = list(BUSINESS_TASK_FEATURES.keys())
    empty = {service_id: [] for service_id in service_ids}
    if not service_ids:
        return {}, {}

    if BUSINESS_TASKS_CACHE_TTL_SECONDS > 0 and not force_refresh:
        with BUSINESS_TASKS_CACHE_LOCK:
            if BUSINESS_TASKS_CACHE["expires_at"] > time.time() and BUSINESS_TASKS_CACHE["tasks"] is not None:
                return deepcopy(BUSINESS_TASKS_CACHE["tasks"]), deepcopy(BUSINESS_TASKS_CACHE["errors"])

    try:
        from models.task import PublicationTask
    except Exception as exc:
        publication_errors = {
            service_id: f"加载任务模型失败: {exc}"
            for service_id in publication_service_ids
        }
        zsxq_buckets, zsxq_errors = _zsxq_business_tasks_snapshot()
        empty.update(zsxq_buckets)
        publication_errors.update(zsxq_errors)
        return empty, {service_id: publication_errors.get(service_id) for service_id in service_ids}

    features = sorted({feature for values in BUSINESS_TASK_FEATURES.values() for feature in values})
    try:
        rows = (
            PublicationTask.query
            .with_entities(
                PublicationTask.id.label("id"),
                PublicationTask.celery_task_id.label("celery_task_id"),
                PublicationTask.feature.label("feature"),
                PublicationTask.status.label("status"),
                PublicationTask.current_step.label("current_step"),
                PublicationTask.progress.label("progress"),
                PublicationTask.task_title.label("task_title"),
                PublicationTask.parent_feature.label("parent_feature"),
                PublicationTask.parent_task_id.label("parent_task_id"),
                PublicationTask.source_feature.label("source_feature"),
                PublicationTask.source_task_id.label("source_task_id"),
                PublicationTask.error_message.label("error_message"),
                PublicationTask.created_at.label("created_at"),
                PublicationTask.updated_at.label("updated_at"),
                PublicationTask.completed_at.label("completed_at"),
            )
            .filter(PublicationTask.feature.in_(features))
            .filter(PublicationTask.status.in_(BUSINESS_TASK_ACTIVE_STATUSES))
            .order_by(PublicationTask.updated_at.desc(), PublicationTask.created_at.desc())
            .limit(max(200, BUSINESS_TASK_LIMIT_PER_SERVICE * max(1, len(service_ids)) * 8))
            .all()
        )
    except Exception as exc:
        return empty, {service_id: f"业务任务读取失败: {exc}" for service_id in service_ids}

    errors = {service_id: None for service_id in service_ids}
    if BUSINESS_TASKS_AUTO_CLEAN_STALE and rows:
        now = datetime.utcnow()
        stale_candidates = [
            row for row in rows
            if _business_task_stale_by_age(row, now)
        ]
    else:
        stale_candidates = []

    if stale_candidates:
        runtime_refs, runtime_error = _business_task_runtime_refs()
        if not runtime_error:
            stale_rows = [
                row for row in stale_candidates
                if _business_task_should_auto_clean(row, runtime_refs, now)
            ]
            if stale_rows:
                cleanup_error = _auto_clean_stale_business_tasks(PublicationTask, stale_rows)
                if cleanup_error:
                    errors = {
                        service_id: f"历史残留自动收口失败: {cleanup_error}"
                        for service_id in service_ids
                    }
                else:
                    stale_ids = {str(row.id) for row in stale_rows}
                    rows = [row for row in rows if str(row.id) not in stale_ids]

    buckets = {service_id: [] for service_id in service_ids}
    for row in rows:
        for service_id, feature_names in BUSINESS_TASK_FEATURES.items():
            if row.feature not in feature_names or len(buckets[service_id]) >= BUSINESS_TASK_LIMIT_PER_SERVICE:
                continue
            buckets[service_id].append(_business_task_payload(row))

    zsxq_buckets, zsxq_errors = _zsxq_business_tasks_snapshot()
    for service_id in ZSXQ_BUSINESS_TASK_SERVICES:
        buckets[service_id] = zsxq_buckets.get(service_id, [])
        errors[service_id] = zsxq_errors.get(service_id)

    if BUSINESS_TASKS_CACHE_TTL_SECONDS > 0:
        with BUSINESS_TASKS_CACHE_LOCK:
            BUSINESS_TASKS_CACHE.update({
                "expires_at": time.time() + BUSINESS_TASKS_CACHE_TTL_SECONDS,
                "tasks": deepcopy(buckets),
                "errors": deepcopy(errors),
            })
    return buckets, errors


def _business_task_service_ids():
    service_ids = list(BUSINESS_TASK_FEATURES.keys())
    for service_id in ZSXQ_BUSINESS_TASK_SERVICES:
        if service_id not in service_ids:
            service_ids.append(service_id)
    return service_ids


def _zsxq_business_tasks_snapshot():
    buckets = {service_id: [] for service_id in ZSXQ_BUSINESS_TASK_SERVICES}
    errors = {service_id: None for service_id in ZSXQ_BUSINESS_TASK_SERVICES}
    try:
        from models.zsxq_sync import ZsxqSyncRun
        from models.zsxq_workflow import ZsxqWorkflowRun
    except Exception as exc:
        return buckets, {
            service_id: f"加载星球任务模型失败: {exc}"
            for service_id in ZSXQ_BUSINESS_TASK_SERVICES
        }

    try:
        sync_rows = (
            ZsxqSyncRun.query
            .with_entities(
                ZsxqSyncRun.id.label("id"),
                ZsxqSyncRun.group_id.label("group_id"),
                ZsxqSyncRun.group_name.label("group_name"),
                ZsxqSyncRun.tab_label.label("tab_label"),
                ZsxqSyncRun.mode.label("mode"),
                ZsxqSyncRun.status.label("status"),
                ZsxqSyncRun.system_batch_id.label("system_batch_id"),
                ZsxqSyncRun.topics_fetched.label("topics_fetched"),
                ZsxqSyncRun.attachments_downloaded.label("attachments_downloaded"),
                ZsxqSyncRun.attachments_failed.label("attachments_failed"),
                ZsxqSyncRun.error_message.label("error_message"),
                ZsxqSyncRun.created_at.label("created_at"),
                ZsxqSyncRun.updated_at.label("updated_at"),
                ZsxqSyncRun.completed_at.label("completed_at"),
            )
            .filter(ZsxqSyncRun.status.in_(ZSXQ_ACTIVE_STATUSES))
            .order_by(ZsxqSyncRun.updated_at.desc(), ZsxqSyncRun.created_at.desc())
            .limit(BUSINESS_TASK_LIMIT_PER_SERVICE)
            .all()
        )
        buckets["zsxq_sync"] = [_zsxq_sync_task_payload(row) for row in sync_rows]
    except Exception as exc:
        errors["zsxq_sync"] = f"星球同步运行记录读取失败: {exc}"

    try:
        workflow_rows = (
            ZsxqWorkflowRun.query
            .with_entities(
                ZsxqWorkflowRun.id.label("id"),
                ZsxqWorkflowRun.batch_id.label("batch_id"),
                ZsxqWorkflowRun.group_id.label("group_id"),
                ZsxqWorkflowRun.group_name.label("group_name"),
                ZsxqWorkflowRun.trigger.label("trigger"),
                ZsxqWorkflowRun.status.label("status"),
                ZsxqWorkflowRun.current_stage.label("current_stage"),
                ZsxqWorkflowRun.round.label("round"),
                ZsxqWorkflowRun.max_rounds.label("max_rounds"),
                ZsxqWorkflowRun.sync_run_id.label("sync_run_id"),
                ZsxqWorkflowRun.error_message.label("error_message"),
                ZsxqWorkflowRun.created_at.label("created_at"),
                ZsxqWorkflowRun.updated_at.label("updated_at"),
                ZsxqWorkflowRun.completed_at.label("completed_at"),
            )
            .filter(ZsxqWorkflowRun.status.in_(ZSXQ_ACTIVE_STATUSES))
            .order_by(ZsxqWorkflowRun.updated_at.desc(), ZsxqWorkflowRun.created_at.desc())
            .limit(BUSINESS_TASK_LIMIT_PER_SERVICE)
            .all()
        )
        buckets["zsxq_workflow"] = [_zsxq_workflow_task_payload(row) for row in workflow_rows]
    except Exception as exc:
        errors["zsxq_workflow"] = f"星球编排运行记录读取失败: {exc}"

    return buckets, errors


def _zsxq_sync_task_payload(row):
    task_id = str(row.id or "")
    title = f"{row.group_name or row.group_id or '知识星球'} · 同步"
    if row.system_batch_id:
        title = f"{title} {row.system_batch_id}"
    details = [
        row.tab_label or row.mode,
        f"topics {row.topics_fetched or 0}",
        f"下载 {row.attachments_downloaded or 0}",
    ]
    if row.attachments_failed:
        details.append(f"失败 {row.attachments_failed}")
    return {
        "id": task_id,
        "short_id": task_id[:8],
        "feature": "zsxq_sync_run",
        "status": _zsxq_business_status(row.status),
        "step": " · ".join(str(item) for item in details if item),
        "progress": 0,
        "title": title,
        "parent_feature": None,
        "parent_task_id": None,
        "source_feature": row.mode,
        "source_task_id": row.system_batch_id,
        "error": row.error_message,
        "created_at": row.created_at.isoformat() + "Z" if row.created_at else None,
        "updated_at": row.updated_at.isoformat() + "Z" if row.updated_at else None,
        "completed_at": row.completed_at.isoformat() + "Z" if row.completed_at else None,
    }


def _zsxq_workflow_task_payload(row):
    task_id = str(row.id or "")
    title = f"{row.group_name or row.group_id or '知识星球'} · 编排"
    if row.batch_id:
        title = f"{title} {row.batch_id}"
    round_text = f"第 {row.round} 轮" if row.round is not None else ""
    max_round_text = f"/{row.max_rounds}" if row.max_rounds is not None else ""
    return {
        "id": task_id,
        "short_id": task_id[:8],
        "feature": "zsxq_workflow_run",
        "status": _zsxq_business_status(row.status),
        "step": " · ".join(item for item in [row.current_stage, f"{round_text}{max_round_text}".strip()] if item),
        "progress": _zsxq_workflow_progress(row.current_stage),
        "title": title,
        "parent_feature": None,
        "parent_task_id": None,
        "source_feature": row.trigger,
        "source_task_id": row.sync_run_id,
        "error": row.error_message,
        "created_at": row.created_at.isoformat() + "Z" if row.created_at else None,
        "updated_at": row.updated_at.isoformat() + "Z" if row.updated_at else None,
        "completed_at": row.completed_at.isoformat() + "Z" if row.completed_at else None,
    }


def _zsxq_business_status(status):
    if status == "queued":
        return "pending"
    if status in {"running", "processing"}:
        return "processing"
    return status or "pending"


def _zsxq_workflow_progress(stage):
    return {
        "init": 0,
        "sync": 25,
        "clean": 60,
        "publish": 85,
        "done": 100,
    }.get(stage, 0)


def _business_task_payload(row):
    task_id = str(row.id or "")
    title = row.task_title or _business_task_fallback_title(row)
    return {
        "id": task_id,
        "short_id": task_id[:8],
        "feature": row.feature,
        "status": row.status,
        "step": row.current_step,
        "progress": row.progress or 0,
        "title": title,
        "parent_feature": row.parent_feature,
        "parent_task_id": row.parent_task_id,
        "source_feature": row.source_feature,
        "source_task_id": row.source_task_id,
        "error": row.error_message,
        "created_at": row.created_at.isoformat() + "Z" if row.created_at else None,
        "updated_at": row.updated_at.isoformat() + "Z" if row.updated_at else None,
        "completed_at": row.completed_at.isoformat() + "Z" if row.completed_at else None,
    }


def _business_task_fallback_title(row):
    labels = {
        "publisher": "视频合成任务",
        "report_audio_workflow": "研报转语音编排",
        "mp3_video_workflow": "MP3 视频编排",
        "content_pdf_clean": "PDF 清洗任务",
        "content_audio_clean": "音频清洗任务",
        "zsxq_clean_output_publish": "知识星球发布任务",
    }
    return labels.get(row.feature, row.feature or "业务任务")


def _service_status(
    service_id,
    service,
    include_logs=False,
    log_lines=20,
    inspect_data=None,
    inspect_error=None,
    queue_lengths=None,
    all_processes=None,
    business_tasks=None,
    business_task_error=None,
    force_refresh=False,
):
    process_source = all_processes if all_processes is not None else _list_processes()
    processes = [proc for proc in process_source if _matches_service(proc, service)]
    if queue_lengths and service["queue"] in queue_lengths:
        queue_len, queue_error = queue_lengths[service["queue"]]
    else:
        queue_len, queue_error = _queue_length(service["queue"])
    starter_pid = _read_pid_file(service_id)
    log_path = _log_path(service)
    tasks = (inspect_data or {}).get(service["queue"]) or {"active": [], "reserved": [], "scheduled": []}
    active_count = len(tasks.get("active") or [])
    reserved_count = len(tasks.get("reserved") or [])
    scheduled_count = len(tasks.get("scheduled") or [])
    consumer_warning = None
    if (
        processes
        and queue_len
        and queue_len > 0
        and active_count == 0
        and reserved_count == 0
        and scheduled_count == 0
        and not inspect_error
    ):
        consumer_warning = "Worker 在线但未领取积压任务，消费连接可能卡住；建议重启该队列 worker。"

    status = {
        "id": service_id,
        "label": service["label"],
        "queue": service["queue"],
        "description": service["description"],
        "running": bool(processes),
        "processes": processes,
        "pids": [proc["pid"] for proc in processes],
        "starter_pid": starter_pid if _process_exists(starter_pid) else None,
        "queue_length": queue_len,
        "queue_error": queue_error,
        "log_file": str(log_path.relative_to(PROJECT_ROOT)),
        "log_exists": log_path.exists(),
        "tasks": tasks,
        "active_count": active_count,
        "reserved_count": reserved_count,
        "scheduled_count": scheduled_count,
        "business_tasks": business_tasks or [],
        "business_active_count": len(business_tasks or []),
        "business_tasks_error": business_task_error,
        "inspect_error": None,
        "consumer_warning": consumer_warning,
        "updated_at": datetime.utcnow().isoformat() + "Z",
    }
    if service_id == "report_video":
        report_video_stats, report_video_error = _report_video_stage_stats(force_refresh=force_refresh)
        status["report_video_stats"] = report_video_stats
        status["report_video_stats_error"] = report_video_error
    if include_logs:
        status["logs"] = _tail_file(log_path, lines=log_lines)
    return status


def _stop_service(service_id, service):
    _ensure_dirs()
    processes = _service_processes(service)
    pids = [proc["pid"] for proc in processes]
    starter_pid = _read_pid_file(service_id)
    if starter_pid and starter_pid not in pids and _process_exists(starter_pid):
        pids.append(starter_pid)

    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass

    deadline = time.time() + 3
    while time.time() < deadline:
        if not any(_process_exists(pid) for pid in pids):
            break
        time.sleep(0.2)

    killed = []
    for pid in pids:
        if _process_exists(pid):
            try:
                os.kill(pid, signal.SIGKILL)
                killed.append(pid)
            except OSError:
                pass

    try:
        _pid_file(service_id).unlink(missing_ok=True)
    except OSError:
        pass

    return {"terminated_pids": pids, "force_killed_pids": killed}


def _start_service(service_id, service):
    _ensure_dirs()
    existing = _service_processes(service)
    if existing:
        return {
            "already_running": True,
            "pids": [proc["pid"] for proc in existing],
            "log_file": str(_log_path(service).relative_to(PROJECT_ROOT)),
        }

    script_path = PROJECT_ROOT / service["run_script"]
    if not script_path.exists():
        raise FileNotFoundError(f"启动脚本不存在: {service['run_script']}")

    log_path = _log_path(service)
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env.setdefault("DATABASE_TYPE", "postgresql")

    with log_path.open("ab") as log:
        log.write(f"\n\n===== UI START {datetime.now().isoformat()} service={service_id} =====\n".encode("utf-8"))
        proc = subprocess.Popen(
            [str(script_path)],
            cwd=str(PROJECT_ROOT),
            stdout=log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            env=env,
            start_new_session=True,
        )

    _pid_file(service_id).write_text(str(proc.pid))
    time.sleep(0.8)
    return {
        "started": True,
        "starter_pid": proc.pid,
        "log_file": str(log_path.relative_to(PROJECT_ROOT)),
    }


@bp.route("/services", methods=["GET"])
def list_services():
    return jsonify({
        "success": True,
        "data": [
            {"id": service_id, **service}
            for service_id, service in SERVICES.items()
        ],
    })


def _status_payload(include_logs=False, log_lines=20, include_inspect=False, force_refresh=False):
    _ensure_dirs()

    broker_ok, broker_error = _broker_ping()
    if include_inspect:
        inspect_data, inspect_error = _inspect_tasks_by_queue()
    else:
        inspect_data, inspect_error = {}, "skipped"
    queue_lengths = _queue_lengths()
    all_processes = _list_processes()
    business_tasks, business_task_errors = _business_tasks_snapshot(force_refresh=force_refresh)
    services = [
        _service_status(
            service_id,
            service,
            include_logs=include_logs,
            log_lines=log_lines,
            inspect_data=inspect_data,
            inspect_error=inspect_error,
            queue_lengths=queue_lengths,
            all_processes=all_processes,
            business_tasks=business_tasks.get(service_id),
            business_task_error=business_task_errors.get(service_id),
            force_refresh=force_refresh,
        )
        for service_id, service in SERVICES.items()
    ]
    return {
        "success": True,
        "data": {
            "broker": {
                "ok": broker_ok,
                "error": broker_error,
                "inspect_error": inspect_error,
                "url_masked": _broker_url().split("@")[-1] if "@" in _broker_url() else _broker_url(),
            },
            "services": services,
        },
    }


@bp.route("/status", methods=["GET"])
def status():
    include_logs = request.args.get("logs", "0") in {"1", "true", "yes"}
    include_inspect = request.args.get("inspect", "0") in {"1", "true", "yes"}
    force_refresh = request.args.get("refresh", "0") in {"1", "true", "yes"}
    try:
        log_lines = min(max(int(request.args.get("log_lines", 20)), 1), 200)
    except ValueError:
        log_lines = 20

    cache_key = (include_logs, log_lines, include_inspect)
    cacheable = not include_logs and STATUS_CACHE_TTL_SECONDS > 0
    now = time.time()
    if cacheable and not force_refresh:
        cached = STATUS_CACHE.get("payload")
        if STATUS_CACHE.get("key") == cache_key and cached and STATUS_CACHE.get("expires_at", 0) > now:
            return jsonify(deepcopy(cached))

    if cacheable:
        with STATUS_CACHE_LOCK:
            now = time.time()
            cached = STATUS_CACHE.get("payload")
            if not force_refresh and STATUS_CACHE.get("key") == cache_key and cached and STATUS_CACHE.get("expires_at", 0) > now:
                return jsonify(deepcopy(cached))
            payload = _status_payload(
                include_logs=include_logs,
                log_lines=log_lines,
                include_inspect=include_inspect,
                force_refresh=force_refresh,
            )
            STATUS_CACHE.update({
                "key": cache_key,
                "expires_at": time.time() + STATUS_CACHE_TTL_SECONDS,
                "payload": deepcopy(payload),
            })
            return jsonify(payload)

    return jsonify(_status_payload(
        include_logs=include_logs,
        log_lines=log_lines,
        include_inspect=include_inspect,
        force_refresh=force_refresh,
    ))


@bp.route("/watchdog/status", methods=["GET"])
def watchdog_status():
    return jsonify({"success": True, "data": {"watchdog": _watchdog_status_payload()}})


@bp.route("/watchdog/start", methods=["POST"])
def watchdog_start():
    try:
        result = _start_watchdog()
        result["watchdog"] = _watchdog_status_payload()
        return jsonify({"success": True, "data": result})
    except Exception as exc:
        return jsonify({"success": False, "message": str(exc)}), 500


@bp.route("/watchdog/stop", methods=["POST"])
def watchdog_stop():
    try:
        result = _stop_watchdog()
        result["watchdog"] = _watchdog_status_payload()
        return jsonify({"success": True, "data": result})
    except Exception as exc:
        return jsonify({"success": False, "message": str(exc)}), 500


@bp.route("/watchdog/restart", methods=["POST"])
def watchdog_restart():
    try:
        stop_result = _stop_watchdog()
        time.sleep(0.6)
        start_result = _start_watchdog()
        return jsonify({
            "success": True,
            "data": {
                "stop": stop_result,
                "start": start_result,
                "watchdog": _watchdog_status_payload(),
            },
        })
    except Exception as exc:
        return jsonify({"success": False, "message": str(exc)}), 500


@bp.route("/watchdog/run-once", methods=["POST"])
def watchdog_run_once():
    try:
        result = run_watchdog_once()
        result["watchdog"] = _watchdog_status_payload()
        return jsonify({"success": True, "data": result})
    except Exception as exc:
        return jsonify({"success": False, "message": str(exc)}), 500


@bp.route("/logs/<service_id>", methods=["GET"])
def logs(service_id):
    service, error = _service_or_404(service_id)
    if error:
        return error
    try:
        lines = min(max(int(request.args.get("lines", 120)), 1), 500)
    except ValueError:
        lines = 120
    return jsonify({
        "success": True,
        "data": {
            "service": service_id,
            "log_file": str(_log_path(service).relative_to(PROJECT_ROOT)),
            "logs": _tail_file(_log_path(service), lines=lines),
        },
    })


@bp.route("/start/<service_id>", methods=["POST"])
def start(service_id):
    service, error = _service_or_404(service_id)
    if error:
        return error
    try:
        result = _start_service(service_id, service)
        result["status"] = _service_status(service_id, service, include_logs=True)
        return jsonify({"success": True, "data": result})
    except Exception as exc:
        return jsonify({"success": False, "message": str(exc)}), 500


@bp.route("/stop/<service_id>", methods=["POST"])
def stop(service_id):
    service, error = _service_or_404(service_id)
    if error:
        return error
    try:
        result = _stop_service(service_id, service)
        result["status"] = _service_status(service_id, service, include_logs=True)
        return jsonify({"success": True, "data": result})
    except Exception as exc:
        return jsonify({"success": False, "message": str(exc)}), 500


@bp.route("/restart/<service_id>", methods=["POST"])
def restart(service_id):
    service, error = _service_or_404(service_id)
    if error:
        return error
    try:
        stop_result = _stop_service(service_id, service)
        time.sleep(0.6)
        start_result = _start_service(service_id, service)
        return jsonify({
            "success": True,
            "data": {
                "stop": stop_result,
                "start": start_result,
                "status": _service_status(service_id, service, include_logs=True),
            },
        })
    except Exception as exc:
        return jsonify({"success": False, "message": str(exc)}), 500


@bp.route("/restart-all", methods=["POST"])
def restart_all():
    """Restart all managed Celery workers, watchdog, and currently running schedulers."""
    try:
        data = request.get_json(silent=True) or {}
        start_all_schedulers = bool(data.get("start_all_schedulers"))
        celery_results = {}
        scheduler_results = {}

        watchdog_stop = _stop_watchdog()

        for service_id, service in SERVICES.items():
            celery_results[service_id] = {
                "label": service.get("label") or service_id,
                "stop": _stop_service(service_id, service),
            }

        for scheduler_id, scheduler in SCHEDULERS.items():
            stop_result = _stop_scheduler(scheduler_id, scheduler) or {}
            scheduler_results[scheduler_id] = {
                "label": scheduler.get("label") or scheduler_id,
                "stop": stop_result,
                "start": None,
                "skipped": not (stop_result.get("was_running") or start_all_schedulers),
            }

        time.sleep(0.8)

        for service_id, service in SERVICES.items():
            celery_results[service_id]["start"] = _start_service(service_id, service)

        watchdog_start = _start_watchdog()

        for scheduler_id, scheduler in SCHEDULERS.items():
            item = scheduler_results[scheduler_id]
            if item.get("skipped"):
                continue
            item["start"] = _start_scheduler(scheduler_id, scheduler)

        _clear_status_cache()

        celery_started = sum(
            1
            for item in celery_results.values()
            if _result_has_flag(item.get("start"), "started", "already_running")
        )
        schedulers_started = sum(
            1
            for item in scheduler_results.values()
            if _result_has_flag(item.get("start"), "started", "already_running")
        )
        schedulers_skipped = sum(1 for item in scheduler_results.values() if item.get("skipped"))

        return jsonify({
            "success": True,
            "data": {
                "summary": {
                    "celery_total": len(SERVICES),
                    "celery_started": celery_started,
                    "schedulers_total": len(SCHEDULERS),
                    "schedulers_started": schedulers_started,
                    "schedulers_skipped": schedulers_skipped,
                    "watchdog_started": _result_has_flag(watchdog_start, "started", "already_running"),
                },
                "watchdog": {
                    "stop": watchdog_stop,
                    "start": watchdog_start,
                    "status": _watchdog_status_payload(),
                },
                "celery": celery_results,
                "schedulers": scheduler_results,
            },
        })
    except Exception as exc:
        _clear_status_cache()
        return jsonify({"success": False, "message": str(exc)}), 500


@bp.route("/purge/<service_id>", methods=["POST"])
def purge(service_id):
    service, error = _service_or_404(service_id)
    if error:
        return error
    try:
        result = _purge_queue(service["queue"])
        result["status"] = _service_status(service_id, service, include_logs=True)
        return jsonify({"success": True, "data": result})
    except Exception as exc:
        return jsonify({"success": False, "message": str(exc)}), 500
