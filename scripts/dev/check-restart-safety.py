#!/usr/bin/env python3
"""Fail closed when a full local restart would interrupt active business work."""

import os
import socket
import sys
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
}


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
    if normalized in WAITING_STEPS:
        return False
    if normalized.startswith("deferred_") or normalized.startswith("waiting_"):
        return False
    if normalized.endswith(("_waiting", "_queued")):
        return False
    return True


def main():
    _use_direct_database_url()

    from app import create_app
    from models import db
    from models.task import PublicationTask

    app = create_app()
    try:
        with app.app_context():
            rows = (
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
            blocking = [row for row in rows if is_non_interruptible_step(row.current_step)]
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
        updated_at = row.updated_at.isoformat() if row.updated_at else "-"
        print(
            "  "
            f"id={row.id} feature={row.feature or '-'} step={row.current_step or '-'} "
            f"progress={row.progress or 0}% updated_at={updated_at} title={row.task_title or '-'}",
            file=sys.stderr,
        )
    print("Wait for these tasks to finish. Override only when interruption is intentional:", file=sys.stderr)
    print("  FORCE_RESTART_ACTIVE_TASKS=1 sh scripts/dev/restart-dev-stack.sh", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
