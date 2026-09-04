import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from flask import Flask
from sqlalchemy import event


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from models import db  # noqa: E402
from models.report_video import ReportVideoReport, ReportVideoTask  # noqa: E402
from tasks import report_video  # noqa: E402


class ReportVideoParseStateUpdatesTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(self.app)
        self.ctx = self.app.app_context()
        self.ctx.push()
        ReportVideoTask.__table__.create(bind=db.engine)
        ReportVideoReport.__table__.create(bind=db.engine)

        db.session.add(ReportVideoTask(
            id="video-task-1",
            task_name="parse-test",
            status="created",
            result={"large_payload": "preserved"},
        ))
        db.session.add(ReportVideoReport(
            id="video-report-1",
            task_id="video-task-1",
            report_code="R001",
            file_name="input.pdf",
            file_path="uploads/input.pdf",
            raw_meta={"source": "test"},
        ))
        db.session.commit()
        db.session.remove()

    def tearDown(self):
        db.session.remove()
        ReportVideoReport.__table__.drop(bind=db.engine)
        ReportVideoTask.__table__.drop(bind=db.engine)
        self.ctx.pop()

    @staticmethod
    def _parsed_result():
        return {
            "page_count": 8,
            "raw_text_path": "tasks/raw.txt",
            "cleaned_text_path": "tasks/cleaned.txt",
            "raw_text_chars": 2400,
            "cleaned_text_chars": 1800,
            "is_scanned": False,
            "stats": {"pages": 8},
            "extraction_method": "pdf_text",
            "cache": {"hit": False},
        }

    def test_parse_uses_scalar_snapshot_and_direct_state_updates(self):
        statements = []

        def capture_sql(_conn, _cursor, statement, _parameters, _context, _executemany):
            statements.append(statement)

        event.listen(db.engine, "before_cursor_execute", capture_sql)
        create_app = Mock(return_value=self.app)
        fake_app_module = SimpleNamespace(create_app=create_app)
        try:
            with patch.dict(sys.modules, {"app": fake_app_module}), \
                 patch.object(report_video, "parse_pdf_file", return_value=self._parsed_result()), \
                 patch.object(report_video, "report_dir", return_value=Path("/tmp/video-task-1/R001")):
                result = report_video.parse_report_pdf_task.run("video-report-1")
        finally:
            event.remove(db.engine, "before_cursor_execute", capture_sql)

        self.assertEqual(result, {"status": "parsed", "report_id": "video-report-1"})
        create_app.assert_called_once_with(register_apis=False)
        report = ReportVideoReport.query.filter_by(id="video-report-1").one()
        task = ReportVideoTask.query.filter_by(id="video-task-1").one()
        self.assertEqual(report.parse_status, "parsed")
        self.assertEqual(report.ai_status, "ai_pending")
        self.assertEqual(report.page_count, 8)
        self.assertEqual(report.raw_meta["source"], "test")
        self.assertEqual(report.raw_meta["scan_stats"], {"pages": 8})
        self.assertEqual(task.status, "parsed")
        self.assertEqual(task.result, {"large_payload": "preserved"})

        report_selects = [
            statement for statement in statements
            if statement.lstrip().upper().startswith("SELECT") and "report_video_reports" in statement
        ]
        self.assertEqual(sum("raw_meta" in statement for statement in report_selects), 1)
        self.assertFalse(any("report_video_tasks.result" in statement for statement in statements))

    def test_parse_failure_persists_report_and_parent_without_full_reload(self):
        create_app = Mock(return_value=self.app)
        fake_app_module = SimpleNamespace(create_app=create_app)
        with patch.dict(sys.modules, {"app": fake_app_module}), \
             patch.object(report_video, "parse_pdf_file", side_effect=RuntimeError("parse failed")), \
             patch.object(report_video, "report_dir", return_value=Path("/tmp/video-task-1/R001")):
            with self.assertRaisesRegex(RuntimeError, "parse failed"):
                report_video.parse_report_pdf_task.run("video-report-1")

        create_app.assert_called_once_with(register_apis=False)

        report = ReportVideoReport.query.filter_by(id="video-report-1").one()
        task = ReportVideoTask.query.filter_by(id="video-task-1").one()
        self.assertEqual(report.parse_status, "parse_failed")
        self.assertEqual(report.ai_status, "ai_blocked")
        self.assertEqual(report.error_message, "parse failed")
        self.assertEqual(task.status, "parse_failed")
        self.assertEqual(task.error_message, "parse failed")


if __name__ == "__main__":
    unittest.main()
