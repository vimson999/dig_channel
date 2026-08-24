import json
import math
import struct
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask
from PIL import Image, ImageDraw


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = PROJECT_ROOT / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.report_stick_figure_video import (  # noqa: E402
    normalize_render_options,
    probe_video,
    render_scene_timeline,
)
from services.report_stick_figure_audio_alignment import (  # noqa: E402
    align_scene_timeline_to_audio,
    align_storyboard_to_audio,
    align_subtitles_to_audio,
)
from models import (  # noqa: E402
    ReportStickFigureAudioTrack,
    ReportStickFigureSceneAsset,
    db,
)
from models.task import PublicationTask  # noqa: E402
from tasks.report_stick_figure_video import (  # noqa: E402
    maybe_auto_enqueue_stick_figure_video,
)


class ReportStickFigureVideoRenderTest(unittest.TestCase):
    def _wav(self, path: Path, seconds: float, frequency: int) -> None:
        sample_rate = 16000
        frames = int(sample_rate * seconds)
        with wave.open(str(path), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(sample_rate)
            samples = (
                struct.pack("<h", int(9000 * math.sin(2 * math.pi * frequency * index / sample_rate)))
                for index in range(frames)
            )
            audio.writeframes(b"".join(samples))

    def test_render_uses_each_audio_duration_for_matching_image(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            scenes = []
            expected_duration = 0.0
            for sequence, seconds in enumerate((0.30, 0.45, 0.70)):
                image_path = root / f"{sequence:03d}.png"
                audio_path = root / f"{sequence:03d}.wav"
                image = Image.new("RGB", (960, 540), "white")
                ImageDraw.Draw(image).text((40, 40), f"scene {sequence}", fill="black")
                image.save(image_path)
                self._wav(audio_path, seconds, 300 + sequence * 100)
                scenes.append({
                    "asset_id": f"asset-{sequence}",
                    "sequence": sequence,
                    "image_path": str(image_path),
                    "audio_path": str(audio_path),
                })
                if sequence >= 1:
                    expected_duration += seconds

            with patch("services.report_stick_figure_video.workflow_output_root", return_value=root):
                result = render_scene_timeline("workflow-test", scenes, width=640, height=360, fps=12)

            output = Path(result["video_path"])
            manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
            metadata = probe_video(str(output))
            self.assertTrue(output.is_file())
            self.assertEqual(manifest["scene_count"], 2)
            self.assertTrue(manifest["title_scene_excluded"])
            self.assertEqual(manifest["body_start_sequence"], 1)
            self.assertEqual([item["sequence"] for item in manifest["timeline"]], [1, 2])
            self.assertAlmostEqual(manifest["timeline"][0]["duration_seconds"], 0.45, delta=0.01)
            self.assertAlmostEqual(manifest["timeline"][1]["start_seconds"], 0.45, delta=0.01)
            self.assertAlmostEqual(metadata["duration_seconds"], expected_duration, delta=0.12)
            self.assertEqual((metadata["width"], metadata["height"]), (640, 360))

    def test_celery_and_detail_page_expose_video_stage(self):
        celery_text = (BACKEND_DIR / "celery_config.py").read_text(encoding="utf-8")
        api_text = (BACKEND_DIR / "api" / "report_stick_figure_video_workflow.py").read_text(encoding="utf-8")
        frontend_text = (PROJECT_ROOT / "src" / "frontend" / "js" / "report-stick-figure-video-workflow-detail.js").read_text(encoding="utf-8")
        self.assertIn("tasks.report_stick_figure_video.render_video", celery_text)
        self.assertIn("tasks.report_stick_figure_video.align_whole_audio", celery_text)
        self.assertIn('/jobs/<job_id>/video", methods=["POST"]', api_text)
        self.assertIn("len(render_assets)", api_text)
        self.assertIn("000 标题镜头不参与", api_text)
        self.assertIn("data-video-submit", frontend_text)
        self.assertIn("data-video-speed", frontend_text)
        self.assertIn("1,1.1,1.15,1.25", frontend_text)
        self.assertIn("data-video-subtitles", frontend_text)
        self.assertIn("data-video-subtitle-background", frontend_text)
        self.assertIn("data-video-audio-preview", frontend_text)
        self.assertIn("data-audio-preview-box", frontend_text)
        self.assertIn("previewBox.innerHTML=this.audioPreviewMarkup(source)", frontend_text)
        self.assertIn("previewBox.innerHTML=this.audioPreviewMarkup('')", frontend_text)
        self.assertIn("syncAudioPreview", frontend_text)
        self.assertIn("data-video-watermark", frontend_text)
        self.assertIn("watermark_opacity", frontend_text)
        self.assertIn("subtitle_font_color", frontend_text)
        self.assertIn("正片从 001 正文镜头开始", frontend_text)
        self.assertIn('preload="metadata"', frontend_text)
        self.assertIn("data-scene-audio", frontend_text)
        self.assertIn("data-video-audio-mode", frontend_text)
        self.assertIn("复用整篇语音并对齐", frontend_text)
        self.assertIn("整篇 WAV 连续时间轴", frontend_text)
        self.assertIn('payload["video"] = _video_payload(parent_snapshot)', api_text)
        self.assertIn("_overlay_durable_audio_strategy", api_text)
        self.assertIn("if task\n            else None", api_text)
        self.assertIn('/jobs/<job_id>/audio-preview', api_text)

        task_text = (BACKEND_DIR / "tasks" / "report_stick_figure_video.py").read_text(encoding="utf-8")
        self.assertIn('params.get("source_task_id")', task_text)
        self.assertIn('params.get("render_options")', task_text)
        self.assertIn("ReportStickFigureSceneAsset.storyboard_task_id == storyboard_id", task_text)

    def test_detail_page_exposes_sticky_quick_navigation(self):
        html = (
            PROJECT_ROOT / "src" / "frontend" / "report-stick-figure-video-workflow-detail.html"
        ).read_text(encoding="utf-8")
        frontend_text = (
            PROJECT_ROOT / "src" / "frontend" / "js" / "report-stick-figure-video-workflow-detail.js"
        ).read_text(encoding="utf-8")

        self.assertIn('id="quick-nav"', html)
        self.assertIn("lg:sticky lg:top-24", html)
        for section_id in (
            "workflow-overview",
            "report-script",
            "storyboard-plan",
            "scene-assets",
            "video-render",
            "future-pipeline",
        ):
            self.assertIn(f'data-quick-nav="{section_id}"', html)
        self.assertIn("scrollIntoView({behavior:'smooth'", frontend_text)
        self.assertIn("new IntersectionObserver", frontend_text)
        self.assertIn("setActiveQuickNav", frontend_text)
        self.assertIn('id="sidebar-task-status"', html)
        self.assertIn('id="sidebar-refresh-btn"', html)
        self.assertIn("max-h-[calc(100vh-7rem)]", html)
        self.assertIn("workflowStageData()", frontend_text)
        self.assertIn("renderSidebarStatus()", frontend_text)
        self.assertIn('aria-label="左侧视觉任务流程状态"', frontend_text)
        self.assertIn("最近刷新", frontend_text)
        self.assertIn("sticky-sidebar-status-v4-storyboard-guard", html)

    def test_report_audio_detail_exposes_sticky_quick_navigation(self):
        html = (
            PROJECT_ROOT / "src" / "frontend" / "report-audio-workflow-detail.html"
        ).read_text(encoding="utf-8")
        frontend_text = (
            PROJECT_ROOT / "src" / "frontend" / "js" / "report-audio-workflow-detail.js"
        ).read_text(encoding="utf-8")

        self.assertIn('id="quick-nav"', html)
        self.assertIn("lg:sticky lg:top-24", html)
        for section_id in (
            "task-overview",
            "production-tools",
            "visual-videos",
            "media-assets",
            "content-results",
            "child-tasks",
            "publish-center",
        ):
            self.assertIn(f'data-quick-nav="{section_id}"', html)
            self.assertIn(f"['{section_id}',", frontend_text)
        self.assertIn(
            ".map(([id, html]) => ({ id, markup: this.navSection(id, html) }))",
            frontend_text,
        )
        self.assertIn("scrollIntoView({ behavior: 'smooth'", frontend_text)
        self.assertIn("new IntersectionObserver", frontend_text)
        self.assertIn("setupQuickNavigation", frontend_text)

    def test_report_audio_can_reuse_script_and_wav_for_stick_figure_video(self):
        api_text = (BACKEND_DIR / "api" / "report_stick_figure_video_workflow.py").read_text(encoding="utf-8")
        model_text = (BACKEND_DIR / "models" / "report_stick_figure_asset.py").read_text(encoding="utf-8")
        audio_frontend = (PROJECT_ROOT / "src" / "frontend" / "js" / "report-audio-workflow-detail.js").read_text(encoding="utf-8")
        monitor_text = (BACKEND_DIR / "apis" / "v1" / "celery_monitor.py").read_text(encoding="utf-8")

        self.assertIn('/jobs/from-report-audio/<audio_job_id>', api_text)
        self.assertIn("REPORT_AUDIO_FEATURE", api_text)
        self.assertIn("audio_mode", api_text)
        self.assertIn("_video_request_matches", api_text)
        self.assertIn("class ReportStickFigureAudioTrack", model_text)
        self.assertIn("timeline_start_seconds", model_text)
        self.assertIn("视觉方案横屏视频", audio_frontend)
        self.assertIn("visual_scheme_key", audio_frontend)
        self.assertIn("openStickFigureWorkflow", audio_frontend)
        self.assertIn("report_stick_figure_audio_alignment", monitor_text)

    def test_speed_and_styled_subtitles_stay_on_the_same_timeline(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            image_path = root / "001.png"
            audio_path = root / "001.wav"
            Image.new("RGB", (960, 540), "white").save(image_path)
            self._wav(audio_path, 1.2, 420)
            options = normalize_render_options({
                "speed": 1.5,
                "subtitles_enabled": True,
                "subtitle_font_name": "Hiragino Sans GB",
                "subtitle_font_size": 40,
                "subtitle_font_color": "#FFFF00",
                "subtitle_outline_color": "#000000",
                "subtitle_outline": 2,
                "subtitle_position": "bottom",
                "subtitle_background_enabled": True,
                "subtitle_background_color": "#111827",
                "subtitle_background_opacity": 28,
                "watermark_enabled": True,
                "watermark_text": "Shioa研报",
                "watermark_font_size": 26,
                "watermark_color": "#111827",
                "watermark_opacity": 45,
            })
            scenes = [{
                "asset_id": "asset-1",
                "sequence": 1,
                "image_path": str(image_path),
                "audio_path": str(audio_path),
                "script_text": "这是第一句字幕。这是第二句字幕。",
            }]

            with patch("services.report_stick_figure_video.workflow_output_root", return_value=root):
                result = render_scene_timeline(
                    "workflow-subtitle-test",
                    scenes,
                    width=640,
                    height=360,
                    fps=12,
                    render_options=options,
                )

            manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
            subtitle_path = Path(result["subtitle_path"])
            subtitle_text = subtitle_path.read_text(encoding="utf-8")
            self.assertTrue(subtitle_path.is_file())
            self.assertEqual(manifest["render_options"]["speed"], 1.5)
            self.assertTrue(manifest["render_options"]["subtitles_enabled"])
            self.assertAlmostEqual(manifest["duration_seconds"], 0.8, delta=0.02)
            self.assertGreaterEqual(result["subtitle_entry_count"], 1)
            self.assertIn("Hiragino Sans GB,40", subtitle_text)
            self.assertIn("&H0000FFFF", subtitle_text)
            self.assertIn("&HB8271811", subtitle_text)
            self.assertIn(",3,7,0,2,", subtitle_text)
            self.assertIn("这是第一句字幕", subtitle_text)
            self.assertIn("Style: Watermark", subtitle_text)
            self.assertIn("Shioa研报", subtitle_text)
            self.assertTrue(manifest["render_options"]["watermark_enabled"])

    def test_whole_audio_is_used_once_and_drives_scene_timeline(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            audio_path = root / "whole.wav"
            self._wav(audio_path, 1.0, 380)
            scenes = []
            for sequence, start, end in ((1, 0.0, 0.4), (2, 0.4, 1.0)):
                image_path = root / f"{sequence:03d}.png"
                Image.new("RGB", (960, 540), "white").save(image_path)
                scenes.append({
                    "asset_id": f"asset-{sequence}",
                    "sequence": sequence,
                    "image_path": str(image_path),
                    "script_text": f"第{sequence}段",
                    "start_seconds": start,
                    "end_seconds": end,
                    "alignment_confidence": 0.9,
                })

            with patch("services.report_stick_figure_video.workflow_output_root", return_value=root):
                result = render_scene_timeline(
                    "workflow-whole-audio",
                    scenes,
                    audio_mode="whole_audio",
                    whole_audio_path=str(audio_path),
                    width=640,
                    height=360,
                    fps=12,
                )

            manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
            self.assertEqual("whole_audio", result["audio_mode"])
            self.assertEqual("whole_audio", manifest["audio_mode"])
            self.assertAlmostEqual(manifest["timeline"][0]["duration_seconds"], 0.4, delta=0.02)
            self.assertAlmostEqual(manifest["timeline"][1]["start_seconds"], 0.4, delta=0.02)
            self.assertAlmostEqual(result["duration_seconds"], 1.0, delta=0.12)

    def test_text_alignment_builds_continuous_full_audio_boundaries(self):
        scenes = [
            {"asset_id": "a1", "segment_id": "s1", "sequence": 1, "script_text": "硬件公司的利润天花板正在被打破。"},
            {"asset_id": "a2", "segment_id": "s2", "sequence": 2, "script_text": "净利润未来三年保持高速增长。"},
        ]
        asr = [
            {"start": 0.2, "end": 2.2, "text": "硬件公司的利润天花板正在被打破"},
            {"start": 2.4, "end": 4.8, "text": "净利润未来三年保持高速增长"},
        ]
        result = align_storyboard_to_audio(
            scenes,
            asr,
            audio_seconds=5.0,
            source_script="硬件公司的利润天花板正在被打破。净利润未来三年保持高速增长。",
        )
        self.assertEqual(2, result["scene_count"])
        self.assertGreater(result["average_confidence"], 0.9)
        self.assertEqual(0.0, result["timeline"][0]["start_seconds"])
        self.assertEqual(result["timeline"][0]["end_seconds"], result["timeline"][1]["start_seconds"])
        self.assertEqual(5.0, result["timeline"][1]["end_seconds"])

    def test_subtitle_alignment_preserves_original_copy_with_asr_timestamps(self):
        subtitles = [
            {"start": 0.0, "end": 2.0, "text": "钢厂产量还在惯性走高"},
            {"start": 2.0, "end": 2.1, "text": "。"},
            {"start": 2.0, "end": 5.0, "text": "下游真实需求正在下滑。"},
        ]
        asr = [
            {"start": 0.0, "end": 1.6, "text": "钢厂产量还在惯性走高"},
            {"start": 2.1, "end": 4.1, "text": "下游真实需求正在下滑"},
        ]

        result = align_subtitles_to_audio(
            subtitles,
            asr,
            audio_seconds=4.5,
        )

        self.assertEqual(
            ["钢厂产量还在惯性走高。", "下游真实需求正在下滑。"],
            [item["text"] for item in result["subtitles"]],
        )
        self.assertAlmostEqual(result["subtitles"][0]["end"], 1.85, places=2)
        self.assertEqual(result["subtitles"][1]["start"], result["subtitles"][0]["end"])
        self.assertEqual(result["subtitles"][-1]["end"], 4.5)

    def test_scheme3_timeline_uses_same_forced_alignment_as_subtitles(self):
        timeline = {
            "timing_source": "script_weight",
            "scenes": [
                {
                    "asset_id": "a1",
                    "sequence": 1,
                    "image_path": "/tmp/001.png",
                    "script_text": "第一段研报正文。",
                    "start_seconds": 0.0,
                    "end_seconds": 2.5,
                    "duration_seconds": 2.5,
                },
                {
                    "asset_id": "a2",
                    "sequence": 2,
                    "image_path": "/tmp/002.png",
                    "script_text": "第二段研报正文。",
                    "start_seconds": 2.5,
                    "end_seconds": 5.0,
                    "duration_seconds": 2.5,
                },
            ],
        }
        asr = [
            {"start": 0.1, "end": 1.4, "text": "第一段研报正文"},
            {"start": 2.2, "end": 4.6, "text": "第二段研报正文"},
        ]

        result = align_scene_timeline_to_audio(
            timeline,
            asr,
            audio_seconds=5.0,
            source_script="第一段研报正文。第二段研报正文。",
        )

        self.assertEqual(result["timing_source"], "asr_forced_alignment")
        self.assertEqual(result["scenes"][0]["image_path"], "/tmp/001.png")
        self.assertAlmostEqual(result["scenes"][0]["end_seconds"], 1.8, places=2)
        self.assertEqual(
            result["scenes"][0]["end_seconds"],
            result["scenes"][1]["start_seconds"],
        )

    def test_alignment_rejects_audio_from_different_script(self):
        scenes = [{"asset_id": "a1", "segment_id": "s1", "sequence": 1, "script_text": "这是当前研报正文。"}]
        with self.assertRaisesRegex(ValueError, "文案与当前分镜正文不一致"):
            align_storyboard_to_audio(
                scenes,
                [{"start": 0, "end": 1, "text": "这是当前研报正文"}],
                audio_seconds=1.2,
                source_script="完全不同的另一份投资报告内容。",
            )

    def test_render_preflight_failures_close_parent_workflow(self):
        task_text = (
            BACKEND_DIR / "tasks" / "report_stick_figure_video.py"
        ).read_text(encoding="utf-8")
        render_body = task_text.split("def _render_video(", 1)[1].split(
            "\ndef _update_progress", 1
        )[0]
        preflight_body = render_body.split("if not assets:", 1)[1].split(
            "_update_progress(", 1
        )[0]
        mark_failed_body = task_text.split("def _mark_failed(", 1)[1]

        self.assertGreaterEqual(preflight_body.count("workflow_id=workflow_id"), 3)
        self.assertIn("PublicationTask.waiting_child_status: \"failed\"", mark_failed_body)
        self.assertIn("PublicationTask.completed_at: now", mark_failed_body)

    def test_end_to_end_workflow_wires_final_render_from_both_race_orders(self):
        video_task_text = (
            BACKEND_DIR / "tasks" / "report_stick_figure_video.py"
        ).read_text(encoding="utf-8")
        image_task_text = (
            BACKEND_DIR / "tasks" / "report_stick_figure_assets.py"
        ).read_text(encoding="utf-8")
        monitor_text = (
            BACKEND_DIR / "apis" / "v1" / "celery_monitor.py"
        ).read_text(encoding="utf-8")
        align_completion = video_task_text.split(
            "def _align_whole_audio(", 1
        )[1].split("\ndef _update_alignment_parent", 1)[0]
        image_completion = image_task_text.split(
            "def _refresh_parent_image_state(", 1
        )[1].split("\ndef _normalize_png", 1)[0]

        self.assertIn(
            "maybe_auto_enqueue_stick_figure_video(workflow_id)",
            align_completion,
        )
        self.assertIn(
            "maybe_auto_enqueue_stick_figure_video(",
            image_completion,
        )
        self.assertIn(
            "repair_ready_stick_figure_video_workflows_once",
            monitor_text,
        )

    def test_report_audio_detail_aggregates_visual_videos_by_stable_pdf_identity(self):
        api_text = (
            BACKEND_DIR / "api" / "report_audio_workflow.py"
        ).read_text(encoding="utf-8")
        helper_body = api_text.split("def _linked_visual_video_payloads(", 1)[1].split(
            "\n\ndef _job_status_payload", 1
        )[0]
        frontend_text = (
            PROJECT_ROOT / "src" / "frontend" / "js" / "report-audio-workflow-detail.js"
        ).read_text(encoding="utf-8")

        self.assertIn("PublicationTask.report_audio_pdf_attachment_id == attachment_id", helper_body)
        self.assertIn("PublicationTask.report_audio_pdf_filename == filename", helper_body)
        self.assertIn("PublicationTask.report_audio_pdf_bytes == pdf_bytes", helper_body)
        self.assertIn("PublicationTask.feature == VISUAL_VIDEO_WORKFLOW_FEATURE", helper_body)
        self.assertIn("PublicationTask.feature == VISUAL_VIDEO_RENDER_FEATURE", helper_body)
        self.assertNotIn("PublicationTask.params", helper_body)
        self.assertNotIn("PublicationTask.result", helper_body)
        self.assertIn("renderVisualVideos(job)", frontend_text)
        self.assertIn("同一 PDF 的视觉方案视频", frontend_text)
        self.assertIn("data-visual-video-variants", frontend_text)
        self.assertIn("biliUploadVideoOptions(payload)", frontend_text)
        self.assertIn("visualWorkflowId: workflowId", frontend_text)
        publish_panel_text = (
            PROJECT_ROOT / "src" / "frontend" / "js" / "bilibili-publish-panel.js"
        ).read_text(encoding="utf-8")
        self.assertIn("visual_workflow_id: selectedVideo?.visualWorkflowId", publish_panel_text)
        self.assertIn("_linked_visual_video_path(current_user, task, visual_workflow_id)", api_text)

    def test_report_audio_detail_polling_only_renders_changed_sections(self):
        frontend_text = (
            PROJECT_ROOT / "src" / "frontend" / "js" / "report-audio-workflow-detail.js"
        ).read_text(encoding="utf-8")
        schedule_body = frontend_text.split("scheduleAutoRefresh() {", 1)[1].split(
            "\n    async refreshBilibiliUploads()", 1
        )[0]

        self.assertIn("this.sectionRenderCache = new Map()", frontend_text)
        self.assertIn("if (background) this.renderChangedSections();", frontend_text)
        self.assertIn("this.load({ background: true });", schedule_body)
        self.assertNotIn("this.els.root.innerHTML", schedule_body)
        self.assertIn("if (this.sectionRenderCache.get(id) === markup) continue;", frontend_text)
        self.assertIn("if (id === 'publish-center'", frontend_text)


class ReportStickFigureAutoRenderContinuationTest(unittest.TestCase):
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
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_ready_workflow_auto_enqueues_exactly_one_final_video(self):
        image_path = Path(self.tmp.name) / "001.png"
        audio_path = Path(self.tmp.name) / "whole.wav"
        image_path.write_bytes(b"png")
        audio_path.write_bytes(b"wav")
        source = PublicationTask(
            status="completed",
            params={"feature": "report_pdf_audio_workflow", "user_id": "user-1"},
            result={},
        )
        workflow = PublicationTask(
            status="processing",
            current_step="whole_audio_aligned",
            progress=88,
            params={
                "feature": "report_stick_figure_video_workflow",
                "user_id": "user-1",
                "source_feature": "report_pdf_audio_workflow",
            },
            result={},
        )
        storyboard = PublicationTask(
            status="completed",
            params={"feature": "report_visual_plan", "user_id": "user-1"},
            result={},
        )
        db.session.add_all([source, workflow, storyboard])
        db.session.flush()
        track = ReportStickFigureAudioTrack(
            workflow_task_id=workflow.id,
            storyboard_task_id=storyboard.id,
            source_task_id=source.id,
            status="completed",
            audio_path=str(audio_path),
            aligned_scene_count=1,
        )
        asset = ReportStickFigureSceneAsset(
            workflow_task_id=workflow.id,
            storyboard_task_id=storyboard.id,
            segment_id="segment-1",
            sequence=1,
            script_text_hash="script",
            visual_prompt_hash="visual",
            image_status="completed",
            image_path=str(image_path),
            timeline_start_seconds=0.0,
            timeline_end_seconds=2.0,
            timeline_source="whole_audio",
        )
        db.session.add_all([track, asset])
        db.session.commit()
        workflow_id = str(workflow.id)

        async_result = SimpleNamespace(id="celery-video-1")
        with patch(
            "tasks.report_stick_figure_video.maybe_guarded_apply_async",
            return_value={"async_result": async_result},
        ) as submit:
            first = maybe_auto_enqueue_stick_figure_video(workflow_id)
            second = maybe_auto_enqueue_stick_figure_video(workflow_id)

        self.assertTrue(first["enqueued"])
        self.assertEqual(second["reason"], "render_already_active")
        self.assertEqual(submit.call_count, 1)
        render_tasks = PublicationTask.query.filter(
            PublicationTask.feature == "report_stick_figure_video_render",
            PublicationTask.parent_task_id == workflow_id,
        ).all()
        self.assertEqual(len(render_tasks), 1)
        self.assertEqual(render_tasks[0].celery_task_id, "celery-video-1")
        refreshed = db.session.get(PublicationTask, workflow_id)
        self.assertEqual(refreshed.current_step, "video_queued")
        self.assertEqual(refreshed.waiting_child_id, render_tasks[0].id)


if __name__ == "__main__":
    unittest.main()
