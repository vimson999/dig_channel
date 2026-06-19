import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from flask import Flask


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import services.report_video_asset_service as asset_service  # noqa: E402
from models import db  # noqa: E402
from models.report_video import ReportVideoReport, ReportVideoTask  # noqa: E402
from models.zsxq_sync import ZsxqSyncRun, ZsxqTopic  # noqa: E402
from services.report_video_asset_service import (  # noqa: E402
    ZsxqReportAssetService,
    build_sync_run_asset_scan_payload,
    classify_theme_hint,
    _source_hashes_from_report,
)


class ReportVideoAssetServiceTest(unittest.TestCase):
    def test_classify_theme_hint_prefers_most_specific_keyword_bucket(self):
        result = classify_theme_hint("AI 算力服务器与光模块产业链深度报告")

        self.assertEqual(result["theme"], "AI算力")
        self.assertIn("算力", result["matched_keywords"])

    def test_source_hashes_keep_auto_asset_and_task_level_hashes(self):
        report = SimpleNamespace(
            raw_meta={
                "auto_asset": {"source_sha256": "asset-hash"},
            },
            task=SimpleNamespace(
                result={
                    "auto_scan": {"source_sha256": "task-scan-hash"},
                }
            ),
        )

        self.assertEqual(_source_hashes_from_report(report), ["asset-hash", "task-scan-hash"])

    def test_build_sync_run_payload_defaults_to_parse_and_bounded_max_files(self):
        payload = build_sync_run_asset_scan_payload(
            "run-1",
            {
                "trigger": "scheduler_start",
                "report_asset_scan_max_files": "999",
                "report_asset_scan_tab_keyword": "外资研报",
            },
        )

        self.assertEqual(payload["source"], "sync_run")
        self.assertEqual(payload["run_id"], "run-1")
        self.assertTrue(payload["parse_pdfs"])
        self.assertEqual(payload["config"]["max_files"], 200)
        self.assertEqual(payload["config"]["tab_label_keyword"], "外资研报")

    def test_existing_reports_by_hash_filters_to_requested_hashes(self):
        app = Flask(__name__)
        app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
        app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
        db.init_app(app)

        with app.app_context():
            db.create_all()
            task = ReportVideoTask(
                id="task-existing",
                task_name="existing",
                video_type="daily_digest",
                status="completed",
                result={"auto_scan": {"source_sha256": "task-hash"}},
            )
            report = ReportVideoReport(
                id="report-existing",
                task_id="task-existing",
                report_code="R001",
                file_name="existing.pdf",
                file_path="/tmp/existing.pdf",
                raw_meta={"auto_asset": {"source_sha256": "asset-hash"}},
            )
            db.session.add_all([task, report])
            db.session.commit()

            result = ZsxqReportAssetService()._existing_reports_by_hash(["asset-hash", "missing-hash"])

            self.assertIn("asset-hash", result)
            self.assertNotIn("missing-hash", result)
            self.assertEqual(result["asset-hash"].task_status, "completed")

    def test_create_batch_from_sync_run_creates_daily_digest_asset_report(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            source_pdf = temp_root / "AI算力深度.pdf"
            source_pdf.write_bytes(b"%PDF-1.4\nasset")

            app = Flask(__name__)
            app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
            app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
            db.init_app(app)

            old_report_dir = asset_service.report_dir
            old_rel_path = asset_service.rel_path
            asset_service.report_dir = lambda task_id, code: temp_root / "tasks" / str(task_id) / code
            asset_service.rel_path = lambda path: str(Path(path))
            try:
                with app.app_context():
                    db.create_all()
                    run = ZsxqSyncRun(
                        id="run-asset-1",
                        group_id="group-1",
                        group_name="前沿信息收录",
                        result={
                            "downloaded_pdf_files": [
                                {
                                    "path": str(source_pdf),
                                    "name": source_pdf.name,
                                    "topic_id": "topic-1",
                                    "tab_id": "tab-report",
                                    "attachment_id": "attachment-1",
                                }
                            ]
                        },
                    )
                    topic = ZsxqTopic(
                        group_id="group-1",
                        group_name="前沿信息收录",
                        tab_id="tab-report",
                        tab_label="外资研报",
                        topic_id="topic-1",
                        title="AI 算力服务器深度",
                        content="光模块 GPU 数据中心",
                    )
                    db.session.add_all([run, topic])
                    db.session.commit()

                    result = asset_service.create_zsxq_report_asset_batch({
                        "source": "sync_run",
                        "run_id": "run-asset-1",
                        "parse_pdfs": False,
                        "config": {"max_files": 5, "tab_label_keyword": "外资研报"},
                    })

                    self.assertEqual(result["created_count"], 1)
                    self.assertEqual(ReportVideoTask.query.count(), 1)
                    report = ReportVideoReport.query.one()
                    self.assertEqual(report.parse_status, "uploaded")
                    auto_asset = report.raw_meta["auto_asset"]
                    self.assertEqual(auto_asset["source"], "sync_run")
                    self.assertEqual(auto_asset["zsxq"]["topic_id"], "topic-1")
                    self.assertEqual(auto_asset["theme_hint"], "AI算力")
            finally:
                asset_service.report_dir = old_report_dir
                asset_service.rel_path = old_rel_path


if __name__ == "__main__":
    unittest.main()
