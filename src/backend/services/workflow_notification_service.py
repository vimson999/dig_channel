import logging
import os
import socket
import smtplib
import ssl
import time
from datetime import datetime
from email.message import EmailMessage
from email.utils import formataddr
from zoneinfo import ZoneInfo


logger = logging.getLogger(__name__)


EVENT_LABELS = {
    "workflow_started": "工作流启动",
    "sync_completed": "同步完成",
    "sync_failed": "同步失败",
    "clean_submitted": "清洗任务已提交",
    "clean_completed": "清洗完成",
    "clean_partial": "清洗部分成功",
    "clean_failed": "清洗失败",
    "clean_skipped": "清洗已跳过",
    "publish_submitted": "发布任务已提交",
    "publish_completed": "发布完成",
    "publish_partial": "发布部分成功",
    "publish_failed": "发布失败",
    "publish_skipped": "发布已跳过",
    "workflow_completed": "工作流完成",
    "workflow_partial": "工作流部分成功",
    "workflow_failed": "工作流失败",
    "report_audio_started": "研报转语音启动",
    "report_audio_gemini_submitted": "研报 PDF Gem 解析已提交",
    "report_audio_gemini_completed": "研报 PDF Gem 解析完成",
    "report_audio_normalized": "结构化整理完成",
    "report_audio_doubao_submitted": "豆包浏览器朗读已提交",
    "report_audio_doubao_completed": "豆包浏览器朗读完成",
    "report_audio_completed": "研报转语音完成",
    "report_audio_failed": "研报转语音失败",
    "mp3_video_started": "MP3 视频编排启动",
    "mp3_video_gemini_started": "MP3 Gemini 解析已开始",
    "mp3_video_gemini_completed": "MP3 Gemini 解析完成",
    "mp3_video_plan_completed": "MP3 视频规划完成",
    "mp3_video_visuals_completed": "MP3 封皮背景生成完成",
    "mp3_video_landscape_submitted": "MP3 横屏视频已提交",
    "mp3_video_landscape_completed": "MP3 横屏视频完成",
    "mp3_video_vertical_submitted": "MP3 竖屏视频已提交",
    "mp3_video_vertical_completed": "MP3 竖屏视频完成",
    "mp3_video_completed": "MP3 视频编排完成",
    "mp3_video_failed": "MP3 视频编排失败",
    "test": "测试通知",
}

FINAL_EVENTS = {
    "workflow_completed",
    "workflow_partial",
    "workflow_failed",
    "report_audio_completed",
    "report_audio_failed",
    "mp3_video_completed",
    "mp3_video_failed",
}
FAILURE_EVENTS = {"workflow_failed", "report_audio_failed", "mp3_video_failed"}
SUPPORTED_EMAIL_PROVIDERS = {"email", "smtp", "qq_email", "qq-mail"}
TRUTHY = {"1", "true", "yes", "on"}
FALSY = {"0", "false", "no", "off"}


