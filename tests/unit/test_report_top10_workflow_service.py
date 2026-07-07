import json
import sys
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.report_top10_workflow import (  # noqa: E402
    assemble_final_script_for_tts,
    build_editor_input,
    extract_editor_json,
    selected_report_count,
)


class ReportTop10WorkflowServiceTest(unittest.TestCase):
    def test_selected_report_count_clamps_to_supported_range(self):
        self.assertEqual(selected_report_count(None), 10)
        self.assertEqual(selected_report_count("3"), 3)
        self.assertEqual(selected_report_count(0), 1)
        self.assertEqual(selected_report_count(99), 20)

    def test_build_editor_input_uses_material_boundary(self):
        text = build_editor_input([
            {
                "source_task_id": "task-1",
                "pdf": {
                    "attachment_id": "asset-1",
                    "filename": "a.pdf",
                    "topic_title": "#外资研报",
                },
                "structured_result": {
                    "title": "单篇标题",
                    "cover_title": "封面标题",
                    "summary": "摘要",
                    "risk_notes": ["不构成投资建议"],
                    "final_script_for_tts": "这里是单篇口播。",
                },
                "single_report_script": "这里是单篇口播。",
            }
        ], {"date_label": "2026-06-24"})

        self.assertIn("【输入素材】", text)
        self.assertIn("single_report_script_char_limit: 800", text)
        self.assertIn('<report id="R01">', text)
        self.assertIn("filename: a.pdf", text)
        self.assertIn("single_report_script:\n这里是单篇口播。", text)
        self.assertNotIn("你是“今日研报 Top10”的总编辑", text)

    def test_build_editor_input_compacts_long_single_report_script(self):
        long_script = "开头" + ("重要信息" * 400) + "结尾"
        text = build_editor_input([
            {
                "source_task_id": "task-1",
                "pdf": {"attachment_id": "asset-1", "filename": "a.pdf"},
                "structured_result": {
                    "title": "单篇标题",
                    "summary": "摘要",
                    "final_script_for_tts": long_script,
                },
                "single_report_script": long_script,
            }
        ], {"editor_script_char_limit": 600})

        self.assertIn("single_report_script_char_limit: 600", text)
        self.assertIn("single_report_script_original_chars:", text)
        self.assertIn("正文已为总编辑阶段压缩", text)
        self.assertIn("开头", text)
        self.assertIn("结尾", text)
        self.assertLess(len(text), len(long_script) + 300)

    def test_extract_editor_json_from_fenced_response(self):
        payload = {
            "program_title": "今日研报 Top10",
            "final_script_for_tts": "完整口播",
            "video_segments": [],
        }
        text = "好的，结果如下：\n```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```"

        self.assertEqual(extract_editor_json(text), payload)

    def test_assemble_final_script_from_video_segments_and_source_scripts(self):
        editor_result = {
            "final_script_for_tts": "ASSEMBLE_BY_BACKEND_USING_final_script_assembly_plan",
            "opening": {"voice_text": "开场"},
            "items": [{"editorial_intro": "第一篇导语", "transition_after": "转场"}],
            "closing": {"voice_text": "收尾"},
            "video_segments": [
                {"voice_text_ref": "opening.voice_text"},
                {"voice_text_ref": "items[0].editorial_intro"},
                {
                    "voice_text_source": {
                        "source_report_id": "R01",
                        "source_field": "single_report_script",
                    }
                },
                {"voice_text_ref": "items[0].transition_after"},
                {"voice_text_ref": "closing.voice_text"},
            ],
        }
        script = assemble_final_script_for_tts(editor_result, [{"single_report_script": "单篇正文"}])

        self.assertEqual(script, "开场\n\n第一篇导语\n\n单篇正文\n\n转场\n\n收尾")


if __name__ == "__main__":
    unittest.main()
