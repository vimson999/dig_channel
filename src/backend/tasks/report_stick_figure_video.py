"""火柴人视频的整篇音频对齐与最终 FFmpeg 合成任务。"""

import logging
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import load_only

from app import create_app
from celery_config import celery_app
from models import (
    ReportStickFigureAudioTrack,
    ReportStickFigureSceneAsset,
    ReportVisualPlanSegment,
    db,
)
from models.task import PublicationTask
from services.asr_cache_service import transcribe_with_cache
from services.report_stick_figure_audio_alignment import (
    FEATURE_NAME as ALIGNMENT_FEATURE,
    align_storyboard_to_audio,
    extract_final_script,
    write_alignment_manifest,
)
from services.report_stick_figure_video import (
    FEATURE_NAME,
    normalize_render_options,
    render_scene_timeline,
)
from services.runtime_guard import release_db_session_quietly


logger = logging.getLogger("REPORT_STICK_FIGURE_VIDEO_TASK")


@celery_app.task(
    bind=True,
    name="tasks.report_stick_figure_video.align_whole_audio",
    store_errors_even_if_ignored=True,
)
def align_stick_figure_whole_audio(self, task_id):
    app = create_app()
    with app.app_context():
        try:
            return _align_whole_audio(str(task_id))
        except Exception as exc:
            logger.error("Stick figure whole-audio alignment failed: %s", exc, exc_info=True)
            _mark_alignment_failed(str(task_id), str(exc))
            return False
        finally:
            db.session.remove()


