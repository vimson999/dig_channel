import logging
import os
import uuid
from copy import deepcopy
from datetime import datetime, timedelta

from sqlalchemy import inspect, or_, text
from sqlalchemy.exc import DBAPIError, OperationalError
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
from services.zsxq_sync_service import zsxq_sync_service


logger = logging.getLogger(__name__)

TERMINAL_WORKFLOW_STATUSES = {"completed", "failed", "partial", "skipped"}
TERMINAL_TASK_STATUSES = {"completed", "failed"}
QUEUED_STALE_MINUTES_ENV_NAME = "ZSXQ_WORKFLOW_QUEUED_STALE_MINUTES"
DEFAULT_QUEUED_STALE_MINUTES = 20
CLEAN_STALE_MINUTES_ENV_NAME = "ZSXQ_WORKFLOW_CLEAN_STALE_MINUTES"
DEFAULT_CLEAN_STALE_MINUTES = 360
SCHEMA_CHECK_TTL_SECONDS_ENV_NAME = "ZSXQ_WORKFLOW_SCHEMA_CHECK_TTL_SECONDS"
DEFAULT_SCHEMA_CHECK_TTL_SECONDS = 300
DEFAULT_AUTO_VIDEO_PDF_THRESHOLD = 35
DEFAULT_AUTO_VIDEO_MP3_THRESHOLD = 25
TOPIC_PAGE_SIZE_HINT = 20


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
        inspector = inspect(db.session.connection())
        existing_tables = self._table_names(inspector)
        if ZsxqWorkflowRun.__tablename__ not in existing_tables:
            raise ValueError(
                "知识星球工作流表尚未初始化，请先在远端 PostgreSQL 执行 "
                "docs/sql/zsxq_workflow_runs.sql；缺失表：zsxq_workflow_runs"
            )
        privilege_sql = text(
            "SELECT has_table_privilege("
            "current_user, 'public.zsxq_workflow_runs', 'SELECT, INSERT, UPDATE, DELETE'"
            ")"
        )
        has_privilege = db.session.execute(privilege_sql).scalar()
        if not has_privilege:
            raise ValueError(
                "当前数据库账号没有 public.zsxq_workflow_runs 的读写权限。请用表 owner 或管理员执行："
                "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.zsxq_workflow_runs TO cappa_rw;"
            )
        self._schema_checked_at = now

    def _table_names(self, inspector):
        try:
            return set(inspector.get_table_names(schema="public"))
        except Exception:
            return set(inspector.get_table_names())

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
        self.ensure_schema()
        snapshot = self._build_system_snapshot(
            config=config,
            trigger=trigger,
            batch_id=batch_id,
            batch_round=batch_round,
            max_rounds=max_rounds,
            max_download_files=max_download_files,
            jobs=jobs,
        )
        workflow = ZsxqWorkflowRun(
            batch_id=batch_id,
            group_id=str(config.get("group_id") or ""),
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
        )
        db.session.add(workflow)
        return self._commit_and_payload(workflow)

    def create_manual_download_workflow(self, *, config, trigger):
        self.ensure_schema()
        snapshot = self._build_manual_download_snapshot(config=config, trigger=trigger)
        system_sync = snapshot.get("system_sync") or {}
        workflow = ZsxqWorkflowRun(
            batch_id=system_sync.get("batch_id"),
            group_id=str((snapshot.get("sync") or {}).get("group_id") or ""),
            group_name=(snapshot.get("sync") or {}).get("group_name"),
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
        )
        db.session.add(workflow)
        return self._commit_and_payload(workflow)

    def enqueue_workflow_task(self, workflow_run_id, *, cookie=None, credential_id=None):
        self.ensure_schema()
        workflow = ZsxqWorkflowRun.query.get(str(workflow_run_id))
        if not workflow:
            raise ValueError(f"工作流不存在: {workflow_run_id}")

        from tasks.zsxq_workflow import run_zsxq_workflow

        async_result = run_zsxq_workflow.apply_async(
            args=[
                workflow.id,
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
        stage_results = self._stage_results(workflow)
        workflow_result = dict(stage_results.get("workflow") or {})
        workflow_result.update({
            "status": "queued",
            "celery_task_id": async_result.id,
            "queued_at": self._now_iso(),
        })
        stage_results["workflow"] = workflow_result
        workflow.stage_results = stage_results
        workflow_payload = self._commit_and_payload(workflow)
        return {
            "workflow_run_id": workflow_payload["id"],
            "celery_task_id": async_result.id,
            "workflow": workflow_payload,
        }

    def start_workflow(self, workflow_run_id, runtime=None):
        self.ensure_schema()
        runtime = runtime if isinstance(runtime, dict) else {}
        workflow = ZsxqWorkflowRun.query.get(str(workflow_run_id))
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
                "credential_id": credential_id,
                # Workflow owns downstream orchestration. Keep legacy direct
                # sync->clean handoff off for workflow-triggered sync tasks.
                "auto_pdf_clean": False,
                "auto_audio_clean": False,
                "auto_publish_after_clean": {"enabled": False},
            }

            from tasks.zsxq_sync import run_zsxq_sync

            async_result = run_zsxq_sync.apply_async(
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
            workflow = ZsxqWorkflowRun.query.get(str(workflow_run_id))
            if workflow:
                self._mark_failed(workflow, f"同步阶段入队失败: {exc}", stage="sync")
            db.session.commit()
            raise

    def record_sync_finished(self, workflow_run_id, *, run_id, success, result=None, error=None):
        self.ensure_schema()
        workflow = ZsxqWorkflowRun.query.get(str(workflow_run_id))
        if not workflow:
            logger.warning("[ZSXQ_WORKFLOW] sync finish ignored, workflow missing: %s", workflow_run_id)
            return None

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
        return self._commit_and_payload(workflow)

    def advance_workflow(self, workflow_run_id, reason=None):
        self.ensure_schema()
        workflow = ZsxqWorkflowRun.query.get(str(workflow_run_id))
        if not workflow:
            raise ValueError(f"工作流不存在: {workflow_run_id}")
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
        query = ZsxqWorkflowRun.query.order_by(ZsxqWorkflowRun.created_at.desc())
        if group_id:
            query = query.filter(ZsxqWorkflowRun.group_id == str(group_id))
        return [item.to_dict() for item in query.limit(min(max(int(limit or 20), 1), 100)).all()]

    def get_run(self, workflow_run_id):
        self.ensure_schema()
        workflow = ZsxqWorkflowRun.query.get(str(workflow_run_id))
        return workflow.to_dict() if workflow else None

    def mark_failed_by_id(self, workflow_run_id, message, stage="workflow"):
        self.ensure_schema()
        workflow = ZsxqWorkflowRun.query.get(str(workflow_run_id))
        if not workflow:
            return None
        self._mark_failed(workflow, message, stage=stage)
        return self._commit_and_payload(workflow)

    def pending_workflow(self, group_id=None, *, recover_stale=True):
        self.ensure_schema()
        if recover_stale:
            self.recover_stale_initial_workflows(group_id=group_id)
            self.recover_stale_running_clean_workflows(group_id=group_id)
        query = ZsxqWorkflowRun.query.filter(ZsxqWorkflowRun.status.in_(("queued", "running")))
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

        workflows = query.order_by(ZsxqWorkflowRun.created_at.asc()).limit(20).all()
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

        workflows = query.order_by(ZsxqWorkflowRun.updated_at.asc(), ZsxqWorkflowRun.created_at.asc()).limit(20).all()
        recovered = []
        workflow_ids_to_advance = []
        now = datetime.utcnow()
        for workflow in workflows:
            task_ids = [str(item) for item in (workflow.clean_task_ids or []) if item]
            if not task_ids:
                continue
            tasks = PublicationTask.query.filter(PublicationTask.id.in_(task_ids)).all()
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
                message = (
                    f"清洗子任务长时间停留在 {task.status}/{task.current_step or '-'} "
                    f"（超过 {stale_minutes} 分钟），已自动标记失败，避免阻塞后续星球同步。"
                )
                result = dict(task.result or {})
                result["error"] = message
                result["stale_recovered"] = True
                result["stale_recovered_at"] = self._now_iso()
                task.status = "failed"
                task.current_step = "failed"
                task.error_message = message
                task.completed_at = now
                task.result = result
                flag_modified(task, "result")
                recovered.append({
                    "workflow_id": workflow.id,
                    "task_id": task.id,
                    "feature": task.feature or (task.params or {}).get("feature"),
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
        tasks = []
        task_ids = []

        if clean_config.get("auto_pdf_clean", True):
            result = self._submit_one_clean_task(
                workflow=workflow,
                run_id=run_id,
                file_type="pdf",
                cleaner=self._normalize_pdf_cleaner(clean_config.get("pdf_cleaner")),
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
            )
            tasks.append(result)
            if result.get("queued") and result.get("task_id"):
                task_ids.append(result["task_id"])

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

    def _submit_one_clean_task(self, *, workflow, run_id, file_type, cleaner):
        try:
            if file_type == "pdf":
                result = zsxq_sync_service.submit_pdf_clean_for_run(
                    run_id,
                    cleaner=cleaner,
                    trigger="zsxq_workflow",
                    auto_publish={"enabled": False},
                    workflow_run_id=workflow.id,
                )
            else:
                result = zsxq_sync_service.submit_audio_clean_for_run(
                    run_id,
                    cleaner=cleaner,
                    trigger="zsxq_workflow",
                    auto_publish={"enabled": False},
                    workflow_run_id=workflow.id,
                )
            result = dict(result or {})
            result["file_type"] = file_type
            return result
        except Exception as exc:
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

        tasks = PublicationTask.query.filter(PublicationTask.id.in_(task_ids)).all()
        for task in tasks:
            self._recover_publish_task_from_runtime(task)
        task_map = {task.id: task for task in tasks}
        summaries = [self._task_summary(task_map.get(task_id), task_id=task_id) for task_id in task_ids]
        statuses = [item.get("status") for item in summaries]
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

        for clean_task_id in clean_task_ids:
            clean_task = PublicationTask.query.get(clean_task_id)
            if not clean_task or clean_task.status != "completed":
                continue
            output_dir = (clean_task.result or {}).get("local_output_dir")
            file_type = self._clean_task_file_type(clean_task)
            if not output_dir or not file_type:
                continue
            try:
                publish_task = submit_publish_clean_output_task(
                    title=f"知识星球工作流发布 - {workflow.id[:8]}",
                    file_type=file_type,
                    output_dir=output_dir,
                    options=publish_options,
                    source_task_id=clean_task.id,
                    trigger="zsxq_workflow_clean_complete",
                    metadata={
                        "workflow_run_id": workflow.id,
                        "batch_id": workflow.batch_id,
                        "clean_task_id": clean_task.id,
                        "sync_run_id": workflow.sync_run_id,
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

        tasks = PublicationTask.query.filter(PublicationTask.id.in_(task_ids)).all()
        task_map = {task.id: task for task in tasks}
        summaries = [self._task_summary(task_map.get(task_id), task_id=task_id) for task_id in task_ids]
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
            run = ZsxqSyncRun.query.get(workflow.sync_run_id)
            if run and run.status == "completed":
                sync_result.update({
                    "status": "completed",
                    "run_id": run.id,
                    "finished_at": self._now_iso(),
                    "summary": self._sync_summary({"run": run.to_dict(), "summary": run.result or {}}),
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

    def _task_summary(self, task, task_id=None):
        if not task:
            return {
                "task_id": task_id,
                "status": "failed",
                "error": "任务记录不存在",
            }
        params = task.params or {}
        feature = params.get("feature")
        file_type = self._clean_task_file_type(task)
        if feature == "zsxq_clean_output_publish":
            file_type = (params.get("zsxq_publish") or {}).get("file_type")
        result = task.result or {}
        publish_runtime = None
        if feature == "zsxq_clean_output_publish":
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

    def _recover_publish_task_from_runtime(self, task):
        if not task or task.status in TERMINAL_TASK_STATUSES:
            return False
        params = task.params if isinstance(task.params, dict) else {}
        if params.get("feature") != "zsxq_clean_output_publish":
            return False

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
        task.result = {
            **(task.result or {}),
            **recovered,
            "publish_result": recovered,
        }
        flag_modified(task, "result")
        try:
            zsxq_sync_service.update_attachment_publish_status_from_publish_task(task)
        except Exception:
            logger.exception("[ZSXQ_WORKFLOW] failed to backfill recovered publish task attachments | task=%s", task.id)
        return True

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

        clean_task = PublicationTask.query.get(source_task_id)
        result = clean_task.result if clean_task and isinstance(clean_task.result, dict) else {}
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
            "auto_video_dispatch": summary.get("auto_video_dispatch"),
        }
        tabs = summary.get("tabs") if isinstance(summary.get("tabs"), list) else []
        compact["tabs"] = [self._sync_tab_summary(tab) for tab in tabs[:20] if isinstance(tab, dict)]
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
        try:
            stage_results = self._stage_results(workflow)
            notifications = dict(stage_results.get("notifications") or {})
            if notifications.get(event_type, {}).get("queued"):
                return

            result = workflow_notification_service.enqueue_workflow_event(event_type, workflow.to_dict())
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
        except Exception:
            logger.exception("[ZSXQ_WORKFLOW] notification queue failed | workflow=%s event=%s", workflow.id, event_type)

    def _snapshot(self, workflow):
        return workflow.config_snapshot if isinstance(workflow.config_snapshot, dict) else {}

    def _stage_results(self, workflow):
        return dict(workflow.stage_results) if isinstance(workflow.stage_results, dict) else {}

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

    def _now_iso(self):
        return datetime.utcnow().isoformat() + "Z"


def _is_retryable_database_error(exc):
    if isinstance(exc, OperationalError):
        return True
    if isinstance(exc, DBAPIError) and getattr(exc, "connection_invalidated", False):
        return True

    text = str(exc).lower()
    retryable_fragments = (
        "timeout expired",
        "connection to server",
        "server closed the connection unexpectedly",
        "could not connect to server",
        "connection refused",
        "terminating connection",
        "connection not open",
    )
    return any(fragment in text for fragment in retryable_fragments)


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


def _schema_check_ttl_seconds():
    try:
        seconds = int(os.environ.get(SCHEMA_CHECK_TTL_SECONDS_ENV_NAME, DEFAULT_SCHEMA_CHECK_TTL_SECONDS))
    except (TypeError, ValueError):
        seconds = DEFAULT_SCHEMA_CHECK_TTL_SECONDS
    return max(0, min(3600, seconds))


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
