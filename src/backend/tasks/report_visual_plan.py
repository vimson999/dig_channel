import json
import logging
import os
from datetime import datetime
from pathlib import Path

from PIL import Image
from sqlalchemy.exc import OperationalError
from sqlalchemy import or_
from sqlalchemy.orm import load_only

from app import create_app
from celery_config import celery_app
from models import db, ReportVisualPlanOutput, ReportVisualPlanSegment
from models.task import PublicationTask
from services.gemini_browser import (
    GeminiBrowserAutomationError,
    GeminiBrowserHumanInterventionError,
    GeminiBrowserOptions,
    GeminiBrowserWorker,
)
from services.report_visual_plan_service import (
    FEATURE_NAME,
    OUTPUT_ROOT,
    QUEUE_NAME,
    STORYBOARD_SCHEMA_VERSION,
    normalize_storyboard_response,
    storyboard_response_covers_script,
    storyboard_prompt,
)
from services.report_audio_workflow import FEATURE_NAME as REPORT_AUDIO_WORKFLOW_FEATURE
from services.report_pdf_evidence import link_storyboard_evidence
from services.report_stick_figure_assets import scene_image_path
from services.report_visual_plan_integrity import durable_storyboard_integrity
from services.report_visual_scheme import DEFAULT_VISUAL_SCHEME_KEY, visual_scheme
from services.runtime_guard import maybe_guarded_apply_async, release_db_session_quietly


logger = logging.getLogger("REPORT_VISUAL_PLAN_TASK")
PROCESSING_SCRIPT_READY_FEATURES = frozenset((
    REPORT_AUDIO_WORKFLOW_FEATURE,
    "report_audio_workflow",
))
VISUAL_RETRY_LIMIT = 3
VISUAL_PROFILE_RETRY_LIMIT = 12
VISUAL_PROFILE_RETRY_BACKOFF_MAX_SECONDS = 120
VISUAL_RETRYABLE_ERROR_FRAGMENTS = (
    "profile 正在被其他 chrome 占用",
    "等待 gemini 回复超时",
    "页面自动化超时",
    "browser has been closed",
    "target page, context or browser has been closed",
    "target closed",
    "protocol error",
    "net::err_",
    "未找到 gemini 输入框",
    "提示词未成功提交",
)


def _storyboard_gemini_worker():
    """Use the proven long-form Gemini budget for multi-turn storyboard output."""
    options = GeminiBrowserOptions.from_env()
    # The profile-pool launcher owns isolated automation profiles.  If one of
    # those profiles is orphaned by a worker restart, allow the browser layer
    # to reclaim only that exact automation Chrome process.  A custom/manual
    # profile remains opt-in via the explicit environment variable.
    pool_profile = "report_visual_plan_gemini_pool" in str(options.user_data_dir or "")
    options.auto_recover_orphaned_chrome = _truthy_env(
        "REPORT_VISUAL_PLAN_AUTO_RECOVER_ORPHANED_CHROME",
        pool_profile,
    )
    response_floor = max(1800, int(options.response_timeout_seconds or 0))
    total_floor = max(7200, response_floor * 3 + 300)
    options.response_timeout_seconds = _positive_env_int(
        "REPORT_VISUAL_PLAN_GEMINI_RESPONSE_TIMEOUT",
        response_floor,
    )
    options.total_timeout_seconds = _positive_env_int(
        "REPORT_VISUAL_PLAN_GEMINI_TOTAL_TIMEOUT",
        max(total_floor, options.response_timeout_seconds * 3 + 300),
    )
    return GeminiBrowserWorker(options)


def _positive_env_int(name, default):
    raw = str(os.getenv(name) or "").strip()
    if not raw:
        return int(default)
    try:
        return max(1, int(raw))
    except ValueError:
        return int(default)


def _human_intervention_details(task):
    workflow_id = str(getattr(task, "parent_task_id", "") or "").strip()
    if (
        not workflow_id
        or str(getattr(task, "parent_feature", "") or "")
        != "report_stick_figure_video_workflow"
    ):
        return {}
    base_url = str(
        os.getenv("REPORT_WORKFLOW_FRONTEND_BASE_URL") or "http://localhost:5001"
    ).strip().rstrip("/")
    return {
        "工作流详情": (
            f"{base_url}/report-stick-figure-video-workflow-detail.html"
            f"?workflow_job_id={workflow_id}"
        ),
    }


def _truthy_env(name, default=False):
    raw = str(os.getenv(name) or "").strip().lower()
    if not raw:
        return bool(default)
    return raw not in {"0", "false", "no", "off"}


def _is_profile_contention_error(error):
    text = str(error or "").lower()
    return "profile 正在被其他 chrome 占用" in text


def _visual_retry_limit(error):
    if _is_profile_contention_error(error):
        return _positive_env_int(
            "REPORT_VISUAL_PLAN_PROFILE_RETRY_LIMIT",
            VISUAL_PROFILE_RETRY_LIMIT,
        )
    return VISUAL_RETRY_LIMIT


def _visual_retry_delay_seconds(error, retry_index):
    delay = 30 * max(1, int(retry_index or 1))
    if _is_profile_contention_error(error):
        ceiling = _positive_env_int(
            "REPORT_VISUAL_PLAN_PROFILE_RETRY_BACKOFF_MAX_SECONDS",
            VISUAL_PROFILE_RETRY_BACKOFF_MAX_SECONDS,
        )
    else:
        ceiling = 120
    return min(ceiling, delay)


