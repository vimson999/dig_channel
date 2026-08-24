import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.workflow_notification_service import workflow_notification_service  # noqa: E402
from services import workflow_notification_service as notification_module  # noqa: E402


class WorkflowNotificationServiceTest(unittest.TestCase):
    def test_public_human_intervention_notification_masks_recipient(self):
        summary = notification_module.public_browser_human_intervention_notification({
            "sent": True,
            "sent_at": "2026-08-12T08:34:14Z",
            "event_type": "doubao_captcha_blocked",
            "to": "owner@example.test",
        })

        self.assertEqual(summary["recipient_hint"], "o***@example.test")
        self.assertNotIn("to", summary)

    def test_public_human_intervention_notification_masks_every_recipient(self):
        summary = notification_module.public_browser_human_intervention_notification({
            "sent": True,
            "to": "owner@example.test, backup@example.test",
        })

        self.assertEqual(
            summary["recipient_hint"],
            "o***@example.test, b***@example.test",
        )
        self.assertNotIn("owner@", summary["recipient_hint"])
        self.assertNotIn("backup@", summary["recipient_hint"])

    def test_browser_verification_alert_uses_caller_recovery_steps(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            "os.environ",
            {
                "WORKFLOW_NOTIFY_PROVIDER": "email",
                "WORKFLOW_EMAIL_USER": "sender@example.test",
                "WORKFLOW_EMAIL_PASSWORD": "secret",
                "WORKFLOW_NOTIFY_TO": "owner@example.test",
                "BROWSER_HUMAN_INTERVENTION_STATE_DIR": directory,
            },
            clear=False,
        ), patch.object(workflow_notification_service, "_send_email_with_retry") as send:
            result = workflow_notification_service.send_browser_human_intervention(
                provider="豆包",
                reason="verification",
                message="需要人机验证",
                job_id="child-1",
                details={
                    "工作流详情": "http://localhost:5001/report-audio-workflow-detail.html?job_id=parent-1",
                },
                resolution_steps=[
                    "使用指定浏览器 Profile 打开豆包页面",
                    "完成人机验证并确认可以发起朗读",
                    "返回工作流详情页，点击“从失败处续跑”",
                ],
            )

        self.assertTrue(result["sent"])
        body = send.call_args.args[2]
        self.assertIn("工作流详情：http://localhost:5001/report-audio-workflow-detail.html?job_id=parent-1", body)
        self.assertIn("1. 使用指定浏览器 Profile 打开豆包页面", body)
        self.assertIn("3. 返回工作流详情页，点击“从失败处续跑”", body)

    def test_failure_only_level_includes_direct_sync_failure(self):
        with patch.dict(
            "os.environ",
            {
                "WORKFLOW_NOTIFY_PROVIDER": "email",
                "WORKFLOW_NOTIFY_LEVEL": "failure_only",
            },
            clear=False,
        ):
            self.assertTrue(
                workflow_notification_service.should_send_event("sync_failed")
            )
            self.assertFalse(
                workflow_notification_service.should_send_event("sync_completed")
            )

    def test_browser_human_intervention_email_is_deduplicated_during_cooldown(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            "os.environ",
            {
                "WORKFLOW_NOTIFY_PROVIDER": "email",
                "WORKFLOW_EMAIL_USER": "sender@example.test",
                "WORKFLOW_EMAIL_PASSWORD": "secret",
                "WORKFLOW_NOTIFY_TO": "owner@example.test",
                "BROWSER_HUMAN_INTERVENTION_STATE_DIR": directory,
                "BROWSER_HUMAN_INTERVENTION_EMAIL_COOLDOWN_SECONDS": "1800",
            },
            clear=False,
        ), patch.object(workflow_notification_service, "_send_email_with_retry") as send:
            first = workflow_notification_service.send_browser_human_intervention(
                provider="Gemini",
                reason="login",
                message="登录态失效",
                job_id="job-1",
                profile="profile-a",
            )
            second = workflow_notification_service.send_browser_human_intervention(
                provider="Gemini",
                reason="login",
                message="登录态失效",
                job_id="job-2",
                profile="profile-a",
            )

        self.assertTrue(first["sent"])
        self.assertEqual("cooldown", second["reason"])
        send.assert_called_once()

    def test_browser_login_alert_contains_actionable_profile_recovery_runbook(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            "os.environ",
            {
                "WORKFLOW_NOTIFY_PROVIDER": "email",
                "WORKFLOW_EMAIL_USER": "sender@example.test",
                "WORKFLOW_EMAIL_PASSWORD": "secret",
                "WORKFLOW_NOTIFY_TO": "owner@example.test",
                "BROWSER_HUMAN_INTERVENTION_STATE_DIR": directory,
            },
            clear=False,
        ), patch.object(workflow_notification_service, "_send_email_with_retry") as send:
            result = workflow_notification_service.send_browser_human_intervention(
                provider="Gemini",
                reason="login",
                message="登录态失效",
                job_id="storyboard-job",
                page_url="https://gemini.google.com/app",
                screenshot_path="/tmp/gemini-login.png",
                profile="/runtime/browser_profiles/worker_2",
                worker_id="worker_2",
                details={
                    "工作流详情": "http://localhost:5001/report-stick-figure-video-workflow-detail.html?workflow_job_id=workflow-job",
                },
            )

        self.assertTrue(result["sent"])
        body = send.call_args.args[2]
        self.assertIn("关闭正在占用上述浏览器 Profile 的自动化 Chrome 窗口", body)
        self.assertIn("使用上述浏览器 Profile 打开 Gemini 页面", body)
        self.assertIn("完成 Google/Gemini 登录以及验证码或安全验证", body)
        self.assertIn("确认 Gemini 输入框可见并能进入目标 Gem", body)
        self.assertIn("点击“登录完成，恢复任务”", body)
        self.assertIn("workflow_job_id=workflow-job", body)

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
        self.assertIn("发布已提交 23 个，发布后回查确认 7 个，待审核/待复核 16 个，发布失败 0 个", text)
        self.assertIn("发布：成功 | 任务 1 个，完成 1，失败 0，跳过 0，进行中 0 | 已提交 23，回查确认 7，待审核/待复核 16，失败 0", text)

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

        self.assertIn("业务结论：下载 21 个，清洗成功 21 个，发布后回查确认 21 个。未完成项：下载失败 1 个。", text)
        self.assertIn("PDF：1 批，1 个附件，1 个 topic，全部发布后回查确认。", text)
        self.assertIn("音频：3 批，8 + 8 + 4 = 20 个附件，3 个 topic，全部发布后回查确认。", text)
        self.assertIn("说明：有 1 个发布子任务状态为失败，但本地发布运行记录显示已有内容发布后回查确认", text)

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
