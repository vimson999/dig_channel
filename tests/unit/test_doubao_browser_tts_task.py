import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from models import db  # noqa: E402
from models.task import PublicationTask  # noqa: E402
from tasks import doubao_browser_tts  # noqa: E402


class RetryScheduled(RuntimeError):
    pass


class FakeCeleryTask:
    def __init__(self, retries=0):
        self.request = SimpleNamespace(retries=retries)
        self.retry_kwargs = None

    def retry(self, **kwargs):
        self.retry_kwargs = kwargs
        raise RetryScheduled()


class DoubaoBrowserTTSTaskTest(unittest.TestCase):
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

    def _create_task(self):
        text = "用一句话说明 AI 服务器为什么需要更高效的散热系统。" * 8
        task = PublicationTask(
            status="pending",
            current_step="queued",
            progress=0,
            params={
                "feature": "doubao_browser_tts",
                "doubao_browser_tts": {
                    "text": text,
                    "metadata": {"text_hash": "hash-1"},
                },
            },
            result={"metadata": {"text_hash": "hash-1"}, "audio": None, "logs": []},
        )
        db.session.add(task)
        db.session.commit()
        return task

    def test_short_audio_failure_auto_retries_before_final_failure(self):
        task = self._create_task()
        celery_task = FakeCeleryTask(retries=0)
        worker = SimpleNamespace(
            synthesize=lambda **kwargs: {"wav_path": "/tmp/short.wav", "seconds": 0.447, "bytes": 1024}
        )

        with patch.object(doubao_browser_tts, "DoubaoBrowserTTSWorker", return_value=worker), \
            patch.dict("os.environ", {"DOUBAO_BROWSER_TTS_RETRY_LIMIT": "2", "DOUBAO_BROWSER_TTS_RETRY_DELAY_SECONDS": "7"}, clear=False):
            with self.assertRaises(RetryScheduled):
                doubao_browser_tts._process_job(task.id, celery_task=celery_task)

        self.assertEqual(celery_task.retry_kwargs["countdown"], 7)
        self.assertEqual(celery_task.retry_kwargs["max_retries"], 2)
        db.session.refresh(task)
        self.assertEqual(task.status, "pending")
        self.assertEqual(task.current_step, "auto_retry")
        self.assertIsNone(task.error_message)
        self.assertEqual(task.result["retry_state"]["retry_number"], 1)
        self.assertIn("豆包音频时长异常", task.result["last_error"])

    def test_short_audio_failure_marks_failed_after_retry_limit(self):
        task = self._create_task()
        celery_task = FakeCeleryTask(retries=2)
        worker = SimpleNamespace(
            synthesize=lambda **kwargs: {"wav_path": "/tmp/short.wav", "seconds": 0.447, "bytes": 1024}
        )

        with patch.object(doubao_browser_tts, "DoubaoBrowserTTSWorker", return_value=worker), \
            patch.dict("os.environ", {"DOUBAO_BROWSER_TTS_RETRY_LIMIT": "2"}, clear=False):
            ok = doubao_browser_tts._process_job(task.id, celery_task=celery_task)

        self.assertFalse(ok)
        db.session.refresh(task)
        self.assertEqual(task.status, "failed")
        self.assertEqual(task.current_step, "failed")
        self.assertIn("豆包音频时长异常", task.error_message)


if __name__ == "__main__":
    unittest.main()