def _align_whole_audio(task_id: str) -> bool:
    task = (
        PublicationTask.query.options(load_only(
            PublicationTask.id,
            PublicationTask.status,
            PublicationTask.params,
            PublicationTask.parent_task_id,
        ))
        .filter(PublicationTask.id == task_id, PublicationTask.feature == ALIGNMENT_FEATURE)
        .one_or_none()
    )
    if not task or task.status == "canceled":
        return False
    params = task.params if isinstance(task.params, dict) else {}
    workflow_id = str(params.get("workflow_task_id") or task.parent_task_id or "").strip()
    track_id = str(params.get("audio_track_id") or "").strip()
    if not workflow_id or not track_id:
        raise ValueError("整篇音频对齐任务缺少工作流或音轨 ID")
    track = (
        ReportStickFigureAudioTrack.query.options(load_only(
            ReportStickFigureAudioTrack.id,
            ReportStickFigureAudioTrack.workflow_task_id,
            ReportStickFigureAudioTrack.storyboard_task_id,
            ReportStickFigureAudioTrack.source_task_id,
            ReportStickFigureAudioTrack.audio_path,
            ReportStickFigureAudioTrack.audio_seconds,
            ReportStickFigureAudioTrack.status,
        ))
        .filter(
            ReportStickFigureAudioTrack.id == track_id,
            ReportStickFigureAudioTrack.workflow_task_id == workflow_id,
        )
        .one_or_none()
    )
    if not track:
        raise ValueError("整篇音轨绑定不存在")
    audio_path = Path(str(track.audio_path or ""))
    if not audio_path.is_file() or audio_path.stat().st_size <= 0:
        raise ValueError("可复用的整篇 WAV 不存在或为空")

    assets = (
        ReportStickFigureSceneAsset.query.options(load_only(
            ReportStickFigureSceneAsset.id,
            ReportStickFigureSceneAsset.segment_id,
            ReportStickFigureSceneAsset.sequence,
        ))
        .filter(
            ReportStickFigureSceneAsset.workflow_task_id == workflow_id,
            ReportStickFigureSceneAsset.storyboard_task_id == track.storyboard_task_id,
            ReportStickFigureSceneAsset.sequence >= 1,
        )
        .order_by(ReportStickFigureSceneAsset.sequence.asc())
        .all()
    )
    if not assets:
        raise ValueError("没有可对齐的正文分镜")
    segment_ids = [str(asset.segment_id) for asset in assets]
    segment_rows = (
        ReportVisualPlanSegment.query.options(load_only(
            ReportVisualPlanSegment.id,
            ReportVisualPlanSegment.script_text,
        ))
        .filter(
            ReportVisualPlanSegment.task_id == track.storyboard_task_id,
            ReportVisualPlanSegment.id.in_(segment_ids),
        )
        .all()
    )
    script_by_id = {str(row.id): str(row.script_text or "") for row in segment_rows}
    scene_snapshots = [{
        "asset_id": str(asset.id),
        "segment_id": str(asset.segment_id),
        "sequence": int(asset.sequence),
        "script_text": script_by_id.get(str(asset.segment_id), ""),
    } for asset in assets]
    source_result_row = (
        PublicationTask.query.options(load_only(PublicationTask.id, PublicationTask.result))
        .filter(PublicationTask.id == track.source_task_id)
        .one_or_none()
    )
    source_script = extract_final_script(source_result_row.result if source_result_row else {})
    audio_seconds = float(track.audio_seconds or 0)
    asr_model = str(params.get("asr_model") or "medium").strip() or "medium"
    now = datetime.utcnow()
    PublicationTask.query.filter(PublicationTask.id == task_id).update({
        PublicationTask.status: "processing",
        PublicationTask.current_step: "transcribe_whole_audio",
        PublicationTask.progress: 10,
        PublicationTask.error_message: None,
        PublicationTask.updated_at: now,
    }, synchronize_session=False)
    ReportStickFigureAudioTrack.query.filter(ReportStickFigureAudioTrack.id == track_id).update({
        ReportStickFigureAudioTrack.status: "processing",
        ReportStickFigureAudioTrack.error: None,
        ReportStickFigureAudioTrack.updated_at: now,
    }, synchronize_session=False)
    _update_alignment_parent(workflow_id, task_id, "transcribe_whole_audio", 72)
    db.session.commit()

    # ASR 可能运行数分钟；等待期间不能持有数据库事务或 ORM 对象。
    release_db_session_quietly()
    asr_result = transcribe_with_cache(
        str(audio_path),
        model_size=asr_model,
        include_metadata=True,
    )
    alignment = align_storyboard_to_audio(
        scene_snapshots,
        asr_result.get("segments") or [],
        audio_seconds=audio_seconds,
        source_script=source_script,
    )
    manifest_payload = {
        **alignment,
        "workflow_task_id": workflow_id,
        "storyboard_task_id": str(params.get("storyboard_task_id") or ""),
        "source_task_id": str(params.get("source_task_id") or ""),
        "audio_path": str(audio_path.resolve()),
        "asr": asr_result.get("metadata") or {},
    }
    manifest_path = write_alignment_manifest(workflow_id, manifest_payload)

    current_status = db.session.query(PublicationTask.status).filter(PublicationTask.id == task_id).scalar()
    if current_status == "canceled":
        return False
    db.session.bulk_update_mappings(
        ReportStickFigureSceneAsset,
        [{
            "id": entry["asset_id"],
            "timeline_start_seconds": entry["start_seconds"],
            "timeline_end_seconds": entry["end_seconds"],
            "timeline_confidence": entry["confidence"],
            "timeline_source": "whole_audio",
            "updated_at": datetime.utcnow(),
        } for entry in alignment["timeline"]],
    )
    metadata = asr_result.get("metadata") or {}
    completed_at = datetime.utcnow()
    ReportStickFigureAudioTrack.query.filter(ReportStickFigureAudioTrack.id == track_id).update({
        ReportStickFigureAudioTrack.status: "completed",
        ReportStickFigureAudioTrack.source_script_hash: alignment.get("source_script_hash"),
        ReportStickFigureAudioTrack.storyboard_script_hash: alignment.get("storyboard_script_hash"),
        ReportStickFigureAudioTrack.asr_cache_key: metadata.get("cache_key"),
        ReportStickFigureAudioTrack.asr_model: asr_model,
        ReportStickFigureAudioTrack.aligned_scene_count: alignment["scene_count"],
        ReportStickFigureAudioTrack.average_confidence: alignment["average_confidence"],
        ReportStickFigureAudioTrack.minimum_confidence: alignment["minimum_confidence"],
        ReportStickFigureAudioTrack.manifest_path: manifest_path,
        ReportStickFigureAudioTrack.error: None,
        ReportStickFigureAudioTrack.updated_at: completed_at,
    }, synchronize_session=False)
    PublicationTask.query.filter(PublicationTask.id == task_id).update({
        PublicationTask.status: "completed",
        PublicationTask.current_step: "done",
        PublicationTask.progress: 100,
        PublicationTask.result: {
            "manifest_path": manifest_path,
            "scene_count": alignment["scene_count"],
            "audio_seconds": alignment["audio_seconds"],
            "average_confidence": alignment["average_confidence"],
            "minimum_confidence": alignment["minimum_confidence"],
            "asr_cache_key": metadata.get("cache_key"),
        },
        PublicationTask.error_message: None,
        PublicationTask.completed_at: completed_at,
        PublicationTask.updated_at: completed_at,
    }, synchronize_session=False)
    PublicationTask.query.filter(PublicationTask.id == workflow_id).update({
        PublicationTask.status: "processing",
        PublicationTask.current_step: "whole_audio_aligned",
        PublicationTask.progress: 82,
        PublicationTask.waiting_child_status: "completed",
        PublicationTask.waiting_child_step: "done",
        PublicationTask.waiting_child_updated_at: completed_at.isoformat() + "Z",
        PublicationTask.error_message: None,
        PublicationTask.updated_at: completed_at,
    }, synchronize_session=False)
    db.session.commit()
    return True


