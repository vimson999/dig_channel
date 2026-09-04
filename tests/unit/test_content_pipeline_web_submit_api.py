import io
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask
from werkzeug.datastructures import FileStorage


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from apis.v1 import content_pipeline as content_pipeline_api  # noqa: E402
from models import db  # noqa: E402
from models.task import PublicationTask  # noqa: E402


class ContentPipelineWebSubmitApiTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.upload_root = Path(self.temp_dir.name) / "web_uploads"
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            CONTENT_PIPELINE_WEB_UPLOAD_ROOT=str(self.upload_root),
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(self.app)
        self.app.register_blueprint(content_pipeline_api.bp)
        self.client = self.app.test_client()
        self.auth_patch = patch(
            "utils.auth_utils.get_current_user",
            return_value=SimpleNamespace(id=42, is_admin=True),
        )
        self.auth_patch.start()

    def tearDown(self):
        self.auth_patch.stop()
        self.temp_dir.cleanup()

    @staticmethod
    def _task(kind):
        return {
            "id": f"{kind}-task-1",
            "celery_task_id": f"celery-{kind}-1",
            "status": "pending",
            "current_step": "init",
            "progress": 0,
            "result": {
                "dedupe": {
                    "received_count": 1,
                    "unique_count": 1,
                    "duplicate_count": 0,
                }
            },
        }

    def test_mixed_web_upload_stages_files_and_enqueues_the_existing_pipelines(self):
        with patch.object(
            content_pipeline_api,
            "enqueue_pdf_clean_task",
            return_value=self._task("pdf"),
        ) as submit_pdf, patch.object(
            content_pipeline_api,
            "enqueue_audio_clean_task",
            return_value=self._task("mp3"),
        ) as submit_audio:
            response = self.client.post(
                "/api/v1/content-pipeline/web-submit",
                data={
                    "input_mode": "upload",
                    "files": [
                        (io.BytesIO(b"pdf bytes"), "中文研报.pdf"),
                        (io.BytesIO(b"audio bytes"), "会议纪要.mp3"),
                    ],
                },
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 202)
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["data"]["source_mode"], "web_upload")
        self.assertEqual(payload["data"]["accepted_count"], 2)
        self.assertEqual(set(payload["data"]["tasks"]), {"pdf", "mp3"})

        self.assertEqual(submit_pdf.call_count, 1)
        self.assertEqual(submit_audio.call_count, 1)
        pdf_kwargs = submit_pdf.call_args.kwargs
        audio_kwargs = submit_audio.call_args.kwargs
        self.assertEqual(pdf_kwargs["trigger"], "web_upload")
        self.assertEqual(audio_kwargs["trigger"], "web_upload")
        self.assertEqual(pdf_kwargs["auto_publish"], {"enabled": True})
        self.assertEqual(audio_kwargs["auto_publish"], {"enabled": True})
        self.assertEqual(pdf_kwargs["metadata"]["source_mode"], "web_upload")
        self.assertEqual(audio_kwargs["metadata"]["source_mode"], "web_upload")
        self.assertEqual(pdf_kwargs["metadata"]["submission_channel"], "content_pipeline_web")
        self.assertEqual(audio_kwargs["metadata"]["submission_channel"], "content_pipeline_web")
        self.assertEqual(pdf_kwargs["user_id"], 42)
        self.assertEqual(audio_kwargs["user_id"], 42)
        self.assertTrue(Path(pdf_kwargs["files"][0]).is_file())
        self.assertTrue(Path(audio_kwargs["files"][0]).is_file())
        self.assertEqual(Path(pdf_kwargs["files"][0]).name, "中文研报.pdf")
        self.assertEqual(Path(audio_kwargs["files"][0]).name, "会议纪要.mp3")

    def test_upload_save_releases_auth_read_transaction(self):
        observed = []
        real_save = FileStorage.save

        def auth_read():
            db.session.query(PublicationTask.id).all()
            self.assertTrue(db.session().in_transaction())
            return SimpleNamespace(id=42, is_admin=True)

        def save(uploaded, *args, **kwargs):
            observed.append(db.session().in_transaction())
            return real_save(uploaded, *args, **kwargs)

        with self.app.app_context():
            db.create_all()
            try:
                with patch("utils.auth_utils.get_current_user", side_effect=auth_read), patch.object(
                    FileStorage, "save", save
                ), patch.object(content_pipeline_api, "enqueue_pdf_clean_task", return_value=self._task("pdf")):
                    response = self.client.post(
                        "/api/v1/content-pipeline/web-submit",
                        data={"files": [(io.BytesIO(b"pdf bytes"), "report.pdf")]},
                        content_type="multipart/form-data",
                    )
                self.assertEqual(response.status_code, 202)
                self.assertEqual(observed, [False])
            finally:
                db.session.remove()
                db.drop_all()

    def test_folder_submission_excludes_nested_files_unless_explicitly_enabled(self):
        with patch.object(
            content_pipeline_api,
            "enqueue_pdf_clean_task",
            return_value=self._task("pdf"),
        ) as submit_pdf, patch.object(
            content_pipeline_api,
            "enqueue_audio_clean_task",
            return_value=self._task("mp3"),
        ) as submit_audio:
            response = self.client.post(
                "/api/v1/content-pipeline/web-submit",
                data={
                    "input_mode": "folder",
                    "include_subdirectories": "false",
                    "relative_paths": ["根目录.pdf", "子目录/会议.mp3"],
                    "files": [
                        (io.BytesIO(b"pdf bytes"), "根目录.pdf"),
                        (io.BytesIO(b"audio bytes"), "会议.mp3"),
                    ],
                },
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 202)
        payload = response.get_json()
        self.assertEqual(payload["data"]["accepted_count"], 1)
        self.assertEqual(payload["data"]["skipped_count"], 1)
        self.assertEqual(payload["data"]["skipped"][0]["reason"], "nested_file_not_selected")
        self.assertEqual(submit_pdf.call_count, 1)
        self.assertEqual(submit_audio.call_count, 0)

    def test_folder_submission_accepts_a_root_file_when_browser_includes_the_selected_folder_name(self):
        with patch.object(
            content_pipeline_api,
            "enqueue_pdf_clean_task",
            return_value=self._task("pdf"),
        ) as submit_pdf:
            response = self.client.post(
                "/api/v1/content-pipeline/web-submit",
                data={
                    "input_mode": "folder",
                    "folder_root": "0821手动下载PDF",
                    "include_subdirectories": "false",
                    "relative_paths": ["0821手动下载PDF/根目录.pdf"],
                    "files": [(io.BytesIO(b"pdf bytes"), "根目录.pdf")],
                },
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 202)
        payload = response.get_json()
        self.assertEqual(payload["data"]["accepted_count"], 1)
        self.assertEqual(payload["data"]["accepted"][0]["relative_path"], "根目录.pdf")
        self.assertEqual(submit_pdf.call_count, 1)

    def test_invalid_file_type_is_rejected_without_creating_tasks(self):
        with patch.object(content_pipeline_api, "enqueue_pdf_clean_task") as submit_pdf, patch.object(
            content_pipeline_api,
            "enqueue_audio_clean_task",
        ) as submit_audio:
            response = self.client.post(
                "/api/v1/content-pipeline/web-submit",
                data={"input_mode": "upload", "files": [(io.BytesIO(b"zip"), "archive.zip")]},
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 400)
        payload = response.get_json()
        self.assertFalse(payload["success"])
        self.assertIn("MP3 或 PDF", payload["message"])
        self.assertEqual(submit_pdf.call_count, 0)
        self.assertEqual(submit_audio.call_count, 0)
        self.assertFalse(self.upload_root.exists())

    def test_failure_for_one_media_type_keeps_the_other_type_submitted(self):
        with patch.object(
            content_pipeline_api,
            "enqueue_pdf_clean_task",
            side_effect=RuntimeError("PDF 入队失败"),
        ), patch.object(
            content_pipeline_api,
            "enqueue_audio_clean_task",
            return_value=self._task("mp3"),
        ) as submit_audio:
            response = self.client.post(
                "/api/v1/content-pipeline/web-submit",
                data={
                    "input_mode": "upload",
                    "files": [
                        (io.BytesIO(b"pdf bytes"), "失败.pdf"),
                        (io.BytesIO(b"audio bytes"), "仍应投递.mp3"),
                    ],
                },
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 202)
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertEqual(set(payload["data"]["tasks"]), {"mp3"})
        self.assertEqual(payload["data"]["errors"][0]["kind"], "pdf")
        self.assertEqual(submit_audio.call_count, 1)


if __name__ == "__main__":
    unittest.main()
