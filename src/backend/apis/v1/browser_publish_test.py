"""Blueprint factory shared by Cookie-backed platform upload test pages."""

import os
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, FrozenSet, Optional

from flask import Blueprint, jsonify, request
from sqlalchemy.orm import load_only
from werkzeug.utils import secure_filename

from models import db
from models.task import PublicationTask
from services.runtime_guard import release_db_session_quietly
from utils.auth_utils import require_login


LIST_COLUMNS = (
    PublicationTask.id, PublicationTask.celery_task_id, PublicationTask.status,
    PublicationTask.current_step, PublicationTask.progress, PublicationTask.params,
    PublicationTask.result, PublicationTask.error_message, PublicationTask.created_at,
    PublicationTask.updated_at, PublicationTask.completed_at,
)


@dataclass(frozen=True)
class BrowserPublishTestSpec:
    blueprint_name: str
    url_prefix: str
    feature_name: str
    platform_label: str
    upload_subdir: str
    max_upload_env: str
    create_task: Callable[..., PublicationTask]
    enqueue_task: Callable[..., PublicationTask]
    task_payload: Callable[[PublicationTask], Dict[str, Any]]
    cancel_task: Callable[[PublicationTask], Any]
    allowed_visibility: FrozenSet[str]
    title_min_chars: int = 1
    default_visibility: str = "private"
    default_extra: Optional[Dict[str, Any]] = None


def upload_filename_metadata(filename):
    raw = str(filename or "").replace("\\", "/")
    basename = raw.rsplit("/", 1)[-1].strip()
    basename = "".join(char for char in basename if char >= " " and char != "\x7f")[:255]
    extension = Path(basename).suffix.lower()
    return basename or secure_filename(raw) or f"video{extension}", extension


def _truthy(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _project_root():
    return Path(__file__).resolve().parents[4]


def _max_upload_bytes(env_name):
    try:
        megabytes = int(os.environ.get(env_name, "1024") or 1024)
    except (TypeError, ValueError):
        megabytes = 1024
    return max(10, min(megabytes, 4096)) * 1024 * 1024


def create_browser_publish_test_blueprint(spec: BrowserPublishTestSpec):
    bp = Blueprint(spec.blueprint_name, __name__ + "." + spec.blueprint_name, url_prefix=spec.url_prefix)

    def owned_query(current_user):
        return PublicationTask.query.filter(
            PublicationTask.feature == spec.feature_name,
            PublicationTask.user_id == str(getattr(current_user, "id", None)),
        )

    @bp.post("/jobs")
    @require_login
    def create_job(current_user):
        video = request.files.get("video")
        credential_id = str(request.form.get("credential_id") or "").strip()
        title = " ".join(str(request.form.get("title") or "").split())[:100]
        description = str(request.form.get("description") or "").strip()[:3000]
        tags = [item.strip().lstrip("#")[:20] for item in str(request.form.get("tags") or "").replace("，", ",").split(",") if item.strip()][:10]
        submit = _truthy(request.form.get("submit"))
        visibility = str(request.form.get("visibility") or spec.default_visibility).strip().lower()
        if not video or not video.filename:
            return jsonify({"success": False, "message": "请选择测试视频"}), 400
        original_filename, extension = upload_filename_metadata(video.filename)
        if extension not in {".mp4", ".mov", ".m4v"}:
            return jsonify({"success": False, "message": "仅支持 MP4、MOV、M4V 视频"}), 400
        if not credential_id:
            return jsonify({"success": False, "message": f"请选择{spec.platform_label}账号凭据"}), 400
        if len("".join(title.split())) < spec.title_min_chars:
            suffix = f"至少需要 {spec.title_min_chars} 个字符" if spec.title_min_chars > 1 else "不能为空"
            return jsonify({"success": False, "message": f"{spec.platform_label}标题{suffix}"}), 400
        if visibility not in spec.allowed_visibility:
            return jsonify({"success": False, "message": f"不支持的{spec.platform_label}可见范围"}), 400
        if submit and request.form.get("publish_confirmation") != "PUBLISH":
            return jsonify({"success": False, "message": "确认发布校验失败，请重新勾选"}), 400
        if request.content_length and request.content_length > _max_upload_bytes(spec.max_upload_env):
            return jsonify({"success": False, "message": "测试视频超过允许大小"}), 413
        upload_dir = _project_root() / "data" / "uploads" / spec.upload_subdir / datetime.utcnow().strftime("%Y%m%d")
        upload_dir.mkdir(parents=True, exist_ok=True)
        video_path = upload_dir / f"{uuid.uuid4().hex}{extension}"
        try:
            video.save(video_path)
            if not video_path.is_file() or video_path.stat().st_size <= 0:
                raise OSError("上传文件为空")
            task = spec.create_task(
                current_user=current_user, video_path=video_path, title=title,
                credential_id=credential_id, description=description, tags=tags,
                visibility=visibility, submit=submit, original_filename=original_filename,
                source_label=f"{spec.feature_name}:manual_upload", video_key="manual",
                **dict(spec.default_extra or {}),
            )
            spec.enqueue_task(task)
        except ValueError as exc:
            video_path.unlink(missing_ok=True)
            return jsonify({"success": False, "message": str(exc)}), 400
        except RuntimeError as exc:
            return jsonify({"success": False, "message": str(exc)}), 502
        except Exception as exc:
            video_path.unlink(missing_ok=True)
            return jsonify({"success": False, "message": f"测试视频保存失败: {exc}"}), 500
        payload = spec.task_payload(task)
        release_db_session_quietly()
        return jsonify({"success": True, "data": payload}), 201

    @bp.get("/jobs")
    @require_login
    def list_jobs(current_user):
        rows = owned_query(current_user).options(load_only(*LIST_COLUMNS)).order_by(PublicationTask.created_at.desc()).limit(20).all()
        payload = [spec.task_payload(row) for row in rows]
        release_db_session_quietly()
        return jsonify({"success": True, "data": {"items": payload}})

    @bp.get("/jobs/<job_id>")
    @require_login
    def get_job(current_user, job_id):
        task = owned_query(current_user).options(load_only(*LIST_COLUMNS)).filter(PublicationTask.id == str(job_id)).one_or_none()
        if not task:
            release_db_session_quietly()
            return jsonify({"success": False, "message": "任务不存在"}), 404
        payload = spec.task_payload(task)
        release_db_session_quietly()
        return jsonify({"success": True, "data": payload})

    @bp.post("/jobs/<job_id>/cancel")
    @require_login
    def cancel_job(current_user, job_id):
        task = owned_query(current_user).filter(PublicationTask.id == str(job_id)).one_or_none()
        if not task:
            return jsonify({"success": False, "message": "任务不存在"}), 404
        ok, message = spec.cancel_task(task)
        payload = getattr(task, "_browser_publish_cancel_response_payload", None)
        if payload is None:
            payload = spec.task_payload(task)
        release_db_session_quietly()
        return jsonify({"success": ok, "message": message, "data": payload}), (200 if ok else 409)

    return bp
