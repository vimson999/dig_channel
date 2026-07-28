import logging
import os
import time
import uuid
from copy import deepcopy
from datetime import datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import or_, text
from sqlalchemy.orm import load_only
from sqlalchemy.orm.attributes import flag_modified

from models import db
from models.task import PublicationTask
from models.zsxq_sync import ZsxqSyncRun
from models.zsxq_workflow import ZsxqWorkflowRun
from services.content_pipeline.auto_publish_handoff import (
    submit_publish_clean_output_task,
    summarize_publish_runs_for_request,
)
from services.workflow_notification_service import workflow_notification_service
from services.runtime_guard import (
    apply_async_after_db_release,
    is_retryable_db_error,
    release_db_session_quietly,
)
from services.zsxq_sync_service import zsxq_sync_service
from services.zsxq_system_sync_test import SYSTEM_SYNC_GROUP_ID, SYSTEM_SYNC_GROUP_NAME


logger = logging.getLogger(__name__)

TERMINAL_WORKFLOW_STATUSES = {"completed", "failed", "partial", "skipped"}
TERMINAL_TASK_STATUSES = {"completed", "failed"}
QUEUED_STALE_MINUTES_ENV_NAME = "ZSXQ_WORKFLOW_QUEUED_STALE_MINUTES"
DEFAULT_QUEUED_STALE_MINUTES = 20
CLEAN_STALE_MINUTES_ENV_NAME = "ZSXQ_WORKFLOW_CLEAN_STALE_MINUTES"
DEFAULT_CLEAN_STALE_MINUTES = 360
SYNC_STALE_MINUTES_ENV_NAME = "ZSXQ_WORKFLOW_SYNC_STALE_MINUTES"
DEFAULT_SYNC_STALE_MINUTES = 30
SCHEMA_CHECK_TTL_SECONDS_ENV_NAME = "ZSXQ_WORKFLOW_SCHEMA_CHECK_TTL_SECONDS"
DEFAULT_SCHEMA_CHECK_TTL_SECONDS = 300
QUEUE_DB_RETRIES_ENV_NAME = "ZSXQ_WORKFLOW_QUEUE_DB_RETRIES"
QUEUE_DB_RETRY_DELAY_SECONDS_ENV_NAME = "ZSXQ_WORKFLOW_QUEUE_DB_RETRY_DELAY_SECONDS"
DEFAULT_QUEUE_DB_RETRIES = 3
DEFAULT_QUEUE_DB_RETRY_DELAY_SECONDS = 10
DEFAULT_AUTO_VIDEO_PDF_THRESHOLD = 35
DEFAULT_AUTO_VIDEO_MP3_THRESHOLD = 25
DEFAULT_AUTO_SCRIPT_MATERIAL_PDF_THRESHOLD = 40
TOPIC_PAGE_SIZE_HINT = 20
REQUIRED_CLEAN_PROJECTION_COLUMNS = {
    "content_clean_output_dir",
    "content_clean_files_count",
    "content_clean_success_count",
    "content_clean_failed_count",
    "content_clean_message",
}
WORKFLOW_TASK_STATE_COLUMNS = (
    PublicationTask.id,
    PublicationTask.celery_task_id,
    PublicationTask.status,
    PublicationTask.current_step,
    PublicationTask.progress,
    PublicationTask.error_message,
    PublicationTask.feature,
    PublicationTask.content_clean_output_dir,
    PublicationTask.content_clean_files_count,
    PublicationTask.content_clean_success_count,
    PublicationTask.content_clean_failed_count,
    PublicationTask.content_clean_message,
    PublicationTask.updated_at,
    PublicationTask.completed_at,
    PublicationTask.created_at,
)
WORKFLOW_CLEAN_RESULT_COLUMNS = (
    PublicationTask.id,
    PublicationTask.feature,
    PublicationTask.content_clean_output_dir,
    PublicationTask.content_clean_files_count,
    PublicationTask.content_clean_success_count,
    PublicationTask.content_clean_failed_count,
    PublicationTask.content_clean_message,
)
ZSXQ_SYNC_RUN_STATE_COLUMNS = (
    ZsxqSyncRun.id,
    ZsxqSyncRun.status,
    ZsxqSyncRun.error_message,
    ZsxqSyncRun.completed_at,
    ZsxqSyncRun.updated_at,
)
ZSXQ_SYNC_RUN_HEARTBEAT_COLUMNS = (
    ZsxqSyncRun.id,
    ZsxqSyncRun.result,
)
ZSXQ_SYNC_RUN_RESULT_COLUMNS = (
    *ZSXQ_SYNC_RUN_STATE_COLUMNS,
    ZsxqSyncRun.group_id,
    ZsxqSyncRun.group_name,
    ZsxqSyncRun.tab_id,
    ZsxqSyncRun.tab_label,
    ZsxqSyncRun.mode,
    ZsxqSyncRun.requested_tabs,
    ZsxqSyncRun.result,
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
    ZsxqSyncRun.download_ledger_complete,
    ZsxqSyncRun.started_at,
    ZsxqSyncRun.created_at,
)
ZSXQ_WORKFLOW_STATE_COLUMNS = (
    ZsxqWorkflowRun.id,
    ZsxqWorkflowRun.batch_id,
    ZsxqWorkflowRun.group_id,
    ZsxqWorkflowRun.group_name,
    ZsxqWorkflowRun.trigger,
    ZsxqWorkflowRun.status,
    ZsxqWorkflowRun.current_stage,
    ZsxqWorkflowRun.round,
    ZsxqWorkflowRun.max_rounds,
    ZsxqWorkflowRun.sync_run_id,
    ZsxqWorkflowRun.error_message,
    ZsxqWorkflowRun.started_at,
    ZsxqWorkflowRun.completed_at,
    ZsxqWorkflowRun.created_at,
    ZsxqWorkflowRun.updated_at,
)
ZSXQ_WORKFLOW_RUNTIME_COLUMNS = (
    *ZSXQ_WORKFLOW_STATE_COLUMNS,
    ZsxqWorkflowRun.config_snapshot,
    ZsxqWorkflowRun.stage_results,
    ZsxqWorkflowRun.clean_task_ids,
    ZsxqWorkflowRun.publish_task_ids,
)


