import logging
import os
import sys

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from celery_config import celery_app


logger = logging.getLogger(__name__)


@celery_app.task(bind=True, name="tasks.zsxq_workflow.run_zsxq_workflow")
def run_zsxq_workflow(self, workflow_run_id, runtime=None):
    """Start a configured ZSXQ workflow and submit its first stage."""
    from app import create_app
    from services.zsxq_workflow_service import zsxq_workflow_service

    app = create_app()
    with app.app_context():
        logger.info("[ZSXQ_WORKFLOW_TASK] start | workflow=%s", workflow_run_id)
        return zsxq_workflow_service.start_workflow(workflow_run_id, runtime=runtime or {})


@celery_app.task(bind=True, name="tasks.zsxq_workflow.advance_zsxq_workflow")
def advance_zsxq_workflow(self, workflow_run_id, reason=None):
    """Advance a workflow after one stage has reached a terminal state."""
    from app import create_app
    from services.zsxq_workflow_service import zsxq_workflow_service

    app = create_app()
    with app.app_context():
        logger.info("[ZSXQ_WORKFLOW_TASK] advance | workflow=%s reason=%s", workflow_run_id, reason or "-")
        return zsxq_workflow_service.advance_workflow(workflow_run_id, reason=reason)