class WorkflowNotificationService:
    """Dispatch workflow milestone notifications without blocking workflow stages."""

    def enqueue_workflow_event(self, event_type, workflow_payload):
        if not self.should_send_event(event_type):
            return {"queued": False, "reason": "disabled"}

        settings = self.email_settings()
        missing = self.missing_email_settings(settings)
        if missing:
            logger.warning("[WORKFLOW_NOTIFY] email config missing: %s", ", ".join(missing))
            return {"queued": False, "reason": "missing_config", "missing": missing}

        try:
            from tasks.workflow_notify import send_workflow_notification

            payload = {
                "provider": self.provider(),
                "event_type": event_type,
                "workflow": workflow_payload or {},
                "queued_at": self._now_iso(),
            }
            result = send_workflow_notification.apply_async(
                args=[payload],
                queue="zsxq_workflow",
                retry=False,
            )
            return {"queued": True, "celery_task_id": result.id}
        except Exception as exc:
            logger.exception("[WORKFLOW_NOTIFY] queue failed | event=%s", event_type)
            return {"queued": False, "reason": "queue_failed", "error": str(exc)}

    def send(self, payload):
        payload = payload if isinstance(payload, dict) else {}
        event_type = payload.get("event_type") or "workflow"
        if not self.should_send_event(event_type):
            logger.info("[WORKFLOW_NOTIFY] skipped by level/provider | event=%s", event_type)
            return {"sent": False, "reason": "disabled"}

        provider = self.provider()
        if provider not in SUPPORTED_EMAIL_PROVIDERS:
            return {"sent": False, "reason": f"unsupported_provider:{provider or 'none'}"}

        settings = self.email_settings()
        missing = self.missing_email_settings(settings)
        if missing:
            return {"sent": False, "reason": "missing_config", "missing": missing}

        subject, body = self.build_email_content(payload)
        self._send_email_with_retry(settings, subject, body)
        logger.info("[WORKFLOW_NOTIFY] email sent | event=%s to=%s", event_type, settings["to"])
        workflow = payload.get("workflow") if isinstance(payload.get("workflow"), dict) else {}
        if workflow.get("stage_results"):
            logger.info(
                "[WORKFLOW_NOTIFY] workflow audit | event=%s workflow=%s\n%s",
                event_type,
                workflow.get("id") or "-",
                "\n".join(self._stage_lines(workflow)),
            )
        return {"sent": True, "event_type": event_type, "to": settings["to"]}

    def send_test_email(self):
        workflow = {
            "id": "test",
            "batch_id": "manual-test",
            "group_name": "知识星球工作流",
            "trigger": "manual_test",
            "status": "running",
            "current_stage": "notify",
            "round": None,
            "max_rounds": None,
            "stage_results": {
                "workflow": {
                    "status": "test",
                    "message": "这是一封 QQ 邮箱 SMTP 测试通知。",
                }
            },
            "created_at": self._now_iso(),
        }
        return self.send({"provider": self.provider(), "event_type": "test", "workflow": workflow})

    def send_email_message(self, subject, body):
        if self.provider() not in SUPPORTED_EMAIL_PROVIDERS:
            return {"sent": False, "reason": "disabled"}
        settings = self.email_settings()
        missing = self.missing_email_settings(settings)
        if missing:
            return {"sent": False, "reason": "missing_config", "missing": missing}
        self._send_email_with_retry(settings, str(subject or "系统通知"), str(body or ""))
        return {"sent": True, "to": settings["to"]}

    def should_send_event(self, event_type):
        provider = self.provider()
        if provider not in SUPPORTED_EMAIL_PROVIDERS:
            return False

        level = self.level()
        if level in {"", "none", "off", "disabled"}:
            return False
        if level == "failure_only":
            return event_type in FAILURE_EVENTS
        if level == "summary":
            return event_type in FINAL_EVENTS
        return True

    def provider(self):
        return (os.getenv("WORKFLOW_NOTIFY_PROVIDER") or "").strip().lower()

    def level(self):
        return (os.getenv("WORKFLOW_NOTIFY_LEVEL") or "milestone").strip().lower()

    def email_settings(self):
        port = self._int_env(
            ["WORKFLOW_EMAIL_SMTP_PORT", "SMTP_PORT", "EMAIL_SMTP_PORT"],
            default=465,
        )
        return {
            "host": self._first_env(["WORKFLOW_EMAIL_SMTP_HOST", "SMTP_HOST", "EMAIL_SMTP_SERVER"], "smtp.qq.com"),
            "port": port,
            "user": self._first_env(["WORKFLOW_EMAIL_USER", "SMTP_USER", "EMAIL_USERNAME"], ""),
            "password": self._first_env(["WORKFLOW_EMAIL_PASSWORD", "SMTP_PASSWORD", "EMAIL_PASSWORD"], ""),
            "from_addr": self._first_env(["WORKFLOW_EMAIL_FROM", "SMTP_FROM", "EMAIL_FROM"], ""),
            "from_name": self._first_env(["WORKFLOW_EMAIL_FROM_NAME", "SMTP_FROM_NAME", "EMAIL_FROM_NAME"], "内容流水线"),
            "to": self._first_env(["WORKFLOW_NOTIFY_TO", "WORKFLOW_EMAIL_TO", "EMAIL_TO"], ""),
            "timeout": self._int_env(["WORKFLOW_EMAIL_TIMEOUT_SECONDS", "SMTP_TIMEOUT_SECONDS"], default=12),
            "retry_attempts": self._int_env(["WORKFLOW_EMAIL_RETRY_ATTEMPTS", "SMTP_RETRY_ATTEMPTS"], default=3),
            "retry_delay_seconds": self._float_env(["WORKFLOW_EMAIL_RETRY_DELAY_SECONDS", "SMTP_RETRY_DELAY_SECONDS"], default=2.0),
            "fallback_starttls": self._bool_env(["WORKFLOW_EMAIL_FALLBACK_STARTTLS", "SMTP_FALLBACK_STARTTLS"], default=True),
            "use_ssl": self._bool_env(["WORKFLOW_EMAIL_USE_SSL", "SMTP_USE_SSL"], default=(port == 465)),
            "use_tls": self._bool_env(["WORKFLOW_EMAIL_USE_TLS", "SMTP_USE_TLS"], default=(port == 587)),
        }

    def missing_email_settings(self, settings):
        missing = []
        for key in ("host", "port", "user", "password", "to"):
            if not settings.get(key):
                missing.append(key)
        return missing

    def build_email_content(self, payload):
        event_type = payload.get("event_type") or "workflow"
        workflow = payload.get("workflow") if isinstance(payload.get("workflow"), dict) else {}
        event_label = EVENT_LABELS.get(event_type, event_type)
        subject_prefix = workflow.get("subject_prefix") or os.getenv("WORKFLOW_NOTIFY_SUBJECT_PREFIX", "[知识星球工作流]")
        group_name = workflow.get("group_name") or "未知星球"
        group_label = workflow.get("group_label") or ("星球" if workflow.get("group_id") else "工作流")
        status = workflow.get("status") or "-"
        round_text = self._round_text(workflow)
        subject = f"{subject_prefix} {event_label} · {group_name}{round_text}"

        lines = [
            f"事件：{event_label}",
            f"{group_label}：{group_name}",
            f"状态：{self._status_label(status)} / 当前阶段：{workflow.get('current_stage') or '-'}",
            f"批次：{workflow.get('batch_id') or '-'}",
            f"工作流 ID：{workflow.get('id') or '-'}",
            f"触发方式：{workflow.get('trigger') or '-'}",
            f"轮次：{self._round_text(workflow, plain=True) or '-'}",
        ]
        if workflow.get("event_message"):
            lines.append(f"说明：{workflow.get('event_message')}")
        if workflow.get("error_message"):
            lines.append(f"错误：{workflow.get('error_message')}")

        lines.extend(self._stage_lines(workflow))
        lines.append("")
        lines.append(f"发送时间：{self._local_time_text(datetime.utcnow().isoformat() + 'Z')}")
        return subject, "\n".join(lines)

    def _send_email_with_retry(self, settings, subject, body):
        attempts = max(1, int(settings.get("retry_attempts") or 1))
        delay = max(0.0, float(settings.get("retry_delay_seconds") or 0))
        errors = []
        for attempt in range(1, attempts + 1):
            try:
                self._send_email(settings, subject, body)
                return
            except Exception as exc:
                errors.append(exc)
                if not self._is_transient_email_error(exc) or attempt >= attempts:
                    break
                logger.warning(
                    "[WORKFLOW_NOTIFY] email transient failure, retrying | attempt=%s/%s error=%s",
                    attempt,
                    attempts,
                    exc,
                )
                time.sleep(delay)

        if settings.get("fallback_starttls") and int(settings.get("port") or 0) == 465:
            fallback = dict(settings)
            fallback["port"] = 587
            fallback["use_ssl"] = False
            fallback["use_tls"] = True
            try:
                logger.warning("[WORKFLOW_NOTIFY] SMTP SSL failed, trying STARTTLS fallback on port 587")
                self._send_email(fallback, subject, body)
                return
            except Exception as exc:
                errors.append(exc)

        raise errors[-1]

    def _send_email(self, settings, subject, body):
        from_addr = settings["from_addr"] or settings["user"]
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = formataddr((settings["from_name"], from_addr))
        message["To"] = settings["to"]
        message.set_content(body)

        if settings["use_ssl"]:
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(settings["host"], settings["port"], timeout=settings["timeout"], context=context) as smtp:
                smtp.login(settings["user"], settings["password"])
                smtp.send_message(message)
            return

        with smtplib.SMTP(settings["host"], settings["port"], timeout=settings["timeout"]) as smtp:
            if settings["use_tls"]:
                smtp.starttls(context=ssl.create_default_context())
            smtp.login(settings["user"], settings["password"])
            smtp.send_message(message)

    def _is_transient_email_error(self, exc):
        return isinstance(
            exc,
            (
                TimeoutError,
                ConnectionError,
                ConnectionResetError,
                socket.timeout,
                OSError,
                smtplib.SMTPServerDisconnected,
                smtplib.SMTPConnectError,
                ssl.SSLError,
            ),
        )

    def _stage_lines(self, workflow):
        stage_results = workflow.get("stage_results") if isinstance(workflow.get("stage_results"), dict) else {}
        lines = ["", "阶段概览："]
        totals = self._workflow_totals(stage_results)
        if totals:
            lines.append(
                "总量："
                f"下载 {totals['downloaded']} 个，下载失败 {totals['download_failed']} 个"
                f" | 清洗成功 {totals['clean_success']} 个，清洗失败 {totals['clean_failed']} 个"
                f" | 发布已提交 {totals['publish_submitted']} 个，已确认可见 {totals['publish_verified']} 个"
                f"，待审核/待确认 {totals['publish_pending']} 个，发布失败 {totals['publish_failed']} 个"
            )
            business_summary = self._business_summary_text(totals)
            if business_summary:
                lines.append(business_summary)

        sync = stage_results.get("sync") if isinstance(stage_results.get("sync"), dict) else {}
        if sync:
            summary = sync.get("summary") if isinstance(sync.get("summary"), dict) else {}
            lines.append(
                "同步："
                f"{self._status_label(sync.get('status'))}"
                f" | 新增 {self._num(summary.get('topics_new'))}"
                f" | 更新 {self._num(summary.get('topics_updated'))}"
                f" | 下载 {self._num(summary.get('attachments_downloaded'))}"
                f" | 跳过 {self._num(summary.get('attachments_skipped'))}"
                f" | 失败 {self._num(summary.get('attachments_failed'))}"
            )
            lines.extend(self._sync_audit_lines(summary))
            if sync.get("error"):
                lines.append(f"同步错误：{sync.get('error')}")

        clean = stage_results.get("clean") if isinstance(stage_results.get("clean"), dict) else {}
        if clean:
            clean_counts = self._clean_counts(clean.get("tasks"))
            lines.append(
                f"清洗：{self._status_label(clean.get('status'))}"
                f"{self._tasks_text(clean.get('tasks'), stage_status=clean.get('status'))}"
                f"{self._clean_counts_text(clean_counts)}"
            )
            if clean.get("error"):
                lines.append(f"清洗错误：{clean.get('error')}")

        publish = stage_results.get("publish") if isinstance(stage_results.get("publish"), dict) else {}
        if publish:
            publish_counts = self._publish_counts(publish.get("tasks"))
            lines.append(
                f"发布：{self._status_label(publish.get('status'))}"
                f"{self._tasks_text(publish.get('tasks'), stage_status=publish.get('status'))}"
                f"{self._publish_counts_text(publish_counts)}"
            )
            publish_detail_lines = self._publish_detail_lines(publish.get("tasks"))
            if publish_detail_lines:
                lines.append("发布明细：")
                lines.extend(publish_detail_lines)
            recovered_note = self._publish_recovered_note(publish.get("tasks"))
            if recovered_note:
                lines.append(recovered_note)
            if publish.get("error"):
                lines.append(f"发布错误：{publish.get('error')}")

        stages = stage_results.get("stages") if isinstance(stage_results.get("stages"), list) else []
        if stages:
            lines.append("")
            lines.append("流程节点：")
            for stage in stages[:20]:
                if not isinstance(stage, dict):
                    continue
                name = stage.get("name") or stage.get("id") or "节点"
                message = stage.get("message")
                suffix = f" | {message}" if message else ""
                lines.append(f"{name}：{self._status_label(stage.get('status'))}{suffix}")

        outputs = stage_results.get("outputs") if isinstance(stage_results.get("outputs"), dict) else {}
        if outputs:
            lines.append("")
            lines.append("产物：")
            for key, value in outputs.items():
                if value is None or value == "":
                    continue
                lines.append(f"{key}：{value}")

        return lines

    def _workflow_totals(self, stage_results):
        sync = stage_results.get("sync") if isinstance(stage_results.get("sync"), dict) else {}
        sync_summary = sync.get("summary") if isinstance(sync.get("summary"), dict) else {}
        clean = stage_results.get("clean") if isinstance(stage_results.get("clean"), dict) else {}
        publish = stage_results.get("publish") if isinstance(stage_results.get("publish"), dict) else {}
        clean_counts = self._clean_counts(clean.get("tasks"))
        publish_counts = self._publish_counts(publish.get("tasks"))
        totals = {
            "downloaded": self._safe_int(sync_summary.get("attachments_downloaded")),
            "download_failed": self._safe_int(sync_summary.get("attachments_failed")),
            "download_skipped": self._safe_int(sync_summary.get("attachments_skipped")),
            "download_below_threshold": self._safe_int(sync_summary.get("attachments_below_threshold")),
            "download_missing_count": self._safe_int(sync_summary.get("attachments_missing_download_count")),
            "download_already_present": self._safe_int(sync_summary.get("attachments_already_downloaded_skipped")),
            "attachments_non_target_skipped": self._safe_int(sync_summary.get("attachments_non_target_skipped")),
            "attachments_limited_skipped": self._safe_int(sync_summary.get("attachments_limited_skipped")),
            "clean_success": clean_counts["success"],
            "clean_failed": clean_counts["failed"],
            "publish_submitted": publish_counts["submitted"],
            "publish_verified": publish_counts["verified"],
            "publish_pending": publish_counts["pending"],
            "publish_failed": publish_counts["failed"],
        }
        return totals if any(totals.values()) else None

    def _sync_audit_lines(self, summary):
        if not isinstance(summary, dict):
            return []

        planned_topics = self._safe_int(summary.get("planned_topics_total"))
        topics_target = self._safe_int(summary.get("target_topics_per_tab"))
        tabs = summary.get("tabs") if isinstance(summary.get("tabs"), list) else []
        if not planned_topics and topics_target and tabs:
            planned_topics = topics_target * len(tabs)

        planned_pages = self._safe_int(summary.get("planned_pages_total"))
        max_pages = self._safe_int(summary.get("max_pages"))
        if not planned_pages and max_pages and tabs:
            planned_pages = max_pages * len(tabs)

        actual_pages = self._safe_int(summary.get("pages_fetched"))
        if not actual_pages and tabs:
            actual_pages = sum(self._safe_int(tab.get("pages")) for tab in tabs if isinstance(tab, dict))

        topics_fetched = self._safe_int(summary.get("topics_fetched"))
        attachments_seen = self._safe_int(summary.get("attachments_seen"))
        downloaded = self._safe_int(summary.get("attachments_downloaded"))
        failed = self._safe_int(summary.get("attachments_failed"))
        skipped = self._safe_int(summary.get("attachments_skipped"))

        lines = []
        if any([planned_topics, planned_pages, actual_pages, topics_fetched, attachments_seen]):
            plan_bits = []
            if topics_target:
                plan_bits.append(f"每个目标 Tab {topics_target} 个 topic")
            if planned_topics:
                plan_bits.append(f"合计计划 {planned_topics} 个 topic")
            if max_pages:
                plan_bits.append(f"每个目标最多 {max_pages} 页")
            if planned_pages:
                plan_bits.append(f"合计最多 {planned_pages} 页")
            plan_text = "，".join(plan_bits) if plan_bits else "未记录计划值"
            lines.append(
                f"同步对账：计划 {plan_text}；实际拉取 {actual_pages} 页，"
                f"拿到 {topics_fetched} 个 topic，发现 {attachments_seen} 个附件/item。"
            )

        if any([downloaded, failed, skipped]):
            skip_text = self._download_skip_breakdown_text(summary)
            limit_text = "；达到本轮下载上限" if summary.get("download_limit_reached") else ""
            lines.append(
                f"下载对账：真正下载 {downloaded} 个，失败 {failed} 个，"
                f"跳过 {skipped} 个{skip_text}{limit_text}。"
            )

        tab_lines = self._sync_tab_audit_lines(tabs)
        if tab_lines:
            lines.append("同步 Tab 明细：")
            lines.extend(tab_lines)
        return lines

    def _sync_tab_audit_lines(self, tabs):
        if not isinstance(tabs, list):
            return []
        lines = []
        for tab in tabs[:8]:
            if not isinstance(tab, dict):
                continue
            label = tab.get("tab_label") or tab.get("tab_id") or "Tab"
            target = self._safe_int(tab.get("target_topics"))
            planned_pages = self._safe_int(tab.get("planned_pages"))
            pages = self._safe_int(tab.get("pages"))
            topics = self._safe_int(tab.get("topics_fetched"))
            seen = self._safe_int(tab.get("attachments_seen"))
            downloaded = self._safe_int(tab.get("attachments_downloaded"))
            skipped = self._safe_int(tab.get("attachments_skipped"))
            failed = self._safe_int(tab.get("attachments_failed"))
            if not any([target, planned_pages, pages, topics, seen, downloaded, skipped, failed]):
                continue
            stop_reasons = []
            if tab.get("stopped_by_existing"):
                stop_reasons.append("遇到已有 topic 后停止")
            if tab.get("stopped_by_download_limit") or tab.get("download_limit_reached"):
                stop_reasons.append("达到下载上限")
            stop_text = f"；{'，'.join(stop_reasons)}" if stop_reasons else ""
            lines.append(
                f"{label}：计划 {target or '-'} 个 topic / {planned_pages or '-'} 页，"
                f"实际 {topics} 个 topic / {pages} 页；附件 {seen}，下载 {downloaded}，"
                f"跳过 {skipped}{self._download_skip_breakdown_text(tab)}，失败 {failed}{stop_text}。"
            )
        if len(tabs) > 8:
            lines.append(f"其余 {len(tabs) - 8} 个 Tab 已省略。")
        return lines

    def _clean_counts(self, tasks):
        counts = {"files": 0, "success": 0, "failed": 0}
        if not isinstance(tasks, list):
            return counts
        for item in tasks:
            if not isinstance(item, dict):
                continue
            counts["files"] += self._safe_int(item.get("files_count"))
            counts["success"] += self._safe_int(item.get("success_count"))
            counts["failed"] += self._safe_int(item.get("failed_count"))
        return counts

    def _publish_counts(self, tasks):
        counts = {
            "selected": 0,
            "uploaded": 0,
            "submitted": 0,
            "verified": 0,
            "pending": 0,
            "failed": 0,
            "topics": 0,
            "verified_topics": 0,
            "pending_topics": 0,
        }
        if not isinstance(tasks, list):
            return counts
        for item in tasks:
            if not isinstance(item, dict):
                continue
            summary = item.get("summary") if isinstance(item.get("summary"), dict) else {}
            selected = self._first_number(item.get("selected_count"), summary.get("selected_count"))
            uploaded = self._first_number(item.get("uploaded_count"), summary.get("uploaded_count"))
            submitted = self._first_number(item.get("submitted_count"), summary.get("submitted_count"), uploaded)
            verified = self._first_number(item.get("verified_count"), summary.get("verified_count"))
            pending = self._first_number(
                item.get("pending_verify_count"),
                summary.get("pending_verify_count"),
                max(0, submitted - verified),
            )
            counts["selected"] += selected
            counts["uploaded"] += uploaded
            counts["submitted"] += submitted
            counts["verified"] += verified
            counts["pending"] += pending
            counts["failed"] += self._first_number(item.get("failed_count"), summary.get("failed_count"))
            counts["topics"] += self._first_number(item.get("topic_count"), summary.get("topic_count"))
            counts["verified_topics"] += self._first_number(item.get("verified_topic_count"), summary.get("verified_topic_count"))
            counts["pending_topics"] += self._first_number(item.get("pending_verify_topic_count"), summary.get("pending_verify_topic_count"))
        return counts

    def _clean_counts_text(self, counts):
        if not counts or not any(counts.values()):
            return ""
        return f" | 文件成功 {counts['success']}，失败 {counts['failed']}"

    def _publish_counts_text(self, counts):
        if not counts or not any(counts.values()):
            return ""
        return (
            f" | 已提交 {counts['submitted']}，已确认 {counts['verified']}，"
            f"待审核/待确认 {counts['pending']}，失败 {counts['failed']}"
        )

    def _business_summary_text(self, totals):
        if not totals:
            return ""
        text = (
            f"业务结论：下载 {totals['downloaded']} 个，清洗成功 {totals['clean_success']} 个，"
            f"发布确认 {totals['publish_verified']} 个。"
        )
        extras = []
        if totals.get("download_failed"):
            extras.append(f"下载失败 {totals['download_failed']} 个")
        if totals.get("download_skipped"):
            skip_reason = self._download_skip_breakdown_text(totals)
            extras.append(f"下载跳过 {totals['download_skipped']} 个{skip_reason}")
        if totals.get("clean_failed"):
            extras.append(f"清洗失败 {totals['clean_failed']} 个")
        if totals.get("publish_pending"):
            extras.append(f"发布待审核/待确认 {totals['publish_pending']} 个")
        if totals.get("publish_failed"):
            extras.append(f"发布失败 {totals['publish_failed']} 个")
        if extras:
            text += f"未完成项：{'，'.join(extras)}。"
        return text

    def _download_skip_breakdown_text(self, summary):
        if not isinstance(summary, dict):
            return ""
        parts = []
        already = self._safe_int(
            summary.get("attachments_already_downloaded_skipped")
            or summary.get("download_already_present")
        )
        below = self._safe_int(
            summary.get("attachments_below_threshold")
            or summary.get("download_below_threshold")
        )
        missing = self._safe_int(
            summary.get("attachments_missing_download_count")
            or summary.get("download_missing_count")
        )
        non_target = self._safe_int(summary.get("attachments_non_target_skipped"))
        limited = self._safe_int(summary.get("attachments_limited_skipped"))
        if already:
            parts.append(f"已下载/复用 {already}")
        if below:
            parts.append(f"低于阈值 {below}")
        if missing:
            parts.append(f"缺少下载次数 {missing}")
        if non_target:
            parts.append(f"非目标类型 {non_target}")
        if limited:
            parts.append(f"下载上限 {limited}")
        return f"（{', '.join(parts)}）" if parts else ""

    def _publish_detail_lines(self, tasks):
        if not isinstance(tasks, list):
            return []
        lines = []
        for item in tasks:
            if not isinstance(item, dict):
                continue
            runtime = item.get("publish_runtime") if isinstance(item.get("publish_runtime"), dict) else {}
            batches = runtime.get("batches") if isinstance(runtime.get("batches"), list) else []
            summary = item.get("summary") if isinstance(item.get("summary"), dict) else {}
            counts = [self._safe_int(batch.get("submitted_count")) for batch in batches if isinstance(batch, dict)]
            counts = [count for count in counts if count > 0]
            submitted = self._first_number(item.get("submitted_count"), summary.get("submitted_count"), sum(counts))
            if submitted <= 0:
                continue
            batch_count = len(counts) or self._safe_int(summary.get("round_count")) or 1
            count_text = self._batch_count_text(counts, submitted)
            topic_count = self._first_number(item.get("topic_count"), summary.get("topic_count"))
            verified = self._first_number(item.get("verified_count"), summary.get("verified_count"))
            pending = self._first_number(item.get("pending_verify_count"), summary.get("pending_verify_count"))
            failed = self._first_number(item.get("failed_count"), summary.get("failed_count"))
            label = self._file_type_label(item.get("file_type"))
            verify_text = self._publish_verify_text(submitted, verified, pending, failed)
            lines.append(
                f"{label}：{batch_count} 批，{count_text} 个附件，"
                f"{topic_count} 个 topic，{verify_text}。"
            )
        return lines

    def _publish_recovered_note(self, tasks):
        if not isinstance(tasks, list):
            return ""
        failed_with_runtime = 0
        for item in tasks:
            if not isinstance(item, dict):
                continue
            status = str(item.get("status") or "").lower()
            runtime = item.get("publish_runtime") if isinstance(item.get("publish_runtime"), dict) else {}
            summary = runtime.get("summary") if isinstance(runtime.get("summary"), dict) else {}
            if status == "failed" and self._safe_int(summary.get("verified_count")) > 0:
                failed_with_runtime += 1
        if not failed_with_runtime:
            return ""
        return (
            f"说明：有 {failed_with_runtime} 个发布子任务状态为失败，但本地发布运行记录显示已有内容确认可见，"
            "通常是结果回写数据库超时导致的状态误报。"
        )

    def _batch_count_text(self, counts, total):
        if not counts:
            return str(total)
        if len(counts) == 1:
            return str(counts[0])
        return f"{' + '.join(str(count) for count in counts)} = {total}"

    def _publish_verify_text(self, submitted, verified, pending, failed):
        if failed:
            return f"已确认可见 {verified}，待审核/待确认 {pending}，失败 {failed}"
        if pending:
            return f"已确认可见 {verified}，待审核/待确认 {pending}"
        if verified >= submitted:
            return "全部确认可见"
        return f"已确认可见 {verified}"

    def _file_type_label(self, value):
        labels = {
            "pdf": "PDF",
            "audio": "音频",
            "mp3": "音频",
        }
        return labels.get(str(value or "").lower(), str(value or "发布"))

    def _tasks_text(self, tasks, stage_status=None):
        if not isinstance(tasks, list) or not tasks:
            return ""

        completed = 0
        failed = 0
        skipped = 0
        running = 0
        for item in tasks:
            if not isinstance(item, dict):
                continue
            status = str(item.get("status") or "").lower()
            reason = str(item.get("reason") or "").lower()
            queued = item.get("queued")
            if status == "completed":
                completed += 1
            elif status == "failed":
                failed += 1
            elif (
                status == "skipped"
                or queued is False
                or reason.startswith("no_")
                or reason in {"disabled", "skip", "skipped"}
                or (stage_status == "skipped" and not item.get("task_id"))
            ):
                skipped += 1
            else:
                running += 1

        if skipped and not completed and not failed and not running:
            return f" | 检查 {len(tasks)} 项，跳过 {skipped}"
        return f" | 任务 {len(tasks)} 个，完成 {completed}，失败 {failed}，跳过 {skipped}，进行中 {running}"

    def _status_label(self, value):
        labels = {
            "queued": "排队中",
            "pending": "等待",
            "running": "运行中",
            "completed": "成功",
            "succeeded": "成功",
            "partial": "部分成功",
            "failed": "失败",
            "skipped": "跳过",
            "created": "已创建",
            "processing": "处理中",
            "canceled": "已取消",
        }
        return labels.get(str(value or ""), str(value or "-"))

    def _round_text(self, workflow, plain=False):
        current = workflow.get("round")
        maximum = workflow.get("max_rounds")
        if current is None and maximum is None:
            return ""
        if maximum:
            text = f"{current or 0}/{maximum} 轮"
        else:
            text = f"第 {current or 0} 轮"
        return text if plain else f" · {text}"

    def _local_time_text(self, value):
        if not value:
            return "-"
        raw = str(value)
        try:
            if raw.endswith("Z"):
                raw = raw[:-1] + "+00:00"
            dt = datetime.fromisoformat(raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=ZoneInfo("UTC"))
            return dt.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return str(value)

    def _num(self, value):
        return value if value is not None else 0

    def _safe_int(self, value):
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    def _first_number(self, *values):
        for value in values:
            if value is not None:
                return self._safe_int(value)
        return 0

    def _first_env(self, names, default=None):
        for name in names:
            value = os.getenv(name)
            if value is not None and str(value).strip() != "":
                return str(value).strip()
        return default

    def _int_env(self, names, default):
        raw = self._first_env(names, None)
        if raw is None:
            return default
        try:
            return int(raw)
        except (TypeError, ValueError):
            return default

    def _float_env(self, names, default):
        raw = self._first_env(names, None)
        if raw is None:
            return default
        try:
            return float(raw)
        except (TypeError, ValueError):
            return default

    def _bool_env(self, names, default=False):
        raw = self._first_env(names, None)
        if raw is None:
            return default
        value = str(raw).strip().lower()
        if value in TRUTHY:
            return True
        if value in FALSY:
            return False
        return default

    def _now_iso(self):
        return datetime.utcnow().isoformat() + "Z"


workflow_notification_service = WorkflowNotificationService()
