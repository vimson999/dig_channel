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
    "test": "测试通知",
}

FINAL_EVENTS = {"workflow_completed", "workflow_partial", "workflow_failed", "report_audio_completed", "report_audio_failed"}
FAILURE_EVENTS = {"workflow_failed", "report_audio_failed"}
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

        sync = stage_results.get("sync") if isinstance(stage_results.get("sync"), dict) else {}
        if sync:
            summary = sync.get("summary") if isinstance(sync.get("summary"), dict) else {}
            lines.append(
                "同步："
                f"{self._status_label(sync.get('status'))}"
                f" | 新增 {self._num(summary.get('topics_new'))}"
                f" | 更新 {self._num(summary.get('topics_updated'))}"
                f" | 下载 {self._num(summary.get('attachments_downloaded'))}"
                f" | 失败 {self._num(summary.get('attachments_failed'))}"
            )
            if sync.get("error"):
                lines.append(f"同步错误：{sync.get('error')}")

        clean = stage_results.get("clean") if isinstance(stage_results.get("clean"), dict) else {}
        if clean:
            lines.append(
                f"清洗：{self._status_label(clean.get('status'))}"
                f"{self._tasks_text(clean.get('tasks'), stage_status=clean.get('status'))}"
            )
            if clean.get("error"):
                lines.append(f"清洗错误：{clean.get('error')}")

        publish = stage_results.get("publish") if isinstance(stage_results.get("publish"), dict) else {}
        if publish:
            lines.append(
                f"发布：{self._status_label(publish.get('status'))}"
                f"{self._tasks_text(publish.get('tasks'), stage_status=publish.get('status'))}"
            )
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
