import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.zsxq_sync_service import zsxq_sync_service  # noqa: E402


class ZsxqSyncPdfCleanHandoffTest(unittest.TestCase):
    def test_downloaded_pdf_files_from_run_result_is_strict_and_deduped(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            pdf = tmp_path / "new.pdf"
            txt = tmp_path / "note.txt"
            pdf.write_bytes(b"%PDF-1.4\nnew")
            txt.write_text("not a pdf", encoding="utf-8")

            run = SimpleNamespace(result={
                "downloaded_pdf_files": [
                    {
                        "path": str(pdf),
                        "name": "new.pdf",
                        "topic_id": "topic-1",
                        "tab_id": "tab-1",
                        "attachment_id": "attachment-1",
                    },
                    {
                        "path": str(pdf),
                        "name": "duplicate.pdf",
                        "topic_id": "topic-1",
                        "tab_id": "tab-1",
                        "attachment_id": "attachment-1",
                    },
                    {"path": str(txt), "name": "note.txt"},
                    {"path": str(tmp_path / "missing.pdf"), "name": "missing.pdf"},
                ],
            })

            self.assertEqual(
                zsxq_sync_service._downloaded_pdf_files_from_run_result(run),
                [
                    {
                        "path": str(pdf.resolve()),
                        "name": "new.pdf",
                        "topic_id": "topic-1",
                        "tab_id": "tab-1",
                        "attachment_id": "attachment-1",
                    }
                ],
            )

    def test_downloaded_file_entry_requires_real_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            pdf = tmp_path / "new.pdf"
            pdf.write_bytes(b"%PDF-1.4\nnew")
            record = SimpleNamespace(
                name="new.pdf",
                topic_id="topic-1",
                tab_id="tab-1",
                id="attachment-1",
            )

            self.assertEqual(
                zsxq_sync_service._downloaded_file_entry(record, str(pdf)),
                {
                    "path": str(pdf.resolve()),
                    "name": "new.pdf",
                    "topic_id": "topic-1",
                    "tab_id": "tab-1",
                    "attachment_id": "attachment-1",
                },
            )
            self.assertIsNone(
                zsxq_sync_service._downloaded_file_entry(record, str(tmp_path / "missing.pdf"))
            )


if __name__ == "__main__":
    unittest.main()
