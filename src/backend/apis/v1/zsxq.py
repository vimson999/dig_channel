import logging
import os
import re
import time
from copy import deepcopy
from threading import Lock
from urllib.parse import quote

from flask import Blueprint, Response, jsonify, request, stream_with_context

from models import db
from services.zsxq_service import zsxq_service
from services.zsxq_auto_publish_service import (
    config_status as auto_publish_config_status,
    list_runs as auto_publish_list_runs,
    preview_payload as auto_publish_preview_payload,
    save_config as auto_publish_save_config,
)
from services.zsxq_auto_publish_scheduler import (
    restart_scheduler as restart_auto_publish_scheduler,
    scheduler_log_tail as auto_publish_scheduler_log_tail,
    scheduler_status as auto_publish_scheduler_status,
    start_scheduler as start_auto_publish_scheduler,
    stop_scheduler as stop_auto_publish_scheduler,
    trigger_once as trigger_once_auto_publish_scheduler,
)
from services.zsxq_system_sync_test import config_status, save_config
from services.zsxq_system_sync_scheduler import (
    restart_scheduler as restart_system_sync_scheduler,
    scheduler_log_tail,
    scheduler_status as system_sync_scheduler_status,
    start_scheduler as start_system_sync_scheduler,
    stop_scheduler as stop_system_sync_scheduler,
    trigger_once as trigger_once_system_sync_scheduler,
)
from services.zsxq_sync_service import zsxq_sync_service
from services.zsxq_workflow_service import zsxq_workflow_service
from services.workflow_notification_service import workflow_notification_service
from utils.api_utils import handle_api_error


bp = Blueprint("zsxq", __name__, url_prefix="/api/v1/zsxq")
logger = logging.getLogger(__name__)


def _float_env(name, default):
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return float(default)


