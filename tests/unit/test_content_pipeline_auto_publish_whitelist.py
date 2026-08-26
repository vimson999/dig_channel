import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from models import db  # noqa: E402
from models.task import PublicationTask  # noqa: E402
from services import zsxq_auto_publish_service as auto_publish  # noqa: E402
from services.content_pipeline import auto_publish_handoff  # noqa: E402


class ContentPipelineAutoPublishWhitelistTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
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
        self.temp_dir.cleanup()

    def test_submit_persists_explicit_cleaned_file_whitelist(self):
        output_dir = self.root / "pdf_cleaned"
        output_dir.mkdir()
        allowed = output_dir / "allowed.pdf"
        allowed.write_bytes(b"%PDF-cleaned")

        def dispatch(_platform, _task_id, *, broker_task_id, retry):
            return {
                "celery_id": broker_task_id,
                "admission": {"allowed": True},
                "deferred": False,
            }

        with patch.object(auto_publish_handoff, "dispatch_platform_publish", side_effect=dispatch), \
             patch(
                 "services.content_pipeline.notification_service.notify_content_pipeline_event",
                 return_value={"queued": False},
             ):
            payload = auto_publish_handoff.submit_publish_clean_output_task(
                title="PDF 发布",
                file_type="pdf",
                output_dir=str(output_dir),
                approved_paths=[str(allowed)],
                options={"enabled": True},
            )

        task = db.session.get(PublicationTask, payload["id"])
        self.assertEqual(
            task.params["zsxq_publish"]["approved_paths"],
            [str(allowed.resolve())],
        )
        self.assertEqual(
            task.result["approved_paths"],
            [str(allowed.resolve())],
        )

    def test_rule_iterator_never_scans_neighboring_files_when_whitelist_is_present(self):
        output_dir = self.root / "audio_cleaned"
        output_dir.mkdir()
        allowed = output_dir / "allowed.mp3"
        neighbor = output_dir / "neighbor.mp3"
        allowed.write_bytes(b"allowed")
        neighbor.write_bytes(b"neighbor")

        paths = list(auto_publish._iter_rule_files({
            "source_dir": str(output_dir),
            "recursive": True,
            "extensions": [".mp3"],
            "approved_paths": [str(allowed)],
        }))

        self.assertEqual(paths, [allowed.resolve()])


if __name__ == "__main__":
    unittest.main()
