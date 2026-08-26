#!/usr/bin/env python3
"""Backfill canonical cleaned-output records for historical content-pipeline tasks.

Run a dry preview first. The backfill only accepts an existing output that is
inside CONTENT_PIPELINE_OUTPUT_ROOT and can be matched to exactly one asset via
the task's recorded source path (or a legacy relative source plus source_root).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from sqlalchemy.orm import load_only


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = PROJECT_ROOT / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


FEATURES_BY_MEDIA_TYPE = {
    "pdf": "content_pdf_clean",
    "audio": "content_audio_clean",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只输出可登记/待修复统计，不写数据库。",
    )
    parser.add_argument(
        "--task-id",
        action="append",
        dest="task_ids",
        help="仅处理指定清洗任务；可重复传入。",
    )
    parser.add_argument(
        "--media-type",
        choices=sorted(FEATURES_BY_MEDIA_TYPE),
        help="仅处理 pdf 或 audio 清洗任务。",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=500,
        help="未指定 --task-id 时最多处理的任务数（默认 500）。",
    )
    return parser


def _positive_limit(value: int) -> int:
    try:
        return max(1, min(int(value), 10_000))
    except (TypeError, ValueError):
        return 500


def _backfill_task_filter(PublicationTask, *, task_ids=None, media_type=None):
    query = PublicationTask.query.filter(
        PublicationTask.feature.in_(FEATURES_BY_MEDIA_TYPE.values())
    )
    if task_ids:
        query = query.filter(PublicationTask.id.in_(task_ids))
    else:
        query = query.filter(PublicationTask.status == "completed")
    if media_type:
        query = query.filter(
            PublicationTask.feature == FEATURES_BY_MEDIA_TYPE[media_type]
        )
    return query


def list_backfill_task_ids(PublicationTask, *, task_ids=None, media_type=None, limit=500):
    """Fetch batch identifiers without loading heavyweight task JSON."""
    rows = _backfill_task_filter(
        PublicationTask,
        task_ids=task_ids,
        media_type=media_type,
    ).with_entities(
        PublicationTask.id
    ).order_by(
        PublicationTask.created_at.asc(),
        PublicationTask.id.asc(),
    ).limit(_positive_limit(limit)).all()
    return [str(row[0]) for row in rows if row and row[0]]


def load_backfill_task(PublicationTask, task_id):
    """Load one task's required JSON and detach it before per-task rollback."""
    query = PublicationTask.query.options(load_only(
        PublicationTask.id,
        PublicationTask.feature,
        PublicationTask.params,
        PublicationTask.result,
    )).filter(PublicationTask.id == str(task_id))
    task = query.one_or_none()
    if task:
        query.session.expunge(task)
    return task


def iter_backfill_tasks(PublicationTask, *, task_ids=None, media_type=None, limit=500):
    """Yield historical tasks one payload at a time for bounded DB reads."""
    for task_id in list_backfill_task_ids(
        PublicationTask,
        task_ids=task_ids,
        media_type=media_type,
        limit=limit,
    ):
        task = load_backfill_task(PublicationTask, task_id)
        if task:
            yield task


def main() -> int:
    args = build_parser().parse_args()
    from app import create_app
    from models import db
    from models.task import PublicationTask
    from services.content_pipeline.canonical_asset_service import (
        backfill_task_cleaned_outputs,
    )

    app = create_app()
    reports = []
    errors = []
    with app.app_context():
        if args.task_ids:
            task_ids = [str(value).strip() for value in args.task_ids if str(value).strip()]
        else:
            task_ids = None

        selected_task_ids = list_backfill_task_ids(
            PublicationTask,
            task_ids=task_ids,
            media_type=args.media_type,
            limit=args.limit,
        )

        for task_id in selected_task_ids:
            try:
                task = load_backfill_task(PublicationTask, task_id)
                if not task:
                    errors.append({"task_id": str(task_id), "error": "task_not_found"})
                    continue
                reports.append(
                    backfill_task_cleaned_outputs(task, dry_run=bool(args.dry_run))
                )
            except Exception as exc:  # Continue so one corrupt task never hides others.
                db.session.rollback()
                errors.append({"task_id": str(task_id), "error": str(exc)})

    totals = Counter()
    for report in reports:
        totals["registered_count"] += int(report.get("registered_count") or 0)
        totals["failed_count"] += int(report.get("failed_count") or 0)
        totals["unresolved_count"] += int(report.get("unresolved_count") or 0)
        totals[f"status:{report.get('status') or 'unknown'}"] += 1
    payload = {
        "dry_run": bool(args.dry_run),
        "processed_tasks": len(reports),
        "errors": errors,
        "totals": dict(totals),
        "reports": reports,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
