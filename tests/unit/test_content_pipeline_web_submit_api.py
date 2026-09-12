import io
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
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
        with self.app.app_context():
            db.create_all()
            try:
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

                history = self.client.get(
                    "/api/v1/content-pipeline/web-submissions"
                ).get_json()["data"]["items"]
                self.assertEqual(len(history), 1)
                self.assertEqual(history[0]["status"], "failed")
                self.assertEqual(history[0]["media_types"], ["pdf"])
                self.assertEqual(history[0]["tasks"][0]["failed_count"], 1)
            finally:
                db.session.remove()
                db.drop_all()

    def test_total_submit_failure_is_still_visible_in_history(self):
        with self.app.app_context():
            db.create_all()
            try:
                with patch.object(
                    content_pipeline_api,
                    "enqueue_pdf_clean_task",
                    side_effect=RuntimeError("PDF 入队失败"),
                ), patch.object(
                    content_pipeline_api,
                    "enqueue_audio_clean_task",
                    side_effect=RuntimeError("MP3 入队失败"),
                ):
                    response = self.client.post(
                        "/api/v1/content-pipeline/web-submit",
                        data={
                            "input_mode": "upload",
                            "files": [
                                (io.BytesIO(b"pdf bytes"), "失败.pdf"),
                                (io.BytesIO(b"audio bytes"), "失败.mp3"),
                            ],
                        },
                        content_type="multipart/form-data",
                    )

                self.assertEqual(response.status_code, 500)
                submission_id = response.get_json()["data"]["submission_id"]
                history = self.client.get(
                    "/api/v1/content-pipeline/web-submissions"
                ).get_json()["data"]["items"]
                self.assertEqual(len(history), 1)
                self.assertEqual(history[0]["submission_id"], submission_id)
                self.assertEqual(history[0]["status"], "failed")
                self.assertEqual(history[0]["media_types"], ["pdf", "mp3"])
                self.assertTrue(all(item["status"] == "failed" for item in history[0]["tasks"]))
            finally:
                db.session.remove()
                db.drop_all()

    def test_history_groups_one_web_submission_and_tracks_publish_status(self):
        submitted_at = datetime(2026, 9, 12, 6, 30, 0)

        def clean_task(task_id, kind):
            feature = "content_pdf_clean" if kind == "pdf" else "content_audio_clean"
            return PublicationTask(
                id=task_id,
                status="completed",
                current_step="done",
                progress=100,
                created_at=submitted_at,
                completed_at=submitted_at + timedelta(minutes=2),
                params={
                    "feature": feature,
                    "user_id": "42",
                    "content_pipeline": {
                        "title": f"网页投递 {kind.upper()}",
                        "metadata": {
                            "submission_channel": "content_pipeline_web",
                            "web_submission_id": "submission-1",
                            "input_mode": "folder",
                        },
                    },
                },
                result={"files_count": 1, "success_count": 1},
            )

        with self.app.app_context():
            db.create_all()
            try:
                pdf_task = clean_task("pdf-task-1", "pdf")
                mp3_task = clean_task("mp3-task-1", "mp3")
                publish_task = PublicationTask(
                    id="publish-task-1",
                    status="processing",
                    current_step="publish",
                    progress=45,
                    created_at=submitted_at + timedelta(minutes=2),
                    params={
                        "feature": "zsxq_clean_output_publish",
                        "user_id": "42",
                        "source_task_id": "pdf-task-1",
                        "zsxq_publish": {
                            "source_task_id": "pdf-task-1",
                            "title": "知识星球清洗产物发布",
                        },
                    },
                    result={},
                )
                db.session.add_all([pdf_task, mp3_task, publish_task])
                db.session.commit()

                response = self.client.get(
                    "/api/v1/content-pipeline/web-submissions?limit=20"
                )

                self.assertEqual(response.status_code, 200)
                payload = response.get_json()
                self.assertTrue(payload["success"])
                self.assertFalse(payload["data"]["has_more"])
                self.assertEqual(len(payload["data"]["items"]), 1)

                submission = payload["data"]["items"][0]
                self.assertEqual(submission["submission_id"], "submission-1")
                self.assertEqual(submission["input_mode"], "folder")
                self.assertEqual(submission["media_types"], ["pdf", "mp3"])
                self.assertEqual(submission["status"], "processing")

                tasks = {item["kind"]: item for item in submission["tasks"]}
                self.assertEqual(tasks["pdf"]["status"], "processing")
                self.assertEqual(tasks["pdf"]["progress"], 45)
                self.assertEqual(tasks["pdf"]["publish_task_id"], "publish-task-1")
                self.assertEqual(tasks["mp3"]["status"], "completed")
                self.assertEqual(tasks["mp3"]["progress"], 100)
            finally:
                db.session.remove()
                db.drop_all()

    def test_history_does_not_mix_another_users_task_with_the_same_submission_id(self):
        submitted_at = datetime(2026, 9, 12, 7, 0, 0)

        def task(task_id, feature, user_id):
            return PublicationTask(
                id=task_id,
                status="completed",
                progress=100,
                created_at=submitted_at,
                params={
                    "feature": feature,
                    "user_id": user_id,
                    "content_pipeline": {
                        "metadata": {
                            "submission_channel": "content_pipeline_web",
                            "web_submission_id": "shared-submission-id",
                            "input_mode": "upload",
                        },
                    },
                },
                result={"files_count": 1, "success_count": 1},
            )

        with self.app.app_context():
            db.create_all()
            try:
                db.session.add_all(
                    [
                        task("current-user-pdf", "content_pdf_clean", "42"),
                        task("other-user-mp3", "content_audio_clean", "99"),
                    ]
                )
                db.session.commit()

                response = self.client.get("/api/v1/content-pipeline/web-submissions")

                self.assertEqual(response.status_code, 200)
                items = response.get_json()["data"]["items"]
                self.assertEqual(len(items), 1)
                self.assertEqual(items[0]["media_types"], ["pdf"])
                self.assertEqual(
                    [item["task_id"] for item in items[0]["tasks"]],
                    ["current-user-pdf"],
                )
            finally:
                db.session.remove()
                db.drop_all()

    def test_history_ignores_a_publish_child_owned_by_another_user(self):
        submitted_at = datetime(2026, 9, 12, 7, 30, 0)
        with self.app.app_context():
            db.create_all()
            try:
                clean_task = PublicationTask(
                    id="current-user-clean",
                    status="completed",
                    current_step="done",
                    progress=100,
                    created_at=submitted_at,
                    params={
                        "feature": "content_pdf_clean",
                        "user_id": "42",
                        "content_pipeline": {
                            "metadata": {
                                "submission_channel": "content_pipeline_web",
                                "web_submission_id": "submission-owned",
                                "input_mode": "upload",
                            },
                        },
                    },
                    result={"files_count": 1, "success_count": 1},
                )
                foreign_publish = PublicationTask(
                    id="foreign-publish",
                    status="processing",
                    current_step="publish",
                    progress=30,
                    created_at=submitted_at + timedelta(minutes=1),
                    params={
                        "feature": "zsxq_clean_output_publish",
                        "user_id": "99",
                        "source_task_id": "current-user-clean",
                        "zsxq_publish": {"source_task_id": "current-user-clean"},
                    },
                    result={},
                )
                db.session.add_all([clean_task, foreign_publish])
                db.session.commit()

                history = self.client.get(
                    "/api/v1/content-pipeline/web-submissions"
                ).get_json()["data"]["items"]
                self.assertEqual(history[0]["status"], "completed")
                self.assertIsNone(history[0]["tasks"][0]["publish_task_id"])
            finally:
                db.session.remove()
                db.drop_all()

    def test_history_marks_completed_clean_task_with_failed_files_as_partial(self):
        submitted_at = datetime(2026, 9, 12, 7, 45, 0)
        with self.app.app_context():
            db.create_all()
            try:
                db.session.add(
                    PublicationTask(
                        id="partial-clean",
                        status="completed",
                        current_step="done",
                        progress=100,
                        created_at=submitted_at,
                        params={
                            "feature": "content_pdf_clean",
                            "user_id": "42",
                            "content_pipeline": {
                                "metadata": {
                                    "submission_channel": "content_pipeline_web",
                                    "web_submission_id": "submission-partial",
                                    "input_mode": "folder",
                                },
                            },
                        },
                        result={
                            "files_count": 2,
                            "success_count": 1,
                            "failed_count": 1,
                        },
                    )
                )
                db.session.commit()

                history = self.client.get(
                    "/api/v1/content-pipeline/web-submissions"
                ).get_json()["data"]["items"]
                self.assertEqual(history[0]["status"], "partial_failed")
                self.assertEqual(history[0]["tasks"][0]["status"], "partial_failed")
                self.assertEqual(history[0]["tasks"][0]["failed_count"], 1)
            finally:
                db.session.remove()
                db.drop_all()

    def test_history_supports_loading_every_page(self):
        with self.app.app_context():
            db.create_all()
            try:
                for index in range(3):
                    db.session.add(
                        PublicationTask(
                            id=f"history-task-{index}",
                            status="pending",
                            progress=0,
                            created_at=datetime(2026, 9, 12, 8, index, 0),
                            params={
                                "feature": "content_pdf_clean",
                                "user_id": "42",
                                "content_pipeline": {
                                    "metadata": {
                                        "submission_channel": "content_pipeline_web",
                                        "web_submission_id": f"submission-{index}",
                                        "input_mode": "upload",
                                    },
                                },
                            },
                            result={},
                        )
                    )
                db.session.commit()

                first = self.client.get(
                    "/api/v1/content-pipeline/web-submissions?limit=1&offset=0"
                ).get_json()["data"]
                second = self.client.get(
                    "/api/v1/content-pipeline/web-submissions?limit=1&offset=1"
                ).get_json()["data"]

                self.assertEqual(first["items"][0]["submission_id"], "submission-2")
                self.assertTrue(first["has_more"])
                self.assertEqual(first["next_offset"], 1)
                self.assertEqual(second["items"][0]["submission_id"], "submission-1")
                self.assertTrue(second["has_more"])
                self.assertEqual(second["next_offset"], 2)
            finally:
                db.session.remove()
                db.drop_all()


if __name__ == "__main__":
    unittest.main()
