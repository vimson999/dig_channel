import json
import logging
from datetime import datetime
from pathlib import Path

from sqlalchemy.exc import OperationalError
from sqlalchemy import or_
from sqlalchemy.orm import load_only

from app import create_app
from celery_config import celery_app
from models import db, ReportVisualPlanOutput, ReportVisualPlanSegment
from models.task import PublicationTask
from services.gemini_browser import GeminiBrowserWorker
from services.report_visual_plan_service import (
    FEATURE_NAME,
    OUTPUT_ROOT,
    QUEUE_NAME,
    STORYBOARD_SCHEMA_VERSION,
    normalize_storyboard_response,
    storyboard_prompt,
)
from services.report_visual_scheme import DEFAULT_VISUAL_SCHEME_KEY, visual_scheme
from services.runtime_guard import maybe_guarded_apply_async, release_db_session_quietly


logger = logging.getLogger("REPORT_VISUAL_PLAN_TASK")


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
    app = create_app()
    with app.app_context():
        return _process_job(str(task_id))


def enqueue_report_visual_plan_for_material(
    material_task_id,
    *,
    user_id="",
    parent_feature="storyboard",
    parent_task_id="",
    visual_scheme_key=DEFAULT_VISUAL_SCHEME_KEY,
):
    """Idempotently reuse or enqueue one storyboard for a completed material task."""
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
    if not source or source.status != "completed" or int(source.final_script_chars or 0) <= 0:
        raise ValueError("基础文案任务还没有完整文案")

    existing = (
        db.session.query(PublicationTask.id)
        .join(ReportVisualPlanOutput, ReportVisualPlanOutput.task_id == PublicationTask.id)
        .filter(ReportVisualPlanOutput.source_task_id == material_task_id)
        .filter(ReportVisualPlanOutput.schema_version == STORYBOARD_SCHEMA_VERSION)
        .filter(ReportVisualPlanOutput.visual_scheme_key == scheme.key)
        .filter(PublicationTask.status == "completed")
        .order_by(ReportVisualPlanOutput.created_at.desc())
        .first()
    )
    if existing:
        return {"job_id": str(existing.id), "status": "completed", "cached": True}

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


def _process_job(task_id):
    task = (
        PublicationTask.query.options(load_only(
            PublicationTask.id, PublicationTask.status, PublicationTask.feature,
            PublicationTask.source_task_id, PublicationTask.user_id,
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
    release_db_session_quietly()

    def progress(event):
        mapped = min(68, max(5, int(event.get("progress") or 0) * 68 // 100))
        _update_status(task_id, event.get("step") or "gemini", mapped)

    try:
        _update_status(task_id, "gemini_storyboard", 5, status="processing")
        gemini_result = GeminiBrowserWorker().generate(
            job_id=task_id,
            prompt=prompt,
            output_path=str(gemini_path),
            target_url=scheme.storyboard_gemini_url,
            followup_prompts=[],
            progress_callback=progress,
        )
        raw_text = str(gemini_result.get("final_response_text") or gemini_result.get("response_text") or "").strip()
        _update_status(task_id, "normalize_storyboard", 72)
        normalized = normalize_storyboard_response(material, raw_text)
        normalized_path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
        _persist_success(
            task_id,
            source_task_id=source_task_id,
            title=task_title,
            gemini_path=str(gemini_path),
            normalized_path=str(normalized_path),
            normalized=normalized,
            scheme=scheme,
        )
        try:
            _dispatch_auto_scene_images(task_id)
        except Exception as exc:
            logger.error("Auto scene image dispatch failed: %s", exc, exc_info=True)
            _sync_parent_image_dispatch_failure(task_id, str(exc))
        return True
    except Exception as exc:
        logger.error("Report visual plan failed: %s", exc, exc_info=True)
        return _fail(task_id, str(exc))
    finally:
        release_db_session_quietly()


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
    logger.info(
        "Auto scene images dispatched: workflow=%s storyboard=%s queued=%s skipped=%s errors=%s",
        workflow_id,
        storyboard_task_id,
        len(queued),
        len(skipped),
        len(errors),
    )
    return {
        "workflow_job_id": workflow_id,
        "queued": len(queued),
        "skipped": len(skipped),
        "errors": errors,
    }


def _sync_parent_storyboard_failure(storyboard_task_id, message):
    link = (
        PublicationTask.query.options(load_only(
            PublicationTask.parent_task_id,
            PublicationTask.parent_feature,
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
