#!/usr/bin/env python3
"""Summarize database performance and connection symptoms from local logs."""

from __future__ import annotations

import argparse
import hashlib
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TARGET_TABLES = (
    "publication_tasks",
    "zsxq_sync_runs",
    "zsxq_topics",
    "zsxq_topic_comments",
    "zsxq_attachments",
    "external_documents",
)
SLOW_LOG = ROOT / "logs" / "slow_query.log"
DEFAULT_EVENT_LOGS = (
    ROOT / "logs" / "app.log",
    ROOT / "logs" / "backend.log",
    ROOT / "logs" / "scheduler.log",
    ROOT / "logs" / "publication_tasks_query.log",
)
EVENT_CATEGORIES = (
    "connection_held",
    "server_closed",
    "statement_timeout",
    "lock_timeout",
    "deadlock",
    "query_timeout",
    "connection_timeout",
    "idle_in_transaction",
)
TIMESTAMP = re.compile(
    r"^\[?(?P<value>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})[,.]\d+"
)
SLOW_START = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})[,.]\d+"
    r".*?慢查询 \[[^]]+\] (?P<duration>\d+(?:\.\d+)?)ms"
)
ORIGIN = re.compile(r"origin=(?P<value>[^] ]+)")
PID = re.compile(r"pid=(?P<value>\d+)")
PARAMETER = re.compile(r"%\([^)]+\)s|:\w+|\$\d+")
LITERAL = re.compile(r"'(?:''|[^'])*'|\b\d+(?:\.\d+)?\b")
ANSI = re.compile(r"\x1b\[[0-9;]*m")
LIFECYCLE_DURATION = re.compile(
    r"db_connection_lifecycle.*?duration_ms=(?P<duration>\d+(?:\.\d+)?)"
)
PGBOUNCER_STATS = re.compile(
    r"stats:.*?xact (?P<xact>\d+) us, query (?P<query>\d+) us, wait (?P<wait>\d+) us"
)
SQL_ERROR = re.compile(
    r"publication_tasks SQL categories=[^ ]*error[^ ]*"
    r".*?fingerprint=(?P<fingerprint>[0-9a-f]+)"
    r".*?origin=(?P<origin>[^ ]+)"
    r".*?error_type=(?P<error_type>[^ ]+)"
)
FIELD = re.compile(r"(?P<name>request_id|pid|duration_ms)=(?P<value>[^ ]+)")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", help="Start time in ISO or 'YYYY-MM-DD HH:MM:SS' form")
    parser.add_argument("--hours", type=float, default=24.0)
    parser.add_argument("--limit", type=int, default=15)
    parser.add_argument("logs", nargs="*", type=Path)
    return parser.parse_args()


def percentile(values: list[float], ratio: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * ratio)))
    return ordered[index]


def stats(values: list[float]) -> str:
    if not values:
        return "count=0 p50_ms=0.00 p95_ms=0.00 max_ms=0.00"
    return (
        f"count={len(values)} p50_ms={percentile(values, 0.50):.2f} "
        f"p95_ms={percentile(values, 0.95):.2f} max_ms={max(values):.2f}"
    )


def normalize_sql(record: str) -> str:
    lowered = record.lower()
    offsets = [
        offset
        for verb in ("select ", "update ", "insert ", "delete ", "alter ", "create ", "drop ")
        if (offset := lowered.find(verb)) >= 0
    ]
    sql = record[min(offsets) :] if offsets else record
    sql = PARAMETER.sub("?", sql.lower())
    sql = LITERAL.sub("?", sql)
    return " ".join(sql.split())


def fingerprint(record: str) -> str:
    return hashlib.sha256(normalize_sql(record).encode("utf-8")).hexdigest()[:16]


def is_test_record(record: str) -> bool:
    lowered = record.lower()
    if "role=test" in lowered or "dialect=sqlite" in lowered:
        return True
    if "process=pytest" in lowered or "process=py.test" in lowered:
        return True
    sql = record.split("]:", 1)[-1]
    return (
        "process=__main__.py" in lowered
        and "?" in sql
        and "%(" not in sql
        and "$1" not in sql
    )


