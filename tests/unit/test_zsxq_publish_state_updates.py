import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from models import db  # noqa: E402
from models.task import PublicationTask  # noqa: E402
from services import zsxq_auto_publish_service  # noqa: E402
from tasks import zsxq_publish  # noqa: E402


class ZsxqPublishStateUpdateTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(self.app)
        self.ctx = self.app.app_context()
        self.ctx.push()
        PublicationTask.__table__.create(bind=db.engine)

    def tearDown(self):
        db.session.remove()
        PublicationTask.__table__.drop(bind=db.engine)
        self.ctx.pop()

    def _task(self):
        task = PublicationTask(
            feature="zsxq_clean_output_publish",
            status="pending",
            current_step="queued",
            progress=0,
            params={
                "feature": "zsxq_clean_output_publish",
                "zsxq_publish": {
                    "file_type": "audio",
                    "output_dir": "/tmp/cleaned",
                    "options": {"group_id": "group-1"},
                    "metadata": {"workflow_run_id": "workflow-1"},
                },
            },
            result={"preserved": "yes"},
        )
        db.session.add(task)
        db.session.commit()
        return str(task.id)

    def test_success_uses_snapshot_and_preserves_result(self):
        task_id = self._task()
        publish_result = {
            "status": "completed",
            "published_count": 2,
            "message": "published",
        }

        with patch.object(zsxq_publish, "_create_zsxq_publish_app", return_value=self.app), \
             patch.object(zsxq_publish, "publish_clean_output", return_value=publish_result) as publish, \
             patch.object(zsxq_publish, "_update_attachment_publish_status") as attachment_update, \
             patch.object(zsxq_publish, "_notify_workflow_run", return_value=True) as notify:
            ok = zsxq_publish._run_publish_clean_output_task_impl(task_id)

        self.assertTrue(ok)
        task = PublicationTask.query.filter(PublicationTask.id == task_id).one()
        self.assertEqual(task.status, "completed")
        self.assertEqual(task.current_step, "done")
        self.assertEqual(task.progress, 100)
        self.assertEqual(task.result["preserved"], "yes")
        self.assertEqual(task.result["published_count"], 2)
        self.assertEqual(task.result["publish_result"], publish_result)
        publish.assert_called_once_with(
            file_type="audio",
            output_dir="/tmp/cleaned",
            options={"group_id": "group-1"},
            requested_by=f"zsxq_publish:{task_id}",
        )
        attachment_update.assert_called_once()
        snapshot = attachment_update.call_args.args[0]
        self.assertEqual(snapshot.id, task_id)
        self.assertEqual(snapshot.status, "completed")
        self.assertEqual(snapshot.feature, "zsxq_clean_output_publish")
        self.assertEqual(snapshot.result["published_count"], 2)
        self.assertEqual(snapshot.result["preserved"], "yes")
        notify.assert_called_once_with("workflow-1", task_id)

    def test_db_credential_is_marked_only_after_publish_outcome(self):
        account = {"credential_id": "credential-1", "cookie": "token=abc"}

        with patch(
            "services.platform_credential_service.mark_credential_success"
        ) as mark_success:
            zsxq_auto_publish_service._mark_cookie_account_used(account)
            mark_success.assert_not_called()

            zsxq_auto_publish_service._mark_cookie_account_success(account)

        mark_success.assert_called_once_with("credential-1")

    def test_failed_credential_success_write_rolls_back_session(self):
        account = {"credential_id": "credential-1", "cookie": "token=abc"}

        with patch(
            "services.platform_credential_service.mark_credential_success",
            side_effect=RuntimeError("server closed the connection unexpectedly"),
        ), patch.object(db.session, "rollback") as rollback:
            zsxq_auto_publish_service._mark_cookie_account_success(account)

        rollback.assert_called_once_with()

    def test_db_credentials_release_session_before_remote_publish(self):
        accounts = [{"credential_id": "credential-1", "cookie": "token=abc"}]

        with patch(
            "services.platform_credential_service.resolve_zsxq_cookie_accounts",
            return_value=accounts,
        ), patch.object(db.session, "remove") as remove:
            result = zsxq_auto_publish_service._db_publish_cookie_accounts(
                group_id="group-1",
                required=True,
            )

        self.assertEqual(accounts, result)
        remove.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
