import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from api import report_script_material_workflow as material_api  # noqa: E402
from models import db  # noqa: E402
from models.task import PublicationTask  # noqa: E402
from models.zsxq_sync import ZsxqAttachment, ZsxqTopic  # noqa: E402
from services.report_script_material_workflow import FEATURE_NAME, QUEUE_NAME  # noqa: E402


class ReportScriptMaterialWorkflowApiTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
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
        self.tmpdir.cleanup()

    def _add_pdf_asset(self):
        pdf_path = Path(self.tmpdir.name) / "report.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\nfixture")
        topic = ZsxqTopic(
            group_id="group-1",
            tab_id="tab-1",
            topic_id="topic-1",
            title="AI 算力深度报告",
            tab_label="外资研报",
        )
        attachment = ZsxqAttachment(
            id="asset-1",
            group_id="group-1",
            tab_id="tab-1",
            topic_id="topic-1",
            attachment_key="asset-key",
            name="report.pdf",
            file_ext="pdf",
            download_count=42,
            local_path=str(pdf_path),
            download_status="downloaded",
            attachment_type="file",
        )
        db.session.add_all([topic, attachment])
        db.session.commit()
        return attachment

    def test_create_task_for_asset_uses_script_material_feature_and_queue(self):
        self._add_pdf_asset()

        with patch.object(
            material_api,
            "maybe_guarded_apply_async",
            return_value={
                "async_result": SimpleNamespace(id="celery-material"),
                "deferred": False,
                "admission": {"allowed": True},
            },
        ) as guarded_apply:
            result = material_api._create_task_for_asset(
                "asset-1",
                user_id="user-1",
                prompt="解析",
                review_prompt="复核",
                followups=[],
                gemini_url="",
                metadata={"source": "unit-test"},
            )

        task = PublicationTask.query.one()
        self.assertEqual(result["status"], "queued")
        self.assertEqual(result["job_id"], task.id)
        self.assertEqual(task.feature, FEATURE_NAME)
        self.assertEqual(task.celery_task_id, "celery-material")
        self.assertEqual(task.source_feature, "zsxq_attachment")
        self.assertEqual(task.source_task_id, "asset-1")
        self.assertEqual(task.report_audio_pdf_attachment_id, "asset-1")
        self.assertEqual(task.params[FEATURE_NAME]["pdf"]["attachment_id"], "asset-1")
        guarded_apply.assert_called_once()
        _, kwargs = guarded_apply.call_args
        self.assertEqual(kwargs["queue"], QUEUE_NAME)
        self.assertEqual(kwargs["feature"], FEATURE_NAME)

    def test_asset_status_detects_existing_full_report_audio_material(self):
        attachment = self._add_pdf_asset()
        task = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            params={
                "feature": "report_pdf_audio_workflow",
                "source_feature": "zsxq_attachment",
                "source_task_id": attachment.id,
                "report_audio_workflow": {
                    "pdf": {"attachment_id": attachment.id, "filename": "report.pdf"},
                },
            },
            result={
                "structured_result": {
                    "final_script_for_tts": "这是一段历史完整研报任务留下的基础文案。",
                },
            },
        )
        db.session.add(task)
        db.session.commit()

        items = [{"id": attachment.id}]
        material_api._attach_material_status(items)

        self.assertEqual(items[0]["material_task_id"], task.id)
        self.assertEqual(items[0]["material_task_status"], "completed")
        self.assertEqual(items[0]["material_task_feature"], "report_pdf_audio_workflow")
        self.assertTrue(items[0]["material_has_script"])
        self.assertEqual(items[0]["material_script_chars"], len("这是一段历史完整研报任务留下的基础文案。"))

        payload = material_api._material_task_payload(task)
        self.assertEqual(payload["job_id"], task.id)
        self.assertEqual(payload["feature_label"], "研报转语音")
        self.assertEqual(payload["final_script_for_tts"], "这是一段历史完整研报任务留下的基础文案。")


if __name__ == "__main__":
    unittest.main()