def slow_records(path: Path):
    timestamp = None
    duration = None
    origin = "unknown"
    pid = "unknown"
    lines: list[str] = []
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            match = SLOW_START.match(line)
            if match:
                if timestamp is not None:
                    yield timestamp, duration, origin, pid, "".join(lines)
                timestamp = datetime.fromisoformat(match.group("timestamp"))
                duration = float(match.group("duration"))
                origin_match = ORIGIN.search(line)
                origin = origin_match.group("value") if origin_match else "unknown"
                pid_match = PID.search(line)
                pid = pid_match.group("value") if pid_match else "unknown"
                lines = [line]
            elif timestamp is not None:
                lines.append(line)
    if timestamp is not None:
        yield timestamp, duration, origin, pid, "".join(lines)


def event_type(line: str) -> str | None:
    lowered = line.lower()
    if "db_connection_lifecycle" in lowered:
        return "connection_held"
    if "server closed the connection unexpectedly" in lowered:
        return "server_closed"
    if "canceling statement due to statement timeout" in lowered or "statement timeout" in lowered:
        return "statement_timeout"
    if "canceling statement due to lock timeout" in lowered or "lock timeout" in lowered:
        return "lock_timeout"
    if "deadlock detected" in lowered:
        return "deadlock"
    if "query timeout" in lowered:
        return "query_timeout"
    if (
        "connect timeout" in lowered
        or "connection timed out" in lowered
        or "queuepool limit" in lowered
    ):
        return "connection_timeout"
    if "idle in transaction" in lowered and not any(
        marker in lowered for marker in ("select ", "where ", "state =")
    ):
        return "idle_in_transaction"
    return None


