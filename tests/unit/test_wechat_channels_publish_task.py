import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from models import db  # noqa: E402
from models.task import PublicationTask  # noqa: E402
from tasks import wechat_channels_publish  # noqa: E402


class WechatChannelsPublishTaskTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(SQLALCHEMY_DATABASE_URI="sqlite:///:memory:", SQLALCHEMY_TRACK_MODIFICATIONS=False)
        db.init_app(self.app)
        self.ctx = self.app.app_context(); self.ctx.push(); db.create_all()

    def tearDown(self):
        db.session.remove(); db.drop_all(); self.ctx.pop()

    def _create_task(self, submit=False):
        task = PublicationTask(
            status="pending", current_step="queued", progress=0,
            params={
                "feature": wechat_channels_publish.FEATURE_NAME, "user_id": "user-1",
                wechat_channels_publish.FEATURE_NAME: {
                    "credential_id": "credential-1", "video_path": "/tmp/test.mp4",
                    "original_filename": "test.mp4", "title": "这是测试标题",
                    "description": "测试描述", "tags": ["财经"], "submit": submit,
                    "visibility": "private",
                },
            }, result={"publish_status": "queued"},
        )
        db.session.add(task); db.session.commit(); return task.id

    def test_safe_test_is_prepared_and_cookie_is_never_persisted(self):
        job_id = self._create_task(False)
        def fake_publish(**kwargs):
            self.assertEqual("secret", kwargs["cookies"][0]["value"])
            self.assertFalse(kwargs["submit"])
            self.assertEqual("private", kwargs["visibility"])
            return {"publish_status": "prepared", "submitted": False, "visibility": "private", "message": "未点击发表"}
        with patch("app.create_app", return_value=self.app), patch.object(
            wechat_channels_publish, "guard_publication_task_start", return_value={"allowed": True}
        ), patch.object(
            wechat_channels_publish, "resolve_wechat_channels_cookies",
            return_value={"cookies":[{"name":"wxuin","value":"secret"}],"credential_id":"credential-1","credential_name":"主账号"},
        ), patch.object(
            wechat_channels_publish, "publish_wechat_channels_video", side_effect=fake_publish
        ), patch.object(wechat_channels_publish, "mark_credential_success"):
            wechat_channels_publish.process_job.run(job_id)
        task = db.session.get(PublicationTask, job_id)
        self.assertEqual("completed", task.status)
        self.assertEqual("prepared", task.current_step)
        self.assertNotIn("secret", str(task.params))
        self.assertNotIn("secret", str(task.result))

    def test_real_publish_is_submitted_not_visible(self):
        job_id = self._create_task(True)
        with patch("app.create_app", return_value=self.app), patch.object(
            wechat_channels_publish, "guard_publication_task_start", return_value={"allowed": True}
        ), patch.object(
            wechat_channels_publish, "resolve_wechat_channels_cookies",
            return_value={"cookies":[{"name":"wxuin","value":"secret"}],"credential_id":"credential-1","credential_name":"主账号"},
        ), patch.object(
            wechat_channels_publish, "publish_wechat_channels_video",
            return_value={"publish_status":"submitted","submitted":True,"visibility":"private","message":"已提交"},
        ), patch.object(wechat_channels_publish, "mark_credential_success"):
            wechat_channels_publish.process_job.run(job_id)
        task = db.session.get(PublicationTask, job_id)
        self.assertEqual("submitted", task.current_step)
        self.assertEqual("submitted", task.result["publish_status"])
        self.assertNotEqual("visible", task.result["publish_status"])


if __name__ == "__main__":
    unittest.main()
