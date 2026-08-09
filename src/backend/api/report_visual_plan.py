import json
import hashlib
import logging
import time
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from threading import Lock

from flask import Blueprint, jsonify, request, send_file
from sqlalchemy import or_
from sqlalchemy.orm import load_only
from sqlalchemy.orm.attributes import flag_modified

from api.report_script_material_workflow import (
    MATERIAL_STATUS_FEATURES,
    _latest_material_task_for_asset,
    _material_task_payload,
    _pdf_meta_from_zsxq_attachment,
    _request_payload,
    _resolve_pdf_path,
    _visible_user_ids,
)
from models import db, ReportVisualPlanOutput, ReportVisualPlanSegment
from models.task import PublicationTask
from services.report_visual_plan_service import (
    FEATURE_NAME,
    OUTPUT_ROOT,
    QUEUE_NAME,
    SCHEMA_VERSION,
    STORYBOARD_SCHEMA_VERSION,
    build_visual_plan_preview,
    provider_status,
)
from services.report_visual_scheme import (
    DEFAULT_VISUAL_SCHEME_KEY,
    visual_scheme,
    visual_scheme_payloads,
)
from services import gemini_browser
from services.runtime_guard import maybe_guarded_apply_async
from tasks.report_visual_plan import (
    process_report_visual_plan_job,
    retry_normalize_report_visual_plan_job,
)
from utils.auth_utils import require_login


report_visual_plan_bp = Blueprint(
    "report_visual_plan",
    __name__,
    url_prefix="/api/report-visual-plans",
)

_TERMINAL_STORYBOARD_CACHE_TTL_SECONDS = 300
_TERMINAL_STORYBOARD_REDIS_TTL_SECONDS = 86400
_TERMINAL_STORYBOARD_CACHE = {}
_TERMINAL_STORYBOARD_JOB_CACHE = {}
_TERMINAL_STORYBOARD_CACHE_LOCK = Lock()
_TERMINAL_STORYBOARD_CACHE_MAX_ENTRIES = 512
_STORYBOARD_PARENT_FEATURES = {"storyboard", "report_stick_figure_video_workflow"}
logger = logging.getLogger(__name__)


def _storyboard_cache_key(current_user, source_task_id):
    return (str(getattr(current_user, "id", "") or ""), str(source_task_id))


def _storyboard_redis_client():
    try:
        from utils.redis_client import redis_client

        return redis_client
    except Exception as exc:
        logger.warning("storyboard terminal cache unavailable: %s", exc)
        return None


def _storyboard_redis_key(kind, current_user, reference_id):
    user_id, reference_id = _storyboard_cache_key(current_user, reference_id)
    digest = hashlib.sha256(f"{user_id}:{reference_id}".encode("utf-8")).hexdigest()
    return f"shioa:report_visual_plan:terminal:v2:{kind}:{digest}"


def _persistent_storyboard_get(kind, current_user, reference_id):
    client = _storyboard_redis_client()
    if client is None:
        return None
    try:
        raw = client.get(_storyboard_redis_key(kind, current_user, reference_id))
        payload = json.loads(raw) if raw else None
        return payload if isinstance(payload, dict) else None
    except Exception as exc:
        logger.warning("storyboard terminal cache read failed: %s", exc)
        return None


def _persistent_storyboard_set(kind, current_user, reference_id, payload):
    client = _storyboard_redis_client()
    if client is None:
        return
    try:
        client.setex(
            _storyboard_redis_key(kind, current_user, reference_id),
            _TERMINAL_STORYBOARD_REDIS_TTL_SECONDS,
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        )
    except Exception as exc:
        logger.warning("storyboard terminal cache write failed: %s", exc)


def _persistent_storyboard_delete(kind, current_user, reference_id):
    client = _storyboard_redis_client()
    if client is None:
        return
    try:
        client.delete(_storyboard_redis_key(kind, current_user, reference_id))
    except Exception as exc:
        logger.warning("storyboard terminal cache delete failed: %s", exc)


def _cached_terminal_storyboard(current_user, source_task_id):
    key = _storyboard_cache_key(current_user, source_task_id)
    now = time.time()
    with _TERMINAL_STORYBOARD_CACHE_LOCK:
        cached = _TERMINAL_STORYBOARD_CACHE.get(key)
        if cached and float(cached.get("expires_at") or 0) > now:
            return deepcopy(cached["payload"])
        _TERMINAL_STORYBOARD_CACHE.pop(key, None)
    payload = _persistent_storyboard_get("source", current_user, source_task_id)
    if payload is not None:
        with _TERMINAL_STORYBOARD_CACHE_LOCK:
            _TERMINAL_STORYBOARD_CACHE[key] = {
                "expires_at": now + _TERMINAL_STORYBOARD_CACHE_TTL_SECONDS,
                "payload": deepcopy(payload),
            }
        return deepcopy(payload)
    return None