def _int_value(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


SYSTEM_SYNC_BATCHES_CACHE_TTL_SECONDS = max(0.0, _float_env("ZSXQ_SYSTEM_SYNC_BATCHES_CACHE_TTL", 60))
_SYSTEM_SYNC_BATCHES_CACHE = {}
_SYSTEM_SYNC_BATCHES_CACHE_LOCK = Lock()


def _clear_system_sync_batches_cache():
    if not _SYSTEM_SYNC_BATCHES_CACHE:
        return
    with _SYSTEM_SYNC_BATCHES_CACHE_LOCK:
        _SYSTEM_SYNC_BATCHES_CACHE.clear()


def _payload(purpose="zsxq.sync", prefer_db=True):
    data = request.get_json(silent=True) or {}
    cookie = _resolve_request_cookie(data, purpose=purpose, prefer_db=prefer_db)
    return data, cookie


def _resolve_request_cookie(data, *, purpose="zsxq.sync", prefer_db=True, form=None):
    form = form or {}
    credential_id = (
        data.get("credential_id")
        or data.get("platform_credential_id")
        or form.get("credential_id")
        or form.get("platform_credential_id")
    )
    inline_cookie = ((data.get("cookie") if isinstance(data, dict) else None) or form.get("cookie") or "").strip()
    use_db = bool(prefer_db or credential_id or not inline_cookie)
    if use_db:
        try:
            from services.platform_credential_service import resolve_zsxq_cookie

            credential = resolve_zsxq_cookie(
                account_id=str(credential_id).strip() if credential_id else None,
                purpose=purpose,
                required=True,
            )
            data["_credential_id"] = credential.get("credential_id")
            data["_credential_source"] = credential.get("credential_source")
            return credential["cookie"]
        except Exception:
            # Sync and publish endpoints are DB-backed. An inline Cookie in a
            # stale page payload must never silently change which account a
            # task uses when the selected DB credential cannot be resolved.
            if prefer_db or credential_id or not inline_cookie:
                raise
            logger.warning("[ZSXQ] DB credential unavailable, falling back to inline cookie for purpose=%s", purpose)
    if not inline_cookie:
        raise ValueError("请先在平台凭据管理里保存知识星球 Cookie")
    return inline_cookie


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


@bp.route("/groups/local", methods=["GET"])
def local_groups():
    """Read cached ZSXQ groups from local sync tables without touching upstream."""
    try:
        result = zsxq_sync_service.list_cached_groups()
        return jsonify({"success": True, "data": result})
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


@bp.route("/topics/local", methods=["POST"])
def local_topics():
    """Read cached topics from local sync tables without touching ZSXQ upstream."""
    try:
        data = request.get_json(silent=True) or {}
        group_id = data.get("group_id")
        if not group_id:
            return handle_api_error(400, "group_id 不能为空")
        result = zsxq_sync_service.list_cached_topics(
            group_id=group_id,
            tab_id=data.get("tab_id"),
            count=data.get("count", 20),
            end_time=data.get("end_time"),
            publish_filter=data.get("publish_filter"),
            publish_date=data.get("publish_date"),
            publish_date_start=data.get("publish_date_start"),
            publish_date_end=data.get("publish_date_end"),
            keyword=data.get("keyword"),
            favorite_only=bool(data.get("favorite_only")),
            include_raw_payload=bool(data.get("include_raw_payload")),
        )
        return jsonify({"success": True, "data": result})
    except Exception as e:
        return handle_api_error(502, _safe_error(e))


@bp.route("/topics/local/favorite", methods=["POST"])
def local_topic_favorite():
    """Create or remove a database-backed topic favorite."""
    try:
        data = request.get_json(silent=True) or {}
        result = zsxq_sync_service.set_topic_favorite(
            group_id=data.get("group_id"),
            topic_id=data.get("topic_id"),
            favorite=data.get("favorite") is not False,
        )
        return jsonify({"success": True, "data": result})
    except ValueError as e:
        return handle_api_error(400, str(e))
    except Exception as e:
        db.session.rollback()
        return handle_api_error(502, _safe_error(e))


@bp.route("/topics/local/tabs", methods=["POST"])
def local_topic_tabs():
    """Read cached tab definitions from local topic tables."""
    try:
        data = request.get_json(silent=True) or {}
        group_id = data.get("group_id")
        if not group_id:
            return handle_api_error(400, "group_id 不能为空")
        result = zsxq_sync_service.list_cached_tabs(group_id=group_id)
        return jsonify({"success": True, "data": result})
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
    data = {}
    file_info = None
    filename = None
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
        if isinstance(file_info, dict):
            try:
                zsxq_sync_service.mark_browser_download_failed(
                    group_id=data.get("group_id"),
                    tab_id=data.get("tab_id"),
                    topic_id=data.get("topic_id"),
                    file_info=file_info,
                    filename=filename,
                    error_message=e,
                )
            except Exception as exc:
                logger.warning("[ZSXQ_DOWNLOAD] mark browser download failed status failed: %s", exc)
        return handle_api_error(502, _safe_error(e))


@bp.route("/publish", methods=["POST"])
def publish():
    """Upload files/images and publish one topic to the selected ZSXQ group."""
    try:
        cookie = _resolve_request_cookie({}, purpose="zsxq.publish", prefer_db=True, form=request.form)
        group_id = (request.form.get("group_id") or "").strip()
        text = request.form.get("text") or ""
        hashtag_id = (request.form.get("hashtag_id") or "").strip() or None
        hashtag_title = (request.form.get("hashtag_title") or "").strip() or None

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
    """Queue selected ZSXQ tabs into the dedicated Celery sync worker."""
    try:
        data, cookie = _payload(purpose="zsxq.sync", prefer_db=True)
        group_id = data.get("group_id")
        if not group_id:
            return handle_api_error(400, "group_id 不能为空")

        from tasks.zsxq_sync import run_zsxq_sync

        tabs = data.get("tabs")
        mode = data.get("mode", "manual")
        run = zsxq_sync_service.create_sync_run(
            group_id=group_id,
            group_name=data.get("group_name"),
            tabs=tabs,
            mode=mode,
        )
        payload = {
            "cookie": None if data.get("_credential_source") == "db" else cookie,
            "credential_id": data.get("_credential_id") or data.get("credential_id"),
            "group_id": group_id,
            "group_name": data.get("group_name"),
            "tabs": tabs,
            "count": data.get("count", 20),
            "max_pages": data.get("max_pages", 1),
            "stop_on_existing": data.get("stop_on_existing", True),
            "download_attachments": data.get("download_attachments", False),
            "download_pdf_only": data.get("download_pdf_only", True),
            "download_file_types": data.get("download_file_types"),
            "download_file_types_by_tab": data.get("download_file_types_by_tab"),
            "max_download_files": data.get("max_download_files"),
            "allow_attachment_downloads": data.get("allow_attachment_downloads", False),
            "attachment_download_rules": data.get("attachment_download_rules"),
            "mode": mode,
            "auto_pdf_clean": data.get("auto_pdf_clean", False),
            "pdf_cleaner": data.get("pdf_cleaner") or {},
            "auto_audio_clean": data.get("auto_audio_clean", False),
            "audio_cleaner": data.get("audio_cleaner") or {},
            "auto_publish_after_clean": data.get("auto_publish_after_clean") or {},
        }
        try:
            async_result = run_zsxq_sync.apply_async(
                args=[run["id"], payload],
                queue="zsxq_sync",
                retry=False,
            )
        except Exception as exc:
            logger.exception("[ZSXQ_SYNC] queue publish failed | run_id=%s", run["id"])
            zsxq_sync_service.mark_run_failed(
                run["id"],
                f"同步任务入队失败: {exc}",
                {"queued": False, "running": False, "queue_publish_failed": True},
            )
            return handle_api_error(502, f"知识星球同步任务入队失败：{exc}")
        result = {
            "queued": True,
            "run": run,
            "celery_task_id": async_result.id,
        }
        return jsonify({"success": True, "data": result})
    except ValueError as e:
        return handle_api_error(400, str(e))
    except Exception as e:
        logger.exception("[ZSXQ_SYNC] sync endpoint failed")
        return handle_api_error(502, f"知识星球同步接口异常：{e}")


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
            attachment_refs=data.get("attachments") or [],
        )
        return jsonify({"success": True, "data": {"statuses": result}})
    except Exception as e:
        return handle_api_error(502, _safe_error(e))


