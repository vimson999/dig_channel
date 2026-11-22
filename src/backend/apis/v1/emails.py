"""Send an email using the project's configured SMTP account."""

import logging

from flask import Blueprint, jsonify, request

from services.runtime_guard import end_db_transaction_before_external_io
from services.workflow_notification_service import workflow_notification_service


bp = Blueprint("emails", __name__, url_prefix="/api/v1/emails")
logger = logging.getLogger(__name__)
RECIPIENT = "27518653@qq.com"


@bp.post("/send")
def send_email():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"success": False, "error": "请求体必须是 JSON 对象"}), 400

    subject = data.get("subject")
    body = data.get("body")
    if (not isinstance(subject, str) or not subject.strip() or len(subject) > 200
            or "\r" in subject or "\n" in subject):
        return jsonify({"success": False, "error": "邮件主题长度必须为 1 到 200 个字符，且不能换行"}), 400
    if not isinstance(body, str) or not body.strip() or len(body) > 10000:
        return jsonify({"success": False, "error": "邮件正文长度必须为 1 到 10000 个字符"}), 400

    try:
        end_db_transaction_before_external_io("manual email SMTP send")
        result = workflow_notification_service.send_email_message(subject.strip(), body, to=RECIPIENT)
    except Exception:
        logger.exception("[EMAILS] send failed")
        return jsonify({"success": False, "error": "邮件发送失败"}), 502
    if not result.get("sent"):
        return jsonify({"success": False, "error": "邮件服务未启用或 SMTP 配置不完整"}), 503
    return jsonify({"success": True, "data": {"sent": True, "to": RECIPIENT}})
