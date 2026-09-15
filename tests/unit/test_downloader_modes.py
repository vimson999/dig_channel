import os
import sys
import tempfile
import unittest
from unittest.mock import patch


BACKEND_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "src", "backend")
)
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from flask import Flask

from apis.v1.downloader import bp
from models import DownloadTask, db
from services.download_service import DownloadService


class DownloaderModeApiTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(self.app)
        self.app.register_blueprint(bp)
        with self.app.app_context():
            db.create_all()
        self.client = self.app.test_client()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    @patch("apis.v1.downloader.apply_async_after_db_release")
    def test_missing_type_defaults_to_video(self, enqueue):
        enqueue.return_value.id = "celery-1"

        response = self.client.post(
            "/api/v1/downloader/submit", json={"url": "https://youtu.be/example"}
        )

        self.assertEqual(response.status_code, 200)
        with self.app.app_context():
            task = DownloadTask.query.one()
            self.assertEqual(task.download_type, "video")
            self.assertEqual(task.to_dict()["download_type"], "video")

    @patch("apis.v1.downloader.apply_async_after_db_release")
    def test_selected_audio_type_is_persisted(self, enqueue):
        enqueue.return_value.id = "celery-2"

        response = self.client.post(
            "/api/v1/downloader/submit",
            json={"url": "https://youtu.be/example", "download_type": "audio"},
        )

        self.assertEqual(response.status_code, 200)
        with self.app.app_context():
            self.assertEqual(DownloadTask.query.one().download_type, "audio")

    def test_unknown_type_is_rejected_without_creating_task(self):
        response = self.client.post(
            "/api/v1/downloader/submit",
            json={"url": "https://youtu.be/example", "download_type": "images"},
        )

        self.assertEqual(response.status_code, 400)
        with self.app.app_context():
            self.assertEqual(DownloadTask.query.count(), 0)

    @patch("apis.v1.downloader.apply_async_after_db_release")
    def test_running_task_can_be_cancelled_without_deleting_its_record(self, enqueue):
        enqueue.return_value.id = "celery-3"
        submitted = self.client.post(
            "/api/v1/downloader/submit", json={"url": "https://youtu.be/example"}
        ).get_json()
        with self.app.app_context():
            task = DownloadTask.query.get(submitted["task_id"])
            task.status = "downloading"
            db.session.commit()

        response = self.client.post(
            f"/api/v1/downloader/task/{submitted['task_id']}/cancel"
        )

        self.assertEqual(response.status_code, 200)
        with self.app.app_context():
            task = DownloadTask.query.get(submitted["task_id"])
            self.assertEqual(task.status, "cancelled")
            self.assertIsNone(task.error_message)

    @patch("apis.v1.downloader.apply_async_after_db_release")
    def test_completed_task_is_not_changed_by_cancel(self, enqueue):
        enqueue.return_value.id = "celery-4"
        submitted = self.client.post(
            "/api/v1/downloader/submit", json={"url": "https://youtu.be/example"}
        ).get_json()
        with self.app.app_context():
            task = DownloadTask.query.get(submitted["task_id"])
            task.status = "completed"
            db.session.commit()

        response = self.client.post(
            f"/api/v1/downloader/task/{submitted['task_id']}/cancel"
        )

        self.assertEqual(response.status_code, 409)
        with self.app.app_context():
            self.assertEqual(
                DownloadTask.query.get(submitted["task_id"]).status, "completed"
            )


class DownloadCancellationTest(unittest.TestCase):
    def test_cancellation_exception_is_not_reported_as_failure(self):
        app = Flask(__name__)
        app.config.update(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(app)
        with app.app_context():
            db.create_all()
            task = DownloadTask(url="https://youtu.be/example", status="cancelled")
            db.session.add(task)
            db.session.commit()

            service = DownloadService()
            result = service.download(task.id, task.url)

            self.assertIsNone(result)
            self.assertEqual(task.status, "cancelled")
            self.assertIsNone(task.error_message)


class SubtitleTextConversionTest(unittest.TestCase):
    def test_srt_is_converted_to_readable_deduplicated_text(self):
        srt = """1
00:00:00,000 --> 00:00:01,000
Hello world

2
00:00:01,000 --> 00:00:02,000
Hello world
第二行
"""
        with tempfile.TemporaryDirectory() as directory:
            source = os.path.join(directory, "source.srt")
            target = os.path.join(directory, "source.txt")
            with open(source, "w", encoding="utf-8") as handle:
                handle.write(srt)

            DownloadService.srt_to_text(source, target)

            with open(target, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "Hello world\n第二行\n")


if __name__ == "__main__":
    unittest.main()
