import hashlib
import json
import logging
import os
import random
import re
import time
from copy import deepcopy
from datetime import datetime, timedelta
from html import unescape
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote

import requests
from flask import current_app, has_app_context
from sqlalchemy import and_, bindparam, cast, column, inspect, or_, text, update, values
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import load_only

from models import db
from models.task import PublicationTask
from models.zsxq_sync import (
    ZsxqAttachment,
    ZsxqSyncCursor,
    ZsxqSyncRun,
    ZsxqSyncRunDownload,
    ZsxqTopic,
    ZsxqTopicComment,
    ZsxqTopicFavorite,
    jsonb_payload_md5,
)
from services.runtime_guard import (
    end_db_transaction_before_external_io,
    is_retryable_db_error,
    maybe_guarded_apply_async,
)
from services.zsxq_service import zsxq_service
from services.zsxq_yiren_award_cleanup_service import is_yiren_award_comment


logger = logging.getLogger(__name__)

STALE_SYNC_RUN_STATUSES = ("queued", "running", "retrying")
STALE_SYNC_RUN_ERROR = "同步任务长时间未被 worker 领取或执行无进度，可能因后端重启、连接断开或队列消息丢失，已自动标记失败。"


TRANSIENT_UPSTREAM_CODES = {"1059"}
ATTACHMENT_DOWNLOADS_ENABLED_ENV = "ZSXQ_SYNC_ATTACHMENT_DOWNLOADS_ENABLED"
ATTACHMENT_DOWNLOAD_LIMIT_ENV = "ZSXQ_SYNC_ATTACHMENT_DOWNLOAD_LIMIT"
ATTACHMENT_PUBLISH_FALLBACK_ENV = "ZSXQ_ATTACHMENT_PUBLISH_FALLBACK_ENABLED"
MANUAL_ATTACHMENT_PLAN_LIMIT_ENV = "ZSXQ_MANUAL_DOWNLOAD_PLAN_LIMIT"
MANUAL_ATTACHMENT_PLAN_HARD_LIMIT_ENV = "ZSXQ_MANUAL_DOWNLOAD_PLAN_HARD_LIMIT"
PROGRESS_RESULT_PERSIST_SECONDS_ENV = "ZSXQ_PROGRESS_RESULT_PERSIST_SECONDS"
DEFAULT_SAFE_ATTACHMENT_DOWNLOAD_LIMIT = 1
DEFAULT_THRESHOLD_ATTACHMENT_DOWNLOAD_LIMIT = 40
DEFAULT_MANUAL_ATTACHMENT_PLAN_LIMIT = 20
DEFAULT_MANUAL_ATTACHMENT_PLAN_HARD_LIMIT = 50
DEFAULT_PROGRESS_RESULT_PERSIST_SECONDS = 30.0
TOPIC_PAGE_SIZE_HINT = 20
MAX_COMMENTS_PER_TOPIC_ENV = "ZSXQ_SYNC_MAX_COMMENTS_PER_TOPIC"
DEFAULT_MAX_COMMENTS_PER_TOPIC = 200
ATTACHMENT_STATUS_CACHE_TTL_SECONDS = 20
PUBLISH_STATUS_CONTEXT_CACHE_TTL_SECONDS = 60
MAX_ATTACHMENT_STATUS_CACHE_ENTRIES = 128
PUBLISH_TASK_CONTEXT_COLUMNS = (
    PublicationTask.id,
    PublicationTask.status,
    PublicationTask.feature,
    PublicationTask.params,
    PublicationTask.result,
    PublicationTask.source_task_id,
    PublicationTask.completed_at,
    PublicationTask.updated_at,
)
CLEAN_TASK_CONTEXT_COLUMNS = (
    PublicationTask.id,
    PublicationTask.params,
    PublicationTask.result,
)
REQUIRED_ATTACHMENT_COLUMNS = {
    "error_message",
    "publish_status",
    "publish_task_id",
    "publish_source_task_id",
    "publish_topic_id",
    "publish_submitted_count",
    "publish_verified_count",
    "publish_failed_count",
    "publish_updated_at",
}
REQUIRED_SYNC_RUN_PROJECTION_COLUMNS = {
    "system_batch_id",
    "system_attempt_id",
    "system_round",
    "system_max_rounds",
    "system_trigger",
    "system_queued_at",
    "system_max_download_files",
    "topics_fetched",
    "topics_new",
    "topics_updated",
    "topics_existing",
    "attachments_seen",
    "attachments_downloaded",
    "attachments_failed",
    "attachments_skipped",
    "download_limit_reached",
    "download_ledger_complete",
}
ATTACHMENT_AUDIT_COUNTERS = (
    "attachments_seen",
    "attachments_downloaded",
    "attachments_failed",
    "attachments_skipped",
    "attachments_below_threshold",
    "attachments_missing_download_count",
    "attachments_non_target_skipped",
    "attachments_already_downloaded_skipped",
    "attachments_reused_local",
    "attachments_reused_existing",
    "attachments_limited_skipped",
)

AUTO_DISPATCH_ATTACHMENT_COLUMNS = (
    ZsxqAttachment.id,
    ZsxqAttachment.group_id,
    ZsxqAttachment.tab_id,
    ZsxqAttachment.topic_id,
    ZsxqAttachment.name,
    ZsxqAttachment.file_ext,
    ZsxqAttachment.local_path,
    ZsxqAttachment.download_count,
    ZsxqAttachment.download_status,
)

ATTACHMENT_DOWNLOAD_SNAPSHOT_COLUMNS = (
    ZsxqAttachment.id,
    ZsxqAttachment.group_id,
    ZsxqAttachment.tab_id,
    ZsxqAttachment.topic_id,
    ZsxqAttachment.attachment_key,
    ZsxqAttachment.attachment_type,
    ZsxqAttachment.name,
    ZsxqAttachment.file_id,
    ZsxqAttachment.download_count,
    ZsxqAttachment.local_path,
    ZsxqAttachment.file_ext,
    ZsxqAttachment.download_status,
    ZsxqAttachment.error_message,
)

TOPIC_UPDATE_SNAPSHOT_FIELDS = (
    "id",
    "group_id",
    "group_name",
    "tab_id",
    "tab_label",
    "topic_id",
    "topic_uid",
    "raw_json_path",
    "raw_payload_hash",
    "first_sync_run_id",
    "first_synced_at",
)
TOPIC_STRUCTURED_UPDATE_FIELDS = (
    "tab_id",
    "tab_label",
    "title",
    "author_name",
    "content",
    "topic_type",
    "question_text",
    "answer_text",
    "article_title",
    "article_excerpt",
    "files_count",
    "images_count",
    "create_time",
)
AUTO_VIDEO_DISPATCH_SOURCE = "zsxq_auto_video_dispatch"
AUTO_SCRIPT_MATERIAL_DISPATCH_SOURCE = "zsxq_auto_script_material_dispatch"
DEFAULT_AUTO_VIDEO_PDF_THRESHOLD = 35
DEFAULT_AUTO_VIDEO_MP3_THRESHOLD = 25
DEFAULT_AUTO_SCRIPT_MATERIAL_PDF_THRESHOLD = 40
AUTO_VIDEO_AUDIO_EXTENSIONS = {"mp3", "m4a", "wav", "aac", "flac", "ogg", "opus"}
AUTO_VIDEO_REPORT_AUDIO_FEATURES = ("report_pdf_audio_workflow", "report_audio_workflow")
SCRIPT_MATERIAL_WORKFLOW_FEATURE = "report_script_material_workflow"
AUTO_SCRIPT_MATERIAL_FEATURES = (*AUTO_VIDEO_REPORT_AUDIO_FEATURES, SCRIPT_MATERIAL_WORKFLOW_FEATURE)
GENERIC_AUTO_VIDEO_TOPIC_TITLE_NORMALIZED = {
    "pdf",
    "外资",
    "外资研报",
    "研报",
    "调研",
    "调研纪要",
    "纪要",
}


