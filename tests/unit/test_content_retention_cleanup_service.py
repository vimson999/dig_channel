import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.content_pipeline.retention_cleanup_service import (  # noqa: E402
    ContentPipelineRetentionCleanupService,
    ContentRetentionPolicy,
    _default_pytest_dir,
    _record_is_within_protection_window,
)


class ContentRetentionCleanupServiceTest(unittest.TestCase):
    def test_default_pytest_dir_supports_shallow_container_root(self):
        self.assertEqual(_default_pytest_dir(Path("/app")), Path("/app/pytest"))

    def test_preview_collects_old_content_pipeline_large_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            batch = root / "runtime" / "content_pipeline" / "batches" / "old-batch"
            file_path = batch / "pdf_cleaned" / "report.pdf"
            file_path.parent.mkdir(parents=True)
            file_path.write_bytes(b"x" * 128)
            (batch / "manifest.json").write_text("{}", encoding="utf-8")
            _touch_tree(batch, days_old=5)

            service = _service(root)
            service._protected_paths = lambda *_, **__: set()
            result = service.run(
                policy=ContentRetentionPolicy(
                    content_pipeline_batch_days=2,
                    include_sync_raw=False,
                    include_manual_publish=False,
                    include_pytest_batches=False,
                ),
                dry_run=True,
            )

            self.assertEqual(result["candidate_count"], 1)
            self.assertTrue(batch.exists())
            self.assertEqual(result["candidates"][0]["category"], "content_pipeline_batch_file")
            self.assertEqual(result["candidates"][0]["kind"], "file")
            self.assertEqual(result["candidates"][0]["path"], str(file_path.resolve()))

    def test_cleanup_deletes_only_expired_content_pipeline_large_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            batches_root = root / "runtime" / "content_pipeline" / "batches"
            old_batch = batches_root / "old-batch"
            new_batch = batches_root / "new-batch"
            (old_batch / "out").mkdir(parents=True)
            (new_batch / "out").mkdir(parents=True)
            old_pdf = old_batch / "out" / "old.pdf"
            old_manifest = old_batch / "manifest.json"
            new_pdf = new_batch / "out" / "new.pdf"
            old_pdf.write_bytes(b"old")
            old_manifest.write_text("keep", encoding="utf-8")
            new_pdf.write_bytes(b"new")
            _touch_tree(old_batch, days_old=5)

            service = _service(root)
            service._protected_paths = lambda *_, **__: set()
            result = service.run(
                policy=ContentRetentionPolicy(
                    content_pipeline_batch_days=2,
                    include_sync_raw=False,
                    include_manual_publish=False,
                    include_pytest_batches=False,
                ),
                dry_run=False,
            )

            self.assertEqual(result["deleted_count"], 1)
            self.assertFalse(old_pdf.exists())
            self.assertTrue(old_manifest.exists())
            self.assertTrue(old_batch.exists())
            self.assertTrue(new_batch.exists())
            self.assertTrue(new_pdf.exists())

    def test_content_pipeline_large_file_expiry_uses_file_mtime_not_parent_directory_mtime(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            batch = root / "runtime" / "content_pipeline" / "batches" / "touched-batch"
            file_path = batch / "audio_cleaned" / "files" / "old.mp3"
            file_path.parent.mkdir(parents=True)
            file_path.write_bytes(b"old-audio")
            old_timestamp = time.time() - 8 * 86400
            os.utime(file_path, (old_timestamp, old_timestamp))
            now = time.time()
            os.utime(file_path.parent, (now, now))
            os.utime(batch, (now, now))

            service = _service(root)
            service._protected_paths = lambda *_, **__: set()
            result = service.run(
                policy=ContentRetentionPolicy(
                    content_pipeline_batch_days=3,
                    include_sync_raw=False,
                    include_manual_publish=False,
                    include_pytest_batches=False,
                ),
                dry_run=True,
            )

            self.assertEqual(result["candidate_count"], 1)
            self.assertEqual(result["candidates"][0]["path"], str(file_path.resolve()))

    def test_sync_raw_cleanup_deletes_only_large_attachment_suffixes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            files_dir = root / "data" / "zsxq" / "group" / "tab" / "files"
            files_dir.mkdir(parents=True)
            pdf = files_dir / "report.pdf"
            note = files_dir / "manifest.txt"
            pdf.write_bytes(b"pdf")
            note.write_text("keep", encoding="utf-8")
            _touch_tree(files_dir, days_old=5)

            service = _service(root)
            service._protected_paths = lambda *_, **__: set()
            result = service.run(
                policy=ContentRetentionPolicy(
                    sync_raw_days=2,
                    include_content_pipeline_batches=False,
                    include_manual_publish=False,
                    include_pytest_batches=False,
                ),
                dry_run=False,
            )

            self.assertEqual(result["deleted_count"], 1)
            self.assertFalse(pdf.exists())
            self.assertTrue(note.exists())
            self.assertTrue(files_dir.exists())

    def test_preview_collects_only_expired_processed_mp4_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            processed = root / "data" / "processed" / "mp3_video_workflow" / "20260701"
            processed.mkdir(parents=True)
            old_mp4 = processed / "old.mp4"
            new_mp4 = processed / "new.mp4"
            old_wav = processed / "keep.wav"
            old_mp4.write_bytes(b"old-video")
            new_mp4.write_bytes(b"new-video")
            old_wav.write_bytes(b"keep-audio")
            _touch_tree(processed, days_old=5)
            now = time.time()
            os.utime(new_mp4, (now, now))

            service = _service(root)
            service._protected_paths = lambda *_, **__: set()
            result = service.run(
                policy=ContentRetentionPolicy(
                    processed_video_days=2,
                    include_content_pipeline_batches=False,
                    include_manual_publish=False,
                    include_pytest_batches=False,
                    include_sync_raw=False,
                ),
                dry_run=True,
            )

            self.assertEqual(result["candidate_count"], 1)
            self.assertEqual(result["candidates"][0]["category"], "processed_video")
            self.assertEqual(result["candidates"][0]["path"], str(old_mp4.resolve()))
            self.assertNotIn(str(old_wav.resolve()), {item["path"] for item in result["candidates"]})

    def test_cleanup_deletes_expired_processed_mp4_but_keeps_other_artifacts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            processed = root / "data" / "processed" / "report_audio_workflow" / "20260701"
            processed.mkdir(parents=True)
            old_mp4 = processed / "old.mp4"
            old_json = processed / "manifest.json"
            old_mp4.write_bytes(b"old-video")
            old_json.write_text("keep", encoding="utf-8")
            _touch_tree(processed, days_old=5)

            service = _service(root)
            service._protected_paths = lambda *_, **__: set()
            result = service.run(
                policy=ContentRetentionPolicy(
                    processed_video_days=2,
                    include_content_pipeline_batches=False,
                    include_manual_publish=False,
                    include_pytest_batches=False,
                    include_sync_raw=False,
                ),
                dry_run=False,
            )

            self.assertEqual(result["deleted_count"], 1)
            self.assertFalse(old_mp4.exists())
            self.assertTrue(old_json.exists())

    def test_protected_processed_mp4_is_not_collected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            processed = root / "data" / "processed" / "mp3_video_workflow" / "20260701"
            processed.mkdir(parents=True)
            old_mp4 = processed / "still-running.mp4"
            old_mp4.write_bytes(b"video")
            _touch_tree(processed, days_old=5)

            service = _service(root)
            service._protected_paths = lambda *_, **__: {old_mp4.resolve()}
            result = service.run(
                policy=ContentRetentionPolicy(
                    processed_video_days=2,
                    include_content_pipeline_batches=False,
                    include_manual_publish=False,
                    include_pytest_batches=False,
                    include_sync_raw=False,
                ),
                dry_run=True,
            )

            self.assertEqual(result["candidate_count"], 0)
            self.assertTrue(old_mp4.exists())

    def test_protected_directory_is_not_collected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            batch = root / "runtime" / "content_pipeline" / "batches" / "failed-batch"
            output = batch / "pdf_cleaned"
            output.mkdir(parents=True)
            (output / "report.pdf").write_bytes(b"x" * 128)
            _touch_tree(batch, days_old=5)

            service = _service(root)
            service._protected_paths = lambda *_, **__: {output.resolve()}
            result = service.run(
                policy=ContentRetentionPolicy(
                    content_pipeline_batch_days=2,
                    include_sync_raw=False,
                    include_manual_publish=False,
                    include_pytest_batches=False,
                ),
                dry_run=True,
            )

            self.assertEqual(result["candidate_count"], 0)
            self.assertTrue(batch.exists())

    def test_stale_unfinished_reference_is_not_protected_forever(self):
        recent = type("Record", (), {"updated_at": datetime.utcnow() - timedelta(days=1)})()
        stale = type("Record", (), {"updated_at": datetime.utcnow() - timedelta(days=5)})()
        now = time.time()

        self.assertTrue(_record_is_within_protection_window(recent, now, 3))
        self.assertFalse(_record_is_within_protection_window(stale, now, 3))

    def test_policy_defaults_include_unfinished_reference_protection_window(self):
        policy = ContentRetentionPolicy.from_payload({})

        self.assertEqual(policy.unfinished_reference_protect_days, 3)
        self.assertEqual(policy.processed_video_days, 3)
        self.assertTrue(policy.include_processed_videos)
        self.assertEqual(policy.to_dict()["unfinished_reference_protect_days"], 3)
        self.assertEqual(policy.to_dict()["processed_video_days"], 3)


def _service(root: Path) -> ContentPipelineRetentionCleanupService:
    return ContentPipelineRetentionCleanupService(
        project_root=root,
        content_pipeline_root=root / "runtime" / "content_pipeline",
        zsxq_data_dir=root / "data" / "zsxq",
        pytest_project_dir=root / "external" / "pytest",
    )


def _touch_tree(path: Path, *, days_old: int):
    timestamp = time.time() - days_old * 86400
    for child in sorted(path.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        os.utime(child, (timestamp, timestamp))
    os.utime(path, (timestamp, timestamp))


if __name__ == "__main__":
    unittest.main()
