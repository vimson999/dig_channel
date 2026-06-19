#!/usr/bin/env python3
"""
Print a PostgreSQL bottleneck snapshot for the current slow-query issue.

Examples:
    python scripts/diagnostics/db_bottleneck_report.py
    python scripts/diagnostics/db_bottleneck_report.py --explain
    python scripts/diagnostics/db_bottleneck_report.py --group-id 28888112822211
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any, Iterable

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection
from sqlalchemy.engine.url import make_url
from sqlalchemy.exc import SQLAlchemyError


ROOT = Path(__file__).resolve().parents[2]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="PostgreSQL bottleneck diagnostics")
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL"),
        help="PostgreSQL DATABASE_URL. Defaults to DATABASE_URL from env/.env.",
    )
    parser.add_argument(
        "--group-id",
        default="28888112822211",
        help="ZSXQ group_id used for representative ZSXQ EXPLAIN queries.",
    )
    parser.add_argument(
        "--top-limit",
        type=int,
        default=20,
        help="Number of rows to show for top query/activity sections.",
    )
    parser.add_argument(
        "--statement-timeout-ms",
        type=int,
        default=60_000,
        help="Per-statement timeout for diagnostics.",
    )
    parser.add_argument(
        "--explain",
        action="store_true",
        help="Run EXPLAIN ANALYZE for representative slow SELECTs.",
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


def truncate(value: Any, width: int = 120) -> str:
    if value is None:
        return ""
    text_value = str(value).replace("\n", " ")
    if len(text_value) <= width:
        return text_value
    return text_value[: width - 3] + "..."


def print_rows(title: str, rows: Iterable[dict[str, Any]], width: int = 120) -> None:
    rows = list(rows)
    print(f"\n== {title} ==")
    if not rows:
        print("(no rows)")
        return

    columns = list(rows[0].keys())
    formatted = [
        {column: truncate(row.get(column), width=width) for column in columns}
        for row in rows
    ]
    col_widths = {
        column: min(
            max(len(column), *(len(row[column]) for row in formatted)),
            width,
        )
        for column in columns
    }

    header = " | ".join(column.ljust(col_widths[column]) for column in columns)
    print(header)
    print("-" * len(header))
    for row in formatted:
        print(" | ".join(row[column].ljust(col_widths[column]) for column in columns))


def fetch_rows(conn: Connection, sql: str, params: dict[str, Any] | None = None):
    return conn.execute(text(sql), params or {}).mappings().all()


def print_query(
    conn: Connection,
    title: str,
    sql: str,
    params: dict[str, Any] | None = None,
    width: int = 120,
) -> None:
    try:
        print_rows(title, fetch_rows(conn, sql, params), width=width)
    except SQLAlchemyError as exc:
        print(f"\n== {title} ==")
        print(f"query failed: {exc}")


def print_explain(
    conn: Connection,
    title: str,
    select_sql: str,
    params: dict[str, Any] | None = None,
) -> None:
    print(f"\n== EXPLAIN ANALYZE: {title} ==")
    try:
        result = conn.execute(
            text("EXPLAIN (ANALYZE, BUFFERS) " + select_sql),
            params or {},
        )
        for row in result:
            print(row[0])
    except SQLAlchemyError as exc:
        print(f"explain failed: {exc}")


def pg_stat_statements_installed(conn: Connection) -> bool:
    row = conn.execute(
        text("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_stat_statements')")
    ).scalar()
    return bool(row)


def run_snapshot(conn: Connection, args: argparse.Namespace) -> None:
    conn.exec_driver_sql(f"SET statement_timeout = {int(args.statement_timeout_ms)}")

    print_query(
        conn,
        "database identity",
        """
        SELECT
            now() AS checked_at,
            current_database() AS database_name,
            current_user AS current_user,
            version() AS postgres_version
        """,
    )

    print_query(
        conn,
        "target table sizes",
        """
        WITH target_tables(relname) AS (
            VALUES
                ('public.publication_tasks'),
                ('public.zsxq_sync_runs'),
                ('public.zsxq_topics'),
                ('public.zsxq_attachments')
        )
        SELECT
            relname,
            pg_size_pretty(pg_relation_size(to_regclass(relname))) AS table_size,
            pg_size_pretty(pg_indexes_size(to_regclass(relname))) AS indexes_size,
            pg_size_pretty(pg_total_relation_size(to_regclass(relname))) AS total_size
        FROM target_tables
        WHERE to_regclass(relname) IS NOT NULL
        ORDER BY pg_total_relation_size(to_regclass(relname)) DESC
        """,
    )

    print_query(
        conn,
        "existing indexes",
        """
        SELECT
            tablename,
            indexname,
            indexdef
        FROM pg_indexes
        WHERE schemaname = 'public'
          AND tablename IN (
              'publication_tasks',
              'zsxq_sync_runs',
              'zsxq_topics',
              'zsxq_attachments'
          )
        ORDER BY tablename, indexname
        """,
        width=180,
    )

    print_query(
        conn,
        "table scan and vacuum stats",
        """
        SELECT
            relname,
            n_live_tup,
            n_dead_tup,
            seq_scan,
            idx_scan,
            CASE
                WHEN (seq_scan + idx_scan) = 0 THEN NULL
                ELSE round((CAST(idx_scan AS numeric) / (seq_scan + idx_scan)) * 100, 2)
            END AS idx_scan_pct,
            last_vacuum,
            last_autovacuum,
            last_analyze,
            last_autoanalyze
        FROM pg_stat_user_tables
        WHERE schemaname = 'public'
          AND relname IN (
              'publication_tasks',
              'zsxq_sync_runs',
              'zsxq_topics',
              'zsxq_attachments'
          )
        ORDER BY n_dead_tup DESC
        """,
    )

    print_query(
        conn,
        "current activity summary",
        """
        SELECT
            state,
            wait_event_type,
            wait_event,
            count(*) AS sessions,
            max(clock_timestamp() - COALESCE(query_start, state_change)) AS max_age,
            left(max(query), 180) AS sample_query
        FROM pg_stat_activity
        WHERE datname = current_database()
          AND pid <> pg_backend_pid()
        GROUP BY state, wait_event_type, wait_event
        ORDER BY sessions DESC, max_age DESC NULLS LAST
        """,
    )

    print_query(
        conn,
        "oldest active or waiting sessions",
        """
        SELECT
            pid,
            usename,
            application_name,
            client_addr,
            state,
            wait_event_type,
            wait_event,
            clock_timestamp() - COALESCE(query_start, state_change) AS age,
            left(query, 300) AS query_sample
        FROM pg_stat_activity
        WHERE datname = current_database()
          AND pid <> pg_backend_pid()
        ORDER BY COALESCE(query_start, state_change) ASC NULLS LAST
        LIMIT :limit
        """,
        {"limit": args.top_limit},
    )

    if pg_stat_statements_installed(conn):
        print_query(
            conn,
            "pg_stat_statements hot queries",
            """
            SELECT
                calls,
                round(CAST(mean_exec_time AS numeric), 2) AS mean_ms,
                round(CAST(max_exec_time AS numeric), 2) AS max_ms,
                round(CAST(total_exec_time AS numeric), 2) AS total_ms,
                rows,
                left(query, 500) AS query_sample
            FROM pg_stat_statements
            WHERE dbid = (SELECT oid FROM pg_database WHERE datname = current_database())
              AND (
                  query ILIKE '%publication_tasks%'
                  OR query ILIKE '%zsxq_sync_runs%'
                  OR query ILIKE '%zsxq_topics%'
                  OR query ILIKE '%zsxq_attachments%'
              )
            ORDER BY total_exec_time DESC
            LIMIT :limit
            """,
            {"limit": args.top_limit},
            width=180,
        )
    else:
        print("\n== pg_stat_statements hot queries ==")
        print("pg_stat_statements is not installed; skipping historical query stats.")


def run_explains(conn: Connection, args: argparse.Namespace) -> None:
    print_explain(
        conn,
        "publisher task list with JSON feature filter",
        """
        SELECT
            id,
            celery_task_id,
            status,
            current_step,
            progress,
            params,
            result,
            error_message,
            created_at,
            updated_at,
            completed_at
        FROM public.publication_tasks
        WHERE (
            CAST(params ->> 'feature' AS varchar) IS NULL
            OR CAST(params ->> 'feature' AS varchar) = 'publisher'
        )
        ORDER BY created_at DESC
        LIMIT 20
        """,
    )

    print_explain(
        conn,
        "content pipeline task list",
        """
        SELECT
            id,
            celery_task_id,
            status,
            current_step,
            progress,
            params,
            result,
            error_message,
            created_at,
            updated_at,
            completed_at
        FROM public.publication_tasks
        WHERE CAST(params ->> 'feature' AS varchar) IN (
            'content_pdf_clean',
            'content_audio_clean'
        )
        ORDER BY created_at DESC
        LIMIT 20
        """,
    )

    print_explain(
        conn,
        "recent publication tasks with large JSON columns",
        """
        SELECT
            id,
            status,
            params,
            result,
            created_at,
            updated_at,
            completed_at
        FROM public.publication_tasks
        ORDER BY created_at DESC
        LIMIT 20
        """,
    )

    print_explain(
        conn,
        "recent publication tasks without large JSON columns",
        """
        SELECT
            id,
            celery_task_id,
            status,
            current_step,
            progress,
            created_at,
            updated_at,
            completed_at
        FROM public.publication_tasks
        ORDER BY created_at DESC
        LIMIT 20
        """,
    )

    print_explain(
        conn,
        "ZSXQ system sync runs",
        """
        SELECT
            id,
            group_id,
            mode,
            status,
            created_at,
            completed_at,
            result
        FROM public.zsxq_sync_runs
        WHERE group_id = :group_id
          AND mode LIKE 'sys%'
        ORDER BY created_at DESC
        LIMIT 120
        """,
        {"group_id": args.group_id},
    )


def main() -> int:
    load_dotenv(ROOT / ".env")
    args = parse_args()
    database_url = normalize_database_url(args.database_url or os.environ.get("DATABASE_URL"))
    if not database_url.startswith("postgresql"):
        raise SystemExit(f"PostgreSQL DATABASE_URL is required, got: {masked_url(database_url)}")

    print(f"Connecting to {masked_url(database_url)}")
    engine = create_engine(database_url, pool_pre_ping=True)
    with engine.connect() as conn:
        run_snapshot(conn, args)
        if args.explain:
            run_explains(conn, args)
        else:
            print("\nEXPLAIN ANALYZE skipped. Add --explain to execute representative slow SELECTs.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        raise SystemExit(130)
