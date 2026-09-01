#!/usr/bin/env python3
"""Run an auditable, read-only workload A/B for PgBouncer server_lifetime.

The command is a dry-run unless ``--apply`` is supplied.  Applying a candidate
only reloads PgBouncer; it never restarts the application or database.  The
original config is restored unless ``--keep-config`` is explicit.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import statistics
import time
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PROJECT_ROOT / "runtime" / "pgbouncer" / "pgbouncer.ini"
DEFAULT_PID_FILE = PROJECT_ROOT / "runtime" / "pgbouncer" / "pgbouncer.pid"
DEFAULT_LOG = PROJECT_ROOT / "logs" / "pgbouncer.log"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "artifacts" / "db-performance"
APPROVED_CANDIDATES = (900, 1800)
LIFETIME_RE = re.compile(r"(?m)^server_lifetime\s*=\s*(\d+)\s*$")
TIMESTAMP_RE = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d+)?)"
)
STATS_RE = re.compile(
    r"\bquery\s+(?P<query>\d+)\s+us,\s+wait\s+(?P<wait>\d+)\s+us"
)
SERVER_SOURCE_RE = re.compile(r"new connection to server \(from (?P<source>[^: )]+):")
FAILURE_MARKERS = {
    "connect_timeout": "connect timeout",
    "query_timeout": "query timeout",
    "query_wait_timeout": "query_wait_timeout",
    "server_conn_crashed": "closing because: server conn crashed?",
    "tls_receive_error": "tls_sbufio_recv: read failed:",
    "server_login_failed": "warning server login failed",
}


def replace_server_lifetime(config_text, candidate):
    candidate = int(candidate)
    if candidate not in APPROVED_CANDIDATES:
        raise ValueError(
            f"server_lifetime candidate must be one of {APPROVED_CANDIDATES}"
        )
    matches = list(LIFETIME_RE.finditer(str(config_text)))
    if len(matches) != 1:
        raise ValueError(
            f"expected exactly one server_lifetime setting, found {len(matches)}"
        )
    previous = int(matches[0].group(1))
    updated = LIFETIME_RE.sub(f"server_lifetime = {candidate}", str(config_text))
    return updated, previous


def _percentile(values, percentile):
    ordered = sorted(float(item) for item in values)
    if not ordered:
        return 0.0
    if len(ordered) == 1:
        return round(ordered[0], 3)
    rank = (len(ordered) - 1) * float(percentile)
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = rank - lower
    return round(
        ordered[lower] + ((ordered[upper] - ordered[lower]) * fraction),
        3,
    )


def _metric_summary(values):
    numeric = [float(item) for item in values]
    if not numeric:
        return {"count": 0, "p50": 0.0, "p95": 0.0, "max": 0.0}
    return {
        "count": len(numeric),
        "p50": _percentile(numeric, 0.50),
        "p95": _percentile(numeric, 0.95),
        "max": round(max(numeric), 3),
    }


def summarize_pgbouncer_log(log_text, *, started_at, ended_at):
    connections = 0
    lifetime_closes = 0
    failures = {name: 0 for name in FAILURE_MARKERS}
    sources = {}
    wait_ms = []
    query_ms = []
    for line in str(log_text or "").splitlines():
        match = TIMESTAMP_RE.match(line)
        if not match:
            continue
        try:
            timestamp = datetime.fromisoformat(match.group("timestamp"))
        except ValueError:
            continue
        if timestamp < started_at or timestamp > ended_at:
            continue
        lowered = line.lower()
        if "new connection to server" in lowered:
            connections += 1
            source_match = SERVER_SOURCE_RE.search(line)
            if source_match:
                source = source_match.group("source")
                sources[source] = sources.get(source, 0) + 1
        if "closing because: server lifetime over" in lowered:
            lifetime_closes += 1
        for name, marker in FAILURE_MARKERS.items():
            if marker in lowered:
                failures[name] += 1
        stats_match = STATS_RE.search(line)
        if stats_match:
            query_ms.append(float(stats_match.group("query")) / 1000.0)
            wait_ms.append(float(stats_match.group("wait")) / 1000.0)

    window_seconds = max(0.001, (ended_at - started_at).total_seconds())
    return {
        "window_seconds": round(window_seconds, 3),
        "new_server_connections": connections,
        "connections_per_hour": round(connections * 3600.0 / window_seconds, 3),
        "server_lifetime_closes": lifetime_closes,
        "failures": failures,
        "server_sources": sources,
        "wait_ms": _metric_summary(wait_ms),
        "query_ms": _metric_summary(query_ms),
    }


def select_candidate(results):
    qualified = []
    for raw in results or ():
        result = dict(raw or {})
        baseline = float(result.get("baseline_connections_per_hour") or 0)
        connection_rate = float((result.get("log") or {}).get("connections_per_hour") or 0)
        if baseline <= 0:
            continue
        reduction = max(0.0, (baseline - connection_rate) / baseline)
        failures = (result.get("log") or {}).get("failures") or {}
        disqualifying_failures = sum(
            int(failures.get(name) or 0)
            for name in (
                "connect_timeout",
                "query_timeout",
                "query_wait_timeout",
                "server_conn_crashed",
                "tls_receive_error",
                "server_login_failed",
            )
        )
        success_rate = float((result.get("probe") or {}).get("success_rate") or 0)
        if reduction < 0.70 or disqualifying_failures or success_rate < 1.0:
            continue
        result["connection_reduction_ratio"] = round(reduction, 6)
        qualified.append(result)
    if not qualified:
        return None
    qualified.sort(
        key=lambda item: (
            float((item.get("probe") or {}).get("p95_ms") or float("inf")),
            float((item.get("log") or {}).get("connections_per_hour") or float("inf")),
        )
    )
    return qualified[0]


def _atomic_write(path, text):
    path = Path(path)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_text(text, encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def _read_pid(path):
    try:
        pid = int(Path(path).read_text(encoding="utf-8").strip())
    except (OSError, TypeError, ValueError) as exc:
        raise RuntimeError(f"invalid PgBouncer pid file: {path}") from exc
    try:
        os.kill(pid, 0)
    except OSError as exc:
        raise RuntimeError(f"PgBouncer process is not running: pid={pid}") from exc
    return pid


def _reload(pid):
    os.kill(int(pid), signal.SIGHUP)


def _database_url():
    for raw in (PROJECT_ROOT / ".env").read_text(
        encoding="utf-8", errors="replace"
    ).splitlines():
        if raw.startswith("DATABASE_URL="):
            value = raw.split("=", 1)[1].strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            return value.replace("postgresql+psycopg2://", "postgresql://")
    raise RuntimeError(".env does not contain DATABASE_URL")


def _probe_once(database_url):
    import psycopg2

    started = time.perf_counter()
    connection = psycopg2.connect(database_url, connect_timeout=10)
    try:
        connection.set_session(readonly=True, autocommit=False)
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL statement_timeout = '10s'")
            cursor.execute("SELECT 1")
            if cursor.fetchone()[0] != 1:
                raise RuntimeError("unexpected SELECT 1 result")
        connection.rollback()
    finally:
        connection.close()
    return (time.perf_counter() - started) * 1000.0


def _run_probes(database_url, duration_seconds, interval_seconds):
    latencies = []
    errors = []
    deadline = time.monotonic() + max(1, int(duration_seconds))
    while True:
        try:
            latencies.append(_probe_once(database_url))
        except Exception as exc:
            errors.append({"type": type(exc).__name__, "message": str(exc)[:1000]})
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(max(1, int(interval_seconds)), remaining))
    total = len(latencies) + len(errors)
    summary = _metric_summary(latencies)
    return {
        "attempts": total,
        "successes": len(latencies),
        "failures": len(errors),
        "success_rate": round(len(latencies) / total, 6) if total else 0.0,
        "p50_ms": summary["p50"],
        "p95_ms": summary["p95"],
        "max_ms": summary["max"],
        "errors": errors,
    }


def _log_delta(path, offset):
    path = Path(path)
    if not path.exists():
        return ""
    with path.open("rb") as handle:
        if path.stat().st_size >= offset:
            handle.seek(offset)
        return handle.read().decode("utf-8", errors="replace")


def _parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=int, choices=APPROVED_CANDIDATES, required=True)
    parser.add_argument("--duration-seconds", type=int, default=900)
    parser.add_argument("--interval-seconds", type=int, default=5)
    parser.add_argument("--baseline-connections-per-hour", type=float, default=72.0)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--pid-file", type=Path, default=DEFAULT_PID_FILE)
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--keep-config", action="store_true")
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    original = args.config.read_text(encoding="utf-8")
    candidate_config, previous = replace_server_lifetime(original, args.candidate)
    plan = {
        "candidate": args.candidate,
        "previous": previous,
        "duration_seconds": max(1, args.duration_seconds),
        "interval_seconds": max(1, args.interval_seconds),
        "config": str(args.config),
        "keep_config": bool(args.keep_config),
        "apply": bool(args.apply),
    }
    if not args.apply:
        print(json.dumps({"status": "dry_run", **plan}, ensure_ascii=False, indent=2))
        return 0

    pid = _read_pid(args.pid_file)
    database_url = _database_url()
    log_offset = args.log.stat().st_size if args.log.exists() else 0
    started_at = datetime.now()
    result = None
    config_changed = False
    try:
        _atomic_write(args.config, candidate_config)
        config_changed = True
        _reload(pid)
        time.sleep(1)
        probe = _run_probes(
            database_url,
            max(1, args.duration_seconds),
            max(1, args.interval_seconds),
        )
        ended_at = datetime.now()
        log_summary = summarize_pgbouncer_log(
            _log_delta(args.log, log_offset),
            started_at=started_at,
            ended_at=ended_at,
        )
        result = {
            "status": "completed",
            **plan,
            "started_at": started_at.isoformat(),
            "ended_at": ended_at.isoformat(),
            "baseline_connections_per_hour": args.baseline_connections_per_hour,
            "probe": probe,
            "log": log_summary,
        }
    finally:
        if config_changed and not args.keep_config:
            _atomic_write(args.config, original)
            _reload(pid)

    output = args.output
    if output is None:
        DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        output = DEFAULT_OUTPUT_DIR / (
            f"pgbouncer-lifetime-{args.candidate}-{started_at.strftime('%Y%m%d-%H%M%S')}.json"
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(output, json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({**result, "output": str(output)}, ensure_ascii=False, indent=2))
    return 0 if result["probe"]["success_rate"] == 1.0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
