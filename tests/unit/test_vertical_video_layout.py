import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from PIL import Image


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.thumbnail_service import ThumbnailService  # noqa: E402
from services.video_editor import VideoEditorService  # noqa: E402


class VerticalVideoLayoutTest(unittest.TestCase):
    def test_video_editor_uses_bundled_ffmpeg_with_ass_filter(self):
        service = VideoEditorService()

        executable = service._ffmpeg_executable()

        self.assertIn("imageio_ffmpeg", executable)

    def test_video_editor_ffmpeg_override_takes_precedence(self):
        service = VideoEditorService()

        with patch.dict("os.environ", {"VIDEO_EDITOR_FFMPEG_BINARY": "/custom/ffmpeg"}):
            self.assertEqual(service._ffmpeg_executable(), "/custom/ffmpeg")

    def test_vertical_subtitle_default_uses_lower_safe_area(self):
        service = VideoEditorService()

        alignment, margin_v, font_size = service._compute_subtitle_params(
            is_vertical=True,
            canvas_h=1920,
        )

        self.assertEqual(alignment, 2)
        self.assertEqual(margin_v, 230)
        self.assertEqual(font_size, 56)

    def test_vertical_thumbnail_renders_compact_source_and_title_regions(self):
        service = ThumbnailService()
        if not service.font_path:
            self.skipTest("No CJK-capable font available for thumbnail rendering")

        with tempfile.TemporaryDirectory() as tmpdir:
            screenshot_path = Path(tmpdir) / "source.png"
            Image.new("RGB", (1080, 1500), "#f8fafc").save(screenshot_path)

            output_path = service.generate_vertical_thumbnail(
                main_title="野村:大基金三期或领投大模型\n超长期国债与14倍杠杆",
                sub_title="政策性工具释放14倍杠杆潜力为基建",
                date_text="Jun 2026 | 研报播客",
                screenshot_path=str(screenshot_path),
                output_filename="unit_vertical_layout.jpg",
            )

            rendered = Image.open(output_path).convert("RGB")
            self.assertEqual(rendered.size, (1080, 1920))
            self.assertNotEqual(rendered.getpixel((80, 260)), rendered.getpixel((80, 900)))


if __name__ == "__main__":
    unittest.main()
