"""Authentication helpers for the server-local contact message API."""

import hmac
import os

from flask import request


def _tcp_peer_address():
    """Return the socket peer saved before ProxyFix rewrites REMOTE_ADDR."""
    original = request.environ.get("werkzeug.proxy_fix.orig")
    if isinstance(original, dict):
        peer = str(original.get("REMOTE_ADDR") or "").strip()
        if peer:
            return peer
    return str(request.environ.get("REMOTE_ADDR") or "").strip()


def is_valid_contact_request():
    allowed_ips = {
        item.strip()
        for item in os.getenv("CONTACT_API_ALLOWED_IPS", "127.0.0.1,::1").split(",")
        if item.strip()
    }
    if _tcp_peer_address() not in allowed_ips:
        return False

    expected = os.getenv("CONTACT_API_KEY", "").strip()
    authorization = request.headers.get("Authorization", "")
    scheme, separator, supplied = authorization.partition(" ")
    if len(expected) < 32 or not separator or scheme.lower() != "bearer":
        return False
    return hmac.compare_digest(supplied.strip(), expected)
