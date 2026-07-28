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

    def test_scheme3_compositor_places_visual_video_in_template_frame(self):
        service = VideoEditorService()
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "scheme3.mp4"

            def fake_run(command, **_kwargs):
                Path(command[-1]).write_bytes(b"mp4")

            with patch.object(service, "_ffmpeg_executable", return_value="/fake/ffmpeg"), \
                patch.object(service, "_probe_media_duration", return_value=3.0), \
                patch.object(service, "_assert_playable_video"), \
                patch("services.video_editor.subprocess.run", side_effect=fake_run) as run:
                rendered = service._synthesize_framed_vertical_ffmpeg(
                    background_image="/tmp/background.jpg",
                    content_video="/tmp/visual.mp4",
                    audio_file="/tmp/audio.mp3",
                    output_path=str(output),
                    export_params={"resolution": "1080p"},
                )

            command = run.call_args.args[0]
            filters = command[command.index("-filter_complex") + 1]
            self.assertEqual(rendered, str(output))
            self.assertIn("crop=1080:608", filters)
            self.assertIn("overlay=0:620", filters)

    def test_scheme3_direct_scene_compositor_uses_stills_without_content_video(self):
        service = VideoEditorService()
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "scheme3-scenes.mp4"

            def fake_run(command, **_kwargs):
                Path(command[-1]).write_bytes(b"mp4")

            with patch.object(service, "_ffmpeg_executable", return_value="/fake/ffmpeg"), \
                patch.object(service, "_probe_media_duration", return_value=3.03), \
                patch.object(service, "_assert_playable_video"), \
                patch("services.video_editor.subprocess.run", side_effect=fake_run) as run:
                rendered = service._synthesize_framed_vertical_scenes_ffmpeg(
                    background_image="/tmp/background.jpg",
                    scenes=[
                        {"sequence": 1, "image_path": "/tmp/scene-1.png", "duration_seconds": 1.01},
                        {"sequence": 2, "image_path": "/tmp/scene-2.png", "duration_seconds": 2.02},
                    ],
                    audio_file="/tmp/audio.mp3",
                    output_path=str(output),
                    export_params={"resolution": "1080p"},
                )

            command = run.call_args.args[0]
            filters = command[command.index("-filter_complex") + 1]
            self.assertEqual(rendered, str(output))
            self.assertIn("concat=n=2:v=1:a=0[content]", filters)
            self.assertIn("crop=1080:608", filters)
            self.assertIn("overlay=0:620", filters)
            self.assertIn("trim=duration=1.000000", filters)
            self.assertIn("trim=duration=2.033333", filters)
            self.assertNotIn("-stream_loop", command)
            self.assertNotIn("-t", command)

    def test_scheme3_full_direct_render_reuses_single_wav_without_mp3_transcode(self):
        service = VideoEditorService()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            background = root / "background.jpg"
            scene = root / "scene.png"
            audio = root / "audio.wav"
            background.write_bytes(b"background")
            scene.write_bytes(b"scene")
            audio.write_bytes(b"audio")
            with patch.object(service, "_extract_global_subtitles", return_value=[]), \
                patch.object(
                    service,
                    "_synthesize_framed_vertical_scenes_ffmpeg",
                    return_value="/tmp/output.mp4",
                ) as synthesize:
                rendered = service.process_framed_vertical_scenes(
                    background_image=str(background),
                    scenes=[{
                        "sequence": 1,
                        "image_path": str(scene),
                        "duration_seconds": 3.0,
                    }],
                    audio_files=str(audio),
                )

            self.assertEqual(rendered, "/tmp/output.mp4")
            self.assertEqual(synthesize.call_args.kwargs["audio_file"], str(audio))


if __name__ == "__main__":
    unittest.main()
