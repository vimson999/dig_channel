import json
import logging
import mimetypes
import os
import re
import time
import uuid
from copy import deepcopy
from datetime import datetime
from pathlib import Path

from flask import has_app_context

from services.zsxq_service import zsxq_service


logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
CONFIG_PATH = PROJECT_ROOT / "runtime" / "zsxq_auto_publish_strategy.json"
STATE_PATH = PROJECT_ROOT / "runtime" / "zsxq_auto_publish_state.json"
RUNS_PATH = PROJECT_ROOT / "runtime" / "zsxq_auto_publish_runs.json"
LOCK_PATH = PROJECT_ROOT / "runtime" / "zsxq_auto_publish.lock.json"
FAILURE_LOG_PATH = PROJECT_ROOT / "runtime" / "zsxq_auto_publish_failures.jsonl"

COOKIE_ENV_NAME = "ZSXQ_AUTO_PUBLISH_COOKIE"
COOKIE_OVERRIDE_ENV_NAME = "ZSXQ_AUTO_PUBLISH_COOKIE_OVERRIDE"

DEFAULT_INTERVAL_MINUTES = 30
DEFAULT_BATCH_SIZE = 4
DEFAULT_MAX_ROUNDS = 0
DEFAULT_UPLOAD_RETRIES = 2
DEFAULT_PUBLISH_RETRIES = 5
DEFAULT_PUBLISH_RETRY_DELAY_SECONDS = 60
DEFAULT_PUBLISH_VERIFY_ATTEMPTS = 6
DEFAULT_PUBLISH_VERIFY_DELAY_SECONDS = 30
DEFAULT_PUBLISH_VERIFY_CONFIRMATIONS = 2
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_RUNS_LIMIT = 20
MAX_HISTORY_RUNS = 200
MAX_COOKIE_ACCOUNTS = 20
PLATFORM_MAX_FILE_SIZE_MB = 50

PDF_EXTENSIONS = [".pdf"]
AUDIO_EXTENSIONS = [".mp3", ".m4a", ".wav", ".aac", ".flac", ".ogg"]
COOKIE_LIMIT_HINTS = [
    "达到上限",
    "次数上限",
    "今日上限",
    "今日限额",
    "文件数量已达今日限额",
    "操作太频繁",
    "频率限制",
    "稍后再试",
    "too many",
    "rate limit",
    "daily limit",
]


def _now_iso():
    return datetime.utcnow().isoformat() + "Z"


