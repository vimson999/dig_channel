import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import fitz
from flask import Flask


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from api import pdf_merge_workflow as pdf_merge_api  # noqa: E402
from models import db  # noqa: E402
from models.content_pipeline_asset import ContentPipelineAsset  # noqa: E402
from models.task import PublicationTask  # noqa: E402
from services.pdf_merge_workflow import FEATURE_NAME, PdfMergeError, fingerprint_file  # noqa: E402
from tasks import pdf_merge_workflow as pdf_merge_task  # noqa: E402


class PdfMergeWorkflowApiTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.output_root = self.root / "content-pipeline-output"
        self.upload_root = self.root / "uploads"
        self.app = Flask(__name__)
        self.app.config.update(
            SECRET_KEY="pdf-merge-test",
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

    def _pdf_bytes(self, text):
        document = fitz.open()
        page = document.new_page()
        page.insert_text((72, 72), text)
        payload = document.tobytes()
        document.close()
        return payload

    def _pipeline_asset(self, text="EXISTING ASSET"):
        parent = PublicationTask(
            feature="content_pdf_clean",
            status="completed",
            current_step="completed",
            progress=100,
            params={"feature": "content_pdf_clean"},
            result={},
        )
        db.session.add(parent)
        db.session.flush()
        source = self.root / f"source-{parent.id}.pdf"
        source.write_bytes(self._pdf_bytes(text))
        cleaned = self.output_root / "seed" / f"cleaned-{parent.id}.pdf"
        cleaned.parent.mkdir(parents=True, exist_ok=True)
        cleaned.write_bytes(source.read_bytes())
        fingerprint = fingerprint_file(cleaned)
        asset = ContentPipelineAsset(
            media_type="pdf",
            content_sha256=fingerprint["sha256"],
            canonical_task_id=str(parent.id),
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

    def _submission_result(self):
        return {
            "async_result": SimpleNamespace(id="celery-pdf-merge-1"),
            "admission": {"allowed": True, "reason": "ok"},
            "deferred": False,
            "deferred_state": None,
        }

    def _call_create(self, *, current_user, items, files):
        data = {"items": json.dumps(items), "title": "八月 PDF 合集"}
        if files:
            data["files"] = files
        with self.app.test_request_context(
            "/api/pdf-merge-workflows/jobs",
            method="POST",
            data=data,
            content_type="multipart/form-data",
        ):
            return pdf_merge_api.create_job.__wrapped__(current_user)

    def _new_task(self, *, user_id="owner", status="pending", result=None):
        task = PublicationTask(
            feature=FEATURE_NAME,
            status=status,
            current_step="queued" if status == "pending" else status,
            progress=0,
            user_id=user_id,
            params={
                "feature": FEATURE_NAME,
                "user_id": user_id,
                FEATURE_NAME: {
                    "title": "历史 PDF 合集",
                    "sources": [
                        {"sequence": 1, "kind": "upload", "filename": "one.pdf", "path": "/tmp/one.pdf", "bytes": 1, "sha256": "a" * 64},
                        {"sequence": 2, "kind": "upload", "filename": "two.pdf", "path": "/tmp/two.pdf", "bytes": 1, "sha256": "b" * 64},
                    ],
                },
            },
            result=result or {"logs": []},
        )
        db.session.add(task)
        db.session.commit()
        return task

    def test_create_job_freezes_mixed_source_order_without_exposing_local_paths(self):
        existing = self._pipeline_asset()
        current_user = SimpleNamespace(id="owner")
        with patch.object(pdf_merge_api, "maybe_guarded_apply_async", return_value=self._submission_result()) as enqueue:
            response, status = self._call_create(
                current_user=current_user,
                items=[
                    {"kind": "pipeline_asset", "asset_ref": f"pipeline:{existing.id}"},
                    {"kind": "upload", "upload_index": 0},
                ],
                files=[(io.BytesIO(self._pdf_bytes("LOCAL SECOND")), "本地-第二份.pdf")],
            )

        self.assertEqual(status, 202)
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertEqual(
            [item["kind"] for item in payload["data"]["sources"]],
            ["pipeline_asset", "upload"],
        )
        self.assertEqual(payload["data"]["sources"][1]["filename"], "本地-第二份.pdf")
        self.assertNotIn(str(self.upload_root), json.dumps(payload, ensure_ascii=False))
        enqueue.assert_called_once()

        task = db.session.get(PublicationTask, payload["data"]["job_id"])
        sources = task.params[FEATURE_NAME]["sources"]
        self.assertEqual([source["kind"] for source in sources], ["pipeline_asset", "upload"])
        self.assertEqual(sources[0]["asset_ref"], f"pipeline:{existing.id}")
        self.assertEqual(task.task_title, "八月 PDF 合集")
        self.assertTrue(Path(sources[1]["path"]).is_file())
        self.assertEqual(
            Path(sources[1]["path"]).parent,
            (self.upload_root / str(task.id)).resolve(),
        )
        self.assertEqual(task.celery_task_id, "celery-pdf-merge-1")

    def test_oversized_upload_is_rejected_before_file_storage_save(self):
        class SaveForbiddenUpload:
            def __init__(self, filename, payload):
                self.filename = filename
                self.stream = io.BytesIO(payload)

            def save(self, destination):
                raise AssertionError(f"unexpected unbounded save to {destination}")

        uploads = [
            SaveForbiddenUpload("oversized.pdf", b"x" * (1024 * 1024 + 1)),
            SaveForbiddenUpload("second.pdf", self._pdf_bytes("SECOND")),
        ]
        items = [
            {"kind": "upload", "upload_index": 0},
            {"kind": "upload", "upload_index": 1},
        ]
        with patch.dict(
            os.environ,
            {
                "PDF_MERGE_WORKFLOW_MAX_SINGLE_BYTES": str(1024 * 1024),
                "PDF_MERGE_WORKFLOW_MAX_TOTAL_BYTES": str(2 * 1024 * 1024),
            },
            clear=False,
        ):
            with self.assertRaisesRegex(PdfMergeError, "单个 PDF 超过大小限制"):
                pdf_merge_api._freeze_sources("oversized-upload", items, uploads)

        self.assertFalse((self.upload_root / "oversized-upload" / "input-0001.pdf").exists())

    def test_owner_scoping_cancel_and_retry_keep_the_frozen_sources(self):
        task = self._new_task()
        owner = SimpleNamespace(id="owner")
        stranger = SimpleNamespace(id="stranger")

        with self.app.test_request_context(f"/api/pdf-merge-workflows/jobs/{task.id}"):
            forbidden, forbidden_status = pdf_merge_api.get_job.__wrapped__(stranger, str(task.id))
        self.assertEqual(forbidden_status, 404)
        self.assertFalse(forbidden.get_json()["success"])

        with self.app.test_request_context(f"/api/pdf-merge-workflows/jobs/{task.id}/cancel", method="POST"):
            canceled = pdf_merge_api.cancel_job.__wrapped__(owner, str(task.id))
        self.assertEqual(canceled.status_code, 200)
        self.assertEqual(db.session.get(PublicationTask, task.id).status, "canceled")

        frozen_sources = db.session.get(PublicationTask, task.id).params[FEATURE_NAME]["sources"]
        with patch.object(pdf_merge_api, "maybe_guarded_apply_async", return_value=self._submission_result()) as enqueue:
            with self.app.test_request_context(f"/api/pdf-merge-workflows/jobs/{task.id}/retry", method="POST"):
                retried, status = pdf_merge_api.retry_job.__wrapped__(owner, str(task.id))
        self.assertEqual(status, 202)
        self.assertTrue(retried.get_json()["success"])
        refreshed = db.session.get(PublicationTask, task.id)
        self.assertEqual(refreshed.status, "pending")
        self.assertEqual(refreshed.params[FEATURE_NAME]["sources"], frozen_sources)
        enqueue.assert_called_once()

    def test_download_only_serves_completed_outputs_to_the_owner(self):
        asset = self._pipeline_asset("MERGED OUTPUT")
        task = self._new_task(
            status="completed",
            result={
                "logs": [],
                "output": {
                    "asset_id": str(asset.id),
                    "asset_ref": f"pipeline:{asset.id}",
                    "filename": "merged-output.pdf",
                },
            },
        )
        owner = SimpleNamespace(id="owner")
        stranger = SimpleNamespace(id="stranger")

        with self.app.test_request_context(f"/api/pdf-merge-workflows/jobs/{task.id}/download"):
            forbidden, status = pdf_merge_api.download_job.__wrapped__(stranger, str(task.id))
        self.assertEqual(status, 404)
        self.assertFalse(forbidden.get_json()["success"])

        with self.app.test_request_context(f"/api/pdf-merge-workflows/jobs/{task.id}/download"):
            response = pdf_merge_api.download_job.__wrapped__(owner, str(task.id))
            response.direct_passthrough = False
            self.assertEqual(response.status_code, 200)
            with fitz.open(stream=response.get_data(), filetype="pdf") as document:
                self.assertIn("MERGED OUTPUT", document[0].get_text())

    def test_api_submission_runs_to_a_reusable_asset_and_owner_download(self):
        existing = self._pipeline_asset("SYSTEM ASSET FIRST")
        owner = SimpleNamespace(id="owner")
        with patch.object(pdf_merge_api, "maybe_guarded_apply_async", return_value=self._submission_result()):
            response, status = self._call_create(
                current_user=owner,
                items=[
                    {"kind": "pipeline_asset", "asset_ref": f"pipeline:{existing.id}"},
                    {"kind": "upload", "upload_index": 0},
                ],
                files=[(io.BytesIO(self._pdf_bytes("LOCAL UPLOAD SECOND")), "local.pdf")],
            )
        self.assertEqual(status, 202)
        task_id = response.get_json()["data"]["job_id"]

        with patch.object(pdf_merge_task, "_create_pdf_merge_app", return_value=self.app), \
             patch.object(pdf_merge_task, "_notify", return_value={"queued": False}):
            self.assertTrue(pdf_merge_task._run_pdf_merge_job(task_id))

        db.session.expire_all()
        task = db.session.get(PublicationTask, task_id)
        self.assertEqual(task.status, "completed")
        output = task.result["output"]
        output_asset = db.session.get(ContentPipelineAsset, output["asset_id"])
        self.assertEqual(output_asset.status, "cleaned")
        self.assertEqual(output_asset.source_mode, "pdf_merge")
        with fitz.open(output_asset.cleaned_path) as merged:
            self.assertIn("SYSTEM ASSET FIRST", merged[0].get_text())
            self.assertIn("LOCAL UPLOAD SECOND", merged[1].get_text())

        with self.app.test_request_context(f"/api/pdf-merge-workflows/jobs/{task_id}/download"):
            download = pdf_merge_api.download_job.__wrapped__(owner, task_id)
            download.direct_passthrough = False
            self.assertEqual(download.status_code, 200)
            with fitz.open(stream=download.get_data(), filetype="pdf") as merged:
                self.assertEqual(merged.page_count, 2)


if __name__ == "__main__":
    unittest.main()
