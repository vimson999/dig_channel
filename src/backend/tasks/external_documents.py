import logging
import os
import sys
from datetime import datetime, timedelta

from sqlalchemy.orm import load_only
from sqlalchemy.orm.attributes import flag_modified


backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from celery_config import celery_app
from models import db
from models.external_document import ExternalDocument
from models.task import PublicationTask
from services.external_document_service import external_document_service


logger = logging.getLogger(__name__)
QUEUE_NAME = "content_pipeline"
FEATURE_NAME = "external_document_backfill"
TASK_COLUMNS = (
    PublicationTask.id,
    PublicationTask.status,
    PublicationTask.current_step,
    PublicationTask.progress,
    PublicationTask.params,
    PublicationTask.result,
    PublicationTask.error_message,
    PublicationTask.completed_at,
)


def _create_app(register_apis=False):
    from app import create_app

    return create_app(register_apis=False)


def _load_task(task_id):
    return (
        PublicationTask.query.options(load_only(*TASK_COLUMNS))
        .filter(PublicationTask.id == str(task_id))
        .one_or_none()
    )


@celery_app.task(bind=True, name="tasks.external_documents.run_backfill")
def run_external_document_backfill(self, task_id):
    app = _create_app(register_apis=False)
    with app.app_context():
        task = _load_task(task_id)
        if not task:
            logger.error("External document backfill task missing: %s", task_id)
            return False
        if task.status in {"completed", "canceled"}:
            return True

        params = task.params if isinstance(task.params, dict) else {}
        options = params.get("external_document_backfill") if isinstance(params.get("external_document_backfill"), dict) else {}
        providers = {
            str(item).strip().lower()
            for item in (options.get("providers") or ["feishu", "zsxq", "wechat"])
            if str(item).strip().lower() in {"feishu", "zsxq", "wechat"}
        }
        fetch_enabled = bool(options.get("fetch", True))
        summarize_enabled = bool(options.get("summarize", False))
        document_limit = int(options.get("document_limit") or 0)
        credential_id = options.get("credential_id")

        task.status = "processing"
        task.current_step = "scan"
        task.progress = 1
        task.result = {"scan": None, "processed": 0, "succeeded": 0, "failed": 0, "items": []}
        db.session.commit()
        db.session.remove()

        try:
            scan_result = external_document_service.scan_existing_topics(
                group_id=options.get("group_id"),
                limit=options.get("scan_limit"),
            ) if options.get("scan", True) else None

            stale_cutoff = datetime.utcnow() - timedelta(minutes=10)
            ExternalDocument.query.filter(
                ExternalDocument.fetch_status == "fetching",
                ExternalDocument.updated_at < stale_cutoff,
            ).update({
                ExternalDocument.fetch_status: "failed",
                ExternalDocument.fetch_error: "上次抓取进程中断，可安全重试",
                ExternalDocument.updated_at: datetime.utcnow(),
            }, synchronize_session=False)
            db.session.commit()

            query = ExternalDocument.query.filter(ExternalDocument.provider.in_(providers))
            if fetch_enabled:
                query = query.filter(ExternalDocument.fetch_status.in_(["pending", "failed", "empty"]))
            elif summarize_enabled:
                query = query.filter(
                    ExternalDocument.fetch_status == "fetched",
                    ExternalDocument.summary_status.in_(["pending", "failed"]),
                )
            query = query.order_by(ExternalDocument.updated_at.asc())
            if document_limit > 0:
                query = query.limit(document_limit)
            document_ids = [row[0] for row in query.with_entities(ExternalDocument.id).all()]
            total = len(document_ids)

            task = _load_task(task_id)
            if not task:
                raise RuntimeError(f"External document backfill task disappeared: {task_id}")
            result = dict(task.result or {})
            result.update({"scan": scan_result, "total": total, "providers": sorted(providers)})
            task.result = result
            task.current_step = "fetch" if fetch_enabled else "summarize"
            flag_modified(task, "result")
            db.session.commit()

            succeeded = 0
            failed = 0
            items = []
            for index, document_id in enumerate(document_ids, start=1):
                item = {"id": document_id}
                try:
                    document = (
                        ExternalDocument.query.options(
                            load_only(
                                ExternalDocument.id,
                                ExternalDocument.provider,
                                ExternalDocument.fetch_status,
                            )
                        )
                        .filter(ExternalDocument.id == str(document_id))
                        .one_or_none()
                    )
                    if not document:
                        raise ValueError("关联内容不存在")
                    item["provider"] = document.provider
                    if fetch_enabled:
                        document = external_document_service.fetch_document(
                            document_id,
                            credential_id=credential_id if document.provider == "feishu" else None,
                        )
                    if summarize_enabled and document.fetch_status == "fetched":
                        document = external_document_service.summarize_document(document_id)
                    item.update({"ok": True, "status": document.fetch_status})
                    succeeded += 1
                except Exception as exc:
                    db.session.rollback()
                    item.update({"ok": False, "error": str(exc)[:500]})
                    failed += 1
                    logger.warning("External document backfill item failed | id=%s | error=%s", document_id, exc)
                items.append(item)
                items = items[-100:]

                task = _load_task(task_id)
                if not task:
                    raise RuntimeError(f"External document backfill task disappeared: {task_id}")
                result = dict(task.result or {})
                result.update({
                    "processed": index,
                    "succeeded": succeeded,
                    "failed": failed,
                    "items": items,
                })
                task.result = result
                task.progress = min(99, 5 + int(index * 94 / max(total, 1)))
                flag_modified(task, "result")
                db.session.commit()

            task = _load_task(task_id)
            if not task:
                raise RuntimeError(f"External document backfill task disappeared: {task_id}")
            task.status = "completed"
            task.current_step = "done"
            task.progress = 100
            task.completed_at = datetime.utcnow()
            result = dict(task.result or {})
            result["completed_at"] = task.completed_at.isoformat() + "Z"
            task.result = result
            flag_modified(task, "result")
            db.session.commit()
            return True
        except Exception as exc:
            db.session.rollback()
            task = _load_task(task_id)
            if task:
                task.status = "failed"
                task.current_step = "failed"
                task.error_message = str(exc)[:2000]
                task.completed_at = datetime.utcnow()
                result = dict(task.result or {})
                result["fatal_error"] = str(exc)[:1000]
                task.result = result
                flag_modified(task, "result")
                db.session.commit()
            logger.exception("External document backfill failed: %s", task_id)
            return False