def _strip_wrapped_quotes(value):
    text = str(value or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        return text[1:-1].strip()
    return text


def _read_json(path, default):
    if not path.exists():
        return deepcopy(default)
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    return data if isinstance(data, type(default)) else deepcopy(default)


def _write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.chmod(tmp_path, 0o600)
    tmp_path.replace(path)
    os.chmod(path, 0o600)


def _append_jsonl(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(payload, ensure_ascii=False) + "\n"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line)
    os.chmod(path, 0o600)


def _bounded_int(value, default, lower=None, upper=None):
    try:
        number = int(str(value).strip())
    except Exception:
        number = default
    if lower is not None:
        number = max(lower, number)
    if upper is not None:
        number = min(upper, number)
    return number


def _first_config_value(primary, fallback, key):
    for source in (primary, fallback):
        if not isinstance(source, dict) or key not in source:
            continue
        value = source.get(key)
        if value is not None and str(value).strip() != "":
            return value
    return None


def _normalize_extensions(raw, file_type):
    if isinstance(raw, list):
        items = raw
    else:
        items = str(raw or "").replace("，", ",").split(",")
    output = []
    for item in items:
        ext = str(item or "").strip().lower()
        if not ext:
            continue
        if not ext.startswith("."):
            ext = f".{ext}"
        if ext not in output:
            output.append(ext)
    if output:
        return output
    return PDF_EXTENSIONS[:] if file_type == "pdf" else AUDIO_EXTENSIONS[:]


def _default_max_file_size_mb(file_type):
    return 50 if file_type == "audio" else 100


def _normalize_group_ids(raw):
    if isinstance(raw, list):
        items = raw
    else:
        items = str(raw or "").replace("，", ",").split(",")
    output = []
    for item in items:
        group_id = str(item or "").strip()
        if group_id and group_id not in output:
            output.append(group_id)
    return output


def _extract_cookie_token(cookie):
    value = str(cookie or "").strip()
    if not value:
        return ""
    if value.startswith("[") or value.startswith("{"):
        try:
            payload = json.loads(value)
            items = payload if isinstance(payload, list) else [payload]
            token_item = next((item for item in items if item.get("name") == "zsxq_access_token"), None)
            return str((token_item or {}).get("value") or "").strip()
        except Exception:
            return ""
    match = re.search(r"(?:^|;\s*)zsxq_access_token=([^;]+)", value)
    return str(match.group(1) or "").strip() if match else ""


def _mask_cookie_token(token):
    if not token:
        return "未检测到"
    return "****" if len(token) <= 14 else f"{token[:8]}...{token[-6:]}"


def _cookie_account_label(account):
    return str(account.get("name") or account.get("id") or "未命名账号").strip() or "未命名账号"


def _normalize_cookie_account(item, index, previous=None):
    if not isinstance(item, dict):
        raise ValueError(f"第 {index + 1} 个发帖账号格式不正确")

    previous = previous or {}
    account_id = str(item.get("id") or previous.get("id") or f"account_{uuid.uuid4().hex[:12]}").strip()
    if not account_id:
        raise ValueError(f"第 {index + 1} 个发帖账号缺少 id")

    cookie = str(item.get("cookie") or previous.get("cookie") or "").strip()
    enabled = bool(item.get("enabled", True))
    if enabled and not cookie:
        raise ValueError(f"发帖账号 {index + 1} 缺少 Cookie")

    name = str(item.get("name") or previous.get("name") or f"发布账号 {index + 1}").strip() or f"发布账号 {index + 1}"
    notes = str(item.get("notes") or previous.get("notes") or "").strip()
    allowed_group_ids = _normalize_group_ids(
        item.get("allowed_group_ids") if "allowed_group_ids" in item else previous.get("allowed_group_ids")
    )
    created_at = str(previous.get("created_at") or _now_iso())
    updated_at = _now_iso()

    return {
        "id": account_id,
        "name": name,
        "cookie": cookie,
        "enabled": enabled,
        "notes": notes,
        "allowed_group_ids": allowed_group_ids,
        "created_at": created_at,
        "updated_at": updated_at,
        "quota_hit_count": _bounded_int(item.get("quota_hit_count", previous.get("quota_hit_count", 0)), 0, lower=0, upper=1_000_000),
        "last_limit_hit_at": str(item.get("last_limit_hit_at") or previous.get("last_limit_hit_at") or "").strip(),
        "last_limit_error": str(item.get("last_limit_error") or previous.get("last_limit_error") or "").strip(),
        "last_success_at": str(item.get("last_success_at") or previous.get("last_success_at") or "").strip(),
        "last_used_at": str(item.get("last_used_at") or previous.get("last_used_at") or "").strip(),
    }


def _normalize_cookie_accounts(raw, previous_accounts=None):
    previous_map = {
        str(item.get("id")): item
        for item in (previous_accounts or [])
        if isinstance(item, dict) and item.get("id")
    }
    items = raw or []
    if not isinstance(items, list):
        raise ValueError("cookie_accounts 必须是数组")
    if len(items) > MAX_COOKIE_ACCOUNTS:
        raise ValueError(f"发帖账号数量不能超过 {MAX_COOKIE_ACCOUNTS} 个")

    output = []
    seen = set()
    for index, item in enumerate(items):
        previous = previous_map.get(str((item or {}).get("id") or ""))
        account = _normalize_cookie_account(item, index, previous=previous)
        if account["id"] in seen:
            raise ValueError(f"发帖账号 id 重复：{account['id']}")
        seen.add(account["id"])
        output.append(account)
    return output


def _eligible_cookie_accounts(config, group_id=None):
    target_group_id = str(group_id or config.get("group_id") or "").strip()
    output = []
    for item in config.get("cookie_accounts") or []:
        if not isinstance(item, dict):
            continue
        if not item.get("enabled", True):
            continue
        if not str(item.get("cookie") or "").strip():
            continue
        allowed_group_ids = _normalize_group_ids(item.get("allowed_group_ids"))
        if allowed_group_ids and target_group_id and target_group_id not in allowed_group_ids:
            continue
        output.append(item)
    return output


def _snapshot_cookie_account(account, include_secrets=False):
    token = _extract_cookie_token(account.get("cookie") or "")
    payload = {
        "id": account.get("id"),
        "name": account.get("name"),
        "enabled": bool(account.get("enabled", True)),
        "notes": account.get("notes") or "",
        "allowed_group_ids": list(account.get("allowed_group_ids") or []),
        "cookie_masked": _mask_cookie_token(token),
        "created_at": account.get("created_at") or "",
        "updated_at": account.get("updated_at") or "",
        "quota_hit_count": int(account.get("quota_hit_count") or 0),
        "last_limit_hit_at": account.get("last_limit_hit_at") or "",
        "last_limit_error": account.get("last_limit_error") or "",
        "last_success_at": account.get("last_success_at") or "",
        "last_used_at": account.get("last_used_at") or "",
    }
    if include_secrets:
        payload["cookie"] = account.get("cookie") or ""
    return payload


def _scheduler_interval_minutes(value):
    return _bounded_int(value, default=DEFAULT_INTERVAL_MINUTES, lower=1, upper=1440)


def _normalize_rule(item, index):
    if not isinstance(item, dict):
        raise ValueError(f"第 {index + 1} 个规则格式不正确")

    file_type = str(item.get("file_type") or "").strip().lower()
    if file_type not in {"pdf", "audio"}:
        raise ValueError(f"第 {index + 1} 个规则的 file_type 只能是 pdf 或 audio")

    enabled = bool(item.get("enabled", True))
    source_dir = str(item.get("source_dir") or "").strip()
    source_path = Path(source_dir).expanduser() if source_dir else None
    if enabled:
        if not source_dir:
            raise ValueError(f"{file_type.upper()} 规则缺少来源目录")
        if not source_path.exists() or not source_path.is_dir():
            raise ValueError(f"{file_type.upper()} 来源目录不存在：{source_dir}")

    label = str(item.get("label") or ("PDF 批次" if file_type == "pdf" else "音频批次")).strip()
    hashtag_id = str(item.get("hashtag_id") or "").strip()
    hashtag_title = str(item.get("hashtag_title") or "").strip()

    rule = {
        "id": str(item.get("id") or file_type).strip() or file_type,
        "label": label,
        "enabled": enabled,
        "file_type": file_type,
        "source_dir": source_dir,
        "recursive": bool(item.get("recursive", True)),
        "extensions": _normalize_extensions(item.get("extensions"), file_type),
        "batch_size": _bounded_int(item.get("batch_size"), default=DEFAULT_BATCH_SIZE, lower=1, upper=8),
        "body_prefix": str(item.get("body_prefix") or "").strip(),
        "include_filenames": bool(item.get("include_filenames", True)),
        "hashtag_id": hashtag_id,
        "hashtag_title": hashtag_title,
        "upload_retry_count": _bounded_int(item.get("upload_retry_count"), default=DEFAULT_UPLOAD_RETRIES, lower=0, upper=5),
        "publish_retry_count": _bounded_int(item.get("publish_retry_count"), default=DEFAULT_PUBLISH_RETRIES, lower=0, upper=20),
        "publish_retry_delay_seconds": _bounded_int(
            item.get("publish_retry_delay_seconds"),
            default=DEFAULT_PUBLISH_RETRY_DELAY_SECONDS,
            lower=0,
            upper=600,
        ),
        "publish_verify_attempts": _bounded_int(
            item.get("publish_verify_attempts"),
            default=DEFAULT_PUBLISH_VERIFY_ATTEMPTS,
            lower=1,
            upper=20,
        ),
        "publish_verify_delay_seconds": _bounded_int(
            item.get("publish_verify_delay_seconds"),
            default=DEFAULT_PUBLISH_VERIFY_DELAY_SECONDS,
            lower=0,
            upper=600,
        ),
        "publish_verify_confirmations": _bounded_int(
            item.get("publish_verify_confirmations"),
            default=DEFAULT_PUBLISH_VERIFY_CONFIRMATIONS,
            lower=1,
            upper=5,
        ),
        "max_run_attempts": _bounded_int(item.get("max_run_attempts"), default=DEFAULT_MAX_ATTEMPTS, lower=1, upper=10),
        "max_file_size_mb": min(
            _bounded_int(item.get("max_file_size_mb"), default=_default_max_file_size_mb(file_type), lower=1, upper=1024),
            PLATFORM_MAX_FILE_SIZE_MB,
        ),
        "sort_mode": "mtime_asc",
    }
    return rule


def _normalize_config(payload, previous=None):
    if not isinstance(payload, dict):
        raise ValueError("配置格式不正确")

    previous = previous or {}
    group_id = str(payload.get("group_id") or "").strip()
    if not group_id:
        raise ValueError("group_id 不能为空")

    rules = [_normalize_rule(item, index) for index, item in enumerate(payload.get("rules") or [])]
    if not rules:
        raise ValueError("至少需要一条自动发帖规则")
    if not any(rule.get("enabled") for rule in rules):
        raise ValueError("至少需要启用一条自动发帖规则")

    now = _now_iso()
    incoming_cookie_accounts = (
        payload.get("cookie_accounts")
        if "cookie_accounts" in payload
        else previous.get("cookie_accounts")
    )
    if (
        "cookie_accounts" in payload
        and not payload.get("cookie_accounts")
        and previous.get("cookie_accounts")
    ):
        incoming_cookie_accounts = previous.get("cookie_accounts")
    cookie_accounts = _normalize_cookie_accounts(
        incoming_cookie_accounts,
        previous_accounts=previous.get("cookie_accounts") or [],
    )
    return {
        "name": str(payload.get("name") or "知识星球自动发帖策略").strip() or "知识星球自动发帖策略",
        "group_id": group_id,
        "group_name": str(payload.get("group_name") or "").strip(),
        "enabled": bool(payload.get("enabled", True)),
        "interval_minutes": _scheduler_interval_minutes(payload.get("interval_minutes")),
        "max_rounds": _bounded_int(payload.get("max_rounds"), default=DEFAULT_MAX_ROUNDS, lower=0, upper=1_000_000),
        "created_at": str(payload.get("created_at") or now),
        "updated_at": now,
        "rules": rules,
        "cookie_accounts": cookie_accounts,
    }


def _snapshot_rule(rule):
    return {
        "id": rule.get("id"),
        "label": rule.get("label"),
        "enabled": bool(rule.get("enabled")),
        "file_type": rule.get("file_type"),
        "source_dir": rule.get("source_dir"),
        "recursive": bool(rule.get("recursive")),
        "extensions": list(rule.get("extensions") or []),
        "batch_size": rule.get("batch_size"),
        "body_prefix": rule.get("body_prefix") or "",
        "include_filenames": bool(rule.get("include_filenames", True)),
        "hashtag_id": rule.get("hashtag_id") or "",
        "hashtag_title": rule.get("hashtag_title") or "",
        "upload_retry_count": rule.get("upload_retry_count"),
        "publish_retry_count": rule.get("publish_retry_count"),
        "publish_retry_delay_seconds": rule.get("publish_retry_delay_seconds"),
        "publish_verify_attempts": rule.get("publish_verify_attempts"),
        "publish_verify_delay_seconds": rule.get("publish_verify_delay_seconds"),
        "publish_verify_confirmations": rule.get("publish_verify_confirmations"),
        "max_run_attempts": rule.get("max_run_attempts"),
        "max_file_size_mb": rule.get("max_file_size_mb"),
    }


def _snapshot_config(config, include_secrets=False):
    db_cookie_account_count = _db_publish_cookie_account_count(
        group_id=(config or {}).get("group_id") if isinstance(config, dict) else None,
    )
    db_cookie_ready = db_cookie_account_count > 0
    if not config:
        return {
            "exists": False,
            "path": str(CONFIG_PATH),
            "cookie_env_name": COOKIE_ENV_NAME,
            "cookie_env_configured": bool(_strip_wrapped_quotes(os.environ.get(COOKIE_ENV_NAME))),
            "cookie_db_configured": db_cookie_ready,
            "cookie_accounts": [],
            "cookie_account_count": 0,
            "db_cookie_account_count": db_cookie_account_count,
            "active_cookie_account_count": db_cookie_account_count,
            "cookie_pool_ready": db_cookie_ready,
            "cookie_source_mode": "db" if db_cookie_ready else ("env" if bool(_strip_wrapped_quotes(os.environ.get(COOKIE_ENV_NAME))) else None),
        }
    active_cookie_accounts = _eligible_cookie_accounts(config, config.get("group_id"))
    effective_cookie_account_count = db_cookie_account_count or len(active_cookie_accounts)
    return {
        "exists": True,
        "path": str(CONFIG_PATH),
        "cookie_env_name": COOKIE_ENV_NAME,
        "cookie_env_configured": bool(_strip_wrapped_quotes(os.environ.get(COOKIE_ENV_NAME))),
        "cookie_db_configured": db_cookie_ready,
        "name": config.get("name"),
        "group_id": config.get("group_id"),
        "group_name": config.get("group_name"),
        "enabled": bool(config.get("enabled", True)),
        "interval_minutes": config.get("interval_minutes"),
        "max_rounds": config.get("max_rounds", 0),
        "continuous": int(config.get("max_rounds", 0) or 0) == 0,
        "updated_at": config.get("updated_at"),
        "rules": [_snapshot_rule(rule) for rule in (config.get("rules") or [])],
        "cookie_accounts": [_snapshot_cookie_account(item, include_secrets=include_secrets) for item in (config.get("cookie_accounts") or [])],
        "cookie_account_count": len(config.get("cookie_accounts") or []),
        "db_cookie_account_count": db_cookie_account_count,
        "active_cookie_account_count": effective_cookie_account_count,
        "cookie_pool_ready": bool(effective_cookie_account_count),
        "cookie_source_mode": "db" if db_cookie_ready else ("pool" if active_cookie_accounts else ("env" if bool(_strip_wrapped_quotes(os.environ.get(COOKIE_ENV_NAME))) else None)),
    }


def config_status(include_secrets=False):
    config = load_config(required=False)
    return _snapshot_config(config, include_secrets=include_secrets)


def load_config(required=True):
    if not CONFIG_PATH.exists():
        if required:
            raise FileNotFoundError(f"自动发帖配置不存在：{CONFIG_PATH}")
        return None
    config = _read_json(CONFIG_PATH, {})
    if not isinstance(config, dict):
        raise ValueError("自动发帖配置格式不正确")
    return config


def save_config(payload, cookie=None):
    previous = load_config(required=False) or {}
    config = _normalize_config(payload, previous=previous)
    db_accounts = _db_publish_cookie_accounts(group_id=config.get("group_id"), required=False)
    validation_cookie = (db_accounts[0]["cookie"] if db_accounts else None) or cookie or runtime_cookie(required=False)
    if validation_cookie:
        validation_cookie = zsxq_service.normalize_cookie(validation_cookie)
        _validate_group(config.get("group_id"), validation_cookie)
    config["created_at"] = previous.get("created_at") or config["created_at"]
    _write_json(CONFIG_PATH, config)
    return _snapshot_config(config, include_secrets=False)


def runtime_cookie(required=True):
    override = _strip_wrapped_quotes(os.environ.get(COOKIE_OVERRIDE_ENV_NAME) or "")
    if override:
        return zsxq_service.normalize_cookie(override)
    value = _strip_wrapped_quotes(os.environ.get(COOKIE_ENV_NAME) or "")
    if value:
        return zsxq_service.normalize_cookie(value)
    if required:
        raise ValueError(f"未配置环境变量 {COOKIE_ENV_NAME}")
    return None


def _persist_runtime_config(config):
    if not isinstance(config, dict):
        return
    _write_json(CONFIG_PATH, config)


def _runtime_cookie_account(cookie, source, group_id):
    return {
        "id": f"{source}_cookie",
        "name": "页面 Cookie" if source == "page" else "环境变量 Cookie",
        "cookie": zsxq_service.normalize_cookie(cookie),
        "enabled": True,
        "notes": "运行时注入，不写入策略文件",
        "allowed_group_ids": [str(group_id).strip()] if str(group_id or "").strip() else [],
        "runtime_only": True,
    }


def _resolve_runtime_cookie_accounts(config, cookie_override=None, required=True):
    db_accounts = _db_publish_cookie_accounts(group_id=config.get("group_id"), required=False)
    if db_accounts:
        return db_accounts, "db"

    pool_accounts = _eligible_cookie_accounts(config, config.get("group_id"))
    if pool_accounts:
        return pool_accounts, "pool"

    if cookie_override:
        return [_runtime_cookie_account(cookie_override, "page", config.get("group_id"))], "page"

    cookie = runtime_cookie(required=required)
    if cookie:
        return [_runtime_cookie_account(cookie, "env", config.get("group_id"))], "env"
    return [], None


def _db_publish_cookie_accounts(group_id=None, required=False):
    if not has_app_context():
        return []
    try:
        from services.platform_credential_service import resolve_zsxq_cookie_accounts

        return resolve_zsxq_cookie_accounts(
            purpose="zsxq.publish",
            group_id=group_id,
            required=required,
        )
    except Exception:
        if required:
            raise
        return []


def _db_publish_cookie_account_count(group_id=None):
    if not has_app_context():
        return 0
    try:
        from models import db
        from models.platform_credential import PlatformCredentialAccount

        query = PlatformCredentialAccount.query.filter(
            PlatformCredentialAccount.status == "active",
            PlatformCredentialAccount.platform == "zsxq",
            db.cast(PlatformCredentialAccount.purposes, db.String).ilike('%"zsxq.publish"%'),
        )
        accounts = query.all()
        target_group_id = str(group_id or "").strip()
        if not target_group_id:
            return len(accounts)
        count = 0
        for account in accounts:
            extra = account.extra if isinstance(account.extra, dict) else {}
            allowed = extra.get("group_ids") or extra.get("allowed_group_ids") or []
            allowed = [str(item).strip() for item in allowed if str(item).strip()]
            if not allowed or target_group_id in allowed:
                count += 1
        return count
    except Exception:
        return 0


def _append_publish_event(event_type, **payload):
    event = {
        "event_type": event_type,
        "created_at": _now_iso(),
    }
    event.update(payload)
    try:
        _append_jsonl(FAILURE_LOG_PATH, event)
    except Exception:
        logger.exception("[ZSXQ_AUTO_PUBLISH] append event failed | type=%s", event_type)
    logger.warning("[ZSXQ_AUTO_PUBLISH] event=%s", json.dumps(event, ensure_ascii=False, sort_keys=True))


def _quota_hint_from_error(error_text):
    normalized = str(error_text or "").strip().lower()
    if not normalized:
        return ""
    for hint in COOKIE_LIMIT_HINTS:
        if hint.lower() in normalized:
            return hint
    return ""


def _auth_hint_from_error(error_text):
    try:
        from services.platform_credential_service import is_auth_error

        if is_auth_error(error_text):
            return "认证失效"
    except Exception:
        logger.exception("[ZSXQ_AUTO_PUBLISH] auth error check failed")
    return ""


def _mark_cookie_account_used(account):
    if account.get("credential_id"):
        try:
            from services.platform_credential_service import mark_credential_success

            mark_credential_success(account.get("credential_id"))
        except Exception:
            logger.exception("[ZSXQ_AUTO_PUBLISH] credential used mark failed | credential=%s", account.get("credential_id"))
        return
    if account.get("runtime_only"):
        return
    account["last_used_at"] = _now_iso()
    account["updated_at"] = account["last_used_at"]


def _mark_cookie_account_success(account):
    if account.get("credential_id"):
        try:
            from services.platform_credential_service import mark_credential_success

            mark_credential_success(account.get("credential_id"))
        except Exception:
            logger.exception("[ZSXQ_AUTO_PUBLISH] credential success mark failed | credential=%s", account.get("credential_id"))
        return
    if account.get("runtime_only"):
        return
    now = _now_iso()
    account["last_used_at"] = now
    account["last_success_at"] = now
    account["updated_at"] = now


def _mark_cookie_account_limit(account, error_text):
    if account.get("credential_id"):
        try:
            from services.platform_credential_service import is_auth_error, mark_credential_error

            mark_credential_error(account.get("credential_id"), error_text, expired=is_auth_error(error_text))
        except Exception:
            logger.exception("[ZSXQ_AUTO_PUBLISH] credential error mark failed | credential=%s", account.get("credential_id"))
        return
    if account.get("runtime_only"):
        return
    now = _now_iso()
    account["last_used_at"] = now
    account["last_limit_hit_at"] = now
    account["last_limit_error"] = str(error_text or "").strip()[:1000]
    account["quota_hit_count"] = int(account.get("quota_hit_count") or 0) + 1
    account["updated_at"] = now


def _mark_cookie_account_error(account, error_text):
    if not account.get("credential_id"):
        return
    try:
        from services.platform_credential_service import is_auth_error, mark_credential_error

        mark_credential_error(account.get("credential_id"), error_text, expired=is_auth_error(error_text))
    except Exception:
        logger.exception("[ZSXQ_AUTO_PUBLISH] credential error mark failed | credential=%s", account.get("credential_id"))


def _validate_group(group_id, cookie):
    try:
        result = zsxq_service.get_groups(cookie, count=100)
    except Exception:
        return
    groups = ((result or {}).get("resp_data") or {}).get("groups") or []
    if not groups:
        return
    if any(str(item.get("group_id")) == str(group_id) for item in groups if isinstance(item, dict)):
        return
    raise ValueError(f"group_id 不在当前 Cookie 可访问的星球列表中：{group_id}")


def _load_state():
    data = _read_json(STATE_PATH, {"records": {}})
    if not isinstance(data, dict):
        return {"records": {}}
    data.setdefault("records", {})
    return data


def _save_state(state):
    _write_json(STATE_PATH, state)


def _load_runs():
    data = _read_json(RUNS_PATH, {"runs": []})
    if not isinstance(data, dict):
        return {"runs": []}
    runs = data.get("runs")
    if not isinstance(runs, list):
        data["runs"] = []
    return data


def _save_runs(data):
    runs = list(data.get("runs") or [])[:MAX_HISTORY_RUNS]
    _write_json(RUNS_PATH, {"runs": runs})


def list_runs(limit=DEFAULT_RUNS_LIMIT):
    runs = _load_runs().get("runs") or []
    return runs[:_bounded_int(limit, DEFAULT_RUNS_LIMIT, lower=1, upper=100)]


def _append_run(run):
    payload = _load_runs()
    runs = payload.get("runs") or []
    runs.insert(0, run)
    payload["runs"] = runs[:MAX_HISTORY_RUNS]
    _save_runs(payload)


def _replace_run(run_id, updated):
    payload = _load_runs()
    runs = payload.get("runs") or []
    replaced = False
    for index, item in enumerate(runs):
        if item.get("id") == run_id:
            runs[index] = updated
            replaced = True
            break
    if not replaced:
        runs.insert(0, updated)
    payload["runs"] = runs[:MAX_HISTORY_RUNS]
    _save_runs(payload)


def _is_pid_running(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except PermissionError:
        return True
    except (ProcessLookupError, ValueError, TypeError):
        return False
    except OSError:
        return False


def _acquire_lock(run_id, trigger):
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    if LOCK_PATH.exists():
        payload = _read_json(LOCK_PATH, {})
        pid = payload.get("pid")
        if pid and _is_pid_running(pid):
            return False, payload
        LOCK_PATH.unlink(missing_ok=True)
    payload = {
        "run_id": run_id,
        "trigger": trigger,
        "pid": os.getpid(),
        "locked_at": _now_iso(),
    }
    _write_json(LOCK_PATH, payload)
    return True, payload


def _release_lock(run_id):
    if not LOCK_PATH.exists():
        return
    payload = _read_json(LOCK_PATH, {})
    if payload.get("run_id") and payload.get("run_id") != run_id:
        return
    LOCK_PATH.unlink(missing_ok=True)


def _iter_rule_files(rule):
    base = Path(rule.get("source_dir") or "").expanduser()
    iterator = base.rglob("*") if rule.get("recursive", True) else base.glob("*")
    allowed = set(ext.lower() for ext in (rule.get("extensions") or []))
    for candidate in iterator:
        if not candidate.is_file():
            continue
        if candidate.name.startswith("."):
            continue
        if candidate.suffix.lower() not in allowed:
            continue
        yield candidate


def _file_identity(path):
    stat = path.stat()
    return f"{path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}"


def _scan_rule(rule, state):
    records = (state or {}).get("records") or {}
    eligible = []
    skipped = []
    now = _now_iso()
    for path in _iter_rule_files(rule):
        stat = path.stat()
        identity = _file_identity(path)
        record = records.get(identity) or {}
        size = int(stat.st_size)
        item = {
            "identity": identity,
            "path": str(path.resolve()),
            "name": path.name,
            "size": size,
            "size_mb": round(size / (1024 * 1024), 2),
            "mtime": datetime.utcfromtimestamp(stat.st_mtime).isoformat() + "Z",
            "attempts": int(record.get("attempts") or 0),
            "rule_id": rule.get("id"),
        }
        if size <= 0:
            item["reason"] = "empty"
            skipped.append(item)
            continue
        effective_max_mb = _effective_max_file_size_mb(rule)
        if size > effective_max_mb * 1024 * 1024:
            item["reason"] = "too_large_platform" if effective_max_mb == PLATFORM_MAX_FILE_SIZE_MB else "too_large"
            skipped.append(item)
            if record.get("status") != "published":
                records[identity] = {
                    "path": item["path"],
                    "name": item["name"],
                    "size": size,
                    "status": "skipped",
                    "skip_reason": item["reason"],
                    "max_file_size_mb": effective_max_mb,
                    "updated_at": now,
                    "attempts": item["attempts"],
                }
            continue
        if record.get("status") == "published":
            item["reason"] = "published"
            skipped.append(item)
            continue
        if record.get("status") == "pending_verify":
            item["reason"] = "pending_verify"
            skipped.append(item)
            continue
        if record.get("status") in {"failed", "publish_failed"} and int(record.get("attempts") or 0) >= int(rule.get("max_run_attempts") or DEFAULT_MAX_ATTEMPTS):
            item["reason"] = "max_attempts"
            skipped.append(item)
            continue
        eligible.append(item)

    eligible.sort(key=lambda item: (item.get("mtime") or "", item.get("name") or ""))
    return {
        "eligible": eligible,
        "skipped": skipped,
        "next_batch": eligible[: int(rule.get("batch_size") or DEFAULT_BATCH_SIZE)],
    }


def preview_payload(payload, cookie=None):
    config = _normalize_config(payload)
    if cookie:
        zsxq_service.normalize_cookie(cookie)
    state = _load_state()
    previews = []
    for rule in config.get("rules") or []:
        scan = _scan_rule(rule, state)
        skipped_counter = {}
        for item in scan["skipped"]:
            skipped_counter[item["reason"]] = skipped_counter.get(item["reason"], 0) + 1
        previews.append({
            "rule": _snapshot_rule(rule),
            "eligible_count": len(scan["eligible"]),
            "skipped_count": len(scan["skipped"]),
            "skipped_reasons": skipped_counter,
            "skipped": scan["skipped"][:100],
            "next_batch": scan["next_batch"],
        })
    return {
        "config": _snapshot_config(config),
        "preview": {
            "generated_at": _now_iso(),
            "rules": previews,
        },
    }


def _mark_record(state, item, status, **extra):
    records = state.setdefault("records", {})
    current = records.get(item["identity"]) or {}
    attempts = int(current.get("attempts") or 0)
    if status in {"failed", "publish_failed"}:
        attempts += 1
    elif status in {"published", "pending_verify"}:
        attempts = attempts + 1 if attempts else 1
    payload = {
        "path": item.get("path"),
        "name": item.get("name"),
        "size": item.get("size"),
        "status": status,
        "attempts": attempts,
        "updated_at": _now_iso(),
    }
    payload.update(extra)
    records[item["identity"]] = payload


def _effective_max_file_size_mb(rule):
    configured = int(rule.get("max_file_size_mb") or _default_max_file_size_mb(rule.get("file_type")))
    return max(1, min(configured, PLATFORM_MAX_FILE_SIZE_MB))


def _compose_topic_text(rule, file_names):
    parts = []
    prefix = str(rule.get("body_prefix") or "").strip()
    if prefix:
        parts.append(prefix)
    if rule.get("include_filenames", True) and file_names:
        parts.append("\n".join(file_names))
    return "\n".join(part for part in parts if part).strip()


def _assert_upstream_success(result):
    if not isinstance(result, dict) or result.get("succeeded") is not False:
        return
    code = result.get("code") or "UNKNOWN"
    message = result.get("error") or result.get("message") or result.get("resp_data") or result
    raise ValueError(f"知识星球接口返回失败 code={code}: {message}")


def _is_transient_publish_error(error_text):
    normalized = str(error_text or "").lower()
    return "code=1059" in normalized or "code 1059" in normalized or "1059" in normalized


def _publish_topic_with_retry(rule, cookie, group_id, topic_text, uploaded_file_ids, run_id, cookie_account):
    attempts = _bounded_int(rule.get("publish_retry_count"), DEFAULT_PUBLISH_RETRIES, lower=0, upper=20) + 1
    delay_seconds = _bounded_int(
        rule.get("publish_retry_delay_seconds"),
        DEFAULT_PUBLISH_RETRY_DELAY_SECONDS,
        lower=0,
        upper=600,
    )
    cookie_account_id = cookie_account.get("id") or ""
    cookie_account_name = _cookie_account_label(cookie_account)

    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            publish_result = zsxq_service.publish_topic(
                cookie,
                group_id,
                topic_text,
                file_ids=uploaded_file_ids,
                image_ids=[],
            )
            _assert_upstream_success(publish_result)
            if attempt > 1:
                logger.info(
                    "[ZSXQ_AUTO_PUBLISH] publish retry succeeded | run_id=%s rule=%s attempt=%s/%s",
                    run_id,
                    rule.get("id"),
                    attempt,
                    attempts,
                )
            return publish_result
        except Exception as exc:
            last_error = str(exc)
            if not _is_transient_publish_error(last_error) or attempt >= attempts:
                raise
            _append_publish_event(
                "publish_retry",
                run_id=run_id,
                rule_id=rule.get("id"),
                cookie_account_id=cookie_account_id,
                cookie_account_name=cookie_account_name,
                attempt=attempt,
                max_attempts=attempts,
                retry_after_seconds=delay_seconds,
                error=last_error,
            )
            logger.warning(
                "[ZSXQ_AUTO_PUBLISH] publish transient failure, retrying | run_id=%s rule=%s attempt=%s/%s delay=%ss error=%s",
                run_id,
                rule.get("id"),
                attempt,
                attempts,
                delay_seconds,
                last_error,
            )
            if delay_seconds > 0:
                time.sleep(delay_seconds)
    raise ValueError(last_error or "知识星球发帖失败")


def _verify_published_topic(cookie, topic_id, *, expected_file_count, run_id, rule, cookie_account):
    topic_id = str(topic_id or "").strip()
    if not topic_id:
        raise ValueError("知识星球发帖返回缺少 topic_id")

    rule = rule or {}
    cookie_account = cookie_account or {}
    attempts_value = _first_config_value(rule, cookie_account, "publish_verify_attempts")
    attempts = _bounded_int(
        attempts_value,
        DEFAULT_PUBLISH_VERIFY_ATTEMPTS,
        lower=1,
        upper=20,
    )
    delay_value = _first_config_value(rule, cookie_account, "publish_verify_delay_seconds")
    delay_seconds = _bounded_int(
        delay_value,
        DEFAULT_PUBLISH_VERIFY_DELAY_SECONDS,
        lower=0,
        upper=600,
    )
    confirmations_value = _first_config_value(rule, cookie_account, "publish_verify_confirmations")
    required_confirmations = _bounded_int(
        confirmations_value,
        DEFAULT_PUBLISH_VERIFY_CONFIRMATIONS,
        lower=1,
        upper=5,
    )
    required_confirmations = min(required_confirmations, attempts)
    last_error = None
    last_file_count = 0
    consecutive_confirmations = 0
    for attempt in range(1, attempts + 1):
        try:
            response = zsxq_service.get_topic(cookie, topic_id)
            if isinstance(response, dict) and response.get("succeeded") is False:
                last_error = f"code={response.get('code')}: {response.get('error') or response.get('message')}"
                consecutive_confirmations = 0
            else:
                topic = ((response or {}).get("resp_data") or {}).get("topic") if isinstance(response, dict) else None
                if topic:
                    file_count = _topic_file_count(topic)
                    last_file_count = file_count
                    if file_count >= int(expected_file_count or 0):
                        consecutive_confirmations += 1
                        if consecutive_confirmations >= required_confirmations:
                            return {
                                "topic_id": topic_id,
                                "status": "verified",
                                "confirmed": True,
                                "file_count": file_count,
                                "expected_file_count": int(expected_file_count or 0),
                                "attempts": attempt,
                                "confirmations": consecutive_confirmations,
                                "required_confirmations": required_confirmations,
                                "verified_at": _now_iso(),
                            }
                        last_error = (
                            f"等待连续回查确认，当前 {consecutive_confirmations}/"
                            f"{required_confirmations}，附件数 {file_count}/{expected_file_count}"
                        )
                    else:
                        consecutive_confirmations = 0
                        last_error = f"附件数量不匹配，期望 {expected_file_count}，实际 {file_count}"
                else:
                    consecutive_confirmations = 0
                    last_error = "接口返回中缺少 topic"
        except Exception as exc:
            last_error = str(exc)
            consecutive_confirmations = 0

        if attempt < attempts:
            time.sleep(delay_seconds)

    _append_publish_event(
        "publish_verify_pending",
        run_id=run_id,
        rule_id=(rule or {}).get("id"),
        cookie_account_id=cookie_account.get("id") or "",
        cookie_account_name=_cookie_account_label(cookie_account),
        topic_id=topic_id,
        expected_file_count=expected_file_count,
        attempts=attempts,
        retry_delay_seconds=delay_seconds,
        error=last_error,
    )
    return {
        "topic_id": topic_id,
        "status": "pending",
        "confirmed": False,
        "file_count": last_file_count,
        "expected_file_count": int(expected_file_count or 0),
        "attempts": attempts,
        "confirmations": consecutive_confirmations,
        "required_confirmations": required_confirmations,
        "checked_at": _now_iso(),
        "reason": "pending_review_or_indexing",
        "last_error": last_error,
    }


def _topic_file_count(topic):
    direct_files = []
    containers = [topic]
    if isinstance(topic, dict):
        containers.extend([
            topic.get("talk") or {},
            topic.get("article") or {},
        ])
    for container in containers:
        if not isinstance(container, dict):
            continue
        files = container.get("files")
        if isinstance(files, list):
            direct_files.extend(item for item in files if isinstance(item, dict))
    if direct_files:
        return len(direct_files)

    count = 0
    stack = [topic] if isinstance(topic, dict) else []
    seen = set()
    while stack:
        current = stack.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, dict):
            keys = {str(key).lower() for key in current.keys()}
            if (
                ({"file_id", "file_name"} & keys or {"file_id", "name"} <= keys or {"hash_id", "name"} <= keys)
                and ({"size", "download_count", "downloads_count"} & keys)
            ):
                count += 1
                continue
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)
    return count


