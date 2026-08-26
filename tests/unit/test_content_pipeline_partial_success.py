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

from models import db  # noqa: E402
from models.content_pipeline_asset import ContentPipelineAsset  # noqa: E402
from models.task import PublicationTask  # noqa: E402
from models.zsxq_sync import ZsxqAttachment, ZsxqSyncRun, ZsxqTopic  # noqa: E402
from tasks import content_pipeline  # noqa: E402


class ContentPipelinePartialSuccessTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.output_root = self.root / "runtime" / "content_pipeline" / "batches"
        self.app = Flask(__name__)
        self.app.config.update(
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(self.app)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        self.env_patch = patch.dict(
            os.environ,
            {"CONTENT_PIPELINE_OUTPUT_ROOT": str(self.output_root)},
            clear=False,
        )
        self.env_patch.start()

    def tearDown(self):
        self.env_patch.stop()
        db.session.remove()
        db.drop_all()
        self.ctx.pop()
        self.temp_dir.cleanup()

    def _task_and_assets(self, media_type):
        source_root = self.root / "downloads"
        source_root.mkdir()
        sources = [source_root / f"{index}.{ 'pdf' if media_type == 'pdf' else 'mp3'}" for index in (1, 2)]
        for source in sources:
            source.write_bytes(b"raw")

        config_key = "pdf_clean" if media_type == "pdf" else "audio_clean"
        task = PublicationTask(
            feature=f"content_{media_type}_clean",
            status="pending",
            current_step="queued",
            progress=0,
            params={
                "feature": f"content_{media_type}_clean",
                "content_pipeline": {
                    config_key: {
                        "files": [str(source) for source in sources],
                        "source_root": str(source_root),
                        "cleaner": {"engine": "local"} if media_type == "pdf" else {},
                    },
                    "auto_publish": {"enabled": False},
                },
            },
        )
        db.session.add(task)
        db.session.flush()
        assets = []
        for index, source in enumerate(sources, start=1):
            asset = ContentPipelineAsset(
                media_type=media_type,
                content_sha256=f"{index:064x}",
                canonical_task_id=str(task.id),
                source_path=str(source.resolve()),
                filename=source.name,
                source_mode="manual_download",
                status="reserved",
            )
            db.session.add(asset)
            assets.append(asset)
        db.session.commit()
        return str(task.id), sources, assets

    def _partial_result(self, *, task_id, sources, media_type):
        extension = ".pdf" if media_type == "pdf" else ".mp3"
        output = self.output_root / task_id / f"{media_type}_cleaned" / f"1{extension}"
        output.parent.mkdir(parents=True)
        output.write_bytes(b"cleaned")
        return {
            "batch_id": task_id,
            "local_output_dir": str(output.parent),
            "files": [
                {
                    "source_path": str(sources[0]),
                    "status": "SUCCESS",
                    "output_path": str(output),
                    "path": str(output),
                },
                {
                    "source_path": str(sources[1]),
                    "status": "FAILED",
                    "path": str(output.parent / f"2{extension}"),
                },
            ],
            "cleaned_files": [
                {
                    "source_path": str(sources[0]),
                    "status": "SUCCESS",
                    "output_path": str(output),
                    "path": str(output),
                },
            ],
            "success_count": 1,
            "failed_count": 1,
        }

    def _assert_partial_success(self, *, task_id, assets):
        task = PublicationTask.query.filter(PublicationTask.id == task_id).one()
        db.session.refresh(assets[0])
        db.session.refresh(assets[1])
        self.assertEqual(task.status, "completed")
        self.assertEqual(task.current_step, "done")
        self.assertEqual(task.content_clean_success_count, 1)
        self.assertEqual(task.content_clean_failed_count, 1)
        self.assertEqual(assets[0].status, "cleaned")
        self.assertEqual(assets[1].status, "failed")
        self.assertEqual(len(task.result["cleaned_files"]), 1)

    def test_pdf_partial_clean_success_completes_and_keeps_failed_asset_individual(self):
        task_id, sources, assets = self._task_and_assets("pdf")
        result = self._partial_result(task_id=task_id, sources=sources, media_type="pdf")

        with patch.object(content_pipeline, "_create_content_pipeline_app", return_value=self.app), \
             patch.object(content_pipeline, "PdfCleanerAdapter") as adapter, \
             patch.object(content_pipeline, "_notify_content_pipeline_event", return_value={"queued": False}), \
             patch.object(content_pipeline, "_notify_workflow_run", return_value=True):
            adapter.return_value.run_batch.return_value = result
            ok = content_pipeline._run_pdf_clean_task_impl_guarded(task_id)

        self.assertTrue(ok)
        self._assert_partial_success(task_id=task_id, assets=assets)

    def test_audio_partial_clean_success_completes_and_keeps_failed_asset_individual(self):
        task_id, sources, assets = self._task_and_assets("audio")
        result = self._partial_result(task_id=task_id, sources=sources, media_type="audio")

        with patch.object(content_pipeline, "_create_content_pipeline_app", return_value=self.app), \
             patch.object(content_pipeline, "AudioCleanerAdapter") as adapter, \
             patch.object(content_pipeline, "_notify_content_pipeline_event", return_value={"queued": False}), \
             patch.object(content_pipeline, "_notify_workflow_run", return_value=True):
            adapter.return_value.run_batch.return_value = result
            ok = content_pipeline._run_audio_clean_task_impl_guarded(task_id)

        self.assertTrue(ok)
        self._assert_partial_success(task_id=task_id, assets=assets)

    def test_audio_clean_dispatches_video_from_registered_output_after_raw_file_is_removed(self):
        """The auto-video branch must consume the registered clean file, never Downloads."""
        task_id, sources, _assets = self._task_and_assets("audio")
        clean_result = self._partial_result(
            task_id=task_id,
            sources=sources,
            media_type="audio",
        )
        cleaned_path = Path(clean_result["cleaned_files"][0]["output_path"])

        run = ZsxqSyncRun(
            id="run-post-clean-video",
            group_id="group-1",
            group_name="前沿信息收录",
            result={},
        )
        topic = ZsxqTopic(
            group_id="group-1",
            group_name="前沿信息收录",
            tab_id="tab-mp3",
            tab_label="调研纪要",
            topic_id="topic-mp3",
            title="机器人调研纪要",
        )
        attachment = ZsxqAttachment(
            id="att-post-clean-audio",
            group_id="group-1",
            tab_id="tab-mp3",
            topic_id="topic-mp3",
            attachment_key="post-clean-audio",
            name=sources[0].name,
            file_ext="mp3",
            download_count=26,
            local_path=str(sources[0]),
            download_status="downloaded",
        )
        db.session.add_all([run, topic, attachment])
        task = db.session.get(PublicationTask, task_id)
        pipeline = dict(task.params["content_pipeline"])
        pipeline["metadata"] = {
            "sync_run_id": run.id,
            "group_id": run.group_id,
            "group_name": run.group_name,
            "auto_video_dispatch": {
                "enabled": True,
                "pdf_min_download_count": 35,
                "mp3_min_download_count": 25,
                "wait_for_completion": False,
            },
            "files": [
                {
                    "path": str(sources[0]),
                    "name": sources[0].name,
                    "attachment_id": attachment.id,
                    "group_id": run.group_id,
                    "tab_id": attachment.tab_id,
                    "topic_id": attachment.topic_id,
                }
            ],
        }
        task.params = {**task.params, "content_pipeline": pipeline}
        db.session.commit()

        def return_clean_result_and_remove_raw(*_args, **_kwargs):
            sources[0].unlink()
            return clean_result

        with patch.object(content_pipeline, "_create_content_pipeline_app", return_value=self.app), \
             patch.object(content_pipeline, "AudioCleanerAdapter") as adapter, \
             patch.object(content_pipeline, "_notify_content_pipeline_event", return_value={"queued": False}), \
             patch.object(content_pipeline, "_notify_workflow_run", return_value=True), \
             patch(
                 "tasks.mp3_video_workflow.process_mp3_video_workflow_job.apply_async",
                 return_value=SimpleNamespace(id="celery-post-clean-audio"),
             ), \
             patch(
                 "services.workflow_notification_service.workflow_notification_service.send_email_message"
             ):
            adapter.return_value.run_batch.side_effect = return_clean_result_and_remove_raw
            ok = content_pipeline._run_audio_clean_task_impl_guarded(task_id)

        self.assertTrue(ok)
        self.assertFalse(sources[0].exists())
        video_task = PublicationTask.query.filter(
            PublicationTask.feature == "mp3_video_workflow"
        ).one()
        audio = video_task.params["mp3_video_workflow"]["audio"]
        self.assertEqual(audio["attachment_id"], attachment.id)
        self.assertEqual(audio["path"], str(cleaned_path.resolve()))

    def test_pdf_clean_dispatches_script_material_from_registered_output_after_raw_file_is_removed(self):
        """The script-material branch must consume the registered clean PDF, never Downloads."""
        task_id, sources, _assets = self._task_and_assets("pdf")
        clean_result = self._partial_result(
            task_id=task_id,
            sources=sources,
            media_type="pdf",
        )
        cleaned_path = Path(clean_result["cleaned_files"][0]["output_path"])

        run = ZsxqSyncRun(
            id="run-post-clean-script",
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
            id="att-post-clean-pdf",
            group_id="group-1",
            tab_id="tab-pdf",
            topic_id="topic-pdf",
            attachment_key="post-clean-pdf",
            name=sources[0].name,
            file_ext="pdf",
            download_count=40,
            local_path=str(sources[0]),
            download_status="downloaded",
        )
        db.session.add_all([run, topic, attachment])
        task = db.session.get(PublicationTask, task_id)
        pipeline = dict(task.params["content_pipeline"])
        pipeline["metadata"] = {
            "sync_run_id": run.id,
            "group_id": run.group_id,
            "group_name": run.group_name,
            "auto_script_material_dispatch": {
                "enabled": True,
                "pdf_min_download_count": 40,
                "visual_scheme_keys": [],
                "wait_for_completion": False,
            },
            "files": [
                {
                    "path": str(sources[0]),
                    "name": sources[0].name,
                    "attachment_id": attachment.id,
                    "group_id": run.group_id,
                    "tab_id": attachment.tab_id,
                    "topic_id": attachment.topic_id,
                }
            ],
        }
        task.params = {**task.params, "content_pipeline": pipeline}
        db.session.commit()

        def return_clean_result_and_remove_raw(*_args, **_kwargs):
            sources[0].unlink()
            return clean_result

        with patch.object(content_pipeline, "_create_content_pipeline_app", return_value=self.app), \
             patch.object(content_pipeline, "PdfCleanerAdapter") as adapter, \
             patch.object(content_pipeline, "_notify_content_pipeline_event", return_value={"queued": False}), \
             patch.object(content_pipeline, "_notify_workflow_run", return_value=True), \
             patch(
                 "tasks.report_script_material_workflow.process_report_script_material_workflow_job.apply_async",
                 return_value=SimpleNamespace(id="celery-post-clean-pdf"),
             ):
            adapter.return_value.run_batch.side_effect = return_clean_result_and_remove_raw
            ok = content_pipeline._run_pdf_clean_task_impl_guarded(task_id)

        self.assertTrue(ok)
        self.assertFalse(sources[0].exists())
        script_task = PublicationTask.query.filter(
            PublicationTask.feature == "report_script_material_workflow"
        ).one()
        pdf = script_task.params["report_script_material_workflow"]["pdf"]
        self.assertEqual(pdf["attachment_id"], attachment.id)
        self.assertEqual(pdf["path"], str(cleaned_path.resolve()))


if __name__ == "__main__":
    unittest.main()
