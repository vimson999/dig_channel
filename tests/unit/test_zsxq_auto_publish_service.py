import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services import zsxq_auto_publish_service as auto_publish  # noqa: E402


class ZsxqAutoPublishServiceTest(unittest.TestCase):
    def test_topic_file_count_counts_direct_topic_files(self):
        self.assertEqual(
            auto_publish._topic_file_count(
                {
                    "topic_id": "topic-1",
                    "talk": {
                        "files": [
                            {"file_id": "file-1", "name": "a.pdf"},
                            {"file_id": "file-2", "name": "b.pdf"},
                        ]
                    },
                }
            ),
            2,
        )

    def test_verify_published_topic_accepts_visible_topic_with_expected_files(self):
        with patch.object(
            auto_publish.zsxq_service,
            "get_topic",
            return_value={
                "succeeded": True,
                "resp_data": {
                    "topic": {
                        "topic_id": "topic-1",
                        "talk": {
                            "files": [
                                {"file_id": "file-1", "name": "a.pdf"},
                                {"file_id": "file-2", "name": "b.pdf"},
                            ]
                        },
                    }
                },
            },
        ):
            result = auto_publish._verify_published_topic(
                "cookie",
                "topic-1",
                expected_file_count=2,
                run_id="run-1",
                rule={"id": "rule-1", "publish_verify_attempts": 1},
                cookie_account={"id": "account-1"},
            )

        self.assertEqual(result["topic_id"], "topic-1")
        self.assertEqual(result["status"], "verified")
        self.assertTrue(result["confirmed"])
        self.assertEqual(result["file_count"], 2)

    def test_verify_published_topic_marks_invisible_topic_pending(self):
        with patch.object(
            auto_publish.zsxq_service,
            "get_topic",
            return_value={"succeeded": False, "code": 1007, "error": "主题不存在或已被删除"},
        ) as get_topic:
            with patch.object(auto_publish, "_append_publish_event") as append_event:
                result = auto_publish._verify_published_topic(
                    "cookie",
                    "missing-topic",
                    expected_file_count=8,
                    run_id="run-1",
                    rule={"id": "rule-1", "publish_verify_attempts": 1},
                    cookie_account={"id": "account-1", "name": "小青"},
                )

        get_topic.assert_called_once_with("cookie", "missing-topic")
        self.assertEqual(result["status"], "pending")
        self.assertFalse(result["confirmed"])
        self.assertEqual(result["expected_file_count"], 8)
        append_event.assert_called_once()
        self.assertEqual(append_event.call_args.args[0], "publish_verify_pending")

    def test_upload_auth_failure_rotates_to_next_cookie(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            audio = Path(temp_dir) / "a.mp3"
            audio.write_bytes(b"audio")
            rule = {
                "id": "audio",
                "file_type": "audio",
                "enabled": True,
                "source_dir": temp_dir,
                "extensions": [".mp3"],
                "batch_size": 1,
                "upload_retry_count": 0,
                "publish_verify_attempts": 1,
                "publish_verify_delay_seconds": 0,
            }
            config = {"group_id": "group-1"}
            accounts = [
                {"id": "cookie-1", "name": "失效账号", "cookie": "bad"},
                {"id": "cookie-2", "name": "可用账号", "cookie": "good"},
            ]

            with patch.object(auto_publish, "_append_publish_event"):
                with patch.object(auto_publish.zsxq_service, "normalize_cookie", side_effect=lambda value: value):
                    with patch.object(
                        auto_publish.zsxq_service,
                        "upload_topic_asset",
                        side_effect=[Exception("401 Client Error: Unauthorized"), {"id": "file-1"}],
                    ) as upload:
                        with patch.object(
                            auto_publish.zsxq_service,
                            "publish_topic",
                            return_value={"succeeded": True, "resp_data": {"topic": {"topic_id": "topic-1"}}},
                        ):
                            with patch.object(
                                auto_publish.zsxq_service,
                                "get_topic",
                                return_value={
                                    "succeeded": True,
                                    "resp_data": {
                                        "topic": {
                                            "topic_id": "topic-1",
                                            "talk": {"files": [{"file_id": "file-1", "name": "a.mp3"}]},
                                        }
                                    },
                                },
                            ):
                                result = auto_publish._run_rule_batch(rule, config, accounts, {}, "run-1")

        self.assertEqual(result["status"], "success")
        self.assertEqual(upload.call_count, 2)
        self.assertEqual(result["uploaded_count"], 1)
        self.assertEqual(result["verified_count"], 1)
        self.assertEqual(result["cookie_attempts"][0]["status"], "retry_with_next_cookie")
        self.assertEqual(result["cookie_attempts"][0]["auth_hint"], "认证失效")
        self.assertEqual(result["cookie_attempts"][1]["status"], "success")

    def test_db_credentials_do_not_fall_back_to_legacy_pool_when_unavailable(self):
        config = {
            "group_id": "group-1",
            "cookie_accounts": [
                {"id": "legacy", "name": "旧账号", "cookie": "expired", "enabled": True},
            ],
        }

        with patch.object(auto_publish, "has_app_context", return_value=True):
            with patch.object(
                auto_publish,
                "_db_publish_cookie_accounts",
                side_effect=ValueError("没有可用于当前星球的知识星球发帖凭据"),
            ):
                with self.assertRaisesRegex(ValueError, "没有可用于当前星球"):
                    auto_publish._resolve_runtime_cookie_accounts(config, required=True)

    def test_legacy_pool_is_disabled_even_when_db_has_no_accounts(self):
        config = {
            "group_id": "group-1",
            "cookie_accounts": [
                {"id": "legacy", "name": "旧账号", "cookie": "cookie", "enabled": True},
            ],
        }

        with patch.object(auto_publish, "has_app_context", return_value=True):
            with patch.object(auto_publish, "_db_publish_cookie_accounts", return_value=[]):
                with self.assertRaisesRegex(ValueError, "数据库中没有可用于当前星球"):
                    auto_publish._resolve_runtime_cookie_accounts(config, required=True)


if __name__ == "__main__":
    unittest.main()
