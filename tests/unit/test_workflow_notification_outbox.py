import sys
import unittest
import os
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from flask import Flask


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from models import db  # noqa: E402
from models.workflow_notification import (  # noqa: E402
    WorkflowNotificationDelivery,
    WorkflowNotificationProviderState,
)
from services.workflow_notification_outbox import (  # noqa: E402
    acquire_delivery,
    complete_delivery,
    due_provider_drains,
    enqueue_delivery,
    fail_delivery,
    notification_dedupe_key,
)
from services import workflow_notification_outbox  # noqa: E402
from services.workflow_notification_service import WorkflowNotificationService  # noqa: E402


class WorkflowNotificationOutboxTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(self.app)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        self.now = datetime(2026, 8, 29, 8, 0, 0)

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _enqueue(self, workflow_id, event_type="workflow_completed"):
        workflow = {
            "id": workflow_id,
            "title": "研报工作流",
            "stage_results": {"publish": {"status": "completed"}},
        }
        payload = {
            "provider": "email",
            "event_type": event_type,
            "workflow": workflow,
            "queued_at": "2026-08-29T08:00:00Z",
        }
        return enqueue_delivery(
            provider="email",
            event_type=event_type,
            workflow=workflow,
            payload=payload,
            now=self.now,
        )

    def test_dedupe_key_is_stable_for_mapping_order_and_ignores_queued_at(self):
        first = notification_dedupe_key(
            "email",
            "workflow_completed",
            {"id": "workflow-1", "title": "A", "queued_at": "one"},
        )
        second = notification_dedupe_key(
            "email",
            "workflow_completed",
            {"queued_at": "two", "title": "B", "id": "workflow-1"},
        )

        self.assertEqual(first, second)
        self.assertNotEqual(
            first,
            notification_dedupe_key("email", "workflow_failed", {"id": "workflow-1"}),
        )

    def test_duplicate_enqueue_reuses_one_durable_delivery(self):
        first = self._enqueue("workflow-1")
        second = self._enqueue("workflow-1")

        self.assertEqual(first["id"], second["id"])
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertEqual(1, WorkflowNotificationDelivery.query.count())
        self.assertEqual("pending", first["status"])

    def test_duplicate_enqueue_ignores_only_volatile_queue_timestamp_for_payload_conflict(self):
        first = self._enqueue("workflow-1")
        workflow = {"id": "workflow-1", "title": "研报工作流"}
        duplicate = enqueue_delivery(
            provider="email",
            event_type="workflow_completed",
            workflow=workflow,
            payload={
                "provider": "email",
                "event_type": "workflow_completed",
                "workflow": {
                    "id": "workflow-1",
                    "title": "研报工作流",
                    "stage_results": {"publish": {"status": "completed"}},
                },
                "queued_at": "2026-08-29T09:00:00Z",
            },
            now=self.now + timedelta(hours=1),
        )

        self.assertEqual(first["id"], duplicate["id"])
        self.assertFalse(duplicate["payload_conflict"])

    def test_provider_has_only_one_active_send_lease(self):
        first = self._enqueue("workflow-1")
        self._enqueue("workflow-2")

        acquired = acquire_delivery("email", "worker-a", now=self.now)
        blocked = acquire_delivery("email", "worker-b", now=self.now)

        self.assertTrue(acquired["acquired"])
        self.assertEqual(first["id"], acquired["delivery"]["id"])
        self.assertFalse(blocked["acquired"])
        self.assertEqual("provider_busy", blocked["reason"])

    def test_state_transition_locks_provider_before_delivery(self):
        order = []
        provider_state = object()
        delivery = type("Delivery", (), {"provider": "email"})()
        with mock.patch.object(
            workflow_notification_outbox,
            "_provider_for_delivery",
            return_value="email",
        ), mock.patch.object(
            workflow_notification_outbox,
            "_provider_state",
            side_effect=lambda _session, _provider: order.append("provider")
            or provider_state,
        ), mock.patch.object(
            workflow_notification_outbox,
            "_delivery_query_for_update",
            side_effect=lambda _session, _delivery_id: order.append("delivery")
            or delivery,
        ):
            state_result, delivery_result = (
                workflow_notification_outbox._lock_provider_then_delivery(
                    object(), "delivery-1"
                )
            )

        self.assertEqual(["provider", "delivery"], order)
        self.assertIs(provider_state, state_result)
        self.assertIs(delivery, delivery_result)

    def test_transient_failure_opens_shared_circuit_and_recovery_drains(self):
        first = self._enqueue("workflow-1")
        second = self._enqueue("workflow-2")
        acquired = acquire_delivery("email", "worker-a", now=self.now)

        failed = fail_delivery(
            acquired["delivery"]["id"],
            "worker-a",
            error=TimeoutError("smtp timeout"),
            transient=True,
            now=self.now,
        )
        blocked = acquire_delivery(
            "email",
            "worker-b",
            now=self.now + timedelta(seconds=30),
        )

        self.assertEqual("retry_wait", failed["delivery"]["status"])
        self.assertEqual("open", failed["provider"]["state"])
        self.assertFalse(blocked["acquired"])
        self.assertEqual("circuit_open", blocked["reason"])

        retry_at = datetime.fromisoformat(failed["provider"]["open_until"])
        retry = acquire_delivery("email", "worker-b", now=retry_at)
        self.assertTrue(retry["acquired"])
        self.assertEqual(first["id"], retry["delivery"]["id"])
        complete_delivery(first["id"], "worker-b", now=retry_at)

        next_delivery = acquire_delivery(
            "email",
            "worker-c",
            now=retry_at + timedelta(seconds=1),
        )
        self.assertTrue(next_delivery["acquired"])
        self.assertEqual(second["id"], next_delivery["delivery"]["id"])

    def test_expired_worker_lease_is_recovered(self):
        item = self._enqueue("workflow-1")
        acquire_delivery("email", "dead-worker", now=self.now, lease_seconds=10)

        recovered = acquire_delivery(
            "email",
            "replacement-worker",
            now=self.now + timedelta(seconds=11),
            lease_seconds=10,
        )

        self.assertTrue(recovered["acquired"])
        self.assertEqual(item["id"], recovered["delivery"]["id"])
        self.assertEqual("replacement-worker", recovered["delivery"]["lease_owner"])

    def test_permanent_failure_is_auditable_dead_letter(self):
        item = self._enqueue("workflow-1")
        acquire_delivery("email", "worker-a", now=self.now)

        failed = fail_delivery(
            item["id"],
            "worker-a",
            error=ValueError("invalid recipient"),
            transient=False,
            now=self.now,
        )

        self.assertEqual("dead_letter", failed["delivery"]["status"])
        self.assertIn("invalid recipient", failed["delivery"]["last_error_message"])
        state = db.session.get(WorkflowNotificationProviderState, "email")
        self.assertIsNone(state.lease_owner)

    def test_reconciler_only_returns_due_provider_without_live_lease_or_open_circuit(self):
        item = self._enqueue("workflow-1")

        self.assertEqual(
            [{"provider": "email", "due_count": 1}],
            due_provider_drains(now=self.now),
        )

        acquire_delivery("email", "worker-a", now=self.now)
        self.assertEqual([], due_provider_drains(now=self.now))

        failed = fail_delivery(
            item["id"],
            "worker-a",
            error=TimeoutError("smtp timeout"),
            transient=True,
            now=self.now,
        )
        retry_at = datetime.fromisoformat(failed["provider"]["open_until"])
        self.assertEqual([], due_provider_drains(now=retry_at - timedelta(seconds=1)))
        self.assertEqual(
            [{"provider": "email", "due_count": 1}],
            due_provider_drains(now=retry_at),
        )

    def test_broker_failure_after_commit_keeps_durable_pending_delivery(self):
        service = WorkflowNotificationService()
        with mock.patch.dict(
            os.environ,
            {
                "WORKFLOW_NOTIFY_PROVIDER": "email",
                "WORKFLOW_NOTIFY_LEVEL": "milestone",
                "WORKFLOW_EMAIL_USER": "sender@example.test",
                "WORKFLOW_EMAIL_PASSWORD": "secret",
                "WORKFLOW_NOTIFY_TO": "owner@example.test",
            },
            clear=False,
        ), mock.patch(
            "services.workflow_notification_service.apply_async_after_db_release",
            side_effect=ConnectionError("broker unavailable"),
        ):
            result = service.enqueue_workflow_event(
                "workflow_completed",
                {"id": "workflow-broker-failure"},
            )

        db.session.remove()
        delivery = WorkflowNotificationDelivery.query.one()
        self.assertTrue(result["queued"])
        self.assertTrue(result["durable"])
        self.assertFalse(result["dispatch_scheduled"])
        self.assertEqual("pending", delivery.status)
        self.assertEqual("ConnectionError", delivery.last_error_type)

    def test_uncommitted_business_changes_block_outbox_before_durable_insert(self):
        service = WorkflowNotificationService()
        db.session.add(WorkflowNotificationProviderState(provider="dirty-business"))
        with unittest.mock.patch.dict(
            os.environ,
            {
                "WORKFLOW_NOTIFY_PROVIDER": "email",
                "WORKFLOW_NOTIFY_LEVEL": "milestone",
                "WORKFLOW_EMAIL_USER": "sender@example.test",
                "WORKFLOW_EMAIL_PASSWORD": "secret",
                "WORKFLOW_NOTIFY_TO": "owner@example.test",
            },
            clear=False,
        ), unittest.mock.patch(
            "services.workflow_notification_service.apply_async_after_db_release",
        ) as dispatch:
            result = service.enqueue_workflow_event(
                "workflow_completed",
                {"id": "workflow-uncommitted-business-change"},
            )

        dispatch.assert_not_called()
        self.assertFalse(result["queued"])
        self.assertEqual("outbox_failed", result["reason"])
        db.session.rollback()
        self.assertEqual(0, WorkflowNotificationDelivery.query.count())

    def test_business_commit_failure_happens_before_outbox_insert(self):
        service = WorkflowNotificationService()
        with mock.patch.dict(
            os.environ,
            {
                "WORKFLOW_NOTIFY_PROVIDER": "email",
                "WORKFLOW_NOTIFY_LEVEL": "milestone",
                "WORKFLOW_EMAIL_USER": "sender@example.test",
                "WORKFLOW_EMAIL_PASSWORD": "secret",
                "WORKFLOW_NOTIFY_TO": "owner@example.test",
            },
            clear=False,
        ), mock.patch.object(
            db.session(),
            "commit",
            side_effect=RuntimeError("business commit failed"),
        ):
            result = service.enqueue_workflow_event(
                "workflow_completed",
                {"id": "workflow-business-commit-failure"},
            )

        self.assertFalse(result["queued"])
        self.assertEqual("outbox_failed", result["reason"])
        db.session.rollback()
        self.assertEqual(0, WorkflowNotificationDelivery.query.count())

    def test_missing_smtp_config_is_audited_in_outbox_instead_of_losing_event(self):
        service = WorkflowNotificationService()
        dispatched = type("DispatchResult", (), {"id": "celery-1"})()
        with unittest.mock.patch.dict(
            os.environ,
            {
                "WORKFLOW_NOTIFY_PROVIDER": "email",
                "WORKFLOW_NOTIFY_LEVEL": "milestone",
            },
            clear=True,
        ), unittest.mock.patch(
            "services.workflow_notification_service.apply_async_after_db_release",
            return_value=dispatched,
        ):
            result = service.enqueue_workflow_event(
                "workflow_failed",
                {"id": "workflow-missing-config"},
            )

        db.session.remove()
        delivery = WorkflowNotificationDelivery.query.one()
        self.assertTrue(result["queued"])
        self.assertTrue(result["durable"])
        self.assertEqual("pending", delivery.status)

    def test_twenty_events_make_one_provider_attempt_during_outage_then_all_drain(self):
        deliveries = [self._enqueue(f"workflow-{index}") for index in range(20)]
        first = acquire_delivery("email", "worker-0", now=self.now)
        failed = fail_delivery(
            first["delivery"]["id"],
            "worker-0",
            error=TimeoutError("smtp outage"),
            transient=True,
            now=self.now,
        )

        for index in range(1, 20):
            blocked = acquire_delivery(
                "email",
                f"worker-{index}",
                now=self.now + timedelta(seconds=1),
            )
            self.assertFalse(blocked["acquired"])
            self.assertEqual("circuit_open", blocked["reason"])

        db.session.remove()
        self.assertEqual(
            1,
            sum(
                item.attempt_count
                for item in WorkflowNotificationDelivery.query.all()
            ),
        )

        retry_at = datetime.fromisoformat(failed["provider"]["open_until"])
        for index in range(20):
            owner = f"recovery-{index}"
            acquired = acquire_delivery("email", owner, now=retry_at)
            self.assertTrue(acquired["acquired"])
            complete_delivery(acquired["delivery"]["id"], owner, now=retry_at)

        db.session.remove()
        rows = WorkflowNotificationDelivery.query.all()
        self.assertEqual(20, len(deliveries))
        self.assertEqual({"sent"}, {item.status for item in rows})
        self.assertEqual(21, sum(item.attempt_count for item in rows))


if __name__ == "__main__":
    unittest.main()
