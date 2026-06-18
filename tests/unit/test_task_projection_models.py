import sys
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from models.task import PublicationTask  # noqa: E402
from models.zsxq_sync import ZsxqAttachment, ZsxqSyncRun  # noqa: E402


class TaskProjectionModelTest(unittest.TestCase):
    def test_publication_task_projection_from_report_audio_payload(self):
        task = PublicationTask(
            params={
                "feature": "report_pdf_audio_workflow",
                "user_id": "1",
                "report_audio_workflow": {
                    "pdf": {
                        "attachment_id": "asset-1",
                        "filename": "sample.pdf",
                        "bytes": 123,
                        "topic_title": "原始标题",
                    },
                    "metadata": {"title": "元数据标题"},
                    "video": {"title": "视频标题"},
                },
            },
            result={
                "started_at": "2026-06-17T01:02:03Z",
                "pdf": {"filename": "clean.pdf", "bytes": 456, "topic_title": "结果标题"},
                "structured_result": {"cover_title": "封面标题", "title": "结构化标题"},
                "audio": {"wav_path": "/tmp/audio.wav", "seconds": 12.5, "bytes": 789},
                "video": {
                    "enabled": "true",
                    "status": "completed",
                    "publisher_task_id": "pub-1",
                    "message": "ok",
                    "video_path": "/tmp/video.mp4",
                    "cover_path": "/tmp/cover.png",
                },
                "output_path": "/tmp/result.json",
            },
        )

        self.assertEqual(task.feature, "report_pdf_audio_workflow")
        self.assertEqual(task.user_id, "1")
        self.assertEqual(task.report_audio_pdf_attachment_id, "asset-1")
        self.assertEqual(task.report_audio_pdf_filename, "sample.pdf")
        self.assertEqual(task.report_audio_result_pdf_filename, "clean.pdf")
        self.assertEqual(task.report_audio_cover_title, "封面标题")
        self.assertEqual(task.report_audio_wav_path, "/tmp/audio.wav")
        self.assertTrue(task.report_audio_video_enabled)
        self.assertEqual(task.report_audio_video_path, "/tmp/video.mp4")
        self.assertEqual(task.task_title, "元数据标题")

    def test_publication_task_projection_defaults_legacy_publisher_feature(self):
        task = PublicationTask(params={"bilibili": {"title": "发布标题"}}, result={"video_path": "/tmp/a.mp4"})

        self.assertEqual(task.feature, "publisher")
        self.assertEqual(task.task_title, "发布标题")
        self.assertEqual(task.task_video_path, "/tmp/a.mp4")

    def test_zsxq_attachment_file_ext_projection(self):
        attachment = ZsxqAttachment(
            group_id="g",
            tab_id="t",
            topic_id="topic",
            attachment_key="k",
            name="研报.PDF",
            local_path="/tmp/other.txt",
        )

        self.assertEqual(attachment.file_ext, "pdf")

    def test_zsxq_sync_run_projection_from_system_result(self):
        run = ZsxqSyncRun(
            group_id="28888112822211",
            mode="sys_manual",
            status="running",
            result={
                "system_sync": {
                    "batch_id": "manual-20260617-1",
                    "attempt_id": "attempt-1",
                    "round": "2",
                    "max_rounds": "5",
                    "trigger": "manual",
                    "queued_at": "2026-06-17T01:02:03Z",
                    "max_download_files": "3",
                },
                "topics_fetched": "12",
                "topics_new": 2,
                "attachments_downloaded": "3",
                "download_limit_reached": "true",
            },
        )

        self.assertEqual(run.system_batch_id, "manual-20260617-1")
        self.assertEqual(run.system_attempt_id, "attempt-1")
        self.assertEqual(run.system_round, 2)
        self.assertEqual(run.system_max_rounds, 5)
        self.assertEqual(run.system_trigger, "manual")
        self.assertEqual(run.system_max_download_files, 3)
        self.assertEqual(run.topics_fetched, 12)
        self.assertEqual(run.topics_new, 2)
        self.assertEqual(run.attachments_downloaded, 3)
        self.assertTrue(run.download_limit_reached)

    def test_zsxq_sync_run_projection_falls_back_to_partial_progress(self):
        run = ZsxqSyncRun(
            group_id="28888112822211",
            mode="sys_manual",
            result={
                "system_sync": {"batch_id": "batch-1"},
                "partial": {
                    "topics_fetched": "4",
                    "topics_existing": "3",
                    "attachments_seen": "8",
                    "download_limit_reached": "false",
                },
            },
        )

        self.assertEqual(run.system_batch_id, "batch-1")
        self.assertEqual(run.topics_fetched, 4)
        self.assertEqual(run.topics_existing, 3)
        self.assertEqual(run.attachments_seen, 8)
        self.assertFalse(run.download_limit_reached)


if __name__ == "__main__":
    unittest.main()
