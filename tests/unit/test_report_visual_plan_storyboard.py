import json
import re
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from PIL import Image
from flask import Flask

BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services import report_visual_plan_service as service
from services.gemini_browser import (
    GeminiBrowserAutomationError,
    GeminiBrowserHumanInterventionError,
    GeminiBrowserOptions,
    GeminiBrowserWorker,
    _write_generation_checkpoint,
    _strip_gemini_page_suffix,
    _is_image_quota_text,
)
from services.report_stick_figure_assets import (
    IMAGE_STYLE_SUFFIX,
    has_browser_overlay_artifact,
    scene_image_path,
    scene_image_prompt,
    scene_image_prompt_hash,
)
from services.report_visual_scheme import visual_scheme, visual_scheme_payloads
from services.report_visual_plan_integrity import storyboard_integrity_from_rows
from api.report_stick_figure_video_workflow import (
    _completed_image_asset_can_adopt_prompt_hash,
    _preferred_storyboard,
    _workflow_payload,
    _workflow_source_payload,
)
from api import report_visual_plan as report_visual_plan_api
from tasks import report_visual_plan as report_visual_plan_task
from tasks.report_visual_plan import (
    _adopt_storyboard_embedded_images,
    _checkpoint_storyboard_text,
    _material_source_ready,
    _preserve_completed_video_state,
    _storyboard_gemini_worker,
    _visual_retry_delay_seconds,
    _visual_retry_limit,
)
from tasks.report_stick_figure_assets import (
    PARENT_IMAGE_COMPLETION_STEPS,
    _is_transient_image_error,
    _is_image_quota_error,
)


