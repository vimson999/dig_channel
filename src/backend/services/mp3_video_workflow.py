import base64
import json
import logging
import mimetypes
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps
from sqlalchemy import or_
from sqlalchemy.orm.attributes import flag_modified

from celery_config import celery_app
from models import db
from models.task import PublicationTask
from models.zsxq_sync import ZsxqAttachment, ZsxqTopic
from services.asr_cache_service import probe_media_duration
from services.gemini_browser import (
    GeminiBrowserOptions,
    GeminiBrowserWorker,
    _looks_like_page_shell_text,
    is_configured as gemini_is_configured,
)
from services.workflow_notification_service import workflow_notification_service
from tasks.publisher import run_publication_pipeline


logger = logging.getLogger(__name__)
FEATURE_NAME = "mp3_video_workflow"
QUEUE_NAME = "mp3_video_workflow"
DEFAULT_GEMINI_URL = "https://gemini.google.com/gem/f9e84d5e215c"
DEFAULT_SPEED = 1.2
TERMINAL_STATUSES = {"completed", "failed", "canceled"}
CELERY_FAILURE_STATES = {"FAILURE", "REVOKED"}
AUDIO_EXTENSIONS = {"mp3", "m4a", "wav", "aac", "flac", "ogg", "opus"}
PLANNER_VERSION = "mp3_video_planner_20260618_v2"
VISUAL_ARTIFACT_KEYS = (
    "finance_cover",
    "audio_background",
    "xiaojunjun_cover",
    "xiaojunjun_background",
)
VISUAL_ARTIFACT_VERSION = "mp3_video_visuals_20260618_v3"

INFOGRAPHIC_PROMPT = (
    "根据以上内容\n"
    "请创造一张横向排版的信息图，以简短、干净、极简为排版原则；视觉上采用手绘笔记风格，"
    "并严格参照《华尔街日报》经典插图的黑白钢笔画、点刻/Hedcut、细密排线表现形式来呈现主题图案；"
    "所有说明文字请使用简体中文；排版中不得出现任何机构名称或字符串“FCCNN88”；"
    "保持整体手绘质感，图文协调，适合直接用于展示。"
)


class Mp3VideoWorkflowError(RuntimeError):
    pass


def project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def storage_dir_for_job(job_id: str, created_at: Optional[datetime] = None) -> Path:
    created_at = created_at or datetime.utcnow()
    root = project_root() / "data" / "processed" / FEATURE_NAME / created_at.strftime("%Y%m%d") / str(job_id)
    root.mkdir(parents=True, exist_ok=True)
    return root


def storage_path_for_job(job_id: str, created_at: Optional[datetime] = None) -> str:
    return str(storage_dir_for_job(job_id, created_at) / "workflow_result.json")


def artifact_path_for_job(job_id: str, name: str, ext: str = "jpg", created_at: Optional[datetime] = None) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(name or "artifact")).strip("_") or "artifact"
    return str(storage_dir_for_job(job_id, created_at) / f"{safe}.{ext.lstrip('.')}")


def status_payload() -> Dict[str, Any]:
    deepseek_configured = bool(os.environ.get("MP3_VIDEO_PLANNER_API_KEY") or os.environ.get("DEEPSEEK_API_KEY"))
    options = gemini_options_from_env()
    return {
        "configured": gemini_is_configured(options),
        "mode": "celery_orchestrator",
        "gemini_url": options.gemini_url,
        "deepseek_configured": deepseek_configured,
        "planner_provider": "deepseek" if deepseek_configured else "local_fallback",
        "planner_model": planner_model() if deepseek_configured else None,
        "business_feature": FEATURE_NAME,
    }


def gemini_options_from_env() -> GeminiBrowserOptions:
    options = GeminiBrowserOptions.from_env()
    options.gemini_url = _env("MP3_VIDEO_GEMINI_URL", "GEMINI_MP3_VIDEO_URL", DEFAULT_GEMINI_URL)
    options.response_timeout_seconds = _int_env("MP3_VIDEO_GEMINI_RESPONSE_TIMEOUT", max(options.response_timeout_seconds, 900))
    options.image_timeout_seconds = _int_env("MP3_VIDEO_GEMINI_IMAGE_TIMEOUT", max(options.image_timeout_seconds, 900))
    options.total_timeout_seconds = _int_env("MP3_VIDEO_GEMINI_TOTAL_TIMEOUT", max(options.total_timeout_seconds, 2400))
    return options


def list_audio_assets(
    *,
    page: int = 1,
    per_page: int = 30,
    q: str = "",
    group_id: str = "",
    tab_id: str = "",
) -> Dict[str, Any]:
    page = max(1, int(page or 1))
    per_page = max(1, min(int(per_page or 30), 100))
    q = (q or "").strip()
    group_id = (group_id or "").strip()
    tab_id = (tab_id or "").strip()

    topic_join = (
        (ZsxqTopic.group_id == ZsxqAttachment.group_id)
        & (ZsxqTopic.tab_id == ZsxqAttachment.tab_id)
        & (ZsxqTopic.topic_id == ZsxqAttachment.topic_id)
    )
    query = (
        db.session.query(
            ZsxqAttachment.id.label("attachment_id"),
            ZsxqAttachment.name.label("attachment_name"),
            ZsxqAttachment.download_count.label("download_count"),
            ZsxqAttachment.local_path.label("local_path"),
            ZsxqAttachment.file_ext.label("file_ext"),
            ZsxqAttachment.updated_at.label("attachment_updated_at"),
            ZsxqAttachment.created_at.label("attachment_created_at"),
            ZsxqAttachment.group_id.label("group_id"),
            ZsxqAttachment.tab_id.label("tab_id"),
            ZsxqAttachment.topic_id.label("topic_id"),
            ZsxqTopic.title.label("topic_title"),
            ZsxqTopic.content.label("topic_content"),
            ZsxqTopic.create_time.label("topic_create_time"),
        )
        .outerjoin(ZsxqTopic, topic_join)
        .filter(ZsxqAttachment.download_status == "downloaded")
        .filter(ZsxqAttachment.local_path.isnot(None))
        .filter(_audio_asset_filter())
    )
    if group_id:
        query = query.filter(ZsxqAttachment.group_id == group_id)
    if tab_id:
        query = query.filter(ZsxqAttachment.tab_id == tab_id)
    if q:
        pattern = f"%{q}%"
        query = query.filter(or_(
            ZsxqAttachment.name.ilike(pattern),
            ZsxqAttachment.local_path.ilike(pattern),
            ZsxqTopic.title.ilike(pattern),
            ZsxqTopic.content.ilike(pattern),
        ))

    target_count = page * per_page + 1
    chunk_size = min(max(per_page * 4, 40), 120)
    scan_limit = min(max(target_count * 5, 120), 2500)
    ordered = query.order_by(ZsxqAttachment.updated_at.desc(), ZsxqAttachment.created_at.desc())
    valid_items: List[Dict[str, Any]] = []
    skipped_missing = 0
    scanned = 0
    while len(valid_items) < target_count and scanned < scan_limit:
        batch_limit = min(chunk_size, scan_limit - scanned)
        rows = ordered.offset(scanned).limit(batch_limit).all()
        if not rows:
            break
        scanned += len(rows)
        for row in rows:
            item = _audio_asset_row_payload(row)
            if item:
                valid_items.append(item)
            else:
                skipped_missing += 1
        if len(rows) < batch_limit:
            break

    start = (page - 1) * per_page
    end = start + per_page
    return {
        "items": valid_items[start:end],
        "pagination": {
            "page": page,
            "per_page": per_page,
            "has_more": len(valid_items) > end or scanned >= scan_limit,
            "skipped_missing": skipped_missing,
            "scanned": scanned,
        },
    }


def audio_meta_from_zsxq_attachment(asset_id: str) -> Dict[str, Any]:
    asset_id = str(asset_id or "").strip()
    if not asset_id:
        raise ValueError("请选择一个已同步音频")
    row = (
        db.session.query(
            ZsxqAttachment.id.label("attachment_id"),
            ZsxqAttachment.name.label("attachment_name"),
            ZsxqAttachment.download_count.label("download_count"),
            ZsxqAttachment.local_path.label("local_path"),
            ZsxqAttachment.file_ext.label("file_ext"),
            ZsxqAttachment.updated_at.label("attachment_updated_at"),
            ZsxqAttachment.created_at.label("attachment_created_at"),
            ZsxqAttachment.group_id.label("group_id"),
            ZsxqAttachment.tab_id.label("tab_id"),
            ZsxqAttachment.topic_id.label("topic_id"),
            ZsxqTopic.title.label("topic_title"),
            ZsxqTopic.content.label("topic_content"),
            ZsxqTopic.create_time.label("topic_create_time"),
        )
        .outerjoin(
            ZsxqTopic,
            (ZsxqTopic.group_id == ZsxqAttachment.group_id)
            & (ZsxqTopic.tab_id == ZsxqAttachment.tab_id)
            & (ZsxqTopic.topic_id == ZsxqAttachment.topic_id),
        )
        .filter(ZsxqAttachment.id == asset_id)
        .filter(ZsxqAttachment.download_status == "downloaded")
        .filter(ZsxqAttachment.local_path.isnot(None))
        .filter(_audio_asset_filter())
        .first()
    )
    item = _audio_asset_row_payload(row) if row else None
    if not item:
        raise ValueError("音频不存在、未下载或本地文件缺失")
    try:
        item["duration_seconds"] = probe_media_duration(item["path"])
    except Exception:
        item["duration_seconds"] = 0
    return item