def _cache_terminal_storyboard(current_user, source_task_id, payload):
    key = _storyboard_cache_key(current_user, source_task_id)
    with _TERMINAL_STORYBOARD_CACHE_LOCK:
        if len(_TERMINAL_STORYBOARD_CACHE) >= _TERMINAL_STORYBOARD_CACHE_MAX_ENTRIES:
            _TERMINAL_STORYBOARD_CACHE.clear()
        _TERMINAL_STORYBOARD_CACHE[key] = {
            "expires_at": time.time() + _TERMINAL_STORYBOARD_CACHE_TTL_SECONDS,
            "payload": deepcopy(payload),
        }
    _persistent_storyboard_set("source", current_user, source_task_id, payload)


def _cached_terminal_storyboard_job(current_user, job_id):
    key = _storyboard_cache_key(current_user, job_id)
    now = time.time()
    with _TERMINAL_STORYBOARD_CACHE_LOCK:
        cached = _TERMINAL_STORYBOARD_JOB_CACHE.get(key)
        if cached and float(cached.get("expires_at") or 0) > now:
            return deepcopy(cached["payload"])
        _TERMINAL_STORYBOARD_JOB_CACHE.pop(key, None)
    payload = _persistent_storyboard_get("job", current_user, job_id)
    if payload is not None:
        with _TERMINAL_STORYBOARD_CACHE_LOCK:
            _TERMINAL_STORYBOARD_JOB_CACHE[key] = {
                "expires_at": now + _TERMINAL_STORYBOARD_CACHE_TTL_SECONDS,
                "payload": deepcopy(payload),
            }
        return deepcopy(payload)
    return None


def _cache_terminal_storyboard_job(current_user, job_id, payload):
    key = _storyboard_cache_key(current_user, job_id)
    with _TERMINAL_STORYBOARD_CACHE_LOCK:
        if len(_TERMINAL_STORYBOARD_JOB_CACHE) >= _TERMINAL_STORYBOARD_CACHE_MAX_ENTRIES:
            _TERMINAL_STORYBOARD_JOB_CACHE.clear()
        _TERMINAL_STORYBOARD_JOB_CACHE[key] = {
            "expires_at": time.time() + _TERMINAL_STORYBOARD_CACHE_TTL_SECONDS,
            "payload": deepcopy(payload),
        }
    _persistent_storyboard_set("job", current_user, job_id, payload)


def _clear_terminal_storyboard_cache(source_task_id=None, current_user=None):
    source_task_id = str(source_task_id or "")
    with _TERMINAL_STORYBOARD_CACHE_LOCK:
        if not source_task_id:
            _TERMINAL_STORYBOARD_CACHE.clear()
            _TERMINAL_STORYBOARD_JOB_CACHE.clear()
            return
        for key in list(_TERMINAL_STORYBOARD_CACHE):
            if key[1] == source_task_id:
                _TERMINAL_STORYBOARD_CACHE.pop(key, None)
    if current_user is not None:
        _persistent_storyboard_delete("source", current_user, source_task_id)


@report_visual_plan_bp.route("/status", methods=["GET"])
@require_login
def status(_current_user):
    return jsonify({"success": True, "data": {
        "provider": provider_status(),
        "gemini": gemini_browser.status_payload(),
        "visual_schemes": visual_scheme_payloads(),
    }})


