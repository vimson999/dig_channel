import sys
import tempfile
import unittest
from pathlib import Path

import fitz  # type: ignore
import numpy as np


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.watermark.pdf_processor import (  # noqa: E402
    CUSTOM_QR_FILENAME,
    PDFProcessor,
    _candidate_qr_boxes_from_component,
    _should_paste_custom_qr,
    apply_footer_red_watermark_clean,
)


class MigratedPdfProcessorTest(unittest.TestCase):
    def test_watermark_resources_are_available_in_current_project(self):
        resource_dir = BACKEND_DIR / "services" / "resources" / "watermarks"

        self.assertEqual(CUSTOM_QR_FILENAME, "my_wechat_qr.png")
        self.assertTrue((resource_dir / CUSTOM_QR_FILENAME).exists())
        self.assertTrue((resource_dir / "water-1.png").exists())
        self.assertTrue((resource_dir / "water-2.png").exists())
        self.assertTrue((resource_dir / "water-mark-3.png").exists())

    def test_rectangular_qr_component_generates_square_candidate(self):
        boxes = _candidate_qr_boxes_from_component(84, 2026, 166, 124, 85, 374)

        self.assertIn((84, 2026, 124, 124), boxes)

    def test_custom_qr_is_only_pasted_on_even_pages(self):
        self.assertFalse(_should_paste_custom_qr(1))
        self.assertTrue(_should_paste_custom_qr(2))
        self.assertFalse(_should_paste_custom_qr(3))
        self.assertTrue(_should_paste_custom_qr(4))

    def test_footer_red_clean_is_limited_to_footer_band(self):
        img = np.full((200, 300, 3), 255, dtype=np.uint8)
        img[30:40, 40:90] = [0, 0, 255]
        img[180:190, 120:220] = [0, 0, 255]

        cleaned = apply_footer_red_watermark_clean(img.copy())

        self.assertTrue(np.array_equal(cleaned[30:40, 40:90], img[30:40, 40:90]))
        self.assertTrue(np.all(cleaned[180:190, 120:220] == 255))

    def test_text_clean_processing_removes_searchable_watermark(self):
        watermark_text = "SECRET_WATERMARK_123"
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "watermark.pdf"
            target = root / "cleaned.pdf"

            doc = fitz.open()
            page = doc.new_page()
            page.insert_text((100, 100), "This is the main content.")
            page.insert_text((200, 200), watermark_text, fontsize=20, color=(0.8, 0.8, 0.8))
            doc.save(source)
            doc.close()

            success, message = PDFProcessor().remove_watermark(
                str(source),
                str(target),
                mode="text_clean",
                text_to_remove=watermark_text,
            )

            self.assertTrue(success, message)
            final_doc = fitz.open(target)
            try:
                self.assertEqual(final_doc[0].search_for(watermark_text), [])
            finally:
                final_doc.close()


if __name__ == "__main__":
    unittest.main()
