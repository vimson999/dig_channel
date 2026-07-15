import base64
import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from cryptography.fernet import Fernet, InvalidToken
from flask import current_app
from sqlalchemy.orm import load_only

from models import db
from models.platform_credential import PlatformCredentialAccount


PLATFORM_DEFINITIONS = [
    {"id": "zsxq", "name": "知识星球", "credential_types": ["cookie_header", "cookie_json"]},
    {"id": "feishu", "name": "飞书", "credential_types": ["cookie_header", "cookie_json"]},
    {"id": "bilibili", "name": "B站", "credential_types": ["bilibili_auth", "cookie_header", "cookie_json"]},
    {"id": "douyin", "name": "抖音", "credential_types": ["cookie_header", "token_json"]},
    {"id": "wechat", "name": "微信", "credential_types": ["cookie_header", "token_json"]},
    {"id": "other", "name": "其他平台", "credential_types": ["cookie_header", "token_json", "api_key"]},
]

PURPOSE_DEFINITIONS = [
    {"id": "zsxq.sync", "name": "知识星球同步", "platform": "zsxq"},
    {"id": "zsxq.publish", "name": "知识星球发帖", "platform": "zsxq"},
    {"id": "zsxq.browser", "name": "知识星球浏览器自动化", "platform": "zsxq"},
    {"id": "feishu.fetch", "name": "飞书文档抓取", "platform": "feishu"},
    {"id": "feishu.browser", "name": "飞书浏览器自动化", "platform": "feishu"},
    {"id": "bilibili.upload", "name": "B站视频上传", "platform": "bilibili"},
    {"id": "bilibili.browser", "name": "B站浏览器自动化", "platform": "bilibili"},
    {"id": "douyin.publish", "name": "抖音发布", "platform": "douyin"},
    {"id": "wechat.publish", "name": "微信发布", "platform": "wechat"},
    {"id": "other.browser", "name": "其他浏览器自动化", "platform": "other"},
    {"id": "other.api", "name": "其他 API 调用", "platform": "other"},
]

VALID_STATUSES = {"active", "expired", "disabled", "deleted"}
VALID_PLATFORM_IDS = {item["id"] for item in PLATFORM_DEFINITIONS}
VALID_PURPOSE_IDS = {item["id"] for item in PURPOSE_DEFINITIONS}
CREDENTIAL_LAST_USED_TOUCH_INTERVAL_SECONDS = int(
    os.environ.get("PLATFORM_CREDENTIAL_LAST_USED_TOUCH_INTERVAL_SECONDS", "300") or 300
)
RUNTIME_CREDENTIAL_COLUMNS = (
    PlatformCredentialAccount.id,
    PlatformCredentialAccount.platform,
    PlatformCredentialAccount.name,
    PlatformCredentialAccount.credential_type,
    PlatformCredentialAccount.purposes,
    PlatformCredentialAccount.status,
    PlatformCredentialAccount.priority,
    PlatformCredentialAccount.owner_label,
    PlatformCredentialAccount.encrypted_payload,
    PlatformCredentialAccount.notes,
    PlatformCredentialAccount.last_used_at,
    PlatformCredentialAccount.extra,
    PlatformCredentialAccount.updated_at,
)
LIST_CREDENTIAL_COLUMNS = (
    PlatformCredentialAccount.id,
    PlatformCredentialAccount.platform,
    PlatformCredentialAccount.name,
    PlatformCredentialAccount.credential_type,
    PlatformCredentialAccount.purposes,
    PlatformCredentialAccount.status,
    PlatformCredentialAccount.priority,
    PlatformCredentialAccount.owner_label,
    PlatformCredentialAccount.payload_fingerprint,
    PlatformCredentialAccount.masked_summary,
    PlatformCredentialAccount.notes,
    PlatformCredentialAccount.last_validated_at,
    PlatformCredentialAccount.last_used_at,
    PlatformCredentialAccount.last_error,
    PlatformCredentialAccount.extra,
    PlatformCredentialAccount.created_at,
    PlatformCredentialAccount.updated_at,
)


