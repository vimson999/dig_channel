from datetime import datetime
import uuid

from . import db


def _new_id():
    return str(uuid.uuid4())


class PlatformCredentialAccount(db.Model):
    """Encrypted platform credential account.

    Stores cookies/tokens for platforms such as ZSXQ and Bilibili. Plaintext
    payloads must never be serialized through this model.
    """

    __tablename__ = "platform_credential_accounts"

    id = db.Column(db.String(36), primary_key=True, default=_new_id)
    platform = db.Column(db.String(40), nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    credential_type = db.Column(db.String(40), nullable=False, default="cookie_header")
    purposes = db.Column(db.JSON, nullable=False, default=list)
    status = db.Column(db.String(24), nullable=False, default="active", index=True)
    priority = db.Column(db.Integer, nullable=False, default=100)
    owner_label = db.Column(db.String(120), nullable=True)
    encrypted_payload = db.Column(db.Text, nullable=False)
    payload_fingerprint = db.Column(db.String(64), nullable=False, unique=True, index=True)
    masked_summary = db.Column(db.String(255), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    last_validated_at = db.Column(db.DateTime, nullable=True)
    last_used_at = db.Column(db.DateTime, nullable=True)
    last_error = db.Column(db.Text, nullable=True)
    extra = db.Column(db.JSON, nullable=False, default=dict)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    def to_dict(self):
        return {
            "id": self.id,
            "platform": self.platform,
            "name": self.name,
            "credential_type": self.credential_type,
            "purposes": self.purposes or [],
            "status": self.status,
            "priority": self.priority,
            "owner_label": self.owner_label,
            "payload_fingerprint": self.payload_fingerprint,
            "masked_summary": self.masked_summary,
            "notes": self.notes,
            "last_validated_at": self.last_validated_at.isoformat() if self.last_validated_at else None,
            "last_used_at": self.last_used_at.isoformat() if self.last_used_at else None,
            "last_error": self.last_error,
            "extra": self.extra or {},
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class PlatformCredentialBinding(db.Model):
    """Maps credentials to runtime scopes or business resources."""

    __tablename__ = "platform_credential_bindings"

    id = db.Column(db.String(36), primary_key=True, default=_new_id)
    account_id = db.Column(
        db.String(36),
        db.ForeignKey("platform_credential_accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    scope = db.Column(db.String(80), nullable=False, index=True)
    resource_type = db.Column(db.String(50), nullable=True)
    resource_id = db.Column(db.String(120), nullable=True)
    status = db.Column(db.String(24), nullable=False, default="active", index=True)
    priority = db.Column(db.Integer, nullable=False, default=100)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    account = db.relationship("PlatformCredentialAccount", backref=db.backref("bindings", lazy=True))

    def to_dict(self):
        return {
            "id": self.id,
            "account_id": self.account_id,
            "scope": self.scope,
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "status": self.status,
            "priority": self.priority,
            "notes": self.notes,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
