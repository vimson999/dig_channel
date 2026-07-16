"""Structured, secret-safe security audit events."""

import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone

from flask import current_app, g, has_request_context, request


logger = logging.getLogger('security')


def redacted_request_path(path=None):
    value = str(path if path is not None else (request.path if has_request_context() else ''))
    if value.startswith('/access/'):
        return '/access/<redacted>'
    return value


def account_fingerprint(value):
    normalized = str(value or '').strip().casefold()
    if not normalized:
        return 'missing'
    key = str(current_app.config.get('SECRET_KEY') or '').encode('utf-8')
    return hmac.new(key, normalized.encode('utf-8'), hashlib.sha256).hexdigest()[:20]


def log_security_event(event, result, *, user=None, account=None, reason=None, **fields):
    payload = {
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'event': str(event),
        'result': str(result),
    }
    if has_request_context():
        payload.update({
            'request_id': getattr(g, 'request_id', None),
            'client_ip': request.remote_addr,
            'proxy_peer': request.environ.get('REMOTE_ADDR'),
            'method': request.method,
            'path': redacted_request_path(),
            'user_agent_hash': hashlib.sha256(
                request.headers.get('User-Agent', '').encode('utf-8')
            ).hexdigest()[:16],
        })
    if user is not None:
        payload['user_id'] = getattr(user, 'id', None)
        payload['is_admin'] = bool(getattr(user, 'is_admin', False))
    if account is not None:
        payload['account_hash'] = account_fingerprint(account)
    if reason:
        payload['reason'] = str(reason)
    for key, value in fields.items():
        if value is not None and key not in {'password', 'token', 'cookie', 'authorization'}:
            payload[key] = value
    logger.info(json.dumps(payload, ensure_ascii=False, sort_keys=True))
