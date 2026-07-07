import json
import os
import re
from datetime import datetime
from pathlib import Path

from flask import Blueprint, jsonify, request, send_file
from sqlalchemy import String, and_, cast, or_
from sqlalchemy.orm import load_only
from sqlalchemy.orm.attributes import flag_modified

from celery_config import celery_app
from models import db
from models.report_video import (
    ReportVideoAnalysis,
    ReportVideoMindmap,
    ReportVideoReport,
    ReportVideoSegment,
    ReportVideoTask,
)
from models.task import PublicationTask
from models.zsxq_sync import ZsxqAttachment, ZsxqTopic
from services import gemini_browser
from services.report_top10_workflow import (
    DEFAULT_PROGRAM_NAME,
    FEATURE_NAME,
    QUEUE_NAME,
    ReportTop10WorkflowError,
    cached_single_report_for_pdf,
    default_editor_gemini_url,
    pdf_meta_from_zsxq_attachment,
    resolve_pdf_path,
    selected_report_count,
    status_payload as workflow_status_payload,
    storage_path_for_job,
)
from tasks.report_top10_workflow import process_report_top10_workflow_job
from utils.auth_utils import require_login
from services.report_video_service import rel_path, task_dir


report_top10_workflow_bp = Blueprint("report_top10_workflow", __name__, url_prefix="/api/report-top10-workflows")

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SYSTEM_USER_ID = "system"
STATUS_MAP = {
    "pending": "queued",
    "processing": "running",
    "completed": "succeeded",
    "failed": "failed",
    "canceled": "canceled",
}
TERMINAL_STATUSES = {"completed", "failed", "canceled"}

TASK_LIST_COLUMNS = (
    PublicationTask.id,
    PublicationTask.celery_task_id,
    PublicationTask.status,
    PublicationTask.current_step,
    PublicationTask.progress,
    PublicationTask.error_message,
    PublicationTask.feature,
    PublicationTask.user_id,
    PublicationTask.task_title,
    PublicationTask.created_at,
    PublicationTask.updated_at,
    PublicationTask.completed_at,
)
TASK_REQUEST_COLUMNS = (
    *TASK_LIST_COLUMNS,
    PublicationTask.params,
)
TASK_DETAIL_COLUMNS = (
    *TASK_REQUEST_COLUMNS,
    PublicationTask.result,
)


@report_top10_workflow_bp.route("/status", methods=["GET"])
@require_login
def service_status(_current_user):
    return jsonify({
        "success": True,
        "data": {
            "provider": {
                "id": FEATURE_NAME,
                "name": "今日研报严选",
                **workflow_status_payload(),
            },
            "report_pdf_gemini": gemini_browser.status_payload(),
        },
    })


@report_top10_workflow_bp.route("/pdf-assets", methods=["GET"])
@require_login
def list_pdf_assets(_current_user):
    page = _bounded_int(request.args.get("page"), 1, 1, 100000)
    per_page = _bounded_int(request.args.get("per_page"), 10, 1, 100)
    q = _normalize_search_query(request.args.get("q"))
    scope = str(request.args.get("scope") or "all").strip().lower()
    hot_download_threshold = 30

    query = (
        db.session.query(
            ZsxqAttachment.id.label("attachment_id"),
            ZsxqAttachment.group_id.label("group_id"),
            ZsxqAttachment.tab_id.label("tab_id"),
            ZsxqAttachment.topic_id.label("topic_id"),
            ZsxqAttachment.name.label("attachment_name"),
            ZsxqAttachment.file_id.label("file_id"),
            ZsxqAttachment.download_count.label("download_count"),
            ZsxqAttachment.local_path.label("local_path"),
            ZsxqAttachment.created_at.label("attachment_created_at"),
            ZsxqAttachment.updated_at.label("attachment_updated_at"),
            ZsxqTopic.title.label("topic_title"),
            ZsxqTopic.tab_label.label("tab_label"),
            ZsxqTopic.author_name.label("topic_author"),
            ZsxqTopic.create_time.label("topic_create_time"),
            ZsxqTopic.first_synced_at.label("topic_first_synced_at"),
            ZsxqTopic.last_synced_at.label("topic_last_synced_at"),
        )
        .outerjoin(
            ZsxqTopic,
            and_(
                ZsxqTopic.group_id == ZsxqAttachment.group_id,
                ZsxqTopic.tab_id == ZsxqAttachment.tab_id,
                ZsxqTopic.topic_id == ZsxqAttachment.topic_id,
            ),
        )
        .filter(
            ZsxqAttachment.attachment_type == "file",
            ZsxqAttachment.download_status == "downloaded",
            ZsxqAttachment.local_path.isnot(None),
            or_(ZsxqAttachment.file_ext == "pdf", ZsxqAttachment.name.ilike("%.pdf"), ZsxqAttachment.local_path.ilike("%.pdf")),
        )
    )
    if scope in {"hot", "popular"}:
        query = query.filter(ZsxqAttachment.download_count >= hot_download_threshold)
    if q:
        pattern = f"%{_escape_like(q)}%"
        query = query.filter(or_(
            ZsxqAttachment.name.ilike(pattern, escape="\\"),
            ZsxqAttachment.local_path.ilike(pattern, escape="\\"),
            ZsxqTopic.title.ilike(pattern, escape="\\"),
            ZsxqTopic.tab_label.ilike(pattern, escape="\\"),
            ZsxqTopic.content.ilike(pattern, escape="\\"),
        ))

    target_count = page * per_page + 1
    chunk_size = min(max(per_page * 4, 40), 120)
    scan_limit = min(max(target_count * 5, 120), 2000)
    sync_time_order = db.func.coalesce(
        ZsxqTopic.last_synced_at,
        ZsxqAttachment.updated_at,
    )
    ordered = query.order_by(
        ZsxqTopic.create_time.desc().nullslast(),
        sync_time_order.desc(),
        ZsxqAttachment.updated_at.desc(),
        ZsxqAttachment.created_at.desc(),
    )
    valid_items = []
    skipped_missing = 0
    scanned = 0
    while len(valid_items) < target_count and scanned < scan_limit:
        batch_limit = min(chunk_size, scan_limit - scanned)
        rows = ordered.offset(scanned).limit(batch_limit).all()
        if not rows:
            break
        scanned += len(rows)
        for row in rows:
            item = _pdf_asset_payload(row)
            if item:
                valid_items.append(item)
            else:
                skipped_missing += 1
        if len(rows) < batch_limit:
            break

    start = (page - 1) * per_page
    end = start + per_page
    items = valid_items[start:end]
    _attach_cache_status(items)
    return jsonify({
        "success": True,
        "data": {
            "items": items,
            "pagination": {
                "page": page,
                "per_page": per_page,
                "has_more": len(valid_items) > end or scanned >= scan_limit,
                "skipped_missing": skipped_missing,
                "scanned": scanned,
                "scope": "hot" if scope in {"hot", "popular"} else "all",
                "hot_download_threshold": hot_download_threshold,
                "order": "published_desc",
                "q": q,
            },
        },
        "items": items,
    })