@report_visual_plan_bp.route("/jobs", methods=["POST"])
@require_login
def create_storyboard_job(current_user):
    data = _request_payload()
    force = _truthy(data.get("force"), default=False)
    try:
        scheme = visual_scheme(data.get("visual_scheme_key"))
        material_task = _resolve_storyboard_source(data, current_user)
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    if not force:
        existing = (
            db.session.query(ReportVisualPlanOutput.task_id)
            .join(PublicationTask, PublicationTask.id == ReportVisualPlanOutput.task_id)
            .filter(ReportVisualPlanOutput.source_task_id == material_task.id)
            .filter(ReportVisualPlanOutput.schema_version == STORYBOARD_SCHEMA_VERSION)
            .filter(ReportVisualPlanOutput.visual_scheme_key == scheme.key)
            .filter(PublicationTask.status == "completed")
            .order_by(ReportVisualPlanOutput.created_at.desc())
            .first()
        )
        if existing:
            return jsonify({"success": True, "cached": True, "job_id": existing.task_id, "status": "succeeded"})

    source_cache_key = f"{material_task.id}:{scheme.key}"
    _clear_terminal_storyboard_cache(source_cache_key, current_user=current_user)

    user_id = str(getattr(current_user, "id", "") or "")
    workflow_id = str(data.get("workflow_job_id") or "").strip()
    if workflow_id:
        workflow = (
            PublicationTask.query.options(load_only(
                PublicationTask.id,
                PublicationTask.source_task_id,
            ))
            .filter(
                PublicationTask.id == workflow_id,
                PublicationTask.feature == "report_stick_figure_video_workflow",
                PublicationTask.source_task_id == material_task.id,
                or_(
                    PublicationTask.user_id.in_(_visible_user_ids(current_user)),
                    PublicationTask.user_id.is_(None),
                ),
            )
            .one_or_none()
        )
        if not workflow:
            return jsonify({"success": False, "message": "火柴人视频编排任务不存在或与文案不匹配"}), 400
        try:
            from api.report_stick_figure_video_workflow import _clear_scene_structure_cache

            _clear_scene_structure_cache(
                workflow_id=workflow_id,
                source_task_id=material_task.id,
            )
        except Exception as exc:
            logger.warning("清理旧分镜结构缓存失败: workflow=%s source=%s error=%s", workflow_id, material_task.id, exc)
    task = PublicationTask(
        status="pending",
        current_step="queued",
        progress=0,
        params={
            "feature": FEATURE_NAME,
            "user_id": user_id,
            "source_task_id": material_task.id,
            "source_feature": material_task.feature,
            "parent_feature": (
                "report_stick_figure_video_workflow"
                if workflow_id
                else "storyboard"
            ),
            "parent_task_id": workflow_id or None,
            "visual_scheme_key": scheme.key,
            FEATURE_NAME: {
                "schema_version": STORYBOARD_SCHEMA_VERSION,
                "visual_scheme_key": scheme.key,
            },
        },
        result={"schema_version": STORYBOARD_SCHEMA_VERSION},
    )
    task.task_title = material_task.task_title or material_task.report_audio_pdf_topic_title or material_task.report_audio_pdf_filename or "研报分镜"
    task.report_visual_scheme_key = scheme.key
    task.report_audio_pdf_attachment_id = material_task.report_audio_pdf_attachment_id
    task.report_audio_pdf_filename = material_task.report_audio_pdf_filename
    task.report_audio_pdf_topic_title = material_task.report_audio_pdf_topic_title
    db.session.add(task)
    db.session.flush()
    task_id = task.id
    db.session.commit()
    try:
        submit_result = maybe_guarded_apply_async(
            process_report_visual_plan_job,
            args=[task_id],
            queue=QUEUE_NAME,
            feature=FEATURE_NAME,
            task_id=task_id,
            retry=False,
        )
        async_result = submit_result["async_result"]
    except Exception as exc:
        PublicationTask.query.filter(PublicationTask.id == task_id).update({
            PublicationTask.status: "failed",
            PublicationTask.current_step: "failed",
            PublicationTask.error_message: f"分镜任务入队失败: {exc}"[:4000],
            PublicationTask.completed_at: datetime.utcnow(),
        }, synchronize_session=False)
        db.session.commit()
        return jsonify({"success": False, "message": f"分镜任务入队失败: {exc}"}), 502
    PublicationTask.query.filter(PublicationTask.id == task_id).update(
        {PublicationTask.celery_task_id: async_result.id}, synchronize_session=False
    )
    db.session.commit()
    return jsonify({
        "success": True,
        "cached": False,
        "job_id": task_id,
        "status": "deferred" if submit_result.get("deferred") else "queued",
    })


def _resolve_storyboard_source(data, current_user):
    material_job_id = str(data.get("material_job_id") or data.get("job_id") or "").strip()
    if not material_job_id:
        raise ValueError("请提供 material_job_id")
    task = (
        PublicationTask.query.options(load_only(
            PublicationTask.id,
            PublicationTask.feature,
            PublicationTask.user_id,
            PublicationTask.status,
            PublicationTask.final_script_chars,
            PublicationTask.task_title,
            PublicationTask.report_audio_pdf_attachment_id,
            PublicationTask.report_audio_pdf_filename,
            PublicationTask.report_audio_pdf_topic_title,
        ))
        .filter(PublicationTask.id == material_job_id)
        .filter(PublicationTask.feature.in_(MATERIAL_STATUS_FEATURES))
        .filter(or_(PublicationTask.user_id.in_(_visible_user_ids(current_user)), PublicationTask.user_id.is_(None)))
        .first()
    )
    if not task:
        raise ValueError("基础文案任务不存在")
    if task.status != "completed" or int(task.final_script_chars or 0) <= 0:
        raise ValueError("基础文案任务还没有完整文案")
    return task


