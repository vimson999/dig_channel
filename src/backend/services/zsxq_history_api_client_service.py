"""Provision and resolve database-backed ZSXQ history API clients."""

from datetime import datetime, timezone
import hashlib
import re
import secrets
from typing import Any, Dict, Optional, Tuple

from models import db
from models.zsxq_history_api_client import ZsxqHistoryApiClient
from services.platform_credential_service import decrypt_payload, encrypt_payload
from sqlalchemy.exc import IntegrityError


HISTORY_SCOPE = "zsxq.history.read"
VALID_STATUSES = {"active", "revoked", "expired", "deleted"}
_TENANT_RE = re.compile(r"^[A-Za-z0-9_.:@/-]{1,120}$")
_APP_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{16,120}$")


class HistoryApiClientError(ValueError):
    pass


def _normalize_tenant_id(value: Any) -> str:
    tenant_id = str(value or "").strip()
    if not tenant_id or not _TENANT_RE.fullmatch(tenant_id):
        raise HistoryApiClientError(
            "tenant_id 必须是 1 到 120 位字母、数字或 . _ : @ / - 字符"
        )
    return tenant_id


def _normalize_name(value: Any) -> str:
    name = str(value or "").strip()
    if not name or len(name) > 120:
        raise HistoryApiClientError("name 必须是 1 到 120 个字符")
    return name


def _normalize_scopes(value: Any):
    if value is None:
        return [HISTORY_SCOPE]
    if isinstance(value, str):
        scopes = [item.strip() for item in value.split(",") if item.strip()]
    elif isinstance(value, (list, tuple, set)):
        scopes = [str(item).strip() for item in value if str(item).strip()]
    else:
        raise HistoryApiClientError("scopes 必须是数组或逗号分隔字符串")
    scopes = sorted(set(scopes))
    if not scopes or any(scope != HISTORY_SCOPE for scope in scopes):
        raise HistoryApiClientError(f"当前只支持 {HISTORY_SCOPE} scope")
    return scopes


def _normalize_app_id(value: Any) -> str:
    app_id = str(value or "").strip()
    if not app_id:
        return ""
    if not _APP_ID_RE.fullmatch(app_id):
        raise HistoryApiClientError("app_id 必须是 16 到 120 位安全字符")
    return app_id


def _new_app_id() -> str:
    return "zsxq_" + secrets.token_urlsafe(20).replace("-", "_")


def _new_secret() -> str:
    return secrets.token_urlsafe(32)


def _secret_fingerprint(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _parse_expiry(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None
    try:
        parsed = (
            value
            if isinstance(value, datetime)
            else datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        )
    except (TypeError, ValueError) as exc:
        raise HistoryApiClientError("expires_at 必须是 ISO 8601 时间") from exc
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _result(account: ZsxqHistoryApiClient, secret: Optional[str] = None) -> Dict[str, Any]:
    payload = account.to_dict()
    if secret is not None:
        payload["secret"] = secret
    return payload


def create_history_api_client(
    *,
    tenant_id: Any,
    name: Any,
    app_id: Any = None,
    scopes: Any = None,
    expires_at: Any = None,
    created_by_user_id: Any = None,
) -> Dict[str, Any]:
    tenant = _normalize_tenant_id(tenant_id)
    display_name = _normalize_name(name)
    normalized_app_id = _normalize_app_id(app_id)
    if not normalized_app_id:
        normalized_app_id = _new_app_id()
    if ZsxqHistoryApiClient.query.filter_by(app_id=normalized_app_id).first():
        raise HistoryApiClientError("app_id 已存在")

    secret = _new_secret()
    account = ZsxqHistoryApiClient(
        tenant_id=tenant,
        app_id=normalized_app_id,
        name=display_name,
        encrypted_secret=encrypt_payload(secret),
        secret_fingerprint=_secret_fingerprint(secret),
        scopes=_normalize_scopes(scopes),
        status="active",
        created_by_user_id=created_by_user_id,
        expires_at=_parse_expiry(expires_at),
    )
    db.session.add(account)
    try:
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        raise HistoryApiClientError("app_id 已存在") from exc
    return _result(account, secret)


def list_history_api_clients(
    *,
    tenant_id: Optional[Any] = None,
    status: Optional[str] = None,
    page: int = 1,
    per_page: int = 50,
) -> Tuple[list, int]:
    page = max(int(page or 1), 1)
    per_page = min(max(int(per_page or 50), 1), 100)
    query = ZsxqHistoryApiClient.query
    if tenant_id not in (None, ""):
        query = query.filter(
            ZsxqHistoryApiClient.tenant_id == _normalize_tenant_id(tenant_id)
        )
    normalized_status = str(status or "").strip().lower()
    if normalized_status:
        if normalized_status not in VALID_STATUSES:
            raise HistoryApiClientError("status 不合法")
        query = query.filter(ZsxqHistoryApiClient.status == normalized_status)
    else:
        query = query.filter(ZsxqHistoryApiClient.status != "deleted")
    total = query.count()
    items = (
        query.order_by(ZsxqHistoryApiClient.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )
    return [_result(item) for item in items], total


def get_history_api_client(client_id: str) -> Optional[ZsxqHistoryApiClient]:
    return ZsxqHistoryApiClient.query.filter_by(id=str(client_id)).first()


def resolve_history_api_client(app_id: str) -> Optional[Tuple[ZsxqHistoryApiClient, str]]:
    app_id = str(app_id or "").strip()
    if not app_id:
        return None
    account = ZsxqHistoryApiClient.query.filter_by(
        app_id=app_id,
        status="active",
    ).first()
    if not account or not account.is_active():
        return None
    try:
        secret = decrypt_payload(account.encrypted_secret)
    except Exception:
        return None
    return account, secret


def rotate_history_api_client(client_id: str) -> Dict[str, Any]:
    account = get_history_api_client(client_id)
    if not account or account.status == "deleted":
        raise HistoryApiClientError("调用方凭据不存在")
    secret = _new_secret()
    account.encrypted_secret = encrypt_payload(secret)
    account.secret_fingerprint = _secret_fingerprint(secret)
    account.status = "active"
    account.revoked_at = None
    account.updated_at = datetime.utcnow()
    db.session.commit()
    return _result(account, secret)


def revoke_history_api_client(client_id: str) -> Dict[str, Any]:
    account = get_history_api_client(client_id)
    if not account or account.status == "deleted":
        raise HistoryApiClientError("调用方凭据不存在")
    account.status = "revoked"
    account.revoked_at = datetime.utcnow()
    account.updated_at = datetime.utcnow()
    db.session.commit()
    payload = _result(account)
    payload["active"] = False
    return payload


def touch_history_api_client(account: ZsxqHistoryApiClient, remote_addr: Optional[str] = None):
    account.last_used_at = datetime.utcnow()
    account.last_used_ip = str(remote_addr or "")[:64] or None
    account.updated_at = datetime.utcnow()
    db.session.commit()


__all__ = [
    "HISTORY_SCOPE",
    "HistoryApiClientError",
    "create_history_api_client",
    "get_history_api_client",
    "list_history_api_clients",
    "resolve_history_api_client",
    "revoke_history_api_client",
    "rotate_history_api_client",
    "touch_history_api_client",
]
