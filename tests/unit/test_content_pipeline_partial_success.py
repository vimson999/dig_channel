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
from tasks import content_pipeline  # noqa: E402


class ContentPipelinePartialSuccessTest(unittest.TestCase):
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

    def _task_and_assets(self, media_type):
        source_root = self.root / "downloads"
        source_root.mkdir()
        sources = [source_root / f"{index}.{ 'pdf' if media_type == 'pdf' else 'mp3'}" for index in (1, 2)]
        for source in sources:
            source.write_bytes(b"raw")

        config_key = "pdf_clean" if media_type == "pdf" else "audio_clean"
        task = PublicationTask(
            feature=f"content_{media_type}_clean",
            status="pending",
            current_step="queued",
            progress=0,
            params={
                "feature": f"content_{media_type}_clean",
                "content_pipeline": {
                    config_key: {
                        "files": [str(source) for source in sources],
                        "source_root": str(source_root),
                        "cleaner": {"engine": "local"} if media_type == "pdf" else {},
                    },
                    "auto_publish": {"enabled": False},
                },
            },
        )
        db.session.add(task)
        db.session.flush()
        assets = []
        for index, source in enumerate(sources, start=1):
            asset = ContentPipelineAsset(
                media_type=media_type,
                content_sha256=f"{index:064x}",
                canonical_task_id=str(task.id),
                source_path=str(source.resolve()),
                filename=source.name,
                source_mode="manual_download",
                status="reserved",
            )
            db.session.add(asset)
            assets.append(asset)
        db.session.commit()
        return str(task.id), sources, assets

    def _partial_result(self, *, task_id, sources, media_type):
        extension = ".pdf" if media_type == "pdf" else ".mp3"
        output = self.output_root / task_id / f"{media_type}_cleaned" / f"1{extension}"
        output.parent.mkdir(parents=True)
        output.write_bytes(b"cleaned")
        return {
            "batch_id": task_id,
            "local_output_dir": str(output.parent),
            "files": [
                {
                    "source_path": str(sources[0]),
                    "status": "SUCCESS",
                    "output_path": str(output),
                    "path": str(output),
                },
                {
                    "source_path": str(sources[1]),
                    "status": "FAILED",
                    "path": str(output.parent / f"2{extension}"),
                },
            ],
            "cleaned_files": [
                {
                    "source_path": str(sources[0]),
                    "status": "SUCCESS",
                    "output_path": str(output),
                    "path": str(output),
                },
            ],
            "success_count": 1,
            "failed_count": 1,
        }

    def _assert_partial_success(self, *, task_id, assets):
        task = PublicationTask.query.filter(PublicationTask.id == task_id).one()
        db.session.refresh(assets[0])
        db.session.refresh(assets[1])
        self.assertEqual(task.status, "completed")
        self.assertEqual(task.current_step, "done")
        self.assertEqual(task.content_clean_success_count, 1)
        self.assertEqual(task.content_clean_failed_count, 1)
        self.assertEqual(assets[0].status, "cleaned")
        self.assertEqual(assets[1].status, "failed")
        self.assertEqual(len(task.result["cleaned_files"]), 1)

    def test_pdf_partial_clean_success_completes_and_keeps_failed_asset_individual(self):
        task_id, sources, assets = self._task_and_assets("pdf")
        result = self._partial_result(task_id=task_id, sources=sources, media_type="pdf")

        with patch.object(content_pipeline, "_create_content_pipeline_app", return_value=self.app), \
             patch.object(content_pipeline, "PdfCleanerAdapter") as adapter, \
             patch.object(content_pipeline, "_notify_content_pipeline_event", return_value={"queued": False}), \
             patch.object(content_pipeline, "_notify_workflow_run", return_value=True):
            adapter.return_value.run_batch.return_value = result
            ok = content_pipeline._run_pdf_clean_task_impl_guarded(task_id)

        self.assertTrue(ok)
        self._assert_partial_success(task_id=task_id, assets=assets)

    def test_audio_partial_clean_success_completes_and_keeps_failed_asset_individual(self):
        task_id, sources, assets = self._task_and_assets("audio")
        result = self._partial_result(task_id=task_id, sources=sources, media_type="audio")

        with patch.object(content_pipeline, "_create_content_pipeline_app", return_value=self.app), \
             patch.object(content_pipeline, "AudioCleanerAdapter") as adapter, \
             patch.object(content_pipeline, "_notify_content_pipeline_event", return_value={"queued": False}), \
             patch.object(content_pipeline, "_notify_workflow_run", return_value=True):
            adapter.return_value.run_batch.return_value = result
            ok = content_pipeline._run_audio_clean_task_impl_guarded(task_id)

        self.assertTrue(ok)
        self._assert_partial_success(task_id=task_id, assets=assets)


if __name__ == "__main__":
    unittest.main()
