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
    "zsxq_sync": {
        "label": "知识星球同步",
        "queue": "zsxq_sync",
        "hostname_prefix": "zsxq_sync@",
        "run_script": "scripts/dev/run-celery-zsxq.sh",
        "log_file": "zsxq_sync.log",
        "description": "知识星球增量同步与附件下载队列",
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

        inspector = celery_app.control.inspect(timeout=1.2)
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
        "inspect_error": inspect_error,
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