def process_job(task_id: str) -> bool:
    task = PublicationTask.query.get(task_id)
    if not task:
        raise Mp3VideoWorkflowError(f"MP3 视频编排任务不存在: {task_id}")
    if not isinstance(task.params, dict) or task.params.get("feature") != FEATURE_NAME:
        raise Mp3VideoWorkflowError(f"无效的 MP3 视频编排任务参数: {task_id}")
    if task.status == "canceled":
        return False

    workflow = task.params.get(FEATURE_NAME) or {}
    audio = workflow.get("audio") if isinstance(workflow.get("audio"), dict) else {}
    audio_path = _resolve_project_path(audio.get("path"))
    if not audio_path or not audio_path.exists():
        _mark_failed(task, f"音频文件不存在: {audio.get('path')}")
        _queue_notification(task.id, "mp3_video_failed", f"音频文件不存在: {audio.get('path')}")
        return False

    user_id = task.user_id or (task.params or {}).get("user_id")
    created_at = task.created_at or datetime.utcnow()
    _start_task(task)
    _queue_notification(task.id, "mp3_video_started", "MP3 视频编排已启动")

    try:
        image_path = artifact_path_for_job(task.id, "gemini_infographic", "png", created_at)
        gemini_result_path = artifact_path_for_job(task.id, "gemini_result", "json", created_at)
        existing = _clear_invalid_gemini_shell_result(task.id, _fresh_result(task.id))
        gemini_result, first_response, infographic = _reusable_gemini_result(existing, fallback_image_path=image_path)
        if gemini_result:
            _emit(task.id, "gemini_reused", 54, "复用已完成的 Gemini 第一轮返回和横向信息图")
            _update_result(task.id, {
                "gemini": _public_gemini_result(gemini_result),
                "initial_response_text": first_response,
                "infographic": _public_artifact(infographic, "Gemini 横向信息图"),
            })
        else:
            _emit(task.id, "gemini_upload_audio", 8, "上传 MP3 到 Gemini Gem 并等待第一轮返回")
            _queue_notification(task.id, "mp3_video_gemini_started", "上传 MP3 到 Gemini Gem 并等待第一轮返回")
            gemini_result = GeminiBrowserWorker(gemini_options_from_env()).generate_from_file(
                job_id=task.id,
                file_path=str(audio_path),
                output_path=gemini_result_path,
                prompt="",
                target_url=(workflow.get("gemini_url") or DEFAULT_GEMINI_URL),
                image_followup_prompt=(workflow.get("image_prompt") or INFOGRAPHIC_PROMPT),
                image_output_path=image_path,
                progress_callback=lambda event: _emit(
                    task.id,
                    f"gemini_{event.get('step') or 'running'}",
                    min(54, 8 + int((event.get("progress") or 0) * 0.46)),
                    event.get("message") or "Gemini 自动化执行中",
                    result=event.get("result"),
                ),
            )
            first_response = gemini_result.get("initial_response_text") or gemini_result.get("response_text") or ""
            infographic = gemini_result.get("image") or {"path": image_path}
            _update_result(task.id, {
                "gemini": _public_gemini_result(gemini_result),
                "initial_response_text": first_response,
                "infographic": _public_artifact(infographic, "Gemini 横向信息图"),
            })
        _queue_notification(task.id, "mp3_video_gemini_completed", "Gemini 第一轮返回和横向信息图已生成")

        existing = _fresh_result(task.id)
        plan = _reusable_plan(existing)
        if plan:
            audio = existing.get("audio") if isinstance(existing.get("audio"), dict) else audio
            _emit(task.id, "deepseek_plan_reused", 58, "复用已完成的 DS v4 pro 标题和音频时间戳规划")
        else:
            _emit(task.id, "deepseek_plan", 58, "DS v4 pro 选择标题和音频时间戳")
            duration = float(audio.get("duration_seconds") or 0) or probe_media_duration(str(audio_path))
            audio["duration_seconds"] = duration
            plan = plan_from_first_response(first_response, audio=audio)
            _update_result(task.id, {"plan": plan, "audio": audio})
        _queue_notification(task.id, "mp3_video_plan_completed", "DS v4 pro 已完成标题、封面文案和音频时间戳规划")

        existing = _fresh_result(task.id)
        artifacts = existing.get("artifacts") if isinstance(existing.get("artifacts"), dict) else {}
        if _visual_artifacts_ready(artifacts):
            _emit(task.id, "generate_visual_assets_reused", 68, "复用已生成的四张封皮/背景图")
        else:
            _emit(task.id, "generate_visual_assets", 68, "生成四张封皮/背景图")
            artifacts = generate_visual_artifacts(
                task.id,
                plan=plan,
                infographic_path=infographic.get("path") or image_path,
                created_at=created_at,
            )
            _update_result(task.id, {"artifacts": artifacts})
        _queue_notification(task.id, "mp3_video_visuals_completed", "四张封皮/背景图已生成")

        existing = _fresh_result(task.id)
        landscape_summary, landscape_task = _reusable_video_summary(existing, "landscape_video")
        if landscape_summary:
            _emit(task.id, "landscape_video_reused", 90, "复用已完成的小君君横屏视频")
            if landscape_task:
                _record_child(task.id, "landscape_video", landscape_task)
            _update_result(task.id, {"landscape_video": landscape_summary})
        else:
            _emit(task.id, "submit_landscape_video", 78, "提交小君君横屏视频合成任务")
            landscape_task = create_publisher_video_task(
                parent_id=task.id,
                user_id=user_id,
                audio=audio,
                plan=plan,
                image_path=artifacts["xiaojunjun_background"]["path"],
                role="xiaojunjun_landscape",
                aspect_ratio="16:9",
            )
            _record_child(task.id, "landscape_video", landscape_task)
            _queue_notification(task.id, "mp3_video_landscape_submitted", "小君君横屏视频合成任务已提交")
            landscape_task = _wait_child(task.id, landscape_task.id, "landscape_video", start_progress=80, end_progress=90)
            if landscape_task.status != "completed":
                raise RuntimeError(f"横屏视频子任务失败: {landscape_task.error_message or (landscape_task.result or {}).get('error')}")
            landscape_summary = _publisher_result_summary(landscape_task)
            _update_result(task.id, {"landscape_video": landscape_summary})
        _queue_notification(task.id, "mp3_video_landscape_completed", "小君君横屏视频已完成")

        existing = _fresh_result(task.id)
        vertical_summary, vertical_task = _reusable_video_summary(existing, "vertical_video")
        if vertical_summary:
            _emit(task.id, "vertical_video_reused", 99, "复用已完成的此刻此声竖屏视频")
            if vertical_task:
                _record_child(task.id, "vertical_video", vertical_task)
            _update_result(task.id, {"vertical_video": vertical_summary})
        else:
            _emit(task.id, "submit_vertical_video", 91, "提交此刻此声竖屏视频合成任务")
            vertical_task = create_publisher_video_task(
                parent_id=task.id,
                user_id=user_id,
                audio=audio,
                plan=plan,
                image_path=artifacts["audio_background"]["path"],
                role="ciketc_vertical",
                aspect_ratio="9:16",
            )
            _record_child(task.id, "vertical_video", vertical_task)
            _queue_notification(task.id, "mp3_video_vertical_submitted", "此刻此声竖屏视频合成任务已提交")
            vertical_task = _wait_child(task.id, vertical_task.id, "vertical_video", start_progress=92, end_progress=99)
            if vertical_task.status != "completed":
                raise RuntimeError(f"竖屏视频子任务失败: {vertical_task.error_message or (vertical_task.result or {}).get('error')}")
            vertical_summary = _publisher_result_summary(vertical_task)
            _update_result(task.id, {"vertical_video": vertical_summary})
        _queue_notification(task.id, "mp3_video_vertical_completed", "此刻此声竖屏视频已完成")

        output_path = storage_path_for_job(task.id, created_at)
        fresh = PublicationTask.query.get(task.id)
        final_result = dict(fresh.result or {})
        final_result.update({
            "output_path": output_path,
            "completed_at": datetime.utcnow().isoformat() + "Z",
        })
        Path(output_path).write_text(json.dumps({
            "workflow_task_id": task.id,
            "audio": audio,
            "gemini": _public_gemini_result(gemini_result),
            "plan": plan,
            "artifacts": artifacts,
            "landscape_video": landscape_summary,
            "vertical_video": vertical_summary,
            "completed_at": final_result["completed_at"],
        }, ensure_ascii=False, indent=2), encoding="utf-8")

        fresh.status = "completed"
        fresh.current_step = "done"
        fresh.progress = 100
        fresh.completed_at = datetime.utcnow()
        fresh.error_message = None
        fresh.result = final_result
        flag_modified(fresh, "result")
        db.session.commit()
        _queue_notification(task.id, "mp3_video_completed", "MP3 视频编排完成，横屏和竖屏视频均已生成")
        return True
    except Exception as exc:
        mark_job_failed_safely(task_id, str(exc), notify=True)
        logger.error("MP3 video workflow failed: %s", exc, exc_info=True)
        return False


def plan_from_first_response(first_response: str, *, audio: Dict[str, Any]) -> Dict[str, Any]:
    first_response = (first_response or "").strip()
    if not first_response:
        raise Mp3VideoWorkflowError("Gemini 第一轮返回为空，无法规划视频")
    fallback = fallback_plan(first_response, audio=audio)
    try:
        payload = deepseek_plan(first_response, audio=audio)
    except Exception as exc:
        fallback["planner_error"] = str(exc)
        return fallback
    return validate_plan(payload, fallback=fallback, audio=audio)


def deepseek_plan(first_response: str, *, audio: Dict[str, Any]) -> Dict[str, Any]:
    api_key = os.environ.get("MP3_VIDEO_PLANNER_API_KEY") or os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        return {}
    base_url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    timeout = _int_env("MP3_VIDEO_PLANNER_TIMEOUT", 120)
    duration = float(audio.get("duration_seconds") or 0)
    system_prompt = (
        "你是财经音频视频编排策划器。你只能基于输入的 Gemini 第一轮返回做选择和规整，"
        "不得新增事实。输出严格 JSON，不要 Markdown，不要解释。"
    )
    user_prompt = "\n".join([
        "请从 Gemini 第一轮返回中选择一个最适合成片的标题版本，并规划视频封面与音频截取。",
        "必须输出 JSON 字段：",
        "- title: 最终视频标题，优先选择原文中已给出的标题版本，可轻微润色",
        "- title_options: 字符串数组，最多 5 个候选标题",
        "- subtitle: 一句事实型副标题，30 字以内",
        "- cover_main_title: 此刻此声横屏封皮栏目/场景主标题，默认可用“内资闭门会议”，不要把长内容标题放这里",
        "- cover_title: 此刻此声竖屏纯音频背景板主标题，短促有冲击力，可用 \\n 分两行，每行不超过 10 个中文字符",
        "- cover_subtitle: 此刻此声横屏封皮/竖屏背景看点文案，2-3 行，每行不超过 18 个中文字符，只基于原文事实",
        "- xiaojunjun_title: 小君君横屏封皮/背景栏目主标题，默认可用“内资闭门会议”，不要把长内容标题放这里",
        "- xiaojunjun_subtitle: 小君君横屏封皮/背景黄色高亮条文案，2-3 行，每行不超过 18 个中文字符，只基于原文事实",
        "- date_text: 日期，格式 YYYY.M.D",
        "- emotion: pro/macro/bull/bear 之一",
        "- audio: 对象，包含 start_seconds、end_seconds、speed。speed 必须是 1.2；start/end 要按 Gemini 返回中最适合成片的内容段落判断，如果没有明确范围则 start_seconds=0、end_seconds=null。",
        "",
        f"音频文件名：{audio.get('filename') or audio.get('name') or ''}",
        f"音频总时长秒：{duration:.3f}" if duration else "音频总时长秒：未知",
        "",
        "【Gemini 第一轮返回】",
        _compact_text(first_response, _int_env("MP3_VIDEO_PLANNER_INPUT_CHARS", 18000)),
    ])
    response = requests.post(
        f"{base_url.rstrip('/')}/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": planner_model(),
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.1,
        },
        timeout=timeout,
    )
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        raise Mp3VideoWorkflowError(f"DeepSeek 规划失败: HTTP {response.status_code} {response.text[:600]}") from exc
    content = response.json()["choices"][0]["message"]["content"]
    payload = _json_from_text(content)
    if isinstance(payload, dict):
        payload["provider"] = "deepseek"
    return payload if isinstance(payload, dict) else {}


