import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.doubao_browser_tts import (  # noqa: E402
    DOUBAO_REPEAT_PROMPT_PREFIX,
    DoubaoBrowserTTSWorker,
    DoubaoBrowserTTSAutomationError,
    DoubaoBrowserTTSError,
    DoubaoBrowserTTSOptions,
    build_repeat_prompt,
    validate_audio_duration_for_text,
)
from services.report_audio_workflow import split_tts_text  # noqa: E402


class DoubaoBrowserTTSServiceTest(unittest.TestCase):
    def test_login_loss_sends_human_intervention_email(self):
        worker = DoubaoBrowserTTSWorker.__new__(DoubaoBrowserTTSWorker)
        worker.options = DoubaoBrowserTTSOptions(
            user_data_dir="/tmp/doubao-profile",
            extension_path="/tmp/extension",
        )
        page = SimpleNamespace(url="https://www.doubao.com/chat/")
        with patch.object(worker, "_captcha_error", return_value=None), patch.object(
            worker,
            "_first_visible",
            return_value=object(),
        ), patch.object(
            worker,
            "_save_failure_screenshot",
            return_value="/tmp/doubao-login.png",
        ), patch(
            "services.doubao_browser_tts.workflow_notification_service.send_browser_human_intervention"
        ) as notify:
            with self.assertRaisesRegex(DoubaoBrowserTTSAutomationError, "登录态失效"):
                worker._raise_if_login_or_captcha(page, "doubao-job")

        notify.assert_called_once()
        self.assertEqual("login", notify.call_args.kwargs["reason"])
        self.assertEqual("doubao-job", notify.call_args.kwargs["job_id"])

    def test_login_selectors_do_not_match_narration_containing_login_word(self):
        options = DoubaoBrowserTTSOptions(
            user_data_dir="/tmp/profile",
            extension_path="/tmp/extension",
        )
        worker = DoubaoBrowserTTSWorker.__new__(DoubaoBrowserTTSWorker)
        worker.options = options
        selectors = worker._selectors("login")
        self.assertNotIn("text=登录", selectors)
        self.assertIn('button:has-text("登录")', selectors)

    def _worker(self):
        return DoubaoBrowserTTSWorker(
            DoubaoBrowserTTSOptions(
                user_data_dir="/tmp/doubao-test-profile",
                extension_path=str(BACKEND_DIR.parents[1] / "tools" / "doubao-audio-capture-extension"),
            )
        )

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

    def test_wav_export_prefers_browser_download_without_serializing_blob(self):
        saved = {}

        class Download:
            def save_as(self, path):
                saved["path"] = path

        class DownloadContext:
            value = Download()

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        class Page:
            def __init__(self):
                self.evaluate_calls = []

            def evaluate(self, expression, arg=None):
                self.evaluate_calls.append((expression, arg))
                if expression == "() => typeof window.downloadDoubaoWav === 'function'" and arg is None:
                    return True
                if "downloadDoubaoWav(filename)" in expression:
                    return True
                raise AssertionError("Blob serialization fallback should not run")

            def expect_download(self, timeout):
                self.download_timeout = timeout
                return DownloadContext()

        page = Page()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "job.wav"
            wav_bytes = self._worker()._export_wav_bytes(page, output, "job-1")

        self.assertEqual(wav_bytes, b"")
        self.assertEqual(saved["path"], str(output))
        self.assertEqual(page.download_timeout, 30_000)
        self.assertEqual(page.evaluate_calls[1][1], "job.wav")

    def test_dismiss_known_promotional_dialog_uses_page_script(self):
        class Page:
            expression = ""

            def evaluate(self, expression):
                self.expression = expression
                return 1

        page = Page()

        dismissed = self._worker()._dismiss_known_promotional_dialogs(page)

        self.assertEqual(dismissed, 1)
        self.assertIn("下载电脑版", page.expression)
        self.assertIn("dialog-close", page.expression)


if __name__ == "__main__":
    unittest.main()
