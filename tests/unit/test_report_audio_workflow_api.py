import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from api import report_audio_workflow as report_audio_api  # noqa: E402
from models import db  # noqa: E402
from models.task import PublicationTask  # noqa: E402
from services.report_audio_workflow import FEATURE_NAME  # noqa: E402


class ReportAudioWorkflowApiTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
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
        self.tmpdir.cleanup()

    def test_vertical_backfill_creates_vertical_only_publisher_task(self):
        root = Path(self.tmpdir.name)
        audio_path = root / "audio.wav"
        screenshot_path = root / "page.jpg"
        audio_path.write_bytes(b"RIFF----WAVE")
        screenshot_path.write_bytes(b"jpeg")

        parent = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            params={
                "feature": FEATURE_NAME,
                "user_id": "user-1",
                "report_audio_workflow": {
                    "pdf": {"filename": "sample.pdf", "path": str(root / "sample.pdf")},
                    "video": {
                        "enabled": True,
                        "title": "大摩:十天内两度上调国巨",
                        "subtitle": "AI机柜电容产值飙升182%",
                        "sub_burn_enabled": True,
                    },
                },
            },
            result={
                "audio": {"wav_path": str(audio_path), "seconds": 12.0},
                "structured_result": {
                    "final_script_for_tts": "这是一段用于生成字幕的口播稿。",
                    "cover_title": "大摩:十天内两度上调国巨",
                    "cover_subtitle": "AI机柜电容产值飙升182%",
                },
                "pdf_first_page_image": {"path": str(screenshot_path), "bytes": 4},
                "video": {"video_path": str(root / "landscape.mp4")},
            },
        )
        db.session.add(parent)
        db.session.commit()

        with patch.object(
            report_audio_api.run_publication_pipeline,
            "apply_async",
            return_value=SimpleNamespace(id="celery-vertical"),
        ) as apply_async:
            child = report_audio_api._create_vertical_video_backfill_task(parent)

        apply_async.assert_called_once_with(args=[child.id], queue="render", retry=False)
        self.assertEqual(child.celery_task_id, "celery-vertical")
        self.assertEqual(child.params["feature"], "publisher")
        self.assertEqual(child.params["source_feature"], FEATURE_NAME)
        self.assertEqual(child.params["source_task_id"], parent.id)
        self.assertFalse(child.params["video"]["enabled"])
        self.assertTrue(child.params["vertical_video"]["enabled"])
        self.assertEqual(child.params["vertical_video"]["image_path"], str(screenshot_path))
        self.assertTrue(child.params["vertical_thumbnail"]["enabled"])
        self.assertFalse(child.params["bilibili"]["enabled"])
        self.assertNotIn("thumbnail", child.params)
        self.assertEqual(child.result["metadata"]["source"], report_audio_api.VERTICAL_BACKFILL_SOURCE)

    def test_regenerate_cover_updates_parent_result_and_keeps_history(self):
        root = Path(self.tmpdir.name)
        screenshot_path = root / "page.jpg"
        old_cover_path = root / "old-cover.jpg"
        new_cover_path = root / "new-cover.jpg"
        screenshot_path.write_bytes(b"jpeg")
        old_cover_path.write_bytes(b"old")

        parent = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            params={
                "feature": FEATURE_NAME,
                "user_id": "user-1",
                "report_audio_workflow": {
                    "pdf": {"filename": "sample.pdf", "path": str(root / "sample.pdf")},
                    "video": {
                        "enabled": True,
                        "tag_text": "研报播客",
                        "overlay_opacity": "0.6",
                        "cover_transform": {"x": 1, "y": 2, "scale": 1.1},
                    },
                },
            },
            result={
                "structured_result": {
                    "cover_title": "旧标题",
                    "cover_subtitle": "旧副标题",
                },
                "pdf_first_page_image": {"path": str(screenshot_path), "bytes": 4},
                "video": {
                    "video_path": str(root / "landscape.mp4"),
                    "cover_path": str(old_cover_path),
                },
            },
        )
        db.session.add(parent)
        db.session.commit()

        def fake_generate_thumbnail(**kwargs):
            self.assertEqual(kwargs["main_title"], "新标题")
            self.assertEqual(kwargs["sub_title"], "新副标题")
            self.assertEqual(kwargs["custom_bg_path"], str(screenshot_path))
            self.assertEqual(kwargs["mode"], "screenshot")
            self.assertEqual(kwargs["transform"], {"x": 1, "y": 2, "scale": 1.1})
            new_cover_path.write_bytes(b"new")
            return str(new_cover_path)

        with patch.object(report_audio_api.thumbnail_service, "generate_thumbnail", side_effect=fake_generate_thumbnail):
            cover = report_audio_api._regenerate_cover_for_task(parent, title="新标题", subtitle="新副标题")

        self.assertEqual(cover["path"], str(new_cover_path))
        self.assertEqual(cover["title"], "新标题")
        self.assertEqual(cover["subtitle"], "新副标题")

        db.session.refresh(parent)
        result = parent.result
        self.assertEqual(result["video"]["cover_path"], str(new_cover_path))
        self.assertEqual(result["video"]["cover_title"], "新标题")
        self.assertEqual(result["video"]["cover_subtitle"], "新副标题")
        self.assertEqual(result["structured_result"]["cover_title"], "新标题")
        self.assertEqual(result["structured_result"]["cover_subtitle"], "新副标题")
        self.assertEqual(result["cover_regenerations"][-1]["cover_path"], str(old_cover_path))
        self.assertEqual(result["cover_regenerations"][-1]["title"], "旧标题")
        self.assertEqual(parent.report_audio_cover_title, "新标题")
        self.assertEqual(parent.report_audio_cover_path, str(new_cover_path))


if __name__ == "__main__":
    unittest.main()
