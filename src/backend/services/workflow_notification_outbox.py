"""Durable notification delivery and cross-event provider circuit breaker."""

from __future__ import annotations

import hashlib
import json
import os
import random
from datetime import datetime, timedelta

from sqlalchemy import and_, case, func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from models import db
from models.workflow_notification import (
    WorkflowNotificationDelivery,
    WorkflowNotificationProviderState,
)


OPEN_STATUSES = ("pending", "retry_wait")


def _now(value=None):
    return value or datetime.utcnow()


def _canonical_json(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _workflow_identity(workflow):
    workflow = workflow if isinstance(workflow, dict) else {}
    for key in (
        "id",
        "workflow_id",
        "workflow_run_id",
        "task_id",
        "run_id",
        "orchestration_id",
    ):
        value = str(workflow.get(key) or "").strip()
        if value:
            return value[:200]
    stable = {
        key: value
        for key, value in workflow.items()
        if key not in {"queued_at", "updated_at", "created_at"}
    }
    return "payload:" + hashlib.sha256(_canonical_json(stable).encode("utf-8")).hexdigest()


def notification_dedupe_key(provider, event_type, workflow):
    identity = _workflow_identity(workflow)
    raw = "\x1f".join(
        (
            str(provider or "").strip().lower(),
            str(event_type or "workflow").strip().lower(),
            identity,
        )
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def notification_payload_hash(payload):
    stable = dict(payload) if isinstance(payload, dict) else payload or {}
    if isinstance(stable, dict):
        stable.pop("queued_at", None)
    return hashlib.sha256(_canonical_json(stable).encode("utf-8")).hexdigest()


def _session():
    return sessionmaker(bind=db.engine, expire_on_commit=False, future=True)()


def enqueue_delivery(*, provider, event_type, workflow, payload, now=None):
    provider = str(provider or "email").strip().lower() or "email"
    event_type = str(event_type or "workflow").strip() or "workflow"
    workflow = workflow if isinstance(workflow, dict) else {}
    payload = payload if isinstance(payload, dict) else {}
    timestamp = _now(now)
    dedupe_key = notification_dedupe_key(provider, event_type, workflow)
    payload_hash = notification_payload_hash(payload)
    session = _session()
    try:
        existing = session.query(WorkflowNotificationDelivery).filter_by(
            dedupe_key=dedupe_key
        ).one_or_none()
        created = existing is None
        if existing is None:
            existing = WorkflowNotificationDelivery(
                dedupe_key=dedupe_key,
                provider=provider,
                event_type=event_type,
                workflow_id=_workflow_identity(workflow),
                payload=payload,
                payload_hash=payload_hash,
                status="pending",
                available_at=timestamp,
                created_at=timestamp,
                updated_at=timestamp,
            )
            session.add(existing)
        session.commit()
        result = existing.to_dict()
        result.update(
            {
                "created": created,
                "payload_conflict": existing.payload_hash != payload_hash,
            }
        )
        return result
    except IntegrityError:
        session.rollback()
        existing = session.query(WorkflowNotificationDelivery).filter_by(
            dedupe_key=dedupe_key
        ).one()
        result = existing.to_dict()
        result.update(
            {
                "created": False,
                "payload_conflict": existing.payload_hash != payload_hash,
            }
        )
        return result
    finally:
        session.close()


def _provider_state(session, provider):
    if session.bind.dialect.name == "postgresql":
        # A SELECT FOR UPDATE cannot lock a missing row.  Seed it with a
        # conflict-safe insert first so concurrent first deliveries serialize
        # on the provider primary key instead of both acquiring send permits.
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        session.execute(
            pg_insert(WorkflowNotificationProviderState)
            .values(
                provider=provider,
                state="closed",
                consecutive_failures=0,
            )
            .on_conflict_do_nothing(index_elements=["provider"])
        )
    query = session.query(WorkflowNotificationProviderState).filter_by(
        provider=provider
    )
    if session.bind.dialect.name == "postgresql":
        query = query.with_for_update()
    state = query.one_or_none()
    if state is None:
        state = WorkflowNotificationProviderState(
            provider=provider,
            state="closed",
            consecutive_failures=0,
        )
        session.add(state)
        session.flush()
    return state


def _delivery_query_for_update(session, delivery_id):
    query = session.query(WorkflowNotificationDelivery).filter_by(id=str(delivery_id))
    if session.bind.dialect.name == "postgresql":
        query = query.with_for_update()
    return query.one()


def _provider_for_delivery(session, delivery_id):
    provider = (
        session.query(WorkflowNotificationDelivery.provider)
        .filter(WorkflowNotificationDelivery.id == str(delivery_id))
        .scalar()
    )
    if not provider:
        raise RuntimeError(f"notification delivery does not exist: {delivery_id}")
    return str(provider)


def _lock_provider_then_delivery(session, delivery_id):
    """Use the same provider→delivery lock order as acquisition.

    The provider column is immutable.  Reading it before either row lock lets
    completion/failure avoid a lock-order inversion with an expired-lease
    acquisition while still rechecking the authoritative delivery under lock.
    """

    provider = _provider_for_delivery(session, delivery_id)
    state = _provider_state(session, provider)
    delivery = _delivery_query_for_update(session, delivery_id)
    if delivery.provider != provider:
        raise RuntimeError(
            "notification delivery provider changed while locking: "
            f"delivery={delivery_id} expected={provider} actual={delivery.provider}"
        )
    return state, delivery


def acquire_delivery(provider, owner, *, now=None, lease_seconds=45):
    provider = str(provider or "email").strip().lower() or "email"
    owner = str(owner or "unknown")[:100]
    timestamp = _now(now)
    lease_until = timestamp + timedelta(seconds=max(5, int(lease_seconds or 45)))
    session = _session()
    try:
        with session.begin():
            state = _provider_state(session, provider)
            if state.lease_expires_at and state.lease_expires_at <= timestamp:
                state.lease_owner = None
                state.lease_expires_at = None
            if (
                state.lease_owner
                and state.lease_owner != owner
                and state.lease_expires_at
                and state.lease_expires_at > timestamp
            ):
                return {
                    "acquired": False,
                    "reason": "provider_busy",
                    "provider": state.to_dict(),
                }
            if state.open_until and state.open_until > timestamp:
                state.state = "open"
                return {
                    "acquired": False,
                    "reason": "circuit_open",
                    "retry_at": state.open_until.isoformat(),
                    "provider": state.to_dict(),
                }

            due = or_(
                and_(
                    WorkflowNotificationDelivery.status.in_(OPEN_STATUSES),
                    or_(
                        WorkflowNotificationDelivery.available_at.is_(None),
                        WorkflowNotificationDelivery.available_at <= timestamp,
                    ),
                ),
                and_(
                    WorkflowNotificationDelivery.status == "leased",
                    WorkflowNotificationDelivery.lease_expires_at <= timestamp,
                ),
            )
            query = (
                session.query(WorkflowNotificationDelivery)
                .filter(
                    WorkflowNotificationDelivery.provider == provider,
                    due,
                )
                .order_by(
                    case(
                        (
                            WorkflowNotificationDelivery.status.in_(
                                ("retry_wait", "leased")
                            ),
                            0,
                        ),
                        else_=1,
                    ),
                    WorkflowNotificationDelivery.available_at.asc(),
                    WorkflowNotificationDelivery.created_at.asc(),
                )
            )
            if session.bind.dialect.name == "postgresql":
                query = query.with_for_update(skip_locked=True)
            delivery = query.first()
            if delivery is None:
                if state.state == "half_open":
                    state.state = "closed"
                    state.open_until = None
                return {
                    "acquired": False,
                    "reason": "no_due_delivery",
                    "provider": state.to_dict(),
                }

            state.state = "half_open" if state.open_until else "closed"
            state.lease_owner = owner
            state.lease_expires_at = lease_until
            state.updated_at = timestamp
            delivery.status = "leased"
            delivery.lease_owner = owner
            delivery.lease_expires_at = lease_until
            delivery.attempt_count = int(delivery.attempt_count or 0) + 1
            delivery.first_attempt_at = delivery.first_attempt_at or timestamp
            delivery.last_attempt_at = timestamp
            delivery.updated_at = timestamp
            session.flush()
            result = {
                "acquired": True,
                "reason": "leased",
                "delivery": delivery.to_dict(),
                "provider": state.to_dict(),
            }
        return result
    finally:
        session.close()


def _assert_owner(delivery, owner):
    if delivery.status != "leased" or delivery.lease_owner != str(owner or "")[:100]:
        raise RuntimeError(
            "notification delivery lease ownership changed: "
            f"delivery={delivery.id} expected={owner} actual={delivery.lease_owner} "
            f"status={delivery.status}"
        )


def complete_delivery(delivery_id, owner, *, now=None):
    timestamp = _now(now)
    session = _session()
    try:
        with session.begin():
            state, delivery = _lock_provider_then_delivery(session, delivery_id)
            _assert_owner(delivery, owner)
            delivery.status = "sent"
            delivery.sent_at = timestamp
            delivery.available_at = None
            delivery.lease_owner = None
            delivery.lease_expires_at = None
            delivery.last_error_type = None
            delivery.last_error_message = None
            delivery.updated_at = timestamp
            state.state = "closed"
            state.consecutive_failures = 0
            state.open_until = None
            state.lease_owner = None
            state.lease_expires_at = None
            state.last_error_type = None
            state.last_error_message = None
            state.last_success_at = timestamp
            state.updated_at = timestamp
            session.flush()
            result = {
                "delivery": delivery.to_dict(),
                "provider": state.to_dict(),
            }
        return result
    finally:
        session.close()


def _breaker_delay_seconds(failure_count):
    try:
        base = max(10, int(os.environ.get("WORKFLOW_NOTIFY_BREAKER_BASE_SECONDS", "60")))
    except (TypeError, ValueError):
        base = 60
    try:
        maximum = max(base, int(os.environ.get("WORKFLOW_NOTIFY_BREAKER_MAX_SECONDS", "1800")))
    except (TypeError, ValueError):
        maximum = 1800
    backoff = min(
        base * (2 ** max(0, int(failure_count or 1) - 1)),
        maximum,
    )
    jitter_room = max(0, maximum - backoff)
    jitter = random.uniform(0, min(30, jitter_room)) if jitter_room else 0
    return backoff + jitter


def fail_delivery(delivery_id, owner, *, error, transient, now=None):
    timestamp = _now(now)
    error_type = type(error).__name__
    error_message = str(error or error_type)[:4000]
    session = _session()
    try:
        with session.begin():
            state, delivery = _lock_provider_then_delivery(session, delivery_id)
            _assert_owner(delivery, owner)
            delivery.last_error_type = error_type[:160]
            delivery.last_error_message = error_message
            delivery.lease_owner = None
            delivery.lease_expires_at = None
            delivery.updated_at = timestamp
            state.last_error_type = error_type[:160]
            state.last_error_message = error_message
            state.last_failure_at = timestamp
            state.lease_owner = None
            state.lease_expires_at = None
            state.updated_at = timestamp
            if transient:
                failures = int(state.consecutive_failures or 0) + 1
                open_until = timestamp + timedelta(
                    seconds=_breaker_delay_seconds(failures)
                )
                state.state = "open"
                state.consecutive_failures = failures
                state.open_until = open_until
                delivery.status = "retry_wait"
                delivery.available_at = open_until
            else:
                state.state = "closed"
                state.open_until = None
                delivery.status = "dead_letter"
                delivery.available_at = None
            session.flush()
            result = {
                "delivery": delivery.to_dict(),
                "provider": state.to_dict(),
            }
        return result
    finally:
        session.close()


def mark_dispatch_result(delivery_id, *, celery_task_id=None, error=None, now=None):
    timestamp = _now(now)
    session = _session()
    try:
        with session.begin():
            delivery = _delivery_query_for_update(session, delivery_id)
            if celery_task_id:
                delivery.celery_task_id = str(celery_task_id)[:50]
            if error is not None:
                delivery.last_error_type = type(error).__name__[:160]
                delivery.last_error_message = str(error)[:4000]
            delivery.updated_at = timestamp
            session.flush()
            result = delivery.to_dict()
        return result
    finally:
        session.close()


def has_open_delivery(provider, *, now=None):
    provider = str(provider or "email").strip().lower() or "email"
    timestamp = _now(now)
    session = _session()
    try:
        return bool(
            session.query(WorkflowNotificationDelivery.id)
            .filter(
                WorkflowNotificationDelivery.provider == provider,
                or_(
                    WorkflowNotificationDelivery.status.in_(OPEN_STATUSES),
                    and_(
                        WorkflowNotificationDelivery.status == "leased",
                        WorkflowNotificationDelivery.lease_expires_at <= timestamp,
                    ),
                ),
            )
            .first()
        )
    finally:
        session.close()


def due_provider_drains(*, now=None):
    """Return provider snapshots that are safe for the watchdog to dispatch.

    The read session is always closed before the caller performs a broker I/O.
    Duplicate broker messages are harmless because ``acquire_delivery`` owns
    the authoritative provider lease.
    """

    timestamp = _now(now)
    due = or_(
        and_(
            WorkflowNotificationDelivery.status.in_(OPEN_STATUSES),
            or_(
                WorkflowNotificationDelivery.available_at.is_(None),
                WorkflowNotificationDelivery.available_at <= timestamp,
            ),
        ),
        and_(
            WorkflowNotificationDelivery.status == "leased",
            WorkflowNotificationDelivery.lease_expires_at <= timestamp,
        ),
    )
    session = _session()
    try:
        rows = (
            session.query(
                WorkflowNotificationDelivery.provider,
                func.count(WorkflowNotificationDelivery.id),
            )
            .filter(due)
            .group_by(WorkflowNotificationDelivery.provider)
            .order_by(WorkflowNotificationDelivery.provider.asc())
            .all()
        )
        providers = [str(provider) for provider, _count in rows]
        if not providers:
            return []
        states = {
            item.provider: item
            for item in session.query(WorkflowNotificationProviderState)
            .filter(WorkflowNotificationProviderState.provider.in_(providers))
            .all()
        }
        result = []
        for provider, count in rows:
            state = states.get(provider)
            if state is not None:
                if state.open_until and state.open_until > timestamp:
                    continue
                if (
                    state.lease_owner
                    and state.lease_expires_at
                    and state.lease_expires_at > timestamp
                ):
                    continue
            result.append({"provider": str(provider), "due_count": int(count or 0)})
        return result
    finally:
        session.close()
