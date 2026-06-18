import os
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw

from services.gemini_browser import GeminiBrowserOptions, GeminiBrowserWorker, _looks_like_page_shell_text
from services.mp3_video_workflow import (
    DEFAULT_SPEED,
    PLANNER_VERSION,
    VISUAL_ARTIFACT_KEYS,
    VISUAL_ARTIFACT_VERSION,
    _visual_artifacts_ready,
    audio_segment_from_plan,
    draw_audio_background,
    draw_closed_door_cover,
    draw_finance_cover,
    fallback_plan,
    gemini_options_from_env,
    validate_plan,
)


class Mp3VideoWorkflowServiceTest(unittest.TestCase):
    def test_fallback_plan_extracts_clean_title_and_audio_speed(self):
        plan = fallback_plan(
            "标题方案：算力调整假摔还是上车。核心观点：CPO 时代预期差。",
            audio={"filename": "demo.mp3", "duration_seconds": 120},
        )

        self.assertEqual(plan["audio"]["speed"], DEFAULT_SPEED)
        self.assertIn("算力调整", plan["title"])
        self.assertNotIn("标题方案", plan["title"])
        self.assertEqual(plan["cover_main_title"], "内资闭门会议")
        self.assertEqual(plan["xiaojunjun_title"], "内资闭门会议")
        self.assertEqual(plan["planner_version"], PLANNER_VERSION)

    def test_validate_plan_clamps_audio_range(self):
        fallback = fallback_plan("标题方案：CPO 时代预期差。", audio={"duration_seconds": 60})
        plan = validate_plan(
            {
                "title": "CPO 时代预期差",
                "audio": {"start_seconds": "00:10", "end_seconds": "99:00", "speed": 1.0},
            },
            fallback=fallback,
            audio={"duration_seconds": 60},
        )

        self.assertEqual(plan["audio"]["start_seconds"], 10)
        self.assertEqual(plan["audio"]["end_seconds"], 60)
        self.assertEqual(plan["audio"]["speed"], DEFAULT_SPEED)

    def test_audio_segment_uses_end_offset_for_publisher(self):
        segment = audio_segment_from_plan(
            {"path": "/tmp/demo.mp3", "duration_seconds": 100},
            {"audio": {"start_seconds": 12, "end_seconds": 72}},
        )

        self.assertEqual(segment["start"], 12)
        self.assertEqual(segment["end_offset"], 28)
        self.assertEqual(segment["speed"], DEFAULT_SPEED)

    def test_draw_visual_assets_create_files(self):
        plan = fallback_plan("标题方案：算力调整假摔还是上车。", audio={"duration_seconds": 12})
        with tempfile.TemporaryDirectory() as tmpdir:
            infographic = os.path.join(tmpdir, "infographic.png")
            img = Image.new("RGB", (1200, 700), "white")
            draw = ImageDraw.Draw(img)
            draw.rectangle([40, 40, 1160, 660], outline="black", width=4)
            draw.text((100, 100), "dummy", fill="black")
            img.save(infographic)

            outputs = [
                os.path.join(tmpdir, "finance.jpg"),
                os.path.join(tmpdir, "audio_bg.jpg"),
                os.path.join(tmpdir, "closed_cover.jpg"),
                os.path.join(tmpdir, "closed_bg.jpg"),
            ]
            draw_finance_cover(outputs[0], plan)
            draw_audio_background(outputs[1], plan, infographic)
            draw_closed_door_cover(outputs[2], plan, include_date=True)
            draw_closed_door_cover(outputs[3], plan, include_date=False)

            for path in outputs:
                self.assertTrue(os.path.exists(path), path)
                self.assertGreater(os.path.getsize(path), 10_000)

    def test_visual_artifacts_ready_requires_all_non_empty_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            artifacts = {}
            for key in VISUAL_ARTIFACT_KEYS:
                path = os.path.join(tmpdir, f"{key}.jpg")
                with open(path, "wb") as handle:
                    handle.write(b"ok")
                artifacts[key] = {"path": path, "template_version": VISUAL_ARTIFACT_VERSION}

            self.assertTrue(_visual_artifacts_ready(artifacts))

            os.remove(artifacts["audio_background"]["path"])
            self.assertFalse(_visual_artifacts_ready(artifacts))

    def test_visual_artifacts_ready_rejects_old_template_version(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            artifacts = {}
            for key in VISUAL_ARTIFACT_KEYS:
                path = os.path.join(tmpdir, f"{key}.jpg")
                with open(path, "wb") as handle:
                    handle.write(b"ok")
                artifacts[key] = {"path": path, "template_version": "old"}

            self.assertFalse(_visual_artifacts_ready(artifacts))

    def test_gemini_response_detection_rejects_page_shell(self):
        shell_text = (
            "跳至主要内容 Gemini 发起新对话 搜索对话内容 库 Gem 笔记本 "
            "最近 与 Gemini 对话 MP3 demo.mp3 Pro Gemini 是一款 AI 工具"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            worker = GeminiBrowserWorker(GeminiBrowserOptions(user_data_dir=tmpdir))

            self.assertTrue(_looks_like_page_shell_text(shell_text))
            self.assertFalse(worker._looks_like_response(shell_text, ""))
            self.assertTrue(worker._looks_like_response("这份音频的核心结论是 AI 算力资产被系统性低估。", ""))

    def test_mp3_gemini_defaults_wait_for_long_audio(self):
        with patch.dict(os.environ, {
            "MP3_VIDEO_GEMINI_RESPONSE_TIMEOUT": "",
            "MP3_VIDEO_GEMINI_IMAGE_TIMEOUT": "",
            "MP3_VIDEO_GEMINI_TOTAL_TIMEOUT": "",
        }, clear=False):
            options = gemini_options_from_env()

        self.assertGreaterEqual(options.response_timeout_seconds, 900)
        self.assertGreaterEqual(options.image_timeout_seconds, 900)
        self.assertGreaterEqual(options.total_timeout_seconds, 2400)


if __name__ == "__main__":
    unittest.main()
