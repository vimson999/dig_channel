import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from flask import Flask  # noqa: E402

from api import report_audio_workflow as report_audio_api  # noqa: E402
from models import db  # noqa: E402
from models.content_pipeline_asset import ContentPipelineAsset  # noqa: E402
from models.task import PublicationTask  # noqa: E402


class ReportPdfAssetMergeTest(unittest.TestCase):
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
        self.output_root = Path(self.tmpdir.name) / "content-pipeline-output"
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
        self.tmpdir.cleanup()

    def test_manual_pipeline_pdf_has_a_selectable_page_asset_payload(self):
        raw_path = Path(self.tmpdir.name) / "manual-report.pdf"
        raw_path.write_bytes(b"%PDF-manual")
        cleaned_path = self.output_root / "batch-1" / "pdf_cleaned" / raw_path.name
        cleaned_path.parent.mkdir(parents=True)
        cleaned_path.write_bytes(b"%PDF-cleaned")
        task = PublicationTask(
            status="queued",
            current_step="queued",
            progress=0,
            feature="content_pdf_clean",
            params={"feature": "content_pdf_clean"},
            result={},
        )
        db.session.add(task)
        db.session.flush()
        asset = ContentPipelineAsset(
            media_type="pdf",
            content_sha256="a" * 64,
            canonical_task_id=task.id,
            source_path=str(raw_path.resolve()),
            filename=raw_path.name,
            source_mode="manual_download",
            status="cleaned",
            cleaned_path=str(cleaned_path.resolve()),
            cleaned_sha256="b" * 64,
            cleaned_bytes=cleaned_path.stat().st_size,
        )
        db.session.add(asset)
        db.session.commit()

        payload = report_audio_api._content_pipeline_pdf_asset_row_payload(asset)

        self.assertIsNotNone(payload)
        self.assertEqual(f"pipeline:{asset.id}", payload["id"])
        self.assertEqual("手动投递", payload["source_label"])
        self.assertEqual("content_pipeline", payload["asset_kind"])
        self.assertEqual(str(cleaned_path.resolve()), payload["local_path"])

        metadata = report_audio_api._pdf_meta_from_asset_reference(payload["id"], "job-1")
        self.assertEqual(payload["id"], metadata["attachment_id"])
        self.assertEqual(str(cleaned_path.resolve()), metadata["path"])
        self.assertEqual("manual_download", metadata["source_mode"])

    def test_legacy_manual_pdf_reference_does_not_fall_back_to_raw_file(self):
        pdf_path = Path(self.tmpdir.name) / "legacy-manual-report.pdf"
        pdf_path.write_bytes(b"%PDF-legacy")
        task = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            feature="content_pdf_clean",
            params={
                "feature": "content_pdf_clean",
                "content_pipeline": {
                    "metadata": {"source_mode": "manual_download"},
                    "pdf_clean": {"files": [str(pdf_path)]},
                },
            },
            result={},
        )
        db.session.add(task)
        db.session.commit()

        with self.assertRaisesRegex(ValueError, "尚无可用清洗产物"):
            report_audio_api._pdf_meta_from_asset_reference(
                f"legacy:{task.id}:0",
                "job-legacy",
            )


if __name__ == "__main__":
    unittest.main()
