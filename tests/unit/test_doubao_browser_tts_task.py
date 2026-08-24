import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from tasks import doubao_browser_tts as task_module  # noqa: E402


class DoubaoBrowserTTSTaskTest(unittest.TestCase):
    def task(self):
        return SimpleNamespace(
            id="child-1",
            params={
                "feature": "doubao_browser_tts",
                "parent_task_id": "parent-1",
                "doubao_browser_tts": {
                    "text": "需要朗读的文本",
                    "user_data_dir": "/runtime/browser_profiles/doubao-1",
                    "metadata": {
                        "parent_task_id": "parent-1",
                        "worker_id": "doubao-worker-1",
                        "chunk_index": 5,
                        "chunk_total": 5,
                        "chunk_attempt": 1,
                    },
                },
            },
            result={
                "captcha_blocked": {
                    "requires_human_action": True,
                    "message": "豆包要求完成图片验证",
                    "page_url": "https://www.doubao.com/chat/local_1",
                    "screenshot_path": "/tmp/doubao-captcha.png",
                }
            },
            error_message="豆包要求完成图片验证",
            current_step="blocked_by_captcha",
        )

    def test_captcha_context_contains_parent_workflow_link_and_recovery_steps(self):
        with patch.dict(
            "os.environ",
            {"REPORT_AUDIO_WORKFLOW_FRONTEND_BASE_URL": "http://localhost:5001/"},
            clear=False,
        ):
            context = task_module._captcha_human_intervention_context(
                self.task(),
                {"page_url": "https://www.doubao.com/chat/local_1"},
            )

        self.assertEqual(context["parent_task_id"], "parent-1")
        self.assertEqual(
            context["workflow_url"],
            "http://localhost:5001/report-audio-workflow-detail.html?job_id=parent-1",
        )
        self.assertEqual(context["profile"], "/runtime/browser_profiles/doubao-1")
        self.assertEqual(context["worker_id"], "doubao-worker-1")
        self.assertIn("从失败处续跑", context["resolution_steps"][-1])

    def test_captcha_email_persists_actionable_context_and_notification_result(self):
        task = self.task()
        fake_session = SimpleNamespace(commit=MagicMock())
        with patch.dict(
            "os.environ",
            {"REPORT_AUDIO_WORKFLOW_FRONTEND_BASE_URL": "http://localhost:5001"},
            clear=False,
        ), patch.object(task_module, "_load_task", return_value=task), patch.object(
            task_module,
            "db",
            SimpleNamespace(session=fake_session),
        ), patch.object(task_module, "flag_modified"), patch.object(
            task_module.workflow_notification_service,
            "send_browser_human_intervention",
            return_value={"sent": True, "to": "owner@example.test"},
        ) as send:
            sent = task_module._send_captcha_blocked_email(task.id)

        self.assertTrue(sent)
        captcha = task.result["captcha_blocked"]
        self.assertEqual(captcha["parent_task_id"], "parent-1")
        self.assertIn("job_id=parent-1", captcha["workflow_url"])
        self.assertEqual(captcha["profile"], "/runtime/browser_profiles/doubao-1")
        self.assertIn("从失败处续跑", captcha["resolution_steps"][-1])
        self.assertTrue(
            task.result["notifications"]["doubao_captcha_blocked"]["sent"]
        )
        kwargs = send.call_args.kwargs
        self.assertEqual(kwargs["details"]["工作流详情"], captcha["workflow_url"])
        self.assertEqual(kwargs["resolution_steps"], captcha["resolution_steps"])
        fake_session.commit.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
