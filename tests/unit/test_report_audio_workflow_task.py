import json
import inspect
import os
import sys
import tempfile
import unittest
import wave
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask
from sqlalchemy import text


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from models import db  # noqa: E402
from models.task import PublicationTask  # noqa: E402
from tasks import publisher, report_audio_workflow  # noqa: E402
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
    _doubao_parallelism,
    _ordered_doubao_tts_outputs,
    _process_job,
    _publisher_script_subtitles,
    _publisher_sub_burn_enabled,
    _publisher_result_summary,
    _publisher_main_title,
    _publisher_cover_title_with_institution,
    _publisher_workbench_institution,
    _publisher_sub_title,
    _publisher_video_transform,
    _report_video_completion_contract,
    _task_chunk_attempt,
    _tts_text_hash,
    _valid_structured_result,
    _video_stage_enabled,
    _wait_child,
)
from tests.unit.test_report_audio_gem_v2 import (  # noqa: E402
    PDF_FILENAME as GEM_V2_PDF_FILENAME,
    valid_payload as valid_gem_v2_payload,
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
    def test_doubao_browser_tts_defaults_to_three_parallel_chunks(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(_doubao_parallelism(), 3)

    def test_scheme3_visual_resolution_uses_narrow_parent_projection(self):
        source = inspect.getsource(report_audio_workflow._resolve_scheme3_visual_inputs)

        self.assertIn(".options(load_only(", source)
        for column in (
            "PublicationTask.id",
            "PublicationTask.user_id",
            "PublicationTask.report_audio_pdf_attachment_id",
            "PublicationTask.report_audio_pdf_filename",
            "PublicationTask.report_audio_pdf_bytes",
        ):
            self.assertIn(column, source)
        self.assertNotIn(
            "PublicationTask.query.filter(PublicationTask.id == parent_id).one_or_none()",
            source,
        )

    def test_public_child_summary_keeps_sanitized_captcha_notification(self):
        summary = report_audio_workflow._task_public_summary({
            "job_id": "child-1",
            "status": "blocked_by_captcha",
            "current_step": "blocked_by_captcha",
            "progress": 45,
            "result": {
                "captcha_blocked": {
                    "requires_human_action": True,
                    "workflow_url": "http://localhost:5001/report-audio-workflow-detail.html?job_id=parent-1",
                },
                "notifications": {
                    "doubao_captcha_blocked": {
                        "sent": True,
                        "sent_at": "2026-08-12T08:34:14Z",
                        "to": "owner@example.test",
                    }
                },
            },
        })

        notification = summary["notifications"]["doubao_captcha_blocked"]
        self.assertTrue(notification["sent"])
        self.assertEqual(notification["recipient_hint"], "o***@example.test")
        self.assertNotIn("to", notification)
        self.assertTrue(summary["captcha_blocked"]["requires_human_action"])

    def test_full_video_completion_contract_requires_every_selected_output(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            landscape = root / "landscape.mp4"
            wav = root / "final.wav"
            landscape.write_bytes(b"video")
            wav.write_bytes(b"audio")

            with patch.object(
                report_audio_workflow,
                "COMPLETION_MEDIA_PROBE",
                side_effect=lambda path: (
                    {"duration": 30.0, "video_streams": 0, "audio_streams": 1}
                    if str(path).endswith(".wav")
                    else {"duration": 30.0, "video_streams": 1, "audio_streams": 1}
                ),
            ):
                result = _report_video_completion_contract(
                    {
                        "landscape_enabled": True,
                        "vertical_enabled": True,
                        "landscape_video_path": str(landscape),
                        "vertical_video_schemes": ["scheme3"],
                        "vertical_video_paths": {},
                    },
                    {"wav_path": str(wav)},
                    {
                        "video": {
                            "enabled": True,
                            "vertical_enabled": True,
                            "render_mode": "full_video",
                        }
                    },
                )

        self.assertFalse(result["ready"])
        self.assertEqual(result["missing"], ["vertical_scheme3"])

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

    def test_workbench_cover_title_always_uses_chinese_institution_prefix(self):
        title = _publisher_cover_title_with_institution(
            "决定商业航天生死的根本不是订单",
            "Nomura",
        )

        self.assertTrue(title.startswith("野村："))
        self.assertLessEqual(len(title.split("\n")), 2)

    def test_jefferies_cover_title_replaces_generic_prefix_once(self):
        title = _publisher_cover_title_with_institution(
            "机构：杰富瑞：周期逻辑失效，买错PCB暴跌461%",
            "Jefferies",
        )

        self.assertTrue(title.startswith("杰富瑞："))
        self.assertEqual(title.count("杰富瑞"), 1)
        self.assertNotIn("机构：", title)

    def test_jefferies_is_detected_from_pdf_filename(self):
        institution = _publisher_workbench_institution(
            {"title": "周期逻辑失效"},
            {"filename": "杰富瑞-中国科技行业-PCB更新报告.pdf"},
            {},
        )

        self.assertEqual(institution, "Jefferies")

    def test_bernstein_is_detected_and_replaces_legacy_shengbo_prefix(self):
        institution = _publisher_workbench_institution(
            {"cover_title": "盛博:存储见顶设备必跌？研报打破常识"},
            {"filename": "伯恩斯坦-全球半导体及设备-260713.pdf"},
            {},
        )
        title = _publisher_cover_title_with_institution(
            "机构研报：盛博:存储见顶设备必跌？研报打破常识",
            institution,
        )

        self.assertEqual(institution, "Bernstein")
        self.assertTrue(title.startswith("伯恩斯坦："))
        self.assertNotIn("机构", title)
        self.assertNotIn("盛博", title)

    def test_gfhk_filename_is_detected_as_hong_kong_gf_securities(self):
        institution = _publisher_workbench_institution(
            {"title": "广发证券：AI资本开支与平台价值转移"},
            {"filename": "GHHK - AI Capex & Platform Value Shift.pdf"},
            {},
        )
        title = _publisher_cover_title_with_institution(
            "机构：AI资本开支与平台价值转移\n2028年增速急刹车",
            institution,
        )

        self.assertEqual(institution, "GF Securities (Hong Kong)")
        self.assertTrue(title.startswith("香港广发："))
        self.assertNotIn("机构：", title)

    def test_cover_subtitle_is_single_line_and_bounded(self):
        subtitle = _format_cover_subtitle(
            "本报告解读摩根大通对亚洲被动元件、PCB-CCL、基板及测试行业的研究观点，重点关注需求复苏。"
        )

        self.assertNotIn("\n", subtitle)
        self.assertLessEqual(len(subtitle), 34)

    def test_publisher_title_helpers_select_landscape_and_vertical_variants(self):
        structured = {
            "landscape_title": "大摩:增持腾讯\n盈利预期上修",
            "landscape_subtitle": "目标价上调至700港元",
            "vertical_title": "腾讯盈利为何再超预期",
            "vertical_subtitle": "AI广告进入兑现期",
            "cover_title": "旧版兼容标题",
        }

        self.assertEqual(
            _publisher_main_title(structured, {}, variant="landscape"),
            "大摩:增持腾讯\n盈利预期上修",
        )
        self.assertEqual(
            _publisher_main_title(structured, {}, variant="vertical"),
            "腾讯盈利为何再超预期",
        )
        self.assertEqual(
            _publisher_sub_title(structured, {}, variant="vertical"),
            "AI广告进入兑现期",
        )

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
            patch.object(report_audio_workflow, "_persist_child_celery_task_id"), \
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
        self.assertTrue(child.params["bilibili_cover"]["enabled"])
        self.assertFalse(child.params["bilibili_cover"]["show_footer"])
        self.assertTrue(child.params["subtitle_alignment"]["required"])
        self.assertTrue(child.params["subtitle_alignment"]["strict"])
        self.assertEqual(
            child.params["vertical_video"]["subtitle_position"]["y_pct"],
            0.73,
        )
        for asset_key in (
            "thumbnail",
            "vertical_thumbnail",
            "vertical_cover",
            "podcast_cover",
            "bilibili_cover",
        ):
            with self.subTest(asset_key=asset_key):
                self.assertTrue(child.params[asset_key]["main_title"].startswith("机构："))

    def test_publisher_video_task_generates_both_selected_vertical_schemes(self):
        class FakePublisherTask:
            latest = None

            def __init__(self, **kwargs):
                self.__dict__.update(kwargs)
                self.id = "publisher-child-both"
                FakePublisherTask.latest = self

        fake_session = SimpleNamespace(add=lambda child: None, flush=lambda: None, commit=lambda: None)
        parent = SimpleNamespace(id="parent-both", created_at=datetime.utcnow())

        with patch.object(report_audio_workflow, "_find_existing_publisher_video_task", return_value=None), \
            patch.object(report_audio_workflow, "PublicationTask", FakePublisherTask), \
            patch.object(report_audio_workflow, "db", SimpleNamespace(session=fake_session)), \
            patch.object(
                report_audio_workflow.run_publication_pipeline,
                "apply_async",
                return_value=SimpleNamespace(id="celery-both"),
            ), \
            patch.object(report_audio_workflow, "_persist_child_celery_task_id"), \
            patch.object(report_audio_workflow, "_load_task", side_effect=lambda *args: FakePublisherTask.latest):
            child = _create_publisher_video_task(
                parent,
                audio={"wav_path": "/tmp/audio.wav", "seconds": 12},
                structured={
                    "title": "双竖屏测试",
                    "cover_title": "双竖屏测试",
                    "cover_subtitle": "一次生成方案一和方案二",
                    "final_script_for_tts": "这是一段用于测试两个竖屏方案的视频文案。",
                },
                pdf={"filename": "source.pdf"},
                pdf_screenshot={"path": "/tmp/source-page.jpg", "width": 100, "height": 100},
                payload={
                    "video": {
                        "enabled": True,
                        "vertical_enabled": True,
                        "vertical_video_schemes": ["scheme1", "scheme2"],
                        "vertical_video_scheme": "scheme2",
                        "bilibili_enabled": False,
                    }
                },
            )

        self.assertEqual(child.params["vertical_video"]["scheme"], "scheme2")
        self.assertTrue(child.params["vertical_video_2"]["enabled"])
        self.assertEqual(child.params["vertical_video_2"]["scheme"], "scheme1")
        self.assertEqual(child.params["metadata"]["vertical_video_schemes"], ["scheme1", "scheme2"])

    def test_publisher_video_task_includes_scheme3_scene_timeline(self):
        class FakePublisherTask:
            latest = None

            def __init__(self, **kwargs):
                self.__dict__.update(kwargs)
                self.id = "publisher-child-scheme3"
                FakePublisherTask.latest = self

        timeline = {
            "version": 1,
            "scene_count": 1,
            "scenes": [{"sequence": 1, "image_path": "/tmp/scene-1.png", "start_seconds": 0, "end_seconds": 12}],
        }
        fake_session = SimpleNamespace(add=lambda child: None, flush=lambda: None, commit=lambda: None)
        parent = SimpleNamespace(id="parent-scheme3", created_at=datetime.utcnow())

        with patch.object(report_audio_workflow, "_find_existing_publisher_video_task", return_value=None), \
            patch.object(
                report_audio_workflow,
                "_resolve_scheme3_visual_inputs",
                return_value={
                    "workflow_id": "visual-1",
                    "scene_timeline": timeline,
                    "video_path": None,
                    "status": "ready",
                    "step": "images_done",
                },
            ), \
            patch.object(report_audio_workflow, "PublicationTask", FakePublisherTask), \
            patch.object(report_audio_workflow, "db", SimpleNamespace(session=fake_session)), \
            patch.object(
                report_audio_workflow.run_publication_pipeline,
                "apply_async",
                return_value=SimpleNamespace(id="celery-scheme3"),
            ), \
            patch.object(report_audio_workflow, "_persist_child_celery_task_id"), \
            patch.object(report_audio_workflow, "_load_task", side_effect=lambda *args: FakePublisherTask.latest):
            child = _create_publisher_video_task(
                parent,
                audio={"wav_path": "/tmp/audio.wav", "seconds": 12},
                structured={
                    "title": "方案三测试",
                    "cover_title": "方案三测试",
                    "cover_subtitle": "分镜必须传给渲染器",
                    "final_script_for_tts": "这是一段用于测试方案三分镜视频的文案。",
                },
                pdf={"filename": "source.pdf"},
                pdf_screenshot={"path": "/tmp/source-page.jpg", "width": 100, "height": 100},
                payload={
                    "video": {
                        "enabled": True,
                        "vertical_enabled": True,
                        "vertical_video_schemes": ["scheme3"],
                        "bilibili_enabled": False,
                    }
                },
            )

        self.assertEqual(child.params["vertical_video"]["visual_scene_timeline"], timeline)
        self.assertEqual(child.params["metadata"]["visual_workflow_id"], "visual-1")

    def test_publisher_video_task_defers_scheme3_until_visual_assets_are_ready(self):
        parent = SimpleNamespace(id="parent-scheme3-wait", created_at=datetime.utcnow())
        with patch.object(report_audio_workflow, "_find_existing_publisher_video_task", return_value=None), \
            patch.object(
                report_audio_workflow,
                "_resolve_scheme3_visual_inputs",
                return_value={
                    "workflow_id": "visual-waiting",
                    "scene_timeline": None,
                    "video_path": None,
                    "status": "processing",
                    "step": "images_queued",
                },
            ):
            with self.assertRaises(report_audio_workflow.ChildTaskStillRunning) as raised:
                _create_publisher_video_task(
                    parent,
                    audio={"wav_path": "/tmp/audio.wav", "seconds": 12},
                    structured={"final_script_for_tts": "等待视觉素材。"},
                    pdf={"filename": "source.pdf"},
                    pdf_screenshot={"path": "/tmp/source-page.jpg"},
                    payload={
                        "video": {
                            "enabled": True,
                            "vertical_enabled": True,
                            "vertical_video_schemes": ["scheme3"],
                        }
                    },
                )

        self.assertEqual(raised.exception.label, "scheme3_visual")
        self.assertEqual(raised.exception.child_id, "visual-waiting")

    def test_scheme3_keyframe_uses_workbench_video_frame_without_visual_video(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            screenshot_path = Path(tmpdir) / "page.jpg"
            background_path = Path(tmpdir) / "scheme3.jpg"
            screenshot_path.write_bytes(b"page")
            background_path.write_bytes(b"background")
            with patch.object(report_audio_workflow, "_latest_report_audio_keyframe", return_value=None), \
                patch.object(
                    report_audio_workflow.thumbnail_service,
                    "generate_workbench_video_background",
                    return_value=str(background_path),
                ) as generate_background, \
                patch.object(
                    report_audio_workflow.thumbnail_service,
                    "generate_workbench_bilibili_cover",
                    return_value=str(background_path),
                ):
                result = report_audio_workflow._generate_report_audio_keyframes(
                    "parent-scheme3",
                    datetime.utcnow(),
                    structured={
                        "vertical_title": "方案三标题",
                        "vertical_subtitle": "方案三副标题",
                    },
                    pdf={"filename": "J.P. Morgan-report.pdf"},
                    audio={"seconds": 12},
                    pdf_screenshot={"path": str(screenshot_path)},
                    payload={
                        "video": {
                            "enabled": False,
                            "vertical_enabled": True,
                            "vertical_video_scheme": "scheme3",
                            "vertical_video_schemes": ["scheme3"],
                        }
                    },
                )

        self.assertEqual(result["vertical_video_scheme"], "scheme3")
        self.assertEqual(result["vertical_background_path"], str(background_path))
        self.assertIn("方案三", result["keyframes"]["vertical_background"]["label"])
        generate_background.assert_called_once()

    def test_scheme3_keyframe_selection_repairs_missing_vertical_enabled_flag(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            background_path = Path(tmpdir) / "scheme3.jpg"
            background_path.write_bytes(b"background")
            with patch.object(report_audio_workflow, "_latest_report_audio_keyframe", return_value=None), \
                patch.object(
                    report_audio_workflow.thumbnail_service,
                    "generate_workbench_video_background",
                    return_value=str(background_path),
                ) as generate_background, \
                patch.object(
                    report_audio_workflow.thumbnail_service,
                    "generate_workbench_bilibili_cover",
                    return_value=str(background_path),
                ):
                result = report_audio_workflow._generate_report_audio_keyframes(
                    "parent-scheme3",
                    datetime.utcnow(),
                    structured={"vertical_title": "方案三标题"},
                    pdf={"filename": "J.P. Morgan-report.pdf"},
                    audio={"seconds": 12},
                    pdf_screenshot={"path": "/tmp/source-page.jpg"},
                    payload={
                        "video": {
                            "enabled": False,
                            "vertical_enabled": False,
                            "vertical_video_scheme": "scheme3",
                            "vertical_video_schemes": ["scheme3"],
                        }
                    },
                )

        self.assertTrue(result["vertical_enabled"])
        self.assertIn("vertical_background", result["keyframes"])
        generate_background.assert_called_once()

    def test_scheme2_publisher_generates_bilibili_cover_without_footer(self):
        with patch.object(
            publisher.thumbnail_service,
            "generate_workbench_bilibili_cover",
            return_value="/tmp/bilibili-cover.jpg",
        ) as generate_cover:
            path = publisher._generate_scheme2_bilibili_cover(
                params={"enabled": True, "main_title": "方案二标题", "show_footer": False},
                main_title="默认标题",
                sub_title="副标题",
                screenshot_path="/tmp/page.jpg",
                base_name="job-1",
                institution="J.P. Morgan",
                emotion="bull",
                footer_text="听见资本",
                image_transform={"anchor": "top-left"},
            )

        self.assertEqual(path, "/tmp/bilibili-cover.jpg")
        generate_cover.assert_called_once()
        self.assertEqual(generate_cover.call_args.kwargs["output_filename"], "bcover2_job-1.jpg")
        self.assertFalse(generate_cover.call_args.kwargs["show_footer"])

    def test_keyframe_mode_also_generates_bilibili_cover_without_footer(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            landscape_path = tmp / "landscape.jpg"
            bilibili_path = tmp / "bilibili.jpg"
            landscape_path.write_bytes(b"landscape")
            bilibili_path.write_bytes(b"bilibili")

            with patch.object(report_audio_workflow.thumbnail_service, "generate_thumbnail", return_value=str(landscape_path)), \
                patch.object(
                    report_audio_workflow.thumbnail_service,
                    "generate_workbench_bilibili_cover",
                    return_value=str(bilibili_path),
                ) as generate_bilibili:
                result = report_audio_workflow._generate_report_audio_keyframes(
                    "parent-1",
                    datetime(2026, 7, 9, 12, 0, 0),
                    structured={
                        "cover_title": "核心大牛\n集体出走",
                        "cover_subtitle": "阿里AI被小摩看空?",
                        "final_script_for_tts": "测试文案",
                    },
                    pdf={"filename": "source.pdf"},
                    audio={"seconds": 60},
                    pdf_screenshot={"path": "/tmp/source-page.jpg"},
                    payload={
                        "video": {
                            "enabled": True,
                            "vertical_enabled": False,
                            "vertical_video_scheme": "scheme2",
                        }
                    },
                )

        generate_bilibili.assert_called_once()
        self.assertFalse(generate_bilibili.call_args.kwargs["show_footer"])
        self.assertEqual(generate_bilibili.call_args.kwargs["main_title"], "机构：核心大牛\n集体出走")
        self.assertEqual(result["bilibili_cover_path"], str(bilibili_path))
        self.assertIn("bilibili_cover", result["keyframes"])
        self.assertEqual(result["keyframes"]["bilibili_cover"]["label"], "横版封皮")
        self.assertEqual(result["keyframes"]["bilibili_cover"]["ratio"], "1146:717")

    def test_explicit_keyframe_outputs_generate_only_selected_artifacts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            bilibili_path = Path(tmpdir) / "bilibili.jpg"
            bilibili_path.write_bytes(b"bilibili")

            with patch.object(report_audio_workflow, "_latest_report_audio_keyframe", return_value=None), \
                patch.object(report_audio_workflow.thumbnail_service, "generate_thumbnail") as generate_landscape, \
                patch.object(
                    report_audio_workflow.thumbnail_service,
                    "generate_workbench_vertical_cover",
                ) as generate_vertical_cover, \
                patch.object(
                    report_audio_workflow.thumbnail_service,
                    "generate_workbench_bilibili_cover",
                    return_value=str(bilibili_path),
                ) as generate_bilibili:
                result = report_audio_workflow._generate_report_audio_keyframes(
                    "parent-explicit",
                    datetime(2026, 7, 28, 12, 0, 0),
                    structured={"cover_title": "测试标题", "cover_subtitle": "测试副标题"},
                    pdf={"filename": "source.pdf"},
                    audio={"seconds": 60},
                    pdf_screenshot={"path": "/tmp/source-page.jpg"},
                    payload={
                        "video": {
                            "enabled": False,
                            "vertical_enabled": False,
                            "keyframe_outputs": ["bilibili_cover"],
                        }
                    },
                )

        generate_bilibili.assert_called_once()
        generate_landscape.assert_not_called()
        generate_vertical_cover.assert_not_called()
        self.assertEqual(list(result["keyframes"]), ["bilibili_cover"])
        self.assertEqual(result["keyframe_outputs"], ["bilibili_cover"])
        self.assertFalse(result["landscape_enabled"])
        self.assertFalse(result["vertical_enabled"])

    def test_keyframe_outputs_enable_video_stage_without_video_versions(self):
        payload = {
            "video": {
                "enabled": False,
                "vertical_enabled": False,
                "render_mode": "keyframes",
                "keyframe_outputs": ["vertical_cover"],
            }
        }

        self.assertTrue(_video_stage_enabled(payload))

    def test_keyframe_mode_reuses_valid_files_after_partial_failure(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            landscape_path = tmp / "report_audio_keyframe_landscape_parent-1_1.jpg"
            bilibili_path = tmp / "report_audio_keyframe_bilibili_cover_parent-1_2.jpg"
            Image.new("RGB", (1920, 1080), "white").save(landscape_path)
            Image.new("RGB", (1146, 717), "black").save(bilibili_path)

            with patch.object(report_audio_workflow.thumbnail_service, "output_dir", str(tmp)), \
                patch.object(report_audio_workflow.thumbnail_service, "generate_thumbnail") as generate_landscape, \
                patch.object(report_audio_workflow.thumbnail_service, "generate_workbench_bilibili_cover") as generate_bilibili:
                result = report_audio_workflow._generate_report_audio_keyframes(
                    "parent-1",
                    datetime(2026, 7, 9, 12, 0, 0),
                    structured={"cover_title": "测试标题", "cover_subtitle": "测试副标题"},
                    pdf={"filename": "source.pdf"},
                    audio={"seconds": 60},
                    pdf_screenshot={"path": "/tmp/source-page.jpg"},
                    payload={"video": {"enabled": True, "vertical_enabled": False}},
                )

        generate_landscape.assert_not_called()
        generate_bilibili.assert_not_called()
        self.assertEqual(result["cover_path"], str(landscape_path))
        self.assertEqual(result["bilibili_cover_path"], str(bilibili_path))

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
                "bilibili_cover_path": "/tmp/bilibili-cover.jpg",
            }

        summary = _publisher_result_summary(FakePublisherTask(), {"path": "/tmp/page.jpg"})

        self.assertFalse(summary["landscape_enabled"])
        self.assertTrue(summary["vertical_enabled"])
        self.assertEqual(summary["video_path"], "/tmp/vertical.mp4")
        self.assertEqual(summary["vertical_video_path"], "/tmp/vertical.mp4")
        self.assertEqual(summary["bilibili_cover_path"], "/tmp/bilibili-cover.jpg")

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

    def test_closed_gemini_browser_is_retryable(self):
        message = "Page.evaluate: Target page, context or browser has been closed"

        self.assertTrue(_is_retryable_child_error("gemini", message))

    def test_missing_pcm_audio_child_is_retryable(self):
        self.assertTrue(
            _is_retryable_child_error("doubao", "朗读已触发但未捕获到 PCM 音频")
        )

    def test_worker_import_mismatch_does_not_create_another_doubao_attempt(self):
        text = "worker 导入版本不一致时不应消耗朗读重试次数。"
        metadata = {"chunk_index": 1, "chunk_attempt": 1, "text_hash": _tts_text_hash(text)}
        failed_task = SimpleNamespace(
            id="failed-child-1",
            celery_task_id="celery-failed-child-1",
            feature="doubao_browser_tts",
            parent_task_id="parent-1",
            source_task_id=None,
            created_at=datetime.utcnow(),
            completed_at=datetime.utcnow(),
            status="failed",
            current_step="failed",
            progress=0,
            error_message=(
                "Celery 子任务异常退出: FAILURE: cannot import name "
                "'public_browser_human_intervention_notification'"
            ),
            params={"doubao_browser_tts": {"text": text, "metadata": metadata}},
            result={"metadata": metadata},
        )

        with patch.object(report_audio_workflow, "_completed_tts_task_for_chunk", return_value=None), \
            patch.object(report_audio_workflow, "_active_tts_task_for_chunk", return_value=None), \
            patch.object(report_audio_workflow, "_failed_tts_tasks_for_chunk", return_value=[failed_task]), \
            patch.object(report_audio_workflow, "_clear_waiting_child"), \
            patch.object(report_audio_workflow, "_record_child_attempt"), \
            patch.object(report_audio_workflow, "_create_doubao_tts_task") as create_child:
            with self.assertRaisesRegex(RuntimeError, "worker_code_mismatch"):
                report_audio_workflow._run_doubao_tts_chunks(
                    SimpleNamespace(id="parent-1"),
                    "parent-1",
                    [text],
                    {},
                    retry_limit=1,
                )

        create_child.assert_not_called()

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

    def test_gemini_wait_defer_uses_saved_id_after_task_detaches(self):
        class DetachableTask:
            def __init__(self):
                self.detached = False
                self._fields = {
                    "id": "gemini-child-1",
                    "celery_task_id": "celery-gemini-child-1",
                    "status": "processing",
                    "current_step": "upload_pdf",
                    "progress": 20,
                    "error_message": None,
                    "result": {"logs": []},
                    "params": {"feature": "report_pdf_gemini"},
                    "feature": "report_pdf_gemini",
                    "parent_task_id": "parent-1",
                    "source_task_id": None,
                    "created_at": datetime.utcnow(),
                    "completed_at": None,
                }

            def __getattr__(self, name):
                if name not in self._fields:
                    raise AttributeError(name)
                if self.detached:
                    raise RuntimeError("detached task attribute refresh")
                return self._fields[name]

        child = DetachableTask()

        def detach_and_defer(parent_id, child_id, label, **kwargs):
            self.assertEqual(child_id, "gemini-child-1")
            child.detached = True
            raise report_audio_workflow.ChildTaskStillRunning(
                label,
                child_id,
                "processing",
                "upload_pdf",
            )

        with patch.object(report_audio_workflow, "_emit"), \
            patch.object(report_audio_workflow, "_create_gemini_task", return_value=child), \
            patch.object(report_audio_workflow, "_record_child"), \
            patch.object(report_audio_workflow, "_record_child_attempt"), \
            patch.object(report_audio_workflow, "_wait_child", side_effect=detach_and_defer), \
            patch.object(report_audio_workflow, "_load_task", return_value=None):
            with self.assertRaises(report_audio_workflow.ChildTaskStillRunning) as raised:
                report_audio_workflow._run_gemini_task_with_retries(
                    SimpleNamespace(id="parent-1"),
                    pdf={"filename": "report.pdf"},
                    pdf_path="/tmp/report.pdf",
                    prompt="解析研报",
                    followups=[],
                    payload={},
                    start_progress=10,
                    end_progress=52,
                )

        self.assertEqual(raised.exception.child_id, "gemini-child-1")

    def test_gemini_wait_transient_db_error_does_not_fail_or_revoke_child(self):
        child = SimpleNamespace(
            id="gemini-child-db-retry",
            celery_task_id="celery-gemini-db-retry",
            status="processing",
            current_step="upload_pdf",
            progress=20,
            error_message=None,
            result={"logs": []},
            params={"feature": "report_pdf_gemini"},
            feature="report_pdf_gemini",
            parent_task_id="parent-1",
            source_task_id=None,
            created_at=datetime.utcnow(),
            completed_at=None,
        )
        transient_error = RuntimeError("server closed the connection unexpectedly")

        with patch.object(report_audio_workflow, "_emit"), \
            patch.object(report_audio_workflow, "_create_gemini_task", return_value=child), \
            patch.object(report_audio_workflow, "_record_child"), \
            patch.object(report_audio_workflow, "_record_child_attempt"), \
            patch.object(report_audio_workflow, "_wait_child", side_effect=transient_error), \
            patch.object(report_audio_workflow, "_is_retryable_db_error", return_value=True), \
            patch.object(report_audio_workflow, "_revoke_child_task") as revoke_child, \
            patch.object(report_audio_workflow, "_mark_child_failed") as mark_child_failed:
            with self.assertRaisesRegex(RuntimeError, "server closed the connection unexpectedly"):
                report_audio_workflow._run_gemini_task_with_retries(
                    SimpleNamespace(id="parent-1"),
                    pdf={"filename": "report.pdf"},
                    pdf_path="/tmp/report.pdf",
                    prompt="解析研报",
                    followups=[],
                    payload={},
                    start_progress=10,
                    end_progress=52,
                )

        revoke_child.assert_not_called()
        mark_child_failed.assert_not_called()


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
        self.completion_probe_patch = patch.object(
            report_audio_workflow,
            "COMPLETION_MEDIA_PROBE",
            side_effect=lambda path: (
                {"duration": 1.0, "video_streams": 0, "audio_streams": 1}
                if str(path).endswith(".wav")
                else {"duration": 1.0, "video_streams": 1, "audio_streams": 1}
            ),
        )
        self.completion_probe_patch.start()

    def tearDown(self):
        self.completion_probe_patch.stop()
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _gem_v2_workflow(self, mode):
        root = Path(self.app.instance_path)
        root.mkdir(parents=True, exist_ok=True)
        pdf_path = root / GEM_V2_PDF_FILENAME
        pdf_path.write_bytes(b"%PDF-1.4\n% Gem V2 test PDF\n")
        workflow = PublicationTask(
            status="pending",
            current_step="queued",
            progress=0,
            params={
                "feature": "report_pdf_audio_workflow",
                "user_id": "1",
                "report_audio_workflow": {
                    "prompt": "旧版长调用提示词，不应在 V2 模式发送",
                    "review_prompt": "旧版 Gemini 通用复核，不应在 V2 模式发送",
                    "followups": ["旧版追问，也不应发送"],
                    "gem_v2_mode": mode,
                    "gem_v2_focus": "重点解释供需验证指标",
                    "gemini_min_final_chars": 20,
                    "force_gemini_regeneration": True,
                    "pdf_evidence_enabled": False,
                    "script_material_only": True,
                    "pdf_path": str(pdf_path),
                    "pdf": {
                        "attachment_id": f"gem-v2-{mode}",
                        "filename": GEM_V2_PDF_FILENAME,
                        "path": str(pdf_path),
                    },
                    "video": {"enabled": False},
                },
            },
            result={"children": {}, "logs": []},
        )
        db.session.add(workflow)
        db.session.commit()
        return workflow

    @staticmethod
    def _gemini_snapshot(raw_text):
        return {
            "job_id": "gem-v2-child",
            "status": "completed",
            "current_step": "done",
            "progress": 100,
            "result": {
                "initial_response_text": raw_text,
                "final_response_text": raw_text,
            },
        }

    @staticmethod
    def _legacy_structured():
        return {
            "title": "DeepSeek 兼容标题",
            "landscape_title": "DeepSeek 横屏标题",
            "landscape_subtitle": "兼容副标题",
            "vertical_title": "DeepSeek 竖屏标题",
            "vertical_subtitle": "兼容副标题",
            "cover_title": "DeepSeek 横屏标题",
            "cover_subtitle": "兼容副标题",
            "final_script_for_tts": "这是 DeepSeek 回退链路生成的完整可朗读正文，长度足以通过当前测试要求。",
        }

    def test_deepseek_structuring_runs_after_database_session_release(self):
        transaction_states = []

        db.session.execute(text("SELECT 1"))
        self.assertTrue(db.session().in_transaction())

        def normalize_without_database_session(*_args, **_kwargs):
            transaction_states.append(db.session().in_transaction())
            return self._legacy_structured()

        with patch.object(
            report_audio_workflow,
            "normalize_for_tts",
            side_effect=normalize_without_database_session,
        ):
            report_audio_workflow._structure_gemini_response(
                "Gemini 原始研报内容",
                initial_text="Gemini 初始内容",
                minimum_script_chars=20,
                gem_v2_mode="off",
                allow_external_information=False,
                pdf={"filename": "report.pdf"},
                pdf_path="/tmp/report.pdf",
                metadata={"workflow_task_id": "workflow-1"},
            )

        self.assertEqual([False], transaction_states)

    def test_gem_v2_shadow_uses_minimal_prompt_without_generic_followup_and_keeps_deepseek(self):
        workflow = self._gem_v2_workflow("shadow")
        workflow_id = workflow.id
        raw = json.dumps(valid_gem_v2_payload(), ensure_ascii=False)
        legacy = self._legacy_structured()

        with patch.dict(os.environ, {"REPORT_AUDIO_GEMINI_MIN_FINAL_CHARS": "20"}, clear=False), \
            patch.object(
                report_audio_workflow,
                "_run_gemini_task_with_retries",
                return_value=self._gemini_snapshot(raw),
            ) as run_gemini, \
            patch.object(report_audio_workflow, "normalize_for_tts", return_value=legacy) as normalize, \
            patch.object(report_audio_workflow, "review_report_cover_titles", return_value=legacy) as review, \
            patch.object(report_audio_workflow, "_queue_notification", return_value=True):
            ok = _process_job(workflow.id)

        self.assertTrue(ok)
        submitted = run_gemini.call_args.kwargs
        self.assertIn("report_audio_gem_v2", submitted["prompt"])
        self.assertIn(GEM_V2_PDF_FILENAME, submitted["prompt"])
        self.assertNotIn("旧版长调用提示词", submitted["prompt"])
        self.assertEqual(submitted["followups"], [])
        normalize.assert_called_once()
        review.assert_called_once()
        workflow = db.session.get(PublicationTask, workflow_id)
        self.assertTrue(workflow.result["gem_v2"]["valid"])
        self.assertEqual(workflow.result["gem_v2"]["route"], "shadow_deepseek")
        self.assertEqual(workflow.result["structured_result"]["title"], "DeepSeek 兼容标题")

    def test_gem_v2_direct_valid_response_bypasses_both_deepseek_stages(self):
        workflow = self._gem_v2_workflow("direct")
        raw = json.dumps(valid_gem_v2_payload(), ensure_ascii=False)

        with patch.dict(os.environ, {"REPORT_AUDIO_GEMINI_MIN_FINAL_CHARS": "20"}, clear=False), \
            patch.object(
                report_audio_workflow,
                "_run_gemini_task_with_retries",
                return_value=self._gemini_snapshot(raw),
            ), \
            patch.object(report_audio_workflow, "normalize_for_tts") as normalize, \
            patch.object(report_audio_workflow, "review_report_cover_titles") as review, \
            patch.object(report_audio_workflow, "_queue_notification", return_value=True):
            ok = _process_job(workflow.id)

        self.assertTrue(ok)
        normalize.assert_not_called()
        review.assert_not_called()
        db.session.refresh(workflow)
        self.assertEqual(workflow.result["gem_v2"]["route"], "direct")
        self.assertEqual(workflow.result["structured_result"]["schema_version"], "report_audio_gem_v2")
        self.assertEqual(workflow.result["structured_result"]["provider"], "gemini_gem")

    def test_gem_v2_direct_invalid_response_falls_back_to_both_deepseek_stages(self):
        workflow = self._gem_v2_workflow("direct")
        workflow_id = workflow.id
        invalid = valid_gem_v2_payload()
        invalid["schema_version"] = "report_audio_gem_v1"
        raw = json.dumps(invalid, ensure_ascii=False)
        legacy = self._legacy_structured()

        with patch.dict(os.environ, {"REPORT_AUDIO_GEMINI_MIN_FINAL_CHARS": "20"}, clear=False), \
            patch.object(
                report_audio_workflow,
                "_run_gemini_task_with_retries",
                return_value=self._gemini_snapshot(raw),
            ), \
            patch.object(report_audio_workflow, "normalize_for_tts", return_value=legacy) as normalize, \
            patch.object(report_audio_workflow, "review_report_cover_titles", return_value=legacy) as review, \
            patch.object(report_audio_workflow, "_queue_notification", return_value=True):
            ok = _process_job(workflow.id)

        self.assertTrue(ok)
        normalize.assert_called_once()
        review.assert_called_once()
        workflow = db.session.get(PublicationTask, workflow_id)
        self.assertEqual(workflow.result["gem_v2"]["route"], "deepseek_fallback")
        self.assertIn("schema_version", workflow.result["gem_v2"]["error_codes"])

    def test_gem_v2_child_declares_contract_for_one_targeted_repair(self):
        parent = SimpleNamespace(id="parent-gem-v2-contract", params={"user_id": "1"})
        pdf = {"filename": GEM_V2_PDF_FILENAME, "attachment_id": "asset-gem-v2"}

        with patch.object(
            report_audio_workflow,
            "apply_async_after_db_release",
            return_value=SimpleNamespace(id="celery-gem-v2"),
        ):
            child = report_audio_workflow._create_gemini_task(
                parent,
                pdf,
                "/tmp/gem-v2.pdf",
                "minimal trigger",
                [],
                {"gem_v2_mode": "shadow", "gemini_min_final_chars": 1500},
            )

        stored = db.session.get(PublicationTask, child.id)
        request = stored.params["report_pdf_gemini"]
        self.assertEqual(request["response_contract"], "report_audio_gem_v2")
        self.assertEqual(request["contract_expected_pdf_filename"], GEM_V2_PDF_FILENAME)
        self.assertEqual(request["contract_minimum_script_chars"], 1500)
        self.assertEqual(request["contract_max_repairs"], 1)

    def test_resumed_direct_result_does_not_run_deepseek_title_review(self):
        workflow = self._gem_v2_workflow("direct")
        payload = valid_gem_v2_payload()
        structured = dict(payload["reports"][0])
        structured.update({
            "schema_version": "report_audio_gem_v2",
            "batch_quality_control": payload["batch_quality_control"],
        })
        workflow.result = {
            "children": {},
            "logs": [],
            "structured_result": structured,
            "gem_v2": {"mode": "direct", "route": "direct", "valid": True},
        }
        db.session.commit()

        with patch.dict(os.environ, {"REPORT_AUDIO_GEMINI_MIN_FINAL_CHARS": "20"}, clear=False), \
            patch.object(report_audio_workflow, "normalize_for_tts") as normalize, \
            patch.object(report_audio_workflow, "review_report_cover_titles") as review, \
            patch.object(report_audio_workflow, "_queue_notification", return_value=True):
            ok = _process_job(workflow.id)

        self.assertTrue(ok)
        normalize.assert_not_called()
        review.assert_not_called()

    def test_deferred_parent_resume_persists_replacement_delivery_id(self):
        parent = PublicationTask(
            status="processing",
            current_step="doubao_1_waiting",
            progress=60,
            celery_task_id="old-delivery-id",
            params={
                "feature": "report_pdf_audio_workflow",
                "report_audio_workflow": {"pdf_path": "/tmp/report.pdf"},
            },
            result={"children": {}, "logs": []},
        )
        db.session.add(parent)
        db.session.commit()
        waiting = report_audio_workflow.ChildTaskStillRunning(
            "doubao_browser_1",
            "tts-child-1",
            "processing",
            "capture_audio",
        )

        with patch.object(
            report_audio_workflow,
            "apply_async_after_db_release",
            return_value=SimpleNamespace(id="replacement-delivery-id"),
        ) as publish:
            report_audio_workflow._defer_parent_retry(parent.id, waiting)

        db.session.expire_all()
        refreshed = db.session.get(PublicationTask, parent.id)
        self.assertEqual(refreshed.status, "processing")
        self.assertEqual(refreshed.celery_task_id, "replacement-delivery-id")
        self.assertEqual(refreshed.waiting_child_id, "tts-child-1")
        publish.assert_called_once_with(
            report_audio_workflow.process_report_audio_workflow_job,
            args=[parent.id],
            queue=report_audio_workflow.WORKFLOW_QUEUE_NAME,
            countdown=30,
            retry=False,
        )

    def test_completed_parent_ignores_duplicate_delayed_delivery(self):
        completed = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            params={
                "feature": "report_pdf_audio_workflow",
                "report_audio_workflow": {"pdf_path": "/tmp/already-done.pdf"},
            },
            result={"completed_at": datetime.utcnow().isoformat() + "Z"},
            completed_at=datetime.utcnow(),
        )
        db.session.add(completed)
        db.session.commit()

        with patch.object(report_audio_workflow, "_queue_notification") as queue_notification:
            ok = _process_job(completed.id)

        self.assertTrue(ok)
        queue_notification.assert_not_called()
        db.session.refresh(completed)
        self.assertEqual(completed.status, "completed")
        self.assertEqual(completed.current_step, "done")
        self.assertEqual(completed.progress, 100)

    def test_result_patch_updates_media_projection_columns(self):
        task = PublicationTask(
            status="processing",
            current_step="video",
            progress=90,
            params={"feature": "report_pdf_audio_workflow"},
            result={},
        )
        db.session.add(task)
        db.session.commit()

        report_audio_workflow._update_result(task.id, {
            "output_path": "/tmp/output",
            "audio": {
                "wav_path": "/tmp/audio.wav",
                "seconds": 12.5,
                "bytes": 2048,
            },
            "video": {
                "enabled": True,
                "status": "completed",
                "video_path": "/tmp/video.mp4",
                "cover_path": "/tmp/cover.jpg",
                "keyframes": {
                    "landscape_cover": {"path": "/tmp/landscape.jpg"},
                    "vertical_cover": {"path": "/tmp/vertical.jpg"},
                    "podcast_cover": {"path": "/tmp/podcast.jpg"},
                    "bilibili_cover": {"path": "/tmp/bilibili.jpg"},
                },
            },
        })

        db.session.refresh(task)
        self.assertEqual(task.report_audio_output_path, "/tmp/output")
        self.assertEqual(task.report_audio_wav_path, "/tmp/audio.wav")
        self.assertEqual(task.report_audio_seconds, "12.5")
        self.assertEqual(task.report_audio_audio_bytes, "2048")
        self.assertTrue(task.report_audio_video_enabled)
        self.assertEqual(task.report_audio_video_status, "completed")
        self.assertEqual(task.report_audio_video_path, "/tmp/video.mp4")
        self.assertEqual(task.report_audio_cover_path, "/tmp/cover.jpg")
        self.assertEqual(
            task.report_audio_landscape_keyframe_path,
            "/tmp/landscape.jpg",
        )
        self.assertEqual(task.report_audio_vertical_cover_path, "/tmp/vertical.jpg")
        self.assertEqual(task.report_audio_podcast_cover_path, "/tmp/podcast.jpg")
        self.assertEqual(task.report_audio_bilibili_cover_path, "/tmp/bilibili.jpg")

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

    def test_reusable_material_for_pdf_rejects_script_below_requested_minimum(self):
        structured = {
            "title": "旧短文案",
            "final_script_for_tts": "这是一段不足一千五百字的旧文案。" * 20,
        }
        existing = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            params={
                "feature": "report_script_material_workflow",
                "source_feature": "zsxq_attachment",
                "source_task_id": "asset-short",
                "report_script_material_workflow": {
                    "pdf": {"attachment_id": "asset-short", "filename": "report.pdf"},
                },
            },
            result={"structured_result": structured},
        )
        db.session.add(existing)
        db.session.commit()

        reusable = report_audio_workflow._reusable_material_for_pdf(
            {"attachment_id": "asset-short", "filename": "report.pdf"},
            exclude_task_id="new-task",
            minimum_chars=1500,
        )

        self.assertIsNone(reusable)

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
            patch.object(report_audio_workflow, "review_report_cover_titles", return_value=structured), \
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

    def test_active_tts_task_for_chunk_keeps_queued_child_during_worker_backlog(self):
        text = "这是一段仍在队列中等待浏览器 worker 的文本。"
        queued = PublicationTask(
            status="pending",
            current_step="queued",
            progress=0,
            params={
                "feature": "doubao_browser_tts",
                "parent_task_id": "parent-queued",
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
        db.session.add(queued)
        db.session.commit()

        with patch.dict(
            os.environ,
            {
                "REPORT_AUDIO_DOUBAO_STALE_SECONDS": "60",
                "REPORT_AUDIO_DOUBAO_QUEUE_STALE_SECONDS": "1800",
            },
            clear=False,
        ):
            active = _active_tts_task_for_chunk("parent-queued", 1, text)

        self.assertIsNotNone(active)
        db.session.refresh(queued)
        self.assertEqual(queued.status, "pending")

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

        db.session.refresh(first)
        first_snapshot = report_audio_workflow._task_state_snapshot(first)
        db.session.refresh(second)
        second_snapshot = report_audio_workflow._task_state_snapshot(second)

        task_ids, wav_paths = _ordered_doubao_tts_outputs(
            parent.id,
            chunks,
            {2: second_snapshot, 1: first_snapshot},
        )

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

        self.assertEqual(report_audio_workflow._task_state_snapshot(child)["status"], "completed")
        clear_waiting.assert_called_once_with(parent_id, "publisher_video")

    def test_wait_child_reloads_terminal_child_after_parent_summary_commit(self):
        parent = PublicationTask(
            status="processing",
            current_step="gemini_waiting",
            progress=50,
            params={"feature": "report_pdf_audio_workflow"},
            result={},
        )
        child = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            params={
                "feature": "report_pdf_gemini",
                "parent_task_id": None,
            },
            result={"final_response_text": "已完成的 Gemini 返回值" * 20},
            completed_at=datetime.utcnow(),
        )
        db.session.add_all([parent, child])
        db.session.flush()
        child.parent_task_id = parent.id
        child.params["parent_task_id"] = parent.id
        db.session.commit()

        with patch.object(report_audio_workflow, "_emit"), \
            patch.dict(os.environ, {"REPORT_AUDIO_CHILD_TIMEOUT_SECONDS": "1"}, clear=False):
            returned = _wait_child(parent.id, child.id, "gemini", start_progress=10, end_progress=52)

        snapshot = report_audio_workflow._task_state_snapshot(returned)
        self.assertEqual(snapshot["id"], child.id)
        self.assertEqual(snapshot["status"], "completed")
        self.assertEqual(snapshot["current_step"], "done")
        self.assertIn("Gemini", snapshot["result"]["final_response_text"])

        db.session.expire_all()
        refreshed_parent = PublicationTask.query.filter(PublicationTask.id == parent.id).one()
        self.assertEqual(refreshed_parent.result["children"]["gemini"]["job_id"], child.id)
        self.assertEqual(refreshed_parent.result["children"]["gemini"]["status"], "completed")

    def test_wait_child_freezes_waiting_status_before_parent_summary_commit(self):
        parent_id = "parent-waiting"
        child_id = "child-waiting"
        parent = SimpleNamespace(id=parent_id, status="processing")
        child = SimpleNamespace(
            id=child_id,
            status="processing",
            current_step="capture_audio",
            progress=40,
            celery_task_id="celery-child",
            result={},
            params={},
            error_message=None,
            feature="doubao_browser_tts",
            parent_task_id=parent_id,
            source_task_id=None,
            created_at=datetime.utcnow(),
            completed_at=None,
        )
        record_calls = {"count": 0}

        def fake_load_task(task_id, *columns):
            return parent if task_id == parent_id else child

        def fake_record_child(parent_task_id, label, value):
            record_calls["count"] += 1
            if record_calls["count"] == 2 and not isinstance(value, dict):
                value.status = None
                value.current_step = None

        with patch.object(report_audio_workflow, "_load_task", side_effect=fake_load_task), \
            patch.object(report_audio_workflow, "_emit"), \
            patch.object(report_audio_workflow, "_record_child", side_effect=fake_record_child), \
            patch.object(report_audio_workflow, "_celery_child_failure_message", return_value=None), \
            patch.object(report_audio_workflow.time, "monotonic", side_effect=[0.0, 2.0]), \
            patch.dict(os.environ, {"REPORT_AUDIO_CHILD_TIMEOUT_SECONDS": "1"}, clear=False):
            with self.assertRaises(report_audio_workflow.ChildTaskStillRunning) as raised:
                _wait_child(parent_id, child_id, "gemini", start_progress=10, end_progress=52)

        self.assertEqual(raised.exception.child_id, child_id)
        self.assertEqual(raised.exception.status, "processing")
        self.assertEqual(raised.exception.step, "capture_audio")

    def test_gemini_retry_runner_returns_snapshot_not_session_bound_orm(self):
        parent = SimpleNamespace(id="parent-1")
        created = SimpleNamespace(
            id="gemini-1",
            status="pending",
            current_step="queued",
            progress=0,
            celery_task_id="celery-1",
            result={},
            params={},
            error_message=None,
            feature="report_pdf_gemini",
            parent_task_id="parent-1",
            source_task_id=None,
            created_at=datetime.utcnow(),
            completed_at=None,
        )
        completed = SimpleNamespace(
            id="gemini-1",
            status="completed",
            current_step="done",
            progress=100,
            celery_task_id="celery-1",
            result={"final_response_text": "Gemini 完整返回" * 100},
            params={},
            error_message=None,
            feature="report_pdf_gemini",
            parent_task_id="parent-1",
            source_task_id=None,
            created_at=datetime.utcnow(),
            completed_at=datetime.utcnow(),
        )

        with patch.object(report_audio_workflow, "_emit"), \
            patch.object(report_audio_workflow, "_create_gemini_task", return_value=created), \
            patch.object(report_audio_workflow, "_wait_child", return_value=completed), \
            patch.object(report_audio_workflow, "_record_child"), \
            patch.object(report_audio_workflow, "_record_child_attempt"):
            returned = report_audio_workflow._run_gemini_task_with_retries(
                parent,
                pdf={"filename": "report.pdf"},
                pdf_path="/tmp/report.pdf",
                prompt="解析研报",
                followups=[],
                payload={},
                start_progress=10,
                end_progress=52,
            )

        self.assertIsInstance(returned, dict)
        self.assertEqual(returned["job_id"], "gemini-1")
        self.assertEqual(returned["status"], "completed")

    def test_pdf_video_workflow_reuses_script_material_and_cached_audio_before_video(self):
        root = Path(self.app.instance_path)
        root.mkdir(parents=True, exist_ok=True)
        asset_id = "asset-with-script-and-audio"
        pdf_path = root / "source.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\n% test pdf\n")
        script = "".join(
            f"观点{index}，这是一段可以复用的基础文案，用来模拟已经由 Gemini Gem 清洗后的口播稿，"
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
            patch.object(report_audio_workflow, "review_report_cover_titles", return_value=structured), \
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
            patch.object(report_audio_workflow, "review_report_cover_titles", return_value=structured), \
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
            patch.object(report_audio_workflow, "review_report_cover_titles", return_value=structured), \
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
