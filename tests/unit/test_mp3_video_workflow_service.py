import base64
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from PIL import Image, ImageDraw
from sqlalchemy.exc import OperationalError

from api.mp3_video_workflow import (
    _bilibili_upload_delay_time as _api_bilibili_upload_delay_time,
    _bilibili_upload_season_id as _api_bilibili_upload_season_id,
    _charging_pay_from_upload_payload as _api_charging_pay_from_upload_payload,
    _clean_bilibili_upload_title as _api_clean_bilibili_upload_title,
    _clean_ds_title as _api_clean_ds_title,
    _default_bilibili_title as _api_default_bilibili_title,
    _ds_extract_payload as _api_ds_extract_payload,
    _prepare_job_for_video_regeneration as _api_prepare_job_for_video_regeneration,
    _apply_continue_audio_range_override as _api_apply_continue_audio_range_override,
    _prepare_keyframe_job_for_full_video as _api_prepare_keyframe_job_for_full_video,
)
from models.task import PublicationTask
from models.zsxq_sync import ZsxqTopic
from services.gemini_browser import (
    GeminiBrowserAutomationError,
    GeminiBrowserOptions,
    GeminiBrowserWorker,
    _clear_stale_singleton_files,
    _expects_pdf_evidence_contract,
    _has_complete_pdf_evidence_payload,
    _looks_like_page_shell_text,
    _looks_like_incomplete_response,
    _looks_like_singleton_launch_error,
    _promptless_submit_confirmed,
    _submit_state_summary,
    _upload_is_ready,
)
from services.bilibili.uploader import BiliUploader
from services.mp3_video_workflow import (
    DEFAULT_GEMINI_INITIAL_PROMPT,
    DEFAULT_SPEED,
    DEFAULT_TRANSCRIPT_DEEPSEEK_PROMPT,
    PLANNER_VERSION,
    ChildTaskStillRunning,
    Mp3VideoWorkflowError,
    VISUAL_ARTIFACT_EXPECTED_SIZES,
    VISUAL_ARTIFACT_KEYS,
    VISUAL_ARTIFACT_VERSION,
    _apply_workflow_audio_range_override,
    _build_transcript_user_prompt,
    _active_workflow_state,
    _child_inline_wait_seconds,
    _child_id_from_result,
    _cover_browser_launch_kwargs,
    _db_retry,
    _direct_ds_numbered_sections,
    _emit,
    _finance_cover_highlight_text,
    _generate_from_transcript,
    _infographic_bullets,
    _is_retryable_gemini_error,
    _local_plan_from_direct_ds_response,
    _keyframe_artifacts_ready,
    _parent_max_defers,
    _parent_retry_delay_seconds,
    _queue_notification,
    _replace_cached_workflow_result,
    _resume_waiting_child_lightweight,
    _fresh_result,
    _publisher_child_generate_subtitles,
    _publisher_result_summary,
    _recover_completed_publisher_child_from_disk,
    _publisher_video_file_is_stable,
    _publisher_video_title,
    _reusable_video_summary,
    _topic_content_preview_projection,
    _video_summary_ready,
    _video_file_ready,
    _visual_artifacts_ready,
    _wait_child,
    _without_waiting_state,
    _workflow_result_scope,
    _write_data_url,
    _workflow_generate_subtitles,
    _workflow_force_video_regenerate_at,
    _workflow_render_mode,
    audio_segment_from_plan,
    draw_audio_background,
    draw_closed_door_cover,
    draw_finance_cover,
    draw_transcript_infographic,
    deepseek_transcript_response,
    extract_closed_door_video_titles,
    extract_audio_range,
    extract_title_candidates,
    fallback_plan,
    generate_video_keyframes,
    gemini_options_from_env,
    initial_gemini_prompt,
    initial_transcript_prompt,
    plan_from_direct_ds_response,
    planner_model,
    transcript_model,
    validate_plan,
    _validate_strict_ds_plan,
    _validate_ds_response_contract,
)


class Mp3CoverBrowserLaunchTests(unittest.TestCase):
    @patch.dict(os.environ, {"MP3_VIDEO_COVER_BROWSER_HEADLESS": "1"}, clear=False)
    def test_current_chrome_uses_new_headless_mode(self):
        kwargs = _cover_browser_launch_kwargs()

        self.assertFalse(kwargs["headless"])
        self.assertIn("--headless=new", kwargs["args"])


