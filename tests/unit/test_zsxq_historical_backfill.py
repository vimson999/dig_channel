import inspect
import unittest
from datetime import datetime
from unittest.mock import patch

from scripts.maintenance import zsxq_historical_backfill as historical_backfill
from scripts.maintenance.zsxq_historical_backfill import (
    classify_topic_window,
    format_zsxq_time,
    parse_topic_time,
    seek_cursor_candidates,
)
from apis.v1 import zsxq as zsxq_api


class ZsxqHistoricalBackfillTest(unittest.TestCase):
    def setUp(self):
        self.start = parse_topic_time("2024-01-01T00:00:00+08:00")
        self.end = parse_topic_time("2024-08-11T21:45:37.743+08:00")

    def test_parses_zsxq_timezone_without_colon(self):
        parsed = parse_topic_time("2024-08-11T21:45:37.743+0800")
        self.assertEqual(parsed.utcoffset().total_seconds(), 8 * 60 * 60)

    def test_formats_timestamp_for_zsxq_without_timezone_colon(self):
        self.assertEqual(format_zsxq_time(self.end), "2024-08-11T21:45:37.743+0800")

    def test_classifies_target_window_boundaries(self):
        self.assertEqual(classify_topic_window("2024-08-12T00:00:00+0800", self.start, self.end), "newer")
        self.assertEqual(classify_topic_window("2024-06-01T00:00:00+0800", self.start, self.end), "target")
        self.assertEqual(classify_topic_window("2023-12-31T23:59:59+0800", self.start, self.end), "older")
        self.assertEqual(classify_topic_window("not-a-time", self.start, self.end), "invalid")

    def test_seek_candidates_start_near_target_and_fall_back_to_current_cursor(self):
        candidates = seek_cursor_candidates("2025-11-02T00:03:42.951+0800", self.end)

        self.assertEqual(parse_topic_time(candidates[0]), self.end)
        self.assertTrue(candidates[0].endswith("+0800"))
        self.assertEqual(candidates[-1], "2025-11-02T00:03:42.951+0800")

    def test_in_window_cursor_is_the_only_resume_candidate(self):
        candidates = seek_cursor_candidates("2024-06-01T00:00:00+0800", self.end)

        self.assertEqual(candidates, ["2024-06-01T00:00:00+0800"])

    def test_auto_backfill_skips_sync_credential_without_group_access(self):
        candidates = [
            {"credential_id": "wrong", "credential_name": "发布账号", "cookie": "wrong-cookie"},
            {"credential_id": "right", "credential_name": "同步账号", "cookie": "right-cookie"},
        ]
        rejected = {"succeeded": False, "code": 1005, "resp_data": {"message": "登录的用户不是星球成员"}}

        with patch.object(
            historical_backfill,
            "resolve_zsxq_cookie_accounts",
            return_value=candidates,
        ) as resolve_accounts, patch.object(
            historical_backfill.zsxq_service,
            "get_menus",
            side_effect=[rejected, {"succeeded": True, "resp_data": {}}],
        ):
            selected = historical_backfill.resolve_group_sync_credential("1824528822")

        self.assertEqual(selected["credential_id"], "right")
        resolve_accounts.assert_called_once_with(
            purpose="zsxq.sync",
            group_id="1824528822",
            account_id=None,
            required=True,
        )

    def test_db_preferred_sync_never_falls_back_to_inline_cookie(self):
        payload = {"credential_id": "missing", "cookie": "zsxq_access_token=inline"}

        with patch(
            "services.platform_credential_service.resolve_zsxq_cookie",
            side_effect=RuntimeError("DB credential unavailable"),
        ):
            with self.assertRaisesRegex(RuntimeError, "DB credential unavailable"):
                zsxq_api._resolve_request_cookie(payload, purpose="zsxq.sync", prefer_db=True)

    def test_backfill_never_reuses_expired_run_after_commit(self):
        source = inspect.getsource(historical_backfill.HistoricalBackfillRunner.run)
        after_create_commit = source.split("db.session.commit()", 1)[1]

        self.assertIn("run_id = str(run.id)", source)
        self.assertIn("run_ref = SimpleNamespace(id=run_id)", source)
        self.assertNotIn("run.id", after_create_commit)
        self.assertIn("ZsxqSyncRun.query.filter(ZsxqSyncRun.id == run_id).update", source)

    def test_backfill_uses_page_snapshot_instead_of_full_topic_point_queries(self):
        run_source = inspect.getsource(historical_backfill.HistoricalBackfillRunner.run)
        upsert_source = inspect.getsource(historical_backfill.HistoricalBackfillRunner.upsert_topic)

        self.assertIn("_load_existing_topic_snapshots", run_source)
        self.assertIn("existing_topics", upsert_source)
        self.assertIn("previous=previous", upsert_source)
        self.assertNotIn("ZsxqTopic.query", upsert_source)
        self.assertNotIn("raw_payload", upsert_source)


if __name__ == "__main__":
    unittest.main()