def _commit_deferred_marks(state, deferred_marks):
    for entry in deferred_marks:
        _mark_record(state, entry["item"], entry["status"], **entry["extra"])


def _attempt_rule_batch_with_cookie(rule, config, cookie_account, state, run_id, batch):
    cookie = zsxq_service.normalize_cookie(cookie_account.get("cookie") or "")
    cookie_account_id = cookie_account.get("id") or ""
    cookie_account_name = _cookie_account_label(cookie_account)
    _mark_cookie_account_used(cookie_account)

    result = {
        "rule_id": rule.get("id"),
        "label": rule.get("label"),
        "file_type": rule.get("file_type"),
        "status": "failed",
        "message": "本批执行失败。",
        "batch_size": rule.get("batch_size"),
        "eligible_count": len(batch),
        "selected_count": len(batch),
        "uploaded_count": 0,
        "failed_count": 0,
        "topic_id": None,
        "files": [],
        "cookie_account_id": cookie_account_id,
        "cookie_account_name": cookie_account_name,
    }

    uploaded_assets = []
    uploaded_file_ids = []
    successful_names = []
    file_results = []
    deferred_marks = []

    for item in batch:
        upload_error = None
        max_upload_attempts = int(rule.get("upload_retry_count") or 0) + 1
        for attempt in range(1, max_upload_attempts + 1):
            try:
                content = Path(item["path"]).read_bytes()
                asset = zsxq_service.upload_topic_asset(
                    cookie,
                    item["name"],
                    content,
                    mimetypes.guess_type(item["name"])[0],
                )
                uploaded_assets.append({"item": item, "asset": asset})
                uploaded_file_ids.append(asset["id"])
                successful_names.append(item["name"])
                file_results.append({
                    "name": item["name"],
                    "path": item["path"],
                    "size_mb": item["size_mb"],
                    "status": "uploaded",
                    "attempt": attempt,
                })
                upload_error = None
                break
            except Exception as exc:
                upload_error = str(exc)
                quota_hint = _quota_hint_from_error(upload_error)
                auth_hint = _auth_hint_from_error(upload_error)
                logger.warning(
                    "[ZSXQ_AUTO_PUBLISH] upload failed | run_id=%s rule=%s cookie=%s file=%s attempt=%s error=%s",
                    run_id,
                    rule.get("id"),
                    cookie_account_name,
                    item["name"],
                    attempt,
                    upload_error,
                )
                _append_publish_event(
                    "upload_failure",
                    run_id=run_id,
                    rule_id=rule.get("id"),
                    cookie_account_id=cookie_account_id,
                    cookie_account_name=cookie_account_name,
                    file_name=item["name"],
                    file_path=item["path"],
                    attempt=attempt,
                    error=upload_error,
                    quota_hint=quota_hint or "",
                    auth_hint=auth_hint or "",
                )
                if auth_hint:
                    _mark_cookie_account_error(cookie_account, upload_error)
                    result["status"] = "retry_with_next_cookie"
                    result["message"] = f"账号 {cookie_account_name} 上传鉴权失败（{auth_hint}），准备整批切换下一个账号重试。"
                    result["auth_hint"] = auth_hint
                    result["upload_error"] = upload_error
                    result["files"] = file_results
                    return result
                if quota_hint:
                    _mark_cookie_account_limit(cookie_account, upload_error)
                    result["status"] = "retry_with_next_cookie"
                    result["message"] = f"账号 {cookie_account_name} 命中限制提示（{quota_hint}），准备整批切换下一个账号重试。"
                    result["quota_hint"] = quota_hint
                    result["files"] = file_results
                    return result
                if attempt < max_upload_attempts:
                    time.sleep(1.5)
        if upload_error:
            _mark_cookie_account_error(cookie_account, upload_error)
            deferred_marks.append({
                "item": item,
                "status": "failed",
                "extra": {
                    "rule_id": rule.get("id"),
                    "last_error": upload_error,
                },
            })
            file_results.append({
                "name": item["name"],
                "path": item["path"],
                "size_mb": item["size_mb"],
                "status": "failed",
                "error": upload_error,
            })

    result["files"] = file_results
    if not uploaded_file_ids:
        _commit_deferred_marks(state, deferred_marks)
        result["failed_count"] = sum(1 for item in file_results if item.get("status") == "failed")
        result["status"] = "failed"
        result["message"] = f"选中 {len(batch)} 个，上传成功 0 个，失败 {result['failed_count']} 个。"
        return result

    topic_text = _compose_topic_text(rule, successful_names)
    topic_text = zsxq_service.build_topic_text(
        topic_text,
        hashtag_id=rule.get("hashtag_id") or None,
        hashtag_title=rule.get("hashtag_title") or None,
    )
    try:
        publish_result = _publish_topic_with_retry(
            rule,
            cookie,
            config.get("group_id"),
            topic_text,
            uploaded_file_ids,
            run_id,
            cookie_account,
        )
        topic_id = (((publish_result.get("resp_data") or {}).get("topic") or {}).get("topic_id"))
        verification = _verify_published_topic(
            cookie,
            topic_id,
            expected_file_count=len(uploaded_assets),
            run_id=run_id,
            rule=rule,
            cookie_account=cookie_account,
        )
        _commit_deferred_marks(state, deferred_marks)
        verified_count = len(uploaded_assets) if verification.get("confirmed") else 0
        pending_verify_count = max(0, len(uploaded_assets) - verified_count)
        record_status = "published" if verification.get("confirmed") else "pending_verify"
        for pair in uploaded_assets:
            _mark_record(
                state,
                pair["item"],
                record_status,
                rule_id=rule.get("id"),
                topic_id=topic_id,
                group_id=config.get("group_id"),
                verification_status=verification.get("status"),
                verification_confirmed=bool(verification.get("confirmed")),
                verification_checked_at=verification.get("verified_at") or verification.get("checked_at"),
                verification_error=verification.get("last_error") or "",
            )
        _mark_cookie_account_success(cookie_account)
        success_count = len(uploaded_assets)
        fail_count = sum(1 for item in file_results if item.get("status") == "failed")
        result["uploaded_count"] = success_count
        result["submitted_count"] = success_count
        result["verified_count"] = verified_count
        result["pending_verify_count"] = pending_verify_count
        result["failed_count"] = fail_count
        result["topic_id"] = str(topic_id or "")
        result["verification"] = verification
        result["status"] = "partial" if (fail_count or pending_verify_count) else "success"
        if pending_verify_count:
            result["message"] = (
                f"选中 {len(batch)} 个，已提交发布 {success_count} 个，"
                f"回查确认 {verified_count} 个，待审核/待复核 {pending_verify_count} 个，失败 {fail_count} 个。"
            )
        else:
            result["message"] = (
                f"选中 {len(batch)} 个，已提交并回查确认 {success_count} 个，失败 {fail_count} 个。"
                if fail_count
                else f"已提交并回查确认 {success_count} 个附件。"
            )
        return result
    except Exception as exc:
        error_text = str(exc)
        quota_hint = _quota_hint_from_error(error_text)
        auth_hint = _auth_hint_from_error(error_text)
        if auth_hint:
            _mark_cookie_account_error(cookie_account, error_text)
            _append_publish_event(
                "publish_auth_switch",
                run_id=run_id,
                rule_id=rule.get("id"),
                cookie_account_id=cookie_account_id,
                cookie_account_name=cookie_account_name,
                file_names=[item.get("name") for item in batch],
                error=error_text,
                auth_hint=auth_hint,
            )
            result["status"] = "retry_with_next_cookie"
            result["message"] = f"账号 {cookie_account_name} 发布鉴权失败（{auth_hint}），准备整批切换下一个账号重试。"
            result["auth_hint"] = auth_hint
            result["publish_error"] = error_text
            return result
        if quota_hint:
            _mark_cookie_account_limit(cookie_account, error_text)
            _append_publish_event(
                "publish_limit_switch",
                run_id=run_id,
                rule_id=rule.get("id"),
                cookie_account_id=cookie_account_id,
                cookie_account_name=cookie_account_name,
                file_names=[item.get("name") for item in batch],
                error=error_text,
                quota_hint=quota_hint,
            )
            result["status"] = "retry_with_next_cookie"
            result["message"] = f"账号 {cookie_account_name} 发布时命中限制提示（{quota_hint}），准备整批切换下一个账号重试。"
            result["quota_hint"] = quota_hint
            return result

        _commit_deferred_marks(state, deferred_marks)
        for pair in uploaded_assets:
            _mark_record(
                state,
                pair["item"],
                "publish_failed",
                rule_id=rule.get("id"),
                last_error=error_text,
            )
        _append_publish_event(
            "publish_failure",
            run_id=run_id,
            rule_id=rule.get("id"),
            cookie_account_id=cookie_account_id,
            cookie_account_name=cookie_account_name,
            file_names=[pair["item"]["name"] for pair in uploaded_assets],
            error=error_text,
        )
        _mark_cookie_account_error(cookie_account, error_text)
        result["status"] = "failed"
        result["uploaded_count"] = 0
        result["failed_count"] = len(batch)
        result["message"] = f"选中 {len(batch)} 个，上传成功 {len(uploaded_assets)} 个，但发布失败：{error_text}"
        result["publish_error"] = error_text
        return result


