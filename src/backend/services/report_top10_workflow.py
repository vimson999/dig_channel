import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import load_only

from models.zsxq_sync import ZsxqAttachment, ZsxqTopic
from models.task import PublicationTask
from models import db
from services.report_audio_workflow import structured_result_quality_issue


FEATURE_NAME = "report_top10_workflow"
QUEUE_NAME = "report_top10_workflow"
SINGLE_REPORT_CACHE_FEATURE = "report_single_script_cache"
DEFAULT_EDITOR_GEMINI_URL = "https://gemini.google.com/gem/3d1eca63c142"
DEFAULT_PROGRAM_NAME = "今日研报严选"
DEFAULT_TARGET_DURATION_MINUTES = "6-9"
DEFAULT_REPORT_COUNT = 10
MIN_REPORT_COUNT = 1
MAX_REPORT_COUNT = 20
REQUIRED_REPORT_COUNT = DEFAULT_REPORT_COUNT
DEFAULT_EDITOR_SCRIPT_CHAR_LIMIT = 800
FINAL_SCRIPT_PLACEHOLDERS = {
    "ASSEMBLE_BY_BACKEND_USING_final_script_assembly_plan",
}


class ReportTop10WorkflowError(RuntimeError):
    pass


def status_payload() -> Dict[str, Any]:
    return {
        "configured": True,
        "business_feature": FEATURE_NAME,
        "queue": QUEUE_NAME,
        "editor_gemini_url": default_editor_gemini_url(),
        "default_report_count": DEFAULT_REPORT_COUNT,
        "min_report_count": MIN_REPORT_COUNT,
        "max_report_count": MAX_REPORT_COUNT,
        "required_report_count": DEFAULT_REPORT_COUNT,
        "single_report_cache_feature": SINGLE_REPORT_CACHE_FEATURE,
    }


def selected_report_count(value: Any = None) -> int:
    try:
        count = int(value)
    except (TypeError, ValueError):
        count = DEFAULT_REPORT_COUNT
    return min(max(count, MIN_REPORT_COUNT), MAX_REPORT_COUNT)


def default_editor_gemini_url() -> str:
    return (
        os.getenv("REPORT_TOP10_GEMINI_URL")
        or os.getenv("REPORT_EDITOR_GEMINI_URL")
        or DEFAULT_EDITOR_GEMINI_URL
    ).strip()


def storage_path_for_job(job_id: str, created_at: Optional[datetime] = None) -> str:
    day = (created_at or datetime.utcnow()).strftime("%Y%m%d")
    root = _project_root() / "data" / "processed" / FEATURE_NAME / day
    root.mkdir(parents=True, exist_ok=True)
    return str(root / f"{job_id}_result.json")


def pdf_meta_from_zsxq_attachment(asset_id: str) -> Dict[str, Any]:
    attachment = (
        ZsxqAttachment.query.options(
            load_only(
                ZsxqAttachment.id,
                ZsxqAttachment.group_id,
                ZsxqAttachment.tab_id,
                ZsxqAttachment.topic_id,
                ZsxqAttachment.name,
                ZsxqAttachment.file_id,
                ZsxqAttachment.local_path,
                ZsxqAttachment.download_status,
            )
        )
        .filter(ZsxqAttachment.id == str(asset_id))
        .one_or_none()
    )
    if not attachment:
        raise ReportTop10WorkflowError("选择的同步 PDF 不存在")
    if attachment.download_status != "downloaded":
        raise ReportTop10WorkflowError("选择的同步 PDF 还没有下载完成")

    path = resolve_pdf_path(attachment.local_path)
    if not path:
        raise ReportTop10WorkflowError("选择的同步 PDF 本地文件不存在")
    size = path.stat().st_size
    if size <= 0:
        raise ReportTop10WorkflowError("选择的同步 PDF 文件为空")
    if size > _max_pdf_bytes():
        raise ReportTop10WorkflowError(f"PDF 超过 {_max_pdf_mb()}MB 限制")

    topic = ZsxqTopic.query.filter_by(
        group_id=attachment.group_id,
        tab_id=attachment.tab_id,
        topic_id=attachment.topic_id,
    ).options(
        load_only(
            ZsxqTopic.id,
            ZsxqTopic.title,
            ZsxqTopic.tab_label,
        )
    ).first()
    return {
        "filename": attachment.name or path.name,
        "path": str(path),
        "bytes": size,
        "source": "zsxq_attachment",
        "attachment_id": attachment.id,
        "file_id": attachment.file_id,
        "group_id": attachment.group_id,
        "tab_id": attachment.tab_id,
        "topic_id": attachment.topic_id,
        "topic_title": topic.title if topic else None,
        "tab_label": topic.tab_label if topic else None,
    }


