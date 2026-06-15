import json
import os
import signal
import subprocess
import time
from collections import Counter
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


def _service_or_404(service_id):
    service = SERVICES.get(service_id)
    if not service:
        return None, (jsonify({"success": False, "message": "未知 Celery 服务"}), 404)
    return service, None


def _ensure_dirs():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)


def _pid_file(service_id):
    return RUNTIME_DIR / f"{service_id}.pid"


def _watchdog_pid_file():
    return RUNTIME_DIR / "celery_watchdog.pid"


def _log_path(service):
    return LOG_DIR / service["log_file"]


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

        inspector = celery_app.control.inspect(timeout=3.0)
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


def _report_video_stage_stats():
    try:
        from models.report_video import ReportVideoReport, ReportVideoTask
    except Exception as exc:
        return None, f"加载研报模型失败: {exc}"

    try:
        task_statuses = [row[0] for row in ReportVideoTask.query.with_entities(ReportVideoTask.status).all()]
        report_rows = ReportVideoReport.query.with_entities(
            ReportVideoReport.parse_status,
            ReportVideoReport.ai_status,
        ).all()
    except Exception as exc:
        return None, str(exc)

    task_counter = Counter(filter(None, task_statuses))
    parse_counter = Counter(row[0] for row in report_rows if row and row[0])
    ai_counter = Counter(row[1] for row in report_rows if row and row[1])

    return {
        "task_status": dict(task_counter),
        "parse_status": dict(parse_counter),
        "ai_status": dict(ai_counter),
        "totals": {
            "tasks": len(task_statuses),
            "reports": len(report_rows),
        },
    }, None


def _service_status(service_id, service, include_logs=False, log_lines=20, inspect_data=None, inspect_error=None):
    processes = _service_processes(service)
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
        "inspect_error": None,
        "consumer_warning": consumer_warning,
        "updated_at": datetime.utcnow().isoformat() + "Z",
    }
    if service_id == "report_video":
        report_video_stats, report_video_error = _report_video_stage_stats()
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


@bp.route("/status", methods=["GET"])
def status():
    _ensure_dirs()
    include_logs = request.args.get("logs", "0") in {"1", "true", "yes"}
    try:
        log_lines = min(max(int(request.args.get("log_lines", 20)), 1), 200)
    except ValueError:
        log_lines = 20

    broker_ok, broker_error = _broker_ping()
    inspect_data, inspect_error = _inspect_tasks_by_queue()
    services = [
        _service_status(
            service_id,
            service,
            include_logs=include_logs,
            log_lines=log_lines,
            inspect_data=inspect_data,
            inspect_error=inspect_error,
        )
        for service_id, service in SERVICES.items()
    ]
    return jsonify({
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
    })


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
