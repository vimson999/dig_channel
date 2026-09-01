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
    def test_default_engine_is_local_without_external_service(self):
        self.assertEqual(PdfCleanOptions().engine, "local")

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
                    batch_id="default-local-clean",
                    files=[source],
                )

            self.assertEqual(result["engine"], "local")
            self.assertEqual(result["success_count"], 1)

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
            self.assertEqual(result["files"][0]["source_path"], str(source.resolve()))
            self.assertEqual(result["files"][0]["output_path"], str(cleaned_path))
            self.assertTrue((root / "batches" / "local-clean" / "manifest.json").exists())

    def test_retry_reuses_completed_manifest_without_cleaning_again(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.pdf"
            source.write_bytes(b"%PDF-1.4\nsample")
            adapter = PdfCleanerAdapter(output_root=root / "batches")

            def fake_remove_watermark(_input_path, output_path, *args, **kwargs):
                Path(output_path).write_bytes(b"%PDF-1.4\ncleaned")
                return True, "Success"

            with patch(
                "services.watermark.pdf_processor.PDFProcessor.remove_watermark",
                side_effect=fake_remove_watermark,
            ):
                adapter.run_batch(
                    batch_id="retry-checkpoint",
                    files=[source],
                    options=PdfCleanOptions(engine="local"),
                )

            with patch(
                "services.watermark.pdf_processor.PDFProcessor.remove_watermark",
                side_effect=AssertionError("durable output must not be cleaned twice"),
            ) as cleaner:
                resumed = adapter.run_batch(
                    batch_id="retry-checkpoint",
                    files=[source],
                    options=PdfCleanOptions(engine="local"),
                    reset_workspace=False,
                )

            cleaner.assert_not_called()
            self.assertTrue(resumed["resumed_from_checkpoint"])
            self.assertEqual(resumed["success_count"], 1)

    def test_external_output_copy_keeps_source_binding_for_success_and_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_one = root / "downloads" / "nested" / "one.pdf"
            source_two = root / "downloads" / "two.pdf"
            source_one.parent.mkdir(parents=True)
            source_one.write_bytes(b"%PDF-raw-one")
            source_two.write_bytes(b"%PDF-raw-two")
            pytest_output = root / "pytest-output"
            cleaned_external = pytest_output / "nested" / "one.pdf"
            cleaned_external.parent.mkdir(parents=True)
            cleaned_external.write_bytes(b"%PDF-cleaned-one")
            local_output = root / "runtime" / "pdf_cleaned"
            adapter = PdfCleanerAdapter(output_root=root / "runtime")

            files = adapter._copy_outputs(
                pytest_output,
                local_output,
                {
                    "files": [
                        {
                            "name": "nested/one.pdf",
                            "status": "SUCCESS",
                            "output": "nested/one.pdf",
                            "message": "ok",
                        },
                        {
                            "name": "two.pdf",
                            "status": "FAILED",
                            "output": "",
                            "message": "bad source",
                        },
                    ],
                },
                staged_files=[
                    {
                        "source_path": str(source_one.resolve()),
                        "staged_relative_path": "nested/one.pdf",
                    },
                    {
                        "source_path": str(source_two.resolve()),
                        "staged_relative_path": "two.pdf",
                    },
                ],
            )

            self.assertEqual(len(files), 2)
            self.assertEqual(files[0]["source_path"], str(source_one.resolve()))
            self.assertEqual(files[0]["status"], "SUCCESS")
            self.assertTrue(Path(files[0]["output_path"]).is_file())
            self.assertEqual(files[1]["source_path"], str(source_two.resolve()))
            self.assertEqual(files[1]["status"], "FAILED")
            self.assertNotIn("output_path", files[1])

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
