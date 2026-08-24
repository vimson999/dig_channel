import sys
import tempfile
import unittest
from pathlib import Path

from flask import Flask


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from models import db  # noqa: E402
from models.content_pipeline_asset import ContentPipelineAsset  # noqa: E402
from models.task import PublicationTask  # noqa: E402
from services.mp3_video_workflow import (  # noqa: E402
    audio_meta_from_asset_reference,
    list_audio_assets,
)


class Mp3VideoAudioAssetMergeTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
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
        self.temp_dir.cleanup()

    def test_audio_asset_list_includes_manual_pipeline_submission(self):
        path = Path(self.temp_dir.name) / "手动投递.mp3"
        path.write_bytes(b"manual-audio")
        task = PublicationTask(
            status="pending",
            current_step="init",
            progress=0,
            params={"feature": "content_audio_clean"},
        )
        db.session.add(task)
        db.session.flush()
        db.session.add(
            ContentPipelineAsset(
                media_type="audio",
                content_sha256="a" * 64,
                canonical_task_id=str(task.id),
                source_path=str(path),
                filename=path.name,
                source_mode="manual_download",
                status="reserved",
            )
        )
        db.session.commit()

        payload = list_audio_assets(page=1, per_page=30, scope="all")

        manual = [item for item in payload["items"] if item.get("asset_kind") == "content_pipeline"]
        self.assertEqual(len(manual), 1)
        self.assertEqual(manual[0]["filename"], path.name)
        self.assertEqual(manual[0]["source_label"], "手动投递")
        self.assertTrue(manual[0]["attachment_id"].startswith("pipeline:"))
        resolved = audio_meta_from_asset_reference(manual[0]["attachment_id"])
        self.assertEqual(resolved["path"], str(path))


if __name__ == "__main__":
    unittest.main()