@report_visual_plan_bp.route("/preview", methods=["POST"])
@require_login
def create_preview(current_user):
    data = _request_payload()
    force = _truthy(data.get("force") or data.get("refresh"), default=False)
    try:
        material_task, material_payload, pdf_path = _resolve_material_input(data, current_user)
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400

    if not force:
        existing = _latest_visual_plan_task(material_task.id, current_user)
        if existing:
            payload = _job_payload(existing, include_detail=True)
            return jsonify({
                "success": True,
                "cached": True,
                "job_id": payload.get("job_id"),
                "status": payload.get("status"),
                "data": payload,
            })

    task = _create_visual_plan_task(material_task, material_payload, current_user)
    db.session.add(task)
    db.session.commit()
    try:
        task.progress = 25
        task.current_step = "generate_visual_plan"
        db.session.commit()

        result = build_visual_plan_preview(
            material_payload,
            pdf_path=str(pdf_path) if pdf_path else None,
            job_id=task.id,
        )
        result["schema_version"] = SCHEMA_VERSION
        result["source_task_id"] = material_task.id
        result["source_feature"] = material_task.feature
        result["material"] = _material_summary(material_payload)
        task.status = "completed"
        task.current_step = "done"
        task.progress = 100
        task.completed_at = datetime.utcnow()
        task.result = result
        task.task_title = _visual_task_title(material_payload)
        task.report_audio_output_path = _first_asset_path(result)
        flag_modified(task, "result")
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        task = (
            PublicationTask.query.options(load_only(
                PublicationTask.id,
                PublicationTask.status,
                PublicationTask.current_step,
                PublicationTask.progress,
                PublicationTask.result,
                PublicationTask.error_message,
            ))
            .filter(PublicationTask.id == task.id)
            .first()
        )
        if task:
            task.status = "failed"
            task.current_step = "failed"
            task.progress = max(task.progress or 0, 5)
            task.error_message = str(exc)
            task.result = {**(task.result or {}), "error": str(exc), "failed_at": datetime.utcnow().isoformat() + "Z"}
            flag_modified(task, "result")
            db.session.commit()
        return jsonify({"success": False, "message": f"visual_plan 生成失败：{exc}"}), 500

    payload = _job_payload(task, include_detail=True)
    return jsonify({
        "success": True,
        "cached": False,
        "job_id": task.id,
        "status": payload.get("status"),
        "data": payload,
    })


@report_visual_plan_bp.route("/jobs/<job_id>", methods=["GET"])
@require_login
def get_job(current_user, job_id):
    cached = _cached_terminal_storyboard_job(current_user, job_id)
    if cached is not None:
        return jsonify(cached)

    task = _get_owned_visual_task(current_user, job_id, include_result=False)
    if not task:
        return jsonify({"success": False, "message": "visual_plan 任务不存在"}), 404
    output = _storyboard_output(task.id)
    if output:
        payload = _storyboard_job_payload(task, output, include_segments=True)
    elif task.parent_feature in _STORYBOARD_PARENT_FEATURES:
        payload = _storyboard_job_payload(task, None, include_segments=False)
    else:
        task = _get_owned_visual_task(current_user, job_id, include_result=True)
        payload = _job_payload(task, include_detail=True)
    response_payload = {"success": True, "job_id": task.id, "status": payload.get("status"), "data": payload}
    if task.status == "completed" and output:
        _cache_terminal_storyboard_job(current_user, task.id, response_payload)
    return jsonify(response_payload)


@report_visual_plan_bp.route("/jobs/<job_id>/retry-normalize", methods=["POST"])
@require_login
def retry_storyboard_normalize(current_user, job_id):
    task = _get_owned_visual_task(current_user, job_id, include_result=False)
    if not task:
        return jsonify({"success": False, "message": "分镜任务不存在"}), 404
    if task.status in {"pending", "processing"}:
        return jsonify({
            "success": True,
            "job_id": task.id,
            "status": _status_label(task.status),
            "message": "分镜梳理正在运行",
        })
    gemini_path = (OUTPUT_ROOT / task.id / "gemini_storyboard.json").resolve()
    task_root = (OUTPUT_ROOT / task.id).resolve()
    if task_root not in gemini_path.parents or not gemini_path.is_file():
        return jsonify({
            "success": False,
            "message": "没有可复用的 Gem 原始返回，请重新生成分镜方案",
        }), 409

    try:
        submit_result = maybe_guarded_apply_async(
            retry_normalize_report_visual_plan_job,
            args=[task.id],
            queue=QUEUE_NAME,
            feature=FEATURE_NAME,
            task_id=task.id,
            retry=False,
        )
        async_result = submit_result["async_result"]
    except Exception as exc:
        return jsonify({"success": False, "message": f"续跑梳理入队失败：{exc}"}), 502

    PublicationTask.query.filter(PublicationTask.id == task.id).update({
        PublicationTask.status: "pending",
        PublicationTask.current_step: "normalize_queued",
        PublicationTask.progress: 70,
        PublicationTask.error_message: None,
        PublicationTask.completed_at: None,
        PublicationTask.celery_task_id: async_result.id,
    }, synchronize_session=False)
    db.session.commit()
    scheme_key = task.report_visual_scheme_key or DEFAULT_VISUAL_SCHEME_KEY
    _clear_terminal_storyboard_cache(
        f"{task.source_task_id}:{scheme_key}",
        current_user=current_user,
    )
    return jsonify({
        "success": True,
        "job_id": task.id,
        "status": "queued",
        "message": "已复用 Gem 原文继续梳理，不会重新请求 Gemini",
    })


