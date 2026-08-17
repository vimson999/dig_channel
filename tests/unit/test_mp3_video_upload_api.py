import io
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from api import mp3_video_workflow as mp3_api  # noqa: E402
from models import db  # noqa: E402
from models.task import PublicationTask  # noqa: E402


class Mp3VideoUploadApiTest(unittest.TestCase):
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

    def test_uploaded_audio_uses_the_same_workflow_task_shape(self):
        saved_audio = {
            "attachment_id": "upload-id",
            "filename": "本地调研.mp3",
            "name": "本地调研.mp3",
            "path": "/tmp/local-mp3.mp3",
            "bytes": 1234,
            "file_ext": "mp3",
            "duration_seconds": 1800,
            "topic_title": "本地调研.mp3",
            "tab_label": "本地上传",
            "source": "local_upload",
        }

        def save_upload(_file_storage, upload_id):
            return {**saved_audio, "attachment_id": upload_id}

        with (
            self.app.test_request_context(
                "/api/mp3-video-workflows/jobs",
                method="POST",
                data={
                    "audio": (io.BytesIO(b"not-used-by-mocked-save"), "local.mp3"),
                    "input_source": "ds_response",
                    "ds_response_text": "1：标题\n2：副标题\n3：短标题\n4：00:00-10:00\n5：金句",
                    "render_mode": "full_video",
                    "generate_subtitles": "0",
                    "metadata": json.dumps({"title": "本地上传测试"}, ensure_ascii=False),
                },
                content_type="multipart/form-data",
            ),
            patch.object(mp3_api, "_save_audio_upload", side_effect=save_upload),
            patch.object(
                mp3_api,
                "apply_async_after_db_release",
                return_value=SimpleNamespace(id="celery-upload"),
            ),
        ):
            response = mp3_api.create_job.__wrapped__(SimpleNamespace(id="user-1"))

        payload = response.get_json()
        self.assertTrue(payload["success"])
        task = PublicationTask.query.filter_by(id=payload["job_id"]).one()
        workflow = task.params["mp3_video_workflow"]
        self.assertEqual(task.source_feature, "local_mp3_upload")
        self.assertEqual(workflow["audio"]["source"], "local_upload")
        self.assertEqual(workflow["audio"]["attachment_id"], task.source_task_id)
        self.assertEqual(workflow["audio"]["topic_title"], "本地上传测试")
        self.assertEqual(workflow["render_mode"], "full_video")
        self.assertFalse(workflow["generate_subtitles"])

    def test_upload_rejects_non_audio_extension_before_enqueue(self):
        with self.app.test_request_context(
            "/api/mp3-video-workflows/jobs",
            method="POST",
            data={"audio": (io.BytesIO(b"file"), "notes.txt")},
            content_type="multipart/form-data",
        ):
            response, status = mp3_api.create_job.__wrapped__(SimpleNamespace(id="user-1"))

        self.assertEqual(status, 400)
        self.assertIn("只支持音频文件", response.get_json()["message"])
        self.assertEqual(PublicationTask.query.count(), 0)


if __name__ == "__main__":
    unittest.main()
