import os
import sys
import tempfile
import time
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.content_pipeline.retention_cleanup_service import (  # noqa: E402
    ContentPipelineRetentionCleanupService,
    ContentRetentionPolicy,
)


class ContentRetentionCleanupServiceTest(unittest.TestCase):
    def test_preview_does_not_delete_old_content_pipeline_batch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            batch = root / "runtime" / "content_pipeline" / "batches" / "old-batch"
            file_path = batch / "pdf_cleaned" / "report.pdf"
            file_path.parent.mkdir(parents=True)
            file_path.write_bytes(b"x" * 128)
            _touch_tree(batch, days_old=5)

            service = _service(root)
            service._protected_paths = lambda: set()
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
            self.assertEqual(result["candidates"][0]["category"], "content_pipeline_batch")

    def test_cleanup_deletes_only_expired_batch_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            batches_root = root / "runtime" / "content_pipeline" / "batches"
            old_batch = batches_root / "old-batch"
            new_batch = batches_root / "new-batch"
            (old_batch / "out").mkdir(parents=True)
            (new_batch / "out").mkdir(parents=True)
            (old_batch / "out" / "old.pdf").write_bytes(b"old")
            (new_batch / "out" / "new.pdf").write_bytes(b"new")
            _touch_tree(old_batch, days_old=5)

            service = _service(root)
            service._protected_paths = lambda: set()
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
            self.assertFalse(old_batch.exists())
            self.assertTrue(new_batch.exists())

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
            service._protected_paths = lambda: set()
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

    def test_protected_directory_is_not_collected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            batch = root / "runtime" / "content_pipeline" / "batches" / "failed-batch"
            output = batch / "pdf_cleaned"
            output.mkdir(parents=True)
            (output / "report.pdf").write_bytes(b"x" * 128)
            _touch_tree(batch, days_old=5)

            service = _service(root)
            service._protected_paths = lambda: {output.resolve()}
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
