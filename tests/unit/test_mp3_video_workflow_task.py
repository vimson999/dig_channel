import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from celery.exceptions import Retry
from flask import Flask
from sqlalchemy.exc import OperationalError


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from tasks import mp3_video_workflow  # noqa: E402


class Mp3VideoWorkflowTaskTest(unittest.TestCase):
    def test_retryable_database_disconnect_does_not_mark_business_task_failed(self):
        app = Flask(__name__)
        disconnect = OperationalError(
            "SELECT count(*) FROM publication_tasks",
            {},
            RuntimeError("server closed the connection unexpectedly"),
        )

        with (
            patch.object(mp3_video_workflow, "create_app", return_value=app),
            patch.object(mp3_video_workflow, "guard_publication_task_start"),
            patch.object(mp3_video_workflow, "process_job", side_effect=disconnect),
            patch.object(mp3_video_workflow, "release_db_session_quietly") as release_session,
            patch.object(mp3_video_workflow, "mark_job_failed_safely") as mark_failed,
            patch.object(mp3_video_workflow.process_mp3_video_workflow_job, "retry", side_effect=Retry()) as retry,
            patch.object(mp3_video_workflow.db.session, "remove", MagicMock()),
        ):
            with self.assertRaises(Retry):
                mp3_video_workflow.process_mp3_video_workflow_job.run("job-1")

        release_session.assert_called_once_with()
        retry.assert_called_once()
        mark_failed.assert_not_called()


if __name__ == "__main__":
    unittest.main()
