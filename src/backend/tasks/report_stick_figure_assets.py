import logging
from datetime import datetime
from pathlib import Path

from PIL import Image
from sqlalchemy import case, or_
from sqlalchemy.orm import load_only

from app import create_app
from celery_config import celery_app
from models import ReportStickFigureSceneAsset, ReportVisualPlanOutput, ReportVisualPlanSegment, db
from models.task import PublicationTask
from services.gemini_browser import GeminiBrowserAutomationError, GeminiBrowserWorker
from services.report_stick_figure_assets import (
    FEATURE_NAME,
    has_browser_overlay_artifact,
    image_gemini_url,
    scene_image_path,
    scene_image_prompt,
    scene_image_prompt_hash,
)
from services.report_pdf_evidence import evidence_visual_for_segment, render_claim_screenshot
from services.report_visual_scheme import visual_scheme


logger = logging.getLogger("REPORT_STICK_FIGURE_ASSETS_TASK")

PARENT_IMAGE_COMPLETION_STEPS = frozenset({
    "material_queued",
    "storyboard_queued",
    "storyboard_done",
    "storyboard_submit_failed",
    "images_queued",
    "images_done",
    "image_submit_failed",
})


@celery_app.task(
    bind=True,
    name="tasks.report_stick_figure_assets.generate_scene_image",
    store_errors_even_if_ignored=True,
    max_retries=2,
)
def generate_scene_image(self, task_id):
    app = create_app()
    with app.app_context():
        try:
            result = _generate_scene_image(str(task_id))
            if not result and int(getattr(self.request, "retries", 0) or 0) < int(self.max_retries or 0):
                task = PublicationTask.query.options(load_only(
                    PublicationTask.error_message,
                )).filter(PublicationTask.id == str(task_id)).one_or_none()
                error = str(getattr(task, "error_message", "") or "")
                if _is_transient_image_error(error):
                    logger.warning(
                        "Retrying transient scene image failure task=%s attempt=%s error=%s",
                        task_id,
                        int(getattr(self.request, "retries", 0) or 0) + 1,
                        error,
                    )
                    raise self.retry(
                        exc=GeminiBrowserAutomationError(error),
                        countdown=15,
                    )
            return result
        finally:
            db.session.remove()


def _is_transient_image_error(error: str) -> bool:
    text = str(error or "").lower()
    return any(marker in text for marker in (
        "等待 gemini 生成图片超时",
        "页面自动化超时",
        "browser has been closed",
        "target page, context or browser has been closed",
        "net::err_",
        "未找到 gemini 工具/生成图片入口",
        "gemini 工具入口点击失败",
        "gemini 工具菜单中没有找到生成图片选项",
        "未找到 gemini 输入框",
        "gemini 提示词未成功提交",
        "无法定位 gemini 生成图片",
        "gemini 新图片保存为空",
        "gemini 图片包含浏览器浮层",
    ))


