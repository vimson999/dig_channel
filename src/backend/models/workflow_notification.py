from datetime import datetime
import uuid

from . import db


def _iso(value):
    return value.isoformat() if value else None


class WorkflowNotificationDelivery(db.Model):
    """Durable, auditable delivery state for one workflow notification event."""

    __tablename__ = "workflow_notification_deliveries"
    __table_args__ = (
        db.UniqueConstraint(
            "dedupe_key",
            name="uq_workflow_notification_deliveries_dedupe_key",
        ),
        db.Index(
            "ix_workflow_notification_deliveries_provider_due",
            "provider",
            "status",
            "available_at",
            "created_at",
        ),
        db.Index(
            "ix_workflow_notification_deliveries_lease",
            "status",
            "lease_expires_at",
        ),
        db.Index(
            "ix_workflow_notification_deliveries_workflow",
            "workflow_id",
            "event_type",
            "created_at",
        ),
    )

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    dedupe_key = db.Column(db.String(64), nullable=False)
    provider = db.Column(db.String(32), nullable=False)
    event_type = db.Column(db.String(80), nullable=False)
    workflow_id = db.Column(db.String(200), nullable=False)
    payload = db.Column(db.JSON, nullable=False)
    payload_hash = db.Column(db.String(64), nullable=False)
    status = db.Column(db.String(24), nullable=False, default="pending")
    attempt_count = db.Column(db.Integer, nullable=False, default=0)
    available_at = db.Column(db.DateTime, nullable=True)
    lease_owner = db.Column(db.String(100), nullable=True)
    lease_expires_at = db.Column(db.DateTime, nullable=True)
    celery_task_id = db.Column(db.String(50), nullable=True)
    last_error_type = db.Column(db.String(160), nullable=True)
    last_error_message = db.Column(db.Text, nullable=True)
    first_attempt_at = db.Column(db.DateTime, nullable=True)
    last_attempt_at = db.Column(db.DateTime, nullable=True)
    sent_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    def to_dict(self):
        return {
            "id": self.id,
            "dedupe_key": self.dedupe_key,
            "provider": self.provider,
            "event_type": self.event_type,
            "workflow_id": self.workflow_id,
            "payload": dict(self.payload or {}),
            "payload_hash": self.payload_hash,
            "status": self.status,
            "attempt_count": int(self.attempt_count or 0),
            "available_at": _iso(self.available_at),
            "lease_owner": self.lease_owner,
            "lease_expires_at": _iso(self.lease_expires_at),
            "celery_task_id": self.celery_task_id,
            "last_error_type": self.last_error_type,
            "last_error_message": self.last_error_message,
            "first_attempt_at": _iso(self.first_attempt_at),
            "last_attempt_at": _iso(self.last_attempt_at),
            "sent_at": _iso(self.sent_at),
            "created_at": _iso(self.created_at),
            "updated_at": _iso(self.updated_at),
        }


class WorkflowNotificationProviderState(db.Model):
    """One cross-event SMTP permit and circuit breaker per provider."""

    __tablename__ = "workflow_notification_provider_states"

    provider = db.Column(db.String(32), primary_key=True)
    state = db.Column(db.String(16), nullable=False, default="closed")
    consecutive_failures = db.Column(db.Integer, nullable=False, default=0)
    open_until = db.Column(db.DateTime, nullable=True)
    lease_owner = db.Column(db.String(100), nullable=True)
    lease_expires_at = db.Column(db.DateTime, nullable=True)
    last_error_type = db.Column(db.String(160), nullable=True)
    last_error_message = db.Column(db.Text, nullable=True)
    last_success_at = db.Column(db.DateTime, nullable=True)
    last_failure_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    def to_dict(self):
        return {
            "provider": self.provider,
            "state": self.state,
            "consecutive_failures": int(self.consecutive_failures or 0),
            "open_until": _iso(self.open_until),
            "lease_owner": self.lease_owner,
            "lease_expires_at": _iso(self.lease_expires_at),
            "last_error_type": self.last_error_type,
            "last_error_message": self.last_error_message,
            "last_success_at": _iso(self.last_success_at),
            "last_failure_at": _iso(self.last_failure_at),
            "created_at": _iso(self.created_at),
            "updated_at": _iso(self.updated_at),
        }
