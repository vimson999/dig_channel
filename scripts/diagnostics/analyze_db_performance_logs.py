#!/usr/bin/env python3
"""Summarize database performance and connection symptoms from local logs."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import ipaddress
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
    r".*?慢查询 \[(?P<request_id>[^]]+)\] (?P<duration>\d+(?:\.\d+)?)ms"
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
PGBOUNCER_SERVER_SOURCE = re.compile(
    r"new connection to server \(from (?P<address>.+):(?P<port>\d+)\)"
)
SQL_ERROR = re.compile(
    r"publication_tasks SQL categories=[^ ]*error[^ ]*"
    r".*?fingerprint=(?P<fingerprint>[0-9a-f]+)"
    r".*?origin=(?P<origin>[^ ]+)"
    r".*?error_type=(?P<error_type>[^ ]+)"
)
FIELD = re.compile(
    r"(?P<name>request_id|pid|duration_ms|role|process|task_id|task_name|"
    r"checkout_origin|sql_count|sql_total_ms|sql_max_ms|non_sql_hold_ms)="
    r"(?P<value>[^ ]+)"
)
REQUEST_START = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})[,.]\d+"
    r".*?middleware\.request_logger.*?🌐 \[(?P<request_id>[0-9a-f]+)\] "
    r"(?P<method>[A-Z]+) (?P<path>\S+) - IP:"
)
REQUEST_END = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})[,.]\d+"
    r".*?middleware\.request_logger.*?[✅⚠❌]\ufe0f? \[(?P<request_id>[0-9a-f]+)\] "
    r"(?P<status>\d{3}) - (?P<duration>\d+(?:\.\d+)?)ms"
)
CELERY_SUCCESS = re.compile(
    r"Task (?P<task>[^\[]+)\[(?P<task_id>[^]]+)\] succeeded in "
    r"(?P<duration>\d+(?:\.\d+)?)s:"
)
CELERY_RETRY = re.compile(
    r"Task (?P<task>[^\[]+)\[(?P<task_id>[^]]+)\] retry:"
)
CELERY_FAILURE = re.compile(
    r"Task (?P<task>[^\[]+)\[(?P<task_id>[^]]+)\] raised unexpected:"
)
IDLE_PRESSURE_DEFER = re.compile(
    r"\[TASK_GUARD\] defer task start \| task=(?P<task>[^ ]+).*?"
    r"reason=db_idle_in_transaction_pressure"
)
IDLE_PRESSURE_COUNT = re.compile(
    r"(?:idle_in_transaction_connections['\"]?|idle_tx)\s*[:=]\s*(?P<count>\d+)"
)
UUID_PATH_SEGMENT = re.compile(
    r"/(?:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|[0-9a-f]{32})(?=/|$)",
    re.IGNORECASE,
)
NUMERIC_PATH_SEGMENT = re.compile(r"/\d+(?=/|$)")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", help="Start time in ISO or 'YYYY-MM-DD HH:MM:SS' form")
    parser.add_argument("--until", help="End time in ISO or 'YYYY-MM-DD HH:MM:SS' form")
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
    request_id = "unknown"
    lines: list[str] = []
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            match = SLOW_START.match(line)
            if match:
                if timestamp is not None:
                    yield timestamp, duration, origin, pid, request_id, "".join(lines)
                timestamp = datetime.fromisoformat(match.group("timestamp"))
                duration = float(match.group("duration"))
                request_id = match.group("request_id")
                origin_match = ORIGIN.search(line)
                origin = origin_match.group("value") if origin_match else "unknown"
                pid_match = PID.search(line)
                pid = pid_match.group("value") if pid_match else "unknown"
                lines = [line]
            elif timestamp is not None:
                lines.append(line)
    if timestamp is not None:
        yield timestamp, duration, origin, pid, request_id, "".join(lines)


def normalize_endpoint(path: str) -> str:
    """Collapse entity identifiers without hiding API version or route structure."""
    normalized = UUID_PATH_SEGMENT.sub("/:id", str(path or "unknown"))
    return NUMERIC_PATH_SEGMENT.sub("/:id", normalized)


def open_log(path: Path):
    """Open plain-text and gzip logs with the same tolerant text semantics."""
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8", errors="ignore")
    return path.open("r", encoding="utf-8", errors="ignore")


def rotated_log_may_overlap(path: Path, since: datetime | None) -> bool:
    """Use rotation time to avoid scanning archives that predate the window."""
    if since is None:
        return True
    return datetime.fromtimestamp(path.stat().st_mtime) >= since


def discover_event_logs(since: datetime | None = None) -> list[Path]:
    """Include active and rotated logs that may contain the window.

    Several long-running processes share the project's logging handlers. During
    size-based rotation, web request lines can therefore land in files such as
    ``scheduler.log.1`` even though they originated in the web process. Reading
    only the active filename silently drops those requests from the audit.
    """
    paths: set[Path] = set()
    for base_path in DEFAULT_EVENT_LOGS:
        paths.add(base_path.resolve())
        paths.update(
            candidate.resolve()
            for candidate in base_path.parent.glob(base_path.name + ".*")
            if candidate.is_file() and rotated_log_may_overlap(candidate, since)
        )
    paths.update(
        path.resolve()
        for pattern in ("*.log", "*.log.*")
        for path in (ROOT / "logs" / "celery").glob(pattern)
        if path.is_file()
        and (path.suffix != ".gz" or rotated_log_may_overlap(path, since))
    )
    return sorted(paths)


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


def pgbouncer_event_types(line: str) -> set[str]:
    """Classify pooler failures without double-counting their closing lines."""
    lowered = line.lower()
    categories: set[str] = set()
    if "connect timeout" in lowered:
        categories.add("connect_timeout")
    if "query_wait_timeout" in lowered:
        categories.add("query_wait_timeout")
    if "query timeout" in lowered:
        categories.add("query_timeout")
    if "closing because: server conn crashed?" in lowered and " s-" in lowered:
        categories.add("server_conn_crashed")
    if "tls_sbufio_recv: read failed:" in lowered:
        categories.add("tls_receive_error")
    if "warning server login failed" in lowered:
        categories.add("server_login_failed")
        if "no pg_hba.conf entry" in lowered:
            categories.add("hba_rejected")
    if "pooler error: server login has been failing" in lowered:
        categories.add("cached_server_login_failure")
    return categories


def pgbouncer_server_source(line: str) -> str | None:
    match = PGBOUNCER_SERVER_SOURCE.search(line)
    return match.group("address") if match else None


def is_proxy_tun_source(address: str) -> bool:
    try:
        return ipaddress.ip_address(address) in ipaddress.ip_network("198.18.0.0/15")
    except ValueError:
        return False


def is_adhoc_diagnostic_context(
    fields: dict[str, str],
    *,
    origin: str = "unknown",
) -> bool:
    """Separate one-off shell/diagnostic SQL from managed application traffic."""
    role = str(fields.get("role") or "unknown").strip().lower()
    process = str(fields.get("process") or "unknown").strip().lower()
    normalized_origin = str(origin or "unknown").strip().lower()
    return (
        role == "unknown"
        and normalized_origin == "unknown"
        and process in {"-", "-c", "python", "python3", "unknown"}
    )


def main() -> int:
    args = parse_args()
    since = (
        datetime.fromisoformat(args.since.replace("Z", "+00:00")).replace(tzinfo=None)
        if args.since
        else datetime.now() - timedelta(hours=args.hours)
    )
    until = (
        datetime.fromisoformat(args.until.replace("Z", "+00:00")).replace(tzinfo=None)
        if args.until
        else datetime.now()
    )

    durations: list[float] = []
    application_durations: list[float] = []
    adhoc_durations: list[float] = []
    slow_samples: list[tuple[datetime, float, str, str]] = []
    grouped: dict[tuple[str, str], list[float]] = defaultdict(list)
    slow_by_request: dict[str, list[float]] = defaultdict(list)
    slow_tables: Counter[str] = Counter()
    if SLOW_LOG.exists():
        for timestamp, duration, origin, pid, request_id, record in slow_records(SLOW_LOG):
            if timestamp < since or timestamp > until:
                continue
            if is_test_record(record):
                continue
            durations.append(duration)
            slow_samples.append((timestamp, duration, pid, request_id))
            record_fields = {
                match.group("name"): match.group("value")
                for match in FIELD.finditer(record)
            }
            is_adhoc = is_adhoc_diagnostic_context(record_fields, origin=origin)
            (adhoc_durations if is_adhoc else application_durations).append(duration)
            if not is_adhoc:
                grouped[(fingerprint(record), origin)].append(duration)
            if request_id != "unknown":
                slow_by_request[request_id].append(duration)
            normalized = normalize_sql(record)
            if not is_adhoc:
                for table_name in TARGET_TABLES:
                    if re.search(rf"\b{re.escape(table_name)}\b", normalized):
                        slow_tables[table_name] += 1

    print(f"window={since.isoformat(sep=' ')}..{until.isoformat(sep=' ')}")
    print(f"slow_queries {stats(durations)}")
    print(f"slow_queries_application {stats(application_durations)}")
    print(f"slow_queries_adhoc_diagnostics {stats(adhoc_durations)}")
    print(
        f"slow_query_totals application_ms={sum(application_durations):.2f} "
        f"adhoc_diagnostics_ms={sum(adhoc_durations):.2f} all_ms={sum(durations):.2f}"
    )
    print(
        "slow_tables "
        + " ".join(f"{table_name}={slow_tables[table_name]}" for table_name in TARGET_TABLES)
    )
    fingerprint_rankings = (
        ("total", lambda item: (sum(item[1]), percentile(item[1], 0.95), max(item[1]))),
        ("p95", lambda item: (percentile(item[1], 0.95), sum(item[1]), max(item[1]))),
        ("max", lambda item: (max(item[1]), sum(item[1]), percentile(item[1], 0.95))),
        ("count", lambda item: (len(item[1]), sum(item[1]), max(item[1]))),
    )
    for rank_name, rank_key in fingerprint_rankings:
        for (sql_fingerprint, origin), values in sorted(
            grouped.items(), key=rank_key, reverse=True
        )[: max(1, args.limit)]:
            print(
                f"slow_fingerprint_by_{rank_name}={sql_fingerprint} {stats(values)} "
                f"total_ms={sum(values):.2f} avg_ms={sum(values) / len(values):.2f} "
                f"origin={origin}"
            )

    paths = [path.resolve() for path in args.logs]
    if not paths:
        paths = discover_event_logs(since)
    event_counts: Counter[str] = Counter()
    lifecycle: list[float] = []
    lifecycle_samples: list[dict[str, object]] = []
    sql_errors: Counter[tuple[str, str, str]] = Counter()
    application_sql_errors: Counter[tuple[str, str, str]] = Counter()
    adhoc_sql_errors: Counter[tuple[str, str, str]] = Counter()
    seen: set[tuple[datetime, str, str, str]] = set()
    sql_error_seen: set[tuple[str, str, str, str, str, str]] = set()
    request_starts: dict[str, tuple[datetime, str, str]] = {}
    request_ends: dict[str, tuple[datetime, int, float]] = {}
    celery_successes: dict[str, list[float]] = defaultdict(list)
    celery_retries: Counter[str] = Counter()
    celery_failures: Counter[str] = Counter()
    celery_seen: set[tuple[datetime, str, str, str]] = set()
    idle_pressure_deferrals: set[tuple[datetime, str]] = set()
    idle_pressure_observations: set[tuple[datetime, int]] = set()
    for path in paths:
        if not path.exists():
            continue
        with open_log(path) as handle:
            for raw_line in handle:
                line = ANSI.sub("", raw_line)
                timestamp_match = TIMESTAMP.match(line)
                if not timestamp_match:
                    continue
                timestamp = datetime.fromisoformat(timestamp_match.group("value"))
                if timestamp < since or timestamp > until:
                    continue
                idle_pressure_defer_match = IDLE_PRESSURE_DEFER.search(line)
                if idle_pressure_defer_match:
                    idle_pressure_deferrals.add(
                        (timestamp, idle_pressure_defer_match.group("task"))
                    )
                idle_pressure_count_match = IDLE_PRESSURE_COUNT.search(line)
                if idle_pressure_count_match:
                    idle_pressure_observations.add(
                        (timestamp, int(idle_pressure_count_match.group("count")))
                    )
                request_start_match = REQUEST_START.search(line)
                if request_start_match:
                    request_starts.setdefault(
                        request_start_match.group("request_id"),
                        (
                            timestamp,
                            request_start_match.group("method"),
                            normalize_endpoint(request_start_match.group("path")),
                        ),
                    )
                request_end_match = REQUEST_END.search(line)
                if request_end_match:
                    request_ends.setdefault(
                        request_end_match.group("request_id"),
                        (
                            timestamp,
                            int(request_end_match.group("status")),
                            float(request_end_match.group("duration")),
                        ),
                    )
                for outcome, pattern in (
                    ("success", CELERY_SUCCESS),
                    ("retry", CELERY_RETRY),
                    ("failure", CELERY_FAILURE),
                ):
                    task_match = pattern.search(line)
                    if not task_match:
                        continue
                    task_name = task_match.group("task")
                    identity = (
                        timestamp,
                        task_name,
                        task_match.group("task_id"),
                        outcome,
                    )
                    if identity in celery_seen:
                        break
                    celery_seen.add(identity)
                    if outcome == "success":
                        celery_successes[task_name].append(
                            float(task_match.group("duration")) * 1000.0
                        )
                    elif outcome == "retry":
                        celery_retries[task_name] += 1
                    else:
                        celery_failures[task_name] += 1
                    break
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
                            lifecycle_samples.append({
                                "timestamp": timestamp,
                                "held_ms": held_ms,
                                "pid": fields.get("pid", "unknown"),
                                "role": fields.get("role", "unknown"),
                                "process": fields.get("process", "unknown"),
                                "request_id": fields.get("request_id", "unknown"),
                                "task_id": fields.get("task_id", "unknown"),
                                "task_name": fields.get("task_name", "unknown"),
                                "checkout_origin": fields.get("checkout_origin", "unknown"),
                                "sql_count": int(float(fields.get("sql_count", 0) or 0)),
                                "sql_total_ms": float(fields.get("sql_total_ms", 0.0) or 0.0),
                                "sql_max_ms": float(fields.get("sql_max_ms", 0.0) or 0.0),
                                "non_sql_hold_ms": float(
                                    fields.get("non_sql_hold_ms", held_ms) or held_ms
                                ),
                                "has_breakdown": "sql_total_ms" in fields,
                            })
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
                        target = (
                            adhoc_sql_errors
                            if is_adhoc_diagnostic_context(
                                fields,
                                origin=error_match.group("origin"),
                            )
                            else application_sql_errors
                        )
                        target[error_key] += 1

    print(
        "events "
        + " ".join(f"{key}={event_counts[key]}" for key in EVENT_CATEGORIES)
    )
    print(
        "idle_in_transaction_pressure "
        f"deferrals={len(idle_pressure_deferrals)} "
        f"affected_tasks={len({task for _timestamp, task in idle_pressure_deferrals})} "
        f"observations={len(idle_pressure_observations)} "
        f"max_connections={max((count for _timestamp, count in idle_pressure_observations), default=0)}"
    )
    print(f"connection_holds {stats(lifecycle)}")
    lifecycle_by_role: dict[str, list[float]] = defaultdict(list)
    lifecycle_adhoc: list[float] = []
    lifecycle_by_origin: dict[str, list[dict[str, object]]] = defaultdict(list)
    for sample in lifecycle_samples:
        held_ms = float(sample["held_ms"])
        held_role = str(sample["role"])
        held_process = str(sample["process"])
        lifecycle_by_role[held_role].append(held_ms)
        lifecycle_by_origin[str(sample["checkout_origin"])].append(sample)
        if is_adhoc_diagnostic_context(
            {"role": held_role, "process": held_process},
        ):
            lifecycle_adhoc.append(held_ms)
    for role, values in sorted(
        lifecycle_by_role.items(),
        key=lambda item: (len(item[1]), max(item[1])),
        reverse=True,
    ):
        print(f"connection_holds_by_role role={role} {stats(values)}")
    print(f"connection_holds_adhoc_diagnostics {stats(lifecycle_adhoc)}")
    samples_with_breakdown = [
        sample for sample in lifecycle_samples if bool(sample["has_breakdown"])
    ]
    print(
        "connection_hold_breakdown "
        f"samples={len(samples_with_breakdown)} "
        f"sql_total_ms={sum(float(item['sql_total_ms']) for item in samples_with_breakdown):.2f} "
        f"non_sql_total_ms={sum(float(item['non_sql_hold_ms']) for item in samples_with_breakdown):.2f}"
    )
    for origin, samples in sorted(
        lifecycle_by_origin.items(),
        key=lambda item: (
            sum(float(sample["non_sql_hold_ms"]) for sample in item[1]),
            max(float(sample["held_ms"]) for sample in item[1]),
        ),
        reverse=True,
    )[: max(1, args.limit)]:
        print(
            "connection_holds_by_origin "
            f"origin={origin} count={len(samples)} "
            f"total_ms={sum(float(item['held_ms']) for item in samples):.2f} "
            f"non_sql_total_ms={sum(float(item['non_sql_hold_ms']) for item in samples):.2f} "
            f"max_ms={max(float(item['held_ms']) for item in samples):.2f}"
        )
    query_correlated: list[float] = []
    uncorrelated: list[float] = []
    managed_uncorrelated: list[float] = []
    managed_uncorrelated_samples: list[dict[str, object]] = []
    for sample in lifecycle_samples:
        held_at = sample["timestamp"]
        held_ms = float(sample["held_ms"])
        held_pid = str(sample["pid"])
        held_role = str(sample["role"])
        held_process = str(sample["process"])
        if bool(sample["has_breakdown"]):
            correlated = float(sample["non_sql_hold_ms"]) <= 1500.0
        else:
            correlated = any(
                slow_pid == held_pid
                and abs((slow_at - held_at).total_seconds()) <= 2
                and abs(slow_ms - held_ms) <= 1500
                for slow_at, slow_ms, slow_pid, _slow_request_id in slow_samples
            )
        (query_correlated if correlated else uncorrelated).append(held_ms)
        if not correlated and not is_adhoc_diagnostic_context(
            {"role": held_role, "process": held_process},
        ):
            managed_uncorrelated.append(held_ms)
            managed_uncorrelated_samples.append(sample)
    print(f"connection_holds_query_correlated {stats(query_correlated)}")
    print(f"connection_holds_without_matching_slow_sql {stats(uncorrelated)}")
    print(
        "connection_holds_without_matching_slow_sql_managed "
        + stats(managed_uncorrelated)
    )
    for sample in sorted(
        managed_uncorrelated_samples,
        key=lambda item: float(item["held_ms"]),
        reverse=True,
    )[: max(1, args.limit)]:
        print(
            "connection_hold_without_matching_slow_sql_managed "
            f"at={sample['timestamp'].isoformat(sep=' ')} "
            f"duration_ms={float(sample['held_ms']):.2f} pid={sample['pid']} "
            f"role={sample['role']} process={sample['process']} "
            f"request_id={sample['request_id']} task_id={sample['task_id']} "
            f"task_name={sample['task_name']} origin={sample['checkout_origin']} "
            f"sql_count={sample['sql_count']} sql_total_ms={float(sample['sql_total_ms']):.2f} "
            f"non_sql_hold_ms={float(sample['non_sql_hold_ms']):.2f}"
        )
    endpoint_groups: dict[tuple[str, str], list[tuple[str, int, float]]] = defaultdict(list)
    incomplete_requests = 0
    for request_id, (_started_at, method, endpoint) in request_starts.items():
        finished = request_ends.get(request_id)
        if finished is None:
            incomplete_requests += 1
            continue
        _finished_at, status_code, duration_ms = finished
        endpoint_groups[(method, endpoint)].append(
            (request_id, status_code, duration_ms)
        )
    print(
        f"requests completed={sum(len(values) for values in endpoint_groups.values())} "
        f"incomplete={incomplete_requests} endpoints={len(endpoint_groups)}"
    )
    endpoint_rows = []
    for (method, endpoint), values in endpoint_groups.items():
        durations_for_endpoint = [value[2] for value in values]
        failures = sum(1 for _request_id, status, _duration in values if status >= 400)
        matching_sql = [
            sql_duration
            for request_id, _status, _duration in values
            for sql_duration in slow_by_request.get(request_id, ())
        ]
        endpoint_rows.append(
            (
                method,
                endpoint,
                values,
                durations_for_endpoint,
                failures,
                matching_sql,
            )
        )
    all_request_durations = [
        duration
        for _method, _endpoint, values, _durations, _failures, _matching_sql in endpoint_rows
        for _request_id, _status, duration in values
    ]
    all_request_failures = sum(row[4] for row in endpoint_rows)
    print(
        f"request_totals {stats(all_request_durations)} "
        f"success={len(all_request_durations) - all_request_failures} "
        f"failure={all_request_failures} "
        f"failure_rate={all_request_failures / max(len(all_request_durations), 1):.4f} "
        f"total_ms={sum(all_request_durations):.2f}"
    )
    for method, endpoint, values, endpoint_durations, failures, matching_sql in sorted(
        endpoint_rows,
        key=lambda row: (
            sum(row[3]),
            percentile(row[3], 0.95),
            max(row[3]),
            row[4] / len(row[2]),
        ),
        reverse=True,
    )[: max(1, args.limit)]:
        worst_request_id, _worst_status, worst_duration = max(
            values,
            key=lambda value: value[2],
        )
        worst_started_at = request_starts[worst_request_id][0]
        print(
            f"endpoint method={method} path={endpoint} {stats(endpoint_durations)} "
            f"success={len(values) - failures} failure={failures} "
            f"failure_rate={failures / len(values):.4f} total_ms={sum(endpoint_durations):.2f} "
            f"slow_sql_count={len(matching_sql)} slow_sql_total_ms={sum(matching_sql):.2f} "
            f"worst_request_id={worst_request_id} "
            f"worst_started_at={worst_started_at.isoformat(sep=' ')} "
            f"worst_duration_ms={worst_duration:.2f}"
        )
    for method, endpoint, values, endpoint_durations, failures, matching_sql in sorted(
        (
            row
            for row in endpoint_rows
            if len(row[2]) <= 5 and max(row[3]) >= 10000
        ),
        key=lambda row: max(row[3]),
        reverse=True,
    )[: max(1, args.limit)]:
        print(
            f"endpoint_low_frequency_high_latency method={method} path={endpoint} "
            f"{stats(endpoint_durations)} failure={failures} total_ms={sum(endpoint_durations):.2f} "
            f"slow_sql_count={len(matching_sql)} slow_sql_total_ms={sum(matching_sql):.2f}"
        )
    for method, endpoint, values, endpoint_durations, failures, matching_sql in sorted(
        (
            row
            for row in endpoint_rows
            if not row[5] and max(row[3]) >= 1000
        ),
        key=lambda row: (sum(row[3]), max(row[3])),
        reverse=True,
    )[: max(1, args.limit)]:
        print(
            f"endpoint_slow_without_matching_sql method={method} path={endpoint} "
            f"{stats(endpoint_durations)} failure={failures} total_ms={sum(endpoint_durations):.2f}"
        )
    celery_task_names = set(celery_successes) | set(celery_retries) | set(celery_failures)
    print(
        f"celery_tasks completed={sum(map(len, celery_successes.values()))} "
        f"retry={sum(celery_retries.values())} failed={sum(celery_failures.values())} "
        f"types={len(celery_task_names)} "
        f"success_total_ms={sum(sum(values) for values in celery_successes.values()):.2f}"
    )
    for task_name in sorted(
        celery_task_names,
        key=lambda name: (
            sum(celery_successes[name]),
            max(celery_successes[name], default=0.0),
            celery_failures[name],
            celery_retries[name],
        ),
        reverse=True,
    )[: max(1, args.limit)]:
        values = celery_successes[task_name]
        print(
            f"celery_task name={task_name} {stats(values)} "
            f"success={len(values)} retry={celery_retries[task_name]} "
            f"failure={celery_failures[task_name]} total_ms={sum(values):.2f}"
        )
    print(f"sql_errors count={sum(sql_errors.values())} signatures={len(sql_errors)}")
    for label, error_counter in (
        ("application", application_sql_errors),
        ("adhoc_diagnostics", adhoc_sql_errors),
    ):
        print(
            f"sql_errors_{label} count={sum(error_counter.values())} "
            f"signatures={len(error_counter)}"
        )
        for (sql_fingerprint, origin, error_name), count in error_counter.most_common(
            args.limit
        ):
            print(
                f"sql_error_{label} count={count} fingerprint={sql_fingerprint} "
                f"error_type={error_name} origin={origin}"
            )

    pgbouncer_path = ROOT / "logs" / "pgbouncer.log"
    pgbouncer_counts: Counter[str] = Counter()
    pgbouncer_events: dict[str, set[datetime]] = defaultdict(set)
    pgbouncer_server_sources: Counter[str] = Counter()
    pgbouncer_proxy_tun_timestamps: list[datetime] = []
    pgbouncer_wait_ms: list[float] = []
    pgbouncer_query_ms: list[float] = []
    pgbouncer_paths = [pgbouncer_path]
    pgbouncer_paths.extend(
        path
        for path in pgbouncer_path.parent.glob(pgbouncer_path.name + ".*")
        if path.is_file() and rotated_log_may_overlap(path, since)
    )
    for current_pgbouncer_path in pgbouncer_paths:
        if not current_pgbouncer_path.exists():
            continue
        with open_log(current_pgbouncer_path) as handle:
            for line in handle:
                timestamp_match = TIMESTAMP.match(line)
                if not timestamp_match:
                    continue
                timestamp = datetime.fromisoformat(timestamp_match.group("value"))
                if timestamp < since or timestamp > until:
                    continue
                for category in pgbouncer_event_types(line):
                    pgbouncer_counts[category] += 1
                    pgbouncer_events[category].add(timestamp)
                server_source = pgbouncer_server_source(line)
                if server_source:
                    pgbouncer_server_sources[server_source] += 1
                    if is_proxy_tun_source(server_source):
                        pgbouncer_proxy_tun_timestamps.append(timestamp)
                stat_match = PGBOUNCER_STATS.search(line)
                if stat_match:
                    pgbouncer_wait_ms.append(float(stat_match.group("wait")) / 1000.0)
                    pgbouncer_query_ms.append(float(stat_match.group("query")) / 1000.0)
    print(
        "pgbouncer "
        + " ".join(
            f"{key}={pgbouncer_counts[key]}"
            for key in (
                "connect_timeout",
                "query_timeout",
                "query_wait_timeout",
                "server_conn_crashed",
                "tls_receive_error",
                "server_login_failed",
                "hba_rejected",
                "cached_server_login_failure",
            )
        )
        + f" stats_samples={len(pgbouncer_wait_ms)}"
    )
    print(
        "pgbouncer_events "
        + " ".join(
            f"{key}={len(pgbouncer_events[key])}"
            for key in (
                "connect_timeout",
                "query_timeout",
                "query_wait_timeout",
                "server_conn_crashed",
                "tls_receive_error",
                "server_login_failed",
                "hba_rejected",
                "cached_server_login_failure",
            )
        )
    )
    for address, count in pgbouncer_server_sources.most_common():
        print(
            "pgbouncer_server_source "
            f"local_address={address} connections={count} "
            f"proxy_tun={str(is_proxy_tun_source(address)).lower()}"
        )
    if pgbouncer_proxy_tun_timestamps:
        print(
            "pgbouncer_proxy_tun_fallback "
            f"connections={len(pgbouncer_proxy_tun_timestamps)} "
            f"first_at={min(pgbouncer_proxy_tun_timestamps).isoformat(sep=' ')} "
            f"last_at={max(pgbouncer_proxy_tun_timestamps).isoformat(sep=' ')}"
        )
    else:
        print("pgbouncer_proxy_tun_fallback connections=0")
    print(f"pgbouncer_wait {stats(pgbouncer_wait_ms)}")
    print(f"pgbouncer_query {stats(pgbouncer_query_ms)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