def _generate_scene_image(task_id: str) -> bool:
    task = (
        PublicationTask.query.options(load_only(
            PublicationTask.id,
            PublicationTask.status,
        ))
        .filter(PublicationTask.id == task_id, PublicationTask.feature == FEATURE_NAME)
        .one_or_none()
    )
    if not task or task.status == "canceled":
        return False
    asset = (
        ReportStickFigureSceneAsset.query.options(load_only(
            ReportStickFigureSceneAsset.id,
            ReportStickFigureSceneAsset.workflow_task_id,
            ReportStickFigureSceneAsset.segment_id,
            ReportStickFigureSceneAsset.sequence,
            ReportStickFigureSceneAsset.visual_prompt_hash,
        ))
        .filter(ReportStickFigureSceneAsset.image_task_id == task_id)
        .one_or_none()
    )
    if not asset:
        _mark_task_failed(task_id, "分镜资产记录不存在")
        return False
    asset_id = str(asset.id)
    workflow_task_id = str(asset.workflow_task_id)
    segment_id = str(asset.segment_id)
    sequence = int(asset.sequence)
    visual_prompt_hash = str(asset.visual_prompt_hash or "")
    segment = (
        ReportVisualPlanSegment.query.options(load_only(
            ReportVisualPlanSegment.id,
            ReportVisualPlanSegment.task_id,
            ReportVisualPlanSegment.script_text,
            ReportVisualPlanSegment.visual_prompt,
            ReportVisualPlanSegment.sequence,
        ))
        .filter(ReportVisualPlanSegment.id == segment_id)
        .one_or_none()
    )
    if not segment:
        _mark_failed(task_id, asset_id, "分镜记录不存在")
        return False
    evidence_visual = evidence_visual_for_segment(segment_id)
    if evidence_visual:
        evidence_output, evidence_claim = evidence_visual
        try:
            _update_progress(task_id, asset_id, "prepare_evidence", 8)
            output_path = scene_image_path(workflow_task_id, sequence)
            render_claim_screenshot(evidence_output, evidence_claim, output_path)
            width, height = _normalize_png(Path(output_path))
            size = Path(output_path).stat().st_size
            PublicationTask.query.filter(PublicationTask.id == task_id).update({
                PublicationTask.status: "completed",
                PublicationTask.current_step: "done",
                PublicationTask.progress: 100,
                PublicationTask.error_message: None,
                PublicationTask.completed_at: datetime.utcnow(),
                PublicationTask.result: {
                    "scene_asset_id": asset_id,
                    "image_path": output_path,
                    "image_source": "pdf_evidence",
                    "evidence_claim_id": evidence_claim.id,
                },
            }, synchronize_session=False)
            ReportStickFigureSceneAsset.query.filter(ReportStickFigureSceneAsset.id == asset_id).update({
                ReportStickFigureSceneAsset.image_status: "completed",
                ReportStickFigureSceneAsset.image_progress: 100,
                ReportStickFigureSceneAsset.image_path: output_path,
                ReportStickFigureSceneAsset.image_bytes: size,
                ReportStickFigureSceneAsset.image_width: width,
                ReportStickFigureSceneAsset.image_height: height,
                ReportStickFigureSceneAsset.image_error: None,
                ReportStickFigureSceneAsset.image_source: "pdf_evidence",
                ReportStickFigureSceneAsset.evidence_claim_id: evidence_claim.id,
                ReportStickFigureSceneAsset.updated_at: datetime.utcnow(),
            }, synchronize_session=False)
            db.session.commit()
            _refresh_parent_image_state(workflow_task_id)
            return True
        except Exception as exc:
            logger.warning(
                "Evidence image unavailable; degrading to generated visual task=%s claim=%s error=%s",
                task_id,
                evidence_claim.id,
                exc,
            )
    raw_prompt = str(segment.visual_prompt or "").strip()
    script_text = str(segment.script_text or "").strip()
    scheme_output = (
        ReportVisualPlanOutput.query.options(load_only(
            ReportVisualPlanOutput.visual_scheme_key,
            ReportVisualPlanOutput.image_gemini_url,
            ReportVisualPlanOutput.image_style_profile,
        ))
        .filter(ReportVisualPlanOutput.task_id == str(segment.task_id))
        .one_or_none()
    )
    if not scheme_output:
        _mark_failed(task_id, asset_id, "分镜视觉方案快照不存在")
        return False
    scheme_key = str(scheme_output.visual_scheme_key or "stick_figure")
    scheme = visual_scheme(scheme_key, require_configured=False)
    if scheme.image_profile_version != str(scheme_output.image_style_profile or ""):
        _mark_failed(task_id, asset_id, "视觉方案版本已变化，请重新生成分镜后再生成图片")
        return False
    prompt = scene_image_prompt(raw_prompt, script_text, scheme_key)
    prompt_hash = scene_image_prompt_hash(raw_prompt, script_text, scheme_key)
    if prompt_hash != visual_prompt_hash:
        _mark_failed(task_id, asset_id, "分镜提示词或视觉方案契约已变化，请重新初始化资产")
        return False

    _update_progress(task_id, asset_id, "prepare_browser", 3)

    def progress_callback(event):
        _update_progress(
            task_id,
            asset_id,
            str(event.get("step") or "processing"),
            int(event.get("progress") or 0),
            update_business_task=False,
        )

    try:
        output_path = scene_image_path(workflow_task_id, sequence)
        result = GeminiBrowserWorker().generate_image(
            job_id=task_id,
            prompt=prompt,
            image_output_path=output_path,
            target_url=str(scheme_output.image_gemini_url or image_gemini_url(scheme_key)),
            progress_callback=progress_callback,
        )
        width, height = _normalize_png(Path(output_path))
        size = Path(output_path).stat().st_size
        PublicationTask.query.filter(PublicationTask.id == task_id).update({
            PublicationTask.status: "completed",
            PublicationTask.current_step: "done",
            PublicationTask.progress: 100,
            PublicationTask.error_message: None,
            PublicationTask.completed_at: datetime.utcnow(),
            PublicationTask.result: {
                "scene_asset_id": asset_id,
                "image_path": output_path,
                "page_url": result.get("page_url"),
            },
        }, synchronize_session=False)
        ReportStickFigureSceneAsset.query.filter(ReportStickFigureSceneAsset.id == asset_id).update({
            ReportStickFigureSceneAsset.image_status: "completed",
            ReportStickFigureSceneAsset.image_progress: 100,
            ReportStickFigureSceneAsset.image_path: output_path,
            ReportStickFigureSceneAsset.image_bytes: size,
            ReportStickFigureSceneAsset.image_width: width,
            ReportStickFigureSceneAsset.image_height: height,
            ReportStickFigureSceneAsset.image_error: None,
            ReportStickFigureSceneAsset.image_source: "generated",
            ReportStickFigureSceneAsset.evidence_claim_id: None,
            ReportStickFigureSceneAsset.updated_at: datetime.utcnow(),
        }, synchronize_session=False)
        db.session.commit()
        _refresh_parent_image_state(workflow_task_id)
        return True
    except Exception as exc:
        logger.error("Stick figure scene image failed: %s", exc, exc_info=True)
        _mark_failed(task_id, asset_id, str(exc))
        return False