@bp.route("/attachments/publish-status/backfill", methods=["POST"])
def attachment_publish_status_backfill():
    """Backfill attachment publish status from existing clean/publish task history."""
    try:
        data = request.get_json(silent=True) or {}
        result = zsxq_sync_service.backfill_attachment_publish_statuses(
            limit=data.get("limit", 240),
        )
        return jsonify({"success": True, "data": result})
    except ValueError as e:
        return handle_api_error(400, str(e))
    except Exception as e:
        return handle_api_error(502, _safe_error(e))


@bp.route("/system-sync-test/config", methods=["GET", "POST"])
def system_sync_test_config():
    """Read or save the local system scheduled-sync test config."""
    if request.method == "GET":
        try:
            return jsonify({"success": True, "data": {"config": config_status()}})
        except Exception as e:
            return handle_api_error(502, _safe_error(e))

    try:
        data = request.get_json(silent=True) or {}
        cookie = (data.get("cookie") or "").strip()
        logger.info(
            "[ZSXQ_SYSTEM_SYNC_TEST] save config request | group=%s interval=%s count=%s max_rounds=%s max_files=%s jobs=%s",
            data.get("group_id"),
            data.get("interval_minutes"),
            data.get("count"),
            data.get("max_rounds"),
            data.get("max_download_files"),
            len(data.get("jobs") or []),
        )
        result = save_config(data, cookie)
        _clear_system_sync_batches_cache()
        return jsonify({"success": True, "data": {"config": result}})
    except ValueError as e:
        return handle_api_error(400, str(e))
    except Exception as e:
        logger.exception("[ZSXQ_SYSTEM_SYNC_TEST] save config failed")
        return handle_api_error(502, _safe_error(e))


