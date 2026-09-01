import importlib.util
import unittest
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = (
    PROJECT_ROOT / "scripts" / "diagnostics" / "benchmark_pgbouncer_lifetime.py"
)


def load_module():
    spec = importlib.util.spec_from_file_location("pgbouncer_lifetime_benchmark", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PgBouncerLifetimeBenchmarkTest(unittest.TestCase):
    def test_replaces_exactly_one_server_lifetime_with_approved_candidate(self):
        benchmark = load_module()
        original = "pool_mode = transaction\nserver_lifetime = 300\nquery_timeout = 20\n"

        updated, previous = benchmark.replace_server_lifetime(original, 1800)

        self.assertEqual(300, previous)
        self.assertIn("server_lifetime = 1800", updated)
        with self.assertRaises(ValueError):
            benchmark.replace_server_lifetime(original, 301)

    def test_summarizes_new_connections_churn_and_failures_for_window(self):
        benchmark = load_module()
        log = "\n".join(
            [
                "2026-08-29 10:00:00.000 CST [1] LOG S-1: new connection to server (from 100.96.3.12:1)",
                "2026-08-29 10:30:00.000 CST [1] LOG S-2: new connection to server (from 100.96.3.12:2)",
                "2026-08-29 10:31:00.000 CST [1] LOG S-3: closing because: connect timeout (age=10s)",
                "2026-08-29 10:32:00.000 CST [1] LOG stats: 1 xacts/s, 2 queries/s, in 0 B/s, out 0 B/s, xact 1000 us, query 2000 us, wait 3000 us",
            ]
        )

        summary = benchmark.summarize_pgbouncer_log(
            log,
            started_at=datetime(2026, 8, 29, 10, 0, 0),
            ended_at=datetime(2026, 8, 29, 11, 0, 0),
        )

        self.assertEqual(2, summary["new_server_connections"])
        self.assertEqual(2.0, summary["connections_per_hour"])
        self.assertEqual(1, summary["failures"]["connect_timeout"])
        self.assertEqual(3.0, summary["wait_ms"]["p95"])

    def test_selection_requires_seventy_percent_churn_reduction_and_no_timeouts(self):
        benchmark = load_module()
        results = [
            {
                "candidate": 900,
                "baseline_connections_per_hour": 72.0,
                "log": {
                    "connections_per_hour": 24.0,
                    "failures": {"connect_timeout": 0, "query_timeout": 0},
                },
                "probe": {"success_rate": 1.0, "p95_ms": 220.0},
            },
            {
                "candidate": 1800,
                "baseline_connections_per_hour": 72.0,
                "log": {
                    "connections_per_hour": 12.0,
                    "failures": {"connect_timeout": 0, "query_timeout": 0},
                },
                "probe": {"success_rate": 1.0, "p95_ms": 240.0},
            },
        ]

        selected = benchmark.select_candidate(results)

        self.assertEqual(1800, selected["candidate"])
        self.assertGreaterEqual(selected["connection_reduction_ratio"], 0.70)

        results[1]["log"]["failures"]["connect_timeout"] = 1
        self.assertIsNone(benchmark.select_candidate(results))

    def test_cli_is_dry_run_unless_apply_is_explicit(self):
        source = SCRIPT_PATH.read_text(encoding="utf-8")

        self.assertIn('action="store_true"', source)
        self.assertIn('if not args.apply:', source)
        self.assertIn("--keep-config", source)


if __name__ == "__main__":
    unittest.main()