@report_visual_plan_bp.route("/sources/<source_task_id>/latest", methods=["GET"])
@require_login
def get_latest_storyboard_for_source(current_user, source_task_id):
    try:
        scheme = visual_scheme(request.args.get("visual_scheme_key"), require_configured=False)
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    source_cache_key = f"{source_task_id}:{scheme.key}"
    cached = _cached_terminal_storyboard(current_user, source_cache_key)
    if cached is not None:
        return jsonify(cached)

    source = (
        PublicationTask.query.options(load_only(PublicationTask.id))
        .filter(PublicationTask.id == str(source_task_id))
        .filter(PublicationTask.feature.in_(MATERIAL_STATUS_FEATURES))
        .filter(or_(PublicationTask.user_id.in_(_visible_user_ids(current_user)), PublicationTask.user_id.is_(None)))
        .first()
    )
    if not source:
        return jsonify({"success": False, "message": "基础文案任务不存在"}), 404

    latest_id = (
        db.session.query(PublicationTask.id)
        .filter(PublicationTask.feature == FEATURE_NAME)
        .filter(PublicationTask.source_task_id == source.id)
        .filter(PublicationTask.parent_feature == "storyboard")
        .filter(or_(
            PublicationTask.report_visual_scheme_key == scheme.key,
            *([PublicationTask.report_visual_scheme_key.is_(None)] if scheme.key == DEFAULT_VISUAL_SCHEME_KEY else []),
        ))
        .filter(or_(PublicationTask.user_id.in_(_visible_user_ids(current_user)), PublicationTask.user_id.is_(None)))
        .order_by(PublicationTask.created_at.desc())
        .first()
    )
    if not latest_id:
        # Compatibility for storyboard rows created before parent_feature was projected.
        latest_id = (
            db.session.query(ReportVisualPlanOutput.task_id)
            .join(PublicationTask, PublicationTask.id == ReportVisualPlanOutput.task_id)
            .filter(ReportVisualPlanOutput.source_task_id == source.id)
            .filter(ReportVisualPlanOutput.schema_version == STORYBOARD_SCHEMA_VERSION)
            .filter(ReportVisualPlanOutput.visual_scheme_key == scheme.key)
            .filter(or_(PublicationTask.user_id.in_(_visible_user_ids(current_user)), PublicationTask.user_id.is_(None)))
            .order_by(ReportVisualPlanOutput.created_at.desc())
            .first()
        )
    if not latest_id:
        return jsonify({"success": True, "data": None})

    task = _get_owned_visual_task(current_user, latest_id[0], include_result=False)
    output = _storyboard_output(task.id)
    payload = _storyboard_job_payload(task, output, include_segments=bool(output))
    response_payload = {"success": True, "job_id": task.id, "status": payload.get("status"), "data": payload}
    if task.status == "completed" and output:
        _cache_terminal_storyboard(current_user, source_cache_key, response_payload)
        _cache_terminal_storyboard_job(current_user, task.id, response_payload)
    return jsonify(response_payload)


