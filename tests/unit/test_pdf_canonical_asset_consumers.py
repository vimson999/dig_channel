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

from api import report_audio_workflow as report_audio_api  # noqa: E402
from api import report_script_material_workflow as report_script_api  # noqa: E402
from models import db  # noqa: E402
from models.content_pipeline_asset import ContentPipelineAsset  # noqa: E402
from models.task import PublicationTask  # noqa: E402
from services import report_top10_workflow as report_top10_service  # noqa: E402


class PdfCanonicalAssetConsumersTest(unittest.TestCase):
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

    def _cleaned_pdf_asset(self):
        raw_path = self.root / "source.pdf"
        raw_path.write_bytes(b"%PDF-raw")
        cleaned_path = self.output_root / "batch-1" / "pdf_cleaned" / raw_path.name
        cleaned_path.parent.mkdir(parents=True)
        cleaned_path.write_bytes(b"%PDF-cleaned")
        task = PublicationTask(
            feature="content_pdf_clean",
            status="completed",
            current_step="completed",
            progress=100,
            params={"feature": "content_pdf_clean"},
        )
        db.session.add(task)
        db.session.flush()
        asset = ContentPipelineAsset(
            media_type="pdf",
            content_sha256="a" * 64,
            canonical_task_id=str(task.id),
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
        return raw_path, cleaned_path.resolve(), asset

    def test_every_pdf_consumer_resolves_pipeline_reference_after_raw_source_deletion(self):
        raw_path, cleaned_path, asset = self._cleaned_pdf_asset()
        raw_path.unlink()
        reference = f"pipeline:{asset.id}"

        self.assertEqual(
            report_audio_api._pdf_meta_from_asset_reference(reference, None)["path"],
            str(cleaned_path),
        )
        self.assertEqual(
            report_script_api._pdf_meta_from_asset_reference(reference)["path"],
            str(cleaned_path),
        )
        self.assertEqual(
            report_top10_service.pdf_meta_from_asset_reference(reference)["path"],
            str(cleaned_path),
        )

    def test_report_audio_catalog_lists_only_ready_cleaned_assets_from_all_sources(self):
        expected_labels = {
            "manual_download": "手动投递",
            "sidebar": "侧边栏下载",
            "scheduled_api": "计划任务",
        }
        raw_paths = []
        for index, source_mode in enumerate(expected_labels, start=1):
            raw_path = self.root / f"source-{index}.pdf"
            raw_path.write_bytes(f"%PDF-raw-{index}".encode())
            cleaned_path = self.output_root / "batch-2" / "pdf_cleaned" / raw_path.name
            cleaned_path.parent.mkdir(parents=True, exist_ok=True)
            cleaned_path.write_bytes(f"%PDF-cleaned-{index}".encode())
            task = PublicationTask(
                feature="content_pdf_clean",
                status="completed",
                current_step="completed",
                progress=100,
                params={"feature": "content_pdf_clean"},
            )
            db.session.add(task)
            db.session.flush()
            db.session.add(ContentPipelineAsset(
                media_type="pdf",
                content_sha256=(chr(97 + index) * 64),
                canonical_task_id=str(task.id),
                source_path=str(raw_path.resolve()),
                filename=raw_path.name,
                source_mode=source_mode,
                status="cleaned",
                cleaned_path=str(cleaned_path.resolve()),
                cleaned_sha256=(chr(107 + index) * 64),
                cleaned_bytes=cleaned_path.stat().st_size,
            ))
            raw_paths.append(raw_path)

        unavailable_raw = self.root / "unavailable.pdf"
        unavailable_raw.write_bytes(b"%PDF-unavailable")
        unavailable_task = PublicationTask(
            feature="content_pdf_clean",
            status="completed",
            current_step="completed",
            progress=100,
            params={"feature": "content_pdf_clean"},
        )
        db.session.add(unavailable_task)
        db.session.flush()
        db.session.add(ContentPipelineAsset(
            media_type="pdf",
            content_sha256="z" * 64,
            canonical_task_id=str(unavailable_task.id),
            source_path=str(unavailable_raw.resolve()),
            filename=unavailable_raw.name,
            source_mode="manual_download",
            status="cleaned",
            cleaned_path=str(self.output_root / "batch-2" / "pdf_cleaned" / "missing.pdf"),
        ))
        db.session.commit()
        for raw_path in [*raw_paths, unavailable_raw]:
            raw_path.unlink()

        with self.app.test_request_context("/pdf-assets?page=1&per_page=30&scope=all"):
            response = report_audio_api.list_pdf_assets.__wrapped__(None)
        payload = response.get_json()["data"]
        items = payload["items"]

        self.assertEqual(len(items), 3)
        self.assertEqual(
            {item["source_label"] for item in items},
            set(expected_labels.values()),
        )
        self.assertTrue(all(item["id"].startswith("pipeline:") for item in items))
        self.assertTrue(all("content-pipeline-output" in item["local_path"] for item in items))
        self.assertEqual(payload["pagination"]["skipped_missing"], 1)


if __name__ == "__main__":
    unittest.main()