@bp.route("/system-sync-test/batches", methods=["POST"])
def system_sync_test_batches():
    """List project-scheduler sync batches with per-run attachment details."""
    try:
        data = request.get_json(silent=True) or {}
        group_id = data.get("group_id")
        limit = data.get("limit", 10)
        force_refresh = str(data.get("refresh") or "").lower() in {"1", "true", "yes"}
        cache_key = (str(group_id or ""), _int_value(limit, 10))
        if SYSTEM_SYNC_BATCHES_CACHE_TTL_SECONDS > 0 and not force_refresh:
            with _SYSTEM_SYNC_BATCHES_CACHE_LOCK:
                cached = _SYSTEM_SYNC_BATCHES_CACHE.get(cache_key)
                if cached and cached["expires_at"] > time.time():
                    return jsonify({"success": True, "data": {"batches": deepcopy(cached["batches"])}})

        result = zsxq_sync_service.list_system_sync_batches(
            group_id=group_id,
            limit=limit,
        )
        if SYSTEM_SYNC_BATCHES_CACHE_TTL_SECONDS > 0:
            with _SYSTEM_SYNC_BATCHES_CACHE_LOCK:
                _SYSTEM_SYNC_BATCHES_CACHE[cache_key] = {
                    "expires_at": time.time() + SYSTEM_SYNC_BATCHES_CACHE_TTL_SECONDS,
                    "batches": deepcopy(result),
                }
        return jsonify({"success": True, "data": {"batches": result}})
    except Exception as e:
        return jsonify({"success": True, "data": {"batches": [], "error": _safe_error(e)}})


@bp.route("/system-sync-test/batch-detail", methods=["POST"])
def system_sync_test_batch_detail():
    """Read one project-scheduler sync batch with full review details."""
    try:
        data = request.get_json(silent=True) or {}
        batch_id = str(data.get("batch_id") or "").strip()
        if not batch_id:
            return handle_api_error(400, "batch_id 不能为空")

        result = zsxq_sync_service.get_system_sync_batch(
            batch_id=batch_id,
            group_id=data.get("group_id"),
        )
        if not result:
            return handle_api_error(404, "未找到批次明细")
        return jsonify({"success": True, "data": {"batch": result}})
    except Exception as e:
        return handle_api_error(502, _safe_error(e))


@bp.route("/manual-download-workflows", methods=["POST"])
def manual_download_workflows():
    """Create a workflow from explicitly selected ZSXQ attachments."""
    try:
        data, cookie = _payload(purpose="zsxq.sync", prefer_db=True)
        group_id = data.get("group_id")
        if not group_id:
            return handle_api_error(400, "group_id 不能为空")
        if not (data.get("attachments") or data.get("items")):
            return handle_api_error(400, "请先选择需要下载的附件")

        trigger = data.get("trigger") or "zsxq_lab_manual_download_plan"
        workflow = zsxq_workflow_service.create_manual_download_workflow(
            config=data,
            trigger=trigger,
        )
        try:
            queued = zsxq_workflow_service.enqueue_workflow_task(
                workflow["id"],
                credential_id=data.get("_credential_id") or data.get("credential_id"),
                workflow_payload=workflow,
            )
        except Exception as exc:
            logger.exception("[ZSXQ_WORKFLOW] manual download workflow queue failed | workflow=%s", workflow.get("id"))
            zsxq_workflow_service.mark_failed_by_id(
                workflow["id"],
                f"人工计划工作流入队失败: {exc}",
                stage="workflow",
            )
            return handle_api_error(502, f"人工计划工作流入队失败：{exc}")

        _clear_system_sync_batches_cache()
        return jsonify({
            "success": True,
            "data": {
                "workflow": queued.get("workflow") or workflow,
                "workflow_run_id": queued.get("workflow_run_id") or workflow["id"],
                "celery_task_id": queued.get("celery_task_id"),
            },
        })
    except ValueError as e:
        return handle_api_error(400, str(e))
    except Exception as e:
        logger.exception("[ZSXQ_WORKFLOW] manual download workflow endpoint failed")
        return handle_api_error(502, f"人工计划工作流接口异常：{e}")


@bp.route("/workflows", methods=["POST"])
def zsxq_workflows():
    """List recent ZSXQ workflow runs."""
    try:
        data = request.get_json(silent=True) or {}
        result = zsxq_workflow_service.list_runs(
            group_id=data.get("group_id"),
            limit=data.get("limit", 20),
        )
        return jsonify({"success": True, "data": {"workflows": result}})
    except Exception as e:
        return handle_api_error(502, _safe_error(e))


@bp.route("/workflows/<workflow_run_id>", methods=["GET"])
def zsxq_workflow_detail(workflow_run_id):
    """Read one ZSXQ workflow run."""
    try:
        result = zsxq_workflow_service.get_run(workflow_run_id)
        if not result:
            return handle_api_error(404, "未找到工作流记录")
        return jsonify({"success": True, "data": {"workflow": result}})
    except Exception as e:
        return handle_api_error(502, _safe_error(e))


