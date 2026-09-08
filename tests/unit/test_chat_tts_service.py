import os
from pathlib import Path
import sys
from unittest import mock
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = PROJECT_ROOT / "src" / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from services.chat_tts_service import ChatTTSService


class _FakeAudio:
    def __add__(self, other):
        return self

    def export(self, path, format):
        Path(path).write_bytes(b"combined")


class ChatTtsServiceTest(unittest.TestCase):
    def setUp(self):
        ChatTTSService._instance = None
        self.service = ChatTTSService()

    def tearDown(self):
        ChatTTSService._instance = None

    def test_dialogue_supplies_unique_output_path_for_each_segment(self):
        segment_paths = []

        def synthesize(text, speaker_id="default", output_path=None):
            self.assertIsNotNone(output_path)
            self.assertNotIn(output_path, segment_paths)
            Path(output_path).write_bytes(b"segment")
            segment_paths.append(output_path)
            return output_path

        with mock.patch.object(self.service, "synthesize", side_effect=synthesize), mock.patch(
            "services.chat_tts_service.AudioSegment.from_wav",
            return_value=_FakeAudio(),
        ), mock.patch(
            "services.chat_tts_service.AudioSegment.silent",
            return_value=_FakeAudio(),
        ):
            result = self.service.synthesize_dialogue(
                [
                    {"speaker": "A", "text": "你好"},
                    {"speaker": "B", "text": "你好呀"},
                ]
            )

        try:
            self.assertEqual(len(segment_paths), 2)
            self.assertTrue(Path(result).is_file())
            self.assertTrue(all(not os.path.exists(path) for path in segment_paths))
        finally:
            if result and os.path.exists(result):
                os.remove(result)


if __name__ == "__main__":
    unittest.main()
