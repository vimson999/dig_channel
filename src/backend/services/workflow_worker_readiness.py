"""Worker code-revision handshake for report-audio orchestration.

The browser workflow spans four long-lived Celery queues.  A Python import can
otherwise succeed in the web process while an already-running worker still has
the previous module graph in memory.  Workers publish their loaded revision at
boot; submission compares it with the web process revision before enqueueing.
"""

import hashlib
import json
import os
import re
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


REPORT_AUDIO_DEPENDENCY_QUEUES = (
    "report_audio_workflow",
    "report_pdf_gemini",
    "doubao_browser_tts",
    "render",
)
_REVISION_FILES = (
    "services/workflow_notification_service.py",
    "tasks/report_audio_workflow.py",
    "tasks/doubao_browser_tts.py",
    "tasks/gemini_browser.py",
    "tasks/publisher.py",
)


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _runtime_dir(runtime_dir=None) -> Path:
    configured = runtime_dir or os.getenv("WORKFLOW_WORKER_READINESS_DIR")
    return Path(configured) if configured else _project_root() / "runtime" / "worker_readiness"


def workflow_code_revision() -> str:
    """Fingerprint source modules that form the report-audio worker boundary."""
    digest = hashlib.sha256()
    root = _project_root()
    for relative in _REVISION_FILES:
        path = root / "src" / "backend" / relative
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()[:16]


def _safe_part(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "default"))


def _readiness_path(queue: str, worker_id: str, runtime_dir=None) -> Path:
    return _runtime_dir(runtime_dir) / f"{_safe_part(queue)}__{_safe_part(worker_id)}.json"


def record_worker_readiness(queue: str, *, worker_id: str = "", revision: str = "", runtime_dir=None) -> dict:
    """Atomically publish a worker's loaded revision after its imports succeed."""
    queue = str(queue or "").strip()
    if not queue:
        raise ValueError("worker readiness queue is required")
    worker_id = str(worker_id or os.getenv("DOUBAO_BROWSER_TTS_WORKER_ID") or socket.gethostname())
    data = {
        "queue": queue,
        "worker_id": worker_id,
        "revision": str(revision or workflow_code_revision()),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "pid": os.getpid(),
    }
    path = _readiness_path(queue, worker_id, runtime_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    temporary.replace(path)
    return data


def report_audio_worker_readiness(*, runtime_dir=None, revision: str = "", queues: Iterable[str] = REPORT_AUDIO_DEPENDENCY_QUEUES) -> dict:
    """Return whether every report-audio dependency has a worker on this revision."""
    expected = str(revision or workflow_code_revision())
    required = tuple(str(queue) for queue in queues)
    directory = _runtime_dir(runtime_dir)
    ready_queues = set()
    stale_queues = set()
    for path in directory.glob("*.json") if directory.exists() else ():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        queue = str(data.get("queue") or "")
        if queue not in required:
            continue
        if data.get("revision") == expected:
            ready_queues.add(queue)
        else:
            stale_queues.add(queue)
    stale = [queue for queue in required if queue in stale_queues]
    missing = [queue for queue in required if queue not in ready_queues or queue in stale_queues]
    return {
        "ready": not missing,
        "expected_revision": expected,
        "ready_queues": [queue for queue in required if queue in ready_queues],
        "missing_queues": missing,
        "stale_queues": stale,
    }


def require_report_audio_workers_ready() -> dict:
    readiness = report_audio_worker_readiness()
    if not readiness["ready"]:
        queues = ", ".join(readiness["missing_queues"])
        raise RuntimeError(
            "deployment_not_ready: report-audio workers are not on the current code revision; "
            f"restart required queues: {queues}"
        )
    return readiness