@report_visual_plan_bp.route("/jobs/<job_id>/gemini-raw", methods=["GET"])
@require_login
def get_storyboard_gemini_raw(current_user, job_id):
    task = _get_owned_visual_task(current_user, job_id, include_result=False)
    if not task:
        return jsonify({"success": False, "message": "分镜任务不存在"}), 404
    output = (
        ReportVisualPlanOutput.query.options(load_only(
            ReportVisualPlanOutput.gemini_output_path,
            ReportVisualPlanOutput.gemini_url,
        ))
        .filter(ReportVisualPlanOutput.task_id == task.id)
        .first()
    )
    root = (OUTPUT_ROOT / task.id).resolve()
    path = Path(output.gemini_output_path).resolve() if output and output.gemini_output_path else (
        root / "gemini_storyboard.json"
    ).resolve()
    if root not in path.parents or not path.is_file():
        return jsonify({"success": False, "message": "Gem 原始返回文件不存在或路径非法"}), 404
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return jsonify({"success": False, "message": f"读取 Gem 原始返回失败：{exc}"}), 500

    raw_text = str(stored.get("final_response_text") or stored.get("response_text") or "")
    if not raw_text.strip():
        return jsonify({"success": False, "message": "Gem 原始返回内容为空"}), 404
    return jsonify({
        "success": True,
        "data": {
            "job_id": task.id,
            "gemini_url": (
                output.gemini_url if output
                else visual_scheme(task.report_visual_scheme_key, require_configured=False).storyboard_gemini_url
            ),
            "completed_at": stored.get("completed_at"),
            "raw_text": raw_text,
        },
    })


def _storyboard_job_payload(task, output, *, include_segments=False):
    payload = {
        "job_id": task.id,
        "status": _status_label(task.status),
        "step": task.current_step,
        "progress": task.progress or 0,
        "title": task.task_title,
        "source_task_id": output.source_task_id if output else task.source_task_id,
        "visual_scheme": {
            "key": output.visual_scheme_key if output else (task.report_visual_scheme_key or DEFAULT_VISUAL_SCHEME_KEY),
            "name": output.visual_scheme_name if output else visual_scheme(task.report_visual_scheme_key, require_configured=False).name,
            "image_profile": output.image_style_profile if output else visual_scheme(task.report_visual_scheme_key, require_configured=False).image_profile_version,
        },
        "schema_version": output.schema_version if output else STORYBOARD_SCHEMA_VERSION,
        "segment_count": output.segment_count if output else 0,
        "source_script_chars": output.source_script_chars if output else 0,
        "covered_script_chars": output.covered_script_chars if output else 0,
        "coverage_complete": bool(output and output.source_script_chars == output.covered_script_chars),
        "gemini_url": output.gemini_url if output else visual_scheme(task.report_visual_scheme_key, require_configured=False).storyboard_gemini_url,
        "normalizer_provider": output.normalizer_provider if output else None,
        "normalizer_model": output.normalizer_model if output else None,
        "created_at": _isoformat_z(task.created_at),
        "completed_at": _isoformat_z(task.completed_at),
        "error": task.error_message,
    }
    if include_segments and output:
        rows = (
            db.session.query(
                ReportVisualPlanSegment.sequence,
                ReportVisualPlanSegment.scene_title,
                ReportVisualPlanSegment.script_text,
                ReportVisualPlanSegment.visual_prompt,
                ReportVisualPlanSegment.estimated_seconds,
            )
            .filter(ReportVisualPlanSegment.task_id == task.id)
            .order_by(ReportVisualPlanSegment.sequence.asc())
            .all()
        )
        payload["segments"] = [{
            "sequence": row.sequence,
            "scene_title": row.scene_title,
            "script_text": row.script_text,
            "visual_prompt": row.visual_prompt,
            "estimated_seconds": row.estimated_seconds,
        } for row in rows]
    return payload


def _storyboard_output(task_id):
    return (
        ReportVisualPlanOutput.query.options(load_only(
            ReportVisualPlanOutput.task_id,
            ReportVisualPlanOutput.source_task_id,
            ReportVisualPlanOutput.schema_version,
            ReportVisualPlanOutput.source_script_chars,
            ReportVisualPlanOutput.covered_script_chars,
            ReportVisualPlanOutput.segment_count,
            ReportVisualPlanOutput.visual_scheme_key,
            ReportVisualPlanOutput.visual_scheme_name,
            ReportVisualPlanOutput.gemini_url,
            ReportVisualPlanOutput.image_style_profile,
            ReportVisualPlanOutput.normalizer_provider,
            ReportVisualPlanOutput.normalizer_model,
        ))
        .filter(ReportVisualPlanOutput.task_id == str(task_id))
        .first()
    )


@report_visual_plan_bp.route("/jobs/<job_id>/asset/<path:filename>", methods=["GET"])
@require_login
def get_asset(current_user, job_id, filename):
    task = _get_owned_visual_task(current_user, job_id, include_result=True)
    if not task:
        return jsonify({"success": False, "message": "visual_plan 任务不存在"}), 404
    safe_name = Path(filename).name
    path = (OUTPUT_ROOT / task.id / safe_name).resolve()
    root = (OUTPUT_ROOT / task.id).resolve()
    if root not in path.parents and path != root:
        return jsonify({"success": False, "message": "非法文件路径"}), 400
    if not path.exists() or not path.is_file():
        return jsonify({"success": False, "message": "预览文件不存在"}), 404
    return send_file(path, as_attachment=False)