@report_top10_workflow_bp.route("/pdf-assets/<asset_id>/preview", methods=["GET"])
@require_login
def preview_pdf_asset(_current_user, asset_id):
    try:
        pdf = pdf_meta_from_zsxq_attachment(asset_id)
    except ReportTop10WorkflowError as exc:
        return jsonify({"success": False, "message": str(exc)}), 404
    return send_file(
        pdf["path"],
        mimetype="application/pdf",
        as_attachment=False,
        download_name=pdf.get("filename") or f"{asset_id}.pdf",
    )


@report_top10_workflow_bp.route("/jobs", methods=["POST"])
@require_login
def create_job(current_user):
    data = _request_payload()
    user_id = str(getattr(current_user, "id", None))
    asset_ids = _parse_asset_ids(data)
    expected_count = selected_report_count(len(asset_ids))
    if len(asset_ids) != expected_count:
        return jsonify({"success": False, "message": "本期支持选择 1-20 份 PDF"}), 400
    if len(set(asset_ids)) != len(asset_ids):
        return jsonify({"success": False, "message": "PDF 不能重复选择"}), 400

    editor_url = str(data.get("editor_gemini_url") or data.get("gemini_url") or default_editor_gemini_url()).strip()
    single_url = str(data.get("single_gemini_url") or "").strip()
    for label, url in (("editor_gemini_url", editor_url), ("single_gemini_url", single_url)):
        if url and not url.startswith(("http://", "https://")):
            return jsonify({"success": False, "message": f"{label} 必须是 http(s) 地址"}), 400

    gemini_status = gemini_browser.status_payload()
    if not gemini_status.get("configured"):
        return jsonify({
            "success": False,
            "message": "Gemini 浏览器自动化未配置",
            "data": {"report_pdf_gemini": gemini_status},
        }), 400

    pdfs = []
    try:
        for asset_id in asset_ids:
            pdfs.append(pdf_meta_from_zsxq_attachment(asset_id))
    except ReportTop10WorkflowError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400

    metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    title = str(data.get("program_name") or DEFAULT_PROGRAM_NAME).strip()
    task = PublicationTask(
        status="pending",
        current_step="queued",
        progress=0,
        params={
            "feature": FEATURE_NAME,
            "user_id": user_id,
            "report_top10_workflow": {
                "pdfs": pdfs,
                "pdf_asset_ids": asset_ids,
                "selected_count": expected_count,
                "program_package_type": "daily_report_selection",
                "program_name": title,
                "date_label": str(data.get("date_label") or datetime.utcnow().strftime("%Y-%m-%d")).strip(),
                "target_duration_minutes": str(data.get("target_duration_minutes") or "6-9").strip(),
                "editor_gemini_url": editor_url,
                "single_gemini_url": single_url,
                "reuse_existing_single_reports": _truthy(data.get("reuse_existing_single_reports"), True),
                "metadata": metadata,
            },
        },
        result={
            "metadata": metadata,
            "pdfs": [_public_pdf_meta(pdf) for pdf in pdfs],
            "children": {},
            "source_reports": [],
            "logs": [],
        },
    )
    db.session.add(task)
    db.session.commit()

    try:
        celery_task = process_report_top10_workflow_job.apply_async(args=[task.id], queue=QUEUE_NAME, retry=False)
    except Exception as exc:
        task.status = "failed"
        task.current_step = "failed"
        task.error_message = f"编排任务入队失败: {exc}"
        task.completed_at = datetime.utcnow()
        db.session.commit()
        return jsonify({"success": False, "message": task.error_message}), 502

    task.celery_task_id = celery_task.id
    db.session.commit()
    return jsonify({
        "success": True,
        "job_id": task.id,
        "status": "queued",
        "data": {"job_id": task.id, "status": "queued", "celery_id": celery_task.id},
    })


