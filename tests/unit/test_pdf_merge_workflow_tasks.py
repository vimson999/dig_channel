import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import fitz
from flask import Flask


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from models import db  # noqa: E402
from models.content_pipeline_asset import ContentPipelineAsset  # noqa: E402
from models.task import PublicationTask  # noqa: E402
from api import report_audio_workflow as report_audio_api  # noqa: E402
from services.pdf_merge_workflow import (  # noqa: E402
    FEATURE_NAME,
    fingerprint_file,
    merge_pdf_sources,
    workflow_output_root,
    workflow_upload_root,
)
from services import runtime_guard  # noqa: E402
from tasks import pdf_merge_workflow  # noqa: E402


class PdfMergeWorkflowTaskTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.output_root = self.root / "content-pipeline-output"
        self.upload_root = self.root / "uploads"
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
            {
                "CONTENT_PIPELINE_OUTPUT_ROOT": str(self.output_root),
                "PDF_MERGE_WORKFLOW_UPLOAD_ROOT": str(self.upload_root),
            },
            clear=False,
        )
        self.env_patch.start()

    def tearDown(self):
        self.env_patch.stop()
        db.session.remove()
        db.drop_all()
        self.ctx.pop()
        self.temp_dir.cleanup()

    def _pdf(self, path, text):
        document = fitz.open()
        page = document.new_page()
        page.insert_text((72, 72), text)
        document.save(path)
        document.close()
        return path

    def _pipeline_asset(self, text):
        prior_task = PublicationTask(
            feature="content_pdf_clean",
            status="completed",
            current_step="done",
            progress=100,
            params={"feature": "content_pdf_clean"},
            result={},
        )
        db.session.add(prior_task)
        db.session.flush()
        source = self.root / f"source-{prior_task.id}.pdf"
        self._pdf(source, text)
        cleaned = self.output_root / "seed" / f"cleaned-{prior_task.id}.pdf"
        cleaned.parent.mkdir(parents=True, exist_ok=True)
        cleaned.write_bytes(source.read_bytes())
        fingerprint = fingerprint_file(cleaned)
        asset = ContentPipelineAsset(
            media_type="pdf",
            content_sha256=fingerprint["sha256"],
            canonical_task_id=str(prior_task.id),
            source_path=str(source.resolve()),
            filename=cleaned.name,
            source_mode="manual_download",
            status="cleaned",
            cleaned_path=fingerprint["path"],
            cleaned_sha256=fingerprint["sha256"],
            cleaned_bytes=fingerprint["bytes"],
        )
        db.session.add(asset)
        db.session.commit()
        return asset

    def _pending_task(self, *, asset=None, upload_text="UPLOAD"):
        task = PublicationTask(
            feature=FEATURE_NAME,
            status="pending",
            current_step="queued",
            progress=0,
            params={"feature": FEATURE_NAME, "user_id": "user-1", "pdf_merge_workflow": {}},
            result={"logs": []},
        )
        db.session.add(task)
        db.session.flush()
        upload_path = workflow_upload_root(str(task.id)) / "input-0001.pdf"
        self._pdf(upload_path, upload_text)
        upload = fingerprint_file(upload_path)
        sources = []
        if asset is not None:
            sources.append({
                "sequence": 1,
                "kind": "pipeline_asset",
                "asset_ref": f"pipeline:{asset.id}",
                "asset_id": str(asset.id),
                "filename": asset.filename,
                "cleaned_sha256": asset.cleaned_sha256,
                "bytes": asset.cleaned_bytes,
            })
        sources.append({
            "sequence": len(sources) + 1,
            "kind": "upload",
            "filename": upload_path.name,
            "path": upload["path"],
            "sha256": upload["sha256"],
            "bytes": upload["bytes"],
        })
        if asset is None:
            second_path = workflow_upload_root(str(task.id)) / "input-0002.pdf"
            self._pdf(second_path, "SECOND")
            second = fingerprint_file(second_path)
            sources.append({
                "sequence": 2,
                "kind": "upload",
                "filename": second_path.name,
                "path": second["path"],
                "sha256": second["sha256"],
                "bytes": second["bytes"],
            })
        task.params = {
            "feature": FEATURE_NAME,
            "user_id": "user-1",
            "pdf_merge_workflow": {"title": "季度研报合集", "sources": sources},
        }
        task.sync_projection_fields()
        db.session.commit()
        return task

    def _run(self, task_id):
        with patch.object(pdf_merge_workflow, "_create_pdf_merge_app", return_value=self.app), \
             patch.object(pdf_merge_workflow, "_notify", return_value={"queued": False}):
            return pdf_merge_workflow._run_pdf_merge_job(task_id)

    def _fresh_task(self, task_id):
        """Read through a new unit-of-work after the nested worker context exits."""
        db.session.expire_all()
        return db.session.get(PublicationTask, task_id)

    def test_process_job_registers_a_ready_pdf_merge_asset(self):
        asset = self._pipeline_asset("ASSET FIRST")
        task = self._pending_task(asset=asset, upload_text="UPLOAD SECOND")

        self.assertTrue(self._run(str(task.id)))

        stored = self._fresh_task(task.id)
        output = stored.result["output"]
        registered = db.session.get(ContentPipelineAsset, output["asset_id"])
        self.assertEqual(stored.status, "completed")
        self.assertEqual(stored.current_step, "completed")
        self.assertEqual(output["page_count"], 2)
        self.assertFalse(output["reused_asset"])
        self.assertEqual(output["asset_ref"], f"pipeline:{registered.id}")
        self.assertEqual(registered.source_mode, "pdf_merge")
        self.assertEqual(registered.status, "cleaned")
        self.assertTrue(Path(registered.cleaned_path).is_file())
        with fitz.open(registered.cleaned_path) as merged:
            self.assertIn("ASSET FIRST", merged[0].get_text())
            self.assertIn("UPLOAD SECOND", merged[1].get_text())

    def test_register_or_reuse_output_uses_existing_ready_canonical_asset(self):
        first = self._pdf(self.root / "first.pdf", "FIRST")
        second = self._pdf(self.root / "second.pdf", "SECOND")
        task = self._pending_task()
        expected = workflow_output_root("canonical-seed") / "same.pdf"
        seed = merge_pdf_sources(
            [
                {"sequence": 1, "kind": "upload", "filename": first.name, **fingerprint_file(first)},
                {"sequence": 2, "kind": "upload", "filename": second.name, **fingerprint_file(second)},
            ],
            expected,
        )
        prior = PublicationTask(
            feature=FEATURE_NAME,
            status="completed",
            current_step="completed",
            progress=100,
            params={"feature": FEATURE_NAME},
            result={},
        )
        db.session.add(prior)
        db.session.flush()
        existing = ContentPipelineAsset(
            media_type="pdf",
            content_sha256=seed.sha256,
            canonical_task_id=str(prior.id),
            source_path=str(seed.path),
            filename=seed.path.name,
            source_mode="pdf_merge",
            status="cleaned",
            cleaned_path=str(seed.path),
            cleaned_sha256=seed.sha256,
            cleaned_bytes=seed.bytes,
        )
        db.session.add(existing)
        db.session.commit()

        result = pdf_merge_workflow._register_or_reuse_output(str(task.id), seed.path)

        self.assertTrue(result["reused_asset"])
        self.assertEqual(result["asset_id"], str(existing.id))
        self.assertEqual(result["asset_ref"], f"pipeline:{existing.id}")

    def test_process_job_fails_when_a_frozen_pipeline_asset_has_changed(self):
        asset = self._pipeline_asset("ORIGINAL")
        task = self._pending_task(asset=asset)
        Path(asset.cleaned_path).write_bytes(Path(asset.cleaned_path).read_bytes()[:-1] + b"0")

        self.assertFalse(self._run(str(task.id)))

        stored = self._fresh_task(task.id)
        self.assertEqual(stored.status, "failed")
        self.assertEqual(stored.current_step, "failed")
        self.assertIn("PDF 资产内容已变化", stored.error_message)

    def test_queue_guard_and_asset_catalog_register_the_pdf_merge_feature(self):
        celery_config = (BACKEND_DIR / "celery_config.py").read_text(encoding="utf-8")

        self.assertEqual(runtime_guard.DEFAULT_FEATURE_LIMITS[FEATURE_NAME], 1)
        self.assertEqual(runtime_guard.DEFAULT_FEATURE_BACKLOG_LIMITS[FEATURE_NAME], 5)
        self.assertIn("'tasks.pdf_merge_workflow'", celery_config)
        self.assertIn(
            "'tasks.pdf_merge_workflow.process_job': {'queue': 'content_pipeline'}",
            celery_config,
        )
        self.assertEqual(report_audio_api._content_pipeline_source_label("pdf_merge"), "PDF 合并")


if __name__ == "__main__":
    unittest.main()
