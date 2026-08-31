import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import fitz


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.pdf_merge_workflow import (  # noqa: E402
    PdfMergeError,
    fingerprint_file,
    merge_pdf_sources,
    normalize_output_filename,
    validate_pdf_source,
    workflow_output_root,
)


class PdfMergeWorkflowServiceTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.output_root = self.root / "content-pipeline-output"
        self.env_patch = patch.dict(
            os.environ,
            {"CONTENT_PIPELINE_OUTPUT_ROOT": str(self.output_root)},
            clear=False,
        )
        self.env_patch.start()

    def tearDown(self):
        self.env_patch.stop()
        self.temp_dir.cleanup()

    def _pdf(self, name, text):
        path = self.root / name
        document = fitz.open()
        page = document.new_page()
        page.insert_text((72, 72), text)
        document.save(path)
        document.close()
        return path

    def _source(self, path, sequence):
        fingerprint = fingerprint_file(path)
        return {
            "sequence": sequence,
            "kind": "upload",
            "filename": path.name,
            "path": fingerprint["path"],
            "sha256": fingerprint["sha256"],
            "bytes": fingerprint["bytes"],
        }

    def test_merge_pdf_sources_preserves_the_frozen_source_order(self):
        first = self._pdf("first.pdf", "FIRST PAGE")
        second = self._pdf("second.pdf", "SECOND PAGE")
        output = workflow_output_root("merge-order") / "merged.pdf"
        progress = []

        result = merge_pdf_sources(
            [self._source(first, 1), self._source(second, 2)],
            output,
            progress_callback=lambda completed, total, pages: progress.append(
                (completed, total, pages)
            ),
        )

        self.assertEqual(result.path, output.resolve())
        self.assertEqual(result.page_count, 2)
        self.assertGreater(result.bytes, 0)
        self.assertEqual(progress, [(1, 2, 1), (2, 2, 2)])
        with fitz.open(result.path) as merged:
            self.assertIn("FIRST PAGE", merged[0].get_text())
            self.assertIn("SECOND PAGE", merged[1].get_text())

    def test_validate_pdf_source_rejects_changed_content_before_opening_file(self):
        source_path = self._pdf("source.pdf", "ORIGINAL")
        source = self._source(source_path, 1)
        source_path.write_bytes(source_path.read_bytes()[:-1] + b"0")

        with self.assertRaisesRegex(PdfMergeError, "文件内容已变化"):
            validate_pdf_source(source)

    def test_merge_pdf_sources_rejects_an_encrypted_input(self):
        first = self._pdf("first.pdf", "FIRST")
        encrypted = self.root / "encrypted.pdf"
        document = fitz.open()
        document.new_page().insert_text((72, 72), "SECRET")
        document.save(
            encrypted,
            encryption=fitz.PDF_ENCRYPT_AES_256,
            owner_pw="owner-password",
            user_pw="user-password",
        )
        document.close()

        with self.assertRaisesRegex(PdfMergeError, "加密"):
            merge_pdf_sources(
                [self._source(first, 1), self._source(encrypted, 2)],
                workflow_output_root("merge-encrypted") / "merged.pdf",
            )

    def test_merge_pdf_sources_requires_at_least_two_sources(self):
        only = self._pdf("only.pdf", "ONLY")

        with self.assertRaisesRegex(PdfMergeError, "至少需要 2 个 PDF"):
            merge_pdf_sources(
                [self._source(only, 1)],
                workflow_output_root("merge-single") / "merged.pdf",
            )

    def test_normalize_output_filename_removes_path_components_and_adds_extension(self):
        self.assertEqual(normalize_output_filename("../研报合集"), "研报合集.pdf")
        self.assertRegex(
            normalize_output_filename(""),
            re.compile(r"^合并PDF-\d{8}-\d{6}\.pdf$"),
        )


if __name__ == "__main__":
    unittest.main()
