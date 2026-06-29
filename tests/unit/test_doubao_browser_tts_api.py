import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from flask import Flask


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from api import doubao_browser_tts as doubao_api  # noqa: E402
from models import db  # noqa: E402
from models.task import PublicationTask  # noqa: E402


class DoubaoBrowserTTSApiCacheTest(unittest.TestCase):
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
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _completed_task(self, text, user_id="user-1"):
        wav_path = Path(self.tmp.name) / f"{user_id}.wav"
        wav_path.write_bytes(b"RIFF....WAVE")
        task = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            params={
                "feature": "doubao_browser_tts",
                "user_id": user_id,
                "doubao_browser_tts": {
                    "text": text,
                    "metadata": {"text_hash": doubao_api._text_hash(text)},
                },
            },
            result={
                "metadata": {"text_hash": doubao_api._text_hash(text)},
                "audio": {"wav_path": str(wav_path), "seconds": 12.3, "bytes": wav_path.stat().st_size},
            },
        )
        db.session.add(task)
        db.session.commit()
        return task

    def test_completed_task_for_text_reuses_current_user_audio(self):
        text = "这是一段已经生成过的基础文案。"
        task = self._completed_task(text)

        cached = doubao_api._completed_task_for_text(
            " 这是一段已经生成过的\n基础文案。 ",
            current_user=SimpleNamespace(id="user-1"),
        )

        self.assertIsNotNone(cached)
        self.assertEqual(cached.id, task.id)

    def test_completed_task_for_text_does_not_cross_users(self):
        text = "这是一段不能跨用户复用的基础文案。"
        self._completed_task(text, user_id="user-1")

        cached = doubao_api._completed_task_for_text(
            text,
            current_user=SimpleNamespace(id="user-2"),
        )

        self.assertIsNone(cached)


if __name__ == "__main__":
    unittest.main()
