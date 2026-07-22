import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services import report_audio_workflow as report_audio_service  # noqa: E402
from services.report_audio_workflow import (  # noqa: E402
    ReportAudioWorkflowError,
    _normalizer_model,
    normalize_for_tts,
    status_payload,
    structured_result_quality_issue,
)


class ReportAudioWorkflowServiceTest(unittest.TestCase):
    def test_structured_result_preserves_separate_landscape_and_vertical_titles(self):
        payload = report_audio_service._validate_structured_result({
            "title": "列表标题",
            "landscape_title": "大摩:增持腾讯\n盈利预期上修",
            "landscape_subtitle": "目标价上调至700港元",
            "vertical_title": "腾讯盈利为何再超预期",
            "vertical_subtitle": "AI广告进入兑现期",
            "final_script_for_tts": "这是用于直接朗读的完整研报正文，包含足够信息并且没有工作流结构标签。",
        }, "Gemini 审核后的完整结果")

        self.assertEqual(payload["landscape_title"], "大摩:增持腾讯\n盈利预期上修")
        self.assertEqual(payload["vertical_title"], "腾讯盈利为何再超预期")
        self.assertEqual(payload["cover_title"], payload["landscape_title"])
        self.assertEqual(payload["cover_subtitle"], payload["landscape_subtitle"])

    def test_legacy_cover_fields_backfill_both_title_variants(self):
        payload = report_audio_service._validate_structured_result({
            "cover_title": "高盛:首次覆盖智谱",
            "cover_subtitle": "企业级编码模型进入兑现期",
            "final_script_for_tts": "这是用于直接朗读的完整研报正文，包含足够信息并且没有工作流结构标签。",
        }, "历史 Gemini 审核结果")

        self.assertEqual(payload["landscape_title"], "高盛:首次覆盖智谱")
        self.assertEqual(payload["vertical_title"], "高盛:首次覆盖智谱")

    def test_default_normalizer_model_is_deepseek_v4_flash(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(_normalizer_model(), "deepseek-v4-flash")

    def test_status_marks_normalizer_unavailable_without_deepseek_or_fallback(self):
        with patch.dict(os.environ, {}, clear=True):
            payload = status_payload()

        self.assertFalse(payload["configured"])
        self.assertFalse(payload["normalizer_available"])
        self.assertEqual(payload["normalizer_provider"], "unavailable")

    def test_normalizer_model_env_override_takes_precedence(self):
        with patch.dict(os.environ, {
            "REPORT_AUDIO_NORMALIZER_MODEL": "custom-normalizer",
            "DEEPSEEK_MODEL": "deepseek-chat",
            "DEEPSEEK_API_KEY": "test-key",
        }, clear=False):
            payload = status_payload()

        self.assertEqual(payload["normalizer_model"], "custom-normalizer")

    def test_local_fallback_is_disabled_by_default(self):
        with patch.dict(os.environ, {"REPORT_AUDIO_ALLOW_LOCAL_FALLBACK": ""}, clear=False), \
            patch.object(report_audio_service, "_deepseek_normalize", return_value=None):
            with self.assertRaisesRegex(ReportAudioWorkflowError, "禁止 local_fallback"):
                normalize_for_tts("这是一段 Gemini 返回的研报口播正文。")

    def test_local_fallback_strips_gemini_capture_artifacts(self):
        dirty_text = (
            "研报pdf解析视频文案 自定义 Gem 研报pdf解析视频文案说 "
            "【研报气质】 颠覆型 【Hook策略】 反常识提问型 【结尾情绪基调】 聚焦式 "
            "如果我告诉你，中东的紧张局势全面降温，黄金非但不会默默，反而会迎来新一轮爆发，你信吗？ "
            "PDF + 1 今天我们就来拆解这份报告。"
        )

        with patch.dict(os.environ, {"REPORT_AUDIO_ALLOW_LOCAL_FALLBACK": "1"}, clear=False), \
            patch.object(report_audio_service, "_deepseek_normalize", return_value=None):
            payload = normalize_for_tts(dirty_text)

        script = payload["final_script_for_tts"]
        self.assertEqual(payload["provider"], "local_fallback")
        self.assertTrue(script.startswith("如果我告诉你"))
        self.assertNotIn("自定义 Gem", script)
        self.assertNotIn("研报pdf解析视频文案", script)
        self.assertNotIn("PDF + 1", script)

    def test_local_fallback_prefers_final_podcast_script_over_review_notes(self):
        dirty_text = (
            "作为首席内容主编，我已对您提供的播客文案初稿进行了全面复核。"
            "以下是具体的核对说明、勘误点以及优化后的最终播客文案。"
            "一、 核对过程与勘误说明 原稿表述：这里都是工作流说明，不应朗读。"
            "最终优化版播客文案 ：反差型 ：孤立数据型 ：聚焦式 "
            "35%。这是什么概念？欢迎收听《听见资本》。今天我们要拆解美银证券对闪迪的最新研报。"
            "最后需要郑重提醒的是，以上内容不构成任何形式的投资建议。 三、"
        )

        with patch.dict(os.environ, {"REPORT_AUDIO_ALLOW_LOCAL_FALLBACK": "1"}, clear=False), \
            patch.object(report_audio_service, "_deepseek_normalize", return_value=None):
            payload = normalize_for_tts(dirty_text)

        script = payload["final_script_for_tts"]
        self.assertTrue(script.startswith("35%。这是什么概念？"))
        self.assertNotIn("首席内容主编", script)
        self.assertNotIn("核对过程", script)
        self.assertNotIn("原稿表述", script)
        self.assertFalse(script.endswith("三、"))

    def test_normalizer_timeout_override_is_passed_to_deepseek(self):
        payload = {
            "final_script_for_tts": "这是一段已经整理好的研报口播正文，足够作为结构化结果。",
        }

        with patch.object(report_audio_service, "_deepseek_normalize", return_value=payload) as deepseek:
            normalize_for_tts("原始 Gemini 文本", normalizer_timeout=7)

        self.assertEqual(deepseek.call_args.kwargs["timeout_seconds"], 7)

    def test_normalizer_strips_section_outline_labels_from_tts_script(self):
        payload = {
            "final_script_for_tts": (
                "第一部分：开场与变量切换（引入悬念与核心结论） "
                "过去我们分析硬件产业链，最关心的是单一的成本优势。"
                "第二部分：产业背景与实地调研印证（铺垫与底层逻辑） "
                "在当前AI基础设施狂飙突进的表象下，顶级投行正在关注供应链安全重构。"
            ),
        }

        with patch.object(report_audio_service, "_deepseek_normalize", return_value=payload):
            normalized = normalize_for_tts("原始 Gemini 文本")

        script = normalized["final_script_for_tts"]
        self.assertTrue(script.startswith("过去我们分析硬件产业链"))
        self.assertIn("在当前AI基础设施狂飙突进", script)
        self.assertNotIn("第一部分", script)
        self.assertNotIn("第二部分", script)
        self.assertNotIn("开场与变量切换", script)
        self.assertNotIn("产业背景与实地调研印证", script)

    def test_normalizer_strips_citation_markers_from_tts_script(self):
        payload = {
            "final_script_for_tts": (
                "固定资产投资增速转负[cite: 1]。"
                "政策节奏仍需观察【cite：2】，市场关注财政发力(cite: 3-4)。"
                "最后是裸露标记 cite:5 也不能进入语音。"
            ),
        }

        with patch.object(report_audio_service, "_deepseek_normalize", return_value=payload):
            normalized = normalize_for_tts("原始 Gemini 文本")

        script = normalized["final_script_for_tts"]
        self.assertEqual(
            script,
            "固定资产投资增速转负。政策节奏仍需观察，市场关注财政发力。最后是裸露标记 也不能进入语音。",
        )
        self.assertNotRegex(script.lower(), r"cite\s*[:：]")

    def test_structured_result_quality_detects_section_outline_labels(self):
        issue = structured_result_quality_issue({
            "final_script_for_tts": "第一部分：开场与变量切换 这里才是正文。",
        })

        self.assertIn("结构", issue)

    def test_structured_result_quality_detects_cached_capture_artifacts(self):
        issue = structured_result_quality_issue({
            "final_script_for_tts": "研报pdf解析视频文案 自定义 Gem 研报pdf解析视频文案说 这是一段正文。",
        })

        self.assertIn("页面", issue)

    def test_structured_result_quality_detects_review_note_residue(self):
        issue = structured_result_quality_issue({
            "final_script_for_tts": "作为首席内容主编，我已完成复核。原稿表述：这里是勘误说明。",
        })

        self.assertIn("勘误", issue)

    def test_normalizer_rejects_unclean_capture_artifacts(self):
        payload = {
            "final_script_for_tts": "这是一段正文，但中间混入了自定义 Gem 这种页面残留，不能交给豆包朗读。",
        }

        with patch.object(report_audio_service, "_deepseek_normalize", return_value=payload):
            with self.assertRaisesRegex(ReportAudioWorkflowError, "不可用于朗读"):
                normalize_for_tts("原始 Gemini 文本")


if __name__ == "__main__":
    unittest.main()