class ZsxqWorkflowService:
    """Lightweight state machine for the ZSXQ sync -> clean -> publish workflow."""

    def __init__(self):
        self._schema_checked_at = None

    def ensure_schema(self):
        now = datetime.utcnow()
        if self._schema_checked_at and (now - self._schema_checked_at).total_seconds() < _schema_check_ttl_seconds():
            return

        backend = db.engine.url.get_backend_name()
        if backend == "sqlite":
            raise ValueError(
                "工作流状态表必须创建在远端 PostgreSQL 中。当前 DATABASE_URL 指向本地 SQLite，"
                "请配置 PostgreSQL DATABASE_URL 后执行 docs/sql/zsxq_workflow_runs.sql。"
            )
        contract = db.session.execute(text("""
            WITH target AS (
                SELECT to_regclass('public.zsxq_workflow_runs') AS workflow_table
            )
            SELECT
                target.workflow_table IS NOT NULL AS workflow_table_exists,
                COALESCE((
                    SELECT jsonb_agg(column_name ORDER BY ordinal_position)
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = 'publication_tasks'
                ), '[]'::jsonb) AS publication_columns,
                CASE
                    WHEN target.workflow_table IS NULL THEN FALSE
                    ELSE has_table_privilege(
                        current_user,
                        target.workflow_table,
                        'SELECT, INSERT, UPDATE, DELETE'
                    )
                END AS has_workflow_privilege
            FROM target
        """)).one()
        if not contract.workflow_table_exists:
            raise ValueError(
                "知识星球工作流表尚未初始化，请先在远端 PostgreSQL 执行 "
                "docs/sql/zsxq_workflow_runs.sql；缺失表：zsxq_workflow_runs"
            )
        publication_columns = set(contract.publication_columns or ())
        missing_clean_columns = sorted(REQUIRED_CLEAN_PROJECTION_COLUMNS - publication_columns)
        if missing_clean_columns:
            raise ValueError(
                "清洗工作流结构化投影尚未部署，请先执行 "
                "docs/sql/content_pipeline_run_download_projection_dbeaver_20260718.sql；缺失列："
                + ", ".join(missing_clean_columns)
            )
        if not contract.has_workflow_privilege:
            raise ValueError(
                "当前数据库账号没有 public.zsxq_workflow_runs 的读写权限。请用表 owner 或管理员执行："
                "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.zsxq_workflow_runs TO cappa_rw;"
            )
        self._schema_checked_at = now

    def create_system_workflow(
        self,
        *,
        config,
        trigger,
        batch_id,
        batch_round,
        max_rounds,
        max_download_files,
        jobs,
    ):
        snapshot = self._build_system_snapshot(
            config=config,
            trigger=trigger,
            batch_id=batch_id,
            batch_round=batch_round,
            max_rounds=max_rounds,
            max_download_files=max_download_files,
            jobs=jobs,
        )
        group_id = str(config.get("group_id") or "")
        return self._create_workflow_with_db_retry(
            label=f"system batch={batch_id} round={batch_round}",
            existing_lookup=lambda: self._find_existing_system_workflow(
                batch_id=batch_id,
                group_id=group_id,
                batch_round=batch_round,
            ),
            workflow_factory=lambda: ZsxqWorkflowRun(
                batch_id=batch_id,
                group_id=group_id,
                group_name=config.get("group_name"),
                trigger=trigger,
                status="queued",
                current_stage="init",
                round=batch_round,
                max_rounds=max_rounds,
                config_snapshot=snapshot,
                stage_results={
                    "workflow": {
                        "status": "created",
                        "created_at": self._now_iso(),
                    }
                },
            ),
        )

    def create_manual_download_workflow(self, *, config, trigger):
        snapshot = self._build_manual_download_snapshot(config=config, trigger=trigger)
        system_sync = snapshot.get("system_sync") or {}
        sync_snapshot = snapshot.get("sync") or {}
        batch_id = system_sync.get("batch_id")
        group_id = str(sync_snapshot.get("group_id") or "")
        return self._create_workflow_with_db_retry(
            label=f"manual batch={batch_id}",
            existing_lookup=lambda: self._find_existing_manual_workflow(
                batch_id=batch_id,
                group_id=group_id,
                trigger=trigger,
            ),
            workflow_factory=lambda: ZsxqWorkflowRun(
                batch_id=batch_id,
                group_id=group_id,
                group_name=sync_snapshot.get("group_name"),
                trigger=trigger,
                status="queued",
                current_stage="init",
                round=None,
                max_rounds=None,
                config_snapshot=snapshot,
                stage_results={
                    "workflow": {
                        "status": "created",
                        "created_at": self._now_iso(),
                    },
                    "manual_download_plan": system_sync.get("manual_attachment_plan") or {},
                },
            ),
        )

    def enqueue_workflow_task(
        self,
        workflow_run_id,
        *,
        cookie=None,
        credential_id=None,
        workflow_payload=None,
    ):
        self.ensure_schema()
        supplied_payload = deepcopy(workflow_payload) if isinstance(workflow_payload, dict) else None
        workflow = None
        if supplied_payload is not None:
            supplied_id = str(supplied_payload.get("id") or "")
            if supplied_id != str(workflow_run_id):
                raise ValueError("工作流快照与入队 ID 不一致")
        else:
            workflow = self._load_workflow_run(workflow_run_id, include_runtime=True)
            if not workflow:
                raise ValueError(f"工作流不存在: {workflow_run_id}")

        from tasks.zsxq_workflow import run_zsxq_workflow

        async_result = apply_async_after_db_release(
            run_zsxq_workflow,
            args=[
                str(workflow_run_id),
                {
                    key: value
                    for key, value in {
                        "cookie": cookie,
                        "credential_id": credential_id,
                    }.items()
                    if value
                },
            ],
            queue="zsxq_workflow",
            retry=False,
        )
        stage_results = (
            deepcopy(supplied_payload.get("stage_results") or {})
            if supplied_payload is not None
            else self._stage_results(workflow)
        )
        workflow_result = dict(stage_results.get("workflow") or {})
        queued_at = self._now_iso()
        workflow_result.update({
            "status": "queued",
            "celery_task_id": async_result.id,
            "queued_at": queued_at,
        })
        stage_results["workflow"] = workflow_result
        if supplied_payload is not None:
            updated_at = datetime.utcnow()
            updated = (
                ZsxqWorkflowRun.query
                .filter(ZsxqWorkflowRun.id == str(workflow_run_id))
                .update(
                    {
                        ZsxqWorkflowRun.stage_results: stage_results,
                        ZsxqWorkflowRun.updated_at: updated_at,
                    },
                    synchronize_session=False,
                )
            )
            if updated != 1:
                db.session.rollback()
                raise ValueError(f"工作流不存在: {workflow_run_id}")
            db.session.commit()
            supplied_payload["stage_results"] = stage_results
            supplied_payload["updated_at"] = updated_at.isoformat()
            committed_payload = supplied_payload
        else:
            workflow.stage_results = stage_results
            committed_payload = self._commit_and_payload(workflow)
        return {
            "workflow_run_id": committed_payload["id"],
            "celery_task_id": async_result.id,
            "workflow": committed_payload,
        }

    def start_workflow(self, workflow_run_id, runtime=None):
        self.ensure_schema()
        runtime = runtime if isinstance(runtime, dict) else {}
        workflow = self._load_workflow_run(workflow_run_id, include_runtime=True)
        if not workflow:
            raise ValueError(f"工作流不存在: {workflow_run_id}")
        if workflow.status in TERMINAL_WORKFLOW_STATUSES:
            return workflow.to_dict()

        snapshot = self._snapshot(workflow)
        sync_config = snapshot.get("sync") or {}
        cookie = runtime.get("cookie")
        credential_id = (
            runtime.get("credential_id")
            or sync_config.get("credential_id")
            or (snapshot.get("system_sync") or {}).get("credential_id")
        )
        if not cookie:
            from services.platform_credential_service import resolve_zsxq_cookie

            credential = resolve_zsxq_cookie(account_id=credential_id, purpose="zsxq.sync", required=True)
            credential_id = credential.get("credential_id") or credential_id

        workflow.status = "running"
        workflow.current_stage = "sync"
        workflow.started_at = workflow.started_at or datetime.utcnow()

        try:
            run = zsxq_sync_service.create_sync_run(
                group_id=sync_config.get("group_id"),
                group_name=sync_config.get("group_name"),
                tabs=sync_config.get("tabs"),
                mode=sync_config.get("mode") or "workflow",
                initial_result={
                    "queued": True,
                    "system_sync": snapshot.get("system_sync") or {},
                    "manual_attachment_plan": (sync_config.get("manual_attachment_plan") or {}).get("public") or {},
                    "attachment_download_rules": sync_config.get("attachment_download_rules") or {},
                    "workflow": {
                        "workflow_run_id": workflow.id,
                        "batch_id": workflow.batch_id,
                    },
                },
            )
            payload = {
                "cookie": cookie,
                "group_id": sync_config.get("group_id"),
                "group_name": sync_config.get("group_name"),
                "tabs": sync_config.get("tabs"),
                "count": sync_config.get("count", 20),
                "max_pages": sync_config.get("max_pages", 1),
                "stop_on_existing": sync_config.get("stop_on_existing", True),
                "download_attachments": sync_config.get("download_attachments", False),
                "download_pdf_only": False,
                "download_file_types": sync_config.get("download_file_types") or ["all"],
                "download_file_types_by_tab": sync_config.get("download_file_types_by_tab") or {},
                "max_download_files": sync_config.get("max_download_files"),
                "allow_attachment_downloads": sync_config.get("allow_attachment_downloads", False),
                "mode": sync_config.get("mode") or "workflow",
                "trigger": workflow.trigger or "workflow",
                "sync_meta": snapshot.get("system_sync") or {},
                "workflow_run_id": workflow.id,
                "manual_attachment_plan": sync_config.get("manual_attachment_plan"),
                "attachment_download_rules": sync_config.get("attachment_download_rules"),
                "auto_video_dispatch": sync_config.get("auto_video_dispatch"),
                "auto_script_material_dispatch": sync_config.get("auto_script_material_dispatch"),
                "credential_id": credential_id,
                # Workflow owns downstream orchestration. Keep legacy direct
                # sync->clean handoff off for workflow-triggered sync tasks.
                "auto_pdf_clean": False,
                "auto_audio_clean": False,
                "auto_publish_after_clean": {"enabled": False},
            }

            from tasks.zsxq_sync import run_zsxq_sync

            async_result = apply_async_after_db_release(
                run_zsxq_sync,
                args=[run["id"], payload],
                queue="zsxq_sync",
                retry=False,
            )
            workflow.sync_run_id = run["id"]
            stage_results = self._stage_results(workflow)
            stage_results["sync"] = {
                "status": "queued",
                "run_id": run["id"],
                "celery_task_id": async_result.id,
                "queued_at": self._now_iso(),
            }
            workflow.stage_results = stage_results
            self._queue_notification(workflow, "workflow_started")
            return self._commit_and_payload(workflow)
        except Exception as exc:
            db.session.rollback()
            if _is_retryable_database_error(exc):
                logger.warning(
                    "[ZSXQ_WORKFLOW] sync stage queue transient db failure | workflow=%s error=%s",
                    workflow.id,
                    exc,
                )
                raise

            logger.exception("[ZSXQ_WORKFLOW] sync stage queue failed | workflow=%s", workflow.id)
            workflow = self._load_workflow_run(workflow_run_id, include_runtime=True)
            if workflow:
                self._mark_failed(workflow, f"同步阶段入队失败: {exc}", stage="sync")
            db.session.commit()
            raise

    def record_sync_finished(self, workflow_run_id, *, run_id, success, result=None, error=None):
        self.ensure_schema()
        workflow = self._load_workflow_run(workflow_run_id, include_runtime=True)
        if not workflow:
            logger.warning("[ZSXQ_WORKFLOW] sync finish ignored, workflow missing: %s", workflow_run_id)
            return None
        return self._record_sync_finished_loaded(
            workflow,
            run_id=run_id,
            success=success,
            result=result,
            error=error,
            commit=True,
        )

    def record_sync_finished_and_advance(self, workflow_run_id, *, run_id, success, result=None, error=None):
        self.ensure_schema()
        workflow = self._load_workflow_run(workflow_run_id, include_runtime=True)
        if not workflow:
            logger.warning("[ZSXQ_WORKFLOW] sync finish ignored, workflow missing: %s", workflow_run_id)
            return None
        self._record_sync_finished_loaded(
            workflow,
            run_id=run_id,
            success=success,
            result=result,
            error=error,
            commit=False,
        )
        if not success:
            return self._commit_and_payload(workflow)
        return self._advance_workflow_loaded(workflow, reason="sync_finished")

    def _record_sync_finished_loaded(self, workflow, *, run_id, success, result=None, error=None, commit=True):
        stage_results = self._stage_results(workflow)
        sync_result = dict(stage_results.get("sync") or {})
        sync_result.update({
            "status": "completed" if success else "failed",
            "run_id": str(run_id),
            "finished_at": self._now_iso(),
        })
        if result:
            sync_result["summary"] = self._sync_summary(result)
        if error:
            sync_result["error"] = str(error)
        stage_results["sync"] = sync_result
        workflow.stage_results = stage_results

        if success:
            workflow.status = "running"
            workflow.current_stage = "sync"
            self._queue_notification(workflow, "sync_completed")
        else:
            workflow.status = "failed"
            workflow.current_stage = "failed"
            workflow.error_message = str(error or "同步阶段失败")
            workflow.completed_at = datetime.utcnow()
            self._queue_notification(workflow, "sync_failed")
            self._queue_notification(workflow, "workflow_failed")
        if commit:
            return self._commit_and_payload(workflow)
        return workflow

    def advance_workflow(self, workflow_run_id, reason=None, event_payload=None):
        self.ensure_schema()
        workflow = self._load_workflow_run(workflow_run_id, include_runtime=True)
        if not workflow:
            raise ValueError(f"工作流不存在: {workflow_run_id}")
        self._merge_stage_event(workflow, event_payload)
        return self._advance_workflow_loaded(workflow, reason=reason)

    def _merge_stage_event(self, workflow, event_payload):
        event = event_payload if isinstance(event_payload, dict) else {}
        if event.get("stage") != "clean" or not event.get("task_id"):
            return
        source_refs = event.get("source_refs") if isinstance(event.get("source_refs"), list) else []
        if not source_refs:
            return
        stage_results = self._stage_results(workflow)
        clean_result = dict(stage_results.get("clean") or {})
        task_id = str(event["task_id"])
        tasks = [dict(item) for item in (clean_result.get("tasks") or []) if isinstance(item, dict)]
        task_by_id = {str(item.get("task_id")): item for item in tasks if item.get("task_id")}
        current = task_by_id.get(task_id, {"task_id": task_id})
        current["source_refs"] = deepcopy(source_refs[:100])
        task_by_id[task_id] = current
        ordered_ids = [str(item.get("task_id")) for item in tasks if item.get("task_id")]
        if task_id not in ordered_ids:
            ordered_ids.append(task_id)
        clean_result["tasks"] = [task_by_id[item_id] for item_id in ordered_ids]
        stage_results["clean"] = clean_result
        workflow.stage_results = stage_results

    def _advance_workflow_loaded(self, workflow, reason=None):
        if workflow.status in TERMINAL_WORKFLOW_STATUSES:
            return workflow.to_dict()

        workflow.status = "running"
        workflow.started_at = workflow.started_at or datetime.utcnow()

        if not self._sync_completed(workflow):
            return self._commit_and_payload(workflow)

        clean_status = self._clean_stage_status(workflow)
        if clean_status in {None, "created"}:
            self._submit_clean_stage(workflow)
            db.session.flush()
            clean_status = self._clean_stage_status(workflow)
            if clean_status in {"running", "queued"}:
                payload = workflow.to_dict()
                db.session.commit()
                return payload

        if clean_status in {"running", "queued"}:
            self._refresh_clean_stage(workflow)
            db.session.flush()
            clean_status = self._clean_stage_status(workflow)
            if clean_status in {"running", "queued"}:
                payload = workflow.to_dict()
                db.session.commit()
                return payload

        if clean_status == "failed":
            self._mark_failed(workflow, "清洗阶段失败", stage="clean")
            return self._commit_and_payload(workflow)

        publish_status = self._publish_stage_status(workflow)
        if publish_status in {None, "created"}:
            self._submit_publish_stage(workflow)
            db.session.flush()
            publish_status = self._publish_stage_status(workflow)
            if publish_status in {"running", "queued"}:
                payload = workflow.to_dict()
                db.session.commit()
                return payload

        if publish_status in {"running", "queued"}:
            self._refresh_publish_stage(workflow)
            db.session.flush()
            publish_status = self._publish_stage_status(workflow)
            if publish_status in {"running", "queued"}:
                payload = workflow.to_dict()
                db.session.commit()
                return payload

        if publish_status == "failed":
            self._mark_failed(workflow, "发布阶段失败", stage="publish")
        elif publish_status == "partial" or clean_status == "partial":
            self._mark_partial(workflow, "工作流部分成功")
        else:
            self._mark_completed(workflow)
        return self._commit_and_payload(workflow)

    def list_runs(self, group_id=None, limit=20):
        self.ensure_schema()
        query = (
            ZsxqWorkflowRun.query
            .options(load_only(*ZSXQ_WORKFLOW_STATE_COLUMNS))
            .order_by(ZsxqWorkflowRun.created_at.desc())
        )
        if group_id:
            query = query.filter(ZsxqWorkflowRun.group_id == str(group_id))
        return [item.to_dict() for item in query.limit(min(max(int(limit or 20), 1), 100)).all()]

    def get_run(self, workflow_run_id):
        self.ensure_schema()
        workflow = self._load_workflow_run(workflow_run_id, include_runtime=True)
        return workflow.to_dict() if workflow else None

    def mark_failed_by_id(self, workflow_run_id, message, stage="workflow"):
        self.ensure_schema()
        workflow = self._load_workflow_run(workflow_run_id, include_runtime=True)
        if not workflow:
            return None
        self._mark_failed(workflow, message, stage=stage)
        return self._commit_and_payload(workflow)

    def pending_workflow(self, group_id=None, *, recover_stale=True):
        self.ensure_schema()
        if recover_stale:
            self.recover_stale_initial_workflows(group_id=group_id)
            self.recover_stale_running_sync_workflows(group_id=group_id)
            self.recover_stale_running_clean_workflows(group_id=group_id)
        query = (
            ZsxqWorkflowRun.query
            .options(load_only(*ZSXQ_WORKFLOW_STATE_COLUMNS))
            .filter(ZsxqWorkflowRun.status.in_(("queued", "running")))
        )
        if group_id:
            query = query.filter(ZsxqWorkflowRun.group_id == str(group_id))
        workflow = query.order_by(ZsxqWorkflowRun.created_at.desc()).first()
        return workflow.to_dict() if workflow else None

    def recover_stale_initial_workflows(self, group_id=None, stale_minutes=None):
        self.ensure_schema()
        stale_minutes = _workflow_queued_stale_minutes(stale_minutes)
        cutoff = datetime.utcnow() - timedelta(minutes=stale_minutes)
        query = ZsxqWorkflowRun.query.filter(
            ZsxqWorkflowRun.status == "queued",
            ZsxqWorkflowRun.current_stage == "init",
            or_(
                ZsxqWorkflowRun.updated_at < cutoff,
                ZsxqWorkflowRun.created_at < cutoff,
            ),
        )
        if group_id:
            query = query.filter(ZsxqWorkflowRun.group_id == str(group_id))

        workflows = (
            query
            .options(load_only(*ZSXQ_WORKFLOW_RUNTIME_COLUMNS))
            .order_by(ZsxqWorkflowRun.created_at.asc())
            .limit(20)
            .all()
        )
        if not workflows:
            return []

        recovered = []
        for workflow in workflows:
            message = (
                f"工作流启动任务长时间停留在 queued/init（超过 {stale_minutes} 分钟），"
                "已自动标记失败，避免阻塞后续调度。"
            )
            self._mark_failed(workflow, message, stage="workflow")
            recovered.append(workflow.to_dict())
        db.session.commit()
        logger.warning(
            "[ZSXQ_WORKFLOW] stale queued workflows marked failed | group_id=%s count=%s ids=%s",
            group_id or "-",
            len(recovered),
            [item.get("id") for item in recovered],
        )
        return recovered

    def recover_stale_running_sync_workflows(self, group_id=None, stale_minutes=None):
        self.ensure_schema()
        stale_minutes = _workflow_sync_stale_minutes(stale_minutes)
        cutoff = datetime.utcnow() - timedelta(minutes=stale_minutes)
        query = ZsxqWorkflowRun.query.filter(
            ZsxqWorkflowRun.status == "running",
            ZsxqWorkflowRun.current_stage == "sync",
            ZsxqWorkflowRun.sync_run_id.isnot(None),
            or_(
                ZsxqWorkflowRun.updated_at < cutoff,
                ZsxqWorkflowRun.created_at < cutoff,
            ),
        )
        if group_id:
            query = query.filter(ZsxqWorkflowRun.group_id == str(group_id))

        workflows = (
            query
            .options(load_only(*ZSXQ_WORKFLOW_RUNTIME_COLUMNS))
            .order_by(ZsxqWorkflowRun.updated_at.asc(), ZsxqWorkflowRun.created_at.asc())
            .limit(20)
            .all()
        )
        if not workflows:
            return []

        now = datetime.utcnow()
        recovered = []
        completed_workflow_ids = []
        for workflow in workflows:
            sync_run = self._load_sync_run_state(workflow.sync_run_id)
            if sync_run and sync_run.status == "completed":
                completed_workflow_ids.append(str(workflow.id))
                continue

            if sync_run and sync_run.status == "failed":
                message = sync_run.error_message or "同步阶段失败"
                self._mark_failed(workflow, message, stage="sync")
                recovered.append({
                    "id": workflow.id,
                    "workflow_id": workflow.id,
                    "sync_run_id": workflow.sync_run_id,
                    "message": message,
                    "action": "terminal_failed_reconciled",
                })
                continue

            heartbeat_run = self._load_sync_run_heartbeat(workflow.sync_run_id) if sync_run else None
            sync_result = (
                heartbeat_run.result
                if heartbeat_run and isinstance(heartbeat_run.result, dict)
                else {}
            )
            partial = sync_result.get("partial") if isinstance(sync_result.get("partial"), dict) else {}
            heartbeat = _parse_iso_datetime(
                sync_result.get("heartbeat_at")
                or sync_result.get("progress_at")
                or partial.get("heartbeat_at")
                or partial.get("progress_at")
            )
            sync_last_touched = heartbeat or workflow.updated_at or workflow.created_at or now
            if sync_last_touched >= cutoff:
                continue

            message = (
                f"同步任务长时间停留在 running/sync（超过 {stale_minutes} 分钟），"
                "通常是 worker 重启或进程被杀导致 Celery 任务丢失；已自动标记失败，避免阻塞后续调度。"
            )
            if sync_run:
                result = dict(sync_result)
                result["error"] = message
                result["stale_recovered"] = True
                result["stale_recovered_at"] = self._now_iso()
                sync_run.status = "failed"
                sync_run.error_message = message
                sync_run.completed_at = now
                sync_run.result = result
                flag_modified(sync_run, "result")

            self._mark_failed(workflow, message, stage="sync")
            recovered.append({
                "id": workflow.id,
                "workflow_id": workflow.id,
                "sync_run_id": workflow.sync_run_id,
                "message": message,
                "action": "stale_running_failed",
            })

        if recovered:
            db.session.commit()
            logger.warning(
                "[ZSXQ_WORKFLOW] stale running sync workflows reconciled as failed | "
                "group_id=%s count=%s ids=%s",
                group_id or "-",
                len(recovered),
                [item.get("workflow_id") for item in recovered],
            )

        advanced = []
        for workflow_id in completed_workflow_ids:
            payload = self.advance_workflow(workflow_id, reason="stale_sync_terminal_recovery")
            advanced.append({
                "id": workflow_id,
                "workflow_id": workflow_id,
                "sync_run_id": payload.get("sync_run_id"),
                "message": "同步任务已完成，工作流已自动继续推进。",
                "action": "terminal_completed_advanced",
            })
        if advanced:
            logger.warning(
                "[ZSXQ_WORKFLOW] stale workflows with completed sync advanced | "
                "group_id=%s count=%s ids=%s",
                group_id or "-",
                len(advanced),
                [item.get("workflow_id") for item in advanced],
            )
        return recovered + advanced

    def recover_stale_running_clean_workflows(self, group_id=None, stale_minutes=None):
        self.ensure_schema()
        stale_minutes = _workflow_clean_stale_minutes(stale_minutes)
        cutoff = datetime.utcnow() - timedelta(minutes=stale_minutes)
        query = ZsxqWorkflowRun.query.filter(
            ZsxqWorkflowRun.status == "running",
            ZsxqWorkflowRun.current_stage == "clean",
        )
        if group_id:
            query = query.filter(ZsxqWorkflowRun.group_id == str(group_id))

        workflows = (
            query
            .options(load_only(*ZSXQ_WORKFLOW_RUNTIME_COLUMNS))
            .order_by(ZsxqWorkflowRun.updated_at.asc(), ZsxqWorkflowRun.created_at.asc())
            .limit(20)
            .all()
        )
        recovered = []
        workflow_ids_to_advance = []
        now = datetime.utcnow()
        for workflow in workflows:
            task_ids = [str(item) for item in (workflow.clean_task_ids or []) if item]
            if not task_ids:
                continue
            tasks = (
                PublicationTask.query
                .options(load_only(*WORKFLOW_TASK_STATE_COLUMNS))
                .filter(PublicationTask.id.in_(task_ids))
                .all()
            )
            stale_tasks = []
            for task in tasks:
                if not task or task.status in TERMINAL_TASK_STATUSES:
                    continue
                last_touched = task.updated_at or task.created_at or now
                if last_touched < cutoff:
                    stale_tasks.append(task)

            if not stale_tasks:
                continue

            for task in stale_tasks:
                detail_task = (
                    PublicationTask.query
                    .options(load_only(PublicationTask.id, PublicationTask.result))
                    .filter(PublicationTask.id == str(task.id))
                    .first()
                ) or task
                message = (
                    f"清洗子任务长时间停留在 {task.status}/{task.current_step or '-'} "
                    f"（超过 {stale_minutes} 分钟），已自动标记失败，避免阻塞后续星球同步。"
                )
                result = dict(detail_task.result or {})
                result["error"] = message
                result["stale_recovered"] = True
                result["stale_recovered_at"] = self._now_iso()
                detail_task.status = "failed"
                detail_task.current_step = "failed"
                detail_task.error_message = message
                detail_task.completed_at = now
                detail_task.result = result
                flag_modified(detail_task, "result")
                recovered.append({
                    "workflow_id": workflow.id,
                    "task_id": task.id,
                    "feature": task.feature,
                    "message": message,
                })
            workflow_ids_to_advance.append(workflow.id)

        if not recovered:
            return []

        db.session.commit()
        advanced = []
        for workflow_id in workflow_ids_to_advance:
            try:
                advanced.append(self.advance_workflow(workflow_id, reason="stale_clean_task_recovered"))
            except Exception:
                logger.exception("[ZSXQ_WORKFLOW] failed to advance stale clean workflow | workflow=%s", workflow_id)

        logger.warning(
            "[ZSXQ_WORKFLOW] stale running clean tasks marked failed | group_id=%s count=%s workflow_ids=%s advanced=%s",
            group_id or "-",
            len(recovered),
            workflow_ids_to_advance,
            len(advanced),
        )
        return recovered

    def _submit_clean_stage(self, workflow):
        snapshot = self._snapshot(workflow)
        clean_config = snapshot.get("clean") or {}
        stage_results = self._stage_results(workflow)
        if not self._clean_enabled(snapshot):
            stage_results["clean"] = {
                "status": "skipped",
                "reason": "disabled",
                "finished_at": self._now_iso(),
            }
            workflow.stage_results = stage_results
            workflow.current_stage = "clean"
            self._queue_notification(workflow, "clean_skipped")
            return

        run_id = workflow.sync_run_id
        # PDF and audio belong to one transition. Reuse one materialized sync
        # result and keep it current after each child submission writes back.
        run_context = {}
        tasks = []
        task_ids = []
        session = db.session()
        previous_expire_on_commit = session.expire_on_commit
        session.expire_on_commit = False
        try:
            if clean_config.get("auto_pdf_clean", True):
                result = self._submit_one_clean_task(
                    workflow=workflow,
                    run_id=run_id,
                    file_type="pdf",
                    cleaner=self._normalize_pdf_cleaner(clean_config.get("pdf_cleaner")),
                    run_context=run_context,
                )
                tasks.append(result)
                if result.get("queued") and result.get("task_id"):
                    task_ids.append(result["task_id"])

            if clean_config.get("auto_audio_clean", True):
                result = self._submit_one_clean_task(
                    workflow=workflow,
                    run_id=run_id,
                    file_type="audio",
                    cleaner=clean_config.get("audio_cleaner") or {},
                    run_context=run_context,
                )
                tasks.append(result)
                if result.get("queued") and result.get("task_id"):
                    task_ids.append(result["task_id"])
        finally:
            session.expire_on_commit = previous_expire_on_commit

        workflow.clean_task_ids = task_ids
        submit_failed = any(item.get("status") == "failed" or item.get("reason") == "submit_failed" for item in tasks)
        clean_status = "running" if task_ids else ("failed" if submit_failed else "skipped")
        stage_results["clean"] = {
            "status": clean_status,
            "tasks": tasks,
            "task_ids": task_ids,
            "queued_at": self._now_iso(),
            "reason": None if task_ids else ("submit_failed" if submit_failed else "no_clean_tasks"),
        }
        workflow.stage_results = stage_results
        workflow.current_stage = "clean"
        if clean_status == "running":
            self._queue_notification(workflow, "clean_submitted")
        elif clean_status == "failed":
            self._queue_notification(workflow, "clean_failed")
        else:
            self._queue_notification(workflow, "clean_skipped")

    def _submit_one_clean_task(self, *, workflow, run_id, file_type, cleaner, run_context=None):
        try:
            if file_type == "pdf":
                result = zsxq_sync_service.submit_pdf_clean_for_run(
                    run_id,
                    cleaner=cleaner,
                    trigger="zsxq_workflow",
                    auto_publish={"enabled": False},
                    workflow_run_id=workflow.id,
                    run_context=run_context,
                )
            else:
                result = zsxq_sync_service.submit_audio_clean_for_run(
                    run_id,
                    cleaner=cleaner,
                    trigger="zsxq_workflow",
                    auto_publish={"enabled": False},
                    workflow_run_id=workflow.id,
                    run_context=run_context,
                )
            result = dict(result or {})
            result["file_type"] = file_type
            return result
        except Exception as exc:
            if is_retryable_db_error(exc):
                # A disconnected PostgreSQL connection leaves SQLAlchemy's
                # scoped session in a failed transaction. Do not swallow that
                # error and attempt the next cleaner with the poisoned session;
                # reset it and let the Celery task retry the whole transition.
                db.session.rollback()
                raise
            logger.exception(
                "[ZSXQ_WORKFLOW] clean submit failed | workflow=%s type=%s",
                workflow.id,
                file_type,
            )
            return {
                "queued": False,
                "file_type": file_type,
                "status": "failed",
                "reason": "submit_failed",
                "error": str(exc),
            }

    def _refresh_clean_stage(self, workflow):
        task_ids = [str(item) for item in (workflow.clean_task_ids or []) if item]
        stage_results = self._stage_results(workflow)
        clean_result = dict(stage_results.get("clean") or {})
        if not task_ids:
            clean_result["status"] = clean_result.get("status") or "skipped"
            clean_result["finished_at"] = clean_result.get("finished_at") or self._now_iso()
            stage_results["clean"] = clean_result
            workflow.stage_results = stage_results
            return

        tasks = (
            PublicationTask.query
            .with_entities(*WORKFLOW_TASK_STATE_COLUMNS)
            .filter(PublicationTask.id.in_(task_ids))
            .all()
        )
        task_map = {task.id: task for task in tasks}
        statuses = [task_map.get(task_id).status if task_map.get(task_id) else "failed" for task_id in task_ids]
        previous_summaries = {
            str(item.get("task_id")): item
            for item in (clean_result.get("tasks") or [])
            if isinstance(item, dict) and item.get("task_id")
        }
        summaries = [
            self._merge_task_summary(
                previous_summaries.get(task_id),
                self._task_summary(
                    task_map.get(task_id),
                    task_id=task_id,
                    include_payload=False,
                ),
            )
            for task_id in task_ids
        ]
        terminal_status = None
        if any(status not in TERMINAL_TASK_STATUSES for status in statuses):
            clean_result.update({
                "status": "running",
                "tasks": summaries,
                "task_ids": task_ids,
            })
        else:
            failed = [item for item in summaries if item.get("status") == "failed"]
            completed = [item for item in summaries if item.get("status") == "completed"]
            clean_result.update({
                "status": "failed" if failed and not completed else ("partial" if failed else "completed"),
                "tasks": summaries,
                "task_ids": task_ids,
                "finished_at": self._now_iso(),
            })
            terminal_status = clean_result["status"]
        stage_results["clean"] = clean_result
        workflow.stage_results = stage_results
        if terminal_status:
            self._queue_notification(workflow, f"clean_{terminal_status}")

    def _submit_publish_stage(self, workflow):
        snapshot = self._snapshot(workflow)
        stage_results = self._stage_results(workflow)
        if not self._publish_enabled(snapshot):
            stage_results["publish"] = {
                "status": "skipped",
                "reason": "disabled",
                "finished_at": self._now_iso(),
            }
            workflow.stage_results = stage_results
            workflow.current_stage = "publish"
            self._queue_notification(workflow, "publish_skipped")
            return

        clean_task_ids = [str(item) for item in (workflow.clean_task_ids or []) if item]
        publish_options = deepcopy(snapshot.get("publish") or {})
        publish_options["enabled"] = True
        publish_options.setdefault("trigger", "zsxq_workflow_clean_complete")
        publish_task_ids = []
        publish_tasks = []

        clean_summaries = {
            str(item.get("task_id")): item
            for item in ((stage_results.get("clean") or {}).get("tasks") or [])
            if isinstance(item, dict) and item.get("task_id")
        }
        for clean_task_id in clean_task_ids:
            clean_task = self._clean_publish_context(
                clean_task_id,
                clean_summaries.get(clean_task_id),
            )
            if clean_task.get("status") != "completed":
                continue
            output_dir = clean_task.get("local_output_dir")
            file_type = clean_task.get("file_type")
            if not output_dir or not file_type:
                continue
            try:
                publish_task = submit_publish_clean_output_task(
                    title=f"知识星球工作流发布 - {workflow.id[:8]}",
                    file_type=file_type,
                    output_dir=output_dir,
                    options=publish_options,
                    source_task_id=clean_task_id,
                    trigger="zsxq_workflow_clean_complete",
                    metadata={
                        "workflow_run_id": workflow.id,
                        "batch_id": workflow.batch_id,
                        "clean_task_id": clean_task_id,
                        "sync_run_id": workflow.sync_run_id,
                        "source_refs": deepcopy(clean_task.get("source_refs") or []),
                    },
                )
                publish_task_ids.append(publish_task["id"])
                publish_tasks.append({
                    "queued": True,
                    "task_id": publish_task["id"],
                    "celery_task_id": publish_task.get("celery_task_id"),
                    "file_type": file_type,
                    "output_dir": output_dir,
                })
            except Exception as exc:
                logger.exception("[ZSXQ_WORKFLOW] publish submit failed | workflow=%s clean_task=%s", workflow.id, clean_task_id)
                publish_tasks.append({
                    "queued": False,
                    "status": "failed",
                    "reason": "submit_failed",
                    "file_type": file_type,
                    "output_dir": output_dir,
                    "error": str(exc),
                })

        workflow.publish_task_ids = publish_task_ids
        submit_failed = any(item.get("status") == "failed" or item.get("reason") == "submit_failed" for item in publish_tasks)
        publish_status = "running" if publish_task_ids else ("failed" if submit_failed else "skipped")
        stage_results["publish"] = {
            "status": publish_status,
            "tasks": publish_tasks,
            "task_ids": publish_task_ids,
            "queued_at": self._now_iso(),
            "reason": None if publish_task_ids else ("submit_failed" if submit_failed else "no_publish_tasks"),
        }
        workflow.stage_results = stage_results
        workflow.current_stage = "publish"
        if publish_status == "running":
            self._queue_notification(workflow, "publish_submitted")
        elif publish_status == "failed":
            self._queue_notification(workflow, "publish_failed")
        else:
            self._queue_notification(workflow, "publish_skipped")

    def _clean_publish_context(self, clean_task_id, summary):
        summary = dict(summary or {})
        if summary.get("status") != "completed":
            return summary
        if summary.get("local_output_dir") and summary.get("file_type"):
            return summary

        # Compatibility for workflows that reached the clean stage before the
        # compact summary was introduced. New transitions never take this path.
        row = (
            db.session.query(
                PublicationTask.id,
                PublicationTask.status,
                PublicationTask.feature,
                PublicationTask.content_clean_output_dir,
            )
            .filter(PublicationTask.id == str(clean_task_id))
            .one_or_none()
        )
        if not row:
            return summary
        feature = row.feature
        output_dir = row.content_clean_output_dir
        if not output_dir:
            output_dir = self._load_publication_task_result(clean_task_id).get("local_output_dir")
        return {
            **summary,
            "task_id": row.id,
            "status": row.status,
            "file_type": {
                "content_pdf_clean": "pdf",
                "content_audio_clean": "audio",
            }.get(feature),
            "local_output_dir": output_dir,
        }

    def _refresh_publish_stage(self, workflow):
        task_ids = [str(item) for item in (workflow.publish_task_ids or []) if item]
        stage_results = self._stage_results(workflow)
        publish_result = dict(stage_results.get("publish") or {})
        if not task_ids:
            publish_result["status"] = publish_result.get("status") or "skipped"
            publish_result["finished_at"] = publish_result.get("finished_at") or self._now_iso()
            stage_results["publish"] = publish_result
            workflow.stage_results = stage_results
            return

        tasks = [
            SimpleNamespace(**dict(row._mapping))
            for row in (
                PublicationTask.query
                .with_entities(*WORKFLOW_TASK_STATE_COLUMNS)
                .filter(PublicationTask.id.in_(task_ids))
                .all()
            )
        ]
        task_map = {task.id: task for task in tasks}
        previous_summaries = {
            str(item.get("task_id")): item
            for item in (publish_result.get("tasks") or [])
            if isinstance(item, dict) and item.get("task_id")
        }
        summaries = []
        for task_id in task_ids:
            task = task_map.get(task_id)
            summary = self._task_summary(
                task,
                task_id=task_id,
                include_payload=False,
            )
            publish_runtime = summary.get("publish_runtime") if isinstance(summary, dict) else None

            # Runtime verification can finish before the worker records its
            # terminal state. Only then load the one candidate's payload for
            # recovery; ordinary polling remains a state-only query.
            if task and task.status != "completed" and self._publish_runtime_can_recover(publish_runtime):
                recovery_task = self._load_publish_recovery_task(task_id)
                recovered = self._recover_publish_task_from_runtime(
                    recovery_task,
                    publish_runtime=publish_runtime,
                )
                if recovered:
                    task.status = "completed"
                    task.current_step = "done"
                    task.progress = 100
                    task.error_message = None
                    task.completed_at = recovered.get("completed_at")
                    summary = self._task_summary(
                        task,
                        task_id=task_id,
                        include_payload=False,
                        result_override=recovered.get("result") or {},
                        publish_runtime_override=publish_runtime,
                    )
            elif task and task.status == "completed" and not self._publish_runtime_has_summary(publish_runtime):
                summary = self._task_summary(
                    task,
                    task_id=task_id,
                    include_payload=False,
                    result_override=self._load_publication_task_result(task_id),
                    publish_runtime_override=publish_runtime,
                )

            summaries.append(self._merge_task_summary(previous_summaries.get(task_id), summary))
        statuses = [item.get("status") for item in summaries]
        terminal_status = None
        if any(status not in TERMINAL_TASK_STATUSES for status in statuses):
            publish_result.update({
                "status": "running",
                "tasks": summaries,
                "task_ids": task_ids,
            })
        else:
            failed = [item for item in summaries if item.get("status") == "failed"]
            completed = [item for item in summaries if item.get("status") == "completed"]
            publish_result.update({
                "status": "failed" if failed and not completed else ("partial" if failed else "completed"),
                "tasks": summaries,
                "task_ids": task_ids,
                "finished_at": self._now_iso(),
            })
            terminal_status = publish_result["status"]
        stage_results["publish"] = publish_result
        workflow.stage_results = stage_results
        if terminal_status:
            self._queue_notification(workflow, f"publish_{terminal_status}")

    def _build_manual_download_snapshot(self, *, config, trigger):
        config = config if isinstance(config, dict) else {}
        group_id = str(config.get("group_id") or "").strip()
        if not group_id:
            raise ValueError("group_id 不能为空")
        if group_id != SYSTEM_SYNC_GROUP_ID:
            raise ValueError(
                f"人工计划仅支持“{SYSTEM_SYNC_GROUP_NAME}”"
                f"（Group ID: {SYSTEM_SYNC_GROUP_ID}）"
            )
        credential_id = config.get("credential_id") or config.get("_credential_id") or None

        raw_items = config.get("attachments") or config.get("items") or []
        tabs = self._manual_tabs_from_config(config, raw_items)
        if not tabs:
            raise ValueError("人工计划至少需要一个 tab")

        options = config.get("options") if isinstance(config.get("options"), dict) else {}
        max_download_files = (
            options.get("max_download_files")
            or config.get("max_download_files")
        )
        manual_plan = zsxq_sync_service.normalize_manual_attachment_plan(
            {
                "enabled": True,
                "items": raw_items,
                "max_download_files": max_download_files,
            },
            group_id=group_id,
            tabs=tabs,
        )
        if not manual_plan.get("items"):
            raise ValueError("人工计划至少需要一个附件")

        selected_types = {
            str(item.get("file_type") or "").lower()
            for item in manual_plan.get("items") or []
            if item.get("file_type")
        }
        download_file_types = sorted(item for item in selected_types if item in {"pdf", "mp3"})
        if not download_file_types:
            download_file_types = ["all"]
        has_pdf = "pdf" in selected_types or download_file_types == ["all"]
        has_audio = bool(selected_types.intersection({"mp3", "m4a", "wav", "aac", "flac", "ogg"}))
        auto_pdf_clean = bool(options.get("auto_pdf_clean", has_pdf))
        auto_audio_clean = bool(options.get("auto_audio_clean", has_audio))
        publish_config = self._normalize_auto_publish(options.get("auto_publish_after_clean"))
        batch_id = str(config.get("batch_id") or f"manual-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}")
        stamp = datetime.utcnow().strftime("%H%M%S")
        public_plan = zsxq_sync_service.public_manual_attachment_plan(manual_plan)
        system_sync = {
            "batch_id": batch_id,
            "attempt_id": batch_id,
            "round": None,
            "max_rounds": None,
            "continuous": False,
            "trigger": trigger,
            "queued_at": self._now_iso(),
            "manual_download_plan": True,
            "manual_attachment_plan": public_plan,
            "selected_count": public_plan.get("selected_count"),
            "max_download_files": public_plan.get("limit"),
            "download_attachments_requested": True,
            "download_attachments": True,
            "allow_attachment_downloads": True,
            "auto_pdf_clean": auto_pdf_clean,
            "auto_audio_clean": auto_audio_clean,
            "auto_publish_after_clean": publish_config,
            "jobs": [
                {
                    "key": f"manual_{index + 1}",
                    "tab_label": item.get("tab_label"),
                    "tab_id": item.get("tab_id"),
                    "file_type": item.get("file_type") or "file",
                    "topic_id": item.get("topic_id"),
                    "name": item.get("name"),
                }
                for index, item in enumerate(manual_plan.get("items") or [])
            ],
        }
        return {
            "version": 1,
            "stages": {
                "sync": True,
                "clean": auto_pdf_clean or auto_audio_clean,
                "publish": bool(publish_config.get("enabled")),
            },
            "system_sync": system_sync,
            "sync": {
                "group_id": group_id,
                "group_name": config.get("group_name") or "",
                "credential_id": credential_id,
                "tabs": tabs,
                "count": 1,
                "max_pages": 1,
                "stop_on_existing": False,
                "download_attachments": True,
                "allow_attachment_downloads": True,
                "download_file_types": download_file_types,
                "download_file_types_by_tab": {},
                "max_download_files": public_plan.get("limit"),
                "mode": f"manual_plan_{stamp}",
                "manual_attachment_plan": {
                    "enabled": True,
                    "max_download_files": public_plan.get("limit"),
                    "items": manual_plan.get("items") or [],
                    "public": public_plan,
                },
            },
            "clean": {
                "auto_pdf_clean": auto_pdf_clean,
                "pdf_cleaner": self._normalize_pdf_cleaner(options.get("pdf_cleaner") or config.get("pdf_cleaner")),
                "auto_audio_clean": auto_audio_clean,
                "audio_cleaner": options.get("audio_cleaner") or config.get("audio_cleaner") or {},
            },
            "publish": publish_config,
        }

    def _manual_tabs_from_config(self, config, raw_items):
        candidates = []
        if isinstance(config.get("tab"), dict):
            candidates.append(config["tab"])
        if isinstance(config.get("tabs"), list):
            candidates.extend(item for item in config["tabs"] if isinstance(item, dict))
        for item in raw_items or []:
            if not isinstance(item, dict):
                continue
            tab_id = str(item.get("tab_id") or item.get("tabId") or "").strip()
            if not tab_id:
                continue
            candidates.append({
                "id": tab_id,
                "label": item.get("tab_label") or item.get("tabLabel") or tab_id,
                "scope": "all",
                "hashtagId": item.get("hashtag_id") or item.get("hashtagId"),
            })

        tabs = []
        seen = set()
        for item in candidates:
            tab_id = str(item.get("id") or item.get("tab_id") or "").strip()
            if not tab_id or tab_id in seen:
                continue
            seen.add(tab_id)
            tabs.append({
                "id": tab_id,
                "label": str(item.get("label") or item.get("tab_label") or tab_id).strip(),
                "scope": item.get("scope") or "all",
                "hashtagId": item.get("hashtagId") or item.get("hashtag_id"),
                "extraParams": item.get("extraParams") if isinstance(item.get("extraParams"), dict) else {},
            })
        return tabs

    def _build_system_snapshot(self, *, config, trigger, batch_id, batch_round, max_rounds, max_download_files, jobs):
        jobs = jobs or []
        tabs = [job["tab"] for job in jobs]
        download_file_types_by_tab = {
            job["tab"]["id"]: [job["file_type"]]
            for job in jobs
        }
        stamp = datetime.utcnow().strftime("%H%M%S")
        mode = f"sys{batch_round}_workflow_{stamp}"
        auto_pdf_clean = bool(config.get("auto_pdf_clean", False))
        auto_audio_clean = bool(config.get("auto_audio_clean", False))
        publish_config = self._normalize_auto_publish(config.get("auto_publish_after_clean"))
        requested_download_attachments = bool(config.get("download_attachments", False))
        allow_attachment_downloads = bool(config.get("allow_attachment_downloads", False))
        download_attachments = requested_download_attachments and allow_attachment_downloads
        attachment_download_rules = self._normalize_attachment_download_rules(config.get("attachment_download_rules"))
        auto_video_dispatch = self._normalize_auto_video_dispatch(config.get("auto_video_dispatch"))
        auto_script_material_dispatch = self._normalize_auto_script_material_dispatch(
            config.get("auto_script_material_dispatch")
        )
        skip_existing_downloads = bool(config.get("skip_existing_downloads", True))
        count = _topic_count(config.get("count", 12))
        max_pages = _effective_topic_max_pages(count, config.get("max_pages", 1))
        system_sync = {
            "batch_id": batch_id,
            "attempt_id": batch_id,
            "round": batch_round,
            "max_rounds": max_rounds,
            "continuous": max_rounds is None,
            "trigger": trigger,
            "queued_at": self._now_iso(),
            "credential_id": config.get("credential_id") or None,
            "count": count,
            "max_pages": max_pages,
            "max_download_files": max_download_files,
            "download_attachments_requested": requested_download_attachments,
            "download_attachments": download_attachments,
            "allow_attachment_downloads": allow_attachment_downloads,
            "attachment_download_rules": attachment_download_rules,
            "auto_video_dispatch": auto_video_dispatch,
            "auto_script_material_dispatch": auto_script_material_dispatch,
            "skip_existing_downloads": skip_existing_downloads,
            "auto_pdf_clean": auto_pdf_clean,
            "auto_audio_clean": auto_audio_clean,
            "auto_publish_after_clean": publish_config,
            "jobs": [
                {
                    "key": job["key"],
                    "tab_label": job["tab"].get("label"),
                    "tab_id": job["tab"].get("id"),
                    "file_type": job["file_type"],
                }
                for job in jobs
            ],
        }
        return {
            "version": 1,
            "stages": {
                "sync": True,
                "clean": auto_pdf_clean or auto_audio_clean,
                "publish": bool(publish_config.get("enabled")),
            },
            "system_sync": system_sync,
            "sync": {
                "group_id": str(config.get("group_id") or ""),
                "group_name": config.get("group_name"),
                "credential_id": config.get("credential_id") or None,
                "tabs": tabs,
                "count": count,
                "max_pages": max_pages,
                "stop_on_existing": config.get("stop_on_existing", True),
                "download_attachments": download_attachments,
                "allow_attachment_downloads": allow_attachment_downloads,
                "download_file_types": ["all"],
                "download_file_types_by_tab": download_file_types_by_tab,
                "max_download_files": max_download_files,
                "attachment_download_rules": attachment_download_rules,
                "auto_video_dispatch": auto_video_dispatch,
                "auto_script_material_dispatch": auto_script_material_dispatch,
                "skip_existing_downloads": skip_existing_downloads,
                "mode": mode,
            },
            "clean": {
                "auto_pdf_clean": auto_pdf_clean,
                "pdf_cleaner": self._normalize_pdf_cleaner(config.get("pdf_cleaner")),
                "auto_audio_clean": auto_audio_clean,
                "audio_cleaner": config.get("audio_cleaner") or {},
            },
            "publish": publish_config,
        }

    def _sync_completed(self, workflow):
        stage_results = self._stage_results(workflow)
        sync_result = dict(stage_results.get("sync") or {})
        if sync_result.get("status") == "completed":
            return True
        if sync_result.get("status") == "failed":
            self._queue_notification(workflow, "sync_failed")
            self._mark_failed(workflow, sync_result.get("error") or "同步阶段失败", stage="sync")
            return False
        if workflow.sync_run_id:
            run = self._load_sync_run_state(workflow.sync_run_id)
            if run and run.status == "completed":
                detail_run = self._load_sync_run_state(run.id, include_result=True) or run
                detail_result = detail_run.result if detail_run is not run and isinstance(detail_run.result, dict) else {}
                sync_result.update({
                    "status": "completed",
                    "run_id": run.id,
                    "finished_at": self._now_iso(),
                    "summary": self._sync_summary({
                        "run": self._sync_run_payload(detail_run),
                        "summary": detail_result,
                    }),
                })
                stage_results["sync"] = sync_result
                workflow.stage_results = stage_results
                self._queue_notification(workflow, "sync_completed")
                return True
            if run and run.status == "failed":
                sync_result.update({
                    "status": "failed",
                    "run_id": run.id,
                    "finished_at": self._now_iso(),
                    "error": run.error_message,
                })
                stage_results["sync"] = sync_result
                workflow.stage_results = stage_results
                self._queue_notification(workflow, "sync_failed")
                self._mark_failed(workflow, run.error_message or "同步阶段失败", stage="sync")
        return False

    def _load_workflow_run(self, workflow_run_id, *, include_runtime=True):
        if not workflow_run_id:
            return None
        columns = ZSXQ_WORKFLOW_RUNTIME_COLUMNS if include_runtime else ZSXQ_WORKFLOW_STATE_COLUMNS
        return (
            ZsxqWorkflowRun.query
            .options(load_only(*columns))
            .filter(ZsxqWorkflowRun.id == str(workflow_run_id))
            .first()
        )

    def _load_sync_run_state(self, run_id, *, include_result=False):
        if not run_id:
            return None
        columns = ZSXQ_SYNC_RUN_RESULT_COLUMNS if include_result else ZSXQ_SYNC_RUN_STATE_COLUMNS
        return (
            ZsxqSyncRun.query
            .options(load_only(*columns))
            .filter(ZsxqSyncRun.id == str(run_id))
            .first()
        )

    def _load_sync_run_heartbeat(self, run_id):
        if not run_id:
            return None
        return (
            ZsxqSyncRun.query
            .options(load_only(*ZSXQ_SYNC_RUN_HEARTBEAT_COLUMNS))
            .filter(ZsxqSyncRun.id == str(run_id))
            .first()
        )

    def _sync_run_payload(self, run):
        if not run:
            return {}
        return {
            "id": run.id,
            "group_id": getattr(run, "group_id", None),
            "group_name": getattr(run, "group_name", None),
            "tab_id": getattr(run, "tab_id", None),
            "tab_label": getattr(run, "tab_label", None),
            "mode": getattr(run, "mode", None),
            "status": run.status,
            "requested_tabs": getattr(run, "requested_tabs", None) or [],
            "system_batch_id": getattr(run, "system_batch_id", None),
            "system_attempt_id": getattr(run, "system_attempt_id", None),
            "system_round": getattr(run, "system_round", None),
            "system_max_rounds": getattr(run, "system_max_rounds", None),
            "system_trigger": getattr(run, "system_trigger", None),
            "system_queued_at": getattr(run, "system_queued_at", None),
            "system_max_download_files": getattr(run, "system_max_download_files", None),
            "topics_fetched": getattr(run, "topics_fetched", None) or 0,
            "topics_new": getattr(run, "topics_new", None) or 0,
            "topics_updated": getattr(run, "topics_updated", None) or 0,
            "topics_existing": getattr(run, "topics_existing", None) or 0,
            "attachments_seen": getattr(run, "attachments_seen", None) or 0,
            "attachments_downloaded": getattr(run, "attachments_downloaded", None) or 0,
            "attachments_failed": getattr(run, "attachments_failed", None) or 0,
            "attachments_skipped": getattr(run, "attachments_skipped", None) or 0,
            "download_limit_reached": bool(getattr(run, "download_limit_reached", False)),
            "download_ledger_complete": bool(
                getattr(run, "download_ledger_complete", False)
            ),
            "error_message": run.error_message,
            "started_at": run.started_at.isoformat() if getattr(run, "started_at", None) else None,
            "completed_at": run.completed_at.isoformat() if run.completed_at else None,
            "created_at": run.created_at.isoformat() if getattr(run, "created_at", None) else None,
            "updated_at": run.updated_at.isoformat() if run.updated_at else None,
        }

    def _clean_stage_status(self, workflow):
        return (self._stage_results(workflow).get("clean") or {}).get("status")

    def _publish_stage_status(self, workflow):
        return (self._stage_results(workflow).get("publish") or {}).get("status")

    def _clean_enabled(self, snapshot):
        clean_config = snapshot.get("clean") or {}
        return bool((snapshot.get("stages") or {}).get("clean")) and (
            bool(clean_config.get("auto_pdf_clean", True))
            or bool(clean_config.get("auto_audio_clean", True))
        )

    def _publish_enabled(self, snapshot):
        return bool((snapshot.get("stages") or {}).get("publish")) and bool(
            (snapshot.get("publish") or {}).get("enabled")
        )

    def _clean_task_file_type(self, task):
        params = task.params or {}
        feature = params.get("feature")
        if feature == "content_pdf_clean":
            return "pdf"
        if feature == "content_audio_clean":
            return "audio"
        pipeline = params.get("content_pipeline") or {}
        if pipeline.get("pdf_clean"):
            return "pdf"
        if pipeline.get("audio_clean"):
            return "audio"
        return None

    def _task_summary(
        self,
        task,
        task_id=None,
        *,
        include_payload=True,
        result_override=None,
        publish_runtime_override=None,
    ):
        if not task:
            return {
                "task_id": task_id,
                "status": "failed",
                "error": "任务记录不存在",
            }
        params = (task.params or {}) if include_payload else {}
        feature = task.feature or params.get("feature")
        file_type = {
            "content_pdf_clean": "pdf",
            "content_audio_clean": "audio",
        }.get(feature)
        if not file_type and include_payload:
            file_type = self._clean_task_file_type(task)
        if feature == "zsxq_clean_output_publish":
            file_type = (params.get("zsxq_publish") or {}).get("file_type")
        result = (task.result or {}) if include_payload else (result_override or {})
        if not include_payload:
            result = {
                **result,
                "local_output_dir": result.get("local_output_dir") or getattr(task, "content_clean_output_dir", None),
                "files_count": (
                    result.get("files_count")
                    if result.get("files_count") is not None
                    else getattr(task, "content_clean_files_count", None)
                ),
                "success_count": (
                    result.get("success_count")
                    if result.get("success_count") is not None
                    else getattr(task, "content_clean_success_count", None)
                ),
                "failed_count": (
                    result.get("failed_count")
                    if result.get("failed_count") is not None
                    else getattr(task, "content_clean_failed_count", None)
                ),
                "message": result.get("message") or getattr(task, "content_clean_message", None),
            }
        publish_runtime = None
        if feature == "zsxq_clean_output_publish":
            if publish_runtime_override is not None:
                publish_runtime = publish_runtime_override
            else:
                try:
                    publish_runtime = summarize_publish_runs_for_request(f"zsxq_publish:{task.id}")
                except Exception:
                    logger.exception("[ZSXQ_WORKFLOW] failed to summarize publish runtime runs | task=%s", task.id)
                    publish_runtime = None
        runtime_summary = (publish_runtime or {}).get("summary") if isinstance(publish_runtime, dict) else None
        result_summary = result.get("summary") if isinstance(result.get("summary"), dict) else None
        summary = runtime_summary if runtime_summary and any(runtime_summary.values()) else result_summary
        return {
            "task_id": task.id,
            "celery_task_id": task.celery_task_id,
            "status": task.status,
            "current_step": task.current_step,
            "progress": task.progress,
            "feature": feature,
            "file_type": file_type,
            "error_message": task.error_message,
            "local_output_dir": result.get("local_output_dir"),
            "files_count": result.get("files_count"),
            "success_count": result.get("success_count"),
            "failed_count": result.get("failed_count"),
            "selected_count": result.get("selected_count") if result.get("selected_count") is not None else (summary or {}).get("selected_count"),
            "uploaded_count": result.get("uploaded_count") if result.get("uploaded_count") is not None else (summary or {}).get("uploaded_count"),
            "submitted_count": result.get("submitted_count") if result.get("submitted_count") is not None else (summary or {}).get("submitted_count"),
            "verified_count": result.get("verified_count") if result.get("verified_count") is not None else (summary or {}).get("verified_count"),
            "pending_verify_count": result.get("pending_verify_count") if result.get("pending_verify_count") is not None else (summary or {}).get("pending_verify_count"),
            "topic_count": result.get("topic_count") if result.get("topic_count") is not None else (summary or {}).get("topic_count"),
            "verified_topic_count": result.get("verified_topic_count") if result.get("verified_topic_count") is not None else (summary or {}).get("verified_topic_count"),
            "pending_verify_topic_count": result.get("pending_verify_topic_count") if result.get("pending_verify_topic_count") is not None else (summary or {}).get("pending_verify_topic_count"),
            "summary": summary,
            "publish_runtime": publish_runtime,
            "message": result.get("message"),
            "updated_at": task.updated_at.isoformat() if task.updated_at else None,
            "completed_at": task.completed_at.isoformat() if task.completed_at else None,
        }

    def _merge_task_summary(self, previous, current):
        previous = previous if isinstance(previous, dict) else {}
        current = current if isinstance(current, dict) else {}
        merged = {**previous, **current}
        for key, value in previous.items():
            if merged.get(key) is None:
                merged[key] = value
        if "error_message" in current:
            merged["error_message"] = current.get("error_message")
        return merged

    def _publish_runtime_has_summary(self, publish_runtime):
        summary = (publish_runtime or {}).get("summary") if isinstance(publish_runtime, dict) else None
        return bool(isinstance(summary, dict) and any(summary.values()))

    def _publish_runtime_can_recover(self, publish_runtime):
        summary = (publish_runtime or {}).get("summary") if isinstance(publish_runtime, dict) else {}
        if not isinstance(summary, dict):
            return False
        return (
            _safe_int(summary.get("verified_count")) > 0
            and _safe_int(summary.get("failed_count")) == 0
            and _safe_int(summary.get("pending_verify_count")) == 0
        )

    def _load_publish_recovery_task(self, task_id):
        return (
            PublicationTask.query.options(load_only(
                PublicationTask.id,
                PublicationTask.status,
                PublicationTask.current_step,
                PublicationTask.progress,
                PublicationTask.error_message,
                PublicationTask.feature,
                PublicationTask.source_task_id,
                PublicationTask.params,
                PublicationTask.result,
                PublicationTask.completed_at,
                PublicationTask.updated_at,
            ))
            .filter(PublicationTask.id == str(task_id))
            .one_or_none()
        )

    def _load_publication_task_result(self, task_id):
        row = (
            db.session.query(PublicationTask.result)
            .filter(PublicationTask.id == str(task_id))
            .one_or_none()
        )
        result = row[0] if row else None
        return result if isinstance(result, dict) else {}

    def _recover_publish_task_from_runtime(self, task, publish_runtime=None):
        if not task or task.status == "completed":
            return False
        params = task.params if isinstance(task.params, dict) else {}
        if (task.feature or params.get("feature")) != "zsxq_clean_output_publish":
            return False

        if publish_runtime is None:
            try:
                publish_runtime = summarize_publish_runs_for_request(f"zsxq_publish:{task.id}")
            except Exception:
                logger.exception("[ZSXQ_WORKFLOW] failed to recover publish task from runtime | task=%s", task.id)
                return False

        summary = (publish_runtime or {}).get("summary") if isinstance(publish_runtime, dict) else {}
        if not self._publish_runtime_completed(task, summary):
            return False

        now = datetime.utcnow()
        recovered = {
            "queued": True,
            "enabled": True,
            "status": "completed",
            "reason": "recovered_after_worker_shutdown",
            "message": "清洗输出目录已无待发布文件。",
            "summary": summary,
            "rounds": (publish_runtime or {}).get("runs") or [],
            "batches": (publish_runtime or {}).get("batches") or [],
            "topic_ids": (publish_runtime or {}).get("topic_ids") or [],
            "recovered_from_runtime": True,
            "recovered_at": now.isoformat() + "Z",
        }
        task.status = "completed"
        task.current_step = "done"
        task.progress = 100
        task.error_message = None
        task.completed_at = task.completed_at or now
        recovered_result = {
            **(task.result or {}),
            **recovered,
            "publish_result": recovered,
        }
        task.result = recovered_result
        flag_modified(task, "result")
        try:
            zsxq_sync_service.update_attachment_publish_status_from_publish_task(task)
        except Exception:
            logger.exception("[ZSXQ_WORKFLOW] failed to backfill recovered publish task attachments | task=%s", task.id)
        return {
            "result": deepcopy(recovered_result),
            "completed_at": task.completed_at,
        }

    def _publish_runtime_completed(self, task, summary):
        summary = summary if isinstance(summary, dict) else {}
        submitted = _safe_int(summary.get("submitted_count"))
        verified = _safe_int(summary.get("verified_count"))
        failed = _safe_int(summary.get("failed_count"))
        pending = _safe_int(summary.get("pending_verify_count"))
        if verified <= 0 or failed > 0 or pending > 0:
            return False

        expected = self._publish_expected_source_count(task)
        if expected > 0:
            return verified >= expected
        return submitted > 0 and verified >= submitted

    def _publish_expected_source_count(self, task):
        params = task.params if isinstance(task.params, dict) else {}
        publish_params = params.get("zsxq_publish") if isinstance(params.get("zsxq_publish"), dict) else {}
        source_task_id = str(publish_params.get("source_task_id") or "").strip()
        if not source_task_id:
            return 0

        clean_task = (
            PublicationTask.query.options(load_only(*WORKFLOW_CLEAN_RESULT_COLUMNS))
            .filter(PublicationTask.id == source_task_id)
            .first()
        )
        if not clean_task:
            return 0
        projected = _safe_int(
            clean_task.content_clean_success_count
            or clean_task.content_clean_files_count
        )
        if projected:
            return projected
        result = self._load_publication_task_result(source_task_id)
        for key in ("cleaned_files", "files"):
            files = result.get(key)
            if isinstance(files, list):
                return len(files)
        return _safe_int(result.get("success_count") or result.get("files_count"))

    def _sync_summary(self, result):
        summary = result.get("summary") if isinstance(result, dict) else {}
        if not isinstance(summary, dict):
            summary = {}
        run = result.get("run") if isinstance(result, dict) else {}
        compact = {
            "run_id": (run or {}).get("id"),
            "target_topics_per_tab": summary.get("target_topics_per_tab"),
            "planned_topics_total": summary.get("planned_topics_total"),
            "max_pages": summary.get("max_pages"),
            "planned_pages_total": summary.get("planned_pages_total"),
            "pages_fetched": summary.get("pages_fetched"),
            "topics_fetched": summary.get("topics_fetched"),
            "topics_new": summary.get("topics_new"),
            "topics_updated": summary.get("topics_updated"),
            "topics_existing": summary.get("topics_existing"),
            "attachments_seen": summary.get("attachments_seen"),
            "attachments_downloaded": summary.get("attachments_downloaded"),
            "attachments_failed": summary.get("attachments_failed"),
            "attachments_skipped": summary.get("attachments_skipped"),
            "attachments_below_threshold": summary.get("attachments_below_threshold"),
            "attachments_missing_download_count": summary.get("attachments_missing_download_count"),
            "attachments_non_target_skipped": summary.get("attachments_non_target_skipped"),
            "attachments_already_downloaded_skipped": summary.get("attachments_already_downloaded_skipped"),
            "attachments_reused_local": summary.get("attachments_reused_local"),
            "attachments_reused_existing": summary.get("attachments_reused_existing"),
            "attachments_limited_skipped": summary.get("attachments_limited_skipped"),
            "max_download_files": summary.get("max_download_files"),
            "download_limit_reached": summary.get("download_limit_reached"),
            "auto_video_dispatch": self._compact_dispatch_summary(summary.get("auto_video_dispatch")),
            "auto_script_material_dispatch": self._compact_dispatch_summary(
                summary.get("auto_script_material_dispatch")
            ),
        }
        tabs = summary.get("tabs") if isinstance(summary.get("tabs"), list) else []
        compact["tabs"] = [self._sync_tab_summary(tab) for tab in tabs[:20] if isinstance(tab, dict)]
        return compact

    def _compact_dispatch_summary(self, value):
        if not isinstance(value, dict):
            return value
        compact = {
            key: value.get(key)
            for key in (
                "enabled",
                "status",
                "reason",
                "source",
                "run_id",
                "workflow_run_id",
                "group_id",
                "group_name",
                "attachments_checked",
                "attachments_loaded",
                "missing_attachment_rows",
                "skipped_non_target",
                "queued_count",
                "deferred_count",
                "dispatched_count",
                "failed_count",
                "eligible_count",
            )
            if value.get(key) is not None
        }
        task_ids = [str(item) for item in (value.get("queued_task_ids") or []) if item]
        if task_ids:
            compact["queued_task_ids"] = task_ids[:20]
        errors = [item for item in (value.get("errors") or []) if isinstance(item, dict)]
        if errors:
            compact["errors"] = [
                {
                    key: item.get(key)
                    for key in ("kind", "attachment_id", "task_id", "message")
                    if item.get(key) is not None
                }
                for item in errors[:5]
            ]
        for bucket_name in ("pdf", "mp3"):
            bucket = value.get(bucket_name)
            if not isinstance(bucket, dict):
                continue
            compact[bucket_name] = {
                key: bucket.get(key)
                for key in (
                    "threshold",
                    "checked",
                    "eligible",
                    "queued",
                    "deferred",
                    "failed",
                    "skipped_existing",
                    "skipped_below_threshold",
                    "skipped_missing_download_count",
                    "skipped_not_downloaded",
                    "skipped_missing_file",
                )
                if bucket.get(key) is not None
            }
        return compact

    def _sync_tab_summary(self, tab):
        return {
            "tab_id": tab.get("tab_id"),
            "tab_label": tab.get("tab_label"),
            "target_topics": tab.get("target_topics"),
            "planned_pages": tab.get("planned_pages") or tab.get("max_pages"),
            "pages": tab.get("pages"),
            "topics_fetched": tab.get("topics_fetched"),
            "topics_new": tab.get("topics_new"),
            "topics_updated": tab.get("topics_updated"),
            "topics_existing": tab.get("topics_existing"),
            "attachments_seen": tab.get("attachments_seen"),
            "attachments_downloaded": tab.get("attachments_downloaded"),
            "attachments_failed": tab.get("attachments_failed"),
            "attachments_skipped": tab.get("attachments_skipped"),
            "attachments_below_threshold": tab.get("attachments_below_threshold"),
            "attachments_missing_download_count": tab.get("attachments_missing_download_count"),
            "attachments_non_target_skipped": tab.get("attachments_non_target_skipped"),
            "attachments_already_downloaded_skipped": tab.get("attachments_already_downloaded_skipped"),
            "attachments_limited_skipped": tab.get("attachments_limited_skipped"),
            "download_limit_reached": tab.get("download_limit_reached"),
            "stopped_by_existing": tab.get("stopped_by_existing"),
            "stopped_by_download_limit": tab.get("stopped_by_download_limit"),
        }

    def _mark_failed(self, workflow, message, stage=None):
        workflow.status = "failed"
        workflow.current_stage = "failed"
        workflow.error_message = str(message or "工作流失败")[:1000]
        workflow.completed_at = datetime.utcnow()
        if stage:
            stage_results = self._stage_results(workflow)
            stage_result = dict(stage_results.get(stage) or {})
            stage_result["status"] = "failed"
            stage_result["error"] = workflow.error_message
            stage_result["finished_at"] = self._now_iso()
            stage_results[stage] = stage_result
            workflow.stage_results = stage_results
        self._queue_notification(workflow, "workflow_failed")

    def _mark_partial(self, workflow, message):
        workflow.status = "partial"
        workflow.current_stage = "done"
        workflow.error_message = str(message or "工作流部分成功")[:1000]
        workflow.completed_at = datetime.utcnow()
        self._queue_notification(workflow, "workflow_partial")

    def _mark_completed(self, workflow):
        workflow.status = "completed"
        workflow.current_stage = "done"
        workflow.error_message = None
        workflow.completed_at = datetime.utcnow()
        self._queue_notification(workflow, "workflow_completed")

    def _queue_notification(self, workflow, event_type):
        workflow_id = str(workflow.id)
        try:
            stage_results = self._stage_results(workflow)
            notifications = dict(stage_results.get("notifications") or {})
            if notifications.get(event_type, {}).get("queued"):
                return

            workflow_payload = workflow.to_dict()
            db.session.commit()
            result = workflow_notification_service.enqueue_workflow_event(event_type, workflow_payload)
            if not result.get("queued"):
                return

            notifications[event_type] = {
                "queued": True,
                "event_type": event_type,
                "celery_task_id": result.get("celery_task_id"),
                "queued_at": self._now_iso(),
            }
            stage_results["notifications"] = notifications
            workflow.stage_results = stage_results
            db.session.commit()
        except Exception:
            db.session.rollback()
            logger.exception("[ZSXQ_WORKFLOW] notification queue failed | workflow=%s event=%s", workflow_id, event_type)

    def _snapshot(self, workflow):
        return workflow.config_snapshot if isinstance(workflow.config_snapshot, dict) else {}

    def _stage_results(self, workflow):
        return dict(workflow.stage_results) if isinstance(workflow.stage_results, dict) else {}

    def _create_workflow_with_db_retry(self, *, workflow_factory, existing_lookup, label):
        attempts = _queue_db_retries() + 1
        last_exc = None
        for attempt in range(1, attempts + 1):
            try:
                self.ensure_schema()
                existing = existing_lookup()
                if existing:
                    return existing.to_dict()
                workflow = workflow_factory()
                db.session.add(workflow)
                return self._commit_and_payload(workflow)
            except Exception as exc:
                db.session.rollback()
                last_exc = exc
                if not _is_retryable_database_error(exc) or attempt >= attempts:
                    raise

                self._schema_checked_at = None
                release_db_session_quietly()
                delay = _queue_db_retry_delay_seconds(attempt)
                logger.warning(
                    "[ZSXQ_WORKFLOW] create workflow transient db failure, retrying | label=%s attempt=%s/%s delay=%ss error=%s",
                    label,
                    attempt,
                    attempts - 1,
                    delay,
                    exc,
                )
                time.sleep(delay)

        raise last_exc

    def _find_existing_system_workflow(self, *, batch_id, group_id, batch_round):
        if not batch_id or not group_id:
            return None
        existing = (
            db.session.query(ZsxqWorkflowRun.id)
            .filter(
                ZsxqWorkflowRun.batch_id == str(batch_id),
                ZsxqWorkflowRun.group_id == str(group_id),
                ZsxqWorkflowRun.round == batch_round,
            )
            .order_by(ZsxqWorkflowRun.created_at.desc())
            .first()
        )
        if not existing:
            return None
        return ZsxqWorkflowRun.query.filter(ZsxqWorkflowRun.id == existing.id).one()

    def _find_existing_manual_workflow(self, *, batch_id, group_id, trigger):
        if not batch_id or not group_id:
            return None
        existing = (
            db.session.query(ZsxqWorkflowRun.id)
            .filter(
                ZsxqWorkflowRun.batch_id == str(batch_id),
                ZsxqWorkflowRun.group_id == str(group_id),
                ZsxqWorkflowRun.trigger == trigger,
            )
            .order_by(ZsxqWorkflowRun.created_at.desc())
            .first()
        )
        if not existing:
            return None
        return ZsxqWorkflowRun.query.filter(ZsxqWorkflowRun.id == existing.id).one()

    def _commit_and_payload(self, workflow):
        db.session.flush()
        payload = workflow.to_dict()
        db.session.commit()
        return payload

    def _normalize_auto_publish(self, value):
        data = deepcopy(value) if isinstance(value, dict) else {}
        data["enabled"] = bool(data.get("enabled", False))
        return data

    def _normalize_pdf_cleaner(self, value):
        data = deepcopy(value) if isinstance(value, dict) else {}
        engine = str(data.get("engine") or os.getenv("PDF_CLEANER_ENGINE", "local")).strip().lower()
        data["engine"] = engine or "local"
        return data

    def _normalize_attachment_download_rules(self, value):
        if not isinstance(value, dict):
            return {"enabled": False, "rules": []}

        rules = []
        for raw_rule in value.get("rules") or []:
            if not isinstance(raw_rule, dict):
                continue
            file_type = str(raw_rule.get("file_type") or "").strip().lower().lstrip(".")
            if file_type not in {"pdf", "mp3", "all"}:
                continue
            try:
                threshold = int(raw_rule.get("min_download_count", raw_rule.get("threshold")))
            except (TypeError, ValueError):
                continue
            rules.append({
                "key": str(raw_rule.get("key") or f"{raw_rule.get('tab_id') or '*'}:{file_type}")[:80],
                "tab_id": str(raw_rule.get("tab_id") or "").strip(),
                "tab_label": str(raw_rule.get("tab_label") or "").strip(),
                "file_type": file_type,
                "min_download_count": min(max(threshold, 0), 1_000_000),
            })
        return {
            "enabled": bool(value.get("enabled", False) and rules),
            "rules": rules,
        }

    def _normalize_auto_video_dispatch(self, value):
        value = value if isinstance(value, dict) else {}
        return {
            "enabled": bool(value.get("enabled", False)),
            "pdf_min_download_count": _bounded_int(
                value.get("pdf_min_download_count"),
                default=DEFAULT_AUTO_VIDEO_PDF_THRESHOLD,
                lower=0,
                upper=1_000_000,
            ),
            "mp3_min_download_count": _bounded_int(
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
            "enabled": bool(value.get("enabled", False)),
            "pdf_min_download_count": _bounded_int(
                value.get("pdf_min_download_count"),
                default=DEFAULT_AUTO_SCRIPT_MATERIAL_PDF_THRESHOLD,
                lower=0,
                upper=1_000_000,
            ),
            "visual_scheme_keys": visual_scheme_keys,
            "wait_for_completion": False,
        }

    def _now_iso(self):
        return datetime.utcnow().isoformat() + "Z"


def _is_retryable_database_error(exc):
    return is_retryable_db_error(exc)


def _workflow_queued_stale_minutes(value=None):
    try:
        minutes = int(value if value is not None else os.environ.get(QUEUED_STALE_MINUTES_ENV_NAME))
    except (TypeError, ValueError):
        minutes = DEFAULT_QUEUED_STALE_MINUTES
    return max(5, min(24 * 60, minutes))


def _workflow_clean_stale_minutes(value=None):
    try:
        minutes = int(value if value is not None else os.environ.get(CLEAN_STALE_MINUTES_ENV_NAME))
    except (TypeError, ValueError):
        minutes = DEFAULT_CLEAN_STALE_MINUTES
    return max(30, min(7 * 24 * 60, minutes))


def _workflow_sync_stale_minutes(value=None):
    try:
        minutes = int(value if value is not None else os.environ.get(SYNC_STALE_MINUTES_ENV_NAME))
    except (TypeError, ValueError):
        minutes = DEFAULT_SYNC_STALE_MINUTES
    return max(30, min(24 * 60, minutes))


def _parse_iso_datetime(value):
    if not value:
        return None
    try:
        text_value = str(value).strip()
        if text_value.endswith("Z"):
            text_value = text_value[:-1]
        parsed = datetime.fromisoformat(text_value)
        return parsed.replace(tzinfo=None)
    except (TypeError, ValueError):
        return None


def _schema_check_ttl_seconds():
    try:
        seconds = int(os.environ.get(SCHEMA_CHECK_TTL_SECONDS_ENV_NAME, DEFAULT_SCHEMA_CHECK_TTL_SECONDS))
    except (TypeError, ValueError):
        seconds = DEFAULT_SCHEMA_CHECK_TTL_SECONDS
    return max(0, min(3600, seconds))


def _queue_db_retries():
    try:
        retries = int(os.environ.get(QUEUE_DB_RETRIES_ENV_NAME, DEFAULT_QUEUE_DB_RETRIES))
    except (TypeError, ValueError):
        retries = DEFAULT_QUEUE_DB_RETRIES
    return max(0, min(10, retries))


def _queue_db_retry_delay_seconds(attempt):
    try:
        delay = int(os.environ.get(QUEUE_DB_RETRY_DELAY_SECONDS_ENV_NAME, DEFAULT_QUEUE_DB_RETRY_DELAY_SECONDS))
    except (TypeError, ValueError):
        delay = DEFAULT_QUEUE_DB_RETRY_DELAY_SECONDS
    return min(max(delay, 1) * max(int(attempt or 1), 1), 300)


def _topic_count(value):
    try:
        return min(max(int(value or 12), 1), 50)
    except (TypeError, ValueError):
        return 12


def _safe_int(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _bounded_int(value, *, default, lower, upper):
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = int(default)
    return min(max(number, int(lower)), int(upper))


def _effective_topic_max_pages(target_count, requested_max_pages):
    try:
        requested = min(max(int(requested_max_pages or 1), 1), 20)
    except (TypeError, ValueError):
        requested = 1
    target_pages = max(1, (target_count + TOPIC_PAGE_SIZE_HINT - 1) // TOPIC_PAGE_SIZE_HINT)
    return min(max(requested, target_pages), 20)


zsxq_workflow_service = ZsxqWorkflowService()