@report_top10_workflow_bp.route("/jobs", methods=["GET"])
@require_login
def list_jobs(current_user):
    page = _bounded_int(request.args.get("page"), 1, 1, 100000)
    per_page = _bounded_int(request.args.get("per_page"), 10, 1, 100)
    status = str(request.args.get("status") or "").strip()
    search = _normalize_search_query(request.args.get("q") or request.args.get("search") or request.args.get("job_search"))
    query = PublicationTask.query.options(load_only(*TASK_LIST_COLUMNS)).filter(
        PublicationTask.feature == FEATURE_NAME,
        PublicationTask.user_id.in_(_visible_user_ids(current_user)),
    )
    if status:
        reverse = {value: key for key, value in STATUS_MAP.items()}
        query = query.filter(PublicationTask.status == reverse.get(status, status))
    if search:
        pattern = f"%{_escape_like(search)}%"
        query = query.filter(or_(
            PublicationTask.task_title.ilike(pattern, escape="\\"),
            cast(PublicationTask.params, String).ilike(pattern, escape="\\"),
        ))
    pagination = query.order_by(PublicationTask.created_at.desc()).paginate(
        page=page,
        per_page=per_page,
        error_out=False,
    )
    items = [_job_payload(task, include_detail=False) for task in pagination.items]
    return jsonify({
        "success": True,
        "data": {
            "items": items,
            "pagination": {
                "page": pagination.page,
                "per_page": per_page,
                "total": pagination.total,
                "pages": pagination.pages,
                "q": search,
            },
        },
        "items": items,
    })


@report_top10_workflow_bp.route("/jobs/<job_id>", methods=["GET"])
@require_login
def get_job(current_user, job_id):
    task = _get_owned_task(current_user, job_id, include_result=False)
    if not task:
        return jsonify({"success": False, "message": "任务不存在"}), 404
    payload = _job_payload(task, include_detail=True, result_override=_detail_result_payload(task))
    return jsonify({
        "success": True,
        "job_id": payload.get("job_id"),
        "status": payload.get("status"),
        "data": payload,
    })


@report_top10_workflow_bp.route("/jobs/<job_id>/cancel", methods=["POST"])
@require_login
def cancel_job(current_user, job_id):
    task = _get_owned_task(current_user, job_id)
    if not task:
        return jsonify({"success": False, "message": "任务不存在"}), 404
    if task.status in TERMINAL_STATUSES:
        payload = _job_payload(task)
        return jsonify({"success": True, "message": "任务已结束", **payload, "data": payload})
    if task.celery_task_id:
        celery_app.control.revoke(task.celery_task_id, terminate=False)
    result = dict(task.result or {})
    result["canceled_at"] = datetime.utcnow().isoformat() + "Z"
    task.status = "canceled"
    task.current_step = "canceled"
    task.completed_at = datetime.utcnow()
    task.result = result
    flag_modified(task, "result")
    db.session.commit()
    payload = _job_payload(task)
    return jsonify({"success": True, "message": "任务已取消", **payload, "data": payload})


@report_top10_workflow_bp.route("/jobs/<job_id>/retry", methods=["POST"])
@require_login
def retry_job(current_user, job_id):
    task = _get_owned_task(current_user, job_id)
    if not task:
        return jsonify({"success": False, "message": "任务不存在"}), 404
    if task.status in {"pending", "processing"}:
        payload = _job_payload(task)
        return jsonify({"success": True, "message": "任务仍在运行中", **payload, "data": payload})
    if task.status == "completed":
        payload = _job_payload(task)
        return jsonify({"success": False, "message": "任务已完成，无需续跑", **payload, "data": payload}), 400

    result = dict(task.result or {})
    logs = list(result.get("logs") or [])
    logs.append({
        "step": "retry_requested",
        "message": "用户请求从失败处续跑今日研报严选编排",
        "progress": task.progress or 0,
        "updated_at": datetime.utcnow().isoformat() + "Z",
    })
    result["logs"] = logs[-160:]
    result["retry_requested_at"] = datetime.utcnow().isoformat() + "Z"
    result["error"] = None
    task.status = "pending"
    task.current_step = "queued"
    task.error_message = None
    task.completed_at = None
    task.result = result
    flag_modified(task, "result")
    db.session.commit()

    try:
        celery_task = process_report_top10_workflow_job.apply_async(args=[task.id], queue=QUEUE_NAME, retry=False)
    except Exception as exc:
        task.status = "failed"
        task.current_step = "failed"
        task.error_message = f"续跑任务入队失败: {exc}"
        task.completed_at = datetime.utcnow()
        db.session.commit()
        return jsonify({"success": False, "message": task.error_message}), 502
    task.celery_task_id = celery_task.id
    db.session.commit()
    payload = _job_payload(task)
    return jsonify({"success": True, "message": "任务已从失败处续跑", **payload, "data": payload})


@report_top10_workflow_bp.route("/jobs/<job_id>/download-result", methods=["GET"])
@require_login
def download_result(current_user, job_id):
    task = _get_owned_task(current_user, job_id, include_result=False)
    if not task:
        return jsonify({"success": False, "message": "任务不存在"}), 404
    output_path = _result_file_path(task)
    if not output_path or not os.path.exists(output_path):
        result = _task_result_from_db(task.id)
        output_path = (result or {}).get("output_path")
    if not output_path or not os.path.exists(output_path):
        return jsonify({"success": False, "message": "结果文件不存在"}), 404
    return send_file(
        output_path,
        mimetype="application/json",
        as_attachment=True,
        download_name=f"{task.id}_report_top10_result.json",
    )


