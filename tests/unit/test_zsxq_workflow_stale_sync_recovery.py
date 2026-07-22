import sys
import unittest
from contextlib import contextmanager
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
from models.zsxq_sync import ZsxqSyncRun  # noqa: E402
from models.zsxq_workflow import ZsxqWorkflowRun  # noqa: E402
from services.zsxq_workflow_service import ZsxqWorkflowService  # noqa: E402


class ZsxqWorkflowStaleSyncRecoveryTest(unittest.TestCase):
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
        self.service = ZsxqWorkflowService()
        self.service.ensure_schema = lambda: None

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    @contextmanager
    def _capture_sql(self):
        statements = []

        def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
            statements.append(" ".join(statement.lower().split()))

        event.listen(db.engine, "before_cursor_execute", capture)
        try:
            yield statements
        finally:
            event.remove(db.engine, "before_cursor_execute", capture)

    def _create_pair(self, *, run_status, result=None, error_message=None):
        old = datetime.utcnow() - timedelta(hours=2)
        run = ZsxqSyncRun(
            id=f"sync-{run_status}",
            group_id="group-1",
            status=run_status,
            result=result or {},
            error_message=error_message,
            created_at=old,
            updated_at=old,
        )
        workflow = ZsxqWorkflowRun(
            id=f"workflow-{run_status}",
            group_id="group-1",
            status="running",
            current_stage="sync",
            sync_run_id=run.id,
            config_snapshot={
                "stages": {"sync": True, "clean": False, "publish": False},
                "clean": {"auto_pdf_clean": False, "auto_audio_clean": False},
                "publish": {"enabled": False},
            },
            stage_results={},
            created_at=old,
            updated_at=old,
        )
        db.session.add_all([run, workflow])
        db.session.commit()
        return workflow.id, run.id

    @staticmethod
    def _sync_selects(statements):
        return [
            sql for sql in statements
            if sql.startswith("select") and " from zsxq_sync_runs " in sql
        ]

    def test_terminal_failed_reconciles_without_loading_result(self):
        workflow_id, _ = self._create_pair(
            run_status="failed",
            result={"large": "x" * 10000},
            error_message="remote database disconnected",
        )

        with patch.object(self.service, "_queue_notification"), self._capture_sql() as statements:
            recovered = self.service.recover_stale_running_sync_workflows(
                group_id="group-1",
                stale_minutes=30,
            )

        sync_selects = self._sync_selects(statements)
        self.assertEqual(len(sync_selects), 1, statements)
        self.assertNotIn("zsxq_sync_runs.result", sync_selects[0])
        workflow = db.session.get(ZsxqWorkflowRun, workflow_id)
        self.assertEqual(workflow.status, "failed")
        self.assertEqual(workflow.error_message, "remote database disconnected")
        self.assertEqual(recovered[0]["action"], "terminal_failed_reconciled")

    def test_new_workflow_duplicate_checks_select_only_id(self):
        with self._capture_sql() as statements:
            system = self.service._find_existing_system_workflow(
                batch_id="new-batch",
                group_id="group-1",
                batch_round=133,
            )
            manual = self.service._find_existing_manual_workflow(
                batch_id="new-manual-batch",
                group_id="group-1",
                trigger="manual",
            )

        self.assertIsNone(system)
        self.assertIsNone(manual)
        workflow_selects = [
            sql for sql in statements
            if sql.startswith("select") and " from zsxq_workflow_runs " in sql
        ]
        self.assertEqual(2, len(workflow_selects), statements)
        for sql in workflow_selects:
            self.assertIn("zsxq_workflow_runs.id", sql)
            self.assertNotIn("config_snapshot", sql)
            self.assertNotIn("stage_results", sql)
            self.assertNotIn("clean_task_ids", sql)

    def test_running_sync_loads_heartbeat_only_and_keeps_fresh_workflow(self):
        heartbeat = datetime.utcnow().isoformat() + "Z"
        workflow_id, _ = self._create_pair(
            run_status="running",
            result={"heartbeat_at": heartbeat, "large": "x" * 10000},
        )

        with patch.object(self.service, "_queue_notification"), self._capture_sql() as statements:
            recovered = self.service.recover_stale_running_sync_workflows(
                group_id="group-1",
                stale_minutes=30,
            )

        sync_selects = self._sync_selects(statements)
        self.assertEqual(len(sync_selects), 2, statements)
        self.assertNotIn("zsxq_sync_runs.result", sync_selects[0])
        self.assertIn("zsxq_sync_runs.result", sync_selects[1])
        self.assertNotIn("zsxq_sync_runs.requested_tabs", sync_selects[1])
        self.assertEqual(recovered, [])
        self.assertEqual(db.session.get(ZsxqWorkflowRun, workflow_id).status, "running")

    def test_terminal_completed_loads_full_result_once_and_advances(self):
        workflow_id, _ = self._create_pair(
            run_status="completed",
            result={"topics_fetched": 3, "attachments_downloaded": 2, "large": "x" * 10000},
        )

        with patch.object(self.service, "_queue_notification"), self._capture_sql() as statements:
            recovered = self.service.recover_stale_running_sync_workflows(
                group_id="group-1",
                stale_minutes=30,
            )

        sync_selects = self._sync_selects(statements)
        result_selects = [sql for sql in sync_selects if "zsxq_sync_runs.result" in sql]
        self.assertGreaterEqual(len(sync_selects), 2, statements)
        self.assertNotIn("zsxq_sync_runs.result", sync_selects[0])
        self.assertEqual(len(result_selects), 1, statements)
        workflow = db.session.get(ZsxqWorkflowRun, workflow_id)
        self.assertEqual(workflow.status, "completed")
        self.assertEqual(workflow.stage_results["sync"]["status"], "completed")
        self.assertEqual(recovered[0]["action"], "terminal_completed_advanced")

    def test_clean_recovery_scans_terminal_children_without_multirow_payload(self):
        old = datetime.utcnow() - timedelta(hours=8)
        tasks = [
            PublicationTask(
                id=f"clean-{index}",
                status="completed",
                current_step="done",
                feature="content_pdf_clean",
                params={"feature": "content_pdf_clean", "large": "x" * 10000},
                result={"large": "x" * 10000},
                created_at=old,
                updated_at=old,
            )
            for index in range(2)
        ]
        workflow = ZsxqWorkflowRun(
            id="workflow-clean-terminal",
            group_id="group-1",
            status="running",
            current_stage="clean",
            clean_task_ids=[task.id for task in tasks],
            config_snapshot={},
            stage_results={},
            created_at=old,
            updated_at=old,
        )
        db.session.add_all([*tasks, workflow])
        db.session.commit()

        with patch.object(self.service, "_queue_notification"), self._capture_sql() as statements:
            recovered = self.service.recover_stale_running_clean_workflows(
                group_id="group-1",
                stale_minutes=30,
            )

        task_selects = [
            sql for sql in statements
            if sql.startswith("select") and " from publication_tasks " in sql
        ]
        self.assertEqual(len(task_selects), 1, statements)
        self.assertNotIn("publication_tasks.params", task_selects[0])
        self.assertNotIn("publication_tasks.result", task_selects[0])
        self.assertEqual(recovered, [])


if __name__ == "__main__":
    unittest.main()
