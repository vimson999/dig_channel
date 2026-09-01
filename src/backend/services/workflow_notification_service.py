import logging
import os
import socket
import smtplib
import ssl
import time
import hashlib
import threading
from datetime import datetime
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path
from zoneinfo import ZoneInfo

from services.runtime_guard import (
    apply_async_after_db_release,
    end_db_transaction_before_external_io,
)

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX fallback
    fcntl = None


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
    "sync_completed",
    "sync_failed",
    "workflow_completed",
    "workflow_partial",
    "workflow_failed",
    "report_audio_completed",
    "report_audio_failed",
    "mp3_video_completed",
    "mp3_video_failed",
}
FAILURE_EVENTS = {"sync_failed", "workflow_failed", "report_audio_failed", "mp3_video_failed"}
SUPPORTED_EMAIL_PROVIDERS = {"email", "smtp", "qq_email", "qq-mail"}
TRUTHY = {"1", "true", "yes", "on"}
FALSY = {"0", "false", "no", "off"}
_BROWSER_ALERT_LOCK = threading.RLock()
_PUBLIC_BROWSER_NOTIFICATION_FIELDS = (
    "sent",
    "reason",
    "sent_at",
    "retry_after_seconds",
    "event_type",
    "error",
    "missing",
)


def _mask_email_address(value):
    text = str(value or "").strip()
    if not text:
        return ""
    masked = []
    for address in text.replace(";", ",").split(","):
        address = address.strip()
        if not address:
            continue
        local, separator, domain = address.partition("@")
        masked.append(
            f"{local[:1]}***@{domain}"
            if separator
            else f"{address[:1]}***"
        )
    return ", ".join(masked)


def public_browser_human_intervention_notification(notification):
    """Return the browser-alert fields safe for authenticated workflow UIs."""
    if not isinstance(notification, dict):
        return {}
    summary = {
        key: notification.get(key)
        for key in _PUBLIC_BROWSER_NOTIFICATION_FIELDS
        if key in notification and notification.get(key) is not None
    }
    recipient = notification.get("to")
    if recipient:
        summary["recipient_hint"] = _mask_email_address(recipient)
    elif notification.get("recipient_hint"):
        summary["recipient_hint"] = str(notification.get("recipient_hint"))
    return summary