def fallback_plan(first_response: str, *, audio: Dict[str, Any]) -> Dict[str, Any]:
    candidates = extract_title_candidates(first_response)
    title = candidates[0] if candidates else _clip_text(_clean_line(first_response), 36) or "此刻此声精选"
    subtitle = extract_subtitle(first_response)
    duration = float(audio.get("duration_seconds") or 0)
    return {
        "title": title,
        "title_options": candidates[:5],
        "subtitle": subtitle,
        "cover_main_title": "内资闭门会议",
        "cover_title": _cover_lines(title, max_chars=10),
        "cover_subtitle": _cover_lines(subtitle or title, max_chars=18, max_lines=3),
        "xiaojunjun_title": "内资闭门会议",
        "xiaojunjun_subtitle": _cover_lines(subtitle or title, max_chars=18, max_lines=3),
        "date_text": datetime.now().strftime("%Y.%m.%d"),
        "emotion": "bull",
        "audio": {"start_seconds": 0, "end_seconds": None if duration else None, "speed": DEFAULT_SPEED},
        "provider": "local_fallback",
        "planner_version": PLANNER_VERSION,
    }


def validate_plan(payload: Dict[str, Any], *, fallback: Dict[str, Any], audio: Dict[str, Any]) -> Dict[str, Any]:
    payload = payload if isinstance(payload, dict) else {}
    duration = float(audio.get("duration_seconds") or 0)
    title = _clip_text(payload.get("title") or fallback["title"], 80)
    subtitle = _clip_text(payload.get("subtitle") or fallback.get("subtitle"), 60)
    cover_main_title = _clip_text(payload.get("cover_main_title") or fallback.get("cover_main_title") or "内资闭门会议", 16)
    cover_title = _clean_multiline(payload.get("cover_title") or fallback.get("cover_title"))
    x_title = _clean_multiline(payload.get("xiaojunjun_title") or fallback.get("xiaojunjun_title"))
    plan_audio = payload.get("audio") if isinstance(payload.get("audio"), dict) else {}
    start = _parse_seconds(plan_audio.get("start_seconds"), default=0.0)
    end = _parse_seconds(plan_audio.get("end_seconds"), default=None)
    start = max(0.0, start or 0.0)
    if duration and start >= duration:
        start = 0.0
    if end is not None:
        end = max(start + 0.1, float(end))
        if duration:
            end = min(duration, end)
        if end <= start + 0.1:
            end = None
    title_options = payload.get("title_options") if isinstance(payload.get("title_options"), list) else fallback.get("title_options", [])
    return {
        "title": title,
        "title_options": [_clip_text(item, 80) for item in title_options if _clean_line(item)][:5],
        "subtitle": subtitle,
        "cover_main_title": cover_main_title or "内资闭门会议",
        "cover_title": _cover_lines(cover_title or title, max_chars=10),
        "cover_subtitle": _cover_lines(payload.get("cover_subtitle") or fallback.get("cover_subtitle") or subtitle or title, max_chars=18, max_lines=3),
        "xiaojunjun_title": _clip_text(x_title or payload.get("cover_main_title") or fallback.get("xiaojunjun_title") or "内资闭门会议", 16),
        "xiaojunjun_subtitle": _cover_lines(payload.get("xiaojunjun_subtitle") or fallback.get("xiaojunjun_subtitle") or subtitle or title, max_chars=18, max_lines=3),
        "date_text": _clip_text(payload.get("date_text") or fallback.get("date_text"), 20),
        "emotion": payload.get("emotion") if payload.get("emotion") in {"pro", "macro", "bull", "bear"} else fallback.get("emotion", "bull"),
        "audio": {"start_seconds": round(start, 3), "end_seconds": round(end, 3) if end is not None else None, "speed": DEFAULT_SPEED},
        "provider": payload.get("provider") or fallback.get("provider") or "local_fallback",
        "planner_error": fallback.get("planner_error"),
        "planner_version": PLANNER_VERSION,
    }


def generate_visual_artifacts(
    job_id: str,
    *,
    plan: Dict[str, Any],
    infographic_path: str,
    created_at: Optional[datetime] = None,
) -> Dict[str, Dict[str, Any]]:
    return generate_visual_artifacts_with_browser(
        job_id,
        plan=plan,
        infographic_path=infographic_path,
        created_at=created_at,
    )


def generate_visual_artifacts_with_browser(
    job_id: str,
    *,
    plan: Dict[str, Any],
    infographic_path: str,
    created_at: Optional[datetime] = None,
) -> Dict[str, Dict[str, Any]]:
    finance_cover = artifact_path_for_job(job_id, "ciketc_landscape_cover_1", "jpg", created_at)
    audio_background = artifact_path_for_job(job_id, "ciketc_vertical_audio_background_1", "jpg", created_at)
    xiaojunjun_cover = artifact_path_for_job(job_id, "xiaojunjun_landscape_cover", "jpg", created_at)
    xiaojunjun_background = artifact_path_for_job(job_id, "xiaojunjun_landscape_background", "jpg", created_at)

    infographic_data_url = _image_data_url(infographic_path)
    with _cover_browser_context() as context:
        _render_canvas_artifact(
            context,
            page_name="finance-cover-generator.html",
            function_name="renderWorkflowFinanceCover",
            payload=_finance_cover_payload(plan),
            output_path=finance_cover,
        )
        _render_canvas_artifact(
            context,
            page_name="thumbnail-workbench.html",
            function_name="renderWorkflowThumbnail",
            payload=_audio_background_payload(plan, infographic_data_url),
            output_path=audio_background,
        )
        _render_canvas_artifact(
            context,
            page_name="closed-door-card-cover-generator.html",
            function_name="renderWorkflowClosedDoorCover",
            payload=_closed_door_payload(plan, include_date=True),
            output_path=xiaojunjun_cover,
        )
        _render_canvas_artifact(
            context,
            page_name="closed-door-card-cover-generator.html",
            function_name="renderWorkflowClosedDoorCover",
            payload=_closed_door_payload(plan, include_date=False),
            output_path=xiaojunjun_background,
        )

    return {
        "finance_cover": _artifact_meta(finance_cover, "此刻此声-横屏视频封皮 1", renderer="browser:finance-cover-generator"),
        "audio_background": _artifact_meta(audio_background, "此刻此声-竖屏视频背景图 1", renderer="browser:thumbnail-workbench"),
        "xiaojunjun_cover": _artifact_meta(xiaojunjun_cover, "小君君-横屏视频封皮", renderer="browser:closed-door-card-cover-generator"),
        "xiaojunjun_background": _artifact_meta(xiaojunjun_background, "小君君 横屏视频 背景", renderer="browser:closed-door-card-cover-generator"),
    }


def _cover_browser_context():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise Mp3VideoWorkflowError("缺少 playwright 依赖，无法调用前端封面生成器") from exc

    class _ContextManager:
        def __enter__(self):
            self._playwright = sync_playwright().start()
            headless = _bool_env("MP3_VIDEO_COVER_BROWSER_HEADLESS", True)
            kwargs = {
                "headless": headless,
                "args": ["--disable-dev-shm-usage", "--no-sandbox"],
            }
            chrome_path = _cover_chrome_executable_path()
            chrome_channel = _env(
                "MP3_VIDEO_COVER_BROWSER_CHANNEL",
                "REPORT_PDF_GEMINI_CHANNEL",
                os.environ.get("GEMINI_BROWSER_CHANNEL") or "",
            )
            if chrome_path:
                kwargs["executable_path"] = chrome_path
            elif chrome_channel:
                kwargs["channel"] = chrome_channel
            else:
                kwargs["channel"] = "chrome"
            if headless and (chrome_path or kwargs.get("channel")):
                kwargs["headless"] = False
                kwargs["args"].append("--headless=new")
            self._browser = self._playwright.chromium.launch(**kwargs)
            self._context = self._browser.new_context(
                viewport={"width": 1440, "height": 1000},
                device_scale_factor=1,
            )
            self._context.set_default_timeout(_int_env("MP3_VIDEO_COVER_BROWSER_TIMEOUT_MS", 30000))
            return self._context

        def __exit__(self, exc_type, exc, tb):
            try:
                self._context.close()
            finally:
                try:
                    self._browser.close()
                finally:
                    self._playwright.stop()

    return _ContextManager()


def _cover_chrome_executable_path() -> str:
    configured = _env(
        "MP3_VIDEO_COVER_CHROME_PATH",
        "REPORT_PDF_GEMINI_CHROME_PATH",
        os.environ.get("GEMINI_BROWSER_CHROME_PATH") or "",
    )
    if configured and Path(configured).expanduser().exists():
        return str(Path(configured).expanduser())
    for candidate in (
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/usr/bin/chromium-browser",
        "/usr/bin/chromium",
    ):
        if os.path.exists(candidate):
            return candidate
    return ""


def _render_canvas_artifact(context, *, page_name: str, function_name: str, payload: Dict[str, Any], output_path: str) -> None:
    page = context.new_page()
    try:
        _goto_frontend_page(page, page_name)
        page.wait_for_function(f"() => typeof window.{function_name} === 'function'")
        result = page.evaluate(f"(payload) => window.{function_name}(payload)", payload)
        data_url = result.get("dataUrl") if isinstance(result, dict) else None
        _write_data_url(data_url, output_path)
    finally:
        page.close()


def _goto_frontend_page(page, page_name: str) -> str:
    last_error: Optional[Exception] = None
    for url in _frontend_page_urls(page_name):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=_int_env("MP3_VIDEO_COVER_PAGE_TIMEOUT_MS", 25000))
            return url
        except Exception as exc:
            last_error = exc
    raise Mp3VideoWorkflowError(f"无法打开前端封面页面 {page_name}: {last_error}")


def _frontend_page_urls(page_name: str) -> List[str]:
    urls = []
    base_url = _env("MP3_VIDEO_FRONTEND_BASE_URL", "FRONTEND_BASE_URL", "http://localhost:8888").strip()
    if base_url and base_url.lower() not in {"file", "file://"}:
        urls.append(f"{base_url.rstrip('/')}/{page_name.lstrip('/')}")
    local_path = project_root() / "src" / "frontend" / page_name
    if local_path.exists():
        urls.append(local_path.resolve().as_uri())
    return urls


def _finance_cover_payload(plan: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "mainTitle": _cover_main_title(plan),
        "subTitle": _highlight_text(plan, preferred_key="cover_subtitle"),
        "dateText": plan.get("date_text") or datetime.now().strftime("%Y.%m.%d"),
        "mainColor": "#ffffff",
        "subColor": "#FFE900",
        "mimeType": "image/jpeg",
        "quality": 0.95,
    }