class CredentialError(ValueError):
    pass


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _fernet() -> Fernet:
    explicit = (
        os.environ.get("PLATFORM_CREDENTIAL_ENCRYPTION_KEY")
        or os.environ.get("ZSXQ_COOKIE_ENCRYPTION_KEY")
        or ""
    ).strip()
    if explicit:
        return Fernet(explicit.encode("utf-8"))

    secret = str(current_app.config.get("SECRET_KEY") or "dev-secret-key-change-in-production")
    digest = hashlib.sha256(("platform-credentials:" + secret).encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_payload(payload: str) -> str:
    return _fernet().encrypt(payload.encode("utf-8")).decode("utf-8")


def decrypt_payload(encrypted_payload: str) -> str:
    try:
        return _fernet().decrypt((encrypted_payload or "").encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise CredentialError("凭据无法解密，请检查 PLATFORM_CREDENTIAL_ENCRYPTION_KEY 是否变化") from exc


def _runtime_credential_query():
    return PlatformCredentialAccount.query.options(load_only(*RUNTIME_CREDENTIAL_COLUMNS))


def _credential_has_purpose(account: PlatformCredentialAccount, purpose: Optional[str]) -> bool:
    purpose = str(purpose or "").strip()
    if not purpose:
        return True
    purposes = getattr(account, "purposes", None) or []
    if isinstance(purposes, str):
        parsed = _safe_json_loads(purposes)
        purposes = parsed if isinstance(parsed, list) else [purposes]
    if not isinstance(purposes, list):
        return False
    return purpose in {str(item).strip() for item in purposes}


def _filter_accounts_by_purpose(
    accounts: Iterable[PlatformCredentialAccount],
    purpose: Optional[str],
) -> List[PlatformCredentialAccount]:
    return [account for account in accounts if _credential_has_purpose(account, purpose)]


def _mark_credential_used_if_due(account: PlatformCredentialAccount, now: Optional[datetime] = None) -> bool:
    now = now or datetime.utcnow()
    last_used_at = getattr(account, "last_used_at", None)
    if CREDENTIAL_LAST_USED_TOUCH_INTERVAL_SECONDS <= 0:
        account.last_used_at = now
        return True
    if not last_used_at:
        account.last_used_at = now
        return True
    try:
        age_seconds = (now - last_used_at).total_seconds()
    except Exception:
        age_seconds = CREDENTIAL_LAST_USED_TOUCH_INTERVAL_SECONDS
    if age_seconds >= CREDENTIAL_LAST_USED_TOUCH_INTERVAL_SECONDS:
        account.last_used_at = now
        return True
    return False


def _mask_token(value: str, head: int = 6, tail: int = 6) -> str:
    token = (value or "").strip()
    if not token:
        return ""
    if len(token) <= head + tail + 3:
        return token[:2] + "..." if len(token) > 2 else "***"
    return f"{token[:head]}...{token[-tail:]}"


def _safe_json_loads(value: str) -> Optional[Any]:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return json.loads(value)
    except Exception:
        return None


def _cookie_pairs_from_json(value: Any) -> Dict[str, str]:
    pairs: Dict[str, str] = {}
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, (str, int, float)):
                pairs[str(key)] = str(item)
        if "cookies" in value:
            pairs.update(_cookie_pairs_from_json(value.get("cookies")))
    elif isinstance(value, list):
        for item in value:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            cookie_value = item.get("value")
            if name and cookie_value is not None:
                pairs[str(name)] = str(cookie_value)
    return pairs


def _cookie_pairs_from_header(value: str) -> Dict[str, str]:
    pairs: Dict[str, str] = {}
    lines = []
    for line in str(value or "").replace("\r", "\n").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("//") or stripped.startswith("#"):
            continue
        lines.append(stripped)
    text = re.sub(r"^\s*cookie\s*:\s*", "", "; ".join(lines), flags=re.I).strip()
    for part in text.split(";"):
        if "=" not in part:
            continue
        key, item_value = part.split("=", 1)
        key = key.strip()
        if re.search(r"[\s\r\n]", key):
            continue
        if key:
            pairs[key] = item_value.strip()
    return pairs


def _extract_cookie_pairs(payload: str) -> Dict[str, str]:
    parsed = _safe_json_loads(payload)
    if parsed is not None:
        pairs = _cookie_pairs_from_json(parsed)
        if pairs:
            return pairs
    return _cookie_pairs_from_header(payload)


def normalize_payload(payload: Any) -> str:
    if payload is None:
        return ""
    if isinstance(payload, (dict, list)):
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    text = str(payload).strip()
    parsed = _safe_json_loads(text)
    if parsed is not None:
        return json.dumps(parsed, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return text


def fingerprint_payload(platform: str, payload: str) -> str:
    material = f"{platform.strip().lower()}\n{normalize_payload(payload)}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def masked_summary(platform: str, payload: str) -> str:
    pairs = _extract_cookie_pairs(payload)
    platform = (platform or "").strip().lower()
    if platform == "zsxq":
        token = pairs.get("zsxq_access_token") or pairs.get("access_token")
        return f"zsxq_access_token: {_mask_token(token)}" if token else "知识星球 Cookie"

    if platform == "bilibili":
        parts = []
        for key in ["SESSDATA", "bili_jct", "DedeUserID", "buvid3"]:
            value = pairs.get(key) or pairs.get(key.lower())
            if value:
                parts.append(f"{key}: {_mask_token(value)}")
        return " · ".join(parts) if parts else "B站凭据"

    if platform == "feishu":
        parts = []
        for key in ["session", "passport_web_did", "MONITOR_WEB_ID", "tenant_key", "locale"]:
            value = pairs.get(key) or pairs.get(key.upper()) or pairs.get(key.lower())
            if value:
                parts.append(f"{key}: {_mask_token(value)}")
        return " · ".join(parts[:3]) if parts else "飞书 Cookie"

    if pairs:
        names = list(pairs.keys())[:3]
        return "Cookie: " + ", ".join(names)

    text = normalize_payload(payload)
    return _mask_token(text, 8, 8) if text else "-"


def _normalize_platform(platform: str) -> str:
    platform = (platform or "").strip().lower()
    if platform not in VALID_PLATFORM_IDS:
        raise CredentialError("platform 不合法")
    return platform


def _normalize_purposes(platform: str, raw: Any) -> List[str]:
    if isinstance(raw, str):
        raw_items = [item.strip() for item in raw.split(",") if item.strip()]
    elif isinstance(raw, list):
        raw_items = [str(item).strip() for item in raw if str(item).strip()]
    else:
        raw_items = []

    if not raw_items:
        return []
    invalid = [item for item in raw_items if item not in VALID_PURPOSE_IDS]
    if invalid:
        raise CredentialError(f"用途不合法: {', '.join(invalid)}")
    mismatched = [
        item for item in raw_items
        if next((p for p in PURPOSE_DEFINITIONS if p["id"] == item), {}).get("platform") != platform
    ]
    if mismatched:
        raise CredentialError(f"用途与平台不匹配: {', '.join(mismatched)}")
    return sorted(set(raw_items))


def _normalize_status(raw: str, default: str = "active") -> str:
    status = (raw or default).strip().lower()
    if status not in VALID_STATUSES:
        raise CredentialError("status 不合法")
    return status


def list_credentials(filters: Dict[str, Any]) -> Tuple[List[PlatformCredentialAccount], int]:
    page = max(int(filters.get("page") or 1), 1)
    per_page = min(max(int(filters.get("per_page") or 20), 1), 100)
    query = PlatformCredentialAccount.query

    platform = (filters.get("platform") or "").strip().lower()
    if platform:
        query = query.filter(PlatformCredentialAccount.platform == platform)

    status = (filters.get("status") or "").strip().lower()
    if status:
        query = query.filter(PlatformCredentialAccount.status == status)
    else:
        query = query.filter(PlatformCredentialAccount.status != "deleted")

    purpose = (filters.get("purpose") or "").strip()

    keyword = (filters.get("q") or "").strip()
    if keyword:
        like = f"%{keyword}%"
        query = query.filter(
            db.or_(
                PlatformCredentialAccount.name.ilike(like),
                PlatformCredentialAccount.owner_label.ilike(like),
                PlatformCredentialAccount.masked_summary.ilike(like),
                PlatformCredentialAccount.notes.ilike(like),
            )
        )

    ordered_query = query.order_by(
        PlatformCredentialAccount.platform.asc(),
        PlatformCredentialAccount.priority.asc(),
        PlatformCredentialAccount.updated_at.desc(),
    )
    if purpose:
        purpose_candidates = ordered_query.options(load_only(
            PlatformCredentialAccount.id,
            PlatformCredentialAccount.purposes,
        )).all()
        matched_ids = [
            account.id
            for account in purpose_candidates
            if _credential_has_purpose(account, purpose)
        ]
        total = len(matched_ids)
        start = (page - 1) * per_page
        selected_ids = matched_ids[start:start + per_page]
        if not selected_ids:
            return [], total
        items = query.options(load_only(*LIST_CREDENTIAL_COLUMNS)).filter(
            PlatformCredentialAccount.id.in_(selected_ids)
        ).all()
        items_by_id = {item.id: item for item in items}
        return [items_by_id[item_id] for item_id in selected_ids if item_id in items_by_id], total

    total = ordered_query.count()
    items = (
        ordered_query
        .options(load_only(*LIST_CREDENTIAL_COLUMNS))
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )
    return items, total


def save_credential(data: Dict[str, Any], account_id: Optional[str] = None) -> PlatformCredentialAccount:
    platform = _normalize_platform(data.get("platform"))
    purposes = _normalize_purposes(platform, data.get("purposes") or [])
    status = _normalize_status(data.get("status"), "active")
    name = (data.get("name") or "").strip()
    if not name:
        raise CredentialError("name 不能为空")

    payload_input = data.get("payload")
    payload = normalize_payload(payload_input)

    account = PlatformCredentialAccount.query.get(account_id) if account_id else None
    if account_id and not account:
        raise CredentialError("凭据不存在")

    if not payload and not account:
        raise CredentialError("payload 不能为空")

    if payload:
        fingerprint = fingerprint_payload(platform, payload)
        existing = PlatformCredentialAccount.query.filter_by(payload_fingerprint=fingerprint).first()
        if existing and (not account or existing.id != account.id):
            account = existing
        encrypted = encrypt_payload(payload)
        summary = masked_summary(platform, payload)
    else:
        fingerprint = account.payload_fingerprint
        encrypted = account.encrypted_payload
        summary = account.masked_summary

    if not account:
        account = PlatformCredentialAccount()
        db.session.add(account)

    account.platform = platform
    account.name = name
    account.credential_type = (data.get("credential_type") or "cookie_header").strip()
    account.purposes = purposes
    account.status = status
    account.priority = int(data.get("priority") if data.get("priority") is not None else account.priority or 100)
    account.owner_label = (data.get("owner_label") or "").strip() or None
    account.encrypted_payload = encrypted
    account.payload_fingerprint = fingerprint
    account.masked_summary = summary
    account.notes = (data.get("notes") or "").strip() or None
    account.extra = data.get("extra") if isinstance(data.get("extra"), dict) else (account.extra or {})
    account.updated_at = datetime.utcnow()
    db.session.commit()
    return account


def soft_delete_credential(account_id: str) -> PlatformCredentialAccount:
    account = PlatformCredentialAccount.query.get(account_id)
    if not account:
        raise CredentialError("凭据不存在")
    account.status = "deleted"
    account.updated_at = datetime.utcnow()
    db.session.commit()
    return account


def validate_credential(account_id: str) -> Dict[str, Any]:
    account = PlatformCredentialAccount.query.get(account_id)
    if not account:
        raise CredentialError("凭据不存在")
    payload = decrypt_payload(account.encrypted_payload)
    ok, message = validate_payload_structure(account.platform, account.credential_type, payload)
    account.last_validated_at = datetime.utcnow()
    account.last_error = None if ok else message
    if ok and account.status in {"expired", "disabled"}:
        account.status = "active"
    if not ok and account.status == "active":
        account.status = "expired"
    db.session.commit()
    return {"valid": ok, "message": message, "account": account.to_dict()}


def validate_payload_structure(platform: str, credential_type: str, payload: str) -> Tuple[bool, str]:
    pairs = _extract_cookie_pairs(payload)
    platform = (platform or "").strip().lower()
    if platform == "zsxq":
        if pairs.get("zsxq_access_token"):
            return True, "结构校验通过：检测到 zsxq_access_token"
        return False, "未检测到 zsxq_access_token"

    if platform == "bilibili":
        required = ["SESSDATA", "bili_jct"]
        missing = [key for key in required if not (pairs.get(key) or pairs.get(key.lower()))]
        if missing:
            return False, "缺少 B站必要字段: " + ", ".join(missing)
        return True, "结构校验通过：检测到 SESSDATA 和 bili_jct"

    if platform == "feishu":
        common_keys = {"session", "passport_web_did", "MONITOR_WEB_ID", "tenant_key", "locale"}
        detected = [key for key in pairs if key in common_keys or key.lower() in {item.lower() for item in common_keys}]
        if detected:
            return True, "结构校验通过：检测到飞书 Cookie 字段 " + ", ".join(detected[:4])
        if pairs:
            return True, "结构校验通过：检测到 Cookie 字段，可尝试访问飞书文档"
        return False, "未检测到 Cookie 字段"

    if normalize_payload(payload):
        return True, "结构校验通过：凭据内容非空"
    return False, "凭据内容为空"


def resolve_credential_payload(
    *,
    account_id: Optional[str] = None,
    platform: Optional[str] = None,
    purpose: Optional[str] = None,
) -> str:
    account, payload = resolve_credential_record(
        account_id=account_id,
        platform=platform,
        purpose=purpose,
    )
    return payload


def has_active_credential(*, platform: str, purpose: Optional[str] = None) -> bool:
    query = PlatformCredentialAccount.query.options(load_only(
        PlatformCredentialAccount.id,
        PlatformCredentialAccount.purposes,
    )).filter(
        PlatformCredentialAccount.status == "active",
        PlatformCredentialAccount.platform == platform,
    )
    if purpose:
        return any(_credential_has_purpose(account, purpose) for account in query.all())
    return db.session.query(query.exists()).scalar()


def resolve_credential_record(
    *,
    account_id: Optional[str] = None,
    platform: Optional[str] = None,
    purpose: Optional[str] = None,
) -> Tuple[PlatformCredentialAccount, str]:
    base_query = PlatformCredentialAccount.query.filter(PlatformCredentialAccount.status == "active")
    if account_id:
        base_query = base_query.filter(PlatformCredentialAccount.id == account_id)
    if platform:
        base_query = base_query.filter(PlatformCredentialAccount.platform == platform)

    ordered_query = base_query.order_by(
        PlatformCredentialAccount.priority.asc(),
        PlatformCredentialAccount.updated_at.desc(),
    )
    if account_id:
        account = base_query.options(load_only(*RUNTIME_CREDENTIAL_COLUMNS)).first()
        if account and not _credential_has_purpose(account, purpose):
            account = None
    elif purpose:
        purpose_candidates = ordered_query.options(load_only(
            PlatformCredentialAccount.id,
            PlatformCredentialAccount.purposes,
        )).all()
        matched_id = next(
            (candidate.id for candidate in purpose_candidates if _credential_has_purpose(candidate, purpose)),
            None,
        )
        account = (
            base_query.options(load_only(*RUNTIME_CREDENTIAL_COLUMNS))
            .filter(PlatformCredentialAccount.id == matched_id)
            .first()
            if matched_id else None
        )
    else:
        account = ordered_query.options(load_only(*RUNTIME_CREDENTIAL_COLUMNS)).first()
    if not account:
        raise CredentialError("没有可用的平台凭据")
    if _mark_credential_used_if_due(account):
        db.session.commit()
    return account, decrypt_payload(account.encrypted_payload)


def resolve_credential_records(
    *,
    platform: str,
    purpose: Optional[str] = None,
    account_id: Optional[str] = None,
) -> List[Tuple[PlatformCredentialAccount, str]]:
    query = _runtime_credential_query().filter(
        PlatformCredentialAccount.status == "active",
        PlatformCredentialAccount.platform == platform,
    )
    if account_id:
        query = query.filter(PlatformCredentialAccount.id == account_id)
    accounts = query.order_by(
        PlatformCredentialAccount.priority.asc(),
        PlatformCredentialAccount.updated_at.desc(),
    ).all()
    if purpose:
        accounts = _filter_accounts_by_purpose(accounts, purpose)
    if not accounts:
        raise CredentialError("没有可用的平台凭据")
    now = datetime.utcnow()
    touched = False
    for account in accounts:
        touched = _mark_credential_used_if_due(account, now) or touched
    if touched:
        db.session.commit()
    return [(account, decrypt_payload(account.encrypted_payload)) for account in accounts]


def mark_credential_success(account_id: Optional[str]) -> None:
    if not account_id:
        return
    account = PlatformCredentialAccount.query.get(account_id)
    if not account:
        return
    now = datetime.utcnow()
    account.last_used_at = now
    account.last_validated_at = now
    account.last_error = None
    if account.status == "expired":
        account.status = "active"
    account.updated_at = now
    db.session.commit()


def mark_credential_error(account_id: Optional[str], error: Any, *, expired: bool = False) -> None:
    if not account_id:
        return
    account = PlatformCredentialAccount.query.get(account_id)
    if not account:
        return
    now = datetime.utcnow()
    account.last_used_at = now
    account.last_error = str(error or "")[:1000]
    if expired:
        account.status = "expired"
        account.last_validated_at = now
    account.updated_at = now
    db.session.commit()


def is_auth_error(error: Any) -> bool:
    text = str(error or "").lower()
    if not text:
        return False
    auth_hints = [
        "401",
        "403",
        "unauthorized",
        "forbidden",
        "未登录",
        "登录",
        "认证",
        "权限",
        "cookie",
        "sessdata",
        "csrf",
    ]
    return any(hint in text for hint in auth_hints)


def resolve_zsxq_cookie(
    *,
    account_id: Optional[str] = None,
    purpose: str = "zsxq.sync",
    required: bool = True,
) -> Dict[str, Any]:
    try:
        account, payload = resolve_credential_record(
            account_id=account_id,
            platform="zsxq",
            purpose=purpose,
        )
    except CredentialError:
        if required:
            raise
        return {}

    from services.zsxq_service import zsxq_service

    cookie = zsxq_service.normalize_cookie(payload)
    return {
        "cookie": cookie,
        "credential_id": account.id,
        "credential_name": account.name,
        "credential_source": "db",
        "account": account,
    }


def resolve_zsxq_cookie_accounts(
    *,
    purpose: str = "zsxq.publish",
    group_id: Optional[str] = None,
    account_id: Optional[str] = None,
    required: bool = True,
) -> List[Dict[str, Any]]:
    from services.zsxq_service import zsxq_service

    output = []
    target_group_id = str(group_id or "").strip()
    query = _runtime_credential_query().filter(
        PlatformCredentialAccount.status == "active",
        PlatformCredentialAccount.platform == "zsxq",
    )
    if account_id:
        query = query.filter(PlatformCredentialAccount.id == account_id)
    accounts = query.order_by(
        PlatformCredentialAccount.priority.asc(),
        PlatformCredentialAccount.updated_at.desc(),
    ).all()
    if purpose:
        accounts = _filter_accounts_by_purpose(accounts, purpose)
    now = datetime.utcnow()
    touched = False
    for account in accounts:
        extra = account.extra if isinstance(account.extra, dict) else {}
        allowed_group_ids = extra.get("group_ids") or extra.get("allowed_group_ids") or []
        allowed_group_ids = [str(item).strip() for item in allowed_group_ids if str(item).strip()]
        if allowed_group_ids and target_group_id and target_group_id not in allowed_group_ids:
            continue
        payload = decrypt_payload(account.encrypted_payload)
        touched = _mark_credential_used_if_due(account, now) or touched
        output.append({
            "id": account.id,
            "name": account.owner_label or account.name,
            "cookie": zsxq_service.normalize_cookie(payload),
            "enabled": True,
            "notes": account.notes or "",
            "allowed_group_ids": allowed_group_ids,
            "credential_id": account.id,
            "credential_name": account.name,
            "credential_source": "db",
            "runtime_only": True,
            "db_backed": True,
        })
    if output and touched:
        db.session.commit()
    if required and not output:
        raise CredentialError("没有可用于当前星球的知识星球发帖凭据")
    return output


def resolve_bilibili_auth(
    *,
    account_id: Optional[str] = None,
    required: bool = True,
) -> Dict[str, Any]:
    try:
        account, payload = resolve_credential_record(
            account_id=account_id,
            platform="bilibili",
            purpose="bilibili.upload",
        )
    except CredentialError:
        if required:
            raise
        return {}

    pairs = _extract_cookie_pairs(payload)
    auth = {
        "sessdata": pairs.get("SESSDATA") or pairs.get("sessdata") or "",
        "bili_jct": pairs.get("bili_jct") or pairs.get("BILI_JCT") or "",
        "buvid3": pairs.get("buvid3") or pairs.get("BUVID3") or "",
        "dedeuserid": pairs.get("DedeUserID") or pairs.get("dedeuserid") or pairs.get("DEDEUSERID") or "",
        "credential_id": account.id,
        "credential_name": account.name,
        "credential_source": "db",
    }
    if required and (not auth["sessdata"] or not auth["bili_jct"]):
        raise CredentialError("B站凭据缺少 SESSDATA 或 bili_jct")
    return auth


def resolve_feishu_cookie(
    *,
    account_id: Optional[str] = None,
    purpose: str = "feishu.fetch",
    required: bool = True,
) -> Dict[str, Any]:
    try:
        account, payload = resolve_credential_record(
            account_id=account_id,
            platform="feishu",
            purpose=purpose,
        )
    except CredentialError:
        if required:
            raise
        return {}

    cookie = _cookie_header_from_payload(payload)
    if required and not cookie:
        raise CredentialError("飞书凭据里没有可用 Cookie")
    return {
        "cookie": cookie,
        "credential_id": account.id,
        "credential_name": account.name,
        "credential_source": "db",
        "account": account,
    }


def _cookie_header_from_payload(payload: str) -> str:
    pairs = _extract_cookie_pairs(payload)
    if pairs:
        return "; ".join(f"{key}={value}" for key, value in pairs.items() if key and value is not None)
    return re.sub(r"^\s*cookie\s*:\s*", "", normalize_payload(payload), flags=re.I).strip()


def _bilibili_payload_from_system_config() -> Dict[str, str]:
    from services.config_service import ConfigService

    return {
        "SESSDATA": ConfigService.get("sessdata", "bilibili_auth") or "",
        "bili_jct": ConfigService.get("bili_jct", "bilibili_auth") or "",
        "buvid3": ConfigService.get("buvid3", "bilibili_auth") or "",
        "DedeUserID": ConfigService.get("dedeuserid", "bilibili_auth") or "",
    }


def import_bilibili_system_config() -> PlatformCredentialAccount:
    payload = {key: value for key, value in _bilibili_payload_from_system_config().items() if value}
    if not payload.get("SESSDATA") or not payload.get("bili_jct"):
        raise CredentialError("当前 system_configs / 环境变量里没有完整 B站凭据")
    return save_credential({
        "platform": "bilibili",
        "name": "B站发布账号",
        "credential_type": "bilibili_auth",
        "purposes": ["bilibili.upload"],
        "status": "active",
        "payload": payload,
        "notes": "从 system_configs / 环境变量导入",
    })


def import_zsxq_auto_publish_runtime() -> List[PlatformCredentialAccount]:
    path = _project_root() / "runtime" / "zsxq_auto_publish_strategy.json"
    if not path.exists():
        raise CredentialError("没有找到 runtime/zsxq_auto_publish_strategy.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    accounts = data.get("cookie_accounts") or []
    imported: List[PlatformCredentialAccount] = []
    for index, item in enumerate(accounts, start=1):
        if not isinstance(item, dict) or not item.get("cookie"):
            continue
        imported.append(save_credential({
            "platform": "zsxq",
            "name": item.get("name") or f"知识星球发帖账号 {index}",
            "credential_type": "cookie_header",
            "purposes": ["zsxq.publish"],
            "status": "active" if item.get("enabled", True) else "disabled",
            "payload": item.get("cookie"),
            "owner_label": item.get("name") or None,
            "notes": "从知识星球自动发帖 runtime 账号池导入",
            "extra": {
                "group_ids": item.get("group_ids") or [],
                "source": "zsxq_auto_publish_runtime",
            },
        }))
    if not imported:
        raise CredentialError("runtime 账号池里没有可导入的 Cookie")
    return imported


def options_payload() -> Dict[str, Any]:
    return {
        "platforms": PLATFORM_DEFINITIONS,
        "purposes": PURPOSE_DEFINITIONS,
        "statuses": [
            {"id": "active", "name": "可用"},
            {"id": "expired", "name": "失效"},
            {"id": "disabled", "name": "禁用"},
        ],
    }
