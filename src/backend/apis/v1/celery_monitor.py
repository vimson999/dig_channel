import json
import os
import re
import shlex
import signal
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

import redis
from flask import Blueprint, jsonify, request


bp = Blueprint("celery_monitor", __name__, url_prefix="/api/v1/celery")

PROJECT_ROOT = Path(__file__).resolve().parents[4]
LOG_DIR = PROJECT_ROOT / "logs" / "celery"
RUNTIME_DIR = PROJECT_ROOT / "runtime" / "celery"
WATCHDOG_STATE_FILE = RUNTIME_DIR / "watchdog_state.json"
PAUSED_SERVICES_FILE = RUNTIME_DIR / "paused_services.json"
WATCHDOG_LOG_FILE = LOG_DIR / "celery_watchdog.log"
WATCHDOG_SCRIPT = PROJECT_ROOT / "scripts" / "dev" / "run-celery-watchdog.sh"
DEFAULT_WATCHDOG_SERVICES = (
    "render",
    "platform_upload",
    "cloud_tts",
    "doubao_browser_tts",
    "report_pdf_gemini",
    "report_audio_workflow",
    "report_script_material_workflow",
    "report_visual_plan",
    "report_top10_workflow",
    "mp3_video_workflow",
    "zsxq_sync",
    "zsxq_workflow",
    "zsxq_publish",
    "podcast_visualizer",
    "report_video",
    "content_pipeline",
)