def _run_rule_batch(rule, config, cookie_accounts, state, run_id):
    scan = _scan_rule(rule, state)
    batch = scan["next_batch"]
    result = {
        "rule_id": rule.get("id"),
        "label": rule.get("label"),
        "file_type": rule.get("file_type"),
        "status": "skipped",
        "message": "没有待处理文件。",
        "batch_size": rule.get("batch_size"),
        "eligible_count": len(scan["eligible"]),
        "selected_count": len(batch),
        "uploaded_count": 0,
        "failed_count": 0,
        "topic_id": None,
        "files": [],
        "cookie_attempts": [],
    }
    if not batch:
        return result

    if not cookie_accounts:
        result["status"] = "failed"
        result["message"] = "当前策略没有可用的发帖账号。"
        return result

    last_retry_result = None
    for index, cookie_account in enumerate(cookie_accounts, start=1):
        attempt_result = _attempt_rule_batch_with_cookie(rule, config, cookie_account, state, run_id, batch)
        attempt_result["eligible_count"] = len(scan["eligible"])
        attempt_result["selected_count"] = len(batch)
        result["cookie_attempts"].append({
            "index": index,
            "cookie_account_id": attempt_result.get("cookie_account_id") or cookie_account.get("id"),
            "cookie_account_name": attempt_result.get("cookie_account_name") or _cookie_account_label(cookie_account),
            "status": attempt_result.get("status"),
            "message": attempt_result.get("message"),
            "quota_hint": attempt_result.get("quota_hint") or "",
            "auth_hint": attempt_result.get("auth_hint") or "",
        })
        if attempt_result.get("status") == "retry_with_next_cookie":
            last_retry_result = attempt_result
            continue
        attempt_result["cookie_attempts"] = list(result["cookie_attempts"])
        return attempt_result

    result["status"] = "failed"
    issue_parts = []
    for attempt in result["cookie_attempts"]:
        name = attempt.get("cookie_account_name") or attempt.get("cookie_account_id") or f"账号{attempt.get('index') or ''}"
        if attempt.get("auth_hint"):
            issue_parts.append(f"{name}: 认证失败/401")
        elif attempt.get("quota_hint"):
            issue_parts.append(f"{name}: {attempt.get('quota_hint')}")
    result["message"] = (
        "当前账号池已全部触发限制或认证失败，本批未发布。"
        + (f"账号线索：{'；'.join(issue_parts[:6])}。" if issue_parts else "请根据 failure log 排查。")
    )
    if last_retry_result:
        result["files"] = last_retry_result.get("files") or []
        result["uploaded_count"] = last_retry_result.get("uploaded_count") or 0
        result["failed_count"] = last_retry_result.get("failed_count") or 0
    return result


