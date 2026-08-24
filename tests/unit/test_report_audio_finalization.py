import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from flask import Flask
from sqlalchemy import event


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from models import db  # noqa: E402
from models.task import PublicationTask  # noqa: E402
from services import report_audio_finalization as finalization_service  # noqa: E402
from services.report_audio_workflow import FEATURE_NAME as REPORT_AUDIO_FEATURE  # noqa: E402


class ReportAudioFinalizationTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        self.app = Flask(__name__)
        self.app.config.update(
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(self.app)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        self.probe_patch = patch.object(
            finalization_service,
            "COMPLETION_MEDIA_PROBE",
            side_effect=lambda path: (
                {"duration": 12.0, "video_streams": 0, "audio_streams": 1}
                if str(path).endswith(".wav")
                else {"duration": 12.0, "video_streams": 1, "audio_streams": 1}
            ),
        )
        self.probe_patch.start()

    def tearDown(self):
        self.probe_patch.stop()
        db.session.remove()
        db.drop_all()
        self.ctx.pop()
        self.tmpdir.cleanup()

    def _audio_parent(self):
        audio_path = self.root / "final.wav"
        pdf_path = self.root / "report.pdf"
        screenshot_path = self.root / "page.jpg"
        audio_path.write_bytes(b"RIFF----WAVE")
        pdf_path.write_bytes(b"%PDF-1.4")
        screenshot_path.write_bytes(b"jpeg")
        parent = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            feature=REPORT_AUDIO_FEATURE,
            user_id="user-1",
            params={
                "feature": REPORT_AUDIO_FEATURE,
                "user_id": "user-1",
                "report_audio_workflow": {
                    "pdf": {"filename": "report.pdf", "path": str(pdf_path), "bytes": 8},
                    "video": {"render_mode": "keyframes", "enabled": True},
                },
            },
            result={
                "audio": {"wav_path": str(audio_path), "seconds": 12.0},
                "structured_result": {
                    "final_script_for_tts": "这是一段用于测试自动续链的完整口播文案。",
                    "cover_title": "测试标题",
                },
                "pdf_first_page_image": {"path": str(screenshot_path), "bytes": 4},
                "video": {"render_mode": "keyframes"},
            },
        )
        db.session.add(parent)
        db.session.commit()
        return parent

    def test_confirmation_persists_complete_intent_and_is_idempotent_while_visual_runs(self):
        parent = self._audio_parent()
        active_visual = {
            "workflow_job_id": "visual-1",
            "visual_scheme": {"key": "stick_figure"},
            "status": "running",
            "step": "images_queued",
            "progress": 45,
            "scheme3_ready": False,
        }
        with (
            patch.object(finalization_service, "_visual_candidates", side_effect=[[], [active_visual]]),
            patch.object(
                finalization_service,
                "_dispatch_visual",
                return_value={"workflow_job_id": "visual-1", "status": "storyboard_queued"},
            ) as dispatch,
        ):
            first = finalization_service.create_or_resume_finalization(parent.id)
            second = finalization_service.create_or_resume_finalization(parent.id)

        self.assertEqual(first.id, second.id)
        self.assertEqual(dispatch.call_count, 1)
        rows = PublicationTask.query.filter_by(
            feature=finalization_service.FEATURE_NAME,
            source_task_id=parent.id,
        ).all()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].status, "processing")
        self.assertEqual(rows[0].current_step, "waiting_visual")
        options = rows[0].params[finalization_service.INTENT_KEY]
        self.assertTrue(options["include_landscape"])
        self.assertEqual(options["vertical_video_schemes"], ["scheme3"])
        self.assertFalse(PublicationTask.query.filter_by(feature="publisher").count())

    def test_ready_visual_queues_one_combined_publisher_and_completion_closes_intent(self):
        parent = self._audio_parent()
        whiteboard = self.root / "whiteboard.mp4"
        whiteboard.write_bytes(b"whiteboard")
        ready_visual = {
            "workflow_job_id": "visual-ready",
            "visual_scheme": {"key": "stick_figure"},
            "status": "ready",
            "step": "images_done",
            "progress": 75,
            "scheme3_ready": True,
        }
        created_children = []

        def create_child(audio_parent, **kwargs):
            child = PublicationTask(
                status="pending",
                current_step="queued",
                progress=0,
                feature="publisher",
                source_task_id=str(audio_parent.id),
                params={
                    "feature": "publisher",
                    "source_task_id": str(audio_parent.id),
                    "video": {"enabled": True},
                    "vertical_video": {"enabled": True, "scheme": "scheme3"},
                    "metadata": {
                        "source": "report_audio_workflow_vertical_backfill",
                        "include_landscape": True,
                        "vertical_video_schemes": ["scheme3"],
                        "finalization_task_id": kwargs["finalization_task_id"],
                    },
                },
                result={},
            )
            db.session.add(child)
            db.session.commit()
            created_children.append(str(child.id))
            return child

        with (
            patch.object(finalization_service, "_visual_candidates", return_value=[ready_visual]),
            patch.object(
                finalization_service,
                "_visual_inputs",
                return_value={"scene_timeline": {"scenes": [{"sequence": 1}]}, "video_path": str(whiteboard)},
            ),
            patch(
                "api.report_audio_workflow._create_vertical_video_backfill_task",
                side_effect=create_child,
            ) as create_publisher,
        ):
            first = finalization_service.create_or_resume_finalization(parent.id)
            second = finalization_service.create_or_resume_finalization(parent.id)

        self.assertEqual(first.id, second.id)
        self.assertEqual(create_publisher.call_count, 1)
        self.assertEqual(len(created_children), 1)
        coordinator = db.session.get(PublicationTask, first.id)
        self.assertEqual(coordinator.current_step, "final_video_queued")
        self.assertEqual(coordinator.result["publisher_task_id"], created_children[0])

        landscape = self.root / "landscape.mp4"
        vertical = self.root / "scheme3.mp4"
        landscape.write_bytes(b"landscape")
        vertical.write_bytes(b"vertical")
        child = db.session.get(PublicationTask, created_children[0])
        child.status = "completed"
        child.current_step = "done"
        child.progress = 100
        child.task_video_path = str(landscape)
        child.publisher_vertical_video_path = str(vertical)
        child.result = {
            "video_path": str(landscape),
            "vertical_video_path": str(vertical),
            "vertical_video_paths": {"scheme3": str(vertical)},
        }
        db.session.commit()

        with (
            patch.object(finalization_service, "_visual_candidates", return_value=[ready_visual]),
            patch.object(
                finalization_service,
                "_visual_inputs",
                return_value={"scene_timeline": {"scenes": [{"sequence": 1}]}, "video_path": str(whiteboard)},
            ),
        ):
            result = finalization_service.reconcile_finalization(first.id)

        coordinator = db.session.get(PublicationTask, first.id)
        self.assertEqual(result["reason"], "completed")
        self.assertEqual(coordinator.status, "completed")
        self.assertEqual(coordinator.current_step, "done")
        self.assertEqual(coordinator.progress, 100)
        self.assertEqual(coordinator.result["whiteboard_video_path"], str(whiteboard))
        self.assertTrue(coordinator.result["completion_contract"]["ready"])
        self.assertEqual(coordinator.result["completion_contract"]["missing"], [])

    def test_combined_outputs_do_not_close_intent_before_whiteboard_mp4_exists(self):
        parent = self._audio_parent()
        landscape = self.root / "landscape.mp4"
        vertical = self.root / "scheme3.mp4"
        landscape.write_bytes(b"landscape")
        vertical.write_bytes(b"vertical")
        coordinator = PublicationTask(
            status="processing",
            current_step="final_video_queued",
            progress=80,
            feature=finalization_service.FEATURE_NAME,
            user_id="user-1",
            parent_task_id=str(parent.id),
            source_task_id=str(parent.id),
            params={
                "feature": finalization_service.FEATURE_NAME,
                "user_id": "user-1",
                "parent_task_id": str(parent.id),
                "source_task_id": str(parent.id),
                "parent_feature": REPORT_AUDIO_FEATURE,
                "source_feature": REPORT_AUDIO_FEATURE,
                finalization_service.INTENT_KEY: finalization_service._intent_options(
                    include_landscape=True,
                    schemes=["scheme3"],
                    visual_scheme_key="stick_figure",
                    selection_source="test",
                ),
            },
            result={
                "visual_workflow_id": "visual-ready",
                "whiteboard_attempts": 0,
                "publisher_attempts": 1,
            },
        )
        db.session.add(coordinator)
        db.session.flush()
        publisher = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            feature="publisher",
            source_task_id=str(parent.id),
            task_video_path=str(landscape),
            publisher_preview_seconds=0,
            publisher_landscape_enabled=True,
            publisher_vertical_enabled=True,
            publisher_vertical_scheme="scheme3",
            publisher_vertical_video_path=str(vertical),
            params={
                "feature": "publisher",
                "metadata": {"finalization_task_id": str(coordinator.id)},
            },
            result={
                "video_path": str(landscape),
                "vertical_video_path": str(vertical),
                "vertical_video_paths": {"scheme3": str(vertical)},
            },
        )
        db.session.add(publisher)
        db.session.flush()
        coordinator.result = {
            **coordinator.result,
            "publisher_task_id": str(publisher.id),
        }
        db.session.commit()
        ready_visual = {
            "workflow_job_id": "visual-ready",
            "visual_scheme": {"key": "stick_figure"},
            "status": "ready",
            "step": "images_done",
            "progress": 75,
            "scheme3_ready": True,
        }

        with (
            patch.object(finalization_service, "_visual_candidates", return_value=[ready_visual]),
            patch.object(
                finalization_service,
                "_visual_inputs",
                return_value={"scene_timeline": {"scenes": [{"sequence": 1}]}, "video_path": None},
            ),
            patch.object(
                finalization_service,
                "_whiteboard_video_state",
                return_value={
                    "ready": False,
                    "status": "processing",
                    "active_task_id": "whiteboard-render-active",
                    "failed_task_id": None,
                },
            ),
        ):
            result = finalization_service.reconcile_finalization(coordinator.id)

        coordinator = db.session.get(PublicationTask, coordinator.id)
        self.assertEqual(result["reason"], "waiting_whiteboard_video")
        self.assertEqual(coordinator.status, "processing")
        self.assertEqual(coordinator.current_step, "waiting_whiteboard_video")
        self.assertEqual(coordinator.progress, 95)
        self.assertIsNone(coordinator.completed_at)

    def test_legacy_completed_intent_without_whiteboard_is_reopened(self):
        parent = self._audio_parent()
        landscape = self.root / "legacy-landscape.mp4"
        vertical = self.root / "legacy-scheme3.mp4"
        landscape.write_bytes(b"landscape")
        vertical.write_bytes(b"vertical")
        coordinator = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            feature=finalization_service.FEATURE_NAME,
            user_id="user-1",
            parent_task_id=str(parent.id),
            source_task_id=str(parent.id),
            params={
                "feature": finalization_service.FEATURE_NAME,
                "parent_task_id": str(parent.id),
                "parent_feature": REPORT_AUDIO_FEATURE,
                "source_task_id": str(parent.id),
                "source_feature": REPORT_AUDIO_FEATURE,
                finalization_service.INTENT_KEY: finalization_service._intent_options(
                    include_landscape=True,
                    schemes=["scheme3"],
                    visual_scheme_key="stick_figure",
                    selection_source="legacy-test",
                ),
            },
            result={"visual_workflow_id": "visual-ready"},
            completed_at=datetime.utcnow(),
        )
        db.session.add(coordinator)
        db.session.flush()
        publisher = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            feature="publisher",
            source_task_id=str(parent.id),
            task_video_path=str(landscape),
            publisher_preview_seconds=0,
            publisher_landscape_enabled=True,
            publisher_vertical_enabled=True,
            publisher_vertical_scheme="scheme3",
            publisher_vertical_video_path=str(vertical),
            params={
                "feature": "publisher",
                "metadata": {"finalization_task_id": str(coordinator.id)},
            },
            result={
                "video_path": str(landscape),
                "vertical_video_path": str(vertical),
                "vertical_video_paths": {"scheme3": str(vertical)},
            },
        )
        db.session.add(publisher)
        db.session.flush()
        coordinator.result = {
            **coordinator.result,
            "publisher_task_id": str(publisher.id),
        }
        db.session.commit()
        ready_visual = {
            "workflow_job_id": "visual-ready",
            "visual_scheme": {"key": "stick_figure"},
            "status": "ready",
            "step": "images_done",
            "progress": 75,
            "scheme3_ready": True,
        }

        with (
            patch.object(finalization_service, "_visual_candidates", return_value=[ready_visual]),
            patch.object(
                finalization_service,
                "_visual_inputs",
                return_value={"scene_timeline": {"scenes": [{"sequence": 1}]}, "video_path": None},
            ),
            patch.object(
                finalization_service,
                "_whiteboard_video_state",
                return_value={
                    "ready": False,
                    "status": "processing",
                    "active_task_id": "whiteboard-render-active",
                    "failed_task_id": None,
                },
            ),
        ):
            result = finalization_service.reconcile_finalization(coordinator.id)

        coordinator = db.session.get(PublicationTask, coordinator.id)
        self.assertEqual(result["reason"], "waiting_whiteboard_video")
        self.assertEqual(coordinator.status, "processing")
        self.assertEqual(coordinator.current_step, "waiting_whiteboard_video")
        self.assertIsNone(coordinator.completed_at)
        self.assertIn("legacy_completion_reopened_at", coordinator.result)

    def test_watchdog_does_not_rescan_terminal_legacy_completions(self):
        parent = self._audio_parent()
        coordinator = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            feature=finalization_service.FEATURE_NAME,
            parent_task_id=str(parent.id),
            source_task_id=str(parent.id),
            params={
                "feature": finalization_service.FEATURE_NAME,
                "parent_task_id": str(parent.id),
                "parent_feature": REPORT_AUDIO_FEATURE,
                "source_task_id": str(parent.id),
                "source_feature": REPORT_AUDIO_FEATURE,
                finalization_service.INTENT_KEY: finalization_service._intent_options(
                    include_landscape=True,
                    schemes=["scheme3"],
                    visual_scheme_key="stick_figure",
                    selection_source="legacy-watchdog-test",
                ),
            },
            result={},
            completed_at=datetime.utcnow() - timedelta(minutes=2),
            updated_at=datetime.utcnow() - timedelta(minutes=2),
        )
        db.session.add(coordinator)
        db.session.commit()

        with patch.object(
            finalization_service,
            "reconcile_finalization",
            return_value={"reconciled": True, "reason": "legacy_reopened"},
        ) as reconcile:
            summary = finalization_service.repair_pending_finalizations_once()

        reconcile.assert_not_called()
        self.assertEqual(summary["checked"], 0)

    def test_batch_finalization_summaries_keep_fields_without_full_json_rows(self):
        parent = self._audio_parent()
        old = PublicationTask(
            status="processing",
            current_step="waiting_visual",
            progress=40,
            feature=finalization_service.FEATURE_NAME,
            source_task_id=str(parent.id),
            params={
                "feature": finalization_service.FEATURE_NAME,
                "source_task_id": str(parent.id),
                finalization_service.INTENT_KEY: {
                    "include_landscape": False,
                    "vertical_video_schemes": ["scheme1"],
                    "visual_scheme_key": "doraemon",
                },
            },
            result={"message": "old"},
            created_at=datetime.utcnow() - timedelta(minutes=2),
        )
        latest = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            feature=finalization_service.FEATURE_NAME,
            source_task_id=str(parent.id),
            params={
                "feature": finalization_service.FEATURE_NAME,
                "source_task_id": str(parent.id),
                finalization_service.INTENT_KEY: {
                    "include_landscape": True,
                    "vertical_video_schemes": ["scheme3"],
                    "visual_scheme_key": "stick_figure",
                },
            },
            result={
                "visual_workflow_id": "visual-1",
                "whiteboard_video_path": "/tmp/whiteboard.mp4",
                "whiteboard_attempts": 2,
                "publisher_task_id": "publisher-1",
                "message": "ready",
                "retryable": True,
            },
            completed_at=datetime.utcnow(),
            created_at=datetime.utcnow() - timedelta(minutes=1),
        )
        db.session.add_all([old, latest])
        db.session.commit()
        parent_id = str(parent.id)
        latest_id = str(latest.id)
        statements = []

        def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
            normalized = " ".join(statement.lower().split())
            if " from publication_tasks " in normalized:
                statements.append(normalized)

        event.listen(db.engine, "before_cursor_execute", capture)
        try:
            summaries = finalization_service.finalization_summaries_for_audio_ids(
                [parent_id]
            )
        finally:
            event.remove(db.engine, "before_cursor_execute", capture)

        summary = summaries[parent_id]
        self.assertEqual(summary["job_id"], latest_id)
        self.assertEqual(summary["vertical_video_schemes"], ["scheme3"])
        self.assertEqual(summary["visual_workflow_id"], "visual-1")
        self.assertEqual(summary["whiteboard_attempts"], 2)
        self.assertTrue(summary["retryable"])
        self.assertEqual(len(statements), 1, statements)
        self.assertNotIn("publication_tasks.params as", statements[0])
        self.assertNotIn("publication_tasks.result as", statements[0])

    def test_single_finalization_summary_avoids_full_json_transfer(self):
        parent = self._audio_parent()
        coordinator = PublicationTask(
            status="processing",
            current_step="waiting_visual",
            progress=45,
            feature=finalization_service.FEATURE_NAME,
            source_task_id=str(parent.id),
            params={
                "feature": finalization_service.FEATURE_NAME,
                "source_task_id": str(parent.id),
                finalization_service.INTENT_KEY: {
                    "include_landscape": True,
                    "vertical_video_schemes": ["scheme3"],
                    "visual_scheme_key": "stick_figure",
                },
            },
            result={
                "visual_workflow_id": "visual-single",
                "message": "waiting",
            },
        )
        db.session.add(coordinator)
        db.session.commit()
        parent_id = str(parent.id)
        statements = []

        def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
            normalized = " ".join(statement.lower().split())
            if " from publication_tasks " in normalized:
                statements.append(normalized)

        event.listen(db.engine, "before_cursor_execute", capture)
        try:
            summary = finalization_service.finalization_summary_for_audio(parent_id)
        finally:
            event.remove(db.engine, "before_cursor_execute", capture)

        self.assertEqual("visual-single", summary["visual_workflow_id"])
        self.assertEqual(["scheme3"], summary["vertical_video_schemes"])
        self.assertEqual(1, len(statements), statements)
        self.assertNotIn("publication_tasks.params as", statements[0])
        self.assertNotIn("publication_tasks.result as", statements[0])

    def test_failed_visual_replaced_by_new_task_is_reported_as_running(self):
        parent = self._audio_parent()
        failed_visual = {
            "workflow_job_id": "visual-failed",
            "visual_scheme": {"key": "stick_figure"},
            "status": "failed",
            "step": "storyboard_failed",
            "progress": 35,
            "scheme3_ready": False,
            "error": "旧 profile 被占用",
        }
        coordinator = PublicationTask(
            status="processing",
            current_step="confirmed",
            progress=5,
            feature=finalization_service.FEATURE_NAME,
            user_id="user-1",
            parent_task_id=str(parent.id),
            source_task_id=str(parent.id),
            params={
                "feature": finalization_service.FEATURE_NAME,
                "parent_task_id": str(parent.id),
                "parent_feature": REPORT_AUDIO_FEATURE,
                "source_task_id": str(parent.id),
                "source_feature": REPORT_AUDIO_FEATURE,
                finalization_service.INTENT_KEY: finalization_service._intent_options(
                    include_landscape=True,
                    schemes=["scheme3"],
                    visual_scheme_key="stick_figure",
                    selection_source="test",
                ),
            },
            result={"visual_attempts": 0},
        )
        db.session.add(coordinator)
        db.session.commit()

        with (
            patch.object(finalization_service, "_visual_candidates", return_value=[failed_visual]),
            patch.object(
                finalization_service,
                "_dispatch_visual",
                return_value={
                    "workflow_job_id": "visual-replacement",
                    "status": "storyboard_queued",
                },
            ),
        ):
            result = finalization_service.reconcile_finalization(coordinator.id)

        coordinator = db.session.get(PublicationTask, coordinator.id)
        self.assertEqual(result["reason"], "visual_retried")
        self.assertEqual(coordinator.status, "processing")
        self.assertEqual(coordinator.current_step, "visual_queued")
        self.assertEqual(coordinator.result["visual_workflow_id"], "visual-replacement")
        self.assertIsNone(coordinator.result["next_retry_at"])
        self.assertIn("已新建安全续跑任务", coordinator.result["message"])


if __name__ == "__main__":
    unittest.main()
