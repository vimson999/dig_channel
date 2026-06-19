#!/usr/bin/env python3
"""
Apply PostgreSQL hotfix indexes one statement at a time without psql.

This avoids driver pipeline/batch mode and runs with AUTOCOMMIT, which is
required by CREATE INDEX CONCURRENTLY.

Examples:
    python scripts/maintenance/apply_db_hotfix_indexes.py --dry-run
    python scripts/maintenance/apply_db_hotfix_indexes.py
    python scripts/maintenance/apply_db_hotfix_indexes.py --include-zsxq
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.engine.url import make_url
from sqlalchemy.exc import SQLAlchemyError


ROOT = Path(__file__).resolve().parents[2]


PUBLICATION_TASK_INDEXES = [
    (
        "ix_publication_tasks_created_at_desc",
        """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_publication_tasks_created_at_desc
        ON public.publication_tasks (created_at DESC)
        """,
    ),
    (
        "ix_publication_tasks_feature_created_at_desc",
        """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_publication_tasks_feature_created_at_desc
        ON public.publication_tasks (((params ->> 'feature')::varchar), created_at DESC)
        """,
    ),
    (
        "ix_publication_tasks_publisher_created_at_desc",
        """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_publication_tasks_publisher_created_at_desc
        ON public.publication_tasks (created_at DESC)
        WHERE ((params ->> 'feature')::varchar IS NULL OR (params ->> 'feature')::varchar = 'publisher')
        """,
    ),
    (
        "ix_publication_tasks_publisher_pending_updated_at",
        """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_publication_tasks_publisher_pending_updated_at
        ON public.publication_tasks (status, updated_at ASC)
        WHERE ((params ->> 'feature')::varchar IS NULL OR (params ->> 'feature')::varchar = 'publisher')
        """,
    ),
]


ZSXQ_INDEXES = [
    (
        "ix_zsxq_sync_runs_group_created_at_desc",
        """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_zsxq_sync_runs_group_created_at_desc
        ON public.zsxq_sync_runs (group_id, created_at DESC)
        """,
    ),
    (
        "ix_zsxq_sync_runs_group_mode_created_at_desc",
        """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_zsxq_sync_runs_group_mode_created_at_desc
        ON public.zsxq_sync_runs (group_id, mode varchar_pattern_ops, created_at DESC)
        """,
    ),
    (
        "ix_zsxq_sync_runs_mode_created_at_desc",
        """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_zsxq_sync_runs_mode_created_at_desc
        ON public.zsxq_sync_runs (mode varchar_pattern_ops, created_at DESC)
        """,
    ),
    (
        "ix_zsxq_topics_group_first_run_tab_synced_desc",
        """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_zsxq_topics_group_first_run_tab_synced_desc
        ON public.zsxq_topics (group_id, first_sync_run_id, tab_id, last_synced_at DESC)
        """,
    ),
    (
        "ix_zsxq_topics_group_last_run_tab_synced_desc",
        """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_zsxq_topics_group_last_run_tab_synced_desc
        ON public.zsxq_topics (group_id, last_sync_run_id, tab_id, last_synced_at DESC)
        """,
    ),
    (
        "ix_zsxq_attachments_group_topic_tab_created",
        """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_zsxq_attachments_group_topic_tab_created
        ON public.zsxq_attachments (group_id, topic_id, tab_id, created_at)
        """,
    ),
    (
        "ix_zsxq_attachments_publish_source_task_id",
        """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_zsxq_attachments_publish_source_task_id
        ON public.zsxq_attachments (publish_source_task_id)
        """,
    ),
    (
        "ix_zsxq_attachments_publish_status_updated_at",
        """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_zsxq_attachments_publish_status_updated_at
        ON public.zsxq_attachments (publish_status, publish_updated_at DESC)
        """,
    ),
]


ANALYZE_STATEMENTS = [
    "ANALYZE public.publication_tasks",
    "ANALYZE public.zsxq_sync_runs",
    "ANALYZE public.zsxq_topics",
    "ANALYZE public.zsxq_attachments",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apply DB hotfix indexes without psql")
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL"),
        help="PostgreSQL DATABASE_URL. Defaults to DATABASE_URL from env/.env.",
    )
    parser.add_argument(
        "--include-zsxq",
        action="store_true",
        help="Also apply ZSXQ indexes. By default only publication_tasks indexes are applied.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print statements without executing them.",
    )
    parser.add_argument(
        "--lock-timeout",
        default="5s",
        help="PostgreSQL lock_timeout, default 5s.",
    )
    return parser.parse_args()


def normalize_database_url(raw_url: str | None) -> str:
    if not raw_url:
        raise SystemExit("DATABASE_URL is required")
    if raw_url.startswith("postgres://"):
        return "postgresql://" + raw_url[len("postgres://") :]
    return raw_url


def masked_url(url: str) -> str:
    try:
        return make_url(url).render_as_string(hide_password=True)
    except Exception:
        return "<unparseable database url>"


def compact_sql(sql: str) -> str:
    return " ".join(sql.strip().split())


def load_existing_indexes(conn) -> set[str]:
    rows = conn.exec_driver_sql(
        """
        SELECT indexname
        FROM pg_indexes
        WHERE schemaname = 'public'
          AND tablename IN (
              'publication_tasks',
              'zsxq_sync_runs',
              'zsxq_topics',
              'zsxq_attachments'
          )
        """
    )
    return {str(row[0]) for row in rows}


def main() -> int:
    load_dotenv(ROOT / ".env")
    args = parse_args()
    database_url = normalize_database_url(args.database_url or os.environ.get("DATABASE_URL"))
    if not database_url.startswith("postgresql"):
        raise SystemExit(f"PostgreSQL DATABASE_URL is required, got: {masked_url(database_url)}")

    statements = list(PUBLICATION_TASK_INDEXES)
    if args.include_zsxq:
        statements.extend(ZSXQ_INDEXES)

    print(f"Target DB: {masked_url(database_url)}")
    print(f"Statements: {len(statements)} indexes, {len(ANALYZE_STATEMENTS)} ANALYZE")
    if args.dry_run:
        for name, sql in statements:
            print(f"\n-- {name}\n{compact_sql(sql)};")
        for sql in ANALYZE_STATEMENTS:
            print(f"\n{sql};")
        return 0

    engine = create_engine(
        database_url,
        isolation_level="AUTOCOMMIT",
        pool_pre_ping=True,
    )

    with engine.connect() as conn:
        conn.exec_driver_sql(f"SET lock_timeout = '{args.lock_timeout}'")
        conn.exec_driver_sql("SET statement_timeout = '0'")
        existing_indexes = load_existing_indexes(conn)

        for index, (name, sql) in enumerate(statements, start=1):
            if name in existing_indexes:
                print(f"[{index}/{len(statements)}] skipping {name}; already exists")
                continue
            started = time.monotonic()
            print(f"[{index}/{len(statements)}] creating {name} ...", flush=True)
            try:
                conn.exec_driver_sql(compact_sql(sql))
            except SQLAlchemyError as exc:
                print(f"FAILED {name}: {exc}", file=sys.stderr)
                return 1
            elapsed = time.monotonic() - started
            print(f"done {name} in {elapsed:.2f}s")

        for sql in ANALYZE_STATEMENTS:
            print(f"running {sql} ...", flush=True)
            try:
                conn.exec_driver_sql(sql)
            except SQLAlchemyError as exc:
                print(f"FAILED {sql}: {exc}", file=sys.stderr)
                return 1

    print("DB hotfix indexes complete.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        raise SystemExit(130)
