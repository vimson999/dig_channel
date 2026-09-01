import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from models import db  # noqa: E402
from models.task import PublicationTask  # noqa: E402
from tasks import report_audio_workflow  # noqa: E402
from tasks import report_script_material_workflow  # noqa: E402
from tasks import report_top10_workflow  # noqa: E402


class BackgroundRevokeSessionLifecycleTest(unittest.TestCase):
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

    def test_child_revoke_helpers_release_clean_read_transaction(self):
        modules = (
            report_audio_workflow,
            report_script_material_workflow,
            report_top10_workflow,
        )
        for index, module in enumerate(modules):
            with self.subTest(module=module.__name__):
                task = PublicationTask(
                    feature="publisher",
                    status="processing",
                    current_step="wait_response",
                    progress=33,
                    celery_task_id=f"child-revoke-{index}",
                    params={"feature": "publisher"},
                    result={},
                )
                db.session.add(task)
                db.session.commit()
                task_id = str(task.id)
                self.assertIsNotNone(db.session.get(PublicationTask, task_id))
                self.assertTrue(db.session().in_transaction())
                transaction_states = []

                def capture_revoke(*_args, **_kwargs):
                    transaction_states.append(db.session().in_transaction())

                with patch.object(
                    module.celery_app.control,
                    "revoke",
                    side_effect=capture_revoke,
                ):
                    module._revoke_child_task(SimpleNamespace(
                        id=task_id,
                        celery_task_id=f"child-revoke-{index}",
                    ))

                self.assertEqual(transaction_states, [False])
                persisted = db.session.get(PublicationTask, task_id)
                db.session.delete(persisted)
                db.session.commit()

    def test_report_audio_duplicate_cleanup_persists_before_revoke(self):
        keep = PublicationTask(
            feature="publisher",
            status="processing",
            current_step="render",
            progress=50,
            celery_task_id="keep-celery-id",
            params={"feature": "publisher"},
            result={},
        )
        duplicate = PublicationTask(
            feature="publisher",
            status="pending",
            current_step="queued",
            progress=0,
            celery_task_id="duplicate-celery-id",
            params={"feature": "publisher"},
            result={"raw_audit": "x" * 20_000},
        )
        db.session.add_all([keep, duplicate])
        db.session.commit()
        keep_id = str(keep.id)
        duplicate_id = str(duplicate.id)
        transaction_states = []
        durable_statuses = []

        def capture_revoke(*_args, **_kwargs):
            transaction_states.append(db.session().in_transaction())
            durable_statuses.append(
                db.session.get(PublicationTask, duplicate_id).status
            )

        with patch.object(
            report_audio_workflow.celery_app.control,
            "revoke",
            side_effect=capture_revoke,
        ):
            report_audio_workflow._cancel_duplicate_publisher_tasks(
                [keep, duplicate],
                keep_id=keep_id,
            )

        self.assertEqual(transaction_states, [False])
        self.assertEqual(durable_statuses, ["canceled"])
        persisted = db.session.get(PublicationTask, duplicate_id)
        self.assertEqual(persisted.status, "canceled")
        self.assertEqual(persisted.current_step, "canceled")


if __name__ == "__main__":
    unittest.main()