class Mp3VideoWorkflowServiceTest(unittest.TestCase):
    def test_audio_asset_topic_preview_projection_is_bounded(self):
        projection = _topic_content_preview_projection()
        sql = str(projection.compile(compile_kwargs={"literal_binds": True}))

        self.assertIn("substr(zsxq_topics.content, 1, 720)", sql)
        self.assertNotEqual(projection.element, ZsxqTopic.content)

    def test_strict_pro_request_stops_before_submission_when_model_is_unconfirmed(self):
        worker = object.__new__(GeminiBrowserWorker)
        worker.options = GeminiBrowserOptions(
            user_data_dir="/tmp/gemini-test",
            require_pro_model=True,
        )

        with self.assertRaisesRegex(GeminiBrowserAutomationError, "未能确认 Gemini Pro 模型"):
            worker._ensure_required_pro_model({"status": "picker_not_found", "confirmed": False})

        worker._ensure_required_pro_model({"status": "selected_pro", "confirmed": True})

    def test_gemini_login_selectors_target_controls_not_prompt_text(self):
        worker = object.__new__(GeminiBrowserWorker)
        worker.options = GeminiBrowserOptions(user_data_dir="/tmp/gemini-test")

        selectors = worker._selectors("login")

        self.assertNotIn("text=登录", selectors)
        self.assertNotIn("text=Sign in", selectors)
        self.assertIn("button:has-text('登录')", selectors)
        self.assertIn("a:has-text('Sign in')", selectors)

    def test_gemini_first_visible_skips_hidden_duplicate_login_control(self):
        worker = object.__new__(GeminiBrowserWorker)
        worker.options = GeminiBrowserOptions(user_data_dir="/tmp/gemini-test")
        hidden = MagicMock()
        hidden.is_visible.return_value = False
        visible = MagicMock()
        visible.is_visible.return_value = True
        locator = MagicMock()
        locator.count.return_value = 2
        locator.nth.side_effect = [hidden, visible]
        page = MagicMock()
        page.locator.return_value = locator

        result = worker._first_visible(page, ["button:has-text('登录')"])

        self.assertIs(result, visible)

    def test_disk_recovery_uses_atomic_json_patch_on_postgresql(self):
        video_path = Path("/tmp/recovered-child.mp4")
        child = SimpleNamespace(
            id="child-recovered",
            feature="publisher",
            status="processing",
            publisher_generate_subtitles=False,
            publisher_aspect_ratio="9:16",
        )
        recovered = SimpleNamespace(id=child.id, status="completed")
        bind = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
        execute_result = SimpleNamespace(rowcount=1)

        with (
            patch("services.mp3_video_workflow._publisher_child_disk_video_path", return_value=video_path),
            patch("services.mp3_video_workflow.db.session.get_bind", return_value=bind),
            patch("services.mp3_video_workflow.db.session.execute", return_value=execute_result) as execute,
            patch("services.mp3_video_workflow.db.session.commit") as commit,
            patch("services.mp3_video_workflow._db_retry", side_effect=lambda operation, _label: operation()),
            patch("services.mp3_video_workflow._load_task", return_value=recovered) as load_task,
        ):
            result = _recover_completed_publisher_child_from_disk(child)

        self.assertIs(result, recovered)
        sql = str(execute.call_args.args[0])
        self.assertIn("COALESCE(result, '{}'::jsonb) || CAST(:result_patch AS jsonb)", sql)
        self.assertIn("status NOT IN ('completed', 'failed', 'canceled')", sql)
        params = execute.call_args.args[1]
        self.assertIn('"vertical_video_path": "/tmp/recovered-child.mp4"', params["result_patch"])
        commit.assert_called_once()
        self.assertNotIn(PublicationTask.params, load_task.call_args.args)
        self.assertNotIn(PublicationTask.result, load_task.call_args.args)

    def test_wait_continuation_uses_projected_state_and_carries_generation_token(self):
        parent = SimpleNamespace(
            status="processing",
            progress=80,
            waiting_child_label="landscape_video",
            waiting_child_id="child-1",
            waiting_child_updated_at="2026-07-18T04:00:00Z",
        )
        child = SimpleNamespace(
            id="child-1",
            status="pending",
            current_step="queued",
        )
        hint = {
            "label": "landscape_video",
            "child_id": "child-1",
            "defer_count": 3,
            "updated_at": "2026-07-18T04:00:00Z",
        }

        with (
            patch("services.mp3_video_workflow._load_task", side_effect=[parent, child]) as load_task,
            patch("services.mp3_video_workflow._publisher_child_disk_video_path", return_value=None),
            patch("services.mp3_video_workflow._publisher_child_repair_due", return_value=False),
            patch("services.mp3_video_workflow._celery_failure_message", return_value=None),
            patch("services.mp3_video_workflow._db_retry", return_value=1),
            patch("services.mp3_video_workflow.celery_app.send_task") as send_task,
        ):
            result = _resume_waiting_child_lightweight("parent-1", hint)

        self.assertTrue(result)
        self.assertEqual(2, load_task.call_count)
        for call in load_task.call_args_list:
            self.assertNotIn(PublicationTask.params, call.args)
            self.assertNotIn(PublicationTask.result, call.args)
        queued_hint = send_task.call_args.kwargs["args"][1]
        self.assertEqual(4, queued_hint["defer_count"])
        self.assertNotEqual(hint["updated_at"], queued_hint["updated_at"])

    def test_wait_continuation_releases_db_before_disk_and_celery_probes(self):
        parent = SimpleNamespace(
            status="processing",
            progress=80,
            waiting_child_label="landscape_video",
            waiting_child_id="child-1",
            waiting_child_updated_at="2026-08-09T02:00:00Z",
        )
        child = SimpleNamespace(
            id="child-1",
            celery_task_id="celery-child-1",
            status="processing",
            current_step="rendering",
            progress=60,
            error_message=None,
            feature="publisher",
            task_video_path=None,
            publisher_aspect_ratio="16:9",
            publisher_generate_subtitles=True,
            publisher_audio_signature=None,
            publisher_vertical_video_path=None,
            publisher_vertical_cover_path=None,
            publisher_podcast_cover_path=None,
            publisher_bilibili_cover_path=None,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
            completed_at=None,
            params={},
            result={},
        )
        hint = {
            "label": "landscape_video",
            "child_id": "child-1",
            "updated_at": "2026-08-09T02:00:00Z",
        }
        released = {"count": 0}

        def release():
            released["count"] += 1

        def disk_probe(_child):
            self.assertGreaterEqual(released["count"], 1)
            return None

        def celery_probe(_child):
            self.assertGreaterEqual(released["count"], 2)
            return None

        with (
            patch("services.mp3_video_workflow._load_task", side_effect=[parent, child]),
            patch("services.mp3_video_workflow.release_db_session_quietly", side_effect=release),
            patch("services.mp3_video_workflow._publisher_child_disk_video_path", side_effect=disk_probe),
            patch("services.mp3_video_workflow._publisher_child_repair_due", return_value=False),
            patch("services.mp3_video_workflow._celery_failure_message", side_effect=celery_probe),
            patch("services.mp3_video_workflow._db_retry", return_value=1),
            patch("services.mp3_video_workflow.celery_app.send_task"),
        ):
            result = _resume_waiting_child_lightweight("parent-1", hint)

        self.assertTrue(result)

    def test_wait_child_releases_db_before_poll_sleep(self):
        parent = SimpleNamespace(id="parent-1", status="processing")
        child = SimpleNamespace(
            id="child-1",
            celery_task_id=None,
            status="processing",
            current_step="rendering",
            progress=60,
            error_message=None,
            feature="publisher",
            task_video_path=None,
            publisher_aspect_ratio="16:9",
            publisher_generate_subtitles=True,
            publisher_audio_signature=None,
            publisher_vertical_video_path=None,
            publisher_vertical_cover_path=None,
            publisher_podcast_cover_path=None,
            publisher_bilibili_cover_path=None,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
            completed_at=None,
            params={},
            result={},
        )
        released = {"count": 0}

        def release():
            released["count"] += 1

        def sleep(_seconds):
            self.assertGreaterEqual(released["count"], 2)

        with (
            patch("services.mp3_video_workflow._child_inline_wait_seconds", return_value=1),
            patch("services.mp3_video_workflow.time.monotonic", side_effect=[0, 0, 0, 2]),
            patch("services.mp3_video_workflow._load_task", side_effect=[parent, child, child]),
            patch("services.mp3_video_workflow.release_db_session_quietly", side_effect=release),
            patch("services.mp3_video_workflow._publisher_child_disk_video_path", return_value=None),
            patch("services.mp3_video_workflow._publisher_child_repair_due", return_value=False),
            patch("services.mp3_video_workflow._emit"),
            patch("services.mp3_video_workflow._record_child"),
            patch("services.mp3_video_workflow.time.sleep", side_effect=sleep),
        ):
            with self.assertRaises(ChildTaskStillRunning):
                _wait_child(
                    parent.id,
                    child.id,
                    "landscape_video",
                    start_progress=80,
                    end_progress=90,
                )


    def test_stale_wait_continuation_is_a_noop(self):
        parent = SimpleNamespace(
            status="processing",
            progress=80,
            waiting_child_label="landscape_video",
            waiting_child_id="child-1",
            waiting_child_updated_at="new-token",
        )
        hint = {
            "label": "landscape_video",
            "child_id": "child-1",
            "defer_count": 3,
            "updated_at": "old-token",
        }

        with (
            patch("services.mp3_video_workflow._load_task", return_value=parent) as load_task,
            patch("services.mp3_video_workflow._db_retry") as db_retry,
            patch("services.mp3_video_workflow.celery_app.send_task") as send_task,
        ):
            result = _resume_waiting_child_lightweight("parent-1", hint)

        self.assertTrue(result)
        load_task.assert_called_once()
        db_retry.assert_not_called()
        send_task.assert_not_called()

    def test_wait_continuation_captures_parent_progress_before_child_repair_commit(self):
        expired = {"value": False}

        class Parent:
            status = "processing"
            waiting_child_label = "landscape_video"
            waiting_child_id = "child-1"
            waiting_child_updated_at = "2026-07-18T04:00:00Z"

            @property
            def progress(self):
                if expired["value"]:
                    raise AssertionError("expired parent ORM entity was accessed")
                return 81

        child = SimpleNamespace(
            id="child-1",
            status="pending",
            current_step="queued",
        )
        hint = {
            "label": "landscape_video",
            "child_id": "child-1",
            "defer_count": 3,
            "updated_at": "2026-07-18T04:00:00Z",
        }

        def requeue(full_child, log=None):
            expired["value"] = True
            return full_child, True

        with (
            patch(
                "services.mp3_video_workflow._load_task",
                side_effect=[Parent(), child, child, child],
            ),
            patch("services.mp3_video_workflow._publisher_child_disk_video_path", return_value=None),
            patch("services.mp3_video_workflow._publisher_child_repair_due", return_value=True),
            patch("services.mp3_video_workflow.maybe_requeue_stale_publisher_child", side_effect=requeue),
            patch("services.mp3_video_workflow._celery_failure_message", return_value=None),
            patch("services.mp3_video_workflow._db_retry", return_value=1),
            patch("services.mp3_video_workflow.celery_app.send_task") as send_task,
        ):
            result = _resume_waiting_child_lightweight("parent-1", hint)

        self.assertTrue(result)
        self.assertEqual(send_task.call_args.kwargs["args"][1]["defer_count"], 4)

    def test_wait_continuation_retry_does_not_dereference_detached_child(self):
        class Child:
            id = "child-1"
            status = "processing"
            current_step = "synthesis"

        parent = SimpleNamespace(
            status="processing",
            progress=80,
            waiting_child_label="landscape_video",
            waiting_child_id="child-1",
            waiting_child_updated_at="2026-07-20T01:29:59Z",
        )
        hint = {
            "label": "landscape_video",
            "child_id": "child-1",
            "defer_count": 9,
            "updated_at": "2026-07-20T01:29:59Z",
        }

        def assert_retry_closure_uses_only_scalar_snapshots(operation, _label):
            self.assertNotIn("child", operation.__code__.co_freevars)
            self.assertIn("child_step", operation.__code__.co_freevars)
            return 1

        with (
            patch("services.mp3_video_workflow._load_task", side_effect=[parent, Child()]),
            patch("services.mp3_video_workflow._publisher_child_disk_video_path", return_value=None),
            patch("services.mp3_video_workflow._publisher_child_repair_due", return_value=False),
            patch("services.mp3_video_workflow._celery_failure_message", return_value=None),
            patch(
                "services.mp3_video_workflow._db_retry",
                side_effect=assert_retry_closure_uses_only_scalar_snapshots,
            ),
            patch("services.mp3_video_workflow.celery_app.send_task") as send_task,
        ):
            result = _resume_waiting_child_lightweight("parent-1", hint)

        self.assertTrue(result)
        send_task.assert_called_once()

    def test_wait_child_pending_deadline_does_not_load_json_payload(self):
        child = PublicationTask(
            id="child-still-running",
            status="processing",
            current_step="rendering",
            progress=50,
        )

        with (
            patch("services.mp3_video_workflow._child_inline_wait_seconds", return_value=1),
            patch("services.mp3_video_workflow.time.monotonic", side_effect=[0, 2]),
            patch("services.mp3_video_workflow._load_task", return_value=child) as load_task,
            patch("services.mp3_video_workflow._record_child"),
        ):
            with self.assertRaises(ChildTaskStillRunning):
                _wait_child(
                    "parent-1",
                    child.id,
                    "landscape_video",
                    start_progress=80,
                    end_progress=90,
                )

        load_task.assert_called_once()
        loaded_columns = load_task.call_args.args[1:]
        self.assertNotIn(PublicationTask.params, loaded_columns)
        self.assertNotIn(PublicationTask.result, loaded_columns)

    def test_wait_child_accepts_completion_at_inline_deadline(self):
        child = PublicationTask(
            id="child-at-deadline",
            status="completed",
            current_step="done",
            progress=100,
        )

        with (
            patch("services.mp3_video_workflow._child_inline_wait_seconds", return_value=1),
            patch("services.mp3_video_workflow.time.monotonic", side_effect=[0, 2]),
            patch("services.mp3_video_workflow._load_task", return_value=child),
            patch("services.mp3_video_workflow._clear_waiting_child") as clear_waiting,
        ):
            result = _wait_child(
                "parent-1",
                child.id,
                "landscape_video",
                start_progress=80,
                end_progress=90,
            )

        self.assertIs(result, child)
        clear_waiting.assert_called_once_with("parent-1", "landscape_video")

    def test_workflow_result_scope_reuses_loaded_state_without_db_read(self):
        @_workflow_result_scope
        def exercise(task_id):
            _replace_cached_workflow_result(task_id, {"plan": {"title": "cached"}})
            with patch("services.mp3_video_workflow._load_task") as load_task:
                result = _fresh_result(task_id)
            load_task.assert_not_called()
            return result

        self.assertEqual({"plan": {"title": "cached"}}, exercise("job-cached"))

    def test_emit_buffers_progress_payload_without_writing_result_json(self):
        @_workflow_result_scope
        def exercise(task_id):
            _replace_cached_workflow_result(task_id, {"logs": []})
            with patch("services.mp3_video_workflow._db_retry", return_value=1):
                _emit(
                    task_id,
                    "gemini_upload",
                    18,
                    "上传完成",
                    result={"upload": {"file_id": "file-1"}},
                )
            return _fresh_result(task_id)

        result = exercise("job-progress-buffer")

        self.assertEqual("file-1", result["upload"]["file_id"])
        self.assertEqual("gemini_upload", result["logs"][-1]["step"])
        self.assertEqual(18, result["logs"][-1]["progress"])

    def test_notification_uses_cached_payload_and_keeps_audit_state(self):
        @_workflow_result_scope
        def exercise(task_id):
            state = _active_workflow_state.get()
            state["params"] = {
                "feature": "mp3_video_workflow",
                "mp3_video_workflow": {"metadata": {"source": "manual"}},
            }
            _replace_cached_workflow_result(task_id, {
                "logs": [],
                "audio": {"filename": "demo.mp3"},
                "notifications": {},
            })
            task = SimpleNamespace(
                id=task_id,
                status="processing",
                current_step="gemini_upload",
                error_message=None,
                created_at=None,
                completed_at=None,
            )
            with (
                patch("services.mp3_video_workflow._load_task", return_value=task) as load_task,
                patch(
                    "services.mp3_video_workflow.workflow_notification_service.enqueue_workflow_event",
                    return_value={"queued": True, "celery_task_id": "notify-1"},
                ),
                patch("services.mp3_video_workflow._db_retry", return_value=1),
            ):
                queued = _queue_notification(task_id, "mp3_video_started", "开始")
            return queued, load_task.call_args, _fresh_result(task_id)

        queued, load_call, result = exercise("job-notification-cache")

        self.assertTrue(queued)
        loaded_column_keys = [column.key for column in load_call.args[1:]]
        self.assertNotIn("params", loaded_column_keys)
        self.assertNotIn("result", loaded_column_keys)
        notification = result["notifications"]["mp3_video_started"]
        self.assertTrue(notification["queued"])
        self.assertEqual("notify-1", notification["celery_task_id"])

    def test_api_bilibili_upload_parses_schedule_and_season(self):
        with patch("api.mp3_video_workflow.time.time", return_value=1000):
            self.assertEqual(
                _api_bilibili_upload_delay_time({"schedule_enabled": 1, "delay_time": "1600"}),
                1600,
            )
            self.assertIsNone(_api_bilibili_upload_delay_time({"schedule_enabled": 0}))
            with self.assertRaises(ValueError):
                _api_bilibili_upload_delay_time({"schedule_enabled": 1, "delay_time": "1200"})

        self.assertEqual(_api_bilibili_upload_season_id({"season_id": "12345"}), 12345)
        self.assertIsNone(_api_bilibili_upload_season_id({"season_id": ""}))
        with self.assertRaises(ValueError):
            _api_bilibili_upload_season_id({"season_id": "not-a-number"})

    def test_api_closed_door_title_ignores_prompt_placeholder(self):
        actual = "内资闭门会 / Micro-TEC光模块核心器件 / 国产突破内挤压工艺 / 出口管制误解消除 / 2026-07-07"
        dirty = (
            "尝试优化下标题，使用 内资闭门会 / xxxx / xxxx / xxxx / 当日日期) 这样的格式来写 "
            f"{actual}"
        )

        self.assertEqual(_api_clean_ds_title(dirty, prefer_closed_door=True), actual)
        self.assertEqual(_api_clean_bilibili_upload_title(dirty), actual)

    def test_api_default_bilibili_title_cleans_plan_fallback(self):
        actual = "内资闭门会 / Micro-TEC光模块核心器件 / 国产突破内挤压工艺 / 出口管制误解消除 / 2026-07-07"
        result = {
            "plan": {
                "xiaojunjun_video_title": (
                    "内资闭门会 / xxxx / xxxx / xxxx / 当日日期) "
                    f"{actual}"
                )
            }
        }

        self.assertEqual(_api_default_bilibili_title(result, {}, "landscape"), actual)

    def test_api_ds_extract_handles_bracketed_clip_and_quoted_title_words(self):
        response = (
            "1. 标题版本一（信息密度高、点击率强）\n"
            "东北电子内部闭门会深度解读：华为“涛”=13年前的新能源车\n\n"
            "2. 标题版本二（另一种视角）\n"
            "信息差：华为“涛”不是技术概念，而是中国半导体产业的“宁德时代时刻”！未来10年结构性投资机会\n\n"
            "4. 最精华音频片段\n"
            "开始时间戳： [00:02:36.520]\n"
            "结束时间戳： [00:36:41.040]\n"
        )

        payload = _api_ds_extract_payload({
            "source": {"response_text": response},
        })

        self.assertIn("东北电子内部闭门会深度解读", payload["title_1"])
        self.assertIn("华为", payload["title_2"])
        self.assertIn("未来10年结构性投资机会", payload["title_2"])
        self.assertNotEqual(payload["title_2"], "宁德时代时刻")
        self.assertEqual(payload["clip"]["start_text"], "02:36")
        self.assertEqual(payload["clip"]["end_text"], "36:41")

    def test_api_ds_extract_falls_back_to_ds_plan(self):
        payload = _api_ds_extract_payload({
            "source": {"response_text": "非标准输出，前端不应该靠这段硬猜。"},
            "plan": {
                "title": "特斯拉机器人供应链信息差",
                "title_options": [
                    "中信内部闭门会深度爆料：Optimus V3量产时间线铁定不延",
                    "内资忽视的港股低估标的空间测算曝光",
                ],
                "audio": {"start_seconds": 32, "end_seconds": 1532, "speed": 1.2},
                "xiaojunjun_video_title": "内资闭门会 / 特斯拉Optimus量产 / 恒帅电子皮肤新定点 / 港股低估标的 / 2026-07-07",
                "ciketc_video_title": "内资闭门会 | Optimus V3量产不延 | 核心供应链定点解析 | 港股低估空间测算 | 2026-07-07",
            },
        })

        self.assertEqual(payload["title_1"], "中信内部闭门会深度爆料：Optimus V3量产时间线铁定不延")
        self.assertEqual(payload["title_2"], "内资忽视的港股低估标的空间测算曝光")
        self.assertEqual(payload["clip"]["start_text"], "00:32")
        self.assertEqual(payload["clip"]["end_text"], "25:32")
        self.assertIn("内资闭门会 / 特斯拉Optimus量产", payload["title_6"])
        self.assertIn("内资闭门会 | Optimus V3量产不延", payload["title_7"])

    def test_api_ds_extract_prefers_authoritative_plan_over_raw_heading_noise(self):
        raw_title_2 = "AI硬件需求远未结束：存储、光、能源电力成确定性方向"
        raw_title_7 = "内资闭门会 | AI赚钱效应已现：云厂订单保障收入 / 2026-08-19"
        plan_title_7 = "内资闭门会 | AI赚钱效应已现：云厂订单保障收入 | 2026-08-19"
        payload = _api_ds_extract_payload({
            "source": {
                "response_text": (
                    "1. 标题（主版本，含具体对象与投资影响）\nAI商业化拐点确认\n"
                    f"2. 不同角度标题版本（换一个核心变量）\n{raw_title_2}\n"
                    "4. 最精华片段\n起点：00:03:57.140\n终点：00:52:33.540\n"
                    f"7. 标题优化另一版本（内资闭门会 | 格式）\n{raw_title_7}"
                )
            },
            "plan": {
                "title_options": ["AI商业化拐点确认", raw_title_2],
                "audio": {"start_seconds": 237.14, "end_seconds": 3153.54},
                "ciketc_video_title": plan_title_7,
            },
        })

        self.assertEqual(payload["title_2"], raw_title_2)
        self.assertEqual(payload["title_7"], plan_title_7)
        self.assertNotIn("格式）", payload["title_7"])

    def test_api_ds_extract_ignores_date_as_clip_pair(self):
        payload = _api_ds_extract_payload({
            "source": {"response_text": "6. 内资闭门会 / 特斯拉V3量产确认 / 2026-07-07"},
            "plan": {
                "audio": {"start_seconds": 32, "end_seconds": 1532, "speed": 1.2},
            },
        })

        self.assertEqual(payload["clip"]["start_text"], "00:32")
        self.assertEqual(payload["clip"]["end_text"], "25:32")

    def test_api_bilibili_charging_payload_uses_preview_and_level(self):
        payload = _api_charging_pay_from_upload_payload({
            "charging_pay": {
                "enabled": True,
                "preview_end_time": "00:03:00",
                "upower_level_id": "level-1",
                "upower_unit_price": "68",
            }
        })

        self.assertEqual(payload["preview"]["start_time"], 0)
        self.assertEqual(payload["preview"]["end_time"], 180)
        self.assertEqual(payload["preview_end_time"], 180)
        self.assertEqual(payload["upower_level_id"], "level-1")
        self.assertEqual(payload["upower_unit_price"], 68)

    def test_bilibili_charging_options_parse_upower_levels(self):
        uploader = BiliUploader(sessdata="sess", bili_jct="csrf")
        info = {
            "upower_auth": 1,
            "upower_level_list": [
                {
                    "level_id": "30",
                    "publish_info": {
                        "level_title_str": "30元档 金牌富豪",
                        "upower_unit_price": 30,
                    },
                },
                {
                    "upower_level_id": "68",
                    "level_title_str": "68元档 顶流富豪",
                    "upower_unit_price": "68",
                },
            ],
        }

        with patch.object(BiliUploader, "_fetch_charging_pay_info", return_value=info):
            result = uploader.charging_pay_options()

        self.assertEqual(result["default_level_id"], "30")
        self.assertEqual(result["levels"][0]["label"], "30元档 金牌富豪")
        self.assertEqual(result["levels"][0]["upower_unit_price"], 30)
        self.assertEqual(result["levels"][1]["id"], "68")

    def test_api_bilibili_charging_payload_rejects_invalid_time_range(self):
        with self.assertRaisesRegex(ValueError, "充电试看结束时间"):
            _api_charging_pay_from_upload_payload({
                "charging_pay": {
                    "enabled": True,
                    "preview": {"start_time": 30, "end_time": 20},
                }
            })

    def test_direct_ds_response_without_timestamp_fails_before_visuals(self):
        response = (
            "1. 投资视角标题\n"
            "Micro-TEC核心器件国产替代进入关键阶段\n"
            "2. 投资视角标题版本二\n"
            "国产突破内挤压工艺，出口管制误解消除\n"
            "4. 最精华片段\n"
            "未识别到时间戳\n"
            "6. 优化标题\n"
            "内资闭门会 / Micro-TEC光模块核心器件 / 国产突破内挤压工艺 / 出口管制误解消除 / 2026-07-07\n"
            "7. 优化标题\n"
            "内资闭门会 | Micro-TEC关键材料用量极小 | 复兴科技良品率追赶日系 | 1.6T送样预计50元单价 | 2026-07-07"
        )

        with patch("services.mp3_video_workflow.deepseek_plan", return_value={}):
            with self.assertRaisesRegex(Mp3VideoWorkflowError, "时间戳"):
                plan_from_direct_ds_response(response, audio={"filename": "demo.mp3", "duration_seconds": 3600})

    def test_direct_ds_response_with_required_sections_builds_plan(self):
        response = (
            "1. 投资视角标题（版本一）\n"
            "专家密讲：Micro-TEC光模块核心器件国产替代关键时刻，复兴科技订单超预期\n"
            "2. 投资视角标题（版本二）\n"
            "独家：光模块升级带动Micro-TEC需求井喷，真正稀缺的是内挤压工艺\n"
            "3. 精简短标题\n"
            "Micro-TEC国产替代加速，复兴科技订单超预期\n"
            "4. 最精华片段\n"
            "开始时间戳：00:14:27.900\n"
            "结束时间戳：00:39:27.900\n"
            "6. 优化标题\n"
            "内资闭门会 / Micro-TEC光模块核心器件 / 国产突破内挤压工艺 / 出口管制误解消除 / 2026-07-07\n"
            "7. 优化标题\n"
            "内资闭门会 | Micro-TEC关键材料用量极小 | 复兴科技良品率追赶日系 | 1.6T送样预计50元单价 | 2026-07-07"
        )

        with patch("services.mp3_video_workflow.deepseek_plan", return_value={}):
            plan = plan_from_direct_ds_response(response, audio={"filename": "demo.mp3", "duration_seconds": 4260})
        _validate_strict_ds_plan(plan)

        self.assertEqual(plan["audio"]["start_seconds"], 867.9)
        self.assertEqual(plan["audio"]["end_seconds"], 2367.9)
        self.assertEqual(
            plan["xiaojunjun_video_title"],
            "内资闭门会 / Micro-TEC光模块核心器件 / 国产突破内挤压工艺 / 出口管制误解消除 / 2026-07-07",
        )
        self.assertEqual(
            plan["ciketc_video_title"],
            "内资闭门会 | Micro-TEC关键材料用量极小 | 复兴科技良品率追赶日系 | 1.6T送样预计50元单价 | 2026-07-07",
        )

    def test_markdown_numbered_sections_ignore_numbered_gold_quotes(self):
        response = """
### 1. 投资者视角标题（版本一）
**AI资本开支中光通信占比将从5%升至15%**

### 2｜投资者视角标题（版本二）
**ScaleUp光连接从0到1放量**

### 3. 精简短标题
**光模块高增长刚开始**

### 4｜最精华片段推荐
**开始时间：05:43**
**结束时间：20:35**
片段内部关键钩子位置：
- 10:51-11:27：光进铜退
- 13:02-13:50：ScaleUp放量

### 5｜金句提炼
1. **第一条金句**
2. **第二条金句**

### 6｜优化标题（内资闭门会格式）
**内资闭门会 / 光通信占比5%到15% / ScaleUp从0到1 / 龙头高增长 / 2026-08-01**

### 7｜另一个版本（内资闭门会竖线格式）
**内资闭门会 | 光模块认知差 | 规格持续升级 | 龙头最受益 | 2026-08-01**
"""

        sections = _direct_ds_numbered_sections(response)
        titles = extract_closed_door_video_titles(response)
        plan = _local_plan_from_direct_ds_response(response, audio={"duration_seconds": 1275.0})
        _validate_ds_response_contract(response, source_label="IMA 全文 DS 返回值")
        _validate_strict_ds_plan(plan, source_label="IMA 全文 DS 返回值")

        self.assertEqual(list(sections), ["1", "2", "3", "4", "5", "6", "7"])
        self.assertNotIn("第一条金句", sections["1"])
        self.assertEqual(extract_audio_range(sections["4"]), (343.0, 1235.0))
        self.assertEqual(plan["audio"]["start_seconds"], 343.0)
        self.assertEqual(plan["audio"]["end_seconds"], 1235.0)
        self.assertEqual(plan["xiaojunjun_video_title"], titles["xiaojunjun_video_title"])
        self.assertEqual(plan["ciketc_video_title"], titles["ciketc_video_title"])
        self.assertIn("光通信占比5%到15%", titles["xiaojunjun_video_title"])
        self.assertIn("光模块认知差", titles["ciketc_video_title"])

    def test_direct_ds_response_skips_standalone_section_headings(self):
        response = (
            "1. 标题版本一（信息密度高、点击率强）\n"
            "东北电子内部闭门会深度解读：华为涛等于13年前的新能源车\n\n"
            "2. 标题版本二（另一种视角）\n"
            "信息差：华为涛不是技术概念，而是中国半导体产业的宁德时代时刻\n\n"
            "3. 短标题版本\n"
            "华为涛完整版：20分钟搞懂中国半导体投资图谱\n\n"
            "4. 最精华音频片段\n"
            "开始时间戳： [00:02:36.520]\n"
            "结束时间戳： [00:36:41.040]\n\n"
            "6. 优化标题格式 (版本A)\n"
            "内资闭门会 / 华为涛改写半导体规则 / 3片7nm堆叠打1片3nm / 宁德时代时刻 / 2026-07-07\n\n"
            "7. 优化标题格式 (版本B)\n"
            "内资闭门会 | 华为涛哲学 | 产业链话语权之战 | 超级通胀赛道 | 2026-07-07"
        )

        with patch("services.mp3_video_workflow.deepseek_plan", return_value={}):
            plan = plan_from_direct_ds_response(response, audio={"filename": "demo.mp3", "duration_seconds": 2588})
        _validate_strict_ds_plan(plan)

        self.assertIn("华为涛不是技术概念", plan["title"])
        self.assertNotIn("标题版本", plan["title"])
        self.assertEqual(plan["audio"]["start_seconds"], 156.52)
        self.assertEqual(plan["audio"]["end_seconds"], 2201.04)
        self.assertEqual(
            plan["xiaojunjun_video_title"],
            "内资闭门会 / 华为涛改写半导体规则 / 3片7nm堆叠打1片3nm / 宁德时代时刻 / 2026-07-07",
        )
        self.assertEqual(
            plan["ciketc_video_title"],
            "内资闭门会 | 华为涛哲学 | 产业链话语权之战 | 超级通胀赛道 | 2026-07-07",
        )

    def test_extract_audio_range_accepts_origin_destination_labels(self):
        response = (
            "4. 最精华音频片段\n"
            "起点：00:03:57.140（讨论AI商业化）\n"
            "终点：00:52:33.540（总结投资方向）"
        )

        self.assertEqual(extract_audio_range(response), (237.14, 3153.54))

    def test_direct_ds_response_skips_descriptive_title_headings(self):
        response = (
            "1. 标题（主版本，单一主重点，含具体对象、最新事实/预期差、投资影响）\n"
            "AI商业化拐点确认：云厂订单与收入高增，企业级AI需求持续\n"
            "2. 不同角度标题版本（换一个核心变量，仍以二级市场最关心变量为锚）\n"
            "AI赚钱效应已现：云厂在手订单保障未来收入\n"
            "4. 最精华片段\n"
            "开始时间戳：00:03:57.140\n"
            "结束时间戳：00:52:33.540\n"
            "6. 标题优化（内资闭门会 / 格式）\n"
            "内资闭门会 / AI商业化拐点确认 / 云厂订单高增 / 企业级AI需求持续 / 2026-08-19\n"
            "7. 标题优化另一版本（内资闭门会 | 格式）\n"
            "内资闭门会 | AI赚钱效应已现：云厂订单保障收入，能源电力成下一棋眼 / 2026-08-19"
        )

        with patch("services.mp3_video_workflow.deepseek_plan", return_value={}):
            plan = plan_from_direct_ds_response(response, audio={"filename": "demo.mp3", "duration_seconds": 3504})
        _validate_strict_ds_plan(plan)

        self.assertEqual(plan["title"], "AI赚钱效应已现：云厂在手订单保障未来收入")
        self.assertNotIn("标题版本", plan["title"])
        self.assertEqual(
            plan["xiaojunjun_video_title"],
            "内资闭门会 / AI商业化拐点确认 / 云厂订单高增 / 企业级AI需求持续 / 2026-08-19",
        )
        self.assertEqual(
            plan["ciketc_video_title"],
            "内资闭门会 | AI赚钱效应已现：云厂订单保障收入，能源电力成下一棋眼 | 2026-08-19",
        )

    def test_direct_ds_response_maps_unnumbered_descriptive_headings(self):
        response = (
            "标题（主版本，单一主重点，含具体对象、最新事实/预期差、投资影响）\n"
            "AI算力爆发驱动MPO/FAU量价齐升：光模块三倍增长拉动MPO需求翻倍\n"
            "（说明：聚焦MPO/FAU需求爆发叠加小型化涨价。）\n\n"
            "不同角度标题版本（换一个核心变量，仍以二级市场最关心变量为锚）\n"
            "AI集群化推动FAU价值跃升：36芯DFU单价提升10倍\n"
            "（说明：从FAU场景切换和单价提升角度切入。）\n\n"
            "精简短标题（保留最有辨识度的对象和核心变化）\n"
            "MPO/FAU需求爆发叠加涨价\n\n"
            "黄金片段（最精华连续区间）\n"
            "起点：00:01:12.880（开始讨论MPO和FAU）\n"
            "终点：00:12:48.860（以上是全部内容）\n"
            "说明：完整覆盖量升和价格提升逻辑。\n\n"
            "金句（代表性语句，非完整列表）\n"
            "MPO连接器的使用数量会大幅增加。\n\n"
            "标题优化（内资闭门会 / 格式）\n"
            "内资闭门会 / MPO需求翻倍 / 小型连接器涨价 / 三家标的量价弹性 / 2026-08-26\n\n"
            "标题优化另一版本（内资闭门会 | 格式）\n"
            "内资闭门会 | MPO量价齐升 | 光模块三倍增长 | 三家标的受益 | 2026-08-26"
        )

        with patch(
            "services.mp3_video_workflow.deepseek_plan",
            side_effect=Mp3VideoWorkflowError("DeepSeek 规划失败: HTTP 402"),
        ):
            plan = plan_from_direct_ds_response(
                response,
                audio={"filename": "demo.mp3", "duration_seconds": 827.446893},
            )

        self.assertEqual(
            plan["title"],
            "AI集群化推动FAU价值跃升：36芯DFU单价提升10倍",
        )
        self.assertEqual(plan["audio"]["start_seconds"], 72.88)
        self.assertEqual(plan["audio"]["end_seconds"], 768.86)
        self.assertEqual(
            plan["xiaojunjun_video_title"],
            "内资闭门会 / MPO需求翻倍 / 小型连接器涨价 / 三家标的量价弹性 / 2026-08-26",
        )
        self.assertEqual(
            plan["ciketc_video_title"],
            "内资闭门会 | MPO量价齐升 | 光模块三倍增长 | 三家标的受益 | 2026-08-26",
        )

    def test_direct_ds_response_prefers_deepseek_structured_plan(self):
        response = (
            "1. 标题版本一\n"
            "本地标题不应优先\n"
            "2. 标题版本二\n"
            "本地二号标题\n"
            "4. 最精华音频片段\n"
            "开始时间戳： [00:02:36.520]\n"
            "结束时间戳： [00:36:41.040]\n"
            "6. 优化标题格式 (版本A)\n"
            "内资闭门会 / 本地A / 本地B / 本地C / 2026-07-07\n"
            "7. 优化标题格式 (版本B)\n"
            "内资闭门会 | 本地A | 本地B | 本地C | 2026-07-07"
        )
        planner_payload = {
            "title": "DS结构化标题",
            "title_options": ["DS结构化标题"],
            "subtitle": "DS副标题",
            "cover_main_title": "内资闭门会议",
            "cover_title": "DS主标题",
            "cover_subtitle": "DS副标题",
            "finance_cover_subtitle": "DS横屏封皮标题",
            "xiaojunjun_title": "内资闭门会议",
            "xiaojunjun_subtitle": "DS小君君标题",
            "xiaojunjun_video_title": "内资闭门会 / DS一 / DS二 / DS三 / 2026-07-07",
            "ciketc_video_title": "内资闭门会 | DS一 | DS二 | DS三 | 2026-07-07",
            "date_text": "2026.07.07",
            "emotion": "pro",
            "audio": {"start_seconds": 156.52, "end_seconds": 2201.04, "speed": 1.2},
            "provider": "deepseek",
        }

        with patch("services.mp3_video_workflow.deepseek_plan", return_value=planner_payload):
            plan = plan_from_direct_ds_response(response, audio={"filename": "demo.mp3", "duration_seconds": 2588})

        self.assertEqual(plan["title"], "DS结构化标题")
        self.assertEqual(plan["xiaojunjun_video_title"], "内资闭门会 / DS一 / DS二 / DS三 / 2026-07-07")
        self.assertEqual(plan["ciketc_video_title"], "内资闭门会 | DS一 | DS二 | DS三 | 2026-07-07")
        self.assertEqual(plan["planner_input_source"], "direct_ds_response")

    def test_strict_ds_plan_rejects_placeholder_visual_copy(self):
        plan = {
            "title": "标题版本一（信息密度高，点击率高）",
            "cover_title": "格式(版本A)",
            "cover_subtitle": "未识别到时间戳",
            "xiaojunjun_video_title": "内资闭门会 / xxxx / xxxx / xxxx / 当日日期",
            "ciketc_video_title": "内资闭门会 | xxxx | xxxx | xxxx | 当日日期",
            "audio": {"start_seconds": 0, "end_seconds": None},
        }

        with self.assertRaisesRegex(Mp3VideoWorkflowError, "规划不合格"):
            _validate_strict_ds_plan(plan)

    def test_db_retry_recovers_after_operational_error(self):
        attempts = {"count": 0}

        def operation():
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise OperationalError("SELECT 1", {}, Exception("server closed the connection unexpectedly"))
            return "ok"

        with patch("services.mp3_video_workflow.db.session.rollback") as rollback, \
                patch("services.mp3_video_workflow.db.session.remove") as remove, \
                patch("services.mp3_video_workflow.time.sleep") as sleep:
            self.assertEqual(_db_retry(operation, "test"), "ok")

        self.assertEqual(attempts["count"], 2)
        rollback.assert_called_once()
        remove.assert_called_once()
        sleep.assert_called_once()

    def test_db_retry_recovers_after_read_only_transaction(self):
        attempts = {"count": 0}

        def operation():
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise RuntimeError("cannot execute UPDATE in a read-only transaction")
            return "ok"

        with patch("services.mp3_video_workflow.db.session.rollback") as rollback, \
                patch("services.mp3_video_workflow.db.session.remove") as remove, \
                patch("services.mp3_video_workflow.time.sleep") as sleep:
            self.assertEqual(_db_retry(operation, "read-only test"), "ok")

        self.assertEqual(attempts["count"], 2)
        rollback.assert_called_once()
        remove.assert_called_once()
        sleep.assert_called_once()

    def test_generate_from_transcript_reuses_same_input_cache(self):
        transcript = "这里是一段足够长的 IMA 复制全文，讨论黄金、美元和美债的交易机会。" * 3
        workflow = {
            "transcript_text": transcript,
            "transcript_prompt": "请总结这段音频\n\n{{transcript}}",
        }
        audio = {
            "attachment_id": "asset-1",
            "filename": "demo.mp3",
            "path": "/tmp/demo.mp3",
            "duration_seconds": 90,
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {
                "MP3_VIDEO_TRANSCRIPT_CACHE_DIR": tmpdir,
                "MP3_VIDEO_TRANSCRIPT_MODEL": "deepseek-test",
                "MP3_VIDEO_TRANSCRIPT_CACHE_DISABLED": "0",
            }, clear=False), \
                    patch("services.mp3_video_workflow.deepseek_transcript_response", return_value="DS 返回结果") as deepseek, \
                    patch("services.mp3_video_workflow._find_recent_transcript_deepseek_result", return_value=None), \
                    patch("services.mp3_video_workflow._emit"):
                first = _generate_from_transcript(
                    task_id="job-1",
                    workflow=workflow,
                    audio=audio,
                    output_path=os.path.join(tmpdir, "job-1.json"),
                )
                second = _generate_from_transcript(
                    task_id="job-2",
                    workflow=workflow,
                    audio=audio,
                    output_path=os.path.join(tmpdir, "job-2.json"),
                )
                self.assertTrue(os.path.exists(first["cache"]["path"]))

        deepseek.assert_called_once()
        self.assertEqual(first["cache"]["status"], "miss")
        self.assertEqual(second["cache"]["status"], "hit")
        self.assertEqual(second["response_text"], "DS 返回结果")

    def test_fallback_plan_extracts_clean_title_and_audio_speed(self):
        plan = fallback_plan(
            "标题方案：算力调整假摔还是上车。核心观点：CPO 时代预期差。",
            audio={"filename": "demo.mp3", "duration_seconds": 120},
        )

        self.assertEqual(plan["audio"]["speed"], DEFAULT_SPEED)
        self.assertIn("算力调整", plan["title"])
        self.assertNotIn("标题方案", plan["title"])
        self.assertEqual(plan["cover_main_title"], "内资闭门会议")
        self.assertEqual(plan["xiaojunjun_title"], "内资闭门会议")
        self.assertEqual(plan["planner_version"], PLANNER_VERSION)

    def test_fallback_plan_extracts_gemini_investor_titles_and_clip_range(self):
        response = """
        1. 投资者视角长标题（3个版本）
        这三个标题兼顾了高信息密度与吸引力。

        内资闭门会 | 航空基本面拐点确立现价进入买入窗口 | 估值极寒跌至历史1%分位 | 油价大跌释放超额红利 | 260622
        内资闭门会 | 揭秘航空股左侧布局时机 | 隐形供给瓶颈引爆戴维斯双击 | 票价背后的真实需求反转 | 260622
        内资闭门会 | 油价骤降叠加运力断供危机 | 航空业供需错配红利即将兑现 | 现价步入绝佳抄底区间 | 260622

        4. 音频精华片段提取
        推荐截取时间戳：01:10 - 09:45 （预估时长约 8分35秒）
        """

        titles = extract_title_candidates(response)
        audio_range = extract_audio_range(response)
        plan = fallback_plan(response, audio={"filename": "demo.mp3", "duration_seconds": 800})

        self.assertEqual(
            titles[0],
            "航空基本面拐点确立现价进入买入窗口 | 估值极寒跌至历史1%分位 | 油价大跌释放超额红利",
        )
        self.assertIn(
            "揭秘航空股左侧布局时机 | 隐形供给瓶颈引爆戴维斯双击 | 票价背后的真实需求反转",
            titles,
        )
        self.assertEqual(audio_range, (70.0, 585.0))
        self.assertEqual(plan["audio"]["start_seconds"], 70)
        self.assertEqual(plan["audio"]["end_seconds"], 585)

    def test_fallback_plan_extracts_version_titles_and_separate_timestamps(self):
        response = """
        1. 长标题（3个版本）
        版本一： 内资闭门会 | AI风口引爆有色板块 | 铜铝镍锂估值修复 | 下半场Alpha个股机会 | 260621
        版本二： 内资闭门会 | 顺周期迎AI催化 | 黄金与海运双重红利 | 寻找性价比空间 | 260621
        版本三： 内资闭门会 | 供给硬约束+需求刚性 | 有色高景气度标的 | 资金回流防御首选 | 260621

        4. 精华片段提取
        开始时间戳：00:36
        结束时间戳：02:41
        """

        plan = fallback_plan(response, audio={"filename": "demo.mp3", "duration_seconds": 229})

        self.assertEqual(
            plan["title"],
            "AI风口引爆有色板块 | 铜铝镍锂估值修复 | 下半场Alpha个股机会",
        )
        self.assertEqual(plan["audio"]["start_seconds"], 36)
        self.assertEqual(plan["audio"]["end_seconds"], 161)

    def test_audio_range_extracts_bracketed_separate_timestamps(self):
        response = """
        4. 最精华音频片段
        开始时间戳： [00:02:36.520]
        结束时间戳： [00:36:41.040]
        该片段信息密度最高。
        """

        self.assertEqual(extract_audio_range(response), (156.52, 2201.04))

    def test_audio_range_extracts_bracketed_inline_timestamp_range(self):
        response = """
        4. 音频最精华片段
        精华片段时间戳： [00:00:10] 到 [00:25:06.500]
        该片段是整场电话会的核心部分。
        """

        self.assertEqual(extract_audio_range(response), (10.0, 1506.5))

    def test_audio_range_prefers_explicit_clip_over_internal_hook_ranges(self):
        response = """
        ### 4｜最精华片段推荐
        **开始时间：05:43**
        **结束时间：20:35**
        片段内部关键钩子位置：
        - 10:51-11:27：光进铜退
        - 13:02-13:50：ScaleUp光连接放量
        """

        self.assertEqual(extract_audio_range(response), (343.0, 1235.0))

    def test_fallback_plan_extracts_flattened_gemini_version_titles(self):
        response = (
            "1. 长标题（投资者视角，高信息密度） "
            "版本一： 内资闭门会 | AI狂飙引爆有色板块 | 参股控股资源标的迎来重估 | 寻找黄金与铜的个股Alpha | 260621 "
            "版本二： 内资闭门会 | 顺周期高景气度+AI催化 | 有色板块兼具红利与进攻属性 | 海运放开预期下的估值弹性 | 260621 "
            "版本三： 内资闭门会 | AI二三线标的全面补涨 | 资源加工材料估值洼地怎么选 | 黄金、铜、稀土的赔率与策略 | 260621 "
            "4. 精华片段提取 开始时间戳： 01:00 结束时间戳： 02:40"
        )

        plan = fallback_plan(response, audio={"filename": "demo.mp3", "duration_seconds": 229})

        self.assertEqual(
            plan["title"],
            "AI狂飙引爆有色板块 | 参股控股资源标的迎来重估 | 寻找黄金与铜的个股Alpha",
        )
        self.assertEqual(plan["title_options"][:3], [
            "AI狂飙引爆有色板块 | 参股控股资源标的迎来重估 | 寻找黄金与铜的个股Alpha",
            "顺周期高景气度+AI催化 | 有色板块兼具红利与进攻属性 | 海运放开预期下的估值弹性",
            "AI二三线标的全面补涨 | 资源加工材料估值洼地怎么选 | 黄金、铜、稀土的赔率与策略",
        ])
        self.assertEqual(plan["audio"]["start_seconds"], 60)
        self.assertEqual(plan["audio"]["end_seconds"], 160)

    def test_title_extraction_ignores_prompt_placeholders(self):
        response = (
            "好的，收到您的指令。以下是基于您提供的 IMA 转录全文和处理要求，输出结果如下：\n"
            "7：尝试优化下标题，并给我另外一个版本，使用 内资闭门会 | xxxx | xxxx | xxxx | 当日日期 ，这样的格式来写\n"
            "**7. 标题优化** 内资闭门会 | “小微” 如何复制小程序奇迹 | 解码To C Agent | 2026-07-06\n"
            "中金闭门会揭示最大信息差：市场低估C端Agent独占价值，高估B端商业化速度。"
        )

        candidates = extract_title_candidates(response)
        joined = "\n".join(candidates)

        self.assertIn("小微", candidates[0])
        self.assertIn("解码To C Agent", candidates[0])
        self.assertNotIn("xxxx", joined)
        self.assertNotIn("当日日期", joined)
        self.assertNotIn("收到您的指令", joined)

    def test_fallback_plan_maps_ds_six_and_seven_to_video_titles(self):
        slash_title = "内资闭门会 / 化工供给侧东升西落 / 油价70美元补库倒计时 / 蛋氨酸-PVA-制冷剂三大主线 / 2026-07-06"
        pipe_title = "内资闭门会 | 全球化工产能重塑 | 补库提前量价齐升 | 锁定MDI/化肥/氟化工机遇 | 2026-07-06"
        response = (
            "6. **闭门会格式标题（斜杠版）** "
            f"{slash_title} "
            "7. **闭门会格式标题（竖线版）** "
            f"{pipe_title}"
        )

        extracted = extract_closed_door_video_titles(response)
        plan = validate_plan({}, fallback=fallback_plan(response, audio={"duration_seconds": 1000}), audio={"duration_seconds": 1000})

        self.assertEqual(extracted["xiaojunjun_video_title"], slash_title)
        self.assertEqual(extracted["ciketc_video_title"], pipe_title)
        self.assertEqual(plan["xiaojunjun_video_title"], slash_title)
        self.assertEqual(plan["ciketc_video_title"], pipe_title)
        self.assertEqual(_publisher_video_title(plan, role="xiaojunjun_landscape"), slash_title)
        self.assertEqual(_publisher_video_title(plan, role="ciketc_vertical"), pipe_title)

    def test_fallback_plan_uses_high_density_titles_for_ciketc_covers(self):
        response = (
            "**1. 高信息密度投资人标题（版本一）** "
            "“多数人还在用GDP和信用周期分析美元美债时，我们已全部抛掉——科技泡沫会崩吗？"
            "美债利率回不去了？美元指数破不了90？黄金今年不行但等这个信号就是暴利机会” "
            "**2. 高信息密度投资人标题（版本二）** "
            "“算力投资可能已经过剩？一个群体性误判的三条脆弱线索，以及美元、美债、黄金如何全部回归"
            "‘国家信用’这把最原始的秤——2026下半年最大的信息差与赚钱窗口” "
            "**3. 精简短标题** “科技不崩，美元不跌；美债回不去了；黄金等最后一跌” "
            "**4. 最精华音频片段（约25分钟）** - **开始时间戳**：00:11 - **结束时间戳**：25:27"
        )

        fallback = fallback_plan(response, audio={"filename": "demo.mp3", "duration_seconds": 2000})
        plan = validate_plan({}, fallback=fallback, audio={"filename": "demo.mp3", "duration_seconds": 2000})

        self.assertIn("科技泡沫会崩吗", plan["title_options"][0])
        self.assertIn("算力投资可能已经过剩", plan["title_options"][1])
        self.assertEqual(
            plan["finance_cover_subtitle"],
            "抛掉GDP和信用周期\n科技泡沫会崩吗，美债回不去\n美元难破90",
        )
        self.assertEqual(plan["cover_title"], "美债美元\n黄金窗口")
        self.assertEqual(plan["cover_subtitle"], "回归国家信用定价锚")
        self.assertEqual(plan["audio"]["start_seconds"], 11)
        self.assertEqual(plan["audio"]["end_seconds"], 1527)
        self.assertNotEqual(plan["xiaojunjun_subtitle"], plan["finance_cover_subtitle"])

    def test_validate_plan_preserves_deepseek_vertical_cover_decision(self):
        response = (
            "**1. 高信息密度投资人标题（版本一）** "
            "“多数人还在用GDP和信用周期分析美元美债时，我们已全部抛掉——科技泡沫会崩吗？"
            "美债利率回不去了？美元指数破不了90？黄金今年不行但等这个信号就是暴利机会” "
            "**2. 高信息密度投资人标题（版本二）** "
            "“算力投资可能已经过剩？一个群体性误判的三条脆弱线索，以及美元、美债、黄金如何全部回归"
            "‘国家信用’这把最原始的秤——2026下半年最大的信息差与赚钱窗口”"
        )
        fallback = fallback_plan(response, audio={"filename": "demo.mp3", "duration_seconds": 2000})

        plan = validate_plan(
            {
                "title": fallback["title"],
                "title_options": fallback["title_options"],
                "cover_title": "美元美债\n黄金信号",
                "cover_subtitle": "国家信用重定价",
                "finance_cover_subtitle": "抛掉GDP旧框架\n美债美元再定价\n黄金等待信号",
                "audio": {},
            },
            fallback=fallback,
            audio={"filename": "demo.mp3", "duration_seconds": 2000},
        )

        self.assertEqual(plan["cover_title"], "美元美债\n黄金信号")
        self.assertEqual(plan["cover_subtitle"], "国家信用重定价")
        self.assertEqual(
            plan["finance_cover_subtitle"],
            "抛掉GDP旧框架\n美债美元再定价\n黄金等待信号",
        )

    def test_validate_plan_separates_xiaojunjun_from_ciketc_copy(self):
        response = (
            "**7. 标题优化** 内资闭门会 | “小微” 如何复制小程序奇迹 | "
            "解码 To C Agent 的抽佣逻辑 | 2026-07-06\n"
            "中金闭门会揭示最大信息差：市场低估C端Agent独占价值，高估B端商业化速度。"
        )
        fallback = fallback_plan(response, audio={"filename": "demo.mp3", "duration_seconds": 1000})

        plan = validate_plan(
            {
                "title": fallback["title"],
                "title_options": fallback["title_options"],
                "cover_title": "微信小微\n交易入口",
                "cover_subtitle": "C端Agent重估",
                "finance_cover_subtitle": "微信小微灰测\n复制小程序奇迹\n重估抽佣逻辑",
                "xiaojunjun_subtitle": "C端Agent重估",
                "audio": {},
            },
            fallback=fallback,
            audio={"filename": "demo.mp3", "duration_seconds": 1000},
        )

        self.assertNotEqual(plan["xiaojunjun_subtitle"], plan["cover_subtitle"])
        self.assertNotEqual(plan["xiaojunjun_subtitle"], plan["finance_cover_subtitle"])
        self.assertIn("小微", plan["xiaojunjun_subtitle"])
        self.assertIn("To C Agent", plan["xiaojunjun_subtitle"])

    def test_validate_plan_prefers_dedicated_xiaojunjun_copy_when_role_titles_overlap(self):
        fallback = fallback_plan(
            "内资闭门会 / 科技股调整真相 / 机构重新定价三大信号 / 业绩兑现成新主线 / 2026-07-19\n"
            "内资闭门会 | 科技股调整真相 | 机构重新定价三大信号 | 业绩兑现成新主线 | 2026-07-19",
            audio={"filename": "demo.mp3", "duration_seconds": 1000},
        )

        plan = validate_plan(
            {
                "title": "科技股调整真相",
                "finance_cover_subtitle": "科技股调整真相\n机构重新定价三大信号\n业绩兑现成新主线",
                "xiaojunjun_subtitle": "龙头回撤并非转弱\n资金切向确定性业绩\n三季度验证估值切换",
                "xiaojunjun_video_title": "内资闭门会 / 科技股调整真相 / 机构重新定价三大信号 / 业绩兑现成新主线 / 2026-07-19",
                "ciketc_video_title": "内资闭门会 | 科技股调整真相 | 机构重新定价三大信号 | 业绩兑现成新主线 | 2026-07-19",
                "audio": {},
            },
            fallback=fallback,
            audio={"filename": "demo.mp3", "duration_seconds": 1000},
        )

        self.assertEqual(
            plan["xiaojunjun_subtitle"],
            "龙头回撤并非转弱\n资金切向确定性业绩\n三季度验证估值切换",
        )
        self.assertNotEqual(plan["xiaojunjun_subtitle"], plan["finance_cover_subtitle"])

    def test_validate_plan_rejects_duplicate_dedicated_xiaojunjun_copy(self):
        fallback = fallback_plan(
            "版本一：科技股调整真相，机构重新定价三大信号，业绩兑现成新主线。\n"
            "版本二：龙头回撤不是转弱，资金切向确定性业绩。",
            audio={"filename": "demo.mp3", "duration_seconds": 1000},
        )
        duplicate = "科技股调整真相\n机构重新定价三大信号\n业绩兑现成新主线"

        plan = validate_plan(
            {
                "title": "科技股调整真相",
                "finance_cover_subtitle": duplicate,
                "xiaojunjun_subtitle": duplicate,
                "xiaojunjun_video_title": "内资闭门会 / 龙头回撤不是转弱 / 资金切向确定性业绩 / 2026-07-19",
                "audio": {},
            },
            fallback=fallback,
            audio={"filename": "demo.mp3", "duration_seconds": 1000},
        )

        self.assertNotEqual(plan["xiaojunjun_subtitle"], duplicate)
        self.assertIn("龙头回撤不是转弱", plan["xiaojunjun_subtitle"])

    def test_vertical_cover_asset_focus_is_not_hardcoded_to_us_bonds_gold(self):
        response = (
            "**1. 高信息密度投资人标题（版本一）** "
            "“港股和铜油资产如何重新回归供需定价？原油窗口、铜价弹性与港股估值修复，"
            "可能是下半年最大的交易机会”"
        )

        plan = validate_plan(
            {},
            fallback=fallback_plan(response, audio={"filename": "demo.mp3", "duration_seconds": 800}),
            audio={"filename": "demo.mp3", "duration_seconds": 800},
        )

        self.assertNotEqual(plan["cover_title"], "美债美元\n黄金窗口")
        self.assertIn("港股", plan["cover_title"])
        self.assertTrue(any(term in plan["cover_title"] for term in ("铜", "原油", "油价")))

    def test_validate_plan_preserves_local_clip_range_when_planner_omits_it(self):
        fallback = fallback_plan(
            "内资闭门会 | 油价骤降叠加运力断供危机 | 航空业供需错配红利即将兑现 | 现价步入绝佳抄底区间 | 260622\n"
            "推荐截取时间戳：01:10 - 09:45",
            audio={"duration_seconds": 800},
        )
        plan = validate_plan(
            {
                "title": "内资闭门会 | 油价骤降叠加运力断供危机 | 航空业供需错配红利即将兑现 | 现价步入绝佳抄底区间 | 260622",
                "audio": {},
            },
            fallback=fallback,
            audio={"duration_seconds": 800},
        )

        self.assertEqual(
            plan["title"],
            "油价骤降叠加运力断供危机 | 航空业供需错配红利即将兑现 | 现价步入绝佳抄底区间",
        )
        self.assertEqual(plan["audio"]["start_seconds"], 70)
        self.assertEqual(plan["audio"]["end_seconds"], 585)

    def test_validate_plan_clamps_audio_range(self):
        fallback = fallback_plan("标题方案：CPO 时代预期差。", audio={"duration_seconds": 60})
        plan = validate_plan(
            {
                "title": "CPO 时代预期差",
                "audio": {"start_seconds": "00:10", "end_seconds": "99:00", "speed": 1.0},
            },
            fallback=fallback,
            audio={"duration_seconds": 60},
        )

        self.assertEqual(plan["audio"]["start_seconds"], 10)
        self.assertEqual(plan["audio"]["end_seconds"], 60)
        self.assertEqual(plan["audio"]["speed"], DEFAULT_SPEED)

    def test_audio_segment_uses_end_offset_for_publisher(self):
        segment = audio_segment_from_plan(
            {"path": "/tmp/demo.mp3", "duration_seconds": 100},
            {"audio": {"start_seconds": 12, "end_seconds": 72}},
        )

        self.assertEqual(segment["start"], 12)
        self.assertEqual(segment["end_offset"], 28)
        self.assertEqual(segment["speed"], DEFAULT_SPEED)

    def test_draw_visual_assets_create_files(self):
        plan = fallback_plan("标题方案：算力调整假摔还是上车。", audio={"duration_seconds": 12})
        with tempfile.TemporaryDirectory() as tmpdir:
            infographic = os.path.join(tmpdir, "infographic.png")
            img = Image.new("RGB", (1200, 700), "white")
            draw = ImageDraw.Draw(img)
            draw.rectangle([40, 40, 1160, 660], outline="black", width=4)
            draw.text((100, 100), "dummy", fill="black")
            img.save(infographic)

            outputs = [
                os.path.join(tmpdir, "finance.jpg"),
                os.path.join(tmpdir, "audio_bg.jpg"),
                os.path.join(tmpdir, "closed_cover.jpg"),
                os.path.join(tmpdir, "closed_bg.jpg"),
            ]
            draw_finance_cover(outputs[0], plan)
            draw_audio_background(outputs[1], plan, infographic)
            draw_closed_door_cover(outputs[2], plan, include_date=True)
            draw_closed_door_cover(outputs[3], plan, include_date=False)

            for path in outputs:
                self.assertTrue(os.path.exists(path), path)
                self.assertGreater(os.path.getsize(path), 10_000)

    def test_finance_cover_copy_is_short_before_generic_renderer(self):
        value = (
            "市场恐慌暴跌为何高呼抓紧布局\n"
            "全球化工供给侧东升西落逻辑已定\n"
            "短期超跌砸出黄金坑补库品类拿好"
        )

        result = _finance_cover_highlight_text({"finance_cover_subtitle": value})

        self.assertEqual(
            result,
            "市场恐慌暴跌抓紧布局\n化工供给东升西落\n超跌黄金坑补库拿好",
        )
        self.assertTrue(all(len(line) <= 13 for line in result.split("\n")))

    def test_visual_artifacts_ready_requires_all_non_empty_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            artifacts = {}
            for key in VISUAL_ARTIFACT_KEYS:
                path = os.path.join(tmpdir, f"{key}.jpg")
                Image.new("RGB", VISUAL_ARTIFACT_EXPECTED_SIZES[key], "white").save(path)
                artifacts[key] = {"path": path, "template_version": VISUAL_ARTIFACT_VERSION}

            self.assertTrue(_visual_artifacts_ready(artifacts))

            os.remove(artifacts["audio_background"]["path"])
            self.assertFalse(_visual_artifacts_ready(artifacts))

    def test_visual_artifacts_ready_rejects_old_template_version(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            artifacts = {}
            for key in VISUAL_ARTIFACT_KEYS:
                path = os.path.join(tmpdir, f"{key}.jpg")
                Image.new("RGB", VISUAL_ARTIFACT_EXPECTED_SIZES[key], "white").save(path)
                artifacts[key] = {"path": path, "template_version": "old"}

            self.assertFalse(_visual_artifacts_ready(artifacts))

    def test_workflow_render_mode_defaults_to_full_and_accepts_preview_aliases(self):
        self.assertEqual(_workflow_render_mode({}), "full_video")
        self.assertEqual(_workflow_render_mode({"render_mode": "keyframes"}), "keyframes")
        self.assertEqual(_workflow_render_mode({"output_mode": "preview"}), "keyframes")
        self.assertEqual(_workflow_render_mode({"preview_only": True}), "keyframes")

    def test_workflow_generate_subtitles_defaults_to_false_and_accepts_explicit_true(self):
        self.assertFalse(_workflow_generate_subtitles({}))
        self.assertTrue(_workflow_generate_subtitles({"generate_subtitles": True}))
        self.assertFalse(_workflow_generate_subtitles({"generate_subtitles": False}))
        self.assertFalse(_workflow_generate_subtitles({"generate_subtitles": "off"}))
        self.assertFalse(_workflow_generate_subtitles({"metadata": {"enable_subtitles": "0"}}))

    def test_generate_video_keyframes_creates_reusable_artifacts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            landscape = os.path.join(tmpdir, "landscape.jpg")
            vertical = os.path.join(tmpdir, "vertical.jpg")
            Image.new("RGB", (1280, 720), "#112233").save(landscape)
            Image.new("RGB", (1080, 1920), "#223344").save(vertical)
            artifacts = {
                "xiaojunjun_background": {"path": landscape},
                "audio_background": {"path": vertical},
            }

            with patch("services.mp3_video_workflow.storage_dir_for_job", return_value=Path(tmpdir)):
                keyframes = generate_video_keyframes(
                    "job-1",
                    plan=fallback_plan("标题方案：黄金调整以后怎么看。", audio={"duration_seconds": 180}),
                    artifacts=artifacts,
                    audio={"filename": "demo.mp3", "duration_seconds": 180},
                    source_text="黄金调整以后怎么看。央行购金和避险需求仍是长期主线。",
                )

            self.assertEqual(len(keyframes), 6)
            self.assertTrue(_keyframe_artifacts_ready(keyframes))
            for artifact in keyframes.values():
                self.assertTrue(os.path.exists(artifact["path"]))
                self.assertGreater(os.path.getsize(artifact["path"]), 10_000)

    def test_write_data_url_rejects_wrong_image_size(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "source.png"
            output = Path(tmpdir) / "output.png"
            Image.new("RGB", (64, 64), "white").save(source)
            data_url = "data:image/png;base64," + base64.b64encode(source.read_bytes()).decode("ascii")

            with self.assertRaises(Mp3VideoWorkflowError):
                _write_data_url(data_url, str(output), expected_size=(1280, 720), label="test")

    def test_gemini_response_detection_rejects_page_shell(self):
        shell_text = (
            "跳至主要内容 Gemini 发起新对话 搜索对话内容 库 Gem 笔记本 "
            "最近 与 Gemini 对话 MP3 demo.mp3 Pro Gemini 是一款 AI 工具"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            worker = GeminiBrowserWorker(GeminiBrowserOptions(user_data_dir=tmpdir))
            valid_response = (
                "这份音频的核心结论是 AI 算力资产被系统性低估。报告先解释需求端的训练和推理增长，"
                "再讨论供给端的数据中心、电力、网络和芯片约束，最后给出市场可能重新定价的路径，"
                "整体内容具有明确观点、事实链条和可用于视频策划的结构化信息。并且包含足够长的正文，"
                "用于区分真实回答和页面外壳。"
            )

            self.assertTrue(_looks_like_page_shell_text(shell_text))
            self.assertFalse(worker._looks_like_response(shell_text, ""))
            self.assertTrue(worker._looks_like_response(valid_response, ""))

    def test_mp3_gemini_defaults_wait_for_long_audio(self):
        with patch.dict(os.environ, {
            "MP3_VIDEO_GEMINI_RESPONSE_TIMEOUT": "",
            "MP3_VIDEO_GEMINI_IMAGE_TIMEOUT": "",
            "MP3_VIDEO_GEMINI_TOTAL_TIMEOUT": "",
            "MP3_VIDEO_GEMINI_UPLOAD_TIMEOUT": "",
            "GEMINI_MP3_VIDEO_UPLOAD_TIMEOUT": "",
        }, clear=False):
            options = gemini_options_from_env()

        self.assertGreaterEqual(options.response_timeout_seconds, 900)
        self.assertGreaterEqual(options.image_timeout_seconds, 900)
        self.assertGreaterEqual(options.total_timeout_seconds, 2400)
        self.assertGreaterEqual(options.upload_timeout_seconds, 900)

    def test_mp3_gemini_initial_prompt_is_never_empty(self):
        self.assertEqual(initial_gemini_prompt({}), DEFAULT_GEMINI_INITIAL_PROMPT)
        self.assertEqual(initial_gemini_prompt({"gemini_prompt": "请处理这个音频"}), "请处理这个音频")

    def test_mp3_transcript_prompt_defaults_and_override(self):
        self.assertEqual(initial_transcript_prompt({}), DEFAULT_TRANSCRIPT_DEEPSEEK_PROMPT)
        self.assertEqual(initial_transcript_prompt({"transcript_prompt": "按我的模板处理"}), "按我的模板处理")

    def test_build_transcript_user_prompt_replaces_placeholder(self):
        prompt = "请处理 {audio_filename}\n\n{{transcript}}"
        rendered = _build_transcript_user_prompt(
            "这里是 IMA 复制全文，包含完整会议讨论。",
            prompt=prompt,
            audio={"filename": "demo.mp3"},
        )

        self.assertIn("demo.mp3", rendered)
        self.assertIn("完整会议讨论", rendered)
        self.assertNotIn("{{transcript}}", rendered)

    def test_deepseek_transcript_response_posts_prompt_and_transcript(self):
        class FakeResponse:
            status_code = 200
            text = ""

            def raise_for_status(self):
                return None

            def json(self):
                return {"choices": [{"message": {"content": "DS 返回结果"}}]}

        captured = {}

        def fake_post(url, headers=None, json=None, timeout=None):
            captured["url"] = url
            captured["headers"] = headers or {}
            captured["json"] = json or {}
            captured["timeout"] = timeout
            return FakeResponse()

        with patch.dict(os.environ, {"MP3_VIDEO_TRANSCRIPT_API_KEY": "test-key", "MP3_VIDEO_TRANSCRIPT_MODEL": "deepseek-test"}, clear=False):
            with patch("services.mp3_video_workflow.requests.post", side_effect=fake_post):
                result = deepseek_transcript_response("转录全文内容足够长", prompt="请总结", audio={"filename": "demo.mp3"})

        self.assertEqual(result, "DS 返回结果")
        self.assertTrue(captured["url"].endswith("/v1/chat/completions"))
        self.assertEqual(captured["json"]["model"], "deepseek-test")
        user_message = captured["json"]["messages"][1]["content"]
        self.assertIn("请总结", user_message)
        self.assertIn("转录全文内容足够长", user_message)

    def test_draw_transcript_infographic_creates_image(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output = os.path.join(tmpdir, "transcript.png")
            draw_transcript_infographic(
                output,
                "投资者视角长标题：内资闭门会 | 黄金短期调整但长期逻辑仍在 | 260705\n推荐截取时间戳：00:10 - 01:20\n核心观点：央行购金和避险需求仍是主线。",
                audio={"filename": "demo.mp3"},
            )

            self.assertTrue(os.path.exists(output))
            self.assertGreater(os.path.getsize(output), 10_000)

    def test_transcript_infographic_filters_process_markers(self):
        bullets = _infographic_bullets(
            "好的，收到您的指令。以下是基于您提供的 IMA 转录全文和处理要求，输出结果如下：\n"
            "使用 内资闭门会 | xxxx | xxxx | xxxx | 当日日期 ，这样的格式来写\n"
            "1. 核心观点：科技不崩之前，美元指数一直偏强。\n"
            "4 最精华音频片段（约25分钟）\n"
            "推荐截取时间戳：00:11 - 25:27\n"
            "Generated from transcript, no Gemini upload\n"
            "黄金的基本面就是央行，决定了黄金跌不深。"
        )

        joined = "\n".join(bullets)
        self.assertIn("科技不崩", joined)
        self.assertIn("黄金的基本面", joined)
        self.assertNotIn("最精华音频片段", joined)
        self.assertNotIn("推荐截取", joined)
        self.assertNotIn("xxxx", joined)
        self.assertNotIn("收到您的指令", joined)

    def test_transcript_infographic_prefers_ds_gold_quotes_over_ai_preamble(self):
        source = (
            "好的，作为您的投研助手，根据您提供的音频文档内容，我已为您整理了投资者视角的信息摘要。\n"
            "以下是针对您7个问题的具体回答：\n"
            "1. 投资者视角标题（版本一）\n"
            "AI连接器格局已变，国产算力七月底定份额。\n"
            "4. 最精华片段识别（时间戳）\n"
            "开始时间戳：00:05:03.840\n结束时间戳：00:19:35.685\n"
            "5. 音频金句\n"
            "“全球的连接器格局已经发生了50年以来的巨变”（约00:04:15）\n"
            "要点：AI对连接器行业的影响并非短期炒作。\n"
            "“今天这波调整与基本面有比较大的背离”（约00:19:40）\n"
            "要点：当前下跌反而提供了逆向投资机会。\n"
            "6. 优化标题\n内资闭门会 / AI连接器 / 七月底定份额 / 450亿空间 / 2026-07-15\n"
        )

        bullets = _infographic_bullets(source)

        self.assertGreaterEqual(len(bullets), 3)
        self.assertIn("全球的连接器格局", bullets[0])
        self.assertTrue(any("基本面" in item for item in bullets))
        self.assertNotIn("投研助手", "\n".join(bullets))
        self.assertNotIn("具体回答", "\n".join(bullets))

    def test_transcript_model_uses_specific_override(self):
        with patch.dict(os.environ, {"MP3_VIDEO_TRANSCRIPT_MODEL": "deepseek-transcript", "MP3_VIDEO_PLANNER_MODEL": "deepseek-planner"}, clear=False):
            self.assertEqual(transcript_model(), "deepseek-transcript")

    def test_planner_model_uses_flash_specific_override(self):
        with patch.dict(
            os.environ,
            {"DEEPSEEK_FLASH_MODEL": "deepseek-v4-flash-high"},
            clear=True,
        ):
            self.assertEqual(planner_model(), "deepseek-v4-flash-high")

    def test_mp3_gemini_retryable_error_classification(self):
        self.assertTrue(_is_retryable_gemini_error("等待 Gemini 生成图片超时"))
        self.assertTrue(_is_retryable_gemini_error("页面自动化超时: Locator.click: Timeout 10000ms exceeded"))
        self.assertTrue(_is_retryable_gemini_error("Gemini 提示词未成功提交: 未找到 Gemini 发送按钮"))
        self.assertFalse(_is_retryable_gemini_error("音频文件不存在: demo.mp3"))

    def test_gemini_upload_ready_requires_file_name_and_enabled_submit_button(self):
        self.assertFalse(_upload_is_ready({"hasName": False, "busy": False, "canceled": False, "enabledSend": True}))
        self.assertFalse(_upload_is_ready({"hasName": True, "busy": False, "canceled": False, "enabledSend": False}))
        self.assertTrue(_upload_is_ready({"hasName": True, "busy": False, "canceled": False, "enabledSend": True}))
        self.assertFalse(_upload_is_ready({"hasName": True, "busy": True, "canceled": False, "enabledSend": True}))
        self.assertFalse(_upload_is_ready({"hasName": False, "busy": False, "canceled": False, "enabledSend": False}))

    def test_gemini_response_wait_rejects_dangling_timestamp(self):
        self.assertTrue(_looks_like_incomplete_response("4. 精华片段提取 开始时间戳： 00:36"))
        self.assertFalse(_looks_like_incomplete_response("4. 精华片段提取 开始时间戳： 00:36 结束时间戳：02:41"))
        self.assertFalse(_looks_like_incomplete_response("推荐截取时间戳：01:10 - 09:45 （预估时长约 8分35秒）"))

    def test_pdf_evidence_wait_requires_a_complete_third_json_block(self):
        prompt = (
            "请复核正文。最后输出 PDF_EVIDENCE_V1_JSON: "
            '{"schema_version":"report_pdf_evidence_v1","claims":[...]}'
        )
        first_two_blocks = "第一块正文。第二块标题。"
        partial_third_block = (
            first_two_blocks
            + ' JSON {"schema_version":"report_pdf_evidence_v1","claims":['
        )
        complete_third_block = (
            partial_third_block
            + '{"claim_text":"增长","pdf_page":1}]}'
        )
        legacy_complete_block = (
            first_two_blocks
            + ' JSON {"evidence_map":[{"script_paragraph_index":1,'
            '"pdf_pages":[1],"source_excerpt":"Revenue"}]}'
        )

        self.assertTrue(_expects_pdf_evidence_contract(prompt))
        self.assertFalse(_has_complete_pdf_evidence_payload(first_two_blocks))
        self.assertFalse(_has_complete_pdf_evidence_payload(partial_third_block))
        self.assertTrue(_has_complete_pdf_evidence_payload(complete_third_block))
        self.assertTrue(_has_complete_pdf_evidence_payload(legacy_complete_block))

    def test_promptless_submit_wait_does_not_treat_ready_button_as_submitted(self):
        self.assertFalse(_promptless_submit_confirmed({"enabledSend": True}, "", generating=False))
        self.assertFalse(_promptless_submit_confirmed({"enabledSend": False}, "", generating=False))
        self.assertTrue(_promptless_submit_confirmed({"enabledSend": False}, "", generating=False, clicked_send=True))
        self.assertTrue(_promptless_submit_confirmed({"enabledSend": True}, "", generating=True))
        self.assertFalse(_promptless_submit_confirmed({"enabledSend": False}, "还在输入框里", generating=False, clicked_send=True))

    def test_submit_state_summary_keeps_compact_diagnostics(self):
        summary = _submit_state_summary(
            {"hasName": True, "busy": False, "canceled": False, "enabledSend": False, "sendButtonCount": 0},
            "还在输入框里",
        )

        self.assertIn("enabledSend=False", summary)
        self.assertIn("sendButtonCount=0", summary)
        self.assertIn("composer_chars=6", summary)

    def test_stale_gemini_singleton_files_are_cleared(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_dir = Path(tmpdir)
            for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
                (profile_dir / name).write_text("stale", encoding="utf-8")

            cleared = _clear_stale_singleton_files(profile_dir, "host-99999999（pid 99999999 未运行/疑似残留）")

            self.assertTrue(cleared)
            self.assertFalse((profile_dir / "SingletonLock").exists())
            self.assertFalse((profile_dir / "SingletonCookie").exists())
            self.assertFalse((profile_dir / "SingletonSocket").exists())

    def test_running_gemini_singleton_files_are_not_cleared(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_dir = Path(tmpdir)
            lock_path = profile_dir / "SingletonLock"
            lock_path.write_text("active", encoding="utf-8")

            cleared = _clear_stale_singleton_files(profile_dir, f"host-{os.getpid()}（pid {os.getpid()} 运行中）")

            self.assertFalse(cleared)
            self.assertTrue(lock_path.exists())

    def test_singleton_launch_error_detection_is_specific(self):
        self.assertTrue(_looks_like_singleton_launch_error("Failed to create /tmp/profile/SingletonLock: File exists"))
        self.assertTrue(_looks_like_singleton_launch_error("Failed to create a ProcessSingleton for your profile directory. Aborting now"))
        self.assertFalse(_looks_like_singleton_launch_error("Target page, context or browser has been closed"))

    def test_video_child_id_can_be_recovered_for_deferred_resume(self):
        result = {
            "children": {
                "vertical_video": {
                    "job_id": "child-vertical",
                    "status": "processing",
                }
            }
        }

        self.assertEqual(_child_id_from_result(result, "vertical_video"), "child-vertical")

    def test_publisher_result_summary_uses_projected_video_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            video_path = Path(tmpdir) / "done.mp4"
            video_path.write_bytes(b"mp4")
            task = PublicationTask(
                id="child-landscape",
                status="completed",
                current_step="done",
                progress=100,
                params={"aspect_ratio": "16:9"},
                result={},
            )
            task.task_video_path = str(video_path)

            with patch("services.mp3_video_workflow._video_file_ready", return_value=True):
                summary = _publisher_result_summary(task)

                self.assertEqual(summary["video_path"], str(video_path))
                self.assertEqual(summary["landscape_video_path"], str(video_path))
                self.assertTrue(_video_summary_ready(summary))

    def test_incomplete_mp4_is_not_reusable_or_disk_recoverable(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            video_path = Path(tmpdir) / "broken.mp4"
            video_path.write_bytes(b"not-a-real-mp4" * 100000)
            old_time = max(0, video_path.stat().st_mtime - 60)
            os.utime(video_path, (old_time, old_time))

            self.assertFalse(_video_file_ready(video_path))
            self.assertFalse(_video_summary_ready({"status": "completed", "video_path": str(video_path)}))
            self.assertFalse(_publisher_video_file_is_stable(video_path))

    def test_probeable_video_is_accepted(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            video_path = Path(tmpdir) / "ready.mp4"
            video_path.write_bytes(b"placeholder")
            probe_payload = '{"streams":[{"codec_type":"video","width":1280,"height":720}],"format":{"duration":"12.5"}}'
            with patch("services.mp3_video_workflow.subprocess.run", return_value=SimpleNamespace(stdout=probe_payload)):
                self.assertTrue(_video_file_ready(video_path))

    def test_publisher_result_summary_marks_subtitle_toggle(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            video_path = Path(tmpdir) / "done.mp4"
            video_path.write_bytes(b"mp4")
            task = PublicationTask(
                id="child-no-subtitles",
                status="completed",
                current_step="done",
                progress=100,
                params={
                    "aspect_ratio": "9:16",
                    "generate_subtitles": False,
                    "video": {"sub_burn_enabled": False, "asr_enabled": False},
                },
                result={"video_path": str(video_path), "generate_subtitles": False},
            )

            summary = _publisher_result_summary(task)

            self.assertFalse(_publisher_child_generate_subtitles(task))
            self.assertFalse(summary["generate_subtitles"])
            self.assertEqual(summary["vertical_video_path"], str(video_path))

    def test_reusable_video_summary_falls_back_to_existing_publisher_child(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            video_path = Path(tmpdir) / "done.mp4"
            video_path.write_bytes(b"mp4")
            child = PublicationTask(
                id="child-vertical",
                status="completed",
                current_step="done",
                progress=100,
                params={"aspect_ratio": "9:16"},
                result={"video_path": str(video_path)},
            )
            with (
                patch(
                    "services.mp3_video_workflow._find_existing_publisher_video_task",
                    return_value=child,
                ) as find_existing,
                patch("services.mp3_video_workflow._video_file_ready", return_value=True),
            ):
                summary, task = _reusable_video_summary({}, "vertical_video", parent_id="parent-1")

            self.assertIs(task, child)
            self.assertEqual(summary["vertical_video_path"], str(video_path))
            find_existing.assert_called_once_with(
                "parent-1",
                role="ciketc_vertical",
                aspect_ratio="9:16",
            )

    def test_forced_regeneration_reuses_only_child_created_after_request(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            video_path = Path(tmpdir) / "regenerated.mp4"
            video_path.write_bytes(b"mp4")
            requested_at = datetime(2026, 7, 30, 1, 30, 0)
            child = PublicationTask(
                id="child-regenerated",
                status="completed",
                current_step="done",
                progress=100,
                params={"aspect_ratio": "16:9"},
                result={"video_path": str(video_path)},
                created_at=requested_at + timedelta(seconds=1),
            )
            with (
                patch(
                    "services.mp3_video_workflow._completed_child_from_result",
                    return_value=child,
                ),
                patch("services.mp3_video_workflow._video_file_ready", return_value=True),
                patch(
                    "services.mp3_video_workflow._find_existing_publisher_video_task",
                ) as find_existing,
            ):
                summary, task = _reusable_video_summary(
                    {"landscape_video": {"video_path": "/tmp/old.mp4"}},
                    "landscape_video",
                    parent_id="parent-1",
                    created_not_before=requested_at,
                )

            self.assertIs(task, child)
            self.assertEqual(summary["landscape_video_path"], str(video_path))
            find_existing.assert_not_called()

    def test_forced_regeneration_rejects_child_created_before_request(self):
        requested_at = datetime(2026, 7, 30, 1, 30, 0)
        old_child = PublicationTask(
            id="child-old",
            status="completed",
            current_step="done",
            progress=100,
            params={"aspect_ratio": "16:9"},
            result={"video_path": "/tmp/old.mp4"},
            created_at=requested_at - timedelta(seconds=1),
        )
        with (
            patch(
                "services.mp3_video_workflow._completed_child_from_result",
                return_value=old_child,
            ),
            patch(
                "services.mp3_video_workflow._find_existing_publisher_video_task",
                return_value=None,
            ) as find_existing,
        ):
            summary, task = _reusable_video_summary(
                {"landscape_video": {"video_path": "/tmp/old.mp4"}},
                "landscape_video",
                parent_id="parent-1",
                created_not_before=requested_at,
            )

        self.assertIsNone(summary)
        self.assertIsNone(task)
        find_existing.assert_called_once_with(
            "parent-1",
            role="xiaojunjun_landscape",
            aspect_ratio="16:9",
            created_not_before=requested_at,
        )

    def test_force_regeneration_timestamp_is_utc_naive_cutoff(self):
        cutoff = _workflow_force_video_regenerate_at(
            {"force_video_regenerate_at": "2026-07-30T09:30:00+08:00"},
            {},
        )

        self.assertEqual(cutoff, datetime(2026, 7, 30, 1, 30, 0))

    def test_completed_result_drops_waiting_state(self):
        result = {
            "plan": {"title": "demo"},
            "waiting_child": {"label": "vertical_video", "child_id": "child-1"},
            "deferred_waits": {"vertical_video": {"defer_count": 2}},
        }

        cleaned = _without_waiting_state(result)

        self.assertEqual(cleaned, {"plan": {"title": "demo"}})
        self.assertIn("waiting_child", result)

    def test_prepare_keyframe_job_for_full_video_preserves_assets(self):
        task = PublicationTask(
            id="job-keyframes",
            status="completed",
            current_step="keyframes_ready",
            progress=100,
            params={
                "feature": "mp3_video_workflow",
                "mp3_video_workflow": {
                    "render_mode": "keyframes",
                    "generate_subtitles": False,
                },
            },
            result={
                "render_mode": "keyframes",
                "artifacts": {"finance_cover": {"path": "/tmp/cover.jpg"}},
                "keyframes": {"keyframe_vertical_01": {"path": "/tmp/keyframe.jpg"}},
                "landscape_video": {"status": "skipped", "reason": "keyframes_preview"},
                "vertical_video": {"status": "skipped", "reason": "keyframes_preview"},
                "output_path": "/tmp/keyframes-result.json",
                "completed_at": "2026-07-07T00:00:00Z",
            },
        )

        ok, message = _api_prepare_keyframe_job_for_full_video(task)

        self.assertTrue(ok, message)
        self.assertEqual(task.status, "pending")
        self.assertEqual(task.current_step, "queued_full_video")
        self.assertEqual(task.params["mp3_video_workflow"]["render_mode"], "full_video")
        self.assertEqual(task.result["render_mode"], "full_video")
        self.assertEqual(task.result["artifacts"]["finance_cover"]["path"], "/tmp/cover.jpg")
        self.assertEqual(task.result["keyframes"]["keyframe_vertical_01"]["path"], "/tmp/keyframe.jpg")
        self.assertEqual(task.result["landscape_video"], {})
        self.assertEqual(task.result["vertical_video"], {})
        self.assertNotIn("output_path", task.result)
        self.assertNotIn("completed_at", task.result)

    def test_prepare_job_for_video_regeneration_archives_old_videos(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            landscape_bg = Path(tmpdir) / "landscape.jpg"
            vertical_bg = Path(tmpdir) / "vertical.jpg"
            landscape_bg.write_bytes(b"landscape")
            vertical_bg.write_bytes(b"vertical")
            task = PublicationTask(
                id="job-full",
                status="completed",
                current_step="done",
                progress=100,
                params={
                    "feature": "mp3_video_workflow",
                    "mp3_video_workflow": {
                        "render_mode": "full_video",
                        "generate_subtitles": False,
                    },
                },
                result={
                    "render_mode": "full_video",
                    "plan": {"title": "demo", "audio": {"start_seconds": 0, "end_seconds": 60, "speed": 1.2}},
                    "artifacts": {
                        "xiaojunjun_background": {"path": str(landscape_bg)},
                        "audio_background": {"path": str(vertical_bg)},
                    },
                    "children": {
                        "landscape_video": {"job_id": "old-landscape"},
                        "vertical_video": {"job_id": "old-vertical"},
                    },
                    "landscape_video": {"video_path": "/tmp/old-landscape.mp4"},
                    "vertical_video": {"video_path": "/tmp/old-vertical.mp4"},
                    "output_path": "/tmp/full-result.json",
                    "completed_at": "2026-07-07T00:00:00Z",
                },
            )

            ok, message = _api_prepare_job_for_video_regeneration(
                task,
                {"start_seconds": "00:12", "end_seconds": "12:46", "speed": 1.5},
            )

        self.assertTrue(ok, message)
        self.assertEqual(task.status, "pending")
        self.assertEqual(task.current_step, "queued_regenerate_videos")
        self.assertEqual(task.params["mp3_video_workflow"]["render_mode"], "full_video")
        self.assertIn("force_video_regenerate_at", task.params["mp3_video_workflow"])
        self.assertEqual(
            task.params["mp3_video_workflow"]["audio_range_override"],
            {"start_seconds": 12.0, "end_seconds": 766.0, "speed": 1.5},
        )
        self.assertEqual(
            task.result["plan"]["audio"],
            {"start_seconds": 12.0, "end_seconds": 766.0, "speed": 1.5},
        )
        self.assertIn("force_video_regenerate_at", task.result)
        self.assertEqual(task.result["children"], {})
        self.assertNotIn("landscape_video", task.result)
        self.assertNotIn("vertical_video", task.result)
        self.assertEqual(task.result["regenerated_video_history"][0]["children"]["landscape_video"]["job_id"], "old-landscape")
        self.assertNotIn("output_path", task.result)
        self.assertNotIn("completed_at", task.result)
        self.assertIn("音频 00:12-12:46", task.result["logs"][-1]["message"])

    def test_regeneration_audio_override_wins_over_rebuilt_ds_plan(self):
        plan = {
            "title": "demo",
            "audio": {"start_seconds": 12, "end_seconds": 766, "speed": 1.2},
        }

        updated = _apply_workflow_audio_range_override(
            plan,
            {"audio_range_override": {"start_seconds": 95, "end_seconds": 754}},
        )

        self.assertEqual(
            updated["audio"],
            {"start_seconds": 95.0, "end_seconds": 754.0, "speed": 1.2},
        )
        self.assertEqual(
            plan["audio"],
            {"start_seconds": 12, "end_seconds": 766, "speed": 1.2},
        )

    def test_regeneration_speed_reaches_publisher_audio_segment(self):
        result = {
            "plan": {"audio": {"start_seconds": 12, "end_seconds": 72, "speed": 1.2}},
            "audio": {"duration_seconds": 100},
        }
        workflow = {}

        ok, message, _ = _api_apply_continue_audio_range_override(
            result,
            workflow,
            {"start_seconds": 12, "end_seconds": 72, "speed": 1.5},
            allow_speed=True,
        )

        self.assertTrue(ok, message)
        self.assertEqual(result["plan"]["audio"]["speed"], 1.5)
        rebuilt = _apply_workflow_audio_range_override(
            {"audio": {"start_seconds": 0, "end_seconds": 100, "speed": 1.2}},
            workflow,
        )
        self.assertEqual(rebuilt["audio"]["speed"], 1.5)
        segment = audio_segment_from_plan(
            {"path": "/tmp/demo.mp3", "duration_seconds": 100}, rebuilt,
        )
        self.assertEqual(segment["speed"], 1.5)

    def test_regeneration_rejects_speed_outside_supported_range(self):
        for speed in (0.4, 2.1, "nan", "abc", None):
            with self.subTest(speed=speed):
                result = {"plan": {"audio": {"start_seconds": 12, "end_seconds": 72, "speed": 1.2}}}
                workflow = {}
                ok, message, _ = _api_apply_continue_audio_range_override(
                    result,
                    workflow,
                    {"start_seconds": 12, "end_seconds": 72, "speed": speed},
                    allow_speed=True,
                )
                self.assertFalse(ok)
                self.assertIn("倍速", message)
                self.assertEqual(result["plan"]["audio"]["speed"], 1.2)
                self.assertEqual(workflow, {})

    def test_continue_audio_override_keeps_legacy_speed_behavior(self):
        result = {"plan": {"audio": {"start_seconds": 12, "end_seconds": 72, "speed": 1.2}}}
        workflow = {}

        ok, message, audio_range = _api_apply_continue_audio_range_override(
            result,
            workflow,
            {"start_seconds": 15, "end_seconds": 70, "speed": 1.5},
        )

        self.assertTrue(ok, message)
        self.assertEqual(result["plan"]["audio"]["speed"], 1.2)
        self.assertNotIn("speed", audio_range)

    def test_publisher_video_children_use_short_inline_wait_and_deferred_resume(self):
        with patch.dict(os.environ, {
            "MP3_VIDEO_PUBLISHER_INLINE_WAIT_SECONDS": "",
            "MP3_VIDEO_PUBLISHER_PARENT_RETRY_DELAY_SECONDS": "",
            "MP3_VIDEO_PUBLISHER_PARENT_MAX_DEFERS": "",
            "MP3_VIDEO_CHILD_TIMEOUT_SECONDS": "",
            "MP3_VIDEO_PARENT_RETRY_DELAY_SECONDS": "",
            "MP3_VIDEO_PARENT_MAX_DEFERS": "",
        }, clear=False):
            self.assertEqual(_child_inline_wait_seconds("landscape_video"), 5)
            self.assertEqual(_child_inline_wait_seconds("vertical_video"), 5)
            self.assertEqual(_parent_retry_delay_seconds("landscape_video"), 60)
            self.assertEqual(_parent_max_defers("vertical_video"), 180)
            self.assertEqual(_child_inline_wait_seconds("gemini"), 2400)
            self.assertEqual(_parent_retry_delay_seconds("gemini"), 300)
            self.assertEqual(_parent_max_defers("gemini"), 144)

    def test_publisher_video_wait_settings_can_be_overridden(self):
        with patch.dict(os.environ, {
            "MP3_VIDEO_PUBLISHER_INLINE_WAIT_SECONDS": "12",
            "MP3_VIDEO_PUBLISHER_PARENT_RETRY_DELAY_SECONDS": "45",
            "MP3_VIDEO_PUBLISHER_PARENT_MAX_DEFERS": "9",
        }, clear=False):
            self.assertEqual(_child_inline_wait_seconds("landscape_video"), 12)
            self.assertEqual(_parent_retry_delay_seconds("vertical_video"), 45)
            self.assertEqual(_parent_max_defers("landscape_video"), 9)


if __name__ == "__main__":
    unittest.main()
