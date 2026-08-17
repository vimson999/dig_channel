"""Loopback-only API for Chrome sidebar Knowledge Planet imports."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from services.zsxq_browser_import_service import (
    BrowserImportValidationError,
    DEFAULT_GROUP_ID,
    IdempotencyConflictError,
    TAB_CONFIGS,
    zsxq_browser_import_service,
)


bp = Blueprint(
    "zsxq_browser_import",
    __name__,
    url_prefix="/api/v1/zsxq",
)

_LOOPBACK_ADDRESSES = {"127.0.0.1", "::1", "localhost"}


def _reject_non_loopback():
    if request.remote_addr not in _LOOPBACK_ADDRESSES:
        return jsonify({"success": False, "error": "该接口只允许本机调用"}), 403
    return None


def _manifest_paths():
    payload = request.get_json(silent=True) or {}
    paths = payload.get("manifest_paths")
    if (
        not isinstance(paths, list)
        or not paths
        or not all(isinstance(path, str) and path.strip() for path in paths)
    ):
        raise ValueError("manifest_paths 必须是非空字符串数组")
    if len(paths) > 20:
        raise ValueError("单次最多提交 20 个 manifest")
    return [path.strip() for path in paths], payload


def _import_inputs():
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        raise ValueError("请求体必须是 JSON 对象")
    has_manifest = "manifest" in payload
    has_paths = "manifest_paths" in payload
    if has_manifest == has_paths:
        raise ValueError("manifest 与 manifest_paths 必须且只能提供一种")
    if has_manifest:
        manifest = payload.get("manifest")
        if not isinstance(manifest, dict):
            raise ValueError("manifest 必须是 JSON 对象")
        return "inline", manifest, payload

    paths = payload.get("manifest_paths")
    if (
        not isinstance(paths, list)
        or not paths
        or not all(isinstance(path, str) and path.strip() for path in paths)
    ):
        raise ValueError("manifest_paths 必须是非空字符串数组")
    if len(paths) > 20:
        raise ValueError("单次最多提交 20 个 manifest")
    return "paths", [path.strip() for path in paths], payload


def _history_request():
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        raise ValueError("请求体必须是 JSON 对象")
    kind = str(payload.get("kind") or "").strip().lower()
    if kind not in TAB_CONFIGS:
        raise ValueError("kind 只允许 mp3 或 pdf")
    days = payload.get("days", 3)
    if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 7:
        raise ValueError("days 必须是 1 到 7 之间的整数")
    group_id = str(payload.get("group_id") or "").strip()
    if group_id != DEFAULT_GROUP_ID:
        raise ValueError("group_id 与服务端配置不匹配")
    tab_id = str(payload.get("tab_id") or "").strip()
    if tab_id != TAB_CONFIGS[kind]["tab_id"]:
        raise ValueError("tab_id 与 kind 的服务端配置不匹配")
    return kind, days


@bp.route("/browser-import/history", methods=["POST"])
def browser_import_history():
    rejected = _reject_non_loopback()
    if rejected:
        return rejected
    try:
        kind, days = _history_request()
        result = zsxq_browser_import_service.recent_download_history(kind, days=days)
        return jsonify({"success": True, "data": result})
    except BrowserImportValidationError as exc:
        return (
            jsonify({"success": False, "error": str(exc), "details": exc.details}),
            400,
        )
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"success": False, "error": f"下载历史查询失败: {exc}"}), 500


@bp.route("/browser-import/preview", methods=["POST"])
def preview_browser_import():
    rejected = _reject_non_loopback()
    if rejected:
        return rejected
    try:
        paths, _payload = _manifest_paths()
        result = zsxq_browser_import_service.preview_manifests(paths)
        return jsonify({"success": True, "data": result})
    except BrowserImportValidationError as exc:
        return (
            jsonify({"success": False, "error": str(exc), "details": exc.details}),
            400,
        )
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"success": False, "error": f"侧边栏 manifest 校验失败: {exc}"}), 500


@bp.route("/browser-import", methods=["POST"])
def enqueue_browser_import():
    rejected = _reject_non_loopback()
    if rejected:
        return rejected
    try:
        mode, value, payload = _import_inputs()
        if mode == "inline":
            idempotency_key = request.headers.get("Idempotency-Key", "")
            dispatch_options = {
                "dispatch_clean": bool(payload.get("dispatch_clean", False)),
                "cleaner": payload.get("cleaner")
                if isinstance(payload.get("cleaner"), dict)
                else {},
                "auto_publish": payload.get("auto_publish")
                if isinstance(payload.get("auto_publish"), dict)
                else {},
            }
            prepared = zsxq_browser_import_service.prepare_inline_submission(
                value,
                idempotency_key,
                **dispatch_options,
            )
            if prepared["should_dispatch"]:
                from tasks.zsxq_browser_import import run_browser_import

                persisted_options = prepared["dispatch_options"]
                try:
                    run_browser_import.apply_async(
                        args=[[prepared["manifest_path"]]],
                        kwargs={
                            "dispatch_clean": persisted_options["dispatch_clean"],
                            "cleaner": persisted_options["cleaner"],
                            "auto_publish": persisted_options["auto_publish"],
                            "submission_id": prepared["submission_id"],
                        },
                        task_id=prepared["task_id"],
                    )
                except Exception as exc:
                    failed = (
                        zsxq_browser_import_service.mark_submission_dispatch_failed(
                            prepared["submission_id"], str(exc)
                        )
                    )
                    return (
                        jsonify(
                            {
                                "success": False,
                                "queued": False,
                                **failed,
                                "replayed": prepared["replayed"],
                                "error": f"Celery 任务投递失败: {exc}",
                            }
                        ),
                        503,
                    )
                queued = zsxq_browser_import_service.mark_submission_queued(
                    prepared["submission_id"]
                )
                prepared = {**queued, "replayed": prepared["replayed"]}

            status_code = 200 if prepared["status"] == "completed" else 202
            return (
                jsonify(
                    {
                        "success": True,
                        "queued": prepared["status"]
                        in {"received", "queued", "processing"},
                        **prepared,
                    }
                ),
                status_code,
            )

        paths = value
        preview = zsxq_browser_import_service.preview_manifests(paths)
        from tasks.zsxq_browser_import import run_browser_import

        async_result = run_browser_import.delay(
            paths,
            dispatch_clean=bool(payload.get("dispatch_clean", False)),
            cleaner=payload.get("cleaner")
            if isinstance(payload.get("cleaner"), dict)
            else {},
            auto_publish=payload.get("auto_publish")
            if isinstance(payload.get("auto_publish"), dict)
            else {},
        )
        return (
            jsonify(
                {
                    "success": True,
                    "queued": True,
                    "task_id": getattr(async_result, "id", None),
                    "preview": preview,
                }
            ),
            202,
        )
    except IdempotencyConflictError as exc:
        return (
            jsonify(
                {
                    "success": False,
                    "code": "idempotency_conflict",
                    "error": str(exc),
                    "details": exc.details,
                }
            ),
            409,
        )
    except BrowserImportValidationError as exc:
        return (
            jsonify({"success": False, "error": str(exc), "details": exc.details}),
            400,
        )
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"success": False, "error": f"侧边栏导入任务创建失败: {exc}"}), 500


__all__ = ["bp"]
