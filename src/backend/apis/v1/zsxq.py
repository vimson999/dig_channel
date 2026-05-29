import logging
import re
from urllib.parse import quote

from flask import Blueprint, Response, jsonify, request, stream_with_context

from services.zsxq_service import zsxq_service
from services.zsxq_sync_service import zsxq_sync_service
from utils.api_utils import handle_api_error


bp = Blueprint("zsxq", __name__, url_prefix="/api/v1/zsxq")
logger = logging.getLogger(__name__)


def _payload():
    data = request.get_json(silent=True) or {}
    cookie = (data.get("cookie") or "").strip()
    if not cookie:
        raise ValueError("请先填写知识星球 Cookie")
    return data, cookie


def _safe_error(error):
    message = str(error)
    if "Cookie" in message:
        return message
    return f"知识星球请求失败: {message}"


def _safe_filename(name):
    name = (name or "zsxq_file").strip()
    name = re.sub(r'[\\/:*?"<>|\r\n]+', "_", name)
    return name[:180] or "zsxq_file"


def _assert_upstream_success(result):
    if not isinstance(result, dict) or result.get("succeeded") is not False:
        return

    code = result.get("code") or "UNKNOWN"
    message = result.get("error") or result.get("message") or result.get("resp_data") or result
    raise ValueError(f"知识星球接口返回失败 code={code}: {message}")


@bp.route("/groups", methods=["POST"])
def groups():
    """Fetch joined ZSXQ groups with a caller-provided Cookie."""
    try:
        data, cookie = _payload()
        result = zsxq_service.get_groups(cookie, data.get("count", 50))
        _assert_upstream_success(result)
        return jsonify({"success": True, "data": result})
    except ValueError as e:
        return handle_api_error(400, str(e))
    except Exception as e:
        return handle_api_error(502, _safe_error(e))


@bp.route("/topics", methods=["POST"])
def topics():
    """Fetch latest topics from one ZSXQ group."""
    data = {}
    try:
        data, cookie = _payload()
        group_id = data.get("group_id")
        if not group_id:
            return handle_api_error(400, "group_id 不能为空")

        result = zsxq_service.get_topics(
            cookie=cookie,
            group_id=group_id,
            count=data.get("count", 20),
            scope=data.get("scope", "all"),
            end_time=data.get("end_time"),
            extra_params=data.get("extra_params") if isinstance(data.get("extra_params"), dict) else None,
        )
        _assert_upstream_success(result)
        return jsonify({"success": True, "data": result})
    except ValueError as e:
        logger.warning(
            "[ZSXQ_TOPICS] request rejected | group_id=%s | count=%s | scope=%s | has_end_time=%s | extra_keys=%s | error=%s",
            data.get("group_id"),
            data.get("count"),
            data.get("scope"),
            bool(data.get("end_time")),
            sorted((data.get("extra_params") or {}).keys()) if isinstance(data.get("extra_params"), dict) else [],
            str(e),
        )
        return handle_api_error(400, str(e))
    except Exception as e:
        return handle_api_error(502, _safe_error(e))


@bp.route("/menus", methods=["POST"])
def menus():
    """Fetch official ZSXQ group menus/tabs."""
    try:
        data, cookie = _payload()
        group_id = data.get("group_id")
        if not group_id:
            return handle_api_error(400, "group_id 不能为空")

        result = zsxq_service.get_menus(cookie, group_id)
        _assert_upstream_success(result)
        return jsonify({"success": True, "data": result})
    except ValueError as e:
        return handle_api_error(400, str(e))
    except Exception as e:
        return handle_api_error(502, _safe_error(e))


@bp.route("/topic", methods=["POST"])
def topic():
    """Fetch one topic detail."""
    try:
        data, cookie = _payload()
        topic_id = data.get("topic_id")
        if not topic_id:
            return handle_api_error(400, "topic_id 不能为空")

        result = zsxq_service.get_topic(cookie, topic_id)
        _assert_upstream_success(result)
        return jsonify({"success": True, "data": result})
    except ValueError as e:
        return handle_api_error(400, str(e))
    except Exception as e:
        return handle_api_error(502, _safe_error(e))


