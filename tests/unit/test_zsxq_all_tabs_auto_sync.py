import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services import zsxq_all_tabs_auto_sync_service as service  # noqa: E402
from models import SystemConfig, db  # noqa: E402
from services.zsxq_sync_service import zsxq_sync_service  # noqa: E402


class ZsxqAllTabsAutoSyncTest(unittest.TestCase):
    def setUp(self):
        service._invalidate_config_cache()

    def tearDown(self):
        service._invalidate_config_cache()

    def test_targets_are_fixed_to_three_hours_and_use_distinct_modes(self):
        self.assertEqual(service.DEFAULT_INTERVAL_MINUTES, 180)
        self.assertEqual(
            [target["group_id"] for target in service.AUTO_SYNC_TARGETS],
            ["1824528822", "88885882121542"],
        )
        self.assertEqual(
            [target["mode"] for target in service.AUTO_SYNC_TARGETS],
            ["auto_all_tabs_sc", "auto_all_tabs_hh"],
        )
        self.assertEqual(
            [target["topic_count"] for target in service.AUTO_SYNC_TARGETS],
            [40, 20],
        )
        self.assertFalse(service.AUTO_SYNC_TARGETS[0]["stop_on_existing"])
        self.assertTrue(service.AUTO_SYNC_TARGETS[1]["stop_on_existing"])
        self.assertTrue(service.AUTO_SYNC_TARGETS[0]["notify_milestones"])
        self.assertTrue(service.AUTO_SYNC_TARGETS[1]["notify_milestones"])

    def test_menu_conversion_matches_the_lab_all_tabs_behavior(self):
        tabs = service.tabs_from_menus([
            {
                "type": "hashtag",
                "title": "#风向标#",
                "hashtag": {"hashtag_id": 123},
            },
            {"title": "最新", "preset_type": "all"},
            {"title": "精华", "preset_type": "digests"},
            {
                "type": "hashtag",
                "title": "#重复#",
                "hashtag": {"hashtag_id": 123},
            },
            {"title": "无法识别"},
        ])

        self.assertEqual(tabs, [
            {
                "id": "hashtag:123",
                "label": "风向标",
                "scope": "all",
                "hashtagId": "123",
            },
            {"id": "preset:all", "label": "最新", "scope": "all"},
            {"id": "preset:digests", "label": "精华", "scope": "digests"},
        ])

    def test_payload_never_downloads_attachments_and_refreshes_shengcai_comments(self):
        shengcai = service.AUTO_SYNC_TARGETS[0]
        payload = service.build_auto_sync_payload(
            shengcai,
            credential_id="credential-frontier",
            tabs=[{"id": "preset:all", "label": "最新", "scope": "all"}],
            trigger="scheduler",
            attempt_id="attempt-1",
        )

        self.assertEqual(payload["credential_id"], "credential-frontier")
        self.assertEqual(payload["count"], 40)
        self.assertEqual(payload["max_pages"], 2)
        self.assertFalse(payload["stop_on_existing"])
        self.assertFalse(payload["download_attachments"])
        self.assertFalse(payload["allow_attachment_downloads"])
        self.assertEqual(payload["mode"], "auto_all_tabs_sc")
        self.assertTrue(payload["auto_all_tabs"]["notify_milestones"])

    def test_active_run_skips_remote_menu_and_queue_submission(self):
        target = service.AUTO_SYNC_TARGETS[0]
        active = {
            "id": "run-active",
            "status": "running",
            "mode": "manual_all_tabs",
        }
        with patch.object(service, "_active_run_snapshot", return_value=active), patch.object(
            service.zsxq_service,
            "get_menus",
        ) as get_menus:
            result = service._queue_target(
                target,
                cookie="zsxq_access_token=token",
                credential_id="credential-frontier",
                trigger="scheduler",
                attempt_id="attempt-1",
            )

        self.assertTrue(result["skipped"])
        self.assertEqual(result["reason"], "active_run_exists")
        get_menus.assert_not_called()

    def test_menu_discovery_retries_transient_1059_before_failing_round(self):
        target = service.AUTO_SYNC_TARGETS[0]
        transient = {
            "succeeded": False,
            "code": 1059,
            "error": "内部错误",
        }
        recovered = {
            "succeeded": True,
            "resp_data": {"menus": [{"title": "最新", "preset_type": "all"}]},
        }
        with patch.object(
            service.zsxq_service,
            "get_menus",
            side_effect=[transient, recovered],
        ) as get_menus, patch.object(
            service.zsxq_sync_service,
            "_sync_retry_attempts",
            return_value=3,
        ), patch.object(
            service.zsxq_sync_service,
            "_sync_retry_delay_seconds",
            return_value=0.1,
        ), patch.object(service.time, "sleep") as sleep:
            response = service._get_menus_with_retry("cookie", target)

        self.assertTrue(response["succeeded"])
        self.assertEqual(get_menus.call_count, 2)
        sleep.assert_called_once_with(0.1)

    def test_queue_target_uses_existing_sync_task_and_business_run(self):
        target = service.AUTO_SYNC_TARGETS[1]
        menus_response = {
            "succeeded": True,
            "resp_data": {
                "menus": [{
                    "title": "#宏观#",
                    "hashtag": {"hashtag_id": "456"},
                }],
            },
        }
        async_result = SimpleNamespace(id="celery-1")
        with patch.object(service, "_active_run_snapshot", return_value=None), patch.object(
            service.zsxq_service,
            "get_menus",
            return_value=menus_response,
        ), patch.object(
            service.zsxq_sync_service,
            "_assert_upstream_success",
        ), patch.object(
            service.zsxq_sync_service,
            "create_sync_run",
            return_value={"id": "run-1"},
        ) as create_run, patch.object(
            service,
            "release_db_session_quietly",
        ), patch(
            "tasks.zsxq_sync.run_zsxq_sync.apply_async",
            return_value=async_result,
        ) as apply_async, patch.object(
            service,
            "_enqueue_all_tabs_notification",
        ) as notify:
            result = service._queue_target(
                target,
                cookie="zsxq_access_token=token",
                credential_id="credential-frontier",
                trigger="scheduler",
                attempt_id="attempt-1",
            )

        self.assertTrue(result["queued"])
        self.assertEqual(result["run_id"], "run-1")
        self.assertEqual(result["tabs_total"], 1)
        self.assertEqual(create_run.call_args.kwargs["mode"], "auto_all_tabs_hh")
        payload = apply_async.call_args.kwargs["args"][1]
        self.assertEqual(payload["count"], 20)
        self.assertTrue(payload["stop_on_existing"])
        self.assertFalse(payload["download_attachments"])
        self.assertEqual(apply_async.call_args.kwargs["queue"], "zsxq_sync")
        notify.assert_called_once()
        self.assertEqual("workflow_started", notify.call_args.args[0])
        self.assertEqual("run-1", notify.call_args.kwargs["run_id"])

    def test_queue_publish_failure_closes_the_business_run(self):
        target = service.AUTO_SYNC_TARGETS[0]
        menus_response = {
            "succeeded": True,
            "resp_data": {
                "menus": [{
                    "title": "最新",
                    "preset_type": "all",
                }],
            },
        }
        with patch.object(service, "_active_run_snapshot", return_value=None), patch.object(
            service.zsxq_service,
            "get_menus",
            return_value=menus_response,
        ), patch.object(
            service.zsxq_sync_service,
            "_assert_upstream_success",
        ), patch.object(
            service.zsxq_sync_service,
            "create_sync_run",
            return_value={"id": "run-failed-publish"},
        ), patch.object(
            service.zsxq_sync_service,
            "mark_run_failed",
        ) as mark_run_failed, patch.object(
            service,
            "release_db_session_quietly",
        ), patch(
            "tasks.zsxq_sync.run_zsxq_sync.apply_async",
            side_effect=RuntimeError("broker unavailable"),
        ):
            with self.assertRaisesRegex(RuntimeError, "broker unavailable"):
                service._queue_target(
                    target,
                    cookie="zsxq_access_token=token",
                    credential_id="credential-frontier",
                    trigger="scheduler",
                    attempt_id="attempt-1",
                )

        mark_run_failed.assert_called_once()
        self.assertEqual(mark_run_failed.call_args.args[0], "run-failed-publish")
        self.assertTrue(mark_run_failed.call_args.args[2]["queue_publish_failed"])

    def test_shared_credential_prefers_the_frontier_credential_id(self):
        with patch(
            "services.zsxq_system_sync_test.load_config",
            return_value={"credential_id": "frontier-credential"},
        ), patch(
            "services.zsxq_system_sync_test.runtime_cookie_info",
            return_value={
                "cookie": "zsxq_access_token=shared",
                "credential_id": "frontier-credential",
            },
        ) as runtime_cookie_info:
            credential = service._resolve_shared_sync_credential()

        self.assertEqual(credential["cookie"], "zsxq_access_token=shared")
        runtime_cookie_info.assert_called_once_with(
            account_id="frontier-credential",
            required=True,
        )

    def test_round_reuses_one_shared_credential_for_both_targets(self):
        lock = object()
        configs = [
            service._normalize_stored_config(
                target["key"],
                service._default_config_payload(
                    target,
                    credential_id="credential-frontier",
                ),
                enabled=True,
            )
            for target in service.DEFAULT_AUTO_SYNC_TARGETS
        ]
        with patch.object(
            service,
            "load_all_tabs_auto_sync_configs",
            return_value=configs,
        ), patch.object(service, "_try_acquire_lock", return_value=lock), patch.object(
            service,
            "_release_lock",
        ), patch.object(
            service,
            "_resolve_shared_sync_credential",
            return_value={
                "cookie": "zsxq_access_token=shared",
                "credential_id": "credential-frontier",
            },
        ), patch.object(
            service,
            "release_db_session_quietly",
        ), patch.object(
            service,
            "_queue_target",
            side_effect=[
                {"key": "shengcai", "queued": True, "skipped": False},
                {"key": "honghao", "queued": True, "skipped": False},
            ],
        ) as queue_target, patch.object(service, "_write_state"):
            result = service.queue_all_tabs_auto_sync_targets(trigger="scheduler")

        self.assertTrue(result["queued"])
        self.assertEqual(queue_target.call_count, 2)
        for call in queue_target.call_args_list:
            self.assertEqual(call.kwargs["cookie"], "zsxq_access_token=shared")
            self.assertEqual(call.kwargs["credential_id"], "credential-frontier")


