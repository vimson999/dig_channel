import sys
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.workflow_notification_service import workflow_notification_service  # noqa: E402


class WorkflowNotificationServiceTest(unittest.TestCase):
    def test_skipped_clean_checks_are_not_reported_as_running(self):
        workflow = {
            "stage_results": {
                "clean": {
                    "status": "skipped",
                    "tasks": [
                        {"queued": False, "reason": "no_downloaded_pdf", "files_count": 0},
                        {"queued": False, "reason": "no_downloaded_audio", "files_count": 0},
                    ],
                }
            }
        }

        lines = workflow_notification_service._stage_lines(workflow)

        self.assertIn("清洗：跳过 | 检查 2 项，跳过 2", lines)
        self.assertNotIn("进行中 2", "\n".join(lines))

    def test_stage_lines_include_download_clean_publish_totals(self):
        workflow = {
            "stage_results": {
                "sync": {
                    "status": "completed",
                    "summary": {
                        "topics_new": 6,
                        "topics_updated": 1,
                        "attachments_downloaded": 23,
                        "attachments_failed": 2,
                    },
                },
                "clean": {
                    "status": "completed",
                    "tasks": [
                        {"status": "completed", "files_count": 23, "success_count": 23, "failed_count": 0},
                    ],
                },
                "publish": {
                    "status": "completed",
                    "tasks": [
                        {
                            "status": "completed",
                            "summary": {
                                "submitted_count": 23,
                                "verified_count": 7,
                                "pending_verify_count": 16,
                                "failed_count": 0,
                            },
                        },
                    ],
                },
            }
        }

        text = "\n".join(workflow_notification_service._stage_lines(workflow))

        self.assertIn("下载 23 个，下载失败 2 个", text)
        self.assertIn("清洗成功 23 个，清洗失败 0 个", text)
        self.assertIn("发布已提交 23 个，已确认可见 7 个，待审核/待确认 16 个，发布失败 0 个", text)
        self.assertIn("发布：成功 | 任务 1 个，完成 1，失败 0，跳过 0，进行中 0 | 已提交 23，已确认 7，待审核/待确认 16，失败 0", text)

    def test_stage_lines_include_publish_batch_breakdown_and_recovered_note(self):
        workflow = {
            "stage_results": {
                "sync": {
                    "status": "completed",
                    "summary": {"attachments_downloaded": 21, "attachments_failed": 1},
                },
                "clean": {
                    "status": "completed",
                    "tasks": [
                        {"status": "completed", "success_count": 1, "failed_count": 0},
                        {"status": "completed", "success_count": 20, "failed_count": 0},
                    ],
                },
                "publish": {
                    "status": "partial",
                    "tasks": [
                        {
                            "status": "completed",
                            "file_type": "pdf",
                            "summary": {"submitted_count": 1, "verified_count": 1, "topic_count": 1},
                            "publish_runtime": {
                                "batches": [{"submitted_count": 1, "verified_count": 1, "topic_id": "pdf-topic"}],
                            },
                        },
                        {
                            "status": "failed",
                            "file_type": "audio",
                            "summary": {
                                "submitted_count": 20,
                                "verified_count": 20,
                                "pending_verify_count": 0,
                                "failed_count": 0,
                                "topic_count": 3,
                            },
                            "publish_runtime": {
                                "summary": {"verified_count": 20},
                                "batches": [
                                    {"submitted_count": 8, "verified_count": 8, "topic_id": "topic-1"},
                                    {"submitted_count": 8, "verified_count": 8, "topic_id": "topic-2"},
                                    {"submitted_count": 4, "verified_count": 4, "topic_id": "topic-3"},
                                ],
                            },
                        },
                    ],
                },
            }
        }

        text = "\n".join(workflow_notification_service._stage_lines(workflow))

        self.assertIn("业务结论：下载 21 个，清洗成功 21 个，发布确认 21 个。未完成项：下载失败 1 个。", text)
        self.assertIn("PDF：1 批，1 个附件，1 个 topic，全部确认可见。", text)
        self.assertIn("音频：3 批，8 + 8 + 4 = 20 个附件，3 个 topic，全部确认可见。", text)
        self.assertIn("说明：有 1 个发布子任务状态为失败，但本地发布运行记录显示已有内容确认可见", text)


if __name__ == "__main__":
    unittest.main()
