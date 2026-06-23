import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from tasks.report_audio_workflow import (  # noqa: E402
    CURRENT_STEP_MAX_LENGTH,
    _child_id_from_result,
    _child_inline_wait_seconds,
    _current_step_code,
    _format_cover_subtitle,
    _format_cover_title,
    _is_retryable_child_error,
    _parent_max_defers,
    _parent_retry_delay_seconds,
    _parse_utc_datetime,
    _publisher_script_subtitles,
    _publisher_sub_burn_enabled,
    _publisher_result_summary,
    _publisher_video_transform,
    _task_chunk_attempt,
    _valid_structured_result,
    _video_stage_enabled,
)


class ReportAudioWorkflowTaskTest(unittest.TestCase):
    def test_parent_current_step_codes_fit_publication_task_column(self):
        steps = [
            "submit_gemini",
            "gemini_prepare_browser",
            "gemini_open_page",
            "gemini_upload_pdf",
            "gemini_submit_prompt",
            "gemini_wait_response",
            "gemini_initial_response",
            "gemini_followup_1_submit",
            "gemini_followup_1_wait",
            "gemini_followup_1_response",
            "gemini_save_result",
            "submit_doubao_browser_1",
            "doubao_browser_1_prepare_browser",
            "doubao_browser_1_clear_audio_cache",
            "doubao_browser_1_submit_text",
            "doubao_browser_1_wait_response",
            "doubao_browser_1_start_reading",
            "doubao_browser_1_capture_audio",
            "doubao_browser_1_download_wav",
            "doubao_browser_1_save_file",
            "combine_audio",
        ]

        for step in steps:
            with self.subTest(step=step):
                self.assertLessEqual(len(_current_step_code(step)), CURRENT_STEP_MAX_LENGTH)

    def test_cover_title_uses_bank_prefix_and_avoids_leading_punctuation(self):
        title = _format_cover_title("摩根大通亚洲被动元件、PCB-CCL、基板及测试行业研报解读")

        lines = title.split("\n")
        self.assertLessEqual(len(lines), 2)
        self.assertEqual(lines[0], "摩根大通:亚洲被动元件")
        self.assertFalse(lines[1].startswith("、"))

    def test_cover_title_preserves_ai_two_line_format(self):
        title = _format_cover_title("大摩:增持腾讯\n微信原生AI将测")

        self.assertEqual(title, "大摩:增持腾讯\n微信原生AI将测")

    def test_cover_subtitle_is_single_line_and_bounded(self):
        subtitle = _format_cover_subtitle(
            "本报告解读摩根大通对亚洲被动元件、PCB-CCL、基板及测试行业的研究观点，重点关注需求复苏。"
        )

        self.assertNotIn("\n", subtitle)
        self.assertLessEqual(len(subtitle), 34)

    def test_default_video_transform_is_omitted_for_pdf_top_alignment(self):
        self.assertIsNone(_publisher_video_transform({}))
        self.assertIsNone(_publisher_video_transform({"video_transform": {"x": 0, "y": 0, "scale": 1}}))
        self.assertIsNone(_publisher_video_transform({"video_transform": {"x": "0", "y": "0", "scale": "1"}}))

        custom = _publisher_video_transform({"video_transform": {"x": 0, "y": -120, "scale": 1}})
        self.assertEqual(custom["y"], -120)

    def test_subtitle_burn_defaults_on_for_report_audio_video(self):
        self.assertTrue(_publisher_sub_burn_enabled({}))
        self.assertTrue(_publisher_sub_burn_enabled({"sub_burn_enabled": None}))
        self.assertTrue(_publisher_sub_burn_enabled({"sub_burn_enabled": "1"}))
        self.assertFalse(_publisher_sub_burn_enabled({"sub_burn_enabled": False}))
        self.assertFalse(_publisher_sub_burn_enabled({"sub_burn_enabled": "0"}))

    def test_video_stage_runs_when_only_vertical_enabled(self):
        self.assertTrue(_video_stage_enabled({"video": {"enabled": False, "vertical_enabled": True}}))
        self.assertTrue(_video_stage_enabled({"video": {"enabled": True, "vertical_enabled": False}}))
        self.assertFalse(_video_stage_enabled({"video": {"enabled": False, "vertical_enabled": False}}))

    def test_publisher_result_summary_exposes_vertical_video(self):
        class FakePublisherTask:
            id = "publisher-1"
            celery_task_id = "celery-1"
            status = "completed"
            params = {
                "video": {"enabled": False},
                "vertical_video": {"enabled": True},
            }
            result = {
                "vertical_video_path": "/tmp/vertical.mp4",
                "cover_path": "/tmp/cover.jpg",
            }

        summary = _publisher_result_summary(FakePublisherTask(), {"path": "/tmp/page.jpg"})

        self.assertFalse(summary["landscape_enabled"])
        self.assertTrue(summary["vertical_enabled"])
        self.assertEqual(summary["video_path"], "/tmp/vertical.mp4")
        self.assertEqual(summary["vertical_video_path"], "/tmp/vertical.mp4")

    def test_script_subtitles_are_generated_from_tts_text(self):
        subtitles = _publisher_script_subtitles(
            {"final_script_for_tts": "180天。在北美仿制药市场里，这意味着限时护城河。"},
            {"seconds": 8.0},
        )

        self.assertGreaterEqual(len(subtitles), 2)
        self.assertEqual(subtitles[0]["start"], 0)
        self.assertEqual(subtitles[-1]["end"], 8.0)
        self.assertIn("仿制药", "".join(item["text"] for item in subtitles))

    def test_gemini_browser_failures_are_retryable_but_cancel_is_not(self):
        self.assertTrue(_is_retryable_child_error("gemini", "文件上传失败: 等待 Gemini 文件上传完成超时"))
        self.assertTrue(_is_retryable_child_error("gemini", "页面自动化超时: Locator.click: Timeout 10000ms exceeded"))
        self.assertTrue(_is_retryable_child_error("gemini", "Gemini 提示词未成功提交: 未找到 Gemini 发送按钮"))
        self.assertTrue(_is_retryable_child_error("gemini", "子任务仍在执行: gemini child-1"))
        self.assertFalse(_is_retryable_child_error("gemini", "父编排任务已取消"))

    def test_publisher_child_id_can_be_recovered_for_deferred_resume(self):
        result = {
            "children": {
                "publisher_video": {
                    "job_id": "publisher-child",
                    "status": "pending",
                }
            }
        }

        self.assertEqual(_child_id_from_result(result, "publisher_video"), "publisher-child")
        self.assertLessEqual(len(_current_step_code("publisher_video_waiting")), CURRENT_STEP_MAX_LENGTH)

    def test_heavy_children_use_short_inline_waits_and_deferred_resume(self):
        env_keys = [
            "REPORT_AUDIO_DOUBAO_INLINE_WAIT_SECONDS",
            "REPORT_AUDIO_PUBLISHER_INLINE_WAIT_SECONDS",
            "REPORT_AUDIO_DOUBAO_PARENT_RETRY_DELAY_SECONDS",
            "REPORT_AUDIO_PUBLISHER_PARENT_RETRY_DELAY_SECONDS",
            "REPORT_AUDIO_DOUBAO_PARENT_MAX_DEFERS",
            "REPORT_AUDIO_PUBLISHER_PARENT_MAX_DEFERS",
        ]
        with patch.dict(os.environ, {key: "" for key in env_keys}, clear=False):
            for key in env_keys:
                os.environ.pop(key, None)
            self.assertEqual(_child_inline_wait_seconds("doubao_browser_1"), 5)
            self.assertEqual(_child_inline_wait_seconds("publisher_video"), 5)
            self.assertEqual(_parent_retry_delay_seconds("doubao_browser_1"), 30)
            self.assertEqual(_parent_retry_delay_seconds("publisher_video"), 60)
            self.assertEqual(_parent_max_defers("doubao_browser_1"), 240)
            self.assertEqual(_parent_max_defers("publisher_video"), 180)
            self.assertEqual(_child_inline_wait_seconds("gemini"), 1800)
            self.assertEqual(_parent_retry_delay_seconds("gemini"), 300)

    def test_tts_chunk_attempt_can_be_read_from_result_or_params(self):
        class ResultTask:
            result = {"metadata": {"chunk_attempt": "3"}}
            params = {}

        class ParamsTask:
            result = {}
            params = {"doubao_browser_tts": {"metadata": {"chunk_attempt": 2}}}

        self.assertEqual(_task_chunk_attempt(ResultTask()), 3)
        self.assertEqual(_task_chunk_attempt(ParamsTask()), 2)

    def test_cached_structured_result_with_capture_artifacts_is_not_reused(self):
        bad_script = (
            "研报pdf解析视频文案 自定义 Gem 研报pdf解析视频文案说 "
            + "这是一段正文。" * 40
        )
        result = {"structured_result": {"final_script_for_tts": bad_script}}

        with patch.dict(os.environ, {"REPORT_AUDIO_GEMINI_MIN_FINAL_CHARS": "10"}, clear=False):
            self.assertIsNone(_valid_structured_result(result))

    def test_clean_cached_structured_result_can_be_reused(self):
        good_script = "这是一段已经清洗干净、可以直接交给豆包朗读的正文。" * 8
        result = {"structured_result": {"final_script_for_tts": good_script}}

        with patch.dict(os.environ, {"REPORT_AUDIO_GEMINI_MIN_FINAL_CHARS": "10"}, clear=False):
            self.assertIs(_valid_structured_result(result), result["structured_result"])

    def test_parse_utc_datetime_accepts_z_suffix_and_offsets(self):
        self.assertEqual(_parse_utc_datetime("2026-06-23T01:02:03Z").isoformat(), "2026-06-23T01:02:03")
        self.assertEqual(_parse_utc_datetime("2026-06-23T09:02:03+08:00").isoformat(), "2026-06-23T01:02:03")
        self.assertIsNone(_parse_utc_datetime(""))


if __name__ == "__main__":
    unittest.main()