def _update_alignment_parent(workflow_id: str, task_id: str, step: str, progress: int) -> None:
    now = datetime.utcnow()
    PublicationTask.query.filter(PublicationTask.id == workflow_id).update({
        PublicationTask.status: "processing",
        PublicationTask.current_step: step,
        PublicationTask.progress: progress,
        PublicationTask.waiting_child_id: task_id,
        PublicationTask.waiting_child_label: "整篇音频时间轴对齐",
        PublicationTask.waiting_child_status: "processing",
        PublicationTask.waiting_child_step: step,
        PublicationTask.waiting_child_updated_at: now.isoformat() + "Z",
        PublicationTask.error_message: None,
        PublicationTask.updated_at: now,
    }, synchronize_session=False)


def _mark_alignment_failed(task_id: str, error: str) -> None:
    task = (
        PublicationTask.query.options(load_only(
            PublicationTask.id,
            PublicationTask.params,
            PublicationTask.parent_task_id,
        ))
        .filter(PublicationTask.id == task_id, PublicationTask.feature == ALIGNMENT_FEATURE)
        .one_or_none()
    )
    if not task:
        return
    params = task.params if isinstance(task.params, dict) else {}
    workflow_id = str(params.get("workflow_task_id") or task.parent_task_id or "").strip()
    track_id = str(params.get("audio_track_id") or "").strip()
    now = datetime.utcnow()
    PublicationTask.query.filter(PublicationTask.id == task_id).update({
        PublicationTask.status: "failed",
        PublicationTask.current_step: "failed",
        PublicationTask.error_message: str(error)[:4000],
        PublicationTask.completed_at: now,
        PublicationTask.updated_at: now,
    }, synchronize_session=False)
    if track_id:
        ReportStickFigureAudioTrack.query.filter(ReportStickFigureAudioTrack.id == track_id).update({
            ReportStickFigureAudioTrack.status: "failed",
            ReportStickFigureAudioTrack.error: str(error)[:4000],
            ReportStickFigureAudioTrack.updated_at: now,
        }, synchronize_session=False)
    if workflow_id:
        # 对齐失败不终结父工作流；用户仍可切换到逐镜头 TTS。
        PublicationTask.query.filter(PublicationTask.id == workflow_id).update({
            PublicationTask.status: "processing",
            PublicationTask.current_step: "whole_audio_alignment_failed",
            PublicationTask.waiting_child_status: "failed",
            PublicationTask.waiting_child_step: "failed",
            PublicationTask.waiting_child_updated_at: now.isoformat() + "Z",
            PublicationTask.error_message: str(error)[:4000],
            PublicationTask.updated_at: now,
        }, synchronize_session=False)
    db.session.commit()


@celery_app.task(
    bind=True,
    name="tasks.report_stick_figure_video.render_video",
    store_errors_even_if_ignored=True,
)
def render_stick_figure_video(self, task_id):
    app = create_app()
    with app.app_context():
        try:
            return _render_video(str(task_id))
        finally:
            db.session.remove()


