import json
import os
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
from models.content_pipeline_asset import ContentPipelineAsset  # noqa: E402
from models.task import PublicationTask  # noqa: E402
from models.zsxq_sync import ZsxqAttachment, ZsxqTopic  # noqa: E402
from services.report_script_material_workflow import FEATURE_NAME, QUEUE_NAME  # noqa: E402


class ReportScriptMaterialWorkflowApiTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.disk_cache_patcher = patch.object(
            material_api,
            "TERMINAL_DETAIL_DISK_ROOT",
            Path(self.tmpdir.name) / "detail-cache",
        )
        self.disk_cache_patcher.start()
        self.app = Flask(__name__)
        self.app.config.update(
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(self.app)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        self.output_root = Path(self.tmpdir.name) / "content-pipeline-output"
        self.output_root_patch = patch.dict(
            os.environ,
            {"CONTENT_PIPELINE_OUTPUT_ROOT": str(self.output_root)},
            clear=False,
        )
        self.output_root_patch.start()
        material_api._clear_terminal_detail_cache()

    def tearDown(self):
        material_api._clear_terminal_detail_cache()
        self.output_root_patch.stop()
        self.disk_cache_patcher.stop()
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
        cleaned_path = self.output_root / "batch-1" / "pdf_cleaned" / pdf_path.name
        cleaned_path.parent.mkdir(parents=True)
        cleaned_path.write_bytes(b"%PDF-cleaned")
        clean_task = PublicationTask(
            feature="content_pdf_clean",
            status="completed",
            current_step="completed",
            progress=100,
            params={"feature": "content_pdf_clean"},
        )
        db.session.add_all([topic, attachment, clean_task])
        db.session.flush()
        db.session.add(ContentPipelineAsset(
            media_type="pdf",
            content_sha256="a" * 64,
            canonical_task_id=str(clean_task.id),
            source_path=str(pdf_path.resolve()),
            filename=pdf_path.name,
            source_mode="sidebar",
            status="cleaned",
            cleaned_path=str(cleaned_path.resolve()),
            cleaned_sha256="b" * 64,
            cleaned_bytes=cleaned_path.stat().st_size,
        ))
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

        task = PublicationTask.query.filter(PublicationTask.feature == FEATURE_NAME).one()
        self.assertEqual(result["status"], "queued")
        self.assertEqual(result["job_id"], task.id)
        self.assertEqual(task.feature, FEATURE_NAME)
        self.assertEqual(task.celery_task_id, "celery-material")
        self.assertEqual(task.source_feature, "zsxq_attachment")
        self.assertEqual(task.source_task_id, "asset-1")
        self.assertEqual(task.report_audio_pdf_attachment_id, "asset-1")
        self.assertEqual(task.params[FEATURE_NAME]["pdf"]["attachment_id"], "asset-1")
        self.assertFalse(task.params[FEATURE_NAME]["pdf_evidence_enabled"])
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

    def test_completed_detail_cache_skips_database_and_is_user_scoped(self):
        first_user = SimpleNamespace(id="user-1")
        second_user = SimpleNamespace(id="user-2")
        payload = {
            "success": True,
            "job_id": "job-1",
            "status": "succeeded",
            "data": {"final_script_for_tts": "完整文案", "logs": []},
        }

        with patch.object(material_api, "_terminal_detail_redis_client", return_value=None):
            material_api._cache_terminal_detail(first_user, "job-1", payload)
            with self.app.test_request_context():
                with patch.object(
                    material_api,
                    "_get_owned_task",
                    side_effect=AssertionError("cached request must not query publication_tasks"),
                ):
                    response = material_api.get_job.__wrapped__(first_user, "job-1")

            self.assertEqual(payload, response.get_json())
            self.assertIsNone(material_api._cached_terminal_detail(second_user, "job-1"))
            response.get_json()["data"]["final_script_for_tts"] = "mutated"
            self.assertEqual(
                "完整文案",
                material_api._cached_terminal_detail(first_user, "job-1")["data"]["final_script_for_tts"],
            )

    def test_completed_detail_cache_survives_process_memory_loss(self):
        class FakeRedis:
            def __init__(self):
                self.values = {}

            def get(self, key):
                return self.values.get(key)

            def setex(self, key, _ttl, value):
                self.values[key] = value

        user = SimpleNamespace(id="user-1")
        payload = {"success": True, "job_id": "job-1", "data": {"logs": ["done"]}}
        fake_redis = FakeRedis()

        with patch.object(material_api, "_terminal_detail_redis_client", return_value=fake_redis):
            material_api._cache_terminal_detail(user, "job-1", payload)
            material_api._TERMINAL_DETAIL_CACHE.clear()
            restored = material_api._cached_terminal_detail(user, "job-1")

        self.assertEqual(payload, restored)
        self.assertTrue(fake_redis.values)

    def test_completed_detail_disk_cache_survives_redis_timeout(self):
        user = SimpleNamespace(id="user-1")
        payload = {"success": True, "job_id": "job-1", "data": {"logs": ["done"]}}
        material_api._terminal_detail_disk_set(user, "job-1", payload)
        material_api._TERMINAL_DETAIL_CACHE.clear()

        with patch.object(
            material_api,
            "_persistent_terminal_detail_get",
            side_effect=AssertionError("disk cache must be checked before Redis"),
        ):
            restored = material_api._cached_terminal_detail(user, "job-1")

        self.assertEqual(payload, restored)

    def test_completed_detail_reads_versioned_result_file_without_json_columns(self):
        task = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            feature=FEATURE_NAME,
            user_id="user-1",
            task_title="完整物料",
            final_script_chars=4,
            params={"feature": FEATURE_NAME, "large": "ignored"},
            result={"large": "ignored"},
        )
        db.session.add(task)
        db.session.commit()
        PublicationTask.query.filter(PublicationTask.id == task.id).update({
            PublicationTask.feature: FEATURE_NAME,
            PublicationTask.user_id: "user-1",
        }, synchronize_session=False)
        db.session.commit()
        db.session.expire_all()
        result_path = Path(self.tmpdir.name) / "result.json"
        result_path.write_text(
            json.dumps({
                "detail_cache_version": material_api.RESULT_DETAIL_CACHE_VERSION,
                "structured_result": {"final_script_for_tts": "完整文案"},
                "script_material": {"status": "completed"},
                "children": {},
                "logs": ["done"],
                "tts_text": {"chars": 4, "chunks": 1},
            }, ensure_ascii=False),
            encoding="utf-8",
        )
        user = SimpleNamespace(id="user-1")

        with self.app.test_request_context():
            with patch.object(material_api, "_cached_terminal_detail", return_value=None), patch.object(
                material_api, "_result_file_path", return_value=str(result_path)
            ), patch.object(
                material_api, "_get_owned_task",
                side_effect=AssertionError("versioned result file must avoid params/result SELECT"),
            ):
                response = material_api.get_job.__wrapped__(user, task.id)

        data = response.get_json()["data"]
        self.assertEqual("完整文案", data["final_script_for_tts"])
        self.assertEqual(["done"], data["logs"])


if __name__ == "__main__":
    unittest.main()
