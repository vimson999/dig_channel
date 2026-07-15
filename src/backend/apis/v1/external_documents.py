import logging
from datetime import datetime
from pathlib import Path

from flask import Blueprint, jsonify, request, send_file

from services.external_document_service import ExternalDocumentError, external_document_service
from models import db
from models.task import PublicationTask


logger = logging.getLogger(__name__)
bp = Blueprint("external_documents", __name__, url_prefix="/api/v1/external-documents")


def _json_error(message, status=400):
    return jsonify({"success": False, "message": message}), status


def _task_payload(task):
    return {
        "id": task.id,
        "status": task.status,
        "current_step": task.current_step,
        "progress": task.progress or 0,
        "result": task.result or {},
        "error_message": task.error_message,
        "celery_task_id": task.celery_task_id,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
    }


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
            providers=data.get("providers"),
        )
        return jsonify({"success": True, "data": result})
    except Exception as exc:
        logger.exception("外链回填失败")
        return _json_error(f"外链回填失败: {exc}", 500)


@bp.route("", methods=["GET"])
def list_external_documents():
    try:
        result = external_document_service.list_documents(
            provider=request.args.get("provider"),
            status=request.args.get("status"),
            keyword=request.args.get("q"),
            page=request.args.get("page", 1, type=int),
            per_page=request.args.get("per_page", 30, type=int),
        )
        return jsonify({"success": True, "data": result})
    except Exception as exc:
        logger.exception("读取关联内容列表失败")
        return _json_error(f"读取关联内容列表失败: {exc}", 500)


@bp.route("/backfill/tasks", methods=["POST"])
def submit_external_document_backfill():
    data = request.get_json(silent=True) or {}
    active = PublicationTask.query.filter(
        PublicationTask.feature == "external_document_backfill",
        PublicationTask.status.in_(["pending", "processing"]),
    ).order_by(PublicationTask.created_at.desc()).first()
    if active:
        return jsonify({"success": True, "message": "已有补抓任务运行中", "data": {"task": _task_payload(active)}})

    options = {
        "scan": data.get("scan", True),
        "fetch": data.get("fetch", True),
        "summarize": bool(data.get("summarize", False)),
        "providers": data.get("providers") or ["feishu", "zsxq", "wechat"],
        "group_id": data.get("group_id"),
        "scan_limit": data.get("scan_limit"),
        "document_limit": data.get("document_limit"),
        "credential_id": data.get("credential_id") or data.get("feishu_credential_id"),
    }
    task = PublicationTask(
        status="pending",
        current_step="queued",
        progress=0,
        params={
            "feature": "external_document_backfill",
            "source_feature": "linked_content_center",
            "external_document_backfill": options,
        },
        result={"processed": 0, "succeeded": 0, "failed": 0, "items": []},
    )
    db.session.add(task)
    db.session.flush()
    try:
        from tasks.external_documents import QUEUE_NAME, run_external_document_backfill

        queued = run_external_document_backfill.apply_async(args=[task.id], queue=QUEUE_NAME, retry=False)
        task.celery_task_id = queued.id
        db.session.commit()
    except Exception as exc:
        task.status = "failed"
        task.current_step = "failed"
        task.error_message = f"补抓任务入队失败: {exc}"
        task.completed_at = datetime.utcnow()
        db.session.commit()
        return _json_error(task.error_message, 503)
    return jsonify({"success": True, "message": "关联内容补抓任务已提交", "data": {"task": _task_payload(task)}}), 202


@bp.route("/backfill/tasks/<task_id>", methods=["GET"])
def get_external_document_backfill(task_id):
    task = PublicationTask.query.filter(
        PublicationTask.id == str(task_id),
        PublicationTask.feature == "external_document_backfill",
    ).first()
    if not task:
        return _json_error("补抓任务不存在", 404)
    return jsonify({"success": True, "data": {"task": _task_payload(task)}})


@bp.route("/<document_id>", methods=["GET"])
def get_external_document(document_id):
    try:
        document = external_document_service.get_document(document_id)
        return jsonify({"success": True, "data": {
            "document": document.detail_dict(),
            "references": external_document_service.document_references(document.id),
        }})
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
        return jsonify({"success": True, "message": "关联内容已抓取", "data": {"document": document.detail_dict()}})
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
