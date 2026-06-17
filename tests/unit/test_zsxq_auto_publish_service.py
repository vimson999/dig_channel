import sys
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


if __name__ == "__main__":
    unittest.main()