def _material_source_ready(source):
    """Accept completed material, or a script-ready audio workflow awaiting/recovering output."""
    return bool(
        source
        and int(source.final_script_chars or 0) > 0
        and (
            source.status == "completed"
            or (
                source.feature in PROCESSING_SCRIPT_READY_FEATURES
                and source.status in {"processing", "failed"}
            )
        )
    )


@celery_app.task(
    bind=True,
    name="tasks.report_visual_plan.process_job",
    store_errors_even_if_ignored=True,
    autoretry_for=(OperationalError,),
    retry_backoff=True,
    retry_jitter=True,
    retry_kwargs={"max_retries": 3},
)
def process_report_visual_plan_job(self, task_id):
    app = create_app(register_apis=False)
    with app.app_context():
        return _process_job(str(task_id), celery_task=self)


@celery_app.task(
    bind=True,
    name="tasks.report_visual_plan.retry_normalize_job",
    store_errors_even_if_ignored=True,
    autoretry_for=(OperationalError,),
    retry_backoff=True,
    retry_jitter=True,
    retry_kwargs={"max_retries": 3},
)
def retry_normalize_report_visual_plan_job(self, task_id):
    app = create_app(register_apis=False)
    with app.app_context():
        return _retry_normalize_job(str(task_id))


def enqueue_report_visual_plan_for_material(
    material_task_id,
    *,
    user_id="",
    parent_feature="storyboard",
    parent_task_id="",
    visual_scheme_key=DEFAULT_VISUAL_SCHEME_KEY,
):
    """Idempotently reuse or enqueue one storyboard for a script-ready source task."""
    material_task_id = str(material_task_id or "").strip()
    if not material_task_id:
        raise ValueError("基础文案任务 ID 不能为空")
    scheme = visual_scheme(visual_scheme_key)
    source = (
        PublicationTask.query.options(load_only(
            PublicationTask.id,
            PublicationTask.feature,
            PublicationTask.status,
            PublicationTask.final_script_chars,
            PublicationTask.user_id,
            PublicationTask.task_title,
            PublicationTask.report_audio_pdf_attachment_id,
            PublicationTask.report_audio_pdf_filename,
            PublicationTask.report_audio_pdf_topic_title,
        ))
        .filter(PublicationTask.id == material_task_id)
        .one_or_none()
    )
    if not _material_source_ready(source):
        raise ValueError("基础文案任务还没有完整文案")

    existing_candidates = (
        db.session.query(PublicationTask.id)
        .join(ReportVisualPlanOutput, ReportVisualPlanOutput.task_id == PublicationTask.id)
        .filter(ReportVisualPlanOutput.source_task_id == material_task_id)
        .filter(ReportVisualPlanOutput.schema_version == STORYBOARD_SCHEMA_VERSION)
        .filter(ReportVisualPlanOutput.visual_scheme_key == scheme.key)
        .order_by(ReportVisualPlanOutput.created_at.desc())
        .limit(5)
        .all()
    )
    for existing in existing_candidates:
        existing_id = str(existing.id)
        if durable_storyboard_integrity(existing_id):
            _reconcile_durable_storyboard_success(existing_id)
            return {"job_id": existing_id, "status": "completed", "cached": True}

    active = (
        PublicationTask.query.options(load_only(
            PublicationTask.id,
            PublicationTask.status,
            PublicationTask.current_step,
        ))
        .filter(
            PublicationTask.feature == FEATURE_NAME,
            PublicationTask.source_task_id == material_task_id,
            PublicationTask.status.in_(["pending", "processing"]),
        )
        .filter(
            or_(
                PublicationTask.report_visual_scheme_key == scheme.key,
                *(
                    [PublicationTask.report_visual_scheme_key.is_(None)]
                    if scheme.key == DEFAULT_VISUAL_SCHEME_KEY else []
                ),
            )
        )
        .order_by(PublicationTask.created_at.desc())
        .first()
    )
    if active:
        return {"job_id": str(active.id), "status": str(active.status), "cached": True}

    task = PublicationTask(
        status="pending",
        current_step="queued",
        progress=0,
        params={
            "feature": FEATURE_NAME,
            "user_id": str(user_id or source.user_id or ""),
            "source_task_id": material_task_id,
            "source_feature": source.feature,
            "parent_feature": str(parent_feature or "storyboard"),
            "parent_task_id": str(parent_task_id or "") or None,
            "visual_scheme_key": scheme.key,
            FEATURE_NAME: {
                "schema_version": STORYBOARD_SCHEMA_VERSION,
                "visual_scheme_key": scheme.key,
            },
        },
        result={"schema_version": STORYBOARD_SCHEMA_VERSION},
    )
    task.task_title = source.task_title or source.report_audio_pdf_topic_title or source.report_audio_pdf_filename or "研报分镜"
    task.report_visual_scheme_key = scheme.key
    task.report_audio_pdf_attachment_id = source.report_audio_pdf_attachment_id
    task.report_audio_pdf_filename = source.report_audio_pdf_filename
    task.report_audio_pdf_topic_title = source.report_audio_pdf_topic_title
    db.session.add(task)
    db.session.flush()
    task_id = str(task.id)
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
        raise RuntimeError(f"分镜任务入队失败: {exc}") from exc
    PublicationTask.query.filter(PublicationTask.id == task_id).update(
        {PublicationTask.celery_task_id: async_result.id},
        synchronize_session=False,
    )
    db.session.commit()
    return {
        "job_id": task_id,
        "status": "deferred" if submit_result.get("deferred") else "pending",
        "cached": False,
    }


def _reconcile_duplicate_scene_images(task_id):
    """Resume the image fan-out for an already durable storyboard delivery."""

    _dispatch_auto_scene_images(task_id)


