import sys
import tempfile
import unittest
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, patch

from flask import Flask


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from models import db  # noqa: E402
from models.task import PublicationTask  # noqa: E402
from tasks import douyin_publish  # noqa: E402
from services.douyin_publish import DouyinSubmissionPendingVerification  # noqa: E402
from services.browser_publish_jobs import create_browser_publish_task  # noqa: E402
from services.douyin_publish_jobs import SPEC  # noqa: E402


class DouyinPublishTaskTest(unittest.TestCase):
    def setUp(self):
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
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _create_task(self, submit=False):
        task = PublicationTask(
            status="pending",
            current_step="queued",
            progress=0,
            params={
                "feature": douyin_publish.FEATURE_NAME,
                "user_id": "user-1",
                douyin_publish.FEATURE_NAME: {
                    "credential_id": "credential-1",
                    "video_path": "/tmp/test.mp4",
                    "original_filename": "test.mp4",
                    "title": "测试标题",
                    "description": "测试描述",
                    "tags": ["财经"],
                    "submit": submit,
                },
            },
            result={"publish_status": "queued"},
        )
        db.session.add(task)
        db.session.commit()
        return task.id

    def test_safe_test_completes_as_prepared_without_cookie_persistence(self):
        job_id = self._create_task(submit=False)

        def fake_publish(**kwargs):
            kwargs["progress"]("uploading_video", 35, "上传中")
            self.assertEqual("secret", kwargs["cookies"][0]["value"])
            self.assertEqual("credential-1", kwargs["credential_id"])
            self.assertFalse(kwargs["submit"])
            return {"publish_status": "prepared", "submitted": False, "message": "未点击发布"}

        with patch("app.create_app", return_value=self.app), patch.object(
            douyin_publish, "guard_publication_task_start", return_value={"allowed": True}
        ), patch.object(
            douyin_publish, "douyin_publish_execution_guard", return_value=nullcontext()
        ), patch.object(
            douyin_publish,
            "resolve_douyin_cookies",
            return_value={
                "cookies": [{"name": "sessionid", "value": "secret"}],
                "credential_id": "credential-1",
                "credential_name": "主账号",
            },
        ), patch.object(douyin_publish, "publish_douyin_video", side_effect=fake_publish), patch.object(
            douyin_publish, "mark_credential_success"
        ):
            douyin_publish.process_job.run(job_id)

        task = db.session.get(PublicationTask, job_id)
        self.assertEqual("completed", task.status)
        self.assertEqual("prepared", task.current_step)
        self.assertEqual("prepared", task.result["publish_status"])
        self.assertNotIn("secret", str(task.params))
        self.assertNotIn("secret", str(task.result))

    def test_same_douyin_credential_rejects_overlapping_active_task(self):
        self._create_task(submit=True)
        spec = replace(
            SPEC,
            resolve_credential=lambda **_kwargs: {
                "credential_id": "credential-1",
                "credential_name": "主账号",
            },
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            video = Path(temp_dir) / "video.mp4"
            video.write_bytes(b"video")
            with self.assertRaisesRegex(ValueError, "已有上传任务"):
                create_browser_publish_task(
                    spec,
                    current_user=SimpleNamespace(id="user-1"),
                    video_path=str(video),
                    title="测试标题",
                    credential_id="credential-1",
                )

    def test_real_publish_is_recorded_as_submitted_not_visible(self):
        job_id = self._create_task(submit=True)

        with patch("app.create_app", return_value=self.app), patch.object(
            douyin_publish, "guard_publication_task_start", return_value={"allowed": True}
        ), patch.object(
            douyin_publish, "douyin_publish_execution_guard", return_value=nullcontext()
        ), patch.object(
            douyin_publish,
            "resolve_douyin_cookies",
            return_value={
                "cookies": [{"name": "sessionid", "value": "secret"}],
                "credential_id": "credential-1",
                "credential_name": "主账号",
            },
        ), patch.object(
            douyin_publish,
            "publish_douyin_video",
            return_value={"publish_status": "submitted", "submitted": True, "message": "已提交"},
        ), patch.object(douyin_publish, "mark_credential_success"):
            douyin_publish.process_job.run(job_id)

        task = db.session.get(PublicationTask, job_id)
        self.assertEqual("completed", task.status)
        self.assertEqual("submitted", task.current_step)
        self.assertEqual("submitted", task.result["publish_status"])
        self.assertNotEqual("visible", task.result["publish_status"])

    def test_confirmed_publish_is_recorded_as_visible(self):
        job_id = self._create_task(submit=True)

        with patch("app.create_app", return_value=self.app), patch.object(
            douyin_publish, "guard_publication_task_start", return_value={"allowed": True}
        ), patch.object(
            douyin_publish, "douyin_publish_execution_guard", return_value=nullcontext()
        ), patch.object(
            douyin_publish,
            "resolve_douyin_cookies",
            return_value={
                "cookies": [{"name": "sessionid", "value": "secret"}],
                "credential_id": "credential-1",
                "credential_name": "主账号",
            },
        ), patch.object(
            douyin_publish,
            "publish_douyin_video",
            return_value={"publish_status": "visible", "submitted": True, "confirmed": True},
        ), patch.object(douyin_publish, "mark_credential_success"):
            douyin_publish.process_job.run(job_id)

        task = db.session.get(PublicationTask, job_id)
        self.assertEqual("completed", task.status)
        self.assertEqual("visible", task.current_step)
        self.assertEqual("visible", task.result["publish_status"])
        self.assertTrue(task.result["confirmed"])

    def test_login_loss_after_click_is_pending_and_not_retried(self):
        job_id = self._create_task(submit=True)

        with patch("app.create_app", return_value=self.app), patch.object(
            douyin_publish, "guard_publication_task_start", return_value={"allowed": True}
        ), patch.object(
            douyin_publish, "douyin_publish_execution_guard", return_value=nullcontext()
        ), patch.object(
            douyin_publish,
            "resolve_douyin_cookies",
            return_value={
                "cookies": [{"name": "sessionid", "value": "secret"}],
                "credential_id": "credential-1",
                "credential_name": "主账号",
            },
        ), patch.object(
            douyin_publish,
            "publish_douyin_video",
            side_effect=DouyinSubmissionPendingVerification(
                "已点击发布，等待人工确认", credential_expired=True
            ),
        ), patch.object(douyin_publish, "mark_credential_error") as mark_error:
            result = douyin_publish.process_job.run(job_id)

        task = db.session.get(PublicationTask, job_id)
        self.assertEqual("completed", task.status)
        self.assertEqual("submitted_pending_verification", task.current_step)
        self.assertTrue(task.result["submitted"])
        self.assertFalse(task.result["confirmed"])
        self.assertEqual("submitted_pending_verification", result["publish_status"])
        mark_error.assert_called_once_with(
            "credential-1", ANY, expired=True
        )


if __name__ == "__main__":
    unittest.main()
