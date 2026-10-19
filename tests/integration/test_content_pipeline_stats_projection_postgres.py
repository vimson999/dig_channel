"""Read-only PostgreSQL checks; no tables are created or changed."""

import json
import os
import sys
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "src" / "backend"))


@unittest.skipUnless(
    os.environ.get("RUN_READONLY_POSTGRES_TESTS") == "1",
    "requires opt-in read-only PostgreSQL connection",
)
class PublicationStatsProjectionPostgresTest(unittest.TestCase):
    def setUp(self):
        import psycopg2
        from dotenv import dotenv_values

        values = dotenv_values(PROJECT_ROOT / ".env")
        url = os.environ.get("STATS_TEST_DATABASE_URL") or values["DATABASE_URL"]
        self.connection = psycopg2.connect(
            url.replace("postgresql+psycopg2://", "postgresql://"),
            connect_timeout=5,
            application_name="stats_projection_readonly_test",
        )
        self.connection.set_session(readonly=True)
        with self.connection.cursor() as cursor:
            cursor.execute("SET LOCAL statement_timeout = '5s'")

    def tearDown(self):
        self.connection.rollback()
        self.connection.close()

    def _project(self, payload, *, include_details=False):
        from sqlalchemy import bindparam, cast, select
        from sqlalchemy.dialects import postgresql
        from services.content_pipeline.web_submission_stats import (
            publication_statistics_result_column,
        )

        fixture = select(
            cast(bindparam("payload"), postgresql.JSONB).label("result")
        ).subquery("publication_tasks")
        statement = select(
            publication_statistics_result_column("postgresql", include_details=include_details)
        ).select_from(fixture)
        compiled = statement.compile(dialect=postgresql.dialect())
        parameters = dict(compiled.params)
        parameters["payload"] = json.dumps(payload)
        with self.connection.cursor() as cursor:
            cursor.execute(str(compiled), parameters)
            return cursor.fetchone()[0]

    def test_projection_discards_audit_payload_but_keeps_file_statistics_fields(self):
        file = {
            "status": "uploaded",
            "name": "report.pdf",
            "filename": "fallback.pdf",
            "path": "/clean/report.pdf",
            "investment_bank": "高盛",
            "bank": "Goldman",
            "institution": "Apple",
            "institution_name": "Apple Inc",
        }
        rule = {
            "file_type": "pdf",
            "verified_count": 1,
            "verification": {"confirmed": True},
            "files": [file],
        }
        large_file = {**file, "upload_response": "x" * 50000}
        payload = {
            "preview": "x" * 50000,
            "publish_result": {
                "rounds": [{"rules": [{
                    **rule,
                    "verification": {"confirmed": True, "response": "x" * 50000},
                    "files": [large_file, "malformed-file"],
                }]}],
            },
        }

        projected = self._project(payload)

        self.assertEqual(projected, {"rules": [rule]})
        self.assertLess(len(json.dumps(projected).encode()), 1000)

    def test_publication_links_are_projected_only_for_details(self):
        rule = {
            "file_type": "pdf", "verified_count": 1, "topic_id": "12345678901234",
            "files": [{"status": "uploaded", "name": "高盛-report.pdf", "topic_id": "98765432101234"}],
        }
        payload = {"rounds": [{"rules": [rule]}]}
        overview = self._project(payload)["rules"][0]
        self.assertNotIn("topic_id", overview)
        self.assertNotIn("topic_id", overview["files"][0])
        detail = self._project(payload, include_details=True)["rules"][0]
        self.assertEqual(detail["topic_id"], "12345678901234")
        self.assertEqual(detail["files"][0]["topic_id"], "98765432101234")

    def test_projection_preserves_counts_for_legacy_and_malformed_payloads(self):
        from services.content_pipeline.web_submission_stats import (
            collect_verified_publication_files,
        )

        rule = {
            "file_type": "pdf",
            "verified_count": 1,
            "files": [{"status": "uploaded", "name": "高盛-Apple（AAPL.US）.pdf"}],
        }
        cases = [
            ({"publish_result": {"rounds": [{"rules": [rule]}]}}, 1),
            ({"rounds": [{"rules": [rule]}]}, 1),
            ({"publish_result": {"rules": [rule]}}, 1),
            ({"rules": [rule]}, 1),
            ({"rounds": "malformed", "rules": [rule]}, 1),
            ({"rounds": [], "rules": [rule]}, 0),
            ({"publish_result": [], "rules": [rule]}, 1),
            ({"publish_result": {}, "rules": [rule]}, 0),
            ({"rounds": [None, "bad", {"rules": [None, rule]}]}, 1),
            ({"rules": [{**rule, "files": "bad"}]}, 1),
            ({"rules": [{**rule, "verified_count": 0}]}, 0),
            ({"rules": [{**rule, "verified_count": 0,
                         "verification": {"confirmed": True}}]}, 1),
            ([], 0),
            (None, 0),
        ]
        for payload, expected_count in cases:
            with self.subTest(payload=payload):
                projected = self._project(payload)

                def collect(result):
                    task = SimpleNamespace(
                        source_task_id="clean-1",
                        completed_at=datetime(2026, 10, 14, 2),
                        result=result,
                    )
                    return collect_verified_publication_files([task], {"clean-1": "pdf"})

                rows = collect(projected)
                self.assertEqual(sum(row["count"] for row in rows), expected_count)
                self.assertEqual(rows, collect(payload))