def _publish_counts_from_rule_results(results):
    totals = {
        "selected_count": 0,
        "uploaded_count": 0,
        "submitted_count": 0,
        "verified_count": 0,
        "pending_verify_count": 0,
        "failed_count": 0,
        "topic_count": 0,
        "verified_topic_count": 0,
        "pending_verify_topic_count": 0,
    }
    for item in results or []:
        if not isinstance(item, dict):
            continue
        selected = int(item.get("selected_count") or 0)
        uploaded = int(item.get("uploaded_count") or 0)
        submitted = int(item.get("submitted_count") if item.get("submitted_count") is not None else uploaded)
        verified = int(item.get("verified_count") or 0)
        pending = int(item.get("pending_verify_count") or max(0, submitted - verified))
        failed = int(item.get("failed_count") or 0)
        totals["selected_count"] += selected
        totals["uploaded_count"] += uploaded
        totals["submitted_count"] += submitted
        totals["verified_count"] += verified
        totals["pending_verify_count"] += pending
        totals["failed_count"] += failed
        if item.get("topic_id"):
            totals["topic_count"] += 1
            verification = item.get("verification") if isinstance(item.get("verification"), dict) else {}
            if verification.get("confirmed"):
                totals["verified_topic_count"] += 1
            else:
                totals["pending_verify_topic_count"] += 1
    return totals


