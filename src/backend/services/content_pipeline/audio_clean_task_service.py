import logging
from pathlib import Path

from sqlalchemy import update

from models import db
from models.task import PublicationTask
from services.runtime_guard import maybe_guarded_apply_async
from tasks.content_pipeline import run_audio_clean_task


logger = logging.getLogger(__name__)


def submit_audio_clean_task(
    *,
    title,
    files,
    source_root=None,
    cleaner=None,
    auto_publish=None,
    user_id=None,
    trigger="manual",
    metadata=None,
):
    """Create and enqueue an audio clean task through the shared pipeline."""
    cleaner = cleaner if isinstance(cleaner, dict) else {}
    auto_publish = auto_publish if isinstance(auto_publish, dict) else {}
    metadata = metadata if isinstance(metadata, dict) else {}
    normalized_files = _normalize_audio_files(files)
    if not normalized_files:
        raise ValueError("files 不能为空")

    normalized_source_root = None
    if source_root:
        source_root_path = Path(str(source_root)).expanduser().resolve()
        if not source_root_path.exists() or not source_root_path.is_dir():
            raise ValueError(f"source_root 不存在: {source_root_path}")
        normalized_source_root = str(source_root_path)

    task = PublicationTask(
        status="pending",
        current_step="init",
        progress=0,
        params={
            "feature": "content_audio_clean",
            "user_id": user_id,
            "content_pipeline": {
                "title": (title or "音频清洗任务").strip() or "音频清洗任务",
                "trigger": trigger,
                "metadata": metadata,
                "auto_publish": auto_publish,
                "audio_clean": {
                    "files": normalized_files,
                    "source_root": normalized_source_root,
                    "cleaner": {
                        "head_trim_seconds": float(cleaner.get("head_trim_seconds") or 11),
                        "tail_trim_seconds": float(cleaner.get("tail_trim_seconds") or 90),
                        "gap_audio_path": cleaner.get("gap_audio_path") or None,
                        "bitrate": cleaner.get("bitrate") or "128k",
                        "sample_rate": int(cleaner.get("sample_rate") or 44100),
                        "channels": int(cleaner.get("channels") or 2),
                        "fast_copy": cleaner.get("fast_copy", True),
                        "max_output_size_mb": float(cleaner.get("max_output_size_mb") or 49),
                        "output_root": cleaner.get("output_root") or None,
                    },
                },
            },
        },
        result={
            "files": [{"path": item, "status": "waiting"} for item in normalized_files],
            "trigger": trigger,
            "metadata": metadata,
        },
    )
    db.session.add(task)
    db.session.flush()
    task_id = str(task.id)
    payload = task.to_dict()
    db.session.commit()

    try:
        submit_result = maybe_guarded_apply_async(
            run_audio_clean_task,
            args=[task_id],
            queue="content_pipeline",
            feature="content_audio_clean",
            task_id=task_id,
            retry=False,
        )
        celery_task = submit_result["async_result"]
    except Exception as exc:
        logger.exception("音频清洗任务入队失败: %s", task_id)
        db.session.rollback()
        error_message = f"音频清洗任务入队失败: {exc}"
        db.session.execute(
            update(PublicationTask)
            .where(PublicationTask.id == task_id)
            .values(
                status="failed",
                current_step="failed",
                error_message=error_message,
            )
            .execution_options(synchronize_session=False)
        )
        db.session.commit()
        raise RuntimeError(error_message) from exc

    db.session.execute(
        update(PublicationTask)
        .where(PublicationTask.id == task_id)
        .values(celery_task_id=celery_task.id)
        .execution_options(synchronize_session=False)
    )
    db.session.commit()
    payload["celery_task_id"] = celery_task.id
    deferred_state = submit_result.get("deferred_state")
    if isinstance(deferred_state, dict):
        payload.update(deferred_state)
    payload["deferred"] = bool(submit_result.get("deferred"))
    payload["admission"] = submit_result.get("admission")
    return payload


def _normalize_audio_files(files):
    normalized = []
    seen = set()
    allowed = {".mp3", ".m4a", ".wav", ".aac", ".flac", ".ogg"}
    for item in files or []:
        path = Path(str(item)).expanduser().resolve()
        if not path.exists() or not path.is_file():
            raise ValueError(f"文件不存在: {path}")
        if path.suffix.lower() not in allowed:
            raise ValueError(f"只支持音频文件: {path}")
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        normalized.append(key)
    return normalized
