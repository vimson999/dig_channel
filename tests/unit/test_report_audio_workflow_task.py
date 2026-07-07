import os
import sys
import unittest
import wave
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from models import db  # noqa: E402
from models.task import PublicationTask  # noqa: E402
from tasks import report_audio_workflow  # noqa: E402
from tasks.report_audio_workflow import (  # noqa: E402
    CURRENT_STEP_MAX_LENGTH,
    _child_id_from_result,
    _child_inline_wait_seconds,
    _active_tts_task_for_chunk,
    _current_step_code,
    _create_doubao_tts_task,
    _create_publisher_video_task,
    _format_cover_subtitle,
    _format_cover_title,
    _is_retryable_child_error,
    _parent_max_defers,
    _parent_retry_delay_seconds,
    _parse_utc_datetime,
    _completed_tts_task_for_chunk,
    _ordered_doubao_tts_outputs,
    _process_job,
    _publisher_script_subtitles,
    _publisher_sub_burn_enabled,
    _publisher_result_summary,
    _publisher_video_transform,
    _task_chunk_attempt,
    _tts_text_hash,
    _valid_structured_result,
    _video_stage_enabled,
    _wait_child,
)


def _write_test_wav(path, seconds=1.0, sample_rate=16000):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = int(sample_rate * seconds)
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(sample_rate)
        writer.writeframes(b"\x00\x00" * frames)
    return path


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

    def test_publisher_video_task_respects_landscape_toggle_when_vertical_only(self):
        class FakePublisherTask:
            latest = None

            def __init__(self, **kwargs):
                self.__dict__.update(kwargs)
                self.id = "publisher-child"
                FakePublisherTask.latest = self

        fake_session = SimpleNamespace(add=lambda child: None, flush=lambda: None, commit=lambda: None)
        parent = SimpleNamespace(id="parent-1", created_at=datetime.utcnow())

        with patch.object(report_audio_workflow, "_find_existing_publisher_video_task", return_value=None), \
            patch.object(report_audio_workflow, "PublicationTask", FakePublisherTask), \
            patch.object(report_audio_workflow, "db", SimpleNamespace(session=fake_session)), \
            patch.object(
                report_audio_workflow.run_publication_pipeline,
                "apply_async",
                return_value=SimpleNamespace(id="celery-1"),
            ), \
            patch.object(report_audio_workflow, "_load_task", side_effect=lambda *args: FakePublisherTask.latest):
            child = _create_publisher_video_task(
                parent,
                audio={"wav_path": "/tmp/audio.wav", "seconds": 12},
                structured={
                    "title": "只生成竖屏测试",
                    "cover_title": "只生成竖屏测试",
                    "cover_subtitle": "横屏开关关闭时不应合成横屏",
                    "final_script_for_tts": "这是一段用于测试竖屏单独生成的视频文案。",
                },
                pdf={"filename": "source.pdf"},
                pdf_screenshot={"path": "/tmp/source-page.jpg", "width": 100, "height": 100},
                payload={
                    "video": {
                        "enabled": False,
                        "vertical_enabled": True,
                        "vertical_video_scheme": "scheme2",
                        "bilibili_enabled": False,
                    }
                },
            )

        self.assertFalse(child.params["video"]["enabled"])
        self.assertTrue(child.params["vertical_video"]["enabled"])
        self.assertTrue(child.params["vertical_thumbnail"]["enabled"])
        self.assertTrue(child.params["vertical_cover"]["enabled"])
        self.assertTrue(child.params["podcast_cover"]["enabled"])

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

    def test_tts_text_hash_ignores_whitespace_only_differences(self):
        self.assertEqual(_tts_text_hash("第一段\n第二段"), _tts_text_hash("第一段 第二段"))

    def test_doubao_failed_child_uses_snapshot_after_attempt_record(self):
        text = "豆包失败后仍然要保留原始错误。"
        metadata = {"chunk_index": 1, "chunk_attempt": 1, "text_hash": _tts_text_hash(text)}

        class DetachableTask:
            id = "failed-child-1"
            celery_task_id = "celery-failed-child-1"
            feature = "doubao_browser_tts"
            parent_task_id = "parent-1"
            source_task_id = None
            created_at = datetime.utcnow()
            completed_at = datetime.utcnow()

            def __init__(self):
                self.detached = False
                self._fields = {
                    "status": "failed",
                    "current_step": "failed",
                    "progress": 45,
                    "error_message": "失败尝试 1",
                    "params": {
                        "feature": "doubao_browser_tts",
                        "parent_task_id": "parent-1",
                        "doubao_browser_tts": {"text": text, "metadata": metadata},
                    },
                    "result": {"metadata": metadata, "error": "失败尝试 1"},
                }

            def _value(self, name):
                if self.detached:
                    raise RuntimeError("detached task attribute refresh")
                return self._fields[name]

            @property
            def status(self):
                return self._value("status")

            @property
            def current_step(self):
                return self._value("current_step")

            @property
            def progress(self):
                return self._value("progress")

            @property
            def error_message(self):
                return self._value("error_message")

            @property
            def params(self):
                return self._value("params")

            @property
            def result(self):
                return self._value("result")

        failed_task = DetachableTask()

        def detach_after_record(*args, **kwargs):
            failed_task.detached = True

        with patch.object(report_audio_workflow, "_completed_tts_task_for_chunk", return_value=None), \
            patch.object(report_audio_workflow, "_active_tts_task_for_chunk", return_value=None), \
            patch.object(report_audio_workflow, "_failed_tts_tasks_for_chunk", return_value=[failed_task]), \
            patch.object(report_audio_workflow, "_clear_waiting_child"), \
            patch.object(report_audio_workflow, "_record_child_attempt", side_effect=detach_after_record):
            with self.assertRaisesRegex(RuntimeError, "豆包浏览器朗读子任务失败: 失败尝试 1"):
                report_audio_workflow._run_doubao_tts_chunks(
                    SimpleNamespace(id="parent-1"),
                    "parent-1",
                    [text],
                    {},
                    retry_limit=0,
                )


class ReportAudioWorkflowMaterialReuseTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(self.app)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_reusable_material_for_pdf_reads_script_material_workflow(self):
        structured = {
            "title": "缓存文案",
            "final_script_for_tts": "这是一段可以复用的基础文案。" * 10,
        }
        existing = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            params={
                "feature": "report_script_material_workflow",
                "source_feature": "zsxq_attachment",
                "source_task_id": "asset-1",
                "report_script_material_workflow": {
                    "pdf": {"attachment_id": "asset-1", "filename": "report.pdf"},
                },
            },
            result={"structured_result": structured},
        )
        db.session.add(existing)
        db.session.commit()

        with patch.dict(os.environ, {"REPORT_AUDIO_GEMINI_MIN_FINAL_CHARS": "1"}, clear=False):
            reusable = report_audio_workflow._reusable_material_for_pdf(
                {"attachment_id": "asset-1", "filename": "report.pdf"},
                exclude_task_id="new-task",
            )

        self.assertIsNotNone(reusable)
        self.assertEqual(reusable["source_task_id"], existing.id)
        self.assertEqual(reusable["source_feature"], "report_script_material_workflow")
        self.assertEqual(reusable["structured_result"], structured)

    def test_script_material_only_preserves_reused_material_source(self):
        root = Path(self.app.instance_path)
        root.mkdir(parents=True, exist_ok=True)
        asset_id = "asset-script-only"
        pdf_path = root / "script-only-source.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\n% test pdf\n")
        structured = {
            "title": "基础文案来源测试",
            "final_script_for_tts": "这是一段已经存在的基础文案，新的基础文案任务应该直接复用它。" * 8,
        }
        existing = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            params={
                "feature": "report_script_material_workflow",
                "source_task_id": asset_id,
                "report_script_material_workflow": {
                    "pdf": {"attachment_id": asset_id, "filename": "script-only-source.pdf", "path": str(pdf_path)},
                },
            },
            result={"structured_result": structured},
            completed_at=datetime.utcnow(),
        )
        workflow = PublicationTask(
            status="pending",
            current_step="queued",
            progress=0,
            params={
                "feature": "report_pdf_audio_workflow",
                "user_id": "1",
                "report_audio_workflow": {
                    "prompt": "unused",
                    "review_prompt": "",
                    "followups": [],
                    "metadata": {"title": "基础文案来源测试"},
                    "script_material_only": True,
                    "pdf_path": str(pdf_path),
                    "pdf": {"attachment_id": asset_id, "filename": "script-only-source.pdf", "path": str(pdf_path)},
                    "video": {"enabled": False},
                },
            },
            result={"metadata": {"title": "基础文案来源测试"}, "logs": []},
        )
        db.session.add_all([existing, workflow])
        db.session.commit()

        with patch.dict(os.environ, {"REPORT_AUDIO_GEMINI_MIN_FINAL_CHARS": "10"}, clear=False), \
            patch.object(report_audio_workflow, "_run_gemini_task_with_retries") as run_gemini, \
            patch.object(report_audio_workflow, "_queue_notification", return_value=True):
            ok = _process_job(workflow.id)

        self.assertTrue(ok)
        run_gemini.assert_not_called()
        db.session.refresh(workflow)
        self.assertEqual(workflow.status, "completed")
        self.assertEqual(workflow.result["script_material"]["mode"], "reused_script_material")
        self.assertEqual(workflow.result["script_material"]["source_task_id"], existing.id)
        self.assertEqual(workflow.result["gemini"], None)

    def test_completed_tts_task_for_chunk_reuses_global_exact_text_audio(self):
        root = Path(self.app.instance_path)
        root.mkdir(parents=True, exist_ok=True)
        wav_path = root / "cached.wav"
        wav_path.write_bytes(b"RIFF----WAVE")
        text = "这是一段可以复用的豆包朗读文本。"
        cached = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            params={
                "feature": "doubao_browser_tts",
                "parent_task_id": "other-parent",
                "doubao_browser_tts": {
                    "text": text,
                    "metadata": {"chunk_index": 7, "text_hash": _tts_text_hash(text)},
                },
            },
            result={
                "metadata": {"chunk_index": 7, "text_hash": _tts_text_hash(text)},
                "audio": {"wav_path": str(wav_path), "seconds": 2.0},
            },
        )
        db.session.add(cached)
        db.session.commit()

        reusable = _completed_tts_task_for_chunk("new-parent", 1, text)

        self.assertIsNotNone(reusable)
        self.assertEqual(reusable.id, cached.id)

    def test_active_tts_task_for_chunk_releases_stale_processing_child(self):
        text = "这是一段卡住后应该释放重试的文本。"
        stale = PublicationTask(
            status="processing",
            current_step="download_wav",
            progress=88,
            params={
                "feature": "doubao_browser_tts",
                "parent_task_id": "parent-1",
                "doubao_browser_tts": {
                    "text": text,
                    "metadata": {"chunk_index": 1, "text_hash": _tts_text_hash(text)},
                },
            },
            result={
                "metadata": {"chunk_index": 1, "text_hash": _tts_text_hash(text)},
                "logs": [],
            },
            updated_at=datetime.utcnow() - timedelta(minutes=10),
        )
        db.session.add(stale)
        db.session.commit()

        with patch.dict(os.environ, {"REPORT_AUDIO_DOUBAO_STALE_SECONDS": "60"}, clear=False):
            active = _active_tts_task_for_chunk("parent-1", 1, text)

        self.assertIsNone(active)
        db.session.refresh(stale)
        self.assertEqual(stale.status, "failed")
        self.assertIn("stale", stale.error_message)

    def test_ordered_doubao_tts_outputs_uses_chunk_index_not_completion_order(self):
        root = Path(self.app.instance_path)
        root.mkdir(parents=True, exist_ok=True)
        first_wav = root / "1.wav"
        second_wav = root / "2.wav"
        first_wav.write_bytes(b"RIFF-1")
        second_wav.write_bytes(b"RIFF-2")
        chunks = ["第一段文本", "第二段文本"]
        first = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            params={
                "feature": "doubao_browser_tts",
                "doubao_browser_tts": {"text": chunks[0], "metadata": {"chunk_index": 1}},
            },
            result={"audio": {"wav_path": str(first_wav)}},
        )
        second = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            params={
                "feature": "doubao_browser_tts",
                "doubao_browser_tts": {"text": chunks[1], "metadata": {"chunk_index": 2}},
            },
            result={"audio": {"wav_path": str(second_wav)}},
        )
        parent = PublicationTask(
            status="processing",
            current_step="combine_audio",
            progress=92,
            params={"feature": "report_pdf_audio_workflow"},
            result={},
        )
        db.session.add_all([parent, second, first])
        db.session.commit()

        task_ids, wav_paths = _ordered_doubao_tts_outputs(parent.id, chunks, {2: second, 1: first})

        self.assertEqual(task_ids, [first.id, second.id])
        self.assertEqual(wav_paths, [str(first_wav), str(second_wav)])
        db.session.refresh(parent)
        self.assertEqual([item["chunk_index"] for item in parent.result["doubao_browser_order"]], [1, 2])

    def test_create_doubao_tts_task_protects_system_chunk_metadata(self):
        parent = PublicationTask(
            status="processing",
            current_step="doubao",
            progress=60,
            params={"feature": "report_pdf_audio_workflow", "user_id": "1"},
            result={},
        )
        db.session.add(parent)
        db.session.commit()

        class AsyncResult:
            id = "celery-doubao-1"

        payload = {
            "metadata": {
                "source": "report-audio-workflow-test-page",
                "parent_task_id": "wrong-parent",
                "chunk_index": 99,
                "chunk_total": 99,
                "chunk_attempt": 99,
                "retry_index": 99,
                "local_id": "local-1",
            }
        }
        with patch.object(report_audio_workflow.process_doubao_browser_tts_job, "apply_async", return_value=AsyncResult()):
            child = _create_doubao_tts_task(parent, "第一段文本", 2, 5, payload, attempt=3)

        metadata = child.params["doubao_browser_tts"]["metadata"]
        self.assertEqual(metadata["source"], report_audio_workflow.FEATURE_NAME)
        self.assertEqual(metadata["origin_source"], "report-audio-workflow-test-page")
        self.assertEqual(metadata["parent_task_id"], parent.id)
        self.assertEqual(metadata["chunk_index"], 2)
        self.assertEqual(metadata["chunk_total"], 5)
        self.assertEqual(metadata["chunk_attempt"], 3)
        self.assertEqual(metadata["retry_index"], 2)
        self.assertEqual(metadata["local_id"], "local-1")
        self.assertEqual(child.result["metadata"], metadata)
        self.assertEqual(child.celery_task_id, "celery-doubao-1")

    def test_wait_child_refreshes_stale_session_snapshot(self):
        parent_id = "parent-stale"
        child_id = "child-stale"
        parent = SimpleNamespace(id=parent_id, status="processing")
        stale_child = SimpleNamespace(
            id=child_id,
            status="processing",
            current_step="synthesis",
            progress=10,
            celery_task_id="celery-child",
            result={},
            error_message=None,
        )
        completed_child = SimpleNamespace(
            id=child_id,
            status="completed",
            current_step="done",
            progress=100,
            celery_task_id="celery-child",
            result={"video_path": "/tmp/done.mp4"},
            error_message=None,
        )
        refreshed = {"value": False}

        def fake_load_task(task_id, *columns):
            if task_id == parent_id:
                return parent
            if task_id == child_id:
                return completed_child if refreshed["value"] else stale_child
            return None

        def fake_expire_all():
            refreshed["value"] = True

        with patch.object(report_audio_workflow.db.session, "expire_all", side_effect=fake_expire_all), \
            patch.object(report_audio_workflow, "_load_task", side_effect=fake_load_task), \
            patch.object(report_audio_workflow, "_emit"), \
            patch.object(report_audio_workflow, "_record_child"), \
            patch.object(report_audio_workflow, "_clear_waiting_child") as clear_waiting, \
            patch.object(report_audio_workflow, "_celery_child_failure_message", return_value=None), \
            patch.object(report_audio_workflow, "maybe_requeue_stale_publisher_child", side_effect=lambda child, log=None: (child, False)), \
            patch.dict(os.environ, {"REPORT_AUDIO_PUBLISHER_INLINE_WAIT_SECONDS": "0"}, clear=False):
            child = _wait_child(parent_id, child_id, "publisher_video", start_progress=95, end_progress=99)

        self.assertEqual(child.status, "completed")
        clear_waiting.assert_called_once_with(parent_id, "publisher_video")

    def test_pdf_video_workflow_reuses_script_material_and_cached_audio_before_video(self):
        root = Path(self.app.instance_path)
        root.mkdir(parents=True, exist_ok=True)
        asset_id = "asset-with-script-and-audio"
        pdf_path = root / "source.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\n% test pdf\n")
        script = "".join(
            f"第{index}部分：这是一段可以复用的基础文案，用来模拟已经由 Gemini Gem 清洗后的口播稿，"
            f"并且保留唯一编号 {index}，避免不同分段文本完全相同。"
            for index in range(1, 13)
        )
        structured = {
            "title": "缓存复用测试",
            "cover_title": "缓存复用测试",
            "final_script_for_tts": script,
        }
        material = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            params={
                "feature": "report_script_material_workflow",
                "source_task_id": asset_id,
                "report_script_material_workflow": {
                    "pdf": {"attachment_id": asset_id, "filename": "source.pdf"},
                },
            },
            result={"structured_result": structured},
            completed_at=datetime.utcnow(),
        )
        db.session.add(material)
        db.session.commit()

        with patch.dict(
            os.environ,
            {
                "REPORT_AUDIO_GEMINI_MIN_FINAL_CHARS": "10",
                "REPORT_AUDIO_TTS_CHUNK_CHARS": "220",
            },
            clear=False,
        ):
            chunks = report_audio_workflow.split_tts_text(script)

        cached_audio_tasks = []
        for index, chunk in enumerate(chunks, start=1):
            wav_path = _write_test_wav(root / f"cached-{index}.wav", seconds=20)
            task = PublicationTask(
                status="completed",
                current_step="done",
                progress=100,
                params={
                    "feature": "doubao_browser_tts",
                    "parent_task_id": "older-parent",
                    "doubao_browser_tts": {
                        "text": chunk,
                        "metadata": {
                            "chunk_index": index,
                            "chunk_total": len(chunks),
                            "text_hash": _tts_text_hash(chunk),
                        },
                    },
                },
                result={
                    "metadata": {
                        "chunk_index": index,
                        "chunk_total": len(chunks),
                        "text_hash": _tts_text_hash(chunk),
                        "worker_id": f"worker_{index}",
                    },
                    "audio": {"wav_path": str(wav_path), "seconds": 20, "bytes": wav_path.stat().st_size},
                },
                completed_at=datetime.utcnow(),
            )
            cached_audio_tasks.append(task)
        db.session.add_all(cached_audio_tasks)
        db.session.commit()

        workflow = PublicationTask(
            status="pending",
            current_step="queued",
            progress=0,
            params={
                "feature": "report_pdf_audio_workflow",
                "user_id": "1",
                "report_audio_workflow": {
                    "prompt": "unused",
                    "review_prompt": "",
                    "followups": [],
                    "metadata": {"title": "缓存复用测试"},
                    "script_material_only": False,
                    "pdf_path": str(pdf_path),
                    "pdf": {"attachment_id": asset_id, "filename": "source.pdf", "path": str(pdf_path)},
                    "video": {"enabled": True, "vertical_enabled": True, "bilibili_enabled": False},
                },
            },
            result={
                "metadata": {"title": "缓存复用测试"},
                "pdf": {"attachment_id": asset_id, "filename": "source.pdf", "path": str(pdf_path)},
                "children": {},
                "structured_result": None,
                "audio": None,
                "video": None,
                "logs": [],
            },
        )
        publisher = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            params={
                "feature": "publisher",
                "source_feature": "report_pdf_audio_workflow",
                "source_task_id": "filled-after-flush",
                "video": {"enabled": True},
                "vertical_video": {"enabled": True},
            },
            result={
                "video_path": str(root / "video.mp4"),
                "vertical_video_path": str(root / "vertical.mp4"),
                "cover_path": str(root / "cover.jpg"),
            },
            completed_at=datetime.utcnow(),
        )
        db.session.add_all([workflow, publisher])
        db.session.flush()
        publisher.source_task_id = workflow.id
        publisher.params["source_task_id"] = workflow.id
        db.session.commit()

        screenshot = {"path": str(root / "first-page.jpg"), "bytes": 10, "width": 100, "height": 100}
        with patch.dict(
            os.environ,
            {
                "REPORT_AUDIO_GEMINI_MIN_FINAL_CHARS": "10",
                "REPORT_AUDIO_TTS_CHUNK_CHARS": "220",
            },
            clear=False,
        ), patch.object(report_audio_workflow, "_run_gemini_task_with_retries") as run_gemini, \
            patch.object(report_audio_workflow, "_create_doubao_tts_task") as create_doubao, \
            patch.object(report_audio_workflow, "render_pdf_first_page_image", return_value=screenshot), \
            patch.object(report_audio_workflow, "_create_publisher_video_task", return_value=publisher) as create_video, \
            patch.object(report_audio_workflow, "_queue_notification", return_value=True):
            ok = _process_job(workflow.id)

        self.assertTrue(ok)
        run_gemini.assert_not_called()
        create_doubao.assert_not_called()
        create_video.assert_called_once()
        db.session.refresh(workflow)
        self.assertEqual(workflow.status, "completed")
        self.assertEqual(workflow.current_step, "done")
        self.assertEqual(workflow.result["script_material"]["source_task_id"], material.id)
        self.assertEqual(
            [item["task_id"] for item in workflow.result["doubao_browser_order"]],
            [task.id for task in cached_audio_tasks],
        )
        self.assertTrue(workflow.result["audio"]["wav_path"].endswith(f"{workflow.id}.wav"))
        self.assertEqual(workflow.result["video"]["publisher_task_id"], publisher.id)

    def test_pdf_video_workflow_reuses_existing_final_audio_before_video(self):
        root = Path(self.app.instance_path)
        root.mkdir(parents=True, exist_ok=True)
        asset_id = "asset-with-final-audio"
        pdf_path = root / "source-final-audio.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\n% test pdf\n")
        final_wav = _write_test_wav(root / "existing-final.wav", seconds=20)
        script = "这是一段历史编排已经生成过最终音频的口播稿。它应该被新的视频编排直接复用。" * 8
        structured = {
            "title": "最终音频复用测试",
            "cover_title": "最终音频复用测试",
            "final_script_for_tts": script,
        }
        historical = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            params={
                "feature": "report_pdf_audio_workflow",
                "source_task_id": asset_id,
                "report_audio_workflow": {
                    "pdf": {"attachment_id": asset_id, "filename": "source-final-audio.pdf"},
                },
            },
            result={
                "structured_result": structured,
                "audio": {"wav_path": str(final_wav), "seconds": 20, "bytes": final_wav.stat().st_size},
            },
            completed_at=datetime.utcnow(),
        )
        workflow = PublicationTask(
            status="pending",
            current_step="queued",
            progress=0,
            params={
                "feature": "report_pdf_audio_workflow",
                "user_id": "1",
                "report_audio_workflow": {
                    "prompt": "unused",
                    "review_prompt": "",
                    "followups": [],
                    "metadata": {"title": "最终音频复用测试"},
                    "script_material_only": False,
                    "pdf_path": str(pdf_path),
                    "pdf": {"attachment_id": asset_id, "filename": "source-final-audio.pdf", "path": str(pdf_path)},
                    "video": {"enabled": True, "vertical_enabled": False, "bilibili_enabled": False},
                },
            },
            result={
                "metadata": {"title": "最终音频复用测试"},
                "pdf": {"attachment_id": asset_id, "filename": "source-final-audio.pdf", "path": str(pdf_path)},
                "children": {},
                "structured_result": None,
                "audio": None,
                "video": None,
                "logs": [],
            },
        )
        publisher = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            params={
                "feature": "publisher",
                "source_feature": "report_pdf_audio_workflow",
                "source_task_id": "filled-after-flush",
                "video": {"enabled": True},
                "vertical_video": {"enabled": False},
            },
            result={"video_path": str(root / "video-final-audio.mp4")},
            completed_at=datetime.utcnow(),
        )
        db.session.add_all([historical, workflow, publisher])
        db.session.flush()
        publisher.source_task_id = workflow.id
        publisher.params["source_task_id"] = workflow.id
        db.session.commit()

        screenshot = {"path": str(root / "first-page-final.jpg"), "bytes": 10, "width": 100, "height": 100}
        with patch.dict(os.environ, {"REPORT_AUDIO_GEMINI_MIN_FINAL_CHARS": "10"}, clear=False), \
            patch.object(report_audio_workflow, "_run_gemini_task_with_retries") as run_gemini, \
            patch.object(report_audio_workflow, "_run_doubao_tts_chunks") as run_doubao, \
            patch.object(report_audio_workflow, "combine_wav_files") as combine_audio, \
            patch.object(report_audio_workflow, "render_pdf_first_page_image", return_value=screenshot), \
            patch.object(report_audio_workflow, "_create_publisher_video_task", return_value=publisher) as create_video, \
            patch.object(report_audio_workflow, "_queue_notification", return_value=True):
            ok = _process_job(workflow.id)

        self.assertTrue(ok)
        run_gemini.assert_not_called()
        run_doubao.assert_not_called()
        combine_audio.assert_not_called()
        create_video.assert_called_once()
        db.session.refresh(workflow)
        self.assertEqual(workflow.status, "completed")
        self.assertEqual(workflow.result["script_material"]["source_task_id"], historical.id)
        self.assertEqual(workflow.result["audio"]["wav_path"], str(final_wav))
        self.assertEqual(workflow.result["audio_reuse"]["source_task_id"], historical.id)
        self.assertEqual(workflow.result["video"]["publisher_task_id"], publisher.id)

    def test_pdf_video_workflow_finishes_existing_completed_publisher_child(self):
        root = Path(self.app.instance_path)
        root.mkdir(parents=True, exist_ok=True)
        asset_id = "asset-final-video-child"
        pdf_path = root / "source-existing-video-child.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\n% test pdf\n")
        final_wav = _write_test_wav(root / "existing-child-final.wav", seconds=20)
        video_path = root / "existing-child-video.mp4"
        video_path.write_bytes(b"video")
        script = "这是一段用于恢复已完成视频子任务的口播稿，父任务应该复用文案和最终音频后直接收口。" * 8
        structured = {
            "title": "已完成视频子任务恢复测试",
            "cover_title": "已完成视频子任务恢复测试",
            "final_script_for_tts": script,
        }
        historical = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            params={
                "feature": "report_pdf_audio_workflow",
                "source_task_id": asset_id,
                "report_audio_workflow": {
                    "pdf": {"attachment_id": asset_id, "filename": "source-existing-video-child.pdf"},
                },
            },
            result={
                "structured_result": structured,
                "audio": {"wav_path": str(final_wav), "seconds": 20, "bytes": final_wav.stat().st_size},
            },
            completed_at=datetime.utcnow(),
        )
        workflow = PublicationTask(
            status="processing",
            current_step="pub_wait_exist",
            progress=95,
            params={
                "feature": "report_pdf_audio_workflow",
                "user_id": "1",
                "report_audio_workflow": {
                    "prompt": "unused",
                    "review_prompt": "",
                    "followups": [],
                    "metadata": {"title": "已完成视频子任务恢复测试"},
                    "script_material_only": False,
                    "pdf_path": str(pdf_path),
                    "pdf": {"attachment_id": asset_id, "filename": "source-existing-video-child.pdf", "path": str(pdf_path)},
                    "video": {"enabled": True, "vertical_enabled": False, "bilibili_enabled": False},
                },
            },
            result={
                "metadata": {"title": "已完成视频子任务恢复测试"},
                "pdf": {"attachment_id": asset_id, "filename": "source-existing-video-child.pdf", "path": str(pdf_path)},
                "children": {},
                "waiting_child": {"label": "publisher_video", "child_id": "filled-after-flush"},
                "logs": [],
            },
        )
        publisher = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            params={
                "feature": "publisher",
                "source_feature": "report_pdf_audio_workflow",
                "source_task_id": "filled-after-flush",
                "video": {"enabled": True},
                "vertical_video": {"enabled": False},
            },
            result={"video_path": str(video_path), "cover_path": str(root / "existing-child-cover.jpg")},
            completed_at=datetime.utcnow(),
        )
        db.session.add_all([historical, workflow, publisher])
        db.session.flush()
        publisher.source_task_id = workflow.id
        publisher.params["source_task_id"] = workflow.id
        workflow_result = dict(workflow.result or {})
        workflow_result["structured_result"] = structured
        workflow_result["script_material"] = {
            "status": "completed",
            "mode": "reused_script_material",
            "source_task_id": historical.id,
            "source_feature": historical.feature,
            "chars": len(script),
            "chunks": 1,
        }
        workflow_result["children"] = {
            "publisher_video": {
                "job_id": publisher.id,
                "status": "processing",
                "step": "synthesis",
            }
        }
        workflow_result["waiting_child"] = dict(workflow_result["waiting_child"])
        workflow_result["waiting_child"]["child_id"] = publisher.id
        workflow.result = workflow_result
        db.session.commit()

        screenshot = {"path": str(root / "first-page-existing-child.jpg"), "bytes": 10, "width": 100, "height": 100}
        with patch.dict(os.environ, {"REPORT_AUDIO_GEMINI_MIN_FINAL_CHARS": "10"}, clear=False), \
            patch.object(report_audio_workflow, "_run_gemini_task_with_retries") as run_gemini, \
            patch.object(report_audio_workflow, "_run_doubao_tts_chunks") as run_doubao, \
            patch.object(report_audio_workflow, "combine_wav_files") as combine_audio, \
            patch.object(report_audio_workflow, "render_pdf_first_page_image", return_value=screenshot), \
            patch.object(report_audio_workflow, "_create_publisher_video_task") as create_video, \
            patch.object(report_audio_workflow, "_queue_notification", return_value=True):
            ok = _process_job(workflow.id)

        self.assertTrue(ok)
        run_gemini.assert_not_called()
        run_doubao.assert_not_called()
        combine_audio.assert_not_called()
        create_video.assert_not_called()
        db.session.refresh(workflow)
        self.assertEqual(workflow.status, "completed")
        self.assertNotIn("waiting_child", workflow.result)
        self.assertEqual(workflow.result["script_material"]["source_task_id"], historical.id)
        self.assertEqual(workflow.result["audio_reuse"]["source_task_id"], historical.id)
        self.assertEqual(workflow.result["video"]["publisher_task_id"], publisher.id)
        self.assertEqual(workflow.result["video"]["video_path"], str(video_path))


if __name__ == "__main__":
    unittest.main()
