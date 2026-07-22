import logging

from flask import Blueprint, jsonify, request

from models.platform_credential import PlatformCredentialAccount
from services.platform_credential_service import (
    CredentialError,
    import_bilibili_system_config,
    import_zsxq_auto_publish_runtime,
    list_credentials,
    options_payload,
    save_credential,
    soft_delete_credential,
    validate_credential,
)


logger = logging.getLogger(__name__)

bp = Blueprint("platform_credentials", __name__, url_prefix="/api/v1/platform-credentials")


def _json_error(message, status=400):
    return jsonify({"success": False, "message": message}), status


@bp.route("/options", methods=["GET"])
def options():
    return jsonify({"success": True, "data": options_payload()})


@bp.route("", methods=["GET"])
def list_platform_credentials():
    try:
        items, total = list_credentials(request.args.to_dict())
        page = max(int(request.args.get("page") or 1), 1)
        per_page = min(max(int(request.args.get("per_page") or 20), 1), 100)
        return jsonify({
            "success": True,
            "data": {
                "items": [item.to_dict() for item in items],
                "total": total,
                "page": page,
                "per_page": per_page,
                "pages": max((total + per_page - 1) // per_page, 1),
            },
        })
    except Exception as exc:
        logger.exception("平台凭据列表读取失败")
        return _json_error(f"读取失败: {exc}", 500)


@bp.route("", methods=["POST"])
def create_platform_credential():
    data = request.get_json(silent=True) or {}
    try:
        account = save_credential(data)
        return jsonify({"success": True, "message": "凭据已保存", "data": {"account": account.to_dict()}})
    except CredentialError as exc:
        return _json_error(str(exc), 400)
    except Exception as exc:
        logger.exception("平台凭据保存失败")
        return _json_error(f"保存失败: {exc}", 500)


@bp.route("/<account_id>", methods=["GET"])
def get_platform_credential(account_id):
    account = PlatformCredentialAccount.query.get(account_id)
    if not account or account.status == "deleted":
        return _json_error("凭据不存在", 404)
    return jsonify({"success": True, "data": {"account": account.to_dict()}})


@bp.route("/<account_id>", methods=["PUT"])
def update_platform_credential(account_id):
    data = request.get_json(silent=True) or {}
    try:
        account = save_credential(data, account_id=account_id)
        return jsonify({"success": True, "message": "凭据已更新", "data": {"account": account.to_dict()}})
    except CredentialError as exc:
        return _json_error(str(exc), 400)
    except Exception as exc:
        logger.exception("平台凭据更新失败")
        return _json_error(f"更新失败: {exc}", 500)


@bp.route("/<account_id>", methods=["DELETE"])
def delete_platform_credential(account_id):
    try:
        account = soft_delete_credential(account_id)
        return jsonify({"success": True, "message": "凭据已删除", "data": {"account": account.to_dict()}})
    except CredentialError as exc:
        return _json_error(str(exc), 404)
    except Exception as exc:
        logger.exception("平台凭据删除失败")
        return _json_error(f"删除失败: {exc}", 500)


@bp.route("/<account_id>/validate", methods=["POST"])
def validate_platform_credential(account_id):
    try:
        data = request.get_json(silent=True) or {}
        live = str(data.get("live") or request.args.get("live") or "").strip().lower() in {
            "1", "true", "yes", "on",
        }
        return jsonify({"success": True, "data": validate_credential(account_id, live=live)})
    except CredentialError as exc:
        return _json_error(str(exc), 400)
    except Exception as exc:
        logger.exception("平台凭据校验失败")
        return _json_error(f"校验失败: {exc}", 500)


@bp.route("/import/bilibili-system-config", methods=["POST"])
def import_bilibili_config():
    try:
        account = import_bilibili_system_config()
        return jsonify({
            "success": True,
            "message": "已从现有 B站配置导入凭据",
            "data": {"account": account.to_dict()},
        })
    except CredentialError as exc:
        return _json_error(str(exc), 400)
    except Exception as exc:
        logger.exception("B站配置导入失败")
        return _json_error(f"导入失败: {exc}", 500)


@bp.route("/import/zsxq-auto-publish-runtime", methods=["POST"])
def import_zsxq_runtime():
    try:
        accounts = import_zsxq_auto_publish_runtime()
        return jsonify({
            "success": True,
            "message": f"已导入 {len(accounts)} 个知识星球发帖凭据",
            "data": {"accounts": [item.to_dict() for item in accounts]},
        })
    except CredentialError as exc:
        return _json_error(str(exc), 400)
    except Exception as exc:
        logger.exception("知识星球 runtime 导入失败")
        return _json_error(f"导入失败: {exc}", 500)
