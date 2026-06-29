import logging
from pathlib import Path

from flask import Blueprint, jsonify, request, send_file

from services.external_document_service import ExternalDocumentError, external_document_service


logger = logging.getLogger(__name__)
bp = Blueprint("external_documents", __name__, url_prefix="/api/v1/external-documents")


def _json_error(message, status=400):
    return jsonify({"success": False, "message": message}), status


@bp.route("/scan", methods=["POST"])
def scan_external_documents():
    data = request.get_json(silent=True) or {}
    try:
        result = external_document_service.scan_existing_topics(
            group_id=data.get("group_id"),
            topic_id=data.get("topic_id"),
            limit=data.get("limit"),
        )
        return jsonify({"success": True, "data": result})
    except Exception as exc:
        logger.exception("外链扫描失败")
        return _json_error(f"外链扫描失败: {exc}", 500)


@bp.route("/backfill", methods=["POST"])
def backfill_external_documents():
    data = request.get_json(silent=True) or {}
    try:
        result = external_document_service.backfill(
            group_id=data.get("group_id"),
            scan_limit=data.get("scan_limit"),
            scan=not bool(data.get("skip_scan")),
            fetch=bool(data.get("fetch")),
            summarize=bool(data.get("summarize")),
            document_limit=data.get("document_limit"),
            credential_id=data.get("credential_id") or data.get("feishu_credential_id"),
        )
        return jsonify({"success": True, "data": result})
    except Exception as exc:
        logger.exception("外链回填失败")
        return _json_error(f"外链回填失败: {exc}", 500)


@bp.route("/<document_id>", methods=["GET"])
def get_external_document(document_id):
    try:
        document = external_document_service.get_document(document_id)
        return jsonify({"success": True, "data": {"document": document.detail_dict()}})
    except ExternalDocumentError as exc:
        return _json_error(str(exc), 404)
    except Exception as exc:
        logger.exception("读取外链文档失败")
        return _json_error(f"读取外链文档失败: {exc}", 500)


@bp.route("/<document_id>/fetch", methods=["POST"])
def fetch_external_document(document_id):
    data = request.get_json(silent=True) or {}
    try:
        document = external_document_service.fetch_document(
            document_id,
            credential_id=data.get("credential_id") or data.get("feishu_credential_id"),
            force=bool(data.get("force")),
        )
        return jsonify({"success": True, "message": "飞书文档已抓取", "data": {"document": document.detail_dict()}})
    except ExternalDocumentError as exc:
        return _json_error(str(exc), 400)
    except Exception as exc:
        logger.exception("抓取外链文档失败")
        return _json_error(f"抓取外链文档失败: {exc}", 502)


@bp.route("/<document_id>/summarize", methods=["POST"])
def summarize_external_document(document_id):
    data = request.get_json(silent=True) or {}
    try:
        document = external_document_service.summarize_document(document_id, force=bool(data.get("force")))
        return jsonify({"success": True, "message": "AI 总结已生成", "data": {"document": document.detail_dict()}})
    except ExternalDocumentError as exc:
        return _json_error(str(exc), 400)
    except Exception as exc:
        logger.exception("总结外链文档失败")
        return _json_error(f"总结外链文档失败: {exc}", 502)


@bp.route("/<document_id>/tts", methods=["POST"])
def submit_external_document_tts(document_id):
    data = request.get_json(silent=True) or {}
    try:
        document = external_document_service.submit_tts(document_id, force=bool(data.get("force")))
        return jsonify({"success": True, "message": "豆包浏览器 TTS 任务已提交", "data": {"document": document.detail_dict()}})
    except ExternalDocumentError as exc:
        return _json_error(str(exc), 400)
    except Exception as exc:
        logger.exception("提交外链文档 TTS 失败")
        return _json_error(f"提交外链文档 TTS 失败: {exc}", 502)


@bp.route("/<document_id>/tts/audio", methods=["GET"])
def external_document_tts_audio(document_id):
    try:
        document = external_document_service.get_document(document_id)
        path = Path(document.tts_audio_path or "")
        if not document.tts_audio_path or not path.exists() or not path.is_file():
            return _json_error("音频文件不存在或尚未生成", 404)
        return send_file(path, mimetype="audio/wav", as_attachment=True, download_name=path.name)
    except ExternalDocumentError as exc:
        return _json_error(str(exc), 404)
    except Exception as exc:
        logger.exception("下载外链文档音频失败")
        return _json_error(f"下载外链文档音频失败: {exc}", 500)
