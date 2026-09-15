import io
import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask
from sqlalchemy import event, update
from sqlalchemy.dialects.postgresql import dialect as postgresql_dialect
from sqlalchemy.exc import OperationalError


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from api import report_audio_workflow as report_audio_api  # noqa: E402
from models import db  # noqa: E402
from models.bilibili_publish import PublicationTaskBilibiliUpload  # noqa: E402
from models.content_pipeline_asset import ContentPipelineAsset  # noqa: E402
from models.platform_credential import PlatformCredentialAccount  # noqa: E402
from models.task import PublicationTask  # noqa: E402
from models.zsxq_sync import ZsxqAttachment, ZsxqTopic  # noqa: E402
from services.bilibili_publish import (  # noqa: E402
    bilibili_upload_execution_context,
    bilibili_upload_children,
    create_bilibili_upload_task,
    enqueue_bilibili_upload_task,
    retry_bilibili_upload_child,
)
from services.browser_publish_jobs import (  # noqa: E402
    BrowserPublishJobSpec,
    browser_publish_children,
    browser_publish_payload,
    cancel_browser_publish_task,
    create_browser_publish_task,
    enqueue_browser_publish_task,
)
from services.report_audio_workflow import FEATURE_NAME  # noqa: E402


class ReportAudioWorkflowApiTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.app = Flask(__name__)
        self.app.config.update(
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(self.app)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        report_audio_api._clear_jobs_summary_cache()

    def tearDown(self):
        report_audio_api._clear_jobs_summary_cache()
        db.session.remove()
        db.drop_all()
        self.ctx.pop()
        self.tmpdir.cleanup()

    def test_submission_date_range_uses_shanghai_calendar_day(self):
        start, end = report_audio_api._submission_date_utc_range("2026-07-14")

        self.assertEqual(start, datetime(2026, 7, 13, 16, 0, 0))
        self.assertEqual(end, datetime(2026, 7, 14, 16, 0, 0))
        self.assertIsNone(report_audio_api._submission_date_utc_range("2026-02-30"))

    def test_lightweight_child_query_exposes_captcha_handoff_without_full_result(self):
        parent = PublicationTask(
            status="failed",
            current_step="failed",
            progress=45,
            params={"feature": FEATURE_NAME, "user_id": "user-1"},
            result={},
        )
        db.session.add(parent)
        db.session.flush()
        parent_id = str(parent.id)
        child = PublicationTask(
            status="blocked_by_captcha",
            current_step="blocked_by_captcha",
            progress=45,
            error_message="豆包要求完成图片验证",
            params={
                "feature": report_audio_api.doubao_browser_tts.FEATURE_NAME,
                "parent_task_id": parent_id,
                "doubao_browser_tts": {
                    "metadata": {
                        "parent_task_id": parent_id,
                        "chunk_index": 5,
                        "chunk_total": 5,
                    }
                },
            },
            result={
                "captcha_blocked": {
                    "requires_human_action": True,
                    "workflow_url": f"http://localhost:5001/report-audio-workflow-detail.html?job_id={parent_id}",
                    "resolution_steps": ["完成验证", "从失败处续跑"],
                },
                "notifications": {
                    "doubao_captcha_blocked": {
                        "sent": False,
                        "reason": "missing_config",
                        "to": "owner@example.test",
                    }
                },
            },
        )
        db.session.add(child)
        db.session.commit()
        statements = []

        def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
            statements.append(statement)

        event.listen(db.engine, "before_cursor_execute", capture)
        try:
            grouped = report_audio_api._child_summaries_for_parent_ids([parent_id])
        finally:
            event.remove(db.engine, "before_cursor_execute", capture)

        payload = grouped[parent_id]["doubao_browser_tts"][0]
        self.assertTrue(payload["captcha_blocked"]["requires_human_action"])
        notification = payload["notifications"]["doubao_captcha_blocked"]
        self.assertEqual(notification["reason"], "missing_config")
        self.assertEqual(notification["recipient_hint"], "o***@example.test")
        self.assertNotIn("to", notification)
        selects = [
            statement.lower()
            for statement in statements
            if statement.lstrip().lower().startswith("select")
        ]
        self.assertEqual(len(selects), 1)
        self.assertNotIn("publication_tasks.params", selects[0])

    def test_manual_captcha_confirmation_resolves_history_before_retry(self):
        """A verified captcha attempt must no longer block the replacement retry."""
        parent = PublicationTask(
            status="failed",
            current_step="failed",
            progress=45,
            params={"feature": FEATURE_NAME, "user_id": "user-1"},
            result={},
        )
        db.session.add(parent)
        db.session.flush()
        parent_id = str(parent.id)
        blocked = PublicationTask(
            status="blocked_by_captcha",
            current_step="blocked_by_captcha",
            progress=45,
            error_message="豆包要求完成图片验证",
            parent_task_id=parent_id,
            feature=report_audio_api.doubao_browser_tts.FEATURE_NAME,
            params={
                "feature": report_audio_api.doubao_browser_tts.FEATURE_NAME,
                "parent_task_id": parent_id,
            },
            result={"captcha_blocked": {"requires_human_action": True, "reason": "captcha"}},
        )
        replacement = PublicationTask(
            status="pending",
            current_step="queued",
            parent_task_id=parent_id,
            feature=report_audio_api.doubao_browser_tts.FEATURE_NAME,
            params={
                "feature": report_audio_api.doubao_browser_tts.FEATURE_NAME,
                "parent_task_id": parent_id,
            },
            result={},
        )
        db.session.add_all([blocked, replacement])
        db.session.commit()

        handler = getattr(report_audio_api, "_resolve_doubao_captcha_children_for_retry", None)
        self.assertTrue(callable(handler), "missing CAPTCHA recovery state transition")
        resolved_ids = handler(parent_id, verified_at="2026-08-13T08:20:00Z")
        db.session.commit()

        self.assertEqual(resolved_ids, [str(blocked.id)])
        refreshed = db.session.get(PublicationTask, blocked.id)
        self.assertEqual(refreshed.status, "blocked_by_captcha")
        self.assertEqual(refreshed.current_step, "captcha_verified")
        captcha = refreshed.result["captcha_blocked"]
        self.assertFalse(captcha["requires_human_action"])
        self.assertEqual(captcha["resolved_at"], "2026-08-13T08:20:00Z")
        self.assertEqual(captcha["recovery_state"], "retry_queued")

        current = report_audio_api._child_summaries_for_parent_ids([parent_id])[parent_id]["doubao_browser_tts"]
        self.assertEqual([item["job_id"] for item in current], [str(replacement.id)])


    def test_read_endpoint_replaces_invalid_session_and_retries_once(self):
        attempts = []

        @report_audio_api._retry_transient_db_read("test read")
        def read_once():
            attempts.append(1)
            if len(attempts) == 1:
                raise OperationalError(
                    "SELECT 1",
                    {},
                    Exception("server closed the connection unexpectedly"),
                )
            return "ok"

        with (
            patch.object(report_audio_api, "_set_hot_read_statement_timeout"),
            patch.object(report_audio_api, "_reset_failed_db_session") as reset,
        ):
            self.assertEqual(read_once(), "ok")

        self.assertEqual(len(attempts), 2)
        reset.assert_called_once_with()

    def test_linked_pdf_bytes_identity_matches_varchar_projection(self):
        self.assertEqual(
            report_audio_api._linked_pdf_bytes_identity(
                SimpleNamespace(report_audio_pdf_bytes=495174)
            ),
            "495174",
        )
        self.assertEqual(
            report_audio_api._linked_pdf_bytes_identity(
                SimpleNamespace(report_audio_pdf_bytes="495174")
            ),
            "495174",
        )
        self.assertEqual(
            report_audio_api._linked_pdf_bytes_identity(
                SimpleNamespace(report_audio_pdf_bytes="not-a-number")
            ),
            "",
        )

    def test_video_options_accept_both_vertical_schemes(self):
        options = report_audio_api._parse_video_options({
            "vertical_video_enabled": "1",
            "vertical_video_schemes": '["scheme1", "scheme2"]',
        })

        self.assertTrue(options["vertical_enabled"])
        self.assertEqual(options["vertical_video_schemes"], ["scheme1", "scheme2"])
        self.assertEqual(options["vertical_video_scheme"], "scheme2")

    def test_video_options_accept_scheme3_as_primary(self):
        options = report_audio_api._parse_video_options({
            "vertical_video_enabled": "1",
            "vertical_video_schemes": '["scheme1", "scheme3"]',
        })

        self.assertEqual(options["vertical_video_schemes"], ["scheme1", "scheme3"])
        self.assertEqual(options["vertical_video_scheme"], "scheme3")
        self.assertEqual(report_audio_api._vertical_video_scheme_label("video_bg"), "方案三")

    def test_video_options_keep_legacy_single_vertical_scheme(self):
        options = report_audio_api._parse_video_options({
            "vertical_video_enabled": "1",
            "vertical_video_scheme": "scheme1",
        })

        self.assertEqual(options["vertical_video_schemes"], ["scheme1"])
        self.assertEqual(options["vertical_video_scheme"], "scheme1")

    def test_pdf_evidence_is_opt_in_for_new_submissions(self):
        self.assertFalse(report_audio_api._parse_pdf_evidence_enabled({}))
        self.assertFalse(report_audio_api._parse_pdf_evidence_enabled({"pdf_evidence_enabled": "0"}))
        self.assertTrue(report_audio_api._parse_pdf_evidence_enabled({"pdf_evidence_enabled": "1"}))

    def test_initial_submission_persists_1500_character_contract(self):
        pdf_path = Path(self.tmpdir.name) / "source.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\n%%EOF\n")
        pdf_meta = {
            "filename": "source.pdf",
            "path": str(pdf_path),
            "bytes": pdf_path.stat().st_size,
        }

        with (
            self.app.test_request_context(
                "/api/report-audio-workflows/jobs",
                method="POST",
                data={
                    "pdf": (io.BytesIO(pdf_path.read_bytes()), "source.pdf"),
                    "prompt": "请解析 PDF 并撰写口播文案。",
                    "review_prompt": "请复核事实并输出最终稿。",
                    "gem_v2_mode": "off",
                },
                content_type="multipart/form-data",
            ),
            patch.object(report_audio_api.gemini_browser, "status_payload", return_value={"configured": True}),
            patch.object(report_audio_api.doubao_browser_tts, "status_payload", return_value={"configured": True}),
            patch.object(report_audio_api, "require_report_audio_workers_ready", return_value={"ready": True}),
            patch.object(report_audio_api, "_save_pdf_upload", return_value=pdf_meta),
            patch.object(
                report_audio_api,
                "apply_async_after_db_release",
                return_value=SimpleNamespace(id="celery-initial"),
            ),
        ):
            response = report_audio_api.create_job.__wrapped__(SimpleNamespace(id="user-1"))

        payload = response.get_json()
        task = PublicationTask.query.filter_by(id=payload["job_id"]).one()
        workflow = task.params["report_audio_workflow"]
        self.assertEqual(workflow["gemini_min_final_chars"], 1500)
        self.assertTrue(workflow["gemini_prefer_pro_model"])
        self.assertFalse(workflow["gemini_require_pro_model"])
        self.assertEqual(workflow["gem_v2_mode"], "off")
        self.assertIn("不少于 1500 字", workflow["prompt"])
        self.assertIn("不少于 1500 字", workflow["review_prompt"])

    def test_default_shadow_submission_omits_legacy_prompt_expansion_and_followups(self):
        pdf_path = Path(self.tmpdir.name) / "source.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\n%%EOF\n")
        pdf_meta = {
            "filename": "source.pdf",
            "path": str(pdf_path),
            "bytes": pdf_path.stat().st_size,
        }

        with (
            self.app.test_request_context(
                "/api/report-audio-workflows/jobs",
                method="POST",
                data={
                    "pdf": (io.BytesIO(pdf_path.read_bytes()), "source.pdf"),
                    "prompt": "旧版调用提示词",
                    "review_prompt": "旧版通用复核",
                    "followups": '["旧版追加追问"]',
                    "gem_v2_focus": "重点解释验证指标",
                },
                content_type="multipart/form-data",
            ),
            patch.object(report_audio_api.gemini_browser, "status_payload", return_value={"configured": True}),
            patch.object(report_audio_api.doubao_browser_tts, "status_payload", return_value={"configured": True}),
            patch.object(report_audio_api, "require_report_audio_workers_ready", return_value={"ready": True}),
            patch.object(report_audio_api, "_save_pdf_upload", return_value=pdf_meta),
            patch.object(
                report_audio_api,
                "apply_async_after_db_release",
                return_value=SimpleNamespace(id="celery-shadow"),
            ),
        ):
            response = report_audio_api.create_job.__wrapped__(SimpleNamespace(id="user-1"))

        payload = response.get_json()
        task = PublicationTask.query.filter_by(id=payload["job_id"]).one()
        workflow = task.params["report_audio_workflow"]
        self.assertEqual(workflow["gem_v2_mode"], "shadow")
        self.assertEqual(workflow["gem_v2_focus"], "重点解释验证指标")
        self.assertNotIn("不少于 1500 字", workflow["prompt"])
        self.assertNotIn("不少于 1500 字", workflow["review_prompt"])
        self.assertEqual(workflow["followups"], [])
        self.assertEqual(task.result["gem_v2"], {"mode": "shadow", "status": "pending"})

    def test_submission_rejects_unknown_gem_v2_mode(self):
        pdf_path = Path(self.tmpdir.name) / "source.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\n%%EOF\n")

        with self.app.test_request_context(
            "/api/report-audio-workflows/jobs",
            method="POST",
            data={
                "pdf": (io.BytesIO(pdf_path.read_bytes()), "source.pdf"),
                "gem_v2_mode": "experimental",
            },
            content_type="multipart/form-data",
        ):
            response, status_code = report_audio_api.create_job.__wrapped__(SimpleNamespace(id="user-1"))

        self.assertEqual(status_code, 400)
        self.assertIn("gem_v2_mode", response.get_json()["message"])

    def test_script_regeneration_creates_independent_strict_pro_material_task(self):
        pdf_path = Path(self.tmpdir.name) / "source.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\n%%EOF\n")
        source = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            feature=FEATURE_NAME,
            user_id="user-1",
            params={
                "feature": FEATURE_NAME,
                "user_id": "user-1",
                "report_audio_workflow": {
                    "prompt": "解析 PDF 并撰写口播文案。",
                    "review_prompt": "复核事实并输出最终稿。",
                    "pdf": {"path": str(pdf_path), "filename": "source.pdf"},
                    "pdf_path": str(pdf_path),
                    "video": {"enabled": True, "vertical_enabled": True},
                },
            },
            result={"pdf": {"path": str(pdf_path), "filename": "source.pdf"}},
        )
        db.session.add(source)
        db.session.commit()

        with (
            self.app.test_request_context(json={"minimum_chars": 1500}),
            patch.object(
                report_audio_api,
                "maybe_guarded_apply_async",
                return_value={
                    "async_result": SimpleNamespace(id="celery-regenerate"),
                    "deferred": False,
                    "admission": {"allowed": True},
                },
            ) as enqueue,
        ):
            response, status_code = report_audio_api.regenerate_script.__wrapped__(
                SimpleNamespace(id="user-1"),
                source.id,
            )

        payload = response.get_json()
        self.assertEqual(status_code, 202)
        child = PublicationTask.query.filter_by(id=payload["regeneration_job_id"]).one()
        workflow = child.params["report_audio_workflow"]
        self.assertNotEqual(child.id, source.id)
        self.assertEqual(child.source_task_id, source.id)
        self.assertEqual(child.parent_feature, report_audio_api.SCRIPT_REGENERATION_PARENT_FEATURE)
        self.assertTrue(workflow["script_material_only"])
        self.assertTrue(workflow["force_gemini_regeneration"])
        self.assertTrue(workflow["gemini_require_pro_model"])
        self.assertEqual(workflow["gemini_min_final_chars"], 1500)
        self.assertFalse(workflow["pdf_evidence_enabled"])
        self.assertFalse(workflow["video"]["enabled"])
        self.assertIn("不少于 1500 字", workflow["review_prompt"])
        self.assertNotIn("PDF_EVIDENCE_V1_JSON", workflow["review_prompt"])
        enqueue.assert_called_once()

    def test_generate_audio_from_script_creates_idempotent_tts_only_continuation(self):
        pdf_path = Path(self.tmpdir.name) / "source.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\n%%EOF\n")
        script = "这是一段已经完成审核、可以直接交给语音合成的财经口播正文。" * 30
        structured = {
            "title": "测试研报",
            "final_script_for_tts": script,
        }
        source = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            feature=FEATURE_NAME,
            user_id="user-1",
            params={
                "feature": FEATURE_NAME,
                "user_id": "user-1",
                "report_audio_workflow": {
                    "pdf": {"path": str(pdf_path), "filename": "source.pdf"},
                    "pdf_path": str(pdf_path),
                    "script_material_only": True,
                    "tts_provider": "doubao_browser",
                    "gemini_min_final_chars": 1500,
                    "video": {"enabled": False},
                },
            },
            result={
                "pdf": {"path": str(pdf_path), "filename": "source.pdf"},
                "structured_result": structured,
                "script_material": {"status": "completed"},
            },
        )
        db.session.add(source)
        db.session.commit()

        with (
            self.app.test_request_context(json={}),
            patch.object(
                report_audio_api.doubao_browser_tts,
                "status_payload",
                return_value={"configured": True},
            ),
            patch.object(
                report_audio_api,
                "maybe_guarded_apply_async",
                return_value={
                    "async_result": SimpleNamespace(id="celery-audio"),
                    "deferred": False,
                    "admission": {"allowed": True},
                },
            ) as enqueue,
        ):
            response, status_code = report_audio_api.generate_audio_from_script.__wrapped__(
                SimpleNamespace(id="user-1"),
                source.id,
            )
            repeated_response, repeated_status = report_audio_api.generate_audio_from_script.__wrapped__(
                SimpleNamespace(id="user-1"),
                source.id,
            )

        payload = response.get_json()
        repeated_payload = repeated_response.get_json()
        self.assertEqual(status_code, 202)
        self.assertEqual(repeated_status, 202)
        self.assertEqual(
            repeated_payload["audio_generation_job_id"],
            payload["audio_generation_job_id"],
        )
        child = PublicationTask.query.filter_by(id=payload["audio_generation_job_id"]).one()
        workflow = child.params["report_audio_workflow"]
        self.assertNotEqual(child.id, source.id)
        self.assertEqual(child.source_task_id, source.id)
        self.assertEqual(child.parent_feature, report_audio_api.AUDIO_GENERATION_PARENT_FEATURE)
        self.assertFalse(workflow["script_material_only"])
        self.assertFalse(workflow["force_gemini_regeneration"])
        self.assertFalse(workflow["video"]["enabled"])
        self.assertEqual(workflow["visual_scheme_keys"], [])
        self.assertEqual(workflow["tts_provider"], "doubao_browser")
        self.assertNotIn("gemini_min_final_chars", workflow)
        self.assertEqual(child.result["structured_result"]["final_script_for_tts"], script)
        enqueue.assert_called_once()

    def test_video_options_normalize_explicit_keyframe_outputs(self):
        options = report_audio_api._parse_video_options({
            "video_render_mode": "keyframes",
            "keyframe_outputs": (
                '["bilibili_cover", "vertical_keyframe_scheme1", '
                '"vertical_keyframe_scheme1", "unknown"]'
            ),
        })

        self.assertEqual(options["render_mode"], "keyframes")
        self.assertEqual(
            options["keyframe_outputs"],
            ["bilibili_cover", "vertical_keyframe_scheme1"],
        )

    def test_scheme3_scene_timeline_falls_back_to_script_weight(self):
        root = Path(self.tmpdir.name)
        first_image = root / "scene-1.png"
        second_image = root / "scene-2.png"
        first_image.write_bytes(b"image-1")
        second_image.write_bytes(b"image-2")
        rows = [
            SimpleNamespace(
                asset_id="asset-1",
                sequence=1,
                image_status="completed",
                image_path=str(first_image),
                script_text="十个字的短文案",
                estimated_seconds=None,
                timeline_start_seconds=None,
                timeline_end_seconds=None,
            ),
            SimpleNamespace(
                asset_id="asset-2",
                sequence=2,
                image_status="completed",
                image_path=str(second_image),
                script_text="这是一段明显更长的正文文案，用于获得更长的画面停留时间。",
                estimated_seconds=None,
                timeline_start_seconds=None,
                timeline_end_seconds=None,
            ),
        ]

        timeline = report_audio_api._build_scheme3_scene_timeline(rows, 30)

        self.assertEqual(timeline["timing_source"], "script_weight")
        self.assertEqual(timeline["scene_count"], 2)
        self.assertEqual(timeline["scenes"][0]["start_seconds"], 0)
        self.assertEqual(timeline["scenes"][-1]["end_seconds"], 30)
        self.assertGreater(
            timeline["scenes"][1]["duration_seconds"],
            timeline["scenes"][0]["duration_seconds"],
        )
        self.assertEqual(timeline["scenes"][0]["script_text"], "十个字的短文案")

    def test_ready_linked_visual_workflow_uses_live_scene_readiness(self):
        with patch.object(
            report_audio_api,
            "_linked_visual_video_payloads",
            return_value=[
                {
                    "workflow_job_id": "visual-running-but-ready",
                    "visual_scheme": {"key": "stick_figure"},
                    "status": "running",
                    "step": "images_queued",
                    "progress": 65,
                    "scheme3_ready": True,
                },
            ],
        ):
            workflow_id = report_audio_api._ready_linked_visual_workflow_id(
                SimpleNamespace(id="user-1"),
                SimpleNamespace(id="audio-1"),
                "stick_figure",
            )

        self.assertEqual(workflow_id, "visual-running-but-ready")

    def test_public_video_exposes_each_vertical_scheme_download(self):
        root = Path(self.tmpdir.name)
        scheme1 = root / "scheme1.mp4"
        scheme2 = root / "scheme2.mp4"
        scheme1.write_bytes(b"scheme1")
        scheme2.write_bytes(b"scheme2")

        public = report_audio_api._public_video("job-both", {
            "enabled": True,
            "vertical_enabled": True,
            "vertical_video_scheme": "scheme2",
            "vertical_video_schemes": ["scheme1", "scheme2"],
            "vertical_video_path": str(scheme2),
            "vertical_video_paths": {
                "scheme1": str(scheme1),
                "scheme2": str(scheme2),
            },
        })

        self.assertEqual(set(public["vertical_videos"]), {"scheme1", "scheme2"})
        self.assertTrue(public["vertical_videos"]["scheme1"]["download_url"].endswith("scheme=scheme1"))
        self.assertTrue(public["vertical_videos"]["scheme2"]["download_url"].endswith("scheme=scheme2"))

    def test_linked_visual_videos_include_all_schemes_for_same_pdf(self):
        root = Path(self.tmpdir.name)
        stick_video = root / "stick.mp4"
        doraemon_video = root / "doraemon.mp4"
        stick_video.write_bytes(b"stick-video")
        doraemon_video.write_bytes(b"doraemon-video")
        audio_task = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            params={
                "feature": FEATURE_NAME,
                "user_id": "user-1",
                "report_audio_workflow": {
                    "pdf": {
                        "attachment_id": "pdf-attachment-1",
                        "filename": "same-report.pdf",
                        "bytes": 123456,
                    },
                },
            },
            result={},
        )
        audio_task.feature = FEATURE_NAME
        audio_task.user_id = "user-1"
        audio_task.report_audio_pdf_attachment_id = "pdf-attachment-1"
        audio_task.report_audio_pdf_filename = "same-report.pdf"
        audio_task.report_audio_pdf_bytes = 123456
        db.session.add(audio_task)
        db.session.flush()
        for scheme_key, video_path in (
            ("stick_figure", stick_video),
            ("doraemon", doraemon_video),
        ):
            visual_task = PublicationTask(
                status="completed",
                current_step="done",
                progress=100,
                params={
                    "feature": report_audio_api.VISUAL_VIDEO_WORKFLOW_FEATURE,
                    "user_id": "user-1",
                    "source_task_id": str(audio_task.id),
                    "source_feature": FEATURE_NAME,
                    "visual_scheme_key": scheme_key,
                    "report_stick_figure_video_workflow": {
                        "visual_scheme_key": scheme_key,
                        "pdf": {
                            "attachment_id": "pdf-attachment-1",
                            "filename": "same-report.pdf",
                            "bytes": 123456,
                        },
                    },
                },
                result={"video_path": str(video_path)},
            )
            visual_task.feature = report_audio_api.VISUAL_VIDEO_WORKFLOW_FEATURE
            visual_task.user_id = "user-1"
            visual_task.source_task_id = str(audio_task.id)
            visual_task.source_feature = FEATURE_NAME
            visual_task.task_title = f"{scheme_key} video"
            visual_task.task_video_path = str(video_path)
            visual_task.report_visual_scheme_key = scheme_key
            visual_task.report_audio_pdf_attachment_id = "pdf-attachment-1"
            visual_task.report_audio_pdf_filename = "same-report.pdf"
            visual_task.report_audio_pdf_bytes = 123456
            db.session.add(visual_task)
        unrelated_task = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            params={
                "feature": report_audio_api.VISUAL_VIDEO_WORKFLOW_FEATURE,
                "user_id": "user-1",
                "visual_scheme_key": "stick_figure",
                "report_stick_figure_video_workflow": {
                    "visual_scheme_key": "stick_figure",
                    "pdf": {
                        "attachment_id": "different-pdf",
                        "filename": "same-report.pdf",
                        "bytes": 123456,
                    },
                },
            },
            result={"video_path": str(stick_video)},
        )
        unrelated_task.feature = report_audio_api.VISUAL_VIDEO_WORKFLOW_FEATURE
        unrelated_task.user_id = "user-1"
        unrelated_task.task_video_path = str(stick_video)
        unrelated_task.report_visual_scheme_key = "stick_figure"
        unrelated_task.report_audio_pdf_attachment_id = "different-pdf"
        unrelated_task.report_audio_pdf_filename = "same-report.pdf"
        unrelated_task.report_audio_pdf_bytes = 123456
        db.session.add(unrelated_task)
        db.session.commit()

        items = report_audio_api._linked_visual_video_payloads(
            SimpleNamespace(id="user-1"),
            audio_task,
        )

        self.assertEqual({"stick_figure", "doraemon"}, {
            item["visual_scheme"]["key"] for item in items
        })
        self.assertEqual(2, len(items))
        self.assertTrue(all(item["status"] == "succeeded" for item in items))
        self.assertTrue(all(item["source"] == "report_audio" for item in items))
        self.assertTrue(all(item["video"]["url"].endswith("/video/file") for item in items))
        resolved = report_audio_api._linked_visual_video_path(
            SimpleNamespace(id="user-1"),
            audio_task,
            items[0]["workflow_job_id"],
        )
        self.assertTrue(resolved in {str(stick_video), str(doraemon_video)})
        self.assertIsNone(report_audio_api._linked_visual_video_path(
            SimpleNamespace(id="user-1"),
            audio_task,
            str(unrelated_task.id),
        ))

    def test_job_summary_page_keeps_exact_pagination_with_two_round_trips(self):
        for index in range(3):
            db.session.add(PublicationTask(
                status="completed",
                current_step="done",
                progress=100,
                params={
                    "feature": FEATURE_NAME,
                    "user_id": "user-1",
                    "report_audio_workflow": {"pdf": {"filename": f"report-{index}.pdf"}},
                },
                result={},
                created_at=datetime(2026, 7, 18, 3, index, 0),
            ))
        db.session.commit()

        query = PublicationTask.query.filter(
            PublicationTask.feature == FEATURE_NAME,
            PublicationTask.user_id == "user-1",
        )
        statements = []

        def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
            normalized = " ".join(statement.lower().split())
            if " from publication_tasks " in normalized:
                statements.append(normalized)

        event.listen(db.engine, "before_cursor_execute", capture)
        try:
            items, total, pages = report_audio_api._query_jobs_page(
                query,
                page=1,
                per_page=2,
                include_detail=False,
            )
        finally:
            event.remove(db.engine, "before_cursor_execute", capture)

        self.assertEqual(total, 3)
        self.assertEqual(pages, 2)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["request"]["pdf"]["filename"], "report-2.pdf")
        self.assertEqual(2, len(statements), statements)
        self.assertIn("select count(publication_tasks.id)", statements[0])

    def test_job_detail_page_batches_finalization_summaries(self):
        tasks = []
        for index in range(2):
            task = PublicationTask(
                status="completed",
                current_step="done",
                progress=100,
                feature=FEATURE_NAME,
                user_id="user-1",
                params={"feature": FEATURE_NAME, "user_id": "user-1"},
                result={},
                created_at=datetime(2026, 7, 18, 3, index, 0),
            )
            db.session.add(task)
            tasks.append(task)
        db.session.commit()
        expected = {
            str(task.id): {"job_id": f"finalization-{index}"}
            for index, task in enumerate(tasks)
        }
        query = PublicationTask.query.filter(
            PublicationTask.feature == FEATURE_NAME,
            PublicationTask.user_id == "user-1",
        )

        with (
            patch.object(
                report_audio_api,
                "finalization_summaries_for_audio_ids",
                return_value=expected,
                create=True,
            ) as batch,
            patch.object(report_audio_api, "_job_payload", wraps=report_audio_api._job_payload) as payload,
        ):
            items, total, pages = report_audio_api._query_jobs_page(
                query,
                page=1,
                per_page=2,
                include_detail=True,
            )

        self.assertEqual((len(items), total, pages), (2, 2, 1))
        batch.assert_called_once()
        requested_ids = batch.call_args.args[0]
        self.assertEqual(set(requested_ids), {str(task.id) for task in tasks})
        self.assertEqual(payload.call_count, 2)
        for call in payload.call_args_list:
            task = call.args[0]
            self.assertEqual(call.kwargs["finalization"], expected[str(task.id)])

    def test_clear_jobs_cache_matches_composite_visible_user_key(self):
        key = ("system+user-1", 1, 10, "", "", "")
        report_audio_api._REPORT_AUDIO_JOBS_CACHE[key] = {
            "expires_at": 9999999999,
            "stale_until": 9999999999,
            "payload": {"success": True},
        }

        report_audio_api._clear_jobs_summary_cache("user-1")

        self.assertNotIn(key, report_audio_api._REPORT_AUDIO_JOBS_CACHE)

    def test_media_range_lookup_reuses_cached_path(self):
        media_path = Path(self.tmpdir.name) / "audio.wav"
        media_path.write_bytes(b"audio")
        task = SimpleNamespace(id="job-1", report_audio_wav_path=str(media_path))
        calls = []

        def loader(_user, _job_id):
            calls.append(_job_id)
            return task

        user = SimpleNamespace(id="user-1")
        first = report_audio_api._cached_owned_media_path(
            user, "job-1", "audio", loader, lambda item: item.report_audio_wav_path
        )
        second = report_audio_api._cached_owned_media_path(
            user, "job-1", "audio", loader, lambda item: item.report_audio_wav_path
        )

        self.assertEqual(1, len(calls))
        self.assertEqual(str(media_path), first[1])
        self.assertEqual(str(media_path), second[1])

    def test_keyframe_payload_is_loaded_once_per_cache_window(self):
        task = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            feature=FEATURE_NAME,
            user_id="user-1",
            params={"feature": FEATURE_NAME, "user_id": "user-1"},
            result={"video": {"keyframes": {"cover": {"path": "/tmp/cover.jpg"}}}},
        )
        db.session.add(task)
        db.session.flush()
        task_id = task.id
        db.session.commit()
        statements = []

        def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
            if "publication_tasks" in statement.lower() and statement.lstrip().lower().startswith("select"):
                statements.append(statement)

        event.listen(db.engine, "before_cursor_execute", capture)
        try:
            user = SimpleNamespace(id="user-1")
            first = report_audio_api._get_owned_keyframe_task(user, task_id)
            second = report_audio_api._get_owned_keyframe_task(user, task_id)
        finally:
            event.remove(db.engine, "before_cursor_execute", capture)

        self.assertEqual(1, len(statements))
        self.assertEqual(first.result, second.result)
        self.assertEqual("/tmp/cover.jpg", second.result["video"]["keyframes"]["cover"]["path"])

    def test_landscape_keyframe_uses_structured_projection(self):
        keyframe_path = Path(self.tmpdir.name) / "landscape-keyframe.jpg"
        keyframe_path.write_bytes(b"keyframe")
        task = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            feature=FEATURE_NAME,
            user_id="user-1",
            params={"feature": FEATURE_NAME, "user_id": "user-1"},
            result={
                "video": {
                    "keyframes": {
                        "landscape_cover": {"path": str(keyframe_path)},
                    },
                },
            },
        )
        db.session.add(task)
        db.session.flush()
        task_id = task.id
        db.session.commit()
        statements = []

        def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
            if "publication_tasks" in statement.lower() and statement.lstrip().lower().startswith("select"):
                statements.append(statement.lower())

        loader, resolver = report_audio_api._keyframe_media_accessors("landscape_cover")
        event.listen(db.engine, "before_cursor_execute", capture)
        try:
            loaded = loader(SimpleNamespace(id="user-1"), task_id)
        finally:
            event.remove(db.engine, "before_cursor_execute", capture)

        self.assertEqual(str(keyframe_path), resolver(loaded))
        self.assertEqual(1, len(statements))
        self.assertNotIn("publication_tasks.result", statements[0])
        self.assertIn("report_audio_landscape_keyframe_path", statements[0])

    def test_pdf_asset_lookup_excludes_attachment_raw_payload(self):
        pdf_path = Path(self.tmpdir.name) / "report.pdf"
        pdf_path.write_bytes(b"%PDF-test")
        output_root = Path(self.tmpdir.name) / "content-pipeline-output"
        cleaned_path = output_root / "batch-1" / "pdf_cleaned" / pdf_path.name
        cleaned_path.parent.mkdir(parents=True)
        cleaned_path.write_bytes(b"%PDF-cleaned")
        topic = ZsxqTopic(
            group_id="group-1",
            group_name="测试星球",
            tab_id="tab-1",
            tab_label="研报",
            topic_id="topic-1",
            title="结构化标题",
            raw_payload={"large": "payload"},
        )
        attachment = ZsxqAttachment(
            group_id="group-1",
            tab_id="tab-1",
            topic_id="topic-1",
            attachment_key="attachment-1",
            name="report.pdf",
            local_path=str(pdf_path),
            download_status="downloaded",
            raw_payload={"large": "payload"},
        )
        clean_task = PublicationTask(
            feature="content_pdf_clean",
            status="completed",
            current_step="completed",
            progress=100,
            params={"feature": "content_pdf_clean"},
        )
        db.session.add_all([topic, attachment, clean_task])
        db.session.flush()
        db.session.add(ContentPipelineAsset(
            media_type="pdf",
            content_sha256="e" * 64,
            canonical_task_id=str(clean_task.id),
            source_path=str(pdf_path.resolve()),
            filename=pdf_path.name,
            source_mode="sidebar",
            status="cleaned",
            cleaned_path=str(cleaned_path.resolve()),
            cleaned_sha256="f" * 64,
            cleaned_bytes=cleaned_path.stat().st_size,
        ))
        attachment_id = attachment.id
        db.session.commit()
        statements = []

        def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
            if "zsxq_attachments" in statement.lower() and statement.lstrip().lower().startswith("select"):
                statements.append(statement.lower())

        with patch.dict(os.environ, {"CONTENT_PIPELINE_OUTPUT_ROOT": str(output_root)}, clear=False):
            event.listen(db.engine, "before_cursor_execute", capture)
            try:
                metadata = report_audio_api._pdf_meta_from_zsxq_attachment(attachment_id, "job-1")
            finally:
                event.remove(db.engine, "before_cursor_execute", capture)

        self.assertEqual("结构化标题", metadata["topic_title"])
        self.assertEqual("研报", metadata["tab_label"])
        self.assertEqual(1, len(statements))
        self.assertNotIn("raw_payload", statements[0])

    def test_terminal_owner_reference_is_shared_by_parallel_detail_subrequests(self):
        task = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            feature=FEATURE_NAME,
            user_id="user-1",
            params={"feature": FEATURE_NAME, "user_id": "user-1"},
            result={},
        )
        db.session.add(task)
        db.session.commit()
        task_id = task.id
        statements = []

        def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
            if "publication_tasks" in statement.lower() and statement.lstrip().lower().startswith("select"):
                statements.append(statement)

        event.listen(db.engine, "before_cursor_execute", capture)
        try:
            user = SimpleNamespace(id="user-1")
            first = report_audio_api._get_owned_task_reference(user, task_id)
            second = report_audio_api._get_owned_task_reference(user, task_id)
        finally:
            event.remove(db.engine, "before_cursor_execute", capture)

        self.assertEqual(1, len(statements))
        self.assertEqual(task_id, first.id)
        self.assertEqual(task_id, second.id)

    def test_active_owner_reference_is_not_cached(self):
        task = PublicationTask(
            status="processing",
            current_step="synthesis",
            progress=40,
            feature=FEATURE_NAME,
            user_id="user-1",
            params={"feature": FEATURE_NAME, "user_id": "user-1"},
            result={},
        )
        db.session.add(task)
        db.session.commit()
        task_id = task.id
        statements = []

        def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
            if "publication_tasks" in statement.lower() and statement.lstrip().lower().startswith("select"):
                statements.append(statement)

        event.listen(db.engine, "before_cursor_execute", capture)
        try:
            user = SimpleNamespace(id="user-1")
            report_audio_api._get_owned_task_reference(user, task_id)
            report_audio_api._get_owned_task_reference(user, task_id)
        finally:
            event.remove(db.engine, "before_cursor_execute", capture)

        self.assertEqual(2, len(statements))

    def test_browser_publish_child_list_extracts_only_required_json_keys(self):
        spec = BrowserPublishJobSpec(
            platform="douyin",
            feature_name="douyin_upload_test",
            platform_label="抖音",
            resolve_credential=lambda **_kwargs: {},
            allowed_visibility=frozenset({"private", "public"}),
            default_extra={"declaration": "none"},
        )
        child = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            feature=spec.feature_name,
            parent_task_id="parent-1",
            parent_feature=FEATURE_NAME,
            params={
                "feature": spec.feature_name,
                "parent_task_id": "parent-1",
                "parent_feature": FEATURE_NAME,
                spec.feature_name: {
                    "title": "结构化读取",
                    "submit": True,
                    "visibility": "public",
                    "declaration": "original",
                },
            },
            result={"publish_status": "published", "message": "ok"},
        )
        db.session.add(child)
        db.session.commit()
        statements = []

        def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
            if statement.lstrip().lower().startswith("select"):
                statements.append(statement)

        event.listen(db.engine, "before_cursor_execute", capture)
        try:
            rows = browser_publish_children(
                spec,
                "parent-1",
                parent_feature=FEATURE_NAME,
            )
        finally:
            event.remove(db.engine, "before_cursor_execute", capture)

        self.assertEqual(1, len(rows))
        self.assertEqual("结构化读取", rows[0]["title"])
        self.assertTrue(rows[0]["submit_requested"])
        self.assertEqual("original", rows[0]["declaration"])
        self.assertEqual(1, len(statements))
        normalized_sql = statements[0].lower()
        self.assertNotIn("publication_tasks.params as", normalized_sql)
        self.assertNotIn("publication_tasks.result as", normalized_sql)

    def test_cached_job_detail_refreshes_bilibili_upload_history(self):
        parent = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            params={"feature": FEATURE_NAME, "user_id": "user-1"},
            result={},
        )
        db.session.add(parent)
        db.session.commit()
        parent_id = parent.id
        cache_key = ("user-1", parent_id)
        version = report_audio_api._job_detail_version(parent)
        report_audio_api._REPORT_AUDIO_JOB_DETAIL_CACHE[cache_key] = {
            "version": version,
            "payload": {"job_id": parent_id, "bilibili_uploads": []},
        }
        child = create_bilibili_upload_task(
            parent,
            parent_feature=FEATURE_NAME,
            source_label=report_audio_api.BILIBILI_UPLOAD_SOURCE,
            video_path="/tmp/report.mp4",
            video_key="landscape",
            title="缓存后新增投稿",
            data={},
        )

        with patch.object(
            report_audio_api,
            "_get_owned_task_reference",
            return_value=parent,
        ):
            response = report_audio_api.get_job.__wrapped__(
                SimpleNamespace(id="user-1"),
                parent_id,
            )

        payload = response.get_json()
        self.assertEqual(len(payload["bilibili_uploads"]), 1)
        self.assertEqual(payload["bilibili_uploads"][0]["task_id"], child.id)
        self.assertEqual(payload["data"]["bilibili_uploads"][0]["title"], "缓存后新增投稿")

    def test_manual_retry_archives_queue_repair_and_starts_with_fresh_budget(self):
        parent = PublicationTask(
            status="failed",
            current_step="failed",
            progress=0,
            params={"feature": FEATURE_NAME, "user_id": "user-1"},
            result={
                "error": "queue_repair_limit_reached",
                "error_type": "queue_repair_limit_reached",
                "completed_at": "2026-07-20T01:58:21Z",
                "queue_repair": {"count": 3, "limit": 3, "reason": "queue_repair_limit_reached"},
            },
            error_message="任务入队修复已达到上限",
            completed_at=datetime(2026, 7, 20, 1, 58, 21),
        )
        db.session.add(parent)
        db.session.commit()

        with patch.object(
            report_audio_api,
            "_get_owned_task",
            return_value=parent,
        ), patch.object(
            report_audio_api,
            "require_report_audio_workers_ready",
            return_value={"ready": True},
        ), patch.object(
            report_audio_api,
            "maybe_guarded_apply_async",
            return_value={
                "async_result": SimpleNamespace(id="celery-manual-retry"),
                "deferred": False,
                "admission": {"allowed": True, "reason": "ok"},
            },
        ):
            response = report_audio_api.retry_job.__wrapped__(
                SimpleNamespace(id="user-1"),
                parent.id,
            )

        payload = response.get_json()
        db.session.refresh(parent)
        self.assertTrue(payload["success"])
        self.assertEqual(parent.status, "pending")
        self.assertEqual(parent.current_step, "queued")
        self.assertEqual(parent.celery_task_id, "celery-manual-retry")
        self.assertNotIn("queue_repair", parent.result)
        self.assertNotIn("error_type", parent.result)
        self.assertNotIn("completed_at", parent.result)
        self.assertEqual(parent.result["queue_repair_history"][-1]["count"], 3)
        self.assertEqual(parent.result["queue_repair_history"][-1]["archive_reason"], "manual_retry")

    def test_manual_retry_cleans_cached_evidence_contract_when_evidence_is_disabled(self):
        cached_contract = report_audio_api.DEFAULT_REVIEW_PROMPT.replace(
            "必须包含claim_text",
            "必须包含 claim_text",
        )
        parent = PublicationTask(
            status="failed",
            current_step="failed",
            progress=18,
            params={
                "feature": FEATURE_NAME,
                "user_id": "user-1",
                FEATURE_NAME: {
                    "pdf_evidence_enabled": False,
                    "review_prompt": cached_contract,
                },
            },
            result={"error": "picker_not_found"},
            error_message="picker_not_found",
        )
        db.session.add(parent)
        db.session.commit()

        with patch.object(
            report_audio_api,
            "_get_owned_task",
            return_value=parent,
        ), patch.object(
            report_audio_api,
            "require_report_audio_workers_ready",
            return_value={"ready": True},
        ), patch.object(
            report_audio_api,
            "maybe_guarded_apply_async",
            return_value={
                "async_result": SimpleNamespace(id="celery-clean-retry"),
                "deferred": False,
                "admission": {"allowed": True, "reason": "ok"},
            },
        ):
            response = report_audio_api.retry_job.__wrapped__(
                SimpleNamespace(id="user-1"),
                parent.id,
            )

        payload = response.get_json()
        db.session.refresh(parent)
        self.assertTrue(payload["success"])
        self.assertNotIn("PDF_EVIDENCE_V1_JSON", parent.params[FEATURE_NAME]["review_prompt"])

    def test_manual_retry_can_switch_failed_browser_tts_to_doubao_api(self):
        parent = PublicationTask(
            status="failed",
            current_step="failed",
            progress=88,
            params={
                "feature": FEATURE_NAME,
                "user_id": "user-1",
                FEATURE_NAME: {
                    "tts_provider": "doubao_browser",
                    "tts_voice": None,
                },
            },
            result={"error": "豆包浏览器朗读失败"},
            error_message="豆包浏览器朗读失败",
        )
        db.session.add(parent)
        db.session.commit()

        with self.app.test_request_context(
            "/retry",
            method="POST",
            json={
                "tts_provider": "doubao_api",
                "tts_voice": "zh_female_mizai_uranus_bigtts",
            },
        ), patch.object(
            report_audio_api,
            "_get_owned_task",
            return_value=parent,
        ), patch.object(
            report_audio_api,
            "is_doubao_api_configured",
            return_value=True,
        ), patch.object(
            report_audio_api,
            "require_report_audio_workers_ready",
            return_value={"ready": True},
        ), patch.object(
            report_audio_api,
            "maybe_guarded_apply_async",
            return_value={
                "async_result": SimpleNamespace(id="celery-api-fallback"),
                "deferred": False,
                "admission": {"allowed": True, "reason": "ok"},
            },
        ):
            response = report_audio_api.retry_job.__wrapped__(
                SimpleNamespace(id="user-1"),
                parent.id,
            )

        payload = response.get_json()
        db.session.refresh(parent)
        workflow = parent.params[FEATURE_NAME]
        self.assertTrue(payload["success"])
        self.assertEqual(workflow["tts_provider"], "doubao_api")
        self.assertEqual(workflow["tts_voice"], "zh_female_mizai_uranus_bigtts")
        self.assertEqual(parent.result["retry_tts_override"]["previous_provider"], "doubao_browser")
        self.assertEqual(parent.result["retry_tts_override"]["provider"], "doubao_api")

    def test_cover_lookup_reads_projection_columns_without_parent_result_json(self):
        root = Path(self.tmpdir.name)
        vertical_cover = root / "vertical.jpg"
        podcast_cover = root / "podcast.jpg"
        bilibili_cover = root / "bilibili.jpg"
        for path in (vertical_cover, podcast_cover, bilibili_cover):
            path.write_bytes(b"cover")

        parent = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            params={"feature": FEATURE_NAME, "user_id": "user-1"},
            result={
                "video": {
                    "vertical_cover_path": str(vertical_cover),
                    "podcast_cover_path": str(podcast_cover),
                    "bilibili_cover_path": str(bilibili_cover),
                },
            },
        )
        db.session.add(parent)
        db.session.commit()
        parent_id = parent.id
        db.session.expire_all()
        statements = []

        def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
            statements.append(statement.lower())

        event.listen(db.engine, "before_cursor_execute", capture)
        try:
            projected = report_audio_api._get_owned_media_task(
                SimpleNamespace(id="user-1"),
                parent_id,
            )
            self.assertEqual(
                report_audio_api._vertical_cover_path_for_task(projected),
                str(vertical_cover),
            )
            self.assertEqual(
                report_audio_api._podcast_cover_path_for_task(projected),
                str(podcast_cover),
            )
            self.assertEqual(
                report_audio_api._bilibili_cover_path_for_task(projected),
                str(bilibili_cover),
            )
        finally:
            event.remove(db.engine, "before_cursor_execute", capture)

        publication_selects = [
            statement
            for statement in statements
            if statement.lstrip().startswith("select") and "publication_tasks" in statement
        ]
        self.assertEqual(len(publication_selects), 1)
        self.assertNotIn("publication_tasks.result", publication_selects[0])

    def test_bilibili_enqueue_preserves_response_after_commit(self):
        parent = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            params={"feature": FEATURE_NAME, "user_id": "user-1"},
            result={},
        )
        db.session.add(parent)
        db.session.commit()
        parent_id = parent.id
        statements = []

        def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
            statements.append(statement.lower())

        event.listen(db.engine, "before_cursor_execute", capture)
        try:
            child = create_bilibili_upload_task(
                parent,
                parent_feature=FEATURE_NAME,
                source_label=report_audio_api.BILIBILI_UPLOAD_SOURCE,
                video_path="/tmp/report.mp4",
                video_key="landscape",
                title="测试投稿",
                data={},
            )
            preassigned_celery_id = child.celery_task_id

            with patch(
                "tasks.publisher.run_bilibili_upload_only.apply_async",
                side_effect=lambda *_args, **kwargs: SimpleNamespace(
                    id=kwargs.get("task_id")
                ),
            ) as apply_async:
                enqueue_bilibili_upload_task(child)
        finally:
            event.remove(db.engine, "before_cursor_execute", capture)

        response = child._bilibili_enqueue_response_payload
        self.assertEqual(response["task_id"], child.id)
        self.assertEqual(response["celery_id"], preassigned_celery_id)
        self.assertEqual(response["status"], "queued")
        self.assertEqual(response["title"], "测试投稿")
        self.assertTrue(preassigned_celery_id)
        self.assertNotEqual(child.id, preassigned_celery_id)
        self.assertEqual(child.celery_task_id, preassigned_celery_id)
        self.assertEqual(
            apply_async.call_args.kwargs.get("task_id"),
            preassigned_celery_id,
        )
        projection = PublicationTaskBilibiliUpload.query.filter_by(task_id=child.id).one()
        self.assertEqual(projection.parent_task_id, parent_id)
        self.assertEqual(projection.source_label, report_audio_api.BILIBILI_UPLOAD_SOURCE)
        publication_selects = [
            statement
            for statement in statements
            if statement.lstrip().startswith("select") and "publication_tasks" in statement
        ]
        self.assertEqual(publication_selects, [])
        publication_updates = [
            statement
            for statement in statements
            if statement.lstrip().startswith("update publication_tasks")
        ]
        self.assertEqual(publication_updates, [])

    def test_bilibili_submit_uses_one_detached_parent_and_credential_snapshot(self):
        root = Path(self.tmpdir.name)
        video_path = root / "landscape.mp4"
        cover_path = root / "bilibili-cover.jpg"
        video_path.write_bytes(b"video")
        cover_path.write_bytes(b"cover")
        parent = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            params={
                "feature": FEATURE_NAME,
                "user_id": "user-1",
                "report_audio_workflow": {
                    "metadata": {"title": "父任务标题"},
                    "pdf": {"filename": "中信证券-半导体-20260831.pdf"},
                },
            },
            result={
                "structured_result": {
                    "landscape_title": "半导体景气反转",
                    "landscape_subtitle": "供需拐点确认",
                },
                "video": {
                    "video_path": str(video_path),
                    "cover_path": str(cover_path),
                    "bilibili_cover_path": str(cover_path),
                },
                "raw_audit_payload": "x" * 100_000,
            },
        )
        credential = PlatformCredentialAccount(
            platform="bilibili",
            name="研报主账号",
            owner_label="研报账号展示名",
            encrypted_payload="encrypted",
            payload_fingerprint="report-audio-bili-narrow-snapshot",
        )
        db.session.add_all([parent, credential])
        db.session.commit()
        parent_id = str(parent.id)
        credential_id = str(credential.id)
        statements = []
        file_probe_transactions = []
        original_path_exists = report_audio_api._path_exists

        def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
            statements.append(" ".join(statement.lower().split()))

        def probe_path_without_db_hold(path):
            file_probe_transactions.append(db.session().in_transaction())
            return original_path_exists(path)

        def enqueue_without_broker(child):
            child._bilibili_enqueue_response_payload = dict(
                child._bilibili_create_response_payload
            )
            return child

        event.listen(db.engine, "before_cursor_execute", capture)
        try:
            with (
                self.app.test_request_context(
                    f"/api/report-audio-workflows/jobs/{parent_id}/bilibili-upload",
                    method="POST",
                    json={
                        "credential_id": credential_id,
                        "video_key": "landscape",
                        "charging_pay": {"enabled": False},
                    },
                ),
                patch.object(
                    report_audio_api,
                    "_path_exists",
                    side_effect=probe_path_without_db_hold,
                ),
                patch.object(
                    report_audio_api,
                    "enqueue_bilibili_upload_task",
                    side_effect=enqueue_without_broker,
                ),
            ):
                response = report_audio_api.submit_bilibili_upload.__wrapped__(
                    SimpleNamespace(id="user-1"),
                    parent_id,
                )
        finally:
            event.remove(db.engine, "before_cursor_execute", capture)

        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertIn("半导体景气反转", payload["upload_task"]["title"])
        self.assertEqual(
            payload["upload_task"]["credential_name"],
            "研报账号展示名",
        )
        self.assertTrue(file_probe_transactions)
        self.assertEqual(
            file_probe_transactions,
            [False] * len(file_probe_transactions),
            "任何视频或封面文件探测前都必须释放父任务数据库事务",
        )
        publication_selects = [
            statement
            for statement in statements
            if statement.startswith("select") and "publication_tasks" in statement
        ]
        self.assertEqual(len(publication_selects), 1)
        self.assertIn("platform_credential_accounts", publication_selects[0])
        self.assertNotIn(
            "publication_tasks.params as publication_tasks_params",
            publication_selects[0],
        )
        self.assertNotIn(
            "publication_tasks.result as publication_tasks_result",
            publication_selects[0],
        )
        standalone_credential_selects = [
            statement
            for statement in statements
            if statement.startswith("select")
            and "platform_credential_accounts" in statement
            and "publication_tasks" not in statement
        ]
        self.assertEqual(standalone_credential_selects, [])

    def test_bilibili_submit_uses_result_pdf_metadata_when_request_pdf_is_absent(self):
        root = Path(self.tmpdir.name)
        video_path = root / "result-only-landscape.mp4"
        cover_path = root / "result-only-cover.jpg"
        video_path.write_bytes(b"video")
        cover_path.write_bytes(b"cover")
        parent = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            created_at=datetime(2026, 8, 31, 9, 0, 0),
            params={"feature": FEATURE_NAME, "user_id": "user-1"},
            result={
                "pdf": {"filename": "广发证券-算力产业-20260830.pdf"},
                "structured_result": {
                    "landscape_title": "算力需求进入新拐点",
                },
                "video": {
                    "video_path": str(video_path),
                    "bilibili_cover_path": str(cover_path),
                },
            },
        )
        db.session.add(parent)
        db.session.commit()
        parent_id = str(parent.id)

        def enqueue_without_broker(upload_task):
            upload_task._bilibili_enqueue_response_payload = dict(
                upload_task._bilibili_create_response_payload
            )
            return upload_task

        with (
            self.app.test_request_context(
                f"/api/report-audio-workflows/jobs/{parent_id}/bilibili-upload",
                method="POST",
                json={
                    "video_key": "landscape",
                    "charging_pay": {"enabled": False},
                },
            ),
            patch.object(
                report_audio_api,
                "enqueue_bilibili_upload_task",
                side_effect=enqueue_without_broker,
            ),
        ):
            response = report_audio_api.submit_bilibili_upload.__wrapped__(
                SimpleNamespace(id="user-1"),
                parent_id,
            )

        payload = response.get_json()
        self.assertTrue(payload["success"])
        title = payload["upload_task"]["title"]
        self.assertIn("广发证券", title)
        self.assertIn("2026-08-30", title)

    def test_bilibili_submit_prefers_completed_child_bilibili_cover_over_generic_parent_cover(self):
        root = Path(self.tmpdir.name)
        video_path = root / "landscape.mp4"
        generic_cover_path = root / "generic-cover.jpg"
        child_bilibili_cover_path = root / "child-bilibili-cover.jpg"
        for path, body in (
            (video_path, b"video"),
            (generic_cover_path, b"generic"),
            (child_bilibili_cover_path, b"bilibili"),
        ):
            path.write_bytes(body)
        parent = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            params={"feature": FEATURE_NAME, "user_id": "user-1"},
            result={
                "video": {
                    "video_path": str(video_path),
                    "cover_path": str(generic_cover_path),
                },
            },
        )
        db.session.add(parent)
        db.session.flush()
        parent_id = str(parent.id)
        child = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            params={
                "feature": "publisher",
                "source_task_id": parent_id,
                "vertical_video": {"enabled": True},
            },
            result={
                "vertical_video_path": str(video_path),
                "bilibili_cover_path": str(child_bilibili_cover_path),
            },
            source_task_id=parent_id,
        )
        db.session.add(child)
        db.session.commit()
        file_probe_transactions = []
        original_path_exists = report_audio_api._path_exists

        def probe_path_without_db_hold(path):
            file_probe_transactions.append(db.session().in_transaction())
            return original_path_exists(path)

        def enqueue_without_broker(upload_task):
            upload_task._bilibili_enqueue_response_payload = dict(
                upload_task._bilibili_create_response_payload
            )
            return upload_task

        with (
            self.app.test_request_context(
                f"/api/report-audio-workflows/jobs/{parent_id}/bilibili-upload",
                method="POST",
                json={
                    "video_key": "landscape",
                    "title": "保留专用封面优先级",
                    "charging_pay": {"enabled": False},
                },
            ),
            patch.object(
                report_audio_api,
                "enqueue_bilibili_upload_task",
                side_effect=enqueue_without_broker,
            ),
            patch.object(
                report_audio_api,
                "_path_exists",
                side_effect=probe_path_without_db_hold,
            ),
        ):
            response = report_audio_api.submit_bilibili_upload.__wrapped__(
                SimpleNamespace(id="user-1"),
                parent_id,
            )

        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertTrue(file_probe_transactions)
        self.assertEqual(
            file_probe_transactions,
            [False] * len(file_probe_transactions),
            "child fallback 查询后也必须在文件探测前释放数据库事务",
        )
        self.assertEqual(
            payload["upload_task"]["cover_key"],
            "bilibili_cover",
        )
        upload_projection = PublicationTaskBilibiliUpload.query.filter_by(
            task_id=payload["upload_task"]["task_id"]
        ).one()
        self.assertEqual(
            upload_projection.cover_path,
            str(child_bilibili_cover_path),
        )

    def test_bilibili_upload_history_is_one_structured_query_without_task_json(self):
        parent = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            params={"feature": FEATURE_NAME, "user_id": "user-1"},
            result={},
        )
        db.session.add(parent)
        db.session.commit()
        for index in range(3):
            create_bilibili_upload_task(
                parent,
                parent_feature=FEATURE_NAME,
                source_label=report_audio_api.BILIBILI_UPLOAD_SOURCE,
                video_path=f"/tmp/report-{index}.mp4",
                video_key="landscape",
                title=f"测试投稿 {index}",
                data={"season_id": index + 1},
            )
        parent_id = parent.id

        statements = []

        def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
            statements.append(statement)

        event.listen(db.engine, "before_cursor_execute", capture)
        try:
            items = bilibili_upload_children(
                parent_id,
                parent_feature=FEATURE_NAME,
                source_label=report_audio_api.BILIBILI_UPLOAD_SOURCE,
            )
        finally:
            event.remove(db.engine, "before_cursor_execute", capture)

        selects = [statement.lower() for statement in statements if statement.lstrip().lower().startswith("select")]
        self.assertEqual(len(items), 3)
        self.assertEqual(len(selects), 1)
        self.assertIn("publication_task_bilibili_uploads", selects[0])
        self.assertNotIn("publication_tasks.params", selects[0])
        self.assertNotIn("publication_tasks.result", selects[0])

    def test_browser_publish_enqueue_uses_detached_preallocated_broker_snapshot(self):
        root = Path(self.tmpdir.name)
        video_path = root / "browser-publish.mp4"
        video_path.write_bytes(b"video")
        spec = BrowserPublishJobSpec(
            platform="douyin",
            feature_name="douyin_upload_test",
            platform_label="抖音",
            resolve_credential=lambda **_kwargs: {
                "credential_id": "douyin-account-1",
                "credential_name": "抖音测试账号",
            },
            allowed_visibility=frozenset({"private", "public"}),
        )
        statements = []
        broker_transaction_states = []
        phase = {"broker_started": False}

        def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
            statements.append((
                phase["broker_started"],
                " ".join(statement.lower().split()),
            ))

        def publish_without_broker(
            _platform,
            _task_id,
            *,
            broker_task_id=None,
            retry,
        ):
            broker_transaction_states.append(db.session().in_transaction())
            phase["broker_started"] = True
            return {"celery_id": broker_task_id or "legacy-generated-broker-id"}

        event.listen(db.engine, "before_cursor_execute", capture)
        try:
            with patch(
                "services.browser_publish_jobs.dispatch_platform_publish",
                side_effect=publish_without_broker,
            ):
                task = create_browser_publish_task(
                    spec,
                    current_user=SimpleNamespace(id="user-1"),
                    video_path=video_path,
                    title="浏览器发布窄快照",
                    credential_id="douyin-account-1",
                    description="测试简介",
                    tags=["财经"],
                    visibility="private",
                )
                task = enqueue_browser_publish_task(spec, task)
                payload = browser_publish_payload(spec, task)
        finally:
            event.remove(db.engine, "before_cursor_execute", capture)

        self.assertEqual(payload["status"], "queued")
        self.assertEqual(payload["credential_name"], "抖音测试账号")
        self.assertEqual(broker_transaction_states, [False])
        task_selects = [
            statement
            for _after_broker, statement in statements
            if statement.startswith("select") and "publication_tasks" in statement
        ]
        self.assertEqual(task_selects, [])
        post_broker_selects = [
            statement
            for after_broker, statement in statements
            if after_broker and statement.startswith("select")
        ]
        self.assertEqual(post_broker_selects, [])

    def test_browser_publish_cancel_persists_before_revoke_and_returns_snapshot(self):
        root = Path(self.tmpdir.name)
        video_path = root / "browser-cancel.mp4"
        video_path.write_bytes(b"video")
        spec = BrowserPublishJobSpec(
            platform="wechat_channels",
            feature_name="wechat_channels_upload_test",
            platform_label="视频号",
            resolve_credential=lambda **_kwargs: {
                "credential_id": "channels-account-1",
                "credential_name": "视频号测试账号",
            },
            allowed_visibility=frozenset({"private", "public"}),
        )
        task = create_browser_publish_task(
            spec,
            current_user=SimpleNamespace(id="user-1"),
            video_path=video_path,
            title="视频号取消事务边界",
            credential_id="channels-account-1",
            visibility="private",
        )
        task.celery_task_id = "channels-cancel-broker-id"
        db.session.commit()
        task_id = str(task.id)
        statements = []
        revoke_transaction_states = []
        phase = {"revoke_started": False}

        def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
            statements.append((
                phase["revoke_started"],
                " ".join(statement.lower().split()),
            ))

        def revoke_without_broker(*_args, **_kwargs):
            revoke_transaction_states.append(db.session().in_transaction())
            phase["revoke_started"] = True

        event.listen(db.engine, "before_cursor_execute", capture)
        try:
            with patch(
                "celery_config.celery_app.control.revoke",
                side_effect=revoke_without_broker,
            ):
                ok, message = cancel_browser_publish_task(spec, task)
                payload = getattr(
                    task,
                    "_browser_publish_cancel_response_payload",
                    None,
                ) or browser_publish_payload(spec, task)
        finally:
            event.remove(db.engine, "before_cursor_execute", capture)

        self.assertTrue(ok)
        self.assertIn("已取消", message)
        self.assertEqual(payload["task_id"], task_id)
        self.assertEqual(payload["status"], "canceled")
        self.assertEqual(revoke_transaction_states, [False])
        post_revoke_selects = [
            statement
            for after_revoke, statement in statements
            if after_revoke and statement.startswith("select")
        ]
        self.assertEqual(post_revoke_selects, [])

    def test_browser_publish_cancel_keeps_terminal_guard_when_revoke_fails(self):
        root = Path(self.tmpdir.name)
        video_path = root / "browser-cancel-failure.mp4"
        video_path.write_bytes(b"video")
        spec = BrowserPublishJobSpec(
            platform="douyin",
            feature_name="douyin_upload_test",
            platform_label="抖音",
            resolve_credential=lambda **_kwargs: {
                "credential_id": "douyin-account-2",
                "credential_name": "抖音备用账号",
            },
            allowed_visibility=frozenset({"private"}),
        )
        task = create_browser_publish_task(
            spec,
            current_user=SimpleNamespace(id="user-1"),
            video_path=video_path,
            title="控制面失败仍取消",
            credential_id="douyin-account-2",
            visibility="private",
        )
        task.celery_task_id = "douyin-cancel-broker-id"
        db.session.commit()
        task_id = str(task.id)

        with patch(
            "celery_config.celery_app.control.revoke",
            side_effect=RuntimeError("broker control unavailable"),
        ):
            try:
                ok, message = cancel_browser_publish_task(spec, task)
            except RuntimeError as exc:
                self.fail(f"持久化取消不能被 broker revoke 失败回滚: {exc}")

        self.assertTrue(ok)
        self.assertIn("已取消", message)
        persisted = db.session.get(PublicationTask, task_id)
        self.assertEqual(persisted.status, "canceled")
        self.assertEqual(persisted.current_step, "canceled")

    def test_failed_bilibili_upload_can_be_requeued_without_rebuilding_media(self):
        parent = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            params={"feature": FEATURE_NAME, "user_id": "user-1"},
            result={},
        )
        db.session.add(parent)
        db.session.commit()
        child = create_bilibili_upload_task(
            parent,
            parent_feature=FEATURE_NAME,
            source_label=report_audio_api.BILIBILI_UPLOAD_SOURCE,
            video_path="/tmp/retry-report.mp4",
            video_key="landscape",
            title="失败后重投",
            data={"credential_id": "credential-1", "season_id": "123"},
        )
        persisted = PublicationTask.query.filter_by(id=child.id).one()
        persisted.status = "failed"
        persisted.current_step = "failed"
        persisted.progress = 10
        persisted.error_message = "HTTP 403"
        persisted.completed_at = datetime.utcnow()
        db.session.commit()

        with patch(
            "services.bilibili_publish.dispatch_platform_publish",
            return_value={"celery_id": "retry-celery-id"},
        ) as dispatch:
            retried = retry_bilibili_upload_child(
                child.id,
                parent_task_id=parent.id,
                parent_feature=FEATURE_NAME,
                source_label=report_audio_api.BILIBILI_UPLOAD_SOURCE,
            )

        self.assertEqual(retried.id, child.id)
        self.assertEqual(retried.status, "pending")
        self.assertEqual(retried.current_step, "queued")
        self.assertEqual(retried.progress, 0)
        self.assertEqual(retried.error_message, None)
        self.assertEqual(retried.celery_task_id, "retry-celery-id")
        dispatch.assert_called_once()
        broker_task_id = dispatch.call_args.kwargs["broker_task_id"]
        self.assertTrue(broker_task_id)
        self.assertNotEqual(broker_task_id, child.id)
        self.assertEqual(dispatch.call_args.args[0], "bilibili")
        self.assertEqual(dispatch.call_args.args[1], child.id)
        self.assertEqual(
            PublicationTask.query.filter_by(id=child.id).one().result["bilibili_retry_history"][-1]["previous_error"],
            "HTTP 403",
        )

    def test_bilibili_retry_uses_detached_narrow_state_across_broker_publish(self):
        parent = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            params={
                "feature": FEATURE_NAME,
                "user_id": "user-1",
                "raw_request_audit": "p" * 100_000,
            },
            result={"raw_parent_audit": "r" * 100_000},
        )
        db.session.add(parent)
        db.session.commit()
        child = create_bilibili_upload_task(
            parent,
            parent_feature=FEATURE_NAME,
            source_label=report_audio_api.BILIBILI_UPLOAD_SOURCE,
            video_path="/tmp/retry-narrow-report.mp4",
            video_key="landscape",
            title="重投窄快照",
            data={"credential_id": "credential-1", "season_id": "456"},
            result_extra={"raw_child_audit": "x" * 100_000},
        )
        child_id = str(child.id)
        parent_id = str(parent.id)
        persisted = PublicationTask.query.filter_by(id=child_id).one()
        persisted.status = "failed"
        persisted.current_step = "failed"
        persisted.progress = 23
        persisted.error_message = "HTTP 502"
        persisted.completed_at = datetime.utcnow()
        db.session.commit()

        statements = []
        broker_transaction_states = []
        phase = {"broker_started": False}

        def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
            statements.append((
                phase["broker_started"],
                " ".join(statement.lower().split()),
            ))

        def publish_without_broker(_platform, _task_id, *, broker_task_id, retry):
            broker_transaction_states.append(db.session().in_transaction())
            phase["broker_started"] = True
            return {"celery_id": broker_task_id}

        event.listen(db.engine, "before_cursor_execute", capture)
        try:
            with (
                self.app.test_request_context(
                    f"/api/report-audio-workflows/jobs/{parent_id}/"
                    f"bilibili-upload/{child_id}/retry",
                    method="POST",
                ),
                patch(
                    "services.bilibili_publish.dispatch_platform_publish",
                    side_effect=publish_without_broker,
                ),
            ):
                response = report_audio_api.retry_bilibili_upload.__wrapped__(
                    SimpleNamespace(id="user-1"),
                    parent_id,
                    child_id,
                )
                payload = response.get_json()
        finally:
            event.remove(db.engine, "before_cursor_execute", capture)

        self.assertTrue(payload["success"])
        self.assertEqual(payload["upload_task"]["task_id"], child_id)
        self.assertEqual(payload["upload_task"]["status"], "queued")
        self.assertEqual(broker_transaction_states, [False])
        task_selects = [
            statement
            for _after_broker, statement in statements
            if statement.startswith("select") and "publication_tasks" in statement
        ]
        self.assertTrue(task_selects)
        for statement in task_selects:
            self.assertNotIn("publication_tasks.params", statement)
        post_broker_selects = [
            statement
            for after_broker, statement in statements
            if after_broker and statement.startswith("select")
        ]
        self.assertEqual(post_broker_selects, [])

    def test_report_bilibili_cancel_uses_narrow_state_and_releases_before_revoke(self):
        parent = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            params={
                "feature": FEATURE_NAME,
                "user_id": "user-1",
                "raw_request_audit": "p" * 100_000,
            },
            result={"raw_parent_audit": "r" * 100_000},
        )
        db.session.add(parent)
        db.session.flush()
        parent_id = str(parent.id)
        child = PublicationTask(
            feature="publisher",
            user_id="user-1",
            parent_task_id=parent_id,
            parent_feature=FEATURE_NAME,
            source_task_id=parent_id,
            source_feature=FEATURE_NAME,
            status="pending",
            current_step="queued",
            progress=0,
            celery_task_id="report-cancel-broker-id",
            params={
                "feature": "publisher",
                "user_id": "user-1",
                "parent_task_id": parent_id,
                "parent_feature": FEATURE_NAME,
                "source_task_id": parent_id,
                "source_feature": FEATURE_NAME,
                "raw_request_audit": "c" * 100_000,
            },
            result={"raw_child_audit": "d" * 100_000},
        )
        db.session.add(child)
        db.session.flush()
        child_id = str(child.id)
        db.session.add(PublicationTaskBilibiliUpload(
            task_id=child_id,
            parent_task_id=parent_id,
            parent_feature=FEATURE_NAME,
            source_label=report_audio_api.BILIBILI_UPLOAD_SOURCE,
            video_key="landscape",
            title="取消窄快照",
            video_path="/tmp/report-cancel.mp4",
            tags=["财经"],
        ))
        db.session.commit()

        statements = []
        revoke_transaction_states = []
        phase = {"revoke_started": False}

        def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
            statements.append((
                phase["revoke_started"],
                " ".join(statement.lower().split()),
            ))

        def revoke_without_broker(*_args, **_kwargs):
            revoke_transaction_states.append(db.session().in_transaction())
            phase["revoke_started"] = True

        event.listen(db.engine, "before_cursor_execute", capture)
        try:
            with (
                self.app.test_request_context(
                    f"/api/report-audio-workflows/jobs/{parent_id}/"
                    f"bilibili-upload/{child_id}/cancel",
                    method="POST",
                ),
                patch.object(
                    report_audio_api.celery_app.control,
                    "revoke",
                    side_effect=revoke_without_broker,
                ),
            ):
                response = report_audio_api.cancel_bilibili_upload.__wrapped__(
                    SimpleNamespace(id="user-1"),
                    parent_id,
                    child_id,
                )
                payload = response[0].get_json() if isinstance(response, tuple) else response.get_json()
        finally:
            event.remove(db.engine, "before_cursor_execute", capture)

        self.assertTrue(payload["success"], payload)
        self.assertEqual(payload["upload_task"]["status"], "canceled")
        self.assertEqual(revoke_transaction_states, [False])
        task_selects = [
            statement
            for _after_revoke, statement in statements
            if statement.startswith("select") and "publication_tasks" in statement
        ]
        self.assertTrue(task_selects)
        for statement in task_selects:
            self.assertNotIn("publication_tasks.params", statement)
            self.assertNotIn("publication_tasks.result", statement)
        post_revoke_selects = [
            statement
            for after_revoke, statement in statements
            if after_revoke and statement.startswith("select")
        ]
        self.assertEqual(post_revoke_selects, [])

    def test_bilibili_upload_retry_rejects_completed_remote_submission(self):
        parent = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            params={"feature": FEATURE_NAME, "user_id": "user-1"},
            result={},
        )
        db.session.add(parent)
        db.session.commit()
        child = create_bilibili_upload_task(
            parent,
            parent_feature=FEATURE_NAME,
            source_label=report_audio_api.BILIBILI_UPLOAD_SOURCE,
            video_path="/tmp/already-submitted.mp4",
            video_key="landscape",
            title="已有稿件",
            data={},
        )
        upload = PublicationTaskBilibiliUpload.query.filter_by(task_id=child.id).one()
        upload.bvid = "BV1already"
        task = PublicationTask.query.filter_by(id=child.id).one()
        task.status = "failed"
        task.error_message = "网络响应中断"
        db.session.commit()

        with self.assertRaisesRegex(RuntimeError, "已有 B站稿件标识"):
            retry_bilibili_upload_child(
                child.id,
                parent_task_id=parent.id,
                parent_feature=FEATURE_NAME,
                source_label=report_audio_api.BILIBILI_UPLOAD_SOURCE,
            )

    def test_bilibili_upload_retry_restores_failed_state_when_enqueue_fails(self):
        parent = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            params={"feature": FEATURE_NAME, "user_id": "user-1"},
            result={},
        )
        db.session.add(parent)
        db.session.commit()
        child = create_bilibili_upload_task(
            parent,
            parent_feature=FEATURE_NAME,
            source_label=report_audio_api.BILIBILI_UPLOAD_SOURCE,
            video_path="/tmp/retry-fails.mp4",
            video_key="landscape",
            title="重投入队失败",
            data={},
        )
        task = PublicationTask.query.filter_by(id=child.id).one()
        task.status = "failed"
        task.current_step = "failed"
        task.error_message = "旧错误"
        db.session.commit()

        with patch(
            "services.bilibili_publish.dispatch_platform_publish",
            side_effect=RuntimeError("broker offline"),
        ):
            with self.assertRaisesRegex(RuntimeError, "重投入队失败"):
                retry_bilibili_upload_child(
                    child.id,
                    parent_task_id=parent.id,
                    parent_feature=FEATURE_NAME,
                    source_label=report_audio_api.BILIBILI_UPLOAD_SOURCE,
                )

        restored = PublicationTask.query.filter_by(id=child.id).one()
        self.assertEqual(restored.status, "failed")
        self.assertEqual(restored.current_step, "failed")
        self.assertIn("broker offline", restored.error_message)

    def test_bilibili_worker_context_is_one_structured_query_without_task_json(self):
        parent = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            params={"feature": FEATURE_NAME, "user_id": "user-1"},
            result={},
        )
        db.session.add(parent)
        db.session.commit()
        with patch("services.bilibili_publish.time.time", return_value=1_800_000_000):
            child = create_bilibili_upload_task(
                parent,
                parent_feature=FEATURE_NAME,
                source_label=report_audio_api.BILIBILI_UPLOAD_SOURCE,
                video_path="/tmp/report.mp4",
                video_key="landscape",
                title="结构化投稿",
                cover_path="/tmp/report.jpg",
                data={
                    "description": "完整简介",
                    "tags": ["财经", "研报解读"],
                    "tid": 207,
                    "copyright": 2,
                    "no_reprint": 0,
                    "dynamic": "动态文案",
                    "neutral_mark": "个人观点",
                    "isOnlySelf": 1,
                    "space_hidden": 0,
                    "creation_statement_id": 2,
                    "schedule_enabled": True,
                    "delay_time": 1_800_000_600,
                    "season_id": 9988,
                    "charging_pay": {
                        "enabled": True,
                        "preview": {"start_time": 2, "end_time": 8},
                        "upower_level_id": "level-1",
                        "upower_unit_price": 30,
                        "upower_mode": 1,
                        "member_first": {"exp_time": 300},
                    },
                },
            )
        statements = []

        def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
            if statement.lstrip().lower().startswith("select"):
                statements.append(statement.lower())

        event.listen(db.engine, "before_cursor_execute", capture)
        try:
            context = bilibili_upload_execution_context(child.id)
        finally:
            event.remove(db.engine, "before_cursor_execute", capture)

        self.assertEqual(len(statements), 1)
        self.assertIn("publication_task_bilibili_uploads", statements[0])
        self.assertNotIn("publication_tasks.params", statements[0])
        self.assertNotIn("publication_tasks.result", statements[0])
        self.assertEqual(context.video_path, "/tmp/report.mp4")
        self.assertEqual(context.cover_path, "/tmp/report.jpg")
        self.assertEqual(context.description, "完整简介")
        self.assertEqual(context.tags, ["财经", "研报解读"])
        self.assertEqual(context.copyright, 2)
        self.assertEqual(context.no_reprint, 0)
        self.assertEqual(context.dynamic, "动态文案")
        self.assertEqual(context.neutral_mark, "个人观点")
        self.assertEqual(context.is_only_self, 1)
        self.assertEqual(context.space_hidden, 0)
        self.assertEqual(context.creation_statement_id, 2)
        self.assertEqual(context.delay_time, 1_800_000_600)
        self.assertEqual(context.season_id, 9988)
        self.assertEqual(context.charging_pay["preview"]["start_time"], 2)
        self.assertEqual(context.charging_pay["preview"]["end_time"], 8)
        self.assertEqual(context.charging_pay["upower_level_id"], "level-1")

    def test_bilibili_result_patch_compiles_to_one_postgresql_jsonb_update(self):
        from tasks import publisher as publisher_tasks

        expression = publisher_tasks._postgres_jsonb_merge(
            PublicationTask.result,
            {"bilibili_result": {"bvid": "BV1STRUCTURED"}},
        )
        statement = (
            update(PublicationTask)
            .where(PublicationTask.id == "task-1")
            .values({PublicationTask.result: expression})
        )
        sql = str(statement.compile(dialect=postgresql_dialect()))

        self.assertIn("CAST(publication_tasks.result AS JSONB)", sql)
        self.assertIn(" || CAST(", sql)
        self.assertNotIn("SELECT", sql.upper())

    def test_bilibili_worker_full_call_uses_projection_and_preserves_upload_command(self):
        from tasks import publisher as publisher_tasks

        video_path = Path(self.tmpdir.name) / "structured-upload.mp4"
        cover_path = Path(self.tmpdir.name) / "structured-cover.jpg"
        video_path.write_bytes(b"video")
        cover_path.write_bytes(b"cover")
        parent = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            params={"feature": FEATURE_NAME, "user_id": "user-1"},
            result={},
        )
        db.session.add(parent)
        db.session.commit()
        child = create_bilibili_upload_task(
            parent,
            parent_feature=FEATURE_NAME,
            source_label=report_audio_api.BILIBILI_UPLOAD_SOURCE,
            video_path=str(video_path),
            video_key="landscape",
            title="完整执行投稿",
            cover_path=str(cover_path),
            data={
                "description": "执行简介",
                "tags": ["财经", "执行契约"],
                "tid": 207,
                "copyright": 2,
                "no_reprint": 0,
                "dynamic": "执行动态",
                "neutral_mark": "个人观点",
                "is_only_self": 1,
                "space_hidden": 0,
                "creation_statement_id": 2,
                "season_id": 7788,
            },
        )
        statements = []
        upload_kwargs = {}

        def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
            if statement.lstrip().lower().startswith("select"):
                statements.append(statement.lower())

        class FakeUploader:
            async def upload_single(self, **kwargs):
                upload_kwargs.update(kwargs)
                PublicationTask.query.filter_by(id=child.id).update({
                    PublicationTask.error_message:
                        "publisher orphan cleanup raced with the active worker",
                })
                db.session.commit()
                return {"aid": 123, "bvid": "BV1FULLWORKER"}

        auth = {
            "credential_id": "credential-1",
            "credential_name": "主账号",
            "sessdata": "sessdata",
            "bili_jct": "csrf",
            "buvid3": "buvid",
            "dedeuserid": "123",
        }
        event.listen(db.engine, "before_cursor_execute", capture)
        try:
            with patch("app.create_app", return_value=self.app), patch.object(
                publisher_tasks,
                "guard_publication_task_start",
                return_value=None,
            ), patch.object(
                publisher_tasks,
                "resolve_bilibili_auth",
                return_value=auth,
            ), patch.object(
                publisher_tasks,
                "BiliUploader",
                return_value=FakeUploader(),
            ), patch(
                "services.platform_credential_service.mark_credential_success",
                return_value=None,
            ):
                publisher_tasks.run_bilibili_upload_only.run(child.id)
        finally:
            event.remove(db.engine, "before_cursor_execute", capture)

        projection_selects = [
            statement
            for statement in statements
            if "publication_task_bilibili_uploads" in statement
        ]
        self.assertEqual(len(projection_selects), 1)
        self.assertNotIn("publication_tasks.params", projection_selects[0])
        self.assertNotIn("publication_tasks.result", projection_selects[0])
        self.assertFalse(any("publication_tasks.params" in statement for statement in statements))
        # SQLite needs one compatibility SELECT to emulate PostgreSQL's atomic
        # JSONB patch. The PostgreSQL compilation test above proves production
        # emits one UPDATE and no SELECT-before-UPDATE.
        result_selects = [
            statement for statement in statements if "publication_tasks.result" in statement
        ]
        self.assertEqual(len(result_selects), 1)
        self.assertIn("select publication_tasks.result", result_selects[0])

        self.assertEqual(upload_kwargs["video_path"], str(video_path))
        self.assertEqual(upload_kwargs["cover_path"], str(cover_path))
        self.assertEqual(upload_kwargs["title"], "完整执行投稿")
        self.assertEqual(upload_kwargs["desc"], "执行简介")
        self.assertEqual(upload_kwargs["tags"], ["财经", "执行契约"])
        self.assertEqual(upload_kwargs["copyright"], 2)
        self.assertEqual(upload_kwargs["no_reprint"], 0)
        self.assertEqual(upload_kwargs["dynamic"], "执行动态")
        self.assertEqual(upload_kwargs["is_only_self"], 1)
        self.assertEqual(upload_kwargs["space_hidden"], 0)
        self.assertEqual(upload_kwargs["creation_statement_id"], 2)
        self.assertEqual(upload_kwargs["season_id"], 7788)

        task = PublicationTask.query.filter_by(id=child.id).one()
        projection = PublicationTaskBilibiliUpload.query.filter_by(task_id=child.id).one()
        self.assertEqual(task.status, "completed")
        self.assertIsNone(task.error_message)
        self.assertEqual(task.result["bilibili_result"]["bvid"], "BV1FULLWORKER")
        self.assertEqual(projection.aid, "123")
        self.assertEqual(projection.bvid, "BV1FULLWORKER")
        self.assertEqual(projection.credential_name, "主账号")

    def test_bilibili_worker_terminal_update_syncs_structured_result(self):
        from tasks.publisher import update_task_record

        parent = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            params={"feature": FEATURE_NAME, "user_id": "user-1"},
            result={},
        )
        db.session.add(parent)
        db.session.commit()
        child = create_bilibili_upload_task(
            parent,
            parent_feature=FEATURE_NAME,
            source_label=report_audio_api.BILIBILI_UPLOAD_SOURCE,
            video_path="/tmp/report.mp4",
            video_key="landscape",
            title="提交前标题",
            data={},
        )

        update_task_record(
            child.id,
            status="completed",
            current_step="done",
            progress=100,
            result={
                "bilibili_result": {"aid": 123, "bvid": "BV1STRUCTURED"},
                "bilibili_title": "提交后标题",
                "bilibili_credential_id": "credential-1",
                "bilibili_credential_name": "主账号",
            },
            completed_at=datetime.utcnow(),
        )

        projection = PublicationTaskBilibiliUpload.query.filter_by(task_id=child.id).one()
        self.assertEqual(projection.aid, "123")
        self.assertEqual(projection.bvid, "BV1STRUCTURED")
        self.assertEqual(projection.title, "提交后标题")
        self.assertEqual(projection.credential_name, "主账号")

    def test_job_payload_exposes_audio_script_quality_issue(self):
        parent = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            params={
                "feature": FEATURE_NAME,
                "user_id": "user-1",
                "report_audio_workflow": {
                    "pdf": {"filename": "sample.pdf"},
                },
            },
            result={
                "structured_result": {
                    "provider": "deepseek",
                    "final_script_for_tts": "第一部分：开场与变量切换 这里才是正文。",
                },
                "tts_text": {"chars": 23, "chunks": 1},
            },
        )
        db.session.add(parent)
        db.session.commit()

        payload = report_audio_api._job_payload(parent)

        self.assertEqual(payload["audio_script"]["provider"], "deepseek")
        self.assertEqual(payload["audio_script"]["chunks"], 1)
        self.assertIn("第一部分", payload["audio_script"]["text"])
        self.assertIn("结构", payload["audio_script"]["quality_issue"])

    def test_job_payload_repairs_legacy_generic_gfhk_cover_prefix_for_display(self):
        parent = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            params={
                "feature": FEATURE_NAME,
                "user_id": "user-1",
                "report_audio_workflow": {
                    "pdf": {"filename": "GHHK - AI Capex & Platform Value Shift.pdf"},
                },
            },
            result={
                "structured_result": {
                    "title": "广发证券：2028年资本开支增速急刹车",
                    "cover_title": "机构：AI资本开支与平台价值转移\n2028年增速急刹车",
                },
                "pdf": {"filename": "GHHK - AI Capex & Platform Value Shift.pdf"},
            },
        )
        db.session.add(parent)
        db.session.commit()

        payload = report_audio_api._job_payload(parent)

        self.assertTrue(payload["title"].startswith("香港广发："))
        self.assertTrue(payload["cover_title"].startswith("香港广发："))
        self.assertNotIn("机构：", payload["title"])

    def test_job_status_payload_omits_raw_json_payloads(self):
        parent = PublicationTask(
            status="processing",
            current_step="tts",
            progress=48,
            error_message=None,
            params={
                "feature": FEATURE_NAME,
                "user_id": "user-1",
                "report_audio_workflow": {"prompt": "large prompt"},
            },
            result={
                "logs": [{"step": "tts", "message": "running"}],
                "structured_result": {"final_script_for_tts": "large script"},
            },
        )
        db.session.add(parent)
        db.session.commit()

        payload = report_audio_api._job_status_payload(parent)

        self.assertEqual(payload["job_id"], parent.id)
        self.assertEqual(payload["status"], "running")
        self.assertEqual(payload["step"], "tts")
        self.assertEqual(payload["progress"], 48)
        self.assertNotIn("raw_params", payload)
        self.assertNotIn("raw_result", payload)
        self.assertNotIn("logs", payload)
        self.assertNotIn("structured_result", payload)

    def test_job_payload_exposes_capacity_wait_with_next_retry_time(self):
        parent = PublicationTask(
            status="pending",
            current_step="deferred_capacity",
            progress=95,
            params={
                "feature": FEATURE_NAME,
                "user_id": "user-1",
                "report_audio_workflow": {"pdf": {"filename": "capacity.pdf"}},
            },
            result={
                "runtime_guard": {
                    "reason": "feature_capacity",
                    "message": "系统保护：任务稍后自动重试",
                    "active_count": 2,
                    "limit": 2,
                    "countdown": 180,
                    "queue": "report_audio_workflow",
                    "created_at": "2026-08-10T01:00:00Z",
                }
            },
        )
        db.session.add(parent)
        db.session.commit()

        payload = report_audio_api._job_payload(parent)

        capacity = payload["orchestration"]["capacity"]
        self.assertEqual(capacity["active_count"], 2)
        self.assertEqual(capacity["limit"], 2)
        self.assertEqual(capacity["countdown"], 180)
        self.assertEqual(capacity["next_retry_at"], "2026-08-10T01:03:00Z")
        fragments, _request = report_audio_api._status_orchestration_fragments(parent.id)
        self.assertEqual(fragments["runtime_guard"]["active_count"], 2)

    def test_job_payload_exposes_exact_waiting_child_projection(self):
        parent = PublicationTask(
            status="processing",
            current_step="pub_waiting",
            progress=95,
            params={
                "feature": FEATURE_NAME,
                "user_id": "user-1",
                "report_audio_workflow": {"pdf": {"filename": "waiting.pdf"}},
            },
            result={
                "waiting_child": {
                    "label": "publisher_video",
                    "child_id": "publisher-child-1",
                    "status": "processing",
                    "step": "v_synthesis",
                    "updated_at": "2026-08-10T01:05:00Z",
                }
            },
        )
        db.session.add(parent)
        db.session.commit()

        payload = report_audio_api._job_payload(parent)

        waiting = payload["orchestration"]["waiting"]
        self.assertEqual(waiting["label"], "publisher_video")
        self.assertEqual(waiting["child_id"], "publisher-child-1")
        self.assertEqual(waiting["status"], "processing")
        self.assertEqual(waiting["step"], "v_synthesis")

    def test_completed_job_exposes_seven_stages_and_completion_outputs(self):
        contract = {
            "schema_version": "report_video_completion_v1",
            "ready": True,
            "missing": [],
            "outputs": {
                "landscape": {"ready": True, "path": "/tmp/landscape.mp4", "reason": None},
                "vertical_scheme3": {"ready": True, "path": "/tmp/scheme3.mp4", "reason": None},
            },
        }
        parent = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            params={
                "feature": FEATURE_NAME,
                "user_id": "user-1",
                "report_audio_workflow": {
                    "pdf": {"filename": "completed.pdf"},
                    "video": {
                        "enabled": True,
                        "vertical_enabled": True,
                        "vertical_video_schemes": ["scheme3"],
                    },
                },
            },
            result={"completion_contract": contract},
            completed_at=datetime.utcnow(),
        )
        db.session.add(parent)
        db.session.commit()

        payload = report_audio_api._job_payload(parent)

        orchestration = payload["orchestration"]
        self.assertEqual(
            [stage["label"] for stage in orchestration["stages"]],
            ["输入", "文案", "音频", "视觉", "成片", "验收", "发布"],
        )
        self.assertTrue(orchestration["completion_contract"]["ready"])
        self.assertTrue(
            orchestration["completion_contract"]["outputs"]["vertical_scheme3"]["ready"]
        )

    def test_previewable_file_is_inline_only_for_preview_requests(self):
        media_path = Path(self.tmpdir.name) / "cover.jpg"
        media_path.write_bytes(b"jpeg-preview")

        with self.app.test_request_context("/?inline=1"):
            preview = report_audio_api._send_previewable_file(
                str(media_path),
                "image/jpeg",
                "cover.jpg",
            )
        with self.app.test_request_context("/"):
            download = report_audio_api._send_previewable_file(
                str(media_path),
                "image/jpeg",
                "cover.jpg",
            )

        try:
            self.assertTrue(preview.headers["Content-Disposition"].startswith("inline;"))
            self.assertTrue(preview.cache_control.no_store)
            self.assertTrue(preview.cache_control.no_cache)
            self.assertTrue(download.headers["Content-Disposition"].startswith("attachment;"))
        finally:
            preview.close()
            download.close()

    def test_bilibili_cover_is_promoted_and_accepted_by_upload_resolver(self):
        root = Path(self.tmpdir.name)
        bilibili_cover_path = root / "bilibili-cover.jpg"
        bilibili_cover_path.write_bytes(b"jpeg-bilibili-cover")
        parent = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            params={
                "feature": FEATURE_NAME,
                "user_id": "user-1",
                "report_audio_workflow": {"pdf": {"filename": "sample.pdf"}},
            },
            result={
                "video": {
                    "enabled": True,
                    "status": "completed",
                    "bilibili_cover_path": str(bilibili_cover_path),
                    "keyframes": {
                        "bilibili_cover": {
                            "path": str(bilibili_cover_path),
                            "label": "B站封皮关键帧",
                            "ratio": "1146:717",
                            "width": 1146,
                            "height": 717,
                        },
                    },
                },
            },
        )
        db.session.add(parent)
        db.session.commit()

        payload = report_audio_api._job_payload(parent)
        cover_path, cover_key = report_audio_api._report_bilibili_cover_path(
            parent,
            "vertical",
            "bilibili_cover",
        )

        expected_url = f"/api/report-audio-workflows/jobs/{parent.id}/download-bilibili-cover"
        self.assertEqual(payload["download_bilibili_cover_url"], expected_url)
        self.assertEqual(payload["video"]["bilibili_cover"]["download_url"], expected_url)
        self.assertEqual(payload["video"]["bilibili_cover"]["ratio"], "1146:717")
        self.assertEqual(cover_path, str(bilibili_cover_path))
        self.assertEqual(cover_key, "bilibili_cover")

    def test_bilibili_defaults_pair_video_with_variant_title_and_cover(self):
        root = Path(self.tmpdir.name)
        landscape_cover = root / "landscape.jpg"
        bilibili_cover = root / "bilibili.jpg"
        landscape_cover.write_bytes(b"landscape")
        bilibili_cover.write_bytes(b"bilibili")
        parent = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            params={"feature": FEATURE_NAME, "report_audio_workflow": {"pdf": {"filename": "sample.pdf"}}},
            result={
                "structured_result": {
                    "landscape_title": "横屏标题：稳健完整",
                    "vertical_title": "竖屏标题：核心机会",
                    "cover_title": "旧版兼容标题",
                },
                "video": {
                    "cover_path": str(landscape_cover),
                    "bilibili_cover_path": str(bilibili_cover),
                },
            },
            created_at=datetime(2026, 7, 11, 12, 0, 0),
        )
        db.session.add(parent)
        db.session.commit()

        self.assertEqual(
            report_audio_api._report_bilibili_default_title(parent, "landscape"),
            "机构研报 / 横屏标题：稳健完整 / 2026-07-11",
        )
        self.assertEqual(
            report_audio_api._report_bilibili_default_title(parent, "vertical"),
            "机构研报 | 竖屏标题：核心机会 | 2026-07-11",
        )
        self.assertEqual(report_audio_api._report_bilibili_cover_path(parent, "landscape"), (str(bilibili_cover), "bilibili_cover"))
        self.assertEqual(report_audio_api._report_bilibili_cover_path(parent, "vertical"), (str(bilibili_cover), "bilibili_cover"))

    def test_bilibili_upload_defaults_and_renderer_use_dual_ratio_safe_cover(self):
        frontend_dir = BACKEND_DIR.parent / "frontend"
        detail_script = (frontend_dir / "js" / "report-audio-workflow-detail.js").read_text(encoding="utf-8")
        workbench_script = (frontend_dir / "js" / "thumbnail-workbench.js").read_text(encoding="utf-8")
        cover_options_body = detail_script.split("\n    biliCoverOptions(job", 1)[1].split("\n    bilibiliCoverUrl(job", 1)[0]

        self.assertIn("add('bilibili_cover', 'B站双比例安全封皮'", cover_options_body)
        self.assertLess(
            cover_options_body.index("add('bilibili_cover', 'B站双比例安全封皮'"),
            cover_options_body.index("add('cover', '横屏封面'"),
        )
        self.assertIn("const margin = isBiliWxCover ? 150 : 80", workbench_script)
        self.assertIn("Math.round(h * 0.60)", workbench_script)

    def test_bilibili_publish_title_extracts_bank_and_report_date(self):
        parent = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            params={
                "feature": FEATURE_NAME,
                "report_audio_workflow": {"pdf": {"filename": "野村-源杰科技首次覆盖-260708.pdf"}},
            },
            result={
                "structured_result": {
                    "landscape_title": "野村:源杰科技估值已透支\n目标价隐含下行空间",
                    "landscape_subtitle": "盈利复合增速114%",
                    "vertical_title": "光芯片盈利暴增114%\n源杰估值为何仍透支",
                    "vertical_subtitle": "目标价隐含3.5%下行",
                },
            },
        )
        db.session.add(parent)
        db.session.commit()

        self.assertEqual(
            report_audio_api._report_bilibili_default_title(parent, "landscape"),
            "野村 / 源杰科技估值已透支 / 目标价隐含下行空间 / 盈利复合增速114% / 2026-07-08",
        )
        self.assertEqual(
            report_audio_api._report_bilibili_default_title(parent, "vertical"),
            "野村 | 光芯片盈利暴增114% | 源杰估值为何仍透支 | 目标价隐含3.5%下行 | 2026-07-08",
        )

    def test_completed_scheme2_job_can_backfill_bilibili_cover_without_video_rerender(self):
        root = Path(self.tmpdir.name)
        screenshot_path = root / "page.jpg"
        screenshot_path.write_bytes(b"jpeg-page")
        bilibili_cover_path = root / "bilibili-cover.jpg"
        bilibili_cover_path.write_bytes(b"jpeg-bilibili-cover")
        parent = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            params={
                "feature": FEATURE_NAME,
                "user_id": "user-1",
                "report_audio_workflow": {
                    "pdf": {"filename": "sample.pdf"},
                    "video": {"vertical_enabled": True, "vertical_video_scheme": "scheme2"},
                },
            },
            result={
                "structured_result": {
                    "cover_title": "方案二标题",
                    "cover_subtitle": "方案二副标题",
                },
                "pdf_first_page_image": {"path": str(screenshot_path), "bytes": 9},
                "video": {"status": "completed", "vertical_video_scheme": "scheme2"},
            },
        )
        db.session.add(parent)
        db.session.commit()

        with patch.object(
            report_audio_api.thumbnail_service,
            "generate_workbench_bilibili_cover",
            return_value=str(bilibili_cover_path),
        ) as generate_cover:
            cover = report_audio_api._generate_bilibili_cover_for_task(parent)

        self.assertEqual(cover["path"], str(bilibili_cover_path))
        self.assertFalse(generate_cover.call_args.kwargs["show_footer"])
        db.session.refresh(parent)
        self.assertEqual(parent.result["video"]["bilibili_cover_path"], str(bilibili_cover_path))

    def test_scheme2_vertical_background_text_can_be_regenerated(self):
        root = Path(self.tmpdir.name)
        screenshot_path = root / "page.jpg"
        old_background = root / "old-background.jpg"
        new_background = root / "new-background.jpg"
        screenshot_path.write_bytes(b"jpeg-page")
        old_background.write_bytes(b"jpeg-old")
        new_background.write_bytes(b"jpeg-new")
        parent = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            params={
                "feature": FEATURE_NAME,
                "user_id": "user-1",
                "report_audio_workflow": {
                    "pdf": {"filename": "摩根士丹利-稀土.pdf"},
                    "video": {"vertical_enabled": True, "vertical_video_scheme": "scheme2"},
                },
            },
            result={
                "pdf_first_page_image": {"path": str(screenshot_path), "bytes": 9},
                "video": {
                    "status": "keyframes_ready",
                    "vertical_video_scheme": "scheme2",
                    "vertical_background_path": str(old_background),
                    "vertical_background_paths": {"scheme2": str(old_background)},
                    "keyframes": {
                        "vertical_background": {
                            "path": str(old_background),
                            "label": "方案二竖屏背景关键帧",
                            "ratio": "9:16",
                        },
                    },
                },
            },
        )
        db.session.add(parent)
        db.session.commit()

        with patch.object(
            report_audio_api.thumbnail_service,
            "generate_workbench_vertical_thumbnail",
            return_value=str(new_background),
        ) as generate_background:
            background = report_audio_api._generate_vertical_background_for_task(
                parent,
                scheme="scheme2",
                title="用户修改后的标题",
                subtitle="用户修改后的副标题",
            )

        self.assertEqual(generate_background.call_args.kwargs["main_title"], "用户修改后的标题")
        self.assertEqual(background["keyframe_key"], "vertical_background")
        db.session.refresh(parent)
        video = parent.result["video"]
        self.assertEqual(video["vertical_background_path"], str(new_background))
        self.assertEqual(video["vertical_background_paths"]["scheme2"], str(new_background))
        self.assertEqual(video["vertical_background_title"], "用户修改后的标题")
        self.assertEqual(video["keyframes"]["vertical_background"]["subtitle"], "用户修改后的副标题")
        self.assertEqual(video["keyframes"]["vertical_background"]["width"], 1080)
        self.assertEqual(video["keyframes"]["vertical_background"]["height"], 1920)
        public = report_audio_api._public_keyframes(parent.id, video)
        self.assertEqual(public["vertical_background"]["title"], "用户修改后的标题")
        self.assertEqual(public["vertical_background"]["scheme"], "scheme2")

    def test_cover_paths_fall_back_to_keyframe_artifacts(self):
        root = Path(self.tmpdir.name)
        vertical_cover = root / "vertical-cover.jpg"
        podcast_cover = root / "podcast-cover.jpg"
        vertical_cover.write_bytes(b"vertical")
        podcast_cover.write_bytes(b"podcast")
        parent = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            params={"feature": FEATURE_NAME, "user_id": "user-1"},
            result={
                "video": {
                    "keyframes": {
                        "vertical_cover": {"path": str(vertical_cover)},
                        "podcast_cover": {"path": str(podcast_cover)},
                    }
                }
            },
        )
        db.session.add(parent)
        db.session.commit()
        db.session.refresh(parent, attribute_names=["result"])

        self.assertEqual(report_audio_api._vertical_cover_path_for_task(parent), str(vertical_cover))
        self.assertEqual(report_audio_api._podcast_cover_path_for_task(parent), str(podcast_cover))
        public = report_audio_api._public_video(parent.id, parent.result["video"])
        self.assertEqual(
            public["vertical_cover"]["download_url"],
            f"/api/report-audio-workflows/jobs/{parent.id}/download-vertical-cover",
        )
        self.assertEqual(
            public["podcast_cover"]["download_url"],
            f"/api/report-audio-workflows/jobs/{parent.id}/download-podcast-cover",
        )

    def test_cover_response_includes_cache_busting_version(self):
        payload = {"video": {"status": "keyframes_ready"}}
        updated_at = "2026-07-16T08:10:42.161972Z"

        merged = report_audio_api._merge_cover_response_payload(
            payload,
            "vertical",
            {
                "path": "/tmp/vertical.jpg",
                "bytes": 123,
                "title": "新标题",
                "subtitle": "新副标题",
                "download_url": "/download-vertical-cover",
                "updated_at": updated_at,
            },
        )

        self.assertEqual(merged["video"]["vertical_cover"]["updated_at"], updated_at)
        self.assertEqual(merged["video"]["vertical_cover_regenerated_at"], updated_at)

    def test_vertical_backfill_creates_vertical_only_publisher_task(self):
        root = Path(self.tmpdir.name)
        audio_path = root / "audio.wav"
        screenshot_path = root / "page.jpg"
        existing_vertical_cover = root / "vertical-cover.jpg"
        existing_podcast_cover = root / "podcast-cover.jpg"
        existing_bilibili_cover = root / "bilibili-cover.jpg"
        audio_path.write_bytes(b"RIFF----WAVE")
        screenshot_path.write_bytes(b"jpeg")
        existing_vertical_cover.write_bytes(b"vertical-cover")
        existing_podcast_cover.write_bytes(b"podcast-cover")
        existing_bilibili_cover.write_bytes(b"bilibili-cover")

        parent = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            params={
                "feature": FEATURE_NAME,
                "user_id": "user-1",
                "report_audio_workflow": {
                    "pdf": {"filename": "sample.pdf", "path": str(root / "sample.pdf")},
                    "video": {
                        "enabled": True,
                        "title": "大摩:十天内两度上调国巨",
                        "subtitle": "AI机柜电容产值飙升182%",
                        "sub_burn_enabled": True,
                    },
                },
            },
            result={
                "audio": {"wav_path": str(audio_path), "seconds": 12.0},
                "structured_result": {
                    "final_script_for_tts": "这是一段用于生成字幕的口播稿。",
                    "cover_title": "大摩:十天内两度上调国巨",
                    "cover_subtitle": "AI机柜电容产值飙升182%",
                },
                "pdf_first_page_image": {"path": str(screenshot_path), "bytes": 4},
                "video": {
                    "video_path": str(root / "landscape.mp4"),
                    "vertical_cover_path": str(existing_vertical_cover),
                    "podcast_cover_path": str(existing_podcast_cover),
                    "bilibili_cover_path": str(existing_bilibili_cover),
                },
            },
        )
        db.session.add(parent)
        db.session.commit()

        with patch.object(
            report_audio_api.run_publication_pipeline,
            "apply_async",
            return_value=SimpleNamespace(id="celery-vertical"),
        ) as apply_async:
            child = report_audio_api._create_vertical_video_backfill_task(parent, scheme="scheme2")

        apply_async.assert_called_once_with(args=[child.id], queue="render", retry=False)
        self.assertEqual(child.celery_task_id, "celery-vertical")
        self.assertEqual(child.params["feature"], "publisher")
        self.assertEqual(child.params["source_feature"], FEATURE_NAME)
        self.assertEqual(child.params["source_task_id"], parent.id)
        self.assertFalse(child.params["video"]["enabled"])
        self.assertTrue(child.params["vertical_video"]["enabled"])
        self.assertEqual(child.params["vertical_video"]["image_path"], str(screenshot_path))
        self.assertTrue(child.params["vertical_thumbnail"]["enabled"])
        self.assertFalse(child.params["vertical_cover"]["enabled"])
        self.assertFalse(child.params["podcast_cover"]["enabled"])
        self.assertFalse(child.params["bilibili_cover"]["enabled"])
        self.assertFalse(child.params["bilibili_cover"]["show_footer"])
        for asset_key in ("vertical_thumbnail", "vertical_cover", "podcast_cover", "bilibili_cover"):
            with self.subTest(asset_key=asset_key):
                self.assertTrue(child.params[asset_key]["main_title"].startswith("大摩："))
        self.assertFalse(child.params["bilibili"]["enabled"])
        self.assertNotIn("thumbnail", child.params)
        self.assertEqual(child.result["metadata"]["source"], report_audio_api.VERTICAL_BACKFILL_SOURCE)

    def test_keyframe_confirmation_creates_landscape_and_scheme2_vertical_task(self):
        root = Path(self.tmpdir.name)
        audio_path = root / "audio.wav"
        screenshot_path = root / "page.jpg"
        scheme1_background = root / "scheme1-background.jpg"
        audio_path.write_bytes(b"RIFF----WAVE")
        screenshot_path.write_bytes(b"jpeg")
        scheme1_background.write_bytes(b"scheme1")

        parent = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            params={
                "feature": FEATURE_NAME,
                "user_id": "user-1",
                "report_audio_workflow": {
                    "pdf": {"filename": "sample.pdf", "path": str(root / "sample.pdf")},
                    "video": {"enabled": True, "vertical_enabled": True, "vertical_video_scheme": "scheme1"},
                },
            },
            result={
                "audio": {"wav_path": str(audio_path), "seconds": 12.0},
                "structured_result": {"final_script_for_tts": "测试口播稿。", "cover_title": "测试标题"},
                "pdf_first_page_image": {"path": str(screenshot_path), "bytes": 4},
                "video": {
                    "render_mode": "keyframes",
                    "vertical_video_scheme": "scheme1",
                    "vertical_background_path": str(scheme1_background),
                },
            },
        )
        db.session.add(parent)
        db.session.commit()

        with patch.object(
            report_audio_api.run_publication_pipeline,
            "apply_async",
            return_value=SimpleNamespace(id="celery-final-videos"),
        ):
            child = report_audio_api._create_vertical_video_backfill_task(
                parent,
                scheme="scheme2",
                include_landscape=True,
            )

        self.assertTrue(child.params["video"]["enabled"])
        self.assertTrue(child.params["vertical_video"]["enabled"])
        self.assertEqual(child.params["vertical_video"]["scheme"], "scheme2")
        self.assertEqual(child.params["vertical_video"]["image_path"], str(screenshot_path))
        self.assertTrue(child.params["vertical_thumbnail"]["enabled"])
        self.assertTrue(child.result["metadata"]["include_landscape"])

        landscape_path = root / "landscape.mp4"
        vertical_path = root / "vertical.mp4"
        landscape_path.write_bytes(b"landscape")
        vertical_path.write_bytes(b"vertical")
        child.status = "completed"
        child.current_step = "done"
        child.progress = 100
        child.result = {
            **child.result,
            "video_path": str(landscape_path),
            "vertical_video_path": str(vertical_path),
        }
        db.session.commit()

        video = report_audio_api._video_result_with_vertical_backfill(parent)
        self.assertEqual(video["video_path"], str(landscape_path))
        self.assertEqual(video["vertical_video_path"], str(vertical_path))
        self.assertEqual(report_audio_api._landscape_video_path_for_task(parent), str(landscape_path))
        self.assertEqual(report_audio_api._report_bilibili_video_path(parent, "landscape"), str(landscape_path))
        self.assertEqual(report_audio_api._report_bilibili_video_path(parent, "vertical"), str(vertical_path))

    def test_keyframe_confirmation_can_generate_landscape_and_both_vertical_schemes(self):
        root = Path(self.tmpdir.name)
        audio_path = root / "audio.wav"
        screenshot_path = root / "page.jpg"
        audio_path.write_bytes(b"RIFF----WAVE")
        screenshot_path.write_bytes(b"jpeg")

        parent = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            params={
                "feature": FEATURE_NAME,
                "user_id": "user-1",
                "report_audio_workflow": {
                    "pdf": {"filename": "sample.pdf", "path": str(root / "sample.pdf")},
                    "video": {"enabled": True, "vertical_enabled": True},
                },
            },
            result={
                "audio": {"wav_path": str(audio_path), "seconds": 12.0},
                "structured_result": {"final_script_for_tts": "测试口播稿。", "cover_title": "测试标题"},
                "pdf_first_page_image": {"path": str(screenshot_path), "bytes": 4},
                "video": {"render_mode": "keyframes"},
            },
        )
        db.session.add(parent)
        db.session.commit()

        with patch.object(
            report_audio_api.run_publication_pipeline,
            "apply_async",
            return_value=SimpleNamespace(id="celery-all-videos"),
        ):
            child = report_audio_api._create_vertical_video_backfill_task(
                parent,
                schemes=["scheme1", "scheme2"],
                include_landscape=True,
            )

        self.assertTrue(child.params["video"]["enabled"])
        self.assertTrue(child.params["vertical_video"]["enabled"])
        self.assertEqual(child.params["vertical_video"]["scheme"], "scheme2")
        self.assertTrue(child.params["vertical_video_2"]["enabled"])
        self.assertEqual(child.params["vertical_video_2"]["scheme"], "scheme1")
        self.assertEqual(child.result["metadata"]["vertical_video_schemes"], ["scheme1", "scheme2"])

    def test_keyframe_confirmation_can_generate_landscape_only(self):
        root = Path(self.tmpdir.name)
        audio_path = root / "audio.wav"
        screenshot_path = root / "page.jpg"
        audio_path.write_bytes(b"RIFF----WAVE")
        screenshot_path.write_bytes(b"jpeg")

        parent = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            params={
                "feature": FEATURE_NAME,
                "user_id": "user-1",
                "report_audio_workflow": {
                    "pdf": {"filename": "sample.pdf", "path": str(root / "sample.pdf")},
                    "video": {"enabled": True},
                },
            },
            result={
                "audio": {"wav_path": str(audio_path), "seconds": 12.0},
                "structured_result": {"final_script_for_tts": "测试口播稿。", "cover_title": "测试标题"},
                "pdf_first_page_image": {"path": str(screenshot_path), "bytes": 4},
                "video": {"render_mode": "keyframes"},
            },
        )
        db.session.add(parent)
        db.session.commit()

        with patch.object(
            report_audio_api.run_publication_pipeline,
            "apply_async",
            return_value=SimpleNamespace(id="celery-landscape-only"),
        ):
            child = report_audio_api._create_vertical_video_backfill_task(
                parent,
                schemes=[],
                include_landscape=True,
            )

        self.assertTrue(child.params["video"]["enabled"])
        self.assertFalse(child.params["vertical_video"]["enabled"])
        self.assertFalse(child.params["vertical_video_2"]["enabled"])
        self.assertFalse(child.params["vertical_thumbnail"]["enabled"])
        self.assertFalse(child.params["vertical_cover"]["enabled"])
        self.assertEqual(child.result["metadata"]["vertical_video_schemes"], [])

    def test_combined_landscape_and_unready_scheme3_still_queues_landscape(self):
        root = Path(self.tmpdir.name)
        audio_path = root / "audio.wav"
        screenshot_path = root / "page.jpg"
        pdf_path = root / "sample.pdf"
        audio_path.write_bytes(b"RIFF----WAVE")
        screenshot_path.write_bytes(b"jpeg")
        pdf_path.write_bytes(b"%PDF-1.4")

        parent = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            user_id="user-1",
            params={
                "feature": FEATURE_NAME,
                "user_id": "user-1",
                "report_audio_workflow": {
                    "pdf": {"filename": "sample.pdf", "path": str(pdf_path)},
                    "video": {"enabled": True},
                },
            },
            result={
                "audio": {"wav_path": str(audio_path), "seconds": 12.0},
                "structured_result": {
                    "final_script_for_tts": "测试口播稿。",
                    "cover_title": "测试标题",
                },
                "pdf_first_page_image": {"path": str(screenshot_path), "bytes": 4},
                "video": {"render_mode": "keyframes"},
            },
        )
        db.session.add(parent)
        db.session.commit()

        with (
            self.app.test_request_context(
                json={
                    "vertical_video_schemes": ["scheme3"],
                    "include_landscape": True,
                    "force": True,
                },
            ),
            patch.object(
                report_audio_api,
                "_ready_linked_visual_workflow_id",
                return_value=None,
            ),
            patch.object(
                report_audio_api.run_publication_pipeline,
                "apply_async",
                return_value=SimpleNamespace(id="celery-landscape-fallback"),
            ),
        ):
            response = report_audio_api.generate_vertical_video.__wrapped__(
                SimpleNamespace(id="user-1"),
                parent.id,
            )

        payload = response.get_json()
        self.assertTrue(payload["success"], payload)
        child = PublicationTask.query.filter_by(source_task_id=parent.id, feature="publisher").one()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["partial_success"])
        self.assertEqual(payload["submitted_vertical_video_schemes"], [])
        self.assertEqual(payload["skipped_outputs"][0]["output"], "scheme3")
        self.assertTrue(child.params["video"]["enabled"])
        self.assertFalse(child.params["vertical_video"]["enabled"])

    def test_keyframe_confirmation_persists_full_chain_instead_of_dropping_scheme3(self):
        parent = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            feature=FEATURE_NAME,
            user_id="user-1",
            params={"feature": FEATURE_NAME, "user_id": "user-1", "report_audio_workflow": {}},
            result={},
        )
        db.session.add(parent)
        db.session.commit()
        coordinator = SimpleNamespace(id="finalization-1")
        summary = {
            "job_id": "finalization-1",
            "status": "processing",
            "step": "visual_queued",
            "progress": 20,
            "include_landscape": True,
            "vertical_video_schemes": ["scheme3"],
            "message": "白板任务已创建",
        }

        with (
            self.app.test_request_context(json={
                "include_landscape": True,
                "vertical_video_schemes": ["scheme3"],
                "visual_scheme_key": "stick_figure",
            }),
            patch.object(
                report_audio_api,
                "create_or_resume_finalization",
                return_value=coordinator,
            ) as create_intent,
            patch.object(report_audio_api, "finalization_summary", return_value=summary),
            patch.object(report_audio_api, "_linked_visual_video_payloads", return_value=[]),
            patch.object(report_audio_api, "_job_payload", return_value={"job_id": parent.id, "children": {}}),
        ):
            response, status_code = report_audio_api.confirm_keyframes.__wrapped__(
                SimpleNamespace(id="user-1"),
                parent.id,
            )

        payload = response.get_json()
        self.assertEqual(status_code, 202)
        self.assertTrue(payload["accepted"])
        self.assertEqual(payload["finalization"]["vertical_video_schemes"], ["scheme3"])
        create_intent.assert_called_once_with(
            parent.id,
            include_landscape=True,
            schemes=["scheme3"],
            visual_scheme_key="stick_figure",
            selection_source="keyframe-confirmation",
            force=True,
        )

    def test_main_publisher_vertical_output_is_reused_as_downloadable_video(self):
        root = Path(self.tmpdir.name)
        landscape_path = root / "landscape.mp4"
        old_vertical_path = root / "old-vertical.mp4"
        vertical_path = root / "vertical.mp4"
        screenshot_path = root / "page.jpg"
        landscape_path.write_bytes(b"landscape")
        old_vertical_path.write_bytes(b"old-vertical")
        vertical_path.write_bytes(b"vertical")
        screenshot_path.write_bytes(b"jpeg")

        parent = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            params={
                "feature": FEATURE_NAME,
                "user_id": "user-1",
                "report_audio_workflow": {
                    "pdf": {"filename": "sample.pdf", "path": str(root / "sample.pdf")},
                    "video": {"enabled": True, "vertical_enabled": True},
                },
            },
            result={
                "video": {
                    "enabled": True,
                    "status": "completed",
                    "video_path": str(landscape_path),
                    "vertical_enabled": True,
                    "vertical_video_path": str(old_vertical_path),
                },
            },
        )
        db.session.add(parent)
        db.session.commit()

        child = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            params={
                "feature": "publisher",
                "source_feature": FEATURE_NAME,
                "source_task_id": parent.id,
                "video": {"enabled": True},
                "vertical_video": {"enabled": True},
            },
            result={
                "source_task_id": parent.id,
                "video_path": str(landscape_path),
                "vertical_video_path": str(vertical_path),
                "pdf_first_page_image": {"path": str(screenshot_path), "bytes": 4},
            },
        )
        db.session.add(child)
        db.session.commit()

        video = report_audio_api._video_result_with_vertical_backfill(parent)
        self.assertEqual(video["vertical_video_path"], str(vertical_path))
        self.assertEqual(video["vertical_publisher_task_id"], child.id)
        self.assertEqual(report_audio_api._vertical_video_path_for_task(parent), str(vertical_path))

        public = report_audio_api._public_video(parent.id, video)
        self.assertEqual(
            public["vertical_video"]["download_url"],
            f"/api/report-audio-workflows/jobs/{parent.id}/download-vertical-video",
        )

        summaries = report_audio_api._vertical_child_summaries_for_parent_ids([parent.id])
        self.assertEqual(summaries[parent.id]["job_id"], child.id)
        self.assertEqual(
            summaries[parent.id]["vertical_video"]["download_url"],
            f"/api/report-audio-workflows/jobs/{parent.id}/download-vertical-video",
        )

    def test_regenerate_cover_updates_parent_result_and_keeps_history(self):
        root = Path(self.tmpdir.name)
        screenshot_path = root / "page.jpg"
        old_cover_path = root / "old-cover.jpg"
        new_cover_path = root / "new-cover.jpg"
        screenshot_path.write_bytes(b"jpeg")
        old_cover_path.write_bytes(b"old")

        parent = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            params={
                "feature": FEATURE_NAME,
                "user_id": "user-1",
                "report_audio_workflow": {
                    "pdf": {"filename": "sample.pdf", "path": str(root / "sample.pdf")},
                    "video": {
                        "enabled": True,
                        "tag_text": "研报播客",
                        "overlay_opacity": "0.6",
                        "cover_transform": {"x": 1, "y": 2, "scale": 1.1},
                    },
                },
            },
            result={
                "structured_result": {
                    "cover_title": "旧标题",
                    "cover_subtitle": "旧副标题",
                },
                "pdf_first_page_image": {"path": str(screenshot_path), "bytes": 4},
                "video": {
                    "video_path": str(root / "landscape.mp4"),
                    "cover_path": str(old_cover_path),
                },
            },
        )
        db.session.add(parent)
        db.session.commit()

        def fake_generate_thumbnail(**kwargs):
            self.assertEqual(kwargs["main_title"], "新标题")
            self.assertEqual(kwargs["sub_title"], "新副标题")
            self.assertEqual(kwargs["custom_bg_path"], str(screenshot_path))
            self.assertEqual(kwargs["mode"], "screenshot")
            self.assertEqual(kwargs["transform"], {"x": 1, "y": 2, "scale": 1.1})
            new_cover_path.write_bytes(b"new")
            return str(new_cover_path)

        with patch.object(report_audio_api.thumbnail_service, "generate_thumbnail", side_effect=fake_generate_thumbnail):
            cover = report_audio_api._regenerate_cover_for_task(parent, title="新标题", subtitle="新副标题")

        self.assertEqual(cover["path"], str(new_cover_path))
        self.assertEqual(cover["title"], "新标题")
        self.assertEqual(cover["subtitle"], "新副标题")

        db.session.refresh(parent)
        result = parent.result
        self.assertEqual(result["video"]["cover_path"], str(new_cover_path))
        self.assertEqual(result["video"]["cover_title"], "新标题")
        self.assertEqual(result["video"]["cover_subtitle"], "新副标题")
        self.assertEqual(result["structured_result"]["cover_title"], "新标题")
        self.assertEqual(result["structured_result"]["cover_subtitle"], "新副标题")
        self.assertEqual(result["cover_regenerations"][-1]["cover_path"], str(old_cover_path))
        self.assertEqual(result["cover_regenerations"][-1]["title"], "旧标题")
        self.assertEqual(parent.report_audio_cover_title, "新标题")
        self.assertEqual(parent.report_audio_cover_subtitle, "新副标题")
        self.assertEqual(parent.report_audio_cover_path, str(new_cover_path))

    def test_regenerate_vertical_cover_uses_manual_copy_without_rerendering_video(self):
        root = Path(self.tmpdir.name)
        screenshot_path = root / "page.jpg"
        vertical_cover_path = root / "vertical-cover.jpg"
        screenshot_path.write_bytes(b"jpeg")
        vertical_cover_path.write_bytes(b"vertical")
        parent = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            params={
                "feature": FEATURE_NAME,
                "user_id": "user-1",
                "report_audio_workflow": {
                    "pdf": {"filename": "野村-航天-260710.pdf"},
                    "video": {"vertical_video_scheme": "scheme2"},
                },
            },
            result={
                "structured_result": {"vertical_title": "旧标题"},
                "pdf_first_page_image": {"path": str(screenshot_path), "bytes": 4},
                "video": {"vertical_video_path": str(root / "vertical.mp4")},
            },
        )
        db.session.add(parent)
        db.session.commit()

        with patch.object(
            report_audio_api.thumbnail_service,
            "generate_workbench_vertical_cover",
            return_value=str(vertical_cover_path),
        ) as generate_cover:
            cover = report_audio_api._generate_vertical_cover_for_task(
                parent,
                title="火箭回收成本暴降",
                subtitle="每公斤成本直逼 SpaceX",
            )

        self.assertEqual(generate_cover.call_args.kwargs["main_title"], "野村：火箭回收成本暴降")
        self.assertEqual(generate_cover.call_args.kwargs["sub_title"], "每公斤成本直逼 SpaceX")
        self.assertEqual(cover["path"], str(vertical_cover_path))
        db.session.refresh(parent)
        self.assertEqual(parent.result["video"]["vertical_cover_path"], str(vertical_cover_path))
        self.assertEqual(parent.result["video"]["vertical_cover_title"], "野村：火箭回收成本暴降")
        self.assertEqual(parent.result["video"]["vertical_cover_subtitle"], "每公斤成本直逼 SpaceX")

    def test_normalize_cover_text_preserves_manual_line_breaks(self):
        text = report_audio_api._normalize_cover_text("  第一行  标题 \r\n 第二行\t标题  \n\n第三行", 80)

        self.assertEqual(text, "第一行 标题\n第二行 标题\n第三行")

    def test_manual_cover_title_normalizes_to_institution_colon_format(self):
        parent = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            params={
                "feature": FEATURE_NAME,
                "report_audio_workflow": {"pdf": {"filename": "野村证券-中国航天-260710.pdf"}},
            },
            result={"structured_result": {"title": "商业航天成本拐点"}},
        )
        db.session.add(parent)
        db.session.commit()

        title = report_audio_api._cover_title_with_institution(
            parent,
            "野村证券：中国火箭回收重复使用的原创路线\n决定商业航天生死的根本不是订单",
        )

        self.assertEqual(
            title,
            "野村：中国火箭回收重复使用的原创路线\n决定商业航天生死的根本不是订单",
        )

    def test_manual_cover_title_preserves_reviewed_full_institution_and_line_break(self):
        parent = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            params={
                "feature": FEATURE_NAME,
                "report_audio_workflow": {
                    "pdf": {"filename": "Morgan Stanley-China Steel Weekly Update.pdf"},
                },
            },
            result={"structured_result": {"title": "中国钢铁与铁矿石周度更新"}},
        )
        db.session.add(parent)
        db.session.commit()

        title = report_audio_api._cover_title_with_institution(
            parent,
            "摩根士丹利：中国钢铁与铁矿石\n周度更新",
        )

        self.assertEqual(title, "摩根士丹利：中国钢铁与铁矿石\n周度更新")

    def test_jefferies_cover_title_removes_legacy_generic_prefix(self):
        parent = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            params={
                "feature": FEATURE_NAME,
                "report_audio_workflow": {
                    "pdf": {"filename": "杰富瑞-中国科技行业-PCB更新报告.pdf"},
                },
            },
            result={"structured_result": {"title": "周期逻辑失效"}},
        )
        db.session.add(parent)
        db.session.commit()

        title = report_audio_api._cover_title_with_institution(
            parent,
            "机构：杰富瑞：周期逻辑失效，买错PCB暴跌461%",
        )

        self.assertEqual(title, "杰富瑞：周期逻辑失效，买错PCB暴跌461%")

    def test_bofa_cover_title_does_not_add_generic_institution_prefix(self):
        parent = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            params={
                "feature": FEATURE_NAME,
                "report_audio_workflow": {
                    "pdf": {"filename": "美银-全球存储技术周报.pdf"},
                },
            },
            result={"structured_result": {"title": "存储行业核心观点"}},
        )
        db.session.add(parent)
        db.session.commit()

        title = report_audio_api._cover_title_with_institution(
            parent,
            "美银：存储降速只是市场的虚假幻觉\n服务器急单才是定价内核",
        )

        self.assertEqual(
            title,
            "美银：存储降速只是市场的虚假幻觉\n服务器急单才是定价内核",
        )
        self.assertNotIn("机构：", title)

    def test_bernstein_cover_title_replaces_legacy_shengbo_prefix(self):
        parent = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            params={
                "feature": FEATURE_NAME,
                "report_audio_workflow": {
                    "pdf": {"filename": "伯恩斯坦-全球半导体及设备-260713.pdf"},
                },
            },
            result={"structured_result": {"cover_title": "盛博:存储见顶设备必跌？研报打破常识"}},
        )
        db.session.add(parent)
        db.session.commit()

        title = report_audio_api._cover_title_with_institution(
            parent,
            "机构研报：盛博:存储见顶设备必跌？研报打破常识",
        )

        self.assertEqual(title, "伯恩斯坦：存储见顶设备必跌？研报打破常识")

    def test_job_summary_payload_includes_cover_subtitle_from_projection(self):
        row = SimpleNamespace(
            id="job-1",
            request_pdf_filename="sample.pdf",
            result_pdf_filename=None,
            request_pdf_bytes="123",
            result_pdf_bytes=None,
            request_pdf_topic_title="PDF 标题",
            result_pdf_topic_title=None,
            cover_title="封面主标题",
            cover_subtitle="第一行 副标题\n第二行 副标题",
            structured_title="结构化标题",
            audio_wav_path=None,
            audio_seconds=None,
            audio_bytes=None,
            video_enabled=True,
            video_status="completed",
            video_publisher_task_id="publisher-1",
            video_message=None,
            video_path=None,
            cover_path="/tmp/cover.jpg",
            video_title=None,
            metadata_title=None,
            status="completed",
            current_step="done",
            progress=100,
            created_at=None,
            started_at=None,
            completed_at=None,
            result_completed_at=None,
            error_message=None,
            result_error=None,
            output_path=None,
        )

        payload = report_audio_api._job_summary_payload(row)

        self.assertEqual(payload["cover_subtitle"], "第一行 副标题\n第二行 副标题")
        self.assertEqual(payload["structured_result"]["cover_subtitle"], "第一行 副标题\n第二行 副标题")
        self.assertEqual(payload["video"]["cover_subtitle"], "第一行 副标题\n第二行 副标题")
        self.assertNotIn("children", payload)

    def test_job_summary_payload_prefers_pdf_filename_over_generic_topic_title(self):
        row = SimpleNamespace(
            id="job-1",
            request_pdf_filename="野村-亚洲洞察：中国AI行业如何获得融资？-260624.pdf",
            result_pdf_filename=None,
            request_pdf_bytes="123",
            result_pdf_bytes=None,
            request_pdf_topic_title="#外资研报",
            result_pdf_topic_title=None,
            cover_title=None,
            cover_subtitle=None,
            structured_title=None,
            audio_wav_path=None,
            audio_seconds=None,
            audio_bytes=None,
            video_enabled=None,
            video_status=None,
            video_publisher_task_id=None,
            video_message=None,
            video_path=None,
            cover_path=None,
            video_title=None,
            metadata_title=None,
            status="pending",
            current_step="queued",
            progress=0,
            created_at=None,
            started_at=None,
            completed_at=None,
            result_completed_at=None,
            error_message=None,
            result_error=None,
            output_path=None,
        )

        payload = report_audio_api._job_summary_payload(row)

        self.assertEqual(payload["title"], "野村-亚洲洞察：中国AI行业如何获得融资？-260624.pdf")
        self.assertEqual(payload["pdf"]["topic_title"], "#外资研报")

    def test_job_summary_payload_exposes_download_for_main_publisher_vertical_video(self):
        row = SimpleNamespace(
            id="job-1",
            request_pdf_filename="sample.pdf",
            result_pdf_filename=None,
            request_pdf_bytes="123",
            result_pdf_bytes=None,
            request_pdf_topic_title="PDF 标题",
            result_pdf_topic_title=None,
            cover_title="封面主标题",
            cover_subtitle=None,
            structured_title=None,
            audio_wav_path=None,
            audio_seconds=None,
            audio_bytes=None,
            video_enabled=True,
            video_status="completed",
            video_publisher_task_id="publisher-1",
            video_message=None,
            video_path=None,
            cover_path=None,
            video_title=None,
            metadata_title=None,
            status="completed",
            current_step="done",
            progress=100,
            created_at=None,
            started_at=None,
            completed_at=None,
            result_completed_at=None,
            error_message=None,
            result_error=None,
            output_path=None,
        )

        payload = report_audio_api._job_summary_payload(
            row,
            {
                "publisher_vertical_video": {
                    "job_id": "publisher-1",
                    "status": "completed",
                    "vertical_video": {
                        "bytes": 8,
                        "download_url": "/api/report-audio-workflows/jobs/job-1/download-vertical-video",
                    },
                }
            },
        )

        self.assertEqual(
            payload["download_vertical_video_url"],
            "/api/report-audio-workflows/jobs/job-1/download-vertical-video",
        )
        self.assertEqual(payload["video"]["vertical_video"]["bytes"], 8)
        self.assertTrue(payload["video"]["vertical_enabled"])


if __name__ == "__main__":
    unittest.main()
