"""Admin API for provisioning tenant-scoped ZSXQ history callers."""

import logging

from flask import Blueprint, jsonify, request

from services.zsxq_history_api_client_service import (
    HistoryApiClientError,
    create_history_api_client,
    get_history_api_client,
    list_history_api_clients,
    revoke_history_api_client,
    rotate_history_api_client,
)
from utils.auth_utils import require_admin


logger = logging.getLogger(__name__)
bp = Blueprint(
    "zsxq_history_api_clients",
    __name__,
    url_prefix="/api/v1/zsxq/history-api-clients",
)


def _error(message, status=400):
    return jsonify({"success": False, "error": str(message)}), status


def _public_credentials(payload):
    payload = dict(payload or {})
    secret = payload.pop("secret", None)
    return {
        "client": payload,
        "credentials": {
            "app_id": payload.get("app_id"),
            "secret": secret,
        },
    }


@bp.route("", methods=["GET"])
@require_admin
def list_clients(_current_user):
    try:
        items, total = list_history_api_clients(
            tenant_id=request.args.get("tenant_id"),
            status=request.args.get("status"),
            page=request.args.get("page", 1),
            per_page=request.args.get("per_page", 50),
        )
        page = max(int(request.args.get("page") or 1), 1)
        per_page = min(max(int(request.args.get("per_page") or 50), 1), 100)
        return jsonify(
            {
                "success": True,
                "data": {
                    "items": items,
                    "total": total,
                    "page": page,
                    "per_page": per_page,
                },
            }
        )
    except HistoryApiClientError as exc:
        return _error(exc, 400)
    except Exception as exc:
        logger.exception("知识星球历史 API 调用方列表读取失败")
        return _error("读取失败", 500)


@bp.route("", methods=["POST"])
@require_admin
def create_client(current_user):
    data = request.get_json(silent=True) or {}
    try:
        result = create_history_api_client(
            tenant_id=data.get("tenant_id") or getattr(current_user, "id", None),
            name=data.get("name"),
            app_id=data.get("app_id"),
            scopes=data.get("scopes"),
            expires_at=data.get("expires_at"),
            created_by_user_id=getattr(current_user, "id", None),
        )
        return jsonify({"success": True, **_public_credentials(result)}), 201
    except HistoryApiClientError as exc:
        return _error(exc, 400)
    except Exception as exc:
        logger.exception("知识星球历史 API 调用方创建失败")
        return _error("创建失败", 500)


@bp.route("/<client_id>", methods=["GET"])
@require_admin
def get_client(_current_user, client_id):
    client = get_history_api_client(client_id)
    if not client or client.status == "deleted":
        return _error("调用方凭据不存在", 404)
    return jsonify({"success": True, "data": {"client": client.to_dict()}})


@bp.route("/<client_id>/rotate", methods=["POST"])
@require_admin
def rotate_client(_current_user, client_id):
    try:
        result = rotate_history_api_client(client_id)
        return jsonify({"success": True, **_public_credentials(result)})
    except HistoryApiClientError as exc:
        return _error(exc, 404)
    except Exception as exc:
        logger.exception("知识星球历史 API 调用方轮换失败")
        return _error("轮换失败", 500)


@bp.route("/<client_id>/revoke", methods=["POST"])
@require_admin
def revoke_client(_current_user, client_id):
    try:
        result = revoke_history_api_client(client_id)
        return jsonify({"success": True, "data": {"client": result}})
    except HistoryApiClientError as exc:
        return _error(exc, 404)
    except Exception as exc:
        logger.exception("知识星球历史 API 调用方吊销失败")
        return _error("吊销失败", 500)