@report_top10_workflow_bp.route("/jobs/<job_id>/create-video-task", methods=["POST"])
@require_login
def create_video_task(current_user, job_id):
    task = _get_owned_task(current_user, job_id, include_result=True)
    if not task:
        return jsonify({"success": False, "message": "任务不存在"}), 404
    if task.status != "completed":
        return jsonify({"success": False, "message": "节目包尚未完成，不能送入视频工厂"}), 400

    result = _detail_result_payload(task)
    existing_id = ((result.get("video_factory") or {}).get("task_id") or "").strip()
    existing = ReportVideoTask.query.get(existing_id) if existing_id else None
    if existing:
        return jsonify({
            "success": True,
            "message": "已存在视频工厂任务",
            "data": {
                "report_video_task_id": existing.id,
                "task": existing.to_dict(include_reports=True, include_ai=True),
                "reused": True,
            },
        })

    try:
        video_task = _create_report_video_task_from_program_package(task, result, request.get_json(silent=True) or {})
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400

    persisted_result = _task_result_from_db(task.id)
    if isinstance(persisted_result, dict):
        persisted_result["video_factory"] = {
            "task_id": video_task.id,
            "created_at": datetime.utcnow().isoformat() + "Z",
        }
        task.result = persisted_result
        flag_modified(task, "result")
        db.session.commit()

    return jsonify({
        "success": True,
        "message": "节目包已送入视频工厂",
        "data": {
            "report_video_task_id": video_task.id,
            "task": video_task.to_dict(include_reports=True, include_ai=True),
            "reused": False,
        },
    })