def _render_video(task_id: str) -> bool:
    task = (
        PublicationTask.query.options(load_only(
            PublicationTask.id,
            PublicationTask.status,
            PublicationTask.params,
            PublicationTask.parent_task_id,
        ))
        .filter(PublicationTask.id == task_id, PublicationTask.feature == FEATURE_NAME)
        .one_or_none()
    )
    if not task or task.status == "canceled":
        return False
    params = task.params if isinstance(task.params, dict) else {}
    workflow_id = str(params.get("workflow_task_id") or task.parent_task_id or "").strip()
    if not workflow_id:
        _mark_failed(task_id, "缺少火柴人视频父任务 ID")
        return False
    storyboard_id = str(params.get("source_task_id") or "").strip()
    if not storyboard_id:
        _mark_failed(task_id, "缺少本次视频对应的分镜任务 ID", workflow_id=workflow_id)
        return False
    try:
        render_options = normalize_render_options(params.get("render_options"))
    except ValueError as exc:
        _mark_failed(task_id, str(exc), workflow_id=workflow_id)
        return False
    audio_mode = str(params.get("audio_mode") or "scene_tts").strip()
    if audio_mode not in {"scene_tts", "whole_audio"}:
        _mark_failed(task_id, f"不支持的音频模式：{audio_mode}", workflow_id=workflow_id)
        return False

    assets = (
        ReportStickFigureSceneAsset.query.options(load_only(
            ReportStickFigureSceneAsset.id,
            ReportStickFigureSceneAsset.segment_id,
            ReportStickFigureSceneAsset.sequence,
            ReportStickFigureSceneAsset.image_status,
            ReportStickFigureSceneAsset.image_path,
            ReportStickFigureSceneAsset.tts_status,
            ReportStickFigureSceneAsset.audio_path,
            ReportStickFigureSceneAsset.timeline_start_seconds,
            ReportStickFigureSceneAsset.timeline_end_seconds,
            ReportStickFigureSceneAsset.timeline_confidence,
            ReportStickFigureSceneAsset.timeline_source,
        ))
        .filter(
            ReportStickFigureSceneAsset.workflow_task_id == workflow_id,
            ReportStickFigureSceneAsset.storyboard_task_id == storyboard_id,
            ReportStickFigureSceneAsset.sequence >= 1,
        )
        .order_by(ReportStickFigureSceneAsset.sequence.asc())
        .all()
    )
    if not assets:
        _mark_failed(
            task_id,
            "没有可合成的正文分镜资产（正文必须从 001 开始）",
            workflow_id=workflow_id,
        )
        return False
    segment_ids = [str(asset.segment_id) for asset in assets if asset.segment_id]
    segment_rows = (
        ReportVisualPlanSegment.query.options(load_only(
            ReportVisualPlanSegment.id,
            ReportVisualPlanSegment.script_text,
        ))
        .filter(ReportVisualPlanSegment.id.in_(segment_ids))
        .all()
    )
    script_by_segment_id = {str(row.id): str(row.script_text or "") for row in segment_rows}
    whole_audio_path = None
    audio_track = None
    if audio_mode == "whole_audio":
        audio_track_id = str(params.get("audio_track_id") or "").strip()
        audio_track = (
            ReportStickFigureAudioTrack.query.options(load_only(
                ReportStickFigureAudioTrack.id,
                ReportStickFigureAudioTrack.workflow_task_id,
                ReportStickFigureAudioTrack.storyboard_task_id,
                ReportStickFigureAudioTrack.status,
                ReportStickFigureAudioTrack.audio_path,
            ))
            .filter(
                ReportStickFigureAudioTrack.id == audio_track_id,
                ReportStickFigureAudioTrack.workflow_task_id == workflow_id,
                ReportStickFigureAudioTrack.storyboard_task_id == storyboard_id,
            )
            .one_or_none()
        )
        if not audio_track or audio_track.status != "completed":
            _mark_failed(task_id, "整篇音频时间轴尚未对齐完成", workflow_id=workflow_id)
            return False
        whole_audio_path = str(audio_track.audio_path or "")
        if not whole_audio_path or not Path(whole_audio_path).is_file():
            _mark_failed(task_id, "整篇音频文件不存在", workflow_id=workflow_id)
            return False
    scenes = []
    for asset in assets:
        if asset.image_status != "completed" or not asset.image_path or not Path(asset.image_path).is_file():
            _mark_failed(
                task_id,
                f"第 {asset.sequence} 段图片尚未完成",
                workflow_id=workflow_id,
            )
            return False
        scene = {
            "asset_id": asset.id,
            "sequence": asset.sequence,
            "image_path": asset.image_path,
            "script_text": script_by_segment_id.get(str(asset.segment_id), ""),
        }
        if audio_mode == "scene_tts":
            if asset.tts_status != "completed" or not asset.audio_path or not Path(asset.audio_path).is_file():
                _mark_failed(
                    task_id,
                    f"第 {asset.sequence} 段语音尚未完成",
                    workflow_id=workflow_id,
                )
                return False
            scene["audio_path"] = asset.audio_path
        else:
            if (
                asset.timeline_source != "whole_audio"
                or asset.timeline_start_seconds is None
                or asset.timeline_end_seconds is None
                or float(asset.timeline_end_seconds) <= float(asset.timeline_start_seconds)
            ):
                _mark_failed(
                    task_id,
                    f"第 {asset.sequence} 段整篇音频时间轴尚未就绪",
                    workflow_id=workflow_id,
                )
                return False
            scene.update({
                "start_seconds": float(asset.timeline_start_seconds),
                "end_seconds": float(asset.timeline_end_seconds),
                "alignment_confidence": float(asset.timeline_confidence or 0),
            })
        scenes.append(scene)

    _update_progress(task_id, workflow_id, "prepare_timeline", 2, f"准备 {len(scenes)} 段音画素材")

    def progress_callback(event):
        _update_progress(
            task_id,
            workflow_id,
            str(event.get("step") or "render_video"),
            int(event.get("progress") or 0),
            str(event.get("message") or "正在合成视频"),
        )

    try:
        result = render_scene_timeline(
            workflow_id,
            scenes,
            audio_mode=audio_mode,
            whole_audio_path=whole_audio_path,
            render_options=render_options,
            progress_callback=progress_callback,
        )
        now = datetime.utcnow()
        compact_result = {
            "video_path": result["video_path"],
            "manifest_path": result["manifest_path"],
            "bytes": result["bytes"],
            "scene_count": result["scene_count"],
            "duration_seconds": result["duration_seconds"],
            "width": result["width"],
            "height": result["height"],
            "fps": result["fps"],
            "render_profile": result["render_profile"],
            "render_options": result["render_options"],
            "audio_mode": result["audio_mode"],
            "subtitle_path": result["subtitle_path"],
            "subtitle_entry_count": result["subtitle_entry_count"],
        }
        PublicationTask.query.filter(PublicationTask.id == task_id).update({
            PublicationTask.status: "completed",
            PublicationTask.current_step: "done",
            PublicationTask.progress: 100,
            PublicationTask.result: compact_result,
            PublicationTask.task_video_path: result["video_path"],
            PublicationTask.error_message: None,
            PublicationTask.completed_at: now,
            PublicationTask.updated_at: now,
        }, synchronize_session=False)
        PublicationTask.query.filter(PublicationTask.id == workflow_id).update({
            PublicationTask.status: "completed",
            PublicationTask.current_step: "video_done",
            PublicationTask.progress: 100,
            PublicationTask.task_video_path: result["video_path"],
            PublicationTask.waiting_child_status: "completed",
            PublicationTask.waiting_child_step: "done",
            PublicationTask.waiting_child_updated_at: now.isoformat() + "Z",
            PublicationTask.error_message: None,
            PublicationTask.completed_at: now,
            PublicationTask.updated_at: now,
        }, synchronize_session=False)
        db.session.commit()
        return True
    except Exception as exc:
        logger.error("Stick figure video render failed: %s", exc, exc_info=True)
        _mark_failed(task_id, str(exc), workflow_id=workflow_id)
        return False


