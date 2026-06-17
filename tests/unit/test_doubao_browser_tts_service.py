import sys
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.doubao_browser_tts import (  # noqa: E402
    DOUBAO_REPEAT_PROMPT_PREFIX,
    DoubaoBrowserTTSError,
    build_repeat_prompt,
    validate_audio_duration_for_text,
)
from services.report_audio_workflow import split_tts_text  # noqa: E402


class DoubaoBrowserTTSServiceTest(unittest.TestCase):
    def test_repeat_prompt_wraps_original_text(self):
        text = "第一句。\n第二句，不要被总结。"

        prompt = build_repeat_prompt(text)

        self.assertTrue(prompt.startswith(DOUBAO_REPEAT_PROMPT_PREFIX))
        self.assertIn("请一字不差重复下面的内容", prompt)
        self.assertTrue(prompt.endswith(text))

    def test_duration_validation_rejects_implausibly_short_audio(self):
        text = "光伏储能已经成为基荷电源。" * 120

        with self.assertRaises(DoubaoBrowserTTSError):
            validate_audio_duration_for_text({"seconds": 0.467}, text)

    def test_duration_validation_allows_reasonable_audio(self):
        text = "光伏储能已经成为基荷电源。" * 120

        audio = validate_audio_duration_for_text({"seconds": 180.0}, text)

        self.assertEqual(audio["seconds"], 180.0)

    def test_report_workflow_splits_tts_text_into_reliable_chunks_by_default(self):
        text = "这是一个用于豆包朗读拆段的句子。" * 120

        chunks = split_tts_text(text)

        self.assertGreater(len(chunks), 1)
        self.assertLessEqual(max(len(chunk) for chunk in chunks), 500)


if __name__ == "__main__":
    unittest.main()
