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

from apis.v1 import celery_monitor  # noqa: E402
from models import db  # noqa: E402
from models.task import PublicationTask  # noqa: E402
from models.zsxq_sync import ZsxqAttachment, ZsxqSyncRun, ZsxqTopic  # noqa: E402
from services.zsxq_sync_service import ZsxqSyncService  # noqa: E402


class ZsxqAutoVideoDispatchTest(unittest.TestCase):
    def test_monitor_maps_report_pdf_audio_feature_to_report_workflow_service(self):
        self.assertIn(
            "report_pdf_audio_workflow",
            celery_monitor.BUSINESS_TASK_FEATURES["report_audio_workflow"],
        )
        self.assertIn(
            "report_script_material_workflow",
            celery_monitor.BUSINESS_TASK_FEATURES["report_script_material_workflow"],
        )

    def test_dispatch_collects_reused_downloaded_attachment_candidates(self):
        sync_result = {
            "downloaded_files": [{"attachment_id": "new-pdf"}],
            "auto_video_dispatch_files": [{"attachment_id": "reused-root"}],
            "tabs": [
                {
                    "downloaded_files": [{"attachment_id": "new-pdf"}],
                    "auto_video_dispatch_files": [
                        {"attachment_id": "reused-tab"},
                        {"attachment_id": "reused-root"},
                    ],
                }
            ],
        }

        ids = ZsxqSyncService()._downloaded_attachment_ids_from_sync_result(sync_result)

        self.assertEqual(ids, ["new-pdf", "reused-root", "reused-tab"])

    def test_dispatch_queues_pdf_and_mp3_workflow_tasks(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            pdf_path = temp_root / "report.pdf"
            mp3_path = temp_root / "memo.mp3"
            pdf_path.write_bytes(b"%PDF-1.4\nfixture")
            mp3_path.write_bytes(b"fixture audio")

            app = Flask(__name__)
            app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
            app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
            db.init_app(app)

            with app.app_context():
                db.create_all()
                run = ZsxqSyncRun(
                    id="run-auto-video",
                    group_id="group-1",
                    group_name="前沿信息收录",
                    result={},
                )
                pdf_topic = ZsxqTopic(
                    group_id="group-1",
                    group_name="前沿信息收录",
                    tab_id="tab-pdf",
                    tab_label="外资研报",
                    topic_id="topic-pdf",
                    title="AI 算力深度报告",
                )
                mp3_topic = ZsxqTopic(
                    group_id="group-1",
                    group_name="前沿信息收录",
                    tab_id="tab-mp3",
                    tab_label="调研纪要",
                    topic_id="topic-mp3",
                    title="机器人调研纪要",
                )
                pdf_attachment = ZsxqAttachment(
                    id="att-pdf",
                    group_id="group-1",
                    tab_id="tab-pdf",
                    topic_id="topic-pdf",
                    attachment_key="pdf-key",
                    name="report.pdf",
                    download_count=36,
                    local_path=str(pdf_path),
                    download_status="downloaded",
                )
                mp3_attachment = ZsxqAttachment(
                    id="att-mp3",
                    group_id="group-1",
                    tab_id="tab-mp3",
                    topic_id="topic-mp3",
                    attachment_key="mp3-key",
                    name="memo.mp3",
                    download_count=26,
                    local_path=str(mp3_path),
                    download_status="downloaded",
                )
                db.session.add_all([run, pdf_topic, mp3_topic, pdf_attachment, mp3_attachment])
                db.session.commit()

                sync_result = {
                    "downloaded_files": [
                        {"path": str(pdf_path), "attachment_id": "att-pdf"},
                        {"path": str(mp3_path), "attachment_id": "att-mp3"},
                    ],
                    "downloaded_pdf_files": [
                        {"path": str(pdf_path), "attachment_id": "att-pdf"},
                    ],
                }
                config = {
                    "enabled": True,
                    "pdf_min_download_count": 35,
                    "mp3_min_download_count": 25,
                    "wait_for_completion": False,
                }

                with patch(
                    "tasks.report_audio_workflow.process_report_audio_workflow_job.apply_async",
                    return_value=SimpleNamespace(id="celery-pdf"),
                ) as report_apply:
                    with patch(
                        "tasks.mp3_video_workflow.process_mp3_video_workflow_job.apply_async",
                        return_value=SimpleNamespace(id="celery-mp3"),
                    ) as mp3_apply:
                        with patch(
                            "services.workflow_notification_service.workflow_notification_service.send_email_message"
                        ) as send_email:
                            summary = ZsxqSyncService()._submit_auto_video_dispatch_for_run(
                                run=run,
                                sync_result=sync_result,
                                config=config,
                                workflow_run_id="workflow-1",
                            )

                self.assertEqual(summary["status"], "completed")
                self.assertEqual(summary["pdf"]["queued"], 1)
                self.assertEqual(summary["mp3"]["queued"], 1)
                self.assertEqual(summary["queued_count"], 2)
                report_apply.assert_called_once()
                mp3_apply.assert_called_once()
                send_email.assert_called_once()

                tasks = PublicationTask.query.order_by(PublicationTask.created_at.asc()).all()
                self.assertEqual(len(tasks), 2)
                features = {task.feature for task in tasks}
                self.assertEqual(features, {"report_pdf_audio_workflow", "mp3_video_workflow"})
                for task in tasks:
                    self.assertEqual(task.parent_feature, "zsxq_sync_run")
                    self.assertEqual(task.parent_task_id, "run-auto-video")
                    self.assertEqual(task.source_feature, "zsxq_attachment")

                report_task = next(task for task in tasks if task.feature == "report_pdf_audio_workflow")
                report_workflow = report_task.params["report_audio_workflow"]
                self.assertTrue(report_workflow["video"]["enabled"])
                self.assertTrue(report_workflow["video"]["vertical_enabled"])
                self.assertFalse(report_workflow["video"]["bilibili_enabled"])
                self.assertEqual(report_task.report_audio_pdf_attachment_id, "att-pdf")

                mp3_task = next(task for task in tasks if task.feature == "mp3_video_workflow")
                self.assertEqual(mp3_task.source_task_id, "att-mp3")
                self.assertEqual(mp3_task.params["mp3_video_workflow"]["audio_asset_id"], "att-mp3")

                db.session.remove()
                db.drop_all()

    def test_script_material_dispatch_queues_script_only_pdf_workflow(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "report.pdf"
            pdf_path.write_bytes(b"%PDF-1.4\nfixture")

            app = Flask(__name__)
            app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
            app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
            db.init_app(app)

            with app.app_context():
                db.create_all()
                run = ZsxqSyncRun(
                    id="run-script-material",
                    group_id="group-1",
                    group_name="前沿信息收录",
                    result={},
                )
                topic = ZsxqTopic(
                    group_id="group-1",
                    group_name="前沿信息收录",
                    tab_id="tab-pdf",
                    tab_label="外资研报",
                    topic_id="topic-pdf",
                    title="AI 算力深度报告",
                )
                attachment = ZsxqAttachment(
                    id="att-script-material",
                    group_id="group-1",
                    tab_id="tab-pdf",
                    topic_id="topic-pdf",
                    attachment_key="pdf-key",
                    name="report.pdf",
                    download_count=40,
                    local_path=str(pdf_path),
                    download_status="downloaded",
                )
                db.session.add_all([run, topic, attachment])
                db.session.commit()

                sync_result = {
                    "downloaded_files": [{"path": str(pdf_path), "attachment_id": "att-script-material"}],
                    "downloaded_pdf_files": [{"path": str(pdf_path), "attachment_id": "att-script-material"}],
                }
                config = {
                    "enabled": True,
                    "pdf_min_download_count": 40,
                    "wait_for_completion": False,
                }

                with patch(
                    "tasks.report_script_material_workflow.process_report_script_material_workflow_job.apply_async",
                    return_value=SimpleNamespace(id="celery-script-material"),
                ) as report_apply:
                    summary = ZsxqSyncService()._submit_auto_script_material_dispatch_for_run(
                        run=run,
                        sync_result=sync_result,
                        config=config,
                        workflow_run_id="workflow-1",
                    )

                self.assertEqual(summary["status"], "completed")
                self.assertEqual(summary["pdf"]["queued"], 1)
                self.assertEqual(summary["queued_count"], 1)
                report_apply.assert_called_once()

                task = PublicationTask.query.one()
                self.assertEqual(task.feature, "report_script_material_workflow")
                self.assertEqual(task.parent_feature, "zsxq_sync_run")
                self.assertEqual(task.parent_task_id, "run-script-material")
                self.assertEqual(task.source_feature, "zsxq_attachment")
                self.assertEqual(task.source_task_id, "att-script-material")
                self.assertEqual(task.report_audio_pdf_attachment_id, "att-script-material")
                workflow = task.params["report_script_material_workflow"]
                self.assertFalse(workflow["video"]["enabled"])
                self.assertFalse(workflow["video"]["vertical_enabled"])
                self.assertEqual(task.result["script_material"]["status"], "pending")
                self.assertEqual(
                    task.result["auto_script_material_dispatch"]["threshold"],
                    40,
                )

                db.session.remove()
                db.drop_all()

    def test_script_material_dispatch_reports_deferred_as_queued_work_not_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "report.pdf"
            pdf_path.write_bytes(b"%PDF-1.4\nfixture")

            app = Flask(__name__)
            app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
            app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
            db.init_app(app)

            with app.app_context():
                db.create_all()
                run = ZsxqSyncRun(id="run-script-material-deferred", group_id="group-1", result={})
                topic = ZsxqTopic(
                    group_id="group-1",
                    tab_id="tab-pdf",
                    topic_id="topic-pdf",
                    title="AI 算力深度报告",
                )
                attachment = ZsxqAttachment(
                    id="att-script-material-deferred",
                    group_id="group-1",
                    tab_id="tab-pdf",
                    topic_id="topic-pdf",
                    attachment_key="pdf-key",
                    name="report.pdf",
                    download_count=40,
                    local_path=str(pdf_path),
                    download_status="downloaded",
                )
                db.session.add_all([run, topic, attachment])
                db.session.commit()

                sync_result = {
                    "downloaded_files": [{"path": str(pdf_path), "attachment_id": "att-script-material-deferred"}],
                }
                config = {
                    "enabled": True,
                    "pdf_min_download_count": 40,
                    "wait_for_completion": False,
                }

                with patch(
                    "services.zsxq_sync_service.maybe_guarded_apply_async",
                    return_value={
                        "async_result": SimpleNamespace(id="celery-deferred"),
                        "deferred": True,
                        "admission": {"allowed": False, "reason": "feature_backlog"},
                    },
                ):
                    summary = ZsxqSyncService()._submit_auto_script_material_dispatch_for_run(
                        run=run,
                        sync_result=sync_result,
                        config=config,
                        workflow_run_id="workflow-1",
                    )

                self.assertEqual(summary["status"], "completed")
                self.assertEqual(summary["pdf"]["queued"], 0)
                self.assertEqual(summary["pdf"]["deferred"], 1)
                self.assertEqual(summary["pdf"]["failed"], 0)
                self.assertEqual(summary["queued_count"], 0)
                self.assertEqual(summary["deferred_count"], 1)
                self.assertEqual(summary["dispatched_count"], 1)
                self.assertEqual(summary["failed_count"], 0)

                db.session.remove()
                db.drop_all()

    def test_auto_video_dispatch_ignores_existing_script_material_only_task(self):
        app = Flask(__name__)
        app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
        app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
        db.init_app(app)

        with app.app_context():
            db.create_all()
            attachment = ZsxqAttachment(
                id="att-pdf",
                group_id="group-1",
                tab_id="tab-pdf",
                topic_id="topic-pdf",
                attachment_key="pdf-key",
                name="report.pdf",
                download_count=40,
                local_path="/tmp/report.pdf",
                download_status="downloaded",
            )
            material_task = PublicationTask(
                status="pending",
                current_step="queued",
                progress=0,
                params={
                    "feature": "report_pdf_audio_workflow",
                    "source_feature": "zsxq_attachment",
                    "source_task_id": "att-pdf",
                    "report_audio_workflow": {
                        "script_material_only": True,
                        "pdf": {"attachment_id": "att-pdf", "filename": "report.pdf"},
                    },
                },
                result={"script_material": {"status": "pending"}},
            )
            db.session.add_all([attachment, material_task])
            db.session.commit()

            existing = ZsxqSyncService()._find_existing_auto_video_task(attachment, "pdf")

            self.assertIsNone(existing)

            db.session.remove()
            db.drop_all()


if __name__ == "__main__":
    unittest.main()
