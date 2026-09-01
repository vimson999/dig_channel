#!/usr/bin/env python3
"""Verify durable workflow notification enqueue, dedupe, worker drain, and audit.

The command is read-only/dry-run unless ``--send`` is explicit.  A send run
creates one real workflow-completed email event, enqueues the same event twice,
then waits for the durable delivery to reach a terminal state.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = PROJECT_ROOT / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def _parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--send",
        action="store_true",
        help="enqueue one real test email and wait for its durable result",
    )
    parser.add_argument(
        "--wait-seconds",
        type=int,
        default=120,
        help="maximum time to wait for sent/dead_letter (default: 120)",
    )
    return parser


def _delivery_snapshot(db, delivery_model, provider_model, delivery_id):
    db.session.remove()
    try:
        delivery = db.session.get(delivery_model, str(delivery_id))
        if delivery is None:
            return {"delivery": None, "provider": None, "matching_rows": 0}
        provider = db.session.get(provider_model, delivery.provider)
        matching_rows = delivery_model.query.filter_by(
            dedupe_key=delivery.dedupe_key
        ).count()
        return {
            "delivery": {
                "id": delivery.id,
                "provider": delivery.provider,
                "event_type": delivery.event_type,
                "workflow_id": delivery.workflow_id,
                "status": delivery.status,
                "attempt_count": int(delivery.attempt_count or 0),
                "celery_task_id": delivery.celery_task_id,
                "last_error_type": delivery.last_error_type,
                "last_error_message": delivery.last_error_message,
                "sent_at": delivery.sent_at.isoformat() if delivery.sent_at else None,
            },
            "provider": provider.to_dict() if provider is not None else None,
            "matching_rows": int(matching_rows),
        }
    finally:
        db.session.remove()


def main(argv=None):
    args = _parser().parse_args(argv)
    if not args.send:
        print(
            json.dumps(
                {
                    "status": "dry_run",
                    "will_send_real_email": True,
                    "checks": [
                        "same event enqueued twice maps to one delivery",
                        "workflow_notify worker drains the durable row",
                        "provider state and SMTP result are persisted",
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    from app import create_app
    from models import db
    from models.workflow_notification import (
        WorkflowNotificationDelivery,
        WorkflowNotificationProviderState,
    )
    from services.workflow_notification_service import workflow_notification_service

    workflow_id = f"db-audit-runtime-{uuid.uuid4()}"
    workflow = {
        "id": workflow_id,
        "group_name": "每日数据库巡检",
        "group_label": "验收",
        "status": "completed",
        "current_stage": "notification_runtime_acceptance",
        "trigger": "db_audit",
        "event_message": "durable outbox、去重与真实 worker 发送验收",
        "created_at": datetime.utcnow().isoformat() + "Z",
    }
    app = create_app()
    with app.app_context():
        first = workflow_notification_service.enqueue_workflow_event(
            "workflow_completed", workflow
        )
        second = workflow_notification_service.enqueue_workflow_event(
            "workflow_completed", dict(workflow)
        )
        delivery_id = first.get("delivery_id")
        deadline = time.monotonic() + max(1, int(args.wait_seconds or 120))
        snapshot = {
            "delivery": None,
            "provider": None,
            "matching_rows": 0,
        }
        while delivery_id and time.monotonic() <= deadline:
            snapshot = _delivery_snapshot(
                db,
                WorkflowNotificationDelivery,
                WorkflowNotificationProviderState,
                delivery_id,
            )
            status = (snapshot.get("delivery") or {}).get("status")
            if status in {"sent", "dead_letter"}:
                break
            time.sleep(2)

        result = {
            "status": "accepted"
            if (
                delivery_id
                and delivery_id == second.get("delivery_id")
                and snapshot.get("matching_rows") == 1
                and (snapshot.get("delivery") or {}).get("status") == "sent"
                and int((snapshot.get("delivery") or {}).get("attempt_count") or 0)
                == 1
            )
            else "failed",
            "workflow_id": workflow_id,
            "same_delivery_id": bool(
                delivery_id and delivery_id == second.get("delivery_id")
            ),
            "first_enqueue": {
                "queued": first.get("queued"),
                "durable": first.get("durable"),
                "deduplicated": first.get("deduplicated"),
                "delivery_id": delivery_id,
            },
            "second_enqueue": {
                "queued": second.get("queued"),
                "durable": second.get("durable"),
                "deduplicated": second.get("deduplicated"),
                "delivery_id": second.get("delivery_id"),
            },
            **snapshot,
            "mailbox_receipt_verified": False,
        }
        print(json.dumps(result, ensure_ascii=False, default=str, indent=2))
        return 0 if result["status"] == "accepted" else 1


if __name__ == "__main__":
    raise SystemExit(main())
