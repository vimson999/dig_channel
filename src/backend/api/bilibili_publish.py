from flask import Blueprint, jsonify, request

from services.bilibili_publish import charging_options_payload, clean_upload_text, seasons_payload
from utils.auth_utils import require_login


bilibili_publish_bp = Blueprint("bilibili_publish", __name__, url_prefix="/api/bilibili-publish")


@bilibili_publish_bp.route("/charging-options", methods=["GET"])
@require_login
def charging_options(_current_user):
    credential_id = clean_upload_text(request.args.get("credential_id"), max_length=80)
    try:
        payload = charging_options_payload(credential_id)
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    except Exception as exc:
        return jsonify({"success": False, "message": str(exc)}), 502
    return jsonify({"success": True, "data": payload, **payload})


@bilibili_publish_bp.route("/seasons", methods=["GET"])
@require_login
def seasons(_current_user):
    credential_id = clean_upload_text(request.args.get("credential_id"), max_length=80)
    try:
        items = seasons_payload(credential_id)
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    except Exception as exc:
        return jsonify({"success": False, "message": str(exc)}), 502
    return jsonify({"success": True, "data": {"items": items}, "items": items})
