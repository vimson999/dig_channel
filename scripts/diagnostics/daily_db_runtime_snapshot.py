#!/usr/bin/env python3
"""Read-only daily runtime snapshot used by the DB performance audit."""

from __future__ import annotations

import argparse
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from sqlalchemy import create_engine, text


ROOT = Path(__file__).resolve().parents[2]


def utc_naive_database_boundary(value, *, timezone_name="Asia/Shanghai"):
    """Convert a local audit boundary to the UTC-naive timestamps stored by models."""
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(timezone_name))
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


def _percentile(values, quantile):
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    rank = (len(ordered) - 1) * quantile
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = rank - lower
    return ordered[lower] + ((ordered[upper] - ordered[lower]) * fraction)


def summarize_publisher_rows(rows):
    values = [dict(row) for row in rows]
    durations = [float(row.get("duration_s") or 0.0) for row in values]
    return {
        "total": len(values),
        "completed": sum(row.get("status") == "completed" for row in values),
        "failed": sum(row.get("status") == "failed" for row in values),
        "canceled": sum(row.get("status") == "canceled" for row in values),
        "old_root": sum(bool(row.get("old_root")) for row in values),
        "old_reason": sum(
            row.get("repair_reason") == "pending_message_not_active_but_queue_visible"
            for row in values
        ),
        "replacement_history": sum(
            len(row.get("repair_history") or [])
            if isinstance(row.get("repair_history"), list)
            else 0
            for row in values
        ),
        "total_s": round(sum(durations), 3),
        "p50_s": round(_percentile(durations, 0.5), 3),
        "p95_s": round(_percentile(durations, 0.95), 3),
        "max_s": round(max(durations, default=0.0), 3),
    }


def summarize_task_roots(rows):
    """Summarize checkout ownership for every durable task feature."""
    grouped = defaultdict(lambda: {
        "total": 0,
        "old_root": 0,
        "current_root": 0,
        "unclassified": 0,
    })
    for raw_row in rows:
        row = dict(raw_row)
        summary = grouped[str(row.get("feature") or "unknown")]
        summary["total"] += 1
        old_root = bool(row.get("old_root"))
        current_root = bool(row.get("current_root"))
        summary["old_root"] += old_root
        summary["current_root"] += current_root
        summary["unclassified"] += not old_root and not current_root
    return [
        {"feature": feature, **grouped[feature]}
        for feature in sorted(grouped)
    ]


def _rows(conn, sql, params):
    return conn.execute(text(sql), params).mappings().all()


def _print(title, values):
    print(f"\n== {title} ==")
    for value in values:
        print(dict(value))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", required=True)
    parser.add_argument("--until", required=True)
    parser.add_argument(
        "--timezone",
        default=os.environ.get("DAILY_DB_AUDIT_TIMEZONE", "Asia/Shanghai"),
        help="Timezone for naive --since/--until values (default: Asia/Shanghai)",
    )
    args = parser.parse_args()
    params = {
        "since": utc_naive_database_boundary(
            args.since,
            timezone_name=args.timezone,
        ),
        "until": utc_naive_database_boundary(
            args.until,
            timezone_name=args.timezone,
        ),
        "old_root": "%dig_ytb_data_claude_v1%",
        "current_root": f"%{ROOT}%",
        "empty": "",
    }
    print(
        "database_window_utc="
        f"{params['since'].isoformat(sep=' ')}..{params['until'].isoformat(sep=' ')}"
    )

    load_dotenv(ROOT / ".env")
    engine = create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
    with engine.connect() as conn:
        conn.exec_driver_sql("SET TRANSACTION READ ONLY")
        conn.exec_driver_sql("SET LOCAL statement_timeout = '60s'")
        _print(
            "publication task feature summary",
            _rows(
                conn,
                """
                SELECT feature, status, count(*) AS count,
                       round(sum(extract(epoch FROM (coalesce(completed_at, updated_at) - created_at)))::numeric, 3) AS total_s,
                       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM (coalesce(completed_at, updated_at) - created_at)))::numeric, 3) AS p50_s,
                       round(percentile_cont(0.95) WITHIN GROUP (ORDER BY extract(epoch FROM (coalesce(completed_at, updated_at) - created_at)))::numeric, 3) AS p95_s,
                       round(max(extract(epoch FROM (coalesce(completed_at, updated_at) - created_at)))::numeric, 3) AS max_s
                FROM publication_tasks
                WHERE created_at >= :since AND created_at < :until
                GROUP BY feature, status
                ORDER BY total_s DESC NULLS LAST
                """,
                params,
            ),
        )
        task_root_rows = _rows(
            conn,
            """
            SELECT id, feature, status, created_at, completed_at,
                   round(extract(epoch FROM (coalesce(completed_at, updated_at) - created_at))::numeric, 3) AS duration_s,
                   (CAST(params AS text) LIKE :old_root
                    OR CAST(result AS text) LIKE :old_root
                    OR coalesce(task_video_path, :empty) LIKE :old_root) AS old_root,
                   (CAST(params AS text) LIKE :current_root
                    OR CAST(result AS text) LIKE :current_root
                    OR coalesce(task_video_path, :empty) LIKE :current_root) AS current_root
            FROM publication_tasks
            WHERE created_at >= :since AND created_at < :until
            ORDER BY created_at
            """,
            params,
        )
        print("\n== publication task root summary ==")
        for value in summarize_task_roots(task_root_rows):
            print(value)
        _print(
            "old-root publication task rows",
            (row for row in task_root_rows if row["old_root"]),
        )
        publisher_rows = _rows(
            conn,
            """
            SELECT id, status, current_step, created_at, completed_at,
                   round(extract(epoch FROM (coalesce(completed_at, updated_at) - created_at))::numeric, 3) AS duration_s,
                   (CAST(params AS text) LIKE :old_root OR CAST(result AS text) LIKE :old_root OR coalesce(task_video_path, '') LIKE :old_root) AS old_root,
                   result -> 'queue_repair' ->> 'reason' AS repair_reason,
                   result -> 'queue_repair_history' AS repair_history,
                   left(coalesce(error_message, ''), 180) AS error
            FROM publication_tasks
            WHERE feature = 'publisher' AND created_at >= :since AND created_at < :until
            ORDER BY created_at
            """,
            params,
        )
        print("\n== publisher summary ==")
        print(summarize_publisher_rows(publisher_rows))
        _print("publisher rows", publisher_rows)
        _print(
            "notification summary",
            _rows(
                conn,
                """
                SELECT status, count(*) AS count, sum(attempt_count) AS attempts,
                       max(attempt_count) AS max_attempts
                FROM workflow_notification_deliveries
                WHERE created_at >= :since AND created_at < :until
                GROUP BY status ORDER BY count(*) DESC
                """,
                params,
            ),
        )
        _print(
            "zsxq sync summary",
            _rows(
                conn,
                """
                SELECT mode, status, count(*) AS count
                FROM zsxq_sync_runs
                WHERE created_at >= :since AND created_at < :until
                GROUP BY mode, status ORDER BY count(*) DESC
                """,
                params,
            ),
        )
        _print(
            "current database clients",
            _rows(
                conn,
                """
                SELECT client_addr, application_name, state, count(*) AS sessions,
                       max(clock_timestamp() - state_change) AS max_state_age
                FROM pg_stat_activity
                WHERE datname = current_database() AND pid <> pg_backend_pid()
                GROUP BY client_addr, application_name, state
                ORDER BY sessions DESC, client_addr
                """,
                params,
            ),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