def resolve_pdf_path(value: Any) -> Optional[Path]:
    raw = str(value or "").strip()
    if not raw:
        return None
    path = Path(raw)
    candidates = [path]
    if not path.is_absolute():
        candidates.append(_project_root() / raw)
    for candidate in candidates:
        try:
            resolved = candidate.expanduser().resolve()
        except OSError:
            continue
        if resolved.exists() and resolved.is_file() and resolved.suffix.lower() == ".pdf":
            return resolved
    return None


def valid_single_report_script(structured: Any) -> str:
    if not isinstance(structured, dict):
        return ""
    script = str(structured.get("final_script_for_tts") or "").strip()
    if not script:
        return ""
    if structured_result_quality_issue(structured):
        return ""
    return script


def cached_single_report_for_pdf(pdf: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    source_task_id = _pdf_source_task_id(pdf)
    if source_task_id:
        task = (
            PublicationTask.query
            .filter(
                PublicationTask.feature == SINGLE_REPORT_CACHE_FEATURE,
                PublicationTask.status == "completed",
                PublicationTask.source_task_id == source_task_id,
            )
            .order_by(PublicationTask.completed_at.desc(), PublicationTask.created_at.desc())
            .first()
        )
        payload = _single_report_payload_from_cache_task(task, pdf)
        if payload:
            return payload

    file_id = str(pdf.get("file_id") or "").strip()
    if file_id:
        task = (
            PublicationTask.query
            .filter(
                PublicationTask.feature == SINGLE_REPORT_CACHE_FEATURE,
                PublicationTask.status == "completed",
                PublicationTask.params["report_single_script_cache"]["pdf"]["file_id"].as_string() == file_id,
            )
            .order_by(PublicationTask.completed_at.desc(), PublicationTask.created_at.desc())
            .first()
        )
        payload = _single_report_payload_from_cache_task(task, pdf)
        if payload:
            return payload
    return None


def cache_single_report_script(
    *,
    pdf: Dict[str, Any],
    structured_result: Dict[str, Any],
    source_feature: str,
    source_task_id: str,
    user_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[PublicationTask]:
    script = valid_single_report_script(structured_result)
    if not script:
        return None
    pdf_source_task_id = _pdf_source_task_id(pdf)
    if not pdf_source_task_id:
        return None

    task = (
        PublicationTask.query
        .filter(
            PublicationTask.feature == SINGLE_REPORT_CACHE_FEATURE,
            PublicationTask.source_task_id == pdf_source_task_id,
        )
        .order_by(PublicationTask.completed_at.desc(), PublicationTask.created_at.desc())
        .first()
    )
    now = datetime.utcnow()
    params = {
        "feature": SINGLE_REPORT_CACHE_FEATURE,
        "user_id": str(user_id or "system"),
        "source_feature": pdf.get("source") or "pdf",
        "source_task_id": pdf_source_task_id,
        "report_single_script_cache": {
            "pdf": pdf,
            "metadata": metadata or {},
        },
    }
    result = {
        "pdf": pdf,
        "structured_result": structured_result,
        "final_script_for_tts": script,
        "source_feature": source_feature,
        "source_task_id": source_task_id,
        "cached_at": now.isoformat() + "Z",
        "metadata": metadata or {},
    }
    if task:
        task.status = "completed"
        task.current_step = "done"
        task.progress = 100
        task.params = params
        task.result = result
        task.error_message = None
        task.completed_at = now
    else:
        task = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            params=params,
            result=result,
            completed_at=now,
        )
        db.session.add(task)
    db.session.commit()
    return task


def build_editor_input(source_reports: List[Dict[str, Any]], config: Optional[Dict[str, Any]] = None) -> str:
    config = config or {}
    script_char_limit = _editor_script_char_limit(config)
    lines = [
        "【本期参数】",
        f"date_label: {str(config.get('date_label') or '').strip() or datetime.utcnow().strftime('%Y-%m-%d')}",
        f"program_name: {str(config.get('program_name') or '').strip() or DEFAULT_PROGRAM_NAME}",
        f"target_duration_minutes: {str(config.get('target_duration_minutes') or '').strip() or DEFAULT_TARGET_DURATION_MINUTES}",
        f"single_report_script_char_limit: {script_char_limit}",
        "",
        "【输入素材】",
        "",
    ]
    for index, report in enumerate(source_reports, start=1):
        report_id = f"R{index:02d}"
        lines.extend(_report_block_lines(report_id, report, script_char_limit=script_char_limit))
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def _single_report_payload_from_cache_task(task: Optional[PublicationTask], pdf: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not task or task.status != "completed":
        return None
    result = task.result if isinstance(task.result, dict) else {}
    structured = result.get("structured_result") if isinstance(result.get("structured_result"), dict) else {}
    script = valid_single_report_script(structured)
    if not script:
        return None
    cached_pdf = result.get("pdf") if isinstance(result.get("pdf"), dict) else {}
    return {
        "source": SINGLE_REPORT_CACHE_FEATURE,
        "source_task_id": result.get("source_task_id") or task.id,
        "cache_task_id": task.id,
        "pdf": pdf or cached_pdf,
        "pdf_asset_id": (pdf or cached_pdf).get("attachment_id"),
        "filename": (pdf or cached_pdf).get("filename"),
        "structured_result": structured,
        "single_report_script": script,
    }


def _pdf_source_task_id(pdf: Dict[str, Any]) -> str:
    return str(
        pdf.get("attachment_id")
        or pdf.get("file_id")
        or pdf.get("sha256")
        or ""
    ).strip()


def extract_editor_json(text: Any) -> Dict[str, Any]:
    raw = str(text or "").strip()
    if not raw:
        raise ReportTop10WorkflowError("总编辑 Gem 没有返回内容")
    candidates = [raw]
    fenced = _extract_fenced_json(raw)
    if fenced:
        candidates.insert(0, fenced)
    balanced = _extract_balanced_json_object(raw)
    if balanced:
        candidates.append(balanced)

    last_error = None
    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError as exc:
            last_error = exc
            continue
        if isinstance(payload, dict):
            return payload
    raise ReportTop10WorkflowError(f"总编辑 Gem 返回非严格 JSON: {last_error}")


def write_result_file(
    *,
    job_id: str,
    created_at: Optional[datetime],
    editor_input: str,
    editor_response_text: str,
    editor_result: Dict[str, Any],
    source_reports: List[Dict[str, Any]],
) -> str:
    output_path = storage_path_for_job(job_id, created_at)
    payload = {
        "job_id": job_id,
        "source_reports": source_reports,
        "editor_input": editor_input,
        "editor_response_text": editor_response_text,
        "editor_result": editor_result,
        "final_script_for_tts": editor_result.get("final_script_for_tts"),
        "video_segments": editor_result.get("video_segments"),
        "created_at": datetime.utcnow().isoformat() + "Z",
    }
    Path(output_path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path


def assemble_final_script_for_tts(editor_result: Dict[str, Any], source_reports: List[Dict[str, Any]]) -> str:
    existing = str((editor_result or {}).get("final_script_for_tts") or "").strip()
    if existing and existing not in FINAL_SCRIPT_PLACEHOLDERS and len(existing) >= 200:
        return existing

    result = editor_result if isinstance(editor_result, dict) else {}
    source_by_report_id = {
        f"R{index:02d}": report
        for index, report in enumerate(source_reports or [], start=1)
        if isinstance(report, dict)
    }
    parts: List[str] = []
    for segment in result.get("video_segments") or []:
        if not isinstance(segment, dict):
            continue
        text = ""
        ref = str(segment.get("voice_text_ref") or "").strip()
        if ref:
            text = _string_from_value(_resolve_ref(result, ref))
        if not text:
            source = segment.get("voice_text_source") if isinstance(segment.get("voice_text_source"), dict) else {}
            report_id = str(source.get("source_report_id") or "").strip()
            source_field = str(source.get("source_field") or "single_report_script").strip()
            report = source_by_report_id.get(report_id) or {}
            text = _string_from_value(_resolve_ref(report, source_field))
        text = _collapse_blank_lines(text)
        if text:
            parts.append(text)

    if parts:
        return "\n\n".join(parts).strip()
    return existing


def _report_block_lines(report_id: str, report: Dict[str, Any], *, script_char_limit: int) -> List[str]:
    structured = report.get("structured_result") if isinstance(report.get("structured_result"), dict) else {}
    pdf = report.get("pdf") if isinstance(report.get("pdf"), dict) else {}
    script = str(report.get("single_report_script") or structured.get("final_script_for_tts") or "").strip()
    compact_script, original_chars = _compact_editor_script(script, script_char_limit)
    fields = [
        ("task_id", report.get("source_task_id") or report.get("gemini_task_id") or ""),
        ("pdf_asset_id", pdf.get("attachment_id") or report.get("pdf_asset_id") or ""),
        ("filename", pdf.get("filename") or report.get("filename") or ""),
        ("original_topic_title", pdf.get("topic_title") or ""),
        ("single_report_title", structured.get("title") or report.get("title") or ""),
        ("cover_title", structured.get("cover_title") or report.get("cover_title") or ""),
        ("cover_subtitle", structured.get("cover_subtitle") or report.get("cover_subtitle") or ""),
        ("summary", structured.get("summary") or report.get("summary") or ""),
        ("risk_notes", _join_notes(structured.get("risk_notes") or report.get("risk_notes"))),
    ]
    lines = [f'<report id="{report_id}">']
    for key, value in fields:
        text = str(value or "").strip()
        if text:
            lines.append(f"{key}: {text}")
    if original_chars > len(compact_script):
        lines.append(f"single_report_script_original_chars: {original_chars}")
        lines.append("single_report_script_note: 正文已为总编辑阶段压缩，完整单篇结果仍保存在 source_reports。")
    lines.extend(["single_report_script:", compact_script, "</report>"])
    return lines


def _editor_script_char_limit(config: Dict[str, Any]) -> int:
    raw = (
        config.get("editor_script_char_limit")
        or os.getenv("REPORT_TOP10_EDITOR_SCRIPT_CHAR_LIMIT")
        or DEFAULT_EDITOR_SCRIPT_CHAR_LIMIT
    )
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = DEFAULT_EDITOR_SCRIPT_CHAR_LIMIT
    return max(400, value)


def _compact_editor_script(script: str, limit: int) -> tuple[str, int]:
    text = _collapse_blank_lines(str(script or "").strip())
    original_chars = len(text)
    if original_chars <= limit:
        return text, original_chars
    if limit < 600:
        return text[:limit].rstrip(), original_chars
    head_limit = max(400, int(limit * 0.72))
    tail_limit = max(120, limit - head_limit - 48)
    head = text[:head_limit].rstrip()
    tail = text[-tail_limit:].lstrip()
    compact = f"{head}\n...\n{tail}".strip()
    return compact, original_chars


def _join_notes(value: Any) -> str:
    if isinstance(value, list):
        return "；".join(str(item).strip() for item in value if str(item).strip())
    return str(value or "").strip()


def _resolve_ref(root: Any, ref: str) -> Any:
    value = root
    for token in str(ref or "").split("."):
        if not token:
            continue
        match = re.fullmatch(r"([^\[\]]+)(?:\[(\d+)\])?", token)
        if not match:
            return None
        key, index_text = match.groups()
        if isinstance(value, dict):
            value = value.get(key)
        else:
            return None
        if index_text is not None:
            if not isinstance(value, list):
                return None
            index = int(index_text)
            if index < 0 or index >= len(value):
                return None
            value = value[index]
    return value


def _string_from_value(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        return str(value.get("voice_text") or value.get("text") or "").strip()
    return ""


def _collapse_blank_lines(value: str) -> str:
    lines = [line.rstrip() for line in str(value or "").splitlines()]
    compact: List[str] = []
    blank = False
    for line in lines:
        if line.strip():
            compact.append(line)
            blank = False
        elif not blank:
            compact.append("")
            blank = True
    return "\n".join(compact).strip()


def _extract_fenced_json(text: str) -> str:
    marker = "```"
    start = text.find(marker)
    if start < 0:
        return ""
    start = text.find("\n", start)
    if start < 0:
        return ""
    end = text.find(marker, start + 1)
    if end < 0:
        return ""
    return text[start:end].strip()


def _extract_balanced_json_object(text: str) -> str:
    start = text.find("{")
    if start < 0:
        return ""
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(text)):
        char = text[index]
        if escape:
            escape = False
            continue
        if char == "\\" and in_string:
            escape = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start:index + 1]
    return ""


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _max_pdf_mb() -> int:
    try:
        return max(1, int(os.getenv("REPORT_TOP10_MAX_PDF_MB", os.getenv("REPORT_AUDIO_WORKFLOW_MAX_PDF_MB", "200"))))
    except (TypeError, ValueError):
        return 200


def _max_pdf_bytes() -> int:
    return _max_pdf_mb() * 1024 * 1024
