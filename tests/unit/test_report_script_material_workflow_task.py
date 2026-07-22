import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

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
        PublicationTask.__table__.create(bind=db.engine)

    def tearDown(self):
        db.session.remove()
        PublicationTask.__table__.drop(bind=db.engine)
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

    def test_gemini_cache_min_chars_uses_shared_report_audio_threshold(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(report_script_material_workflow._min_gemini_final_chars(), 120)

        with patch.dict(os.environ, {
            "REPORT_PDF_GEMINI_MIN_RESPONSE_CHARS": "240",
            "REPORT_AUDIO_GEMINI_MIN_FINAL_CHARS": "360",
        }, clear=True):
            self.assertEqual(report_script_material_workflow._min_gemini_final_chars(), 360)

        with patch.dict(os.environ, {"REPORT_AUDIO_GEMINI_MIN_FINAL_CHARS": "-1"}, clear=True):
            self.assertEqual(report_script_material_workflow._min_gemini_final_chars(), 0)

    def test_script_material_normalizer_uses_production_timeout_and_retry_budget(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(report_script_material_workflow._normalizer_timeout_seconds(), 90)
            self.assertEqual(report_script_material_workflow._normalizer_retry_limit(), 2)
            self.assertEqual(report_script_material_workflow._normalizer_retry_delay_seconds(), 15)

        with patch.dict(os.environ, {"REPORT_SCRIPT_MATERIAL_NORMALIZER_TIMEOUT_SECONDS": "7"}):
            self.assertEqual(report_script_material_workflow._normalizer_timeout_seconds(), 7)

        with patch.dict(os.environ, {"REPORT_SCRIPT_MATERIAL_NORMALIZER_TIMEOUT_SECONDS": "600"}):
            self.assertEqual(report_script_material_workflow._normalizer_timeout_seconds(), 120)

        with patch.dict(os.environ, {"REPORT_SCRIPT_MATERIAL_NORMALIZER_RETRY_LIMIT": "9"}):
            self.assertEqual(report_script_material_workflow._normalizer_retry_limit(), 5)

        with patch.dict(os.environ, {"REPORT_SCRIPT_MATERIAL_NORMALIZER_RETRY_DELAY_SECONDS": "0"}):
            self.assertEqual(report_script_material_workflow._normalizer_retry_delay_seconds(), 1)

        self.assertTrue(report_script_material_workflow._is_retryable_normalizer_error("Read timed out"))
        self.assertTrue(report_script_material_workflow._is_retryable_normalizer_error("HTTP 503"))
        self.assertFalse(report_script_material_workflow._is_retryable_normalizer_error("返回内容缺少 final_script_for_tts"))

    def test_postgresql_progress_and_patch_updates_never_select_parent_json_first(self):
        fake_session = SimpleNamespace(execute=Mock(), commit=Mock())
        fake_db = SimpleNamespace(session=fake_session)

        with (
            patch.object(report_script_material_workflow, "db", fake_db),
            patch.object(report_script_material_workflow, "_is_postgresql_session", return_value=True),
        ):
            report_script_material_workflow._emit("parent-1", "normalize_result", 58, "整理结果")
            report_script_material_workflow._update_result(
                "parent-1",
                {"gemini": {"job_id": "child-1", "status": "completed"}},
            )

        self.assertEqual(fake_session.execute.call_count, 2)
        statements = [str(call.args[0]).lower() for call in fake_session.execute.call_args_list]
        self.assertTrue(all("update public.publication_tasks" in statement for statement in statements))
        self.assertTrue(all("select " not in statement for statement in statements))
        self.assertEqual(fake_session.commit.call_count, 2)

    def test_postgresql_child_summary_is_merged_without_loading_parent_result(self):
        fake_session = SimpleNamespace(execute=Mock(), commit=Mock())
        fake_db = SimpleNamespace(session=fake_session)

        with (
            patch.object(report_script_material_workflow, "db", fake_db),
            patch.object(report_script_material_workflow, "_is_postgresql_session", return_value=True),
            patch.object(report_script_material_workflow, "_load_task") as load_task,
        ):
            report_script_material_workflow._record_child_summary(
                "parent-1",
                "gemini",
                {"job_id": "child-1", "status": "completed"},
            )

        load_task.assert_not_called()
        statement = str(fake_session.execute.call_args.args[0]).lower()
        self.assertIn("update public.publication_tasks", statement)
        self.assertIn("jsonb_set", statement)
        self.assertNotIn("select ", statement)
        self.assertEqual(fake_session.commit.call_count, 1)

    def test_postgresql_attempt_history_is_updated_without_loading_parent_result(self):
        fake_session = SimpleNamespace(execute=Mock(), commit=Mock())
        fake_db = SimpleNamespace(session=fake_session)

        with (
            patch.object(report_script_material_workflow, "db", fake_db),
            patch.object(report_script_material_workflow, "_is_postgresql_session", return_value=True),
            patch.object(report_script_material_workflow, "_load_task") as load_task,
        ):
            report_script_material_workflow._record_child_attempt(
                "parent-1",
                "gemini",
                None,
                2,
                3,
                "failed",
                error="timeout",
            )
            report_script_material_workflow._record_normalizer_attempt(
                "parent-1",
                2,
                3,
                "completed",
            )

        load_task.assert_not_called()
        self.assertEqual(fake_session.execute.call_count, 2)
        statements = [str(call.args[0]).lower() for call in fake_session.execute.call_args_list]
        self.assertTrue(all("update public.publication_tasks" in statement for statement in statements))
        self.assertTrue(all("jsonb_set" in statement for statement in statements))
        self.assertTrue(all("select publication_tasks" not in statement for statement in statements))
        self.assertIn("child_attempts", statements[0])
        self.assertIn("normalizer_attempts", statements[1])
        self.assertEqual(fake_session.commit.call_count, 2)


if __name__ == "__main__":
    unittest.main()
