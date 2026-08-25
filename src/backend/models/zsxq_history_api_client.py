"""Database-backed machine credentials for the ZSXQ history API."""

from datetime import datetime
import uuid

from . import db


def _new_id():
    return str(uuid.uuid4())


class ZsxqHistoryApiClient(db.Model):
    """An app credential assigned to one tenant.

    ``encrypted_secret`` is intentionally not exposed by :meth:`to_dict`.
    The plaintext secret exists only in the create/rotate response and in the
    caller's secret store.  The API server decrypts it only while validating a
    signed request.
    """

    __tablename__ = "zsxq_history_api_clients"

    id = db.Column(db.String(36), primary_key=True, default=_new_id)
    tenant_id = db.Column(db.String(120), nullable=False, index=True)
    app_id = db.Column(db.String(120), nullable=False, unique=True)
    name = db.Column(db.String(120), nullable=False)
    encrypted_secret = db.Column(db.Text, nullable=False)
    secret_fingerprint = db.Column(db.String(64), nullable=False, index=True)
    scopes = db.Column(db.JSON, nullable=False, default=list)
    status = db.Column(db.String(24), nullable=False, default="active", index=True)
    created_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    expires_at = db.Column(db.DateTime, nullable=True)
    last_used_at = db.Column(db.DateTime, nullable=True)
    last_used_ip = db.Column(db.String(64), nullable=True)
    revoked_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    def is_active(self, now=None):
        now = now or datetime.utcnow()
        return (
            self.status == "active"
            and (self.expires_at is None or self.expires_at > now)
        )

    def to_dict(self):
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "app_id": self.app_id,
            "name": self.name,
            "scopes": list(self.scopes or []),
            "status": self.status,
            "created_by_user_id": self.created_by_user_id,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "last_used_at": self.last_used_at.isoformat() if self.last_used_at else None,
            "last_used_ip": self.last_used_ip,
            "revoked_at": self.revoked_at.isoformat() if self.revoked_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
