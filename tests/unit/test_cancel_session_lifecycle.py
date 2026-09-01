import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask, g
from sqlalchemy import text


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from api import doubao_browser_tts  # noqa: E402
from api import gemini_browser  # noqa: E402
from api import mp3_video_workflow  # noqa: E402
from api import report_audio_workflow  # noqa: E402
from api import report_script_material_workflow  # noqa: E402
from api import report_top10_workflow  # noqa: E402
from models import ReportStickFigureSceneAsset, db  # noqa: E402
from models.task import PublicationTask  # noqa: E402
from services import runtime_guard  # noqa: E402
from tasks import report_stick_figure_assets  # noqa: E402


class CancelSessionLifecycleTest(unittest.TestCase):
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

    def _task(self, module, suffix):
        feature = str(module.FEATURE_NAME)
        task = PublicationTask(
            feature=feature,
            user_id="user-1",
            status="pending",
            current_step="queued",
            progress=17,
            celery_task_id=f"cancel-broker-{suffix}",
            params={
                "feature": feature,
                "user_id": "user-1",
                feature: {},
            },
            result={"logs": [], "raw_audit": "x" * 20_000},
        )
        db.session.add(task)
        db.session.commit()
        return task

    def test_workflow_cancel_routes_persist_before_celery_revoke(self):
        modules = (
            doubao_browser_tts,
            gemini_browser,
            mp3_video_workflow,
            report_audio_workflow,
            report_script_material_workflow,
            report_top10_workflow,
        )

        for index, module in enumerate(modules):
            with self.subTest(module=module.__name__):
                task = self._task(module, index)
                task_id = str(task.id)
                transaction_states = []

                def capture_revoke(*_args, **_kwargs):
                    transaction_states.append(db.session().in_transaction())

                with (
                    self.app.test_request_context(
                        f"/api/test/jobs/{task_id}/cancel",
                        method="POST",
                    ),
                    patch.object(module, "_get_owned_task", return_value=task),
                    patch.object(
                        module.celery_app.control,
                        "revoke",
                        side_effect=capture_revoke,
                    ),
                ):
                    response = module.cancel_job.__wrapped__(
                        SimpleNamespace(id="user-1"),
                        task_id,
                    )
                    body = (
                        response[0].get_json()
                        if isinstance(response, tuple)
                        else response.get_json()
                    )

                self.assertTrue(body["success"], body)
                self.assertEqual(transaction_states, [False])
                persisted = db.session.get(PublicationTask, task_id)
                self.assertEqual(persisted.status, "canceled")
                self.assertEqual(persisted.current_step, "canceled")
                db.session.delete(persisted)
                db.session.commit()

    def test_workflow_cancel_remains_durable_when_revoke_control_fails(self):
        module = mp3_video_workflow
        task = self._task(module, "failure")
        task_id = str(task.id)

        with (
            self.app.test_request_context(
                f"/api/test/jobs/{task_id}/cancel",
                method="POST",
            ),
            patch.object(module, "_get_owned_task", return_value=task),
            patch.object(
                module.celery_app.control,
                "revoke",
                side_effect=RuntimeError("broker control unavailable"),
            ),
        ):
            try:
                response = module.cancel_job.__wrapped__(
                    SimpleNamespace(id="user-1"),
                    task_id,
                )
            except RuntimeError as exc:
                self.fail(f"持久化取消不能被 broker revoke 失败回滚: {exc}")

        body = response[0].get_json() if isinstance(response, tuple) else response.get_json()
        self.assertTrue(body["success"], body)
        persisted = db.session.get(PublicationTask, task_id)
        self.assertEqual(persisted.status, "canceled")
        self.assertEqual(persisted.current_step, "canceled")

    def test_shared_revoke_logs_clean_request_and_broker_boundary(self):
        transaction_states = []

        def capture_revoke(*_args, **_kwargs):
            transaction_states.append(db.session().in_transaction())

        with self.app.test_request_context("/api/test/jobs/job-1/cancel", method="POST"):
            g.request_id = "req-boundary-1"
            db.session.execute(text("SELECT 1"))
            self.assertTrue(db.session().in_transaction())

            with (
                patch(
                    "celery_config.celery_app.control.revoke",
                    side_effect=capture_revoke,
                ),
                self.assertLogs(runtime_guard.logger, level="INFO") as captured,
            ):
                revoked = runtime_guard.revoke_celery_task_after_db_commit(
                    "broker-boundary-1",
                    operation="workflow cancel task=job-1",
                    business_task_id="job-1",
                )

        self.assertTrue(revoked)
        self.assertEqual(transaction_states, [False])
        combined = "\n".join(captured.output)
        self.assertIn("[DB_SESSION_BOUNDARY]", combined)
        self.assertIn("phase=before_io", combined)
        self.assertIn("phase=after_io", combined)
        self.assertIn("request_id=req-boundary-1", combined)
        self.assertIn("business_task=job-1", combined)
        self.assertIn("broker_task=broker-boundary-1", combined)
        self.assertIn("session_in_transaction=false", combined)
        self.assertIn("outcome=success", combined)

    def test_shared_batch_revoke_preserves_single_control_call_and_signal(self):
        helper = getattr(runtime_guard, "revoke_celery_tasks_after_db_commit", None)
        self.assertIsNotNone(
            helper,
            "批量撤销必须复用共享 DB Session 边界，而不是直接调用 Celery control",
        )
        if helper is None:
            return

        transaction_states = []
        calls = []

        def capture_revoke(task_ids, **kwargs):
            transaction_states.append(db.session().in_transaction())
            calls.append((task_ids, kwargs))

        db.session.execute(text("SELECT 1"))
        with (
            patch(
                "celery_config.celery_app.control.revoke",
                side_effect=capture_revoke,
            ),
            self.assertLogs(runtime_guard.logger, level="INFO") as captured,
        ):
            revoked = helper(
                ["broker-batch-1", "broker-batch-2", "broker-batch-1", ""],
                terminate=True,
                signal="SIGTERM",
                operation="image quota sibling revoke",
                business_task_id="workflow-1",
            )

        self.assertTrue(revoked)
        self.assertEqual(transaction_states, [False])
        self.assertEqual(
            calls,
            [(
                ["broker-batch-1", "broker-batch-2"],
                {"terminate": True, "signal": "SIGTERM"},
            )],
        )
        combined = "\n".join(captured.output)
        self.assertIn("broker_count=2", combined)
        self.assertIn("business_task=workflow-1", combined)
        self.assertIn("signal=SIGTERM", combined)
        self.assertIn("outcome=success", combined)

    def test_image_quota_terminal_state_survives_batch_revoke_failure(self):
        workflow = PublicationTask(
            feature="report_stick_figure_video_workflow",
            user_id="user-1",
            status="processing",
            current_step="images_queued",
            progress=82,
            params={"feature": "report_stick_figure_video_workflow"},
            result={},
        )
        sibling = PublicationTask(
            feature="report_stick_figure_image",
            user_id="user-1",
            status="pending",
            current_step="queued",
            progress=5,
            celery_task_id="image-sibling-broker-1",
            params={"feature": "report_stick_figure_image"},
            result={"raw_audit": "preserve-me"},
        )
        db.session.add_all([workflow, sibling])
        db.session.flush()
        asset = ReportStickFigureSceneAsset(
            workflow_task_id=str(workflow.id),
            storyboard_task_id=str(workflow.id),
            segment_id="segment-quota-1",
            sequence=1,
            script_text_hash="script-hash",
            visual_prompt_hash="prompt-hash",
            image_task_id=str(sibling.id),
            image_status="pending",
        )
        db.session.add(asset)
        db.session.commit()
        workflow_id = str(workflow.id)
        sibling_id = str(sibling.id)
        asset_id = str(asset.id)

        with patch(
            "tasks.report_stick_figure_assets.celery_app.control.revoke",
            side_effect=RuntimeError("broker control unavailable"),
        ):
            try:
                report_stick_figure_assets._cancel_workflow_images_for_quota(
                    workflow_id,
                    current_task_id="current-image-task",
                )
            except RuntimeError as exc:
                self.fail(f"额度终态已提交后不能被 broker revoke 失败改写为任务异常: {exc}")

        db.session.expire_all()
        persisted_workflow = db.session.get(PublicationTask, workflow_id)
        persisted_sibling = db.session.get(PublicationTask, sibling_id)
        persisted_asset = db.session.get(ReportStickFigureSceneAsset, asset_id)
        self.assertEqual(persisted_workflow.status, "failed")
        self.assertEqual(persisted_workflow.current_step, "image_quota_exhausted")
        self.assertEqual(persisted_sibling.status, "canceled")
        self.assertEqual(persisted_sibling.result["raw_audit"], "preserve-me")
        self.assertEqual(persisted_asset.image_status, "canceled")


if __name__ == "__main__":
    unittest.main()
