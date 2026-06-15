import logging
import os
import sys
from datetime import datetime

from sqlalchemy.orm.attributes import flag_modified

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from celery_config import celery_app
from models import db
from models.task import PublicationTask
from services.content_pipeline.auto_publish_handoff import publish_clean_output


logger = logging.getLogger(__name__)


def _create_zsxq_publish_app():
    """Create an app context for publish tasks without depending on API blueprints."""
    from app import create_app

    try:
        return create_app()
    except Exception:
        logger.exception("Full Flask app initialization failed; falling back to DB-only publish app.")

    from flask import Flask
    from config import Config

    app = Flask(__name__)
    app.config.from_object(Config)
    db.init_app(app)
    return app


@celery_app.task(bind=True, name="tasks.zsxq_publish.run_publish_clean_output_task")
def run_publish_clean_output_task(self, task_id):
    """Publish cleaned content-pipeline output in the dedicated zsxq_publish queue."""
    app = _create_zsxq_publish_app()
    with app.app_context():
        task = PublicationTask.query.get(task_id)
        if not task:
            logger.error("ZSXQ publish task not found: %s", task_id)
            return False

        params = task.params or {}
        publish_params = params.get("zsxq_publish") or {}

        try:
            task.status = "processing"
            task.current_step = "publish"
            task.progress = 5
            result = dict(task.result or {})
            result["message"] = "正在发布清洗产物到知识星球。"
            task.result = result
            flag_modified(task, "result")
            db.session.commit()

            publish_result = publish_clean_output(
                file_type=publish_params.get("file_type"),
                output_dir=publish_params.get("output_dir"),
                options=publish_params.get("options") or {},
                requested_by=f"zsxq_publish:{task.id}",
            )

            status = str(publish_result.get("status") or "").lower()
            failed = status == "failed" or bool(publish_result.get("reason") == "exception")
            task.status = "failed" if failed else "completed"
            task.current_step = "failed" if failed else "done"
            task.progress = 100
            task.completed_at = datetime.utcnow()
            task.error_message = publish_result.get("message") if failed else None
            task.result = {
                **(task.result or {}),
                **publish_result,
                "publish_result": publish_result,
            }
            flag_modified(task, "result")
            db.session.commit()

            if failed:
                logger.error("ZSXQ publish task failed: %s message=%s", task_id, task.error_message)
                _notify_workflow_from_publish_task(task)
                return False

            logger.info("ZSXQ publish task completed: %s status=%s", task_id, status or "completed")
            _notify_workflow_from_publish_task(task)
            return True
        except Exception as exc:
            db.session.rollback()
            task = PublicationTask.query.get(task_id)
            if task:
                result = dict(task.result or {})
                result["error"] = str(exc)
                task.status = "failed"
                task.current_step = "failed"
                task.progress = task.progress or 0
                task.error_message = str(exc)
                task.completed_at = datetime.utcnow()
                task.result = result
                flag_modified(task, "result")
                db.session.commit()
                _notify_workflow_from_publish_task(task)
            logger.error("ZSXQ publish task failed: %s", exc, exc_info=True)
            return False


def _notify_workflow_from_publish_task(task):
    try:
        publish_params = (task.params or {}).get("zsxq_publish") or {}
        metadata = publish_params.get("metadata") if isinstance(publish_params.get("metadata"), dict) else {}
        workflow_run_id = metadata.get("workflow_run_id")
        if not workflow_run_id:
            return

        from tasks.zsxq_workflow import advance_zsxq_workflow

        advance_zsxq_workflow.apply_async(
            args=[workflow_run_id, "publish_task_finished"],
            queue="zsxq_workflow",
            retry=False,
        )
    except Exception:
        logger.exception("[ZSXQ_PUBLISH_TASK] workflow notify failed | task=%s", getattr(task, "id", "-"))