def main() -> int:
    args = parse_args()
    since = (
        datetime.fromisoformat(args.since.replace("Z", "+00:00")).replace(tzinfo=None)
        if args.since
        else datetime.now() - timedelta(hours=args.hours)
    )
    until = datetime.now()

    durations: list[float] = []
    slow_samples: list[tuple[datetime, float, str]] = []
    grouped: dict[tuple[str, str], list[float]] = defaultdict(list)
    slow_tables: Counter[str] = Counter()
    if SLOW_LOG.exists():
        for timestamp, duration, origin, pid, record in slow_records(SLOW_LOG):
            if timestamp < since or timestamp > until:
                continue
            if is_test_record(record):
                continue
            durations.append(duration)
            slow_samples.append((timestamp, duration, pid))
            grouped[(fingerprint(record), origin)].append(duration)
            normalized = normalize_sql(record)
            for table_name in TARGET_TABLES:
                if re.search(rf"\b{re.escape(table_name)}\b", normalized):
                    slow_tables[table_name] += 1

    print(f"window={since.isoformat(sep=' ')}..{until.isoformat(sep=' ')}")
    print(f"slow_queries {stats(durations)}")
    print(
        "slow_tables "
        + " ".join(f"{table_name}={slow_tables[table_name]}" for table_name in TARGET_TABLES)
    )
    for (sql_fingerprint, origin), values in sorted(
        grouped.items(),
        key=lambda item: (len(item[1]), max(item[1])),
        reverse=True,
    )[: max(1, args.limit)]:
        print(
            f"slow_fingerprint={sql_fingerprint} {stats(values)} "
            f"avg_ms={sum(values) / len(values):.2f} origin={origin}"
        )

    paths = [path.resolve() for path in args.logs]
    if not paths:
        paths = [*DEFAULT_EVENT_LOGS]
        paths.extend(sorted((ROOT / "logs" / "celery").glob("*.log")))
    event_counts: Counter[str] = Counter()
    lifecycle: list[float] = []
    lifecycle_samples: list[tuple[datetime, float, str]] = []
    sql_errors: Counter[tuple[str, str, str]] = Counter()
    seen: set[tuple[datetime, str, str, str]] = set()
    sql_error_seen: set[tuple[str, str, str, str, str, str]] = set()
    for path in paths:
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            for raw_line in handle:
                line = ANSI.sub("", raw_line)
                timestamp_match = TIMESTAMP.match(line)
                if not timestamp_match:
                    continue
                timestamp = datetime.fromisoformat(timestamp_match.group("value"))
                if timestamp < since or timestamp > until:
                    continue
                category = event_type(line)
                if category:
                    fields = {
                        match.group("name"): match.group("value")
                        for match in FIELD.finditer(line)
                    }
                    identity = (
                        timestamp,
                        category,
                        fields.get("pid", "unknown"),
                        fields.get("duration_ms", "unknown"),
                    )
                    if identity not in seen:
                        seen.add(identity)
                        event_counts[category] += 1
                        duration_match = LIFECYCLE_DURATION.search(line)
                        if duration_match:
                            held_ms = float(duration_match.group("duration"))
                            lifecycle.append(held_ms)
                            lifecycle_samples.append(
                                (timestamp, held_ms, fields.get("pid", "unknown"))
                            )
                error_match = SQL_ERROR.search(line)
                if error_match:
                    fields = {
                        match.group("name"): match.group("value")
                        for match in FIELD.finditer(line)
                    }
                    error_key = (
                        error_match.group("fingerprint"),
                        error_match.group("origin"),
                        error_match.group("error_type"),
                    )
                    error_identity = (
                        timestamp.isoformat(),
                        *error_key,
                        fields.get("request_id", "unknown"),
                        fields.get("pid", "unknown"),
                        fields.get("duration_ms", "unknown"),
                    )
                    if error_identity not in sql_error_seen:
                        sql_error_seen.add(error_identity)
                        sql_errors[error_key] += 1

    print(
        "events "
        + " ".join(f"{key}={event_counts[key]}" for key in EVENT_CATEGORIES)
    )
    print(f"connection_holds {stats(lifecycle)}")
    query_correlated: list[float] = []
    uncorrelated: list[float] = []
    for held_at, held_ms, held_pid in lifecycle_samples:
        correlated = any(
            slow_pid == held_pid
            and abs((slow_at - held_at).total_seconds()) <= 2
            and abs(slow_ms - held_ms) <= 1500
            for slow_at, slow_ms, slow_pid in slow_samples
        )
        (query_correlated if correlated else uncorrelated).append(held_ms)
    print(f"connection_holds_query_correlated {stats(query_correlated)}")
    print(f"connection_holds_without_matching_slow_sql {stats(uncorrelated)}")
    print(f"sql_errors count={sum(sql_errors.values())} signatures={len(sql_errors)}")
    for (sql_fingerprint, origin, error_name), count in sql_errors.most_common(args.limit):
        print(
            f"sql_error count={count} fingerprint={sql_fingerprint} "
            f"error_type={error_name} origin={origin}"
        )

    pgbouncer_path = ROOT / "logs" / "pgbouncer.log"
    pgbouncer_counts: Counter[str] = Counter()
    pgbouncer_wait_ms: list[float] = []
    pgbouncer_query_ms: list[float] = []
    if pgbouncer_path.exists():
        with pgbouncer_path.open("r", encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                timestamp_match = TIMESTAMP.match(line)
                if not timestamp_match:
                    continue
                timestamp = datetime.fromisoformat(timestamp_match.group("value"))
                if timestamp < since or timestamp > until:
                    continue
                lowered = line.lower()
                if "connect timeout" in lowered:
                    pgbouncer_counts["connect_timeout"] += 1
                if "query timeout" in lowered:
                    pgbouncer_counts["query_timeout"] += 1
                stat_match = PGBOUNCER_STATS.search(line)
                if stat_match:
                    pgbouncer_wait_ms.append(float(stat_match.group("wait")) / 1000.0)
                    pgbouncer_query_ms.append(float(stat_match.group("query")) / 1000.0)
    print(
        "pgbouncer "
        + " ".join(
            f"{key}={pgbouncer_counts[key]}"
            for key in ("connect_timeout", "query_timeout")
        )
        + f" stats_samples={len(pgbouncer_wait_ms)}"
    )
    print(f"pgbouncer_wait {stats(pgbouncer_wait_ms)}")
    print(f"pgbouncer_query {stats(pgbouncer_query_ms)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
