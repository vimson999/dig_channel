import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from models import db  # noqa: E402
from models.content_pipeline_asset import ContentPipelineAsset  # noqa: E402
from models.task import PublicationTask  # noqa: E402
from models.zsxq_sync import ZsxqAttachment  # noqa: E402
from services.mp3_video_workflow import (  # noqa: E402
    audio_meta_from_asset_reference,
    audio_meta_from_zsxq_attachment,
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
        self.output_root = Path(self.temp_dir.name) / "content-pipeline-output"
        self.env_patch = patch.dict(
            os.environ,
            {"CONTENT_PIPELINE_OUTPUT_ROOT": str(self.output_root)},
            clear=False,
        )
        self.env_patch.start()

    def tearDown(self):
        self.env_patch.stop()
        db.session.remove()
        db.drop_all()
        self.ctx.pop()
        self.temp_dir.cleanup()

    def test_audio_asset_list_uses_cleaned_output_after_raw_source_is_deleted(self):
        raw_path = Path(self.temp_dir.name) / "手动投递.mp3"
        raw_path.write_bytes(b"manual-audio")
        cleaned_path = self.output_root / "batch-1" / "audio_cleaned" / raw_path.name
        cleaned_path.parent.mkdir(parents=True)
        cleaned_path.write_bytes(b"cleaned-audio")
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
                source_path=str(raw_path.resolve()),
                filename=raw_path.name,
                source_mode="manual_download",
                status="cleaned",
                cleaned_path=str(cleaned_path),
                cleaned_sha256="b" * 64,
                cleaned_bytes=cleaned_path.stat().st_size,
            )
        )
        db.session.commit()
        raw_path.unlink()

        payload = list_audio_assets(page=1, per_page=30, scope="all")

        manual = [item for item in payload["items"] if item.get("asset_kind") == "content_pipeline"]
        self.assertEqual(len(manual), 1)
        self.assertEqual(manual[0]["filename"], raw_path.name)
        self.assertEqual(manual[0]["source_label"], "手动投递")
        self.assertTrue(manual[0]["attachment_id"].startswith("pipeline:"))
        self.assertEqual(manual[0]["path"], str(cleaned_path.resolve()))
        resolved = audio_meta_from_asset_reference(manual[0]["attachment_id"])
        self.assertEqual(resolved["path"], str(cleaned_path.resolve()))

    def test_synced_attachment_uses_cleaned_output_after_raw_source_is_deleted(self):
        raw_path = Path(self.temp_dir.name) / "同步音频.mp3"
        raw_path.write_bytes(b"raw-synced-audio")
        cleaned_path = self.output_root / "batch-2" / "audio_cleaned" / raw_path.name
        cleaned_path.parent.mkdir(parents=True)
        cleaned_path.write_bytes(b"cleaned-synced-audio")
        task = PublicationTask(
            status="completed",
            current_step="completed",
            progress=100,
            params={"feature": "content_audio_clean"},
        )
        attachment = ZsxqAttachment(
            group_id="group-1",
            tab_id="tab-1",
            topic_id="topic-1",
            attachment_key="audio-1",
            attachment_type="file",
            name=raw_path.name,
            file_id="file-1",
            download_count=25,
            local_path=str(raw_path),
            download_status="downloaded",
        )
        db.session.add_all([task, attachment])
        db.session.flush()
        db.session.add(
            ContentPipelineAsset(
                media_type="audio",
                content_sha256="c" * 64,
                canonical_task_id=str(task.id),
                source_path=str(raw_path.resolve()),
                filename=raw_path.name,
                source_mode="sidebar",
                status="cleaned",
                cleaned_path=str(cleaned_path),
                cleaned_sha256="d" * 64,
                cleaned_bytes=cleaned_path.stat().st_size,
            )
        )
        db.session.commit()
        raw_path.unlink()

        payload = list_audio_assets(page=1, per_page=30, scope="all")

        synced = [item for item in payload["items"] if item.get("asset_kind") == "zsxq_attachment"]
        self.assertEqual(len(synced), 1)
        self.assertEqual(synced[0]["attachment_id"], attachment.id)
        self.assertEqual(synced[0]["source_label"], "知识星球·侧边栏下载")
        self.assertEqual(synced[0]["path"], str(cleaned_path.resolve()))
        resolved = audio_meta_from_zsxq_attachment(attachment.id)
        self.assertEqual(resolved["path"], str(cleaned_path.resolve()))


if __name__ == "__main__":
    unittest.main()
