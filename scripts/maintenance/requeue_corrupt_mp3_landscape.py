#!/usr/bin/env python3
"""Invalidate corrupt MP3-workflow landscape outputs and requeue only that stage."""

import argparse
import sys
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = PROJECT_ROOT / "src" / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


def _video_path(summary):
    if not isinstance(summary, dict):
        return None
    return summary.get("video_path") or summary.get("landscape_video_path")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("job_ids", nargs="+")
    args = parser.parse_args()

    from app import create_app
    from models import db
    from models.task import PublicationTask
    from services.mp3_video_workflow import QUEUE_NAME, _video_file_ready
    from tasks.mp3_video_workflow import process_mp3_video_workflow_job
    from sqlalchemy.orm.attributes import flag_modified

    app = create_app()
    outcomes = []
    with app.app_context():
        for job_id in args.job_ids:
            parent = db.session.get(PublicationTask, job_id)
            if not parent or parent.feature != "mp3_video_workflow":
                outcomes.append({"job_id": job_id, "status": "missing"})
                continue

            result = dict(parent.result or {})
            landscape = result.get("landscape_video") if isinstance(result.get("landscape_video"), dict) else {}
            corrupt_path = _video_path(landscape)
            if _video_file_ready(corrupt_path):
                outcomes.append({"job_id": job_id, "status": "already_valid", "path": corrupt_path})
                continue

            children = dict(result.get("children") or {})
            child_summary = children.get("landscape_video") if isinstance(children.get("landscape_video"), dict) else {}
            child_id = child_summary.get("job_id") or landscape.get("job_id")
            child = db.session.get(PublicationTask, str(child_id)) if child_id else None
            now = datetime.utcnow()
            now_iso = now.isoformat() + "Z"

            if child:
                child_result = dict(child.result or {})
                child_result.update({
                    "invalidated_at": now_iso,
                    "invalidated_reason": "ffprobe_rejected_incomplete_mp4",
                    "invalidated_video_path": corrupt_path or child_result.get("video_path"),
                    "video_path": None,
                    "landscape_video_path": None,
                })
                child.status = "failed"
                child.current_step = "failed"
                child.progress = 0
                child.error_message = "视频完整性校验失败，已安排重新生成横屏"
                child.completed_at = now
                child.task_video_path = None
                child.result = child_result
                flag_modified(child, "result")

            repair_history = list(result.get("landscape_video_repair_history") or [])
            repair_history.append({
                "at": now_iso,
                "child_id": child_id,
                "corrupt_path": corrupt_path,
                "reason": "ffprobe_rejected_incomplete_mp4",
            })
            children.pop("landscape_video", None)
            result.pop("landscape_video", None)
            result["children"] = children
            result["landscape_video_repair_history"] = repair_history[-20:]
            result.pop("completed_at", None)
            result.pop("output_path", None)
            result.pop("force_video_regenerate_at", None)
            if isinstance(result.get("waiting_child"), dict) and result["waiting_child"].get("label") == "landscape_video":
                result.pop("waiting_child", None)
            deferred = dict(result.get("deferred_waits") or {})
            deferred.pop("landscape_video", None)
            if deferred:
                result["deferred_waits"] = deferred
            else:
                result.pop("deferred_waits", None)
            logs = list(result.get("logs") or [])
            logs.append({
                "step": "repair_corrupt_landscape_video",
                "message": "横屏 MP4 完整性校验失败，仅重新生成横屏；复用现有竖屏和素材",
                "progress": 90,
                "updated_at": now_iso,
            })
            result["logs"] = logs[-160:]

            params = dict(parent.params or {})
            workflow = dict(params.get("mp3_video_workflow") or {})
            workflow.pop("force_video_regenerate_at", None)
            params["mp3_video_workflow"] = workflow
            parent.status = "pending"
            parent.current_step = "queued_repair_landscape"
            parent.progress = 90
            parent.error_message = None
            parent.completed_at = None
            parent.params = params
            parent.result = result
            flag_modified(parent, "params")
            flag_modified(parent, "result")
            db.session.commit()

            try:
                queued = process_mp3_video_workflow_job.apply_async(
                    args=[parent.id],
                    queue=QUEUE_NAME,
                    retry=False,
                )
            except Exception as exc:
                parent = db.session.get(PublicationTask, job_id)
                parent.status = "failed"
                parent.current_step = "failed"
                parent.error_message = f"损坏横屏修复入队失败: {exc}"
                parent.completed_at = datetime.utcnow()
                db.session.commit()
                outcomes.append({"job_id": job_id, "status": "enqueue_failed", "error": str(exc)})
                continue

            parent = db.session.get(PublicationTask, job_id)
            parent.celery_task_id = queued.id
            db.session.commit()
            outcomes.append({
                "job_id": job_id,
                "status": "queued",
                "celery_task_id": queued.id,
                "invalidated_child_id": child_id,
            })

    for outcome in outcomes:
        print(outcome)


if __name__ == "__main__":
    main()