class ZsxqAllTabsAutoSyncDatabaseConfigTest(unittest.TestCase):
    def setUp(self):
        service._invalidate_config_cache()
        self.app = Flask(__name__)
        self.app.config.update(
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(self.app)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

    def tearDown(self):
        service._invalidate_config_cache()
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_defaults_are_one_row_per_group_in_shared_system_config_table(self):
        with patch.object(
            service,
            "_frontier_preferred_credential_id",
            return_value="credential-frontier",
        ):
            configs = service.load_all_tabs_auto_sync_configs()

        rows = SystemConfig.query.filter_by(
            type=service.CONFIG_TYPE,
            scope=service.CONFIG_SCOPE,
        ).order_by(SystemConfig.key).all()
        self.assertEqual(len(rows), 2)
        self.assertEqual(
            {row.key for row in rows},
            {
                "zsxq.sync.schedule.shengcai",
                "zsxq.sync.schedule.honghao",
            },
        )
        self.assertEqual(
            {config["credential_id"] for config in configs},
            {"credential-frontier"},
        )
        by_key = {config["key"]: config for config in configs}
        self.assertEqual(by_key["shengcai"]["topic_count"], 40)
        self.assertEqual(by_key["shengcai"]["max_pages"], 2)
        self.assertEqual(by_key["honghao"]["topic_count"], 20)

    def test_reuses_normalized_database_configs_for_one_minute(self):
        row = SimpleNamespace(
            config_key="zsxq.sync.schedule.shengcai",
            value={"group_id": "1824528822", "group_name": "生财有术"},
            status=1,
            updated_at=None,
        )
        with patch.object(service, "_db_config_rows", return_value=[row]) as rows, patch.object(
            service.time,
            "monotonic",
            side_effect=[100.0, 100.0, 120.0],
        ):
            first = service.load_all_tabs_auto_sync_configs(ensure_defaults=False)
            first[0]["group_name"] = "mutated"
            second = service.load_all_tabs_auto_sync_configs(ensure_defaults=False)

        self.assertEqual(rows.call_count, 1)
        self.assertEqual(second[0]["group_name"], "生财有术")

    def test_updating_one_group_does_not_change_the_other_group(self):
        with patch.object(
            service,
            "_frontier_preferred_credential_id",
            return_value="credential-frontier",
        ):
            service.ensure_default_auto_sync_configs()
            updated = service.save_all_tabs_auto_sync_config(
                "shengcai",
                {
                    "topic_count": 45,
                    "interval_minutes": 120,
                },
            )
            configs = service.load_all_tabs_auto_sync_configs()

        self.assertEqual(updated["topic_count"], 45)
        self.assertEqual(updated["interval_minutes"], 120)
        by_key = {config["key"]: config for config in configs}
        self.assertEqual(by_key["honghao"]["topic_count"], 20)
        self.assertEqual(by_key["honghao"]["interval_minutes"], 180)

    def test_notification_preference_is_persisted_per_group(self):
        with patch.object(
            service,
            "_frontier_preferred_credential_id",
            return_value="credential-frontier",
        ):
            service.ensure_default_auto_sync_configs()
            updated = service.save_all_tabs_auto_sync_config(
                "honghao",
                {"notify_milestones": False},
            )
            configs = service.load_all_tabs_auto_sync_configs()

        self.assertFalse(updated["notify_milestones"])
        by_key = {config["key"]: config for config in configs}
        self.assertFalse(by_key["honghao"]["notify_milestones"])
        self.assertTrue(by_key["shengcai"]["notify_milestones"])

    def test_disabled_group_is_excluded_from_runtime_schedule(self):
        with patch.object(
            service,
            "_frontier_preferred_credential_id",
            return_value="credential-frontier",
        ):
            service.ensure_default_auto_sync_configs()
            service.save_all_tabs_auto_sync_config(
                "honghao",
                {"enabled": False},
            )
            enabled = service.load_all_tabs_auto_sync_configs(
                include_disabled=False,
            )

        self.assertEqual([config["key"] for config in enabled], ["shengcai"])

    def test_target_of_40_uses_api_safe_pages_of_20(self):
        with patch.object(
            zsxq_sync_service,
            "_get_topics_with_retry",
            return_value={"resp_data": {"topics": []}},
        ) as get_topics, patch.object(
            zsxq_sync_service,
            "_upsert_cursor",
        ):
            result = zsxq_sync_service._sync_one_tab(
                cookie="cookie",
                group_id="1824528822",
                group_name="生财有术",
                tab={"id": "preset:all", "label": "最新", "scope": "all"},
                run=SimpleNamespace(id="run-1"),
                count=40,
                max_pages=2,
                stop_on_existing=False,
                download_attachments=False,
                download_file_types={"all"},
            )

        self.assertEqual(get_topics.call_args.kwargs["count"], 20)
        self.assertEqual(result["target_topics"], 40)
        self.assertEqual(result["planned_pages"], 2)


if __name__ == "__main__":
    unittest.main()