class ReportVisualPlanStoryboardTest(unittest.TestCase):
    def test_profile_contention_gets_longer_recovery_budget_than_transient_browser_errors(self):
        self.assertEqual(
            12,
            _visual_retry_limit("Gemini 浏览器 profile 正在被其他 Chrome 占用"),
        )
        self.assertEqual(3, _visual_retry_limit("页面自动化超时"))
        error = "Gemini 浏览器 profile 正在被其他 Chrome 占用"
        self.assertEqual(30, _visual_retry_delay_seconds(error, 1))
        self.assertEqual(120, _visual_retry_delay_seconds(error, 8))

    def test_durable_storyboard_integrity_requires_full_contiguous_content(self):
        output = SimpleNamespace(
            task_id="storyboard-1",
            source_task_id="source-1",
            schema_version="v1",
            source_script_chars=8,
            covered_script_chars=8,
            segment_count=2,
            visual_scheme_key="stick_figure",
            visual_scheme_name="白板火柴人",
            gemini_output_path="raw.json",
            normalized_output_path="normalized.json",
            normalizer_model="model",
            evidence_status=None,
        )
        rows = [
            SimpleNamespace(sequence=0, script_text="标题", visual_prompt="标题画面"),
            SimpleNamespace(sequence=1, script_text="正文", visual_prompt="正文画面"),
        ]

        self.assertEqual(2, storyboard_integrity_from_rows(output, rows)["segment_count"])
        self.assertIsNone(storyboard_integrity_from_rows(output, rows[1:]))
        self.assertIsNone(storyboard_integrity_from_rows(
            output,
            [rows[0], SimpleNamespace(sequence=2, script_text="正文", visual_prompt="正文画面")],
        ))
        self.assertIsNone(storyboard_integrity_from_rows(
            output,
            [rows[0], SimpleNamespace(sequence=1, script_text="正文", visual_prompt="")],
        ))

    def test_ambiguous_storyboard_commit_reconciles_before_failure_projection(self):
        task_path = BACKEND_DIR / "tasks" / "report_visual_plan.py"
        text = task_path.read_text(encoding="utf-8")
        process = text.split("def _process_job(", 1)[1].split("\ndef _retry_normalize_job", 1)[0]
        retry = text.split("def _retry_normalize_job(", 1)[1].split("\ndef _checkpoint_storyboard_text", 1)[0]
        reconcile = text.split("def _reconcile_durable_storyboard_success(", 1)[1].split(
            "\ndef _dispatch_auto_scene_images", 1
        )[0]

        self.assertIn("if not _reconcile_durable_storyboard_success(task_id, persist_exc)", process)
        self.assertIn("if not _reconcile_durable_storyboard_success(task_id, persist_exc)", retry)
        self.assertLess(reconcile.index("release_db_session_quietly()"), reconcile.index("durable_storyboard_integrity(task_id)"))
        self.assertIn('PublicationTask.status: "completed"', reconcile)
        self.assertIn('PublicationTask.current_step: "storyboard_done"', reconcile)
    def test_image_completion_cannot_regress_audio_or_video_parent_stage(self):
        for step in (
            "material_queued",
            "storyboard_done",
            "images_queued",
            "images_done",
            "images_failed",
        ):
            self.assertIn(step, PARENT_IMAGE_COMPLETION_STEPS)
        for step in (
            "whole_audio_alignment_queued",
            "transcribe_whole_audio",
            "whole_audio_aligned",
            "video_queued",
            "render_video",
            "video_done",
        ):
            self.assertNotIn(step, PARENT_IMAGE_COMPLETION_STEPS)

    def test_terminal_image_failures_close_parent_instead_of_leaving_82_percent_stuck(self):
        text = (
            BACKEND_DIR / "tasks" / "report_stick_figure_assets.py"
        ).read_text(encoding="utf-8")
        refresh = text.split(
            "def _refresh_parent_image_state(", 1
        )[1].split("\ndef _normalize_png", 1)[0]
        mark_failed = text.split("def _mark_failed(", 1)[1].split(
            "\ndef _refresh_parent_image_for_task", 1
        )[0]
        worker = text.split("def generate_scene_image(", 1)[1].split(
            "\ndef _is_transient_image_error", 1
        )[0]

        self.assertIn('("completed", "failed", "canceled", "blocked")', refresh)
        self.assertIn('PublicationTask.current_step: "images_failed"', refresh)
        self.assertIn('PublicationTask.waiting_child_status: "failed"', refresh)
        self.assertIn("续跑失败图片", refresh)
        self.assertNotIn("_refresh_parent_image_state", mark_failed)
        self.assertIn("_refresh_parent_image_for_task(str(task_id))", worker)
        self.assertIn("raise self.retry(", worker)

    def test_scene_image_retry_only_accepts_transient_browser_failures(self):
        self.assertTrue(_is_transient_image_error("等待 Gemini 生成图片超时"))
        self.assertTrue(_is_transient_image_error("net::ERR_CONNECTION_RESET"))
        self.assertTrue(_is_transient_image_error("未找到 Gemini 工具/生成图片入口"))
        self.assertTrue(_is_transient_image_error("Gemini 图片包含浏览器浮层"))
        self.assertFalse(_is_transient_image_error("分镜提示词契约已变化"))
        self.assertFalse(_is_transient_image_error("视觉方案版本已变化"))
        self.assertFalse(_is_transient_image_error(
            "Gemini 图片生成额度已耗尽，请等待额度重置后再继续"
        ))

    def test_gemini_image_quota_message_is_detected_and_not_retried(self):
        english = (
            "I can create more images as soon as your limit resets. "
            "Check your usage in Settings."
        )
        self.assertTrue(_is_image_quota_text(english))
        self.assertTrue(_is_image_quota_text("图片生成已达上限，请稍后再试"))
        self.assertTrue(_is_image_quota_error(
            "Gemini 图片生成额度已耗尽，请等待额度重置"
        ))
        self.assertFalse(_is_image_quota_text("Create exactly one image"))

    def test_browser_overlay_detector_rejects_dark_top_right_popover(self):
        with tempfile.TemporaryDirectory() as tempdir:
            contaminated = Path(tempdir) / "contaminated.png"
            clean = Path(tempdir) / "clean.png"
            image = Image.new("RGB", (708, 396), "white")
            for x in range(500, 708):
                for y in range(0, 135):
                    image.putpixel((x, y), (32, 32, 32))
            image.save(contaminated)
            Image.new("RGB", (708, 396), "white").save(clean)

            self.assertTrue(has_browser_overlay_artifact(str(contaminated)))
            self.assertFalse(has_browser_overlay_artifact(str(clean)))

    def test_auto_image_replay_does_not_downgrade_completed_video_workflow(self):
        with tempfile.TemporaryDirectory() as tempdir:
            video_path = Path(tempdir) / "completed.mp4"
            video_path.write_bytes(b"video")
            parent = SimpleNamespace(task_video_path=str(video_path))

            self.assertTrue(_preserve_completed_video_state(
                parent,
                all_completed=True,
                queued=[],
                errors=[],
            ))
            self.assertFalse(_preserve_completed_video_state(
                parent,
                all_completed=True,
                queued=["new-image-task"],
                errors=[],
            ))

    def test_visual_workflow_source_contract_distinguishes_report_audio(self):
        parent = SimpleNamespace(
            source_feature="report_pdf_audio_workflow",
            source_task_id="audio-job",
        )
        audio_source = SimpleNamespace(
            id="audio-job",
            feature="report_pdf_audio_workflow",
        )

        payload = _workflow_source_payload(parent, audio_source)

        self.assertEqual("audio-job", payload["material_job_id"])
        self.assertEqual("audio-job", payload["source_job_id"])
        self.assertEqual("audio-job", payload["report_audio_job_id"])
        self.assertEqual("report_pdf_audio_workflow", payload["source_feature"])

    def test_visual_detail_loads_source_from_its_declared_feature(self):
        frontend_text = (
            BACKEND_DIR.parents[0] / "frontend" / "js" / "report-stick-figure-video-workflow-detail.js"
        ).read_text(encoding="utf-8")

        self.assertIn("workflow.data?.source_feature", frontend_text)
        self.assertIn("workflow.data?.source_job_id", frontend_text)
        self.assertIn("/api/report-audio-workflows/jobs/${encodeURIComponent(this.materialJobId)}", frontend_text)
        self.assertIn("normalizeMaterialSource(materialResult.data)", frontend_text)
        self.assertIn("workflow_job_id:this.workflowJobId", frontend_text)

    def test_visual_detail_exposes_profile_auto_repair_reason_while_waiting(self):
        frontend_text = (
            BACKEND_DIR.parents[0] / "frontend" / "js" / "report-stick-figure-video-workflow-detail.js"
        ).read_text(encoding="utf-8")
        task_text = (BACKEND_DIR / "tasks" / "report_visual_plan.py").read_text(encoding="utf-8")

        self.assertIn("自动修复中", frontend_text)
        self.assertIn("board.error", frontend_text)
        self.assertIn("retry_limit", task_text)

    def test_processing_report_audio_source_is_ready_after_script_projection(self):
        source = SimpleNamespace(
            feature="report_pdf_audio_workflow",
            status="processing",
            final_script_chars=1551,
        )

        self.assertTrue(_material_source_ready(source))

    def test_failed_report_audio_source_keeps_its_projected_script_for_visual_recovery(self):
        """A final-video failure must not make an intact script ineligible for a storyboard."""
        source = SimpleNamespace(
            feature="report_pdf_audio_workflow",
            status="failed",
            final_script_chars=1551,
        )

        self.assertTrue(_material_source_ready(source))

    def test_processing_report_audio_source_requires_projected_script(self):
        source = SimpleNamespace(
            feature="report_pdf_audio_workflow",
            status="processing",
            final_script_chars=0,
        )

        self.assertFalse(_material_source_ready(source))

    def test_gemini_login_loss_sends_human_intervention_email(self):
        worker = GeminiBrowserWorker.__new__(GeminiBrowserWorker)
        worker.options = GeminiBrowserOptions(user_data_dir="/tmp/gemini-profile")
        worker._current_job_id = "gemini-job"
        page = SimpleNamespace(url="https://gemini.google.com/app")
        with patch.object(worker, "_first_visible", side_effect=[None, object()]), patch.object(
            worker,
            "_save_failure_screenshot",
            return_value="/tmp/gemini-login.png",
        ), patch(
            "services.gemini_browser.workflow_notification_service.send_browser_human_intervention"
        ) as notify:
            notify.return_value = {"sent": True, "event_type": "login"}
            with self.assertRaisesRegex(GeminiBrowserAutomationError, "登录态失效") as raised:
                worker._raise_if_login_or_captcha(page)

        notify.assert_called_once()
        self.assertEqual("login", notify.call_args.kwargs["reason"])
        self.assertEqual("gemini-job", notify.call_args.kwargs["job_id"])
        self.assertEqual("login", raised.exception.reason)
        self.assertEqual(
            {"sent": True, "event_type": "login"},
            raised.exception.notification,
        )
        self.assertEqual("/tmp/gemini-login.png", raised.exception.context["screenshot_path"])

    def test_login_intervention_blocks_storyboard_and_parent_for_human_recovery(self):
        child = SimpleNamespace(
            id="storyboard-job",
            feature="report_visual_plan",
            status="processing",
            current_step="gemini_storyboard",
            progress=12,
            result={"schema_version": "storyboard-v1"},
            parent_task_id="workflow-job",
            parent_feature="report_stick_figure_video_workflow",
            source_task_id="material-job",
            error_message=None,
            completed_at=None,
            updated_at=None,
        )
        parent = SimpleNamespace(
            id="workflow-job",
            status="processing",
            current_step="storyboard_queued",
            progress=35,
            waiting_child_id="storyboard-job",
            waiting_child_label="标题与正文分镜",
            waiting_child_status="processing",
            waiting_child_step="gemini_storyboard",
            waiting_child_updated_at=None,
            error_message=None,
            completed_at=None,
            updated_at=None,
        )
        exc = GeminiBrowserHumanInterventionError(
            "Gemini 登录态失效，需要先在浏览器 profile 中登录",
            reason="login",
            notification={"sent": True, "to": "owner@example.test"},
            context={
                "profile": "/runtime/browser_profiles/worker_2",
                "page_url": "https://gemini.google.com/app",
                "screenshot_path": "/tmp/gemini-login.png",
                "worker_id": "worker_2",
            },
        )
        handler = getattr(report_visual_plan_task, "_block_for_human_intervention", None)
        self.assertTrue(callable(handler), "missing human-intervention state projector")

        with patch.object(
            report_visual_plan_task.db.session,
            "get",
            side_effect=[child, parent],
        ), patch.object(report_visual_plan_task.db.session, "rollback"), patch.object(
            report_visual_plan_task.db.session,
            "commit",
        ), patch.object(report_visual_plan_task, "release_db_session_quietly"):
            result = handler("storyboard-job", exc)

        self.assertFalse(result)
        self.assertEqual("blocked_by_login", child.status)
        self.assertEqual("blocked_by_login", child.current_step)
        self.assertIsNone(child.completed_at)
        intervention = child.result["human_intervention"]
        self.assertEqual("login", intervention["reason"])
        self.assertEqual("/runtime/browser_profiles/worker_2", intervention["profile"])
        self.assertEqual({"sent": True, "to": "owner@example.test"}, intervention["notification"])
        self.assertEqual("blocked_by_login", parent.status)
        self.assertEqual("storyboard_blocked_by_login", parent.current_step)
        self.assertEqual("blocked_by_login", parent.waiting_child_status)
        self.assertIsNone(parent.completed_at)

    def test_workflow_payload_reports_login_block_instead_of_running(self):
        parent = SimpleNamespace(
            id="workflow-job",
            report_visual_scheme_key="stick_figure",
            source_task_id="material-job",
            source_feature="report_pdf_audio_workflow",
            task_title="测试研报",
            report_audio_pdf_attachment_id=None,
            report_audio_pdf_filename="report.pdf",
            report_audio_pdf_bytes=100,
            report_audio_pdf_topic_title="测试研报",
            task_video_path=None,
            current_step="storyboard_blocked_by_login",
            progress=35,
            waiting_child_label="标题与正文分镜",
            waiting_child_status="blocked_by_login",
            waiting_child_step="blocked_by_login",
            error_message="等待人工登录 Gemini",
            created_at=None,
            updated_at=None,
        )
        material = SimpleNamespace(
            id="material-job",
            feature="report_pdf_audio_workflow",
            status="completed",
            current_step="completed",
            progress=100,
            task_title="测试研报",
            report_audio_pdf_attachment_id=None,
            report_audio_pdf_filename="report.pdf",
            report_audio_pdf_bytes=100,
            report_audio_pdf_topic_title="测试研报",
            final_script_chars=1600,
        )
        storyboard = SimpleNamespace(
            id="storyboard-job",
            status="blocked_by_login",
            current_step="blocked_by_login",
            progress=12,
            error_message="等待人工登录 Gemini",
        )

        payload = _workflow_payload(parent, material, storyboard, segment_count=1)

        self.assertEqual("blocked_by_login", payload["status"])
        self.assertEqual("分镜等待人工登录", payload["stage"])

    def test_login_recovery_email_context_links_parent_workflow(self):
        builder = getattr(report_visual_plan_task, "_human_intervention_details", None)
        self.assertTrue(callable(builder), "missing workflow recovery link builder")
        task = SimpleNamespace(
            parent_task_id="workflow-job",
            parent_feature="report_stick_figure_video_workflow",
        )
        with patch.dict(
            "os.environ",
            {"REPORT_WORKFLOW_FRONTEND_BASE_URL": "http://localhost:5001/"},
            clear=False,
        ):
            details = builder(task)

        self.assertEqual(
            "http://localhost:5001/report-stick-figure-video-workflow-detail.html?workflow_job_id=workflow-job",
            details["工作流详情"],
        )

    def test_resume_after_login_requeues_same_storyboard_and_parent(self):
        task = SimpleNamespace(
            id="storyboard-job",
            feature="report_visual_plan",
            status="blocked_by_login",
            current_step="blocked_by_login",
            progress=12,
            error_message="等待人工登录 Gemini",
            completed_at=None,
            updated_at=None,
            celery_task_id="old-delivery",
            parent_task_id="workflow-job",
            parent_feature="report_stick_figure_video_workflow",
            result={"human_intervention": {"reason": "login"}},
        )
        parent = SimpleNamespace(
            id="workflow-job",
            status="blocked_by_login",
            current_step="storyboard_blocked_by_login",
            progress=35,
            waiting_child_id="storyboard-job",
            waiting_child_label="标题与正文分镜",
            waiting_child_status="blocked_by_login",
            waiting_child_step="blocked_by_login",
            waiting_child_updated_at=None,
            error_message="等待人工登录 Gemini",
            completed_at=None,
            updated_at=None,
        )
        resume = getattr(report_visual_plan_api, "_resume_after_login", None)
        self.assertTrue(callable(resume), "missing same-task login recovery")
        async_result = SimpleNamespace(id="new-delivery")
        submit = {
            "async_result": async_result,
            "deferred": False,
            "countdown": 0,
        }

        with patch.object(
            report_visual_plan_api.db.session,
            "get",
            return_value=parent,
        ), patch.object(report_visual_plan_api.db.session, "commit"), patch.object(
            report_visual_plan_api,
            "uuid4",
            return_value="new-delivery",
        ), patch.object(
            report_visual_plan_api,
            "release_db_session_quietly",
        ), patch.object(
            report_visual_plan_api,
            "maybe_guarded_apply_async",
            return_value=submit,
        ) as enqueue, patch.object(
            report_visual_plan_api,
            "_clear_terminal_storyboard_cache",
        ):
            result = resume(task)

        self.assertEqual("storyboard-job", result["job_id"])
        self.assertEqual("queued", result["status"])
        self.assertEqual("pending", task.status)
        self.assertEqual("login_recovery_queued", task.current_step)
        self.assertEqual("new-delivery", task.celery_task_id)
        self.assertEqual("processing", parent.status)
        self.assertEqual("storyboard_queued", parent.current_step)
        self.assertEqual("pending", parent.waiting_child_status)
        self.assertIsNone(parent.error_message)
        self.assertEqual(["storyboard-job"], enqueue.call_args.kwargs["args"])

    def test_resume_after_login_rejects_non_login_blocked_task(self):
        resume = getattr(report_visual_plan_api, "_resume_after_login", None)
        self.assertTrue(callable(resume), "missing same-task login recovery")
        task = SimpleNamespace(id="storyboard-job", status="failed")
        with patch.object(report_visual_plan_api, "maybe_guarded_apply_async") as enqueue:
            with self.assertRaisesRegex(ValueError, "仅等待人工登录"):
                resume(task)
        enqueue.assert_not_called()

    def test_resume_after_login_publish_failure_reloads_and_restores_block(self):
        task = SimpleNamespace(
            id="storyboard-job",
            status="blocked_by_login",
            current_step="blocked_by_login",
            result={"human_intervention": {"reason": "login"}},
            parent_task_id="workflow-job",
            parent_feature="report_stick_figure_video_workflow",
            error_message=None,
            completed_at=None,
            updated_at=None,
            celery_task_id="old-delivery",
        )
        parent = SimpleNamespace(
            id="workflow-job",
            status="blocked_by_login",
            current_step="storyboard_blocked_by_login",
            progress=35,
            waiting_child_id="storyboard-job",
            waiting_child_label="标题与正文分镜",
            waiting_child_status="blocked_by_login",
            waiting_child_step="blocked_by_login",
            waiting_child_updated_at=None,
            error_message=None,
            completed_at=None,
            updated_at=None,
        )
        reloaded_task = SimpleNamespace(**vars(task))
        reloaded_parent = SimpleNamespace(**vars(parent))
        release = getattr(report_visual_plan_api, "release_db_session_quietly", None)
        self.assertTrue(callable(release), "resume must release the DB session before publish")

        with patch.object(
            report_visual_plan_api.db.session,
            "get",
            side_effect=[parent, reloaded_task, reloaded_parent],
        ) as get, patch.object(report_visual_plan_api.db.session, "commit"), patch.object(
            report_visual_plan_api.db.session,
            "rollback",
        ), patch.object(
            report_visual_plan_api,
            "release_db_session_quietly",
        ) as release_session, patch.object(
            report_visual_plan_api,
            "maybe_guarded_apply_async",
            side_effect=RuntimeError("broker unavailable"),
        ):
            with self.assertRaisesRegex(RuntimeError, "恢复任务入队失败"):
                report_visual_plan_api._resume_after_login(task)

        release_session.assert_called_once()
        self.assertEqual(3, get.call_count)
        self.assertEqual("blocked_by_login", reloaded_task.status)
        self.assertEqual("blocked_by_login", reloaded_parent.status)
        self.assertEqual("resume_enqueue_failed", reloaded_parent.waiting_child_step)

    def test_resume_after_login_route_is_registered(self):
        app = Flask(__name__)
        app.register_blueprint(report_visual_plan_api.report_visual_plan_bp)

        rules = {rule.rule for rule in app.url_map.iter_rules()}

        self.assertIn(
            "/api/report-visual-plans/jobs/<job_id>/resume-after-login",
            rules,
        )

    def test_blocked_storyboard_payload_exposes_human_intervention(self):
        intervention = {
            "reason": "login",
            "profile": "/runtime/browser_profiles/worker_2",
            "notification": {"sent": True, "to": "owner@example.test"},
            "resolution_steps": ["登录 Gemini", "恢复任务"],
        }
        task = SimpleNamespace(
            id="storyboard-job",
            status="blocked_by_login",
            current_step="blocked_by_login",
            progress=12,
            task_title="测试分镜",
            source_task_id="material-job",
            report_visual_scheme_key="stick_figure",
            created_at=None,
            completed_at=None,
            error_message="等待人工登录 Gemini",
            result={"human_intervention": intervention},
        )

        payload = report_visual_plan_api._storyboard_job_payload(
            task,
            None,
            include_segments=False,
        )

        self.assertEqual("blocked_by_login", payload["status"])
        self.assertEqual(intervention, payload["human_intervention"])

    def test_visual_detail_renders_and_resumes_login_block(self):
        frontend_text = (
            BACKEND_DIR.parents[0]
            / "frontend"
            / "js"
            / "report-stick-figure-video-workflow-detail.js"
        ).read_text(encoding="utf-8")

        self.assertIn("data-resume-storyboard-login", frontend_text)
        self.assertIn("resume-after-login", frontend_text)
        self.assertIn("等待人工登录", frontend_text)
        self.assertIn("human_intervention", frontend_text)
        self.assertIn("resolution_steps", frontend_text)
        self.assertIn("登录完成，恢复任务", frontend_text)

    def test_pdf_gemini_selects_and_confirms_pro_before_upload(self):
        worker = GeminiBrowserWorker.__new__(GeminiBrowserWorker)
        worker.options = GeminiBrowserOptions(
            user_data_dir="/tmp/gemini-profile",
            prefer_pro_model=True,
            model_switch_timeout_seconds=2,
        )
        picker_before = MagicMock()
        picker_before.inner_text.return_value = "Fast"
        picker_before.get_attribute.return_value = None
        pro_option = MagicMock()
        pro_option.inner_text.return_value = "Pro"
        pro_option.get_attribute.return_value = None
        picker_after = MagicMock()
        picker_after.inner_text.return_value = "Gemini 2.5 Pro"
        picker_after.get_attribute.return_value = None

        with patch.object(
            worker,
            "_first_visible",
            side_effect=[picker_before, pro_option, picker_after],
        ):
            result = worker._try_select_pro_model(MagicMock())

        picker_before.click.assert_called_once_with(timeout=8_000)
        pro_option.click.assert_called_once_with(timeout=8_000)
        self.assertEqual("selected_pro", result["status"])
        self.assertTrue(result["confirmed"])

    def test_pdf_gemini_keeps_running_when_pro_picker_is_unavailable(self):
        worker = GeminiBrowserWorker.__new__(GeminiBrowserWorker)
        worker.options = GeminiBrowserOptions(
            user_data_dir="/tmp/gemini-profile",
            prefer_pro_model=True,
            model_switch_timeout_seconds=0,
        )

        with patch.object(worker, "_first_visible", return_value=None):
            result = worker._try_select_pro_model(MagicMock())

        self.assertEqual("picker_not_found", result["status"])
        self.assertFalse(result["selected"])

    def test_gemini_pro_selectors_only_target_model_controls_and_menu_options(self):
        worker = GeminiBrowserWorker.__new__(GeminiBrowserWorker)
        worker.options = GeminiBrowserOptions(user_data_dir="/tmp/gemini-profile")

        picker_selectors = worker._selectors("model_picker")
        option_selectors = worker._selectors("pro_model_option")

        self.assertNotIn("text=Pro", picker_selectors)
        self.assertNotIn("button:has-text('Pro')", option_selectors)
        self.assertIn("button[data-test-id='bard-mode-menu-button']", picker_selectors)
        self.assertIn("button[aria-label*='当前模式']", picker_selectors)
        self.assertIn("[data-test-id^='bard-mode-option-']:has-text('Pro')", option_selectors)
        self.assertTrue(any("aria-label" in item or "data-test" in item for item in picker_selectors))
        self.assertTrue(
            all("role=" in item or "mat-" in item or "data-test-id" in item for item in option_selectors)
        )

    def test_failed_storyboard_regeneration_keeps_previous_completed_plan(self):
        failed_retry = SimpleNamespace(id="retry", status="failed")
        completed_plan = SimpleNamespace(id="usable", status="completed")

        selected = _preferred_storyboard([failed_retry, completed_plan])

        self.assertEqual("usable", selected.id)

    def test_active_storyboard_regeneration_remains_visible(self):
        active_retry = SimpleNamespace(id="retry", status="processing")
        completed_plan = SimpleNamespace(id="usable", status="completed")

        selected = _preferred_storyboard([active_retry, completed_plan])

        self.assertEqual("retry", selected.id)

    def material(self):
        script = "第一段讲需求增长。\n第二段讲产品升级，利润率提升。最后提示客户集中风险。"
        return {
            "title": "测试研报",
            "final_script_for_tts": script,
            "structured_result": {"final_script_for_tts": script},
        }

    def test_normalizer_preserves_every_source_character(self):
        script = self.material()["final_script_for_tts"]
        ds_payload = {
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
            "title_scene": {
                "scene_title": "标题镜头",
                "visual_prompt": "测试研报标题画面",
                "estimated_seconds": 3,
            },
            "segments": [
                {
                    "scene_title": "需求",
                    "script_text": "第一段讲需求增长。",
                    "visual_prompt": "数据中心需求增长的财经信息图",
                    "estimated_seconds": 5,
                },
                {
                    "scene_title": "产品与风险",
                    "script_text": "第二段讲产品升级，利润率提升。最后提示客户集中风险。",
                    "visual_prompt": "产品升级与风险警示的分屏画面",
                    "estimated_seconds": 9,
                },
            ],
        }
        gemini_text = "测试研报标题画面\n数据中心需求增长的财经信息图\n产品升级与风险警示的分屏画面"
        with patch.object(service, "_deepseek_json", return_value=ds_payload):
            result = service.normalize_storyboard_response(self.material(), gemini_text)

        self.assertEqual("".join(item["script_text"] for item in result["segments"]), script)
        self.assertEqual(result["covered_script_chars"], len(script))
        self.assertEqual(result["segment_count"], 3)
        self.assertEqual(result["title_scene"]["script_text"], "测试研报")

    def test_labeled_gemini_response_is_normalized_without_ds_rewriting_prompts(self):
        gemini_text = """
标题镜头
【文案原句】：标题：测试研报
【画面动作】：新建画面
【视觉隐喻】：标题板
【AI 提示词】：Exact English title prompt, --ar 16:9
分镜 1
【文案原句】：第一段讲需求增长。
【画面动作】：新建画面
【视觉隐喻】：需求增长
【AI 提示词】：Exact English demand prompt, --ar 16:9
分镜 2
【文案原句】：第二段讲产品升级，利润率提升。最后提示客户集中风险。
【画面动作】：新建画面
【视觉隐喻】：产品与风险
【AI 提示词】：Exact English risk prompt, --ar 16:9
""".strip()
        with patch.object(service, "_deepseek_json", side_effect=AssertionError("DS should not be called")):
            result = service.normalize_storyboard_response(self.material(), gemini_text)

        self.assertEqual(result["provider"], "deterministic_parser")
        self.assertEqual(result["title_scene"]["visual_prompt"], "Exact English title prompt, --ar 16:9")
        self.assertEqual(result["segments"][1]["visual_prompt"], "Exact English risk prompt, --ar 16:9")
        self.assertEqual(
            "".join(item["script_text"] for item in result["segments"]),
            self.material()["final_script_for_tts"],
        )

    def test_labeled_gemini_response_uses_complete_suffix_after_repeated_prefix(self):
        material = {
            "title": "测试研报",
            "final_script_for_tts": "第一段。第二段。第三段。",
        }
        # Gemini can repeat the already-visible prefix when a long answer is
        # continued.  The complete suffix is still entirely Gem-authored and
        # must be accepted without a second provider call.
        gemini_text = """
标题镜头
【文案原句】：标题：测试研报
【画面动作】：新建画面
【视觉隐喻】：标题板
【AI 提示词】：Exact title prompt
分镜 1
【文案原句】：第一段。
【画面动作】：新建画面
【视觉隐喻】：第一段
【AI 提示词】：Repeated prefix prompt
分镜 1
【文案原句】：第一段。第二段。
【画面动作】：新建画面
【视觉隐喻】：前两段
【AI 提示词】：Complete first prompt
分镜 2
【文案原句】：第三段。
【画面动作】：新建画面
【视觉隐喻】：第三段
【AI 提示词】：Complete second prompt
【全文分镜完成】
""".strip()
        with patch.object(service, "_deepseek_json", side_effect=AssertionError("DS should not be called")):
            result = service.normalize_storyboard_response(material, gemini_text)

        self.assertEqual("deterministic_parser", result["provider"])
        self.assertEqual(
            ["Complete first prompt", "Complete second prompt"],
            [item["visual_prompt"] for item in result["segments"]],
        )
        self.assertEqual(material["final_script_for_tts"], "".join(
            item["script_text"] for item in result["segments"]
        ))

    def test_labeled_gemini_response_accepts_observation_typo_for_action_field(self):
        gemini_text = """
标题镜头
【文案原句】：标题：测试研报
【画面动作】：新建画面
【视觉隐喻】：标题板
【AI 提示词】：Exact English title prompt, --ar 16:9
分镜 1
【文案原句】：第一段讲需求增长。
【画面观察】：新建画面
【视觉隐喻】：需求增长
【AI 提示词】：Exact English demand prompt, --ar 16:9
分镜 2
【文案原句】：第二段讲产品升级，利润率提升。最后提示客户集中风险。
【画面动作】：新建画面
【视觉隐喻】：产品与风险
【AI 提示词】：Exact English risk prompt, --ar 16:9
""".strip()
        with patch.object(service, "_deepseek_json", side_effect=AssertionError("DS should not be called")):
            result = service.normalize_storyboard_response(self.material(), gemini_text)

        self.assertEqual("deterministic_parser", result["provider"])
        self.assertEqual(3, result["segment_count"])

    def test_numbered_zero_title_and_compact_ai_label_use_deterministic_parser(self):
        gemini_text = """
白板动画分镜生成器说 分镜 0（标题镜头）
【文案原句】：标题：测试研报
【画面动作】：新建画面
【视觉隐喻】：标题板
【AI 提示词】：Exact English title prompt, --ar 16:9
分镜 1
【文案原句】：第一段讲需求增长。
【画面动作】：新建画面
【视觉隐喻】：需求增长
【AI提示词】：ExactEnglishdemandprompt,--ar16:9
分镜 2
【文案原句】：第二段讲产品升级，利润率提升。最后提示客户集中风险。
【画面动作】：新建画面
【视觉隐喻】：产品与风险
【AI 提示词】：Exact English risk prompt, --ar 16:9
""".strip()
        with patch.object(service, "_deepseek_json", side_effect=AssertionError("DS should not be called")):
            result = service.normalize_storyboard_response(self.material(), gemini_text)

        self.assertEqual("deterministic_parser", result["provider"])
        self.assertEqual("Exact English title prompt, --ar 16:9", result["title_scene"]["visual_prompt"])
        self.assertEqual("ExactEnglishdemandprompt,--ar16:9", result["segments"][0]["visual_prompt"])

    def test_source_script_repairs_gem_gap_without_ds_or_prompt_rewrite(self):
        material = {
            "title": "测试研报",
            "final_script_for_tts": "第一句。Gem 漏掉的第二句。",
        }
        gemini_text = """
分镜 0（标题镜头）
【文案原句】：标题：测试研报
【画面动作】：新建画面
【视觉隐喻】：标题板
【AI 提示词】：ExactEnglishTitlePrompt,--ar16:9
分镜 1
【文案原句】：第一句。
【画面动作】：新建画面
【视觉隐喻】：正文
【AI提示词】：ExactEnglishBodyPrompt,--ar16:9
""".strip()
        with patch.object(service, "_deepseek_json", side_effect=AssertionError("DS should not be called")):
            result = service.normalize_storyboard_response(material, gemini_text)

        self.assertEqual("ExactEnglishTitlePrompt,--ar16:9", result["title_scene"]["visual_prompt"])
        self.assertEqual("ExactEnglishBodyPrompt,--ar16:9", result["segments"][0]["visual_prompt"])
        self.assertEqual(material["final_script_for_tts"], result["segments"][0]["script_text"])

    def test_source_script_repairs_single_character_omission_inside_scene(self):
        material = {
            "title": "测试研报",
            "final_script_for_tts": "第一句。企业在二零二六年将继续扩产。最后一句。",
        }
        gemini_text = """
标题镜头
【文案原句】：标题：测试研报
【画面动作】：新建画面
【视觉隐喻】：标题板
【AI 提示词】：Exact title prompt, --ar 16:9
分镜 1
【文案原句】：第一句。
【画面动作】：新建画面
【视觉隐喻】：开场
【AI 提示词】：Exact first prompt, --ar 16:9
分镜 2
【文案原句】：企业在二零二六年继续扩产。
【画面动作】：新建画面
【视觉隐喻】：扩产
【AI 提示词】：Exact second prompt, --ar 16:9
分镜 3
【文案原句】：最后一句。
【画面动作】：新建画面
【视觉隐喻】：结束
【AI 提示词】：Exact third prompt, --ar 16:9
""".strip()
        with patch.object(service, "_deepseek_json", side_effect=AssertionError("DS should not be called")):
            result = service.normalize_storyboard_response(material, gemini_text)

        self.assertEqual(material["final_script_for_tts"], "".join(
            item["script_text"] for item in result["segments"]
        ))
        self.assertEqual("Exact second prompt, --ar 16:9", result["segments"][1]["visual_prompt"])

    def test_failed_storyboard_has_reuse_gem_normalize_retry_entry(self):
        frontend_text = (
            BACKEND_DIR.parents[0] / "frontend" / "js" / "report-stick-figure-video-workflow-detail.js"
        ).read_text(encoding="utf-8")
        api_text = (BACKEND_DIR / "api" / "report_visual_plan.py").read_text(encoding="utf-8")
        task_text = (BACKEND_DIR / "tasks" / "report_visual_plan.py").read_text(encoding="utf-8")

        self.assertIn("data-retry-storyboard-normalize", frontend_text)
        self.assertIn("复用 Gem 原文继续梳理", frontend_text)
        self.assertIn("分镜整理未保存，Gem 原始返回仍已保留", frontend_text)
        self.assertIn('/jobs/<job_id>/retry-normalize', api_text)
        self.assertIn("retry_normalize_report_visual_plan_job", task_text)

    def test_storyboard_worker_returns_merged_incomplete_continuation_text(self):
        worker_text = (BACKEND_DIR / "services" / "gemini_browser.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("final_response_text = combined_response_text", worker_text)
        self.assertIn('"final_response_text": final_response_text', worker_text)

    def test_normalize_retry_prefers_first_self_contained_completed_turn(self):
        stored = {
            "turns": [
                {
                    "response_text": (
                        "标题镜头【AI 提示词】：title 分镜 1"
                        "【AI 提示词】：body【全文分镜完成】"
                    ),
                },
                {"response_text": "重复续写"},
            ],
            "final_response_text": "合并后的重复内容",
        }

        self.assertEqual(
            stored["turns"][0]["response_text"],
            _checkpoint_storyboard_text(stored),
        )

    def test_normalize_retry_reconstructs_initial_and_continuation_turns(self):
        stored = {
            "turns": [
                {
                    "kind": "initial",
                    "response_text": (
                        "标题镜头\n【文案原句】：标题：测试研报\n"
                        "【AI 提示词】：title\n分镜 1\n【文案原句】：第一句。\n"
                        "【AI 提示词】：one"
                    ),
                },
                {
                    "kind": "incomplete_storyboard_continuation",
                    "response_text": (
                        "分镜 2\n【文案原句】：第二句。\n"
                        "【AI 提示词】：two\n【全文分镜完成】"
                    ),
                },
            ],
            # Older workers persisted only the last continuation here.
            "final_response_text": "分镜 2\n【文案原句】：第二句。\n【AI 提示词】：two",
        }

        reconstructed = _checkpoint_storyboard_text(stored)
        self.assertIn("标题镜头", reconstructed)
        self.assertIn("【文案原句】：第一句。", reconstructed)
        self.assertIn("【文案原句】：第二句。", reconstructed)

    def test_normalizer_restores_labeled_gem_prompts_after_ds_repairs_blocks(self):
        gemini_text = (
            "标题镜头\n【文案原句】：标题：测试研报\n"
            "【AI 提示词】：Gem title prompt\n"
            "分镜 1\n【文案原句】：第一段讲需求增长。\n"
            "【AI 提示词】：Gem demand prompt\n"
            "分镜 2\n【文案原句】：第二段讲产品升级，利润率提升。最后提示客户集中风险。\n"
            "【AI 提示词】：Gem risk prompt"
        )
        ds_payload = {
            "title_scene": {"scene_title": "标题镜头", "visual_prompt": "DS title rewrite"},
            "segments": [
                {
                    "scene_title": "需求",
                    "script_text": "第一段讲需求增长。",
                    "visual_prompt": "DS demand rewrite",
                },
                {
                    "scene_title": "风险",
                    "script_text": "第二段讲产品升级，利润率提升。最后提示客户集中风险。",
                    "visual_prompt": "DS risk rewrite",
                },
            ],
        }
        with patch.object(service, "_deepseek_json", return_value=ds_payload):
            result = service.normalize_storyboard_response(self.material(), gemini_text)

        self.assertEqual("Gem title prompt", result["title_scene"]["visual_prompt"])
        self.assertEqual("Gem demand prompt", result["segments"][0]["visual_prompt"])
        self.assertEqual("Gem risk prompt", result["segments"][1]["visual_prompt"])

    def test_normalizer_reports_incomplete_labeled_gem_prompts_precisely(self):
        gemini_text = (
            "标题镜头\n【文案原句】：标题：测试研报\n"
            "【AI 提示词】：Gem title prompt\n"
            "分镜 1\n【文案原句】：第一段讲需求增长。\n"
            "【AI 提示词】：Gem demand prompt\n"
            "分镜 2\n【文案原句】：第二段讲产品升级，利润率提升。最后提示客户集中风险。"
        )
        ds_payload = {
            "title_scene": {"scene_title": "标题镜头", "visual_prompt": "DS title rewrite"},
            "segments": [
                {"scene_title": "需求", "script_text": "第一段讲需求增长。", "visual_prompt": "DS demand"},
                {
                    "scene_title": "风险",
                    "script_text": "第二段讲产品升级，利润率提升。最后提示客户集中风险。",
                    "visual_prompt": "DS risk",
                },
            ],
        }
        with patch.object(service, "_deepseek_json", return_value=ds_payload):
            with self.assertRaisesRegex(ValueError, "Gem 原始分镜缺少完整 AI 提示词"):
                service.normalize_storyboard_response(self.material(), gemini_text)

    def test_incomplete_ds_result_is_rejected(self):
        ds_payload = {
            "title_scene": {"scene_title": "标题镜头", "visual_prompt": "标题画面"},
            "segments": [{
                "scene_title": "只有开头",
                "script_text": "第一段讲需求增长。",
                "visual_prompt": "需求增长画面",
            }]
        }
        with patch.object(service, "_deepseek_json", return_value=ds_payload):
            with self.assertRaisesRegex(ValueError, "未完整覆盖"):
                service.normalize_storyboard_response(self.material(), "标题画面\n需求增长画面")

    def test_storyboard_prompt_requires_full_coverage(self):
        prompt = service.storyboard_prompt(self.material())
        self.assertIn("不得省略", prompt)
        self.assertIn(self.material()["final_script_for_tts"], prompt)
        self.assertIn("禁止翻译或改写", prompt)
        self.assertIn("必须先单独输出一个‘标题镜头’", prompt)
        self.assertIn("不得省略标题镜头", prompt)

    def test_normalizer_must_preserve_gem_prompt_language_and_wording(self):
        english_prompt = "Cinematic data center, blue fiber links, ultra-detailed, 8k"
        ds_payload = {
            "title_scene": {"scene_title": "标题镜头", "visual_prompt": "Editorial title card, clean typography"},
            "segments": [{
                "scene_title": "完整文案",
                "script_text": self.material()["final_script_for_tts"],
                "visual_prompt": english_prompt,
                "estimated_seconds": 12,
            }]
        }
        with patch.object(service, "_deepseek_json", return_value=ds_payload):
            result = service.normalize_storyboard_response(
                self.material(),
                f"Editorial title card, clean typography\nPrompt:\n{english_prompt}",
            )
        self.assertEqual(result["segments"][0]["visual_prompt"], english_prompt)

    def test_normalizer_rejects_translated_or_rewritten_prompt(self):
        ds_payload = {
            "title_scene": {"scene_title": "标题镜头", "visual_prompt": "Editorial title card"},
            "segments": [{
                "scene_title": "完整文案",
                "script_text": self.material()["final_script_for_tts"],
                "visual_prompt": "蓝色光纤连接的数据中心",
            }]
        }
        with patch.object(service, "_deepseek_json", return_value=ds_payload):
            with self.assertRaisesRegex(ValueError, "改写或翻译"):
                service.normalize_storyboard_response(
                    self.material(),
                    "Editorial title card\nCinematic data center with blue fiber links",
                )

    def test_hot_submission_uses_projection_not_task_json(self):
        api_path = BACKEND_DIR / "api" / "report_visual_plan.py"
        text = api_path.read_text(encoding="utf-8")
        body = text.split("def _resolve_storyboard_source", 1)[1].split("\ndef create_preview", 1)[0]
        self.assertIn("PublicationTask.final_script_chars", body)
        self.assertNotIn("PublicationTask.params", body)
        self.assertNotIn("PublicationTask.result", body)

    def test_task_result_keeps_only_summary_not_segment_list(self):
        task_path = BACKEND_DIR / "tasks" / "report_visual_plan.py"
        text = task_path.read_text(encoding="utf-8")
        persist = text.split("def _persist_success", 1)[1].split("\ndef _fail", 1)[0]
        self.assertIn("ReportVisualPlanSegment(", persist)
        self.assertNotIn('"segments": segments', persist)

    def test_raw_gem_response_has_on_demand_endpoint_and_ui_entry(self):
        api_path = BACKEND_DIR / "api" / "report_visual_plan.py"
        api_text = api_path.read_text(encoding="utf-8")
        self.assertIn('/jobs/<job_id>/gemini-raw', api_text)
        self.assertIn('final_response_text', api_text)
        frontend_path = BACKEND_DIR.parents[0] / "frontend" / "js" / "report-script-material-workflow-test.js"
        frontend_text = frontend_path.read_text(encoding="utf-8")
        self.assertIn('data-storyboard-raw', frontend_text)
        self.assertIn('/gemini-raw', frontend_text)

    def test_existing_storyboard_is_discovered_by_source(self):
        api_path = BACKEND_DIR / "api" / "report_visual_plan.py"
        api_text = api_path.read_text(encoding="utf-8")
        self.assertIn('/sources/<source_task_id>/latest', api_text)
        frontend_path = BACKEND_DIR.parents[0] / "frontend" / "js" / "report-script-material-workflow-test.js"
        frontend_text = frontend_path.read_text(encoding="utf-8")
        self.assertIn('loadExistingStoryboard', frontend_text)
        self.assertIn('/latest', frontend_text)

    def test_completed_storyboard_polling_uses_immutable_cache(self):
        api_path = BACKEND_DIR / "api" / "report_visual_plan.py"
        api_text = api_path.read_text(encoding="utf-8")
        latest = api_text.split("def get_latest_storyboard_for_source(", 1)[1].split(
            "\n\n@report_visual_plan_bp.route", 1
        )[0]
        create = api_text.split("def create_storyboard_job(", 1)[1].split(
            "\ndef _resolve_storyboard_source", 1
        )[0]

        self.assertLess(latest.index("_cached_terminal_storyboard"), latest.index("PublicationTask.query"))
        self.assertIn('if task.status == "completed" and output:', latest)
        self.assertIn("_cache_terminal_storyboard", latest)
        self.assertIn(
            "_clear_terminal_storyboard_cache(source_cache_key, current_user=current_user)",
            create,
        )

    def test_completed_storyboard_job_detail_uses_immutable_cache(self):
        api_path = BACKEND_DIR / "api" / "report_visual_plan.py"
        api_text = api_path.read_text(encoding="utf-8")
        detail = api_text.split("def get_job(", 1)[1].split(
            "\n\n@report_visual_plan_bp.route", 1
        )[0]
        payload = api_text.split("def _storyboard_job_payload(", 1)[1].split(
            "\ndef _storyboard_output", 1
        )[0]

        self.assertLess(detail.index("_cached_terminal_storyboard_job"), detail.index("_get_owned_visual_task"))
        self.assertIn('_cache_terminal_storyboard_job(current_user, task.id, response_payload)', detail)
        self.assertIn("db.session.query(", payload)
        self.assertNotIn("ReportVisualPlanSegment.query.options", payload)

    def test_stick_video_detail_can_force_regenerate_storyboard(self):
        frontend_path = BACKEND_DIR.parents[0] / "frontend" / "js" / "report-stick-figure-video-workflow-detail.js"
        frontend_text = frontend_path.read_text(encoding="utf-8")

        self.assertIn("重新请求 Gem", frontend_text)
        self.assertIn("复用 Gem 原文继续梳理", frontend_text)
        self.assertIn('data-generate-storyboard data-force="1"', frontend_text)
        self.assertIn("现有图片、语音和视频不会复用", frontend_text)
        self.assertIn("visual_scheme_key:this.visualScheme.key", frontend_text)

    def test_stick_video_detail_hides_asset_batch_actions_until_storyboard_succeeds(self):
        frontend_path = BACKEND_DIR.parents[0] / "frontend" / "js" / "report-stick-figure-video-workflow-detail.js"
        frontend_text = frontend_path.read_text(encoding="utf-8")

        self.assertIn("const storyboardReady=storyboardStatus==='succeeded'", frontend_text)
        self.assertIn("暂不能批量生成图片", frontend_text)
        self.assertIn("标题与正文分镜完成后，这里才可以批量生成图片", frontend_text)

    def test_visual_schemes_bind_storyboard_and_image_contract_together(self):
        schemes = {item["key"]: item for item in visual_scheme_payloads()}
        self.assertIn("stick_figure", schemes)
        self.assertIn("doraemon", schemes)
        self.assertTrue(schemes["stick_figure"]["configured"])
        self.assertTrue(schemes["doraemon"]["configured"])

        whiteboard = scene_image_prompt("A robot cat points to a chart", "机器人讲解图表", "stick_figure")
        doraemon = scene_image_prompt("A robot cat points to a chart", "机器人讲解图表", "doraemon")
        self.assertIn("Render the scene as a Chinese self-media whiteboard animation", whiteboard)
        self.assertIn("retro 1990s Japanese 2D animation", doraemon)
        self.assertIn("Every visible text label must be concise Simplified Chinese", doraemon)
        self.assertIn("Classic Doraemon anime style", doraemon)
        self.assertNotIn("Render the scene as a Chinese self-media whiteboard animation", doraemon)
        self.assertNotEqual(
            scene_image_prompt_hash("same", "相同", "stick_figure"),
            scene_image_prompt_hash("same", "相同", "doraemon"),
        )

    def test_doraemon_scheme_is_enabled_by_configured_gem_url(self):
        with patch.dict("os.environ", {"REPORT_DORAEMON_STORYBOARD_GEMINI_URL": "https://gemini.google.com/gem/doraemon-test"}):
            scheme = visual_scheme("doraemon")
            self.assertEqual(scheme.storyboard_gemini_url, "https://gemini.google.com/gem/doraemon-test")
            self.assertEqual(scheme.image_contract, "doraemon_retro_anime_zh")

    def test_doraemon_scheme_uses_production_gem_by_default(self):
        with patch.dict("os.environ", {}, clear=False):
            scheme = visual_scheme("doraemon")
        self.assertEqual(
            scheme.storyboard_gemini_url,
            "https://gemini.google.com/gem/f9fb1e11c799",
        )
        self.assertEqual(scheme.image_profile_version, "doraemon_retro_anime_zh_16_9_v1")

    def test_stick_video_workflow_matches_audio_page_shape(self):
        frontend_root = BACKEND_DIR.parents[0] / "frontend"
        html = (frontend_root / "report-stick-figure-video-workflow.html").read_text(encoding="utf-8")
        script = (frontend_root / "js" / "report-stick-figure-video-workflow.js").read_text(encoding="utf-8")

        self.assertIn('js/download-count-highlight.js', html)
        self.assertIn('DownloadCountHighlight?.badgeClass', script)
        self.assertIn('DownloadCountHighlight?.badgeText', script)
        api = (BACKEND_DIR / "api" / "report_script_material_workflow.py").read_text(encoding="utf-8")
        self.assertIn("xl:grid-cols-[460px_minmax(0,1fr)]", html)
        self.assertIn('id="pdf-source-upload"', html)
        self.assertIn('id="pdf-source-asset"', html)
        self.assertIn('id="job-list"', html)
        self.assertIn('id="visual-scheme-select"', html)
        self.assertIn("visual_scheme_key", script)
        self.assertIn('aria-pressed="true"', html)
        self.assertIn("renderAssetTabs()", script)
        self.assertIn("button.setAttribute('aria-pressed'", script)
        self.assertIn("border-slate-900 bg-slate-900", script)
        self.assertIn("fa-check':'fa-plus", script)
        self.assertIn('data-asset-page="next"', script)
        self.assertIn("详情新页", script)
        self.assertIn('target="_blank" rel="noopener noreferrer"', script)

        detail_script = (frontend_root / "js" / "report-stick-figure-video-workflow-detail.js").read_text(encoding="utf-8")
        workflow_api = (BACKEND_DIR / "api" / "report_stick_figure_video_workflow.py").read_text(encoding="utf-8")
        self.assertIn("storyboardJobId=workflow.data?.storyboard_job_id", detail_script)
        self.assertIn("/api/report-visual-plans/jobs/${encodeURIComponent(storyboardJobId)}", detail_script)
        self.assertIn("def _storyboard_for_workflow(parent):", workflow_api)
        self.assertIn("PublicationTask.parent_task_id == str(parent.id)", workflow_api)
        submit_body = script.split("async submit(){", 1)[1].split("async loadJobs()", 1)[0]
        self.assertNotIn("location.href", submit_body)
        self.assertIn("自动投递分镜", submit_body)
        stick_api = (BACKEND_DIR / "api" / "report_stick_figure_video_workflow.py").read_text(encoding="utf-8")
        material_task = (BACKEND_DIR / "tasks" / "report_script_material_workflow.py").read_text(encoding="utf-8")
        storyboard_task = (BACKEND_DIR / "tasks" / "report_visual_plan.py").read_text(encoding="utf-8")
        self.assertIn("_latest_material_task_for_asset", stick_api)
        self.assertIn("enqueue_report_visual_plan_for_material", stick_api)
        self.assertIn("_dispatch_auto_storyboard(workflow_task_id)", material_task)
        process_body = material_task.split("def _process_job(", 1)[1].split("def _dispatch_auto_storyboard(", 1)[0]
        self.assertGreater(
            process_body.index("_dispatch_auto_storyboard(workflow_task_id)"),
            process_body.index("_complete_material_task("),
        )
        self.assertIn("def enqueue_report_visual_plan_for_material(", storyboard_task)
        process_storyboard_body = storyboard_task.split("def _process_job(", 1)[1].split(
            "\ndef _update_status", 1
        )[0]
        self.assertGreater(
            process_storyboard_body.index("_dispatch_auto_scene_images(task_id)"),
            process_storyboard_body.index("_persist_success("),
        )
        auto_images_body = storyboard_task.split("def _dispatch_auto_scene_images(", 1)[1].split(
            "\ndef _sync_parent_storyboard_failure", 1
        )[0]
        self.assertIn("_ensure_scene_assets(parent_snapshot, force_refresh=True)", auto_images_body)
        self.assertIn("_enqueue_image_asset(parent_snapshot, asset)", auto_images_body)
        self.assertIn(
            "_auto_enqueue_whole_audio_alignment(",
            auto_images_body,
        )
        self.assertNotIn("PublicationTask.params", auto_images_body)
        self.assertNotIn("PublicationTask.result", auto_images_body)
        self.assertIn('request.files.get("pdf")', api)
        self.assertIn("_save_pdf_upload", api)

    def test_scene_assets_are_row_based_and_tasks_keep_only_references(self):
        model_text = (BACKEND_DIR / "models" / "report_stick_figure_asset.py").read_text(encoding="utf-8")
        api_text = (BACKEND_DIR / "api" / "report_stick_figure_video_workflow.py").read_text(encoding="utf-8")
        image_task_text = (BACKEND_DIR / "tasks" / "report_stick_figure_assets.py").read_text(encoding="utf-8")
        frontend_text = (BACKEND_DIR.parents[0] / "frontend" / "js" / "report-stick-figure-video-workflow-detail.js").read_text(encoding="utf-8")
        self.assertIn("class ReportStickFigureSceneAsset", model_text)
        self.assertIn("visual_prompt_hash", model_text)
        self.assertIn('"scene_asset_id": asset_id', api_text)
        self.assertIn('"text_source": {"kind": "report_visual_plan_segment"', api_text)
        self.assertNotIn('"visual_prompt":', image_task_text)
        self.assertIn("续跑失败图片", frontend_text)
        self.assertIn("续跑失败语音", frontend_text)

    def test_scene_files_use_stable_zero_padded_sequence(self):
        self.assertTrue(scene_image_path("workflow-1", 0).endswith("/workflow-1/images/000_title.png"))
        self.assertTrue(scene_image_path("workflow-1", 7).endswith("/workflow-1/images/007.png"))

    def test_scene_image_prompt_binds_whiteboard_style_and_chinese_labels(self):
        raw = 'Photorealistic server room with label "Citi 149%", cinematic lighting, 8k'
        script = "花旗：中际旭创净利润复合增速149%"
        prompt = scene_image_prompt(raw, script)
        self.assertIn(raw, prompt)
        self.assertIn(script, prompt)
        self.assertIn("Every visible text label in the image must be concise Simplified Chinese", prompt)
        self.assertIn("Ignore conflicting style words such as photorealistic", prompt)
        self.assertTrue(prompt.endswith(IMAGE_STYLE_SUFFIX))

    def test_scene_image_prompt_hash_covers_style_and_chinese_context(self):
        first = scene_image_prompt_hash("same scene", "中文标签一")
        second = scene_image_prompt_hash("same scene", "中文标签二")
        self.assertNotEqual(first, second)

    def test_completed_pre_scheme_hash_image_can_be_adopted_without_regeneration(self):
        with tempfile.TemporaryDirectory() as tempdir:
            image_path = Path(tempdir) / "000_title.png"
            image_path.write_bytes(b"completed-image")
            asset = SimpleNamespace(
                image_status="completed",
                image_task_id="image-task-1",
                image_path=str(image_path),
                image_bytes=image_path.stat().st_size,
                image_width=1280,
                image_height=720,
            )
            self.assertTrue(_completed_image_asset_can_adopt_prompt_hash(asset))

            asset.image_status = "failed"
            self.assertFalse(_completed_image_asset_can_adopt_prompt_hash(asset))

    def test_generated_image_fallback_replaces_existing_file(self):
        class ImageNode:
            def screenshot(self, path):
                Path(path).write_bytes(b"new-whiteboard-image")

        class ImageList:
            def nth(self, _index):
                return ImageNode()

        class Page:
            def evaluate(self, *_args):
                raise RuntimeError("blob fetch unavailable")

            def locator(self, selector):
                self.selector = selector
                return ImageList()

        worker = GeminiBrowserWorker.__new__(GeminiBrowserWorker)
        with tempfile.TemporaryDirectory() as tempdir:
            output = Path(tempdir) / "000_title.png"
            output.write_bytes(b"old-photorealistic-image")
            result = worker._save_generated_image(
                Page(),
                {"src": "blob:new-image", "dom_index": 0, "width": 1280, "height": 720},
                str(output),
            )
            self.assertEqual(output.read_bytes(), b"new-whiteboard-image")
            self.assertEqual(result["saved_by"], "screenshot")
            self.assertFalse(output.with_suffix(".png.part").exists())

    def test_storyboard_response_collects_only_new_large_images_in_dom_order(self):
        worker = GeminiBrowserWorker.__new__(GeminiBrowserWorker)
        worker._current_job_id = "storyboard-job"
        records = [
            {"src": "baseline", "dom_index": 0, "width": 900, "height": 500},
            {"src": "too-small", "dom_index": 1, "width": 100, "height": 100},
            {"src": "scene-two", "dom_index": 8, "width": 900, "height": 500},
            {"src": "scene-one", "dom_index": 4, "width": 900, "height": 500},
        ]
        with tempfile.TemporaryDirectory() as tempdir, patch.object(
            worker,
            "_visible_image_records",
            return_value=records,
        ), patch.object(
            worker,
            "_save_generated_image",
            side_effect=lambda _page, row, path: {
                "path": path,
                "bytes": 100,
                "source": row["src"],
                "saved_by": "fetch",
                "width": 900,
                "height": 500,
            },
        ):
            result = worker._save_embedded_response_images(
                object(),
                ["baseline"],
                tempdir,
            )

        self.assertEqual(["scene-one", "scene-two"], [item["source"] for item in result])
        self.assertEqual([1, 2], [item["ordinal"] for item in result])

    def test_image_only_storyboard_detection_ignores_baseline_and_small_images(self):
        worker = GeminiBrowserWorker.__new__(GeminiBrowserWorker)
        worker._visible_image_records = lambda _page: [
            {"src": "baseline", "width": 900, "height": 500},
            {"src": "small-new", "width": 100, "height": 100},
        ]
        self.assertFalse(worker._has_new_large_image(object(), ["baseline"]))

        worker._visible_image_records = lambda _page: [
            {"src": "baseline", "width": 900, "height": 500},
            {"src": "generated", "width": 900, "height": 500},
        ]
        self.assertTrue(worker._has_new_large_image(object(), ["baseline"]))

    def test_storyboard_task_enables_image_only_recovery(self):
        task_text = (BACKEND_DIR / "tasks" / "report_visual_plan.py").read_text(encoding="utf-8")
        self.assertIn("recover_image_only_storyboard=True", task_text)
        self.assertIn("response_complete_callback=", task_text)

    def test_storyboard_worker_uses_multi_turn_timeout_budget(self):
        with patch.dict("os.environ", {
            "REPORT_VISUAL_PLAN_GEMINI_RESPONSE_TIMEOUT": "",
            "REPORT_VISUAL_PLAN_GEMINI_TOTAL_TIMEOUT": "",
        }, clear=False), patch.object(
            GeminiBrowserWorker,
            "_validate_options",
            return_value=None,
        ):
            worker = _storyboard_gemini_worker()

        self.assertGreaterEqual(worker.options.response_timeout_seconds, 1800)
        self.assertGreaterEqual(worker.options.total_timeout_seconds, 7200)
        self.assertGreaterEqual(
            worker.options.total_timeout_seconds,
            worker.options.response_timeout_seconds * 3 + 300,
        )

    def test_gemini_turn_checkpoint_survives_before_final_result(self):
        with tempfile.TemporaryDirectory() as tempdir:
            output = Path(tempdir) / "gemini_storyboard.json"
            turns = [{
                "index": 1,
                "kind": "initial",
                "response_text": "首轮分镜",
            }]

            _write_generation_checkpoint(
                output,
                prompt="生成分镜",
                followups=[],
                initial_response_text="首轮分镜",
                combined_response_text="首轮分镜",
                turns=turns,
                page_url="https://gemini.google.com/app",
                target_url="https://gemini.google.com/gem/test",
                embedded_images=[],
            )

            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual("partial", payload["status"])
            self.assertEqual("首轮分镜", payload["final_response_text"])
            self.assertEqual(turns, payload["turns"])
            self.assertFalse((output.parent / ".gemini_storyboard.json.tmp").exists())

    def test_gemini_execution_budget_starts_after_profile_context_acquired(self):
        worker_text = (BACKEND_DIR / "services" / "gemini_browser.py").read_text(
            encoding="utf-8"
        )
        resets = re.findall(
            r"context = self\._launch_context\(playwright\)\n"
            r"(?:(?:\s*#.*)?\n){0,4}"
            r"\s+started = time\.monotonic\(\)",
            worker_text,
        )

        self.assertEqual(3, len(resets))

    def test_gemini_browser_launch_has_a_bounded_timeout(self):
        worker_text = (BACKEND_DIR / "services" / "gemini_browser.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("browser_launch_timeout_seconds", worker_text)
        self.assertIn('"timeout": max(5, int(self.options.browser_launch_timeout_seconds or 0)) * 1000', worker_text)
        self.assertIn("Gemini 浏览器启动超时", worker_text)

    def test_storyboard_worker_serializes_shared_gemini_profile(self):
        runner_text = (
            BACKEND_DIR.parents[1]
            / "scripts"
            / "dev"
            / "run-celery-report-visual-plan.sh"
        ).read_text(encoding="utf-8")
        pool_runner_text = (
            BACKEND_DIR.parents[1]
            / "scripts"
            / "dev"
            / "run-celery-report-visual-plan-pool.sh"
        ).read_text(encoding="utf-8")
        pool_prepare_text = (
            BACKEND_DIR.parents[1]
            / "scripts"
            / "dev"
            / "prepare-report-visual-plan-gemini-profile-pool.sh"
        ).read_text(encoding="utf-8")
        monitor_text = (BACKEND_DIR / "apis" / "v1" / "celery_monitor.py").read_text(
            encoding="utf-8"
        )
        worker_text = (BACKEND_DIR / "services" / "gemini_browser.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("REPORT_VISUAL_PLAN_CELERY_CONCURRENCY:-1", runner_text)
        self.assertIn("REPORT_VISUAL_PLAN_WORKER_ID", runner_text)
        self.assertIn("REPORT_VISUAL_PLAN_PROFILE_ROOT", runner_text)
        self.assertIn('export REPORT_PDF_GEMINI_USER_DATA_DIR="$PROFILE_POOL_ROOT/$WORKER_ID"', runner_text)
        self.assertIn("--concurrency=${REPORT_VISUAL_PLAN_CELERY_CONCURRENCY:-1}", runner_text)
        self.assertIn("REPORT_VISUAL_PLAN_PROFILE_COUNT", pool_runner_text)
        self.assertIn("REPORT_VISUAL_PLAN_WORKER_ID=\"$slot\"", pool_runner_text)
        self.assertIn("REPORT_VISUAL_PLAN_TEMPLATE_USER_DATA_DIR", pool_prepare_text)
        self.assertIn("Default/Cache/***", pool_prepare_text)
        self.assertNotIn("report_pdf_gemini_pool/worker_4\"", runner_text)
        self.assertIn('"run_script": "scripts/dev/run-celery-report-visual-plan-pool.sh"', monitor_text)
        self.assertIn("options, \"total_timeout_seconds\"", worker_text)
        self.assertIn("* 3", worker_text)

    def test_storyboard_coverage_requires_exact_all_fragments_in_order(self):
        material = {"final_script_for_tts": "第一句。\n第二句。"}
        partial = (
            "分镜 1【文案原句】：第一句。【画面动作】：新建画面"
            "【AI 提示词】：one"
        )
        complete = (
            f"{partial}\n分镜 2【文案原句】：第二句。【画面动作】：新建画面"
            "【AI 提示词】：two"
        )
        self.assertFalse(service.storyboard_response_covers_script(material, partial))
        self.assertTrue(service.storyboard_response_covers_script(material, complete))

    def test_storyboard_coverage_rejects_source_fragment_without_prompt(self):
        material = {"final_script_for_tts": "第一句。第二句。"}
        missing_prompt = (
            "分镜 1【文案原句】：第一句。【画面动作】：新建画面"
            "【AI 提示词】：one\n分镜 2【文案原句】：第二句。【画面动作】：新建画面"
        )
        complete = f"{missing_prompt}【AI 提示词】：two"
        self.assertFalse(service.storyboard_response_covers_script(material, missing_prompt))
        self.assertTrue(service.storyboard_response_covers_script(material, complete))

    def test_storyboard_coverage_allows_title_prefix_and_repeated_tail(self):
        material = {"final_script_for_tts": "第一句。第二句。"}
        response = (
            "分镜 0【文案原句】：标题：示例研报【画面动作】：标题画面"
            "【AI 提示词】：title\n"
            "分镜 1【文案原句】：第一句。【画面动作】：场景一"
            "【AI 提示词】：one\n"
            "分镜 2【文案原句】：第二句。【画面动作】：场景二"
            "【AI 提示词】：two\n"
            "分镜 2【文案原句】：第二句。【画面动作】：重复场景二"
            "【AI 提示词】：two repeated"
        )

        self.assertTrue(service.storyboard_response_covers_script(material, response))

    def test_storyboard_coverage_stops_on_single_repairable_character_drift(self):
        material = {"final_script_for_tts": "第一句。" + ("完整原文。" * 80) + "第五点。"}
        response = (
            "分镜 0【文案原句】：标题：示例研报【画面动作】：标题画面"
            "【AI 提示词】：title\n"
            "分镜 1【文案原句】："
            + material["final_script_for_tts"].replace("第五点", "第5点")
            + "【画面动作】：正文画面【AI 提示词】：body"
        )

        self.assertTrue(service.storyboard_response_covers_script(material, response))

    def test_storyboard_coverage_stops_on_completed_marker_with_parseable_scenes(self):
        material = {"final_script_for_tts": "第一句。第二句。"}
        response = (
            "标题镜头【文案原句】：标题：示例研报【画面动作】：新建画面"
            "【视觉隐喻】：标题【AI 提示词】：title\n"
            "分镜 1【文案原句】：第一句。第二句被轻微改写。"
            "【画面观察】：新建画面【视觉隐喻】：正文"
            "【AI 提示词】：body\n【全文分镜完成】"
        )

        self.assertTrue(service.storyboard_response_covers_script(material, response))

    def test_gem_manager_redirect_clicks_requested_custom_gem(self):
        class Link:
            def count(self):
                return 1

            def scroll_into_view_if_needed(self, timeout):
                self.scroll_timeout = timeout

            def click(self, timeout):
                page.url = "https://gemini.google.com/gem/293c6e0735aa"

        class Locator:
            first = Link()

        class Page:
            url = "https://gemini.google.com/gems/view"

            def locator(self, selector):
                self.selector = selector
                return Locator()

            def wait_for_timeout(self, _timeout):
                return None

        page = Page()
        worker = GeminiBrowserWorker.__new__(GeminiBrowserWorker)
        worker.options = GeminiBrowserOptions(total_timeout_seconds=900)
        worker._recover_gem_manager_redirect(
            page,
            "https://gemini.google.com/gem/293c6e0735aa",
            time.monotonic(),
        )
        self.assertEqual(
            'a[href*="/gem/293c6e0735aa"]',
            page.selector,
        )

    def test_storyboard_reply_keeps_content_and_strips_gemini_footer(self):
        text = (
            "分镜 1 【文案原句】：测试文案 【AI 提示词】：test prompt "
            "Gemini 是一款 AI 工具，其回答未必正确无误。"
        )
        cleaned = _strip_gemini_page_suffix(text)
        worker = GeminiBrowserWorker.__new__(GeminiBrowserWorker)
        worker.options = GeminiBrowserOptions(min_response_chars=120)

        self.assertNotIn("Gemini 是一款", cleaned)
        self.assertTrue(worker._looks_like_response(cleaned, "long prompt"))

    def test_inline_storyboard_images_fill_prefix_and_leave_gaps_pending(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            storyboard_root = root / "storyboard" / "storyboard-job"
            storyboard_root.mkdir(parents=True)
            image_rows = []
            for ordinal in (1, 2):
                source = root / f"source-{ordinal}.png"
                Image.new("RGB", (640, 360), "white").save(source)
                image_rows.append({"ordinal": ordinal, "path": str(source)})
            (storyboard_root / "gemini_storyboard.json").write_text(
                json.dumps({"embedded_images": image_rows}),
                encoding="utf-8",
            )
            assets = [
                SimpleNamespace(
                    sequence=index,
                    image_status="pending",
                    image_path=None,
                    image_task_id=None,
                    image_attempt=0,
                )
                for index in range(3)
            ]
            with patch("tasks.report_visual_plan.OUTPUT_ROOT", root / "storyboard"), patch(
                "tasks.report_visual_plan.scene_image_path",
                side_effect=lambda _workflow, sequence: str(root / "workflow" / f"{sequence:03d}.png"),
            ), patch("tasks.report_visual_plan.db.session.commit") as commit:
                adopted = _adopt_storyboard_embedded_images(
                    "storyboard-job",
                    "workflow-job",
                    assets,
                )

            self.assertEqual(2, adopted)
            self.assertEqual(["completed", "completed", "pending"], [item.image_status for item in assets])
            self.assertTrue(Path(assets[0].image_path).is_file())
            self.assertTrue(assets[0].image_task_id.startswith("storyboard-gemini:"))
            commit.assert_called_once()

    def test_gemini_image_mode_supports_current_upload_and_tools_menu(self):
        browser_text = (BACKEND_DIR / "services" / "gemini_browser.py").read_text(encoding="utf-8")
        self.assertIn("button[aria-label*='上传和工具']", browser_text)
        self.assertIn("button[aria-label*='Upload and tools']", browser_text)
        self.assertIn("[role='menuitemcheckbox']:has-text('{label}')", browser_text)
        self.assertIn('"制作图片"', browser_text)
        self.assertIn("tool_button = self._wait_for_visible_enabled(", browser_text)


if __name__ == "__main__":
    unittest.main()
