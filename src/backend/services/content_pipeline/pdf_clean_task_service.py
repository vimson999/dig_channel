import logging
import os
from pathlib import Path

from models import db
from models.task import PublicationTask
from tasks.content_pipeline import run_pdf_clean_task


logger = logging.getLogger(__name__)


def submit_pdf_clean_task(
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
    """Create and enqueue a PDF clean task through the shared content pipeline."""
    cleaner = cleaner if isinstance(cleaner, dict) else {}
    auto_publish = auto_publish if isinstance(auto_publish, dict) else {}
    metadata = metadata if isinstance(metadata, dict) else {}
    normalized_files = _normalize_pdf_files(files)
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
            "feature": "content_pdf_clean",
            "user_id": user_id,
            "content_pipeline": {
                "title": (title or "PDF 清洗任务").strip() or "PDF 清洗任务",
                "trigger": trigger,
                "metadata": metadata,
                "auto_publish": auto_publish,
                "pdf_clean": {
                    "files": normalized_files,
                    "source_root": normalized_source_root,
                    "cleaner": {
                        "threshold": int(cleaner.get("threshold") or 245),
                        "do_ocr": bool(cleaner.get("do_ocr", False)),
                        "overwrite": bool(cleaner.get("overwrite", False)),
                        "prefix": cleaner.get("prefix") or "",
                        "engine": cleaner.get("engine") or os.getenv("PDF_CLEANER_ENGINE") or "local",
                        "base_url": cleaner.get("base_url") or None,
                        "cleaner_project_dir": cleaner.get("cleaner_project_dir") or None,
                        "cleaner_batch_root": cleaner.get("cleaner_batch_root") or None,
                        "output_root": cleaner.get("output_root") or None,
                        "poll_interval": cleaner.get("poll_interval") or None,
                        "timeout_seconds": cleaner.get("timeout_seconds") or None,
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
    db.session.commit()

    try:
        celery_task = run_pdf_clean_task.apply_async(args=[task.id], queue="content_pipeline", retry=False)
    except Exception as exc:
        logger.exception("PDF 清洗任务入队失败: %s", task.id)
        task.status = "failed"
        task.current_step = "failed"
        task.error_message = f"PDF 清洗任务入队失败: {exc}"
        db.session.commit()
        raise RuntimeError(task.error_message) from exc

    task.celery_task_id = celery_task.id
    db.session.commit()
    return task.to_dict()


def _normalize_pdf_files(files):
    normalized = []
    seen = set()
    for item in files or []:
        path = Path(str(item)).expanduser().resolve()
        if not path.exists() or not path.is_file():
            raise ValueError(f"文件不存在: {path}")
        if path.suffix.lower() != ".pdf":
            raise ValueError(f"只支持 PDF 文件: {path}")
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        normalized.append(key)
    return normalized