@bp.route("/download", methods=["POST"])
def download():
    """Proxy-download one attachment from the topic payload."""
    try:
        data, cookie = _payload()
        file_info = data.get("file")
        if not isinstance(file_info, dict):
            return handle_api_error(400, "file 参数不能为空")

        upstream = zsxq_service.fetch_topic_file(cookie, data.get("topic_id"), file_info)
        filename = _safe_filename(
            data.get("filename")
            or file_info.get("name")
            or file_info.get("title")
            or file_info.get("file_name")
        )
        try:
            zsxq_sync_service.mark_browser_download(
                group_id=data.get("group_id"),
                tab_id=data.get("tab_id"),
                topic_id=data.get("topic_id"),
                file_info=file_info,
                filename=filename,
            )
        except Exception as exc:
            logger.warning("[ZSXQ_DOWNLOAD] mark browser download failed: %s", exc)

        content_type = upstream.headers.get("Content-Type", "application/octet-stream")
        headers = {
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
        }
        content_length = upstream.headers.get("Content-Length")
        if content_length:
            headers["Content-Length"] = content_length

        return Response(
            stream_with_context(upstream.iter_content(chunk_size=1024 * 256)),
            content_type=content_type,
            headers=headers,
        )
    except ValueError as e:
        return handle_api_error(400, str(e))
    except Exception as e:
        return handle_api_error(502, _safe_error(e))


@bp.route("/publish", methods=["POST"])
def publish():
    """Upload files/images and publish one topic to the selected ZSXQ group."""
    try:
        cookie = (request.form.get("cookie") or "").strip()
        group_id = (request.form.get("group_id") or "").strip()
        text = request.form.get("text") or ""
        hashtag_id = (request.form.get("hashtag_id") or "").strip() or None
        hashtag_title = (request.form.get("hashtag_title") or "").strip() or None

        if not cookie:
            return handle_api_error(400, "请先填写知识星球 Cookie")
        if not group_id:
            return handle_api_error(400, "group_id 不能为空")

        uploaded_files = []
        for file_storage in request.files.getlist("files"):
            filename = _safe_filename(file_storage.filename)
            content = file_storage.read()
            if not content:
                continue
            uploaded_files.append(
                {
                    "filename": filename,
                    "content": content,
                    "content_type": file_storage.mimetype,
                }
            )

        result = zsxq_service.upload_and_publish_topic(
            cookie=cookie,
            group_id=group_id,
            text=text,
            uploaded_files=uploaded_files,
            hashtag_id=hashtag_id,
            hashtag_title=hashtag_title,
        )
        publish_result = result.get("publish") or {}
        _assert_upstream_success(publish_result)

        return jsonify({"success": True, "data": result})
    except ValueError as e:
        return handle_api_error(400, str(e))
    except Exception as e:
        return handle_api_error(502, _safe_error(e))


@bp.route("/sync", methods=["POST"])
def sync():
    """Manually sync selected ZSXQ tabs into local DB and data/zsxq."""
    try:
        data, cookie = _payload()
        group_id = data.get("group_id")
        if not group_id:
            return handle_api_error(400, "group_id 不能为空")

        result = zsxq_sync_service.sync_tabs(
            cookie=cookie,
            group_id=group_id,
            group_name=data.get("group_name"),
            tabs=data.get("tabs"),
            count=data.get("count", 20),
            max_pages=data.get("max_pages", 1),
            stop_on_existing=data.get("stop_on_existing", True),
            download_attachments=data.get("download_attachments", True),
            download_pdf_only=data.get("download_pdf_only", True),
            download_file_types=data.get("download_file_types"),
            mode=data.get("mode", "manual"),
        )
        return jsonify({"success": True, "data": result})
    except ValueError as e:
        return handle_api_error(400, str(e))
    except Exception as e:
        return handle_api_error(502, _safe_error(e))


@bp.route("/sync/runs", methods=["POST"])
def sync_runs():
    """List recent ZSXQ sync runs."""
    try:
        data = request.get_json(silent=True) or {}
        result = zsxq_sync_service.list_runs(
            group_id=data.get("group_id"),
            limit=data.get("limit", 20),
        )
        return jsonify({"success": True, "data": {"runs": result}})
    except Exception as e:
        return handle_api_error(502, _safe_error(e))


@bp.route("/attachments/status", methods=["POST"])
def attachment_status():
    """Batch-read local download status for visible topic attachments."""
    try:
        data = request.get_json(silent=True) or {}
        group_id = data.get("group_id")
        tab_id = data.get("tab_id")
        if not group_id:
            return handle_api_error(400, "group_id 不能为空")
        if not tab_id:
            return handle_api_error(400, "tab_id 不能为空")

        result = zsxq_sync_service.get_attachment_statuses(
            group_id=group_id,
            tab_id=tab_id,
            topic_ids=data.get("topic_ids") or [],
        )
        return jsonify({"success": True, "data": {"statuses": result}})
    except Exception as e:
        return handle_api_error(502, _safe_error(e))