def _update_progress(
    task_id: str,
    asset_id: str,
    step: str,
    progress: int,
    *,
    update_business_task: bool = True,
) -> None:
    progress = max(0, min(100, int(progress)))
    if update_business_task:
        PublicationTask.query.filter(
            PublicationTask.id == task_id,
            PublicationTask.status != "canceled",
        ).update({
            PublicationTask.status: "processing",
            PublicationTask.current_step: step,
            PublicationTask.progress: progress,
            PublicationTask.updated_at: datetime.utcnow(),
        }, synchronize_session=False)
    ReportStickFigureSceneAsset.query.filter(ReportStickFigureSceneAsset.id == asset_id).update({
        ReportStickFigureSceneAsset.image_status: "processing",
        ReportStickFigureSceneAsset.image_progress: progress,
        ReportStickFigureSceneAsset.updated_at: datetime.utcnow(),
    }, synchronize_session=False)
    db.session.commit()


def _mark_task_failed(task_id: str, error: str) -> None:
    PublicationTask.query.filter(PublicationTask.id == task_id).update({
        PublicationTask.status: "failed",
        PublicationTask.current_step: "failed",
        PublicationTask.error_message: str(error)[:4000],
        PublicationTask.completed_at: datetime.utcnow(),
    }, synchronize_session=False)
    db.session.commit()


def _mark_failed(task_id: str, asset_id: str, error: str) -> None:
    _mark_task_failed(task_id, error)
    ReportStickFigureSceneAsset.query.filter(ReportStickFigureSceneAsset.id == asset_id).update({
        ReportStickFigureSceneAsset.image_status: "failed",
        ReportStickFigureSceneAsset.image_progress: 0,
        ReportStickFigureSceneAsset.image_error: str(error)[:4000],
        ReportStickFigureSceneAsset.updated_at: datetime.utcnow(),
    }, synchronize_session=False)
    db.session.commit()


def _refresh_parent_image_state(workflow_task_id: str) -> None:
    """Project aggregate scene-image completion back onto the visual parent."""
    status_counts = (
        db.session.query(
            ReportStickFigureSceneAsset.image_status,
            db.func.count(ReportStickFigureSceneAsset.id),
        )
        .filter(ReportStickFigureSceneAsset.workflow_task_id == str(workflow_task_id))
        .group_by(ReportStickFigureSceneAsset.image_status)
        .all()
    )
    counts = {str(status or ""): int(count or 0) for status, count in status_counts}
    total = sum(counts.values())
    if total <= 0 or counts.get("completed", 0) < total:
        return
    now = datetime.utcnow()
    PublicationTask.query.filter(
        PublicationTask.id == str(workflow_task_id),
        PublicationTask.feature == "report_stick_figure_video_workflow",
        PublicationTask.status.notin_(("canceled", "completed")),
        or_(
            PublicationTask.current_step.is_(None),
            PublicationTask.current_step.in_(
                tuple(PARENT_IMAGE_COMPLETION_STEPS)
            ),
        ),
    ).update({
        PublicationTask.status: "processing",
        PublicationTask.current_step: "images_done",
        PublicationTask.progress: case(
            (PublicationTask.progress > 75, PublicationTask.progress),
            else_=75,
        ),
        PublicationTask.waiting_child_label: "分镜图片",
        PublicationTask.waiting_child_status: "completed",
        PublicationTask.waiting_child_step: "done",
        PublicationTask.waiting_child_updated_at: now.isoformat() + "Z",
        PublicationTask.error_message: None,
        PublicationTask.completed_at: None,
        PublicationTask.updated_at: now,
    }, synchronize_session=False)
    db.session.commit()
    try:
        from tasks.report_stick_figure_video import (
            maybe_auto_enqueue_stick_figure_video,
        )

        continuation = maybe_auto_enqueue_stick_figure_video(
            str(workflow_task_id)
        )
        logger.info(
            "Scene images auto-render continuation: workflow=%s result=%s",
            workflow_task_id,
            continuation,
        )
    except Exception as exc:
        logger.error(
            "Scene images auto-render continuation failed: workflow=%s error=%s",
            workflow_task_id,
            exc,
            exc_info=True,
        )


def _normalize_png(path: Path):
    if not path.exists() or path.stat().st_size <= 0:
        raise ValueError("Gemini 图片文件为空")
    with Image.open(path) as image:
        width, height = image.size
        if width < 256 or height < 256:
            raise ValueError(f"Gemini 图片尺寸异常：{width}x{height}")
        converted = image.convert("RGB")
        converted.save(path, format="PNG")
    if has_browser_overlay_artifact(str(path)):
        raise ValueError("Gemini 图片包含浏览器浮层，已拒绝入库，请重试该分镜")
    return width, height
