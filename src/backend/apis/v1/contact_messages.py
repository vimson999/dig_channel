"""Server-local endpoint for enqueueing website contact messages."""

import os
import re

from flask import Blueprint, abort, jsonify, request

from services.workflow_notification_service import workflow_notification_service
from utils.contact_api_auth import is_valid_contact_request


bp = Blueprint("contact_messages", __name__, url_prefix="/api/v1/contact-messages")
_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_IDEMPOTENCY_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,200}$")


def _validation_error(field, message):
    return jsonify({
        "success": False,
        "error": {"field": field, "message": message},
    }), 400


@bp.post("")
def create_contact_message():
    if not is_valid_contact_request():
        abort(404)
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return _validation_error("body", "请求体必须是 JSON 对象")

    normalized = {
        "name": str(data.get("name") or "匿名访客").strip(),
        "email": str(data.get("email") or "").strip(),
        "message": str(data.get("message") or "").strip(),
        "source": str(data.get("source") or "未知来源").strip(),
        "idempotency_key": str(data.get("idempotency_key") or "").strip(),
    }
    if not _EMAIL_PATTERN.fullmatch(normalized["email"]) or len(normalized["email"]) > 254:
        return _validation_error("email", "请输入有效的访客邮箱")
    if not normalized["message"] or len(normalized["message"]) > 5000:
        return _validation_error("message", "留言内容长度必须为 1 到 5000 个字符")
    if len(normalized["name"]) > 100:
        return _validation_error("name", "访客姓名不能超过 100 个字符")
    if len(normalized["source"]) > 200:
        return _validation_error("source", "来源不能超过 200 个字符")
    if not _IDEMPOTENCY_PATTERN.fullmatch(normalized["idempotency_key"]):
        return _validation_error(
            "idempotency_key",
            "幂等键必须为 1 到 200 位字母、数字、点、下划线、冒号或连字符",
        )
    if not os.getenv("CONTACT_TO_EMAIL", "").strip():
        return jsonify({
            "success": False,
            "error": {"message": "留言收件邮箱尚未配置"},
        }), 503

    result = workflow_notification_service.enqueue_contact_message(normalized)
    return jsonify({
        "success": True,
        "data": {
            "queued": bool(result.get("queued")),
            "deduplicated": bool(result.get("deduplicated")),
            "delivery_id": result.get("delivery_id"),
        },
    }), 202
