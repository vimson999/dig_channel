"""Authentication for the machine-to-machine ZSXQ history endpoint.

The caller identity and signing secret are provisioned per tenant and stored
in ``zsxq_history_api_clients``. No app credential is read from environment
variables.
"""

from __future__ import annotations

import hashlib
import hmac
import time

from flask import g, request

from services.zsxq_history_api_client_service import HISTORY_SCOPE, resolve_history_api_client


HISTORY_ENDPOINT_PATH = "/api/v1/zsxq/browser-import/history"
MAX_HEADER_VALUE_LENGTH = 512
HISTORY_TIMESTAMP_TOLERANCE_SECONDS = 300
_CLIENT_CACHE_KEY = "_zsxq_history_api_client"
_CLIENT_VALID_KEY = "_zsxq_history_api_client_valid"


def _bounded_header(*names: str) -> str:
    values = [str(request.headers.get(name) or "").strip() for name in names]
    values = [value for value in values if value]
    if not values or len(values[0]) > MAX_HEADER_VALUE_LENGTH:
        return ""
    # X-API-Key remains a compatibility alias for X-App-Id, but conflicting
    # aliases are rejected.
    if len(set(values)) != 1:
        return ""
    return values[0]


def authenticated_service_client():
    """Return the validated DB client for this request, or ``None``."""

    if getattr(g, _CLIENT_VALID_KEY, False):
        return getattr(g, _CLIENT_CACHE_KEY, None)
    setattr(g, _CLIENT_VALID_KEY, True)

    if request.method.upper() != "POST" or request.path != HISTORY_ENDPOINT_PATH:
        return None

    app_id = _bounded_header("X-App-Id", "X-API-Key")
    timestamp_text = _bounded_header("X-Timestamp")
    signature = _bounded_header("X-Signature").lower()
    if not app_id or not timestamp_text or not signature:
        return None

    try:
        timestamp = int(timestamp_text)
    except (TypeError, ValueError):
        return None
    if abs(time.time() - timestamp) > HISTORY_TIMESTAMP_TOLERANCE_SECONDS:
        return None

    try:
        resolved = resolve_history_api_client(app_id)
    except Exception:
        # An unavailable or not-yet-migrated credential table must fail closed
        # rather than turning the project-wide auth guard into a 500.
        try:
            from models import db
            db.session.rollback()
        except Exception:
            pass
        return None
    if not resolved:
        return None
    client, secret = resolved
    scopes = client.scopes if isinstance(client.scopes, list) else []
    if HISTORY_SCOPE not in {str(scope).strip() for scope in scopes}:
        return None

    body = request.get_data(cache=True)
    body_hash = hashlib.sha256(body).hexdigest()
    signing_string = "\n".join(
        [app_id, timestamp_text, request.method.upper(), request.path, body_hash]
    )
    expected_signature = hmac.new(
        secret.encode("utf-8"),
        signing_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(signature, expected_signature):
        return None

    setattr(g, _CLIENT_CACHE_KEY, client)
    return client


def is_valid_service_request() -> bool:
    return authenticated_service_client() is not None


__all__ = [
    "HISTORY_ENDPOINT_PATH",
    "HISTORY_TIMESTAMP_TOLERANCE_SECONDS",
    "authenticated_service_client",
    "is_valid_service_request",
]
