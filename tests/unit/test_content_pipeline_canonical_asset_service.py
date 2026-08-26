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
from services.content_pipeline.canonical_asset_service import (  # noqa: E402
    register_cleaned_outputs,
    resolve_cleaned_asset,
)


class ContentPipelineCanonicalAssetServiceTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.output_root = self.root / "content-pipeline-output"
        self.app = Flask(__name__)
        self.app.config.update(
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(self.app)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
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

    def _asset(self, *, media_type, source_path, status="cleaning"):
        task = PublicationTask(
            feature=f"content_{media_type}_clean",
            status="processing",
            current_step="cleaning",
            progress=10,
            params={"feature": f"content_{media_type}_clean"},
        )
        db.session.add(task)
        db.session.flush()
        asset = ContentPipelineAsset(
            media_type=media_type,
            content_sha256=("a" if media_type == "audio" else "b") * 64,
            canonical_task_id=str(task.id),
            source_path=str(source_path.resolve()),
            filename=source_path.name,
            source_mode="manual_download",
            status=status,
        )
        db.session.add(asset)
        db.session.commit()
        return asset

    def test_registers_cleaned_audio_and_resolves_it_after_raw_source_is_deleted(self):
        raw = self.root / "source.mp3"
        raw.write_bytes(b"raw-audio")
        cleaned = self.output_root / "batch-1" / "audio_cleaned" / "source.mp3"
        cleaned.parent.mkdir(parents=True)
        cleaned.write_bytes(b"cleaned-audio")
        asset = self._asset(media_type="audio", source_path=raw)

        registration = register_cleaned_outputs(
            asset.canonical_task_id,
            media_type="audio",
            cleaned_files=[{"source_path": str(raw), "output_path": str(cleaned)}],
        )
        db.session.refresh(asset)
        raw.unlink()

        resolution = resolve_cleaned_asset(asset, media_type="audio")

        self.assertEqual(registration.registered_paths, [str(cleaned.resolve())])
        self.assertEqual(asset.status, "cleaned")
        self.assertEqual(asset.cleaned_path, str(cleaned.resolve()))
        self.assertEqual(resolution.state, "ready")
        self.assertEqual(resolution.path, cleaned.resolve())

    def test_missing_cleaned_output_never_falls_back_to_raw_source(self):
        raw = self.root / "source.pdf"
        raw.write_bytes(b"%PDF-raw")
        cleaned = self.output_root / "batch-2" / "pdf_cleaned" / "source.pdf"
        cleaned.parent.mkdir(parents=True)
        cleaned.write_bytes(b"%PDF-cleaned")
        asset = self._asset(media_type="pdf", source_path=raw)

        register_cleaned_outputs(
            asset.canonical_task_id,
            media_type="pdf",
            cleaned_files=[{"source_path": str(raw), "cleaner_output_path": str(cleaned)}],
        )
        db.session.refresh(asset)
        cleaned.unlink()

        resolution = resolve_cleaned_asset(asset, media_type="pdf")

        self.assertIsNone(resolution.path)
        self.assertEqual(resolution.state, "unavailable")
        self.assertEqual(resolution.reason, "cleaned_output_missing_or_invalid")


if __name__ == "__main__":
    unittest.main()