def _audio_background_payload(plan: Dict[str, Any], infographic_data_url: str) -> Dict[str, Any]:
    return {
        "format": "video_bg_audio",
        "showGuides": False,
        "inst": "深度研报",
        "mainTitle": _cover_lines(plan.get("cover_title") or plan.get("title"), max_chars=10, max_lines=2),
        "subTitle": _cover_lines(plan.get("cover_subtitle") or plan.get("subtitle"), max_chars=18, max_lines=2),
        "footerText": "此刻此声",
        "emotion": plan.get("emotion") if plan.get("emotion") in {"pro", "macro", "bull", "bear"} else "bull",
        "imageDataUrl": infographic_data_url,
        "mimeType": "image/jpeg",
        "quality": 0.95,
    }


def _closed_door_payload(plan: Dict[str, Any], *, include_date: bool) -> Dict[str, Any]:
    return {
        "mainTitle": plan.get("xiaojunjun_title") or _cover_main_title(plan),
        "subTitle": _highlight_text(plan, preferred_key="xiaojunjun_subtitle"),
        "dateText": plan.get("date_text") if include_date else "",
        "subtitleSafeSpace": False,
        "mainColor": "#FFFFFF",
        "subColor": "#FFE900",
        "backgroundDataUrl": _closed_door_reference_data_url(),
        "mimeType": "image/jpeg",
        "quality": 0.95,
    }


def _cover_main_title(plan: Dict[str, Any]) -> str:
    return _clip_text(plan.get("cover_main_title") or "内资闭门会议", 16) or "内资闭门会议"


def _highlight_text(plan: Dict[str, Any], *, preferred_key: str) -> str:
    value = plan.get(preferred_key) or plan.get("cover_subtitle") or plan.get("subtitle") or plan.get("title")
    return _cover_lines(value, max_chars=18, max_lines=3)


def _image_data_url(path_value: str) -> str:
    path = _resolve_project_path(path_value)
    if not path or not path.exists():
        raise Mp3VideoWorkflowError(f"前端封面生成器需要的图片不存在: {path_value}")
    mime = mimetypes.guess_type(str(path))[0] or "image/png"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _closed_door_reference_data_url() -> str:
    path = project_root() / "src" / "frontend" / "assets" / "cover" / "default" / "closed-door-reference-bg.png"
    return _image_data_url(str(path)) if path.exists() else ""


def _write_data_url(data_url: Optional[str], output_path: str) -> None:
    if not data_url or "," not in data_url:
        raise Mp3VideoWorkflowError("前端封面生成器未返回有效图片")
    _, encoded = data_url.split(",", 1)
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_bytes(base64.b64decode(encoded))


def create_publisher_video_task(
    *,
    parent_id: str,
    user_id: Optional[str],
    audio: Dict[str, Any],
    plan: Dict[str, Any],
    image_path: str,
    role: str,
    aspect_ratio: str,
) -> PublicationTask:
    audio_segment = audio_segment_from_plan(audio, plan)
    title = plan.get("title") or "MP3 视频编排"
    subtitle_y = 84 if aspect_ratio == "9:16" else 86
    params = {
        "feature": "publisher",
        "user_id": user_id,
        "parent_feature": FEATURE_NAME,
        "parent_task_id": parent_id,
        "source_feature": FEATURE_NAME,
        "source_task_id": parent_id,
        "audio_segments": [audio_segment],
        "aspect_ratio": aspect_ratio,
        "asr_model": os.environ.get("MP3_VIDEO_ASR_MODEL", "medium"),
        "video": {
            "enabled": True,
            "image_path": image_path,
            "video_path": "",
            "media_type": "image",
            "sub_burn_enabled": True,
            "asr_enabled": True,
            "asr_model": os.environ.get("MP3_VIDEO_ASR_MODEL", "medium"),
            "subtitle_position": {"y_pct": subtitle_y},
            "subtitle_size": "normal",
            "cta": None,
        },
        "thumbnail": {},
        "bilibili": {
            "enabled": False,
            "title": title,
            "desc": "MP3 视频编排自动生成",
            "tags": ["财经", "音频", "视频"],
        },
        "youtube": {"enabled": False},
        "metadata": {
            "source": FEATURE_NAME,
            "parent_task_id": parent_id,
            "role": role,
            "title": title,
            "audio": audio,
        },
    }
    child = PublicationTask(
        status="pending",
        current_step="queued",
        progress=0,
        params=params,
        result={
            "metadata": params["metadata"],
            "source_task_id": parent_id,
            "video_path": None,
            "cover_path": None,
        },
    )
    db.session.add(child)
    db.session.commit()
    async_result = run_publication_pipeline.apply_async(args=[child.id], queue="render", retry=False)
    child.celery_task_id = async_result.id
    db.session.commit()
    return child


def audio_segment_from_plan(audio: Dict[str, Any], plan: Dict[str, Any]) -> Dict[str, Any]:
    duration = float(audio.get("duration_seconds") or 0)
    plan_audio = plan.get("audio") if isinstance(plan.get("audio"), dict) else {}
    start = max(0.0, _parse_seconds(plan_audio.get("start_seconds"), default=0.0) or 0.0)
    end = _parse_seconds(plan_audio.get("end_seconds"), default=None)
    if duration and start >= duration:
        start = 0.0
    end_offset = 0.0
    if end is not None and duration:
        end = min(duration, max(start + 0.1, end))
        end_offset = max(0.0, duration - end)
    return {
        "path": audio.get("path"),
        "subtitle": "",
        "start": round(start, 3),
        "end_offset": round(end_offset, 3),
        "speed": DEFAULT_SPEED,
    }


def draw_finance_cover(output_path: str, plan: Dict[str, Any]) -> str:
    w, h = 1920, 1080
    accent = _accent_color(plan.get("emotion"))
    img = Image.new("RGB", (w, h), "#08111f")
    draw = ImageDraw.Draw(img)
    _draw_vertical_gradient(draw, w, h, (6, 14, 27), (12, 31, 55))
    _draw_grid(draw, w, h, spacing=64, color=(255, 255, 255, 13))
    _draw_market_silhouette(draw, w, h)
    _draw_curve(draw, w, h, accent)

    title_font = _font(132, bold=True)
    sub_font = _font(58, bold=True)
    label_font = _font(34, bold=True)
    footer_font = _font(40, bold=True)
    margin = 130
    draw.rectangle([margin, 150, margin + 12, 195], fill=accent)
    draw.text((margin + 34, 151), "此刻此声 · 财经音频", font=label_font, fill=(231, 238, 248))
    next_y = _draw_wrapped_text(
        draw,
        plan.get("cover_title") or plan.get("title"),
        margin,
        330,
        1160,
        title_font,
        fill=accent,
        line_height=154,
        shadow=(0, 0, 0, 150),
        max_lines=3,
    )
    subtitle = plan.get("cover_subtitle") or plan.get("subtitle")
    if subtitle:
        _draw_wrapped_text(draw, subtitle, margin, next_y + 42, 1150, sub_font, fill=(255, 255, 255), line_height=76, max_lines=2)
    _draw_footer_mark(draw, margin, h - 122, "此刻此声", accent, footer_font)
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path, quality=95)
    return output_path


def draw_audio_background(output_path: str, plan: Dict[str, Any], infographic_path: str) -> str:
    w, h = 1080, 1920
    accent = _accent_color(plan.get("emotion"))
    img = Image.new("RGB", (w, h), "#060b16")
    draw = ImageDraw.Draw(img)
    _draw_vertical_gradient(draw, w, h, (6, 12, 26), (4, 8, 20))
    _draw_grid(draw, w, h, spacing=80, color=(255, 255, 255, 10))

    source = _open_image(infographic_path)
    if source:
        faint = ImageOps.fit(source.convert("RGB"), (w, h), method=Image.Resampling.LANCZOS).filter(ImageFilter.GaussianBlur(radius=1.5))
        overlay = Image.new("RGB", (w, h), "#060b16")
        img.paste(Image.blend(overlay, faint, 0.15))
        draw = ImageDraw.Draw(img)

    margin = 80
    max_w = w - margin * 2
    label_font = _font(36, bold=True)
    sub_font = _font(55, bold=True)
    footer_font = _font(40, bold=True)
    draw.rectangle([margin, 120, margin + 10, 160], fill=accent)
    draw.text((margin + 25, 122), "深度研报 · 研报解构", font=label_font, fill=(226, 232, 240))
    next_y = _draw_two_line_title(
        draw,
        plan.get("cover_title") or plan.get("title"),
        margin,
        260,
        max_w,
        base_size=90,
        min_size=76,
        fill=accent,
        shadow=(0, 0, 0, 120),
    )
    subtitle = plan.get("cover_subtitle") or plan.get("subtitle")
    if subtitle:
        next_y = _draw_wrapped_text(draw, subtitle, margin, next_y + 40, max_w, sub_font, fill=(255, 255, 255), line_height=75, max_lines=2)

    box_y = max(next_y + 78, 550)
    box_h = 550
    draw.rounded_rectangle([margin, box_y, margin + max_w, box_y + box_h], radius=20, fill=(255, 255, 255, 12), outline=(255, 255, 255, 32), width=4)
    if source:
        fitted = ImageOps.fit(source.convert("RGB"), (max_w, box_h), method=Image.Resampling.LANCZOS)
        mask = Image.new("L", (max_w, box_h), 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, max_w, box_h], radius=20, fill=255)
        img.paste(fitted, (margin, box_y), mask)

    _draw_footer_mark(draw, margin, 1750, "此刻此声", accent, footer_font)
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path, quality=95)
    return output_path


def draw_closed_door_cover(output_path: str, plan: Dict[str, Any], *, include_date: bool) -> str:
    w, h = 1920, 1080
    img = _closed_door_background(w, h)
    draw = ImageDraw.Draw(img)
    shade = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    shade_draw = ImageDraw.Draw(shade)
    for y in range(h):
        alpha = int(58 + 48 * (y / h))
        shade_draw.line([(0, y), (w, y)], fill=(2, 10, 22, alpha))
    img = Image.alpha_composite(img.convert("RGBA"), shade).convert("RGB")
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([8, 8, w - 8, h - 8], radius=16, outline=(255, 255, 255, 110), width=5)

    title_font = _font(184, bold=True)
    sub_font = _font(66, bold=True)
    date_font = _font(38, bold=True)
    disclaimer_font = _font(27, bold=False)
    center_x = w // 2
    y_shift = -62 if not include_date else 0
    _draw_centered_wrapped(
        draw,
        plan.get("xiaojunjun_title") or plan.get("title"),
        center_x,
        258 + y_shift,
        1520,
        title_font,
        fill=(255, 255, 255),
        line_height=190,
        max_lines=2,
        shadow=(0, 0, 0, 160),
    )

    highlight_w = 1254
    highlight_h = 270
    highlight_x = (w - highlight_w) // 2
    highlight_y = 507 + y_shift
    draw.rectangle([highlight_x, highlight_y, highlight_x + highlight_w, highlight_y + highlight_h], fill=(255, 233, 0))
    sub_lines = _wrap_text(draw, _clean_multiline(plan.get("xiaojunjun_subtitle") or plan.get("subtitle")), sub_font, highlight_w - 110, max_lines=3)
    line_h = 84 if len(sub_lines) < 3 else 78
    sub_y = highlight_y + max(36, (highlight_h - len(sub_lines) * line_h) // 2)
    for index, line in enumerate(sub_lines):
        _draw_centered_text(draw, line, center_x, sub_y + index * line_h, sub_font, fill=(7, 20, 35))

    if include_date:
        _draw_date_pill(draw, center_x, 858, plan.get("date_text") or datetime.now().strftime("%Y.%m.%d"), date_font)
    disclaimer = "本内容只供交流学习，不作为任何投资建议。理财有风险，投资需谨慎。"
    _draw_centered_text(draw, disclaimer, center_x, 982, disclaimer_font, fill=(255, 255, 255, 194))

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path, quality=95)
    return output_path