def _process_job(task_id, celery_task=None):
    task = (
        PublicationTask.query.options(load_only(
            PublicationTask.id, PublicationTask.status, PublicationTask.feature,
            PublicationTask.celery_task_id,
            PublicationTask.source_task_id, PublicationTask.user_id,
            PublicationTask.parent_task_id, PublicationTask.parent_feature,
            PublicationTask.report_visual_scheme_key,
        ))
        .filter(PublicationTask.id == task_id)
        .first()
    )
    if not task or task.feature != FEATURE_NAME:
        release_db_session_quietly()
        return False
    request_id = str(
        getattr(getattr(celery_task, "request", None), "id", "") or ""
    )
    current_celery_id = str(task.celery_task_id or "")
    if request_id and current_celery_id and request_id != current_celery_id:
        logger.info(
            "Report visual plan received stale delivery; task=%s request=%s current=%s",
            task_id,
            request_id,
            current_celery_id,
        )
        release_db_session_quietly()
        return False
    if task.status == "canceled":
        release_db_session_quietly()
        return False
    if durable_storyboard_integrity(task_id):
        _reconcile_durable_storyboard_success(task_id)
        try:
            _reconcile_duplicate_scene_images(task_id)
        except Exception as exc:
            logger.error(
                "Durable storyboard duplicate delivery could not reconcile images: %s",
                exc,
                exc_info=True,
            )
            _sync_parent_image_dispatch_failure(task_id, str(exc))
        return True
    if task.status in {"failed", "blocked_by_captcha", "blocked_by_login"}:
        release_db_session_quietly()
        return False
    source_task_id = str(task.source_task_id or "")
    human_intervention_details = _human_intervention_details(task)
    scheme = visual_scheme(task.report_visual_scheme_key or DEFAULT_VISUAL_SCHEME_KEY)
    source = (
        PublicationTask.query.options(load_only(
            PublicationTask.id, PublicationTask.feature, PublicationTask.params,
            PublicationTask.result, PublicationTask.task_title,
            PublicationTask.report_audio_pdf_filename,
            PublicationTask.report_audio_pdf_topic_title,
        ))
        .filter(PublicationTask.id == source_task_id)
        .first()
    )
    if not source:
        return _fail(task_id, "基础文案任务不存在")

    from api.report_script_material_workflow import _material_task_payload
    material = _material_task_payload(source)
    try:
        prompt = storyboard_prompt(material)
    except Exception as exc:
        return _fail(task_id, str(exc))
    task_title = str(material.get("title") or source.task_title or "研报分镜").strip()[:500]
    output_dir = Path(OUTPUT_ROOT) / task_id
    output_dir.mkdir(parents=True, exist_ok=True)
    gemini_path = output_dir / "gemini_storyboard.json"
    normalized_path = output_dir / "storyboard_segments.json"
    embedded_image_dir = output_dir / "gemini_embedded_images"
    release_db_session_quietly()

    def progress(event):
        mapped = min(68, max(5, int(event.get("progress") or 0) * 68 // 100))
        _update_status(task_id, event.get("step") or "gemini", mapped)

    try:
        _update_status(task_id, "gemini_storyboard", 5, status="processing")
        gemini_result = _storyboard_gemini_worker().generate(
            job_id=task_id,
            prompt=prompt,
            output_path=str(gemini_path),
            target_url=scheme.storyboard_gemini_url,
            followup_prompts=[],
            embedded_image_output_dir=str(embedded_image_dir),
            recover_image_only_storyboard=True,
            response_complete_callback=lambda text: storyboard_response_covers_script(material, text),
            incomplete_response_followup_prompt=(
                "你刚才没有覆盖完最初提供的完整口播文案。"
                "请检查已经输出的所有分镜：凡是存在【文案原句】但缺少【AI 提示词】的分镜，先按原编号补齐完整四字段；"
                "然后从上一次最后一个完整【文案原句】之后继续输出尚未覆盖的剩余分镜，"
                "保持原文顺序且逐字复制，不要重复已经完整记录的内容；"
                "每个分镜必须继续包含【文案原句】【画面动作】【视觉隐喻】【AI 提示词】，"
                "即使上一轮出现【全文分镜完成】但仍有字段缺失，也必须补齐后再结束；"
                "本轮仍然只允许输出文字，严禁调用任何图片生成、绘图或图片编辑工具，"
                "不得生成、插入或展示图片。"
            ),
            human_intervention_details=human_intervention_details,
            progress_callback=progress,
        )
        raw_text = str(gemini_result.get("final_response_text") or gemini_result.get("response_text") or "").strip()
        _update_status(task_id, "normalize_storyboard", 72)
        normalized = normalize_storyboard_response(material, raw_text)
        normalized_path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            _persist_success(
                task_id,
                source_task_id=source_task_id,
                title=task_title,
                gemini_path=str(gemini_path),
                normalized_path=str(normalized_path),
                normalized=normalized,
                scheme=scheme,
            )
        except Exception as persist_exc:
            if not _reconcile_durable_storyboard_success(task_id, persist_exc):
                raise
        try:
            _dispatch_auto_scene_images(task_id)
        except Exception as exc:
            logger.error("Auto scene image dispatch failed: %s", exc, exc_info=True)
            _sync_parent_image_dispatch_failure(task_id, str(exc))
        return True
    except Exception as exc:
        logger.error("Report visual plan failed: %s", exc, exc_info=True)
        if isinstance(exc, GeminiBrowserHumanInterventionError):
            return _block_for_human_intervention(task_id, exc)
        if _should_retry_visual_plan(celery_task, exc):
            retry_index = int(
                getattr(getattr(celery_task, "request", None), "retries", 0) or 0
            ) + 1
            retry_limit = _visual_retry_limit(exc)
            delay = _visual_retry_delay_seconds(str(exc), retry_index)
            _mark_visual_plan_retrying(
                task_id,
                str(exc),
                retry_index=retry_index,
                delay_seconds=delay,
                retry_limit=retry_limit,
            )
            raise celery_task.retry(
                exc=GeminiBrowserAutomationError(str(exc)),
                countdown=delay,
                max_retries=retry_limit,
            )
        return _fail(task_id, str(exc))
    finally:
        release_db_session_quietly()


def _should_retry_visual_plan(celery_task, exc):
    if not celery_task or not hasattr(celery_task, "retry"):
        return False
    retries = int(
        getattr(getattr(celery_task, "request", None), "retries", 0) or 0
    )
    if retries >= _visual_retry_limit(exc):
        return False
    text = str(exc or "").lower()
    if any(marker in text for marker in ("登录态失效", "验证码", "captcha", "额度已耗尽")):
        return False
    return any(marker in text for marker in VISUAL_RETRYABLE_ERROR_FRAGMENTS)


def _mark_visual_plan_retrying(
    task_id,
    error,
    *,
    retry_index,
    delay_seconds,
    retry_limit=None,
):
    now = datetime.utcnow()
    task = PublicationTask.query.options(load_only(
        PublicationTask.id,
        PublicationTask.result,
        PublicationTask.parent_task_id,
        PublicationTask.parent_feature,
    )).filter(PublicationTask.id == str(task_id)).one_or_none()
    if not task:
        return
    result = dict(task.result or {})
    history = list(result.get("auto_retry_history") or [])
    history.append({
        "attempt": int(retry_index),
        "error": str(error)[:1000],
        "delay_seconds": int(delay_seconds),
        "at": now.isoformat() + "Z",
    })
    retry_limit = int(retry_limit or _visual_retry_limit(error))
    retry_message = (
        f"自动修复中（第 {int(retry_index)}/{retry_limit} 次）："
        f"{str(error)[:3600]}"
    )
    result["auto_retry_history"] = history[-retry_limit:]
    result["last_retry_error"] = str(error)[:1000]
    result["retry_limit"] = retry_limit
    PublicationTask.query.filter(
        PublicationTask.id == str(task_id),
        PublicationTask.status != "canceled",
    ).update({
        PublicationTask.status: "pending",
        PublicationTask.current_step: "auto_retry",
        PublicationTask.error_message: retry_message,
        PublicationTask.completed_at: None,
        PublicationTask.result: result,
        PublicationTask.updated_at: now,
    }, synchronize_session=False)
    workflow_id = str(task.parent_task_id or "").strip()
    if workflow_id and str(task.parent_feature or "") == "report_stick_figure_video_workflow":
        PublicationTask.query.filter(
            PublicationTask.id == workflow_id,
            PublicationTask.status != "canceled",
        ).update({
            PublicationTask.status: "processing",
            PublicationTask.current_step: "storyboard_retry_wait",
            PublicationTask.waiting_child_status: "pending",
            PublicationTask.waiting_child_step: "auto_retry",
            PublicationTask.waiting_child_updated_at: now.isoformat() + "Z",
            PublicationTask.error_message: retry_message,
            PublicationTask.completed_at: None,
            PublicationTask.updated_at: now,
        }, synchronize_session=False)
    db.session.commit()


def _retry_normalize_job(task_id):
    task = (
        PublicationTask.query.options(load_only(
            PublicationTask.id,
            PublicationTask.status,
            PublicationTask.feature,
            PublicationTask.source_task_id,
            PublicationTask.report_visual_scheme_key,
        ))
        .filter(PublicationTask.id == task_id)
        .first()
    )
    if not task or task.feature != FEATURE_NAME or task.status == "canceled":
        release_db_session_quietly()
        return False
    source_task_id = str(task.source_task_id or "")
    scheme = visual_scheme(task.report_visual_scheme_key or DEFAULT_VISUAL_SCHEME_KEY)
    source = (
        PublicationTask.query.options(load_only(
            PublicationTask.id,
            PublicationTask.feature,
            PublicationTask.params,
            PublicationTask.result,
            PublicationTask.task_title,
            PublicationTask.report_audio_pdf_filename,
            PublicationTask.report_audio_pdf_topic_title,
        ))
        .filter(PublicationTask.id == source_task_id)
        .first()
    )
    if not source:
        return _fail(task_id, "基础文案任务不存在")

    from api.report_script_material_workflow import _material_task_payload
    material = _material_task_payload(source)
    task_title = str(material.get("title") or source.task_title or "研报分镜").strip()[:500]
    output_dir = Path(OUTPUT_ROOT) / task_id
    gemini_path = output_dir / "gemini_storyboard.json"
    normalized_path = output_dir / "storyboard_segments.json"
    if not gemini_path.is_file():
        return _fail(task_id, "Gem 原始返回文件不存在，无法续跑梳理")
    try:
        stored = json.loads(gemini_path.read_text(encoding="utf-8"))
        raw_text = _checkpoint_storyboard_text(stored)
    except (OSError, ValueError) as exc:
        return _fail(task_id, f"读取 Gem 原始返回失败：{exc}")
    if not raw_text:
        return _fail(task_id, "Gem 原始返回为空，无法续跑梳理")
    release_db_session_quietly()

    try:
        _update_status(task_id, "normalize_storyboard", 72, status="processing")
        PublicationTask.query.filter(PublicationTask.id == task_id).update({
            PublicationTask.error_message: None,
            PublicationTask.completed_at: None,
        }, synchronize_session=False)
        db.session.commit()
        normalized = normalize_storyboard_response(material, raw_text)
        normalized_path.write_text(
            json.dumps(normalized, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        try:
            _persist_success(
                task_id,
                source_task_id=source_task_id,
                title=task_title,
                gemini_path=str(gemini_path),
                normalized_path=str(normalized_path),
                normalized=normalized,
                scheme=scheme,
            )
        except Exception as persist_exc:
            if not _reconcile_durable_storyboard_success(task_id, persist_exc):
                raise
        try:
            _dispatch_auto_scene_images(task_id)
        except Exception as exc:
            logger.error("Auto scene image dispatch after normalize retry failed: %s", exc, exc_info=True)
            _sync_parent_image_dispatch_failure(task_id, str(exc))
        return True
    except Exception as exc:
        logger.error("Report visual plan normalize retry failed: %s", exc, exc_info=True)
        return _fail(task_id, str(exc))
    finally:
        release_db_session_quietly()


def _checkpoint_storyboard_text(stored):
    """Prefer the first self-contained completed turn over repeated continuations."""
    turns = stored.get("turns") if isinstance(stored, dict) else None
    if isinstance(turns, list):
        for turn in turns:
            if not isinstance(turn, dict):
                continue
            text = str(turn.get("response_text") or "").strip()
            if (
                "【全文分镜完成】" in text
                and "标题镜头" in text
                and "【AI 提示词】" in text
            ):
                return text
        storyboard_parts = []
        known_storyboard_kinds = {
            "initial",
            "image_only_recovery",
            "incomplete_storyboard_continuation",
        }
        for turn in turns:
            if not isinstance(turn, dict):
                continue
            kind = str(turn.get("kind") or "").strip()
            if kind and kind not in known_storyboard_kinds:
                continue
            text = str(turn.get("response_text") or "").strip()
            if text:
                storyboard_parts.append(text)
        combined = "\n\n".join(storyboard_parts).strip()
        if (
            "标题镜头" in combined
            and "【文案原句】" in combined
            and "【AI 提示词】" in combined
        ):
            return combined
    return str(
        (stored.get("final_response_text") or stored.get("response_text") or "")
        if isinstance(stored, dict)
        else ""
    ).strip()


def _update_status(task_id, step, progress, status=None):
    values = {
        PublicationTask.current_step: str(step or "processing")[:80],
        PublicationTask.progress: max(0, min(99, int(progress or 0))),
    }
    if status:
        values[PublicationTask.status] = status
    PublicationTask.query.filter(
        PublicationTask.id == task_id,
        PublicationTask.status != "canceled",
    ).update(values, synchronize_session=False)
    db.session.commit()
    release_db_session_quietly()


def _persist_success(task_id, *, source_task_id, title, gemini_path, normalized_path, normalized, scheme):
    ReportVisualPlanSegment.query.filter(ReportVisualPlanSegment.task_id == task_id).delete(synchronize_session=False)
    ReportVisualPlanOutput.query.filter(ReportVisualPlanOutput.task_id == task_id).delete(synchronize_session=False)
    title_scene = normalized.get("title_scene")
    segments = ([title_scene] if isinstance(title_scene, dict) else []) + normalized["segments"]
    output = ReportVisualPlanOutput(
        task_id=task_id,
        source_task_id=source_task_id,
        schema_version=STORYBOARD_SCHEMA_VERSION,
        source_script_hash=normalized["source_script_hash"],
        source_script_chars=normalized["source_script_chars"],
        covered_script_chars=normalized["covered_script_chars"],
        segment_count=len(segments),
        visual_scheme_key=scheme.key,
        visual_scheme_name=scheme.name,
        gemini_url=scheme.storyboard_gemini_url,
        image_gemini_url=scheme.image_gemini_url,
        image_style_profile=scheme.image_profile_version,
        gemini_output_path=gemini_path,
        normalized_output_path=normalized_path,
        normalizer_provider=normalized.get("provider"),
        normalizer_model=normalized.get("model"),
    )
    db.session.add(output)
    for item in segments:
        db.session.add(ReportVisualPlanSegment(
            task_id=task_id,
            sequence=item["sequence"],
            scene_title=item.get("scene_title"),
            script_text=item["script_text"],
            visual_prompt=item["visual_prompt"],
            estimated_seconds=item.get("estimated_seconds"),
        ))
    db.session.flush()
    evidence_link_count = link_storyboard_evidence(task_id, commit=False)
    summary = {
        "schema_version": STORYBOARD_SCHEMA_VERSION,
        "segment_count": len(segments),
        "source_script_chars": normalized["source_script_chars"],
        "covered_script_chars": normalized["covered_script_chars"],
        "coverage_complete": True,
        "gemini_output_path": gemini_path,
        "normalized_output_path": normalized_path,
        "normalizer_model": normalized.get("model"),
        "visual_scheme_key": scheme.key,
        "visual_scheme_name": scheme.name,
        "evidence_status": output.evidence_status or "legacy_unavailable",
        "evidence_link_count": evidence_link_count,
    }
    PublicationTask.query.filter(PublicationTask.id == task_id).update({
        PublicationTask.status: "completed",
        PublicationTask.current_step: "done",
        PublicationTask.progress: 100,
        PublicationTask.completed_at: datetime.utcnow(),
        PublicationTask.error_message: None,
        PublicationTask.task_title: title,
        PublicationTask.result: summary,
    }, synchronize_session=False)
    db.session.commit()


def _reconcile_durable_storyboard_success(task_id, cause=None):
    """Repair stale failed state after an ambiguous successful commit.

    A failed/invalid Session must never be reused for the recovery read.  This
    helper is intentionally idempotent so operators and enqueue paths can also
    use it to repair historical rows without regenerating the storyboard.
    """
    release_db_session_quietly()
    integrity = durable_storyboard_integrity(task_id)
    if not integrity:
        release_db_session_quietly()
        return False
    now = datetime.utcnow()
    summary = {
        "schema_version": integrity["schema_version"],
        "segment_count": integrity["segment_count"],
        "source_script_chars": integrity["source_script_chars"],
        "covered_script_chars": integrity["covered_script_chars"],
        "coverage_complete": True,
        "gemini_output_path": integrity["gemini_output_path"],
        "normalized_output_path": integrity["normalized_output_path"],
        "normalizer_model": integrity["normalizer_model"],
        "visual_scheme_key": integrity["visual_scheme_key"],
        "visual_scheme_name": integrity["visual_scheme_name"],
        "evidence_status": integrity["evidence_status"],
    }
    PublicationTask.query.filter(
        PublicationTask.id == str(task_id),
        PublicationTask.status != "canceled",
    ).update({
        PublicationTask.status: "completed",
        PublicationTask.current_step: "done",
        PublicationTask.progress: 100,
        PublicationTask.completed_at: now,
        PublicationTask.error_message: None,
        PublicationTask.result: summary,
        PublicationTask.updated_at: now,
    }, synchronize_session=False)
    link = (
        PublicationTask.query.options(load_only(
            PublicationTask.parent_task_id,
            PublicationTask.parent_feature,
        ))
        .filter(PublicationTask.id == str(task_id))
        .one_or_none()
    )
    workflow_id = str(getattr(link, "parent_task_id", None) or "").strip()
    if workflow_id and str(getattr(link, "parent_feature", None) or "") == "report_stick_figure_video_workflow":
        PublicationTask.query.filter(
            PublicationTask.id == workflow_id,
            PublicationTask.status != "canceled",
        ).update({
            PublicationTask.status: "processing",
            PublicationTask.current_step: "storyboard_done",
            PublicationTask.progress: 60,
            PublicationTask.waiting_child_id: str(task_id),
            PublicationTask.waiting_child_label: "标题与正文分镜",
            PublicationTask.waiting_child_status: "completed",
            PublicationTask.waiting_child_step: "done",
            PublicationTask.waiting_child_updated_at: now.isoformat() + "Z",
            PublicationTask.error_message: None,
            PublicationTask.completed_at: None,
            PublicationTask.updated_at: now,
        }, synchronize_session=False)
    db.session.commit()
    logger.warning(
        "Reconciled durable storyboard after stale/ambiguous task state: task=%s workflow=%s cause=%s",
        task_id,
        workflow_id or None,
        str(cause)[:500] if cause else None,
    )
    release_db_session_quietly()
    return True


def _dispatch_auto_scene_images(storyboard_task_id, workflow_job_id=None):
    """Initialize and enqueue every scene image after a workflow storyboard succeeds."""
    storyboard_task = (
        PublicationTask.query.options(load_only(
            PublicationTask.id,
            PublicationTask.parent_task_id,
            PublicationTask.parent_feature,
        ))
        .filter(PublicationTask.id == str(storyboard_task_id))
        .one_or_none()
    )
    workflow_id = str(
        workflow_job_id or getattr(storyboard_task, "parent_task_id", None) or ""
    ).strip()
    if (
        not storyboard_task
        or not workflow_id
        or (
            not workflow_job_id
            and str(storyboard_task.parent_feature or "") != "report_stick_figure_video_workflow"
        )
    ):
        return {"workflow_job_id": None, "queued": 0, "skipped": 0}

    # Import lazily because the workflow API imports this task module while the
    # Flask app is bootstrapping.  At execution time the API module is complete.
    from api.report_stick_figure_video_workflow import (
        SCENE_PARENT_COLUMNS,
        _auto_enqueue_whole_audio_alignment,
        _enqueue_image_asset,
        _ensure_scene_assets,
        _scene_asset_enqueue_snapshot,
        _scene_parent_snapshot,
    )

    parent = (
        PublicationTask.query.options(load_only(*SCENE_PARENT_COLUMNS))
        .filter(
            PublicationTask.id == workflow_id,
            PublicationTask.feature == "report_stick_figure_video_workflow",
            PublicationTask.status != "canceled",
        )
        .one_or_none()
    )
    if not parent:
        return {"workflow_job_id": workflow_id, "queued": 0, "skipped": 0}

    parent_snapshot = _scene_parent_snapshot(parent)
    storyboard, assets = _ensure_scene_assets(parent_snapshot, force_refresh=True)
    if str(storyboard.id) != str(storyboard_task_id):
        raise RuntimeError("自动初始化图片时匹配到了另一版分镜")
    adopted = _adopt_storyboard_embedded_images(
        storyboard_task_id,
        workflow_id,
        assets,
    )

    queued = []
    skipped = []
    errors = []
    # Snapshot before the first enqueue commit. Otherwise SQLAlchemy expires
    # the remaining rows after every commit and turns a batch into N point reads.
    asset_snapshots = [_scene_asset_enqueue_snapshot(item) for item in assets]
    for asset in asset_snapshots:
        if asset.image_status in {"queued", "processing", "completed"}:
            skipped.append(asset.sequence)
            continue
        try:
            queued.append(_enqueue_image_asset(parent_snapshot, asset))
        except Exception as exc:
            db.session.rollback()
            errors.append({"sequence": asset.sequence, "message": str(exc)[:500]})

    now = datetime.utcnow()
    all_completed = bool(asset_snapshots) and all(
        item.image_status == "completed" for item in asset_snapshots
    )
    fatal_error = bool(errors) and not queued
    if _preserve_completed_video_state(
        parent_snapshot,
        all_completed=all_completed,
        queued=queued,
        errors=errors,
    ):
        logger.info(
            "Auto scene images left completed workflow unchanged: "
            "workflow=%s storyboard=%s skipped=%s",
            workflow_id,
            storyboard_task_id,
            len(skipped),
        )
        return {
            "workflow_job_id": workflow_id,
            "queued": len(queued),
            "skipped": len(skipped),
            "adopted": adopted,
            "errors": errors,
        }
    PublicationTask.query.filter(PublicationTask.id == workflow_id).update({
        PublicationTask.status: "failed" if fatal_error else "processing",
        PublicationTask.current_step: (
            "image_submit_failed" if fatal_error
            else ("images_done" if all_completed else "images_queued")
        ),
        PublicationTask.progress: 80 if all_completed else 65,
        PublicationTask.waiting_child_id: str(storyboard_task_id),
        PublicationTask.waiting_child_label: "分镜图片",
        PublicationTask.waiting_child_status: (
            "failed" if fatal_error else ("completed" if all_completed else "pending")
        ),
        PublicationTask.waiting_child_step: (
            "submit_failed" if fatal_error else ("done" if all_completed else "queued")
        ),
        PublicationTask.waiting_child_updated_at: now.isoformat() + "Z",
        PublicationTask.error_message: (
            f"自动投递图片失败：{errors[0]['message']}" if fatal_error else None
        ),
        PublicationTask.completed_at: now if fatal_error else None,
        PublicationTask.updated_at: now,
    }, synchronize_session=False)
    db.session.commit()
    audio_alignment = None
    audio_alignment_error = None
    try:
        audio_alignment = _auto_enqueue_whole_audio_alignment(
            parent_snapshot,
            storyboard,
        )
    except Exception as exc:
        # Image generation remains useful when a reusable WAV is temporarily
        # unavailable or its alignment queue cannot be reached.  The durable
        # audio-track/task state keeps the failure visible and the existing UI
        # action remains an explicit recovery path.
        audio_alignment_error = str(exc)[:500]
        logger.error(
            "Auto whole-audio alignment dispatch failed: workflow=%s storyboard=%s error=%s",
            workflow_id,
            storyboard_task_id,
            exc,
            exc_info=True,
        )
    logger.info(
        "Auto scene media dispatched: workflow=%s storyboard=%s adopted=%s queued=%s skipped=%s errors=%s audio=%s audio_error=%s",
        workflow_id,
        storyboard_task_id,
        adopted,
        len(queued),
        len(skipped),
        len(errors),
        audio_alignment,
        audio_alignment_error,
    )
    return {
        "workflow_job_id": workflow_id,
        "adopted": adopted,
        "queued": len(queued),
        "skipped": len(skipped),
        "errors": errors,
        "audio_alignment": audio_alignment,
        "audio_alignment_error": audio_alignment_error,
    }


def _preserve_completed_video_state(parent, *, all_completed, queued, errors):
    video_path = Path(str(getattr(parent, "task_video_path", "") or ""))
    return bool(
        all_completed
        and not queued
        and not errors
        and video_path.is_file()
    )


def _adopt_storyboard_embedded_images(storyboard_task_id, workflow_id, assets):
    """Adopt inline Gem images, leaving only missing scenes for the old queue."""
    gemini_path = Path(OUTPUT_ROOT) / str(storyboard_task_id) / "gemini_storyboard.json"
    if not gemini_path.is_file() or not assets:
        return 0
    try:
        payload = json.loads(gemini_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    images = payload.get("embedded_images")
    if not isinstance(images, list) or not images:
        return 0

    # Gemini emits inline pictures in the same order as the storyboard blocks.
    # A partial reply is therefore treated as an ordered prefix; later scenes
    # stay pending and continue through the existing image worker.
    ordered_assets = sorted(assets, key=lambda item: int(item.sequence))
    adopted = 0
    for asset, image_row in zip(ordered_assets, images):
        if not isinstance(image_row, dict):
            continue
        current_path = Path(str(asset.image_path or "")) if asset.image_path else None
        if asset.image_status == "completed" and current_path and current_path.is_file():
            continue
        source = Path(str(image_row.get("path") or ""))
        if not source.is_file():
            continue
        destination = Path(scene_image_path(workflow_id, asset.sequence))
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            with Image.open(source) as image:
                width, height = image.size
                if width < 256 or height < 256:
                    continue
                image.convert("RGB").save(destination, format="PNG")
            image_bytes = destination.stat().st_size
        except (OSError, ValueError):
            continue
        asset.image_task_id = f"storyboard-gemini:{storyboard_task_id}"
        asset.image_status = "completed"
        asset.image_progress = 100
        asset.image_attempt = max(1, int(asset.image_attempt or 0))
        asset.image_path = str(destination)
        asset.image_bytes = image_bytes
        asset.image_width = width
        asset.image_height = height
        asset.image_error = None
        asset.updated_at = datetime.utcnow()
        adopted += 1
    if adopted:
        db.session.commit()
    return adopted


def _sync_parent_storyboard_failure(storyboard_task_id, message):
    link = (
        PublicationTask.query.options(load_only(
            PublicationTask.parent_task_id,
            PublicationTask.parent_feature,
            PublicationTask.source_task_id,
        ))
        .filter(PublicationTask.id == str(storyboard_task_id))
        .one_or_none()
    )
    workflow_id = str(getattr(link, "parent_task_id", None) or "").strip()
    if not workflow_id or str(getattr(link, "parent_feature", None) or "") != "report_stick_figure_video_workflow":
        return
    now = datetime.utcnow()
    PublicationTask.query.filter(
        PublicationTask.id == workflow_id,
        PublicationTask.status != "canceled",
    ).update({
        PublicationTask.status: "failed",
        PublicationTask.current_step: "storyboard_failed",
        PublicationTask.progress: 35,
        PublicationTask.waiting_child_id: str(storyboard_task_id),
        PublicationTask.waiting_child_label: "标题与正文分镜",
        PublicationTask.waiting_child_status: "failed",
        PublicationTask.waiting_child_step: "failed",
        PublicationTask.waiting_child_updated_at: now.isoformat() + "Z",
        PublicationTask.error_message: str(message)[:4000],
        PublicationTask.completed_at: now,
        PublicationTask.updated_at: now,
    }, synchronize_session=False)
    db.session.commit()
    try:
        from api.report_stick_figure_video_workflow import _clear_scene_structure_cache

        _clear_scene_structure_cache(
            workflow_id=workflow_id,
            source_task_id=getattr(link, "source_task_id", None),
            storyboard_id=storyboard_task_id,
        )
    except Exception as exc:
        logger.warning("清理失败分镜结构缓存失败: workflow=%s storyboard=%s error=%s", workflow_id, storyboard_task_id, exc)


def _sync_parent_image_dispatch_failure(storyboard_task_id, message, workflow_job_id=None):
    link = (
        PublicationTask.query.options(load_only(
            PublicationTask.parent_task_id,
            PublicationTask.parent_feature,
        ))
        .filter(PublicationTask.id == str(storyboard_task_id))
        .one_or_none()
    )
    workflow_id = str(
        workflow_job_id or getattr(link, "parent_task_id", None) or ""
    ).strip()
    if (
        not workflow_id
        or (
            not workflow_job_id
            and str(getattr(link, "parent_feature", None) or "") != "report_stick_figure_video_workflow"
        )
    ):
        return
    now = datetime.utcnow()
    PublicationTask.query.filter(
        PublicationTask.id == workflow_id,
        PublicationTask.status != "canceled",
    ).update({
        PublicationTask.status: "failed",
        PublicationTask.current_step: "image_submit_failed",
        PublicationTask.progress: 60,
        PublicationTask.waiting_child_id: str(storyboard_task_id),
        PublicationTask.waiting_child_label: "分镜图片",
        PublicationTask.waiting_child_status: "failed",
        PublicationTask.waiting_child_step: "submit_failed",
        PublicationTask.waiting_child_updated_at: now.isoformat() + "Z",
        PublicationTask.error_message: f"自动投递图片失败：{str(message)[:3800]}",
        PublicationTask.completed_at: now,
        PublicationTask.updated_at: now,
    }, synchronize_session=False)
    db.session.commit()


def _block_for_human_intervention(task_id, exc):
    db.session.rollback()
    task = db.session.get(PublicationTask, str(task_id))
    if not task or task.feature != FEATURE_NAME or task.status == "canceled":
        release_db_session_quietly()
        return False

    now = datetime.utcnow()
    reason = str(getattr(exc, "reason", "verification") or "verification")
    blocked_status = "blocked_by_login" if reason == "login" else "blocked_by_captcha"
    intervention = {
        "reason": reason,
        "message": str(exc)[:1000],
        **dict(getattr(exc, "context", {}) or {}),
        "notification": dict(getattr(exc, "notification", {}) or {}),
        "blocked_at": now.isoformat() + "Z",
        "resolution_steps": [
            "使用通知中指定的浏览器 Profile 打开 Gemini",
            "完成登录以及验证码或安全验证",
            "确认 Gemini 输入框可见并能进入目标 Gem",
            "返回工作流详情页点击“登录完成，恢复任务”",
        ],
    }
    result = dict(task.result or {})
    result["error"] = str(exc)[:1000]
    result["error_type"] = blocked_status
    result["human_intervention"] = intervention
    task.status = blocked_status
    task.current_step = blocked_status
    task.error_message = (
        "等待人工登录 Gemini；完成后请点击“登录完成，恢复任务”"
        if reason == "login"
        else str(exc)[:4000]
    )
    task.completed_at = None
    task.updated_at = now
    task.result = result

    workflow_id = str(task.parent_task_id or "").strip()
    if (
        workflow_id
        and str(task.parent_feature or "") == "report_stick_figure_video_workflow"
    ):
        parent = db.session.get(PublicationTask, workflow_id)
        if parent and parent.status != "canceled":
            parent.status = blocked_status
            parent.current_step = f"storyboard_{blocked_status}"
            parent.progress = 35
            parent.waiting_child_id = task.id
            parent.waiting_child_label = "标题与正文分镜"
            parent.waiting_child_status = blocked_status
            parent.waiting_child_step = blocked_status
            parent.waiting_child_updated_at = now.isoformat() + "Z"
            parent.error_message = task.error_message
            parent.completed_at = None
            parent.updated_at = now

    db.session.commit()
    release_db_session_quietly()
    return False


def _fail(task_id, message):
    db.session.rollback()
    PublicationTask.query.filter(
        PublicationTask.id == task_id,
        PublicationTask.status != "canceled",
    ).update({
        PublicationTask.status: "failed",
        PublicationTask.current_step: "failed",
        PublicationTask.error_message: str(message)[:4000],
        PublicationTask.completed_at: datetime.utcnow(),
        PublicationTask.result: {"schema_version": STORYBOARD_SCHEMA_VERSION, "error": str(message)[:1000]},
    }, synchronize_session=False)
    db.session.commit()
    _sync_parent_storyboard_failure(task_id, message)
    release_db_session_quietly()
    return False
