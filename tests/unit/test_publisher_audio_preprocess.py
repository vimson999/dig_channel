import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = ROOT / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.mp3_video_workflow import _audio_segment_signature  # noqa: E402
from tasks import publisher  # noqa: E402


class PublisherAudioPreprocessTest(unittest.TestCase):
    def test_expected_duration_applies_speed(self):
        expected = publisher._expected_prepared_audio_duration(
            source_duration=4283.35,
            start_offset=867.9,
            end_seconds=2367.9,
            speed=1.2,
        )

        self.assertAlmostEqual(expected, 1250.0, places=2)

    def test_ffmpeg_command_trims_before_input_when_speeding_audio(self):
        cmd = publisher._build_audio_preprocess_command(
            "source.mp3",
            "edit.mp3",
            start_offset=867.9,
            end_seconds=2367.9,
            speed=1.2,
        )

        self.assertLess(cmd.index("-t"), cmd.index("-i"))
        self.assertEqual(cmd[cmd.index("-filter:a") + 1], "atempo=1.2")

    def test_bad_cached_edit_is_rebuilt(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            raw = os.path.join(tmpdir, "source.mp3")
            edit = os.path.join(tmpdir, "source_edit_v2_s1.2_st867.9_eo1915.45.mp3")
            Path(raw).write_bytes(b"raw-audio")
            Path(edit).write_bytes(b"stale-edit")
            os.utime(raw, (1000, 1000))
            os.utime(edit, (2000, 2000))

            with patch.object(publisher, "_probe_audio_duration", return_value=1500.05):
                self.assertTrue(publisher._prepared_audio_needs_rebuild(edit, raw, 1250.0))

    def test_matching_cached_edit_is_reused(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            raw = os.path.join(tmpdir, "source.mp3")
            edit = os.path.join(tmpdir, "source_edit_v2_s1.2_st867.9_eo1915.45.mp3")
            Path(raw).write_bytes(b"raw-audio")
            Path(edit).write_bytes(b"fresh-edit")
            os.utime(raw, (1000, 1000))
            os.utime(edit, (2000, 2000))

            with patch.object(publisher, "_probe_audio_duration", return_value=1249.8):
                self.assertFalse(publisher._prepared_audio_needs_rebuild(edit, raw, 1250.0))

    def test_audio_signature_changes_with_speed(self):
        base = {"path": "/tmp/source.mp3", "start": 10, "end_offset": 5, "speed": 1.2}
        slower = {**base, "speed": 1.0}

        self.assertNotEqual(_audio_segment_signature(base), _audio_segment_signature(slower))

    def test_forced_alignment_runs_asr_even_when_estimated_subtitles_exist(self):
        subtitles = [[{"start": 0, "end": 2, "text": "已有但只是估算的字幕"}]]

        self.assertTrue(publisher._should_trigger_asr(
            asr_enabled=False,
            sub_burn_enabled=True,
            subtitles=subtitles,
            alignment_required=True,
        ))
        self.assertFalse(publisher._should_trigger_asr(
            asr_enabled=False,
            sub_burn_enabled=True,
            subtitles=subtitles,
            alignment_required=False,
        ))


if __name__ == "__main__":
    unittest.main()