@bp.route("/workflow-notifications/test", methods=["POST"])
def zsxq_workflow_notification_test():
    """Send one test workflow notification through the configured provider."""
    try:
        result = workflow_notification_service.send_test_email()
        if not result.get("sent"):
            return handle_api_error(400, f"工作流通知未发送：{result}")
        return jsonify({"success": True, "data": {"notification": result}})
    except Exception as e:
        logger.exception("[ZSXQ_WORKFLOW_NOTIFY] test email failed")
        return handle_api_error(502, f"工作流测试邮件发送失败：{e}")


@bp.route("/system-sync-scheduler/status", methods=["GET"])
def system_sync_scheduler_status_endpoint():
    """Read system scheduler status (pid/running/next-run)."""
    try:
        return jsonify({"success": True, "data": {"scheduler": system_sync_scheduler_status()}})
    except Exception as e:
        return handle_api_error(502, _safe_error(e))


@bp.route("/system-sync-scheduler/start", methods=["POST"])
def system_sync_scheduler_start():
    """Start the long-running ZSXQ scheduler process."""
    try:
        result = start_system_sync_scheduler(cookie_override=None)
        _clear_system_sync_batches_cache()
        return jsonify({"success": True, "data": {"scheduler": result}})
    except Exception as e:
        logger.exception("[ZSXQ_SYSTEM_SYNC_SCHEDULER] start failed")
        return handle_api_error(502, _safe_error(e))


@bp.route("/system-sync-scheduler/stop", methods=["POST"])
def system_sync_scheduler_stop():
    """Stop the long-running ZSXQ scheduler process."""
    try:
        result = stop_system_sync_scheduler()
        _clear_system_sync_batches_cache()
        return jsonify({"success": True, "data": {"scheduler": result}})
    except Exception as e:
        logger.exception("[ZSXQ_SYSTEM_SYNC_SCHEDULER] stop failed")
        return handle_api_error(502, _safe_error(e))


@bp.route("/system-sync-scheduler/restart", methods=["POST"])
def system_sync_scheduler_restart():
    """Restart the long-running ZSXQ scheduler process."""
    try:
        result = restart_system_sync_scheduler(cookie_override=None)
        _clear_system_sync_batches_cache()
        return jsonify({"success": True, "data": {"scheduler": result}})
    except Exception as e:
        logger.exception("[ZSXQ_SYSTEM_SYNC_SCHEDULER] restart failed")
        return handle_api_error(502, _safe_error(e))


@bp.route("/system-sync-scheduler/run-once", methods=["POST"])
def system_sync_scheduler_run_once():
    """Trigger one scheduler round immediately (non-loop)."""
    try:
        _clear_system_sync_batches_cache()
        result = trigger_once_system_sync_scheduler(cookie_override=None)
        _clear_system_sync_batches_cache()
        return jsonify({"success": True, "data": {"scheduler": result}})
    except Exception as e:
        logger.exception("[ZSXQ_SYSTEM_SYNC_SCHEDULER] run once failed")
        return handle_api_error(502, _safe_error(e))


@bp.route("/system-sync-scheduler/logs", methods=["GET"])
def system_sync_scheduler_logs():
    """Read recent lines from system scheduler log file."""
    try:
        lines = request.args.get("lines", "200")
        try:
            parsed_lines = max(1, min(int(lines), 1000))
        except (TypeError, ValueError):
            parsed_lines = 200
        content = scheduler_log_tail(lines=parsed_lines)
        return jsonify({
            "success": True,
            "data": {
                "lines": content,
                "raw": "\n".join(content),
            },
        })
    except Exception as e:
        return handle_api_error(502, f"读取系统计划日志失败: {e}")


@bp.route("/auto-publish/config", methods=["GET", "POST"])
def auto_publish_config():
    """Read or save ZSXQ auto publish strategy config."""
    if request.method == "GET":
        try:
            return jsonify({"success": True, "data": {"config": auto_publish_config_status(include_secrets=False)}})
        except Exception as e:
            return handle_api_error(502, f"读取自动发帖配置失败: {e}")

    try:
        data = request.get_json(silent=True) or {}
        result = auto_publish_save_config(data, cookie=None)
        return jsonify({"success": True, "data": {"config": result}})
    except ValueError as e:
        return handle_api_error(400, str(e))
    except Exception as e:
        logger.exception("[ZSXQ_AUTO_PUBLISH] save config failed")
        return handle_api_error(502, _safe_error(e))