def _resolve_material_input(data, current_user):
    material_job_id = str(data.get("material_job_id") or data.get("job_id") or "").strip()
    asset_id = str(data.get("asset_id") or data.get("pdf_asset_id") or "").strip()
    material_task = None
    if material_job_id:
        material_task = (
            PublicationTask.query.options(load_only(PublicationTask.id, PublicationTask.feature, PublicationTask.user_id, PublicationTask.params, PublicationTask.result, PublicationTask.status))
            .filter(PublicationTask.id == material_job_id)
            .filter(PublicationTask.feature.in_(MATERIAL_STATUS_FEATURES))
            .filter(or_(PublicationTask.user_id.in_(_visible_user_ids(current_user)), PublicationTask.user_id.is_(None)))
            .first()
        )
    elif asset_id:
        material_task = _latest_material_task_for_asset(asset_id, current_user=current_user, include_result=True)
    else:
        raise ValueError("请提供 asset_id 或 material_job_id")

    if not material_task:
        raise ValueError("这份 PDF 暂无可复用的基础文案")

    material_payload = _material_task_payload(material_task)
    if not str(material_payload.get("final_script_for_tts") or "").strip():
        raise ValueError("基础文案任务还没有 final_script_for_tts")

    pdf_path = _pdf_path_from_material_task(material_task)
    if not pdf_path and asset_id:
        try:
            pdf_meta = _pdf_meta_from_zsxq_attachment(asset_id)
            pdf_path = Path(pdf_meta["path"])
            material_payload.setdefault("pdf", {}).update({key: value for key, value in pdf_meta.items() if key != "path"})
        except ValueError:
            pdf_path = None
    return material_task, material_payload, pdf_path


def _pdf_path_from_material_task(task):
    candidates = []
    for payload in (task.params if isinstance(task.params, dict) else {}, task.result if isinstance(task.result, dict) else {}):
        for key in (task.feature, "report_script_material_workflow", "report_audio_workflow", "report_pdf_audio_workflow"):
            value = payload.get(key) if isinstance(payload.get(key), dict) else None
            pdf = value.get("pdf") if isinstance(value, dict) and isinstance(value.get("pdf"), dict) else None
            if pdf:
                candidates.append(pdf.get("path") or pdf.get("local_path"))
        pdf = payload.get("pdf") if isinstance(payload.get("pdf"), dict) else None
        if pdf:
            candidates.append(pdf.get("path") or pdf.get("local_path"))
    for candidate in candidates:
        path = _resolve_pdf_path(candidate)
        if path:
            return path
    return None


def _create_visual_plan_task(material_task, material_payload, current_user):
    user_id = str(getattr(current_user, "id", "") or "")
    pdf = material_payload.get("pdf") if isinstance(material_payload.get("pdf"), dict) else {}
    params = {
        "feature": FEATURE_NAME,
        "user_id": user_id,
        "source_task_id": material_task.id,
        "source_feature": material_task.feature,
        FEATURE_NAME: {
            "material_task_id": material_task.id,
            "material_feature": material_task.feature,
            "pdf": {key: pdf.get(key) for key in ("attachment_id", "filename", "topic_title", "tab_label", "preview_url") if pdf.get(key) is not None},
        },
    }
    task = PublicationTask(
        status="processing",
        current_step="init",
        progress=5,
        params=params,
        result={"schema_version": SCHEMA_VERSION, "visual_plan": {"status": "pending", "schema_version": SCHEMA_VERSION}},
    )
    task.task_title = _visual_task_title(material_payload)
    task.report_audio_pdf_attachment_id = pdf.get("attachment_id")
    task.report_audio_pdf_filename = pdf.get("filename")
    task.report_audio_pdf_topic_title = pdf.get("topic_title")
    return task


def _latest_visual_plan_task(material_task_id, current_user):
    candidates = (
        PublicationTask.query.options(load_only(PublicationTask.id, PublicationTask.status, PublicationTask.result, PublicationTask.params, PublicationTask.created_at, PublicationTask.completed_at, PublicationTask.task_title, PublicationTask.progress, PublicationTask.current_step, PublicationTask.error_message, PublicationTask.report_audio_pdf_filename, PublicationTask.report_audio_pdf_topic_title))
        .filter(PublicationTask.feature == FEATURE_NAME)
        .filter(PublicationTask.source_task_id == material_task_id)
        .filter(PublicationTask.status == "completed")
        .filter(or_(PublicationTask.user_id.in_(_visible_user_ids(current_user)), PublicationTask.user_id.is_(None)))
        .order_by(PublicationTask.completed_at.desc().nullslast(), PublicationTask.created_at.desc())
        .limit(10)
        .all()
    )
    for task in candidates:
        if _matches_schema_version(task.result):
            return task
    return None


