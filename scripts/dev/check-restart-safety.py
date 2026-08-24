#!/usr/bin/env python3
"""Fail closed when a full local restart would interrupt active business work."""

import os
import socket
import sys
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = PROJECT_ROOT / "src" / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

os.environ.setdefault("BACKEND_PROCESS_BOOTSTRAP_ENABLED", "0")


WAITING_STEPS = {
    "queued",
    "pending",
    "waiting",
    "deferred_capacity",
    "deferred_backlog",
    "deferred_db_pressure",
    "orphaned_processing",
    "reconcile_retry_wait",
    "visual_retry_wait",
    "final_video_retry_wait",
}
READY_STEPS = {
    # These visual-workflow parent states have no running worker of their own.
    # They wait for an explicit video-render request and must not block a restart.
    "images_done",
    "whole_audio_aligned",
    "whole_audio_alignment_failed",
}
# A retrying run has already published its replacement Celery message and is
# waiting in the broker.  Treating it as executing makes a quiesced restart
# deadlock: consumers are frozen, so the retry cannot run, while the restart
# waits for its database status to change.  Only a worker that is actively
# executing the run needs the graceful-drain guard.
ZSXQ_ACTIVE_STATUSES = ("running",)


def _zsxq_restart_guard_seconds():
    try:
        minutes = int(os.environ.get("CELERY_MONITOR_ZSXQ_RESTART_GUARD_MINUTES", "5"))
    except (TypeError, ValueError):
        minutes = 5
    return max(60, minutes * 60)


def _local_database_url_is_available(database_url):
    try:
        parsed = urlparse(str(database_url or ""))
        host = parsed.hostname
        port = parsed.port or 5432
    except (TypeError, ValueError):
        return False
    if host not in {"127.0.0.1", "localhost", "::1"}:
        return False
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def _use_direct_database_url():
    """Prefer a live local proxy, falling back to the direct control path."""
    from dotenv import dotenv_values

    values = dotenv_values(PROJECT_ROOT / ".env")
    database_url = os.environ.get("DATABASE_URL") or values.get("DATABASE_URL")
    if database_url and _local_database_url_is_available(database_url):
        os.environ["DATABASE_URL"] = str(database_url)
        return
    direct_url = os.environ.get("DIRECT_DATABASE_URL") or values.get(
        "DIRECT_DATABASE_URL"
    )
    if direct_url:
        os.environ["DATABASE_URL"] = str(direct_url)


def is_non_interruptible_step(step):
    normalized = str(step or "").strip().lower()
    if not normalized:
        return True
    if normalized in WAITING_STEPS or normalized in READY_STEPS:
        return False
    if normalized.startswith("deferred_") or normalized.startswith("waiting_"):
        return False
    if normalized.endswith(("_waiting", "_queued")):
        return False
    return True


def is_recent_active_zsxq_run(status, updated_at, *, now=None, guard_seconds=None):
    if str(status or "").strip().lower() not in ZSXQ_ACTIVE_STATUSES:
        return False
    if updated_at is None:
        return False
    now = now or datetime.utcnow()
    guard_seconds = guard_seconds or _zsxq_restart_guard_seconds()
    return (now - updated_at).total_seconds() <= guard_seconds


def main():
    _use_direct_database_url()

    from app import create_app
    from models import db
    from models.task import PublicationTask
    from models.zsxq_sync import ZsxqSyncRun

    app = create_app()
    try:
        with app.app_context():
            publication_rows = (
                db.session.query(
                    PublicationTask.id,
                    PublicationTask.feature,
                    PublicationTask.status,
                    PublicationTask.current_step,
                    PublicationTask.progress,
                    PublicationTask.task_title,
                    PublicationTask.updated_at,
                )
                .filter(PublicationTask.status == "processing")
                .order_by(PublicationTask.updated_at.desc(), PublicationTask.created_at.desc())
                .limit(100)
                .all()
            )
            now = datetime.utcnow()
            cutoff = now - timedelta(seconds=_zsxq_restart_guard_seconds())
            zsxq_rows = (
                db.session.query(
                    ZsxqSyncRun.id,
                    ZsxqSyncRun.group_id,
                    ZsxqSyncRun.group_name,
                    ZsxqSyncRun.mode,
                    ZsxqSyncRun.status,
                    ZsxqSyncRun.updated_at,
                )
                .filter(ZsxqSyncRun.status.in_(ZSXQ_ACTIVE_STATUSES))
                .filter(ZsxqSyncRun.updated_at >= cutoff)
                .order_by(ZsxqSyncRun.updated_at.desc())
                .limit(100)
                .all()
            )
            blocking = [
                {
                    "source": "publication_tasks",
                    "id": row.id,
                    "feature": row.feature,
                    "step": row.current_step,
                    "progress": row.progress,
                    "title": row.task_title,
                    "updated_at": row.updated_at,
                }
                for row in publication_rows
                if is_non_interruptible_step(row.current_step)
            ]
            blocking.extend({
                "source": "zsxq_sync_runs",
                "id": row.id,
                "feature": "zsxq_sync",
                "step": row.mode,
                "progress": None,
                "title": row.group_name or row.group_id,
                "updated_at": row.updated_at,
            } for row in zsxq_rows if is_recent_active_zsxq_run(
                row.status,
                row.updated_at,
                now=now,
            ))
    except Exception as exc:
        print(f"ERROR: restart safety check could not verify active tasks: {exc}", file=sys.stderr)
        print("Refusing the restart. Inspect the database or use FORCE_RESTART_ACTIVE_TASKS=1 explicitly.", file=sys.stderr)
        return 3
    finally:
        try:
            db.session.remove()
        except Exception:
            pass

    if not blocking:
        print("OK: no non-interruptible processing tasks found.")
        return 0

    print(f"BLOCKED: {len(blocking)} non-interruptible processing task(s) are still running:", file=sys.stderr)
    for row in blocking:
        updated_at = (
            row["updated_at"].isoformat() + "Z"
            if row["updated_at"]
            else "-"
        )
        print(
            "  "
            f"source={row['source']} id={row['id']} feature={row['feature'] or '-'} "
            f"step={row['step'] or '-'} progress={row['progress'] or 0}% "
            f"updated_at={updated_at} title={row['title'] or '-'}",
            file=sys.stderr,
        )
    print("Wait for these tasks to finish. Override only when interruption is intentional:", file=sys.stderr)
    print("  FORCE_RESTART_ACTIVE_TASKS=1 sh scripts/dev/restart-dev-stack.sh", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