def _create_report_video_task_from_program_package(source_task, result, options):
    editor_result = result.get("editor_result") if isinstance(result.get("editor_result"), dict) else {}
    final_script = str(result.get("final_script_for_tts") or editor_result.get("final_script_for_tts") or "").strip()
    if not final_script:
        raise ValueError("节目包没有最终口播稿，不能送入视频工厂")

    params = source_task.params if isinstance(source_task.params, dict) else {}
    payload = params.get("report_top10_workflow") if isinstance(params.get("report_top10_workflow"), dict) else {}
    source_reports = result.get("source_reports") if isinstance(result.get("source_reports"), list) else []
    target_count = selected_report_count(payload.get("selected_count") or len(source_reports) or len(payload.get("pdfs") or []))
    date_label = str(editor_result.get("date_label") or payload.get("date_label") or "").strip()
    title = str(
        editor_result.get("program_title")
        or payload.get("program_name")
        or source_task.task_title
        or DEFAULT_PROGRAM_NAME
    ).strip()
    display_title = f"{title} · {date_label}" if date_label and date_label not in title else title
    ratios = options.get("output_ratios") if isinstance(options.get("output_ratios"), list) else ["16:9", "9:16"]
    ratios = [item for item in ratios if item in {"16:9", "9:16"}] or ["16:9", "9:16"]

    video_task = ReportVideoTask(
        task_name=display_title,
        video_type="topic_mix",
        default_segment_count=max(1, len(_program_video_sections(editor_result, final_script, source_reports))),
        output_ratios=ratios,
        style=options.get("style") or "professional_finance",
        background_music_path=None,
        generate_subtitles=True,
        status="script_ready",
        result={
            "source_program_package": _program_package_summary(source_task, result, target_count),
        },
    )
    db.session.add(video_task)
    db.session.flush()

    package_path = task_dir(video_task.id) / "program_package.json"
    package_path.parent.mkdir(parents=True, exist_ok=True)
    package_path.write_text(json.dumps({
        "source_task_id": source_task.id,
        "program_package": _program_package_summary(source_task, result, target_count),
        "editor_result": editor_result,
        "source_reports": source_reports,
        "final_script_for_tts": final_script,
        "video_segments": result.get("video_segments") or editor_result.get("video_segments") or [],
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    report = ReportVideoReport(
        task_id=video_task.id,
        report_code="001",
        file_name=f"{title}_节目包.json",
        file_path=rel_path(package_path),
        page_count=None,
        raw_text_path=None,
        cleaned_text_path=None,
        raw_text_chars=len(final_script),
        cleaned_text_chars=len(final_script),
        is_scanned=False,
        parse_status="parsed",
        ai_status="script_ready",
        error_message=None,
        raw_meta={
            "source": "program_package",
            "source_feature": FEATURE_NAME,
            "source_task_id": source_task.id,
            "selected_count": target_count,
            "pdfs": [_public_pdf_meta(pdf) for pdf in (payload.get("pdfs") or [])],
        },
    )
    db.session.add(report)
    db.session.flush()

    sections = _program_video_sections(editor_result, final_script, source_reports)
    nodes = _program_mindmap_nodes(editor_result, sections)
    db.session.add(ReportVideoAnalysis(
        report_id=report.id,
        report_title=title,
        institution="多机构研报",
        theme=_program_theme(editor_result),
        core_view=_program_core_view(editor_result, final_script),
        key_points=_program_key_points(editor_result, source_reports),
        video_value_score=90,
        recommended_video_type="topic_mix",
        recommended_duration_sec=sum(int(section.get("estimated_duration_sec") or 30) for section in sections),
        reason=f"来自今日研报严选节目包，已选 {target_count} 篇研报。",
        raw_payload={
            "editor_result": editor_result,
            "source_task_id": source_task.id,
            "program_package": _program_package_summary(source_task, result, target_count),
        },
    ))
    db.session.add(ReportVideoMindmap(
        report_id=report.id,
        center=editor_result.get("cover_title") or title,
        nodes=nodes,
        raw_payload={
            "source": "program_package",
            "nodes": nodes,
        },
    ))
    for index, section in enumerate(sections, start=1):
        node_id = f"node_{index:03d}"
        db.session.add(ReportVideoSegment(
            task_id=video_task.id,
            report_id=report.id,
            segment_id=f"program_{index:03d}",
            segment_index=index,
            segment_type=section.get("section_type") or ("topic_opening" if index == 1 else "topic_section"),
            segment_title=section.get("section_title") or f"节目段落 {index}",
            voice_text=section.get("voice_text") or "",
            estimated_duration_sec=section.get("estimated_duration_sec") or _estimate_voice_duration(section.get("voice_text")),
            focus_node_id=node_id,
            status="script_ready",
            raw_payload={
                **section,
                "topic_id": "program_package",
                "topic_name": title,
                "focus_node_id": node_id,
                "source_program_task_id": source_task.id,
            },
        ))

    topic_script = {
        "video_title": title,
        "topic_core_view": _program_core_view(editor_result, final_script),
        "opening_text": sections[0].get("voice_text") if sections else "",
        "sections": sections,
        "theme_mindmap": {"center": editor_result.get("cover_title") or title, "nodes": nodes},
        "provider": "report_topn_program_package",
    }
    video_task.result = {
        **(video_task.result or {}),
        "topic_planning": {
            "status": "topic_script_applied",
            "active_topic_id": "program_package",
            "applied_topic_id": "program_package",
            "applied_report_id": report.id,
            "topics": [{
                "topic_id": "program_package",
                "topic_name": title,
                "topic_category": "今日研报严选",
                "topic_summary": _program_core_view(editor_result, final_script),
                "report_count": target_count,
                "reports": ["001"],
                "institutions": ["多机构研报"],
                "final_score": 90,
            }],
            "ranked_topics": [{
                "topic_id": "program_package",
                "topic_name": title,
                "rank": 1,
                "final_score": 90,
                "report_count": target_count,
                "institutions": ["多机构研报"],
            }],
            "topic_scripts": {"program_package": topic_script},
            "applied_at": datetime.utcnow().isoformat(),
        },
    }
    db.session.commit()
    return video_task


def _program_package_summary(source_task, result, target_count):
    editor_result = result.get("editor_result") if isinstance(result.get("editor_result"), dict) else {}
    return {
        "source_feature": FEATURE_NAME,
        "source_task_id": source_task.id,
        "title": editor_result.get("program_title") or source_task.task_title or DEFAULT_PROGRAM_NAME,
        "cover_title": editor_result.get("cover_title") or "",
        "cover_subtitle": editor_result.get("cover_subtitle") or "",
        "selected_count": target_count,
        "source_report_count": len(result.get("source_reports") or []),
        "created_at": datetime.utcnow().isoformat() + "Z",
        "package_version": 1,
    }


def _program_video_sections(editor_result, final_script, source_reports):
    raw_segments = editor_result.get("video_segments") if isinstance(editor_result.get("video_segments"), list) else []
    fallback_parts = _split_final_script(final_script, max(len(raw_segments), 1))
    source_by_report_id = {
        f"R{index:02d}": report
        for index, report in enumerate(source_reports or [], start=1)
        if isinstance(report, dict)
    }
    sections = []
    for index, segment in enumerate(raw_segments, start=1):
        if not isinstance(segment, dict):
            continue
        voice_text = _segment_voice_text(segment, editor_result, source_by_report_id)
        if not voice_text and fallback_parts:
            voice_text = fallback_parts[min(index - 1, len(fallback_parts) - 1)]
        if not voice_text:
            continue
        sections.append({
            "section_id": segment.get("section_id") or f"S{index:03d}",
            "section_type": segment.get("section_type") or ("topic_opening" if index == 1 else "topic_section"),
            "section_title": segment.get("section_title") or segment.get("title") or f"节目段落 {index}",
            "voice_text": voice_text,
            "estimated_duration_sec": segment.get("estimated_duration_sec") or _estimate_voice_duration(voice_text),
            "source_report_ids": segment.get("source_report_ids") or _segment_source_report_ids(segment),
        })
    if sections:
        return sections
    return [
        {
            "section_id": f"S{index:03d}",
            "section_type": "topic_opening" if index == 1 else "topic_section",
            "section_title": f"节目段落 {index}",
            "voice_text": part,
            "estimated_duration_sec": _estimate_voice_duration(part),
            "source_report_ids": [],
        }
        for index, part in enumerate(fallback_parts, start=1)
        if part
    ]


def _segment_voice_text(segment, editor_result, source_by_report_id):
    direct = str(segment.get("voice_text") or segment.get("text") or segment.get("script") or "").strip()
    if direct:
        return direct
    ref = str(segment.get("voice_text_ref") or "").strip()
    if ref:
        value = _resolve_ref(editor_result, ref)
        if value:
            return str(value).strip()
    source = segment.get("voice_text_source") if isinstance(segment.get("voice_text_source"), dict) else {}
    report_id = str(source.get("source_report_id") or "").strip()
    source_field = str(source.get("source_field") or "single_report_script").strip()
    report = source_by_report_id.get(report_id) or {}
    value = _resolve_ref(report, source_field)
    return str(value or "").strip()


def _resolve_ref(payload, ref):
    value = payload
    for part in str(ref or "").replace("[", ".").replace("]", "").split("."):
        if part == "":
            continue
        if isinstance(value, list):
            try:
                value = value[int(part)]
            except (ValueError, IndexError):
                return None
        elif isinstance(value, dict):
            value = value.get(part)
        else:
            return None
    return value


def _segment_source_report_ids(segment):
    source = segment.get("voice_text_source") if isinstance(segment.get("voice_text_source"), dict) else {}
    report_id = str(source.get("source_report_id") or "").strip()
    return [report_id] if report_id else []


def _split_final_script(script, preferred_count):
    paragraphs = [item.strip() for item in re.split(r"\n{2,}", str(script or "").strip()) if item.strip()]
    if not paragraphs:
        return []
    preferred_count = max(1, int(preferred_count or 1))
    if len(paragraphs) <= preferred_count:
        return paragraphs
    chunks = [[] for _ in range(preferred_count)]
    for index, paragraph in enumerate(paragraphs):
        chunks[min(index, preferred_count - 1)].append(paragraph)
    return ["\n\n".join(chunk).strip() for chunk in chunks if chunk]


def _estimate_voice_duration(text):
    length = len(re.sub(r"\s+", "", str(text or "")))
    return min(max(round(length / 4.2), 18), 120)


def _program_mindmap_nodes(editor_result, sections):
    root_children = [f"node_{index:03d}" for index, _ in enumerate(sections, start=1)]
    nodes = [{
        "id": "node_overview",
        "label": str(editor_result.get("cover_title") or editor_result.get("program_title") or DEFAULT_PROGRAM_NAME)[:18],
        "type": "root",
        "children": root_children,
    }]
    for index, section in enumerate(sections, start=1):
        nodes.append({
            "id": f"node_{index:03d}",
            "label": str(section.get("section_title") or f"段落 {index}")[:18],
            "type": section.get("section_type") or "topic_section",
            "children": [],
        })
    return nodes


def _program_core_view(editor_result, final_script):
    for key in ("editorial_positioning", "main_threads_intro", "opening"):
        value = editor_result.get(key)
        if isinstance(value, dict):
            value = (
                value.get("voice_text")
                or value.get("text")
                or value.get("column_value")
                or value.get("selection_scope")
            )
        text = str(value or "").strip()
        if text:
            return text[:220]
    return str(final_script or "").strip()[:220]


def _program_theme(editor_result):
    positioning = editor_result.get("editorial_positioning")
    if isinstance(positioning, dict):
        for key in (
            "theme",
            "topic",
            "column_value",
            "selection_scope",
            "program_assembly_mode",
            "voice_text",
            "text",
        ):
            text = _compact_theme_text(positioning.get(key))
            if text:
                return text[:80]
        text = _compact_theme_text(json.dumps(positioning, ensure_ascii=False))
        if text:
            return text[:80]
    else:
        text = _compact_theme_text(positioning)
        if text:
            return text[:80]

    for key in ("cover_title", "program_title"):
        text = _compact_theme_text(editor_result.get(key))
        if text:
            return text[:80]
    return DEFAULT_PROGRAM_NAME


def _compact_theme_text(value):
    if isinstance(value, dict):
        for key in ("title", "name", "summary", "voice_text", "text"):
            text = _compact_theme_text(value.get(key))
            if text:
                return text
        return ""
    if isinstance(value, list):
        return "、".join(text for text in (_compact_theme_text(item) for item in value) if text)
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _program_key_points(editor_result, source_reports):
    points = []
    for value in editor_result.get("main_threads") or []:
        if isinstance(value, dict):
            text = value.get("title") or value.get("summary") or value.get("name")
        else:
            text = value
        if str(text or "").strip():
            points.append(str(text).strip())
    for report in source_reports or []:
        structured = report.get("structured_result") if isinstance(report, dict) else {}
        text = structured.get("cover_title") or structured.get("title")
        if str(text or "").strip():
            points.append(str(text).strip())
        if len(points) >= 5:
            break
    return points[:5]


def _job_payload(task, include_detail=False, result_override=None):
    if not include_detail:
        return _job_summary_payload(task)

    if isinstance(result_override, dict):
        result = result_override
    else:
        result = task.result if isinstance(task.result, dict) else {}
    params = task.params if isinstance(task.params, dict) else {}
    payload = params.get("report_top10_workflow") if isinstance(params.get("report_top10_workflow"), dict) else {}
    editor_result = result.get("editor_result") if isinstance(result.get("editor_result"), dict) else {}
    source_reports = result.get("source_reports") if isinstance(result.get("source_reports"), list) else []
    children = _fresh_child_summaries(result.get("children") or {})
    target_report_count = selected_report_count(payload.get("selected_count") or len(payload.get("pdfs") or []) or len(source_reports))
    item = {
        "job_id": task.id,
        "status": STATUS_MAP.get(task.status, task.status or "queued"),
        "step": task.current_step,
        "progress": task.progress or 0,
        "created_at": task.created_at.isoformat() + "Z" if task.created_at else None,
        "updated_at": task.updated_at.isoformat() + "Z" if task.updated_at else None,
        "completed_at": task.completed_at.isoformat() + "Z" if task.completed_at else result.get("completed_at"),
        "error": task.error_message or result.get("error"),
        "title": editor_result.get("program_title") or payload.get("program_name") or DEFAULT_PROGRAM_NAME,
        "cover_title": editor_result.get("cover_title") or "",
        "cover_subtitle": editor_result.get("cover_subtitle") or "",
        "pdfs": [_public_pdf_meta(pdf) for pdf in (payload.get("pdfs") or result.get("pdfs") or [])],
        "target_report_count": target_report_count,
        "source_report_count": len(source_reports),
        "editor_input_chars": result.get("editor_input_chars") or len(str(result.get("editor_input") or "")),
        "children": children,
        "editor_result": _public_editor_result(editor_result),
        "final_script_for_tts": result.get("final_script_for_tts"),
        "video_segments": result.get("video_segments"),
        "video_factory": result.get("video_factory") if isinstance(result.get("video_factory"), dict) else None,
        "download_result_url": f"/api/report-top10-workflows/jobs/{task.id}/download-result" if result.get("output_path") else None,
        "logs": result.get("logs") or [],
    }
    if include_detail:
        item["source_reports"] = [_public_source_report(report) for report in source_reports]
        item["editor_input"] = result.get("editor_input")
        if _truthy(request.args.get("include_raw"), False):
            item["editor_response_text"] = result.get("editor_response_text")
            item["raw_result"] = result
            item["raw_params"] = params
    return item


def _job_summary_payload(task):
    params = task.params if isinstance(task.params, dict) else {}
    payload = params.get("report_top10_workflow") if isinstance(params.get("report_top10_workflow"), dict) else {}
    target_report_count = selected_report_count(payload.get("selected_count") or len(payload.get("pdfs") or []))
    return {
        "job_id": task.id,
        "status": STATUS_MAP.get(task.status, task.status or "queued"),
        "step": task.current_step,
        "progress": task.progress or 0,
        "created_at": task.created_at.isoformat() + "Z" if task.created_at else None,
        "updated_at": task.updated_at.isoformat() + "Z" if task.updated_at else None,
        "completed_at": task.completed_at.isoformat() + "Z" if task.completed_at else None,
        "error": task.error_message,
        "title": task.task_title or DEFAULT_PROGRAM_NAME,
        "cover_title": "",
        "cover_subtitle": "",
        "target_report_count": target_report_count,
        "source_report_count": _estimated_source_report_count(task, target_report_count=target_report_count),
        "children": {},
        "editor_input_chars": None,
        "download_result_url": f"/api/report-top10-workflows/jobs/{task.id}/download-result" if task.status == "completed" else None,
    }


def _estimated_source_report_count(task, *, target_report_count=None):
    target_report_count = selected_report_count(target_report_count)
    if task.status == "completed":
        return target_report_count
    step = str(task.current_step or "")
    match = re.search(r"(?:single_report|single_gemini)_(\d{1,2})", step)
    if match:
        value = _bounded_int(match.group(1), 0, 0, target_report_count)
        if any(token in step for token in ("waiting", "running", "started")):
            value -= 1
        return max(0, min(target_report_count, value))
    progress = int(task.progress or 0)
    if progress >= 72:
        return target_report_count
    if progress <= 2:
        return 0
    return max(0, min(target_report_count, round((progress - 2) / 65 * target_report_count)))


def _public_editor_result(editor_result):
    if not isinstance(editor_result, dict) or not editor_result:
        return None
    allowed = (
        "program_title",
        "cover_title",
        "cover_subtitle",
        "date_label",
        "target_duration_minutes",
        "selection_mode",
        "program_assembly_mode",
        "editorial_positioning",
        "main_threads",
        "opening",
        "main_threads_intro",
        "midpoint_recap",
        "closing",
        "editorial_self_check",
    )
    public = {key: editor_result.get(key) for key in allowed if editor_result.get(key) not in (None, "", [], {})}
    for key in ("items", "items_continued", "video_segments"):
        value = editor_result.get(key)
        if isinstance(value, list):
            public[f"{key}_count"] = len(value)
    return public or None


def _detail_result_payload(task):
    db_result = _task_result_from_db(task.id)
    result_path = _result_file_path(task)
    if result_path and os.path.exists(result_path):
        try:
            payload = json.loads(Path(result_path).read_text(encoding="utf-8"))
        except Exception:
            payload = None
        if isinstance(payload, dict):
            if isinstance(db_result, dict) and isinstance(db_result.get("video_factory"), dict):
                payload["video_factory"] = db_result["video_factory"]
            payload.setdefault("output_path", result_path)
            payload.setdefault("completed_at", task.completed_at.isoformat() + "Z" if task.completed_at else None)
            return payload
    return db_result if isinstance(db_result, dict) else {}


def _result_file_path(task):
    try:
        return storage_path_for_job(task.id, task.created_at)
    except Exception:
        return None


def _task_result_from_db(task_id):
    row = db.session.query(PublicationTask.result).filter(PublicationTask.id == task_id).first()
    if not row:
        return {}
    result = row[0]
    return result if isinstance(result, dict) else {}


def _get_owned_task(current_user, job_id, include_result=True):
    columns = TASK_DETAIL_COLUMNS if include_result else TASK_REQUEST_COLUMNS
    task = PublicationTask.query.options(load_only(*columns)).get(job_id)
    if not task or (task.feature or (task.params or {}).get("feature")) != FEATURE_NAME:
        return None
    if str(task.user_id or (task.params or {}).get("user_id") or "").strip() not in _visible_user_ids(current_user):
        return None
    return task


def _fresh_child_summaries(children):
    if not isinstance(children, dict) or not children:
        return children or {}
    by_label = {label: dict(value) for label, value in children.items() if isinstance(value, dict)}
    child_ids = [
        str(value.get("job_id") or "").strip()
        for value in by_label.values()
        if str(value.get("job_id") or "").strip()
    ]
    if not child_ids:
        return by_label
    rows = (
        PublicationTask.query
        .with_entities(
            PublicationTask.id,
            PublicationTask.celery_task_id,
            PublicationTask.status,
            PublicationTask.current_step,
            PublicationTask.progress,
            PublicationTask.error_message,
            PublicationTask.feature,
            PublicationTask.created_at,
            PublicationTask.completed_at,
        )
        .filter(PublicationTask.id.in_(child_ids))
        .all()
    )
    row_map = {str(row.id): row for row in rows}
    for value in by_label.values():
        row = row_map.get(str(value.get("job_id") or ""))
        if not row:
            continue
        value.update({
            "celery_id": row.celery_task_id,
            "status": row.status,
            "step": row.current_step,
            "progress": row.progress or 0,
            "feature": row.feature,
            "error": row.error_message,
            "created_at": row.created_at.isoformat() + "Z" if row.created_at else None,
            "completed_at": row.completed_at.isoformat() + "Z" if row.completed_at else None,
        })
    return by_label


def _request_payload():
    if request.is_json:
        data = request.get_json(silent=True) or {}
    else:
        data = request.form.to_dict(flat=True)
    if isinstance(data.get("metadata"), str):
        try:
            data["metadata"] = json.loads(data["metadata"] or "{}")
        except Exception:
            data["metadata"] = {}
    return data if isinstance(data, dict) else {}


def _parse_asset_ids(data):
    raw = data.get("pdf_asset_ids") or data.get("asset_ids") or data.get("pdfs") or []
    if isinstance(raw, str):
        value = raw.strip()
        if not value:
            return []
        try:
            parsed = json.loads(value)
            raw = parsed
        except Exception:
            raw = [item.strip() for item in value.split(",")]
    if not isinstance(raw, list):
        raw = [raw]
    return [str(item).strip() for item in raw if str(item).strip()]


def _pdf_asset_payload(row):
    path = resolve_pdf_path(row.local_path)
    if not path:
        return None
    try:
        size = path.stat().st_size
    except OSError:
        return None
    if size <= 0:
        return None
    return {
        "id": row.attachment_id,
        "filename": row.attachment_name or path.name,
        "bytes": size,
        "download_count": row.download_count,
        "updated_at": row.attachment_updated_at.isoformat() + "Z" if row.attachment_updated_at else None,
        "created_at": row.attachment_created_at.isoformat() + "Z" if row.attachment_created_at else None,
        "group_id": row.group_id,
        "tab_id": row.tab_id,
        "topic_id": row.topic_id,
        "topic_title": row.topic_title,
        "topic_author": row.topic_author,
        "topic_create_time": row.topic_create_time,
        "topic_first_synced_at": _isoformat_z(getattr(row, "topic_first_synced_at", None)),
        "topic_last_synced_at": _isoformat_z(getattr(row, "topic_last_synced_at", None)),
        "tab_label": row.tab_label,
        "file_id": row.file_id,
        "local_path": str(path),
        "preview_url": f"/api/report-top10-workflows/pdf-assets/{row.attachment_id}/preview",
    }


def _attach_cache_status(items):
    for item in items:
        cached = cached_single_report_for_pdf({
            "attachment_id": item.get("id"),
            "file_id": item.get("file_id"),
            "filename": item.get("filename"),
        })
        item["single_script_cached"] = bool(cached)
        item["single_script_cache_source"] = cached.get("source") if cached else ""


def _public_pdf_meta(pdf):
    if not isinstance(pdf, dict):
        return None
    return {
        key: pdf.get(key)
        for key in (
            "filename",
            "bytes",
            "source",
            "attachment_id",
            "file_id",
            "group_id",
            "tab_id",
            "topic_id",
            "topic_title",
            "tab_label",
        )
        if pdf.get(key) is not None
    }


def _public_source_report(report):
    if not isinstance(report, dict):
        return None
    structured = report.get("structured_result") if isinstance(report.get("structured_result"), dict) else {}
    script = str(report.get("single_report_script") or structured.get("final_script_for_tts") or "").strip()
    return {
        "source": report.get("source"),
        "source_task_id": report.get("source_task_id"),
        "cache_task_id": report.get("cache_task_id"),
        "pdf": _public_pdf_meta(report.get("pdf") or {}),
        "title": structured.get("title") or report.get("title") or "",
        "cover_title": structured.get("cover_title") or "",
        "cover_subtitle": structured.get("cover_subtitle") or "",
        "summary": structured.get("summary") or "",
        "risk_notes": structured.get("risk_notes") or report.get("risk_notes") or [],
        "single_report_script": script,
        "final_script_for_tts": script,
        "script_chars": len(script),
        "structured_result": structured,
    }


def _visible_user_ids(current_user_or_id):
    raw = getattr(current_user_or_id, "id", current_user_or_id)
    user_id = str(raw or "").strip()
    values = {SYSTEM_USER_ID}
    if user_id:
        values.add(user_id)
    return values


def _bounded_int(value, default, minimum, maximum):
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


def _escape_like(value):
    return str(value or "").replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _normalize_search_query(value, limit=80):
    return " ".join(str(value or "").split())[:limit]


def _isoformat_z(value):
    if not value:
        return None
    if isinstance(value, str):
        return value
    return value.isoformat() + "Z"


def _truthy(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled", "y"}
