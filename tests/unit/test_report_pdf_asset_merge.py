import sys
import tempfile
import unittest
from pathlib import Path


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

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()
        self.tmpdir.cleanup()

    def test_manual_pipeline_pdf_has_a_selectable_page_asset_payload(self):
        pdf_path = Path(self.tmpdir.name) / "manual-report.pdf"
        pdf_path.write_bytes(b"%PDF-manual")
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
            source_path=str(pdf_path),
            filename=pdf_path.name,
            source_mode="manual_download",
            status="reserved",
        )
        db.session.add(asset)
        db.session.commit()

        payload = report_audio_api._content_pipeline_pdf_asset_row_payload(asset)

        self.assertIsNotNone(payload)
        self.assertEqual(f"pipeline:{asset.id}", payload["id"])
        self.assertEqual("手动投递", payload["source_label"])
        self.assertEqual("content_pipeline", payload["asset_kind"])
        self.assertEqual(str(pdf_path.resolve()), payload["local_path"])

        metadata = report_audio_api._pdf_meta_from_asset_reference(payload["id"], "job-1")
        self.assertEqual(payload["id"], metadata["attachment_id"])
        self.assertEqual(str(pdf_path.resolve()), metadata["path"])
        self.assertEqual("manual_download", metadata["source_mode"])

    def test_legacy_manual_pdf_task_is_exposed_without_a_pipeline_row(self):
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

        items = report_audio_api._legacy_pdf_asset_items(limit=10)

        self.assertEqual(1, len(items))
        self.assertEqual(f"legacy:{task.id}:0", items[0]["id"])
        self.assertEqual("手动投递", items[0]["source_label"])
        metadata = report_audio_api._pdf_meta_from_asset_reference(items[0]["id"], "job-legacy")
        self.assertEqual(str(pdf_path.resolve()), metadata["path"])


if __name__ == "__main__":
    unittest.main()
