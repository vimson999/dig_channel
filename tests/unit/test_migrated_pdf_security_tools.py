import random
import sys
import tempfile
import unittest
from pathlib import Path

import fitz  # type: ignore


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.watermark.pdf_analyzer import PDFAnalyzer  # noqa: E402
from services.watermark.pdf_forensics import PDFForensics  # noqa: E402
from services.watermark.pdf_protector import PDFProtector  # noqa: E402
from services.watermark.pdf_splitter import PDFSplitter  # noqa: E402


class MigratedPdfSecurityToolsTest(unittest.TestCase):
    def test_protector_adds_custom_watermark_and_restricted_encryption(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.pdf"
            protected = root / "protected.pdf"
            self._write_sample_pdf(source, pages=2)

            random.seed(2026)
            success, result = PDFProtector.add_watermark_and_protect(
                str(source),
                str(protected),
                watermark_text="请您不要外传",
                owner_pw="owner-test",
            )

            self.assertTrue(success, result)
            self.assertTrue(protected.exists())

            report = PDFAnalyzer.analyze(str(protected))
            self.assertEqual(report["status"], "success")
            self.assertTrue(report["security"]["is_encrypted"])
            self.assertFalse(report["security"]["permissions_decoded"]["copy_contents"])
            self.assertFalse(report["security"]["permissions_decoded"]["print_high_res"])

    def test_forensics_detects_invisible_fingerprint_text(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "hidden.pdf"
            doc = fitz.open()
            page = doc.new_page(width=300, height=400)
            page.insert_text((40, 80), "Visible content", fontsize=12, color=(0, 0, 0))
            page.insert_text((40, 120), "TRACE_FINGERPRINT", fontsize=0.5, color=(1, 1, 1))
            doc.save(path)
            doc.close()

            report = PDFForensics.deep_scan(str(path))

            hidden_texts = [
                finding["text"]
                for page_report in report["hidden_text_found"]
                for finding in page_report["findings"]
            ]
            self.assertIn("TRACE_FINGERPRINT", hidden_texts)

    def test_analyzer_recommends_text_clean_for_hidden_fingerprint(self):
        report = {
            "forensics": {
                "hidden_text_found": [
                    {"findings": [{"text": "TRACE_A"}, {"text": "TRACE_A"}, {"text": "TRACE_B"}]}
                ]
            },
            "security": {},
        }

        strategy = PDFAnalyzer._generate_strategy(report)

        self.assertEqual(strategy["recommended_mode"], "text_clean")
        self.assertIn("TRACE_A", strategy["text_to_remove"])
        self.assertIn("TRACE_B", strategy["text_to_remove"])

    def test_splitter_splits_ranges_without_losing_pages(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.pdf"
            output_dir = root / "parts"
            self._write_sample_pdf(source, pages=5)

            outputs = PDFSplitter.split_by_range(str(source), str(output_dir), [(1, 2), (3, 5)])

            self.assertEqual(len(outputs), 2)
            first = fitz.open(output_dir / outputs[0])
            second = fitz.open(output_dir / outputs[1])
            try:
                self.assertEqual(len(first), 2)
                self.assertEqual(len(second), 3)
            finally:
                first.close()
                second.close()

    def _write_sample_pdf(self, path: Path, pages: int = 1) -> None:
        doc = fitz.open()
        for index in range(pages):
            page = doc.new_page(width=300, height=400)
            page.insert_text((40, 80), f"Page {index + 1}", fontsize=14, color=(0, 0, 0))
        doc.save(path)
        doc.close()


if __name__ == "__main__":
    unittest.main()
