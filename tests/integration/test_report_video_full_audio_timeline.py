import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = PROJECT_ROOT / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from apis.v1.report_video import (  # noqa: E402
    _build_calibration_windows,
    _fit_linear_correction,
    _generate_timeline_entries_from_asr,
    _parse_timeline_text,
    _smart_retime_timeline_entries,
)


TIMELINE_PATH = PROJECT_ROOT / "tests" / "fixtures" / "report_video_test4_timeline.txt"
AUDIO_PATH = PROJECT_ROOT / "doubao朗读_20260607_200545.wav"


class ReportVideoFullAudioTimelineTest(unittest.TestCase):
    def test_parse_test4_simple_timeline(self):
        entries = _parse_timeline_text(TIMELINE_PATH.read_text(encoding="utf-8"))
        self.assertEqual(len(entries), 37)
        self.assertEqual(entries[0]["start_sec"], 0)
        self.assertEqual(entries[0]["end_sec"], 10)
        self.assertIn("快速看 3 份研报", entries[0]["text"])
        self.assertEqual(entries[-1]["start_sec"], 377)
        self.assertEqual(entries[-1]["end_sec"], 382)

    def test_build_calibration_windows(self):
        windows = _build_calibration_windows(360, sample_seconds=60, max_windows=3)
        self.assertEqual(len(windows), 3)
        self.assertEqual(windows[0], (0.0, 60.0))
        self.assertAlmostEqual(windows[1][0], 180.0, delta=0.01)
        self.assertAlmostEqual(windows[2][0], 300.0, delta=0.01)

    def test_fit_linear_correction(self):
        fit = _fit_linear_correction([
            {"timeline_mid": 10.0, "audio_mid": 12.0},
            {"timeline_mid": 50.0, "audio_mid": 52.0},
        ])
        self.assertIsNotNone(fit)
        self.assertAlmostEqual(fit["slope"], 1.0, delta=0.001)
        self.assertAlmostEqual(fit["intercept"], 2.0, delta=0.001)

    def test_generate_timeline_from_asr_uses_medium_cache_metadata(self):
        with tempfile.NamedTemporaryFile(suffix=".wav") as audio_file:
            audio_file.write(b"fake-audio")
            audio_file.flush()

            def fake_transcribe(source_path, **kwargs):
                self.assertEqual(kwargs.get("model_size"), "medium")
                self.assertTrue(kwargs.get("include_metadata"))
                return {
                    "segments": [
                        {"start": 0.0, "end": 2.5, "text": "测试字幕"},
                    ],
                    "metadata": {
                        "cache_hit": True,
                        "cache_key_prefix": "abc1234567",
                        "model_size": kwargs.get("model_size"),
                    },
                }

            with patch("services.asr_cache_service.transcribe_with_cache", side_effect=fake_transcribe):
                entries, meta = _generate_timeline_entries_from_asr(
                    task=None,
                    audio_path=audio_file.name,
                    target_duration=3.0,
                )

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["text"], "测试字幕")
        self.assertEqual(meta["model_size"], "medium")
        self.assertEqual(meta["asr_cache"]["cache_hit"], True)
        self.assertEqual(meta["asr_cache"]["cache_key_prefix"], "abc1234567")

    def test_smart_retime_with_fake_asr_sampler(self):
        entries = [
            {"start_sec": 0, "end_sec": 20, "text": "开场总览"},
            {"start_sec": 20, "end_sec": 40, "text": "日本国债"},
            {"start_sec": 40, "end_sec": 60, "text": "东盟银行"},
            {"start_sec": 60, "end_sec": 80, "text": "中国铝业"},
            {"start_sec": 80, "end_sec": 100, "text": "结尾总结"},
        ]

        sample_texts = {
            (0.0, 16.0): [{"text": "开场总览 日本国债"}],
            (24.0, 40.0): [{"text": "东盟银行"}],
            (48.0, 64.0): [{"text": "中国铝业 结尾总结"}],
        }

        def fake_sampler(audio_path, start_sec, end_sec, model_size):
            return sample_texts.get((round(start_sec, 1), round(end_sec, 1)), [])

        retimed, meta = _smart_retime_timeline_entries(
            entries,
            target_duration=80,
            audio_path="fake.wav",
            calibration_mode="smart",
            sampler=fake_sampler,
            sample_windows=[(0.0, 16.0), (24.0, 40.0), (48.0, 64.0)],
        )

        self.assertEqual(meta["strategy"], "sampled_asr")
        self.assertEqual(len(meta["anchors"]), 3)
        self.assertAlmostEqual(retimed[-1]["end_sec"], 80.0, delta=0.1)
        self.assertEqual(len(retimed), len(entries))

    @unittest.skipUnless(
        os.environ.get("RUN_REPORT_VIDEO_INTEGRATION") == "1",
        "需要云端 PostgreSQL 和本地 test-4 数据，默认不跑。",
    )
    def test_test4_upload_full_audio_with_timeline(self):
        from app import create_app
        from models.report_video import ReportVideoTask

        self.assertTrue(AUDIO_PATH.exists(), f"missing audio fixture: {AUDIO_PATH}")
        timeline_text = TIMELINE_PATH.read_text(encoding="utf-8")
        app = create_app()
        client = app.test_client()

        with app.app_context():
            task = (
                ReportVideoTask.query
                .filter_by(task_name="test-4")
                .order_by(ReportVideoTask.created_at.desc())
                .first()
            )
            self.assertIsNotNone(task)
            task_id = task.id

        with AUDIO_PATH.open("rb") as audio_file:
            response = client.post(
                f"/api/v1/report-video/tasks/{task_id}/audios",
                data={
                    "audio_mode": "full_audio_srt",
                    "timeline_text": timeline_text,
                    "files": (audio_file, AUDIO_PATH.name),
                },
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        payload = response.get_json()
        self.assertTrue(payload["success"])
        timeline = payload["data"]["timeline"]
        self.assertEqual(timeline["matched"], 12)
        task_payload = payload["data"]["task"]
        self.assertEqual(task_payload["result"]["audio_mode"], "full_audio_srt")
        self.assertEqual(task_payload["result"]["full_audio"]["matched_segment_count"], 12)
        self.assertIsNotNone(task_payload["result"]["full_audio"]["retime"])
        self.assertAlmostEqual(
            task_payload["result"]["full_audio"]["retime"]["target_duration_sec"],
            task_payload["result"]["full_audio"]["duration_sec"],
            delta=0.5,
        )
        self.assertGreaterEqual(len(task_payload["result"]["timeline_spans"]), 12)

    @unittest.skipUnless(
        os.environ.get("RUN_REPORT_VIDEO_RENDER") == "1",
        "会生成完整 16:9 视频，耗时较长，默认不跑。",
    )
    def test_test4_render_full_audio_video(self):
        from app import create_app
        from models.report_video import ReportVideoTask
        from tasks.report_video import generate_videos_task

        app = create_app()
        with app.app_context():
            task = (
                ReportVideoTask.query
                .filter_by(task_name="test-4")
                .order_by(ReportVideoTask.created_at.desc())
                .first()
            )
            self.assertIsNotNone(task)
            self.assertEqual((task.result or {}).get("audio_mode"), "full_audio_srt")
            task_id = task.id

        result = generate_videos_task(task_id, ["16:9"], None, True)
        self.assertIn(result["status"], {"completed", "video_partial"})
        self.assertTrue(result["generated"], result)
        output_path = PROJECT_ROOT / result["generated"][0]["file_path"]
        self.assertTrue(output_path.exists())
        self.assertGreater(output_path.stat().st_size, 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
