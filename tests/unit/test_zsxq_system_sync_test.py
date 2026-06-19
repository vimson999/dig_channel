import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services import zsxq_system_sync_test as system_sync_test  # noqa: E402
from services.zsxq_sync_service import ZsxqTransientUpstreamError  # noqa: E402
from services.zsxq_workflow_service import ZsxqWorkflowService  # noqa: E402


class ZsxqSystemSyncTest(unittest.TestCase):
    def test_topic_pages_for_count_matches_observed_zsxq_page_size(self):
        self.assertEqual(system_sync_test._topic_pages_for_count(12), 1)
        self.assertEqual(system_sync_test._topic_pages_for_count(40), 2)
        self.assertEqual(system_sync_test._topic_pages_for_count(50), 3)

    def test_auto_video_dispatch_config_is_normalized(self):
        normalized = system_sync_test._normalize_auto_video_dispatch({
            "enabled": True,
            "pdf_min_download_count": "35",
            "mp3_min_download_count": "25",
            "wait_for_completion": True,
        })

        self.assertTrue(normalized["enabled"])
        self.assertEqual(normalized["pdf_min_download_count"], 35)
        self.assertEqual(normalized["mp3_min_download_count"], 25)
        self.assertFalse(normalized["wait_for_completion"])

    def test_workflow_snapshot_carries_auto_video_dispatch(self):
        service = ZsxqWorkflowService()
        snapshot = service._build_system_snapshot(
            config={
                "group_id": "28888112822211",
                "group_name": "前沿信息收录",
                "count": 40,
                "max_pages": 2,
                "download_attachments": True,
                "allow_attachment_downloads": True,
                "auto_video_dispatch": {
                    "enabled": True,
                    "pdf_min_download_count": 35,
                    "mp3_min_download_count": 25,
                },
            },
            trigger="scheduler_start",
            batch_id="20260619-150000",
            batch_round=1,
            max_rounds=None,
            max_download_files=40,
            jobs=[
                {
                    "key": "foreign_report",
                    "file_type": "pdf",
                    "tab": {"id": "tab-pdf", "label": "外资研报", "scope": "all"},
                },
                {
                    "key": "research_memo_mp3",
                    "file_type": "mp3",
                    "tab": {"id": "tab-mp3", "label": "调研纪要", "scope": "all"},
                },
            ],
        )

        self.assertEqual(snapshot["system_sync"]["auto_video_dispatch"]["pdf_min_download_count"], 35)
        self.assertEqual(snapshot["system_sync"]["auto_video_dispatch"]["mp3_min_download_count"], 25)
        self.assertTrue(snapshot["sync"]["auto_video_dispatch"]["enabled"])

    def test_runtime_cookie_override_counts_as_configured(self):
        with patch.dict(
            os.environ,
            {
                system_sync_test.COOKIE_ENV_NAME: "",
                system_sync_test.COOKIE_OVERRIDE_ENV_NAME: "zsxq_access_token=token",
            },
        ):
            self.assertTrue(system_sync_test._runtime_cookie_configured())
            self.assertEqual(system_sync_test._runtime_cookie_source(), "page")

    def test_validate_group_id_treats_1059_as_transient(self):
        with patch.object(
            system_sync_test.zsxq_service,
            "get_menus",
            return_value={
                "succeeded": False,
                "code": "1059",
                "resp_data": {"message": "内部错误"},
            },
        ):
            with self.assertRaises(ZsxqTransientUpstreamError):
                system_sync_test._validate_group_id("28888112822211", cookie="zsxq_access_token=token")

    def test_validate_group_id_with_retry_recovers_after_transient_error(self):
        responses = [
            {"succeeded": False, "code": "1059", "resp_data": {"message": "内部错误"}},
            {"succeeded": False, "code": "1059", "resp_data": {"message": "内部错误"}},
            {"succeeded": True, "resp_data": {"menus": []}},
        ]

        with patch.object(system_sync_test.zsxq_service, "get_menus", side_effect=responses) as get_menus:
            with patch.object(system_sync_test.time, "sleep") as sleep:
                system_sync_test._validate_group_id_with_retry(
                    "28888112822211",
                    cookie="zsxq_access_token=token",
                    max_retries=5,
                    delay_seconds=0,
                )

        self.assertEqual(get_menus.call_count, 3)
        self.assertEqual(sleep.call_count, 0)

    def test_validate_group_id_with_retry_exhausts_retries(self):
        with patch.object(
            system_sync_test.zsxq_service,
            "get_menus",
            return_value={
                "succeeded": False,
                "code": "1059",
                "resp_data": {"message": "内部错误"},
            },
        ) as get_menus:
            with patch.object(system_sync_test.time, "sleep") as sleep:
                with self.assertRaises(ZsxqTransientUpstreamError):
                    system_sync_test._validate_group_id_with_retry(
                        "28888112822211",
                        cookie="zsxq_access_token=token",
                        max_retries=2,
                        delay_seconds=0,
                    )

        self.assertEqual(get_menus.call_count, 3)
        self.assertEqual(sleep.call_count, 0)

    def test_invalid_group_skip_records_and_notifies(self):
        config = {
            "enabled": True,
            "group_id": "28888112822211",
            "group_name": "前沿信息收录",
            "interval_minutes": 240,
            "count": 40,
            "max_pages": 2,
            "max_download_files": 40,
            "rounds_completed": 4,
            "max_rounds": None,
            "jobs": [
                {
                    "key": "foreign_report_pdf",
                    "file_type": "pdf",
                    "mode_code": "fr",
                    "tab": {"id": "tab-1", "label": "外资研报", "scope": "all"},
                }
            ],
        }

        with patch.object(system_sync_test, "load_config", return_value=config):
            with patch.object(system_sync_test, "_pending_system_sync_work", return_value={"pending": False}):
                with patch.object(system_sync_test, "runtime_cookie", return_value="zsxq_access_token=token"):
                    with patch.object(
                        system_sync_test,
                        "_validate_group_id_with_retry",
                        side_effect=ValueError("知识星球返回失败 code=401：Unauthorized"),
                    ):
                        with patch.object(system_sync_test, "_write_config") as write_config:
                            with patch(
                                "services.workflow_notification_service.workflow_notification_service.send_email_message"
                            ) as send_email:
                                result = system_sync_test.queue_configured_jobs(
                                    trigger="scheduler",
                                    attempt_id="20260618-210105",
                                )

        self.assertTrue(result["skipped"])
        self.assertEqual(result["reason"], "invalid_group")
        self.assertEqual(config["last_skip_reason"], "invalid_group")
        self.assertIn("401", config["last_skip_error"])
        write_config.assert_called_once()
        send_email.assert_called_once()
        subject, body = send_email.call_args[0]
        self.assertIn("系统同步触发失败", subject)
        self.assertIn("401", body)
        self.assertIn("本轮未入队", body)
        self.assertIn("调度器已停止", body)


if __name__ == "__main__":
    unittest.main()