def planner_model() -> str:
    return os.environ.get("MP3_VIDEO_PLANNER_MODEL") or os.environ.get("DEEPSEEK_MODEL") or "deepseek-v4-pro"


def extract_title_candidates(text: str) -> List[str]:
    text = str(text or "")
    candidates: List[str] = []
    for line in text.replace("\r", "\n").split("\n"):
        clean = _clean_line(line).strip("#-—:： ")
        if not clean:
            continue
        if re.search(r"标题|题目|选题|封面", clean):
            clean = re.sub(r"^(?:候选|标准|短|封面)?标题(?:方案)?\s*\d*\s*[:：-]?", "", clean).strip()
            for piece in re.split(r"[。；;]", clean):
                piece = _clean_line(piece).strip("：:，,。 ")
                if 4 <= len(piece) <= 42:
                    candidates.append(piece)
        if 4 <= len(clean) <= 42 and not clean.lower().startswith(("http", "source", "参考")):
            candidates.append(clean)
    for match in re.finditer(r"[“\"《](.{4,36}?)[”\"》]", text):
        candidates.append(_clean_line(match.group(1)))
    seen = set()
    unique = []
    for item in candidates:
        item = _clean_line(item).strip("：:，,。 ")
        if not item or item in seen:
            continue
        seen.add(item)
        unique.append(item)
    return unique[:8]


def extract_subtitle(text: str) -> str:
    clean = _clean_line(text)
    pieces = [piece.strip() for piece in re.split(r"[。！？!?；;]", clean) if piece.strip()]
    for piece in pieces:
        if 8 <= len(piece) <= 55 and "标题" not in piece:
            return _clip_text(piece, 40)
    return _clip_text(clean, 36)


def _fresh_result(parent_id: str) -> Dict[str, Any]:
    fresh = PublicationTask.query.get(parent_id)
    return dict(fresh.result or {}) if fresh and isinstance(fresh.result, dict) else {}


def _clear_invalid_gemini_shell_result(parent_id: str, result: Dict[str, Any]) -> Dict[str, Any]:
    gemini = result.get("gemini") if isinstance(result.get("gemini"), dict) else {}
    candidates = [
        result.get("initial_response_text"),
        result.get("response_text"),
        result.get("final_response_text"),
        gemini.get("initial_response_text"),
        gemini.get("response_text"),
        gemini.get("final_response_text"),
    ]
    turns = result.get("turns") if isinstance(result.get("turns"), list) else gemini.get("turns")
    if isinstance(turns, list):
        candidates.extend(item.get("response_text") for item in turns if isinstance(item, dict))
    if not any(_looks_like_page_shell_text(str(item or "")) for item in candidates):
        return result

    fresh = PublicationTask.query.get(parent_id)
    if not fresh:
        return result
    current = dict(fresh.result or {})
    for key in ("gemini", "initial_response_text", "response_text", "final_response_text", "turns"):
        current.pop(key, None)
    current["invalid_gemini_response_cleared_at"] = datetime.utcnow().isoformat() + "Z"
    logs = list(current.get("logs") or [])
    logs.append({
        "step": "gemini_shell_response_cleared",
        "message": "清理误识别的 Gemini 页面壳子文本，重新等待真实回复",
        "progress": 8,
        "updated_at": datetime.utcnow().isoformat() + "Z",
    })
    current["logs"] = logs[-160:]
    fresh.result = current
    flag_modified(fresh, "result")
    db.session.commit()
    return current


def _reusable_gemini_result(result: Dict[str, Any], *, fallback_image_path: str) -> Tuple[Optional[Dict[str, Any]], str, Dict[str, Any]]:
    gemini = result.get("gemini") if isinstance(result.get("gemini"), dict) else {}
    first_response = (
        result.get("initial_response_text")
        or gemini.get("initial_response_text")
        or gemini.get("response_text")
        or ""
    )
    if not first_response and _path_ready(gemini.get("output_path")):
        loaded = _load_json_file(gemini.get("output_path"))
        if isinstance(loaded, dict):
            gemini = {**loaded, **gemini}
            first_response = gemini.get("initial_response_text") or gemini.get("response_text") or ""

    infographic = result.get("infographic") if isinstance(result.get("infographic"), dict) else {}
    gemini_image = gemini.get("image") if isinstance(gemini.get("image"), dict) else {}
    image_path = infographic.get("path") or gemini_image.get("path") or fallback_image_path
    if not first_response or not _path_ready(image_path):
        return None, "", {}

    if not infographic:
        infographic = _public_artifact(image_path, "Gemini 横向信息图")
    reusable = {
        **gemini,
        "initial_response_text": first_response,
        "image": gemini_image or infographic,
    }
    return reusable, first_response, infographic


