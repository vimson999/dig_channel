import sys
import tempfile
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.report_audio_completion_contract import (  # noqa: E402
    evaluate_report_video_completion,
)


class ReportAudioCompletionContractTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        self.landscape = self._file("landscape.mp4")
        self.vertical = self._file("scheme3.mp4")
        self.wav = self._file("final.wav")

    def tearDown(self):
        self.tmpdir.cleanup()

    def _file(self, name, payload=b"media"):
        path = self.root / name
        path.write_bytes(payload)
        return path

    def _probe(self, path):
        if str(path).endswith(".wav"):
            return {"duration": 60.0, "video_streams": 0, "audio_streams": 1}
        return {"duration": 60.4, "video_streams": 1, "audio_streams": 1}

    def test_valid_landscape_and_scheme3_outputs_are_ready(self):
        result = evaluate_report_video_completion(
            include_landscape=True,
            vertical_schemes=["scheme3"],
            landscape_path=str(self.landscape),
            vertical_paths={"scheme3": str(self.vertical)},
            audio_path=str(self.wav),
            probe=self._probe,
        )

        self.assertTrue(result["ready"])
        self.assertEqual(result["missing"], [])
        self.assertTrue(result["outputs"]["landscape"]["ready"])
        self.assertTrue(result["outputs"]["vertical_scheme3"]["ready"])

    def test_missing_landscape_reports_specific_output(self):
        result = evaluate_report_video_completion(
            include_landscape=True,
            vertical_schemes=[],
            landscape_path="",
            vertical_paths={},
            probe=self._probe,
        )

        self.assertFalse(result["ready"])
        self.assertEqual(result["missing"], ["landscape"])
        self.assertEqual(result["outputs"]["landscape"]["reason"], "missing_path")

    def test_missing_selected_vertical_scheme_is_not_satisfied_by_another_scheme(self):
        result = evaluate_report_video_completion(
            include_landscape=False,
            vertical_schemes=["scheme3"],
            landscape_path="",
            vertical_paths={"scheme1": str(self.vertical)},
            probe=self._probe,
        )

        self.assertFalse(result["ready"])
        self.assertEqual(result["missing"], ["vertical_scheme3"])
        self.assertEqual(
            result["outputs"]["vertical_scheme3"]["reason"],
            "missing_path",
        )

    def test_zero_byte_video_is_rejected_before_probe(self):
        empty = self._file("empty.mp4", b"")
        calls = []

        def probe(path):
            calls.append(path)
            return self._probe(path)

        result = evaluate_report_video_completion(
            include_landscape=True,
            vertical_schemes=[],
            landscape_path=str(empty),
            vertical_paths={},
            probe=probe,
        )

        self.assertFalse(result["ready"])
        self.assertEqual(result["outputs"]["landscape"]["reason"], "empty_file")
        self.assertEqual(calls, [])

    def test_video_without_video_or_audio_stream_is_rejected(self):
        for profile, expected in (
            ({"duration": 60.0, "video_streams": 0, "audio_streams": 1}, "missing_video_stream"),
            ({"duration": 60.0, "video_streams": 1, "audio_streams": 0}, "missing_audio_stream"),
        ):
            with self.subTest(expected=expected):
                result = evaluate_report_video_completion(
                    include_landscape=True,
                    vertical_schemes=[],
                    landscape_path=str(self.landscape),
                    vertical_paths={},
                    probe=lambda _path, value=profile: value,
                )
                self.assertFalse(result["ready"])
                self.assertEqual(result["outputs"]["landscape"]["reason"], expected)

    def test_duration_mismatch_against_final_wav_is_rejected(self):
        def probe(path):
            if str(path).endswith(".wav"):
                return {"duration": 60.0, "video_streams": 0, "audio_streams": 1}
            return {"duration": 67.0, "video_streams": 1, "audio_streams": 1}

        result = evaluate_report_video_completion(
            include_landscape=True,
            vertical_schemes=[],
            landscape_path=str(self.landscape),
            vertical_paths={},
            audio_path=str(self.wav),
            probe=probe,
        )

        self.assertFalse(result["ready"])
        output = result["outputs"]["landscape"]
        self.assertEqual(output["reason"], "duration_mismatch")
        self.assertEqual(output["audio_duration"], 60.0)
        self.assertEqual(output["duration"], 67.0)

    def test_scheme_names_are_normalized_and_deduplicated(self):
        result = evaluate_report_video_completion(
            include_landscape=False,
            vertical_schemes=["SCHEME3", "scheme3", "invalid"],
            landscape_path="",
            vertical_paths={"scheme3": str(self.vertical)},
            probe=self._probe,
        )

        self.assertTrue(result["ready"])
        self.assertEqual(list(result["outputs"]), ["vertical_scheme3"])


if __name__ == "__main__":
    unittest.main()
