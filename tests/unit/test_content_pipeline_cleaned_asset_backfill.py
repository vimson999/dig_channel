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
    backfill_task_cleaned_outputs,
    resolve_cleaned_asset,
)


class ContentPipelineCleanedAssetBackfillTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.output_root = self.root / "runtime" / "content_pipeline" / "batches"
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

    def _legacy_pdf_task(self):
        source_root = self.root / "downloads"
        source_root.mkdir()
        raw = source_root / "report.pdf"
        raw.write_bytes(b"%PDF-raw")
        cleaned = self.output_root / "legacy-task" / "pdf_cleaned" / "report.pdf"
        cleaned.parent.mkdir(parents=True)
        cleaned.write_bytes(b"%PDF-cleaned")
        task = PublicationTask(
            feature="content_pdf_clean",
            status="completed",
            current_step="done",
            progress=100,
            params={
                "feature": "content_pdf_clean",
                "content_pipeline": {
                    "pdf_clean": {"source_root": str(source_root)},
                },
            },
            result={
                "cleaned_files": [
                    {
                        "source": "report.pdf",
                        "status": "SUCCESS",
                        "path": str(cleaned),
                    },
                ],
            },
        )
        db.session.add(task)
        db.session.flush()
        asset = ContentPipelineAsset(
            media_type="pdf",
            content_sha256="c" * 64,
            canonical_task_id=str(task.id),
            source_path=str(raw.resolve()),
            filename=raw.name,
            source_mode="sidebar",
            status="cleaned",
        )
        db.session.add(asset)
        db.session.commit()
        return task, asset, raw, cleaned

    def test_backfill_registers_legacy_relative_pdf_source_against_its_source_root(self):
        task, asset, raw, cleaned = self._legacy_pdf_task()

        report = backfill_task_cleaned_outputs(task)
        db.session.refresh(asset)
        raw.unlink()

        resolution = resolve_cleaned_asset(asset, media_type="pdf")
        self.assertEqual(report["registered_count"], 1)
        self.assertEqual(report["unresolved_count"], 0)
        self.assertEqual(asset.cleaned_path, str(cleaned.resolve()))
        self.assertEqual(resolution.state, "ready")
        self.assertEqual(resolution.path, cleaned.resolve())

    def test_backfill_dry_run_does_not_persist_cleaned_path(self):
        task, asset, _raw, cleaned = self._legacy_pdf_task()

        report = backfill_task_cleaned_outputs(task, dry_run=True)
        asset = db.session.get(ContentPipelineAsset, asset.id)

        self.assertEqual(report["registered_count"], 1)
        self.assertEqual(report["registered_paths"], [str(cleaned.resolve())])
        self.assertIsNone(asset.cleaned_path)


if __name__ == "__main__":
    unittest.main()