BUSINESS_TASK_FEATURES = {
    "render": ("publisher", "report_stick_figure_video_render", "report_stick_figure_audio_alignment"),
    "platform_upload": ("publisher", "douyin_upload_test", "wechat_channels_upload_test"),
    "doubao_browser_tts": ("doubao_browser_tts",),
    "report_pdf_gemini": ("report_pdf_gemini", "gemini_browser", "report_stick_figure_image"),
    "report_audio_workflow": ("report_audio_workflow", "report_pdf_audio_workflow"),
    "report_script_material_workflow": ("report_script_material_workflow",),
    "report_visual_plan": ("report_visual_plan",),
    "report_top10_workflow": ("report_top10_workflow",),
    "mp3_video_workflow": ("mp3_video_workflow",),
    "content_pipeline": ("content_pdf_clean", "content_audio_clean"),
    "zsxq_publish": ("zsxq_clean_output_publish",),
}
ZSXQ_BUSINESS_TASK_SERVICES = ("zsxq_sync", "zsxq_workflow")
ZSXQ_ACTIVE_STATUSES = ("queued", "running", "retrying", "pending", "processing")
ZSXQ_ALL_TABS_MODES = ("auto_all_tabs_sc", "auto_all_tabs_hh")
ZSXQ_ORPHANABLE_STATUSES = ("running", "retrying")
BUSINESS_TASK_ACTIVE_STATUSES = ("pending", "processing")
BUSINESS_TASK_CONSUMER_GRACE_SECONDS_DEFAULT = 1800
BUSINESS_TASK_LIMIT_PER_SERVICE = 8
NON_INTERRUPTIBLE_SERVICE_STEPS = {
    "platform_upload": ("upload_bilibili", "upload_youtube", "uploading_video", "filling_copy", "submitting"),
}
INTERRUPTIBLE_BUSINESS_STEPS = {
    "queued",
    "pending",
    "waiting",
    "orphaned_processing",
    "deferred_capacity",
    "deferred_backlog",
    "deferred_db_pressure",
}
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
        "description": "publisher / video-editor / 火柴人视频主渲染队列",
    },
    "platform_upload": {
        "label": "平台上传",
        "queue": "platform_upload",
        "hostname_prefix": "platform_upload@",
        "run_script": "scripts/dev/run-celery-platform-upload.sh",
        "log_file": "platform_upload.log",
        "description": "B站 / YouTube / 抖音 / 视频号后投上传队列，不占用视频合成 render 队列",
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
        "label": "豆包浏览器朗读捕获池",
        "queue": "doubao_browser_tts",
        "hostname_prefix": "doubao_browser_tts",
        "run_script": "scripts/dev/run-celery-doubao-browser-tts-pool.sh",
        "log_file": "doubao_browser_tts.log",
        "description": "多个独立 Chrome profile 共同消费豆包网页朗读捕获队列，单个 profile 内仍保持串行",
    },
    "report_pdf_gemini": {
        "label": "研报 PDF Gem 解析池",
        "queue": "report_pdf_gemini",
        "hostname_prefix": "report_pdf_gemini@",
        "run_script": "scripts/dev/run-celery-report-pdf-gemini-pool.sh",
        "log_file": "report_pdf_gemini.log",
        "description": "多个独立 Chrome profile 共同消费研报解析 Gem 队列，单个 profile 内仍保持串行",
    },
    "report_audio_workflow": {
        "label": "研报转语音编排",
        "queue": "report_audio_workflow",
        "hostname_prefix": "report_audio_workflow@",
        "run_script": "scripts/dev/run-celery-report-audio-workflow.sh",
        "log_file": "report_audio_workflow.log",
        "description": "调度研报 PDF Gem 解析、自审结构化、豆包浏览器朗读并汇总音频",
    },
    "report_script_material_workflow": {
        "label": "研报基础文案物料",
        "queue": "report_script_material_workflow",
        "hostname_prefix": "report_script_material_workflow@",
        "run_script": "scripts/dev/run-celery-report-script-material-workflow.sh",
        "log_file": "report_script_material_workflow.log",
        "description": "调度研报 PDF Gem 解析并生成可复用口播文案物料，不占用完整研报转语音编排容量",
    },
    "report_visual_plan": {
        "label": "研报分镜梳理",
        "queue": "report_visual_plan",
        "hostname_prefix": "report_visual_plan@",
        "run_script": "scripts/dev/run-celery-report-visual-plan-pool.sh",
        "log_file": "report_visual_plan.log",
        "description": "多个独立 Gemini profile 并行消费分镜任务；单个 profile 内保持串行，生成完整覆盖的结构化分镜列表",
    },
    "report_top10_workflow": {
        "label": "今日研报 Top10 编排",
        "queue": "report_top10_workflow",
        "hostname_prefix": "report_top10_workflow@",
        "run_script": "scripts/dev/run-celery-report-top10-workflow.sh",
        "log_file": "report_top10_workflow.log",
        "description": "调度 10 篇研报单篇文案缓存/解析，并提交总编辑 Gem 生成 Top10 栏目稿",
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
    "workflow_notify": {
        "label": "工作流通知",
        "queue": "workflow_notify",
        "hostname_prefix": "workflow_notify@",
        "run_script": "scripts/dev/run-celery-workflow-notify.sh",
        "log_file": "workflow_notify.log",
        "description": "邮件等工作流通知与失败重试，独立于知识星球业务编排队列",
        "auto_heal": False,
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
        "description": "前沿系统编排，以及生财有术/洪灝每 3 小时全部 Tab 自动同步",
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
    "bilibili_charging_scheduler": {
        "label": "B站充电人数采集调度器",
        "pattern": "run_bilibili_charging_scheduler.py",
        "argv": [
            "venv/bin/python",
            "src/backend/run_bilibili_charging_scheduler.py",
            "start",
        ],
        "log_file": "bilibili_charging_scheduler.log",
        "description": "每天 00:00（Asia/Shanghai）刷新首页 B站 UP 主充电人数并发送节点邮件",
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
        "label": "YouTube 频道数据同步调度器",
        "pattern": "run_youtube_channel_sync_scheduler.py",
        "argv": [
            "venv/bin/python",
            "src/backend/run_youtube_channel_sync_scheduler.py",
            "start",
        ],
        "log_file": "youtube_channel_sync_scheduler.log",
        "description": "每天 00:03（Asia/Shanghai）执行首页全部启用频道同步并发送节点邮件",
    },
    "youtube_video_sync_scheduler": {
        "label": "YouTube 视频数据同步调度器",
        "pattern": "run_youtube_video_sync_scheduler.py",
        "argv": [
            "venv/bin/python",
            "src/backend/run_youtube_video_sync_scheduler.py",
            "start",
        ],
        "log_file": "youtube_video_sync_scheduler.log",
        "description": "每天 01:00（Asia/Shanghai）同步全部启用频道最近 3 天视频并发送节点邮件",
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


def _apply_db_process_role(env, role):
    normalized_role = "scheduler" if role == "watchdog" else role
    env["DB_PROCESS_ROLE"] = normalized_role
    env.setdefault("DATABASE_TYPE", "postgresql")
    env.setdefault("DB_POOL_MODE", "queue")
    env.setdefault("DB_POOL_SIZE", "1")
    env.setdefault("DB_MAX_OVERFLOW", "0")
    env.setdefault("DB_CONNECT_TIMEOUT", "5")
    env.setdefault("WEB_DB_POOL_MODE", "queue")
    env.setdefault("WEB_DB_POOL_SIZE", "4")
    env.setdefault("WEB_DB_MAX_OVERFLOW", "0")
    env.setdefault("WORKER_DB_POOL_MODE", "null")
    env.setdefault("WORKER_DB_POOL_SIZE", "1")
    env.setdefault("WORKER_DB_MAX_OVERFLOW", "0")
    env.setdefault("SCHEDULER_DB_POOL_MODE", "null")
    env.setdefault("SCHEDULER_DB_POOL_SIZE", "1")
    env.setdefault("SCHEDULER_DB_MAX_OVERFLOW", "0")
    return env


STATUS_CACHE_TTL_SECONDS = float(os.environ.get("CELERY_MONITOR_STATUS_CACHE_TTL", "20"))
REPORT_VIDEO_STATS_CACHE_TTL_SECONDS = float(os.environ.get("CELERY_MONITOR_REPORT_VIDEO_STATS_CACHE_TTL", "60"))
BUSINESS_TASKS_CACHE_TTL_SECONDS = float(os.environ.get("CELERY_MONITOR_BUSINESS_TASKS_CACHE_TTL", "75"))
BUSINESS_TASKS_AUTO_CLEAN_STALE = _truthy_env("CELERY_MONITOR_AUTO_CLEAN_STALE_BUSINESS_TASKS", False)
BUSINESS_TASKS_STALE_PENDING_SECONDS = max(3600, _int_env("CELERY_MONITOR_STALE_PENDING_MINUTES", 120) * 60)
BUSINESS_TASKS_STALE_PROCESSING_SECONDS = max(7200, _int_env("CELERY_MONITOR_STALE_PROCESSING_MINUTES", 240) * 60)
BUSINESS_TASKS_QUEUE_SCAN_LIMIT = max(20, _int_env("CELERY_MONITOR_BUSINESS_TASK_QUEUE_SCAN_LIMIT", 1000))
ZSXQ_ALL_TABS_ORPHAN_SECONDS = max(
    300,
    _int_env("CELERY_MONITOR_ZSXQ_ORPHAN_MINUTES", 10) * 60,
)
ZSXQ_SYNC_RESTART_GUARD_SECONDS = max(
    60,
    _int_env("CELERY_MONITOR_ZSXQ_RESTART_GUARD_MINUTES", 5) * 60,
)
STATUS_CACHE = {
    "key": None,
    "expires_at": 0.0,
    "refreshed_at": 0.0,
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
            "refreshed_at": 0.0,
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


def _release_db_session_quietly():
    try:
        from models import db
    except Exception:
        return
    try:
        db.session.rollback()
    except Exception:
        pass
    try:
        db.session.remove()
    except Exception:
        pass


def _ensure_dirs():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)


def _env_paused_services():
    raw = os.environ.get("CELERY_PAUSED_SERVICES", "")
    if not raw:
        return set()
    if raw.strip().lower() in {"none", "off", "false", "0"}:
        return set()
    return {item.strip() for item in raw.split(",") if item.strip() in SERVICES}


def _load_paused_services():
    paused = {}
    if PAUSED_SERVICES_FILE.exists():
        try:
            data = json.loads(PAUSED_SERVICES_FILE.read_text())
            if isinstance(data, dict):
                paused.update({
                    key: value if isinstance(value, dict) else {"reason": str(value)}
                    for key, value in data.items()
                    if key in SERVICES
                })
        except (OSError, json.JSONDecodeError):
            paused = {}
    for service_id in _env_paused_services():
        paused.setdefault(service_id, {
            "reason": "CELERY_PAUSED_SERVICES",
            "source": "env",
        })
    return paused


def _is_service_paused(service_id):
    return service_id in _load_paused_services()


def _set_service_paused(service_id, paused, reason=None):
    if service_id not in SERVICES:
        raise ValueError(f"未知 Celery 服务: {service_id}")
    _ensure_dirs()
    data = {}
    if PAUSED_SERVICES_FILE.exists():
        try:
            raw = json.loads(PAUSED_SERVICES_FILE.read_text())
            if isinstance(raw, dict):
                data.update(raw)
        except (OSError, json.JSONDecodeError):
            data = {}
    if paused:
        data[service_id] = {
            "paused_at": datetime.utcnow().isoformat() + "Z",
            "reason": reason or "manual",
        }
    else:
        data.pop(service_id, None)
    PAUSED_SERVICES_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    _clear_status_cache()
    return _load_paused_services()


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
    matched = []
    for proc in _list_processes():
        command = proc.get("command", "")
        try:
            argv = shlex.split(command)
        except ValueError:
            argv = command.split()
        if any(Path(token).name == pattern for token in argv):
            matched.append(proc)
    return matched


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
    env["SCHEDULER_STDOUT_REDIRECTED_TO_LOG"] = "1"
    _apply_db_process_role(env, "scheduler")
    env["PYTHONPATH"] = str(PROJECT_ROOT / "src" / "backend")
    env["ZSXQ_PROJECT_ROOT"] = str(PROJECT_ROOT)

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
    _apply_db_process_role(env, "watchdog")
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
        if raw.strip().lower() == "all":
            service_ids = list(SERVICES.keys())
        else:
            service_ids = [item.strip() for item in raw.split(",") if item.strip()]
    else:
        service_ids = list(DEFAULT_WATCHDOG_SERVICES)
    return [
        service_id
        for service_id in service_ids
        if service_id in SERVICES and SERVICES[service_id].get("auto_heal", True)
    ]


def _watchdog_config():
    paused = _load_paused_services()
    return {
        "enabled": _truthy_env("CELERY_AUTO_HEAL_ENABLED", True),
        "notify_enabled": _truthy_env("CELERY_AUTO_HEAL_NOTIFY_ENABLED", True),
        "requeue_stale_tasks": _truthy_env("CELERY_AUTO_HEAL_REQUEUE_STALE_TASKS", True),
        "services": _watchdog_services(),
        "paused_services": sorted(paused.keys()),
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


def _worker_process_definitively_missing(status):
    """Return true only when process inspection confirms no worker exists.

    A stale database row must normally keep the stop guard closed.  When the
    service has no process at all, however, starting a replacement cannot
    interrupt that row and is the safe way to let the broker redeliver it.
    """
    return (
        status.get("running") is False
        and not status.get("pids")
        and not status.get("processes")
    )


def _watchdog_business_activity_probe(service_id):
    """Narrow fail-closed heartbeat check for a queue suspected to be stuck."""
    try:
        tasks = _active_non_interruptible_service_tasks(service_id)
    except Exception as exc:
        return [], str(exc)
    finally:
        _release_db_session_quietly()

    errors = [
        str(item.get("error"))
        for item in tasks
        if isinstance(item, dict) and item.get("error")
    ]
    if errors:
        return tasks, "；".join(errors)[:1000]
    return tasks, None


def _parse_iso_datetime(value):
    if not value:
        return None
    try:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo:
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed
    except (TypeError, ValueError):
        return None


def _business_tasks_recently_active(tasks, now=None):
    now = now or datetime.utcnow()
    grace_seconds = max(
        60,
        _int_env("CELERY_AUTO_HEAL_BUSINESS_GRACE_SECONDS", BUSINESS_TASK_CONSUMER_GRACE_SECONDS_DEFAULT),
    )
    for task in tasks or []:
        status = str(task.get("status") or "").lower()
        if status not in {"processing", "running"}:
            continue
        updated_at = _parse_iso_datetime(task.get("updated_at") or task.get("created_at"))
        if not updated_at:
            return True
        if (now - updated_at).total_seconds() <= grace_seconds:
            return True
    return False


def _paused_business_features():
    paused = _load_paused_services()
    features = set()
    for service_id in paused.keys():
        features.update(BUSINESS_TASK_FEATURES.get(service_id, ()))
    return features


def _isolated_watchdog_repair(name, callback, *, fallback, args=(), kwargs=None):
    """Run one repairer without allowing it to starve the remaining repairs."""

    try:
        return callback(*(args or ()), **(kwargs or {}))
    except Exception as exc:
        # Each repairer owns an independent idempotent claim.  A broken DB
        # session or an optional dependency in one repairer must not prevent
        # the watchdog from healing unrelated queues during the same tick.
        _release_db_session_quietly()
        result = dict(fallback or {})
        result.update({"error": str(exc), "failed_repair": str(name)})
        return result


def _watchdog_repair_stale_business_tasks(config):
    if not config.get("enabled"):
        return {"enabled": False, "skipped": "auto_heal_disabled"}
    if not config.get("requeue_stale_tasks"):
        return {"enabled": False, "skipped": "requeue_disabled"}

    try:
        from services.publisher_queue_repair import (
            cleanup_publisher_children_of_terminal_parents_once,
            repair_stale_business_processing_tasks_once,
            repair_stale_publication_tasks_once,
            repair_stale_publisher_pending_tasks_once,
            repair_stale_publisher_processing_tasks_once,
        )
        from tasks.report_stick_figure_video import (
            repair_ready_stick_figure_video_workflows_once,
        )
        from services.report_audio_finalization import (
            repair_pending_finalizations_once,
        )
        from services.report_audio_recovery import (
            repair_failed_report_audio_workflows_once,
        )

        excluded_features = _paused_business_features()
        if {"report_audio_workflow", "report_pdf_audio_workflow"} & excluded_features:
            report_audio_failed_recovery = {
                "enabled": False,
                "skipped": "report_audio_paused",
                "requeued": 0,
                "items": [],
            }
        else:
            # Claim retryable failed parents before terminal-parent cleanup so
            # a still-useful child is not canceled merely because the parent
            # hit a transient terminal state between watchdog ticks.
            report_audio_failed_recovery = _isolated_watchdog_repair(
                "report_audio_failed_recovery",
                repair_failed_report_audio_workflows_once,
                fallback={"enabled": True, "requeued": 0, "items": []},
            )
        publisher_repair_paused = "publisher" in excluded_features
        if publisher_repair_paused:
            cleanup_result = {"enabled": False, "skipped": "publisher_paused", "cleaned": 0}
            publisher_pending_repair_result = {"enabled": False, "skipped": "publisher_paused", "requeued": 0}
            processing_repair_result = {
                "enabled": False,
                "skipped": "publisher_paused",
                "requeued": 0,
                "demoted": 0,
                "closed": 0,
            }
        else:
            cleanup_result = _isolated_watchdog_repair(
                "publisher_terminal_child_cleanup",
                cleanup_publisher_children_of_terminal_parents_once,
                fallback={"enabled": True, "cleaned": 0, "items": []},
            )
            publisher_pending_repair_result = _isolated_watchdog_repair(
                "publisher_pending_repair",
                repair_stale_publisher_pending_tasks_once,
                fallback={"enabled": True, "requeued": 0, "items": []},
            )
            processing_repair_result = _isolated_watchdog_repair(
                "publisher_processing_repair",
                repair_stale_publisher_processing_tasks_once,
                fallback={
                    "enabled": True,
                    "requeued": 0,
                    "demoted": 0,
                    "closed": 0,
                    "items": [],
                },
            )
        repair_result = _isolated_watchdog_repair(
            "publication_pending_repair",
            repair_stale_publication_tasks_once,
            fallback={"enabled": True, "requeued": 0, "items": []},
            kwargs={"excluded_features": excluded_features},
        )
        business_processing_repair_result = _isolated_watchdog_repair(
            "business_processing_repair",
            repair_stale_business_processing_tasks_once,
            fallback={
                "enabled": True,
                "requeued": 0,
                "demoted": 0,
                "closed": 0,
                "items": [],
            },
            kwargs={"excluded_features": excluded_features},
        )
        if "report_stick_figure_video_render" in excluded_features:
            visual_video_repair_result = {
                "enabled": False,
                "skipped": "render_paused",
                "enqueued": 0,
                "items": [],
            }
        else:
            visual_video_repair_result = _isolated_watchdog_repair(
                "visual_video_repair",
                repair_ready_stick_figure_video_workflows_once,
                fallback={"enabled": True, "enqueued": 0, "items": []},
            )
        if "publisher" in excluded_features or "report_stick_figure_video_render" in excluded_features:
            finalization_repair_result = {
                "enabled": False,
                "skipped": "dependency_paused",
                "reconciled": 0,
                "items": [],
            }
        else:
            finalization_repair_result = _isolated_watchdog_repair(
                "report_audio_finalization_repair",
                repair_pending_finalizations_once,
                fallback={"enabled": True, "reconciled": 0, "items": []},
            )
        repair_result["publisher_pending_repair"] = publisher_pending_repair_result
        repair_result["processing_repair"] = processing_repair_result
        repair_result["business_processing_repair"] = business_processing_repair_result
        repair_result["visual_video_repair"] = visual_video_repair_result
        repair_result["report_audio_finalization_repair"] = finalization_repair_result
        repair_result["report_audio_failed_recovery"] = report_audio_failed_recovery
        repair_result["requeued"] = (
            (repair_result.get("requeued") or 0)
            + (publisher_pending_repair_result.get("requeued") or 0)
            + (processing_repair_result.get("requeued") or 0)
            + (business_processing_repair_result.get("requeued") or 0)
            + (visual_video_repair_result.get("enqueued") or 0)
            + (report_audio_failed_recovery.get("requeued") or 0)
        )
        repair_result["demoted"] = (
            (processing_repair_result.get("demoted") or 0)
            + (business_processing_repair_result.get("demoted") or 0)
        )
        repair_result["closed"] = (
            (processing_repair_result.get("closed") or 0)
            + (business_processing_repair_result.get("closed") or 0)
        )
        repair_result["child_cleanup"] = cleanup_result
        return repair_result
    except Exception as exc:
        return {"enabled": True, "error": str(exc), "requeued": 0, "items": []}
    finally:
        _release_db_session_quietly()


def run_watchdog_once():
    """Detect stuck Celery consumers and optionally restart the affected worker."""
    _ensure_dirs()
    config = _watchdog_config()
    state = _load_watchdog_state()
    services_state = state.setdefault("services", {})
    now = time.time()
    repair_result = _watchdog_repair_stale_business_tasks(config)
    cleanup_count = ((repair_result.get("child_cleanup") or {}).get("cleaned") or 0)
    closed_count = repair_result.get("closed") or 0
    demoted_count = repair_result.get("demoted") or 0
    if repair_result.get("requeued") or cleanup_count or closed_count or demoted_count:
        _watchdog_event(
            state,
            "business_task_repair",
            "requeued",
            (
                f"已自动重投 {repair_result.get('requeued') or 0} 个业务孤儿任务，"
                f"降回排队 {demoted_count} 个 processing 孤儿任务，"
                f"收口 {cleanup_count} 个父任务已终止的子任务，"
                f"关闭 {closed_count} 个消失的 processing 任务。"
            ),
            {"repair": repair_result},
        )
        _clear_status_cache()
    inspect_data, inspect_error = _inspect_tasks_by_queue()
    results = []
    # Worker recovery is decided from process state, queue depth and Celery
    # inspect. Business-task snapshots are dashboard detail only; loading them
    # in the minute watchdog duplicated remote DB polling without changing a
    # restart decision. Repair functions above retain their own narrow reads.
    business_tasks = {service_id: [] for service_id in config["services"]}
    business_task_errors = {service_id: None for service_id in config["services"]}

    for service_id in config["services"]:
        service = SERVICES[service_id]
        paused = _load_paused_services().get(service_id)
        if paused:
            item_state = services_state.setdefault(service_id, {})
            item_state["stuck_count"] = 0
            item_state["first_seen_at"] = None
            item_state["last_seen_at"] = None
            results.append({
                "service_id": service_id,
                "label": service["label"],
                "stuck": False,
                "action": "paused",
                "paused": paused,
                "warning": "服务已暂停，看门狗不会自动拉起。",
            })
            continue
        status = _service_status(
            service_id,
            service,
            include_logs=False,
            inspect_data=inspect_data,
            inspect_error=inspect_error,
            business_tasks=business_tasks.get(service_id),
            business_task_error=business_task_errors.get(service_id),
            force_refresh=True,
        )
        if _consumer_stuck(status):
            heartbeat_tasks, heartbeat_error = _watchdog_business_activity_probe(
                service_id
            )
            status = _service_status(
                service_id,
                service,
                include_logs=False,
                inspect_data=inspect_data,
                inspect_error=inspect_error,
                business_tasks=heartbeat_tasks,
                business_task_error=heartbeat_error,
                force_refresh=True,
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

        # Do not let a stale `processing` row prevent recovery when process
        # inspection has already proved that this service has no worker.  We
        # intentionally start without calling the stop path: there is nothing
        # to interrupt, and _start_service rechecks for a racing worker.
        if _worker_process_definitively_missing(status):
            start_result = _start_service(service_id, service, clear_pause=False)
            action = (
                "started_missing_worker"
                if start_result.get("started")
                else "worker_appeared"
            )
            item_state["last_action_ts"] = now
            item_state["last_action_at"] = datetime.utcnow().isoformat() + "Z"
            item_state["stuck_count"] = 0
            item_state["first_seen_at"] = None
            item_state["last_seen_at"] = None
            result.update({
                "action": action,
                "start": start_result,
            })
            _watchdog_event(
                state,
                service_id,
                action,
                f"{service['label']} 未发现存活 worker，已请求启动替代 worker。",
                {"result": result},
            )
            results.append(result)
            continue

        subject = f"[Celery自愈] 正在自动重启 worker · {service['label']}"
        try:
            stop_result = _stop_service(service_id, service)
            if stop_result.get("skipped"):
                item_state["stuck_count"] = 0
                item_state["first_seen_at"] = None
                item_state["last_seen_at"] = None
                result.update({
                    "action": "restart_skipped",
                    "stop": stop_result,
                    "warning": None,
                })
                _watchdog_event(
                    state,
                    service_id,
                    "restart_skipped",
                    (
                        f"{service['label']} 检测到活动业务任务，"
                        "安全门已跳过自动重启。"
                    ),
                    {"result": result},
                )
                results.append(result)
                continue
            time.sleep(0.6)
            start_result = _start_service(service_id, service, clear_pause=False)
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
        "task_repair": repair_result,
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

        def request_state(state_name):
            # Each broadcast owns its connection/reply queue. Busy solo workers
            # can delay a reply, so serial active/reserved/scheduled requests
            # multiply the control-plane tail latency.
            inspector = celery_app.control.inspect(
                timeout=timeout,
                limit=len(SERVICES),
            )
            return getattr(inspector, state_name)() or {}

        state_names = ("active", "reserved", "scheduled")
        with ThreadPoolExecutor(max_workers=len(state_names)) as executor:
            futures = {
                state_name: executor.submit(request_state, state_name)
                for state_name in state_names
            }
            replies = {
                state_name: futures[state_name].result()
                for state_name in state_names
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
    for service_id in _business_task_service_ids():
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
        unacked_length = int(client.hlen("unacked"))
        if unacked_length > BUSINESS_TASKS_QUEUE_SCAN_LIMIT:
            return refs, (
                f"Celery unacked 数量 {unacked_length} 超过扫描上限 "
                f"{BUSINESS_TASKS_QUEUE_SCAN_LIMIT}，跳过历史残留自动收口"
            )
        for _delivery_tag, item in client.hscan_iter(
            "unacked",
            count=min(BUSINESS_TASKS_QUEUE_SCAN_LIMIT, 200),
        ):
            refs.update(_uuid_refs_from_value(item))
    except Exception as exc:
        return refs, f"Redis 队列扫描失败: {exc}"

    celery_refs, inspect_error = _zsxq_celery_runtime_refs()
    if inspect_error:
        return refs, f"Celery 运行态检查失败: {inspect_error}"
    refs.update(celery_refs)
    return refs, None


def _zsxq_celery_runtime_refs():
    refs = set()
    inspect_data, inspect_error = _inspect_tasks_by_queue()
    if inspect_error:
        return refs, inspect_error
    for service_id in _business_task_service_ids():
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
    finally:
        _release_db_session_quietly()


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
    finally:
        _release_db_session_quietly()

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
                PublicationTask.waiting_child_label.label("waiting_child_label"),
                PublicationTask.waiting_child_id.label("waiting_child_id"),
                PublicationTask.waiting_child_status.label("waiting_child_status"),
                PublicationTask.waiting_child_step.label("waiting_child_step"),
                PublicationTask.waiting_child_updated_at.label("waiting_child_updated_at"),
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
    finally:
        _release_db_session_quietly()

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
            if (
                not _business_task_matches_service(row, service_id, feature_names)
                or len(buckets[service_id]) >= BUSINESS_TASK_LIMIT_PER_SERVICE
            ):
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
    _release_db_session_quietly()
    return buckets, errors


def _business_task_matches_service(row, service_id, feature_names):
    if row.feature not in feature_names:
        return False
    if row.feature == "publisher":
        is_upload = _business_task_is_platform_upload(row)
        if service_id == "platform_upload":
            return is_upload
        if service_id == "render":
            return not is_upload
    return True


def _business_task_is_platform_upload(row):
    params = row.params if isinstance(getattr(row, "params", None), dict) else {}
    metadata = params.get("metadata") if isinstance(params.get("metadata"), dict) else {}
    source = str(metadata.get("source") or "").strip()
    if source in {"mp3_video_workflow:bilibili_upload"}:
        return True

    video = params.get("video") if isinstance(params.get("video"), dict) else {}
    bilibili = params.get("bilibili") if isinstance(params.get("bilibili"), dict) else {}
    youtube = params.get("youtube") if isinstance(params.get("youtube"), dict) else {}
    if (
        str(video.get("media_type") or "") == "existing_video"
        and video.get("enabled") is False
        and (bool(bilibili.get("enabled")) or bool(youtube.get("enabled")))
    ):
        return True

    step = str(getattr(row, "current_step", None) or "").strip().lower()
    return step in {"upload_bilibili", "upload_youtube"}


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
        sync_rows, cleanup_error = _auto_close_orphaned_zsxq_all_tabs_runs(sync_rows)
        if cleanup_error:
            errors["zsxq_sync"] = f"星球同步残留任务自动收口失败: {cleanup_error}"
        buckets["zsxq_sync"] = [_zsxq_sync_task_payload(row) for row in sync_rows]
    except Exception as exc:
        errors["zsxq_sync"] = f"星球同步运行记录读取失败: {exc}"
    finally:
        _release_db_session_quietly()

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
    finally:
        _release_db_session_quietly()

    return buckets, errors


def _zsxq_all_tabs_orphan_candidates(rows, now=None):
    now = now or datetime.utcnow()
    candidates = []
    for row in rows or []:
        if str(row.mode or "") not in ZSXQ_ALL_TABS_MODES:
            continue
        if str(row.status or "") not in ZSXQ_ORPHANABLE_STATUSES:
            continue
        marker = row.updated_at or row.created_at
        if not marker:
            continue
        if (now - marker).total_seconds() >= ZSXQ_ALL_TABS_ORPHAN_SECONDS:
            candidates.append(row)
    return candidates


def _auto_close_orphaned_zsxq_all_tabs_runs(rows):
    candidates = _zsxq_all_tabs_orphan_candidates(rows)
    if not candidates:
        return rows, None

    # A stale running/retrying row must be backed by a task the worker can
    # actually report as active, reserved, or scheduled. Redis ``unacked`` can
    # outlive a force-killed worker until the visibility timeout; treating that
    # broker residue as live would leave the business row stuck indefinitely.
    # A later redelivery is harmless because the task rejects terminal runs.
    runtime_refs, runtime_error = _zsxq_celery_runtime_refs()
    if runtime_error:
        return rows, runtime_error

    orphan_ids = [
        str(row.id)
        for row in candidates
        if row.id and str(row.id) not in runtime_refs
    ]
    if not orphan_ids:
        return rows, None

    try:
        from services.zsxq_sync_service import zsxq_sync_service

        reason = (
            "同步心跳已超过阈值，且 Redis 队列与 Celery active/reserved/scheduled "
            "均无对应任务；Celery Monitor 自动收口为 failed。"
        )
        for run_id in orphan_ids:
            zsxq_sync_service.mark_run_failed(
                run_id,
                reason,
                {
                    "interrupted": True,
                    "queued": False,
                    "running": False,
                    "stale_cleanup": {
                        "cleaned_at": datetime.utcnow().isoformat() + "Z",
                        "reason": reason,
                        "auto_cleaned_by": "celery_monitor",
                    },
                },
            )
    except Exception as exc:
        _release_db_session_quietly()
        return rows, str(exc)

    orphan_id_set = set(orphan_ids)
    return [row for row in rows if str(row.id) not in orphan_id_set], None


def _zsxq_sync_task_payload(row):
    task_id = str(row.id or "")
    mode_labels = {
        "auto_all_tabs_sc": "全部 Tab 定时同步",
        "auto_all_tabs_hh": "全部 Tab 定时同步",
    }
    mode_label = mode_labels.get(str(row.mode or ""), "同步")
    title = f"{row.group_name or row.group_id or '知识星球'} · {mode_label}"
    if row.system_batch_id:
        title = f"{title} {row.system_batch_id}"
    details = [
        row.tab_label or mode_labels.get(str(row.mode or ""), row.mode),
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
    if status in {"running", "retrying", "processing"}:
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
        "waiting_child": _business_task_waiting_child_payload(row),
        "error": row.error_message,
        "created_at": row.created_at.isoformat() + "Z" if row.created_at else None,
        "updated_at": row.updated_at.isoformat() + "Z" if row.updated_at else None,
        "completed_at": row.completed_at.isoformat() + "Z" if row.completed_at else None,
    }


def _business_task_waiting_child_payload(row):
    if not getattr(row, "waiting_child_id", None):
        return None
    return {
        "label": row.waiting_child_label,
        "child_id": row.waiting_child_id,
        "status": row.waiting_child_status,
        "step": row.waiting_child_step,
        "updated_at": row.waiting_child_updated_at,
    }


def _business_task_fallback_title(row):
    labels = {
        "publisher": "视频合成任务",
        "report_audio_workflow": "研报转语音编排",
        "report_pdf_audio_workflow": "研报转语音编排",
        "report_top10_workflow": "今日研报 Top10 编排",
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
    paused = _load_paused_services().get(service_id)
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
    consumer_note = None
    inspect_blocked = bool(inspect_error and inspect_error != "skipped")
    business_recently_active = _business_tasks_recently_active(business_tasks)
    if paused and not processes:
        consumer_note = "服务已暂停；看门狗不会自动拉起该队列 worker。"
    elif paused and processes:
        consumer_note = "服务已暂停，但当前仍有 worker 进程在线。"
    elif not processes:
        consumer_warning = "Worker 未运行；看门狗将按自愈策略拉起该队列 worker。"
    elif (
        processes
        and queue_len
        and queue_len > 0
        and active_count == 0
        and reserved_count == 0
        and scheduled_count == 0
        and not inspect_blocked
    ):
        if business_task_error:
            consumer_note = (
                "队列有积压，但业务心跳复核失败；为避免中断活动任务，"
                "本轮禁止告警和自动重启。"
            )
        elif business_recently_active:
            consumer_note = (
                "Worker 正在处理业务任务；solo Worker 忙碌时可能无法及时响应 "
                "Celery inspect，当前不判定为消费卡死。"
            )
        else:
            consumer_warning = (
                "Worker 在线但未领取积压任务，消费连接可能卡住；"
                "建议重启该队列 worker。"
            )

    status = {
        "id": service_id,
        "label": service["label"],
        "queue": service["queue"],
        "description": service["description"],
        "running": bool(processes),
        "paused": bool(paused),
        "pause": paused,
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
        "inspect_error": inspect_error,
        "consumer_warning": consumer_warning,
        "consumer_note": consumer_note,
        "updated_at": datetime.utcnow().isoformat() + "Z",
    }
    if service_id == "report_video":
        report_video_stats, report_video_error = _report_video_stage_stats(force_refresh=force_refresh)
        status["report_video_stats"] = report_video_stats
        status["report_video_stats_error"] = report_video_error
    if include_logs:
        status["logs"] = _tail_file(log_path, lines=log_lines)
    return status


def _stop_service(service_id, service, *, pause=False, reason=None, force=False):
    _ensure_dirs()
    guard = None
    if not force:
        guard = _service_stop_guard(service_id)
    _release_db_session_quietly()
    if guard:
        return {
            **guard,
            "terminated_pids": [],
            "force_killed_pids": [],
            "paused": None,
        }

    pause_state = None
    if pause:
        pause_state = _set_service_paused(service_id, True, reason=reason or "manual_stop")
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

    graceful_timeout = max(3, _int_env("CELERY_STOP_GRACE_SECONDS", 10))
    deadline = time.time() + graceful_timeout
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

    return {"terminated_pids": pids, "force_killed_pids": killed, "paused": pause_state.get(service_id) if pause_state else None}


def _service_stop_guard(service_id, *, rows=None):
    active_tasks = _active_non_interruptible_service_tasks(service_id, rows=rows)
    if not active_tasks:
        return None
    return {
        "skipped": True,
        "reason": "active_non_interruptible_tasks",
        "message": "该队列有不可中断的业务任务正在进行，已跳过停止/重启以避免产生孤儿任务。",
        "active_tasks": active_tasks,
    }


def _active_non_interruptible_service_tasks(service_id, *, rows=None):
    if service_id == "zsxq_sync":
        return _active_zsxq_sync_guard_tasks(rows=rows)

    features = BUSINESS_TASK_FEATURES.get(service_id)
    if not features:
        return []

    if rows is None:
        try:
            rows = _query_non_interruptible_rows_with_retry(
                features,
                include_feature=False,
                limit=BUSINESS_TASK_LIMIT_PER_SERVICE,
            )
        except Exception as exc:
            return [{
                "id": None,
                "status": "unknown",
                "step": "guard_check_failed",
                "progress": None,
                "title": None,
                "updated_at": None,
                "error": str(exc),
            }]

    items = []
    for row in rows or []:
        row_feature = str(_row_get(row, "feature") or "")
        if row_feature and row_feature not in features:
            continue
        status = str(_row_get(row, "status") or "").lower()
        step = str(_row_get(row, "current_step") or "").lower()
        if status != "processing" or not _is_non_interruptible_business_step(service_id, step):
            continue
        updated_at = _row_get(row, "updated_at")
        items.append({
            "id": _row_get(row, "id"),
            "status": status,
            "step": step,
            "progress": _row_get(row, "progress"),
            "title": _row_get(row, "task_title"),
            "updated_at": updated_at.isoformat() + "Z" if hasattr(updated_at, "isoformat") else updated_at,
        })
    return items


def _active_zsxq_sync_guard_tasks(*, rows=None, now=None):
    now = now or datetime.utcnow()
    if rows is None:
        try:
            from models.zsxq_sync import ZsxqSyncRun

            cutoff = now - timedelta(seconds=ZSXQ_SYNC_RESTART_GUARD_SECONDS)
            rows = (
                ZsxqSyncRun.query
                .with_entities(
                    ZsxqSyncRun.id.label("id"),
                    ZsxqSyncRun.group_id.label("group_id"),
                    ZsxqSyncRun.group_name.label("group_name"),
                    ZsxqSyncRun.mode.label("mode"),
                    ZsxqSyncRun.status.label("status"),
                    ZsxqSyncRun.updated_at.label("updated_at"),
                )
                .filter(ZsxqSyncRun.status.in_(ZSXQ_ORPHANABLE_STATUSES))
                .filter(ZsxqSyncRun.updated_at >= cutoff)
                .order_by(ZsxqSyncRun.updated_at.desc())
                .limit(BUSINESS_TASK_LIMIT_PER_SERVICE)
                .all()
            )
        except Exception as exc:
            _release_db_session_quietly()
            return [{
                "id": None,
                "status": "unknown",
                "step": "guard_check_failed",
                "progress": None,
                "title": None,
                "updated_at": None,
                "error": str(exc),
            }]
        finally:
            _release_db_session_quietly()

    items = []
    for row in rows or []:
        status = str(_row_get(row, "status") or "").lower()
        updated_at = _row_get(row, "updated_at")
        if status not in ZSXQ_ORPHANABLE_STATUSES or not updated_at:
            continue
        if (now - updated_at).total_seconds() > ZSXQ_SYNC_RESTART_GUARD_SECONDS:
            continue
        group_name = _row_get(row, "group_name") or _row_get(row, "group_id")
        items.append({
            "id": _row_get(row, "id"),
            "status": status,
            "step": str(_row_get(row, "mode") or "zsxq_sync"),
            "progress": None,
            "title": group_name,
            "updated_at": updated_at.isoformat() + "Z",
        })
    return items


def _is_non_interruptible_business_step(service_id, step):
    normalized = str(step or "").strip().lower()
    explicit_steps = NON_INTERRUPTIBLE_SERVICE_STEPS.get(service_id)
    if explicit_steps:
        return normalized in explicit_steps
    if not normalized:
        return True
    if normalized in INTERRUPTIBLE_BUSINESS_STEPS:
        return False
    if normalized.startswith("deferred_") or normalized.startswith("waiting_"):
        return False
    if normalized.endswith(("_waiting", "_queued")):
        return False
    return True


def _restart_all_stop_guards():
    try:
        all_features = sorted({
            feature
            for features in BUSINESS_TASK_FEATURES.values()
            for feature in features
        })
        rows = _query_non_interruptible_rows_with_retry(
            all_features,
            include_feature=True,
        )
    except Exception as exc:
        return {
            "restart_preflight": {
                "skipped": True,
                "reason": "guard_check_failed",
                "message": "无法确认业务任务状态，拒绝重启。",
                "active_tasks": [{"error": str(exc)}],
            }
        }

    guards = {}
    for service_id in SERVICES:
        guard = _service_stop_guard(service_id, rows=rows)
        if guard:
            guards[service_id] = guard
    return guards


def _query_non_interruptible_rows_with_retry(features, *, include_feature, limit=None):
    from models.task import PublicationTask
    from services.runtime_guard import is_retryable_db_error

    last_exc = None
    for attempt in range(2):
        try:
            columns = [
                PublicationTask.id,
                PublicationTask.status,
                PublicationTask.current_step,
                PublicationTask.progress,
                PublicationTask.task_title,
                PublicationTask.updated_at,
            ]
            if include_feature:
                columns.insert(1, PublicationTask.feature)
            query = (
                PublicationTask.query
                .with_entities(*columns)
                .filter(PublicationTask.feature.in_(features))
                .filter(PublicationTask.status == "processing")
                .order_by(PublicationTask.updated_at.desc(), PublicationTask.created_at.desc())
            )
            if limit is not None:
                query = query.limit(int(limit))
            return query.all()
        except Exception as exc:
            last_exc = exc
            _release_db_session_quietly()
            if attempt > 0 or not is_retryable_db_error(exc):
                raise
    raise last_exc


def _row_get(row, key):
    if isinstance(row, dict):
        return row.get(key)
    return getattr(row, key, None)


def _start_service(service_id, service, *, clear_pause=True):
    _ensure_dirs()
    paused = _load_paused_services().get(service_id)
    if paused and not clear_pause:
        return {
            "skipped": True,
            "reason": "paused",
            "pause": paused,
            "log_file": str(_log_path(service).relative_to(PROJECT_ROOT)),
        }
    if paused and clear_pause:
        _set_service_paused(service_id, False)
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
    _apply_db_process_role(env, "worker")

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
    # Authentication may have checked out a connection before this route runs.
    # Do not carry that request-scoped read transaction across Redis/Celery,
    # process inspection, or filesystem I/O below.
    _release_db_session_quietly()
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
    refresh_requested_at = time.time()
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
            cache_is_fresh = (
                STATUS_CACHE.get("key") == cache_key
                and cached
                and STATUS_CACHE.get("expires_at", 0) > now
            )
            refresh_was_coalesced = (
                force_refresh
                and STATUS_CACHE.get("refreshed_at", 0) >= refresh_requested_at
            )
            if cache_is_fresh and (not force_refresh or refresh_was_coalesced):
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
                "refreshed_at": time.time(),
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
        result = _start_service(service_id, service, clear_pause=True)
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
        data = request.get_json(silent=True) or {}
        pause = bool(data.get("pause", True))
        reason = data.get("reason") or "manual_stop"
        result = _stop_service(service_id, service, pause=pause, reason=reason, force=bool(data.get("force")))
        result["status"] = _service_status(service_id, service, include_logs=True)
        return jsonify({"success": True, "data": result})
    except Exception as exc:
        return jsonify({"success": False, "message": str(exc)}), 500


@bp.route("/pause/<service_id>", methods=["POST"])
def pause(service_id):
    service, error = _service_or_404(service_id)
    if error:
        return error
    try:
        data = request.get_json(silent=True) or {}
        paused = _set_service_paused(service_id, True, reason=data.get("reason") or "manual_pause")
        return jsonify({
            "success": True,
            "data": {
                "service": service_id,
                "pause": paused.get(service_id),
                "status": _service_status(service_id, service, include_logs=True),
            },
        })
    except Exception as exc:
        return jsonify({"success": False, "message": str(exc)}), 500


@bp.route("/resume/<service_id>", methods=["POST"])
def resume(service_id):
    service, error = _service_or_404(service_id)
    if error:
        return error
    try:
        _set_service_paused(service_id, False)
        return jsonify({
            "success": True,
            "data": {
                "service": service_id,
                "status": _service_status(service_id, service, include_logs=True),
            },
        })
    except Exception as exc:
        return jsonify({"success": False, "message": str(exc)}), 500


@bp.route("/restart/<service_id>", methods=["POST"])
def restart(service_id):
    service, error = _service_or_404(service_id)
    if error:
        return error
    try:
        data = request.get_json(silent=True) or {}
        stop_result = _stop_service(service_id, service, force=bool(data.get("force")))
        if stop_result.get("skipped"):
            return jsonify({
                "success": False,
                "message": stop_result.get("message") or "该服务当前不允许重启。",
                "data": {"stop": stop_result},
            }), 409
        time.sleep(0.6)
        start_result = _start_service(service_id, service, clear_pause=True)
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
        include_paused = bool(data.get("include_paused"))
        force = bool(data.get("force"))
        celery_results = {}
        scheduler_results = {}

        if not force:
            stop_guards = _restart_all_stop_guards()
            if stop_guards:
                return jsonify({
                    "success": False,
                    "message": "仍有不可中断的业务任务，未停止任何服务。",
                    "data": {"stop_guards": stop_guards},
                }), 409

        watchdog_stop = _stop_watchdog()

        paused_services = _load_paused_services()
        for service_id, service in SERVICES.items():
            if service_id in paused_services and not include_paused:
                celery_results[service_id] = {
                    "label": service.get("label") or service_id,
                    "skipped": True,
                    "reason": "paused",
                    "pause": paused_services.get(service_id),
                }
                continue
            # The global preflight above is the atomic safety decision for this
            # restart. Re-querying once per worker keeps one long HTTP session
            # open and can turn a transient disconnect into a false blocker.
            stop_result = _stop_service(service_id, service, force=True)
            celery_results[service_id] = {
                "label": service.get("label") or service_id,
                "stop": stop_result,
            }
            if stop_result.get("skipped"):
                celery_results[service_id]["skipped"] = True
                celery_results[service_id]["reason"] = stop_result.get("reason")

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
            if celery_results.get(service_id, {}).get("skipped"):
                continue
            celery_results[service_id]["start"] = _start_service(
                service_id,
                service,
                clear_pause=include_paused,
            )

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
            if not item.get("skipped") and _result_has_flag(item.get("start"), "started")
        )
        celery_online = sum(
            1
            for item in celery_results.values()
            if _result_has_flag(item.get("start"), "started", "already_running")
        )
        celery_skipped = sum(1 for item in celery_results.values() if item.get("skipped"))
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
                    "celery_online": celery_online,
                    "celery_skipped": celery_skipped,
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