def _update_progress(task_id: str, workflow_id: str, step: str, progress: int, message: str) -> None:
    progress = max(0, min(100, int(progress)))
    now = datetime.utcnow()
    PublicationTask.query.filter(
        PublicationTask.id == task_id,
        PublicationTask.status != "canceled",
    ).update({
        PublicationTask.status: "processing",
        PublicationTask.current_step: step,
        PublicationTask.progress: progress,
        PublicationTask.updated_at: now,
    }, synchronize_session=False)
    PublicationTask.query.filter(PublicationTask.id == workflow_id).update({
        PublicationTask.status: "processing",
        PublicationTask.current_step: step,
        PublicationTask.progress: 90 + int(progress * 0.09),
        PublicationTask.waiting_child_status: "processing",
        PublicationTask.waiting_child_step: step,
        PublicationTask.waiting_child_updated_at: now.isoformat() + "Z",
        PublicationTask.error_message: None,
        PublicationTask.updated_at: now,
    }, synchronize_session=False)
    db.session.commit()


def _mark_failed(task_id: str, error: str, *, workflow_id: str = "") -> None:
    now = datetime.utcnow()
    PublicationTask.query.filter(PublicationTask.id == task_id).update({
        PublicationTask.status: "failed",
        PublicationTask.current_step: "failed",
        PublicationTask.error_message: str(error)[:4000],
        PublicationTask.completed_at: now,
        PublicationTask.updated_at: now,
    }, synchronize_session=False)
    if workflow_id:
        PublicationTask.query.filter(PublicationTask.id == workflow_id).update({
            PublicationTask.status: "failed",
            PublicationTask.current_step: "video_failed",
            PublicationTask.progress: 90,
            PublicationTask.waiting_child_status: "failed",
            PublicationTask.waiting_child_step: "failed",
            PublicationTask.waiting_child_updated_at: now.isoformat() + "Z",
            PublicationTask.error_message: str(error)[:4000],
            PublicationTask.completed_at: now,
            PublicationTask.updated_at: now,
        }, synchronize_session=False)
    db.session.commit()