def _is_generic_auto_video_topic_title(value):
    text = str(value or "").strip()
    if not text:
        return False
    compact = re.sub(r"[\s#＃:：|｜/_\\-]+", "", text).lower()
    if compact in GENERIC_AUTO_VIDEO_TOPIC_TITLE_NORMALIZED:
        return True
    if text.startswith(("#", "＃")) and len(compact) <= 6 and not re.search(r"\d", compact):
        return True
    return False


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
        self._attachment_status_cache = {}
        self._publish_status_context_cache = {"expires_at": 0, "items": []}

    def ensure_schema(self):
        if self._schema_checked:
            return
        required_tables = {
            "zsxq_sync_runs",
            "zsxq_sync_run_downloads",
            "zsxq_topics",
            "zsxq_topic_comments",
            "zsxq_attachments",
            "zsxq_sync_cursors",
        }
        if db.engine.dialect.name == "postgresql":
            existing_tables, table_columns, has_download_ledger_privilege = (
                self._postgres_schema_contract()
            )
        else:
            inspector = inspect(db.session.connection())
            existing_tables = self._table_names(inspector)
            table_columns = {}
            has_download_ledger_privilege = True
        missing_tables = sorted(required_tables - existing_tables)
        if missing_tables:
            raise ValueError(
                "知识星球同步表尚未初始化，请先用有建表权限的数据库账号执行 "
                "docs/sql/zsxq_sync_tables.sql；缺失表："
                + ", ".join(missing_tables)
            )
        if db.engine.dialect.name != "postgresql":
            table_columns = {
                "zsxq_sync_runs": {
                    item["name"] for item in self._get_columns(inspector, "zsxq_sync_runs")
                },
                "zsxq_attachments": {
                    item["name"] for item in self._get_columns(inspector, "zsxq_attachments")
                },
            }
        if db.engine.dialect.name == "postgresql":
            if not has_download_ledger_privilege:
                raise ValueError(
                    "当前数据库账号没有 public.zsxq_sync_run_downloads 的读写权限，请用管理员执行 "
                    "docs/sql/content_pipeline_run_download_projection_dbeaver_20260718.sql。"
                )
        sync_run_columns = table_columns.get("zsxq_sync_runs", set())
        missing_sync_run_columns = sorted(REQUIRED_SYNC_RUN_PROJECTION_COLUMNS - sync_run_columns)
        if missing_sync_run_columns:
            migration_script = (
                "docs/sql/content_pipeline_run_download_projection_dbeaver_20260718.sql"
                if set(missing_sync_run_columns) == {"download_ledger_complete"}
                else "docs/sql/zsxq_sync_runs_projection_dbeaver_20260617.sql"
            )
            raise ValueError(
                "知识星球同步运行表结构需要更新，请用 postgres/admin 在 DBeaver 执行 "
                f"{migration_script}；缺失列："
                + ", ".join(missing_sync_run_columns)
            )
        attachment_columns = table_columns.get("zsxq_attachments", set())
        missing_attachment_columns = sorted(REQUIRED_ATTACHMENT_COLUMNS - attachment_columns)
        if missing_attachment_columns:
            raise ValueError(
                "知识星球同步表结构需要更新，请执行 "
                "docs/sql/zsxq_attachment_publish_status_columns.sql；缺失列："
                + ", ".join(missing_attachment_columns)
            )
        self._schema_checked = True

    def _postgres_schema_contract(self):
        row = db.session.execute(text("""
            WITH required(table_name) AS (
                VALUES
                    ('zsxq_sync_runs'),
                    ('zsxq_sync_run_downloads'),
                    ('zsxq_topics'),
                    ('zsxq_topic_comments'),
                    ('zsxq_attachments'),
                    ('zsxq_sync_cursors')
            ), table_state AS (
                SELECT
                    required.table_name,
                    to_regclass('public.' || required.table_name) IS NOT NULL AS table_exists,
                    COALESCE((
                        SELECT jsonb_agg(columns.column_name ORDER BY columns.ordinal_position)
                        FROM information_schema.columns AS columns
                        WHERE columns.table_schema = 'public'
                          AND columns.table_name = required.table_name
                    ), '[]'::jsonb) AS column_names
                FROM required
            )
            SELECT
                COALESCE(
                    jsonb_agg(table_name ORDER BY table_name) FILTER (WHERE table_exists),
                    '[]'::jsonb
                ) AS existing_tables,
                COALESCE(
                    jsonb_object_agg(table_name, column_names),
                    '{}'::jsonb
                ) AS table_columns,
                CASE
                    WHEN to_regclass('public.zsxq_sync_run_downloads') IS NULL THEN FALSE
                    ELSE has_table_privilege(
                        current_user,
                        'public.zsxq_sync_run_downloads',
                        'SELECT, INSERT, UPDATE, DELETE'
                    )
                END AS has_download_ledger_privilege
            FROM table_state
        """)).one()
        table_columns = {
            str(table_name): set(column_names or ())
            for table_name, column_names in (row.table_columns or {}).items()
        }
        return (
            set(row.existing_tables or ()),
            table_columns,
            bool(row.has_download_ledger_privilege),
        )

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
        download_attachments=False,
        download_pdf_only=True,
        download_file_types=None,
        download_file_types_by_tab=None,
        max_download_files=None,
        allow_attachment_downloads=False,
        mode="manual",
        run_id=None,
        sync_meta=None,
        manual_attachment_plan=None,
        attachment_download_rules=None,
        auto_video_dispatch=None,
        auto_script_material_dispatch=None,
        workflow_run_id=None,
    ):
        self.ensure_schema()
        tabs = self._normalize_tabs(tabs)
        if not tabs:
            raise ValueError("tabs 不能为空")

        group_id = str(group_id)
        mode = self._normalize_mode(mode)
        count = min(max(int(count or 20), 1), 50)
        requested_max_pages = min(max(int(max_pages or 1), 1), 20)
        max_pages = self._effective_topic_max_pages(count, requested_max_pages)
        download_file_types = self._normalize_download_file_types(download_file_types, download_pdf_only)
        download_file_types_by_tab = self._normalize_download_file_types_by_tab(download_file_types_by_tab)
        manual_plan = self._normalize_manual_attachment_plan(
            manual_attachment_plan,
            group_id=group_id,
            tabs=tabs,
        )
        sync_meta = sync_meta if isinstance(sync_meta, dict) else {}
        download_rules = self._normalize_attachment_download_rules(
            attachment_download_rules,
            tabs=tabs,
        )
        auto_video_dispatch = self._dispatch_config_from_sync_meta(
            auto_video_dispatch,
            sync_meta,
            "auto_video_dispatch",
        )
        auto_script_material_dispatch = self._dispatch_config_from_sync_meta(
            auto_script_material_dispatch,
            sync_meta,
            "auto_script_material_dispatch",
        )
        auto_video_dispatch = self._normalize_auto_video_dispatch(auto_video_dispatch)
        auto_script_material_dispatch = self._normalize_auto_script_material_dispatch(auto_script_material_dispatch)
        if manual_plan["items"] and max_download_files in (None, "", 0, "0"):
            max_download_files = len(manual_plan["items"])
        download_policy = self._attachment_download_policy(
            requested=download_attachments,
            explicit_allow=allow_attachment_downloads,
            max_download_files=max_download_files,
            manual_plan_count=len(manual_plan["items"]),
            threshold_rule_count=len(download_rules.get("rules") or []) if download_rules.get("enabled") else 0,
        )
        download_attachments = download_policy["enabled"]
        max_download_files = download_policy["max_download_files"]
        running_result = {"running": True}
        if sync_meta:
            running_result["system_sync"] = sync_meta
        if manual_plan["items"]:
            running_result["manual_attachment_plan"] = self._public_manual_attachment_plan(manual_plan)
        if download_rules.get("enabled"):
            running_result["attachment_download_rules"] = self._public_attachment_download_rules(download_rules)
        if auto_video_dispatch.get("enabled"):
            running_result["auto_video_dispatch"] = {
                "status": "pending",
                "config": auto_video_dispatch,
            }
        if auto_script_material_dispatch.get("enabled"):
            running_result["auto_script_material_dispatch"] = {
                "status": "pending",
                "config": auto_script_material_dispatch,
            }
        running_result["attachment_download_policy"] = download_policy

        if run_id:
            run = (
                ZsxqSyncRun.query.options(load_only(
                    ZsxqSyncRun.id,
                    ZsxqSyncRun.status,
                    ZsxqSyncRun.started_at,
                    ZsxqSyncRun.download_ledger_complete,
                ))
                .filter(ZsxqSyncRun.id == str(run_id))
                .one_or_none()
            )
            if not run:
                raise ValueError(f"同步任务不存在: {run_id}")
            if run.status == "queued":
                run.started_at = datetime.utcnow()
            run.status = "running"
            run.download_ledger_complete = False
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
                download_ledger_complete=False,
                requested_tabs=[self._public_tab(tab) for tab in tabs],
                result=running_result,
            )
            db.session.add(run)
        db.session.flush()
        run_identity = SimpleNamespace(
            id=str(run.id),
            group_id=group_id,
            group_name=group_name,
        )
        run._zsxq_cached_result = deepcopy(running_result)
        run._zsxq_cached_run_id = run.id
        db.session.commit()
        run._zsxq_result_persisted_monotonic = time.monotonic()

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
            "attachments_below_threshold": 0,
            "attachments_missing_download_count": 0,
            "attachments_non_target_skipped": 0,
            "attachments_already_downloaded_skipped": 0,
            "attachments_reused_local": 0,
            "attachments_reused_existing": 0,
            "attachments_limited_skipped": 0,
            "downloaded_files": [],
            "downloaded_pdf_files": [],
            "target_topics_per_tab": count,
            "max_pages": max_pages,
            "planned_pages_total": len(tabs) * max_pages,
            "pages_fetched": 0,
            "planned_topics_total": len(tabs) * count,
            "max_download_files": max_download_files,
            "attachment_download_policy": download_policy,
            "download_limit_reached": False,
            "tabs": [],
        }
        if sync_meta:
            result["system_sync"] = sync_meta
        if manual_plan["items"]:
            result["manual_attachment_plan"] = self._public_manual_attachment_plan(manual_plan)
        if download_rules.get("enabled"):
            result["attachment_download_rules"] = self._public_attachment_download_rules(download_rules)
        if auto_video_dispatch.get("enabled"):
            result["auto_video_dispatch"] = {
                "status": "pending",
                "config": auto_video_dispatch,
            }
        if auto_script_material_dispatch.get("enabled"):
            result["auto_script_material_dispatch"] = {
                "status": "pending",
                "config": auto_script_material_dispatch,
            }

        try:
            if manual_plan["items"]:
                if not download_attachments:
                    raise ValueError(f"人工计划下载被安全策略拦截: {download_policy.get('reason') or 'disabled'}")
                manual_result = self._sync_manual_attachment_plan(
                    cookie=cookie,
                    group_id=group_id,
                    group_name=group_name,
                    tabs=tabs,
                    run=run,
                    download_file_types=download_file_types,
                    manual_plan=manual_plan,
                    max_download_files=max_download_files,
                )
                result.update({
                    "tabs": manual_result["tabs"],
                    "topics_fetched": manual_result["topics_fetched"],
                    "topics_new": manual_result["topics_new"],
                    "topics_updated": manual_result["topics_updated"],
                    "topics_existing": manual_result["topics_existing"],
                    "attachments_seen": manual_result["attachments_seen"],
                    "attachments_downloaded": manual_result["attachments_downloaded"],
                    "attachments_failed": manual_result["attachments_failed"],
                    "attachments_skipped": manual_result["attachments_skipped"],
                    "attachments_below_threshold": manual_result.get("attachments_below_threshold", 0),
                    "attachments_missing_download_count": manual_result.get("attachments_missing_download_count", 0),
                    "attachments_non_target_skipped": manual_result.get("attachments_non_target_skipped", 0),
                    "attachments_already_downloaded_skipped": manual_result.get("attachments_already_downloaded_skipped", 0),
                    "attachments_reused_local": manual_result.get("attachments_reused_local", 0),
                    "attachments_reused_existing": manual_result.get("attachments_reused_existing", 0),
                    "attachments_limited_skipped": manual_result.get("attachments_limited_skipped", 0),
                    "pages_fetched": manual_result.get("pages_fetched", 0),
                    "download_limit_reached": manual_result["download_limit_reached"],
                    "downloaded_files": manual_result["downloaded_files"],
                    "downloaded_pdf_files": manual_result["downloaded_pdf_files"],
                })
            else:
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
                        attachment_download_rules=download_rules,
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
                    result["attachments_below_threshold"] += tab_result.get("attachments_below_threshold", 0)
                    result["attachments_missing_download_count"] += tab_result.get("attachments_missing_download_count", 0)
                    result["attachments_non_target_skipped"] += tab_result.get("attachments_non_target_skipped", 0)
                    result["attachments_already_downloaded_skipped"] += tab_result.get("attachments_already_downloaded_skipped", 0)
                    result["attachments_reused_local"] += tab_result.get("attachments_reused_local", 0)
                    result["attachments_reused_existing"] += tab_result.get("attachments_reused_existing", 0)
                    result["attachments_limited_skipped"] += tab_result.get("attachments_limited_skipped", 0)
                    result["pages_fetched"] += tab_result.get("pages", 0)
                    self._merge_downloaded_file_entries(result, tab_result)
                    if tab_result.get("download_limit_reached") or tab_result.get("stopped_by_download_limit"):
                        result["download_limit_reached"] = True

            if auto_video_dispatch.get("enabled"):
                result["auto_video_dispatch"] = self._submit_auto_video_dispatch_for_run(
                    run=run_identity,
                    sync_result=result,
                    config=auto_video_dispatch,
                    workflow_run_id=workflow_run_id,
                )
            if auto_script_material_dispatch.get("enabled"):
                result["auto_script_material_dispatch"] = self._submit_auto_script_material_dispatch_for_run(
                    run=run_identity,
                    sync_result=result,
                    config=auto_script_material_dispatch,
                    workflow_run_id=workflow_run_id,
                )

            run.status = "completed"
            run.completed_at = datetime.utcnow()
            run.download_ledger_complete = True
            run.result = result
            db.session.commit()
            return {
                "run": self._sync_run_public_payload(self._cached_sync_run_id(run), result=result),
                "summary": result,
            }
        except Exception as exc:
            db.session.rollback()
            if is_retryable_db_error(exc):
                logger.warning(
                    "[ZSXQ_SYNC] transient db error; keep run retryable | run_id=%s | error=%s",
                    self._cached_sync_run_id(run),
                    exc,
                    exc_info=True,
                )
                raise
            run.status = "failed"
            run.completed_at = datetime.utcnow()
            run.error_message = str(exc)
            run.result = result
            db.session.add(run)
            db.session.commit()
            logger.warning(
                "[ZSXQ_SYNC] sync failed | run_id=%s | error=%s",
                self._cached_sync_run_id(run),
                exc,
                exc_info=True,
            )
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
        db.session.flush()
        payload = run.to_dict()
        db.session.commit()
        return payload

    def _normalize_mode(self, mode):
        value = str(mode or "manual").strip() or "manual"
        return value[:20]

    def list_runs(self, group_id=None, limit=20):
        self.ensure_schema()
        self._mark_stale_runs_failed(group_id=group_id)
        rows = self._sync_run_summary_rows(
            group_id=group_id,
            limit=min(max(int(limit or 20), 1), 100),
        )
        payloads = [self._sync_run_summary_payload_from_row(row) for row in rows]
        self._attach_sync_queue_context(payloads)
        return payloads

    def _attach_sync_queue_context(self, payloads):
        queued_ids = {str(item.get("id")) for item in payloads if item.get("status") == "queued"}
        if not queued_ids:
            return

        pending = (
            db.session.query(
                ZsxqSyncRun.id,
                ZsxqSyncRun.group_id,
                ZsxqSyncRun.group_name,
                ZsxqSyncRun.status,
                ZsxqSyncRun.created_at,
                ZsxqSyncRun.updated_at,
            )
            .filter(ZsxqSyncRun.status.in_(("running", "queued")))
            .order_by(ZsxqSyncRun.created_at.asc())
            .all()
        )
        running = [row for row in pending if row.status == "running"]
        queued = [row for row in pending if row.status == "queued"]
        queued_index = {str(row.id): index for index, row in enumerate(queued)}
        blocker = running[0] if running else None

        for payload in payloads:
            run_id = str(payload.get("id") or "")
            if run_id not in queued_ids or run_id not in queued_index:
                continue
            payload["queue_position"] = queued_index[run_id] + 1
            payload["queue_ahead"] = len(running) + queued_index[run_id]
            if blocker:
                payload["blocking_run"] = {
                    "id": blocker.id,
                    "group_id": blocker.group_id,
                    "group_name": blocker.group_name,
                    "status": blocker.status,
                    "updated_at": blocker.updated_at.isoformat() if blocker.updated_at else None,
                }

    def _sync_run_summary_rows(self, group_id=None, limit=20):
        query = self._sync_run_summary_query()
        if group_id:
            query = query.filter(ZsxqSyncRun.group_id == str(group_id))
        return query.order_by(ZsxqSyncRun.created_at.desc()).limit(limit).all()

    def _sync_run_summary_query(self):
        return db.session.query(
            ZsxqSyncRun.id.label("id"),
            ZsxqSyncRun.group_id.label("group_id"),
            ZsxqSyncRun.group_name.label("group_name"),
            ZsxqSyncRun.tab_id.label("tab_id"),
            ZsxqSyncRun.tab_label.label("tab_label"),
            ZsxqSyncRun.mode.label("mode"),
            ZsxqSyncRun.status.label("status"),
            ZsxqSyncRun.requested_tabs.label("requested_tabs"),
            ZsxqSyncRun.error_message.label("error_message"),
            ZsxqSyncRun.started_at.label("started_at"),
            ZsxqSyncRun.completed_at.label("completed_at"),
            ZsxqSyncRun.created_at.label("created_at"),
            ZsxqSyncRun.updated_at.label("updated_at"),
            ZsxqSyncRun.system_batch_id.label("system_batch_id"),
            ZsxqSyncRun.system_attempt_id.label("system_attempt_id"),
            ZsxqSyncRun.system_round.label("system_round"),
            ZsxqSyncRun.system_max_rounds.label("system_max_rounds"),
            ZsxqSyncRun.system_trigger.label("system_trigger"),
            ZsxqSyncRun.system_queued_at.label("system_queued_at"),
            ZsxqSyncRun.system_max_download_files.label("system_max_download_files"),
            ZsxqSyncRun.topics_fetched.label("topics_fetched"),
            ZsxqSyncRun.topics_new.label("topics_new"),
            ZsxqSyncRun.topics_updated.label("topics_updated"),
            ZsxqSyncRun.topics_existing.label("topics_existing"),
            ZsxqSyncRun.attachments_seen.label("attachments_seen"),
            ZsxqSyncRun.attachments_downloaded.label("attachments_downloaded"),
            ZsxqSyncRun.attachments_failed.label("attachments_failed"),
            ZsxqSyncRun.attachments_skipped.label("attachments_skipped"),
            ZsxqSyncRun.download_limit_reached.label("download_limit_reached"),
            ZsxqSyncRun.download_ledger_complete.label("download_ledger_complete"),
        )

    def _sync_run_public_payload(self, run_id, *, result=None):
        row = (
            self._sync_run_summary_query()
            .filter(ZsxqSyncRun.id == str(run_id))
            .one_or_none()
        )
        if not row:
            raise ValueError(f"同步任务不存在: {run_id}")
        payload = self._sync_run_summary_payload_from_row(row)
        if isinstance(result, dict):
            payload["result"] = deepcopy(result)
        return payload

    def _sync_run_summary_payload_from_row(self, row):
        summary = {
            "topics_fetched": self._int_or_zero(row.topics_fetched),
            "topics_new": self._int_or_zero(row.topics_new),
            "topics_updated": self._int_or_zero(row.topics_updated),
            "topics_existing": self._int_or_zero(row.topics_existing),
            "attachments_seen": self._int_or_zero(row.attachments_seen),
            "attachments_downloaded": self._int_or_zero(row.attachments_downloaded),
            "attachments_failed": self._int_or_zero(row.attachments_failed),
            "attachments_skipped": self._int_or_zero(row.attachments_skipped),
            "download_limit_reached": self._truthy_text(row.download_limit_reached),
            "download_ledger_complete": bool(row.download_ledger_complete),
        }
        result = dict(summary)
        if row.status in {"queued", "running"}:
            result["queued"] = row.status == "queued"
            result["running"] = row.status == "running"
            result["partial"] = dict(summary)
        if row.system_batch_id or str(row.mode or "").startswith("sys"):
            result["system_sync"] = {
                "batch_id": row.system_batch_id,
                "attempt_id": row.system_attempt_id,
                "round": self._int_or_none(row.system_round),
                "max_rounds": self._int_or_none(row.system_max_rounds),
                "trigger": row.system_trigger,
                "queued_at": row.system_queued_at,
                "max_download_files": self._int_or_none(row.system_max_download_files),
            }

        return {
            "id": row.id,
            "group_id": row.group_id,
            "group_name": row.group_name,
            "tab_id": row.tab_id,
            "tab_label": row.tab_label,
            "mode": row.mode,
            "status": row.status,
            "requested_tabs": row.requested_tabs or [],
            "result": result,
            "system_batch_id": row.system_batch_id,
            "system_attempt_id": row.system_attempt_id,
            "system_round": self._int_or_none(row.system_round),
            "system_max_rounds": self._int_or_none(row.system_max_rounds),
            "system_trigger": row.system_trigger,
            "system_queued_at": row.system_queued_at,
            "system_max_download_files": self._int_or_none(row.system_max_download_files),
            **summary,
            "error_message": row.error_message,
            "started_at": row.started_at.isoformat() if row.started_at else None,
            "completed_at": row.completed_at.isoformat() if row.completed_at else None,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }

    def list_system_sync_batches(self, group_id=None, limit=10, batch_id=None):
        self.ensure_schema()
        self._mark_stale_runs_failed(group_id=group_id)
        batches = {}
        max_limit = min(max(int(limit or 10), 1), 30)
        target_batch_id = str(batch_id or "").strip()
        query_limit = 80 if target_batch_id else min(max(max_limit * 3, 10), 40)
        rows = self._system_sync_run_summary_rows(
            group_id=group_id,
            limit=query_limit,
            batch_id=target_batch_id or None,
        )
        if target_batch_id and not rows:
            rows = self._system_sync_run_summary_rows(group_id=group_id, limit=500)
        for row in rows:
            run_payload, meta = self._system_sync_run_payload_from_row(row)
            current_batch_id = meta.get("batch_id") or self._legacy_system_batch_id(row.mode)
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
                "topics_count": 0,
                "topics_truncated": False,
                "_run_ids": [],
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
                    "attachments_below_threshold": 0,
                    "attachments_missing_download_count": 0,
                    "download_limit_reached": False,
                },
            })
            batch["created_at"] = min(filter(None, [batch.get("created_at"), run_payload.get("created_at")]), default=run_payload.get("created_at"))
            batch["completed_at"] = max(filter(None, [batch.get("completed_at"), run_payload.get("completed_at")]), default=run_payload.get("completed_at"))

            run_payload["failed_attachments"] = []
            run_payload["topics"] = []
            run_payload["topics_truncated"] = False
            batch["_run_ids"].append(row.id)
            batch["runs"].append(run_payload)
            self._merge_batch_summary(batch, run_payload)

        ordered = sorted(
            batches.values(),
            key=lambda item: item.get("created_at") or "",
            reverse=True,
        )
        result = ordered[:max_limit]
        if target_batch_id:
            self._hydrate_system_sync_batch_details(result)
        else:
            for batch in result:
                batch["topics_count"] = int((batch.get("summary") or {}).get("topics_fetched") or 0)
        for batch in result:
            batch.pop("_run_ids", None)
        return result

    def _system_sync_run_summary_rows(self, group_id=None, limit=120, batch_id=None):
        query = db.session.query(
            ZsxqSyncRun.id.label("id"),
            ZsxqSyncRun.group_id.label("group_id"),
            ZsxqSyncRun.group_name.label("group_name"),
            ZsxqSyncRun.tab_id.label("tab_id"),
            ZsxqSyncRun.tab_label.label("tab_label"),
            ZsxqSyncRun.mode.label("mode"),
            ZsxqSyncRun.status.label("status"),
            ZsxqSyncRun.requested_tabs.label("requested_tabs"),
            ZsxqSyncRun.error_message.label("error_message"),
            ZsxqSyncRun.started_at.label("started_at"),
            ZsxqSyncRun.completed_at.label("completed_at"),
            ZsxqSyncRun.created_at.label("created_at"),
            ZsxqSyncRun.updated_at.label("updated_at"),
            ZsxqSyncRun.system_batch_id.label("batch_id"),
            ZsxqSyncRun.system_attempt_id.label("attempt_id"),
            ZsxqSyncRun.system_round.label("round"),
            ZsxqSyncRun.system_max_rounds.label("max_rounds"),
            ZsxqSyncRun.system_trigger.label("trigger"),
            ZsxqSyncRun.system_queued_at.label("queued_at"),
            ZsxqSyncRun.system_max_download_files.label("max_download_files"),
            ZsxqSyncRun.topics_fetched.label("topics_fetched"),
            ZsxqSyncRun.topics_new.label("topics_new"),
            ZsxqSyncRun.topics_updated.label("topics_updated"),
            ZsxqSyncRun.topics_existing.label("topics_existing"),
            ZsxqSyncRun.attachments_seen.label("attachments_seen"),
            ZsxqSyncRun.attachments_downloaded.label("attachments_downloaded"),
            ZsxqSyncRun.attachments_failed.label("attachments_failed"),
            ZsxqSyncRun.attachments_skipped.label("attachments_skipped"),
            ZsxqSyncRun.download_limit_reached.label("download_limit_reached"),
        ).filter(text("zsxq_sync_runs.mode LIKE 'sys%'"))
        if group_id:
            query = query.filter(ZsxqSyncRun.group_id == str(group_id))
        batch_id = str(batch_id or "").strip()
        if batch_id:
            query = query.filter(or_(
                ZsxqSyncRun.system_batch_id == batch_id,
                ZsxqSyncRun.system_attempt_id == batch_id,
            ))
        return query.order_by(ZsxqSyncRun.created_at.desc()).limit(limit).all()

    def _system_sync_run_payload_from_row(self, row):
        meta = {
            "batch_id": row.batch_id,
            "attempt_id": row.attempt_id,
            "round": self._int_or_none(row.round),
            "max_rounds": self._int_or_none(row.max_rounds),
            "trigger": row.trigger,
            "queued_at": row.queued_at,
            "max_download_files": self._int_or_none(row.max_download_files),
            "jobs": [],
        }
        result = {
            "system_sync": meta,
            "topics_fetched": self._int_or_zero(row.topics_fetched),
            "topics_new": self._int_or_zero(row.topics_new),
            "topics_updated": self._int_or_zero(row.topics_updated),
            "topics_existing": self._int_or_zero(row.topics_existing),
            "attachments_seen": self._int_or_zero(row.attachments_seen),
            "attachments_downloaded": self._int_or_zero(row.attachments_downloaded),
            "attachments_failed": self._int_or_zero(row.attachments_failed),
            "attachments_skipped": self._int_or_zero(row.attachments_skipped),
            "attachments_below_threshold": 0,
            "attachments_missing_download_count": 0,
            "download_limit_reached": self._truthy_text(row.download_limit_reached),
        }
        return {
            "id": row.id,
            "group_id": row.group_id,
            "group_name": row.group_name,
            "tab_id": row.tab_id,
            "tab_label": row.tab_label,
            "mode": row.mode,
            "status": row.status,
            "requested_tabs": row.requested_tabs or [],
            "result": result,
            "error_message": row.error_message,
            "started_at": row.started_at.isoformat() if row.started_at else None,
            "completed_at": row.completed_at.isoformat() if row.completed_at else None,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }, meta

    def _hydrate_system_sync_batch_details(self, batches):
        run_ids = [
            run_id
            for batch in batches
            for run_id in (batch.get("_run_ids") or [])
            if run_id
        ]
        if not run_ids:
            return
        runs = ZsxqSyncRun.query.options(
            load_only(
                ZsxqSyncRun.id,
                ZsxqSyncRun.group_id,
                ZsxqSyncRun.group_name,
                ZsxqSyncRun.tab_id,
                ZsxqSyncRun.tab_label,
                ZsxqSyncRun.mode,
                ZsxqSyncRun.status,
                ZsxqSyncRun.requested_tabs,
                ZsxqSyncRun.system_batch_id,
                ZsxqSyncRun.system_attempt_id,
                ZsxqSyncRun.system_round,
                ZsxqSyncRun.system_max_rounds,
                ZsxqSyncRun.system_trigger,
                ZsxqSyncRun.system_queued_at,
                ZsxqSyncRun.system_max_download_files,
                ZsxqSyncRun.topics_fetched,
                ZsxqSyncRun.topics_new,
                ZsxqSyncRun.topics_updated,
                ZsxqSyncRun.topics_existing,
                ZsxqSyncRun.attachments_seen,
                ZsxqSyncRun.attachments_downloaded,
                ZsxqSyncRun.attachments_failed,
                ZsxqSyncRun.attachments_skipped,
                ZsxqSyncRun.download_limit_reached,
                ZsxqSyncRun.error_message,
                ZsxqSyncRun.started_at,
                ZsxqSyncRun.completed_at,
                ZsxqSyncRun.created_at,
                ZsxqSyncRun.updated_at,
            )
        ).filter(ZsxqSyncRun.id.in_(run_ids)).all()
        runs_by_id = {run.id: run for run in runs}
        for batch in batches:
            detailed_runs = []
            topics = []
            topics_truncated = False
            for run_id in batch.get("_run_ids") or []:
                run = runs_by_id.get(run_id)
                if not run:
                    continue
                run_payload = self._system_sync_run_payload_from_model(run)
                topic_detail = self._topics_for_run(run)
                run_payload["failed_attachments"] = self._failed_attachments_for_run(run)
                run_payload["topics"] = topic_detail["topics"]
                run_payload["topics_truncated"] = topic_detail["truncated"]
                detailed_runs.append(run_payload)
                topics.extend(run_payload["topics"])
                topics_truncated = topics_truncated or run_payload["topics_truncated"]
            batch["runs"] = detailed_runs
            batch["topics"] = topics
            batch["topics_count"] = len(topics)
            batch["topics_truncated"] = topics_truncated

    def _system_sync_run_payload_from_model(self, run):
        result = {
            "system_sync": {
                "batch_id": run.system_batch_id,
                "attempt_id": run.system_attempt_id,
                "round": self._int_or_none(run.system_round),
                "max_rounds": self._int_or_none(run.system_max_rounds),
                "trigger": run.system_trigger,
                "queued_at": run.system_queued_at,
                "max_download_files": self._int_or_none(run.system_max_download_files),
                "jobs": [],
            },
            "topics_fetched": self._int_or_zero(run.topics_fetched),
            "topics_new": self._int_or_zero(run.topics_new),
            "topics_updated": self._int_or_zero(run.topics_updated),
            "topics_existing": self._int_or_zero(run.topics_existing),
            "attachments_seen": self._int_or_zero(run.attachments_seen),
            "attachments_downloaded": self._int_or_zero(run.attachments_downloaded),
            "attachments_failed": self._int_or_zero(run.attachments_failed),
            "attachments_skipped": self._int_or_zero(run.attachments_skipped),
            "download_limit_reached": bool(run.download_limit_reached),
        }
        return {
            "id": run.id,
            "group_id": run.group_id,
            "group_name": run.group_name,
            "tab_id": run.tab_id,
            "tab_label": run.tab_label,
            "mode": run.mode,
            "status": run.status,
            "requested_tabs": run.requested_tabs or [],
            "result": result,
            "system_batch_id": run.system_batch_id,
            "system_attempt_id": run.system_attempt_id,
            "system_round": run.system_round,
            "system_max_rounds": run.system_max_rounds,
            "system_trigger": run.system_trigger,
            "system_queued_at": run.system_queued_at,
            "system_max_download_files": run.system_max_download_files,
            "topics_fetched": self._int_or_zero(run.topics_fetched),
            "topics_new": self._int_or_zero(run.topics_new),
            "topics_updated": self._int_or_zero(run.topics_updated),
            "topics_existing": self._int_or_zero(run.topics_existing),
            "attachments_seen": self._int_or_zero(run.attachments_seen),
            "attachments_downloaded": self._int_or_zero(run.attachments_downloaded),
            "attachments_failed": self._int_or_zero(run.attachments_failed),
            "attachments_skipped": self._int_or_zero(run.attachments_skipped),
            "download_limit_reached": bool(run.download_limit_reached),
            "error_message": run.error_message,
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "completed_at": run.completed_at.isoformat() if run.completed_at else None,
            "created_at": run.created_at.isoformat() if run.created_at else None,
            "updated_at": run.updated_at.isoformat() if run.updated_at else None,
        }

    def _int_or_zero(self, value):
        return self._int_or_none(value) or 0

    def _int_or_none(self, value):
        if value in (None, ""):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _truthy_text(self, value):
        if isinstance(value, bool):
            return value
        return str(value or "").strip().lower() in {"1", "true", "yes", "on"}

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

        topics = query.options(
            load_only(
                ZsxqTopic.id,
                ZsxqTopic.group_id,
                ZsxqTopic.group_name,
                ZsxqTopic.tab_id,
                ZsxqTopic.tab_label,
                ZsxqTopic.topic_id,
                ZsxqTopic.topic_uid,
                ZsxqTopic.title,
                ZsxqTopic.author_name,
                ZsxqTopic.content,
                ZsxqTopic.topic_type,
                ZsxqTopic.question_text,
                ZsxqTopic.answer_text,
                ZsxqTopic.article_title,
                ZsxqTopic.article_excerpt,
                ZsxqTopic.files_count,
                ZsxqTopic.images_count,
                ZsxqTopic.create_time,
                ZsxqTopic.raw_json_path,
                ZsxqTopic.first_sync_run_id,
                ZsxqTopic.last_sync_run_id,
                ZsxqTopic.last_synced_at,
            )
        ).order_by(ZsxqTopic.last_synced_at.desc()).limit(limit + 1).all()
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

    def _attachments_by_topic(self, group_id, topics, include_raw_payload=False):
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

        columns = [
            ZsxqAttachment.id,
            ZsxqAttachment.group_id,
            ZsxqAttachment.tab_id,
            ZsxqAttachment.topic_id,
            ZsxqAttachment.attachment_key,
            ZsxqAttachment.attachment_type,
            ZsxqAttachment.name,
            ZsxqAttachment.file_id,
            ZsxqAttachment.download_count,
            ZsxqAttachment.local_path,
            ZsxqAttachment.file_ext,
            ZsxqAttachment.download_status,
            ZsxqAttachment.error_message,
            ZsxqAttachment.publish_status,
            ZsxqAttachment.publish_task_id,
            ZsxqAttachment.publish_source_task_id,
            ZsxqAttachment.publish_topic_id,
            ZsxqAttachment.publish_submitted_count,
            ZsxqAttachment.publish_verified_count,
            ZsxqAttachment.publish_failed_count,
            ZsxqAttachment.publish_updated_at,
            ZsxqAttachment.created_at,
            ZsxqAttachment.updated_at,
        ]
        if include_raw_payload:
            columns.append(ZsxqAttachment.raw_payload)

        grouped = {}
        for attachment in query.options(load_only(*columns)).order_by(
            ZsxqAttachment.topic_id.asc(),
            ZsxqAttachment.created_at.asc(),
        ).all():
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
            for item in query.options(
                load_only(
                    ZsxqAttachment.id,
                    ZsxqAttachment.group_id,
                    ZsxqAttachment.tab_id,
                    ZsxqAttachment.topic_id,
                    ZsxqAttachment.attachment_key,
                    ZsxqAttachment.attachment_type,
                    ZsxqAttachment.name,
                    ZsxqAttachment.file_id,
                    ZsxqAttachment.download_count,
                    ZsxqAttachment.local_path,
                    ZsxqAttachment.file_ext,
                    ZsxqAttachment.download_status,
                    ZsxqAttachment.error_message,
                    ZsxqAttachment.publish_status,
                    ZsxqAttachment.publish_task_id,
                    ZsxqAttachment.publish_source_task_id,
                    ZsxqAttachment.publish_topic_id,
                    ZsxqAttachment.publish_submitted_count,
                    ZsxqAttachment.publish_verified_count,
                    ZsxqAttachment.publish_failed_count,
                    ZsxqAttachment.publish_updated_at,
                    ZsxqAttachment.created_at,
                    ZsxqAttachment.updated_at,
                )
            ).order_by(ZsxqAttachment.updated_at.desc()).limit(20).all()
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
            "attachments_below_threshold",
            "attachments_missing_download_count",
        ):
            summary[key] += int(result.get(key) or 0)
        summary["download_limit_reached"] = (
            summary["download_limit_reached"] or bool(result.get("download_limit_reached"))
        )

    def mark_run_failed(self, run_id, error_message, result_update=None):
        self.ensure_schema()
        run = self._load_content_pipeline_run(run_id)
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
        return self._sync_run_public_payload(run_id, result=result)

    def submit_pdf_clean_for_run(
        self,
        run_id,
        cleaner=None,
        trigger="zsxq_sync",
        auto_publish=None,
        workflow_run_id=None,
        run_context=None,
    ):
        """Submit downloaded PDFs from one sync run to the content pipeline."""
        self.ensure_schema()
        run = self._content_pipeline_run_for_submission(run_id, run_context)
        if not run:
            raise ValueError(f"同步任务不存在: {run_id}")

        cleanable = {
            "files": self._downloaded_files_for_run(run, {"pdf"}),
            "source_root": self._content_pipeline_source_root(run, run_context),
        }
        files = [item["path"] for item in cleanable["files"]]
        if not files:
            result = {
                "queued": False,
                "reason": "no_downloaded_pdf",
                "files_count": 0,
            }
            self._merge_run_pipeline_result(run, result)
            self._sync_content_pipeline_run_context(run_context, run)
            return result

        from services.content_pipeline.pdf_clean_task_service import submit_pdf_clean_task

        task = submit_pdf_clean_task(
            title=f"知识星球 PDF 清洗 - {run.group_name or run.group_id}",
            files=files,
            source_root=cleanable.get("source_root"),
            cleaner=cleaner,
            auto_publish=auto_publish,
            trigger=trigger,
            metadata={
                "sync_run_id": run.id,
                "workflow_run_id": workflow_run_id,
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
        self._sync_content_pipeline_run_context(run_context, run)
        return result

    def set_run_pdf_clean_result(self, run_id, pdf_clean_result):
        run = self._load_content_pipeline_run(run_id)
        if not run:
            raise ValueError(f"同步任务不存在: {run_id}")
        self._merge_run_pipeline_result(run, pdf_clean_result)
        return self._sync_run_public_payload(run_id, result=run._zsxq_cached_result)

    def submit_audio_clean_for_run(
        self,
        run_id,
        cleaner=None,
        trigger="zsxq_sync",
        auto_publish=None,
        workflow_run_id=None,
        run_context=None,
    ):
        """Submit newly downloaded audio files from one sync run."""
        self.ensure_schema()
        run = self._content_pipeline_run_for_submission(run_id, run_context)
        if not run:
            raise ValueError(f"同步任务不存在: {run_id}")

        cleanable = {
            "files": self._downloaded_files_for_run(run, AUTO_VIDEO_AUDIO_EXTENSIONS),
            "source_root": self._content_pipeline_source_root(run, run_context),
        }
        files = [item["path"] for item in cleanable["files"]]
        if not files:
            result = {
                "queued": False,
                "reason": "no_downloaded_audio",
                "files_count": 0,
            }
            self._merge_run_content_pipeline_result(run, "audio_clean", result)
            self._sync_content_pipeline_run_context(run_context, run)
            return result

        from services.content_pipeline.audio_clean_task_service import submit_audio_clean_task

        task = submit_audio_clean_task(
            title=f"知识星球音频清洗 - {run.group_name or run.group_id}",
            files=files,
            source_root=cleanable.get("source_root"),
            cleaner=cleaner,
            auto_publish=auto_publish,
            trigger=trigger,
            metadata={
                "sync_run_id": run.id,
                "workflow_run_id": workflow_run_id,
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
        self._merge_run_content_pipeline_result(run, "audio_clean", result)
        self._sync_content_pipeline_run_context(run_context, run)
        return result

    def set_run_audio_clean_result(self, run_id, audio_clean_result):
        run = self._load_content_pipeline_run(run_id)
        if not run:
            raise ValueError(f"同步任务不存在: {run_id}")
        self._merge_run_content_pipeline_result(run, "audio_clean", audio_clean_result)
        return self._sync_run_public_payload(run_id, result=run._zsxq_cached_result)

    def downloaded_pdf_files_for_run(self, run_id):
        self.ensure_schema()
        run = self._load_content_pipeline_run(run_id, include_result=False)
        if not run:
            raise ValueError(f"同步任务不存在: {run_id}")

        return {
            "files": self._downloaded_files_for_run(run, {"pdf"}),
            "source_root": self._run_source_root(run),
            "run": self._content_pipeline_run_snapshot(run),
        }

    def downloaded_audio_files_for_run(self, run_id):
        self.ensure_schema()
        run = self._load_content_pipeline_run(run_id, include_result=False)
        if not run:
            raise ValueError(f"同步任务不存在: {run_id}")

        return {
            "files": self._downloaded_files_for_run(run, AUTO_VIDEO_AUDIO_EXTENSIONS),
            "source_root": self._run_source_root(run),
            "run": self._content_pipeline_run_snapshot(run),
        }

    def _downloaded_files_for_run(self, run, extensions):
        extensions = {str(item).lower().lstrip(".") for item in (extensions or []) if item}
        rows = (
            db.session.query(
                ZsxqSyncRunDownload.attachment_id,
                ZsxqSyncRunDownload.name,
                ZsxqSyncRunDownload.topic_id,
                ZsxqSyncRunDownload.tab_id,
                ZsxqSyncRunDownload.local_path,
                ZsxqSyncRunDownload.file_ext,
            )
            .filter(ZsxqSyncRunDownload.run_id == self._cached_sync_run_id(run))
            .order_by(ZsxqSyncRunDownload.downloaded_at.asc())
            .all()
        )
        files = []
        seen_paths = set()
        for row in rows:
            if str(row.file_ext or "").lower().lstrip(".") not in extensions:
                continue
            path = self._absolute_local_path(row.local_path)
            if not path or not path.exists() or not path.is_file():
                continue
            path_key = str(path)
            if path_key in seen_paths:
                continue
            seen_paths.add(path_key)
            files.append({
                "path": path_key,
                "name": row.name or path.name,
                "topic_id": row.topic_id,
                "tab_id": row.tab_id,
                "attachment_id": row.attachment_id,
            })
        if bool(getattr(run, "download_ledger_complete", False)):
            return files

        # Compatibility only: pre-migration runs have no ledger rows. New
        # ingestion always writes zsxq_sync_run_downloads in the same commit as
        # the attachment download state.
        legacy_run = self._load_content_pipeline_run(self._cached_sync_run_id(run), include_result=True)
        if not legacy_run:
            return []
        if extensions == {"pdf"}:
            legacy_files = self._downloaded_pdf_files_from_run_result(legacy_run)
        else:
            legacy_files = self._downloaded_audio_files_from_run_result(legacy_run)
        return legacy_files or files

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

    def _downloaded_audio_files_from_run_result(self, run):
        result = run.result if isinstance(run.result, dict) else {}
        raw_files = result.get("downloaded_files")
        if raw_files is None:
            raw_files = []
            for tab in result.get("tabs") or []:
                if isinstance(tab, dict):
                    raw_files.extend(tab.get("downloaded_files") or [])

        files = []
        seen_paths = set()
        allowed = {".mp3", ".m4a", ".wav", ".aac", ".flac", ".ogg"}
        for item in raw_files or []:
            if isinstance(item, dict):
                path_value = item.get("path") or item.get("local_path")
                metadata = item
            else:
                path_value = item
                metadata = {}
            path = self._absolute_local_path(path_value)
            if not path or path.suffix.lower() not in allowed or not path.exists() or not path.is_file():
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

    def _normalize_auto_video_dispatch(self, value):
        value = value if isinstance(value, dict) else {}
        return {
            "enabled": self._truthy_text(value.get("enabled", False)),
            "pdf_min_download_count": self._bounded_int(
                value.get("pdf_min_download_count"),
                default=DEFAULT_AUTO_VIDEO_PDF_THRESHOLD,
                lower=0,
                upper=1_000_000,
            ),
            "mp3_min_download_count": self._bounded_int(
                value.get("mp3_min_download_count"),
                default=DEFAULT_AUTO_VIDEO_MP3_THRESHOLD,
                lower=0,
                upper=1_000_000,
            ),
            "wait_for_completion": False,
        }

    def _normalize_auto_script_material_dispatch(self, value):
        value = value if isinstance(value, dict) else {}
        visual_scheme_keys = []
        for item in value.get("visual_scheme_keys") if isinstance(value.get("visual_scheme_keys"), list) else []:
            key = str(item or "").strip().lower()
            if key in {"stick_figure", "doraemon"} and key not in visual_scheme_keys:
                visual_scheme_keys.append(key)
        return {
            "enabled": self._truthy_text(value.get("enabled", False)),
            "pdf_min_download_count": self._bounded_int(
                value.get("pdf_min_download_count"),
                default=DEFAULT_AUTO_SCRIPT_MATERIAL_PDF_THRESHOLD,
                lower=0,
                upper=1_000_000,
            ),
            "visual_scheme_keys": visual_scheme_keys,
            "wait_for_completion": False,
        }

    def _dispatch_config_from_sync_meta(self, value, sync_meta, key):
        if value is not None:
            return value
        sync_meta = sync_meta if isinstance(sync_meta, dict) else {}
        fallback = sync_meta.get(key)
        if isinstance(fallback, dict):
            return fallback
        return value

    def _submit_auto_video_dispatch_for_run(self, run, sync_result, config, workflow_run_id=None):
        summary = self._new_auto_video_dispatch_summary(config, run, workflow_run_id)
        run = SimpleNamespace(
            id=summary["run_id"],
            group_id=summary["group_id"],
            group_name=summary["group_name"],
        )
        try:
            attachment_ids = self._downloaded_attachment_ids_from_sync_result(sync_result)
            summary["attachments_checked"] = len(attachment_ids)
            if not attachment_ids:
                summary["status"] = "skipped"
                summary["reason"] = "no_downloaded_attachments"
                self._finalize_auto_video_dispatch_summary(summary)
                self._send_auto_video_dispatch_email(run, summary)
                return summary

            attachments = (
                ZsxqAttachment.query
                .with_entities(*AUTO_DISPATCH_ATTACHMENT_COLUMNS)
                .filter(ZsxqAttachment.id.in_(attachment_ids))
                .all()
            )
            attachment_by_id = {item.id: item for item in attachments}
            summary["attachments_loaded"] = len(attachment_by_id)
            summary["missing_attachment_rows"] = max(0, len(attachment_ids) - len(attachment_by_id))

            for attachment_id in attachment_ids:
                attachment = attachment_by_id.get(attachment_id)
                if not attachment:
                    continue
                kind = self._auto_video_attachment_kind(attachment)
                if not kind:
                    summary["skipped_non_target"] += 1
                    continue
                bucket = summary[kind]
                bucket["checked"] += 1
                threshold = (
                    config["pdf_min_download_count"]
                    if kind == "pdf"
                    else config["mp3_min_download_count"]
                )
                item = self._auto_video_attachment_summary(attachment, kind, threshold)
                download_count = self._int_or_none(attachment.download_count)
                if download_count is None:
                    bucket["skipped_missing_download_count"] += 1
                    item.update({"status": "skipped", "reason": "missing_download_count"})
                    bucket["items"].append(item)
                    continue
                if download_count < threshold:
                    bucket["skipped_below_threshold"] += 1
                    item.update({"status": "skipped", "reason": "below_threshold"})
                    bucket["items"].append(item)
                    continue
                if attachment.download_status != "downloaded":
                    bucket["skipped_not_downloaded"] += 1
                    item.update({"status": "skipped", "reason": "not_downloaded"})
                    bucket["items"].append(item)
                    continue
                local_path = self._absolute_local_path(attachment.local_path)
                if not local_path or not local_path.exists() or not local_path.is_file():
                    bucket["skipped_missing_file"] += 1
                    item.update({"status": "skipped", "reason": "missing_local_file"})
                    bucket["items"].append(item)
                    continue
                existing_task = self._find_existing_auto_video_task(attachment, kind)
                if existing_task:
                    bucket["skipped_existing"] += 1
                    item.update({
                        "status": "skipped",
                        "reason": "existing_task",
                        "existing_task": self._auto_video_task_summary(existing_task),
                    })
                    bucket["items"].append(item)
                    continue

                bucket["eligible"] += 1
                try:
                    if kind == "pdf":
                        task_result = self._create_auto_report_audio_video_task(
                            run=run,
                            attachment=attachment,
                            threshold=threshold,
                            workflow_run_id=workflow_run_id,
                        )
                    else:
                        task_result = self._create_auto_mp3_video_task(
                            run=run,
                            attachment=attachment,
                            threshold=threshold,
                            workflow_run_id=workflow_run_id,
                        )
                except Exception as exc:
                    db.session.rollback()
                    logger.exception(
                        "[ZSXQ_SYNC] auto video dispatch item failed | run_id=%s attachment=%s kind=%s",
                        run.id,
                        attachment.id,
                        kind,
                    )
                    bucket["failed"] += 1
                    item.update({"status": "failed", "error": str(exc)})
                    summary["errors"].append({
                        "kind": kind,
                        "attachment_id": attachment.id,
                        "message": str(exc),
                    })
                    bucket["items"].append(item)
                    continue

                if task_result.get("queued"):
                    bucket["queued"] += 1
                    summary["queued_task_ids"].append(task_result.get("task_id"))
                    item.update({
                        "status": "queued",
                        "task": task_result,
                    })
                elif task_result.get("deferred"):
                    bucket["deferred"] += 1
                    summary["queued_task_ids"].append(task_result.get("task_id"))
                    item.update({
                        "status": "deferred",
                        "task": task_result,
                    })
                else:
                    bucket["failed"] += 1
                    item.update({
                        "status": "failed",
                        "task": task_result,
                        "error": task_result.get("error") or "任务入队失败",
                    })
                    summary["errors"].append({
                        "kind": kind,
                        "attachment_id": attachment.id,
                        "task_id": task_result.get("task_id"),
                        "message": task_result.get("error") or "任务入队失败",
                    })
                bucket["items"].append(item)

            self._finalize_auto_video_dispatch_summary(summary)
        except Exception as exc:
            db.session.rollback()
            summary["status"] = "failed"
            summary["errors"].append({"message": str(exc)})
            logger.exception("[ZSXQ_SYNC] auto video dispatch failed | run_id=%s", run.id)

        self._send_auto_video_dispatch_email(run, summary)
        return summary

    def _submit_auto_script_material_dispatch_for_run(self, run, sync_result, config, workflow_run_id=None):
        summary = self._new_auto_script_material_dispatch_summary(config, run, workflow_run_id)
        run = SimpleNamespace(
            id=summary["run_id"],
            group_id=summary["group_id"],
            group_name=summary["group_name"],
        )
        try:
            attachment_ids = self._downloaded_attachment_ids_from_sync_result(sync_result)
            summary["attachments_checked"] = len(attachment_ids)
            if not attachment_ids:
                summary["status"] = "skipped"
                summary["reason"] = "no_downloaded_attachments"
                self._finalize_auto_script_material_dispatch_summary(summary)
                return summary

            attachments = (
                ZsxqAttachment.query
                .with_entities(*AUTO_DISPATCH_ATTACHMENT_COLUMNS)
                .filter(ZsxqAttachment.id.in_(attachment_ids))
                .all()
            )
            attachment_by_id = {item.id: item for item in attachments}
            summary["attachments_loaded"] = len(attachment_by_id)
            summary["missing_attachment_rows"] = max(0, len(attachment_ids) - len(attachment_by_id))
            threshold = config["pdf_min_download_count"]
            bucket = summary["pdf"]

            for attachment_id in attachment_ids:
                attachment = attachment_by_id.get(attachment_id)
                if not attachment:
                    continue
                kind = self._auto_video_attachment_kind(attachment)
                if kind != "pdf":
                    summary["skipped_non_target"] += 1
                    continue
                bucket["checked"] += 1
                item = self._auto_video_attachment_summary(attachment, kind, threshold)
                download_count = self._int_or_none(attachment.download_count)
                if download_count is None:
                    bucket["skipped_missing_download_count"] += 1
                    item.update({"status": "skipped", "reason": "missing_download_count"})
                    bucket["items"].append(item)
                    continue
                if download_count < threshold:
                    bucket["skipped_below_threshold"] += 1
                    item.update({"status": "skipped", "reason": "below_threshold"})
                    bucket["items"].append(item)
                    continue
                if attachment.download_status != "downloaded":
                    bucket["skipped_not_downloaded"] += 1
                    item.update({"status": "skipped", "reason": "not_downloaded"})
                    bucket["items"].append(item)
                    continue
                local_path = self._absolute_local_path(attachment.local_path)
                if not local_path or not local_path.exists() or not local_path.is_file():
                    bucket["skipped_missing_file"] += 1
                    item.update({"status": "skipped", "reason": "missing_local_file"})
                    bucket["items"].append(item)
                    continue
                existing_task = self._find_existing_auto_script_material_task(attachment)
                if existing_task:
                    bucket["skipped_existing"] += 1
                    item.update({
                        "status": "skipped",
                        "reason": "existing_material_task",
                        "existing_task": self._auto_video_task_summary(existing_task),
                    })
                    visual_scheme_keys = config.get("visual_scheme_keys")
                    if existing_task.status == "completed" and visual_scheme_keys:
                        try:
                            from tasks.report_audio_workflow import _dispatch_requested_visual_workflows

                            item["visual_workflows"] = _dispatch_requested_visual_workflows(
                                str(existing_task.id),
                                {"visual_scheme_keys": visual_scheme_keys},
                            )
                            item["reason"] = "existing_material_task_visuals_dispatched"
                        except Exception as exc:
                            db.session.rollback()
                            logger.exception(
                                "[ZSXQ_SYNC] existing material visual dispatch failed | task=%s attachment=%s",
                                existing_task.id,
                                attachment.id,
                            )
                            item["visual_workflows"] = {
                                "requested": list(visual_scheme_keys),
                                "items": [],
                                "status": "failed",
                                "error": str(exc),
                            }
                            summary["errors"].append({
                                "kind": kind,
                                "attachment_id": attachment.id,
                                "task_id": str(existing_task.id),
                                "message": f"视觉方案投递失败: {exc}",
                            })
                    bucket["items"].append(item)
                    continue

                bucket["eligible"] += 1
                try:
                    task_result = self._create_auto_report_script_material_task(
                        run=run,
                        attachment=attachment,
                        threshold=threshold,
                        workflow_run_id=workflow_run_id,
                        visual_scheme_keys=config.get("visual_scheme_keys"),
                    )
                except Exception as exc:
                    db.session.rollback()
                    logger.exception(
                        "[ZSXQ_SYNC] auto script material dispatch item failed | run_id=%s attachment=%s",
                        run.id,
                        attachment.id,
                    )
                    bucket["failed"] += 1
                    item.update({"status": "failed", "error": str(exc)})
                    summary["errors"].append({
                        "kind": kind,
                        "attachment_id": attachment.id,
                        "message": str(exc),
                    })
                    bucket["items"].append(item)
                    continue

                if task_result.get("queued"):
                    bucket["queued"] += 1
                    summary["queued_task_ids"].append(task_result.get("task_id"))
                    item.update({
                        "status": "queued",
                        "task": task_result,
                    })
                elif task_result.get("deferred"):
                    bucket["deferred"] += 1
                    summary["queued_task_ids"].append(task_result.get("task_id"))
                    item.update({
                        "status": "deferred",
                        "task": task_result,
                    })
                else:
                    bucket["failed"] += 1
                    item.update({
                        "status": "failed",
                        "task": task_result,
                        "error": task_result.get("error") or "任务入队失败",
                    })
                    summary["errors"].append({
                        "kind": kind,
                        "attachment_id": attachment.id,
                        "task_id": task_result.get("task_id"),
                        "message": task_result.get("error") or "任务入队失败",
                    })
                bucket["items"].append(item)

            self._finalize_auto_script_material_dispatch_summary(summary)
        except Exception as exc:
            db.session.rollback()
            summary["status"] = "failed"
            summary["errors"].append({"message": str(exc)})
            logger.exception("[ZSXQ_SYNC] auto script material dispatch failed | run_id=%s", run.id)
        return summary

    def _new_auto_video_dispatch_summary(self, config, run, workflow_run_id=None):
        return {
            "enabled": bool(config.get("enabled")),
            "status": "pending",
            "source": AUTO_VIDEO_DISPATCH_SOURCE,
            "config": dict(config),
            "run_id": run.id,
            "workflow_run_id": workflow_run_id,
            "group_id": run.group_id,
            "group_name": run.group_name,
            "attachments_checked": 0,
            "attachments_loaded": 0,
            "missing_attachment_rows": 0,
            "skipped_non_target": 0,
            "queued_task_ids": [],
            "errors": [],
            "pdf": self._auto_video_dispatch_bucket(config.get("pdf_min_download_count")),
            "mp3": self._auto_video_dispatch_bucket(config.get("mp3_min_download_count")),
        }

    def _new_auto_script_material_dispatch_summary(self, config, run, workflow_run_id=None):
        return {
            "enabled": bool(config.get("enabled")),
            "status": "pending",
            "source": AUTO_SCRIPT_MATERIAL_DISPATCH_SOURCE,
            "config": dict(config),
            "run_id": run.id,
            "workflow_run_id": workflow_run_id,
            "group_id": run.group_id,
            "group_name": run.group_name,
            "attachments_checked": 0,
            "attachments_loaded": 0,
            "missing_attachment_rows": 0,
            "skipped_non_target": 0,
            "queued_task_ids": [],
            "errors": [],
            "pdf": self._auto_video_dispatch_bucket(config.get("pdf_min_download_count")),
        }

    def _auto_video_dispatch_bucket(self, threshold):
        return {
            "threshold": threshold,
            "checked": 0,
            "eligible": 0,
            "queued": 0,
            "deferred": 0,
            "failed": 0,
            "skipped_existing": 0,
            "skipped_below_threshold": 0,
            "skipped_missing_download_count": 0,
            "skipped_not_downloaded": 0,
            "skipped_missing_file": 0,
            "items": [],
        }

    def _downloaded_attachment_ids_from_sync_result(self, sync_result):
        ids = []

        def collect(items):
            for item in items or []:
                if not isinstance(item, dict):
                    continue
                attachment_id = str(item.get("attachment_id") or "").strip()
                if attachment_id:
                    ids.append(attachment_id)

        collect((sync_result or {}).get("downloaded_files"))
        collect((sync_result or {}).get("downloaded_pdf_files"))
        collect((sync_result or {}).get("auto_video_dispatch_files"))
        for tab in (sync_result or {}).get("tabs") or []:
            if not isinstance(tab, dict):
                continue
            collect(tab.get("downloaded_files"))
            collect(tab.get("downloaded_pdf_files"))
            collect(tab.get("auto_video_dispatch_files"))
        return list(dict.fromkeys(ids))

    def _auto_video_attachment_kind(self, attachment):
        ext = str(
            attachment.file_ext
            or Path(str(attachment.local_path or attachment.name or "")).suffix.lstrip(".")
        ).strip().lower().lstrip(".")
        if ext == "pdf":
            return "pdf"
        if ext in AUTO_VIDEO_AUDIO_EXTENSIONS:
            return "mp3"
        return None

    def _auto_video_attachment_summary(self, attachment, kind, threshold):
        return {
            "kind": kind,
            "attachment_id": attachment.id,
            "name": attachment.name,
            "topic_id": attachment.topic_id,
            "tab_id": attachment.tab_id,
            "download_count": attachment.download_count,
            "threshold": threshold,
        }

    def _find_existing_auto_video_task(self, attachment, kind):
        if kind == "pdf":
            tasks = (
                PublicationTask.query
                .filter(PublicationTask.feature.in_(AUTO_VIDEO_REPORT_AUDIO_FEATURES))
                .filter(PublicationTask.status != "canceled")
                .filter(or_(
                    PublicationTask.source_task_id == attachment.id,
                    PublicationTask.report_audio_pdf_attachment_id == attachment.id,
                ))
                .order_by(PublicationTask.created_at.desc())
                .limit(20)
                .all()
            )
            for task in tasks:
                if self._is_script_material_only_report_audio_task(task):
                    continue
                return task
            return None

        task = (
            PublicationTask.query
            .filter(PublicationTask.feature == "mp3_video_workflow")
            .filter(PublicationTask.status != "canceled")
            .filter(PublicationTask.source_task_id == attachment.id)
            .order_by(PublicationTask.created_at.desc())
            .first()
        )
        if task:
            return task

        recent_tasks = (
            PublicationTask.query
            .options(load_only(
                PublicationTask.id,
                PublicationTask.status,
                PublicationTask.current_step,
                PublicationTask.celery_task_id,
                PublicationTask.params,
                PublicationTask.created_at,
            ))
            .filter(PublicationTask.feature == "mp3_video_workflow")
            .filter(PublicationTask.status != "canceled")
            .order_by(PublicationTask.created_at.desc())
            .limit(500)
            .all()
        )
        for row in recent_tasks:
            params = row.params if isinstance(row.params, dict) else {}
            workflow = params.get("mp3_video_workflow") if isinstance(params.get("mp3_video_workflow"), dict) else {}
            audio = workflow.get("audio") if isinstance(workflow.get("audio"), dict) else {}
            if str(workflow.get("audio_asset_id") or "") == attachment.id:
                return row
            if str(audio.get("attachment_id") or "") == attachment.id:
                return row
        return None

    def _find_existing_auto_script_material_task(self, attachment):
        return (
            PublicationTask.query
            .options(load_only(
                PublicationTask.id,
                PublicationTask.celery_task_id,
                PublicationTask.feature,
                PublicationTask.status,
                PublicationTask.current_step,
                PublicationTask.created_at,
            ))
            .filter(PublicationTask.feature.in_(AUTO_SCRIPT_MATERIAL_FEATURES))
            .filter(PublicationTask.status.in_(("pending", "processing", "completed")))
            .filter(or_(
                PublicationTask.source_task_id == attachment.id,
                PublicationTask.report_audio_pdf_attachment_id == attachment.id,
            ))
            .order_by(PublicationTask.created_at.desc())
            .first()
        )

    def _is_script_material_only_report_audio_task(self, task):
        params = task.params if isinstance(task.params, dict) else {}
        workflow = params.get("report_audio_workflow") if isinstance(params.get("report_audio_workflow"), dict) else {}
        return self._truthy_text(workflow.get("script_material_only", False))

    def _auto_video_task_summary(self, task):
        return {
            "task_id": task.id,
            "celery_task_id": task.celery_task_id,
            "feature": task.feature,
            "status": task.status,
            "current_step": task.current_step,
        }

    def _create_auto_report_audio_video_task(self, *, run, attachment, threshold, workflow_run_id=None):
        from services.report_audio_workflow import (
            DEFAULT_INITIAL_PROMPT,
            DEFAULT_REVIEW_PROMPT,
            FEATURE_NAME,
            QUEUE_NAME,
        )
        from tasks.report_audio_workflow import process_report_audio_workflow_job

        pdf_meta = self._report_audio_pdf_meta_from_attachment(attachment, job_id=None)
        topic_title = str(pdf_meta.get("topic_title") or "").strip()
        filename_title = str(pdf_meta.get("filename") or "").strip()
        title = (
            topic_title
            if topic_title and not _is_generic_auto_video_topic_title(topic_title)
            else filename_title or "研报视频编排"
        )
        metadata = self._auto_video_dispatch_metadata(
            run=run,
            attachment=attachment,
            threshold=threshold,
            workflow_run_id=workflow_run_id,
            title=title,
        )
        video_options = {
            "enabled": True,
            "vertical_enabled": True,
            "bilibili_enabled": False,
            "sub_burn_enabled": True,
            "title": "",
            "subtitle": "",
            "tag_text": "研报播客",
            "source": AUTO_VIDEO_DISPATCH_SOURCE,
        }
        task = PublicationTask(
            status="pending",
            current_step="queued",
            progress=0,
            params={
                "feature": FEATURE_NAME,
                "user_id": "system",
                "parent_feature": "zsxq_sync_run",
                "parent_task_id": run.id,
                "source_feature": "zsxq_attachment",
                "source_task_id": attachment.id,
                "report_audio_workflow": {
                    "prompt": DEFAULT_INITIAL_PROMPT,
                    "review_prompt": DEFAULT_REVIEW_PROMPT,
                    "followups": [],
                    "gemini_url": "",
                    "metadata": metadata,
                    "video": video_options,
                    "pdf": None,
                    "pdf_path": None,
                },
            },
            result={
                "metadata": metadata,
                "pdf": None,
                "children": {},
                "structured_result": None,
                "audio": None,
                "video": None,
                "logs": [],
                "auto_video_dispatch": metadata["auto_video_dispatch"],
            },
        )
        db.session.add(task)
        db.session.flush()

        pdf_meta = dict(pdf_meta)
        pdf_meta["download_url"] = f"/api/report-audio-workflows/jobs/{task.id}/input-pdf"
        params = dict(task.params or {})
        workflow = dict(params.get("report_audio_workflow") or {})
        workflow["pdf"] = pdf_meta
        workflow["pdf_path"] = pdf_meta["path"]
        params["report_audio_workflow"] = workflow
        task.params = params
        result = dict(task.result or {})
        result["pdf"] = pdf_meta
        task.result = result
        db.session.commit()

        try:
            submit_result = maybe_guarded_apply_async(
                process_report_audio_workflow_job,
                args=[task.id],
                queue=QUEUE_NAME,
                feature=FEATURE_NAME,
                task=task,
                retry=False,
            )
            celery_task = submit_result["async_result"]
        except Exception as exc:
            task.status = "failed"
            task.current_step = "failed"
            task.error_message = f"编排任务入队失败: {exc}"
            task.completed_at = datetime.utcnow()
            db.session.commit()
            return {
                "queued": False,
                "task_id": task.id,
                "feature": FEATURE_NAME,
                "error": task.error_message,
            }

        task.celery_task_id = celery_task.id
        db.session.commit()
        deferred = bool(submit_result.get("deferred"))
        return {
            "queued": not deferred,
            "deferred": deferred,
            "task_id": task.id,
            "celery_task_id": celery_task.id,
            "feature": FEATURE_NAME,
            "queue": QUEUE_NAME,
            "admission": submit_result.get("admission"),
        }

    def _create_auto_report_script_material_task(
        self,
        *,
        run,
        attachment,
        threshold,
        workflow_run_id=None,
        visual_scheme_keys=None,
    ):
        from services.report_script_material_workflow import (
            DEFAULT_INITIAL_PROMPT,
            DEFAULT_REVIEW_PROMPT,
            FEATURE_NAME,
            QUEUE_NAME,
        )
        from tasks.report_script_material_workflow import process_report_script_material_workflow_job

        pdf_meta = self._report_audio_pdf_meta_from_attachment(attachment, job_id=None)
        topic_title = str(pdf_meta.get("topic_title") or "").strip()
        filename_title = str(pdf_meta.get("filename") or "").strip()
        title = (
            topic_title
            if topic_title and not _is_generic_auto_video_topic_title(topic_title)
            else filename_title or "研报基础口播文案物料"
        )
        metadata = self._auto_script_material_dispatch_metadata(
            run=run,
            attachment=attachment,
            threshold=threshold,
            workflow_run_id=workflow_run_id,
            title=title,
            visual_scheme_keys=visual_scheme_keys,
        )
        video_options = {
            "enabled": False,
            "vertical_enabled": False,
            "bilibili_enabled": False,
            "sub_burn_enabled": False,
            "title": "",
            "subtitle": "",
            "tag_text": "研报播客",
            "source": AUTO_SCRIPT_MATERIAL_DISPATCH_SOURCE,
        }
        task = PublicationTask(
            status="pending",
            current_step="queued",
            progress=0,
            params={
                "feature": FEATURE_NAME,
                "user_id": "system",
                "parent_feature": "zsxq_sync_run",
                "parent_task_id": run.id,
                "source_feature": "zsxq_attachment",
                "source_task_id": attachment.id,
                FEATURE_NAME: {
                    "prompt": DEFAULT_INITIAL_PROMPT,
                    "review_prompt": DEFAULT_REVIEW_PROMPT,
                    "followups": [],
                    "gemini_url": "",
                    "metadata": metadata,
                    "video": video_options,
                    "pdf": None,
                    "pdf_path": None,
                },
            },
            result={
                "metadata": metadata,
                "pdf": None,
                "children": {},
                "structured_result": None,
                "script_material": {"status": "pending"},
                "audio": None,
                "video": {
                    "enabled": False,
                    "status": "skipped",
                    "message": FEATURE_NAME,
                },
                "logs": [],
                "auto_script_material_dispatch": metadata["auto_script_material_dispatch"],
            },
        )
        db.session.add(task)
        db.session.flush()

        pdf_meta = dict(pdf_meta)
        pdf_meta["download_url"] = None
        params = dict(task.params or {})
        workflow = dict(params.get(FEATURE_NAME) or {})
        workflow["pdf"] = pdf_meta
        workflow["pdf_path"] = pdf_meta["path"]
        params[FEATURE_NAME] = workflow
        task.params = params
        result = dict(task.result or {})
        result["pdf"] = pdf_meta
        task.result = result
        task_id = str(task.id)
        db.session.commit()

        try:
            submit_result = maybe_guarded_apply_async(
                process_report_script_material_workflow_job,
                args=[task_id],
                queue=QUEUE_NAME,
                feature=FEATURE_NAME,
                task_id=task_id,
                retry=False,
            )
            celery_task = submit_result["async_result"]
        except Exception as exc:
            error_message = f"基础文案物料任务入队失败: {exc}"
            PublicationTask.query.filter(PublicationTask.id == task_id).update(
                {
                    PublicationTask.status: "failed",
                    PublicationTask.current_step: "failed",
                    PublicationTask.error_message: error_message,
                    PublicationTask.completed_at: datetime.utcnow(),
                },
                synchronize_session=False,
            )
            db.session.commit()
            return {
                "queued": False,
                "task_id": task_id,
                "feature": FEATURE_NAME,
                "error": error_message,
            }

        PublicationTask.query.filter(PublicationTask.id == task_id).update(
            {PublicationTask.celery_task_id: celery_task.id},
            synchronize_session=False,
        )
        db.session.commit()
        deferred = bool(submit_result.get("deferred"))
        return {
            "queued": not deferred,
            "deferred": deferred,
            "task_id": task_id,
            "celery_task_id": celery_task.id,
            "feature": FEATURE_NAME,
            "queue": QUEUE_NAME,
            "admission": submit_result.get("admission"),
        }

    def _create_auto_mp3_video_task(self, *, run, attachment, threshold, workflow_run_id=None):
        from services.mp3_video_workflow import (
            DEFAULT_GEMINI_URL,
            FEATURE_NAME,
            INFOGRAPHIC_PROMPT,
            QUEUE_NAME,
            audio_meta_from_zsxq_attachment,
        )
        from tasks.mp3_video_workflow import process_mp3_video_workflow_job

        audio = audio_meta_from_zsxq_attachment(attachment.id)
        title = audio.get("topic_title") or audio.get("filename") or "MP3 视频编排"
        metadata = self._auto_video_dispatch_metadata(
            run=run,
            attachment=attachment,
            threshold=threshold,
            workflow_run_id=workflow_run_id,
            title=title,
        )
        task = PublicationTask(
            status="pending",
            current_step="queued",
            progress=0,
            params={
                "feature": FEATURE_NAME,
                "user_id": "system",
                "parent_feature": "zsxq_sync_run",
                "parent_task_id": run.id,
                "source_feature": "zsxq_attachment",
                "source_task_id": attachment.id,
                FEATURE_NAME: {
                    "audio_asset_id": attachment.id,
                    "audio": audio,
                    "gemini_url": DEFAULT_GEMINI_URL,
                    "image_prompt": INFOGRAPHIC_PROMPT,
                    "metadata": metadata,
                },
            },
            result={
                "metadata": metadata,
                "audio": audio,
                "children": {},
                "logs": [],
                "auto_video_dispatch": metadata["auto_video_dispatch"],
            },
        )
        db.session.add(task)
        db.session.flush()
        task_id = str(task.id)
        db.session.commit()

        try:
            submit_result = maybe_guarded_apply_async(
                process_mp3_video_workflow_job,
                args=[task_id],
                queue=QUEUE_NAME,
                feature=FEATURE_NAME,
                task_id=task_id,
                retry=False,
            )
            celery_task = submit_result["async_result"]
        except Exception as exc:
            error_message = f"编排任务入队失败: {exc}"
            PublicationTask.query.filter(PublicationTask.id == task_id).update(
                {
                    PublicationTask.status: "failed",
                    PublicationTask.current_step: "failed",
                    PublicationTask.error_message: error_message,
                    PublicationTask.completed_at: datetime.utcnow(),
                },
                synchronize_session=False,
            )
            db.session.commit()
            return {
                "queued": False,
                "task_id": task_id,
                "feature": FEATURE_NAME,
                "error": error_message,
            }

        PublicationTask.query.filter(PublicationTask.id == task_id).update(
            {PublicationTask.celery_task_id: celery_task.id},
            synchronize_session=False,
        )
        db.session.commit()
        deferred = bool(submit_result.get("deferred"))
        return {
            "queued": not deferred,
            "deferred": deferred,
            "task_id": task_id,
            "celery_task_id": celery_task.id,
            "feature": FEATURE_NAME,
            "queue": QUEUE_NAME,
            "admission": submit_result.get("admission"),
        }

    def _auto_video_dispatch_metadata(self, *, run, attachment, threshold, workflow_run_id=None, title=None):
        return {
            "title": title or attachment.name,
            "source": AUTO_VIDEO_DISPATCH_SOURCE,
            "auto_video_dispatch": {
                "sync_run_id": run.id,
                "workflow_run_id": workflow_run_id,
                "group_id": run.group_id,
                "group_name": run.group_name,
                "attachment_id": attachment.id,
                "topic_id": attachment.topic_id,
                "tab_id": attachment.tab_id,
                "download_count": attachment.download_count,
                "threshold": threshold,
                "queued_at": datetime.utcnow().isoformat() + "Z",
            },
        }

    def _auto_script_material_dispatch_metadata(
        self,
        *,
        run,
        attachment,
        threshold,
        workflow_run_id=None,
        title=None,
        visual_scheme_keys=None,
    ):
        normalized_visual_schemes = []
        for item in visual_scheme_keys if isinstance(visual_scheme_keys, list) else []:
            key = str(item or "").strip().lower()
            if key in {"stick_figure", "doraemon"} and key not in normalized_visual_schemes:
                normalized_visual_schemes.append(key)
        return {
            "title": title or attachment.name,
            "source": AUTO_SCRIPT_MATERIAL_DISPATCH_SOURCE,
            "visual_scheme_keys": normalized_visual_schemes,
            "auto_script_material_dispatch": {
                "sync_run_id": run.id,
                "workflow_run_id": workflow_run_id,
                "group_id": run.group_id,
                "group_name": run.group_name,
                "attachment_id": attachment.id,
                "topic_id": attachment.topic_id,
                "tab_id": attachment.tab_id,
                "download_count": attachment.download_count,
                "threshold": threshold,
                "visual_scheme_keys": normalized_visual_schemes,
                "queued_at": datetime.utcnow().isoformat() + "Z",
            },
        }

    def _report_audio_pdf_meta_from_attachment(self, attachment, job_id=None):
        if attachment.download_status != "downloaded":
            raise ValueError("选择的同步 PDF 还没有下载完成")
        path = self._absolute_local_path(attachment.local_path)
        if not path or not path.exists() or not path.is_file() or path.suffix.lower() != ".pdf":
            raise ValueError("选择的同步 PDF 本地文件不存在")
        size = path.stat().st_size
        if size <= 0:
            raise ValueError("选择的同步 PDF 文件为空")
        max_bytes = self._report_audio_max_pdf_bytes()
        if size > max_bytes:
            raise ValueError(f"PDF 超过 {self._report_audio_max_pdf_mb()}MB 限制")

        topic = (
            ZsxqTopic.query
            .with_entities(ZsxqTopic.title, ZsxqTopic.tab_label)
            .filter_by(
                group_id=attachment.group_id,
                tab_id=attachment.tab_id,
                topic_id=attachment.topic_id,
            )
            .first()
        )
        return {
            "filename": attachment.name or path.name,
            "path": str(path),
            "bytes": size,
            "source": "zsxq_attachment",
            "attachment_id": attachment.id,
            "group_id": attachment.group_id,
            "tab_id": attachment.tab_id,
            "topic_id": attachment.topic_id,
            "topic_title": topic.title if topic else None,
            "tab_label": topic.tab_label if topic else None,
            "download_url": f"/api/report-audio-workflows/jobs/{job_id}/input-pdf" if job_id else None,
        }

    def _report_audio_max_pdf_mb(self):
        value = os.getenv("REPORT_AUDIO_WORKFLOW_MAX_PDF_MB", os.getenv("GEMINI_BROWSER_MAX_PDF_MB", "200"))
        return self._bounded_int(value, default=200, lower=1, upper=10_000)

    def _report_audio_max_pdf_bytes(self):
        return self._report_audio_max_pdf_mb() * 1024 * 1024

    def _finalize_auto_video_dispatch_summary(self, summary):
        pdf = summary.get("pdf") or {}
        mp3 = summary.get("mp3") or {}
        queued = self._safe_int(pdf.get("queued")) + self._safe_int(mp3.get("queued"))
        deferred = self._safe_int(pdf.get("deferred")) + self._safe_int(mp3.get("deferred"))
        dispatched = queued + deferred
        failed = self._safe_int(pdf.get("failed")) + self._safe_int(mp3.get("failed"))
        eligible = self._safe_int(pdf.get("eligible")) + self._safe_int(mp3.get("eligible"))
        checked = self._safe_int(pdf.get("checked")) + self._safe_int(mp3.get("checked"))
        summary["queued_count"] = queued
        summary["deferred_count"] = deferred
        summary["dispatched_count"] = dispatched
        summary["failed_count"] = failed
        summary["eligible_count"] = eligible
        if failed and dispatched:
            summary["status"] = "partial"
        elif failed:
            summary["status"] = "failed"
        elif dispatched:
            summary["status"] = "completed"
        elif checked:
            summary["status"] = "skipped"
            summary.setdefault("reason", "no_eligible_attachment")
        else:
            summary["status"] = "skipped"
            summary.setdefault("reason", "no_target_attachment")

    def _finalize_auto_script_material_dispatch_summary(self, summary):
        pdf = summary.get("pdf") or {}
        queued = self._safe_int(pdf.get("queued"))
        failed = self._safe_int(pdf.get("failed"))
        eligible = self._safe_int(pdf.get("eligible"))
        checked = self._safe_int(pdf.get("checked"))
        summary["queued_count"] = queued
        deferred = self._safe_int(pdf.get("deferred"))
        dispatched = queued + deferred
        summary["deferred_count"] = deferred
        summary["dispatched_count"] = dispatched
        summary["failed_count"] = failed
        summary["eligible_count"] = eligible
        if failed and dispatched:
            summary["status"] = "partial"
        elif failed:
            summary["status"] = "failed"
        elif dispatched:
            summary["status"] = "completed"
        elif checked:
            summary["status"] = "skipped"
            summary.setdefault("reason", "no_eligible_attachment")
        else:
            summary["status"] = "skipped"
            summary.setdefault("reason", "no_target_attachment")

    def _send_auto_video_dispatch_email(self, run, summary):
        try:
            from services.workflow_notification_service import workflow_notification_service

            subject = f"[知识星球工作流] 视频编排旁路投递 · {run.group_name or run.group_id}"
            lines = [
                "事件：视频编排旁路投递",
                f"星球：{run.group_name or run.group_id}",
                f"状态：{self._auto_video_status_label(summary.get('status'))}",
                f"同步 Run ID：{run.id}",
                f"工作流 ID：{summary.get('workflow_run_id') or '-'}",
                f"来源：{AUTO_VIDEO_DISPATCH_SOURCE}",
                "",
                "投递概览：",
                f"检查附件：{summary.get('attachments_checked', 0)} 个，加载记录：{summary.get('attachments_loaded', 0)} 个，非目标类型跳过：{summary.get('skipped_non_target', 0)} 个。",
                self._auto_video_email_bucket_line("PDF", summary.get("pdf") or {}),
                self._auto_video_email_bucket_line("MP3", summary.get("mp3") or {}),
            ]
            if summary.get("reason"):
                lines.append(f"原因：{summary.get('reason')}")
            if summary.get("queued_task_ids"):
                lines.append("")
                lines.append("已投递任务：")
                for task_id in summary.get("queued_task_ids")[:20]:
                    lines.append(f"- {task_id}")
            if summary.get("errors"):
                lines.append("")
                lines.append("异常明细：")
                for item in summary.get("errors")[:20]:
                    if not isinstance(item, dict):
                        continue
                    prefix = item.get("attachment_id") or item.get("task_id") or item.get("kind") or "dispatch"
                    lines.append(f"- {prefix}: {item.get('message')}")
            lines.extend([
                "",
                f"发送时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            ])
            workflow_notification_service.send_email_message(subject, "\n".join(lines))
            summary["email"] = {
                "sent": True,
                "sent_at": datetime.utcnow().isoformat() + "Z",
            }
        except Exception as exc:
            logger.exception("[ZSXQ_SYNC] auto video dispatch email failed | run_id=%s", run.id)
            summary["email"] = {
                "sent": False,
                "error": str(exc),
                "failed_at": datetime.utcnow().isoformat() + "Z",
            }

    def _auto_video_email_bucket_line(self, label, bucket):
        return (
            f"{label}：阈值 {bucket.get('threshold')}，检查 {bucket.get('checked', 0)}，"
            f"达阈值 {bucket.get('eligible', 0)}，投递 {bucket.get('queued', 0)}，"
            f"延后排队 {bucket.get('deferred', 0)}，"
            f"已有任务 {bucket.get('skipped_existing', 0)}，低于阈值 {bucket.get('skipped_below_threshold', 0)}，"
            f"缺少下载数 {bucket.get('skipped_missing_download_count', 0)}，"
            f"文件缺失 {bucket.get('skipped_missing_file', 0)}，失败 {bucket.get('failed', 0)}。"
        )

    def _auto_video_status_label(self, status):
        return {
            "completed": "成功",
            "partial": "部分成功",
            "failed": "失败",
            "skipped": "跳过",
            "pending": "待处理",
        }.get(str(status or ""), status or "-")

    def _bounded_int(self, value, *, default, lower, upper):
        number = self._int_or_none(value)
        if number is None:
            number = int(default)
        return min(max(int(number), int(lower)), int(upper))

    def _merge_run_pipeline_result(self, run, pdf_clean_result):
        self._merge_run_content_pipeline_result(run, "pdf_clean", pdf_clean_result)

    def _merge_run_content_pipeline_result(self, run, key, value):
        cached = getattr(run, "_zsxq_cached_result", None)
        result = dict(cached) if isinstance(cached, dict) else {}
        content_pipeline = dict(result.get("content_pipeline") or {})
        content_pipeline[key] = value
        result["content_pipeline"] = content_pipeline
        self._patch_run_content_pipeline_result(self._cached_sync_run_id(run), key, value)
        run._zsxq_cached_result = deepcopy(result)

    def _patch_run_content_pipeline_result(self, run_id, key, value):
        payload = json.dumps(value if isinstance(value, dict) else {}, ensure_ascii=False, default=str)
        dialect = db.engine.dialect.name
        if dialect == "postgresql":
            statement = text("""
                UPDATE public.zsxq_sync_runs
                SET result = COALESCE(result::jsonb, '{}'::jsonb) || jsonb_build_object(
                        'content_pipeline',
                        COALESCE(result::jsonb -> 'content_pipeline', '{}'::jsonb)
                        || jsonb_build_object(:pipeline_key, CAST(:pipeline_value AS jsonb))
                    ),
                    updated_at = :updated_at
                WHERE id = :run_id
            """)
            params = {
                "pipeline_key": str(key),
                "pipeline_value": payload,
                "updated_at": datetime.utcnow(),
                "run_id": str(run_id),
            }
        elif dialect == "sqlite":
            statement = text("""
                UPDATE zsxq_sync_runs
                SET result = json_set(
                        COALESCE(result, '{}'),
                        :json_path,
                        json(:pipeline_value)
                    ),
                    updated_at = :updated_at
                WHERE id = :run_id
            """)
            params = {
                "json_path": f"$.content_pipeline.{key}",
                "pipeline_value": payload,
                "updated_at": datetime.utcnow(),
                "run_id": str(run_id),
            }
        else:
            current = (
                db.session.query(ZsxqSyncRun.result)
                .filter(ZsxqSyncRun.id == str(run_id))
                .scalar()
            )
            merged = dict(current) if isinstance(current, dict) else {}
            content_pipeline = dict(merged.get("content_pipeline") or {})
            content_pipeline[str(key)] = value
            merged["content_pipeline"] = content_pipeline
            statement = (
                update(ZsxqSyncRun)
                .where(ZsxqSyncRun.id == str(run_id))
                .values(result=merged, updated_at=datetime.utcnow())
            )
            params = {}
        updated = db.session.execute(statement, params)
        if updated.rowcount != 1:
            db.session.rollback()
            raise ValueError(f"同步任务不存在: {run_id}")
        db.session.commit()

    def _load_content_pipeline_run(self, run_id, *, include_result=True):
        columns = [
            ZsxqSyncRun.id,
            ZsxqSyncRun.group_id,
            ZsxqSyncRun.group_name,
            ZsxqSyncRun.tab_id,
            ZsxqSyncRun.tab_label,
            ZsxqSyncRun.mode,
            ZsxqSyncRun.attachments_downloaded,
            ZsxqSyncRun.download_ledger_complete,
        ]
        if include_result:
            columns.append(ZsxqSyncRun.result)
        run = (
            ZsxqSyncRun.query.options(
                load_only(*columns)
            )
            .filter(ZsxqSyncRun.id == str(run_id))
            .one_or_none()
        )
        if run:
            run._zsxq_cached_run_id = self._cached_sync_run_id(run)
            run._zsxq_cached_result = (
                deepcopy(run.result)
                if include_result and isinstance(run.result, dict)
                else {}
            )
        return run

    def _content_pipeline_run_for_submission(self, run_id, run_context):
        if isinstance(run_context, dict) and isinstance(run_context.get("run"), dict):
            snapshot = run_context["run"]
            snapshot_run_id = str(snapshot.get("id") or "")
            if snapshot_run_id != str(run_id):
                raise ValueError(
                    f"清洗上下文与同步任务不匹配: context={snapshot_run_id or '-'} run={run_id}"
                )
            result = deepcopy(run_context.get("result")) if isinstance(run_context.get("result"), dict) else {}
            return SimpleNamespace(
                **snapshot,
                result=result,
                _zsxq_cached_run_id=snapshot_run_id,
                _zsxq_cached_result=deepcopy(result),
            )

        run = self._load_content_pipeline_run(run_id, include_result=False)
        if run and isinstance(run_context, dict):
            run_context.update({
                "run": self._content_pipeline_run_snapshot(run),
                "result": deepcopy(run._zsxq_cached_result),
                "source_root": self._run_source_root(run),
            })
        return run

    def _content_pipeline_source_root(self, run, run_context):
        if isinstance(run_context, dict) and "source_root" in run_context:
            return run_context.get("source_root")
        return self._run_source_root(run)

    def _sync_content_pipeline_run_context(self, run_context, run):
        if not isinstance(run_context, dict):
            return
        cached_result = getattr(run, "_zsxq_cached_result", None)
        run_context["result"] = deepcopy(cached_result) if isinstance(cached_result, dict) else {}

    def _content_pipeline_run_snapshot(self, run):
        return {
            "id": self._cached_sync_run_id(run),
            "group_id": run.group_id,
            "group_name": run.group_name,
            "tab_id": run.tab_id,
            "tab_label": run.tab_label,
            "mode": run.mode,
            "attachments_downloaded": self._safe_int(
                getattr(run, "attachments_downloaded", 0)
            ),
            "download_ledger_complete": bool(
                getattr(run, "download_ledger_complete", False)
            ),
        }

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

    def list_cached_groups(self):
        if not self._sync_tables_ready():
            return {
                "succeeded": True,
                "source": "local_db",
                "tables_ready": False,
                "resp_data": {
                    "groups": [],
                    "message": "本地同步表尚未初始化。",
                },
            }

        rows = db.session.query(
            ZsxqTopic.group_id,
            db.func.max(ZsxqTopic.group_name),
            db.func.count(ZsxqTopic.id),
            db.func.count(db.distinct(ZsxqTopic.tab_id)),
            db.func.max(ZsxqTopic.last_synced_at),
            db.func.max(ZsxqTopic.create_time),
        ).group_by(
            ZsxqTopic.group_id,
        ).order_by(
            db.func.max(ZsxqTopic.last_synced_at).desc(),
        ).all()

        groups = []
        for group_id, group_name, topic_count, tab_count, last_synced_at, latest_topic_time in rows:
            group_id = str(group_id or "").strip()
            if not group_id:
                continue
            groups.append({
                "group_id": group_id,
                "name": group_name or f"星球 {group_id}",
                "type": "local",
                "local_cached": True,
                "topic_count": int(topic_count or 0),
                "tab_count": int(tab_count or 0),
                "last_synced_at": last_synced_at.isoformat() if last_synced_at else None,
                "latest_topic_time": latest_topic_time,
            })

        return {
            "succeeded": True,
            "source": "local_db",
            "tables_ready": True,
            "resp_data": {
                "groups": groups,
                "total": len(groups),
            },
        }

    def list_cached_tabs(self, group_id):
        if not self._sync_tables_ready():
            return {"tabs": [], "source": "local_db", "tables_ready": False}

        group_id = str(group_id or "").strip()
        if not group_id:
            return {"tabs": [], "source": "local_db", "tables_ready": True}

        rows = db.session.query(
            ZsxqTopic.tab_id,
            ZsxqTopic.tab_label,
            db.func.count(ZsxqTopic.id),
            db.func.max(ZsxqTopic.last_synced_at),
        ).filter(
            ZsxqTopic.group_id == group_id,
        ).group_by(
            ZsxqTopic.tab_id,
            ZsxqTopic.tab_label,
        ).order_by(
            db.func.max(ZsxqTopic.last_synced_at).desc(),
        ).all()

        tabs = []
        for tab_id, tab_label, topic_count, last_synced_at in rows:
            tab_id = str(tab_id or "").strip()
            if not tab_id:
                continue
            tabs.append({
                "id": tab_id,
                "tab_id": tab_id,
                "label": tab_label or tab_id,
                "tab_label": tab_label or tab_id,
                "topic_count": int(topic_count or 0),
                "last_synced_at": last_synced_at.isoformat() if last_synced_at else None,
            })
        return {
            "tabs": tabs,
            "source": "local_db",
            "tables_ready": True,
        }

    def list_cached_topics(
        self,
        group_id,
        tab_id=None,
        count=20,
        end_time=None,
        publish_filter=None,
        publish_date=None,
        publish_date_start=None,
        publish_date_end=None,
        keyword=None,
        favorite_only=False,
        include_raw_payload=False,
    ):
        if not self._sync_tables_ready():
            return {
                "succeeded": True,
                "source": "local_db",
                "tables_ready": False,
                "resp_data": {
                    "topics": [],
                    "next_end_time": None,
                    "has_more": False,
                    "message": "本地同步表尚未初始化。",
                },
            }

        group_id = str(group_id or "").strip()
        tab_id = str(tab_id or "").strip()
        if not group_id:
            return {
                "succeeded": True,
                "source": "local_db",
                "tables_ready": True,
                "resp_data": {
                    "topics": [],
                    "next_end_time": None,
                    "has_more": False,
                    "message": "group_id 不能为空。",
                },
            }

        try:
            limit = min(max(int(count or 20), 1), 100)
        except (TypeError, ValueError):
            limit = 20

        base_query = ZsxqTopic.query.filter(ZsxqTopic.group_id == group_id)
        if tab_id:
            base_query = base_query.filter(ZsxqTopic.tab_id == tab_id)
        favorite_only = bool(favorite_only)
        if favorite_only:
            favorite_topic_ids = db.session.query(ZsxqTopicFavorite.topic_id).filter(
                ZsxqTopicFavorite.group_id == group_id,
            )
            base_query = base_query.filter(ZsxqTopic.topic_id.in_(favorite_topic_ids))
        publish_date = str(publish_date or "").strip()
        publish_date_start = str(publish_date_start or publish_date or "").strip()
        publish_date_end = str(publish_date_end or publish_date or "").strip()
        parsed_publish_dates = {}
        for field_name, field_value in (
            ("publish_date_start", publish_date_start),
            ("publish_date_end", publish_date_end),
        ):
            if not field_value:
                continue
            try:
                parsed_publish_dates[field_name] = datetime.strptime(field_value, "%Y-%m-%d")
            except ValueError as exc:
                raise ValueError(f"{field_name} 必须是 YYYY-MM-DD 格式") from exc
        if publish_date_start and publish_date_end and publish_date_start > publish_date_end:
            raise ValueError("publish_date_start 不能晚于 publish_date_end")
        if publish_date_start:
            base_query = base_query.filter(ZsxqTopic.create_time >= publish_date_start)
        if publish_date_end:
            exclusive_end = parsed_publish_dates["publish_date_end"] + timedelta(days=1)
            base_query = base_query.filter(ZsxqTopic.create_time < exclusive_end.strftime("%Y-%m-%d"))

        keyword = str(keyword or "").strip()[:200]
        if keyword:
            escaped_keyword = keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern = f"%{escaped_keyword}%"
            base_query = base_query.filter(or_(
                ZsxqTopic.title.ilike(pattern, escape="\\"),
                ZsxqTopic.author_name.ilike(pattern, escape="\\"),
                ZsxqTopic.content.ilike(pattern, escape="\\"),
                ZsxqTopic.question_text.ilike(pattern, escape="\\"),
                ZsxqTopic.answer_text.ilike(pattern, escape="\\"),
                ZsxqTopic.article_title.ilike(pattern, escape="\\"),
                ZsxqTopic.article_excerpt.ilike(pattern, escape="\\"),
            ))
        attention_filter = str(publish_filter or "").strip().lower() == "attention"
        if attention_filter:
            attachment_query = db.session.query(ZsxqAttachment.topic_id).filter(
                ZsxqAttachment.group_id == group_id,
                ZsxqAttachment.download_status == "downloaded",
                or_(
                    ZsxqAttachment.publish_status.in_(["publish_failed", "publish_skipped"]),
                    and_(
                        ZsxqAttachment.publish_status == "published",
                        or_(
                            ZsxqAttachment.publish_topic_id.is_(None),
                            ZsxqAttachment.publish_topic_id == "",
                        ),
                    ),
                ),
            )
            if tab_id:
                attachment_query = attachment_query.filter(ZsxqAttachment.tab_id == tab_id)
            base_query = base_query.filter(ZsxqTopic.topic_id.in_(attachment_query))

        page_query = base_query
        if end_time:
            page_query = page_query.filter(ZsxqTopic.create_time < str(end_time))

        filters_applied = favorite_only or attention_filter or bool(publish_date_start) or bool(publish_date_end) or bool(keyword)
        total = (
            base_query.with_entities(db.func.count(ZsxqTopic.id)).scalar()
            if filters_applied
            else self._cached_topic_total(group_id, tab_id)
        )
        rows = page_query.options(
            load_only(
                ZsxqTopic.id,
                ZsxqTopic.group_id,
                ZsxqTopic.group_name,
                ZsxqTopic.tab_id,
                ZsxqTopic.tab_label,
                ZsxqTopic.topic_id,
                ZsxqTopic.topic_uid,
                ZsxqTopic.title,
                ZsxqTopic.author_name,
                ZsxqTopic.content,
                ZsxqTopic.topic_type,
                ZsxqTopic.question_text,
                ZsxqTopic.answer_text,
                ZsxqTopic.article_title,
                ZsxqTopic.article_excerpt,
                ZsxqTopic.files_count,
                ZsxqTopic.images_count,
                ZsxqTopic.create_time,
                ZsxqTopic.raw_json_path,
                ZsxqTopic.first_sync_run_id,
                ZsxqTopic.last_sync_run_id,
                ZsxqTopic.last_synced_at,
            )
        ).order_by(
            ZsxqTopic.create_time.desc(),
            ZsxqTopic.last_synced_at.desc(),
        ).limit(limit + 1).all()
        has_more = len(rows) > limit
        topics = rows[:limit]
        if include_raw_payload:
            self._hydrate_topic_raw_payloads(topics)
        else:
            # Raw topic JSON is already stored on local disk. Reading the page
            # files exposes comments and images without triggering one deferred
            # JSON-column query per topic.
            self._hydrate_topic_raw_payloads(topics, allow_db_fallback=False)
        attachments = self._attachments_by_topic(
            group_id,
            topics,
            include_raw_payload=True,
        )
        external_links = self._external_links_for_cached_topics(group_id, topics)
        favorite_ids = {
            str(row[0])
            for row in db.session.query(ZsxqTopicFavorite.topic_id).filter(
                ZsxqTopicFavorite.group_id == group_id,
                ZsxqTopicFavorite.topic_id.in_([topic.topic_id for topic in topics]),
            ).all()
        } if topics else set()
        payloads = []
        for topic in topics:
            payload = self._public_cached_topic(topic, attachments.get((topic.tab_id, topic.topic_id), []))
            payload["is_favorite"] = str(topic.topic_id) in favorite_ids
            links = external_links.get((str(topic.tab_id), str(topic.topic_id))) or []
            if links:
                payload["external_documents"] = links
            payloads.append(payload)
        next_end_time = self._oldest_cached_topic_time(topics) if has_more else None
        message = ""
        if not payloads and not end_time and filters_applied:
            message = "当前日期和关键词条件下没有匹配主题。"
        elif not payloads and not end_time and not self._has_cached_topics(group_id, tab_id):
            message = "本地库暂无该 Tab 的主题。请先点击“同步当前 Tab”拉取元数据。"
        elif not payloads and end_time:
            message = "本地库已加载到底。"

        return {
            "succeeded": True,
            "source": "local_db",
            "tables_ready": True,
            "resp_data": {
                "topics": payloads,
                "count": len(payloads),
                "total": total,
                "next_end_time": next_end_time,
                "has_more": has_more,
                "favorite_total": db.session.query(db.func.count(ZsxqTopicFavorite.id)).filter(
                    ZsxqTopicFavorite.group_id == group_id,
                ).scalar() or 0,
                "message": message,
            },
        }

    def set_topic_favorite(self, group_id, topic_id, favorite=True):
        group_id = str(group_id or "").strip()
        topic_id = str(topic_id or "").strip()
        if not group_id or not topic_id:
            raise ValueError("group_id 和 topic_id 不能为空")
        topic_exists = ZsxqTopic.query.filter_by(group_id=group_id, topic_id=topic_id).first() is not None
        if not topic_exists:
            raise ValueError("本地库中未找到该主题")

        record = ZsxqTopicFavorite.query.filter_by(group_id=group_id, topic_id=topic_id).first()
        if favorite and not record:
            record = ZsxqTopicFavorite(group_id=group_id, topic_id=topic_id)
            db.session.add(record)
        elif not favorite and record:
            db.session.delete(record)
            record = None
        db.session.commit()
        total = db.session.query(db.func.count(ZsxqTopicFavorite.id)).filter(
            ZsxqTopicFavorite.group_id == group_id,
        ).scalar() or 0
        return {
            "group_id": group_id,
            "topic_id": topic_id,
            "is_favorite": bool(favorite),
            "favorite_total": int(total),
        }

    def _cached_topic_total(self, group_id, tab_id=None):
        if not tab_id:
            return None
        cursor = ZsxqSyncCursor.query.filter_by(
            group_id=str(group_id),
            tab_id=str(tab_id),
        ).first()
        if not cursor:
            return None
        try:
            return int(cursor.total_topics or 0)
        except (TypeError, ValueError):
            return None

    def _has_cached_topics(self, group_id, tab_id=None):
        query = ZsxqTopic.query.filter(ZsxqTopic.group_id == str(group_id))
        if tab_id:
            query = query.filter(ZsxqTopic.tab_id == str(tab_id))
        return query.with_entities(ZsxqTopic.id).limit(1).first() is not None

    def _oldest_cached_topic_time(self, topics):
        times = sorted(
            str(topic.create_time or "")
            for topic in topics or []
            if topic.create_time
        )
        return times[0] if times else None

    def _public_cached_topic(self, topic, attachments):
        raw_payload = getattr(topic, "_cached_raw_payload", None)
        if raw_payload is None:
            raw_payload = self._loaded_model_attr(topic, "raw_payload", {})
        raw_payload = raw_payload if isinstance(raw_payload, dict) else {}
        raw_talk = raw_payload.get("talk") if isinstance(raw_payload.get("talk"), dict) else {}
        raw_article = raw_payload.get("article") if isinstance(raw_payload.get("article"), dict) else {}
        raw_talk_article = raw_talk.get("article") if isinstance(raw_talk.get("article"), dict) else {}
        article = self._public_topic_article(raw_talk_article or raw_article)
        payload = {}
        payload.setdefault("topic_id", topic.topic_id)
        payload.setdefault("topic_uid", topic.topic_uid)
        payload.setdefault("title", topic.title)
        payload.setdefault("create_time", topic.create_time)
        payload.setdefault("raw_json_path", topic.raw_json_path)
        payload.setdefault("tab_id", topic.tab_id)
        payload.setdefault("tab_label", topic.tab_label)
        if topic.topic_type:
            payload.setdefault("topic_type", topic.topic_type)
        if raw_payload.get("annotation"):
            payload.setdefault("annotation", raw_payload.get("annotation"))
        comments = [
            deepcopy(item)
            for item in (raw_payload.get("show_comments") or [])
            if isinstance(item, dict)
        ]
        try:
            comments_count = max(int(raw_payload.get("comments_count") or 0), len(comments))
        except (TypeError, ValueError):
            comments_count = len(comments)
        payload["comments_count"] = comments_count
        payload["show_comments"] = comments
        if raw_payload.get("comments_truncated"):
            payload["comments_truncated"] = True
        if topic.author_name:
            payload.setdefault("owner", {"name": topic.author_name})

        content = self._content(raw_payload) if raw_payload else (topic.content or "")
        if content:
            payload["talk"] = {
                "text": content,
                "create_time": topic.create_time,
                "owner": {"name": topic.author_name or "未知作者"},
            }
        if isinstance(raw_payload.get("question"), dict):
            payload["question"] = {
                "text": self._normalize_topic_text(raw_payload["question"].get("text") or ""),
                "owner": raw_payload["question"].get("owner") or {},
            }
        elif topic.question_text:
            payload["question"] = {
                "text": topic.question_text,
                "owner": {"name": topic.author_name or "未知作者"},
            }
        if isinstance(raw_payload.get("answer"), dict):
            payload["answer"] = {
                "text": self._normalize_topic_text(raw_payload["answer"].get("text") or ""),
                "owner": raw_payload["answer"].get("owner") or {},
                "images": raw_payload["answer"].get("images") or [],
            }
        elif topic.answer_text:
            payload["answer"] = {
                "text": topic.answer_text,
                "owner": {"name": topic.author_name or "未知作者"},
                "images": [],
            }
        if article:
            payload.setdefault("talk", {})
            payload["talk"]["article"] = article
        elif topic.article_title or topic.article_excerpt:
            payload.setdefault("talk", {})
            payload["talk"]["article"] = {
                "title": topic.article_title,
                "article_content": topic.article_excerpt,
            }

        files = []
        images = [
            deepcopy(item) if isinstance(item, dict) else {"url": str(item)}
            for item in self._topic_feed_images(raw_payload)
            if item
        ]
        attachment_images = []
        answer_attachment_images = []
        unknown_attachment_images = []
        for attachment in attachments or []:
            item = dict(self._loaded_model_attr(attachment, "raw_payload", {}) or {})
            image_context = str(item.pop("_zsxq_image_context", "") or "").strip()
            item.setdefault("name", attachment.name)
            if attachment.file_id:
                item.setdefault("file_id", attachment.file_id)
            if attachment.download_count is not None:
                item.setdefault("download_count", attachment.download_count)
            item.setdefault("attachment_key", attachment.attachment_key)
            if attachment.attachment_type == "image":
                if self._image_payload_has_url(item):
                    if image_context == "answer":
                        answer_attachment_images.append(item)
                    elif image_context:
                        attachment_images.append(item)
                    else:
                        unknown_attachment_images.append(item)
            else:
                files.append(item)

        if files:
            payload["files"] = files
        if answer_attachment_images and payload.get("answer") is not None:
            payload["answer"]["images"] = self._dedupe_topic_images(
                (payload["answer"].get("images") or []) + answer_attachment_images
            )
        if unknown_attachment_images and not raw_payload and payload.get("answer") is not None:
            payload["answer"]["images"] = self._dedupe_topic_images(
                (payload["answer"].get("images") or []) + unknown_attachment_images
            )
        else:
            images.extend(unknown_attachment_images)
        images.extend(attachment_images)
        if images:
            payload["images"] = self._dedupe_topic_images(images)
        return payload

    def _dedupe_topic_images(self, images):
        unique = []
        seen = set()
        for image in images or []:
            item = image if isinstance(image, dict) else {"url": str(image)}
            original = item.get("original") if isinstance(item.get("original"), dict) else {}
            large = item.get("large") if isinstance(item.get("large"), dict) else {}
            thumbnail = item.get("thumbnail") if isinstance(item.get("thumbnail"), dict) else {}
            key = str(
                item.get("image_id")
                or item.get("file_id")
                or item.get("id")
                or item.get("hash_id")
                or item.get("url")
                or original.get("url")
                or large.get("url")
                or thumbnail.get("url")
                or ""
            )
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            unique.append(item)
        return unique

    def _topic_feed_images(self, raw_payload):
        if not isinstance(raw_payload, dict):
            return []
        talk = raw_payload.get("talk") if isinstance(raw_payload.get("talk"), dict) else {}
        article = raw_payload.get("article") if isinstance(raw_payload.get("article"), dict) else {}
        talk_article = talk.get("article") if isinstance(talk.get("article"), dict) else {}
        images = []
        for value in (
            raw_payload.get("images"),
            talk.get("images"),
            article.get("images"),
            talk_article.get("images"),
        ):
            if isinstance(value, list):
                images.extend(value)
        return images

    def _image_payload_has_url(self, item):
        if not isinstance(item, dict):
            return bool(item)
        for value in (
            item.get("url"),
            item.get("image_url"),
            item.get("raw_url"),
            item.get("source_url"),
            item.get("download_url"),
            item.get("src"),
            item.get("href"),
            item.get("link"),
        ):
            if str(value or "").strip():
                return True
        for key in ("thumbnail", "large", "original", "small", "file", "picture", "photo"):
            nested = item.get(key)
            if isinstance(nested, str) and nested.strip():
                return True
            if isinstance(nested, dict) and any(str(nested.get(field) or "").strip() for field in ("url", "src", "href", "link", "image_url")):
                return True
        return False

    def _hydrate_topic_raw_payloads(self, topics, *, allow_db_fallback=True):
        missing_topics = []
        for topic in topics or []:
            if not topic or not topic.id:
                continue
            payload = self._topic_raw_payload_from_path(getattr(topic, "raw_json_path", None))
            if isinstance(payload, dict):
                topic._cached_raw_payload = payload
            else:
                missing_topics.append(topic)

        topic_ids = [topic.id for topic in missing_topics if topic.id]
        if not topic_ids:
            return
        if not allow_db_fallback:
            for topic in missing_topics:
                topic._cached_raw_payload = {}
            return
        rows = (
            db.session.query(ZsxqTopic.id, ZsxqTopic.raw_payload)
            .filter(ZsxqTopic.id.in_(topic_ids))
            .all()
        )
        raw_by_id = {
            row.id: row.raw_payload
            for row in rows
            if isinstance(row.raw_payload, dict)
        }
        for topic in missing_topics:
            topic._cached_raw_payload = raw_by_id.get(topic.id) or {}

    def _topic_raw_payload_from_path(self, raw_json_path):
        if not raw_json_path:
            return None
        try:
            path = Path(str(raw_json_path)).expanduser()
            if not path.is_absolute():
                path = self._project_root() / path
            resolved = path.resolve()
            allowed_roots = [self._project_root().resolve(), self._data_root().resolve()]
            if not any(resolved.is_relative_to(root) for root in allowed_roots):
                return None
            if not resolved.is_file() or resolved.stat().st_size <= 0:
                return None
            payload = json.loads(resolved.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else None
        except Exception:
            return None

    def _loaded_model_attr(self, record, name, default=None):
        try:
            state = inspect(record)
            if name in state.unloaded:
                return default
        except Exception:
            pass
        try:
            return getattr(record, name, default)
        except Exception:
            return default

    def _external_links_for_cached_topics(self, group_id, topics):
        try:
            from services.external_document_service import external_document_service

            return external_document_service.links_for_topics(group_id, topics)
        except Exception as exc:
            logger.warning("[ZSXQ_SYNC] external document links skipped | group_id=%s | error=%s", group_id, exc)
            return {}

    def _scan_external_document_links(self, topic_record):
        if str(os.environ.get("ZSXQ_EXTERNAL_LINK_SCAN_ENABLED", "1")).strip().lower() in {"0", "false", "no", "off"}:
            return
        try:
            from services.external_document_service import external_document_service

            external_document_service.scan_topic_record(topic_record, commit=False)
        except Exception as exc:
            logger.warning(
                "[ZSXQ_SYNC] external document scan skipped | group_id=%s | tab=%s | topic_id=%s | error=%s",
                getattr(topic_record, "group_id", None),
                getattr(topic_record, "tab_id", None),
                getattr(topic_record, "topic_id", None),
                exc,
            )

    def _public_topic_article(self, article):
        if not isinstance(article, dict):
            return None
        url = (
            article.get("article_url")
            or article.get("url")
            or article.get("link")
            or article.get("inline_article_url")
        )
        if not url:
            return None
        payload = {
            "title": article.get("title") or "查看原文",
            "article_url": str(url),
        }
        if article.get("article_id"):
            payload["article_id"] = article.get("article_id")
        if article.get("inline_article_url"):
            payload["inline_article_url"] = article.get("inline_article_url")
        return payload

    def get_attachment_statuses(self, group_id, tab_id, topic_ids, attachment_refs=None):
        if not self._sync_tables_ready():
            return {}

        group_id = str(group_id)
        tab_id = str(tab_id)
        topic_ids = [str(item) for item in (topic_ids or []) if item]
        if not topic_ids:
            return {}

        cache_key = self._attachment_status_cache_key(group_id, tab_id, topic_ids[:200], attachment_refs)
        cached = self._get_cache_value(self._attachment_status_cache, cache_key)
        if cached is not None:
            return deepcopy(cached)

        status_columns = (
            ZsxqAttachment.id,
            ZsxqAttachment.group_id,
            ZsxqAttachment.tab_id,
            ZsxqAttachment.topic_id,
            ZsxqAttachment.attachment_key,
            ZsxqAttachment.attachment_type,
            ZsxqAttachment.name,
            ZsxqAttachment.file_id,
            ZsxqAttachment.download_count,
            ZsxqAttachment.local_path,
            ZsxqAttachment.file_ext,
            ZsxqAttachment.download_status,
            ZsxqAttachment.error_message,
            ZsxqAttachment.publish_status,
            ZsxqAttachment.publish_task_id,
            ZsxqAttachment.publish_source_task_id,
            ZsxqAttachment.publish_topic_id,
            ZsxqAttachment.publish_submitted_count,
            ZsxqAttachment.publish_verified_count,
            ZsxqAttachment.publish_failed_count,
            ZsxqAttachment.publish_updated_at,
            ZsxqAttachment.created_at,
            ZsxqAttachment.updated_at,
        )
        rows = ZsxqAttachment.query.filter(
            ZsxqAttachment.group_id == group_id,
            ZsxqAttachment.tab_id == tab_id,
            ZsxqAttachment.topic_id.in_(topic_ids[:200]),
        ).options(load_only(*status_columns)).all()

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
            ).options(load_only(*status_columns)).all()
            for row in fallback_rows:
                add_row(row)

        self._add_attachment_ref_statuses(
            result=result,
            seen_keys=seen_keys,
            group_id=group_id,
            tab_id=tab_id,
            attachment_refs=attachment_refs,
        )
        self._add_attachment_publish_statuses(result)
        self._set_cache_value(
            self._attachment_status_cache,
            cache_key,
            result,
            ATTACHMENT_STATUS_CACHE_TTL_SECONDS,
            MAX_ATTACHMENT_STATUS_CACHE_ENTRIES,
        )
        return result

    def _attachment_status_cache_key(self, group_id, tab_id, topic_ids, attachment_refs=None):
        refs = []
        for item in attachment_refs or []:
            if not isinstance(item, dict):
                continue
            refs.append({
                "topic_id": str(item.get("topic_id") or ""),
                "attachment_key": str(item.get("attachment_key") or ""),
                "file_id": str(item.get("file_id") or ""),
                "name": str(item.get("name") or ""),
            })
        payload = {
            "group_id": str(group_id or ""),
            "tab_id": str(tab_id or ""),
            "topic_ids": list(topic_ids or []),
            "refs": refs,
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()
        return f"{group_id}:{tab_id}:{digest}"

    def _get_cache_value(self, cache, key):
        item = cache.get(key)
        if not item:
            return None
        if float(item.get("expires_at") or 0) <= time.time():
            cache.pop(key, None)
            return None
        return item.get("value")

    def _set_cache_value(self, cache, key, value, ttl_seconds, max_entries=128):
        if len(cache) >= max_entries:
            now = time.time()
            expired_keys = [
                cache_key
                for cache_key, item in cache.items()
                if float(item.get("expires_at") or 0) <= now
            ]
            for cache_key in expired_keys:
                cache.pop(cache_key, None)
            if len(cache) >= max_entries:
                oldest_key = min(cache.items(), key=lambda pair: pair[1].get("expires_at") or 0)[0]
                cache.pop(oldest_key, None)
        cache[key] = {
            "expires_at": time.time() + max(float(ttl_seconds or 0), 1),
            "value": deepcopy(value),
        }

    def _clear_attachment_status_cache(self):
        self._attachment_status_cache.clear()

    def _add_attachment_publish_statuses(self, result):
        payloads = [
            item
            for rows in (result or {}).values()
            for item in (rows or [])
            if isinstance(item, dict)
        ]
        if not payloads:
            return
        missing_payloads = [item for item in payloads if not item.get("publish_status")]
        if not missing_payloads:
            return
        if not self._attachment_publish_fallback_enabled():
            return

        match_index = self._attachment_publish_match_index(missing_payloads)
        if not any(match_index.values()):
            return

        for context in self._recent_publish_status_context():
            status = context.get("status")
            outcomes = context.get("outcomes") or {"by_path": {}, "by_name": {}}
            for source_ref in context.get("source_refs") or []:
                source_status = self._status_for_source_ref(source_ref, status, outcomes)
                if not source_status:
                    continue
                for payload in self._matched_attachment_payloads(source_ref, match_index):
                    self._merge_publish_status(payload, source_status)

    def _attachment_publish_fallback_enabled(self):
        config_value = current_app.config.get("ZSXQ_ATTACHMENT_PUBLISH_FALLBACK_ENABLED") if has_app_context() else None
        if config_value is not None:
            return self._truthy(config_value)
        return self._truthy(os.getenv(ATTACHMENT_PUBLISH_FALLBACK_ENV))

    def _recent_publish_status_context(self):
        now = time.time()
        cached = self._publish_status_context_cache
        if float(cached.get("expires_at") or 0) > now:
            return deepcopy(cached.get("items") or [])

        try:
            recent_tasks = PublicationTask.query.options(
                load_only(
                    PublicationTask.id,
                    PublicationTask.feature,
                    PublicationTask.source_task_id,
                    PublicationTask.status,
                    PublicationTask.params,
                    PublicationTask.result,
                    PublicationTask.created_at,
                    PublicationTask.updated_at,
                    PublicationTask.completed_at,
                )
            ).filter(
                PublicationTask.feature == "zsxq_clean_output_publish"
            ).order_by(PublicationTask.created_at.desc()).limit(80).all()
        except Exception as exc:
            db.session.rollback()
            logger.warning("[ZSXQ_SYNC] attachment publish status lookup failed: %s", exc)
            return []

        publish_tasks = recent_tasks
        if not publish_tasks:
            self._publish_status_context_cache = {
                "expires_at": now + PUBLISH_STATUS_CONTEXT_CACHE_TTL_SECONDS,
                "items": [],
            }
            return []

        source_task_ids = [
            source_task_id
            for task in publish_tasks
            if not self._publish_task_source_refs(task)
            for source_task_id in [self._publish_source_task_id(task)]
            if source_task_id
        ]
        clean_tasks = []
        if source_task_ids:
            try:
                clean_tasks = PublicationTask.query.options(
                    load_only(
                        PublicationTask.id,
                        PublicationTask.params,
                        PublicationTask.result,
                    )
                ).filter(PublicationTask.id.in_(list(dict.fromkeys(source_task_ids)))).all()
            except Exception as exc:
                db.session.rollback()
                logger.warning("[ZSXQ_SYNC] attachment publish source lookup failed: %s", exc)
                return []

        clean_task_by_id = {str(task.id): task for task in clean_tasks}
        contexts = []
        for publish_task in publish_tasks:
            source_task_id = self._publish_source_task_id(publish_task)
            clean_task = clean_task_by_id.get(source_task_id)
            if not clean_task and not self._publish_task_source_refs(publish_task):
                continue
            status = self._publish_task_status_snapshot(publish_task)
            if not status:
                continue
            contexts.append({
                "status": status,
                "outcomes": self._publish_file_outcomes(publish_task),
                "source_refs": self._publish_task_source_refs(publish_task)
                or self._clean_task_source_refs(clean_task),
            })

        self._publish_status_context_cache = {
            "expires_at": time.time() + PUBLISH_STATUS_CONTEXT_CACHE_TTL_SECONDS,
            "items": deepcopy(contexts),
        }
        return contexts

    def update_attachment_publish_status_from_publish_task(self, publish_task_or_id):
        if not self._sync_tables_ready():
            return {"updated": 0, "matched": 0, "reason": "schema_not_ready"}

        publish_task = publish_task_or_id
        is_publish_task_snapshot = (
            not isinstance(publish_task_or_id, (str, bytes, int))
            and getattr(publish_task_or_id, "id", None) is not None
            and hasattr(publish_task_or_id, "result")
        )
        if not isinstance(publish_task_or_id, PublicationTask) and not is_publish_task_snapshot:
            publish_task = (
                PublicationTask.query.options(load_only(*PUBLISH_TASK_CONTEXT_COLUMNS))
                .filter(PublicationTask.id == str(publish_task_or_id))
                .first()
            )
        if not publish_task:
            return {"updated": 0, "matched": 0, "reason": "publish_task_not_found"}
        if self._task_feature(publish_task) != "zsxq_clean_output_publish":
            return {"updated": 0, "matched": 0, "reason": "not_zsxq_publish_task"}

        source_task_id = self._publish_source_task_id(publish_task)
        if not source_task_id:
            return {"updated": 0, "matched": 0, "reason": "missing_source_task_id"}

        clean_task = None
        if not self._publish_task_source_refs(publish_task):
            clean_task = (
                PublicationTask.query.options(load_only(*CLEAN_TASK_CONTEXT_COLUMNS))
                .filter(PublicationTask.id == source_task_id)
                .first()
            )
            if not clean_task:
                return {"updated": 0, "matched": 0, "reason": "source_task_not_found"}

        context = self._publish_task_context(publish_task, clean_task)
        if not context:
            return {"updated": 0, "matched": 0, "reason": "no_publish_context"}

        result = self._apply_publish_context_to_attachments(context)
        db.session.commit()
        self._clear_attachment_status_cache()
        self._publish_status_context_cache = {"expires_at": 0, "items": []}
        return result

    def backfill_attachment_publish_statuses(self, limit=240):
        if not self._sync_tables_ready():
            return {"updated": 0, "matched": 0, "tasks": 0, "reason": "schema_not_ready"}
        try:
            max_limit = min(max(int(limit or 240), 1), 1000)
        except (TypeError, ValueError):
            max_limit = 240

        recent_tasks = PublicationTask.query.options(
            load_only(
                PublicationTask.id,
                PublicationTask.feature,
                PublicationTask.source_task_id,
                PublicationTask.status,
                PublicationTask.params,
                PublicationTask.result,
                PublicationTask.created_at,
                PublicationTask.updated_at,
                PublicationTask.completed_at,
            )
        ).filter(
            PublicationTask.feature == "zsxq_clean_output_publish"
        ).order_by(PublicationTask.created_at.desc()).limit(max_limit).all()
        publish_tasks = recent_tasks

        totals = {"updated": 0, "matched": 0, "tasks": len(publish_tasks)}
        for task in publish_tasks:
            result = self.update_attachment_publish_status_from_publish_task(task)
            totals["updated"] += int(result.get("updated") or 0)
            totals["matched"] += int(result.get("matched") or 0)
        return totals

    def _publish_task_context(self, publish_task, clean_task):
        status = self._publish_task_status_snapshot(publish_task)
        if not status:
            return None
        return {
            "status": status,
            "outcomes": self._publish_file_outcomes(publish_task),
            "source_refs": self._publish_task_source_refs(publish_task)
            or self._clean_task_source_refs(clean_task),
        }

    def _publish_task_source_refs(self, publish_task):
        params = publish_task.params if isinstance(getattr(publish_task, "params", None), dict) else {}
        publish = params.get("zsxq_publish") if isinstance(params.get("zsxq_publish"), dict) else {}
        metadata = publish.get("metadata") if isinstance(publish.get("metadata"), dict) else {}
        raw_refs = metadata.get("source_refs") if isinstance(metadata.get("source_refs"), list) else []
        refs = []
        for item in raw_refs[:100]:
            if not isinstance(item, dict):
                continue
            refs.append({
                "attachment_id": str(item.get("attachment_id") or "").strip(),
                "group_id": str(item.get("group_id") or "").strip(),
                "tab_id": str(item.get("tab_id") or "").strip(),
                "topic_id": str(item.get("topic_id") or "").strip(),
                "source_path": self._path_key(item.get("source_path")),
                "name": str(item.get("name") or "").strip(),
                "output_paths": [
                    path
                    for value in (item.get("output_paths") or [])[:20]
                    for path in [self._path_key(value)]
                    if path
                ],
            })
        return refs

    def _apply_publish_context_to_attachments(self, context):
        status = context.get("status") or {}
        outcomes = context.get("outcomes") or {"by_path": {}, "by_name": {}}
        source_refs = context.get("source_refs") or []
        attachment_candidates = self._attachment_records_for_source_refs(source_refs)
        seen_ids = set()
        matched = 0
        updated = 0
        for source_ref, attachments in zip(source_refs, attachment_candidates):
            source_status = self._status_for_source_ref(source_ref, status, outcomes)
            if not source_status:
                continue
            for attachment in attachments:
                if attachment.id in seen_ids:
                    continue
                seen_ids.add(attachment.id)
                matched += 1
                if self._apply_publish_status_to_attachment(attachment, source_status):
                    updated += 1
        return {"updated": updated, "matched": matched}

    def _attachment_records_for_source_refs(self, source_refs):
        source_refs = list(source_refs or [])
        if not source_refs:
            return []

        attachment_ids = set()
        fallback_conditions = {}
        for source_ref in source_refs:
            attachment_id = str(source_ref.get("attachment_id") or "").strip()
            if attachment_id:
                attachment_ids.add(attachment_id)

            group_id = str(source_ref.get("group_id") or "").strip()
            tab_id = str(source_ref.get("tab_id") or "").strip()
            topic_id = str(source_ref.get("topic_id") or "").strip()
            name = str(source_ref.get("name") or "").strip()
            if group_id and tab_id and topic_id:
                key = (group_id, tab_id, topic_id, name)
                fallback_conditions[key] = and_(
                    ZsxqAttachment.group_id == group_id,
                    ZsxqAttachment.tab_id == tab_id,
                    ZsxqAttachment.topic_id == topic_id,
                    *([ZsxqAttachment.name == name] if name else []),
                )

        conditions = list(fallback_conditions.values())
        if attachment_ids:
            conditions.append(ZsxqAttachment.id.in_(attachment_ids))
        if not conditions:
            return [[] for _source_ref in source_refs]

        records = (
            ZsxqAttachment.query.options(
                load_only(
                    ZsxqAttachment.id,
                    ZsxqAttachment.group_id,
                    ZsxqAttachment.tab_id,
                    ZsxqAttachment.topic_id,
                    ZsxqAttachment.attachment_key,
                    ZsxqAttachment.name,
                    ZsxqAttachment.local_path,
                    ZsxqAttachment.publish_status,
                    ZsxqAttachment.publish_task_id,
                    ZsxqAttachment.publish_source_task_id,
                    ZsxqAttachment.publish_topic_id,
                    ZsxqAttachment.publish_submitted_count,
                    ZsxqAttachment.publish_verified_count,
                    ZsxqAttachment.publish_failed_count,
                    ZsxqAttachment.publish_updated_at,
                )
            )
            .filter(or_(*conditions))
            .all()
        )
        by_id = {str(record.id): record for record in records}
        by_topic = {}
        for record in records:
            key = (
                str(record.group_id or ""),
                str(record.tab_id or ""),
                str(record.topic_id or ""),
            )
            by_topic.setdefault(key, []).append(record)

        matches = []
        for source_ref in source_refs:
            attachment_id = str(source_ref.get("attachment_id") or "").strip()
            if attachment_id and attachment_id in by_id:
                matches.append([by_id[attachment_id]])
                continue

            topic_key = (
                str(source_ref.get("group_id") or "").strip(),
                str(source_ref.get("tab_id") or "").strip(),
                str(source_ref.get("topic_id") or "").strip(),
            )
            candidates = by_topic.get(topic_key, [])
            name = str(source_ref.get("name") or "").strip()
            if name:
                candidates = [record for record in candidates if record.name == name]
            candidates = candidates[:20]
            source_path = source_ref.get("source_path")
            if source_path:
                exact = [
                    record
                    for record in candidates
                    if self._path_key(record.local_path) == source_path
                ]
                if exact:
                    candidates = exact
            matches.append(candidates)
        return matches

    def _apply_publish_status_to_attachment(self, attachment, status):
        incoming = str(status.get("publish_status") or "")
        if not incoming:
            return False
        if incoming == "published" and not str(status.get("publish_topic_id") or "").strip():
            incoming = "submitted"
        current = str(attachment.publish_status or "")
        if not self._should_replace_attachment_publish_status(attachment, incoming):
            return False

        updated_at = status.get("publish_updated_at_dt")
        changed = False
        updates = {
            "publish_status": incoming,
            "publish_task_id": status.get("publish_task_id"),
            "publish_source_task_id": status.get("publish_source_task_id"),
            "publish_topic_id": status.get("publish_topic_id") or attachment.publish_topic_id,
            "publish_submitted_count": int(status.get("publish_submitted_count") or 0),
            "publish_verified_count": int(status.get("publish_verified_count") or 0),
            "publish_failed_count": int(status.get("publish_failed_count") or 0),
            "publish_updated_at": updated_at or datetime.utcnow(),
        }
        for field, value in updates.items():
            if getattr(attachment, field) != value:
                setattr(attachment, field, value)
                changed = True
        return changed

    def _should_replace_attachment_publish_status(self, attachment, incoming):
        current = str(attachment.publish_status or "")
        if current == "published" and not attachment.publish_topic_id and incoming in {"publish_failed", "publish_skipped"}:
            return True
        return self._should_replace_publish_status(current, incoming)

    def _should_replace_publish_status(self, current, incoming):
        rank = {
            "publish_skipped": 1,
            "publish_failed": 1,
            "publishing": 2,
            "submitted": 3,
            "published": 4,
        }
        if current == "publishing" and incoming == "publish_failed":
            return True
        return rank.get(current, 0) <= rank.get(incoming, 0)

    def _attachment_publish_match_index(self, payloads):
        index = {"attachment_id": {}, "source_path": {}, "name": {}}
        for payload in payloads:
            attachment_id = str(payload.get("id") or "").strip()
            if attachment_id:
                index["attachment_id"].setdefault(attachment_id, []).append(payload)

            local_path = self._path_key(payload.get("local_path"))
            if local_path:
                index["source_path"].setdefault(local_path, []).append(payload)

            name = str(payload.get("name") or "").strip()
            if name:
                index["name"].setdefault(name, []).append(payload)
        return index

    def _matched_attachment_payloads(self, source_ref, match_index):
        for key_type, value in (
            ("attachment_id", source_ref.get("attachment_id")),
            ("source_path", source_ref.get("source_path")),
            ("name", source_ref.get("name")),
        ):
            if not value:
                continue
            matched = match_index.get(key_type, {}).get(str(value), [])
            if matched:
                return matched
        return []

    def _clean_task_source_refs(self, clean_task):
        if not clean_task:
            return []
        params = clean_task.params if isinstance(clean_task.params, dict) else {}
        pipeline = params.get("content_pipeline") if isinstance(params.get("content_pipeline"), dict) else {}
        metadata = pipeline.get("metadata") if isinstance(pipeline.get("metadata"), dict) else {}
        source_files = metadata.get("files") if isinstance(metadata.get("files"), list) else []
        output_paths_by_source = self._clean_output_paths_by_source(clean_task)
        default_group_id = str(metadata.get("group_id") or "").strip()

        refs = []
        for item in source_files:
            if not isinstance(item, dict):
                continue
            path_key = self._path_key(item.get("path"))
            attachment_id = str(item.get("attachment_id") or "").strip()
            name = str(item.get("name") or "").strip()
            if not name and path_key:
                name = Path(path_key).name
            refs.append({
                "attachment_id": attachment_id,
                "group_id": str(item.get("group_id") or default_group_id or "").strip(),
                "tab_id": str(item.get("tab_id") or "").strip(),
                "topic_id": str(item.get("topic_id") or "").strip(),
                "source_path": path_key,
                "name": name,
                "output_paths": output_paths_by_source.get(path_key, []) if path_key else [],
            })
        return refs

    def _clean_output_paths_by_source(self, clean_task):
        params = clean_task.params if isinstance(clean_task.params, dict) else {}
        pipeline = params.get("content_pipeline") if isinstance(params.get("content_pipeline"), dict) else {}
        result = clean_task.result if isinstance(clean_task.result, dict) else {}
        source_root = self._path_key(
            ((pipeline.get("pdf_clean") or {}).get("source_root") if isinstance(pipeline.get("pdf_clean"), dict) else None)
            or ((pipeline.get("audio_clean") or {}).get("source_root") if isinstance(pipeline.get("audio_clean"), dict) else None)
        )

        output_paths_by_source = {}
        for item in (result.get("cleaned_files") or result.get("files") or []):
            if not isinstance(item, dict):
                continue
            source_key = self._clean_result_source_key(item, source_root)
            output_key = self._path_key(
                item.get("cleaner_output_path")
                or item.get("output_path")
                or item.get("path")
            )
            if source_key and output_key:
                output_paths_by_source.setdefault(source_key, []).append(output_key)
        return output_paths_by_source

    def _clean_result_source_key(self, item, source_root=None):
        source_value = item.get("source_path") or item.get("source")
        if not source_value:
            return None
        source_text = str(source_value)
        if source_root and not Path(source_text).is_absolute():
            return self._path_key(Path(source_root) / source_text)
        return self._path_key(source_text)

    def _publish_file_outcomes(self, publish_task):
        result = publish_task.result if isinstance(publish_task.result, dict) else {}
        by_path = {}
        by_name = {}
        task_snapshot = self._publish_task_status_snapshot(publish_task) or {}
        for rule in self._iter_publish_result_rules(result):
            rule_status = self._publish_rule_status(rule)
            for file_item in rule.get("files") or []:
                if not isinstance(file_item, dict):
                    continue
                file_status = str(file_item.get("status") or "").lower()
                status = {
                    "failed": "publish_failed",
                    "publish_failed": "publish_failed",
                    "skipped": "publish_skipped",
                    "publish_skipped": "publish_skipped",
                    "uploaded": rule_status,
                    "published": "published",
                }.get(file_status, rule_status)
                if not status:
                    continue
                snapshot = {
                    **task_snapshot,
                    "publish_status": status,
                    "publish_file_status": file_status,
                    "publish_skip_reason": file_item.get("skip_reason") or file_item.get("reason") or "",
                    "publish_topic_id": str(rule.get("topic_id") or ""),
                }
                path_key = self._path_key(file_item.get("path"))
                if path_key:
                    by_path[path_key] = snapshot
                name = str(file_item.get("name") or "").strip()
                if name:
                    by_name[name] = snapshot
        for file_item in self._iter_publish_preview_skipped_files(result):
            snapshot = {
                **task_snapshot,
                "publish_status": "publish_skipped",
                "publish_file_status": "skipped",
                "publish_skip_reason": file_item.get("skip_reason") or file_item.get("reason") or "",
                "publish_topic_id": "",
            }
            path_key = self._path_key(file_item.get("path"))
            if path_key and path_key not in by_path:
                by_path[path_key] = snapshot
            name = str(file_item.get("name") or "").strip()
            if name and name not in by_name:
                by_name[name] = snapshot
        return {"by_path": by_path, "by_name": by_name}

    def _iter_publish_result_rules(self, result):
        containers = []
        if isinstance(result, dict):
            containers.append(result)
            publish_result = result.get("publish_result")
            if isinstance(publish_result, dict):
                containers.append(publish_result)

        for container in containers:
            rounds = container.get("rounds") if isinstance(container.get("rounds"), list) else [container]
            for run in rounds:
                if not isinstance(run, dict):
                    continue
                for rule in run.get("rules") or []:
                    if isinstance(rule, dict):
                        yield rule

    def _publish_rule_status(self, rule):
        verified = self._safe_int(rule.get("verified_count"))
        submitted = self._safe_int(rule.get("submitted_count"))
        uploaded = self._safe_int(rule.get("uploaded_count"))
        failed = self._safe_int(rule.get("failed_count"))
        status = str(rule.get("status") or "").lower()
        if verified > 0 and verified >= max(submitted, uploaded, 1):
            return "published"
        if submitted > 0 or uploaded > 0 or status in {"success", "partial"}:
            return "submitted"
        if failed > 0 or status == "failed":
            return "publish_failed"
        if status == "skipped":
            return "publish_skipped"
        return ""

    def _iter_publish_preview_skipped_files(self, result):
        preview = result.get("preview") if isinstance(result.get("preview"), dict) else {}
        rules = preview.get("rules") if isinstance(preview.get("rules"), list) else []
        for rule in rules:
            for item in rule.get("skipped") or []:
                reason = str((item or {}).get("reason") or (item or {}).get("skip_reason") or "").strip().lower()
                if isinstance(item, dict) and reason not in {"published"}:
                    yield item

    def _status_for_source_ref(self, source_ref, task_status, outcomes):
        for output_path in source_ref.get("output_paths") or []:
            matched = outcomes.get("by_path", {}).get(output_path)
            if matched:
                return matched
            output_name = Path(output_path).name if output_path else ""
            matched = outcomes.get("by_name", {}).get(output_name)
            if matched:
                return matched
        if source_ref.get("output_paths"):
            return None
        return task_status

    def _publish_task_status_snapshot(self, task):
        result = task.result if isinstance(task.result, dict) else {}
        summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
        verified = self._safe_int(result.get("verified_count") or summary.get("verified_count"))
        submitted = self._safe_int(result.get("submitted_count") or summary.get("submitted_count"))
        uploaded = self._safe_int(result.get("uploaded_count") or summary.get("uploaded_count"))
        failed = self._safe_int(result.get("failed_count") or summary.get("failed_count"))
        raw_status = str(result.get("status") or task.status or "").lower()

        if verified > 0:
            publish_status = "published"
        elif submitted > 0 or uploaded > 0:
            publish_status = "submitted"
        elif task.status in {"pending", "processing"}:
            publish_status = "publishing"
        elif failed > 0 or raw_status == "failed" or task.status == "failed":
            publish_status = "publish_failed"
        elif raw_status == "skipped" or str(result.get("reason") or "").lower() in {"no_eligible_files", "disabled"}:
            publish_status = "publish_skipped"
        else:
            return None

        return {
            "publish_status": publish_status,
            "publish_task_id": task.id,
            "publish_source_task_id": self._publish_source_task_id(task),
            "publish_verified_count": verified,
            "publish_submitted_count": submitted,
            "publish_uploaded_count": uploaded,
            "publish_failed_count": failed,
            "publish_updated_at_dt": task.completed_at or task.updated_at,
            "publish_updated_at": task.completed_at.isoformat() if task.completed_at else (
                task.updated_at.isoformat() if task.updated_at else None
            ),
        }

    def _merge_publish_status(self, payload, status):
        current = str(payload.get("publish_status") or "")
        incoming = str(status.get("publish_status") or "")
        if incoming == "published" and not str(status.get("publish_topic_id") or "").strip():
            incoming = "submitted"
        if not self._should_replace_publish_status(current, incoming):
            return
        merged = {
            key: value
            for key, value in status.items()
            if key != "publish_updated_at_dt"
        }
        merged["publish_status"] = incoming
        payload.update(merged)
        payload["published"] = incoming == "published"
        payload["publish_submitted"] = incoming in {"submitted", "published"}
        payload["publish_attention_required"] = (
            payload.get("download_status") == "downloaded"
            and incoming in {"publish_failed", "publish_skipped"}
        )

    def _publish_source_task_id(self, task):
        params = task.params if isinstance(task.params, dict) else {}
        publish = params.get("zsxq_publish") if isinstance(params.get("zsxq_publish"), dict) else {}
        result = task.result if isinstance(task.result, dict) else {}
        return str(task.source_task_id or publish.get("source_task_id") or result.get("source_task_id") or "").strip()

    def _task_feature(self, task):
        params = task.params if isinstance(task.params, dict) else {}
        return str(task.feature or params.get("feature") or "").strip()

    def _path_key(self, value):
        if not value or str(value).startswith("browser-download:"):
            return None
        try:
            path = Path(str(value)).expanduser()
            if not path.is_absolute():
                path = self._project_root() / path
            return str(path.resolve())
        except (TypeError, OSError, ValueError):
            return None

    def _safe_int(self, value, default=0):
        try:
            return int(value or default)
        except (TypeError, ValueError):
            return default

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
        ).options(
            load_only(
                ZsxqAttachment.id,
                ZsxqAttachment.group_id,
                ZsxqAttachment.tab_id,
                ZsxqAttachment.topic_id,
                ZsxqAttachment.attachment_key,
                ZsxqAttachment.attachment_type,
                ZsxqAttachment.name,
                ZsxqAttachment.file_id,
                ZsxqAttachment.download_count,
                ZsxqAttachment.local_path,
                ZsxqAttachment.file_ext,
                ZsxqAttachment.download_status,
                ZsxqAttachment.error_message,
                ZsxqAttachment.publish_status,
                ZsxqAttachment.publish_task_id,
                ZsxqAttachment.publish_source_task_id,
                ZsxqAttachment.publish_topic_id,
                ZsxqAttachment.publish_submitted_count,
                ZsxqAttachment.publish_verified_count,
                ZsxqAttachment.publish_failed_count,
                ZsxqAttachment.publish_updated_at,
                ZsxqAttachment.created_at,
                ZsxqAttachment.updated_at,
            )
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
        ).options(
            load_only(
                ZsxqAttachment.id,
                ZsxqAttachment.group_id,
                ZsxqAttachment.tab_id,
                ZsxqAttachment.topic_id,
                ZsxqAttachment.attachment_key,
            )
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
        self._clear_attachment_status_cache()
        return record.to_dict()

    def _sync_tables_ready(self):
        if self._schema_checked:
            return True
        required_tables = {
            "zsxq_sync_runs",
            "zsxq_sync_run_downloads",
            "zsxq_topics",
            "zsxq_topic_comments",
            "zsxq_attachments",
            "zsxq_sync_cursors",
        }
        if db.engine.dialect.name == "postgresql":
            existing_tables, table_columns, _has_privilege = self._postgres_schema_contract()
        else:
            inspector = inspect(db.session.connection())
            existing_tables = self._table_names(inspector)
            table_columns = {
                "zsxq_attachments": {
                    item["name"] for item in self._get_columns(inspector, "zsxq_attachments")
                },
                "zsxq_sync_runs": {
                    item["name"] for item in self._get_columns(inspector, "zsxq_sync_runs")
                },
            }
        if not required_tables.issubset(existing_tables):
            return False
        attachment_columns = table_columns.get("zsxq_attachments", set())
        sync_run_columns = table_columns.get("zsxq_sync_runs", set())
        ready = (
            REQUIRED_ATTACHMENT_COLUMNS.issubset(attachment_columns)
            and REQUIRED_SYNC_RUN_PROJECTION_COLUMNS.issubset(sync_run_columns)
        )
        if ready:
            self._schema_checked = True
        return ready

    def _sync_manual_attachment_plan(self, cookie, group_id, group_name, tabs, run, download_file_types, manual_plan, max_download_files=None):
        tab_map = {tab["id"]: tab for tab in tabs}
        tab_results = {}
        topic_states = {}
        remaining_downloads = int(max_download_files) if max_download_files else None
        result = {
            "topics_fetched": 0,
            "topics_new": 0,
            "topics_updated": 0,
            "topics_existing": 0,
            "attachments_seen": 0,
            "attachments_downloaded": 0,
            "attachments_failed": 0,
            "attachments_skipped": 0,
            "attachments_below_threshold": 0,
            "attachments_missing_download_count": 0,
            "attachments_non_target_skipped": 0,
            "attachments_already_downloaded_skipped": 0,
            "attachments_reused_local": 0,
            "attachments_reused_existing": 0,
            "attachments_limited_skipped": 0,
            "downloaded_files": [],
            "downloaded_pdf_files": [],
            "download_limit_reached": False,
            "pages_fetched": 0,
            "tabs": [],
        }

        def ensure_tab_result(tab):
            tab_id = tab["id"]
            if tab_id not in tab_results:
                tab_results[tab_id] = {
                    "tab_id": tab_id,
                    "tab_label": tab["label"],
                    "pages": 0,
                    "topics_fetched": 0,
                    "topics_new": 0,
                    "topics_updated": 0,
                    "topics_existing": 0,
                    "attachments_seen": 0,
                    "attachments_downloaded": 0,
                    "attachments_failed": 0,
                    "attachments_skipped": 0,
                    "attachments_below_threshold": 0,
                    "attachments_missing_download_count": 0,
                    "attachments_non_target_skipped": 0,
                    "attachments_already_downloaded_skipped": 0,
                    "attachments_reused_local": 0,
                    "attachments_reused_existing": 0,
                    "attachments_limited_skipped": 0,
                    "downloaded_files": [],
                    "downloaded_pdf_files": [],
                    "stopped_by_existing": False,
                    "stopped_by_download_limit": False,
                    "download_limit_reached": False,
                    "attachment_download_enabled": True,
                    "manual_attachment_plan": True,
                    "last_end_time": None,
                }
            return tab_results[tab_id]

        for item in manual_plan["items"]:
            tab = tab_map.get(item["tab_id"]) or {
                "id": item["tab_id"],
                "label": item.get("tab_label") or item["tab_id"],
                "scope": "all",
                "hashtagId": item.get("hashtag_id"),
                "extraParams": {},
            }
            tab_map.setdefault(tab["id"], tab)
            tab_result = ensure_tab_result(tab)
            topic_key = (tab["id"], item["topic_id"])
            topic = self._manual_topic_payload(item)

            if topic_key not in topic_states:
                topic_status = self._upsert_manual_topic(group_id, group_name, tab, topic, run)
                topic_states[topic_key] = topic_status
                tab_result["topics_fetched"] += 1
                if topic_status == "new":
                    tab_result["topics_new"] += 1
                elif topic_status == "updated":
                    tab_result["topics_updated"] += 1
                else:
                    tab_result["topics_existing"] += 1

            record = self._upsert_manual_attachment(group_id, tab, item)
            tab_result["attachments_seen"] += 1
            download_stats = self._download_manual_attachment_record(
                cookie=cookie,
                group_id=group_id,
                tab=tab,
                topic_id=item["topic_id"],
                record=record,
                download_file_types=download_file_types,
                remaining_downloads=remaining_downloads,
                run_id=self._cached_sync_run_id(run),
            )
            self._merge_attachment_stats(tab_result, download_stats)
            if download_stats.get("download_limit_reached"):
                tab_result["download_limit_reached"] = True
                tab_result["stopped_by_download_limit"] = True
            if download_stats.get("_download_attempted") and remaining_downloads is not None:
                remaining_downloads = max(0, remaining_downloads - 1)
            self._commit_manual_running_progress(run, tab, item["topic_id"], tab_result, manual_plan)

        for tab_result in tab_results.values():
            result["tabs"].append(tab_result)
            result["topics_fetched"] += tab_result["topics_fetched"]
            result["topics_new"] += tab_result["topics_new"]
            result["topics_updated"] += tab_result["topics_updated"]
            result["topics_existing"] += tab_result["topics_existing"]
            result["attachments_seen"] += tab_result["attachments_seen"]
            result["attachments_downloaded"] += tab_result["attachments_downloaded"]
            result["attachments_failed"] += tab_result["attachments_failed"]
            result["attachments_skipped"] += tab_result["attachments_skipped"]
            result["attachments_below_threshold"] += tab_result.get("attachments_below_threshold", 0)
            result["attachments_missing_download_count"] += tab_result.get("attachments_missing_download_count", 0)
            result["attachments_non_target_skipped"] += tab_result.get("attachments_non_target_skipped", 0)
            result["attachments_already_downloaded_skipped"] += tab_result.get("attachments_already_downloaded_skipped", 0)
            result["attachments_reused_local"] += tab_result.get("attachments_reused_local", 0)
            result["attachments_reused_existing"] += tab_result.get("attachments_reused_existing", 0)
            result["attachments_limited_skipped"] += tab_result.get("attachments_limited_skipped", 0)
            result["pages_fetched"] += tab_result.get("pages", 0)
            self._merge_downloaded_file_entries(result, tab_result)
            if tab_result.get("download_limit_reached") or tab_result.get("stopped_by_download_limit"):
                result["download_limit_reached"] = True

        db.session.commit()
        return result

    def _upsert_manual_topic(self, group_id, group_name, tab, topic, run):
        topic_id = self._topic_id(topic)
        record = ZsxqTopic.query.filter_by(
            group_id=group_id,
            tab_id=tab["id"],
            topic_id=topic_id,
        ).options(
            load_only(
                ZsxqTopic.id,
                ZsxqTopic.group_name,
                ZsxqTopic.title,
                ZsxqTopic.last_sync_run_id,
            )
        ).first()
        if record:
            record.last_sync_run_id = self._cached_sync_run_id(run)
            meta = self._topic_meta(topic)
            if not record.title and meta["title"]:
                record.title = meta["title"]
            if not record.group_name and group_name:
                record.group_name = group_name
            db.session.flush()
            return "existing"

        record = self._create_topic_record(group_id, group_name, tab, topic, run)
        db.session.add(record)
        db.session.flush()
        return "new"

    def _upsert_manual_attachment(self, group_id, tab, item):
        payload = item["file"]
        topic_id = item["topic_id"]
        key = item.get("attachment_key") or self._attachment_key(payload, 0, "file")
        record = ZsxqAttachment.query.filter_by(
            group_id=group_id,
            tab_id=tab["id"],
            topic_id=topic_id,
            attachment_key=key,
        ).options(
            load_only(
                ZsxqAttachment.id,
                ZsxqAttachment.group_id,
                ZsxqAttachment.tab_id,
                ZsxqAttachment.topic_id,
                ZsxqAttachment.attachment_key,
            )
        ).first()
        values = {
            "group_id": group_id,
            "tab_id": tab["id"],
            "topic_id": topic_id,
            "attachment_key": key,
            "attachment_type": "file",
            "name": item.get("name") or self._attachment_name(payload, "file"),
            "file_id": item.get("file_id") or self._attachment_file_id(payload),
            "download_count": self._download_count(payload),
            "raw_payload": payload,
        }
        if record:
            for field, value in values.items():
                setattr(record, field, value)
        else:
            record = ZsxqAttachment(**values)
            db.session.add(record)
        db.session.flush()
        return record

    def _download_manual_attachment_record(
        self,
        cookie,
        group_id,
        tab,
        topic_id,
        record,
        download_file_types,
        remaining_downloads=None,
        run_id=None,
    ):
        payload = record.raw_payload or {}
        record_id = record.id
        record_name = record.name
        stats = {
            "attachments_seen": 0,
            "attachments_downloaded": 0,
            "attachments_failed": 0,
            "attachments_skipped": 0,
            "attachments_below_threshold": 0,
            "attachments_missing_download_count": 0,
            "attachments_non_target_skipped": 0,
            "attachments_already_downloaded_skipped": 0,
            "attachments_reused_local": 0,
            "attachments_reused_existing": 0,
            "attachments_limited_skipped": 0,
            "downloaded_files": [],
            "downloaded_pdf_files": [],
            "stopped_by_download_limit": False,
            "download_limit_reached": False,
            "_download_attempted": False,
        }
        if not self._should_download_attachment(record, download_file_types):
            if record.download_status == "pending":
                record.download_status = "skipped"
                record.error_message = f"非目标类型附件，跳过人工计划下载: {self._attachment_file_type(record)}"
                stats["attachments_skipped"] += 1
                stats["attachments_non_target_skipped"] += 1
            db.session.commit()
            return stats
        if self._download_record_is_reusable(record):
            record.download_status = "downloaded"
            record.error_message = None
            stats["attachments_skipped"] += 1
            stats["attachments_already_downloaded_skipped"] += 1
            stats["attachments_reused_local"] += 1
            self._append_downloaded_record_entry(stats, record, record.local_path)
            self._record_sync_run_download(run_id, group_id, record, record.local_path)
            db.session.commit()
            return stats

        existing_download = self._find_existing_downloaded_attachment(record)
        if existing_download:
            record.local_path = existing_download.local_path
            record.download_status = "downloaded"
            record.error_message = None
            stats["attachments_skipped"] += 1
            stats["attachments_already_downloaded_skipped"] += 1
            stats["attachments_reused_existing"] += 1
            self._append_downloaded_record_entry(stats, record, record.local_path)
            self._record_sync_run_download(run_id, group_id, record, record.local_path)
            db.session.commit()
            return stats

        if remaining_downloads is not None and remaining_downloads <= 0:
            stats["stopped_by_download_limit"] = True
            stats["download_limit_reached"] = True
            stats["attachments_limited_skipped"] += 1
            db.session.commit()
            return stats

        db.session.commit()
        self._attachment_download_pause()
        record_for_update = None
        try:
            stats["_download_attempted"] = True
            local_path = self._download_attachment_file(cookie, group_id, tab, topic_id, payload, record_name)
            record_for_update = self._load_attachment_for_download_update(record_id)
            if not record_for_update:
                raise ValueError("附件记录不存在，无法写入下载结果")
            record_for_update.local_path = local_path
            record_for_update.download_status = "downloaded"
            record_for_update.error_message = None
            stats["attachments_downloaded"] += 1
            self._append_downloaded_record_entry(stats, record_for_update, local_path)
            self._record_sync_run_download(run_id, group_id, record_for_update, local_path)
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            if isinstance(exc, SQLAlchemyError) or is_retryable_db_error(exc):
                raise
            if record_for_update is None and record_id:
                record_for_update = self._load_attachment_for_download_update(record_id)
            if record_for_update:
                record_for_update.download_status = "failed"
                record_for_update.error_message = str(exc)[:1000]
            stats["attachments_failed"] += 1
            logger.warning(
                "[ZSXQ_SYNC] manual attachment download failed | group_id=%s | tab=%s | topic_id=%s | name=%s | error=%s",
                group_id,
                tab["id"],
                topic_id,
                record_name,
                exc,
            )
            db.session.commit()
        return stats

    def _append_downloaded_record_entry(self, stats, record, local_path):
        entry = self._downloaded_file_entry(record, local_path)
        if not entry:
            return
        self._append_unique_file_entry(stats["downloaded_files"], entry)
        if entry["path"].lower().endswith(".pdf"):
            self._append_unique_file_entry(stats["downloaded_pdf_files"], entry)

    def _append_auto_video_dispatch_file_entry(self, stats, record, local_path):
        entry = self._downloaded_file_entry(record, local_path)
        if not entry:
            return
        stats.setdefault("auto_video_dispatch_files", [])
        self._append_unique_file_entry(stats["auto_video_dispatch_files"], entry)

    def _commit_manual_running_progress(self, run, tab, topic_id, tab_result, manual_plan):
        previous_result = self._cached_run_result(run)
        next_result = {
            "running": True,
            "heartbeat_at": datetime.utcnow().isoformat() + "Z",
            "current_tab": self._public_tab(tab),
            "current_topic_id": topic_id,
            "manual_attachment_plan": self._public_manual_attachment_plan(manual_plan),
            "partial": dict(tab_result),
        }
        next_result = self._preserve_run_result_context(next_result, previous_result)
        self._update_run_progress_result(run, next_result)

    def _sync_one_tab(
        self,
        cookie,
        group_id,
        group_name,
        tab,
        run,
        count,
        max_pages,
        stop_on_existing,
        download_attachments,
        download_file_types,
        max_download_files=None,
        attachment_download_rules=None,
    ):
        tab_id = tab["id"]
        tab_label = tab["label"]
        end_time = None
        target_topics = min(max(int(count or 20), 1), 50)
        tab_result = {
            "tab_id": tab_id,
            "tab_label": tab_label,
            "target_topics": target_topics,
            "pages": 0,
            "topics_fetched": 0,
            "topics_new": 0,
            "topics_updated": 0,
            "topics_existing": 0,
            "attachments_seen": 0,
            "attachments_downloaded": 0,
            "attachments_failed": 0,
            "attachments_skipped": 0,
            "attachments_below_threshold": 0,
            "attachments_missing_download_count": 0,
            "attachments_non_target_skipped": 0,
            "attachments_already_downloaded_skipped": 0,
            "attachments_reused_local": 0,
            "attachments_reused_existing": 0,
            "attachments_limited_skipped": 0,
            "downloaded_files": [],
            "downloaded_pdf_files": [],
            "stopped_by_existing": False,
            "stopped_by_download_limit": False,
            "download_limit_reached": False,
            "attachment_download_enabled": bool(download_attachments),
            "max_pages": max_pages,
            "planned_pages": max_pages,
            "last_end_time": None,
        }
        newest_topic = None
        seen_ids = set()
        remaining_downloads = int(max_download_files) if max_download_files else None

        for _ in range(max_pages):
            remaining_topic_slots = target_topics - tab_result["topics_fetched"]
            if remaining_topic_slots <= 0:
                break
            if tab_result["pages"] > 0:
                self._human_pause()
            # ZSXQ topic-list endpoints reject a per-request count above 20
            # (code 14001). Larger per-tab targets are fulfilled by paging.
            page_count = min(
                max(remaining_topic_slots, 1),
                TOPIC_PAGE_SIZE_HINT,
            )
            response = self._get_topics_with_retry(
                cookie=cookie,
                group_id=group_id,
                count=page_count,
                scope=tab.get("scope") or "all",
                end_time=end_time,
                extra_params=self._tab_extra_params(tab),
                tab_id=tab_id,
                tab_label=tab_label,
                page=tab_result["pages"] + 1,
            )
            raw_topics = self._extract_topics(response)
            tab_result["pages"] += 1

            if not raw_topics:
                break

            topics = []
            page_seen_ids = set()
            for topic in raw_topics:
                topic_id = self._topic_id(topic)
                if not topic_id or topic_id in seen_ids or topic_id in page_seen_ids:
                    continue
                topics.append(topic)
                page_seen_ids.add(topic_id)
                if len(topics) >= remaining_topic_slots:
                    break

            if not topics:
                break

            tab_result["topics_fetched"] += len(topics)

            # Finish upstream hydration before opening the page-level database
            # transaction, then load all existing topic projections at once.
            for topic in topics:
                self._hydrate_truncated_topic_comments(cookie, topic)
            existing_topic_snapshots = self._load_existing_topic_snapshots(
                group_id,
                tab_id,
                topics,
            )
            existing_attachment_snapshots = self._load_existing_attachment_snapshots(
                group_id,
                tab_id,
                topics,
            )
            self._refresh_existing_attachment_snapshots_for_page(
                group_id,
                tab,
                topics,
                existing_attachment_snapshots,
            )
            # Page snapshots are detached projections. Persist their refresh and
            # return the connection before per-topic CPU and file processing.
            db.session.commit()

            for topic in topics:
                topic_id = self._topic_id(topic)
                if not topic_id or topic_id in seen_ids:
                    continue
                seen_ids.add(topic_id)
                if newest_topic is None:
                    newest_topic = topic

                self._upsert_topic_comments(group_id, tab, topic)

                topic_snapshot = existing_topic_snapshots.get(str(topic_id))
                if topic_snapshot:
                    tab_result["topics_existing"] += 1
                    attachment_stats, attachment_records = self._upsert_attachments(
                        group_id,
                        tab,
                        topic,
                        existing_records=existing_attachment_snapshots.get(str(topic_id), ()),
                        existing_records_persisted=True,
                    )
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
                            remaining_downloads=remaining_downloads,
                            attachment_download_rules=attachment_download_rules,
                            run_id=self._cached_sync_run_id(run),
                            records=attachment_records,
                        )
                        remaining_downloads = download_stats.get("remaining_downloads", remaining_downloads)
                        self._merge_attachment_stats(tab_result, download_stats)
                        if download_stats.get("download_limit_reached"):
                            tab_result["download_limit_reached"] = True
                        if remaining_downloads is not None:
                            tab_result["remaining_downloads"] = remaining_downloads
                    has_pending_download_work = (
                        download_stats.get("attachments_downloaded", 0) > 0
                        or download_stats.get("attachments_failed", 0) > 0
                    )
                    if stop_on_existing and not max_download_files and not has_pending_download_work:
                        tab_result["stopped_by_existing"] = True
                        break
                    changed, topic_for_external_scan = self._update_existing_topic(
                        None,
                        topic,
                        tab,
                        run,
                        previous=topic_snapshot,
                    )
                    tab_result["topics_updated"] += 1 if changed else 0
                    self._scan_external_document_links(topic_for_external_scan)
                    self._commit_running_progress(run, tab, topic_id, tab_result)
                    continue

                topic_record = self._create_topic_record(group_id, group_name, tab, topic, run)
                db.session.add(topic_record)
                db.session.flush()
                self._scan_external_document_links(topic_record)
                attachment_stats, attachment_records = self._upsert_attachments(
                    group_id,
                    tab,
                    topic,
                    existing_records=existing_attachment_snapshots.get(str(topic_id), ()),
                    existing_records_persisted=True,
                )
                self._merge_attachment_stats(tab_result, attachment_stats)
                if download_attachments:
                    download_stats = self._download_topic_attachments(
                        cookie=cookie,
                        group_id=group_id,
                        tab=tab,
                        topic=topic,
                        download_file_types=download_file_types,
                        max_download_files=max_download_files,
                        remaining_downloads=remaining_downloads,
                        attachment_download_rules=attachment_download_rules,
                        run_id=self._cached_sync_run_id(run),
                        records=attachment_records,
                    )
                    remaining_downloads = download_stats.get("remaining_downloads", remaining_downloads)
                    self._merge_attachment_stats(tab_result, download_stats)
                    if download_stats.get("download_limit_reached"):
                        tab_result["download_limit_reached"] = True
                    if remaining_downloads is not None:
                        tab_result["remaining_downloads"] = remaining_downloads
                tab_result["topics_new"] += 1
                self._commit_running_progress(run, tab, topic_id, tab_result)

            db.session.commit()
            if tab_result["stopped_by_existing"]:
                break
            if tab_result["topics_fetched"] >= target_topics:
                break

            end_time = (response.get("resp_data") or {}).get("next_end_time") or self._oldest_topic_time(topics)
            tab_result["last_end_time"] = end_time
            if not end_time:
                break

        self._upsert_cursor(group_id, tab, newest_topic, tab_result)
        db.session.commit()
        return tab_result

    def _hydrate_truncated_topic_comments(self, cookie, topic):
        """Complete comments only when the topic-list preview was truncated."""
        if not isinstance(topic, dict):
            return topic
        try:
            expected = max(int(topic.get("comments_count") or 0), 0)
        except (TypeError, ValueError):
            expected = 0
        existing = [item for item in (topic.get("show_comments") or []) if isinstance(item, dict)]
        if expected <= len(existing):
            return topic

        try:
            limit = max(int(os.environ.get(MAX_COMMENTS_PER_TOPIC_ENV, DEFAULT_MAX_COMMENTS_PER_TOPIC)), 1)
        except (TypeError, ValueError):
            limit = DEFAULT_MAX_COMMENTS_PER_TOPIC
        limit = min(limit, 1000)
        topic_id = self._topic_id(topic)
        if not topic_id:
            return topic
        end_db_transaction_before_external_io("ZSXQ comment hydration")

        merged = {}

        def remember(comment, parent_comment_id=None):
            if not isinstance(comment, dict):
                return
            item = deepcopy(comment)
            comment_id = str(item.get("comment_id") or "").strip()
            if not comment_id:
                return
            if parent_comment_id and not item.get("parent_comment_id"):
                item["parent_comment_id"] = parent_comment_id
            merged[comment_id] = item

        for item in existing:
            remember(item)

        index = None
        try:
            while len(merged) < min(expected, limit):
                response = zsxq_service.get_topic_comments(
                    cookie,
                    topic_id,
                    count=min(30, limit - len(merged)),
                    index=index,
                )
                resp_data = response.get("resp_data") if isinstance(response, dict) else {}
                resp_data = resp_data if isinstance(resp_data, dict) else {}
                page = [item for item in (resp_data.get("comments") or []) if isinstance(item, dict)]
                if not page:
                    break
                before = len(merged)
                for comment in page:
                    remember(comment)
                    replies_count = int(comment.get("replies_count") or 0)
                    replied_comments = [
                        item for item in (comment.get("replied_comments") or []) if isinstance(item, dict)
                    ]
                    if replies_count > len(replied_comments) and len(merged) < limit:
                        detail = zsxq_service.get_comment(cookie, comment.get("comment_id"))
                        detailed = ((detail.get("resp_data") or {}).get("comment") or {}) if isinstance(detail, dict) else {}
                        replied_comments = [
                            item for item in (detailed.get("replied_comments") or []) if isinstance(item, dict)
                        ]
                    for reply in replied_comments:
                        if len(merged) >= limit:
                            break
                        remember(reply, parent_comment_id=comment.get("comment_id"))
                next_index = resp_data.get("index")
                if len(merged) == before or next_index in (None, "") or next_index == index:
                    break
                index = next_index
        except Exception as exc:
            logger.warning(
                "[ZSXQ_COMMENTS] fallback fetch failed | topic_id=%s expected=%s collected=%s error=%s",
                topic_id,
                expected,
                len(merged),
                exc,
            )

        comments = list(merged.values())[:limit]
        comments.sort(key=lambda item: str(item.get("create_time") or ""))
        topic["show_comments"] = comments
        topic["comments_count"] = max(expected, len(comments))
        topic["comments_truncated"] = len(comments) < expected
        return topic

    def _topic_comments(self, topic):
        if not isinstance(topic, dict):
            return []
        output = []
        seen = set()

        def visit(comment, parent_comment_id=None):
            if not isinstance(comment, dict):
                return
            item = deepcopy(comment)
            comment_id = str(item.get("comment_id") or "").strip()
            if not comment_id or comment_id in seen:
                return
            if parent_comment_id and not item.get("parent_comment_id"):
                item["parent_comment_id"] = parent_comment_id
            seen.add(comment_id)
            output.append(item)
            for reply in item.get("replied_comments") or []:
                visit(reply, comment_id)

        for comment in topic.get("show_comments") or []:
            visit(comment)
        return output

    def _upsert_topic_comments(self, group_id, tab, topic):
        comments = self._topic_comments(topic)
        if not comments:
            return {"comments_seen": 0, "comments_new": 0, "comments_updated": 0, "favorite_added": False}
        topic_id = self._topic_id(topic)
        comment_ids = [str(item.get("comment_id")) for item in comments]
        existing = ZsxqTopicComment.query.filter(
            ZsxqTopicComment.group_id == str(group_id),
            ZsxqTopicComment.topic_id == str(topic_id),
            ZsxqTopicComment.comment_id.in_(comment_ids),
        ).options(
            load_only(
                ZsxqTopicComment.id,
                ZsxqTopicComment.comment_id,
                ZsxqTopicComment.tab_id,
                ZsxqTopicComment.parent_comment_id,
                ZsxqTopicComment.owner_user_id,
                ZsxqTopicComment.owner_name,
                ZsxqTopicComment.text,
                ZsxqTopicComment.create_time,
                ZsxqTopicComment.likes_count,
                ZsxqTopicComment.replies_count,
                ZsxqTopicComment.rewards_count,
                ZsxqTopicComment.sticky,
                ZsxqTopicComment.raw_payload_hash,
            )
        ).all()
        by_comment_id = {str(item.comment_id): item for item in existing}
        stats = {"comments_seen": len(comments), "comments_new": 0, "comments_updated": 0, "favorite_added": False}
        now = datetime.utcnow()
        for payload in comments:
            comment_id = str(payload.get("comment_id") or "").strip()
            owner = payload.get("owner") if isinstance(payload.get("owner"), dict) else {}
            payload_hash = jsonb_payload_md5(payload)
            values = {
                "tab_id": str(tab.get("id") or "") or None,
                "parent_comment_id": self._text_or_none(payload.get("parent_comment_id"), 64),
                "owner_user_id": self._text_or_none(owner.get("user_id"), 64),
                "owner_name": self._text_or_none(owner.get("name") or owner.get("alias"), 255),
                "text": str(payload.get("text") or "") or None,
                "create_time": self._text_or_none(payload.get("create_time"), 64),
                "likes_count": self._int_or_zero(payload.get("likes_count")),
                "replies_count": self._int_or_zero(payload.get("replies_count")),
                "rewards_count": self._int_or_zero(payload.get("rewards_count")),
                "sticky": self._truthy_text(payload.get("sticky")),
            }
            record = by_comment_id.get(comment_id)
            if record is None:
                record = ZsxqTopicComment(
                    group_id=str(group_id),
                    topic_id=str(topic_id),
                    comment_id=comment_id,
                    raw_payload=payload,
                    raw_payload_hash=payload_hash,
                    last_synced_at=now,
                    **values,
                )
                db.session.add(record)
                by_comment_id[comment_id] = record
                stats["comments_new"] += 1
                continue
            changed_values = {
                key: value
                for key, value, current in (
                    ("tab_id", values["tab_id"], record.tab_id),
                    ("parent_comment_id", values["parent_comment_id"], record.parent_comment_id),
                    ("owner_user_id", values["owner_user_id"], record.owner_user_id),
                    ("owner_name", values["owner_name"], record.owner_name),
                    ("text", values["text"], record.text),
                    ("create_time", values["create_time"], record.create_time),
                    ("likes_count", values["likes_count"], record.likes_count),
                    ("replies_count", values["replies_count"], record.replies_count),
                    ("rewards_count", values["rewards_count"], record.rewards_count),
                    ("sticky", values["sticky"], record.sticky),
                )
                if current != value
            }
            for key, value in changed_values.items():
                setattr(record, key, value)
            changed = bool(changed_values)
            if record.raw_payload_hash != payload_hash:
                record.raw_payload = payload
                record.raw_payload_hash = payload_hash
                changed = True
            if changed:
                record.last_synced_at = now
                stats["comments_updated"] += 1
        yiren_awarded = any(
            is_yiren_award_comment(
                (item.get("owner") or {}).get("name") or (item.get("owner") or {}).get("alias"),
                item.get("text"),
                owner_user_id=(item.get("owner") or {}).get("user_id"),
                group_id=group_id,
            )
            for item in comments
            if isinstance(item.get("owner"), dict)
        )
        if yiren_awarded:
            favorite = ZsxqTopicFavorite.query.filter_by(
                group_id=str(group_id),
                topic_id=str(topic_id),
            ).first()
            if favorite is None:
                db.session.add(ZsxqTopicFavorite(group_id=str(group_id), topic_id=str(topic_id)))
                stats["favorite_added"] = True
        return stats

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

            candidates = query.options(
                load_only(
                    ZsxqAttachment.id,
                    ZsxqAttachment.group_id,
                    ZsxqAttachment.tab_id,
                    ZsxqAttachment.topic_id,
                    ZsxqAttachment.attachment_key,
                    ZsxqAttachment.name,
                    ZsxqAttachment.file_id,
                    ZsxqAttachment.local_path,
                    ZsxqAttachment.download_status,
                    ZsxqAttachment.updated_at,
                )
            ).order_by(ZsxqAttachment.updated_at.desc()).limit(10).all()
            for candidate in candidates:
                if self._download_marker_exists(candidate.local_path):
                    return candidate
        return None

    def _commit_running_progress(self, run, tab, topic_id, tab_result):
        previous_result = self._cached_run_result(run)
        next_result = {
            "running": True,
            "heartbeat_at": datetime.utcnow().isoformat() + "Z",
            "current_tab": self._public_tab(tab),
            "current_topic_id": topic_id,
            "partial": dict(tab_result),
        }
        next_result = self._preserve_run_result_context(next_result, previous_result)
        self._update_run_progress_result(run, next_result)

    def _preserve_run_result_context(self, next_result, previous_result):
        if not isinstance(previous_result, dict):
            return next_result
        for key in ("system_sync", "attachment_download_policy", "manual_attachment_plan", "attachment_download_rules"):
            if key in next_result:
                continue
            value = previous_result.get(key)
            if value is not None:
                next_result[key] = deepcopy(value)
        return next_result

    def _cached_run_result(self, run):
        cached = getattr(run, "_zsxq_cached_result", None)
        if isinstance(cached, dict):
            return deepcopy(cached)
        return {}

    def _cached_sync_run_id(self, run):
        cached = getattr(run, "_zsxq_cached_run_id", None)
        if cached:
            return str(cached)
        direct = getattr(run, "__dict__", {}).get("id")
        if direct:
            run._zsxq_cached_run_id = str(direct)
            return str(direct)
        identity = inspect(run).identity if run is not None else None
        if identity and identity[0]:
            run._zsxq_cached_run_id = str(identity[0])
            return str(identity[0])
        raise ValueError("同步任务 ID 不能为空")

    def _update_run_progress_result(self, run, result, *, force_result=False):
        run_id = self._cached_sync_run_id(run)
        now_monotonic = time.monotonic()
        last_persisted = getattr(run, "_zsxq_result_persisted_monotonic", None)
        persist_interval = self._progress_result_persist_seconds()
        persist_result = (
            force_result
            or last_persisted is None
            or persist_interval <= 0
            or now_monotonic - last_persisted >= persist_interval
        )
        values = {
            "updated_at": datetime.utcnow(),
            **self._sync_run_projection_values(result),
        }
        if persist_result:
            values["result"] = result
        db.session.execute(
            update(ZsxqSyncRun)
            .where(ZsxqSyncRun.id == str(run_id))
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        run._zsxq_cached_result = deepcopy(result)
        run._zsxq_cached_run_id = str(run_id)
        if persist_result:
            run._zsxq_result_persisted_monotonic = now_monotonic
        db.session.commit()

    def _progress_result_persist_seconds(self):
        config_value = current_app.config.get(PROGRESS_RESULT_PERSIST_SECONDS_ENV) if has_app_context() else None
        value = (
            config_value
            if config_value is not None
            else os.environ.get(
                PROGRESS_RESULT_PERSIST_SECONDS_ENV,
                DEFAULT_PROGRESS_RESULT_PERSIST_SECONDS,
            )
        )
        try:
            return min(max(float(value), 0.0), 300.0)
        except (TypeError, ValueError):
            return DEFAULT_PROGRESS_RESULT_PERSIST_SECONDS

    def _sync_run_projection_values(self, result):
        result = result if isinstance(result, dict) else {}
        partial = result.get("partial") if isinstance(result.get("partial"), dict) else {}
        meta = result.get("system_sync") if isinstance(result.get("system_sync"), dict) else {}
        return {
            "system_batch_id": self._text_or_none(meta.get("batch_id"), 80),
            "system_attempt_id": self._text_or_none(meta.get("attempt_id"), 80),
            "system_round": self._int_or_none(meta.get("round")),
            "system_max_rounds": self._int_or_none(meta.get("max_rounds")),
            "system_trigger": self._text_or_none(meta.get("trigger"), 120),
            "system_queued_at": self._text_or_none(meta.get("queued_at"), 64),
            "system_max_download_files": self._int_or_none(meta.get("max_download_files")),
            "topics_fetched": self._int_or_zero(result.get("topics_fetched", partial.get("topics_fetched"))),
            "topics_new": self._int_or_zero(result.get("topics_new", partial.get("topics_new"))),
            "topics_updated": self._int_or_zero(result.get("topics_updated", partial.get("topics_updated"))),
            "topics_existing": self._int_or_zero(result.get("topics_existing", partial.get("topics_existing"))),
            "attachments_seen": self._int_or_zero(result.get("attachments_seen", partial.get("attachments_seen"))),
            "attachments_downloaded": self._int_or_zero(result.get("attachments_downloaded", partial.get("attachments_downloaded"))),
            "attachments_failed": self._int_or_zero(result.get("attachments_failed", partial.get("attachments_failed"))),
            "attachments_skipped": self._int_or_zero(result.get("attachments_skipped", partial.get("attachments_skipped"))),
            "download_limit_reached": self._truthy_text(result.get("download_limit_reached", partial.get("download_limit_reached"))),
        }

    def _text_or_none(self, value, max_length):
        text_value = str(value or "").strip()
        if not text_value:
            return None
        return text_value[:max_length]

    def _mark_stale_runs_failed(self, group_id=None, stale_minutes=120):
        cutoff = datetime.utcnow() - timedelta(minutes=stale_minutes)
        query = ZsxqSyncRun.query.filter(
            ZsxqSyncRun.status.in_(STALE_SYNC_RUN_STATUSES),
            ZsxqSyncRun.updated_at < cutoff,
        )
        if group_id:
            query = query.filter(ZsxqSyncRun.group_id == str(group_id))

        stale_ids = [row.id for row in query.with_entities(ZsxqSyncRun.id).limit(20).all()]
        if not stale_ids:
            return

        now = datetime.utcnow()
        if db.engine.url.get_backend_name() == "postgresql":
            patch = json.dumps({
                "interrupted": True,
                "queued": False,
                "running": False,
            }, ensure_ascii=False)
            db.session.execute(
                text("""
                    UPDATE public.zsxq_sync_runs
                    SET
                        status = 'failed',
                        completed_at = :completed_at,
                        error_message = :error_message,
                        result = COALESCE(result::jsonb, '{}'::jsonb) || CAST(:patch AS jsonb)
                    WHERE id = ANY(:ids)
                """),
                {
                    "completed_at": now,
                    "error_message": STALE_SYNC_RUN_ERROR,
                    "patch": patch,
                    "ids": stale_ids,
                },
            )
            db.session.commit()
            return

        stale_runs = (
            ZsxqSyncRun.query
            .options(load_only(
                ZsxqSyncRun.id,
                ZsxqSyncRun.status,
                ZsxqSyncRun.result,
                ZsxqSyncRun.completed_at,
                ZsxqSyncRun.error_message,
            ))
            .filter(ZsxqSyncRun.id.in_(stale_ids))
            .all()
        )
        for run in stale_runs:
            result = dict(run.result) if isinstance(run.result, dict) else {}
            result["interrupted"] = True
            result["queued"] = False
            result["running"] = False
            run.status = "failed"
            run.completed_at = now
            run.error_message = STALE_SYNC_RUN_ERROR
            run.result = result
        db.session.commit()

    def _create_topic_record(self, group_id, group_name, tab, topic, run):
        topic_id = self._topic_id(topic)
        raw_path = self._write_topic_json(group_id, tab, topic_id, topic)
        meta = self._topic_meta(topic)
        run_id = self._cached_sync_run_id(run)
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
            first_sync_run_id=run_id,
            last_sync_run_id=run_id,
        )

    def _load_existing_topic_snapshots(
        self,
        group_id,
        tab_id,
        topics,
        payload_hash_column_available=True,
    ):
        topic_ids = [
            str(topic_id)
            for topic_id in (self._topic_id(topic) for topic in topics)
            if topic_id
        ]
        if not topic_ids:
            return {}
        snapshot_fields = tuple(
            field
            for field in TOPIC_UPDATE_SNAPSHOT_FIELDS
            if payload_hash_column_available or field != "raw_payload_hash"
        )
        columns = tuple(getattr(ZsxqTopic, field) for field in snapshot_fields)
        rows = (
            db.session.query(*columns)
            .filter(
                ZsxqTopic.group_id == str(group_id),
                ZsxqTopic.tab_id == str(tab_id),
                ZsxqTopic.topic_id.in_(tuple(topic_ids)),
            )
            .all()
        )
        return {
            str(row.topic_id): {
                **{
                    field: getattr(row, field)
                    for field in snapshot_fields
                },
                "raw_payload_hash": (
                    getattr(row, "raw_payload_hash")
                    if payload_hash_column_available
                    else None
                ),
            }
            for row in rows
        }

    def _update_existing_topic(
        self,
        record,
        topic,
        tab,
        run,
        previous=None,
        payload_hash_column_available=True,
    ):
        meta = self._topic_meta(topic)
        run_id = self._cached_sync_run_id(run)
        if previous is None:
            previous = {
                field: self._loaded_model_attr(record, field)
                for field in TOPIC_UPDATE_SNAPSHOT_FIELDS
            }
        payload_hash = jsonb_payload_md5(topic)
        payload_changed = previous.get("raw_payload_hash") != payload_hash
        raw_json_path = previous.get("raw_json_path")
        if payload_changed or not raw_json_path:
            raw_json_path = self._write_topic_json(
                previous.get("group_id"),
                tab,
                previous.get("topic_id"),
                topic,
            )
        projected = ZsxqTopic(
            id=previous.get("id"),
            group_id=previous.get("group_id"),
            group_name=previous.get("group_name"),
            tab_id=tab["id"],
            tab_label=tab["label"],
            topic_id=previous.get("topic_id"),
            topic_uid=previous.get("topic_uid"),
            title=meta["title"],
            author_name=meta["author_name"],
            content=meta["content"],
            create_time=meta["create_time"],
            raw_json_path=raw_json_path,
            raw_payload=topic,
            raw_payload_hash=payload_hash,
            first_sync_run_id=previous.get("first_sync_run_id"),
            last_sync_run_id=run_id,
            first_synced_at=previous.get("first_synced_at"),
            last_synced_at=datetime.utcnow(),
        )
        changed_fields = {
            field: getattr(projected, field)
            for field in ("tab_id", "tab_label")
            if previous.get(field) != getattr(projected, field)
        }
        if payload_changed:
            # All structured values are deterministic projections of the new
            # payload. Avoid fetching the old large text columns merely to
            # compare them; a changed payload hash means the new projection is
            # the authoritative snapshot.
            changed_fields.update({
                field: getattr(projected, field)
                for field in TOPIC_STRUCTURED_UPDATE_FIELDS
            })
        changed = bool(changed_fields)
        values = {
            **changed_fields,
            "last_sync_run_id": run_id,
            "last_synced_at": projected.last_synced_at,
        }
        if payload_changed:
            values.update({
                "raw_json_path": raw_json_path,
                "raw_payload": topic,
            })
            if payload_hash_column_available:
                values["raw_payload_hash"] = payload_hash
        db.session.execute(
            update(ZsxqTopic)
            .where(ZsxqTopic.id == str(previous.get("id")))
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        return changed, projected

    def _load_existing_attachment_snapshots(self, group_id, tab_id, topics):
        topic_ids = []
        for topic in topics or ():
            topic_id = self._topic_id(topic)
            if topic_id and self._topic_attachment_items(topic):
                topic_ids.append(str(topic_id))
        if not topic_ids:
            return {}
        rows = (
            ZsxqAttachment.query
            .with_entities(*ATTACHMENT_DOWNLOAD_SNAPSHOT_COLUMNS)
            .filter(
                ZsxqAttachment.group_id == str(group_id),
                ZsxqAttachment.tab_id == str(tab_id),
                ZsxqAttachment.topic_id.in_(topic_ids),
            )
            .all()
        )
        grouped = {topic_id: [] for topic_id in topic_ids}
        for row in rows:
            snapshot = SimpleNamespace(**{
                column.key: getattr(row, column.key)
                for column in ATTACHMENT_DOWNLOAD_SNAPSHOT_COLUMNS
            })
            grouped.setdefault(str(snapshot.topic_id), []).append(snapshot)
        return grouped

    def _attachment_projection_values(self, group_id, tab_id, topic_id, key, attachment_type, payload):
        return {
            "group_id": group_id,
            "tab_id": tab_id,
            "topic_id": topic_id,
            "attachment_key": key,
            "attachment_type": attachment_type,
            "name": self._attachment_name(payload, attachment_type),
            "file_id": self._attachment_file_id(payload),
            "download_count": self._download_count(payload),
            "raw_payload": payload,
        }

    @staticmethod
    def _postgres_attachment_projection_values(table, fields, mappings):
        return values(
            column("id", table.c.id.type),
            *(
                column(field, table.c[field].type)
                for field in fields
            ),
            name="incoming_attachments",
        ).data([
            (
                str(mapping["id"]),
                *(mapping.get(field) for field in fields),
            )
            for mapping in mappings
        ])

    @staticmethod
    def _postgres_attachment_projection_update(table, fields, incoming):
        # PostgreSQL does not preserve SQLAlchemy's Python-side column types in
        # the rendered VALUES alias. A batch whose first values are NULL can
        # therefore infer text for integer/jsonb columns and make
        # IS DISTINCT FROM fail at runtime. Cast every alias column to the
        # authoritative destination type instead of fixing individual fields
        # after each production failure. raw_payload is declared with the
        # portable ORM JSON type but the PostgreSQL schema contract is JSONB,
        # so it must retain its explicit dialect type here.
        incoming_values = {
            field: cast(
                incoming.c[field],
                JSONB if field == "raw_payload" else table.c[field].type,
            )
            for field in fields
        }
        incoming_id = cast(incoming.c.id, table.c.id.type)
        return (
            update(table)
            .where(table.c.id == incoming_id)
            .where(or_(*(
                table.c[field].is_distinct_from(incoming_values[field])
                for field in fields
            )))
            .values({
                **incoming_values,
                "updated_at": datetime.utcnow(),
            })
            .execution_options(synchronize_session=False)
        )

    def _update_existing_attachment_projections(self, mappings):
        if not mappings:
            return
        table = ZsxqAttachment.__table__
        fields = (
            "group_id",
            "tab_id",
            "topic_id",
            "attachment_key",
            "attachment_type",
            "name",
            "file_id",
            "download_count",
            "file_ext",
            "raw_payload",
        )
        if db.session.get_bind().dialect.name == "postgresql":
            incoming = self._postgres_attachment_projection_values(
                table,
                fields,
                mappings,
            )
            statement = self._postgres_attachment_projection_update(
                table,
                fields,
                incoming,
            )
            db.session.execute(statement)
            return

        bound = {
            field: bindparam(f"_attachment_{field}")
            for field in fields
        }
        statement = (
            update(table)
            .where(table.c.id == bindparam("_attachment_id"))
            .where(or_(*(
                table.c[field].is_distinct_from(bound[field])
                for field in fields
            )))
            .values(**bound)
            .execution_options(synchronize_session=False)
        )
        parameters = [
            {
                "_attachment_id": str(mapping["id"]),
                **{
                    f"_attachment_{field}": mapping.get(field)
                    for field in fields
                },
            }
            for mapping in mappings
        ]
        db.session.execute(statement, parameters)

    def _refresh_existing_attachment_snapshots_for_page(
        self,
        group_id,
        tab,
        topics,
        existing_snapshots,
    ):
        mappings = []
        for topic in topics or ():
            topic_id = self._topic_id(topic)
            records = existing_snapshots.get(str(topic_id), ())
            records_by_key = {
                str(record.attachment_key): record
                for record in records
            }
            for key, attachment_type, payload in self._topic_attachment_items(topic):
                record = records_by_key.get(str(key))
                if not record:
                    continue
                values = self._attachment_projection_values(
                    group_id,
                    tab["id"],
                    topic_id,
                    key,
                    attachment_type,
                    payload,
                )
                projected = ZsxqAttachment(
                    name=values["name"],
                    local_path=getattr(record, "local_path", None),
                )
                values["file_ext"] = projected.file_ext
                mappings.append({"id": str(record.id), **values})
                for field, value in values.items():
                    setattr(record, field, value)
        if mappings:
            self._update_existing_attachment_projections(mappings)
        return len(mappings)

    def _upsert_attachments(
        self,
        group_id,
        tab,
        topic,
        *,
        existing_records=None,
        existing_records_persisted=False,
    ):
        topic_id = self._topic_id(topic)
        attachments = self._topic_attachment_items(topic)

        stats = {
            "attachments_seen": 0,
            "attachments_downloaded": 0,
            "attachments_failed": 0,
            "attachments_skipped": 0,
        }
        if existing_records is None:
            rows = (
                ZsxqAttachment.query
                .with_entities(*ATTACHMENT_DOWNLOAD_SNAPSHOT_COLUMNS)
                .filter_by(
                    group_id=group_id,
                    tab_id=tab["id"],
                    topic_id=topic_id,
                )
                .all()
            )
            existing_records = [
                SimpleNamespace(**{
                    column.key: getattr(row, column.key)
                    for column in ATTACHMENT_DOWNLOAD_SNAPSHOT_COLUMNS
                })
                for row in rows
            ]
        records_by_key = {
            str(record.attachment_key): record
            for record in existing_records
        }
        records = []
        pending_updates = []
        for key, attachment_type, payload in attachments:
            record = records_by_key.get(str(key))
            values = self._attachment_projection_values(
                group_id,
                tab["id"],
                topic_id,
                key,
                attachment_type,
                payload,
            )
            if record:
                projected = ZsxqAttachment(
                    name=values["name"],
                    local_path=getattr(record, "local_path", None),
                )
                values["file_ext"] = projected.file_ext
                if not existing_records_persisted:
                    pending_updates.append({"id": str(record.id), **values})
                for field, value in values.items():
                    setattr(record, field, value)
            else:
                record = ZsxqAttachment(**values)
                db.session.add(record)
                records_by_key[str(key)] = record
            records.append(record)
            stats["attachments_seen"] += 1
        if pending_updates:
            self._update_existing_attachment_projections(pending_updates)
        db.session.flush()
        snapshots = [
            SimpleNamespace(**{
                column.key: getattr(record, column.key)
                for column in ATTACHMENT_DOWNLOAD_SNAPSHOT_COLUMNS
            })
            for record in records
        ]
        return stats, snapshots

    def _download_topic_attachments(
        self,
        cookie,
        group_id,
        tab,
        topic,
        download_file_types=None,
        max_download_files=None,
        remaining_downloads=None,
        attachment_download_rules=None,
        run_id=None,
        records=None,
    ):
        topic_id = self._topic_id(topic)
        fresh_payloads = {
            key: payload
            for key, _attachment_type, payload in self._topic_attachment_items(topic)
        }
        if records is None:
            records = (
                ZsxqAttachment.query
                .with_entities(*ATTACHMENT_DOWNLOAD_SNAPSHOT_COLUMNS)
                .filter_by(
                    group_id=group_id,
                    tab_id=tab["id"],
                    topic_id=topic_id,
                )
                .all()
            )
        stats = {
            "attachments_seen": 0,
            "attachments_downloaded": 0,
            "attachments_failed": 0,
            "attachments_skipped": 0,
            "attachments_below_threshold": 0,
            "attachments_missing_download_count": 0,
            "attachments_non_target_skipped": 0,
            "attachments_already_downloaded_skipped": 0,
            "attachments_reused_local": 0,
            "attachments_reused_existing": 0,
            "attachments_limited_skipped": 0,
            "downloaded_files": [],
            "downloaded_pdf_files": [],
            "auto_video_dispatch_files": [],
            "stopped_by_download_limit": False,
            "download_limit_reached": False,
        }
        if remaining_downloads is None:
            remaining_downloads = int(max_download_files) if max_download_files else None

        for record in records:
            record_id = record.id
            record_name = record.name
            payload = fresh_payloads.get(str(record.attachment_key))
            if payload is None and self._attachment_type_needs_payload(record):
                payload = self._load_attachment_download_payload(record_id)
            if not self._should_download_attachment(
                record,
                download_file_types,
                raw_payload=payload,
            ):
                if record.download_status == "pending":
                    reason = (
                        "非目标类型附件，跳过自动下载: "
                        f"{self._attachment_file_type(record, raw_payload=payload)}"
                    )
                    self._update_attachment_download_state(
                        record_id,
                        download_status="skipped",
                        error_message=reason,
                    )
                    record.download_status = "skipped"
                    record.error_message = reason
                    stats["attachments_skipped"] += 1
                    stats["attachments_non_target_skipped"] += 1
                db.session.commit()
                continue
            threshold_result = self._attachment_download_rule_result(
                record,
                attachment_download_rules,
                raw_payload=payload,
            )
            if not threshold_result["allowed"]:
                reason = threshold_result["reason"]
                if record.download_status != "skipped" or record.error_message != reason:
                    self._update_attachment_download_state(
                        record_id,
                        download_status="skipped",
                        error_message=reason,
                    )
                    record.download_status = "skipped"
                    record.error_message = reason
                stats["attachments_skipped"] += 1
                if threshold_result.get("missing_download_count"):
                    stats["attachments_missing_download_count"] += 1
                else:
                    stats["attachments_below_threshold"] += 1
                db.session.commit()
                continue
            if self._download_record_is_reusable(record):
                stats["attachments_skipped"] += 1
                stats["attachments_already_downloaded_skipped"] += 1
                stats["attachments_reused_local"] += 1
                self._append_auto_video_dispatch_file_entry(stats, record, record.local_path)
                continue
            existing_download = self._find_existing_downloaded_attachment(record)
            if existing_download:
                existing_path = existing_download.local_path
                if (
                    record.local_path != existing_path
                    or record.download_status != "downloaded"
                    or record.error_message is not None
                ):
                    self._update_attachment_download_state(
                        record_id,
                        local_path=existing_path,
                        download_status="downloaded",
                        error_message=None,
                    )
                    record.local_path = existing_path
                    record.download_status = "downloaded"
                    record.error_message = None
                stats["attachments_skipped"] += 1
                stats["attachments_already_downloaded_skipped"] += 1
                stats["attachments_reused_existing"] += 1
                self._append_auto_video_dispatch_file_entry(stats, record, existing_path)
                db.session.commit()
                continue
            if remaining_downloads is not None and remaining_downloads <= 0:
                stats["stopped_by_download_limit"] = True
                stats["download_limit_reached"] = True
                stats["attachments_limited_skipped"] += 1
                db.session.commit()
                break
            db.session.commit()
            self._attachment_download_pause()
            attempted_download = False
            record_for_update = None
            try:
                attempted_download = True
                if payload is None:
                    payload = self._load_attachment_download_payload(record_id)
                local_path = self._download_attachment_file(cookie, group_id, tab, topic_id, payload, record_name)
                record_for_update = self._load_attachment_for_download_update(record_id)
                if not record_for_update:
                    raise ValueError("附件记录不存在，无法写入下载结果")
                record_for_update.local_path = local_path
                record_for_update.download_status = "downloaded"
                record_for_update.error_message = None
                stats["attachments_downloaded"] += 1
                entry = self._downloaded_file_entry(record_for_update, local_path)
                if entry:
                    self._append_unique_file_entry(stats["downloaded_files"], entry)
                    if entry["path"].lower().endswith(".pdf"):
                        self._append_unique_file_entry(stats["downloaded_pdf_files"], entry)
                self._record_sync_run_download(run_id, group_id, record_for_update, local_path)
                db.session.commit()
            except Exception as exc:
                db.session.rollback()
                if isinstance(exc, SQLAlchemyError) or is_retryable_db_error(exc):
                    raise
                if record_for_update is None and record_id:
                    record_for_update = self._load_attachment_for_download_update(record_id)
                if record_for_update:
                    record_for_update.download_status = "failed"
                    record_for_update.error_message = str(exc)[:1000]
                stats["attachments_failed"] += 1
                logger.warning(
                    "[ZSXQ_SYNC] attachment download failed | group_id=%s | tab=%s | topic_id=%s | name=%s | error=%s",
                    group_id,
                    tab["id"],
                    topic_id,
                    record_name,
                    exc,
                )
                db.session.commit()
            finally:
                if attempted_download and remaining_downloads is not None:
                    remaining_downloads = max(0, remaining_downloads - 1)
                    if remaining_downloads <= 0:
                        stats["stopped_by_download_limit"] = True
                        stats["download_limit_reached"] = True
        stats["remaining_downloads"] = remaining_downloads
        if stats["attachments_downloaded"] or stats["attachments_failed"] or stats["attachments_skipped"]:
            self._clear_attachment_status_cache()
        return stats

    def _download_attachment_file(self, cookie, group_id, tab, topic_id, payload, name):
        end_db_transaction_before_external_io("ZSXQ attachment download")
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
        for key in ATTACHMENT_AUDIT_COUNTERS:
            target[key] = target.get(key, 0) + int((stats or {}).get(key, 0) or 0)
        self._merge_downloaded_file_entries(target, stats)

    def _merge_downloaded_file_entries(self, target, stats):
        if not stats:
            return
        for key in ("downloaded_files", "downloaded_pdf_files", "auto_video_dispatch_files"):
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

    def _record_sync_run_download(self, run_id, group_id, record, local_path):
        if not run_id or not record or not getattr(record, "id", None) or not local_path:
            return
        name = str(getattr(record, "name", None) or "").strip() or None
        file_ext = (
            str(getattr(record, "file_ext", None) or "").strip().lower().lstrip(".")
            or Path(str(local_path or "")).suffix.lower().lstrip(".")
            or Path(str(name or "")).suffix.lower().lstrip(".")
            or None
        )
        values = {
            "run_id": str(run_id),
            "attachment_id": str(record.id),
            "group_id": str(group_id),
            "tab_id": str(record.tab_id),
            "topic_id": str(record.topic_id),
            "name": name[:500] if name else None,
            "local_path": str(local_path)[:600],
            "file_ext": file_ext[:20] if file_ext else None,
            "downloaded_at": datetime.utcnow(),
        }
        dialect = db.engine.dialect.name
        if dialect == "postgresql":
            from sqlalchemy.dialects.postgresql import insert as dialect_insert
        elif dialect == "sqlite":
            from sqlalchemy.dialects.sqlite import insert as dialect_insert
        else:
            db.session.merge(ZsxqSyncRunDownload(**values))
            return
        statement = dialect_insert(ZsxqSyncRunDownload).values(**values)
        statement = statement.on_conflict_do_update(
            index_elements=["run_id", "attachment_id"],
            set_={
                key: value
                for key, value in values.items()
                if key not in {"run_id", "attachment_id"}
            },
        )
        db.session.execute(statement)

    def _load_attachment_for_download_update(self, record_id):
        if not record_id:
            return None
        return (
            ZsxqAttachment.query.options(
                load_only(
                    ZsxqAttachment.id,
                    ZsxqAttachment.name,
                    ZsxqAttachment.topic_id,
                    ZsxqAttachment.tab_id,
                    ZsxqAttachment.local_path,
                    ZsxqAttachment.download_status,
                    ZsxqAttachment.error_message,
                )
            )
            .filter(ZsxqAttachment.id == str(record_id))
            .one_or_none()
        )

    def _load_attachment_download_payload(self, record_id):
        if not record_id:
            return {}
        row = (
            db.session.query(ZsxqAttachment.raw_payload)
            .filter(ZsxqAttachment.id == str(record_id))
            .one_or_none()
        )
        payload = row[0] if row else None
        return payload if isinstance(payload, dict) else {}

    def _update_attachment_download_state(self, record_id, **values):
        if not record_id or not values:
            return
        table = ZsxqAttachment.__table__
        changed = [
            table.c[key].is_distinct_from(value)
            for key, value in values.items()
            if key in table.c and key != "updated_at"
        ]
        statement = update(table).where(table.c.id == str(record_id))
        if changed:
            statement = statement.where(or_(*changed))
        db.session.execute(
            statement
            .values(**values)
            .execution_options(synchronize_session=False)
        )

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

    def normalize_manual_attachment_plan(self, manual_attachment_plan, group_id, tabs):
        return self._normalize_manual_attachment_plan(
            manual_attachment_plan,
            group_id=group_id,
            tabs=self._normalize_tabs(tabs),
        )

    def public_manual_attachment_plan(self, manual_plan):
        return self._public_manual_attachment_plan(manual_plan)

    def _normalize_manual_attachment_plan(self, manual_attachment_plan, group_id, tabs):
        if not manual_attachment_plan:
            return {"enabled": False, "items": [], "selected_count": 0}
        if isinstance(manual_attachment_plan, list):
            raw_items = manual_attachment_plan
            enabled = True
        elif isinstance(manual_attachment_plan, dict):
            raw_items = (
                manual_attachment_plan.get("items")
                or manual_attachment_plan.get("attachments")
                or []
            )
            enabled = self._truthy(manual_attachment_plan.get("enabled", True)) or bool(raw_items)
            requested_limit = manual_attachment_plan.get("max_download_files") or manual_attachment_plan.get("limit")
        else:
            raise ValueError("manual_attachment_plan 格式不正确")
        if isinstance(manual_attachment_plan, list):
            requested_limit = None
        if not enabled or not raw_items:
            return {"enabled": False, "items": [], "selected_count": 0}
        if not isinstance(raw_items, list):
            raise ValueError("manual_attachment_plan.items 必须是数组")

        default_tab = tabs[0] if tabs else {"id": "", "label": ""}
        items = []
        seen = set()
        for index, raw in enumerate(raw_items):
            if not isinstance(raw, dict):
                raise ValueError(f"第 {index + 1} 个附件计划格式不正确")
            file_info = raw.get("file")
            if not isinstance(file_info, dict):
                raise ValueError(f"第 {index + 1} 个附件缺少 file 原始信息")
            topic_id = str(raw.get("topic_id") or raw.get("topicId") or "").strip()
            if not topic_id:
                raise ValueError(f"第 {index + 1} 个附件缺少 topic_id")
            tab_id = str(raw.get("tab_id") or raw.get("tabId") or default_tab.get("id") or "").strip()
            if not tab_id:
                raise ValueError(f"第 {index + 1} 个附件缺少 tab_id")
            attachment_key = str(raw.get("attachment_key") or self._attachment_key(file_info, index, "file") or "").strip()
            file_id = str(raw.get("file_id") or self._attachment_file_id(file_info) or "").strip()
            name = str(raw.get("name") or self._attachment_name(file_info, "file") or "附件").strip()
            dedupe_key = (
                tab_id,
                topic_id,
                attachment_key or file_id or name,
            )
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            items.append({
                "group_id": str(raw.get("group_id") or group_id or "").strip(),
                "tab_id": tab_id,
                "tab_label": str(raw.get("tab_label") or raw.get("tabLabel") or default_tab.get("label") or tab_id).strip(),
                "hashtag_id": raw.get("hashtag_id") or raw.get("hashtagId"),
                "topic_id": topic_id,
                "topic_title": str(raw.get("topic_title") or raw.get("topicTitle") or raw.get("title") or name).strip(),
                "topic_content": str(raw.get("topic_content") or raw.get("topicContent") or "").strip(),
                "author_name": str(raw.get("author_name") or raw.get("authorName") or "未知作者").strip(),
                "create_time": str(raw.get("create_time") or raw.get("createTime") or "").strip(),
                "attachment_key": attachment_key[:255],
                "file_id": file_id[:128],
                "name": name[:500],
                "file_type": str(raw.get("file_type") or raw.get("fileType") or "").lower().lstrip("."),
                "file": file_info,
            })

        limit = self._normalize_manual_attachment_plan_limit(requested_limit)
        if len(items) > limit:
            raise ValueError(f"已选择 {len(items)} 个附件，超过页面配置的单次上限 {limit} 个。请调大上限或拆成多个小批次。")
        return {
            "enabled": True,
            "items": items,
            "selected_count": len(items),
            "limit": limit,
        }

    def _public_manual_attachment_plan(self, manual_plan):
        items = manual_plan.get("items") or []
        return {
            "enabled": bool(items),
            "selected_count": len(items),
            "limit": manual_plan.get("limit") or self._normalize_manual_attachment_plan_limit(None),
            "hard_limit": self._manual_attachment_plan_hard_limit(),
            "items": [
                {
                    "tab_id": item.get("tab_id"),
                    "tab_label": item.get("tab_label"),
                    "topic_id": item.get("topic_id"),
                    "topic_title": item.get("topic_title"),
                    "attachment_key": item.get("attachment_key"),
                    "file_id": item.get("file_id"),
                    "name": item.get("name"),
                    "file_type": item.get("file_type"),
                }
                for item in items
            ],
        }

    def _manual_topic_payload(self, item):
        owner = {"name": item.get("author_name") or "未知作者"}
        create_time = item.get("create_time") or ""
        text = item.get("topic_content") or item.get("topic_title") or item.get("name") or ""
        return {
            "topic_id": item["topic_id"],
            "title": item.get("topic_title") or item.get("name") or "人工计划附件",
            "owner": owner,
            "create_time": create_time,
            "files": [item["file"]],
            "talk": {
                "text": text,
                "owner": owner,
                "create_time": create_time,
                "files": [item["file"]],
            },
        }

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
                end_db_transaction_before_external_io("ZSXQ topic-list request")
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
                end_db_transaction_before_external_io("ZSXQ upstream retry wait")
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
        content = self._content(topic)
        title = (
            topic.get("title")
            or (topic.get("article") or {}).get("title")
            or content[:48]
            or "无标题主题"
        )
        title = self._normalize_topic_text(title, include_web_url=False).replace("\n", " ").strip() or "无标题主题"
        owner = topic.get("owner") or (topic.get("talk") or {}).get("owner") or {}
        return {
            "title": str(title)[:500],
            "author_name": owner.get("name") or "未知作者",
            "content": content,
            "create_time": topic.get("create_time") or (topic.get("talk") or {}).get("create_time") or "",
        }

    def _content(self, topic):
        if not isinstance(topic, dict):
            return ""
        question_text = self._normalize_topic_text((topic.get("question") or {}).get("text") or "")
        answer_text = self._normalize_topic_text((topic.get("answer") or {}).get("text") or "")
        if question_text and answer_text:
            return f"提问：{question_text}\n\n回答：{answer_text}"
        text = (
            (topic.get("talk") or {}).get("text")
            or (topic.get("article") or {}).get("article_content")
            or (topic.get("article") or {}).get("title")
            or question_text
            or answer_text
            or ""
        )
        return self._normalize_topic_text(text)

    def _normalize_topic_text(self, text, include_web_url=True):
        def decode_attr(value):
            try:
                return unquote(str(value or ""))
            except Exception:
                return str(value or "")

        def replace_entity(match):
            tag = match.group(0)
            attrs = {
                key: decode_attr(value)
                for key, value in re.findall(r"([a-zA-Z0-9_-]+)=[\"']([^\"']*)[\"']", tag)
            }
            entity_type = str(attrs.get("type") or "").lower()
            title = str(attrs.get("title") or "").strip()
            href = str(attrs.get("href") or attrs.get("url") or "").strip()
            if entity_type == "web":
                if not include_web_url:
                    return title or ""
                if title and href and title != href:
                    return f"{title}\n{href}"
                return title or href or ""
            if title:
                return title
            if include_web_url and href:
                return href
            return ""

        value = unescape(str(text or ""))
        value = re.sub(r"<e\b[^>]*\/?>", replace_entity, value)
        value = re.sub(r"</e>", "", value)
        value = value.replace("\r\n", "\n").replace("\r", "\n")
        value = re.sub(r"\n{3,}", "\n\n", value)
        return value.strip()

    def _topic_files(self, topic):
        return topic.get("files") or (topic.get("talk") or {}).get("files") or (topic.get("article") or {}).get("files") or []

    def _topic_attachment_items(self, topic):
        attachments = [("file", item) for item in self._topic_files(topic)]
        attachments.extend(
            ("image", item)
            for _context, item in self._topic_images_with_context(topic)
        )
        return [
            (self._attachment_key(payload, index, attachment_type), attachment_type, payload)
            for index, (attachment_type, payload) in enumerate(attachments)
        ]

    def _topic_images(self, topic):
        images = []
        for _context, payload in self._topic_images_with_context(topic):
            item = dict(payload)
            item.pop("_zsxq_image_context", None)
            images.append(item)
        return images

    def _topic_images_with_context(self, topic):
        images = []
        topic = topic if isinstance(topic, dict) else {}
        talk = topic.get("talk") if isinstance(topic.get("talk"), dict) else {}
        article = topic.get("article") if isinstance(topic.get("article"), dict) else {}
        talk_article = talk.get("article") if isinstance(talk.get("article"), dict) else {}
        question = topic.get("question") if isinstance(topic.get("question"), dict) else {}
        answer = topic.get("answer") if isinstance(topic.get("answer"), dict) else {}
        for context, item in (
            ("topic", topic.get("images")),
            ("talk", talk.get("images")),
            ("article", article.get("images")),
            ("talk_article", talk_article.get("images")),
            ("question", question.get("images")),
            ("answer", answer.get("images")),
        ):
            if isinstance(item, list):
                for image in item:
                    if isinstance(image, dict):
                        payload = dict(image)
                    else:
                        payload = {"url": str(image)}
                    payload.setdefault("_zsxq_image_context", context)
                    images.append((context, payload))
        return images

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

    def _effective_topic_max_pages(self, target_count, requested_max_pages):
        try:
            target_count = min(max(int(target_count or 20), 1), 50)
        except (TypeError, ValueError):
            target_count = 20
        try:
            requested_max_pages = min(max(int(requested_max_pages or 1), 1), 20)
        except (TypeError, ValueError):
            requested_max_pages = 1
        pages_for_target = max(1, (target_count + TOPIC_PAGE_SIZE_HINT - 1) // TOPIC_PAGE_SIZE_HINT)
        return min(max(requested_max_pages, pages_for_target), 20)

    def _normalize_attachment_download_rules(self, value, tabs=None):
        if not isinstance(value, dict):
            return {"enabled": False, "rules": []}

        enabled = self._truthy(value.get("enabled", False))
        tabs_by_id = {
            str(tab.get("id")): tab
            for tab in (tabs or [])
            if isinstance(tab, dict) and tab.get("id")
        }
        rules = []
        for raw_rule in value.get("rules") or []:
            if not isinstance(raw_rule, dict):
                continue
            file_type = str(raw_rule.get("file_type") or "").strip().lower().lstrip(".")
            if file_type not in {"pdf", "mp3", "all"}:
                continue
            tab_id = str(raw_rule.get("tab_id") or "").strip()
            if tab_id and tabs_by_id and tab_id not in tabs_by_id:
                continue
            threshold = self._int_or_none(raw_rule.get("min_download_count"))
            if threshold is None:
                threshold = self._int_or_none(raw_rule.get("threshold"))
            if threshold is None:
                continue
            threshold = min(max(int(threshold), 0), 1_000_000)
            tab = tabs_by_id.get(tab_id) or {}
            rules.append({
                "key": str(raw_rule.get("key") or f"{tab_id or '*'}:{file_type}")[:80],
                "tab_id": tab_id,
                "tab_label": str(raw_rule.get("tab_label") or tab.get("label") or ""),
                "file_type": file_type,
                "min_download_count": threshold,
            })
        return {
            "enabled": bool(enabled and rules),
            "rules": rules,
        }

    def _public_attachment_download_rules(self, rules):
        normalized = self._normalize_attachment_download_rules(rules)
        return {
            "enabled": normalized.get("enabled", False),
            "rules": [dict(item) for item in normalized.get("rules") or []],
        }

    def _normalize_max_download_files(self, max_download_files):
        if max_download_files in (None, "", 0, "0"):
            return None
        try:
            return min(max(int(max_download_files), 1), 100)
        except (TypeError, ValueError):
            raise ValueError("max_download_files 必须是正整数")

    def _attachment_download_policy(
        self,
        *,
        requested,
        explicit_allow=False,
        max_download_files=None,
        manual_plan_count=0,
        threshold_rule_count=0,
    ):
        requested = self._truthy(requested)
        normalized_limit = self._normalize_max_download_files(max_download_files)
        env_enabled = self._truthy(os.getenv(ATTACHMENT_DOWNLOADS_ENABLED_ENV))
        explicit_allowed = self._truthy(explicit_allow)
        manual_plan_count = min(max(int(manual_plan_count or 0), 0), 10_000)
        threshold_rule_count = min(max(int(threshold_rule_count or 0), 0), 10_000)
        is_manual_plan = manual_plan_count > 0
        is_threshold_guarded = threshold_rule_count > 0
        safe_cap = (
            self._manual_attachment_plan_hard_limit()
            if is_manual_plan
            else (DEFAULT_THRESHOLD_ATTACHMENT_DOWNLOAD_LIMIT if is_threshold_guarded else self._safe_attachment_download_limit())
        )

        policy = {
            "requested": requested,
            "enabled": False,
            "explicit_allow": explicit_allowed,
            "env_enabled": env_enabled,
            "manual_plan": is_manual_plan,
            "manual_plan_count": manual_plan_count,
            "threshold_guarded": is_threshold_guarded,
            "threshold_rule_count": threshold_rule_count,
            "env_name": ATTACHMENT_DOWNLOADS_ENABLED_ENV,
            "limit_env_name": ATTACHMENT_DOWNLOAD_LIMIT_ENV,
            "safe_cap": safe_cap,
            "requested_max_download_files": normalized_limit,
            "max_download_files": 0,
            "reason": "metadata_only",
        }
        if not requested:
            return policy
        if not explicit_allowed:
            policy["reason"] = "missing_explicit_allow"
            return policy
        if not env_enabled and not is_manual_plan and not is_threshold_guarded:
            policy["reason"] = "env_disabled"
            return policy

        if is_manual_plan and manual_plan_count > safe_cap:
            policy["reason"] = "manual_plan_limit_exceeded"
            return policy

        effective_limit = normalized_limit if normalized_limit is not None else (manual_plan_count if is_manual_plan else safe_cap)
        effective_limit = min(max(int(effective_limit or 0), 0), safe_cap)
        if effective_limit <= 0:
            policy["reason"] = "zero_limit"
            return policy

        policy["enabled"] = True
        policy["max_download_files"] = effective_limit
        if is_manual_plan and not env_enabled:
            policy["reason"] = "manual_plan_enabled"
        elif is_threshold_guarded and not env_enabled:
            policy["reason"] = "threshold_guarded_enabled"
        else:
            policy["reason"] = "enabled"
        return policy

    def _safe_attachment_download_limit(self):
        try:
            value = int(os.getenv(ATTACHMENT_DOWNLOAD_LIMIT_ENV, str(DEFAULT_SAFE_ATTACHMENT_DOWNLOAD_LIMIT)))
        except (TypeError, ValueError):
            value = DEFAULT_SAFE_ATTACHMENT_DOWNLOAD_LIMIT
        return min(max(value, 0), 3)

    def _normalize_manual_attachment_plan_limit(self, value):
        hard_limit = self._manual_attachment_plan_hard_limit()
        if value in (None, "", 0, "0"):
            return min(DEFAULT_MANUAL_ATTACHMENT_PLAN_LIMIT, hard_limit)
        try:
            number = int(value)
        except (TypeError, ValueError):
            raise ValueError("人工计划单次上限必须是正整数")
        return min(max(number, 1), hard_limit)

    def _manual_attachment_plan_hard_limit(self):
        raw_value = (
            os.getenv(MANUAL_ATTACHMENT_PLAN_HARD_LIMIT_ENV)
            or os.getenv(MANUAL_ATTACHMENT_PLAN_LIMIT_ENV)
            or str(DEFAULT_MANUAL_ATTACHMENT_PLAN_HARD_LIMIT)
        )
        try:
            value = int(raw_value)
        except (TypeError, ValueError):
            value = DEFAULT_MANUAL_ATTACHMENT_PLAN_HARD_LIMIT
        return min(max(value, 1), 100)

    def _truthy(self, value):
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value != 0
        return str(value or "").strip().lower() in {"1", "true", "yes", "on", "enabled", "y"}

    def _should_download_attachment(self, record, download_file_types, raw_payload=None):
        if not download_file_types or "all" in download_file_types:
            return record.attachment_type == "file"
        return self._attachment_file_type(record, raw_payload=raw_payload) in download_file_types

    def _attachment_download_rule_result(self, record, rules, raw_payload=None):
        normalized = self._normalize_attachment_download_rules(rules)
        if not normalized.get("enabled"):
            return {"allowed": True}

        file_type = self._attachment_file_type(record, raw_payload=raw_payload)
        matching_rules = [
            rule
            for rule in normalized.get("rules") or []
            if (not rule.get("tab_id") or str(rule.get("tab_id")) == str(record.tab_id))
            and (rule.get("file_type") in {"all", file_type})
        ]
        if not matching_rules:
            return {
                "allowed": False,
                "reason": f"未匹配自动下载阈值规则，跳过: {file_type}",
            }

        threshold = max(int(rule.get("min_download_count") or 0) for rule in matching_rules)
        if threshold <= 0:
            return {"allowed": True, "threshold": threshold}

        current_count = self._int_or_none(record.download_count)
        if current_count is None:
            return {
                "allowed": False,
                "threshold": threshold,
                "missing_download_count": True,
                "reason": f"缺少下载次数，未达到自动下载阈值: {file_type} >= {threshold}",
            }
        if current_count < threshold:
            return {
                "allowed": False,
                "threshold": threshold,
                "download_count": current_count,
                "reason": f"下载次数 {current_count} 未达到自动下载阈值 {threshold}，跳过: {file_type}",
            }
        return {
            "allowed": True,
            "threshold": threshold,
            "download_count": current_count,
        }

    def _attachment_type_needs_payload(self, record):
        return (
            str(getattr(record, "attachment_type", "") or "") == "file"
            and self._attachment_file_type(record, raw_payload={}) == "file"
        )

    def _attachment_file_type(self, record, raw_payload=None):
        attachment_type = str(getattr(record, "attachment_type", "") or "")
        if attachment_type != "file":
            return attachment_type or "unknown"

        projected_ext = str(getattr(record, "file_ext", "") or "").strip().lower().lstrip(".")
        if projected_ext:
            return projected_ext

        name = str(getattr(record, "name", "") or "")
        payload = raw_payload if isinstance(raw_payload, dict) else getattr(record, "raw_payload", None)
        payload = payload if isinstance(payload, dict) else {}
        content_type = str(payload.get("content_type") or payload.get("mime_type") or payload.get("mime") or "")
        lowered_name = name.lower()
        lowered_type = content_type.lower()
        if lowered_name.endswith(".pdf") or "pdf" in lowered_type:
            return "pdf"
        if lowered_name.endswith(".mp3") or "mpeg" in lowered_type or "mp3" in lowered_type:
            return "mp3"
        match = re.search(r"\.([a-z0-9]{2,5})(?:$|\?)", lowered_name)
        return match.group(1) if match else "file"

    def _is_pdf_attachment(self, record):
        return self._attachment_file_type(record) == "pdf"

    def _human_pause(self, min_seconds=4.5, max_seconds=7.0):
        end_db_transaction_before_external_io("ZSXQ pacing wait")
        seconds = min_seconds + random.random() * (max_seconds - min_seconds)
        logger.info("[ZSXQ_SYNC] pacing wait %.1fs", seconds)
        time.sleep(seconds)

    def _attachment_download_pause(self):
        if self._last_pdf_download_at:
            self._human_pause(9, 12)
        self._last_pdf_download_at = time.time()


zsxq_sync_service = ZsxqSyncService()