@bp.route("/auto-publish/preview", methods=["POST"])
def auto_publish_preview():
    """Preview the next publish batch for current strategy payload."""
    try:
        data = request.get_json(silent=True) or {}
        result = auto_publish_preview_payload(data, cookie=None)
        return jsonify({"success": True, "data": result})
    except ValueError as e:
        return handle_api_error(400, str(e))
    except Exception as e:
        logger.exception("[ZSXQ_AUTO_PUBLISH] preview failed")
        return handle_api_error(502, _safe_error(e))


@bp.route("/auto-publish/runs", methods=["GET"])
def auto_publish_runs():
    """List recent auto publish runs."""
    try:
        limit = request.args.get("limit", "20")
        try:
            parsed_limit = max(1, min(int(limit), 100))
        except (TypeError, ValueError):
            parsed_limit = 20
        runs = auto_publish_list_runs(limit=parsed_limit)
        return jsonify({"success": True, "data": {"runs": runs}})
    except Exception as e:
        return handle_api_error(502, f"读取自动发帖历史失败: {e}")


@bp.route("/auto-publish/scheduler/status", methods=["GET"])
def auto_publish_scheduler_status_endpoint():
    """Read auto publish scheduler status."""
    try:
        return jsonify({"success": True, "data": {"scheduler": auto_publish_scheduler_status()}})
    except Exception as e:
        return handle_api_error(502, f"读取自动发帖调度状态失败: {e}")


@bp.route("/auto-publish/scheduler/start", methods=["POST"])
def auto_publish_scheduler_start():
    """Start auto publish scheduler."""
    try:
        result = start_auto_publish_scheduler(cookie_override=None)
        return jsonify({"success": True, "data": {"scheduler": result}})
    except Exception as e:
        logger.exception("[ZSXQ_AUTO_PUBLISH] start scheduler failed")
        return handle_api_error(502, _safe_error(e))


@bp.route("/auto-publish/scheduler/stop", methods=["POST"])
def auto_publish_scheduler_stop():
    """Stop auto publish scheduler."""
    try:
        result = stop_auto_publish_scheduler()
        return jsonify({"success": True, "data": {"scheduler": result}})
    except Exception as e:
        logger.exception("[ZSXQ_AUTO_PUBLISH] stop scheduler failed")
        return handle_api_error(502, _safe_error(e))


@bp.route("/auto-publish/scheduler/restart", methods=["POST"])
def auto_publish_scheduler_restart():
    """Restart auto publish scheduler."""
    try:
        result = restart_auto_publish_scheduler(cookie_override=None)
        return jsonify({"success": True, "data": {"scheduler": result}})
    except Exception as e:
        logger.exception("[ZSXQ_AUTO_PUBLISH] restart scheduler failed")
        return handle_api_error(502, _safe_error(e))


@bp.route("/auto-publish/scheduler/run-once", methods=["POST"])
def auto_publish_scheduler_run_once():
    """Trigger one background auto publish run."""
    try:
        result = trigger_once_auto_publish_scheduler(cookie_override=None)
        return jsonify({"success": True, "data": {"scheduler": result}})
    except Exception as e:
        logger.exception("[ZSXQ_AUTO_PUBLISH] run once failed")
        return handle_api_error(502, _safe_error(e))


@bp.route("/auto-publish/scheduler/logs", methods=["GET"])
def auto_publish_scheduler_logs():
    """Read recent auto publish scheduler logs."""
    try:
        lines = request.args.get("lines", "200")
        try:
            parsed_lines = max(1, min(int(lines), 1000))
        except (TypeError, ValueError):
            parsed_lines = 200
        content = auto_publish_scheduler_log_tail(lines=parsed_lines)
        return jsonify({
            "success": True,
            "data": {
                "lines": content,
                "raw": "\n".join(content),
            },
        })
    except Exception as e:
        return handle_api_error(502, f"读取自动发帖日志失败: {e}")
