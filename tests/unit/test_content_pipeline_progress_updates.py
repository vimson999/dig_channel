import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from models import db  # noqa: E402
from models.task import PublicationTask  # noqa: E402
from tasks import content_pipeline  # noqa: E402


class _SuccessfulPdfAdapter:
    last_checkpoint = None

    def __init__(self, **_kwargs):
        pass

    def run_batch(self, *, batch_id, progress_callback, **_kwargs):
        progress_callback({
            "phase": "local_cleaning",
            "progress": 46,
            "job_id": "pdf-job-1",
            "job": {"message": "PDF cleaning in progress"},
        })
        checkpoint = PublicationTask.query.filter(PublicationTask.id == batch_id).one()
        type(self).last_checkpoint = dict(checkpoint.result or {})
        db.session.remove()
        return {
            "batch_id": batch_id,
            "local_output_dir": "/tmp/pdf-cleaned",
            "cleaned_files": [{
                "source_path": "/tmp/a.pdf",
                "path": "/tmp/pdf-cleaned/a.pdf",
            }],
            "failed_count": 0,
        }


class _SuccessfulAudioAdapter:
    def __init__(self, **_kwargs):
        pass

    def run_batch(self, *, batch_id, progress_callback, **_kwargs):
        progress_callback({
            "phase": "audio_cleaning",
            "progress": 63,
            "message": "Audio cleaning in progress",
        })
        return {
            "batch_id": batch_id,
            "local_output_dir": "/tmp/audio-cleaned",
            "cleaned_files": [{
                "source_path": "/tmp/a.mp3",
                "path": "/tmp/audio-cleaned/a.mp3",
            }],
            "failed_count": 0,
        }


class ContentPipelineProgressUpdateTest(unittest.TestCase):
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

    def _task(self, feature, pipeline):
        clean_config = pipeline.get("pdf_clean") or pipeline.get("audio_clean") or {}
        source_path = (clean_config.get("files") or [""])[0]
        task = PublicationTask(
            feature=feature,
            status="pending",
            current_step="queued",
            progress=0,
            params={
                "feature": feature,
                "content_pipeline": {
                    **pipeline,
                    "metadata": {
                        "workflow_run_id": "workflow-1",
                        "group_id": "group-1",
                        "files": [{
                            "path": source_path,
                            "attachment_id": "attachment-1",
                            "tab_id": "tab-1",
                            "topic_id": "topic-1",
                        }],
                    },
                    "auto_publish": {"enabled": False},
                },
            },
            result={"preserved": "yes"},
        )
        db.session.add(task)
        db.session.commit()
        return str(task.id)

    def test_pdf_progress_keeps_audit_result_and_final_output(self):
        _SuccessfulPdfAdapter.last_checkpoint = None
        task_id = self._task(
            "content_pdf_clean",
            {"pdf_clean": {"files": ["/tmp/a.pdf"], "cleaner": {"engine": "local"}}},
        )

        with patch.object(content_pipeline, "_create_content_pipeline_app", return_value=self.app), \
             patch.object(content_pipeline, "PdfCleanerAdapter", _SuccessfulPdfAdapter), \
             patch.object(content_pipeline, "_notify_workflow_run", return_value=True) as notify:
            ok = content_pipeline._run_pdf_clean_task_impl_guarded(task_id)

        self.assertTrue(ok)
        task = PublicationTask.query.filter(PublicationTask.id == task_id).one()
        self.assertEqual(task.status, "completed")
        self.assertEqual(task.current_step, "done")
        self.assertEqual(task.progress, 100)
        self.assertEqual(task.result["preserved"], "yes")
        self.assertEqual(task.result["cleaner_job_id"], "pdf-job-1")
        self.assertEqual(task.result["message"], "PDF cleaning in progress")
        self.assertEqual(task.result["cleaned_files"][0]["path"], "/tmp/pdf-cleaned/a.pdf")
        self.assertEqual(task.content_clean_output_dir, "/tmp/pdf-cleaned")
        self.assertEqual(task.content_clean_files_count, 1)
        self.assertEqual(task.content_clean_success_count, 1)
        self.assertEqual(task.content_clean_failed_count, 0)
        self.assertEqual(_SuccessfulPdfAdapter.last_checkpoint["cleaner_job_id"], "pdf-job-1")
        self.assertEqual(_SuccessfulPdfAdapter.last_checkpoint["preserved"], "yes")
        notify.assert_called_once()
        event = notify.call_args.kwargs["event_payload"]
        self.assertEqual(event["task_id"], task_id)
        self.assertEqual(event["source_refs"][0]["attachment_id"], "attachment-1")
        self.assertEqual(
            event["source_refs"][0]["output_paths"],
            [str(Path("/tmp/pdf-cleaned/a.pdf").resolve())],
        )

    def test_audio_progress_keeps_message_and_final_output(self):
        task_id = self._task(
            "content_audio_clean",
            {"audio_clean": {"files": ["/tmp/a.mp3"], "cleaner": {}}},
        )

        with patch.object(content_pipeline, "_create_content_pipeline_app", return_value=self.app), \
             patch.object(content_pipeline, "AudioCleanerAdapter", _SuccessfulAudioAdapter), \
             patch.object(content_pipeline, "_notify_workflow_run", return_value=True) as notify:
            ok = content_pipeline._run_audio_clean_task_impl_guarded(task_id)

        self.assertTrue(ok)
        task = PublicationTask.query.filter(PublicationTask.id == task_id).one()
        self.assertEqual(task.status, "completed")
        self.assertEqual(task.current_step, "done")
        self.assertEqual(task.progress, 100)
        self.assertEqual(task.result["preserved"], "yes")
        self.assertEqual(task.result["message"], "Audio cleaning in progress")
        self.assertEqual(task.result["cleaned_files"][0]["path"], "/tmp/audio-cleaned/a.mp3")
        self.assertEqual(task.content_clean_output_dir, "/tmp/audio-cleaned")
        self.assertEqual(task.content_clean_files_count, 1)
        self.assertEqual(task.content_clean_success_count, 1)
        self.assertEqual(task.content_clean_failed_count, 0)
        notify.assert_called_once()
        event = notify.call_args.kwargs["event_payload"]
        self.assertEqual(event["task_id"], task_id)
        self.assertEqual(event["source_refs"][0]["attachment_id"], "attachment-1")
        self.assertEqual(
            event["source_refs"][0]["output_paths"],
            [str(Path("/tmp/audio-cleaned/a.mp3").resolve())],
        )


if __name__ == "__main__":
    unittest.main()