class WorkflowNotificationService:
    """Dispatch workflow milestone notifications without blocking workflow stages."""

    def enqueue_workflow_event(self, event_type, workflow_payload):
        if not self.should_send_event(event_type):
            return {"queued": False, "reason": "disabled"}

        settings = self.email_settings()
        missing = self.missing_email_settings(settings)
        if missing:
            logger.warning(
                "[WORKFLOW_NOTIFY] email config missing; event will be audited "
                "in durable outbox: %s",
                ", ".join(missing),
            )

        try:
            # The delivery record must never outlive a business transaction that
            # can still roll back.  End the caller transaction before the
            # independent outbox session commits anything.  The boundary helper
            # refuses pending ORM writes and surfaces commit failures, while also
            # covering Core writes that are not visible through session.dirty.
            end_db_transaction_before_external_io(
                "workflow notification outbox enqueue"
            )
            from services.workflow_notification_outbox import (
                enqueue_delivery,
                mark_dispatch_result,
            )
            from tasks.workflow_notify import send_workflow_notification

            provider = self.provider()
            payload = {
                "provider": provider,
                "event_type": event_type,
                "workflow": workflow_payload or {},
                "queued_at": self._now_iso(),
            }
            delivery = enqueue_delivery(
                provider=provider,
                event_type=event_type,
                workflow=workflow_payload or {},
                payload=payload,
            )
            if delivery.get("payload_conflict"):
                logger.warning(
                    "[WORKFLOW_NOTIFY] dedupe payload conflict; preserving first payload | "
                    "delivery=%s event=%s workflow=%s",
                    delivery.get("id"),
                    event_type,
                    delivery.get("workflow_id"),
                )
            if delivery.get("status") == "sent":
                return {
                    "queued": True,
                    "deduplicated": True,
                    "sent": True,
                    "delivery_id": delivery.get("id"),
                    "celery_task_id": delivery.get("celery_task_id"),
                }
            try:
                result = apply_async_after_db_release(
                    send_workflow_notification,
                    args=[provider],
                    queue="workflow_notify",
                    retry=False,
                )
                mark_dispatch_result(
                    delivery["id"],
                    celery_task_id=result.id,
                )
                return {
                    "queued": True,
                    "durable": True,
                    "deduplicated": not delivery.get("created"),
                    "delivery_id": delivery.get("id"),
                    "celery_task_id": result.id,
                }
            except Exception as dispatch_exc:
                mark_dispatch_result(delivery["id"], error=dispatch_exc)
                logger.exception(
                    "[WORKFLOW_NOTIFY] broker dispatch failed; delivery remains pending | "
                    "delivery=%s event=%s",
                    delivery.get("id"),
                    event_type,
                )
                return {
                    "queued": True,
                    "durable": True,
                    "dispatch_scheduled": False,
                    "delivery_id": delivery.get("id"),
                    "reason": "broker_dispatch_pending",
                    "error": str(dispatch_exc),
                }
        except Exception as exc:
            logger.exception("[WORKFLOW_NOTIFY] durable queue failed | event=%s", event_type)
            return {"queued": False, "reason": "outbox_failed", "error": str(exc)}

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
        # The durable provider breaker owns retry/backoff for workflow events.
        # Clamp the legacy in-process retry knob so one leased delivery can
        # never multiply connections before opening the shared circuit.
        settings = dict(settings)
        settings["retry_attempts"] = 1
        self._send_email_with_retry(
            settings,
            subject,
            body,
            event_id=payload.get("delivery_id") or payload.get("event_id"),
        )
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

    def send_browser_human_intervention(
        self,
        *,
        provider,
        reason,
        message,
        job_id="",
        page_url="",
        screenshot_path="",
        profile="",
        worker_id="",
        details=None,
        resolution_steps=None,
    ):
        """Send a deduplicated alert when browser automation needs a human."""
        settings = self.email_settings()
        missing = self.missing_email_settings(settings)
        if self.provider() not in SUPPORTED_EMAIL_PROVIDERS:
            return {"sent": False, "reason": "disabled"}
        if missing:
            return {"sent": False, "reason": "missing_config", "missing": missing}

        provider = str(provider or "浏览器").strip()
        reason = str(reason or "verification").strip().lower()
        reason_label = "登录状态失效" if reason == "login" else "需要安全验证"
        fingerprint = "|".join((provider.lower(), reason, str(profile or ""), str(worker_id or "")))
        stamp_dir = Path(os.getenv(
            "BROWSER_HUMAN_INTERVENTION_STATE_DIR",
            str(Path(__file__).resolve().parents[3] / "runtime" / "browser_alerts"),
        )).expanduser()
        stamp_path = stamp_dir / f"{hashlib.sha256(fingerprint.encode('utf-8')).hexdigest()}.sent"
        lock_path = stamp_path.with_suffix(".lock")
        cooldown = max(0, self._int_env(
            ["BROWSER_HUMAN_INTERVENTION_EMAIL_COOLDOWN_SECONDS"],
            default=1800,
        ))

        try:
            stamp_dir.mkdir(parents=True, exist_ok=True)
            with _BROWSER_ALERT_LOCK, lock_path.open("a+", encoding="utf-8") as lock_file:
                if fcntl is not None:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                if stamp_path.exists() and cooldown > 0:
                    elapsed = max(0.0, time.time() - stamp_path.stat().st_mtime)
                    if elapsed < cooldown:
                        return {
                            "sent": False,
                            "reason": "cooldown",
                            "retry_after_seconds": int(cooldown - elapsed),
                        }

                subject = f"[浏览器自动化需处理] {provider} {reason_label}"
                lines = [
                    f"平台：{provider}",
                    f"事件：{reason_label}",
                    f"任务 ID：{job_id or '-'}",
                    f"Worker：{worker_id or '-'}",
                    f"浏览器 Profile：{profile or '-'}",
                    f"页面：{page_url or '-'}",
                    f"截图：{screenshot_path or '-'}",
                    f"原因：{message or '-'}",
                ]
                for key, value in (details or {}).items():
                    lines.append(f"{key}：{value or '-'}")
                lines.append("")
                custom_steps = [
                    str(item or "").strip()
                    for item in (resolution_steps or [])
                    if str(item or "").strip()
                ]
                if custom_steps:
                    lines.append("处理步骤：")
                    lines.extend(
                        f"{index}. {step}"
                        for index, step in enumerate(custom_steps, start=1)
                    )
                elif reason == "login":
                    lines.extend([
                        "处理步骤：",
                        "1. 关闭正在占用上述浏览器 Profile 的自动化 Chrome 窗口。",
                        f"2. 使用上述浏览器 Profile 打开 {provider} 页面。",
                        "3. 完成 Google/Gemini 登录以及验证码或安全验证。",
                        "4. 确认 Gemini 输入框可见并能进入目标 Gem。",
                        "5. 返回工作流详情页，点击“登录完成，恢复任务”。",
                        "6. 若仍失败，请根据上述截图、页面、Worker 和 Profile 定位对应浏览器实例。",
                    ])
                else:
                    lines.extend([
                        "处理步骤：",
                        f"1. 使用上述浏览器 Profile 打开 {provider} 页面。",
                        "2. 完成页面要求的验证码或安全验证。",
                        "3. 返回工作流详情页并重试被阻塞的任务。",
                    ])
                lines.extend([
                    "",
                    "系统处理：当前任务会保留人工阻塞状态，不会把登录或验证页面当作正常结果保存。",
                    "",
                    f"发送时间：{self._now_iso()}",
                ])
                self._send_email_with_retry(settings, subject, "\n".join(lines))
                stamp_path.write_text(self._now_iso(), encoding="utf-8")
                logger.warning(
                    "[BROWSER_HUMAN_INTERVENTION] email sent | provider=%s reason=%s job=%s",
                    provider,
                    reason,
                    job_id or "-",
                )
                return {"sent": True, "to": settings["to"], "event_type": reason}
        except Exception as exc:
            logger.exception(
                "[BROWSER_HUMAN_INTERVENTION] email failed | provider=%s reason=%s job=%s",
                provider,
                reason,
                job_id or "-",
            )
            return {"sent": False, "reason": "send_failed", "error": str(exc)}

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
            # The Celery delivery task owns the retry budget.  Retrying here as
            # well multiplies SMTP attempts and synchronizes failure bursts.
            "retry_attempts": self._int_env(["WORKFLOW_EMAIL_RETRY_ATTEMPTS", "SMTP_RETRY_ATTEMPTS"], default=1),
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
            f"上游接入：{workflow.get('source_mode') or '-'}",
            f"轮次：{self._round_text(workflow, plain=True) or '-'}",
        ]
        if workflow.get("content_pipeline_task_id"):
            lines.append(f"内容流水线任务：{workflow.get('content_pipeline_task_id')}")
        if workflow.get("event_message"):
            lines.append(f"说明：{workflow.get('event_message')}")
        if workflow.get("error_message"):
            lines.append(f"错误：{workflow.get('error_message')}")

        lines.extend(self._stage_lines(workflow))
        lines.append("")
        lines.append(f"发送时间：{self._local_time_text(datetime.utcnow().isoformat() + 'Z')}")
        return subject, "\n".join(lines)

    def _send_email_with_retry(self, settings, subject, body, event_id=None):
        attempts = max(1, int(settings.get("retry_attempts") or 1))
        delay = max(0.0, float(settings.get("retry_delay_seconds") or 0))
        errors = []
        for attempt in range(1, attempts + 1):
            try:
                self._send_email(settings, subject, body, event_id=event_id)
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

        if (
            settings.get("fallback_starttls")
            and int(settings.get("port") or 0) == 465
            and self._is_protocol_fallback_error(errors[-1])
        ):
            fallback = dict(settings)
            fallback["port"] = 587
            fallback["use_ssl"] = False
            fallback["use_tls"] = True
            try:
                logger.warning("[WORKFLOW_NOTIFY] SMTP SSL failed, trying STARTTLS fallback on port 587")
                self._send_email(fallback, subject, body, event_id=event_id)
                return
            except Exception as exc:
                errors.append(exc)

        raise errors[-1]

    def _send_email(self, settings, subject, body, event_id=None):
        from_addr = settings["from_addr"] or settings["user"]
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = formataddr((settings["from_name"], from_addr))
        message["To"] = settings["to"]
        if event_id:
            message["X-Workflow-Event-ID"] = str(event_id)[:200]
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
        # ``SMTPException`` inherits ``OSError`` in the standard library, so
        # SMTP response failures must be classified before generic network I/O.
        if isinstance(exc, smtplib.SMTPAuthenticationError):
            return False
        if isinstance(exc, smtplib.SMTPRecipientsRefused):
            codes = []
            for response in (exc.recipients or {}).values():
                try:
                    codes.append(int(response[0]))
                except (TypeError, ValueError, IndexError):
                    return False
            return bool(codes) and all(400 <= code < 500 for code in codes)
        if isinstance(exc, smtplib.SMTPResponseException):
            try:
                return 400 <= int(exc.smtp_code) < 500
            except (TypeError, ValueError):
                return False
        if isinstance(exc, smtplib.SMTPServerDisconnected):
            return True
        if isinstance(exc, smtplib.SMTPException):
            return False
        if isinstance(exc, ssl.SSLCertVerificationError):
            return False
        return isinstance(
            exc,
            (
                TimeoutError,
                ConnectionError,
                ConnectionResetError,
                socket.timeout,
                OSError,
                ssl.SSLError,
            ),
        )

    def _is_protocol_fallback_error(self, exc):
        if not isinstance(exc, ssl.SSLError):
            return False
        message = str(exc or "").upper()
        return any(
            marker in message
            for marker in (
                "WRONG_VERSION_NUMBER",
                "UNKNOWN_PROTOCOL",
                "UNSUPPORTED_PROTOCOL",
            )
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
                f" | 发布已提交 {totals['publish_submitted']} 个，发布后回查确认 {totals['publish_verified']} 个"
                f"，待审核/待复核 {totals['publish_pending']} 个，发布失败 {totals['publish_failed']} 个"
            )
            business_summary = self._business_summary_text(totals)
            if business_summary:
                lines.append(business_summary)

        sync = stage_results.get("sync") if isinstance(stage_results.get("sync"), dict) else {}
        if sync:
            summary = sync.get("summary") if isinstance(sync.get("summary"), dict) else {}
            bilibili_summary = summary.get("bilibili_intelligence")
            if bilibili_summary:
                lines.extend(self._bilibili_intelligence_lines(bilibili_summary))
            else:
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
            auto_video_lines = self._auto_video_dispatch_lines(summary.get("auto_video_dispatch"))
            if auto_video_lines:
                lines.append("视频编排旁路：")
                lines.extend(auto_video_lines)
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

    def _bilibili_intelligence_lines(self, summary):
        if not isinstance(summary, dict):
            return []

        lines = [
            "B站情报同步："
            f"{self._status_label(summary.get('status'))}"
            f" | 监控源 {self._num(summary.get('total_competitors'))} 个"
            f" | 成功 {self._num(summary.get('succeeded_count'))} 个"
            f" | 失败 {self._num(summary.get('failed_count'))} 个"
            f" | 抓取 {self._num(summary.get('fetched'))} 条"
            f" | 新增 {self._num(summary.get('created'))} 条"
            f" | 更新 {self._num(summary.get('updated'))} 条"
            f" | 耗时 {self._num(summary.get('duration_seconds'))} 秒"
        ]

        succeeded = summary.get("succeeded") if isinstance(summary.get("succeeded"), list) else []
        if succeeded:
            lines.append("成功明细：")
            for item in succeeded:
                if not isinstance(item, dict):
                    continue
                name = item.get("competitor_name") or item.get("competitor_id") or "未知监控源"
                uid = item.get("uid")
                identity = f"{name}（UID {uid}）" if uid else str(name)
                lines.append(
                    f"- {identity}：抓取 {self._num(item.get('fetched'))} 条，"
                    f"新增 {self._num(item.get('created'))} 条，"
                    f"更新 {self._num(item.get('updated'))} 条"
                )

        created_titles = []
        for item in succeeded:
            if not isinstance(item, dict):
                continue
            name = item.get("competitor_name") or item.get("competitor_id") or "未知监控源"
            uid = item.get("uid")
            identity = f"{name}（UID {uid}）" if uid else str(name)
            titles = item.get("created_titles")
            if not isinstance(titles, (list, tuple)):
                continue
            for title in titles:
                title = str(title or "").strip()
                if title:
                    created_titles.append((identity, title))

        if created_titles:
            lines.append(f"新增内容标题（{len(created_titles)} 条）：")
            lines.extend(f"- {identity}：{title}" for identity, title in created_titles)
        else:
            lines.append("新增内容标题：0 条")

        failed = summary.get("failed") if isinstance(summary.get("failed"), list) else []
        if failed:
            lines.append("失败明细：")
            for item in failed:
                if not isinstance(item, dict):
                    continue
                name = item.get("competitor_name") or item.get("competitor_id") or "未知监控源"
                uid = item.get("uid")
                identity = f"{name}（UID {uid}）" if uid else str(name)
                lines.append(f"- {identity}：{item.get('error') or '未知错误'}")
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

    def _auto_video_dispatch_lines(self, summary):
        if not isinstance(summary, dict) or not summary.get("enabled"):
            return []
        lines = []
        lines.append(
            f"状态 {self._status_label(summary.get('status'))}；"
            f"检查附件 {self._safe_int(summary.get('attachments_checked'))}，"
            f"达阈值 {self._safe_int(summary.get('eligible_count'))}，"
            f"已投递 {self._safe_int(summary.get('queued_count'))}，"
            f"失败 {self._safe_int(summary.get('failed_count'))}。"
        )
        for label, key in (("PDF", "pdf"), ("MP3", "mp3")):
            bucket = summary.get(key) if isinstance(summary.get(key), dict) else {}
            if not bucket:
                continue
            lines.append(
                f"{label}：阈值 {bucket.get('threshold') or 0}，检查 {self._safe_int(bucket.get('checked'))}，"
                f"投递 {self._safe_int(bucket.get('queued'))}，已有任务 {self._safe_int(bucket.get('skipped_existing'))}，"
                f"低于阈值 {self._safe_int(bucket.get('skipped_below_threshold'))}，失败 {self._safe_int(bucket.get('failed'))}。"
            )
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
            f" | 已提交 {counts['submitted']}，回查确认 {counts['verified']}，"
            f"待审核/待复核 {counts['pending']}，失败 {counts['failed']}"
        )

    def _business_summary_text(self, totals):
        if not totals:
            return ""
        text = (
            f"业务结论：下载 {totals['downloaded']} 个，清洗成功 {totals['clean_success']} 个，"
            f"发布后回查确认 {totals['publish_verified']} 个。"
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
            extras.append(f"发布待审核/待复核 {totals['publish_pending']} 个")
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
            batch_count = len(counts) or self._safe_int(summary.get("round_count")) or 1
            count_text = self._batch_count_text(counts, submitted)
            topic_count = self._first_number(item.get("topic_count"), summary.get("topic_count"))
            verified = self._first_number(item.get("verified_count"), summary.get("verified_count"))
            pending = self._first_number(item.get("pending_verify_count"), summary.get("pending_verify_count"))
            failed = self._first_number(item.get("failed_count"), summary.get("failed_count"))
            label = self._file_type_label(item.get("file_type"))
            issue_text = self._publish_issue_text(item)
            recovered_text = self._publish_recovered_issue_text(item)
            topic_text = self._publish_topic_trace_text(item)
            target_text = self._publish_target_text(item)
            if submitted <= 0:
                if failed <= 0 and not issue_text:
                    continue
                lines.append(
                    f"{label}：{batch_count} 批，发布失败 {failed} 个附件。"
                    + (f"原因：{issue_text}。" if issue_text else "")
                )
                continue
            verify_text = self._publish_verify_text(submitted, verified, pending, failed)
            extra_parts = []
            if target_text:
                extra_parts.append(f"发布目标：{target_text}")
            if topic_text:
                extra_parts.append(f"Topic：{topic_text}")
            if issue_text and (failed > 0 or pending > 0):
                extra_parts.append(f"原因：{issue_text}")
            if recovered_text:
                extra_parts.append(f"账号切换：{recovered_text}")
            extra_text = "".join(f"{part}。" for part in extra_parts)
            lines.append(
                f"{label}：{batch_count} 批，{count_text} 个附件，"
                f"{topic_count} 个 topic，{verify_text}。"
                + extra_text
            )
        return lines

    def _publish_issue_text(self, task):
        parts = []
        rules = self._publish_rule_items(task)
        for rule in rules:
            if self._publish_rule_confirmed(rule):
                continue
            parts.extend(self._publish_attempt_issue_parts(rule.get("cookie_attempts")))
            verification = rule.get("verification") if isinstance(rule.get("verification"), dict) else {}
            if verification.get("status") == "pending" and verification.get("last_error"):
                parts.append(f"回查待复核：{verification.get('last_error')}")
            status = str(rule.get("status") or "").lower()
            if status in {"failed", "partial"} and rule.get("message"):
                parts.append(str(rule.get("message")).strip())
        if not parts:
            attempts = self._publish_attempts(task)
            parts.extend(self._publish_attempt_issue_parts(attempts))
        return "；".join(parts[:4])

    def _publish_recovered_issue_text(self, task):
        parts = []
        for rule in self._publish_rule_items(task):
            if not self._publish_rule_confirmed(rule):
                continue
            final_account = rule.get("cookie_account_name") or ""
            attempts = rule.get("cookie_attempts") if isinstance(rule.get("cookie_attempts"), list) else []
            for attempt in attempts:
                if not isinstance(attempt, dict):
                    continue
                status = str(attempt.get("status") or "").strip()
                if status != "retry_with_next_cookie":
                    continue
                name = attempt.get("cookie_account_name") or attempt.get("cookie_account_id") or f"账号{attempt.get('index') or ''}"
                auth_hint = str(attempt.get("auth_hint") or "").strip()
                quota_hint = str(attempt.get("quota_hint") or "").strip()
                if auth_hint:
                    text = f"{name} 认证失败/401"
                elif quota_hint:
                    text = f"{name} 命中{quota_hint}"
                else:
                    text = f"{name} 切换下一个账号"
                if final_account:
                    text += f"，已切换到{final_account}发布"
                if text not in parts:
                    parts.append(text)
        return "；".join(parts[:4])

    def _publish_topic_trace_text(self, task):
        parts = []
        for rule in self._publish_rule_items(task):
            topic_id = str(rule.get("topic_id") or "").strip()
            if not topic_id:
                continue
            account = str(rule.get("cookie_account_name") or "").strip()
            verification = rule.get("verification") if isinstance(rule.get("verification"), dict) else {}
            expected = self._safe_int(verification.get("expected_file_count") or rule.get("submitted_count"))
            file_count = self._safe_int(verification.get("file_count") or rule.get("verified_count"))
            status = str(verification.get("status") or "").strip()
            if status == "verified":
                verify_text = f"{file_count}/{expected} 回查确认" if expected else "回查确认"
            elif status == "pending":
                verify_text = f"{file_count}/{expected} 待复核" if expected else "待复核"
            else:
                verify_text = f"{file_count}/{expected}" if expected else status
            prefix = f"{account}/" if account else ""
            text = f"{prefix}{topic_id}"
            if verify_text:
                text += f"（{verify_text}）"
            parts.append(text)
        return "；".join(parts[:6])

    def _publish_target_text(self, task):
        targets = []
        for run in self._publish_runs(task):
            group_name = str(run.get("group_name") or "").strip()
            group_id = str(run.get("group_id") or "").strip()
            text = group_name or group_id
            if text and text not in targets:
                targets.append(text)
        return "；".join(targets[:4])

    def _publish_rule_items(self, task):
        if not isinstance(task, dict):
            return []
        rules = []
        for run in self._publish_runs(task):
            for rule in run.get("rules") or []:
                if isinstance(rule, dict):
                    rules.append(rule)
        if not rules and (task.get("topic_id") or task.get("cookie_attempts")):
            rules.append(task)
        return rules

    def _publish_runs(self, task):
        if not isinstance(task, dict):
            return []
        sources = []
        runtime = task.get("publish_runtime") if isinstance(task.get("publish_runtime"), dict) else None
        result = task.get("publish_result") if isinstance(task.get("publish_result"), dict) else None
        if runtime:
            sources.append(runtime)
        if result:
            sources.append(result)
        if isinstance(task.get("rounds"), list):
            sources.append(task)

        runs = []
        for source in sources:
            if isinstance(source.get("runs"), list):
                runs.extend(source.get("runs") or [])
            if isinstance(source.get("rounds"), list):
                runs.extend(source.get("rounds") or [])
        return [run for run in runs if isinstance(run, dict)]

    def _publish_attempts(self, task):
        attempts = []
        for rule in self._publish_rule_items(task):
            for attempt in rule.get("cookie_attempts") or []:
                if isinstance(attempt, dict):
                    attempts.append(attempt)
        return attempts

    def _publish_rule_confirmed(self, rule):
        if not isinstance(rule, dict):
            return False
        submitted = self._safe_int(rule.get("submitted_count") or rule.get("uploaded_count"))
        verified = self._safe_int(rule.get("verified_count"))
        pending = self._safe_int(rule.get("pending_verify_count"))
        failed = self._safe_int(rule.get("failed_count"))
        status = str(rule.get("status") or "").lower()
        verification = rule.get("verification") if isinstance(rule.get("verification"), dict) else {}
        verification_ok = verification.get("confirmed") is True or verification.get("status") == "verified"
        return submitted > 0 and verified >= submitted and pending <= 0 and failed <= 0 and status == "success" and verification_ok

    def _publish_attempt_issue_parts(self, attempts):
        parts = []
        for attempt in attempts or []:
            if not isinstance(attempt, dict):
                continue
            name = attempt.get("cookie_account_name") or attempt.get("cookie_account_id") or f"账号{attempt.get('index') or ''}"
            auth_hint = str(attempt.get("auth_hint") or "").strip()
            quota_hint = str(attempt.get("quota_hint") or "").strip()
            status = str(attempt.get("status") or "").strip()
            if auth_hint:
                parts.append(f"{name} 认证失败/401，需要更新 Cookie")
            elif quota_hint:
                parts.append(f"{name} 命中{quota_hint}")
            elif status == "failed":
                message = str(attempt.get("message") or "发布失败").strip()
                parts.append(f"{name} {message}")
        return parts

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
            f"说明：有 {failed_with_runtime} 个发布子任务状态为失败，但本地发布运行记录显示已有内容发布后回查确认，"
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
            return f"回查确认 {verified}，待审核/待复核 {pending}，失败 {failed}"
        if pending:
            return f"回查确认 {verified}，待审核/待复核 {pending}"
        if verified >= submitted:
            return "全部发布后回查确认"
        return f"回查确认 {verified}"

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
            "blocked": "已阻塞",
            "blocked_by_captcha": "人机验证阻塞",
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