def _reusable_plan(result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    plan = result.get("plan") if isinstance(result.get("plan"), dict) else {}
    plan_audio = plan.get("audio") if isinstance(plan.get("audio"), dict) else {}
    if _clean_line(plan.get("title")) and plan_audio and plan.get("planner_version") == PLANNER_VERSION:
        return plan
    return None


def _visual_artifacts_ready(artifacts: Any) -> bool:
    if not isinstance(artifacts, dict):
        return False
    for key in VISUAL_ARTIFACT_KEYS:
        artifact = artifacts.get(key)
        if not isinstance(artifact, dict) or not _path_ready(artifact.get("path")):
            return False
        if artifact.get("template_version") != VISUAL_ARTIFACT_VERSION:
            return False
    return True


def _reusable_video_summary(result: Dict[str, Any], label: str) -> Tuple[Optional[Dict[str, Any]], Optional[PublicationTask]]:
    summary = result.get(label) if isinstance(result.get(label), dict) else {}
    if _video_summary_ready(summary):
        return summary, None

    child = _completed_child_from_result(result, label)
    if child:
        child_summary = _publisher_result_summary(child)
        if _video_summary_ready(child_summary):
            return child_summary, child
    return None, None


def _completed_child_from_result(result: Dict[str, Any], label: str) -> Optional[PublicationTask]:
    children = result.get("children") if isinstance(result.get("children"), dict) else {}
    child_summary = children.get(label) if isinstance(children.get(label), dict) else {}
    direct_summary = result.get(label) if isinstance(result.get(label), dict) else {}
    child_id = child_summary.get("job_id") or direct_summary.get("job_id")
    if not child_id:
        return None
    child = PublicationTask.query.get(child_id)
    if child and child.status == "completed":
        return child
    return None


def _video_summary_ready(summary: Any) -> bool:
    if not isinstance(summary, dict) or not summary:
        return False
    status = str(summary.get("status") or "").lower()
    if status and status not in {"completed", "succeeded"}:
        return False
    return _path_ready(_video_output_message(summary))


def _path_ready(path: Any) -> bool:
    text = str(path or "").strip()
    resolved = _resolve_project_path(text) if text else None
    return bool(resolved and resolved.exists() and resolved.is_file() and resolved.stat().st_size > 0)


def _load_json_file(path: Any) -> Optional[Dict[str, Any]]:
    try:
        with open(str(path), "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return payload if isinstance(payload, dict) else None
    except Exception:
        return None


def _wait_child(parent_id: str, child_id: str, label: str, *, start_progress: int, end_progress: int) -> PublicationTask:
    timeout = _int_env("MP3_VIDEO_CHILD_TIMEOUT_SECONDS", 2400)
    poll_seconds = max(1, _int_env("MP3_VIDEO_CHILD_POLL_SECONDS", 3))
    deadline = time.monotonic() + timeout
    last_step = None
    while time.monotonic() < deadline:
        parent = PublicationTask.query.get(parent_id)
        if not parent or parent.status == "canceled":
            raise RuntimeError("父编排任务已取消")
        child = PublicationTask.query.get(child_id)
        if not child:
            raise RuntimeError(f"子任务不存在: {child_id}")
        if child.current_step != last_step:
            last_step = child.current_step
            child_progress = int(child.progress or 0)
            mapped = start_progress + int((end_progress - start_progress) * child_progress / 100)
            _emit(parent_id, f"{label}_{child.current_step}", mapped, f"{label}: {child.current_step}")
            _record_child(parent_id, label, child)
        if child.status in TERMINAL_STATUSES:
            return child
        celery_failure = _celery_failure_message(child)
        if celery_failure:
            _mark_child_failed(child, celery_failure)
            db.session.expire_all()
            return PublicationTask.query.get(child_id)
        time.sleep(poll_seconds)
        db.session.expire_all()
    raise RuntimeError(f"等待子任务超时: {label} {child_id}")


def _start_task(task: PublicationTask) -> None:
    result = dict(task.result or {})
    started_at = datetime.utcnow().isoformat() + "Z"
    notifications = result.get("notifications") if isinstance(result.get("notifications"), dict) else {}
    if notifications:
        history = list(result.get("notification_history") or [])
        history.append({
            "archived_at": started_at,
            "reason": "new_attempt",
            "notifications": notifications,
        })
        result["notification_history"] = history[-8:]
    result["notifications"] = {}
    result.pop("completed_at", None)
    result.update({
        "started_at": started_at,
        "error": None,
        "logs": result.get("logs") or [],
        "children": result.get("children") or {},
    })
    task.status = "processing"
    task.current_step = "start"
    task.progress = 2
    task.error_message = None
    task.completed_at = None
    task.result = result
    flag_modified(task, "result")
    db.session.commit()


def _emit(parent_id: str, step: str, progress: int, message: str, *, result: Optional[Dict[str, Any]] = None) -> None:
    fresh = PublicationTask.query.get(parent_id)
    if not fresh:
        return
    current = dict(fresh.result or {})
    logs = list(current.get("logs") or [])
    logs.append({
        "step": step,
        "message": message,
        "progress": progress,
        "updated_at": datetime.utcnow().isoformat() + "Z",
    })
    current["logs"] = logs[-160:]
    if result:
        current.update(result)
    fresh.current_step = _safe_step_code(step)
    fresh.progress = max(0, min(100, int(progress or fresh.progress or 0)))
    fresh.result = current
    flag_modified(fresh, "result")
    db.session.commit()


def _update_result(parent_id: str, values: Dict[str, Any]) -> None:
    fresh = PublicationTask.query.get(parent_id)
    result = dict(fresh.result or {})
    result.update(values)
    fresh.result = result
    flag_modified(fresh, "result")
    db.session.commit()


def _record_child(parent_id: str, label: str, child: PublicationTask) -> None:
    fresh = PublicationTask.query.get(parent_id)
    result = dict(fresh.result or {})
    children = dict(result.get("children") or {})
    children[label] = _task_public_summary(child)
    result["children"] = children
    fresh.result = result
    flag_modified(fresh, "result")
    db.session.commit()


def _mark_failed(task: PublicationTask, message: str) -> None:
    result = dict(task.result or {})
    result["error"] = message
    result["completed_at"] = datetime.utcnow().isoformat() + "Z"
    task.status = "failed"
    task.current_step = "failed"
    task.progress = task.progress or 0
    task.error_message = message
    task.completed_at = datetime.utcnow()
    task.result = result
    flag_modified(task, "result")
    db.session.commit()


def mark_job_failed_safely(task_id: str, message: str, *, notify: bool = True) -> bool:
    try:
        db.session.rollback()
    except Exception:
        pass

    persisted = False
    persist_error = None
    try:
        fresh = PublicationTask.query.get(task_id)
        if not fresh or fresh.status == "canceled":
            if notify and not fresh:
                _send_direct_mp3_workflow_failure_email(
                    task_id=task_id,
                    message=message,
                    persisted=False,
                    notification_queued=False,
                    persist_error=f"任务不存在: {task_id}",
                )
            return False
        _mark_failed(fresh, message)
        persisted = True
    except Exception as exc:
        persist_error = str(exc)
        try:
            db.session.rollback()
        except Exception:
            pass
        logger.exception("[MP3_VIDEO_WORKFLOW] failed to persist failure state | task=%s", task_id)
        if notify:
            _send_direct_mp3_workflow_failure_email(
                task_id=task_id,
                message=message,
                persisted=False,
                notification_queued=False,
                persist_error=persist_error,
            )
        return False

    notification_queued = False
    if notify:
        notification_queued = _queue_notification(task_id, "mp3_video_failed", message)
        if not notification_queued:
            _send_direct_mp3_workflow_failure_email(
                task_id=task_id,
                message=message,
                persisted=persisted,
                notification_queued=notification_queued,
                persist_error=persist_error,
            )
    return True


def mark_job_failed_if_unfinished(task_id: str, message: str, *, notify: bool = True) -> bool:
    try:
        db.session.rollback()
    except Exception:
        pass
    try:
        fresh = PublicationTask.query.get(task_id)
        if not fresh or fresh.status in TERMINAL_STATUSES:
            return False
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass
        logger.exception("[MP3_VIDEO_WORKFLOW] failed to inspect unfinished task | task=%s", task_id)
        return False
    return mark_job_failed_safely(task_id, message, notify=notify)


def _mark_child_failed(child: PublicationTask, message: str) -> None:
    result = dict(child.result or {})
    result["error"] = message
    result["completed_at"] = datetime.utcnow().isoformat() + "Z"
    child.status = "failed"
    child.current_step = "failed"
    child.error_message = message
    child.completed_at = datetime.utcnow()
    child.result = result
    flag_modified(child, "result")
    db.session.commit()


def _queue_notification(task_id: str, event_type: str, message: Optional[str] = None) -> bool:
    try:
        fresh = PublicationTask.query.get(task_id)
        if not fresh:
            return False
        result = dict(fresh.result or {})
        notifications = dict(result.get("notifications") or {})
        if notifications.get(event_type, {}).get("queued"):
            return True

        payload = _notification_payload(fresh, event_type, message)
        queued = workflow_notification_service.enqueue_workflow_event(event_type, payload)
        if not queued.get("queued"):
            return False

        notifications[event_type] = {
            "queued": True,
            "event_type": event_type,
            "celery_task_id": queued.get("celery_task_id"),
            "queued_at": datetime.utcnow().isoformat() + "Z",
        }
        result["notifications"] = notifications
        fresh.result = result
        flag_modified(fresh, "result")
        db.session.commit()
        return True
    except Exception:
        db.session.rollback()
        logger.exception("[MP3_VIDEO_WORKFLOW] notification queue failed | task=%s event=%s", task_id, event_type)
        return False


def _send_direct_mp3_workflow_failure_email(
    *,
    task_id: str,
    message: str,
    persisted: bool = False,
    notification_queued: bool = False,
    persist_error: Optional[str] = None,
) -> None:
    try:
        subject = f"[MP3 视频编排] 任务异常 · {str(task_id)[:8]}"
        lines = [
            "MP3 视频编排任务异常退出，已触发兜底通知。",
            "",
            f"任务 ID：{task_id}",
            f"失败已写入数据库：{'是' if persisted else '否'}",
            f"失败通知已入队：{'是' if notification_queued else '否'}",
            f"异常：{message}",
        ]
        if persist_error:
            lines.append(f"落库/通知异常：{persist_error}")
        lines.extend([
            "",
            f"发送时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        ])
        workflow_notification_service.send_email_message(subject, "\n".join(lines))
    except Exception:
        logger.exception("[MP3_VIDEO_WORKFLOW] direct failure email failed | task=%s", task_id)


def _notification_payload(task: PublicationTask, event_type: str, message: Optional[str] = None) -> Dict[str, Any]:
    result = task.result if isinstance(task.result, dict) else {}
    params = task.params if isinstance(task.params, dict) else {}
    workflow = params.get(FEATURE_NAME) if isinstance(params.get(FEATURE_NAME), dict) else {}
    metadata = workflow.get("metadata") if isinstance(workflow.get("metadata"), dict) else {}
    audio = result.get("audio") or workflow.get("audio") or {}
    plan = result.get("plan") or {}
    artifacts = result.get("artifacts") if isinstance(result.get("artifacts"), dict) else {}
    children = result.get("children") if isinstance(result.get("children"), dict) else {}
    gemini = result.get("gemini") if isinstance(result.get("gemini"), dict) else {}
    infographic = result.get("infographic") if isinstance(result.get("infographic"), dict) else {}
    landscape = result.get("landscape_video") or children.get("landscape_video") or {}
    vertical = result.get("vertical_video") or children.get("vertical_video") or {}

    return {
        "id": task.id,
        "batch_id": task.id,
        "group_id": FEATURE_NAME,
        "group_name": "MP3 视频编排",
        "group_label": "编排",
        "subject_prefix": "[MP3 视频编排]",
        "trigger": metadata.get("source") or "manual",
        "status": _notification_status(task.status),
        "current_stage": task.current_step,
        "event_type": event_type,
        "event_message": message,
        "error_message": task.error_message or result.get("error"),
        "created_at": task.created_at.isoformat() + "Z" if task.created_at else None,
        "started_at": result.get("started_at"),
        "completed_at": task.completed_at.isoformat() + "Z" if task.completed_at else result.get("completed_at"),
        "stage_results": {
            "stages": _notification_stages(task, audio, gemini, infographic, plan, artifacts, landscape, vertical),
            "outputs": _notification_outputs(result, audio, infographic, plan, artifacts, landscape, vertical),
        },
    }


def _notification_stages(
    task: PublicationTask,
    audio: Dict[str, Any],
    gemini: Dict[str, Any],
    infographic: Dict[str, Any],
    plan: Dict[str, Any],
    artifacts: Dict[str, Any],
    landscape: Dict[str, Any],
    vertical: Dict[str, Any],
) -> List[Dict[str, Any]]:
    return [
        {
            "name": "MP3 输入",
            "status": "completed" if audio else "pending",
            "message": _audio_message(audio),
        },
        {
            "name": "Gemini Gem 解析/信息图",
            "status": _stage_status(task, bool(gemini or infographic), "gemini"),
            "message": _gemini_message(gemini, infographic),
        },
        {
            "name": "DS v4 pro 标题与时间戳规划",
            "status": _stage_status(task, bool(plan), "deepseek_plan"),
            "message": _plan_message(plan),
        },
        {
            "name": "封皮/背景图生成",
            "status": _stage_status(task, _visual_artifacts_ready(artifacts), "generate_visual_assets"),
            "message": _artifacts_message(artifacts),
        },
        {
            "name": "小君君横屏视频合成",
            "status": _child_stage_status(landscape),
            "message": _video_message(landscape),
        },
        {
            "name": "此刻此声竖屏视频合成",
            "status": _child_stage_status(vertical),
            "message": _video_message(vertical),
        },
    ]


def _notification_outputs(
    result: Dict[str, Any],
    audio: Dict[str, Any],
    infographic: Dict[str, Any],
    plan: Dict[str, Any],
    artifacts: Dict[str, Any],
    landscape: Dict[str, Any],
    vertical: Dict[str, Any],
) -> Dict[str, Any]:
    outputs = {
        "音频": _audio_message(audio),
        "标题": plan.get("title") if isinstance(plan, dict) else None,
        "音频时间戳": _plan_audio_message(plan),
        "Gemini 信息图": _artifact_message(infographic),
        "封皮/背景图": _artifacts_message(artifacts),
        "横屏视频": _video_output_message(landscape),
        "竖屏视频": _video_output_message(vertical),
        "结果 JSON": result.get("output_path"),
    }
    return {key: value for key, value in outputs.items() if value}


def _notification_status(status: Any) -> str:
    mapping = {
        "pending": "queued",
        "processing": "running",
        "completed": "completed",
        "failed": "failed",
        "canceled": "canceled",
    }
    return mapping.get(status or "", status or "-")


def _stage_status(task: PublicationTask, completed: bool, active_step: str) -> str:
    if completed:
        return "completed"
    step = str(task.current_step or "")
    if task.status in {"pending", "processing"} and active_step in step:
        return "running"
    if task.status == "failed" and active_step in step:
        return "failed"
    return "pending"


def _child_stage_status(child: Any) -> str:
    if not isinstance(child, dict) or not child:
        return "pending"
    status = str(child.get("status") or "").lower()
    if status in {"completed", "succeeded"}:
        return "completed"
    if status in {"failed", "canceled"}:
        return status
    if status in {"pending", "queued"}:
        return "queued"
    return "running"


def _audio_message(audio: Any) -> Optional[str]:
    if not isinstance(audio, dict) or not audio:
        return None
    parts = [audio.get("filename") or audio.get("name") or "MP3"]
    size = _format_bytes(audio.get("bytes"))
    if size:
        parts.append(size)
    duration = _format_seconds(audio.get("duration_seconds"))
    if duration:
        parts.append(duration)
    return " / ".join(str(item) for item in parts if item)


def _gemini_message(gemini: Any, infographic: Any) -> Optional[str]:
    if not isinstance(gemini, dict) and not isinstance(infographic, dict):
        return None
    parts = []
    if isinstance(gemini, dict):
        if gemini.get("initial_response_text") or gemini.get("response_text"):
            parts.append("第一轮返回已获取")
        if gemini.get("target_url"):
            parts.append(gemini.get("target_url"))
    image_text = _artifact_message(infographic)
    if image_text:
        parts.append(f"信息图 {image_text}")
    return " / ".join(parts) if parts else None


def _plan_message(plan: Any) -> Optional[str]:
    if not isinstance(plan, dict) or not plan:
        return None
    title = plan.get("title") or "已规划"
    audio = _plan_audio_message(plan)
    return f"{title} / {audio}" if audio else title


def _plan_audio_message(plan: Any) -> Optional[str]:
    if not isinstance(plan, dict):
        return None
    audio = plan.get("audio") if isinstance(plan.get("audio"), dict) else {}
    if not audio:
        return None
    start = audio.get("start_seconds")
    end = audio.get("end_seconds")
    speed = audio.get("speed")
    end_text = "自动" if end is None else f"{end}s"
    return f"{start or 0}s - {end_text} / {speed or DEFAULT_SPEED}x"


def _artifacts_message(artifacts: Any) -> Optional[str]:
    if not isinstance(artifacts, dict) or not artifacts:
        return None
    labels = []
    for value in artifacts.values():
        if isinstance(value, dict):
            labels.append(value.get("label") or value.get("path"))
    labels = [item for item in labels if item]
    return f"{len(artifacts)} 张：{'、'.join(labels[:4])}" if labels else f"{len(artifacts)} 张"


def _artifact_message(artifact: Any) -> Optional[str]:
    if not isinstance(artifact, dict) or not artifact:
        return None
    path = artifact.get("path")
    size = _format_bytes(artifact.get("bytes"))
    label = artifact.get("label")
    return " / ".join(str(item) for item in [label, size, path] if item)


def _video_message(video: Any) -> Optional[str]:
    if not isinstance(video, dict) or not video:
        return None
    parts = []
    status = video.get("status")
    if status:
        parts.append(str(status))
    if video.get("step"):
        parts.append(str(video.get("step")))
    if video.get("progress") is not None:
        parts.append(f"{video.get('progress')}%")
    output = _video_output_message(video)
    if output:
        parts.append(output)
    if video.get("error"):
        parts.append(str(video.get("error")))
    return " / ".join(parts) if parts else None


def _video_output_message(video: Any) -> Optional[str]:
    if not isinstance(video, dict) or not video:
        return None
    return video.get("video_path") or video.get("landscape_video_path") or video.get("vertical_video_path")


def _format_bytes(value: Any) -> Optional[str]:
    try:
        size = float(value or 0)
    except (TypeError, ValueError):
        return None
    if size <= 0:
        return None
    units = ["B", "KB", "MB", "GB"]
    index = 0
    while size >= 1024 and index < len(units) - 1:
        size /= 1024
        index += 1
    digits = 0 if index == 0 else 1
    return f"{size:.{digits}f} {units[index]}"


def _format_seconds(value: Any) -> Optional[str]:
    try:
        seconds = float(value or 0)
    except (TypeError, ValueError):
        return None
    if seconds <= 0:
        return None
    total = int(round(seconds))
    minutes = total // 60
    remain = total % 60
    return f"{minutes:02d}:{remain:02d}"


def _celery_failure_message(child: PublicationTask) -> Optional[str]:
    if not child.celery_task_id:
        return None
    try:
        async_result = celery_app.AsyncResult(child.celery_task_id)
        state = async_result.state
    except Exception:
        return None
    if state not in CELERY_FAILURE_STATES:
        return None
    detail = ""
    try:
        detail = str(async_result.result) if async_result.result else ""
    except Exception as exc:
        detail = str(exc)
    return f"Celery 子任务异常退出: {state}: {detail}" if detail else f"Celery 子任务异常退出: {state}"


def _task_public_summary(task: Optional[PublicationTask]) -> Optional[Dict[str, Any]]:
    if not task:
        return None
    return {
        "job_id": task.id,
        "celery_id": task.celery_task_id,
        "status": task.status,
        "step": task.current_step,
        "progress": task.progress or 0,
        "feature": task.feature or (task.params or {}).get("feature"),
        "error": task.error_message or (task.result or {}).get("error"),
        "created_at": task.created_at.isoformat() + "Z" if task.created_at else None,
        "completed_at": task.completed_at.isoformat() + "Z" if task.completed_at else None,
    }


def _publisher_result_summary(task: PublicationTask) -> Dict[str, Any]:
    result = task.result or {}
    return {
        **(_task_public_summary(task) or {}),
        "video_path": result.get("video_path"),
        "landscape_video_path": result.get("video_path") if (task.params or {}).get("aspect_ratio") != "9:16" else None,
        "vertical_video_path": result.get("vertical_video_path") or (result.get("video_path") if (task.params or {}).get("aspect_ratio") == "9:16" else None),
        "cover_path": result.get("cover_path"),
        "bilibili_result": result.get("bilibili_result"),
        "youtube_result": result.get("youtube_result"),
    }


def _public_gemini_result(result: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "initial_response_text": result.get("initial_response_text"),
        "response_text": result.get("response_text"),
        "final_response_text": result.get("final_response_text"),
        "turns": result.get("turns") or [],
        "file": result.get("file"),
        "image": _public_artifact(result.get("image"), "Gemini 横向信息图") if result.get("image") else None,
        "target_url": result.get("target_url"),
        "page_url": result.get("page_url"),
        "output_path": result.get("output_path"),
        "completed_at": result.get("completed_at"),
    }


def _public_artifact(value: Any, label: str = "") -> Dict[str, Any]:
    if isinstance(value, dict):
        path = value.get("path")
        payload = dict(value)
    else:
        path = str(value or "")
        payload = {"path": path}
    if path and os.path.exists(path):
        payload["bytes"] = os.path.getsize(path)
    payload["label"] = label or payload.get("label")
    return payload


def _artifact_meta(path: str, label: str, *, renderer: Optional[str] = None) -> Dict[str, Any]:
    return {
        "path": path,
        "label": label,
        "template_version": VISUAL_ARTIFACT_VERSION,
        "renderer": renderer,
        "bytes": os.path.getsize(path) if path and os.path.exists(path) else None,
    }


def _audio_asset_filter():
    ext_conditions = [ZsxqAttachment.file_ext == ext for ext in sorted(AUDIO_EXTENSIONS)]
    path_conditions = []
    for ext in sorted(AUDIO_EXTENSIONS):
        path_conditions.append(ZsxqAttachment.name.ilike(f"%.{ext}"))
        path_conditions.append(ZsxqAttachment.local_path.ilike(f"%.{ext}"))
    return or_(*(ext_conditions + path_conditions))


def _audio_asset_row_payload(row) -> Optional[Dict[str, Any]]:
    if not row:
        return None
    path = _resolve_project_path(row.local_path)
    if not path or not path.exists() or not path.is_file():
        return None
    stat = path.stat()
    filename = row.attachment_name or path.name
    return {
        "attachment_id": row.attachment_id,
        "filename": filename,
        "name": filename,
        "path": str(path),
        "bytes": stat.st_size,
        "file_ext": (row.file_ext or path.suffix.lstrip(".")).lower(),
        "download_count": row.download_count,
        "updated_at": row.attachment_updated_at.isoformat() + "Z" if row.attachment_updated_at else None,
        "created_at": row.attachment_created_at.isoformat() + "Z" if row.attachment_created_at else None,
        "group_id": row.group_id,
        "tab_id": row.tab_id,
        "topic_id": row.topic_id,
        "topic_title": row.topic_title,
        "topic_content_excerpt": _clip_text(_clean_line(row.topic_content), 180),
        "topic_create_time": row.topic_create_time,
    }


def _resolve_project_path(path_value: Any) -> Optional[Path]:
    text = str(path_value or "").strip()
    if not text:
        return None
    path = Path(text).expanduser()
    if path.is_absolute():
        return path
    return (project_root() / path).resolve()


def _font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    candidates = [
        os.environ.get("MP3_VIDEO_FONT_PATH"),
        os.environ.get("REPORT_THUMBNAIL_FONT_PATH"),
        str(project_root() / "assets" / "fonts" / ("SourceHanSansSC-Heavy.otf" if bold else "SourceHanSansSC-Regular.otf")),
        str(project_root() / "assets" / "fonts" / "SourceHanSansSC-Bold.otf"),
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/System/Library/Fonts/Supplemental/Songti.ttc",
        "/System/Library/Fonts/PingFang.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "C:\\Windows\\Fonts\\msyhbd.ttc",
        "C:\\Windows\\Fonts\\msyh.ttc",
    ]
    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default()


def _accent_color(emotion: Any) -> Tuple[int, int, int]:
    return {
        "pro": (191, 161, 95),
        "macro": (250, 204, 21),
        "bull": (74, 222, 128),
        "bear": (239, 68, 68),
    }.get(str(emotion or "bull"), (74, 222, 128))


def _draw_vertical_gradient(draw: ImageDraw.ImageDraw, w: int, h: int, start: Tuple[int, int, int], end: Tuple[int, int, int]) -> None:
    for y in range(h):
        ratio = y / max(1, h - 1)
        color = tuple(int(start[i] + (end[i] - start[i]) * ratio) for i in range(3))
        draw.line([(0, y), (w, y)], fill=color)


def _draw_grid(draw: ImageDraw.ImageDraw, w: int, h: int, *, spacing: int, color: Tuple[int, int, int, int]) -> None:
    for x in range(0, w, spacing):
        draw.line([(x, 0), (x, h)], fill=color, width=1)
    for y in range(0, h, spacing):
        draw.line([(0, y), (w, y)], fill=color, width=1)


def _draw_market_silhouette(draw: ImageDraw.ImageDraw, w: int, h: int) -> None:
    base = h
    x = int(w * 0.55)
    widths = [62, 78, 58, 112, 72, 96, 72, 120, 84]
    heights = [250, 420, 320, 560, 380, 650, 455, 710, 500]
    for width, height in zip(widths, heights):
        draw.rectangle([x, base - height, x + width, base], fill=(255, 255, 255, 12))
        draw.rectangle([x, base - height, x + 7, base], fill=(255, 255, 255, 24))
        x += width + 22


def _draw_curve(draw: ImageDraw.ImageDraw, w: int, h: int, accent: Tuple[int, int, int]) -> None:
    points = [
        (int(w * 0.45), h - 20),
        (int(w * 0.55), int(h * 0.74)),
        (int(w * 0.67), int(h * 0.62)),
        (int(w * 0.78), int(h * 0.38)),
        (int(w * 0.88), int(h * 0.29)),
        (w, int(h * 0.12)),
    ]
    for offset, alpha in [(16, 42), (8, 72), (0, 180)]:
        shifted = [(x, y + offset) for x, y in points]
        draw.line(shifted, fill=accent + (alpha,), width=8 if offset else 5, joint="curve")


def _draw_footer_mark(draw: ImageDraw.ImageDraw, x: int, y: int, text: str, accent: Tuple[int, int, int], font: ImageFont.ImageFont) -> None:
    bars = [15, 30, 20, 35, 10]
    for index, height in enumerate(bars):
        draw.rectangle([x + index * 12, y - height, x + index * 12 + 6, y], fill=accent)
    draw.text((x + 72, y - 34), text, font=font, fill=(148, 163, 184))


def _draw_wave_hint(draw: ImageDraw.ImageDraw, center_x: int, y: int, accent: Tuple[int, int, int]) -> None:
    widths = [8, 8, 8, 8, 8, 8, 8]
    heights = [24, 42, 58, 36, 68, 44, 28]
    total_w = len(widths) * 18
    x = center_x - total_w // 2
    for index, height in enumerate(heights):
        draw.rounded_rectangle([x + index * 18, y - height // 2, x + index * 18 + 8, y + height // 2], radius=4, fill=accent)


def _draw_two_line_title(
    draw: ImageDraw.ImageDraw,
    text: Any,
    x: int,
    y: int,
    max_width: int,
    *,
    base_size: int,
    min_size: int,
    fill: Tuple[int, int, int],
    shadow: Optional[Tuple[int, int, int, int]] = None,
) -> int:
    raw = _clean_multiline(text)
    clean = re.sub(r"\s+", "", raw) if re.search(r"[\u4e00-\u9fff]", raw) else _clean_line(raw.replace("\n", " "))
    clean = clean or _clean_line(text)
    for size in range(base_size, min_size - 1, -4):
        font = _font(size, bold=True)
        lines = _best_two_line_title(draw, clean, font, max_width)
        if lines and all(draw.textlength(line, font=font) <= max_width for line in lines):
            return _draw_title_lines(draw, lines, x, y, font, fill=fill, line_height=int(size * 1.22), shadow=shadow)

    font = _font(min_size, bold=True)
    lines = _best_two_line_title(draw, clean, font, max_width) or _wrap_text(draw, clean, font, max_width, max_lines=2)
    lines = [_trim_text_to_width(draw, line, font, max_width) for line in lines[:2]]
    return _draw_title_lines(draw, lines, x, y, font, fill=fill, line_height=int(min_size * 1.22), shadow=shadow)


def _best_two_line_title(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> List[str]:
    clean = _clean_line(text).strip("，,。；;：:、 ")
    if not clean:
        return [""]
    if draw.textlength(clean, font=font) <= max_width:
        return [clean]
    if len(clean) <= 2:
        return [_trim_text_to_width(draw, clean, font, max_width)]

    best_lines: Optional[List[str]] = None
    best_score: Optional[float] = None
    midpoint = len(clean) / 2
    for index in range(1, len(clean)):
        left = clean[:index].strip("，,。；;：:、 ")
        right = clean[index:].strip("，,。；;：:、 ")
        if not left or not right:
            continue
        left_w = draw.textlength(left, font=font)
        right_w = draw.textlength(right, font=font)
        if left_w > max_width or right_w > max_width:
            continue
        punctuation_bonus = -180 if clean[index - 1] in "，,。；;：:、" else 0
        score = abs(left_w - right_w) + abs(index - midpoint) * 18 + punctuation_bonus + _title_split_penalty(clean, index)
        if best_score is None or score < best_score:
            best_score = score
            best_lines = [left, right]
    if best_lines:
        return best_lines
    return _wrap_text(draw, clean, font, max_width, max_lines=2)


def _title_split_penalty(text: str, index: int) -> int:
    protected_terms = (
        "半导体",
        "人工智能",
        "碳化硅",
        "先进材料",
        "电子级",
        "新能源",
        "机器人",
        "数据中心",
    )
    for term in protected_terms:
        start = text.find(term)
        while start >= 0:
            if start < index < start + len(term):
                return 500
            start = text.find(term, start + 1)
    return 0


def _draw_title_lines(
    draw: ImageDraw.ImageDraw,
    lines: List[str],
    x: int,
    y: int,
    font: ImageFont.ImageFont,
    *,
    fill: Tuple[int, int, int],
    line_height: int,
    shadow: Optional[Tuple[int, int, int, int]] = None,
) -> int:
    for index, line in enumerate(lines):
        yy = y + index * line_height
        if shadow:
            draw.text((x + 4, yy + 5), line, font=font, fill=shadow)
        draw.text((x, yy), line, font=font, fill=fill)
    return y + len(lines) * line_height


def _trim_text_to_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> str:
    clean = _clean_line(text)
    if draw.textlength(clean, font=font) <= max_width:
        return clean
    suffix = "..."
    while clean and draw.textlength(clean + suffix, font=font) > max_width:
        clean = clean[:-1]
    return clean + suffix if clean else suffix


def _closed_door_background(w: int, h: int) -> Image.Image:
    ref_path = project_root() / "src" / "frontend" / "assets" / "cover" / "default" / "closed-door-reference-bg.png"
    if ref_path.exists():
        return ImageOps.fit(Image.open(ref_path).convert("RGB"), (w, h), method=Image.Resampling.LANCZOS)
    img = Image.new("RGB", (w, h), "#06182c")
    draw = ImageDraw.Draw(img)
    _draw_vertical_gradient(draw, w, h, (6, 24, 44), (2, 9, 21))
    _draw_grid(draw, w, h, spacing=78, color=(121, 169, 206, 22))
    for i in range(8):
        x = 80 + i * 210
        y = 120 + (i % 3) * 72
        draw.rounded_rectangle([x, y, x + 520, y + 190], radius=16, fill=(255, 255, 255, 16), outline=(255, 255, 255, 22), width=2)
    return img


def _open_image(path: str) -> Optional[Image.Image]:
    try:
        if path and os.path.exists(path):
            return Image.open(path)
    except Exception:
        return None
    return None


def _draw_wrapped_text(
    draw: ImageDraw.ImageDraw,
    text: Any,
    x: int,
    y: int,
    max_width: int,
    font: ImageFont.ImageFont,
    *,
    fill: Tuple[int, int, int],
    line_height: int,
    max_lines: int = 4,
    shadow: Optional[Tuple[int, int, int, int]] = None,
) -> int:
    lines = _wrap_text(draw, text, font, max_width, max_lines=max_lines)
    for index, line in enumerate(lines):
        yy = y + index * line_height
        if shadow:
            draw.text((x + 4, yy + 5), line, font=font, fill=shadow)
        draw.text((x, yy), line, font=font, fill=fill)
    return y + len(lines) * line_height


def _draw_centered_wrapped(
    draw: ImageDraw.ImageDraw,
    text: Any,
    center_x: int,
    y: int,
    max_width: int,
    font: ImageFont.ImageFont,
    *,
    fill: Tuple[int, int, int],
    line_height: int,
    max_lines: int = 2,
    shadow: Optional[Tuple[int, int, int, int]] = None,
) -> int:
    lines = _wrap_text(draw, text, font, max_width, max_lines=max_lines)
    for index, line in enumerate(lines):
        yy = y + index * line_height
        if shadow:
            _draw_centered_text(draw, line, center_x + 4, yy + 5, font, fill=shadow)
        _draw_centered_text(draw, line, center_x, yy, font, fill=fill)
    return y + len(lines) * line_height


def _draw_centered_text(draw: ImageDraw.ImageDraw, text: str, center_x: int, y: int, font: ImageFont.ImageFont, *, fill: Any) -> None:
    bbox = draw.textbbox((0, 0), text, font=font)
    draw.text((center_x - (bbox[2] - bbox[0]) // 2, y), text, font=font, fill=fill)


def _draw_date_pill(draw: ImageDraw.ImageDraw, center_x: int, y: int, text: str, font: ImageFont.ImageFont) -> None:
    text = _clean_line(text)
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    pill_w = text_w + 96
    pill_h = 74
    x = center_x - pill_w // 2
    draw.rounded_rectangle([x, y, x + pill_w, y + pill_h], radius=37, fill=(15, 23, 42, 210), outline=(255, 255, 255, 80), width=2)
    draw.text((x + 48, y + 14), text, font=font, fill=(255, 255, 255))


def _wrap_text(draw: ImageDraw.ImageDraw, text: Any, font: ImageFont.ImageFont, max_width: int, *, max_lines: int = 4) -> List[str]:
    raw_lines = str(text or "").replace("\r", "\n").split("\n")
    lines: List[str] = []
    for raw in raw_lines:
        current = ""
        for ch in raw.strip():
            candidate = current + ch
            if draw.textlength(candidate, font=font) > max_width and current:
                lines.append(current)
                current = ch
                if len(lines) >= max_lines:
                    return lines[:max_lines]
            else:
                current = candidate
        if current:
            lines.append(current)
        if len(lines) >= max_lines:
            break
    return lines[:max_lines] or [""]


def _cover_lines(text: Any, *, max_chars: int, max_lines: int = 2) -> str:
    lines = [_clean_line(line) for line in str(text or "").replace("\r", "\n").split("\n") if _clean_line(line)]
    if len(lines) >= max_lines:
        return "\n".join(line[:max_chars] for line in lines[:max_lines])
    single = _clean_line(text)
    if len(single) <= max_chars:
        return single
    output = []
    cursor = 0
    while cursor < len(single) and len(output) < max_lines:
        piece = single[cursor:cursor + max_chars].strip("，,、。.;；:：!！?？ ")
        if piece:
            output.append(piece)
        cursor += max_chars
    return "\n".join(output)


def _parse_seconds(value: Any, default: Optional[float] = None) -> Optional[float]:
    if value in (None, ""):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return default
    if ":" in text:
        parts = [float(part or 0) for part in text.split(":")]
        seconds = 0.0
        for part in parts:
            seconds = seconds * 60 + part
        return seconds
    try:
        return float(text)
    except (TypeError, ValueError):
        return default


def _json_from_text(value: str) -> Dict[str, Any]:
    text = (value or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()
    match = re.search(r"\{.*\}", text, flags=re.S)
    if match:
        text = match.group(0)
    return json.loads(text)


def _clean_line(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _clean_multiline(value: Any) -> str:
    lines = [_clean_line(line) for line in str(value or "").replace("\r", "\n").split("\n")]
    return "\n".join(line for line in lines if line)


def _clip_text(value: Any, max_length: int) -> str:
    text = _clean_line(value)
    return text[:max_length] if max_length and len(text) > max_length else text


def _compact_text(value: Any, max_chars: int) -> str:
    text = str(value or "").strip()
    if len(text) <= max_chars:
        return text
    head = max_chars // 2
    tail = max_chars - head
    return f"{text[:head]}\n\n...[中间省略 {len(text) - max_chars} 字]...\n\n{text[-tail:]}"


def _safe_step_code(value: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9_]+", "_", str(value or "running")).strip("_") or "running"
    return text[:80]


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _env(*names: str) -> str:
    default = names[-1] if names else ""
    for name in names[:-1]:
        value = os.environ.get(name)
        if value:
            return value
    return default
