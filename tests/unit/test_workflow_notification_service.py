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

    def test_stage_lines_include_sync_audit_breakdown(self):
        workflow = {
            "stage_results": {
                "sync": {
                    "status": "completed",
                    "summary": {
                        "target_topics_per_tab": 40,
                        "planned_topics_total": 40,
                        "max_pages": 2,
                        "planned_pages_total": 2,
                        "pages_fetched": 2,
                        "topics_fetched": 39,
                        "topics_new": 0,
                        "topics_updated": 39,
                        "attachments_seen": 52,
                        "attachments_downloaded": 9,
                        "attachments_failed": 1,
                        "attachments_skipped": 42,
                        "attachments_already_downloaded_skipped": 20,
                        "attachments_below_threshold": 12,
                        "attachments_missing_download_count": 3,
                        "attachments_non_target_skipped": 7,
                        "tabs": [
                            {
                                "tab_label": "外资研报",
                                "target_topics": 40,
                                "planned_pages": 2,
                                "pages": 2,
                                "topics_fetched": 39,
                                "attachments_seen": 52,
                                "attachments_downloaded": 9,
                                "attachments_failed": 1,
                                "attachments_skipped": 42,
                                "attachments_already_downloaded_skipped": 20,
                                "attachments_below_threshold": 12,
                                "attachments_missing_download_count": 3,
                                "attachments_non_target_skipped": 7,
                            }
                        ],
                    },
                },
                "clean": {
                    "status": "completed",
                    "tasks": [{"status": "completed", "success_count": 9, "failed_count": 0}],
                },
                "publish": {
                    "status": "completed",
                    "tasks": [{"status": "completed", "summary": {"submitted_count": 9, "verified_count": 9}}],
                },
            }
        }

        text = "\n".join(workflow_notification_service._stage_lines(workflow))

        self.assertIn("同步对账：计划 每个目标 Tab 40 个 topic，合计计划 40 个 topic，每个目标最多 2 页，合计最多 2 页；实际拉取 2 页，拿到 39 个 topic，发现 52 个附件/item。", text)
        self.assertIn("下载对账：真正下载 9 个，失败 1 个，跳过 42 个（已下载/复用 20, 低于阈值 12, 缺少下载次数 3, 非目标类型 7）。", text)
        self.assertIn("外资研报：计划 40 个 topic / 2 页，实际 39 个 topic / 2 页；附件 52，下载 9，跳过 42（已下载/复用 20, 低于阈值 12, 缺少下载次数 3, 非目标类型 7），失败 1。", text)

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

    def test_mp3_video_event_label_and_stage_lines(self):
        payload = {
            "event_type": "mp3_video_completed",
            "workflow": {
                "id": "job-1",
                "batch_id": "job-1",
                "group_name": "MP3 视频编排",
                "group_label": "编排",
                "subject_prefix": "[MP3 视频编排]",
                "status": "completed",
                "current_stage": "done",
                "stage_results": {
                    "stages": [
                        {"name": "MP3 输入", "status": "completed", "message": "demo.mp3 / 42.0 MB"},
                        {"name": "Gemini Gem 解析/信息图", "status": "completed", "message": "第一轮返回已获取"},
                    ],
                    "outputs": {
                        "标题": "算力调整假摔还是上车",
                        "横屏视频": "/tmp/landscape.mp4",
                    },
                },
            },
        }

        subject, body = workflow_notification_service.build_email_content(payload)

        self.assertIn("MP3 视频编排完成", subject)
        self.assertIn("流程节点：", body)
        self.assertIn("MP3 输入：成功 | demo.mp3 / 42.0 MB", body)
        self.assertIn("标题：算力调整假摔还是上车", body)


if __name__ == "__main__":
    unittest.main()
