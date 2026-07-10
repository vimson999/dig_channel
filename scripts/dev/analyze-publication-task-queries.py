#!/usr/bin/env python3
"""Summarize publication_tasks query regressions from dedicated and legacy logs."""

import argparse
import hashlib
import re
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LOGS = (
    PROJECT_ROOT / "logs" / "publication_tasks_query.log",
    PROJECT_ROOT / "logs" / "slow_query.log",
)
RECORD_START = re.compile(r"^(?P<timestamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})(?:,\d+)?\s+-")
DURATION = re.compile(
    r"(?:duration_ms=(?P<dedicated>\d+(?:\.\d+)?)|\]\s+(?P<legacy>\d+(?:\.\d+)?)ms)"
)
FINGERPRINT = re.compile(r"fingerprint=(?P<value>[0-9a-f]{8,64})")
CATEGORIES = re.compile(r"categories=(?P<value>[^ ]+)")
ORIGIN = re.compile(r"origin=(?P<value>[^\] ]+)")
PID = re.compile(r"pid=(?P<value>\d+)")
PARAMETER = re.compile(r"%\([^)]+\)s|:\w+|\$\d+")
LITERAL = re.compile(r"'(?:''|[^'])*'|\b\d+(?:\.\d+)?\b")


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", help="Start time: YYYY-MM-DD HH:MM:SS or ISO-8601")
    parser.add_argument("--minutes", type=int, default=0, help="Inspect the most recent N minutes")
    parser.add_argument("--limit", type=int, default=20, help="Maximum signatures to print")
    parser.add_argument("logs", nargs="*", type=Path, help="Optional log files")
    return parser.parse_args()


def parse_since(raw, minutes):
    if raw:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)
    if minutes > 0:
        return datetime.now() - timedelta(minutes=minutes)
    return datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)


def records(path):
    timestamp = None
    lines = []
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            match = RECORD_START.match(line)
            if match:
                if timestamp is not None:
                    yield timestamp, "".join(lines)
                timestamp = datetime.fromisoformat(match.group("timestamp"))
                lines = [line]
            elif timestamp is not None:
                lines.append(line)
    if timestamp is not None:
        yield timestamp, "".join(lines)


def sql_fingerprint(record):
    marker = record.lower().find("select ")
    if marker < 0:
        marker = record.lower().find("update ")
    sql = record[marker:] if marker >= 0 else record
    normalized = PARAMETER.sub("?", sql.lower())
    normalized = LITERAL.sub("?", normalized)
    normalized = " ".join(normalized.split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def classify(record):
    lowered = record.lower()
    categories = []
    if "error_type=" in lowered and "error_type=none" not in lowered:
        categories.append("error")
    if re.search(r"(?:params|result)\s*(?:->|#>)", lowered):
        categories.append("json_predicate")
    select_prefix = lowered.split(" from publication_tasks", 1)[0]
    has_payload = "publication_tasks.params" in select_prefix or "publication_tasks.result" in select_prefix
    point_lookup = bool(re.search(r"publication_tasks.id\s*=\s*(?:%\(|:\w+|\$\d+)", lowered))
    if has_payload and not point_lookup:
        categories.append("multirow_payload")
    if has_payload:
        categories.append("payload")
    return ",".join(categories or ["slow"])


def field(pattern, record, default):
    match = pattern.search(record)
    return match.group("value") if match else default


def percentile(values, ratio):
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * ratio))))
    return ordered[index]


def main():
    args = parse_args()
    since = parse_since(args.since, args.minutes)
    paths = tuple(path.resolve() for path in args.logs) or DEFAULT_LOGS
    grouped = defaultdict(lambda: {"durations": [], "pids": set(), "last": None})
    scanned = 0
    duplicates = 0
    seen = set()

    for path in paths:
        if not path.exists():
            continue
        for timestamp, record in records(path):
            if timestamp < since or "publication_tasks" not in record.lower():
                continue
            duration_match = DURATION.search(record)
            if not duration_match:
                continue
            scanned += 1
            duration = float(duration_match.group("dedicated") or duration_match.group("legacy"))
            fingerprint = field(FINGERPRINT, record, sql_fingerprint(record))
            categories = field(CATEGORIES, record, classify(record))
            origin = field(ORIGIN, record, "unknown")
            pid = field(PID, record, "unknown")
            identity = (timestamp, fingerprint, round(duration, 2), pid, origin)
            if identity in seen:
                duplicates += 1
                continue
            seen.add(identity)
            key = (fingerprint, categories, origin)
            grouped[key]["durations"].append(duration)
            grouped[key]["last"] = timestamp
            grouped[key]["pids"].add(pid)

    print(
        f"publication_tasks audit since={since.isoformat(sep=' ')} "
        f"records={scanned - duplicates} duplicates={duplicates} signatures={len(grouped)}"
    )
    ranked = sorted(
        grouped.items(),
        key=lambda item: (len(item[1]["durations"]), max(item[1]["durations"])),
        reverse=True,
    )
    for (fingerprint, categories, origin), data in ranked[: max(1, args.limit)]:
        durations = data["durations"]
        print(
            f"count={len(durations):4d} avg_ms={sum(durations) / len(durations):9.2f} "
            f"p95_ms={percentile(durations, 0.95):9.2f} max_ms={max(durations):9.2f} "
            f"fingerprint={fingerprint} categories={categories} origin={origin} "
            f"pids={','.join(sorted(data['pids']))} last={data['last'].isoformat(sep=' ')}"
        )


if __name__ == "__main__":
    main()
