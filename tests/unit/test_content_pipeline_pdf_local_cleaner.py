import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.content_pipeline.pdf_cleaner_adapter import PdfCleanOptions, PdfCleanerAdapter  # noqa: E402


class LocalPdfCleanerAdapterTest(unittest.TestCase):
    def test_local_engine_writes_cleaned_output_without_external_service(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.pdf"
            source.write_bytes(b"%PDF-1.4\nsample")

            def fake_remove_watermark(_input_path, output_path, *args, **kwargs):
                Path(output_path).write_bytes(b"%PDF-1.4\ncleaned")
                return True, "Success"

            adapter = PdfCleanerAdapter(
                base_url="http://127.0.0.1:1",
                output_root=root / "batches",
            )

            with patch(
                "services.watermark.pdf_processor.PDFProcessor.remove_watermark",
                side_effect=fake_remove_watermark,
            ):
                result = adapter.run_batch(
                    batch_id="local-clean",
                    files=[source],
                    options=PdfCleanOptions(engine="local", prefix="clean_"),
                )

            self.assertEqual(result["engine"], "local")
            self.assertEqual(result["success_count"], 1)
            self.assertEqual(result["failed_count"], 0)
            cleaned_path = Path(result["cleaned_files"][0]["path"])
            self.assertTrue(cleaned_path.exists())
            self.assertEqual(cleaned_path.name, "clean_source.pdf")
            self.assertTrue((root / "batches" / "local-clean" / "manifest.json").exists())

    def test_local_engine_health_check_does_not_require_external_service(self):
        adapter = PdfCleanerAdapter(base_url="http://127.0.0.1:1")

        result = adapter.check_service(engine="local")

        self.assertTrue(result["success"])
        self.assertEqual(result["engine"], "local")

    def test_migrated_local_processor_cleans_real_pdf(self):
        import fitz  # type: ignore

        from services.watermark.pdf_processor import PDFProcessor

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.pdf"
            target = root / "cleaned.pdf"

            doc = fitz.open()
            page = doc.new_page(width=300, height=420)
            page.insert_text((32, 64), "Research Report", fontsize=14, color=(0, 0, 0))
            page.insert_text((60, 230), "知识星球", fontsize=28, color=(0.82, 0.82, 0.82))
            doc.save(source)
            doc.close()

            success, message = PDFProcessor(threshold=245).remove_watermark(str(source), str(target))

            self.assertTrue(success, message)
            self.assertTrue(target.exists())
            cleaned_doc = fitz.open(target)
            try:
                self.assertEqual(len(cleaned_doc), 1)
            finally:
                cleaned_doc.close()


if __name__ == "__main__":
    unittest.main()