def _persist_cookie_account_runtime_state(runtime_config):
    """Persist cookie-account runtime counters without changing active rules."""
    if not isinstance(runtime_config, dict):
        return
    runtime_accounts = {
        str(item.get("id")): item
        for item in (runtime_config.get("cookie_accounts") or [])
        if isinstance(item, dict) and item.get("id")
    }
    if not runtime_accounts:
        return

    config = load_config(required=False)
    if not isinstance(config, dict):
        return

    changed = False
    runtime_fields = {
        "quota_hit_count",
        "last_limit_hit_at",
        "last_limit_error",
        "last_success_at",
        "last_used_at",
        "updated_at",
    }
    for account in config.get("cookie_accounts") or []:
        runtime_account = runtime_accounts.get(str(account.get("id") or ""))
        if not runtime_account:
            continue
        for field in runtime_fields:
            if account.get(field) != runtime_account.get(field):
                account[field] = runtime_account.get(field)
                changed = True

    if changed:
        _write_json(CONFIG_PATH, config)


def _run_once_for_config(
    config,
    *,
    trigger="manual",
    cookie_override=None,
    requested_by="api",
    persist_config=True,
):
    if not config:
        return {
            "queued": False,
            "skipped": True,
            "reason": "missing_config",
            "message": "未找到自动发帖配置，请先保存策略。",
        }
    if not config.get("enabled", True):
        return {
            "queued": False,
            "skipped": True,
            "reason": "disabled",
            "message": "当前自动发帖策略已停用。",
        }

    run_id = str(uuid.uuid4())
    locked, lock_payload = _acquire_lock(run_id, trigger)
    if not locked:
        return {
            "queued": False,
            "skipped": True,
            "reason": "busy",
            "message": "已有自动发帖任务正在执行，请稍后重试。",
            "lock": lock_payload,
        }

    run = {
        "id": run_id,
        "status": "running",
        "trigger": trigger,
        "requested_by": requested_by,
        "name": config.get("name"),
        "group_id": config.get("group_id"),
        "group_name": config.get("group_name"),
        "started_at": _now_iso(),
        "finished_at": None,
        "message": "任务执行中",
        "rules": [],
        "cookie_source": None,
        "cookie_account_count": 0,
    }
    _append_run(run)

    try:
        cookie_accounts, cookie_source = _resolve_runtime_cookie_accounts(config, cookie_override=cookie_override, required=True)
        run["cookie_source"] = cookie_source
        run["cookie_account_count"] = len(cookie_accounts)
        _replace_run(run_id, run)
        state = _load_state()
        results = []
        for rule in config.get("rules") or []:
            if not rule.get("enabled", True):
                continue
            logger.info(
                "[ZSXQ_AUTO_PUBLISH] run_rule_start | run_id=%s trigger=%s rule=%s dir=%s",
                run_id,
                trigger,
                rule.get("id"),
                rule.get("source_dir"),
            )
            results.append(_run_rule_batch(rule, config, cookie_accounts, state, run_id))
            _save_state(state)
            if persist_config:
                _persist_runtime_config(config)
            else:
                _persist_cookie_account_runtime_state(config)

        success_rules = sum(1 for item in results if item.get("status") == "success")
        partial_rules = sum(1 for item in results if item.get("status") == "partial")
        failed_rules = sum(1 for item in results if item.get("status") == "failed")
        if success_rules or partial_rules:
            run_status = "partial" if (partial_rules or failed_rules) else "success"
            message = f"本轮完成：成功 {success_rules}，部分成功 {partial_rules}，失败 {failed_rules}。"
        elif all(item.get("status") == "skipped" for item in results):
            run_status = "skipped"
            message = "本轮没有可发布的新文件。"
        else:
            run_status = "failed"
            message = f"本轮失败：失败规则 {failed_rules}。"
        run.update({
            "status": run_status,
            "message": message,
            "rules": results,
            "summary": _publish_counts_from_rule_results(results),
            "finished_at": _now_iso(),
            "cookie_source": cookie_source,
            "cookie_account_count": len(cookie_accounts),
        })
        _replace_run(run_id, run)
        summary = _publish_counts_from_rule_results(results)
        return {
            "queued": True,
            "run_id": run_id,
            "status": run_status,
            "message": message,
            "rules": results,
            "summary": summary,
            **summary,
            "cookie_source": cookie_source,
            "cookie_account_count": len(cookie_accounts),
        }
    except Exception as exc:
        logger.exception("[ZSXQ_AUTO_PUBLISH] run failed | run_id=%s trigger=%s", run_id, trigger)
        run.update({
            "status": "failed",
            "message": str(exc),
            "finished_at": _now_iso(),
        })
        _replace_run(run_id, run)
        return {
            "queued": False,
            "run_id": run_id,
            "status": "failed",
            "message": str(exc),
        }
    finally:
        _release_lock(run_id)


def run_configured_once(trigger="manual", cookie_override=None, requested_by="api"):
    config = load_config(required=False)
    return _run_once_for_config(
        config,
        trigger=trigger,
        cookie_override=cookie_override,
        requested_by=requested_by,
        persist_config=True,
    )


def run_once_with_config(payload, trigger="manual", cookie_override=None, requested_by="api"):
    """Run a one-off publish using a supplied config without rewriting rules."""
    previous = load_config(required=False) or {}
    config = _normalize_config(payload, previous=previous)
    return _run_once_for_config(
        config,
        trigger=trigger,
        cookie_override=cookie_override,
        requested_by=requested_by,
        persist_config=False,
    )
