import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from models import ContentPipelineAsset, db  # noqa: E402
from models.task import PublicationTask  # noqa: E402
from services.content_pipeline import audio_clean_task_service, pdf_clean_task_service  # noqa: E402
from services.content_pipeline.dedupe_service import mark_task_assets  # noqa: E402


class ContentPipelineDedupeTest(unittest.TestCase):
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
        self.notification_patch = patch(
            "services.content_pipeline.notification_service.notify_content_pipeline_event",
            return_value={"queued": False, "reason": "test"},
        )
        self.notification_patch.start()

    def tearDown(self):
        db.session.remove()
        self.notification_patch.stop()
        db.drop_all()
        self.ctx.pop()
        self.temp_dir.cleanup()

    def _path(self, name, content):
        path = Path(self.temp_dir.name) / name
        path.write_bytes(content)
        return path

    def _guarded(self, module):
        return patch.object(
            module,
            "maybe_guarded_apply_async",
            return_value={
                "async_result": SimpleNamespace(id="celery-1"),
                "admission": {"allowed": True, "reason": "ok"},
                "deferred": False,
                "deferred_state": None,
            },
        )

    def test_same_batch_duplicate_content_is_not_sent_to_cleaner(self):
        first = self._path("first.pdf", b"same-pdf")
        second = self._path("renamed.pdf", b"same-pdf")

        with self._guarded(pdf_clean_task_service) as guarded:
            payload = pdf_clean_task_service.submit_pdf_clean_task(
                title="PDF",
                files=[str(first), str(second)],
                metadata={"source_mode": "manual_download"},
            )

        self.assertEqual(guarded.call_count, 1)
        self.assertEqual(
            payload["params"]["content_pipeline"]["pdf_clean"]["files"],
            [str(first.resolve())],
        )
        self.assertEqual(payload["result"]["dedupe"]["unique_count"], 1)
        self.assertEqual(payload["result"]["dedupe"]["duplicate_count"], 1)
        self.assertEqual(payload["result"]["dedupe"]["duplicates"][0]["duplicate_of"], str(first.resolve()))

    def test_duplicate_from_another_upstream_is_not_enqueued_again(self):
        source = self._path("recording.mp3", b"same-audio")

        with self._guarded(audio_clean_task_service):
            first = audio_clean_task_service.submit_audio_clean_task(
                title="侧边栏音频",
                files=[str(source)],
                metadata={"source_mode": "sidebar"},
            )

        with self._guarded(audio_clean_task_service) as guarded:
            second = audio_clean_task_service.submit_audio_clean_task(
                title="手动音频",
                files=[str(source)],
                metadata={"source_mode": "manual_download"},
            )

        self.assertEqual(guarded.call_count, 0)
        self.assertFalse(second.get("celery_task_id"))
        self.assertEqual(second["result"]["dedupe"]["unique_count"], 0)
        self.assertEqual(second["result"]["dedupe"]["duplicate_count"], 1)
        self.assertEqual(
            second["result"]["dedupe"]["duplicates"][0]["canonical_task_id"],
            first["id"],
        )
        persisted = db.session.get(PublicationTask, second["id"])
        self.assertEqual(persisted.status, "completed")
        self.assertEqual(persisted.current_step, "dedupe_skipped")

    def test_mixed_submission_keeps_unique_files(self):
        duplicate = self._path("already-seen.mp3", b"already-seen")
        new_file = self._path("new.mp3", b"new-content")

        with self._guarded(audio_clean_task_service):
            audio_clean_task_service.submit_audio_clean_task(
                title="首批",
                files=[str(duplicate)],
                metadata={"source_mode": "scheduled_api"},
            )

        with self._guarded(audio_clean_task_service) as guarded:
            payload = audio_clean_task_service.submit_audio_clean_task(
                title="混合批次",
                files=[str(duplicate), str(new_file)],
                metadata={"source_mode": "manual_download"},
            )

        self.assertEqual(guarded.call_count, 1)
        self.assertEqual(
            payload["params"]["content_pipeline"]["audio_clean"]["files"],
            [str(new_file.resolve())],
        )
        self.assertEqual(payload["result"]["dedupe"]["received_count"], 2)
        self.assertEqual(payload["result"]["dedupe"]["unique_count"], 1)
        self.assertEqual(payload["result"]["dedupe"]["duplicate_count"], 1)

    def test_retry_start_does_not_downgrade_a_cleaned_canonical_asset(self):
        task_id = "retry-preserves-cleaned"
        cleaned = ContentPipelineAsset(
            media_type="pdf",
            content_sha256="1" * 64,
            canonical_task_id=task_id,
            source_path=str(self._path("cleaned.pdf", b"cleaned")),
            filename="cleaned.pdf",
            status="cleaned",
        )
        reserved = ContentPipelineAsset(
            media_type="pdf",
            content_sha256="2" * 64,
            canonical_task_id=task_id,
            source_path=str(self._path("reserved.pdf", b"reserved")),
            filename="reserved.pdf",
            status="reserved",
        )
        db.session.add_all([cleaned, reserved])
        db.session.commit()

        updated = mark_task_assets(task_id, status="cleaning")
        db.session.refresh(cleaned)
        db.session.refresh(reserved)

        self.assertEqual(updated, 1)
        self.assertEqual(cleaned.status, "cleaned")
        self.assertEqual(reserved.status, "cleaning")

        updated = mark_task_assets(task_id, status="failed")
        db.session.refresh(cleaned)
        db.session.refresh(reserved)

        self.assertEqual(updated, 1)
        self.assertEqual(cleaned.status, "cleaned")
        self.assertEqual(reserved.status, "failed")

if __name__ == "__main__":
    unittest.main()