def _get_owned_visual_task(current_user, job_id, include_result=False):
    columns = [
        PublicationTask.id,
        PublicationTask.status,
        PublicationTask.current_step,
        PublicationTask.progress,
        PublicationTask.error_message,
        PublicationTask.feature,
        PublicationTask.source_task_id,
        PublicationTask.parent_feature,
        PublicationTask.report_visual_scheme_key,
        PublicationTask.task_title,
        PublicationTask.created_at,
        PublicationTask.updated_at,
        PublicationTask.completed_at,
    ]
    if include_result:
        columns.extend((
            PublicationTask.source_feature,
            PublicationTask.report_audio_pdf_attachment_id,
            PublicationTask.report_audio_pdf_filename,
            PublicationTask.report_audio_pdf_topic_title,
            PublicationTask.report_audio_output_path,
            PublicationTask.params,
            PublicationTask.result,
        ))
    return (
        PublicationTask.query.options(load_only(*columns))
        .filter(PublicationTask.id == str(job_id))
        .filter(PublicationTask.feature == FEATURE_NAME)
        .filter(or_(PublicationTask.user_id.in_(_visible_user_ids(current_user)), PublicationTask.user_id.is_(None)))
        .first()
    )


def _job_payload(task, include_detail=False):
    result = task.result if isinstance(task.result, dict) else {}
    params = task.params if isinstance(task.params, dict) else {}
    assets = []
    for asset in result.get("preview_assets") or []:
        if not isinstance(asset, dict):
            continue
        filename = Path(str(asset.get("filename") or "")).name
        if not filename:
            continue
        assets.append({
            **asset,
            "url": f"/api/report-visual-plans/jobs/{task.id}/asset/{filename}",
        })
    payload = {
        "job_id": task.id,
        "status": _status_label(task.status),
        "step": task.current_step,
        "progress": task.progress or 0,
        "title": task.task_title or task.report_audio_pdf_topic_title or task.report_audio_pdf_filename or "研报视觉编排",
        "source_task_id": task.source_task_id,
        "source_feature": task.source_feature,
        "created_at": _isoformat_z(task.created_at),
        "updated_at": _isoformat_z(task.updated_at),
        "completed_at": _isoformat_z(task.completed_at),
        "error": task.error_message or result.get("error"),
        "provider": result.get("provider"),
        "provider_error": result.get("provider_error"),
        "schema_version": result.get("schema_version") or (result.get("visual_plan") or {}).get("schema_version"),
        "preview_assets": assets,
        "material": result.get("material") or ((params.get(FEATURE_NAME) or {}) if isinstance(params.get(FEATURE_NAME), dict) else {}),
    }
    if include_detail:
        payload["visual_plan"] = result.get("visual_plan") if isinstance(result.get("visual_plan"), dict) else None
        payload["result"] = result
    return payload


def _material_summary(material):
    pdf = material.get("pdf") if isinstance(material.get("pdf"), dict) else {}
    return {
        "title": material.get("title"),
        "script_chars": material.get("script_chars") or len(str(material.get("final_script_for_tts") or "")),
        "pdf": {key: pdf.get(key) for key in ("attachment_id", "filename", "topic_title", "tab_label", "preview_url") if pdf.get(key) is not None},
    }


def _visual_task_title(material):
    pdf = material.get("pdf") if isinstance(material.get("pdf"), dict) else {}
    return str(material.get("title") or pdf.get("topic_title") or pdf.get("filename") or "研报视觉编排").strip()[:500]


def _first_asset_path(result):
    for asset in result.get("preview_assets") or []:
        if isinstance(asset, dict) and asset.get("path"):
            return str(asset.get("path"))[:600]
    return None


def _matches_schema_version(result):
    if not isinstance(result, dict):
        return False
    if result.get("schema_version") == SCHEMA_VERSION:
        return True
    plan = result.get("visual_plan") if isinstance(result.get("visual_plan"), dict) else {}
    return plan.get("schema_version") == SCHEMA_VERSION


def _status_label(status):
    return {
        "pending": "queued",
        "processing": "running",
        "completed": "succeeded",
        "failed": "failed",
        "canceled": "canceled",
    }.get(status or "", status or "queued")


def _truthy(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled", "y"}


def _isoformat_z(value):
    return value.isoformat() + "Z" if hasattr(value, "isoformat") else value
