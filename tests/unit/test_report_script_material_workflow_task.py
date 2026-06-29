import os
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
from tasks import report_script_material_workflow  # noqa: E402


class ReportScriptMaterialWorkflowTaskTest(unittest.TestCase):
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

    def test_process_job_reuses_completed_report_audio_material_for_same_pdf(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "report.pdf"
            pdf_path.write_bytes(b"%PDF-1.4\nfixture")
            structured = {
                "title": "AI 算力深度报告",
                "summary": "摘要",
                "final_script_for_tts": "这是一段已经整理好的研报口播正文，足够作为基础文案物料复用。",
            }
            existing = PublicationTask(
                status="completed",
                current_step="done",
                progress=100,
                params={
                    "feature": "report_pdf_audio_workflow",
                    "source_feature": "zsxq_attachment",
                    "source_task_id": "asset-1",
                    "report_audio_workflow": {
                        "pdf": {
                            "attachment_id": "asset-1",
                            "filename": "report.pdf",
                            "path": str(pdf_path),
                        },
                    },
                },
                result={"structured_result": structured},
            )
            task = PublicationTask(
                status="pending",
                current_step="queued",
                progress=0,
                params={
                    "feature": "report_script_material_workflow",
                    "user_id": "system",
                    "source_feature": "zsxq_attachment",
                    "source_task_id": "asset-1",
                    "report_script_material_workflow": {
                        "prompt": "解析",
                        "followups": [],
                        "pdf": {
                            "attachment_id": "asset-1",
                            "filename": "report.pdf",
                            "path": str(pdf_path),
                        },
                        "pdf_path": str(pdf_path),
                    },
                },
                result={"children": {}, "script_material": {"status": "pending"}},
            )
            db.session.add_all([existing, task])
            db.session.commit()

            output_path = str(Path(temp_dir) / "material.json")
            with (
                patch.dict(os.environ, {"REPORT_SCRIPT_MATERIAL_MIN_FINAL_CHARS": "1"}),
                patch.object(report_script_material_workflow, "write_result_file", return_value=output_path),
                patch.object(
                    report_script_material_workflow.process_report_pdf_gemini_job,
                    "apply_async",
                ) as gemini_apply,
            ):
                ok = report_script_material_workflow._process_job(task.id)

            self.assertTrue(ok)
            gemini_apply.assert_not_called()
            db.session.refresh(task)
            self.assertEqual(task.status, "completed")
            self.assertEqual(task.current_step, "done")
            self.assertEqual(task.result["structured_result"], structured)
            self.assertEqual(task.result["script_material"]["source"], "reused_completed_material")
            self.assertEqual(task.result["script_material"]["source_task_id"], existing.id)
            self.assertEqual(task.result["output_path"], output_path)


if __name__ == "__main__":
    unittest.main()
