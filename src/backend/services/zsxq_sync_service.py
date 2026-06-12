import json
import logging
import os
import random
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests
from flask import current_app, has_app_context
from sqlalchemy import and_, inspect, or_

from models import db
from models.zsxq_sync import ZsxqAttachment, ZsxqSyncCursor, ZsxqSyncRun, ZsxqTopic
from services.zsxq_service import zsxq_service


logger = logging.getLogger(__name__)


TRANSIENT_UPSTREAM_CODES = {"1059"}


class ZsxqTransientUpstreamError(ValueError):
    """Raised when ZSXQ returns a retryable upstream failure."""

    def __init__(self, code, message, payload=None):
        self.code = str(code or "UNKNOWN")
        self.payload = payload
        super().__init__(f"知识星球接口返回失败 code={self.code}: {message}")


class ZsxqSyncService:
    """Manual-first ZSXQ sync service.

    Cookie is supplied per request and is never persisted. Synced topic metadata
    goes to DB; raw topic JSON is also written under data/zsxq for local review.
    """

    def __init__(self):
        self._schema_checked = False
        self._last_pdf_download_at = 0.0

    def ensure_schema(self):
        if self._schema_checked:
            return
        required_tables = {
            "zsxq_sync_runs",
            "zsxq_topics",
            "zsxq_attachments",
            "zsxq_sync_cursors",
        }
        inspector = inspect(db.engine)
        existing_tables = self._table_names(inspector)
        missing_tables = sorted(required_tables - existing_tables)
        if missing_tables:
            raise ValueError(
                "知识星球同步表尚未初始化，请先用有建表权限的数据库账号执行 "
                "docs/sql/zsxq_sync_tables.sql；缺失表："
                + ", ".join(missing_tables)
            )
        attachment_columns = {
            item["name"]
            for item in self._get_columns(inspector, "zsxq_attachments")
        }
        if "error_message" not in attachment_columns:
            raise ValueError(
                "知识星球同步表结构需要更新，请执行 docs/sql/zsxq_sync_tables.sql 中的 "
                "ALTER TABLE public.zsxq_attachments ADD COLUMN IF NOT EXISTS error_message text;"
            )
        self._schema_checked = True

    def _table_names(self, inspector):
        """Return table names with compatibility for different SQL dialects."""
        try:
            return set(inspector.get_table_names(schema="public"))
        except Exception:
            return set(inspector.get_table_names())

    def _get_columns(self, inspector, table_name):
        """Read columns with fallback when schema argument is unsupported."""
        try:
            return inspector.get_columns(table_name, schema="public")
        except Exception:
            return inspector.get_columns(table_name)

    def sync_tabs(
        self,
        cookie,
        group_id,
        group_name=None,
        tabs=None,
        count=20,
        max_pages=1,
        stop_on_existing=True,
        download_attachments=True,
        download_pdf_only=True,
        download_file_types=None,
        download_file_types_by_tab=None,
        max_download_files=None,
        mode="manual",
        run_id=None,
        sync_meta=None,
    ):
        self.ensure_schema()
        tabs = self._normalize_tabs(tabs)
        if not tabs:
            raise ValueError("tabs 不能为空")

        group_id = str(group_id)
        mode = self._normalize_mode(mode)
        count = min(max(int(count or 20), 1), 50)
        max_pages = min(max(int(max_pages or 1), 1), 20)
        download_file_types = self._normalize_download_file_types(download_file_types, download_pdf_only)
        download_file_types_by_tab = self._normalize_download_file_types_by_tab(download_file_types_by_tab)
        max_download_files = self._normalize_max_download_files(max_download_files)
        sync_meta = sync_meta if isinstance(sync_meta, dict) else {}
        running_result = {"running": True}
        if sync_meta:
            running_result["system_sync"] = sync_meta

        if run_id:
            run = ZsxqSyncRun.query.get(str(run_id))
            if not run:
                raise ValueError(f"同步任务不存在: {run_id}")
            run.status = "running"
            run.error_message = None
            run.result = running_result
            run.group_id = group_id
            run.group_name = group_name
            run.tab_id = tabs[0]["id"] if len(tabs) == 1 else None
            run.tab_label = tabs[0]["label"] if len(tabs) == 1 else None
            run.mode = mode
            run.requested_tabs = [self._public_tab(tab) for tab in tabs]
        else:
            run = ZsxqSyncRun(
                group_id=group_id,
                group_name=group_name,
                tab_id=tabs[0]["id"] if len(tabs) == 1 else None,
                tab_label=tabs[0]["label"] if len(tabs) == 1 else None,
                mode=mode,
                status="running",
                requested_tabs=[self._public_tab(tab) for tab in tabs],
                result=running_result,
            )
            db.session.add(run)
        db.session.commit()

        result = {
            "group_id": group_id,
            "group_name": group_name,
            "tabs_total": len(tabs),
            "topics_fetched": 0,
            "topics_new": 0,
            "topics_updated": 0,
            "topics_existing": 0,
            "attachments_seen": 0,
            "attachments_downloaded": 0,
            "attachments_failed": 0,
            "attachments_skipped": 0,
            "downloaded_files": [],
            "downloaded_pdf_files": [],
            "max_download_files": max_download_files,
            "download_limit_reached": False,
            "tabs": [],
        }
        if sync_meta:
            result["system_sync"] = sync_meta

        try:
            for index, tab in enumerate(tabs):
                if index > 0:
                    self._human_pause()
                tab_download_file_types = download_file_types_by_tab.get(tab["id"], download_file_types)
                tab_result = self._sync_one_tab(
                    cookie=cookie,
                    group_id=group_id,
                    group_name=group_name,
                    tab=tab,
                    run=run,
                    count=count,
                    max_pages=max_pages,
                    stop_on_existing=stop_on_existing,
                    download_attachments=download_attachments,
                    download_file_types=tab_download_file_types,
                    max_download_files=max_download_files,
                )
                result["tabs"].append(tab_result)
                result["topics_fetched"] += tab_result["topics_fetched"]
                result["topics_new"] += tab_result["topics_new"]
                result["topics_updated"] += tab_result["topics_updated"]
                result["topics_existing"] += tab_result["topics_existing"]
                result["attachments_seen"] += tab_result["attachments_seen"]
                result["attachments_downloaded"] += tab_result["attachments_downloaded"]
                result["attachments_failed"] += tab_result["attachments_failed"]
                result["attachments_skipped"] += tab_result["attachments_skipped"]
                self._merge_downloaded_file_entries(result, tab_result)
                if tab_result.get("download_limit_reached") or tab_result.get("stopped_by_download_limit"):
                    result["download_limit_reached"] = True

            run.status = "completed"
            run.completed_at = datetime.utcnow()
            run.result = result
            db.session.commit()
            return {
                "run": run.to_dict(),
                "summary": result,
            }
        except Exception as exc:
            db.session.rollback()
            run.status = "failed"
            run.completed_at = datetime.utcnow()
            run.error_message = str(exc)
            run.result = result
            db.session.add(run)
            db.session.commit()
            logger.warning("[ZSXQ_SYNC] sync failed | run_id=%s | error=%s", run.id, exc, exc_info=True)
            raise

    def create_sync_run(
        self,
        group_id,
        group_name=None,
        tabs=None,
        mode="manual",
        initial_result=None,
    ):
        self.ensure_schema()
        tabs = self._normalize_tabs(tabs)
        if not tabs:
            raise ValueError("tabs 不能为空")
        mode = self._normalize_mode(mode)

        run = ZsxqSyncRun(
            group_id=str(group_id),
            group_name=group_name,
            tab_id=tabs[0]["id"] if len(tabs) == 1 else None,
            tab_label=tabs[0]["label"] if len(tabs) == 1 else None,
            mode=mode,
            status="queued",
            requested_tabs=[self._public_tab(tab) for tab in tabs],
            result=initial_result if isinstance(initial_result, dict) else {"queued": True},
        )
        db.session.add(run)
        db.session.commit()
        return run.to_dict()

    def _normalize_mode(self, mode):
        value = str(mode or "manual").strip() or "manual"
        return value[:20]

    def list_runs(self, group_id=None, limit=20):
        self.ensure_schema()
        self._mark_stale_runs_failed(group_id=group_id)
        query = ZsxqSyncRun.query.order_by(ZsxqSyncRun.created_at.desc())
        if group_id:
            query = query.filter(ZsxqSyncRun.group_id == str(group_id))
        return [item.to_dict() for item in query.limit(min(max(int(limit or 20), 1), 100)).all()]

    def list_system_sync_batches(self, group_id=None, limit=10, batch_id=None):
        self.ensure_schema()
        self._mark_stale_runs_failed(group_id=group_id)
        query = ZsxqSyncRun.query.filter(ZsxqSyncRun.mode.like("sys%")).order_by(ZsxqSyncRun.created_at.desc())
        if group_id:
            query = query.filter(ZsxqSyncRun.group_id == str(group_id))

        batches = {}
        target_batch_id = str(batch_id or "").strip()
        query_limit = 500 if target_batch_id else 120
        for run in query.limit(query_limit).all():
            run_payload = run.to_dict()
            result = run_payload.get("result") or {}
            meta = result.get("system_sync") if isinstance(result, dict) else None
            meta = meta if isinstance(meta, dict) else {}
            current_batch_id = meta.get("batch_id") or self._legacy_system_batch_id(run.mode)
            if not current_batch_id:
                continue
            if target_batch_id and target_batch_id not in {str(current_batch_id), str(meta.get("attempt_id") or "")}:
                continue

            batch = batches.setdefault(current_batch_id, {
                "batch_id": current_batch_id,
                "attempt_id": meta.get("attempt_id") or current_batch_id,
                "group_id": run_payload.get("group_id"),
                "group_name": run_payload.get("group_name"),
                "round": meta.get("round"),
                "max_rounds": meta.get("max_rounds"),
                "trigger": meta.get("trigger"),
                "queued_at": meta.get("queued_at"),
                "max_download_files": meta.get("max_download_files"),
                "jobs": meta.get("jobs") or [],
                "created_at": run_payload.get("created_at"),
                "completed_at": run_payload.get("completed_at"),
                "status": "completed",
                "runs": [],
                "topics": [],
                "topics_truncated": False,
                "summary": {
                    "runs_total": 0,
                    "runs_completed": 0,
                    "runs_failed": 0,
                    "runs_active": 0,
                    "topics_fetched": 0,
                    "topics_new": 0,
                    "topics_updated": 0,
                    "topics_existing": 0,
                    "attachments_seen": 0,
                    "attachments_downloaded": 0,
                    "attachments_failed": 0,
                    "attachments_skipped": 0,
                    "download_limit_reached": False,
                },
            })
            batch["created_at"] = min(filter(None, [batch.get("created_at"), run_payload.get("created_at")]), default=run_payload.get("created_at"))
            batch["completed_at"] = max(filter(None, [batch.get("completed_at"), run_payload.get("completed_at")]), default=run_payload.get("completed_at"))

            topic_detail = self._topics_for_run(run)
            run_payload["failed_attachments"] = self._failed_attachments_for_run(run)
            run_payload["topics"] = topic_detail["topics"]
            run_payload["topics_truncated"] = topic_detail["truncated"]
            batch["runs"].append(run_payload)
            batch["topics"].extend(run_payload["topics"])
            batch["topics_truncated"] = batch["topics_truncated"] or run_payload["topics_truncated"]
            self._merge_batch_summary(batch, run_payload)

        ordered = sorted(
            batches.values(),
            key=lambda item: item.get("created_at") or "",
            reverse=True,
        )
        max_limit = min(max(int(limit or 10), 1), 30)
        return ordered[:max_limit]

    def get_system_sync_batch(self, batch_id, group_id=None):
        batches = self.list_system_sync_batches(
            group_id=group_id,
            limit=1,
            batch_id=batch_id,
        )
        return batches[0] if batches else None

    def _topics_for_run(self, run, limit=80):
        tab_ids = self._run_tab_ids(run)
        query = ZsxqTopic.query.filter(
            ZsxqTopic.group_id == run.group_id,
            or_(
                ZsxqTopic.first_sync_run_id == run.id,
                ZsxqTopic.last_sync_run_id == run.id,
            ),
        )
        if tab_ids:
            query = query.filter(ZsxqTopic.tab_id.in_(tab_ids))

        topics = query.order_by(ZsxqTopic.last_synced_at.desc()).limit(limit + 1).all()
        truncated = len(topics) > limit
        topics = topics[:limit]
        attachments = self._attachments_by_topic(run.group_id, topics)
        return {
            "topics": [
                self._public_run_topic(topic, run, attachments.get((topic.tab_id, topic.topic_id), []))
                for topic in topics
            ],
            "truncated": truncated,
        }

    def _run_tab_ids(self, run):
        tab_ids = []
        if run.tab_id:
            tab_ids.append(str(run.tab_id))
        for tab in run.requested_tabs or []:
            if isinstance(tab, dict) and tab.get("id"):
                tab_ids.append(str(tab["id"]))
        return sorted(set(tab_ids))

    def _attachments_by_topic(self, group_id, topics):
        if not topics:
            return {}
        topic_ids = sorted({item.topic_id for item in topics if item.topic_id})
        tab_ids = sorted({item.tab_id for item in topics if item.tab_id})
        if not topic_ids:
            return {}

        query = ZsxqAttachment.query.filter(
            ZsxqAttachment.group_id == str(group_id),
            ZsxqAttachment.topic_id.in_(topic_ids),
        )
        if tab_ids:
            query = query.filter(ZsxqAttachment.tab_id.in_(tab_ids))

        grouped = {}
        for attachment in query.order_by(ZsxqAttachment.topic_id.asc(), ZsxqAttachment.created_at.asc()).all():
            grouped.setdefault((attachment.tab_id, attachment.topic_id), []).append(attachment)
        return grouped

    def _public_run_topic(self, topic, run, attachments):
        status = "new" if topic.first_sync_run_id == run.id else "touched"
        return {
            "topic_id": topic.topic_id,
            "tab_id": topic.tab_id,
            "tab_label": topic.tab_label,
            "status": status,
            "status_label": "新增" if status == "new" else "本轮处理",
            "title": topic.title,
            "author_name": topic.author_name,
            "content": topic.content,
            "create_time": topic.create_time,
            "raw_json_path": topic.raw_json_path,
            "first_sync_run_id": topic.first_sync_run_id,
            "last_sync_run_id": topic.last_sync_run_id,
            "attachments": [self._public_run_attachment(item) for item in attachments],
        }

    def _public_run_attachment(self, attachment):
        payload = attachment.to_dict()
        payload["file_type"] = self._attachment_file_type(attachment)
        payload["is_reusable"] = self._download_record_is_reusable(attachment)
        return payload

    def _legacy_system_batch_id(self, mode):
        value = str(mode or "")
        if not value.startswith("sys"):
            return None
        parts = value.split("_")
        if len(parts) >= 3:
            return f"legacy-{parts[-1]}"
        return value or None

    def _failed_attachments_for_run(self, run):
        query = ZsxqAttachment.query.filter(
            ZsxqAttachment.group_id == run.group_id,
            ZsxqAttachment.download_status == "failed",
        )
        tab_ids = []
        if run.tab_id:
            tab_ids.append(run.tab_id)
        else:
            for tab in run.requested_tabs or []:
                if isinstance(tab, dict) and tab.get("id"):
                    tab_ids.append(str(tab["id"]))
        if tab_ids:
            query = query.filter(ZsxqAttachment.tab_id.in_(tab_ids))
        if run.started_at:
            query = query.filter(ZsxqAttachment.updated_at >= run.started_at - timedelta(minutes=2))
        if run.completed_at:
            query = query.filter(ZsxqAttachment.updated_at <= run.completed_at + timedelta(minutes=2))
        return [
            item.to_dict()
            for item in query.order_by(ZsxqAttachment.updated_at.desc()).limit(20).all()
        ]

    def _merge_batch_summary(self, batch, run):
        summary = batch["summary"]
        result = run.get("result") or {}
        summary["runs_total"] += 1
        if run.get("status") == "completed":
            summary["runs_completed"] += 1
        elif run.get("status") == "failed":
            summary["runs_failed"] += 1
        elif run.get("status") in {"queued", "running"}:
            summary["runs_active"] += 1

        if run.get("status") == "failed":
            batch["status"] = "failed"
        elif run.get("status") in {"queued", "running"} and batch["status"] != "failed":
            batch["status"] = "running"

        for key in (
            "topics_fetched",
            "topics_new",
            "topics_updated",
            "topics_existing",
            "attachments_seen",
            "attachments_downloaded",
            "attachments_failed",
            "attachments_skipped",
        ):
            summary[key] += int(result.get(key) or 0)
        summary["download_limit_reached"] = (
            summary["download_limit_reached"] or bool(result.get("download_limit_reached"))
        )

    def mark_run_failed(self, run_id, error_message, result_update=None):
        self.ensure_schema()
        run = ZsxqSyncRun.query.get(str(run_id))
        if not run:
            return None
        result = dict(run.result) if isinstance(run.result, dict) else {}
        if result_update:
            result.update(result_update)
        run.status = "failed"
        run.completed_at = datetime.utcnow()
        run.error_message = str(error_message)[:1000]
        run.result = result
        db.session.commit()
        return run.to_dict()

    def submit_pdf_clean_for_run(self, run_id, cleaner=None, trigger="zsxq_sync"):
        """Submit downloaded PDFs from one sync run to the content pipeline."""
        run = ZsxqSyncRun.query.get(str(run_id))
        if not run:
            raise ValueError(f"同步任务不存在: {run_id}")

        cleanable = self.downloaded_pdf_files_for_run(run_id)
        files = [item["path"] for item in cleanable["files"]]
        if not files:
            result = {
                "queued": False,
                "reason": "no_downloaded_pdf",
                "files_count": 0,
            }
            self._merge_run_pipeline_result(run, result)
            return result

        from services.content_pipeline.pdf_clean_task_service import submit_pdf_clean_task

        task = submit_pdf_clean_task(
            title=f"知识星球 PDF 清洗 - {run.group_name or run.group_id}",
            files=files,
            source_root=cleanable.get("source_root"),
            cleaner=cleaner,
            trigger=trigger,
            metadata={
                "sync_run_id": run.id,
                "group_id": run.group_id,
                "group_name": run.group_name,
                "mode": run.mode,
                "files": cleanable["files"],
            },
        )
        result = {
            "queued": True,
            "task_id": task["id"],
            "celery_task_id": task.get("celery_task_id"),
            "files_count": len(files),
            "source_root": cleanable.get("source_root"),
        }
        self._merge_run_pipeline_result(run, result)
        return result

    def set_run_pdf_clean_result(self, run_id, pdf_clean_result):
        run = ZsxqSyncRun.query.get(str(run_id))
        if not run:
            raise ValueError(f"同步任务不存在: {run_id}")
        self._merge_run_pipeline_result(run, pdf_clean_result)
        return run.to_dict()

    def downloaded_pdf_files_for_run(self, run_id):
        run = ZsxqSyncRun.query.get(str(run_id))
        if not run:
            raise ValueError(f"同步任务不存在: {run_id}")

        return {
            "files": self._downloaded_pdf_files_from_run_result(run),
            "source_root": self._run_source_root(run),
        }

    def _downloaded_pdf_files_from_run_result(self, run):
        result = run.result if isinstance(run.result, dict) else {}
        raw_files = result.get("downloaded_pdf_files")
        if raw_files is None:
            raw_files = []
            for tab in result.get("tabs") or []:
                if isinstance(tab, dict):
                    raw_files.extend(tab.get("downloaded_pdf_files") or [])

        files = []
        seen_paths = set()
        for item in raw_files or []:
            if isinstance(item, dict):
                path_value = item.get("path") or item.get("local_path")
                metadata = item
            else:
                path_value = item
                metadata = {}
            path = self._absolute_local_path(path_value)
            if not path or path.suffix.lower() != ".pdf" or not path.exists() or not path.is_file():
                continue
            path_key = str(path)
            if path_key in seen_paths:
                continue
            seen_paths.add(path_key)
            files.append({
                "path": path_key,
                "name": metadata.get("name") or path.name,
                "topic_id": metadata.get("topic_id"),
                "tab_id": metadata.get("tab_id"),
                "attachment_id": metadata.get("attachment_id"),
            })
        return files

    def _merge_run_pipeline_result(self, run, pdf_clean_result):
        result = dict(run.result) if isinstance(run.result, dict) else {}
        content_pipeline = dict(result.get("content_pipeline") or {})
        content_pipeline["pdf_clean"] = pdf_clean_result
        result["content_pipeline"] = content_pipeline
        run.result = result
        db.session.commit()

    def _run_source_root(self, run):
        source_root = self._data_root() / str(run.group_id)
        return str(source_root) if source_root.exists() else None

    def _absolute_local_path(self, local_path):
        if not local_path or str(local_path).startswith("browser-download:"):
            return None
        try:
            path = Path(local_path)
            if not path.is_absolute():
                path = self._project_root() / path
            return path.resolve()
        except (TypeError, OSError, ValueError):
            return None

    def get_attachment_statuses(self, group_id, tab_id, topic_ids, attachment_refs=None):
        if not self._sync_tables_ready():
            return {}

        group_id = str(group_id)
        tab_id = str(tab_id)
        topic_ids = [str(item) for item in (topic_ids or []) if item]
        if not topic_ids:
            return {}

        rows = ZsxqAttachment.query.filter(
            ZsxqAttachment.group_id == group_id,
            ZsxqAttachment.tab_id == tab_id,
            ZsxqAttachment.topic_id.in_(topic_ids[:200]),
        ).all()

        result = {}
        seen_keys = set()

        def add_row(row, bucket_topic_id=None):
            result_topic_id = str(bucket_topic_id or row.topic_id)
            key = (result_topic_id, row.attachment_key, row.tab_id, row.id)
            if key in seen_keys:
                return
            seen_keys.add(key)
            result.setdefault(result_topic_id, []).append(row.to_dict())

        for row in rows:
            add_row(row)

        terminal_status_topic_ids = {
            row.topic_id for row in rows if row.download_status in {"downloaded", "failed"}
        }
        fallback_topic_ids = [
            topic_id for topic_id in topic_ids[:200]
            if topic_id not in terminal_status_topic_ids
        ]
        if fallback_topic_ids:
            fallback_rows = ZsxqAttachment.query.filter(
                ZsxqAttachment.group_id == group_id,
                ZsxqAttachment.topic_id.in_(fallback_topic_ids),
                ZsxqAttachment.download_status.in_(["downloaded", "failed"]),
            ).all()
            for row in fallback_rows:
                add_row(row)

        self._add_attachment_ref_statuses(
            result=result,
            seen_keys=seen_keys,
            group_id=group_id,
            tab_id=tab_id,
            attachment_refs=attachment_refs,
        )
        return result

    def _add_attachment_ref_statuses(self, result, seen_keys, group_id, tab_id, attachment_refs=None):
        refs = []
        for item in attachment_refs or []:
            if not isinstance(item, dict):
                continue
            topic_id = str(item.get("topic_id") or "").strip()
            if not topic_id:
                continue
            refs.append({
                "topic_id": topic_id,
                "attachment_key": str(item.get("attachment_key") or "").strip(),
                "file_id": str(item.get("file_id") or "").strip(),
                "name": str(item.get("name") or "").strip(),
            })
        if not refs:
            return

        file_ids = {item["file_id"] for item in refs if item["file_id"]}
        attachment_keys = {item["attachment_key"] for item in refs if item["attachment_key"]}
        names = {item["name"] for item in refs if item["name"]}
        conditions = []
        if file_ids:
            conditions.append(ZsxqAttachment.file_id.in_(file_ids))
        if attachment_keys:
            conditions.append(ZsxqAttachment.attachment_key.in_(attachment_keys))
        if names:
            conditions.append(ZsxqAttachment.name.in_(names))
        if not conditions:
            return

        rows = ZsxqAttachment.query.filter(
            ZsxqAttachment.group_id == group_id,
            ZsxqAttachment.tab_id == tab_id,
            ZsxqAttachment.download_status.in_(["downloaded", "failed"]),
            or_(*conditions),
        ).all()
        if not rows:
            return

        def add_row(row, bucket_topic_id):
            key = (bucket_topic_id, row.attachment_key, row.tab_id, row.id)
            if key in seen_keys:
                return
            seen_keys.add(key)
            result.setdefault(bucket_topic_id, []).append(row.to_dict())

        for ref in refs:
            for row in rows:
                if (
                    (ref["file_id"] and row.file_id == ref["file_id"])
                    or (ref["attachment_key"] and row.attachment_key == ref["attachment_key"])
                    or (ref["name"] and row.name == ref["name"])
                ):
                    add_row(row, ref["topic_id"])

    def mark_browser_download(self, group_id, tab_id, topic_id, file_info, filename=None):
        return self._mark_browser_download_status(
            group_id=group_id,
            tab_id=tab_id,
            topic_id=topic_id,
            file_info=file_info,
            filename=filename,
            download_status="downloaded",
            local_path=f"browser-download:{filename or self._attachment_name(file_info, 'file')}" if isinstance(file_info, dict) else None,
            error_message=None,
        )

    def mark_browser_download_failed(self, group_id, tab_id, topic_id, file_info, error_message, filename=None):
        return self._mark_browser_download_status(
            group_id=group_id,
            tab_id=tab_id,
            topic_id=topic_id,
            file_info=file_info,
            filename=filename,
            download_status="failed",
            local_path=None,
            error_message=str(error_message)[:1000],
        )

    def _mark_browser_download_status(self, group_id, tab_id, topic_id, file_info, filename=None, download_status="pending", local_path=None, error_message=None):
        if not group_id or not tab_id or not topic_id or not isinstance(file_info, dict):
            return None
        if not self._sync_tables_ready():
            return None

        group_id = str(group_id)
        tab_id = str(tab_id)
        topic_id = str(topic_id)
        key = self._attachment_key(file_info, 0, "file")
        record = ZsxqAttachment.query.filter_by(
            group_id=group_id,
            tab_id=tab_id,
            topic_id=topic_id,
            attachment_key=key,
        ).first()
        values = {
            "group_id": group_id,
            "tab_id": tab_id,
            "topic_id": topic_id,
            "attachment_key": key,
            "attachment_type": "file",
            "name": filename or self._attachment_name(file_info, "file"),
            "file_id": self._attachment_file_id(file_info),
            "download_count": self._download_count(file_info),
            "download_status": download_status,
            "local_path": local_path,
            "error_message": error_message,
            "raw_payload": file_info,
        }
        if record:
            for field, value in values.items():
                setattr(record, field, value)
        else:
            record = ZsxqAttachment(**values)
            db.session.add(record)
        db.session.commit()
        return record.to_dict()

    def _sync_tables_ready(self):
        required_tables = {
            "zsxq_sync_runs",
            "zsxq_topics",
            "zsxq_attachments",
            "zsxq_sync_cursors",
        }
        inspector = inspect(db.engine)
        existing_tables = self._table_names(inspector)
        if not required_tables.issubset(existing_tables):
            return False
        columns = {item["name"] for item in self._get_columns(inspector, "zsxq_attachments")}
        return "error_message" in columns

    def _sync_one_tab(self, cookie, group_id, group_name, tab, run, count, max_pages, stop_on_existing, download_attachments, download_file_types, max_download_files=None):
        tab_id = tab["id"]
        tab_label = tab["label"]
        end_time = None
        tab_result = {
            "tab_id": tab_id,
            "tab_label": tab_label,
            "pages": 0,
            "topics_fetched": 0,
            "topics_new": 0,
            "topics_updated": 0,
            "topics_existing": 0,
            "attachments_seen": 0,
            "attachments_downloaded": 0,
            "attachments_failed": 0,
            "attachments_skipped": 0,
            "downloaded_files": [],
            "downloaded_pdf_files": [],
            "stopped_by_existing": False,
            "stopped_by_download_limit": False,
            "download_limit_reached": False,
            "last_end_time": None,
        }
        newest_topic = None
        seen_ids = set()

        for _ in range(max_pages):
            if tab_result["pages"] > 0:
                self._human_pause()
            response = self._get_topics_with_retry(
                cookie=cookie,
                group_id=group_id,
                count=count,
                scope=tab.get("scope") or "all",
                end_time=end_time,
                extra_params=self._tab_extra_params(tab),
                tab_id=tab_id,
                tab_label=tab_label,
                page=tab_result["pages"] + 1,
            )
            topics = self._extract_topics(response)
            tab_result["pages"] += 1
            tab_result["topics_fetched"] += len(topics)

            if not topics:
                break

            for topic in topics:
                topic_id = self._topic_id(topic)
                if not topic_id or topic_id in seen_ids:
                    continue
                seen_ids.add(topic_id)
                if newest_topic is None:
                    newest_topic = topic

                existing = ZsxqTopic.query.filter_by(group_id=group_id, tab_id=tab_id, topic_id=topic_id).first()
                if existing:
                    tab_result["topics_existing"] += 1
                    attachment_stats = self._upsert_attachments(group_id, tab, topic)
                    self._merge_attachment_stats(tab_result, attachment_stats)
                    download_stats = {}
                    if download_attachments:
                        download_stats = self._download_topic_attachments(
                            cookie=cookie,
                            group_id=group_id,
                            tab=tab,
                            topic=topic,
                            download_file_types=download_file_types,
                            max_download_files=max_download_files,
                        )
                        self._merge_attachment_stats(tab_result, download_stats)
                        if download_stats.get("download_limit_reached"):
                            tab_result["download_limit_reached"] = True
                    has_pending_download_work = (
                        download_stats.get("attachments_downloaded", 0) > 0
                        or download_stats.get("attachments_failed", 0) > 0
                    )
                    if stop_on_existing and not max_download_files and not has_pending_download_work:
                        tab_result["stopped_by_existing"] = True
                        break
                    changed = self._update_existing_topic(existing, topic, tab, run)
                    tab_result["topics_updated"] += 1 if changed else 0
                    self._commit_running_progress(run, tab, topic_id, tab_result)
                    continue

                topic_record = self._create_topic_record(group_id, group_name, tab, topic, run)
                db.session.add(topic_record)
                db.session.flush()
                attachment_stats = self._upsert_attachments(group_id, tab, topic)
                self._merge_attachment_stats(tab_result, attachment_stats)
                if download_attachments:
                    download_stats = self._download_topic_attachments(
                        cookie=cookie,
                        group_id=group_id,
                        tab=tab,
                        topic=topic,
                        download_file_types=download_file_types,
                        max_download_files=max_download_files,
                    )
                    self._merge_attachment_stats(tab_result, download_stats)
                    if download_stats.get("download_limit_reached"):
                        tab_result["download_limit_reached"] = True
                tab_result["topics_new"] += 1
                self._commit_running_progress(run, tab, topic_id, tab_result)

            db.session.commit()
            if tab_result["stopped_by_existing"]:
                break

            end_time = (response.get("resp_data") or {}).get("next_end_time") or self._oldest_topic_time(topics)
            tab_result["last_end_time"] = end_time
            if not end_time:
                break

        self._upsert_cursor(group_id, tab, newest_topic, tab_result)
        db.session.commit()
        return tab_result

    def _find_existing_downloaded_attachment(self, record):
        conditions = []
        if record.file_id:
            conditions.append(ZsxqAttachment.file_id == record.file_id)
        if record.attachment_key:
            conditions.append(ZsxqAttachment.attachment_key == record.attachment_key)
        if record.name:
            conditions.append(and_(
                ZsxqAttachment.topic_id == record.topic_id,
                ZsxqAttachment.name == record.name,
            ))
        if not conditions:
            return None

        for same_tab_only in (True, False):
            query = ZsxqAttachment.query.filter(
                ZsxqAttachment.group_id == record.group_id,
                ZsxqAttachment.download_status == "downloaded",
                ZsxqAttachment.local_path.isnot(None),
                or_(*conditions),
            )
            if same_tab_only:
                query = query.filter(ZsxqAttachment.tab_id == record.tab_id)
            if record.id:
                query = query.filter(ZsxqAttachment.id != record.id)

            for candidate in query.order_by(ZsxqAttachment.updated_at.desc()).limit(10).all():
                if self._download_marker_exists(candidate.local_path):
                    return candidate
        return None

    def _commit_running_progress(self, run, tab, topic_id, tab_result):
        run.updated_at = datetime.utcnow()
        run.result = {
            "running": True,
            "current_tab": self._public_tab(tab),
            "current_topic_id": topic_id,
            "partial": dict(tab_result),
        }
        db.session.commit()

    def _mark_stale_runs_failed(self, group_id=None, stale_minutes=120):
        cutoff = datetime.utcnow() - timedelta(minutes=stale_minutes)
        query = ZsxqSyncRun.query.filter(
            ZsxqSyncRun.status == "running",
            ZsxqSyncRun.updated_at < cutoff,
        )
        if group_id:
            query = query.filter(ZsxqSyncRun.group_id == str(group_id))

        stale_runs = query.limit(20).all()
        if not stale_runs:
            return

        now = datetime.utcnow()
        for run in stale_runs:
            result = dict(run.result) if isinstance(run.result, dict) else {}
            result["interrupted"] = True
            result["queued"] = False
            result["running"] = False
            run.status = "failed"
            run.completed_at = now
            run.error_message = "同步请求中断：后端重启、连接断开或长时间无进度，已自动标记失败。"
            run.result = result
        db.session.commit()

    def _create_topic_record(self, group_id, group_name, tab, topic, run):
        topic_id = self._topic_id(topic)
        raw_path = self._write_topic_json(group_id, tab, topic_id, topic)
        meta = self._topic_meta(topic)
        return ZsxqTopic(
            group_id=group_id,
            group_name=group_name,
            tab_id=tab["id"],
            tab_label=tab["label"],
            topic_id=topic_id,
            topic_uid=str(topic.get("topic_uid") or ""),
            title=meta["title"],
            author_name=meta["author_name"],
            content=meta["content"],
            create_time=meta["create_time"],
            raw_json_path=raw_path,
            raw_payload=topic,
            first_sync_run_id=run.id,
            last_sync_run_id=run.id,
        )

    def _update_existing_topic(self, record, topic, tab, run):
        meta = self._topic_meta(topic)
        changed = False
        updates = {
            "tab_id": tab["id"],
            "tab_label": tab["label"],
            "title": meta["title"],
            "author_name": meta["author_name"],
            "content": meta["content"],
            "create_time": meta["create_time"],
            "raw_payload": topic,
            "last_sync_run_id": run.id,
        }
        for key, value in updates.items():
            if getattr(record, key) != value:
                setattr(record, key, value)
                changed = True
        if changed:
            record.raw_json_path = self._write_topic_json(record.group_id, tab, record.topic_id, topic)
        return changed

    def _upsert_attachments(self, group_id, tab, topic):
        topic_id = self._topic_id(topic)
        attachments = []
        for file_item in self._topic_files(topic):
            attachments.append(("file", file_item))
        for image_item in self._topic_images(topic):
            attachments.append(("image", image_item))

        stats = {
            "attachments_seen": 0,
            "attachments_downloaded": 0,
            "attachments_failed": 0,
            "attachments_skipped": 0,
        }
        for index, (attachment_type, payload) in enumerate(attachments):
            key = self._attachment_key(payload, index, attachment_type)
            record = ZsxqAttachment.query.filter_by(
                group_id=group_id,
                tab_id=tab["id"],
                topic_id=topic_id,
                attachment_key=key,
            ).first()
            values = {
                "group_id": group_id,
                "tab_id": tab["id"],
                "topic_id": topic_id,
                "attachment_key": key,
                "attachment_type": attachment_type,
                "name": self._attachment_name(payload, attachment_type),
                "file_id": self._attachment_file_id(payload),
                "download_count": self._download_count(payload),
                "raw_payload": payload,
            }
            if record:
                for field, value in values.items():
                    setattr(record, field, value)
            else:
                db.session.add(ZsxqAttachment(**values))
            stats["attachments_seen"] += 1
        return stats

    def _download_topic_attachments(self, cookie, group_id, tab, topic, download_file_types=None, max_download_files=None):
        topic_id = self._topic_id(topic)
        records = ZsxqAttachment.query.filter_by(
            group_id=group_id,
            tab_id=tab["id"],
            topic_id=topic_id,
        ).all()
        stats = {
            "attachments_seen": 0,
            "attachments_downloaded": 0,
            "attachments_failed": 0,
            "attachments_skipped": 0,
            "downloaded_files": [],
            "downloaded_pdf_files": [],
            "stopped_by_download_limit": False,
            "download_limit_reached": False,
        }
        remaining_downloads = int(max_download_files) if max_download_files else None

        for record in records:
            payload = record.raw_payload or {}
            if not self._should_download_attachment(record, download_file_types):
                if record.download_status == "pending":
                    record.download_status = "skipped"
                    record.error_message = f"非目标类型附件，跳过自动下载: {self._attachment_file_type(record)}"
                    stats["attachments_skipped"] += 1
                continue
            if self._download_record_is_reusable(record):
                record.download_status = "downloaded"
                record.error_message = None
                stats["attachments_skipped"] += 1
                continue
            existing_download = self._find_existing_downloaded_attachment(record)
            if existing_download:
                record.local_path = existing_download.local_path
                record.download_status = "downloaded"
                record.error_message = None
                stats["attachments_skipped"] += 1
                continue
            if remaining_downloads is not None and remaining_downloads <= 0:
                stats["stopped_by_download_limit"] = True
                stats["download_limit_reached"] = True
                break
            self._attachment_download_pause()
            attempted_download = False
            try:
                attempted_download = True
                local_path = self._download_attachment_file(cookie, group_id, tab, topic_id, payload, record.name)
                record.local_path = local_path
                record.download_status = "downloaded"
                record.error_message = None
                stats["attachments_downloaded"] += 1
                entry = self._downloaded_file_entry(record, local_path)
                if entry:
                    self._append_unique_file_entry(stats["downloaded_files"], entry)
                    if entry["path"].lower().endswith(".pdf"):
                        self._append_unique_file_entry(stats["downloaded_pdf_files"], entry)
            except Exception as exc:
                record.download_status = "failed"
                record.error_message = str(exc)[:1000]
                stats["attachments_failed"] += 1
                logger.warning(
                    "[ZSXQ_SYNC] attachment download failed | group_id=%s | tab=%s | topic_id=%s | name=%s | error=%s",
                    group_id,
                    tab["id"],
                    topic_id,
                    record.name,
                    exc,
                )
            finally:
                if attempted_download and remaining_downloads is not None:
                    remaining_downloads = max(0, remaining_downloads - 1)
                db.session.flush()
        return stats

    def _download_attachment_file(self, cookie, group_id, tab, topic_id, payload, name):
        filename = self._safe_filename(name or self._attachment_name(payload, "file"))
        target_dir = self._tab_directory(group_id, tab) / "files"
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = self._unique_path(target_dir / filename)

        response = zsxq_service.fetch_topic_file(cookie, topic_id, payload)
        try:
            with target_path.open("wb") as output:
                for chunk in response.iter_content(chunk_size=1024 * 256):
                    if chunk:
                        output.write(chunk)
        finally:
            response.close()

        if target_path.stat().st_size <= 0:
            target_path.unlink(missing_ok=True)
            raise ValueError("下载结果为空文件")
        if filename.lower().endswith(".pdf"):
            with target_path.open("rb") as downloaded:
                if downloaded.read(5) != b"%PDF-":
                    target_path.unlink(missing_ok=True)
                    raise ValueError("下载结果不是有效 PDF 文件")
        return self._relative_to_project(target_path)

    def _merge_attachment_stats(self, target, stats):
        for key in ("attachments_seen", "attachments_downloaded", "attachments_failed", "attachments_skipped"):
            target[key] = target.get(key, 0) + int((stats or {}).get(key, 0) or 0)
        self._merge_downloaded_file_entries(target, stats)

    def _merge_downloaded_file_entries(self, target, stats):
        if not stats:
            return
        for key in ("downloaded_files", "downloaded_pdf_files"):
            target.setdefault(key, [])
            for item in stats.get(key) or []:
                self._append_unique_file_entry(target[key], item)

    def _append_unique_file_entry(self, items, item):
        if not isinstance(item, dict):
            return
        path = item.get("path")
        if not path:
            return
        if any(existing.get("path") == path for existing in items if isinstance(existing, dict)):
            return
        items.append(item)

    def _downloaded_file_entry(self, record, local_path):
        path = self._absolute_local_path(local_path)
        if not path or not path.exists() or not path.is_file():
            return None
        return {
            "path": str(path),
            "name": record.name or path.name,
            "topic_id": record.topic_id,
            "tab_id": record.tab_id,
            "attachment_id": record.id,
        }

    def _upsert_cursor(self, group_id, tab, newest_topic, tab_result):
        cursor = ZsxqSyncCursor.query.filter_by(group_id=group_id, tab_id=tab["id"]).first()
        if not cursor:
            cursor = ZsxqSyncCursor(group_id=group_id, tab_id=tab["id"])
            db.session.add(cursor)

        cursor.tab_label = tab["label"]
        if newest_topic:
            cursor.newest_topic_id = self._topic_id(newest_topic)
            cursor.newest_create_time = self._topic_meta(newest_topic)["create_time"]
        cursor.last_end_time = tab_result.get("last_end_time")
        cursor.total_topics = ZsxqTopic.query.filter_by(group_id=group_id, tab_id=tab["id"]).count()

    def _write_topic_json(self, group_id, tab, topic_id, topic):
        directory = self._tab_directory(group_id, tab)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{self._safe_path_part(topic_id)}.json"
        path.write_text(json.dumps(topic, ensure_ascii=False, indent=2), encoding="utf-8")
        return self._relative_to_project(path)

    def _normalize_tabs(self, tabs):
        output = []
        for item in tabs or []:
            if not isinstance(item, dict):
                continue
            tab_id = str(item.get("id") or item.get("tab_id") or "").strip()
            label = str(item.get("label") or item.get("tab_label") or tab_id or "未命名").strip()
            if not tab_id:
                continue
            output.append({
                "id": tab_id,
                "label": label,
                "scope": item.get("scope") or "all",
                "hashtagId": item.get("hashtagId") or item.get("hashtag_id"),
                "extraParams": item.get("extraParams") if isinstance(item.get("extraParams"), dict) else {},
            })
        return output

    def _public_tab(self, tab):
        return {
            "id": tab["id"],
            "label": tab["label"],
            "scope": tab.get("scope") or "all",
            "hashtagId": tab.get("hashtagId"),
        }

    def _tab_extra_params(self, tab):
        params = dict(tab.get("extraParams") or {})
        if tab.get("hashtagId"):
            params["hashtag_id"] = tab["hashtagId"]
        return params

    def _get_topics_with_retry(
        self,
        cookie,
        group_id,
        count,
        scope,
        end_time,
        extra_params,
        tab_id,
        tab_label,
        page,
    ):
        attempts = self._sync_retry_attempts()
        for attempt in range(1, attempts + 1):
            try:
                response = zsxq_service.get_topics(
                    cookie=cookie,
                    group_id=group_id,
                    count=count,
                    scope=scope,
                    end_time=end_time,
                    extra_params=extra_params,
                )
                self._assert_upstream_success(response)
                if attempt > 1:
                    logger.info(
                        "[ZSXQ_SYNC] upstream retry recovered | group_id=%s tab_id=%s tab=%s page=%s attempt=%s/%s",
                        group_id,
                        tab_id,
                        tab_label,
                        page,
                        attempt,
                        attempts,
                    )
                return response
            except (ZsxqTransientUpstreamError, requests.RequestException) as exc:
                if attempt >= attempts:
                    logger.warning(
                        "[ZSXQ_SYNC] upstream retry exhausted | group_id=%s tab_id=%s tab=%s page=%s attempts=%s error=%s",
                        group_id,
                        tab_id,
                        tab_label,
                        page,
                        attempts,
                        exc,
                    )
                    raise

                delay = self._sync_retry_delay_seconds(attempt)
                logger.warning(
                    "[ZSXQ_SYNC] upstream transient failure | group_id=%s tab_id=%s tab=%s page=%s attempt=%s/%s retry_in=%.1fs error=%s",
                    group_id,
                    tab_id,
                    tab_label,
                    page,
                    attempt,
                    attempts,
                    delay,
                    exc,
                )
                time.sleep(delay)

        raise RuntimeError("ZSXQ_SYNC_RETRY_LOOP_UNREACHABLE")

    def _sync_retry_attempts(self):
        config_value = current_app.config.get("ZSXQ_SYNC_RETRY_ATTEMPTS") if has_app_context() else None
        value = (
            config_value
            or os.environ.get("ZSXQ_SYNC_RETRY_ATTEMPTS")
            or 3
        )
        try:
            return min(max(int(value), 1), 8)
        except (TypeError, ValueError):
            return 3

    def _sync_retry_delay_seconds(self, attempt):
        config_value = current_app.config.get("ZSXQ_SYNC_RETRY_BASE_DELAY_SECONDS") if has_app_context() else None
        value = (
            config_value
            or os.environ.get("ZSXQ_SYNC_RETRY_BASE_DELAY_SECONDS")
            or 5
        )
        try:
            base_delay = max(float(value), 0.1)
        except (TypeError, ValueError):
            base_delay = 5.0
        return min(base_delay * (2 ** max(attempt - 1, 0)), 60.0) + random.uniform(0, 1.5)

    def _assert_upstream_success(self, result):
        if not isinstance(result, dict) or result.get("succeeded") is not False:
            return
        code = result.get("code") or "UNKNOWN"
        message = result.get("error") or result.get("message") or result.get("resp_data") or result
        if str(code) in TRANSIENT_UPSTREAM_CODES:
            raise ZsxqTransientUpstreamError(code, message, result)
        raise ValueError(f"知识星球接口返回失败 code={code}: {message}")

    def _extract_topics(self, result):
        resp_data = result.get("resp_data") or {}
        direct = [
            resp_data.get("topics"),
            resp_data.get("sticky_topics"),
            resp_data.get("pin_topics"),
            resp_data.get("pinned_topics"),
            resp_data.get("top_topics"),
            resp_data.get("items"),
            resp_data.get("list"),
        ]
        topics = []
        for items in direct:
            if isinstance(items, list):
                topics.extend(item for item in items if self._is_topic_like(item))
        if not topics:
            self._collect_topic_arrays(resp_data, topics)

        deduped = []
        seen = set()
        for topic in topics:
            topic_id = self._topic_id(topic)
            if not topic_id or topic_id in seen:
                continue
            seen.add(topic_id)
            deduped.append(topic)
        return deduped

    def _collect_topic_arrays(self, value, output):
        if isinstance(value, list):
            items = [item for item in value if self._is_topic_like(item)]
            if items:
                output.extend(items)
                return
            for item in value:
                self._collect_topic_arrays(item, output)
        elif isinstance(value, dict):
            for child in value.values():
                self._collect_topic_arrays(child, output)

    def _is_topic_like(self, item):
        return isinstance(item, dict) and bool(
            item.get("topic_id")
            or item.get("talk")
            or item.get("article")
            or item.get("question")
            or item.get("create_time")
        )

    def _topic_id(self, topic):
        return str(topic.get("topic_id") or topic.get("id") or topic.get("topic_uid") or "").strip()

    def _topic_meta(self, topic):
        title = (
            topic.get("title")
            or (topic.get("article") or {}).get("title")
            or self._content(topic)[:48]
            or "无标题主题"
        )
        owner = topic.get("owner") or (topic.get("talk") or {}).get("owner") or {}
        return {
            "title": str(title)[:500],
            "author_name": owner.get("name") or "未知作者",
            "content": self._content(topic),
            "create_time": topic.get("create_time") or (topic.get("talk") or {}).get("create_time") or "",
        }

    def _content(self, topic):
        text = (
            (topic.get("talk") or {}).get("text")
            or (topic.get("article") or {}).get("article_content")
            or (topic.get("article") or {}).get("title")
            or (topic.get("question") or {}).get("text")
            or ""
        )
        return self._normalize_topic_text(text)

    def _normalize_topic_text(self, text):
        def replace_hashtag(match):
            title = re.search(r'title=["\']([^"\']+)["\']', match.group(0))
            if not title:
                return ""
            from urllib.parse import unquote
            return unquote(title.group(1))

        return re.sub(r"<e\s+[^>]*type=[\"']hashtag[\"'][^>]*\/?>", replace_hashtag, str(text or "")).strip()

    def _topic_files(self, topic):
        return topic.get("files") or (topic.get("talk") or {}).get("files") or (topic.get("article") or {}).get("files") or []

    def _topic_images(self, topic):
        return topic.get("images") or (topic.get("talk") or {}).get("images") or (topic.get("article") or {}).get("images") or []

    def _attachment_key(self, payload, index, attachment_type):
        return str(
            payload.get("file_id")
            or payload.get("image_id")
            or payload.get("id")
            or payload.get("hash_id")
            or payload.get("name")
            or payload.get("url")
            or f"{attachment_type}-{index}"
        )[:255]

    def _attachment_name(self, payload, attachment_type):
        if attachment_type == "image":
            return payload.get("name") or payload.get("url") or "图片"
        return payload.get("name") or payload.get("title") or payload.get("file_name") or "附件"

    def _attachment_file_id(self, payload):
        value = payload.get("file_id") or payload.get("image_id") or payload.get("id") or payload.get("hash_id")
        return str(value)[:128] if value is not None else None

    def _download_count(self, payload):
        candidates = [
            payload.get("download_count"),
            payload.get("downloads_count"),
            payload.get("downloadCount"),
            payload.get("downloadsCount"),
            (payload.get("statistics") or {}).get("download_count") if isinstance(payload.get("statistics"), dict) else None,
        ]
        for item in candidates:
            if item not in (None, ""):
                try:
                    return int(item)
                except (TypeError, ValueError):
                    return None
        return None

    def _oldest_topic_time(self, topics):
        times = [self._topic_meta(topic)["create_time"] for topic in topics]
        times = [item for item in times if item]
        return min(times) if times else None

    def _safe_path_part(self, value):
        value = re.sub(r'[\\/:*?"<>|\r\n\s]+', "_", str(value or "item")).strip("_")
        return value[:120] or "item"

    def _safe_filename(self, value):
        value = re.sub(r'[\\/:*?"<>|\r\n]+', "_", str(value or "zsxq_file.pdf")).strip()
        return value[:180] or "zsxq_file.pdf"

    def _project_root(self):
        return Path(current_app.root_path).parents[1]

    def _data_root(self):
        return Path(current_app.config.get("ZSXQ_DATA_DIR") or self._project_root() / "data" / "zsxq")

    def _tab_directory(self, group_id, tab):
        tab_slug = self._safe_path_part(f"{tab['label']}-{tab['id']}")
        return self._data_root() / str(group_id) / tab_slug

    def _relative_to_project(self, path):
        try:
            return str(path.relative_to(self._project_root()))
        except ValueError:
            return str(path)

    def _path_exists(self, local_path):
        try:
            path = Path(local_path)
            if not path.is_absolute():
                path = self._project_root() / path
            return path.exists() and path.stat().st_size > 0
        except (TypeError, OSError, ValueError):
            return False

    def _download_record_is_reusable(self, record):
        return (
            record.download_status == "downloaded"
            and self._download_marker_exists(record.local_path)
        )

    def _download_marker_exists(self, local_path):
        if not local_path:
            return False
        if str(local_path).startswith("browser-download:"):
            return True
        return self._path_exists(local_path)

    def _unique_path(self, path):
        if not path.exists():
            return path
        stem = path.stem
        suffix = path.suffix
        parent = path.parent
        for index in range(2, 1000):
            candidate = parent / f"{stem}_{index}{suffix}"
            if not candidate.exists():
                return candidate
        raise ValueError(f"无法生成唯一文件名: {path.name}")

    def _normalize_download_file_types(self, download_file_types, download_pdf_only=True):
        if download_file_types is None:
            return {"pdf"} if download_pdf_only else {"all"}
        if isinstance(download_file_types, str):
            raw_items = [download_file_types]
        elif isinstance(download_file_types, (list, tuple, set)):
            raw_items = list(download_file_types)
        else:
            raw_items = []

        allowed = {"pdf", "mp3", "all"}
        normalized = {
            str(item).lower().lstrip(".")
            for item in raw_items
            if str(item).strip()
        }
        normalized = {item for item in normalized if item in allowed}
        return normalized or ({"pdf"} if download_pdf_only else {"all"})

    def _normalize_download_file_types_by_tab(self, value):
        if not isinstance(value, dict):
            return {}
        result = {}
        for tab_id, file_types in value.items():
            key = str(tab_id or "").strip()
            if not key:
                continue
            result[key] = self._normalize_download_file_types(file_types, download_pdf_only=True)
        return result

    def _normalize_max_download_files(self, max_download_files):
        if max_download_files in (None, "", 0, "0"):
            return None
        try:
            return min(max(int(max_download_files), 1), 100)
        except (TypeError, ValueError):
            raise ValueError("max_download_files 必须是正整数")

    def _should_download_attachment(self, record, download_file_types):
        if not download_file_types or "all" in download_file_types:
            return record.attachment_type == "file"
        return self._attachment_file_type(record) in download_file_types

    def _attachment_file_type(self, record):
        name = str(record.name or "")
        payload = record.raw_payload or {}
        content_type = str(payload.get("content_type") or payload.get("mime_type") or payload.get("mime") or "")
        lowered_name = name.lower()
        lowered_type = content_type.lower()
        if record.attachment_type != "file":
            return record.attachment_type or "unknown"
        if lowered_name.endswith(".pdf") or "pdf" in lowered_type:
            return "pdf"
        if lowered_name.endswith(".mp3") or "mpeg" in lowered_type or "mp3" in lowered_type:
            return "mp3"
        match = re.search(r"\.([a-z0-9]{2,5})(?:$|\?)", lowered_name)
        return match.group(1) if match else "file"

    def _is_pdf_attachment(self, record):
        return self._attachment_file_type(record) == "pdf"

    def _human_pause(self, min_seconds=4.5, max_seconds=7.0):
        seconds = min_seconds + random.random() * (max_seconds - min_seconds)
        logger.info("[ZSXQ_SYNC] pacing wait %.1fs", seconds)
        time.sleep(seconds)

    def _attachment_download_pause(self):
        if self._last_pdf_download_at:
            self._human_pause(9, 12)
        self._last_pdf_download_at = time.time()


zsxq_sync_service = ZsxqSyncService()
