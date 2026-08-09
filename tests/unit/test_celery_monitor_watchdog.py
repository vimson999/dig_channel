import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
STOP_WATCHDOG_SCRIPT = PROJECT_ROOT / "scripts" / "dev" / "stop-celery-watchdog.sh"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from apis.v1 import celery_monitor  # noqa: E402


class CeleryMonitorWatchdogTest(unittest.TestCase):
    def test_watchdog_stop_script_uses_an_anchored_process_match(self):
        text = STOP_WATCHDOG_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("celery_watchdog_loop[.]py$", text)
        self.assertNotIn("grep 'celery_watchdog_loop.py'", text)

    def test_all_tabs_auto_sync_is_visible_as_a_named_zsxq_business_task(self):
        row = SimpleNamespace(
            id="run-1",
            group_id="1824528822",
            group_name="生财有术",
            tab_label=None,
            mode="auto_all_tabs_sc",
            status="queued",
            system_batch_id=None,
            topics_fetched=0,
            attachments_downloaded=0,
            attachments_failed=0,
            error_message=None,
            created_at=datetime(2026, 7, 24, 1, 0, 0),
            updated_at=datetime(2026, 7, 24, 1, 0, 0),
            completed_at=None,
        )

        payload = celery_monitor._zsxq_sync_task_payload(row)

        self.assertEqual(payload["feature"], "zsxq_sync_run")
        self.assertEqual(payload["status"], "pending")
        self.assertIn("全部 Tab 定时同步", payload["title"])
        self.assertIn("全部 Tab 定时同步", payload["step"])

    def test_retrying_zsxq_sync_remains_visible_as_processing(self):
        self.assertIn("retrying", celery_monitor.ZSXQ_ACTIVE_STATUSES)
        self.assertEqual(
            celery_monitor._zsxq_business_status("retrying"),
            "processing",
        )

    def test_recent_or_runtime_owned_all_tabs_run_is_not_auto_closed(self):
        now = datetime.utcnow()
        recent = SimpleNamespace(
            id="run-recent",
            mode="auto_all_tabs_sc",
            status="running",
            created_at=now,
            updated_at=now,
        )
        active = SimpleNamespace(
            id="run-active",
            mode="auto_all_tabs_sc",
            status="running",
            created_at=now - timedelta(hours=1),
            updated_at=now - timedelta(minutes=20),
        )

        self.assertEqual(
            celery_monitor._zsxq_all_tabs_orphan_candidates([recent], now=now),
            [],
        )
        with patch.object(
            celery_monitor,
            "_zsxq_celery_runtime_refs",
            return_value=({"run-active"}, None),
        ), patch(
            "services.zsxq_sync_service.zsxq_sync_service.mark_run_failed",
        ) as mark_failed:
            remaining, error = celery_monitor._auto_close_orphaned_zsxq_all_tabs_runs(
                [active]
            )

        self.assertIsNone(error)
        self.assertEqual(remaining, [active])
        mark_failed.assert_not_called()

    def test_orphaned_all_tabs_run_is_auto_closed_after_runtime_verification(self):
        now = datetime.utcnow()
        stale = SimpleNamespace(
            id="run-orphan",
            mode="auto_all_tabs_sc",
            status="running",
            created_at=now - timedelta(hours=1),
            updated_at=now - timedelta(minutes=20),
        )

        with patch.object(
            celery_monitor,
            "_zsxq_celery_runtime_refs",
            return_value=(set(), None),
        ), patch(
            "services.zsxq_sync_service.zsxq_sync_service.mark_run_failed",
        ) as mark_failed:
            remaining, error = celery_monitor._auto_close_orphaned_zsxq_all_tabs_runs(
                [stale]
            )

        self.assertIsNone(error)
        self.assertEqual(remaining, [])
        mark_failed.assert_called_once()
        self.assertEqual(mark_failed.call_args.args[0], "run-orphan")
        result_patch = mark_failed.call_args.args[2]
        self.assertTrue(result_patch["interrupted"])
        self.assertEqual(
            result_patch["stale_cleanup"]["auto_cleaned_by"],
            "celery_monitor",
        )

    def test_runtime_inspection_failure_never_auto_closes_zsxq_run(self):
        now = datetime.utcnow()
        stale = SimpleNamespace(
            id="run-unknown",
            mode="auto_all_tabs_hh",
            status="retrying",
            created_at=now - timedelta(hours=1),
            updated_at=now - timedelta(minutes=20),
        )

        with patch.object(
            celery_monitor,
            "_zsxq_celery_runtime_refs",
            return_value=(set(), "inspect unavailable"),
        ), patch(
            "services.zsxq_sync_service.zsxq_sync_service.mark_run_failed",
        ) as mark_failed:
            remaining, error = celery_monitor._auto_close_orphaned_zsxq_all_tabs_runs(
                [stale]
            )

        self.assertEqual(error, "inspect unavailable")
        self.assertEqual(remaining, [stale])
        mark_failed.assert_not_called()

    def test_browser_child_features_are_owned_by_their_worker_services(self):
        self.assertEqual(
            celery_monitor.BUSINESS_TASK_FEATURES["doubao_browser_tts"],
            ("doubao_browser_tts",),
        )
        self.assertIn(
            "report_stick_figure_image",
            celery_monitor.BUSINESS_TASK_FEATURES["report_pdf_gemini"],
        )

    def test_script_material_workflow_service_is_registered(self):
        service = celery_monitor.SERVICES["report_script_material_workflow"]

        self.assertEqual(service["queue"], "report_script_material_workflow")
        self.assertEqual(
            celery_monitor.BUSINESS_TASK_FEATURES["report_script_material_workflow"],
            ("report_script_material_workflow",),
        )
        self.assertIn("report_script_material_workflow", celery_monitor.DEFAULT_WATCHDOG_SERVICES)

    def test_stick_figure_video_is_managed_by_render_service(self):
        service = celery_monitor.SERVICES["render"]

        self.assertEqual(service["queue"], "render")
        self.assertEqual(service["run_script"], "scripts/dev/run-celery.sh")
        self.assertIn(
            "report_stick_figure_video_render",
            celery_monitor.BUSINESS_TASK_FEATURES["render"],
        )
        self.assertIn(
            "report_stick_figure_audio_alignment",
            celery_monitor.BUSINESS_TASK_FEATURES["render"],
        )
        self.assertIn("render", celery_monitor.DEFAULT_WATCHDOG_SERVICES)

    def test_platform_upload_service_tracks_upload_only_publisher_tasks(self):
        row = SimpleNamespace(
            feature="publisher",
            current_step="upload_bilibili",
            params={
                "metadata": {"source": "mp3_video_workflow:bilibili_upload"},
                "video": {"enabled": False, "media_type": "existing_video"},
                "bilibili": {"enabled": True},
            },
        )

        self.assertTrue(
            celery_monitor._business_task_matches_service(
                row,
                "platform_upload",
                celery_monitor.BUSINESS_TASK_FEATURES["platform_upload"],
            )
        )
        self.assertFalse(
            celery_monitor._business_task_matches_service(
                row,
                "render",
                celery_monitor.BUSINESS_TASK_FEATURES["render"],
            )
        )

    def test_platform_upload_stop_guard_blocks_processing_upload_task(self):
        rows = [
            SimpleNamespace(
                id="upload-1",
                status="processing",
                current_step="upload_bilibili",
                progress=10,
                task_title="内资闭门会 | 测试",
                updated_at=datetime.utcnow(),
            )
        ]

        active = celery_monitor._active_non_interruptible_service_tasks("platform_upload", rows=rows)

        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["id"], "upload-1")
        self.assertEqual(active[0]["step"], "upload_bilibili")

    def test_zsxq_stop_guard_blocks_fresh_running_sync_but_ignores_orphan(self):
        now = datetime.utcnow()
        rows = [
            SimpleNamespace(
                id="zsxq-live",
                group_id="1824528822",
                group_name="生财有术",
                mode="auto_all_tabs_sc",
                status="running",
                updated_at=now - timedelta(seconds=30),
            ),
            SimpleNamespace(
                id="zsxq-orphan",
                group_id="1824528822",
                group_name="生财有术",
                mode="auto_all_tabs_sc",
                status="running",
                updated_at=now - timedelta(minutes=20),
            ),
        ]

        active = celery_monitor._active_zsxq_sync_guard_tasks(
            rows=rows,
            now=now,
        )

        self.assertEqual([item["id"] for item in active], ["zsxq-live"])
        guard = celery_monitor._service_stop_guard("zsxq_sync", rows=rows)
        self.assertEqual(guard["reason"], "active_non_interruptible_tasks")
        self.assertEqual(
            [item["id"] for item in guard["active_tasks"]],
            ["zsxq-live"],
        )

    def test_mp3_stop_guard_blocks_real_work_but_allows_waiting_state(self):
        now = datetime.utcnow()
        rows = [
            SimpleNamespace(
                id="mp3-running",
                status="processing",
                current_step="direct_ds_plan",
                progress=58,
                task_title="调研纪要",
                updated_at=now,
            ),
            SimpleNamespace(
                id="mp3-waiting",
                status="processing",
                current_step="landscape_video_waiting",
                progress=80,
                task_title="等待横版视频",
                updated_at=now,
            ),
            SimpleNamespace(
                id="mp3-queued",
                status="processing",
                current_step="storyboard_queued",
                progress=35,
                task_title="等待分镜 worker",
                updated_at=now,
            ),
        ]

        active = celery_monitor._active_non_interruptible_service_tasks(
            "mp3_video_workflow",
            rows=rows,
        )

        self.assertEqual([item["id"] for item in active], ["mp3-running"])

    def test_restart_all_preflight_is_atomic(self):
        app = Flask(__name__)
        blockers = {
            "mp3_video_workflow": {
                "skipped": True,
                "reason": "active_non_interruptible_tasks",
                "active_tasks": [{"id": "mp3-running"}],
            }
        }
        with app.test_request_context(json={}), patch.object(
            celery_monitor,
            "_restart_all_stop_guards",
            return_value=blockers,
        ), patch.object(celery_monitor, "_stop_watchdog") as stop_watchdog:
            response, status = celery_monitor.restart_all()

        self.assertEqual(status, 409)
        self.assertFalse(response.get_json()["success"])
        self.assertEqual(response.get_json()["data"]["stop_guards"], blockers)
        stop_watchdog.assert_not_called()

    def test_restart_all_reuses_successful_preflight_without_per_service_db_guard(self):
        app = Flask(__name__)
        services = {
            "worker-a": {
                "label": "Worker A",
                "queue": "worker-a",
                "run_script": "scripts/dev/run-celery.sh",
            }
        }
        with app.test_request_context(json={}), patch.object(
            celery_monitor,
            "SERVICES",
            services,
        ), patch.object(celery_monitor, "SCHEDULERS", {}), patch.object(
            celery_monitor,
            "_restart_all_stop_guards",
            return_value={},
        ), patch.object(celery_monitor, "_stop_watchdog", return_value={}), patch.object(
            celery_monitor,
            "_start_watchdog",
            return_value={"started": True},
        ), patch.object(
            celery_monitor,
            "_watchdog_status_payload",
            return_value={"running": True},
        ), patch.object(
            celery_monitor,
            "_load_paused_services",
            return_value={},
        ), patch.object(
            celery_monitor,
            "_stop_service",
            return_value={"terminated_pids": [11], "force_killed_pids": []},
        ) as stop_service, patch.object(
            celery_monitor,
            "_start_service",
            return_value={"started": True, "starter_pid": 12},
        ), patch.object(celery_monitor.time, "sleep"):
            response = celery_monitor.restart_all()

        self.assertTrue(response.get_json()["success"])
        stop_service.assert_called_once_with("worker-a", services["worker-a"], force=True)
        summary = response.get_json()["data"]["summary"]
        self.assertEqual(summary["celery_started"], 1)
        self.assertEqual(summary["celery_online"], 1)
        self.assertEqual(summary["celery_skipped"], 0)

    def test_stop_guard_retries_transient_db_error_after_releasing_session(self):
        from models import db
        from models.task import PublicationTask

        app = Flask(__name__)
        app.config.update(
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(app)
        transient = RuntimeError("server closed the connection unexpectedly")
        query = SimpleNamespace(calls=0)

        def chain(*_args, **_kwargs):
            return query

        def all_rows():
            query.calls += 1
            if query.calls == 1:
                raise transient
            return []

        query.with_entities = chain
        query.filter = chain
        query.order_by = chain
        query.limit = chain
        query.all = all_rows

        with app.app_context():
            with patch.object(PublicationTask, "query", query), patch.object(
                celery_monitor,
                "_release_db_session_quietly",
            ) as release_session:
                rows = celery_monitor._query_non_interruptible_rows_with_retry(
                    ("content_pdf_clean",),
                    include_feature=False,
                    limit=8,
                )

        self.assertEqual(rows, [])
        self.assertEqual(query.calls, 2)
        release_session.assert_called_once()

    def test_stop_service_releases_db_session_before_process_wait(self):
        service = {"label": "MP3", "queue": "mp3_video_workflow"}

        with tempfile.TemporaryDirectory() as tmpdir, patch.object(
            celery_monitor,
            "_ensure_dirs",
        ), patch.object(
            celery_monitor,
            "_service_stop_guard",
            return_value=None,
        ), patch.object(
            celery_monitor,
            "_release_db_session_quietly",
        ) as release_session, patch.object(
            celery_monitor,
            "_service_processes",
            side_effect=lambda _service: (release_session.assert_called_once(), [])[1],
        ), patch.object(
            celery_monitor,
            "_read_pid_file",
            return_value=None,
        ), patch.object(
            celery_monitor,
            "_pid_file",
            return_value=Path(tmpdir) / "mp3.pid",
        ):
            result = celery_monitor._stop_service("mp3_video", service)

        self.assertEqual(result["terminated_pids"], [])

    def test_guarded_stop_releases_db_session_before_returning(self):
        guard = {
            "skipped": True,
            "reason": "active_non_interruptible_tasks",
        }

        with patch.object(celery_monitor, "_ensure_dirs"), patch.object(
            celery_monitor,
            "_service_stop_guard",
            return_value=guard,
        ), patch.object(
            celery_monitor,
            "_release_db_session_quietly",
        ) as release_session, patch.object(
            celery_monitor,
            "_service_processes",
        ) as service_processes:
            result = celery_monitor._stop_service("mp3_video", {})

        release_session.assert_called_once()
        service_processes.assert_not_called()
        self.assertTrue(result["skipped"])

    def test_scheduler_start_passes_the_canonical_project_root(self):
        scheduler = {"label": "ZSXQ", "argv": ["python", "scheduler.py"]}
        process = SimpleNamespace(pid=1234, poll=lambda: None)
        with tempfile.TemporaryDirectory() as tmpdir, patch.object(
            celery_monitor,
            "PROJECT_ROOT",
            Path(tmpdir),
        ), patch.object(
            celery_monitor,
            "_scheduler_processes",
            return_value=[],
        ), patch.object(
            celery_monitor,
            "_scheduler_log_path",
            return_value=Path(tmpdir) / "scheduler.log",
        ), patch.object(
            celery_monitor.subprocess,
            "Popen",
            return_value=process,
        ) as popen, patch.object(celery_monitor.time, "sleep"):
            celery_monitor._start_scheduler("zsxq_auto", scheduler)
            expected_project_root = str(celery_monitor.PROJECT_ROOT)

        env = popen.call_args.kwargs["env"]
        self.assertEqual(env["ZSXQ_PROJECT_ROOT"], expected_project_root)
        self.assertEqual(env["SCHEDULER_STDOUT_REDIRECTED_TO_LOG"], "1")

    def test_scheduler_process_matching_requires_exact_script_argument(self):
        scheduler = {"pattern": "run_youtube_video_sync_scheduler.py"}
        processes = [
            {
                "pid": 100,
                "etime": "00:10",
                "command": (
                    "/project/venv/bin/python "
                    "/project/src/backend/run_youtube_video_sync_scheduler.py start"
                ),
            },
            {
                "pid": 101,
                "etime": "00:01",
                "command": (
                    "/bin/zsh -c 'rg run_youtube_video_sync_scheduler.py logs'"
                ),
            },
        ]
        with patch.object(
            celery_monitor,
            "_list_processes",
            return_value=processes,
        ):
            matched = celery_monitor._scheduler_processes(scheduler)

        self.assertEqual([item["pid"] for item in matched], [100])

    def test_single_restart_does_not_start_after_guarded_stop(self):
        app = Flask(__name__)
        guarded_stop = {
            "skipped": True,
            "reason": "active_non_interruptible_tasks",
            "message": "任务仍在运行",
        }
        with app.test_request_context(json={}), patch.object(
            celery_monitor,
            "_stop_service",
            return_value=guarded_stop,
        ), patch.object(celery_monitor, "_start_service") as start_service:
            response, status = celery_monitor.restart("mp3_video_workflow")

        self.assertEqual(status, 409)
        self.assertFalse(response.get_json()["success"])
        start_service.assert_not_called()

    def test_runtime_refs_include_redis_unacked_messages(self):
        task_id = "06431ca1-b532-425e-a84f-a47827e4b50f"
        client = SimpleNamespace(
            llen=lambda _queue: 0,
            lrange=lambda _queue, _start, _end: [],
            hlen=lambda _key: 1,
            hscan_iter=lambda _key, count: iter([(b"delivery-tag", task_id.encode())]),
        )
        with patch.object(
            celery_monitor.redis.Redis,
            "from_url",
            return_value=client,
        ), patch.object(
            celery_monitor,
            "_inspect_tasks_by_queue",
            return_value=({}, None),
        ):
            refs, error = celery_monitor._business_task_runtime_refs()

        self.assertIsNone(error)
        self.assertIn(task_id, refs)

    def test_celery_runtime_refs_include_zsxq_worker_tasks(self):
        run_id = "5f908f6f-7a64-41b3-80a1-f9c537b9c0f6"
        inspect_data = {
            "zsxq_sync": {
                "active": [{
                    "id": "celery-task-id",
                    "name": "tasks.zsxq_sync.run_zsxq_sync",
                    "args": [run_id, {"count": 40}],
                }],
                "reserved": [],
                "scheduled": [],
            },
        }
        with patch.object(
            celery_monitor,
            "_inspect_tasks_by_queue",
            return_value=(inspect_data, None),
        ):
            refs, error = celery_monitor._zsxq_celery_runtime_refs()

        self.assertIsNone(error)
        self.assertIn(run_id, refs)

    def _status(self, business_tasks):
        service = celery_monitor.SERVICES["report_audio_workflow"]
        return celery_monitor._service_status(
            "report_audio_workflow",
            service,
            inspect_data={
                "report_audio_workflow": {
                    "active": [],
                    "reserved": [],
                    "scheduled": [],
                }
            },
            inspect_error=None,
            queue_lengths={"report_audio_workflow": (10, None)},
            all_processes=[
                {
                    "pid": 12345,
                    "etime": "01:00",
                    "command": "celery -A celery_config.celery_app worker -Q report_audio_workflow --hostname=report_audio_workflow@host",
                }
            ],
            business_tasks=business_tasks,
            business_task_error=None,
        )

    def test_recent_business_task_suppresses_solo_inspect_false_warning(self):
        status = self._status([
            {
                "id": "job-1",
                "status": "processing",
                "step": "pub_queued",
                "progress": 95,
                "updated_at": datetime.utcnow().isoformat() + "Z",
            }
        ])

        self.assertIsNone(status["consumer_warning"])
        self.assertIn("solo Worker", status["consumer_note"])

    def test_business_heartbeat_query_failure_suppresses_destructive_warning(self):
        service = celery_monitor.SERVICES["report_audio_workflow"]
        status = celery_monitor._service_status(
            "report_audio_workflow",
            service,
            inspect_data={
                "report_audio_workflow": {
                    "active": [],
                    "reserved": [],
                    "scheduled": [],
                }
            },
            inspect_error=None,
            queue_lengths={"report_audio_workflow": (10, None)},
            all_processes=[
                {
                    "pid": 12345,
                    "etime": "01:00",
                    "command": "celery -A celery_config.celery_app worker -Q report_audio_workflow --hostname=report_audio_workflow@host",
                }
            ],
            business_tasks=[],
            business_task_error="database unavailable",
        )

        self.assertIsNone(status["consumer_warning"])
        self.assertIn("禁止告警和自动重启", status["consumer_note"])

    def test_stale_or_pending_business_tasks_still_warn(self):
        old = datetime.utcnow() - timedelta(seconds=celery_monitor.BUSINESS_TASK_CONSUMER_GRACE_SECONDS_DEFAULT + 120)
        status = self._status([
            {
                "id": "job-1",
                "status": "processing",
                "step": "gem_wait_browser_pro",
                "progress": 11,
                "updated_at": old.isoformat() + "Z",
            },
            {
                "id": "job-2",
                "status": "pending",
                "step": "queued",
                "progress": 20,
                "updated_at": datetime.utcnow().isoformat() + "Z",
            },
        ])

        self.assertIn("Worker 在线但未领取积压任务", status["consumer_warning"])
        self.assertIsNone(status["consumer_note"])

    def test_skipped_inspect_does_not_suppress_consumer_warning(self):
        service = celery_monitor.SERVICES["report_script_material_workflow"]
        status = celery_monitor._service_status(
            "report_script_material_workflow",
            service,
            inspect_data={},
            inspect_error="skipped",
            queue_lengths={"report_script_material_workflow": (7, None)},
            all_processes=[
                {
                    "pid": 12345,
                    "etime": "01:00",
                    "command": "celery -A celery_config.celery_app worker -Q report_script_material_workflow --hostname=report_script_material_workflow@host",
                }
            ],
            business_tasks=[
                {
                    "id": "job-1",
                    "status": "pending",
                    "step": "queued",
                    "progress": 0,
                    "updated_at": datetime.utcnow().isoformat() + "Z",
                }
            ],
            business_task_error=None,
        )

        self.assertEqual(status["inspect_error"], "skipped")
        self.assertIn("Worker 在线但未领取积压任务", status["consumer_warning"])

    def test_paused_services_are_excluded_from_stale_task_repair(self):
        with patch.object(
            celery_monitor,
            "_load_paused_services",
            return_value={"content_pipeline": {"reason": "manual_pause"}},
        ):
            self.assertEqual(
                celery_monitor._paused_business_features(),
                {"content_pdf_clean", "content_audio_clean"},
            )

    def test_watchdog_passes_business_tasks_into_service_status(self):
        captured = {}

        def fake_service_status(service_id, service, **kwargs):
            captured["service_id"] = service_id
            captured["business_tasks"] = kwargs.get("business_tasks")
            captured["business_task_error"] = kwargs.get("business_task_error")
            return {
                "consumer_warning": None,
                "queue_length": 1,
            }

        with patch.object(celery_monitor, "_ensure_dirs"), \
            patch.object(celery_monitor, "_watchdog_config", return_value={
                "enabled": True,
                "notify_enabled": False,
                "requeue_stale_tasks": True,
                "services": ["render"],
                "confirmations": 3,
                "cooldown_seconds": 900,
            }), \
            patch.object(celery_monitor, "_load_watchdog_state", return_value={"services": {}, "events": []}), \
            patch.object(celery_monitor, "_save_watchdog_state"), \
            patch.object(celery_monitor, "_watchdog_repair_stale_business_tasks", return_value={"requeued": 0}), \
            patch.object(celery_monitor, "_inspect_tasks_by_queue", return_value=({"render": {"active": [], "reserved": [], "scheduled": []}}, None)), \
            patch.object(celery_monitor, "_business_tasks_snapshot") as snapshot, \
            patch.object(celery_monitor, "_load_paused_services", return_value={}), \
            patch.object(celery_monitor, "_service_status", side_effect=fake_service_status):
            result = celery_monitor.run_watchdog_once()

        self.assertEqual(result["results"][0]["service_id"], "render")
        self.assertEqual(captured["service_id"], "render")
        self.assertEqual(captured["business_tasks"], [])
        self.assertIsNone(captured["business_task_error"])
        snapshot.assert_not_called()

    def test_watchdog_probes_recent_business_heartbeat_before_warning(self):
        calls = []

        def fake_service_status(service_id, service, **kwargs):
            calls.append(kwargs.get("business_tasks"))
            if kwargs.get("business_tasks"):
                return {
                    "consumer_warning": None,
                    "consumer_note": "solo Worker 正在处理任务",
                    "queue_length": 44,
                }
            return {
                "consumer_warning": "Worker 在线但未领取积压任务",
                "consumer_note": None,
                "queue_length": 44,
            }

        active = [{
            "id": "image-1",
            "status": "processing",
            "step": "prepare_browser",
            "progress": 3,
            "updated_at": datetime.utcnow().isoformat() + "Z",
        }]
        with patch.object(celery_monitor, "_ensure_dirs"), patch.object(
            celery_monitor,
            "_watchdog_config",
            return_value={
                "enabled": True,
                "notify_enabled": True,
                "requeue_stale_tasks": True,
                "services": ["report_pdf_gemini"],
                "confirmations": 1,
                "cooldown_seconds": 0,
            },
        ), patch.object(
            celery_monitor,
            "_load_watchdog_state",
            return_value={"services": {}, "events": []},
        ), patch.object(
            celery_monitor,
            "_save_watchdog_state",
        ), patch.object(
            celery_monitor,
            "_watchdog_repair_stale_business_tasks",
            return_value={"requeued": 0},
        ), patch.object(
            celery_monitor,
            "_inspect_tasks_by_queue",
            return_value=({
                "report_pdf_gemini": {
                    "active": [],
                    "reserved": [],
                    "scheduled": [],
                }
            }, None),
        ), patch.object(
            celery_monitor,
            "_load_paused_services",
            return_value={},
        ), patch.object(
            celery_monitor,
            "_watchdog_business_activity_probe",
            return_value=(active, None),
        ) as heartbeat_probe, patch.object(
            celery_monitor,
            "_service_status",
            side_effect=fake_service_status,
        ), patch.object(
            celery_monitor,
            "_watchdog_notify",
        ) as notify, patch.object(
            celery_monitor,
            "_stop_service",
        ) as stop_service:
            result = celery_monitor.run_watchdog_once()

        self.assertEqual(calls, [[], active])
        heartbeat_probe.assert_called_once_with("report_pdf_gemini")
        notify.assert_not_called()
        stop_service.assert_not_called()
        self.assertEqual(result["results"][0]["action"], "none")

    def test_watchdog_does_not_claim_restart_when_stop_guard_skips(self):
        warning_status = {
            "consumer_warning": "Worker 在线但未领取积压任务",
            "consumer_note": None,
            "queue_length": 44,
        }
        state = {"services": {}, "events": []}
        guarded_stop = {
            "skipped": True,
            "reason": "active_non_interruptible_tasks",
            "active_tasks": [{"id": "image-1"}],
        }
        with patch.object(celery_monitor, "_ensure_dirs"), patch.object(
            celery_monitor,
            "_watchdog_config",
            return_value={
                "enabled": True,
                "notify_enabled": False,
                "requeue_stale_tasks": True,
                "services": ["report_pdf_gemini"],
                "confirmations": 1,
                "cooldown_seconds": 0,
            },
        ), patch.object(
            celery_monitor,
            "_load_watchdog_state",
            return_value=state,
        ), patch.object(
            celery_monitor,
            "_save_watchdog_state",
        ), patch.object(
            celery_monitor,
            "_watchdog_repair_stale_business_tasks",
            return_value={"requeued": 0},
        ), patch.object(
            celery_monitor,
            "_inspect_tasks_by_queue",
            return_value=({}, None),
        ), patch.object(
            celery_monitor,
            "_load_paused_services",
            return_value={},
        ), patch.object(
            celery_monitor,
            "_watchdog_business_activity_probe",
            return_value=([], None),
        ), patch.object(
            celery_monitor,
            "_service_status",
            return_value=warning_status,
        ), patch.object(
            celery_monitor,
            "_stop_service",
            return_value=guarded_stop,
        ), patch.object(
            celery_monitor,
            "_start_service",
        ) as start_service:
            result = celery_monitor.run_watchdog_once()

        start_service.assert_not_called()
        self.assertEqual(result["results"][0]["action"], "restart_skipped")
        events = [
            item for item in state["events"]
            if item["service_id"] == "report_pdf_gemini"
        ]
        self.assertEqual(events[-1]["event_type"], "restart_skipped")
        self.assertNotIn("restarted", [item["event_type"] for item in events])

    def test_paused_render_skips_publisher_repair(self):
        with patch.object(
            celery_monitor,
            "_load_paused_services",
            return_value={"render": {"reason": "manual_pause"}},
        ), patch(
            "services.publisher_queue_repair.cleanup_publisher_children_of_terminal_parents_once"
        ) as cleanup_publisher, patch(
            "services.publisher_queue_repair.repair_stale_publisher_pending_tasks_once"
        ) as repair_pending, patch(
            "services.publisher_queue_repair.repair_stale_publisher_processing_tasks_once"
        ) as repair_processing, patch(
            "services.publisher_queue_repair.repair_stale_publication_tasks_once",
            return_value={"enabled": True, "requeued": 0, "items": []},
        ) as repair_generic, patch(
            "services.publisher_queue_repair.repair_stale_business_processing_tasks_once",
            return_value={"enabled": True, "requeued": 0, "closed": 0, "items": []},
        ):
            result = celery_monitor._watchdog_repair_stale_business_tasks({
                "enabled": True,
                "requeue_stale_tasks": True,
            })

        cleanup_publisher.assert_not_called()
        repair_pending.assert_not_called()
        repair_processing.assert_not_called()
        repair_generic.assert_called_once_with(
            excluded_features={
                "publisher",
                "report_stick_figure_video_render",
                "report_stick_figure_audio_alignment",
            }
        )
        self.assertEqual(result["publisher_pending_repair"]["skipped"], "publisher_paused")
        self.assertEqual(result["processing_repair"]["skipped"], "publisher_paused")


if __name__ == "__main__":
    unittest.main()
