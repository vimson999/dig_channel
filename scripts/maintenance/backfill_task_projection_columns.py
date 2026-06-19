#!/usr/bin/env python3
"""Backfill denormalized query projection columns.

Run after applying the task projection schema migration, or rerun when old JSON
payloads were imported from backup. The script is idempotent.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = PROJECT_ROOT / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def _positive_int(value, default):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, parsed)


def main():
    parser = argparse.ArgumentParser(description="Backfill task and attachment projection columns.")
    parser.add_argument("--batch-size", type=int, default=200)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    from app import create_app
    from models import db
    from models.task import PublicationTask
    from models.zsxq_sync import ZsxqAttachment

    batch_size = _positive_int(args.batch_size, 200)
    app = create_app()
    task_count = 0
    attachment_count = 0

    with app.app_context():
        for task in PublicationTask.query.order_by(PublicationTask.created_at.asc()).yield_per(batch_size):
            task.sync_projection_fields()
            task_count += 1
            if not args.dry_run and task_count % batch_size == 0:
                db.session.commit()

        for attachment in ZsxqAttachment.query.order_by(ZsxqAttachment.created_at.asc()).yield_per(batch_size):
            attachment.sync_projection_fields()
            attachment_count += 1
            if not args.dry_run and attachment_count % batch_size == 0:
                db.session.commit()

        if args.dry_run:
            db.session.rollback()
        else:
            db.session.commit()

    print({
        "dry_run": bool(args.dry_run),
        "publication_tasks": task_count,
        "zsxq_attachments": attachment_count,
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
